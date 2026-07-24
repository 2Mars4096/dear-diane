"""Canonical Task Blueprint bridge for legacy Super DAN live task graphs.

The live executor still accepts ``super_dan_task_graph_v1`` planner output during
the migration.  This bridge admits that output into the canonical blueprint at
safe checkpoints, projects the accepted topology back into the legacy execution
context, and records runtime progress in a separate execution attempt.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

_FAMILY_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "manufacturing",
        (
            "manufactur",
            "fabricat",
            "dfm",
            "bill of materials",
            " bom ",
            "supplier quote",
            "tolerance",
            "cnc",
            "injection mold",
            "production line",
        ),
    ),
    (
        "meeting",
        (
            "meeting",
            "transcript",
            "minutes",
            "agenda",
            "action items",
            "meeting decisions",
        ),
    ),
    (
        "debugging",
        (
            "debug",
            "bug",
            "failing test",
            "test failure",
            "regression",
            "traceback",
            "exception",
            "reproduce",
            "root cause",
            "fix the current",
        ),
    ),
    (
        "research",
        (
            "research",
            "literature",
            "paper",
            "citation",
            "sources",
            "evidence review",
            "systematic review",
            "experiment",
        ),
    ),
    (
        "design",
        (
            "design",
            "redesign",
            "wireframe",
            "mockup",
            "user experience",
            " ux ",
            "visual direction",
            "brand identity",
            "interface concept",
        ),
    ),
)

_DECISION_RE = re.compile(
    r"\b(decide|decision|choose|select|approve|compare alternatives?)\b", re.I
)
_VALIDATION_RE = re.compile(
    r"\b(validate|verify|test|check|audit|review|inspect|acceptance)\b", re.I
)


class UnsafeBlueprintUpdate(ValueError):
    """Raised when a legacy proposal would rewrite running or completed work."""


def infer_task_family(
    objective: str,
    *,
    request_kind: str = "",
    work_mode: str = "",
) -> str:
    """Infer a topology family without turning family labels into rigid modes."""

    text = f" {str(objective or '').strip().lower()} "
    kind = str(request_kind or "").strip().lower()
    if any(action in text for action in (" fix ", " repair ", " diagnose ")) and any(
        problem in text
        for problem in (" test", " error", " failure", " bug", " exception")
    ):
        return "debugging"
    for family, signals in _FAMILY_SIGNALS:
        if any(signal in text for signal in signals):
            return family
    if kind in {"debugging", "research", "design", "meeting", "manufacturing"}:
        return kind
    if kind == "research":
        return "research"
    if str(work_mode or "").strip().lower() in {"answer_only", "chat_answer"}:
        return "direct"
    direct_prefixes = (
        "tell me ",
        "what is ",
        "explain ",
        "summarize ",
        "rename ",
        "open ",
        "list ",
    )
    if len(text.split()) <= 18 and any(
        text.strip().startswith(prefix) for prefix in direct_prefixes
    ):
        return "direct"
    return "general"


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [text for item in value if (text := _single_line(item))]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _model_payload(value: Any) -> dict[str, Any]:
    dumper = getattr(value, "model_dump", None)
    if callable(dumper):
        return dict(dumper(mode="json"))
    return _mapping(value)


def _semantic_task(task: Mapping[str, Any]) -> dict[str, Any]:
    task_id = _single_line(task.get("task_id") or task.get("id"))
    return {
        "task_id": task_id,
        "goal": _single_line(task.get("goal") or task.get("summary") or task_id),
        "depends_on": _string_list(
            task.get("depends_on") or task.get("dependencies") or []
        ),
        "owned_paths": _string_list(task.get("owned_paths") or task.get("paths") or []),
        "deliverables": _string_list(task.get("deliverables") or []),
        "validation": _string_list(task.get("validation") or task.get("checks") or []),
        "parallel_safe": bool(task.get("parallel_safe", True)),
        "parent_id": _single_line(task.get("parent_id") or task.get("parent")),
        "branch_id": _single_line(task.get("branch_id") or task.get("branch")),
        "plan_id": _single_line(task.get("plan_id") or task.get("parent_plan_id")),
        "node_type": _single_line(task.get("node_type") or task.get("node_kind")),
        "risk": _single_line(task.get("risk")),
    }


def _semantic_tasks(task_graph_state: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = task_graph_state.get("tasks") or []
    if not isinstance(raw, list):
        return []
    tasks = [_semantic_task(item) for item in raw if isinstance(item, Mapping)]
    return [task for task in tasks if task["task_id"]]


def _semantic_fingerprint(tasks: Sequence[Mapping[str, Any]]) -> str:
    rendered = json.dumps(
        list(tasks), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _request_understanding_from_event(event: Mapping[str, Any]) -> dict[str, Any]:
    direct = event.get("request_understanding")
    if isinstance(direct, Mapping):
        return dict(direct)
    plan_context = event.get("plan_context")
    if isinstance(plan_context, Mapping) and isinstance(
        plan_context.get("request_understanding"), Mapping
    ):
        return dict(plan_context["request_understanding"])
    if str(event.get("event") or "") == "live.request_understanding.briefed":
        return {
            "request_kind": event.get("request_kind"),
            "original_request": event.get("original_request"),
            "workspace_root": event.get("workspace_root"),
            "target_paths": list(event.get("target_paths") or []),
            "aspect_reviews": list(event.get("aspect_reviews") or []),
            "confidence_scoped_acceptance": list(
                event.get("confidence_scoped_acceptance") or []
            ),
            "stop_rule": event.get("stop_rule"),
        }
    return {}


def _phase_for_event(event_name: str, source: str = "") -> str | None:
    if event_name.startswith("live.request_understanding"):
        return "planning"
    if event_name.startswith("live.planning"):
        return "planning"
    if event_name.startswith("live.plan_validation"):
        return "planning"
    if event_name.startswith("live.validation"):
        return "validating"
    if "repair" in event_name or "builder_retry" in event_name:
        return "repairing"
    if event_name.startswith("live.answer_recovery"):
        return "validating"
    if event_name.startswith("live.generic") or event_name.startswith("live.website"):
        return "executing"
    if event_name.startswith("live.worktree"):
        return "executing"
    if event_name == "live.task_graph.updated":
        return {
            "request_understanding": "planning",
            "request_understanding_repair": "planning",
            "planner": "planning",
            "plan_validator": "planning",
            "execution_frontier": "executing",
            "execution_result": "executing",
            "validator": "validating",
        }.get(source)
    return None


@dataclass
class SuperBlueprintEventBridge:
    """Stateful admission and projection bridge for one Super DAN live run."""

    task_id: str
    objective: str
    workspace_root: str
    trace_id: str = ""
    operator_policy: Mapping[str, Any] = field(default_factory=dict)
    execution_budget: Mapping[str, Any] = field(default_factory=dict)
    requested_model: str = ""
    _request_understanding: dict[str, Any] = field(default_factory=dict, init=False)
    _blueprint: Any = field(default=None, init=False)
    _attempt: Any = field(default=None, init=False)
    _legacy_specs: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    _semantic_hash: str = field(default="", init=False)
    _node_states: dict[str, str] = field(default_factory=dict, init=False)
    _phase: str = field(default="planning", init=False)
    _model: str = field(default="", init=False)
    _workers: set[str] = field(default_factory=set, init=False)
    _tools: set[str] = field(default_factory=set, init=False)
    _models: set[str] = field(default_factory=set, init=False)
    _retry_count: int = field(default=0, init=False)
    _attempts: list[Any] = field(default_factory=list, init=False)
    _schedule: str = field(default="dependency_frontier", init=False)
    _pending_blueprint_snapshots: list[Any] = field(default_factory=list, init=False)

    def observe_event(self, event: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Admit an event and return additive canonical events.

        Semantic update failures intentionally propagate.  The caller must not
        continue execution from a graph that canonical policy rejected.
        """

        payload = dict(event)
        self._pending_blueprint_snapshots.clear()
        event_name = _single_line(payload.get("event"))
        source = _single_line(payload.get("source")) or event_name
        understanding = _request_understanding_from_event(payload)
        if understanding:
            self._request_understanding.update(understanding)
        model = _single_line(payload.get("model"))
        if model:
            self._model = model
            self._models.add(model)
        worker_id = _single_line(payload.get("worker_id"))
        if worker_id:
            self._workers.add(worker_id)
        tool_id = _single_line(payload.get("tool_id"))
        if tool_id:
            self._tools.add(tool_id)
        if event_name in {
            "live.builder_retry.started",
            "live.generic_repair.started",
            "live.website_repair.started",
            "live.answer_recovery.started",
        }:
            self._retry_count += 1

        emitted: list[dict[str, Any]] = []
        blueprint_changed = False
        if event_name == "live.task_graph.updated":
            graph_state = _mapping(payload.get("task_graph_state"))
            self._record_graph_runtime_state(graph_state)
            blueprint_changed = self._admit_task_graph(graph_state, source=source)
        elif (
            event_name == "live.request_understanding.updated"
            and self._blueprint is None
        ):
            blueprint_changed = self._ensure_blueprint(source=source)
        elif event_name == "live.request_understanding.updated":
            blueprint_changed = self._strengthen_contract(source=source)

        if blueprint_changed:
            emitted.extend(
                self._blueprint_event(source=source, blueprint=blueprint)
                for blueprint in self._pending_blueprint_snapshots
            )

        phase = _phase_for_event(event_name, source)
        if phase:
            self._phase = phase
        attempt_changed = self._update_attempt(event_name=event_name, payload=payload)
        if attempt_changed:
            emitted.append(self._attempt_event(source=source))
        return emitted

    def compile_plan_context(
        self, plan_context: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        """Project the accepted blueprint into the current legacy execution seam."""

        if not isinstance(plan_context, Mapping):
            return None
        if self._blueprint is None:
            return dict(plan_context)
        canonical_ids = {
            str(node.node_id)
            for node in self._blueprint.nodes
            if bool(node.active) and str(node.node_id) in self._legacy_specs
        }
        if not canonical_ids:
            return dict(plan_context)
        dependency_map: dict[str, list[str]] = {
            node_id: [] for node_id in canonical_ids
        }
        for edge in self._blueprint.edges:
            if (
                str(getattr(edge.kind, "value", edge.kind)) == "dependency"
                and edge.source_node_id in canonical_ids
                and edge.target_node_id in canonical_ids
            ):
                dependency_map[edge.target_node_id].append(edge.source_node_id)
        task_graph: list[dict[str, Any]] = []
        for node in self._blueprint.nodes:
            node_id = str(node.node_id)
            if node_id not in canonical_ids:
                continue
            spec = dict(self._legacy_specs.get(node_id) or {})
            spec.update(
                {
                    "task_id": node_id,
                    "goal": str(node.title),
                    "depends_on": sorted(set(dependency_map.get(node_id) or [])),
                }
            )
            task_graph.append(spec)

        compiled = dict(plan_context)
        compiled["task_graph"] = task_graph
        compiled["task_blueprint_ref"] = {
            "schema": "dan_task_blueprint_ref_v1",
            "blueprint_id": self._blueprint.blueprint_id,
            "revision_id": self._blueprint.revision_id,
            "revision": self._blueprint.revision,
            "family": self._blueprint.family,
        }
        for key in (
            "assigned_task_ids",
            "ready_task_ids",
            "deferred_task_ids",
            "parallel_worktree_task_ids",
        ):
            if isinstance(compiled.get(key), list):
                compiled[key] = [
                    item for item in compiled[key] if str(item) in canonical_ids
                ]
        graph_state = _mapping(compiled.get("task_graph_state"))
        if graph_state:
            state_by_id = {
                _single_line(item.get("task_id")): dict(item)
                for item in graph_state.get("tasks") or []
                if isinstance(item, Mapping) and _single_line(item.get("task_id"))
            }
            graph_state["tasks"] = [
                {
                    **task,
                    "state": _single_line(
                        state_by_id.get(task["task_id"], {}).get("state")
                    )
                    or "planned",
                }
                for task in task_graph
            ]
            for key in (
                "changed_task_ids",
                "ready_task_ids",
                "deferred_task_ids",
                "active_task_ids",
                "completed_task_ids",
            ):
                if isinstance(graph_state.get(key), list):
                    graph_state[key] = [
                        item for item in graph_state[key] if str(item) in canonical_ids
                    ]
            graph_state["task_blueprint_ref"] = dict(compiled["task_blueprint_ref"])
            compiled["task_graph_state"] = graph_state
        return compiled

    def snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        if self._blueprint is not None:
            snapshot["task_blueprint"] = _model_payload(self._blueprint)
        if self._attempt is not None:
            snapshot["execution_attempt"] = _model_payload(self._attempt)
        if self._attempts:
            snapshot["execution_attempts"] = [
                _model_payload(item) for item in self._attempts
            ]
        return snapshot

    def _ensure_blueprint(self, *, source: str) -> bool:
        if self._blueprint is not None:
            return False
        self._blueprint = _create_initial_blueprint(
            task_id=self.task_id,
            objective=self.objective,
            workspace_root=self.workspace_root,
            operator_policy=self.operator_policy,
            execution_budget=self.execution_budget,
            request_understanding=self._request_understanding,
            tasks=list(self._legacy_specs.values()),
            node_states=self._node_states,
            update_reason=f"Accepted Super DAN topology from {source}.",
        )
        self._pending_blueprint_snapshots.append(self._blueprint)
        return True

    def _admit_task_graph(self, graph_state: Mapping[str, Any], *, source: str) -> bool:
        tasks = _semantic_tasks(graph_state)
        if not tasks:
            return self._ensure_blueprint(source=source)
        proposed = {task["task_id"]: task for task in tasks}
        proposed_hash = _semantic_fingerprint(tasks)
        contract_changed = self._strengthen_contract(source=source)
        if self._blueprint is not None and proposed_hash == self._semantic_hash:
            return contract_changed
        self._guard_pinned_nodes(proposed)
        self._legacy_specs = proposed
        if self._blueprint is None:
            self._ensure_blueprint(source=source)
        else:
            self._blueprint = _sync_legacy_graph(
                self._blueprint,
                tasks=tasks,
                request_understanding=self._request_understanding,
                author="super_dan",
                reason=f"Accepted Super DAN semantic topology update from {source}.",
                node_states=self._node_states,
            )
            self._pending_blueprint_snapshots.append(self._blueprint)
        self._semantic_hash = proposed_hash
        return True

    def _strengthen_contract(self, *, source: str) -> bool:
        if self._blueprint is None:
            return False
        updated = _strengthen_blueprint_contract(
            self._blueprint,
            request_understanding=self._request_understanding,
            author="super_dan",
            reason=f"Strengthened acceptance criteria from {source}.",
        )
        if updated is self._blueprint:
            return False
        self._blueprint = updated
        self._pending_blueprint_snapshots.append(updated)
        return True

    def _guard_pinned_nodes(self, proposed: Mapping[str, Mapping[str, Any]]) -> None:
        for node_id, state in self._node_states.items():
            if state not in {"active", "running", "done", "completed"}:
                continue
            previous = self._legacy_specs.get(node_id)
            current = proposed.get(node_id)
            if current is None:
                raise UnsafeBlueprintUpdate(
                    f"Blueprint update cannot remove {state} task {node_id!r}."
                )
            if previous is not None and dict(previous) != dict(current):
                raise UnsafeBlueprintUpdate(
                    f"Blueprint update cannot rewrite {state} task {node_id!r}."
                )

    def _record_graph_runtime_state(self, graph_state: Mapping[str, Any]) -> None:
        for item in graph_state.get("tasks") or []:
            if not isinstance(item, Mapping):
                continue
            task_id = _single_line(item.get("task_id"))
            state = _single_line(item.get("state") or item.get("status")).lower()
            if task_id and state:
                self._node_states[task_id] = state
        parallel_groups = [
            _string_list(group)
            for group in graph_state.get("parallel_groups") or []
            if isinstance(group, Sequence)
            and not isinstance(group, (str, bytes, bytearray))
        ]
        parallel_groups = [group for group in parallel_groups if len(group) > 1]
        if parallel_groups:
            self._schedule = "dependency_frontier; parallel_groups=" + json.dumps(
                parallel_groups, separators=(",", ":")
            )

    def _update_attempt(self, *, event_name: str, payload: Mapping[str, Any]) -> bool:
        if self._blueprint is None:
            return False
        status = "running"
        if event_name == "run.log.completed":
            status = (
                "completed"
                if str(payload.get("status") or "") == "completed"
                else "failed"
            )
        elif event_name == "run.log.failed":
            status = "failed"
        previous_payload = (
            _model_payload(self._attempt) if self._attempt is not None else None
        )
        self._attempt = _make_execution_attempt(
            previous=self._attempt,
            blueprint=self._blueprint,
            task_id=self.task_id,
            run_id=self.task_id,
            trace_id=self.trace_id,
            status=status,
            phase=self._phase,
            model=self._model or self.requested_model,
            policy=self.operator_policy,
            budget=self.execution_budget,
            node_states=self._node_states,
            workers=sorted(self._workers),
            tools=sorted(self._tools),
            models=sorted(
                self._models
                or ({self.requested_model} if self.requested_model else set())
            ),
            retry_count=self._retry_count,
            schedule=self._schedule,
        )
        previous_attempt_id = (
            _single_line(_model_payload(self._attempts[-1]).get("attempt_id"))
            if self._attempts
            else ""
        )
        current_attempt_id = _single_line(
            _model_payload(self._attempt).get("attempt_id")
        )
        if not self._attempts or previous_attempt_id != current_attempt_id:
            self._attempts.append(self._attempt)
        else:
            self._attempts[-1] = self._attempt
        return previous_payload != _model_payload(self._attempt)

    def _blueprint_event(self, *, source: str, blueprint: Any) -> dict[str, Any]:
        return {
            "event": "live.task_blueprint.updated",
            "source": source,
            "event_schema": "dan_task_blueprint_event_v1",
            "task_blueprint": _model_payload(blueprint),
            "blueprint_id": blueprint.blueprint_id,
            "blueprint_revision_id": blueprint.revision_id,
            "blueprint_revision": blueprint.revision,
            "task_family": blueprint.family,
        }

    def _attempt_event(self, *, source: str) -> dict[str, Any]:
        return {
            "event": "live.execution_attempt.updated",
            "source": source,
            "event_schema": "dan_execution_attempt_event_v1",
            "blueprint_id": self._blueprint.blueprint_id,
            "blueprint_revision_id": self._blueprint.revision_id,
            "execution_attempt": _model_payload(self._attempt),
            "node_states": dict(self._node_states),
        }


def _stable_id(prefix: str, *parts: Any) -> str:
    source = "\x1f".join(str(part or "") for part in parts)
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def _permission_scope(policy: Mapping[str, Any]) -> tuple[str, ...]:
    permissions: list[str] = []
    forbid_other_inputs = bool(policy.get("forbid_other_workspace_inputs"))
    read_paths = _string_list(policy.get("allowed_read_paths") or [])
    write_paths = _string_list(policy.get("allowed_write_paths") or [])
    if read_paths:
        permissions.extend(f"workspace:read:{path}" for path in read_paths)
    elif not forbid_other_inputs:
        permissions.append("workspace:read")
    if bool(policy.get("allow_workspace_mutation")):
        if write_paths:
            permissions.extend(f"workspace:write:{path}" for path in write_paths)
        elif not forbid_other_inputs:
            permissions.append("workspace:write")
    if bool(policy.get("allow_shell_command")):
        permissions.append("shell:execute")
    if "internet" in _single_line(policy.get("evidence_policy")).lower():
        permissions.append("network:read")
    return tuple(dict.fromkeys(permissions))


def _criteria_from_understanding(
    request_understanding: Mapping[str, Any],
    *,
    family: str = "general",
) -> tuple[Any, ...]:
    from dan.task_blueprints import AcceptanceCriterion

    criteria: list[AcceptanceCriterion] = []
    seen_descriptions: set[str] = set()
    for index, item in enumerate(
        request_understanding.get("confidence_scoped_acceptance") or (), start=1
    ):
        entry = _mapping(item)
        description = _single_line(entry.get("criterion") if entry else item)
        if not description or description.lower() in seen_descriptions:
            continue
        seen_descriptions.add(description.lower())
        action = _single_line(entry.get("action")).lower()
        criteria.append(
            AcceptanceCriterion(
                criterion_id=f"criterion-{index}-{_stable_id('c', description).split(':', 1)[1]}",
                description=description,
                evidence_required=("validation_result",),
                approval_required="approve" in action,
            )
        )
    stop_rule = _single_line(request_understanding.get("stop_rule"))
    if stop_rule and stop_rule.lower() not in seen_descriptions:
        criteria.append(
            AcceptanceCriterion(
                criterion_id=f"criterion-stop-{_stable_id('c', stop_rule).split(':', 1)[1]}",
                description=stop_rule,
                evidence_required=("completion_evidence",),
            )
        )
    if family == "manufacturing":
        criteria.append(
            AcceptanceCriterion(
                criterion_id="criterion-manufacturing-human-release",
                description=(
                    "Human approval is recorded before prototype release, purchasing, or any production commitment."
                ),
                evidence_required=("approval_record",),
                approval_required=True,
            )
        )
    return tuple(criteria)


def _contract_from_understanding(
    *,
    objective: str,
    operator_policy: Mapping[str, Any],
    execution_budget: Mapping[str, Any],
    request_understanding: Mapping[str, Any],
    family: str,
) -> Any:
    from dan.task_blueprints import BudgetLimits, RiskPolicy, TaskContract

    criteria = _criteria_from_understanding(
        request_understanding,
        family=family,
    )

    non_goals: list[str] = []
    if not bool(operator_policy.get("allow_workspace_mutation", True)):
        non_goals.append("Do not mutate workspace artifacts.")
    if bool(operator_policy.get("forbid_other_workspace_inputs")):
        non_goals.append(
            "Do not use workspace inputs outside the operator-approved source scope."
        )
    if not bool(operator_policy.get("allow_shell_command", True)):
        non_goals.append("Do not run shell commands.")
    if family == "manufacturing":
        non_goals.append(
            "Do not authorize production, purchasing, or regulatory release autonomously."
        )

    budget_fields = {
        key: value
        for key, value in execution_budget.items()
        if key
        in {
            "max_wall_seconds",
            "max_work_seconds",
            "max_cost_usd",
            "max_tokens",
            "max_tool_calls",
            "max_parallel_workers",
            "max_revisions",
            "max_auto_fix_rounds",
            "max_validation_cycles",
        }
        and value is not None
    }
    risk = RiskPolicy(
        level="moderate" if family == "manufacturing" else "low",
        hazards=(
            ("Physical, supplier, compliance, and irreversible commitment risk.",)
            if family == "manufacturing"
            else ()
        ),
        mitigations=(
            ("Require explicit approval gates and keep early work prototype-only.",)
            if family == "manufacturing"
            else ()
        ),
        prohibited_actions=(
            ("Autonomous production release or purchase commitment.",)
            if family == "manufacturing"
            else ()
        ),
    )
    return TaskContract(
        goal=_single_line(objective),
        non_goals=tuple(non_goals),
        constraints=tuple(_string_list(operator_policy.get("constraints") or [])),
        permissions=_permission_scope(operator_policy),
        risk=risk,
        budget=BudgetLimits(**budget_fields),
        acceptance_criteria=criteria,
    )


def _strengthen_blueprint_contract(
    current: Any,
    *,
    request_understanding: Mapping[str, Any],
    author: str,
    reason: str,
) -> Any:
    from dan.task_blueprints import apply_blueprint_patch, make_blueprint_patch

    existing_descriptions = {
        item.description.strip().lower()
        for item in current.contract.acceptance_criteria
    }
    new_criteria = [
        item
        for item in _criteria_from_understanding(request_understanding)
        if item.description.strip().lower() not in existing_descriptions
    ]
    if not new_criteria:
        return current
    gate = next(
        (
            node
            for node in current.nodes
            if node.active and node.kind == "gate" and node.criterion_ids
        ),
        None,
    ) or next(
        (node for node in current.nodes if node.active and node.kind == "gate"),
        None,
    )
    operations: list[dict[str, Any]] = []
    for criterion in new_criteria:
        operations.append(
            {
                "op": "strengthen_criteria",
                "data": {"criterion": criterion.model_dump(mode="python")},
            }
        )
        if gate is not None:
            operations.append(
                {
                    "op": "attach_criterion",
                    "data": {
                        "node_id": gate.node_id,
                        "criterion_id": criterion.criterion_id,
                    },
                }
            )
    patch = make_blueprint_patch(
        current,
        operations,
        author=author,
        reason=reason,
    )
    return apply_blueprint_patch(current, patch).current


def _topology_role(task: Mapping[str, Any]) -> str:
    raw = _single_line(task.get("node_type")).lower()
    goal = _single_line(task.get("goal")).lower()
    role_signals: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("dfm_gate", ("dfm", "manufacturability")),
        ("compliance_gate", ("compliance", "regulatory", "regulation")),
        ("approval", ("approve", "approval", "release gate")),
        ("reconciliation", ("reconcile", "resolve conflict", "conflicting decision")),
        ("hypothesis", ("hypothesis", "possible cause")),
        ("reproduction", ("reproduce", "replicate the failure")),
        ("diagnosis", ("diagnose", "root cause")),
        ("regression_gate", ("regression", "focused test", "verify the fix")),
        ("claim_source_matrix", ("claim/source", "claim-source", "claims to evidence")),
        ("synthesis", ("synthesi", "integrate findings")),
        ("evidence", ("evidence", "source collection", "collect sources")),
        ("variant", ("variant", "alternative concept", "contrasting concept")),
        ("critique", ("critique", "design review")),
        ("refinement", ("refine", "refinement", "iterate the selected")),
        ("design_artifact", ("mockup", "wireframe", "design package")),
        ("specification", ("specification", "engineering spec", "product spec")),
        ("quote_comparison", ("quote", "supplier comparison", "vendor comparison")),
        ("action_ledger", ("action item", "owners and actions", "decision log")),
        ("decision", ("decision", "choose", "select direction")),
        ("validation_gate", ("validate", "verification", "acceptance check")),
    )
    for role, signals in role_signals:
        if any(signal in goal for signal in signals):
            return role
    if raw in {"plan", "phase", "composite"}:
        return "composite"
    if raw in {"decision", "artifact", "gate", "loop"}:
        return raw
    return "legacy_task"


def _node_kind(task: Mapping[str, Any]) -> str:
    raw = _single_line(task.get("node_type")).lower()
    if raw in {"work", "decision", "artifact", "gate", "composite"}:
        return raw
    if raw in {"plan", "phase"}:
        return "composite"
    role = _topology_role(task)
    if role in {
        "dfm_gate",
        "compliance_gate",
        "approval",
        "regression_gate",
        "critique",
        "validation_gate",
    }:
        return "gate"
    if role in {"decision", "reconciliation"}:
        return "decision"
    if role in {
        "claim_source_matrix",
        "synthesis",
        "design_artifact",
        "specification",
        "quote_comparison",
        "action_ledger",
    }:
        return "artifact"
    goal = _single_line(task.get("goal"))
    if _DECISION_RE.search(goal):
        return "decision"
    return "work"


def _legacy_nodes_and_edges(
    tasks: Sequence[Mapping[str, Any]],
    *,
    criterion_ids: Sequence[str],
) -> tuple[list[Any], list[Any]]:
    from dan.task_blueprints import BlueprintEdge, BlueprintNode

    nodes: list[BlueprintNode] = []
    edges: list[BlueprintEdge] = []
    task_ids = {str(task.get("task_id") or "") for task in tasks}
    outgoing_dependencies: set[str] = set()
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        capabilities = [
            "parallel_safe" if bool(task.get("parallel_safe", True)) else "serial_only",
            *[f"owns:{path}" for path in _string_list(task.get("owned_paths") or [])],
        ]
        nodes.append(
            BlueprintNode(
                node_id=task_id,
                kind=_node_kind(task),
                title=_single_line(task.get("goal")) or task_id,
                description=_single_line(task.get("risk")),
                topology_role=_topology_role(task),
                capability_requirements=tuple(capabilities),
            )
        )
        for dependency in _string_list(task.get("depends_on") or []):
            outgoing_dependencies.add(dependency)
            edges.append(
                BlueprintEdge(
                    edge_id=_stable_id("dependency", dependency, task_id),
                    source_node_id=dependency,
                    target_node_id=task_id,
                    kind="dependency",
                )
            )
        for index, deliverable in enumerate(
            _string_list(task.get("deliverables") or []), start=1
        ):
            artifact_id = _stable_id("artifact", task_id, index, deliverable)
            nodes.append(
                BlueprintNode(
                    node_id=artifact_id,
                    kind="artifact",
                    title=deliverable,
                    topology_role="deliverable",
                    artifact_refs=(deliverable,),
                )
            )
            edges.append(
                BlueprintEdge(
                    edge_id=_stable_id("produces", task_id, artifact_id),
                    source_node_id=task_id,
                    target_node_id=artifact_id,
                    kind="produces",
                )
            )
        for index, validation in enumerate(
            _string_list(task.get("validation") or []), start=1
        ):
            gate_id = _stable_id("gate", task_id, index, validation)
            nodes.append(
                BlueprintNode(
                    node_id=gate_id,
                    kind="gate",
                    title=validation,
                    topology_role="validation_gate",
                )
            )
            edges.append(
                BlueprintEdge(
                    edge_id=_stable_id("validates", task_id, gate_id),
                    source_node_id=task_id,
                    target_node_id=gate_id,
                    kind="validates",
                )
            )

    if criterion_ids:
        acceptance_id = "gate:acceptance"
        nodes.append(
            BlueprintNode(
                node_id=acceptance_id,
                kind="gate",
                title="Validate the task acceptance contract",
                topology_role="acceptance_gate",
                criterion_ids=tuple(criterion_ids),
            )
        )
        terminal_ids = sorted(task_ids - outgoing_dependencies) or sorted(task_ids)
        for task_id in terminal_ids:
            edges.append(
                BlueprintEdge(
                    edge_id=_stable_id("acceptance", task_id, acceptance_id),
                    source_node_id=task_id,
                    target_node_id=acceptance_id,
                    kind="validates",
                )
            )
    return nodes, edges


def _blueprint_node_states(
    tasks: Sequence[Mapping[str, Any]],
    node_states: Mapping[str, Any],
) -> dict[str, str]:
    task_ids = {str(task.get("task_id") or "") for task in tasks}
    return {
        node_id: str(state)
        for node_id, state in node_states.items()
        if str(node_id) in task_ids
    }


def _create_initial_blueprint(**kwargs: Any) -> Any:
    from dan.task_blueprints import create_blueprint

    policy = _mapping(kwargs.get("operator_policy"))
    understanding = _mapping(kwargs.get("request_understanding"))
    family = infer_task_family(
        str(kwargs.get("objective") or ""),
        request_kind=_single_line(understanding.get("request_kind")),
        work_mode=_single_line(policy.get("work_mode")),
    )
    contract = _contract_from_understanding(
        objective=str(kwargs.get("objective") or ""),
        operator_policy=policy,
        execution_budget=_mapping(kwargs.get("execution_budget")),
        request_understanding=understanding,
        family=family,
    )
    tasks = list(kwargs.get("tasks") or [])
    node_states = _mapping(kwargs.get("node_states"))
    if tasks:
        nodes, edges = _legacy_nodes_and_edges(
            tasks,
            criterion_ids=[item.criterion_id for item in contract.acceptance_criteria],
        )
        return create_blueprint(
            task_id=str(kwargs.get("task_id") or ""),
            contract=contract,
            family=family,
            nodes=nodes,
            edges=edges,
            update_reason=str(
                kwargs.get("update_reason") or "Initial Super DAN task blueprint."
            ),
            author="super_dan",
            node_states=_blueprint_node_states(tasks, node_states),
        )
    return create_blueprint(
        task_id=str(kwargs.get("task_id") or ""),
        contract=contract,
        family=family,
        update_reason=str(
            kwargs.get("update_reason") or "Initial Super DAN task blueprint."
        ),
        author="super_dan",
    )


def _sync_legacy_graph(current: Any, **kwargs: Any) -> Any:
    from dan.task_blueprints import sync_blueprint_graph

    tasks = list(kwargs.get("tasks") or [])
    nodes, edges = _legacy_nodes_and_edges(
        tasks,
        criterion_ids=[
            item.criterion_id for item in current.contract.acceptance_criteria
        ],
    )
    update = sync_blueprint_graph(
        current,
        nodes,
        edges,
        author=str(kwargs.get("author") or "super_dan"),
        reason=str(
            kwargs.get("reason") or "Accepted Super DAN semantic topology update."
        ),
        node_states=_blueprint_node_states(tasks, _mapping(kwargs.get("node_states"))),
    )
    return update.current


def _make_execution_attempt(**kwargs: Any) -> Any:
    from dan.task_blueprints import (
        execution_attempt_for_blueprint,
        revise_execution_attempt,
    )

    blueprint = kwargs["blueprint"]
    previous = kwargs.get("previous")
    attempt_id = f"{kwargs.get('task_id')}:attempt:r{blueprint.revision}"
    if previous is None or previous.blueprint_revision_id != blueprint.revision_id:
        previous = execution_attempt_for_blueprint(
            blueprint,
            attempt_id=attempt_id,
            run_id=str(kwargs.get("run_id") or ""),
            backend="super_dan",
            worker_summary=tuple(kwargs.get("workers") or ()),
            model_summary=tuple(kwargs.get("models") or ()),
            tool_summary=tuple(kwargs.get("tools") or ()),
            max_retries=_mapping(kwargs.get("budget")).get("max_auto_fix_rounds"),
            schedule=str(kwargs.get("schedule") or "dependency_frontier"),
        )
    status = str(kwargs.get("status") or "running")
    phase = str(kwargs.get("phase") or "planning")
    return revise_execution_attempt(
        previous,
        status=status,
        phase=phase,
        worker_summary=tuple(kwargs.get("workers") or ()),
        model_summary=tuple(kwargs.get("models") or ()),
        tool_summary=tuple(kwargs.get("tools") or ()),
        retry_count=int(kwargs.get("retry_count") or 0),
        node_states={
            str(node_id): str(state)
            for node_id, state in _mapping(kwargs.get("node_states")).items()
        },
    )
