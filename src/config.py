"""
Centralized configuration — reads from environment variables with fallback
defaults. In production you'd use pydantic-settings; this keeps it dependency-light.
"""

import os


def _env(key: str, default: str) -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _env_float(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))


# ── Redis ──
REDIS_HOST = _env("REDIS_HOST", "localhost")
REDIS_PORT = _env_int("REDIS_PORT", 6379)
REDIS_DB = _env_int("REDIS_DB", 0)
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

# ── PostgreSQL ──
POSTGRES_HOST = _env("POSTGRES_HOST", "localhost")
POSTGRES_PORT = _env_int("POSTGRES_PORT", 5432)
POSTGRES_DB = _env("POSTGRES_DB", "sentinel_fi")
POSTGRES_USER = _env("POSTGRES_USER", "sentinel")
POSTGRES_PASSWORD = _env("POSTGRES_PASSWORD", "changeme")

# ── Redis Stream ──
STREAM_KEY = "transactions:stream"
CONSUMER_GROUP = "anomaly_workers"
CONSUMER_NAME_PREFIX = "worker"

# ── Producer ──
DEFAULT_TPS = _env_int("PRODUCER_TPS", 75)
USER_POOL_SIZE = 50
MERCHANTS = [
    "Amazon", "Walmart", "Target", "Starbucks", "Shell Gas",
    "Uber", "DoorDash", "Apple Store", "Best Buy", "Costco",
    "Netflix", "Spotify", "Steam", "Home Depot", "Whole Foods",
]
NORMAL_AMOUNT_RANGE = (5.00, 200.00)
SPIKE_AMOUNT_RANGE = (800.00, 5000.00)
SPIKE_PROBABILITY = 0.05

# ── Anomaly Thresholds ──
SLIDING_WINDOW_SECONDS = _env_int("SLIDING_WINDOW_SECONDS", 300)
VELOCITY_WINDOW_SECONDS = _env_int("VELOCITY_WINDOW_SECONDS", 60)
VELOCITY_THRESHOLD = _env_int("VELOCITY_THRESHOLD", 10)
SPIKE_MULTIPLIER = _env_float("SPIKE_MULTIPLIER", 3.0)

# ── Batch Inserter ──
BATCH_SIZE = 50
FLUSH_INTERVAL_SECONDS = 5.0
