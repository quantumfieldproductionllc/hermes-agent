"""SQLite schema for the Experience Memory Engine MVP."""

SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS experience_schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS experience_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experience_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'hermes',
    session_id TEXT NOT NULL DEFAULT '',
    parent_session_id TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT 'cli',
    scope_level TEXT NOT NULL DEFAULT 'profile',
    user_scope_hash TEXT NOT NULL DEFAULT '',
    chat_scope_hash TEXT NOT NULL DEFAULT '',
    thread_scope_hash TEXT NOT NULL DEFAULT '',
    gateway_session_hash TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_experience_events_idempotency_scope
ON experience_events(
    profile_id,
    workspace_id,
    session_id,
    parent_session_id,
    platform,
    scope_level,
    user_scope_hash,
    chat_scope_hash,
    thread_scope_hash,
    gateway_session_hash,
    idempotency_key
)
WHERE idempotency_key != '';

CREATE TABLE IF NOT EXISTS experience_records (
    record_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'hermes',
    session_id TEXT NOT NULL DEFAULT '',
    parent_session_id TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT 'cli',
    scope_level TEXT NOT NULL DEFAULT 'profile',
    user_scope_hash TEXT NOT NULL DEFAULT '',
    chat_scope_hash TEXT NOT NULL DEFAULT '',
    thread_scope_hash TEXT NOT NULL DEFAULT '',
    gateway_session_hash TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    applies_when_json TEXT NOT NULL DEFAULT '{}',
    does_not_apply_when_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    tags_json TEXT NOT NULL DEFAULT '[]',
    source_event_id TEXT,
    source_session_id TEXT NOT NULL DEFAULT '',
    source_turn_index INTEGER,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    privacy_level TEXT NOT NULL DEFAULT 'profile',
    confidence REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    superseded_by TEXT,
    deleted_at TEXT,
    FOREIGN KEY(source_event_id) REFERENCES experience_events(event_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_experience_records_content_scope
ON experience_records(
    profile_id,
    workspace_id,
    session_id,
    parent_session_id,
    platform,
    scope_level,
    user_scope_hash,
    chat_scope_hash,
    thread_scope_hash,
    gateway_session_hash,
    kind,
    content_hash
);

CREATE INDEX IF NOT EXISTS idx_experience_records_scope
ON experience_records(
    profile_id,
    workspace_id,
    platform,
    scope_level,
    user_scope_hash,
    chat_scope_hash,
    thread_scope_hash,
    session_id,
    status,
    deleted_at
);

CREATE TABLE IF NOT EXISTS experience_record_corrections (
    correction_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    new_record_id TEXT,
    operation TEXT NOT NULL,
    reason TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'hermes',
    session_id TEXT NOT NULL DEFAULT '',
    parent_session_id TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT 'cli',
    scope_level TEXT NOT NULL DEFAULT 'profile',
    user_scope_hash TEXT NOT NULL DEFAULT '',
    chat_scope_hash TEXT NOT NULL DEFAULT '',
    thread_scope_hash TEXT NOT NULL DEFAULT '',
    gateway_session_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(record_id) REFERENCES experience_records(record_id),
    FOREIGN KEY(new_record_id) REFERENCES experience_records(record_id)
);

CREATE TABLE IF NOT EXISTS experience_extraction_jobs (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'queued',
    payload_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experience_projections (
    projection_id TEXT PRIMARY KEY,
    record_id TEXT,
    target TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    payload_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(record_id) REFERENCES experience_records(record_id)
);

CREATE TABLE IF NOT EXISTS experience_projection_watermarks (
    target TEXT PRIMARY KEY,
    watermark TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS experience_fts USING fts5(
    record_id UNINDEXED,
    profile_id UNINDEXED,
    workspace_id UNINDEXED,
    session_id UNINDEXED,
    parent_session_id UNINDEXED,
    platform UNINDEXED,
    scope_level UNINDEXED,
    user_scope_hash UNINDEXED,
    chat_scope_hash UNINDEXED,
    thread_scope_hash UNINDEXED,
    gateway_session_hash UNINDEXED,
    title,
    body,
    tags,
    tokenize='unicode61'
);
"""
