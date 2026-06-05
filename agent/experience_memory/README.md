# Experience Memory Engine MVP

Experience Memory Engine (EME) is Hermes Agent's local, profile-scoped store for reusable operational experience: cases, decisions, rules, rejected hypotheses, user-model updates, and skill candidates.

This MVP is intentionally narrow: it gives Hermes a canonical SQLite memory substrate and a manual dynamic tool. It does not yet run automatic extraction, prompt prefetch injection, projection workers, embeddings, or a dedicated CLI command surface.

## What exists now

- Canonical database: `$HERMES_HOME/experience/experience.db`
- Runtime package: `agent/experience_memory/`
- Dynamic agent-level tool: `experience_memory`
- Supported actions: `status`, `record`, `recall`, `correct`
- Record kinds:
  - `case`
  - `decision`
  - `rejected_hypothesis`
  - `rule`
  - `user_model_update`
  - `skill_candidate`
- Correction operations:
  - `retract`
  - `tombstone`
  - `supersede`
  - `scrub`
- Scope isolation across profile, workspace, platform, user, chat, thread, gateway session, and session IDs.
- FTS5 local recall with scope filtering before ranking/limiting.
- Queue placeholder tables for future extraction/projection workers.

## Why it exists

Hermes already has memory-adjacent surfaces: session history, `MEMORY.md`, `USER.md`, external memory providers, skills, and Hindsight. Those are useful, but none of them is a local canonical store for reusable experience and corrections.

EME makes SQLite the source of truth. Hindsight, skills, Obsidian, and compact prompt memory can later become projections from EME instead of being the canonical memory layer themselves.

## Runtime architecture

Core files:

- `engine.py` — runtime coordinator and dynamic tool handler.
- `store.py` — canonical SQLite write/read layer. All persistence flows through this file.
- `schema.py` — declarative SQLite schema, currently schema version 2.
- `migrations.py` — atomic schema/data migrations.
- `models.py` — Pydantic/domain models and allowed record/correction values.
- `privacy.py` — scope normalization, gateway hashing, secret redaction, and content hashing.
- `retrieval.py` — FTS recall and token-budgeted result shaping.
- `tool_schema.py` — JSON schema for the dynamic `experience_memory` tool.
- `tool_gating.py` — toolset allow/deny logic for dynamic schema injection.

Integration points in Hermes:

- `agent/agent_init.py` initializes EME when `experience_memory.enabled` is true and `skip_memory` is false.
- `agent/tool_executor.py` routes sequential `experience_memory` calls before registry fallback.
- `agent/agent_runtime_helpers.py` routes runtime/helper calls before registry fallback.
- `model_tools.py` intentionally refuses direct registry fallback dispatch for `experience_memory`.
- `run_agent.py` closes the EME store during agent shutdown.
- `toolsets.py`, `hermes_cli/config.py`, and `hermes_cli/tools_config.py` expose config/toolset wiring without registering a normal tool module.

Important design choice: there is no `tools/experience_memory_tool.py`, no `registry.register(...)`, and no `_HERMES_CORE_TOOLS` entry. This tool is dynamic and agent-owned.

## Configuration

Default config is disabled:

```yaml
experience_memory:
  enabled: false
  mode: shadow
  tools_enabled: true
  prefetch_enabled: false
  max_recall_items: 6
  recall_token_budget: 1200
  store:
    busy_timeout_ms: 5000
    retry_writes: 2
  privacy:
    redact_secrets: true
    store_raw_excerpts: false
    store_tool_payloads: false
    allow_user_model: true
    allow_cross_chat_retrieval: false
    hash_gateway_ids: true
  extraction:
    enabled: false
    explicit_signals_only: true
    max_records_per_turn: 3
    max_title_chars: 90
    max_body_chars: 700
    max_raw_excerpt_chars: 240
    max_queue_size: 200
    retry_limit: 3
  projections:
    enabled: false
```

Enable locally:

```bash
hermes config set experience_memory.enabled true
hermes config set experience_memory.prefetch_enabled true
hermes config set experience_memory.extraction.enabled true
hermes tools enable experience_memory
```

Tool changes require a new session/reset to affect the model tool list.

## Tool usage examples

Status:

```json
{"action":"status"}
```

Record a durable lesson:

```json
{
  "action": "record",
  "kind": "case",
  "title": "SQLite WAL fallback on read-only filesystems",
  "body": "When WAL cannot be enabled, keep running and report the fallback journal mode in status.",
  "tags": ["sqlite", "migration", "fallback"],
  "confidence": 0.8,
  "idempotency_key": "stable-caller-key"
}
```

Recall scoped local experience:

```json
{"action":"recall", "query":"sqlite migration fallback", "limit":3}
```

Correct a bad record:

```json
{
  "action": "correct",
  "operation": "supersede",
  "record_id": "rec_...",
  "reason": "New evidence changed the recommended workflow.",
  "replacement_title": "Corrected lesson",
  "replacement_body": "Updated durable guidance."
}
```

Scrub a record for privacy:

```json
{
  "action": "correct",
  "operation": "scrub",
  "record_id": "rec_...",
  "reason": "Contains sensitive user data."
}
```

## Privacy and safety behavior

- Gateway identifiers are hashed with a profile-local salt stored in `experience_meta`.
- Non-applicable scope fields are stored as empty strings, not `NULL`.
- Store writes validate required scope fields before persistence.
- Obvious secrets are redacted before persistence, including API-key-shaped values, bearer tokens, private key blocks, env-style secrets, and token/password assignments.
- Caller-provided idempotency keys are never stored raw. They are persisted as `idem_` + HMAC-SHA256 using the profile-local salt.
- Legacy raw idempotency keys are migrated to safe HMAC keys, including old raw values that happen to start with `idem_`.
- `scrub` removes FTS content and redacts canonical/event payload content while preserving relational history.
- Inactive records cannot be resurrected by later corrections.

## Current tests

Focused coverage lives in:

- `tests/agent/test_experience_memory_migrations.py`
- `tests/agent/test_experience_memory_privacy.py`
- `tests/agent/test_experience_memory_store.py`
- `tests/agent/test_experience_memory_retrieval.py`
- `tests/agent/test_experience_memory_engine.py`
- `tests/run_agent/test_experience_memory_init.py`
- `tests/run_agent/test_experience_memory_tool_routing.py`
- `tests/hermes_cli/test_experience_memory_tools_config.py`
- `tests/test_toolsets.py`

Verification command:

```bash
scripts/run_tests.sh \
  tests/hermes_cli/test_experience_memory_tools_config.py \
  tests/test_toolsets.py \
  tests/agent/test_experience_memory_migrations.py \
  tests/agent/test_experience_memory_privacy.py \
  tests/agent/test_experience_memory_store.py \
  tests/agent/test_experience_memory_retrieval.py \
  tests/agent/test_experience_memory_engine.py \
  tests/run_agent/test_experience_memory_init.py \
  tests/run_agent/test_experience_memory_tool_routing.py
```

Last verified result during implementation review: `92 passed, 0 failed`.

## What is not implemented yet

These are deliberate post-MVP layers, not missing MVP pieces:

- LLM-based turn extraction.
- Compression-window extraction.
- External projection workers for Hindsight, skills, Obsidian, or memory files.
- Embedding retrieval.
- `hermes experience ...` CLI commands.
- Automatic skill creation.
- Rich split tables for cases/decisions/rules/user-model updates/skill candidates.

## Usable MVP layer

The usable MVP adds prompt-time scoped recall injection and deterministic
capture of explicit user "remember/record/learn/save" instructions. Both are
disabled by default and fail open. Recall context is appended only to the
current API user-message copy; it is not written to the system prompt,
transcript, session DB, or trajectory.

See `docs/plans/experience-memory-usable-mvp-plan.md` for the approved design.
It deliberately excludes LLM extraction, projections, embeddings, CLI commands,
registry-backed tools, and automatic skill creation.
