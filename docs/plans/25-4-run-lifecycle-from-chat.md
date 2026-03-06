# 25-4: Run Lifecycle from Chat

**Parent:** [25-chat-control-plane](25-chat-control-plane.md)
**Status:** completed
**Goal:** Full run lifecycle management without leaving chat: start, monitor, inspect status, cancel, resume from checkpoint, tail logs, view checkpoints, and resolve `HumanNode` prompts — wrapping `RunManager`, `ActivityTracker`, and `RunStore` as chat tools.

## Design

### Tool Schemas

Each tool is a thin wrapper around existing APIs. Schemas follow the `MUTATION_TOOL_SCHEMA` pattern in `ChatManager.send_message_with_tools()`.

| Tool | Backend | Parameters | Purpose |
|------|---------|------------|---------|
| `start_run` | `RunManager.start_run()` | `workflow_id`, `inputs?`, `run_id?`, `session_id?` | Start execution (current or named workflow) |
| `get_run_status` | `RunManager.get_run()` + `RunRecord.snapshot()` | `run_id` or `"latest"` / `"last_failed"` / `"paused"` | Status of a specific run or resolved reference |
| `list_active_runs` | `ActivityTracker.get_activity()` | — | What's currently running + recent 20 completed |
| `cancel_run` | `RunManager.cancel_run()` | `run_id` | Cancel by run_id |
| `resume_run` | `RunManager.resume_run()` | `run_id`, `workflow_id` | Resume a checkpointed run |
| `get_run_logs` | `RunStore.load_events()` / `RunRecord.events` | `run_id`, `limit?`, `node_id?` | Recent events/logs for a run |
| `get_run_checkpoints` | `RunManager.get_checkpoint_info()` | `run_id` | Checkpoint details (completed nodes, staleness) |
| `rerun_from_checkpoint` | `RunManager.rerun_from_checkpoint()` | `source_run_id`, `scope` (RerunScope) | Partial rerun from checkpoint |
| `submit_human_input` | `RunManager.submit_human_input()` | `run_id`, `request_id`, `response` | Resolve a pending `HumanNode` prompt |

### Resolved References

Handlers must resolve natural-language references before calling backend:

- `"latest"` → most recent run by `started_at` (from `RunManager.list_runs()`)
- `"last_failed"` → most recent run with `status == "failed"`
- `"paused"` / `"the paused one"` → run with pending HumanNode (`get_all_pending_human_inputs()`)
- `"run-123"` → exact `run_id`

### Example Interactions

| User says | Tool(s) called | Result |
|-----------|----------------|--------|
| "Run it" | `start_run(workflow_id=current)` | Starts run, returns run_id; streaming begins |
| "What's still running?" | `list_active_runs()` | ActivitySnapshot: active runs, recent 20, surfaces |
| "Resume the paused one" | Resolve pending `HumanNode` or checkpointed run → `submit_human_input(...)` or `resume_run(...)` | Continues the interrupted run using the appropriate existing mechanism |
| "Show logs for the last run" | Resolve "latest" → `get_run_logs(run_id)` | Recent events as chat-friendly lines |
| "Cancel run abc123" | `cancel_run(run_id="abc123")` | Cancels, returns confirmation |
| "Status of the last failed run" | Resolve → `get_run_status(run_id)` | RunRecord snapshot (status, errors, costs) |
| "What checkpoints exist for run-xyz?" | `get_run_checkpoints(run_id)` | Checkpoint info (completed nodes, staleness) |
| "Rerun from node X downstream" | `rerun_from_checkpoint(source_run_id, scope={scope_type:"downstream_of", target_node_id:"X"})` | New run from checkpoint |

### Run Event Streaming in Chat

- **CLI** (`cli/chat.py`): Already has `_pipe_run_events()` via server `app.py` — subscribes to `RunManager.subscribe(run_id)`, maps via `map_run_event_to_chat_block()` in `scoped_run.py`, streams as `chat_run_event` WebSocket messages.
- **Editor ChatPanel**: Uses same WS `/api/chat/{channel_id}/events`; `chat_run_event` already supported per 21-6.
- **Messaging adapters**: Need equivalent: when run starts from Telegram/WhatsApp, HumanNode prompts and run events must surface in that surface. Gateway router already dispatches; adapters must subscribe to run events for their surface's runs.

### HumanNode Integration

- When a run hits a HumanNode, `RunManager` emits `human_input_needed`; `get_pending_human_inputs(run_id)` returns pending requests.
- Chat tool should surface the prompt (already in `map_run_event_to_chat_block` for `human_input_needed`).
- Submit via `RunManager.submit_human_input(run_id, request_id, response)` — CLI already has `ChatClient.submit_human_input()`.
- **Cross-surface**: If run started from Telegram, HumanNode prompt appears in Telegram; response submitted there continues the run. Gateway `POST /api/gateway/submit-input` and `RunManager.submit_human_input()` are the backend; adapters must render prompts and relay responses.

## Tasks

### 1. Define run lifecycle tool schemas

- [x] 1-1. Add `RUN_LIFECYCLE_TOOL_SCHEMAS` in `capability_registry.py` (or `capability_handlers.py`) with 9 tools: `start_run`, `get_run_status`, `list_active_runs`, `cancel_run`, `resume_run`, `get_run_logs`, `get_run_checkpoints`, `rerun_from_checkpoint`, `submit_human_input`
- [x] 1-2. `start_run`: params `workflow_id` (required), `inputs` (optional dict), `run_id` (optional), `session_id` (optional)
- [x] 1-3. `get_run_status`: param `run_id` (str, supports "latest" | "last_failed" | "paused" | concrete id)
- [x] 1-4. `list_active_runs`: no params (wraps `ActivityTracker.get_activity()`)
- [x] 1-5. `cancel_run`: param `run_id` (required)
- [x] 1-6. `resume_run`: params `run_id`, `workflow_id` (required)
- [x] 1-7. `get_run_logs`: params `run_id`, `limit` (default 50), `node_id` (optional filter)
- [x] 1-8. `get_run_checkpoints`: param `run_id`; returns `RunManager.get_checkpoint_info()` shape
- [x] 1-9. `rerun_from_checkpoint`: params `source_run_id`, `scope_type` (downstream_of | single_node | subgraph), `target_node_id?`, `sub_graph_key?` — maps to `RerunScope` from `engine/checkpoint.py`
- [x] 1-10. `submit_human_input`: params `run_id`, `request_id`, `response`

### 2. Implement tool handlers

- [x] 2-1. Create `handle_start_run(args, context)` in `capability_handlers.py`: resolve `workflow_id` from context (current chat workflow); load graph via `GraphStore`; call `RunManager.start_run(graph, graph_id, inputs, run_id, session_id)`; return `{run_id, status, message}`
- [x] 2-2. Create `resolve_run_reference(ref: str, run_manager, run_store) -> str | None`: map "latest", "last_failed", "paused" to concrete run_id using in-memory `list_runs()` + `get_all_pending_human_inputs()`, with persisted-history fallback via `RunStore.list_summaries(...)` when no in-memory match exists
- [x] 2-3. `handle_get_run_status`: resolve ref → `RunManager.get_run(run_id)`; format `RunRecord.snapshot()` as chat text (status, node_statuses, elapsed, tokens, cost, errors)
- [x] 2-4. `handle_list_active_runs`: call `ActivityTracker.get_activity()`; format `ActivitySnapshot` (active, recent, surfaces) as chat text
- [x] 2-5. `handle_cancel_run`: call `RunManager.cancel_run(run_id)`; return `{cancelled: bool, message}`
- [x] 2-6. `handle_resume_run`: load graph; call `RunManager.resume_run(graph, graph_id, run_id, session_id)` only for checkpoint-based resumes; return `{run_id, status}`
- [x] 2-7. `handle_get_run_logs`: resolve `workflow_id` from `RunRecord.graph_id` (or `RunStore.load_summary`) before calling `RunStore.load_events(workflow_id, run_id, node_id=?, event_type=?)`; slice to `limit`; for live runs use `RunRecord.events` and slice similarly; format as readable log lines
- [x] 2-8. `handle_get_run_checkpoints`: call `RunManager.get_checkpoint_info(run_id)`; format completed_node_ids, staleness, node_output_keys
- [x] 2-9. `handle_rerun_from_checkpoint`: build `RerunScope` from args; load graph; call `RunManager.rerun_from_checkpoint(graph, graph_id, source_run_id, scope)`; return new run_id
- [x] 2-10. `handle_submit_human_input`: call `RunManager.submit_human_input(run_id, request_id, response)`; return success/failure and a compact message
- [x] 2-11. Add safe disambiguation for implicit references: if multiple pending `HumanNode` requests exist for "the paused one", return a short clarification prompt listing candidate `run_id`/node names instead of guessing

### 3. Run event streaming in chat

- [x] 3-1. Verify CLI `_pipe_run_events()` in `app.py` (lines ~3179+) and `map_run_event_to_chat_block()` in `scoped_run.py` cover all needed event types for lifecycle (run_started/completed/failed/cancelled, human_input_needed already done)
- [x] 3-2. Add an app-level run-stream bridge for tool-started runs (parallel to `/run` command path): when `start_run` tool succeeds, create a chat stream channel and pipe `RunManager.subscribe(run_id)` events so clients can receive `chat_run_event` without slash commands
- [x] 3-3. Add equivalent for editor ChatPanel: when tool returns `stream_channel_id`, ChatPanel opens WS and displays run events (reuses `/run` event rendering path via `attachRunStream()`)
- [x] 3-4. Map `EngineEvent` types (`EventType` in `engine/events.py`) to chat-friendly status updates in `map_run_event_to_chat_block` — add any missing types (e.g. `retry_attempted`, `validation_result` if useful for user)
- [x] 3-5. Provide fallback polling behavior for surfaces that cannot hold long-lived WS streams (some messaging adapters): periodically call `get_run_status` / `get_run_logs` and emit compact updates

### 4. HumanNode integration

- [x] 4-1. When `get_run_status` or `list_active_runs` shows a run with pending HumanNode, include prompt text and `request_id` in formatted output so user can respond
- [x] 4-2. Add `submit_human_input` tool: params `run_id`, `request_id`, `response` (dict); calls `RunManager.submit_human_input(run_id, request_id, response)`; this is the primary continuation path for `HumanNode` pauses
- [x] 4-3. Cross-surface: document that adapters (Telegram, WhatsApp) must subscribe to run events for runs they started; HumanNode prompts already flow via gateway; ensure `submit_human_input` is reachable from adapter context (gateway `POST /api/gateway/submit-input`)
- [x] 4-4. Preserve ownership checks: rely on `RunManager.submit_human_input` run/request ownership validation and surface a clear user message when a response targets the wrong run/request pair

### 5. Status formatting

- [x] 5-1. Compact run status: `[run_id] status | N/M nodes | Xs elapsed | Y tokens | $Z` (progress bar optional)
- [x] 5-2. Detailed run output: per-node results, errors, costs; use `RunRecord.snapshot()` fields: `node_statuses`, `result.outputs`, `result.errors`, `total_cost`, `elapsed_seconds`
- [x] 5-3. Create `format_run_status_compact(record)` and `format_run_status_detailed(record)` helpers in `capability_handlers.py` or a shared `run_formatters.py`

### 6. Cross-surface consistency

- [x] 6-1. All tools operate through `ChatManager`; no per-surface logic in handlers — same tool calls from dan-chat, editor, Telegram produce same backend behavior
- [x] 6-2. Ensure `stream_channel_id` and run event WS are available to all surfaces that support streaming (CLI, editor; adapters may use polling fallback)
- [x] 6-3. Document adapter integration: adapters receive `human_input_needed` via their event channel, but they still need explicit Phase 15 bridge work to map external conversations to chat threads/runs and relay replies back through the chat/gateway APIs

### 7. Tests

- [x] 7-1. Unit tests for `resolve_run_reference()`: "latest", "last_failed", "paused", concrete id, invalid ref
- [x] 7-1b. Unit test `resolve_run_reference()` persisted fallback path when in-memory run list is empty
- [x] 7-2. Unit tests for each handler with mocked `RunManager`, `ActivityTracker`, `RunStore`, `GraphStore`
- [x] 7-3. Integration test: send chat message "Run it" → tool `start_run` called → run starts → events stream to WS
- [x] 7-4. Integration test: "Cancel run X" → `cancel_run` → run marked cancelled
- [x] 7-5. Integration test: `HumanNode` workflow — run pauses, `get_run_status` shows pending input, `submit_human_input` resumes
- [x] 7-6. Integration test: multiple pending `HumanNode` requests — implicit "resume paused one" returns disambiguation instead of applying to the wrong run

## Files to Touch

| File | Changes |
|------|---------|
| `src/dan/server/capability_registry.py` | Add `RUN_LIFECYCLE_TOOL_SCHEMAS` (or extend registry from 25-1) |
| `src/dan/server/capability_handlers.py` | 9 run lifecycle handlers + `resolve_run_reference`, formatters |
| `src/dan/server/chat_manager.py` | Wire run lifecycle tools into `get_tools_for_mode` (via registry) |
| `src/dan/server/scoped_run.py` | Extend `map_run_event_to_chat_block` if new event types needed |
| `src/dan/server/app.py` | Ensure `start_run` tool triggers `_pipe_run_events`-style streaming when returning from tool call |
| `src/dan/adapters/` | Adapter bridge updates for run-event relay and human-input continuation on messaging surfaces |
| `tests/test_server/test_capability_run_lifecycle.py` | New test file for handlers and integration |

## Decisions

- (To be filled during execution)

## Notes

- `ActivityTracker.get_activity()` uses `RunManager.list_runs()`; sorts recent by `started_at` (RunRecord field). ActivitySnapshot model: `active`, `recent`, `connected_surfaces`.
- `RunStore.load_events(workflow_id, run_id, node_id?, event_type?)` does not take `limit`; handlers should slice results after loading. Completed runs come from `RunStore`, live runs from `RunRecord.events` in memory.
- `RerunScope` from `dan.engine.checkpoint`: `scope_type`, `target_node_id`, `sub_graph_key`.
- There is no arbitrary `pause_run()` API today. Phase 15 should expose existing interruption points only: pending `HumanNode` input, cancellation, checkpoint resume, and partial rerun.
- Effort: ~2 days.
