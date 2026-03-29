# Sentinel-FI

**Real-time financial anomaly detection engine** — ingests high-throughput transaction streams, flags fraud in milliseconds using Redis-backed sliding windows, and batch-persists results for audit.

Built to demonstrate concurrent systems design, streaming data pipelines, and production-grade Python.

---

## Why This Exists

Financial platforms process thousands of transactions per second and need to flag fraud *before* it settles — not in a nightly batch job. Sentinel-FI simulates that constraint: a producer generates 50–100 transactions/sec, a consumer evaluates every one against multiple anomaly rules using only in-memory state, and a batch inserter buffers writes to avoid hammering the database. The entire hot path touches Redis and never hits disk.

---

## Architecture

```
┌──────────────┐       XADD        ┌────────────────┐     XREADGROUP    ┌───────────────────┐
│   Producer   │ ────────────────▶ │  Redis Stream  │ ─────────────────▶│  Consumer Workers │
│  (asyncio)   │  100 TPS (JSON)   │                │  Consumer Group   │  (async Python)   │
└──────────────┘                   └────────────────┘                   └─────────┬─────────┘
                                                                                  │
                                                                     ┌────────────┴────────────┐
                                                                     ▼                         ▼
                                                           ┌──────────────────┐     ┌──────────────────┐
                                                           │  Redis Sorted    │     │  Batch Inserter  │
                                                           │  Sets (ZADD)     │     │  (50 txns / 5s)  │
                                                           │  5-min sliding   │     └────────┬─────────┘
                                                           │  window / user   │              │
                                                           └────────┬─────────┘              ▼
                                                                    │              ┌──────────────────┐
                                                                    └─────────────▶│   PostgreSQL     │
                                                                      anomaly      │   txns + flags   │
                                                                      flags        └──────────────────┘
```

**Data flow:** The Producer pushes JSON transactions into a Redis Stream via `XADD`. The Consumer reads them through a Consumer Group (`XREADGROUP`), enabling horizontal scaling with built-in acknowledgment and at-least-once delivery. Each transaction is evaluated against anomaly rules using a per-user Redis Sorted Set sliding window — zero database queries on the hot path. Processed transactions accumulate in an in-memory buffer that flushes to PostgreSQL in batches.

---

## Anomaly Detection

All detection runs against a **5-minute sliding window** stored as a Redis Sorted Set per user. The window is maintained with a pipelined `ZADD` → `ZREMRANGEBYSCORE` → `ZRANGEBYSCORE` → `EXPIRE` in a **single Redis round-trip**.

| Rule | What It Catches | Logic | Severity |
|------|----------------|-------|----------|
| **Velocity** | Bot-like transaction bursts | > 10 txns from one user in 60s | HIGH |
| **Spike** | Stolen card / account takeover | Amount > 3× the user's 5-min rolling avg | CRITICAL |
| **Rapid-fire** | Script-driven fraud | Two txns from the same user within 1s | MEDIUM |

Rules are isolated async functions — adding a new rule means writing one function and appending it to a single `asyncio.gather()` call.

---

## Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Language | Python 3.11+ / asyncio | Async-native, clean concurrency model |
| Message bus | Redis Streams | Consumer groups + ack without Kafka's operational weight |
| Hot state | Redis Sorted Sets | O(log N) sliding window queries, sub-ms latency |
| Persistence | PostgreSQL 16 | ACID audit trail and historical analysis |
| Validation | Pydantic v2 | Type-safe data contracts across all components |
| Batch writes | In-memory buffer | Dual-trigger flush (size OR timer) — same pattern as Kinesis Firehose |
| CI | GitHub Actions | Lint (Ruff) + tests on every push |
| Infra | Docker Compose | One command: `docker compose up` |

---

## Quick Start

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/sentinel-fi.git
cd sentinel-fi

# Start Redis + Postgres
docker compose up -d

# Install the project (editable mode + dev tools)
pip install -e ".[dev]"

# Terminal 1 — Consumer
python -m src.consumer

# Terminal 2 — Producer
python -m src.producer --tps 75
```

You should see anomaly flags within seconds:

```
🚨 [CRITICAL] SPIKE: user=user_0023 $1847.32 — $1847.32 is 14.2x the 5-min avg of $130.09 (threshold: 3.0x)
🚨 [HIGH] VELOCITY: user=user_0012 — 12 transactions in last 60s (threshold: 10)
```

### Scaling

```bash
# Multiple async workers in one process
python -m src.consumer --workers 3

# Or separate processes for true parallelism
python -m src.consumer --worker-id w1 &
python -m src.consumer --worker-id w2 &
```

---

## Project Structure

```
sentinel-fi/
├── .github/
│   └── workflows/
│       └── ci.yml              # GitHub Actions: lint + test on every push
├── data/                       # Mock data samples, seed files
├── docker/
│   └── init.sql                # PostgreSQL schema (auto-runs on first compose up)
├── src/
│   ├── __init__.py
│   ├── config.py               # All tunable parameters (env vars with defaults)
│   ├── models.py               # Pydantic v2 models (Transaction, Anomaly, Severity)
│   ├── database.py             # Redis + PostgreSQL connection management
│   ├── producer.py             # Async mock transaction generator → Redis Stream
│   ├── consumer.py             # Stream reader + worker orchestration
│   ├── anomaly_rules.py        # Pluggable detection (velocity, spike, rapid-fire)
│   └── batch_inserter.py       # In-memory buffer with dual-trigger flush
├── tests/
│   └── test_anomaly_rules.py   # Unit + integration test patterns
├── .env.example                # Environment template (copy to .env)
├── .gitignore
├── docker-compose.yml          # One command: redis + postgres + schema
├── pyproject.toml              # Modern Python project config
└── README.md
```

---

## Design Decisions

**Why Redis Sorted Sets instead of application-level state?**
Sorted Sets give you O(log N) range queries scored by timestamp — purpose-built for sliding windows. Keeping state in Redis means multiple consumer workers share the same window without coordination, and windows survive worker restarts.

**Why pipeline all sorted set operations?**
Each transaction requires `ZADD` + `ZREMRANGEBYSCORE` + `ZRANGEBYSCORE` + `EXPIRE`. Without pipelining: 4 round-trips × 75 TPS = 300 Redis calls/sec. With `redis.pipeline()`: 75 calls/sec — a 4× reduction from one line of code.

**Why batch inserts instead of per-transaction writes?**
At 75 TPS, individual INSERTs create 75 round-trips/sec with TCP overhead, pool pressure, and WAL fsync cost. The `BatchInserter` accumulates 50 transactions and flushes in a single `executemany()`, cutting write amplification by ~50×. The dual trigger (size OR timer) ensures throughput under load and freshness under low traffic.

**Why Redis Streams over Kafka?**
At < 1,000 TPS, Redis Streams provide the same consumer-group semantics — load balancing, acknowledgment, pending entry lists — without a JVM, ZooKeeper/KRaft, or multi-broker setup. A deliberate scope decision, not a limitation.

---

## Roadmap

- [ ] Core engine: producer, consumer, anomaly rules, batch inserter
- [ ] Pydantic models for type-safe data contracts
- [ ] Docker Compose for one-command infrastructure
- [ ] GitHub Actions CI pipeline
- [ ] Prometheus metrics + Grafana dashboard
- [ ] Real PostgreSQL integration via `asyncpg`
- [ ] FastAPI endpoints: `/health`, `/metrics`, `/anomalies`
- [ ] Additional rules: geo-impossible travel, merchant mismatch
- [ ] Load testing: benchmark at 500+ TPS

---

## License

MIT