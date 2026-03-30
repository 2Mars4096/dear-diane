# 49: Cwd-Safe Runtime Execution

**Status:** completed
**Goal:** Keep server-side workflow execution stable when the backend process cwd is missing or unrelated to the active workspace.

## Tasks
- [x] 1. Remove cwd-sensitive runtime storage paths from server engine config
  - [x] 1-1. Anchor relative checkpoint, memory, rules, state-store, and cache dirs to the resolved workspace root.
  - [x] 1-2. Seed `DAN_WORKSPACE_ROOT` during server bootstrap so runtime helpers can rely on it.
- [x] 2. Make inline code-node file reads respect the workspace root
  - [x] 2-1. Wrap inline `open(...)` so relative paths resolve under `DAN_WORKSPACE_ROOT` instead of raw process cwd.
- [x] 3. Add focused regressions
  - [x] 3-1. Cover server runtime-config path resolution.
  - [x] 3-2. Cover code-executor relative file reads via workspace root.
  - [x] 3-3. Cover a real `RunManager` run under a deleted cwd.

## Decisions
- Server runtime directories should be absolute and rooted at the resolved workspace, not left as raw relative paths.
- Inline code nodes may continue using Python `open(...)`, but relative paths should resolve against `DAN_WORKSPACE_ROOT` in server/local DAN runtimes.

## Notes
- This fixes the backend/runtime crash class behind run summaries like `[Errno 2] No such file or directory: '.'` or relative `./checkpoints` failures.
- Workflows that intentionally use relative files like `watchlist.csv` still semantically depend on the chosen workspace root; if the file lives elsewhere, callers should supply an absolute path or launch DAN against the correct workspace.
