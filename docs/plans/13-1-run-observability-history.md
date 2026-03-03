# 13-1: Run Observability & History Foundation

**Parent:** [13-observe-recover](13-observe-recover.md)
**Status:** in-progress
**Goal:** Make run data durable and queryable so every run can be inspected, audited, and compared after completion — and across server restarts.

## Existing Baseline

- `RunManager` (`server/run_manager.py`) keeps `RunRecord` in-memory. `_event_callback` buffers events (rolling 10k cap) and fans out to WS subscribers.
- `RunRecord.snapshot()` returns `run_id`, `graph_id`, `status`, `node_statuses`, `started_at`, `finished_at`, `success`, `errors`, `outputs` — but **omits** `metadata` (token usage, per-node usage, cost).
- `RunResult` (engine) includes `metadata.node_metadata` with per-node `usage` (prompt/completion/total tokens) — this is computed but never flows into `RunRecord`.
- `_aggregate_usage()` in the scheduler emits totals in `RUN_COMPLETED`/`RUN_FAILED` event data, but these are not captured in the snapshot.
- `estimate_cost()` in `providers/costs.py` exists but is not called from the run layer — only frontend `handleRunEvent` computes `nodeCosts` locally.
- `GET /api/runs` returns `rm.list_runs()` (in-memory). No frontend consumer (`api.ts` has no `listRuns`). No workflow-scoped filtering.
- `WS /api/runs/{run_id}/events` streams live events + catch-up from the in-memory buffer. No persistence.
- `LogPanel` shows events for the current run only; `RunSummaryBar` shows tokens/cost/elapsed at the bottom.

## Tasks

- [x] 1. Enrich `RunRecord` and persist run summaries to disk
  - [x] 1-1. Extend `RunRecord` to capture `metadata` from `RunResult` (token usage, per-node usage, cost estimates, `elapsed_seconds`). Wire `_aggregate_usage()` output and `estimate_cost()` into the record at run completion.
  - [x] 1-2. Add `RunStore` (filesystem-backed, one JSON per run under `runs/{workflow_id}/{run_id}.json`). Write on `run_completed`/`run_failed`. Include full snapshot + aggregated metrics.
  - [x] 1-3. On server startup, hydrate `RunManager` index from `RunStore` (metadata only — not full event streams) so `list_runs()` returns historical runs.
  - [x] 1-4. Define retention policy: keep all by default; add optional `DAN_RUN_RETENTION_DAYS` env var for age-based cleanup on startup.

- [x] 2. Persist event streams as append-only audit log
  - [x] 2-1. Add `EventLog` writer: append `EngineEvent.to_dict()` as newline-delimited JSON to `runs/{workflow_id}/{run_id}.events.jsonl` during execution.
  - [ ] 2-2. Capture chat/mutation audit records alongside run events: graph mutations applied, chat mode, mention context used — keyed by `thread_id` and `run_id` where applicable.
  - [ ] 2-3. Ensure failure paths (node errors, tool exceptions, gate evaluation failures) persist full context (traceback, input snapshot, partial outputs) for postmortem debugging.
  - [x] 2-4. Add `EventLog` reader: load and filter events by `run_id`, `node_id`, `event_type`, time range. Used by query APIs and comparison.

- [ ] 3. Extend run history API and add comparison endpoint
  - [x] 3-1. Extend `GET /api/runs` with query params: `workflow_id`, `status`, `after`/`before` (date), `limit`/`offset`. Return enriched snapshots (with token/cost/elapsed).
  - [x] 3-2. Add `GET /api/runs/{run_id}/events` (REST, not WS) to load persisted event stream for completed runs. Keep existing WS endpoint for live runs.
  - [ ] 3-3. Add `GET /api/runs/compare?run_a={id}&run_b={id}` — aligns two runs by node execution order, computes per-node diffs (output delta, timing delta, token delta, status changes).
  - [ ] 3-4. Add `listRuns(workflowId, filters)` and `compareRuns(runA, runB)` to `editor/src/lib/api.ts`.

- [ ] 4. Add editor run history panel and side-by-side comparison UX
  - [ ] 4-1. Add run history panel (new tab in bottom panel next to Logs) with filterable list: status badge, workflow name, elapsed time, token count, cost, timestamp. Source: `listRuns()`.
  - [ ] 4-2. Click a history entry → load its event stream into LogPanel in read-only replay mode (reuse existing LogPanel rendering; distinguish from live via visual indicator).
  - [ ] 4-3. Add "Compare with..." action on history entries → side-by-side diff view with per-node/per-step deltas and compact summary header (total token/cost/time diff).
  - [ ] 4-4. Add deep-links from chat `RunRefBlock` / `RunOutputBlock` into the corresponding history entry.

- [ ] 5. Quality, performance, and docs
  - [ ] 5-1. Backend tests: `RunStore` write/read round-trip, `EventLog` append/query, `compare` alignment correctness, retention cleanup, edge cases (partial runs, missing events, corrupted JSON).
  - [ ] 5-2. Frontend tests: history list rendering/filtering, replay mode loading, compare view states, empty/error handling.
  - [ ] 5-3. Update `docs/architecture.md` (new `RunStore`, `EventLog`, API endpoints), `docs/llm-api-guide.md` (if run APIs are externally callable), `docs/changelog.md`.

## Decisions

- **Extend `RunManager`, don't replace it.** `RunManager` remains the in-memory runtime owner; `RunStore` is the persistence layer it delegates to.
- **Persist both raw event stream (`events.jsonl`) and derived run summary (`{run_id}.json`)** — event stream for forensics, summary for fast list views.
- **Filesystem-backed first.** One directory per run under `runs/{workflow_id}/`. Database abstraction can follow if needed.
- **Comparison is node/event-aligned first.** Semantic output diffing (e.g., LLM text diff) is a future enhancement.
- **Audit scope:** execution events + graph mutations + chat context mentions. Sensitive payload redaction policy to be defined before multi-user release (Phase 9).
- **Replay mode reuses LogPanel rendering** with a read-only flag — no second log component.

## Notes

- This plan is the hard dependency for checkpoint portals and deeper recovery UX in 13-2.
- `handleRunEvent` in `useGraphStore` already computes `nodeCosts` client-side from event data — persistence should capture the same computation server-side so history entries show cost without re-derivation.
