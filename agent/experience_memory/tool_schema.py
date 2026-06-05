"""Dynamic tool schema for the Experience Memory Engine MVP."""

from __future__ import annotations

from agent.experience_memory.models import ALLOWED_CORRECTION_OPERATIONS, ALLOWED_KINDS


def experience_memory_tool_schema(max_recall_items: int = 6) -> dict:
    return {
        "name": "experience_memory",
        "description": (
            "Local scoped experience memory. Use recall before answering when the user refers "
            "to prior work, local conventions, known preferences, previous decisions, rejected "
            "approaches, or repo/platform-specific procedures. Use record only for durable, "
            "reusable lessons or explicit user instructions to remember, record, learn, or save "
            "something. Use correct when memory is wrong, superseded, retracted, unsafe, or "
            "privacy-sensitive. Current user instructions and observed repository state override "
            "recalled memory."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "record", "recall", "correct"],
                    "description": (
                        "Operation to perform: status inspects availability, recall retrieves scoped "
                        "experience, record stores one compact durable lesson, and correct retracts, "
                        "tombstones, supersedes, or scrubs stale or sensitive memory."
                    ),
                },
                "kind": {
                    "type": "string",
                    "enum": sorted(ALLOWED_KINDS),
                    "description": (
                        "Record kind for action=record. Choose intentionally: rule for durable "
                        "instructions, case for reusable examples, decision for settled choices, "
                        "rejected_hypothesis for approaches to avoid, user_model_update for user "
                        "preferences/facts, or skill_candidate for lessons that may become a skill."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Short stable title for action=record; keep it focused on one lesson.",
                },
                "body": {
                    "type": "string",
                    "description": (
                        "Compact durable lesson for action=record. Do not store secrets, raw "
                        "transcripts, private keys, credentials, one-off todos, or vague impressions."
                    ),
                },
                "applies_when": {
                    "type": "object",
                    "description": "Optional compact conditions where this record applies.",
                },
                "does_not_apply_when": {
                    "type": "object",
                    "description": "Optional compact conditions where this record should not be used.",
                },
                "evidence": {
                    "type": "object",
                    "description": (
                        "Optional minimal evidence for action=record. Prefer small metadata or "
                        "redacted snippets; do not include raw tool payloads or full transcripts."
                    ),
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional short tags for action=record.",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Optional confidence for action=record; use lower values for uncertain lessons.",
                },
                "idempotency_key": {
                    "type": "string",
                    "description": (
                        "Caller-stable key for duplicate-safe records. Supply one when recording from "
                        "a known explicit user instruction or repeated workflow."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Full-text query for action=recall. Use before context-sensitive answers such "
                        "as 'as before', repo conventions, prior decisions, preferences, or rejected approaches."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": max(1, int(max_recall_items or 1)),
                    "description": "Maximum recall results for action=recall.",
                },
                "record_id": {
                    "type": "string",
                    "description": "Target record_id for action=correct, usually from recall results.",
                },
                "operation": {
                    "type": "string",
                    "enum": sorted(ALLOWED_CORRECTION_OPERATIONS),
                    "description": (
                        "Correction operation for action=correct: retract incorrect content, tombstone "
                        "content that should no longer appear, supersede with updated guidance, or scrub sensitive content."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Required concise audit reason for action=correct.",
                },
                "replacement_title": {
                    "type": "string",
                    "description": "Replacement title required when operation=supersede.",
                },
                "replacement_body": {
                    "type": "string",
                    "description": (
                        "Replacement compact lesson required when operation=supersede; keep privacy limits "
                        "and current evidence precedence in mind."
                    ),
                },
            },
            "required": ["action"],
            "allOf": [
                {
                    "if": {"properties": {"action": {"const": "record"}}, "required": ["action"]},
                    "then": {"required": ["kind", "title", "body"]},
                },
                {
                    "if": {"properties": {"action": {"const": "recall"}}, "required": ["action"]},
                    "then": {"required": ["query"]},
                },
                {
                    "if": {"properties": {"action": {"const": "correct"}}, "required": ["action"]},
                    "then": {"required": ["record_id", "operation", "reason"]},
                },
                {
                    "if": {
                        "properties": {
                            "action": {"const": "correct"},
                            "operation": {"const": "supersede"},
                        },
                        "required": ["action", "operation"],
                    },
                    "then": {"required": ["replacement_title", "replacement_body"]},
                },
            ],
        },
    }
