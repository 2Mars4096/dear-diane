# 45: Backend Startup Path Hardening

**Status:** completed
**Goal:** Make DAN server startup independent of missing or unstable current working directories so `dan-up` can reach `/health` reliably.

## Tasks
- [x] 1. Centralize graph persistence directory resolution
  - [x] 1-1. Extend `resolve_graphs_dir()` with a missing-cwd fallback anchored to the repo root or `~/.dan`.
  - [x] 1-2. Route package-backed app/bootstrap entrypoints through that shared resolver instead of raw `./graphs`.
- [x] 2. Centralize workspace-root resolution
  - [x] 2-1. Add `resolve_workspace_root()` for startup/request paths that previously called `os.getcwd()` directly.
  - [x] 2-2. Route package-backed startup and server utility modules through that shared resolver.
- [x] 3. Seed the resolved graph directory before app import
  - [x] 3-1. Set `DAN_GRAPHS_DIR` in `src/dan/server/__main__.py` before uvicorn imports `dan.server.app`.
- [x] 4. Add regressions
  - [x] 4-1. Cover missing-cwd graph-dir fallback in `tests/test_server/test_paths.py`.
  - [x] 4-2. Cover missing-cwd workspace-root fallback in `tests/test_server/test_paths.py`.
  - [x] 4-3. Cover `dan.server.__main__` exporting the resolved graph dir.

## Decisions
- Use the package-backed modules (`startup/__init__.py`, `chat_factory/__init__.py`) rather than the flat legacy files because Dropbox conflict-copy issues still affect the flat paths.
- Preserve existing `./graphs` behavior while the working directory is valid; only anchor to an absolute fallback when the cwd is unavailable.

## Notes
- Repro from `~/.dan/logs/server.log`: startup first died in `GraphStore(base_dir=_graphs_dir)` with `FileNotFoundError: [Errno 2] No such file or directory: '.'`, then after that fix died in `init_stores()` on `os.getcwd()` for `DAN_WORKSPACE_ROOT`.
- Validation: `pytest -q tests/test_server/test_paths.py` (`7 passed`).
- Deleted-cwd smoke: after `chdir()` into a temp dir and deleting it, `init_stores()` succeeds and `TestClient(app).get("/health")` returns `200` / `status=ok`.
