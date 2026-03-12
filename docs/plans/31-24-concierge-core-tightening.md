# 31-24: Concierge Core Tightening

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Reduce latency on trivial chat turns and remove adapter-vs-concierge prompt conflicts by making surface identity metadata first-class in the concierge path.

## Tasks
- [x] 1. Add a conservative simple-message fast path before heavy concierge prep.
  - [x] 1-1. Detect only low-risk turns: short social messages plus heuristic direct-task asks (short drafting + direct live lookups).
  - [x] 1-2. Skip the fast path whenever there are pending follow-ups, active goals, attachments, or explicit build/plan/debug modes.
  - [x] 1-3. Reuse the existing `ConversationHandler` / `DirectTaskHandler` so bookkeeping, post-processing, and tool execution stay consistent.
  - [x] 1-4. Add a regression test proving `_retrieve_memory_context()` is skipped for eligible turns.
  - [x] 1-5. Add guardrail regressions so short acknowledgement replies (`yes` / `ok` / `sure`) plus pending/active state still block the fast path.
- [x] 2. Move Telegram bot identity out of injected history system messages.
  - [x] 2-1. Send structured `surface_context` metadata (`identity` + `peers`) from `telegram_fleet.py` to `/api/chat/message`.
  - [x] 2-2. Carry `surface_context` through `app.py` into concierge metadata.
  - [x] 2-3. Render that metadata as extra system instructions from concierge handlers instead of adapter-authored system history.
  - [x] 2-4. Add a regression test ensuring Telegram sends raw conversation history plus structured surface context.
  - [x] 2-5. Add prompt-assembly regressions proving concierge handlers/chat-manager preserve `surface_context` as final system instructions.
- [x] 3. Verify and document.
  - [x] 3-1. Run targeted tests for the concierge fast path, Telegram fleet, and chat-manager-adjacent paths.
  - [x] 3-2. Update changelog, architecture, todo, and bug notes.

## Decisions
- Keep the fast path intentionally narrow. This is a latency optimization for obvious trivial turns, not a second router.
- Do not let adapters inject system messages into `history`. Adapters send structured metadata; the concierge owns the final prompt assembly.

## Notes
- `surface="telegram:<bot>"` already gave chat-manager surface hints the bot name; `surface_context` adds persona, project focus, and peer-bot metadata without competing prompt layers.
- The fast path still records turns and runs normal post-processing, so it is a prep optimization rather than a behavior fork.
- Review-driven follow-up tests exposed one real edge case: because the social-turn detector reused a broader greeting token set, single-word acknowledgements like `yes` were still fast-path eligible until explicit reply-ack exclusions were added.
