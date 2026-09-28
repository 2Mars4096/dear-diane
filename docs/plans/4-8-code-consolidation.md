# 4-8: Code consolidation

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** in-progress
**Goal:** Resolve the reviewed reading-state bug and remove duplicated infrastructure without changing workspace behavior.

## Tasks
- [x] Reconcile closed reading sessions and preserve unsynced recovery; add lifecycle regressions.
- [ ] Share bounded JSON transport and one library poller; verify timeout, abort, and subscriber cleanup.
- [ ] Generate the Electron preload from TypeScript; delete handwritten duplicate and verify sandboxed IPC.
- [ ] Extract document/tab lifecycle and project actions; share explicit file-target construction.
- [ ] Extract metadata parsing and permission-aware atomic writes below HTTP routers.
- [ ] Run focused regressions, production build/budgets and Electron checks; commit logical batches and push.

## Decisions
- Preserve active-reader state, local recovery records, native path validation, and existing lazy bundle boundaries.
- Keep the dependency-free SSH installer self-contained.
- Review scope and original evidence: [review](../code-consolidation-review.md).
