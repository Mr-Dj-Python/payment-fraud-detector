"""Dataclasses/enums: Transaction, EdgeRecord, AccountStats, RingVerdict, HoldRecord, RunMetrics. See LLD.md §3."""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from enum import Enum


class TxLabel(str, Enum):
    LEGIT = "legit"
    RING_FRAUD = "ring_fraud"
    BENIGN_FANOUT = "benign_fanout"


class EdgeDirection(str, Enum):
    IN = "in"
    OUT = "out"


class RingReason(str, Enum):
    FAN_OUT = "fan_out"
    FAN_IN = "fan_in"
    HOP_CHAIN = "hop_chain"
    NEAR_CYCLE = "near_cycle"


class HoldOutcome(str, Enum):
    HELD = "held"
    MISSED = "missed"


@dataclass
class Transaction:
    transaction_id: str
    sender_id: str
    receiver_id: str
    amount: int
    timestamp_ms: int
    label: TxLabel
    ring_id: str | None = None


@dataclass
class EdgeRecord:
    counterparty_id: str
    amount: int
    timestamp_ms: int
    transaction_id: str
    direction: EdgeDirection


@dataclass
class AccountStats:
    edges: collections.deque[EdgeRecord] = field(default_factory=collections.deque)
    counterparty_counts: collections.Counter[str] = field(default_factory=collections.Counter)
    out_amount_sum: int = 0
    in_amount_sum: int = 0


@dataclass
class RollingWindowGraph:
    window_ms: int
    accounts: collections.defaultdict[str, AccountStats] = field(
        default_factory=lambda: collections.defaultdict(AccountStats)
    )


@dataclass
class RingVerdict:
    flagged: bool
    reason_codes: list[RingReason] = field(default_factory=list)
    confidence_score: float = 0.0
    accounts_involved: set[str] = field(default_factory=set)
    flagged_edges: list[EdgeRecord] = field(default_factory=list)


@dataclass
class HoldRecord:
    transaction_id: str
    hold_time_ms: int
    window_close_ms: int
    outcome: HoldOutcome
    accounts_frozen: set[str] = field(default_factory=set)


@dataclass
class RunMetrics:
    rings_injected: int = 0
    rings_caught: int = 0
    rings_missed: int = 0
    false_positives: int = 0
    detection_latencies_ms: list[int] = field(default_factory=list)
    ack_latencies_ms: list[int] = field(default_factory=list)
    peak_node_count: int = 0
    peak_edge_count: int = 0
