from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_GOAL_REVIEW_STOPWORDS = frozenset({
    "about", "across", "after", "against", "before", "brief", "child", "children",
    "combined", "deliverable", "finish", "from", "goal", "into", "original",
    "parent", "remaining", "result", "results", "review", "session", "task",
    "tasks", "that", "them", "then", "this", "with", "work",
})
_PENDING_CHECKBOX_RE = re.compile(r"(?im)^\s*[-*]?\s*\[\s\]\s+(.+)$")
_OPEN_LOOP_REVIEW_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(?:todo|fixme)\b", re.IGNORECASE),
        "contains TODO-style follow-up markers",
    ),
    (
        re.compile(
            r"\b(?:could not|couldn't|unable to|failed to|blocked by|waiting on)\b",
            re.IGNORECASE,
        ),
        "reports incomplete execution",
    ),
    (
        re.compile(
            r"\b(?:not yet|not implemented|not found|follow[- ]up|remaining|next steps?|pending)\b",
            re.IGNORECASE,
        ),
        "calls out remaining follow-up work",
    ),
)


@dataclass(frozen=True)
class SynthesisGapReview:
    hard_gap_reason: str | None = None
    ambiguous_gap_reason: str | None = None


def planned_subtasks(session: Any) -> list[str]:
    triage = getattr(session, "triage", None)
    if triage is not None and getattr(triage, "subtasks", None):
        return [
            str(task).strip()
            for task in getattr(triage, "subtasks", [])
            if str(task).strip()
        ]
    task_context = getattr(session, "task_context", None)
    if isinstance(task_context, dict):
        return [
            str(task).strip()
            for task in task_context.get("subtasks", [])
            if str(task).strip()
        ]
    return []


def significant_terms(text: str, *, limit: int | None = None) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[a-z0-9][a-z0-9._/-]*", text.lower()):
        token = raw.strip("._/-")
        if len(token) < 4 or token.isdigit() or token in _GOAL_REVIEW_STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        terms.append(token)
        if limit is not None and len(terms) >= limit:
            break
    return terms


def collect_followup_signals(text: str, *, limit: int = 2) -> list[str]:
    if not str(text or "").strip():
        return []
    signals: list[str] = []
    pending_items = [
        item.strip()
        for item in _PENDING_CHECKBOX_RE.findall(text)
        if str(item).strip()
    ]
    if pending_items:
        preview = "; ".join(pending_items[:2])
        signals.append(f"contains pending checklist items ({preview})")
    for pattern, label in _OPEN_LOOP_REVIEW_PATTERNS:
        if pattern.search(text):
            signals.append(label)
        if len(signals) >= limit:
            break
    deduped: list[str] = []
    seen: set[str] = set()
    for signal in signals:
        if signal in seen:
            continue
        seen.add(signal)
        deduped.append(signal)
        if len(deduped) >= limit:
            break
    return deduped


def collect_synthesis_uncertainties(
    child_results: dict[str, Any],
    manager: Any,
) -> list[str]:
    uncertainties: list[str] = []
    for child_id, result in child_results.items():
        child = manager.get(child_id)
        label = getattr(child, "task", child_id)
        content = str(getattr(result, "content", "") or "").strip()
        error = str(getattr(result, "error", "") or "").strip()
        if error:
            uncertainties.append(f"- {label}: {error}")
            continue
        for signal in collect_followup_signals(content, limit=1):
            uncertainties.append(f"- {label}: {signal}")
    return uncertainties


def deterministic_synthesis_gap_review(
    session: Any,
    child_results: dict[str, Any],
    manager: Any,
) -> SynthesisGapReview:
    if not child_results:
        return SynthesisGapReview(hard_gap_reason="no child sessions produced usable output")
    missing_output: list[str] = []
    errored: list[str] = []
    child_labels: list[str] = []
    combined_evidence_parts: list[str] = []
    for child_id, result in child_results.items():
        child = manager.get(child_id)
        label = getattr(child, "task", child_id)
        child_labels.append(label)
        content = str(getattr(result, "content", "") or "").strip()
        error = str(getattr(result, "error", "") or "").strip()
        if error:
            errored.append(f"{label}: {error}")
        elif not content:
            missing_output.append(label)
        else:
            combined_evidence_parts.append(f"{label}\n{content}")
    if errored:
        return SynthesisGapReview(
            hard_gap_reason="one or more child sessions failed: " + "; ".join(errored[:3]),
        )
    if missing_output:
        return SynthesisGapReview(
            hard_gap_reason="one or more child sessions produced no content: "
            + ", ".join(missing_output[:3]),
        )
    expected_subtasks = planned_subtasks(session)
    if expected_subtasks:
        missing_subtasks = [task for task in expected_subtasks if task not in child_labels]
        if missing_subtasks:
            return SynthesisGapReview(
                hard_gap_reason="planned subtasks were not completed: "
                + ", ".join(missing_subtasks[:3]),
            )
    ambiguous_reasons: list[str] = []
    uncertainties = collect_synthesis_uncertainties(child_results, manager)
    if uncertainties:
        trimmed = [item.removeprefix("- ").strip() for item in uncertainties[:3]]
        ambiguous_reasons.append(
            "child results still show unresolved follow-up work: " + "; ".join(trimmed),
        )
    triage = getattr(session, "triage", None)
    goal_text = "\n".join(
        part
        for part in [
            str(getattr(session, "task", "") or "").strip(),
            str(getattr(triage, "goal", "") or "").strip(),
            str(getattr(triage, "deliverable", "") or "").strip(),
        ]
        if part
    )
    goal_terms = significant_terms(goal_text, limit=6)
    if len(goal_terms) < 3:
        return SynthesisGapReview(
            ambiguous_gap_reason="; ".join(ambiguous_reasons[:2]) if ambiguous_reasons else None,
        )
    evidence_terms = set(significant_terms("\n\n".join(combined_evidence_parts)))
    missing_goal_terms = [term for term in goal_terms if term not in evidence_terms]
    if len(missing_goal_terms) >= max(2, len(goal_terms) // 2):
        ambiguous_reasons.append(
            "combined child results do not clearly cover the original goal: keyword coverage is missing for "
            + ", ".join(missing_goal_terms[:3]),
        )
    return SynthesisGapReview(
        ambiguous_gap_reason="; ".join(ambiguous_reasons[:2]) if ambiguous_reasons else None,
    )


def find_synthesis_gap_reason(
    session: Any,
    child_results: dict[str, Any],
    manager: Any,
) -> str | None:
    review = deterministic_synthesis_gap_review(session, child_results, manager)
    return review.hard_gap_reason or review.ambiguous_gap_reason


def parse_synthesis_review_response(content: str) -> tuple[str | None, str]:
    text = str(content or "").strip()
    if not text:
        return None, ""
    candidate = text
    if candidate.startswith("```"):
        first_newline = candidate.find("\n")
        if first_newline != -1:
            candidate = candidate[first_newline + 1 :]
        if candidate.endswith("```"):
            candidate = candidate[:-3]
        candidate = candidate.strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            return None, text
        try:
            payload = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None, text
    if not isinstance(payload, dict):
        return None, text
    decision = str(payload.get("decision", "") or "").strip().lower()
    reason = str(payload.get("reason", "") or "").strip()
    if decision not in {"accept", "remediate"}:
        return None, text
    return decision, reason or text


def extract_json_candidate(text: str) -> Any | None:
    candidate = str(text or "").strip()
    if not candidate:
        return None
    if candidate.startswith("```"):
        first_newline = candidate.find("\n")
        if first_newline != -1:
            candidate = candidate[first_newline + 1 :]
        if candidate.endswith("```"):
            candidate = candidate[:-3]
        candidate = candidate.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start_obj = candidate.find("{")
        end_obj = candidate.rfind("}")
        if start_obj != -1 and end_obj > start_obj:
            try:
                return json.loads(candidate[start_obj : end_obj + 1])
            except json.JSONDecodeError:
                pass
        start_list = candidate.find("[")
        end_list = candidate.rfind("]")
        if start_list != -1 and end_list > start_list:
            try:
                return json.loads(candidate[start_list : end_list + 1])
            except json.JSONDecodeError:
                pass
    return None


def normalize_subtask_items(tasks: list[Any], *, fallback_task: str) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tasks:
        item = str(raw or "").strip()
        if not item:
            continue
        item = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", item).strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
        if len(normalized) >= 8:
            break
    return normalized or ([fallback_task] if fallback_task else [])


def parse_subtask_decomposition_response(
    content: str,
    *,
    fallback_task: str,
) -> list[str] | None:
    payload = extract_json_candidate(content)
    raw_tasks: list[Any] | None = None
    if isinstance(payload, dict):
        candidate = payload.get("subtasks")
        if isinstance(candidate, list):
            raw_tasks = candidate
    elif isinstance(payload, list):
        raw_tasks = payload

    if raw_tasks is None:
        bullets = [
            re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
            for line in str(content or "").splitlines()
            if re.match(r"^\s*(?:[-*]|\d+[.)])\s+", line)
        ]
        if bullets:
            raw_tasks = bullets

    if raw_tasks is None:
        return None
    return normalize_subtask_items(raw_tasks, fallback_task=fallback_task)


__all__ = [
    "SynthesisGapReview",
    "collect_followup_signals",
    "collect_synthesis_uncertainties",
    "deterministic_synthesis_gap_review",
    "extract_json_candidate",
    "find_synthesis_gap_reason",
    "normalize_subtask_items",
    "parse_subtask_decomposition_response",
    "parse_synthesis_review_response",
    "planned_subtasks",
    "significant_terms",
]
