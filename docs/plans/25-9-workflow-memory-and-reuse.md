# 25-9: Workflow Memory And Reuse

**Parent:** [25-chat-control-plane](25-chat-control-plane.md)
**Status:** completed
**Goal:** Treat saved workflows and past executions as a semantic knowledge base for solver planning so DAN reuses or adapts prior solutions before building from scratch.

## Tasks
- [x] 1. Build workflow-memory retrieval for planning
  - [x] 1-1. Retrieve semantically similar workflows for each incoming goal, not just keyword matches.
  - [x] 1-2. Retrieve relevant past run summaries, learned principles, and related experience as planning context.
  - [x] 1-3. Rank retrieved items for `reuse`, `adapt`, or `reference only`.
- [x] 2. Add reuse-aware planning signals
  - [x] 2-1. Extend solver planning context with workflow titles, summaries, interfaces, and reasons for prior success/failure.
  - [x] 2-2. Teach the planner to prefer `workflow_reuse` or `workflow_adapt` before `workflow_build` when a close match exists.
  - [x] 2-3. Let the planner cite why a retrieved workflow is suitable or why a new build is still required.
- [x] 3. Learn from completed tasks
  - [x] 3-1. After successful tasks, decide whether a new reusable workflow should be saved or an existing one updated.
  - [x] 3-2. Capture user corrections during execution and feed them back into workflow summaries or metadata.
  - [x] 3-3. Avoid duplicate saves when a semantically identical workflow already exists.
- [x] 4. Add tests and acceptance coverage
  - [x] 4-1. Unit tests for semantic retrieval, reuse-vs-build selection, and duplicate suppression.
  - [x] 4-2. Integration tests where a user request matches an old workflow with different wording.
  - [x] 4-3. Regression tests for cases where direct solve remains preferable to workflowing.

## Decisions
- Workflow memory is a planning input, not just a user-visible lookup feature.
- Reuse decisions should be explained in the planner output for observability and debugging.
- Save/update recommendations should be based on actual task patterns, not only on node count.

## Files

| File | Action |
|---|---|
| `src/dan/server/concierge/solver.py` | Modify — add workflow retrieval and reuse scoring to planning context |
| `src/dan/server/concierge/memory_bridge.py` | Create — `WorkflowMemoryIndex` wrapper for semantic workflow retrieval |
| `src/engine/experience.py` | Modify — ensure experience records expose planning-relevant fields (title, summary, interface, outcome) |
| `tests/test_concierge/test_workflow_memory.py` | Create — semantic retrieval, reuse-vs-build ranking, and duplicate suppression tests |

## Dependencies

- **25-8 (Solver Runtime)** provides the planning context structure where workflow memory is injected.
- **Existing Experience System** provides `ExperienceIndex`, `ExperienceStore`, and learned principles.

## Acceptance Criteria

- Semantic workflow retrieval returns relevant matches even when query wording differs from saved workflow titles.
- Planning context includes top-N workflow candidates with reuse/adapt/build recommendations.
- Post-task reflection proposes saves/updates only when a semantically distinct reusable pattern exists.
- Direct solve remains preferred over workflowing for one-shot tasks that don't benefit from reuse.

## Notes

- This plan is the flywheel for making later requests faster and less reasoning-heavy.
- The retrieval layer should prefer semantic search and fall back to lexical search only when necessary.
- Corrections should feed back into workflow metadata so the same mistake is less likely to recur.
