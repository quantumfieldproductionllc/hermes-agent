"""SQLite migrations for the Experience Memory Engine MVP."""

from __future__ import annotations
import hashlib
import hmac
import re
import secrets
import sqlite3

from agent.experience_memory.models import utc_now
from agent.experience_memory.schema import SCHEMA_SQL, SCHEMA_VERSION

_SAFE_IDEMPOTENCY_RE = re.compile(r"^idem_[0-9a-f]{64}(?:_\d+)?$")


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply idempotent MVP schema migrations atomically."""
    script = f"""
    BEGIN IMMEDIATE;
    -- v2 scopes event idempotency to the complete memory identity. Drop the
    -- v1 profile-wide index before replaying declarative schema so existing
    -- MVP databases do not keep the overly broad uniqueness rule.
    DROP INDEX IF EXISTS idx_experience_events_idempotency;
    {SCHEMA_SQL}
    """
    try:
        conn.executescript(script)
        _migrate_event_idempotency_keys(conn)
        conn.execute(
            """
            INSERT INTO experience_schema_version (id, version)
            VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET version = excluded.version
            """,
            (int(SCHEMA_VERSION),),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def _safe_idempotency_key(profile_id: str, salt: str, raw: str) -> str:
    key_salt = str(salt or profile_id or "default")
    digest = hmac.new(
        key_salt.encode("utf-8"),
        str(raw).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"idem_{digest}"


def _get_or_create_profile_salt(conn: sqlite3.Connection) -> str:
    salt_row = conn.execute(
        "SELECT value FROM experience_meta WHERE key = 'profile_salt'"
    ).fetchone()
    if salt_row and str(salt_row[0]):
        return str(salt_row[0])
    salt = secrets.token_hex(32)
    conn.execute(
        """
        INSERT INTO experience_meta (key, value, updated_at)
        VALUES ('profile_salt', ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (salt, utc_now()),
    )
    return salt


def _migrate_event_idempotency_keys(conn: sqlite3.Connection) -> None:
    profile_salt = _get_or_create_profile_salt(conn)
    rows = conn.execute(
        """
        SELECT
            rowid, profile_id, workspace_id, session_id, parent_session_id,
            platform, scope_level, user_scope_hash, chat_scope_hash,
            thread_scope_hash, gateway_session_hash, idempotency_key
        FROM experience_events
        WHERE idempotency_key != ''
        """
    ).fetchall()
    for row in rows:
        raw_key = str(row[11])
        if _SAFE_IDEMPOTENCY_RE.match(raw_key):
            continue
        safe_key = _safe_idempotency_key(row[1], profile_salt, raw_key)
        try:
            conn.execute(
                "UPDATE experience_events SET idempotency_key = ? WHERE rowid = ?",
                (safe_key, row[0]),
            )
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE experience_events SET idempotency_key = ? WHERE rowid = ?",
                (f"{safe_key}_{row[0]}", row[0]),
            )


def current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT version FROM experience_schema_version WHERE id = 1"
    ).fetchone()
    return int(row[0]) if row else 0
