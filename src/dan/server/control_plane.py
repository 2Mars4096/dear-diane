"""DAN control-plane selector and DAN-v2 runtime bridge."""

from __future__ import annotations

import inspect
import logging
import os
import re
import sys
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

_SHARED_OPERATOR_CONTROL_MEMBRANE = "supervisor_brief_worker_report_review_v1"
_OPERATOR_V1_NON_GOALS = (
    "No raw unrestricted AppleScript surface.",
    "No unsandboxed system-administration autonomy.",
    "No silent outbound messaging.",
    "No pseudo-motivational filler instead of concrete direction.",
)

_KNOWN_ADAPTER_SURFACES = (
    "telegram",
    "wechat",
    "whatsapp-web",
    "whatsapp",
    "email",
)

_LOCAL_OPERATOR_ACTION_CUES = (
    "create",
    "edit",
    "write",
    "patch",
    "fix",
    "repair",
    "implement",
    "refactor",
    "run",
    "test",
    "build",
    "branch",
    "commit",
)
_LOCAL_MUTATION_CUES = _LOCAL_OPERATOR_ACTION_CUES + (
    "delete",
    "rename",
    "move",
    "apply",
)
_BROWSER_DOWNLOAD_CUES = (
    "browser",
    "page",
    "site",
    "url",
    "navigate",
    "open the page",
    "open the site",
    "download",
    "screenshot",
    "screen capture",
    "extract text",
    "scrape",
    "form",
)
_BROWSER_INPUT_CUES = (
    "click",
    "fill",
    "submit",
    "type into",
    "log in",
    "login",
)
_DESKTOP_ACTION_CUES = (
    "desktop",
    "focus app",
    "open app",
    "window",
    "clipboard",
    "file dialog",
    "press key",
    "hotkey",
)
_MESSAGING_ACTION_CUES = (
    "send email",
    "email",
    "telegram",
    "wechat",
    "whatsapp",
    "message",
    "reply",
    "notify",
    "post",
)


@dataclass(frozen=True)
class OperatorEnvelopeProfile:
    use_case_pack: str
    safety_envelope: str
    supervision_policy: str
    stop_conditions: tuple[str, ...]


@dataclass(frozen=True)
class OperatorExecutionBoundary:
    execution_target: str
    deterministic_capability_sets: tuple[str, ...]
    deterministic_adapters: tuple[str, ...]
    shared_control_membrane: str = _SHARED_OPERATOR_CONTROL_MEMBRANE
    non_goals: tuple[str, ...] = _OPERATOR_V1_NON_GOALS


def classify_operator_profile(
    *,
    message: str,
    requested_mode: str = "",
) -> OperatorEnvelopeProfile:
    normalized_message = _clean_text(message).lower()
    normalized_mode = _clean_text(requested_mode).lower()

    local_action = normalized_mode in {"build", "mutate"} or _contains_any(
        normalized_message,
        _LOCAL_OPERATOR_ACTION_CUES,
    )
    browser_action = _contains_any(normalized_message, _BROWSER_DOWNLOAD_CUES)
    desktop_action = _contains_any(normalized_message, _DESKTOP_ACTION_CUES)
    messaging_action = _contains_any(normalized_message, _MESSAGING_ACTION_CUES)

    active_domains = sum(
        1
        for flag in (
            local_action,
            browser_action,
            desktop_action or messaging_action,
        )
        if flag
    )
    if active_domains >= 2:
        use_case_pack = "cross_surface_operator"
    elif desktop_action or messaging_action:
        use_case_pack = "desktop_messaging"
    elif browser_action:
        use_case_pack = "browser_download"
    elif local_action:
        use_case_pack = "local_operator"
    else:
        use_case_pack = "knowledge_local_context"

    external_side_effect = (
        _contains_any(normalized_message, _BROWSER_INPUT_CUES)
        or desktop_action
        or messaging_action
    )
    local_mutation = (
        normalized_mode in {"build", "mutate"}
        or _contains_any(normalized_message, _LOCAL_MUTATION_CUES)
        or "download" in normalized_message
    )
    if external_side_effect:
        return OperatorEnvelopeProfile(
            use_case_pack=use_case_pack,
            safety_envelope="external_side_effect",
            supervision_policy="approval_gate_for_external_side_effects",
            stop_conditions=(
                "Stop for explicit approval before browser input, desktop input, or outbound messaging.",
                "Stop and ask when the target app, account, page, or recipient is ambiguous.",
            ),
        )
    if local_mutation:
        return OperatorEnvelopeProfile(
            use_case_pack=use_case_pack,
            safety_envelope="local_mutation",
            supervision_policy="continue_with_local_guards",
            stop_conditions=(
                "Stop and ask when the target repo, file, command, or desired delta is ambiguous.",
            ),
        )
    return OperatorEnvelopeProfile(
        use_case_pack=use_case_pack,
        safety_envelope="read_only",
        supervision_policy="continue_with_evidence",
        stop_conditions=(
            "Stop and ask when the scope, evidence target, or comparison frame is ambiguous.",
        ),
    )


def resolve_operator_execution_boundary(
    *,
    profile: OperatorEnvelopeProfile,
    available_tool_families: list[str] | None = None,
    available_adapters: list[str] | None = None,
) -> OperatorExecutionBoundary:
    tool_families = {
        text.lower()
        for text in _normalize_text_list(available_tool_families)
    }
    adapters = tuple(
        adapter
        for adapter in _dedupe_texts(
            [text.lower() for text in _normalize_text_list(available_adapters)]
        )
        if adapter in _KNOWN_ADAPTER_SURFACES
    )

    capability_sets: list[str] = []
    use_case_pack = profile.use_case_pack
    if use_case_pack == "knowledge_local_context":
        capability_sets.append("local_context_readers")
        if not tool_families or tool_families & {"web", "browser", "research"}:
            capability_sets.append("grounded_web_readers")
    elif use_case_pack == "local_operator":
        capability_sets.append("workspace_mutation")
        if not tool_families or tool_families & {"shell", "git"}:
            capability_sets.append("shell_git")
    elif use_case_pack == "browser_download":
        capability_sets.extend(["browser_navigation", "artifact_downloads"])
    elif use_case_pack == "desktop_messaging":
        capability_sets.append("desktop_control")
        if adapters or "adapters" in tool_families:
            capability_sets.append("messaging_adapters")
    elif use_case_pack == "cross_surface_operator":
        if not tool_families or tool_families & {"files", "shell", "git", "code"}:
            capability_sets.append("workspace_mutation")
        if not tool_families or tool_families & {"shell", "git"}:
            capability_sets.append("shell_git")
        if not tool_families or tool_families & {"browser", "web"}:
            capability_sets.append("browser_navigation")
        if not tool_families or tool_families & {"browser", "files"}:
            capability_sets.append("artifact_downloads")
        if not tool_families or tool_families & {"desktop"}:
            capability_sets.append("desktop_control")
        if adapters or "adapters" in tool_families:
            capability_sets.append("messaging_adapters")

    execution_target = (
        "inline_or_specialist"
        if profile.use_case_pack == "knowledge_local_context"
        and profile.safety_envelope == "read_only"
        else "bounded_operator_lane"
    )
    return OperatorExecutionBoundary(
        execution_target=execution_target,
        deterministic_capability_sets=tuple(_dedupe_texts(capability_sets)),
        deterministic_adapters=adapters,
    )


def parse_control_plane_mode(value: Any | None) -> ControlPlaneMode | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    normalized = _CONTROL_PLANE_ALIASES.get(raw, raw)
    if normalized not in {"v1", "v2"}:
        raise ValueError("control plane mode must resolve to `v1` or `v2`")
    return "v2" if normalized == "v2" else "v1"


def resolve_control_plane_mode(value: Any | None = None) -> ControlPlaneMode:
    raw = (
        value
        if value is not None
        else os.environ.get("DAN_CONTROL_PLANE", DEFAULT_CONTROL_PLANE_MODE)
    )
    try:
        parsed = parse_control_plane_mode(raw)
    except ValueError:
        return DEFAULT_CONTROL_PLANE_MODE
    return parsed or DEFAULT_CONTROL_PLANE_MODE


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


def _contains_any(text: str, cues: tuple[str, ...]) -> bool:
    lowered = str(text or "").strip().lower()
    for cue in cues:
        normalized_cue = str(cue or "").strip().lower()
        if not normalized_cue:
            continue
        if " " in normalized_cue:
            if normalized_cue in lowered:
                return True
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(normalized_cue)}(?![a-z0-9])", lowered):
            return True
    return False


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


def _normalize_texts(*parts: Any) -> list[str]:
    normalized: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (list, tuple, set)):
            normalized.extend(_normalize_text_list(part))
            continue
        text = _clean_text(part)
        if text:
            normalized.append(text)
    return _dedupe_texts(normalized)


def _compact_artifacts(raw: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    payload = dict(raw or {})
    payload.update(kwargs)
    compact: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
            if text:
                compact[key] = text
            continue
        if isinstance(value, dict):
            nested = _compact_artifacts(value)
            if nested:
                compact[key] = nested
            continue
        if isinstance(value, (list, tuple, set)):
            items = _normalize_text_list(value)
            if items:
                compact[key] = items
            continue
        compact[key] = value
    return compact


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
        operator_profile = self._operator_profile(req)
        operator_boundary = self._operator_execution_boundary(req)
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
                workspace_root=self._workspace_root(req),
                platform=str(
                    (
                        req.surface_context.get("platform")
                        if isinstance(req.surface_context, dict)
                        else ""
                    )
                    or sys.platform
                ),
                approval_mode=self._approval_mode(req),
                active_model=self._model,
                requested_mode=str(req.mode or ""),
                normalized_mode=str(normalized_mode or ""),
                operator_use_case_pack=operator_profile.use_case_pack,
                operator_safety_envelope=operator_profile.safety_envelope,
                operator_supervision_policy=operator_profile.supervision_policy,
                operator_stop_conditions=list(operator_profile.stop_conditions),
                operator_execution_target=operator_boundary.execution_target,
                operator_deterministic_capability_sets=list(
                    operator_boundary.deterministic_capability_sets
                ),
                operator_deterministic_adapters=list(
                    operator_boundary.deterministic_adapters
                ),
                operator_shared_control_membrane=operator_boundary.shared_control_membrane,
                operator_non_goals=list(operator_boundary.non_goals),
                available_organisms=["code", "research", "incident", "legacy"],
                available_adapters=self._available_adapters(req),
                available_tool_families=self._available_tool_families(req),
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

    def _approval_mode(self, req: Any) -> str:
        surface_context = req.surface_context if isinstance(req.surface_context, dict) else {}
        text = str(
            surface_context.get("approval_mode")
            or getattr(req, "approval_mode", "")
            or "server"
        ).strip()
        return text or "server"

    def _available_adapters(self, req: Any) -> list[str]:
        surface_context = req.surface_context if isinstance(req.surface_context, dict) else {}
        return _normalize_text_list(
            surface_context.get("available_adapters") or surface_context.get("adapters")
        )

    def _available_tool_families(self, req: Any) -> list[str]:
        surface_context = req.surface_context if isinstance(req.surface_context, dict) else {}
        explicit = _normalize_text_list(
            surface_context.get("available_tool_families")
            or surface_context.get("tool_families")
        )
        if explicit:
            return explicit
        inferred = [
            "files",
            "shell",
            "git",
            "web",
            "code",
            "research",
            "incident",
        ]
        if self._run_manager is not None:
            inferred.append("workflow_runs")
        if self._available_adapters(req):
            inferred.append("adapters")
        return _dedupe_texts(inferred)

    def _enabled_tools(self, req: Any) -> list[str]:
        surface_context = req.surface_context if isinstance(req.surface_context, dict) else {}
        explicit = _normalize_text_list(
            surface_context.get("enabled_tools")
            or surface_context.get("tool_ids")
        )
        if explicit:
            return explicit
        return self._available_tool_families(req)

    def _operator_profile(self, req: Any) -> OperatorEnvelopeProfile:
        return classify_operator_profile(
            message=str(getattr(req, "message", "") or ""),
            requested_mode=str(getattr(req, "mode", "") or ""),
        )

    def _operator_profile_payload(self, req: Any) -> dict[str, Any]:
        profile = self._operator_profile(req)
        return {
            "operator_use_case_pack": profile.use_case_pack,
            "operator_safety_envelope": profile.safety_envelope,
            "operator_supervision_policy": profile.supervision_policy,
            "operator_stop_conditions": list(profile.stop_conditions),
        }

    def _operator_execution_boundary(self, req: Any) -> OperatorExecutionBoundary:
        return resolve_operator_execution_boundary(
            profile=self._operator_profile(req),
            available_tool_families=self._available_tool_families(req),
            available_adapters=self._available_adapters(req),
        )

    def _operator_execution_boundary_payload(self, req: Any) -> dict[str, Any]:
        boundary = self._operator_execution_boundary(req)
        return {
            "operator_execution_target": boundary.execution_target,
            "operator_deterministic_capability_sets": list(
                boundary.deterministic_capability_sets
            ),
            "operator_deterministic_adapters": list(
                boundary.deterministic_adapters
            ),
            "operator_shared_control_membrane": boundary.shared_control_membrane,
            "operator_non_goals": list(boundary.non_goals),
        }

    def _operator_contract_payload(self, req: Any) -> dict[str, Any]:
        return {
            **self._operator_profile_payload(req),
            **self._operator_execution_boundary_payload(req),
        }

    def _direct_code_execution_enabled(self, req: Any) -> bool:
        surface_context = req.surface_context if isinstance(req.surface_context, dict) else {}
        if "direct_code_execution" in surface_context:
            return _is_truthy(surface_context.get("direct_code_execution"))
        env_value = os.environ.get("DAN_V2_DIRECT_CODE_RUNTIME")
        if str(env_value or "").strip():
            return _is_truthy(env_value)
        return True

    async def _list_live_adapter_snapshots(self) -> list[dict[str, Any]]:
        lister = getattr(self, "_adapter_snapshot_lister", None)
        if callable(lister):
            result = lister()
            if inspect.isawaitable(result):
                result = await result
            return [dict(item) for item in list(result or []) if isinstance(item, dict)]
        try:
            from dan.server.routers import adapters as adapters_router
        except Exception:
            logger.debug("Failed to import adapter router for incident inspection", exc_info=True)
            return []
        status_reader = getattr(adapters_router, "adapter_status", None)
        if not callable(status_reader):
            return []
        try:
            result = status_reader()
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            logger.debug("Failed to inspect live adapters for incident evidence", exc_info=True)
            return []
        return [dict(item) for item in list(result or []) if isinstance(item, dict)]

    async def _adapter_config_summary(self, surface_type: str) -> dict[str, Any]:
        loader = getattr(self, "_adapter_config_summary_loader", None)
        if callable(loader):
            result = loader(surface_type)
            if inspect.isawaitable(result):
                result = await result
            return dict(result or {})
        try:
            from dan.server.routers import adapters as adapters_router
        except Exception:
            logger.debug("Failed to import adapter router for config lookup", exc_info=True)
            return {}
        summary_builders = {
            "telegram": "_build_telegram_config_summary",
            "whatsapp-web": "_build_whatsapp_web_config_summary",
            "wechat": "_build_wechat_official_account_config_summary",
        }
        builder_name = summary_builders.get(str(surface_type or "").strip().lower())
        if not builder_name:
            return {}
        builder = getattr(adapters_router, builder_name, None)
        if not callable(builder):
            return {}
        try:
            result = builder()
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            logger.debug("Failed to build adapter config summary for %s", surface_type, exc_info=True)
            return {}
        return dict(result or {})

    async def _stop_live_adapter(self, adapter_id: str, *, missing_ok: bool = False) -> bool:
        stopper = getattr(self, "_adapter_stop_runner", None)
        if callable(stopper):
            result = stopper(adapter_id, missing_ok=missing_ok)
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        try:
            from dan.server.routers import adapters as adapters_router
        except Exception:
            logger.debug("Failed to import adapter router for stop action", exc_info=True)
            return False
        stop_active = getattr(adapters_router, "_stop_active_adapter", None)
        if not callable(stop_active):
            return False
        try:
            result = stop_active(adapter_id, missing_ok=missing_ok)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            logger.debug("Failed to stop live adapter %s", adapter_id, exc_info=True)
            return False
        return bool(result)

    async def _restart_live_adapter(
        self,
        *,
        surface_type: str,
        adapter_id: str = "",
    ) -> dict[str, Any]:
        runner = getattr(self, "_adapter_restart_runner", None)
        if callable(runner):
            result = runner(surface_type, adapter_id=adapter_id)
            if inspect.isawaitable(result):
                result = await result
            return dict(result or {})
        normalized_surface = _clean_text(surface_type).lower()
        if normalized_surface not in {"telegram", "whatsapp-web", "wechat"}:
            raise RuntimeError(
                f"live retry is not supported for adapter surface `{normalized_surface or 'unknown'}`"
            )
        config_summary = await self._adapter_config_summary(normalized_surface)
        if not config_summary.get("configured"):
            raise RuntimeError(
                f"stored adapter config is missing for `{normalized_surface}`"
            )
        if adapter_id:
            await self._stop_live_adapter(adapter_id, missing_ok=True)
        try:
            from dan.server.routers.adapters import AdapterStartRequest, start_adapter
        except Exception as exc:
            raise RuntimeError("adapter runtime is unavailable for live retry") from exc
        result = start_adapter(
            AdapterStartRequest(
                type=normalized_surface,
                config={},
            )
        )
        if inspect.isawaitable(result):
            result = await result
        return dict(result or {})

    @staticmethod
    def _adapter_snapshot_sort_key(snapshot: dict[str, Any]) -> tuple[int, float, float]:
        state = _clean_text(snapshot.get("connection_state")).lower()
        last_error = _clean_text(snapshot.get("last_error"))
        priority = 0
        if last_error or state == "error":
            priority = 4
        elif state in {"reconnecting", "pairing", "starting"}:
            priority = 3
        elif state == "disconnected":
            priority = 2
        elif snapshot.get("paired") is False:
            priority = 1
        return (
            priority,
            _coerce_timestamp(snapshot.get("session_count")),
            _coerce_timestamp(snapshot.get("uptime_seconds")),
        )

    def _preferred_adapter_surfaces(
        self,
        *,
        req: Any,
        incident_decision: IncidentConversationTurnDecision,
        execution_request: IncidentExecutionRequest | None = None,
    ) -> list[str]:
        surface_context = req.surface_context if isinstance(req.surface_context, dict) else {}
        adapter_context = (
            dict(surface_context.get("adapter"))
            if isinstance(surface_context.get("adapter"), dict)
            else {}
        )
        known = set(_KNOWN_ADAPTER_SURFACES)
        explicit = _dedupe_texts(
            [
                *(
                    item
                    for item in self._available_adapters(req)
                    if _clean_text(item).lower() in known
                ),
                str(adapter_context.get("surface") or ""),
                str(execution_request.evidence.get("adapter_type") or "")
                if execution_request is not None
                else "",
            ]
        )
        cue_text = _clean_text(
            " ".join(
                part
                for part in [
                    str(req.message or ""),
                    incident_decision.desired_delta,
                    str(execution_request.target or "") if execution_request is not None else "",
                ]
                if _clean_text(part)
            )
        ).lower()
        mentioned = [surface for surface in _KNOWN_ADAPTER_SURFACES if surface in cue_text]
        return _dedupe_texts([*explicit, *mentioned])

    async def _select_failed_surface_snapshot(
        self,
        *,
        req: Any,
        incident_decision: IncidentConversationTurnDecision,
        execution_request: IncidentExecutionRequest | None = None,
        preferred_states: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        snapshots = await self._list_live_adapter_snapshots()
        if not snapshots:
            return None
        surface_context = req.surface_context if isinstance(req.surface_context, dict) else {}
        adapter_context = (
            dict(surface_context.get("adapter"))
            if isinstance(surface_context.get("adapter"), dict)
            else {}
        )
        preferred_surfaces = set(
            self._preferred_adapter_surfaces(
                req=req,
                incident_decision=incident_decision,
                execution_request=execution_request,
            )
        )
        preferred_states_set = {str(item).strip().lower() for item in preferred_states if str(item).strip()}
        requested_adapter_id = _clean_text(
            adapter_context.get("adapter_id")
            or (execution_request.evidence.get("adapter_id") if execution_request is not None else "")
        )
        cue_text = _clean_text(
            " ".join(
                part
                for part in [
                    str(req.message or ""),
                    incident_decision.desired_delta,
                    str(execution_request.target or "") if execution_request is not None else "",
                    requested_adapter_id,
                ]
                if _clean_text(part)
            )
        ).lower()
        candidates = [
            snapshot
            for snapshot in snapshots
            if not preferred_surfaces
            or _clean_text(snapshot.get("type")).lower() in preferred_surfaces
        ]
        if requested_adapter_id:
            exact = [
                snapshot
                for snapshot in candidates
                if _clean_text(snapshot.get("adapter_id")) == requested_adapter_id
            ]
            if exact:
                return sorted(exact, key=self._adapter_snapshot_sort_key, reverse=True)[0]
        direct_matches = [
            snapshot
            for snapshot in candidates
            if (
                _clean_text(snapshot.get("adapter_id")).lower()
                and _clean_text(snapshot.get("adapter_id")).lower() in cue_text
            )
            or (
                _clean_text(snapshot.get("type")).lower()
                and _clean_text(snapshot.get("type")).lower() in cue_text
            )
        ]
        if preferred_states_set:
            preferred_direct = [
                snapshot
                for snapshot in direct_matches
                if _clean_text(snapshot.get("connection_state")).lower() in preferred_states_set
            ]
            if preferred_direct:
                return sorted(preferred_direct, key=self._adapter_snapshot_sort_key, reverse=True)[0]
        if direct_matches:
            return sorted(direct_matches, key=self._adapter_snapshot_sort_key, reverse=True)[0]
        if preferred_states_set:
            preferred = [
                snapshot
                for snapshot in candidates
                if _clean_text(snapshot.get("connection_state")).lower() in preferred_states_set
            ]
            if preferred:
                return sorted(preferred, key=self._adapter_snapshot_sort_key, reverse=True)[0]
        concerning = [
            snapshot
            for snapshot in candidates
            if _clean_text(snapshot.get("last_error"))
            or _clean_text(snapshot.get("connection_state")).lower()
            in {"error", "reconnecting", "pairing", "starting", "disconnected"}
            or snapshot.get("paired") is False
        ]
        if not concerning:
            return None
        return sorted(concerning, key=self._adapter_snapshot_sort_key, reverse=True)[0]

    def _surface_snapshot_evidence(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        evidence: dict[str, Any] = {}
        adapter_id = _clean_text(snapshot.get("adapter_id"))
        surface_type = _clean_text(snapshot.get("type"))
        connection_state = _clean_text(snapshot.get("connection_state"))
        last_error = _clean_text(snapshot.get("last_error"))
        if adapter_id:
            evidence["adapter_id"] = adapter_id
        if surface_type:
            evidence["adapter_type"] = surface_type
        if connection_state:
            evidence["connection_state"] = connection_state
            evidence["current_status"] = connection_state
            evidence["latest_status"] = connection_state
        if last_error:
            evidence["last_error"] = last_error
        if "running" in snapshot:
            evidence["running"] = bool(snapshot.get("running"))
        if "paired" in snapshot and snapshot.get("paired") is not None:
            evidence["paired"] = bool(snapshot.get("paired"))
        if snapshot.get("session_count") is not None:
            evidence["session_count"] = snapshot.get("session_count")
        return evidence

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
                approval_mode=self._approval_mode(req),
                enabled_tools=self._enabled_tools(req),
                available_tool_families=self._available_tool_families(req),
                available_adapters=self._available_adapters(req),
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
                "approval_mode": self._approval_mode(req),
                "enabled_tools": self._enabled_tools(req),
                "available_tool_families": self._available_tool_families(req),
                "available_adapters": self._available_adapters(req),
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

    @staticmethod
    def _build_incident_response(
        *,
        public_response: str,
        execution_report: IncidentExecutionReport,
        execution_mode: str,
    ) -> str:
        lines = _dedupe_texts(
            [
                public_response,
                execution_report.public_summary,
                f"Action: {execution_report.action_id} via {execution_mode}.",
                execution_report.action_result.summary,
                (
                    "Verification: "
                    + _clean_text(execution_report.verification_result.summary)
                )
                if _clean_text(execution_report.verification_result.summary)
                else "",
                (
                    "Follow-up: "
                    + "; ".join(execution_report.verification_result.required_follow_up)
                )
                if execution_report.verification_result.required_follow_up
                else "",
            ]
        )
        return "\n\n".join(lines)

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

    async def _build_failed_surface_execution_request(
        self,
        *,
        req: Any,
        incident_decision: IncidentConversationTurnDecision,
    ) -> IncidentExecutionRequest | None:
        if incident_decision.incident_scenario_id != "failed_external_surface_session":
            return None
        if incident_decision.chosen_action != "investigate":
            return None
        snapshot = await self._select_failed_surface_snapshot(
            req=req,
            incident_decision=incident_decision,
            preferred_states=("error", "reconnecting", "pairing", "starting", "disconnected"),
        )
        if snapshot is None:
            return None
        evidence = self._surface_snapshot_evidence(snapshot)
        adapter_id = _clean_text(snapshot.get("adapter_id"))
        surface_type = _clean_text(snapshot.get("type"))
        target = adapter_id or surface_type or _clean_text(req.message)
        verification_checks = _dedupe_texts(
            [
                *list(incident_decision.verification_checks),
                f"Confirm the live connection state for `{adapter_id or surface_type or 'the adapter surface'}`.",
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

    async def _execute_live_surface_incident_action(
        self,
        *,
        req: Any,
        incident_decision: IncidentConversationTurnDecision,
        execution_request: IncidentExecutionRequest | None,
    ) -> IncidentExecutionReport | None:
        if incident_decision.incident_scenario_id != "failed_external_surface_session":
            return None
        if incident_decision.chosen_action not in {"contain", "pause"}:
            return None
        boundary = resolve_incident_action_boundary(
            incident_decision.chosen_action,
            preferred_lane=incident_decision.action_lane or "legacy",
        )
        snapshot = await self._select_failed_surface_snapshot(
            req=req,
            incident_decision=incident_decision,
            execution_request=execution_request,
        )
        if snapshot is None:
            return None
        surface_type = _clean_text(snapshot.get("type"))
        adapter_id = _clean_text(snapshot.get("adapter_id"))
        connection_state = _clean_text(snapshot.get("connection_state")).lower()
        running = bool(snapshot.get("running"))
        target = adapter_id or surface_type or _clean_text(req.message)
        evidence = (
            dict(execution_request.evidence)
            if execution_request is not None
            else {}
        )
        evidence.update(self._surface_snapshot_evidence(snapshot))
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
        if connection_state == "disconnected" or not running:
            request_payload.evidence[result_key] = "disconnected"
            request_payload.evidence["post_action_status"] = "disconnected"
            action_result = IncidentActionResult(
                action_id=boundary.action_id,
                lane=boundary.lane,
                status="completed",
                summary=(
                    f"Adapter `{adapter_id or surface_type or target}` was already stopped, so the surface session is contained."
                ),
                evidence=[
                    f"adapter_id: {adapter_id}" if adapter_id else "",
                    f"adapter_type: {surface_type}" if surface_type else "",
                    "post_action_status: disconnected",
                ],
                artifacts={"adapter_id": adapter_id, "adapter_type": surface_type},
            )
            return finalize_incident_execution(
                request_payload,
                boundary=boundary,
                action_result=action_result,
                scenario_id=incident_decision.incident_scenario_id,
            )
        stopped = await self._stop_live_adapter(adapter_id, missing_ok=False) if adapter_id else False
        if not stopped:
            action_result = IncidentActionResult(
                action_id=boundary.action_id,
                lane=boundary.lane,
                status="blocked",
                summary=(
                    f"Attempted to {boundary.action_id} `{target}`, but the live adapter could not be stopped."
                ),
                evidence=[
                    f"adapter_id: {adapter_id}" if adapter_id else "",
                    f"adapter_type: {surface_type}" if surface_type else "",
                    f"connection_state: {connection_state}" if connection_state else "",
                ],
                blockers=["the adapter state changed before containment completed"],
            )
            return finalize_incident_execution(
                request_payload,
                boundary=boundary,
                action_result=action_result,
                scenario_id=incident_decision.incident_scenario_id,
            )
        request_payload.evidence[result_key] = "disconnected"
        request_payload.evidence["post_action_status"] = "disconnected"
        action_result = IncidentActionResult(
            action_id=boundary.action_id,
            lane=boundary.lane,
            status="completed",
            summary=(
                f"Stopped adapter `{adapter_id or surface_type}` to {boundary.action_id} the failed surface session."
            ),
            changed_state=True,
            evidence=[
                f"adapter_id: {adapter_id}" if adapter_id else "",
                f"adapter_type: {surface_type}" if surface_type else "",
                "post_action_status: disconnected",
            ],
            artifacts={"adapter_id": adapter_id, "adapter_type": surface_type},
        )
        return finalize_incident_execution(
            request_payload,
            boundary=boundary,
            action_result=action_result,
            scenario_id=incident_decision.incident_scenario_id,
        )

    async def _execute_live_surface_retry(
        self,
        *,
        req: Any,
        incident_decision: IncidentConversationTurnDecision,
        execution_request: IncidentExecutionRequest | None,
    ) -> IncidentExecutionReport | None:
        if incident_decision.incident_scenario_id != "failed_external_surface_session":
            return None
        if incident_decision.chosen_action != "retry":
            return None
        boundary = resolve_incident_action_boundary(
            incident_decision.chosen_action,
            preferred_lane=incident_decision.action_lane or "legacy",
        )
        snapshot = await self._select_failed_surface_snapshot(
            req=req,
            incident_decision=incident_decision,
            execution_request=execution_request,
        )
        if snapshot is None:
            return None
        surface_type = _clean_text(snapshot.get("type")).lower()
        adapter_id = _clean_text(snapshot.get("adapter_id"))
        connection_state = _clean_text(snapshot.get("connection_state")).lower()
        last_error = _clean_text(snapshot.get("last_error"))
        target = adapter_id or surface_type or _clean_text(req.message)
        evidence = (
            dict(execution_request.evidence)
            if execution_request is not None
            else {}
        )
        evidence.update(self._surface_snapshot_evidence(snapshot))
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
        if connection_state == "connected" and not last_error:
            action_result = IncidentActionResult(
                action_id=boundary.action_id,
                lane=boundary.lane,
                status="blocked",
                summary=(
                    f"Cannot retry `{target}` because the latest adapter state is healthy (`connected`)."
                ),
                evidence=[
                    f"adapter_id: {adapter_id}" if adapter_id else "",
                    f"adapter_type: {surface_type}" if surface_type else "",
                    "connection_state: connected",
                ],
                blockers=["choose investigate, contain, or escalate instead of retry"],
            )
            return finalize_incident_execution(
                request_payload,
                boundary=boundary,
                action_result=action_result,
                scenario_id=incident_decision.incident_scenario_id,
            )
        try:
            replay_record = await self._restart_live_adapter(
                surface_type=surface_type,
                adapter_id=adapter_id,
            )
        except Exception as exc:
            action_result = IncidentActionResult(
                action_id=boundary.action_id,
                lane=boundary.lane,
                status="blocked",
                summary=f"Retry for `{target}` could not start: {_clean_text(exc)}",
                evidence=[
                    f"adapter_id: {adapter_id}" if adapter_id else "",
                    f"adapter_type: {surface_type}" if surface_type else "",
                    f"connection_state: {connection_state}" if connection_state else "",
                ],
                blockers=[
                    "stored adapter config is missing or the adapter runtime could not restart",
                ],
            )
            return finalize_incident_execution(
                request_payload,
                boundary=boundary,
                action_result=action_result,
                scenario_id=incident_decision.incident_scenario_id,
            )
        retry_adapter_id = _clean_text(replay_record.get("adapter_id"))
        request_payload.evidence["retry_result"] = "pending"
        request_payload.evidence["post_action_status"] = "pending"
        if retry_adapter_id:
            request_payload.evidence["retry_adapter_id"] = retry_adapter_id
        action_result = IncidentActionResult(
            action_id=boundary.action_id,
            lane=boundary.lane,
            status="completed",
            summary=(
                f"Retried `{target}` by restarting `{retry_adapter_id or surface_type or 'the adapter surface'}`."
            ),
            changed_state=True,
            evidence=[
                f"adapter_id: {adapter_id}" if adapter_id else "",
                f"retry_adapter_id: {retry_adapter_id}" if retry_adapter_id else "",
                "post_action_status: pending",
            ],
            artifacts={
                "source_adapter_id": adapter_id,
                "retry_adapter_id": retry_adapter_id,
                "adapter_type": surface_type,
            },
        )
        return finalize_incident_execution(
            request_payload,
            boundary=boundary,
            action_result=action_result,
            scenario_id=incident_decision.incident_scenario_id,
        )

    async def _build_incident_execution_request(
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
        workflow_request = self._build_failed_workflow_execution_request(
            req=req,
            incident_decision=incident_decision,
        )
        if workflow_request is not None:
            return workflow_request
        return await self._build_failed_surface_execution_request(
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
                approval_mode=self._approval_mode(req),
                enabled_tools=self._enabled_tools(req),
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
                enabled_tools=self._enabled_tools(req),
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
                what_changed=["The DAN-v2 controller answered directly without delegation."],
                confidence=0.9,
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
                what_changed=["The DAN-v2 controller stopped for one concrete clarification."],
                confidence=0.3,
                best_next_question=_clean_text(turn_decision.clarifying_question),
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
                what_changed=["The DAN-v2 controller could not produce a valid delegation brief."],
                confidence=0.1,
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

        operator_boundary = self._operator_execution_boundary(req)
        worker_report = WorkerReport(
            lane="legacy",
            status="handoff",
            summary=turn_decision.public_response or "Routing this through the general operator lane.",
            objective=brief.desired_delta,
            acceptance_criteria=list(brief.success_criteria),
            what_changed=["Selected the general operator lane for this turn."],
            evidence=_normalize_texts(brief.why_now, turn_decision.public_response),
            artifacts=_compact_artifacts(
                selected_lane="legacy",
                **self._operator_contract_payload(req),
            ),
            confidence=0.65,
        )
        review = ReviewDecision(
            action="continue",
            public_response=turn_decision.public_response or "Routing this through the general operator lane.",
            reason="The request needs the shared legacy execution substrate.",
            next_lane="legacy",
            next_delta=brief.desired_delta,
        )
        return DANV2TurnOutcome(
            turn_decision=turn_decision,
            supervisor_brief=brief,
            worker_report=worker_report,
            review_decision=review,
            direct_response=None,
            handoff_prompt_context=_merge_prompt_context(
                brief.render_prompt_context(),
                "Operator lane boundary:",
                f"- Execution target: {operator_boundary.execution_target}",
                "- Deterministic capability sets: "
                + "; ".join(operator_boundary.deterministic_capability_sets)
                if operator_boundary.deterministic_capability_sets
                else "",
                "- Deterministic adapters: "
                + "; ".join(operator_boundary.deterministic_adapters)
                if operator_boundary.deterministic_adapters
                else "",
                f"- Shared control membrane: {operator_boundary.shared_control_membrane}",
                "- Non-goals: " + "; ".join(operator_boundary.non_goals),
            ),
            handoff_metadata={
                "selected_lane": "legacy",
                **self._operator_contract_payload(req),
            },
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
                what_changed=["DAN Code answered directly without launching execution."],
                confidence=0.75,
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
                what_changed=["DAN Code stopped for one concrete clarification before execution."],
                confidence=0.3,
                best_next_question=_clean_text(code_decision.clarifying_question),
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
                        what_changed=_normalize_texts(
                            report.get("change_summary")
                            or "DAN Code completed one bounded pass inside the DAN-v2 runtime."
                        ),
                        evidence=_normalize_texts(
                            report.get("target_files"),
                            report.get("test_plan"),
                            report.get("risks"),
                        ),
                        artifacts=_compact_artifacts(
                            trace_id=report.get("trace_id"),
                            candidate_id=report.get("candidate_id"),
                            target_files=report.get("target_files"),
                            test_plan=report.get("test_plan"),
                        ),
                        blockers=blockers,
                        confidence=(
                            0.85
                            if str(report.get("status") or "").strip().lower() == "completed"
                            else 0.45
                        ),
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
            what_changed=["Prepared one bounded coding objective for downstream execution."],
            evidence=_normalize_texts(code_decision.research_findings, brief.why_now),
            artifacts=_compact_artifacts(
                selected_lane="code",
                repair_brief=code_decision.repair_brief,
            ),
            confidence=0.7,
        )
        review = ReviewDecision(
            action="continue",
            public_response=public_response,
            reason="DAN Code shaped the task and the shared execution substrate should run it next.",
            next_lane="code",
            next_delta=objective or str(req.message or "").strip(),
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
                what_changed=["DAN Research answered directly without launching a bounded run."],
                confidence=0.75,
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
                what_changed=["DAN Research stopped for one concrete clarification before execution."],
                confidence=0.3,
                best_next_question=_clean_text(research_decision.clarifying_question),
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
            what_changed=["Prepared one bounded research objective for downstream execution."],
            evidence=_normalize_texts(research_decision.delivery_target, brief.why_now),
            artifacts=_compact_artifacts(
                selected_lane="research",
                delivery_target=research_decision.delivery_target,
            ),
            confidence=0.72,
        )
        review = ReviewDecision(
            action="continue",
            public_response=public_response,
            reason="DAN Research shaped the task and the shared execution substrate should run it next.",
            next_lane="research",
            next_delta=objective or str(req.message or "").strip(),
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
                what_changed=["Incident Commander stopped with an explicit terminal state."],
                evidence=_normalize_texts(
                    incident_decision.terminal_state,
                    incident_decision.chosen_action,
                ),
                confidence=0.75,
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
                what_changed=["Incident Commander stopped for one concrete clarification."],
                confidence=0.3,
                best_next_question=_clean_text(incident_decision.clarifying_question),
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
                what_changed=["Incident Commander could not choose a valid downstream action brief."],
                confidence=0.1,
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

        execution_request = await self._build_incident_execution_request(
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
        if execution_report is None:
            execution_report = await self._execute_live_surface_retry(
                req=req,
                incident_decision=incident_decision,
                execution_request=execution_request,
            )
            if execution_report is not None:
                execution_mode = "live"
        if execution_report is None:
            execution_report = await self._execute_live_surface_incident_action(
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
            response = self._build_incident_response(
                public_response=incident_decision.public_response,
                execution_report=execution_report,
                execution_mode=execution_mode,
            )
            retry_run_id = str(
                execution_report.action_result.artifacts.get("retry_run_id") or ""
                if isinstance(execution_report.action_result.artifacts, dict)
                else ""
            ).strip()
            retry_adapter_id = str(
                execution_report.action_result.artifacts.get("retry_adapter_id") or ""
                if isinstance(execution_report.action_result.artifacts, dict)
                else ""
            ).strip()
            worker_report = WorkerReport(
                lane="incident",
                status="responded",
                summary=response,
                objective=execution_report.objective,
                acceptance_criteria=list(incident_brief.success_criteria),
                what_changed=[
                    "Launched a live retry and left the incident open until the new run settles."
                ],
                evidence=_normalize_texts(
                    execution_report.public_summary,
                    execution_report.verification_result.required_follow_up,
                ),
                artifacts=_compact_artifacts(
                    getattr(execution_report.action_result, "artifacts", None),
                    retry_run_id=retry_run_id,
                ),
                blockers=list(execution_report.verification_result.required_follow_up),
                confidence=0.78,
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
            if retry_adapter_id:
                handoff_metadata["retry_adapter_id"] = retry_adapter_id
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
            response = self._build_incident_response(
                public_response=incident_decision.public_response,
                execution_report=execution_report,
                execution_mode=execution_mode,
            )
            worker_report = WorkerReport(
                lane="incident",
                status="responded",
                summary=response,
                objective=execution_report.objective,
                acceptance_criteria=list(incident_brief.success_criteria),
                what_changed=_normalize_texts(
                    execution_report.public_summary
                    or f"Incident Commander ended with terminal_state={execution_report.terminal_state}."
                ),
                evidence=_normalize_texts(
                    execution_report.verification_result.required_follow_up,
                    execution_report.action_result.evidence,
                ),
                artifacts=_compact_artifacts(
                    getattr(execution_report.action_result, "artifacts", None)
                ),
                blockers=list(execution_report.verification_result.required_follow_up),
                confidence=(
                    0.82
                    if execution_report.terminal_state in {"resolved", "contained"}
                    else 0.55
                ),
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
            what_changed=["Incident Commander selected the next bounded remediation lane."],
            evidence=_normalize_texts(
                incident_decision.incident_scenario_id,
                incident_decision.chosen_action,
                incident_decision.verification_checks,
                execution_report.public_summary if execution_report is not None else "",
            ),
            artifacts=_compact_artifacts(
                selected_lane="incident",
                incident_action_lane=incident_brief.lane,
                incident_execution_mode=execution_mode,
            ),
            confidence=0.7,
        )
        review = ReviewDecision(
            action="continue",
            public_response=public_response,
            reason="Incident Commander chose the next bounded remediation lane.",
            next_lane=incident_brief.lane,
            next_delta=incident_brief.desired_delta or str(req.message or "").strip(),
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
    "parse_control_plane_mode",
    "resolve_control_plane_mode",
]
