"""Dedicated coding organism built entirely from universal-worker roles."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Sequence

from pydantic import BaseModel, Field

from dan.worker.composition import (
    CrossCellTraceLog,
    HandoffExecution,
    execute_cell_handoff,
    make_completion_signal,
)
from dan.worker.core.contracts import OutputContract
from dan.worker.core.executor import WorkerCoreExecutor, WorkerExecutionResult
from dan.worker.core.model import WorkerDefinition
from dan.worker.model import WorkerAuthority
from dan.worker.organism_log import new_trace_id
from dan.worker.outcomes import (
    ExecutionEffect as WorkspaceEffect,
    StructuredOutcomeView as CodingCandidateView,
    clean_text as _clean_text,
    first_text as _first_text,
    has_material_structured_outcome as _has_material_structured_outcome,
    infer_execution_effect as _infer_execution_effect,
    mutation_claim_requires_evidence as _mutation_claim_requires_evidence,
    normalize_structured_outcome_payload as _normalize_structured_outcome_payload,
    structured_outcome_view as _structured_outcome_view,
)
from dan.worker.organisms.project_execution import OrganismObservability, OrganismStageRecord
from dan.worker.organs import (
    OrganExecution,
    OrganExecutionResult,
    OrganPattern,
    coding_aggregation_organ,
    compact_universal_validator_organ,
    execute_organ_pattern,
    universal_validator_organ,
)
from dan.worker.scheduler import (
    ArtifactPartition,
    SchedulerAction,
    SchedulerGuardrailState,
    SchedulerTask,
    SchedulingProposal,
    evaluate_artifact_partition_admission,
    evaluate_task_readiness_from_capsules,
    select_scheduler_proposal,
)
from dan.worker.signaling import (
    CellAddress,
    CellAuthorityLimits,
    CellBudgetLimits,
    CellHandoffPacket,
    CompletionSignal,
    ContinuationHooks,
    EvidenceRef,
    HandoffTask,
    SignalTrace,
)
from dan.worker.structured_payload import parse_jsonish_payload
from dan.worker.tissue import (
    TissueExecution,
    TissueMember,
    TissuePattern,
    TissuePoolLimits,
    TissueExecutionResult,
    TissueMergeMode,
    execute_tissue_pattern,
    parallel_worker_pool,
)

OrganismEventCallback = Callable[[dict[str, Any]], None]

_VALIDATION_COMPLETION_TIMEOUT_CAP_SECONDS = 30.0


def _parse_payload(outputs: dict[str, Any]) -> dict[str, Any]:
    raw = outputs.get("result", outputs.get("text", outputs))
    if isinstance(raw, dict):
        return dict(raw)
    parsed = parse_jsonish_payload(raw)
    if isinstance(parsed, dict):
        return parsed
    return {"result": parsed}


def _preview_text(value: Any, *, limit: int = 140) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _plan_status_message(plan: "CodingOrchestratorPlan") -> str:
    public_response = _clean_text(getattr(plan, "public_response", ""))
    if public_response:
        return public_response
    briefs = [_preview_text(brief, limit=90) for brief in plan.worker_briefs if _clean_text(brief)]
    if not briefs:
        return ""
    return "; ".join(briefs[:2]) + (f"; +{len(briefs) - 2} more" if len(briefs) > 2 else "")


def _worker_status_message(member_result: dict[str, Any]) -> str:
    return _first_text(
        member_result.get("change_summary"),
        member_result.get("candidate_fragment"),
    )


def _candidate_view(payload: dict[str, Any]) -> CodingCandidateView:
    return _structured_outcome_view(
        payload,
        id_field="candidate_id",
        summary_field="change_summary",
        target_refs_field="target_files",
        validation_steps_field="test_plan",
        risks_field="risks",
        effect_field="workspace_effect",
    )


def _candidate_workspace_effect(candidate: CodingCandidateView) -> WorkspaceEffect | None:
    return _infer_execution_effect(candidate)


def _candidate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return _normalize_structured_outcome_payload(
        payload,
        id_field="candidate_id",
        summary_field="change_summary",
        target_refs_field="target_files",
        validation_steps_field="test_plan",
        risks_field="risks",
        effect_field="workspace_effect",
    )


def _candidate_readiness_key(candidate_payload: dict[str, Any]) -> str:
    normalized = _candidate_payload(dict(candidate_payload or {}))
    serialized = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _aggregation_status_message(payload: dict[str, Any]) -> str:
    candidate = _candidate_view(payload)
    return _first_text(
        candidate.change_summary,
        candidate.candidate_id,
    )


def _validation_status_message(payload: dict[str, Any]) -> str:
    return _first_text(
        payload.get("comparison_note"),
        payload.get("repair_brief"),
    )


def _result_failure_message(stage: str, *, error: str | None, metadata: dict[str, Any] | None = None) -> str:
    details = [str(item).strip() for item in (metadata or {}).get("contract_errors") or [] if str(item).strip()]
    base = _clean_text(error) or f"{stage.title()} did not complete."
    if not details:
        return f"{stage.title()} failed: {base}"
    preview = ", ".join(details[:4])
    if len(details) > 4:
        preview += f", +{len(details) - 4} more"
    return f"{stage.title()} failed: {base} ({preview})"


def build_coding_orchestrator_worker(
    *,
    worker_id: str,
    model: str | None,
) -> WorkerDefinition:
    """Shared DAN Code orchestrator worker on the universal-agent substrate."""

    return WorkerDefinition(
        id=worker_id,
        role="coding_orchestrator",
        instruction=(
            "You are the coding orchestrator for DAN Code on top of the universal worker substrate. "
            "Treat the user as another agent in the system and respond directly, concretely, and briefly. "
            "Use the current task plus output contract to decide whether you are answering a durable "
            "conversation turn, reviewing the last bounded coding run, or planning the next bounded "
            "coding attempt. Only launch coding when the request is a direct actionable repo or "
            "implementation task. For social chatter, acknowledgements, meta discussion, or ambiguous "
            "turns, respond conversationally or ask one clarifying question. When planning a coding "
            "attempt, keep the pool small, purposeful, bounded, and non-overlapping; default to one "
            "worker unless multiple distinct paths are materially useful. Prefer one worker when one "
            "bounded owner can materialize the change directly, and reserve multi-worker plans for true "
            "alternative paths or separable sub-problems. When reviewing a bounded run, treat a validator-"
            "passing completed candidate with concrete material output as the default stopping point. "
            "Do not plan another pass after a completed validated candidate just for optional extra "
            "exploration, environment cleanup, or stylistic polish."
        ),
        model=model,
    )


def _coerce_worker_count(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _planned_worker_count(
    *,
    payload: dict[str, Any],
    organism: "CodingOrganism",
) -> int:
    explicit = _coerce_worker_count(payload.get("worker_count"))
    if explicit is not None:
        return max(1, min(explicit, int(organism.max_worker_count)))

    brief_count = len(
        [str(brief).strip() for brief in payload.get("worker_briefs") or [] if str(brief).strip()]
    )
    if brief_count > 0:
        return max(1, min(brief_count, int(organism.max_worker_count)))

    return max(1, min(int(organism.default_worker_count), int(organism.max_worker_count)))


def _task_in_benchmark_mode(task: "CodingTask") -> bool:
    raw = task.session_context.get("benchmark_context") if isinstance(task.session_context, dict) else None
    return isinstance(raw, dict) and bool(raw)


_ARTIFACT_PARTITION_SUFFIX_KIND = {
    ".html": "markup",
    ".css": "style",
    ".js": "script",
    ".jsx": "script",
    ".ts": "script",
    ".tsx": "script",
    ".py": "python",
    ".md": "markdown",
    ".json": "data",
    ".toml": "config",
    ".yaml": "config",
    ".yml": "config",
}
_ARTIFACT_PARTITION_PRIORITY = {
    "markup": 10,
    "style": 20,
    "script": 30,
    "python": 40,
    "markdown": 50,
    "config": 60,
    "data": 70,
}
_ARTIFACT_PATH_PATTERN = re.compile(
    r"(?<![\w/.-])(?:[\w.-]+/)*[\w.-]+\.(?:html|css|js|jsx|ts|tsx|py|md|json|toml|ya?ml)(?![\w.-])",
    flags=re.IGNORECASE,
)


def _artifact_kind_for_path(path: str) -> str:
    return _ARTIFACT_PARTITION_SUFFIX_KIND.get(Path(path).suffix.lower(), "artifact")


def _artifact_path_key(path: Any) -> str:
    text = _clean_text(path).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    while "//" in text:
        text = text.replace("//", "/")
    return text.strip().strip("/")


def _task_artifact_text(task: "CodingTask", repair_brief: str = "") -> str:
    return " ".join(
        text
        for text in [
            _clean_text(task.objective),
            _clean_text(repair_brief),
            " ".join(_clean_text(item) for item in task.acceptance_criteria),
            " ".join(_clean_text(item) for item in task.hard_constraints),
            " ".join(_clean_text(item) for item in task.soft_constraints),
        ]
        if text
    )


def _explicit_artifact_paths_from_text(text: str) -> list[str]:
    paths: list[str] = []
    for match in _ARTIFACT_PATH_PATTERN.finditer(text):
        path = _artifact_path_key(match.group(0).rstrip(".,;:"))
        if path and path not in paths:
            paths.append(path)
    return paths


def _artifact_paths_blocked_for_mutation(text: str) -> set[str]:
    normalized = _clean_text(text)
    if not normalized:
        return set()
    blocked: set[str] = set()
    markers = (
        "do not modify",
        "don't modify",
        "must not modify",
        "should not modify",
        "do not edit",
        "don't edit",
        "must not edit",
        "should not edit",
    )
    for marker in markers:
        for match in re.finditer(re.escape(marker), normalized, flags=re.IGNORECASE):
            fragment = normalized[match.end() : match.end() + 240]
            boundary = re.search(
                r"(?:\.|;|\n|\bunless\b|\bexcept\b|\bother\s+than\b|\bafter\b|\bbefore\b)",
                fragment,
                flags=re.IGNORECASE,
            )
            if boundary:
                fragment = fragment[: boundary.start()]
            blocked.update(_explicit_artifact_paths_from_text(fragment))
    return blocked


def _artifact_paths_allowed_as_only_mutations(text: str) -> set[str]:
    normalized = _clean_text(text)
    if not normalized:
        return set()
    allowed: set[str] = set()
    patterns = (
        r"\bonly\s+([A-Za-z0-9_./~\\-]+\.[A-Za-z0-9_./~\\-]+)\s+(?:is|are|should be|may be|can be)\s+(?:modified|edited|changed|updated|written|overwritten)",
        r"\b(?:modify|edit|change|update|write|overwrite)\s+only\s+([A-Za-z0-9_./~\\-]+\.[A-Za-z0-9_./~\\-]+)",
        r"\boverwrite\s+([A-Za-z0-9_./~\\-]+\.[A-Za-z0-9_./~\\-]+)\s+with\s+(?:a\s+)?single",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            path = _artifact_path_key(match.group(1).rstrip(".,;:"))
            if path:
                allowed.add(path)
    return allowed


def _workspace_artifact_candidate_is_auxiliary(path: Path) -> bool:
    stem = path.stem.lower()
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix in {".bak", ".backup", ".orig", ".tmp"}:
        return True
    auxiliary_markers = ("backup", "bak", "copy", "old", "tmp", "temp")
    return any(
        stem == marker
        or stem.startswith(f"{marker}_")
        or stem.startswith(f"{marker}-")
        or stem.endswith(f"_{marker}")
        or stem.endswith(f"-{marker}")
        or name.endswith(f".{marker}{suffix}")
        for marker in auxiliary_markers
    )


def _artifact_paths_from_context(task: "CodingTask") -> list[str]:
    context = task.session_context if isinstance(task.session_context, dict) else {}
    raw = (
        context.get("scheduler_artifacts")
        or context.get("artifact_candidates")
        or context.get("target_artifacts")
    )
    if not isinstance(raw, list):
        return []
    paths: list[str] = []
    for item in raw:
        if isinstance(item, str):
            path = _artifact_path_key(item)
        elif isinstance(item, dict):
            path = _artifact_path_key(
                item.get("path") or item.get("artifact_path") or item.get("file") or item.get("target_file")
            )
        else:
            path = ""
        if path and path not in paths:
            paths.append(path)
    return paths


def _task_requests_existing_artifact_partition(task: "CodingTask", repair_brief: str = "") -> bool:
    context_paths = _artifact_paths_from_context(task)
    if context_paths:
        return True
    text = _task_artifact_text(task, repair_brief).lower()
    markers = (
        "entry-point",
        "entry point",
        "entrypoint",
        "existing files",
        "target files",
        "modified files",
        "all files",
        "non-overlapping",
        "separable",
    )
    return any(marker in text for marker in markers)


def _workspace_artifact_candidates(task: "CodingTask") -> list[str]:
    workspace_root = _workspace_root_from_task(task)
    if workspace_root is None or not workspace_root.exists():
        return []
    candidates: list[str] = []
    try:
        children = [path for path in workspace_root.iterdir() if path.is_file()]
    except OSError:
        return []
    for path in children:
        if path.name.startswith("."):
            continue
        if path.suffix.lower() not in _ARTIFACT_PARTITION_SUFFIX_KIND:
            continue
        if _workspace_artifact_candidate_is_auxiliary(path):
            continue
        key = _artifact_path_key(path.name)
        if key:
            candidates.append(key)
    candidates.sort(
        key=lambda value: (
            _ARTIFACT_PARTITION_PRIORITY.get(_artifact_kind_for_path(value), 999),
            value,
        )
    )
    return candidates


def _generic_artifact_partitions(
    *,
    task: "CodingTask",
    max_partitions: int,
    repair_brief: str = "",
) -> tuple[list[ArtifactPartition], dict[str, Any]]:
    if _task_in_benchmark_mode(task) or _repair_brief_requests_compact_replan(repair_brief):
        return [], {}
    if not _task_requests_existing_artifact_partition(task, repair_brief):
        return [], {}

    paths: list[str] = []
    for path in [
        *_artifact_paths_from_context(task),
        *_explicit_artifact_paths_from_text(_task_artifact_text(task, repair_brief)),
        *_workspace_artifact_candidates(task),
    ]:
        key = _artifact_path_key(path)
        if key and key not in paths:
            paths.append(key)
    artifact_text = _task_artifact_text(task, repair_brief)
    blocked_paths = _artifact_paths_blocked_for_mutation(artifact_text)
    only_paths = _artifact_paths_allowed_as_only_mutations(artifact_text)
    if blocked_paths:
        paths = [path for path in paths if path not in blocked_paths]
    if only_paths:
        paths = [path for path in paths if path in only_paths]
    if len(paths) < 2:
        return [], {}

    partitions: list[ArtifactPartition] = []
    for index, path in enumerate(paths[:max_partitions], start=1):
        partitions.append(
            ArtifactPartition(
                partition_id=f"artifact:{index}:{path}",
                artifact_paths=[path],
                artifact_kind=_artifact_kind_for_path(path),
                owner_id=f"worker-{index}",
                expected_quality_gain=1.0,
                expected_latency_seconds=30.0,
                coordination_cost=0.05,
                metadata={"source": "coding_task_artifact_partition"},
            )
        )
    admissions = evaluate_artifact_partition_admission(
        partitions,
        max_parallel=max_partitions,
        min_parallel_score=0.0,
    )
    admitted_ids = {item.partition_id for item in admissions if item.admitted}
    admitted_partitions = [
        partition for partition in partitions if partition.partition_id in admitted_ids
    ]
    if len(admitted_partitions) < 2:
        return [], {
            "admissions": [
                admission.model_dump(mode="json", exclude_none=True)
                for admission in admissions
            ],
            "selection_reason": "fewer_than_two_admitted_partitions",
        }

    total_quality = sum(
        partition.expected_quality_gain
        if partition.expected_quality_gain is not None
        else 1.0
        for partition in admitted_partitions
    )
    latencies = [
        partition.expected_latency_seconds
        if partition.expected_latency_seconds is not None
        else 30.0
        for partition in admitted_partitions
    ]
    average_rework_risk = sum(
        partition.rework_risk + partition.conflict_risk
        for partition in admitted_partitions
    ) / len(admitted_partitions)
    proposals = [
        SchedulingProposal(
            action=SchedulerAction.SERIAL,
            target_task_id="artifact_partitions:serial",
            reason="Run the artifact work as one bounded worker when parallel overhead is not worth it.",
            required_capacity=1,
            expected_value=total_quality,
            latency_cost=0.01 * sum(latencies),
            rework_risk=min(1.0, average_rework_risk),
            material_yield_probability=0.65,
            metadata={"source": "coding_task_artifact_partition"},
        ),
        SchedulingProposal(
            action=SchedulerAction.PARALLEL,
            target_task_id="artifact_partitions:parallel",
            reason="Run non-overlapping artifact-owner lanes to reduce the slowest path.",
            required_capacity=len(admitted_partitions),
            expected_value=total_quality,
            latency_cost=0.01 * max(latencies),
            rework_risk=min(1.0, average_rework_risk + 0.05),
            material_yield_probability=0.75,
            metadata={"source": "coding_task_artifact_partition"},
        ),
    ]
    selection = select_scheduler_proposal(
        proposals,
        state=SchedulerGuardrailState(
            available_capacity=max_partitions,
            max_capacity=max_partitions,
            audit_log_enabled=True,
            safety_envelope="bounded_task_dispatch",
        ),
    )
    decision = selection.model_dump(mode="json", exclude_none=True)
    decision["admissions"] = [
        admission.model_dump(mode="json", exclude_none=True)
        for admission in admissions
    ]
    if selection.selected_action != SchedulerAction.PARALLEL:
        return [], decision
    return admitted_partitions, decision


def _artifact_partition_worker_briefs(
    *,
    task: "CodingTask",
    partitions: Sequence[ArtifactPartition],
) -> list[str]:
    objective = _clean_text(task.objective)
    briefs: list[str] = []
    for partition in partitions:
        path = partition.artifact_paths[0] if partition.artifact_paths else partition.partition_id
        kind = _clean_text(partition.artifact_kind) or "artifact"
        briefs.append(
            (
                f"EXCLUSIVE WRITE OWNER: {path}. This is a scheduler artifact partition of kind `{kind}`. "
                "Inspect only the local context required for this artifact, then materialize the bounded change "
                "for this artifact when the objective requires a write. Do not edit any other artifact path. "
                "Return candidate_fragment, change_summary, target_files, test_plan, and risks for this owned "
                "artifact only. The organism-level aggregator handles cross-artifact merge, global modified-file "
                "listing, read-back confirmation, and final delivery. "
                f"Objective: {objective}"
            )
        )
    return briefs


def _exclusive_write_owner_path_from_brief(brief: str) -> str:
    match = re.search(
        r"exclusive\s+write\s+owner\s*:\s*([^\s,;:]+)",
        _clean_text(brief),
        flags=re.IGNORECASE,
    )
    return match.group(1).rstrip(".") if match else ""


def _repair_brief_requests_compact_replan(repair_brief: str) -> bool:
    text = _clean_text(repair_brief).lower()
    if not text:
        return False
    markers = (
        "no material bounded candidate",
        "no material mutation evidence",
        "failed to materialize",
        "provider timed out before the coding worker produced a bounded candidate",
        "re-plan with a smaller",
        "prefer 1 worker",
    )
    return any(marker in text for marker in markers)


def _aggregation_failure_repair_brief(
    error: str,
    *,
    parallel_owner_plan: bool,
) -> str:
    base = _clean_text(error)
    guidance = (
        "Re-plan with a smaller write-first attempt. Prefer 1 worker unless another split has a concrete immediate first write."
        if parallel_owner_plan
        else "Re-plan with a smaller write-first attempt and make the first file edit happen in the next model round."
    )
    return (
        f"{base} {guidance} Keep discovery narrow, avoid rediscovering unrelated files, and return a material bounded candidate."
    ).strip()


def _has_material_candidate_output(payload: dict[str, Any]) -> bool:
    if _is_timeout_or_blocked_no_output_candidate(payload):
        return False
    return _has_material_structured_outcome(
        payload,
        id_field="candidate_id",
        summary_field="change_summary",
        target_refs_field="target_files",
        validation_steps_field="test_plan",
        risks_field="risks",
        effect_field="workspace_effect",
    )


def _is_timeout_or_blocked_no_output_candidate(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    candidate = _candidate_view(payload)
    if candidate.target_refs:
        return False
    text = " ".join(
        _clean_text(value).lower()
        for value in (
            candidate.candidate_id,
            candidate.change_summary,
            " ".join(candidate.validation_steps),
            " ".join(candidate.risks),
        )
        if _clean_text(value)
    )
    if not text:
        return False
    blocked_markers = (
        "provider timed out",
        "provider completion timed out",
        "timed out before",
        "no code candidate was produced",
        "did not leave a materialized workspace patch",
        "no material bounded candidate",
        "before the coding worker produced a bounded candidate",
    )
    return any(marker in text for marker in blocked_markers)


def _salvage_candidate_output(execution: OrganExecution | None) -> dict[str, Any]:
    if execution is None:
        return {}
    candidates: list[dict[str, Any]] = []
    direct_outputs = dict(execution.result.outputs or {})
    if direct_outputs:
        candidates.append(direct_outputs)
    raw_outputs = execution.result.metadata.get("raw_outputs")
    if raw_outputs:
        candidates.append(_parse_payload(dict(raw_outputs) if isinstance(raw_outputs, dict) else {"result": raw_outputs}))
    if execution.lead_execution is not None:
        candidates.append(_parse_payload(dict(execution.lead_execution.result.outputs or {})))
    for candidate in candidates:
        if _has_material_candidate_output(candidate):
            return _candidate_payload(candidate)
    return {}


class CodingTask(BaseModel):
    """Bounded task contract for the coding organism."""

    task_id: str
    objective: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    research_findings: list[str] = Field(default_factory=list)
    repair_brief: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    session_context: dict[str, Any] = Field(default_factory=dict)
    hard_constraints: list[str] = Field(default_factory=list)
    soft_constraints: list[str] = Field(default_factory=list)


class CodingOrchestratorPlan(BaseModel):
    """Normalized orchestrator plan for one coding attempt."""

    public_response: str = ""
    worker_count: int = Field(default=1, ge=1)
    worker_briefs: list[str] = Field(default_factory=list)
    aggregation_focus: str = (
        "Merge the worker outputs into one bounded coding candidate with explicit files and focused validation."
    )
    validator_focus: str = "Score the aggregated candidate against the acceptance criteria and emit a repair brief when needed."
    pass_threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    artifact_owner_paths: list[str] = Field(default_factory=list)
    scheduler_policy_source: str = ""
    scheduler_decision: dict[str, Any] = Field(default_factory=dict)


class CodingOrganism(BaseModel):
    """Composed coding organism with explicit orchestrator, worker pool, aggregator, and validator roles."""

    organism_id: str
    base_id: str
    orchestrator_address: CellAddress
    orchestrator_worker: WorkerDefinition
    worker_role: str = "coding_worker"
    worker_instruction: str = (
        "Produce one bounded coding contribution for the assigned brief. Prefer the most specific structured tool "
        "available, avoid redundant repo-wide discovery, and inspect only what the brief needs. Return "
        "candidate_fragment, change_summary, target_files, test_plan, and risks. When you are the only coding "
        "worker and write-capable tools are enabled, materialize the smallest correct patch directly instead of "
        "deferring it to a later stage. When multiple coding workers are running in parallel, stay read-only and "
        "return a concrete candidate_fragment for later reconciliation instead of mutating the shared workspace, "
        "unless your brief explicitly names you as an exclusive write owner for specific files. In that case, "
        "materialize only those owned files and do not edit any other file. "
        "Do not pass write-like arguments to read tools, and do not repeatedly probe paths that do not exist just "
        "because you intend to create them. If the workspace is empty or the brief requires new files, inspect "
        "only enough context to confirm that and then either materialize the first bounded slice directly when you "
        "own the write path or return a concrete candidate_fragment for later materialization. Do not turn an "
        "empty-workspace greenfield brief into generic architecture or best-practice research. Use external search "
        "only when the brief explicitly depends on current external facts, library documentation, or version-"
        "specific behavior; otherwise finalize the bounded candidate from local context. If inspection shows the "
        "workspace already satisfies the brief, say 'No code changes required' explicitly in change_summary."
    )
    worker_model: str | None = None
    worker_tool_ids: list[str] = Field(default_factory=list)
    parallel_worker_tool_ids: list[str] = Field(default_factory=list)
    aggregator_organ: OrganPattern
    validator_organ: OrganPattern
    compact_validator_organ: OrganPattern | None = None
    default_worker_count: int = Field(default=1, ge=1)
    max_worker_count: int = Field(default=4, ge=1)
    max_repair_rounds: int = Field(default=1, ge=0)
    delivery_pass_tolerance: float = Field(default=0.03, ge=0.0, le=0.25)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CodingOrganismResult(BaseModel):
    """Normalized coding-organism outcome."""

    status: Literal["completed", "failed"]
    final_output: dict[str, Any] = Field(default_factory=dict)
    selected_attempt: int | None = None
    validation_scores: list[float] = Field(default_factory=list)
    improved_via_repair: bool = False
    error: str | None = None
    observability: OrganismObservability
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class CodingOrganismExecution:
    """Recorded runtime for the full coding organism."""

    organism: CodingOrganism
    task: CodingTask
    orchestrator_runs: list[HandoffExecution] = field(default_factory=list)
    worker_pool_executions: list[TissueExecution] = field(default_factory=list)
    aggregation_executions: list[OrganExecution] = field(default_factory=list)
    validation_executions: list[OrganExecution] = field(default_factory=list)
    trace_log: CrossCellTraceLog | None = None
    result: CodingOrganismResult | None = None

def _orchestrator_output_contract() -> OutputContract:
    return OutputContract(
        definition_of_done=(
            "Return the next bounded coding plan: a concise public_response, worker count, worker briefs, aggregation focus, validator focus, and pass threshold."
        ),
        expected_return_shape=json.dumps(
            {
                "public_response": "<optional>",
                "worker_count": "<required>",
                "worker_briefs": "<required>",
                "aggregation_focus": "<required>",
                "validator_focus": "<required>",
                "pass_threshold": "<required>",
            },
            sort_keys=True,
        ),
    )


def _worker_output_contract() -> OutputContract:
    return OutputContract(
        definition_of_done="Return one bounded coding contribution for the assigned brief.",
        expected_return_shape=json.dumps(
            {
                "candidate_fragment": "<required>",
                "change_summary": "<required>",
                "target_files": "<required>",
                "test_plan": "<required>",
                "risks": "<required>",
            },
            sort_keys=True,
        ),
    )


def _user_address(organism_id: str) -> CellAddress:
    return CellAddress(cell_id="user.request", organism_id=organism_id)


def _trace(
    root_task_id: str,
    organism_id: str,
    *,
    trace_id: str | None = None,
) -> SignalTrace:
    return SignalTrace(
        trace_id=trace_id or new_trace_id(),
        root_task_id=root_task_id,
        lineage=[f"organism:{organism_id}"],
    )


def _record(
    *,
    stage: str,
    attempt: int,
    packet: CellHandoffPacket,
    status: str,
    summary: str,
    organ_id: str | None = None,
    score: float | None = None,
    output_keys: list[str] | None = None,
) -> OrganismStageRecord:
    return OrganismStageRecord(
        stage=stage,
        attempt=attempt,
        organ_id=organ_id,
        packet_id=packet.packet_id,
        sender_cell_id=packet.sender.cell_id,
        recipient_cell_id=packet.recipient.cell_id,
        accountable_cell_id=packet.recipient.cell_id,
        status=status,
        summary=summary,
        score=score,
        output_keys=list(output_keys or []),
    )


def _child_hooks(parent_packet: CellHandoffPacket) -> ContinuationHooks:
    hooks = parent_packet.continuation_hooks.model_copy(deep=True)
    hooks.reply_to_cell_id = parent_packet.recipient.cell_id
    hooks.resume_from_packet_id = parent_packet.packet_id
    return hooks


def _child_authority_limits(
    parent_packet: CellHandoffPacket,
    *,
    authority: WorkerAuthority,
) -> CellAuthorityLimits:
    payload = parent_packet.authority_limits.model_dump(mode="json", exclude_none=True)
    payload["acting_authority"] = authority
    return CellAuthorityLimits.model_validate(payload)


def _child_packet(
    *,
    sender: CellAddress,
    recipient: CellAddress,
    parent_packet: CellHandoffPacket,
    parent_signal_id: str,
    lineage_suffix: str,
    task_id: str,
    instruction: str,
    scope: str,
    hard_constraints: list[str],
    soft_constraints: list[str],
    input_payload: dict[str, Any],
    evidence_refs: list[EvidenceRef],
    output_contract: OutputContract,
    authority: WorkerAuthority,
    metadata: dict[str, Any],
) -> CellHandoffPacket:
    return CellHandoffPacket(
        trace=SignalTrace(
            trace_id=parent_packet.trace.trace_id,
            root_task_id=parent_packet.trace.root_task_id or parent_packet.task.task_id,
            parent_packet_id=parent_packet.packet_id,
            parent_signal_id=parent_signal_id,
            lineage=[*parent_packet.trace.lineage, lineage_suffix],
        ),
        sender=sender.model_copy(deep=True),
        recipient=recipient.model_copy(deep=True),
        task=HandoffTask(
            task_id=task_id,
            instruction=instruction,
            scope=scope,
            hard_constraints=list(hard_constraints),
            soft_constraints=list(soft_constraints),
            input_payload=dict(input_payload),
        ),
        evidence_refs=[ref.model_copy(deep=True) for ref in evidence_refs],
        output_contract=output_contract.model_copy(deep=True),
        budget_limits=parent_packet.budget_limits.model_copy(deep=True),
        authority_limits=_child_authority_limits(parent_packet, authority=authority),
        continuation=(
            parent_packet.continuation.model_copy(deep=True)
            if parent_packet.continuation is not None
            else None
        ),
        continuation_hooks=_child_hooks(parent_packet),
        metadata={
            **dict(parent_packet.metadata),
            **dict(metadata),
        },
    )


def _root_orchestrator_packet(
    *,
    organism: CodingOrganism,
    task: CodingTask,
    trace_id: str | None = None,
) -> CellHandoffPacket:
    return CellHandoffPacket(
        trace=_trace(task.task_id, organism.organism_id, trace_id=trace_id),
        sender=_user_address(organism.organism_id),
        recipient=organism.orchestrator_address.model_copy(deep=True),
        task=HandoffTask(
            task_id=f"{task.task_id}:orchestrate:1",
            instruction=(
                "Plan the next coding attempt. First analyze the user intent and write one concise formal "
                "public_response for the user-facing console that states what you understand and what you will do next. "
                "Then choose how many workers to run, define distinct briefs for them, and set the aggregation and validation focus. "
                "Default to 1 worker unless multiple genuinely distinct, non-overlapping paths are materially useful. "
                "If you choose more than 1 worker, provide the same number of distinct briefs."
            ),
            scope="coding-organism.orchestrate",
            hard_constraints=list(task.hard_constraints),
            soft_constraints=list(task.soft_constraints),
            input_payload={
                "objective": task.objective,
                "acceptance_criteria": list(task.acceptance_criteria),
                "research_findings": list(task.research_findings),
                "repair_brief": task.repair_brief,
                "session_context": dict(task.session_context),
                "attempt": 1,
                "repair_history": [],
            },
        ),
        evidence_refs=[ref.model_copy(deep=True) for ref in task.evidence_refs],
        output_contract=_orchestrator_output_contract(),
        budget_limits=CellBudgetLimits(max_completion_rounds=1),
        authority_limits=CellAuthorityLimits(
            acting_authority=WorkerAuthority.LEAD,
            max_spawned_cells=0,
        ),
        metadata={
            "organism_id": organism.organism_id,
            "organism_stage": "orchestration",
            **_runtime_policy_metadata(task),
        },
    )


def _repair_orchestrator_packet(
    *,
    organism: CodingOrganism,
    task: CodingTask,
    attempt: int,
    parent_packet: CellHandoffPacket,
    parent_signal_id: str,
    repair_history: list[dict[str, Any]],
    previous_validation: dict[str, Any],
    repair_brief: str,
) -> CellHandoffPacket:
    return _child_packet(
        sender=parent_packet.recipient,
        recipient=organism.orchestrator_address,
        parent_packet=parent_packet,
        parent_signal_id=parent_signal_id,
        lineage_suffix="coordinator:orchestrator",
        task_id=f"{task.task_id}:orchestrate:{attempt}",
        instruction=(
            "Plan the next coding attempt after validator feedback. First analyze the updated user intent and write "
            "one concise formal public_response for the user-facing console that states what you understand and what "
            "you will do next. Then choose worker_count, worker_briefs, aggregation_focus, validator_focus, and pass_threshold. "
            "Default to 1 worker unless multiple genuinely distinct, non-overlapping paths are materially useful. "
            "If you choose more than 1 worker, provide the same number of distinct briefs."
        ),
        scope="coding-organism.orchestrate",
        hard_constraints=list(task.hard_constraints),
        soft_constraints=list(task.soft_constraints),
        input_payload={
            "objective": task.objective,
            "acceptance_criteria": list(task.acceptance_criteria),
            "research_findings": list(task.research_findings),
            "repair_brief": repair_brief,
            "session_context": dict(task.session_context),
            "attempt": attempt,
            "repair_history": list(repair_history),
            "previous_validation": dict(previous_validation),
        },
        evidence_refs=task.evidence_refs,
        output_contract=_orchestrator_output_contract(),
        authority=WorkerAuthority.LEAD,
        metadata={"organism_id": organism.organism_id, "organism_stage": "orchestration"},
    )


def _benchmark_validator_focus_addendum(task: CodingTask) -> str:
    benchmark_context = {}
    if isinstance(task.session_context, dict):
        raw = task.session_context.get("benchmark_context")
        if isinstance(raw, dict):
            benchmark_context = raw
    if not benchmark_context:
        return ""
    return (
        " For benchmark tasks, reject candidates that miss the exact checked-out "
        "branch contract for warning/error ids, message text, severity, visible "
        "stdout or warning side effects, deprecation behavior, or protected "
        "surrounding behavior, even if the general fix direction seems plausible. "
        "Treat issue examples and repro snippets as illustrative rather than "
        "exhaustive. Reject candidates that fix one literal example while leaving "
        "adjacent checked-out tests, helpers, parametrizations, or symmetric "
        "parser/read/write/round-trip paths inconsistent with the same public "
        "contract."
    )


def _normalize_worker_briefs(
    *,
    worker_count: int,
    worker_briefs: list[str],
    objective: str,
) -> list[str]:
    normalized = [str(brief).strip() for brief in worker_briefs if str(brief).strip()]
    while len(normalized) < worker_count:
        normalized.append(
            f"Investigate one bounded candidate path for objective: {objective}"
        )
    return normalized[:worker_count]


def _distinct_worker_briefs(worker_briefs: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for brief in worker_briefs:
        text = _clean_text(brief)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)
    return ordered


def _normalize_orchestrator_plan(
    *,
    outputs: dict[str, Any],
    organism: CodingOrganism,
    task: CodingTask,
    repair_brief: str = "",
) -> CodingOrchestratorPlan:
    payload = _parse_payload(outputs)
    worker_count = _planned_worker_count(payload=payload, organism=organism)
    try:
        plan = CodingOrchestratorPlan.model_validate(payload)
    except Exception:
        plan = CodingOrchestratorPlan(worker_count=worker_count)
    artifact_partitions, scheduler_decision = _generic_artifact_partitions(
        task=task,
        max_partitions=int(organism.max_worker_count),
        repair_brief=repair_brief,
    )
    if artifact_partitions:
        fanout_briefs = _artifact_partition_worker_briefs(
            task=task,
            partitions=artifact_partitions,
        )
        owner_paths = [
            partition.artifact_paths[0]
            for partition in artifact_partitions
            if partition.artifact_paths
        ]
        worker_count = min(len(fanout_briefs), int(organism.max_worker_count))
        plan = plan.model_copy(
            update={
                "worker_count": worker_count,
                "worker_briefs": fanout_briefs[:worker_count],
                "aggregation_focus": (
                    _clean_text(plan.aggregation_focus)
                    + " Merge the parallel artifact-owner fragments into one small concrete patch from read-back "
                    "and mutation evidence, and avoid broad rereads once the target artifacts are known."
                ).strip(),
                "artifact_owner_paths": owner_paths[:worker_count],
                "scheduler_policy_source": "generic_artifact_partition",
                "scheduler_decision": dict(scheduler_decision),
            }
        )
    distinct_briefs = _distinct_worker_briefs(list(plan.worker_briefs))
    if worker_count > 1 and len(distinct_briefs) < worker_count:
        worker_count = max(1, len(distinct_briefs))
    worker_briefs = _normalize_worker_briefs(
        worker_count=worker_count,
        worker_briefs=distinct_briefs,
        objective=_clean_text(repair_brief) or task.objective,
    )
    validator_focus = _clean_text(plan.validator_focus)
    benchmark_validator_focus = _benchmark_validator_focus_addendum(task)
    if benchmark_validator_focus and benchmark_validator_focus not in validator_focus:
        validator_focus = (validator_focus + benchmark_validator_focus).strip()
    return plan.model_copy(
        update={
            "worker_count": worker_count,
            "worker_briefs": worker_briefs,
            "artifact_owner_paths": list(plan.artifact_owner_paths)[:worker_count],
            "validator_focus": validator_focus or plan.validator_focus,
        }
    )


def _worker_results_have_material_for_aggregation(member_results: dict[str, Any]) -> bool:
    for raw_payload in member_results.values():
        payload = _parse_payload(dict(raw_payload or {}))
        if _has_material_candidate_output(payload):
            return True
    return False


def _worker_material_yield_signal(
    *,
    member_id: str,
    owner_path: str = "",
    payload: dict[str, Any] | None = None,
    executed_tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    candidate_payload = dict(payload or {})
    candidate = _candidate_view(candidate_payload)
    target_files = list(candidate.target_files)
    mutation_paths = _successful_file_mutation_paths(executed_tools)
    candidate_material = _has_material_candidate_output(candidate_payload)
    matching_mutation_evidence = (
        _candidate_has_matching_mutation_evidence(
            candidate_payload,
            executed_tools=executed_tools,
        )
        if candidate_material
        else False
    )
    workspace_effect = _candidate_workspace_effect(candidate)
    materialized_artifact = bool(
        candidate_material
        and (
            matching_mutation_evidence
            or workspace_effect == "verified"
            or not _mutation_claim_requires_evidence(candidate)
        )
    )
    no_material_reason = ""
    if not candidate_material:
        no_material_reason = (
            "timeout_or_blocked_no_output"
            if _is_timeout_or_blocked_no_output_candidate(candidate_payload)
            else "no_material_candidate"
        )
    elif not materialized_artifact:
        no_material_reason = "missing_mutation_evidence"

    return {
        "member_id": member_id,
        "owner_path": _artifact_path_key(owner_path),
        "candidate_id": candidate.candidate_id,
        "target_files": target_files,
        "workspace_effect": workspace_effect or "",
        "candidate_material": candidate_material,
        "materialized_artifact": materialized_artifact,
        "matching_mutation_evidence": matching_mutation_evidence,
        "mutation_paths": mutation_paths,
        "no_material_reason": no_material_reason,
    }


def _worker_material_yield_metrics(
    *,
    member_results: dict[str, Any],
    worker_execution: TissueExecution,
) -> dict[str, Any]:
    execution_by_member = _worker_execution_map(worker_execution)
    owner_by_member = {
        member.member_id: _artifact_path_key(
            dict(member.metadata or {}).get("exclusive_write_owner_path")
        )
        for member in worker_execution.pattern.members
    }
    lane_signals: list[dict[str, Any]] = []
    for member_id in sorted(member_results):
        payload = _parse_payload(dict(member_results.get(member_id) or {}))
        lane_signals.append(
            _worker_material_yield_signal(
                member_id=member_id,
                owner_path=owner_by_member.get(member_id, ""),
                payload=payload,
                executed_tools=_executed_tools_from_handoff_execution(
                    execution_by_member.get(member_id)
                ),
            )
        )

    total_lanes = len(lane_signals)
    material_candidate_count = sum(1 for item in lane_signals if item["candidate_material"])
    materialized_artifact_count = sum(1 for item in lane_signals if item["materialized_artifact"])
    mutation_evidence_count = sum(1 for item in lane_signals if item["matching_mutation_evidence"])
    no_material_reasons: dict[str, int] = {}
    for item in lane_signals:
        reason = _clean_text(item.get("no_material_reason"))
        if reason:
            no_material_reasons[reason] = no_material_reasons.get(reason, 0) + 1

    return {
        "total_lanes": total_lanes,
        "material_candidate_count": material_candidate_count,
        "materialized_artifact_count": materialized_artifact_count,
        "mutation_evidence_count": mutation_evidence_count,
        "material_candidate_rate": (
            material_candidate_count / total_lanes if total_lanes else 0.0
        ),
        "materialized_artifact_rate": (
            materialized_artifact_count / total_lanes if total_lanes else 0.0
        ),
        "no_material_reasons": no_material_reasons,
        "lanes": lane_signals,
    }


def _tool_context_capsules(
    executed_tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    capsules: list[dict[str, Any]] = []
    for tool in executed_tools or []:
        for capsule in tool.get("context_capsules") or []:
            if isinstance(capsule, dict):
                capsules.append(dict(capsule))
    return capsules


def _tool_readiness_signals(
    executed_tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for tool in executed_tools or []:
        signal = tool.get("readiness_signal")
        if isinstance(signal, dict):
            signals.append(dict(signal))
    return signals


def _capsules_with_owner_unlock_aliases(
    *,
    capsules: list[dict[str, Any]],
    owner_path: str,
    mutation_paths: Sequence[str],
) -> list[dict[str, Any]]:
    owner_key = _artifact_path_key(owner_path)
    if not owner_key:
        return [dict(capsule) for capsule in capsules]
    alias = f"file_changed:{owner_key}"
    matching_mutation = any(_paths_overlap(owner_key, path) for path in mutation_paths)
    enhanced: list[dict[str, Any]] = []
    for capsule in capsules:
        copied = dict(capsule)
        unlocks = [
            _clean_text(unlock)
            for unlock in copied.get("unlocks") or []
            if _clean_text(unlock)
        ]
        if matching_mutation or any(
            unlock.startswith("file_changed:")
            and _paths_overlap(owner_key, unlock.split(":", 1)[1])
            for unlock in unlocks
        ):
            if alias not in unlocks:
                unlocks.append(alias)
        copied["unlocks"] = unlocks
        enhanced.append(copied)
    return enhanced


def _artifact_readiness_lane(
    *,
    plan: CodingOrchestratorPlan,
    member_id: str,
    owner_path: str,
    payload: dict[str, Any],
    executed_tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    candidate = _candidate_view(payload)
    mutation_paths = _successful_file_mutation_paths(executed_tools)
    context_capsules = _tool_context_capsules(executed_tools)
    readiness_signals = _tool_readiness_signals(executed_tools)
    owner_mutation_ready = bool(
        owner_path
        and any(_paths_overlap(owner_path, mutation_path) for mutation_path in mutation_paths)
    )
    scheduler_readiness: dict[str, Any] = {}
    scheduler_ready = False
    missing_unlocks: list[str] = []
    blockers: list[str] = []
    if owner_path:
        readiness_task = SchedulerTask(
            task_id=f"{member_id}:owner:{owner_path}",
            title=f"Owner artifact ready: {owner_path}",
            required_artifact_kinds=["implementation_delta"],
            required_unlocks=[f"file_changed:{owner_path}"],
            metadata={
                "member_id": member_id,
                "owner_path": owner_path,
                "scheduler_policy_source": _clean_text(plan.scheduler_policy_source),
            },
        )
        evaluation = evaluate_task_readiness_from_capsules(
            readiness_task,
            _capsules_with_owner_unlock_aliases(
                capsules=context_capsules,
                owner_path=owner_path,
                mutation_paths=mutation_paths,
            ),
            readiness_signals=readiness_signals,
        )
        scheduler_ready = evaluation.ready
        missing_unlocks = list(evaluation.missing_unlocks)
        blockers = list(evaluation.blockers)
        scheduler_readiness = evaluation.model_dump(
            mode="json",
            exclude_none=True,
        )

    ready_for_downstream = bool(scheduler_ready or owner_mutation_ready)
    if scheduler_ready:
        readiness_source = "capsule_scheduler"
    elif owner_mutation_ready:
        readiness_source = "mutation_evidence_fallback"
    elif owner_path:
        readiness_source = "blocked"
    elif _has_material_candidate_output(payload):
        readiness_source = "candidate_only"
    else:
        readiness_source = "none"

    return {
        "member_id": member_id,
        "owner_path": owner_path,
        "ready_for_downstream": ready_for_downstream,
        "readiness_source": readiness_source,
        "target_files": list(candidate.target_files),
        "candidate_material": _has_material_candidate_output(payload),
        "mutation_paths": mutation_paths,
        "capsule_count": len(context_capsules),
        "readiness_signal_count": len(readiness_signals),
        "missing_unlocks": missing_unlocks,
        "blockers": blockers,
        "scheduler_readiness": scheduler_readiness,
    }


def _artifact_readiness_summary(
    *,
    plan: CodingOrchestratorPlan,
    lanes: Sequence[dict[str, Any]],
    owner_paths: Sequence[str],
) -> dict[str, Any]:
    ready_owner_paths: list[str] = []
    blocked_owner_paths: list[str] = []
    for lane in lanes:
        owner_path = _artifact_path_key(lane.get("owner_path"))
        if not owner_path:
            continue
        if lane.get("ready_for_downstream"):
            if owner_path not in ready_owner_paths:
                ready_owner_paths.append(owner_path)
        elif owner_path not in blocked_owner_paths:
            blocked_owner_paths.append(owner_path)

    missing_owner_paths = [
        owner_path for owner_path in owner_paths if owner_path not in ready_owner_paths
    ]
    return {
        "scheduler_policy_source": _clean_text(plan.scheduler_policy_source),
        "owner_path_count": len(owner_paths),
        "ready_owner_paths": ready_owner_paths,
        "blocked_owner_paths": blocked_owner_paths,
        "missing_owner_paths": missing_owner_paths,
        "all_owner_paths_ready": bool(owner_paths) and not missing_owner_paths,
        "lanes": list(lanes),
    }


def _artifact_readiness_ledger(
    *,
    plan: CodingOrchestratorPlan,
    member_results: dict[str, Any],
    worker_execution: TissueExecution,
) -> dict[str, Any]:
    execution_by_member = _worker_execution_map(worker_execution)
    owner_by_member = {
        member.member_id: _artifact_path_key(
            dict(member.metadata or {}).get("exclusive_write_owner_path")
        )
        for member in worker_execution.pattern.members
    }

    lanes: list[dict[str, Any]] = []
    for member_id in sorted(member_results):
        payload = _parse_payload(dict(member_results.get(member_id) or {}))
        owner_path = owner_by_member.get(member_id, "")
        executed_tools = _executed_tools_from_handoff_execution(
            execution_by_member.get(member_id)
        )
        lanes.append(
            _artifact_readiness_lane(
                plan=plan,
                member_id=member_id,
                owner_path=owner_path,
                payload=payload,
                executed_tools=executed_tools,
            )
        )

    owner_paths = _exclusive_owner_paths(worker_execution)
    return _artifact_readiness_summary(
        plan=plan,
        lanes=lanes,
        owner_paths=owner_paths,
    )


def _partial_aggregation_fragment(
    *,
    member_id: str,
    owner_path: str,
    payload: dict[str, Any],
    executed_tools: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if not _has_material_candidate_output(payload):
        return None
    if not _candidate_has_matching_mutation_evidence(
        payload,
        executed_tools=executed_tools,
    ):
        return None
    mutation_paths = _successful_file_mutation_paths(executed_tools)
    return {
        "member_id": member_id,
        "owner_path": _artifact_path_key(owner_path),
        "mutation_paths": mutation_paths,
        "payload": _candidate_payload(payload),
    }


def _partial_aggregation_progress(
    *,
    fragments: Sequence[dict[str, Any]],
    owner_paths: Sequence[str],
) -> dict[str, Any]:
    ready_owner_paths: list[str] = []
    for fragment in fragments:
        owner_path = _artifact_path_key(fragment.get("owner_path"))
        if not owner_path:
            continue
        if owner_path not in ready_owner_paths:
            ready_owner_paths.append(owner_path)
    missing_owner_paths = [
        owner_path for owner_path in owner_paths if owner_path not in ready_owner_paths
    ]
    return {
        "fragment_count": len(fragments),
        "owner_path_count": len(owner_paths),
        "ready_owner_paths": ready_owner_paths,
        "missing_owner_paths": missing_owner_paths,
        "all_owner_paths_ready": bool(owner_paths) and not missing_owner_paths,
    }


def _incremental_worker_merge_candidate(
    *,
    fragments: Sequence[dict[str, Any]],
    owner_paths: Sequence[str],
    attempt: int,
) -> dict[str, Any] | None:
    if not fragments:
        return None
    progress = _partial_aggregation_progress(
        fragments=fragments,
        owner_paths=owner_paths,
    )
    if owner_paths and not progress["all_owner_paths_ready"]:
        return None

    material_payloads = [
        dict(fragment.get("payload") or {})
        for fragment in fragments
        if isinstance(fragment.get("payload"), dict)
    ]
    if not material_payloads:
        return None

    owned_mutation_paths = [
        _artifact_path_key(fragment.get("owner_path"))
        for fragment in fragments
        if _artifact_path_key(fragment.get("owner_path"))
    ]
    target_files: list[str] = []
    test_plan: list[str] = []
    risks: list[str] = []
    summaries: list[str] = []
    _extend_unique_candidate_paths(
        target_files,
        owned_mutation_paths,
        owner_paths=owner_paths,
    )
    for payload in material_payloads:
        _extend_unique_candidate_paths(
            target_files,
            payload.get("target_files"),
            owner_paths=owner_paths,
        )
        _extend_unique_text(test_plan, payload.get("test_plan"))
        _extend_unique_text(risks, payload.get("risks"))
        summary = _clean_text(payload.get("change_summary"))
        if summary and summary not in summaries:
            summaries.append(summary)
    if not target_files:
        return None

    ordered_targets = [
        owner_path
        for owner_path in owner_paths
        if any(_paths_overlap(owner_path, path) for path in target_files)
    ]
    for path in target_files:
        if path not in ordered_targets:
            ordered_targets.append(path)
    candidate_fragment = _merge_candidate_fragments(material_payloads)
    return _candidate_payload(
        {
            "candidate_id": f"candidate-{attempt}-worker-merge",
            "change_summary": (
                "Merged worker material changes: " + " ".join(summaries)
                if summaries
                else "Merged worker material changes across covered owner lanes."
            ),
            "target_files": ordered_targets,
            "test_plan": test_plan,
            "risks": risks,
            "workspace_effect": "modified",
            **({"candidate_fragment": candidate_fragment} if candidate_fragment is not None else {}),
            "synthesized_from_incremental_reducer": True,
        }
    )


def _exclusive_owner_paths(worker_execution: TissueExecution) -> list[str]:
    paths: list[str] = []
    for member in worker_execution.pattern.members:
        owner_path = _candidate_path_key(
            dict(member.metadata or {}).get("exclusive_write_owner_path")
        )
        if owner_path and owner_path not in paths:
            paths.append(owner_path)
    return paths


def _material_worker_payloads_with_mutation_evidence(
    *,
    member_results: dict[str, Any],
    worker_execution: TissueExecution,
) -> list[dict[str, Any]]:
    execution_by_member = _worker_execution_map(worker_execution)
    payloads: list[dict[str, Any]] = []
    for member_id in sorted(member_results):
        payload = _parse_payload(dict(member_results.get(member_id) or {}))
        if not _has_material_candidate_output(payload):
            continue
        executed_tools = _executed_tools_from_handoff_execution(execution_by_member.get(member_id))
        if not _candidate_has_matching_mutation_evidence(
            payload,
            executed_tools=executed_tools,
        ):
            continue
        payloads.append(_candidate_payload(payload))
    return payloads


def _exclusive_owner_mutation_paths(worker_execution: TissueExecution) -> list[str]:
    paths: list[str] = []
    for member, execution in zip(
        worker_execution.pattern.members,
        worker_execution.member_executions,
        strict=True,
    ):
        owner_path = _candidate_path_key(
            dict(member.metadata or {}).get("exclusive_write_owner_path")
        )
        if not owner_path:
            continue
        executed_tools = _executed_tools_from_handoff_execution(execution)
        for mutation_path in _successful_file_mutation_paths(executed_tools):
            if _paths_overlap(owner_path, mutation_path) and owner_path not in paths:
                paths.append(owner_path)
    return paths


def _extend_unique_text(items: list[str], values: Any) -> None:
    for value in values or []:
        text = _clean_text(value)
        if text and text not in items:
            items.append(text)


def _owner_normalized_path(path: Any, *, owner_paths: Sequence[str]) -> str:
    normalized = _candidate_path_key(path)
    if not normalized:
        return ""
    for owner_path in owner_paths:
        if _paths_overlap(owner_path, normalized):
            return owner_path
    return normalized


def _extend_unique_candidate_paths(
    items: list[str],
    values: Any,
    *,
    owner_paths: Sequence[str],
) -> None:
    for value in values or []:
        path = _owner_normalized_path(value, owner_paths=owner_paths)
        if path and path not in items:
            items.append(path)


def _merge_candidate_fragments(payloads: Sequence[dict[str, Any]]) -> Any:
    fragments: dict[str, Any] = {}
    text_fragments: list[str] = []
    for payload in payloads:
        fragment = payload.get("candidate_fragment")
        if isinstance(fragment, dict):
            for key, value in fragment.items():
                text_key = _clean_text(key)
                if text_key and text_key not in fragments:
                    fragments[text_key] = value
            continue
        text = _clean_text(fragment)
        if text and text not in text_fragments:
            text_fragments.append(text)
    if fragments:
        if text_fragments:
            fragments["_notes"] = "\n\n".join(text_fragments)
        return fragments
    if text_fragments:
        return "\n\n".join(text_fragments)
    return None


def _copy_candidate_material_extras(
    candidate_payload: dict[str, Any],
    source_payload: dict[str, Any],
) -> None:
    for key in (
        "candidate_fragment",
        "readback_files",
        "synthesized_from_tool_evidence",
        "fallback_reason",
    ):
        if key in source_payload and key not in candidate_payload:
            candidate_payload[key] = source_payload[key]


def _missing_exclusive_owner_material_paths(
    *,
    member_results: dict[str, Any],
    worker_execution: TissueExecution,
) -> list[str]:
    owner_paths = _exclusive_owner_paths(worker_execution)
    if len(owner_paths) <= 1:
        return []
    material_payloads = _material_worker_payloads_with_mutation_evidence(
        member_results=member_results,
        worker_execution=worker_execution,
    )
    successful_paths: list[str] = []
    _extend_unique_candidate_paths(
        successful_paths,
        _exclusive_owner_mutation_paths(worker_execution),
        owner_paths=owner_paths,
    )
    for payload in material_payloads:
        _extend_unique_candidate_paths(
            successful_paths,
            payload.get("target_files"),
            owner_paths=owner_paths,
        )
    return [
        owner_path
        for owner_path in owner_paths
        if not any(_paths_overlap(owner_path, path) for path in successful_paths)
    ]


def _deterministic_worker_merge_candidate(
    *,
    member_results: dict[str, Any],
    worker_execution: TissueExecution,
    attempt: int,
) -> dict[str, Any] | None:
    owner_paths = _exclusive_owner_paths(worker_execution)
    material_payloads = _material_worker_payloads_with_mutation_evidence(
        member_results=member_results,
        worker_execution=worker_execution,
    )
    owned_mutation_paths = _exclusive_owner_mutation_paths(worker_execution)
    target_files: list[str] = []
    test_plan: list[str] = []
    risks: list[str] = []
    summaries: list[str] = []
    _extend_unique_candidate_paths(
        target_files,
        owned_mutation_paths,
        owner_paths=owner_paths,
    )
    for payload in material_payloads:
        _extend_unique_candidate_paths(
            target_files,
            payload.get("target_files"),
            owner_paths=owner_paths,
        )
        _extend_unique_text(test_plan, payload.get("test_plan"))
        _extend_unique_text(risks, payload.get("risks"))
        summary = _clean_text(payload.get("change_summary"))
        if summary and summary not in summaries:
            summaries.append(summary)
    if not target_files:
        return None
    candidate_fragment = _merge_candidate_fragments(material_payloads)
    if owner_paths:
        ordered_targets = [
            owner_path
            for owner_path in owner_paths
            if any(_paths_overlap(owner_path, path) for path in target_files)
        ]
        for path in target_files:
            if path not in ordered_targets:
                ordered_targets.append(path)
        target_files = ordered_targets
    change_summary = (
        "Merged worker material changes: " + " ".join(summaries)
        if summaries
        else "Merged worker material changes across covered owner lanes."
    )
    return _candidate_payload(
        {
            "candidate_id": f"candidate-{attempt}-worker-merge",
            "change_summary": change_summary,
            "target_files": target_files,
            "test_plan": test_plan,
            "risks": risks,
            "workspace_effect": "modified",
            **({"candidate_fragment": candidate_fragment} if candidate_fragment is not None else {}),
        }
    )


_FRONTEND_CONTRACT_FILES = ("index.html", "styles.css", "app.js")


def _frontend_contract_files_are_owned(
    *,
    owner_paths: Sequence[str],
    candidate_payload: dict[str, Any],
) -> bool:
    paths = [
        _candidate_path_key(path)
        for path in [*owner_paths, *(candidate_payload.get("target_files") or [])]
        if _candidate_path_key(path)
    ]
    return all(any(_paths_overlap(path, required) for path in paths) for required in _FRONTEND_CONTRACT_FILES)


def _workspace_root_from_task(task: "CodingTask") -> Path | None:
    context = task.session_context if isinstance(task.session_context, dict) else {}
    for key in ("workspace_root", "effective_working_directory", "current_working_directory"):
        value = _clean_text(context.get(key))
        if not value:
            continue
        try:
            return Path(value).expanduser().resolve()
        except OSError:
            return None
    return None


def _workspace_contract_file(workspace_root: Path, relative_path: str) -> Path | None:
    try:
        path = (workspace_root / relative_path).resolve()
        path.relative_to(workspace_root)
    except (OSError, ValueError):
        return None
    return path


def _dedupe_duplicate_features_sections(html: str) -> tuple[str, bool]:
    section_pattern = re.compile(
        r"<section\b[^>]*\bid=[\"']features[\"'][^>]*>.*?</section>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    matches = [
        match
        for match in section_pattern.finditer(html)
        if re.search(r"\bclass=[\"'][^\"']*\bfeatures\b[^\"']*[\"']", match.group(0), re.IGNORECASE)
    ]
    if len(matches) <= 1:
        return html, False

    keep_index = 0
    for index, match in enumerate(matches):
        if "data-animate" in match.group(0):
            keep_index = index
            break

    parts: list[str] = []
    cursor = 0
    for index, match in enumerate(matches):
        if index == keep_index:
            parts.append(html[cursor : match.end()])
        else:
            parts.append(html[cursor : match.start()])
        cursor = match.end()
    parts.append(html[cursor:])
    return "".join(parts), True


def _ensure_feature_animation_hooks(html: str) -> tuple[str, bool]:
    section_pattern = re.compile(
        r"(<section\b[^>]*\bid=[\"']features[\"'][^>]*>)(.*?)(</section>)",
        flags=re.IGNORECASE | re.DOTALL,
    )

    def _replace_section(match: re.Match[str]) -> str:
        opening = match.group(1)
        body = match.group(2)
        closing = match.group(3)
        if not re.search(r"\bdata-animate=", opening, re.IGNORECASE):
            opening = opening[:-1] + ' data-animate="features">'
        body = re.sub(
            r"<article\b(?![^>]*\bdata-animate=)([^>]*)>",
            lambda article: f'<article{article.group(1)} data-animate="feature">',
            body,
            flags=re.IGNORECASE,
        )
        return opening + body + closing

    updated, count = section_pattern.subn(_replace_section, html, count=1)
    return updated, bool(count and updated != html)


def _dedupe_consecutive_closing_tags(html: str, tags: Sequence[str] = ("main", "body", "html")) -> tuple[str, bool]:
    lines = html.splitlines(keepends=True)
    output: list[str] = []
    changed = False
    tag_set = {tag.lower() for tag in tags}
    for line in lines:
        stripped = line.strip().lower()
        if stripped.startswith("</") and stripped.endswith(">"):
            tag = stripped[2:-1].strip()
            if tag in tag_set and output and output[-1].strip().lower() == stripped:
                changed = True
                continue
        output.append(line)
    return "".join(output), changed


def _normalize_animation_delay_attributes(html: str) -> tuple[str, bool]:
    tag_pattern = re.compile(r"<[^>]*\bdata-delay=[^>]*>", flags=re.IGNORECASE)

    def _replace_tag(match: re.Match[str]) -> str:
        tag = match.group(0)
        if re.search(r"\bdata-animate-delay=", tag, re.IGNORECASE):
            return tag
        return re.sub(r"\bdata-delay=", "data-animate-delay=", tag, flags=re.IGNORECASE)

    updated, count = tag_pattern.subn(_replace_tag, html)
    return updated, bool(count and updated != html)


def _close_unclosed_style_blocks_before_head(html: str) -> tuple[str, bool]:
    style_pattern = re.compile(
        r"(<style\b[^>]*>)(.*?)(</head>)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    changed = False

    def _replace_style(match: re.Match[str]) -> str:
        nonlocal changed
        body = match.group(2)
        if re.search(r"</style\s*>", body, flags=re.IGNORECASE):
            return match.group(0)
        changed = True
        css_lines: list[str] = []
        for line in body.splitlines():
            if line.lstrip().startswith("<"):
                continue
            css_lines.append(line)
        css_body = "\n".join(css_lines).rstrip()
        if css_body:
            css_body += "\n"
        return f"{match.group(1)}{css_body}  </style>\n{match.group(3)}"

    return style_pattern.sub(_replace_style, html), changed


def _dedupe_document_tail_tags(html: str) -> tuple[str, bool]:
    changed = False

    def _dedupe_pattern(source: str, pattern: re.Pattern[str]) -> str:
        nonlocal changed
        matches = list(pattern.finditer(source))
        if len(matches) <= 1:
            return source
        changed = True
        keep_end = matches[-1].end()
        parts: list[str] = []
        cursor = 0
        for match in matches[:-1]:
            parts.append(source[cursor : match.start()])
            cursor = match.end()
        parts.append(source[cursor:keep_end])
        parts.append(source[keep_end:])
        return "".join(parts)

    html = _dedupe_pattern(
        html,
        re.compile(
            r"\s*<script\b[^>]*\bsrc=[\"']\.\/app\.js[\"'][^>]*>\s*</script>\s*",
            flags=re.IGNORECASE,
        ),
    )
    for tag in ("main", "body", "html"):
        html = _dedupe_pattern(
            html,
            re.compile(rf"\s*</{tag}\s*>\s*", flags=re.IGNORECASE),
        )
    return html, changed


def _frontend_contract_validation_errors(workspace_root: Path) -> list[str]:
    errors: list[str] = []
    index_path = _workspace_contract_file(workspace_root, "index.html")
    if index_path is None or not index_path.exists():
        return errors
    try:
        html = index_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"index.html: {exc}"]

    for tag in ("html", "head", "body", "main"):
        open_count = len(re.findall(rf"<{tag}(?:\s|>)", html, flags=re.IGNORECASE))
        close_count = len(re.findall(rf"</{tag}\s*>", html, flags=re.IGNORECASE))
        if open_count != 1 or close_count != 1:
            errors.append(f"index.html has {open_count} <{tag}> and {close_count} </{tag}> tags")

    for style_match in re.finditer(r"<style\b[^>]*>(.*?)</style\s*>", html, flags=re.IGNORECASE | re.DOTALL):
        if re.search(r"</head\s*>|<body\b|<section\b", style_match.group(1), flags=re.IGNORECASE):
            errors.append("index.html has markup leaked into a style block")
            break
    head_match = re.search(r"<head\b[^>]*>(.*?)</head\s*>", html, flags=re.IGNORECASE | re.DOTALL)
    if head_match and "<style" in head_match.group(1).lower() and "</style>" not in head_match.group(1).lower():
        errors.append("index.html has an unclosed style block before </head>")

    for ref in re.findall(r"\b(?:href|src)=[\"']([^\"']+)[\"']", html, flags=re.IGNORECASE):
        if ref.startswith(("#", "http://", "https://", "mailto:", "tel:", "data:")):
            continue
        local_ref = ref.split("#", 1)[0].split("?", 1)[0]
        if not local_ref:
            continue
        ref_path = _workspace_contract_file(workspace_root, local_ref)
        if ref_path is None or not ref_path.exists():
            errors.append(f"index.html references missing local file {ref}")

    styles_path = _workspace_contract_file(workspace_root, "styles.css")
    if styles_path is not None and styles_path.exists():
        try:
            css = styles_path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"styles.css: {exc}")
        else:
            if css.count("{") != css.count("}"):
                errors.append("styles.css has unbalanced braces")

    return errors


def _ensure_visible_animation_css(css: str) -> tuple[str, bool]:
    additions: list[str] = []
    if "[data-animate]" in css and ".js-reveal.is-visible" not in css:
        additions.append(
            """
/* Compatibility for JS-driven reveal classes used by parallel app.js lanes. */
.js-reveal {
  opacity: 0;
  transform: translateY(18px);
  transition: opacity 0.7s cubic-bezier(0.22, 0.61, 0.36, 1), transform 0.7s cubic-bezier(0.22, 0.61, 0.36, 1);
}

[data-animate].is-visible,
.js-reveal.is-visible {
  opacity: 1;
  transform: translateY(0);
}
""".strip()
        )
    if ".scroll-cue" in css and ".scroll-cue.is-hidden" not in css:
        additions.append(
            """
.scroll-cue.is-hidden {
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.3s ease;
}
""".strip()
        )
    if (
        ("[data-animate]" in css or ".js-reveal" in css or ".scroll-cue-dot" in css)
        and "prefers-reduced-motion" not in css
    ):
        additions.append(
            """
@media (prefers-reduced-motion: reduce) {
  [data-animate],
  .js-reveal {
    opacity: 1;
    transform: none;
    transition: none;
  }

  .scroll-cue-dot {
    animation: none;
  }
}
""".strip()
        )
    if not additions:
        return css, False
    return css.rstrip() + "\n\n" + "\n\n".join(additions) + "\n", True


def _apply_frontend_contract_hygiene(
    *,
    task: "CodingTask",
    candidate_payload: dict[str, Any],
    owner_paths: Sequence[str],
) -> dict[str, Any] | None:
    if not _frontend_contract_files_are_owned(owner_paths=owner_paths, candidate_payload=candidate_payload):
        return None
    workspace_root = _workspace_root_from_task(task)
    if workspace_root is None:
        return None

    changed_files: list[str] = []
    repairs: list[str] = []
    errors: list[str] = []

    index_path = _workspace_contract_file(workspace_root, "index.html")
    if index_path is not None and index_path.exists():
        try:
            html = index_path.read_text(encoding="utf-8")
            updated_html, style_closed = _close_unclosed_style_blocks_before_head(html)
            updated_html, tail_tags_deduped = _dedupe_document_tail_tags(updated_html)
            updated_html, deduped = _dedupe_duplicate_features_sections(updated_html)
            updated_html, hooks_added = _ensure_feature_animation_hooks(updated_html)
            updated_html, closing_tags_deduped = _dedupe_consecutive_closing_tags(updated_html)
            updated_html, delay_attrs_normalized = _normalize_animation_delay_attributes(updated_html)
            if updated_html != html:
                index_path.write_text(updated_html, encoding="utf-8")
                changed_files.append("index.html")
                if deduped:
                    repairs.append("deduped duplicate #features sections")
                if style_closed:
                    repairs.append("closed unclosed style blocks before </head>")
                if tail_tags_deduped:
                    repairs.append("removed duplicate document tail tags")
                if hooks_added:
                    repairs.append("normalized feature data-animate hooks")
                if closing_tags_deduped:
                    repairs.append("removed duplicate closing container tags")
                if delay_attrs_normalized:
                    repairs.append("normalized data-animate-delay attributes")
        except OSError as exc:
            errors.append(f"index.html: {exc}")

    styles_path = _workspace_contract_file(workspace_root, "styles.css")
    if styles_path is not None and styles_path.exists():
        try:
            css = styles_path.read_text(encoding="utf-8")
            updated_css, css_changed = _ensure_visible_animation_css(css)
            if css_changed:
                styles_path.write_text(updated_css, encoding="utf-8")
                changed_files.append("styles.css")
                repairs.append("added .is-visible/.js-reveal CSS compatibility")
        except OSError as exc:
            errors.append(f"styles.css: {exc}")

    validation_errors = _frontend_contract_validation_errors(workspace_root)
    errors.extend(error for error in validation_errors if error not in errors)

    if not changed_files and not errors:
        return None

    target_files = list(candidate_payload.get("target_files") or [])
    for changed_file in changed_files:
        if changed_file not in target_files:
            target_files.append(changed_file)
    candidate_payload["target_files"] = target_files

    if changed_files:
        summary = _clean_text(candidate_payload.get("change_summary"))
        hygiene_summary = "Applied deterministic frontend contract hygiene: " + "; ".join(repairs) + "."
        candidate_payload["change_summary"] = f"{summary} {hygiene_summary}".strip()
        test_plan = list(candidate_payload.get("test_plan") or [])
        hygiene_test = (
            "Validate that index.html has one #features section and that CSS/JS reveal hooks agree on "
            "[data-animate], .is-visible, and .js-reveal."
        )
        if hygiene_test not in test_plan:
            test_plan.append(hygiene_test)
        candidate_payload["test_plan"] = test_plan

    if errors:
        risks = list(candidate_payload.get("risks") or [])
        risk = "Deterministic frontend contract hygiene could not inspect all files: " + "; ".join(errors)
        if risk not in risks:
            risks.append(risk)
        candidate_payload["risks"] = risks

    return {
        "changed_files": changed_files,
        "repairs": repairs,
        "errors": errors,
    }


def _apply_short_timeout_frontend_contract_hygiene(
    *,
    task: "CodingTask",
    candidate_payload: dict[str, Any],
    owner_paths: Sequence[str],
) -> dict[str, Any] | None:
    return _apply_frontend_contract_hygiene(
        task=task,
        candidate_payload=candidate_payload,
        owner_paths=owner_paths,
    )


def _augment_aggregation_candidate_from_worker_results(
    payload: dict[str, Any],
    *,
    member_results: dict[str, Any],
    worker_execution: TissueExecution,
    attempt: int,
) -> dict[str, Any]:
    material_payloads = _material_worker_payloads_with_mutation_evidence(
        member_results=member_results,
        worker_execution=worker_execution,
    )
    owned_mutation_paths = _exclusive_owner_mutation_paths(worker_execution)
    if not material_payloads and not owned_mutation_paths:
        return payload

    candidate = _candidate_payload(payload)
    was_timeout_placeholder = _is_timeout_or_blocked_no_output_candidate(candidate)
    owner_paths = _exclusive_owner_paths(worker_execution)
    target_files: list[str] = []
    test_plan: list[str] = []
    risks: list[str] = []
    summaries: list[str] = []
    _extend_unique_candidate_paths(
        target_files,
        owned_mutation_paths,
        owner_paths=owner_paths,
    )
    for material_payload in material_payloads:
        _extend_unique_candidate_paths(
            target_files,
            material_payload.get("target_files"),
            owner_paths=owner_paths,
        )
        _extend_unique_text(test_plan, material_payload.get("test_plan"))
        _extend_unique_text(risks, material_payload.get("risks"))
        summary = _clean_text(material_payload.get("change_summary"))
        if summary and summary not in summaries:
            summaries.append(summary)

    if target_files:
        merged_target_files: list[str] = []
        _extend_unique_candidate_paths(
            merged_target_files,
            candidate.get("target_files"),
            owner_paths=owner_paths,
        )
        _extend_unique_candidate_paths(
            merged_target_files,
            target_files,
            owner_paths=owner_paths,
        )
        candidate["target_files"] = merged_target_files
    if test_plan:
        merged_test_plan: list[str] = []
        _extend_unique_text(merged_test_plan, candidate.get("test_plan"))
        _extend_unique_text(merged_test_plan, test_plan)
        candidate["test_plan"] = merged_test_plan
    if risks:
        merged_risks: list[str] = []
        _extend_unique_text(merged_risks, candidate.get("risks"))
        _extend_unique_text(merged_risks, risks)
        candidate["risks"] = merged_risks
    if summaries and (
        not _clean_text(candidate.get("change_summary"))
        or was_timeout_placeholder
    ):
        candidate["change_summary"] = "Merged worker material changes: " + " ".join(summaries)
    if target_files and (
        not _clean_text(candidate.get("candidate_id"))
        or was_timeout_placeholder
    ):
        candidate["candidate_id"] = f"candidate-{attempt}-worker-merge"
    if target_files and not _clean_text(candidate.get("workspace_effect")):
        candidate["workspace_effect"] = "modified"
    candidate_fragment = _merge_candidate_fragments(material_payloads)
    if candidate_fragment is not None and "candidate_fragment" not in candidate:
        candidate["candidate_fragment"] = candidate_fragment
    return _candidate_payload(candidate)


def _worker_member(
    *,
    organism: CodingOrganism,
    attempt: int,
    index: int,
    tissue_id: str,
    brief: str,
    owner_path: str = "",
    tool_ids: list[str],
) -> TissueMember:
    member_id = f"worker-{index}"
    cell_id = f"{organism.base_id}.{member_id}"
    exclusive_owner_path = _artifact_path_key(owner_path) or _exclusive_write_owner_path_from_brief(brief)
    instruction_suffix = f"Assigned brief:\n{brief}"
    if exclusive_owner_path:
        instruction_suffix = (
            f"{instruction_suffix}\n\n"
            f"Exclusive write-owner constraint: `{exclusive_owner_path}` is your owned file. "
            "The global entry-point inspection, modified-file listing, full updated-content return, and read-back "
            "confirmation requirements are satisfied across the whole organism, not inside this lane alone. "
            "Do not try to satisfy those global delivery requirements by reading other files or drafting full final output here. "
            "Read only the minimum local context needed, then use targeted `file_edit` when "
            "that tool is available for an existing owned file; use `file_write` only when "
            "the owned file must be created or `file_edit` is unavailable. Do not spend "
            "another round on broad auditing after reading the owned file once. "
            f"After the first successful read of `{exclusive_owner_path}`, the very next model response must be "
            "`file_edit` or `file_write` against that same owned file, not more reads, planning prose, or candidate-only text."
        )
    return TissueMember(
        member_id=member_id,
        address=CellAddress(
            cell_id=cell_id,
            tissue_id=tissue_id,
            organ_id=f"{organism.base_id}.worker-pool",
            organism_id=organism.organism_id,
        ),
        worker=WorkerDefinition(
            id=cell_id,
            role=organism.worker_role,
            instruction=organism.worker_instruction,
            model=organism.worker_model,
            tool_ids=list(tool_ids),
        ),
        instruction_suffix=instruction_suffix,
        input_payload_overrides={
            "worker_brief": brief,
            "worker_index": index,
            "attempt": attempt,
        },
        metadata={
            "organism_id": organism.organism_id,
            "worker_role": organism.worker_role,
            "exclusive_write_owner_path": exclusive_owner_path,
        },
    )


def _worker_tool_ids_for_plan(
    *,
    organism: CodingOrganism,
    plan: CodingOrchestratorPlan,
) -> list[str]:
    if plan.worker_count <= 1:
        return list(organism.worker_tool_ids)
    if _plan_has_parallel_artifact_owners(plan):
        file_tool_ids = [
            tool_id
            for tool_id in organism.worker_tool_ids
            if tool_id in {"file_read", "file_edit", "file_write"}
        ]
        if file_tool_ids:
            return file_tool_ids
        return list(organism.worker_tool_ids)
    if organism.parallel_worker_tool_ids:
        return list(organism.parallel_worker_tool_ids)
    return list(organism.worker_tool_ids)


def _plan_has_parallel_artifact_owners(plan: CodingOrchestratorPlan) -> bool:
    if plan.worker_count <= 1:
        return False
    if any(_artifact_path_key(path) for path in plan.artifact_owner_paths):
        return True
    return any(
        "exclusive write owner" in _clean_text(brief).lower()
        for brief in plan.worker_briefs
    )


def _plan_has_parallel_exclusive_write_owners(plan: CodingOrchestratorPlan) -> bool:
    return _plan_has_parallel_artifact_owners(plan)


def _runtime_policy_metadata(task: "CodingTask") -> dict[str, Any]:
    session_context = task.session_context if isinstance(task.session_context, dict) else {}
    metadata: dict[str, Any] = {}
    raw_timeout = session_context.get("completion_timeout_seconds")
    timeout_seconds: float | None = None
    try:
        timeout_seconds = float(raw_timeout)
    except (TypeError, ValueError):
        timeout_seconds = None
    if timeout_seconds is not None:
        metadata["completion_timeout_seconds"] = timeout_seconds
    short_timeout = bool(session_context.get("short_completion_timeout"))
    if timeout_seconds is not None and 0 < timeout_seconds <= 60.0:
        short_timeout = True
    if short_timeout:
        metadata["short_completion_timeout"] = True
        metadata["disable_timeout_recovery"] = True
    return metadata


def _short_completion_timeout_task(task: "CodingTask") -> bool:
    return bool(_runtime_policy_metadata(task).get("short_completion_timeout"))


def _validation_runtime_policy_metadata(task: "CodingTask") -> dict[str, Any]:
    metadata = _runtime_policy_metadata(task)
    raw_timeout = metadata.get("completion_timeout_seconds")
    try:
        timeout_seconds = float(raw_timeout)
    except (TypeError, ValueError):
        return metadata
    if timeout_seconds <= 0:
        return metadata
    cap = _VALIDATION_COMPLETION_TIMEOUT_CAP_SECONDS
    if timeout_seconds <= cap:
        return metadata
    metadata["completion_timeout_seconds"] = cap
    metadata["short_completion_timeout"] = True
    metadata["disable_timeout_recovery"] = True
    metadata["validation_timeout_cap_seconds"] = cap
    metadata["inherited_completion_timeout_seconds"] = timeout_seconds
    return metadata


def _candidate_path_keys(candidate_payload: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for path in _candidate_view(candidate_payload).target_files:
        key = _artifact_path_key(path)
        if key and key not in paths:
            paths.append(key)
    return paths


def _deterministic_validation_precheck(
    *,
    task: "CodingTask",
    plan: CodingOrchestratorPlan,
    candidate_payload: dict[str, Any],
    candidate_source: str,
) -> dict[str, Any]:
    candidate = _candidate_view(candidate_payload)
    blocking_errors: list[str] = []
    warnings: list[str] = []
    checks: list[str] = []

    if _is_timeout_or_blocked_no_output_candidate(candidate_payload):
        blocking_errors.append("candidate is a timeout/blocker placeholder rather than material output")
    if not _clean_text(candidate.candidate_id):
        warnings.append("candidate does not include a stable candidate_id")
    target_paths = _candidate_path_keys(candidate_payload)
    if not target_paths:
        blocking_errors.append("candidate names no target files")
    if not candidate.test_plan:
        warnings.append("candidate has no focused validation steps")

    owner_paths = [_artifact_path_key(path) for path in plan.artifact_owner_paths if _artifact_path_key(path)]
    if owner_paths:
        for owner_path in owner_paths:
            if not any(_paths_overlap(owner_path, target_path) for target_path in target_paths):
                blocking_errors.append(f"candidate does not cover artifact owner path {owner_path}")
        checks.append("artifact_owner_coverage")

    workspace_root = _workspace_root_from_task(task)
    frontend_like_paths = {
        Path(path).name
        for path in [*target_paths, *owner_paths]
        if Path(path).suffix.lower() in {".html", ".css", ".js"}
    }
    if workspace_root is not None and frontend_like_paths:
        frontend_errors = _frontend_contract_validation_errors(workspace_root)
        if frontend_errors:
            blocking_errors.extend(frontend_errors)
        checks.append("frontend_static_contract")

    return {
        "policy_source": (
            "compact_validator"
            if _should_use_compact_validation(
                task=task,
                plan=plan,
                candidate_source=candidate_source,
                blocking_errors=blocking_errors,
            )
            else "full_validator"
        ),
        "candidate_source": candidate_source,
        "blocking_errors": blocking_errors,
        "warnings": warnings,
        "checks": checks,
    }


def _should_use_compact_validation(
    *,
    task: "CodingTask",
    plan: CodingOrchestratorPlan,
    candidate_source: str,
    blocking_errors: Sequence[str],
) -> bool:
    if blocking_errors:
        return True
    if _short_completion_timeout_task(task):
        return True
    if candidate_source == "worker_merge":
        return True
    if _clean_text(plan.scheduler_policy_source) == "generic_artifact_partition":
        return True
    return False


def _incremental_validation_precheck_record(
    *,
    task: "CodingTask",
    plan: CodingOrchestratorPlan,
    candidate_payload: dict[str, Any],
    candidate_source: str,
    attempt: int,
    fragment_count: int,
    owner_paths: Sequence[str],
) -> dict[str, Any]:
    candidate = _candidate_view(candidate_payload)
    precheck = _deterministic_validation_precheck(
        task=task,
        plan=plan,
        candidate_payload=candidate_payload,
        candidate_source=candidate_source,
    )
    normalized_owner_paths = [
        _artifact_path_key(path)
        for path in owner_paths
        if _artifact_path_key(path)
    ]
    return {
        "attempt": attempt,
        "candidate_key": _candidate_readiness_key(candidate_payload),
        "candidate_id": candidate.candidate_id,
        "candidate_source": candidate_source,
        "fragment_count": int(fragment_count),
        "owner_paths": normalized_owner_paths,
        "policy_source": precheck["policy_source"],
        "blocking_errors": list(precheck.get("blocking_errors") or []),
        "warnings": list(precheck.get("warnings") or []),
        "checks": list(precheck.get("checks") or []),
        "validation_precheck": dict(precheck),
    }


def _select_validation_organ(
    *,
    organism: "CodingOrganism",
    validation_precheck: dict[str, Any],
) -> OrganPattern:
    if (
        validation_precheck["policy_source"] == "compact_validator"
        and organism.compact_validator_organ is not None
    ):
        return organism.compact_validator_organ
    return organism.validator_organ


def _validation_output_contract() -> OutputContract:
    return OutputContract(
        definition_of_done="Return the validation result for the aggregated coding candidate.",
        expected_return_shape=json.dumps(
            {
                "passed": "<required>",
                "overall_score": "<required>",
                "dimension_scores": "<required>",
                "repair_brief": "<required>",
                "missing_requirements": "<required>",
                "comparison_note": "<required>",
            },
            sort_keys=True,
        ),
    )


def _validation_packet(
    *,
    task: "CodingTask",
    plan: CodingOrchestratorPlan,
    selected_validator_organ: OrganPattern,
    parent_packet: CellHandoffPacket,
    parent_signal_id: str,
    candidate_payload: dict[str, Any],
    candidate_source: str,
    validation_precheck: dict[str, Any],
    attempt: int,
    worker_count: int,
    best_score_so_far: float | None,
    repair_brief: str,
    evidence_refs: Sequence[EvidenceRef],
    task_id: str,
    metadata: dict[str, Any] | None = None,
) -> CellHandoffPacket:
    return _child_packet(
        sender=parent_packet.recipient,
        recipient=selected_validator_organ.boundary_address,
        parent_packet=parent_packet,
        parent_signal_id=parent_signal_id,
        lineage_suffix=f"organ:{selected_validator_organ.organ_id}",
        task_id=task_id,
        instruction=plan.validator_focus,
        scope="coding-organism.validate",
        hard_constraints=list(task.hard_constraints),
        soft_constraints=list(task.soft_constraints),
        input_payload={
            "candidate": dict(candidate_payload),
            "acceptance_criteria": list(task.acceptance_criteria),
            "research_findings": list(task.research_findings),
            "session_context": dict(task.session_context),
            "quality_bar": float(plan.pass_threshold),
            "deterministic_precheck": dict(validation_precheck),
            "comparison_context": {
                "attempt": attempt,
                "best_score_so_far": best_score_so_far,
                "repair_brief": repair_brief,
                "worker_count": worker_count,
                "candidate_source": candidate_source,
                "validation_policy_source": validation_precheck["policy_source"],
            },
        },
        evidence_refs=list(evidence_refs),
        output_contract=_validation_output_contract(),
        authority=WorkerAuthority.DELEGATE,
        metadata={
            "organism_id": _organism_id_from_validator(selected_validator_organ),
            "organism_stage": "validation",
            "organ_id": selected_validator_organ.organ_id,
            "validation_policy_source": validation_precheck["policy_source"],
            **dict(metadata or {}),
            **_validation_runtime_policy_metadata(task),
        },
    )


def _organism_id_from_validator(selected_validator_organ: OrganPattern) -> str:
    return selected_validator_organ.boundary_address.organism_id or ""


def _speculative_model_validation_admission(
    *,
    validation_precheck: dict[str, Any],
    selected_validator_organ: OrganPattern,
) -> dict[str, Any]:
    blockers: list[str] = []
    if validation_precheck.get("blocking_errors"):
        blockers.append("blocking_precheck")
    if validation_precheck.get("policy_source") != "compact_validator":
        blockers.append("non_compact_policy")
    if selected_validator_organ.tissue is not None:
        blockers.append("validator_requires_tissue_quorum")
    if selected_validator_organ.metadata.get("validation_mode") != "compact_model_review":
        blockers.append("non_compact_validator")
    return {
        "admitted": not blockers,
        "blockers": blockers,
        "reason": "admitted" if not blockers else ",".join(blockers),
    }


def _precheck_failed_validation_payload(precheck: dict[str, Any]) -> dict[str, Any]:
    missing_requirements = [
        _clean_text(item)
        for item in precheck.get("blocking_errors") or []
        if _clean_text(item)
    ]
    comparison_note = (
        "Deterministic validation precheck found blocking issues: "
        + "; ".join(missing_requirements)
        if missing_requirements
        else "Deterministic validation precheck failed."
    )
    return {
        "passed": False,
        "overall_score": 0.0,
        "dimension_scores": {"deterministic_precheck": 0.0},
        "repair_brief": comparison_note,
        "missing_requirements": missing_requirements,
        "comparison_note": comparison_note,
        "validation_policy_source": "deterministic_precheck",
        "deterministic_precheck": dict(precheck),
    }


def _validation_precheck_execution(
    *,
    pattern: OrganPattern,
    packet: CellHandoffPacket,
    payload: dict[str, Any],
    trace_log: CrossCellTraceLog | None,
) -> OrganExecution:
    if trace_log is not None:
        trace_log.record_handoff(packet)
    result = WorkerExecutionResult(
        status="completed",
        outputs=payload,
        metadata={
            "organ_id": pattern.organ_id,
            "organ_kind": pattern.kind.value,
            "validation_policy_source": "deterministic_precheck",
        },
    )
    completion = make_completion_signal(
        packet,
        result,
        summary="Deterministic validation precheck blocked the candidate.",
        metadata={
            "organ_id": pattern.organ_id,
            "organ_kind": pattern.kind.value,
            "output_keys": sorted(payload),
            "validation_policy_source": "deterministic_precheck",
        },
    )
    if trace_log is not None:
        trace_log.record_signal(completion)
    return OrganExecution(
        pattern=pattern,
        packet=packet,
        result=OrganExecutionResult(
            status="completed",
            outputs=dict(payload),
            metadata={
                "organ_id": pattern.organ_id,
                "organ_kind": pattern.kind.value,
                "validation_policy_source": "deterministic_precheck",
            },
        ),
        signals=[completion],
    )


def _worker_pool_pattern(
    *,
    organism: CodingOrganism,
    attempt: int,
    plan: CodingOrchestratorPlan,
) -> tuple[TissuePattern, CellAddress]:
    tissue_id = f"{organism.base_id}.worker-pool.{attempt}"
    coordinator = CellAddress(
        cell_id=f"{tissue_id}.coordinator",
        tissue_id=tissue_id,
        organ_id=f"{organism.base_id}.worker-pool",
        organism_id=organism.organism_id,
    )
    worker_tool_ids = _worker_tool_ids_for_plan(organism=organism, plan=plan)
    members = [
        _worker_member(
            organism=organism,
            attempt=attempt,
            index=index,
            tissue_id=tissue_id,
            brief=brief,
            owner_path=(
                plan.artifact_owner_paths[index - 1]
                if index - 1 < len(plan.artifact_owner_paths)
                else ""
            ),
            tool_ids=worker_tool_ids,
        )
        for index, brief in enumerate(plan.worker_briefs, start=1)
    ]
    pattern = parallel_worker_pool(
        f"{organism.base_id}.worker-pool",
        members=members,
        limits=TissuePoolLimits(
            max_members=organism.max_worker_count,
            max_concurrency=max(1, len(members)),
            max_failures=0,
        ),
        merge_mode=TissueMergeMode.APPEND,
        metadata={
            "organism_id": organism.organism_id,
            "attempt": attempt,
        },
    )
    return pattern, coordinator


def _completion_output_refs(signals: list[Any]) -> list[EvidenceRef]:
    terminal_signal = signals[-1] if signals else None
    if isinstance(terminal_signal, CompletionSignal):
        return [ref.model_copy(deep=True) for ref in terminal_signal.output_refs]
    return []


def _executed_tools_from_execution(execution: OrganExecution | None) -> list[dict[str, Any]] | None:
    if execution is None:
        return None
    return _executed_tools_from_handoff_execution(execution.lead_execution)


def _executed_tools_from_handoff_execution(execution: HandoffExecution | None) -> list[dict[str, Any]] | None:
    if execution is None:
        return None
    raw_response = execution.result.metadata.get("raw_response")
    if not isinstance(raw_response, dict):
        return None
    executed_tools = raw_response.get("executed_tools")
    if not isinstance(executed_tools, list):
        return None
    return [dict(tool) for tool in executed_tools if isinstance(tool, dict)]


def _executed_tools_from_tissue_execution(
    worker_execution: TissueExecution | None,
) -> list[dict[str, Any]] | None:
    if worker_execution is None:
        return None
    executed_tools: list[dict[str, Any]] = []
    for member_execution in _worker_execution_map(worker_execution).values():
        member_tools = _executed_tools_from_handoff_execution(member_execution)
        if member_tools:
            executed_tools.extend(member_tools)
    return executed_tools or None


def _worker_execution_map(worker_execution: TissueExecution) -> dict[str, HandoffExecution]:
    return {
        member.member_id: execution
        for member, execution in zip(
            worker_execution.pattern.members,
            worker_execution.member_executions,
            strict=True,
        )
    }


def _looks_like_mutating_shell_command(command: Any) -> bool:
    text = _clean_text(command).lower()
    if not text:
        return False
    mutation_markers = (
        "apply_patch",
        "git apply",
        "sed -i",
        "perl -pi",
        "perl -0pi",
        "tee ",
        "cat >",
        "cat >>",
        "mv ",
        "cp ",
        "mkdir ",
        "touch ",
        "rm ",
        " > ",
        " >> ",
    )
    return any(marker in text for marker in mutation_markers)


def _candidate_path_key(path: Any) -> str:
    text = _clean_text(path).replace("\\", "/").rstrip("/")
    while "//" in text:
        text = text.replace("//", "/")
    return text


def _paths_overlap(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    return left.endswith(f"/{right}") or right.endswith(f"/{left}")


def _successful_file_mutation_paths(executed_tools: list[dict[str, Any]] | None) -> list[str]:
    paths: list[str] = []
    for tool in executed_tools or []:
        if not tool.get("ok"):
            continue
        if str(tool.get("tool_id") or "").strip() not in {"file_edit", "file_write"}:
            continue
        arguments = dict(tool.get("arguments") or {})
        result = dict(tool.get("result") or {})
        path = _candidate_path_key(result.get("path") or arguments.get("path"))
        if path and path not in paths:
            paths.append(path)
    return paths


def _candidate_has_matching_mutation_evidence(
    payload: dict[str, Any],
    *,
    executed_tools: list[dict[str, Any]] | None,
) -> bool:
    target_files = [_candidate_path_key(path) for path in payload.get("target_files") or []]
    target_files = [path for path in target_files if path]
    if not target_files:
        return False
    mutation_paths = _successful_file_mutation_paths(executed_tools)
    if not mutation_paths:
        return False
    return any(
        _paths_overlap(target_path, mutation_path)
        for target_path in target_files
        for mutation_path in mutation_paths
    )


def _candidate_requires_mutation_evidence(
    *,
    payload: dict[str, Any],
    executed_tools: list[dict[str, Any]] | None,
) -> tuple[str, dict[str, Any]] | None:
    candidate = _candidate_view(payload)
    claimed_files = list(candidate.target_files)
    workspace_effect = _candidate_workspace_effect(candidate)
    if not claimed_files:
        if workspace_effect == "modified":
            return (
                "Candidate claimed a modified workspace but did not name target files.",
                {
                    "mutation_evidence_required": True,
                    "target_files_required": True,
                    "claimed_target_files": claimed_files,
                    "workspace_effect": workspace_effect,
                },
            )
        return None

    if executed_tools is None:
        return None

    successful_mutation_tools: list[str] = []
    executed_tool_ids: list[str] = []
    for tool in executed_tools:
        tool_id = str(tool.get("tool_id") or "").strip()
        if tool_id:
            executed_tool_ids.append(tool_id)
        if not tool.get("ok"):
            continue
        if tool_id in {"file_edit", "file_write"}:
            successful_mutation_tools.append(tool_id)
            continue
        if tool_id == "shell_command":
            command = dict(tool.get("arguments") or {}).get("command")
            if _looks_like_mutating_shell_command(command):
                successful_mutation_tools.append(tool_id)

    if successful_mutation_tools:
        return None

    if not _mutation_claim_requires_evidence(candidate):
        return None

    return (
        "Candidate claimed concrete file changes but no mutation-capable tool call succeeded.",
        {
            "mutation_evidence_required": True,
            "claimed_target_files": claimed_files,
            "executed_tool_ids": executed_tool_ids,
            "workspace_effect": workspace_effect,
        },
    )


def _aggregation_requires_mutation_evidence(
    *,
    payload: dict[str, Any],
    execution: OrganExecution | None,
    worker_execution: TissueExecution | None = None,
) -> tuple[str, dict[str, Any]] | None:
    executed_tools: list[dict[str, Any]] = []
    aggregation_tools = _executed_tools_from_execution(execution)
    worker_tools = _executed_tools_from_tissue_execution(worker_execution)
    if aggregation_tools:
        executed_tools.extend(aggregation_tools)
    if worker_tools:
        executed_tools.extend(worker_tools)
    return _candidate_requires_mutation_evidence(
        payload=payload,
        executed_tools=executed_tools or None,
    )


def _promote_single_worker_candidate(
    *,
    attempt: int,
    worker_execution: TissueExecution,
) -> tuple[str, dict[str, Any]] | None:
    successful_member_ids = list(worker_execution.result.metadata.get("successful_member_ids") or [])
    if len(successful_member_ids) != 1:
        return None

    member_id = successful_member_ids[0]
    member_results = dict(worker_execution.result.outputs.get("member_results") or {})
    member_payload = _parse_payload(dict(member_results.get(member_id) or {}))
    if not _has_material_candidate_output(member_payload):
        return None

    candidate_payload: dict[str, Any] = {
        "candidate_id": _clean_text(member_payload.get("candidate_id")) or f"candidate-{attempt}-{member_id}",
        "change_summary": member_payload.get("change_summary"),
        "target_files": list(member_payload.get("target_files") or []),
        "test_plan": list(member_payload.get("test_plan") or []),
        "risks": list(member_payload.get("risks") or []),
    }
    _copy_candidate_material_extras(candidate_payload, member_payload)
    if member_payload.get("workspace_effect") is not None:
        candidate_payload["workspace_effect"] = member_payload.get("workspace_effect")

    member_execution = _worker_execution_map(worker_execution).get(member_id)
    executed_tools = _executed_tools_from_handoff_execution(member_execution)
    if executed_tools is None:
        return None
    if executed_tools is not None:
        has_mutation_evidence = False
        for tool in executed_tools:
            if not tool.get("ok"):
                continue
            tool_id = str(tool.get("tool_id") or "").strip()
            if tool_id in {"file_edit", "file_write"}:
                has_mutation_evidence = True
                break
            if tool_id == "shell_command":
                command = dict(tool.get("arguments") or {}).get("command")
                if _looks_like_mutating_shell_command(command):
                    has_mutation_evidence = True
                    break
        if has_mutation_evidence and "workspace_effect" not in candidate_payload:
            candidate_payload["workspace_effect"] = "modified"

    candidate_payload = _candidate_payload(candidate_payload)
    if _candidate_requires_mutation_evidence(
        payload=candidate_payload,
        executed_tools=executed_tools,
    ) is not None:
        return None
    return member_id, candidate_payload


def coding_execution_organism(
    *,
    organism_id: str = "coding-organism",
    model: str | None = None,
    base_id: str = "coding-build",
) -> CodingOrganism:
    """Build the coding organism preset."""

    orchestrator_address = CellAddress(
        cell_id=f"{base_id}.orchestrator",
        organ_id=f"{base_id}.orchestrator",
        organism_id=organism_id,
    )
    return CodingOrganism(
        organism_id=organism_id,
        base_id=base_id,
        orchestrator_address=orchestrator_address,
        orchestrator_worker=build_coding_orchestrator_worker(
            worker_id=orchestrator_address.cell_id,
            model=model,
        ),
        worker_model=model,
        aggregator_organ=coding_aggregation_organ(
            organism_id=organism_id,
            model=model,
            organ_id=f"{base_id}.aggregation",
        ),
        validator_organ=universal_validator_organ(
            organism_id=organism_id,
            model=model,
            organ_id=f"{base_id}.validator",
            review_tie_break_priority=["repair", "pass"],
        ),
        compact_validator_organ=compact_universal_validator_organ(
            organism_id=organism_id,
            model=model,
            organ_id=f"{base_id}.validator",
        ),
        metadata={
            "coding_flow": "orchestrator -> worker pool -> direct single-worker candidate or aggregation fallback -> validator",
        },
    )


async def execute_coding_organism(
    *,
    executor: WorkerCoreExecutor,
    organism: CodingOrganism,
    task: CodingTask,
    trace_log: CrossCellTraceLog | None = None,
    event_callback: OrganismEventCallback | None = None,
    trace_id: str | None = None,
) -> CodingOrganismExecution:
    """Run the coding organism end to end."""

    def _emit(event: str, **payload: Any) -> None:
        if event_callback is None:
            return
        event_callback({"event": event, **payload})

    def _emit_status_update(
        *,
        actor: str,
        phase: str,
        attempt: int,
        message: str,
        status: str = "running",
        replace_last: bool = False,
        **payload: Any,
    ) -> None:
        text = _clean_text(message)
        if not text:
            return
        _emit(
            "status.update",
            actor=actor,
            phase=phase,
            attempt=attempt,
            status=status,
            message=text,
            replace_last=replace_last,
            **payload,
        )

    trace_log = trace_log or CrossCellTraceLog()
    stage_records: list[OrganismStageRecord] = []
    orchestrator_runs: list[HandoffExecution] = []
    worker_pool_executions: list[TissueExecution] = []
    aggregation_executions: list[OrganExecution] = []
    validation_executions: list[OrganExecution] = []
    repair_history: list[dict[str, Any]] = []
    material_yield_history: list[dict[str, Any]] = []
    readiness_ledger_history: list[dict[str, Any]] = []
    incremental_readiness_history: list[dict[str, Any]] = []
    partial_aggregation_history: list[dict[str, Any]] = []
    incremental_validation_history: list[dict[str, Any]] = []
    incremental_validation_precheck_by_candidate_key: dict[str, dict[str, Any]] = {}
    speculative_validation_history: list[dict[str, Any]] = []
    speculative_validation_tasks_by_candidate_key: dict[str, asyncio.Task[OrganExecution]] = {}
    speculative_validation_context_by_candidate_key: dict[str, dict[str, Any]] = {}

    best_score = -1.0
    best_attempt: int | None = None
    best_candidate_output: dict[str, Any] = {}
    best_candidate_source: str | None = None
    latest_candidate_output: dict[str, Any] = {}
    latest_candidate_source: str | None = None
    best_aggregation: OrganExecution | None = None
    best_validation: OrganExecution | None = None
    best_pass_threshold: float | None = None
    aggregation_failed_error: str | None = None

    def _consume_speculative_validation_task(task_obj: asyncio.Task[OrganExecution]) -> None:
        try:
            task_obj.result()
        except asyncio.CancelledError:
            return
        except Exception:
            return

    async def _run_speculative_model_validation(
        *,
        record: dict[str, Any],
        selected_validator_organ: OrganPattern,
        validation_packet: CellHandoffPacket,
        candidate_key: str,
        candidate_id: str | None,
        attempt: int,
        worker_count: int,
        validation_precheck: dict[str, Any],
    ) -> OrganExecution:
        _emit(
            "validation.speculative_model.started",
            stage="validation",
            source_stage="aggregation",
            attempt=attempt,
            worker_count=worker_count,
            candidate_id=candidate_id,
            candidate_key=candidate_key,
            validator_organ_id=selected_validator_organ.organ_id,
            policy_source=validation_precheck["policy_source"],
            message="Started compact model validation from a complete incremental merge candidate.",
        )
        try:
            execution = await execute_organ_pattern(
                executor=executor,
                pattern=selected_validator_organ,
                packet=validation_packet,
                trace_log=trace_log,
            )
        except asyncio.CancelledError:
            record["status"] = "cancelled"
            raise
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = str(exc)
            _emit(
                "validation.speculative_model.failed",
                stage="validation",
                attempt=attempt,
                candidate_id=candidate_id,
                candidate_key=candidate_key,
                error=str(exc),
            )
            raise

        score = None
        if execution.result.status == "completed":
            score = float(execution.result.outputs.get("overall_score") or 0.0)
        record.update(
            {
                "status": execution.result.status,
                "score": score,
                "passed": bool(execution.result.outputs.get("passed")),
            }
        )
        _emit(
            "validation.speculative_model.completed",
            stage="validation",
            source_stage="aggregation",
            attempt=attempt,
            worker_count=worker_count,
            candidate_id=candidate_id,
            candidate_key=candidate_key,
            status=execution.result.status,
            score=score,
            passed=bool(execution.result.outputs.get("passed")),
            missing_requirements=list(execution.result.outputs.get("missing_requirements") or []),
            repair_brief=str(execution.result.outputs.get("repair_brief") or ""),
            message="Completed compact model validation for the incremental merge candidate.",
        )
        return execution

    def _start_speculative_model_validation(
        *,
        selected_validator_organ: OrganPattern,
        validation_packet: CellHandoffPacket,
        candidate_key: str,
        candidate_id: str | None,
        candidate_source: str,
        validation_precheck: dict[str, Any],
        attempt: int,
        worker_count: int,
        fragment_count: int,
        owner_paths: Sequence[str],
        admission: dict[str, Any],
    ) -> None:
        if candidate_key in speculative_validation_tasks_by_candidate_key:
            return
        record = {
            "attempt": attempt,
            "worker_count": worker_count,
            "candidate_key": candidate_key,
            "candidate_id": candidate_id,
            "candidate_source": candidate_source,
            "validator_organ_id": selected_validator_organ.organ_id,
            "policy_source": validation_precheck["policy_source"],
            "fragment_count": int(fragment_count),
            "owner_paths": list(owner_paths),
            "status": "scheduled",
            "admission": dict(admission),
            "reused_at_final_validation": False,
        }
        speculative_validation_history.append(record)
        speculative_validation_context_by_candidate_key[candidate_key] = {
            "candidate_source": candidate_source,
            "validator_organ_id": selected_validator_organ.organ_id,
            "policy_source": validation_precheck["policy_source"],
            "validation_precheck": dict(validation_precheck),
            "record": record,
        }
        task_obj = asyncio.create_task(
            _run_speculative_model_validation(
                record=record,
                selected_validator_organ=selected_validator_organ,
                validation_packet=validation_packet,
                candidate_key=candidate_key,
                candidate_id=candidate_id,
                attempt=attempt,
                worker_count=worker_count,
                validation_precheck=validation_precheck,
            )
        )
        task_obj.add_done_callback(_consume_speculative_validation_task)
        speculative_validation_tasks_by_candidate_key[candidate_key] = task_obj

    def _discard_speculative_model_validation(
        *,
        candidate_key: str,
        reason: str,
    ) -> None:
        task_obj = speculative_validation_tasks_by_candidate_key.get(candidate_key)
        if task_obj is None or task_obj.done():
            return
        context = speculative_validation_context_by_candidate_key.get(candidate_key) or {}
        record = context.get("record")
        if isinstance(record, dict):
            record["status"] = "cancelled"
            record["discard_reason"] = reason
        _emit(
            "validation.speculative_model.discarded",
            stage="validation",
            attempt=record.get("attempt") if isinstance(record, dict) else None,
            worker_count=record.get("worker_count") if isinstance(record, dict) else None,
            candidate_id=(
                record.get("candidate_id")
                if isinstance(record, dict)
                else context.get("candidate_id")
            ),
            candidate_key=candidate_key,
            reason=reason,
        )
        task_obj.cancel()

    prior_packet: CellHandoffPacket | None = None
    prior_signal_id: str | None = None
    repair_brief = task.repair_brief
    previous_validation_payload: dict[str, Any] = {}
    resolved_trace_id = trace_id or _trace(task.task_id, organism.organism_id).trace_id
    _emit(
        "organism.started",
        trace_id=resolved_trace_id,
        organism_id=organism.organism_id,
        task_id=task.task_id,
        objective=task.objective,
        max_repair_rounds=organism.max_repair_rounds,
    )

    for attempt in range(1, organism.max_repair_rounds + 2):
        _emit(
            "attempt.started",
            trace_id=resolved_trace_id,
            organism_id=organism.organism_id,
            task_id=task.task_id,
            attempt=attempt,
            reason=("initial" if attempt == 1 else "repair"),
            repair_brief=repair_brief,
        )
        if attempt == 1:
            orchestrator_packet = _root_orchestrator_packet(
                organism=organism,
                task=task,
                trace_id=resolved_trace_id,
            )
        else:
            assert prior_packet is not None and prior_signal_id is not None
            orchestrator_packet = _repair_orchestrator_packet(
                organism=organism,
                task=task,
                attempt=attempt,
                parent_packet=prior_packet,
                parent_signal_id=prior_signal_id,
                repair_history=repair_history,
                previous_validation=previous_validation_payload,
                repair_brief=repair_brief,
            )

        _emit(
            "stage.started",
            trace_id=resolved_trace_id,
            organism_id=organism.organism_id,
            task_id=task.task_id,
            stage="orchestration",
            attempt=attempt,
            message=(
                "Planning the coding attempt."
                if attempt == 1
                else "Planning the repair attempt from validator feedback."
            ),
        )
        orchestrator_run = await execute_cell_handoff(
            executor=executor,
            worker=organism.orchestrator_worker,
            packet=orchestrator_packet,
            trace_log=trace_log,
        )
        orchestrator_runs.append(orchestrator_run)
        orchestrator_payload = _parse_payload(orchestrator_run.result.outputs)
        plan: CodingOrchestratorPlan | None = None
        if orchestrator_run.result.status == "completed":
            plan = _normalize_orchestrator_plan(
                outputs=orchestrator_run.result.outputs,
                organism=organism,
                task=task,
                repair_brief=(repair_brief if attempt > 1 else ""),
            )
        stage_records.append(
            _record(
                stage="orchestration",
                attempt=attempt,
                packet=orchestrator_packet,
                status=orchestrator_run.result.status,
                summary=orchestrator_run.signals[-1].summary,
                organ_id=f"{organism.base_id}.orchestrator",
                output_keys=sorted(orchestrator_payload),
            )
        )
        _emit(
            "stage.completed",
            stage="orchestration",
            attempt=attempt,
            status=orchestrator_run.result.status,
            worker_count=(plan.worker_count if plan is not None else orchestrator_payload.get("worker_count")),
            worker_briefs=(
                list(plan.worker_briefs)
                if plan is not None
                else list(orchestrator_payload.get("worker_briefs") or [])
            ),
            pass_threshold=(plan.pass_threshold if plan is not None else orchestrator_payload.get("pass_threshold")),
            scheduler_policy_source=(
                _clean_text(plan.scheduler_policy_source)
                if plan is not None
                else ""
            ),
            scheduler_decision=(
                dict(plan.scheduler_decision)
                if plan is not None and plan.scheduler_decision
                else None
            ),
            message=(
                f"Planned {(plan.worker_count if plan is not None else orchestrator_payload.get('worker_count'))} coding workers."
                if orchestrator_run.result.status == "completed"
                else "Could not complete planning."
            ),
        )
        if orchestrator_run.result.status != "completed":
            prior_packet = orchestrator_packet
            prior_signal_id = orchestrator_run.signals[-1].signal_id
            break

        assert plan is not None
        _emit_status_update(
            actor="orchestrator",
            phase="orchestration",
            attempt=attempt,
            status="completed",
            message=_plan_status_message(plan),
            public_response=plan.public_response,
            worker_count=plan.worker_count,
            worker_briefs=list(plan.worker_briefs),
            aggregation_focus=plan.aggregation_focus,
            validator_focus=plan.validator_focus,
            scheduler_policy_source=_clean_text(plan.scheduler_policy_source),
            scheduler_decision=dict(plan.scheduler_decision),
        )
        if plan.scheduler_decision:
            _emit(
                "scheduler.action.selected",
                stage="orchestration",
                attempt=attempt,
                scheduler_policy_source=_clean_text(plan.scheduler_policy_source),
                **dict(plan.scheduler_decision),
            )
        worker_pool_pattern, worker_pool_address = _worker_pool_pattern(
            organism=organism,
            attempt=attempt,
            plan=plan,
        )
        worker_pool_owner_paths = [
            _artifact_path_key(dict(member.metadata or {}).get("exclusive_write_owner_path"))
            for member in worker_pool_pattern.members
            if _artifact_path_key(dict(member.metadata or {}).get("exclusive_write_owner_path"))
        ]
        partial_aggregation_fragments_by_member: dict[str, dict[str, Any]] = {}
        partial_aggregation_executions_by_member: dict[str, HandoffExecution] = {}
        _emit(
            "stage.started",
            stage="workers",
            attempt=attempt,
            worker_count=plan.worker_count,
            message=f"Running {plan.worker_count} coding workers.",
        )
        worker_packet = _child_packet(
            sender=orchestrator_packet.recipient,
            recipient=worker_pool_address,
            parent_packet=orchestrator_packet,
            parent_signal_id=orchestrator_run.signals[-1].signal_id,
            lineage_suffix=f"tissue:{worker_pool_pattern.pattern_id}",
            task_id=f"{task.task_id}:workers:{attempt}",
            instruction="Run the bounded coding worker pool according to the assigned briefs.",
            scope="coding-organism.workers",
            hard_constraints=list(task.hard_constraints),
            soft_constraints=list(task.soft_constraints),
            input_payload={
                "objective": task.objective,
                "acceptance_criteria": list(task.acceptance_criteria),
                "research_findings": list(task.research_findings),
                "repair_brief": repair_brief,
                "session_context": dict(task.session_context),
                "orchestration_plan": plan.model_dump(mode="json"),
                "attempt": attempt,
            },
            evidence_refs=task.evidence_refs,
            output_contract=_worker_output_contract(),
            authority=WorkerAuthority.DELEGATE,
            metadata={
                "organism_id": organism.organism_id,
                "organism_stage": "workers",
                "worker_count": plan.worker_count,
            },
        )
        def _on_worker_member_completed(
            member: TissueMember,
            _member_packet: CellHandoffPacket,
            member_execution: HandoffExecution,
        ) -> None:
            payload = _parse_payload(dict(member_execution.result.outputs or {}))
            owner_path = _artifact_path_key(
                dict(member.metadata or {}).get("exclusive_write_owner_path")
            )
            lane = _artifact_readiness_lane(
                plan=plan,
                member_id=member.member_id,
                owner_path=owner_path,
                payload=payload,
                executed_tools=_executed_tools_from_handoff_execution(member_execution),
            )
            summary = _artifact_readiness_summary(
                plan=plan,
                lanes=[lane],
                owner_paths=[owner_path] if owner_path else [],
            )
            record = {
                "attempt": attempt,
                "worker_count": plan.worker_count,
                "member_id": member.member_id,
                **summary,
            }
            incremental_readiness_history.append(record)
            _emit(
                "scheduler.readiness.member_evaluated",
                stage="workers",
                attempt=attempt,
                worker_count=plan.worker_count,
                member_id=member.member_id,
                **summary,
            )
            if lane.get("ready_for_downstream"):
                _emit(
                    "scheduler.downstream.unlocked",
                    stage="workers",
                    attempt=attempt,
                    worker_count=plan.worker_count,
                    member_id=member.member_id,
                    owner_path=owner_path,
                    readiness_source=lane.get("readiness_source"),
                    unlocked_tasks=(
                        ["aggregation:owner_fragment"]
                        if owner_path
                        else ["aggregation:candidate_fragment"]
                    ),
                    message="A worker lane produced a downstream-ready artifact before the worker pool barrier completed.",
                )
                fragment = _partial_aggregation_fragment(
                    member_id=member.member_id,
                    owner_path=owner_path,
                    payload=payload,
                    executed_tools=_executed_tools_from_handoff_execution(member_execution),
                )
                if fragment is not None:
                    partial_aggregation_fragments_by_member[member.member_id] = fragment
                    partial_aggregation_executions_by_member[member.member_id] = member_execution
                    partial_progress = _partial_aggregation_progress(
                        fragments=list(partial_aggregation_fragments_by_member.values()),
                        owner_paths=worker_pool_owner_paths,
                    )
                    partial_record = {
                        "attempt": attempt,
                        "worker_count": plan.worker_count,
                        "member_id": member.member_id,
                        "owner_path": owner_path,
                        **partial_progress,
                    }
                    partial_aggregation_history.append(partial_record)
                    _emit(
                        "scheduler.downstream.started",
                        stage="aggregation",
                        source_stage="workers",
                        attempt=attempt,
                        worker_count=plan.worker_count,
                        member_id=member.member_id,
                        owner_path=owner_path,
                        downstream_task_id="aggregation:partial_reduce",
                        message="Started deterministic partial aggregation from a downstream-ready worker lane.",
                    )
                    _emit(
                        "aggregation.partial_reduced",
                        stage="aggregation",
                        source_stage="workers",
                        attempt=attempt,
                        worker_count=plan.worker_count,
                        member_id=member.member_id,
                        owner_path=owner_path,
                        **partial_progress,
                    )
                    incremental_candidate = _incremental_worker_merge_candidate(
                        fragments=list(partial_aggregation_fragments_by_member.values()),
                        owner_paths=worker_pool_owner_paths,
                        attempt=attempt,
                    )
                    if incremental_candidate is not None:
                        candidate_key = _candidate_readiness_key(incremental_candidate)
                        if candidate_key not in incremental_validation_precheck_by_candidate_key:
                            precheck_record = _incremental_validation_precheck_record(
                                task=task,
                                plan=plan,
                                candidate_payload=incremental_candidate,
                                candidate_source="worker_merge",
                                attempt=attempt,
                                fragment_count=int(partial_progress["fragment_count"]),
                                owner_paths=worker_pool_owner_paths,
                            )
                            precheck_record["worker_count"] = plan.worker_count
                            incremental_validation_history.append(precheck_record)
                            incremental_validation_precheck_by_candidate_key[candidate_key] = (
                                precheck_record
                            )
                            incremental_candidate_view = _candidate_view(incremental_candidate)
                            _emit(
                                "scheduler.downstream.started",
                                stage="validation",
                                source_stage="aggregation",
                                attempt=attempt,
                                worker_count=plan.worker_count,
                                downstream_task_id="validation:deterministic_precheck",
                                candidate_id=incremental_candidate_view.candidate_id,
                                candidate_key=candidate_key,
                                fragment_count=int(partial_progress["fragment_count"]),
                                owner_paths=worker_pool_owner_paths,
                                message="Started deterministic validation precheck from a complete incremental merge candidate.",
                            )
                            _emit(
                                "validation.precheck.incremental_completed",
                                stage="validation",
                                source_stage="aggregation",
                                attempt=attempt,
                                worker_count=plan.worker_count,
                                candidate_id=incremental_candidate_view.candidate_id,
                                candidate_key=candidate_key,
                                policy_source=precheck_record["policy_source"],
                                blocking_errors=list(precheck_record["blocking_errors"]),
                                warnings=list(precheck_record["warnings"]),
                                checks=list(precheck_record["checks"]),
                            )
                            selected_speculative_validator = _select_validation_organ(
                                organism=organism,
                                validation_precheck=precheck_record["validation_precheck"],
                            )
                            speculative_admission = _speculative_model_validation_admission(
                                validation_precheck=precheck_record["validation_precheck"],
                                selected_validator_organ=selected_speculative_validator,
                            )
                            precheck_record["speculative_model_admission"] = dict(
                                speculative_admission
                            )
                            if speculative_admission["admitted"]:
                                speculative_evidence_refs = list(task.evidence_refs)
                                for execution in partial_aggregation_executions_by_member.values():
                                    speculative_evidence_refs.extend(
                                        _completion_output_refs(execution.signals)
                                    )
                                speculative_packet = _validation_packet(
                                    task=task,
                                    plan=plan,
                                    selected_validator_organ=selected_speculative_validator,
                                    parent_packet=_member_packet,
                                    parent_signal_id=member_execution.signals[-1].signal_id,
                                    candidate_payload=incremental_candidate,
                                    candidate_source="worker_merge",
                                    validation_precheck=precheck_record["validation_precheck"],
                                    attempt=attempt,
                                    worker_count=plan.worker_count,
                                    best_score_so_far=(
                                        None if best_score < 0 else best_score
                                    ),
                                    repair_brief=repair_brief,
                                    evidence_refs=speculative_evidence_refs,
                                    task_id=f"{task.task_id}:validate:{attempt}:speculative",
                                    metadata={
                                        "speculative_validation": True,
                                        "candidate_key": candidate_key,
                                    },
                                )
                                _start_speculative_model_validation(
                                    selected_validator_organ=selected_speculative_validator,
                                    validation_packet=speculative_packet,
                                    candidate_key=candidate_key,
                                    candidate_id=incremental_candidate_view.candidate_id,
                                    candidate_source="worker_merge",
                                    validation_precheck=precheck_record["validation_precheck"],
                                    attempt=attempt,
                                    worker_count=plan.worker_count,
                                    fragment_count=int(partial_progress["fragment_count"]),
                                    owner_paths=worker_pool_owner_paths,
                                    admission=speculative_admission,
                                )
                            else:
                                _emit(
                                    "validation.speculative_model.skipped",
                                    stage="validation",
                                    source_stage="aggregation",
                                    attempt=attempt,
                                    worker_count=plan.worker_count,
                                    candidate_id=incremental_candidate_view.candidate_id,
                                    candidate_key=candidate_key,
                                    reason=speculative_admission["reason"],
                                    blockers=list(speculative_admission["blockers"]),
                                )

        worker_execution = await execute_tissue_pattern(
            executor=executor,
            pattern=worker_pool_pattern,
            packet=worker_packet,
            trace_log=trace_log,
            member_completion_callback=_on_worker_member_completed,
        )
        worker_pool_executions.append(worker_execution)
        stage_records.append(
            _record(
                stage="workers",
                attempt=attempt,
                packet=worker_packet,
                status=worker_execution.result.status,
                summary=worker_execution.signals[-1].summary,
                organ_id=f"{organism.base_id}.worker-pool",
                output_keys=sorted(worker_execution.result.outputs),
            )
        )
        _emit(
            "stage.completed",
            stage="workers",
            attempt=attempt,
            status=worker_execution.result.status,
            worker_count=plan.worker_count,
            successful_workers=list(worker_execution.result.metadata.get("successful_member_ids") or []),
            message=(
                f"Worker pool finished with {len(worker_execution.result.metadata.get('successful_member_ids') or [])} successful workers."
                if worker_execution.result.status == "completed"
                else "Worker pool did not complete."
            ),
        )
        if worker_execution.result.status != "completed":
            prior_packet = worker_packet
            prior_signal_id = worker_execution.signals[-1].signal_id
            break

        member_results = dict(worker_execution.result.outputs.get("member_results") or {})
        material_yield = _worker_material_yield_metrics(
            member_results=member_results,
            worker_execution=worker_execution,
        )
        material_yield_history.append(
            {
                "attempt": attempt,
                "worker_count": plan.worker_count,
                "scheduler_policy_source": _clean_text(plan.scheduler_policy_source),
                **material_yield,
            }
        )
        _emit(
            "worker.material_yield.measured",
            stage="workers",
            attempt=attempt,
            worker_count=plan.worker_count,
            scheduler_policy_source=_clean_text(plan.scheduler_policy_source),
            **material_yield,
        )
        readiness_ledger = _artifact_readiness_ledger(
            plan=plan,
            member_results=member_results,
            worker_execution=worker_execution,
        )
        readiness_ledger_record = {
            "attempt": attempt,
            "worker_count": plan.worker_count,
            **readiness_ledger,
        }
        readiness_ledger_history.append(readiness_ledger_record)
        _emit(
            "scheduler.readiness.evaluated",
            stage="workers",
            attempt=attempt,
            worker_count=plan.worker_count,
            **readiness_ledger,
        )
        for member_id in sorted(member_results):
            member_result = _parse_payload(dict(member_results.get(member_id) or {}))
            member_candidate = _candidate_view(member_result)
            _emit_status_update(
                actor=member_id,
                phase="workers",
                attempt=attempt,
                status="completed",
                message=_worker_status_message(member_result),
                target_files=list(member_candidate.target_files),
                test_plan=list(member_candidate.test_plan),
            )

        direct_worker_candidate: tuple[str, dict[str, Any]] | None = None
        if plan.worker_count == 1:
            direct_worker_candidate = _promote_single_worker_candidate(
                attempt=attempt,
                worker_execution=worker_execution,
            )

        _emit(
            "stage.started",
            stage="aggregation",
            attempt=attempt,
            message="Merging worker output into one bounded candidate.",
        )
        candidate_payload: dict[str, Any] = {}
        candidate_source = "aggregation"
        candidate_output_refs: list[EvidenceRef] = []
        validation_parent_packet = worker_packet
        validation_parent_signal_id = worker_execution.signals[-1].signal_id

        if direct_worker_candidate is not None:
            direct_member_id, candidate_payload = direct_worker_candidate
            candidate_source = "worker"
            aggregation_candidate = _candidate_view(candidate_payload)
            stage_records.append(
                _record(
                    stage="aggregation",
                    attempt=attempt,
                    packet=worker_packet,
                    status="completed",
                    summary=f"Promoted {direct_member_id} as the direct bounded candidate.",
                    organ_id=f"{organism.base_id}.worker-pool",
                    output_keys=sorted(candidate_payload),
                )
            )
            _emit(
                "stage.completed",
                stage="aggregation",
                attempt=attempt,
                status="completed",
                candidate_source=candidate_source,
                candidate_id=aggregation_candidate.candidate_id,
                target_files=list(aggregation_candidate.target_files),
                test_plan=list(aggregation_candidate.test_plan),
                workspace_effect=_candidate_workspace_effect(aggregation_candidate),
                message="Using the single-worker candidate directly.",
            )
            _emit_status_update(
                actor="aggregator",
                phase="aggregation",
                attempt=attempt,
                status="completed",
                message=f"Using {direct_member_id} as the bounded candidate.",
                candidate_source=candidate_source,
                candidate_id=aggregation_candidate.candidate_id,
                target_files=list(aggregation_candidate.target_files),
                test_plan=list(aggregation_candidate.test_plan),
                workspace_effect=_candidate_workspace_effect(aggregation_candidate),
            )
        else:
            if not _worker_results_have_material_for_aggregation(member_results):
                aggregation_failed_error = (
                    "Workers produced no material bounded candidate to merge; stop and re-plan instead of rediscovering the workspace in aggregation."
                )
                stage_records.append(
                    _record(
                        stage="aggregation",
                        attempt=attempt,
                        packet=worker_packet,
                        status="failed",
                        summary=aggregation_failed_error,
                        organ_id=organism.aggregator_organ.organ_id,
                        output_keys=[],
                    )
                )
                _emit(
                    "stage.completed",
                    stage="aggregation",
                    attempt=attempt,
                    status="failed",
                    candidate_source=candidate_source,
                    candidate_id=None,
                    target_files=[],
                    test_plan=[],
                    workspace_effect=None,
                    scheduler_policy_source=_clean_text(plan.scheduler_policy_source),
                    message="Workers did not produce a material candidate for aggregation.",
                )
                _emit_status_update(
                    actor="aggregator",
                    phase="aggregation",
                    attempt=attempt,
                    status="failed",
                    message=aggregation_failed_error,
                    candidate_source=candidate_source,
                    candidate_id=None,
                    target_files=[],
                    test_plan=[],
                    workspace_effect=None,
                    scheduler_policy_source=_clean_text(plan.scheduler_policy_source),
                )
                if attempt <= organism.max_repair_rounds:
                    repair_brief = _aggregation_failure_repair_brief(
                        aggregation_failed_error,
                        parallel_owner_plan=_plan_has_parallel_artifact_owners(plan),
                    )
                    previous_validation_payload = {
                        "passed": False,
                        "overall_score": 0.0,
                        "dimension_scores": {},
                        "repair_brief": repair_brief,
                        "missing_requirements": ["material bounded candidate"],
                        "comparison_note": aggregation_failed_error,
                    }
                    repair_history.append(
                        {
                            "attempt": attempt,
                            "worker_count": plan.worker_count,
                            "worker_briefs": list(plan.worker_briefs),
                            "candidate_id": None,
                            "score": None,
                            "passed": False,
                            "pass_threshold": float(plan.pass_threshold),
                            "repair_brief": repair_brief,
                        }
                    )
                    _emit(
                        "repair.requested",
                        attempt=attempt,
                        score=None,
                        repair_brief=repair_brief,
                        missing_requirements=["material bounded candidate"],
                        source="aggregation",
                    )
                    _emit_status_update(
                        actor="aggregator",
                        phase="repair",
                        attempt=attempt,
                        status="needs_repair",
                        message=repair_brief,
                        missing_requirements=["material bounded candidate"],
                    )
                    prior_packet = worker_packet
                    prior_signal_id = worker_execution.signals[-1].signal_id
                    continue
                prior_packet = worker_packet
                prior_signal_id = worker_execution.signals[-1].signal_id
                break
            if not candidate_payload:
                missing_owner_paths = _missing_exclusive_owner_material_paths(
                    member_results=member_results,
                    worker_execution=worker_execution,
                )
            else:
                missing_owner_paths = []
            if missing_owner_paths:
                aggregation_failed_error = (
                    "Required parallel owner lanes produced no material mutation evidence: "
                    + ", ".join(missing_owner_paths)
                )
                stage_records.append(
                    _record(
                        stage="aggregation",
                        attempt=attempt,
                        packet=worker_packet,
                        status="failed",
                        summary=aggregation_failed_error,
                        organ_id=organism.aggregator_organ.organ_id,
                        output_keys=[],
                    )
                )
                _emit(
                    "stage.completed",
                    stage="aggregation",
                    attempt=attempt,
                    status="failed",
                    candidate_source=candidate_source,
                    candidate_id=None,
                    target_files=[],
                    test_plan=[],
                    workspace_effect=None,
                    missing_owner_paths=missing_owner_paths,
                    message="Workers did not cover every required exclusive owner lane.",
                )
                _emit_status_update(
                    actor="aggregator",
                    phase="aggregation",
                    attempt=attempt,
                    status="failed",
                    message=aggregation_failed_error,
                    candidate_source=candidate_source,
                    candidate_id=None,
                    target_files=[],
                    test_plan=[],
                    workspace_effect=None,
                )
                if attempt <= organism.max_repair_rounds:
                    repair_brief = _aggregation_failure_repair_brief(
                        aggregation_failed_error,
                        parallel_owner_plan=True,
                    )
                    missing_requirements = [
                        f"material mutation evidence for {path}"
                        for path in missing_owner_paths
                    ]
                    previous_validation_payload = {
                        "passed": False,
                        "overall_score": 0.0,
                        "dimension_scores": {},
                        "repair_brief": repair_brief,
                        "missing_requirements": missing_requirements,
                        "comparison_note": aggregation_failed_error,
                    }
                    repair_history.append(
                        {
                            "attempt": attempt,
                            "worker_count": plan.worker_count,
                            "worker_briefs": list(plan.worker_briefs),
                            "candidate_id": None,
                            "score": None,
                            "passed": False,
                            "pass_threshold": float(plan.pass_threshold),
                            "repair_brief": repair_brief,
                        }
                    )
                    _emit(
                        "repair.requested",
                        attempt=attempt,
                        score=None,
                        repair_brief=repair_brief,
                        missing_requirements=missing_requirements,
                        source="aggregation",
                    )
                    _emit_status_update(
                        actor="aggregator",
                        phase="repair",
                        attempt=attempt,
                        status="needs_repair",
                        message=repair_brief,
                        missing_requirements=missing_requirements,
                    )
                    prior_packet = worker_packet
                    prior_signal_id = worker_execution.signals[-1].signal_id
                    continue
                prior_packet = worker_packet
                prior_signal_id = worker_execution.signals[-1].signal_id
                break
            incremental_merge = _incremental_worker_merge_candidate(
                fragments=list(partial_aggregation_fragments_by_member.values()),
                owner_paths=worker_pool_owner_paths,
                attempt=attempt,
            )
            deterministic_merge = (
                None
                if candidate_payload
                else (
                    incremental_merge
                    or (
                        _deterministic_worker_merge_candidate(
                            member_results=member_results,
                            worker_execution=worker_execution,
                            attempt=attempt,
                        )
                        if _plan_has_parallel_exclusive_write_owners(plan)
                        else None
                    )
                )
            )
            if deterministic_merge is not None:
                candidate_payload = deterministic_merge
                hygiene_report = _apply_frontend_contract_hygiene(
                    task=task,
                    candidate_payload=candidate_payload,
                    owner_paths=_exclusive_owner_paths(worker_execution),
                )
                candidate_source = "worker_merge"
                aggregation_candidate = _candidate_view(candidate_payload)
                if candidate_payload.get("synthesized_from_incremental_reducer"):
                    _emit(
                        "aggregation.incremental_merge.reused",
                        stage="aggregation",
                        attempt=attempt,
                        candidate_id=aggregation_candidate.candidate_id,
                        fragment_count=len(partial_aggregation_fragments_by_member),
                        owner_paths=worker_pool_owner_paths,
                        message="Reused deterministic partial aggregation fragments for the final worker merge.",
                    )
                if hygiene_report is not None:
                    _emit(
                        "contract_hygiene.applied",
                        stage="aggregation",
                        attempt=attempt,
                        candidate_source=candidate_source,
                        candidate_id=aggregation_candidate.candidate_id,
                        changed_files=list(hygiene_report.get("changed_files") or []),
                        repairs=list(hygiene_report.get("repairs") or []),
                        errors=list(hygiene_report.get("errors") or []),
                        message="Applied deterministic frontend contract hygiene before validation.",
                    )
                stage_records.append(
                    _record(
                        stage="aggregation",
                        attempt=attempt,
                        packet=worker_packet,
                        status="completed",
                        summary="Synthesized a deterministic worker-merge candidate from covered owner lanes.",
                        organ_id=f"{organism.base_id}.worker-pool",
                        output_keys=sorted(candidate_payload),
                    )
                )
                _emit(
                    "stage.completed",
                    stage="aggregation",
                    attempt=attempt,
                    status="completed",
                    candidate_source=candidate_source,
                    candidate_id=aggregation_candidate.candidate_id,
                    target_files=list(aggregation_candidate.target_files),
                    test_plan=list(aggregation_candidate.test_plan),
                    workspace_effect=_candidate_workspace_effect(aggregation_candidate),
                    message="Using deterministic worker-merge candidate from covered owner lanes.",
                )
                _emit_status_update(
                    actor="aggregator",
                    phase="aggregation",
                    attempt=attempt,
                    status="completed",
                    message="Using deterministic worker-merge candidate from covered owner lanes.",
                    candidate_source=candidate_source,
                    candidate_id=aggregation_candidate.candidate_id,
                    target_files=list(aggregation_candidate.target_files),
                    test_plan=list(aggregation_candidate.test_plan),
                    workspace_effect=_candidate_workspace_effect(aggregation_candidate),
                )
            elif not candidate_payload:
                aggregation_packet = _child_packet(
                    sender=worker_packet.recipient,
                    recipient=organism.aggregator_organ.boundary_address,
                    parent_packet=worker_packet,
                    parent_signal_id=worker_execution.signals[-1].signal_id,
                    lineage_suffix=f"organ:{organism.aggregator_organ.organ_id}",
                    task_id=f"{task.task_id}:aggregate:{attempt}",
                    instruction=plan.aggregation_focus,
                    scope="coding-organism.aggregate",
                    hard_constraints=list(task.hard_constraints),
                    soft_constraints=list(task.soft_constraints),
                    input_payload={
                        "objective": task.objective,
                        "acceptance_criteria": list(task.acceptance_criteria),
                        "research_findings": list(task.research_findings),
                        "repair_brief": repair_brief,
                        "session_context": dict(task.session_context),
                        "orchestration_plan": plan.model_dump(mode="json"),
                        "worker_results": dict(worker_execution.result.outputs.get("member_results") or {}),
                    },
                    evidence_refs=[
                        *task.evidence_refs,
                        *_completion_output_refs(worker_execution.signals),
                    ],
                    output_contract=OutputContract(
                        definition_of_done="Return the aggregated bounded coding candidate.",
                        expected_return_shape=json.dumps(
                            {
                                "candidate_id": "<required>",
                                "change_summary": "<required>",
                                "target_files": "<required>",
                                "test_plan": "<required>",
                                "risks": "<required>",
                                "workspace_effect": "<optional: modified|verified>",
                            },
                            sort_keys=True,
                        ),
                    ),
                    authority=WorkerAuthority.DELEGATE,
                    metadata={
                        "organism_id": organism.organism_id,
                        "organism_stage": "aggregation",
                        "organ_id": organism.aggregator_organ.organ_id,
                    },
                )
                aggregation_execution = await execute_organ_pattern(
                    executor=executor,
                    pattern=organism.aggregator_organ,
                    packet=aggregation_packet,
                    trace_log=trace_log,
                )
                aggregation_executions.append(aggregation_execution)
                if aggregation_execution.result.outputs:
                    aggregation_execution.result.outputs = _augment_aggregation_candidate_from_worker_results(
                        _candidate_payload(dict(aggregation_execution.result.outputs)),
                        member_results=member_results,
                        worker_execution=worker_execution,
                        attempt=attempt,
                    )
                if aggregation_execution.result.status == "completed":
                    evidence_failure = _aggregation_requires_mutation_evidence(
                        payload=dict(aggregation_execution.result.outputs),
                        execution=aggregation_execution,
                        worker_execution=worker_execution,
                    )
                    if evidence_failure is not None:
                        error_text, extra_metadata = evidence_failure
                        aggregation_execution.result.status = "failed"
                        aggregation_execution.result.error = error_text
                        aggregation_execution.result.metadata = {
                            **dict(aggregation_execution.result.metadata),
                            **dict(extra_metadata),
                        }
                stage_records.append(
                    _record(
                        stage="aggregation",
                        attempt=attempt,
                        packet=aggregation_packet,
                        status=aggregation_execution.result.status,
                        summary=(
                            aggregation_execution.result.error
                            if aggregation_execution.result.status != "completed" and aggregation_execution.result.error
                            else aggregation_execution.signals[-1].summary
                        ),
                        organ_id=organism.aggregator_organ.organ_id,
                        output_keys=sorted(aggregation_execution.result.outputs),
                    )
                )
                aggregation_candidate = _candidate_view(dict(aggregation_execution.result.outputs))
                _emit(
                    "stage.completed",
                    stage="aggregation",
                    attempt=attempt,
                    status=aggregation_execution.result.status,
                    candidate_source=candidate_source,
                    candidate_id=aggregation_candidate.candidate_id,
                    target_files=list(aggregation_candidate.target_files),
                    test_plan=list(aggregation_candidate.test_plan),
                    workspace_effect=_candidate_workspace_effect(aggregation_candidate),
                    message=(
                        "Prepared one bounded coding candidate."
                        if aggregation_execution.result.status == "completed"
                        else "Could not prepare a candidate."
                    ),
                )
                _emit_status_update(
                    actor="aggregator",
                    phase="aggregation",
                    attempt=attempt,
                    status=(
                        "completed"
                        if aggregation_execution.result.status == "completed"
                        else aggregation_execution.result.status
                    ),
                    message=_aggregation_status_message(dict(aggregation_execution.result.outputs)),
                    candidate_source=candidate_source,
                    candidate_id=aggregation_candidate.candidate_id,
                    target_files=list(aggregation_candidate.target_files),
                    test_plan=list(aggregation_candidate.test_plan),
                    workspace_effect=_candidate_workspace_effect(aggregation_candidate),
                )
                if aggregation_execution.result.status != "completed":
                    prior_packet = aggregation_packet
                    prior_signal_id = aggregation_execution.signals[-1].signal_id
                    break
                candidate_payload = dict(aggregation_execution.result.outputs)
                candidate_output_refs = list(aggregation_execution.result.output_refs)
                validation_parent_packet = aggregation_packet
                validation_parent_signal_id = aggregation_execution.signals[-1].signal_id

        latest_candidate_output = dict(candidate_payload)
        latest_candidate_source = candidate_source

        _emit(
            "stage.started",
            stage="validation",
            attempt=attempt,
            message="Validating the aggregated candidate.",
        )
        validation_candidate_key = _candidate_readiness_key(candidate_payload)
        incremental_precheck_record = incremental_validation_precheck_by_candidate_key.get(
            validation_candidate_key
        )
        if (
            incremental_precheck_record is not None
            and incremental_precheck_record.get("candidate_source") == candidate_source
        ):
            validation_precheck = dict(incremental_precheck_record["validation_precheck"])
            _emit(
                "validation.precheck.reused",
                stage="validation",
                attempt=attempt,
                source="incremental_validation",
                candidate_id=_candidate_view(candidate_payload).candidate_id,
                candidate_key=validation_candidate_key,
                policy_source=validation_precheck["policy_source"],
                message="Reused deterministic validation precheck computed from the incremental merge candidate.",
            )
        else:
            validation_precheck = _deterministic_validation_precheck(
                task=task,
                plan=plan,
                candidate_payload=candidate_payload,
                candidate_source=candidate_source,
            )
        _emit(
            "validation.precheck.completed",
            stage="validation",
            attempt=attempt,
            policy_source=validation_precheck["policy_source"],
            candidate_source=candidate_source,
            blocking_errors=list(validation_precheck.get("blocking_errors") or []),
            warnings=list(validation_precheck.get("warnings") or []),
            checks=list(validation_precheck.get("checks") or []),
        )
        selected_validator_organ = _select_validation_organ(
            organism=organism,
            validation_precheck=validation_precheck,
        )
        _emit(
            "validation.policy.selected",
            stage="validation",
            attempt=attempt,
            policy_source=validation_precheck["policy_source"],
            validator_organ_id=selected_validator_organ.organ_id,
            compact_model_review=selected_validator_organ.tissue is None,
            blocking_precheck=bool(validation_precheck.get("blocking_errors")),
        )
        validation_packet = _validation_packet(
            task=task,
            plan=plan,
            selected_validator_organ=selected_validator_organ,
            parent_packet=validation_parent_packet,
            parent_signal_id=validation_parent_signal_id,
            candidate_payload=candidate_payload,
            candidate_source=candidate_source,
            validation_precheck=validation_precheck,
            attempt=attempt,
            worker_count=plan.worker_count,
            best_score_so_far=None if best_score < 0 else best_score,
            repair_brief=repair_brief,
            evidence_refs=[
                *task.evidence_refs,
                *_completion_output_refs(worker_execution.signals),
                *candidate_output_refs,
            ],
            task_id=f"{task.task_id}:validate:{attempt}",
        )
        speculative_context = speculative_validation_context_by_candidate_key.get(
            validation_candidate_key
        )
        speculative_task = speculative_validation_tasks_by_candidate_key.get(
            validation_candidate_key
        )
        speculative_reusable = (
            speculative_task is not None
            and speculative_context is not None
            and speculative_context.get("candidate_source") == candidate_source
            and speculative_context.get("validator_organ_id") == selected_validator_organ.organ_id
            and speculative_context.get("policy_source") == validation_precheck["policy_source"]
            and not validation_precheck.get("blocking_errors")
        )
        if not speculative_reusable:
            for candidate_key in list(speculative_validation_tasks_by_candidate_key):
                _discard_speculative_model_validation(
                    candidate_key=candidate_key,
                    reason=(
                        "final_candidate_changed"
                        if candidate_key != validation_candidate_key
                        else "final_validation_context_changed"
                    ),
                )
        if validation_precheck.get("blocking_errors"):
            validation_execution = _validation_precheck_execution(
                pattern=selected_validator_organ,
                packet=validation_packet,
                payload=_precheck_failed_validation_payload(validation_precheck),
                trace_log=trace_log,
            )
        elif speculative_reusable and speculative_task is not None:
            _emit(
                "validation.speculative_model.awaited",
                stage="validation",
                attempt=attempt,
                candidate_id=_candidate_view(candidate_payload).candidate_id,
                candidate_key=validation_candidate_key,
                validator_organ_id=selected_validator_organ.organ_id,
                message="Awaiting the compact validator already started from the incremental merge candidate.",
            )
            try:
                validation_execution = await speculative_task
                validation_packet = validation_execution.packet
                record = dict(speculative_context.get("record") or {})
                if isinstance(speculative_context.get("record"), dict):
                    speculative_context["record"]["reused_at_final_validation"] = True
                _emit(
                    "validation.speculative_model.reused",
                    stage="validation",
                    attempt=attempt,
                    candidate_id=_candidate_view(candidate_payload).candidate_id,
                    candidate_key=validation_candidate_key,
                    validator_organ_id=selected_validator_organ.organ_id,
                    status=validation_execution.result.status,
                    score=(
                        float(validation_execution.result.outputs.get("overall_score") or 0.0)
                        if validation_execution.result.status == "completed"
                        else None
                    ),
                    passed=bool(validation_execution.result.outputs.get("passed")),
                    speculative_status=record.get("status"),
                    message="Reused the compact model validation result started from the readiness boundary.",
                )
            except Exception as exc:
                _emit(
                    "validation.speculative_model.failed",
                    stage="validation",
                    attempt=attempt,
                    candidate_id=_candidate_view(candidate_payload).candidate_id,
                    candidate_key=validation_candidate_key,
                    error=str(exc),
                    source="final_reuse",
                )
                validation_execution = await execute_organ_pattern(
                    executor=executor,
                    pattern=selected_validator_organ,
                    packet=validation_packet,
                    trace_log=trace_log,
                )
        else:
            validation_execution = await execute_organ_pattern(
                executor=executor,
                pattern=selected_validator_organ,
                packet=validation_packet,
                trace_log=trace_log,
            )
        validation_executions.append(validation_execution)
        score = None
        if validation_execution.result.status == "completed":
            score = float(validation_execution.result.outputs.get("overall_score") or 0.0)
        stage_records.append(
            _record(
                stage="validation",
                attempt=attempt,
                packet=validation_packet,
                status=validation_execution.result.status,
                summary=validation_execution.signals[-1].summary,
                organ_id=selected_validator_organ.organ_id,
                score=score,
                output_keys=sorted(validation_execution.result.outputs),
            )
        )
        validation_passed = bool(validation_execution.result.outputs.get("passed"))
        _emit(
            "stage.completed",
            stage="validation",
            attempt=attempt,
            status=validation_execution.result.status,
            score=score,
            passed=validation_passed,
            repair_brief=str(validation_execution.result.outputs.get("repair_brief") or ""),
            missing_requirements=list(validation_execution.result.outputs.get("missing_requirements") or []),
            comparison_note=str(validation_execution.result.outputs.get("comparison_note") or ""),
            validation_policy_source=validation_precheck["policy_source"],
            deterministic_precheck=dict(validation_precheck),
            message=(
                f"Validator scored the candidate at {score:.2f}."
                if validation_execution.result.status == "completed" and score is not None
                else "Validation did not complete."
            ),
        )
        _emit_status_update(
            actor="validator",
            phase="validation",
            attempt=attempt,
            status=(
                "passed"
                if validation_passed
                else (
                    "needs_repair"
                    if validation_execution.result.status == "completed"
                    else validation_execution.result.status
                )
            ),
            message=_validation_status_message(dict(validation_execution.result.outputs)),
            score=score,
            missing_requirements=list(validation_execution.result.outputs.get("missing_requirements") or []),
            repair_brief=str(validation_execution.result.outputs.get("repair_brief") or ""),
            validation_policy_source=validation_precheck["policy_source"],
        )

        prior_packet = validation_packet
        prior_signal_id = validation_execution.signals[-1].signal_id
        previous_validation_payload = dict(validation_execution.result.outputs)

        if validation_execution.result.status != "completed":
            break

        if score is not None and score > best_score:
            best_score = score
            best_attempt = attempt
            best_candidate_output = dict(candidate_payload)
            best_candidate_source = candidate_source
            if candidate_source == "aggregation":
                best_aggregation = aggregation_execution
            best_validation = validation_execution
            best_pass_threshold = float(plan.pass_threshold)

        passed = validation_passed
        repair_brief = str(validation_execution.result.outputs.get("repair_brief") or "")
        repair_history.append(
            {
                "attempt": attempt,
                "worker_count": plan.worker_count,
                "worker_briefs": list(plan.worker_briefs),
                "candidate_id": candidate_payload.get("candidate_id"),
                "score": score,
                "passed": passed,
                "pass_threshold": float(plan.pass_threshold),
                "repair_brief": repair_brief,
            }
        )
        if not passed:
            _emit(
                "repair.requested",
                attempt=attempt,
                score=score,
                repair_brief=repair_brief,
                missing_requirements=list(validation_execution.result.outputs.get("missing_requirements") or []),
            )
            _emit_status_update(
                actor="validator",
                phase="repair",
                attempt=attempt,
                status="needs_repair",
                message=repair_brief,
                missing_requirements=list(validation_execution.result.outputs.get("missing_requirements") or []),
            )
        if passed and score is not None and score >= plan.pass_threshold:
            break

    trace_id = (
        orchestrator_runs[0].packet.trace.trace_id
        if orchestrator_runs
        else resolved_trace_id
    )
    observability = OrganismObservability(
        trace_id=trace_id,
        stage_records=stage_records,
        trace_rows=trace_log.inspect_trace(trace_id),
    )
    validation_scores = [
        float(execution.result.outputs.get("overall_score") or 0.0)
        for execution in validation_executions
        if execution.result.status == "completed"
    ]

    final_output: dict[str, Any] = {}
    status: Literal["completed", "failed"] = "failed"
    error: str | None = None
    selected_score = best_score if best_score >= 0 else None
    selected_pass_threshold = best_pass_threshold
    delivery_pass_tolerance = float(organism.delivery_pass_tolerance)
    fallback_candidate_output = (
        dict(best_candidate_output)
        or dict(latest_candidate_output)
        or _salvage_candidate_output(
            best_aggregation if best_aggregation is not None else (aggregation_executions[-1] if aggregation_executions else None)
        )
    )
    if best_validation is not None and fallback_candidate_output:
        validator_passed = bool(best_validation.result.outputs.get("passed"))
        within_delivery_tolerance = (
            validator_passed
            and selected_score is not None
            and selected_pass_threshold is not None
            and selected_score < selected_pass_threshold
            and (selected_pass_threshold - selected_score) <= delivery_pass_tolerance
        )
        final_output = {
            **dict(best_candidate_output or fallback_candidate_output),
            "validation_report": dict(best_validation.result.outputs),
            "repair_history": list(repair_history),
        }
        if (
            validator_passed
            and selected_score is not None
            and selected_pass_threshold is not None
            and selected_score >= selected_pass_threshold
        ):
            status = "completed"
        elif within_delivery_tolerance:
            status = "completed"
        elif validator_passed:
            error = (
                "The best candidate passed the validator verdict but stayed below the "
                f"required pass threshold ({selected_score:.2f} < {selected_pass_threshold:.2f})."
            )
        else:
            error = "The best candidate still failed validation."
    else:
        if fallback_candidate_output:
            final_output = dict(fallback_candidate_output)
        if validation_executions:
            latest = validation_executions[-1].result
            error = _result_failure_message(
                "validation",
                error=latest.error,
                metadata=latest.metadata,
            )
        elif aggregation_executions:
            latest = aggregation_executions[-1].result
            error = _result_failure_message(
                "aggregation",
                error=latest.error,
                metadata=latest.metadata,
            )
        elif aggregation_failed_error:
            error = f"Aggregation failed: {aggregation_failed_error}"
        elif worker_pool_executions:
            latest = worker_pool_executions[-1].result
            error = _result_failure_message(
                "workers",
                error=latest.error,
                metadata=latest.metadata,
            )
        elif orchestrator_runs:
            latest = orchestrator_runs[-1].result
            error = _result_failure_message(
                "orchestration",
                error=latest.error,
                metadata=latest.metadata,
            )
        else:
            error = "The coding organism did not produce a validated candidate."

    if status == "completed":
        _emit_status_update(
            actor="orchestrator",
            phase="delivery",
            attempt=best_attempt or 0,
            status="completed",
            message=_aggregation_status_message(final_output),
            candidate_id=final_output.get("candidate_id"),
            target_files=list(final_output.get("target_files") or []),
            test_plan=list(final_output.get("test_plan") or []),
        )
    elif error:
        _emit_status_update(
            actor="orchestrator",
            phase="delivery",
            attempt=best_attempt or 0,
            status="failed",
            message=error,
            candidate_id=final_output.get("candidate_id"),
            target_files=list(final_output.get("target_files") or []),
            test_plan=list(final_output.get("test_plan") or []),
        )

    result = CodingOrganismResult(
        status=status,
        final_output=final_output,
        selected_attempt=best_attempt,
        validation_scores=validation_scores,
        improved_via_repair=len(validation_scores) >= 2 and validation_scores[-1] > validation_scores[0],
        error=error,
        observability=observability,
        metadata={
            "repair_history": repair_history,
            "material_yield_history": material_yield_history,
            "readiness_ledger_history": readiness_ledger_history,
            "incremental_readiness_history": incremental_readiness_history,
            "partial_aggregation_history": partial_aggregation_history,
            "incremental_validation_history": incremental_validation_history,
            "speculative_validation_history": speculative_validation_history,
            "orchestrator_runs": len(orchestrator_runs),
            "worker_pool_attempts": len(worker_pool_executions),
            "aggregation_attempts": len(aggregation_executions),
            "validation_attempts": len(validation_executions),
            "coding_flow": organism.metadata.get("coding_flow"),
            "selected_pass_threshold": selected_pass_threshold,
            "delivery_pass_tolerance": delivery_pass_tolerance,
            "selected_within_delivery_tolerance": (
                status == "completed"
                and selected_score is not None
                and selected_pass_threshold is not None
                and selected_score < selected_pass_threshold
            ),
            "selected_candidate_source": best_candidate_source or latest_candidate_source,
        },
    )
    _emit(
        "organism.completed",
        trace_id=trace_id,
        organism_id=organism.organism_id,
        task_id=task.task_id,
        status=result.status,
        selected_attempt=result.selected_attempt,
        error=result.error,
        validation_scores=list(result.validation_scores),
        candidate_id=result.final_output.get("candidate_id"),
    )
    return CodingOrganismExecution(
        organism=organism,
        task=task,
        orchestrator_runs=orchestrator_runs,
        worker_pool_executions=worker_pool_executions,
        aggregation_executions=aggregation_executions,
        validation_executions=validation_executions,
        trace_log=trace_log,
        result=result,
    )


__all__ = [
    "CodingOrganism",
    "CodingOrganismExecution",
    "CodingOrganismResult",
    "CodingOrchestratorPlan",
    "CodingTask",
    "build_coding_orchestrator_worker",
    "coding_execution_organism",
    "execute_coding_organism",
]
