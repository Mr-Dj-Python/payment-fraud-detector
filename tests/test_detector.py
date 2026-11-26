"""One case per heuristic (fan-out/in, hop-chain, near-cycle) plus a benign-fan-out false-positive guard (LLD.md §3.5). Chunk 5."""

from payment_fraud_detector.detector import check_fan_in, check_fan_out, check_hop_chain, check_near_cycle, detect
from payment_fraud_detector.graph_store import insert_transaction
from payment_fraud_detector.models import RingReason, RollingWindowGraph, Transaction, TxLabel

WINDOW_MS = 300_000


def tx(tx_id, sender, receiver, amount, ts, label=TxLabel.LEGIT, ring_id=None):
    return Transaction(
        transaction_id=tx_id, sender_id=sender, receiver_id=receiver,
        amount=amount, timestamp_ms=ts, label=label, ring_id=ring_id,
    )


# --- happy path: each heuristic fires when it should ---

def test_fan_out_flags_when_distinct_receivers_reach_threshold():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    for i, receiver in enumerate(["m1", "m2", "m3"]):
        insert_transaction(graph, tx(f"t{i}", "origin", receiver, 100, ts=i))

    assert check_fan_out(graph.accounts["origin"], threshold=3) is True


def test_fan_in_flags_when_distinct_senders_reach_threshold():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    for i, sender in enumerate(["m1", "m2", "m3"]):
        insert_transaction(graph, tx(f"t{i}", sender, "cashout", 100, ts=i))

    assert check_fan_in(graph.accounts["cashout"], threshold=3) is True


def test_hop_chain_flags_fast_pass_through():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    insert_transaction(graph, tx("t1", "origin", "mule", 1000, ts=0))
    insert_transaction(graph, tx("t2", "mule", "cashout", 1000, ts=30))  # forwarded within 50ms

    assert check_hop_chain(graph.accounts["mule"], max_span_ms=50) is True


def test_near_cycle_flags_direct_round_trip():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    insert_transaction(graph, tx("t1", "A", "B", 1000, ts=0))
    insert_transaction(graph, tx("t2", "B", "A", 900, ts=10))  # B sends most of it back to A

    assert check_near_cycle(graph.accounts["A"]) == {"B"}


def test_detect_flags_full_ring_with_multiple_reason_codes():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    mules = ["m1", "m2", "m3"]
    last_verdict = None
    for i, mule in enumerate(mules):
        t = tx(f"out{i}", "origin", mule, 1000, ts=i)
        insert_transaction(graph, t)
    for i, mule in enumerate(mules):
        t = tx(f"in{i}", mule, "cashout", 1000, ts=100 + i)
        insert_transaction(graph, t)
        last_verdict = detect(graph, t, fan_out_threshold=3, fan_in_threshold=3, max_hop_chain_ms=200)

    assert last_verdict.flagged is True
    assert RingReason.FAN_IN in last_verdict.reason_codes
    assert "cashout" in last_verdict.accounts_involved


# --- negative / edge cases: heuristics must NOT fire below threshold / outside window ---

def test_fan_out_does_not_flag_below_threshold():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    for i, receiver in enumerate(["m1", "m2"]):
        insert_transaction(graph, tx(f"t{i}", "origin", receiver, 100, ts=i))

    assert check_fan_out(graph.accounts["origin"], threshold=3) is False


def test_fan_in_does_not_flag_below_threshold():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    for i, sender in enumerate(["m1", "m2"]):
        insert_transaction(graph, tx(f"t{i}", sender, "cashout", 100, ts=i))

    assert check_fan_in(graph.accounts["cashout"], threshold=3) is False


def test_hop_chain_does_not_flag_when_forward_is_too_slow():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    insert_transaction(graph, tx("t1", "origin", "mule", 1000, ts=0))
    insert_transaction(graph, tx("t2", "mule", "cashout", 1000, ts=5000))  # way outside the hop window

    assert check_hop_chain(graph.accounts["mule"], max_span_ms=50) is False


def test_near_cycle_does_not_flag_one_directional_transfer():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    insert_transaction(graph, tx("t1", "A", "B", 1000, ts=0))  # no money ever comes back

    assert check_near_cycle(graph.accounts["A"]) == set()


def test_near_cycle_does_not_flag_a_slow_pay_back():
    # Chunk 8 finding: a round trip that happens DAYS apart (ordinary "pay a friend
    # back later") must not be flagged — only a FAST round trip is suspicious.
    graph = RollingWindowGraph(window_ms=10_000_000)
    insert_transaction(graph, tx("t1", "A", "B", 1000, ts=0))
    insert_transaction(graph, tx("t2", "B", "A", 900, ts=5_000))  # far outside a 100ms cycle window

    assert check_near_cycle(graph.accounts["A"], max_span_ms=100) == set()


def test_detect_does_not_flag_an_ordinary_single_transaction():
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    t = tx("t1", "A", "B", 1000, ts=0)
    insert_transaction(graph, t)

    verdict = detect(graph, t)
    assert verdict.flagged is False
    assert verdict.reason_codes == []


def test_known_limitation_benign_payroll_fanout_above_threshold_is_a_false_positive():
    # Documents the exact ambiguity PRD §10 calls out: this detector's fan-out
    # check is a pure distinct-counterparty count, so a legitimate payroll run
    # that exceeds the threshold DOES get flagged — same as a real mule fan-out.
    # Not a bug to fix silently; it's the false-positive rate Phase 0 is meant to measure.
    graph = RollingWindowGraph(window_ms=WINDOW_MS)
    employer = "employer"
    last_verdict = None
    for i, employee in enumerate(["e1", "e2", "e3", "e4"]):
        t = tx(f"payroll{i}", employer, employee, 50_000, ts=i, label=TxLabel.BENIGN_FANOUT)
        insert_transaction(graph, t)
        last_verdict = detect(graph, t, fan_out_threshold=4)

    assert last_verdict.flagged is True
    assert RingReason.FAN_OUT in last_verdict.reason_codes
