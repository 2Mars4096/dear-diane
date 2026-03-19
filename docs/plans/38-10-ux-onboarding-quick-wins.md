# 38-10: UX & Onboarding Quick Wins

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** in-progress
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
- [ ] 1-1. In `startup.py` or `resolve_config()`, check if `DAN_LLM_API_KEY` equals the placeholder `your-api-key-here` or is empty.
- [ ] 1-2. Emit a clear WARNING with actionable guidance: "Set DAN_LLM_API_KEY in your .env file. See .env.example."
- [ ] 1-3. In `dan-chat` banner, show API key status (configured / missing / placeholder).

### 2. User-friendly error wrapping
- [ ] 2-1. Create an error mapping layer for common HTTP status codes: 401 → "Check your API key", 429 → "Rate limited, retrying...", 404 → "Model not found", 500 → "Server error, check logs".
- [ ] 2-2. Apply the mapping in `cli/chat.py` where raw `RuntimeError(f"Chat API error {resp.status_code}: {resp.text}")` is raised.
- [ ] 2-3. Apply the same mapping in provider error paths so `ChatErrorEvent` contains actionable text.
- [ ] 2-4. Keep raw details available at DEBUG log level.

### 3. Enable cost visibility by default
- [x] 3-1. Change `DAN_SHOW_COST` default from `"0"` to `"1"` so session costs are visible. *(fixed 2026-03-19: chat-manager cost rendering now defaults to on unless `DAN_SHOW_COST=0` is set explicitly)*
- [ ] 3-2. Show cumulative session cost in the `dan-chat` REPL prompt or status line.
- [x] 3-3. Update `.env.example` to reflect the new default. *(already reflected in `.env.example`; retained as enabled-by-default example setting)*

### 4. Hide disabled "coming soon" modes
- [x] 4-1. In `useAppStore.ts`, filter out `enabled: false` modes from the ModeBar rendering instead of showing grayed-out tabs. *(fixed 2026-03-19 in `ModeBar.tsx`)*
- [x] 4-2. Keep the mode definitions in code for future activation. *(fixed 2026-03-19: hidden in UI, definitions retained in `MODE_CONFIGS`)*

### 5. Reduce reassurance delay
- [x] 5-1. Reduce `DAN_CONCIERGE_REASSURANCE_DELAY` default from `10` to `3` seconds. *(fixed 2026-03-19; repeat interval also aligned to `5` seconds to match `.env.example`)*
- [ ] 5-2. Emit a lightweight "Thinking..." acknowledgment immediately when the concierge starts processing, before the reassurance timer fires.

### 6. Wire CLI progress renderer to terminal
- [ ] 6-1. In the `dan-chat` REPL event loop, consume `CLIProgressRenderer.output` and print phase updates to the terminal.
- [ ] 6-2. Respect the verbosity setting (`full`/`compact`/`minimal`).

### 7. Startup degradation summary
- [ ] 7-1. After all startup phases complete, emit a structured summary at WARNING level listing any subsystems that failed to initialize.
- [ ] 7-2. Surface the summary in the `dan-chat` banner alongside the existing model/tier/learning status.

### 8. Create `.env.minimal`
- [ ] 8-1. Create a `.env.minimal` file with only the 3-4 required variables (`DAN_LLM_API_KEY`, `DAN_LLM_MODEL`, optionally `DAN_LLM_PROVIDER`).
- [ ] 8-2. Reference it from the README Quick Start section.

### 9. Reconcile `.env.example` defaults with code defaults
- [ ] 9-1. Audit `.env.example` commented-out values against actual code defaults (e.g., reassurance delay: `.env.example` says 2.0, code says 10).
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

## Estimate

~1.5 days
