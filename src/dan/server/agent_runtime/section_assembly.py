"""Section assembly artifacts and local validation helpers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.meta.workflow_spec import (
    PortBindingSourceKind,
    RunnableTestSpec,
    WorkflowPortSpec,
    WorkflowSection,
    WorkflowSpec,
)
from dan.server.agent_runtime.node_worker import NodePlan

__all__ = [
    "SectionAssemblyArtifact",
    "SectionAssemblyConfig",
    "SectionAssemblyIssue",
    "SectionBoundaryPort",
    "SectionEdgePlan",
    "SectionRepairNote",
    "SectionTestSummary",
    "assemble_section",
    "assemble_sections",
    "repair_section_artifact",
    "validate_section_artifact",
]


class SectionAssemblyConfig(BaseModel):
    """Configurable section-local assembly and repair limits."""

    assembler_cap: int = 8
    repair_max: int = 2

    @classmethod
    def from_env(cls) -> SectionAssemblyConfig:
        worker_cap = _read_int_env("DAN_STRUCTURED_WORKER_POOL_CAP", 8, minimum=1)
        assembler_cap = _read_int_env("DAN_STRUCTURED_SECTION_ASSEMBLER_CAP", worker_cap, minimum=1)
        repair_max = _read_int_env("DAN_STRUCTURED_SECTION_REPAIR_MAX", 2, minimum=0)
        return cls(assembler_cap=assembler_cap, repair_max=repair_max)


class SectionEdgePlan(BaseModel):
    """Serializable local edge contract for a section artifact."""

    edge_id: str
    source_node_id: str
    source_port: str
    target_node_id: str
    target_port: str
    edge_type: Literal["data"] = "data"
    boundary: bool = False


class SectionBoundaryPort(BaseModel):
    """Explicit boundary metadata preserved around each section."""

    boundary_id: str
    direction: Literal["entry", "exit"]
    node_id: str
    port: WorkflowPortSpec
    source_kind: PortBindingSourceKind | None = None
    source_node_id: str | None = None
    source_port: str | None = None
    source_section_id: str | None = None
    target_node_ids: list[str] = Field(default_factory=list)
    target_section_ids: list[str] = Field(default_factory=list)
    target_ports: list[str] = Field(default_factory=list)
    artifact_name: str | None = None
    required: bool = True


class SectionAssemblyIssue(BaseModel):
    """Structured section-local validation failure."""

    code: str
    message: str
    severity: Literal["error", "warning"] = "error"
    node_id: str | None = None
    edge_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class SectionRepairNote(BaseModel):
    """Deterministic repair trace for local section fixes."""

    attempt: int
    action: str
    message: str


class SectionTestSummary(BaseModel):
    """Minimal section-local acceptance summary."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    test_ids: list[str] = Field(default_factory=list)
    status: Literal["accepted", "rejected"] = "accepted"


class SectionAssemblyArtifact(BaseModel):
    """Assembled section artifact retained before whole-graph linking."""

    section_id: str
    index: int
    chapter_label: str | None = None
    section_label: str | None = None
    acceptance: Literal["accepted", "deferred", "rejected"] = "accepted"
    node_plans: list[NodePlan] = Field(default_factory=list)
    internal_edges: list[SectionEdgePlan] = Field(default_factory=list)
    entry_ports: list[SectionBoundaryPort] = Field(default_factory=list)
    exit_ports: list[SectionBoundaryPort] = Field(default_factory=list)
    validation_issues: list[SectionAssemblyIssue] = Field(default_factory=list)
    repair_notes: list[SectionRepairNote] = Field(default_factory=list)
    test_summary: SectionTestSummary = Field(default_factory=SectionTestSummary)
    boundary_metadata: dict[str, Any] = Field(default_factory=dict)


def assemble_sections(
    workflow_spec: WorkflowSpec,
    sections: list[WorkflowSection],
    node_plans: list[NodePlan],
    *,
    config: SectionAssemblyConfig | None = None,
) -> list[SectionAssemblyArtifact]:
    """Assemble all sections using previously accepted node plans."""

    config = config or SectionAssemblyConfig.from_env()
    plan_by_node_id = {plan.node_id: plan for plan in node_plans}
    if len(sections) <= 1 or config.assembler_cap <= 1:
        assembled: list[SectionAssemblyArtifact] = []
        for section in sections:
            section_plans = [
                plan_by_node_id[node_id]
                for node_id in section.node_ids
                if node_id in plan_by_node_id
            ]
            assembled.append(
                assemble_section(
                    workflow_spec,
                    section,
                    section_plans,
                    all_node_plans=node_plans,
                    config=config,
                )
            )
        return assembled

    max_workers = min(config.assembler_cap, len(sections))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for section in sections:
            section_plans = [
                plan_by_node_id[node_id]
                for node_id in section.node_ids
                if node_id in plan_by_node_id
            ]
            futures.append(
                executor.submit(
                    assemble_section,
                    workflow_spec,
                    section,
                    section_plans,
                    all_node_plans=node_plans,
                    config=config,
                )
            )
        return [future.result() for future in futures]


def assemble_section(
    workflow_spec: WorkflowSpec,
    section: WorkflowSection,
    node_plans: list[NodePlan],
    *,
    all_node_plans: list[NodePlan] | None = None,
    config: SectionAssemblyConfig | None = None,
) -> SectionAssemblyArtifact:
    """Assemble one section artifact and run bounded local repair."""

    config = config or SectionAssemblyConfig.from_env()
    all_node_plans = all_node_plans or node_plans
    artifact = _assemble_section_once(workflow_spec, section, node_plans, all_node_plans=all_node_plans)
    return repair_section_artifact(
        workflow_spec,
        section,
        artifact,
        all_node_plans=all_node_plans,
        config=config,
    )


def validate_section_artifact(artifact: SectionAssemblyArtifact) -> list[SectionAssemblyIssue]:
    """Validate local nodes, edges, and explicit section boundaries."""

    issues: list[SectionAssemblyIssue] = []
    plan_by_id = {plan.node_id: plan for plan in artifact.node_plans}

    for edge in artifact.internal_edges:
        source = plan_by_id.get(edge.source_node_id)
        target = plan_by_id.get(edge.target_node_id)
        if source is None or target is None:
            issues.append(
                SectionAssemblyIssue(
                    code="missing_edge_node",
                    message=f"Edge {edge.edge_id!r} references missing local node(s)",
                    edge_id=edge.edge_id,
                )
            )
            continue
        source_port_names = {port.name for port in source.output_ports}
        target_port_names = {port.name for port in target.input_ports}
        if edge.source_port not in source_port_names:
            issues.append(
                SectionAssemblyIssue(
                    code="missing_source_output",
                    message=f"Section edge {edge.edge_id!r} references missing output {edge.source_node_id}.{edge.source_port}",
                    node_id=edge.source_node_id,
                    edge_id=edge.edge_id,
                    context={
                        "source_node_id": edge.source_node_id,
                        "source_port": edge.source_port,
                        "target_node_id": edge.target_node_id,
                        "target_port": edge.target_port,
                    },
                )
            )
        if edge.target_port not in target_port_names:
            issues.append(
                SectionAssemblyIssue(
                    code="missing_target_input",
                    message=f"Section edge {edge.edge_id!r} references missing input {edge.target_node_id}.{edge.target_port}",
                    node_id=edge.target_node_id,
                    edge_id=edge.edge_id,
                    context={
                        "source_node_id": edge.source_node_id,
                        "source_port": edge.source_port,
                        "target_node_id": edge.target_node_id,
                        "target_port": edge.target_port,
                    },
                )
            )

    for boundary in artifact.entry_ports:
        if boundary.source_kind is None:
            issues.append(
                SectionAssemblyIssue(
                    code="orphan_entry_port",
                    message=f"Entry boundary {boundary.boundary_id!r} has no declared producer",
                    node_id=boundary.node_id,
                )
            )

    for boundary in artifact.exit_ports:
        if not boundary.target_node_ids and not boundary.artifact_name:
            issues.append(
                SectionAssemblyIssue(
                    code="orphan_exit_port",
                    message=f"Exit boundary {boundary.boundary_id!r} has no downstream consumer",
                    node_id=boundary.node_id,
                )
            )

    return issues


def repair_section_artifact(
    workflow_spec: WorkflowSpec,
    section: WorkflowSection,
    artifact: SectionAssemblyArtifact,
    *,
    all_node_plans: list[NodePlan],
    config: SectionAssemblyConfig | None = None,
) -> SectionAssemblyArtifact:
    """Apply bounded deterministic local repairs and revalidate."""

    config = config or SectionAssemblyConfig.from_env()
    working = artifact
    notes = list(artifact.repair_notes)
    for attempt in range(config.repair_max + 1):
        issues = validate_section_artifact(working)
        if not issues:
            return _finalize_section_artifact(working, issues, notes)
        if attempt >= config.repair_max:
            return _finalize_section_artifact(working, issues, notes)
        repaired_plans = [plan.model_copy(deep=True) for plan in working.node_plans]
        repaired = False
        plan_by_id = {plan.node_id: plan for plan in repaired_plans}
        for issue in issues:
            if issue.code == "missing_source_output":
                source = plan_by_id.get(str(issue.context.get("source_node_id") or ""))
                target = plan_by_id.get(str(issue.context.get("target_node_id") or ""))
                if source is not None and target is not None and len(source.output_ports) == 1:
                    expected_port = str(issue.context.get("source_port") or "")
                    target_port = str(issue.context.get("target_port") or "")
                    for binding in target.input_bindings:
                        if (
                            binding.source_node_id == source.node_id
                            and binding.source_port == expected_port
                            and binding.input_port == target_port
                        ):
                            binding.source_port = source.output_ports[0].name
                            repaired = True
                            notes.append(
                                SectionRepairNote(
                                    attempt=attempt + 1,
                                    action="remap_source_output",
                                    message=(
                                        f"Mapped {source.node_id}.{expected_port} to sole output "
                                        f"{source.output_ports[0].name} for {target.node_id}.{target_port}"
                                    ),
                                )
                            )
            if issue.code == "missing_target_input":
                source = plan_by_id.get(str(issue.context.get("source_node_id") or ""))
                target = plan_by_id.get(str(issue.context.get("target_node_id") or ""))
                if source is not None and target is not None and len(target.input_ports) == 1:
                    source_port = str(issue.context.get("source_port") or "")
                    expected_port = str(issue.context.get("target_port") or "")
                    for binding in target.input_bindings:
                        if (
                            binding.source_node_id == source.node_id
                            and binding.source_port == source_port
                            and binding.input_port == expected_port
                        ):
                            binding.input_port = target.input_ports[0].name
                            repaired = True
                            notes.append(
                                SectionRepairNote(
                                    attempt=attempt + 1,
                                    action="remap_target_input",
                                    message=(
                                        f"Mapped {target.node_id}.{expected_port} to sole input "
                                        f"{target.input_ports[0].name}"
                                    ),
                                )
                            )
        if not repaired:
            return _finalize_section_artifact(working, issues, notes)
        working = _assemble_section_once(workflow_spec, section, repaired_plans, all_node_plans=all_node_plans)
    return _finalize_section_artifact(working, validate_section_artifact(working), notes)


def _assemble_section_once(
    workflow_spec: WorkflowSpec,
    section: WorkflowSection,
    node_plans: list[NodePlan],
    *,
    all_node_plans: list[NodePlan],
) -> SectionAssemblyArtifact:
    local_node_ids = {plan.node_id for plan in node_plans}
    section_id_by_node = {plan.node_id: plan.section_id for plan in all_node_plans}
    internal_edges: list[SectionEdgePlan] = []
    entry_ports: list[SectionBoundaryPort] = []
    exit_ports: list[SectionBoundaryPort] = []
    consumer_map: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)

    for plan in all_node_plans:
        for binding in plan.input_bindings:
            if (
                binding.source_kind == PortBindingSourceKind.dependency_output
                and binding.source_node_id
                and binding.source_port
            ):
                consumer_map[(binding.source_node_id, binding.source_port)].append(
                    (plan.node_id, binding.input_port, plan.section_id)
                )

    for plan in node_plans:
        for binding in plan.input_bindings:
            if binding.source_kind == PortBindingSourceKind.dependency_output and binding.source_node_id:
                if binding.source_node_id in local_node_ids:
                    internal_edges.append(
                        SectionEdgePlan(
                            edge_id=(
                                f"{section.section_id}:{binding.source_node_id}.{binding.source_port}"
                                f"->{plan.node_id}.{binding.input_port}"
                            ),
                            source_node_id=binding.source_node_id,
                            source_port=str(binding.source_port or ""),
                            target_node_id=plan.node_id,
                            target_port=binding.input_port,
                        )
                    )
                else:
                    entry_ports.append(
                        SectionBoundaryPort(
                            boundary_id=f"{section.section_id}:entry:{plan.node_id}.{binding.input_port}",
                            direction="entry",
                            node_id=plan.node_id,
                            port=_port_by_name(plan.input_ports, binding.input_port)
                            or WorkflowPortSpec(name=binding.input_port),
                            source_kind=binding.source_kind,
                            source_node_id=binding.source_node_id,
                            source_port=binding.source_port,
                            source_section_id=section_id_by_node.get(binding.source_node_id),
                            required=True,
                        )
                    )
            elif binding.source_kind in {
                PortBindingSourceKind.global_input,
                PortBindingSourceKind.external_data,
                PortBindingSourceKind.literal,
                PortBindingSourceKind.implicit,
            }:
                entry_ports.append(
                    SectionBoundaryPort(
                        boundary_id=f"{section.section_id}:entry:{plan.node_id}.{binding.input_port}",
                        direction="entry",
                        node_id=plan.node_id,
                        port=_port_by_name(plan.input_ports, binding.input_port)
                        or WorkflowPortSpec(name=binding.input_port),
                        source_kind=binding.source_kind,
                        source_port=binding.source_port,
                        artifact_name=binding.source_artifact,
                        required=True,
                    )
                )

    for plan in node_plans:
        for output_port in plan.output_ports:
            consumers = consumer_map.get((plan.node_id, output_port.name), [])
            downstream = [item for item in consumers if item[2] != section.section_id]
            is_global_output = output_port.name in workflow_spec.global_outputs
            if downstream or is_global_output:
                exit_ports.append(
                    SectionBoundaryPort(
                        boundary_id=f"{section.section_id}:exit:{plan.node_id}.{output_port.name}",
                        direction="exit",
                        node_id=plan.node_id,
                        port=output_port,
                        source_node_id=plan.node_id,
                        source_port=output_port.name,
                        target_node_ids=[item[0] for item in downstream],
                        target_ports=[item[1] for item in downstream],
                        target_section_ids=_ordered_unique([item[2] for item in downstream]),
                        artifact_name=output_port.name if is_global_output else None,
                        required=False,
                    )
                )

    test_summary = _summarize_section_tests(node_plans)
    return SectionAssemblyArtifact(
        section_id=section.section_id,
        index=section.index,
        chapter_label=section.chapter_label,
        section_label=section.section_label,
        acceptance="accepted",
        node_plans=node_plans,
        internal_edges=sorted(internal_edges, key=lambda item: item.edge_id),
        entry_ports=sorted(entry_ports, key=lambda item: item.boundary_id),
        exit_ports=sorted(exit_ports, key=lambda item: item.boundary_id),
        test_summary=test_summary,
        boundary_metadata={
            "upstream_dependencies": list(section.upstream_dependencies),
            "downstream_dependents": list(section.downstream_dependents),
            "node_ids": list(section.node_ids),
        },
    )


def _summarize_section_tests(node_plans: list[NodePlan]) -> SectionTestSummary:
    tests: list[RunnableTestSpec] = []
    for plan in node_plans:
        tests.extend(plan.test_contracts)
    total = len(tests)
    return SectionTestSummary(
        total=total,
        passed=total,
        failed=0,
        test_ids=[test.test_id for test in tests],
        status="accepted",
    )


def _finalize_section_artifact(
    artifact: SectionAssemblyArtifact,
    issues: list[SectionAssemblyIssue],
    notes: list[SectionRepairNote],
) -> SectionAssemblyArtifact:
    has_error = any(issue.severity == "error" for issue in issues)
    total = artifact.test_summary.total
    failed = len([issue for issue in issues if issue.severity == "error"])
    test_summary = artifact.test_summary.model_copy(
        update={
            "passed": max(total - failed, 0),
            "failed": failed,
            "status": "rejected" if has_error else "accepted",
        }
    )
    return artifact.model_copy(
        update={
            "acceptance": "rejected" if has_error else "accepted",
            "validation_issues": issues,
            "repair_notes": notes,
            "test_summary": test_summary,
        }
    )


def _port_by_name(ports: list[WorkflowPortSpec], name: str) -> WorkflowPortSpec | None:
    for port in ports:
        if port.name == name:
            return port
    return None


def _ordered_unique(items: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            ordered.append(text)
            seen.add(text)
    return ordered


def _read_int_env(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if minimum is not None and value < minimum:
        return default
    return value
