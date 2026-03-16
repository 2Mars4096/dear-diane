from __future__ import annotations

import json as _json
import logging
import os
import re
import math
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
_QUESTION_START_RE = re.compile(
    r"^(?:what|how|why|where|when|which|who|can you|could you|would you|is|are|do|did|does|have you|has|should)\b",
    re.IGNORECASE,
)
_ANAPHORA_RE = re.compile(r"\b(?:it|that|this|them|those|these)\b", re.IGNORECASE)
_PATH_HINT_RE = re.compile(r"(?:~?/|\.{1,2}/|[A-Za-z]:\\)")
_WRITE_INTENT_RE = re.compile(
    r"\b(?:write|draft|create|save|update|edit|rewrite|revise|modify|patch|fix|append)\b",
    re.IGNORECASE,
)
_READ_INTENT_RE = re.compile(
    r"\b(?:read|review|reviewed|inspect|check|open|look(?:\s+into)?|show|summarize|verify)\b",
    re.IGNORECASE,
)
_WEB_INTENT_RE = re.compile(
    r"\b(?:web|online|internet|latest|current|news|recent|search|look up|browse|research)\b",
    re.IGNORECASE,
)
_WORKFLOW_ENTITY_RE = re.compile(
    r"\b(?:workflow|graph|node|edge|port|subgraph)\b",
    re.IGNORECASE,
)
_WORKFLOW_EDIT_RE = re.compile(
    r"\b(?:add|remove|delete|rename|connect|disconnect|move|update|change|modify|edit|fix|patch|rewire)\b",
    re.IGNORECASE,
)
_FURNACE_INTENT_RE = re.compile(
    r"(?:\bfurnace\b|\bdistill(?:ation)?\b|\brecipe\s+session\b|\bpill-\d+\b|\bstart\s+furnace\b|\bstart\s+session\b|蒸馏|熔炉|配方会话|炉子会话|启动会话|启动furnace|开始furnace|开始蒸馏)",
    re.IGNORECASE,
)
_TOPIC_ONLY_ONLINE_RE = re.compile(
    r"\b(?:topic|learn|online|web|internet|research)\b",
    re.IGNORECASE,
)
_TRIAGE_EMBEDDING_ENABLED = os.environ.get("DAN_TRIAGE_EMBEDDING_ENABLED", "1").lower() in (
    "1",
    "true",
    "yes",
)
_TRIAGE_EMBEDDING_MODEL = (
    os.environ.get("DAN_TRIAGE_EMBEDDING_MODEL", "").strip()
    or os.environ.get("DAN_DEFAULT_EMBEDDING_MODEL", "").strip()
    or "text-embedding-3-small"
)
_TRIAGE_EMBEDDING_MIN_CONFIDENCE = float(
    os.environ.get("DAN_TRIAGE_EMBEDDING_MIN_CONFIDENCE", "0.34")
)
_TRIAGE_EMBEDDING_HINT_THRESHOLD = float(
    os.environ.get("DAN_TRIAGE_EMBEDDING_HINT_THRESHOLD", "0.30")
)
_EMBEDDING_PROTOTYPE_CACHE: dict[tuple[str, str], dict[str, list[list[float]]]] = {}

_EMBEDDING_INTENT_PROTOTYPES: dict[str, tuple[str, ...]] = {
    "ask": (
        "explain this",
        "what is the status",
        "read-only answer",
        "请解释一下",
        "请告诉我状态",
        "solo quiero una respuesta",
    ),
    "agent": (
        "do the task and execute steps",
        "read file then update and save",
        "run the session and control execution",
        "请帮我执行任务并处理文件",
        "启动会话并执行",
        "ejecuta la tarea y guarda结果",
    ),
    "plan": (
        "design workflow structure",
        "add node and connect edge",
        "plan automation architecture",
        "设计工作流结构",
        "规划自动化流程",
        "planificar flujo de trabajo",
    ),
}
_EMBEDDING_TARGET_PROTOTYPES: dict[str, tuple[str, ...]] = {
    "run": (
        "start session run control",
        "execute pipeline",
        "resume pause cancel run",
        "启动会话 运行流程",
    ),
    "file": (
        "read local file path",
        "open folder and inspect files",
        "读取本地文件 路径",
    ),
    "web": (
        "search web online research",
        "look up latest internet information",
        "在线搜索 最新信息",
    ),
    "workflow": (
        "edit workflow graph nodes and edges",
        "change automation topology",
        "编辑工作流 节点 边",
    ),
    "general": (
        "general assistant work",
        "multi-step execution task",
        "通用助手任务",
    ),
}
_EMBEDDING_HINT_PROTOTYPES: dict[str, tuple[str, ...]] = {
    "read_file": (
        "read file",
        "inspect local path",
        "查看文件 路径",
    ),
    "search_web": (
        "search online",
        "web research",
        "在线搜索",
    ),
    "write_file": (
        "write file",
        "save output",
        "写入文件 保存",
    ),
    "run_control": (
        "start run",
        "resume pause cancel session",
        "启动 暂停 恢复 会话",
    ),
    "workflow_edit": (
        "edit workflow graph",
        "add remove node edge",
        "编辑工作流 节点 边",
    ),
}
_SOCIAL_TOKENS = frozenset({
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank you",
    "ok",
    "okay",
    "cool",
    "great",
    "sounds good",
    "yes",
    "no",
})
_SOCIAL_REPLIES = {
    "hi": "Hello.",
    "hello": "Hello.",
    "hey": "Hello.",
    "thanks": "You're welcome.",
    "thank you": "You're welcome.",
    "ok": "Got it.",
    "okay": "Got it.",
    "cool": "Got it.",
    "great": "Great.",
    "sounds good": "Sounds good.",
    "yes": "Got it.",
    "no": "Got it.",
}
_FILE_CONTEXT_MARKERS = (
    "file",
    "folder",
    "document",
    "doc",
    "paper",
    "report",
    "draft",
    "log",
    "note",
    "notes",
    "readme",
    ".md",
    ".txt",
    ".tex",
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".pdf",
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


def fast_classify_text(text: str) -> TriageResult | None:
    """Return an immediate tier-0 triage for obvious social turns.

    This intentionally handles only exact low-risk matches so normal turns
    continue through full triage.
    """
    normalized = text.strip().lower()
    if not normalized:
        return None
    normalized = normalized.rstrip("!.?").strip()
    if normalized not in _SOCIAL_TOKENS:
        return None
    response = _SOCIAL_REPLIES.get(normalized, "Got it.")
    return TriageResult(
        tier=0,
        intent="ask",
        confidence=0.99,
        goal=normalized[:200],
        deliverable=response,
        is_social=True,
        social_response=response,
        rationale="Fast lexical social classification",
    )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0 or norm_b <= 0:
        return -1.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _embedding_api_settings() -> tuple[str, str, str]:
    api_key = (
        os.environ.get("DAN_EMBEDDING_API_KEY", "").strip()
        or os.environ.get("DAN_OPENAI_API_KEY", "").strip()
        or os.environ.get("DAN_LLM_API_KEY", "").strip()
    )
    base_url = (
        os.environ.get("DAN_EMBEDDING_BASE_URL", "").strip()
        or os.environ.get("DAN_OPENAI_BASE_URL", "").strip()
        or os.environ.get("DAN_LLM_BASE_URL", "").strip()
        or "https://api.vectorengine.ai/v1"
    )
    model = _TRIAGE_EMBEDDING_MODEL
    return api_key, base_url, model


async def _embed_texts(texts: list[str], *, model: str, api_key: str, base_url: str) -> list[list[float]] | None:
    if not texts:
        return []
    try:
        from openai import AsyncOpenAI
    except Exception:
        logger.debug("Embedding routing unavailable: openai package missing")
        return None
    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        response = await client.embeddings.create(input=texts, model=model)
        return [list(item.embedding) for item in response.data]
    except Exception:
        logger.debug("Embedding routing call failed", exc_info=True)
        return None


async def _prototype_vectors(model: str, api_key: str, base_url: str) -> dict[str, list[list[float]]] | None:
    cache_key = (model, base_url)
    cached = _EMBEDDING_PROTOTYPE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    labels: list[str] = []
    texts: list[str] = []
    for intent, examples in _EMBEDDING_INTENT_PROTOTYPES.items():
        for example in examples:
            labels.append(f"intent:{intent}")
            texts.append(example)
    for target, examples in _EMBEDDING_TARGET_PROTOTYPES.items():
        for example in examples:
            labels.append(f"target:{target}")
            texts.append(example)
    for hint, examples in _EMBEDDING_HINT_PROTOTYPES.items():
        for example in examples:
            labels.append(f"hint:{hint}")
            texts.append(example)

    vectors = await _embed_texts(texts, model=model, api_key=api_key, base_url=base_url)
    if vectors is None or len(vectors) != len(labels):
        return None

    grouped: dict[str, list[list[float]]] = {}
    for label, vector in zip(labels, vectors):
        grouped.setdefault(label, []).append(vector)
    _EMBEDDING_PROTOTYPE_CACHE[cache_key] = grouped
    return grouped


def _best_label_score(query: list[float], grouped: dict[str, list[list[float]]], prefix: str) -> tuple[str, float]:
    best_label = ""
    best_score = -1.0
    for label, vectors in grouped.items():
        if not label.startswith(prefix):
            continue
        local_best = max((_cosine_similarity(query, vec) for vec in vectors), default=-1.0)
        if local_best > best_score:
            best_score = local_best
            best_label = label.split(":", 1)[1]
    return best_label, best_score


async def _embedding_triage_result(text: str, context: ResolvedContext) -> TriageResult | None:
    if not _TRIAGE_EMBEDDING_ENABLED:
        return None
    stripped = text.strip()
    if len(stripped) < 3:
        return None
    if fast_classify_text(stripped) is not None:
        return None

    api_key, base_url, model = _embedding_api_settings()
    if not api_key:
        return None

    query_vectors = await _embed_texts([stripped], model=model, api_key=api_key, base_url=base_url)
    if not query_vectors:
        return None
    grouped = await _prototype_vectors(model, api_key, base_url)
    if not grouped:
        return None

    query = query_vectors[0]
    intent, intent_score = _best_label_score(query, grouped, "intent:")
    if not intent or intent_score < _TRIAGE_EMBEDDING_MIN_CONFIDENCE:
        return None

    target, _target_score = _best_label_score(query, grouped, "target:")
    if not target:
        target = "general"

    action_hints: list[str] = []
    for hint_name in _EMBEDDING_HINT_PROTOTYPES:
        label = f"hint:{hint_name}"
        vectors = grouped.get(label, [])
        score = max((_cosine_similarity(query, vec) for vec in vectors), default=-1.0)
        if score >= _TRIAGE_EMBEDDING_HINT_THRESHOLD and hint_name not in action_hints:
            action_hints.append(hint_name)

    has_path = bool(_PATH_HINT_RE.search(stripped))
    if has_path and "read_file" not in action_hints:
        action_hints.append("read_file")
    if _WEB_INTENT_RE.search(stripped) and "search_web" not in action_hints:
        action_hints.append("search_web")

    if intent == "plan":
        target = "workflow"
        if "workflow_edit" not in action_hints:
            action_hints.append("workflow_edit")
    elif intent == "agent":
        if target == "workflow" and "workflow_edit" not in action_hints:
            action_hints.append("workflow_edit")
        if target == "run" and "run_control" not in action_hints:
            action_hints.append("run_control")
    else:
        action_hints = [hint for hint in action_hints if hint in {"read_file", "search_web", "status_check"}]

    tier = 2 if (
        intent == "plan"
        or "workflow_edit" in action_hints
        or "write_file" in action_hints
        or "run_control" in action_hints
        or ("search_web" in action_hints and intent == "agent")
    ) else 1
    mode = RouteMode(intent)
    route = RouteDecision(
        mode=mode,
        target=target,
        action_hints=action_hints,
        rationale="Embedding triage classifier",
    )
    goal = _fallback_goal(text, context)
    return TriageResult(
        tier=tier,
        intent=intent,
        route=route,
        confidence=max(0.0, min(1.0, intent_score)),
        goal=goal,
        deliverable=goal,
        rationale="Embedding-first routing",
    )


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
    result = _enforce_furnace_route(result, text)
    return result


def _enforce_furnace_route(result: TriageResult, text: str) -> TriageResult:
    if not _FURNACE_INTENT_RE.search(text):
        return result

    has_path = bool(_PATH_HINT_RE.search(text))
    wants_online = bool(_TOPIC_ONLY_ONLINE_RE.search(text))

    route = result.route
    hints: list[str] = []
    if route is not None:
        for hint in route.action_hints:
            hint_text = str(hint).strip()
            if not hint_text:
                continue
            if hint_text in {"write_file", "workflow_edit"}:
                continue
            if hint_text not in hints:
                hints.append(hint_text)

    if "run_control" not in hints:
        hints.append("run_control")
    if has_path and "read_file" not in hints:
        hints.append("read_file")
    if (not has_path or wants_online) and "search_web" not in hints:
        hints.append("search_web")

    forced_route = RouteDecision(
        mode=RouteMode.AGENT,
        target="run",
        action_hints=hints,
        rationale="Furnace/distillation request forced to run-control route",
    )
    return result.model_copy(
        update={
            "tier": max(2, int(result.tier)),
            "intent": "agent",
            "route": forced_route,
        }
    )


def _looks_like_question(text: str) -> bool:
    stripped = text.strip()
    return stripped.endswith("?") or bool(_QUESTION_START_RE.search(stripped))


def _context_text(context: ResolvedContext) -> str:
    parts = [
        context.project.label,
        context.project.summary,
        context.task.label,
        " ".join(context.task.pending_steps),
    ]
    return " ".join(part for part in parts if part).lower()


def _infer_fallback_action_hints(text: str, context: ResolvedContext) -> list[str]:
    lower = text.lower()
    context_text = _context_text(context)
    has_anaphora = bool(_ANAPHORA_RE.search(text))
    file_context_like = (
        bool(_PATH_HINT_RE.search(text))
        or any(marker in lower for marker in _FILE_CONTEXT_MARKERS)
        or (has_anaphora and any(marker in context_text for marker in _FILE_CONTEXT_MARKERS))
    )
    workflow_context_like = bool(_WORKFLOW_ENTITY_RE.search(text)) or (
        has_anaphora and bool(_WORKFLOW_ENTITY_RE.search(context_text))
    )

    hints: list[str] = []
    if workflow_context_like and _WORKFLOW_EDIT_RE.search(text):
        hints.append("workflow_edit")
        return hints

    if _WEB_INTENT_RE.search(text):
        hints.append("search_web")
    if _WRITE_INTENT_RE.search(text) and (file_context_like or has_anaphora or "write" in lower or "draft" in lower):
        hints.append("write_file")
    if _READ_INTENT_RE.search(text) and (file_context_like or has_anaphora):
        hints.append("read_file")
    return hints


def _fallback_goal(text: str, context: ResolvedContext) -> str:
    stripped = text.strip()
    if stripped and _ANAPHORA_RE.search(stripped) and context.task.label:
        return context.task.label[:200]
    return stripped[:200]


def _fallback_triage_result(text: str, context: ResolvedContext) -> TriageResult:
    stripped = text.strip()
    lower = stripped.lower()
    goal = _fallback_goal(text, context)
    social = lower in _SOCIAL_TOKENS
    if social:
        response = "How can I help?"
        if "thank" in lower:
            response = "You're welcome."
        elif lower in {"hi", "hello", "hey"}:
            response = "Hello."
        return TriageResult(
            tier=0,
            intent="ask",
            confidence=0.55,
            goal=goal,
            deliverable=response,
            is_social=True,
            social_response=response,
            rationale="Heuristic fallback for simple social turn",
        )

    action_hints = _infer_fallback_action_hints(text, context)
    route: RouteDecision | None = None
    intent = "ask"
    tier = 1
    confidence = 0.5
    question_like = _looks_like_question(text)
    has_resume_pronoun = bool(_ANAPHORA_RE.search(text)) and not context.is_new_task

    if action_hints:
        intent = "agent"
        confidence = 0.58
        if "workflow_edit" in action_hints:
            target = "workflow"
        elif any(hint in action_hints for hint in ("read_file", "write_file")):
            target = "file"
        elif action_hints == ["search_web"]:
            target = "web"
        else:
            target = "general"
        route = RouteDecision(
            mode=RouteMode.AGENT,
            target=target,
            action_hints=action_hints,
            rationale="Heuristic fallback after triage parse failure",
        )
        if "write_file" in action_hints or "workflow_edit" in action_hints or len(action_hints) > 1:
            tier = 2
    elif not question_like and stripped:
        intent = "agent"
        confidence = 0.52

    return TriageResult(
        tier=tier,
        intent=intent,
        route=route,
        confidence=confidence,
        goal=goal,
        deliverable=goal,
        is_resume=_strongly_suggests_resume(text) or has_resume_pronoun,
        rationale="Heuristic fallback after triage LLM failure or unparseable output",
    )


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
    fast = fast_classify_text(text)
    if fast is not None:
        return _post_process_triage_result(
            fast,
            text,
            context,
            project_store=project_store,
        )

    try:
        embedded = await _embedding_triage_result(text, context)
        if embedded is not None:
            return _post_process_triage_result(
                embedded,
                text,
                context,
                project_store=project_store,
            )
    except Exception:
        logger.debug("Embedding triage stage failed; falling through to LLM triage", exc_info=True)

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

    return _post_process_triage_result(
        _fallback_triage_result(text, context),
        text,
        context,
        project_store=project_store,
    )
