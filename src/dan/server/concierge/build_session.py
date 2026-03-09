"""Build session state machine for iterative workflow construction (29-3).

Multi-round build → validate → test → diagnose → modify → re-test loop
driven by the concierge within a ConciergeGoal.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .fan_out import fan_out_dict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Build session models (29-3 §1)
# ---------------------------------------------------------------------------

class BuildSessionStatus(str, Enum):
    DRAFTING = "drafting"
    VALIDATING = "validating"
    TESTING = "testing"
    DIAGNOSING = "diagnosing"
    MODIFYING = "modifying"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"


class BuildIteration(BaseModel):
    """Single iteration within a build session (29-3 §1-2)."""

    iteration_num: int = 0
    action: Literal["build", "modify", "repair"] = "build"
    graph_snapshot_id: str | None = None
    validation_result: dict[str, Any] | None = None
    test_run_id: str | None = None
    test_outcome: str | None = None
    diagnosis: dict[str, Any] | None = None
    modification_applied: dict[str, Any] | None = None


class BuildSession(BaseModel):
    """State of a multi-round workflow build session (29-3 §1-1)."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    goal_id: str = ""
    goal_description: str = ""
    workflow_id: str | None = None
    status: str = BuildSessionStatus.DRAFTING.value
    iteration_count: int = 0
    max_iterations: int = 5
    draft_history: list[dict[str, Any]] = Field(default_factory=list)
    test_results: list[dict[str, Any]] = Field(default_factory=list)
    diagnosis_history: list[dict[str, Any]] = Field(default_factory=list)
    user_feedback: list[str] = Field(default_factory=list)
    iterations: list[BuildIteration] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Build session manager (29-3 §2-1)
# ---------------------------------------------------------------------------

class BuildSessionManager:
    """Manages build session state transitions (29-3 §2-1).

    State machine: DRAFTING → VALIDATING → TESTING → ... → COMPLETED/FAILED.
    """

    def __init__(self, memory_kernel: Any = None) -> None:
        self.memory_kernel = memory_kernel

    def create(
        self,
        goal_id: str,
        max_iterations: int = 5,
        *,
        goal_description: str = "",
    ) -> BuildSession:
        """Create a new build session for a goal."""
        return BuildSession(
            goal_id=goal_id,
            goal_description=goal_description.strip(),
            max_iterations=max_iterations,
            status=BuildSessionStatus.DRAFTING.value,
        )

    def transition_to(self, session: BuildSession, new_status: str) -> BuildSession:
        """Transition session to a new status (29-3 §2-2 to §2-9)."""
        session.status = new_status
        session.updated_at = time.time()
        return session

    def add_iteration(
        self,
        session: BuildSession,
        action: Literal["build", "modify", "repair"] = "build",
        **kwargs: Any,
    ) -> BuildIteration:
        """Record a new iteration."""
        session.iteration_count += 1
        it = BuildIteration(
            iteration_num=session.iteration_count,
            action=action,
            **kwargs,
        )
        session.iterations.append(it)
        session.updated_at = time.time()
        return it

    def add_user_feedback(self, session: BuildSession, feedback: str) -> None:
        """Store user feedback for the current state (29-3 §3-4)."""
        session.user_feedback.append(feedback.strip())
        session.updated_at = time.time()

    def save(self, session: BuildSession) -> None:
        """Persist BuildSession as WORKING_STATE in memory kernel (29-3 §1-3)."""
        if not self.memory_kernel:
            return
        from dan.engine.memory_kernel import MemoryItem, MemoryScope, MemoryType

        item_id = f"build_session_{session.id}"
        item = MemoryItem(
            id=item_id,
            content=session.model_dump_json(),
            memory_type=MemoryType.WORKING_STATE,
            scope=MemoryScope.USER,
            metadata={"goal_id": session.goal_id, "build_session_id": session.id},
        )
        self.memory_kernel.store(item)

    def load(self, session_id: str) -> BuildSession | None:
        """Load BuildSession from memory kernel."""
        if not self.memory_kernel:
            return None
        item_id = f"build_session_{session_id}"
        item = self.memory_kernel.get(item_id)
        if item is None:
            return None
        try:
            return BuildSession.model_validate_json(item.content)
        except Exception:
            return None

    # -----------------------------------------------------------------------
    # Validation (29-3 §2-3)
    # -----------------------------------------------------------------------

    def validate_draft(self, graph: Any) -> list[str]:
        """Run validate_graph on draft; return list of error messages (29-3 §2-3)."""
        from dan.validation.graph import validate_graph

        return validate_graph(graph)

    # -----------------------------------------------------------------------
    # Smoke test (29-3 §4)
    # -----------------------------------------------------------------------

    @staticmethod
    def generate_smoke_inputs(graph: Any) -> dict[str, Any]:
        """Auto-generate sample inputs from InputNode variables (29-3 §4-1)."""
        inputs: dict[str, Any] = {}
        for node in getattr(graph, "nodes", []) or []:
            if getattr(node, "node_type", "") != "input":
                continue
            for var in getattr(node, "variables", []) or []:
                name = getattr(var, "name", None) or (var.get("name") if isinstance(var, dict) else None)
                if not name:
                    continue
                vtype = getattr(var, "type", "string") or (var.get("type", "string") if isinstance(var, dict) else "string")
                default = getattr(var, "default", None) if hasattr(var, "default") else (var.get("default") if isinstance(var, dict) else None)
                if default is not None:
                    inputs[name] = default
                elif vtype == "number":
                    inputs[name] = 0
                elif vtype == "boolean":
                    inputs[name] = True
                else:
                    inputs[name] = "sample"
        return inputs

    async def run_smoke_test(
        self,
        session: BuildSession,
        graph: Any,
        graph_id: str,
        run_manager: Any,
        *,
        inputs: dict[str, Any] | None = None,
        timeout: float = 60.0,
        skip_smoke: bool = False,
        goal_context: dict[str, Any] | None = None,
    ) -> tuple[str | None, bool, str]:
        """Run smoke test via RunManager (29-3 §4-2, §4-3).

        Returns (run_id, passed, error_message).
        Skips if DAN_BUILD_SKIP_SMOKE=1 (29-3 §4-4).
        """
        if skip_smoke or os.environ.get("DAN_BUILD_SKIP_SMOKE", "").strip() == "1":
            return None, True, ""

        smoke_inputs = inputs if inputs is not None else self.generate_smoke_inputs(graph)
        record = await run_manager.start_run(
            graph,
            graph_id=graph_id,
            inputs=smoke_inputs or None,
            goal_context=goal_context,
        )
        run_id = record.run_id

        # Poll until run completes or timeout
        from dan.server.run_manager import RunStatus

        deadline = time.monotonic() + timeout
        while True:
            rec = run_manager.get_run(run_id)
            if rec is None:
                return run_id, False, "Run record lost"
            status = getattr(rec, "status", None)
            if status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
                break
            if time.monotonic() >= deadline:
                try:
                    await run_manager.cancel_run(run_id)
                except Exception:
                    logger.debug("Failed to cancel timed out smoke test %s", run_id, exc_info=True)
                return run_id, False, f"Smoke test timed out after {timeout}s"
            await asyncio.sleep(0.5)

        error = getattr(rec, "error", None) or ""
        passed = status == RunStatus.COMPLETED and not error
        return run_id, passed, error or ("" if passed else "Run failed")

    # -----------------------------------------------------------------------
    # Diagnosis (29-3 §2-5, §5-1, §5-3)
    # -----------------------------------------------------------------------

    def diagnose_for_failure(
        self,
        session: BuildSession,
        error_message: str,
        *,
        limit: int = 8,
        graph_dict: dict[str, Any] | None = None,
        generated_code: str = "",
    ) -> dict[str, Any]:
        """Diagnose a build failure.

        The synchronous entry point is kept for compatibility with call sites
        that are not already in the DIAGNOSING async path. When no event loop is
        running it delegates to the async fan-out implementation; otherwise it
        falls back to the same source helpers sequentially.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.diagnose_for_failure_async(
                session,
                error_message,
                limit=limit,
                graph_dict=graph_dict,
                generated_code=generated_code,
            ))
        return self._diagnose_for_failure_sequential(
            session,
            error_message,
            limit=limit,
            graph_dict=graph_dict,
            generated_code=generated_code,
        )

    async def diagnose_for_failure_async(
        self,
        session: BuildSession,
        error_message: str,
        *,
        limit: int = 8,
        graph_dict: dict[str, Any] | None = None,
        generated_code: str = "",
    ) -> dict[str, Any]:
        """Gather diagnosis inputs concurrently, then aggregate them."""
        result = self._base_diagnosis_result(error_message)
        tasks: dict[str, Any] = {
            "diagnosis_loop": lambda: asyncio.to_thread(
                self._run_diagnosis_loop,
                error_message,
                graph_dict=graph_dict,
                generated_code=generated_code,
            )
        }
        if self.memory_kernel:
            tasks.update({
                "memory_repair": lambda: asyncio.to_thread(
                    self._retrieve_repair_memory_context,
                    error_message,
                    limit,
                ),
                "error_patterns": lambda: asyncio.to_thread(
                    self._retrieve_error_patterns,
                    error_message,
                    limit,
                ),
                "principles": lambda: asyncio.to_thread(
                    self._retrieve_principles,
                    error_message,
                    limit,
                ),
                "similar_workflows": lambda: asyncio.to_thread(
                    self._retrieve_similar_workflows,
                    session,
                    error_message,
                    limit,
                ),
            })
            if graph_dict:
                tasks["topology_advice"] = lambda: asyncio.to_thread(
                    self._retrieve_topology_suggestions,
                    graph_dict,
                )

        gathered = await fan_out_dict(tasks)
        for source_name, source_result in gathered.items():
            self._merge_diagnosis_source(result, source_name, source_result)
        self._finalize_diagnosis_result(result)
        return result

    def _diagnose_for_failure_sequential(
        self,
        session: BuildSession,
        error_message: str,
        *,
        limit: int = 8,
        graph_dict: dict[str, Any] | None = None,
        generated_code: str = "",
    ) -> dict[str, Any]:
        result = self._base_diagnosis_result(error_message)
        if self.memory_kernel:
            for source_name, loader in (
                ("memory_repair", lambda: self._retrieve_repair_memory_context(error_message, limit)),
                ("error_patterns", lambda: self._retrieve_error_patterns(error_message, limit)),
                ("principles", lambda: self._retrieve_principles(error_message, limit)),
                ("similar_workflows", lambda: self._retrieve_similar_workflows(session, error_message, limit)),
            ):
                try:
                    self._merge_diagnosis_source(result, source_name, loader())
                except Exception as exc:
                    logger.debug("Diagnosis source %s failed", source_name, exc_info=True)
                    result.setdefault("source_failures", {})[source_name] = str(exc)
        try:
            self._merge_diagnosis_source(
                result,
                "diagnosis_loop",
                self._run_diagnosis_loop(
                    error_message,
                    graph_dict=graph_dict,
                    generated_code=generated_code,
                ),
            )
        except Exception as exc:
            logger.debug("Diagnosis loop failed", exc_info=True)
            result.setdefault("source_failures", {})["diagnosis_loop"] = str(exc)
        self._finalize_diagnosis_result(result)
        return result

    @staticmethod
    def _base_diagnosis_result(error_message: str) -> dict[str, Any]:
        return {
            "error": error_message,
            "memory_context": "",
            "memory_matches": [],
            "failure_patterns": [],
            "principles": [],
            "similar_workflows": [],
            "classified_errors": [],
            "suggested_strategies": [],
            "items_retrieved": 0,
            "requires_user_review": False,
            "review_reason": "",
        }

    def _retrieve_repair_memory_context(self, error_message: str, limit: int) -> dict[str, Any]:
        scored = self.memory_kernel.retrieve_by_task(
            error_message,
            task_type="workflow_repair",
            limit=limit,
        )
        structured_matches: list[dict[str, Any]] = []
        for si in scored[:5]:
            structured_matches.append({
                "id": si.item.id,
                "memory_type": si.item.memory_type.value,
                "content": si.item.content[:200],
                "score": round(si.score, 3),
                "tags": list(si.item.tags or []),
                "metadata": dict(si.item.metadata or {}),
            })
        lines = [f"[Memory] {si.item.content[:200]}" for si in scored[:5]]
        return {
            "memory_context": "\n".join(lines) if lines else "",
            "memory_matches": structured_matches,
            "items_retrieved": len(scored),
        }

    def _retrieve_error_patterns(self, error_message: str, limit: int) -> dict[str, Any]:
        from dan.engine.memory_kernel import MemoryType

        return {
            "failure_patterns": self._rank_memory_matches(
                memory_type=MemoryType.FAILURE_PATTERN,
                query=error_message,
                limit=min(limit, 5),
                formatter=lambda item, score: {
                    "id": item.id,
                    "memory_type": item.memory_type.value,
                    "content": item.content[:200],
                    "score": round(score, 3),
                    "tags": list(item.tags or []),
                    "metadata": dict(item.metadata or {}),
                },
            )
        }

    def _retrieve_principles(self, error_message: str, limit: int) -> dict[str, Any]:
        from dan.engine.memory_kernel import MemoryType

        return {
            "principles": self._rank_memory_matches(
                memory_type=MemoryType.PRINCIPLE,
                query=error_message,
                limit=min(limit, 5),
                formatter=lambda item, score: {
                    "id": item.id,
                    "memory_type": item.memory_type.value,
                    "content": item.content[:200],
                    "score": round(score, 3),
                    "tags": list(item.tags or []),
                    "metadata": dict(item.metadata or {}),
                },
            )
        }

    def _retrieve_similar_workflows(
        self,
        session: BuildSession,
        error_message: str,
        limit: int,
    ) -> dict[str, Any]:
        from dan.engine.memory_kernel import MemoryType

        query = " ".join(
            part for part in (session.goal_description, error_message) if part
        ).strip() or error_message
        return {
            "similar_workflows": self._rank_memory_matches(
                memory_type=MemoryType.WORKFLOW_ASSET,
                query=query,
                limit=min(limit, 5),
                formatter=lambda item, score: {
                    "id": item.id,
                    "memory_type": item.memory_type.value,
                    "workflow_id": item.metadata.get("workflow_id", ""),
                    "content": item.content[:200],
                    "score": round(score, 3),
                    "tags": list(item.tags or []),
                    "metadata": dict(item.metadata or {}),
                },
            )
        }

    def _run_diagnosis_loop(
        self,
        error_message: str,
        *,
        graph_dict: dict[str, Any] | None = None,
        generated_code: str = "",
    ) -> dict[str, Any]:
        from dan.meta.diagnosis import DiagnosisLoop, ErrorClassifier

        classifier = ErrorClassifier()
        errors = classifier.classify(error_message)
        if not errors:
            return {}
        loop = DiagnosisLoop(max_attempts=1)
        strategies: list[str] = []
        classified_errors = [
            {"type": e.error_type.value, "message": e.message, "recoverable": e.recoverable}
            for e in errors
        ]
        for err in errors:
            artifact = loop.mapper.map_to_artifact(err, code=generated_code, graph_dict=graph_dict)
            strategy = loop.selector.select(err, artifact)
            strategies.append(strategy.value)
        return {
            "classified_errors": classified_errors,
            "suggested_strategies": strategies,
        }

    def _rank_memory_matches(
        self,
        *,
        memory_type: Any,
        query: str,
        limit: int,
        formatter: Any,
    ) -> list[dict[str, Any]]:
        candidates = self.memory_kernel.list_by_type(
            memory_type,
            limit=max(limit * 4, 12),
        )
        ranked: list[tuple[float, Any]] = []
        for item in candidates:
            score = self._match_score(query, item.content)
            if memory_type.value == "principle":
                score += min(float(item.metadata.get("confidence", 0.0) or 0.0), 1.0) * 0.25
            if memory_type.value == "workflow_asset":
                score += min(float(item.metadata.get("success_rate", 0.0) or 0.0), 1.0) * 0.2
            if score <= 0:
                continue
            ranked.append((score, item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [formatter(item, score) for score, item in ranked[:limit]]

    @staticmethod
    def _match_score(query: str, content: str) -> float:
        query_words = {word for word in query.lower().split() if word}
        content_words = {word for word in content.lower().split() if word}
        if not query_words or not content_words:
            return 0.0
        overlap = len(query_words & content_words) / len(query_words)
        substring_bonus = 0.15 if query.lower() in content.lower() else 0.0
        return overlap + substring_bonus

    def _merge_diagnosis_source(
        self,
        result: dict[str, Any],
        source_name: str,
        source_result: Any,
    ) -> None:
        if isinstance(source_result, Exception):
            logger.debug("Diagnosis source %s failed", source_name, exc_info=True)
            result.setdefault("source_failures", {})[source_name] = str(source_result)
            return
        if not isinstance(source_result, dict):
            return
        for key, value in source_result.items():
            if key in {
                "memory_matches",
                "failure_patterns",
                "principles",
                "similar_workflows",
                "classified_errors",
                "suggested_strategies",
                "topology_suggestions",
            }:
                if value:
                    result[key] = value
            elif key == "items_retrieved":
                result[key] = int(value or 0)
            elif value:
                result[key] = value
        if any(
            match.get("metadata", {}).get("repair_level") in {"structural_fix", "redesign"}
            for match in result.get("principles", [])
        ):
            result["requires_user_review"] = True
            result["review_reason"] = "structural_change"
        if "suggest_to_user" in result.get("suggested_strategies", []):
            result["requires_user_review"] = True
            result["review_reason"] = "structural_change"
        if result.get("topology_suggestions"):
            result["requires_user_review"] = True
            result.setdefault("review_reason", "topology_advice")

    def _finalize_diagnosis_result(self, result: dict[str, Any]) -> None:
        sections: list[str] = []
        if result.get("memory_context"):
            sections.append(str(result["memory_context"]))
        if result.get("failure_patterns"):
            sections.append(
                "\n".join(
                    f"[Failure Pattern] {item['content']}"
                    for item in result["failure_patterns"][:3]
                )
            )
        if result.get("principles"):
            sections.append(
                "\n".join(
                    f"[Principle] {item['content']}"
                    for item in result["principles"][:3]
                )
            )
        if result.get("similar_workflows"):
            sections.append(
                "\n".join(
                    f"[Workflow {item.get('workflow_id') or 'unknown'}] {item['content']}"
                    for item in result["similar_workflows"][:3]
                )
            )
        if result.get("classified_errors"):
            sections.append(
                "\n".join(
                    f"[Classified] {item['type']}: {item['message']}"
                    for item in result["classified_errors"][:3]
                )
            )
        if result.get("suggested_strategies"):
            sections.append(
                f"[Strategies] {', '.join(result['suggested_strategies'][:5])}"
            )
        if result.get("topology_suggestions"):
            sections.append(
                "\n".join(
                    f"[Topology] {item}"
                    for item in result["topology_suggestions"][:5]
                )
            )
        result["diagnosis_context"] = "\n\n".join(
            section for section in sections if section
        )

    def _retrieve_topology_suggestions(self, graph_dict: dict[str, Any]) -> dict[str, Any]:
        if self.memory_kernel is None:
            return {"topology_suggestions": []}
        try:
            from dan.engine.outcome_trackers import TopologyAdvisor, TopologyOutcomeTracker

            tracker = TopologyOutcomeTracker(self.memory_kernel)
            advisor = TopologyAdvisor(tracker)
            return {"topology_suggestions": advisor.suggest(graph_dict)}
        except Exception:
            logger.debug("Topology advisor failed", exc_info=True)
            return {"topology_suggestions": []}

    # -----------------------------------------------------------------------
    # Concurrent fix application (29-5 §4-3)
    # -----------------------------------------------------------------------

    async def apply_independent_modifications(
        self,
        modifications: list[dict[str, Any]],
        graph: Any,
    ) -> list[dict[str, Any]]:
        """Apply multiple independent modifications concurrently via fan_out (29-5 §4-3).

        Each modification dict must have a ``"apply"`` key with an async callable
        ``(graph) -> dict`` that returns a result summary. Modifications that target
        different nodes are independent and fan out; those targeting the same node
        are grouped and run sequentially within their group.

        Returns list of result dicts (one per modification, preserving order).
        """
        if not modifications:
            return []

        by_node: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for idx, mod in enumerate(modifications):
            target = mod.get("target_node", "_global")
            by_node.setdefault(target, []).append((idx, mod))

        async def _run_group(group: list[tuple[int, dict[str, Any]]]) -> list[tuple[int, dict[str, Any]]]:
            results: list[tuple[int, dict[str, Any]]] = []
            for idx, mod in group:
                fn = mod.get("apply")
                if fn is None:
                    results.append((idx, {"error": "no apply function"}))
                    continue
                try:
                    r = await fn(graph)
                    results.append((idx, r if isinstance(r, dict) else {"result": r}))
                except Exception as exc:
                    results.append((idx, {"error": str(exc)}))
            return results

        groups = list(by_node.values())
        if len(groups) == 1:
            raw = await _run_group(groups[0])
        else:
            gathered = await fan_out_dict({
                node_key: (lambda g=grp: _run_group(g))
                for node_key, grp in by_node.items()
            })
            raw: list[tuple[int, dict[str, Any]]] = []
            for v in gathered.values():
                if isinstance(v, list):
                    raw.extend(v)
                elif isinstance(v, Exception):
                    logger.debug("Modification group failed", exc_info=v)

        raw.sort(key=lambda x: x[0])
        return [r for _, r in raw]

    # -----------------------------------------------------------------------
    # Advance after execution (29-3 §2-5 to §2-9, §3-2)
    # -----------------------------------------------------------------------

    async def advance_after_execution(
        self,
        session: BuildSession,
        graph: Any,
        graph_id: str,
        run_manager: Any,
        *,
        smoke_timeout: float = 60.0,
        status_callback: Any = None,
        skip_smoke: bool = False,
        goal_context: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Advance build session: validate → test → diagnose → COMPLETED/FAILED (29-3 §3-2).

        Returns (final_status, message).
        """
        def _emit(msg: str) -> None:
            if status_callback:
                try:
                    status_callback(msg)
                except Exception:
                    pass

        if session.iteration_count >= session.max_iterations:
            self.transition_to(session, BuildSessionStatus.FAILED.value)
            return BuildSessionStatus.FAILED.value, "Max iterations exceeded"

        # Validate
        self.transition_to(session, BuildSessionStatus.VALIDATING.value)
        prep = await fan_out_dict({
            "errors": lambda: asyncio.to_thread(self.validate_draft, graph),
            "smoke_inputs": lambda: asyncio.to_thread(self.generate_smoke_inputs, graph),
        })
        errors = prep.get("errors", [])
        smoke_inputs = prep.get("smoke_inputs")
        if isinstance(errors, Exception):
            raise errors
        if errors:
            diag = await self.diagnose_for_failure_async(session, "; ".join(errors[:3]))
            session.diagnosis_history.append(diag)
            self.add_iteration(session, action="build", validation_result={"errors": errors}, diagnosis=diag)
            if session.iteration_count >= session.max_iterations:
                self.transition_to(session, BuildSessionStatus.FAILED.value)
                return BuildSessionStatus.FAILED.value, f"Validation failed: {errors[0][:200]}"
            self.transition_to(session, BuildSessionStatus.MODIFYING.value)
            return BuildSessionStatus.MODIFYING.value, f"Validation errors: {errors[0][:150]}"
        if isinstance(smoke_inputs, Exception):
            raise smoke_inputs

        _emit(f"[Building] Iteration {session.iteration_count + 1}/{session.max_iterations}: validation passed, running smoke test...")
        self.transition_to(session, BuildSessionStatus.TESTING.value)

        # Smoke test
        run_id, passed, err_msg = await self.run_smoke_test(
            session,
            graph,
            graph_id,
            run_manager,
            inputs=smoke_inputs,
            timeout=smoke_timeout,
            skip_smoke=skip_smoke,
            goal_context=goal_context,
        )
        session.test_results.append({"run_id": run_id, "passed": passed, "error": err_msg})
        it = self.add_iteration(session, action="build", test_run_id=run_id, test_outcome="pass" if passed else "fail")

        if passed:
            self.transition_to(session, BuildSessionStatus.COMPLETED.value)
            return BuildSessionStatus.COMPLETED.value, "Smoke test passed"
        # Test failed → diagnose
        self.transition_to(session, BuildSessionStatus.DIAGNOSING.value)
        diag = await self.diagnose_for_failure_async(session, err_msg or "Run failed")
        session.diagnosis_history.append(diag)
        it.diagnosis = diag
        if session.iteration_count >= session.max_iterations:
            self.transition_to(session, BuildSessionStatus.FAILED.value)
            return BuildSessionStatus.FAILED.value, f"Test failed after {session.max_iterations} iterations"
        self.transition_to(session, BuildSessionStatus.MODIFYING.value)
        return BuildSessionStatus.MODIFYING.value, f"Test failed: {err_msg[:150]}. Diagnosis context retrieved."

    # -----------------------------------------------------------------------
    # Post-build memory extraction (29-3 §7)
    # -----------------------------------------------------------------------

    @staticmethod
    def _detect_workflow_pattern(graph_dict: dict[str, Any]) -> str | None:
        """Detect generalizable topology shape (29-3 §7-2)."""
        nodes = graph_dict.get("nodes") or []
        edges = graph_dict.get("edges") or []
        if len(nodes) < 2:
            return None
        node_ids = {n.get("id") if isinstance(n, dict) else getattr(n, "id", None) for n in nodes}
        node_ids = {nid for nid in node_ids if nid}
        if not node_ids:
            return None
        out_degree: dict[str, int] = {nid: 0 for nid in node_ids}
        in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
        for e in edges:
            src = e.get("source_node_id") if isinstance(e, dict) else getattr(e, "source_node_id", None)
            tgt = e.get("target_node_id") if isinstance(e, dict) else getattr(e, "target_node_id", None)
            if src and tgt and src in out_degree and tgt in in_degree:
                out_degree[src] = out_degree.get(src, 0) + 1
                in_degree[tgt] = in_degree.get(tgt, 0) + 1
        has_loop = any(
            (n.get("node_type") or getattr(n, "node_type", "")) in ("while_loop", "for_each")
            for n in nodes
        )
        if has_loop:
            return "loop"
        max_out = max(out_degree.values()) if out_degree else 0
        max_in = max(in_degree.values()) if in_degree else 0
        if max_out >= 2 and max_in >= 2:
            return "dag"
        if max_out >= 2:
            return "fan_out"
        if max_in >= 2:
            return "fan_in"
        return "linear"

    def extract_post_build_memory(
        self,
        session: BuildSession,
        outcome: Literal["completed", "failed"],
        graph_dict: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Extract memory items from build session (29-3 §7-1 to §7-5)."""
        if not self.memory_kernel:
            return []
        from dan.engine.memory_kernel import MemoryItem, MemoryScope, MemoryType

        items: list[MemoryItem] = []
        if outcome == "completed" and graph_dict:
            name = (graph_dict.get("metadata") or {}).get("name") or session.workflow_id or "workflow"
            goal_summary = session.goal_description.strip() or session.goal_id
            feedback_summary = ""
            if session.user_feedback:
                feedback_summary = (
                    f" Feedback: {'; '.join(fb.strip() for fb in session.user_feedback[-3:] if fb.strip())[:240]}."
                )
            content = (
                f"Goal: {goal_summary}. Workflow: {name}. "
                f"Topology: {len(graph_dict.get('nodes', []))} nodes.{feedback_summary}"
            )
            items.append(
                MemoryItem(
                    id=f"workflow_asset:{session.workflow_id or session.goal_id}",
                    content=content,
                    memory_type=MemoryType.WORKFLOW_ASSET,
                    scope=MemoryScope.USER,
                    metadata={
                        "goal_id": session.goal_id,
                        "goal_description": goal_summary,
                        "workflow_id": session.workflow_id,
                        "success_rate": 1.0,
                        "reuse_count": 0,
                        "reuse_success_count": 0,
                    },
                )
            )
            pattern = self._detect_workflow_pattern(graph_dict)
            if pattern:
                items.append(
                    MemoryItem(
                        content=(
                            f"Goal: {goal_summary}. Pattern: {pattern}. "
                            f"Nodes: {len(graph_dict.get('nodes', []))}."
                        ),
                        memory_type=MemoryType.WORKFLOW_PATTERN,
                        scope=MemoryScope.USER,
                        metadata={"goal_id": session.goal_id, "goal_description": goal_summary},
                    )
                )
        if outcome == "failed" and session.diagnosis_history:
            last = session.diagnosis_history[-1]
            goal_summary = session.goal_description.strip() or session.goal_id
            content = f"Build failed. Goal: {goal_summary}. Last error: {last.get('error', '')[:300]}"
            items.append(
                MemoryItem(
                    content=content,
                    memory_type=MemoryType.FAILURE_PATTERN,
                    scope=MemoryScope.USER,
                    metadata={"goal_id": session.goal_id, "goal_description": goal_summary},
                )
            )
        for fb in session.user_feedback:
            if fb and len(fb.strip()) > 3:
                items.append(
                    MemoryItem(
                        content=fb.strip()[:200],
                        memory_type=MemoryType.FACT,
                        scope=MemoryScope.USER,
                        metadata={"source": "build_feedback"},
                    )
                )
        return items

    def store_post_build_memory(
        self,
        session: BuildSession,
        outcome: Literal["completed", "failed"],
        graph_dict: dict[str, Any] | None = None,
    ) -> None:
        """Extract and store post-build memory (29-3 §7-5)."""
        items = self.extract_post_build_memory(session, outcome, graph_dict)
        for item in items:
            try:
                self.memory_kernel.store(item)
            except Exception:
                pass
