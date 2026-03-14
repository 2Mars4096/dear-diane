"""Chat mode detection, tool helpers, and miscellaneous utilities."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from dan.server.capability_registry import CapabilityResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action-hint / tool-routing constants
# ---------------------------------------------------------------------------

_ACTION_HINT_TOOL_MAP: dict[str, frozenset[str]] = {
    "read_file": frozenset({"file_read", "pdf_read", "list_directory"}),
    "search_web": frozenset({"web_search", "web_fetch", "http_request"}),
    "write_file": frozenset({"file_write"}),
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


def _missing_action_hints(
    required_action_hints: list[str] | None,
    satisfied_tool_names: set[str],
) -> list[str]:
    missing: list[str] = []
    for hint in _dedupe_action_hints(required_action_hints):
        tool_names = _ACTION_HINT_TOOL_MAP.get(hint)
        if tool_names and satisfied_tool_names.isdisjoint(tool_names):
            missing.append(hint)
    return missing


def _tool_choice_for_action_hints(
    required_action_hints: list[str] | None,
    satisfied_tool_names: set[str],
    *,
    allow_exact_tool_choice: bool = False,
) -> str | dict[str, Any]:
    missing_action_hints = _missing_action_hints(
        required_action_hints,
        satisfied_tool_names,
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
    return "required"


def _tool_retry_prompt_for_missing_actions(missing_action_hints: list[str]) -> str:
    instructions: list[str] = []
    search_pending = "search_web" in missing_action_hints
    write_pending = "write_file" in missing_action_hints
    read_pending = "read_file" in missing_action_hints
    handled_read = False

    if search_pending and write_pending:
        instructions.append(
            "You still need to gather live web information AND write the "
            "requested output. Do your research FIRST (web_search / web_fetch), "
            "then write incrementally:\n"
            "1. First file_write with mode='overwrite' — preamble + first section only.\n"
            "2. Subsequent file_write calls with mode='append' — one section each.\n"
            "3. Final file_write with mode='append' — close the document "
            "(\\end{document} or equivalent).\n"
            "Do NOT write until you have gathered sufficient data."
        )
    elif read_pending and write_pending:
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
        handled_read = True
    elif write_pending:
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
    elif search_pending:
        instructions.append(
            "You still need to gather live web information. Use web_search or web_fetch before finalizing."
        )

    if read_pending and not handled_read:
        instructions.append(
            "You still need to inspect the referenced local file or folder. "
            "Prefer chunked reads: use file_read or pdf_read with specific "
            "start_line/end_line ranges (or grep to locate sections) instead of "
            "re-reading the whole file."
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
) -> tuple[list[dict[str, Any]], str | dict[str, Any]]:
    filtered_tools = [tool for tool in all_tools if _tool_schema_name(tool) == tool_name]
    if not filtered_tools:
        filtered_tools = all_tools
    if allow_exact_tool_choice:
        return filtered_tools, {
            "type": "function",
            "function": {"name": tool_name},
        }
    return filtered_tools, "required"


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


def _is_transient_llm_error(exc: Exception) -> bool:
    """True if the exception is a transient LLM API error worth retrying."""
    msg = str(exc).lower()
    if "429" in msg or "rate" in msg or "rate limit" in msg:
        return True
    if "500" in msg or "502" in msg or "503" in msg or "internal server" in msg:
        return True
    if "timeout" in msg or "timed out" in msg:
        return True
    if "connection" in msg or "connect" in msg or "network" in msg:
        return True
    exc_cls = type(exc).__name__
    if exc_cls in ("RateLimitError", "APITimeoutError", "TimeoutError", "ConnectError"):
        return True
    return False


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
            for url in _URL_RE.findall(output):
                _add(url)
            args_text = json.dumps(args, default=str) if isinstance(args, dict) else str(args)
            for url in _URL_RE.findall(args_text):
                _add(url)
            if isinstance(result_data, dict):
                for url in _URL_RE.findall(json.dumps(result_data, default=str)):
                    _add(url)

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

    live_data_phrases = [
        "what happened", "recent", "news", "milestone", "latest", "today",
        "this week", "this month", "current", "updates", "headlines",
    ]
    if any(p in msg_lower for p in live_data_phrases):
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
