"""
Anomaly Rules — Each rule is a standalone async function that returns an
Anomaly model (or None). Adding a new rule means writing one function and
appending it to the gather call in evaluate_transaction().
"""

import asyncio

import redis.asyncio as aioredis

from src.config import (
    SLIDING_WINDOW_SECONDS,
    SPIKE_MULTIPLIER,
    VELOCITY_THRESHOLD,
    VELOCITY_WINDOW_SECONDS,
)
from src.models import Anomaly, Severity, Transaction


def _window_key(user_id: str) -> str:
    """Redis key for a user's sliding-window sorted set."""
    return f"user:{user_id}:spending_window"


async def update_sliding_window(
    r: aioredis.Redis,
    txn: Transaction,
) -> list[tuple[str, float]]:
    """
    Core sliding-window maintenance. Returns surviving entries in the
    5-minute window AFTER adding the new transaction.

    Uses a pipeline to batch all four Redis commands into a single
    round-trip — 4x fewer network calls at 75 TPS.

    Member format: "txn_id:amount" so we can recover dollar values
    from the window without a secondary lookup.
    """
    key = _window_key(txn.user_id)
    member = f"{txn.transaction_id}:{txn.amount}"
    cutoff = txn.timestamp - SLIDING_WINDOW_SECONDS

    pipe = r.pipeline(transaction=False)
    pipe.zadd(key, {member: txn.timestamp})
    pipe.zremrangebyscore(key, "-inf", cutoff)
    pipe.zrangebyscore(key, cutoff, "+inf", withscores=True)
    pipe.expire(key, SLIDING_WINDOW_SECONDS + 60)

    results = await pipe.execute()
    return results[2]


async def check_velocity(
    window_entries: list[tuple[str, float]],
    txn: Transaction,
) -> Anomaly | None:
    """
    Velocity: More than VELOCITY_THRESHOLD transactions from the same
    user in the last VELOCITY_WINDOW_SECONDS (60s).
    """
    velocity_cutoff = txn.timestamp - VELOCITY_WINDOW_SECONDS
    recent_count = sum(1 for _, score in window_entries if score >= velocity_cutoff)

    if recent_count > VELOCITY_THRESHOLD:
        return Anomaly(
            rule="velocity",
            detail=(
                f"{recent_count} transactions in last "
                f"{VELOCITY_WINDOW_SECONDS}s (threshold: {VELOCITY_THRESHOLD})"
            ),
            severity=Severity.HIGH,
            transaction_id=txn.transaction_id,
            user_id=txn.user_id,
            amount=txn.amount,
            timestamp=txn.timestamp,
        )
    return None


async def check_spike(
    window_entries: list[tuple[str, float]],
    txn: Transaction,
) -> Anomaly | None:
    """
    Spike: Transaction amount exceeds SPIKE_MULTIPLIER times the rolling
    average. Requires at least 3 prior transactions to avoid false positives.
    """
    if len(window_entries) < 4:
        return None

    amounts = []
    for member, _ in window_entries:
        try:
            amt = float(member.rsplit(":", 1)[1])
            amounts.append(amt)
        except (ValueError, IndexError):
            continue

    if not amounts:
        return None

    rolling_avg = sum(amounts) / len(amounts)

    if rolling_avg > 0 and txn.amount > (SPIKE_MULTIPLIER * rolling_avg):
        ratio = txn.amount / rolling_avg
        return Anomaly(
            rule="spike",
            detail=(
                f"${txn.amount:.2f} is {ratio:.1f}x the "
                f"5-min avg of ${rolling_avg:.2f} "
                f"(threshold: {SPIKE_MULTIPLIER}x)"
            ),
            severity=Severity.CRITICAL if ratio > 5 else Severity.HIGH,
            transaction_id=txn.transaction_id,
            user_id=txn.user_id,
            amount=txn.amount,
            timestamp=txn.timestamp,
        )
    return None


async def check_rapid_fire(
    window_entries: list[tuple[str, float]],
    txn: Transaction,
) -> Anomaly | None:
    """
    Rapid-fire: Another transaction from the same user within 1 second.
    Catches automated/bot-like behavior.
    """
    for _, score in window_entries:
        if abs(score - txn.timestamp) < 0.001:
            continue
        if txn.timestamp - score < 1.0:
            return Anomaly(
                rule="rapid_fire",
                detail=(
                    f"Transaction within {txn.timestamp - score:.3f}s "
                    f"of a previous transaction"
                ),
                severity=Severity.MEDIUM,
                transaction_id=txn.transaction_id,
                user_id=txn.user_id,
                amount=txn.amount,
                timestamp=txn.timestamp,
            )
    return None


async def evaluate_transaction(
    r: aioredis.Redis,
    txn: Transaction,
) -> list[Anomaly]:
    """
    Run all anomaly rules against a single transaction.
    Returns a list of triggered Anomaly objects (empty = clean).
    """
    window_entries = await update_sliding_window(r, txn)

    results = await asyncio.gather(
        check_velocity(window_entries, txn),
        check_spike(window_entries, txn),
        check_rapid_fire(window_entries, txn),
    )

    return [r for r in results if r is not None]
