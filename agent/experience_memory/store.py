"""Canonical SQLite store for the Experience Memory Engine MVP."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from hermes_constants import display_hermes_home, get_hermes_home
from hermes_state import apply_wal_with_fallback

from agent.experience_memory.migrations import apply_migrations, current_schema_version
from agent.experience_memory.models import (
    ALLOWED_CORRECTION_OPERATIONS,
    ALLOWED_KINDS,
    ALLOWED_STATUSES,
    ExperienceCorrection,
    ExperienceEvent,
    ExperienceQuery,
    ExperienceRecord,
    ExperienceScope,
    utc_now,
)
from agent.experience_memory.privacy import (
    content_hash_for_record,
    get_or_create_profile_salt,
    redact_payload,
    redact_text,
    scope_sql_params,
    scope_sql_predicate,
    validate_scope,
)
from agent.experience_memory.retrieval import recall_fts

logger = logging.getLogger(__name__)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ExperienceStore:
    """Profile-local SQLite canonical store."""

    def __init__(self, db_path: str | Path | None = None, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.db_path = Path(db_path) if db_path else get_hermes_home() / "experience" / "experience.db"
        store_cfg = self.config.get("store") or {}
        privacy_cfg = self.config.get("privacy") or {}
        self.busy_timeout_ms = int(store_cfg.get("busy_timeout_ms", 5000))
        self.retry_writes = int(store_cfg.get("retry_writes", 2))
        self.redact_secrets = bool(privacy_cfg.get("redact_secrets", True))
        self._conn: sqlite3.Connection | None = None
        self._journal_mode = ""

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("ExperienceStore is not open")
        return self._conn

    def open(self) -> None:
        if self._conn is not None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        self._journal_mode = apply_wal_with_fallback(conn, db_label="experience.db")
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        conn.execute("PRAGMA foreign_keys=ON")
        self._conn = conn
        self.migrate()

    def migrate(self) -> None:
        apply_migrations(self.conn)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _display_db_path(self) -> str:
        try:
            home = get_hermes_home().resolve()
            rel = self.db_path.resolve().relative_to(home)
            return str(Path(display_hermes_home()) / rel)
        except Exception:
            return self.db_path.name

    def get_meta(self, key: str) -> str:
        row = self.conn.execute(
            "SELECT value FROM experience_meta WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row["value"]) if row else ""

    def set_meta(self, key: str, value: str) -> None:
        now = utc_now()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO experience_meta (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def status(self) -> dict[str, Any]:
        conn = self.conn
        version = current_schema_version(conn)
        total = conn.execute("SELECT COUNT(*) FROM experience_records").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM experience_records WHERE status = 'active' AND deleted_at IS NULL"
        ).fetchone()[0]
        return {
            "ok": True,
            "path": self._display_db_path(),
            "schema_version": version,
            "journal_mode": self._journal_mode,
            "busy_timeout_ms": conn.execute("PRAGMA busy_timeout").fetchone()[0],
            "records_total": int(total),
            "records_active": int(active),
            "queue_counts": self.queue_counts(),
        }

    def append_event(self, event: ExperienceEvent) -> str:
        validate_scope(event.scope)
        event.idempotency_key = self._safe_idempotency_key(
            event.scope,
            event.idempotency_key,
        )
        payload = redact_payload(event.payload) if self.redact_secrets else event.payload

        def _write() -> str:
            if event.idempotency_key:
                existing = self._event_for_idempotency_locked(event.scope, event.idempotency_key)
                if existing:
                    return str(existing["event_id"])
            with self.conn:
                self._insert_event_locked(event, payload)
            return event.event_id

        return self._write_with_retry(_write)

    def record_with_event(self, event: ExperienceEvent, record: ExperienceRecord) -> tuple[str, str]:
        """Atomically append a source event and insert its canonical record."""
        validate_scope(event.scope)
        validate_scope(record.scope)
        if event.scope != record.scope:
            raise ValueError("event and record scopes must match")
        self._validate_record(record)
        event.idempotency_key = self._safe_idempotency_key(
            event.scope,
            event.idempotency_key,
        )
        payload = redact_payload(event.payload) if self.redact_secrets else event.payload

        def _write() -> tuple[str, str]:
            with self.conn:
                event_id = ""
                if event.idempotency_key:
                    existing_event = self._event_for_idempotency_locked(event.scope, event.idempotency_key)
                    if existing_event:
                        event_id = str(existing_event["event_id"])
                        existing_record = self.conn.execute(
                            """
                            SELECT record_id FROM experience_records
                            WHERE source_event_id = ?
                            ORDER BY created_at ASC, record_id ASC
                            LIMIT 1
                            """,
                            (event_id,),
                        ).fetchone()
                        if existing_record:
                            return event_id, str(existing_record["record_id"])

                if not event_id:
                    existing_record_id = self._existing_record_id_locked(record)
                    if existing_record_id:
                        return "", existing_record_id
                    self._insert_event_locked(event, payload)
                    event_id = event.event_id

                record.source_event_id = event_id
                return event_id, self._insert_record_locked(record)

        return self._write_with_retry(_write)

    def record(self, record: ExperienceRecord) -> str:
        validate_scope(record.scope)
        self._validate_record(record)

        def _write() -> str:
            with self.conn:
                return self._insert_record_locked(record)

        return self._write_with_retry(_write)

    def recall(self, query: ExperienceQuery):
        validate_scope(query.scope)
        return recall_fts(self.conn, query)

    def correct(self, correction: ExperienceCorrection) -> dict[str, Any]:
        validate_scope(correction.scope)
        if correction.operation not in ALLOWED_CORRECTION_OPERATIONS - {"scrub"}:
            raise ValueError(f"invalid correction operation: {correction.operation}")
        if not correction.reason:
            raise ValueError("correction reason is required")

        def _write() -> dict[str, Any]:
            with self.conn:
                row = self._visible_record_locked(correction.record_id, correction.scope)
                if row is None:
                    return {"ok": False, "error": "not_found"}

                new_record_id = None
                now = utc_now()
                if correction.operation == "retract":
                    self.conn.execute(
                        """
                        UPDATE experience_records
                        SET status = 'retracted', updated_at = ?
                        WHERE record_id = ?
                        """,
                        (now, correction.record_id),
                    )
                    self._delete_fts_locked(correction.record_id)
                elif correction.operation == "tombstone":
                    self.conn.execute(
                        """
                        UPDATE experience_records
                        SET status = 'tombstoned', updated_at = ?, deleted_at = ?
                        WHERE record_id = ?
                        """,
                        (now, now, correction.record_id),
                    )
                    self._delete_fts_locked(correction.record_id)
                elif correction.operation == "supersede":
                    if not correction.replacement_title or not correction.replacement_body:
                        raise ValueError("supersede requires replacement title and body")
                    tags = self._loads(row["tags_json"], [])
                    evidence = self._loads(row["evidence_json"], {})
                    if isinstance(evidence, dict):
                        evidence = {**evidence, "supersedes": correction.record_id}
                    replacement = ExperienceRecord(
                        kind=row["kind"],
                        scope=correction.scope,
                        title=correction.replacement_title,
                        body=correction.replacement_body,
                        applies_when=self._loads(row["applies_when_json"], {}),
                        does_not_apply_when=self._loads(row["does_not_apply_when_json"], {}),
                        evidence=evidence,
                        tags=tags if isinstance(tags, list) else [],
                        source_session_id=row["source_session_id"] or "",
                        privacy_level=row["privacy_level"] or "profile",
                        confidence=float(row["confidence"]),
                    )
                    new_record_id = self._insert_record_locked(replacement)
                    self.conn.execute(
                        """
                        UPDATE experience_records
                        SET status = 'superseded', superseded_by = ?, updated_at = ?
                        WHERE record_id = ?
                        """,
                        (new_record_id, now, correction.record_id),
                    )
                    self._delete_fts_locked(correction.record_id)

                self._insert_correction_locked(correction, new_record_id)
                self._insert_correction_event_locked(correction, new_record_id)
                return {
                    "ok": True,
                    "record_id": correction.record_id,
                    "operation": correction.operation,
                    "new_record_id": new_record_id,
                }

        return self._write_with_retry(_write)

    def scrub_record(self, record_id: str, reason: str, scope: ExperienceScope) -> dict[str, Any]:
        validate_scope(scope)
        if not reason:
            raise ValueError("scrub reason is required")

        def _write() -> dict[str, Any]:
            with self.conn:
                row = self._visible_record_locked(record_id, scope, include_inactive=True)
                if row is None or row["status"] == "scrubbed":
                    return {"ok": False, "error": "not_found"}
                now = utc_now()
                correction = ExperienceCorrection(
                    record_id=record_id,
                    operation="scrub",
                    reason=redact_text(reason) if self.redact_secrets else reason,
                    scope=scope,
                )
                self._insert_correction_locked(correction, None)
                self.conn.execute(
                    """
                    UPDATE experience_records
                    SET title = '[scrubbed]',
                        body = '[scrubbed]',
                        applies_when_json = '{}',
                        does_not_apply_when_json = '{}',
                        evidence_json = '{"scrubbed":true}',
                        tags_json = '[]',
                        status = 'scrubbed',
                        updated_at = ?,
                        deleted_at = ?
                    WHERE record_id = ?
                    """,
                    (now, now, record_id),
                )
                if row["source_event_id"]:
                    self.conn.execute(
                        """
                        UPDATE experience_events
                        SET payload_json = ?
                        WHERE event_id = ?
                        """,
                        (
                            _json_dumps({"scrubbed": True, "record_id": record_id}),
                            row["source_event_id"],
                        ),
                    )
                self._delete_fts_locked(record_id)
                self._insert_correction_event_locked(correction, None)
                return {"ok": True, "record_id": record_id, "operation": "scrub"}

        return self._write_with_retry(_write)

    def queue_counts(self) -> dict[str, dict[str, int]]:
        return {
            "extraction_jobs": self._status_counts("experience_extraction_jobs"),
            "projections": self._status_counts("experience_projections"),
        }

    def _write_with_retry(self, func: Callable[[], Any]) -> Any:
        attempts = max(0, self.retry_writes) + 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return func()
            except sqlite3.OperationalError as exc:
                last_exc = exc
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                if attempt + 1 >= attempts:
                    raise
                time.sleep(0.05 * (attempt + 1))
        if last_exc:
            raise last_exc
        raise RuntimeError("write retry failed without exception")

    def _validate_record(self, record: ExperienceRecord) -> None:
        if record.kind not in ALLOWED_KINDS:
            raise ValueError(f"invalid record kind: {record.kind}")
        if record.status not in ALLOWED_STATUSES:
            raise ValueError(f"invalid record status: {record.status}")
        if not record.title:
            raise ValueError("record title is required")
        if not record.body:
            raise ValueError("record body is required")

    def _safe_idempotency_key(self, scope: ExperienceScope, idempotency_key: str | None) -> str:
        raw = str(idempotency_key or "").strip()
        if not raw:
            return ""
        salt = get_or_create_profile_salt(self)
        digest = hmac.new(
            str(salt).encode("utf-8"),
            raw.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"idem_{digest}"

    def _event_for_idempotency_locked(self, scope: ExperienceScope, idempotency_key: str):
        return self.conn.execute(
            """
            SELECT event_id FROM experience_events
            WHERE profile_id = ?
              AND workspace_id = ?
              AND session_id = ?
              AND parent_session_id = ?
              AND platform = ?
              AND scope_level = ?
              AND user_scope_hash = ?
              AND chat_scope_hash = ?
              AND thread_scope_hash = ?
              AND gateway_session_hash = ?
              AND idempotency_key = ?
            """,
            (
                scope.profile_id,
                scope.workspace_id,
                scope.session_id,
                scope.parent_session_id,
                scope.platform,
                scope.scope_level,
                scope.user_scope_hash,
                scope.chat_scope_hash,
                scope.thread_scope_hash,
                scope.gateway_session_hash,
                idempotency_key,
            ),
        ).fetchone()

    def _insert_event_locked(self, event: ExperienceEvent, payload: Any) -> None:
        self.conn.execute(
            """
            INSERT INTO experience_events (
                event_id, event_type,
                profile_id, workspace_id, session_id, parent_session_id,
                platform, scope_level, user_scope_hash, chat_scope_hash,
                thread_scope_hash, gateway_session_hash,
                idempotency_key, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.event_type,
                event.scope.profile_id,
                event.scope.workspace_id,
                event.scope.session_id,
                event.scope.parent_session_id,
                event.scope.platform,
                event.scope.scope_level,
                event.scope.user_scope_hash,
                event.scope.chat_scope_hash,
                event.scope.thread_scope_hash,
                event.scope.gateway_session_hash,
                event.idempotency_key,
                _json_dumps(payload),
                event.created_at,
            ),
        )

    def _insert_correction_event_locked(
        self,
        correction: ExperienceCorrection,
        new_record_id: str | None,
    ) -> None:
        payload: dict[str, Any] = {
            "record_id": correction.record_id,
            "operation": correction.operation,
            "reason": correction.reason,
        }
        if new_record_id:
            payload["new_record_id"] = new_record_id
        safe_payload = redact_payload(payload) if self.redact_secrets else payload
        self._insert_event_locked(
            ExperienceEvent(
                event_type="manual_correction",
                scope=correction.scope,
                payload=safe_payload,
            ),
            safe_payload,
        )

    def _prepare_record(self, record: ExperienceRecord) -> dict[str, Any]:
        title = redact_text(record.title) if self.redact_secrets else record.title
        body = redact_text(record.body) if self.redact_secrets else record.body
        applies_when = redact_payload(record.applies_when) if self.redact_secrets else record.applies_when
        does_not_apply = (
            redact_payload(record.does_not_apply_when)
            if self.redact_secrets
            else record.does_not_apply_when
        )
        evidence = redact_payload(record.evidence) if self.redact_secrets else record.evidence
        tags = redact_payload(record.tags) if self.redact_secrets else record.tags
        content_hash = record.content_hash or content_hash_for_record(
            kind=record.kind,
            title=title,
            body=body,
            applies_when=applies_when,
            does_not_apply_when=does_not_apply,
            evidence=evidence,
            tags=tags,
        )
        return {
            "title": title,
            "body": body,
            "applies_when": applies_when,
            "does_not_apply_when": does_not_apply,
            "evidence": evidence,
            "tags": tags,
            "content_hash": content_hash,
        }

    def _existing_record_id_locked(
        self,
        record: ExperienceRecord,
        prepared: dict[str, Any] | None = None,
    ) -> str:
        prepared = prepared or self._prepare_record(record)
        existing = self.conn.execute(
            """
            SELECT record_id FROM experience_records
            WHERE profile_id = ?
              AND workspace_id = ?
              AND session_id = ?
              AND parent_session_id = ?
              AND platform = ?
              AND scope_level = ?
              AND user_scope_hash = ?
              AND chat_scope_hash = ?
              AND thread_scope_hash = ?
              AND gateway_session_hash = ?
              AND kind = ?
              AND content_hash = ?
            """,
            (
                record.scope.profile_id,
                record.scope.workspace_id,
                record.scope.session_id,
                record.scope.parent_session_id,
                record.scope.platform,
                record.scope.scope_level,
                record.scope.user_scope_hash,
                record.scope.chat_scope_hash,
                record.scope.thread_scope_hash,
                record.scope.gateway_session_hash,
                record.kind,
                prepared["content_hash"],
            ),
        ).fetchone()
        return str(existing["record_id"]) if existing else ""

    def _insert_record_locked(self, record: ExperienceRecord) -> str:
        prepared = self._prepare_record(record)
        existing = self.conn.execute(
            """
            SELECT record_id FROM experience_records
            WHERE profile_id = ?
              AND workspace_id = ?
              AND session_id = ?
              AND parent_session_id = ?
              AND platform = ?
              AND scope_level = ?
              AND user_scope_hash = ?
              AND chat_scope_hash = ?
              AND thread_scope_hash = ?
              AND gateway_session_hash = ?
              AND kind = ?
              AND content_hash = ?
            """,
            (
                record.scope.profile_id,
                record.scope.workspace_id,
                record.scope.session_id,
                record.scope.parent_session_id,
                record.scope.platform,
                record.scope.scope_level,
                record.scope.user_scope_hash,
                record.scope.chat_scope_hash,
                record.scope.thread_scope_hash,
                record.scope.gateway_session_hash,
                record.kind,
                prepared["content_hash"],
            ),
        ).fetchone()
        if existing:
            return str(existing["record_id"])

        self.conn.execute(
            """
            INSERT INTO experience_records (
                record_id, kind,
                profile_id, workspace_id, session_id, parent_session_id,
                platform, scope_level, user_scope_hash, chat_scope_hash,
                thread_scope_hash, gateway_session_hash,
                title, body, applies_when_json, does_not_apply_when_json,
                evidence_json, tags_json, source_event_id, source_session_id,
                source_turn_index, content_hash, status, privacy_level,
                confidence, created_at, updated_at, superseded_by, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.record_id,
                record.kind,
                record.scope.profile_id,
                record.scope.workspace_id,
                record.scope.session_id,
                record.scope.parent_session_id,
                record.scope.platform,
                record.scope.scope_level,
                record.scope.user_scope_hash,
                record.scope.chat_scope_hash,
                record.scope.thread_scope_hash,
                record.scope.gateway_session_hash,
                prepared["title"],
                prepared["body"],
                _json_dumps(prepared["applies_when"]),
                _json_dumps(prepared["does_not_apply_when"]),
                _json_dumps(prepared["evidence"]),
                _json_dumps(prepared["tags"]),
                record.source_event_id,
                record.source_session_id or record.scope.session_id,
                record.source_turn_index,
                prepared["content_hash"],
                record.status,
                record.privacy_level,
                max(0.0, min(1.0, float(record.confidence))),
                record.created_at,
                record.updated_at,
                record.superseded_by,
                record.deleted_at,
            ),
        )
        self._insert_fts_locked(record, prepared)
        return record.record_id

    def _insert_fts_locked(self, record: ExperienceRecord, prepared: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO experience_fts (
                record_id, profile_id, workspace_id, session_id, parent_session_id,
                platform, scope_level, user_scope_hash, chat_scope_hash,
                thread_scope_hash, gateway_session_hash, title, body, tags
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.record_id,
                record.scope.profile_id,
                record.scope.workspace_id,
                record.scope.session_id,
                record.scope.parent_session_id,
                record.scope.platform,
                record.scope.scope_level,
                record.scope.user_scope_hash,
                record.scope.chat_scope_hash,
                record.scope.thread_scope_hash,
                record.scope.gateway_session_hash,
                prepared["title"],
                prepared["body"],
                " ".join(str(tag) for tag in prepared["tags"] or []),
            ),
        )

    def _delete_fts_locked(self, record_id: str) -> None:
        self.conn.execute("DELETE FROM experience_fts WHERE record_id = ?", (record_id,))

    def _visible_record_locked(
        self,
        record_id: str,
        scope: ExperienceScope,
        *,
        include_inactive: bool = False,
    ):
        predicate = scope_sql_predicate(scope, alias="r")
        active_predicate = "" if include_inactive else "AND r.deleted_at IS NULL AND r.status = 'active'"
        row = self.conn.execute(
            f"""
            SELECT r.* FROM experience_records r
            WHERE r.record_id = ?
              AND {predicate}
              {active_predicate}
            """,
            [record_id, *scope_sql_params(scope)],
        ).fetchone()
        return row

    def _insert_correction_locked(
        self,
        correction: ExperienceCorrection,
        new_record_id: str | None,
    ) -> None:
        reason = redact_text(correction.reason) if self.redact_secrets else correction.reason
        self.conn.execute(
            """
            INSERT INTO experience_record_corrections (
                correction_id, record_id, new_record_id, operation, reason,
                profile_id, workspace_id, session_id, parent_session_id,
                platform, scope_level, user_scope_hash, chat_scope_hash,
                thread_scope_hash, gateway_session_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                correction.correction_id,
                correction.record_id,
                new_record_id,
                correction.operation,
                reason,
                correction.scope.profile_id,
                correction.scope.workspace_id,
                correction.scope.session_id,
                correction.scope.parent_session_id,
                correction.scope.platform,
                correction.scope.scope_level,
                correction.scope.user_scope_hash,
                correction.scope.chat_scope_hash,
                correction.scope.thread_scope_hash,
                correction.scope.gateway_session_hash,
                correction.created_at,
            ),
        )

    def _status_counts(self, table_name: str) -> dict[str, int]:
        rows = self.conn.execute(
            f"SELECT status, COUNT(*) AS n FROM {table_name} GROUP BY status"
        ).fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}

    @staticmethod
    def _loads(raw: str, default):
        try:
            return json.loads(raw or "")
        except Exception:
            return default
