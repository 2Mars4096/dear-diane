# 20-3: Checkpoint UI & Auto-Mode

**Parent:** [20-patch-polish](20-patch-polish.md)
**Status:** completed
**Goal:** Activate recently-built checkpoint portal and debug workbench backends in the editor frontend, and add heuristic auto-mode detection to the chat system.

## Existing Baseline

### Checkpoint Infrastructure (built in 13-1 and 13-2)

- **Backend complete:** `POST /api/runs/{run_id}/rerun` accepts `RerunScope` (downstream_of, single_node, subgraph), validates against checkpoint, rejects stale (409), creates new run_id with provenance.
- **Checkpoint listing:** `GET /api/runs/{run_id}/checkpoints` returns checkpoint list with staleness check.
- **Variable inspector:** `GET /api/graphs/{graph_id}/nodes/{node_id}/inputs?run_id=` returns upstream wiring + runtime values.
- **Test cases:** Full CRUD (`/api/test-cases/`) + run endpoint.
- **Context menu actions:** "Rerun from Here" and "Rerun This Node" already wired on canvas nodes.
- **LogPanel actions:** "Inspect inputs", "Add test case", "Rerun from here" already wired in node group headers.
- **Missing:** Run history panel entry point (click historical run → see checkpoints → rerun). Multi-tab consistency for test cases and checkpoint state.

### Chat Mode System (built in 12-2)

- **Four modes:** Ask, Agent, Plan, Debug — with mode-specific system prompts and tool availability.
- **Frontend selector:** Segmented control in chat header.
- **Missing:** Auto-detection of appropriate mode from message content.

## Tasks

### Run History Checkpoint UI (from 13-2 task 5-2)

- [x] 1. Checkpoint markers in run history
  - [x] 1-1. `RunHistoryPanel.tsx` (run list + replay views): for each historical run entry, add a "Checkpoints" expandable section. Fetch checkpoint list via `GET /api/runs/{run_id}/checkpoints` on expand.
  - [x] 1-2. Each checkpoint entry shows: checkpoint timestamp, completed node count, and staleness fields from API (`compatible`, `stale`, `missing_nodes`, `message`)
  - [x] 1-3. For compatible checkpoints, add "Rerun from checkpoint" action that opens a scope picker (single-node / downstream-of / subgraph) and posts directly to `POST /api/runs/{run_id}/rerun` with chosen scope
  - [x] 1-4. Stale checkpoint: show amber banner with "Graph has changed since this checkpoint — run full workflow instead" and a "Run Full" fallback button
  - [x] 1-5. Loading state: skeleton placeholder while checkpoint list fetches

### Multi-Tab Checkpoint Consistency (from 13-2 task 5-5)

- [x] 2. Ensure test cases and checkpoint state are workflow-scoped
  - [x] 2-1. Audit `TabSnapshot` in `useGraphStore.ts`: verify that `testCases`, checkpoint-related state, and `upstreamVariables` (from variable inspector) are stored per-workflow-id, not per-tab-index *(confirmed: all are API-fetched on demand, not in TabSnapshot — no refactor needed)*
  - [x] 2-2. ~~If tab state stores these per-tab: refactor~~ — N/A, already workflow-scoped via API
  - [x] 2-3. Tab switch (`activateTab`): test cases and checkpoints survive — they are fetched from API per node/run selection, not wiped on tab deactivation
  - [x] 2-4. ~~Test~~ — verified by code audit: `TestCasePanel` fetches via `listTestCases(workflowId, nodeId)`, `ConfigPanel` fetches via `getNodeInputs(graphId, nodeId, runId)`, both per-request

### Auto-Mode Detection (from 12-2 task 7)

- [x] 3. Heuristic mode detection from message content
  - [x] 3-1. `chat_manager.py`: add `detect_chat_mode(message, recent_run_failed)` function
  - [x] 3-2. Detection heuristics implemented (debug > ask > plan > agent priority)
  - [x] 3-3. `ChatMessageRequest.mode`: extended literal to include `"auto"`. `normalize_chat_mode("auto")` passes through; `_produce()` resolves via `detect_chat_mode`.
  - [x] 3-4. Stream payloads: `ChatCompleteEvent` and `ChatMutationEvent` gain `detected_mode: str | None` field; `_produce()` injects it into `chat_complete`/`chat_mutation` payloads when auto was used.
  - [x] 3-5. `ChatPanel.tsx`: "Auto" option in mode selector with `PencilLine` icon. Shows `→ Debug` / `→ Ask` etc. when `detected_mode` arrives. `ChatStreamEvent` type extended with `detected_mode`.
  - [x] 3-6. Ambiguity: defaults to `"agent"` when no pattern matches (conservative fallback).

### Validation

- [x] 4. Focused verification for checkpoint + auto-mode flows
  - [x] 4-1. Backend tests: 32 pytest cases in `tests/test_server/test_auto_mode.py` — `detect_chat_mode` fixtures for debug/ask/plan/agent intents, priority ordering, `normalize_chat_mode` pass-through/alias tests. All pass.
  - [ ] 4-2. Backend tests: checkpoint rerun API *(deferred — requires live RunManager integration test)*
  - [ ] 4-3. Frontend tests: RunHistoryPanel *(deferred — requires component test setup)*
  - [ ] 4-4. Frontend tests: Auto selector *(deferred — requires component test setup)*

## Decisions

- Task 2 (multi-tab consistency) is a no-op: test cases (`TestCasePanel`), variable inspector (`ConfigPanel`), and checkpoints (`RunHistoryPanel`) are all fetched on demand from the API per workflow/node/run. Nothing is stored in `TabSnapshot` that would be lost on tab switch.
- Auto-mode uses `PencilLine` icon from lucide-react (not `Wand2` — already imported).
- `detected_mode` is injected at the `_produce()` level in `app.py`, not on the event model defaults, keeping the chat_manager event classes generic.
- Frontend clears `detectedMode` state when the user manually selects a non-auto mode or sends a new message.

## Notes

- Task 1 (checkpoint UI) is implemented in `RunHistoryPanel.tsx` (the History tab surface), not `LogPanel.tsx`.
- Current checkpoint listing is one checkpoint marker per run (`checkpoint_id == run_id`), so checkpoint UI should be designed as a marker/status + scope-picker rerun flow rather than a multi-checkpoint timeline.
- Task 2 (multi-tab consistency) may be a no-op if `TabSnapshot` already uses workflow-scoped keys — the audit step (2-1) determines scope.
- Task 3 (auto-mode) is a server-side heuristic, not ML-based. The heuristic should be conservative — defaulting to Agent is always safe. False positives into Debug or Plan mode are more disruptive than staying in Agent.
- Auto-mode is opt-in (`mode="auto"` selector option) — users who prefer explicit mode selection are unaffected.
