PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS agents (
    agent_id     TEXT PRIMARY KEY,
    role         TEXT NOT NULL CHECK(role IN ('orchestrator', 'executor')),
    kid          TEXT UNIQUE NOT NULL,
    public_jwk   TEXT NOT NULL,
    alg          TEXT NOT NULL DEFAULT 'RS256',
    status       TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'revoked', 'suspended')),
    display_name TEXT,
    contact      TEXT,
    registered_at TEXT NOT NULL,
    registered_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_agents_kid ON agents(kid);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);

CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    password_hash TEXT,
    permissions   TEXT NOT NULL DEFAULT '[]',
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jwks_rotation (
    kid        TEXT PRIMARY KEY,
    status     TEXT NOT NULL CHECK(status IN ('active', 'previous', 'archived')),
    public_jwk TEXT NOT NULL,
    created_at TEXT NOT NULL,
    retired_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jwks_status ON jwks_rotation(status);

CREATE TABLE IF NOT EXISTS audit (
    event_id     TEXT PRIMARY KEY,
    event_type   TEXT NOT NULL,
    trace_id     TEXT,
    plan_id      TEXT,
    task_id      TEXT,
    sub          TEXT,
    act          TEXT,
    aud          TEXT,
    decision     TEXT,
    deny_reasons TEXT,
    payload      TEXT NOT NULL,
    ts           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_trace ON audit(trace_id);
CREATE INDEX IF NOT EXISTS idx_audit_plan  ON audit(plan_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts    ON audit(ts);
CREATE INDEX IF NOT EXISTS idx_audit_sub   ON audit(sub);
CREATE INDEX IF NOT EXISTS idx_audit_type  ON audit(event_type);
