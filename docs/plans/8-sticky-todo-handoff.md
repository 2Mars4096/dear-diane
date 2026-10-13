# 8: Sticky Todo Handoff

**Status:** in-progress
**Goal:** Let Todo dispatch bounded tasks into existing Diane projects and return to the same session for all follow-up.

## Tasks
- [x] Publish project IDs/folders and the existing Work composer execution payload from the focused desktop window.
- [x] Add local-only, durable, idempotent dispatch with immutable project/request context.
- [x] Reuse V2 admission/background execution, progress, stop commands, and session persistence.
- [x] Open the linked session in a desktop window through a bounded native-only route.
- [x] Follow the session's latest run when the user continues in Diane.
- [x] Verify protocol, background execution, restart, duplicate requests, stop acknowledgement, session continuity, desktop routing, types, build and bundle budgets.
- [x] Merge the Diane handoff branch into local main, preserving the existing uncommitted workspace changes.
- [x] Integrate the matching Todo companion branch (verified on current main October 13).
- [ ] Run paired installed-app acceptance while existing work is idle.

## Decisions
- Reuse the existing project store and composer profile builder; no independent task runtime, credential store, or model picker.
- Local desktop only, using its current fixed loopback port 45173. Remote handoff remains outside scope.
- The last focused Work window publishes defaults; background windows must not overwrite its selection.
- Durable UUID receipts live in the graphs store under `sticky_todo/`; thread IDs are deterministic and use the existing project workflow ID.
- Interrupted admission never silently reruns. Needs-attention links open the saved session for recovery.
- Todo completion remains independent. All clarification, approval and follow-up happen in Diane.

## Verification
- Six handoff tests and seven existing session tests pass; two desktop routing tests pass.
- TypeScript frontend/Electron checks, Vite production build and bundle budgets pass.
- Background test uses real V2 scheduling with a deterministic backend. Credentials and production runs are untouched.
- Worktree `/private/tmp/diane-sticky-handoff`, branch `feat/sticky-todo-handoff`; companion `/private/tmp/todo-sticky-diane`.

## Merge review, October 11
- [x] Keep the receipt prepared until execution is accepted so an interrupted admission offers recovery rather than remaining queued. Added a regression that inspects durable state at runtime handoff.

- Merged through `e0f97750` on October 11. Combined main/workspace validation: 18 Python handoff/session tests, two desktop routing tests, frontend production build/budgets, and Electron compilation pass. No push, app installation, or worktree removal performed.

- [x] Remove the clean, merged Sticky Todo worktree at user request on October 11; retain the feature branch. Four worktrees remain, including main.

## October 12 — read-only companion integration review
- [x] Inspect companion `/private/tmp/todo-sticky-diane` at `a7f7c05`; worktree clean at inspection.
- [x] Compare frontend types, Rust bridge, Diane routes and desktop opening contract: version 1 projects; immutable UUID dispatch; six statuses; stop acknowledgement; native-only open on loopback 45173 align.
- [x] Seven Diane API tests pass with deterministic execution, including repeated requests, interrupted admission, restart, subsequent Diane runs and scoped Stop.
- [x] Companion `diane.ts`/`DianeAction.tsx` focused typecheck passes; existing browser fixture passes status indicators, reduced motion, open, Escape, double-click dedup, reload, Stop, disconnect and independent Todo editing with offline/pending/malformed/corrupt data.
- Gap: full companion typecheck reports 16 errors in StarFocusOrbitalMap/3D/useStarFocus (optional ring, ES library target, argument count, missing Three types and related inference, duration union). None are in handoff files. Resolve in the Todo project before integration.
- Remaining: merge matching companion branch, build its native shell, then paired installed-app acceptance with real persistence and idle Diane. Mock bridge checks do not prove native transport or installation.
- This review did not modify, merge, install or restart either companion app.

## October 13 — correct companion integration status
- [x] Verify current Todo main already merged the companion on October 11 and released it in 2.1.7/2.1.8; current installed 2.1.10 passes strict deep signature verification. The October 12 stale-worktree review's “merge matching companion branch” next step is superseded; do not merge it again.
- [x] Rerun current main's handoff/standalone browser fixture from an isolated snapshot: statuses, Open, Escape, deduplication, reload, Stop acknowledgement, disconnected state, and independent task CRUD with absent/stalled/malformed/corrupt/disabled integration all pass.
- [x] Recheck full current companion TypeScript: the same 16 Star Focus diagnostics remain outside handoff. No unrelated Star Focus rewrites or native reinstall undertaken.
- [x] Seven Diane handoff API regressions pass as part of the focused backend batch.
- [ ] Paired installed authenticated dispatch and persistence acceptance remains pending while the Diane run is active. Existing installed native bundle is available; no redundant companion merge/build is required for unchanged handoff source.
