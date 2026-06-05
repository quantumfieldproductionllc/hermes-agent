"""Local FTS retrieval for the Experience Memory Engine MVP."""

from __future__ import annotations

import json
import sqlite3

from agent.experience_memory.models import ExperienceQuery, ExperienceResult
from agent.experience_memory.privacy import scope_sql_params, scope_sql_predicate


def sanitize_fts_query(query: str) -> str:
    try:
        from hermes_state import SessionDB

        return SessionDB._sanitize_fts5_query(str(query or ""))
    except Exception:
        return str(query or "").strip()


def _loads_json(raw: str, default):
    try:
        return json.loads(raw or "")
    except Exception:
        return default


def _truncate_results(results: list[ExperienceResult], token_budget: int) -> list[ExperienceResult]:
    char_budget = max(0, int(token_budget or 0) * 4)
    if char_budget <= 0:
        for result in results:
            result.body = ""
        return results

    remaining = char_budget
    for result in results:
        if remaining <= 0:
            result.body = ""
            continue
        if len(result.body) > remaining:
            result.body = result.body[: max(0, remaining - 3)] + "..."
            remaining = 0
        else:
            remaining -= len(result.body)
    return results


def recall_fts(conn: sqlite3.Connection, query: ExperienceQuery) -> list[ExperienceResult]:
    sanitized = sanitize_fts_query(query.query)
    if not sanitized:
        return []

    limit = max(1, int(query.limit or 1))
    predicate = scope_sql_predicate(query.scope, alias="f")
    params = [sanitized, *scope_sql_params(query.scope), limit]
    sql = f"""
        SELECT
            r.record_id,
            r.kind,
            r.title,
            r.body,
            r.confidence,
            r.status,
            r.tags_json,
            r.evidence_json,
            bm25(experience_fts) - (r.confidence * 0.05) AS score
        FROM experience_fts f
        JOIN experience_records r ON r.record_id = f.record_id
        WHERE experience_fts MATCH ?
          AND {predicate}
          AND r.deleted_at IS NULL
          AND r.status = 'active'
        ORDER BY score ASC, r.updated_at DESC, r.record_id ASC
        LIMIT ?
    """
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        # User-provided MATCH expressions can still trip FTS5 despite sanitizing.
        # Treat only those query-syntax failures as an empty recall result; DB
        # drift/corruption/missing tables must surface as tool errors.
        if "fts5:" in message or "malformed match expression" in message:
            return []
        raise

    results: list[ExperienceResult] = []
    for row in rows:
        results.append(
            ExperienceResult(
                record_id=row["record_id"],
                kind=row["kind"],
                title=row["title"],
                body=row["body"],
                confidence=float(row["confidence"]),
                status=row["status"],
                tags=_loads_json(row["tags_json"], []),
                evidence=_loads_json(row["evidence_json"], {}),
            )
        )
    return _truncate_results(results, query.token_budget)
