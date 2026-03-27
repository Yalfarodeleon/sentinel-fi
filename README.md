# Sentinel-fi

A streaming data pipeline that ingests high-throughput financial transactions, detects anomalies in real time using Redis-backed sliding windows, and batch-persists results — built to demonstrate concurrent systems design, observability, and production-grade Python.

---

## Why This Project

Financial platforms process thousands of transactions per second and need to flag fraud *before* it settles — not in a nightly batch job. This engine simulates that constraint: a producer generates 50–100 transactions/sec, a consumer evaluates every one against multiple anomaly rules using only in-memory state, and a batch inserter buffers writes to avoid hammering the database. The entire hot path touches Redis and never hits disk.

---

## Architecture

```
┌──────────────┐        XADD         ┌──────────────────┐      XREADGROUP      ┌────────────────────┐
│              │ ──────────────────▶ │                  │ ───────────────────▶ │                    │
│   Producer   │   100 TPS (JSON)    │   Redis Stream   │   Consumer Group     │   FastAPI Consumer │
│  (asyncio)   │                     │                  │                      │   (async workers)  │
└──────────────┘                     └──────────────────┘                      └─────────┬──────────┘
                                                                                        │
                                                                           ┌────────────┴────────────┐
                                                                           │                         │
                                                                           ▼                         ▼
                                                                 ┌──────────────────┐     ┌─────────────────┐
                                                                 │  Redis Sorted    │     │  Batch Inserter │
                                                                 │  Sets (ZADD)     │     │  (50 txns / 5s) │
                                                                 │  5-min sliding   │     │                 │
                                                                 │  window per user │     └────────┬────────┘
                                                                 └────────┬─────────┘              │
                                                                          │                        ▼
                                                                          │              ┌─────────────────┐
                                                                          └─────────────▶│   PostgreSQL    │
                                                                            anomaly      │   (txn log +    │
                                                                            flags        │   anomaly flags)│
                                                                                         └─────────────────┘
```

**Data flow:** The Producer pushes JSON transactions into a Redis Stream via `XADD`. The Consumer reads them through a Consumer Group (`XREADGROUP`), which enables horizontal scaling — multiple workers split the load automatically with built-in acknowledgment and at-least-once delivery. Each transaction is evaluated against the anomaly rules using a Redis Sorted Set sliding window (no database queries on the hot path). Processed transactions accumulate in an in-memory buffer that flushes to PostgreSQL in batches.

---

## Anomaly Detection Rules

All detection runs against a **5-minute sliding window** stored as a Redis Sorted Set per user. Scores are Unix timestamps; members encode `txn_id:amount`. The window is maintained with a pipelined `ZADD` → `ZREMRANGEBYSCORE` → `ZRANGEBYSCORE` → `EXPIRE` in a single Redis round-trip.

| Rule | What It Catches | Logic | Severity |
|------|----------------|-------|----------|
| **Velocity** | Automated / bot-like bursts | > 10 transactions from one user in 60 seconds | HIGH |
| **Spike** | Stolen card, account takeover | Single amount > 3× the user's 5-minute rolling average | CRITICAL |
| **Rapid-fire** | Script-driven fraud | Two transactions from the same user within 1 second | MEDIUM |

Rules are isolated async functions in `anomaly_rules.py` — adding a new rule means writing one function and appending it to the `evaluate_transaction` gather call.

---

## Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Language | Python 3.11+ (asyncio) | Async-native, clean concurrency model |
| Message Bus | Redis Streams | Consumer groups + acknowledgment without Kafka's operational weight |
| Hot State | Redis Sorted Sets | O(log N) sliding window queries, sub-millisecond latency |
| Persistence | PostgreSQL | ACID guarantees for audit trail and historical analysis |
| Batch Writer | In-memory buffer | Dual-trigger flush (size OR timer) — same pattern as AWS Kinesis Firehose |

---

## Quick Start

### Prerequisites

- Python 3.11+
- Docker (for Redis)

### Setup

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/realtime-anomaly-engine.git
cd realtime-anomaly-engine

# Start Redis
docker run -d --name redis-anomaly -p 6379:6379 redis:7-alpine

# Install dependencies
pip install -r requirements.txt

# Terminal 1 — Start the consumer
python engine/consumer.py

# Terminal 2 — Start the producer
python engine/producer.py --tps 75
```

You should see anomaly flags within seconds:

```
[Producer] Connected to Redis at localhost:6379
[Producer] Target rate: 75 TPS → stream 'transactions:stream'
...
🚨 [CRITICAL] SPIKE: user=user_0023 $1,847.32 — $1847.32 is 14.2x the 5-min avg of $130.09 (threshold: 3.0x)
🚨 [HIGH] VELOCITY: user=user_0012 — 12 transactions in last 60s (threshold: 10)
```

### Scaling Workers

```bash
# Multiple async workers in a single process
python engine/consumer.py --workers 3

# Or separate processes for true parallelism
python engine/consumer.py --worker-id w1 &
python engine/consumer.py --worker-id w2 &
```

---

## Project Structure

```
realtime-anomaly-engine/
├── README.md
├── .gitignore
├── requirements.txt
└── engine/
    ├── __init__.py
    ├── config.py              # All tunable parameters (TPS, thresholds, Redis/PG settings)
    ├── producer.py            # Async mock transaction generator → Redis Stream
    ├── consumer.py            # Stream reader with Consumer Group + worker orchestration
    ├── anomaly_rules.py       # Pluggable detection rules (velocity, spike, rapid-fire)
    └── batch_inserter.py      # In-memory buffer with size + timer dual-trigger flush
```

---

## Key Design Decisions

**Why Redis Sorted Sets instead of application-level state?**
Sorted Sets give you O(log N) range queries scored by timestamp — purpose-built for sliding windows. Keeping state in Redis rather than in-process memory means multiple consumer workers share the same window without coordination. It also survives worker restarts.

**Why pipeline all sorted set operations?**
Each transaction requires a `ZADD`, `ZREMRANGEBYSCORE`, `ZRANGEBYSCORE`, and `EXPIRE`. Without pipelining, that's 4 network round-trips × 75 TPS = 300 Redis calls/sec. With `redis.pipeline()`, it's 75 round-trips/sec — a 4× reduction in network overhead from a single line of code.

**Why batch inserts instead of per-transaction writes?**
At 75 TPS, individual `INSERT` statements would create 75 database connections or round-trips per second — each carrying TCP overhead, connection pool pressure, and WAL fsync cost. The `BatchInserter` accumulates 50 transactions and flushes them in a single `executemany()`, reducing write amplification by ~50×. The dual trigger (size OR 5-second timer) ensures both throughput under load and freshness under low traffic.

**Why Redis Streams over Kafka?**
At this scale (< 1,000 TPS), Redis Streams provide the same consumer-group semantics — automatic load balancing, message acknowledgment, pending entry lists — without requiring a JVM, ZooKeeper/KRaft, or multi-broker configuration. This is a deliberate scope decision, not a limitation.

---

## Roadmap

- [ ] **Observability** — Prometheus metrics (`prometheus_client`) for TPS, anomaly rate, p99 latency; Grafana dashboard
- [ ] **PostgreSQL integration** — `asyncpg` with connection pooling, schema migrations via Alembic
- [ ] **FastAPI endpoints** — `/health`, `/metrics`, `/anomalies?user_id=X` query API
- [ ] **Docker Compose** — Single `docker compose up` for Redis + Postgres + engine
- [ ] **Additional rules** — Geo-impossible travel, merchant category mismatch, time-of-day deviation
- [ ] **Load testing** — Benchmark with 500+ TPS, measure consumer lag and detection latency

---

## License

MIT
