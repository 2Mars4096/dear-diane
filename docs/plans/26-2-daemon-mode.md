# 26-2: Daemon Mode

**Parent:** [26-always-on-service](26-always-on-service.md)
**Status:** completed
**Goal:** OS-level service management so DAN starts at login and stays running — `dan service install` creates macOS launchd plist (+ Linux systemd unit), `dan service start/stop/status/logs`, auto-start at login, health checks, and log rotation to `~/.dan/logs/`.

## Context

- Phase 16 makes DAN a persistent background service.
- Plan 26-1 adds `dan up`/`dan down` for manual server lifecycle. This plan takes it further: OS-level service management.
- Server: FastAPI/uvicorn process via `dan-serve` (entry point `dan.server.__main__:main`). Accepts `--host`, `--port`, `--reload`/`--no-reload`.
- CLI utilities: `ensure_dan_dir()` creates `~/.dan/runs/`. `DAN_DIR = Path.home() / ".dan"`.
- Background runs store PIDs in `~/.dan/runs/`.
- 26-1 introduces `~/.dan/server.pid` for tracking the background server.
- Server has `/health` endpoint for readiness checks.
- Entry points in pyproject.toml: `dan-serve`, `dan-run`, `dan-chat`, etc.

## Tasks

- [x] 1. **`dan service` CLI subcommand**
  - [x] 1-1. Add a concrete service-management entry point first: `dan-service` in `pyproject.toml`, with subcommands `install`, `uninstall`, `start`, `stop`, `status`, `logs`, `health`. If a unified top-level `dan` launcher is added later, it should wrap this command rather than duplicate logic.
  - [x] 1-2. Add a small `service_runner` module used by launchd/systemd/manual start so PID management, log setup, and `dan-serve --no-reload` startup all flow through one code path.

- [x] 2. **macOS launchd integration**
  - [x] 2-1. `dan service install` generates plist at `~/Library/LaunchAgents/com.dan.server.plist`.
  - [x] 2-2. Plist should launch the shared service runner with `--host 127.0.0.1 --port 8000`, not call raw `dan-serve` directly, so log rotation and PID bookkeeping stay consistent with manual starts.
  - [x] 2-3. `StandardOutPath`/`StandardErrorPath` → `~/.dan/logs/server.stdout.log` / `server.stderr.log`.
  - [x] 2-4. `dan service uninstall` removes plist and `launchctl bootout`.
  - [x] 2-5. Port configurable via `--port` at install time.
  - [x] 2-6. Detect macOS via `sys.platform == "darwin"`.

- [x] 3. **Linux systemd integration**
  - [x] 3-1. `dan service install` generates unit file at `~/.config/systemd/user/dan-server.service`.
  - [x] 3-2. Unit: `ExecStart=<python> -m dan.server --host 127.0.0.1 --port <port> --no-reload`. `Type=simple`, `Restart=on-failure`, `RestartSec=5`, `WantedBy=default.target`.
  - [x] 3-3. `dan service start` → `systemctl --user start dan-server`.
  - [x] 3-4. Detect Linux via `sys.platform == "linux"`.

- [x] 4. **Service status command**
  - [x] 4-1. `dan service status`: check if process is running (PID file from 26-1 + OS service status).
  - [x] 4-2. Show port, health check result (GET /health).
  - [x] 4-3. Number of active runs (GET /api/gateway/activity if available).
  - [x] 4-4. Rich formatting if Rich is available.

- [x] 5. **Health check watchdog**
  - [x] 5-1. For launchd/systemd, rely on OS-level `KeepAlive`/`Restart` instead of custom watchdog.
  - [x] 5-2. Expose `dan service health` as a manual probe (GET /health, report OK/fail).

- [x] 6. **Log management**
  - [x] 6-1. `dan-service logs` tails rotated files in `~/.dan/logs/`.
  - [x] 6-2. `--follow` for live tailing (like `tail -f`).
  - [x] 6-3. Log rotation in shared service runner before uvicorn launch + on `dan-service start`. Keep the last 5 files.
  - [x] 6-4. Total log budget: 50MB default (`DAN_LOG_MAX_SIZE`).

- [x] 7. **Tests**
  - [x] 7-1. Unit tests for plist/systemd unit generation (template output validation).
  - [x] 7-2. Status command (mock PID file + health check).
  - [x] 7-3. Log rotation + budget enforcement.
  - [x] 7-4. No integration tests requiring actual launchd/systemd (those are manual).

## Files to Touch

| File | Changes |
|------|---------|
| `src/dan/cli/service.py` | New module: `dan service` subcommands, plist/systemd generation, status, logs |
| `src/dan/cli/service_runner.py` | New shared runner used by launchd/systemd/manual service starts |
| `pyproject.toml` | Add `dan-service` entry point |
| `tests/test_cli/test_service.py` | Unit tests for templates, status, log rotation |
| `docs/cli.md` | Document `dan service` commands |
| `docs/architecture.md` | Note service management, log paths |

## Decisions

- Implemented as `dan-service` entry point with 7 subcommands (`install`, `uninstall`, `start`, `stop`, `status`, `health`, `logs`).
- `service.py` has its own PID/health helpers (compatible copies, not imported from `up.py`) to avoid coupling.
- `service_runner.py` provides a shared `run_server()` function for launchd/systemd with log rotation + PID bookkeeping + uvicorn launch.
- Log rotation cascades `.1` through `.5`, budget enforcement deletes oldest files when total exceeds `DAN_LOG_MAX_SIZE` (default 50 MB).
- Status command shows Rich formatting when available, plain text fallback otherwise.

## Notes

- Completed 2026-03-06.
- Depends on 26-1 (PID file, server lifecycle).
- 51 unit tests covering template generation, log rotation, budget enforcement, PID parsing, health checks, status/health/logs commands, parser registration, main entry point, and service runner lifecycle.
