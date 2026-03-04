# 20: Phase 11.5 — Patch & Polish

**Status:** completed
**Goal:** Close documentation debt, polish chat and editor UX with deferred quick-wins, and activate recently-built backend features (checkpoint portals, variable inspector, test cases) with frontend integration.

## Motivation

Phases 8–11 shipped significant backend infrastructure (checkpoint portals, variable inspector, node test cases, token analytics, multi-mode chat, rich mentions, markdown decompiler) but left a tail of small deferred items — docs that were never written, UI shortcuts that were spec'd but skipped, and frontend surfaces that would activate already-working backends. None of these require new architecture; they're purely "finish what we started" work.

Grouped into three tiers by effort:

1. **Docs & quick wins** — documentation catch-up and trivial UI additions (< 30 min each)
2. **Chat & editor UX polish** — moderate effort features that make the product feel complete (1–3 hours each)
3. **Checkpoint UI & auto-mode** — larger but high-value items that unlock recent backend work (half-day each)

## Sub-Plans

| # | Sub-Plan | Scope | Effort | Source Items |
|---|----------|-------|--------|--------------|
| [20-1](20-1-docs-quick-wins.md) | Docs Sync & Quick Wins | 3 docs updates + 3 trivial UI features | ~2 hours total | 7-1 task 8, 7-2 task 10, 7-3 task 11, 12-2 tasks 2-2/2-4/6-4 |
| [20-2](20-2-chat-editor-polish.md) | Chat & Editor UX Polish | 5 moderate frontend features | ~8–12 hours total | 12-2 task 6-5, 12-1 task 4-4, 12-3 task 8-3, 8-4 tasks 2-3/2-4, 7-4 task 5-2 |
| [20-3](20-3-checkpoint-ui-automode.md) | Checkpoint UI & Auto-Mode | Checkpoint frontend integration + auto-mode detection | ~8–10 hours total | 13-2 tasks 5-2/5-5, 12-2 task 7 |

## Dependencies / Sequencing

- **20-1 has zero dependencies** — can start immediately. Docs are purely additive; UI items are isolated.
- **20-2 has zero inter-task dependencies** — all 5 features touch different files and can be parallelized.
- **20-3 depends on 13-1 (run history) for checkpoint UI** — the run history panel (from 13-1) must exist for checkpoint markers to appear in it. The auto-mode feature is independent.
- **All three sub-plans are parallelizable** — different developers (or sessions) can work on 20-1, 20-2, 20-3 concurrently.

## Success Criteria

- All shipped features (retry, multi-provider, built-in tools) have documentation in architecture.md, llm-api-guide.md, and README.
- Chat mode persists across thread switches; modes are cyclable via keyboard shortcut; debug mutations are visually tagged.
- Run errors have a one-click "Fix this" path to Debug mode.
- Chat run events are collapsed by default, expandable on click.
- `@` mentions use fuzzy matching.
- Markdown export is accessible from the toolbar with preview.
- Log columns are sortable by name/duration/tokens/cost.
- Historical runs show checkpoint markers with "Rerun from here" entry points.
- Chat auto-detects appropriate mode from message intent.

## Decisions

- (filled in during execution)

## Notes

- This phase intentionally contains no new architecture or engine changes — everything is finishing deferred work from prior phases.
- Items are sourced from the backlog section of `todo.md`, each traceable to its original plan and task number.
- After this phase, the backlog should shrink significantly, leaving primarily "requires new architecture" and stretch items.
