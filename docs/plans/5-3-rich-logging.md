# 5-3: Rich Logging Window

**Parent:** [5-phase-3.5-frontend-design](5-phase-3.5-frontend-design.md)
**Status:** completed
**Goal:** Add rich structured logging to the DAN editor: new backend event types for LLM thinking, tool calls, and code output; a collapsible log panel grouped by node with icons, color coding, click-to-select, and filtering, using a single unified parent `run_id` stream for root and sub-graph events.

## Tasks

- [ ] 1. Backend: Add new event types and typed contract
  - [ ] 1-1. Add `LLM_THINKING`, `TOOL_CALL_STARTED`, `TOOL_CALL_RESULT`, `CODE_OUTPUT`, `INTERMEDIATE_TEXT` to `EventType` enum in `src/dan/engine/events.py`
  - [ ] 1-2. Add `emit_event` to `ExecutionContext` in `src/dan/engine/executor.py` — takes `event_type`, `node_id`, `node_type`, `data`; constructs `EngineEvent` and awaits `event_callback`. Pass `event_callback` and `run_id` from `Engine._make_context` in `src/dan/engine/scheduler.py`
  - [ ] 1-3. Unify run stream in scheduler: in `_run_subgraph`, create sub-state with parent run id (`ExecutionState(sub_graph, run_id=parent_state.run_id)`), so all nested events emit under the parent `run_id` and appear in one WebSocket stream
  - [ ] 1-4. Add sub-graph context tags to emitted event `data` (when available): `graph_key`, `layer_path`, `parent_node_id`; root-level events can omit or set `null`
  - [ ] 1-5. Update `_event_callback` in `src/dan/server/run_manager.py` — include new event types in `record.events` buffer and keep only the latest 500 (rolling window). Ensure `node_statuses` updates remain unchanged (only `NODE_*` lifecycle events)
  - [ ] 1-6. Define event payload contracts in `docs/architecture.md` or inline docstrings: `llm_thinking` (`model`, `prompt_preview?`), `tool_call_started` (`tool_id`, `args?`), `tool_call_result` (`tool_id`, `result?`, `error?`), `code_output` (`stdout`, `stderr`), `intermediate_text` (`text`, `chunk?`), plus optional hierarchy fields (`graph_key`, `layer_path`, `parent_node_id`)

- [ ] 2. Backend: Emit new events from executors
  - [ ] 2-1. **LLMExecutor** (`src/dan/executors/llm.py`): In `execute`, call `await context.emit_event(...)` with `LLM_THINKING` before `_call_llm` — include `model`, `prompt_preview` (first 200 chars of rendered prompt), plus hierarchy tags from context. Stretch: emit `INTERMEDIATE_TEXT` during streaming when streaming support is added
  - [ ] 2-2. **ToolExecutor** (`src/dan/executors/tool.py`): In `execute`, emit `TOOL_CALL_STARTED` with `tool_id`, `args` (merged_args) before `await fn(**merged_args)`; emit `TOOL_CALL_RESULT` with `tool_id`, `result` (or `error`) after the call; include hierarchy tags when available
  - [ ] 2-3. **CodeExecutor** (`src/dan/executors/code.py`): Capture stdout/stderr via `io.StringIO` and `sys.stdout`/`sys.stderr` context manager; emit `CODE_OUTPUT` with `stdout`, `stderr` after successful exec (or on failure with partial output). Handle `exec()` in a controlled namespace where `print` is redirected; include hierarchy tags when available

- [ ] 3. Frontend store: Richer log data structure
  - [ ] 3-1. In `editor/src/store/useGraphStore.ts`: Extend `LogEntry` to include `message` (for display) and ensure `data` is typed for new event types. Keep flat `logs: LogEntry[]` for simplicity initially — grouping/filtering done at render time via selectors
  - [ ] 3-2. Add `EVENT_CATEGORY` map: `llm_thinking`→"thinking", `tool_call_started`/`tool_call_result`→"tool", `code_output`→"output", `node_failed`/error-related→"error", others→"lifecycle". Export for use in LogPanel
  - [ ] 3-3. Update `handleRunEvent` to append all new event types to `logs` (same pattern as existing). No `node_statuses` or `nodeOutputs` updates for these events
  - [ ] 3-4. Add `setSelectedNode` call path: expose `selectNodeFromLog(nodeId: string)` action that calls `setSelectedNode(nodeId)` — used when user clicks a log entry

- [ ] 4. Frontend: Rebuild LogPanel with structure and UX
  - [ ] 4-1. Create `LogPanel.tsx` structure: header with filter controls (text search input, node ID dropdown, event type dropdown), scrollable body with grouped content
  - [ ] 4-2. Implement grouping by `node_id`: `logs` → `Map<node_id, LogEntry[]>` (entries without node_id go to a "Run" group). Render collapsible sections; section header = node label (from `nodes` or `node_id`) + expand/collapse chevron. Optionally include `layer_path` badge so same node IDs across layers are distinguishable
  - [ ] 4-3. Within each node section: sub-group by `EVENT_CATEGORY` (Thinking, Tool Calls, Output, Errors, Lifecycle). Each sub-section collapsible. Default: Thinking and Tool Calls expanded, Output collapsed if many entries
  - [ ] 4-4. Add `EVENT_ICONS`: `llm_thinking`→brain/lightbulb, `tool_call_*`→wrench, `code_output`→terminal/arrow, `node_failed`/errors→red X. Use lucide-react or similar. Add `EVENT_COLORS` for new types (purple thinking, blue tool, cyan output, red error)
  - [ ] 4-5. Click log entry → call `selectNodeFromLog(entry.node_id)` to highlight node on canvas. Only when `entry.node_id` is present. Add hover/click affordance
  - [ ] 4-6. Filtering: (a) text search — filter `logs` by `message` or `JSON.stringify(data)` containing search term; (b) node ID filter — dropdown of distinct `node_id`s from logs; (c) event type filter — dropdown of distinct `event_type`s. Apply all filters before grouping
  - [ ] 4-7. Preserve auto-scroll to bottom on new logs (when not manually scrolled up). Cap logs at 500 entries (existing behavior)

- [ ] 5. Tests
  - [ ] 5-1. Backend: Add tests under existing suite layout (`tests/test_engine/`, `tests/test_server/`) — test that `EventType` includes all new values; test `EngineEvent.to_dict()` for new types
  - [ ] 5-2. Backend: Add executor event-emission tests (LLM/Tool/Code) in existing executor-related test modules — mock `event_callback`, assert `LLM_THINKING`, `TOOL_CALL_STARTED`/`TOOL_CALL_RESULT`, and `CODE_OUTPUT` emission and ordering
  - [ ] 5-3. Backend: Add nested-subgraph event test — run graph with composite/loop sub-graph and assert emitted events keep parent `run_id` while carrying hierarchy tags (`graph_key`, `layer_path`, `parent_node_id`)
  - [ ] 5-4. Server: Add run-manager buffer test — confirm event buffer keeps latest 500 events (rolling window), not first 500
  - [ ] 5-5. Frontend: Manual E2E verification — run a graph with LLM + Tool + Code nodes, confirm LogPanel shows grouped sections, icons, hierarchy badges (if enabled), and click-to-select

- [ ] 6. Docs sync
  - [ ] 6-1. Update `docs/architecture.md` — document new event types, payload shapes, unified run stream, and `ExecutionContext.emit_event`
  - [ ] 6-2. Update `docs/todo.md` — mark Section C items complete
  - [ ] 6-3. Append `docs/changelog.md` entry on completion

## Decisions

- (filled in during execution)

## Notes

- **Event callback wiring:** `ExecutionContext` currently has no `event_callback`. The Engine creates context in `_make_context` and has `self.event_callback` and `state.run_id`. Add `emit_event` as an async method on context that delegates to the engine callback with the current run id. For sub-graphs, reuse the parent run id so all nested events remain in one stream.
- **CodeExecutor stdout capture:** Use `contextlib.redirect_stdout` and `redirect_stderr` with `io.StringIO` to capture `print()` and other stdout. Wrap the `exec()` call in this context.
- **Log structure:** Keeping flat `LogEntry[]` in the store and deriving grouped structure at render time keeps the store simple and avoids migration of existing `handleRunEvent` logic. The frontend computes `groupByNode(logs)` and `groupByCategory(entries)` in the component or a small helper.
- **Filter state:** Store filter values (search text, selected node ID, selected event type) in component state or in the Zustand store as `logFilters: { search: string, nodeId: string | null, eventType: string | null }` — plan assumes component state for filters to avoid store bloat.
- **Hierarchy without stream splitting:** Keep one stream keyed by `run_id`; annotate nested events with `graph_key`, `layer_path`, and `parent_node_id` so the UI can present layer-aware logs without managing multiple WebSocket subscriptions.
