# 38-10: UX & Onboarding Quick Wins

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** completed
**Goal:** Address the highest-impact user experience and onboarding issues from the 2026-03-19 user experience review: API key validation, error messaging, cost visibility, UI honesty, and feedback timing.

## Context

- The UX review found 4 P1 and 14 P2 issues. This plan targets the items with the best effort-to-impact ratio.
- No API key validation at startup — users get a cryptic 401 on first LLM call.
- Raw HTTP status codes and provider errors are shown as user-facing messages.
- Cost display is opt-in (`DAN_SHOW_COST=1`) — users can accumulate costs unknowingly.
- Two "coming soon" modes (Analytics, Content) are visible in primary navigation.
- 10-second silence before any reassurance feedback.
- CLI progress renderer output is never consumed.

## Tasks

### 1. API key placeholder detection at startup
- [x] 1-1. In `startup.py` or `resolve_config()`, check if `DAN_LLM_API_KEY` equals the placeholder `your-api-key-here` or is empty.
- [x] 1-2. Emit a clear WARNING with actionable guidance: "Set DAN_LLM_API_KEY in your .env file. See .env.example."
- [x] 1-3. In `dan-chat` banner, show API key status (configured / missing / placeholder).

### 2. User-friendly error wrapping
- [x] 2-1. Create an error mapping layer for common HTTP status codes: 401 → "Check your API key", 429 → "Rate limited, retrying...", 404 → "Model not found", 500 → "Server error, check logs".
- [x] 2-2. Apply the mapping in `cli/chat.py` where raw `RuntimeError(f"Chat API error {resp.status_code}: {resp.text}")` is raised.
- [x] 2-3. Apply the same mapping in provider error paths so `ChatErrorEvent` contains actionable text. *(2026-03-19: added `_friendly_chat_error()` helper in `chat_manager.py` that maps KeyError/401/429/timeout/connection/5xx patterns to actionable messages. Applied to all 5 raw `ChatErrorEvent` sinks in `chat_manager.py` and the router-level `chat_error` in `routers/chat.py`. Regression tests in `test_phase31_review_hardening.py`.)*
- [x] 2-4. Keep raw details available at DEBUG log level.

### 3. Enable cost visibility by default
- [x] 3-1. Change `DAN_SHOW_COST` default from `"0"` to `"1"` so session costs are visible. *(fixed 2026-03-19: chat-manager cost rendering now defaults to on unless `DAN_SHOW_COST=0` is set explicitly)*
- [x] 3-2. Show cumulative session cost in the `dan-chat` REPL prompt or status line.
- [x] 3-3. Update `.env.example` to reflect the new default. *(already reflected in `.env.example`; retained as enabled-by-default example setting)*

### 4. Hide disabled "coming soon" modes
- [x] 4-1. In `useAppStore.ts`, filter out `enabled: false` modes from the ModeBar rendering instead of showing grayed-out tabs. *(fixed 2026-03-19 in `ModeBar.tsx`)*
- [x] 4-2. Keep the mode definitions in code for future activation. *(fixed 2026-03-19: hidden in UI, definitions retained in `MODE_CONFIGS`)*

### 5. Reduce reassurance delay
- [x] 5-1. Reduce `DAN_CONCIERGE_REASSURANCE_DELAY` default from `10` to `3` seconds. *(fixed 2026-03-19; repeat interval also aligned to `5` seconds to match `.env.example`)*
- [x] 5-2. Emit a lightweight "Thinking..." acknowledgment immediately when the concierge starts processing, before the reassurance timer fires. *(fixed 2026-03-19: `Concierge._process_inner()` now emits the intake phase immediately, and the delayed reassurance queue treats that first phase ack as real feedback instead of suppressing it until timeout.)*

### 6. Wire CLI progress renderer to terminal
- [x] 6-1. In the `dan-chat` REPL event loop, consume `CLIProgressRenderer.output` and print phase updates to the terminal. *(2026-03-19: added `_CliProgressDisplay` in `cli/chat.py`, which routes streamed `progress_ack` events through the shared `CLIProgressRenderer` and prints the new renderer output lines in both foreground and queued/background REPL streams instead of hard-coded `[progress]` strings.)*
- [x] 6-2. Respect the verbosity setting (`full`/`compact`/`minimal`). *(2026-03-19: `_CliProgressDisplay` now resolves CLI verbosity through `resolve_verbosity("cli")`; `minimal` prints only the first progress update, `compact` dedupes repeated phase labels, and `full` also renders heartbeat/reassurance lines.)*

### 7. Startup degradation summary
- [x] 7-1. After all startup phases complete, emit a structured summary at WARNING level listing any subsystems that failed to initialize. *(2026-03-19: `startup.py` now records degraded subsystems into `AppState.startup_degradations` and logs a consolidated `Startup degradation summary` warning after phased startup completes.)*
- [x] 7-2. Surface the summary in the `dan-chat` banner alongside the existing model/tier/learning status. *(2026-03-19: `/health` now returns `startup: {status, issues}`, `ChatClient.get_health()` fetches it, and the CLI banner shows `Startup: ok` or `Startup: degraded (...)` on a second line.)*

### 8. Create `.env.minimal`
- [x] 8-1. Create a `.env.minimal` file with only the 3-4 required variables (`DAN_LLM_API_KEY`, `DAN_LLM_MODEL`, optionally `DAN_LLM_PROVIDER`). *(2026-03-19: created `.env.minimal` with `DAN_LLM_API_KEY`, `DAN_LLM_BASE_URL`, `DAN_LLM_MODEL` plus commented provider-specific alternatives.)*
- [x] 8-2. Reference it from the README Quick Start section. *(2026-03-19: README Configure section now offers `cp .env.minimal .env` as the fastest path, with full `.env.example` as the comprehensive alternative.)*

### 9. Reconcile `.env.example` defaults with code defaults
- [x] 9-1. Audit `.env.example` commented-out values against actual code defaults (e.g., reassurance delay: `.env.example` says 2.0, code says 10). *(2026-03-19: audited all `.env.example` defaults against code. Fixed `docs/cli.md` `DAN_SHOW_COST` default from `0` to `1` to match code. Reassurance delay/interval already aligned at 3/5. Added missing `DAN_SANDBOX_SHELL` and `DAN_STRICT_SANDBOX` to `.env.example`.)*
- [x] 9-2. Fix discrepancies so the documented defaults match the code. *(fixed 2026-03-19 for concierge reassurance defaults: code now matches `.env.example` at 3s initial / 5s repeat)*

## Primary Files

- `src/dan/server/startup.py`
- `src/dan/cli/chat.py`
- `src/dan/cli/__init__.py`
- `src/dan/server/concierge/runtime.py`
- `src/dan/server/chat_manager.py`
- `editor/src/store/useAppStore.ts`
- `.env.example`

## Decisions

- Cost display on by default — users should know what they're spending. Power users can opt out with `DAN_SHOW_COST=0`.
- Coming-soon modes: hide completely, not grayed out. Grayed tabs signal broken software.
- Reassurance: immediate lightweight ack + 3s first reassurance is better than 10s of silence.

## Notes

- Added startup-warning coverage in `tests/test_concierge/test_phase31_review_hardening.py` so missing/placeholder API keys and explicit shell sandbox opt-outs stay visible.
- Startup API-key detection now treats supported provider-specific keys (`DAN_OPENAI_API_KEY`, `DAN_ANTHROPIC_API_KEY`, `DAN_GOOGLE_API_KEY`, plus legacy fallbacks) as configured too, so the CLI banner and startup warnings no longer falsely report "missing" on valid non-`DAN_LLM_API_KEY` setups.
- Concierge progress UX now emits the initial "Understanding your request" phase immediately even on the delayed reassurance path, so CLI/editor users get feedback before the first 3s timer fires.
- `dan-chat` now surfaces API-key status in the startup banner, accumulates session cost in the REPL/status line, maps common HTTP errors to more actionable messages, and prints streamed `progress_ack` phase labels instead of silently dropping them.
- `dan-chat` now renders streamed progress through the shared `CLIProgressRenderer` formatting layer, and the CLI banner also shows whether the connected server startup is fully healthy or degraded (based on `/health` startup summary data).

## Estimate

~1.5 days
