"""Mocked settlement hold: held vs. missed-window outcome (LLD.md §3.6). Chunk 6."""

import pytest

from payment_fraud_detector.interceptor import intercept
from payment_fraud_detector.models import HoldOutcome, RingVerdict, Transaction, TxLabel


def make_tx(ts):
    return Transaction(
        transaction_id="t1", sender_id="A", receiver_id="B",
        amount=1000, timestamp_ms=ts, label=TxLabel.RING_FRAUD, ring_id="r1",
    )


FLAGGED = RingVerdict(flagged=True, accounts_involved={"A", "B"})


# --- happy path ---

def test_detection_before_window_close_is_held():
    tx = make_tx(ts=0)
    hold = intercept(tx, FLAGGED, detection_time_ms=50, settlement_window_ms=1000)

    assert hold.outcome is HoldOutcome.HELD
    assert hold.accounts_frozen == {"A", "B"}
    assert hold.window_close_ms == 1000


def test_detection_exactly_at_window_close_is_still_held():
    tx = make_tx(ts=0)
    hold = intercept(tx, FLAGGED, detection_time_ms=1000, settlement_window_ms=1000)

    assert hold.outcome is HoldOutcome.HELD


# --- negative / edge cases ---

def test_detection_after_window_close_is_missed_with_no_frozen_accounts():
    tx = make_tx(ts=0)
    hold = intercept(tx, FLAGGED, detection_time_ms=1001, settlement_window_ms=1000)

    assert hold.outcome is HoldOutcome.MISSED
    assert hold.accounts_frozen == set()


def test_intercept_rejects_a_non_flagged_verdict():
    tx = make_tx(ts=0)
    not_flagged = RingVerdict(flagged=False)
    with pytest.raises(ValueError):
        intercept(tx, not_flagged, detection_time_ms=10, settlement_window_ms=1000)
