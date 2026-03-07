# 25-10: Execution Selector

**Parent:** [25-chat-control-plane](25-chat-control-plane.md)
**Status:** completed
**Goal:** Turn solver decisions into concrete execution by choosing the right backend path while preserving deterministic fast paths and shared concierge infrastructure.

## Tasks
- [x] 1. Define execution backends for solver output
  - [x] 1-1. Map `direct_action` to tool-backed direct solve paths such as file operations, PDF review, web lookup, charting, drafting, and short transformations.
  - [x] 1-2. Map `workflow_reuse`, `workflow_adapt`, and `workflow_build` to the appropriate workflow runtime and graph mutation paths.
  - [x] 1-3. Map `run_control`, `status_pull`, `experience_lookup`, `publish_share`, and `meta_delegate` to the existing subsystem handlers.
- [x] 2. Reframe current handlers as execution backends
  - [x] 2-1. Keep `FileHandler`, `DirectTaskHandler`, `RunHandler`, `StatusHandler`, `ExperienceHandler`, `PublishHandler`, `WorkflowBuildHandler`, `ConversationHandler`, and `MetaGoalHandler` as reusable actuators.
  - [x] 2-2. Remove the assumption that handlers own top-level routing decisions.
  - [x] 2-3. Allow the solver runtime to pick and compose these backends per plan step.
- [x] 3. Preserve deterministic prechecks
  - [x] 3-1. Keep yes/no/number/apply continuations outside the heavy planning path.
  - [x] 3-2. Keep explicit file/path resolution, status queries, run control, and destructive guards as cheap direct checks.
  - [x] 3-3. Ensure queued and pending actions resume through the same execution selector.
- [x] 4. Add synthesis and reporting
  - [x] 4-1. Provide a lightweight synthesis layer when a tool or workflow completes and the user still needs a concise answer or summary.
  - [x] 4-2. Make execution results report assumptions, partials, or remaining human steps consistently.
  - [x] 4-3. Feed completion metadata into reflection and workflow-promotion logic.
- [x] 5. Add tests and rollout safety
  - [x] 5-1. Unit tests for backend selection from representative solver decisions.
  - [x] 5-2. Integration tests for direct solve, reuse, adapt, build, and meta execution paths.
  - [x] 5-3. Compatibility coverage ensuring old fast-path behavior still works while the selector is introduced.

## Decisions
- Execution mode is derived from planning, not from a message taxonomy alone.
- Existing handlers remain valuable and should be repurposed, not discarded.
- The selector should be able to mix deterministic execution and lightweight LLM synthesis in one task.

## Files

| File | Action |
|---|---|
| `src/dan/server/concierge/executor.py` | Create — `ExecutionSelector` that maps `SolverDecision` to backend calls |
| `src/dan/server/concierge/handlers.py` | Modify — ensure all handlers can be called programmatically from the selector |
| `src/dan/server/concierge/__init__.py` | Modify — wire execution selector into `Concierge.process()` |
| `tests/test_concierge/test_execution_selector.py` | Create — backend selection and composition tests |

## Dependencies

- **25-6 (Concierge Runtime)** provides handler backends and deterministic prechecks.
- **25-8 (Solver Runtime)** provides `SolverDecision` outputs that drive selection.
- **25-9 (Workflow Memory)** provides reuse/adapt decisions that affect which backend is chosen.

## Acceptance Criteria

- Every `SolverDecision.execution_mode` maps to a concrete backend invocation.
- Deterministic prechecks (continuations, status, file paths) still bypass heavy planning.
- Execution results consistently report deliverable, assumptions, and remaining human steps.
- Rollout compatibility: existing fast-path behavior remains functional while selector is introduced.

## Notes

- This plan is where the current handler registry stops being the "brain" and becomes the execution layer.
- Observability matters: log which execution mode was chosen and why.
- Handlers remain valuable actuators; they just no longer own routing decisions.
