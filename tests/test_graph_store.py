"""Rolling-window eviction, counterparty counter decrement, peak tracking (LLD.md §3.3/3.4). Chunk 3."""

from payment_fraud_detector.graph_store import insert_transaction, node_count, total_edge_count
from payment_fraud_detector.models import RollingWindowGraph, Transaction, TxLabel

WINDOW_MS = 300_000  # 5 minutes


def make_tx(tx_id, sender, receiver, amount, ts):
    return Transaction(
        transaction_id=tx_id, sender_id=sender, receiver_id=receiver,
        amount=amount, timestamp_ms=ts, label=TxLabel.LEGIT,
    )


# --- happy path ---

def test_single_transaction_updates_both_sides():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    insert_transaction(graph, make_tx("t1", "A", "B", 1000, ts=0))

    assert graph.accounts["A"].out_amount_sum == 1000
    assert graph.accounts["A"].counterparty_counts["B"] == 1
    assert len(graph.accounts["A"].edges) == 1

    assert graph.accounts["B"].in_amount_sum == 1000
    assert graph.accounts["B"].counterparty_counts["A"] == 1
    assert len(graph.accounts["B"].edges) == 1


def test_fan_out_accumulates_distinct_counterparties():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    for i, receiver in enumerate(["B", "C", "D"]):
        insert_transaction(graph, make_tx(f"t{i}", "A", receiver, 100, ts=i))

    assert len(graph.accounts["A"].counterparty_counts) == 3
    assert graph.accounts["A"].out_amount_sum == 300


def test_node_and_edge_counts_reflect_inserted_transactions():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    insert_transaction(graph, make_tx("t1", "A", "B", 100, ts=0))
    insert_transaction(graph, make_tx("t2", "B", "C", 200, ts=1))

    assert node_count(graph) == 3  # A, B, C
    assert total_edge_count(graph) == 4  # A-out, B-in, B-out, C-in


# --- negative / edge cases ---

def test_edge_older_than_window_is_evicted_on_next_insert():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    insert_transaction(graph, make_tx("t1", "A", "B", 1000, ts=0))
    # second tx on A, far enough later that t1's edge has aged out of the window
    insert_transaction(graph, make_tx("t2", "A", "C", 500, ts=WINDOW_MS + 1))

    stats = graph.accounts["A"]
    assert len(stats.edges) == 1
    assert stats.out_amount_sum == 500  # t1's 1000 evicted, only t2 remains
    assert "B" not in stats.counterparty_counts  # key removed entirely, not left at 0


def test_edge_exactly_at_cutoff_boundary_is_not_evicted():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    insert_transaction(graph, make_tx("t1", "A", "B", 1000, ts=0))
    # cutoff = now_ms - window_ms; an edge timestamped exactly at cutoff must survive (< cutoff evicts, not <=)
    insert_transaction(graph, make_tx("t2", "A", "C", 500, ts=WINDOW_MS))

    stats = graph.accounts["A"]
    assert len(stats.edges) == 2
    assert stats.out_amount_sum == 1500


def test_self_transfer_does_not_crash_and_records_both_edges():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    insert_transaction(graph, make_tx("t1", "A", "A", 100, ts=0))

    stats = graph.accounts["A"]
    assert len(stats.edges) == 2  # one OUT, one IN, same account
    assert stats.out_amount_sum == 100
    assert stats.in_amount_sum == 100
