"""Mocked settlement hold/escrow logic: held vs. missed-window outcome. See LLD.md §3.6."""

from __future__ import annotations

from payment_fraud_detector import config
from payment_fraud_detector.models import HoldOutcome, HoldRecord, RingVerdict, Transaction


def intercept(
    tx: Transaction,
    verdict: RingVerdict,
    detection_time_ms: int,
    settlement_window_ms: int = config.SETTLEMENT_WINDOW_MS,
) -> HoldRecord:
    """Only call this when verdict.flagged — calling it otherwise is a caller-contract bug, not a valid 'no hold' path."""
    if not verdict.flagged:
        raise ValueError("intercept() called with a non-flagged verdict — check verdict.flagged first")

    window_close_ms = tx.timestamp_ms + settlement_window_ms
    held = detection_time_ms <= window_close_ms  # exactly at close still counts — see ADR-006 boundary semantics
    return HoldRecord(
        transaction_id=tx.transaction_id,
        hold_time_ms=detection_time_ms,
        window_close_ms=window_close_ms,
        outcome=HoldOutcome.HELD if held else HoldOutcome.MISSED,
        accounts_frozen=verdict.accounts_involved if held else set(),
    )
