"""Runtime coordinator for the Experience Memory Engine MVP."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent.experience_memory.models import (
    ALLOWED_CORRECTION_OPERATIONS,
    ALLOWED_KINDS,
    ExperienceCorrection,
    ExperienceEvent,
    ExperienceQuery,
    ExperienceRecord,
    ExperienceScope,
)
from agent.experience_memory.privacy import (
    get_or_create_profile_salt,
    normalize_scope,
)
from agent.experience_memory.store import ExperienceStore
from agent.experience_memory.tool_schema import experience_memory_tool_schema

logger = logging.getLogger(__name__)


class ExperienceMemoryEngine:
    """Agent-level dynamic Experience Memory Engine."""

    tool_name = "experience_memory"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        store: ExperienceStore | None = None,
        db_path: str | Path | None = None,
    ):
        self.config = config or {}
        self.max_recall_items = int(self.config.get("max_recall_items", 6) or 6)
        self.recall_token_budget = int(self.config.get("recall_token_budget", 1200) or 1200)
        self.store = store or ExperienceStore(db_path=db_path, config=self.config)
        self.scope: ExperienceScope | None = None
        self.initialized = False

    def initialize(self, session_id: str, **kwargs) -> None:
        self.store.open()
        salt = get_or_create_profile_salt(self.store)
        profile_id = kwargs.get("agent_identity") or kwargs.get("profile_id")
        if not profile_id:
            try:
                from hermes_cli.profiles import get_active_profile_name

                profile_id = get_active_profile_name()
            except Exception:
                profile_id = "default"

        self.scope = normalize_scope(
            profile_id=str(profile_id or "default"),
            agent_workspace=kwargs.get("agent_workspace"),
            workspace_id=kwargs.get("workspace_id"),
            platform=kwargs.get("platform") or "cli",
            session_id=session_id,
            parent_session_id=kwargs.get("parent_session_id"),
            user_id=kwargs.get("user_id"),
            user_id_alt=kwargs.get("user_id_alt"),
            user_name=kwargs.get("user_name"),
            chat_id=kwargs.get("chat_id"),
            thread_id=kwargs.get("thread_id"),
            gateway_session_key=kwargs.get("gateway_session_key"),
            salt=salt,
            scope_level=kwargs.get("scope_level"),
        )
        self.initialized = True

    def get_tool_schemas(self) -> list[dict]:
        return [experience_memory_tool_schema(self.max_recall_items)]

    def handle_tool_call(self, name: str, args: dict, **kwargs) -> str:
        try:
            if name != self.tool_name:
                return self._json_error(f"unknown experience memory tool: {name}")
            if not self.initialized or self.scope is None:
                return self._json_error("experience memory is not initialized")
            if not isinstance(args, dict):
                return self._json_error("arguments must be an object")
            action = str(args.get("action") or "").strip()
            if action == "status":
                return self._handle_status()
            if action == "record":
                return self._handle_record(args)
            if action == "recall":
                return self._handle_recall(args)
            if action == "correct":
                return self._handle_correct(args)
            return self._json_error("action must be one of status, record, recall, correct")
        except Exception as exc:
            logger.warning("experience_memory tool failed: %s", exc, exc_info=True)
            return self._json_error(str(exc))

    def on_session_end(self, messages: list) -> None:
        return None

    def shutdown(self) -> None:
        self.store.close()

    def on_turn_start(self, *args, **kwargs) -> None:
        return None

    def sync_turn(self, *args, **kwargs) -> None:
        return None

    def on_pre_compress(self, *args, **kwargs) -> None:
        return None

    def on_memory_write(self, *args, **kwargs) -> None:
        return None

    def on_delegation(self, *args, **kwargs) -> None:
        return None

    def _handle_status(self) -> str:
        status = self.store.status()
        status["scope"] = self._safe_scope_dict()
        return self._json_ok(status)

    def _handle_record(self, args: dict) -> str:
        kind = str(args.get("kind") or "").strip()
        title = str(args.get("title") or "").strip()
        body = str(args.get("body") or "").strip()
        if kind not in ALLOWED_KINDS:
            return self._json_error("record.kind is required and must be a supported kind")
        if kind == "user_model_update" and not (
            (self.config.get("privacy") or {}).get("allow_user_model", True)
        ):
            return self._json_error("user_model_update records are disabled by privacy policy")
        if not title or not body:
            return self._json_error("record requires title and body")

        tags = args.get("tags") if isinstance(args.get("tags"), list) else []
        confidence = args.get("confidence", 0.5)
        try:
            confidence = float(confidence)
        except Exception:
            confidence = 0.5

        event = ExperienceEvent(
            event_type="manual_record",
            scope=self.scope,
            payload={
                "kind": kind,
                "title": title,
                "body": body,
                "tags": tags,
                "evidence": args.get("evidence") if isinstance(args.get("evidence"), dict) else {},
            },
            idempotency_key=str(args.get("idempotency_key") or ""),
        )
        record = ExperienceRecord(
            kind=kind,
            scope=self.scope,
            title=title,
            body=body,
            applies_when=args.get("applies_when") if isinstance(args.get("applies_when"), dict) else {},
            does_not_apply_when=(
                args.get("does_not_apply_when")
                if isinstance(args.get("does_not_apply_when"), dict)
                else {}
            ),
            evidence=args.get("evidence") if isinstance(args.get("evidence", None), dict) else {},
            tags=[str(tag) for tag in tags],
            source_session_id=self.scope.session_id,
            confidence=confidence,
        )
        event_id, record_id = self.store.record_with_event(event, record)
        return self._json_ok({"record_id": record_id, "event_id": event_id})

    def _handle_recall(self, args: dict) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return self._json_error("recall requires query", results=[])
        limit = self._bounded_limit(args.get("limit"))
        try:
            results = self.store.recall(
                ExperienceQuery(
                    query=query,
                    scope=self.scope,
                    limit=limit,
                    token_budget=self.recall_token_budget,
                )
            )
        except Exception as exc:
            logger.warning("experience_memory recall failed: %s", exc, exc_info=True)
            return self._json_error(str(exc), results=[])
        return self._json_ok({"results": [result.to_dict() for result in results]})

    def _handle_correct(self, args: dict) -> str:
        record_id = str(args.get("record_id") or "").strip()
        operation = str(args.get("operation") or "").strip()
        reason = str(args.get("reason") or "").strip()
        if not record_id or not operation or not reason:
            return self._json_error("correct requires record_id, operation, and reason")
        if operation not in ALLOWED_CORRECTION_OPERATIONS:
            return self._json_error("invalid correction operation")
        if operation == "scrub":
            result = self.store.scrub_record(record_id, reason, self.scope)
            return self._json_ok(result) if result.get("ok") else self._json(result)
        if operation == "supersede" and not (
            str(args.get("replacement_title") or "").strip()
            and str(args.get("replacement_body") or "").strip()
        ):
            return self._json_error("supersede requires replacement_title and replacement_body")
        result = self.store.correct(
            ExperienceCorrection(
                record_id=record_id,
                operation=operation,
                reason=reason,
                replacement_title=str(args.get("replacement_title") or "").strip() or None,
                replacement_body=str(args.get("replacement_body") or "").strip() or None,
                scope=self.scope,
            )
        )
        return self._json_ok(result) if result.get("ok") else self._json(result)

    def _bounded_limit(self, raw_limit) -> int:
        try:
            value = int(raw_limit)
        except Exception:
            value = self.max_recall_items
        return max(1, min(value, self.max_recall_items))

    def _safe_scope_dict(self) -> dict[str, str]:
        if self.scope is None:
            return {}
        return {
            "profile_id": self.scope.profile_id,
            "workspace_id": self.scope.workspace_id,
            "platform": self.scope.platform,
            "scope_level": self.scope.scope_level,
            "session_id": self.scope.session_id,
            "parent_session_id": self.scope.parent_session_id,
        }

    def _json_ok(self, payload: dict[str, Any] | None = None) -> str:
        data = {"ok": True}
        if payload:
            data.update(payload)
        return self._json(data)

    def _json_error(self, error: str, **extra) -> str:
        data = {"ok": False, "error": error}
        data.update(extra)
        return self._json(data)

    @staticmethod
    def _json(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False)
