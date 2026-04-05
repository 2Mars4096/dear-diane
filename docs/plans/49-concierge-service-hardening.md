# 49: Concierge Service Hardening

**Status:** completed
**Goal:** Make the concierge an always-available intake and supervision layer by introducing first-class task tracking above session trees, background dispatch that does not monopolize chat, task-scoped clarification/status contracts, and backend observability surfaces for future task-card and dispatcher-dashboard UX.

## Problem

The concierge foundations are much stronger after the rewrite and the 38/41 follow-up hardening, but the next product gap is now clearer:

- the durable unit of work is still too message- and session-shaped instead of task-shaped
- same-project execution still feels more blocking than it should because availability is controlled at the dispatcher/session layer, not at a first-class task layer
- pending clarification, retry, and queue behavior are still easier to reason about as project/message flow than as explicit task ownership
- progress exists, but it is still closer to "a stream is active" than "this task is running, paused, or done"
- operational visibility exists in traces and telemetry, but there is no clean backend contract for a manager-style dispatcher dashboard yet

The next hardening slice should therefore stay in concierge and control-plane code, not frontend code. The goal is to make the concierge behave like the always-on manager of work before any task-card or dashboard UI is built on top.

## Scope

In scope:

- a first-class task registry and task lifecycle above root sessions
- background dispatch semantics that let the concierge acknowledge work and return immediately
- task-aware ownership rules for clarification, retry, supersession, and follow-up turns
- a task-centric status, attention, and notification contract
- backend snapshot/stream contracts for future task-card and dispatcher-manager dashboard UX
- focused regressions and acceptance scenarios for multi-task concierge behavior
- all chat surfaces (editor chat, Telegram adapter, CLI) must converge on the same task lifecycle and ownership contracts; surface-specific rendering (Telegram bubble edits, CLI progress lines, editor panel badges) remains an adapter concern but must consume the same backend task state

Out of scope:

- frontend task-card implementation or dashboard UI
- renumbering internal `SessionTier` enums or rewriting the existing tier executor architecture from scratch
- replacing Worker/linter foundations or reopening plans 46/47 as the home for this work
- broad workflow-generation hardening already tracked under the 48-series plans
- restart recovery for in-flight tasks — tasks persisted by 49-1 survive restarts as durable records, but running asyncio tasks do not. On startup, any `ConciergeTask` still in `running` state should be transitioned to `failed` with `reason: process_restart`. Active re-execution or auto-retry of interrupted tasks is deferred to a later phase.

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [49-1](49-1-task-registry-and-lifecycle.md) | Task Registry and Lifecycle | Add a first-class task model and registry above sessions | P1 | completed |
| [49-2](49-2-background-dispatch-and-concierge-availability.md) | Background Dispatch and Concierge Availability | Make the concierge acknowledge and dispatch backgroundable work without blocking new input | P1 | completed |
| [49-3](49-3-clarification-pause-resume-and-task-ownership.md) | Clarification, Pause/Resume, and Task Ownership | Bind follow-up turns and pending actions to explicit tasks | P1 | completed |
| [49-4](49-4-status-attention-and-notification-contract.md) | Status, Attention, and Notification Contract | Define task-aware status summaries, attention rules, and notification semantics | P1 | completed |
| [49-5](49-5-dispatcher-observability-and-dashboard-backend-contract.md) | Dispatcher Observability and Dashboard Backend Contract | Expose backend snapshot/stream contracts for manager-style inspection and future dashboard UX | P2 | completed |

## Dependencies / Sequencing

Recommended execution order:

```text
49-1 (Task Registry and Lifecycle)
  └→ draft 49-5 snapshot/event schemas early so model fields do not drift
  ↓
49-2 (Background Dispatch and Concierge Availability)
  ├→ 49-3 (Clarification, Pause/Resume, and Task Ownership)
  └→ 49-4 (Status, Attention, and Notification Contract)
       ↓
49-5 (Dispatcher Observability and Dashboard Backend Contract)
```

Rationale:

- the task model must exist before background dispatch, pause/resume, or manager-facing visibility can be coherent
- background dispatch changes the concierge's availability contract, so clarification and notification behavior should be tightened against that newer model, not the old project-queue model
- the dashboard/backend contract should be shaped by the actual task registry and state machine rather than exposing today's lower-level session and websocket plumbing directly

## Success Criteria

- the concierge can accept a backgroundable task, acknowledge it immediately (under 500ms p95 from intake to ack delivery), and remain available for new user input while the task runs
- non-trivial work is represented as a durable `ConciergeTask` with stable `task_id`, explicit lifecycle state, and at least one linked root-session attempt
- two unrelated tasks in the same project can coexist without false serialization — only explicit resource/clarification ownership blocks queue drain
- clarification, retry, and supersession behavior attaches to task ownership; follow-up turns resolve to a specific `task_id` rather than guessing from project-level recency
- `/status` and natural-language status queries return a task-aware summary showing active, paused, completed, and failed tasks with per-task age and latest-attempt metadata
- the backend exposes a stable task snapshot and lifecycle-event contract with documented JSON schemas, suitable for future task-card and dispatcher-dashboard UX
- the existing session tree remains available as an execution trace, but tasks become the primary control-plane object
- focused multi-task acceptance scenarios cover at least: concurrent background tasks, clarification pause/resume, retry with preserved identity, supersession, and coalesced completion notification

## Consolidated Outcome

The 49 tranche landed as a backend-only concierge control-plane hardening pass. The product contract is now:

- concierge intake stays available while backgroundable work moves onto a durable `ConciergeTask`
- task identity is explicit and additive (`concierge_task_id`), without colliding with existing project-task IDs
- clarification, retry, supersession, and natural-language status follow-ups resolve against task ownership instead of loose project recency
- `/status`, notifications, and snapshot/event consumers now read from task state rather than inferring from stream presence

Delivered code surfaces:

- `task_registry.py`: durable concierge task model, state machine, attempt history, startup recovery, and task event log
- `task_attention.py`: derived attention reasons, notification summaries/coalescing, and waiting/stuck sweeps
- `task_snapshot.py`: task/dispatcher snapshot schema plus replayable lifecycle-event projection
- `followup_classifier.py`: task-bound follow-up classification and disambiguation prompt generation
- `tiered_dispatch.py`: background `task_ack`, detached execution, task progress recording, and task-state completion wiring
- `runtime/__init__.py`: task binding, dispatch-mode selection, task-aware `/status`, and snapshot/notification plumbing
- `chat_events.py` / `dispatcher.py`: chat event shapes and dispatcher snapshot hook for future manager/dashboard consumers

Focused validation landed with:

- `tests/test_concierge/test_task_registry.py`
- `tests/test_concierge/test_task_snapshot_consistency.py`
- `tests/test_concierge/test_task_ownership.py`
- `tests/test_concierge/test_task_status_notification.py`
- `tests/test_concierge/test_background_dispatch.py`

## Deferreds

Intentionally deferred beyond 49:

- frontend task cards, task rail, and dispatcher-manager dashboard UI
- cross-surface notification forwarding and presence-aware delivery
- automatic retry/restart of interrupted background tasks after process restart
- larger structural narrowing of concierge/runtime modules, which now belongs to the 50-series consolidation plans

## Decisions

- **Task is the user-facing control-plane object.** Session trees remain execution traces and execution envelopes.
- **Concierge stays always-on in product semantics.** This plan does not require renumbering internal tiers; it hardens availability and ownership behavior first.
- **Task cards and dashboard UX are downstream consumers.** This plan defines their backend contract but does not implement frontend surfaces yet.
- **Serialization moves down a level.** Project-level blocking should narrow toward task-level ownership and resource policy, not remain the default intake rule. The architecture already notes the dispatcher queue key can be extended from `project_id` to `project_id:task_id` as an explicit opt-in (see `docs/architecture.md` queue-identity note).
- **Task storage starts in-process.** The task registry is an in-memory dict with project-store-backed persistence for durability across restarts. No new database dependency in this phase.
- **Restart recovery is cold-start cleanup, not auto-retry.** On startup, zombie `running` tasks are marked `failed` with a `process_restart` reason. Auto-retry is a separate future concern.
- **Surface adapter convergence is a cross-cutting obligation.** Each sub-plan that emits new chat events (`task_ack`, `task_notification`, status summaries) must specify the adapter rendering contract. Adapter-side changes are implementation work inside each sub-plan, not a separate sub-plan.

## Notes

- This plan is the backend-only continuation of the concierge hardening line that already passed through plans 34, 38-8, and 41-3.
- The intended future UX is still consistent with the "one concierge, many workers" model, but this plan deliberately starts with backend truth and lifecycle contracts before any visual redesign.
- The "dispatcher manager dashboard" idea is explicitly preserved here as a backend-contract target so later UI work can consume a deliberate shape instead of reverse-engineering traces, queues, and websocket state.
- Landed implementation summary: `task_registry.py` / `task_snapshot.py` / `task_attention.py` now define the concierge task truth, `followup_classifier.py` and richer `PendingAction` metadata bind retry/refine/clarification semantics to explicit task IDs, `tiered_dispatch.py` issues `task_ack` and detached background execution, `runtime/__init__.py` provides task-aware `/status` and notification plumbing, and `dispatcher.py` exposes a snapshot-oriented summary hook for future dashboard consumers. This parent file is the consolidated landing record for the 49-1 through 49-5 family.
- **Existing `TriageResult.resume_task_id`:** The triage model already carries a `resume_task_id` field. Sub-plans (especially 49-3 follow-up classification) should leverage this as the triage-layer handoff for task binding rather than inventing a parallel mechanism.
- **Existing `Task` model coexistence:** `models.py` has a `Task` dataclass nested inside `Project.tasks[]` with `task_id`, `label`, `status`, `turns`, etc. The new `ConciergeTask` is intentionally separate (concierge-level intake/lifecycle above project tasks). During implementation, `Project.current_task_id` should reference the project-level `Task`, while `ConciergeTask.task_id` references the concierge-level task. Both IDs appear in `ResolvedContext` — ensure naming avoids ambiguity (e.g. `concierge_task_id` vs `project_task_id` in contexts where both are present).
