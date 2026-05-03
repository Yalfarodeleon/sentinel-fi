"""
Dashboard event publishing — fire-and-forget Pub/Sub for the live dashboard.

Called from the consumer hot path after each transaction is processed.
All writes are pipelined into a single Redis round-trip. Failures here
are swallowed: the dashboard is best-effort, and anomaly detection must
never break because the dashboard crashed.
"""

import redis.asyncio as aioredis

from src.config import ANOMALY_CHANNEL, FLAGGED_USERS_KEY, TXN_CHANNEL
from src.models import Anomaly, Transaction


async def publish_events(
    r: aioredis.Redis,
    txn: Transaction,
    anomalies: list[Anomaly],
) -> None:
    """
    Publish transaction + anomaly events for the live dashboard and
    update the flagged-user leaderboard.

    Pipelined so the whole fan-out is a single round-trip, regardless
    of how many anomalies fired.
    """
    try:
        pipe = r.pipeline(transaction=False)
        pipe.publish(TXN_CHANNEL, txn.model_dump_json())

        for a in anomalies:
            pipe.publish(ANOMALY_CHANNEL, a.model_dump_json())
            pipe.zincrby(FLAGGED_USERS_KEY, 1, a.user_id)

        await pipe.execute()
    except Exception as exc:
        # Dashboard publishing is best-effort. Log and move on —
        # never let a dashboard failure break anomaly detection.
        print(f"[events] publish failed (non-fatal): {exc}")