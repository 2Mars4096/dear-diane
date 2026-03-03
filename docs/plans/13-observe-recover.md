# 13: Phase 8 — Observe & Recover

**Status:** in-progress
**Goal:** Make workflow iteration measurable and recoverable by adding durable run artifacts, queryable audit trails, and targeted replay/testing capabilities.

## Motivation

Current execution UX is strong for live runs, but weak for longitudinal debugging and iterative refinement:
- `RunManager` keeps runs in-memory only (`_runs: dict[str, RunRecord]`). Once the server restarts, all run history is gone.
- `RunRecord.snapshot()` omits token usage and node metadata — even while the run is live, the summary is incomplete.
- Engine checkpoints (`FileSystemCheckpointStore`) persist to disk but `list_runs()` is never called by the server; checkpoints are write-only.
- `GET /api/runs` exists but has no frontend consumer — no run history panel, no filtering, no comparison.
- Event streaming (`WS /api/runs/{run_id}/events`) is live-only; events buffer in memory (rolling 10k cap) and are lost on server restart.
- Chat checkpoints (`save_checkpoint`) persist graph snapshots but have no restore or listing UI (deferred from 12-5).
- Node-level debugging ergonomics (upstream variable visibility and reusable node test cases) are missing.

Phase 8 closes these gaps by splitting work into a **data foundation** track (13-1) and a **recovery/debug workbench** track (13-2).

## Existing Infrastructure (baseline)

| Component | Location | What exists | Gap |
|---|---|---|---|
| `RunManager` | `server/run_manager.py` | In-memory `RunRecord` with `snapshot()`, event callback, subscriber fan-out | No disk persistence; snapshot omits usage/metadata |
| `RunRecord` | `server/run_manager.py:30–55` | `run_id`, `graph_id`, `status`, `result`, `node_statuses`, `events`, `started_at`, `finished_at` | No token/cost/latency in snapshot |
| `RunResult` | `engine/scheduler.py:44–52` | `outputs`, `success`, `node_statuses`, `errors`, `metadata` (includes per-node usage) | Only returned at engine level; not persisted |
| Engine checkpoints | `engine/checkpoint.py` | `FileSystemCheckpointStore`: save/load/list_runs, writes `{run_id}/checkpoint.json` | `list_runs` unused; no server-level checkpoint browsing |
| Event types | `engine/events.py` | 30+ `EventType` variants, `EngineEvent` with `to_dict()` | Events not persisted; lost on restart |
| Run endpoints | `server/app.py` | `POST /api/runs`, `GET /api/runs`, `GET /api/runs/{run_id}`, `POST /resume`, `WS /events` | `GET /api/runs` not consumed by frontend |
| Token aggregation | `engine/scheduler.py` | `_aggregate_usage()` sums prompt/completion/total tokens, `elapsed_seconds` in RUN_COMPLETED | Not in `RunRecord.snapshot()` |
| Cost estimation | `providers/costs.py` | `estimate_cost(model, prompt, completion)` | Not wired to run-level reporting |
| Chat checkpoints | `server/chat_store.py` | `save_checkpoint()` writes `{thread_id}_{msg_id}_{ts}.json` | No restore endpoint or listing UI |
| LogPanel | `editor/LogPanel.tsx` | Live event stream, node groups, filters, RunSummaryBar (tokens/cost/elapsed) | Current run only; no historical browsing |
| OutputPreview | `editor/OutputPreview.tsx` | Selected node's output (JSON/streaming) | Outputs only; no upstream/input view |
| DanNode badges | `editor/DanNode.tsx` | Status, duration, token count, cost on canvas nodes | Live run only |

## Sub-Plans

| # | Sub-Plan | Scope | Primary Files |
|---|----------|-------|---------------|
| [13-1](13-1-run-observability-history.md) | Run Observability & History Foundation | Persist structured run artifacts, action audit log, run history list, and side-by-side comparison APIs/UI | `server/run_manager.py`, `engine/scheduler.py`, `engine/events.py`, `server/app.py`, `editor/src/components/`, `editor/src/lib/api.ts` |
| [13-2](13-2-recovery-debug-workbench.md) | Recovery & Debug Workbench | Checkpoint portals for partial reruns, variable inspector, node test cases/annotations, and editor workflow | `engine/checkpoint.py`, `server/run_manager.py`, `server/app.py`, `editor/src/components/ConfigPanel*`, `editor/src/store/useGraphStore.ts` |

## Dependencies / Sequencing

1. **13-1 first** — run/audit persistence is the data backbone used by recovery and debugging UX.
2. **13-2 second** — recovery and node-debug features build on persisted artifacts/checkpoints and run metadata from 13-1.

Parallelization note: 13-2 task 3 (variable inspector) and task 4 (node test cases) have no dependency on 13-1 and can start in parallel. Tasks 1–2 (checkpoint portals) depend on 13-1 persistence.

## Success Criteria

- Users can browse historical runs, inspect artifacts, and compare runs side-by-side from the editor.
- All key execution actions (LLM/tool/decision events) are durably logged and queryable across server restarts.
- Users can rerun from checkpoint boundaries without restarting full workflows.
- Users can inspect upstream variable availability per node before execution.
- Users can save and replay node-level test cases for isolated validation.

## Decisions

- **Extend existing infrastructure, don't replace it.** `RunManager`, `FileSystemCheckpointStore`, event streaming, and `GET /api/runs` are starting points — add persistence and enrich, don't rewrite.
- **Storage is append-only first.** Prefer immutable artifact/event records keyed by run/checkpoint IDs over mutable in-place updates.
- **Forensics before optimization.** Ship reliable observability and replay first; tune storage/query performance after correctness.
- **Single-user local baseline.** Multi-user governance/compliance extensions are out of scope for this phase.

## Notes

- This phase intentionally focuses on practical debugging/recovery loops, not new reasoning primitives.
- Future quality harness work (12-6 stretch) can consume 13-1 artifacts to automate regression analysis.
- Chat checkpoint restore (deferred from 12-5) is a natural add-on once 13-2 checkpoint portals land.
