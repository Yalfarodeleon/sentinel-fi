"""
Database connections — Redis (active) and PostgreSQL (future).
Provides async context managers for clean setup/teardown.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as aioredis

from src.config import REDIS_URL, STREAM_KEY, CONSUMER_GROUP


async def get_redis_pool() -> aioredis.ConnectionPool:
    """Create a decoded Redis connection pool."""
    return aioredis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)


@asynccontextmanager
async def redis_client() -> AsyncGenerator[aioredis.Redis, None]:
    """
    Async context manager that yields a Redis client and cleans up the pool.

    Usage:
        async with redis_client() as r:
            await r.ping()
    """
    pool = await get_redis_pool()
    client = aioredis.Redis(connection_pool=pool)
    try:
        await client.ping()
        yield client
    finally:
        await pool.aclose()


async def ensure_consumer_group(client: aioredis.Redis) -> None:
    """
    Create the consumer group if it doesn't exist yet.
    Uses '0' to read from the beginning of the stream (good for dev).
    In production, switch to '$' to only process new messages.
    """
    try:
        await client.xgroup_create(STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True)
        print(f"[Database] Created consumer group '{CONSUMER_GROUP}'")
    except aioredis.ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            print(f"[Database] Consumer group '{CONSUMER_GROUP}' already exists")
        else:
            raise


# ── PostgreSQL (future) ──
# When you're ready to add asyncpg:
#
# import asyncpg
#
# async def get_pg_pool() -> asyncpg.Pool:
#     return await asyncpg.create_pool(
#         host=POSTGRES_HOST,
#         port=POSTGRES_PORT,
#         database=POSTGRES_DB,
#         user=POSTGRES_USER,
#         password=POSTGRES_PASSWORD,
#         min_size=2,
#         max_size=10,
#     )