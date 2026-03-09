# 31: Daily-Use Quality-of-Life & Power-Ups

**Status:** completed
**Goal:** Make everything that's built actually work smoothly for daily use. Activate dormant features, add missing control surfaces, improve visibility, expose hidden capabilities, and polish the rough edges that create friction every session.

## Problem

After 20 phases of infrastructure, the platform is deep but rough around the edges for daily use:

- **Model tiering exists but is never turned on.** `TierPolicy`, 4 tiers, 3 scorers, escalation/de-escalation — all built, zero production activation.
- **No way to switch models mid-conversation.** Changing model requires editing `.env` and restarting the server.
- **Six self-evolvement features are gated OFF.** Prompt optimization, model learning, topology learning, skill learning, LLM memory extraction, dual-write — all behind env vars defaulting to 0.
- **13 built-in tools not exposed in chat.** `python_eval`, `csv_read`, git tools, `compress`, `notify`, file ops, `text_diff` — built but no capability handlers.
- **No cost visibility in chat.** Workflow runs show cost; chat messages don't.
- **Error handling in chat is raw.** No retry, no friendly messages, no "try again" affordance.
- **Notifications don't fire for chat-initiated work.** Only gateway/adapter runs notify reliably.
- **CLI lacks pipe/one-shot mode.** No `dan ask`, no `--pipe`, no `--output`.
- **`.env.example` documents 3 of 30+ env vars.** New users can't discover features.
- **Memory is read-only from chat.** `/memory-stats` and `/memory-search` exist, but no delete/edit/confirm.
- **`set_config` can't change the model.** `DAN_LLM_*` prefixes are blocked.
- **No `get_config`.** Users can't ask "what model am I using?"

## Sub-Plans

| # | Plan | Scope | Est. |
|---|------|-------|------|
| 31-1 | [Model Control](31-1-model-control.md) | `/model` command, TierPolicy activation, `get_config`/`set_config` expansion, per-session model override | 1.5d |
| 31-2 | [Visibility & Feedback](31-2-visibility-feedback.md) | Chat cost tracking, notification wiring, error retry UX, run status from chat, model/config in surface hints | 1.5d |
| 31-3 | [Capability Exposure](31-3-capability-exposure.md) | Expose 13 built-in tools as chat capabilities, workflow introspection tools, learning feature activation | 1.5d |
| 31-4 | [Power-User Speed](31-4-power-user-speed.md) | CLI pipe/one-shot mode, memory management commands, file handling UX, prep timeout controls | 1d |
| 31-5 | [Defaults & Docs](31-5-defaults-and-docs.md) | `.env.example` overhaul, startup profiles, feature bundles, CLI ref, LLM API guide, architecture, README, dev plan | 1d |

Order: 31-1 → 31-2 (model control first, then visibility relies on it) → 31-3 and 31-4 (independent, can run in parallel) → 31-5 (docs sweep last, covers everything added).

## Key Design Decisions

- **No new architecture.** Every item plugs into existing patterns (`ChatCapabilityRegistry`, fast commands, `CapabilityContext`, env vars, `ChatManager` fields).
- **Backward compatible.** All new features are opt-in or additive. Existing `.env` files, workflows, and chat sessions work unchanged.
- **Chat-first.** Every control surface works from chat (WhatsApp, Telegram, CLI, editor). No server-only or editor-only features.
- **Activate, don't rebuild.** For dormant features (tier scoring, learning loops), the work is wiring + env vars + docs — not reimplementation.

## Dependencies

- Existing: `ChatCapabilityRegistry`, `ModelSelector`, `TierPolicy`, `CostTracker`, `NotificationManager`, `MemoryKernel`, `ConcurrentDispatcher`, adapter framework, `identity.py`
- No new external dependencies.

## Cross-References Between Subplans

- **31-1 → 31-4**: `/model` fast command (31-1) is reused by `--model` CLI flag (31-4 task 1-4). 31-4 depends on 31-1 for this.
- **31-1 → 31-2**: `get_config` (31-1) provides model/config data that `/status` (31-2) reuses.
- **31-3 → 31-5**: `DAN_LEARNING_MODE` bundle is implemented in 31-3 task 3 and documented in 31-5 task 3. `DAN_FULL_TOOLS` is implemented in 31-3 (gate the 13 new tool registrations behind it) and documented in 31-5.
- **31-1 + 31-2 + 31-3**: All three extend `CapabilityContext` in `capability_registry.py` (`chat_manager` for 31-1, `event_bus` for 31-2, `test_case_store` for 31-3). Coordinate additions.
- **31-1 → 31-5**: All new env vars from 31-1 (`DAN_ENABLE_TIER_POLICY`, `DAN_TIER_MAP`) are documented in 31-5's `.env.example`.
- **31-2 → 31-5**: All new env vars from 31-2 (`DAN_SHOW_COST`) and commands (`/cost`, `/status`, `/retry`) are documented in 31-5.

## Non-Goals (this phase)

- User system / multi-user auth
- New node types or engine features
- Frontend editor changes (this phase is backend + chat + CLI only)
- Discord adapter
