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
- [ ] Integrate companion branches and run installed-app acceptance while existing work is idle.

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
