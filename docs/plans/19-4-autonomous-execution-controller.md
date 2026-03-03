# 19-4: Autonomous Execution Controller

**Parent:** [19-meta-orchestrator](19-meta-orchestrator.md)
**Status:** in-progress
**Goal:** Build the outer meta-loop that ties planning, execution, diagnosis, and repair into a single autonomous system — receiving a high-level goal and driving it to completion through iterative execution and graduated repair, while allowing human intervention at any step.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `ExperienceStore` / `ExperienceIndex` (19-1) | `engine/experience.py` (planned) | Workflow experience memory and similarity search | Discovery input for the controller |
| `WorkflowPlanner` (19-2) | `meta/planner.py` (planned) | Goal → workflow plan (REUSE/ADAPT/GENERATE) | Planning capability |
| `RepairEscalator` (19-3) | `meta/repair.py` (planned) | Graduated repair: prompt → parameter → structural → redesign | Repair capability |
| `RunManager` | `server/run_manager.py` | `start_run()`, `get_run()`, `_enrich_and_persist()`, event broadcasting | Execution and observability |
| `Engine` | `engine/scheduler.py` | Full workflow execution with checkpointing, memory, cost tracking | Execution engine |
| Self-evolving (9D) | `engine/error_memory.py`, `engine/rule_generator.py` | Error memory, reflection, self-generating rules | Per-run learning (levels 0–1) |
| `HumanNode` | `models/nodes.py`, `executors/human.py` | Human-in-the-loop with typed I/O, render modes, async input callback | Pattern for human override |
| Event system | `engine/events.py` | Rich typed events, WebSocket broadcasting | Observability channel |

## Tasks

- [x] 1. `MetaSession` model
  - [x] 1-1. Define `MetaSession` in new `meta/controller.py`:
    - `session_id: str` (uuid)
    - `goal: str` — the user's original request
    - `status: Literal["planning", "executing", "diagnosing", "repairing", "paused", "completed", "failed"]`
    - `plan_result: PlanResult | None` — current workflow plan
    - `workflow_id: str | None` — current workflow being executed
    - `run_history: list[str]` — run_ids of all attempts
    - `repair_history: list[RepairAction]` — all repairs applied
    - `pending_repair_action_ids: list[str]` — repair actions awaiting outcome recording
    - `iteration: int` — current plan→execute→repair cycle number
    - `max_iterations: int` — cap on total cycles (default 5)
    - `created_at: float`
    - `updated_at: float`
    - `paused_at_stage: str | None` — if paused, which stage
    - `human_override: list[dict]` — human-provided modifications log
    - `events: list[dict]` — persisted meta-session events for audit
  - [x] 1-2. `MetaSessionStore` — persistence for meta sessions. Uses `MemoryStore` at GLOBAL scope with key `meta_session:{session_id}`. Supports save/load/list/delete.

- [x] 2. Controller loop
  - [x] 2-1. `MetaController` class in `meta/controller.py`:
    - `run(goal: str, config: MetaControllerConfig | None = None) -> MetaSession` — the main entry point. Executes the full loop:
      ```
      while iteration < max_iterations and not completed:
        1. PLAN: call WorkflowPlanner.plan(goal, error_context)
        2. CHECK PAUSE: if human override pending, wait
        3. BUILD: execute the plan (reuse/adapt/generate) → workflow_id
        4. EXECUTE: call RunManager.start_run(graph, graph_id=workflow_id)
        5. OBSERVE: wait for run completion, collect RunRecord
        6. EVALUATE: did it succeed? if yes → update experience, return
        7. DIAGNOSE: extract principles via 9D reflection
        8. REPAIR: call RepairEscalator.repair() for each principle
        9. APPLY: apply repair actions (hyperedges, mutations, or replan)
       10. CHECK PAUSE: if human override pending, wait
       11. Loop back to step 4 (if repair was level 1-3) or step 1 (if level 4)
      ```
    - Returns the `MetaSession` with full history of what was tried.
  - [x] 2-2. `MetaControllerConfig`:
    - `max_iterations: int = 5`
    - `auto_approve_levels: list[int] = [1, 2]` — which repair levels proceed without human approval
    - `pause_before_execute: bool = False` — if true, pause after planning for human review
    - `pause_before_repair: bool = False` — if true, pause before applying any repair
    - `pause_on_redesign: bool = True` — if true, pause before level 4 redesign (sensible default)
    - `timeout_seconds: float | None = None` — total time budget for the entire meta-session

- [x] 3. Human override protocol
  - [x] 3-1. Pause mechanism:
    - Controller checks `session.paused_at_stage` before each major step.
    - External callers (API, UI) can set the pause via `POST /api/meta/sessions/{id}/pause`.
    - Resume via `POST /api/meta/sessions/{id}/resume` with optional `override` body.
    - Scope (v1): pause applies at inter-step checkpoints only (before planning, before execution, before repair, before redesign). No hard mid-run interrupt in this subplan.
  - [x] 3-2. Override types:
    - `ModifyPlan(new_plan: PlanResult)` — replace the planner's output with a human-provided plan
    - `ModifyGraph(mutations: MutationPlan)` — apply human-provided mutations before execution
    - `SkipRepair()` — skip the current repair and proceed to next iteration
    - `Abort()` — stop the meta-session entirely
    - `AcceptAsIs()` — accept current state as the final result (even if run had errors)
  - [x] 3-3. Override persistence: all overrides are stored in the `MetaSession.human_override` log for audit.

- [x] 4. Experience feedback loop
  - [x] 4-1. On successful completion: call `ExperienceStore.save_experience()` with the final workflow's experience (creates or updates with `consolidate_experience`). This makes the workflow discoverable for future similar goals.
  - [x] 4-2. On failure (max iterations reached): save a "failed experience" with the full repair history and failure patterns. This ensures the planner knows "we tried X and it didn't work" next time.
  - [x] 4-3. Cross-session learning: when the controller starts a new session, it queries `ExperienceIndex` with the goal text via `_enrich_goal_with_prior_experience()`. If similar sessions exist (including failed ones), the error context is passed to the planner to avoid repeating mistakes.

- [x] 5. Event emissions
  - [x] 5-1. New `EventType` values for meta-orchestrator lifecycle:
    - `META_SESSION_STARTED` — goal received, planning begins
    - `META_PLAN_CREATED` — planner produced a plan (REUSE/ADAPT/GENERATE)
    - `META_EXECUTION_STARTED` — workflow execution begins
    - `META_DIAGNOSIS_STARTED` — post-failure diagnosis begins
    - `META_REPAIR_APPLIED` — repair action applied (with level and details)
    - `META_REDESIGN_TRIGGERED` — level 4 redesign initiated
    - `META_PAUSED` — human pause triggered
    - `META_RESUMED` — human resumed (with or without override)
    - `META_SESSION_COMPLETED` — goal achieved
    - `META_SESSION_FAILED` — max iterations reached, goal not achieved
  - [ ] 5-2. Emit events through `RunManager`'s event callback so they appear in the WebSocket stream and are visible in the editor UI. (Deferred: requires wiring the meta-controller emit callback through RunManager's broadcast system.)

- [x] 6. REST API
  - [x] 6-1. `POST /api/meta/run` — body: `{"goal": "...", "config": {...}}`. Starts a meta-session. Returns `session_id` immediately; execution runs in background.
  - [x] 6-2. `GET /api/meta/sessions/{id}` — get meta-session status, plan, run history, repair history.
  - [x] 6-3. `GET /api/meta/sessions` — list all meta-sessions.
  - [x] 6-4. `POST /api/meta/sessions/{id}/pause` — pause at next checkpoint.
  - [x] 6-5. `POST /api/meta/sessions/{id}/resume` — resume with optional override body.
  - [x] 6-6. `DELETE /api/meta/sessions/{id}` — abort a running session.
  - [x] 6-7. `GET /api/meta/sessions/{id}/events` — stream meta-session events via WebSocket or SSE.

- [x] 7. Tests
  - [x] 7-1. Unit: `MetaSession` state machine — valid transitions (planning → executing → diagnosing → repairing → executing → ...).
  - [x] 7-2. Unit: `MetaController` with mocked planner/engine/repair — happy path (plan → execute → success in 1 iteration).
  - [x] 7-3. Unit: `MetaController` with mocked failure → repair → retry → success (multi-iteration).
  - [x] 7-4. Unit: pause/resume protocol — controller blocks at pause point, resumes on external signal, applies override.
  - [x] 7-5. Unit: max iterations enforced — after `max_iterations`, session status is `failed`.
  - [x] 7-6. Integration: full loop with mocked LLM — goal → plan → execute → fail → diagnose → repair → re-execute → succeed.
  - [x] 7-7. Integration: experience feedback — successful session saves experience; new similar session retrieves it.
  - [x] 7-8. Safety: timeout enforcement — session aborts after `timeout_seconds`.

## Decisions

- **The controller is async, not blocking.** `POST /api/meta/run` returns immediately with a `session_id`. The controller runs as a background task (like `RunManager._run_task()`). Status is polled or streamed via events.
- **Pause is checkpoint-based, not interrupt-based.** The controller checks for pause at defined checkpoints (before execute, before repair, before redesign), not mid-execution. A true hard-stop + checkpoint-resume API is future work and explicitly out of scope for this plan.
- **Default pause policy is permissive.** Only level 4 redesign pauses by default. Levels 1–2 proceed automatically. This matches the user's preference for fully autonomous operation with intervention capability.
- **Max iterations prevent infinite loops.** Default cap of 5 iterations means at most: initial plan + 4 repair/redesign cycles. This is generous — most workflows should succeed or surface fundamental issues within 2–3 iterations.
- **Failed sessions are valuable experience.** Even failed meta-sessions are persisted with full repair history. This negative experience prevents the planner from repeating the same approach for similar goals.
- **Repair outcomes are tracked.** After every execution, `_record_pending_repair_outcomes()` marks previous repair actions as success/failure in `RepairActionStore`, enabling accurate escalation decisions.
- **Cross-session learning is automatic.** At the start of a new session, `_enrich_goal_with_prior_experience()` queries the ExperienceIndex to surface relevant past sessions.

## Notes

- The controller is the top-level entry point for the entire meta-orchestrator. Everything else (experience memory, planner, repair engine) is a component it orchestrates.
- The controller's state machine is intentionally simple: `planning → executing → diagnosing → repairing → (back to executing or planning)`. Complex branching (parallel repairs, speculative execution) is deferred.
- The human override protocol is inspired by Cursor's Plan mode: the system proposes, the human can modify or accept. But unlike Cursor, the default is "proceed autonomously" rather than "wait for approval."
- Editor UI integration (visualizing the meta-session state, showing the plan, presenting pause/override controls) is out of scope for this plan — that's a frontend task. The API and event stream provide all necessary data.
