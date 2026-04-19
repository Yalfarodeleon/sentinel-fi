"""
Batch Inserter — Accumulates processed transactions in memory and flushes
when either the buffer hits BATCH_SIZE or FLUSH_INTERVAL elapses.

Currently prints the SQL that would be executed. When you're ready for
real Postgres, swap _flush() internals with asyncpg's executemany().
"""

import asyncio
from typing import Optional

from src.config import BATCH_SIZE, FLUSH_INTERVAL_SECONDS
from src.models import Anomaly, Transaction


class BatchInserter:
    """
    Collects transactions and anomalies, then bulk-flushes on two triggers:
      1. Buffer reaches BATCH_SIZE (50)
      2. Timer hits FLUSH_INTERVAL_SECONDS (5s)

    Same dual-trigger pattern used by AWS Kinesis Firehose.
    """

    def __init__(
        self,
        batch_size: int = BATCH_SIZE,
        flush_interval: float = FLUSH_INTERVAL_SECONDS,
    ):
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._txn_buffer: list[Transaction] = []
        self._anomaly_buffer: list[Anomaly] = []
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._total_flushed = 0
        self._total_anomalies_flushed = 0

    async def start(self) -> None:
        """Start the background flush timer."""
        self._flush_task = asyncio.create_task(self._periodic_flush())
        print(
            f"[BatchInserter] Started — flush every {self._batch_size} txns "
            f"or {self._flush_interval}s"
        )

    async def add(self, txn: Transaction, anomalies: list[Anomaly]) -> None:
        """Add a processed transaction to the buffer. Flushes if full."""
        async with self._lock:
            self._txn_buffer.append(txn)
            self._anomaly_buffer.extend(anomalies)

            if len(self._txn_buffer) >= self._batch_size:
                await self._flush("size")

    async def _periodic_flush(self) -> None:
        """Background task: flush on a timer regardless of buffer size."""
        try:
            while True:
                await asyncio.sleep(self._flush_interval)
                async with self._lock:
                    if self._txn_buffer:
                        await self._flush("timer")
        except asyncio.CancelledError:
            pass

    async def _flush(self, trigger: str) -> None:
        """
        Drain the buffer and 'write' to the database.

        To go live with asyncpg, replace the print block with:
            async with self._pg_pool.acquire() as conn:
                await conn.executemany(TXN_SQL, [t.model_dump() for t in txns])
                await conn.executemany(ANO_SQL, [a.model_dump() for a in anomalies])
        """
        txns = self._txn_buffer.copy()
        anomalies = self._anomaly_buffer.copy()
        self._txn_buffer.clear()
        self._anomaly_buffer.clear()

        self._total_flushed += len(txns)
        self._total_anomalies_flushed += len(anomalies)

        print(f"\n{'='*60}")
        print(
            f"[BatchInserter] FLUSH ({trigger}) — "
            f"{len(txns)} txns, {len(anomalies)} anomalies"
        )
        print(
            f"[BatchInserter] Lifetime: "
            f"{self._total_flushed} txns, "
            f"{self._total_anomalies_flushed} anomalies"
        )
        print(f"{'='*60}")

        print(
            f"  SQL: INSERT INTO transactions "
            f"(id, user_id, amount, timestamp, merchant) "
            f"VALUES ... ({len(txns)} rows)"
        )

        if anomalies:
            print(
                f"  SQL: INSERT INTO anomalies "
                f"(transaction_id, user_id, rule, severity, detail) "
                f"VALUES ... ({len(anomalies)} rows)"
            )

        sample = txns[0]
        print(
            f"  Sample: user={sample.user_id} "
            f"amount=${sample.amount:.2f} "
            f"merchant={sample.merchant}"
        )

        if anomalies:
            a = anomalies[0]
            print(f"  Flag:   [{a.severity.value}] {a.rule} — {a.detail}")
        print()

    async def shutdown(self) -> None:
        """Cancel the timer and flush remaining data."""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        async with self._lock:
            if self._txn_buffer:
                await self._flush("shutdown")

        print(
            f"[BatchInserter] Shut down. Final: "
            f"{self._total_flushed} txns, "
            f"{self._total_anomalies_flushed} anomalies"
        )