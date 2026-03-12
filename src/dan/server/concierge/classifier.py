from __future__ import annotations

import json as _json
import logging
import re
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

from pydantic import BaseModel

from .context_resolver import ResolvedContext

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
    return bool(re.search(
        r"(?:~|/)[A-Za-z0-9._~/-]+/[A-Za-z0-9._~-]+"
        r"|[A-Za-z][A-Za-z0-9._-]*/[A-Za-z0-9._~-]+/[A-Za-z0-9._~-]+",
        text,
    ))


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


def classify_intent(
    text: str,
    context: ResolvedContext,
    pattern_accumulator: Any = None,
) -> ClassificationResult:
    lower = text.lower().strip()
    clean = lower.rstrip(".!?,")
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

    polite_send_query = extract_search_query_from_send_request(text)
    if polite_send_query:
        return ClassificationResult(
            intent=IntentCategory.FILE_REQUEST,
            confidence=0.95,
            param=polite_send_query,
            raw_text=text,
        )

    if any(phrase in clean for phrase in _STATUS_PHRASES):
        return ClassificationResult(intent=IntentCategory.STATUS_CHECK, confidence=0.95, raw_text=text)

    if any(phrase in clean for phrase in ("cancel", "resume", "stop the run", "run it", "pause")):
        return ClassificationResult(intent=IntentCategory.RUN_CONTROL, confidence=0.9, raw_text=text)

    if any(
        phrase in clean
        for phrase in (
            "build me",
            "create a workflow for",
            "end-to-end",
            "i need a pipeline",
            "i want a system",
            "from scratch",
        )
    ):
        return ClassificationResult(intent=IntentCategory.META_GOAL, confidence=0.9, raw_text=text)

    if (
        clean.startswith(("draft a short ", "draft an email", "write a short email", "write a short reply"))
        or _looks_like_direct_web_lookup(clean)
    ):
        return ClassificationResult(intent=IntentCategory.DIRECT_TASK, confidence=0.8, raw_text=text)

    if any(phrase in clean for phrase in ("have we done", "similar to", "past work", "what did we learn")):
        return ClassificationResult(intent=IntentCategory.EXPERIENCE_QUERY, confidence=0.9, raw_text=text)

    if any(phrase in clean for phrase in ("publish", "share", "export", "send to")):
        return ClassificationResult(intent=IntentCategory.PUBLISH_SHARE, confidence=0.85, raw_text=text)

    if any(phrase in clean for phrase in ("show me the workflow", "what does it do", "list workflows")):
        return ClassificationResult(intent=IntentCategory.WORKFLOW_QUERY, confidence=0.85, raw_text=text)

    if any(
        phrase in clean
        for phrase in (
            "add a node",
            "add a step",
            "change the prompt",
            "wire ",
            "modify the workflow",
            "edit the workflow",
        )
    ):
        return ClassificationResult(intent=IntentCategory.WORKFLOW_BUILD, confidence=0.9, raw_text=text)

    if _looks_like_path_scoped_task(clean, has_path=has_path):
        return ClassificationResult(
            intent=IntentCategory.DIRECT_TASK,
            confidence=0.9,
            raw_text=text,
        )

    if has_path:
        return ClassificationResult(
            intent=IntentCategory.FILE_REQUEST,
            confidence=0.65,
            raw_text=text,
        )

    file_prefixes = (
        "send me the ",
        "send me ",
        "send the ",
        "get me the ",
        "get me ",
        "find ",
        "search for ",
        "look for ",
        "locate ",
        "where is ",
        "can you find ",
        "help me find ",
    )
    file_cues = ("the document", "the file", "that file", "that doc")
    has_do_you_have_file_cue = "do you have" in clean and any(
        token in clean for token in ("file", "document", "doc", "pdf", "report", "folder")
    )
    if lower.startswith(file_prefixes) or any(cue in clean for cue in file_cues) or has_do_you_have_file_cue:
        return ClassificationResult(intent=IntentCategory.FILE_REQUEST, confidence=0.85, raw_text=text)
    if (
        any(token in clean for token in ("pdf", "paper", "document", "folder", "directory"))
        and any(phrase in clean for phrase in ("list all", "starting with", "under ", "in that folder", "review", "summarize", "read"))
    ):
        return ClassificationResult(intent=IntentCategory.FILE_REQUEST, confidence=0.8, raw_text=text)
    if recent_file_context and any(phrase in clean for phrase in ("summarize it", "review it", "read it", "summarize this", "review this")):
        return ClassificationResult(intent=IntentCategory.FILE_REQUEST, confidence=0.75, raw_text=text)

    if pattern_accumulator is not None:
        keywords = [w for w in text.lower().split() if len(w) > 3][:10]
        pattern_accumulator.record_unrecognized(text[:200], keywords, "taxonomy")
    return ClassificationResult(intent=IntentCategory.CONVERSATION, confidence=0.5, raw_text=text)


# ---------------------------------------------------------------------------
# LLM-based intent classifier (micro-tier model, ~200ms)
# ---------------------------------------------------------------------------

_CLASSIFICATION_SYSTEM_PROMPT = """\
Classify the user's message into exactly one intent. Respond with ONLY: {"intent":"<name>"}

Intents:
- file_request: Find/open/read/review/summarize a local file or document. Mentions a filesystem path or filename.
- direct_task: Quick factual lookup, web search, drafting a short email or message.
- run_control: Start, stop, cancel, resume, or pause a workflow run.
- status_check: Check status/progress of active DAN runs, tasks, or scheduled jobs. Includes "what's running", "check the run". Does NOT include asking about a project by name — those are handled by project commands.
- workflow_build: Create, modify, or edit a workflow or pipeline.
- workflow_query: List or inspect existing workflows.
- experience_query: Ask about past work, similar projects, or lessons learned.
- publish_share: Publish, share, or export a workflow.
- meta_goal: Build a complex end-to-end automation system or pipeline from scratch.
- conversation: General chat, research questions, brainstorming, or anything else."""

_VALID_INTENTS = frozenset(e.value for e in IntentCategory)


def _build_classification_messages(
    text: str,
    context: ResolvedContext,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _CLASSIFICATION_SYSTEM_PROMPT},
    ]
    recent = context.task.turns[-4:]
    if recent:
        for turn in recent:
            if turn.role in ("user", "assistant") and turn.content:
                snippet = turn.content[:200]
                messages.append({"role": turn.role, "content": snippet})
    messages.append({"role": "user", "content": text})
    return messages


def _parse_llm_intent(raw: str) -> str | None:
    """Extract an intent name from the LLM response (JSON or bare text)."""
    raw = raw.strip()
    for start_char in ("{",):
        idx = raw.find(start_char)
        if idx >= 0:
            end = raw.find("}", idx)
            if end >= 0:
                try:
                    obj = _json.loads(raw[idx : end + 1])
                    intent = obj.get("intent", "")
                    if intent in _VALID_INTENTS:
                        return intent
                except (ValueError, TypeError):
                    pass
    lower = raw.lower()
    for intent_val in _VALID_INTENTS:
        if intent_val in lower:
            return intent_val
    return None


async def classify_intent_llm(
    text: str,
    context: ResolvedContext,
    llm_complete: LLMCompleteFunc,
    behavior_store: Any = None,
    pattern_accumulator: Any = None,
    on_fallback: Any = None,
) -> ClassificationResult:
    """Primary classifier: uses a micro-tier LLM call with keyword fallback.

    Fast-path rules (filesystem paths, slash commands) are checked first.
    If the LLM call fails or returns garbage, falls back to keyword rules.

    When falling back, calls on_fallback(reason) if provided. Reason is
    "unparseable", "empty_content", or "exception".
    """
    fast_path_conf = 0.85
    if behavior_store is not None:
        val = behavior_store.get("heuristics/classifier.fast_path_confidence", default=0.85)
        if isinstance(val, dict):
            fast_path_conf = val.get("value", 0.85)
        elif isinstance(val, (int, float)):
            fast_path_conf = float(val)

    heuristic = classify_intent(text, context, pattern_accumulator=pattern_accumulator)
    if heuristic.confidence >= fast_path_conf or (
        heuristic.intent != IntentCategory.CONVERSATION and heuristic.confidence >= 0.8
    ):
        return heuristic

    try:
        messages = _build_classification_messages(text, context)
        result = await llm_complete(messages)
        raw_text = result if isinstance(result, str) else getattr(result, "text", str(result))
        if not (raw_text or "").strip():
            logger.warning("LLM classifier returned empty content, falling back to heuristic")
            if on_fallback:
                try:
                    await on_fallback("empty_content")
                except Exception:
                    pass
            return classify_intent(text, context, pattern_accumulator=pattern_accumulator)
        intent_value = _parse_llm_intent(raw_text)
        if intent_value:
            return ClassificationResult(
                intent=IntentCategory(intent_value),
                confidence=0.85,
                raw_text=text,
            )
        logger.warning("LLM classifier returned unparseable response: %s", raw_text[:200])
        if on_fallback:
            try:
                await on_fallback("unparseable")
            except Exception:
                pass
    except Exception:
        logger.warning("LLM classifier failed, falling back to keyword rules", exc_info=True)
        if on_fallback:
            try:
                await on_fallback("exception")
            except Exception:
                pass

    return classify_intent(text, context, pattern_accumulator=pattern_accumulator)


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

