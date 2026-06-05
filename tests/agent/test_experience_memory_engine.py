import json

import pytest

from agent.experience_memory.engine import ExperienceMemoryEngine
from agent.experience_memory.tool_gating import experience_memory_tools_allowed


@pytest.mark.parametrize(
    ("enabled", "disabled", "expected"),
    [
        (None, [], True),
        ([], [], False),
        (["web"], [], False),
        (["experience_memory"], [], True),
        (["memory"], [], True),
        (["all"], [], True),
        (["*"], [], True),
        (["hermes-cli"], [], True),
        (["experience_memory"], ["experience_memory"], False),
        (["experience_memory"], ["memory"], False),
        (["experience_memory"], ["all"], False),
        (["experience_memory"], ["*"], False),
        (["experience_memory"], ["hermes-cli"], False),
    ],
)
def test_tool_gating(enabled, disabled, expected):
    assert experience_memory_tools_allowed(enabled, disabled) is expected


def _engine(tmp_path, *, platform="cli", user_id=None, chat_id=None, thread_id=None):
    engine = ExperienceMemoryEngine(
        config={
            "enabled": True,
            "tools_enabled": True,
            "max_recall_items": 3,
            "recall_token_budget": 200,
            "privacy": {"redact_secrets": True, "allow_user_model": True},
        },
        db_path=tmp_path / "experience.db",
    )
    engine.initialize(
        "session-1",
        agent_identity="profile-a",
        agent_workspace="hermes",
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        thread_id=thread_id,
        gateway_session_key="gw-key" if platform != "cli" else None,
    )
    return engine


def test_tool_schema_exposes_one_dynamic_tool(tmp_path):
    engine = _engine(tmp_path)
    try:
        schemas = engine.get_tool_schemas()
        assert [schema["name"] for schema in schemas] == ["experience_memory"]
        assert "project" not in schemas[0]["parameters"]["properties"]["action"]["enum"]
    finally:
        engine.shutdown()


def test_status_record_recall_and_retract_return_json(tmp_path):
    engine = _engine(tmp_path)
    try:
        status = json.loads(engine.handle_tool_call("experience_memory", {"action": "status"}))
        assert status["ok"] is True
        assert status["schema_version"] == 2
        assert str(tmp_path) not in status["path"]

        record = json.loads(
            engine.handle_tool_call(
                "experience_memory",
                {
                    "action": "record",
                    "kind": "case",
                    "title": "Engine MVP",
                    "body": "record recall correct works",
                    "tags": ["mvp"],
                },
            )
        )
        assert record["ok"] is True
        record_id = record["record_id"]

        recall = json.loads(
            engine.handle_tool_call(
                "experience_memory",
                {"action": "recall", "query": "correct", "limit": 10},
            )
        )
        assert recall["ok"] is True
        assert [item["record_id"] for item in recall["results"]] == [record_id]

        corrected = json.loads(
            engine.handle_tool_call(
                "experience_memory",
                {
                    "action": "correct",
                    "record_id": record_id,
                    "operation": "retract",
                    "reason": "test cleanup",
                },
            )
        )
        assert corrected["ok"] is True
    finally:
        engine.shutdown()


def test_scrub_does_not_echo_deleted_content(tmp_path):
    engine = _engine(tmp_path)
    try:
        record = json.loads(
            engine.handle_tool_call(
                "experience_memory",
                {
                    "action": "record",
                    "kind": "case",
                    "title": "Secret case",
                    "body": "contains SCRUBME",
                },
            )
        )
        scrubbed = engine.handle_tool_call(
            "experience_memory",
            {
                "action": "correct",
                "record_id": record["record_id"],
                "operation": "scrub",
                "reason": "privacy",
            },
        )
        assert "SCRUBME" not in scrubbed
        data = json.loads(scrubbed)
        assert data["ok"] is True
        assert data["operation"] == "scrub"
    finally:
        engine.shutdown()


def test_gateway_scoped_records_do_not_cross_users(tmp_path):
    engine_a = _engine(tmp_path, platform="telegram", user_id="user-a", chat_id="chat-1")
    try:
        created = json.loads(
            engine_a.handle_tool_call(
                "experience_memory",
                {
                    "action": "record",
                    "kind": "case",
                    "title": "Gateway scoped",
                    "body": "telegram-local-needle",
                },
            )
        )
        assert created["ok"] is True
        assert engine_a.scope.scope_level == "chat"
    finally:
        engine_a.shutdown()

    engine_b = _engine(tmp_path, platform="telegram", user_id="user-b", chat_id="chat-1")
    try:
        recall = json.loads(
            engine_b.handle_tool_call(
                "experience_memory",
                {"action": "recall", "query": "telegram-local-needle"},
            )
        )
        assert recall["ok"] is True
        assert recall["results"] == []
    finally:
        engine_b.shutdown()


def test_idempotency_key_deduplicates_whole_record_operation(tmp_path):
    engine = _engine(tmp_path)
    try:
        first = json.loads(
            engine.handle_tool_call(
                "experience_memory",
                {
                    "action": "record",
                    "kind": "case",
                    "title": "Idempotent first",
                    "body": "first body",
                    "idempotency_key": "idem-record-1",
                },
            )
        )
        second = json.loads(
            engine.handle_tool_call(
                "experience_memory",
                {
                    "action": "record",
                    "kind": "case",
                    "title": "Idempotent second",
                    "body": "second body",
                    "idempotency_key": "idem-record-1",
                },
            )
        )
        assert second["record_id"] == first["record_id"]
        assert second["event_id"] == first["event_id"]
        assert engine.store.conn.execute("SELECT COUNT(*) FROM experience_records").fetchone()[0] == 1
        payload = engine.store.conn.execute("SELECT payload_json FROM experience_events").fetchone()[0]
        assert "Idempotent second" not in payload
    finally:
        engine.shutdown()


def test_recall_surfaces_database_failures_as_tool_error(tmp_path):
    engine = _engine(tmp_path)
    try:
        record = json.loads(
            engine.handle_tool_call(
                "experience_memory",
                {
                    "action": "record",
                    "kind": "case",
                    "title": "Broken recall",
                    "body": "db-failure-token",
                },
            )
        )
        assert record["ok"] is True
        engine.store.conn.execute("DROP TABLE experience_fts")
        recall = json.loads(
            engine.handle_tool_call(
                "experience_memory",
                {"action": "recall", "query": "db-failure-token", "limit": 3},
            )
        )
        assert recall["ok"] is False
        assert recall.get("results", []) == []
        assert "experience_fts" in recall["error"]
    finally:
        engine.shutdown()


def test_validation_errors_are_json(tmp_path):
    engine = _engine(tmp_path)
    try:
        result = json.loads(
            engine.handle_tool_call("experience_memory", {"action": "record", "kind": "case"})
        )
        assert result["ok"] is False
        assert "title and body" in result["error"]
    finally:
        engine.shutdown()
