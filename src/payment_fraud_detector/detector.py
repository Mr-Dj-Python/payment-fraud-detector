"""Heuristic ring detection: fan-out/fan-in, hop-chain, near-cycle, verdict aggregation. See LLD.md §3.5, C4.html Level 3."""

from __future__ import annotations

from payment_fraud_detector import config
from payment_fraud_detector.models import AccountStats, EdgeDirection, RingReason, RingVerdict, RollingWindowGraph, Transaction


def _distinct_counterparties(stats: AccountStats, direction: EdgeDirection) -> set[str]:
    """Direction-filtered distinct counterparties within the window.

    ponytail: AccountStats.counterparty_counts (LLD §3.3) tracks counts across
    BOTH directions combined, so it can't answer "distinct OUT counterparties"
    on its own — recomputed here from the bounded edges deque instead of
    reshaping the already-tested graph_store/models. Still window-bounded
    (O(window size), not O(history)); just not O(1) like counterparty_counts is.
    """
    return {e.counterparty_id for e in stats.edges if e.direction is direction}


def check_fan_out(stats: AccountStats, threshold: int = config.FAN_OUT_THRESHOLD) -> bool:
    return len(_distinct_counterparties(stats, EdgeDirection.OUT)) >= threshold


def check_fan_in(stats: AccountStats, threshold: int = config.FAN_IN_THRESHOLD) -> bool:
    return len(_distinct_counterparties(stats, EdgeDirection.IN)) >= threshold


def check_hop_chain(stats: AccountStats, max_span_ms: int = config.MAX_HOP_CHAIN_MS) -> bool:
    """True if any received (IN) edge is followed by an outgoing (OUT) edge within max_span_ms — a fast pass-through."""
    in_edges = [e for e in stats.edges if e.direction is EdgeDirection.IN]
    out_edges = [e for e in stats.edges if e.direction is EdgeDirection.OUT]
    for in_edge in in_edges:
        for out_edge in out_edges:
            if 0 <= out_edge.timestamp_ms - in_edge.timestamp_ms <= max_span_ms:
                return True
    return False


def check_near_cycle(stats: AccountStats, max_span_ms: int = config.MAX_NEAR_CYCLE_MS) -> set[str]:
    """Counterparties this account sent to AND received from within max_span_ms of each other.

    Originally "any round-trip anywhere in the window" with no time constraint —
    real tuning (Chunk 8) showed that flags ordinary "pay a friend back days
    later" traffic as false positives. Requiring the round-trip to be fast,
    same logic as hop-chain, is what actually distinguishes it from laundering.
    """
    out_edges = [e for e in stats.edges if e.direction is EdgeDirection.OUT]
    in_edges = [e for e in stats.edges if e.direction is EdgeDirection.IN]
    partners = set()
    for out_edge in out_edges:
        for in_edge in in_edges:
            if out_edge.counterparty_id == in_edge.counterparty_id and abs(in_edge.timestamp_ms - out_edge.timestamp_ms) <= max_span_ms:
                partners.add(out_edge.counterparty_id)
    return partners


def _add_reason(reasons: list[RingReason], reason: RingReason) -> None:
    if reason not in reasons:
        reasons.append(reason)


def detect(
    graph: RollingWindowGraph,
    tx: Transaction,
    fan_out_threshold: int = config.FAN_OUT_THRESHOLD,
    fan_in_threshold: int = config.FAN_IN_THRESHOLD,
    max_hop_chain_ms: int = config.MAX_HOP_CHAIN_MS,
    max_near_cycle_ms: int = config.MAX_NEAR_CYCLE_MS,
) -> RingVerdict:
    """Inspect only the local neighborhood (sender, receiver) of the edge just inserted (ADR-001).

    Hop-chain and near-cycle are checked on BOTH sides of the edge, not just the
    receiver — a pass-through mule shows the "received then quickly re-sent"
    pattern as the SENDER of the forwarding leg (origin->mule, then
    mule->cashout: the mule is the sender of that second edge), not its receiver.
    Checking receiver-only (the original version) missed exactly this case —
    found by test_main.py's end-to-end ring test actually failing, not by inspection.
    """
    sender_stats = graph.accounts[tx.sender_id]
    receiver_stats = graph.accounts[tx.receiver_id]

    reasons: list[RingReason] = []
    accounts_involved: set[str] = set()
    flagged_edges = []

    if check_fan_out(sender_stats, fan_out_threshold):
        _add_reason(reasons, RingReason.FAN_OUT)
        accounts_involved.add(tx.sender_id)
        flagged_edges.extend(e for e in sender_stats.edges if e.direction is EdgeDirection.OUT)

    if check_fan_in(receiver_stats, fan_in_threshold):
        _add_reason(reasons, RingReason.FAN_IN)
        accounts_involved.add(tx.receiver_id)
        flagged_edges.extend(e for e in receiver_stats.edges if e.direction is EdgeDirection.IN)

    if check_hop_chain(sender_stats, max_hop_chain_ms):
        _add_reason(reasons, RingReason.HOP_CHAIN)
        accounts_involved.add(tx.sender_id)

    if check_hop_chain(receiver_stats, max_hop_chain_ms):
        _add_reason(reasons, RingReason.HOP_CHAIN)
        accounts_involved.add(tx.receiver_id)

    near_cycle_partners = check_near_cycle(sender_stats, max_near_cycle_ms) | check_near_cycle(receiver_stats, max_near_cycle_ms)
    if near_cycle_partners:
        _add_reason(reasons, RingReason.NEAR_CYCLE)
        accounts_involved.add(tx.sender_id)
        accounts_involved.add(tx.receiver_id)
        accounts_involved.update(near_cycle_partners)

    flagged = bool(reasons)
    confidence_score = min(1.0, len(reasons) / len(RingReason)) if flagged else 0.0

    return RingVerdict(
        flagged=flagged,
        reason_codes=reasons,
        confidence_score=confidence_score,
        accounts_involved=accounts_involved,
        flagged_edges=flagged_edges,
    )
