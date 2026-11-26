"""Wires ingestion -> graph store -> detector -> interceptor -> metrics; prints the run summary. See HLD.md §3, LLD.md §1."""

from __future__ import annotations

import asyncio
import random
import time

from payment_fraud_detector import config
from payment_fraud_detector.detector import detect
from payment_fraud_detector.graph_store import insert_transaction, node_count, total_edge_count
from payment_fraud_detector.interceptor import intercept
from payment_fraud_detector.metrics import summarize
from payment_fraud_detector.models import RollingWindowGraph, RunMetrics, Transaction
from payment_fraud_detector.simulator import generate_benign_fanout, generate_legit_stream, generate_ring

_STOP = object()  # sentinel: tells the detection worker there's nothing more coming


async def _ingest(tx: Transaction, queue: asyncio.Queue, ack_latencies_ms: list[float]) -> None:
    """The customer-visible path — hands off and returns, never waits on detection (HLD §3 step 2)."""
    start = time.perf_counter()
    await queue.put(tx)
    ack_latencies_ms.append((time.perf_counter() - start) * 1000)


async def _detection_worker(
    queue: asyncio.Queue,
    graph: RollingWindowGraph,
    verdicts: dict,
    holds: dict,
    detection_latencies_ms: list[float],
    peaks: dict,
    settlement_window_ms: int,
    fan_out_threshold: int,
    fan_in_threshold: int,
    max_hop_chain_ms: int,
    max_near_cycle_ms: int,
) -> None:
    """Runs off the critical path (HLD §3 steps 3-6): insert -> detect -> (maybe) intercept."""
    while True:
        tx = await queue.get()
        if tx is _STOP:
            queue.task_done()
            return

        start = time.perf_counter()
        insert_transaction(graph, tx)
        verdict = detect(graph, tx, fan_out_threshold, fan_in_threshold, max_hop_chain_ms, max_near_cycle_ms)
        elapsed_ms = (time.perf_counter() - start) * 1000

        verdicts[tx.transaction_id] = verdict
        peaks["node"] = max(peaks["node"], node_count(graph))
        peaks["edge"] = max(peaks["edge"], total_edge_count(graph))

        if verdict.flagged:
            detection_latencies_ms.append(elapsed_ms)
            # bridge real (wall-clock) detection time onto the virtual clock — see ARCHITECTURE.md ADR-006;
            # compute time is microseconds, negligible against settlement windows measured in seconds.
            detection_time_ms = tx.timestamp_ms + int(elapsed_ms)
            holds[tx.transaction_id] = intercept(tx, verdict, detection_time_ms, settlement_window_ms)

        queue.task_done()


async def run_simulation(
    transactions: list[Transaction],
    graph: RollingWindowGraph,
    settlement_window_ms: int = config.SETTLEMENT_WINDOW_MS,
    fan_out_threshold: int = config.FAN_OUT_THRESHOLD,
    fan_in_threshold: int = config.FAN_IN_THRESHOLD,
    max_hop_chain_ms: int = config.MAX_HOP_CHAIN_MS,
    max_near_cycle_ms: int = config.MAX_NEAR_CYCLE_MS,
    ring_caught_edge_fraction: float = config.RING_CAUGHT_EDGE_FRACTION,
    detection_enabled: bool = True,
) -> RunMetrics:
    """detection_enabled=False isolates the pure ack-path cost (HLD §5 N1) — no graph/detector/interceptor at all."""
    queue: asyncio.Queue = asyncio.Queue()
    ack_latencies_ms: list[float] = []

    if not detection_enabled:
        for tx in transactions:
            await _ingest(tx, queue, ack_latencies_ms)
        return RunMetrics(ack_latencies_ms=ack_latencies_ms)

    verdicts: dict = {}
    holds: dict = {}
    detection_latencies_ms: list[float] = []
    peaks = {"node": 0, "edge": 0}

    worker = asyncio.create_task(
        _detection_worker(
            queue, graph, verdicts, holds, detection_latencies_ms, peaks,
            settlement_window_ms, fan_out_threshold, fan_in_threshold, max_hop_chain_ms, max_near_cycle_ms,
        )
    )

    for tx in transactions:
        await _ingest(tx, queue, ack_latencies_ms)

    await queue.put(_STOP)
    await queue.join()
    await worker

    return summarize(
        transactions, verdicts, holds, ack_latencies_ms, detection_latencies_ms,
        peaks["node"], peaks["edge"], ring_caught_edge_fraction,
    )


def build_demo_scenario() -> list[Transaction]:
    """A small mixed stream: legit traffic, a benign payroll fan-out, and one injected ring."""
    rng = random.Random(42)
    t0 = config.SIMULATION_START_MS
    accounts = [f"acct{i}" for i in range(20)]

    legit = generate_legit_stream(rng, accounts, count=30, start_ms=t0, interval_ms=1_000)
    benign = generate_benign_fanout(
        "employer", [f"emp{i}" for i in range(5)], amount_each=50_000, start_ms=t0 + 5_000,
    )
    ring = generate_ring(
        "ring-1", "origin", [f"mule{i}" for i in range(12)], "cashout",
        total_amount=1_800_000_00, start_ms=t0 + 10_000, hop_interval_ms=10,
    )

    all_txs = legit + benign + ring
    all_txs.sort(key=lambda tx: tx.timestamp_ms)
    return all_txs


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def ack_latency_delta_ms(with_detection: RunMetrics, without_detection: RunMetrics) -> float:
    """HLD §5 N1: ack latency with detection enabled vs. disabled — must be ~0."""
    return _avg(with_detection.ack_latencies_ms) - _avg(without_detection.ack_latencies_ms)


def print_summary(metrics: RunMetrics, baseline: RunMetrics | None = None) -> None:
    print("--- Phase 0 run summary ---")
    print(f"Rings injected:      {metrics.rings_injected}")
    print(f"Rings caught:        {metrics.rings_caught}")
    print(f"Rings missed:        {metrics.rings_missed}")
    print(f"False positives:     {metrics.false_positives}")
    if metrics.ack_latencies_ms:
        print(f"Ack latency (ms):    avg={_avg(metrics.ack_latencies_ms):.4f} max={max(metrics.ack_latencies_ms):.4f}")
    if metrics.detection_latencies_ms:
        print(f"Detection latency (ms): avg={_avg(metrics.detection_latencies_ms):.4f} max={max(metrics.detection_latencies_ms):.4f}")
    print(f"Peak node count:     {metrics.peak_node_count}")
    print(f"Peak edge count:     {metrics.peak_edge_count}")
    if baseline is not None:
        print(f"Ack latency delta (detection on vs off): {ack_latency_delta_ms(metrics, baseline):+.4f} ms")


def main() -> None:
    transactions = build_demo_scenario()

    metrics = asyncio.run(run_simulation(transactions, RollingWindowGraph(window_ms=config.WINDOW_MS)))
    baseline = asyncio.run(
        run_simulation(transactions, RollingWindowGraph(window_ms=config.WINDOW_MS), detection_enabled=False)
    )
    print_summary(metrics, baseline)


if __name__ == "__main__":
    main()
