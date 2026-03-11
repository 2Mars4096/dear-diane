# 31-6: Goal-Oriented Loop

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Enable long-running autonomous goal sessions that iterate until a target metric is met or a wall-clock deadline expires — e.g., "beat this Kaggle leaderboard score" or "achieve test coverage >= 90%." Ship the concierge-level loop first, with reusable evaluator/state contracts that an engine-level `goal_loop` node can adopt later.

## Problem

DAN's existing `WhileLoopNode` iterates until a condition on loop variables is true, with `max_iterations` as the safety cap. But there's no first-class support for:
- **Goal-oriented termination:** "keep trying until score >= X" where the score comes from an external evaluation (test suite, Kaggle submission, LLM-as-judge)
- **Wall-clock deadlines:** "stop after 24 hours and return the best result so far"
- **Best-so-far tracking:** across iterations, keep the best result and return it even if the deadline expires
- **Graduated strategy shifts:** if simple approaches fail, escalate to more creative/expensive strategies

There are two plausible integration layers:
- **Concierge goal sessions** — a user asks DAN to keep improving until a metric is met
- **Workflow control-flow node** — `goal_loop` becomes a reusable engine primitive

Both should eventually share the same evaluator/state contracts, but v1 should be **concierge-first** so ownership of persistence, status commands, permissions, and notifications is clear.

The meta-orchestrator's `plan→execute→diagnose→repair` cycle (29-2) is close, but it's designed for single-attempt repair, not open-ended iterative improvement.

## Tasks

- [x] 1. **GoalSpec model**
  - [x] 1-1. `GoalSpec` Pydantic model: `metric_name: str`, `target_value: float`, `comparison: Literal[">=", "<=", "==", ">", "<"]`, `timeout_seconds: int | None`, `max_attempts: int = 100`, `evaluation_mode: Literal["script", "llm_judge", "test_suite", "custom"]`
  - [x] 1-2. `EvaluationResult` model: `score: float`, `passed: bool`, `details: str`, `artifacts: dict[str, Any]`
  - [x] 1-3. `GoalLoopState` model: `attempts: list[AttemptRecord]`, `best_attempt_id: str | None`, `best_result: EvaluationResult | None`, `comparison: ComparisonOp` (shared type alias `ComparisonOp = Literal[">=", "<=", "==", ">", "<"]` — used in both `GoalSpec` and `GoalLoopState`), `start_time: datetime`, `strategy_tier: int`
  - [x] 1-4. Comparator helpers: `is_better(candidate, incumbent, comparison) -> bool`, tie-handling rules, and float tolerance for `==` / threshold comparisons

- [x] 2. **GoalLoop executor**
  - [x] 2-1. `GoalLoopExecutor` in `concierge/goal_loop.py`: main loop — attempt → evaluate → if target met, exit with success; if timeout, exit with best-so-far; else continue
  - [x] 2-2. Pluggable evaluator interface: `ScriptEvaluator` (run a script, parse score from stdout), `LLMJudgeEvaluator` (LLM rates quality 1-10), `TestSuiteEvaluator` (run pytest, count pass/fail), `CustomEvaluator` (user-provided async callable)
  - [x] 2-3. Best-so-far tracking: comparator-aware (maximize, minimize, equality-closest); persisted via JSON serialization helpers
  - [x] 2-4. Wall-clock enforcement: `time.time()` deadline check before each new attempt; on timeout, return best-so-far with `status="timeout"`

- [x] 3. **Strategy escalation**
  - [x] 3-1. Strategy tiers: tier 0 (direct attempt), tier 1 (parameter variation), tier 2 (approach change), tier 3 (decomposition/ensemble)
  - [x] 3-2. Escalation trigger: after N consecutive failures at current tier (configurable, default 3), escalate to next tier
  - [x] 3-3. Tier-specific prompts: inject strategy guidance into the LLM prompt based on current tier
  - [x] 3-4. Wire into existing `RepairClassifier` / `RepairEscalator` (19-3) for diagnosis between attempts

- [x] 4. **Concierge-first integration (v1)**
  - [x] 4-1. Solver recognizes goal-loop intents: "beat this score", "keep improving until X", "iterate until test passes"
  - [x] 4-2. `/goal` chat command: `/goal "score >= 0.85" --timeout 24h --eval script:evaluate.py`
  - [x] 4-3. Progress notifications via `NotificationManager`: periodic updates ("attempt 7/100, best score: 0.82, 3h elapsed")
  - [x] 4-4. `/goal-status` and `/goal-stop` commands for monitoring and early termination
  - [x] 4-5. Explicit task ownership: goal session is anchored to a `Task` / project context so status, permissions, and resume semantics are unambiguous

- [x] 5. **Optional engine / authoring follow-on**
  - [x] 5-1. `GoalLoopNode` model in `control_flow.py` with `goal_text`, `metric_name`, `target_value`, `comparison`, `max_iterations`, `evaluator`, `success_criteria`, `body_graph` + full composite-node contract
  - [x] 5-2. Builder DSL: `wf.goal_loop(node_id, goal_text=..., body=...)` context manager
  - [x] 5-3. Markdown syntax: `type: goal_loop` with goal spec in frontmatter
  - [x] 5-4. Node type registration (`NodeTypeRegistry`, `ExecutorRegistry`), `GoalLoopExecutor` with metric tracking and iteration events, compiler support in builder and loader

- [x] 6. **Tests and docs**
  - [x] 6-1. Unit tests: GoalSpec validation, evaluator dispatch, best-so-far tracking, timeout, strategy escalation
  - [x] 6-2. Integration test: mock evaluator with improving scores, verify exit on target met
  - [x] 6-3. Update architecture, changelog

## Dependencies

- `WhileLoop` / `GateNode` patterns (models, executor)
- `RepairClassifier` / `RepairEscalator` (19-3) for inter-attempt diagnosis
- `NotificationManager` (26-4) for progress updates
- `LocalStateManager` for GoalLoopState persistence
- `CheckpointStore` for crash recovery

## Estimate

2-3 days

## Primary Files

- `src/dan/models/control_flow.py` — GoalSpec, EvaluationResult, GoalLoopState models
- `src/dan/executors/control_flow.py` — GoalLoopExecutor
- `src/dan/engine/evaluators.py` — ScriptEvaluator, LLMJudgeEvaluator, TestSuiteEvaluator, CustomEvaluator
- `src/dan/server/concierge/runtime.py` — /goal command handling

## Notes

- The Kaggle use case is the motivating example: set `target_value` to a leaderboard score, `evaluation_mode` to `script` (which submits and parses the score), and `timeout_seconds` to 86400 (24h). DAN iterates automatically.
- Strategy escalation is inspired by the repair escalation ladder (19-3) but applied to the solution space, not the repair space.
- Each attempt is a full concierge-style interaction — not just re-running the same code. The LLM sees previous attempts, their scores, and the diagnosis, and generates a new approach.
- `best_result` is comparator-aware. For maximize goals it is the highest score; for minimize goals it is the lowest; for equality-style goals it is the closest result within configured tolerance.
- When an individual attempt involves multiple subtasks, the RCPSP scheduler (31-8) can optimize the internal execution of each attempt.
- The evaluator interface is async — `ScriptEvaluator` can run long-running evaluation scripts (Kaggle submissions that take minutes) without blocking the event loop.
