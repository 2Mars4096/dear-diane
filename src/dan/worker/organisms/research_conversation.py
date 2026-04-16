"""Durable conversation controller for the DAN Research orchestrator."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.providers import LLMProvider
from dan.worker.core.contracts import (
    AcquisitionPolicy,
    AcquisitionRequest,
    ExecutionRequest,
    OutputContract,
)
from dan.worker.organisms.coding_conversation import ProviderCompletionAdapter
from dan.worker.organisms.project_execution import build_research_orchestrator_worker
from dan.worker.runner import DurableAgentRunner, DurableAgentSessionState
from dan.worker.structured_payload import parse_jsonish_payload

DEFAULT_RESEARCH_CONTROL_HEDGE_MAX_ATTEMPTS = 2
DEFAULT_RESEARCH_CONTROL_HEDGE_DELAY_SECONDS = 2.0
MIN_RESEARCH_REPORT_CONFIDENCE = 0.55
MIN_RESEARCH_CONFIDENCE_WITH_CONTRADICTIONS = 0.75
REQUIRED_RESEARCH_QUALITY_GATES = frozenset(
    {
        "time_anchor",
        "scope_boundary",
        "source_authority",
        "numeric_reconciliation",
        "claim_object_fit",
        "final_status",
    }
)
CRITICAL_RESEARCH_QUALITY_GATES = frozenset(
    {"time_anchor", "scope_boundary", "source_authority"}
)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


_DECISION_KEYS = frozenset(
    {
        "action",
        "public_response",
        "clarifying_question",
        "research_objective",
        "next_objective",
    }
)


def _dedupe(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _positive_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _normalized_report_readiness(value: Any) -> str:
    text = _clean_text(value).lower().replace("-", "_").replace(" ", "_")
    if text in {"blocked", "not_ready", "notready"}:
        return "blocked"
    if text in {"provisional", "draft", "exploratory", "scout"}:
        return "provisional"
    if text in {"actionable", "decision_ready", "decisionready", "recommendation_ready"}:
        return "actionable"
    return "grounded"


def _normalized_quality_gate_name(value: Any) -> str:
    text = _clean_text(value).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "time": "time_anchor",
        "date": "time_anchor",
        "freshness": "time_anchor",
        "scope": "scope_boundary",
        "scope_fit": "scope_boundary",
        "boundary": "scope_boundary",
        "source": "source_authority",
        "authority": "source_authority",
        "primary_source": "source_authority",
        "numeric": "numeric_reconciliation",
        "number": "numeric_reconciliation",
        "reconciliation": "numeric_reconciliation",
        "claim_fit": "claim_object_fit",
        "object_fit": "claim_object_fit",
        "instrument_fit": "claim_object_fit",
        "thesis_fit": "claim_object_fit",
        "readiness": "final_status",
        "final": "final_status",
        "status": "final_status",
    }
    return aliases.get(text, text)


def _normalized_quality_gate_status(value: Any) -> str:
    text = _clean_text(value).lower().replace("-", "_").replace(" ", "_")
    if text in {"pass", "passed", "ok", "met", "clear", "satisfied"}:
        return "pass"
    if text in {"fail", "failed", "block", "blocked", "missing", "critical"}:
        return "fail"
    if text in {"na", "n_a", "not_applicable", "notapplicable", "irrelevant"}:
        return "not_applicable"
    return "warn"


def _resolve_control_hedge_max_attempts(value: int | None) -> int:
    if value is not None:
        return _positive_int(
            value,
            default=DEFAULT_RESEARCH_CONTROL_HEDGE_MAX_ATTEMPTS,
        )
    return _positive_int(
        os.environ.get("DAN_RESEARCH_CONTROL_HEDGE_MAX_ATTEMPTS"),
        default=DEFAULT_RESEARCH_CONTROL_HEDGE_MAX_ATTEMPTS,
    )


def _resolve_control_hedge_delay_seconds(value: float | None) -> float:
    if value is not None:
        return _positive_float(
            value,
            default=DEFAULT_RESEARCH_CONTROL_HEDGE_DELAY_SECONDS,
        )
    return _positive_float(
        os.environ.get("DAN_RESEARCH_CONTROL_HEDGE_DELAY_SECONDS"),
        default=DEFAULT_RESEARCH_CONTROL_HEDGE_DELAY_SECONDS,
    )


def _embedded_decision_payload(value: Any) -> dict[str, Any] | None:
    parsed = parse_jsonish_payload(value)
    if isinstance(parsed, dict) and any(key in parsed for key in _DECISION_KEYS):
        return dict(parsed)
    return None


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        payload = dict(raw)
        for key in ("result", "text", "public_response", "message", "content"):
            nested_payload = _embedded_decision_payload(payload.get(key))
            if nested_payload is not None and nested_payload != payload:
                return _parse_payload(nested_payload)
        if any(key in payload for key in _DECISION_KEYS):
            return payload
        nested = payload.get("result", payload.get("text"))
        if nested is not None and nested is not raw:
            return _parse_payload(nested)
        return payload
    parsed = parse_jsonish_payload(raw)
    if isinstance(parsed, dict):
        return dict(parsed)
    text = str(parsed or "").strip()
    if not text:
        return {}
    return {"public_response": text}


def _report_has_material_output(report_summary: "ResearchConversationReportSummary") -> bool:
    return bool(
        report_summary.findings
        or report_summary.evidence_refs
        or report_summary.verification_facts
        or report_summary.audit_issues
        or report_summary.quality_gates
        or _clean_text(report_summary.recommended_change)
    )


def _report_is_failed_no_output(
    report_summary: "ResearchConversationReportSummary",
) -> bool:
    return (
        _clean_text(report_summary.status).lower() != "completed"
        and not _report_has_material_output(report_summary)
    )


def _looks_like_user_question(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return False
    return "?" in cleaned


def _looks_like_continuation_request(text: str) -> bool:
    cleaned = _clean_text(text).lower()
    if not cleaned:
        return False
    cues = (
        "continue",
        "retry",
        "go deeper",
        "dig deeper",
        "another pass",
        "resume",
        "research it",
        "investigate it",
        "look into it",
        "verify it",
        "check it",
        "compare it",
    )
    return any(cue in cleaned for cue in cues)


def _latest_resumable_report_summary(
    context: "ResearchConversationContext",
) -> "ResearchConversationReportSummary | None":
    for report in reversed(context.recent_reports):
        if _report_is_failed_no_output(report):
            continue
        if _clean_text(report.objective):
            return report
    return None


def _review_requires_more_work(
    objective: str,
    report_summary: "ResearchConversationReportSummary",
) -> bool:
    _ = objective
    if _clean_text(report_summary.error):
        return True
    if _clean_text(report_summary.status).lower() != "completed":
        return True
    if not _report_has_material_output(report_summary):
        return True
    if report_summary.confidence is None:
        return True
    if float(report_summary.confidence) < MIN_RESEARCH_REPORT_CONFIDENCE:
        return True
    if not report_summary.evidence_refs:
        return True
    if _missing_quality_gates(report_summary):
        return True
    if _has_failed_quality_gate(report_summary):
        return True
    if _has_critical_nonpass_quality_gate(report_summary):
        return True
    readiness = _normalized_report_readiness(report_summary.report_readiness)
    if readiness in {"blocked", "provisional"}:
        return True
    if _has_conflicted_verification_fact(report_summary):
        return True
    if _has_blocking_audit_issue(report_summary):
        return True
    if _major_audit_issue_count(report_summary) >= 2:
        return True
    if (
        readiness == "actionable"
        and _has_non_verified_verification_fact(report_summary)
    ):
        return True
    if (
        readiness == "actionable"
        and _major_audit_issue_count(report_summary) >= 1
    ):
        return True
    if readiness == "actionable" and _has_warn_quality_gate(report_summary):
        return True
    if (
        report_summary.contradictions
        and float(report_summary.confidence)
        < MIN_RESEARCH_CONFIDENCE_WITH_CONTRADICTIONS
    ):
        return True
    return False


def _continue_review_response(report_summary: "ResearchConversationReportSummary") -> str:
    if not _report_has_material_output(report_summary):
        return (
            "This bounded research pass did not produce a concrete grounded report yet, "
            "so I need another bounded pass."
        )
    if report_summary.confidence is None:
        return (
            "This bounded research pass produced material output, but confidence is still "
            "unset, so I need another bounded pass before I should stop."
        )
    if float(report_summary.confidence) < MIN_RESEARCH_REPORT_CONFIDENCE:
        return (
            "This bounded research pass produced material output, but confidence is still "
            "too low to stop yet, so I need one more bounded pass."
        )
    if not report_summary.evidence_refs:
        return (
            "This bounded research pass produced material output, but it still lacks explicit "
            "evidence references, so I need another bounded pass."
        )
    missing_gates = _missing_quality_gates(report_summary)
    if missing_gates:
        return (
            "This bounded research pass is missing required quality gates "
            f"({', '.join(missing_gates)}), so I need another bounded pass."
        )
    if _has_failed_quality_gate(report_summary):
        return (
            "This bounded research pass still has failed quality gates for time anchoring, "
            "scope, source authority, reconciliation, fit, or final status, so I need "
            "another bounded pass."
        )
    if _has_critical_nonpass_quality_gate(report_summary):
        return (
            "This bounded research pass still has unresolved non-pass states in critical gates "
            "such as time anchoring, scope, or source authority, so I need another bounded pass."
        )
    readiness = _normalized_report_readiness(report_summary.report_readiness)
    if readiness == "blocked":
        return (
            "This bounded research pass still marks itself blocked rather than ready, "
            "so I need another bounded pass."
        )
    if readiness == "provisional":
        return (
            "This bounded research pass is still provisional rather than grounded enough "
            "to stop, so I need one more bounded pass."
        )
    if _has_conflicted_verification_fact(report_summary):
        return (
            "This bounded research pass still has conflicted critical facts in the "
            "verification appendix, so I need another bounded pass."
        )
    if (
        readiness == "actionable"
        and _has_non_verified_verification_fact(report_summary)
    ):
        return (
            "This bounded research pass tries to be actionable while some critical facts "
            "remain unverified, so I need another bounded pass."
        )
    if _has_blocking_audit_issue(report_summary):
        return (
            "This bounded research pass still carries blocking audit issues around logic, "
            "freshness, sourcing, or scope fit, so I need another bounded pass."
        )
    if _major_audit_issue_count(report_summary) >= 2:
        return (
            "This bounded research pass still carries multiple major audit issues, so I "
            "need another bounded pass."
        )
    if (
        readiness == "actionable"
        and _major_audit_issue_count(report_summary) >= 1
    ):
        return (
            "This bounded research pass still labels itself actionable while material audit "
            "issues remain, so I need another bounded pass."
        )
    if readiness == "actionable" and _has_warn_quality_gate(report_summary):
        return (
            "This bounded research pass labels itself actionable while standard quality "
            "gates still carry warnings, so I need another bounded pass."
        )
    if (
        report_summary.contradictions
        and float(report_summary.confidence)
        < MIN_RESEARCH_CONFIDENCE_WITH_CONTRADICTIONS
    ):
        return (
            "This bounded research pass surfaced material contradictions that are not resolved "
            "strongly enough yet, so I need one more bounded pass."
        )
    if _report_has_material_output(report_summary):
        return (
            "This bounded research pass produced material output, but it is not stable "
            "enough to stop yet, so I need one more bounded pass."
        )
    return (
        "This bounded research pass did not produce a concrete grounded report yet, "
        "so I need another bounded pass."
    )


def _verification_statuses(
    report_summary: "ResearchConversationReportSummary",
) -> list[str]:
    statuses: list[str] = []
    for item in report_summary.verification_facts:
        if not isinstance(item, dict):
            continue
        status = _clean_text(item.get("status")).lower()
        if status:
            statuses.append(status)
    return statuses


def _has_conflicted_verification_fact(
    report_summary: "ResearchConversationReportSummary",
) -> bool:
    return any(status == "conflicted" for status in _verification_statuses(report_summary))


def _has_non_verified_verification_fact(
    report_summary: "ResearchConversationReportSummary",
) -> bool:
    statuses = _verification_statuses(report_summary)
    return any(status != "verified" for status in statuses) if statuses else False


def _audit_rows(report_summary: "ResearchConversationReportSummary") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in report_summary.audit_issues:
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _quality_gate_rows(
    report_summary: "ResearchConversationReportSummary",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in report_summary.quality_gates:
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _quality_gate_map(
    report_summary: "ResearchConversationReportSummary",
) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for item in _quality_gate_rows(report_summary):
        gate = _normalized_quality_gate_name(item.get("gate"))
        if gate not in REQUIRED_RESEARCH_QUALITY_GATES:
            continue
        statuses[gate] = _normalized_quality_gate_status(item.get("status"))
    return statuses


def _missing_quality_gates(
    report_summary: "ResearchConversationReportSummary",
) -> list[str]:
    present = set(_quality_gate_map(report_summary))
    return sorted(REQUIRED_RESEARCH_QUALITY_GATES - present)


def _has_failed_quality_gate(
    report_summary: "ResearchConversationReportSummary",
) -> bool:
    return any(status == "fail" for status in _quality_gate_map(report_summary).values())


def _has_warn_quality_gate(
    report_summary: "ResearchConversationReportSummary",
) -> bool:
    return any(status == "warn" for status in _quality_gate_map(report_summary).values())


def _has_critical_nonpass_quality_gate(
    report_summary: "ResearchConversationReportSummary",
) -> bool:
    statuses = _quality_gate_map(report_summary)
    return any(
        statuses.get(gate) in {"warn", "not_applicable"}
        for gate in CRITICAL_RESEARCH_QUALITY_GATES
    )


def _has_blocking_audit_issue(
    report_summary: "ResearchConversationReportSummary",
) -> bool:
    return any(
        _clean_text(item.get("severity")).lower() == "critical"
        for item in _audit_rows(report_summary)
    )


def _major_audit_issue_count(
    report_summary: "ResearchConversationReportSummary",
) -> int:
    return sum(
        1
        for item in _audit_rows(report_summary)
        if _clean_text(item.get("severity")).lower() == "major"
    )


class ResearchConversationTurnDecision(BaseModel):
    """Orchestrator decision for one user-authored research turn."""

    action: Literal["respond", "clarify", "research"] = "research"
    public_response: str = ""
    clarifying_question: str = ""
    research_objective: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    delivery_target: str = ""


class ResearchConversationSubproblem(BaseModel):
    """Small executable research subproblem planned before search begins."""

    problem_id: str = ""
    question: str = ""
    why_it_matters: str = ""
    evidence_to_seek: str = ""
    search_hint: str = ""
    depends_on: list[str] = Field(default_factory=list)


class ResearchConversationIntentionPlan(BaseModel):
    """Pre-search decomposition for one bounded DAN Research run."""

    public_response: str = ""
    answer_goal: str = ""
    refined_objective: str = ""
    plan_summary: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    subproblems: list[ResearchConversationSubproblem] = Field(default_factory=list)


class ResearchConversationReviewDecision(BaseModel):
    """Orchestrator decision after one bounded research run finishes."""

    action: Literal["done", "continue", "clarify"] = "done"
    public_response: str = ""
    clarifying_question: str = ""
    next_objective: str = ""


class ResearchConversationMessage(BaseModel):
    """Minimal conversation packet shared between the CLI shell and orchestrator."""

    role: str
    text: str
    kind: str = "message"


class ResearchConversationReportSummary(BaseModel):
    """Compact bounded-run summary routed back into the orchestrator."""

    status: str
    task_id: str
    objective: str
    temporal_mode: str = "timeless"
    temporal_anchor: str = ""
    temporal_window: str = ""
    temporal_guidance: str = ""
    delivery_target: str = ""
    findings: list[str] = Field(default_factory=list)
    evidence_summary: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    verification_facts: list[dict[str, Any]] = Field(default_factory=list)
    audit_issues: list[dict[str, Any]] = Field(default_factory=list)
    quality_gates: list[dict[str, Any]] = Field(default_factory=list)
    report_readiness: str = "grounded"
    readiness_note: str = ""
    confidence: float | None = None
    recommended_change: str = ""
    error: str | None = None


class ResearchConversationFacts(BaseModel):
    """Shared high-signal runtime/session facts for orchestrator reasoning."""

    product_name: str
    workspace_root: str
    effective_working_directory: str
    shell_process_directory: str
    session_id: str
    active_model: str = ""
    thinking_mode: str = ""
    enabled_tools: list[str] = Field(default_factory=list)
    research_turn_count: int = 0
    conversation_message_count: int = 0
    pending_clarification: str | None = None
    latest_report_status: str = ""
    latest_report_objective: str = ""
    latest_report_confidence: float | None = None
    latest_report_error: str | None = None
    default_delivery_target: str = ""
    depth_profile: str = "standard"
    requested_reader_count: int | None = None
    current_timestamp: str = ""
    current_date: str = ""
    timezone: str = ""
    time_awareness_policy: str = ""


class ResearchConversationContext(BaseModel):
    """Shared shell/runtime context for one orchestrator decision."""

    workspace_root: str
    model: str
    thinking_mode: str
    tool_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    default_delivery_target: str = ""
    depth_profile: str = "standard"
    requested_reader_count: int | None = None
    pending_clarification: str | None = None
    facts: ResearchConversationFacts
    recent_conversation: list[ResearchConversationMessage] = Field(default_factory=list)
    recent_reports: list[ResearchConversationReportSummary] = Field(default_factory=list)


def _conversation_plan_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "public_response": {"type": "string"},
            "answer_goal": {"type": "string"},
            "refined_objective": {"type": "string"},
            "plan_summary": {"type": "string"},
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
            },
            "subproblems": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "problem_id": {"type": "string"},
                        "question": {"type": "string"},
                        "why_it_matters": {"type": "string"},
                        "evidence_to_seek": {"type": "string"},
                        "search_hint": {"type": "string"},
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["question", "why_it_matters", "evidence_to_seek"],
                },
            },
        },
        "required": ["answer_goal", "refined_objective", "subproblems"],
    }


def _conversation_turn_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["respond", "clarify", "research"],
            },
            "public_response": {"type": "string"},
            "clarifying_question": {"type": "string"},
            "research_objective": {"type": "string"},
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
            },
            "delivery_target": {"type": "string"},
        },
        "required": ["action", "public_response"],
    }


def _conversation_review_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["done", "continue", "clarify"],
            },
            "public_response": {"type": "string"},
            "clarifying_question": {"type": "string"},
            "next_objective": {"type": "string"},
        },
        "required": ["action", "public_response"],
    }


def _conversation_plan_contract() -> OutputContract:
    return OutputContract(
        definition_of_done=(
            "Before bounded research starts, break the objective into small, concrete, "
            "executable research subproblems. For each subproblem, state why it matters "
            "to the final answer and what evidence would resolve it. If a subproblem is "
            "still too broad, split it again until it is small enough to search or verify "
            "cleanly. When a prior bounded report is supplied, use its unresolved issues "
            "to build a narrower follow-up plan instead of another broad sweep."
        ),
        expected_return_shape=json.dumps(
            {
                "public_response": "<optional>",
                "answer_goal": "<required>",
                "refined_objective": "<required>",
                "plan_summary": "<optional>",
                "acceptance_criteria": ["<optional>"],
                "subproblems": [
                    {
                        "problem_id": "<optional>",
                        "question": "<required>",
                        "why_it_matters": "<required>",
                        "evidence_to_seek": "<required>",
                        "search_hint": "<optional>",
                        "depends_on": ["<optional>"],
                    }
                ],
            },
            sort_keys=True,
        ),
        output_schema=_conversation_plan_schema(),
    )


def _conversation_turn_contract() -> OutputContract:
    return OutputContract(
        definition_of_done=(
            "Decide whether to respond conversationally, ask one clarifying question, "
            "or launch one bounded deep-research run. Use action=respond for status "
            "questions, progress checks, tool/model questions, or other requests already "
            "answerable from context.facts and context.recent_reports. Use action=clarify "
            "when the user has not given enough information to research concretely. Use "
            "action=research only for a concrete investigation, grounding, comparison, "
            "verification, or evidence-gathering request. Before choosing action=research, "
            "use context.facts.current_date, timezone, and time_awareness_policy to "
            "classify the request into one temporal frame: current-as-of-runtime, "
            "historical snapshot, trend over time, or timeless/default."
        ),
        expected_return_shape=json.dumps(
            {
                "action": "respond|clarify|research",
                "public_response": "<required>",
                "clarifying_question": "<optional>",
                "research_objective": "<optional>",
                "acceptance_criteria": ["<optional>"],
                "delivery_target": "<optional>",
            },
            sort_keys=True,
        ),
        output_schema=_conversation_turn_schema(),
    )


def _conversation_review_contract() -> OutputContract:
    return OutputContract(
        definition_of_done=(
            "Review the bounded deep-research run and decide whether to stop, continue "
            "with one more bounded research pass, or ask one clarifying question. Do not "
            "declare done when the run failed, returned no concrete grounded report, has "
            "low confidence, lacks explicit evidence refs, or makes a conclusion about a "
            "concrete external entity whose current identity, status, version, or availability "
            "has not been verified strongly enough from authoritative evidence. Also do not "
            "declare done when the report remains provisional/blocked, when the verification "
            "appendix still contains conflicted critical facts, or when the audit appendix still "
            "shows blocking logic/freshness/authority/scope-fit issues. Every completed report "
            "must also carry standard quality gates for time_anchor, scope_boundary, "
            "source_authority, numeric_reconciliation, claim_object_fit, and final_status; "
            "failed or missing gates require another pass. If another bounded pass is still "
            "required, the run must remain incomplete until that additional pass is actually "
            "included in the artifact."
        ),
        expected_return_shape=json.dumps(
            {
                "action": "done|continue|clarify",
                "public_response": "<required>",
                "clarifying_question": "<optional>",
                "next_objective": "<optional>",
            },
            sort_keys=True,
        ),
        output_schema=_conversation_review_schema(),
    )


def _objective_facets(objective: str) -> list[str]:
    cleaned = _clean_text(objective)
    if not cleaned:
        return []
    clauses = [
        _clean_text(part)
        for part in re.split(r"[;\n]+", cleaned)
        if _clean_text(part)
    ]
    if len(clauses) <= 1:
        clauses = [
            _clean_text(part)
            for part in re.split(
                r"\b(?:and|plus|versus|vs\.?|compare|including)\b",
                cleaned,
                maxsplit=3,
                flags=re.IGNORECASE,
            )
            if _clean_text(part)
        ]
    facets: list[str] = []
    for clause in clauses:
        if clause and clause not in facets:
            facets.append(clause)
    return facets[:4]


def _normalize_subproblem_dependencies(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _dedupe([value])
    if isinstance(value, (list, tuple, set)):
        return _dedupe([str(item) for item in value])
    return _dedupe([str(value)])


def _report_follow_up_subproblems(
    previous_report: ResearchConversationReportSummary | None,
) -> list[ResearchConversationSubproblem]:
    if previous_report is None:
        return []

    subproblems: list[ResearchConversationSubproblem] = []
    seen_questions: set[str] = set()

    def _append(
        *,
        question: str,
        why_it_matters: str,
        evidence_to_seek: str,
        search_hint: str = "",
    ) -> None:
        normalized_question = _clean_text(question)
        if not normalized_question or normalized_question in seen_questions:
            return
        seen_questions.add(normalized_question)
        subproblems.append(
            ResearchConversationSubproblem(
                problem_id=f"followup-{len(subproblems) + 1}",
                question=normalized_question,
                why_it_matters=_clean_text(why_it_matters),
                evidence_to_seek=_clean_text(evidence_to_seek),
                search_hint=_clean_text(search_hint),
            )
        )

    for item in previous_report.audit_issues:
        if not isinstance(item, dict):
            continue
        issue = _clean_text(item.get("issue"))
        if not issue:
            continue
        claim = _clean_text(item.get("affected_claim")) or "the unresolved claim"
        follow_up = _clean_text(item.get("required_follow_up"))
        _append(
            question=f"Resolve the remaining issue around {claim}.",
            why_it_matters=issue,
            evidence_to_seek=follow_up
            or "Gather the evidence needed to close this gap cleanly.",
            search_hint=follow_up,
        )
        if len(subproblems) >= 6:
            return subproblems

    for item in previous_report.verification_facts:
        if not isinstance(item, dict):
            continue
        status = _clean_text(item.get("status")).lower()
        if status == "verified":
            continue
        fact = _clean_text(item.get("fact"))
        if not fact:
            continue
        note = _clean_text(item.get("note"))
        source = _clean_text(item.get("source"))
        _append(
            question=f"Verify or resolve the critical fact: {fact}",
            why_it_matters=note or "The last bounded pass did not settle this fact strongly enough.",
            evidence_to_seek=(
                "Find direct evidence that verifies or resolves this fact."
                + (f" Prior source: {source}." if source else "")
            ),
            search_hint=source,
        )
        if len(subproblems) >= 6:
            return subproblems

    for question in previous_report.open_questions:
        normalized = _clean_text(question)
        if not normalized:
            continue
        _append(
            question=normalized,
            why_it_matters="This answer is still needed to close the final response.",
            evidence_to_seek="Find enough grounded evidence to answer this directly.",
        )
        if len(subproblems) >= 6:
            return subproblems

    for contradiction in previous_report.contradictions:
        normalized = _clean_text(contradiction)
        if not normalized:
            continue
        _append(
            question=f"Resolve this contradiction: {normalized}",
            why_it_matters="Conflicting evidence from the prior pass still affects the answer.",
            evidence_to_seek="Identify the stronger evidence or reconcile the disagreement.",
        )
        if len(subproblems) >= 6:
            return subproblems
    return subproblems


def _fallback_intention_plan(
    *,
    objective: str,
    delivery_target: str,
    acceptance_criteria: list[str],
    context: ResearchConversationContext,
    planning_mode: Literal["initial", "continuation"],
    previous_report: ResearchConversationReportSummary | None = None,
) -> ResearchConversationIntentionPlan:
    _ = delivery_target
    refined_objective = _clean_text(objective) or "Investigate the user's research request."
    subproblems = _report_follow_up_subproblems(previous_report)
    if not subproblems:
        facets = _objective_facets(refined_objective)
        if len(facets) > 1:
            subproblems = [
                ResearchConversationSubproblem(
                    problem_id=f"problem-{index}",
                    question=facet,
                    why_it_matters=(
                        "This is one concrete part of the user's overall ask and needs "
                        "grounded evidence before synthesis."
                    ),
                    evidence_to_seek=(
                        "Find the strongest available evidence that answers this part directly."
                    ),
                )
                for index, facet in enumerate(facets, start=1)
            ]
    if not subproblems:
        subproblems = [
            ResearchConversationSubproblem(
                problem_id="problem-1",
                question=refined_objective,
                why_it_matters=(
                    "This is the core question that the final response needs to answer."
                ),
                evidence_to_seek=(
                    "Gather the strongest directly relevant evidence, then resolve any "
                    "material contradictions or gaps."
                ),
            )
        ]
    plan_summary = (
        "Narrow the research into concrete subproblems before search so each reader "
        "knows what it is trying to resolve and why that evidence matters."
    )
    if planning_mode == "continuation" and previous_report is not None:
        plan_summary = (
            "Narrow the follow-up pass to the unresolved gaps from the last bounded run "
            "instead of repeating another broad sweep."
        )
    return ResearchConversationIntentionPlan(
        public_response=(
            "I’ll break this into smaller research questions first, then run the bounded pass."
            if planning_mode == "initial"
            else "I’ll narrow the next pass to the unresolved gaps from the last run."
        ),
        answer_goal=refined_objective,
        refined_objective=refined_objective,
        plan_summary=plan_summary,
        acceptance_criteria=list(acceptance_criteria or context.acceptance_criteria),
        subproblems=subproblems,
    )


def _fallback_turn_decision(
    *,
    user_message: str,
    pending_clarification: str | None,
    context: ResearchConversationContext,
) -> ResearchConversationTurnDecision:
    if not _clean_text(user_message):
        return ResearchConversationTurnDecision(
            action="respond",
            public_response=(
                "I’m ready. Give me a concrete research question, comparison, or "
                "verification task."
            ),
        )
    if pending_clarification:
        return ResearchConversationTurnDecision(
            action="clarify",
            public_response=(
                "I still need one concrete clarification before I should start researching."
            ),
            clarifying_question=pending_clarification,
        )
    latest_report = _latest_resumable_report_summary(context)
    if latest_report is not None and _looks_like_continuation_request(user_message):
        objective = _clean_text(latest_report.objective)
        if objective:
            return ResearchConversationTurnDecision(
                action="research",
                public_response=(
                    "I’ll continue from the latest research objective and run another "
                    "bounded pass."
                ),
                research_objective=objective,
                delivery_target=(
                    _clean_text(latest_report.delivery_target)
                    or _clean_text(context.default_delivery_target)
                ),
            )
    return ResearchConversationTurnDecision(
        action="respond",
        public_response=(
            "I can chat about the workspace and I can also launch bounded deep research. "
            "Tell me what you want investigated, verified, compared, or grounded."
        ),
    )


def _fallback_review_decision(
    *,
    objective: str,
    report_summary: ResearchConversationReportSummary,
) -> ResearchConversationReviewDecision:
    if not _review_requires_more_work(objective, report_summary):
        return ResearchConversationReviewDecision(
            action="done",
            public_response="This bounded research pass is done.",
        )
    return ResearchConversationReviewDecision(
        action="continue",
        public_response=_continue_review_response(report_summary),
        next_objective=_clean_text(objective),
    )


def _normalize_intention_plan(
    payload: dict[str, Any],
    *,
    objective: str,
    delivery_target: str,
    acceptance_criteria: list[str],
    context: ResearchConversationContext,
    planning_mode: Literal["initial", "continuation"],
    previous_report: ResearchConversationReportSummary | None = None,
) -> ResearchConversationIntentionPlan:
    normalized_payload = dict(_parse_payload(payload or {}))
    try:
        plan = ResearchConversationIntentionPlan.model_validate(normalized_payload)
    except Exception:
        return _fallback_intention_plan(
            objective=objective,
            delivery_target=delivery_target,
            acceptance_criteria=acceptance_criteria,
            context=context,
            planning_mode=planning_mode,
            previous_report=previous_report,
        )

    cleaned_subproblems: list[ResearchConversationSubproblem] = []
    seen_questions: set[str] = set()
    for index, item in enumerate(plan.subproblems, start=1):
        question = _clean_text(item.question)
        if not question or question in seen_questions:
            continue
        seen_questions.add(question)
        cleaned_subproblems.append(
            ResearchConversationSubproblem(
                problem_id=_clean_text(item.problem_id) or f"problem-{index}",
                question=question,
                why_it_matters=_clean_text(item.why_it_matters)
                or "This subproblem closes one necessary part of the final answer.",
                evidence_to_seek=_clean_text(item.evidence_to_seek)
                or "Find direct evidence that resolves this subproblem cleanly.",
                search_hint=_clean_text(item.search_hint),
                depends_on=_normalize_subproblem_dependencies(item.depends_on),
            )
        )

    if not cleaned_subproblems:
        return _fallback_intention_plan(
            objective=objective,
            delivery_target=delivery_target,
            acceptance_criteria=acceptance_criteria,
            context=context,
            planning_mode=planning_mode,
            previous_report=previous_report,
        )

    answer_goal = _clean_text(plan.answer_goal or objective)
    refined_objective = _clean_text(plan.refined_objective or answer_goal or objective)
    plan_summary = _clean_text(plan.plan_summary) or (
        "Answer the user's objective by resolving the planned subproblems in a bounded way."
    )
    return ResearchConversationIntentionPlan(
        public_response=_clean_text(plan.public_response),
        answer_goal=answer_goal or _clean_text(objective),
        refined_objective=refined_objective or _clean_text(objective),
        plan_summary=plan_summary,
        acceptance_criteria=_dedupe(
            [*list(acceptance_criteria), *list(plan.acceptance_criteria)]
        ),
        subproblems=cleaned_subproblems,
    )


def _normalize_turn_decision(
    payload: dict[str, Any],
    *,
    user_message: str,
    pending_clarification: str | None,
    context: ResearchConversationContext,
) -> ResearchConversationTurnDecision:
    normalized_payload = dict(payload or {})
    if "action" not in normalized_payload or not _clean_text(
        normalized_payload.get("action")
    ):
        if _clean_text(normalized_payload.get("clarifying_question")):
            normalized_payload["action"] = "clarify"
        elif _clean_text(normalized_payload.get("research_objective")):
            normalized_payload["action"] = "research"
        elif _clean_text(normalized_payload.get("public_response")):
            normalized_payload["action"] = "respond"
    try:
        decision = ResearchConversationTurnDecision.model_validate(normalized_payload)
    except Exception:
        return _fallback_turn_decision(
            user_message=user_message,
            pending_clarification=pending_clarification,
            context=context,
        )
    decision = decision.model_copy(
        update={
            "public_response": _clean_text(decision.public_response),
            "clarifying_question": _clean_text(decision.clarifying_question),
            "research_objective": _clean_text(decision.research_objective),
            "acceptance_criteria": _dedupe(list(decision.acceptance_criteria)),
            "delivery_target": _clean_text(decision.delivery_target),
        }
    )
    if decision.action == "clarify":
        if not decision.clarifying_question:
            return _fallback_turn_decision(
                user_message=user_message,
                pending_clarification=pending_clarification,
                context=context,
            )
        if not decision.public_response:
            return decision.model_copy(
                update={"public_response": decision.clarifying_question}
            )
        return decision
    if decision.action == "respond" and not decision.clarifying_question and _looks_like_user_question(
        decision.public_response
    ):
        return decision.model_copy(
            update={
                "action": "clarify",
                "clarifying_question": decision.public_response,
            }
        )
    if decision.action == "research":
        research_objective = decision.research_objective or _clean_text(user_message)
        public_response = (
            decision.public_response
            or "I’m starting one bounded research run for this request."
        )
        if not research_objective:
            return _fallback_turn_decision(
                user_message=user_message,
                pending_clarification=pending_clarification,
                context=context,
            )
        return decision.model_copy(
            update={
                "research_objective": research_objective,
                "public_response": public_response,
                "delivery_target": (
                    decision.delivery_target
                    or _clean_text(context.default_delivery_target)
                ),
            }
        )
    return decision.model_copy(
        update={
            "public_response": (
                decision.public_response
                or "I can help with research. Ask me to investigate, compare, verify, or summarize something concrete."
            )
        }
    )


def _normalize_review_decision(
    payload: dict[str, Any],
    *,
    objective: str,
    report_summary: ResearchConversationReportSummary,
) -> ResearchConversationReviewDecision:
    normalized_payload = dict(payload or {})
    if "action" not in normalized_payload or not _clean_text(
        normalized_payload.get("action")
    ):
        if _clean_text(normalized_payload.get("clarifying_question")):
            normalized_payload["action"] = "clarify"
        elif _clean_text(normalized_payload.get("next_objective")):
            normalized_payload["action"] = "continue"
        elif _clean_text(normalized_payload.get("public_response")):
            normalized_payload["action"] = (
                "continue"
                if _review_requires_more_work(objective, report_summary)
                else "done"
            )
    try:
        decision = ResearchConversationReviewDecision.model_validate(normalized_payload)
    except Exception:
        return _fallback_review_decision(objective=objective, report_summary=report_summary)
    decision = decision.model_copy(
        update={
            "public_response": _clean_text(decision.public_response),
            "clarifying_question": _clean_text(decision.clarifying_question),
            "next_objective": _clean_text(decision.next_objective),
        }
    )
    if _review_requires_more_work(objective, report_summary) and decision.action == "done":
        return ResearchConversationReviewDecision(
            action="continue",
            public_response=_continue_review_response(report_summary),
            next_objective=_clean_text(objective),
        )
    if decision.action == "clarify":
        if not decision.clarifying_question:
            return _fallback_review_decision(objective=objective, report_summary=report_summary)
        if not decision.public_response:
            return decision.model_copy(
                update={"public_response": decision.clarifying_question}
            )
        return decision
    if decision.action == "continue":
        next_objective = decision.next_objective or _clean_text(objective)
        public_response = (
            decision.public_response
            or "I see one more bounded research pass to make before I should stop."
        )
        return decision.model_copy(
            update={
                "next_objective": next_objective,
                "public_response": public_response,
            }
        )
    return decision.model_copy(
        update={
            "public_response": (
                decision.public_response or "This bounded research pass is done."
            )
        }
    )


class ResearchConversationController:
    """Durable orchestrator controller for the DAN Research shell."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        model: str,
        stream_text_responses: bool = False,
        provider_request_overrides: dict[str, Any] | None = None,
        hedge_max_attempts: int | None = None,
        hedge_delay_seconds: float | None = None,
        event_callback=None,
    ) -> None:
        self._worker = build_research_orchestrator_worker(
            worker_id="dan-research.orchestrator",
            model=str(model),
        )
        self._runner = DurableAgentRunner(
            completion_provider=ProviderCompletionAdapter(
                provider=provider,
                default_model=str(model),
                stream_text_responses=stream_text_responses,
                provider_request_overrides=provider_request_overrides,
                hedge_max_attempts=_resolve_control_hedge_max_attempts(
                    hedge_max_attempts
                ),
                hedge_delay_seconds=_resolve_control_hedge_delay_seconds(
                    hedge_delay_seconds
                ),
                event_callback=event_callback,
            )
        )

    def create_session(
        self,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> DurableAgentSessionState:
        return self._runner.create_session(self._worker, metadata=metadata)

    @staticmethod
    def load_session(payload: dict[str, Any] | None) -> DurableAgentSessionState | None:
        if not payload:
            return None
        return DurableAgentSessionState.model_validate(payload)

    @staticmethod
    def dump_session(session: DurableAgentSessionState) -> dict[str, Any]:
        return session.model_dump(mode="json")

    async def decide_user_turn(
        self,
        *,
        session: DurableAgentSessionState | None,
        user_message: str,
        pending_clarification: str | None,
        context: ResearchConversationContext,
    ) -> tuple[ResearchConversationTurnDecision, DurableAgentSessionState]:
        durable_session = session or self.create_session(
            metadata={"surface": "dan-research", "kind": "conversation"}
        )
        request = ExecutionRequest.from_harness(
            task=(
                "Handle the next DAN Research user turn. Decide whether to respond "
                "conversationally, ask one clarifying question, or launch one bounded research run."
            ),
            acquisition=AcquisitionRequest(
                policy=AcquisitionPolicy(reuse_continuation=False)
            ),
            output_contract=_conversation_turn_contract(),
            input_payload={
                "mode": "user_turn",
                "user_message": user_message,
                "pending_clarification": pending_clarification or "",
                "context": context.model_dump(mode="json"),
            },
            metadata={"surface": "dan-research", "turn_kind": "user_turn"},
        )
        self._runner.enqueue_message(durable_session, request)
        result = await self._runner.process_next(self._worker, durable_session)
        payload = _parse_payload(result.outputs if result is not None else {})
        return (
            _normalize_turn_decision(
                payload,
                user_message=user_message,
                pending_clarification=pending_clarification,
                context=context,
            ),
            durable_session,
        )

    async def plan_research_intention(
        self,
        *,
        session: DurableAgentSessionState | None,
        objective: str,
        delivery_target: str,
        acceptance_criteria: list[str],
        context: ResearchConversationContext,
        planning_mode: Literal["initial", "continuation"] = "initial",
        previous_report: ResearchConversationReportSummary | None = None,
    ) -> tuple[ResearchConversationIntentionPlan, DurableAgentSessionState]:
        durable_session = session or self.create_session(
            metadata={"surface": "dan-research", "kind": "planning"}
        )
        try:
            request = ExecutionRequest.from_harness(
                task=(
                    "Break this research objective into small executable "
                    "subproblems before search begins."
                ),
                acquisition=AcquisitionRequest(
                    policy=AcquisitionPolicy(reuse_continuation=False)
                ),
                output_contract=_conversation_plan_contract(),
                input_payload={
                    "mode": "research_intention_breakdown",
                    "planning_mode": planning_mode,
                    "objective": objective,
                    "delivery_target": delivery_target,
                    "acceptance_criteria": list(acceptance_criteria),
                    "previous_report": (
                        previous_report.model_dump(mode="json")
                        if previous_report is not None
                        else {}
                    ),
                    "context": context.model_dump(mode="json"),
                },
                metadata={
                    "surface": "dan-research",
                    "turn_kind": "research_intention_breakdown",
                    "planning_mode": planning_mode,
                },
            )
            self._runner.enqueue_message(durable_session, request)
            result = await self._runner.process_next(self._worker, durable_session)
            payload = _parse_payload(result.outputs if result is not None else {})
            return (
                _normalize_intention_plan(
                    payload,
                    objective=objective,
                    delivery_target=delivery_target,
                    acceptance_criteria=acceptance_criteria,
                    context=context,
                    planning_mode=planning_mode,
                    previous_report=previous_report,
                ),
                durable_session,
            )
        except Exception:
            return (
                _fallback_intention_plan(
                    objective=objective,
                    delivery_target=delivery_target,
                    acceptance_criteria=acceptance_criteria,
                    context=context,
                    planning_mode=planning_mode,
                    previous_report=previous_report,
                ),
                durable_session,
            )

    async def review_research_result(
        self,
        *,
        session: DurableAgentSessionState,
        objective: str,
        report_summary: ResearchConversationReportSummary,
        context: ResearchConversationContext,
    ) -> tuple[ResearchConversationReviewDecision, DurableAgentSessionState]:
        request = ExecutionRequest.from_harness(
            task=(
                "Review the bounded deep-research run. Decide whether to stop, "
                "continue with one more bounded research pass, or ask one clarifying question."
            ),
            acquisition=AcquisitionRequest(
                policy=AcquisitionPolicy(reuse_continuation=False)
            ),
            output_contract=_conversation_review_contract(),
            input_payload={
                "mode": "post_run_review",
                "objective": objective,
                "report_summary": report_summary.model_dump(mode="json"),
                "context": context.model_dump(mode="json"),
            },
            metadata={"surface": "dan-research", "turn_kind": "post_run_review"},
        )
        self._runner.enqueue_message(session, request)
        result = await self._runner.process_next(self._worker, session)
        payload = _parse_payload(result.outputs if result is not None else {})
        return (
            _normalize_review_decision(
                payload,
                objective=objective,
                report_summary=report_summary,
            ),
            session,
        )


__all__ = [
    "ResearchConversationContext",
    "ResearchConversationController",
    "ResearchConversationFacts",
    "ResearchConversationIntentionPlan",
    "ResearchConversationMessage",
    "ResearchConversationReportSummary",
    "ResearchConversationReviewDecision",
    "ResearchConversationSubproblem",
    "ResearchConversationTurnDecision",
]
