# 49-4: Status, Attention, and Notification Contract

**Parent:** [49-concierge-service-hardening](49-concierge-service-hardening.md)
**Status:** not-started
**Goal:** Define task-aware status summaries, attention rules, and notification semantics so the concierge can supervise multiple tasks without interrupting the user noisily or forcing them to reverse-engineer raw stream state.

## Dependencies
- **49-1** must land first: attention states and status queries read from the `TaskRegistry`.
- **49-3** should land or be coordinated: the `waiting_input` state and follow-up classification contract determine which transitions trigger notifications. The stuck-detection sweep (task 4-2) calls back into 49-3's pending-action cleanup when a `waiting_input` task times out.
- **49-2** provides the `task_ack` event and background dispatch model that defines what "completion notification for a backgrounded task" means.
- Consumes the existing `/status` routing in the dispatcher bypass list; this plan replaces the bypass+triage path with a registered fast command that produces structured output.
- **Existing `/status` routing:** Today, `/status` and `status` are dispatcher bypass commands that skip the project queue but still run through full triage + tier executor, producing ad-hoc prose. This plan must register `/status` as a fast command in `command_registry.py` so it returns the structured task-aware format directly, bypassing triage entirely.

## Success Criteria
- `/status` returns a structured task-aware summary listing active, paused, and recently completed tasks with per-task title, state, and age.
- Completion and failure of background tasks produce a notification event that surfaces render appropriately (Telegram bubble, CLI line, editor badge).
- `waiting_input` transitions produce an immediate notification; ordinary `running` progress stays silent by default.
- When multiple tasks complete while the user is away, notifications are coalesced into a single summary (≤5 items inline, remainder as "and N more").
- Stuck/aging detection flags tasks that have been `running` for >10 minutes or `waiting_input` for >30 minutes (configurable) without progress.

## Context

The current system has progress, status commands, and telemetry, but the control-plane semantics are still too low-level:

- active work is easier to observe as stream activity, queue state, or traces than as human-meaningful task state
- completion, failure, and needs-input conditions do not yet flow through one deliberate attention model
- natural-language status questions should eventually read from a task registry, not from ad hoc project memory plus whatever stream is currently active

This plan defines that contract before any frontend task-card surface is added.

## Tasks

- [ ] 1. Define the task attention model
  - [ ] 1-1. Add derived attention flags on `ConciergeTask`: `needs_attention` (bool), `attention_reason` (enum: `needs_input`, `completed_unread`, `failed_unread`, `stuck`, `none`). These are computed from task state + timestamps, not stored separately.
  - [ ] 1-2. Transition → attention mapping: `→running`: silent; `→waiting_input`: immediate notification; `→completed`: immediate if backgrounded, silent if foreground (user already saw the stream); `→failed`: always immediate; `→cancelled`/`→superseded`: silent (user initiated). Batching: if ≥2 tasks complete within a 5-second window, coalesce into one summary notification. Cross-surface policy: notifications are delivered to the surface that originated the task (`creator_surface` on `ConciergeTask`). If the user has switched surfaces since task creation, the notification still goes to the originating surface — cross-surface notification forwarding is deferred to a later phase.
  - [ ] 1-3. Attention is derived from `ConciergeTask.state`, `updated_at`, and `dispatch_mode` — never from websocket connection state or stream activity.
- [ ] 2. Tighten the status query contract
  - [ ] 2-1. Register `/status` as a fast command in `command_registry.py` so it returns structured output directly without passing through triage. Remove `/status` and `status` from the dispatcher `_is_bypass_command` list — the fast-command path already bypasses the project queue. Output format: header line ("N tasks active, M waiting input"), then per-task rows: `• {title} — {state} ({age})`. Non-terminal tasks first, sorted by `updated_at` desc. Recently completed tasks (last 30 min) appended under a "Recently completed" heading. Cap visible rows at 10; overflow as "+ N more".
  - [ ] 2-2. Natural-language status queries ("what's happening", "are you done") are classified as `query_status` by the 49-3 follow-up classifier and routed to the same task-aware summary, not to project memory or ad-hoc assistant prose. The dispatcher bypass list should retain these phrases as bypass routes (they still need to skip the project queue), but the bypass path should check for `query_status` classification and redirect to the registered `/status` handler rather than entering full triage.
  - [ ] 2-3. When only one task is active, `/status` shows a more detailed single-task view: title, state, current attempt number, elapsed time, and last progress line (if available).
- [ ] 3. Define notification semantics
  - [ ] 3-1. Emit a `task_notification` chat event with `{task_id, title, state, attention_reason, summary_line}`. Surface adapters render: Telegram → new message or bubble edit; CLI → progress line; editor → notification badge + optional toast.
  - [ ] 3-2. Coalescing: collect notifications arriving within a 5-second window into a single `task_notification_batch` event with an array of summaries. Render as a grouped message ("2 tasks completed: ...").
  - [ ] 3-3. Detail policy: notification `summary_line` is ≤200 chars (title + state + one-line outcome). Full results are available via `/status {task_id}` or the task's stream history, never pushed in the notification payload.
- [ ] 4. Add telemetry and analytics hooks
  - [ ] 4-1. Emit `task_attention_change` telemetry events (distinct from `task_state_transition` events from 49-1) with `{task_id, old_attention, new_attention, timestamp}`.
  - [ ] 4-2. Stuck/aging detection: a periodic sweep (every 60s) checks `running` tasks with no progress event for >10 min and `waiting_input` tasks with no user reply for >30 min. Thresholds configurable via `DAN_TASK_STUCK_MINUTES` / `DAN_TASK_WAITING_TIMEOUT_MINUTES`. Stuck tasks get `attention_reason: stuck` and emit a telemetry event. The sweep runs as a background `asyncio.Task` started by `Concierge.__init__` (or the runtime startup path) and cancelled on shutdown. For `waiting_input` timeouts, the sweep calls into 49-3's pending-action cleanup to clear the stale action and transition the task to `failed` with `reason: waiting_input_timeout`.
  - [ ] 4-3. Event shapes must align with the 49-5 event vocabulary. Coordinate field names and enums during implementation.
- [ ] 5. Add focused regressions and docs
  - [ ] 5-1. Cover status-query summaries, coalesced completions, and needs-input notification behavior
  - [ ] 5-2. Document the attention budget and notification policy explicitly

## Primary Files

- `src/dan/server/concierge/task_attention.py` — new: attention derivation, stuck detection sweep, notification coalescing
- `src/dan/server/concierge/command_registry.py` — upgrade `/status` to task-aware summary
- `src/dan/server/concierge/runtime/__init__.py` — emit `task_notification` events on state transitions
- `src/dan/server/telemetry.py` — add `task_attention_change` event type
- `tests/test_concierge/test_task_status_notification.py` — new: status output format, coalescing, stuck detection

## Decisions

- **Status is task-first.** Status and natural-language supervision queries should read from task state, not infer from stream presence or the latest assistant response.
- **Attention budget is a concierge concern.** Surface-specific rendering can differ later, but the state transitions that deserve attention should be consistent at the backend contract layer.
- **Ordinary background progress stays quiet.** Completion, failure, and needs-input transitions matter more than verbose heartbeat spam.

## Notes

- This plan should leave the backend ready for future task cards and notifications without forcing frontend work now.
- It also gives `/status` and related follow-up turns a much cleaner data source than today's mixed project/session heuristics.
- **Dispatcher bypass interaction:** The current bypass list in `dispatcher.py` includes `status`, `what's happening`, and similar phrases. These must be coordinated with the new `/status` fast command and the 49-3 `query_status` follow-up type. The cleanest path: keep bypass routing for queue-skipping, but route the bypassed turn to the `/status` fast-command handler instead of full triage when the text matches a status pattern.
- **Cross-surface notification is deferred.** This plan delivers notifications to the originating surface only. A user who starts a task on Telegram and switches to the editor will see the completion notification on Telegram, not the editor. Cross-surface presence tracking and notification forwarding are future work.
