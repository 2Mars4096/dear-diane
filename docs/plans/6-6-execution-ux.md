# 6-6: Execution UX — Loop Visualization, Streaming Output, Human Input

**Parent:** [6-phase-3.75-visual-editor-editing](6-phase-3.75-visual-editor-editing.md)
**Status:** completed
**Goal:** Close three execution UX gaps: make loop behavior visible on canvas, stream LLM output into logs/output preview in real time, and support in-run human input popups for `HumanInTheLoop` nodes.

## Tasks

- [ ] 1. Loop behavior visualization (while_loop / for_each)
  - [ ] 1-1. Add new engine event types in `src/dan/engine/events.py`: `ITERATION_STARTED`, `ITERATION_COMPLETED` (plus `FOR_EACH_ITEM_STARTED` / `FOR_EACH_ITEM_COMPLETED` if needed for clarity).
  - [ ] 1-2. In `src/dan/executors/control_flow.py`, emit iteration events inside `WhileLoopExecutor` for each loop turn (include iteration index, max iterations, condition result, and elapsed time).
  - [ ] 1-3. In `src/dan/executors/control_flow.py`, emit per-item events in `ForEachExecutor` (item index and total item count).
  - [ ] 1-4. In `editor/src/store/useGraphStore.ts`, handle new iteration events and maintain per-node loop state (e.g., `nodeIterations: Record<string, { current: number; total?: number }>`).
  - [ ] 1-5. In `editor/src/components/DanNode.tsx`, add loop-specific badges:
    - `while_loop`: condition badge (`while: <condition>`) + live iteration badge (`iter 2/10`)
    - `for_each`: item progress badge (`item 3/8`) and optional parallelism badge
  - [ ] 1-6. Add a persistent loop indicator icon on loop nodes so they read as gates/feedback structures even when idle.
  - [ ] 1-7. In drill-in view, render virtual dashed feedback arrows (visual-only) between body exit and entry mappings; do not persist these edges into `danGraph.edges`.
  - [ ] 1-8. For `for_each`, compute progress as `completed/total` (not event-order index) since branches run in parallel and completion order is non-deterministic.
  - [ ] 1-9. Define a fallback for loop drill-in arrows when explicit port mapping is ambiguous: render a single generic feedback arrow with tooltip instead of failing to render.

- [ ] 2. Streaming LLM output visibility
  - [ ] 2-1. In `src/dan/executors/llm.py`, switch to streamed completions (`stream=True`) and accumulate output incrementally.
  - [ ] 2-2. Emit `intermediate_text` events during generation with both delta and accumulated text payloads; include `attempt` metadata so retries do not mix streams.
  - [ ] 2-3. Add provider compatibility fallback: if streaming is unsupported by the OpenAI-compatible backend, gracefully fall back to non-streaming with a warning event.
  - [ ] 2-4. In `editor/src/store/useGraphStore.ts`, track live streaming text per node (`streamingOutputs` map), replace existing `intermediate_text` log entries in-place (avoid append-per-token growth), and clear stream state on node completion/failure.
  - [ ] 2-5. In `editor/src/components/LogPanel.tsx`, render `intermediate_text` content live (read latest merged entry rather than many chunk rows).
  - [ ] 2-6. In `editor/src/components/OutputPreview.tsx`, display streaming text for selected running nodes, then swap to finalized output when completed.
  - [ ] 2-7. Ensure rich logs visibly cover: thinking, exploration text, tool-call start/result, and intermediate generation text.
  - [ ] 2-8. Update streaming event buffering policy in `src/dan/server/run_manager.py` so reconnect/catch-up remains useful under high-volume token streams (increase buffer and/or coalesce intermediate events).
  - [ ] 2-9. Define and implement explicit stream throttling/coalescing cadence (server and/or client) so token bursts do not freeze the UI while still feeling real-time.

- [ ] 3. Human-in-the-loop popup flow (mid-run)
  - [ ] 3-1. Add `HUMAN_INPUT_NEEDED` event type in `src/dan/engine/events.py`.
  - [ ] 3-2. Extend callback contract in `src/dan/engine/executor.py` and `src/dan/engine/scheduler.py` so human-input callback receives structured request metadata (at least `node_id`, `prompt`, and generated `request_id`).
  - [ ] 3-3. In `src/dan/server/run_manager.py`, add async pending-input registry keyed by request ID (not only run ID) so multiple human requests can be handled safely.
  - [ ] 3-4. Update `src/dan/executors/control_flow.py` (`HumanInTheLoopExecutor`) to call the new callback signature and preserve timeout/default-action behavior.
  - [ ] 3-5. Implement server-side async callback that emits `HUMAN_INPUT_NEEDED`, waits on an `asyncio.Event`, and returns submitted response.
  - [ ] 3-6. Wire callback into engine creation path so `HumanInTheLoopExecutor` receives `human_input_callback` in server mode.
  - [ ] 3-7. In `src/dan/server/app.py`, add `POST /api/runs/{run_id}/human-input` endpoint accepting `{ request_id, node_id, response }`.
  - [ ] 3-8. Add validation/guardrails: reject unknown or stale request IDs, ensure request belongs to run, and resolve timeout/cancellation paths cleanly.
  - [ ] 3-9. In `editor/src/lib/api.ts`, add `submitHumanInput(...)`.
  - [ ] 3-10. Add `pendingHumanInput` state in `editor/src/store/useGraphStore.ts`, handle `human_input_needed` events, and clear on submit/cancel.
  - [ ] 3-11. Create `editor/src/components/HumanInputDialog.tsx` modal popup for prompt + response submission.
  - [ ] 3-12. Mount dialog in `editor/src/App.tsx` and ensure UX is non-blocking for log/timeline visibility.
  - [ ] 3-13. Ensure reconnect-safe UX: include unresolved human-input requests in run catch-up (or expose a pending-requests API) so dialogs reappear after refresh/reconnect.

- [ ] 4. Tests
  - [ ] 4-1. Backend unit tests: loop iteration events emitted with correct payloads and ordering.
  - [ ] 4-2. Backend unit tests: streaming mode emits intermediate_text chunks; fallback path works when streaming unsupported.
  - [ ] 4-3. Backend unit tests: human-input callback waits, resumes on submit, handles invalid/stale request IDs, and timeout paths.
  - [ ] 4-3a. Backend integration test: after WebSocket reconnect/catch-up, pending human-input requests are still discoverable and submit-able.
  - [ ] 4-4. Frontend tests: DanNode loop badges/iteration counters render and update.
  - [ ] 4-5. Frontend tests: LogPanel and OutputPreview show streaming content progressively.
  - [ ] 4-6. Frontend tests: HumanInputDialog appears on event and submits correctly.

- [ ] 5. Docs sync
  - [ ] 5-1. Update `docs/architecture.md` with loop event contract, streaming event flow, and human-input request lifecycle.
  - [ ] 5-2. Update `docs/todo.md` to track 6-6 completion under Phase 3.75.
  - [ ] 5-3. Append `docs/changelog.md` entries for plan creation and implementation.

## Decisions

- Use node-level loop badges + virtual drill-in feedback arrows instead of persistent self-loop edges on root canvas.
- Stream emission is throttled/debounced to preserve responsiveness and avoid event storms.
- Streaming requires catch-up-safe buffering/coalescing so reconnects do not lose critical run context.
- Human input requests use request IDs for concurrency safety and correctness.
- Keep protocol backward compatible where possible (fallback on non-streaming providers).

## Notes

- `HumanInTheLoopExecutor` already supports callbacks; the gap is server/UI plumbing.
- `intermediate_text` event type already exists, so this phase mainly wires producer + renderer behavior.
- This plan is additive to Phase 3.75 and re-opens the parent plan until 6-6 completes.
