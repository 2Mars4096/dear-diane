"""Compatibility plan composers for legacy organism entry points.

These helpers keep the public legacy task models usable while translating their
inputs into the Plan 56 universal-organism control plane.
"""

from __future__ import annotations

from typing import Any, Mapping

from dan.worker.brief import RoleSpec
from dan.worker.contracts.templates import coding_brief, research_brief, review_brief, role_brief
from dan.worker.organisms.universal_organism import (
    OrganismDependency,
    OrganismPlan,
    OrganismPolicy,
    OrganismTask,
)


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _list_value(source: Any, key: str) -> list[Any]:
    value = _value(source, key, [])
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _str_list(source: Any, key: str) -> list[str]:
    return [str(item) for item in _list_value(source, key) if str(item).strip()]


def _dict_value(source: Any, key: str) -> dict[str, Any]:
    value = _value(source, key, {})
    return dict(value) if isinstance(value, Mapping) else {}


def _evidence_refs(source: Any) -> list[dict[str, Any]]:
    refs = []
    for index, ref in enumerate(_list_value(source, "evidence_refs")):
        if isinstance(ref, Mapping):
            refs.append(
                {
                    "ref_id": str(ref.get("ref_id") or ref.get("id") or f"evidence-{index}"),
                    "label": str(ref.get("label") or ref.get("summary") or f"evidence-{index}"),
                    "content": dict(ref),
                }
            )
        else:
            refs.append(
                {
                    "ref_id": str(getattr(ref, "ref_id", "") or f"evidence-{index}"),
                    "label": str(getattr(ref, "label", "") or getattr(ref, "summary", "") or f"evidence-{index}"),
                    "content": getattr(ref, "model_dump", lambda **_: str(ref))(mode="json", exclude_none=True)
                    if hasattr(ref, "model_dump")
                    else str(ref),
                }
            )
    for index, finding in enumerate(_str_list(source, "research_findings")):
        refs.append(
            {
                "ref_id": f"finding-{index}",
                "label": f"research finding {index + 1}",
                "content": finding,
            }
        )
    evidence = _dict_value(source, "evidence")
    for key, value in sorted(evidence.items()):
        refs.append({"ref_id": f"evidence:{key}", "label": str(key), "content": value})
    return refs


def compose_coding_universal_plan(
    task: Any,
    *,
    plan_id: str | None = None,
    run_id: str | None = None,
    model: str | None = None,
    max_concurrency: int = 1,
) -> OrganismPlan:
    """Translate a legacy `CodingTask`-shaped object into a universal plan."""

    task_id = str(_value(task, "task_id", "coding-task"))
    objective = str(_value(task, "objective", "") or "Complete the coding task.")
    implementation_id = f"{task_id}:implement"
    validation_id = f"{task_id}:validate"
    return OrganismPlan(
        plan_id=plan_id or f"{task_id}:coding-plan",
        run_id=run_id,
        objective=objective,
        tasks=[
            OrganismTask(
                task_id=implementation_id,
                role=RoleSpec(
                    role_label="coding-implementer",
                    responsibility="Produce the smallest correct coding candidate.",
                    success_criteria=_str_list(task, "acceptance_criteria"),
                    artifact_targets=_str_list(task, "artifact_owner_paths"),
                    trace_role="coding.implementer",
                ),
                brief=coding_brief(
                    role={
                        "role_label": "coding-implementer",
                        "responsibility": "Produce the smallest correct coding candidate.",
                        "success_criteria": _str_list(task, "acceptance_criteria"),
                        "trace_role": "coding.implementer",
                    },
                    task=objective,
                    hard_constraints=_str_list(task, "hard_constraints"),
                    soft_constraints=_str_list(task, "soft_constraints"),
                    evidence=_evidence_refs(task),
                    context_packet=_dict_value(task, "session_context"),
                    metadata={"legacy_task_id": task_id, "legacy_surface": "coding_execution"},
                ),
                model=model,
                metadata={"legacy_stage": "implementation"},
            ),
            OrganismTask(
                task_id=validation_id,
                brief=review_brief(
                    role={
                        "role_label": "coding-validator",
                        "responsibility": "Validate the coding candidate against acceptance criteria.",
                        "trace_role": "coding.validator",
                    },
                    task=f"Validate the candidate for: {objective}",
                    allowed_tool_ids=["file_read", "git_diff", "git_status", "shell_command"],
                    validation_policy={
                        "acceptance_criteria": _str_list(task, "acceptance_criteria"),
                        "repair_brief": str(_value(task, "repair_brief", "") or ""),
                    },
                    metadata={"legacy_task_id": task_id, "legacy_surface": "coding_execution"},
                ),
                dependencies=[OrganismDependency(upstream_task_id=implementation_id)],
                model=model,
                metadata={"legacy_stage": "validation"},
            ),
        ],
        policy=OrganismPolicy(max_concurrency=max_concurrency),
        repair_policy={"repair_brief": str(_value(task, "repair_brief", "") or "")},
        artifact_policy={"acceptance_criteria": _str_list(task, "acceptance_criteria")},
        acceptance_policy={"legacy_surface": "coding_execution"},
        metadata={"compatibility_facade": "coding_execution_organism"},
    )


def compose_project_execution_universal_plan(
    task: Any,
    *,
    plan_id: str | None = None,
    run_id: str | None = None,
    model: str | None = None,
    max_concurrency: int = 2,
) -> OrganismPlan:
    """Translate a legacy project/research task into a universal plan."""

    task_id = str(_value(task, "task_id", "project-task"))
    objective = str(_value(task, "objective", "") or "Complete the project execution task.")
    research_id = f"{task_id}:research"
    build_id = f"{task_id}:build"
    validate_id = f"{task_id}:validate"
    synthesize_id = f"{task_id}:synthesize"
    return OrganismPlan(
        plan_id=plan_id or f"{task_id}:project-plan",
        run_id=run_id,
        objective=objective,
        tasks=[
            OrganismTask(
                task_id=research_id,
                brief=research_brief(
                    role={
                        "role_label": "project-researcher",
                        "responsibility": "Ground the project task in evidence and constraints.",
                        "trace_role": "project.researcher",
                    },
                    task=objective,
                    allowed_tool_ids=["file_read", "web_search", "list_directory"],
                    evidence=_evidence_refs(task),
                    prompt_slots={
                        "temporal_mode": str(_value(task, "temporal_mode", "")),
                        "temporal_anchor": str(_value(task, "temporal_anchor", "")),
                        "temporal_window": str(_value(task, "temporal_window", "")),
                    },
                    metadata={"legacy_task_id": task_id, "legacy_surface": "project_execution"},
                ),
                model=model,
            ),
            OrganismTask(
                task_id=build_id,
                brief=coding_brief(
                    role={
                        "role_label": "project-builder",
                        "responsibility": "Produce the bounded project change candidate.",
                        "trace_role": "project.builder",
                    },
                    task=objective,
                    hard_constraints=_str_list(task, "hard_constraints"),
                    soft_constraints=_str_list(task, "soft_constraints"),
                    metadata={"legacy_task_id": task_id, "legacy_surface": "project_execution"},
                ),
                dependencies=[OrganismDependency(upstream_task_id=research_id)],
                model=model,
            ),
            OrganismTask(
                task_id=validate_id,
                brief=review_brief(
                    role={
                        "role_label": "project-validator",
                        "responsibility": "Validate the project candidate and focused commands.",
                        "trace_role": "project.validator",
                    },
                    task=f"Validate project output for: {objective}",
                    allowed_tool_ids=["file_read", "shell_command"],
                    validation_policy={
                        "focused_validation_commands": _str_list(task, "focused_validation_commands"),
                        "acceptance_criteria": _str_list(task, "acceptance_criteria"),
                    },
                    metadata={"legacy_task_id": task_id, "legacy_surface": "project_execution"},
                ),
                dependencies=[OrganismDependency(upstream_task_id=build_id)],
                model=model,
            ),
            OrganismTask(
                task_id=synthesize_id,
                brief=role_brief(
                    role={
                        "role_label": "project-synthesizer",
                        "responsibility": "Synthesize research, build, and validation outputs into delivery.",
                        "trace_role": "project.synthesizer",
                    },
                    task=f"Synthesize final delivery for: {objective}",
                    metadata={"legacy_task_id": task_id, "legacy_surface": "project_execution"},
                ),
                dependencies=[
                    OrganismDependency(upstream_task_id=research_id),
                    OrganismDependency(upstream_task_id=validate_id),
                ],
                model=model,
            ),
        ],
        policy=OrganismPolicy(max_concurrency=max_concurrency),
        runtime_budget_policy={
            "temporal_mode": str(_value(task, "temporal_mode", "")),
            "delivery_target": str(_value(task, "delivery_target", "")),
        },
        artifact_policy={"focused_validation_commands": _str_list(task, "focused_validation_commands")},
        acceptance_policy={"legacy_surface": "project_execution"},
        metadata={"compatibility_facade": "project_execution_reference_organism"},
    )


def compose_incident_universal_plan(
    request: Any,
    *,
    plan_id: str | None = None,
    run_id: str | None = None,
    model: str | None = None,
) -> OrganismPlan:
    """Translate a deterministic incident request into response/verification roles."""

    action_id = str(_value(request, "action_id", "investigate"))
    scenario_id = str(_value(request, "scenario_id", "") or "incident")
    objective = str(_value(request, "objective", "") or _value(request, "target", "") or "Handle the incident.")
    response_id = f"{scenario_id}:{action_id}:respond"
    verify_id = f"{scenario_id}:{action_id}:verify"
    communicate_id = f"{scenario_id}:{action_id}:communicate"
    return OrganismPlan(
        plan_id=plan_id or f"{scenario_id}:{action_id}:incident-plan",
        run_id=run_id,
        objective=objective,
        tasks=[
            OrganismTask(
                task_id=response_id,
                brief=role_brief(
                    role={
                        "role_label": "incident-responder",
                        "responsibility": "Execute the admitted incident action boundary.",
                        "trace_role": "incident.responder",
                    },
                    task=objective,
                    input_payload=_value(request, "model_dump", lambda **_: dict(request) if isinstance(request, Mapping) else {})(
                        mode="json",
                        exclude_none=True,
                    )
                    if hasattr(request, "model_dump")
                    else dict(request) if isinstance(request, Mapping) else {},
                    context_packet={"preferred_lane": str(_value(request, "preferred_lane", ""))},
                    metadata={"legacy_surface": "incident_execution", "action_id": action_id},
                ),
                model=model,
            ),
            OrganismTask(
                task_id=verify_id,
                brief=review_brief(
                    role={
                        "role_label": "incident-verifier",
                        "responsibility": "Verify terminal state and follow-up requirements.",
                        "trace_role": "incident.verifier",
                    },
                    task=f"Verify incident action `{action_id}` for: {objective}",
                    allowed_tool_ids=[],
                    validation_policy={"verification_checks": _str_list(request, "verification_checks")},
                    metadata={"legacy_surface": "incident_execution", "action_id": action_id},
                ),
                dependencies=[OrganismDependency(upstream_task_id=response_id)],
                model=model,
            ),
            OrganismTask(
                task_id=communicate_id,
                brief=role_brief(
                    role={
                        "role_label": "incident-communicator",
                        "responsibility": "Prepare concise operator-facing incident closure.",
                        "trace_role": "incident.communicator",
                    },
                    task=f"Summarize incident status for: {objective}",
                    metadata={"legacy_surface": "incident_execution", "action_id": action_id},
                ),
                dependencies=[OrganismDependency(upstream_task_id=verify_id)],
                model=model,
            ),
        ],
        policy=OrganismPolicy(max_concurrency=1),
        acceptance_policy={"legacy_surface": "incident_execution"},
        metadata={"compatibility_facade": "execute_incident_action"},
    )


def compose_super_organism_universal_plan(
    report_or_objective: Any,
    *,
    plan_id: str | None = None,
    run_id: str | None = None,
    model: str | None = None,
    max_concurrency: int = 3,
) -> OrganismPlan:
    """Translate a Super DAN report/objective into universal board roles."""

    objective = (
        str(_value(report_or_objective, "target", "") or report_or_objective)
        if not isinstance(report_or_objective, Mapping)
        else str(report_or_objective.get("target") or report_or_objective.get("objective") or "")
    )
    objective = objective or "Run the Super DAN organism plan."
    board_id = "super-dan:board"
    risk_id = "super-dan:risk"
    synthesize_id = "super-dan:synthesize"
    return OrganismPlan(
        plan_id=plan_id or "super-dan:universal-plan",
        run_id=run_id,
        objective=objective,
        tasks=[
            OrganismTask(
                task_id=board_id,
                brief=role_brief(
                    role={
                        "role_label": "super-dan-board-reader",
                        "responsibility": "Read the shared board and identify executable work.",
                        "trace_role": "super_dan.board",
                    },
                    task=objective,
                    context_packet=_value(report_or_objective, "model_dump", lambda **_: {})(
                        mode="json",
                        exclude_none=True,
                    )
                    if hasattr(report_or_objective, "model_dump")
                    else dict(report_or_objective) if isinstance(report_or_objective, Mapping) else {},
                    metadata={"legacy_surface": "super_organism"},
                ),
                model=model,
            ),
            OrganismTask(
                task_id=risk_id,
                brief=review_brief(
                    role={
                        "role_label": "super-dan-risk-reviewer",
                        "responsibility": "Review risks, blockers, and acceptance gates.",
                        "trace_role": "super_dan.risk",
                    },
                    task=f"Review execution risk for: {objective}",
                    allowed_tool_ids=[],
                    metadata={"legacy_surface": "super_organism"},
                ),
                dependencies=[OrganismDependency(upstream_task_id=board_id)],
                model=model,
            ),
            OrganismTask(
                task_id=synthesize_id,
                brief=role_brief(
                    role={
                        "role_label": "super-dan-synthesizer",
                        "responsibility": "Synthesize final operator-facing Super DAN output.",
                        "trace_role": "super_dan.synthesis",
                    },
                    task=f"Synthesize Super DAN output for: {objective}",
                    metadata={"legacy_surface": "super_organism"},
                ),
                dependencies=[
                    OrganismDependency(upstream_task_id=board_id),
                    OrganismDependency(upstream_task_id=risk_id),
                ],
                model=model,
            ),
        ],
        policy=OrganismPolicy(max_concurrency=max_concurrency),
        acceptance_policy={"legacy_surface": "super_organism"},
        metadata={"compatibility_facade": "run_super_organism_demo"},
    )


compose_reference_demo_universal_plan = compose_project_execution_universal_plan


__all__ = [
    "compose_coding_universal_plan",
    "compose_incident_universal_plan",
    "compose_project_execution_universal_plan",
    "compose_reference_demo_universal_plan",
    "compose_super_organism_universal_plan",
]
