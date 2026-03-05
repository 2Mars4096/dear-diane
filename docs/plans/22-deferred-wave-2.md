# 22-2: Deferred Completion Wave 2

**Status:** completed
**Goal:** Close out 18-5 docs/editor, finish deferred runtime features, and add frontend analytics polish — three parallel streams.

## Streams

### S1 — 18-5 Docs + Editor Tier UI (closes 18-5 fully)
**Files:** `docs/llm-api-guide.md`, `editor/src/components/DanNode.tsx`, `editor/src/components/TokenAnalyticsPanel.tsx`

- [x] 1. Update `docs/llm-api-guide.md` with TierPolicy usage, task_tier override, tier map config (18-5 task 7-1)
- [x] 2. Tier badge in DanNode.tsx — L0/L1/L2/L3 color-coded badges + hover tooltip (18-5 task 7-2)
- [x] 3. Tier breakdown in token analytics panel — distribution, cost comparison, per-node table (18-5 task 7-3)

### S2 — Remaining Deferred Runtime
**Files:** `engine/state_store.py`, `engine/scheduler.py`, `executors/control_flow.py`, `engine/error_memory.py`, `server/run_manager.py`, `builder/builder.py`

- [x] 1. Automatic state externalization (18-3 task 1-3) — scheduler + control-flow executors write state per node/iteration/branch/turn
- [x] 2. Run-level advisory token budget (18-3 task 4-1) — BUDGET_ADVISORY event, proportional allocation, advisory warning
- [x] 3. Builder DSL for tools (7-3 task 8-3) — `config` alias on `wf.tool()` for convenience
- [x] 4. Principle compaction (17-2 task 3-4) — PrincipleStore.compact() groups + merges, wired after principle persist

### S3 — Frontend Analytics Polish
**Files:** `editor/src/components/AnimatedEdge.tsx`, `editor/src/components/EditorToolbar.tsx`, `editor/src/components/TokenAnalyticsPanel.tsx`, `editor/src/store/useGraphStore.ts`

- [x] 1. Token flow edge labels (18-4 task 3-4) — toggle-able token count pills on edge midpoints
- [x] 2. Analytics rule dashboard (18-4 task 5-6) — Rules tab with apply/disable, cumulative savings
- [x] 3. Before/after token estimation (18-4 task 4-4) — per-finding savings estimates with category explanations

## Results

- **Tests:** 2734 passed, 15 skipped, 0 failures (25 new tests from S2)
- **Editor build:** succeeds (no new errors)
- **Code review:** 15 issues found (1 critical, 12 important, 2 minor) — all patched. See changelog 2026-03-05.
