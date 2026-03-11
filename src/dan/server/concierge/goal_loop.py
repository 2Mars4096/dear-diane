"""Goal-oriented loop — autonomous iteration until a target metric is met.

Implements plan 31-6: concierge-first goal sessions that iterate until a
target metric is met or a wall-clock deadline expires.  Tracks best-so-far
across attempts, escalates strategy tiers on consecutive failures, and
persists state for crash recovery.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from dan.server.concierge.boundary_handoff import GoalAttemptAssembler

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

ComparisonOp = Literal[">=", "<=", "==", ">", "<"]

STRATEGY_TIER_LABELS: dict[int, str] = {
    0: "direct",
    1: "parameter_variation",
    2: "approach_change",
    3: "decomposition_ensemble",
}

STRATEGY_TIER_PROMPTS: dict[int, str] = {
    0: (
        "Attempt a direct solution. Focus on the most straightforward approach "
        "to achieve {metric_name} {comparison} {target_value}."
    ),
    1: (
        "Previous direct attempts haven't reached the target. Try varying "
        "parameters, hyperparameters, thresholds, or configuration values. "
        "Small systematic changes to the existing approach."
    ),
    2: (
        "Parameter variations are insufficient. Switch to a fundamentally "
        "different method, algorithm, or architecture. Consider alternative "
        "approaches you haven't tried."
    ),
    3: (
        "Single approaches are insufficient. Decompose the problem into "
        "sub-problems, combine multiple methods (ensemble/stacking), or use "
        "a multi-stage pipeline."
    ),
}


def get_tier_prompt(
    tier: int,
    goal: "GoalSpec",
    best_result: "EvaluationResult | None",
    recent_attempts: "list[AttemptRecord]",
) -> str:
    """Build a comprehensive prompt incorporating tier guidance and loop state."""
    template = STRATEGY_TIER_PROMPTS.get(tier, STRATEGY_TIER_PROMPTS[0])
    tier_text = template.format(
        metric_name=goal.metric_name,
        comparison=goal.comparison,
        target_value=goal.target_value,
    )

    parts = [f"Strategy tier: {tier} ({STRATEGY_TIER_LABELS.get(tier, 'unknown')})", tier_text]

    if best_result is not None:
        parts.append(
            f"Best result so far: {best_result.score} "
            f"(passed={best_result.passed})"
        )

    if recent_attempts:
        parts.append(f"Recent attempts ({len(recent_attempts)}):")
        for a in recent_attempts[-5:]:
            parts.append(
                f"  #{a.attempt_number} tier={a.strategy_tier} "
                f"score={a.result.score} — {a.approach_summary or 'no summary'}"
            )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Pydantic v2 models
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GoalSpec(BaseModel):
    """Declarative specification of what the goal loop is trying to achieve."""

    metric_name: str
    target_value: float
    comparison: ComparisonOp
    timeout_seconds: int | None = None
    max_attempts: int = 100
    evaluation_mode: Literal["script", "llm_judge", "test_suite", "custom"] = "llm_judge"


class EvaluationResult(BaseModel):
    """Outcome of a single evaluation."""

    score: float
    passed: bool
    details: str = ""
    artifacts: dict[str, Any] = Field(default_factory=dict)


class AttemptRecord(BaseModel):
    """Record of a single attempt within the goal loop."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    attempt_number: int
    result: EvaluationResult
    strategy_tier: int
    started_at: datetime
    duration_seconds: float
    approach_summary: str = ""


class GoalLoopState(BaseModel):
    """Full state of a running or completed goal loop."""

    goal: GoalSpec
    attempts: list[AttemptRecord] = Field(default_factory=list)
    best_attempt_id: str | None = None
    best_result: EvaluationResult | None = None
    start_time: datetime = Field(default_factory=_utc_now)
    strategy_tier: int = 0
    status: Literal["running", "success", "timeout", "stopped", "failed"] = "running"


class GoalSession(BaseModel):
    """Anchors a goal loop to a task/project context."""

    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    goal_state: GoalLoopState
    project_id: str | None = None
    task_id: str | None = None
    external_id: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)


# ---------------------------------------------------------------------------
# Comparison helpers
# ---------------------------------------------------------------------------

_CMP_FNS: dict[ComparisonOp, Callable[[float, float], bool]] = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    ">":  lambda a, b: a > b,
    "<":  lambda a, b: a < b,
}


def is_target_met(
    score: float,
    target: float,
    comparison: ComparisonOp,
    tolerance: float = 1e-9,
) -> bool:
    """Return *True* when *score* satisfies the comparison against *target*.

    For ``==`` the check uses *tolerance* for float imprecision.
    For ``>=`` / ``<=`` equality within tolerance also returns True.
    """
    if comparison == "==":
        return abs(score - target) <= tolerance
    if comparison == ">=" and abs(score - target) <= tolerance:
        return True
    if comparison == "<=" and abs(score - target) <= tolerance:
        return True
    return _CMP_FNS[comparison](score, target)


def is_better(
    candidate: float,
    incumbent: float,
    comparison: ComparisonOp,
) -> bool:
    """Return *True* when *candidate* is strictly better than *incumbent*.

    "Better" is defined by the comparison direction:
    - ``>=`` / ``>``  → higher is better
    - ``<=`` / ``<``  → lower is better
    - ``==``          → closer to the target midpoint (handled by caller
      who passes the absolute-distance; here we just treat ``==`` as
      lower-is-better since the caller should pass ``abs(score - target)``).
    """
    if comparison in (">=", ">"):
        return candidate > incumbent
    if comparison in ("<=", "<"):
        return candidate < incumbent
    # For == goals, the caller should compare abs(score - target).
    # Lower distance is better.
    return candidate < incumbent


# ---------------------------------------------------------------------------
# Evaluator protocol + concrete evaluators
# ---------------------------------------------------------------------------

@runtime_checkable
class Evaluator(Protocol):
    async def evaluate(self, context: dict[str, Any]) -> EvaluationResult: ...


class ScriptEvaluator:
    """Run an external script and parse the score from stdout.

    Expects either a JSON object ``{"score": <float>}`` or a plain float
    printed to stdout.
    """

    def __init__(self, script_path: str) -> None:
        self.script_path = script_path

    async def evaluate(self, context: dict[str, Any]) -> EvaluationResult:
        import sys
        script = Path(self.script_path).expanduser()
        if not script.exists():
            return EvaluationResult(
                score=0.0,
                passed=False,
                details=f"Script evaluator path not found: {script}",
            )
        if not script.is_file():
            return EvaluationResult(
                score=0.0,
                passed=False,
                details=f"Script evaluator path is not a regular file: {script}",
            )
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return EvaluationResult(score=0.0, passed=False, details="Script evaluation timed out (300s)")
        stdout_text = stdout_bytes.decode().strip()

        try:
            data = json.loads(stdout_text)
            score = float(data["score"])
        except (json.JSONDecodeError, KeyError, TypeError):
            score = float(stdout_text)

        return EvaluationResult(
            score=score,
            passed=False,  # caller sets this
            details=stderr_bytes.decode().strip() if stderr_bytes else "",
        )


class LLMJudgeEvaluator:
    """Use an LLM callable to rate quality on a 1-10 scale."""

    def __init__(self, llm_fn: Callable[..., Awaitable[Any]]) -> None:
        self.llm_fn = llm_fn

    async def evaluate(self, context: dict[str, Any]) -> EvaluationResult:
        result = await self.llm_fn(context)
        if isinstance(result, (int, float)):
            score = float(result)
        elif isinstance(result, dict):
            score = float(result.get("score", result.get("rating", 5)))
        else:
            score = float(result)
        return EvaluationResult(score=score, passed=False)


class TestSuiteEvaluator:
    """Run a test command (e.g. pytest) and compute pass/fail ratio."""

    __test__ = False

    def __init__(self, test_command: str) -> None:
        self.test_command = test_command

    async def evaluate(self, context: dict[str, Any]) -> EvaluationResult:
        proc = await asyncio.create_subprocess_shell(
            self.test_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=600)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return EvaluationResult(score=0.0, passed=False, details="Test suite timed out (600s)")
        combined = (stdout_bytes or b"").decode() + (stderr_bytes or b"").decode()

        passed_match = re.search(r"(\d+)\s+passed", combined)
        failed_match = re.search(r"(\d+)\s+failed", combined)
        passed_count = int(passed_match.group(1)) if passed_match else 0
        failed_count = int(failed_match.group(1)) if failed_match else 0

        total = passed_count + failed_count
        score = passed_count / total if total > 0 else 0.0

        return EvaluationResult(
            score=score,
            passed=False,
            details=f"{passed_count} passed, {failed_count} failed",
        )


class CustomEvaluator:
    """Wrap a user-provided async callable as an evaluator."""

    def __init__(self, fn: Callable[..., Awaitable[EvaluationResult]]) -> None:
        self.fn = fn

    async def evaluate(self, context: dict[str, Any]) -> EvaluationResult:
        return await self.fn(context)


# ---------------------------------------------------------------------------
# GoalLoopExecutor
# ---------------------------------------------------------------------------

_DEFAULT_ESCALATION_WINDOW = 3


class GoalLoopExecutor:
    """Iteratively run attempts until a goal metric is achieved or timeout."""

    def __init__(
        self,
        goal: GoalSpec,
        evaluator: Evaluator,
        state: GoalLoopState | None = None,
        escalation_window: int = _DEFAULT_ESCALATION_WINDOW,
    ) -> None:
        self.goal = goal
        self.evaluator = evaluator
        self.state = state or GoalLoopState(goal=goal)
        self.escalation_window = escalation_window
        self.handoff_chain: list[Any] = []
        self.last_handoff: Any | None = None

    # -- public API --------------------------------------------------------

    async def run_loop(
        self,
        attempt_fn: Callable[..., Awaitable[dict[str, Any]]],
        on_progress: Callable[[GoalLoopState], Any] | None = None,
    ) -> GoalLoopState:
        """Execute the goal loop until target met, timeout, or max attempts."""
        state = self.state
        state.status = "running"
        deadline: float | None = None
        if self.goal.timeout_seconds is not None:
            deadline = state.start_time.timestamp() + self.goal.timeout_seconds

        attempt_number = len(state.attempts)

        while attempt_number < self.goal.max_attempts:
            if deadline is not None and time.time() >= deadline:
                state.status = "timeout"
                break

            if state.status == "stopped":
                break

            attempt_number += 1
            started = _utc_now()
            t0 = time.monotonic()

            try:
                attempt_context = await attempt_fn(
                    attempt_number=attempt_number,
                    strategy_tier=state.strategy_tier,
                    best_result=state.best_result,
                    previous_attempts=list(state.attempts),
                )
            except Exception:
                logger.exception("attempt_fn raised (attempt %d)", attempt_number)
                attempt_context = {}

            eval_result = await self.evaluator.evaluate(attempt_context)
            eval_result.passed = is_target_met(
                eval_result.score, self.goal.target_value, self.goal.comparison,
            )

            duration = time.monotonic() - t0

            record = AttemptRecord(
                attempt_number=attempt_number,
                result=eval_result,
                strategy_tier=state.strategy_tier,
                started_at=started,
                duration_seconds=duration,
                approach_summary=attempt_context.get("approach_summary", ""),
            )
            state.attempts.append(record)

            self._update_best(state, record)

            if not eval_result.passed:
                try:
                    diag = await self._diagnose_attempt(record, self.goal)
                    record.approach_summary = (
                        f"{record.approach_summary}  [{diag}]"
                        if record.approach_summary
                        else diag
                    )
                except Exception:
                    logger.debug("_diagnose_attempt failed", exc_info=True)

            self._maybe_escalate(state)

            try:
                _handoff = GoalAttemptAssembler.assemble(record, state)
                self.last_handoff = _handoff
                self.handoff_chain.append(_handoff)
            except Exception:
                logger.debug("Goal handoff assembly failed", exc_info=True)

            if on_progress:
                try:
                    on_progress(state)
                except Exception:
                    logger.debug("on_progress callback failed", exc_info=True)

            if eval_result.passed:
                state.status = "success"
                break
        else:
            if state.status == "running":
                state.status = "failed"

        return state

    # -- internals ---------------------------------------------------------

    def _update_best(self, state: GoalLoopState, record: AttemptRecord) -> None:
        score = record.result.score
        if state.best_result is None:
            state.best_attempt_id = record.id
            state.best_result = record.result
            return

        cmp = self.goal.comparison
        if cmp == "==":
            candidate_dist = abs(score - self.goal.target_value)
            incumbent_dist = abs(state.best_result.score - self.goal.target_value)
            if is_better(candidate_dist, incumbent_dist, "=="):
                state.best_attempt_id = record.id
                state.best_result = record.result
        else:
            if is_better(score, state.best_result.score, cmp):
                state.best_attempt_id = record.id
                state.best_result = record.result

    def _maybe_escalate(self, state: GoalLoopState) -> None:
        """Escalate strategy tier after N consecutive non-passing attempts at the current tier."""
        if state.strategy_tier >= 3:
            return
        window = self.escalation_window
        current_tier = state.strategy_tier
        recent_at_tier = [
            a for a in state.attempts[-window:]
            if a.strategy_tier == current_tier
        ]
        if len(recent_at_tier) < window:
            return
        if all(not a.result.passed for a in recent_at_tier):
            state.strategy_tier += 1

    async def _diagnose_attempt(self, record: AttemptRecord, goal: GoalSpec) -> str:
        """Run RepairClassifier (if available) for inter-attempt diagnosis."""
        score = record.result.score
        target = goal.target_value
        gap = abs(score - target)

        try:
            from dan.meta.repair import RepairClassifier
            from types import SimpleNamespace

            principle = SimpleNamespace(
                action=f"Score {score} missed target {goal.comparison} {target}",
                repair_level="prompt_fix",
            )
            classifier = RepairClassifier()
            level = classifier.classify(principle)
            return (
                f"Diagnosis: {level.name.lower()} — "
                f"score {score} vs target {target}, gap of {gap:.4f}"
            )
        except Exception:
            return (
                f"Diagnosis: score {score} vs target {target}, "
                f"gap of {gap:.4f}"
            )

    def stop(self) -> None:
        """Signal the loop to stop after the current attempt."""
        self.state.status = "stopped"


# ---------------------------------------------------------------------------
# State serialization helpers
# ---------------------------------------------------------------------------

def serialize_state(state: GoalLoopState) -> str:
    return state.model_dump_json(indent=2)


def deserialize_state(raw: str) -> GoalLoopState:
    return GoalLoopState.model_validate_json(raw)


# ---------------------------------------------------------------------------
# Natural language intent detection
# ---------------------------------------------------------------------------

_BEAT_PATTERN = re.compile(
    r"(?:beat|exceed|surpass|top)\s+(\d+(?:\.\d+)?)\s+(?:on\s+)?(\w[\w\s]*?)(?:\s+(?:score|metric))?$",
    re.IGNORECASE,
)
_IMPROVE_PATTERN = re.compile(
    r"keep\s+improving\s+(?:until\s+)?(\w[\w\s]*?)\s*(?:>=?|reaches?|hits?)\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_TEST_PATTERN = re.compile(
    r"(?:iterate\s+until\s+tests?\s+pass|get\s+tests?\s+to\s+pass)",
    re.IGNORECASE,
)
_ACCURACY_PATTERN = re.compile(
    r"(?:achieve|reach|get)\s+(\d+(?:\.\d+)?)\s*%?\s*(accuracy|coverage|precision|recall|f1)",
    re.IGNORECASE,
)
_REDUCE_PATTERN = re.compile(
    r"(?:reduce|minimize|lower|decrease)\s+(\w[\w\s]*?)\s+(?:below|under|to)\s+(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def detect_goal_intent(text: str) -> GoalSpec | None:
    """Recognize natural-language goal-loop intents without ``/goal`` syntax.

    Returns *None* if no goal intent is detected.
    """
    text = text.strip()

    m = _TEST_PATTERN.search(text)
    if m:
        return GoalSpec(
            metric_name="test_pass_rate",
            target_value=1.0,
            comparison=">=",
            evaluation_mode="test_suite",
        )

    m = _BEAT_PATTERN.search(text)
    if m:
        target = float(m.group(1))
        metric = m.group(2).strip()
        return GoalSpec(metric_name=metric, target_value=target, comparison=">=")

    m = _IMPROVE_PATTERN.search(text)
    if m:
        metric = m.group(1).strip()
        target = float(m.group(2))
        return GoalSpec(metric_name=metric, target_value=target, comparison=">=")

    m = _ACCURACY_PATTERN.search(text)
    if m:
        target = float(m.group(1))
        metric = m.group(2).strip()
        if target > 1:
            target /= 100.0
        return GoalSpec(metric_name=metric, target_value=target, comparison=">=")

    m = _REDUCE_PATTERN.search(text)
    if m:
        metric = m.group(1).strip()
        target = float(m.group(2))
        return GoalSpec(metric_name=metric, target_value=target, comparison="<=")

    return None


# ---------------------------------------------------------------------------
# Progress notification helper
# ---------------------------------------------------------------------------


def make_progress_callback(
    notify_fn: Callable[[str], Any] | None = None,
    interval_attempts: int = 5,
) -> Callable[[GoalLoopState], None]:
    """Create a callback that emits a progress message every *interval_attempts* attempts."""
    def _callback(state: GoalLoopState) -> None:
        n = len(state.attempts)
        if n % interval_attempts != 0:
            return
        best = state.best_result.score if state.best_result else "N/A"
        tier_label = STRATEGY_TIER_LABELS.get(state.strategy_tier, "unknown")
        elapsed = (_utc_now() - state.start_time).total_seconds()
        msg = (
            f"Goal loop progress: attempt {n}/{state.goal.max_attempts}, "
            f"best score: {best}, tier: {tier_label} "
            f"({elapsed:.0f}s elapsed)"
        )
        if notify_fn is not None:
            notify_fn(msg)
        else:
            logger.info(msg)

    return _callback


# ---------------------------------------------------------------------------
# Chat command handlers (standalone functions)
# ---------------------------------------------------------------------------

_GOAL_PATTERN = re.compile(
    r'/goal\s+"([^"]+)"\s*'
    r"(?:--timeout\s+(\S+))?\s*"
    r"(?:--eval\s+(\S+))?",
)

_TIMEOUT_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_timeout(raw: str) -> int:
    """Parse human-friendly timeout like ``24h``, ``30m``, ``86400``."""
    m = re.match(r"^(\d+)([smhd])?$", raw.strip(), re.IGNORECASE)
    if not m:
        raise ValueError(f"Invalid timeout format: {raw!r}")
    value = int(m.group(1))
    unit = (m.group(2) or "s").lower()
    return value * _TIMEOUT_UNITS[unit]


def _parse_goal_spec(expr: str) -> GoalSpec:
    """Parse ``"metric_name >= 0.85"`` into a GoalSpec."""
    for op in (">=", "<=", "==", ">", "<"):
        if op in expr:
            parts = expr.split(op, 1)
            metric = parts[0].strip()
            target = float(parts[1].strip())
            return GoalSpec(metric_name=metric, target_value=target, comparison=op)
    raise ValueError(f"Could not parse goal expression: {expr!r}")


def handle_goal_command(
    text: str,
    context: Any = None,
    project_id: str | None = None,
    task_id: str | None = None,
) -> str:
    """Parse ``/goal "score >= 0.85" --timeout 24h --eval script:evaluate.py``."""
    m = _GOAL_PATTERN.search(text)
    if not m:
        return (
            "Usage: /goal \"<metric> <op> <target>\" [--timeout <duration>] "
            "[--eval <mode>:<path>]\n"
            "Example: /goal \"score >= 0.85\" --timeout 24h --eval script:evaluate.py"
        )

    goal_expr = m.group(1)
    timeout_raw = m.group(2)
    eval_raw = m.group(3)

    try:
        spec = _parse_goal_spec(goal_expr)
    except ValueError as exc:
        return f"Error parsing goal: {exc}"

    if timeout_raw:
        try:
            spec.timeout_seconds = _parse_timeout(timeout_raw)
        except ValueError as exc:
            return f"Error parsing timeout: {exc}"

    if eval_raw:
        if ":" in eval_raw:
            mode, _ = eval_raw.split(":", 1)
            if mode in ("script", "llm_judge", "test_suite", "custom"):
                spec.evaluation_mode = mode  # type: ignore[assignment]

    state = GoalLoopState(goal=spec)
    session = GoalSession(
        goal_state=state,
        project_id=project_id,
        task_id=task_id,
    )
    if isinstance(context, dict):
        context["goal_state"] = state
        context["goal_session"] = session
    return (
        f"Goal loop started: {spec.metric_name} {spec.comparison} {spec.target_value}\n"
        f"Timeout: {spec.timeout_seconds or 'none'}s | "
        f"Max attempts: {spec.max_attempts} | "
        f"Eval: {spec.evaluation_mode}\n"
        f"Session: {session.session_id} | Status: {state.status}"
    )


def handle_goal_status_command(text: str, context: Any = None) -> str:
    """Show current goal loop state (stub — real state comes from context)."""
    if context and isinstance(context, GoalLoopState):
        state = context
        attempts_count = len(state.attempts)
        best_score = state.best_result.score if state.best_result else "N/A"
        elapsed = (datetime.now(timezone.utc) - state.start_time).total_seconds()
        return (
            f"Goal: {state.goal.metric_name} {state.goal.comparison} {state.goal.target_value}\n"
            f"Status: {state.status} | Tier: {state.strategy_tier} "
            f"({STRATEGY_TIER_LABELS.get(state.strategy_tier, 'unknown')})\n"
            f"Attempts: {attempts_count}/{state.goal.max_attempts} | "
            f"Best: {best_score}\n"
            f"Elapsed: {elapsed:.0f}s"
        )
    return "No active goal loop."


def handle_goal_stop_command(text: str, context: Any = None) -> str:
    """Stop current goal loop and return best result."""
    if context and isinstance(context, GoalLoopState):
        context.status = "stopped"
        best = context.best_result
        if best:
            return (
                f"Goal loop stopped. Best result: {best.score} "
                f"(passed={best.passed})\n"
                f"Details: {best.details or 'none'}"
            )
        return "Goal loop stopped. No attempts completed."
    return "No active goal loop to stop."
