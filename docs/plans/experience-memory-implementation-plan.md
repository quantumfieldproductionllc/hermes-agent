# Experience Memory Engine MVP Implementation Plan

Status: implementation plan only. This document intentionally does not
implement runtime code.

This plan narrows `docs/plans/experience-memory-architecture.md` into a
working MVP that can be shipped and tested in small slices. The MVP provides an
opt-in, local, profile-scoped SQLite store plus a dynamic agent-level
`experience_memory` tool for manual `status`, `record`, `recall`, and
`correct` workflows. It does not run external extraction or external
projection workers.

## MVP Goal

Build the smallest useful Experience Memory Engine:

- Canonical local SQLite database at `$HERMES_HOME/experience/experience.db`.
- Versioned migrations, WAL fallback, busy timeout, foreign keys, and FTS5.
- Privacy and scope helpers for profile, workspace, platform, user, chat,
  thread, gateway session, and session boundaries.
- Engine initialization only when `experience_memory.enabled` is true and
  `skip_memory` is false.
- A dynamic `experience_memory` tool owned by `ExperienceMemoryEngine`, not a
  registry-backed tool.
- Manual record creation for the core record kinds.
- Local FTS recall with scope filtering before ranking and limiting.
- Basic correction by retracting, tombstoning, or superseding records.
- Config defaults disabled.
- Toolset gating that honors `enabled_toolsets`, `disabled_toolsets`, `all`,
  `*`, and composites that expand to the memory path.
- Queue schema placeholders for future extraction and projection, with no
  worker implementation yet.

The MVP is useful because a user can enable EME, let the model call
`experience_memory` to record durable lessons, recall them later through local
FTS, and correct bad entries without relying on Hindsight, skills, Obsidian, or
network extraction.

## Explicit Non-Goals For MVP

- No `tools/experience_memory_tool.py`.
- No `registry.register(...)` for `experience_memory`.
- No entry in `_HERMES_CORE_TOOLS`.
- No external auxiliary extraction.
- No `auxiliary.experience_memory` config key yet.
- No projection adapters for Hindsight, skills, Obsidian, or memory files.
- No automatic skill creation.
- No embedding retrieval.
- No automatic turn extraction beyond manual `record` calls.
- No CLI command surface beyond existing config and toolset UI changes.
- No registry-backed dispatcher path in `model_tools.handle_function_call`.

## MVP Data Shape

Use one generic canonical record table for the MVP rather than the full
architecture's separate case, decision, rejected hypothesis, rule, user model,
and skill candidate tables. This keeps the first implementation small while
preserving the record kinds and scope columns needed for a later split-table
migration.

Schema version 1 should create these tables:

- `experience_schema_version`
- `experience_meta`
- `experience_events`
- `experience_records`
- `experience_record_corrections`
- `experience_extraction_jobs`
- `experience_projections`
- `experience_projection_watermarks`
- `experience_fts`

`experience_records` stores all MVP retrievable items. Required fields:

- `record_id TEXT PRIMARY KEY`
- `kind TEXT NOT NULL`
- `profile_id TEXT NOT NULL`
- `workspace_id TEXT NOT NULL DEFAULT 'hermes'`
- `session_id TEXT NOT NULL DEFAULT ''`
- `parent_session_id TEXT NOT NULL DEFAULT ''`
- `platform TEXT NOT NULL DEFAULT 'cli'`
- `scope_level TEXT NOT NULL DEFAULT 'profile'`
- `user_scope_hash TEXT NOT NULL DEFAULT ''`
- `chat_scope_hash TEXT NOT NULL DEFAULT ''`
- `thread_scope_hash TEXT NOT NULL DEFAULT ''`
- `gateway_session_hash TEXT NOT NULL DEFAULT ''`
- `title TEXT NOT NULL`
- `body TEXT NOT NULL`
- `applies_when_json TEXT NOT NULL DEFAULT '{}'`
- `does_not_apply_when_json TEXT NOT NULL DEFAULT '{}'`
- `evidence_json TEXT NOT NULL DEFAULT '{}'`
- `tags_json TEXT NOT NULL DEFAULT '[]'`
- `source_event_id TEXT`
- `source_session_id TEXT NOT NULL DEFAULT ''`
- `source_turn_index INTEGER`
- `content_hash TEXT NOT NULL`
- `status TEXT NOT NULL DEFAULT 'active'`
- `privacy_level TEXT NOT NULL DEFAULT 'profile'`
- `confidence REAL NOT NULL DEFAULT 0.5`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`
- `superseded_by TEXT`
- `deleted_at TEXT`

Allowed MVP `kind` values:

- `case`
- `decision`
- `rejected_hypothesis`
- `rule`
- `user_model_update`
- `skill_candidate`

Allowed MVP `status` values:

- `active`
- `superseded`
- `retracted`
- `tombstoned`
- `scrubbed`

`experience_fts` must copy scope columns directly from the source record. Do
not rely on joining through `experience_events` for scope enforcement.

The queue placeholder tables should be structurally real and migrated, but the
MVP only exposes counts in `status`. There should be no extraction or projection
worker.

## Exact Files To Create

Create these package files:

- `agent/experience_memory/__init__.py`
- `agent/experience_memory/models.py`
- `agent/experience_memory/schema.py`
- `agent/experience_memory/migrations.py`
- `agent/experience_memory/privacy.py`
- `agent/experience_memory/store.py`
- `agent/experience_memory/retrieval.py`
- `agent/experience_memory/tool_gating.py`
- `agent/experience_memory/tool_schema.py`
- `agent/experience_memory/engine.py`

Create these tests:

- `tests/agent/test_experience_memory_migrations.py`
- `tests/agent/test_experience_memory_privacy.py`
- `tests/agent/test_experience_memory_store.py`
- `tests/agent/test_experience_memory_retrieval.py`
- `tests/agent/test_experience_memory_engine.py`
- `tests/run_agent/test_experience_memory_init.py`
- `tests/run_agent/test_experience_memory_tool_routing.py`
- `tests/hermes_cli/test_experience_memory_tools_config.py`

Add focused assertions to this existing test file:

- `tests/test_toolsets.py`

## Exact Files To Modify

Modify only these runtime files for the MVP:

- `hermes_cli/config.py`
- `hermes_cli/tools_config.py`
- `toolsets.py`
- `agent/agent_init.py`
- `agent/tool_executor.py`
- `agent/agent_runtime_helpers.py`
- `model_tools.py`
- `run_agent.py`

Do not modify these files in the MVP:

- `tools/registry.py`
- `toolsets.py` `_HERMES_CORE_TOOLS`, except to verify it remains unchanged
- `tools/memory_tool.py`
- `agent/memory_manager.py`
- `agent/conversation_loop.py`
- `agent/conversation_compression.py`
- `agent/background_review.py`
- `plugins/memory/hindsight/__init__.py`
- `tools/skill_manager_tool.py`

## Config Changes

Add `experience_memory` to `DEFAULT_CONFIG` in `hermes_cli/config.py` with
disabled defaults:

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
    max_queue_size: 200
    retry_limit: 3
  projections:
    enabled: false
```

Do not bump `_config_version`. Adding a new key is handled by the existing
deep-merge behavior.

Extend `ensure_hermes_home()` so it creates:

```text
$HERMES_HOME/experience/
```

The MVP should not add `auxiliary.experience_memory`. That belongs to the later
extraction phase.

## Toolset Changes

Add a logical toolset in `toolsets.py`:

```python
"experience_memory": {
    "description": "Dynamic Experience Memory Engine tool",
    "tools": [],
    "includes": [],
}
```

Add it to `hermes_cli/tools_config.py::CONFIGURABLE_TOOLSETS` so users can
enable it through `hermes tools`.

Because the toolset has no registry-backed tools, normal registry resolution
will not expose a schema. Dynamic injection must come from `agent/agent_init.py`
after the `ExperienceMemoryEngine` is initialized.

## Tool Gating Rules

Implement a helper in `agent/experience_memory/tool_gating.py`:

```python
def experience_memory_tools_allowed(enabled_toolsets, disabled_toolsets) -> bool:
    ...
```

The helper must use `toolsets.validate_toolset()` and `toolsets.resolve_toolset()`
where possible, but it also must treat literal toolset names as meaningful
because `experience_memory` resolves to no registry tool names.

Rules:

- If `disabled_toolsets` contains `all` or `*`, return false.
- If `disabled_toolsets` contains literal `experience_memory`, return false.
- If `disabled_toolsets` contains literal `memory`, return false.
- If any disabled composite resolves to the registry tool name `memory`,
  return false.
- If `enabled_toolsets is None`, return true unless disabled rules blocked it.
- If `enabled_toolsets` is empty, return false.
- If `enabled_toolsets` contains `all` or `*`, return true unless disabled
  rules blocked it.
- If `enabled_toolsets` contains literal `experience_memory`, return true unless
  disabled rules blocked it.
- If `enabled_toolsets` contains literal `memory`, return true unless disabled
  rules blocked it.
- If any enabled composite resolves to the registry tool name `memory`, return
  true unless disabled rules blocked it.
- Otherwise return false.

Tests must include:

- `enabled_toolsets=None`, `disabled_toolsets=[]` allows.
- `enabled_toolsets=[]` blocks.
- `enabled_toolsets=["web"]` blocks.
- `enabled_toolsets=["experience_memory"]` allows.
- `enabled_toolsets=["memory"]` allows.
- `enabled_toolsets=["all"]` allows.
- `enabled_toolsets=["*"]` allows.
- `enabled_toolsets=["hermes-cli"]` allows because the composite includes
  `memory`.
- `disabled_toolsets=["experience_memory"]` blocks even when enabled.
- `disabled_toolsets=["memory"]` blocks even when enabled.
- `disabled_toolsets=["all"]` blocks.
- `disabled_toolsets=["*"]` blocks.
- `disabled_toolsets=["hermes-cli"]` blocks because the composite includes
  `memory`.

## Engine Initialization

Modify `agent/agent_init.py` after `agent._session_db` and config load are
available:

1. Set `agent._experience_memory = None`.
2. Set `agent._experience_memory_tool_names = set()`.
3. If `skip_memory` is true, leave EME disabled.
4. Read `experience_memory` config.
5. If `enabled` is false or `mode` is `off`, leave EME disabled.
6. Instantiate `ExperienceMemoryEngine` when enabled.
7. Pass profile, workspace, platform, session, parent session, user, chat,
   thread, and gateway session metadata.
8. Open `$HERMES_HOME/experience/experience.db`.
9. Apply migrations.
10. Append dynamic tool schemas only when `tools_enabled` is true and
    `experience_memory_tools_allowed(...)` returns true.
11. Add dynamic tool names to `agent.valid_tool_names`.
12. Add dynamic tool names to `agent._experience_memory_tool_names`.

Initialization failure must log a warning and set:

```python
agent._experience_memory = None
agent._experience_memory_tool_names = set()
```

It must not fail agent startup.

Modify `run_agent.py`:

- `shutdown_memory_provider(messages)` should call
  `agent._experience_memory.on_session_end(messages)` and
  `agent._experience_memory.shutdown()` if EME exists.
- `commit_memory_session(messages)` should call
  `agent._experience_memory.on_session_end(messages)` without closing the DB.

Both calls must be best-effort and must not mask memory-manager or context-engine
shutdown behavior.

## Dynamic Tool Routing

Modify `agent/tool_executor.py` in the sequential execution path:

- Route names in `agent._experience_memory_tool_names` to
  `agent._experience_memory.handle_tool_call(...)`.
- Place this branch before context-engine, memory-manager, and registry
  fallback dispatch.
- Return JSON strings for all failures.

Modify `agent/agent_runtime_helpers.py::invoke_tool`:

- Add the same dynamic EME routing before context-engine, memory-manager,
  plugin, and `model_tools.handle_function_call(...)` fallback dispatch.
- This is required because concurrent tool execution forwards through
  `_invoke_tool()` into `invoke_tool`.

Modify `model_tools.py::handle_function_call`:

- Add an explicit guard for `function_name == "experience_memory"`.
- Return a JSON error explaining that `experience_memory` is an agent-level
  dynamic tool and is not available through the registry dispatcher.
- Do not add `experience_memory` to `TOOL_TO_TOOLSET_MAP`.

## Tool Schema

Implement `agent/experience_memory/tool_schema.py`.

Expose exactly one tool:

```text
experience_memory
```

Supported actions:

- `status`
- `record`
- `recall`
- `correct`

The schema must be strict enough to prevent ambiguous writes:

- `status` needs no content fields.
- `record` requires `kind`, `title`, and `body`.
- `record.kind` must be one of the MVP kinds.
- `recall` requires `query`.
- `recall.limit` defaults to `max_recall_items` and is capped by config.
- `correct` requires `record_id`, `operation`, and `reason`.
- `correct.operation` is one of `retract`, `tombstone`, `supersede`, or `scrub`.
- `supersede` requires replacement `title` and `body`.
- `scrub` requires `record_id` and `reason`; it irreversibly redacts the
  canonical title/body/evidence/tags payload and removes the matching FTS row
  inside the same transaction. It preserves only audit metadata needed to prove
  the deletion happened.

The schema should not offer `project`. Projection is not in the MVP.

## Store API

Implement `ExperienceStore` in `agent/experience_memory/store.py` with only the
methods needed for the MVP:

```python
class ExperienceStore:
    def open(self) -> None: ...
    def migrate(self) -> None: ...
    def status(self) -> dict: ...
    def append_event(self, event: ExperienceEvent) -> str: ...
    def record(self, record: ExperienceRecord) -> str: ...
    def recall(self, query: ExperienceQuery) -> list[ExperienceResult]: ...
    def correct(self, correction: ExperienceCorrection) -> dict: ...
    def scrub_record(self, record_id: str, reason: str, scope: ExperienceScope) -> dict: ...
    def queue_counts(self) -> dict: ...
    def close(self) -> None: ...
```

Store responsibilities:

- Create parent directories with `get_hermes_home()`.
- Use `display_hermes_home()` only in user-facing status strings or schema
  descriptions.
- Apply `hermes_state.apply_wal_with_fallback`.
- Set `PRAGMA busy_timeout`.
- Set `PRAGMA foreign_keys=ON`.
- Use explicit transactions for event plus record plus FTS writes.
- Maintain `experience_fts` directly in store methods.
- Keep writes idempotent with `content_hash` and event idempotency keys.
- Reject invalid scope before writing.
- Exclude `deleted_at IS NOT NULL`, `status='retracted'`, and
  `status='tombstoned'` records from recall.
- For destructive privacy deletion, `scrub_record` must verify current-scope
  visibility, append an audit correction, set `status='scrubbed'`, replace
  `title`, `body`, `applies_when_json`, `does_not_apply_when_json`,
  `evidence_json`, and `tags_json` with safe redacted placeholders, set
  `deleted_at`, and delete the matching `experience_fts` row in the same
  transaction. It must not leave searchable deleted content in FTS.

## Privacy And Scope Helpers

Implement `agent/experience_memory/privacy.py`.

Required MVP objects and helpers:

- `ExperienceScope`
- `normalize_scope(...)`
- `validate_scope(scope)`
- `scope_identity_tuple(scope)`
- `scope_sql_predicate(scope)`
- `scope_sql_params(scope)`
- `hash_gateway_identifier(value, salt)`
- `get_or_create_profile_salt(store)`
- `redact_text(text)`
- `redact_payload(payload)`
- `content_hash_for_record(...)`

Scope rules:

- `profile_id` must always be non-empty.
- Non-applicable scope fields must be `""`, not `NULL`.
- `scope_level="profile"` requires no gateway hashes.
- `scope_level="workspace"` requires `workspace_id`.
- `scope_level="platform_user"` requires `user_scope_hash`.
- `scope_level="chat"` requires `user_scope_hash` and `chat_scope_hash`.
- `scope_level="thread"` requires user, chat, and thread hashes.
- `scope_level="session"` requires `session_id`.

Gateway identifiers must be hashed with a profile-local salt stored in
`experience_meta`. The salt is local to the active Hermes profile.

Redaction can be conservative in the MVP. It must at least redact obvious API
keys, bearer tokens, password assignments, private key blocks, and Hermes `.env`
style secrets before persistence.

## Retrieval

Implement `agent/experience_memory/retrieval.py`.

MVP retrieval is local FTS only:

- Query `experience_fts` with sanitized MATCH input.
- Apply scope predicates in the FTS query before ordering and limiting.
- Join back to `experience_records` only after scope filtering.
- Rank with BM25 plus small deterministic boosts for confidence and recency.
- Enforce `limit`.
- Enforce `recall_token_budget` by truncating result bodies deterministically.
- Return JSON-serializable results with `record_id`, `kind`, `title`, `body`,
  `confidence`, `status`, `tags`, and optional evidence.

If the query is invalid FTS syntax after sanitization, return an empty list with
a non-fatal warning in the tool JSON. Do not throw out of the tool handler.

## Engine API

Implement `ExperienceMemoryEngine` in `agent/experience_memory/engine.py`.

MVP methods:

```python
class ExperienceMemoryEngine:
    def initialize(self, session_id: str, **kwargs) -> None: ...
    def get_tool_schemas(self) -> list[dict]: ...
    def handle_tool_call(self, name: str, args: dict, **kwargs) -> str: ...
    def on_session_end(self, messages: list) -> None: ...
    def shutdown(self) -> None: ...
```

Optional no-op methods may be added for future lifecycle compatibility, but they
must not write automatic extraction data in the MVP:

```python
def on_turn_start(...): ...
def sync_turn(...): ...
def on_pre_compress(...): ...
def on_memory_write(...): ...
def on_delegation(...): ...
```

Tool handler behavior:

- Always return a JSON string.
- Validate `name == "experience_memory"`.
- Validate action-specific args.
- Apply current engine scope. The model cannot select another profile or raw
  gateway ID.
- Current engine scope must be derived during `ExperienceMemoryEngine.initialize`
  from profile, workspace, platform, user, chat, thread, gateway session, and
  session metadata. Gateway sessions default to the narrowest safe scope:
  `thread` when thread metadata exists, otherwise `chat` when chat metadata
  exists, otherwise `platform_user` when user metadata exists, otherwise
  `session`. CLI/local sessions default to profile or workspace scope according
  to config. The tool handler must never accept caller-provided raw gateway IDs
  or profile IDs.
- On `status`, call `store.status()` and include queue counts.
- On `record`, append a `manual_record` event and an `experience_records` row.
- On `recall`, call local FTS retrieval.
- On `correct`, append a `manual_correction` event and update or supersede the
  target record if it is visible in the current scope.
- On `correct` with `operation='scrub'`, call `store.scrub_record(...)`, remove
  the FTS row, and return a JSON result that confirms the record is no longer
  retrievable without echoing scrubbed content.
- On validation failure, return `{"ok": false, "error": "..."}`
  without raising.

## TDD Work Plan

Follow this loop for every task:

1. Add or update one failing test.
2. Run that single test file.
3. Implement only enough code to pass it.
4. Run the adjacent focused tests.
5. Refactor only inside the files touched by the task.
6. Move to the next task.

Do not batch several phases before running tests. Each phase below is intended
to be split into one to three commits.

## Phase 1: Config And Toolset Gates

Tests first:

- Add `tests/hermes_cli/test_experience_memory_tools_config.py`.
- Add assertions to `tests/test_toolsets.py`.
- Add direct unit tests for
  `agent.experience_memory.tool_gating.experience_memory_tools_allowed`.

Implementation:

- Add `experience_memory` defaults in `hermes_cli/config.py`.
- Add `$HERMES_HOME/experience/` directory creation.
- Add the logical empty `experience_memory` toolset in `toolsets.py`.
- Add the `hermes tools` configurator entry in `hermes_cli/tools_config.py`.
- Implement `tool_gating.py`.

Acceptance:

- Config defaults keep EME disabled.
- `experience_memory` is a valid toolset.
- No registry tool named `experience_memory` appears.
- All disabled `all`, `*`, and composite cases suppress dynamic tool injection.

Commands:

```bash
scripts/run_tests.sh tests/hermes_cli/test_experience_memory_tools_config.py
scripts/run_tests.sh tests/test_toolsets.py
scripts/run_tests.sh tests/agent/test_experience_memory_engine.py -- -k tool_gating
```

## Phase 2: Migrations And Store Opening

Tests first:

- `tests/agent/test_experience_memory_migrations.py`
- First slice of `tests/agent/test_experience_memory_store.py`

Implementation:

- Create `schema.py` with `SCHEMA_VERSION = 1` and `SCHEMA_SQL`.
- Create `migrations.py` with idempotent migration application.
- Create `store.py` with `open()`, `migrate()`, `status()`, and `close()`.
- Use `hermes_state.apply_wal_with_fallback`.

Acceptance:

- Fresh store creates `$HERMES_HOME/experience/experience.db`.
- Schema version is `1`.
- Reopening the store is idempotent.
- WAL fallback path is covered by monkeypatch.
- Foreign keys and busy timeout are enabled.
- Queue placeholder tables exist.

Commands:

```bash
scripts/run_tests.sh tests/agent/test_experience_memory_migrations.py
scripts/run_tests.sh tests/agent/test_experience_memory_store.py -- -k "open or migrate or status"
```

## Phase 3: Privacy And Scope

Tests first:

- `tests/agent/test_experience_memory_privacy.py`
- Scope-specific cases in `tests/agent/test_experience_memory_store.py`

Implementation:

- Create `models.py` dataclasses or typed dictionaries.
- Implement `ExperienceScope` and validation in `privacy.py`.
- Implement profile-local salt storage in `experience_meta`.
- Implement gateway ID hashing.
- Implement conservative redaction.
- Wire scope validation into store writes.

Acceptance:

- Profile scope writes succeed.
- Gateway chat scope without user and chat hashes is rejected.
- Thread scope without user, chat, and thread hashes is rejected.
- Session scope without session ID is rejected.
- Gateway hashes are stable within a profile and different across profile
  salts.
- Redaction runs before payload persistence.

Commands:

```bash
scripts/run_tests.sh tests/agent/test_experience_memory_privacy.py
scripts/run_tests.sh tests/agent/test_experience_memory_store.py -- -k "scope or redact"
```

## Phase 4: Manual Record, FTS Recall, And Correction

Tests first:

- Finish `tests/agent/test_experience_memory_store.py`.
- Add `tests/agent/test_experience_memory_retrieval.py`.

Implementation:

- Implement `append_event`.
- Implement `record`.
- Implement explicit FTS maintenance for records.
- Implement `recall`.
- Implement `correct`.
- Implement queue count reporting.

Acceptance:

- `record` writes one source event, one record, and one FTS row in one
  transaction.
- Duplicate idempotency keys do not create duplicate records.
- FTS recall returns same-scope records.
- FTS recall excludes other profiles.
- FTS recall excludes other gateway user/chat scopes.
- FTS recall excludes tombstoned and retracted records.
- `correct` can retract a record.
- `correct` can tombstone a record.
- `correct` can supersede a record and create the replacement.
- `correct` can scrub a record, redacting canonical content and deleting the
  corresponding FTS row in one transaction.
- Corrections append audit rows.
- Scrubbed records are not returned by recall, and their old title/body no
  longer exists in `experience_records` or `experience_fts`.
- Queue placeholder counts are visible in status.

Commands:

```bash
scripts/run_tests.sh tests/agent/test_experience_memory_store.py
scripts/run_tests.sh tests/agent/test_experience_memory_retrieval.py
```

## Phase 5: Engine, Dynamic Tool, And Init

Tests first:

- `tests/agent/test_experience_memory_engine.py`
- `tests/run_agent/test_experience_memory_init.py`

Implementation:

- Implement `tool_schema.py`.
- Implement `engine.py`.
- Modify `agent/agent_init.py` to initialize EME when enabled.
- Append dynamic schemas when config and toolset gating allow.
- Modify `run_agent.py` session-end and shutdown hooks.

Acceptance:

- Default config creates no EME store and no EME tool.
- `skip_memory=True` disables EME even when config enables it.
- Enabled config initializes the store.
- Enabled config plus allowed toolset appends exactly one dynamic schema.
- Disabled toolsets suppress schema injection.
- Gateway metadata initializes the current engine scope to same user/chat/thread
  visibility, not profile-wide visibility.
- Records created by a gateway-scoped engine are not visible to a second engine
  initialized with a different user/chat/thread scope.
- Initialization failure logs and leaves the agent usable.
- `status`, `record`, `recall`, and `correct` return JSON strings.

Commands:

```bash
scripts/run_tests.sh tests/agent/test_experience_memory_engine.py
scripts/run_tests.sh tests/run_agent/test_experience_memory_init.py
```

## Phase 6: Routing And Registry Guard

Tests first:

- `tests/run_agent/test_experience_memory_tool_routing.py`

Implementation:

- Modify `agent/tool_executor.py` sequential routing.
- Modify `agent/agent_runtime_helpers.py::invoke_tool` concurrent routing.
- Modify `model_tools.py::handle_function_call` direct registry guard.

Acceptance:

- Sequential tool execution calls `ExperienceMemoryEngine.handle_tool_call`.
- Concurrent tool execution calls `ExperienceMemoryEngine.handle_tool_call`.
- Routing happens before context-engine, memory-manager, plugin, and registry
  fallback dispatch.
- Direct `model_tools.handle_function_call("experience_memory", ...)` returns a
  scoped JSON error.
- No test needs `tools.registry` to know about `experience_memory`.

Commands:

```bash
scripts/run_tests.sh tests/run_agent/test_experience_memory_tool_routing.py
scripts/run_tests.sh tests/agent/test_experience_memory_engine.py
```

## Failure Behavior

Initialization failure:

- Log a warning.
- Set EME attributes to disabled values.
- Continue agent startup.

Migration failure:

- Roll back the transaction.
- Leave the previous database intact.
- Log the failure.
- Disable EME for the process.

SQLite busy or locked:

- Use configured `busy_timeout`.
- Retry bounded writes according to `store.retry_writes`.
- Return JSON tool errors after retries are exhausted.
- Do not block normal agent operation.

FTS unavailable:

- Treat migration as failed because recall requires local FTS for the MVP.
- Disable EME and report the cause through logs and future `status` paths.

Record validation failure:

- Return JSON with `ok: false`.
- Do not write partial events.

Recall failure:

- Return JSON with `ok: false`, `results: []`, and a redacted error.
- Do not raise into the agent loop.

Correction failure:

- If the record is not visible in current scope, return `not_found`.
- If the operation is invalid, return validation error.
- Do not partially update the record without a correction event.

Shutdown failure:

- Log and continue existing memory-manager and context-engine shutdown paths.

Queue behavior:

- Queue placeholder tables are migrated and counted.
- No worker claims, retries, or external calls happen in the MVP.
- If future code accidentally enqueues beyond `max_queue_size`, explicit manual
  correction events must take priority over placeholder extraction work.

## Verification Commands

Run focused tests after each phase. Before merging the MVP, run:

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

Then run the broader impacted areas:

```bash
scripts/run_tests.sh tests/agent/ tests/run_agent/test_run_agent.py tests/test_model_tools.py
scripts/run_tests.sh tests/hermes_cli/test_tools_config.py tests/hermes_cli/test_tui_resume_flow.py
```

If time permits before merge, run the full suite:

```bash
scripts/run_tests.sh
```

Manual DB verification after an enabled test run:

```bash
sqlite3 "$HERMES_HOME/experience/experience.db" ".tables"
sqlite3 "$HERMES_HOME/experience/experience.db" "select version from experience_schema_version;"
sqlite3 "$HERMES_HOME/experience/experience.db" "select kind, title, status from experience_records order by created_at desc limit 5;"
```

## Live Hermes Smoke Test

After implementation, run a live smoke test against an isolated Hermes home that
copies the active Hermes profile's model config and secrets. Do not hard-code
`$HOME/.hermes`; resolve the source through `hermes_constants.get_hermes_home()`
before setting `HERMES_HOME` for the smoke profile:

```bash
SMOKE_HOME="$(mktemp -d)"
SOURCE_HOME="$(python - <<'PY'
from hermes_constants import get_hermes_home
print(get_hermes_home())
PY
)"
cp "$SOURCE_HOME/config.yaml" "$SMOKE_HOME/config.yaml"
[ -f "$SOURCE_HOME/.env" ] && cp "$SOURCE_HOME/.env" "$SMOKE_HOME/.env"

HERMES_HOME="$SMOKE_HOME" python - <<'PY'
from hermes_cli.config import load_config, save_config

cfg = load_config()
cfg.setdefault("experience_memory", {})
cfg["experience_memory"]["enabled"] = True
cfg["experience_memory"]["mode"] = "shadow"
cfg["experience_memory"]["tools_enabled"] = True
cfg["experience_memory"]["prefetch_enabled"] = False
save_config(cfg)
PY

HERMES_HOME="$SMOKE_HOME" hermes --toolsets experience_memory -z \
  "Use the experience_memory tool to do four things in order: status, record a case titled 'EME smoke test' with body 'Manual local SQLite record works', recall 'EME smoke test', then report whether recall returned the record."
```

If `hermes` is not on `PATH`, replace the final command with the repo's normal
Python entry point for the CLI.

Verify the smoke database:

```bash
HERMES_HOME="$SMOKE_HOME" sqlite3 "$SMOKE_HOME/experience/experience.db" \
  "select kind, title, status from experience_records where title = 'EME smoke test';"
```

## Post-MVP Follow-Ups

Only after the MVP is stable:

- Add automatic completed-turn capture.
- Add `agent/conversation_loop.py` prefetch injection.
- Add `<experience-memory-context>` scrubbing to `agent/memory_manager.py`.
- Add compression and session-switch lifecycle hooks.
- Add local deterministic extraction from queued events.
- Add `auxiliary.experience_memory` for policy-gated extraction.
- Add projection workers and adapters.
- Add CLI commands under `hermes experience`.
- Split `experience_records` into the architecture's specialized tables if the
  generic MVP table becomes limiting.
