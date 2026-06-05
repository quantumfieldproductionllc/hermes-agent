import pytest

from agent.experience_memory.models import ExperienceScope
from agent.experience_memory.privacy import (
    hash_gateway_identifier,
    normalize_scope,
    redact_payload,
    redact_text,
    scope_sql_params,
    scope_sql_predicate,
    validate_scope,
)


def test_scope_validation_accepts_profile_scope():
    validate_scope(ExperienceScope(profile_id="default"))


def test_scope_validation_rejects_missing_gateway_hashes():
    with pytest.raises(ValueError, match="chat scope"):
        validate_scope(ExperienceScope(profile_id="p", scope_level="chat"))

    with pytest.raises(ValueError, match="thread scope"):
        validate_scope(
            ExperienceScope(
                profile_id="p",
                scope_level="thread",
                user_scope_hash="u",
                chat_scope_hash="c",
            )
        )

    with pytest.raises(ValueError, match="session scope"):
        validate_scope(ExperienceScope(profile_id="p", scope_level="session"))


def test_gateway_hashes_are_stable_and_salt_local():
    assert hash_gateway_identifier("user-1", "salt-a") == hash_gateway_identifier("user-1", "salt-a")
    assert hash_gateway_identifier("user-1", "salt-a") != hash_gateway_identifier("user-1", "salt-b")


def test_normalize_scope_uses_narrow_gateway_scope():
    thread_scope = normalize_scope(
        profile_id="p",
        platform="telegram",
        session_id="s",
        user_id="u",
        chat_id="c",
        thread_id="t",
        salt="salt",
    )
    assert thread_scope.scope_level == "thread"
    assert thread_scope.user_scope_hash
    assert thread_scope.chat_scope_hash
    assert thread_scope.thread_scope_hash

    chat_scope = normalize_scope(
        profile_id="p",
        platform="telegram",
        session_id="s",
        user_id="u",
        chat_id="c",
        salt="salt",
    )
    assert chat_scope.scope_level == "chat"


def test_scope_sql_predicate_anchors_workspace_and_platform_for_all_branches():
    scope = ExperienceScope(
        profile_id="p",
        workspace_id="ws-a",
        platform="telegram",
        scope_level="chat",
        user_scope_hash="u",
        chat_scope_hash="c",
        session_id="s",
    )
    predicate = scope_sql_predicate(scope, alias="r")
    assert "r.profile_id = ? AND r.workspace_id = ? AND r.platform = ?" in predicate
    assert scope_sql_params(scope)[:3] == ["p", "ws-a", "telegram"]


def test_redaction_covers_common_secret_shapes():
    api_key = "sk-" + "1234567890abcdefghijklmnopqrstuvwxyz"
    bearer = "Bearer " + "abcdefghijklmnopqrstuvwxyz"
    private_key = "\n".join(
        [
            "-----BEGIN " + "PRIVATE KEY-----",
            "secret material",
            "-----END " + "PRIVATE KEY-----",
        ]
    )
    text = "\n".join(
        [
            f"OPENAI_API_KEY={api_key}",
            f"Authorization: {bearer}",
            "password = hunter2",
            private_key,
        ]
    )
    redacted = redact_text(text)
    assert "sk-123456" not in redacted
    assert "abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "hunter2" not in redacted
    assert "secret material" not in redacted

    payload = redact_payload({"api_key": "abc123", "safe": ["Bearer " + "abcdefghijkl"]})
    assert payload["api_key"] == "[REDACTED]"
    assert "abcdefghijkl" not in payload["safe"][0]


def test_redaction_covers_natural_language_credential_phrases():
    text = (
        "credentials: hunter2 and credentials = swordfish and "
        "my login credentials are hunter3 and "
        "the API key for staging is abcdef1234567890abcdef"
    )

    redacted = redact_text(text)

    for raw in ("hunter2", "swordfish", "hunter3", "abcdef1234567890abcdef"):
        assert raw not in redacted
    assert redacted.count("[REDACTED]") == 4

    payload = redact_payload({"credentials": "hunter2", "safe": "ok"})
    assert payload["credentials"] == "[REDACTED]"
    assert payload["safe"] == "ok"
