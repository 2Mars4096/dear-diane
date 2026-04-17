"""DAN control-plane selector and DAN-v2 runtime bridge."""

from __future__ import annotations

import inspect
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from dan.worker.composition import CrossCellTraceLog
from dan.worker.core.executor import WorkerCoreExecutor
from dan.worker.organisms.coding_conversation import (
    CodingConversationContext,
    CodingConversationController,
    CodingConversationFacts,
    CodingConversationMessage,
    CodingConversationTurnDecision,
)
from dan.worker.organisms.coding_execution import (
    CodingTask,
    coding_execution_organism,
    execute_coding_organism,
)
from dan.worker.organisms.dan_conversation import (
    DANConversationContext,
    DANConversationController,
    DANConversationFacts,
    DANConversationMessage,
    DANConversationTurnDecision,
    ReviewDecision,
    SupervisorBrief,
    WorkerReport,
)
from dan.worker.organisms.incident_conversation import (
    IncidentCommanderController,
    IncidentConversationContext,
    IncidentConversationFacts,
    IncidentConversationMessage,
    IncidentConversationTurnDecision,
)
from dan.worker.organisms.incident_execution import FROZEN_INCIDENT_SCENARIOS
from dan.worker.organisms.incident_execution import (
    IncidentActionResult,
    IncidentExecutionReport,
    IncidentExecutionRequest,
    execute_incident_action,
    finalize_incident_execution,
    resolve_incident_action_boundary,
)
from dan.worker.organisms.local_runtime import (
    DEFAULT_LIVE_ORGANISM_TOOL_IDS,
    LocalOrganismToolRuntime,
    ToolLoopCompletionProvider,
    attach_local_tooling_to_coding_organism,
)
from dan.worker.organisms.research_conversation import (
    ResearchConversationContext,
    ResearchConversationController,
    ResearchConversationFacts,
    ResearchConversationMessage,
    ResearchConversationTurnDecision,
)

ControlPlaneMode = Literal["v1", "v2"]

DEFAULT_CONTROL_PLANE_MODE: ControlPlaneMode = "v1"
_CONTROL_PLANE_ALIASES = {
    "legacy": "v1",
    "old": "v1",
    "default": "v1",
    "new": "v2",
    "dan-v2": "v2",
}

logger = logging.getLogger(__name__)


def resolve_control_plane_mode(value: Any | None = None) -> ControlPlaneMode:
    raw = str(
        value
        if value is not None
        else os.environ.get("DAN_CONTROL_PLANE", DEFAULT_CONTROL_PLANE_MODE)
    ).strip().lower()
    if not raw:
        return DEFAULT_CONTROL_PLANE_MODE
    normalized = _CONTROL_PLANE_ALIASES.get(raw, raw)
    return "v2" if normalized == "v2" else "v1"


def _timestamp_parts() -> tuple[str, str, str]:
    now = datetime.now().astimezone()
    return (
        now.isoformat(),
        now.date().isoformat(),
        str(now.tzinfo or "UTC"),
    )


def _merge_prompt_context(*parts: str) -> str:
    cleaned = ["\n".join(str(part).strip().splitlines()) for part in parts if str(part or "").strip()]
    return "\n\n".join(cleaned)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _compose_user_response(public_response: str, question: str = "") -> str:
    public_response = " ".join(str(public_response or "").split())
    question = " ".join(str(question or "").split())
    if public_response and question:
        if question in public_response:
            return public_response
        return f"{public_response}\n\n{question}"
    return public_response or question


def _overlap_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _merge_unique_texts(*parts: str) -> str:
    merged: list[str] = []
    for part in parts:
        text = " ".join(str(part or "").split())
        if not text:
            continue
        text_key = _overlap_key(text)
        replaced = False
        for idx, existing in enumerate(list(merged)):
            existing_key = _overlap_key(existing)
            if text == existing or text_key == existing_key or text_key in existing_key:
                replaced = True
                break
            if existing in text or existing_key in text_key:
                merged[idx] = text
                replaced = True
                break
        if not replaced:
            merged.append(text)
    return " ".join(merged)


def _dedupe_texts(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _coerce_timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value or "").strip())
    except (TypeError, ValueError):
        return 0.0


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on", "enabled", "direct"}


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = [value]
    normalized: list[str] = []
    for item in items:
        text = _clean_text(item)
        if text:
            normalized.append(text)
    return normalized


@dataclass
class DANV2TurnOutcome:
    turn_decision: DANConversationTurnDecision
    supervisor_brief: SupervisorBrief | None
    worker_report: WorkerReport
    review_decision: ReviewDecision
    direct_response: str | None = None
    handoff_prompt_context: str = ""
    handoff_metadata: dict[str, Any] | None = None
    controller_session_payload: dict[str, Any] | None = None
    code_session_payload: dict[str, Any] | None = None
    research_session_payload: dict[str, Any] | None = None
    incident_session_payload: dict[str, Any] | None = None


class DANV2Runtime:
    """Server-side bridge from the app chat surface into DAN-v2 controllers."""

    def __init__(
        self,
        *,
        chat_manager: Any,
        run_manager: Any | None = None,
        stream_text_responses: bool = False,
        provider_request_overrides: dict[str, Any] | None = None,
    ) -> None:
        model = str(getattr(chat_manager, "default_llm_model", "") or getattr(chat_manager, "_chat_model", "") or "").strip()
        if not model:
            raise RuntimeError("Chat manager does not expose a default llm model")
        provider = chat_manager.resolve_llm_provider(model=model)
        self._model = model
        self._provider = provider
        self._run_manager = run_manager
        self._stream_text_responses = bool(stream_text_responses)
        self._provider_request_overrides = dict(provider_request_overrides or {})
        self._controller = DANConversationController(
            provider=provider,
            model=model,
            stream_text_responses=stream_text_responses,
            provider_request_overrides=provider_request_overrides,
        )
        self._code_controller = CodingConversationController(
            provider=provider,
            model=model,
            stream_text_responses=stream_text_responses,
            provider_request_overrides=provider_request_overrides,
        )
        self._research_controller = ResearchConversationController(
            provider=provider,
            model=model,
            stream_text_responses=stream_text_responses,
            provider_request_overrides=provider_request_overrides,
        )
        self._incident_controller = IncidentCommanderController(
            provider=provider,
            model=model,
            stream_text_responses=stream_text_responses,
            provider_request_overrides=provider_request_overrides,
        )

    def _recent_dan_messages(self, history: list[dict[str, str]]) -> list[DANConversationMessage]:
        return [
            DANConversationMessage(
                role=str(item.get("role") or ""),
                text=str(item.get("content") or ""),
            )
            for item in history[-12:]
            if str(item.get("content") or "").strip()
        ]

    def _recent_coding_messages(self, history: list[dict[str, str]]) -> list[CodingConversationMessage]:
        return [
            CodingConversationMessage(
                role=str(item.get("role") or ""),
                text=str(item.get("content") or ""),
            )
            for item in history[-12:]
            if str(item.get("content") or "").strip()
        ]

    def _recent_research_messages(self, history: list[dict[str, str]]) -> list[ResearchConversationMessage]:
        return [
            ResearchConversationMessage(
                role=str(item.get("role") or ""),
                text=str(item.get("content") or ""),
            )
            for item in history[-12:]
            if str(item.get("content") or "").strip()
        ]

    def _recent_incident_messages(self, history: list[dict[str, str]]) -> list[IncidentConversationMessage]:
        return [
            IncidentConversationMessage(
                role=str(item.get("role") or ""),
                text=str(item.get("content") or ""),
            )
            for item in history[-12:]
            if str(item.get("content") or "").strip()
        ]

    def _build_dan_context(
        self,
        *,
        req: Any,
        normalized_mode: str,
        recent_worker_reports: list[WorkerReport],
    ) -> DANConversationContext:
        current_timestamp, current_date, timezone_name = _timestamp_parts()
        last_report = recent_worker_reports[-1] if recent_worker_reports else None
        return DANConversationContext(
            workflow_id=str(req.workflow_id),
            model=self._model,
            requested_mode=str(req.mode or ""),
            normalized_mode=str(normalized_mode or ""),
            pending_clarification=None,
            facts=DANConversationFacts(
                workflow_id=str(req.workflow_id),
                thread_id=str(req.thread_id or ""),
                session_id=str(req.session_id or req.thread_id or ""),
                surface=str(req.surface or ""),
                active_model=self._model,
                requested_mode=str(req.mode or ""),
                normalized_mode=str(normalized_mode or ""),
                available_organisms=["code", "research", "incident", "legacy"],
                recent_lane=str(last_report.lane) if last_report is not None else "",
                recent_status=str(last_report.status) if last_report is not None else "",
                recent_objective=str(last_report.objective) if last_report is not None else "",
                current_timestamp=current_timestamp,
                current_date=current_date,
                timezone=timezone_name,
            ),
            recent_conversation=self._recent_dan_messages(req.history),
            recent_worker_reports=list(recent_worker_reports[-4:]),
        )

    def _workspace_root(self, req: Any) -> str:
        surface_context = req.surface_context if isinstance(req.surface_context, dict) else {}
        root = str(surface_context.get("workspace_root") or surface_context.get("cwd") or os.getcwd()).strip()
        return root or os.getcwd()

    def _direct_code_execution_enabled(self, req: Any) -> bool:
        surface_context = req.surface_context if isinstance(req.surface_context, dict) else {}
        if "direct_code_execution" in surface_context:
            return _is_truthy(surface_context.get("direct_code_execution"))
        return _is_truthy(os.environ.get("DAN_V2_DIRECT_CODE_RUNTIME"))

    async def _run_direct_code_execution(
        self,
        *,
        req: Any,
        objective: str,
        acceptance_criteria: list[str],
        research_findings: list[str],
        repair_brief: str,
    ) -> dict[str, Any]:
        runner = getattr(self, "_direct_code_runner", None)
        if callable(runner):
            result = runner(
                req=req,
                objective=objective,
                acceptance_criteria=list(acceptance_criteria),
                research_findings=list(research_findings),
                repair_brief=repair_brief,
                workspace_root=self._workspace_root(req),
                model=self._model,
            )
            if inspect.isawaitable(result):
                result = await result
            if hasattr(result, "model_dump"):
                return dict(result.model_dump(mode="json"))
            return dict(result or {})

        provider = getattr(self, "_provider", None)
        if provider is None:
            raise RuntimeError("Direct DAN Code execution requires a resolved provider")

        workspace_root = Path(self._workspace_root(req)).expanduser().resolve()
        tool_runtime = LocalOrganismToolRuntime(
            tool_ids=DEFAULT_LIVE_ORGANISM_TOOL_IDS,
            workspace_root=workspace_root,
        )
        completion_provider = ToolLoopCompletionProvider(
            provider=provider,
            tool_runtime=tool_runtime,
            default_model=self._model,
            stream_text_responses=self._stream_text_responses,
            provider_request_overrides=dict(self._provider_request_overrides),
        )
        executor = WorkerCoreExecutor(completion_provider=completion_provider)
        organism_id = "coding-organism"
        organ_id = "coding-build"
        organism = attach_local_tooling_to_coding_organism(
            coding_execution_organism(
                organism_id=organism_id,
                model=self._model,
                base_id=organ_id,
            ),
            tool_ids=tool_runtime.tool_ids,
        )
        task_id = (
            "server-control-plane:"
            f"{_clean_text(getattr(req, 'workflow_id', 'scratch')) or 'scratch'}:"
            f"{_clean_text(getattr(req, 'session_id', '') or getattr(req, 'thread_id', '') or 'session')}"
        )
        task = CodingTask(
            task_id=task_id,
            objective=objective,
            acceptance_criteria=list(acceptance_criteria),
            research_findings=list(research_findings),
            repair_brief=repair_brief,
            session_context={
                "control_plane_mode": "v2",
                "selected_lane": "code",
                "surface": str(getattr(req, "surface", "") or ""),
                "workflow_id": str(getattr(req, "workflow_id", "") or ""),
                "thread_id": str(getattr(req, "thread_id", "") or ""),
                "session_id": str(getattr(req, "session_id", "") or ""),
            },
        )
        trace_log = CrossCellTraceLog()
        execution = await execute_coding_organism(
            executor=executor,
            organism=organism,
            task=task,
            trace_log=trace_log,
        )
        assert execution.result is not None
        trace_rows = list(execution.result.observability.trace_rows)
        outputs = dict(execution.result.final_output)
        return {
            "status": str(execution.result.status),
            "trace_id": str(execution.result.observability.trace_id),
            "organism_id": organism_id,
            "organ_id": organ_id,
            "task_id": task_id,
            "objective": objective,
            "candidate_id": (
                str(outputs.get("candidate_id")).strip()
                if outputs.get("candidate_id") is not None
                else None
            ),
            "change_summary": str(outputs.get("change_summary") or ""),
            "target_files": outputs.get("target_files"),
            "test_plan": outputs.get("test_plan"),
            "risks": outputs.get("risks"),
            "outputs": outputs,
            "handoff_count": sum(1 for row in trace_rows if row.get("kind") == "handoff"),
            "signal_count": sum(1 for row in trace_rows if row.get("kind") == "signal"),
            "error": str(execution.result.error) if execution.result.error else None,
            "trace_rows": trace_rows,
        }

    @staticmethod
    def _build_direct_code_response(report: dict[str, Any]) -> str:
        status = _clean_text(report.get("status") or "completed") or "completed"
        change_summary = _clean_text(report.get("change_summary") or "")
        target_files = _normalize_text_list(report.get("target_files"))
        test_plan = _normalize_text_list(report.get("test_plan"))
        risks = _normalize_text_list(report.get("risks"))
        error = _clean_text(report.get("error") or "")

        lines: list[str] = []
        if status.lower() == "completed":
            lines.append("DAN Code completed one bounded pass.")
        else:
            lines.append(f"DAN Code finished one bounded pass with status `{status}`.")
        if change_summary:
            lines.append(change_summary)
        elif error:
            lines.append(error)
        if target_files:
            lines.append("Target files: " + ", ".join(target_files[:6]))
        if test_plan:
            lines.append("Validation: " + "; ".join(test_plan[:4]))
        if risks:
            lines.append("Risks: " + "; ".join(risks[:3]))
        return "\n\n".join(line for line in lines if line)

    def _list_run_snapshots(self) -> list[dict[str, Any]]:
        run_manager = getattr(self, "_run_manager", None)
        if run_manager is None:
            return []
        list_runs = getattr(run_manager, "list_runs", None)
        if not callable(list_runs):
            return []
        try:
            snapshots = list_runs()
        except Exception:
            logger.debug("Failed to inspect run manager for incident evidence", exc_info=True)
            return []
        return [dict(item) for item in list(snapshots or []) if isinstance(item, dict)]

    @staticmethod
    def _run_snapshot_sort_key(snapshot: dict[str, Any]) -> tuple[float, float]:
        finished_at = _coerce_timestamp(snapshot.get("finished_at"))
        started_at = _coerce_timestamp(snapshot.get("started_at"))
        return (max(finished_at, started_at), started_at)

    def _select_failed_workflow_snapshot(
        self,
        *,
        req: Any,
        incident_decision: IncidentConversationTurnDecision,
        target_hints: list[str] | None = None,
        preferred_statuses: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        snapshots = self._list_run_snapshots()
        if not snapshots:
            return None
        cue_text = _clean_text(
            " ".join(
                part
                for part in [
                    str(req.message or ""),
                    incident_decision.desired_delta,
                    incident_decision.incident_scenario_id,
                    *(target_hints or []),
                ]
                if _clean_text(part)
            )
        ).lower()
        direct_matches = [
            snapshot
            for snapshot in snapshots
            if (
                _clean_text(snapshot.get("run_id")).lower()
                and _clean_text(snapshot.get("run_id")).lower() in cue_text
            )
            or (
                _clean_text(snapshot.get("graph_id")).lower()
                and _clean_text(snapshot.get("graph_id")).lower() in cue_text
            )
        ]
        if preferred_statuses:
            preferred_direct_matches = [
                snapshot
                for snapshot in direct_matches
                if _clean_text(snapshot.get("status")).lower() in set(preferred_statuses)
            ]
            if preferred_direct_matches:
                return sorted(
                    preferred_direct_matches,
                    key=self._run_snapshot_sort_key,
                    reverse=True,
                )[0]
        if direct_matches:
            return sorted(
                direct_matches,
                key=self._run_snapshot_sort_key,
                reverse=True,
            )[0]
        if preferred_statuses:
            preferred_snapshots = [
                snapshot
                for snapshot in snapshots
                if _clean_text(snapshot.get("status")).lower() in set(preferred_statuses)
            ]
            if preferred_snapshots:
                return sorted(
                    preferred_snapshots,
                    key=self._run_snapshot_sort_key,
                    reverse=True,
                )[0]
        concerning_statuses = {"failed", "cancelled", "running", "pending"}
        concerning_snapshots = [
            snapshot
            for snapshot in snapshots
            if _clean_text(snapshot.get("status")).lower() in concerning_statuses
        ]
        if not concerning_snapshots:
            return None
        return sorted(
            concerning_snapshots,
            key=self._run_snapshot_sort_key,
            reverse=True,
        )[0]

    def _build_failed_workflow_execution_request(
        self,
        *,
        req: Any,
        incident_decision: IncidentConversationTurnDecision,
    ) -> IncidentExecutionRequest | None:
        if incident_decision.incident_scenario_id != "failed_scheduled_workflow":
            return None
        if incident_decision.chosen_action != "investigate":
            return None
        snapshot = self._select_failed_workflow_snapshot(
            req=req,
            incident_decision=incident_decision,
        )
        if snapshot is None:
            return None
        run_id = _clean_text(snapshot.get("run_id"))
        graph_id = _clean_text(snapshot.get("graph_id"))
        target = graph_id or run_id or _clean_text(req.message)
        status = _clean_text(snapshot.get("status"))
        phase = _clean_text(snapshot.get("phase"))
        error = _clean_text(snapshot.get("error"))
        stop_reason = _clean_text(snapshot.get("stop_reason"))
        automatic_recovery = (
            dict(snapshot.get("automatic_recovery"))
            if isinstance(snapshot.get("automatic_recovery"), dict)
            else {}
        )
        evidence: dict[str, Any] = {}
        if run_id:
            evidence["run_id"] = run_id
        if graph_id:
            evidence["graph_id"] = graph_id
        if status:
            evidence["current_status"] = status
            evidence["latest_status"] = status
        if phase:
            evidence["phase"] = phase
        if error:
            evidence["error"] = error
        if stop_reason:
            evidence["stop_reason"] = stop_reason
        recovery_status = _clean_text(automatic_recovery.get("status"))
        if recovery_status:
            evidence["automatic_recovery_status"] = recovery_status
        verification_checks = _dedupe_texts(
            [
                *list(incident_decision.verification_checks),
                f"Confirm the latest status for run `{run_id}`."
                if run_id
                else "",
            ]
        )
        return IncidentExecutionRequest(
            scenario_id=incident_decision.incident_scenario_id,
            action_id=incident_decision.chosen_action,
            target=target,
            objective=_clean_text(incident_decision.desired_delta)
            or _clean_text(req.message)
            or target,
            preferred_lane=incident_decision.action_lane or "legacy",
            evidence=evidence,
            verification_checks=verification_checks,
        )

    async def _execute_live_workflow_incident_action(
        self,
        *,
        req: Any,
        incident_decision: IncidentConversationTurnDecision,
        execution_request: IncidentExecutionRequest | None,
    ) -> IncidentExecutionReport | None:
        if incident_decision.incident_scenario_id != "failed_scheduled_workflow":
            return None
        if incident_decision.chosen_action not in {"contain", "pause"}:
            return None
        run_manager = getattr(self, "_run_manager", None)
        cancel_run = getattr(run_manager, "cancel_run", None) if run_manager is not None else None
        if not callable(cancel_run):
            return None
        boundary = resolve_incident_action_boundary(
            incident_decision.chosen_action,
            preferred_lane=incident_decision.action_lane or "legacy",
        )
        target_hints = [
            str(req.message or ""),
            incident_decision.desired_delta,
        ]
        if execution_request is not None:
            target_hints.extend(
                [
                    execution_request.target,
                    str(execution_request.evidence.get("run_id") or ""),
                    str(execution_request.evidence.get("graph_id") or ""),
                ]
            )
        snapshot = self._select_failed_workflow_snapshot(
            req=req,
            incident_decision=incident_decision,
            target_hints=target_hints,
            preferred_statuses=("running", "pending"),
        )
        if snapshot is None:
            return None
        run_id = _clean_text(snapshot.get("run_id"))
        graph_id = _clean_text(snapshot.get("graph_id"))
        status = _clean_text(snapshot.get("status")).lower()
        target = graph_id or run_id or _clean_text(req.message)
        evidence = dict(execution_request.evidence) if execution_request is not None else {}
        if run_id:
            evidence["run_id"] = run_id
        if graph_id:
            evidence["graph_id"] = graph_id
        if status:
            evidence["current_status"] = status
            evidence["latest_status"] = status
        request_payload = (
            execution_request.model_copy(
                update={
                    "scenario_id": incident_decision.incident_scenario_id,
                    "action_id": incident_decision.chosen_action,
                    "target": execution_request.target or target,
                    "objective": execution_request.objective
                    or incident_decision.desired_delta
                    or str(req.message or "")
                    or target,
                    "preferred_lane": execution_request.preferred_lane
                    or incident_decision.action_lane
                    or "legacy",
                    "evidence": evidence,
                    "verification_checks": list(
                        execution_request.verification_checks
                        or incident_decision.verification_checks
                    ),
                }
            )
            if execution_request is not None
            else IncidentExecutionRequest(
                scenario_id=incident_decision.incident_scenario_id,
                action_id=incident_decision.chosen_action,
                target=target,
                objective=incident_decision.desired_delta
                or str(req.message or "")
                or target,
                preferred_lane=incident_decision.action_lane or "legacy",
                evidence=evidence,
                verification_checks=list(incident_decision.verification_checks),
            )
        )
        result_key = (
            "pause_result" if incident_decision.chosen_action == "pause" else "containment_result"
        )
        if status == "cancelled":
            request_payload.evidence[result_key] = "cancelled"
            request_payload.evidence["post_action_status"] = "cancelled"
            action_result = IncidentActionResult(
                action_id=boundary.action_id,
                lane=boundary.lane,
                status="completed",
                summary=(
                    f"Run `{run_id or target}` was already cancelled, so the workflow is contained."
                ),
                evidence=[
                    f"run_id: {run_id}" if run_id else "",
                    "post_action_status: cancelled",
                ],
            )
            return finalize_incident_execution(
                request_payload,
                boundary=boundary,
                action_result=action_result,
                scenario_id=incident_decision.incident_scenario_id,
            )
        if status not in {"running", "pending"}:
            action_result = IncidentActionResult(
                action_id=boundary.action_id,
                lane=boundary.lane,
                status="blocked",
                summary=(
                    f"Cannot {boundary.action_id} `{target}` because the latest run state is "
                    f"`{status or 'unknown'}`, not a live running/pending workflow."
                ),
                evidence=[
                    f"run_id: {run_id}" if run_id else "",
                    f"current_status: {status}" if status else "",
                ],
                blockers=[
                    "choose investigate, retry, or escalate instead of live containment",
                ],
            )
            return finalize_incident_execution(
                request_payload,
                boundary=boundary,
                action_result=action_result,
                scenario_id=incident_decision.incident_scenario_id,
            )
        cancelled = bool(cancel_run(run_id)) if run_id else False
        if not cancelled:
            action_result = IncidentActionResult(
                action_id=boundary.action_id,
                lane=boundary.lane,
                status="blocked",
                summary=(
                    f"Attempted to {boundary.action_id} `{target}`, but the live run could not be cancelled."
                ),
                evidence=[
                    f"run_id: {run_id}" if run_id else "",
                    f"current_status: {status}" if status else "",
                ],
                blockers=[
                    "the run state changed before containment completed",
                ],
            )
            return finalize_incident_execution(
                request_payload,
                boundary=boundary,
                action_result=action_result,
                scenario_id=incident_decision.incident_scenario_id,
            )
        request_payload.evidence[result_key] = "cancelled"
        request_payload.evidence["post_action_status"] = "cancelled"
        action_result = IncidentActionResult(
            action_id=boundary.action_id,
            lane=boundary.lane,
            status="completed",
            summary=f"Cancelled run `{run_id}` to {boundary.action_id} `{target}`.",
            changed_state=True,
            evidence=[
                f"run_id: {run_id}",
                "post_action_status: cancelled",
            ],
            artifacts={"run_id": run_id, "graph_id": graph_id},
        )
        return finalize_incident_execution(
            request_payload,
            boundary=boundary,
            action_result=action_result,
            scenario_id=incident_decision.incident_scenario_id,
        )

    async def _execute_live_workflow_retry(
        self,
        *,
        req: Any,
        incident_decision: IncidentConversationTurnDecision,
        execution_request: IncidentExecutionRequest | None,
    ) -> IncidentExecutionReport | None:
        if incident_decision.incident_scenario_id != "failed_scheduled_workflow":
            return None
        if incident_decision.chosen_action != "retry":
            return None
        run_manager = getattr(self, "_run_manager", None)
        retry_run = getattr(run_manager, "retry_run", None) if run_manager is not None else None
        if not callable(retry_run):
            return None
        boundary = resolve_incident_action_boundary(
            incident_decision.chosen_action,
            preferred_lane=incident_decision.action_lane or "legacy",
        )
        target_hints = [
            str(req.message or ""),
            incident_decision.desired_delta,
        ]
        if execution_request is not None:
            target_hints.extend(
                [
                    execution_request.target,
                    str(execution_request.evidence.get("run_id") or ""),
                    str(execution_request.evidence.get("graph_id") or ""),
                ]
            )
        snapshot = self._select_failed_workflow_snapshot(
            req=req,
            incident_decision=incident_decision,
            target_hints=target_hints,
            preferred_statuses=("failed", "cancelled"),
        )
        if snapshot is None:
            return None
        run_id = _clean_text(snapshot.get("run_id"))
        graph_id = _clean_text(snapshot.get("graph_id"))
        status = _clean_text(snapshot.get("status")).lower()
        target = graph_id or run_id or _clean_text(req.message)
        evidence = dict(execution_request.evidence) if execution_request is not None else {}
        if run_id:
            evidence["run_id"] = run_id
        if graph_id:
            evidence["graph_id"] = graph_id
        if status:
            evidence["current_status"] = status
            evidence["latest_status"] = status
        request_payload = (
            execution_request.model_copy(
                update={
                    "scenario_id": incident_decision.incident_scenario_id,
                    "action_id": incident_decision.chosen_action,
                    "target": execution_request.target or target,
                    "objective": execution_request.objective
                    or incident_decision.desired_delta
                    or str(req.message or "")
                    or target,
                    "preferred_lane": execution_request.preferred_lane
                    or incident_decision.action_lane
                    or "legacy",
                    "evidence": evidence,
                    "verification_checks": list(
                        execution_request.verification_checks
                        or incident_decision.verification_checks
                    ),
                }
            )
            if execution_request is not None
            else IncidentExecutionRequest(
                scenario_id=incident_decision.incident_scenario_id,
                action_id=incident_decision.chosen_action,
                target=target,
                objective=incident_decision.desired_delta
                or str(req.message or "")
                or target,
                preferred_lane=incident_decision.action_lane or "legacy",
                evidence=evidence,
                verification_checks=list(incident_decision.verification_checks),
            )
        )
        if status not in {"failed", "cancelled"}:
            action_result = IncidentActionResult(
                action_id=boundary.action_id,
                lane=boundary.lane,
                status="blocked",
                summary=(
                    f"Cannot retry `{target}` because the latest run state is "
                    f"`{status or 'unknown'}`, not a failed/cancelled workflow."
                ),
                evidence=[
                    f"run_id: {run_id}" if run_id else "",
                    f"current_status: {status}" if status else "",
                ],
                blockers=[
                    "choose investigate, contain, or escalate instead of retry",
                ],
            )
            return finalize_incident_execution(
                request_payload,
                boundary=boundary,
                action_result=action_result,
                scenario_id=incident_decision.incident_scenario_id,
            )
        try:
            replay_record = await retry_run(run_id)
        except Exception as exc:
            action_result = IncidentActionResult(
                action_id=boundary.action_id,
                lane=boundary.lane,
                status="blocked",
                summary=f"Retry for `{target}` could not start: {_clean_text(exc)}",
                evidence=[
                    f"run_id: {run_id}" if run_id else "",
                    f"current_status: {status}" if status else "",
                ],
                blockers=[
                    "replayable launch context is missing or the workflow graph could not be reloaded",
                ],
            )
            return finalize_incident_execution(
                request_payload,
                boundary=boundary,
                action_result=action_result,
                scenario_id=incident_decision.incident_scenario_id,
            )
        replay_run_id = _clean_text(getattr(replay_record, "run_id", ""))
        request_payload.evidence["retry_result"] = "pending"
        request_payload.evidence["post_action_status"] = "pending"
        if replay_run_id:
            request_payload.evidence["retry_run_id"] = replay_run_id
        action_result = IncidentActionResult(
            action_id=boundary.action_id,
            lane=boundary.lane,
            status="completed",
            summary=(
                f"Retried `{target}` by launching `{replay_run_id or 'a new run'}` from the stored launch request."
            ),
            changed_state=True,
            evidence=[
                f"run_id: {run_id}" if run_id else "",
                f"retry_run_id: {replay_run_id}" if replay_run_id else "",
                "post_action_status: pending",
            ],
            artifacts={"source_run_id": run_id, "retry_run_id": replay_run_id},
        )
        return finalize_incident_execution(
            request_payload,
            boundary=boundary,
            action_result=action_result,
            scenario_id=incident_decision.incident_scenario_id,
        )

    def _build_incident_execution_request(
        self,
        *,
        req: Any,
        incident_decision: IncidentConversationTurnDecision,
    ) -> IncidentExecutionRequest | None:
        surface_context = req.surface_context if isinstance(req.surface_context, dict) else {}
        raw = surface_context.get("incident_execution")
        if isinstance(raw, dict):
            evidence = raw.get("evidence")
            verification_checks = raw.get("verification_checks")
            return IncidentExecutionRequest(
                scenario_id=str(
                    raw.get("scenario_id") or incident_decision.incident_scenario_id or ""
                ),
                action_id=str(raw.get("action_id") or incident_decision.chosen_action or "investigate"),
                target=str(raw.get("target") or req.message or ""),
                objective=str(
                    raw.get("objective")
                    or incident_decision.desired_delta
                    or req.message
                    or ""
                ),
                preferred_lane=str(
                    raw.get("preferred_lane") or incident_decision.action_lane or ""
                ),
                approval_granted=bool(raw.get("approval_granted")),
                evidence=dict(evidence) if isinstance(evidence, dict) else {},
                verification_checks=[
                    str(item).strip()
                    for item in list(verification_checks or incident_decision.verification_checks)
                    if str(item).strip()
                ],
            )
        return self._build_failed_workflow_execution_request(
            req=req,
            incident_decision=incident_decision,
        )

    def _build_code_context(
        self,
        *,
        req: Any,
        brief: SupervisorBrief,
        recent_worker_reports: list[WorkerReport],
    ) -> CodingConversationContext:
        current_timestamp, current_date, timezone_name = _timestamp_parts()
        workspace_root = self._workspace_root(req)
        last_code_report = next((report for report in reversed(recent_worker_reports) if report.lane == "code"), None)
        return CodingConversationContext(
            workspace_root=workspace_root,
            model=self._model,
            thinking_mode="standard",
            tool_ids=[],
            acceptance_criteria=list(brief.success_criteria),
            pending_clarification=None,
            facts=CodingConversationFacts(
                product_name="DAN Code",
                workspace_root=workspace_root,
                effective_working_directory=workspace_root,
                shell_process_directory=workspace_root,
                session_id=str(req.session_id or req.thread_id or req.workflow_id),
                active_model=self._model,
                thinking_mode="standard",
                approval_mode="server",
                enabled_tools=[],
                coding_turn_count=sum(1 for report in recent_worker_reports if report.lane == "code"),
                conversation_message_count=len(req.history),
                latest_report_status=str(last_code_report.status) if last_code_report is not None else "",
                latest_report_objective=str(last_code_report.objective) if last_code_report is not None else "",
                current_timestamp=current_timestamp,
                current_date=current_date,
                timezone=timezone_name,
            ),
            recent_conversation=self._recent_coding_messages(req.history),
            recent_reports=[],
        )

    def _build_research_context(
        self,
        *,
        req: Any,
        brief: SupervisorBrief,
        recent_worker_reports: list[WorkerReport],
    ) -> ResearchConversationContext:
        current_timestamp, current_date, timezone_name = _timestamp_parts()
        workspace_root = self._workspace_root(req)
        research_turn_count = sum(1 for report in recent_worker_reports if report.lane == "research")
        return ResearchConversationContext(
            workspace_root=workspace_root,
            model=self._model,
            thinking_mode="standard",
            tool_ids=[],
            acceptance_criteria=list(brief.success_criteria),
            default_delivery_target="chat answer",
            depth_profile="standard",
            requested_reader_count=None,
            pending_clarification=None,
            facts=ResearchConversationFacts(
                product_name="DAN Research",
                workspace_root=workspace_root,
                effective_working_directory=workspace_root,
                shell_process_directory=workspace_root,
                session_id=str(req.session_id or req.thread_id or req.workflow_id),
                active_model=self._model,
                thinking_mode="standard",
                enabled_tools=[],
                research_turn_count=research_turn_count,
                conversation_message_count=len(req.history),
                default_delivery_target="chat answer",
                current_timestamp=current_timestamp,
                current_date=current_date,
                timezone=timezone_name,
                time_awareness_policy="respect-runtime-date",
            ),
            recent_conversation=self._recent_research_messages(req.history),
            recent_reports=[],
        )

    def _build_incident_context(
        self,
        *,
        req: Any,
        normalized_mode: str,
        brief: SupervisorBrief,
        recent_worker_reports: list[WorkerReport],
    ) -> IncidentConversationContext:
        current_timestamp, current_date, timezone_name = _timestamp_parts()
        workspace_root = self._workspace_root(req)
        last_incident_report = next(
            (report for report in reversed(recent_worker_reports) if report.lane == "incident"),
            None,
        )
        return IncidentConversationContext(
            workflow_id=str(req.workflow_id),
            model=self._model,
            workspace_root=workspace_root,
            requested_mode=str(req.mode or ""),
            normalized_mode=str(normalized_mode or ""),
            pending_clarification=None,
            incoming_brief=brief,
            facts=IncidentConversationFacts(
                workflow_id=str(req.workflow_id),
                thread_id=str(req.thread_id or ""),
                session_id=str(req.session_id or req.thread_id or ""),
                surface=str(req.surface or ""),
                workspace_root=workspace_root,
                active_model=self._model,
                requested_mode=str(req.mode or ""),
                normalized_mode=str(normalized_mode or ""),
                available_action_lanes=["code", "legacy"],
                frozen_scenarios=[
                    scenario.scenario_id for scenario in FROZEN_INCIDENT_SCENARIOS
                ],
                recent_status=str(last_incident_report.status) if last_incident_report is not None else "",
                recent_objective=str(last_incident_report.objective) if last_incident_report is not None else "",
                current_timestamp=current_timestamp,
                current_date=current_date,
                timezone=timezone_name,
            ),
            recent_conversation=self._recent_incident_messages(req.history),
            recent_worker_reports=list(recent_worker_reports[-4:]),
        )

    async def triage_user_turn(
        self,
        *,
        req: Any,
        normalized_mode: str,
        controller_session_payload: dict[str, Any] | None = None,
        code_session_payload: dict[str, Any] | None = None,
        research_session_payload: dict[str, Any] | None = None,
        incident_session_payload: dict[str, Any] | None = None,
        recent_worker_reports_payload: list[dict[str, Any]] | None = None,
    ) -> DANV2TurnOutcome:
        recent_worker_reports = [
            WorkerReport.model_validate(item)
            for item in list(recent_worker_reports_payload or [])
            if isinstance(item, dict)
        ]
        controller_session = self._controller.load_session(controller_session_payload)
        turn_decision, controller_session = await self._controller.decide_user_turn(
            session=controller_session,
            user_message=str(req.message or ""),
            pending_clarification=None,
            context=self._build_dan_context(
                req=req,
                normalized_mode=normalized_mode,
                recent_worker_reports=recent_worker_reports,
            ),
        )
        brief = self._controller.build_supervisor_brief(turn_decision)

        if turn_decision.action == "respond":
            response = _compose_user_response(turn_decision.public_response)
            worker_report = WorkerReport(
                lane="controller",
                status="responded",
                summary=response,
            )
            review = ReviewDecision(
                action="stop",
                public_response=response,
                reason="The controller answered directly.",
            )
            return DANV2TurnOutcome(
                turn_decision=turn_decision,
                supervisor_brief=brief,
                worker_report=worker_report,
                review_decision=review,
                direct_response=response,
                controller_session_payload=self._controller.dump_session(controller_session),
                code_session_payload=code_session_payload,
                research_session_payload=research_session_payload,
                incident_session_payload=incident_session_payload,
            )

        if turn_decision.action == "clarify":
            response = _compose_user_response(
                turn_decision.public_response,
                turn_decision.clarifying_question,
            )
            worker_report = WorkerReport(
                lane="controller",
                status="clarify",
                summary=response,
                blockers=[turn_decision.clarifying_question] if turn_decision.clarifying_question else [],
            )
            review = ReviewDecision(
                action="stop",
                public_response=response,
                reason="The controller needs one concrete clarification.",
            )
            return DANV2TurnOutcome(
                turn_decision=turn_decision,
                supervisor_brief=brief,
                worker_report=worker_report,
                review_decision=review,
                direct_response=response,
                controller_session_payload=self._controller.dump_session(controller_session),
                code_session_payload=code_session_payload,
                research_session_payload=research_session_payload,
                incident_session_payload=incident_session_payload,
            )

        if brief is None:
            response = _compose_user_response(
                turn_decision.public_response or "I need a concrete next step before I should keep going."
            )
            worker_report = WorkerReport(
                lane="controller",
                status="clarify",
                summary=response,
            )
            review = ReviewDecision(
                action="stop",
                public_response=response,
                reason="The controller did not produce a valid delegation brief.",
            )
            return DANV2TurnOutcome(
                turn_decision=turn_decision,
                supervisor_brief=None,
                worker_report=worker_report,
                review_decision=review,
                direct_response=response,
                controller_session_payload=self._controller.dump_session(controller_session),
                code_session_payload=code_session_payload,
                research_session_payload=research_session_payload,
                incident_session_payload=incident_session_payload,
            )

        if brief.lane == "code":
            code_session = self._code_controller.load_session(code_session_payload)
            code_decision, code_session = await self._code_controller.decide_user_turn(
                session=code_session,
                user_message=str(req.message or ""),
                pending_clarification=None,
                context=self._build_code_context(
                    req=req,
                    brief=brief,
                    recent_worker_reports=recent_worker_reports,
                ),
            )
            return await self._resolve_code_outcome(
                req=req,
                turn_decision=turn_decision,
                brief=brief,
                code_decision=code_decision,
                controller_session=controller_session,
                code_session=code_session,
                research_session_payload=research_session_payload,
                incident_session_payload=incident_session_payload,
            )

        if brief.lane == "research":
            research_session = self._research_controller.load_session(research_session_payload)
            research_decision, research_session = await self._research_controller.decide_user_turn(
                session=research_session,
                user_message=str(req.message or ""),
                pending_clarification=None,
                context=self._build_research_context(
                    req=req,
                    brief=brief,
                    recent_worker_reports=recent_worker_reports,
                ),
            )
            return self._resolve_research_outcome(
                req=req,
                turn_decision=turn_decision,
                brief=brief,
                research_decision=research_decision,
                controller_session=controller_session,
                research_session=research_session,
                code_session_payload=code_session_payload,
                incident_session_payload=incident_session_payload,
            )

        if brief.lane == "incident":
            incident_session = self._incident_controller.load_session(incident_session_payload)
            incident_decision, incident_session = await self._incident_controller.decide_user_turn(
                session=incident_session,
                user_message=str(req.message or ""),
                pending_clarification=None,
                context=self._build_incident_context(
                    req=req,
                    normalized_mode=normalized_mode,
                    brief=brief,
                    recent_worker_reports=recent_worker_reports,
                ),
            )
            return await self._resolve_incident_outcome(
                req=req,
                turn_decision=turn_decision,
                brief=brief,
                incident_decision=incident_decision,
                controller_session=controller_session,
                incident_session=incident_session,
                code_session_payload=code_session_payload,
                research_session_payload=research_session_payload,
                recent_worker_reports=recent_worker_reports,
            )

        worker_report = WorkerReport(
            lane="legacy",
            status="handoff",
            summary=turn_decision.public_response or "Routing this through the general operator lane.",
            objective=brief.desired_delta,
            acceptance_criteria=list(brief.success_criteria),
        )
        review = ReviewDecision(
            action="continue",
            public_response=turn_decision.public_response or "Routing this through the general operator lane.",
            reason="The request needs the shared legacy execution substrate.",
            next_lane="legacy",
        )
        return DANV2TurnOutcome(
            turn_decision=turn_decision,
            supervisor_brief=brief,
            worker_report=worker_report,
            review_decision=review,
            direct_response=None,
            handoff_prompt_context=brief.render_prompt_context(),
            handoff_metadata={"selected_lane": "legacy"},
            controller_session_payload=self._controller.dump_session(controller_session),
            code_session_payload=code_session_payload,
            research_session_payload=research_session_payload,
            incident_session_payload=incident_session_payload,
        )

    async def _resolve_code_outcome(
        self,
        *,
        req: Any,
        turn_decision: DANConversationTurnDecision,
        brief: SupervisorBrief,
        code_decision: CodingConversationTurnDecision,
        controller_session: Any,
        code_session: Any,
        research_session_payload: dict[str, Any] | None,
        incident_session_payload: dict[str, Any] | None,
    ) -> DANV2TurnOutcome:
        if code_decision.action == "respond":
            response = _compose_user_response(code_decision.public_response)
            worker_report = WorkerReport(
                lane="code",
                status="responded",
                summary=response,
            )
            review = ReviewDecision(
                action="stop",
                public_response=response,
                reason="DAN Code answered directly without launching execution.",
            )
            return DANV2TurnOutcome(
                turn_decision=turn_decision,
                supervisor_brief=brief,
                worker_report=worker_report,
                review_decision=review,
                direct_response=response,
                controller_session_payload=self._controller.dump_session(controller_session),
                code_session_payload=self._code_controller.dump_session(code_session),
                research_session_payload=research_session_payload,
                incident_session_payload=incident_session_payload,
            )

        if code_decision.action == "clarify":
            response = _compose_user_response(
                code_decision.public_response,
                code_decision.clarifying_question,
            )
            worker_report = WorkerReport(
                lane="code",
                status="clarify",
                summary=response,
                blockers=[code_decision.clarifying_question] if code_decision.clarifying_question else [],
            )
            review = ReviewDecision(
                action="stop",
                public_response=response,
                reason="DAN Code needs one concrete clarification before execution.",
            )
            return DANV2TurnOutcome(
                turn_decision=turn_decision,
                supervisor_brief=brief,
                worker_report=worker_report,
                review_decision=review,
                direct_response=response,
                controller_session_payload=self._controller.dump_session(controller_session),
                code_session_payload=self._code_controller.dump_session(code_session),
                research_session_payload=research_session_payload,
                incident_session_payload=incident_session_payload,
            )

        objective = _merge_unique_texts(
            code_decision.coding_objective,
            brief.desired_delta,
        )
        success_criteria = list(code_decision.acceptance_criteria or brief.success_criteria)
        prompt_context = _merge_prompt_context(
            brief.render_prompt_context(),
            "DAN Code handoff:",
            f"- Objective: {objective or str(req.message or '').strip()}",
            "- Acceptance criteria: " + "; ".join(success_criteria)
            if success_criteria
            else "",
            "- Research findings: " + "; ".join(code_decision.research_findings)
            if code_decision.research_findings
            else "",
            f"- Repair brief: {code_decision.repair_brief}" if str(code_decision.repair_brief or "").strip() else "",
        )
        public_response = code_decision.public_response or turn_decision.public_response or "Routing this through DAN Code."
        if self._direct_code_execution_enabled(req):
            try:
                report = await self._run_direct_code_execution(
                    req=req,
                    objective=objective or str(req.message or "").strip(),
                    acceptance_criteria=list(success_criteria),
                    research_findings=list(code_decision.research_findings),
                    repair_brief=str(code_decision.repair_brief or ""),
                )
            except Exception:
                logger.warning(
                    "Direct DAN Code execution failed in the v2 control plane; falling back to legacy handoff",
                    exc_info=True,
                )
            else:
                direct_response = self._build_direct_code_response(report)
                blockers = []
                error = _clean_text(report.get("error") or "")
                if error and str(report.get("status") or "").strip().lower() != "completed":
                    blockers.append(error)
                return DANV2TurnOutcome(
                    turn_decision=turn_decision,
                    supervisor_brief=brief,
                    worker_report=WorkerReport(
                        lane="code",
                        status="responded",
                        summary=direct_response,
                        objective=objective or str(req.message or "").strip(),
                        acceptance_criteria=list(success_criteria),
                        blockers=blockers,
                    ),
                    review_decision=ReviewDecision(
                        action="stop",
                        public_response=direct_response,
                        reason=(
                            "DAN Code executed directly inside the DAN-v2 control plane and returned a bounded report."
                        ),
                    ),
                    direct_response=direct_response,
                    handoff_metadata={
                        "selected_lane": "code",
                        "code_execution_mode": "direct",
                        "coding_objective": objective,
                        "acceptance_criteria": list(success_criteria),
                        "repair_brief": code_decision.repair_brief,
                        "coding_status": str(report.get("status") or ""),
                        "candidate_id": report.get("candidate_id"),
                        "trace_id": str(report.get("trace_id") or ""),
                        "target_files": _normalize_text_list(report.get("target_files")),
                        "test_plan": _normalize_text_list(report.get("test_plan")),
                        "risks": _normalize_text_list(report.get("risks")),
                    },
                    controller_session_payload=self._controller.dump_session(controller_session),
                    code_session_payload=self._code_controller.dump_session(code_session),
                    research_session_payload=research_session_payload,
                    incident_session_payload=incident_session_payload,
                )
        worker_report = WorkerReport(
            lane="code",
            status="ready",
            summary=public_response,
            objective=objective or str(req.message or "").strip(),
            acceptance_criteria=list(success_criteria),
        )
        review = ReviewDecision(
            action="continue",
            public_response=public_response,
            reason="DAN Code shaped the task and the shared execution substrate should run it next.",
            next_lane="code",
        )
        return DANV2TurnOutcome(
            turn_decision=turn_decision,
            supervisor_brief=brief,
            worker_report=worker_report,
            review_decision=review,
            direct_response=None,
            handoff_prompt_context=prompt_context,
            handoff_metadata={
                "selected_lane": "code",
                "coding_objective": objective,
                "acceptance_criteria": list(success_criteria),
                "repair_brief": code_decision.repair_brief,
            },
            controller_session_payload=self._controller.dump_session(controller_session),
            code_session_payload=self._code_controller.dump_session(code_session),
            research_session_payload=research_session_payload,
            incident_session_payload=incident_session_payload,
        )

    def _resolve_research_outcome(
        self,
        *,
        req: Any,
        turn_decision: DANConversationTurnDecision,
        brief: SupervisorBrief,
        research_decision: ResearchConversationTurnDecision,
        controller_session: Any,
        research_session: Any,
        code_session_payload: dict[str, Any] | None,
        incident_session_payload: dict[str, Any] | None,
    ) -> DANV2TurnOutcome:
        if research_decision.action == "respond":
            response = _compose_user_response(research_decision.public_response)
            worker_report = WorkerReport(
                lane="research",
                status="responded",
                summary=response,
            )
            review = ReviewDecision(
                action="stop",
                public_response=response,
                reason="DAN Research answered directly without launching a bounded run.",
            )
            return DANV2TurnOutcome(
                turn_decision=turn_decision,
                supervisor_brief=brief,
                worker_report=worker_report,
                review_decision=review,
                direct_response=response,
                controller_session_payload=self._controller.dump_session(controller_session),
                code_session_payload=code_session_payload,
                research_session_payload=self._research_controller.dump_session(research_session),
                incident_session_payload=incident_session_payload,
            )

        if research_decision.action == "clarify":
            response = _compose_user_response(
                research_decision.public_response,
                research_decision.clarifying_question,
            )
            worker_report = WorkerReport(
                lane="research",
                status="clarify",
                summary=response,
                blockers=[research_decision.clarifying_question] if research_decision.clarifying_question else [],
            )
            review = ReviewDecision(
                action="stop",
                public_response=response,
                reason="DAN Research needs one concrete clarification before execution.",
            )
            return DANV2TurnOutcome(
                turn_decision=turn_decision,
                supervisor_brief=brief,
                worker_report=worker_report,
                review_decision=review,
                direct_response=response,
                controller_session_payload=self._controller.dump_session(controller_session),
                code_session_payload=code_session_payload,
                research_session_payload=self._research_controller.dump_session(research_session),
                incident_session_payload=incident_session_payload,
            )

        objective = _merge_unique_texts(
            research_decision.research_objective,
            brief.desired_delta,
        )
        success_criteria = list(research_decision.acceptance_criteria or brief.success_criteria)
        prompt_context = _merge_prompt_context(
            brief.render_prompt_context(),
            "DAN Research handoff:",
            f"- Objective: {objective or str(req.message or '').strip()}",
            f"- Delivery target: {research_decision.delivery_target}" if str(research_decision.delivery_target or "").strip() else "",
            "- Acceptance criteria: " + "; ".join(success_criteria)
            if success_criteria
            else "",
        )
        public_response = research_decision.public_response or turn_decision.public_response or "Routing this through DAN Research."
        worker_report = WorkerReport(
            lane="research",
            status="ready",
            summary=public_response,
            objective=objective or str(req.message or "").strip(),
            acceptance_criteria=list(success_criteria),
        )
        review = ReviewDecision(
            action="continue",
            public_response=public_response,
            reason="DAN Research shaped the task and the shared execution substrate should run it next.",
            next_lane="research",
        )
        return DANV2TurnOutcome(
            turn_decision=turn_decision,
            supervisor_brief=brief,
            worker_report=worker_report,
            review_decision=review,
            direct_response=None,
            handoff_prompt_context=prompt_context,
            handoff_metadata={
                "selected_lane": "research",
                "research_objective": objective,
                "acceptance_criteria": list(success_criteria),
                "delivery_target": research_decision.delivery_target,
            },
            controller_session_payload=self._controller.dump_session(controller_session),
            code_session_payload=code_session_payload,
            research_session_payload=self._research_controller.dump_session(research_session),
            incident_session_payload=incident_session_payload,
        )

    async def _resolve_incident_outcome(
        self,
        *,
        req: Any,
        turn_decision: DANConversationTurnDecision,
        brief: SupervisorBrief,
        incident_decision: IncidentConversationTurnDecision,
        controller_session: Any,
        incident_session: Any,
        code_session_payload: dict[str, Any] | None,
        research_session_payload: dict[str, Any] | None,
        recent_worker_reports: list[WorkerReport],
    ) -> DANV2TurnOutcome:
        incident_session_payload = self._incident_controller.dump_session(incident_session)
        if incident_decision.action == "respond":
            response = _compose_user_response(
                incident_decision.public_response
                or f"Incident state: {incident_decision.terminal_state}."
            )
            worker_report = WorkerReport(
                lane="incident",
                status="responded",
                summary=response,
                objective=incident_decision.desired_delta,
                acceptance_criteria=list(incident_decision.success_criteria),
            )
            review = ReviewDecision(
                action="stop",
                public_response=response,
                reason=(
                    "Incident Commander stopped with terminal_state="
                    f"{incident_decision.terminal_state}."
                ),
            )
            return DANV2TurnOutcome(
                turn_decision=turn_decision,
                supervisor_brief=brief,
                worker_report=worker_report,
                review_decision=review,
                direct_response=response,
                controller_session_payload=self._controller.dump_session(controller_session),
                code_session_payload=code_session_payload,
                research_session_payload=research_session_payload,
                incident_session_payload=incident_session_payload,
            )

        if incident_decision.action == "clarify":
            response = _compose_user_response(
                incident_decision.public_response,
                incident_decision.clarifying_question,
            )
            worker_report = WorkerReport(
                lane="incident",
                status="clarify",
                summary=response,
                objective=incident_decision.desired_delta,
                acceptance_criteria=list(incident_decision.success_criteria),
                blockers=[incident_decision.clarifying_question]
                if incident_decision.clarifying_question
                else [],
            )
            review = ReviewDecision(
                action="stop",
                public_response=response,
                reason="Incident Commander needs one concrete clarification before remediation.",
            )
            return DANV2TurnOutcome(
                turn_decision=turn_decision,
                supervisor_brief=brief,
                worker_report=worker_report,
                review_decision=review,
                direct_response=response,
                controller_session_payload=self._controller.dump_session(controller_session),
                code_session_payload=code_session_payload,
                research_session_payload=research_session_payload,
                incident_session_payload=incident_session_payload,
            )

        incident_brief = self._incident_controller.build_supervisor_brief(
            incident_decision
        )
        if incident_brief is None:
            response = _compose_user_response(
                incident_decision.public_response
                or "Incident Commander could not choose a safe bounded action yet."
            )
            worker_report = WorkerReport(
                lane="incident",
                status="clarify",
                summary=response,
                objective=incident_decision.desired_delta,
                acceptance_criteria=list(incident_decision.success_criteria),
            )
            review = ReviewDecision(
                action="stop",
                public_response=response,
                reason="Incident Commander did not produce a valid downstream action brief.",
            )
            return DANV2TurnOutcome(
                turn_decision=turn_decision,
                supervisor_brief=brief,
                worker_report=worker_report,
                review_decision=review,
                direct_response=response,
                controller_session_payload=self._controller.dump_session(controller_session),
                code_session_payload=code_session_payload,
                research_session_payload=research_session_payload,
                incident_session_payload=incident_session_payload,
            )

        execution_request = self._build_incident_execution_request(
            req=req,
            incident_decision=incident_decision,
        )
        execution_mode = "handoff"
        execution_report = await self._execute_live_workflow_retry(
            req=req,
            incident_decision=incident_decision,
            execution_request=execution_request,
        )
        if execution_report is not None:
            execution_mode = "live"
        else:
            execution_report = await self._execute_live_workflow_incident_action(
                req=req,
                incident_decision=incident_decision,
                execution_request=execution_request,
            )
            if execution_report is not None:
                execution_mode = "live"
        if execution_report is None and execution_request is not None:
            execution_report = execute_incident_action(execution_request)
            execution_mode = "deterministic"
        if (
            execution_report is not None
            and execution_mode == "live"
            and execution_report.action_id == "retry"
            and execution_report.terminal_state == "open"
        ):
            response = _compose_user_response(
                incident_decision.public_response,
                execution_report.public_summary,
            )
            retry_run_id = str(
                execution_report.action_result.artifacts.get("retry_run_id")
                if isinstance(execution_report.action_result.artifacts, dict)
                else ""
            ).strip()
            worker_report = WorkerReport(
                lane="incident",
                status="responded",
                summary=response,
                objective=execution_report.objective,
                acceptance_criteria=list(incident_brief.success_criteria),
                blockers=list(execution_report.verification_result.required_follow_up),
            )
            review = ReviewDecision(
                action="stop",
                public_response=response,
                reason="Incident Commander launched the live retry and is waiting for the new run to settle.",
            )
            handoff_metadata = {
                "selected_lane": "incident",
                "incident_terminal_state": execution_report.terminal_state,
                "incident_action": execution_report.action_id,
                "incident_execution_mode": execution_mode,
            }
            if retry_run_id:
                handoff_metadata["retry_run_id"] = retry_run_id
            return DANV2TurnOutcome(
                turn_decision=turn_decision,
                supervisor_brief=incident_brief,
                worker_report=worker_report,
                review_decision=review,
                direct_response=response,
                handoff_metadata=handoff_metadata,
                controller_session_payload=self._controller.dump_session(controller_session),
                code_session_payload=code_session_payload,
                research_session_payload=research_session_payload,
                incident_session_payload=incident_session_payload,
            )
        if execution_report is not None and execution_report.terminal_state != "open":
            response = _compose_user_response(
                incident_decision.public_response,
                execution_report.public_summary,
            )
            worker_report = WorkerReport(
                lane="incident",
                status="responded",
                summary=response,
                objective=execution_report.objective,
                acceptance_criteria=list(incident_brief.success_criteria),
                blockers=list(execution_report.verification_result.required_follow_up),
            )
            review = ReviewDecision(
                action="stop",
                public_response=response,
                reason=(
                    f"Incident Commander {execution_mode} execution ended with terminal_state="
                    f"{execution_report.terminal_state}."
                ),
            )
            return DANV2TurnOutcome(
                turn_decision=turn_decision,
                supervisor_brief=incident_brief,
                worker_report=worker_report,
                review_decision=review,
                direct_response=response,
                handoff_metadata={
                    "selected_lane": "incident",
                    "incident_terminal_state": execution_report.terminal_state,
                    "incident_action": execution_report.action_id,
                    "incident_execution_mode": execution_mode,
                },
                controller_session_payload=self._controller.dump_session(controller_session),
                code_session_payload=code_session_payload,
                research_session_payload=research_session_payload,
                incident_session_payload=incident_session_payload,
            )

        if execution_report is not None and execution_report.action_result.next_lane in {"code", "legacy"}:
            incident_brief = incident_brief.model_copy(
                update={"lane": execution_report.action_result.next_lane}
            )

        incident_prompt_context = _merge_prompt_context(
            brief.render_prompt_context(),
            incident_brief.render_prompt_context(),
            "Incident Commander handoff:",
            f"- Scenario: {incident_decision.incident_scenario_id or 'unclassified'}",
            f"- Severity: {incident_decision.severity}",
            f"- Chosen action: {incident_decision.chosen_action}",
            f"- Terminal state before action: {incident_decision.terminal_state}",
            f"- Execution status: {execution_report.terminal_state}"
            if execution_report is not None
            else "",
            "- Verification checks: " + "; ".join(incident_decision.verification_checks)
            if incident_decision.verification_checks
            else "",
            f"- Execution summary: {execution_report.public_summary}"
            if execution_report is not None
            else "",
        )
        if incident_brief.lane == "code":
            code_session = self._code_controller.load_session(code_session_payload)
            code_decision, code_session = await self._code_controller.decide_user_turn(
                session=code_session,
                user_message=str(incident_brief.desired_delta or req.message or ""),
                pending_clarification=None,
                context=self._build_code_context(
                    req=req,
                    brief=incident_brief,
                    recent_worker_reports=recent_worker_reports,
                ),
            )
            code_outcome = await self._resolve_code_outcome(
                req=req,
                turn_decision=turn_decision,
                brief=incident_brief,
                code_decision=code_decision,
                controller_session=controller_session,
                code_session=code_session,
                research_session_payload=research_session_payload,
                incident_session_payload=incident_session_payload,
            )
            downstream_lane = str(
                (code_outcome.handoff_metadata or {}).get("selected_lane") or "code"
            )
            handoff_metadata = dict(code_outcome.handoff_metadata or {})
            handoff_metadata.update(
                {
                    "selected_lane": "incident",
                    "incident_action_lane": "code",
                    "incident_scenario_id": incident_decision.incident_scenario_id,
                    "incident_terminal_state": incident_decision.terminal_state,
                    "incident_action": incident_decision.chosen_action,
                    "incident_severity": incident_decision.severity,
                    "incident_execution_mode": execution_mode,
                    "verification_checks": list(incident_decision.verification_checks),
                    "nested_lane": downstream_lane,
                }
            )
            return DANV2TurnOutcome(
                turn_decision=code_outcome.turn_decision,
                supervisor_brief=code_outcome.supervisor_brief,
                worker_report=code_outcome.worker_report,
                review_decision=code_outcome.review_decision,
                direct_response=code_outcome.direct_response,
                handoff_prompt_context=_merge_prompt_context(
                    incident_prompt_context,
                    code_outcome.handoff_prompt_context,
                ),
                handoff_metadata=handoff_metadata,
                controller_session_payload=code_outcome.controller_session_payload,
                code_session_payload=code_outcome.code_session_payload,
                research_session_payload=code_outcome.research_session_payload,
                incident_session_payload=code_outcome.incident_session_payload,
            )

        prompt_context = incident_prompt_context
        public_response = (
            incident_decision.public_response
            or turn_decision.public_response
            or "Routing this through Incident Commander."
        )
        worker_report = WorkerReport(
            lane="incident",
            status="handoff",
            summary=public_response,
            objective=incident_brief.desired_delta or str(req.message or "").strip(),
            acceptance_criteria=list(incident_brief.success_criteria),
        )
        review = ReviewDecision(
            action="continue",
            public_response=public_response,
            reason="Incident Commander chose the next bounded remediation lane.",
            next_lane=incident_brief.lane,
        )
        return DANV2TurnOutcome(
            turn_decision=turn_decision,
            supervisor_brief=incident_brief,
            worker_report=worker_report,
            review_decision=review,
            direct_response=None,
            handoff_prompt_context=prompt_context,
            handoff_metadata={
                "selected_lane": "incident",
                "incident_action_lane": incident_brief.lane,
                "incident_scenario_id": incident_decision.incident_scenario_id,
                "incident_terminal_state": incident_decision.terminal_state,
                "incident_action": incident_decision.chosen_action,
                "incident_severity": incident_decision.severity,
                "incident_execution_mode": execution_mode,
                "verification_checks": list(incident_decision.verification_checks),
            },
            controller_session_payload=self._controller.dump_session(controller_session),
            code_session_payload=code_session_payload,
            research_session_payload=research_session_payload,
            incident_session_payload=incident_session_payload,
        )


def build_dan_v2_runtime(
    *,
    chat_manager: Any,
    run_manager: Any | None = None,
) -> DANV2Runtime:
    return DANV2Runtime(chat_manager=chat_manager, run_manager=run_manager)


__all__ = [
    "ControlPlaneMode",
    "DANV2Runtime",
    "DANV2TurnOutcome",
    "DEFAULT_CONTROL_PLANE_MODE",
    "build_dan_v2_runtime",
    "resolve_control_plane_mode",
]
