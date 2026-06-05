"""Data models for the Experience Memory Engine MVP."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


ALLOWED_KINDS = {
    "case",
    "decision",
    "rejected_hypothesis",
    "rule",
    "user_model_update",
    "skill_candidate",
}

ALLOWED_STATUSES = {
    "active",
    "superseded",
    "retracted",
    "tombstoned",
    "scrubbed",
}

ALLOWED_CORRECTION_OPERATIONS = {
    "retract",
    "tombstone",
    "supersede",
    "scrub",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class ExperienceScope:
    profile_id: str
    workspace_id: str = "hermes"
    session_id: str = ""
    parent_session_id: str = ""
    platform: str = "cli"
    scope_level: str = "profile"
    user_scope_hash: str = ""
    chat_scope_hash: str = ""
    thread_scope_hash: str = ""
    gateway_session_hash: str = ""
    session_lineage: tuple[str, ...] = ()


@dataclass
class ExperienceEvent:
    event_type: str
    scope: ExperienceScope
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    event_id: str = field(default_factory=lambda: new_id("evt"))
    created_at: str = field(default_factory=utc_now)


@dataclass
class ExperienceRecord:
    kind: str
    scope: ExperienceScope
    title: str
    body: str
    applies_when: dict[str, Any] = field(default_factory=dict)
    does_not_apply_when: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    source_event_id: str | None = None
    source_session_id: str = ""
    source_turn_index: int | None = None
    privacy_level: str = "profile"
    confidence: float = 0.5
    record_id: str = field(default_factory=lambda: new_id("rec"))
    content_hash: str = ""
    status: str = "active"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    superseded_by: str | None = None
    deleted_at: str | None = None


@dataclass(frozen=True)
class ExperienceQuery:
    query: str
    scope: ExperienceScope
    limit: int = 6
    token_budget: int = 1200


@dataclass
class ExperienceResult:
    record_id: str
    kind: str
    title: str
    body: str
    confidence: float
    status: str
    tags: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "confidence": self.confidence,
            "status": self.status,
            "tags": self.tags,
            "evidence": self.evidence,
        }


@dataclass
class ExperienceCorrection:
    record_id: str
    operation: str
    reason: str
    scope: ExperienceScope
    replacement_title: str | None = None
    replacement_body: str | None = None
    correction_id: str = field(default_factory=lambda: new_id("cor"))
    created_at: str = field(default_factory=utc_now)
