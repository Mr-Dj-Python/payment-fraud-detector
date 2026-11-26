"""Synthetic traffic generation: legit, benign fan-out, ring-fraud (LLD.md §3.1). Chunk 4."""

import random

import pytest

from payment_fraud_detector.models import TxLabel
from payment_fraud_detector.simulator import generate_benign_fanout, generate_legit_stream, generate_ring


# --- happy path ---

def test_generate_ring_fans_out_then_fans_in_with_exact_amount_conservation():
    txs = generate_ring(
        ring_id="ring-1", origin_account="origin", mule_accounts=["m1", "m2", "m3", "m4", "m5"],
        cashout_account="cashout", total_amount=100_000, start_ms=0, hop_interval_ms=50,
    )

    assert len(txs) == 10
    assert all(tx.label is TxLabel.RING_FRAUD for tx in txs)
    assert all(tx.ring_id == "ring-1" for tx in txs)

    fan_out = [tx for tx in txs if tx.sender_id == "origin"]
    fan_in = [tx for tx in txs if tx.receiver_id == "cashout"]
    assert len(fan_out) == 5
    assert len(fan_in) == 5
    assert sum(tx.amount for tx in fan_out) == 100_000  # no paise lost/gained in the split
    assert sum(tx.amount for tx in fan_in) == 100_000
    assert all(tx.timestamp_ms == 0 for tx in fan_out)
    assert all(tx.timestamp_ms == 50 for tx in fan_in)


def test_generate_benign_fanout_labels_as_benign_with_no_ring_id():
    txs = generate_benign_fanout(
        employer_id="employer", employee_ids=["e1", "e2", "e3", "e4"], amount_each=50_000, start_ms=0,
    )

    assert len(txs) == 4
    assert all(tx.label is TxLabel.BENIGN_FANOUT for tx in txs)
    assert all(tx.ring_id is None for tx in txs)
    assert all(tx.amount == 50_000 for tx in txs)
    assert {tx.receiver_id for tx in txs} == {"e1", "e2", "e3", "e4"}


def test_generate_legit_stream_produces_distinct_labeled_transactions():
    rng = random.Random(42)
    txs = generate_legit_stream(rng, account_pool=["A", "B", "C", "D"], count=10, start_ms=0, interval_ms=100)

    assert len(txs) == 10
    assert all(tx.label is TxLabel.LEGIT for tx in txs)
    assert len({tx.transaction_id for tx in txs}) == 10  # all unique ids
    assert [tx.timestamp_ms for tx in txs] == [i * 100 for i in range(10)]


# --- negative / edge cases ---

def test_generate_ring_rejects_empty_mule_list():
    with pytest.raises(ValueError):
        generate_ring("r1", "origin", [], "cashout", 1000, start_ms=0)


def test_generate_ring_rejects_non_positive_amount():
    with pytest.raises(ValueError):
        generate_ring("r1", "origin", ["m1"], "cashout", 0, start_ms=0)


def test_generate_ring_remainder_goes_to_first_mule_not_lost():
    # 100 split across 3 mules doesn't divide evenly — the remainder must still show up somewhere.
    txs = generate_ring("r1", "origin", ["m1", "m2", "m3"], "cashout", 100, start_ms=0)
    fan_out = [tx for tx in txs if tx.sender_id == "origin"]
    assert sum(tx.amount for tx in fan_out) == 100


def test_generate_benign_fanout_rejects_empty_employee_list():
    with pytest.raises(ValueError):
        generate_benign_fanout("employer", [], 1000, start_ms=0)


def test_generate_legit_stream_rejects_negative_count():
    with pytest.raises(ValueError):
        generate_legit_stream(random.Random(1), ["A", "B"], count=-1, start_ms=0)


def test_generate_legit_stream_rejects_too_small_account_pool():
    with pytest.raises(ValueError):
        generate_legit_stream(random.Random(1), ["A"], count=5, start_ms=0)
