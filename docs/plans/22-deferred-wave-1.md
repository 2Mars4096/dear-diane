# 22: Deferred Completion Wave 1

**Status:** completed
**Goal:** Finish the highest-ROI deferred tasks across Phase 10 (Token Optimization) and Phase 7.2 (Cursor-Parity Chat) in three parallel streams with zero file overlap.

## Streams

### S1 — 18-5 Tiering Finish
**Files:** `providers/`, `models/nodes.py`, `models/control_flow.py`, `executors/llm.py`, `executors/control_flow.py` (OrchestratorExecutor only)

- [x] 1. Fix `select_sync` bug — `OrchestratorExecutor._resolve_model()` now uses `await model_selector.select()` (async)
- [x] 2. Sync 18-5 plan checkboxes — tasks 1-4, 2-5, 3-1, 3-2, 3-3, 3-4, 4-1 verified as implemented and checked
- [x] 3. Floor enforcement (18-5 task 4-3) — `task_tier` acts as floor; scoring always runs, tier clamped upward
- [x] 4. Escalation cap (18-5 task 4-4) — verified: `_escalated` flag enforces max 1; L3 falls through to on_failure
- [x] 5. Tests (18-5 tasks 6-1..6-6) — 46 new tests in `test_tier_scorer.py`, existing tests updated in `test_tier_scoring.py`
- [x] 6. Update plan file 18-5 checkboxes

### S2 — Token Optimization Runtime
**Files:** `engine/cache.py`, `engine/memory_pipeline.py`, `engine/state_store.py`, `engine/token_optimization.py`, `models/context.py`, `executors/control_flow.py` (WhileLoop/ForEach executors only)

- [x] 1. Loop compaction strategy runtime (18-3 tasks 3-3, 3-4, 3-5) — `sliding_window`, `summarize`, `diff_based`, `keep_last` implementations in control-flow executors; delegate to ShortTermMemory buffer; inter-iteration wiring
- [x] 2. Persistent cross-run cache (18-2 task 2-3) — disk-backed memoization with session memory coordination
- [x] 3. Memory-aware cache invalidation (18-2 task 2-5) — `memory_dependency_keys` tracking, invalidation on memory change
- [x] 4. Unified cross-source token budget (18-3 task 4-4) — budget accounts for edges + system + context + hyperedge + memory + RAG

### S3 — Frontend UX Polish
**Files:** `editor/src/components/`, `editor/src/lib/`

- [x] 1. Recently used mentions at top (12-3 task 8-4) — localStorage-backed "Recent" section, last 10 selections, deduped
- [x] 2. Preview tooltip on mention hover (12-3 task 8-5) — 300ms debounce, type-aware content, positioned right/left
- [x] 3. Code syntax highlighting in mention context (12-3 task 3-3) — highlight.js in tooltip, dark mono pills in chat

## File Boundary Agreement

| File | Owner |
|------|-------|
| `executors/control_flow.py` — `OrchestratorExecutor._resolve_model` | S1 |
| `executors/control_flow.py` — `WhileLoopExecutor`, `ForEachExecutor` | S2 |
| `editor/src/**` | S3 |
| All other `providers/`, `models/` | S1 |
| All other `engine/` | S2 |

## Sequencing

All three streams run in parallel. No cross-stream dependencies.
Post-completion: parent agent updates `docs/todo.md`, `docs/changelog.md`, `docs/plans/18-5-*.md`, `docs/plans/18-2-*.md`, `docs/plans/18-3-*.md`, `docs/plans/12-3-*.md`.

## Notes

- S1 and S2 both touch `executors/control_flow.py` but in different classes — no merge conflict risk.
- S2 loop compaction is the most complex task; may require follow-up for edge cases.
- S3 is purely additive React code with no backend changes.
