# 4-9: Code consolidation

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** completed
**Goal:** Resolve the reviewed reading-state bug and remove duplicated infrastructure without changing workspace behavior.

## Tasks
- [x] Reconcile closed reading sessions and preserve unsynced recovery; add lifecycle regressions.
- [x] Share bounded JSON transport and one library poller; verify timeout, abort, and subscriber cleanup.
- [x] Generate the Electron preload from TypeScript; delete handwritten duplicate and verify sandboxed IPC.
- [x] Extract document/tab lifecycle and project actions; share explicit file-target construction.
- [x] Extract metadata parsing and permission-aware atomic writes below HTTP routers.
- [x] Run focused regressions, production build/budgets and Electron checks; commit logical batches and push.

## Decisions
- Preserve active-reader state, local recovery records, native path validation, and existing lazy bundle boundaries.
- Keep the dependency-free SSH installer self-contained.
- Review scope and original evidence: [review](../code-consolidation-review.md).

## Validation and publication
- Five logical implementation commits pushed to `origin/main`: reading ownership, shared transport/polling, generated preload, backend helpers, workspace/file targets.
- Isolated archive of implementation commit `0b9a4613`: all 349 frontend tests, production build/budgets, Electron compilation, and real sandboxed preload checks pass. Shared dependencies reused; build metadata isolated.
- All 81 selected backend regressions pass; the relay test required loopback permission. No remote host was contacted.
- Concurrent window-appearance and literature changes remain outside these commits. Plan renumbered from 4-8 to 4-9 to avoid the concurrent literature plan's number.
