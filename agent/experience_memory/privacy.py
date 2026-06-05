"""Privacy and scope helpers for the Experience Memory Engine MVP."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from typing import Any

from agent.experience_memory.models import ExperienceScope, utc_now


VALID_SCOPE_LEVELS = {
    "profile",
    "workspace",
    "platform_user",
    "chat",
    "thread",
    "session",
}


def hash_gateway_identifier(value: str | None, salt: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return hashlib.sha256(f"{salt}:{raw}".encode("utf-8")).hexdigest()


def get_or_create_profile_salt(store) -> str:
    salt = store.get_meta("profile_salt")
    if salt:
        return salt
    salt = secrets.token_hex(32)
    store.set_meta("profile_salt", salt)
    return salt


def normalize_scope(
    *,
    profile_id: str | None = None,
    agent_identity: str | None = None,
    workspace_id: str | None = None,
    agent_workspace: str | None = None,
    platform: str | None = None,
    session_id: str | None = None,
    parent_session_id: str | None = None,
    user_id: str | None = None,
    user_id_alt: str | None = None,
    user_name: str | None = None,
    chat_id: str | None = None,
    thread_id: str | None = None,
    gateway_session_key: str | None = None,
    salt: str = "",
    scope_level: str | None = None,
) -> ExperienceScope:
    profile = str(profile_id or agent_identity or "default").strip() or "default"
    workspace = str(workspace_id or agent_workspace or "hermes").strip() or "hermes"
    platform_value = str(platform or "cli").strip() or "cli"
    session = str(session_id or "").strip()
    parent_session = str(parent_session_id or "").strip()

    user_raw = user_id or user_id_alt or user_name or ""
    user_hash = hash_gateway_identifier(user_raw, salt)
    chat_hash = hash_gateway_identifier(chat_id, salt)
    thread_hash = hash_gateway_identifier(thread_id, salt)
    gateway_hash = hash_gateway_identifier(gateway_session_key, salt)

    if scope_level is None:
        is_gateway = platform_value != "cli" and (
            user_raw or chat_id or thread_id or gateway_session_key
        )
        if is_gateway:
            if user_hash and chat_hash and thread_hash:
                scope_level = "thread"
            elif user_hash and chat_hash:
                scope_level = "chat"
            elif user_hash:
                scope_level = "platform_user"
            else:
                scope_level = "session"
        else:
            scope_level = "profile"

    scope = ExperienceScope(
        profile_id=profile,
        workspace_id=workspace,
        session_id=session,
        parent_session_id=parent_session,
        platform=platform_value,
        scope_level=str(scope_level or "profile"),
        user_scope_hash=user_hash,
        chat_scope_hash=chat_hash,
        thread_scope_hash=thread_hash,
        gateway_session_hash=gateway_hash,
    )
    validate_scope(scope)
    return scope


def validate_scope(scope: ExperienceScope) -> None:
    if not scope.profile_id:
        raise ValueError("profile_id is required")
    if scope.scope_level not in VALID_SCOPE_LEVELS:
        raise ValueError(f"invalid scope_level: {scope.scope_level}")
    fields = (
        scope.workspace_id,
        scope.session_id,
        scope.parent_session_id,
        scope.platform,
        scope.user_scope_hash,
        scope.chat_scope_hash,
        scope.thread_scope_hash,
        scope.gateway_session_hash,
    )
    if any(value is None for value in fields):
        raise ValueError("scope fields must use empty strings, not NULL")
    if scope.scope_level == "workspace" and not scope.workspace_id:
        raise ValueError("workspace scope requires workspace_id")
    if scope.scope_level == "platform_user" and not scope.user_scope_hash:
        raise ValueError("platform_user scope requires user_scope_hash")
    if scope.scope_level == "chat" and not (
        scope.user_scope_hash and scope.chat_scope_hash
    ):
        raise ValueError("chat scope requires user_scope_hash and chat_scope_hash")
    if scope.scope_level == "thread" and not (
        scope.user_scope_hash and scope.chat_scope_hash and scope.thread_scope_hash
    ):
        raise ValueError(
            "thread scope requires user_scope_hash, chat_scope_hash, and thread_scope_hash"
        )
    if scope.scope_level == "session" and not scope.session_id:
        raise ValueError("session scope requires session_id")


def scope_identity_tuple(scope: ExperienceScope) -> tuple[str, ...]:
    return (
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
    )


def _col(name: str, alias: str | None) -> str:
    return f"{alias}.{name}" if alias else name


def scope_sql_predicate(scope: ExperienceScope, alias: str | None = None) -> str:
    profile = _col("profile_id", alias)
    workspace = _col("workspace_id", alias)
    platform = _col("platform", alias)
    scope_level = _col("scope_level", alias)
    user_hash = _col("user_scope_hash", alias)
    chat_hash = _col("chat_scope_hash", alias)
    thread_hash = _col("thread_scope_hash", alias)
    session_id = _col("session_id", alias)
    session_ids = _session_scope_ids(scope)
    session_placeholders = ", ".join("?" for _ in session_ids) or "?"
    return (
        f"({profile} = ? AND {workspace} = ? AND {platform} = ? AND ("
        f"{scope_level} = 'profile' OR "
        f"{scope_level} = 'workspace' OR "
        f"({scope_level} = 'platform_user' AND {user_hash} = ?) OR "
        f"({scope_level} = 'chat' AND {user_hash} = ? AND {chat_hash} = ?) OR "
        f"({scope_level} = 'thread' AND {user_hash} = ? AND {chat_hash} = ? AND {thread_hash} = ?) OR "
        f"({scope_level} = 'session' AND {session_id} IN ({session_placeholders}))))"
    )


def _session_scope_ids(scope: ExperienceScope) -> list[str]:
    values: list[str] = []
    for value in (scope.session_id, *getattr(scope, "session_lineage", ()), scope.parent_session_id):
        text = str(value or "")
        if text and text not in values:
            values.append(text)
    return values or [scope.session_id]


def scope_sql_params(scope: ExperienceScope) -> list[str]:
    return [
        scope.profile_id,
        scope.workspace_id,
        scope.platform,
        scope.user_scope_hash,
        scope.user_scope_hash,
        scope.chat_scope_hash,
        scope.user_scope_hash,
        scope.chat_scope_hash,
        scope.thread_scope_hash,
        *_session_scope_ids(scope),
    ]


_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|api\s+key|token|secret|password|passwd|pwd|credentials?|private\s+key)\s*[:=]\s*['\"]?[^'\"\s]+"
)
_CREDENTIALS_ARE_RE = re.compile(
    r"(?i)\b((?:my|the|a|an|our|your)?\s*(?:login\s+)?credentials?)\s+"
    r"(?:is|was|are|were)\s+['\"]?[^'\"\s.,;:]+"
)
_NATURAL_SECRET_RE = re.compile(
    r"(?i)\b((?:my|the|a|an|our|your)?\s*"
    r"(?:[\w-]+\s+){0,6}"
    r"(?:api[_-]?key|api\s+key|token|secret|password|passwd|pwd|credentials?|private\s+key)"
    r"(?:\s+(?:for|to|of|in|on|at|with|from|[\w-]+)){0,8}"
    r"\s+(?:is|was|are|were))\s+['\"]?[^'\"\s.,;:]+"
)
_ENV_SECRET_RE = re.compile(
    r"(?im)^([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD)[A-Z0-9_]*)=.*$"
)
_COMMON_KEY_RE = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9_-]{16,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"gh[pousr]_[A-Za-z0-9_]{16,}|"
    r"xox[baprs]-[A-Za-z0-9-]{16,}"
    r")\b"
)


def redact_text(text: str | None) -> str:
    if text is None:
        return ""
    value = str(text)
    value = _PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", value)
    value = _BEARER_RE.sub("Bearer [REDACTED]", value)
    value = _ENV_SECRET_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
    value = _CREDENTIALS_ARE_RE.sub(lambda m: f"{m.group(1)} are [REDACTED]", value)
    value = _NATURAL_SECRET_RE.sub(lambda m: f"{m.group(1)} [REDACTED]", value)
    value = _SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
    value = _COMMON_KEY_RE.sub("[REDACTED_SECRET]", value)
    return value


def redact_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        return redact_text(payload)
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    if isinstance(payload, tuple):
        return [redact_payload(item) for item in payload]
    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            if re.search(r"(?i)(api[_-]?key|api\s+key|token|secret|password|passwd|pwd|credentials?)", str(key)):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = redact_payload(value)
        return redacted
    return payload


def content_hash_for_record(
    *,
    kind: str,
    title: str,
    body: str,
    applies_when: Any = None,
    does_not_apply_when: Any = None,
    evidence: Any = None,
    tags: Any = None,
) -> str:
    payload = {
        "kind": kind,
        "title": title,
        "body": body,
        "applies_when": applies_when or {},
        "does_not_apply_when": does_not_apply_when or {},
        "evidence": evidence or {},
        "tags": tags or [],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def meta_timestamp() -> str:
    return utc_now()
