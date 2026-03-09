from __future__ import annotations

import re
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from .context_resolver import ResolvedContext


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


def classify_intent(text: str, context: ResolvedContext) -> ClassificationResult:
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

    polite_send_query = extract_search_query_from_send_request(text)
    if polite_send_query:
        return ClassificationResult(
            intent=IntentCategory.FILE_REQUEST,
            confidence=0.95,
            param=polite_send_query,
            raw_text=text,
        )

    if any(
        phrase in clean
        for phrase in ("what's running", "whats running", "status", "progress", "how's it going", "how is it going")
    ):
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

    return ClassificationResult(intent=IntentCategory.CONVERSATION, confidence=0.5, raw_text=text)

