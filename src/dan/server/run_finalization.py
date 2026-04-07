"""Post-run finalization helpers for RunManager."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from dan.models.graph import Graph
from dan.models.ports import InputPort, OutputPort
from dan.providers.costs import estimate_cost
from dan.worker.model import Worker

if TYPE_CHECKING:
    from dan.server.run_manager import RunRecord
else:
    RunRecord = Any

logger = logging.getLogger(__name__)


def _coerce_event_data(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    if isinstance(data, dict):
        return data
    return {}


def _json_schema_for_runtime_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        return {"type": "array"}
    if isinstance(value, dict):
        return {"type": "object"}
    return {}


def _empty_lint_summary() -> dict[str, Any]:
    return {
        "lint_total_count": 0,
        "lint_passed_count": 0,
        "lint_auto_fixed_count": 0,
        "lint_failed_count": 0,
        "lint_blocked_count": 0,
        "lint_warning_count": 0,
        "had_lint_activity": False,
        "had_lint_blocks": False,
        "had_lint_autofix": False,
        "lint_state": "none",
    }


def _finalize_lint_summary(summary: dict[str, Any]) -> dict[str, Any]:
    summary["lint_total_count"] = (
        summary["lint_passed_count"]
        + summary["lint_auto_fixed_count"]
        + summary["lint_failed_count"]
    )
    summary["had_lint_activity"] = summary["lint_total_count"] > 0
    summary["had_lint_blocks"] = summary["lint_blocked_count"] > 0
    summary["had_lint_autofix"] = summary["lint_auto_fixed_count"] > 0

    if summary["had_lint_blocks"] and summary["had_lint_autofix"]:
        summary["lint_state"] = "blocked+auto_fixed"
    elif summary["had_lint_blocks"]:
        summary["lint_state"] = "blocked"
    elif summary["had_lint_autofix"]:
        summary["lint_state"] = "auto_fixed"
    elif summary["lint_warning_count"] > 0:
        summary["lint_state"] = "warning"
    elif summary["lint_passed_count"] > 0:
        summary["lint_state"] = "passed"
    else:
        summary["lint_state"] = "none"
    return summary


def _summarize_lint_events(
    events: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    overall = _empty_lint_summary()
    per_node: dict[str, dict[str, Any]] = {}

    for event in events:
        event_type = event.get("event_type")
        if event_type not in {"lint_passed", "lint_failed", "lint_auto_fixed"}:
            continue

        node_id = event.get("node_id")
        node_summary = None
        if isinstance(node_id, str) and node_id:
            node_summary = per_node.setdefault(node_id, _empty_lint_summary())

        data = _coerce_event_data(event)
        is_blocking_failure = (
            event_type == "lint_failed"
            and (
                data.get("handoff_committed") is False
                or data.get("severity") == "error"
            )
        )

        targets = [overall]
        if node_summary is not None:
            targets.append(node_summary)

        for target in targets:
            if event_type == "lint_passed":
                target["lint_passed_count"] += 1
            elif event_type == "lint_auto_fixed":
                target["lint_auto_fixed_count"] += 1
            else:
                target["lint_failed_count"] += 1
                if is_blocking_failure:
                    target["lint_blocked_count"] += 1
                else:
                    target["lint_warning_count"] += 1

    _finalize_lint_summary(overall)
    for node_id, summary in list(per_node.items()):
        per_node[node_id] = _finalize_lint_summary(summary)
    return overall, per_node


def _record_status(record: RunRecord) -> str:
    return str(getattr(record.status, "value", record.status))


def _experience_store_has_index(store: Any) -> bool:
    has_index = getattr(store, "has_index", None)
    if callable(has_index):
        try:
            return bool(has_index())
        except Exception:
            logger.debug("Experience-store index probe failed", exc_info=True)
            return False
    return bool(getattr(store, "_index", None))


@dataclass(slots=True)
class RunFinalizer:
    config: Any
    run_store: Any | None
    telemetry_store: Any | None
    memory_kernel: Any | None
    get_run: Callable[[str], RunRecord | None]
    emit_learning_event: Callable[[RunRecord, str, dict[str, Any], str | None], None]
    launch_reflection_run: Callable[[Graph, str, dict[str, Any], str], Awaitable[Any]]
    get_error_memory_index: Callable[[], Any | None]
    get_principle_store: Callable[[], Any | None]
    get_rule_lifecycle_manager: Callable[[], Any | None]
    get_experience_store: Callable[[], Any | None]

    async def finalize(self, record: RunRecord, *, graph: Graph | None = None) -> None:
        if self.telemetry_store is not None:
            await self._emit_workflow_telemetry(record)

        if (
            getattr(self.config, "error_memory_enabled", False)
            and record.result
            and record.result.errors
        ):
            self._index_run_errors(record)

        if record.run_id.startswith("reflection-"):
            self._persist_reflection_principles(record)
        else:
            trigger = getattr(self.config, "reflection_trigger", "disabled")
            status = _record_status(record)
            if trigger == "on_failure" and status == "failed":
                self._schedule_reflection_background(record)
            elif trigger == "on_every_run" and status in {"completed", "failed"}:
                self._schedule_reflection_background(record)

        if getattr(self.config, "self_evolving_rules_enabled", False):
            self._track_rule_effectiveness(record)

        await self._maybe_consolidate_experience(record, graph)
        await self._extract_post_run_learning(record, graph)
        self._track_model_outcomes(record)
        self._track_topology_outcomes(record, graph)

    async def _safe_async(self, coro: Awaitable[Any]) -> None:
        try:
            await coro
        except Exception:
            logger.warning("Background task failed", exc_info=True)

    def _schedule_background(self, coro: Awaitable[Any], *, label: str) -> None:
        try:
            asyncio.get_running_loop().create_task(self._safe_async(coro))
        except RuntimeError:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            logger.debug("No running loop for %s", label, exc_info=True)

    def _index_run_errors(self, record: RunRecord) -> None:
        index = self.get_error_memory_index()
        if index is None:
            return
        try:
            from dan.engine.error_memory import extract_error_records

            errors = extract_error_records(record.snapshot(), list(record.events))
            if not errors:
                return
            self._schedule_background(
                index.index_errors(record.graph_id, errors),
                label="error indexing",
            )
            logger.debug("Indexed %d error records for run %s", len(errors), record.run_id)
            self.emit_learning_event(
                record,
                "error_memory_indexed",
                {
                    "error_count": len(errors),
                    "tier": "error_memory",
                },
                None,
            )
        except Exception:
            logger.debug("Error indexing failed for run %s", record.run_id, exc_info=True)

    def _persist_reflection_principles(self, record: RunRecord) -> None:
        ps = self.get_principle_store()
        if ps is None or not (record.result and record.result.metadata):
            return
        try:
            from dan.engine.error_memory import CausalPrinciple

            all_principles: list[CausalPrinciple] = []
            for node_meta in record.result.metadata.values():
                if not isinstance(node_meta, dict):
                    continue
                raw_principles = node_meta.get("principles")
                if not isinstance(raw_principles, list):
                    continue
                for principle in raw_principles:
                    if not isinstance(principle, dict) or not principle.get("condition"):
                        continue
                    try:
                        all_principles.append(CausalPrinciple.model_validate(principle))
                    except Exception:
                        all_principles.append(
                            CausalPrinciple(
                                condition=str(principle.get("condition", "")),
                                action=str(principle.get("action", "")),
                                reason=str(principle.get("reason", "")),
                                confidence=float(principle.get("confidence", 0.5)),
                                tags=principle.get("tags")
                                if isinstance(principle.get("tags"), list)
                                else [],
                                workflow_id=record.graph_id,
                            )
                        )
            if not all_principles:
                return

            origin_record = None
            if record.run_id.startswith("reflection-"):
                origin_record = self.get_run(record.run_id[len("reflection-"):])

            self._schedule_background(
                ps.store_principles(record.graph_id, all_principles),
                label="principle storage",
            )
            logger.debug(
                "Persisted %d reflection principles for run %s",
                len(all_principles),
                record.run_id,
            )
            self._schedule_background(ps.compact(record.graph_id), label="principle compaction")

            reflection_event = {
                "reflection_run_id": record.run_id,
                "principle_count": len(all_principles),
                "tier": "reflection",
            }
            self.emit_learning_event(record, "reflection_completed", reflection_event, None)
            if origin_record is not None:
                self.emit_learning_event(origin_record, "reflection_completed", reflection_event, None)

            rlm = self.get_rule_lifecycle_manager()
            if rlm is None:
                return
            for principle in all_principles:
                try:
                    repair_level = getattr(principle, "repair_level", "prompt_fix")
                    if repair_level == "parameter_fix":
                        mutation = rlm.create_mutation(principle, record.graph_id)
                        if mutation is None:
                            continue
                        rule_event = {
                            "rule_id": mutation.mutation_id,
                            "hyperedge_type": "parameter_mutation",
                            "principle_id": mutation.source_principle_id,
                            "tier": "rules",
                        }
                    else:
                        rule = rlm.create_rule(principle, record.graph_id)
                        if rule is None:
                            continue
                        rule_event = {
                            "rule_id": rule.rule_id,
                            "hyperedge_type": rule.hyperedge.hyperedge_type,
                            "principle_id": rule.source_principle_id,
                            "tier": "rules",
                        }
                    self.emit_learning_event(record, "rule_generated", rule_event, None)
                    if origin_record is not None:
                        self.emit_learning_event(origin_record, "rule_generated", rule_event, None)
                except Exception:
                    logger.debug(
                        "Rule generation failed for principle %s",
                        getattr(principle, "id", "?"),
                        exc_info=True,
                    )
        except Exception:
            logger.debug("Principle persistence failed for %s", record.run_id, exc_info=True)

    def _schedule_reflection_background(self, record: RunRecord) -> None:
        try:
            reflection_run_id = f"reflection-{record.run_id}"
            if self.get_run(reflection_run_id) is not None:
                return

            self.emit_learning_event(
                record,
                "reflection_started",
                {
                    "source_run_id": record.run_id,
                    "reflection_run_id": reflection_run_id,
                    "tier": "reflection",
                },
                None,
            )

            errors_data = []
            if record.result and record.result.errors:
                for node_id, message in record.result.errors.items():
                    errors_data.append(
                        {
                            "node_id": node_id,
                            "error": message,
                            "node_type": record.node_statuses.get(node_id, ""),
                        }
                    )

            inputs = {
                "run_id": record.run_id,
                "run_errors": errors_data,
                "run_events": record.events[-100:],
                "node_statuses": dict(record.node_statuses),
                "runtime_repair_lineage": (
                    dict((record.result.metadata or {}).get("repair_lineage", {}))
                    if record.result and isinstance(record.result.metadata, dict)
                    else {}
                ),
                "runtime_repair_summaries": (
                    {
                        node_id: dict(node_meta.get("runtime_repair_summary", {}))
                        for node_id, node_meta in (record.result.metadata or {}).items()
                        if isinstance(node_meta, dict) and node_meta.get("runtime_repair_summary")
                    }
                    if record.result and isinstance(record.result.metadata, dict)
                    else {}
                ),
            }

            reflection_node = Worker(
                id="reflection-auto",
                name="Auto Reflection",
                description="Internal post-run reflection helper.",
                role="reflection",
                input_ports=[
                    InputPort(
                        name=key,
                        required=False,
                        json_schema=_json_schema_for_runtime_value(value),
                    )
                    for key, value in inputs.items()
                ],
                output_ports=[
                    OutputPort(name="principles", json_schema={"type": "array"}),
                    OutputPort(name="principle_count", json_schema={"type": "integer"}),
                    OutputPort(name="source", json_schema={"type": "string"}),
                    OutputPort(name="text", json_schema={"type": "string"}),
                ],
                metadata={
                    "reflection_source": "last_run",
                    "scoped_helper": "auto_reflection",
                },
            )
            graph = Graph(
                nodes=[reflection_node],
                entry_points=["reflection-auto"],
                exit_points=["reflection-auto"],
            )

            self._schedule_background(
                self.launch_reflection_run(graph, record.graph_id, inputs, reflection_run_id),
                label="reflection scheduling",
            )
        except Exception:
            logger.debug("Failed to schedule reflection for %s", record.run_id, exc_info=True)

    def _track_rule_effectiveness(self, record: RunRecord) -> None:
        manager = self.get_rule_lifecycle_manager()
        if manager is None:
            return
        try:
            active_rules = manager.list_rules(record.graph_id, status="active")
            if not active_rules:
                return

            current_errors = set(record.result.errors or {}) if record.result else set()
            for rule in active_rules:
                manager.record_application(record.graph_id, rule.rule_id)

                target_nodes = set(rule.hyperedge.attach_to) if rule.hyperedge.attach_to else set()
                if not target_nodes:
                    continue

                error_in_targets = bool(target_nodes & current_errors)
                manager.record_outcome(
                    record.graph_id,
                    rule.rule_id,
                    error_recurred=error_in_targets,
                )

                updated = manager._load_one(record.graph_id, rule.rule_id)
                resolved_rule = updated if updated is not None else rule
                self.emit_learning_event(
                    record,
                    "rule_effectiveness_update",
                    {
                        "rule_id": rule.rule_id,
                        "apply_count": resolved_rule.apply_count,
                        "effectiveness_score": resolved_rule.effectiveness_score,
                        "error_recurred": error_in_targets,
                        "tier": "rules",
                    },
                    None,
                )

            for rule_id in manager.prune_ineffective(record.graph_id):
                self.emit_learning_event(
                    record,
                    "rule_pruned",
                    {
                        "rule_id": rule_id,
                        "reason": "ineffective",
                        "tier": "rules",
                    },
                    None,
                )

            for rule in manager.list_rules(record.graph_id):
                if rule.status == "expired" and rule.expires_at and abs(time.time() - rule.expires_at) < 60:
                    self.emit_learning_event(
                        record,
                        "rule_expired",
                        {
                            "rule_id": rule.rule_id,
                            "reason": "ttl_expired",
                            "tier": "rules",
                        },
                        None,
                    )
        except Exception:
            logger.debug("Rule effectiveness tracking failed for %s", record.run_id, exc_info=True)

    async def _load_principle_dicts(self, workflow_id: str) -> list[dict[str, Any]]:
        ps = self.get_principle_store()
        if ps is None:
            return []
        try:
            principles = await ps.load_principles(workflow_id)
            return [principle.model_dump() for principle in principles]
        except Exception:
            logger.debug("Failed to load principles for %s", workflow_id, exc_info=True)
            return []

    async def _maybe_consolidate_experience(
        self,
        record: RunRecord,
        graph: Graph | None = None,
    ) -> None:
        if record.run_id.startswith("reflection-"):
            return
        store = self.get_experience_store()
        if store is None:
            return

        interval = max(1, int(getattr(self.config, "experience_consolidation_interval", 5)))
        try:
            from dan.engine.experience import consolidate_experience, extract_experience_from_graph

            workflow_id = record.graph_id
            experience = await store.load_experience(workflow_id)
            if experience is None:
                if graph is None:
                    return
                experience = extract_experience_from_graph(graph).model_copy(
                    update={"workflow_id": workflow_id},
                )

            current_success = bool(record.result and record.result.success)
            existing_failure_count = max(experience.run_count - experience.success_count, 0)
            needs_full_refresh = False
            if current_success and experience.success_count == 0:
                needs_full_refresh = True
            if (not current_success) and existing_failure_count == 0:
                needs_full_refresh = True
            if (experience.run_count + 1) % interval == 0:
                needs_full_refresh = True

            if record.run_id in experience.processed_run_ids:
                return

            snapshots: list[dict[str, Any]] = []
            if needs_full_refresh and self.run_store is not None:
                summaries = self.run_store.list_summaries(workflow_id=workflow_id, limit=10000)
                snapshots = [summary for summary in summaries if isinstance(summary, dict)]
            if not snapshots:
                snapshots = [record.snapshot()]

            principle_dicts = await self._load_principle_dicts(workflow_id) if needs_full_refresh else []
            updated = consolidate_experience(experience, snapshots, principle_dicts)
            indexed = False
            write_experience = getattr(store, "write_experience", None)
            index_saved_experience = getattr(store, "index_saved_experience", None)
            if callable(write_experience):
                await write_experience(updated)
                if _experience_store_has_index(store) and callable(index_saved_experience):
                    self._schedule_background(
                        index_saved_experience(updated),
                        label="experience indexing",
                    )
                    indexed = True
            else:
                await store.save_experience(updated)
                indexed = _experience_store_has_index(store)
            self.emit_learning_event(
                record,
                "experience_consolidated",
                {
                    "workflow_id": workflow_id,
                    "run_count": updated.run_count,
                    "success_count": updated.success_count,
                    "tier": "experience",
                },
                None,
            )
            if indexed:
                self.emit_learning_event(
                    record,
                    "experience_indexed",
                    {
                        "workflow_id": workflow_id,
                        "tier": "experience",
                    },
                    None,
                )
        except Exception:
            logger.debug("Experience consolidation failed for run %s", record.run_id, exc_info=True)

    async def _extract_post_run_learning(self, record: RunRecord, graph: Graph | None) -> None:
        if self.memory_kernel is None:
            return
        try:
            from dan.engine.run_learner import RunLearner

            learner = RunLearner(self.memory_kernel)
            await learner.extract_run_learnings_async(
                run_result=record.snapshot(),
                workflow=graph.model_dump() if graph else None,
                goal_context=getattr(record, "goal_context", None),
            )
        except Exception:
            logger.debug("Post-run learning failed", exc_info=True)

    def _track_model_outcomes(self, record: RunRecord) -> None:
        if os.environ.get("DAN_MODEL_LEARNING", "0") != "1" or self.memory_kernel is None:
            return
        try:
            from dan.engine.outcome_trackers import ModelOutcomeTracker

            tracker = ModelOutcomeTracker(self.memory_kernel)
            for node_id, usage in record.node_usage.items():
                node_meta = (record.result.metadata or {}).get(node_id, {}) if record.result else {}
                node_model = node_meta.get("model") or self.config.default_model
                node_type = node_meta.get("node_type", "llm")
                quality = 1.0 if (record.result and record.result.success) else 0.0
                node_cost = (
                    estimate_cost(
                        node_model,
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0),
                    )
                    or 0.0
                )
                tracker.record(
                    node_id=node_id,
                    node_type=node_type,
                    task_description=node_meta.get("task_description", ""),
                    model=node_model,
                    quality_score=quality,
                    cost=node_cost,
                    latency_ms=float(usage.get("latency_ms", 0)),
                )
        except Exception:
            logger.debug("Model outcome tracking failed", exc_info=True)

    def _track_topology_outcomes(self, record: RunRecord, graph: Graph | None) -> None:
        if os.environ.get("DAN_TOPOLOGY_LEARNING", "0") != "1" or self.memory_kernel is None or graph is None:
            return
        try:
            from dan.engine.outcome_trackers import TopologyOutcomeTracker

            tracker = TopologyOutcomeTracker(self.memory_kernel)
            first_failure_node = None
            first_failure_type = None
            if record.result and record.result.errors:
                for node_id, message in record.result.errors.items():
                    first_failure_node = node_id
                    lowered = str(message).lower()
                    if "timeout" in lowered:
                        first_failure_type = "timeout"
                    elif "validat" in lowered or "schema" in lowered:
                        first_failure_type = "validation"
                    else:
                        first_failure_type = "error"
                    break
            tracker.record(
                topology_signature=TopologyOutcomeTracker.compute_signature(graph.model_dump()),
                outcome=bool(record.result and record.result.success),
                failure_node=first_failure_node,
                failure_type=first_failure_type,
            )
        except Exception:
            logger.debug("Topology outcome tracking failed", exc_info=True)

    async def _emit_workflow_telemetry(self, record: RunRecord) -> None:
        try:
            from dan.server.telemetry import TelemetryEvent

            project_id = (record.goal_context or {}).get("project_id")
            parent_event_id = (record.goal_context or {}).get("turn_event_id")
            lint_summary, per_node_lint = _summarize_lint_events(record.events)
            success = _record_status(record) == "completed"

            for node_id, usage in record.node_usage.items():
                node_model = None
                if record.result and record.result.metadata:
                    node_meta = record.result.metadata.get(node_id, {})
                    if isinstance(node_meta, dict):
                        node_model = node_meta.get("model")
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                total_tokens = usage.get("total_tokens", 0)
                node_cost = 0.0
                if node_model:
                    estimated = estimate_cost(node_model, prompt_tokens, completion_tokens)
                    if estimated is not None:
                        node_cost = estimated
                await self.telemetry_store.record(
                    TelemetryEvent(
                        event_type="workflow_node",
                        project_id=project_id,
                        parent_event_id=parent_event_id,
                        run_id=record.run_id,
                        graph_id=record.graph_id,
                        model=node_model or record.model,
                        node_id=node_id,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        estimated_cost=node_cost,
                        duration_ms=0.0,
                        success=success,
                        metadata=dict(per_node_lint.get(node_id, _empty_lint_summary())),
                    )
                )

            await self.telemetry_store.record(
                TelemetryEvent(
                    event_type="workflow_run",
                    project_id=project_id,
                    parent_event_id=parent_event_id,
                    run_id=record.run_id,
                    graph_id=record.graph_id,
                    model=record.model,
                    prompt_tokens=record.total_prompt_tokens,
                    completion_tokens=record.total_completion_tokens,
                    total_tokens=record.total_tokens,
                    estimated_cost=record.total_cost or 0.0,
                    duration_ms=(record.elapsed_seconds or 0) * 1000,
                    success=success,
                    metadata={
                        "node_count": len(record.node_usage),
                        "error": record.error,
                        **lint_summary,
                    },
                )
            )
        except Exception:
            logger.debug("Workflow telemetry failed", exc_info=True)
