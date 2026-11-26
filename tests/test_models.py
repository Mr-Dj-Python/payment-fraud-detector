"""Dataclass/enum shape and mutable-default safety (LLD.md §3). Chunk 2."""

import pytest

from payment_fraud_detector.models import (
    AccountStats,
    EdgeDirection,
    HoldOutcome,
    RingReason,
    RingVerdict,
    RollingWindowGraph,
    Transaction,
    TxLabel,
)


# --- happy path ---

def test_legit_transaction_has_no_ring_id():
    tx = Transaction(
        transaction_id="t1", sender_id="A", receiver_id="B",
        amount=10000, timestamp_ms=0, label=TxLabel.LEGIT,
    )
    assert tx.ring_id is None


def test_ring_fraud_transaction_carries_ring_id():
    tx = Transaction(
        transaction_id="t2", sender_id="A", receiver_id="B",
        amount=500, timestamp_ms=0, label=TxLabel.RING_FRAUD, ring_id="ring-7",
    )
    assert tx.ring_id == "ring-7"


def test_rolling_window_graph_autovivifies_account_stats():
    graph = RollingWindowGraph(window_ms=300_000)
    stats = graph.accounts["new-account"]
    assert isinstance(stats, AccountStats)
    assert stats.out_amount_sum == 0
    assert len(stats.edges) == 0


# --- negative / edge cases ---

def test_invalid_tx_label_value_raises_value_error():
    with pytest.raises(ValueError):
        TxLabel("not_a_real_label")


def test_invalid_ring_reason_value_raises_value_error():
    with pytest.raises(ValueError):
        RingReason("bogus")


def test_invalid_hold_outcome_value_raises_value_error():
    with pytest.raises(ValueError):
        HoldOutcome("maybe")


def test_mutable_defaults_are_not_shared_between_instances():
    # Guards against the classic Python mutable-default-argument bug — field(default_factory=...)
    # must give each instance its own list/set, not one shared across every RingVerdict.
    v1 = RingVerdict(flagged=True)
    v2 = RingVerdict(flagged=True)
    v1.reason_codes.append(RingReason.FAN_OUT)
    v1.accounts_involved.add("mule-1")
    assert v2.reason_codes == []
    assert v2.accounts_involved == set()


def test_account_stats_deques_are_independent_per_instance():
    a = AccountStats()
    b = AccountStats()
    a.edges.append("fake-edge")
    assert list(b.edges) == []
