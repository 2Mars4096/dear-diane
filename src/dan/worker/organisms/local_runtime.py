"""Local LLM + tool runtime helpers for bounded organisms and coding CLIs."""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Callable, Sequence

from dan.providers import CompletionResult, LLMProvider, apply_cache_hints
from dan.tools import get_all_tools
from dan.tools._git_helpers import _find_repo, _git_binary
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse
from dan.worker.core.model import WorkerDefinition
from dan.worker.organism_log import organism_event_context, stable_output_contract_id
from dan.worker.organisms.coding_execution import CodingOrganism
from dan.worker.organisms.project_execution import ProjectExecutionOrganism
from dan.worker.organs import OrganPattern
from dan.worker.structured_payload import parse_jsonish_payload
from dan.worker.tissue import TissuePattern

DEFAULT_LIVE_ORGANISM_TOOL_IDS = [
    "list_directory",
    "file_read",
    "file_edit",
    "file_write",
    "shell_command",
    "web_search",
    "git_status",
    "git_diff",
    "git_log",
]
# ``shell_command`` is intentionally treated as mutation-capable here because the
# current standalone shell tool accepts arbitrary commands rather than a
# constrained read-only subset.
_READ_ONLY_TOOL_EXCLUSIONS = frozenset({"file_edit", "file_write", "shell_command"})
_DISCOVERY_ONLY_TOOL_IDS = frozenset(
    {"list_directory", "file_read", "web_search", "git_status", "git_diff", "git_log"}
)
_RESEARCH_TOOL_PREFERRED_ORDER = ("web_search", "file_read", "list_directory")
_RESEARCH_TOOL_EXCLUSIONS = frozenset({"git_status", "git_diff", "git_log"})
_CODING_AGGREGATION_TOOL_PREFERRED_ORDER = (
    "file_read",
    "file_edit",
    "file_write",
    "git_diff",
)
_CODING_AGGREGATION_TOOL_EXCLUSIONS = frozenset(
    {"list_directory", "shell_command", "web_search", "git_status", "git_log"}
)
_INTERNAL_WORKSPACE_DIR_NAMES = frozenset({".dan-code", ".git", ".pytest_cache", "__pycache__"})
_GREENFIELD_OPERATOR_ARTIFACTS = frozenset({"prompt.md", "acceptance.md", "report.json"})
ToolRuntimeEventCallback = Callable[[dict[str, Any]], None]
ToolApprovalCallback = Callable[[str, dict[str, Any], dict[str, Any]], bool]
_CODING_OUTPUT_COMMON_REQUIRED_KEYS = frozenset(
    {"change_summary", "target_files", "test_plan", "risks"}
)
_CODING_CANDIDATE_REQUIRED_KEYS = frozenset({"candidate_id"}) | _CODING_OUTPUT_COMMON_REQUIRED_KEYS
_CODING_WORKER_REQUIRED_KEYS = frozenset({"candidate_fragment"}) | _CODING_OUTPUT_COMMON_REQUIRED_KEYS
_VALIDATION_REPORT_REQUIRED_KEYS = frozenset(
    {
        "passed",
        "overall_score",
        "dimension_scores",
        "repair_brief",
        "missing_requirements",
        "comparison_note",
    }
)
_BLOCKED_TOOL_DISABLE_THRESHOLD = 2
_TOOL_PROMPT_TEXT_LIMIT = 2000
_PREWRITE_SHELL_ANALYSIS_NUDGE_THRESHOLD = 4
_FILE_WRITE_RAW_ARGUMENT_RISKY_LENGTH = 4000


def _event_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _compact_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _dedupe(values: Sequence[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _normalize_usage_totals(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, value in raw.items():
        try:
            normalized[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    prompt = int(raw.get("prompt_tokens", raw.get("prompt", 0)) or 0)
    completion = int(raw.get("completion_tokens", raw.get("completion", 0)) or 0)
    total = int(raw.get("total_tokens", prompt + completion) or (prompt + completion))
    normalized["prompt_tokens"] = prompt
    normalized["completion_tokens"] = completion
    normalized["total_tokens"] = total
    return normalized


def _merge_usage_totals(
    total: dict[str, int] | None,
    raw: Any,
) -> dict[str, int]:
    merged = dict(total or {})
    for key, value in _normalize_usage_totals(raw).items():
        merged[key] = int(merged.get(key, 0) or 0) + int(value)
    return merged


def _truncate_prompt_text(value: str, *, limit: int = _TOOL_PROMPT_TEXT_LIMIT) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    clipped = max(limit - 64, 0)
    omitted = max(len(value) - clipped, 0)
    suffix = f"\n...[truncated {omitted} chars for prompt]..."
    return value[:clipped] + suffix, True


def _compact_prompt_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        return _truncate_prompt_text(value)
    if isinstance(value, list):
        changed = False
        compacted: list[Any] = []
        for item in value:
            compact_item, item_changed = _compact_prompt_value(item)
            compacted.append(compact_item)
            changed = changed or item_changed
        return compacted, changed
    if isinstance(value, dict):
        changed = False
        compacted: dict[str, Any] = {}
        for key, item in value.items():
            compact_item, item_changed = _compact_prompt_value(item)
            compacted[str(key)] = compact_item
            changed = changed or item_changed
        return compacted, changed
    return value, False


def _compact_tool_payload_for_prompt(tool_payload: dict[str, Any]) -> dict[str, Any]:
    compacted, changed = _compact_prompt_value(tool_payload)
    if not isinstance(compacted, dict):
        return dict(tool_payload)
    if changed:
        compacted["prompt_payload_compacted"] = True
    return compacted


def _tool_schema_name(tool: dict[str, Any]) -> str:
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function")
    if not isinstance(function, dict):
        return ""
    return str(function.get("name") or "").strip()


def _enabled_tool_schemas(
    tool_schemas: Sequence[dict[str, Any]],
    *,
    disabled_tool_ids: Sequence[str],
) -> list[dict[str, Any]]:
    disabled = set(_dedupe(disabled_tool_ids))
    if not disabled:
        return [dict(tool) for tool in tool_schemas if isinstance(tool, dict)]
    return [
        dict(tool)
        for tool in tool_schemas
        if isinstance(tool, dict) and _tool_schema_name(tool) not in disabled
    ]


def _read_only_tool_ids(tool_ids: Sequence[str]) -> list[str]:
    return [tool_id for tool_id in _dedupe(tool_ids) if tool_id not in _READ_ONLY_TOOL_EXCLUSIONS]


def _research_read_only_tool_ids(tool_ids: Sequence[str]) -> list[str]:
    read_only = _read_only_tool_ids(tool_ids)
    preferred = [
        tool_id
        for tool_id in _RESEARCH_TOOL_PREFERRED_ORDER
        if tool_id in read_only
    ]
    extras = [
        tool_id
        for tool_id in read_only
        if tool_id not in preferred and tool_id not in _RESEARCH_TOOL_EXCLUSIONS
    ]
    narrowed = preferred + extras
    return narrowed or read_only


def _coding_aggregation_tool_ids(tool_ids: Sequence[str]) -> list[str]:
    available = _dedupe(tool_ids)
    preferred = [
        tool_id
        for tool_id in _CODING_AGGREGATION_TOOL_PREFERRED_ORDER
        if tool_id in available
    ]
    extras = [
        tool_id
        for tool_id in available
        if tool_id not in preferred and tool_id not in _CODING_AGGREGATION_TOOL_EXCLUSIONS
    ]
    narrowed = preferred + extras
    return narrowed or available


def _tool_ids_are_read_only(tool_ids: Sequence[str]) -> bool:
    available = _dedupe(tool_ids)
    return bool(available) and not any(tool_id in _READ_ONLY_TOOL_EXCLUSIONS for tool_id in available)


def _workspace_supports_git(workspace_root: str | Path) -> bool:
    try:
        _find_repo(str(Path(workspace_root).expanduser().resolve()))
    except Exception:
        return False
    return True


def _workspace_tool_ids(tool_ids: Sequence[str], *, workspace_root: str | Path) -> list[str]:
    selected = _dedupe(tool_ids)
    if _workspace_supports_git(workspace_root):
        return selected
    return [
        tool_id
        for tool_id in selected
        if tool_id not in {"git_status", "git_diff", "git_log"}
    ]


def _tool_use_policy(tool_ids: Sequence[str]) -> str:
    available = _dedupe(tool_ids)
    if not available:
        return ""

    mutation_capable = any(
        tool_id in _READ_ONLY_TOOL_EXCLUSIONS for tool_id in available
    )
    lines = [
        "Local tool-use policy:",
        "- Prefer the most specific structured tool available for the job.",
        "- Keep tool calls targeted and incremental. Avoid duplicate discovery once you already have the needed fact.",
    ]
    if "list_directory" in available:
        lines.append("- Use `list_directory` for directory inspection instead of shell `ls`.")
    if "file_read" in available:
        lines.append("- Use `file_read` for file contents instead of shell `cat`, `head`, or similar fallbacks.")
        lines.append(
            "- Prefer explicit line windows with `start_line`/`end_line` for code inspection. Do not rely on shell `grep`/`sed`/`awk`, regex searches, or other fixed-pattern matching to locate edit sites when `file_read` is available."
        )
        if not mutation_capable:
            lines.append(
                "- This tool set is read-only. Do not try to create files through `file_read`, and do not pass write-like arguments such as `write` or `content` to it."
            )
            lines.append(
                "- If the workspace is empty or a required file does not exist, confirm that quickly and then return a concrete bounded candidate for a later write-capable stage instead of repeatedly probing missing paths."
            )
    if "file_edit" in available:
        lines.append(
            "- Use `file_edit` for targeted line-based edits to existing files. Always include `path` and `start_line`, and include `content` for replace/insert edits. When replacing multiple lines, include `end_line` so the full target range is explicit. If you need multiple non-overlapping edits in the same file, prefer one `file_edit` call with `edits=[...]` over repeated single-edit calls."
        )
        lines.append(
            "- Prefer line-based edits derived from a prior `file_read`. Do not depend on regex, shell pattern matching, or exact text-match replacement as your primary edit localization strategy."
        )
    if "file_write" in available:
        lines.append(
            "- Use `file_write` for creating new files or replacing/appending whole-file content. Do not use shell heredocs, redirection, or `cat > file` when `file_edit` or `file_write` is available."
        )
    if "web_search" in available:
        lines.append(
            "- Use `web_search` for live external lookups or lightweight web research instead of guessing current facts."
        )
        lines.append(
            "- Call `web_search` with one concrete `query` string per call. Do not send batch payloads like `queries=[...]` to this tool."
        )
        if not mutation_capable:
            lines.append(
                "- In read-only coding-worker rounds, do not use `web_search` for generic architecture brainstorming, tutorials, examples, or best-practice browsing after you already confirmed an empty workspace."
            )
            lines.append(
                "- If a greenfield brief in an empty workspace needs new files, prefer returning a concrete `candidate_fragment` for later materialization. Use `web_search` only when the brief explicitly needs current external facts, library documentation, or version-specific behavior."
            )
        lines.append(
            '- When current identity, status, version, availability, or exact source text matters, use `web_search` in grounded mode with `search_depth=\"thorough\"` or `fetch_content=true` so it fetches the top authoritative result pages instead of relying on snippets alone.'
        )
        lines.append(
            "- If you already have a specific page URL, pass it as `url` to `web_search` instead of treating it as a separate tool choice."
        )
    if "web_fetch" in available:
        lines.append(
            "- Use `web_fetch` to read a specific authoritative page after discovery, especially when the current identity, status, version, or availability of a concrete external entity matters."
        )
        lines.append(
            "- Do not rely on search snippets alone for entity status, product availability, ticker tradability, law/policy text, API versioning, or other current-state facts when `web_fetch` is available."
        )
        lines.append(
            "- Prefer issuer, regulator, exchange, or official docs pages over retail quote pages or secondary summary sites when verifying current facts."
        )
    if {"git_status", "git_diff", "git_log"} & set(available):
        lines.append(
            "- Use the structured git tools for repository state, diff, and history. First confirm the target path is actually inside a git repository before using them."
        )
    if "shell_command" in available:
        lines.append(
            "- Use `shell_command` only when the structured tools cannot express the task, such as focused validation/build commands or narrow one-off searches."
        )
        if not ({"git_status", "git_diff", "git_log"} & set(available)):
            lines.append(
                "- Do not use shell `git` commands when git tools are unavailable; that usually means the workspace is not a git repository."
            )
    return "\n".join(lines)


def _missing_required_tool_arguments(
    metadata: dict[str, Any],
    arguments: dict[str, Any],
) -> list[str]:
    parameters = metadata.get("parameters") if isinstance(metadata, dict) else None
    if not isinstance(parameters, dict):
        return []
    required = parameters.get("required")
    if not isinstance(required, (list, tuple)):
        return []
    missing: list[str] = []
    for name in required:
        key = str(name or "").strip()
        if not key:
            continue
        if _tool_argument_is_missing(arguments, key):
            missing.append(key)
    return missing


def _tool_argument_is_missing(arguments: dict[str, Any], key: str) -> bool:
    if key not in arguments:
        return True
    value = arguments.get(key)
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _missing_alternative_required_tool_argument_groups(
    metadata: dict[str, Any],
    arguments: dict[str, Any],
) -> list[list[str]]:
    parameters = metadata.get("parameters") if isinstance(metadata, dict) else None
    if not isinstance(parameters, dict):
        return []

    alternative_groups: list[list[str]] = []
    for keyword in ("anyOf", "oneOf"):
        variants = parameters.get(keyword)
        if not isinstance(variants, (list, tuple)):
            continue
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            required = variant.get("required")
            if not isinstance(required, (list, tuple)):
                continue
            group = [str(name or "").strip() for name in required if str(name or "").strip()]
            if group:
                alternative_groups.append(group)

    if not alternative_groups:
        return []
    if any(all(not _tool_argument_is_missing(arguments, key) for key in group) for group in alternative_groups):
        return []
    return alternative_groups


def _tool_argument_validation_error(
    tool_id: str,
    missing_required: Sequence[str],
    alternative_required_groups: Sequence[Sequence[str]] | None = None,
) -> str:
    detail_parts: list[str] = []
    required_text = ", ".join(str(name).strip() for name in missing_required if str(name).strip())
    if required_text:
        detail_parts.append(required_text)
    if alternative_required_groups:
        rendered_groups = []
        for group in alternative_required_groups:
            names = [str(name).strip() for name in group if str(name).strip()]
            if not names:
                continue
            rendered_groups.append(f"({', '.join(names)})")
        if rendered_groups:
            detail_parts.append(f"one of {' or '.join(rendered_groups)}")
    return (
        f"tool_arguments_invalid: missing required arguments for {tool_id}: "
        f"{'; '.join(detail_parts) or 'unknown'}"
    )


def _tool_argument_failure_nudge(tool_id: str, error_text: str) -> str | None:
    marker = "tool_arguments_invalid:"
    if marker not in error_text:
        return None
    detail = error_text.split(marker, 1)[1].strip()
    if not detail:
        detail = f"invalid arguments for {tool_id}"
    extra_guidance = ""
    if tool_id == "web_search":
        extra_guidance = (
            ' For `web_search`, retry with exactly one concrete JSON object such as '
            '`{"query":"Brent crude oil price April 2026"}` or `{"url":"https://example.com/page"}`. '
            "Do not send `{}` and do not batch multiple empty `web_search` calls in the same round."
        )
    elif tool_id == "file_write":
        extra_guidance = (
            ' For `file_write`, retry with exactly one complete JSON object such as '
            '`{"path":"website/index.html","content":"<!doctype html>..."}`. '
            "Do not omit `path` or `content`, and do not send prose instead of the JSON arguments. "
            "Treat large monolithic writes as risky: if the intended content is above roughly 1200 words or 200 lines, "
            "split it into smaller coherent chunks or switch to `file_edit` for incremental updates to an existing file. "
            "After one failed large write, do not resend the same giant payload."
        )
    elif tool_id == "file_edit":
        extra_guidance = (
            ' For `file_edit`, retry with `{"path":"src/app.py","start_line":12,"content":"..."}` '
            'or `{"path":"src/app.py","edits":[...]}`. Include `path` plus one valid edit shape.'
        )
    return (
        f"Tool correction: the previous `{tool_id}` call failed because {detail}. "
        "Retry only with a complete JSON argument object that satisfies the tool schema exactly. "
        "If you do not know the missing values yet, use a different valid tool call first."
        f"{extra_guidance}"
    )


def _tool_availability_failure_nudge(
    tool_id: str,
    error_text: str,
    *,
    enabled_tool_ids: Sequence[str],
) -> str | None:
    if error_text.startswith("tool_temporarily_disabled:"):
        availability = "temporarily disabled"
    elif error_text.startswith("tool_not_enabled:"):
        availability = "not enabled"
    else:
        return None
    available_tools = [
        f"`{candidate}`"
        for candidate in _dedupe(enabled_tool_ids)
        if candidate and candidate != tool_id
    ]
    if available_tools:
        enabled_text = (
            "Use one of the currently enabled tools instead: "
            + ", ".join(available_tools)
            + "."
        )
    else:
        enabled_text = "No tools are currently enabled in this round, so respond without tool calls."
    return (
        f"Tool correction: `{tool_id}` is {availability} in this round. "
        f"{enabled_text} Do not call `{tool_id}` again until the available tool list changes."
    )


def _stable_tool_value(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _raw_tool_call_arguments_text(
    raw_call: dict[str, Any],
    *,
    arguments: dict[str, Any] | None = None,
) -> str | None:
    if isinstance(arguments, dict):
        raw_text = arguments.get("raw_arguments")
        if isinstance(raw_text, str) and raw_text.strip():
            return raw_text
    function = raw_call.get("function") if isinstance(raw_call, dict) else None
    raw_arguments = function.get("arguments") if isinstance(function, dict) else raw_call.get("arguments")
    if isinstance(raw_arguments, str) and raw_arguments.strip():
        return raw_arguments
    if isinstance(raw_arguments, dict):
        try:
            return json.dumps(raw_arguments, ensure_ascii=False, sort_keys=True)
        except Exception:
            return None
    return None


def _extract_jsonish_string_field(raw_text: str, field_name: str) -> str | None:
    text = str(raw_text or "")
    if not text.strip():
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        value = parsed.get(field_name)
        if isinstance(value, str) and value.strip():
            return value
    pattern = re.compile(
        rf'"{re.escape(field_name)}"\s*:\s*"((?:[^"\\]|\\.)*)"',
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return None
    try:
        return json.loads(f'"{match.group(1)}"')
    except Exception:
        return match.group(1)


def _is_repeated_tool_call(
    previous_tool: dict[str, Any] | None,
    current_tool: dict[str, Any],
) -> bool:
    if not isinstance(previous_tool, dict):
        return False
    if str(previous_tool.get("tool_id") or "").strip() != str(current_tool.get("tool_id") or "").strip():
        return False
    if _stable_tool_value(previous_tool.get("arguments")) != _stable_tool_value(current_tool.get("arguments")):
        return False
    if bool(previous_tool.get("ok")) != bool(current_tool.get("ok")):
        return False
    payload_key = "result" if current_tool.get("ok") else "error"
    return _stable_tool_value(previous_tool.get(payload_key)) == _stable_tool_value(current_tool.get(payload_key))


def _repeated_tool_call_nudge(tool_id: str, arguments: dict[str, Any]) -> str:
    rendered_arguments = _stable_tool_value(arguments)
    if len(rendered_arguments) > 160:
        rendered_arguments = rendered_arguments[:157] + "..."
    return (
        f"Tool correction: you already executed `{tool_id}` with the same arguments "
        f"{rendered_arguments}. Do not repeat that same discovery call. "
        "Use the existing result to choose a more specific next step or return your current conclusion."
    )


def _register_temporarily_disabled_tools(
    tool_ids: Sequence[str],
    *,
    blocked_tool_counts: dict[str, int],
    disabled_tool_ids: set[str],
    enabled_tool_ids: Sequence[str],
    request: CompletionRequest | None = None,
) -> list[str]:
    enabled = set(_dedupe(enabled_tool_ids))
    newly_disabled: list[str] = []
    for tool_id in _dedupe(tool_ids):
        if tool_id not in enabled:
            continue
        blocked_tool_counts[tool_id] = blocked_tool_counts.get(tool_id, 0) + 1
        if blocked_tool_counts[tool_id] < _BLOCKED_TOOL_DISABLE_THRESHOLD:
            continue
        if tool_id in disabled_tool_ids:
            continue
        if (
            request is not None
            and _coding_output_kind(request) is not None
            and tool_id in {"file_edit", "file_write"}
        ):
            # In write-capable coding stages, malformed direct-write calls should
            # trigger recovery nudges, not permanently disarm the only tools that
            # can still materialize the bounded patch in this turn.
            continue
        disabled_tool_ids.add(tool_id)
        newly_disabled.append(tool_id)
    return newly_disabled


def _temporarily_disabled_tool_message(tool_ids: Sequence[str]) -> str:
    names = [f"`{tool_id}`" for tool_id in _dedupe(tool_ids)]
    rendered = ", ".join(names) if names else "the repeated tool"
    verb = "is" if len(names) == 1 else "are"
    return (
        f"Tool correction: {rendered} {verb} temporarily disabled for the rest of this turn "
        "because you repeated invalid or redundant calls. Use a different enabled tool with "
        "concrete arguments, or return your current conclusion."
    )


def _relative_workspace_path(path: Any, *, workspace_root: Path) -> str | None:
    text = str(path or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    try:
        resolved = candidate if candidate.is_absolute() else workspace_root / candidate
        relative = resolved.resolve().relative_to(workspace_root.resolve())
        rendered = relative.as_posix()
        return rendered or "."
    except Exception:
        if candidate.is_absolute():
            return None
        parts = [part for part in Path(text).parts if part not in {"", "."}]
        if not parts:
            return "."
        return Path(*parts).as_posix()


def _is_internal_workspace_path(relative_path: str | None) -> bool:
    if not relative_path:
        return False
    parts = [part for part in Path(relative_path).parts if part not in {"", "."}]
    if not parts:
        return False
    return parts[0] in _INTERNAL_WORKSPACE_DIR_NAMES


def _is_greenfield_operator_artifact(relative_path: str | None) -> bool:
    if not relative_path:
        return False
    parts = [part for part in Path(relative_path).parts if part not in {"", "."}]
    if len(parts) != 1:
        return False
    return parts[0] in _GREENFIELD_OPERATOR_ARTIFACTS


def _effective_listing_entry_paths(result: Any, *, workspace_root: Path) -> list[str]:
    if not isinstance(result, dict):
        return []
    entries = result.get("entries")
    if not isinstance(entries, list):
        return []
    effective: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        relative = _relative_workspace_path(entry.get("path") or entry.get("name"), workspace_root=workspace_root)
        if relative:
            effective.append(relative)
    return effective


def _tool_confirms_effectively_empty_workspace(tool: dict[str, Any], *, workspace_root: Path) -> bool:
    if str(tool.get("tool_id") or "").strip() != "list_directory" or not tool.get("ok"):
        return False
    relative = _relative_workspace_path(dict(tool.get("arguments") or {}).get("path"), workspace_root=workspace_root)
    if relative not in {None, ".", ""}:
        return False
    entries = _effective_listing_entry_paths(tool.get("result"), workspace_root=workspace_root)
    return not entries or all(
        _is_internal_workspace_path(path) or _is_greenfield_operator_artifact(path)
        for path in entries
    )


def _tool_targets_internal_workspace_state(tool: dict[str, Any], *, workspace_root: Path) -> bool:
    tool_id = str(tool.get("tool_id") or "").strip()
    if tool_id not in {"list_directory", "file_read"}:
        return False
    relative = _relative_workspace_path(dict(tool.get("arguments") or {}).get("path"), workspace_root=workspace_root)
    return _is_internal_workspace_path(relative)


def _tool_reads_greenfield_operator_artifact(tool: dict[str, Any], *, workspace_root: Path) -> bool:
    if str(tool.get("tool_id") or "").strip() != "file_read" or not tool.get("ok"):
        return False
    relative = _relative_workspace_path(dict(tool.get("arguments") or {}).get("path"), workspace_root=workspace_root)
    return _is_greenfield_operator_artifact(relative)


def _tool_reports_missing_path(tool: dict[str, Any]) -> bool:
    error_text = str(tool.get("error") or "").lower()
    if not error_text:
        return False
    return (
        "file not found" in error_text
        or "directory not found" in error_text
        or "path not found" in error_text
    )


def _tool_is_web_search(tool: dict[str, Any]) -> bool:
    return str(tool.get("tool_id") or "").strip() == "web_search"


def _coding_output_kind(request: CompletionRequest) -> str | None:
    keys = _expected_return_shape_keys(request)
    if _CODING_CANDIDATE_REQUIRED_KEYS.issubset(keys):
        return "candidate"
    if _CODING_WORKER_REQUIRED_KEYS.issubset(keys):
        return "candidate_fragment"
    return None


def _read_only_finalize_mode(request: CompletionRequest) -> str | None:
    looks_like_validator = _looks_like_validator_request(request)
    if looks_like_validator:
        return "validator"

    system_prompt = str(request.system_prompt or "")
    expected_shape = str(getattr(request.output_contract, "expected_return_shape", "") or "")
    worker_id = str(request.metadata.get("worker_id") or "").strip()

    looks_like_coding_worker = (
        "Role: coding_worker" in system_prompt
        or "candidate_fragment" in expected_shape
        or worker_id.startswith("coding-build.worker")
    )
    if looks_like_coding_worker:
        return "coding_worker"

    return None


def _looks_like_validator_request(request: CompletionRequest) -> bool:
    system_prompt = str(request.system_prompt or "")
    lowered_system_prompt = system_prompt.lower()
    worker_id = str(request.metadata.get("worker_id") or "").strip().lower()
    organism_stage = str(request.metadata.get("organism_stage") or "").strip().lower()
    recipient = dict(request.metadata.get("recipient") or {})
    recipient_cell_id = str(recipient.get("cell_id") or "").strip().lower()
    keys = _expected_return_shape_keys(request)
    return (
        _VALIDATION_REPORT_REQUIRED_KEYS.issubset(keys)
        or "role: validator_" in lowered_system_prompt
        or ".validator" in worker_id
        or organism_stage == "validation"
        or ".validator" in recipient_cell_id
    )


def _validator_read_only_tool_schemas(
    request: CompletionRequest,
    tool_schemas: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not _looks_like_validator_request(request):
        return [dict(tool) for tool in tool_schemas if isinstance(tool, dict)]
    allowed = set(
        _read_only_tool_ids(
            [
                _tool_schema_name(tool)
                for tool in tool_schemas
                if isinstance(tool, dict) and _tool_schema_name(tool)
            ]
        )
    )
    return [
        dict(tool)
        for tool in tool_schemas
        if isinstance(tool, dict) and _tool_schema_name(tool) in allowed
    ]


def _request_expects_coding_candidate(request: CompletionRequest) -> bool:
    return _coding_output_kind(request) == "candidate"


def _tool_argument_path(tool: dict[str, Any]) -> str:
    arguments = dict(tool.get("arguments") or {})
    result = dict(tool.get("result") or {})
    return str(
        result.get("path")
        or arguments.get("path")
        or arguments.get("file_path")
        or ""
    ).strip()


def _file_write_target_path(
    raw_call: dict[str, Any],
    *,
    arguments: dict[str, Any],
    workspace_root: Path,
) -> str | None:
    path_value = arguments.get("path")
    if not isinstance(path_value, str) or not path_value.strip():
        raw_text = _raw_tool_call_arguments_text(raw_call, arguments=arguments)
        if raw_text:
            path_value = _extract_jsonish_string_field(raw_text, "path")
    if not isinstance(path_value, str) or not path_value.strip():
        return None
    return _relative_workspace_path(path_value, workspace_root=workspace_root)


def _existing_workspace_file_write_target_path(
    raw_call: dict[str, Any],
    *,
    arguments: dict[str, Any],
    workspace_root: Path,
) -> str | None:
    relative_path = _file_write_target_path(
        raw_call,
        arguments=arguments,
        workspace_root=workspace_root,
    )
    if not relative_path:
        return None
    candidate = (workspace_root / relative_path).resolve()
    if not candidate.is_file():
        return None
    return relative_path


def _file_write_invalid_large_overwrite_downshift(
    raw_call: dict[str, Any],
    *,
    arguments: dict[str, Any],
    finish_reason: str | None,
    workspace_root: Path,
    file_edit_available: bool,
) -> tuple[str, str] | None:
    if not file_edit_available:
        return None
    relative_path = _existing_workspace_file_write_target_path(
        raw_call,
        arguments=arguments,
        workspace_root=workspace_root,
    )
    if not relative_path:
        return None
    raw_text = _raw_tool_call_arguments_text(raw_call, arguments=arguments)
    if not raw_text or '"content"' not in raw_text:
        return None
    finish_text = str(finish_reason or "").strip().lower()
    trimmed = raw_text.rstrip()
    if finish_text == "length":
        return relative_path, "model_output_truncated"
    if len(raw_text) >= _FILE_WRITE_RAW_ARGUMENT_RISKY_LENGTH and not trimmed.endswith("}"):
        return relative_path, "oversized_payload_cut_off"
    return None


def _file_write_incremental_edit_required_message(path: str, reason: str) -> str:
    reason_text = reason.replace("_", " ")
    return (
        f"Controller note: `file_write` to existing file `{path}` is blocked for the rest of this turn "
        f"because an earlier whole-file payload for that file looked {reason_text}. "
        f"Downshift now: use `file_edit` for bounded incremental updates to `{path}`. "
        "If exact line numbers are stale, take one targeted `file_read` of that same file first, "
        "then apply the edit. Do not resend another monolithic overwrite of the same existing file."
    )


def _normalized_tool_path(path: Any, *, workspace_root: Path | None = None) -> str | None:
    text = str(path or "").strip()
    if not text:
        return None
    if workspace_root is not None:
        relative = _relative_workspace_path(text, workspace_root=workspace_root)
        if relative:
            return relative
    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        try:
            return str(candidate.resolve())
        except Exception:
            return str(candidate)
    parts = [part for part in candidate.parts if part not in {"", "."}]
    if not parts:
        return "."
    return Path(*parts).as_posix()


def _looks_like_temporary_workspace_helper_path(relative_path: str) -> bool:
    path = Path(str(relative_path or "").strip())
    parts = [part for part in path.parts if part not in {"", "."}]
    if len(parts) != 1:
        return False
    name = parts[0].lower()
    suffix = Path(name).suffix
    if suffix not in {".py", ".sh", ".tmp", ".txt", ".log", ".out"}:
        return False
    stem = Path(name).stem
    exact_stems = {
        "tmp",
        "temp",
        "scratch",
        "debug",
        "inspect",
        "repro",
        "check",
        "read",
        "dump",
        "probe",
    }
    if stem in exact_stems:
        return True
    helper_prefixes = (
        "tmp_",
        "tmp-",
        "temp_",
        "temp-",
        "scratch_",
        "scratch-",
        "debug_",
        "debug-",
        "inspect_",
        "inspect-",
        "repro_",
        "repro-",
        "check_",
        "check-",
        "read_",
        "read-",
        "dump_",
        "dump-",
        "probe_",
        "probe-",
        "script_",
        "script-",
        "run_dump",
        "run-dump",
    )
    return stem.startswith(helper_prefixes)


def _tool_materialized_mutation(tool: dict[str, Any]) -> bool:
    if not tool.get("ok"):
        return False
    tool_id = str(tool.get("tool_id") or "").strip()
    if tool_id not in {"file_write", "file_edit"}:
        return False
    if tool_id == "file_edit":
        result = dict(tool.get("result") or {})
        if result.get("changed") is False or result.get("no_op") is True:
            return False
    return True


def _successful_workspace_mutation_paths(
    executed_tools: Sequence[dict[str, Any]],
    *,
    workspace_root: Path,
) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for tool in executed_tools:
        if not _tool_materialized_mutation(tool):
            continue
        relative = _relative_workspace_path(
            _tool_argument_path(tool),
            workspace_root=workspace_root,
        )
        if not relative or relative in seen:
            continue
        if _looks_like_temporary_workspace_helper_path(relative):
            continue
        seen.add(relative)
        paths.append(relative)
    return paths


def _temporary_path_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in (
        tempfile.gettempdir(),
        os.environ.get("TMPDIR"),
        os.environ.get("TEMP"),
        os.environ.get("TMP"),
    ):
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            resolved = Path(text).expanduser().resolve()
        except Exception:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


def _is_temporary_external_path(path: Any, *, workspace_root: Path) -> bool:
    text = str(path or "").strip()
    if not text:
        return False
    if _relative_workspace_path(text, workspace_root=workspace_root) is not None:
        return False
    try:
        resolved = Path(text).expanduser().resolve()
    except Exception:
        return False
    return any(resolved == root or root in resolved.parents for root in _temporary_path_roots())


def _tool_mutates_temporary_external_path(
    tool: dict[str, Any],
    *,
    workspace_root: Path,
) -> bool:
    if not _tool_materialized_mutation(tool):
        return False
    return _is_temporary_external_path(
        _tool_argument_path(tool),
        workspace_root=workspace_root,
    )


def _tool_mutates_temporary_workspace_helper_path(
    tool: dict[str, Any],
    *,
    workspace_root: Path,
) -> bool:
    if not _tool_materialized_mutation(tool):
        return False
    relative = _relative_workspace_path(
        _tool_argument_path(tool),
        workspace_root=workspace_root,
    )
    if not relative:
        return False
    return _looks_like_temporary_workspace_helper_path(relative)


def _successful_mutation_paths(
    executed_tools: Sequence[dict[str, Any]],
    *,
    workspace_root: Path | None = None,
) -> list[str]:
    if workspace_root is not None:
        return _successful_workspace_mutation_paths(
            executed_tools,
            workspace_root=workspace_root,
        )
    paths: list[str] = []
    seen: set[str] = set()
    for tool in executed_tools:
        if not _tool_materialized_mutation(tool):
            continue
        path = _normalized_tool_path(
            _tool_argument_path(tool),
            workspace_root=workspace_root,
        )
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def _tracked_workspace_diff_paths(*, workspace_root: Path) -> list[str]:
    try:
        repo = Path(_find_repo(str(workspace_root))).resolve()
        root = Path(workspace_root).resolve()
    except Exception:
        return []

    try:
        completed = subprocess.run(
            [_git_binary(), "-C", str(repo), "diff", "--name-only"],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []
    if completed.returncode != 0:
        return []

    paths: list[str] = []
    seen: set[str] = set()
    for line in completed.stdout.splitlines():
        text = str(line or "").strip()
        if not text:
            continue
        relative = _relative_workspace_path(str(repo / text), workspace_root=root)
        if not relative or relative in seen:
            continue
        seen.add(relative)
        paths.append(relative)
    return paths


def _verification_like_command(command: str) -> bool:
    text = str(command or "").strip().lower()
    if not text:
        return False
    markers = (
        "pytest",
        "python -m ",
        "python3 -m ",
        "uvicorn ",
        "open ",
        "curl ",
        "--help",
    )
    return any(marker in text for marker in markers)


def _successful_verification_commands(executed_tools: Sequence[dict[str, Any]]) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()
    for tool in executed_tools:
        if not tool.get("ok"):
            continue
        if str(tool.get("tool_id") or "").strip() != "shell_command":
            continue
        command = str(dict(tool.get("arguments") or {}).get("command") or "").strip()
        if not command or not _verification_like_command(command) or command in seen:
            continue
        seen.add(command)
        commands.append(command)
    return commands


def _last_successful_mutation_index(executed_tools: Sequence[dict[str, Any]]) -> int | None:
    last_index: int | None = None
    for index, tool in enumerate(executed_tools):
        if _tool_materialized_mutation(tool):
            last_index = index
    return last_index


def _successful_discovery_tool_count(executed_tools: Sequence[dict[str, Any]]) -> int:
    count = 0
    for tool in executed_tools:
        if not tool.get("ok"):
            continue
        if str(tool.get("tool_id") or "").strip() in _DISCOVERY_ONLY_TOOL_IDS:
            count += 1
    return count


def _successful_read_paths(
    executed_tools: Sequence[dict[str, Any]],
    *,
    workspace_root: Path | None = None,
) -> list[str]:
    paths: list[str] = []
    for tool in executed_tools:
        if not tool.get("ok"):
            continue
        if str(tool.get("tool_id") or "").strip() != "file_read":
            continue
        result = dict(tool.get("result") or {})
        path = _normalized_tool_path(
            result.get("path") or dict(tool.get("arguments") or {}).get("path"),
            workspace_root=workspace_root,
        )
        if path:
            paths.append(path)
    return paths


def _successful_shell_command_count(executed_tools: Sequence[dict[str, Any]]) -> int:
    count = 0
    for tool in executed_tools:
        if not tool.get("ok"):
            continue
        if str(tool.get("tool_id") or "").strip() != "shell_command":
            continue
        command = str(dict(tool.get("arguments") or {}).get("command") or "").strip()
        if not command:
            continue
        count += 1
    return count


def _partial_coding_candidate_from_tool_evidence(
    *,
    request: CompletionRequest,
    executed_tools: Sequence[dict[str, Any]],
    stop_reason: str,
    existing_text: str | None = None,
    workspace_root: Path | None = None,
) -> dict[str, Any] | None:
    output_kind = _coding_output_kind(request)
    if output_kind is None:
        return None

    target_files = _successful_mutation_paths(
        executed_tools,
        workspace_root=workspace_root,
    )
    if workspace_root is not None:
        tracked_diff_paths = _tracked_workspace_diff_paths(workspace_root=workspace_root)
        if tracked_diff_paths:
            tracked_target_files = [
                path for path in target_files if path in tracked_diff_paths
            ]
            if tracked_target_files:
                target_files = tracked_target_files
    if not target_files:
        return None

    base_payload: dict[str, Any] = {}
    parsed = parse_jsonish_payload(existing_text)
    if isinstance(parsed, dict):
        base_payload = dict(parsed)

    reason_text = " ".join(stop_reason.replace(":", " ").replace("_", " ").split())
    verification_commands = _successful_verification_commands(executed_tools)

    payload = dict(base_payload)
    if output_kind == "candidate":
        payload.setdefault("candidate_id", "partial-candidate-from-tool-evidence")
    else:
        payload.setdefault("candidate_fragment", {})
    payload["change_summary"] = str(
        payload.get("change_summary")
        or (
            f"Materialized {len(target_files)} file(s) during the write-capable coding stage, "
            f"but the model did not return the final candidate payload before {reason_text}."
        )
    ).strip()
    payload["target_files"] = target_files
    payload["test_plan"] = (
        verification_commands
        or [
            "Inspect the materialized files and rerun the bounded validation step.",
        ]
    )
    payload["risks"] = [
        f"The provider completion timed out before the final candidate payload was returned, so this result was synthesized from successful write-tool evidence after {reason_text}.",
        "Review the materialized files and rerun the bounded repair/validation loop before treating this as done.",
    ]
    if output_kind == "candidate":
        payload["workspace_effect"] = "modified"
    payload["synthesized_from_tool_evidence"] = True
    payload["fallback_reason"] = stop_reason
    return payload


def _write_stage_unmaterialized_candidate_payload(
    request: CompletionRequest,
    *,
    existing_text: str | None,
    stop_reason: str,
) -> dict[str, Any] | None:
    output_kind = _coding_output_kind(request)
    if output_kind is None:
        return None

    base_payload: dict[str, Any] = {}
    parsed = parse_jsonish_payload(existing_text)
    if isinstance(parsed, dict):
        base_payload = dict(parsed)

    payload = dict(base_payload)
    if output_kind == "candidate":
        candidate_id = str(payload.get("candidate_id") or "").strip()
        payload["candidate_id"] = candidate_id or "unmaterialized-write-stage-candidate"
        payload["workspace_effect"] = "none"
    else:
        payload.setdefault("candidate_fragment", {})

    target_files = payload.get("target_files")
    if isinstance(target_files, list):
        payload["target_files"] = [
            str(item).strip() for item in target_files if str(item).strip()
        ]
    else:
        payload["target_files"] = []

    test_plan = payload.get("test_plan")
    if isinstance(test_plan, list) and any(str(item).strip() for item in test_plan):
        payload["test_plan"] = [
            str(item).strip() for item in test_plan if str(item).strip()
        ]
    else:
        payload["test_plan"] = [
            "Materialize the bounded workspace patch with file_edit/file_write before validation."
        ]

    change_summary = str(payload.get("change_summary") or "").strip()
    if not change_summary:
        payload["change_summary"] = (
            "The model proposed a bounded change in prose but did not materialize any workspace edit after the "
            "direct-write guardrail."
        )
    else:
        payload["change_summary"] = change_summary

    risks = payload.get("risks")
    normalized_risks = (
        [str(item).strip() for item in risks if str(item).strip()]
        if isinstance(risks, list)
        else []
    )
    for risk in (
        "No checked-out workspace file was modified in this write-capable coding stage.",
        "Treat this as blocked until the bounded patch is materialized with file_edit/file_write.",
    ):
        if risk not in normalized_risks:
            normalized_risks.append(risk)
    payload["risks"] = normalized_risks
    payload["fallback_reason"] = stop_reason
    return payload


def _write_capable_coding_stage_first_write_nudge_reason(
    *,
    request: CompletionRequest,
    tool_ids: Sequence[str],
    executed_tools: Sequence[dict[str, Any]],
    workspace_root: Path,
) -> str | None:
    if _coding_output_kind(request) is None:
        return None
    if _tool_ids_are_read_only(tool_ids):
        return None
    if _successful_workspace_mutation_paths(
        executed_tools,
        workspace_root=workspace_root,
    ):
        return None
    for tool in executed_tools:
        if _tool_confirms_effectively_empty_workspace(tool, workspace_root=workspace_root):
            return "first_write_after_empty_workspace"
    if len(_successful_read_paths(executed_tools, workspace_root=workspace_root)) >= 6:
        return "stalled_analysis_before_first_write"
    if _successful_shell_command_count(executed_tools) >= _PREWRITE_SHELL_ANALYSIS_NUDGE_THRESHOLD:
        return "stalled_analysis_before_first_write"
    return None


def _write_capable_coding_stage_first_write_nudge_message(
    reason: str,
    *,
    allow_final_read: bool,
) -> str:
    reason_text = reason.replace("_", " ")
    final_read_sentence = (
        " If you still need exact line grounding, you may take at most one final targeted `file_read` immediately before the first write, then stop auditing and write."
        if allow_final_read
        else ""
    )
    return (
        "Controller note: this write-capable coding stage is still read-only after initial discovery "
        f"({reason_text}). Stop auditing and make the first concrete project write now using the direct file tools "
        "that are already enabled. The next tool call should be `file_write` or `file_edit`, not another read, search, "
        "or final prose-only answer."
        f"{final_read_sentence} Create one or two small real files first, then continue incrementally. Prefer a "
        "minimal runnable slice over a complete project in one giant tool call. If you truly cannot materialize any "
        "bounded file set in this turn, return an explicit blocked candidate now instead of doing more discovery."
    )


def _write_capable_coding_stage_finalize_reason(
    *,
    request: CompletionRequest,
    tool_ids: Sequence[str],
    executed_tools: Sequence[dict[str, Any]],
    workspace_root: Path,
    disabled_tool_ids: Sequence[str] | None = None,
) -> str | None:
    if _coding_output_kind(request) is None:
        return None
    if _tool_ids_are_read_only(tool_ids):
        return None

    workspace_mutation_paths = _successful_workspace_mutation_paths(
        executed_tools,
        workspace_root=workspace_root,
    )
    if not workspace_mutation_paths:
        return None
    for tool in executed_tools:
        if _tool_mutates_temporary_external_path(tool, workspace_root=workspace_root):
            return "temporary_external_write_after_workspace_patch"
        if _tool_mutates_temporary_workspace_helper_path(tool, workspace_root=workspace_root):
            return "temporary_workspace_helper_write_after_workspace_patch"

    last_mutation_index = _last_successful_mutation_index(executed_tools)
    if last_mutation_index is None:
        return None

    successful_post_mutation = [
        tool for tool in executed_tools[last_mutation_index + 1 :] if tool.get("ok")
    ]
    if not successful_post_mutation:
        return None

    saw_verification = False
    successful_post_verification_count = 0
    for tool in successful_post_mutation:
        tool_id = str(tool.get("tool_id") or "").strip()
        if tool_id == "shell_command":
            command = str(dict(tool.get("arguments") or {}).get("command") or "").strip()
            if command and _verification_like_command(command):
                saw_verification = True
                successful_post_verification_count += 1
                continue
            if saw_verification:
                return "non_verification_shell_after_verification"
            continue
        if saw_verification:
            successful_post_verification_count += 1

    if saw_verification and successful_post_verification_count >= 4:
        return "extended_post_write_analysis_after_verification"
    return None


def _write_capable_coding_stage_finalize_message(reason: str) -> str:
    reason_text = reason.replace("_", " ")
    return (
        "Controller note: this write-capable coding stage already materialized a bounded patch and the current turn "
        f"has crossed the point where more tool work is low value ({reason_text}). Stop using tools and return the "
        "required structured coding result now. "
        "Reuse only the current workspace diff plus the successful checks already gathered in this turn. Do not spend "
        "more time on environment/package probing, extra repo discovery, or optional validation churn. If validation "
        "is still incomplete, say that directly in `test_plan` and `risks`, but finalize the candidate now."
    )


def _write_capable_coding_stage_post_final_read_message() -> str:
    return (
        "Controller note: you already used the final targeted `file_read` for this write stage. "
        "The next tool call must be `file_edit` or `file_write`. Stop auditing and materialize the bounded patch now."
    )


def _write_capable_coding_stage_direct_write_required_reason(
    *,
    request: CompletionRequest,
    tool_ids: Sequence[str],
    executed_tools: Sequence[dict[str, Any]],
    workspace_root: Path,
    direct_write_required: bool,
) -> str | None:
    if not direct_write_required:
        return None
    if _coding_output_kind(request) is None:
        return None
    if _tool_ids_are_read_only(tool_ids):
        return None
    if _successful_workspace_mutation_paths(
        executed_tools,
        workspace_root=workspace_root,
    ):
        return None
    return "returned_without_materializing_workspace_patch"


def _write_capable_coding_stage_direct_write_required_message(reason: str) -> str:
    reason_text = reason.replace("_", " ")
    return (
        "Controller note: this write-capable coding stage still has no materialized workspace patch "
        f"({reason_text}). Returning prose-only patch instructions is not enough here. The next response must "
        "either call `file_edit` or `file_write` to apply the bounded change directly in the checked-out "
        "workspace, or return an explicit blocked candidate that says no bounded workspace write could be "
        "completed. Do not propose edits without a real workspace mutation."
    )


def _write_stage_direct_write_helper_path_reason(
    arguments: dict[str, Any],
    *,
    workspace_root: Path,
) -> str | None:
    path = _tool_argument_path({"arguments": arguments})
    if _is_temporary_external_path(path, workspace_root=workspace_root):
        return "temporary_external_helper_before_workspace_patch"
    relative = _relative_workspace_path(path, workspace_root=workspace_root)
    if relative and _looks_like_temporary_workspace_helper_path(relative):
        return "temporary_workspace_helper_before_workspace_patch"
    return None


def _write_stage_direct_write_helper_path_message(
    *,
    tool_id: str,
    reason: str,
    arguments: dict[str, Any],
) -> str:
    path = _tool_argument_path({"arguments": arguments}) or "<missing path>"
    reason_text = reason.replace("_", " ")
    return (
        f"Controller note: `{tool_id}` to `{path}` was blocked because direct-write mode is active and "
        f"there is still no materialized workspace patch ({reason_text}). Do not create temporary helper "
        "scripts or scratch files now. Use `file_edit` or `file_write` on the real checked-out project "
        "file(s), or return an explicit blocked candidate if no bounded project write can be completed."
    )


def _write_stage_tool_schemas(
    tool_schemas: Sequence[dict[str, Any]],
    *,
    allow_final_read: bool,
) -> list[dict[str, Any]]:
    preferred_names = {"file_edit", "file_write"}
    if allow_final_read:
        preferred_names.add("file_read")
    narrowed: list[dict[str, Any]] = []
    for tool in tool_schemas:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = str(function.get("name") or "").strip() if isinstance(function, dict) else ""
        if name in preferred_names:
            narrowed.append(tool)
    return narrowed


def _direct_write_tool_schemas(tool_schemas: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    direct_write_tools = []
    for tool in tool_schemas:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = str(function.get("name") or "").strip() if isinstance(function, dict) else ""
        if name in {"file_write", "file_edit"}:
            direct_write_tools.append(tool)
    return direct_write_tools


def _read_only_finalize_reason(
    *,
    request: CompletionRequest,
    tool_ids: Sequence[str],
    executed_tools: Sequence[dict[str, Any]],
    workspace_root: Path,
) -> str | None:
    mode = _read_only_finalize_mode(request)
    if mode is None:
        return None
    if not _tool_ids_are_read_only(tool_ids):
        return None

    normalized_read_paths = _successful_read_paths(
        executed_tools,
        workspace_root=workspace_root,
    )

    if mode == "validator":
        saw_git_diff = any(
            tool.get("ok")
            and str(tool.get("tool_id") or "").strip() == "git_diff"
            for tool in executed_tools
        )
        if saw_git_diff and len(normalized_read_paths) >= 3:
            counts: dict[str, int] = {}
            for path in normalized_read_paths:
                counts[path] = counts.get(path, 0) + 1
            if any(count >= 2 for count in counts.values()):
                return "repeated_validator_reread_after_grounding"

    if _successful_discovery_tool_count(executed_tools) >= 6:
        return "stalled_analysis_in_checked_out_repo"

    if mode != "coding_worker":
        return None

    empty_workspace_index: int | None = None
    for index, tool in enumerate(executed_tools):
        if _tool_confirms_effectively_empty_workspace(tool, workspace_root=workspace_root):
            empty_workspace_index = index
            break
    if empty_workspace_index is None:
        return None

    for tool in executed_tools[empty_workspace_index + 1 :]:
        if _tool_targets_internal_workspace_state(tool, workspace_root=workspace_root):
            return "internal_state_probe_after_empty_workspace"
        if _tool_reads_greenfield_operator_artifact(tool, workspace_root=workspace_root):
            return "operator_artifact_read_after_empty_workspace"
        if _tool_reports_missing_path(tool):
            return "missing_path_after_empty_workspace"
        if _tool_is_web_search(tool):
            return "web_search_after_empty_workspace"
    return None


def _read_only_finalize_message(reason: str, *, mode: str) -> str:
    reason_text = reason.replace("_", " ")
    if mode == "validator":
        return (
            "Controller note: stop using tools in this read-only validation round. "
            f"You already have enough bounded grounding to return the required validation output now ({reason_text}). "
            "Use only the evidence already gathered in this turn. If your contract expects a brief verdict, emit it "
            "directly; if it expects a structured validation report, fill those fields from the evidence already in "
            "hand. Do not call more tools."
        )
    return (
        "Controller note: stop using tools in this read-only coding worker round. "
        f"You already have enough bounded grounding to return the structured contribution now ({reason_text}). "
        "Return the required structured response now. If new files are needed, emit a concrete "
        "`candidate_fragment` that a later write-capable aggregation stage can materialize. If you "
        "already used one quick external lookup, incorporate only what you learned so far and finalize "
        "the candidate now. If you still cannot propose a bounded candidate, return an explicit no-progress "
        "candidate and explain the blocker in `change_summary` and `risks`."
    )


def _research_note_finalize_reason(
    *,
    request: CompletionRequest,
    tool_ids: Sequence[str],
    executed_tools: Sequence[dict[str, Any]],
    round_number: int,
) -> str | None:
    keys = _expected_return_shape_keys(request)
    looks_like_research_note = {
        "findings",
        "evidence_refs",
        "contradictions",
        "open_questions",
    }.issubset(keys) and "report_readiness" not in keys
    if not looks_like_research_note:
        return None
    if not _tool_ids_are_read_only(tool_ids):
        return None
    if round_number < 2:
        return None
    successful_tools = sum(1 for tool in executed_tools if tool.get("ok"))
    if successful_tools < 2:
        return None
    return "grounded_note_ready_for_synthesis"


def _research_note_finalize_message(reason: str) -> str:
    reason_text = reason.replace("_", " ")
    return (
        "Controller note: stop using tools and return the compact evidence note now. "
        f"You already have enough bounded grounding for this lane ({reason_text}). "
        "Do not attempt the full final report. Return only the lane-level research note "
        "with findings, evidence_refs, contradictions, open_questions, and optional "
        "reasoning_notes/follow_up_queries derived from the evidence already gathered."
    )


def _provider_prompt_filter_error(exc: BaseException) -> bool:
    """Detect provider-side prompt safety/filter rejections without binding to one SDK."""

    text_parts = [
        type(exc).__name__,
        str(exc),
        repr(getattr(exc, "body", "")),
        repr(getattr(exc, "response", "")),
    ]
    text = " ".join(part for part in text_parts if part).lower()
    if "content_filter" in text or "high risk" in text:
        return True
    return "prompt" in text and ("safety" in text or "rejected" in text or "blocked" in text)


def _provider_safety_retry_messages(request: CompletionRequest) -> list[dict[str, Any]]:
    expected_shape = str(getattr(request.output_contract, "expected_return_shape", "") or "").strip()
    schema_text = str(getattr(request.output_contract, "output_schema", "") or "").strip()
    contract_lines = []
    if expected_shape:
        contract_lines.append(f"Expected return shape:\n{expected_shape}")
    if schema_text:
        contract_lines.append(f"Output schema:\n{schema_text}")
    contract_text = "\n\n".join(contract_lines)
    user_prompt = (
        "The previous provider call rejected the assembled prompt before generation. "
        "Return a safe fallback response only. Do not answer the substantive request, do not provide "
        "advice, instructions, recommendations, or decision guidance, and do not infer facts that are "
        "not present here. If the contract includes readiness/status fields, mark the result blocked "
        "or provisional. If it includes recommendation/action fields, state that no substantive "
        "recommendation was generated because provider safety filtering blocked synthesis."
    )
    if contract_text:
        user_prompt = f"{user_prompt}\n\n{contract_text}"
    return [
        {
            "role": "system",
            "content": (
                "Provider safety fallback mode. Produce neutral, non-advisory, bounded output. "
                "Prefer valid JSON when a return shape or schema is provided."
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


def _timeout_recovery_tool_summary(
    tool: dict[str, Any],
    *,
    workspace_root: Path,
) -> str | None:
    if not tool.get("ok"):
        return None
    tool_id = str(tool.get("tool_id") or "").strip()
    arguments = dict(tool.get("arguments") or {})
    result = dict(tool.get("result") or {})
    path = _normalized_tool_path(
        result.get("path") or arguments.get("path") or arguments.get("file_path"),
        workspace_root=workspace_root,
    )
    if tool_id == "file_read":
        start_line = arguments.get("start_line")
        end_line = arguments.get("end_line")
        line_text = ""
        if start_line not in {None, ""} or end_line not in {None, ""}:
            start_text = str(start_line or 1)
            end_text = str(end_line or "EOF")
            line_text = f" lines {start_text}-{end_text}"
        return f"- file_read {path or '(unknown)'}{line_text}"
    if tool_id in {"file_edit", "file_write"}:
        return f"- {tool_id} {path or '(unknown)'}"
    if tool_id == "list_directory":
        count = result.get("count")
        if not isinstance(count, int):
            count = len(list(result.get("entries") or []))
        target = path or "."
        return f"- list_directory {target} ({count} entries)"
    if tool_id == "git_diff":
        files_changed = result.get("files_changed")
        if isinstance(files_changed, int):
            return f"- git_diff ({files_changed} files changed)"
        return "- git_diff"
    if tool_id == "shell_command":
        command = str(arguments.get("command") or "").strip()
        if command:
            return f"- shell_command {command}"
    if tool_id:
        return f"- {tool_id}"
    return None


def _provider_timeout_recovery_messages(
    request: CompletionRequest,
    *,
    tool_ids: Sequence[str],
    executed_tools: Sequence[dict[str, Any]],
    workspace_root: Path,
    timeout_seconds: float | None,
    allow_final_read: bool,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    tool_policy = _tool_use_policy(tool_ids)
    if tool_policy:
        messages.append({"role": "system", "content": tool_policy})
    if request.user_prompt:
        messages.append({"role": "user", "content": request.user_prompt})

    timeout_text = ""
    if timeout_seconds is not None:
        timeout_text = f" after {float(timeout_seconds):.2f}s"
    recovery_lines = [
        f"Recovery note: the previous provider call timed out{timeout_text}.",
        "Continue from the bounded evidence below instead of restarting broad discovery.",
    ]
    if allow_final_read:
        recovery_lines.append(
            "You may take at most one refreshed targeted `file_read` to regain exact line grounding, then the next tool call must be `file_edit` or `file_write`."
        )
    else:
        recovery_lines.append(
            "Do not restart auditing. The next tool call should be `file_edit` or `file_write`, or return the bounded result immediately if the current workspace diff is already sufficient."
        )
    recovery_lines.append("Keep the next step minimal and converge quickly.")

    evidence_sections: list[str] = []
    read_paths = _dedupe(
        _successful_read_paths(executed_tools, workspace_root=workspace_root)
    )
    if read_paths:
        evidence_sections.append(
            "Grounded file paths: " + ", ".join(read_paths[-6:])
        )
    mutation_paths = _successful_workspace_mutation_paths(
        executed_tools,
        workspace_root=workspace_root,
    )
    if mutation_paths:
        evidence_sections.append(
            "Workspace mutations already materialized: " + ", ".join(mutation_paths)
        )
    recent_summaries: list[str] = []
    for tool in reversed(executed_tools):
        summary = _timeout_recovery_tool_summary(
            tool,
            workspace_root=workspace_root,
        )
        if not summary:
            continue
        recent_summaries.append(summary)
        if len(recent_summaries) >= 8:
            break
    if recent_summaries:
        recent_summaries.reverse()
        evidence_sections.append(
            "Recent successful tool evidence:\n" + "\n".join(recent_summaries)
        )

    contract_lines: list[str] = []
    definition_of_done = str(
        getattr(request.output_contract, "definition_of_done", "") or ""
    ).strip()
    expected_shape = str(
        getattr(request.output_contract, "expected_return_shape", "") or ""
    ).strip()
    schema_text = str(
        getattr(request.output_contract, "output_schema", "") or ""
    ).strip()
    if definition_of_done:
        contract_lines.append(f"Definition of done: {definition_of_done}")
    if expected_shape:
        contract_lines.append(f"Expected return shape:\n{expected_shape}")
    if schema_text:
        contract_lines.append(f"Output schema:\n{schema_text}")
    if contract_lines:
        evidence_sections.append("\n\n".join(contract_lines))

    recovery_prompt = " ".join(recovery_lines)
    if evidence_sections:
        recovery_prompt = f"{recovery_prompt}\n\n" + "\n\n".join(evidence_sections)
    messages.append({"role": "user", "content": recovery_prompt})
    return messages


def _expected_return_shape_keys(request: CompletionRequest) -> set[str]:
    raw_shape = str(getattr(request.output_contract, "expected_return_shape", "") or "").strip()
    if not raw_shape:
        return set()
    try:
        parsed = json.loads(raw_shape)
    except Exception:
        return set()
    if not isinstance(parsed, dict):
        return set()
    return {str(key) for key in parsed}


def _provider_safety_fallback_payload(request: CompletionRequest) -> dict[str, Any] | None:
    keys = _expected_return_shape_keys(request)
    if not keys:
        return None

    if {"findings", "evidence_summary", "quality_gates"} & keys:
        payload: dict[str, Any] = {
            "findings": [
                "Provider safety filtering rejected the assembled synthesis prompt before a substantive response was generated."
            ],
            "evidence_summary": [
                "No final synthesis was produced. Previously gathered evidence, if any, should be reviewed from the run artifacts."
            ],
            "evidence_refs": [],
            "contradictions": [],
            "open_questions": [
                "Retry with a narrower, non-advisory evidence-summary objective if a substantive synthesis is still needed."
            ],
            "verification_facts": [],
            "audit_issues": [
                {
                    "kind": "scope_fit",
                    "severity": "blocking",
                    "issue": "The provider rejected the synthesis prompt before completion.",
                    "affected_claim": "final synthesis",
                    "required_follow_up": "Retry with narrower neutral framing or inspect gathered evidence manually.",
                }
            ],
            "quality_gates": [
                {
                    "gate": "final_status",
                    "status": "fail",
                    "summary": "Provider safety filtering blocked final synthesis.",
                    "evidence_ref": "",
                    "required_follow_up": "Retry with narrower neutral framing.",
                }
            ],
            "report_readiness": "blocked",
            "readiness_note": "Provider safety filtering blocked the final synthesis prompt.",
            "confidence": 0.0,
            "recommended_change": "No substantive recommendation was generated because provider safety filtering blocked synthesis.",
        }
    elif _VALIDATION_REPORT_REQUIRED_KEYS.issubset(keys):
        payload = {
            "passed": False,
            "overall_score": 0.0,
            "dimension_scores": {},
            "repair_brief": (
                "Provider safety filtering blocked the validation synthesis prompt before a substantive verdict was "
                "generated. Retry validation on the current candidate."
            ),
            "missing_requirements": [
                "Validation synthesis was blocked by provider safety filtering before a substantive verdict was produced."
            ],
            "comparison_note": "Provider safety filtering blocked the validation synthesis prompt.",
        }
    elif "candidate_fragment" in keys:
        payload = {
            "candidate_fragment": {},
            "change_summary": "Provider safety filtering rejected the coding worker prompt before a substantive candidate was generated.",
            "target_files": [],
            "test_plan": [],
            "risks": ["No code candidate was produced because provider safety filtering blocked generation."],
        }
    elif "worker_count" in keys:
        payload = {
            "public_response": "Provider safety filtering rejected the planning prompt before a substantive plan was generated.",
            "worker_count": 1,
            "worker_briefs": ["Return a bounded blocked result; provider safety filtering prevented normal planning."],
            "aggregation_focus": "Do not synthesize substantive advice or instructions; preserve the blocked status.",
            "validator_focus": "Verify the blocked status is reported clearly.",
            "pass_threshold": 1.0,
        }
    else:
        payload = {}

    for key in keys:
        if key in payload:
            continue
        lowered = key.lower()
        if lowered.endswith("s") or lowered in {"items", "refs", "risks"}:
            payload[key] = []
        elif lowered in {"confidence", "score", "pass_threshold"}:
            payload[key] = 0.0
        elif lowered in {"worker_count"}:
            payload[key] = 1
        elif lowered in {"passed"}:
            payload[key] = False
        else:
            payload[key] = "Provider safety filtering blocked generation."
    return payload


def _provider_safety_fallback_text(request: CompletionRequest) -> str:
    payload = _provider_safety_fallback_payload(request)
    if payload is not None:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return (
        "Provider safety filtering rejected the assembled prompt before generation. "
        "No substantive response was produced."
    )


def _provider_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return True
    text = str(exc or "").strip().lower()
    return "timeout" in text or "timed out" in text


def _provider_timeout_fallback_payload(request: CompletionRequest) -> dict[str, Any] | None:
    keys = _expected_return_shape_keys(request)
    if not keys:
        return None

    if {"findings", "evidence_summary", "quality_gates"} & keys:
        payload: dict[str, Any] = {
            "findings": [
                "The provider timed out before the final synthesis completed."
            ],
            "evidence_summary": [
                "No final synthesis was produced before the completion timeout. Earlier evidence, if any, should be reviewed from the run artifacts."
            ],
            "evidence_refs": [],
            "contradictions": [],
            "open_questions": [
                "Retry with a narrower objective or a higher completion-timeout budget if a substantive synthesis is still needed."
            ],
            "verification_facts": [],
            "audit_issues": [
                {
                    "kind": "method",
                    "severity": "blocking",
                    "issue": "The provider timed out before the bounded completion finished.",
                    "affected_claim": "final synthesis",
                    "required_follow_up": "Retry with narrower framing or a higher timeout budget.",
                }
            ],
            "quality_gates": [
                {
                    "gate": "final_status",
                    "status": "fail",
                    "summary": "Provider completion timed out before final synthesis finished.",
                    "evidence_ref": "",
                    "required_follow_up": "Retry with narrower framing or a higher timeout budget.",
                }
            ],
            "report_readiness": "blocked",
            "readiness_note": "The provider timed out before the final synthesis completed.",
            "confidence": 0.0,
            "recommended_change": "No substantive recommendation was generated because the provider timed out before completion.",
        }
    elif _VALIDATION_REPORT_REQUIRED_KEYS.issubset(keys):
        payload = {
            "passed": False,
            "overall_score": 0.0,
            "dimension_scores": {},
            "repair_brief": (
                "The provider timed out before validation synthesis completed. Retry validation on the current "
                "candidate."
            ),
            "missing_requirements": [
                "Validation synthesis timed out before a substantive verdict was produced."
            ],
            "comparison_note": "The provider timed out before validation synthesis completed.",
        }
    elif "candidate_fragment" in keys:
        payload = {
            "candidate_fragment": {},
            "change_summary": "The provider timed out before the coding worker produced a bounded candidate.",
            "target_files": [],
            "test_plan": [],
            "risks": [
                "No code candidate was produced because the provider timed out before completion."
            ],
        }
    elif "worker_count" in keys:
        payload = {
            "public_response": "The provider timed out before the planning step produced a substantive plan.",
            "worker_count": 1,
            "worker_briefs": [
                "Return a bounded blocked result; the provider timed out before normal planning completed."
            ],
            "aggregation_focus": "Preserve the timeout status and avoid inventing substantive work products.",
            "validator_focus": "Verify the timeout is reported clearly.",
            "pass_threshold": 1.0,
        }
    else:
        payload = {}

    for key in keys:
        if key in payload:
            continue
        lowered = key.lower()
        if lowered.endswith("s") or lowered in {"items", "refs", "risks"}:
            payload[key] = []
        elif lowered in {"confidence", "score", "pass_threshold"}:
            payload[key] = 0.0
        elif lowered in {"worker_count"}:
            payload[key] = 1
        elif lowered in {"passed"}:
            payload[key] = False
        else:
            payload[key] = "Provider completion timed out before generation finished."
    return payload


def _provider_timeout_fallback_text(request: CompletionRequest) -> str:
    payload = _provider_timeout_fallback_payload(request)
    if payload is not None:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return (
        "The provider timed out before generation finished. "
        "No substantive response was produced."
    )


def _drain_detached_asyncio_task(task: asyncio.Task[Any]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        return


def available_local_organism_tools() -> dict[str, dict[str, Any]]:
    """Return the discovered standalone tool metadata keyed by tool id."""

    return {
        tool_id: dict(metadata)
        for tool_id, (_fn, metadata) in get_all_tools().items()
    }


class LocalOrganismToolRuntime:
    """Thin adapter over the standalone ``dan.tools`` modules."""

    def __init__(
        self,
        *,
        tool_ids: Sequence[str] | None = None,
        workspace_root: str | Path | None = None,
        approval_callback: ToolApprovalCallback | None = None,
        event_callback: ToolRuntimeEventCallback | None = None,
    ) -> None:
        available = get_all_tools()
        self._workspace_root = Path(workspace_root or ".").expanduser().resolve()
        selected = _workspace_tool_ids(
            tool_ids or DEFAULT_LIVE_ORGANISM_TOOL_IDS,
            workspace_root=self._workspace_root,
        )
        missing = [tool_id for tool_id in selected if tool_id not in available]
        if missing:
            raise ValueError(
                "Unknown local organism tools: "
                + ", ".join(sorted(missing))
            )
        self._tools = {
            tool_id: available[tool_id]
            for tool_id in selected
        }
        self._approval_callback = approval_callback
        self._event_callback = event_callback

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def tool_ids(self) -> list[str]:
        return list(self._tools)

    def metadata_for(self, tool_id: str) -> dict[str, Any]:
        _fn, metadata = self._tools[tool_id]
        return dict(metadata)

    def _emit_event(self, event: str, **payload: Any) -> None:
        if self._event_callback is None:
            return
        self._event_callback({"event": event, **payload})

    def _normalize_git_tool_path(self, arguments: dict[str, Any]) -> dict[str, Any]:
        kwargs = dict(arguments or {})
        raw_path = str(kwargs.get("path") or "").strip()
        if not raw_path:
            kwargs["path"] = str(self._workspace_root)
            return kwargs
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            kwargs["path"] = str((self._workspace_root / candidate).resolve())
            return kwargs
        kwargs["path"] = str(candidate.resolve())
        return kwargs

    @staticmethod
    def _normalize_tool_arguments(
        tool_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        kwargs = dict(arguments or {})
        if tool_id == "file_read":
            if "path" not in kwargs and str(kwargs.get("file_path") or "").strip():
                kwargs["path"] = str(kwargs.get("file_path")).strip()
            start_line = kwargs.get("start_line")
            end_line = kwargs.get("end_line")
            if start_line is None and kwargs.get("offset") is not None:
                try:
                    offset = int(kwargs.get("offset"))
                except (TypeError, ValueError):
                    offset = None
                if offset is not None:
                    kwargs["start_line"] = max(offset, 0) + 1
                    start_line = kwargs["start_line"]
            if end_line is None and start_line is not None and kwargs.get("limit") is not None:
                try:
                    limit = int(kwargs.get("limit"))
                except (TypeError, ValueError):
                    limit = None
                if limit is not None and limit > 0:
                    kwargs["end_line"] = int(start_line) + limit - 1
            return kwargs
        if tool_id != "web_search":
            return kwargs

        queries = kwargs.pop("queries", None)
        query = kwargs.get("query")
        if isinstance(query, (list, tuple)):
            query = next(
                (str(item).strip() for item in query if str(item).strip()),
                "",
            )
        if not str(query or "").strip():
            if isinstance(queries, str):
                candidates = [queries]
            elif isinstance(queries, (list, tuple)):
                candidates = [str(item) for item in queries]
            else:
                candidates = []
            query = next((item.strip() for item in candidates if item.strip()), "")
        if str(query or "").strip():
            kwargs["query"] = str(query).strip()
        url = kwargs.get("url")
        if isinstance(url, (list, tuple)):
            url = next(
                (str(item).strip() for item in url if str(item).strip()),
                "",
            )
        if str(url or "").strip():
            kwargs["url"] = str(url).strip()
        return kwargs

    async def call(
        self,
        tool_id: str,
        arguments: dict[str, Any] | None = None,
        *,
        worker_id: str | None = None,
        tool_call_id: str | None = None,
        parent_model_call_id: str | None = None,
        event_context: dict[str, Any] | None = None,
    ) -> Any:
        if tool_id not in self._tools:
            raise KeyError(f"Tool '{tool_id}' is not enabled for this runtime")

        function, metadata = self._tools[tool_id]
        kwargs = self._normalize_tool_arguments(tool_id, dict(arguments or {}))
        shared_context = _compact_event_payload(
            {
                **dict(event_context or {}),
                "tool_call_id": _event_text(tool_call_id),
                "parent_model_call_id": _event_text(parent_model_call_id),
                "worker_id": str(worker_id or "").strip() or None,
            }
        )
        if tool_id == "shell_command" and not str(kwargs.get("working_directory") or "").strip():
            kwargs["working_directory"] = str(self._workspace_root)
        elif tool_id in {"git_status", "git_diff", "git_log"}:
            kwargs = self._normalize_git_tool_path(kwargs)
        self._emit_event(
            "tool.started",
            tool_id=tool_id,
            arguments=dict(kwargs),
            metadata=dict(metadata),
            workspace_root=str(self._workspace_root),
            **shared_context,
        )
        missing_required = _missing_required_tool_arguments(dict(metadata), kwargs)
        missing_alternatives = _missing_alternative_required_tool_argument_groups(
            dict(metadata),
            kwargs,
        )
        if missing_required or missing_alternatives:
            error_text = _tool_argument_validation_error(
                tool_id,
                missing_required,
                missing_alternatives,
            )
            self._emit_event(
                "tool.failed",
                tool_id=tool_id,
                arguments=dict(kwargs),
                metadata=dict(metadata),
                error=error_text,
                **shared_context,
            )
            raise ValueError(error_text)
        if self._approval_callback is not None:
            approved = self._approval_callback(tool_id, dict(kwargs), dict(metadata))
            if not approved:
                self._emit_event(
                    "tool.denied",
                    tool_id=tool_id,
                    arguments=dict(kwargs),
                    metadata=dict(metadata),
                    **shared_context,
                )
                raise PermissionError(f"tool_call_denied:{tool_id}")
        prior_workspace = os.environ.get("DAN_WORKSPACE_ROOT")
        os.environ["DAN_WORKSPACE_ROOT"] = str(self._workspace_root)
        try:
            result = await function(**kwargs)
        except Exception as exc:
            self._emit_event(
                "tool.failed",
                tool_id=tool_id,
                arguments=dict(kwargs),
                metadata=dict(metadata),
                error=f"{type(exc).__name__}: {exc}",
                **shared_context,
            )
            raise
        finally:
            if prior_workspace is None:
                os.environ.pop("DAN_WORKSPACE_ROOT", None)
            else:
                os.environ["DAN_WORKSPACE_ROOT"] = prior_workspace
        self._emit_event(
            "tool.completed",
            tool_id=tool_id,
            arguments=dict(kwargs),
            metadata=dict(metadata),
            result=result,
            **shared_context,
        )
        return result


class ToolLoopCompletionProvider:
    """Completion provider that runs a local tool loop around a provider call."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        tool_runtime: LocalOrganismToolRuntime,
        default_model: str,
        max_rounds: int | None = None,
        max_tool_calls: int = 24,
        completion_timeout_seconds: float | None = None,
        stream_text_responses: bool = False,
        provider_request_overrides: dict[str, Any] | None = None,
        event_callback: ToolRuntimeEventCallback | None = None,
    ) -> None:
        self._provider = provider
        self._tool_runtime = tool_runtime
        self._default_model = str(default_model or "").strip()
        self._max_rounds = (
            None
            if max_rounds is None or int(max_rounds) <= 0
            else max(1, int(max_rounds))
        )
        self._max_tool_calls = max(1, int(max_tool_calls))
        self._completion_timeout_seconds = (
            None
            if completion_timeout_seconds is None
            else max(0.01, float(completion_timeout_seconds))
        )
        self._stream_text_responses = bool(stream_text_responses)
        self._provider_request_overrides = dict(provider_request_overrides or {})
        self._event_callback = event_callback
        self._model_call_counter = 0

    def _emit_event(self, event: str, **payload: Any) -> None:
        if self._event_callback is None:
            return
        self._event_callback({"event": event, **payload})

    def _next_model_call_id(self) -> str:
        self._model_call_counter += 1
        return f"model-call:{self._model_call_counter:04d}"

    @staticmethod
    def _event_context(
        request: CompletionRequest,
        *,
        worker_id: str | None,
    ) -> dict[str, Any]:
        return _compact_event_payload(
            {
                **organism_event_context(
                    metadata=request.metadata,
                    output_contract=request.output_contract,
                    worker_id=worker_id,
                ),
                "worker_id": _event_text(worker_id),
                "contract_id": stable_output_contract_id(request.output_contract),
            }
        )

    async def _complete_text_response(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        request: CompletionRequest,
        provider_kwargs: dict[str, Any],
        round_number: int,
        worker_id: str | None,
        model_call_id: str,
        event_context: dict[str, Any],
    ) -> CompletionResult:
        if not self._stream_text_responses:
            return await self._provider.complete(
                messages=messages,
                model=model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                **provider_kwargs,
            )

        stream_method = getattr(self._provider, "stream", None)
        if not callable(stream_method):
            return await self._provider.complete(
                messages=messages,
                model=model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                **provider_kwargs,
            )

        accumulated = ""
        usage: dict[str, Any] | None = None
        emitted_delta = False
        saw_stream_output = False
        finish_reason = ""
        tool_calls: list[dict[str, Any]] | None = None
        raw_assistant_message: dict[str, Any] | None = None
        streamed_model = model
        provider_metadata: dict[str, Any] | None = None
        self._emit_event(
            "model.stream.started",
            model=model,
            round=round_number,
            model_call_id=model_call_id,
            **event_context,
        )
        try:
            async for chunk in stream_method(
                messages=messages,
                model=model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                **provider_kwargs,
            ):
                saw_stream_output = True
                delta = str(getattr(chunk, "delta", "") or "")
                accumulated = str(
                    getattr(chunk, "accumulated", accumulated + delta) or accumulated + delta
                )
                usage_candidate = getattr(chunk, "usage", None)
                if usage_candidate:
                    usage = dict(usage_candidate)
                chunk_model = str(getattr(chunk, "model", "") or "").strip()
                if chunk_model:
                    streamed_model = chunk_model
                chunk_finish_reason = str(getattr(chunk, "finish_reason", "") or "").strip()
                if chunk_finish_reason:
                    finish_reason = chunk_finish_reason
                chunk_tool_calls = getattr(chunk, "tool_calls", None)
                if chunk_tool_calls is not None:
                    tool_calls = list(chunk_tool_calls)
                chunk_raw_message = getattr(chunk, "raw_assistant_message", None)
                if isinstance(chunk_raw_message, dict):
                    raw_assistant_message = dict(chunk_raw_message)
                chunk_provider_metadata = getattr(chunk, "provider_metadata", None)
                if isinstance(chunk_provider_metadata, dict):
                    provider_metadata = dict(chunk_provider_metadata)
                if delta:
                    emitted_delta = True
                    self._emit_event(
                        "model.stream.delta",
                        model=model,
                        round=round_number,
                        model_call_id=model_call_id,
                        delta=delta,
                        accumulated=accumulated,
                        **event_context,
                    )
            self._emit_event(
                "model.stream.completed",
                model=model,
                round=round_number,
                model_call_id=model_call_id,
                usage=usage,
                **event_context,
            )
            if raw_assistant_message is None:
                raw_assistant_message = {
                    "role": "assistant",
                    "content": accumulated if accumulated.strip() else (None if tool_calls else accumulated),
                }
                if tool_calls:
                    raw_assistant_message["tool_calls"] = list(tool_calls)
            merged_provider_metadata = dict(provider_metadata or {})
            merged_provider_metadata["streamed_response"] = True
            return CompletionResult(
                text=accumulated,
                usage=usage,
                model=streamed_model or model,
                tool_calls=tool_calls,
                finish_reason=finish_reason or "stream",
                raw_assistant_message=raw_assistant_message,
                provider_metadata=merged_provider_metadata,
            )
        except Exception:
            if emitted_delta or saw_stream_output:
                raise
            return await self._provider.complete(
                messages=messages,
                model=model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                **provider_kwargs,
            )

    async def _complete_text_response_with_timeout(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        request: CompletionRequest,
        provider_kwargs: dict[str, Any],
        round_number: int,
        worker_id: str | None,
        model_call_id: str,
        event_context: dict[str, Any],
    ) -> CompletionResult:
        if self._completion_timeout_seconds is None:
            return await self._complete_text_response(
                messages=messages,
                model=model,
                request=request,
                provider_kwargs=provider_kwargs,
                round_number=round_number,
                worker_id=worker_id,
                model_call_id=model_call_id,
                event_context=event_context,
            )
        completion_task = asyncio.create_task(
            self._complete_text_response(
                messages=messages,
                model=model,
                request=request,
                provider_kwargs=provider_kwargs,
                round_number=round_number,
                worker_id=worker_id,
                model_call_id=model_call_id,
                event_context=event_context,
            )
        )
        done, _pending = await asyncio.wait(
            {completion_task},
            timeout=self._completion_timeout_seconds,
        )
        if completion_task in done:
            return await completion_task
        completion_task.cancel()
        completion_task.add_done_callback(_drain_detached_asyncio_task)
        raise TimeoutError(
            f"provider_completion_timeout:{self._completion_timeout_seconds:.2f}s"
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        model = str(request.model or self._default_model or "").strip()
        if not model:
            raise ValueError("Tool-loop completion provider requires a concrete model")
        worker_id = str(request.metadata.get("worker_id") or "").strip() or None
        event_context = self._event_context(request, worker_id=worker_id)

        requested_tool_schemas = self._resolve_tool_schemas(request.tools)
        tool_schemas = _validator_read_only_tool_schemas(
            request,
            requested_tool_schemas,
        )
        if len(tool_schemas) != len(requested_tool_schemas):
            self._emit_event(
                "toolloop.validator_tools_narrowed",
                enabled_tools=[
                    _tool_schema_name(tool)
                    for tool in tool_schemas
                    if _tool_schema_name(tool)
                ],
                dropped_tools=[
                    _tool_schema_name(tool)
                    for tool in requested_tool_schemas
                    if _tool_schema_name(tool)
                    and _tool_schema_name(tool)
                    not in {
                        _tool_schema_name(candidate)
                        for candidate in tool_schemas
                        if _tool_schema_name(candidate)
                    }
                ],
                **event_context,
            )
        active_tool_schemas = list(tool_schemas)
        messages: list[dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        tool_policy = _tool_use_policy(
            [
                str(tool.get("function", {}).get("name") or "").strip()
                for tool in tool_schemas
                if isinstance(tool, dict)
            ]
        )
        if tool_policy:
            messages.append({"role": "system", "content": tool_policy})
        messages.append({"role": "user", "content": request.user_prompt})

        executed_tools: list[dict[str, Any]] = []
        rounds = 0
        total_tool_calls = 0
        usage_totals: dict[str, int] = {}
        stop_reason = "completed"
        last_result: Any = None
        forced_finalize_without_tools = False
        write_stage_first_write_nudged = False
        write_stage_final_read_available = False
        write_stage_direct_write_required = False
        write_stage_direct_write_reprompted = False
        research_note_finalize_nudged = False
        provider_safety_retry_attempted = False
        provider_timeout_recovery_attempted = False
        blocked_by_tool_call_ids: list[str] = []
        blocked_tool_counts: dict[str, int] = {}
        disabled_tool_ids: set[str] = set()
        file_write_incremental_edit_paths: dict[str, str] = {}

        def _completion_raw(
            *,
            provider_model: Any,
            finish_reason: Any,
            usage: Any,
            provider_metadata: dict[str, Any] | None,
            assistant_message: dict[str, Any],
            stop_reason_value: str,
        ) -> dict[str, Any]:
            return {
                "provider_result": {
                    "model": provider_model,
                    "finish_reason": finish_reason,
                    "usage": usage,
                    "provider_metadata": provider_metadata,
                },
                "assistant_message": assistant_message,
                "executed_tools": executed_tools,
                "stop_reason": stop_reason_value,
                "usage_totals": dict(usage_totals),
            }

        while True:
            request_tool_schemas = _enabled_tool_schemas(
                active_tool_schemas,
                disabled_tool_ids=sorted(disabled_tool_ids),
            )
            request_tool_ids = [
                _tool_schema_name(tool)
                for tool in request_tool_schemas
                if _tool_schema_name(tool)
            ]
            model_call_id = self._next_model_call_id()
            self._emit_event(
                "model.requested",
                model=model,
                round=rounds + 1,
                tool_count=len(request_tool_schemas),
                model_call_id=model_call_id,
                blocked_by_tool_call_ids=list(blocked_by_tool_call_ids) or None,
                **event_context,
            )
            blocked_by_tool_call_ids = []
            provider_kwargs = {
                "tools": request_tool_schemas or None,
                **self._provider_request_overrides,
            }
            try:
                last_result = await self._complete_text_response_with_timeout(
                    messages=apply_cache_hints(self._provider, list(messages)),
                    model=model,
                    request=request,
                    provider_kwargs=provider_kwargs,
                    round_number=rounds + 1,
                    worker_id=worker_id,
                    model_call_id=model_call_id,
                    event_context=event_context,
                )
            except Exception as exc:
                if _provider_timeout_error(exc):
                    timeout_seconds = self._completion_timeout_seconds
                    self._emit_event(
                        "model.timeout",
                        model=model,
                        round=rounds + 1,
                        model_call_id=model_call_id,
                        timeout_seconds=timeout_seconds,
                        tool_count=len(request_tool_schemas),
                        error_type=type(exc).__name__,
                        **event_context,
                    )
                    can_retry_timeout = (
                        not provider_timeout_recovery_attempted
                        and _coding_output_kind(request) is not None
                        and any(tool_id in {"file_edit", "file_write"} for tool_id in request_tool_ids)
                        and not _successful_workspace_mutation_paths(
                            executed_tools,
                            workspace_root=self._tool_runtime.workspace_root,
                        )
                        and sum(1 for tool in executed_tools if tool.get("ok")) >= 2
                    )
                    if can_retry_timeout:
                        provider_timeout_recovery_attempted = True
                        recovered_tool_schemas = _write_stage_tool_schemas(
                            tool_schemas,
                            allow_final_read=True,
                        )
                        if recovered_tool_schemas:
                            active_tool_schemas = recovered_tool_schemas
                        disabled_tool_ids.clear()
                        write_stage_first_write_nudged = True
                        write_stage_final_read_available = any(
                            _tool_schema_name(tool) == "file_read"
                            for tool in active_tool_schemas
                        )
                        write_stage_direct_write_required = (
                            not write_stage_final_read_available
                        )
                        messages = _provider_timeout_recovery_messages(
                            request,
                            tool_ids=[
                                _tool_schema_name(tool)
                                for tool in active_tool_schemas
                                if _tool_schema_name(tool)
                            ],
                            executed_tools=executed_tools,
                            workspace_root=self._tool_runtime.workspace_root,
                            timeout_seconds=timeout_seconds,
                            allow_final_read=write_stage_final_read_available,
                        )
                        recover = getattr(self._provider, "recover_from_error", None)
                        if callable(recover):
                            try:
                                maybe_result = recover(exc)
                                if inspect.isawaitable(maybe_result):
                                    await maybe_result
                            except Exception:
                                pass
                        self._emit_event(
                            "model.timeout_recovery_retry",
                            model=model,
                            round=rounds + 1,
                            model_call_id=model_call_id,
                            timeout_seconds=timeout_seconds,
                            allow_final_read=write_stage_final_read_available,
                            tool_count=len(active_tool_schemas),
                            **event_context,
                        )
                        continue
                    stop_reason = "provider_completion_timeout"
                    partial_candidate = _partial_coding_candidate_from_tool_evidence(
                        request=request,
                        executed_tools=executed_tools,
                        stop_reason=stop_reason,
                        workspace_root=self._tool_runtime.workspace_root,
                    )
                    fallback_text = (
                        json.dumps(partial_candidate, ensure_ascii=False, sort_keys=True)
                        if partial_candidate is not None
                        else _provider_timeout_fallback_text(request)
                    )
                    self._emit_event(
                        "completion.completed",
                        model=model,
                        model_call_id=model_call_id,
                        stop_reason=stop_reason,
                        tool_calls_executed=len(executed_tools),
                        **event_context,
                    )
                    return CompletionResponse(
                        text=fallback_text,
                        raw=_completion_raw(
                            provider_model=model,
                            finish_reason="provider_completion_timeout",
                            usage=None,
                            provider_metadata={
                                "provider_timeout_fallback": True,
                                "timeout_seconds": timeout_seconds,
                                "error_type": type(exc).__name__,
                                "tool_evidence_fallback": partial_candidate is not None,
                            },
                            assistant_message={
                                "role": "assistant",
                                "content": fallback_text,
                            },
                            stop_reason_value=stop_reason,
                        ),
                    )
                if not _provider_prompt_filter_error(exc):
                    raise
                self._emit_event(
                    "model.provider_prompt_rejected",
                    model=model,
                    round=rounds + 1,
                    model_call_id=model_call_id,
                    error_type=type(exc).__name__,
                    retry=not provider_safety_retry_attempted,
                    **event_context,
                )
                if provider_safety_retry_attempted:
                    stop_reason = "provider_prompt_rejected_after_safety_retry"
                    fallback_text = _provider_safety_fallback_text(request)
                    self._emit_event(
                        "completion.completed",
                        model=model,
                        model_call_id=model_call_id,
                        stop_reason=stop_reason,
                        tool_calls_executed=len(executed_tools),
                        **event_context,
                    )
                    return CompletionResponse(
                        text=fallback_text,
                        raw=_completion_raw(
                            provider_model=model,
                            finish_reason="provider_prompt_rejected",
                            usage=None,
                            provider_metadata={
                                "provider_safety_fallback": True,
                                "error_type": type(exc).__name__,
                            },
                            assistant_message={
                                "role": "assistant",
                                "content": fallback_text,
                            },
                            stop_reason_value=stop_reason,
                        ),
                    )
                provider_safety_retry_attempted = True
                active_tool_schemas = []
                disabled_tool_ids.clear()
                messages = _provider_safety_retry_messages(request)
                stop_reason = "completed_after_provider_safety_retry"
                self._emit_event(
                    "model.provider_safety_retry",
                    model=model,
                    round=rounds + 1,
                    model_call_id=model_call_id,
                    **event_context,
                )
                continue
            usage_totals = _merge_usage_totals(
                usage_totals,
                getattr(last_result, "usage", None),
            )
            assistant_message = self._assistant_message(last_result)
            messages.append(assistant_message)

            tool_calls = list(last_result.tool_calls or [])
            self._emit_event(
                "model.responded",
                model=last_result.model or model,
                round=rounds + 1,
                model_call_id=model_call_id,
                tool_calls=[call.get("function", {}).get("name") or call.get("name") for call in tool_calls if isinstance(call, dict)],
                finish_reason=getattr(last_result, "finish_reason", None),
                text=(last_result.text or "")[:400],
                streamed=bool((getattr(last_result, "provider_metadata", None) or {}).get("streamed_response")),
                **event_context,
            )
            if forced_finalize_without_tools and not request_tool_schemas and tool_calls:
                stop_reason = "forced_finalize_guardrail_unheeded"
                partial_candidate = _partial_coding_candidate_from_tool_evidence(
                    request=request,
                    executed_tools=executed_tools,
                    stop_reason=stop_reason,
                    existing_text=last_result.text or "",
                    workspace_root=self._tool_runtime.workspace_root,
                )
                fallback_text = (
                    json.dumps(partial_candidate, ensure_ascii=False, sort_keys=True)
                    if partial_candidate is not None
                    else (last_result.text or "")
                )
                self._emit_event(
                    "completion.completed",
                    model=last_result.model or model,
                    model_call_id=model_call_id,
                    stop_reason=stop_reason,
                    tool_calls_executed=len(executed_tools),
                    **event_context,
                )
                return CompletionResponse(
                    text=fallback_text,
                    raw=_completion_raw(
                        provider_model=last_result.model,
                        finish_reason=last_result.finish_reason,
                        usage=last_result.usage,
                        provider_metadata={
                            **dict(last_result.provider_metadata or {}),
                            "tool_evidence_fallback": partial_candidate is not None,
                        },
                        assistant_message=assistant_message,
                        stop_reason_value=stop_reason,
                    ),
                )
            if not tool_calls:
                direct_write_required_reason = (
                    _write_capable_coding_stage_direct_write_required_reason(
                        request=request,
                        tool_ids=request_tool_ids,
                        executed_tools=executed_tools,
                        workspace_root=self._tool_runtime.workspace_root,
                        direct_write_required=write_stage_direct_write_required,
                    )
                )
                if direct_write_required_reason is not None:
                    if not write_stage_direct_write_reprompted:
                        write_stage_direct_write_reprompted = True
                        direct_write_tool_schemas = _direct_write_tool_schemas(
                            tool_schemas
                        )
                        if direct_write_tool_schemas:
                            active_tool_schemas = direct_write_tool_schemas
                            disabled_tool_ids.clear()
                        messages.append(
                            {
                                "role": "user",
                                "content": _write_capable_coding_stage_direct_write_required_message(
                                    direct_write_required_reason
                                ),
                            }
                        )
                        self._emit_event(
                            "toolloop.write_stage_direct_write_nudged",
                            reason=direct_write_required_reason,
                            tool_calls_executed=len(executed_tools),
                            enabled_tools=[
                                str(tool.get("function", {}).get("name") or "").strip()
                                for tool in active_tool_schemas
                                if isinstance(tool, dict)
                            ],
                            blocked_by_tool_call_ids=list(blocked_by_tool_call_ids) or None,
                            **event_context,
                        )
                        continue

                    stop_reason = "write_stage_direct_write_guardrail_unheeded"
                    fallback_payload = _write_stage_unmaterialized_candidate_payload(
                        request,
                        existing_text=last_result.text or "",
                        stop_reason=stop_reason,
                    )
                    fallback_text = (
                        json.dumps(fallback_payload, ensure_ascii=False, sort_keys=True)
                        if fallback_payload is not None
                        else (last_result.text or "")
                    )
                    self._emit_event(
                        "completion.completed",
                        model=last_result.model or model,
                        model_call_id=model_call_id,
                        stop_reason=stop_reason,
                        tool_calls_executed=len(executed_tools),
                        **event_context,
                    )
                    return CompletionResponse(
                        text=fallback_text,
                        raw=_completion_raw(
                            provider_model=last_result.model,
                            finish_reason=last_result.finish_reason,
                            usage=last_result.usage,
                            provider_metadata={
                                **dict(last_result.provider_metadata or {}),
                                "write_stage_direct_write_fallback": True,
                            },
                            assistant_message=assistant_message,
                            stop_reason_value=stop_reason,
                        ),
                    )
                self._emit_event(
                    "completion.completed",
                    model=last_result.model or model,
                    model_call_id=model_call_id,
                    stop_reason=stop_reason,
                    tool_calls_executed=len(executed_tools),
                    **event_context,
                )
                return CompletionResponse(
                    text=last_result.text,
                    raw=_completion_raw(
                        provider_model=last_result.model,
                        finish_reason=last_result.finish_reason,
                        usage=last_result.usage,
                        provider_metadata=last_result.provider_metadata,
                        assistant_message=assistant_message,
                        stop_reason_value=stop_reason,
                    ),
                )

            rounds += 1
            if self._max_rounds is not None and rounds > self._max_rounds:
                stop_reason = f"max_tool_rounds_exceeded:{self._max_rounds}"
                partial_candidate = _partial_coding_candidate_from_tool_evidence(
                    request=request,
                    executed_tools=executed_tools,
                    stop_reason=stop_reason,
                    existing_text=last_result.text or "",
                    workspace_root=self._tool_runtime.workspace_root,
                )
                fallback_text = (
                    json.dumps(partial_candidate, ensure_ascii=False, sort_keys=True)
                    if partial_candidate is not None
                    else (last_result.text or "")
                )
                self._emit_event(
                    "completion.completed",
                    model=last_result.model or model,
                    model_call_id=model_call_id,
                    stop_reason=stop_reason,
                    tool_calls_executed=len(executed_tools),
                    **event_context,
                )
                return CompletionResponse(
                    text=fallback_text,
                    raw=_completion_raw(
                        provider_model=last_result.model,
                        finish_reason=last_result.finish_reason,
                        usage=last_result.usage,
                        provider_metadata={
                            **dict(last_result.provider_metadata or {}),
                            "tool_evidence_fallback": partial_candidate is not None,
                        },
                        assistant_message=assistant_message,
                        stop_reason_value=stop_reason,
                    ),
                )
            round_tool_call_ids: list[str] = []
            invalid_tool_argument_nudges: list[tuple[str, str]] = []
            availability_tool_nudges: list[tuple[str, str]] = []
            repeated_tool_call_nudges: list[tuple[str, str]] = []
            write_stage_helper_path_nudges: list[tuple[str, str, str]] = []
            file_write_downshift_nudges: list[tuple[str, str]] = []
            newly_disabled_tool_ids: list[str] = []
            write_stage_final_read_consumed = False

            def _record_tool_result(
                *,
                tool_id: str,
                tool_call_id: str,
                arguments: dict[str, Any],
                tool_payload: dict[str, Any],
            ) -> dict[str, Any]:
                executed_tools.append(
                    {
                        "tool_id": tool_id,
                        "tool_call_id": tool_call_id,
                        "model_call_id": model_call_id,
                        "arguments": arguments,
                        **tool_payload,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": tool_id,
                        "content": json.dumps(
                            _compact_tool_payload_for_prompt(tool_payload),
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ),
                    }
                )
                return executed_tools[-1]

            def _skip_remaining_tool_calls(
                remaining_calls: Sequence[dict[str, Any]],
                *,
                reason: str,
            ) -> None:
                for skipped_call in remaining_calls:
                    skipped_tool_id, skipped_tool_call_id, skipped_arguments = self._parse_tool_call(
                        skipped_call
                    )
                    round_tool_call_ids.append(skipped_tool_call_id)
                    _record_tool_result(
                        tool_id=skipped_tool_id,
                        tool_call_id=skipped_tool_call_id,
                        arguments=skipped_arguments,
                        tool_payload={
                            "ok": False,
                            "error": f"tool_call_skipped:{reason}",
                        },
                    )

            for call_index, raw_call in enumerate(tool_calls):
                total_tool_calls += 1
                tool_id, tool_call_id, arguments = self._parse_tool_call(raw_call)
                round_tool_call_ids.append(tool_call_id)
                if total_tool_calls > self._max_tool_calls:
                    tool_payload = {
                        "ok": False,
                        "error": f"tool_call_limit_exceeded:{self._max_tool_calls}",
                    }
                elif tool_id not in self._tool_runtime.tool_ids:
                    tool_payload = {
                        "ok": False,
                        "error": f"tool_not_enabled:{tool_id}",
                    }
                elif tool_id not in request_tool_ids:
                    tool_payload = {
                        "ok": False,
                        "error": f"tool_temporarily_disabled:{tool_id}",
                    }
                elif (
                    tool_id == "file_write"
                    and (
                        target_path := _existing_workspace_file_write_target_path(
                            raw_call,
                            arguments=arguments,
                            workspace_root=self._tool_runtime.workspace_root,
                        )
                    )
                    is not None
                    and (
                        downshift_reason := file_write_incremental_edit_paths.get(target_path)
                    )
                    is not None
                ):
                    tool_payload = {
                        "ok": False,
                        "error": f"file_write_downshift_required:{downshift_reason}",
                    }
                    file_write_downshift_nudges.append((target_path, downshift_reason))
                elif (
                    tool_id in {"file_edit", "file_write"}
                    and (write_stage_first_write_nudged or write_stage_direct_write_required)
                    and _coding_output_kind(request) is not None
                    and not _successful_workspace_mutation_paths(
                        executed_tools,
                        workspace_root=self._tool_runtime.workspace_root,
                    )
                    and (
                        helper_reason := _write_stage_direct_write_helper_path_reason(
                            arguments,
                            workspace_root=self._tool_runtime.workspace_root,
                        )
                    )
                    is not None
                ):
                    tool_payload = {
                        "ok": False,
                        "error": f"write_stage_helper_path_blocked:{helper_reason}",
                    }
                    write_stage_helper_path_nudges.append(
                        (
                            tool_id,
                            helper_reason,
                            _write_stage_direct_write_helper_path_message(
                                tool_id=tool_id,
                                reason=helper_reason,
                                arguments=arguments,
                            ),
                        )
                    )
                else:
                    try:
                        result = await self._tool_runtime.call(
                            tool_id,
                            arguments,
                            worker_id=worker_id,
                            tool_call_id=tool_call_id,
                            parent_model_call_id=model_call_id,
                            event_context=event_context,
                        )
                    except Exception as exc:
                        tool_payload = {
                            "ok": False,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    else:
                        tool_payload = {
                            "ok": True,
                            "result": result,
                        }

                current_tool = _record_tool_result(
                    tool_id=tool_id,
                    tool_call_id=tool_call_id,
                    arguments=arguments,
                    tool_payload=tool_payload,
                )
                if write_stage_helper_path_nudges:
                    write_stage_direct_write_required = True
                    _skip_remaining_tool_calls(
                        tool_calls[call_index + 1 :],
                        reason="write_stage_helper_path_blocked",
                    )
                    break
                if (
                    current_tool.get("ok")
                    and len(executed_tools) >= 2
                    and _is_repeated_tool_call(executed_tools[-2], current_tool)
                ):
                    repeated_tool_call_nudges.append(
                        (tool_id, _repeated_tool_call_nudge(tool_id, arguments))
                    )
                if (
                    write_stage_first_write_nudged
                    and write_stage_final_read_available
                    and current_tool.get("ok")
                    and tool_id == "file_read"
                    and not _successful_workspace_mutation_paths(
                        executed_tools,
                        workspace_root=self._tool_runtime.workspace_root,
                    )
                ):
                    write_stage_final_read_available = False
                    write_stage_final_read_consumed = True
                    write_stage_direct_write_required = True
                    _skip_remaining_tool_calls(
                        tool_calls[call_index + 1 :],
                        reason="write_stage_final_read_consumed",
                    )
                    # Stop immediately after the single allowed post-nudge read so the
                    # next round is forced back into direct-write mode.
                    break
                if not tool_payload.get("ok"):
                    error_text = str(tool_payload.get("error") or "").strip()
                    if tool_id == "file_write":
                        downshift = _file_write_invalid_large_overwrite_downshift(
                            raw_call,
                            arguments=arguments,
                            finish_reason=last_result.finish_reason,
                            workspace_root=self._tool_runtime.workspace_root,
                            file_edit_available="file_edit" in request_tool_ids,
                        )
                        if downshift is not None:
                            downshift_path, downshift_reason = downshift
                            file_write_incremental_edit_paths[downshift_path] = downshift_reason
                            file_write_downshift_nudges.append(
                                (downshift_path, downshift_reason)
                            )
                    if error_text.startswith("file_write_downshift_required:"):
                        _skip_remaining_tool_calls(
                            tool_calls[call_index + 1 :],
                            reason="file_write_downshift_required",
                        )
                        break
                    followup_message = _tool_argument_failure_nudge(tool_id, error_text)
                    if followup_message:
                        invalid_tool_argument_nudges.append((tool_id, followup_message))
                        _skip_remaining_tool_calls(
                            tool_calls[call_index + 1 :],
                            reason="invalid_tool_argument_nudge",
                        )
                        # Stop immediately after the first schema-invalid tool call so the
                        # model gets the repair hint before we burn the rest of the round on
                        # more malformed calls from the same batch.
                        break
                    followup_message = _tool_availability_failure_nudge(
                        tool_id,
                        error_text,
                        enabled_tool_ids=request_tool_ids,
                    )
                    if followup_message:
                        availability_tool_nudges.append((tool_id, followup_message))
                        _skip_remaining_tool_calls(
                            tool_calls[call_index + 1 :],
                            reason="tool_availability_nudge",
                        )
                        # Stop immediately after the first unavailable-tool call so the
                        # model sees the narrowed tool basket before it burns the rest of the
                        # round on more unavailable calls.
                        break
            if file_write_downshift_nudges:
                unique_messages = []
                seen_messages: set[str] = set()
                affected_paths: list[str] = []
                reasons: list[str] = []
                for path, reason in file_write_downshift_nudges:
                    affected_paths.append(path)
                    reasons.append(reason)
                    message = _file_write_incremental_edit_required_message(path, reason)
                    if message in seen_messages:
                        continue
                    seen_messages.add(message)
                    unique_messages.append(message)
                messages.append(
                    {
                        "role": "user",
                        "content": "\n".join(unique_messages),
                    }
                )
                self._emit_event(
                    "toolloop.file_write_downshift_nudged",
                    paths=_dedupe(affected_paths),
                    reasons=_dedupe(reasons),
                    enabled_tools=list(request_tool_ids),
                    blocked_by_tool_call_ids=list(round_tool_call_ids) or None,
                    tool_calls_executed=len(executed_tools),
                    **event_context,
                )
            if write_stage_helper_path_nudges:
                unique_messages = []
                seen_messages: set[str] = set()
                affected_tool_ids: list[str] = []
                reasons: list[str] = []
                for tool_id, reason, message in write_stage_helper_path_nudges:
                    affected_tool_ids.append(tool_id)
                    reasons.append(reason)
                    if message in seen_messages:
                        continue
                    seen_messages.add(message)
                    unique_messages.append(message)
                direct_write_tool_schemas = _direct_write_tool_schemas(tool_schemas)
                if direct_write_tool_schemas:
                    active_tool_schemas = direct_write_tool_schemas
                    disabled_tool_ids.clear()
                messages.append(
                    {
                        "role": "user",
                        "content": "\n".join(unique_messages),
                    }
                )
                self._emit_event(
                    "toolloop.write_stage_helper_path_blocked",
                    tool_ids=_dedupe(affected_tool_ids),
                    reasons=_dedupe(reasons),
                    enabled_tools=[
                        str(tool.get("function", {}).get("name") or "").strip()
                        for tool in active_tool_schemas
                        if isinstance(tool, dict)
                    ],
                    blocked_by_tool_call_ids=list(round_tool_call_ids) or None,
                    tool_calls_executed=len(executed_tools),
                    **event_context,
                )
            if invalid_tool_argument_nudges:
                unique_messages: list[str] = []
                seen_messages: set[str] = set()
                affected_tool_ids: list[str] = []
                for tool_id, message in invalid_tool_argument_nudges:
                    affected_tool_ids.append(tool_id)
                    if message in seen_messages:
                        continue
                    seen_messages.add(message)
                    unique_messages.append(message)
                messages.append(
                    {
                        "role": "user",
                        "content": "\n".join(unique_messages),
                    }
                )
                self._emit_event(
                    "toolloop.invalid_tool_arguments_nudged",
                    tool_ids=_dedupe(affected_tool_ids),
                    blocked_by_tool_call_ids=list(round_tool_call_ids) or None,
                    tool_calls_executed=len(executed_tools),
                    **event_context,
                )
                newly_disabled_tool_ids.extend(
                    _register_temporarily_disabled_tools(
                        affected_tool_ids,
                        blocked_tool_counts=blocked_tool_counts,
                        disabled_tool_ids=disabled_tool_ids,
                        enabled_tool_ids=request_tool_ids,
                        request=request,
                    )
                )
            if availability_tool_nudges:
                unique_messages = []
                seen_messages: set[str] = set()
                affected_tool_ids: list[str] = []
                for tool_id, message in availability_tool_nudges:
                    affected_tool_ids.append(tool_id)
                    if message in seen_messages:
                        continue
                    seen_messages.add(message)
                    unique_messages.append(message)
                messages.append(
                    {
                        "role": "user",
                        "content": "\n".join(unique_messages),
                    }
                )
                self._emit_event(
                    "toolloop.tool_availability_nudged",
                    tool_ids=_dedupe(affected_tool_ids),
                    enabled_tools=list(request_tool_ids),
                    blocked_by_tool_call_ids=list(round_tool_call_ids) or None,
                    tool_calls_executed=len(executed_tools),
                    **event_context,
                )
            if repeated_tool_call_nudges:
                unique_messages = []
                seen_messages: set[str] = set()
                affected_tool_ids: list[str] = []
                for tool_id, message in repeated_tool_call_nudges:
                    affected_tool_ids.append(tool_id)
                    if message in seen_messages:
                        continue
                    seen_messages.add(message)
                    unique_messages.append(message)
                messages.append(
                    {
                        "role": "user",
                        "content": "\n".join(unique_messages),
                    }
                )
                self._emit_event(
                    "toolloop.repeated_tool_call_nudged",
                    tool_ids=_dedupe(affected_tool_ids),
                    blocked_by_tool_call_ids=list(round_tool_call_ids) or None,
                    tool_calls_executed=len(executed_tools),
                    **event_context,
                )
                newly_disabled_tool_ids.extend(
                    _register_temporarily_disabled_tools(
                        affected_tool_ids,
                        blocked_tool_counts=blocked_tool_counts,
                        disabled_tool_ids=disabled_tool_ids,
                        enabled_tool_ids=request_tool_ids,
                        request=request,
                    )
                )
            if newly_disabled_tool_ids:
                enabled_after_disable = [
                    _tool_schema_name(tool)
                    for tool in _enabled_tool_schemas(
                        active_tool_schemas,
                        disabled_tool_ids=sorted(disabled_tool_ids),
                    )
                    if _tool_schema_name(tool)
                ]
                messages.append(
                    {
                        "role": "user",
                        "content": _temporarily_disabled_tool_message(newly_disabled_tool_ids),
                    }
                )
                self._emit_event(
                    "toolloop.temporarily_disabled_tools",
                    tool_ids=_dedupe(newly_disabled_tool_ids),
                    enabled_tools=enabled_after_disable,
                    blocked_by_tool_call_ids=list(round_tool_call_ids) or None,
                    tool_calls_executed=len(executed_tools),
                    **event_context,
                )
            if write_stage_final_read_consumed:
                direct_write_tool_schemas = _direct_write_tool_schemas(tool_schemas)
                if direct_write_tool_schemas:
                    active_tool_schemas = direct_write_tool_schemas
                    disabled_tool_ids.clear()
                messages.append(
                    {
                        "role": "user",
                        "content": _write_capable_coding_stage_post_final_read_message(),
                    }
                )
                self._emit_event(
                    "toolloop.write_stage_final_read_consumed",
                    enabled_tools=[
                        str(tool.get("function", {}).get("name") or "").strip()
                        for tool in active_tool_schemas
                        if isinstance(tool, dict)
                    ],
                    blocked_by_tool_call_ids=list(round_tool_call_ids) or None,
                    tool_calls_executed=len(executed_tools),
                    **event_context,
                )
            blocked_by_tool_call_ids = list(round_tool_call_ids)
            finalize_mode = _read_only_finalize_mode(request)
            finalize_reason = _read_only_finalize_reason(
                request=request,
                tool_ids=[
                    str(tool.get("function", {}).get("name") or "").strip()
                    for tool in request_tool_schemas
                    if isinstance(tool, dict)
                ],
                executed_tools=executed_tools,
                workspace_root=self._tool_runtime.workspace_root,
            )
            if finalize_reason is not None and not forced_finalize_without_tools:
                forced_finalize_without_tools = True
                active_tool_schemas = []
                messages.append(
                    {
                        "role": "user",
                        "content": _read_only_finalize_message(
                            finalize_reason,
                            mode=finalize_mode or "coding_worker",
                        ),
                    }
                )
                self._emit_event(
                    "toolloop.read_only_finalize_forced",
                    finalize_mode=finalize_mode,
                    reason=finalize_reason,
                    tool_calls_executed=len(executed_tools),
                    blocked_by_tool_call_ids=list(blocked_by_tool_call_ids) or None,
                    **event_context,
                )
                continue

            research_finalize_reason = _research_note_finalize_reason(
                request=request,
                tool_ids=[
                    str(tool.get("function", {}).get("name") or "").strip()
                    for tool in request_tool_schemas
                    if isinstance(tool, dict)
                ],
                executed_tools=executed_tools,
                round_number=rounds,
            )
            if research_finalize_reason is not None and not research_note_finalize_nudged:
                research_note_finalize_nudged = True
                forced_finalize_without_tools = True
                active_tool_schemas = []
                messages.append(
                    {
                        "role": "user",
                        "content": _research_note_finalize_message(
                            research_finalize_reason
                        ),
                    }
                )
                self._emit_event(
                    "toolloop.research_finalize_forced",
                    reason=research_finalize_reason,
                    tool_calls_executed=len(executed_tools),
                    blocked_by_tool_call_ids=list(blocked_by_tool_call_ids) or None,
                    **event_context,
                )
                continue

            write_nudge_reason = _write_capable_coding_stage_first_write_nudge_reason(
                request=request,
                tool_ids=[
                    str(tool.get("function", {}).get("name") or "").strip()
                    for tool in request_tool_schemas
                    if isinstance(tool, dict)
                ],
                executed_tools=executed_tools,
                workspace_root=self._tool_runtime.workspace_root,
            )
            if write_nudge_reason is not None and not write_stage_first_write_nudged:
                write_stage_first_write_nudged = True
                write_stage_final_read_available = any(
                    _tool_schema_name(tool) == "file_read"
                    for tool in request_tool_schemas
                    if isinstance(tool, dict)
                )
                write_stage_tool_schemas = _write_stage_tool_schemas(
                    request_tool_schemas,
                    allow_final_read=write_stage_final_read_available,
                )
                if write_stage_tool_schemas:
                    active_tool_schemas = write_stage_tool_schemas
                    disabled_tool_ids.clear()
                write_stage_direct_write_required = not write_stage_final_read_available
                messages.append(
                    {
                        "role": "user",
                        "content": _write_capable_coding_stage_first_write_nudge_message(
                            write_nudge_reason,
                            allow_final_read=write_stage_final_read_available,
                        ),
                    }
                )
                self._emit_event(
                    "toolloop.write_stage_first_write_nudged",
                    reason=write_nudge_reason,
                    tool_calls_executed=len(executed_tools),
                    enabled_tools=[
                        str(tool.get("function", {}).get("name") or "").strip()
                        for tool in active_tool_schemas
                        if isinstance(tool, dict)
                    ],
                    blocked_by_tool_call_ids=list(blocked_by_tool_call_ids) or None,
                        **event_context,
                    )
            write_finalize_reason = _write_capable_coding_stage_finalize_reason(
                request=request,
                tool_ids=[
                    str(tool.get("function", {}).get("name") or "").strip()
                    for tool in request_tool_schemas
                    if isinstance(tool, dict)
                ],
                executed_tools=executed_tools,
                workspace_root=self._tool_runtime.workspace_root,
                disabled_tool_ids=sorted(disabled_tool_ids),
            )
            if write_finalize_reason is not None and not forced_finalize_without_tools:
                forced_finalize_without_tools = True
                active_tool_schemas = []
                messages.append(
                    {
                        "role": "user",
                        "content": _write_capable_coding_stage_finalize_message(
                            write_finalize_reason
                        ),
                    }
                )
                self._emit_event(
                    "toolloop.write_stage_finalize_forced",
                    reason=write_finalize_reason,
                    tool_calls_executed=len(executed_tools),
                    blocked_by_tool_call_ids=list(blocked_by_tool_call_ids) or None,
                    **event_context,
                )
                continue

    def _resolve_tool_schemas(self, request_tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed = set(self._tool_runtime.tool_ids)
        filtered: list[dict[str, Any]] = []
        seen: set[str] = set()
        for tool in request_tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            name = str(function.get("name") or "").strip() if isinstance(function, dict) else ""
            if name and name in allowed:
                if name in seen:
                    continue
                seen.add(name)
                filtered.append(
                    {
                        "type": str(tool.get("type") or "function") if isinstance(tool, dict) else "function",
                        "function": {
                            "name": name,
                            "description": str(
                                self._tool_runtime.metadata_for(name).get("description")
                                or function.get("description")
                                or name
                            ).strip(),
                            "parameters": copy.deepcopy(
                                self._tool_runtime.metadata_for(name).get("parameters")
                                or function.get("parameters")
                                or {"type": "object", "properties": {}}
                            ),
                        },
                    }
                )
        return filtered

    @staticmethod
    def _assistant_message(result: Any) -> dict[str, Any]:
        raw_message = getattr(result, "raw_assistant_message", None)
        if isinstance(raw_message, dict):
            payload = dict(raw_message)
            payload.setdefault("role", "assistant")
            if result.tool_calls and "tool_calls" not in payload:
                payload["tool_calls"] = list(result.tool_calls)
            payload.setdefault("content", result.text if result.text else None)
            return payload
        payload: dict[str, Any] = {
            "role": "assistant",
            "content": result.text if result.text else None,
        }
        if result.tool_calls:
            payload["tool_calls"] = list(result.tool_calls)
        return payload

    @staticmethod
    def _parse_tool_call(raw_call: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        function = raw_call.get("function") if isinstance(raw_call, dict) else None
        tool_id = str(function.get("name") or raw_call.get("name") or "").strip() if isinstance(function, dict) else ""
        tool_call_id = str(raw_call.get("id") or f"tool-call-{tool_id or 'unknown'}").strip()
        raw_arguments = function.get("arguments") if isinstance(function, dict) else raw_call.get("arguments")
        if isinstance(raw_arguments, dict):
            arguments = dict(raw_arguments)
        elif isinstance(raw_arguments, str) and raw_arguments.strip():
            try:
                parsed = json.loads(raw_arguments)
            except Exception:
                arguments = {"raw_arguments": raw_arguments}
            else:
                arguments = dict(parsed) if isinstance(parsed, dict) else {"arguments": parsed}
        else:
            arguments = {}
        return tool_id, tool_call_id, arguments


def _worker_with_tool_ids(worker: WorkerDefinition, tool_ids: Sequence[str]) -> WorkerDefinition:
    return worker.model_copy(update={"tool_ids": _dedupe(tool_ids)})


def _tissue_with_tool_ids(
    tissue: TissuePattern | None,
    *,
    member_tool_ids: Sequence[str],
) -> TissuePattern | None:
    if tissue is None:
        return None
    return tissue.model_copy(
        update={
            "members": [
                member.model_copy(
                    update={
                        "worker": _worker_with_tool_ids(member.worker, member_tool_ids),
                    }
                )
                for member in tissue.members
            ]
        }
    )


def _organ_with_tool_ids(
    organ: OrganPattern,
    *,
    member_tool_ids: Sequence[str],
    lead_tool_ids: Sequence[str],
) -> OrganPattern:
    return organ.model_copy(
        update={
            "lead_worker": _worker_with_tool_ids(organ.lead_worker, lead_tool_ids),
            "tissue": _tissue_with_tool_ids(organ.tissue, member_tool_ids=member_tool_ids),
        }
    )


def attach_local_tooling_to_reference_organism(
    organism: ProjectExecutionOrganism,
    *,
    tool_ids: Sequence[str] | None = None,
) -> ProjectExecutionOrganism:
    """Return a copy of the reference organism with runtime-selected local tools."""

    full_tool_ids = _dedupe(tool_ids or DEFAULT_LIVE_ORGANISM_TOOL_IDS)
    read_only_tool_ids = _read_only_tool_ids(full_tool_ids)
    research_tool_ids = _research_read_only_tool_ids(full_tool_ids)
    return organism.model_copy(
        update={
            "planner_worker": _worker_with_tool_ids(organism.planner_worker, []),
            "research_organ": _organ_with_tool_ids(
                organism.research_organ,
                member_tool_ids=research_tool_ids,
                lead_tool_ids=research_tool_ids,
            ),
            "validator_organ": _organ_with_tool_ids(
                organism.validator_organ,
                member_tool_ids=full_tool_ids,
                lead_tool_ids=full_tool_ids,
            ),
            "coding_organ": _organ_with_tool_ids(
                organism.coding_organ,
                member_tool_ids=full_tool_ids,
                lead_tool_ids=full_tool_ids,
            ),
            "synthesis_organ": _organ_with_tool_ids(
                organism.synthesis_organ,
                member_tool_ids=read_only_tool_ids,
                lead_tool_ids=read_only_tool_ids,
            ),
        }
    )


def attach_local_tooling_to_coding_organism(
    organism: CodingOrganism,
    *,
    tool_ids: Sequence[str] | None = None,
) -> CodingOrganism:
    """Return a copy of the coding organism with runtime-selected local tools."""

    full_tool_ids = _dedupe(tool_ids or DEFAULT_LIVE_ORGANISM_TOOL_IDS)
    read_only_tool_ids = _read_only_tool_ids(full_tool_ids)
    aggregation_tool_ids = _coding_aggregation_tool_ids(full_tool_ids)
    return organism.model_copy(
        update={
            "orchestrator_worker": _worker_with_tool_ids(organism.orchestrator_worker, []),
            "worker_tool_ids": list(full_tool_ids),
            "parallel_worker_tool_ids": list(read_only_tool_ids),
            "aggregator_organ": _organ_with_tool_ids(
                organism.aggregator_organ,
                member_tool_ids=aggregation_tool_ids,
                lead_tool_ids=aggregation_tool_ids,
            ),
            "validator_organ": _organ_with_tool_ids(
                organism.validator_organ,
                member_tool_ids=read_only_tool_ids,
                lead_tool_ids=read_only_tool_ids,
            ),
        }
    )


__all__ = [
    "DEFAULT_LIVE_ORGANISM_TOOL_IDS",
    "LocalOrganismToolRuntime",
    "ToolLoopCompletionProvider",
    "attach_local_tooling_to_coding_organism",
    "attach_local_tooling_to_reference_organism",
    "available_local_organism_tools",
]
