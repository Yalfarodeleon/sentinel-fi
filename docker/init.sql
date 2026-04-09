-- Sentinel-FI: Initial schema
-- Runs automatically via docker-entrypoint-initdb.d on first `docker compose up`

CREATE TABLE IF NOT EXISTS transactions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    amount          NUMERIC(12, 2) NOT NULL,
    merchant        TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Index for user lookups and time-range queries
    CONSTRAINT positive_amount CHECK (amount > 0)
);

CREATE INDEX idx_txn_user_id ON transactions (user_id);
CREATE INDEX idx_txn_created_at ON transactions (created_at);

CREATE TABLE IF NOT EXISTS anomalies (
    id              SERIAL PRIMARY KEY,
    transaction_id  TEXT NOT NULL REFERENCES transactions(id),
    user_id         TEXT NOT NULL,
    rule            TEXT NOT NULL,
    severity        TEXT NOT NULL,
    detail          TEXT,
    amount          NUMERIC(12, 2) NOT NULL,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_anomaly_user_id ON anomalies (user_id);
CREATE INDEX idx_anomaly_severity ON anomalies (severity);
CREATE INDEX idx_anomaly_rule ON anomalies (rule);