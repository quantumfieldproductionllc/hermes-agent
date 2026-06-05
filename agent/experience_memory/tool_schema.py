"""Dynamic tool schema for the Experience Memory Engine MVP."""

from __future__ import annotations

from agent.experience_memory.models import ALLOWED_CORRECTION_OPERATIONS, ALLOWED_KINDS


def experience_memory_tool_schema(max_recall_items: int = 6) -> dict:
    return {
        "name": "experience_memory",
        "description": (
            "Manually inspect, record, recall, or correct local durable "
            "experience memory for the current Hermes profile and scope."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "record", "recall", "correct"],
                    "description": "Operation to perform.",
                },
                "kind": {
                    "type": "string",
                    "enum": sorted(ALLOWED_KINDS),
                    "description": "Record kind for action=record.",
                },
                "title": {
                    "type": "string",
                    "description": "Short title for action=record.",
                },
                "body": {
                    "type": "string",
                    "description": "Durable lesson or experience body for action=record.",
                },
                "applies_when": {
                    "type": "object",
                    "description": "Optional applicability conditions for action=record.",
                },
                "does_not_apply_when": {
                    "type": "object",
                    "description": "Optional non-applicability conditions for action=record.",
                },
                "evidence": {
                    "type": "object",
                    "description": "Optional compact evidence for action=record.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for action=record.",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Optional confidence for action=record.",
                },
                "idempotency_key": {
                    "type": "string",
                    "description": "Optional caller-stable key for duplicate-safe manual records.",
                },
                "query": {
                    "type": "string",
                    "description": "Full-text query for action=recall.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": max(1, int(max_recall_items or 1)),
                    "description": "Maximum recall results for action=recall.",
                },
                "record_id": {
                    "type": "string",
                    "description": "Target record for action=correct.",
                },
                "operation": {
                    "type": "string",
                    "enum": sorted(ALLOWED_CORRECTION_OPERATIONS),
                    "description": "Correction operation for action=correct.",
                },
                "reason": {
                    "type": "string",
                    "description": "Required audit reason for action=correct.",
                },
                "replacement_title": {
                    "type": "string",
                    "description": "Replacement title required for operation=supersede.",
                },
                "replacement_body": {
                    "type": "string",
                    "description": "Replacement body required for operation=supersede.",
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
            ],
        },
    }
