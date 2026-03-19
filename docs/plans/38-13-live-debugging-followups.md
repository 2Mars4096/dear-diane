# 38-13: Live Debugging Follow-Ups

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** completed
**Goal:** Close the next two live-debugging regressions uncovered while validating the March 19 chat/app fixes: zombie PID lifecycle confusion in the CLI launcher path and unknown-model cost crashes on terminal chat completions.

## Tasks
- [x] 1. Harden CLI server lifecycle checks against zombie processes.
 - [x] 1-1. Add a shared non-zombie process-liveness helper for manual/server-management CLI commands.
 - [x] 1-2. Use it in `dan-up`, `dan-down`, `dan-service`, and `dan-status`.
 - [x] 1-3. Add focused regressions for zombie PID handling in `up` and `down`.
- [x] 2. Prevent terminal chat completions from crashing when cost is unknown.
 - [x] 2-1. Guard all `DAN_SHOW_COST` suffix branches so `estimate_cost(...) == None` does not raise.
 - [x] 2-2. Add a focused ChatManager regression for unknown-model terminal completions.

## Decisions
- Treat zombie PIDs as dead everywhere the CLI interprets `~/.dan/server.pid`; plain `kill(pid, 0)` is not enough on Unix after interrupted launcher/chat sessions.
- Preserve `estimated_cost=None` for unknown models instead of fabricating a `0` cost, but never let that block delivery of the final assistant message.

## Notes
- Root cause reproduction on macOS: `~/.dan/server.pid` pointed at a defunct `dan.server` child whose parent `dan-chat` process was still alive, so lifecycle commands saw the PID as existing while `127.0.0.1:8000` refused connections.
- Live validation after the cost fix: a cheap `Reply with OK only.` probe on the running server emitted several `progress_ack` heartbeats and then a normal terminal `chat_complete` with `content="OK"` instead of ending in `chat_error`.
- Validation: `python -m pytest tests/test_cli/test_service.py tests/test_cli/test_up.py tests/test_cli/test_down.py -q` and `python -m pytest tests/test_post_tool_followup_recovery.py -q`.
