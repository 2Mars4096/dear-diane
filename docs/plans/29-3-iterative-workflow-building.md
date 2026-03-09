# 29-3: Iterative Workflow Building

**Parent:** [29-concierge-memory-evolvement](29-concierge-memory-evolvement.md)
**Status:** completed
**Goal:** Enable multi-round build/test/diagnose/modify workflow construction through conversation, where the concierge drives the iteration loop and the user can observe, intervene, or let it run autonomously.

## Context

Current workflow building is single-shot:
1. User describes intent
2. ChatManager extracts intent → compiles → returns graph (or falls back to mutation plan)
3. User accepts or rejects the diff
4. Done — no iteration, no testing, no refinement

The concierge (after 29-2) can hold goals across messages. This plan adds a structured build session within a goal that iterates: build → validate → test → diagnose → modify → re-test.

## Tasks

### 1. Build session model
- [x] 1-1. Define `BuildSession` model: `id`, `goal_id`, `workflow_id`, `status` (drafting/testing/diagnosing/modifying/completed/failed), `iteration_count`, `max_iterations` (default 5), `draft_history` (list of graph snapshots), `test_results` (list), `diagnosis_history` (list), `user_feedback` (list), `created_at`
- [x] 1-2. Define `BuildIteration` model: `iteration_num`, `action` (build/modify/repair), `graph_snapshot_id`, `validation_result`, `test_run_id`, `test_outcome`, `diagnosis`, `modification_applied`
- [x] 1-3. Persist `BuildSession` as `WORKING_STATE` in memory kernel

### 2. Build loop state machine
- [x] 2-1. `BuildSessionManager` class in `src/dan/server/concierge/build_session.py`
- [x] 2-2. State: `DRAFTING` — initial build or rebuild from intent
- [x] 2-3. State: `VALIDATING` — run `validate_graph()` on draft; auto-fix trivial issues
- [x] 2-4. State: `TESTING` — execute a smoke run with sample/default inputs
- [x] 2-5. State: `DIAGNOSING` — if test fails, analyze errors using memory (failure patterns, principles)
- [x] 2-6. State: `MODIFYING` — apply targeted fix (parameter tweak, structural change, or regenerate portion)
- [x] 2-7. State: `REVIEWING` — present current state to user (if autonomy level requires it)
- [x] 2-8. State: `COMPLETED` — tests pass or user accepts
- [x] 2-9. State: `FAILED` — max iterations exceeded or unrecoverable error

### 3. Integration with concierge goal
- [x] 3-1. `Concierge._execute_goal()` creates a `BuildSession` for workflow-build goals
- [x] 3-2. Each concierge turn advances the build session by one or more states
- [x] 3-3. User messages during a build session are interpreted as feedback/guidance for the current state
- [x] 3-4. "Actually, use tab delimiter" → stored as user feedback, triggers MODIFYING state
- [x] 3-5. "Looks good" / "run it" → advances to TESTING or COMPLETED
- [x] 3-6. "Start over" → resets to DRAFTING with accumulated learnings preserved

### 4. Smoke testing
- [x] 4-1. Auto-generate sample inputs from workflow InputNode variable types/names
- [x] 4-2. Run via `RunManager.start_run()` with timeout (default 60s for smoke)
- [x] 4-3. Capture success/failure, node statuses, error messages
- [x] 4-4. Option to skip smoke test: `DAN_BUILD_SKIP_SMOKE=1` or user says "don't test"

### 5. Diagnosis integration
- [x] 5-1. On test failure, query memory kernel with WORKFLOW_REPAIR policy
- [x] 5-2. Use `DiagnosisLoop` (from Phase 14) for automated repair attempts
- [x] 5-3. Enrich diagnosis with relevant failure patterns and principles from memory
- [x] 5-4. If diagnosis suggests structural change, present to user before applying (unless AUTONOMOUS)

### 6. User interaction during build
- [x] 6-1. After each significant state change, stream status update to user (via chat events)
- [x] 6-2. Status format: "[Building] Iteration 2/5: validation passed, running smoke test..."
- [x] 6-3. User can interrupt with feedback at any point (feedback is queued and applied at next state transition)
- [x] 6-4. `/build-status` command: show current build session state
- [x] 6-5. `/build-stop` command: halt iteration, keep current best draft

### 7. Post-build memory extraction
- [x] 7-1. On COMPLETED: extract `WORKFLOW_ASSET` memory item from final graph
- [x] 7-2. On COMPLETED: extract `WORKFLOW_PATTERN` if the topology matches a generalizable shape
- [x] 7-3. On FAILED: extract `FAILURE_PATTERN` from accumulated diagnosis history
- [x] 7-4. On any outcome: extract `FACT` items from user feedback ("delimiter is tab", "data is in CSV format")
- [x] 7-5. Store all extracted items via memory kernel

### 8. Tests
- [x] 8-1. Unit tests for BuildSession state machine transitions
- [x] 8-2. Unit tests for BuildSessionManager lifecycle
- [x] 8-3. Integration test: full build loop (draft → validate → test-fail → diagnose → modify → test-pass → complete)
- [x] 8-4. Integration test: user interruption mid-build (feedback applied correctly)
- [x] 8-5. Integration test: max iterations exceeded → FAILED with memory extraction

## Decisions

- 2026-03-09: Keep diagnosis enrichment lightweight and local to `BuildSessionManager`. Return structured `memory_matches`, `failure_patterns`, and `principles` alongside the legacy `memory_context` string so current callers stay compatible.
- 2026-03-09: Treat structural-change diagnoses as a review gate in supervised/interactive modes. If diagnosis returns `requires_user_review`, pause the goal at `REVIEWING` instead of auto-continuing; autonomous mode still proceeds.

## Notes

- 2026-03-09 reconciliation: the build-session mechanism is now complete, including structured failure/principle diagnosis context and structural-review gating before non-autonomous changes. Daily task validation for research/report asks remains tracked separately in [29-8](29-8-practical-research-quality-and-audit.md).
- Daily task validation for research/report asks is tracked in [29-8](29-8-practical-research-quality-and-audit.md); this plan remains the mechanism-level state machine beneath those scenarios.
- Smoke testing uses the same `RunManager` infrastructure as normal runs. The only difference is a shorter timeout and auto-generated inputs.
- The build session is a lightweight state machine, not a workflow. It does not use the DAN workflow engine to orchestrate itself — that would be circular.
- Build sessions are scoped to a single workflow. Multi-workflow system builds (via SystemArchitect) are out of scope.
