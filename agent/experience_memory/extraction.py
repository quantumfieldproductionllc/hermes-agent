"""Deterministic explicit-signal extraction for Experience Memory Engine."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any

from agent.experience_memory.privacy import redact_text


@dataclass(frozen=True)
class ExplicitSignalRecord:
    signal: str
    payload: str
    redacted_payload: str
    kind: str
    title: str
    body: str
    evidence: dict[str, Any]
    payload_hash: str


_SIGNALS: tuple[tuple[str, str, str | None, bool], ...] = (
    (r"remember\s+this\s+as\s+a\s+rejected\s+hypothesis", "remember_rejected_hypothesis", "rejected_hypothesis", False),
    (r"record\s+this\s+as\s+a\s+skill\s+candidate", "record_skill_candidate", "skill_candidate", False),
    (r"record\s+this\s+as\s+a\s+decision", "record_decision", "decision", False),
    (r"save\s+this\s+as\s+a\s+rule", "save_rule", "rule", False),
    (r"for\s+future\s+reference", "for_future_reference", None, False),
    (r"make\s+a\s+note\s+that", "make_note_that", None, False),
    (r"remember\s+that", "remember_that", None, False),
    (r"remember\s+this", "remember_this", None, False),
    (r"record\s+this", "record_this", None, False),
    (r"learn\s+this", "learn_this", None, False),
    (r"save\s+this", "save_this", None, False),
    (r"remember\s+to", "remember_to", None, True),
)

_SIGNAL_RE = re.compile(
    r"(?i)\b("
    + "|".join(pattern for pattern, _name, _kind, _remember_to in _SIGNALS)
    + r")\b\s*:?\s*"
)

_DURABLE_RE = re.compile(
    r"(?i)\b(always|never|prefer|from now on|next time|in this repo|for future|when|whenever|use|uses|using)\b"
)
_ONE_OFF_RE = re.compile(
    r"(?i)\b(today|tomorrow|tonight|next\s+(week|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"send|call|email|message|text|pay|buy|book|schedule|appointment|meeting|report)\b"
)
_VAGUE_RE = re.compile(
    r"(?i)^(this|that|it|this\s+conversation|this\s+chat|this\s+session|learn\s+from\s+this|remember\s+this\s+conversation)[.!?\s]*$"
)
_SECRET_RE = re.compile(
    r"(?is)(-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]{12,}|"
    r"\b(api[_-]?key|api\s+key|token|secret|password|passwd|pwd|credentials?|private\s+key)\s*[:=]\s*['\"]?[^'\"\s]+|"
    r"\b(?:my|the|a|an|our|your)?\s*(?:[\w-]+\s+){0,6}"
    r"(api[_-]?key|api\s+key|token|secret|password|passwd|pwd|credentials?|private\s+key)"
    r"(?:\s+(?:for|to|of|in|on|at|with|from|[\w-]+)){0,8}\s+"
    r"(?:is|was|are|were)\s+['\"]?[^'\"\s.,;:]+|"
    r"\b(sk-[A-Za-z0-9_-]{16,}|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9_]{16,}|xox[baprs]-[A-Za-z0-9-]{16,})\b)"
)
_NEGATED_SIGNAL_RE = re.compile(
    r"(?is)(?:^|\b)(?:please\s+)?(?:do\s+not|don't|dont|never|should\s+not|must\s+not)(?:\s+\w+){0,6}\s*$"
)
_USER_MODEL_RE = re.compile(
    r"(?i)\b(i\s+prefer|i\s+like|i\s+want|i\s+usually|i\s+use|i\s+work|i\s+am|i'm|my\s+)\b"
)
_DECISION_RE = re.compile(r"(?i)^(we\s+decided|decision\s*:)")
_REJECTED_RE = re.compile(
    r"(?i)\b(rejected\s+hypothesis|was\s+wrong|is\s+wrong|failed|fails|misleading|should\s+be\s+avoided|avoid\s+this|does\s+not\s+work|did\s+not\s+work|didn't\s+work)\b"
)
_SKILL_CANDIDATE_RE = re.compile(r"(?i)\b(skill\s+candidate|turn\s+.+\s+into\s+a\s+skill|make\s+.+\s+a\s+skill)\b")
_RULE_RE = re.compile(r"(?i)\b(always|never|when|whenever|use|uses|prefer|from now on|next time|in this repo)\b")


def extract_explicit_signals(
    user_text: str,
    *,
    config: dict[str, Any] | None = None,
    source_turn_index: int | None = None,
) -> list[ExplicitSignalRecord]:
    """Extract only explicit durable remember/record/learn/save requests."""
    text = str(user_text or "")
    if not text.strip():
        return []

    config = config or {}
    extraction_cfg = config.get("extraction") or {}
    privacy_cfg = config.get("privacy") or {}
    max_records = int(extraction_cfg.get("max_records_per_turn", 3) or 3)
    max_title_chars = int(extraction_cfg.get("max_title_chars", 90) or 90)
    max_body_chars = int(extraction_cfg.get("max_body_chars", 700) or 700)
    max_raw_excerpt_chars = int(extraction_cfg.get("max_raw_excerpt_chars", 240) or 240)
    allow_user_model = bool(privacy_cfg.get("allow_user_model", True))
    store_raw_excerpts = bool(privacy_cfg.get("store_raw_excerpts", False))

    records: list[ExplicitSignalRecord] = []
    matches = list(_SIGNAL_RE.finditer(text))
    for index, match in enumerate(matches):
        signal_text = match.group(1)
        if _is_negated_signal(text[: match.start()]):
            continue
        signal_name, explicit_kind, is_remember_to = _signal_metadata(signal_text)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        payload = _clean_payload(text[match.end():end])
        if not _payload_is_acceptable(payload, is_remember_to=is_remember_to):
            continue

        redacted_payload = redact_text(payload)
        if not _redacted_payload_is_useful(payload, redacted_payload):
            continue

        kind = _select_kind(
            payload=redacted_payload,
            signal_name=signal_name,
            explicit_kind=explicit_kind,
        )
        if kind == "user_model_update" and not allow_user_model:
            continue

        body = _truncate(redacted_payload, max_body_chars)
        title = _truncate(_first_sentence(redacted_payload), max_title_chars)
        evidence: dict[str, Any] = {
            "source": "completed_turn_sync",
            "signal": signal_name,
        }
        if source_turn_index is not None:
            evidence["source_turn_index"] = source_turn_index
        if store_raw_excerpts:
            evidence["excerpt"] = _truncate(redacted_payload, max_raw_excerpt_chars)

        records.append(
            ExplicitSignalRecord(
                signal=signal_name,
                payload=payload,
                redacted_payload=redacted_payload,
                kind=kind,
                title=title,
                body=body,
                evidence=evidence,
                payload_hash=hashlib.sha256(redacted_payload.encode("utf-8")).hexdigest(),
            )
        )
        if len(records) >= max_records:
            break

    return records


def _is_negated_signal(prefix: str) -> bool:
    tail = str(prefix or "")[-80:]
    tail = re.sub(r"[\s:;,\-.]+$", " ", tail).strip() + " "
    return bool(_NEGATED_SIGNAL_RE.search(tail))


def _signal_metadata(signal_text: str) -> tuple[str, str | None, bool]:
    normalized = re.sub(r"\s+", " ", signal_text.strip().lower())
    for pattern, name, kind, is_remember_to in _SIGNALS:
        if re.fullmatch(pattern, normalized, flags=re.IGNORECASE):
            return name, kind, is_remember_to
    return "explicit_signal", None, False


def _clean_payload(payload: str) -> str:
    value = str(payload or "").strip()
    value = re.sub(r"^[\s:;,\-.]+", "", value).strip()
    value = value.strip("\"'`")
    return re.sub(r"\s+", " ", value).strip()


def _payload_is_acceptable(payload: str, *, is_remember_to: bool) -> bool:
    compact = re.sub(r"\s+", "", payload or "")
    if len(compact) < 20:
        return False
    lowered = payload.strip().lower()
    if _VAGUE_RE.match(lowered):
        return False
    if is_remember_to and not _DURABLE_RE.search(payload):
        return False
    if _ONE_OFF_RE.search(payload) and not _DURABLE_RE.search(payload):
        return False
    if _SECRET_RE.search(payload):
        return False
    return True


def _redacted_payload_is_useful(original: str, redacted: str) -> bool:
    compact = re.sub(r"\s+", "", redacted or "")
    if len(compact) < 20:
        return False
    if original != redacted and "[REDACTED" in redacted:
        useful = re.sub(r"\[REDACTED[^\]]*\]", "", redacted)
        if len(re.sub(r"\s+", "", useful)) < 20:
            return False
    return True


def _select_kind(
    *,
    payload: str,
    signal_name: str,
    explicit_kind: str | None,
) -> str:
    if explicit_kind:
        return explicit_kind
    if signal_name == "for_future_reference" and _USER_MODEL_RE.search(payload):
        return "user_model_update"
    if _SKILL_CANDIDATE_RE.search(payload):
        return "skill_candidate"
    if _DECISION_RE.search(payload):
        return "decision"
    if _REJECTED_RE.search(payload):
        return "rejected_hypothesis"
    if _USER_MODEL_RE.search(payload):
        return "user_model_update"
    if _RULE_RE.search(payload):
        return "rule"
    return "case"


def _first_sentence(payload: str) -> str:
    value = str(payload or "").strip()
    if not value:
        return ""
    match = re.search(r"(.+?[.!?])(?:\s|$)", value)
    return (match.group(1) if match else value).strip()


def _truncate(value: str, limit: int) -> str:
    limit = max(1, int(limit or 1))
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)].rstrip() + "..."
