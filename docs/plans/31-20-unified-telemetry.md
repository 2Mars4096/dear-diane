# 31-20: Unified Telemetry & Analytics

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Unify tracking across chat conversations, workflow runs, and projects into a single telemetry layer with per-turn timing, project-level cost rollup, cross-session analytics, and a query/export surface — so every interaction is measurable for analysis.

## Problem

DAN has rich but fragmented tracking:

| Dimension | Workflows | Chat / Concierge |
|-----------|-----------|-------------------|
| **Tokens** | Per-node 10-field breakdown (`TokenBreakdown`) | Per-message only; no rollup |
| **Cost** | Per-node + run total + budget enforcement | Per-message `estimated_cost`; `/cost` session total |
| **Timing** | `elapsed_seconds`, per-node timings | Nothing — no per-turn latency |
| **Quality** | `NodeOutcome`, retry count, schema validity, workflow quality | Nothing |
| **Analytics** | Dashboard + waste detection + optimization recs | Nothing |
| **Project rollup** | Linked via `linked_run_ids` | No cost/token/time aggregation per project |
| **Cross-session** | Run history panel | No trends over time |
| **Export** | In-memory; no structured export | Nothing |

Observable gaps:
- "How much did my Kaggle project cost this week?" — unanswerable
- "What's the average response time on Telegram?" — unmeasured
- "Which model is most cost-effective for my use cases?" — learning trackers exist but are off by default and siloed
- "Show me a usage report for the last 7 days" — no aggregation layer
- Per-turn latency is never recorded: `TaskTurn` stores `timestamp` but not `duration_ms`
- `CostTracker` (workflows) and `ChatMessage.estimated_cost` (chat) are disconnected stores
- Opt-in trackers (`PromptTracker`, `ModelOutcomeTracker`, `TopologyOutcomeTracker`) write to memory kernel but nothing reads them for cross-cutting analytics

## Design

### Core Principle: One Event, Many Views

Every LLM interaction — whether a chat turn, a workflow node execution, or a guard check — emits a single `TelemetryEvent` to a unified `TelemetryStore`. Aggregation queries (per-project, per-surface, per-model, per-day) are views over this flat event stream. No dual-write, no sync.

### TelemetryEvent Model

```python
class TelemetryEvent(BaseModel):
    id: str                          # unique event ID
    timestamp: datetime              # when the event started
    event_type: Literal[
        "chat_turn",                 # concierge chat turn (LLM-backed)
        "fast_command",              # slash command (no LLM, 0 tokens)
        "workflow_run",              # workflow run summary
        "workflow_node",             # workflow engine node execution
        "guard_check",               # guard pipeline check
        "classification",            # intent classification
        "memory_retrieval",          # memory kernel retrieval
        "tool_call",                 # capability tool invocation
    ]

    # Scope
    project_id: str | None = None
    task_id: str | None = None
    surface: str | None = None       # "cli", "telegram", "whatsapp", "editor"
    session_id: str | None = None    # external_id / surface session

    # Correlation
    parent_event_id: str | None = None  # links child events to parent turn
    run_id: str | None = None           # workflow run ID
    graph_id: str | None = None         # workflow graph ID

    # Identity
    model: str | None = None
    node_id: str | None = None       # workflow node ID
    intent: str | None = None        # classified intent category
    tool_name: str | None = None     # for tool_call events

    # Metrics
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    duration_ms: float = 0.0         # wall-clock time for this event

    # Quality
    success: bool = True
    retry_count: int = 0
    guard_action: str | None = None  # "proceed", "clarify", "short_circuit", etc.

    # Context (extensible — stored as JSON TEXT in SQLite)
    metadata: dict[str, Any] = {}
```

### TelemetryStore

Append-only store with indexed queries. Two backends:

1. **SQLite** (default, `~/.dan/telemetry.db`) — single file, no deps, WAL mode for concurrent reads. Schema: one `events` table matching `TelemetryEvent` fields, with indexes on `(project_id, timestamp)`, `(surface, timestamp)`, `(model, timestamp)`, `(event_type, timestamp)`, `(run_id)`, `(parent_event_id)`. The `metadata` column is `TEXT` storing JSON. The `group_key` column is not stored — it's computed in aggregate queries.

2. **In-memory** (for tests and short-lived sessions) — list with linear scan.

```python
class TelemetryStore:
    async def record(self, event: TelemetryEvent) -> None      # fire-and-forget via asyncio.to_thread
    async def query(self, filters: TelemetryQuery) -> list[TelemetryEvent]
    async def aggregate(self, filters: TelemetryQuery, group_by: list[str]) -> list[AggregateRow]
    async def export_jsonl(self, filters: TelemetryQuery, path: Path) -> int
    async def export_csv(self, filters: TelemetryQuery, path: Path) -> int
```

**SQLite async strategy:** All SQLite operations are blocking I/O. Wrap each in `asyncio.to_thread()` so the event loop is never blocked. For `record()`, an alternative is a background writer queue (bounded `asyncio.Queue` + consumer task) for higher throughput — but `to_thread` is simpler and sufficient given the low event rate (~1-5 events per user turn).

### TelemetryQuery

```python
class TelemetryQuery(BaseModel):
    project_id: str | None = None
    task_id: str | None = None       # drill into specific task
    surface: str | None = None
    model: str | None = None
    event_type: str | None = None
    intent: str | None = None        # filter by classified intent
    tool_name: str | None = None     # filter by tool
    run_id: str | None = None        # filter by workflow run
    parent_event_id: str | None = None  # find child events of a turn
    since: datetime | None = None    # inclusive
    until: datetime | None = None    # exclusive
    limit: int = 1000
```

### AggregateRow

```python
class AggregateRow(BaseModel):
    group_key: dict[str, str]        # e.g. {"project_id": "abc", "model": "gpt-4o"}
    count: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    total_duration_ms: float = 0.0
    avg_duration_ms: float = 0.0
    min_duration_ms: float = 0.0
    max_duration_ms: float = 0.0     # needed for SLA / P95-style latency analysis
    success_rate: float = 0.0
```

### Emission Sites

| Site | Event Type | What's New |
|------|------------|------------|
| `Concierge.process()` (outer method, `try/finally`) | `chat_turn` | **NEW**: wrap in timer; record `duration_ms`, tokens, cost, intent, project_id, surface. Emit in `finally` so all exit paths are covered. |
| `Concierge._try_fast_command()` | `fast_command` | **NEW**: separate event type for slash commands (0 tokens, 0 cost). Prevents inflating turn counts / skewing latency averages. |
| `RunManager._enrich_and_persist()` | `workflow_node` | **Bridge**: iterate `record.result.metadata` (lines 819-836) to emit per-node events with model, tokens, cost, duration. Per-node data is only available here — not at `NODE_COMPLETED` engine event time. |
| `RunManager._enrich_and_persist()` | `workflow_run` | **Bridge**: emit summary event with `run_id`, `graph_id`, total tokens, cost, `elapsed_seconds`. Set `parent_event_id` to the originating `chat_turn` event if triggered from concierge. |
| `guard_classification/understanding/relevance` | `guard_check` | **NEW**: record guard action, duration, notes. Set `parent_event_id` to the enclosing `chat_turn` event. |
| `classify_intent_llm()` | `classification` | **NEW**: record classification confidence, intent, duration |
| `MemoryKernel.retrieve()` | `memory_retrieval` | **NEW**: record retrieval duration, result count |
| Capability tool dispatch | `tool_call` | **NEW**: record tool name, duration, success |

#### Scope boundaries

- **Editor ChatPanel excluded.** `ChatManager.send_message()` / `send_message_with_tools()` is called directly from the editor, bypassing the concierge. These turns are NOT captured. Adding a second emission point in `ChatManager` is a future extension (low priority — editor already has its own usage/cost UI).
- **Scheduled tasks included.** Scheduled tasks synthesize a `SurfaceMessage` and flow through `process()` normally. Tag with `metadata.source = "scheduled"` for filtering.

### Double-Counting Prevention

When a concierge turn triggers a workflow run, both a `chat_turn` event and `workflow_node`/`workflow_run` events are emitted. The `chat_turn.estimated_cost` reflects only the concierge-level LLM call (classification, solver, response generation), NOT the workflow execution cost. This is correct — each cost source is recorded exactly once.

**Rule:** For total cost/token aggregation, sum ALL event types. For concierge-only analysis, filter to `event_type in ("chat_turn", "fast_command")`. For workflow-only analysis, filter to `event_type in ("workflow_node", "workflow_run")`.

The `parent_event_id` field links `workflow_run` events back to the `chat_turn` that triggered them, enabling drill-down: "this turn cost $0.02 for the chat LLM call + triggered workflow run X which cost $0.15."

### Project-Level Rollup

`TelemetryStore.aggregate(project_id="X", group_by=["model"])` returns total tokens, cost, duration, and success rate per model for project X. No separate accumulator needed — it's a query. The `parent_event_id` linkage enables hierarchical drill-down per turn.

### Chat Commands

- **`/analytics [project] [--since 7d] [--by model|surface|day]`** — aggregated usage report. Default: last 7 days, grouped by day.
  ```
  Usage Report (last 7 days)
  ─────────────────────────
  Total tokens: 245,831 (in: 198,420 / out: 47,411)
  Total cost: $1.42
  Avg response time: 3.2s
  Turns: 87 | Runs: 12 | Tools: 34

  By model:
    gpt-4o-mini   142,000 tok  $0.31  2.1s avg
    claude-sonnet  103,831 tok  $1.11  4.8s avg
  ```

- **`/analytics project Kaggle --since 30d`** — project-specific report.

- **`/analytics export [--since 30d] [--format jsonl|csv]`** — export raw events for external analysis (Jupyter, pandas, etc.).

### Env Vars

- `DAN_TELEMETRY=1` — enable telemetry recording (default: `1`, on by default)
- `DAN_TELEMETRY_DB` — path to SQLite DB (default: `~/.dan/telemetry.db`)
- `DAN_TELEMETRY_RETENTION_DAYS` — auto-prune events older than N days (default: `90`)

### Integration with Existing Trackers

The existing opt-in trackers (`PromptTracker`, `ModelOutcomeTracker`, `TopologyOutcomeTracker`, `GenerationStats`, `MutationMetrics`) remain as-is — they serve specialized learning/optimization purposes. `TelemetryStore` is the operational analytics layer; it doesn't replace them but provides the cross-cutting aggregation they lack.

Existing `ChatMessage.estimated_cost` and `CostTracker` continue to function. `TelemetryStore` is a parallel write — events are recorded at the same sites where cost/tokens are already tracked, with the addition of timing and scope metadata.

## Tasks

- [x] 1. **TelemetryEvent model + TelemetryStore**
  - [x] 1-1. Create `src/dan/server/telemetry.py` with `TelemetryEvent`, `TelemetryQuery`, `AggregateRow` Pydantic models
  - [x] 1-2. Implement `InMemoryTelemetryStore` (list-backed, for tests) — all methods async
  - [x] 1-3. Implement `SQLiteTelemetryStore` with WAL mode, indexed schema, `record()`, `query()`, `aggregate()`, `export_jsonl()`, `export_csv()` — all via `asyncio.to_thread()` for non-blocking I/O; `metadata` stored as JSON TEXT column
  - [x] 1-4. Factory function `get_telemetry_store()` respecting `DAN_TELEMETRY` and `DAN_TELEMETRY_DB` env vars
  - [x] 1-5. Auto-prune on startup: delete events older than `DAN_TELEMETRY_RETENTION_DAYS`
  - [x] 1-6. `NullTelemetryStore` no-op implementation when `DAN_TELEMETRY=0` — avoids `if store:` checks everywhere
  - [x] 1-7. Tests: CRUD, query filters (including `task_id`, `run_id`, `parent_event_id`), aggregation (group by project, model, surface, day; verify min/max duration), export (jsonl + csv), pruning — 41 tests

- [x] 2. **Chat turn telemetry emission**
  - [x] 2-1. In `Concierge.process()` outer method: wrap the `_process_inner()` iteration in a `try/finally` timer. Generate `turn_event_id` at entry. In `finally`, emit `chat_turn` event (or `fast_command` for slash commands) with all scope fields. Both code paths (messaging-surface and reassurance-timer) covered.
  - [x] 2-2. Accumulate token usage and cost from yielded `ChatCompleteEvent`s (summed across multiple events)
  - [x] 2-3. Store `turn_event_id` on `self._current_turn_event_id` so child emitters (guards, classification) can reference it as `parent_event_id`
  - [x] 2-4. Thread `TelemetryStore` into `Concierge` via constructor (`telemetry_store` kwarg)
  - [x] 2-5. Emit `fast_command` for slash-command paths via `self._telem_is_fast_command` flag
  - [x] 2-6. `_last_context` cached for project_id / task_id extraction

- [x] 3. **Workflow telemetry bridge**
  - [x] 3-1. In `RunManager._enrich_and_persist()`: emit `workflow_node` events per node with `run_id`, `graph_id`, `node_id`, model, tokens, cost
  - [x] 3-2. Emit `workflow_run` summary event with total metrics. `parent_event_id` from `goal_context.get("turn_event_id")`
  - [x] 3-3. Link to project via `RunRecord.goal_context["project_id"]`
  - [x] 3-4. `TelemetryStore` threaded into `RunManager` via constructor
  - [x] 3-5. `_emit_workflow_telemetry` method with fire-and-forget safety

- [x] 4. **Guard, classification, and tool telemetry**
  - [x] 4-1. `classify_intent_llm()` wrapped with timer, emits `classification` event (confidence, intent, duration)
  - [x] 4-2. All three guards wrapped: guard_classification, guard_understanding (solver + goal orch paths), guard_response_relevance — each emits `guard_check` with action, success, metadata
  - [ ] 4-3. Tool dispatch deferred — no single dispatch point exists; capability tools are distributed across handler classes
  - [x] 4-4. Memory retrieval wrapped with timer, emits `memory_retrieval` event
  - [x] 4-5. `_emit_telemetry_event` fire-and-forget helper on Concierge

- [x] 5. **`/analytics` command + project rollup**
  - [x] 5-1. `/analytics` registered in command registry with `project` and `export` subcommands
  - [x] 5-2. Default report: last 7 days, total tokens/cost/turns, avg response time (from `chat_turn` only)
  - [x] 5-3. `--by model|surface|day|intent` grouping options
  - [x] 5-4. `--since Nd` / `--since YYYY-MM-DD` time range
  - [x] 5-5. Project-specific report via fuzzy-match on `project_store.list_projects()`
  - [x] 5-6. `/analytics export --format jsonl|csv` to `~/.dan/exports/`
  - [x] 5-7. `_handle_analytics_command`, `_analytics_default`, `_analytics_project`, `_analytics_export` handler methods

- [x] 6. **Lifecycle wiring**
  - [x] 6-1. `TelemetryStore` created in `app.py` lifespan, passed to `Concierge` and `RunManager`
  - [x] 6-2. Wired into `chat_factory.py` for `dan-chat` CLI (LocalChatRuntime)
  - [x] 6-3. Startup pruning on `app.py` lifespan
  - [x] 6-4. Graceful shutdown: `_telemetry_store.close()` in lifespan teardown
  - [x] 6-5. `build_concierge()` accepts and forwards `telemetry_store` parameter

- [x] 7. **Documentation**
  - [x] 7-1. Updated `docs/architecture.md` — Unified Telemetry section (from planned to implemented)
  - [x] 7-2. Added env vars to `.env.example`
  - [x] 7-3. `/analytics` registered in command registry (auto-included in `/help` and generated docs)
  - [x] 7-4. Updated changelog

## Decisions

- Tool dispatch telemetry (4-3) deferred: no single dispatch point exists. Capability tools are distributed across the `HandlerRegistry` pattern; instrumenting would require wrapping every handler or a proxy around `capability_context`. Low priority since tool calls are already tracked indirectly via the enclosing `chat_turn` event.
- Per-node `duration_ms` in workflow events is set to 0.0 since `_enrich_and_persist()` only has total elapsed time, not per-node timing. The `workflow_run` summary has accurate total duration.
- `_emit_telemetry_event` helper uses `getattr(self, '_telemetry_store', None)` to gracefully handle cases where the attribute doesn't exist on older or test Concierge instances.
- Both `process()` code paths (messaging-surface fast path and reassurance-timer path) have independent telemetry accumulation and emission to ensure coverage regardless of which branch executes.

## Notes

- **SQLite is sufficient** for a single-user personal service. DAN is not multi-tenant — one user, one machine, one DB file. WAL mode handles concurrent reads from analytics queries while the main event loop writes.
- **All store methods are async.** SQLite operations are blocking I/O. Use `asyncio.to_thread()` to avoid blocking the event loop. For `record()`, this adds ~0.1ms overhead per call which is negligible.
- **Append-only with pruning** keeps storage bounded. At ~500 bytes per event, 1000 events/day = ~500KB/day = ~45MB over 90 days. Well within single-file SQLite capacity.
- **No real-time dashboard** in this plan. The `/analytics` command covers immediate needs. A web dashboard (management frontend) is a separate backlog item.
- **Existing `ChatMessage.token_usage` and `CostTracker` are not replaced.** They serve their own consumers (chat store persistence, budget enforcement). Telemetry is a parallel analytical layer.
- **Event emission should be fire-and-forget.** `TelemetryStore.record()` must never block or fail the main pipeline. Wrap in try/except with debug logging.
- **`NullTelemetryStore`** when `DAN_TELEMETRY=0` — a no-op implementation that avoids `if store:` guards scattered throughout the codebase.
- **`DAN_TELEMETRY=1` by default.** Unlike learning trackers which are opt-in, basic telemetry should be on unless explicitly disabled. The overhead is negligible (one SQLite insert per turn).
- **`fast_command` vs `chat_turn` split.** Slash commands produce 0-token, 0-cost events. Mixing them into `chat_turn` inflates turn counts and deflates average latency/cost. Separate event type keeps analytics clean.
- **`metadata` is JSON TEXT in SQLite.** Not indexed, not queryable via SQL. Used for extensible context (guard notes, error details, scheduled task source).
- **Double-counting prevention.** `chat_turn` cost = concierge LLM cost only. `workflow_run` cost = engine execution cost. Total project cost = sum of all event types. `parent_event_id` provides drill-down linkage.
- **Editor ChatPanel excluded.** `ChatManager` is called directly from the editor, bypassing concierge. These turns have their own usage/cost tracking in the editor UI. Adding a `ChatManager` emission point is a future extension.
- Priority order: Tasks 1 + 2 first (core model + chat emission) — immediately useful for "how long does each response take?" and project cost tracking. Tasks 3-4 add workflow + guard coverage. Task 5 adds the query surface. Task 6 wires everything up.
- The `group_by` aggregation in SQLite uses `GROUP BY` directly with `MIN()`, `MAX()`, `AVG()` — no application-level aggregation needed.
- Future extensions: streaming to external analytics (Prometheus metrics, OpenTelemetry spans), web dashboard, cost alerts, model comparison reports, `ChatManager` emission point for editor turns.
