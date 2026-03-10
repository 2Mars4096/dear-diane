# 31: Daily-Use Quality-of-Life & Power-Ups

**Status:** completed
**Goal:** Make everything that's built actually work smoothly for daily use. Activate dormant features, add missing control surfaces, improve visibility, expose hidden capabilities, polish rough edges, and add execution intelligence, safety, and continuity features.

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
- **No goal-oriented execution.** Can't say "iterate until score >= X or after 24h."
- **No scheduled tasks.** Every task requires human initiation.
- **Plans execute linearly.** No dependency-aware parallel scheduling.
- **No completeness validation.** DAN may silently drop requirements from multi-part requests.
- **PII sent to external APIs.** No way to mask sensitive data before LLM calls.
- **No cross-session resume.** Returning users must re-explain context.
- **No proactive follow-up.** DAN never initiates conversation.
- **No multi-surface continuity.** Switching surfaces loses context.
- **No progressive response feedback.** Users wait in silence during complex tasks — no plan disclosure, no phase progress, no interactive checkpoints.
- **Learning is architecturally complete but practically dormant.** Active self-evolvement features default to off, quality signals are too coarse, user corrections aren't captured, and learning can fail silently.

## Sub-Plans

| # | Plan | Scope | Est. |
|---|------|-------|------|
| 31-1 | [Model Control](31-1-model-control.md) | `/model` command, TierPolicy activation, `get_config`/`set_config` expansion, per-session model override | 1.5d |
| 31-2 | [Visibility & Feedback](31-2-visibility-feedback.md) | Chat cost tracking, notification wiring, error retry UX, run status from chat, model/config in surface hints | 1.5d |
| 31-3 | [Capability Exposure](31-3-capability-exposure.md) | Expose 13 built-in tools as chat capabilities, workflow introspection tools, learning feature activation | 1.5d |
| 31-4 | [Power-User Speed](31-4-power-user-speed.md) | CLI pipe/one-shot mode, memory management commands, file handling UX, prep timeout controls | 1d |
| 31-5 | [Defaults & Docs](31-5-defaults-and-docs.md) | `.env.example` overhaul, startup profiles, feature bundles, CLI ref, LLM API guide, architecture, README, dev plan | 1d |
| 31-6 | [Goal-Oriented Loop](31-6-goal-oriented-loop.md) | Autonomous iteration until target metric met or deadline expires (Kaggle-style improvement loops) | 2-3d |
| 31-7 | [Scheduled Tasks](31-7-scheduled-tasks.md) | Cron-style and interval-based task scheduling, `/schedule` commands, result delivery | 1.5d |
| 31-8 | [Plan Dependency Optimization](31-8-plan-dependency-optimization.md) | Standalone RCPSP solver: exact (n≤50) / heuristic (n>50), dual-caller (concierge + workflow engine), dynamic rescheduling | 3-4d |
| 31-9 | [Completion Guard](31-9-completion-guard.md) | Pre-delivery requirement extraction and completeness validation | 1.5-2d |
| 31-10 | [PII Tokenization](31-10-pii-tokenization.md) | Sensitive data masking with semantic placeholders before LLM API calls | 2d |
| 31-11 | [Cross-Session Resume](31-11-cross-session-resume.md) | Structured task state persistence and one-command resume across sessions | 1.5d |
| 31-12 | [Proactive Follow-Up](31-12-proactive-follow-up.md) | DAN-initiated follow-up messages on completions, stale tasks, and discoveries | 1.5-2d |
| 31-13 | [Multi-Surface Continuity](31-13-multi-surface-continuity.md) | Cross-surface conversation context sharing anchored on Project scope | 1-1.5d |
| 31-14 | [Progressive Response UX](31-14-progressive-response-ux.md) | Phase-chunked progressive disclosure: instant ack, plan disclosure, phase transitions, result checkpoints, surface-adaptive verbosity | 2-3d |
| 31-15 | [Learning & Evolution Optimization](31-15-learning-evolution-optimization.md) | Tiered learning activation, upgraded quality signals, correction memory loop, learning health visibility, unified adaptation governance, storage backend abstraction, planning-time calibration | 3-4d |
| 31-16 | [Command Surface Unification](31-16-command-surface-unification.md) | Canonical command registry, unified dispatch, surface-aware `/help`, centered `docs/commands.md`, adapter/REPL integration | 2-3d |
| 31-17 | [Computer Control & Browser Automation](31-17-computer-control-and-browser-automation.md) | Narrow v1: Playwright-first browser automation, browser-owned native-dialog handoff, minimal macOS desktop fallback, and safety-first policy | 5-7d |

Order: 31-1 through 31-5 completed. For 31-6 through 31-17:
- **Foundation (land first):** 31-16 (command registry) — **must** land before any plan introducing new commands. If that's not practical, those plans include fallback wiring instructions with an explicit TODO for registry migration.
- **Independent (can run in parallel after 31-16):** 31-6, 31-8, 31-9, 31-10, 31-14, core 31-15 tasks (1-6), 31-17 v1 core runtime
- **Sequential:** 31-7 before 31-12 (scheduled tasks enable scheduled follow-up sources; 31-12 degrades gracefully if 31-7 absent)
- **Sequential:** 31-11 before 31-12 and 31-13 (structured task snapshots enable stale/blocker follow-ups and task-first cross-surface continuity)
- **31-8** is the largest and most independent — can start in parallel with 31-16
- **31-14** is mostly independent — builds on existing streaming/edit infrastructure; quiet-hours from 31-12 do NOT apply to in-flight user-requested progress updates
- **31-15** core hardening is independent; task 7's planning-time calibration integrates with 31-8 when both are implemented
- **31-17** v1 core is mostly independent; only its minimal `/computer status|doctor|approve` surface wants 31-16. Richer `/computer allow|deny` policy editing is an explicit follow-on slice after the core runtime proves reliable
- **31-12 → 31-13 routing integration:** 31-12 ships with simple surface routing; 31-13 provides the richer routing policy that 31-12 should upgrade to once available
- **31-9 + 31-10 + 25-12 response pipeline:** all three hook into the concierge response path — should be formalized as a `ResponsePostProcessor` middleware chain rather than three ad-hoc insertion points

## Key Design Decisions

- **No new architecture (31-1 through 31-5).** Every item plugs into existing patterns (`ChatCapabilityRegistry`, fast commands, `CapabilityContext`, env vars, `ChatManager` fields).
- **Execution intelligence (31-6 through 31-8).** New execution modes (goal loop, scheduled tasks, RCPSP scheduler) that compose with existing engine primitives. 31-8 is a **standalone solver** — one module called by both concierge (chat tasks) and workflow engine (graph execution). Philosophy: plan optimistically, validate mechanically, fix dynamically.
- **Safety and completeness (31-9 through 31-10).** Pre-delivery validation and PII protection — hooks into existing LLM/chat call paths.
- **Continuity (31-11 through 31-13).** Cross-session and cross-surface context sharing via the existing Project/Task model, with task-first handoff and private-surface defaults.
- **Progressive UX (31-14).** Phase-chunked progress disclosure with surface-adaptive rendering. Deterministic for structure/timing, LLM for content/framing.
- **Learning optimization (31-15).** Make the existing 5-layer learning architecture practically effective: tiered activation, precision signals, user-correction feedback, observable health, unified governance, storage abstraction, planning-time calibration.
- **Computer use (31-17).** Add a layered control stack: Playwright-first browser automation, browser-owned native-dialog handoff, minimal macOS desktop fallback, observe→act→verify execution loop, and chunked allowlists/approval gates. Note: execution is host-local on a GUI-capable machine, even if the request originated from any chat surface.
- **Backward compatible.** All new features are opt-in or additive. Existing `.env` files, workflows, and chat sessions work unchanged.
- **Chat-first request surface.** Every feature should be invokable from chat (WhatsApp, Telegram, CLI, editor), but some features may execute only where the right host capabilities exist. `31-17` computer use is the main example: request from any surface, execute only on a GUI-capable local host with the required OS permissions.

## Dependencies

- Existing: `ChatCapabilityRegistry`, `ModelSelector`, `TierPolicy`, `CostTracker`, `NotificationManager`, `MemoryKernel`, `ConcurrentDispatcher`, adapter framework, `identity.py`
- 31-6 through 31-8: `WhileLoop`, `ParallelSubagentsExecutor`, `ResourceBudget`, `RepairClassifier`, `PlanBuilder`, `ExperienceStore`
- 31-9 through 31-10: `LLMExecutor`, `ProviderRegistry`, `Concierge`
- 31-11 through 31-13: `ProjectStore`, `ConversationMemoryStore`, `ActivityTracker`, `GlobalEventBus`
- 31-17: existing `screenshot` / `clipboard` capabilities, OS accessibility APIs, computer-use lease/approval policy
- Optional external: `ortools` (31-8, for exact RCPSP solver), `croniter` (31-7, for cron expression parsing), `playwright` (31-17, browser automation backend)

## Cross-References Between Subplans

- **31-1 → 31-4**: `/model` fast command (31-1) is reused by `--model` CLI flag (31-4 task 1-4). 31-4 depends on 31-1 for this.
- **31-1 → 31-2**: `get_config` (31-1) provides model/config data that `/status` (31-2) reuses.
- **31-3 → 31-5**: `DAN_LEARNING_MODE` bundle is implemented in 31-3 task 3 and documented in 31-5 task 3. `DAN_FULL_TOOLS` is implemented in 31-3 (gate the 13 new tool registrations behind it) and documented in 31-5.
- **31-1 + 31-2 + 31-3**: All three extend `CapabilityContext` in `capability_registry.py` (`chat_manager` for 31-1, `event_bus` for 31-2, `test_case_store` for 31-3). Coordinate additions.
- **31-1 → 31-5**: All new env vars from 31-1 (`DAN_ENABLE_TIER_POLICY`, `DAN_TIER_MAP`) are documented in 31-5's `.env.example`.
- **31-2 → 31-5**: All new env vars from 31-2 (`DAN_SHOW_COST`) and commands (`/cost`, `/status`, `/retry`) are documented in 31-5.

## Cross-References Between Subplans (31-6 through 31-17)

- **31-7 → 31-12**: Scheduled tasks enable proactive follow-ups (scheduled task results trigger follow-up messages)
- **31-11 → 31-12**: Structured task snapshots from cross-session resume are required for stale-task / blocker-resolved follow-ups
- **31-11 → 31-13**: Cross-session resume state is required for multi-surface continuity (structured task state is the handoff payload)
- **31-6 → 31-8**: Goal loops can use the RCPSP scheduler for internal iteration planning (each attempt could be a sub-plan)
- **31-9 → 31-12**: Completion guard flags missed requirements; proactive follow-up can offer to address them later
- **31-8 → 31-6**: Plan dependency optimization can schedule goal loop attempts alongside other work
- **31-14 → all execution plans**: Progressive response applies to any long-running execution — goal loops (31-6), scheduled tasks (31-7), RCPSP-scheduled work (31-8)
- **31-14 → 31-2**: Supersedes generic reassurance messages from 29-2; builds on streaming/notification infrastructure from 31-2
- **31-15 → 31-8**: Planning-time calibration (31-15 task 7) feeds duration estimates and model priors into 31-8's RCPSP scheduler once both exist
- **31-15 → 31-3**: Supersedes binary `DAN_LEARNING_MODE` bundle (31-3) with tiered `DAN_LEARNING_TIER`
- **31-15 → 31-2**: Learning health section added to `/status` command from 31-2
- **31-16 → all plans with new commands**: Registry eliminates manual wiring for `/goal`, `/schedule`, `/resume`, `/follow-ups`, `/pii`, `/sync`, `/progress`, `/completion`, `/corrections`, `/adaptations`, `/computer`
- **31-17 → 31-14**: Computer-use tasks are long-running, multi-phase operations that should emit progressive response updates ("opening browser", "waiting for download", "switching to file dialog")
- **31-17 → 31-10**: Screenshot/OCR content sent to external LLMs must pass through provider-boundary PII tokenization
- **31-17 → 31-16**: Minimal `/computer status|doctor|approve` commands should register through the command registry when available; broader policy-edit commands are intentionally deferred

## Non-Goals (this phase)

- User system / multi-user auth
- Frontend editor changes (31-1 through 31-5 were backend + chat + CLI only; 31-6+ may include light editor additions)
- Discord adapter
- Enterprise-grade DLP (31-10 is pragmatic PII protection, not compliance-grade)

## Post-Review Hardening

- Follow-up review fixes tightened the Phase 21 runtime after the main wiring landed.
- Safety/correctness patches included AppleScript quoting for desktop control, including OCR screenshot-path quoting, trigger-callback downloads for browser automation, broader `TriggerContext.source_surface` support, and stateful `/goal` fast commands.
- Scheduler/continuity/learning hardening also removed synthetic schedule task IDs, restored project-only trigger-context resolution, set `~/.dan/sensitive_words.json` to `0600`, rejected unsafe SQLite JSON keys, and ignored weak correction signals.
- Final cleanup also made concierge fast-command dispatch honor registry-resolved handlers (including registry-only chat commands), exposed public `ExecutionContext` session keys for PII wrapping, made executor-side PII wrapping fail closed when protection is enabled, lazy-loaded learning command dependencies, preserved fresh pending fleet locks during PID-write races while still capping retry loops, and stopped Telegram menus from advertising CLI-only REPL commands like `/show` and `/list`.
- Residual cleanup after the final review also fixed two remaining cross-session leaks: project-only scheduled trigger contexts now resolve and persist correctly across synthetic scheduled `external_id` values, and `/progress` verbosity overrides are scoped to the requesting `external_id` instead of process-global state.
- Live routing cleanup also hardened concierge intent handling for real chat traces: the classifier now uses a hybrid heuristic-first/LLM-second flow, topical `status/progress of X` requests stay on the normal conversation path instead of falling into run/activity status handlers, generic-default-provider deployments reuse the configured chat model for classification instead of unsupported provider-tier aliases, and empty experience-history lookups fall back to a helpful chat answer instead of ending with `"No similar workflows found."`
- A subsequent audit patch closed a few smaller but still user-visible seams: adapter `/help` now filters by the actual messaging surface, adapters preserve raw `/status` and `/cancel` so the server's fast-command path stays active, dispatcher bypass follows the command registry for all chat commands, `/cancel` now has a real registry-backed fast handler, and completion-guard follow-up text for missed actions/deliverables is no longer silently dropped.
- Coverage hardening on top of those fixes closed the remaining scheduled-dispatch regression gap, including the explicit cross-surface `project_id + task_id` scheduled path, but it did **not** eliminate the broader Phase 21 audit follow-up slice. The still-open live wiring remains: real `/goal` execution via `GoalLoopExecutor`, emitting schedule-result and run-completion proactive follow-up triggers, driving `ProgressSession` from real phase events beyond session initialization/verbosity, and exposing computer-use actions through the chat capability/controller layer instead of `/computer` policy commands alone.
- Final live-memory cleanup closed the last dormant-learning gap: `DAN_MEMORY_EXTRACTION_LLM` now accepts real model names instead of only `1`, both sync and async concierge memory paths call `extract_with_llm()` with heuristic fallback, and a fresh restarted server was re-verified live to write durable `fact` + `preference` memories while `/corrections` and `/adaptations` return registry-backed learning status replies.
