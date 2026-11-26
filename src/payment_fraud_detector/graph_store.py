"""Rolling-window in-memory transaction graph: incremental insert, window eviction. See LLD.md §3.3/3.4."""

from __future__ import annotations

from payment_fraud_detector.models import AccountStats, EdgeDirection, EdgeRecord, RollingWindowGraph, Transaction


def _evict_expired(stats: AccountStats, now_ms: int, window_ms: int) -> None:
    """Pop edges older than the window off the front of the deque (ADR-001: lazy, on-insert eviction)."""
    cutoff_ms = now_ms - window_ms
    while stats.edges and stats.edges[0].timestamp_ms < cutoff_ms:
        expired = stats.edges.popleft()
        stats.counterparty_counts[expired.counterparty_id] -= 1
        if stats.counterparty_counts[expired.counterparty_id] <= 0:
            del stats.counterparty_counts[expired.counterparty_id]
        if expired.direction is EdgeDirection.OUT:
            stats.out_amount_sum -= expired.amount
        else:
            stats.in_amount_sum -= expired.amount


def insert_transaction(graph: RollingWindowGraph, tx: Transaction) -> None:
    """Add a transaction as one OUT edge (sender) and one IN edge (receiver), evicting stale edges first.

    ponytail: accounts are never removed from `graph.accounts` even once their
    edge deque empties — the window bounds edges-per-account, not the number of
    distinct accounts ever seen in a run. Add an LRU sweep over `graph.accounts`
    if a very long run with many one-off accounts makes that dict itself the
    memory bottleneck (not expected at Phase 0's scale).
    """
    sender_stats = graph.accounts[tx.sender_id]
    receiver_stats = graph.accounts[tx.receiver_id]

    _evict_expired(sender_stats, tx.timestamp_ms, graph.window_ms)
    _evict_expired(receiver_stats, tx.timestamp_ms, graph.window_ms)

    out_edge = EdgeRecord(
        counterparty_id=tx.receiver_id,
        amount=tx.amount,
        timestamp_ms=tx.timestamp_ms,
        transaction_id=tx.transaction_id,
        direction=EdgeDirection.OUT,
    )
    sender_stats.edges.append(out_edge)
    sender_stats.counterparty_counts[tx.receiver_id] += 1
    sender_stats.out_amount_sum += tx.amount

    in_edge = EdgeRecord(
        counterparty_id=tx.sender_id,
        amount=tx.amount,
        timestamp_ms=tx.timestamp_ms,
        transaction_id=tx.transaction_id,
        direction=EdgeDirection.IN,
    )
    receiver_stats.edges.append(in_edge)
    receiver_stats.counterparty_counts[tx.sender_id] += 1
    receiver_stats.in_amount_sum += tx.amount


def node_count(graph: RollingWindowGraph) -> int:
    return len(graph.accounts)


def total_edge_count(graph: RollingWindowGraph) -> int:
    return sum(len(stats.edges) for stats in graph.accounts.values())
