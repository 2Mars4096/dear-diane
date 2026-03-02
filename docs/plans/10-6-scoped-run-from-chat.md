# 10-6: Scoped Run Execution from Chat

**Parent:** [10-chatbox-nl-workflow](10-chatbox-nl-workflow.md)
**Status:** completed — core API, scoped graph builder, command parser, and event mapper done; catch-up payloads, output mapping, NL intent, deep-links, rate guard, tests, and docs deferred to Phase 7.2 (plan 12-*)
**Goal:** Add explicit scoped execution from chat (`full`, `node`, `subgraph`) with server-authoritative target resolution, run orchestration via `RunManager`, and run-event streaming back into chat threads.

## Tasks

- [x] 1. Scoped run API contract
  - [x] 1-1. Add `POST /api/runs/scoped` endpoint in `app.py`
  - [x] 1-2. Define `ScopedRunRequest` model:
    - `workflow_id: str`
    - `scope: "full" | "node" | "subgraph"`
    - `target_node_id: str | None` (required for `node` / optional for `subgraph` aliasing)
    - `target_subgraph_key: str | None` (required for `subgraph`)
    - `inputs: dict[str, Any] | None`
    - `thread_id: str | None`
    - `origin_message_id: str | None`
  - [x] 1-3. Define `ScopedRunResponse` model: `{ run_id, status, scope, target, stream_channel_id }`
  - [x] 1-4. Preserve existing `/api/runs` and `/api/runs/{id}/resume` unchanged for backward compatibility

- [x] 2. Scoped graph builder
  - [x] 2-1. Add `src/dan/server/scoped_run.py` with `build_scoped_graph(graph, scope, target) -> Graph`
  - [x] 2-2. `scope="full"`: pass-through original graph
  - [x] 2-3. `scope="subgraph"`: resolve target composite/loop subgraph and build runnable graph from that subgraph definition
  - [x] 2-4. `scope="node"`: build minimal executable graph for target node (node + required input plumbing). If required inputs are missing, return structured error so chat can ask for inputs instead of running with fake defaults.
  - [x] 2-5. Validate derived graph with `validate_graph()` before run
  - [x] 2-6. Attach scoped-run metadata (`scope`, `target`, `source_workflow_id`) for event and audit context

- [ ] 3. RunManager integration *(partially done)*
  - [x] 3-1. Reuse `RunManager.start_run()` for scoped graphs without changing existing run lifecycle semantics
  - [x] 3-2. Extend run record metadata to include `scope`/`target` so downstream consumers can render contextual run summaries
  - [ ] 3-3. Ensure catch-up payloads include scoped metadata for reconnect/reload handling *(deferred to 12-1)*
  - [ ] 3-4. Define output mapping for scoped runs (e.g., node scope returns selected node outputs; subgraph returns subgraph exit outputs) *(deferred to 12-4)*

- [ ] 4. Chat command handling and target resolution *(partially done)*
  - [x] 4-1. Add explicit command parsing in `ChatManager` for `/run`, `/run @node`, `/run-subgraph @node`
  - [x] 4-2. Resolve command targets server-side from mention references and authoritative graph state
  - [x] 4-3. If target is invalid/missing, return structured chat error (`target_not_found`, `target_not_runnable`)
  - [ ] 4-4. If inputs are required, return `input_required` metadata so frontend can launch `RunInputsDialog` or chat prompt collection *(deferred to 12-4)*
  - [ ] 4-5. Optional follow-up: natural-language run intent detection (`"run this node"`) after explicit command path is stable *(deferred to 12-2)*

- [ ] 5. Run-event streaming into chat *(partially done)*
  - [x] 5-1. Create chat execution event mapper (`run_started`, `node_completed`, `node_failed`, `run_completed`, `run_failed`) -> chat thread message blocks
  - [x] 5-2. Include `run_ref` in generated chat blocks so `10-5` UI can render and link to logs
  - [ ] 5-3. Add "View in logs" deep-link metadata (run id + node id) for each mapped event block *(deferred to 12-4)*
  - [ ] 5-4. Ensure event fan-out does not break existing run WebSocket clients (editor log panel still works) *(deferred to 12-1)*

- [ ] 6. Safety and consistency guards *(partially done)*
  - [x] 6-1. Server-authoritative resolution only: reject client attempts to provide ad-hoc graph snapshots for execution
  - [x] 6-2. Add scope preconditions (e.g., `subgraph` scope requires valid composite/loop target)
  - [ ] 6-3. Add rate/duplication guard for repeated `/run` commands in quick succession *(deferred to 12-1)*
  - [x] 6-4. Ensure scoped runs do not mutate stored workflow graph (execution-only derived graph)

- [ ] 7. Tests *(deferred to 12-6)*
  - [ ] 7-1. API tests for `/api/runs/scoped`: full/node/subgraph success + validation errors
  - [ ] 7-2. Scoped graph builder tests: derived graph correctness and validation behavior
  - [ ] 7-3. Input-required tests: node/subgraph run blocked when required inputs are absent
  - [ ] 7-4. Chat command parsing tests (`/run`, `/run @node`, bad target)
  - [ ] 7-5. Event mapping tests: run events converted to chat blocks with correct `run_ref`
  - [ ] 7-6. Backward compatibility tests: existing `/api/runs` and log panel flows unchanged

- [ ] 8. Docs sync *(deferred — update when features land)*
  - [ ] 8-1. `architecture.md`: add `scoped_run.py`, `/api/runs/scoped`, scoped run semantics
  - [ ] 8-2. `llm-api-guide.md`: document scoped run command/API behavior
  - [ ] 8-3. `README.md`: mention chat-triggered scoped execution
  - [ ] 8-4. `changelog.md`: implementation entry

## Decisions

- Start with explicit command syntax (`/run ...`) before natural-language run intent.
- Target resolution and scoped graph construction are server-authoritative.
- Scoped execution uses derived in-memory graphs and never rewrites persisted workflow JSON.

## Notes

- **Deferred to Phase 7.2:** Catch-up payloads (→ 12-1), output mapping (→ 12-4), input-required dialog (→ 12-4), NL run intent (→ 12-2), deep-link metadata (→ 12-4), event fan-out safety (→ 12-1), rate guard (→ 12-1), all tests (→ 12-6), docs sync (→ when features land).
- This plan is intentionally split from 10-5 to isolate backend/runtime complexity from thread persistence UI.
- Node-scoped execution semantics must be explicit about required inputs; "mock defaults" are risky and should not be the default behavior.
- Scoped runs should emit standard run events so existing observability (log panel, status badges) remains consistent.
