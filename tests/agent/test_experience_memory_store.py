import json

import pytest

from agent.experience_memory import migrations
from agent.experience_memory.models import (
    ExperienceCorrection,
    ExperienceEvent,
    ExperienceQuery,
    ExperienceRecord,
    ExperienceScope,
)
from agent.experience_memory.privacy import normalize_scope
from agent.experience_memory.store import ExperienceStore


def _fake_openai_key(suffix="abcdefghijklmnopqrstuvwxyz1234567890"):
    return "sk-" + "proj-" + suffix


def _fake_github_pat(suffix="abcdefghijklmnopqrstuvwxyz1234567890"):
    return "github" + "_pat_" + suffix


def _store(tmp_path):
    store = ExperienceStore(db_path=tmp_path / "experience.db")
    store.open()
    return store


def _profile_scope(profile="p", session="s"):
    return ExperienceScope(profile_id=profile, session_id=session)


def _chat_scope(profile="p", user="u1", chat="c1", session="s", workspace="hermes", platform="telegram"):
    return normalize_scope(
        profile_id=profile,
        workspace_id=workspace,
        platform=platform,
        session_id=session,
        user_id=user,
        chat_id=chat,
        salt="salt",
    )


def _record(scope, title="SQLite lesson", body="Use SQLite FTS for local recall."):
    return ExperienceRecord(kind="case", scope=scope, title=title, body=body, tags=["sqlite"])


def test_status_reports_queue_placeholders(tmp_path):
    store = _store(tmp_path)
    try:
        status = store.status()
        assert status["queue_counts"] == {"extraction_jobs": {}, "projections": {}}
    finally:
        store.close()


def test_append_event_and_record_write_fts(tmp_path):
    store = _store(tmp_path)
    scope = _profile_scope()
    try:
        event_id = store.append_event(
            ExperienceEvent(
                event_type="manual_record",
                scope=scope,
                payload={"body": "token=secret-value"},
                idempotency_key="idem-1",
            )
        )
        assert store.append_event(
            ExperienceEvent(event_type="manual_record", scope=scope, idempotency_key="idem-1")
        ) == event_id

        record_id = store.record(_record(scope))
        assert store.record(_record(scope)) == record_id

        row = store.conn.execute(
            "SELECT payload_json FROM experience_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        assert "secret-value" not in row["payload_json"]

        assert store.conn.execute(
            "SELECT COUNT(*) FROM experience_records"
        ).fetchone()[0] == 1
        assert store.conn.execute(
            "SELECT COUNT(*) FROM experience_fts WHERE record_id = ?",
            (record_id,),
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_recall_filters_by_scope_before_ranking(tmp_path):
    store = _store(tmp_path)
    scope = _chat_scope(user="u1", chat="c1")
    other_user_scope = _chat_scope(user="u2", chat="c1")
    other_profile_scope = _chat_scope(profile="other", user="u1", chat="c1")
    try:
        visible_id = store.record(_record(scope, title="Visible SQLite", body="durable sqlite record"))
        store.record(_record(other_user_scope, title="Other user SQLite", body="durable sqlite record"))
        store.record(_record(other_profile_scope, title="Other profile SQLite", body="durable sqlite record"))

        results = store.recall(ExperienceQuery(query="sqlite", scope=scope, limit=10))
        assert [r.record_id for r in results] == [visible_id]
    finally:
        store.close()


def test_recall_excludes_retracted_and_tombstoned(tmp_path):
    store = _store(tmp_path)
    scope = _profile_scope()
    try:
        keep_id = store.record(_record(scope, title="Keep FTS", body="needle active"))
        retract_id = store.record(_record(scope, title="Retract FTS", body="needle retracted"))
        tombstone_id = store.record(_record(scope, title="Tombstone FTS", body="needle tombstoned"))

        assert store.correct(
            ExperienceCorrection(
                record_id=retract_id,
                operation="retract",
                reason="bad",
                scope=scope,
            )
        )["ok"]
        assert store.correct(
            ExperienceCorrection(
                record_id=tombstone_id,
                operation="tombstone",
                reason="bad",
                scope=scope,
            )
        )["ok"]

        results = store.recall(ExperienceQuery(query="needle", scope=scope, limit=10))
        assert [r.record_id for r in results] == [keep_id]
    finally:
        store.close()


def test_supersede_creates_replacement_and_audit_row(tmp_path):
    store = _store(tmp_path)
    scope = _profile_scope()
    try:
        old_id = store.record(_record(scope, title="Old rule", body="use old approach"))
        result = store.correct(
            ExperienceCorrection(
                record_id=old_id,
                operation="supersede",
                reason="new evidence",
                replacement_title="New rule",
                replacement_body="use new approach",
                scope=scope,
            )
        )
        assert result["ok"] is True
        assert result["new_record_id"]

        old = store.conn.execute(
            "SELECT status, superseded_by FROM experience_records WHERE record_id = ?",
            (old_id,),
        ).fetchone()
        assert old["status"] == "superseded"
        assert old["superseded_by"] == result["new_record_id"]
        assert store.conn.execute(
            "SELECT COUNT(*) FROM experience_record_corrections"
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_record_redacts_common_raw_api_keys_from_canonical_tables(tmp_path):
    store = _store(tmp_path)
    scope = _profile_scope()
    raw_openai = _fake_openai_key()
    raw_github = _fake_github_pat()
    try:
        event = ExperienceEvent(
            event_type="manual_record",
            scope=scope,
            payload={"body": raw_openai, "evidence": {"token": raw_github}},
            idempotency_key=raw_openai,
        )
        event_id, record_id = store.record_with_event(
            event,
            ExperienceRecord(
                kind="case",
                scope=scope,
                title=f"secret {raw_openai}",
                body=f"body {raw_github}",
                evidence={"openai": raw_openai, "github": raw_github},
                tags=[raw_openai],
            ),
        )
        haystack = []
        haystack.extend(
            str(value)
            for value in store.conn.execute(
                "SELECT title, body, evidence_json, tags_json FROM experience_records WHERE record_id = ?",
                (record_id,),
            ).fetchone()
        )
        haystack.append(
            store.conn.execute("SELECT payload_json FROM experience_events").fetchone()[0]
        )
        idempotency_value = store.conn.execute(
            "SELECT idempotency_key FROM experience_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()[0]
        haystack.append(idempotency_value)
        assert idempotency_value.startswith("idem_")
        assert idempotency_value != raw_openai
        second_event_id, second_record_id = store.record_with_event(
            ExperienceEvent(
                event_type="manual_record",
                scope=scope,
                payload={"body": "duplicate"},
                idempotency_key=raw_openai,
            ),
            ExperienceRecord(
                kind="case",
                scope=scope,
                title="different title",
                body="different body",
            ),
        )
        assert second_event_id == event_id
        assert second_record_id == record_id
        haystack.extend(
            str(value)
            for value in store.conn.execute(
                "SELECT title, body, tags FROM experience_fts WHERE record_id = ?",
                (record_id,),
            ).fetchone()
        )
        joined = "\n".join(haystack)
        assert raw_openai not in joined
        assert raw_github not in joined
        assert "[REDACTED" in joined
    finally:
        store.close()


def test_corrections_append_manual_correction_events_and_skip_not_found(tmp_path):
    store = _store(tmp_path)
    scope = _profile_scope()
    try:
        for operation in ("retract", "tombstone", "supersede"):
            record_id = store.record(_record(scope, title=f"{operation} title", body=f"{operation} body"))
            correction = ExperienceCorrection(
                record_id=record_id,
                operation=operation,
                reason="reason with " + _fake_openai_key("wxyzabcdefghijklmnopqrstuvwxyz"),
                scope=scope,
            )
            if operation == "supersede":
                correction.replacement_title = "Replacement title"
                correction.replacement_body = "Replacement body"
            result = store.correct(correction)
            assert result["ok"] is True

        scrub_id = store.record(_record(scope, title="Scrub event", body="scrub body"))
        assert store.scrub_record(
            scrub_id,
            "privacy " + _fake_openai_key("wxyzabcdefghijklmnopqrstuvwxyz"),
            scope,
        )["ok"] is True

        event_rows = store.conn.execute(
            "SELECT event_type, payload_json FROM experience_events WHERE event_type = 'manual_correction'"
        ).fetchall()
        assert len(event_rows) == 4
        assert store.conn.execute(
            "SELECT COUNT(*) FROM experience_record_corrections"
        ).fetchone()[0] == 4
        payloads = "\n".join(row["payload_json"] for row in event_rows)
        assert _fake_openai_key("wxyzabcdefghijklmnopqrstuvwxyz") not in payloads
        assert store.correct(
            ExperienceCorrection(
                record_id="missing",
                operation="retract",
                reason="not visible",
                scope=scope,
            )
        ) == {"ok": False, "error": "not_found"}
        assert store.conn.execute(
            "SELECT COUNT(*) FROM experience_events WHERE event_type = 'manual_correction'"
        ).fetchone()[0] == 4
    finally:
        store.close()


@pytest.mark.parametrize("operation", ["retract", "tombstone", "supersede", "scrub"])
def test_correction_event_insert_failure_rolls_back_record_update(tmp_path, monkeypatch, operation):
    store = _store(tmp_path)
    scope = _profile_scope()
    try:
        record_id = store.record(_record(scope, title=f"rollback {operation}", body="rollback body"))
        original_insert_event = store._insert_event_locked

        def fail_manual_correction(event, payload):
            if event.event_type == "manual_correction":
                raise RuntimeError("event insert failed")
            return original_insert_event(event, payload)

        monkeypatch.setattr(store, "_insert_event_locked", fail_manual_correction)
        if operation == "scrub":
            with pytest.raises(RuntimeError, match="event insert failed"):
                store.scrub_record(record_id, "privacy", scope)
        else:
            correction = ExperienceCorrection(
                record_id=record_id,
                operation=operation,
                reason="bad",
                scope=scope,
            )
            if operation == "supersede":
                correction.replacement_title = "replacement"
                correction.replacement_body = "replacement body"
            with pytest.raises(RuntimeError, match="event insert failed"):
                store.correct(correction)

        row = store.conn.execute(
            "SELECT status, deleted_at, title, body FROM experience_records WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        assert row["status"] == "active"
        assert row["deleted_at"] is None
        assert row["title"] != "[scrubbed]"
        assert row["body"] != "[scrubbed]"
        assert store.conn.execute(
            "SELECT COUNT(*) FROM experience_record_corrections"
        ).fetchone()[0] == 0
        assert store.conn.execute(
            "SELECT COUNT(*) FROM experience_events WHERE event_type = 'manual_correction'"
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_idempotency_key_is_scoped_to_full_memory_identity(tmp_path):
    store = _store(tmp_path)
    scope_a = _chat_scope(workspace="ws-a")
    scope_b = _chat_scope(workspace="ws-b")
    try:
        event_a = ExperienceEvent(
            event_type="manual_record",
            scope=scope_a,
            payload={"title": "Scoped idem A"},
            idempotency_key="same-key",
        )
        event_b = ExperienceEvent(
            event_type="manual_record",
            scope=scope_b,
            payload={"title": "Scoped idem B"},
            idempotency_key="same-key",
        )
        event_id_a, record_id_a = store.record_with_event(
            event_a,
            _record(scope_a, title="Scoped idem A", body="workspace a"),
        )
        event_id_b, record_id_b = store.record_with_event(
            event_b,
            _record(scope_b, title="Scoped idem B", body="workspace b"),
        )
        assert event_id_b != event_id_a
        assert record_id_b != record_id_a
        assert store.conn.execute("SELECT COUNT(*) FROM experience_events").fetchone()[0] == 2
    finally:
        store.close()


@pytest.mark.parametrize(
    ("raw_key", "preexisting_salt"),
    [
        (_fake_openai_key("legacyabcdefghijklmnopqrstuvwxyz1234567890"), "legacy-salt"),
        ("idem_" + _fake_openai_key("legacyabcdefghijklmnopqrstuvwxyz1234567890"), ""),
    ],
)
def test_migration_hashes_legacy_raw_idempotency_key_and_preserves_dedupe(
    tmp_path,
    raw_key,
    preexisting_salt,
):
    store = _store(tmp_path)
    scope = _profile_scope()
    try:
        if preexisting_salt:
            store.set_meta("profile_salt", preexisting_salt)
        else:
            store.conn.execute("DELETE FROM experience_meta WHERE key = 'profile_salt'")
        event_id, record_id = store.record_with_event(
            ExperienceEvent(
                event_type="manual_record",
                scope=scope,
                payload={"body": "legacy"},
                idempotency_key=raw_key,
            ),
            _record(scope, title="Legacy idem", body="legacy body"),
        )
        store.conn.execute(
            "UPDATE experience_events SET idempotency_key = ? WHERE event_id = ?",
            (raw_key, event_id),
        )
        if not preexisting_salt:
            store.conn.execute("DELETE FROM experience_meta WHERE key = 'profile_salt'")
        migrations.apply_migrations(store.conn)
        stored_key = store.conn.execute(
            "SELECT idempotency_key FROM experience_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()[0]
        assert stored_key.startswith("idem_")
        assert stored_key != raw_key
        assert raw_key not in "\n".join(
            str(value)
            for row in store.conn.execute(
                "SELECT idempotency_key, payload_json FROM experience_events"
            ).fetchall()
            for value in row
        )
        second_event_id, second_record_id = store.record_with_event(
            ExperienceEvent(
                event_type="manual_record",
                scope=scope,
                payload={"body": "duplicate"},
                idempotency_key=raw_key,
            ),
            _record(scope, title="Different legacy idem", body="different body"),
        )
        assert second_event_id == event_id
        assert second_record_id == record_id
    finally:
        store.close()


def test_inactive_records_cannot_be_corrected_or_resurrected(tmp_path):
    store = _store(tmp_path)
    scope = _profile_scope()
    try:
        for operation in ("retract", "tombstone", "scrub"):
            record_id = store.record(
                ExperienceRecord(
                    kind="case",
                    scope=scope,
                    title=f"inactive {operation}",
                    body=f"inactive {operation} body secret-token",
                    evidence={"secret": f"evidence-{operation}"},
                    tags=[f"tag-{operation}"],
                )
            )
            if operation == "scrub":
                assert store.scrub_record(record_id, "privacy", scope)["ok"] is True
            else:
                assert store.correct(
                    ExperienceCorrection(
                        record_id=record_id,
                        operation=operation,
                        reason="bad",
                        scope=scope,
                    )
                )["ok"] is True

            assert store.correct(
                ExperienceCorrection(
                    record_id=record_id,
                    operation="supersede",
                    reason="must not resurrect",
                    replacement_title="resurrected",
                    replacement_body="resurrected secret-token",
                    scope=scope,
                )
            ) == {"ok": False, "error": "not_found"}
            assert store.correct(
                ExperienceCorrection(
                    record_id=record_id,
                    operation="retract",
                    reason="already inactive",
                    scope=scope,
                )
            ) == {"ok": False, "error": "not_found"}

        assert store.recall(ExperienceQuery(query="resurrected", scope=scope, limit=10)) == []
        assert store.conn.execute(
            "SELECT COUNT(*) FROM experience_records WHERE title = 'resurrected'"
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_cross_workspace_recall_and_correction_are_blocked(tmp_path):
    store = _store(tmp_path)
    scope_a = _chat_scope(workspace="ws-a")
    scope_b = _chat_scope(workspace="ws-b")
    try:
        record_id = store.record(_record(scope_a, title="Workspace leak sentinel", body="workspace-only-token"))
        assert store.recall(ExperienceQuery(query="workspace-only-token", scope=scope_b, limit=5)) == []
        assert store.scrub_record(record_id, "wrong workspace", scope_b) == {
            "ok": False,
            "error": "not_found",
        }
        assert store.correct(
            ExperienceCorrection(
                record_id=record_id,
                operation="retract",
                reason="wrong workspace",
                scope=scope_b,
            )
        ) == {"ok": False, "error": "not_found"}
    finally:
        store.close()


def test_scrub_redacts_canonical_content_and_deletes_fts(tmp_path):
    store = _store(tmp_path)
    scope = _profile_scope()
    try:
        event = ExperienceEvent(
            event_type="manual_record",
            scope=scope,
            payload={"title": "Sensitive title", "body": "body contains UNIQUESECRET"},
        )
        _, record_id = store.record_with_event(
            event,
            _record(scope, title="Sensitive title", body="body contains UNIQUESECRET"),
        )
        result = store.scrub_record(record_id, "privacy request", scope)
        assert result == {"ok": True, "record_id": record_id, "operation": "scrub"}

        row = store.conn.execute(
            "SELECT title, body, evidence_json, tags_json, status, deleted_at FROM experience_records WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        assert row["title"] == "[scrubbed]"
        assert row["body"] == "[scrubbed]"
        assert json.loads(row["evidence_json"]) == {"scrubbed": True}
        assert json.loads(row["tags_json"]) == []
        assert row["status"] == "scrubbed"
        assert row["deleted_at"]
        assert "UNIQUESECRET" not in "\n".join(str(value) for value in row)
        assert store.conn.execute(
            "SELECT COUNT(*) FROM experience_fts WHERE record_id = ?",
            (record_id,),
        ).fetchone()[0] == 0
        event = store.conn.execute(
            "SELECT payload_json FROM experience_events WHERE event_id = (SELECT source_event_id FROM experience_records WHERE record_id = ?)",
            (record_id,),
        ).fetchone()
        assert "UNIQUESECRET" not in event["payload_json"]
        assert json.loads(event["payload_json"]) == {"record_id": record_id, "scrubbed": True}
        assert store.recall(ExperienceQuery(query="UNIQUESECRET", scope=scope, limit=5)) == []
    finally:
        store.close()
