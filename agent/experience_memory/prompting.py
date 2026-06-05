"""Prompt formatting helpers for Experience Memory Engine recall."""

from __future__ import annotations

from html import escape
from typing import Any, Iterable


CONTEXT_OPEN_TAG = "<experience-memory-context>"
CONTEXT_CLOSE_TAG = "</experience-memory-context>"
_MAX_EVIDENCE_FIELDS = 3
_MAX_EVIDENCE_VALUE_CHARS = 160


def extract_text_from_user_content(content: Any) -> str:
    """Return text parts from a string or multimodal user message."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            text = part.strip()
            if text:
                parts.append(text)
            continue
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            text = part["text"].strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts).strip()


def append_context_to_user_content(content: Any, context_block: str) -> Any:
    """Append an ephemeral context block without mutating the source content."""
    block = str(context_block or "").strip()
    if not block:
        return content

    if isinstance(content, str):
        return f"{content}\n\n{block}" if content else block

    if isinstance(content, list):
        copied: list[Any] = [
            part.copy() if isinstance(part, dict) else part
            for part in content
        ]
        copied.append({"type": "text", "text": block})
        return copied

    return block if content in (None, "") else f"{content}\n\n{block}"


def format_experience_memory_context(
    results: Iterable[Any],
    *,
    max_items: int | None = None,
) -> str:
    """Format recall results as a compact, escaped prompt context block."""
    items = list(results or [])
    if max_items is not None:
        try:
            items = items[: max(0, int(max_items))]
        except Exception:
            pass
    if not items:
        return ""

    formatted: list[str] = []
    for index, result in enumerate(items, start=1):
        record_id = _field(result, "record_id")
        kind = _field(result, "kind")
        title = _field(result, "title")
        body = _field(result, "body")
        if not (record_id or title or body):
            continue
        confidence = _confidence(_field(result, "confidence", 0.0))
        lines = [
            f"{index}. [{_safe(kind or 'case')} | confidence={confidence:.2f} | record_id={_safe(record_id)}]"
        ]
        if title:
            lines.append(f"Title: {_safe(title)}")
        if body:
            lines.append(f"Body: {_single_paragraph(_safe(body))}")

        tags = _field(result, "tags", [])
        if isinstance(tags, (list, tuple)) and tags:
            bounded_tags = []
            for tag in list(tags)[:8]:
                tag_text = _truncate_text(_single_paragraph(str(tag)), 80)
                if tag_text.strip():
                    bounded_tags.append(_safe(tag_text))
            if len(tags) > 8:
                bounded_tags.append("...")
            safe_tags = ", ".join(bounded_tags)
            if safe_tags:
                lines.append(f"Tags: {safe_tags}")

        evidence = _field(result, "evidence", {})
        evidence_line = _format_evidence(evidence)
        if evidence_line:
            lines.append(f"Evidence: {evidence_line}")

        formatted.append("\n".join(lines))

    if not formatted:
        return ""

    header = (
        "Scoped local experience that may be relevant. Use only if it applies; current\n"
        "user instructions and repository evidence win. Use record_id values if you need\n"
        "to correct stale or unsafe memory."
    )
    joined = "\n\n".join(formatted)
    return (
        f"{CONTEXT_OPEN_TAG}\n"
        f"{header}\n\n"
        f"{joined}\n"
        f"{CONTEXT_CLOSE_TAG}"
    )


def _field(result: Any, name: str, default: Any = "") -> Any:
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _single_paragraph(text: str) -> str:
    return " ".join(str(text or "").split())


def _safe(value: Any) -> str:
    return escape(str(value or ""), quote=False)


def _format_evidence(evidence: Any) -> str:
    if not isinstance(evidence, dict) or not evidence:
        return ""
    parts: list[str] = []
    for key, value in evidence.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (dict, list, tuple)):
            value_text = _single_paragraph(str(value))
        else:
            value_text = _single_paragraph(str(value))
        if not value_text:
            continue
        value_text = _truncate_text(value_text, _MAX_EVIDENCE_VALUE_CHARS)
        parts.append(f"{_safe(key)}={_safe(value_text)}")
        if len(parts) >= _MAX_EVIDENCE_FIELDS:
            break
    return "; ".join(parts)


def _truncate_text(value: str, limit: int) -> str:
    text = str(value or "")
    limit = max(1, int(limit or 1))
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
