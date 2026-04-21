# 54-15: Fast Task-Lane Policy And Control Bypass

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** completed
**Goal:** Add explicit fast/deep task-lane policy in DAN Code and DAN Research so obvious medium review and inspection tasks can bypass extra serial control-model turns when deterministic planning/review is sufficient.

## Tasks
- [x] 1. Define the fast-lane policy seam
  - [x] 1-1. Add shared task-lane policy models and conservative heuristics for DAN Code and DAN Research
  - [x] 1-2. Keep deep lanes as the default and only fast-path obvious bounded review/inspection tasks
- [x] 2. Apply the policy in DAN Code
  - [x] 2-1. Use deterministic project-planner fallback in fast lanes instead of the extra planner model call
  - [x] 2-2. Use deterministic review fallback in fast lanes instead of the extra post-run review model call
  - [x] 2-3. Emit observability for which lane was chosen and why
- [x] 3. Apply the policy in DAN Research
  - [x] 3-1. Use deterministic intention-plan fallback in fast lanes instead of the extra planning model call
  - [x] 3-2. Use deterministic review fallback in fast lanes instead of the extra post-run review model call
  - [x] 3-3. Emit observability for which lane was chosen and why
- [x] 4. Cover the fast lane with focused tests
  - [x] 4-1. Add DAN Code CLI tests proving fast lanes bypass planner/review controller calls
  - [x] 4-2. Add DAN Research CLI tests proving fast lanes bypass planner/review controller calls
- [x] 5. Update docs and validate
  - [x] 5-1. Update architecture and changelog entries
  - [x] 5-2. Run targeted CLI test baskets

## Decisions
- Fast lanes should only remove control-shell model turns that do not require new evidence.
- The first implementation should downgrade planner/review calls to deterministic fallbacks rather than redesigning the bounded worker organs.
- Deep lanes remain the default for edits, multi-milestone work, benchmarks, and ambiguous requests.

## Notes
- The target fast path is still `route -> bounded run -> aggregate/stop`, not a general one-call solution.
- This plan intentionally leaves deeper organism-contract redesign for a later pass; the immediate win is removing avoidable serial control turns in product shells.
- Validation for this slice: `python -m py_compile src/dan/cli/task_lanes.py src/dan/cli/code.py src/dan/cli/research.py tests/test_cli/test_task_lanes.py tests/test_cli/test_code.py tests/test_cli/test_research.py`, `PYTHONPATH=src:. pytest -q tests/test_cli/test_task_lanes.py tests/test_cli/test_code.py -k 'fast_lane_bypasses_project_planner_and_review_calls or classify_'`, and `PYTHONPATH=src:. pytest -q tests/test_cli/test_research.py -k 'fast_lane_bypasses_research_plan_and_review_calls or uses_intention_plan'`.
