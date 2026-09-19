"""Local LLM + tool runtime helpers for bounded organisms and coding CLIs."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import os
import platform
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from typing import Any, Callable, Mapping, Sequence

from dan.providers import CompletionResult, LLMProvider, apply_cache_hints
from dan.providers.multimodal import (
    content_with_image_attachments,
    image_attachments_from_metadata,
    parse_data_url_image,
)
from dan.tools import get_all_tools
from dan.tools._atomic_file import atomic_write_bytes
from dan.tools._git_helpers import _find_repo, _git_binary
from dan.worker.context_capsules import (
    build_tool_context_capsules,
    readiness_signal_from_capsules,
)
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse
from dan.worker.organism_log import organism_event_context, stable_output_contract_id
from dan.worker.structured_payload import parse_jsonish_payload

DEFAULT_LIVE_ORGANISM_TOOL_IDS = [
    "list_directory",
    "file_read",
    "workspace_check",
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
# constrained read-only subset. Browser/desktop actions that navigate, click, type,
# download, or focus are likewise excluded from read-only stages; observation and
# extraction tools remain available.
_READ_ONLY_TOOL_EXCLUSIONS = frozenset(
    {
        "native_worker",
        "file_edit",
        "file_write",
        "shell_command",
        "browser_open",
        "browser_click",
        "browser_fill",
        "browser_type",
        "browser_select",
        "browser_download",
        "desktop_focus",
        "desktop_click",
        "desktop_type",
        "desktop_hotkey",
    }
)
_TRANSIENT_BROWSER_TOOL_IDS = frozenset({"browser_open"})
_EXTERNAL_BROWSER_TOOL_IDS = frozenset(
    {
        "browser_click",
        "browser_fill",
        "browser_type",
        "browser_select",
        "browser_download",
    }
)
_EXTERNAL_DESKTOP_TOOL_IDS = frozenset(
    {
        "desktop_focus",
        "desktop_click",
        "desktop_type",
        "desktop_hotkey",
    }
)
_DISCOVERY_ONLY_TOOL_IDS = frozenset(
    {
        "list_directory",
        "file_read",
        "workspace_check",
        "web_search",
        "browser_tabs",
        "browser_inspect",
        "browser_wait",
        "browser_extract",
        "browser_screenshot",
        "desktop_observe",
        "git_status",
        "git_diff",
        "git_log",
    }
)
_RESEARCH_TOOL_PREFERRED_ORDER = ("web_search", "file_read", "list_directory")
_RESEARCH_TOOL_EXCLUSIONS = frozenset({"git_status", "git_diff", "git_log"})
_CODING_AGGREGATION_TOOL_PREFERRED_ORDER = (
    "file_read",
    "workspace_check",
    "file_edit",
    "file_write",
    "git_diff",
)
_CODING_AGGREGATION_TOOL_EXCLUSIONS = frozenset(
    {"list_directory", "shell_command", "web_search", "git_status", "git_log"}
)
_INTERNAL_WORKSPACE_DIR_NAMES = frozenset(
    {
        ".agent-subsessions",
        ".dan-code",
        ".dan-research",
        ".dan-super",
        ".git",
        ".pytest_cache",
        "__pycache__",
    }
)
_SHELL_WORKSPACE_CHANGE_SKIP_DIR_NAMES = _INTERNAL_WORKSPACE_DIR_NAMES | frozenset(
    {
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "build",
        "dist",
        "memory",
        "node_modules",
        "venv",
    }
)
_SHELL_WORKSPACE_CHANGE_MAX_FILES = 12_000
_SHELL_PROTECTED_FILE_MAX_BYTES = 16 * 1024 * 1024
_SHELL_PROTECTED_TOTAL_MAX_BYTES = 128 * 1024 * 1024
_SHELL_CATASTROPHIC_SHRINK_RATIO = 0.05
_GREENFIELD_OPERATOR_ARTIFACTS = frozenset(
    {"prompt.md", "acceptance.md", "report.json"}
)
ToolRuntimeEventCallback = Callable[[dict[str, Any]], None]
ToolApprovalCallback = Callable[[str, dict[str, Any], dict[str, Any]], bool]
_CODING_OUTPUT_COMMON_REQUIRED_KEYS = frozenset(
    {"change_summary", "target_files", "test_plan", "risks"}
)
_CODING_CANDIDATE_REQUIRED_KEYS = (
    frozenset({"candidate_id"}) | _CODING_OUTPUT_COMMON_REQUIRED_KEYS
)
_CODING_WORKER_REQUIRED_KEYS = (
    frozenset({"candidate_fragment"}) | _CODING_OUTPUT_COMMON_REQUIRED_KEYS
)
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
_TOOL_PROMPT_TEXT_LIMIT = 24_000
_EXCLUSIVE_OWNER_FILE_READ_PROMPT_TEXT_LIMIT = 96_000
_OLDER_FILE_READ_PROMPT_TEXT_LIMIT = 4_000
_RECENT_FULL_FILE_READ_PROMPT_RESULTS = 4
_PROMPT_CONTEXT_SUPER_DAN_TARGET_CHARS = 120_000
_PROMPT_CONTEXT_SUPER_DAN_EMERGENCY_CHARS = 180_000
_PROMPT_CONTEXT_RECENT_FULL_ROUNDS = 2
_PROMPT_CONTEXT_EMERGENCY_RECENT_FULL_ROUNDS = 1
_PROMPT_CONTEXT_OLDER_TOOL_TEXT_LIMIT = 2_000
_PROMPT_CONTEXT_EMERGENCY_TOOL_TEXT_LIMIT = 800
_PROMPT_CONTEXT_TOOL_CALL_ARGUMENT_TEXT_LIMIT = 1_000
_PROMPT_CONTEXT_EMERGENCY_TOOL_CALL_ARGUMENT_TEXT_LIMIT = 500
_PROMPT_CONTEXT_ASSISTANT_TEXT_LIMIT = 2_000
_PROMPT_CONTEXT_EMERGENCY_ASSISTANT_TEXT_LIMIT = 800
_PROMPT_CONTEXT_EMERGENCY_FILE_READ_TEXT_LIMIT = 1_000
_PROMPT_CONTEXT_HARD_SYSTEM_TEXT_LIMIT = 24_000
_PROMPT_CONTEXT_HARD_USER_TEXT_LIMIT = 36_000
_PROMPT_CONTEXT_HARD_ASSISTANT_TEXT_LIMIT = 2_000
_PROMPT_CONTEXT_HARD_TOOL_TEXT_LIMIT = 1_000
_PROMPT_CONTEXT_HARD_MIN_MESSAGE_TEXT_LIMIT = 800
_PREWRITE_SUCCESSFUL_READ_NUDGE_THRESHOLD = 3
_AGGREGATION_PREWRITE_SUCCESSFUL_READ_NUDGE_THRESHOLD = 2
_PREWRITE_SHELL_ANALYSIS_NUDGE_THRESHOLD = 4
_FILE_WRITE_RAW_ARGUMENT_RISKY_LENGTH = 4000
_SOFT_BUDGET_PROFILE_SUPER_DAN = "super_dan_live"
_SUPER_DAN_PROVIDER_OVERLOAD_MAX_RETRIES = 2
_SUPER_DAN_PROVIDER_OVERLOAD_RETRY_DELAYS = (0.5, 2.0)
_SUPER_DAN_BUDGET_EXTENSION_DEFAULT_MAX_LEASES = 1
_SUPER_DAN_BUDGET_EXTENSION_MAX_ROUNDS_PER_LEASE = 3
_SUPER_DAN_BUDGET_EXTENSION_MAX_TOOL_CALLS_PER_LEASE = 32
_DIRECT_WRITE_TIMEOUT_SOFT_CAP_SECONDS = 60.0
_EXCLUSIVE_OWNER_DIRECT_WRITE_SOFT_CAP_SECONDS = 45.0
_SOFT_PHASE_TOOL_CALL_BASE_BUDGETS = {
    "coding_prewrite": 8,
    "coding_direct_write": 8,
    "coding_postwrite": 12,
    "validator_read_only": 10,
    "coding_worker_read_only": 8,
    "research_note": 4,
}
_SOFT_PHASE_ROUND_BASE_BUDGETS = {
    "coding_prewrite": 4,
    "coding_direct_write": 3,
    "coding_postwrite": 5,
    "validator_read_only": 4,
    "coding_worker_read_only": 3,
    "research_note": 2,
}


def _event_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _compact_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _image_data_url_summary(value: Any) -> str | None:
    parsed = parse_data_url_image(str(value or ""))
    if parsed is None:
        return None
    mime_type, data = parsed
    digest = hashlib.sha256(data.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"<image data URL: {mime_type}, base64_chars={len(data)}, sha256={digest}>"


def _image_url_from_content_block(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    if str(value.get("type") or "").strip() != "image_url":
        return ""
    image_url = value.get("image_url")
    if isinstance(image_url, Mapping):
        return str(image_url.get("url") or "")
    return str(value.get("url") or "")


def _value_has_image_content_block(value: Any) -> bool:
    if _image_url_from_content_block(value):
        return True
    if isinstance(value, list):
        return any(_value_has_image_content_block(item) for item in value)
    if isinstance(value, Mapping):
        return any(_value_has_image_content_block(item) for item in value.values())
    return False


def _prompt_debug_value(value: Any) -> Any:
    if isinstance(value, str):
        return _image_data_url_summary(value) or value
    if isinstance(value, list):
        return [_prompt_debug_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _prompt_debug_value(item) for key, item in value.items()}
    return value


def _debug_prompt_messages(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a JSON-safe copy of provider-facing messages for debug logs."""

    copied = _prompt_debug_value(
        [message for message in messages if isinstance(message, dict)]
    )
    try:
        json.dumps(copied, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.loads(json.dumps(copied, ensure_ascii=False, default=str))
    return copied


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


def _truncate_prompt_text(
    value: str, *, limit: int = _TOOL_PROMPT_TEXT_LIMIT
) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    clipped = max(limit - 64, 0)
    omitted = max(len(value) - clipped, 0)
    suffix = f"\n...[truncated {omitted} chars for prompt]..."
    return value[:clipped] + suffix, True


def _compact_prompt_value(
    value: Any,
    *,
    text_limit: int = _TOOL_PROMPT_TEXT_LIMIT,
) -> tuple[Any, bool]:
    if _image_url_from_content_block(value):
        return copy.deepcopy(value), False
    if isinstance(value, str):
        if _image_data_url_summary(value) is not None:
            return value, False
        return _truncate_prompt_text(value, limit=text_limit)
    if isinstance(value, list):
        changed = False
        compacted: list[Any] = []
        for item in value:
            compact_item, item_changed = _compact_prompt_value(
                item,
                text_limit=text_limit,
            )
            compacted.append(compact_item)
            changed = changed or item_changed
        return compacted, changed
    if isinstance(value, dict):
        changed = False
        compacted: dict[str, Any] = {}
        for key, item in value.items():
            compact_item, item_changed = _compact_prompt_value(
                item,
                text_limit=text_limit,
            )
            compacted[str(key)] = compact_item
            changed = changed or item_changed
        return compacted, changed
    return value, False


def _compact_tool_payload_for_prompt(
    tool_payload: dict[str, Any],
    *,
    text_limit: int = _TOOL_PROMPT_TEXT_LIMIT,
) -> dict[str, Any]:
    compacted, changed = _compact_prompt_value(tool_payload, text_limit=text_limit)
    if not isinstance(compacted, dict):
        return dict(tool_payload)
    if changed:
        compacted["prompt_payload_compacted"] = True
    return compacted


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return _image_data_url_summary(content) or content
    content_for_count = _prompt_debug_value(content)
    try:
        return json.dumps(
            content_for_count, ensure_ascii=False, sort_keys=True, default=str
        )
    except Exception:
        return str(content_for_count)


def _message_prompt_char_count(messages: Sequence[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        total += len(_message_content_text(message.get("content")))
        tool_calls = message.get("tool_calls")
        if tool_calls:
            total += len(_message_content_text(tool_calls))
    return total


def _tool_schema_prompt_char_count(tools: Sequence[dict[str, Any]] | None) -> int:
    if not tools:
        return 0
    return len(_message_content_text(list(tools)))


def _positive_int(value: Any) -> int | None:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return None
    return coerced if coerced > 0 else None


def _prompt_context_budget_chars(
    request: CompletionRequest,
    *,
    profile: str | None,
) -> tuple[int | None, int | None]:
    target = _positive_int(request.metadata.get("prompt_context_budget_chars"))
    emergency = _positive_int(
        request.metadata.get("prompt_context_emergency_budget_chars")
    )
    if profile == _SOFT_BUDGET_PROFILE_SUPER_DAN:
        target = target or _PROMPT_CONTEXT_SUPER_DAN_TARGET_CHARS
        emergency = emergency or _PROMPT_CONTEXT_SUPER_DAN_EMERGENCY_CHARS
    return target, emergency


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _compact_middle_text_for_prompt(
    content: str,
    *,
    limit: int,
) -> tuple[str, int]:
    if len(content) <= limit:
        return content, 0
    head_chars = max(int(limit * 0.55), 1)
    tail_chars = max(limit - head_chars, 1)
    omitted = max(len(content) - head_chars - tail_chars, 0)
    marker = (
        f"\n...[older file_read compacted for prompt replay; {omitted} chars omitted. "
        "Use a targeted file_read line range if exact omitted code is needed]...\n"
    )
    return f"{content[:head_chars]}{marker}{content[-tail_chars:]}", omitted


def _compact_older_file_read_payload_for_prompt(
    tool_payload: dict[str, Any],
    *,
    text_limit: int = _OLDER_FILE_READ_PROMPT_TEXT_LIMIT,
) -> tuple[dict[str, Any], bool, int]:
    if not tool_payload.get("ok"):
        return tool_payload, False, 0
    result = tool_payload.get("result")
    if not isinstance(result, dict):
        return tool_payload, False, 0
    content = result.get("content")
    if not isinstance(content, str):
        return tool_payload, False, 0

    compacted_content, omitted = _compact_middle_text_for_prompt(
        content,
        limit=text_limit,
    )
    if omitted <= 0:
        return tool_payload, False, 0

    compacted = copy.deepcopy(tool_payload)
    compacted_result = compacted.setdefault("result", {})
    if isinstance(compacted_result, dict):
        compacted_result["content"] = compacted_content
        compacted_result["content_prompt_scope"] = "older_file_read_excerpt"
        compacted_result["prompt_content_omitted_chars"] = omitted
        compacted_result["prompt_context_note"] = (
            "Older file_read content was compacted only for prompt replay. "
            "The full tool result remains available in runtime logs; call file_read "
            "with a targeted line range if exact omitted code is needed."
        )
    compacted["prompt_payload_compacted"] = True
    compacted["prompt_context_compacted"] = True
    return compacted, True, omitted


def _prompt_context_total_chars(
    messages: Sequence[dict[str, Any]],
    *,
    tool_schema_chars: int,
) -> int:
    return _message_prompt_char_count(messages) + max(0, int(tool_schema_chars))


def _over_prompt_context_budget(
    messages: Sequence[dict[str, Any]],
    *,
    budget_chars: int | None,
    tool_schema_chars: int,
) -> bool:
    if budget_chars is None:
        return False
    return (
        _prompt_context_total_chars(
            messages,
            tool_schema_chars=tool_schema_chars,
        )
        > budget_chars
    )


def _protected_prompt_message_indices(
    messages: Sequence[dict[str, Any]],
    *,
    recent_full_rounds: int,
) -> set[int]:
    protected: set[int] = set()
    first_user_index: int | None = None
    assistant_tool_round_indices: list[int] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        if role == "system":
            protected.add(index)
        elif role == "user" and first_user_index is None:
            first_user_index = index
        if role == "assistant" and message.get("tool_calls"):
            assistant_tool_round_indices.append(index)
    if first_user_index is not None:
        protected.add(first_user_index)

    if assistant_tool_round_indices and recent_full_rounds > 0:
        start = assistant_tool_round_indices[
            max(0, len(assistant_tool_round_indices) - recent_full_rounds)
        ]
        protected.update(range(start, len(messages)))
    elif messages:
        protected.update(range(max(0, len(messages) - 4), len(messages)))
    return protected


def _compact_older_tool_message_for_prompt(
    message: dict[str, Any],
    *,
    text_limit: int,
) -> tuple[bool, int]:
    if str(message.get("role") or "").strip() != "tool":
        return False, 0
    if str(message.get("name") or "").strip() == "file_read":
        return False, 0
    content = message.get("content")
    if not isinstance(content, str):
        return False, 0
    original_chars = len(content)
    try:
        payload = json.loads(content)
    except Exception:
        compacted_content, changed = _truncate_prompt_text(
            content,
            limit=text_limit,
        )
        if not changed:
            return False, 0
        message["content"] = compacted_content
        return True, max(original_chars - len(compacted_content), 0)

    compacted_payload, changed = _compact_prompt_value(
        payload,
        text_limit=text_limit,
    )
    if not changed:
        return False, 0
    if isinstance(compacted_payload, dict):
        compacted_payload["prompt_payload_compacted"] = True
        compacted_payload["prompt_context_compacted"] = True
        compacted_payload["prompt_context_note"] = (
            "Older non-file tool output was compacted only for prompt replay. "
            "The full tool result remains available in runtime logs."
        )
    compacted_content = json.dumps(
        compacted_payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    message["content"] = compacted_content
    return True, max(original_chars - len(compacted_content), 0)


def _summarize_text_field_for_prompt(
    target: dict[str, Any],
    source: dict[str, Any],
    key: str,
) -> None:
    value = source.get(key)
    if not isinstance(value, str):
        return
    target[f"{key}_chars"] = len(value)
    target[f"{key}_sha256"] = _sha256_text(value)


def _compact_file_write_arguments_for_prompt(
    payload: dict[str, Any],
    *,
    original_chars: int,
) -> dict[str, Any]:
    retained: dict[str, Any] = {}
    for key in ("path", "mode", "append", "encoding"):
        if key in payload:
            retained[key] = payload.get(key)
    _summarize_text_field_for_prompt(retained, payload, "content")
    return {
        "prompt_replay_compacted": True,
        "original_chars": original_chars,
        "retained": retained,
        "summary": (
            "Old file_write arguments omitted from provider replay; full raw "
            "arguments remain in the event log and executed tool evidence."
        ),
    }


def _compact_file_edit_arguments_for_prompt(
    payload: dict[str, Any],
    *,
    original_chars: int,
) -> dict[str, Any]:
    retained: dict[str, Any] = {}
    for key in ("path", "mode", "start_line", "end_line"):
        if key in payload:
            retained[key] = payload.get(key)
    for key in ("content", "old_string", "new_string"):
        _summarize_text_field_for_prompt(retained, payload, key)

    edits = payload.get("edits")
    if isinstance(edits, list):
        retained_edits: list[dict[str, Any]] = []
        for item in edits[:20]:
            if not isinstance(item, dict):
                continue
            retained_item: dict[str, Any] = {}
            for key in ("path", "mode", "start_line", "end_line"):
                if key in item:
                    retained_item[key] = item.get(key)
            for key in ("content", "old_string", "new_string"):
                _summarize_text_field_for_prompt(retained_item, item, key)
            retained_edits.append(retained_item)
        retained["edits"] = retained_edits
        if len(edits) > len(retained_edits):
            retained["omitted_edit_items"] = len(edits) - len(retained_edits)

    return {
        "prompt_replay_compacted": True,
        "original_chars": original_chars,
        "retained": retained,
        "summary": (
            "Old file_edit arguments omitted from provider replay; full raw "
            "arguments remain in the event log and executed tool evidence."
        ),
    }


def _compact_tool_call_arguments_for_prompt(
    raw_call: dict[str, Any],
    *,
    text_limit: int,
) -> tuple[bool, int]:
    function_payload = raw_call.get("function")
    if not isinstance(function_payload, dict):
        return False, 0
    arguments = function_payload.get("arguments")
    if not isinstance(arguments, str):
        return False, 0
    original_chars = len(arguments)
    function_name = str(
        function_payload.get("name") or raw_call.get("name") or ""
    ).strip()
    if original_chars <= text_limit and function_name not in {
        "file_write",
        "file_edit",
    }:
        return False, 0

    try:
        parsed = json.loads(arguments)
    except Exception:
        compacted_payload = {
            "prompt_replay_compacted": True,
            "original_chars": original_chars,
            "summary": (
                f"Old {function_name or 'tool'} arguments omitted from provider "
                "replay; full raw arguments remain in the event log."
            ),
        }
    else:
        if isinstance(parsed, dict) and parsed.get("prompt_replay_compacted") is True:
            compacted_payload, changed = _compact_prompt_value(
                parsed,
                text_limit=text_limit,
            )
            if not changed:
                return False, 0
        elif function_name == "file_write" and isinstance(parsed, dict):
            compacted_payload = _compact_file_write_arguments_for_prompt(
                parsed,
                original_chars=original_chars,
            )
        elif function_name == "file_edit" and isinstance(parsed, dict):
            compacted_payload = _compact_file_edit_arguments_for_prompt(
                parsed,
                original_chars=original_chars,
            )
        else:
            compacted_payload, changed = _compact_prompt_value(
                parsed,
                text_limit=text_limit,
            )
            if not changed and original_chars <= text_limit:
                return False, 0
            if isinstance(compacted_payload, dict):
                compacted_payload["prompt_replay_compacted"] = True
                compacted_payload["original_chars"] = original_chars
            else:
                compacted_payload = {
                    "prompt_replay_compacted": True,
                    "original_chars": original_chars,
                    "retained": compacted_payload,
                }

    compacted_arguments = json.dumps(
        compacted_payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    if len(compacted_arguments) >= original_chars:
        return False, 0
    function_payload["arguments"] = compacted_arguments
    return True, max(original_chars - len(compacted_arguments), 0)


def _compact_assistant_message_content_for_prompt(
    message: dict[str, Any],
    *,
    text_limit: int,
) -> tuple[bool, int]:
    if str(message.get("role") or "").strip() != "assistant":
        return False, 0
    content = message.get("content")
    if not isinstance(content, str):
        return False, 0
    compacted, changed = _truncate_prompt_text(content, limit=text_limit)
    if not changed:
        return False, 0
    message["content"] = compacted
    return True, max(len(content) - len(compacted), 0)


def _compact_arbitrary_prompt_text(
    content: str,
    *,
    limit: int,
    reason: str,
) -> tuple[str, int]:
    if len(content) <= limit:
        return content, 0
    marker = (
        f"\n...[prompt replay compacted: {{omitted}} chars omitted; {reason}; "
        "use targeted tools or event logs for exact omitted details]...\n"
    )
    marker_overhead = len(marker.format(omitted=len(content)))
    available = max(int(limit) - marker_overhead, 0)
    if available <= 0:
        omitted = len(content)
        return marker.format(omitted=omitted)[: max(int(limit), 0)], omitted
    head_chars = max(int(available * 0.6), 1)
    tail_chars = max(available - head_chars, 0)
    omitted = max(len(content) - head_chars - tail_chars, 0)
    compacted = (
        content[:head_chars]
        + marker.format(omitted=omitted)
        + (content[-tail_chars:] if tail_chars else "")
    )
    if len(compacted) > limit and tail_chars:
        overflow = len(compacted) - limit
        head_chars = max(head_chars - overflow, 1)
        omitted = max(len(content) - head_chars - tail_chars, 0)
        compacted = (
            content[:head_chars]
            + marker.format(omitted=omitted)
            + content[-tail_chars:]
        )
    return compacted, omitted


def _compact_message_content_hard_for_prompt(
    message: dict[str, Any],
    *,
    text_limit: int,
    reason: str,
) -> tuple[bool, int]:
    content = message.get("content")
    original_chars = len(_message_content_text(content))
    if original_chars <= text_limit:
        return False, 0
    role = str(message.get("role") or "").strip()
    has_image_content = _value_has_image_content_block(content)
    if role == "tool" and isinstance(content, str):
        try:
            payload = json.loads(content)
        except Exception:
            payload = None
        if isinstance(payload, (dict, list)):
            compacted_payload, changed = _compact_prompt_value(
                payload,
                text_limit=text_limit,
            )
            if not changed:
                return False, 0
            if isinstance(compacted_payload, dict):
                compacted_payload["prompt_context_hard_compacted"] = True
                compacted_payload["prompt_context_hard_note"] = (
                    "Tool content was compacted only for provider replay after the active prompt budget was exceeded."
                )
            message["content"] = json.dumps(
                compacted_payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            return True, max(original_chars - len(str(message["content"])), 0)
    if isinstance(content, str):
        compacted, omitted = _compact_arbitrary_prompt_text(
            content,
            limit=text_limit,
            reason=reason,
        )
        if omitted <= 0:
            return False, 0
        message["content"] = compacted
        return True, max(original_chars - len(compacted), 0)

    compacted_content, changed = _compact_prompt_value(
        content,
        text_limit=max(
            _PROMPT_CONTEXT_HARD_MIN_MESSAGE_TEXT_LIMIT, int(text_limit // 4)
        ),
    )
    compacted_chars = len(_message_content_text(compacted_content))
    if changed and compacted_chars <= text_limit:
        message["content"] = compacted_content
        return True, max(original_chars - compacted_chars, 0)
    if has_image_content:
        if changed:
            message["content"] = compacted_content
            return True, max(original_chars - compacted_chars, 0)
        return False, 0

    serialized = _message_content_text(content)
    retained_excerpt, omitted = _compact_arbitrary_prompt_text(
        serialized,
        limit=max(_PROMPT_CONTEXT_HARD_MIN_MESSAGE_TEXT_LIMIT, int(text_limit * 0.75)),
        reason=reason,
    )
    message["content"] = {
        "prompt_replay_compacted": True,
        "original_chars": original_chars,
        "retained_excerpt": retained_excerpt,
        "summary": (
            "Large structured message content was compacted only for provider replay. "
            "The full source remains available through runtime logs or targeted tools."
        ),
    }
    return True, max(
        original_chars - len(_message_content_text(message.get("content"))), omitted
    )


def _hard_prompt_text_limit_for_role(role: str) -> int:
    if role == "system":
        return _PROMPT_CONTEXT_HARD_SYSTEM_TEXT_LIMIT
    if role == "user":
        return _PROMPT_CONTEXT_HARD_USER_TEXT_LIMIT
    if role == "tool":
        return _PROMPT_CONTEXT_HARD_TOOL_TEXT_LIMIT
    return _PROMPT_CONTEXT_HARD_ASSISTANT_TEXT_LIMIT


def _hard_compact_messages_for_provider_prompt(
    messages: list[dict[str, Any]],
    *,
    budget_chars: int | None,
    emergency_budget_chars: int | None,
    tool_schema_chars: int,
    emergency: bool,
    protected_indices: set[int] | None = None,
) -> dict[str, Any]:
    active_budget = (
        emergency_budget_chars
        if emergency and emergency_budget_chars is not None
        else budget_chars
    )
    if active_budget is None:
        active_budget = emergency_budget_chars
    if active_budget is None or not _over_prompt_context_budget(
        messages,
        budget_chars=active_budget,
        tool_schema_chars=tool_schema_chars,
    ):
        return {
            "prompt_context_hard_compaction": False,
            "prompt_context_hard_budget_chars": active_budget,
            "prompt_context_hard_compacted_messages": 0,
            "prompt_context_hard_omitted_message_chars": 0,
            "prompt_context_hard_compacted_tool_call_args": 0,
            "prompt_context_hard_omitted_tool_call_arg_chars": 0,
        }

    message_count = 0
    omitted_message_chars = 0
    tool_call_count = 0
    tool_call_omitted_chars = 0
    reason = "prompt context exceeded the active provider replay budget"
    protected = set(protected_indices or set())

    for index, message in enumerate(messages):
        role = str(message.get("role") or "").strip()
        limit = _hard_prompt_text_limit_for_role(role)
        compacted, omitted = _compact_message_content_hard_for_prompt(
            message,
            text_limit=limit,
            reason=reason,
        )
        if compacted:
            message_count += 1
            omitted_message_chars += omitted
        if index in protected:
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for raw_call in tool_calls:
            if not isinstance(raw_call, dict):
                continue
            compacted_args, omitted_args = _compact_tool_call_arguments_for_prompt(
                raw_call,
                text_limit=_PROMPT_CONTEXT_EMERGENCY_TOOL_CALL_ARGUMENT_TEXT_LIMIT,
            )
            if compacted_args:
                tool_call_count += 1
                tool_call_omitted_chars += omitted_args

    if _over_prompt_context_budget(
        messages,
        budget_chars=active_budget,
        tool_schema_chars=tool_schema_chars,
    ):
        available = max(int(active_budget) - max(0, int(tool_schema_chars)), 0)
        per_message_limit = max(
            _PROMPT_CONTEXT_HARD_MIN_MESSAGE_TEXT_LIMIT,
            int(available / max(len(messages), 1)),
        )
        for message in messages:
            compacted, omitted = _compact_message_content_hard_for_prompt(
                message,
                text_limit=per_message_limit,
                reason=reason,
            )
            if compacted:
                message_count += 1
                omitted_message_chars += omitted

    return {
        "prompt_context_hard_compaction": True,
        "prompt_context_hard_budget_chars": active_budget,
        "prompt_context_hard_compacted_messages": message_count,
        "prompt_context_hard_omitted_message_chars": omitted_message_chars,
        "prompt_context_hard_compacted_tool_call_args": tool_call_count,
        "prompt_context_hard_omitted_tool_call_arg_chars": tool_call_omitted_chars,
    }


def _compact_messages_for_provider_prompt(
    messages: Sequence[dict[str, Any]],
    *,
    recent_full_file_reads: int = _RECENT_FULL_FILE_READ_PROMPT_RESULTS,
    budget_chars: int | None = None,
    emergency_budget_chars: int | None = None,
    tool_schema_chars: int = 0,
    emergency: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return provider-facing messages with old prompt replay payloads shrunk.

    Raw tool evidence stays in ``executed_tools`` and event logs. This only trims
    the copied replay prompt so old high-volume evidence and tool-call arguments
    do not keep reloading into every later model call.
    """

    copied = [
        copy.deepcopy(message) for message in messages if isinstance(message, dict)
    ]
    original_chars = _message_prompt_char_count(copied)
    original_total_chars = original_chars + max(0, int(tool_schema_chars))
    effective_recent_file_reads = (
        min(max(0, int(recent_full_file_reads)), 1)
        if emergency
        else max(0, int(recent_full_file_reads))
    )
    file_read_text_limit = (
        _PROMPT_CONTEXT_EMERGENCY_FILE_READ_TEXT_LIMIT
        if emergency
        else _OLDER_FILE_READ_PROMPT_TEXT_LIMIT
    )
    file_read_indices = [
        index
        for index, message in enumerate(copied)
        if str(message.get("role") or "").strip() == "tool"
        and str(message.get("name") or "").strip() == "file_read"
    ]
    keep_indices = (
        set(file_read_indices[-effective_recent_file_reads:])
        if effective_recent_file_reads > 0
        else set()
    )
    compacted_count = 0
    omitted_chars = 0

    for index in file_read_indices:
        if index in keep_indices:
            continue
        message = copied[index]
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        compacted_payload, compacted, omitted = (
            _compact_older_file_read_payload_for_prompt(
                payload,
                text_limit=file_read_text_limit,
            )
        )
        if not compacted:
            continue
        message["content"] = json.dumps(
            compacted_payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        compacted_count += 1
        omitted_chars += omitted

    protected_indices = _protected_prompt_message_indices(
        copied,
        recent_full_rounds=(
            _PROMPT_CONTEXT_EMERGENCY_RECENT_FULL_ROUNDS
            if emergency
            else _PROMPT_CONTEXT_RECENT_FULL_ROUNDS
        ),
    )
    budget_triggered = emergency or _over_prompt_context_budget(
        copied,
        budget_chars=budget_chars,
        tool_schema_chars=tool_schema_chars,
    )
    non_file_tool_count = 0
    non_file_tool_omitted_chars = 0
    tool_call_arg_count = 0
    tool_call_arg_omitted_chars = 0
    assistant_message_count = 0
    assistant_message_omitted_chars = 0

    if budget_triggered:
        tool_text_limit = (
            _PROMPT_CONTEXT_EMERGENCY_TOOL_TEXT_LIMIT
            if emergency
            else _PROMPT_CONTEXT_OLDER_TOOL_TEXT_LIMIT
        )
        for index, message in enumerate(copied):
            if index in protected_indices:
                continue
            compacted, omitted = _compact_older_tool_message_for_prompt(
                message,
                text_limit=tool_text_limit,
            )
            if not compacted:
                continue
            non_file_tool_count += 1
            non_file_tool_omitted_chars += omitted

    if budget_triggered and (
        emergency
        or _over_prompt_context_budget(
            copied,
            budget_chars=budget_chars,
            tool_schema_chars=tool_schema_chars,
        )
    ):
        argument_text_limit = (
            _PROMPT_CONTEXT_EMERGENCY_TOOL_CALL_ARGUMENT_TEXT_LIMIT
            if emergency
            else _PROMPT_CONTEXT_TOOL_CALL_ARGUMENT_TEXT_LIMIT
        )
        for index, message in enumerate(copied):
            if index in protected_indices:
                continue
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for raw_call in tool_calls:
                if not isinstance(raw_call, dict):
                    continue
                compacted, omitted = _compact_tool_call_arguments_for_prompt(
                    raw_call,
                    text_limit=argument_text_limit,
                )
                if not compacted:
                    continue
                tool_call_arg_count += 1
                tool_call_arg_omitted_chars += omitted

    if budget_triggered and (
        emergency
        or _over_prompt_context_budget(
            copied,
            budget_chars=budget_chars,
            tool_schema_chars=tool_schema_chars,
        )
    ):
        assistant_text_limit = (
            _PROMPT_CONTEXT_EMERGENCY_ASSISTANT_TEXT_LIMIT
            if emergency
            else _PROMPT_CONTEXT_ASSISTANT_TEXT_LIMIT
        )
        for index, message in enumerate(copied):
            if index in protected_indices:
                continue
            compacted, omitted = _compact_assistant_message_content_for_prompt(
                message,
                text_limit=assistant_text_limit,
            )
            if not compacted:
                continue
            assistant_message_count += 1
            assistant_message_omitted_chars += omitted

    hard_compaction_stats = _hard_compact_messages_for_provider_prompt(
        copied,
        budget_chars=budget_chars,
        emergency_budget_chars=emergency_budget_chars,
        tool_schema_chars=tool_schema_chars,
        emergency=emergency,
        protected_indices=protected_indices,
    )
    compacted_chars = _message_prompt_char_count(copied)
    final_total_chars = compacted_chars + max(0, int(tool_schema_chars))
    stats = {
        "prompt_message_count": len(copied),
        "prompt_input_chars": compacted_chars,
        "prompt_context_original_chars": original_chars,
        "prompt_context_saved_chars": max(original_chars - compacted_chars, 0),
        "prompt_context_file_read_messages": len(file_read_indices),
        "prompt_context_compacted_file_reads": compacted_count,
        "prompt_context_omitted_file_read_chars": omitted_chars,
        "prompt_context_budget_chars": budget_chars,
        "prompt_context_emergency_budget_chars": emergency_budget_chars,
        "prompt_context_budget_triggered": bool(budget_triggered),
        "prompt_context_emergency_compaction": bool(emergency),
        "prompt_context_tool_schema_chars": max(0, int(tool_schema_chars)),
        "prompt_context_original_total_chars": original_total_chars,
        "prompt_context_final_chars": final_total_chars,
        "prompt_context_compacted_non_file_tools": non_file_tool_count,
        "prompt_context_omitted_non_file_tool_chars": non_file_tool_omitted_chars,
        "prompt_context_compacted_tool_call_args": tool_call_arg_count,
        "prompt_context_omitted_tool_call_arg_chars": tool_call_arg_omitted_chars,
        "prompt_context_compacted_assistant_messages": assistant_message_count,
        "prompt_context_omitted_assistant_message_chars": assistant_message_omitted_chars,
    }
    stats.update(hard_compaction_stats)
    return copied, stats


def _line_numbered_prompt_content(content: str, *, start_line: int = 1) -> str:
    lines = content.splitlines()
    if content.endswith("\n"):
        lines.append("")
    first = max(1, int(start_line or 1))
    return "\n".join(f"{first + index:>6}| {line}" for index, line in enumerate(lines))


def _file_read_payload_with_line_numbers(
    tool_payload: dict[str, Any],
) -> dict[str, Any]:
    prompt_payload = copy.deepcopy(tool_payload)
    result = prompt_payload.get("result")
    if not isinstance(result, dict):
        return prompt_payload
    content = result.get("content")
    if not isinstance(content, str):
        return prompt_payload
    arguments = (
        prompt_payload.get("arguments")
        if isinstance(prompt_payload.get("arguments"), dict)
        else {}
    )
    try:
        start_line = int(result.get("line_start") or arguments.get("start_line") or 1)
    except (TypeError, ValueError):
        start_line = 1
    result["content"] = _line_numbered_prompt_content(content, start_line=start_line)
    result["content_format"] = "line_numbered"
    return prompt_payload


def _completion_request_user_content(
    request: CompletionRequest,
) -> str | list[dict[str, Any]]:
    attachments = image_attachments_from_metadata(request.metadata)
    if not attachments:
        return request.user_prompt
    return content_with_image_attachments(request.user_prompt, attachments)


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


def _model_facing_tool_parameters(tool_id: str, parameters: Any) -> dict[str, Any]:
    if tool_id != "file_edit":
        return (
            copy.deepcopy(parameters)
            if isinstance(parameters, dict)
            else {"type": "object", "properties": {}}
        )

    edit_item_properties: dict[str, Any] = {
        "start_line": {"type": "integer", "description": "1-indexed anchor line."},
        "end_line": {"type": "integer", "description": "Inclusive 1-indexed end line."},
        "content": {"type": "string", "description": "Replacement or inserted text."},
        "mode": {
            "type": "string",
            "enum": ["replace", "insert_before", "insert_after", "delete"],
            "description": "Edit mode.",
        },
        "old_string": {
            "type": "string",
            "description": "Exact unique old text copied from a recent file_read.",
        },
        "new_string": {"type": "string", "description": "Replacement for old_string."},
    }
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Existing file path."},
            **copy.deepcopy(edit_item_properties),
            "edits": {
                "type": "array",
                "description": (
                    "Batch non-overlapping same-file edits. Each item must include "
                    "start_line or old_string plus new_string."
                ),
                "items": {
                    "type": "object",
                    "properties": copy.deepcopy(edit_item_properties),
                },
            },
        },
        "required": ["path"],
    }


def _read_only_tool_ids(tool_ids: Sequence[str]) -> list[str]:
    return [
        tool_id
        for tool_id in _dedupe(tool_ids)
        if tool_id not in _READ_ONLY_TOOL_EXCLUSIONS
    ]


def _structured_surface_ui_tool_ids(
    request: CompletionRequest,
    tool_ids: Sequence[str],
) -> list[str]:
    raw_policy = request.metadata.get("surface_policy")
    policy = raw_policy if isinstance(raw_policy, Mapping) else {}
    raw_packs = policy.get("capability_packs")
    packs = (
        {str(item).strip().lower() for item in raw_packs if str(item).strip()}
        if isinstance(raw_packs, Sequence) and not isinstance(raw_packs, (str, bytes))
        else set()
    )
    if "computer_control" in packs:
        packs.update({"browser_control", "desktop_control"})
    permission_scope = str(policy.get("permission_scope") or "").strip().lower()
    allowed: set[str] = set()
    if "browser_control" in packs and permission_scope in {
        "transient_execute",
        "external_write",
    }:
        allowed.update(_TRANSIENT_BROWSER_TOOL_IDS)
    if permission_scope == "external_write":
        if "browser_control" in packs:
            allowed.update(_EXTERNAL_BROWSER_TOOL_IDS)
        if "desktop_control" in packs:
            allowed.update(_EXTERNAL_DESKTOP_TOOL_IDS)
    return [tool_id for tool_id in _dedupe(tool_ids) if tool_id in allowed]


def _research_read_only_tool_ids(tool_ids: Sequence[str]) -> list[str]:
    read_only = _read_only_tool_ids(tool_ids)
    preferred = [
        tool_id for tool_id in _RESEARCH_TOOL_PREFERRED_ORDER if tool_id in read_only
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
        if tool_id not in preferred
        and tool_id not in _CODING_AGGREGATION_TOOL_EXCLUSIONS
    ]
    narrowed = preferred + extras
    return narrowed or available


def _tool_ids_are_read_only(tool_ids: Sequence[str]) -> bool:
    available = _dedupe(tool_ids)
    return bool(available) and not any(
        tool_id in _READ_ONLY_TOOL_EXCLUSIONS for tool_id in available
    )


def _operator_intent_policy_payload(request: CompletionRequest) -> Mapping[str, Any]:
    raw_policy = request.metadata.get("operator_intent_policy")
    return raw_policy if isinstance(raw_policy, Mapping) else {}


def _operator_intent_blocked_tool_ids(request: CompletionRequest) -> set[str]:
    policy = _operator_intent_policy_payload(request)
    if not policy:
        return set()

    blocked: set[str] = set()
    raw_work_contract = policy.get("work_contract")
    work_contract = raw_work_contract if isinstance(raw_work_contract, Mapping) else {}
    work_mode = str(policy.get("work_mode") or work_contract.get("work_mode") or "")
    if work_mode == "chat_answer":
        blocked.update(
            {
                "list_directory",
                "file_read",
                "file_edit",
                "file_write",
                "workspace_check",
                "shell_command",
                "git_status",
                "git_diff",
                "git_log",
                "web_search",
            }
        )
    if policy.get("allow_workspace_mutation") is False:
        blocked.update({"file_edit", "file_write"})
    if policy.get("allow_shell_command") is False:
        blocked.add("shell_command")
    if policy.get("allow_directory_listing") is False:
        blocked.add("list_directory")
    if policy.get("allow_git_context") is False:
        blocked.update({"git_status", "git_diff", "git_log"})
    if policy.get("forbid_other_workspace_inputs") is True and not policy.get(
        "allowed_read_paths"
    ):
        blocked.add("file_read")
    return blocked


def _operator_intent_tool_schemas(
    request: CompletionRequest,
    tool_schemas: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    blocked = _operator_intent_blocked_tool_ids(request)
    if not blocked:
        return [dict(tool) for tool in tool_schemas if isinstance(tool, dict)]
    return [
        dict(tool)
        for tool in tool_schemas
        if isinstance(tool, dict) and _tool_schema_name(tool) not in blocked
    ]


def _request_forbids_workspace_mutation(request: CompletionRequest) -> bool:
    raw_policy = _operator_intent_policy_payload(request)
    raw_work_contract = raw_policy.get("work_contract")
    work_contract = raw_work_contract if isinstance(raw_work_contract, Mapping) else {}
    mutation_policy = str(
        raw_policy.get("mutation_policy") or work_contract.get("mutation_policy") or ""
    ).strip()
    if mutation_policy:
        return (
            mutation_policy == "forbidden"
            or raw_policy.get("allow_workspace_mutation") is False
        )
    if "allow_workspace_mutation" in raw_policy:
        return raw_policy.get("allow_workspace_mutation") is False

    text = " ".join(
        str(part or "")
        for part in (
            request.system_prompt,
            request.user_prompt,
            request.metadata.get("operator_prompt"),
            request.metadata.get("objective"),
        )
    )
    lowered = " ".join(text.lower().split())
    if not lowered:
        return False
    patterns = (
        r"\bread[-\s]?only\b",
        r"\bdo\s+not\s+(?:edit|modify|change|write|create|delete|touch|mutate)\s+(?:any\s+)?(?:workspace\s+)?files?\b",
        r"\bdon['’]?t\s+(?:edit|modify|change|write|create|delete|touch|mutate)\s+(?:any\s+)?(?:workspace\s+)?files?\b",
        r"\bdont\s+(?:edit|modify|change|write|create|delete|touch|mutate)\s+(?:any\s+)?(?:workspace\s+)?files?\b",
        r"\bwithout\s+(?:editing|modifying|changing|writing|creating|deleting|touching|mutating)\s+(?:any\s+)?(?:workspace\s+)?files?\b",
        r"\bno\s+(?:file\s+)?(?:edits?|writes?|changes?|modifications?|mutations?)\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _workspace_supports_git(workspace_root: str | Path) -> bool:
    try:
        _find_repo(str(Path(workspace_root).expanduser().resolve()))
    except Exception:
        return False
    return True


def _workspace_tool_ids(
    tool_ids: Sequence[str], *, workspace_root: str | Path
) -> list[str]:
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
    shell_name = Path(str(os.environ.get("SHELL") or "")).name or "unknown"
    platform_name = platform.system() or os.name
    platform_release = platform.release()
    machine = platform.machine()
    lines = [
        "Runtime platform context:",
        f"- OS/platform: {platform_name} {platform_release} {machine}".strip(),
        f"- Default shell: {shell_name}",
        "- `shell_command` runs from the workspace root by default unless a working_directory is supplied.",
        "- Prefer commands and flags that match the current platform; macOS/Darwin and GNU/Linux utilities can differ.",
        "- Before relying on optional binaries, check availability with `command -v <name>`.",
        "- Prefer built-in POSIX/project tools. Do not install new packages or CLIs unless the operator explicitly asks, or the task cannot reasonably proceed without them and you first explain the need.",
        "",
        "Local tool-use policy:",
        "- Prefer the most specific structured tool available for the job.",
        "- Keep tool calls targeted and incremental. Avoid duplicate discovery once you already have the needed fact.",
    ]
    if "list_directory" in available:
        lines.append(
            "- Use `list_directory` for directory inspection instead of shell `ls`."
        )
    if "file_read" in available:
        lines.append(
            "- Use `file_read` for file contents instead of shell `cat`, `head`, or similar fallbacks."
        )
        lines.append(
            "- Prefer explicit line windows with `start_line`/`end_line` for code inspection. Do not rely on shell `grep`/`sed`/`awk`, regex searches, or other fixed-pattern matching to locate edit sites when `file_read` is available."
        )
        lines.append(
            "- Reuse unchanged `file_read` context already present in this tool loop; only refresh a file when it may have changed or exact line grounding is needed for the next edit."
        )
    if "workspace_check" in available:
        lines.append(
            "- Use `workspace_check` before `shell_command` for deterministic file existence, literal/regex counts, HTML tag balance, and Python/JSON/HTML/CSS/registered-profile syntax checks. If a syntax check returns unsupported, treat it as informational and use counts, file reads, or a project-native lint/build command instead of blocking on the unsupported syntax profile."
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
            '- Use `file_edit` for targeted edits to existing files. Before each mutation, do a compact edit-intent check and map it to the schema: replace an existing range with `mode="replace"` plus `content`; insert text with `insert_before`/`insert_after` plus `content`; delete text with `mode="delete"` and no replacement fields; replace exact text with `old_string` plus `new_string` copied from a recent `file_read` and no delete mode.'
        )
        lines.append(
            "- For line-based edits, always include `path` and `start_line`, and include `content` for replace/insert edits. When replacing multiple lines, include `end_line` so the full target range is explicit. If you need multiple non-overlapping edits in the same file, prefer one `file_edit` call with `edits=[...]` over repeated single-edit calls; every batch item must include `start_line` or a unique `old_string`/`new_string` pair copied from a recent `file_read`."
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
            '- When current identity, status, version, availability, or exact source text matters, use `web_search` in grounded mode with `search_depth="thorough"` or `fetch_content=true` so it fetches the top authoritative result pages instead of relying on snippets alone.'
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
            "- Use `shell_command` for genuine terminal work: running tests, build scripts, project CLIs, small validation commands, command availability checks, filesystem transfers, checksums, archive/extract operations, and large directory enumeration that is more faithfully handled by the OS."
        )
        lines.append(
            "- Treat `shell_command` as the local platform's command-line toolbox: when a task sounds terminal-native, actively consider what existing CLI, project script, Python one-liner, or POSIX utility can do it more faithfully than manual reconstruction."
        )
        lines.append(
            "- Common shell utilities that may be appropriate when available include `mkdir`, `cp`, `mv`, `rsync`, `find`, `du`, `wc`, `grep`, `sed`, `awk`, `sort`, `uniq`, `head`, `tail`, `tar`, `gzip`, `unzip`, `shasum`/`sha256sum`, `python`, and project package-manager commands."
        )
        lines.append(
            "- Prefer `shell_command` over reconstructing files through `file_read` + `file_write` when the task is to faithfully copy, move, archive, extract, checksum, or execute existing files/directories."
        )
        lines.append(
            "- Prefer structured `file_read`, `file_edit`, and `file_write` for small precise source inspection and source edits; do not use shell heredocs or redirection to simulate those structured tools."
        )
        if not ({"git_status", "git_diff", "git_log"} & set(available)):
            lines.append(
                "- Do not use shell `git` commands when git tools are unavailable; that usually means the workspace is not a git repository."
            )
    return "\n".join(lines)


def _tool_budget_profile(request: CompletionRequest) -> str | None:
    profile = str(request.metadata.get("tool_budget_profile") or "").strip()
    return profile or None


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
            group = [
                str(name or "").strip() for name in required if str(name or "").strip()
            ]
            if group:
                alternative_groups.append(group)

    if not alternative_groups:
        return []
    if any(
        all(not _tool_argument_is_missing(arguments, key) for key in group)
        for group in alternative_groups
    ):
        return []
    return alternative_groups


def _tool_argument_validation_error(
    tool_id: str,
    missing_required: Sequence[str],
    alternative_required_groups: Sequence[Sequence[str]] | None = None,
) -> str:
    detail_parts: list[str] = []
    required_text = ", ".join(
        str(name).strip() for name in missing_required if str(name).strip()
    )
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


def _file_edit_missing_content_argument(arguments: dict[str, Any]) -> str:
    def _present(value: Any) -> bool:
        return not _tool_argument_is_missing({"value": value}, "value")

    def _edit_has_replacement(edit: dict[str, Any]) -> bool:
        if _present(edit.get("old_string")) and (
            _present(edit.get("new_string")) or _present(edit.get("content"))
        ):
            return True
        for key in ("content", "replace", "replacement", "new_content", "new_string"):
            if _present(edit.get(key)):
                return True
        return False

    if isinstance(arguments.get("edits"), list):
        for index, edit in enumerate(arguments.get("edits") or []):
            if not isinstance(edit, dict):
                continue
            mode = str(edit.get("mode") or "replace").strip().lower()
            if (
                mode != "delete"
                and _present(edit.get("start_line"))
                and not _edit_has_replacement(edit)
            ):
                return f"edits[{index}].content"
        return ""

    mode = str(arguments.get("mode") or "replace").strip().lower()
    if mode == "delete":
        return ""
    if _present(arguments.get("old_string")) and (
        _present(arguments.get("new_string")) or _present(arguments.get("content"))
    ):
        return ""
    if _present(arguments.get("start_line")) and not _present(arguments.get("content")):
        return "content"
    return ""


def _file_edit_placeholder_content_argument(arguments: dict[str, Any]) -> str:
    def _present(value: Any) -> bool:
        return not _tool_argument_is_missing({"value": value}, "value")

    def _is_placeholder(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        normalized = " ".join(value.strip().lower().split())
        if not normalized:
            return False
        return any(
            phrase in normalized
            for phrase in (
                "dummy call",
                "will error",
                "use file_read instead",
            )
        )

    def _placeholder_key(container: dict[str, Any]) -> str:
        if _present(container.get("old_string")):
            for key in ("new_string", "content"):
                if _present(container.get(key)) and _is_placeholder(container.get(key)):
                    return key
            return ""
        for key in ("content", "replace", "replacement", "new_content", "new_string"):
            if _present(container.get(key)) and _is_placeholder(container.get(key)):
                return key
        return ""

    if isinstance(arguments.get("edits"), list):
        for index, edit in enumerate(arguments.get("edits") or []):
            if not isinstance(edit, dict):
                continue
            mode = str(edit.get("mode") or "replace").strip().lower()
            if mode == "delete":
                continue
            key = _placeholder_key(edit)
            if key:
                return f"edits[{index}].{key}"
        return ""

    mode = str(arguments.get("mode") or "replace").strip().lower()
    if mode == "delete":
        return ""
    return _placeholder_key(arguments)


def _file_edit_empty_batch_argument(arguments: dict[str, Any]) -> str:
    if "edits" not in arguments:
        return ""
    edits = arguments.get("edits")
    if not isinstance(edits, list) or not edits:
        return "edits"
    return ""


def _file_edit_delete_replacement_argument(arguments: dict[str, Any]) -> str:
    replacement_keys = (
        "content",
        "new_string",
        "replace",
        "replacement",
        "new_content",
    )

    def _has_replacement_field(container: dict[str, Any]) -> str:
        for key in replacement_keys:
            if key in container and container.get(key) is not None:
                return key
        return ""

    if isinstance(arguments.get("edits"), list):
        for index, edit in enumerate(arguments.get("edits") or []):
            if not isinstance(edit, dict):
                continue
            mode = str(edit.get("mode") or "replace").strip().lower()
            if mode != "delete":
                continue
            key = _has_replacement_field(edit)
            if key:
                return f"edits[{index}].{key}"
        return ""

    mode = str(arguments.get("mode") or "replace").strip().lower()
    if mode != "delete":
        return ""
    key = _has_replacement_field(arguments)
    return key


def _file_edit_compatibility_mode_argument(arguments: dict[str, Any]) -> str:
    def _has_old_text(container: dict[str, Any]) -> bool:
        return "old_string" in container and container.get("old_string") is not None

    def _has_replacement(container: dict[str, Any]) -> bool:
        return any(
            key in container and container.get(key) is not None
            for key in ("new_string", "content")
        )

    if isinstance(arguments.get("edits"), list):
        for index, edit in enumerate(arguments.get("edits") or []):
            if not isinstance(edit, dict):
                continue
            mode = str(edit.get("mode") or "replace").strip().lower()
            if mode != "replace" and _has_old_text(edit) and _has_replacement(edit):
                return f"edits[{index}].mode"
        return ""

    mode = str(arguments.get("mode") or "replace").strip().lower()
    if mode != "replace" and _has_old_text(arguments) and _has_replacement(arguments):
        return "mode"
    return ""


def _tool_specific_argument_validation_error(
    tool_id: str,
    arguments: dict[str, Any],
) -> str:
    if tool_id == "file_edit":
        empty_batch = _file_edit_empty_batch_argument(arguments)
        if empty_batch:
            return (
                "tool_arguments_invalid: invalid arguments for file_edit: "
                "edits must be a non-empty list when provided"
            )
        delete_replacement = _file_edit_delete_replacement_argument(arguments)
        if delete_replacement:
            return (
                "tool_arguments_invalid: invalid argument combination for file_edit: "
                f"{delete_replacement} cannot be used with delete mode. Delete mode removes text only; "
                "retry with mode 'replace' if you intend to swap text."
            )
        compatibility_mode = _file_edit_compatibility_mode_argument(arguments)
        if compatibility_mode:
            return (
                "tool_arguments_invalid: invalid argument combination for file_edit: "
                f"{compatibility_mode} cannot combine old_string plus new_string/content with non-replace mode. "
                "Compatibility replacement uses mode 'replace' or omits mode."
            )
        missing = _file_edit_missing_content_argument(arguments)
        if missing:
            return _tool_argument_validation_error(tool_id, [missing])
        placeholder = _file_edit_placeholder_content_argument(arguments)
        if placeholder:
            return (
                "tool_arguments_invalid: invalid arguments for file_edit: "
                f"{placeholder} contains dummy read-instruction edit content"
            )
    return ""


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
            " For `web_search`, retry with exactly one concrete JSON object such as "
            '`{"query":"Brent crude oil price April 2026"}` or `{"url":"https://example.com/page"}`. '
            "Do not send `{}` and do not batch multiple empty `web_search` calls in the same round."
        )
    elif tool_id == "file_write":
        extra_guidance = (
            " For `file_write`, retry with exactly one complete JSON object such as "
            '`{"path":"website/index.html","content":"<!doctype html>..."}`. '
            "Do not omit `path` or `content`, and do not send prose instead of the JSON arguments. "
            "Treat failed monolithic writes as risky: if the provider or tool failure indicates truncation or oversized content, "
            "split the next attempt into coherent chunks or switch to `file_edit` for incremental updates to an existing file. "
            "After one failed large write, do not resend the same giant payload."
        )
    elif tool_id == "file_edit":
        stale_anchor_guidance = ""
        if (
            "old_string was not found" in detail
            or "old_string matched multiple" in detail
        ):
            stale_anchor_guidance = (
                " The old_string anchor is stale or ambiguous; do not retry the same exact-text edit "
                "and do not switch to a whole-file rewrite unless the task explicitly asks for a complete overwrite. "
                "Re-read the target file with a focused line range around the intended change, then retry with "
                "`start_line`/`end_line` plus `content` copied against the current file."
            )
        extra_guidance = (
            " For `file_edit`, first choose the edit intent: replacement uses "
            '`{"path":"src/app.py","start_line":12,"end_line":14,"mode":"replace","content":"..."}`; '
            'insertion uses `mode:"insert_before"` or `mode:"insert_after"` plus `content`; '
            'deletion uses `mode:"delete"` with no `content`/`new_string`; '
            "exact-text replacement uses `old_string` plus `new_string` and no delete mode. "
            'For multiple edits, retry with `{"path":"src/app.py","edits":[{"start_line":12,"end_line":14,"mode":"replace","content":"..."}]}`. '
            "Every item in `edits` must include `start_line`, or a unique exact-text compatibility pair "
            "such as `old_string` plus `new_string` copied from a recent `file_read`. "
            "If you do not know the line numbers or exact old text, call `file_read` first."
            f"{stale_anchor_guidance}"
        )
    return (
        f"Tool correction: the previous `{tool_id}` call failed because {detail}. "
        "Retry only with a complete JSON argument object that satisfies the tool schema exactly. "
        "If you do not know the missing values yet, use a different valid tool call first."
        f"{extra_guidance}"
    )


def _source_structure_failure_nudge(
    tool_id: str,
    error_text: str,
    arguments: dict[str, Any],
) -> str | None:
    if tool_id not in {"file_edit", "file_write"}:
        return None
    normalized = " ".join(str(error_text or "").lower().split())
    markers = (
        "suspicious source structure",
        "suspicious source shrink",
        "duplicate function definitions",
        "unreachable statement after return",
        "invalid edit shape for file_edit",
        "invalid content for file_write",
        "empty control block",
        "unexpected indentation",
    )
    if not any(marker in normalized for marker in markers):
        return None
    path = str(arguments.get("path") or arguments.get("file_path") or "").strip()
    line_hint = ""
    start_line = arguments.get("start_line")
    end_line = arguments.get("end_line")
    if start_line not in {None, ""} or end_line not in {None, ""}:
        line_hint = f" around lines {start_line or 1}-{end_line or 'EOF'}"
    target = f"`{path}`" if path else "the target source file"
    return (
        f"Source-structure guard: the previous `{tool_id}` was rejected for {error_text}. "
        f"Treat {target} as unchanged and do not retry the same patch. "
        "If the requested invariant in that file already holds, leave that file alone and move to the next failing requirement. "
        f"Otherwise take one focused `file_read` of {target}{line_hint}, then make a smaller line-range edit. "
        "For duplicate-function failures, add only missing code or edit inside an existing function; do not paste a second copy of an existing function. "
        "For unreachable-code failures, move the assignment before the return or remove it."
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
    raw_arguments = (
        function.get("arguments")
        if isinstance(function, dict)
        else raw_call.get("arguments")
    )
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


def _extract_jsonish_int_field(raw_text: str, field_name: str) -> int | None:
    text = str(raw_text or "")
    if not text.strip():
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        value = parsed.get(field_name)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    pattern = re.compile(
        rf'"{re.escape(field_name)}"\s*:\s*(-?\d+)',
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _recover_tool_arguments_from_raw_text(
    tool_id: str, raw_text: str
) -> dict[str, Any]:
    parsed = parse_jsonish_payload(raw_text)
    if isinstance(parsed, dict):
        return dict(parsed)
    if tool_id not in {"file_edit", "file_write"}:
        return {"raw_arguments": raw_text}

    recovered: dict[str, Any] = {}
    for field_name in (
        "path",
        "file_path",
        "content",
        "mode",
        "encoding",
        "old_string",
        "new_string",
    ):
        value = _extract_jsonish_string_field(raw_text, field_name)
        if value is not None:
            recovered[field_name] = value
    if tool_id == "file_edit":
        for field_name in ("start_line", "end_line"):
            value = _extract_jsonish_int_field(raw_text, field_name)
            if value is not None:
                recovered[field_name] = value
    if recovered:
        recovered["raw_arguments"] = raw_text
        return recovered
    return {"raw_arguments": raw_text}


def _is_repeated_tool_call(
    previous_tool: dict[str, Any] | None,
    current_tool: dict[str, Any],
) -> bool:
    if not isinstance(previous_tool, dict):
        return False
    if (
        str(previous_tool.get("tool_id") or "").strip()
        != str(current_tool.get("tool_id") or "").strip()
    ):
        return False
    if _stable_tool_value(previous_tool.get("arguments")) != _stable_tool_value(
        current_tool.get("arguments")
    ):
        return False
    if bool(previous_tool.get("ok")) != bool(current_tool.get("ok")):
        return False
    payload_key = "result" if current_tool.get("ok") else "error"
    return _stable_tool_value(previous_tool.get(payload_key)) == _stable_tool_value(
        current_tool.get(payload_key)
    )


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
        relative = _relative_workspace_path(
            entry.get("path") or entry.get("name"), workspace_root=workspace_root
        )
        if relative:
            effective.append(relative)
    return effective


def _tool_confirms_effectively_empty_workspace(
    tool: dict[str, Any], *, workspace_root: Path
) -> bool:
    if str(tool.get("tool_id") or "").strip() != "list_directory" or not tool.get("ok"):
        return False
    relative = _relative_workspace_path(
        dict(tool.get("arguments") or {}).get("path"), workspace_root=workspace_root
    )
    if relative not in {None, ".", ""}:
        return False
    entries = _effective_listing_entry_paths(
        tool.get("result"), workspace_root=workspace_root
    )
    return not entries or all(
        _is_internal_workspace_path(path) or _is_greenfield_operator_artifact(path)
        for path in entries
    )


def _tool_targets_internal_workspace_state(
    tool: dict[str, Any], *, workspace_root: Path
) -> bool:
    tool_id = str(tool.get("tool_id") or "").strip()
    if tool_id not in {"list_directory", "file_read"}:
        return False
    relative = _relative_workspace_path(
        dict(tool.get("arguments") or {}).get("path"), workspace_root=workspace_root
    )
    return _is_internal_workspace_path(relative)


def _tool_reads_greenfield_operator_artifact(
    tool: dict[str, Any], *, workspace_root: Path
) -> bool:
    if str(tool.get("tool_id") or "").strip() != "file_read" or not tool.get("ok"):
        return False
    relative = _relative_workspace_path(
        dict(tool.get("arguments") or {}).get("path"), workspace_root=workspace_root
    )
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
    expected_shape = str(
        getattr(request.output_contract, "expected_return_shape", "") or ""
    )
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
        result.get("path") or arguments.get("path") or arguments.get("file_path") or ""
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
    if len(raw_text) >= _FILE_WRITE_RAW_ARGUMENT_RISKY_LENGTH and not trimmed.endswith(
        "}"
    ):
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


def _repair_policy_blocked_shrinking_overwrite(
    request: CompletionRequest,
    arguments: dict[str, Any],
    *,
    workspace_root: Path,
) -> tuple[str, int, int] | None:
    policy = request.metadata.get("repair_policy")
    if not isinstance(policy, dict):
        return None
    if not bool(policy.get("forbid_shrinking_existing_artifacts")):
        return None
    mode = str(arguments.get("mode") or "overwrite").strip().lower()
    if mode != "overwrite":
        return None
    content = arguments.get("content")
    if not isinstance(content, str):
        return None
    relative_path = _relative_workspace_path(
        arguments.get("path") or arguments.get("file_path"),
        workspace_root=workspace_root,
    )
    if not relative_path:
        return None
    target_paths = {
        path
        for path in (
            _relative_workspace_path(item, workspace_root=workspace_root)
            for item in (policy.get("target_paths") or [])
        )
        if path
    }
    if target_paths and relative_path not in target_paths:
        return None
    candidate = (workspace_root / relative_path).resolve(strict=False)
    try:
        current_size = candidate.stat().st_size
    except OSError:
        return None
    encoding = str(arguments.get("encoding") or "utf-8")
    try:
        new_size = len(content.encode(encoding))
    except Exception:
        new_size = len(content.encode("utf-8", errors="ignore"))
    if new_size >= current_size:
        return None
    return relative_path, current_size, new_size


def _repair_policy_shrinking_overwrite_message(
    path: str,
    current_size: int,
    new_size: int,
) -> str:
    return (
        f"Repair policy blocked a whole-file overwrite of `{path}` because validation is asking for an additive "
        f"repair and the proposed replacement would shrink the existing artifact from {current_size} bytes to "
        f"{new_size} bytes. Preserve the current substance. Use `file_edit`, append, or a complete expanded "
        "replacement that addresses the validator feedback without turning the artifact into a smaller baseline."
    )


def _normalized_tool_path(
    path: Any, *, workspace_root: Path | None = None
) -> str | None:
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


def _workspace_file_snapshot(
    workspace_root: Path,
    *,
    max_files: int = _SHELL_WORKSPACE_CHANGE_MAX_FILES,
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    files: dict[str, tuple[int, int]] = {}
    truncated = False
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                dirname
                for dirname in sorted(dirnames)
                if dirname not in _SHELL_WORKSPACE_CHANGE_SKIP_DIR_NAMES
            ]
            base = Path(dirpath)
            for filename in sorted(filenames):
                if len(files) >= max_files:
                    truncated = True
                    return {"files": files, "truncated": truncated}
                path = base / filename
                try:
                    stat = path.stat()
                    relative = str(path.relative_to(root))
                except (OSError, ValueError):
                    continue
                files[relative] = (int(stat.st_size), int(stat.st_mtime_ns))
    except OSError:
        truncated = True
    return {"files": files, "truncated": truncated}


def _workspace_snapshot_diff(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    *,
    max_paths: int = 300,
) -> dict[str, Any]:
    before_files = before.get("files") if isinstance(before, Mapping) else None
    after_files = after.get("files") if isinstance(after, Mapping) else None
    if not isinstance(before_files, dict) or not isinstance(after_files, dict):
        return {}
    before_keys = set(before_files)
    after_keys = set(after_files)
    created_all = sorted(after_keys - before_keys)
    deleted_all = sorted(before_keys - after_keys)
    modified_all = sorted(
        path
        for path in before_keys & after_keys
        if before_files.get(path) != after_files.get(path)
    )
    changed_all = created_all + modified_all + deleted_all
    return {
        "created": created_all[:max_paths],
        "modified": modified_all[:max_paths],
        "deleted": deleted_all[:max_paths],
        "changed_paths": changed_all[:max_paths],
        "change_count": len(changed_all),
        "truncated": bool(
            before.get("truncated") if isinstance(before, Mapping) else False
        )
        or bool(after.get("truncated") if isinstance(after, Mapping) else False)
        or len(changed_all) > max_paths,
    }


def _workspace_diff_without_restored_paths(
    changes: Mapping[str, Any],
    *,
    restored_paths: Sequence[Any],
    workspace_root: Path,
) -> dict[str, Any]:
    restored_relative: set[str] = set()
    for raw_path in restored_paths:
        try:
            relative = str(Path(str(raw_path)).resolve().relative_to(workspace_root))
        except (OSError, ValueError):
            continue
        restored_relative.add(relative)
    if not restored_relative:
        return dict(changes)

    filtered = dict(changes)
    for key in ("created", "modified", "deleted", "changed_paths"):
        values = changes.get(key)
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        filtered[key] = [
            str(value) for value in values if str(value) not in restored_relative
        ]
    changed_paths = filtered.get("changed_paths")
    if isinstance(changed_paths, list):
        filtered["change_count"] = len(changed_paths)
    return filtered


def _shell_command_words(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return []


def _shell_command_segments(command: str) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for word in _shell_command_words(command):
        if word in {"|", "||", "&", "&&", ";"}:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(word)
    if current:
        segments.append(current)
    return segments


def _shell_segment_executable_index(segment: Sequence[str]) -> int | None:
    index = 0
    while index < len(segment):
        word = str(segment[index])
        executable = Path(word).name.lower()
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word):
            index += 1
            continue
        if executable == "env":
            index += 1
            while index < len(segment):
                option = str(segment[index])
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", option):
                    index += 1
                    continue
                if option in {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}:
                    index += 2
                    continue
                if option.startswith("-"):
                    index += 1
                    continue
                break
            continue
        if executable == "command":
            index += 1
            if index < len(segment) and str(segment[index]) in {"-v", "-V"}:
                return None
            while index < len(segment) and str(segment[index]).startswith("-"):
                index += 1
            continue
        if executable == "nohup":
            index += 1
            while index < len(segment) and str(segment[index]).startswith("-"):
                index += 1
            continue
        if executable == "sudo":
            value_options = {
                "-C",
                "-D",
                "-g",
                "-h",
                "-p",
                "-r",
                "-R",
                "-t",
                "-T",
                "-u",
                "--chdir",
                "--group",
                "--host",
                "--prompt",
                "--role",
                "--type",
                "--user",
            }
            index += 1
            while index < len(segment):
                option = str(segment[index])
                if option in value_options:
                    index += 2
                    continue
                if any(option.startswith(prefix + "=") for prefix in value_options):
                    index += 1
                    continue
                if option.startswith("-"):
                    index += 1
                    continue
                break
            continue
        return index
    return None


def _shell_segment_command_index(
    segment: Sequence[str],
    executable_name: str,
) -> int | None:
    index = _shell_segment_executable_index(segment)
    if index is None:
        return None
    return index if Path(str(segment[index])).name.lower() == executable_name else None


def _shell_word_path_fragments(word: str) -> list[str]:
    fragments = [word]
    fragments.extend(
        match.group(1)
        for match in re.finditer(r"""["']([^"'\\]*(?:\\.[^"'\\]*)*)["']""", word)
    )
    fragments.extend(
        match.group(0)
        for match in re.finditer(
            r"(?<![\w.-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,12}(?![\w.-])",
            word,
        )
    )
    return list(dict.fromkeys(fragments))


def _existing_workspace_files_named_by_shell_command(
    command: str,
    *,
    working_directory: Path,
    workspace_root: Path,
) -> list[Path]:
    root = workspace_root.resolve()
    paths: list[Path] = []
    seen: set[Path] = set()
    for raw_word in _shell_command_words(command):
        for raw_fragment in _shell_word_path_fragments(str(raw_word or "")):
            word = str(raw_fragment or "").strip()
            if (
                not word
                or word.startswith("-")
                or word in {"|", "||", "&", "&&", ";", "<", ">", ">>"}
                or any(character in word for character in "*?[]{}")
            ):
                continue
            candidate = Path(word).expanduser()
            if not candidate.is_absolute():
                candidate = working_directory / candidate
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            if not resolved.is_file() or resolved in seen:
                continue
            seen.add(resolved)
            paths.append(resolved)
    return paths


def _existing_shell_redirection_targets(
    command: str,
    *,
    working_directory: Path,
    workspace_root: Path,
) -> list[Path]:
    words = _shell_command_words(command)
    targets: list[Path] = []
    for index, word in enumerate(words[:-1]):
        if word not in {">", ">>"}:
            continue
        raw_target = str(words[index + 1] or "").strip()
        if not raw_target or raw_target.startswith("&") or raw_target == "/dev/null":
            continue
        candidate = Path(raw_target).expanduser()
        if not candidate.is_absolute():
            candidate = working_directory / candidate
        try:
            resolved = candidate.resolve()
            resolved.relative_to(workspace_root.resolve())
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            targets.append(resolved)
    return targets


def _unsafe_direct_shell_mutation_error(
    command: str,
    *,
    working_directory: Path,
    workspace_root: Path,
) -> str:
    for segment in _shell_command_segments(command):
        executable_index = _shell_segment_executable_index(segment)
        if executable_index is None:
            continue
        executable = Path(str(segment[executable_index])).name.lower()
        arguments = [str(word) for word in segment[executable_index + 1 :]]
        if executable == "sed" and any(
            argument == "--in-place"
            or argument.startswith("--in-place=")
            or (
                argument.startswith("-")
                and not argument.startswith("--")
                and "i" in argument[1:]
            )
            for argument in arguments
        ):
            return (
                "unsafe direct shell mutation: in-place sed is disabled for Super DAN "
                "workspace files; use file_edit or file_write so the replacement is atomic"
            )
        if executable == "perl" and any(
            argument.startswith("-") and "i" in argument[1:] for argument in arguments
        ):
            return (
                "unsafe direct shell mutation: in-place perl editing is disabled for Super "
                "DAN workspace files; use file_edit or file_write"
            )
        if executable == "truncate":
            return (
                "unsafe direct shell mutation: truncate is disabled for Super DAN workspace "
                "files; use an atomic structured file tool"
            )
        if executable in {"rm", "rmdir", "unlink"} or (
            executable == "xargs"
            and any(
                Path(argument).name.lower() in {"rm", "rmdir", "unlink"}
                for argument in arguments
            )
        ):
            return (
                "unsafe direct shell mutation: direct shell deletion is disabled for Super "
                "DAN workspace files; use a structured, reviewable file operation"
            )
        if executable == "find" and "-delete" in arguments:
            return (
                "unsafe direct shell mutation: find -delete is disabled for Super DAN "
                "workspace files; use a structured, reviewable file operation"
            )
    destructive_git = _destructive_git_shell_invocation(command)
    if destructive_git:
        return (
            "unsafe direct shell mutation: destructive git restoration/cleanup is "
            f"disabled in the Super DAN shell lane ({destructive_git})"
        )
    redirection_targets = _existing_shell_redirection_targets(
        command,
        working_directory=working_directory,
        workspace_root=workspace_root,
    )
    if redirection_targets:
        rendered = ", ".join(
            str(path.relative_to(workspace_root.resolve()))
            for path in redirection_targets[:3]
        )
        return (
            "unsafe direct shell mutation: shell redirection cannot overwrite existing "
            f"workspace files ({rendered}); use file_edit or file_write"
        )
    return ""


def _destructive_git_shell_invocation(command: str) -> str:
    value_options = {
        "-C",
        "-c",
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--work-tree",
    }
    for segment in _shell_command_segments(command):
        git_index = _shell_segment_command_index(segment, "git")
        if git_index is None:
            continue
        index = git_index + 1
        while index < len(segment):
            word = str(segment[index])
            if word == "--":
                index += 1
                continue
            if word in value_options:
                index += 2
                continue
            if any(word.startswith(option + "=") for option in value_options):
                index += 1
                continue
            if word.startswith("-"):
                index += 1
                continue
            subcommand = word.lower()
            if subcommand in {"checkout", "clean", "reset", "restore"}:
                return f"git {subcommand}"
            break
    return ""


def _protected_workspace_file_state(
    path: Path,
    *,
    workspace_root: Path,
) -> dict[str, Any] | None:
    try:
        resolved = path.resolve()
        resolved.relative_to(workspace_root.resolve())
        stat_result = resolved.stat()
    except (OSError, ValueError):
        return None
    if not resolved.is_file() or stat_result.st_size > _SHELL_PROTECTED_FILE_MAX_BYTES:
        return None
    try:
        content = resolved.read_bytes()
    except OSError:
        return None
    return {
        "path": resolved,
        "content": content,
        "mode": int(stat_result.st_mode & 0o7777),
        "atime_ns": int(stat_result.st_atime_ns),
        "mtime_ns": int(stat_result.st_mtime_ns),
    }


def _protected_state_changed(before: Mapping[str, Any]) -> bool:
    path = before.get("path")
    if not isinstance(path, Path):
        return False
    try:
        return path.read_bytes() != before.get("content")
    except OSError:
        return True


def _protected_state_has_catastrophic_loss(before: Mapping[str, Any]) -> bool:
    path = before.get("path")
    original = before.get("content")
    if not isinstance(path, Path) or not isinstance(original, bytes) or not original:
        return False
    try:
        current = path.read_bytes()
    except OSError:
        return True
    if not current:
        return True
    return len(current) <= int(len(original) * _SHELL_CATASTROPHIC_SHRINK_RATIO)


def _restore_protected_file_states(
    states: Sequence[Mapping[str, Any]],
) -> tuple[list[str], list[str]]:
    restored: list[str] = []
    failed: list[str] = []
    for state in states:
        path = state.get("path")
        content = state.get("content")
        if not isinstance(path, Path) or not isinstance(content, bytes):
            continue
        try:
            atomic_write_bytes(path, content)
            mode = state.get("mode")
            if isinstance(mode, int):
                os.chmod(path, mode)
            atime_ns = state.get("atime_ns")
            mtime_ns = state.get("mtime_ns")
            if isinstance(atime_ns, int) and isinstance(mtime_ns, int):
                os.utime(path, ns=(atime_ns, mtime_ns))
            restored.append(str(path))
        except OSError as exc:
            failed.append(f"{path}: {type(exc).__name__}: {exc}")
    return restored, failed


def _shell_move_source_paths(
    command: str,
    *,
    working_directory: Path,
    workspace_root: Path,
) -> set[Path]:
    root = workspace_root.resolve()
    sources: set[Path] = set()
    for segment in _shell_command_segments(command):
        if not segment or Path(str(segment[0])).name.lower() != "mv":
            continue
        move_index = 0
        operands: list[str] = []
        options_done = False
        for raw_word in segment[move_index + 1 :]:
            word = str(raw_word)
            if word in {"<", ">", ">>"}:
                break
            if not options_done and word == "--":
                options_done = True
                continue
            if not options_done and word.startswith("-"):
                continue
            operands.append(word)
        if len(operands) < 2:
            continue
        for raw_source in operands[:-1]:
            candidate = Path(raw_source).expanduser()
            if not candidate.is_absolute():
                candidate = working_directory / candidate
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            sources.add(resolved)
    return sources


def _tool_path_for_cache(path: Any, *, workspace_root: Path) -> Path | None:
    text = str(path or "").strip()
    if not text:
        return None
    root = Path(workspace_root).expanduser().resolve()
    if text == "/workspace":
        candidate = root
    elif text.startswith("/workspace/"):
        candidate = root / text[len("/workspace/") :]
    else:
        raw = Path(text).expanduser()
        candidate = raw if raw.is_absolute() else root / raw
    try:
        return candidate.resolve()
    except OSError:
        return None


def _tool_file_fingerprint(
    path: Any, *, workspace_root: Path
) -> tuple[str, int, int, int, int] | None:
    candidate = _tool_path_for_cache(path, workspace_root=workspace_root)
    if candidate is None:
        return None
    try:
        stat = candidate.stat()
    except OSError:
        return None
    if not candidate.is_file():
        return None
    return (
        str(candidate),
        int(getattr(stat, "st_ino", 0)),
        int(stat.st_size),
        int(stat.st_mtime_ns),
        int(getattr(stat, "st_ctime_ns", 0)),
    )


def _file_read_cache_key(
    arguments: Mapping[str, Any],
    *,
    workspace_root: Path,
) -> tuple[Any, ...] | None:
    path_text = str(
        arguments.get("path")
        or arguments.get("file_path")
        or arguments.get("filepath")
        or ""
    ).strip()
    candidate = _tool_path_for_cache(path_text, workspace_root=workspace_root)
    if candidate is None:
        return None
    return (
        str(candidate),
        arguments.get("start_line"),
        arguments.get("end_line"),
        str(arguments.get("grep") or ""),
        str(arguments.get("encoding") or "utf-8"),
    )


def _shell_workspace_change_paths(tool: Mapping[str, Any]) -> list[str]:
    result = tool.get("result") if isinstance(tool.get("result"), Mapping) else {}
    changes = result.get("workspace_changes") if isinstance(result, Mapping) else {}
    if not isinstance(changes, Mapping):
        return []
    raw_paths = changes.get("changed_paths")
    if not isinstance(raw_paths, Sequence) or isinstance(raw_paths, (str, bytes)):
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        text = str(raw_path or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        paths.append(text)
    return paths


def _tool_materialized_mutation(tool: dict[str, Any]) -> bool:
    if not tool.get("ok"):
        return False
    tool_id = str(tool.get("tool_id") or "").strip()
    if tool_id == "shell_command":
        result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
        try:
            exit_code = int(result.get("exit_code", 0))
        except (TypeError, ValueError):
            exit_code = 0
        return exit_code == 0 and bool(_shell_workspace_change_paths(tool))
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
        if str(tool.get("tool_id") or "").strip() == "shell_command":
            for raw_path in _shell_workspace_change_paths(tool):
                relative = _relative_workspace_path(
                    raw_path,
                    workspace_root=workspace_root,
                )
                if not relative:
                    relative = str(raw_path).strip().lstrip("/\\")
                if not relative or relative in seen:
                    continue
                if _looks_like_temporary_workspace_helper_path(relative):
                    continue
                seen.add(relative)
                paths.append(relative)
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
        "/tmp",
        "/private/tmp",
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
    return any(
        resolved == root or root in resolved.parents for root in _temporary_path_roots()
    )


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


def _successful_verification_commands(
    executed_tools: Sequence[dict[str, Any]],
) -> list[str]:
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


def _last_successful_mutation_index(
    executed_tools: Sequence[dict[str, Any]],
) -> int | None:
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


def _materialized_file_content_snapshot(
    target_files: Sequence[str],
    *,
    workspace_root: Path | None,
) -> dict[str, str]:
    """Read small UTF-8 text artifacts without embedding binary output."""

    if workspace_root is None:
        return {}

    snapshots: dict[str, str] = {}
    root = workspace_root.expanduser().resolve()
    for path in target_files:
        relative = str(path or "").strip()
        if not relative:
            continue
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        try:
            if candidate.stat().st_size > 256_000:
                continue
            content = candidate.read_bytes()
        except OSError:
            continue
        if b"\x00" in content:
            continue
        try:
            snapshots[relative] = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return snapshots


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
        tracked_diff_paths = _tracked_workspace_diff_paths(
            workspace_root=workspace_root
        )
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
    file_snapshots = _materialized_file_content_snapshot(
        target_files,
        workspace_root=workspace_root,
    )

    payload = dict(base_payload)
    if output_kind == "candidate":
        payload.setdefault("candidate_id", "partial-candidate-from-tool-evidence")
    else:
        payload.setdefault("candidate_fragment", {})
    if file_snapshots:
        payload["candidate_fragment"] = file_snapshots
        payload["readback_files"] = list(file_snapshots)
    payload["change_summary"] = str(
        payload.get("change_summary")
        or (
            f"Materialized {len(target_files)} file(s) during the write-capable coding stage, "
            f"but the model did not return the final candidate payload before {reason_text}."
        )
    ).strip()
    payload["target_files"] = target_files
    payload["test_plan"] = verification_commands or [
        (
            (
                "Runtime read back the materialized files from disk; inspect the synthesized "
                "candidate_fragment and rerun bounded validation."
            )
            if file_snapshots
            else "Inspect the materialized files and rerun the bounded validation step."
        ),
    ]
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
    if _request_forbids_workspace_mutation(request):
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
    exclusive_owner_path = _exclusive_write_owner_path(request)
    if exclusive_owner_path:
        read_paths = _successful_read_paths(
            executed_tools,
            workspace_root=workspace_root,
        )
        if exclusive_owner_path in read_paths:
            return "exclusive_write_owner_after_first_read"
    for tool in executed_tools:
        if _tool_confirms_effectively_empty_workspace(
            tool, workspace_root=workspace_root
        ):
            return "first_write_after_empty_workspace"
    if len(
        _successful_read_paths(executed_tools, workspace_root=workspace_root)
    ) >= _prewrite_successful_read_nudge_threshold(request):
        return "stalled_analysis_before_first_write"
    if (
        _successful_shell_command_count(executed_tools)
        >= _PREWRITE_SHELL_ANALYSIS_NUDGE_THRESHOLD
    ):
        return "stalled_analysis_before_first_write"
    return None


def _prewrite_successful_read_nudge_threshold(request: CompletionRequest) -> int:
    if _exclusive_write_owner_path(request):
        return 1
    if _is_builder_retry_request(request) or _recommended_write_paths(request):
        return 1
    if _is_validation_repair_request(request):
        return 1
    if _interactive_source_implementation_request(request):
        return 2
    organism_stage = str(request.metadata.get("organism_stage") or "").strip().lower()
    if organism_stage == "aggregation":
        return _AGGREGATION_PREWRITE_SUCCESSFUL_READ_NUDGE_THRESHOLD
    return _PREWRITE_SUCCESSFUL_READ_NUDGE_THRESHOLD


def _is_builder_retry_request(request: CompletionRequest) -> bool:
    return request.metadata.get("builder_retry") is True


def _interactive_source_implementation_request(request: CompletionRequest) -> bool:
    return request.metadata.get("interactive_source_implementation") is True


def _exclusive_write_owner_path(request: CompletionRequest) -> str:
    return str(request.metadata.get("exclusive_write_owner_path") or "").strip()


def _recommended_write_paths(request: CompletionRequest) -> list[str]:
    raw_paths = request.metadata.get("recommended_write_paths")
    paths: list[str] = []
    if isinstance(raw_paths, str):
        paths.extend(part.strip() for part in raw_paths.split(","))
    elif isinstance(raw_paths, Sequence) and not isinstance(
        raw_paths, (bytes, bytearray)
    ):
        paths.extend(str(path).strip() for path in raw_paths)
    owner_path = _exclusive_write_owner_path(request)
    if owner_path:
        paths.insert(0, owner_path)
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        if not path or path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


def _is_validation_repair_request(request: CompletionRequest) -> bool:
    return request.metadata.get("validation_repair") is True


def _required_repair_write_paths(request: CompletionRequest) -> list[str]:
    raw_paths = request.metadata.get("required_repair_paths")
    paths: list[str] = []
    if isinstance(raw_paths, str):
        paths.extend(part.strip() for part in raw_paths.split(","))
    elif isinstance(raw_paths, Sequence) and not isinstance(
        raw_paths, (bytes, bytearray)
    ):
        paths.extend(str(path).strip() for path in raw_paths)
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        normalized = str(path or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _missing_required_repair_write_paths(
    request: CompletionRequest,
    executed_tools: Sequence[dict[str, Any]],
    *,
    workspace_root: Path,
) -> list[str]:
    required = _required_repair_write_paths(request)
    if not required:
        return []
    materialized = set(
        _successful_workspace_mutation_paths(
            executed_tools,
            workspace_root=workspace_root,
        )
    )
    missing: list[str] = []
    for raw_path in required:
        normalized = (
            _normalized_tool_path(raw_path, workspace_root=workspace_root) or raw_path
        )
        if normalized not in materialized:
            missing.append(normalized)
    return missing


def _recommended_write_paths_sentence(
    request: CompletionRequest,
    *,
    prefix: str = "Recommended builder retry targets",
) -> str:
    paths = _recommended_write_paths(request)
    if not paths:
        return ""
    rendered = ", ".join(f"`{path}`" for path in paths[:6])
    first_path = paths[0]
    return (
        f" {prefix}: {rendered}. Prefer `{first_path}` for the next durable write unless the "
        "latest tool evidence proves another listed path is the correct artifact."
    )


def _exclusive_write_owner_prefers_file_edit(
    request: CompletionRequest,
    tool_schemas: Sequence[dict[str, Any]],
    *,
    workspace_root: Path,
) -> bool:
    owner_path = _exclusive_write_owner_path(request)
    if not owner_path:
        return False
    if "file_edit" not in {
        _tool_schema_name(tool) for tool in tool_schemas if isinstance(tool, dict)
    }:
        return False
    try:
        target_path = (workspace_root / owner_path).resolve()
        workspace = workspace_root.resolve()
        target_path.relative_to(workspace)
    except (OSError, ValueError):
        return False
    return target_path.is_file()


def _write_capable_coding_stage_first_write_nudge_message(
    reason: str,
    *,
    request: CompletionRequest,
    allow_final_read: bool,
    file_write_only: bool = False,
    file_edit_only: bool = False,
) -> str:
    reason_text = reason.replace("_", " ")
    final_read_sentence = (
        " If you still need exact line grounding, you may take at most one final targeted `file_read` immediately before the first write, then stop auditing and write."
        if allow_final_read
        else ""
    )
    direct_tool_instruction = (
        "The next tool call should be `file_edit` with `path`, `start_line`, `end_line`, and `content` "
        "for a bounded line-range update to the owned file, not another read, search, or final prose-only answer."
        if file_edit_only
        else (
            "The next tool call should be `file_write` with complete replacement content for the owned file, "
            "not `file_edit`, another read, search, or final prose-only answer."
            if file_write_only
            else (
                "The next tool call should be `file_write`, `file_edit`, or `shell_command` when the direct "
                "workspace mutation/verification is naturally a command-line operation, not another read, search, "
                "or final prose-only answer."
            )
        )
    )
    return (
        "Controller note: this write-capable coding stage is still read-only after initial discovery "
        f"({reason_text}). Stop auditing and make the first concrete project write now using the direct file tools "
        f"that are already enabled. {direct_tool_instruction}"
        f"{_recommended_write_paths_sentence(request)}"
        f"{final_read_sentence} Create one or two small real files first, then continue incrementally. Prefer a "
        "minimal runnable slice over a complete project in one giant tool call. If you truly cannot materialize any "
        "bounded file set in this turn, return an explicit blocked candidate now instead of doing more discovery."
    )


def _exclusive_write_owner_read_scope_message(owner_path: str) -> str:
    return (
        f"Controller note: this exclusive write-owner lane owns `{owner_path}`. "
        "Before the first write, use at most one `file_read` of that owned file. "
        "Do not read other files or repeat owner-file reads in this lane. Use the existing owned-file content "
        "and make the bounded write now. If `file_edit` is enabled, prefer one targeted line-range edit over "
        "a whole-file replacement."
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
        if _tool_mutates_temporary_workspace_helper_path(
            tool, workspace_root=workspace_root
        ):
            return "temporary_workspace_helper_write_after_workspace_patch"
    if _exclusive_write_owner_path(request):
        return "exclusive_write_owner_workspace_patch_materialized"

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
            command = str(
                dict(tool.get("arguments") or {}).get("command") or ""
            ).strip()
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
    if _coding_output_kind(request) is None:
        return None
    if _tool_ids_are_read_only(tool_ids):
        return None
    missing_repair_paths = _missing_required_repair_write_paths(
        request,
        executed_tools,
        workspace_root=workspace_root,
    )
    if missing_repair_paths:
        rendered = ",".join(missing_repair_paths[:4])
        return f"validation_repair_missing_required_paths:{rendered}"
    if not direct_write_required:
        return None
    if _successful_workspace_mutation_paths(
        executed_tools,
        workspace_root=workspace_root,
    ):
        return None
    return "returned_without_materializing_workspace_patch"


def _write_capable_coding_stage_direct_write_required_message(
    reason: str,
    *,
    request: CompletionRequest,
) -> str:
    reason_text = reason.replace("_", " ")
    if reason.startswith("validation_repair_missing_required_paths:"):
        _, _, raw_paths = reason.partition(":")
        rendered_paths = ", ".join(f"`{path}`" for path in raw_paths.split(",") if path)
        return (
            "Controller note: this validation-repair stage has not yet mutated every required repair target "
            f"({rendered_paths or 'the listed repair paths'}). A partial repair or prose-only summary is not enough. "
            "The next response must call `file_edit`, `file_write`, or a naturally mutating `shell_command` against "
            "one of the missing paths, or return an explicit blocked candidate explaining why no bounded workspace "
            "write can be completed. Do not re-audit the whole project before making that targeted repair."
        )
    return (
        "Controller note: this write-capable coding stage still has no materialized workspace patch "
        f"({reason_text}). Returning prose-only patch instructions is not enough here. The next response must "
        "call `file_edit`, `file_write`, or `shell_command` when the direct workspace operation is naturally a "
        "command-line operation, to apply the bounded change directly in the checked-out workspace; otherwise "
        f"return an explicit blocked candidate that says no bounded workspace write could be "
        f"completed.{_recommended_write_paths_sentence(request)} Do not propose edits without a real workspace mutation."
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
    prefer_file_write_only: bool = False,
    prefer_file_edit_only: bool = False,
) -> list[dict[str, Any]]:
    available_names = {
        str(tool.get("function", {}).get("name") or "").strip()
        for tool in tool_schemas
        if isinstance(tool, dict)
    }
    if prefer_file_edit_only and "file_edit" in available_names:
        preferred_names = {"file_edit"}
    elif prefer_file_write_only and "file_write" in available_names:
        preferred_names = {"file_write"}
    else:
        preferred_names = {"file_edit", "file_write"}
    if allow_final_read:
        preferred_names.add("file_read")
    if "shell_command" in available_names:
        preferred_names.add("shell_command")
    narrowed: list[dict[str, Any]] = []
    for tool in tool_schemas:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = (
            str(function.get("name") or "").strip()
            if isinstance(function, dict)
            else ""
        )
        if name in preferred_names:
            narrowed.append(tool)
    return narrowed


def _direct_write_tool_schemas(
    tool_schemas: Sequence[dict[str, Any]],
    *,
    prefer_file_write_only: bool = False,
    prefer_file_edit_only: bool = False,
) -> list[dict[str, Any]]:
    available_names = {
        str(tool.get("function", {}).get("name") or "").strip()
        for tool in tool_schemas
        if isinstance(tool, dict)
    }
    if prefer_file_edit_only and "file_edit" in available_names:
        preferred_names = {"file_edit"}
    elif prefer_file_write_only and "file_write" in available_names:
        preferred_names = {"file_write"}
    else:
        preferred_names = {"file_write", "file_edit"}
    if "shell_command" in available_names:
        preferred_names.add("shell_command")
    direct_write_tools = []
    for tool in tool_schemas:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = (
            str(function.get("name") or "").strip()
            if isinstance(function, dict)
            else ""
        )
        if name in preferred_names:
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
            tool.get("ok") and str(tool.get("tool_id") or "").strip() == "git_diff"
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
        if _tool_confirms_effectively_empty_workspace(
            tool, workspace_root=workspace_root
        ):
            empty_workspace_index = index
            break
    if empty_workspace_index is None:
        return None

    for tool in executed_tools[empty_workspace_index + 1 :]:
        if _tool_targets_internal_workspace_state(tool, workspace_root=workspace_root):
            return "internal_state_probe_after_empty_workspace"
        if _tool_reads_greenfield_operator_artifact(
            tool, workspace_root=workspace_root
        ):
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


def _looks_like_research_note_request(request: CompletionRequest) -> bool:
    keys = _expected_return_shape_keys(request)
    return {
        "findings",
        "evidence_refs",
        "contradictions",
        "open_questions",
    }.issubset(keys) and "report_readiness" not in keys


def _soft_budget_progress_snapshot(
    *,
    executed_tools: Sequence[dict[str, Any]],
    workspace_root: Path,
) -> dict[str, int]:
    return {
        "distinct_read_paths": len(
            _successful_read_paths(executed_tools, workspace_root=workspace_root)
        ),
        "mutation_paths": len(
            _successful_workspace_mutation_paths(
                executed_tools,
                workspace_root=workspace_root,
            )
        ),
        "verification_commands": len(_successful_verification_commands(executed_tools)),
        "discovery_tools": _successful_discovery_tool_count(executed_tools),
        "shell_commands": _successful_shell_command_count(executed_tools),
    }


def _soft_budget_phase(
    *,
    request: CompletionRequest,
    tool_ids: Sequence[str],
    executed_tools: Sequence[dict[str, Any]],
    workspace_root: Path,
    write_stage_first_write_nudged: bool,
    write_stage_direct_write_required: bool,
) -> str | None:
    mode = _read_only_finalize_mode(request)
    read_only_tools = _tool_ids_are_read_only(tool_ids)
    if mode == "validator" and read_only_tools:
        return "validator_read_only"
    if mode == "coding_worker" and read_only_tools:
        return "coding_worker_read_only"
    if _looks_like_research_note_request(request) and read_only_tools:
        return "research_note"
    if _coding_output_kind(request) is None or read_only_tools:
        return None
    if _successful_workspace_mutation_paths(
        executed_tools,
        workspace_root=workspace_root,
    ):
        return "coding_postwrite"
    if write_stage_first_write_nudged or write_stage_direct_write_required:
        return "coding_direct_write"
    return "coding_prewrite"


def _soft_budget_extension(
    phase: str | None,
    *,
    limit_kind: str,
    progress: dict[str, int],
) -> int:
    if phase == "coding_prewrite":
        if limit_kind == "tool_calls":
            return min(2, max(0, progress.get("distinct_read_paths", 0) - 3))
        return 1 if progress.get("distinct_read_paths", 0) >= 4 else 0
    if phase == "coding_direct_write":
        if limit_kind == "tool_calls":
            return min(2, max(0, progress.get("distinct_read_paths", 0) - 1))
        return 1 if progress.get("distinct_read_paths", 0) >= 3 else 0
    if phase == "coding_postwrite":
        if limit_kind == "tool_calls":
            return min(
                5,
                progress.get("verification_commands", 0)
                + max(0, progress.get("mutation_paths", 0) - 1),
            )
        return min(3, progress.get("verification_commands", 0))
    if phase in {"validator_read_only", "coding_worker_read_only"}:
        if limit_kind == "tool_calls":
            return min(4, max(0, progress.get("distinct_read_paths", 0) - 2))
        return min(2, max(0, progress.get("distinct_read_paths", 0) - 2))
    if phase == "research_note":
        if limit_kind == "tool_calls":
            return 1 if progress.get("distinct_read_paths", 0) >= 2 else 0
        return 0
    return 0


def _soft_budget_limit(
    phase: str | None,
    *,
    limit_kind: str,
    hard_limit: int | None,
    progress: dict[str, int],
) -> int | None:
    if phase is None:
        return hard_limit
    base_budgets = (
        _SOFT_PHASE_TOOL_CALL_BASE_BUDGETS
        if limit_kind == "tool_calls"
        else _SOFT_PHASE_ROUND_BASE_BUDGETS
    )
    base = base_budgets.get(phase)
    if base is None:
        return hard_limit
    limit = base + _soft_budget_extension(
        phase,
        limit_kind=limit_kind,
        progress=progress,
    )
    if hard_limit is None:
        return limit
    return min(hard_limit, limit)


def _soft_budget_action_for_phase(phase: str | None) -> str | None:
    if phase == "coding_prewrite":
        return "narrow_write_stage"
    if phase == "coding_direct_write":
        return "require_direct_write"
    if phase == "coding_postwrite":
        return "force_finalize"
    if phase in {"validator_read_only", "coding_worker_read_only", "research_note"}:
        return "force_finalize"
    return None


def _dynamic_budget_extension_enabled(
    request: CompletionRequest, *, profile: str | None
) -> bool:
    raw = request.metadata.get("dynamic_budget_extension")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
    return profile == _SOFT_BUDGET_PROFILE_SUPER_DAN


def _dynamic_budget_max_leases(
    request: CompletionRequest, *, profile: str | None
) -> int:
    raw = request.metadata.get("max_budget_extension_leases")
    if raw is None:
        raw = request.metadata.get("dynamic_budget_max_leases")
    if raw is None and profile == _SOFT_BUDGET_PROFILE_SUPER_DAN:
        return _SUPER_DAN_BUDGET_EXTENSION_DEFAULT_MAX_LEASES
    try:
        return max(0, min(int(raw), 4))
    except (TypeError, ValueError):
        return 0


def _short_budget_text(value: Any, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _budget_audit_prompt_payload(
    *,
    request: CompletionRequest,
    limit_kind: str,
    current_limit: int | None,
    rounds: int,
    total_tool_calls: int,
    executed_tools: Sequence[dict[str, Any]],
    workspace_root: Path,
    last_text: str,
) -> dict[str, Any]:
    progress = _soft_budget_progress_snapshot(
        executed_tools=executed_tools,
        workspace_root=workspace_root,
    )
    recent_tools = []
    for tool in list(executed_tools)[-12:]:
        if not isinstance(tool, dict):
            continue
        result = tool.get("result")
        result_summary = ""
        if isinstance(result, dict):
            result_summary = (
                _event_text(
                    result.get("path")
                    or result.get("summary")
                    or result.get("message")
                    or result.get("stdout")
                    or result.get("stderr")
                )
                or ""
            )
        elif result is not None:
            result_summary = _short_budget_text(str(result), limit=180)
        recent_tools.append(
            {
                "tool_id": _event_text(tool.get("tool_id")),
                "ok": bool(tool.get("ok")),
                "error": _event_text(tool.get("error")),
                "arguments": _compact_event_payload(dict(tool.get("arguments") or {})),
                "result_summary": _short_budget_text(result_summary, limit=240),
            }
        )
    return {
        "limit_kind": limit_kind,
        "current_limit": current_limit,
        "rounds_used": rounds,
        "tool_calls_used": total_tool_calls,
        "progress": progress,
        "worker_id": _event_text(request.metadata.get("worker_id")),
        "organism_stage": _event_text(request.metadata.get("organism_stage")),
        "definition_of_done": _short_budget_text(
            getattr(request.output_contract, "definition_of_done", "") or "",
            limit=1000,
        ),
        "expected_return_shape": _short_budget_text(
            getattr(request.output_contract, "expected_return_shape", "") or "",
            limit=1000,
        ),
        "user_prompt": _short_budget_text(request.user_prompt, limit=1600),
        "recent_tools": recent_tools,
        "last_assistant_text": _short_budget_text(last_text, limit=1200),
    }


def _budget_audit_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a no-tool budget auditor for a local DAN worker. Decide whether "
                "a small extra budget lease is justified. Approve only when the evidence "
                "shows concrete progress toward the original task and a small lease is "
                "likely to finish or validate the work. Deny if the worker is looping, "
                "speculating, or has no clear remaining step. Return strict JSON only."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "audit_request": payload,
                    "return_shape": {
                        "approved": "boolean",
                        "extra_rounds": "integer 0-3",
                        "extra_tool_calls": "integer 0-32",
                        "reason": "short operator-readable reason",
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
        },
    ]


def _parse_budget_audit_decision(text: str, *, limit_kind: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        parsed = json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return {"approved": False, "reason": "Budget auditor did not return JSON."}
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return {
                "approved": False,
                "reason": "Budget auditor returned invalid JSON.",
            }
    if not isinstance(parsed, dict):
        return {
            "approved": False,
            "reason": "Budget auditor returned a non-object decision.",
        }
    approved_raw = parsed.get("approved")
    approved = approved_raw is True or (
        isinstance(approved_raw, str)
        and approved_raw.strip().lower() in {"true", "yes", "approved", "approve"}
    )
    if not approved:
        return {
            "approved": False,
            "reason": _short_budget_text(
                parsed.get("reason") or "Budget auditor denied the extension.",
                limit=240,
            ),
        }

    def clamp_int(value: Any, *, default: int, upper: int) -> int:
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            coerced = default
        return max(0, min(coerced, upper))

    default_rounds = 2 if limit_kind == "rounds" else 1
    default_tool_calls = 16 if limit_kind == "rounds" else 24
    extra_rounds = clamp_int(
        parsed.get("extra_rounds"),
        default=default_rounds,
        upper=_SUPER_DAN_BUDGET_EXTENSION_MAX_ROUNDS_PER_LEASE,
    )
    extra_tool_calls = clamp_int(
        parsed.get("extra_tool_calls"),
        default=default_tool_calls,
        upper=_SUPER_DAN_BUDGET_EXTENSION_MAX_TOOL_CALLS_PER_LEASE,
    )
    if extra_rounds <= 0 and extra_tool_calls <= 0:
        return {
            "approved": False,
            "reason": "Budget auditor approved but granted no usable lease.",
        }
    return {
        "approved": True,
        "extra_rounds": extra_rounds,
        "extra_tool_calls": extra_tool_calls,
        "reason": _short_budget_text(
            parsed.get("reason") or "Small continuation lease approved.", limit=240
        ),
    }


def _soft_budget_message(
    phase: str,
    *,
    request: CompletionRequest,
    limit_kind: str,
    allow_final_read: bool,
) -> str:
    reason = f"soft_{limit_kind}_budget_exhausted"
    if phase == "coding_prewrite":
        return _write_capable_coding_stage_first_write_nudge_message(
            reason,
            request=request,
            allow_final_read=allow_final_read,
        )
    if phase == "coding_direct_write":
        return _write_capable_coding_stage_direct_write_required_message(
            reason,
            request=request,
        )
    if phase == "coding_postwrite":
        return _write_capable_coding_stage_finalize_message(reason)
    if phase == "validator_read_only":
        return _read_only_finalize_message(reason, mode="validator")
    if phase == "coding_worker_read_only":
        return _read_only_finalize_message(reason, mode="coding_worker")
    return _research_note_finalize_message(reason)


def _tool_call_allowed_past_soft_budget(
    tool_id: str,
    phase: str | None,
    *,
    allow_final_read: bool,
) -> bool:
    if phase == "coding_prewrite":
        return tool_id in {"file_edit", "file_write", "shell_command"}
    if phase == "coding_direct_write":
        if tool_id in {"file_edit", "file_write", "shell_command"}:
            return True
        return allow_final_read and tool_id == "file_read"
    return False


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
    return "prompt" in text and (
        "safety" in text or "rejected" in text or "blocked" in text
    )


def _provider_context_length_error(exc: BaseException) -> bool:
    """Detect provider-side context-window/token-limit rejections."""

    text_parts = [
        type(exc).__name__,
        str(exc),
        repr(getattr(exc, "body", "")),
        repr(getattr(exc, "response", "")),
        str(getattr(exc, "status_code", "") or ""),
    ]
    text = " ".join(part for part in text_parts if part).lower()
    if "content_filter" in text or "high risk" in text or "safety" in text:
        return False
    return any(
        marker in text
        for marker in (
            "context_length",
            "context length",
            "maximum context",
            "max context",
            "context window",
            "too many tokens",
            "token limit",
            "input is too long",
            "prompt is too long",
            "prompt too long",
            "maximum prompt",
            "reduce the length",
        )
    )


def _provider_overload_error(exc: BaseException) -> bool:
    """Detect transient provider overload/rate-limit errors worth retrying briefly."""

    text_parts = [
        type(exc).__name__,
        str(exc),
        repr(getattr(exc, "body", "")),
        repr(getattr(exc, "response", "")),
        str(getattr(exc, "status_code", "") or ""),
    ]
    text = " ".join(part for part in text_parts if part).lower()
    if any(
        permanent in text
        for permanent in (
            "insufficient balance",
            "exceeded_current_quota",
            "invalid authentication",
            "invalid api key",
        )
    ):
        return False
    return (
        "engine_overloaded" in text
        or "currently overloaded" in text
        or "server overloaded" in text
        or "rate limit" in text
        or "ratelimit" in text
        or "429" in text
    )


def _provider_overload_retry_delay(attempt: int) -> float:
    index = max(0, min(attempt - 1, len(_SUPER_DAN_PROVIDER_OVERLOAD_RETRY_DELAYS) - 1))
    return float(_SUPER_DAN_PROVIDER_OVERLOAD_RETRY_DELAYS[index])


def _provider_safety_retry_messages(request: CompletionRequest) -> list[dict[str, Any]]:
    expected_shape = str(
        getattr(request.output_contract, "expected_return_shape", "") or ""
    ).strip()
    schema_text = str(
        getattr(request.output_contract, "output_schema", "") or ""
    ).strip()
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
        messages.append(
            {"role": "user", "content": _completion_request_user_content(request)}
        )

    timeout_text = ""
    if timeout_seconds is not None:
        timeout_text = f" after {float(timeout_seconds):.2f}s"
    recovery_lines = [
        f"Recovery note: the previous provider call timed out{timeout_text}.",
        "Continue from the bounded evidence below instead of restarting broad discovery.",
    ]
    target_sentence = _recommended_write_paths_sentence(
        request,
        prefix="Recovery write targets",
    ).strip()
    if target_sentence:
        recovery_lines.append(target_sentence)
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
        evidence_sections.append("Grounded file paths: " + ", ".join(read_paths[-6:]))
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
    raw_shape = str(
        getattr(request.output_contract, "expected_return_shape", "") or ""
    ).strip()
    if not raw_shape:
        return set()
    try:
        parsed = json.loads(raw_shape)
    except Exception:
        return set()
    if not isinstance(parsed, dict):
        return set()
    return {str(key) for key in parsed}


def _provider_safety_fallback_payload(
    request: CompletionRequest,
) -> dict[str, Any] | None:
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
            "risks": [
                "No code candidate was produced because provider safety filtering blocked generation."
            ],
        }
    elif "worker_count" in keys:
        payload = {
            "public_response": "Provider safety filtering rejected the planning prompt before a substantive plan was generated.",
            "worker_count": 1,
            "worker_briefs": [
                "Return a bounded blocked result; provider safety filtering prevented normal planning."
            ],
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


def _provider_timeout_recovery_disabled(request: CompletionRequest) -> bool:
    if request.metadata.get("disable_timeout_recovery") is True:
        return True
    if request.metadata.get("short_completion_timeout") is True:
        return True
    raw_timeout = request.metadata.get("completion_timeout_seconds")
    try:
        timeout_seconds = float(raw_timeout)
    except (TypeError, ValueError):
        return False
    return 0 < timeout_seconds <= 60.0


def _request_completion_timeout_seconds(
    *,
    base_timeout_seconds: float | None,
    request: CompletionRequest,
) -> float | None:
    raw_timeout = request.metadata.get("completion_timeout_seconds")
    if raw_timeout in {None, ""}:
        return base_timeout_seconds
    try:
        timeout_seconds = float(raw_timeout)
    except (TypeError, ValueError):
        return base_timeout_seconds
    if timeout_seconds <= 0:
        return None
    timeout_seconds = max(0.01, timeout_seconds)
    if base_timeout_seconds is None:
        return timeout_seconds
    return min(float(base_timeout_seconds), timeout_seconds)


def _effective_completion_timeout_budget(
    *,
    base_timeout_seconds: float | None,
    request: CompletionRequest,
    executed_tools: Sequence[dict[str, Any]],
    workspace_root: Path,
    write_stage_direct_write_required: bool,
) -> tuple[float | None, str | None]:
    timeout_budget = _request_completion_timeout_seconds(
        base_timeout_seconds=base_timeout_seconds,
        request=request,
    )
    if timeout_budget is None:
        return None, None
    if _coding_output_kind(request) is None:
        return timeout_budget, None
    if not write_stage_direct_write_required:
        return timeout_budget, None
    if _successful_workspace_mutation_paths(
        executed_tools,
        workspace_root=workspace_root,
    ):
        return timeout_budget, None

    timeout_seconds = float(timeout_budget)
    if _exclusive_write_owner_path(request):
        effective_timeout = min(
            timeout_seconds,
            _EXCLUSIVE_OWNER_DIRECT_WRITE_SOFT_CAP_SECONDS,
        )
        if effective_timeout < timeout_seconds:
            return effective_timeout, "exclusive_owner_direct_write"
        return timeout_seconds, None

    effective_timeout = min(timeout_seconds, _DIRECT_WRITE_TIMEOUT_SOFT_CAP_SECONDS)
    if effective_timeout < timeout_seconds:
        return effective_timeout, "direct_write"
    return timeout_seconds, None


def _timeout_recovery_stage_key(
    *,
    write_stage_direct_write_required: bool,
    write_stage_final_read_consumed: bool,
) -> str:
    if write_stage_final_read_consumed:
        return "direct_write_after_final_read"
    if write_stage_direct_write_required:
        return "direct_write"
    return "general"


def _provider_timeout_fallback_payload(
    request: CompletionRequest,
) -> dict[str, Any] | None:
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
        tool_id: dict(metadata) for tool_id, (_fn, metadata) in get_all_tools().items()
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
        from dan.native_workers.service import current_team
        team = current_team.get()
        if team and "shell_command" in selected and any(profile.get("enabled") for profile in team.profiles.values()):
            selected = list(dict.fromkeys([*selected, "native_worker"]))
        missing = [tool_id for tool_id in selected if tool_id not in available]
        if missing:
            raise ValueError(
                "Unknown local organism tools: " + ", ".join(sorted(missing))
            )
        self._tools = {tool_id: available[tool_id] for tool_id in selected}
        self._approval_callback = approval_callback
        self._event_callback = event_callback
        self._protected_workspace_files: dict[str, dict[str, Any]] = {}
        self._protected_workspace_bytes = 0

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

    def _normalize_shell_working_directory(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        kwargs = dict(arguments or {})
        raw_path = str(kwargs.get("working_directory") or "").strip()
        if not raw_path:
            kwargs["working_directory"] = str(self._workspace_root)
            return kwargs
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self._workspace_root / candidate
        kwargs["working_directory"] = str(candidate.resolve())
        return kwargs

    def _refresh_protected_workspace_path(self, raw_path: Any) -> None:
        candidate = _tool_path_for_cache(
            raw_path,
            workspace_root=self._workspace_root,
        )
        if candidate is None:
            return
        state = _protected_workspace_file_state(
            candidate,
            workspace_root=self._workspace_root,
        )
        key = str(candidate)
        previous = self._protected_workspace_files.pop(key, None)
        if previous is not None and isinstance(previous.get("content"), bytes):
            self._protected_workspace_bytes -= len(previous["content"])
        if state is None:
            return
        content = state.get("content")
        content_size = len(content) if isinstance(content, bytes) else 0
        while (
            self._protected_workspace_files
            and self._protected_workspace_bytes + content_size
            > _SHELL_PROTECTED_TOTAL_MAX_BYTES
        ):
            oldest_key = next(iter(self._protected_workspace_files))
            oldest = self._protected_workspace_files.pop(oldest_key)
            oldest_content = oldest.get("content")
            if isinstance(oldest_content, bytes):
                self._protected_workspace_bytes -= len(oldest_content)
        self._protected_workspace_files[key] = state
        self._protected_workspace_bytes += content_size

    def _refresh_protected_path_for_tool(
        self,
        tool_id: str,
        arguments: Mapping[str, Any],
    ) -> None:
        if tool_id not in {"file_read", "file_edit", "file_write"}:
            return
        self._refresh_protected_workspace_path(
            arguments.get("path") or arguments.get("file_path")
        )

    def _prepare_shell_file_transaction(
        self,
        arguments: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        for raw_path in list(self._protected_workspace_files):
            self._refresh_protected_workspace_path(raw_path)

        command = str(arguments.get("command") or "")
        working_directory = Path(
            str(arguments.get("working_directory") or self._workspace_root)
        ).resolve()
        for path in _existing_workspace_files_named_by_shell_command(
            command,
            working_directory=working_directory,
            workspace_root=self._workspace_root,
        ):
            self._refresh_protected_workspace_path(path)
        return {
            path: dict(state) for path, state in self._protected_workspace_files.items()
        }

    def _settle_shell_file_transaction(
        self,
        *,
        arguments: Mapping[str, Any],
        before: Mapping[str, Mapping[str, Any]],
        result: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        changed = [
            dict(state) for state in before.values() if _protected_state_changed(state)
        ]
        try:
            exit_code = int(result.get("exit_code", 0))
        except (TypeError, ValueError):
            exit_code = 1
        command = str(arguments.get("command") or "")
        working_directory = Path(
            str(arguments.get("working_directory") or self._workspace_root)
        ).resolve()
        move_source_paths = _shell_move_source_paths(
            command,
            working_directory=working_directory,
            workspace_root=self._workspace_root,
        )
        catastrophic = [
            state
            for state in changed
            if _protected_state_has_catastrophic_loss(state)
            and not (
                isinstance(state.get("path"), Path)
                and state["path"] in move_source_paths
                and not state["path"].exists()
            )
        ]
        rollback_reason = ""
        if exit_code != 0 and changed:
            rollback_reason = f"shell command exited with code {exit_code}"
        elif catastrophic:
            rendered = ", ".join(
                str(state["path"].relative_to(self._workspace_root))
                for state in catastrophic[:3]
                if isinstance(state.get("path"), Path)
            )
            rollback_reason = (
                "shell command catastrophically emptied, deleted, or shrank a "
                f"protected workspace file ({rendered})"
            )

        transaction: dict[str, Any] | None = None
        settled_result = dict(result)
        if rollback_reason:
            restored, failed = _restore_protected_file_states(changed)
            transaction = {
                "status": "rollback_failed" if failed else "rolled_back",
                "reason": rollback_reason,
                "restored_paths": restored,
                "restore_errors": failed,
            }
            if exit_code == 0:
                settled_result["exit_code"] = 126 if failed else 125
            message = f"Super DAN workspace transaction rolled back: {rollback_reason}."
            if failed:
                message += " Some protected paths could not be restored."
            stderr = str(settled_result.get("stderr") or "")
            settled_result["stderr"] = f"{stderr.rstrip()}\n{message}".lstrip()
            settled_result["transaction"] = transaction
        elif changed:
            transaction = {
                "status": "committed",
                "protected_paths_changed": [
                    str(state["path"])
                    for state in changed
                    if isinstance(state.get("path"), Path)
                ],
            }
            settled_result["transaction"] = transaction

        for raw_path in before:
            self._refresh_protected_workspace_path(raw_path)
        return settled_result, transaction

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
            if (
                end_line is None
                and start_line is not None
                and kwargs.get("limit") is not None
            ):
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
        if tool_id == "shell_command":
            kwargs = self._normalize_shell_working_directory(kwargs)
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
        tool_specific_error = _tool_specific_argument_validation_error(tool_id, kwargs)
        if tool_specific_error:
            self._emit_event(
                "tool.failed",
                tool_id=tool_id,
                arguments=dict(kwargs),
                metadata=dict(metadata),
                error=tool_specific_error,
                **shared_context,
            )
            raise ValueError(tool_specific_error)
        if tool_id == "shell_command":
            command = str(kwargs.get("command") or "")
            working_directory = Path(
                str(kwargs.get("working_directory") or self._workspace_root)
            ).resolve()
            direct_mutation_error = _unsafe_direct_shell_mutation_error(
                command,
                working_directory=working_directory,
                workspace_root=self._workspace_root,
            )
            if direct_mutation_error:
                error_text = f"tool_arguments_invalid: {direct_mutation_error}"
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
        shell_transaction_before = (
            self._prepare_shell_file_transaction(kwargs)
            if tool_id == "shell_command"
            else {}
        )
        workspace_before = (
            _workspace_file_snapshot(self._workspace_root)
            if tool_id == "shell_command"
            else None
        )
        try:
            result = await function(**kwargs)
        except Exception as exc:
            if shell_transaction_before:
                changed = [
                    dict(state)
                    for state in shell_transaction_before.values()
                    if _protected_state_changed(state)
                ]
                restored, failed = _restore_protected_file_states(changed)
                if changed:
                    self._emit_event(
                        "tool.rolled_back",
                        tool_id=tool_id,
                        arguments=dict(kwargs),
                        reason=f"{type(exc).__name__}: {exc}",
                        restored_paths=restored,
                        restore_errors=failed,
                        **shared_context,
                    )
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
        if tool_id == "shell_command" and isinstance(result, dict):
            result, transaction = self._settle_shell_file_transaction(
                arguments=kwargs,
                before=shell_transaction_before,
                result=result,
            )
            if transaction and transaction.get("status") in {
                "rolled_back",
                "rollback_failed",
            }:
                self._emit_event(
                    "tool.rolled_back",
                    tool_id=tool_id,
                    arguments=dict(kwargs),
                    **dict(transaction),
                    **shared_context,
                )
            workspace_after = _workspace_file_snapshot(self._workspace_root)
            result = dict(result)
            workspace_changes = _workspace_snapshot_diff(
                workspace_before,
                workspace_after,
            )
            if transaction and transaction.get("status") == "rolled_back":
                workspace_changes = _workspace_diff_without_restored_paths(
                    workspace_changes,
                    restored_paths=list(transaction.get("restored_paths") or []),
                    workspace_root=self._workspace_root,
                )
            result["workspace_changes"] = workspace_changes
        self._refresh_protected_path_for_tool(tool_id, kwargs)
        self._emit_event(
            "tool.completed",
            tool_id=tool_id,
            arguments=dict(kwargs),
            metadata=dict(metadata),
            result=result,
            **shared_context,
        )
        return result


def _checkpoint_operator_update_content(item: Mapping[str, Any]) -> str:
    text = " ".join(str(item.get("text") or "").split())
    if not text:
        return ""
    checkpoint = " ".join(str(item.get("checkpoint") or "").split())
    operator_context = (
        dict(item.get("operator_context") or {})
        if isinstance(item.get("operator_context"), Mapping)
        else {}
    )
    bounded_context = {
        key: operator_context.get(key)
        for key in (
            "target_paths",
            "hard_constraints",
            "soft_preferences",
            "validation_requirements",
            "attachments",
        )
        if operator_context.get(key)
    }
    context_text = ""
    if bounded_context:
        context_text = json.dumps(
            bounded_context,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if len(context_text) > 4000:
            context_text = context_text[:3997].rstrip() + "..."
    checkpoint_note = f" at `{checkpoint}`" if checkpoint else ""
    rendered = (
        f"Operator update admitted at a safe execution checkpoint{checkpoint_note}:\n"
        f"{text}\n\n"
        "Apply this update to remaining work and final validation. Preserve already-completed "
        "work unless the update explicitly asks to revise it. This message does not widen the "
        "runtime's file, tool, approval, or external-action authority."
    )
    if context_text:
        rendered += f"\n\nStructured update context:\n{context_text}"
    return rendered


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
        operator_message_provider: (
            Callable[[], Sequence[Mapping[str, Any]]] | None
        ) = None,
        operator_message_acknowledger: (
            Callable[[Sequence[str], str], None] | None
        ) = None,
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
        self._operator_message_provider = operator_message_provider
        self._operator_message_acknowledger = operator_message_acknowledger
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
                    getattr(chunk, "accumulated", accumulated + delta)
                    or accumulated + delta
                )
                usage_candidate = getattr(chunk, "usage", None)
                if usage_candidate:
                    usage = dict(usage_candidate)
                chunk_model = str(getattr(chunk, "model", "") or "").strip()
                if chunk_model:
                    streamed_model = chunk_model
                chunk_finish_reason = str(
                    getattr(chunk, "finish_reason", "") or ""
                ).strip()
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
                    "content": (
                        accumulated
                        if accumulated.strip()
                        else (None if tool_calls else accumulated)
                    ),
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
        timeout_seconds: float | None = None,
    ) -> CompletionResult:
        effective_timeout_seconds = (
            self._completion_timeout_seconds
            if timeout_seconds is None
            else max(0.01, float(timeout_seconds))
        )
        if effective_timeout_seconds is None:
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
            timeout=effective_timeout_seconds,
        )
        if completion_task in done:
            return await completion_task
        completion_task.cancel()
        completion_task.add_done_callback(_drain_detached_asyncio_task)
        raise TimeoutError(
            f"provider_completion_timeout:{effective_timeout_seconds:.2f}s"
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        model = str(request.model or self._default_model or "").strip()
        if not model:
            raise ValueError("Tool-loop completion provider requires a concrete model")
        worker_id = str(request.metadata.get("worker_id") or "").strip() or None
        event_context = self._event_context(request, worker_id=worker_id)
        soft_budget_profile = _tool_budget_profile(request)
        exclusive_write_owner_path = _exclusive_write_owner_path(request)
        exclusive_write_owner = bool(exclusive_write_owner_path)

        requested_tool_schemas = self._resolve_tool_schemas(request.tools)
        operator_intent_tool_schemas = _operator_intent_tool_schemas(
            request,
            requested_tool_schemas,
        )
        if len(operator_intent_tool_schemas) != len(requested_tool_schemas):
            enabled_tool_names = {
                _tool_schema_name(tool)
                for tool in operator_intent_tool_schemas
                if _tool_schema_name(tool)
            }
            self._emit_event(
                "toolloop.operator_intent_tools_narrowed",
                enabled_tools=[
                    _tool_schema_name(tool)
                    for tool in operator_intent_tool_schemas
                    if _tool_schema_name(tool)
                ],
                dropped_tools=[
                    _tool_schema_name(tool)
                    for tool in requested_tool_schemas
                    if _tool_schema_name(tool)
                    and _tool_schema_name(tool) not in enabled_tool_names
                ],
                **event_context,
            )
        requested_tool_schemas = operator_intent_tool_schemas
        operator_read_only = _request_forbids_workspace_mutation(request)
        if operator_read_only:
            allowed_read_only_tools = set(
                _read_only_tool_ids(
                    [
                        _tool_schema_name(tool)
                        for tool in requested_tool_schemas
                        if isinstance(tool, dict) and _tool_schema_name(tool)
                    ]
                )
            )
            allowed_read_only_tools.update(
                _structured_surface_ui_tool_ids(
                    request,
                    [
                        _tool_schema_name(tool)
                        for tool in requested_tool_schemas
                        if isinstance(tool, dict) and _tool_schema_name(tool)
                    ],
                )
            )
            tool_schemas = [
                dict(tool)
                for tool in requested_tool_schemas
                if isinstance(tool, dict)
                and _tool_schema_name(tool) in allowed_read_only_tools
            ]
        else:
            tool_schemas = _validator_read_only_tool_schemas(
                request,
                requested_tool_schemas,
            )
        exclusive_owner_prefers_file_edit = _exclusive_write_owner_prefers_file_edit(
            request,
            tool_schemas,
            workspace_root=self._tool_runtime.workspace_root,
        )
        if len(tool_schemas) != len(requested_tool_schemas):
            self._emit_event(
                (
                    "toolloop.operator_read_only_tools_narrowed"
                    if operator_read_only
                    else "toolloop.validator_tools_narrowed"
                ),
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
        if operator_read_only:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Operator policy: this is a read-only/no-mutation turn. "
                        "Do not claim any file was changed, created, edited, or deleted. "
                        "Use only read-only evidence if needed, then return the requested answer."
                    ),
                }
            )
        messages.append(
            {"role": "user", "content": _completion_request_user_content(request)}
        )

        executed_tools: list[dict[str, Any]] = []
        file_read_result_cache: dict[
            tuple[Any, ...], tuple[tuple[str, int, int, int, int], Any]
        ] = {}
        rounds = 0
        total_tool_calls = 0
        usage_totals: dict[str, int] = {}
        stop_reason = "completed"
        last_result: Any = None
        forced_finalize_without_tools = False
        write_stage_first_write_nudged = False
        write_stage_final_read_available = False
        write_stage_final_read_consumed = False
        write_stage_direct_write_required = False
        write_stage_direct_write_reprompted = False
        research_note_finalize_nudged = False
        provider_safety_retry_attempted = False
        provider_context_length_retry_attempted = False
        prompt_context_emergency_compaction = False
        provider_timeout_recovery_attempted: set[str] = set()
        provider_overload_retry_attempts = 0
        blocked_by_tool_call_ids: list[str] = []
        blocked_tool_counts: dict[str, int] = {}
        disabled_tool_ids: set[str] = set()
        file_write_incremental_edit_paths: dict[str, str] = {}
        soft_budget_tracked_phase: str | None = None
        soft_budget_phase_start_rounds = 0
        soft_budget_phase_start_tool_calls = 0
        dynamic_budget_enabled = _dynamic_budget_extension_enabled(
            request,
            profile=soft_budget_profile,
        )
        dynamic_budget_max_leases = _dynamic_budget_max_leases(
            request,
            profile=soft_budget_profile,
        )
        dynamic_budget_lease_count = 0
        budget_extensions: list[dict[str, Any]] = []
        pending_operator_messages: dict[str, dict[str, Any]] = {}

        def _soft_budget_phase_consumed(
            phase: str | None,
            *,
            limit_kind: str,
        ) -> int:
            nonlocal soft_budget_tracked_phase
            nonlocal soft_budget_phase_start_rounds
            nonlocal soft_budget_phase_start_tool_calls
            if phase != soft_budget_tracked_phase:
                soft_budget_tracked_phase = phase
                soft_budget_phase_start_rounds = rounds
                soft_budget_phase_start_tool_calls = total_tool_calls
            if phase is None:
                return 0
            if limit_kind == "rounds":
                return max(0, rounds - soft_budget_phase_start_rounds)
            return max(0, total_tool_calls - soft_budget_phase_start_tool_calls)

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
                "budget_extensions": list(budget_extensions),
            }

        async def _maybe_extend_hard_budget(
            *,
            limit_kind: str,
            current_limit: int | None,
            last_text: str = "",
        ) -> bool:
            nonlocal dynamic_budget_lease_count
            if not dynamic_budget_enabled or dynamic_budget_max_leases <= 0:
                return False
            if current_limit is None:
                return False
            if dynamic_budget_lease_count >= dynamic_budget_max_leases:
                return False
            audit_payload = _budget_audit_prompt_payload(
                request=request,
                limit_kind=limit_kind,
                current_limit=current_limit,
                rounds=rounds,
                total_tool_calls=total_tool_calls,
                executed_tools=executed_tools,
                workspace_root=self._tool_runtime.workspace_root,
                last_text=last_text,
            )
            audit_model_call_id = self._next_model_call_id()
            self._emit_event(
                "toolloop.budget_review.started",
                model=model,
                model_call_id=audit_model_call_id,
                limit_kind=limit_kind,
                current_limit=current_limit,
                lease_index=dynamic_budget_lease_count + 1,
                max_leases=dynamic_budget_max_leases,
                progress=dict(audit_payload.get("progress") or {}),
                tool_calls_executed=len(executed_tools),
                **event_context,
            )
            try:
                audit_result = await self._provider.complete(
                    messages=_budget_audit_messages(audit_payload),
                    model=model,
                    temperature=0.0,
                    max_tokens=400,
                )
            except Exception as exc:
                self._emit_event(
                    "toolloop.budget_review.failed",
                    model=model,
                    model_call_id=audit_model_call_id,
                    limit_kind=limit_kind,
                    error_type=type(exc).__name__,
                    error=str(exc),
                    tool_calls_executed=len(executed_tools),
                    **event_context,
                )
                return False
            decision = _parse_budget_audit_decision(
                getattr(audit_result, "text", "") or "",
                limit_kind=limit_kind,
            )
            self._emit_event(
                "toolloop.budget_review.completed",
                model=getattr(audit_result, "model", None) or model,
                model_call_id=audit_model_call_id,
                limit_kind=limit_kind,
                approved=bool(decision.get("approved")),
                reason=decision.get("reason"),
                usage=_normalize_usage_totals(getattr(audit_result, "usage", None)),
                tool_calls_executed=len(executed_tools),
                **event_context,
            )
            if not bool(decision.get("approved")):
                return False

            extra_rounds = int(decision.get("extra_rounds") or 0)
            extra_tool_calls = int(decision.get("extra_tool_calls") or 0)
            if limit_kind == "rounds" and extra_rounds <= 0:
                return False
            if limit_kind == "tool_calls" and extra_tool_calls <= 0:
                return False

            previous_round_limit = self._max_rounds
            previous_tool_call_limit = self._max_tool_calls
            if self._max_rounds is not None:
                self._max_rounds += extra_rounds
            self._max_tool_calls += extra_tool_calls
            dynamic_budget_lease_count += 1
            lease = {
                "lease_index": dynamic_budget_lease_count,
                "limit_kind": limit_kind,
                "extra_rounds": extra_rounds,
                "extra_tool_calls": extra_tool_calls,
                "previous_round_limit": previous_round_limit,
                "new_round_limit": self._max_rounds,
                "previous_tool_call_limit": previous_tool_call_limit,
                "new_tool_call_limit": self._max_tool_calls,
                "reason": decision.get("reason") or "",
            }
            budget_extensions.append(lease)
            self._emit_event(
                "toolloop.budget_extension.approved",
                model=getattr(audit_result, "model", None) or model,
                model_call_id=audit_model_call_id,
                **lease,
                tool_calls_executed=len(executed_tools),
                **event_context,
            )
            return True

        while True:
            if self._operator_message_provider is not None:
                try:
                    admitted_operator_messages = [
                        dict(item)
                        for item in self._operator_message_provider()
                        if isinstance(item, Mapping)
                    ]
                except Exception as exc:
                    admitted_operator_messages = []
                    self._emit_event(
                        "toolloop.operator_messages.failed",
                        error_type=type(exc).__name__,
                        error=str(exc),
                        **event_context,
                    )
                if admitted_operator_messages:
                    applied_ids: list[str] = []
                    checkpoints: list[str] = []
                    for item in admitted_operator_messages:
                        content = _checkpoint_operator_update_content(item)
                        if not content:
                            continue
                        queue_item_id = str(item.get("queue_item_id") or "").strip()
                        message_key = queue_item_id or (
                            "local:"
                            + hashlib.sha256(
                                json.dumps(
                                    item,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    default=str,
                                ).encode("utf-8")
                            ).hexdigest()[:16]
                        )
                        is_new = message_key not in pending_operator_messages
                        pending_operator_messages[message_key] = {
                            **item,
                            "_rendered_content": content,
                        }
                        checkpoint = str(item.get("checkpoint") or "").strip()
                        if is_new and queue_item_id:
                            applied_ids.append(queue_item_id)
                        if is_new and checkpoint:
                            checkpoints.append(checkpoint)
                    if applied_ids or checkpoints:
                        self._emit_event(
                            "toolloop.operator_messages.applied",
                            queue_item_ids=applied_ids,
                            checkpoints=list(dict.fromkeys(checkpoints)),
                            message_count=len(admitted_operator_messages),
                            tool_calls_executed=len(executed_tools),
                            **event_context,
                        )
            request_tool_schemas = _enabled_tool_schemas(
                active_tool_schemas,
                disabled_tool_ids=sorted(disabled_tool_ids),
            )
            request_tool_ids = [
                _tool_schema_name(tool)
                for tool in request_tool_schemas
                if _tool_schema_name(tool)
            ]
            if (
                soft_budget_profile == _SOFT_BUDGET_PROFILE_SUPER_DAN
                and request_tool_ids
                and not forced_finalize_without_tools
            ):
                soft_progress = _soft_budget_progress_snapshot(
                    executed_tools=executed_tools,
                    workspace_root=self._tool_runtime.workspace_root,
                )
                soft_phase = _soft_budget_phase(
                    request=request,
                    tool_ids=request_tool_ids,
                    executed_tools=executed_tools,
                    workspace_root=self._tool_runtime.workspace_root,
                    write_stage_first_write_nudged=write_stage_first_write_nudged,
                    write_stage_direct_write_required=write_stage_direct_write_required,
                )
                allow_final_read = not _interactive_source_implementation_request(
                    request
                ) and any(
                    _tool_schema_name(tool) == "file_read"
                    for tool in request_tool_schemas
                    if isinstance(tool, dict)
                )
                for limit_kind, hard_limit in (
                    ("rounds", self._max_rounds),
                    ("tool_calls", self._max_tool_calls),
                ):
                    consumed = _soft_budget_phase_consumed(
                        soft_phase,
                        limit_kind=limit_kind,
                    )
                    soft_limit = _soft_budget_limit(
                        soft_phase,
                        limit_kind=limit_kind,
                        hard_limit=hard_limit,
                        progress=soft_progress,
                    )
                    if (
                        soft_phase is None
                        or soft_limit is None
                        or consumed < soft_limit
                    ):
                        continue
                    action = _soft_budget_action_for_phase(soft_phase)
                    if action is None:
                        continue
                    if action == "narrow_write_stage":
                        write_stage_first_write_nudged = True
                        write_stage_final_read_available = allow_final_read
                        write_stage_tool_schemas = _write_stage_tool_schemas(
                            request_tool_schemas,
                            allow_final_read=write_stage_final_read_available,
                            prefer_file_write_only=exclusive_write_owner,
                            prefer_file_edit_only=exclusive_owner_prefers_file_edit,
                        )
                        if write_stage_tool_schemas:
                            active_tool_schemas = write_stage_tool_schemas
                            disabled_tool_ids.clear()
                        write_stage_direct_write_required = (
                            not write_stage_final_read_available
                        )
                    elif action == "require_direct_write":
                        write_stage_tool_schemas = _write_stage_tool_schemas(
                            request_tool_schemas,
                            allow_final_read=write_stage_final_read_available,
                            prefer_file_write_only=exclusive_write_owner,
                            prefer_file_edit_only=exclusive_owner_prefers_file_edit,
                        )
                        if write_stage_tool_schemas:
                            active_tool_schemas = write_stage_tool_schemas
                            disabled_tool_ids.clear()
                        write_stage_direct_write_required = True
                    else:
                        forced_finalize_without_tools = True
                        active_tool_schemas = []
                    messages.append(
                        {
                            "role": "user",
                            "content": _soft_budget_message(
                                soft_phase,
                                request=request,
                                limit_kind=limit_kind,
                                allow_final_read=allow_final_read,
                            ),
                        }
                    )
                    self._emit_event(
                        "toolloop.soft_budget_nudged",
                        phase=soft_phase,
                        action=action,
                        limit_kind=limit_kind,
                        soft_limit=soft_limit,
                        hard_limit=hard_limit,
                        progress=soft_progress,
                        enabled_tools=[
                            str(tool.get("function", {}).get("name") or "").strip()
                            for tool in active_tool_schemas
                            if isinstance(tool, dict)
                        ],
                        blocked_by_tool_call_ids=list(blocked_by_tool_call_ids) or None,
                        tool_calls_executed=len(executed_tools),
                        **event_context,
                    )
                    blocked_by_tool_call_ids = []
                    request_tool_schemas = _enabled_tool_schemas(
                        active_tool_schemas,
                        disabled_tool_ids=sorted(disabled_tool_ids),
                    )
                    request_tool_ids = [
                        _tool_schema_name(tool)
                        for tool in request_tool_schemas
                        if _tool_schema_name(tool)
                    ]
                    break
            model_call_id = self._next_model_call_id()
            effective_timeout_seconds, timeout_strategy = (
                _effective_completion_timeout_budget(
                    base_timeout_seconds=self._completion_timeout_seconds,
                    request=request,
                    executed_tools=executed_tools,
                    workspace_root=self._tool_runtime.workspace_root,
                    write_stage_direct_write_required=write_stage_direct_write_required,
                )
            )
            prompt_context_budget_chars, prompt_context_emergency_budget_chars = (
                _prompt_context_budget_chars(request, profile=soft_budget_profile)
            )
            tool_schema_chars = _tool_schema_prompt_char_count(request_tool_schemas)
            operator_request_messages = [
                {
                    "role": "user",
                    "content": str(item.get("_rendered_content") or ""),
                }
                for item in pending_operator_messages.values()
                if not bool(item.get("embedded_in_initial_request"))
                and str(item.get("_rendered_content") or "").strip()
            ]
            messages_for_provider = [
                *messages,
                *operator_request_messages,
            ]
            provider_messages, prompt_context_stats = (
                _compact_messages_for_provider_prompt(
                    messages_for_provider,
                    budget_chars=prompt_context_budget_chars,
                    emergency_budget_chars=prompt_context_emergency_budget_chars,
                    tool_schema_chars=tool_schema_chars,
                    emergency=prompt_context_emergency_compaction,
                )
            )
            if (
                not prompt_context_emergency_compaction
                and prompt_context_emergency_budget_chars is not None
                and int(prompt_context_stats.get("prompt_context_final_chars") or 0)
                > prompt_context_emergency_budget_chars
            ):
                prompt_context_emergency_compaction = True
                provider_messages, prompt_context_stats = (
                    _compact_messages_for_provider_prompt(
                        messages_for_provider,
                        budget_chars=prompt_context_budget_chars,
                        emergency_budget_chars=prompt_context_emergency_budget_chars,
                        tool_schema_chars=tool_schema_chars,
                        emergency=True,
                    )
                )
            provider_request_messages = apply_cache_hints(
                self._provider, provider_messages
            )
            self._emit_event(
                "model.requested",
                model=model,
                round=rounds + 1,
                tool_count=len(request_tool_schemas),
                tool_ids=list(request_tool_ids),
                model_call_id=model_call_id,
                timeout_seconds=effective_timeout_seconds,
                timeout_strategy=timeout_strategy,
                blocked_by_tool_call_ids=list(blocked_by_tool_call_ids) or None,
                prompt_messages=_debug_prompt_messages(provider_request_messages),
                **prompt_context_stats,
                **event_context,
            )
            blocked_by_tool_call_ids = []
            provider_kwargs = {
                "tools": request_tool_schemas or None,
                **self._provider_request_overrides,
            }
            try:
                last_result = await self._complete_text_response_with_timeout(
                    messages=provider_request_messages,
                    model=model,
                    request=request,
                    provider_kwargs=provider_kwargs,
                    round_number=rounds + 1,
                    worker_id=worker_id,
                    model_call_id=model_call_id,
                    event_context=event_context,
                    timeout_seconds=effective_timeout_seconds,
                )
            except Exception as exc:
                if (
                    _provider_context_length_error(exc)
                    and not provider_context_length_retry_attempted
                ):
                    provider_context_length_retry_attempted = True
                    prompt_context_emergency_compaction = True
                    self._emit_event(
                        "model.context_length_retry",
                        model=model,
                        round=rounds + 1,
                        model_call_id=model_call_id,
                        retry_attempt=1,
                        error_type=type(exc).__name__,
                        error=str(exc),
                        **prompt_context_stats,
                        **event_context,
                    )
                    continue
                if (
                    soft_budget_profile == _SOFT_BUDGET_PROFILE_SUPER_DAN
                    and _provider_overload_error(exc)
                    and provider_overload_retry_attempts
                    < _SUPER_DAN_PROVIDER_OVERLOAD_MAX_RETRIES
                ):
                    provider_overload_retry_attempts += 1
                    retry_delay = _provider_overload_retry_delay(
                        provider_overload_retry_attempts
                    )
                    self._emit_event(
                        "model.provider_overload_retry",
                        model=model,
                        round=rounds + 1,
                        model_call_id=model_call_id,
                        retry_attempt=provider_overload_retry_attempts,
                        max_retries=_SUPER_DAN_PROVIDER_OVERLOAD_MAX_RETRIES,
                        retry_delay_seconds=retry_delay,
                        error_type=type(exc).__name__,
                        error=str(exc),
                        **event_context,
                    )
                    if retry_delay > 0:
                        await asyncio.sleep(retry_delay)
                    continue
                if _provider_timeout_error(exc):
                    timeout_seconds = effective_timeout_seconds
                    successful_tool_count = sum(
                        1 for tool in executed_tools if tool.get("ok")
                    )
                    successful_tool_threshold = (
                        0
                        if _is_builder_retry_request(request)
                        or _recommended_write_paths(request)
                        else 1 if exclusive_write_owner else 2
                    )
                    timeout_recovery_stage = _timeout_recovery_stage_key(
                        write_stage_direct_write_required=write_stage_direct_write_required,
                        write_stage_final_read_consumed=write_stage_final_read_consumed,
                    )
                    self._emit_event(
                        "model.timeout",
                        model=model,
                        round=rounds + 1,
                        model_call_id=model_call_id,
                        timeout_seconds=timeout_seconds,
                        timeout_strategy=timeout_strategy,
                        tool_count=len(request_tool_schemas),
                        error_type=type(exc).__name__,
                        **event_context,
                    )
                    can_retry_timeout = (
                        timeout_recovery_stage
                        not in provider_timeout_recovery_attempted
                        and not _provider_timeout_recovery_disabled(request)
                        and _coding_output_kind(request) is not None
                        and any(
                            tool_id in {"file_edit", "file_write"}
                            for tool_id in request_tool_ids
                        )
                        and not _successful_workspace_mutation_paths(
                            executed_tools,
                            workspace_root=self._tool_runtime.workspace_root,
                        )
                        and successful_tool_count >= successful_tool_threshold
                    )
                    if can_retry_timeout:
                        provider_timeout_recovery_attempted.add(timeout_recovery_stage)
                        allow_timeout_recovery_final_read = (
                            not write_stage_direct_write_required
                            and not write_stage_final_read_consumed
                            and (
                                not write_stage_first_write_nudged
                                or write_stage_final_read_available
                            )
                        )
                        recovered_tool_schemas = _write_stage_tool_schemas(
                            tool_schemas,
                            allow_final_read=allow_timeout_recovery_final_read,
                            prefer_file_write_only=exclusive_write_owner,
                            prefer_file_edit_only=exclusive_owner_prefers_file_edit,
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
                            timeout_strategy=timeout_strategy,
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
                        json.dumps(
                            partial_candidate, ensure_ascii=False, sort_keys=True
                        )
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
            delivered_operator_messages = list(pending_operator_messages.values())
            delivered_operator_ids = [
                str(item.get("queue_item_id") or "").strip()
                for item in delivered_operator_messages
                if str(item.get("queue_item_id") or "").strip()
            ]
            if (
                delivered_operator_ids
                and self._operator_message_acknowledger is not None
            ):
                try:
                    self._operator_message_acknowledger(
                        delivered_operator_ids,
                        model_call_id,
                    )
                except Exception as exc:
                    self._emit_event(
                        "toolloop.operator_messages.ack_failed",
                        queue_item_ids=delivered_operator_ids,
                        model_call_id=model_call_id,
                        error_type=type(exc).__name__,
                        error=str(exc),
                        **event_context,
                    )
                    raise
            for item in delivered_operator_messages:
                if bool(item.get("embedded_in_initial_request")):
                    continue
                content = str(item.get("_rendered_content") or "").strip()
                if content:
                    messages.append({"role": "user", "content": content})
            if delivered_operator_messages:
                self._emit_event(
                    "toolloop.operator_messages.delivered",
                    queue_item_ids=delivered_operator_ids,
                    model_call_id=model_call_id,
                    message_count=len(delivered_operator_messages),
                    tool_calls_executed=len(executed_tools),
                    **event_context,
                )
                pending_operator_messages.clear()
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
                tool_calls=[
                    call.get("function", {}).get("name") or call.get("name")
                    for call in tool_calls
                    if isinstance(call, dict)
                ],
                finish_reason=getattr(last_result, "finish_reason", None),
                usage=_normalize_usage_totals(getattr(last_result, "usage", None)),
                usage_totals=dict(usage_totals),
                text=(last_result.text or "")[:400],
                response_text=last_result.text or "",
                assistant_message=_debug_prompt_messages([assistant_message])[0],
                streamed=bool(
                    (getattr(last_result, "provider_metadata", None) or {}).get(
                        "streamed_response"
                    )
                ),
                **event_context,
            )
            if (
                forced_finalize_without_tools
                and not request_tool_schemas
                and tool_calls
            ):
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
                            tool_schemas,
                            prefer_file_write_only=exclusive_write_owner,
                            prefer_file_edit_only=exclusive_owner_prefers_file_edit,
                        )
                        if direct_write_tool_schemas:
                            active_tool_schemas = direct_write_tool_schemas
                            disabled_tool_ids.clear()
                        messages.append(
                            {
                                "role": "user",
                                "content": _write_capable_coding_stage_direct_write_required_message(
                                    direct_write_required_reason,
                                    request=request,
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
                            blocked_by_tool_call_ids=list(blocked_by_tool_call_ids)
                            or None,
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
                if await _maybe_extend_hard_budget(
                    limit_kind="rounds",
                    current_limit=self._max_rounds,
                    last_text=last_result.text or "",
                ):
                    self._emit_event(
                        "toolloop.budget_extension.continued",
                        limit_kind="rounds",
                        round=rounds,
                        round_limit=self._max_rounds,
                        tool_call_limit=self._max_tool_calls,
                        tool_calls_executed=len(executed_tools),
                        **event_context,
                    )
                else:
                    stop_reason = f"max_tool_rounds_exceeded:{self._max_rounds}"
                    partial_candidate = _partial_coding_candidate_from_tool_evidence(
                        request=request,
                        executed_tools=executed_tools,
                        stop_reason=stop_reason,
                        existing_text=last_result.text or "",
                        workspace_root=self._tool_runtime.workspace_root,
                    )
                    fallback_text = (
                        json.dumps(
                            partial_candidate, ensure_ascii=False, sort_keys=True
                        )
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
            source_structure_nudges: list[tuple[str, str]] = []
            repeated_tool_call_nudges: list[tuple[str, str]] = []
            write_stage_helper_path_nudges: list[tuple[str, str, str]] = []
            file_write_downshift_nudges: list[tuple[str, str]] = []
            repair_policy_nudges: list[tuple[str, int, int]] = []
            exclusive_owner_read_scope_nudges: list[str] = []
            phase_budget_nudges: list[tuple[str, str, int, dict[str, int]]] = []
            newly_disabled_tool_ids: list[str] = []
            write_stage_final_read_consumed = False

            def _record_tool_result(
                *,
                tool_id: str,
                tool_call_id: str,
                arguments: dict[str, Any],
                tool_payload: dict[str, Any],
            ) -> dict[str, Any]:
                prompt_text_limit = _TOOL_PROMPT_TEXT_LIMIT
                if (
                    exclusive_write_owner
                    and tool_id == "file_read"
                    and tool_payload.get("ok")
                    and _relative_workspace_path(
                        arguments.get("path"),
                        workspace_root=self._tool_runtime.workspace_root,
                    )
                    == exclusive_write_owner_path
                ):
                    prompt_text_limit = _EXCLUSIVE_OWNER_FILE_READ_PROMPT_TEXT_LIMIT
                    if exclusive_owner_prefers_file_edit:
                        tool_payload = _file_read_payload_with_line_numbers(
                            tool_payload
                        )
                executed_tools.append(
                    {
                        "tool_id": tool_id,
                        "tool_call_id": tool_call_id,
                        "model_call_id": model_call_id,
                        "arguments": arguments,
                        **tool_payload,
                    }
                )
                current_record = executed_tools[-1]
                context_source_task_id = str(
                    event_context.get("root_task_id")
                    or request.metadata.get("task_id")
                    or ""
                )
                context_source_worker_id = str(
                    event_context.get("worker_id")
                    or request.metadata.get("worker_id")
                    or worker_id
                    or ""
                )
                context_source_event_ids = [
                    value
                    for value in (tool_call_id, model_call_id)
                    if str(value or "").strip()
                ]
                context_capsules = build_tool_context_capsules(
                    current_record,
                    source_task_id=context_source_task_id,
                    source_worker_id=context_source_worker_id,
                    source_trace_id=str(event_context.get("trace_id") or ""),
                    source_event_ids=context_source_event_ids,
                )
                if context_capsules:
                    capsule_payloads = [
                        capsule.model_dump(mode="json", exclude_none=True)
                        for capsule in context_capsules
                    ]
                    current_record["context_capsules"] = capsule_payloads
                    readiness_signal = readiness_signal_from_capsules(
                        context_capsules,
                        source_task_id=context_source_task_id,
                        source_worker_id=context_source_worker_id,
                        predicate="tool_context_available",
                    )
                    readiness_payload = readiness_signal.model_dump(
                        mode="json",
                        exclude_none=True,
                    )
                    current_record["readiness_signal"] = readiness_payload
                    self._emit_event(
                        "context.capsule.emitted",
                        capsule_count=len(capsule_payloads),
                        capsule_ids=[
                            str(capsule.get("capsule_id") or "")
                            for capsule in capsule_payloads
                            if str(capsule.get("capsule_id") or "").strip()
                        ],
                        capsule_kinds=[
                            str(capsule.get("kind") or "")
                            for capsule in capsule_payloads
                            if str(capsule.get("kind") or "").strip()
                        ],
                        capsules=capsule_payloads,
                        tool_id=tool_id,
                        tool_call_id=tool_call_id,
                        model_call_id=model_call_id,
                        **event_context,
                    )
                    self._emit_event(
                        "context.readiness.emitted",
                        readiness=readiness_payload,
                        readiness_id=readiness_signal.readiness_id,
                        ready_for_downstream=readiness_signal.ready_for_downstream,
                        predicate=readiness_signal.predicate,
                        blocker_count=len(readiness_signal.blockers),
                        capsule_count=len(capsule_payloads),
                        capsule_ids=list(readiness_signal.capsule_ids),
                        tool_id=tool_id,
                        tool_call_id=tool_call_id,
                        model_call_id=model_call_id,
                        **event_context,
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": tool_id,
                        "content": json.dumps(
                            _compact_tool_payload_for_prompt(
                                tool_payload,
                                text_limit=prompt_text_limit,
                            ),
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ),
                    }
                )
                return current_record

            def _skip_remaining_tool_calls(
                remaining_calls: Sequence[dict[str, Any]],
                *,
                reason: str,
            ) -> None:
                for skipped_call in remaining_calls:
                    skipped_tool_id, skipped_tool_call_id, skipped_arguments = (
                        self._parse_tool_call(skipped_call)
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

            round_soft_phase: str | None = None
            round_soft_progress: dict[str, int] = {}
            round_soft_tool_call_limit: int | None = None
            if soft_budget_profile == _SOFT_BUDGET_PROFILE_SUPER_DAN:
                round_soft_progress = _soft_budget_progress_snapshot(
                    executed_tools=executed_tools,
                    workspace_root=self._tool_runtime.workspace_root,
                )
                round_soft_phase = _soft_budget_phase(
                    request=request,
                    tool_ids=request_tool_ids,
                    executed_tools=executed_tools,
                    workspace_root=self._tool_runtime.workspace_root,
                    write_stage_first_write_nudged=write_stage_first_write_nudged,
                    write_stage_direct_write_required=write_stage_direct_write_required,
                )
                round_soft_tool_call_limit = _soft_budget_limit(
                    round_soft_phase,
                    limit_kind="tool_calls",
                    hard_limit=self._max_tool_calls,
                    progress=round_soft_progress,
                )
            for call_index, raw_call in enumerate(tool_calls):
                total_tool_calls += 1
                tool_id, tool_call_id, arguments = self._parse_tool_call(raw_call)
                round_tool_call_ids.append(tool_call_id)
                round_soft_tool_calls_consumed = _soft_budget_phase_consumed(
                    round_soft_phase,
                    limit_kind="tool_calls",
                )
                exclusive_owner_has_read_owned_file = (
                    exclusive_write_owner
                    and exclusive_write_owner_path
                    in _successful_read_paths(
                        executed_tools,
                        workspace_root=self._tool_runtime.workspace_root,
                    )
                    and not _successful_workspace_mutation_paths(
                        executed_tools,
                        workspace_root=self._tool_runtime.workspace_root,
                    )
                )
                if total_tool_calls > self._max_tool_calls:
                    await _maybe_extend_hard_budget(
                        limit_kind="tool_calls",
                        current_limit=self._max_tool_calls,
                        last_text=last_result.text or "",
                    )
                if total_tool_calls > self._max_tool_calls:
                    tool_payload = {
                        "ok": False,
                        "error": f"tool_call_limit_exceeded:{self._max_tool_calls}",
                    }
                elif (
                    exclusive_write_owner
                    and _coding_output_kind(request) is not None
                    and tool_id == "file_read"
                    and not _successful_workspace_mutation_paths(
                        executed_tools,
                        workspace_root=self._tool_runtime.workspace_root,
                    )
                    and (
                        exclusive_owner_has_read_owned_file
                        or _relative_workspace_path(
                            arguments.get("path"),
                            workspace_root=self._tool_runtime.workspace_root,
                        )
                        != exclusive_write_owner_path
                    )
                ):
                    tool_payload = {
                        "ok": False,
                        "error": (
                            "exclusive_write_owner_read_scope:"
                            f"{exclusive_write_owner_path}"
                        ),
                    }
                    exclusive_owner_read_scope_nudges.append(exclusive_write_owner_path)
                elif (
                    exclusive_owner_has_read_owned_file
                    and _coding_output_kind(request) is not None
                    and tool_id not in {"file_edit", "file_write"}
                ):
                    tool_payload = {
                        "ok": False,
                        "error": (
                            "exclusive_write_owner_read_budget_consumed:"
                            f"{exclusive_write_owner_path}"
                        ),
                    }
                    exclusive_owner_read_scope_nudges.append(exclusive_write_owner_path)
                elif (
                    round_soft_phase is not None
                    and round_soft_tool_call_limit is not None
                    and round_soft_tool_calls_consumed > round_soft_tool_call_limit
                    and not _tool_call_allowed_past_soft_budget(
                        tool_id,
                        round_soft_phase,
                        allow_final_read=(
                            write_stage_final_read_available
                            and "file_read" in request_tool_ids
                        ),
                    )
                ):
                    tool_payload = {
                        "ok": False,
                        "error": (
                            "tool_phase_budget_exceeded:"
                            f"{round_soft_phase}:{round_soft_tool_call_limit}"
                        ),
                    }
                    phase_budget_nudges.append(
                        (
                            round_soft_phase,
                            "tool_calls",
                            round_soft_tool_call_limit,
                            round_soft_progress,
                        )
                    )
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
                        downshift_reason := file_write_incremental_edit_paths.get(
                            target_path
                        )
                    )
                    is not None
                ):
                    tool_payload = {
                        "ok": False,
                        "error": f"file_write_downshift_required:{downshift_reason}",
                    }
                    file_write_downshift_nudges.append((target_path, downshift_reason))
                elif (
                    tool_id == "file_write"
                    and (
                        shrink_block := _repair_policy_blocked_shrinking_overwrite(
                            request,
                            arguments,
                            workspace_root=self._tool_runtime.workspace_root,
                        )
                    )
                    is not None
                ):
                    blocked_path, current_size, new_size = shrink_block
                    tool_payload = {
                        "ok": False,
                        "error": (
                            "repair_policy_shrinking_overwrite:"
                            f"{blocked_path}:{current_size}:{new_size}"
                        ),
                    }
                    repair_policy_nudges.append((blocked_path, current_size, new_size))
                elif (
                    tool_id in {"file_edit", "file_write"}
                    and (
                        write_stage_first_write_nudged
                        or write_stage_direct_write_required
                    )
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
                    file_read_cache_key = (
                        _file_read_cache_key(
                            arguments, workspace_root=self._tool_runtime.workspace_root
                        )
                        if tool_id == "file_read"
                        else None
                    )
                    file_read_fingerprint_before = (
                        _tool_file_fingerprint(
                            arguments.get("path")
                            or arguments.get("file_path")
                            or arguments.get("filepath"),
                            workspace_root=self._tool_runtime.workspace_root,
                        )
                        if file_read_cache_key is not None
                        else None
                    )
                    cached_file_read = (
                        file_read_result_cache.get(file_read_cache_key)
                        if file_read_cache_key is not None
                        and file_read_fingerprint_before is not None
                        else None
                    )
                    if (
                        cached_file_read is not None
                        and cached_file_read[0] == file_read_fingerprint_before
                    ):
                        result = copy.deepcopy(cached_file_read[1])
                        tool_payload = {
                            "ok": True,
                            "result": result,
                            "cache_hit": True,
                        }
                        self._emit_event(
                            "tool.cache_hit",
                            tool_id=tool_id,
                            arguments=dict(arguments),
                            path=str(file_read_cache_key[0]),
                            **event_context,
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
                            if (
                                tool_id == "file_read"
                                and file_read_cache_key is not None
                                and file_read_fingerprint_before is not None
                            ):
                                file_read_fingerprint_after = _tool_file_fingerprint(
                                    arguments.get("path")
                                    or arguments.get("file_path")
                                    or arguments.get("filepath"),
                                    workspace_root=self._tool_runtime.workspace_root,
                                )
                                if (
                                    file_read_fingerprint_after
                                    == file_read_fingerprint_before
                                ):
                                    file_read_result_cache[file_read_cache_key] = (
                                        file_read_fingerprint_after,
                                        copy.deepcopy(result),
                                    )

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
                    if error_text.startswith("tool_phase_budget_exceeded:"):
                        _skip_remaining_tool_calls(
                            tool_calls[call_index + 1 :],
                            reason="tool_phase_budget_exceeded",
                        )
                        break
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
                            file_write_incremental_edit_paths[downshift_path] = (
                                downshift_reason
                            )
                            file_write_downshift_nudges.append(
                                (downshift_path, downshift_reason)
                            )
                    if error_text.startswith("file_write_downshift_required:"):
                        _skip_remaining_tool_calls(
                            tool_calls[call_index + 1 :],
                            reason="file_write_downshift_required",
                        )
                        break
                    if error_text.startswith("repair_policy_shrinking_overwrite:"):
                        _skip_remaining_tool_calls(
                            tool_calls[call_index + 1 :],
                            reason="repair_policy_shrinking_overwrite",
                        )
                        break
                    followup_message = _source_structure_failure_nudge(
                        tool_id,
                        error_text,
                        arguments,
                    )
                    if followup_message:
                        source_structure_nudges.append((tool_id, followup_message))
                        _skip_remaining_tool_calls(
                            tool_calls[call_index + 1 :],
                            reason="source_structure_nudge",
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
            if phase_budget_nudges:
                phase, limit_kind, soft_limit, progress = phase_budget_nudges[0]
                action = _soft_budget_action_for_phase(phase)
                allow_final_read = not _interactive_source_implementation_request(
                    request
                ) and any(
                    _tool_schema_name(tool) == "file_read"
                    for tool in request_tool_schemas
                    if isinstance(tool, dict)
                )
                if action == "narrow_write_stage":
                    write_stage_first_write_nudged = True
                    write_stage_final_read_available = allow_final_read
                    write_stage_tool_schemas = _write_stage_tool_schemas(
                        request_tool_schemas,
                        allow_final_read=write_stage_final_read_available,
                        prefer_file_write_only=exclusive_write_owner,
                        prefer_file_edit_only=exclusive_owner_prefers_file_edit,
                    )
                    if write_stage_tool_schemas:
                        active_tool_schemas = write_stage_tool_schemas
                        disabled_tool_ids.clear()
                    write_stage_direct_write_required = (
                        not write_stage_final_read_available
                    )
                elif action == "require_direct_write":
                    write_stage_tool_schemas = _write_stage_tool_schemas(
                        request_tool_schemas,
                        allow_final_read=write_stage_final_read_available,
                        prefer_file_write_only=exclusive_write_owner,
                        prefer_file_edit_only=exclusive_owner_prefers_file_edit,
                    )
                    if write_stage_tool_schemas:
                        active_tool_schemas = write_stage_tool_schemas
                        disabled_tool_ids.clear()
                    write_stage_direct_write_required = True
                elif action == "force_finalize":
                    forced_finalize_without_tools = True
                    active_tool_schemas = []
                messages.append(
                    {
                        "role": "user",
                        "content": _soft_budget_message(
                            phase,
                            request=request,
                            limit_kind=limit_kind,
                            allow_final_read=allow_final_read,
                        ),
                    }
                )
                self._emit_event(
                    "toolloop.soft_budget_nudged",
                    phase=phase,
                    action=action,
                    limit_kind=limit_kind,
                    soft_limit=soft_limit,
                    hard_limit=self._max_tool_calls,
                    progress=progress,
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
                continue
            if file_write_downshift_nudges:
                unique_messages = []
                seen_messages: set[str] = set()
                affected_paths: list[str] = []
                reasons: list[str] = []
                for path, reason in file_write_downshift_nudges:
                    affected_paths.append(path)
                    reasons.append(reason)
                    message = _file_write_incremental_edit_required_message(
                        path, reason
                    )
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
            if repair_policy_nudges:
                unique_messages = []
                seen_messages: set[str] = set()
                affected_paths: list[str] = []
                for path, current_size, new_size in repair_policy_nudges:
                    affected_paths.append(path)
                    message = _repair_policy_shrinking_overwrite_message(
                        path,
                        current_size,
                        new_size,
                    )
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
                    "toolloop.repair_policy_nudged",
                    paths=_dedupe(affected_paths),
                    policy="forbid_shrinking_existing_artifacts",
                    enabled_tools=list(request_tool_ids),
                    blocked_by_tool_call_ids=list(round_tool_call_ids) or None,
                    tool_calls_executed=len(executed_tools),
                    **event_context,
                )
            if exclusive_owner_read_scope_nudges:
                owner_paths = _dedupe(exclusive_owner_read_scope_nudges)
                messages.append(
                    {
                        "role": "user",
                        "content": "\n".join(
                            _exclusive_write_owner_read_scope_message(path)
                            for path in owner_paths
                        ),
                    }
                )
                self._emit_event(
                    "toolloop.exclusive_write_owner_read_scope_nudged",
                    owner_paths=owner_paths,
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
                direct_write_tool_schemas = _direct_write_tool_schemas(
                    tool_schemas,
                    prefer_file_write_only=exclusive_write_owner,
                    prefer_file_edit_only=exclusive_owner_prefers_file_edit,
                )
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
            if source_structure_nudges:
                unique_messages: list[str] = []
                seen_messages: set[str] = set()
                affected_tool_ids: list[str] = []
                for tool_id, message in source_structure_nudges:
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
                if any(
                    _tool_schema_name(tool) == "file_read"
                    for tool in tool_schemas
                    if isinstance(tool, dict)
                ):
                    source_recovery_tool_schemas = _write_stage_tool_schemas(
                        tool_schemas,
                        allow_final_read=True,
                        prefer_file_write_only=exclusive_write_owner,
                        prefer_file_edit_only=exclusive_owner_prefers_file_edit,
                    )
                    if source_recovery_tool_schemas:
                        active_tool_schemas = source_recovery_tool_schemas
                        disabled_tool_ids.clear()
                        write_stage_final_read_available = True
                        write_stage_final_read_consumed = False
                        write_stage_direct_write_required = False
                self._emit_event(
                    "toolloop.source_structure_nudged",
                    tool_ids=_dedupe(affected_tool_ids),
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
                        "content": _temporarily_disabled_tool_message(
                            newly_disabled_tool_ids
                        ),
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
                direct_write_tool_schemas = _direct_write_tool_schemas(
                    tool_schemas,
                    prefer_file_write_only=exclusive_write_owner,
                    prefer_file_edit_only=exclusive_owner_prefers_file_edit,
                )
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
            if (
                research_finalize_reason is not None
                and not research_note_finalize_nudged
            ):
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
                allow_write_nudge_final_read = not _exclusive_write_owner_path(
                    request
                ) and not _interactive_source_implementation_request(request)
                write_stage_final_read_available = allow_write_nudge_final_read and any(
                    _tool_schema_name(tool) == "file_read"
                    for tool in request_tool_schemas
                    if isinstance(tool, dict)
                )
                write_stage_tool_schemas = _write_stage_tool_schemas(
                    request_tool_schemas,
                    allow_final_read=write_stage_final_read_available,
                    prefer_file_write_only=exclusive_write_owner,
                    prefer_file_edit_only=exclusive_owner_prefers_file_edit,
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
                            request=request,
                            allow_final_read=write_stage_final_read_available,
                            file_write_only=exclusive_write_owner
                            and any(
                                _tool_schema_name(tool) == "file_write"
                                for tool in active_tool_schemas
                            ),
                            file_edit_only=exclusive_owner_prefers_file_edit
                            and any(
                                _tool_schema_name(tool) == "file_edit"
                                for tool in active_tool_schemas
                            ),
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

    def _resolve_tool_schemas(
        self, request_tools: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if "native_worker" in self._tool_runtime.tool_ids and any(
            tool.get("function", {}).get("name") == "shell_command" for tool in request_tools
        ):
            from dan.tools.native_worker import TOOL_METADATA
            request_tools = [*request_tools, {"type": "function", "function": {
                "name": "native_worker", "description": TOOL_METADATA["description"], "parameters": TOOL_METADATA["parameters"],
            }}]
        allowed = set(self._tool_runtime.tool_ids)
        filtered: list[dict[str, Any]] = []
        seen: set[str] = set()
        for tool in request_tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            name = (
                str(function.get("name") or "").strip()
                if isinstance(function, dict)
                else ""
            )
            if name and name in allowed:
                if name in seen:
                    continue
                seen.add(name)
                runtime_metadata = self._tool_runtime.metadata_for(name)
                runtime_parameters = (
                    runtime_metadata.get("parameters")
                    or function.get("parameters")
                    or {"type": "object", "properties": {}}
                )
                filtered.append(
                    {
                        "type": (
                            str(tool.get("type") or "function")
                            if isinstance(tool, dict)
                            else "function"
                        ),
                        "function": {
                            "name": name,
                            "description": str(
                                runtime_metadata.get("description")
                                or function.get("description")
                                or name
                            ).strip(),
                            "parameters": _model_facing_tool_parameters(
                                name,
                                runtime_parameters,
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
        tool_id = (
            str(function.get("name") or raw_call.get("name") or "").strip()
            if isinstance(function, dict)
            else ""
        )
        tool_call_id = str(
            raw_call.get("id") or f"tool-call-{tool_id or 'unknown'}"
        ).strip()
        raw_arguments = (
            function.get("arguments")
            if isinstance(function, dict)
            else raw_call.get("arguments")
        )
        if isinstance(raw_arguments, dict):
            arguments = dict(raw_arguments)
        elif isinstance(raw_arguments, str) and raw_arguments.strip():
            arguments = _recover_tool_arguments_from_raw_text(tool_id, raw_arguments)
        else:
            arguments = {}
        return tool_id, tool_call_id, arguments


__all__ = [
    "DEFAULT_LIVE_ORGANISM_TOOL_IDS",
    "LocalOrganismToolRuntime",
    "ToolLoopCompletionProvider",
    "available_local_organism_tools",
]
