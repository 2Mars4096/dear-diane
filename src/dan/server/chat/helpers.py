"""Chat mode detection, tool helpers, and miscellaneous utilities."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Collection

from dan.server.capability_registry import CapabilityResult
from dan.server.search_models import (
    CitationRecord,
    CitationVerification,
    InlineCitation,
    SearchResult,
    canonicalize_search_url,
    parse_search_result_set,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action-hint / tool-routing constants
# ---------------------------------------------------------------------------

_ACTION_HINT_TOOL_MAP: dict[str, frozenset[str]] = {
    "read_file": frozenset({"file_read", "pdf_read", "list_directory"}),
    "search_web": frozenset({"web_search", "web_fetch", "http_request"}),
    "write_file": frozenset({"file_write"}),
    "workflow_edit": frozenset({"plan_graph_mutations", "apply_last_mutation", "delete_graph"}),
    "workflow_run": frozenset({"start_run"}),
    # Run-control turns (e.g. furnace session lifecycle) should execute API control
    # actions instead of ending as narrative prose.
    "run_control": frozenset({"http_request"}),
}

_MISSING_TARGET_PROBE_TOOLS = frozenset({
    "file_read",
    "file_grep",
    "list_directory",
})
_RETRYABLE_CAPABILITY_MAX_RETRIES = 1
_MISSING_TARGET_ERROR_TYPES = frozenset({
    "not_found",
    "file_not_found",
    "directory_not_found",
    "target_not_found",
    "missing_path",
    "missing_file",
    "path_not_found",
})
_MISSING_TARGET_ERROR_MARKERS = (
    "file not found",
    "directory not found",
    "target file missing",
    "target file not found",
    "does not exist",
    "no such file",
    "no such directory",
)

_PARALLEL_TOOL_FAMILY_MAP: dict[str, str] = {
    "file_read": "read",
    "pdf_read": "read",
    "spreadsheet_read": "read",
    "csv_read": "read",
    "list_directory": "read",
    "file_grep": "grep",
    "web_search": "search",
}
_TOOL_HISTORY_COMPAT_MARKERS = (
    "thought_signature",
    "functioncall parts",
    "tool-call transcript",
    "tool call transcript",
)
_APPLY_PREVIEW_CONFIRMATION_RE = re.compile(
    r"\b(?:apply(?:\s+(?:it|that|the\s+preview|the\s+changes))?"
    r"|go\s+ahead"
    r"|looks\s+good"
    r"|approved?"
    r"|ship\s+it"
    r"|proceed"
    r"|do\s+it)\b",
    re.IGNORECASE,
)
_WORKFLOW_DELETE_RE = re.compile(
    r"\b(?:delete|remove)\b(?:(?:\W+\w+){0,6}\W+)?\b(?:workflow|workflows|graph|graphs)\b"
    r"|\b(?:delete|remove)\s+graph\b",
    re.IGNORECASE,
)
_WORKFLOW_STRUCTURE_DELETE_RE = re.compile(
    r"\b(?:node|nodes|edge|edges|port|ports|subgraph|subgraphs|body[_ -]?graph)\b",
    re.IGNORECASE,
)
_WORKFLOW_MODIFICATION_CUE_RE = re.compile(
    r"\b(?:fix|repair|modify|edit|update|change|build|create|add|remove|rewire|replace|review|patch|debug|adjust|refactor)\b",
    re.IGNORECASE,
)

_LIVE_DATA_PHRASES = (
    "what happened",
    "recent",
    "news",
    "milestone",
    "latest",
    "today",
    "this week",
    "this month",
    "current",
    "updates",
    "headlines",
)
_AT_WEB_TOKEN_RE = re.compile(r"(?<!\w)@web(?!\w)", re.IGNORECASE)
_NUMERIC_OR_DATE_RE = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?%?"
    r"|\b(?:19|20)\d{2}\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?",
    re.IGNORECASE,
)
_CLAIM_TERM_STOP_WORDS = frozenset({
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "was",
    "were",
    "with",
})


# ---------------------------------------------------------------------------
# Small standalone helpers
# ---------------------------------------------------------------------------


def _dedupe_action_hints(required_action_hints: list[str] | None) -> list[str]:
    if not required_action_hints:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for hint in required_action_hints:
        if hint in seen:
            continue
        seen.add(hint)
        ordered.append(hint)
    return ordered


def _has_grounded_web_support(
    satisfied_tool_names: set[str],
    tool_results: list[dict[str, Any]] | None = None,
) -> bool:
    if "web_fetch" in satisfied_tool_names or "http_request" in satisfied_tool_names:
        return True
    if not tool_results:
        return False

    for result in tool_results:
        if str(result.get("status") or "success").strip().lower() != "success":
            continue
        if str(result.get("tool_name") or "").strip() != "web_search":
            continue
        cap_result = result.get("cap_result")
        data = getattr(cap_result, "data", None)
        if not isinstance(data, dict):
            continue
        grounded_count = data.get("grounded_result_count")
        if isinstance(grounded_count, int) and grounded_count > 0:
            return True
        search_result_set = parse_search_result_set(data)
        if search_result_set is not None and search_result_set.grounded_result_count > 0:
            return True
    return False


def _missing_action_hints(
    required_action_hints: list[str] | None,
    satisfied_tool_names: set[str],
    *,
    tool_results: list[dict[str, Any]] | None = None,
) -> list[str]:
    missing: list[str] = []
    for hint in _dedupe_action_hints(required_action_hints):
        if hint == "search_web":
            if not _has_grounded_web_support(satisfied_tool_names, tool_results):
                missing.append(hint)
            continue
        tool_names = _ACTION_HINT_TOOL_MAP.get(hint)
        if tool_names and satisfied_tool_names.isdisjoint(tool_names):
            missing.append(hint)
    return missing


def _tool_choice_for_action_hints(
    required_action_hints: list[str] | None,
    satisfied_tool_names: set[str],
    *,
    tool_results: list[dict[str, Any]] | None = None,
    allow_exact_tool_choice: bool = False,
    allow_required_tool_choice: bool = True,
) -> str | dict[str, Any]:
    missing_action_hints = _missing_action_hints(
        required_action_hints,
        satisfied_tool_names,
        tool_results=tool_results,
    )
    if not missing_action_hints:
        return "auto"
    if allow_exact_tool_choice and len(missing_action_hints) == 1:
        tool_names = _ACTION_HINT_TOOL_MAP.get(missing_action_hints[0]) or frozenset()
        if len(tool_names) == 1:
            tool_name = next(iter(tool_names))
            return {
                "type": "function",
                "function": {"name": tool_name},
            }
    if allow_required_tool_choice:
        return "required"
    return "auto"


def _preferred_workflow_edit_tool(
    required_action_hints: list[str] | None,
    user_message: str | None,
    *,
    preview_available: bool,
    allow_plan_graph_mutations: bool,
    allow_apply_last_mutation: bool,
    allow_delete_graph: bool = False,
) -> str | None:
    if "workflow_edit" not in _dedupe_action_hints(required_action_hints):
        return None

    message = str(user_message or "").strip()
    if (
        allow_delete_graph
        and _WORKFLOW_DELETE_RE.search(message)
        and not _WORKFLOW_STRUCTURE_DELETE_RE.search(message)
    ):
        return "delete_graph"

    if (
        preview_available
        and allow_apply_last_mutation
        and _APPLY_PREVIEW_CONFIRMATION_RE.search(message)
        and not _WORKFLOW_MODIFICATION_CUE_RE.search(message)
    ):
        return "apply_last_mutation"

    if allow_plan_graph_mutations:
        return "plan_graph_mutations"
    if preview_available and allow_apply_last_mutation:
        return "apply_last_mutation"
    return None


def _tool_retry_prompt_for_missing_actions(
    missing_action_hints: list[str],
    *,
    available_tool_names: Collection[str] | None = None,
) -> str:
    instructions: list[str] = []
    search_pending = "search_web" in missing_action_hints
    write_pending = "write_file" in missing_action_hints
    read_pending = "read_file" in missing_action_hints
    run_control_pending = "run_control" in missing_action_hints
    workflow_run_pending = "workflow_run" in missing_action_hints
    workflow_edit_pending = "workflow_edit" in missing_action_hints
    handled_read = False
    normalized_available_tools = (
        {
            str(name or "").strip()
            for name in available_tool_names
            if str(name or "").strip()
        }
        if available_tool_names is not None
        else None
    )

    def _has_any(*tool_names: str) -> bool:
        if normalized_available_tools is None:
            return True
        return any(tool_name in normalized_available_tools for tool_name in tool_names)

    if workflow_edit_pending:
        if _has_any("plan_graph_mutations", "apply_last_mutation"):
            instructions.append(
                "You still need to complete workflow editing. If the user is asking "
                "to apply a previously proposed workflow preview from this chat, call "
                "`apply_last_mutation`. Otherwise call `plan_graph_mutations` with the "
                "appropriate operations (add_node, add_edge, expand_pattern, etc.) "
                "to build or update the workflow."
            )
        else:
            instructions.append(
                "Workflow editing is still required, but this turn does not expose a workflow "
                "mutation tool. Do not claim the workflow was updated."
            )

    if search_pending and write_pending:
        if _has_any("web_search", "web_fetch", "http_request") and _has_any("file_write"):
            instructions.append(
                "You still need to gather live web information AND write the "
                "requested output. Do your research FIRST (web_search, then web_fetch "
                "or web_search with fetch_content=true), "
                "then write incrementally:\n"
                "1. First file_write with mode='overwrite' — preamble + first section only.\n"
                "2. Subsequent file_write calls with mode='append' — one section each.\n"
                "3. Final file_write with mode='append' — close the document "
                "(\\end{document} or equivalent).\n"
                "Do NOT rely on search snippets alone, and do NOT write until you have gathered sufficient data."
            )
        else:
            instructions.append(
                "Live web research and writing are still required, but this turn does not expose "
                "the full tool surface needed to finish them. Do not claim completion."
            )
    elif read_pending and write_pending:
        if _has_any("file_read", "pdf_read", "list_directory") and _has_any("file_write"):
            instructions.append(
                "You still need to inspect the referenced local file or folder AND "
                "write the requested output. Read the relevant parts FIRST using "
                "targeted file_read / pdf_read / list_directory calls, then write "
                "incrementally:\n"
                "1. First file_write with mode='overwrite' — preamble + first section only.\n"
                "2. Subsequent file_write calls with mode='append' — one section each.\n"
                "3. Final file_write with mode='append' — close the document "
                "(\\end{document} or equivalent).\n"
                "Do NOT write until you have inspected the necessary local context."
            )
        else:
            instructions.append(
                "Reading local context and writing output are still required, but this turn does not "
                "expose the full tool surface needed to finish them. Do not claim completion."
            )
        handled_read = True
    elif write_pending:
        if _has_any("file_write"):
            instructions.append(
                "CRITICAL: You have not yet written the requested output to disk. "
                "Use file_write NOW — do not research or plan further.\n"
                "Strategy for long documents:\n"
                "1. First call: file_write with mode='overwrite' — write the preamble "
                "and first section only.\n"
                "2. Each subsequent call: file_write with mode='append' — add one "
                "section at a time.\n"
                "3. Final call: file_write with mode='append' — close the document "
                "(\\end{document} or equivalent).\n"
                "Do NOT attempt to write the entire document in a single file_write call."
            )
        else:
            instructions.append(
                "Writing output to disk is still required, but this turn does not expose `file_write`. "
                "Do not claim the file was written."
            )
    elif search_pending:
        if _has_any("web_search", "web_fetch", "http_request"):
            instructions.append(
                "You still need grounded live web information. Use web_search to identify sources, "
                "then web_fetch (or web_search with fetch_content=true) on the most relevant result "
                "before finalizing. Do not rely on snippets alone."
            )
        else:
            instructions.append(
                "Grounded live web research is still required, but this turn does not expose a web "
                "research tool. Do not claim the research is complete."
            )

    if read_pending and not handled_read:
        if _has_any("file_read", "pdf_read", "list_directory"):
            instructions.append(
                "You still need to inspect the referenced local file or folder. "
                "Prefer chunked reads: use file_read or pdf_read with specific "
                "start_line/end_line ranges (or grep to locate sections) instead of "
                "re-reading the whole file."
            )
        else:
            instructions.append(
                "Local file inspection is still required, but this turn does not expose a local-read "
                "tool. Do not claim the file or folder was inspected."
            )
    if workflow_run_pending:
        if _has_any("start_run"):
            instructions.append(
                "You still need to execute the workflow itself. Use `start_run` for the current workflow "
                "(omit `workflow_id` unless you truly need a non-current one). "
                "Do NOT use `http_request` or Furnace endpoints for an ordinary workflow run."
            )
        else:
            instructions.append(
                "Workflow execution is still required, but this turn does not expose a workflow "
                "execution tool. Do not claim the workflow was executed, and do not substitute "
                "other control surfaces, raw HTTP calls, or shell commands."
            )
    if run_control_pending:
        if _has_any("http_request"):
            instructions.append(
                "You still need to perform run control. Use http_request against local "
                "furnace endpoints (for example `/api/furnace/sessions`, then "
                "`/api/furnace/sessions/{id}/sources`, then `/api/furnace/sessions/{id}/start`) "
                "before finalizing. Return session_id and current status in the final answer."
            )
        else:
            instructions.append(
                "Run control is still required, but this turn does not expose the needed session-control "
                "tool. Do not claim Furnace/session control work was completed."
            )
    if not instructions:
        instructions.append("A required capability step is still missing. Use an appropriate tool before finalizing.")
    return " ".join(instructions)


def _tool_schema_name(tool_schema: dict[str, Any]) -> str:
    if not isinstance(tool_schema, dict):
        return ""
    func = tool_schema.get("function")
    if isinstance(func, dict):
        return str(func.get("name") or "")
    return str(tool_schema.get("name") or "")


def _parallel_tool_family(tool_name: str) -> str:
    normalized = str(tool_name or "").strip()
    if not normalized:
        return ""
    if normalized in _PARALLEL_TOOL_FAMILY_MAP:
        return _PARALLEL_TOOL_FAMILY_MAP[normalized]
    if normalized.endswith("_read"):
        return "read"
    if normalized.endswith("_grep") or normalized.startswith("grep_"):
        return "grep"
    if normalized.startswith("search_"):
        return "search"
    return normalized


def _force_single_tool_request(
    all_tools: list[dict[str, Any]],
    tool_name: str,
    *,
    allow_exact_tool_choice: bool,
    allow_required_tool_choice: bool = True,
) -> tuple[list[dict[str, Any]], str | dict[str, Any]]:
    filtered_tools = [tool for tool in all_tools if _tool_schema_name(tool) == tool_name]
    if not filtered_tools:
        filtered_tools = all_tools
    if allow_exact_tool_choice:
        return filtered_tools, {
            "type": "function",
            "function": {"name": tool_name},
        }
    if allow_required_tool_choice:
        return filtered_tools, "required"
    return filtered_tools, "auto"


def _looks_like_missing_target_error(
    tool_name: str,
    cap_result: CapabilityResult,
) -> bool:
    if tool_name not in _MISSING_TARGET_PROBE_TOOLS:
        return False
    error_type = str(getattr(cap_result, "error_type", "") or "").strip().lower()
    if error_type in _MISSING_TARGET_ERROR_TYPES:
        return True
    message = str(getattr(cap_result, "message", "") or "").lower()
    return any(marker in message for marker in _MISSING_TARGET_ERROR_MARKERS)


def _write_file_escalation_prompt(*, missing_target: bool) -> str:
    parts: list[str] = []
    if missing_target:
        parts.append(
            "The target file/path does not exist yet. "
            "Do not retry more read-only tools."
        )
    parts.append(
        "Call file_write NOW. Write ONLY the preamble and first section "
        "(no more than ~2000 characters). Use mode='overwrite'. "
        "You will add remaining sections one at a time with mode='append' "
        "in subsequent turns. Do NOT attempt to write the entire document "
        "in a single file_write call — it will time out."
    )
    return " ".join(parts)


def _llm_error_status_code(exc: Exception) -> int | None:
    """Best-effort HTTP status extraction from provider exceptions."""
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        try:
            status = int(candidate)
        except (TypeError, ValueError):
            continue
        if status > 0:
            return status
    return None


def _is_tool_choice_incompatible_error(exc: Exception) -> bool:
    """Return whether the provider rejected explicit tool_choice settings."""
    message = str(exc).lower()
    return "tool_choice" in message and "thinking enabled" in message


def _parse_retry_after_seconds(
    value: Any,
    *,
    now: datetime | None = None,
) -> float | None:
    """Parse a retry-after hint expressed as seconds or an HTTP date."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        delay = float(value)
        return max(delay, 0.0)

    raw_value = str(value).strip()
    if not raw_value:
        return None
    try:
        delay = float(raw_value)
        return max(delay, 0.0)
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(raw_value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    return max((retry_at - current_time).total_seconds(), 0.0)


def _header_value(headers: Any, name: str) -> Any | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value is None:
            value = getter(name.lower())
        if value is not None:
            return value
    items = getattr(headers, "items", None)
    if callable(items):
        for key, value in items():
            if str(key).lower() == name.lower():
                return value
    return None


def _extract_retry_after_seconds(
    exc: Exception,
    *,
    now: datetime | None = None,
) -> float | None:
    """Best-effort retry-after extraction from provider exception metadata."""
    for attr_name in ("retry_after", "retry_after_seconds", "retryAfter"):
        parsed = _parse_retry_after_seconds(getattr(exc, attr_name, None), now=now)
        if parsed is not None:
            return parsed

    response = getattr(exc, "response", None)
    for headers in (
        getattr(exc, "headers", None),
        getattr(response, "headers", None),
    ):
        retry_after = _parse_retry_after_seconds(
            _header_value(headers, "Retry-After"),
            now=now,
        )
        if retry_after is not None:
            return retry_after

        retry_after_ms = _parse_retry_after_seconds(
            _header_value(headers, "Retry-After-Ms"),
            now=now,
        )
        if retry_after_ms is not None:
            return max(retry_after_ms / 1000.0, 0.0)

    return None


def _post_tool_followup_retry_delay_seconds(
    exc: Exception,
    retry_index: int,
    *,
    base_delay_seconds: float = 2.0,
    max_delay_seconds: float = 8.0,
    now: datetime | None = None,
) -> float:
    """Return the bounded delay before a post-tool follow-up retry."""
    retry_after = _extract_retry_after_seconds(exc, now=now)
    if retry_after is not None:
        return min(max(retry_after, 0.0), max_delay_seconds)

    safe_retry_index = max(retry_index, 1)
    safe_base_delay = max(base_delay_seconds, 0.0)
    safe_max_delay = max(max_delay_seconds, safe_base_delay)
    return min(safe_base_delay * (2 ** (safe_retry_index - 1)), safe_max_delay)


def _is_transient_llm_error(exc: Exception) -> bool:
    """True if the exception is a transient LLM API error worth retrying."""
    kind = _classify_llm_error_kind(exc)
    if kind == "tool_history_incompatible":
        return False
    if kind in {"timeout", "rate_limit", "server_error", "connection"}:
        return True
    exc_cls = type(exc).__name__
    if exc_cls in ("RateLimitError", "APITimeoutError", "TimeoutError", "ConnectError"):
        return True
    return False


def _classify_llm_error_kind(exc: Exception) -> str:
    """Return a coarse error class for retry and recovery decisions."""
    msg = str(exc).lower()
    if any(marker in msg for marker in _TOOL_HISTORY_COMPAT_MARKERS):
        return "tool_history_incompatible"
    status_code = _llm_error_status_code(exc)
    if status_code in {408, 504}:
        return "timeout"
    if status_code == 429:
        return "rate_limit"
    if status_code is not None and status_code >= 500:
        return "server_error"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "429" in msg or "rate" in msg or "rate limit" in msg:
        return "rate_limit"
    if "500" in msg or "502" in msg or "503" in msg or "internal server" in msg:
        return "server_error"
    if "connection" in msg or "connect" in msg or "network" in msg:
        return "connection"
    return "other"


def _build_tool_followup_recovery_prompt(exc: Exception) -> str:
    """Prompt for no-tools synthesis after a post-tool follow-up failure."""
    kind = _classify_llm_error_kind(exc)
    if kind == "timeout":
        reason = (
            "The previous attempt to generate the final answer from the completed "
            "tool results timed out."
        )
    elif kind == "tool_history_incompatible":
        reason = (
            "The previous attempt to generate the final answer from the completed "
            "tool results failed because the provider "
            "rejected the tool-call transcript metadata."
        )
    elif kind == "rate_limit":
        reason = (
            "The previous attempt to generate the final answer from the completed "
            "tool results hit a rate or quota limit."
        )
    elif kind == "connection":
        reason = (
            "The previous attempt to generate the final answer from the completed "
            "tool results failed because of a connection problem."
        )
    elif kind == "server_error":
        reason = (
            "The previous attempt to generate the final answer from the completed "
            "tool results failed because the provider returned a server error."
        )
    else:
        reason = (
            "The previous attempt to generate the final answer from the completed "
            f"tool results failed with {type(exc).__name__}."
        )
    return (
        f"{reason} Using ONLY the completed tool results already in this conversation, "
        "write the final answer for the user. Do not call any tools or ask to rerun them."
    )


def _build_tool_followup_error_intro(exc: Exception) -> str:
    """User-facing summary for a post-tool follow-up failure."""
    kind = _classify_llm_error_kind(exc)
    if kind == "timeout":
        return "The model timed out while generating the final answer from completed tool results."
    if kind == "tool_history_incompatible":
        return "The provider rejected the tool-call transcript while generating the final answer from completed tool results."
    if kind == "rate_limit":
        return "The provider hit a rate or quota limit while generating the final answer from completed tool results."
    if kind == "connection":
        return "A connection issue interrupted final answer generation after the tool results were ready."
    if kind == "server_error":
        return "The provider returned a server error while generating the final answer from completed tool results."
    return "I hit an error while generating the final answer from completed tool results."


# ---------------------------------------------------------------------------
# Tool result cleaning — strip markup boilerplate before feeding back to LLM
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<(script|style|nav|footer|header|noscript)\b[^>]*>[\s\S]*?</\1>", re.IGNORECASE)
_HTML_ALL_TAGS_RE = re.compile(r"<[^>]+>")
_LATEX_PREAMBLE_RE = re.compile(
    r"^.*?\\begin\{document\}", re.DOTALL,
)
_LATEX_BOILERPLATE_RE = re.compile(
    r"\\(?:documentclass|usepackage|newcommand|renewcommand|setlength|pagestyle"
    r"|geometry|fancyhf|bibliographystyle)\b[^\n]*\n?",
)
_REPEATED_BLANK_LINES_RE = re.compile(r"\n{3,}")
_DENSE_NAV_LINKS_RE = re.compile(
    r"(?:^[ \t]*\[[^\]]{1,60}\]\(https?://[^)]+\)\s*){4,}",
    re.MULTILINE,
)
_IMG_MARKDOWN_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_BARE_IMG_URL_RE = re.compile(
    r"^\s*\(?https?://[^\s)]+\.(?:png|jpg|jpeg|gif|webp|svg|ico)\b[^\s)]*\)?\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def _clean_tool_result(tool_name: str, content: str, limit: int = 4000) -> str:
    """Clean tool output before feeding it back to the LLM as context.

    Strips markup boilerplate (HTML tags, LaTeX preambles, image links,
    navigation blocks) that burn tokens without adding semantic value.
    Data, quotes, and meaningful text are preserved verbatim.
    """
    if not content:
        return content

    if tool_name in ("web_fetch", "web_search"):
        content = _HTML_TAG_RE.sub("", content)
        content = _HTML_ALL_TAGS_RE.sub("", content)
        content = _IMG_MARKDOWN_RE.sub("", content)
        content = _BARE_IMG_URL_RE.sub("", content)
        content = _DENSE_NAV_LINKS_RE.sub("[...navigation removed...]", content)

    if tool_name in ("file_read", "file_grep"):
        if "\\documentclass" in content or "\\begin{document}" in content:
            m = _LATEX_PREAMBLE_RE.search(content)
            if m:
                content = content[m.end():]
            content = _LATEX_BOILERPLATE_RE.sub("", content)
            content = content.replace("\\end{document}", "")

        if content.lstrip().startswith(("<!DOCTYPE", "<html", "<?xml")):
            content = _HTML_TAG_RE.sub("", content)
            content = _HTML_ALL_TAGS_RE.sub("", content)

    content = _REPEATED_BLANK_LINES_RE.sub("\n\n", content)
    return content.strip()[:limit]


def _summarize_tool_result(
    tool_name: str,
    args: dict[str, Any],
    message: str,
    success: bool,
    result_data: Any | None = None,
) -> str:
    """Return a short one-line summary of a tool result for fallback display.

    Used instead of raw tool output in ``combined_text_parts`` so that
    timeout / error fallback paths never dump raw web content to the user.
    """
    status = "" if success else " [failed]"
    if tool_name in ("web_search", "search_web"):
        query = args.get("query", "").strip()[:60]
        count = message.count("\n\n") + 1 if message.strip() else 0
        return f"Searched web for \"{query}\" ({count} results){status}"
    if tool_name in ("web_fetch", "fetch_url", "open_url"):
        url = args.get("url", "").strip()[:80]
        size = f"{len(message):,}" if message else "0"
        return f"Fetched {url} ({size} chars){status}"
    if tool_name in ("file_read", "read_file", "pdf_read"):
        path = args.get("path") or args.get("file_path") or args.get("filepath") or ""
        if isinstance(path, str):
            path = path.rsplit("/", 1)[-1][:40]
        size = f"{len(message):,}" if message else "0"
        return f"Read {path} ({size} chars){status}"
    if tool_name in ("file_write", "write_file", "edit_file"):
        path = args.get("path") or args.get("file_path") or ""
        if isinstance(path, str):
            path = path.rsplit("/", 1)[-1][:40]
        return f"Wrote {path}{status}"
    if tool_name == "list_directory":
        path = args.get("path") or ""
        if isinstance(path, str):
            path = path.rstrip("/") or path
            path = path.rsplit("/", 1)[-1][:40] or path[:40]
        if isinstance(result_data, dict):
            count = result_data.get("count")
            total_count = result_data.get("total_count")
            truncated = bool(result_data.get("truncated"))
            next_start_after = result_data.get("next_start_after")
            if isinstance(count, int) and isinstance(total_count, int):
                if truncated and next_start_after:
                    return (
                        f"Listed {path} ({count}/{total_count} entries shown; continue after "
                        f"{next_start_after}){status}"
                    )
                if truncated:
                    return f"Listed {path} ({count}/{total_count} entries shown){status}"
                return f"Listed {path} ({count} entries){status}"
        return f"Listed {path}{status}"
    if tool_name == "shell":
        cmd = (args.get("command") or "")[:40]
        return f"Ran command: {cmd}{status}"
    label = tool_name.replace("_", " ")
    return f"{label}{status}"


# ---------------------------------------------------------------------------
# Source extraction helper
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://[^\s\"'<>\]\)]+")


def _extract_cited_sources(tool_calls: list[dict[str, Any]]) -> list[str]:
    """Pull URLs from web_search/web_fetch and file paths from pdf_read/file_read results.

    *tool_calls* is a list of dicts with at least ``tool_name`` and
    ``output_preview`` (or ``args_preview``).  Returns a deduplicated list of
    source strings (URLs or file paths) in the order first seen.
    """
    seen: set[str] = set()
    sources: list[str] = []

    def _add(s: str) -> None:
        if s and s not in seen:
            seen.add(s)
            sources.append(s)

    for tc in tool_calls:
        name = tc.get("tool_name", "")
        raw_args = tc.get("args")
        args = raw_args if isinstance(raw_args, dict) else tc.get("args_preview", "")
        output = tc.get("output_preview", "")
        result_data = tc.get("result_data")

        if name in ("web_search", "web_fetch"):
            search_result_set = parse_search_result_set(result_data)
            if search_result_set is not None:
                for result in search_result_set.results:
                    url = canonicalize_search_url(result.url) or result.url
                    _add(url)
            for url in _URL_RE.findall(output):
                _add(canonicalize_search_url(url) or url)
            args_text = json.dumps(args, default=str) if isinstance(args, dict) else str(args)
            for url in _URL_RE.findall(args_text):
                _add(canonicalize_search_url(url) or url)
            if isinstance(result_data, dict):
                for url in _URL_RE.findall(json.dumps(result_data, default=str)):
                    _add(canonicalize_search_url(url) or url)

        elif name in ("pdf_read", "file_read"):
            if isinstance(args, dict):
                for field in ("path", "file_path", "filepath"):
                    value = args.get(field)
                    if isinstance(value, str):
                        _add(value)
            else:
                for field in ("path", "file_path", "filepath"):
                    if field in args:
                        try:
                            parsed = json.loads(args)
                            if isinstance(parsed, dict) and field in parsed:
                                _add(parsed[field])
                        except (json.JSONDecodeError, TypeError):
                            pass
            args_text = json.dumps(args, default=str) if isinstance(args, dict) else str(args)
            path_match = re.search(r'["\']?(/[^\s"\']+\.\w+)', args_text)
            if path_match:
                _add(path_match.group(1))
            path_match2 = re.search(r'["\']?(~/[^\s"\']+\.\w+)', args_text)
            if path_match2:
                _add(path_match2.group(1))

    return sources


def has_explicit_web_trigger(message: str, mentions: list[Any] | None = None) -> bool:
    if isinstance(mentions, list):
        for mention in mentions:
            if isinstance(mention, dict) and str(mention.get("type") or "").lower() == "web":
                return True
            if str(getattr(mention, "type", "") or "").lower() == "web":
                return True
    return bool(_AT_WEB_TOKEN_RE.search(str(message or "")))


def should_require_web_grounding(
    message: str,
    *,
    mode: str,
    required_action_hints: list[str] | None = None,
    mentions: list[Any] | None = None,
) -> bool:
    text = str(message or "").strip()
    text_lower = text.lower()
    action_hints = set(required_action_hints or [])
    if "search_web" in (required_action_hints or []):
        return True
    if has_explicit_web_trigger(text, mentions):
        return True
    if any(phrase in text_lower for phrase in _LIVE_DATA_PHRASES):
        return True
    # Local workflow control/query turns are often phrased as questions, but they
    # should not trigger generic web-grounding unless the user explicitly asked
    # for web/current information above.
    if action_hints.intersection({
        "workflow_build",
        "workflow_edit",
        "workflow_query",
        "workflow_run",
        "run_control",
    }):
        return False
    if mode not in {"agent", "conversation"}:
        return False
    return (
        text.endswith("?")
        or text_lower.startswith(("what ", "who ", "when ", "where ", "why ", "how ", "which "))
    )


def _claim_text_for_span(text: str, start: int, end: int) -> str:
    left = max(
        text.rfind(".", 0, start),
        text.rfind("?", 0, start),
        text.rfind("!", 0, start),
        text.rfind("\n", 0, start),
    )
    right_candidates = [idx for idx in (
        text.find(".", end),
        text.find("?", end),
        text.find("!", end),
        text.find("\n", end),
    ) if idx != -1]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1:right].strip()


def _expand_citation_indices(raw: str) -> list[int]:
    indices: list[int] = []
    for chunk in raw.split(","):
        part = chunk.strip()
        if not part:
            continue
        if "-" in part:
            start_raw, end_raw = part.split("-", 1)
            try:
                start = int(start_raw.strip())
                end = int(end_raw.strip())
            except ValueError:
                continue
            if start > 0 and end >= start:
                indices.extend(list(range(start, end + 1)))
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        if value > 0:
            indices.append(value)
    return indices


def extract_inline_citations(text: str) -> list[InlineCitation]:
    citations: list[InlineCitation] = []
    for match in re.finditer(r"\[([0-9,\-\s]+)\]", str(text or "")):
        indices = _expand_citation_indices(match.group(1))
        if not indices:
            continue
        surrounding = text[max(0, match.start() - 50): min(len(text), match.end() + 50)]
        claim_text = _claim_text_for_span(text, match.start(), match.end())
        for index in indices:
            citations.append(InlineCitation(
                index=index,
                surrounding_text=surrounding.strip(),
                claim_text=claim_text or surrounding.strip(),
            ))
    return citations


def _citation_source_url(payload: dict[str, Any]) -> str:
    url = str(payload.get("url", "") or "").strip()
    if url:
        return url
    source = payload.get("source")
    if isinstance(source, dict):
        return str(source.get("url", "") or "").strip()
    if isinstance(source, str):
        return source.strip()
    return ""


def _resolve_citation_source_index(
    payload: dict[str, Any],
    search_results: list[SearchResult],
) -> int | None:
    for key in ("source_index", "citation_index", "search_result_index", "result_index", "index"):
        value = payload.get(key)
        try:
            index = int(value)
        except (TypeError, ValueError):
            index = 0
        if index > 0:
            return index

    url = canonicalize_search_url(_citation_source_url(payload))
    if url:
        for result in search_results:
            result_url = canonicalize_search_url(result.url) or str(result.url or "").strip()
            if result_url and result_url == url:
                return result.index

    title = str(payload.get("title", "") or "").strip().lower()
    if title:
        for result in search_results:
            if str(result.title or "").strip().lower() == title:
                return result.index
    return None


def extract_native_inline_citations(
    raw_assistant_message: dict[str, Any] | None,
    search_results: list[SearchResult],
) -> list[InlineCitation]:
    if not isinstance(raw_assistant_message, dict):
        return []
    raw_blocks = raw_assistant_message.get("anthropic_content")
    if not isinstance(raw_blocks, list):
        return []

    citations: list[InlineCitation] = []
    for raw_block in raw_blocks:
        if not isinstance(raw_block, dict):
            continue
        block_text = str(
            raw_block.get("text")
            or raw_block.get("cited_text")
            or ""
        ).strip()
        block_citations = raw_block.get("citations")
        if isinstance(block_citations, list):
            for item in block_citations:
                if not isinstance(item, dict):
                    continue
                source_index = _resolve_citation_source_index(item, search_results)
                if source_index is None:
                    source_index = _resolve_citation_source_index(raw_block, search_results)
                if source_index is None:
                    continue
                claim_text = str(
                    item.get("cited_text")
                    or item.get("text")
                    or block_text
                    or ""
                ).strip()
                if not claim_text:
                    continue
                citations.append(InlineCitation(
                    index=source_index,
                    surrounding_text=block_text or claim_text,
                    claim_text=claim_text,
                ))
            continue

        if str(raw_block.get("type") or "").strip() != "search_result":
            continue
        source_index = _resolve_citation_source_index(raw_block, search_results)
        claim_text = str(raw_block.get("cited_text") or "").strip()
        if source_index is None or not claim_text:
            continue
        citations.append(InlineCitation(
            index=source_index,
            surrounding_text=claim_text,
            claim_text=claim_text,
        ))
    return citations


def extract_response_citations(
    response_text: str,
    search_results: list[SearchResult],
    *,
    raw_assistant_message: dict[str, Any] | None = None,
) -> list[InlineCitation]:
    seen: set[tuple[int, str]] = set()
    combined: list[InlineCitation] = []
    for citation in extract_native_inline_citations(raw_assistant_message, search_results) + extract_inline_citations(response_text):
        key = (citation.index, citation.claim_text)
        if key in seen:
            continue
        seen.add(key)
        combined.append(citation)
    return combined


def _extract_claim_terms(text: str) -> tuple[list[str], list[str]]:
    claim_text = str(text or "")
    numeric_terms = re.findall(r"(?:19|20)\d{2}|\d[\d,]*(?:\.\d+)?%?", claim_text)
    lexical_terms: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", claim_text.lower()):
        if token in _CLAIM_TERM_STOP_WORDS:
            continue
        lexical_terms.append(token)
    deduped_lexical = list(dict.fromkeys(lexical_terms))
    deduped_numeric = list(dict.fromkeys(numeric_terms))
    return deduped_lexical, deduped_numeric


def _matching_excerpt(source_text: str, anchor_terms: list[str]) -> str | None:
    lowered = source_text.lower()
    for term in anchor_terms:
        idx = lowered.find(term.lower())
        if idx == -1:
            continue
        start = max(0, idx - 80)
        end = min(len(source_text), idx + max(120, len(term) + 80))
        return source_text[start:end].strip()
    return None


def verify_citation(
    citation: InlineCitation,
    search_results: list[SearchResult],
) -> CitationVerification:
    source = next((item for item in search_results if item.index == citation.index), None)
    if source is None:
        return CitationVerification(
            citation_index=citation.index,
            claim_text=citation.claim_text,
            verified=False,
            confidence=0.0,
            reason=f"cited source [{citation.index}] does not exist",
        )

    source_text = str(source.fetched_content or source.snippet or "").strip()
    if not source_text:
        return CitationVerification(
            citation_index=citation.index,
            claim_text=citation.claim_text,
            source_url=source.url,
            verified=False,
            confidence=0.0,
            reason="cited source has no fetched content or usable snippet",
        )

    if not _NUMERIC_OR_DATE_RE.search(citation.claim_text):
        return CitationVerification(
            citation_index=citation.index,
            claim_text=citation.claim_text,
            source_url=source.url,
            source_excerpt_match=_matching_excerpt(source_text, [citation.claim_text]),
            verified=False,
            confidence=0.0,
            reason="claim is outside numeric/date verification scope",
        )

    lexical_terms, numeric_terms = _extract_claim_terms(citation.claim_text)
    lowered_source = source_text.lower()
    if numeric_terms and not all(term.lower() in lowered_source for term in numeric_terms):
        return CitationVerification(
            citation_index=citation.index,
            claim_text=citation.claim_text,
            source_url=source.url,
            source_excerpt_match=_matching_excerpt(source_text, numeric_terms),
            verified=False,
            confidence=0.0,
            reason="source does not contain the claimed numeric/date value",
        )

    total_terms = max(len(lexical_terms), 1)
    matched_terms = sum(1 for term in lexical_terms if term in lowered_source)
    match_ratio = matched_terms / total_terms
    verified = match_ratio >= 0.6
    excerpt = _matching_excerpt(source_text, numeric_terms or lexical_terms)
    return CitationVerification(
        citation_index=citation.index,
        claim_text=citation.claim_text,
        source_url=source.url,
        source_excerpt_match=excerpt,
        verified=verified,
        confidence=match_ratio,
        reason="matched claim terms in source" if verified else "source only partially matches claim terms",
    )


def verify_response_citations(
    response_text: str,
    search_results: list[SearchResult],
    *,
    raw_assistant_message: dict[str, Any] | None = None,
) -> list[CitationVerification]:
    verifications: list[CitationVerification] = []
    for citation in extract_response_citations(
        response_text,
        search_results,
        raw_assistant_message=raw_assistant_message,
    ):
        if not _NUMERIC_OR_DATE_RE.search(citation.claim_text):
            continue
        verifications.append(verify_citation(citation, search_results))
    return verifications


def _citation_verification_key(index: int, claim_text: str) -> tuple[int, str]:
    normalized = str(claim_text or "").strip().rstrip(".,;:")
    return (int(index), normalized)


def build_citation_records(
    response_text: str,
    search_results: list[SearchResult],
    *,
    verifications: list[CitationVerification] | None = None,
    raw_assistant_message: dict[str, Any] | None = None,
) -> list[CitationRecord]:
    verification_by_key = {
        _citation_verification_key(item.citation_index, item.claim_text): item
        for item in (verifications or [])
    }
    records: list[CitationRecord] = []
    seen: set[tuple[int, str]] = set()
    for citation in extract_response_citations(
        response_text,
        search_results,
        raw_assistant_message=raw_assistant_message,
    ):
        source = next((item for item in search_results if item.index == citation.index), None)
        if source is None:
            continue
        key = (citation.index, citation.claim_text)
        if key in seen:
            continue
        seen.add(key)
        verification = verification_by_key.get(
            _citation_verification_key(citation.index, citation.claim_text)
        )
        records.append(CitationRecord(
            claim_text=citation.claim_text,
            source_index=citation.index,
            source_url=source.url,
            cited_excerpt=(
                verification.source_excerpt_match
                if verification is not None and verification.source_excerpt_match
                else str(source.fetched_content or source.snippet or "")[:280]
            ),
            verified=verification.verified if verification is not None else None,
        ))
    return records


# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------

CHAT_MODE_ALIASES: dict[str, str] = {"build": "agent", "mutate": "agent"}


def normalize_chat_mode(mode: str) -> str:
    """Normalize deprecated mode aliases to canonical mode names.

    ``"auto"`` is passed through as-is (the caller resolves it via
    ``detect_chat_mode``).
    """
    if mode == "auto":
        return "auto"
    return CHAT_MODE_ALIASES.get(mode, mode)


def recent_run_failed_for_workflow(runs: list[Any], workflow_id: str) -> bool:
    """Return whether the newest run for this workflow failed.

    Accepts either dict snapshots or older object-like run records.
    """

    def _field(run: Any, key: str, default: Any = None) -> Any:
        if isinstance(run, dict):
            return run.get(key, default)
        return getattr(run, key, default)

    matching = [run for run in runs if _field(run, "graph_id") == workflow_id]
    if not matching:
        return False

    latest = max(matching, key=lambda run: float(_field(run, "started_at", 0.0) or 0.0))
    status = _field(latest, "status")
    return str(getattr(status, "value", status)).lower() == "failed"


def detect_chat_mode(
    message: str,
    recent_run_failed: bool = False,
) -> str:
    """Heuristic mode detection from message content.

    Priority order: debug > live_data (conversation) > ask > plan > agent (default).
    Live-data phrases (news, recent, milestone, etc.) route to conversation so
    tools (web_search) are used instead of text-only ask responses.
    """
    msg_lower = message.lower().strip()
    words = set(msg_lower.split())

    is_question = msg_lower.endswith("?") or any(
        msg_lower.startswith(q) for q in (
            "what", "how", "why", "where", "when", "which",
            "explain", "describe", "tell me about",
        )
    )

    debug_word_patterns = {"bug", "broken"}
    debug_stem_patterns = ("crash", "fail")
    debug_phrase_patterns = ["not working", "wrong output"]
    debug_via_word = bool(words & debug_word_patterns)
    debug_via_stem = any(
        any(w.startswith(s) for w in words) for s in debug_stem_patterns
    )
    debug_via_phrase = any(p in msg_lower for p in debug_phrase_patterns)
    debug_via_context = "error" in words and not is_question
    debug_via_fix = "fix" in words and not is_question
    if recent_run_failed or debug_via_word or debug_via_stem or debug_via_phrase or debug_via_context or debug_via_fix:
        return "debug"

    if any(p in msg_lower for p in _LIVE_DATA_PHRASES):
        return "conversation"

    if is_question:
        return "ask"

    plan_patterns = ["approach", "strategy", "architect", "propose", "how should"]
    plan_word_patterns = {"plan", "design"}
    if any(p in msg_lower for p in plan_patterns) or bool(words & plan_word_patterns):
        return "plan"

    return "agent"


def build_debug_context(runs: list[dict[str, Any]], workflow_id: str) -> str:
    """Build debug context string from run records for a workflow."""
    failed = [
        r for r in runs
        if r.get("graph_id") == workflow_id and r.get("status") == "failed"
    ]
    if not failed:
        return "No recent run failures found for this workflow."

    latest = max(failed, key=lambda run: float(run.get("started_at", 0.0) or 0.0))
    parts = [f"Last failed run: {latest.get('run_id', 'unknown')}"]

    errors = latest.get("errors", {})
    if errors:
        parts.append("Errors:")
        for key, val in errors.items():
            parts.append(f"  - {key}: {val}")

    events = latest.get("events", [])
    error_events = [
        e for e in events
        if isinstance(e, dict) and "error" in str(e.get("type", "")).lower()
    ]
    if error_events:
        parts.append("Error events (most recent):")
        for ev in error_events[-5:]:
            parts.append(f"  - {json.dumps(ev, default=str)[:500]}")

    outputs = latest.get("outputs", {})
    if outputs:
        parts.append("Run outputs:")
        for key, val in outputs.items():
            parts.append(f"  - {key}: {str(val)[:200]}")

    return "\n".join(parts)
