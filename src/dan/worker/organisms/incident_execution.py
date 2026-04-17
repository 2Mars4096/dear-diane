"""Incident Commander scenario matrix and deterministic execution boundary."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

IncidentTerminalState = Literal[
    "open",
    "resolved",
    "contained",
    "blocked",
    "escalated",
    "needs_approval",
]


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalized_key(value: Any) -> str:
    return _clean_text(value).lower().replace("-", "_").replace(" ", "_")


def _evidence_text(evidence: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = evidence.get(key)
        text = _clean_text(value)
        if text:
            return text
    return ""


def _evidence_lines(evidence: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key, value in sorted(evidence.items()):
        if isinstance(value, (dict, list, tuple, set)):
            text = str(value)
        else:
            text = _clean_text(value)
        if text:
            lines.append(f"{key}: {text}")
    return lines


def _looks_success(value: str) -> bool:
    normalized = _normalized_key(value)
    return normalized in {
        "ok",
        "healthy",
        "succeeded",
        "success",
        "resolved",
        "completed",
        "delivered",
        "restored",
        "passed",
    }


def _looks_contained(value: str) -> bool:
    normalized = _normalized_key(value)
    return normalized in {
        "contained",
        "paused",
        "stopped",
        "disabled",
        "isolated",
        "limited",
    }


def _looks_active_failure(value: str) -> bool:
    normalized = _normalized_key(value)
    return normalized in {
        "failed",
        "failure",
        "degraded",
        "stuck",
        "stalled",
        "stale",
        "broken",
        "timeout",
        "timed_out",
    }


def _looks_in_progress(value: str) -> bool:
    normalized = _normalized_key(value)
    return normalized in {
        "pending",
        "queued",
        "running",
        "started",
        "retrying",
        "restarted",
        "in_progress",
    }


class IncidentBenchmarkScenario(BaseModel):
    scenario_id: str
    title: str
    description: str
    user_cues: list[str] = Field(default_factory=list)
    default_action_lane: Literal["code", "legacy"]
    typical_actions: list[str] = Field(default_factory=list)
    terminal_states: list[IncidentTerminalState] = Field(default_factory=list)


class IncidentActionBoundary(BaseModel):
    action_id: str
    title: str
    description: str
    lane: Literal["", "code", "legacy"] = ""
    requires_approval: bool = False


IncidentActionStatus = Literal[
    "completed",
    "blocked",
    "needs_approval",
    "delegated",
]


class IncidentExecutionRequest(BaseModel):
    scenario_id: str = ""
    action_id: str = "investigate"
    target: str = ""
    objective: str = ""
    preferred_lane: Literal["", "code", "legacy"] = ""
    approval_granted: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)
    verification_checks: list[str] = Field(default_factory=list)


class IncidentActionResult(BaseModel):
    action_id: str
    lane: Literal["", "code", "legacy"] = ""
    status: IncidentActionStatus = "completed"
    summary: str = ""
    changed_state: bool = False
    evidence: list[str] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    next_lane: Literal["", "code", "legacy"] = ""


class IncidentVerificationResult(BaseModel):
    terminal_state: IncidentTerminalState
    verified: bool = False
    summary: str = ""
    evidence: list[str] = Field(default_factory=list)
    required_follow_up: list[str] = Field(default_factory=list)


class IncidentPhaseRecord(BaseModel):
    phase: Literal["investigate", "action_gate", "act", "verify", "close"]
    summary: str


class IncidentExecutionReport(BaseModel):
    scenario_id: str = ""
    action_id: str
    target: str = ""
    objective: str = ""
    terminal_state: IncidentTerminalState
    action_result: IncidentActionResult
    verification_result: IncidentVerificationResult
    phase_trace: list[IncidentPhaseRecord] = Field(default_factory=list)
    public_summary: str = ""


IncidentActionAdapter = Callable[
    [IncidentExecutionRequest, IncidentActionBoundary],
    IncidentActionResult,
]


FROZEN_INCIDENT_SCENARIOS: tuple[IncidentBenchmarkScenario, ...] = (
    IncidentBenchmarkScenario(
        scenario_id="failed_scheduled_workflow",
        title="Failed scheduled workflow",
        description=(
            "A scheduled run failed, stalled, or left stale workflow state that needs "
            "bounded investigation and operator remediation."
        ),
        user_cues=[
            "scheduled workflow",
            "cron failed",
            "nightly run failed",
            "job failed",
            "run failed",
            "workflow failed",
            "stale run state",
            "stuck run state",
        ],
        default_action_lane="legacy",
        typical_actions=["investigate", "retry", "contain", "pause"],
        terminal_states=["resolved", "contained", "blocked", "escalated", "needs_approval"],
    ),
    IncidentBenchmarkScenario(
        scenario_id="broken_coding_run_or_stale_run_state",
        title="Broken coding run or stale repo state",
        description=(
            "A coding run, repo state, or validation flow failed in a way that may require "
            "bounded repair work or a safe stop."
        ),
        user_cues=[
            "coding run failed",
            "fix run",
            "stale repo state",
            "broken branch",
            "failed test run",
            "ci failed",
            "broken build",
            "repair the run",
        ],
        default_action_lane="code",
        typical_actions=["investigate", "repair", "retry", "escalate"],
        terminal_states=["resolved", "contained", "blocked", "escalated", "needs_approval"],
    ),
    IncidentBenchmarkScenario(
        scenario_id="failed_external_surface_session",
        title="Failed browser, download, or adapter session",
        description=(
            "A browser/download flow or an external adapter surface is stuck, degraded, "
            "or failed to deliver and needs bounded operational handling."
        ),
        user_cues=[
            "browser stuck",
            "download stuck",
            "session stuck",
            "adapter failed",
            "delivery failed",
            "wechat failed",
            "telegram failed",
            "email failed",
            "desktop session stuck",
        ],
        default_action_lane="legacy",
        typical_actions=["investigate", "retry", "contain", "pause", "escalate"],
        terminal_states=["resolved", "contained", "blocked", "escalated", "needs_approval"],
    ),
)


INCIDENT_ACTION_BOUNDARIES: tuple[IncidentActionBoundary, ...] = (
    IncidentActionBoundary(
        action_id="investigate",
        title="Investigate",
        description="Gather the minimum evidence needed to choose the next bounded step.",
        lane="legacy",
    ),
    IncidentActionBoundary(
        action_id="retry",
        title="Retry",
        description="Re-run or refresh the failed operational step when it is safe to do so.",
        lane="legacy",
    ),
    IncidentActionBoundary(
        action_id="contain",
        title="Contain",
        description="Pause or limit the incident blast radius without broad changes.",
        lane="legacy",
    ),
    IncidentActionBoundary(
        action_id="pause",
        title="Pause",
        description="Stop the unstable path and preserve operator control while investigation continues.",
        lane="legacy",
    ),
    IncidentActionBoundary(
        action_id="repair",
        title="Repair",
        description="Delegate a bounded code or config repair when the incident root cause is a concrete defect.",
        lane="code",
    ),
    IncidentActionBoundary(
        action_id="rollback",
        title="Rollback",
        description="Revert or restore a previous known-good state.",
        lane="legacy",
        requires_approval=True,
    ),
    IncidentActionBoundary(
        action_id="request_approval",
        title="Request approval",
        description="Stop and request explicit operator approval before proceeding.",
        lane="",
        requires_approval=True,
    ),
    IncidentActionBoundary(
        action_id="escalate",
        title="Escalate",
        description="Hand off to a human owner because safe autonomous progress is no longer justified.",
        lane="",
    ),
)


def classify_incident_scenario(message: str) -> IncidentBenchmarkScenario | None:
    lowered = _clean_text(message).lower()
    if not lowered:
        return None
    best_match: IncidentBenchmarkScenario | None = None
    best_score = 0
    for scenario in FROZEN_INCIDENT_SCENARIOS:
        score = sum(1 for cue in scenario.user_cues if cue in lowered)
        if score > best_score:
            best_score = score
            best_match = scenario
    return best_match if best_score > 0 else None


def resolve_incident_action_boundary(
    action_id: str,
    *,
    preferred_lane: str = "",
) -> IncidentActionBoundary:
    normalized_action = _clean_text(action_id).lower().replace(" ", "_").replace("-", "_")
    normalized_lane = _clean_text(preferred_lane).lower()
    for boundary in INCIDENT_ACTION_BOUNDARIES:
        if boundary.action_id == normalized_action:
            if normalized_lane in {"code", "legacy"} and boundary.lane in {"", normalized_lane}:
                return boundary.model_copy(update={"lane": normalized_lane})
            return boundary
    if normalized_lane in {"code", "legacy"}:
        return IncidentActionBoundary(
            action_id=normalized_action or "investigate",
            title="Custom incident action",
            description="A bounded incident action that still needs an explicit execution lane.",
            lane=normalized_lane,
        )
    return IncidentActionBoundary(
        action_id=normalized_action or "investigate",
        title="Custom incident action",
        description="A bounded incident action that does not map cleanly to an execution lane yet.",
        lane="",
    )


def resolve_incident_scenario(
    scenario_id: str,
    *,
    message: str = "",
) -> IncidentBenchmarkScenario | None:
    normalized = _normalized_key(scenario_id)
    for scenario in FROZEN_INCIDENT_SCENARIOS:
        if scenario.scenario_id == normalized:
            return scenario
    return classify_incident_scenario(message)


def _investigate_adapter(
    request: IncidentExecutionRequest,
    boundary: IncidentActionBoundary,
) -> IncidentActionResult:
    evidence = _evidence_lines(request.evidence)
    target = _clean_text(request.target) or "the reported incident target"
    if not evidence:
        return IncidentActionResult(
            action_id=boundary.action_id,
            lane=boundary.lane,
            status="blocked",
            summary=f"Investigation could not close `{target}` because no current state evidence was supplied.",
            blockers=["current incident state evidence is missing"],
        )
    return IncidentActionResult(
        action_id=boundary.action_id,
        lane=boundary.lane,
        status="completed",
        summary=f"Collected current incident evidence for `{target}`.",
        evidence=evidence,
    )


def _retry_adapter(
    request: IncidentExecutionRequest,
    boundary: IncidentActionBoundary,
) -> IncidentActionResult:
    target = _clean_text(request.target) or "the failed operation"
    retry_result = _evidence_text(
        request.evidence,
        "retry_result",
        "post_action_status",
        "after_status",
        "latest_status",
    )
    if not retry_result:
        return IncidentActionResult(
            action_id=boundary.action_id,
            lane=boundary.lane,
            status="blocked",
            summary=f"Retry for `{target}` needs a post-action status before closure.",
            blockers=["retry_result or post_action_status is missing"],
        )
    return IncidentActionResult(
        action_id=boundary.action_id,
        lane=boundary.lane,
        status="completed",
        summary=f"Retried `{target}` and observed status `{retry_result}`.",
        changed_state=True,
        evidence=[f"retry_result: {retry_result}"],
    )


def _contain_adapter(
    request: IncidentExecutionRequest,
    boundary: IncidentActionBoundary,
) -> IncidentActionResult:
    target = _clean_text(request.target) or "the incident target"
    containment = _evidence_text(
        request.evidence,
        "containment_result",
        "post_action_status",
        "after_status",
    ) or "contained"
    return IncidentActionResult(
        action_id=boundary.action_id,
        lane=boundary.lane,
        status="completed",
        summary=f"Applied containment to `{target}` with status `{containment}`.",
        changed_state=True,
        evidence=[f"containment_result: {containment}"],
    )


def _pause_adapter(
    request: IncidentExecutionRequest,
    boundary: IncidentActionBoundary,
) -> IncidentActionResult:
    target = _clean_text(request.target) or "the unstable path"
    pause_result = _evidence_text(
        request.evidence,
        "pause_result",
        "post_action_status",
        "after_status",
    ) or "paused"
    return IncidentActionResult(
        action_id=boundary.action_id,
        lane=boundary.lane,
        status="completed",
        summary=f"Paused `{target}` with status `{pause_result}`.",
        changed_state=True,
        evidence=[f"pause_result: {pause_result}"],
    )


def _rollback_adapter(
    request: IncidentExecutionRequest,
    boundary: IncidentActionBoundary,
) -> IncidentActionResult:
    target = _clean_text(request.target) or "the incident target"
    rollback_result = _evidence_text(
        request.evidence,
        "rollback_result",
        "post_action_status",
        "after_status",
    )
    if not rollback_result:
        return IncidentActionResult(
            action_id=boundary.action_id,
            lane=boundary.lane,
            status="blocked",
            summary=f"Rollback for `{target}` needs a post-action status before closure.",
            blockers=["rollback_result or post_action_status is missing"],
        )
    return IncidentActionResult(
        action_id=boundary.action_id,
        lane=boundary.lane,
        status="completed",
        summary=f"Rolled back `{target}` and observed status `{rollback_result}`.",
        changed_state=True,
        evidence=[f"rollback_result: {rollback_result}"],
    )


def _repair_adapter(
    request: IncidentExecutionRequest,
    boundary: IncidentActionBoundary,
) -> IncidentActionResult:
    target = _clean_text(request.target) or "the failing code/config target"
    objective = _clean_text(request.objective) or f"Repair `{target}`."
    return IncidentActionResult(
        action_id=boundary.action_id,
        lane="code",
        status="delegated",
        summary=f"Repair for `{target}` should be delegated to the code lane.",
        evidence=[f"repair_objective: {objective}"],
        next_lane="code",
    )


def _escalate_adapter(
    request: IncidentExecutionRequest,
    boundary: IncidentActionBoundary,
) -> IncidentActionResult:
    target = _clean_text(request.target) or "the incident target"
    return IncidentActionResult(
        action_id=boundary.action_id,
        lane=boundary.lane,
        status="completed",
        summary=f"Escalated `{target}` to a human owner.",
        evidence=["human escalation requested"],
    )


def _approval_adapter(
    request: IncidentExecutionRequest,
    boundary: IncidentActionBoundary,
) -> IncidentActionResult:
    _ = request
    return IncidentActionResult(
        action_id=boundary.action_id,
        lane=boundary.lane,
        status="needs_approval",
        summary="Explicit operator approval is required before this incident action can continue.",
        blockers=["operator approval required"],
    )


DEFAULT_INCIDENT_ACTION_ADAPTERS: dict[str, IncidentActionAdapter] = {
    "investigate": _investigate_adapter,
    "retry": _retry_adapter,
    "contain": _contain_adapter,
    "pause": _pause_adapter,
    "rollback": _rollback_adapter,
    "repair": _repair_adapter,
    "escalate": _escalate_adapter,
    "request_approval": _approval_adapter,
}


class IncidentActionRegistry:
    """Deterministic action adapter registry for bounded incident steps."""

    def __init__(
        self,
        adapters: dict[str, IncidentActionAdapter] | None = None,
    ) -> None:
        self._adapters = dict(DEFAULT_INCIDENT_ACTION_ADAPTERS)
        if adapters:
            self._adapters.update(adapters)

    def register(self, action_id: str, adapter: IncidentActionAdapter) -> None:
        normalized = _normalized_key(action_id)
        if not normalized:
            raise ValueError("action_id is required")
        self._adapters[normalized] = adapter

    def execute(
        self,
        request: IncidentExecutionRequest,
        boundary: IncidentActionBoundary,
    ) -> IncidentActionResult:
        if boundary.requires_approval and not request.approval_granted:
            return IncidentActionResult(
                action_id=boundary.action_id,
                lane=boundary.lane,
                status="needs_approval",
                summary=(
                    f"`{boundary.title}` requires explicit operator approval "
                    "before Incident Commander can continue."
                ),
                blockers=["operator approval required"],
            )
        adapter = self._adapters.get(boundary.action_id)
        if adapter is None:
            return IncidentActionResult(
                action_id=boundary.action_id,
                lane=boundary.lane,
                status="blocked",
                summary=f"No deterministic adapter is registered for `{boundary.action_id}`.",
                blockers=[f"missing adapter: {boundary.action_id}"],
            )
        return adapter(request, boundary)


def verify_incident_action(
    request: IncidentExecutionRequest,
    boundary: IncidentActionBoundary,
    action_result: IncidentActionResult,
) -> IncidentVerificationResult:
    if action_result.status == "needs_approval":
        return IncidentVerificationResult(
            terminal_state="needs_approval",
            verified=True,
            summary="Stopped at the explicit approval boundary.",
            evidence=list(action_result.evidence),
            required_follow_up=list(action_result.blockers),
        )
    if action_result.status == "delegated":
        return IncidentVerificationResult(
            terminal_state="open",
            verified=False,
            summary=f"Delegated the incident action to `{action_result.next_lane or action_result.lane}`.",
            evidence=list(action_result.evidence),
            required_follow_up=["wait for delegated worker report before closing the incident"],
        )
    if action_result.status == "blocked":
        return IncidentVerificationResult(
            terminal_state="blocked",
            verified=False,
            summary=action_result.summary or "The incident action is blocked.",
            evidence=list(action_result.evidence),
            required_follow_up=list(action_result.blockers),
        )

    status_text = _evidence_text(
        request.evidence,
        "post_action_status",
        "after_status",
        "retry_result",
        "rollback_result",
        "containment_result",
        "pause_result",
        "latest_status",
        "current_status",
    )
    if boundary.action_id == "escalate":
        return IncidentVerificationResult(
            terminal_state="escalated",
            verified=True,
            summary="Incident has been escalated to a human owner.",
            evidence=list(action_result.evidence),
        )
    if boundary.action_id == "retry" and status_text and _looks_in_progress(status_text):
        return IncidentVerificationResult(
            terminal_state="open",
            verified=False,
            summary=f"Retry launched and current status is `{status_text}`.",
            evidence=list(action_result.evidence),
            required_follow_up=["wait for the retried run to settle"],
        )
    if boundary.action_id in {"contain", "pause"}:
        return IncidentVerificationResult(
            terminal_state="contained",
            verified=True,
            summary="Incident blast radius is contained.",
            evidence=list(action_result.evidence),
        )
    if status_text and _looks_success(status_text):
        return IncidentVerificationResult(
            terminal_state="resolved",
            verified=True,
            summary=f"Verification observed a healthy terminal status: `{status_text}`.",
            evidence=list(action_result.evidence),
        )
    if status_text and _looks_contained(status_text):
        return IncidentVerificationResult(
            terminal_state="contained",
            verified=True,
            summary=f"Verification observed a contained terminal status: `{status_text}`.",
            evidence=list(action_result.evidence),
        )
    if status_text and _looks_active_failure(status_text):
        return IncidentVerificationResult(
            terminal_state="blocked",
            verified=False,
            summary=f"Verification still sees active failure status: `{status_text}`.",
            evidence=list(action_result.evidence),
            required_follow_up=["choose another bounded action or escalate"],
        )
    return IncidentVerificationResult(
        terminal_state="blocked",
        verified=False,
        summary="Verification did not receive enough status evidence to close the incident.",
        evidence=list(action_result.evidence),
        required_follow_up=[
            "provide post-action status evidence",
            *list(request.verification_checks),
        ],
    )


def finalize_incident_execution(
    request: IncidentExecutionRequest,
    *,
    boundary: IncidentActionBoundary,
    action_result: IncidentActionResult,
    scenario_id: str = "",
) -> IncidentExecutionReport:
    resolved_scenario = resolve_incident_scenario(
        scenario_id or request.scenario_id,
        message=" ".join(
            part for part in [request.objective, request.target] if _clean_text(part)
        ),
    )
    resolved_scenario_id = (
        resolved_scenario.scenario_id
        if resolved_scenario is not None
        else _normalized_key(scenario_id or request.scenario_id)
    )
    target = _clean_text(request.target)
    objective = _clean_text(request.objective) or boundary.description
    phase_trace = [
        IncidentPhaseRecord(
            phase="investigate",
            summary=(
                f"Scenario `{resolved_scenario_id or 'unclassified'}` selected for "
                f"`{target or 'unspecified target'}`."
            ),
        ),
        IncidentPhaseRecord(
            phase="action_gate",
            summary=(
                f"Action `{boundary.action_id}` uses lane `{boundary.lane or 'none'}` "
                f"and approval_required={boundary.requires_approval}."
            ),
        ),
    ]
    phase_trace.append(
        IncidentPhaseRecord(
            phase="act",
            summary=action_result.summary,
        )
    )
    verification = verify_incident_action(request, boundary, action_result)
    phase_trace.append(
        IncidentPhaseRecord(
            phase="verify",
            summary=verification.summary,
        )
    )
    public_summary = (
        f"Incident `{resolved_scenario_id or 'unclassified'}` action `{boundary.action_id}` "
        f"ended as `{verification.terminal_state}`."
    )
    if verification.required_follow_up:
        public_summary += " Follow-up: " + "; ".join(verification.required_follow_up)
    phase_trace.append(
        IncidentPhaseRecord(
            phase="close",
            summary=public_summary,
        )
    )
    return IncidentExecutionReport(
        scenario_id=resolved_scenario_id,
        action_id=boundary.action_id,
        target=target,
        objective=objective,
        terminal_state=verification.terminal_state,
        action_result=action_result,
        verification_result=verification,
        phase_trace=phase_trace,
        public_summary=public_summary,
    )


def execute_incident_action(
    request: IncidentExecutionRequest,
    *,
    registry: IncidentActionRegistry | None = None,
) -> IncidentExecutionReport:
    scenario = resolve_incident_scenario(
        request.scenario_id,
        message=" ".join(
            part for part in [request.objective, request.target] if _clean_text(part)
        ),
    )
    scenario_id = scenario.scenario_id if scenario is not None else _normalized_key(request.scenario_id)
    boundary = resolve_incident_action_boundary(
        request.action_id,
        preferred_lane=request.preferred_lane,
    )
    action_result = (registry or IncidentActionRegistry()).execute(request, boundary)
    return finalize_incident_execution(
        request,
        boundary=boundary,
        action_result=action_result,
        scenario_id=scenario_id,
    )


__all__ = [
    "DEFAULT_INCIDENT_ACTION_ADAPTERS",
    "FROZEN_INCIDENT_SCENARIOS",
    "INCIDENT_ACTION_BOUNDARIES",
    "IncidentActionBoundary",
    "IncidentActionRegistry",
    "IncidentActionResult",
    "IncidentActionStatus",
    "IncidentExecutionReport",
    "IncidentExecutionRequest",
    "IncidentPhaseRecord",
    "IncidentBenchmarkScenario",
    "IncidentTerminalState",
    "IncidentVerificationResult",
    "classify_incident_scenario",
    "execute_incident_action",
    "finalize_incident_execution",
    "resolve_incident_action_boundary",
    "resolve_incident_scenario",
    "verify_incident_action",
]
