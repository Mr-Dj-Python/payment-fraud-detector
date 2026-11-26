"""RunMetrics end-of-run rollup (LLD.md §3.7). Chunk 6."""

from payment_fraud_detector.metrics import summarize
from payment_fraud_detector.models import HoldOutcome, HoldRecord, RingVerdict, Transaction, TxLabel


def ring_tx(i, ring_id="r1"):
    return Transaction(
        transaction_id=f"{ring_id}-{i}", sender_id="origin", receiver_id=f"m{i}",
        amount=100, timestamp_ms=i, label=TxLabel.RING_FRAUD, ring_id=ring_id,
    )


def held(tx):
    return HoldRecord(transaction_id=tx.transaction_id, hold_time_ms=0, window_close_ms=100, outcome=HoldOutcome.HELD)


# --- happy path ---

def test_fully_caught_ring_counts_as_caught():
    txs = [ring_tx(i) for i in range(6)]
    verdicts = {tx.transaction_id: RingVerdict(flagged=True) for tx in txs}
    holds = {tx.transaction_id: held(tx) for tx in txs}

    metrics = summarize(txs, verdicts, holds, ack_latencies_ms=[1, 2], detection_latencies_ms=[3],
                         peak_node_count=7, peak_edge_count=12, ring_caught_edge_fraction=0.5)

    assert metrics.rings_injected == 1
    assert metrics.rings_caught == 1
    assert metrics.rings_missed == 0
    assert metrics.false_positives == 0
    assert metrics.peak_node_count == 7


def test_ring_caught_exactly_at_fraction_boundary_counts_as_caught():
    txs = [ring_tx(i) for i in range(6)]
    verdicts = {tx.transaction_id: RingVerdict(flagged=True) for tx in txs}
    # only the first 3 of 6 (= exactly 0.5) actually get held
    holds = {tx.transaction_id: held(tx) for tx in txs[:3]}

    metrics = summarize(txs, verdicts, holds, ack_latencies_ms=[], detection_latencies_ms=[],
                         peak_node_count=0, peak_edge_count=0, ring_caught_edge_fraction=0.5)

    assert metrics.rings_caught == 1
    assert metrics.rings_missed == 0


def test_legit_transaction_incorrectly_flagged_counts_as_false_positive():
    legit = Transaction(transaction_id="L1", sender_id="A", receiver_id="B", amount=500, timestamp_ms=0, label=TxLabel.LEGIT)
    verdicts = {"L1": RingVerdict(flagged=True)}

    metrics = summarize([legit], verdicts, holds={}, ack_latencies_ms=[], detection_latencies_ms=[],
                         peak_node_count=1, peak_edge_count=1)

    assert metrics.false_positives == 1


# --- negative / edge cases ---

def test_empty_run_produces_all_zero_metrics_without_crashing():
    metrics = summarize([], {}, {}, ack_latencies_ms=[], detection_latencies_ms=[], peak_node_count=0, peak_edge_count=0)

    assert metrics.rings_injected == 0
    assert metrics.rings_caught == 0
    assert metrics.false_positives == 0


def test_ring_below_fraction_threshold_counts_as_missed_not_caught():
    txs = [ring_tx(i) for i in range(6)]
    verdicts = {tx.transaction_id: RingVerdict(flagged=True) for tx in txs}
    holds = {tx.transaction_id: held(tx) for tx in txs[:1]}  # 1 of 6 = ~0.17, below 0.5

    metrics = summarize(txs, verdicts, holds, ack_latencies_ms=[], detection_latencies_ms=[],
                         peak_node_count=0, peak_edge_count=0, ring_caught_edge_fraction=0.5)

    assert metrics.rings_caught == 0
    assert metrics.rings_missed == 1


def test_correctly_flagged_ring_fraud_transaction_is_not_counted_as_false_positive():
    txs = [ring_tx(i) for i in range(6)]
    verdicts = {tx.transaction_id: RingVerdict(flagged=True) for tx in txs}
    holds = {tx.transaction_id: held(tx) for tx in txs}

    metrics = summarize(txs, verdicts, holds, ack_latencies_ms=[], detection_latencies_ms=[],
                         peak_node_count=0, peak_edge_count=0)

    assert metrics.false_positives == 0  # flagged correctly — not a false positive


def test_transaction_with_no_verdict_entry_is_treated_as_not_flagged():
    legit = Transaction(transaction_id="L2", sender_id="A", receiver_id="B", amount=500, timestamp_ms=0, label=TxLabel.LEGIT)

    metrics = summarize([legit], verdicts={}, holds={}, ack_latencies_ms=[], detection_latencies_ms=[],
                         peak_node_count=0, peak_edge_count=0)

    assert metrics.false_positives == 0
