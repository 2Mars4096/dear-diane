from __future__ import annotations

import json as _json
import logging
import re
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any, Callable, Literal

from pydantic import BaseModel, Field

from .intent_catalog import build_classifier_prompt
from .models import ConciergeState, IntentCategory, ResolvedContext, RouteDecision, RouteMode

if TYPE_CHECKING:
    from dan.engine.behavior_store import BehaviorStore
    from .project_store import ProjectStore

logger = logging.getLogger(__name__)

LLMCompleteFunc = Callable[[list[dict[str, str]]], Any]

_VALID_INTENTS = frozenset(("ask", "agent", "plan"))
_VALID_TIERS = frozenset((0, 1, 2))
_VALID_ENTITY_KINDS = frozenset(("project", "task", "workflow", "file", "person"))
_VALID_EXEC_ORDERS = frozenset(("parallel", "serial", "mixed"))
_CONTEXT_NEED_START_RE = re.compile(r"(?i)(?:memory|reuse|file:|domain:)")
_RESUME_CUE_RE = re.compile(
    r"\b(?:resume|continue|keep going|pick (?:it )?back up|pick up where|where were we|left off|go back to|back to)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class EntityRef(BaseModel):
    kind: Literal["project", "task", "workflow", "file", "person"]
    label: str
    id: str | None = None
    confidence: float = 0.8


class TriageResult(BaseModel):
    tier: int = 1
    intent: str = "ask"
    route: RouteDecision | None = None
    confidence: float = 0.8
    goal: str = ""
    deliverable: str = ""
    entities: list[EntityRef] = Field(default_factory=list)
    rationale: str = ""
    is_resume: bool = False
    resume_task_id: str | None = None
    is_social: bool = False
    social_response: str | None = None
    context_needs: list[str] = Field(default_factory=list)
    subtasks: list[str] = Field(default_factory=list)
    execution_order: Literal["parallel", "serial", "mixed"] = "parallel"


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_TRIAGE_SYSTEM_PROMPT: str = (
    "You are DAN's triage classifier. For the latest user message, produce a single JSON object that decides:\n"
    "1. Execution tier (how complex the task is)\n"
    "2. Intent classification (what kind of work)\n"
    "3. Entity extraction\n"
    "4. Resume detection\n"
    "5. Context needs and decomposition hints\n\n"
    + build_classifier_prompt()
    + "\n\n"
    "## Tier assignment\n"
    "- tier 0: greetings, thanks, confirmations (yes/no/ok/number), simple social turns, "
    "one-word acknowledgements. No tool use needed — the concierge can reply instantly.\n"
    "- tier 1: questions with clear answers, status checks, file lookups, experience queries, "
    "simple explanations, quick calculations — answerable in one LLM response without "
    "iterative tool use or multi-step reasoning.\n"
    "- tier 2: research tasks, report writing, code projects, workflow builds, anything "
    "requiring multiple tool calls, web searches + synthesis, iterative refinement, "
    "or producing a substantial artifact.\n\n"
    "## Entity extraction\n"
    'Identify referenced entities as objects: {"kind":"project|task|workflow|file|person", "label":"...", "id":"...", "confidence":0.0-1.0}.\n'
    "Only include entities that are explicitly mentioned or strongly implied.\n\n"
    "## Resume detection\n"
    "Set is_resume=true if the user is clearly continuing or resuming a previous task "
    "(e.g. 'continue', 'go back to the report', 'resume task X'). "
    "Include resume_task_id if identifiable.\n\n"
    "## Social detection\n"
    "Set is_social=true for pure social turns (greetings, thanks, goodbyes). "
    "For social turns, include a brief social_response the concierge can send directly.\n\n"
    "## Context needs\n"
    "List what the executor will need to gather before starting work. Use tags:\n"
    '- "memory" — needs long-term memory lookup\n'
    '- "file:<path>" — needs to read a specific file\n'
    '- "domain:<name>" — needs domain-specific knowledge (e.g. domain:finance)\n'
    '- "reuse" — should check for reusable past work or templates\n'
    "Only include context_needs that are genuinely required.\n\n"
    "## Decomposition (tier 2 only)\n"
    "For tier 2 tasks, provide:\n"
    "- subtasks: ordered list of sub-steps\n"
    '- execution_order: "parallel" if subtasks are independent, "serial" if ordered, "mixed" otherwise\n\n'
    "## Output format\n"
    "Return ONLY a JSON object (no markdown fences, no commentary):\n"
    "{\n"
    '  "tier": 0|1|2,\n'
    '  "intent": "ask"|"agent"|"plan",\n'
    '  "route": {"mode": "ask"|"agent"|"plan", "target": "general|file|web|run|workflow|memory", "action_hints": [...]},\n'
    '  "confidence": 0.0-1.0,\n'
    '  "goal": "one-sentence user goal",\n'
    '  "deliverable": "one-sentence expected outcome",\n'
    '  "entities": [...],\n'
    '  "is_resume": false,\n'
    '  "resume_task_id": null,\n'
    '  "is_social": false,\n'
    '  "social_response": null,\n'
    '  "context_needs": [...],\n'
    '  "subtasks": [],\n'
    '  "execution_order": "parallel",\n'
    '  "rationale": "short explanation"\n'
    "}\n"
)


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------


def _build_triage_messages(
    text: str,
    context: ResolvedContext,
    concierge_state: ConciergeState | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _TRIAGE_SYSTEM_PROMPT},
    ]

    recent = context.task.turns[-4:]
    for turn in recent:
        if turn.role in ("user", "assistant") and turn.content:
            messages.append({"role": turn.role, "content": turn.content[:200]})

    context_parts: list[str] = []
    if context.project.label:
        context_parts.append(f"Active project: {context.project.label}")
    if context.project.summary:
        context_parts.append(f"Project summary: {context.project.summary[:300]}")
    if concierge_state and concierge_state.active_goals:
        goals_summary = "; ".join(
            g.description[:80] for g in concierge_state.active_goals[:5] if g.description
        )
        if goals_summary:
            context_parts.append(f"Active goals: {goals_summary}")
    if context.project.pending_action:
        pa = context.project.pending_action
        context_parts.append(
            f"Pending {pa.kind}: {pa.original_text[:120]}"
        )

    if context_parts:
        messages.append({
            "role": "system",
            "content": "Current context:\n" + "\n".join(context_parts),
        })

    messages.append({"role": "user", "content": text})
    return messages


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _extract_json(raw: str) -> str | None:
    stripped = raw.strip()
    fence = re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    return None


def _parse_route(raw_route: Any) -> RouteDecision | None:
    if not isinstance(raw_route, dict):
        return None
    mode_str = str(raw_route.get("mode", "")).strip().lower()
    if mode_str not in _VALID_INTENTS:
        return None
    target = str(raw_route.get("target", "general")).strip().lower()
    hints = raw_route.get("action_hints", [])
    if isinstance(hints, str):
        hints = [hints]
    elif not isinstance(hints, list):
        hints = []
    return RouteDecision(
        mode=RouteMode(mode_str),
        target=target or "general",
        action_hints=[str(h) for h in hints if h],
        rationale=str(raw_route.get("rationale", "")),
    )


def _parse_entities(raw_list: Any) -> list[EntityRef]:
    if not isinstance(raw_list, list):
        return []
    result: list[EntityRef] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip().lower()
        label = str(item.get("label", "")).strip()
        if kind not in _VALID_ENTITY_KINDS or not label:
            continue
        result.append(EntityRef(
            kind=kind,  # type: ignore[arg-type]
            label=label,
            id=item.get("id"),
            confidence=float(item.get("confidence", 0.8)),
        ))
    return result


def _coerce_string_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _split_context_need_entry(raw: str) -> list[str]:
    text = str(raw).strip()
    if not text:
        return []
    starts: list[int] = []
    for match in _CONTEXT_NEED_START_RE.finditer(text):
        idx = match.start()
        if idx == 0 or text[idx - 1] in " ,;|/\t\r\n":
            starts.append(idx)
    if not starts:
        return [text]
    parts: list[str] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        part = text[start:end].strip(" ,;|/\t\r\n")
        if part:
            parts.append(part)
    return parts


def _normalize_context_need(value: str) -> str | None:
    token = str(value).strip(" ,;|/\t\r\n")
    if not token:
        return None
    lower = token.lower()
    if lower in {"memory", "reuse"}:
        return lower
    if lower.startswith("file:"):
        path = token[5:].strip()
        if not path:
            return None
        return f"file:{path}"
    if lower.startswith("domain:"):
        domain = token[7:].strip(" /")
        if not domain:
            return None
        return f"domain:{domain.lower()}"
    return token


def normalize_context_needs(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        for part in _split_context_need_entry(raw):
            item = _normalize_context_need(part)
            if not item or item in seen:
                continue
            seen.add(item)
            normalized.append(item)
    return normalized


def _normalize_label(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _entity_match_score(label: str, candidate_label: str, candidate_id: str) -> float:
    norm_label = _normalize_label(label)
    if not norm_label:
        return 0.0
    best = 0.0
    for raw in (candidate_label, candidate_id):
        norm_candidate = _normalize_label(raw)
        if not norm_candidate:
            continue
        if norm_label == norm_candidate:
            return 1.0
        if norm_label in norm_candidate or norm_candidate in norm_label:
            best = max(best, 0.92)
            continue
        best = max(best, SequenceMatcher(None, norm_label, norm_candidate).ratio())
    return best


def _iter_known_projects(
    context: ResolvedContext,
    project_store: ProjectStore | None,
) -> list[Any]:
    projects: list[Any] = []
    seen: set[str] = set()

    def _append(project: Any) -> None:
        project_id = str(getattr(project, "project_id", "") or "")
        if not project_id or project_id in seen:
            return
        seen.add(project_id)
        projects.append(project)

    _append(context.project)
    if project_store is None:
        return projects
    try:
        for project in project_store.list_projects(context.project.surface_id):
            _append(project)
    except Exception:
        logger.debug("Project listing failed during triage entity resolution", exc_info=True)
    return projects


def _resolve_entity_id(
    entity: EntityRef,
    context: ResolvedContext,
    project_store: ProjectStore | None,
) -> str | None:
    if entity.id:
        return entity.id
    if entity.kind == "file":
        label = entity.label.strip()
        return label or None
    if entity.kind == "person":
        return None

    candidates: list[tuple[str, str]] = []
    for project in _iter_known_projects(context, project_store):
        if entity.kind == "project":
            candidates.append((project.label, project.project_id))
            continue
        if entity.kind == "task":
            tasks = list(project.tasks)
            if project.project_id == context.project.project_id and all(
                task.task_id != context.task.task_id for task in tasks
            ):
                tasks.append(context.task)
            candidates.extend((task.label, task.task_id) for task in tasks)
            continue
        if entity.kind == "workflow":
            candidates.extend((workflow_id, workflow_id) for workflow_id in project.linked_workflow_ids)

    best_id: str | None = None
    best_score = 0.0
    for candidate_label, candidate_id in candidates:
        score = _entity_match_score(entity.label, candidate_label, candidate_id)
        if score > best_score:
            best_score = score
            best_id = candidate_id
    return best_id if best_score >= 0.72 else None


def _resolve_entities(
    entities: list[EntityRef],
    context: ResolvedContext,
    project_store: ProjectStore | None,
) -> list[EntityRef]:
    resolved: list[EntityRef] = []
    for entity in entities:
        resolved_id = _resolve_entity_id(entity, context, project_store)
        if resolved_id and resolved_id != entity.id:
            resolved.append(entity.model_copy(update={"id": resolved_id}))
        else:
            resolved.append(entity)
    return resolved


def _strongly_suggests_resume(text: str) -> bool:
    return bool(_RESUME_CUE_RE.search(text))


def _post_process_resume(
    result: TriageResult,
    text: str,
    context: ResolvedContext,
    project_store: ProjectStore | None,
) -> None:
    if not (result.is_resume or _strongly_suggests_resume(text)):
        return
    result.is_resume = True
    if result.resume_task_id:
        return

    if context.task.status in {"active", "paused", "blocked"} and not context.is_new_task:
        result.resume_task_id = context.task.task_id


def _post_process_triage_result(
    result: TriageResult,
    text: str,
    context: ResolvedContext,
    *,
    project_store: ProjectStore | None,
) -> TriageResult:
    result.context_needs = normalize_context_needs(result.context_needs)
    result.entities = _resolve_entities(result.entities, context, project_store)
    _post_process_resume(result, text, context, project_store)
    return result


def _parse_triage_response(raw_text: str) -> TriageResult | None:
    json_str = _extract_json(raw_text)
    if not json_str:
        return None
    try:
        obj = _json.loads(json_str)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None

    tier = obj.get("tier")
    if isinstance(tier, str):
        try:
            tier = int(tier)
        except ValueError:
            tier = None
    if tier not in _VALID_TIERS:
        return None

    intent = str(obj.get("intent", "")).strip().lower()
    if intent not in _VALID_INTENTS:
        return None

    confidence = obj.get("confidence", 0.8)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (ValueError, TypeError):
        confidence = 0.8

    exec_order = str(obj.get("execution_order", "parallel")).strip().lower()
    if exec_order not in _VALID_EXEC_ORDERS:
        exec_order = "parallel"

    is_social = bool(obj.get("is_social", False))
    social_response = obj.get("social_response")
    if social_response is not None:
        social_response = str(social_response).strip() or None

    return TriageResult(
        tier=tier,
        intent=intent,
        route=_parse_route(obj.get("route")),
        confidence=confidence,
        goal=str(obj.get("goal", "")).strip(),
        deliverable=str(obj.get("deliverable", "")).strip(),
        entities=_parse_entities(obj.get("entities")),
        rationale=str(obj.get("rationale", "")).strip(),
        is_resume=bool(obj.get("is_resume", False)),
        resume_task_id=obj.get("resume_task_id"),
        is_social=is_social,
        social_response=social_response,
        context_needs=normalize_context_needs(_coerce_string_list(obj.get("context_needs"))),
        subtasks=_coerce_string_list(obj.get("subtasks")),
        execution_order=exec_order,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Main triage entry point
# ---------------------------------------------------------------------------


async def triage(
    text: str,
    context: ResolvedContext,
    llm_complete: LLMCompleteFunc,
    *,
    concierge_state: ConciergeState | None = None,
    behavior_store: BehaviorStore | None = None,
    project_store: ProjectStore | None = None,
) -> TriageResult:
    messages = _build_triage_messages(text, context, concierge_state)
    try:
        result = await llm_complete(messages)
        raw = result if isinstance(result, str) else getattr(result, "text", str(result))
        if raw and raw.strip():
            parsed = _parse_triage_response(raw)
            if parsed is not None:
                return _post_process_triage_result(
                    parsed,
                    text,
                    context,
                    project_store=project_store,
                )
            logger.warning("Triage LLM returned unparseable response: %s", raw[:200])
        else:
            logger.warning("Triage LLM returned empty response")
    except Exception:
        logger.warning("Triage LLM call failed, falling back to safe default", exc_info=True)

    return TriageResult(tier=1, intent="ask", confidence=0.5, goal=text[:200])
