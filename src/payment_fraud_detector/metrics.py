"""RunMetrics aggregation across a simulation run. See LLD.md §3.7."""

from __future__ import annotations

import collections

from payment_fraud_detector import config
from payment_fraud_detector.models import HoldOutcome, HoldRecord, RingVerdict, RunMetrics, Transaction, TxLabel


def summarize(
    transactions: list[Transaction],
    verdicts: dict[str, RingVerdict],
    holds: dict[str, HoldRecord],
    ack_latencies_ms: list[int],
    detection_latencies_ms: list[int],
    peak_node_count: int,
    peak_edge_count: int,
    ring_caught_edge_fraction: float = config.RING_CAUGHT_EDGE_FRACTION,
) -> RunMetrics:
    """Pure end-of-run rollup — takes every transaction plus its verdict/hold outcome and produces RunMetrics.

    Keyed by transaction_id throughout: `verdicts`/`holds` need only contain
    entries for transactions a verdict/hold actually exists for (not every tx
    gets held — only flagged ones do).
    """
    ring_totals: dict[str, int] = collections.Counter()
    ring_caught: dict[str, int] = collections.Counter()

    false_positives = 0

    for tx in transactions:
        verdict = verdicts.get(tx.transaction_id)
        flagged = verdict.flagged if verdict else False

        if tx.label is TxLabel.RING_FRAUD and tx.ring_id is not None:
            ring_totals[tx.ring_id] += 1
            hold = holds.get(tx.transaction_id)
            if flagged and hold is not None and hold.outcome is HoldOutcome.HELD:
                ring_caught[tx.ring_id] += 1
        elif flagged:
            # a non-fraud transaction (LEGIT or BENIGN_FANOUT) got flagged — that's a false positive
            false_positives += 1

    rings_injected = len(ring_totals)
    rings_caught = sum(
        1
        for ring_id, total in ring_totals.items()
        if total > 0 and (ring_caught[ring_id] / total) >= ring_caught_edge_fraction
    )
    rings_missed = rings_injected - rings_caught

    return RunMetrics(
        rings_injected=rings_injected,
        rings_caught=rings_caught,
        rings_missed=rings_missed,
        false_positives=false_positives,
        detection_latencies_ms=list(detection_latencies_ms),
        ack_latencies_ms=list(ack_latencies_ms),
        peak_node_count=peak_node_count,
        peak_edge_count=peak_edge_count,
    )
