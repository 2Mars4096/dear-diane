"""Local LLM + tool runtime helpers for bounded organisms and coding CLIs."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable, Sequence

from dan.providers import CompletionResult, LLMProvider, apply_cache_hints
from dan.tools import get_all_tools
from dan.tools._git_helpers import _find_repo
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse
from dan.worker.core.model import WorkerDefinition
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
_INTERNAL_WORKSPACE_DIR_NAMES = frozenset({".dan-code", ".git", ".pytest_cache", "__pycache__"})
_GREENFIELD_OPERATOR_ARTIFACTS = frozenset({"prompt.md", "acceptance.md", "report.json"})
ToolRuntimeEventCallback = Callable[[dict[str, Any]], None]
ToolApprovalCallback = Callable[[str, dict[str, Any], dict[str, Any]], bool]
_CODING_CANDIDATE_REQUIRED_KEYS = frozenset(
    {"candidate_id", "change_summary", "target_files", "test_plan", "risks"}
)


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


def _read_only_tool_ids(tool_ids: Sequence[str]) -> list[str]:
    return [tool_id for tool_id in _dedupe(tool_ids) if tool_id not in _READ_ONLY_TOOL_EXCLUSIONS]


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


def _relative_workspace_path(path: Any, *, workspace_root: Path) -> str | None:
    text = str(path or "").strip()
    if not text:
        return None
    try:
        candidate = Path(text).expanduser()
        resolved = candidate if candidate.is_absolute() else workspace_root / candidate
        relative = resolved.resolve().relative_to(workspace_root.resolve())
        rendered = relative.as_posix()
        return rendered or "."
    except Exception:
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


def _request_expects_coding_candidate(request: CompletionRequest) -> bool:
    return _CODING_CANDIDATE_REQUIRED_KEYS.issubset(_expected_return_shape_keys(request))


def _tool_argument_path(tool: dict[str, Any]) -> str:
    arguments = dict(tool.get("arguments") or {})
    result = dict(tool.get("result") or {})
    return str(
        result.get("path")
        or arguments.get("path")
        or arguments.get("file_path")
        or ""
    ).strip()


def _successful_mutation_paths(executed_tools: Sequence[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for tool in executed_tools:
        if not tool.get("ok"):
            continue
        tool_id = str(tool.get("tool_id") or "").strip()
        if tool_id not in {"file_write", "file_edit"}:
            continue
        path = _tool_argument_path(tool)
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
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


def _partial_coding_candidate_from_tool_evidence(
    *,
    request: CompletionRequest,
    executed_tools: Sequence[dict[str, Any]],
    stop_reason: str,
    existing_text: str | None = None,
) -> dict[str, Any] | None:
    if not _request_expects_coding_candidate(request):
        return None

    target_files = _successful_mutation_paths(executed_tools)
    if not target_files:
        return None

    base_payload: dict[str, Any] = {}
    parsed = parse_jsonish_payload(existing_text)
    if isinstance(parsed, dict):
        base_payload = dict(parsed)

    reason_text = " ".join(stop_reason.replace(":", " ").replace("_", " ").split())
    verification_commands = _successful_verification_commands(executed_tools)

    payload = dict(base_payload)
    payload.setdefault("candidate_id", "partial-candidate-from-tool-evidence")
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
    payload["workspace_effect"] = "modified"
    payload["synthesized_from_tool_evidence"] = True
    payload["fallback_reason"] = stop_reason
    return payload


def _write_capable_coding_stage_first_write_nudge_reason(
    *,
    request: CompletionRequest,
    tool_ids: Sequence[str],
    executed_tools: Sequence[dict[str, Any]],
    workspace_root: Path,
) -> str | None:
    if not _request_expects_coding_candidate(request):
        return None
    if _tool_ids_are_read_only(tool_ids):
        return None
    if _successful_mutation_paths(executed_tools):
        return None
    for tool in executed_tools:
        if _tool_confirms_effectively_empty_workspace(tool, workspace_root=workspace_root):
            return "first_write_after_empty_workspace"
    return None


def _write_capable_coding_stage_first_write_nudge_message(reason: str) -> str:
    reason_text = reason.replace("_", " ")
    return (
        "Controller note: this write-capable coding stage has already confirmed an effectively empty workspace "
        f"({reason_text}). Stop auditing and make the first concrete project write now using the direct file tools "
        "that are already enabled. The next tool call should be `file_write` or `file_edit`, not another read, search, "
        "or final prose-only answer. Create one or two small real files first, then continue incrementally. Prefer a "
        "minimal runnable slice over a complete project in one giant tool call. If you truly cannot materialize any "
        "bounded file set in this turn, return an explicit blocked candidate now instead of doing more discovery."
    )


def _direct_write_tool_schemas(tool_schemas: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    direct_write_tools = []
    for tool in tool_schemas:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = str(function.get("name") or "").strip() if isinstance(function, dict) else ""
        if name in {"file_write", "file_edit"}:
            direct_write_tools.append(tool)
    return direct_write_tools


def _read_only_coding_worker_finalize_reason(
    *,
    request: CompletionRequest,
    tool_ids: Sequence[str],
    executed_tools: Sequence[dict[str, Any]],
    workspace_root: Path,
) -> str | None:
    system_prompt = str(request.system_prompt or "")
    expected_shape = str(getattr(request.output_contract, "expected_return_shape", "") or "")
    worker_id = str(request.metadata.get("worker_id") or "").strip()
    looks_like_coding_worker = (
        "Role: coding_worker" in system_prompt
        or "candidate_fragment" in expected_shape
        or worker_id.startswith("coding-build.worker")
    )
    if not looks_like_coding_worker:
        return None
    if not _tool_ids_are_read_only(tool_ids):
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


def _read_only_coding_worker_finalize_message(reason: str) -> str:
    reason_text = reason.replace("_", " ")
    return (
        "Controller note: stop using tools in this read-only coding worker round. "
        f"The empty-workspace probe has already confirmed a no-progress pattern ({reason_text}). "
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
        return kwargs

    async def call(
        self,
        tool_id: str,
        arguments: dict[str, Any] | None = None,
        *,
        worker_id: str | None = None,
    ) -> Any:
        if tool_id not in self._tools:
            raise KeyError(f"Tool '{tool_id}' is not enabled for this runtime")

        function, metadata = self._tools[tool_id]
        kwargs = self._normalize_tool_arguments(tool_id, dict(arguments or {}))
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
            worker_id=str(worker_id or "").strip() or None,
        )
        if self._approval_callback is not None:
            approved = self._approval_callback(tool_id, dict(kwargs), dict(metadata))
            if not approved:
                self._emit_event(
                    "tool.denied",
                    tool_id=tool_id,
                    arguments=dict(kwargs),
                    metadata=dict(metadata),
                    worker_id=str(worker_id or "").strip() or None,
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
                worker_id=str(worker_id or "").strip() or None,
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
            worker_id=str(worker_id or "").strip() or None,
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

    def _emit_event(self, event: str, **payload: Any) -> None:
        if self._event_callback is None:
            return
        self._event_callback({"event": event, **payload})

    async def _complete_text_response(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        request: CompletionRequest,
        provider_kwargs: dict[str, Any],
        round_number: int,
        worker_id: str | None,
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
            worker_id=worker_id,
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
                        delta=delta,
                        accumulated=accumulated,
                        worker_id=worker_id,
                    )
            self._emit_event(
                "model.stream.completed",
                model=model,
                round=round_number,
                usage=usage,
                worker_id=worker_id,
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
    ) -> CompletionResult:
        if self._completion_timeout_seconds is None:
            return await self._complete_text_response(
                messages=messages,
                model=model,
                request=request,
                provider_kwargs=provider_kwargs,
                round_number=round_number,
                worker_id=worker_id,
            )
        try:
            async with asyncio.timeout(self._completion_timeout_seconds):
                return await self._complete_text_response(
                    messages=messages,
                    model=model,
                    request=request,
                    provider_kwargs=provider_kwargs,
                    round_number=round_number,
                    worker_id=worker_id,
                )
        except TimeoutError as exc:
            raise TimeoutError(
                f"provider_completion_timeout:{self._completion_timeout_seconds:.2f}s"
            ) from exc

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        model = str(request.model or self._default_model or "").strip()
        if not model:
            raise ValueError("Tool-loop completion provider requires a concrete model")
        worker_id = str(request.metadata.get("worker_id") or "").strip() or None

        tool_schemas = self._resolve_tool_schemas(request.tools)
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
        stop_reason = "completed"
        last_result: Any = None
        forced_finalize_without_tools = False
        write_stage_first_write_nudged = False
        research_note_finalize_nudged = False
        provider_safety_retry_attempted = False

        while True:
            self._emit_event(
                "model.requested",
                model=model,
                round=rounds + 1,
                tool_count=len(active_tool_schemas),
                worker_id=worker_id,
            )
            provider_kwargs = {
                "tools": active_tool_schemas or None,
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
                )
            except Exception as exc:
                if _provider_timeout_error(exc):
                    timeout_seconds = self._completion_timeout_seconds
                    self._emit_event(
                        "model.timeout",
                        model=model,
                        round=rounds + 1,
                        timeout_seconds=timeout_seconds,
                        tool_count=len(active_tool_schemas),
                        error_type=type(exc).__name__,
                        worker_id=worker_id,
                    )
                    stop_reason = "provider_completion_timeout"
                    partial_candidate = _partial_coding_candidate_from_tool_evidence(
                        request=request,
                        executed_tools=executed_tools,
                        stop_reason=stop_reason,
                    )
                    fallback_text = (
                        json.dumps(partial_candidate, ensure_ascii=False, sort_keys=True)
                        if partial_candidate is not None
                        else _provider_timeout_fallback_text(request)
                    )
                    self._emit_event(
                        "completion.completed",
                        model=model,
                        stop_reason=stop_reason,
                        tool_calls_executed=len(executed_tools),
                        worker_id=worker_id,
                    )
                    return CompletionResponse(
                        text=fallback_text,
                        raw={
                            "provider_result": {
                                "model": model,
                                "finish_reason": "provider_completion_timeout",
                                "usage": None,
                                "provider_metadata": {
                                    "provider_timeout_fallback": True,
                                    "timeout_seconds": timeout_seconds,
                                    "error_type": type(exc).__name__,
                                    "tool_evidence_fallback": partial_candidate is not None,
                                },
                            },
                            "assistant_message": {
                                "role": "assistant",
                                "content": fallback_text,
                            },
                            "executed_tools": executed_tools,
                            "stop_reason": stop_reason,
                        },
                    )
                if not _provider_prompt_filter_error(exc):
                    raise
                self._emit_event(
                    "model.provider_prompt_rejected",
                    model=model,
                    round=rounds + 1,
                    error_type=type(exc).__name__,
                    retry=not provider_safety_retry_attempted,
                    worker_id=worker_id,
                )
                if provider_safety_retry_attempted:
                    stop_reason = "provider_prompt_rejected_after_safety_retry"
                    fallback_text = _provider_safety_fallback_text(request)
                    self._emit_event(
                        "completion.completed",
                        model=model,
                        stop_reason=stop_reason,
                        tool_calls_executed=len(executed_tools),
                        worker_id=worker_id,
                    )
                    return CompletionResponse(
                        text=fallback_text,
                        raw={
                            "provider_result": {
                                "model": model,
                                "finish_reason": "provider_prompt_rejected",
                                "usage": None,
                                "provider_metadata": {
                                    "provider_safety_fallback": True,
                                    "error_type": type(exc).__name__,
                                },
                            },
                            "assistant_message": {
                                "role": "assistant",
                                "content": fallback_text,
                            },
                            "executed_tools": executed_tools,
                            "stop_reason": stop_reason,
                        },
                    )
                provider_safety_retry_attempted = True
                active_tool_schemas = []
                messages = _provider_safety_retry_messages(request)
                stop_reason = "completed_after_provider_safety_retry"
                self._emit_event(
                    "model.provider_safety_retry",
                    model=model,
                    round=rounds + 1,
                    worker_id=worker_id,
                )
                continue
            assistant_message = self._assistant_message(last_result)
            messages.append(assistant_message)

            tool_calls = list(last_result.tool_calls or [])
            self._emit_event(
                "model.responded",
                model=last_result.model or model,
                round=rounds + 1,
                tool_calls=[call.get("function", {}).get("name") or call.get("name") for call in tool_calls if isinstance(call, dict)],
                finish_reason=getattr(last_result, "finish_reason", None),
                text=(last_result.text or "")[:400],
                streamed=bool((getattr(last_result, "provider_metadata", None) or {}).get("streamed_response")),
                worker_id=worker_id,
            )
            if forced_finalize_without_tools and not active_tool_schemas and tool_calls:
                stop_reason = "forced_finalize_guardrail_unheeded"
                self._emit_event(
                    "completion.completed",
                    model=last_result.model or model,
                    stop_reason=stop_reason,
                    tool_calls_executed=len(executed_tools),
                    worker_id=worker_id,
                )
                return CompletionResponse(
                    text=last_result.text or "",
                    raw={
                        "provider_result": {
                            "model": last_result.model,
                            "finish_reason": last_result.finish_reason,
                            "usage": last_result.usage,
                            "provider_metadata": last_result.provider_metadata,
                        },
                        "assistant_message": assistant_message,
                        "executed_tools": executed_tools,
                        "stop_reason": stop_reason,
                    },
                )
            if not tool_calls:
                self._emit_event(
                    "completion.completed",
                    model=last_result.model or model,
                    stop_reason=stop_reason,
                    tool_calls_executed=len(executed_tools),
                    worker_id=worker_id,
                )
                return CompletionResponse(
                    text=last_result.text,
                    raw={
                        "provider_result": {
                            "model": last_result.model,
                            "finish_reason": last_result.finish_reason,
                            "usage": last_result.usage,
                            "provider_metadata": last_result.provider_metadata,
                        },
                        "assistant_message": assistant_message,
                        "executed_tools": executed_tools,
                        "stop_reason": stop_reason,
                    },
                )

            rounds += 1
            if self._max_rounds is not None and rounds > self._max_rounds:
                stop_reason = f"max_tool_rounds_exceeded:{self._max_rounds}"
                partial_candidate = _partial_coding_candidate_from_tool_evidence(
                    request=request,
                    executed_tools=executed_tools,
                    stop_reason=stop_reason,
                    existing_text=last_result.text or "",
                )
                fallback_text = (
                    json.dumps(partial_candidate, ensure_ascii=False, sort_keys=True)
                    if partial_candidate is not None
                    else (last_result.text or "")
                )
                self._emit_event(
                    "completion.completed",
                    model=last_result.model or model,
                    stop_reason=stop_reason,
                    tool_calls_executed=len(executed_tools),
                    worker_id=worker_id,
                )
                return CompletionResponse(
                    text=fallback_text,
                    raw={
                        "provider_result": {
                            "model": last_result.model,
                            "finish_reason": last_result.finish_reason,
                            "usage": last_result.usage,
                            "provider_metadata": {
                                **dict(last_result.provider_metadata or {}),
                                "tool_evidence_fallback": partial_candidate is not None,
                            },
                        },
                        "assistant_message": assistant_message,
                        "executed_tools": executed_tools,
                        "stop_reason": stop_reason,
                    },
                )

            for raw_call in tool_calls:
                total_tool_calls += 1
                tool_id, tool_call_id, arguments = self._parse_tool_call(raw_call)
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
                else:
                    try:
                        result = await self._tool_runtime.call(
                            tool_id,
                            arguments,
                            worker_id=worker_id,
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

                executed_tools.append(
                    {
                        "tool_id": tool_id,
                        "tool_call_id": tool_call_id,
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
                            tool_payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ),
                    }
                )
            finalize_reason = _read_only_coding_worker_finalize_reason(
                request=request,
                tool_ids=[
                    str(tool.get("function", {}).get("name") or "").strip()
                    for tool in active_tool_schemas
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
                        "content": _read_only_coding_worker_finalize_message(finalize_reason),
                    }
                )
                self._emit_event(
                    "toolloop.read_only_finalize_forced",
                    reason=finalize_reason,
                    tool_calls_executed=len(executed_tools),
                    worker_id=worker_id,
                )
                continue

            research_finalize_reason = _research_note_finalize_reason(
                request=request,
                tool_ids=[
                    str(tool.get("function", {}).get("name") or "").strip()
                    for tool in active_tool_schemas
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
                    worker_id=worker_id,
                )
                continue

            write_nudge_reason = _write_capable_coding_stage_first_write_nudge_reason(
                request=request,
                tool_ids=[
                    str(tool.get("function", {}).get("name") or "").strip()
                    for tool in active_tool_schemas
                    if isinstance(tool, dict)
                ],
                executed_tools=executed_tools,
                workspace_root=self._tool_runtime.workspace_root,
            )
            if write_nudge_reason is not None and not write_stage_first_write_nudged:
                write_stage_first_write_nudged = True
                direct_write_tool_schemas = _direct_write_tool_schemas(active_tool_schemas)
                if direct_write_tool_schemas:
                    active_tool_schemas = direct_write_tool_schemas
                messages.append(
                    {
                        "role": "user",
                        "content": _write_capable_coding_stage_first_write_nudge_message(
                            write_nudge_reason
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
                    worker_id=worker_id,
                )

    def _resolve_tool_schemas(self, request_tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed = set(self._tool_runtime.tool_ids)
        filtered: list[dict[str, Any]] = []
        for tool in request_tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            name = str(function.get("name") or "").strip() if isinstance(function, dict) else ""
            if name and name in allowed:
                filtered.append(tool)
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
    return organism.model_copy(
        update={
            "planner_worker": _worker_with_tool_ids(organism.planner_worker, []),
            "research_organ": _organ_with_tool_ids(
                organism.research_organ,
                member_tool_ids=read_only_tool_ids,
                lead_tool_ids=read_only_tool_ids,
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
    return organism.model_copy(
        update={
            "orchestrator_worker": _worker_with_tool_ids(organism.orchestrator_worker, []),
            "worker_tool_ids": list(read_only_tool_ids),
            "aggregator_organ": _organ_with_tool_ids(
                organism.aggregator_organ,
                member_tool_ids=full_tool_ids,
                lead_tool_ids=full_tool_ids,
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
