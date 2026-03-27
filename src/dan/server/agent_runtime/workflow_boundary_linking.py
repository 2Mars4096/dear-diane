"""Boundary linking and candidate graph materialization for structured generation."""

from __future__ import annotations

import copy
import os
from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.meta.workflow_contract import validate_workflow_build_contract
from dan.meta.workflow_spec import ArtifactSpec, PortBindingSourceKind, WorkflowSpec
from dan.server.agent_runtime.node_worker import NodePlan
from dan.server.agent_runtime.section_assembly import SectionAssemblyArtifact, SectionEdgePlan

__all__ = [
    "BoundaryDiagnostic",
    "BoundaryDiagnosticKind",
    "BoundaryLinkFailure",
    "BoundaryLink",
    "BoundaryLinkResult",
    "link_assembled_sections",
    "link_sections_into_candidate_graph",
    "structured_boundary_repair_max",
]


class BoundaryDiagnosticKind(str):
    missing_source_output = "missing_source_output"
    missing_target_input = "missing_target_input"
    renamed_contract_field = "renamed_contract_field"


class BoundaryLink(BaseModel):
    """Boundary join between two assembled sections."""

    link_id: str
    source_section_id: str
    target_section_id: str
    source_node_id: str
    source_port: str
    target_node_id: str
    target_port: str


class BoundaryDiagnostic(BaseModel):
    """Diagnostics-only boundary report used before graph materialization."""

    kind: str
    message: str
    link_id: str
    suggestions: list[str] = Field(default_factory=list)


class AssembledSectionLinkResult(BaseModel):
    """Diagnostics-only linker result for section artifacts."""

    accepted: bool = True
    boundary_links: list[BoundaryLink] = Field(default_factory=list)
    diagnostics: list[BoundaryDiagnostic] = Field(default_factory=list)


class BoundaryLinkFailure(BaseModel):
    """Structured boundary failure that preserves the failing join."""

    kind: Literal["section_internal", "boundary_mismatch", "whole_graph"] = "boundary_mismatch"
    message: str
    source_section_id: str | None = None
    target_section_id: str | None = None
    source_node_id: str | None = None
    source_port: str | None = None
    target_node_id: str | None = None
    target_port: str | None = None


class BoundaryLinkResult(BaseModel):
    """Outcome of stitching accepted sections into one candidate graph."""

    status: Literal["accepted", "rejected"] = "accepted"
    candidate_graph: dict[str, Any] | None = None
    failures: list[BoundaryLinkFailure] = Field(default_factory=list)
    repair_notes: list[str] = Field(default_factory=list)
    reused_sections: list[str] = Field(default_factory=list)


def structured_boundary_repair_max() -> int:
    """Bounded number of boundary-only repair attempts."""

    raw = str(os.environ.get("DAN_STRUCTURED_BOUNDARY_REPAIR_MAX", "2") or "2").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 2


def link_sections_into_candidate_graph(
    workflow_spec: WorkflowSpec,
    sections: list[SectionAssemblyArtifact],
    *,
    workflow_id: str,
) -> BoundaryLinkResult:
    """Materialize structured node plans, merge local edges, and stitch section joins."""

    failures: list[BoundaryLinkFailure] = []
    repaired_sections = [section.model_copy(deep=True) for section in sections]
    diagnostics, repair_notes = _repair_section_boundaries(repaired_sections)
    if not diagnostics.accepted:
        return BoundaryLinkResult(
            status="rejected",
            failures=[
                BoundaryLinkFailure(kind="boundary_mismatch", message=item.message)
                for item in diagnostics.diagnostics
            ],
            repair_notes=repair_notes,
            reused_sections=[
                section.section_id
                for section in repaired_sections
                if section.acceptance == "accepted"
            ],
        )

    accepted_sections = [
        section for section in repaired_sections if section.acceptance == "accepted"
    ]
    if len(accepted_sections) != len(repaired_sections):
        for section in repaired_sections:
            if section.acceptance != "accepted":
                failures.append(
                    BoundaryLinkFailure(
                        kind="section_internal",
                        message=f"{section.section_id} was not accepted for whole-graph linking",
                        source_section_id=section.section_id,
                    )
                )
        return BoundaryLinkResult(
            status="rejected",
            failures=failures,
            reused_sections=[section.section_id for section in accepted_sections],
        )

    candidate: dict[str, Any] = {
        "version": "dan_graph_v1",
        "metadata": {
            "name": workflow_spec.workflow_id or workflow_id,
            "description": workflow_spec.goal,
            "tags": ["structured_generation"],
            "structured_generation": {
                "candidate_workspace_id": workflow_spec.candidate_workspace_id,
                "schedule": workflow_spec.schedule.model_dump(mode="json") if workflow_spec.schedule is not None else None,
                "section_ids": [section.section_id for section in repaired_sections],
            },
        },
        "nodes": [],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [],
        "exit_points": [],
    }

    plan_by_node_id: dict[str, NodePlan] = {}
    edge_ids: set[str] = set()
    for section in accepted_sections:
        for plan in section.node_plans:
            plan_by_node_id[plan.node_id] = plan
            node_dict, sub_graphs = _materialize_node_plan(plan)
            candidate["nodes"].append(node_dict)
            candidate["sub_graphs"].update(sub_graphs)
        for edge in section.internal_edges:
            edge_dict = _edge_dict(edge)
            if edge_dict["id"] not in edge_ids:
                edge_ids.add(edge_dict["id"])
                candidate["edges"].append(edge_dict)
        repair_notes.extend(note.message for note in section.repair_notes)

    _add_global_input_surface(
        candidate,
        workflow_spec,
        accepted_sections,
        edge_ids=edge_ids,
    )

    for section in accepted_sections:
        for boundary in section.entry_ports:
            if boundary.source_kind != PortBindingSourceKind.dependency_output:
                continue
            edge = SectionEdgePlan(
                edge_id=(
                    f"{section.section_id}:{boundary.source_node_id}.{boundary.source_port}"
                    f"->{boundary.node_id}.{boundary.port.name}"
                ),
                source_node_id=str(boundary.source_node_id or ""),
                source_port=str(boundary.source_port or ""),
                target_node_id=boundary.node_id,
                target_port=boundary.port.name,
                boundary=True,
            )
            if not edge.source_node_id or not edge.source_port:
                failures.append(
                    BoundaryLinkFailure(
                        kind="boundary_mismatch",
                        message=f"{boundary.boundary_id} is missing a concrete producer",
                        target_section_id=section.section_id,
                        target_node_id=boundary.node_id,
                        target_port=boundary.port.name,
                    )
                )
                continue
            if edge.source_node_id not in plan_by_node_id:
                failures.append(
                    BoundaryLinkFailure(
                        kind="boundary_mismatch",
                        message=f"Boundary producer {edge.source_node_id!r} is not part of the accepted candidate graph",
                        source_section_id=boundary.source_section_id,
                        target_section_id=section.section_id,
                        source_node_id=edge.source_node_id,
                        source_port=edge.source_port,
                        target_node_id=edge.target_node_id,
                        target_port=edge.target_port,
                    )
                )
                continue
            edge_dict = _edge_dict(edge)
            if edge_dict["id"] not in edge_ids:
                edge_ids.add(edge_dict["id"])
                candidate["edges"].append(edge_dict)

    if failures:
        return BoundaryLinkResult(
            status="rejected",
            failures=failures,
            repair_notes=repair_notes,
            reused_sections=[section.section_id for section in accepted_sections],
        )

    candidate["entry_points"] = _entry_points(candidate)
    candidate["exit_points"] = _exit_points(candidate)
    report = validate_workflow_build_contract(
        copy.deepcopy(candidate),
        workflow_id=workflow_spec.workflow_id or workflow_id,
        apply_repairs=True,
    )
    if report.graph_dict is not None:
        candidate = report.graph_dict
    candidate.setdefault("metadata", {})
    candidate["metadata"]["structured_generation"] = {
        "candidate_workspace_id": workflow_spec.candidate_workspace_id,
        "schedule": (
            workflow_spec.schedule.model_dump(mode="json")
            if workflow_spec.schedule is not None
            else None
        ),
        "section_ids": [section.section_id for section in repaired_sections],
        "global_inputs": [
            spec["name"]
            for spec in _workflow_input_specs(workflow_spec)
        ],
        "expected_outputs": [
            artifact.name
            for artifact in workflow_spec.expected_outputs
        ],
    }
    if report.auto_fixes_applied:
        repair_notes.extend(report.auto_fixes_applied)
    if not report.validated:
        return BoundaryLinkResult(
            status="rejected",
            failures=[
                BoundaryLinkFailure(kind="whole_graph", message=issue.message)
                for issue in report.errors
            ],
            repair_notes=repair_notes,
            reused_sections=[section.section_id for section in accepted_sections],
        )
    if not report.run_ready:
        return BoundaryLinkResult(
            status="rejected",
            failures=[
                BoundaryLinkFailure(kind="whole_graph", message=issue)
                for issue in report.run_readiness_issues
            ],
            repair_notes=repair_notes,
            reused_sections=[section.section_id for section in accepted_sections],
        )

    return BoundaryLinkResult(
        status="accepted",
        candidate_graph=candidate,
        failures=[],
        repair_notes=repair_notes,
        reused_sections=[section.section_id for section in accepted_sections],
    )


def _repair_section_boundaries(
    sections: list[SectionAssemblyArtifact],
) -> tuple[AssembledSectionLinkResult, list[str]]:
    """Apply bounded deterministic alias repairs before rejecting joins."""

    notes: list[str] = []
    max_attempts = structured_boundary_repair_max()
    for attempt in range(max_attempts + 1):
        diagnostics = link_assembled_sections(sections)
        if diagnostics.accepted:
            return diagnostics, notes
        if attempt >= max_attempts:
            return diagnostics, notes
        if not _apply_boundary_alias_repairs(sections, attempt=attempt + 1, notes=notes):
            return diagnostics, notes
    return link_assembled_sections(sections), notes


def _apply_boundary_alias_repairs(
    sections: list[SectionAssemblyArtifact],
    *,
    attempt: int,
    notes: list[str],
) -> bool:
    """Repair obvious single-port source/target drift without rebuilding sections."""

    repaired = False
    plan_by_node = {
        plan.node_id: plan
        for section in sections
        for plan in section.node_plans
    }
    for section in sections:
        for boundary in section.entry_ports:
            if boundary.source_kind != PortBindingSourceKind.dependency_output:
                continue
            source_plan = plan_by_node.get(str(boundary.source_node_id or ""))
            if source_plan is not None:
                source_outputs = {port.name for port in source_plan.output_ports}
                if (
                    boundary.source_port not in source_outputs
                    and len(source_plan.output_ports) == 1
                ):
                    previous = str(boundary.source_port or "")
                    boundary.source_port = source_plan.output_ports[0].name
                    repaired = True
                    notes.append(
                        "Boundary repair attempt "
                        f"{attempt}: mapped {source_plan.node_id}.{previous or '?'} "
                        f"to {source_plan.node_id}.{boundary.source_port}"
                    )
            target_plan = plan_by_node.get(boundary.node_id)
            if target_plan is not None:
                target_inputs = {port.name for port in target_plan.input_ports}
                if (
                    boundary.port.name not in target_inputs
                    and len(target_plan.input_ports) == 1
                ):
                    previous = boundary.port.name
                    boundary.port = target_plan.input_ports[0].model_copy(deep=True)
                    repaired = True
                    notes.append(
                        "Boundary repair attempt "
                        f"{attempt}: mapped {target_plan.node_id}.{previous} "
                        f"to {target_plan.node_id}.{boundary.port.name}"
                    )
    return repaired


def link_assembled_sections(
    sections: list[SectionAssemblyArtifact],
) -> AssembledSectionLinkResult:
    """Run diagnostics-only linking across section boundary ports."""

    plan_by_node: dict[str, NodePlan] = {}
    for section in sections:
        for plan in section.node_plans:
            plan_by_node[plan.node_id] = plan

    boundary_links: list[BoundaryLink] = []
    diagnostics: list[BoundaryDiagnostic] = []
    for section in sections:
        for boundary in section.entry_ports:
            if boundary.source_kind != PortBindingSourceKind.dependency_output:
                continue
            if not boundary.source_node_id or not boundary.source_port:
                diagnostics.append(
                    BoundaryDiagnostic(
                        kind=BoundaryDiagnosticKind.missing_source_output,
                        message=f"{boundary.boundary_id} has no concrete source output",
                        link_id=boundary.boundary_id,
                        suggestions=["Populate source_node_id/source_port from the upstream node plan."],
                    )
                )
                continue
            link = BoundaryLink(
                link_id=(
                    f"{boundary.source_section_id}:{boundary.source_node_id}.{boundary.source_port}"
                    f"->{section.section_id}:{boundary.node_id}.{boundary.port.name}"
                ),
                source_section_id=str(boundary.source_section_id or ""),
                target_section_id=section.section_id,
                source_node_id=boundary.source_node_id,
                source_port=boundary.source_port,
                target_node_id=boundary.node_id,
                target_port=boundary.port.name,
            )
            boundary_links.append(link)

            source_plan = plan_by_node.get(boundary.source_node_id)
            target_plan = plan_by_node.get(boundary.node_id)
            source_outputs = {port.name for port in source_plan.output_ports} if source_plan is not None else set()
            target_inputs = {port.name for port in target_plan.input_ports} if target_plan is not None else set()
            if boundary.source_port not in source_outputs:
                suggestions = []
                if source_plan is not None and len(source_plan.output_ports) == 1:
                    suggestions.append(
                        f"Use {source_plan.output_ports[0].name!r} as the boundary output alias."
                    )
                diagnostics.append(
                    BoundaryDiagnostic(
                        kind=(
                            BoundaryDiagnosticKind.renamed_contract_field
                            if suggestions
                            else BoundaryDiagnosticKind.missing_source_output
                        ),
                        message=(
                            f"Boundary link {link.link_id} references missing source "
                            f"{boundary.source_node_id}.{boundary.source_port}"
                        ),
                        link_id=link.link_id,
                        suggestions=suggestions or ["Reconcile the upstream output port name."],
                    )
                )
            if boundary.port.name not in target_inputs:
                suggestions = []
                if target_plan is not None and len(target_plan.input_ports) == 1:
                    suggestions.append(
                        f"Use {target_plan.input_ports[0].name!r} as the target input alias."
                    )
                diagnostics.append(
                    BoundaryDiagnostic(
                        kind=(
                            BoundaryDiagnosticKind.renamed_contract_field
                            if suggestions
                            else BoundaryDiagnosticKind.missing_target_input
                        ),
                        message=(
                            f"Boundary link {link.link_id} references missing target "
                            f"{boundary.node_id}.{boundary.port.name}"
                        ),
                        link_id=link.link_id,
                        suggestions=suggestions or ["Reconcile the downstream input port name."],
                    )
                )

    return AssembledSectionLinkResult(
        accepted=not diagnostics,
        boundary_links=boundary_links,
        diagnostics=diagnostics,
    )


def _materialize_node_plan(plan: NodePlan) -> tuple[dict[str, Any], dict[str, Any]]:
    node_dict = {
        "id": plan.node_id,
        "name": plan.node_id,
        "description": plan.purpose,
        "node_type": plan.node_type,
        "input_ports": [port.model_dump(mode="json") for port in plan.input_ports],
        "output_ports": [port.model_dump(mode="json") for port in plan.output_ports],
        "metadata": {
            "structured_generation": {
                "section_id": plan.section_id,
                "executor_kind": plan.executor_kind.value,
                "input_bindings": [
                    binding.model_dump(mode="json")
                    for binding in plan.input_bindings
                ],
                "test_contracts": [
                    test.model_dump(mode="json")
                    for test in plan.test_contracts
                ],
            }
        },
    }
    sub_graphs: dict[str, Any] = {}
    config = dict(plan.executor_config)

    if plan.node_type == "llm_operator":
        node_dict.update(
            model=str(config.get("model") or "gpt-5-mini"),
            prompt_template=str(config.get("prompt_template") or plan.purpose),
            system_prompt=str(config.get("system_prompt") or ""),
            temperature=float(config.get("temperature", 0.2)),
            tools=list(config.get("tools", []) or []),
        )
    elif plan.node_type == "tool_operator":
        node_dict.update(
            tool_id=str(config.get("tool_id") or ""),
            tool_config=_materialized_tool_config(plan),
        )
    elif plan.node_type == "code_operator":
        node_dict.update(
            code=str(config.get("code") or ""),
            language=str(config.get("language") or "python"),
            sandbox_config=dict(config.get("sandbox_config", {}) or {}),
        )
    elif plan.node_type == "gate":
        node_dict.update(
            condition=str(config.get("condition") or "result == true"),
            gate_mode=str(config.get("gate_mode") or "if_else"),
            max_iterations=int(config.get("max_iterations", 10) or 10),
        )
    elif plan.node_type == "rag_operator":
        node_dict.update(
            collection=str(config.get("collection") or "default"),
            query_template=str(config.get("query_template") or "{query}"),
        )
    elif plan.node_type == "human":
        node_dict.update(prompt=str(config.get("prompt") or plan.purpose))
    elif plan.node_type == "for_each":
        body_key = f"{plan.node_id}_body"
        body_graph = _for_each_body_graph(plan)
        sub_graphs[body_key] = body_graph
        node_dict.update(
            body_graph=body_key,
            parallelism=int(config.get("parallelism", 1) or 1),
            merge_strategy=str(config.get("merge_strategy") or "append"),
            external_input_schema={"type": "array"},
            external_output_schema={"type": "array"},
        )
    else:
        # Control-flow or future types remain serializable as bare nodes only when
        # their executor config is already complete enough for the Graph contract.
        node_dict.update(config)
    return node_dict, sub_graphs


def _for_each_body_graph(plan: NodePlan) -> dict[str, Any]:
    config = dict(plan.executor_config)
    body_node_id = f"{plan.node_id}_proc"
    body_input_ports = list(config.get("body_input_ports") or [{"name": "item"}, {"name": "index", "required": False}])
    body_output_ports = list(config.get("body_output_ports") or [{"name": "result"}])
    body_code = str(config.get("body_code") or "").strip()
    if body_code:
        inner_node = {
            "id": body_node_id,
            "name": body_node_id,
            "description": f"Structured foreach body for {plan.node_id}",
            "node_type": "code_operator",
            "input_ports": body_input_ports,
            "output_ports": body_output_ports,
            "code": body_code,
            "language": "python",
            "sandbox_config": {},
        }
    else:
        inner_node = {
            "id": body_node_id,
            "name": body_node_id,
            "description": f"Structured foreach body for {plan.node_id}",
            "node_type": "llm_operator",
            "input_ports": body_input_ports,
            "output_ports": body_output_ports,
            "model": "gpt-5-mini",
            "prompt_template": str(
                config.get("prompt_template")
                or plan.purpose
                or f"Process one item for {plan.node_id}"
            ),
            "system_prompt": "",
            "temperature": 0.2,
            "tools": [],
        }
    return {
        "version": "dan_graph_v1",
        "metadata": {
            "name": f"{plan.node_id}_body",
            "description": f"Auto-generated foreach body for {plan.node_id}",
        },
        "nodes": [inner_node],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [body_node_id],
        "exit_points": [body_node_id],
    }


def _materialized_tool_config(plan: NodePlan) -> dict[str, Any]:
    tool_config = dict(plan.executor_config.get("tool_config", {}) or {})
    for binding in plan.input_bindings:
        if binding.input_port in tool_config and tool_config[binding.input_port] not in (None, "", [], {}):
            continue
        if binding.source_kind == PortBindingSourceKind.literal:
            tool_config[binding.input_port] = binding.value
        elif binding.source_kind == PortBindingSourceKind.external_data:
            tool_config[binding.input_port] = str(binding.source_artifact or binding.input_port)
    return tool_config


def _add_global_input_surface(
    candidate: dict[str, Any],
    workflow_spec: WorkflowSpec,
    sections: list[SectionAssemblyArtifact],
    *,
    edge_ids: set[str],
) -> None:
    input_specs = _workflow_input_specs(workflow_spec)
    if not input_specs:
        return

    input_node_id = "workflow_inputs"
    candidate["nodes"].insert(
        0,
        {
            "id": input_node_id,
            "name": "Workflow Inputs",
            "description": "Structured-generation input surface",
            "node_type": "input",
            "variables": input_specs,
            "output_ports": [
                {"name": spec["name"], "required": False}
                for spec in input_specs
            ],
            "metadata": {
                "structured_generation": {
                    "role": "workflow_inputs",
                    "global_inputs": [spec["name"] for spec in input_specs],
                }
            },
        },
    )

    for section in sections:
        for plan in section.node_plans:
            for binding in plan.input_bindings:
                if binding.source_kind != PortBindingSourceKind.global_input:
                    continue
                source_port = str(binding.source_artifact or binding.input_port or "").strip()
                if not source_port:
                    continue
                edge = SectionEdgePlan(
                    edge_id=f"{input_node_id}.{source_port}->{plan.node_id}.{binding.input_port}",
                    source_node_id=input_node_id,
                    source_port=source_port,
                    target_node_id=plan.node_id,
                    target_port=binding.input_port,
                    boundary=True,
                )
                edge_dict = _edge_dict(edge)
                if edge_dict["id"] in edge_ids:
                    continue
                edge_ids.add(edge_dict["id"])
                candidate["edges"].append(edge_dict)

    candidate["metadata"].setdefault("structured_generation", {}).update(
        {
            "global_inputs": [spec["name"] for spec in input_specs],
            "expected_outputs": [
                artifact.name
                for artifact in workflow_spec.expected_outputs
            ],
        }
    )


def _workflow_input_specs(workflow_spec: WorkflowSpec) -> list[dict[str, Any]]:
    artifact_by_name: dict[str, ArtifactSpec] = {
        artifact.name: artifact
        for artifact in workflow_spec.expected_inputs
    }
    ordered_names: list[str] = []
    for name in workflow_spec.global_inputs:
        text = str(name).strip()
        if text and text not in ordered_names:
            ordered_names.append(text)
    for artifact in workflow_spec.expected_inputs:
        if artifact.name not in ordered_names:
            ordered_names.append(artifact.name)

    specs: list[dict[str, Any]] = []
    for name in ordered_names:
        artifact = artifact_by_name.get(name)
        schema = dict((artifact.schema_ if artifact is not None else None) or {})
        input_type = str(schema.get("type") or "string").strip().lower()
        if input_type not in {"string", "number", "boolean"}:
            input_type = "string"
        specs.append(
            {
                "name": name,
                "type": input_type,
                "default": schema.get("default"),
                "description": (artifact.description if artifact is not None else "") or "",
            }
        )
    return specs


def _edge_dict(edge: SectionEdgePlan) -> dict[str, Any]:
    return {
        "id": edge.edge_id,
        "edge_type": edge.edge_type,
        "source_node_id": edge.source_node_id,
        "source_port": edge.source_port,
        "target_node_id": edge.target_node_id,
        "target_port": edge.target_port,
        "metadata": {"boundary": edge.boundary},
    }


def _entry_points(candidate: dict[str, Any]) -> list[str]:
    node_ids = {
        str(node.get("id") or "").strip()
        for node in candidate.get("nodes", []) or []
        if str(node.get("id") or "").strip()
    }
    incoming = {
        str(edge.get("target_node_id") or "").strip()
        for edge in candidate.get("edges", []) or []
        if str(edge.get("target_node_id") or "").strip()
    }
    return sorted(node_ids - incoming)


def _exit_points(candidate: dict[str, Any]) -> list[str]:
    node_ids = {
        str(node.get("id") or "").strip()
        for node in candidate.get("nodes", []) or []
        if str(node.get("id") or "").strip()
    }
    outgoing = {
        str(edge.get("source_node_id") or "").strip()
        for edge in candidate.get("edges", []) or []
        if str(edge.get("source_node_id") or "").strip()
    }
    return sorted(node_ids - outgoing)
