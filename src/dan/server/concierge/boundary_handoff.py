"""Boundary handoff — typed context for execution step transitions (31-23)."""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class BoundaryHandoff(BaseModel):
    """Typed context passed from one execution step to the next."""

    boundary_type: Literal["build_iteration", "goal_attempt", "workflow_dep"]
    step_index: int
    step_label: str

    status: Literal["success", "partial", "failed"]
    confidence: float = 1.0
    validated_facts: list[str] = Field(default_factory=list)
    known_issues: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    artifacts_produced: list[str] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)

    suggestions: list[str] = Field(default_factory=list)
    must_preserve: list[str] = Field(default_factory=list)

    user_summary: str = ""

    def to_prompt_context(self, budget: int = 1500) -> str:
        """Render as structured prompt section, truncating to fit budget."""
        header = (
            f"## Prior Step: {self.step_label} [{self.status}] "
            f"(confidence: {self.confidence:.0%})"
        )
        parts = [header]

        section_defs = [
            ("Issues:", self.known_issues),
            ("Suggestions for this step:", self.suggestions),
            ("Must preserve:", self.must_preserve),
            ("Confirmed:", self.validated_facts),
            ("Assumptions:", self.assumptions),
        ]

        truncated = False
        for title, items in section_defs:
            if not items:
                continue
            section = title
            added_any = False
            for item in items:
                candidate = section + f"\n- {item}"
                full = "\n\n".join(parts + [candidate])
                if len(full) > budget:
                    truncated = True
                    break
                section = candidate
                added_any = True
            if added_any:
                parts.append(section)
            if truncated:
                break

        result = "\n\n".join(parts)
        if truncated:
            suffix = "\n..."
            if len(result) + len(suffix) > budget:
                result = result[: budget - len(suffix)]
            result += suffix
        return result[:budget]

    def to_user_summary(self) -> str:
        return self.user_summary


class BuildIterationAssembler:
    """Assemble BoundaryHandoff from a BuildSession after an iteration."""

    @staticmethod
    def assemble(session: Any) -> BoundaryHandoff:
        """Extract structured handoff from BuildSession state.

        Args:
            session: A BuildSession object with fields:
                - iteration_count: int
                - max_iterations: int
                - status: str
                - diagnosis_history: list[dict] — each dict may have keys:
                    'error', 'suggested_fix', 'repair_strategy',
                    'memory_context', 'error_patterns', 'assumed_context'
                - test_results: list[dict] — each with 'passed' (bool), 'error' (str)
                - goal_description: str
        """
        iteration = getattr(session, "iteration_count", 0)
        max_iter = getattr(session, "max_iterations", 1)
        raw_status = getattr(session, "status", "")
        diagnosis_history: list[dict] = getattr(session, "diagnosis_history", [])
        test_results: list[dict] = getattr(session, "test_results", [])

        status: Literal["success", "partial", "failed"]
        if raw_status == "completed":
            status = "success"
        elif raw_status == "failed":
            status = "failed"
        else:
            status = "partial"

        latest_has_error = bool(
            diagnosis_history and diagnosis_history[-1].get("error")
        )

        if test_results and test_results[-1].get("passed"):
            confidence = 1.0
        elif not test_results and latest_has_error:
            confidence = 0.3
        elif test_results and not test_results[-1].get("passed"):
            confidence = 0.2
        else:
            confidence = 0.5

        validated_facts: list[str] = []
        if not latest_has_error:
            validated_facts.append("Validation passed")
        if test_results and test_results[-1].get("passed"):
            validated_facts.append("Smoke test passed")

        known_issues: list[str] = []
        if diagnosis_history:
            error = diagnosis_history[-1].get("error", "")
            if error:
                known_issues.extend(
                    line.strip() for line in error.split("\n") if line.strip()
                )
        if test_results and not test_results[-1].get("passed"):
            test_error = test_results[-1].get("error", "")
            if test_error:
                known_issues.append(test_error)

        assumptions: list[str] = []
        if diagnosis_history:
            assumed = diagnosis_history[-1].get("assumed_context", "")
            if isinstance(assumed, str) and assumed:
                assumptions = [assumed]
            elif isinstance(assumed, list):
                assumptions = list(assumed)

        suggestions: list[str] = []
        if status != "success" and diagnosis_history:
            last_diag = diagnosis_history[-1]
            if last_diag.get("suggested_fix"):
                suggestions.append(last_diag["suggested_fix"])
            if last_diag.get("repair_strategy"):
                suggestions.append(last_diag["repair_strategy"])

        must_preserve: list[str] = []
        if status != "success" and len(diagnosis_history) > 1:
            for diag in diagnosis_history[:-1]:
                if not diag.get("error"):
                    must_preserve.append("Prior validation rules still apply")
                    break

        metrics = {"iteration": float(iteration), "max_iterations": float(max_iter)}

        if status == "success":
            one_liner = "All checks passed."
        elif known_issues:
            one_liner = known_issues[0]
        else:
            one_liner = ""
        user_summary = f"Iteration {iteration}: {status}. {one_liner}"
        if len(user_summary) > 120:
            user_summary = user_summary[:117] + "..."

        return BoundaryHandoff(
            boundary_type="build_iteration",
            step_index=iteration,
            step_label=f"Build iteration {iteration}/{max_iter}",
            status=status,
            confidence=confidence,
            validated_facts=validated_facts,
            known_issues=known_issues,
            assumptions=assumptions,
            suggestions=suggestions,
            must_preserve=must_preserve,
            metrics=metrics,
            user_summary=user_summary,
        )


class GoalAttemptAssembler:
    """Assemble BoundaryHandoff from an AttemptRecord + GoalLoopState."""

    @staticmethod
    def assemble(record: Any, state: Any) -> BoundaryHandoff:
        """Extract structured handoff from goal loop attempt.

        Args:
            record: An AttemptRecord with fields:
                - attempt_number: int
                - result: EvaluationResult (has .score, .passed, .details)
                - strategy_tier: int
                - approach_summary: str
            state: A GoalLoopState with fields:
                - goal: GoalSpec (has .target_value, .metric_name, .comparison)
                - best_result: EvaluationResult | None
                - attempts: list[AttemptRecord]
        """
        attempt = record.attempt_number
        result = record.result
        tier = record.strategy_tier
        goal = state.goal
        best = state.best_result

        status: Literal["success", "partial", "failed"] = (
            "success" if result.passed else "failed"
        )

        target = getattr(goal, "target_value", 0)
        if target > 0:
            confidence = min(result.score / target, 1.0)
        else:
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        validated_facts: list[str] = []
        if result.passed:
            validated_facts.append(
                f"{goal.metric_name} = {result.score} meets target "
                f"{goal.comparison} {target}"
            )
        details = getattr(result, "details", "")
        if details and result.passed:
            validated_facts.append(str(details)[:200])

        known_issues: list[str] = []
        if not result.passed:
            known_issues.append(
                f"{goal.metric_name} = {result.score} does not meet "
                f"{goal.comparison} {target}"
            )
            if details:
                known_issues.append(str(details)[:200])

        best_score = best.score if best else 0.0
        metrics = {
            "score": float(result.score),
            "target": float(target),
            "attempt": float(attempt),
            "best_so_far": float(best_score),
        }

        suggestions: list[str] = []
        if not result.passed:
            summary = getattr(record, "approach_summary", "")
            if summary:
                diag_tags = re.findall(r"\[Diagnosis:\s*([^\]]+)\]", summary)
                suggestions.extend(diag_tags)

        if result.passed:
            one_liner = "Target met."
        elif known_issues:
            one_liner = known_issues[0]
        else:
            one_liner = ""
        user_summary = (
            f"Attempt {attempt}: score {result.score:.2f} "
            f"(target {goal.comparison} {target}). {one_liner}"
        )
        if len(user_summary) > 120:
            user_summary = user_summary[:117] + "..."

        return BoundaryHandoff(
            boundary_type="goal_attempt",
            step_index=attempt,
            step_label=f"Goal attempt {attempt} (tier {tier})",
            status=status,
            confidence=confidence,
            validated_facts=validated_facts,
            known_issues=known_issues,
            metrics=metrics,
            suggestions=suggestions,
            user_summary=user_summary,
        )


class WorkflowDepAssembler:
    """Assemble BoundaryHandoff from a completed workflow RunResult."""

    @staticmethod
    def assemble(run_result: dict[str, Any], workflow_name: str) -> BoundaryHandoff:
        """Extract structured handoff from a workflow run result.

        Args:
            run_result: dict that may contain:
                - 'run_id': str
                - 'status': str ('completed', 'failed', etc.)
                - 'node_results': dict[str, Any] — per-node outcomes
                - 'total_cost': float
                - 'duration': float
                - 'outputs': dict — final outputs
                - 'errors': list[str]
            workflow_name: str — name of the upstream workflow
        """
        raw_status = run_result.get("status", "")
        status: Literal["success", "partial", "failed"]
        if raw_status == "completed":
            status = "success"
        elif raw_status == "failed":
            status = "failed"
        else:
            status = "partial"

        node_results = run_result.get("node_results", {})
        nodes_total = len(node_results)
        nodes_succeeded = 0
        failed_nodes: list[tuple[str, str]] = []
        succeeded_nodes: list[str] = []

        for node_id, node_data in node_results.items():
            node_status = ""
            if isinstance(node_data, dict):
                node_status = node_data.get("status", "")
            if node_status in ("completed", "success"):
                nodes_succeeded += 1
                succeeded_nodes.append(node_id)
            elif node_status in ("failed", "error"):
                error_msg = ""
                if isinstance(node_data, dict):
                    error_msg = node_data.get("error", "")
                failed_nodes.append((node_id, error_msg))

        if nodes_total > 0:
            confidence = nodes_succeeded / nodes_total
        elif status == "success":
            confidence = 1.0
        else:
            confidence = 0.0

        validated_facts = [f"Node '{n}' succeeded" for n in succeeded_nodes[:5]]

        known_issues: list[str] = []
        for node_id, error_msg in failed_nodes[:10]:
            entry = f"Node '{node_id}' failed"
            if error_msg:
                entry += f": {error_msg[:100]}"
            known_issues.append(entry)
        for err in run_result.get("errors", [])[:5]:
            known_issues.append(str(err)[:200])

        artifacts: list[str] = []
        outputs = run_result.get("outputs", {})
        if outputs:
            artifacts.extend(str(k) for k in list(outputs.keys())[:5])
        run_id = run_result.get("run_id", "")
        if run_id:
            artifacts.append(f"run:{run_id}")

        cost = run_result.get("total_cost", 0.0)
        duration = run_result.get("duration", 0.0)
        metrics = {
            "nodes_completed": float(nodes_succeeded),
            "total_cost": float(cost),
            "duration": float(duration),
        }

        suggestions: list[str] = []
        if confidence < 0.8 or failed_nodes:
            for node_id, error_msg in failed_nodes[:3]:
                if error_msg:
                    suggestions.append(f"Investigate '{node_id}': {error_msg[:80]}")
            if not suggestions and run_result.get("errors"):
                suggestions.append(
                    f"Review errors: {run_result['errors'][0][:100]}"
                )

        user_summary = (
            f"Workflow '{workflow_name}': {status}. "
            f"{nodes_succeeded} nodes completed, cost ${cost:.2f}."
        )
        if len(user_summary) > 120:
            user_summary = user_summary[:117] + "..."

        return BoundaryHandoff(
            boundary_type="workflow_dep",
            step_index=0,
            step_label=f"Workflow '{workflow_name}'",
            status=status,
            confidence=confidence,
            validated_facts=validated_facts,
            known_issues=known_issues,
            artifacts_produced=artifacts,
            metrics=metrics,
            suggestions=suggestions,
            user_summary=user_summary,
        )


class HandoffChainSummary:
    """Summarize a chain of BoundaryHandoff objects into a compact narrative."""

    @staticmethod
    def summarize(handoffs: list[BoundaryHandoff], budget: int = 600) -> str:
        """Produce a compact narrative from a chain of handoffs.

        For short chains (1-3), include per-step details.
        For longer chains, summarize early steps to one line each, detail last 2.
        """
        if not handoffs:
            return ""

        def _step_desc(h: BoundaryHandoff) -> str:
            if h.status == "success":
                return f"{h.step_label}: {h.status}"
            if h.known_issues:
                issues = ", ".join(h.known_issues[:2])
            else:
                issues = "unknown cause" if h.status != "success" else "no issues"
            return f"{h.step_label}: {h.status} ({issues})"

        if len(handoffs) <= 3:
            result = "; ".join(_step_desc(h) for h in handoffs) + "."
        else:
            early = handoffs[:-2]
            late = handoffs[-2:]

            early_issues: list[str] = []
            for h in early:
                if h.known_issues:
                    early_issues.extend(h.known_issues[:1])

            if early_issues:
                compressed = ", ".join(early_issues[:3])
                early_summary = f"Steps 1-{len(early)}: [{compressed}]"
            else:
                early_summary = f"Steps 1-{len(early)}: completed"

            result = (
                early_summary
                + ". "
                + "; ".join(_step_desc(h) for h in late)
                + "."
            )

        if len(result) > budget:
            result = result[: budget - 3] + "..."
        return result
