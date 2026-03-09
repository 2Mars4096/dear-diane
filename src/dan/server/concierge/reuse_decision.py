"""Reuse-first build decision (29-4): query memory before generating workflows.

Includes input mapping (§2-2) and intent diff (§3-2) helpers for
the reuse and adapt paths.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from dan.engine.memory_kernel import MemoryType, ScoredMemoryItem


@dataclass
class ReuseCandidate:
    """A workflow asset candidate for reuse or adaptation."""

    workflow_id: str
    content: str
    score: float
    success_rate: float


def reuse_first_decision(
    memory_kernel: Any,
    query: str,
    *,
    reuse_threshold: float = 0.8,
    adapt_threshold: float = 0.4,
    min_success_rate: float = 0.5,
    limit: int = 10,
) -> tuple[Literal["reuse", "adapt", "generate"], ReuseCandidate | None]:
    """Decide REUSE/ADAPT/GENERATE from memory (29-4 §1-1 to §1-4).

    Returns (decision, candidate). Candidate is None for GENERATE.
    """
    if not memory_kernel:
        return "generate", None
    try:
        scored = memory_kernel.retrieve_by_task(
            query,
            task_type="workflow_build",
            limit=limit,
        )
        workflow_assets = [
            si for si in scored
            if si.item.memory_type == MemoryType.WORKFLOW_ASSET
        ]
        if not workflow_assets:
            return "generate", None

        best = workflow_assets[0]
        workflow_id = best.item.metadata.get("workflow_id") or ""
        success_rate = float(best.item.metadata.get("success_rate", 0.5))
        if not workflow_id:
            return "generate", None

        candidate = ReuseCandidate(
            workflow_id=workflow_id,
            content=best.item.content[:300],
            score=best.score,
            success_rate=success_rate,
        )

        if best.score >= reuse_threshold and success_rate >= min_success_rate:
            return "reuse", candidate
        if best.score >= adapt_threshold:
            return "adapt", candidate
        return "generate", None
    except Exception:
        return "generate", None


async def reuse_first_decision_async(
    memory_kernel: Any,
    query: str,
    *,
    reuse_threshold: float = 0.8,
    adapt_threshold: float = 0.4,
    min_success_rate: float = 0.5,
    limit: int = 10,
) -> tuple[Literal["reuse", "adapt", "generate"], ReuseCandidate | None]:
    """Async version of :func:`reuse_first_decision` (29-5 §6-2).

    Runs the memory search in a thread so it can be fanned out alongside
    other preparation tasks (e.g., intent extraction, build session setup).
    """
    return await asyncio.to_thread(
        reuse_first_decision,
        memory_kernel,
        query,
        reuse_threshold=reuse_threshold,
        adapt_threshold=adapt_threshold,
        min_success_rate=min_success_rate,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Input mapping for reuse path (29-4 §2-2)
# ---------------------------------------------------------------------------

_PATH_RE = re.compile(r"(?:[~/.][\w./\\-]+\.\w+|[~/][\w./\\-]{3,})")
_URL_RE = re.compile(r"https?://\S+")
_QUOTED_RE = re.compile(r'"([^"]+)"|\'([^\']+)\'')

_VAR_KEYWORDS: dict[str, list[str]] = {
    "path": ["file", "path", "csv", "data", "input", "source", "document", "pdf", "xlsx"],
    "url": ["url", "link", "endpoint", "api", "website"],
    "output": ["output", "result", "destination", "target", "save"],
    "query": ["query", "search", "question", "prompt", "topic"],
    "name": ["name", "title", "label", "identifier"],
}


def _map_reuse_inputs(
    user_intent: str,
    workflow_variables: list[dict[str, Any]],
) -> dict[str, str]:
    """Map user-described inputs to workflow input variable names (29-4 §2-2).

    Uses simple keyword matching and value extraction from the user's intent
    text. For example, if the user says "my CSV at ~/data.csv" and the
    workflow has variable ``data_path``, maps ``data_path`` to ``~/data.csv``.

    Returns ``{variable_name: suggested_value}``.
    """
    if not workflow_variables or not user_intent:
        return {}

    paths = _PATH_RE.findall(user_intent)
    urls = _URL_RE.findall(user_intent)
    quoted = [m.group(1) or m.group(2) for m in _QUOTED_RE.finditer(user_intent)]

    intent_lower = user_intent.lower()
    mapping: dict[str, str] = {}

    for var in workflow_variables:
        var_name = var.get("name", "") or var.get("variable", "") or ""
        if not var_name:
            continue
        name_lower = var_name.lower().replace("_", " ").replace("-", " ")
        name_parts = set(name_lower.split())

        matched_value: str | None = None

        for category, keywords in _VAR_KEYWORDS.items():
            if not name_parts & set(keywords):
                continue
            if category == "path" and paths:
                matched_value = paths[0]
                break
            if category == "url" and urls:
                matched_value = urls[0]
                break
            if category in ("query", "name", "output") and quoted:
                matched_value = quoted[0]
                break

        if matched_value is None:
            for kw in name_parts:
                if len(kw) < 3:
                    continue
                idx = intent_lower.find(kw)
                if idx == -1:
                    continue
                rest = user_intent[idx + len(kw):].strip()
                tokens = rest.split(None, 3)
                for tok in tokens:
                    tok_stripped = tok.strip(".,;:!?()\"'")
                    if tok_stripped and len(tok_stripped) > 1:
                        matched_value = tok_stripped
                        break
                if matched_value:
                    break

        if matched_value:
            mapping[var_name] = matched_value

    return mapping


# ---------------------------------------------------------------------------
# Intent diff for adapt path (29-4 §3-2)
# ---------------------------------------------------------------------------

def _compute_intent_diff(
    user_intent: str,
    workflow_description: str,
    workflow_nodes: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Identify what needs to change when adapting an existing workflow (29-4 §3-2).

    Compares keywords in the user's intent against what the workflow
    already does (description + node labels/types) and returns:
    ``{"additions": [...], "modifications": [...], "removals": [...]}``.

    This is heuristic-based — no LLM calls.
    """
    additions: list[str] = []
    modifications: list[str] = []
    removals: list[str] = []

    if not user_intent:
        return {"additions": additions, "modifications": modifications, "removals": removals}

    wf_lower = (workflow_description or "").lower()
    node_labels = set()
    node_types = set()
    for node in workflow_nodes:
        label = (node.get("label") or node.get("name") or "").lower()
        ntype = (node.get("type") or node.get("node_type") or "").lower()
        if label:
            node_labels.add(label)
            for w in label.split():
                if len(w) > 3:
                    node_labels.add(w)
        if ntype:
            node_types.add(ntype)

    wf_words = set(wf_lower.split()) | node_labels | node_types

    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "dare", "must",
        "ought", "used", "to", "of", "in", "for", "on", "with", "at", "by",
        "from", "as", "into", "through", "during", "before", "after",
        "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
        "neither", "each", "every", "all", "any", "few", "more", "most",
        "other", "some", "such", "no", "only", "own", "same", "than",
        "too", "very", "just", "because", "about", "that", "this", "it",
        "its", "my", "me", "i", "we", "our", "you", "your", "he", "she",
        "they", "them", "their", "what", "which", "who", "when", "where",
        "why", "how", "if", "then", "else", "also", "like", "want",
    }

    intent_words = set()
    for word in user_intent.lower().split():
        cleaned = word.strip(".,;:!?()\"'")
        if cleaned and len(cleaned) > 2 and cleaned not in stop_words:
            intent_words.add(cleaned)

    for word in intent_words:
        if word not in wf_words:
            close_match = any(
                word in wf_word or wf_word in word
                for wf_word in wf_words if len(wf_word) > 3
            )
            if close_match:
                modifications.append(word)
            else:
                additions.append(word)

    for ntype in node_types:
        if ntype and ntype not in user_intent.lower():
            has_overlap = any(ntype in w or w in ntype for w in intent_words if len(w) > 3)
            if not has_overlap:
                removals.append(ntype)

    return {
        "additions": sorted(additions),
        "modifications": sorted(modifications),
        "removals": sorted(removals),
    }
