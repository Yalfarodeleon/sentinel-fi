"""
WebSocket fan-out — subscribes to Redis Pub/Sub channels and broadcasts
every event to all connected dashboard clients.

Each client gets its own bounded asyncio.Queue. If a client can't keep up,
its queue fills and the client is evicted. Other clients are unaffected.
This is the standard pattern for "many subscribers, one upstream firehose."
"""

import asyncio
import logging
from typing import AsyncIterator

import redis.asyncio as aioredis

from src.config import ANOMALY_CHANNEL, TXN_CHANNEL

logger = logging.getLogger(__name__)

# Per-client buffer. If the client falls more than this many events behind,
# it is dropped. 100 events ≈ ~1 second of buffer at 75 TPS — plenty for a
# normal browser, fatal for a frozen tab.
CLIENT_QUEUE_MAXSIZE = 100


class Broadcaster:
    """
    One Pub/Sub subscriber, many WebSocket clients.

    Lifecycle:
        - .start() launches the Pub/Sub listener as a background task
        - .subscribe() returns an async iterator for one WS client
        - .stop() cancels the listener and drains
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis
        self._clients: set[asyncio.Queue] = set()
        self._listener_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Spawn the Pub/Sub listener task."""
        self._listener_task = asyncio.create_task(self._listen())
        logger.info("Broadcaster started")

    async def stop(self) -> None:
        """Cancel listener and clean up."""
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        logger.info("Broadcaster stopped")

    async def subscribe(self) -> AsyncIterator[dict]:
        """
        Yield events for one WebSocket client. Caller awaits on this in a
        for-loop and forwards each event to its socket.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE_MAXSIZE)
        self._clients.add(queue)
        logger.info(f"Client subscribed. Total: {len(self._clients)}")
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            self._clients.discard(queue)
            logger.info(f"Client unsubscribed. Total: {len(self._clients)}")

    async def _listen(self) -> None:
        """
        Background task: subscribes to Redis channels and fans messages
        out to every connected client's queue.
        """
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(TXN_CHANNEL, ANOMALY_CHANNEL)
        logger.info(f"Listening on {TXN_CHANNEL}, {ANOMALY_CHANNEL}")

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                # Tag with channel so frontend knows what kind of event it is.
                # Channel names come back as bytes from redis-py.
                channel = message["channel"]
                if isinstance(channel, bytes):
                    channel = channel.decode()

                event = {
                    "type": "txn" if channel == TXN_CHANNEL else "anomaly",
                    "data": message["data"]
                    if isinstance(message["data"], str)
                    else message["data"].decode(),
                }

                # Fan out. Drop slow clients rather than block fast ones.
                for queue in list(self._clients):
                    try:
                        queue.put_nowait(event)
                    except asyncio.QueueFull:
                        logger.warning(
                            "Slow client queue full — dropping event"
                        )
        except asyncio.CancelledError:
            await pubsub.unsubscribe()
            await pubsub.aclose()
            raise