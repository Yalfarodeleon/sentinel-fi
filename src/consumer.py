"""
Anomaly Consumer — Reads transactions from the Redis Stream via a Consumer
Group, evaluates anomaly rules, and passes results to the BatchInserter.

Usage:
    python -m src.consumer                  # Single worker
    python -m src.consumer --workers 3      # 3 concurrent async workers
    python -m src.consumer --worker-id w2   # Named worker (multi-process)
"""

import argparse
import asyncio

import redis.asyncio as aioredis

from src.anomaly_rules import evaluate_transaction
from src.batch_inserter import BatchInserter
from src.config import CONSUMER_GROUP, CONSUMER_NAME_PREFIX, STREAM_KEY
from src.database import ensure_consumer_group, get_redis_pool
from src.models import Transaction


async def process_message(
    r: aioredis.Redis,
    inserter: BatchInserter,
    message_id: str,
    data: dict,
    stats: dict,
) -> None:
    """Deserialize, detect anomalies, and hand off to the batch inserter."""
    try:
        txn = Transaction.model_validate_json(data["payload"])
    except Exception as exc:
        print(f"[Consumer] Malformed message {message_id}: {exc}")
        return

    anomalies = await evaluate_transaction(r, txn)

    stats["processed"] += 1
    if anomalies:
        stats["flagged"] += 1
        for a in anomalies:
            stats["by_rule"][a.rule] = stats["by_rule"].get(a.rule, 0) + 1
            print(
                f"  🚨 [{a.severity.value}] {a.rule.upper()}: "
                f"user={a.user_id} ${a.amount:.2f} — {a.detail}"
            )

    await inserter.add(txn, anomalies)


async def consume(
    r: aioredis.Redis,
    inserter: BatchInserter,
    worker_name: str,
) -> None:
    """
    Core consumer loop. XREADGROUP pulls batches of 10 messages
    with a 2-second block timeout (yields to event loop when idle).
    """
    stats: dict = {"processed": 0, "flagged": 0, "by_rule": {}}
    report_every = 250

    print(f"[{worker_name}] Listening on '{STREAM_KEY}'...")

    try:
        while True:
            messages = await r.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=worker_name,
                streams={STREAM_KEY: ">"},
                count=10,
                block=2000,
            )

            if not messages:
                continue

            for stream_name, stream_messages in messages:
                for message_id, data in stream_messages:
                    await process_message(r, inserter, message_id, data, stats)
                    await r.xack(STREAM_KEY, CONSUMER_GROUP, message_id)

            if stats["processed"] % report_every == 0 and stats["processed"] > 0:
                rate = (stats["flagged"] / stats["processed"]) * 100
                print(
                    f"\n[{worker_name}] "
                    f"{stats['processed']} processed, "
                    f"{stats['flagged']} flagged ({rate:.1f}%), "
                    f"rules: {stats['by_rule']}\n"
                )

    except asyncio.CancelledError:
        print(f"\n[{worker_name}] Shutting down. Stats: {stats}")


async def run_workers(num_workers: int, worker_id: str | None) -> None:
    """Spin up Redis pool, batch inserter, and N consumer workers."""
    pool = await get_redis_pool()
    r = aioredis.Redis(connection_pool=pool)

    await r.ping()
    print("[Consumer] Connected to Redis")

    await ensure_consumer_group(r)

    inserter = BatchInserter()
    await inserter.start()

    tasks = []
    for i in range(num_workers):
        name = worker_id if worker_id else f"{CONSUMER_NAME_PREFIX}_{i}"
        tasks.append(asyncio.create_task(consume(r, inserter, name)))

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        await inserter.shutdown()
        await pool.aclose()


def main():
    parser = argparse.ArgumentParser(description="Sentinel-FI: Anomaly consumer")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--worker-id", type=str, default=None)
    args = parser.parse_args()

    try:
        asyncio.run(run_workers(args.workers, args.worker_id))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
