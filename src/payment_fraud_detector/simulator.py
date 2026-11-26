"""Synthetic transaction generator: legit, benign fan-out, injected ring-fraud traffic. See LLD.md §3.1, HLD.md §2/§3."""

from __future__ import annotations

import random
import uuid

from payment_fraud_detector.models import Transaction, TxLabel


def _new_id() -> str:
    return uuid.uuid4().hex


def generate_legit_stream(
    rng: random.Random, account_pool: list[str], count: int, start_ms: int, interval_ms: int = 1000
) -> list[Transaction]:
    """`count` ordinary P2P transfers between random pairs drawn from account_pool."""
    if count < 0:
        raise ValueError("count must be >= 0")
    if len(account_pool) < 2:
        raise ValueError("account_pool must have at least 2 accounts")

    transactions = []
    for i in range(count):
        sender, receiver = rng.sample(account_pool, 2)
        transactions.append(
            Transaction(
                transaction_id=_new_id(),
                sender_id=sender,
                receiver_id=receiver,
                amount=rng.randint(100, 500_00),
                timestamp_ms=start_ms + i * interval_ms,
                label=TxLabel.LEGIT,
            )
        )
    return transactions


def generate_benign_fanout(
    employer_id: str, employee_ids: list[str], amount_each: int, start_ms: int, interval_ms: int = 100
) -> list[Transaction]:
    """One employer paying many employees the same round amount — looks like fan-out, isn't fraud."""
    if not employee_ids:
        raise ValueError("employee_ids must not be empty")
    if amount_each <= 0:
        raise ValueError("amount_each must be positive")

    return [
        Transaction(
            transaction_id=_new_id(),
            sender_id=employer_id,
            receiver_id=employee_id,
            amount=amount_each,
            timestamp_ms=start_ms + i * interval_ms,
            label=TxLabel.BENIGN_FANOUT,
        )
        for i, employee_id in enumerate(employee_ids)
    ]


def generate_ring(
    ring_id: str,
    origin_account: str,
    mule_accounts: list[str],
    cashout_account: str,
    total_amount: int,
    start_ms: int,
    hop_interval_ms: int = 50,
) -> list[Transaction]:
    """origin -> N mules (fan-out) -> cashout_account (fan-in), split exactly across mules.

    Integer division leaves a remainder when total_amount isn't evenly divisible
    by the mule count — given to the first mule rather than dropped, so the fan-out
    amounts always sum back to exactly total_amount (money must never silently lose
    or gain paise, per LLD §2).
    """
    if not mule_accounts:
        raise ValueError("mule_accounts must not be empty")
    if total_amount <= 0:
        raise ValueError("total_amount must be positive")

    share, remainder = divmod(total_amount, len(mule_accounts))
    transactions = []

    for i, mule in enumerate(mule_accounts):
        amount = share + (remainder if i == 0 else 0)
        transactions.append(
            Transaction(
                transaction_id=_new_id(),
                sender_id=origin_account,
                receiver_id=mule,
                amount=amount,
                timestamp_ms=start_ms,
                label=TxLabel.RING_FRAUD,
                ring_id=ring_id,
            )
        )

    fan_in_ts = start_ms + hop_interval_ms
    for i, mule in enumerate(mule_accounts):
        amount = share + (remainder if i == 0 else 0)
        transactions.append(
            Transaction(
                transaction_id=_new_id(),
                sender_id=mule,
                receiver_id=cashout_account,
                amount=amount,
                timestamp_ms=fan_in_ts,
                label=TxLabel.RING_FRAUD,
                ring_id=ring_id,
            )
        )

    return transactions
