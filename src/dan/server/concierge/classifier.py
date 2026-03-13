from __future__ import annotations

import json as _json
import logging
import math
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

from pydantic import BaseModel

from .context_resolver import ResolvedContext
from .intent_catalog import build_classifier_prompt

if TYPE_CHECKING:
    from dan.engine.behavior_store import BehaviorStore

logger = logging.getLogger(__name__)

LLMCompleteFunc = Callable[[list[dict[str, str]]], Any]


class LLMProvider(Protocol):
    async def complete(self, messages: list[dict], model: str = "", **kwargs: Any) -> str: ...


class IntentCategory(str, Enum):
    FILE_REQUEST = "file_request"
    DIRECT_TASK = "direct_task"
    RUN_CONTROL = "run_control"
    WORKFLOW_BUILD = "workflow_build"
    WORKFLOW_QUERY = "workflow_query"
    EXPERIENCE_QUERY = "experience_query"
    PUBLISH_SHARE = "publish_share"
    STATUS_CHECK = "status_check"
    META_GOAL = "meta_goal"
    CONVERSATION = "conversation"


class ClassificationResult(BaseModel):
    intent: IntentCategory
    confidence: float
    param: str = ""
    raw_text: str
    signals: list[str] = []


@dataclass
class _IntentFeatures:
    text: str
    clean: str
    tokens: set[str]
    recent_file_context: bool
    has_path: bool
    send_request_query: str | None
    direct_web_lookup: bool
    short_draft_request: bool
    format_spec: bool
    multi_action: bool
    creative_artifact: bool
    path_scoped_task: bool
    file_lookup: bool
    read_transform_request: bool
    workflow_nouns: bool
    workflow_edit: bool
    workflow_query: bool
    run_control: bool
    status_check: bool
    experience_query: bool
    publish_share: bool
    meta_goal: bool


@dataclass
class _IntentScoreBoard:
    scores: dict[IntentCategory, float] = field(
        default_factory=lambda: {intent: 0.0 for intent in IntentCategory}
    )
    reasons: dict[IntentCategory, list[str]] = field(
        default_factory=lambda: {intent: [] for intent in IntentCategory}
    )

    def add(self, intent: IntentCategory, weight: float, reason: str) -> None:
        self.scores[intent] += weight
        self.reasons[intent].append(reason)


def register_seed_intents(store: BehaviorStore) -> None:
    """Register core intent categories as seed defaults."""
    core_intents = [e.value for e in IntentCategory]
    store.register_seed("taxonomy/intent_categories", core_intents)


def extract_search_query_from_send_request(text: str) -> str | None:
    lower = text.lower()
    prefixes = (
        "can you send me the ",
        "can you send me ",
        "could you send me the ",
        "could you send me ",
        "would you send me the ",
        "would you send me ",
        "please send me the ",
        "please send me ",
    )
    start_idx = -1
    matched_prefix = ""
    for prefix in prefixes:
        idx = lower.find(prefix)
        if idx >= 0:
            start_idx = idx
            matched_prefix = prefix
            break
    if start_idx < 0:
        return None

    query = text[start_idx + len(matched_prefix):].strip().rstrip(".!?,")
    query_lower = query.lower()
    for marker in (" under ", " from ", " in ", " inside ", " within "):
        idx = query_lower.find(marker)
        if idx >= 0:
            query = query[:idx].strip()
            query_lower = query.lower()
            break
    for suffix in (
        " doc file",
        " document file",
        " doc",
        " document",
        " file",
        " pdf",
        " csv",
    ):
        if query_lower.endswith(suffix):
            query = query[: -len(suffix)].strip()
            query_lower = query.lower()
            break
    query = query.strip("\"' ")
    return query or None


def _normalize_search_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


_INTERNAL_OR_VAGUE_TASK_TERMS = (
    "workflow",
    "graph",
    "node",
    "edge",
    "run",
    "task",
    "project",
    "prompt",
    "dan",
    "blocker",
    "status",
    "progress",
    "failure",
    "failed",
    "error",
    "bug",
    "document",
    "file",
    "pdf",
)


def _looks_like_external_fact_query(clean: str) -> bool:
    if any(term in clean for term in _INTERNAL_OR_VAGUE_TASK_TERMS):
        return False
    if clean in {"what's the latest", "what is the latest", "what's the current", "what is the current"}:
        return False
    return bool(
        re.search(r"^(who is|who's|when is|when was|where is|where was)\b", clean)
        or re.search(r"^(what is|what's) (the )?(current|latest)\s+\w+", clean)
    )


def _looks_like_direct_web_lookup(clean: str) -> bool:
    if any(phrase in clean for phrase in ("stock price", "share price")):
        return True
    if bool(re.search(r"^look up (the )?(stock|share) price\b", clean)):
        return True
    if bool(re.search(r"^(look up|find) (this |that |a |an |some )?(fact|facts|info|information)\b", clean)):
        return True
    return _looks_like_external_fact_query(clean)


_CREATIVE_VERBS_RE = re.compile(
    r"\b(write|create|generate|build|draft|compose|prepare|produce|compile|render|develop|design|make)\b"
)

_OUTPUT_NOUNS_RE = re.compile(
    r"\b(report|paper|document|thesis|manuscript|presentation|analysis|article|essay|brief"
    r"|memo|plan|proposal|guide|manual|chapter|book|newsletter|whitepaper|script|code"
    r"|program|website|app|tool|dashboard|chart|table|diagram|slide|deck|template|outline"
    r"|bibliography|survey|overview|assessment|evaluation|forecast|strategy|framework"
    r"|specification|specification|portfolio|brochure|flyer|infographic|readme)\b"
)

_FORMAT_SPEC_RE = re.compile(
    r"\b(?:in|as|using|into|to)\s+(?:tex|latex|markdown|md|pdf|html|docx|word|powerpoint|pptx|csv|json|beamer)\b"
)

_MULTI_ACTION_RE = re.compile(
    r"\b(?:download|search|scrape|fetch|gather|collect|cite|reference|browse|crawl)\b"
    r".*\b(?:and|then|also)\b"
)

_HELP_ME_TASK_RE = re.compile(
    r"\bhelp\s+me\b.*\b(?:write|create|generate|build|draft|make|prepare|produce|do|finish|complete|start)\b"
)

_FILE_RETRIEVAL_START_RE = re.compile(
    r"^(?:find|locate|search for|look for|where is|send me|get me|open|read)\b"
)


_COMPLEX_TASK_SCOPE_TERMS = (
    "comprehensive",
    "detailed",
    "in-depth",
    "thorough",
    "complete",
    "full ",
    "long-form",
)

_COMPLEX_TASK_RESEARCH_TERMS = (
    "cite",
    "citation",
    "citations",
    "reference",
    "references",
    "bibliography",
    "figure",
    "figures",
    "search online",
    "searching online",
    "download",
    "gather",
    "collect",
    "source",
    "sources",
)


def _looks_like_complex_direct_task(clean: str) -> bool:
    """True for larger research/write tasks that should avoid quick-task routing.

    These requests are still ``DIRECT_TASK`` overall, but they need the fuller
    agent lane instead of the concierge's cheap non-build fast path.
    """
    if not clean:
        return False
    has_path = _has_filesystem_path(clean)
    read_transform_request = bool(re.search(
        r"\b(read|open|load)\b.*\b(and\s+)?(tell|list|what|extract|write|create|save|then|change|update|convert)\b",
        clean,
    ))
    text_sans_paths = re.sub(
        r"(?:~|/)[A-Za-z0-9._~/-]+", " ", clean,
    ).strip()
    has_artifact_request = bool(
        _CREATIVE_VERBS_RE.search(text_sans_paths)
        and (_OUTPUT_NOUNS_RE.search(text_sans_paths) or _FORMAT_SPEC_RE.search(clean))
    )
    if _FILE_RETRIEVAL_START_RE.search(clean) and not read_transform_request and not has_artifact_request:
        return False
    if not has_artifact_request and not read_transform_request:
        return False
    has_scope_cue = any(term in clean for term in _COMPLEX_TASK_SCOPE_TERMS)
    has_research_cue = any(term in clean for term in _COMPLEX_TASK_RESEARCH_TERMS)
    has_format_cue = bool(_FORMAT_SPEC_RE.search(clean))
    has_multi_step_cue = bool(_MULTI_ACTION_RE.search(clean))
    return (
        read_transform_request
        or has_scope_cue
        or has_research_cue
        or has_format_cue
        or has_multi_step_cue
        or (has_path and has_artifact_request)
    )


def _looks_like_path_scoped_task(clean: str, *, has_path: bool) -> bool:
    """Return True when a filesystem path is context for a task, not a file lookup target.

    Catches messages like "help me write a report in /path" or
    "/path — generate a comprehensive analysis in tex" that should
    route to DIRECT_TASK instead of FILE_REQUEST.
    """
    if not has_path:
        return False

    # Strong task signals: override any retrieval-word coincidences
    if _HELP_ME_TASK_RE.search(clean):
        return True
    if _FORMAT_SPEC_RE.search(clean):
        return True
    if _MULTI_ACTION_RE.search(clean):
        return True

    # File-retrieval starts: if the sentence leads with a retrieval verb, it's a lookup
    if _FILE_RETRIEVAL_START_RE.match(clean):
        return False

    # Strip filesystem paths so verb-like words in filenames (e.g. "draft.pdf")
    # don't falsely trigger creative-verb detection.
    text_sans_paths = re.sub(
        r"(?:~|/)[A-Za-z0-9._~/-]+", " ", clean,
    ).strip()

    # Pure file-review actions without any creative verb → file lookup
    # Exception: "read X and [tell me / list / extract / write]" is a multi-step task
    if re.search(
        r"\b(read|open|load)\b.*\b(and\s+)?(tell|list|what|extract|write|create|save|then|change|update|convert)\b",
        clean,
    ):
        return True
    if any(
        phrase in clean
        for phrase in ("summary of", "summarize", "review", "read ", "analyze", "open ")
    ):
        if not _CREATIVE_VERBS_RE.search(text_sans_paths):
            return False

    # Create/write + folder/directory
    if re.search(r"\b(create|make|mkdir|set up)\b.*\b(folder|directory)\b", clean):
        return True
    # Creative verb + output noun (write a report, generate an analysis, etc.)
    if _CREATIVE_VERBS_RE.search(text_sans_paths) and _OUTPUT_NOUNS_RE.search(text_sans_paths):
        return True
    # Creative verb + explicit file type keyword
    if re.search(
        r"\b(create|make|write|save|render|generate|draft|prepare|produce|compile)\b.*"
        r"\b(tex file|latex file|pdf file|markdown file|file)\b",
        clean,
    ):
        return True
    # Starts with create/write/save + path
    if re.search(
        r"^(create|write|save)\s+(?:~|/|[A-Za-z][A-Za-z0-9._-]*/)[A-Za-z0-9._~/-]+",
        clean,
    ):
        return True
    return False


def _score_file_match(query_norm: str, query_tokens: list[str], path: Path) -> float:
    filename_norm = _normalize_search_text(path.name)
    if not filename_norm:
        return -1.0
    coverage = sum(1 for token in query_tokens if token in filename_norm)
    if coverage == 0:
        return -1.0
    ordered = 1.0 if re.search(r".*".join(map(re.escape, query_tokens)), filename_norm) else 0.0
    contains_phrase = 1.0 if query_norm and query_norm in filename_norm else 0.0
    ratio = SequenceMatcher(None, query_norm, filename_norm).ratio() if query_norm else 0.0
    return (coverage * 10.0) + (ordered * 5.0) + (contains_phrase * 5.0) + ratio


def search_local_files(query: str, search_dirs: list[Path], *, limit: int = 10) -> list[str]:
    query_norm = _normalize_search_text(query)
    query_tokens = query_norm.split()
    if not query_norm:
        return []

    candidates: dict[str, tuple[float, str]] = {}

    def _consider(path: Path) -> None:
        if not path.is_file():
            return
        score = _score_file_match(query_norm, query_tokens, path)
        if score <= 0:
            return
        key = str(path)
        current = candidates.get(key)
        if current is None or score > current[0]:
            candidates[key] = (score, path.name.lower())

    exactish_pattern = f"*{'*'.join(query_tokens)}*"
    for d in search_dirs:
        if not d.exists():
            continue
        try:
            for match in d.rglob(exactish_pattern):
                _consider(match)
        except Exception:
            continue

    if not candidates:
        token_patterns = [f"*{token}*" for token in query_tokens[:4]]
        for d in search_dirs:
            if not d.exists():
                continue
            for pattern in token_patterns:
                try:
                    for match in d.rglob(pattern):
                        _consider(match)
                except Exception:
                    continue

    ranked = sorted(candidates.items(), key=lambda item: (-item[1][0], item[1][1], item[0]))
    return [path for path, _meta in ranked[:limit]]


def _has_filesystem_path(text: str) -> bool:
    """Return True if the text contains something that looks like a filesystem path."""
    stripped = re.sub(r"https?://[^\s]+", "", text)
    return bool(re.search(
        r"(?:~|/)[A-Za-z0-9._~/-]+/[A-Za-z0-9._~-]+"
        r"|[A-Za-z][A-Za-z0-9._-]*/[A-Za-z0-9._~-]+/[A-Za-z0-9._~-]+",
        stripped,
    ))


_WORKFLOW_NOUNS = {"workflow", "pipeline", "graph", "node", "edge", "step"}
_WORKFLOW_EDIT_VERBS = {"add", "change", "wire", "modify", "edit", "update", "remove", "delete"}
_WORKFLOW_QUERY_TOKENS = {"show", "list", "inspect", "explain", "what"}
_RUN_CONTROL_TOKENS = {"cancel", "resume", "pause", "stop"}
_STATUS_TOKENS = {"status", "progress", "running"}
_EXPERIENCE_PHRASES = ("have we done", "similar to", "past work", "what did we learn")
_PUBLISH_PHRASES = ("publish", "share", "export", "send to")
_META_GOAL_PHRASES = (
    "build me",
    "create a workflow for",
    "end-to-end",
    "i need a pipeline",
    "i want a system",
    "from scratch",
)
_SHORT_DRAFT_PREFIXES = (
    "draft a short ",
    "draft an email",
    "write a short email",
    "write a short reply",
)


def _extract_intent_features(text: str, context: ResolvedContext) -> _IntentFeatures:
    lower = text.lower().strip()
    clean = lower.rstrip(".!?,")
    tokens = set(re.findall(r"[a-z0-9]+", clean))
    recent_file_context = any(
        (
            turn.intent == IntentCategory.FILE_REQUEST.value
            or ".pdf" in turn.content.lower()
            or "/" in turn.content
            or "document" in turn.content.lower()
            or "file" in turn.content.lower()
        )
        for turn in context.task.turns[-6:]
    )
    has_path = _has_filesystem_path(text)
    send_request_query = extract_search_query_from_send_request(text)
    direct_web_lookup = _looks_like_direct_web_lookup(clean)
    short_draft_request = clean.startswith(_SHORT_DRAFT_PREFIXES)
    format_spec = bool(_FORMAT_SPEC_RE.search(clean))
    multi_action = bool(_MULTI_ACTION_RE.search(clean))
    creative_artifact = bool(
        _CREATIVE_VERBS_RE.search(clean)
        and (_OUTPUT_NOUNS_RE.search(clean) or format_spec)
    )
    read_transform_request = bool(re.search(
        r"\b(read|open|load)\b.*\b(and\s+)?(tell|list|what|extract|write|create|save|then|change|update|convert)\b",
        clean,
    ))
    path_scoped_task = _looks_like_path_scoped_task(clean, has_path=has_path)
    file_lookup = bool(
        has_path
        and _FILE_RETRIEVAL_START_RE.match(clean)
        and not read_transform_request
        and not path_scoped_task
    )
    workflow_nouns = bool(tokens & _WORKFLOW_NOUNS)
    workflow_edit = bool(tokens & _WORKFLOW_EDIT_VERBS) and workflow_nouns
    workflow_query = bool(tokens & _WORKFLOW_QUERY_TOKENS) and workflow_nouns and not workflow_edit
    run_control = bool(tokens & _RUN_CONTROL_TOKENS) and (
        bool(tokens & set(_INTERNAL_STATUS_OBJECTS)) or "run it" in clean
    )
    status_check = (
        any(phrase in clean for phrase in _STATUS_PHRASES)
        or (bool(tokens & _STATUS_TOKENS) and bool(tokens & set(_INTERNAL_STATUS_OBJECTS)))
    )
    experience_query = any(phrase in clean for phrase in _EXPERIENCE_PHRASES)
    publish_share = any(phrase in clean for phrase in _PUBLISH_PHRASES)
    meta_goal = any(phrase in clean for phrase in _META_GOAL_PHRASES)
    return _IntentFeatures(
        text=text,
        clean=clean,
        tokens=tokens,
        recent_file_context=recent_file_context,
        has_path=has_path,
        send_request_query=send_request_query,
        direct_web_lookup=direct_web_lookup,
        short_draft_request=short_draft_request,
        format_spec=format_spec,
        multi_action=multi_action,
        creative_artifact=creative_artifact,
        path_scoped_task=path_scoped_task,
        file_lookup=file_lookup,
        read_transform_request=read_transform_request,
        workflow_nouns=workflow_nouns,
        workflow_edit=workflow_edit,
        workflow_query=workflow_query,
        run_control=run_control,
        status_check=status_check,
        experience_query=experience_query,
        publish_share=publish_share,
        meta_goal=meta_goal,
    )


def _score_intents(features: _IntentFeatures) -> _IntentScoreBoard:
    board = _IntentScoreBoard()

    if features.send_request_query:
        board.add(IntentCategory.FILE_REQUEST, 8.0, "explicit send-me file request")

    if features.status_check:
        board.add(IntentCategory.STATUS_CHECK, 7.0, "status/progress signal")

    if features.run_control:
        board.add(IntentCategory.RUN_CONTROL, 7.0, "run-control signal")

    if features.meta_goal:
        board.add(IntentCategory.META_GOAL, 7.0, "meta-goal phrasing")

    if features.short_draft_request or features.direct_web_lookup:
        board.add(IntentCategory.DIRECT_TASK, 6.0, "direct-task lookup/drafting signal")

    if features.experience_query:
        board.add(IntentCategory.EXPERIENCE_QUERY, 6.0, "experience query signal")

    if features.publish_share:
        board.add(IntentCategory.PUBLISH_SHARE, 5.0, "publish/share signal")

    if features.workflow_query:
        board.add(IntentCategory.WORKFLOW_QUERY, 6.0, "workflow query signal")

    if features.workflow_edit:
        board.add(IntentCategory.WORKFLOW_BUILD, 7.0, "workflow edit signal")

    if features.path_scoped_task:
        board.add(IntentCategory.DIRECT_TASK, 8.0, "path-scoped task signal")

    if features.read_transform_request:
        board.add(IntentCategory.DIRECT_TASK, 7.0, "read-transform-write signal")

    if features.file_lookup:
        board.add(IntentCategory.FILE_REQUEST, 6.0, "path lookup signal")

    if (
        features.recent_file_context
        and any(
            phrase in features.clean
            for phrase in ("summarize it", "review it", "read it", "summarize this", "review this")
        )
    ):
        board.add(IntentCategory.FILE_REQUEST, 5.0, "recent file follow-up signal")

    if features.creative_artifact and not features.workflow_nouns:
        board.add(IntentCategory.DIRECT_TASK, 4.0, "artifact creation signal")

    if features.format_spec and features.creative_artifact:
        board.add(IntentCategory.DIRECT_TASK, 2.0, "explicit output format")

    if features.multi_action and (features.creative_artifact or features.has_path):
        board.add(IntentCategory.DIRECT_TASK, 2.0, "multi-step task signal")

    if features.has_path and not features.path_scoped_task:
        board.add(IntentCategory.FILE_REQUEST, 2.0, "filesystem path fallback")

    if all(score <= 0 for score in board.scores.values()):
        board.add(IntentCategory.CONVERSATION, 1.0, "fallback")

    return board


def _score_to_confidence(top_score: float, second_score: float) -> float:
    """Convert relative score dominance into a confidence value."""
    top = math.exp(min(max(top_score, 0.0), 8.0))
    second = math.exp(min(max(second_score, 0.0), 8.0))
    baseline = 1.0
    return max(0.5, min(0.98, top / (top + second + baseline)))


_STATUS_PHRASES = (
    "what's running", "whats running",
    "what's the status", "whats the status", "what is the status",
    "status update", "status check", "check the status", "check status",
    "show me the status", "give me the status", "give me status",
    "how's it going", "how is it going",
    "how's the run", "how is the run",
    "what's the progress", "whats the progress", "what is the progress",
    "progress update", "progress check",
)

_INTERNAL_STATUS_OBJECTS = (
    "run", "runs", "workflow", "workflows", "task", "tasks",
    "job", "jobs", "session", "sessions", "build", "builds",
    "goal", "goals",
)


def _record_unrecognized_conversation(
    text: str,
    *,
    intent: IntentCategory,
    confidence: float,
    pattern_accumulator: Any = None,
) -> None:
    if (
        pattern_accumulator is None
        or intent != IntentCategory.CONVERSATION
        or confidence > 0.6
    ):
        return
    keywords = [w for w in text.lower().split() if len(w) > 3][:10]
    pattern_accumulator.record_unrecognized(text[:200], keywords, "taxonomy")


def classify_intent(
    text: str,
    context: ResolvedContext,
    pattern_accumulator: Any = None,
) -> ClassificationResult:
    features = _extract_intent_features(text, context)
    board = _score_intents(features)
    ranked = sorted(board.scores.items(), key=lambda item: item[1], reverse=True)
    top_intent, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    confidence = _score_to_confidence(top_score, second_score)
    param = (features.send_request_query or "") if top_intent == IntentCategory.FILE_REQUEST else ""

    _record_unrecognized_conversation(
        text,
        intent=top_intent,
        confidence=confidence,
        pattern_accumulator=pattern_accumulator,
    )

    return ClassificationResult(
        intent=top_intent,
        confidence=confidence,
        param=param,
        raw_text=text,
        signals=board.reasons[top_intent],
    )


# ---------------------------------------------------------------------------
# LLM-based intent classifier (micro-tier model, ~200ms)
# ---------------------------------------------------------------------------

_CLASSIFICATION_SYSTEM_PROMPT = build_classifier_prompt()

_VALID_INTENTS = frozenset(e.value for e in IntentCategory)


def _build_classification_messages(
    text: str,
    context: ResolvedContext,
    *,
    system_prompt: str | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt or _CLASSIFICATION_SYSTEM_PROMPT},
    ]
    recent = context.task.turns[-4:]
    if recent:
        for turn in recent:
            if turn.role in ("user", "assistant") and turn.content:
                snippet = turn.content[:200]
                messages.append({"role": turn.role, "content": snippet})
    messages.append({"role": "user", "content": text})
    return messages


def _resolve_classification_system_prompt(behavior_store: Any = None) -> str:
    if behavior_store is None:
        return _CLASSIFICATION_SYSTEM_PROMPT
    try:
        prompt = behavior_store.get(
            "prompts/classifier.classification_system",
            default=None,
        )
    except Exception:
        prompt = None
    if isinstance(prompt, str) and prompt.strip():
        return prompt
    return _CLASSIFICATION_SYSTEM_PROMPT


def _coerce_confidence(raw_value: Any) -> float | None:
    if isinstance(raw_value, (int, float)):
        value = float(raw_value)
    elif isinstance(raw_value, str):
        try:
            value = float(raw_value.strip())
        except ValueError:
            return None
    else:
        return None
    if value > 1.0 and value <= 100.0:
        value /= 100.0
    return max(0.0, min(1.0, value))


def _parse_llm_classification(raw: str) -> tuple[str | None, float | None, str | None]:
    """Extract intent, confidence, and rationale from JSON or bare text."""
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = _json.loads(raw[start : end + 1])
        except (ValueError, TypeError):
            obj = None
        if isinstance(obj, dict):
            intent = str(obj.get("intent", "")).strip()
            if intent in _VALID_INTENTS:
                reason = (
                    str(
                        obj.get("reason")
                        or obj.get("rationale")
                        or obj.get("explanation")
                        or obj.get("why")
                        or ""
                    ).strip()
                    or None
                )
                return intent, _coerce_confidence(obj.get("confidence")), reason
    lower = raw.lower()
    for intent_val in _VALID_INTENTS:
        if intent_val in lower:
            return intent_val, None, None
    return None, None, None


def _build_llm_classification_result(
    text: str,
    heuristic: ClassificationResult,
    *,
    intent_value: str,
    confidence: float | None,
    reason: str | None,
    pattern_accumulator: Any = None,
) -> ClassificationResult:
    intent = IntentCategory(intent_value)
    resolved_confidence = confidence
    if resolved_confidence is None:
        resolved_confidence = heuristic.confidence if heuristic.intent == intent else 0.72
    if heuristic.intent == intent:
        resolved_confidence = max(resolved_confidence, heuristic.confidence)
    resolved_confidence = max(0.55, min(0.98, resolved_confidence))

    signals: list[str] = []
    if reason:
        signals.append(f"llm: {reason}")
    if heuristic.intent == intent and heuristic.signals:
        signals.extend(f"heuristic: {signal}" for signal in heuristic.signals[:2])
    elif heuristic.signals:
        signals.append(f"heuristic disagreed: {heuristic.intent.value}")

    _record_unrecognized_conversation(
        text,
        intent=intent,
        confidence=resolved_confidence,
        pattern_accumulator=pattern_accumulator,
    )
    return ClassificationResult(
        intent=intent,
        confidence=resolved_confidence,
        param=(extract_search_query_from_send_request(text) or "") if intent == IntentCategory.FILE_REQUEST else "",
        raw_text=text,
        signals=signals,
    )


async def classify_intent_llm(
    text: str,
    context: ResolvedContext,
    llm_complete: LLMCompleteFunc,
    behavior_store: Any = None,
    pattern_accumulator: Any = None,
    on_fallback: Any = None,
) -> ClassificationResult:
    """Primary classifier: use the LLM first, fall back to heuristics on failure."""
    heuristic = classify_intent(text, context, pattern_accumulator=pattern_accumulator)

    try:
        system_prompt = _resolve_classification_system_prompt(behavior_store)
        messages = _build_classification_messages(
            text,
            context,
            system_prompt=system_prompt,
        )
        result = await llm_complete(messages)
        raw_text = result if isinstance(result, str) else getattr(result, "text", str(result))
        if not (raw_text or "").strip():
            logger.warning("LLM classifier returned empty content, falling back to heuristic")
            if on_fallback:
                try:
                    await on_fallback("empty_content")
                except Exception:
                    pass
            return heuristic
        intent_value, llm_confidence, llm_reason = _parse_llm_classification(raw_text)
        if intent_value:
            return _build_llm_classification_result(
                text,
                heuristic,
                intent_value=intent_value,
                confidence=llm_confidence,
                reason=llm_reason,
                pattern_accumulator=pattern_accumulator,
            )
        logger.warning("LLM classifier returned unparseable response: %s", raw_text[:200])
        if on_fallback:
            try:
                await on_fallback("unparseable")
            except Exception:
                pass
    except Exception:
        logger.warning("LLM classifier failed, falling back to heuristic rules", exc_info=True)
        if on_fallback:
            try:
                await on_fallback("exception")
            except Exception:
                pass

    return heuristic


# ---------------------------------------------------------------------------
# Self-adaptive intent discovery (plan 31-22, tasks 7-3 / 7-4 / 7-5)
# ---------------------------------------------------------------------------


def propose_intent_discoveries(
    pattern_accumulator: Any,
    behavior_store: Any = None,
    adaptation_registry: Any = None,
) -> list[dict]:
    """Scan accumulated unrecognized patterns and propose new intent categories.

    Tier behaviour:
      - Tier 0 (pattern_accumulation): just returns discoveries for logging.
      - Tier 1 (intent_discovery_proposal): creates pending AdaptationCandidates.
      - Tier 2 (intent_auto_promotion): creates auto-apply candidates.
    """
    from dan.engine.learning_tiers import is_feature_enabled

    clusters = pattern_accumulator.get_clusters("taxonomy", min_count=5)
    if not clusters:
        return []

    existing_names: set[str] = {e.value for e in IntentCategory}
    if behavior_store is not None:
        stored = behavior_store.get("taxonomy/intent_categories", default=[])
        if isinstance(stored, list):
            existing_names.update(str(v) for v in stored)

    discoveries: list[dict] = []
    for cluster in clusters:
        kws: list[str] = cluster.get("keywords", [])[:2]
        if not kws:
            continue
        proposed_name = "_".join(k.lower() for k in kws)
        if proposed_name in existing_names:
            continue

        examples = cluster.get("examples", [])
        count = cluster.get("count", 0)
        info: dict = {
            "name": proposed_name,
            "keywords": cluster.get("keywords", []),
            "count": count,
            "examples": examples[:5],
        }

        if adaptation_registry is not None and is_feature_enabled("intent_discovery_proposal"):
            from dan.engine.adaptation_registry import AdaptationCandidate

            auto = is_feature_enabled("intent_auto_promotion")
            candidate = AdaptationCandidate(
                source="intent_discovery",
                description=f"New intent '{proposed_name}' from {count} unrecognized messages (e.g. {examples[:2]})",
                parameter_key="taxonomy/intent_categories",
                before_value=str(sorted(existing_names)),
                after_value=str(sorted(existing_names | {proposed_name})),
                evidence=[f"keywords={kws}", f"count={count}", *(ex[:80] for ex in examples[:3])],
                auto_apply=auto,
            )
            adaptation_registry.add(candidate)

        discoveries.append(info)

    return discoveries


def apply_new_intent(
    intent_name: str,
    keywords: list[str],
    behavior_store: Any,
) -> None:
    """Wire a discovered intent into the behavior store's taxonomy and handler map."""
    current = behavior_store.get("taxonomy/intent_categories", default=[])
    if not isinstance(current, list):
        current = []

    if intent_name not in current:
        current.append(intent_name)
        behavior_store.set(
            "taxonomy/intent_categories",
            current,
            reason=f"Added discovered intent '{intent_name}' (keywords: {keywords})",
        )

    handlers: dict = behavior_store.get("taxonomy/intent_handlers", default={})
    if not isinstance(handlers, dict):
        handlers = {}
    handlers[intent_name] = "direct_task"
    behavior_store.set(
        "taxonomy/intent_handlers",
        handlers,
        reason=f"Default handler for new intent '{intent_name}'",
    )


def evolve_classifier_prompt(
    intent_name: str,
    description: str,
    behavior_store: Any,
) -> None:
    """Append a newly discovered intent to the classifier system prompt stored in the behavior store."""
    prompt = behavior_store.get("prompts/classifier.classification_system", default=None)
    if prompt is None:
        logger.debug(
            "Classifier system prompt not externalised to behavior store yet; "
            "skipping prompt co-evolution for intent '%s'",
            intent_name,
        )
        return

    if not isinstance(prompt, str):
        return

    if intent_name in prompt:
        return

    updated = prompt.rstrip() + f"\n- {intent_name}: {description}"
    behavior_store.set(
        "prompts/classifier.classification_system",
        updated,
        reason=f"Added discovered intent '{intent_name}' to classifier prompt",
    )

