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
DEFAULT_RESEARCH_CONTROL_HEDGE_DELAY_SECONDS = 10.0
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
PROVISIONAL_CLOSURE_GATES = frozenset(
    {"time_anchor", "source_authority", "numeric_reconciliation", "final_status"}
)
BEST_EFFORT_FINAL_CLOSURE_GATES = frozenset(
    {*PROVISIONAL_CLOSURE_GATES, "scope_boundary"}
)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _clean_block_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned_lines = [line.rstrip() for line in lines]
    while cleaned_lines and not cleaned_lines[0].strip():
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1].strip():
        cleaned_lines.pop()
    return "\n".join(cleaned_lines)


_DECISION_KEYS = frozenset(
    {
        "action",
        "public_response",
        "clarifying_question",
        "research_objective",
        "next_objective",
        "response_kind",
        "artifact_title",
        "artifact_markdown",
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
        or report_summary.evidence_integrity
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
    report_summary: "ResearchConversationReportSummary",
) -> bool:
    if _clean_text(report_summary.error):
        return True
    if _clean_text(report_summary.status).lower() != "completed":
        return True
    if not _report_has_material_output(report_summary):
        return True
    if not report_summary.evidence_refs:
        return True
    if _has_conflicted_verification_fact(report_summary):
        return True
    if _has_blocking_audit_issue(report_summary):
        return True
    if (
        report_summary.contradictions
        and (
            report_summary.confidence is None
            or float(report_summary.confidence) < MIN_RESEARCH_CONFIDENCE_WITH_CONTRADICTIONS
        )
    ):
        return True
    return False


def _done_review_response(
    objective: str,
    report_summary: "ResearchConversationReportSummary",
) -> str:
    if _allows_explicit_unverifiable_closure(objective, report_summary):
        return (
            "This bounded research pass is complete enough to stop as a provisional "
            "report: the remaining fact gaps are explicitly marked unverifiable and the "
            "confidence is already downgraded accordingly."
        )
    if _allows_best_effort_final_closure(objective, report_summary):
        return (
            "This bounded research pass is complete enough to stop as a provisional "
            "report: the objective explicitly capped further passes and the remaining "
            "caveats are already spelled out."
        )
    readiness = _normalized_report_readiness(report_summary.report_readiness)
    if readiness in {"blocked", "provisional"}:
        return (
            "This bounded research pass is complete enough to stop as a provisional "
            "report with explicit caveats."
        )
    return "This bounded research pass is done."


def _continue_review_response(report_summary: "ResearchConversationReportSummary") -> str:
    status = _clean_text(report_summary.status).lower()
    if not _report_has_material_output(report_summary):
        return (
            "This bounded research pass did not produce a concrete grounded report yet, "
            "so I need another bounded pass."
        )
    if status != "completed":
        return (
            "This bounded research pass surfaced some material output, but it did not "
            "finish cleanly, so I need another bounded pass."
        )
    if not report_summary.evidence_refs:
        return (
            "This bounded research pass produced material output, but it still lacks explicit "
            "evidence references, so I need another bounded pass."
        )
    if _has_conflicted_verification_fact(report_summary):
        return (
            "This bounded research pass still has conflicted critical facts in the "
            "verification appendix, so I need another bounded pass."
        )
    if _has_blocking_audit_issue(report_summary):
        return (
            "This bounded research pass still carries blocking audit issues around logic, "
            "freshness, sourcing, or scope fit, so I need another bounded pass."
        )
    if (
        report_summary.contradictions
        and (
            report_summary.confidence is None
            or float(report_summary.confidence) < MIN_RESEARCH_CONFIDENCE_WITH_CONTRADICTIONS
        )
    ):
        return (
            "This bounded research pass surfaced material contradictions that are not resolved "
            "strongly enough yet, so I need one more bounded pass."
        )
    return (
        "This bounded research pass produced material output, but it is not stable "
        "enough to stop yet, so I need one more bounded pass."
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


def _conflicted_verification_fact_count(
    report_summary: "ResearchConversationReportSummary",
) -> int:
    return sum(status == "conflicted" for status in _verification_statuses(report_summary))


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


def _nonpass_quality_gates(
    report_summary: "ResearchConversationReportSummary",
) -> set[str]:
    return {
        gate
        for gate, status in _quality_gate_map(report_summary).items()
        if status != "pass"
    }


def _missing_quality_gates(
    report_summary: "ResearchConversationReportSummary",
) -> list[str]:
    present = set(_quality_gate_map(report_summary))
    return sorted(REQUIRED_RESEARCH_QUALITY_GATES - present)


def _objective_allows_explicit_unverifiable_closure(objective: str) -> bool:
    text = _clean_text(objective).lower()
    if "unverifiable" not in text:
        return False
    closure_cues = (
        "finalize",
        "finalise",
        "provisional",
        "confidence downgrade",
        "rather than maintaining blocked",
        "rather than remaining blocked",
        "rather than failing",
        "best available evidence",
    )
    return any(cue in text for cue in closure_cues)


def _objective_allows_best_effort_final_closure(objective: str) -> bool:
    text = _clean_text(objective).lower()
    if not text:
        return False
    final_pass_cues = (
        "final bounded research pass",
        "final bounded pass",
        "final pass",
        "last pass",
        "no fifth pass",
        "no sixth pass",
        "no more passes",
        "no further passes",
        "after this pass",
    )
    close_cues = (
        "provisional report",
        "provisional memo",
        "finalize provisionally",
        "finalise provisionally",
        "close provisionally",
        "explicit caveat",
        "explicit caveats",
        "available fragments",
        "best available",
        "best effort",
        "accept headlines",
        "accept snippets",
    )
    return any(cue in text for cue in final_pass_cues) and any(
        cue in text for cue in close_cues
    )


def _report_mentions_unverifiable_closure(
    report_summary: "ResearchConversationReportSummary",
) -> bool:
    verification_statuses = {
        "unverified",
        "unverifiable",
        "not_verified",
        "not_verifiable",
    }
    if any(status in verification_statuses for status in _verification_statuses(report_summary)):
        return True

    text_bits: list[str] = [
        report_summary.readiness_note,
        report_summary.recommended_change,
        *report_summary.findings,
        *report_summary.evidence_summary,
        *report_summary.open_questions,
        *report_summary.evidence_refs,
    ]
    for row in [*report_summary.verification_facts, *report_summary.audit_issues]:
        if not isinstance(row, dict):
            continue
        text_bits.extend(str(value) for value in row.values() if isinstance(value, (str, int, float)))
    lowered = _clean_text(" ".join(text_bits)).lower()
    return any(
        phrase in lowered
        for phrase in (
            "unverifiable",
            "unable to verify",
            "not verifiable",
            "source inaccessible",
            "sources remain inaccessible",
            "confidence downgrade",
        )
    )


def _report_mentions_best_effort_final_closure(
    report_summary: "ResearchConversationReportSummary",
) -> bool:
    text_bits: list[str] = [
        report_summary.readiness_note,
        report_summary.recommended_change,
        *report_summary.findings,
        *report_summary.evidence_summary,
        *report_summary.open_questions,
        *report_summary.evidence_refs,
    ]
    for row in [*report_summary.verification_facts, *report_summary.audit_issues]:
        if not isinstance(row, dict):
            continue
        text_bits.extend(
            str(value) for value in row.values() if isinstance(value, (str, int, float))
        )
    lowered = _clean_text(" ".join(text_bits)).lower()
    return any(
        phrase in lowered
        for phrase in (
            "provisional",
            "caveat",
            "caveats",
            "unverifiable",
            "not actionable",
            "stale",
            "lag",
            "intraday snapshot",
            "not the official",
        )
    )


def _allows_explicit_unverifiable_closure(
    objective: str,
    report_summary: "ResearchConversationReportSummary",
) -> bool:
    readiness = _normalized_report_readiness(report_summary.report_readiness)
    if readiness == "actionable":
        return False
    if not _objective_allows_explicit_unverifiable_closure(objective):
        return False
    if not _report_mentions_unverifiable_closure(report_summary):
        return False
    if _clean_text(report_summary.status).lower() != "completed":
        return False
    if not _report_has_material_output(report_summary):
        return False
    if report_summary.confidence is None:
        return False
    if not report_summary.evidence_refs:
        return False
    if _missing_quality_gates(report_summary):
        return False
    if _has_conflicted_verification_fact(report_summary):
        return False
    if _has_blocking_audit_issue(report_summary):
        return False
    if _major_audit_issue_count(report_summary) >= 2:
        return False
    nonpass = _nonpass_quality_gates(report_summary)
    return not nonpass or nonpass.issubset(PROVISIONAL_CLOSURE_GATES)


def _allows_best_effort_final_closure(
    objective: str,
    report_summary: "ResearchConversationReportSummary",
) -> bool:
    if _allows_explicit_unverifiable_closure(objective, report_summary):
        return True
    readiness = _normalized_report_readiness(report_summary.report_readiness)
    if readiness == "actionable":
        return False
    if not _objective_allows_best_effort_final_closure(objective):
        return False
    if not _report_mentions_best_effort_final_closure(report_summary):
        return False
    if _clean_text(report_summary.error):
        return False
    if _clean_text(report_summary.status).lower() != "completed":
        return False
    if not _report_has_material_output(report_summary):
        return False
    if report_summary.confidence is None:
        return False
    if not report_summary.evidence_refs:
        return False
    if _missing_quality_gates(report_summary):
        return False
    if _has_blocking_audit_issue(report_summary):
        return False
    if _major_audit_issue_count(report_summary) >= 3:
        return False
    if _conflicted_verification_fact_count(report_summary) > 1:
        return False
    nonpass = _nonpass_quality_gates(report_summary)
    return not nonpass or nonpass.issubset(BEST_EFFORT_FINAL_CLOSURE_GATES)


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
    response_kind: Literal["chat_reply", "report_reply", "clarification"] = "chat_reply"
    public_response: str = ""
    clarifying_question: str = ""
    research_objective: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    delivery_target: str = ""
    artifact_title: str = ""
    artifact_markdown: str = ""


class ResearchConversationSubproblem(BaseModel):
    """Small executable research subproblem planned before search begins."""

    problem_id: str = ""
    question: str = ""
    why_it_matters: str = ""
    evidence_to_seek: str = ""
    search_hint: str = ""
    depends_on: list[str] = Field(default_factory=list)


class ResearchConversationWorkstream(BaseModel):
    """Parallel evidence stream that groups related subproblems."""

    stream_id: str = ""
    title: str = ""
    goal: str = ""
    why_it_matters: str = ""
    subproblem_ids: list[str] = Field(default_factory=list)
    aggregation_hint: str = ""


class ResearchConversationEvidenceTarget(BaseModel):
    """Typed fact target that helps readers search for one exact piece of evidence."""

    target_id: str = ""
    claim: str = ""
    entity_type: str = ""
    metric_kind: str = ""
    series_kind: str = ""
    comparison_basis: str = ""
    why_it_matters: str = ""
    related_subproblem_ids: list[str] = Field(default_factory=list)
    as_of: str = ""
    unit_or_format: str = ""
    geography_or_scope: str = ""
    accepted_source_families: list[str] = Field(default_factory=list)
    preferred_sites: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    acceptable_proxy: str = ""
    stop_condition: str = ""
    not_found_guidance: str = ""
    not_available_guidance: str = ""


class ResearchConversationIntentionPlan(BaseModel):
    """Pre-search decomposition for one bounded DAN Research run."""

    public_response: str = ""
    answer_goal: str = ""
    refined_objective: str = ""
    plan_summary: str = ""
    recommended_answer_shape: str = ""
    coverage_priorities: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    subproblems: list[ResearchConversationSubproblem] = Field(default_factory=list)
    workstreams: list[ResearchConversationWorkstream] = Field(default_factory=list)
    evidence_targets: list[ResearchConversationEvidenceTarget] = Field(
        default_factory=list
    )


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
    evidence_integrity: list[dict[str, Any]] = Field(default_factory=list)
    audit_issues: list[dict[str, Any]] = Field(default_factory=list)
    quality_gates: list[dict[str, Any]] = Field(default_factory=list)
    report_readiness: str = "grounded"
    artifact_mode: str = "final_report"
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
    claim_ledger: list[dict[str, Any]] = Field(default_factory=list)


def _conversation_plan_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "public_response": {"type": "string"},
            "answer_goal": {"type": "string"},
            "refined_objective": {"type": "string"},
            "plan_summary": {"type": "string"},
            "recommended_answer_shape": {"type": "string"},
            "coverage_priorities": {
                "type": "array",
                "items": {"type": "string"},
            },
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
            "workstreams": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "stream_id": {"type": "string"},
                        "title": {"type": "string"},
                        "goal": {"type": "string"},
                        "why_it_matters": {"type": "string"},
                        "subproblem_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "aggregation_hint": {"type": "string"},
                    },
                    "required": ["goal", "why_it_matters", "subproblem_ids"],
                },
            },
            "evidence_targets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string"},
                        "claim": {"type": "string"},
                        "entity_type": {"type": "string"},
                        "metric_kind": {"type": "string"},
                        "series_kind": {"type": "string"},
                        "comparison_basis": {"type": "string"},
                        "why_it_matters": {"type": "string"},
                        "related_subproblem_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "as_of": {"type": "string"},
                        "unit_or_format": {"type": "string"},
                        "geography_or_scope": {"type": "string"},
                        "accepted_source_families": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "preferred_sites": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "aliases": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "acceptable_proxy": {"type": "string"},
                        "stop_condition": {"type": "string"},
                        "not_found_guidance": {"type": "string"},
                        "not_available_guidance": {"type": "string"},
                    },
                    "required": ["claim", "why_it_matters"],
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
            "response_kind": {
                "type": "string",
                "enum": ["chat_reply", "report_reply", "clarification"],
            },
            "public_response": {"type": "string"},
            "clarifying_question": {"type": "string"},
            "research_objective": {"type": "string"},
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
            },
            "delivery_target": {"type": "string"},
            "artifact_title": {"type": "string"},
            "artifact_markdown": {"type": "string"},
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
            "cleanly. Then group non-conflicting subproblems into a few parallel "
            "workstreams so evidence can be gathered at the same time and aggregated "
            "cleanly afterward. For each workstream, state why that stream exists and "
            "how its evidence should be folded back into the final answer. Also produce "
            "explicit evidence targets for the exact facts that need to be pinned down: "
            "include typed identity fields for entity_type, metric_kind, series_kind, "
            "and comparison_basis whenever the claim is about a product, fund, policy, "
            "or numeric series. Include helpful aliases, preferred sites or source "
            "families, acceptable proxy rules when the exact fact may publish with lag, "
            "clear stop conditions, and separate guidance for 'I still did not find it' "
            "versus 'the likely source family does not appear to publish this exact metric "
            "in this exact form'. When a prior bounded report or frozen claim ledger is "
            "supplied, use unresolved issues to build a narrower follow-up plan instead "
            "of another broad sweep, and do not reopen facts that were already settled "
            "strongly enough unless a contradiction or blocking audit issue explicitly "
            "reopens them. Also choose the answer shape that best fits the user's query "
            "and decision need, and list the content priorities the final answer must "
            "cover. Ask yourself explicitly: what final format would help this user most, "
            "what content is necessary to answer the query well, and what content would be "
            "noise or overkill for this ask. Those are synthesis cues, not a demand for "
            "one fixed report template."
        ),
        expected_return_shape=json.dumps(
            {
                "public_response": "<optional>",
                "answer_goal": "<required>",
                "refined_objective": "<required>",
                "plan_summary": "<optional>",
                "recommended_answer_shape": "<optional>",
                "coverage_priorities": ["<optional>"],
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
                "workstreams": [
                    {
                        "stream_id": "<optional>",
                        "title": "<optional>",
                        "goal": "<required>",
                        "why_it_matters": "<required>",
                        "subproblem_ids": ["<required>"],
                        "aggregation_hint": "<optional>",
                    }
                ],
                "evidence_targets": [
                    {
                        "target_id": "<optional>",
                        "claim": "<required>",
                        "entity_type": "<optional>",
                        "metric_kind": "<optional>",
                        "series_kind": "<optional>",
                        "comparison_basis": "<optional>",
                        "why_it_matters": "<required>",
                        "related_subproblem_ids": ["<optional>"],
                        "as_of": "<optional>",
                        "unit_or_format": "<optional>",
                        "geography_or_scope": "<optional>",
                        "accepted_source_families": ["<optional>"],
                        "preferred_sites": ["<optional>"],
                        "aliases": ["<optional>"],
                        "acceptable_proxy": "<optional>",
                        "stop_condition": "<optional>",
                        "not_found_guidance": "<optional>",
                        "not_available_guidance": "<optional>",
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
            "historical snapshot, trend over time, or timeless/default. When you choose "
            "action=research, set delivery_target to the best-fit answer shape for this "
            "query and decision need, such as a comparison brief, trend snapshot, "
            "verification note, buyer's guide, investment memo, or blocker note. Ask "
            "yourself explicitly: what format best serves this query, what content must "
            "the final answer cover to be useful, and what presentation would help the "
            "user act on it. Treat delivery_target as synthesis guidance, not a fixed "
            "section template. When the user is asking you to turn already-grounded work "
            "into a final memo/report without launching another bounded run, keep "
            "action=respond, set "
            "response_kind=report_reply, and put the full Markdown artifact in "
            "artifact_markdown. If artifact_markdown is omitted because public_response "
            "already contains the final Markdown, the product will reuse public_response "
            "as the saved report artifact."
        ),
        expected_return_shape=json.dumps(
            {
                "action": "respond|clarify|research",
                "response_kind": "chat_reply|report_reply|clarification",
                "public_response": "<required>",
                "clarifying_question": "<optional>",
                "research_objective": "<optional>",
                "acceptance_criteria": ["<optional>"],
                "delivery_target": "<optional>",
                "artifact_title": "<optional>",
                "artifact_markdown": "<optional>",
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
            "declare done when the run failed, returned no material report, lacks explicit "
            "evidence refs, still contains conflicted critical facts in the verification "
            "appendix, still carries blocking audit issues, or leaves material contradictions "
            "resolved only weakly. Treat evidence_integrity as the canonicalized pre-synthesis "
            "fact table: accepted claims can support the memo, accepted_with_proxy or "
            "unverifiable claims must stay caveated, and conflicted claims should not be "
            "treated as settled. Use confidence, report_readiness, verification/audit "
            "details, evidence_integrity, and quality_gates as context for judging whether "
            "the report is solid enough or should stop provisionally with caveats, but do not "
            "keep continuing solely because those metadata fields are imperfect. If another "
            "bounded pass is still required, the run must remain incomplete until that "
            "additional pass is actually included in the artifact. Exception: when the "
            "objective explicitly authorizes a best-effort provisional close after repeated "
            "attempts by marking specific facts as unverifiable with downgraded confidence, "
            "do not keep continuing solely because that already-surfaced gap leaves the "
            "report provisional."
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
    numbered_facets = _numbered_objective_facets(cleaned)
    if numbered_facets:
        return numbered_facets[:4]
    clauses = [
        _clean_objective_facet(part)
        for part in re.split(r"[;\n]+", cleaned)
        if _clean_objective_facet(part)
    ]
    if len(clauses) <= 1:
        clauses = [
            _clean_objective_facet(part)
            for part in re.split(
                r"\b(?:and|plus|versus|vs\.?|compare|including)\b",
                cleaned,
                maxsplit=3,
                flags=re.IGNORECASE,
            )
            if _clean_objective_facet(part)
        ]
    facets: list[str] = []
    for clause in clauses:
        if clause and not _is_control_only_facet(clause) and clause not in facets:
            facets.append(clause)
    return facets[:4]


_GENERIC_DELIVERY_TARGETS = frozenset(
    {
        "",
        "memo",
        "brief",
        "report",
        "research memo",
        "research report",
        "research brief",
    }
)


def _infer_delivery_target(
    objective: str,
    *,
    default: str = "research memo",
) -> str:
    explicit = _clean_text(default)
    normalized_explicit = explicit.lower()
    text = _clean_text(objective).lower()
    if explicit and normalized_explicit not in _GENERIC_DELIVERY_TARGETS:
        return explicit
    if any(
        token in text
        for token in (
            "worth buying",
            " buy ",
            "investment",
            "etf",
            "valuation",
            "bull case",
            "bear case",
        )
    ):
        return "investment thesis memo with thesis, vehicle fit, risks, and decision caveats"
    if any(
        token in text
        for token in ("compare", "versus", " vs ", "difference", "which is better", "pros and cons")
    ):
        return "comparison brief with direct conclusion first, side-by-side evidence, and the deciding differences"
    if any(
        token in text
        for token in ("trend", "over time", "timeline", "recent changes", "past ", "last ", "history of")
    ):
        return "trend snapshot with the time window, what changed, and the main drivers"
    if any(
        token in text
        for token in (
            "verify",
            "is it true",
            "current status",
            "active or not",
            "still available",
            "what is the status",
        )
    ):
        return "verification note with current status, authoritative source trail, and conflicts or stale data"
    if any(token in text for token in ("recommend", "should i", "what should", "decision", "whether to")):
        return "decision memo with options, tradeoffs, risks, and a recommended next step"
    return explicit or "research memo with the direct answer first, strongest evidence, and caveats"


def _infer_coverage_priorities(
    objective: str,
    *,
    delivery_target: str = "",
) -> list[str]:
    text = _clean_text(f"{objective} {delivery_target}").lower()
    priorities: list[str] = []
    if any(
        token in text
        for token in (
            "worth buying",
            " buy ",
            "investment",
            "etf",
            "valuation",
            "bull case",
            "bear case",
        )
    ):
        priorities.extend(
            [
                "State the thesis in decision terms and say what would have to be true for it to hold.",
                "Check thesis-to-vehicle fit so the recommended product or proxy actually matches the user's target exposure.",
                "Cover valuation, momentum, or current drivers only to the extent they change the investment decision.",
                "Surface the main risks, policy constraints, and what evidence would change the view.",
            ]
        )
    elif any(
        token in text
        for token in ("compare", "versus", " vs ", "difference", "which is better", "pros and cons")
    ):
        priorities.extend(
            [
                "Answer the comparison directly before listing background detail.",
                "Use the comparison axes that actually decide the outcome for the user.",
                "Show the strongest evidence on each side and explain the deciding differences.",
                "Call out mismatches in scope, series identity, or freshness that make the comparison less clean.",
            ]
        )
    elif any(
        token in text
        for token in ("trend", "over time", "timeline", "recent changes", "past ", "last ", "history of")
    ):
        priorities.extend(
            [
                "Anchor the time window clearly and use start/end values or dates where possible.",
                "Explain what changed and the main drivers rather than listing disconnected points.",
                "Keep series identity and comparability explicit when multiple benchmarks or vendors differ.",
                "State the remaining freshness or publication-lag limits that matter for the trend reading.",
            ]
        )
    elif any(
        token in text
        for token in (
            "verify",
            "is it true",
            "current status",
            "active or not",
            "still available",
            "what is the status",
        )
    ):
        priorities.extend(
            [
                "State the exact status or identity claim as of the anchored date.",
                "Prefer the shortest authoritative source trail that closes the fact.",
                "Resolve or caveat stale vendor pages, phantom data, or conflicting secondary sources.",
                "Say whether the fact is safe to rely on or still needs follow-up.",
            ]
        )
    else:
        priorities.extend(
            [
                "Answer the user's actual question directly before supporting detail.",
                "Lead with the strongest evidence that bears on the conclusion.",
                "Surface the caveats that materially change how much weight the answer deserves.",
                "End with the clearest next step or decision implication.",
            ]
        )
    for facet in _objective_facets(objective)[:2]:
        priorities.append(f"Explicitly answer this part of the query: {facet}")
    return _dedupe(priorities)[:5]


def _numbered_objective_facets(objective: str) -> list[str]:
    matches = list(
        re.finditer(
            r"(?:(?<=^)|(?<=[\s;:.]))(?:\(\d{1,2}\)|\d{1,2}[.)]|Priority\s+\d{1,2}(?:\s*\([^)]*\))?\s*:)",
            objective,
            flags=re.IGNORECASE,
        )
    )
    if not matches:
        return []
    facets: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(objective)
        facet = _clean_objective_facet(objective[start:end])
        if facet and not _is_control_only_facet(facet) and facet not in facets:
            facets.append(facet)
    return facets


def _clean_objective_facet(value: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    text = re.sub(
        r"^(?:priority\s+\d+\s*)?(?:\([^)]*\)\s*)?[:.)\-\s]+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^(?:blocking|best effort|deferred|optional|secondary)\s*[:.)\-\s]+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    for pattern in (
        r"\b(?:return|populate)\s+(?:explicit\s+)?verification appendix\b.*$",
        r"\b(?:return|populate)\s+(?:the\s+)?audit appendix\b.*$",
        r"\bdo not return empty results\b.*$",
        r"\bmandatory\s*:\s*.*$",
        r"\bre-synthesize\b.*\bquality gates?\b.*$",
        r"\bprioriti[sz]e\b.*\b(?:claim_object_fit|final_status|quality gates?)\b.*$",
    ):
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip(" ;,.-")
    return _clean_text(text)


def _is_control_only_facet(value: str) -> bool:
    text = _clean_text(value).lower()
    if not text:
        return True
    if any(token in text for token in ("claim_object_fit", "final_status", "quality gate")):
        return True
    if "verification appendix" in text and not re.search(
        r"\b(?:retrieve|obtain|verify|search|query|identify|catalog|extract|list|perform|resolve)\b",
        text,
    ):
        return True
    if "unverifiable" in text and re.search(
        r"\b(?:rather than|instead of|not)\s+fail", text
    ):
        return True
    if text.startswith("if ") and "mark" in text and "unverifiable" in text:
        return True
    return False


def _normalize_subproblem_dependencies(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _dedupe([value])
    if isinstance(value, (list, tuple, set)):
        return _dedupe([str(item) for item in value])
    return _dedupe([str(value)])


def _normalize_text_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _dedupe([value])
    if isinstance(value, (list, tuple, set)):
        return _dedupe([str(item) for item in value])
    return _dedupe([str(value)])


def _normalized_claim_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean_text(value).lower()).strip()


def _claim_tokens(value: Any) -> set[str]:
    return {
        token
        for token in _normalized_claim_key(value).split()
        if len(token) >= 4 and token not in {"with", "from", "that", "this", "into"}
    }


def _claims_overlap(left: Any, right: Any) -> bool:
    left_key = _normalized_claim_key(left)
    right_key = _normalized_claim_key(right)
    if not left_key or not right_key:
        return False
    if min(len(left_key), len(right_key)) >= 10 and (
        left_key in right_key or right_key in left_key
    ):
        return True
    left_tokens = _claim_tokens(left_key)
    right_tokens = _claim_tokens(right_key)
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) >= min(2, len(left_tokens), len(right_tokens))


def _extract_preferred_sites(*texts: Any) -> list[str]:
    candidates: list[str] = []
    for text in texts:
        cleaned = _clean_text(text)
        if not cleaned:
            continue
        candidates.extend(
            match.group(1).lower()
            for match in re.finditer(r"\bsite:([a-z0-9.-]+\.[a-z]{2,})\b", cleaned, re.I)
        )
        candidates.extend(
            match.group(1).lower()
            for match in re.finditer(r"https?://([^/\s]+)", cleaned, re.I)
        )
        candidates.extend(
            match.group(1).lower()
            for match in re.finditer(
                r"\b([a-z0-9.-]+\.(?:com|org|net|gov|edu|io|cn|hk|co\.uk))\b",
                cleaned,
                re.I,
            )
        )
    return _dedupe(candidates)


def _extract_aliases(*texts: Any) -> list[str]:
    aliases: list[str] = []
    for text in texts:
        cleaned = _clean_text(text)
        if not cleaned:
            continue
        aliases.append(cleaned)
        aliases.extend(
            _clean_text(match.group(1) or match.group(2))
            for match in re.finditer(r'"([^"]+)"|\'([^\']+)\'', cleaned)
            if _clean_text(match.group(1) or match.group(2))
        )
        if "/" in cleaned:
            aliases.extend(
                _clean_text(part)
                for part in cleaned.split("/")
                if _clean_text(part)
            )
        if "(" in cleaned and ")" in cleaned:
            aliases.extend(
                _clean_text(part)
                for part in re.split(r"[()]", cleaned)
                if _clean_text(part)
            )
    return _dedupe(aliases)


def _infer_evidence_target_as_of(
    *,
    objective: str,
    context: ResearchConversationContext,
    previous_report: ResearchConversationReportSummary | None = None,
    explicit: str = "",
) -> str:
    explicit_text = _clean_text(explicit)
    if explicit_text:
        return explicit_text
    if previous_report is not None and _clean_text(previous_report.temporal_anchor):
        return _clean_text(previous_report.temporal_anchor)
    objective_text = _clean_text(objective)
    if not objective_text:
        return _clean_text(context.facts.current_date)
    dated = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", objective_text)
    if dated:
        return dated[-1]
    years = re.findall(r"\b(?:19|20)\d{2}\b", objective_text)
    if years:
        return years[-1]
    lowered = objective_text.lower()
    if any(
        cue in lowered
        for cue in ("current", "today", "latest", "recent", "runtime", "now", "this week")
    ):
        return _clean_text(context.facts.current_date)
    return ""


def _infer_evidence_target_scope(*texts: Any) -> str:
    lowered = " ".join(_clean_text(text).lower() for text in texts if _clean_text(text))
    if not lowered:
        return ""
    if "stock connect" in lowered:
        return "China / Hong Kong Stock Connect"
    if "shanghai" in lowered or ".sh" in lowered:
        return "Shanghai"
    if "shenzhen" in lowered or ".sz" in lowered:
        return "Shenzhen"
    if ("china" in lowered or "chinese" in lowered) and (
        "hong kong" in lowered or " hk " in f" {lowered} "
    ):
        return "China / Hong Kong"
    if "china" in lowered or "chinese" in lowered:
        return "China"
    if "hong kong" in lowered or "hk" in lowered:
        return "Hong Kong"
    if "u.s." in lowered or "united states" in lowered or "nyse" in lowered:
        return "United States"
    if "global" in lowered or "world" in lowered:
        return "Global"
    return ""


def _infer_evidence_target_unit(*texts: Any) -> str:
    lowered = " ".join(_clean_text(text).lower() for text in texts if _clean_text(text))
    if not lowered:
        return ""
    if "expense ratio" in lowered or "yield" in lowered or "percentage" in lowered:
        return "percentage"
    if "aum" in lowered or "assets under management" in lowered:
        return "currency amount"
    if "ticker" in lowered or "listing" in lowered or "status" in lowered:
        return "exact identifier or status label"
    if "price" in lowered or "settlement" in lowered or "spot" in lowered:
        return "explicit price with currency and unit"
    return "explicit value with as-of date"


def _infer_entity_type(*texts: Any) -> str:
    lowered = " ".join(_clean_text(text).lower() for text in texts if _clean_text(text))
    if not lowered:
        return ""
    if any(
        token in lowered
        for token in ("etf", "fund", "nav", "aum", "expense ratio", "holdings", "ticker")
    ):
        return "fund_or_etf"
    if any(
        token in lowered
        for token in (
            "brent",
            "wti",
            "coal",
            "commodity",
            "spot",
            "settlement",
            "futures",
            "benchmark",
            "index price",
        )
    ):
        return "commodity_or_benchmark"
    if any(
        token in lowered
        for token in (
            "policy",
            "regulator",
            "government",
            "ministry",
            "ndrc",
            "nea",
            "sec",
            "filing",
            "rule",
            "law",
        )
    ):
        return "policy_or_regulatory_source"
    if any(
        token in lowered
        for token in ("company", "stock", "shares", "equity", "market cap", "listed issuer")
    ):
        return "company_or_security"
    return ""


def _infer_metric_kind(*texts: Any) -> str:
    lowered = " ".join(_clean_text(text).lower() for text in texts if _clean_text(text))
    if not lowered:
        return ""
    metric_rules = (
        ("trading_status", ("trading status", "status", "halt", "liquidated", "delisted", "active trading")),
        ("expense_ratio", ("expense ratio", "ter")),
        ("total_net_assets", ("total net assets",)),
        ("aum", ("assets under management", "aum")),
        ("market_cap", ("market cap", "market capitalization")),
        ("nav", ("nav", "net asset value")),
        ("holdings", ("holdings", "top holdings", "constituents", "weights")),
        ("correlation", ("correlation", "pearson", "spearman", "regression")),
        ("inventory", ("inventory", "stocks", "stockpile")),
        ("volume", ("volume", "turnover", "shares/day")),
        ("yield", ("yield", "distribution")),
        ("settlement_price", ("settlement", "close price", "closing price")),
        ("spot_price", ("spot price", "physical price", "fob")),
        ("price", ("price", "quote", "benchmark")),
    )
    for label, cues in metric_rules:
        if any(cue in lowered for cue in cues):
            return label
    return ""


def _infer_series_kind(*texts: Any) -> str:
    lowered = " ".join(_clean_text(text).lower() for text in texts if _clean_text(text))
    if not lowered:
        return ""
    if "futures" in lowered or "front-month" in lowered or "settlement" in lowered:
        return "futures_contract_series"
    if "spot" in lowered or "physical" in lowered or "fob" in lowered:
        return "physical_spot_assessment"
    if "dated brent" in lowered or "benchmark" in lowered or "index" in lowered:
        return "benchmark_series"
    if "nav" in lowered:
        return "fund_nav_series"
    if "aum" in lowered or "market cap" in lowered or "expense ratio" in lowered:
        return "fund_structural_metric"
    return ""


def _infer_comparison_basis(*texts: Any) -> str:
    lowered = " ".join(_clean_text(text).lower() for text in texts if _clean_text(text))
    metric_kind = _infer_metric_kind(*texts)
    series_kind = _infer_series_kind(*texts)
    entity_type = _infer_entity_type(*texts)
    if metric_kind in {"aum", "total_net_assets", "market_cap", "nav"}:
        return (
            "Keep field names explicit; do not compare AUM, total net assets, market cap, "
            "and NAV as if they were the same metric."
        )
    if series_kind in {
        "futures_contract_series",
        "physical_spot_assessment",
        "benchmark_series",
    }:
        return (
            "Keep physical spot, futures, dated benchmarks, and regional grades labeled "
            "separately before reconciling prices."
        )
    if entity_type == "fund_or_etf" and any(token in lowered for token in ("china", "coal")):
        return (
            "Check thesis-to-vehicle fit explicitly; a live fund can still be a weak proxy "
            "for the requested geography, commodity, or instrument."
        )
    return ""


def _infer_source_families(*texts: Any) -> list[str]:
    lowered = " ".join(_clean_text(text).lower() for text in texts if _clean_text(text))
    if not lowered:
        return ["primary source", "authoritative secondary source"]
    if any(
        hint in lowered
        for hint in ("price", "settlement", "spot", "benchmark", "futures", "index")
    ):
        return [
            "official exchange or benchmark",
            "industry benchmark or issuer",
            "authoritative market data",
        ]
    if any(
        hint in lowered
        for hint in ("etf", "fund", "aum", "expense ratio", "holdings", "distribution")
    ):
        return [
            "issuer or exchange",
            "fund fact sheet or prospectus",
            "authoritative market data",
        ]
    if any(
        hint in lowered
        for hint in ("policy", "ndrc", "nea", "regulator", "government", "tariff")
    ):
        return [
            "government or regulator",
            "official release",
            "authoritative secondary source",
        ]
    return ["primary source", "authoritative secondary source"]


def _default_acceptable_proxy(*, as_of: str, claim: str) -> str:
    lowered = _clean_text(claim).lower()
    if any(hint in lowered for hint in ("price", "settlement", "spot", "close", "fixing")):
        if as_of:
            return (
                f"If the exact {as_of} print is not published, accept the nearest official "
                "prior close or the latest explicitly lagged publication within one market "
                "or publication cycle, and label it as a proxy with the lag."
            )
        return (
            "If the exact print is not published, accept the nearest official prior close "
            "or the latest explicitly lagged publication and label it as a proxy."
        )
    return (
        "If the exact fact is not published in that exact form, accept the nearest "
        "canonical issuer/exchange/regulator disclosure or methodology-equivalent series, "
        "and label it as a proxy rather than an exact match."
    )


def _default_stop_condition(*, claim: str, preferred_sites: list[str]) -> str:
    if preferred_sites:
        return (
            "Stop once one preferred-site result gives a date-stamped direct match, or "
            "once two agreeing non-preferred authoritative sources confirm the same fact."
        )
    lowered = _clean_text(claim).lower()
    if any(hint in lowered for hint in ("price", "settlement", "spot", "aum", "expense ratio")):
        return (
            "Stop once you have one date-stamped primary or benchmark value with units, "
            "or two agreeing authoritative secondary sources."
        )
    return (
        "Stop once you have one direct authoritative source, or two agreeing authoritative "
        "secondary sources that answer the claim cleanly."
    )


def _default_not_found_guidance(*, preferred_sites: list[str]) -> str:
    if preferred_sites:
        return (
            "Treat this as not found only after checking the preferred sites, their obvious "
            "aliases, and one broader authoritative fallback search without finding a match."
        )
    return (
        "Treat this as not found only after alias-expanded search still fails to surface "
        "the fact from the expected authoritative source family."
    )


def _default_not_available_guidance() -> str:
    return (
        "Treat this as not available when the likely source family appears to publish only "
        "lagged, aggregated, or differently scoped data, or does not publish this metric in "
        "that exact form at all; document that explicitly instead of silently broadening the claim."
    )


def _verification_fact_is_closed(item: dict[str, Any]) -> bool:
    status = _clean_text(item.get("status")).lower()
    if status == "verified":
        return True
    note = " ".join(
        _clean_text(item.get(key))
        for key in ("note", "fact", "source")
        if _clean_text(item.get(key))
    ).lower()
    return any(
        cue in note
        for cue in (
            "unverifiable",
            "not available",
            "not published",
            "not disclosed",
            "no public series",
            "source inaccessible",
        )
    )


def _resolved_fact_claims(
    previous_report: ResearchConversationReportSummary | None,
    *,
    context: ResearchConversationContext | None = None,
) -> list[str]:
    resolved: list[str] = []
    if context is not None:
        resolved.extend(
            _clean_text(item.get("fact"))
            for item in context.claim_ledger
            if isinstance(item, dict) and _verification_fact_is_closed(item)
        )
    if previous_report is not None:
        resolved.extend(
            _clean_text(item.get("fact"))
            for item in previous_report.verification_facts
            if isinstance(item, dict) and _verification_fact_is_closed(item)
        )
    return _dedupe([claim for claim in resolved if claim])


def _claim_reopened_by_issue(
    *,
    claim: str,
    previous_report: ResearchConversationReportSummary,
) -> bool:
    for row in [*previous_report.audit_issues, *previous_report.contradictions]:
        if isinstance(row, dict):
            if _claims_overlap(claim, row.get("affected_claim") or row.get("issue")):
                return True
            continue
        if _claims_overlap(claim, row):
            return True
    return False


def _matching_subproblem_ids(
    claim: str,
    subproblems: list[ResearchConversationSubproblem],
) -> list[str]:
    matches = [
        _clean_text(item.problem_id)
        for item in subproblems
        if _clean_text(item.problem_id) and _claims_overlap(claim, item.question)
    ]
    return _dedupe(matches)


def _fallback_evidence_target(
    *,
    target_id: str,
    claim: str,
    why_it_matters: str,
    related_subproblem_ids: list[str],
    objective: str,
    context: ResearchConversationContext,
    previous_report: ResearchConversationReportSummary | None = None,
    search_hint: str = "",
    explicit_as_of: str = "",
    source_hint: str = "",
) -> ResearchConversationEvidenceTarget:
    preferred_sites = _extract_preferred_sites(search_hint, source_hint)
    entity_type = _infer_entity_type(claim, why_it_matters, search_hint, source_hint)
    metric_kind = _infer_metric_kind(claim, why_it_matters, search_hint, source_hint)
    series_kind = _infer_series_kind(claim, why_it_matters, search_hint, source_hint)
    return ResearchConversationEvidenceTarget(
        target_id=target_id,
        claim=_clean_text(claim),
        entity_type=entity_type,
        metric_kind=metric_kind,
        series_kind=series_kind,
        comparison_basis=_infer_comparison_basis(
            claim,
            why_it_matters,
            search_hint,
            source_hint,
            entity_type,
            metric_kind,
            series_kind,
        ),
        why_it_matters=_clean_text(why_it_matters)
        or "This fact needs to be pinned down before the final answer is trustworthy.",
        related_subproblem_ids=_normalize_subproblem_dependencies(related_subproblem_ids),
        as_of=_infer_evidence_target_as_of(
            objective=f"{objective} {claim} {why_it_matters}",
            context=context,
            previous_report=previous_report,
            explicit=explicit_as_of,
        ),
        unit_or_format=_infer_evidence_target_unit(claim, why_it_matters, search_hint),
        geography_or_scope=_infer_evidence_target_scope(objective, claim, why_it_matters),
        accepted_source_families=_infer_source_families(
            claim,
            why_it_matters,
            search_hint,
            source_hint,
        ),
        preferred_sites=preferred_sites,
        aliases=_extract_aliases(claim, search_hint, source_hint),
        acceptable_proxy=_default_acceptable_proxy(
            as_of=_infer_evidence_target_as_of(
                objective=f"{objective} {claim} {why_it_matters}",
                context=context,
                previous_report=previous_report,
                explicit=explicit_as_of,
            ),
            claim=claim,
        ),
        stop_condition=_default_stop_condition(
            claim=claim,
            preferred_sites=preferred_sites,
        ),
        not_found_guidance=_default_not_found_guidance(preferred_sites=preferred_sites),
        not_available_guidance=_default_not_available_guidance(),
    )


def _fallback_evidence_targets(
    *,
    objective: str,
    context: ResearchConversationContext,
    subproblems: list[ResearchConversationSubproblem],
    previous_report: ResearchConversationReportSummary | None = None,
) -> list[ResearchConversationEvidenceTarget]:
    targets: list[ResearchConversationEvidenceTarget] = []
    seen_claims: set[str] = set()
    resolved_claims = _resolved_fact_claims(previous_report, context=context)

    def _append(target: ResearchConversationEvidenceTarget) -> None:
        claim = _clean_text(target.claim)
        if not claim or claim in seen_claims:
            return
        seen_claims.add(claim)
        targets.append(target)

    if previous_report is not None:
        for item in previous_report.verification_facts:
            if not isinstance(item, dict):
                continue
            claim = _clean_text(item.get("fact"))
            if not claim or _verification_fact_is_closed(item):
                continue
            if any(
                _claims_overlap(claim, resolved_claim)
                and not _claim_reopened_by_issue(
                    claim=resolved_claim,
                    previous_report=previous_report,
                )
                for resolved_claim in resolved_claims
            ):
                continue
            _append(
                _fallback_evidence_target(
                    target_id=f"target-{len(targets) + 1}",
                    claim=claim,
                    why_it_matters=_clean_text(item.get("note"))
                    or "This fact was not settled strongly enough in the last bounded pass.",
                    related_subproblem_ids=_matching_subproblem_ids(claim, subproblems),
                    objective=objective,
                    context=context,
                    previous_report=previous_report,
                    search_hint=_clean_text(item.get("source")),
                    explicit_as_of=_clean_text(item.get("as_of")),
                    source_hint=_clean_text(item.get("source")),
                )
            )
        for item in previous_report.audit_issues:
            if not isinstance(item, dict):
                continue
            claim = _clean_text(item.get("affected_claim"))
            if not claim or claim in seen_claims:
                continue
            _append(
                _fallback_evidence_target(
                    target_id=f"target-{len(targets) + 1}",
                    claim=claim,
                    why_it_matters=_clean_text(item.get("issue"))
                    or "This claim still has a blocking audit gap.",
                    related_subproblem_ids=_matching_subproblem_ids(claim, subproblems),
                    objective=objective,
                    context=context,
                    previous_report=previous_report,
                    search_hint=_clean_text(item.get("required_follow_up")),
                )
            )

    covered_problem_ids: set[str] = set()
    for target in targets:
        covered_problem_ids.update(_normalize_subproblem_dependencies(target.related_subproblem_ids))

    for index, subproblem in enumerate(subproblems, start=len(targets) + 1):
        problem_id = _clean_text(subproblem.problem_id)
        if problem_id and problem_id in covered_problem_ids:
            continue
        if previous_report is not None and any(
            _claims_overlap(subproblem.question, resolved_claim)
            and not _claim_reopened_by_issue(
                claim=resolved_claim,
                previous_report=previous_report,
            )
            for resolved_claim in resolved_claims
        ):
            continue
        _append(
            _fallback_evidence_target(
                target_id=f"target-{index}",
                claim=subproblem.question,
                why_it_matters=subproblem.why_it_matters,
                related_subproblem_ids=[problem_id] if problem_id else [],
                objective=objective,
                context=context,
                previous_report=previous_report,
                search_hint=subproblem.search_hint or subproblem.evidence_to_seek,
            )
        )
    return targets


def _report_follow_up_subproblems(
    previous_report: ResearchConversationReportSummary | None,
    *,
    context: ResearchConversationContext | None = None,
) -> list[ResearchConversationSubproblem]:
    if previous_report is None:
        return []

    subproblems: list[ResearchConversationSubproblem] = []
    seen_questions: set[str] = set()
    resolved_claims = _resolved_fact_claims(previous_report, context=context)

    def _append(
        *,
        question: str,
        why_it_matters: str,
        evidence_to_seek: str,
        search_hint: str = "",
        affected_claim: str = "",
    ) -> None:
        normalized_question = _clean_text(question)
        if not normalized_question or normalized_question in seen_questions:
            return
        if affected_claim and any(
            _claims_overlap(affected_claim, claim) and not _claim_reopened_by_issue(
                claim=claim,
                previous_report=previous_report,
            )
            for claim in resolved_claims
        ):
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
            affected_claim=claim,
        )
        if len(subproblems) >= 6:
            return subproblems

    for item in previous_report.verification_facts:
        if not isinstance(item, dict):
            continue
        if _verification_fact_is_closed(item):
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
            affected_claim=fact,
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
            affected_claim=normalized,
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
            affected_claim=normalized,
        )
        if len(subproblems) >= 6:
            return subproblems
    return subproblems


def _fallback_workstreams(
    subproblems: list[ResearchConversationSubproblem],
    *,
    planning_mode: Literal["initial", "continuation"],
    start_index: int = 1,
    existing_ids: set[str] | None = None,
) -> list[ResearchConversationWorkstream]:
    workstreams: list[ResearchConversationWorkstream] = []
    taken_ids = set(existing_ids or set())
    next_index = start_index
    aggregation_hint = (
        "Aggregate this stream into one coherent finding or section before final synthesis."
        if planning_mode == "initial"
        else "Use this stream to close one unresolved gap from the previous bounded run."
    )
    for subproblem in subproblems:
        while f"stream-{next_index}" in taken_ids:
            next_index += 1
        stream_id = f"stream-{next_index}"
        taken_ids.add(stream_id)
        next_index += 1
        workstreams.append(
            ResearchConversationWorkstream(
                stream_id=stream_id,
                title=_clean_text(subproblem.question),
                goal=_clean_text(subproblem.question),
                why_it_matters=_clean_text(subproblem.why_it_matters)
                or "This stream resolves one necessary part of the final answer.",
                subproblem_ids=[
                    _clean_text(subproblem.problem_id) or f"problem-{next_index - 1}"
                ],
                aggregation_hint=aggregation_hint,
            )
        )
    return workstreams


def _fallback_intention_plan(
    *,
    objective: str,
    delivery_target: str,
    acceptance_criteria: list[str],
    context: ResearchConversationContext,
    planning_mode: Literal["initial", "continuation"],
    previous_report: ResearchConversationReportSummary | None = None,
) -> ResearchConversationIntentionPlan:
    refined_objective = _clean_text(objective) or "Investigate the user's research request."
    recommended_answer_shape = _infer_delivery_target(
        refined_objective,
        default=delivery_target,
    )
    coverage_priorities = _infer_coverage_priorities(
        refined_objective,
        delivery_target=recommended_answer_shape,
    )
    subproblems = _report_follow_up_subproblems(previous_report, context=context)
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
    workstreams = _fallback_workstreams(
        subproblems,
        planning_mode=planning_mode,
    )
    evidence_targets = _fallback_evidence_targets(
        objective=refined_objective,
        context=context,
        subproblems=subproblems,
        previous_report=previous_report,
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
        recommended_answer_shape=recommended_answer_shape,
        coverage_priorities=coverage_priorities,
        acceptance_criteria=list(acceptance_criteria or context.acceptance_criteria),
        subproblems=subproblems,
        workstreams=workstreams,
        evidence_targets=evidence_targets,
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
    if _allows_best_effort_final_closure(objective, report_summary):
        return ResearchConversationReviewDecision(
            action="done",
            public_response=_done_review_response(objective, report_summary),
        )
    if not _review_requires_more_work(report_summary):
        return ResearchConversationReviewDecision(
            action="done",
            public_response=_done_review_response(objective, report_summary),
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
    recommended_answer_shape = _clean_text(plan.recommended_answer_shape) or _infer_delivery_target(
        refined_objective or answer_goal or objective,
        default=delivery_target,
    )
    coverage_priorities = _dedupe(
        [
            *_normalize_text_items(getattr(plan, "coverage_priorities", [])),
            *_infer_coverage_priorities(
                refined_objective or answer_goal or objective,
                delivery_target=recommended_answer_shape,
            ),
        ]
    )[:5]
    subproblems_by_id = {
        _clean_text(item.problem_id): item
        for item in cleaned_subproblems
        if _clean_text(item.problem_id)
    }
    cleaned_workstreams: list[ResearchConversationWorkstream] = []
    covered_subproblem_ids: set[str] = set()
    seen_workstream_signatures: set[tuple[str, ...]] = set()
    taken_workstream_ids: set[str] = set()
    for index, item in enumerate(plan.workstreams, start=1):
        subproblem_ids = [
            problem_id
            for problem_id in _normalize_subproblem_dependencies(item.subproblem_ids)
            if problem_id in subproblems_by_id
        ]
        if not subproblem_ids:
            continue
        signature = tuple(subproblem_ids)
        if signature in seen_workstream_signatures:
            continue
        seen_workstream_signatures.add(signature)
        referenced = [subproblems_by_id[problem_id] for problem_id in subproblem_ids]
        stream_id = _clean_text(item.stream_id) or f"stream-{index}"
        if stream_id in taken_workstream_ids:
            next_stream_index = len(taken_workstream_ids) + 1
            stream_id = f"stream-{next_stream_index}"
            while stream_id in taken_workstream_ids:
                next_stream_index += 1
                stream_id = f"stream-{next_stream_index}"
        taken_workstream_ids.add(stream_id)
        derived_goal = _clean_text(item.goal) or _clean_text(item.title)
        if not derived_goal:
            derived_goal = _clean_text(
                "; ".join(subproblem.question for subproblem in referenced[:2])
            )
        derived_why = _clean_text(item.why_it_matters)
        if not derived_why:
            derived_why = _clean_text(
                " ".join(
                    subproblem.why_it_matters
                    for subproblem in referenced
                    if _clean_text(subproblem.why_it_matters)
                )
            ) or (
                "This stream groups related subproblems so the evidence can be gathered "
                "in parallel and aggregated cleanly afterward."
            )
        cleaned_workstreams.append(
            ResearchConversationWorkstream(
                stream_id=stream_id,
                title=_clean_text(item.title) or derived_goal,
                goal=derived_goal,
                why_it_matters=derived_why,
                subproblem_ids=subproblem_ids,
                aggregation_hint=_clean_text(item.aggregation_hint)
                or "Aggregate the evidence from this stream into one coherent finding before final synthesis.",
            )
        )
        covered_subproblem_ids.update(subproblem_ids)

    remaining_subproblems = [
        subproblem
        for subproblem in cleaned_subproblems
        if _clean_text(subproblem.problem_id) not in covered_subproblem_ids
    ]
    if not cleaned_workstreams or remaining_subproblems:
        cleaned_workstreams.extend(
            _fallback_workstreams(
                remaining_subproblems if remaining_subproblems else cleaned_subproblems,
                planning_mode=planning_mode,
                start_index=len(cleaned_workstreams) + 1,
                existing_ids=taken_workstream_ids,
            )
        )
    cleaned_evidence_targets: list[ResearchConversationEvidenceTarget] = []
    covered_target_problem_ids: set[str] = set()
    seen_target_claims: set[str] = set()
    for index, item in enumerate(plan.evidence_targets, start=1):
        claim = _clean_text(item.claim)
        if not claim or claim in seen_target_claims:
            continue
        seen_target_claims.add(claim)
        related_subproblem_ids = [
            problem_id
            for problem_id in _normalize_subproblem_dependencies(item.related_subproblem_ids)
            if problem_id in subproblems_by_id
        ]
        if not related_subproblem_ids:
            related_subproblem_ids = _matching_subproblem_ids(claim, cleaned_subproblems)
        preferred_sites = _extract_preferred_sites(*_normalize_text_items(item.preferred_sites))
        aliases = _extract_aliases(*_normalize_text_items(item.aliases), claim)
        entity_type = _clean_text(item.entity_type) or _infer_entity_type(
            claim,
            item.why_it_matters,
        )
        metric_kind = _clean_text(item.metric_kind) or _infer_metric_kind(
            claim,
            item.why_it_matters,
        )
        series_kind = _clean_text(item.series_kind) or _infer_series_kind(
            claim,
            item.why_it_matters,
        )
        cleaned_target = ResearchConversationEvidenceTarget(
            target_id=_clean_text(item.target_id) or f"target-{index}",
            claim=claim,
            entity_type=entity_type,
            metric_kind=metric_kind,
            series_kind=series_kind,
            comparison_basis=_clean_text(item.comparison_basis)
            or _infer_comparison_basis(
                claim,
                item.why_it_matters,
                entity_type,
                metric_kind,
                series_kind,
            ),
            why_it_matters=_clean_text(item.why_it_matters)
            or "This fact needs to be pinned down before the final answer is trustworthy.",
            related_subproblem_ids=related_subproblem_ids,
            as_of=_clean_text(item.as_of)
            or _infer_evidence_target_as_of(
                objective=f"{objective} {claim} {item.why_it_matters}",
                context=context,
                previous_report=previous_report,
            ),
            unit_or_format=_clean_text(item.unit_or_format)
            or _infer_evidence_target_unit(
                claim,
                item.why_it_matters,
            ),
            geography_or_scope=_clean_text(item.geography_or_scope)
            or _infer_evidence_target_scope(objective, claim, item.why_it_matters),
            accepted_source_families=_normalize_text_items(
                item.accepted_source_families
            )
            or _infer_source_families(claim, item.why_it_matters),
            preferred_sites=preferred_sites,
            aliases=aliases,
            acceptable_proxy=_clean_text(item.acceptable_proxy)
            or _default_acceptable_proxy(
                as_of=_clean_text(item.as_of)
                or _infer_evidence_target_as_of(
                    objective=f"{objective} {claim} {item.why_it_matters}",
                    context=context,
                    previous_report=previous_report,
                ),
                claim=claim,
            ),
            stop_condition=_clean_text(item.stop_condition)
            or _default_stop_condition(
                claim=claim,
                preferred_sites=preferred_sites,
            ),
            not_found_guidance=_clean_text(item.not_found_guidance)
            or _default_not_found_guidance(
                preferred_sites=preferred_sites
            ),
            not_available_guidance=_clean_text(item.not_available_guidance)
            or _default_not_available_guidance(),
        )
        cleaned_evidence_targets.append(cleaned_target)
        covered_target_problem_ids.update(cleaned_target.related_subproblem_ids)
    if not cleaned_evidence_targets or any(
        _clean_text(subproblem.problem_id) not in covered_target_problem_ids
        for subproblem in cleaned_subproblems
        if _clean_text(subproblem.problem_id)
    ):
        fallback_targets = _fallback_evidence_targets(
            objective=refined_objective or answer_goal or objective,
            context=context,
            subproblems=cleaned_subproblems,
            previous_report=previous_report,
        )
        for target in fallback_targets:
            claim = _clean_text(target.claim)
            if not claim or claim in seen_target_claims:
                continue
            seen_target_claims.add(claim)
            cleaned_evidence_targets.append(target)
    return ResearchConversationIntentionPlan(
        public_response=_clean_text(plan.public_response),
        answer_goal=answer_goal or _clean_text(objective),
        refined_objective=refined_objective or _clean_text(objective),
        plan_summary=plan_summary,
        recommended_answer_shape=recommended_answer_shape,
        coverage_priorities=coverage_priorities,
        acceptance_criteria=_dedupe(
            [*list(acceptance_criteria), *list(plan.acceptance_criteria)]
        ),
        subproblems=cleaned_subproblems,
        workstreams=cleaned_workstreams,
        evidence_targets=cleaned_evidence_targets,
    )


def _normalize_turn_decision(
    payload: dict[str, Any],
    *,
    user_message: str,
    pending_clarification: str | None,
    context: ResearchConversationContext,
) -> ResearchConversationTurnDecision:
    normalized_payload = dict(payload or {})
    raw_public_response = _clean_block_text(normalized_payload.get("public_response"))
    raw_artifact_markdown = _clean_block_text(normalized_payload.get("artifact_markdown"))
    if raw_artifact_markdown and not _clean_text(normalized_payload.get("response_kind")):
        normalized_payload["response_kind"] = "report_reply"
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
            "response_kind": (
                "clarification"
                if decision.action == "clarify"
                else decision.response_kind
            ),
            "public_response": (
                raw_public_response
                if decision.response_kind == "report_reply"
                else _clean_text(decision.public_response)
            ),
            "clarifying_question": _clean_text(decision.clarifying_question),
            "research_objective": _clean_text(decision.research_objective),
            "acceptance_criteria": _dedupe(list(decision.acceptance_criteria)),
            "delivery_target": _clean_text(decision.delivery_target),
            "artifact_title": _clean_text(decision.artifact_title),
            "artifact_markdown": raw_artifact_markdown,
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
                update={
                    "public_response": decision.clarifying_question,
                    "response_kind": "clarification",
                    "artifact_title": "",
                    "artifact_markdown": "",
                }
            )
        return decision.model_copy(
            update={
                "response_kind": "clarification",
                "artifact_title": "",
                "artifact_markdown": "",
            }
        )
    if decision.action == "respond" and not decision.clarifying_question and _looks_like_user_question(
        decision.public_response
    ):
        return decision.model_copy(
            update={
                "action": "clarify",
                "response_kind": "clarification",
                "clarifying_question": decision.public_response,
                "artifact_title": "",
                "artifact_markdown": "",
            }
        )
    if decision.action == "research":
        research_objective = decision.research_objective or _clean_text(user_message)
        public_response = (
            decision.public_response
            or "I’m starting one bounded research run for this request."
        )
        inferred_delivery_target = _infer_delivery_target(
            research_objective,
            default=decision.delivery_target or _clean_text(context.default_delivery_target),
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
                    or inferred_delivery_target
                    or _clean_text(context.default_delivery_target)
                ),
                "response_kind": "chat_reply",
                "artifact_title": "",
                "artifact_markdown": "",
            }
        )
    artifact_markdown = decision.artifact_markdown
    if decision.response_kind == "report_reply" and not artifact_markdown:
        artifact_markdown = raw_public_response
    return decision.model_copy(
        update={
            "response_kind": (
                "report_reply"
                if decision.response_kind == "report_reply"
                else "chat_reply"
            ),
            "public_response": (
                decision.public_response
                or (
                    artifact_markdown
                    if decision.response_kind == "report_reply"
                    else "I can help with research. Ask me to investigate, compare, verify, or summarize something concrete."
                )
            ),
            "artifact_markdown": artifact_markdown,
        }
    )


def _normalize_review_decision(
    payload: dict[str, Any],
    *,
    objective: str,
    report_summary: ResearchConversationReportSummary,
) -> ResearchConversationReviewDecision:
    requires_more_work = _review_requires_more_work(report_summary)
    allows_best_effort_final_closure = _allows_best_effort_final_closure(
        objective, report_summary
    )
    normalized_payload = dict(payload or {})
    if "action" not in normalized_payload or not _clean_text(
        normalized_payload.get("action")
    ):
        if _clean_text(normalized_payload.get("clarifying_question")):
            normalized_payload["action"] = "clarify"
        elif _clean_text(normalized_payload.get("next_objective")):
            normalized_payload["action"] = (
                "done" if allows_best_effort_final_closure else "continue"
            )
        elif _clean_text(normalized_payload.get("public_response")):
            normalized_payload["action"] = (
                "continue"
                if requires_more_work and not allows_best_effort_final_closure
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
    if (
        requires_more_work
        and decision.action == "done"
        and not allows_best_effort_final_closure
    ):
        return ResearchConversationReviewDecision(
            action="continue",
            public_response=_continue_review_response(report_summary),
            next_objective=_clean_text(objective),
        )
    if (
        requires_more_work
        and decision.action == "continue"
        and allows_best_effort_final_closure
    ):
        return ResearchConversationReviewDecision(
            action="done",
            public_response=_done_review_response(objective, report_summary),
        )
    if not requires_more_work and decision.action == "continue":
        return ResearchConversationReviewDecision(
            action="done",
            public_response=_done_review_response(objective, report_summary),
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
                decision.public_response or _done_review_response(objective, report_summary)
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
        self._completion_adapter = ProviderCompletionAdapter(
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
        self._runner = DurableAgentRunner(
            completion_provider=self._completion_adapter,
            event_callback=event_callback,
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

    def set_event_callback(self, event_callback) -> None:
        self._completion_adapter.set_event_callback(event_callback)
        self._runner.set_event_callback(event_callback)

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
    "ResearchConversationEvidenceTarget",
    "ResearchConversationFacts",
    "ResearchConversationIntentionPlan",
    "ResearchConversationMessage",
    "ResearchConversationReportSummary",
    "ResearchConversationReviewDecision",
    "ResearchConversationSubproblem",
    "ResearchConversationWorkstream",
    "ResearchConversationTurnDecision",
]
