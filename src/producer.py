"""
Mock Producer — Generates simulated financial transactions and pushes them
into a Redis Stream at a configurable rate.

Usage:
    python -m src.producer              # Default TPS from config
    python -m src.producer --tps 100    # Override TPS
"""

import asyncio
import argparse
import random
import time
import uuid

from src.config import (
    DEFAULT_TPS, USER_POOL_SIZE,
    MERCHANTS, NORMAL_AMOUNT_RANGE, SPIKE_AMOUNT_RANGE, SPIKE_PROBABILITY,
    STREAM_KEY,
)
from src.database import redis_client
from src.models import Transaction


def generate_transaction() -> Transaction:
    """Build a single mock transaction with occasional anomalous spikes."""
    is_spike = random.random() < SPIKE_PROBABILITY
    amount_range = SPIKE_AMOUNT_RANGE if is_spike else NORMAL_AMOUNT_RANGE

    return Transaction(
        transaction_id=str(uuid.uuid4()),
        user_id=f"user_{random.randint(1, USER_POOL_SIZE):04d}",
        amount=round(random.uniform(*amount_range), 2),
        timestamp=time.time(),
        merchant=random.choice(MERCHANTS),
    )


async def produce(tps: int) -> None:
    """
    Main producer loop. Pushes `tps` transactions per second into the
    Redis Stream, using asyncio.sleep to pace the output.

    The stream is capped at ~50k entries so it doesn't grow unbounded
    during long dev sessions.
    """
    async with redis_client() as r:
        print(f"[Producer] Connected to Redis")
        print(f"[Producer] Target rate: {tps} TPS → stream '{STREAM_KEY}'")

        interval = 1.0 / tps
        total_sent = 0

        try:
            while True:
                loop_start = time.monotonic()
                txn = generate_transaction()

                # XADD with approximate maxlen cap
                await r.xadd(
                    STREAM_KEY,
                    {"payload": txn.model_dump_json()},
                    maxlen=50_000,
                    approximate=True,
                )
                total_sent += 1

                if total_sent % 500 == 0:
                    print(
                        f"[Producer] Sent {total_sent} | "
                        f"user={txn.user_id} "
                        f"amount=${txn.amount:.2f} "
                        f"merchant={txn.merchant}"
                    )

                elapsed = time.monotonic() - loop_start
                sleep_time = interval - elapsed
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

        except asyncio.CancelledError:
            print(f"\n[Producer] Shutting down. Total sent: {total_sent}")


def main():
    parser = argparse.ArgumentParser(description="Sentinel-FI: Mock transaction producer")
    parser.add_argument(
        "--tps", type=int, default=DEFAULT_TPS,
        help=f"Transactions per second (default: {DEFAULT_TPS})",
    )
    args = parser.parse_args()

    try:
        asyncio.run(produce(args.tps))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()