"""End-to-end wiring: ingestion (ack path) -> graph/detector/interceptor (async path) -> metrics. Chunk 7."""

import asyncio
import random

from payment_fraud_detector.main import ack_latency_delta_ms, run_simulation
from payment_fraud_detector.models import RollingWindowGraph
from payment_fraud_detector.simulator import generate_benign_fanout, generate_legit_stream, generate_ring


def run(transactions, **kwargs):
    graph = RollingWindowGraph(window_ms=300_000)
    return asyncio.run(run_simulation(transactions, graph, **kwargs))


# --- happy path ---

def test_small_ring_is_caught_end_to_end_when_thresholds_are_met():
    txs = generate_ring("r1", "origin", ["m1", "m2", "m3"], "cashout", total_amount=9000, start_ms=0, hop_interval_ms=10)

    metrics = run(txs, settlement_window_ms=10_000, fan_out_threshold=3, fan_in_threshold=3, ring_caught_edge_fraction=0.5)

    assert metrics.rings_injected == 1
    assert metrics.rings_caught == 1
    assert metrics.rings_missed == 0


def test_ack_path_is_not_blocked_by_detection():
    txs = generate_ring("r1", "origin", ["m1", "m2", "m3"], "cashout", total_amount=9000, start_ms=0, hop_interval_ms=10)

    metrics = run(txs, fan_out_threshold=3, fan_in_threshold=3)

    assert len(metrics.ack_latencies_ms) == len(txs)
    # the ack path only awaits a queue.put — it must stay near-instant regardless of detection cost
    assert all(latency_ms < 100 for latency_ms in metrics.ack_latencies_ms)


# --- negative / edge cases ---

def test_empty_transaction_list_does_not_hang_or_crash():
    metrics = run([])

    assert metrics.rings_injected == 0
    assert metrics.rings_caught == 0
    assert metrics.ack_latencies_ms == []


def test_ring_below_threshold_is_missed_end_to_end():
    # 2 mules (below fan_out/fan_in threshold 3) AND a slow hop (5000ms, past the
    # 50ms hop-chain window) AND no money flowing back to origin (no near-cycle) —
    # every heuristic must individually fail to catch this one, not just two of four.
    txs = generate_ring("r1", "origin", ["m1", "m2"], "cashout", total_amount=9000, start_ms=0, hop_interval_ms=5_000)

    metrics = run(txs, fan_out_threshold=3, fan_in_threshold=3, max_hop_chain_ms=50)

    assert metrics.rings_injected == 1
    assert metrics.rings_caught == 0
    assert metrics.rings_missed == 1


def test_benign_fanout_above_threshold_surfaces_as_false_positive_end_to_end():
    txs = generate_benign_fanout("employer", ["e1", "e2", "e3", "e4"], amount_each=50_000, start_ms=0)

    metrics = run(txs, fan_out_threshold=4)

    assert metrics.false_positives >= 1


def test_detection_disabled_mode_only_records_acks_with_no_rings_counted():
    txs = generate_ring("r1", "origin", ["m1", "m2", "m3"], "cashout", total_amount=9000, start_ms=0, hop_interval_ms=10)

    metrics = run(txs, detection_enabled=False)

    assert len(metrics.ack_latencies_ms) == len(txs)
    assert metrics.rings_injected == 0  # summarize() never ran — nothing to compare against ground truth
    assert metrics.detection_latencies_ms == []


def test_ack_latency_delta_is_near_zero_for_a_realistic_stream():
    # HLD §5 N1: ack latency with detection enabled vs disabled must be ~0 — this is the real comparison, not a guess.
    rng = random.Random(7)
    txs = generate_legit_stream(rng, [f"a{i}" for i in range(20)], count=100, start_ms=0, interval_ms=10)

    with_detection = run(txs)
    without_detection = run(txs, detection_enabled=False)

    delta_ms = ack_latency_delta_ms(with_detection, without_detection)
    assert abs(delta_ms) < 1.0  # sub-millisecond either way — detection truly isn't on the ack path
