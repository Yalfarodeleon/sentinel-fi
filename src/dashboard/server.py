"""
FastAPI dashboard server — REST endpoints + WebSocket live feed.

Run with:
    python -m src.dashboard.server
or
    uvicorn src.dashboard.server:app --host 0.0.0.0 --port 8000
"""

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from src.config import (
    CONSUMER_GROUP,
    DASHBOARD_HOST,
    DASHBOARD_PORT,
    FLAGGED_USERS_KEY,
    STREAM_KEY,
)
from src.dashboard.broadcaster import Broadcaster
from src.database import get_redis_pool

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")


# ── Lifespan: shared state lives here ─────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup: open Redis pool, start the broadcaster.
    Shutdown: stop the broadcaster, close the pool.
    """
    pool = await get_redis_pool()
    redis = aioredis.Redis(connection_pool=pool)
    await redis.ping()
    logger.info("Connected to Redis")

    broadcaster = Broadcaster(redis)
    await broadcaster.start()

    # Stash on app state for handlers to access.
    app.state.redis = redis
    app.state.broadcaster = broadcaster

    try:
        yield
    finally:
        await broadcaster.stop()
        await pool.aclose()
        logger.info("Shutdown complete")


app = FastAPI(title="Sentinel-FI Dashboard", lifespan=lifespan)


# ── Static frontend ───────────────────────────────────────────────────
FRONTEND_DIR = Path(__file__).parent / "static"


@app.get("/")
async def index():
    """Serve the dashboard HTML."""
    return FileResponse(FRONTEND_DIR / "index.html")


# ── REST endpoints ────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    """
    System health: stream depth (XLEN), pending entries (XPENDING),
    and connection state. Lets the frontend show a green/yellow/red dot.
    """
    redis: aioredis.Redis = app.state.redis
    try:
        stream_len = await redis.xlen(STREAM_KEY)

        # XPENDING summary returns [count, min_id, max_id, [[consumer, count], ...]]
        try:
            pending = await redis.xpending(STREAM_KEY, CONSUMER_GROUP)
            pending_count = pending["pending"] if pending else 0
        except Exception:
            # Group doesn't exist yet (no consumer running)
            pending_count = 0

        return {
            "status": "ok",
            "redis": "connected",
            "stream_length": stream_len,
            "pending_messages": pending_count,
        }
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "error": str(exc)},
        )


@app.get("/api/top-flagged")
async def top_flagged(limit: int = 10):
    """
    Top N users by anomaly count, from the sorted set populated by
    src/events.py. ZREVRANGE is O(log N + M) — fast enough for live polling.
    """
    redis: aioredis.Redis = app.state.redis
    raw = await redis.zrevrange(
        FLAGGED_USERS_KEY, 0, limit - 1, withscores=True
    )
    # raw is list[tuple[bytes, float]] — normalize to JSON-friendly shape
    return {
        "users": [
            {
                "user_id": uid.decode() if isinstance(uid, bytes) else uid,
                "anomaly_count": int(score),
            }
            for uid, score in raw
        ]
    }


@app.post("/api/reset-stats")
async def reset_stats():
    """
    Clears the flagged-users leaderboard. Useful for clean demos:
    open the dashboard, hit reset, run the producer for 30 seconds,
    show the rankings populating in real time.
    """
    redis: aioredis.Redis = app.state.redis
    await redis.delete(FLAGGED_USERS_KEY)
    return {"status": "reset", "key": FLAGGED_USERS_KEY}


# ── WebSocket: live event stream ──────────────────────────────────────
@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    """
    Live feed: every transaction + every anomaly, fanned out from
    the Pub/Sub broadcaster. Each client has its own bounded queue;
    slow clients are dropped without affecting others.
    """
    await websocket.accept()
    broadcaster: Broadcaster = app.state.broadcaster

    try:
        async for event in broadcaster.subscribe():
            # event["data"] is already a JSON string from the publisher,
            # so we wrap it once more with type info as JSON.
            await websocket.send_text(json.dumps(event))
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as exc:
        logger.exception(f"WS error: {exc}")


# ── Entrypoint ────────────────────────────────────────────────────────
def main():
    import uvicorn

    uvicorn.run(
        "src.dashboard.server:app",
        host=DASHBOARD_HOST,
        port=DASHBOARD_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()