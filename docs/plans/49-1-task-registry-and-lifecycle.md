# 49-1: Task Registry and Lifecycle

**Parent:** [49-concierge-service-hardening](49-concierge-service-hardening.md)
**Status:** not-started
**Goal:** Introduce a first-class concierge task model and registry above root sessions so long-running, background, paused, retried, or superseded work has stable identity and explicit lifecycle state.

## Dependencies
- None (foundation sub-plan for the 49 family).
- **49-2** through **49-4** all consume the `ConciergeTask` model, lifecycle states, and registry API defined here.
- **49-5** consumes the task model for snapshot/event schemas. Per parent sequencing: draft 49-5 snapshot and event field shapes early during 49-1 implementation so model fields do not drift. Task 4-3 explicitly covers this coordination.
- The existing `ResolvedContext` / `ProjectStore` / `Task` scope described in `docs/architecture.md` is the current project-level context; this plan adds a task layer above it without replacing it.
- **Existing `TriageResult.resume_task_id`** already carries a task-binding hint from the triage layer. Task 3-2 should wire this field into `ConciergeTask` lookup so triage-initiated task binding flows into task creation/resume without a separate mechanism.

## Success Criteria
- A `ConciergeTask` dataclass exists with stable identity, explicit lifecycle states, and ownership metadata.
- Every non-ephemeral concierge turn creates or binds to a `ConciergeTask`; ephemeral turns (social, factual ask, fast-command) do not.
- Task state transitions are validated (no illegal jumps) and persisted to the project store.
- Prior root-session attempts are preserved on the task record so retry/resume history is explicit.
- Pruning retains the last N completed tasks per project (start with N=50) plus all non-terminal tasks.
- `task_id` appears in turn metadata, session exports, and telemetry alongside existing `project_id`/`session_id`.

## Context

The current concierge runtime has strong session and trace primitives, but the primary durable unit is still the root session created from a user message. That is not enough for the operating model we want next:

- a single user-visible task may span multiple root-session attempts because of clarification, retry, or supersession
- background work should be trackable even when the original stream is no longer the active surface
- status and notification logic should talk about tasks, not raw sessions or channel IDs

This plan creates that missing control-plane layer.

## Tasks

- [ ] 1. Define the concierge task model
  - [ ] 1-1. Add a `ConciergeTask` dataclass with fields: `task_id` (prefixed UUID, e.g. `task_<uuid4_short>`), `project_id`, `title` (human-facing, ≤120 chars), `summary` (one-line intent), `state` (enum), `created_at`, `updated_at`, `creator_surface` (cli/editor/telegram), `root_session_attempts` (list of `{session_id, started_at, outcome}`), `pending_action_id` (nullable, links to active clarification/confirmation), `superseded_by` (nullable `task_id`)
  - [ ] 1-2. Define the canonical task state enum: `queued`, `running`, `waiting_input`, `completed`, `failed`, `cancelled`, `superseded`. Define the legal state transition graph (e.g. `queued→running→completed|failed|cancelled`, `running→waiting_input→running`, `*→superseded`). Illegal transitions raise `ValueError`.
  - [ ] 1-3. Separate task state from session state so tasks can outlive one root-session attempt. A `ConciergeTask` holds a list of attempt records; each attempt links to a root `session_id` and records its outcome (`completed`, `failed`, `cancelled`).
- [ ] 2. Add a task registry layer
  - [ ] 2-1. Create `TaskRegistry` with operations: `create(project_id, title, summary, surface) → ConciergeTask`, `get(task_id)`, `list(project_id, states=None, limit=20)`, `transition(task_id, new_state, metadata=None)`, `link_attempt(task_id, session_id)`, `supersede(old_task_id, new_task_id)`
  - [ ] 2-2. Storage: in-memory dict keyed by `task_id`, with project-store-backed JSON persistence. Persistence format: a single `tasks.json` file per project under `~/.dan/projects/{project_id}/tasks.json` containing the full task list (not one file per task). Load on first access per project; write-through on state transitions. Single-file format keeps atomic writes simple and avoids directory-scan overhead for the expected task counts (≤100 retained per project).
  - [ ] 2-3. Pruning: retain all non-terminal tasks plus the last 50 completed/failed/cancelled tasks per project (configurable via `DAN_TASK_RETENTION_COUNT`). Prune on `create()` when count exceeds threshold. Superseded tasks count toward the retention limit but are never pruned while their successor is non-terminal.
  - [ ] 2-4. Startup recovery: on first `TaskRegistry` load for a project, scan for zombie tasks — any `ConciergeTask` in `running` or `queued` state with no live asyncio task. Transition zombies to `failed` with `metadata: {reason: "process_restart", recovered_at: <ISO8601>}`. Log a warning per recovered task. This ensures the registry is consistent after unclean shutdowns without requiring auto-retry infrastructure.
- [ ] 3. Integrate task creation into concierge intake
  - [ ] 3-1. Ephemeral (taskless) turns: social chatter, factual ask/general, fast-command responses, `/help`, `/status` queries. Tracked (task-creating) turns: anything that creates a root session, triggers a workflow run/build/edit/schedule, or enters the solver/goal loop. The boundary aligns with the existing `_resolve_context()` vs `_materialize_context(...)` split — materializing context implies a task.
  - [ ] 3-2. Thread `task_id` through: `ConcurrentDispatcher` queue entries (as `SurfaceMessage.metadata["concierge_task_id"]`), `Concierge.handle_message(...)` context, root session creation metadata, pending-action records, and assistant-turn metadata. Use `resolved_context.task_id` as the canonical carrier. When `TriageResult.resume_task_id` is set, bind the turn to that existing `ConciergeTask` instead of creating a new one.
  - [ ] 3-3. Preserve compatibility: existing stream/channel/progress behavior continues unchanged; `task_id` is additive metadata. Surfaces that do not yet consume `task_id` ignore it.
- [ ] 4. Make task provenance observable
  - [ ] 4-1. Include `task_id`, task state, and latest attempt metadata in persisted turn metadata and exported traces
  - [ ] 4-2. Extend telemetry so task lifecycle events can be aggregated separately from raw session completion events
  - [ ] 4-3. Draft the 49-5 snapshot field shapes early: define which `ConciergeTask` fields are public (suitable for dashboard/task-card rendering) vs internal (session IDs, attempt details). Coordinate with 49-5 so event vocabulary and snapshot schemas align with the model shipped here.
- [ ] 5. Add focused regressions and docs
  - [ ] 5-1. Cover creation, state transitions, retry/supersede history, and retention behavior
  - [ ] 5-2. Document the task-vs-session boundary clearly in concierge docs and tests

## Primary Files

- `src/dan/server/concierge/task_registry.py` — new: `ConciergeTask` model, `TaskState` enum, `TaskRegistry` class
- `src/dan/server/concierge/runtime/__init__.py` — integrate task creation into `_materialize_context(...)` and thread `task_id` through intake
- `src/dan/server/concierge/dispatcher.py` — carry `task_id` on queue entries
- `src/dan/server/concierge/pending_actions.py` — add `task_id` field to pending-action records
- `tests/test_concierge/test_task_registry.py` — new: lifecycle, transitions, retention, integration
- `docs/architecture.md`, `docs/todo.md`, `docs/changelog.md`

## Decisions

- **Task is not the same as session.** A task is the long-lived user-facing work item; a session is one execution attempt or branch within that task.
- **One active root attempt at a time is enough for the first iteration.** The registry should still preserve prior attempts so retry/resume/supersede history is explicit.
- **Task identity must be explicit and inspectable.** It should not be reconstructed later from stream channel IDs or by guessing from the latest message.

## Notes

- This plan is the foundation for every later concierge-only hardening step in the 49 family.
- Existing `SessionTrace` and exported session trees remain valuable, but they should become subordinate execution detail rather than the main control-plane object.
- **Existing `Task` model in `models.py`** already has `task_id`, `label`, `status`, `turns`, `completed_steps`, etc. — but it lives nested inside `Project.tasks[]` as a project-scoped work tracker, not as a standalone concierge-level control-plane object. The new `ConciergeTask` is intentionally separate: it tracks concierge intake/dispatch/lifecycle above the project task, and links *down* to root sessions. Whether to eventually unify or keep both layers is a later decision — for now, `ConciergeTask` is the concierge's own model.
- **Existing `PendingAction` is a singleton on `Project`** (`Project.pending_action`, not a list). 49-3 will need to migrate this to task-scoped storage, but 49-1 only needs to ensure `ConciergeTask` carries a `pending_action_id` nullable field.
- **`ConciergeTask.task_id` vs `Project.current_task_id` / `Project.tasks[].task_id`:** These are separate namespaces. `Project.tasks[]` are project-level work trackers with labels and step tracking. `ConciergeTask` is the concierge intake/dispatch lifecycle object. A `ConciergeTask` may *reference* a project-level `Task` if the turn materializes into one, but the IDs are independent. In `ResolvedContext` and turn metadata, use `concierge_task_id` for the `ConciergeTask` and keep `task_id` for the existing project-level `Task` to avoid ambiguity during the transition period.
- **`TriageResult.resume_task_id`** already exists in the triage model and carries a task-binding hint. This plan should wire it into `ConciergeTask` lookup during intake so the triage layer's existing task-awareness flows into the new registry.
