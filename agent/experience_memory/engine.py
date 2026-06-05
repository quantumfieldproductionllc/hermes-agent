"""Runtime coordinator for the Experience Memory Engine MVP."""

from __future__ import annotations

from dataclasses import replace
import json
import logging
from pathlib import Path
from typing import Any

from agent.experience_memory.extraction import extract_explicit_signals
from agent.experience_memory.models import (
    ALLOWED_CORRECTION_OPERATIONS,
    ALLOWED_KINDS,
    ExperienceCorrection,
    ExperienceEvent,
    ExperienceQuery,
    ExperienceRecord,
    ExperienceScope,
)
from agent.experience_memory.prompting import (
    extract_text_from_user_content,
    format_experience_memory_context,
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
        self.enabled = bool(self.config.get("enabled", False))
        self.prefetch_enabled = bool(self.config.get("prefetch_enabled", False))
        self.extraction_config = self.config.get("extraction") or {}
        self.extraction_enabled = bool(self.extraction_config.get("enabled", False))
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
        lineage = tuple(
            str(item)
            for item in (kwargs.get("session_lineage") or ())
            if str(item or "") and str(item or "") != str(session_id or "")
        )
        if lineage:
            self.scope = replace(self.scope, session_lineage=lineage)
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

    def prefetch(self, query: Any, *, session_id: str = "") -> str:
        """Return formatted scoped recall context for the current turn."""
        try:
            if (
                not self.enabled
                or not self.prefetch_enabled
                or not self.initialized
                or self.scope is None
            ):
                return ""
            text = extract_text_from_user_content(query)
            if not text:
                return ""
            results = self.store.recall(
                ExperienceQuery(
                    query=text,
                    scope=self.scope,
                    limit=self.max_recall_items,
                    token_budget=self.recall_token_budget,
                )
            )
            return format_experience_memory_context(
                results,
                max_items=self.max_recall_items,
            )
        except Exception as exc:
            logger.warning("experience_memory prefetch failed: %s", exc, exc_info=True)
            return ""

    def sync_turn(
        self,
        *,
        original_user_message: Any,
        final_response: Any = None,
        messages: list | None = None,
        completed: bool = True,
        failed: bool = False,
        interrupted: bool = False,
        session_id: str = "",
        source_turn_index: int | None = None,
        turn_error: Any = None,
    ) -> None:
        """Capture explicit user memory instructions from a completed turn."""
        try:
            if (
                not self.enabled
                or not self.extraction_enabled
                or not self.initialized
                or self.scope is None
                or completed is not True
                or failed
                or interrupted
                or turn_error is not None
                or not final_response
            ):
                return
            user_text = extract_text_from_user_content(original_user_message)
            if not user_text:
                return

            candidates = extract_explicit_signals(
                user_text,
                config=self.config,
                source_turn_index=source_turn_index,
            )
            if not candidates:
                return

            scope_session_id = session_id or self.scope.session_id
            turn_part = "" if source_turn_index is None else str(source_turn_index)
            for signal_index, candidate in enumerate(candidates):
                idempotency_key = (
                    "auto_explicit_signal:"
                    f"{scope_session_id}:{turn_part}:{signal_index}:{candidate.payload_hash}"
                )
                event_payload = {
                    "source": "completed_turn_sync",
                    "signal": candidate.signal,
                    "kind": candidate.kind,
                    "title": candidate.title,
                    "payload_hash": candidate.payload_hash,
                }
                if source_turn_index is not None:
                    event_payload["source_turn_index"] = source_turn_index

                event = ExperienceEvent(
                    event_type="auto_explicit_signal",
                    scope=self.scope,
                    payload=event_payload,
                    idempotency_key=idempotency_key,
                )
                record = ExperienceRecord(
                    kind=candidate.kind,
                    scope=self.scope,
                    title=candidate.title,
                    body=candidate.body,
                    evidence=candidate.evidence,
                    source_session_id=scope_session_id,
                    source_turn_index=source_turn_index,
                    confidence=0.85,
                )
                self.store.record_with_event(event, record)
        except Exception as exc:
            logger.warning("experience_memory sync_turn failed: %s", exc, exc_info=True)
            return None

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        session_lineage: list[str] | tuple[str, ...] | None = None,
        reset: bool = False,
        reason: str = "",
        **kwargs,
    ) -> None:
        """Update EME scope metadata when Hermes rotates session ids."""
        if self.scope is None:
            return
        try:
            lineage: list[str] = [] if reset else list(getattr(self.scope, "session_lineage", ()))
            if not reset:
                for value in (*(session_lineage or ()), self.scope.session_id, parent_session_id):
                    text = str(value or "")
                    if text and text != str(new_session_id or "") and text not in lineage:
                        lineage.append(text)
            self.scope = replace(
                self.scope,
                session_id=str(new_session_id or ""),
                parent_session_id="" if reset else str(parent_session_id or ""),
                session_lineage=tuple(lineage),
            )
        except Exception as exc:
            logger.debug("experience_memory on_session_switch failed: %s", exc)

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
        return self._json_ok({"results": [self._safe_result_dict(result) for result in results]})

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

    def _safe_result_dict(self, result) -> dict[str, Any]:
        data = result.to_dict()
        data["evidence"] = self._bound_tool_payload(data.get("evidence", {}))
        data["tags"] = self._bound_tool_payload(data.get("tags", []), max_string_chars=80)
        return data

    def _bound_tool_payload(
        self,
        value: Any,
        *,
        max_string_chars: int = 160,
        max_depth: int = 2,
        max_items: int = 8,
    ) -> Any:
        if max_depth <= 0:
            return self._truncate_tool_string(value, max_string_chars)
        if isinstance(value, dict):
            bounded: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= max_items:
                    bounded["..."] = "truncated"
                    break
                safe_key = self._truncate_tool_string(key, 80)
                bounded[safe_key] = self._bound_tool_payload(
                    item,
                    max_string_chars=max_string_chars,
                    max_depth=max_depth - 1,
                    max_items=max_items,
                )
            return bounded
        if isinstance(value, (list, tuple)):
            items = [
                self._bound_tool_payload(
                    item,
                    max_string_chars=max_string_chars,
                    max_depth=max_depth - 1,
                    max_items=max_items,
                )
                for item in list(value)[:max_items]
            ]
            if len(value) > max_items:
                items.append("truncated")
            return items
        if isinstance(value, str):
            return self._truncate_tool_string(value, max_string_chars)
        return value

    @staticmethod
    def _truncate_tool_string(value: Any, max_chars: int) -> str:
        text = str(value or "")
        max_chars = max(1, int(max_chars or 1))
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 3)].rstrip() + "..."

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
