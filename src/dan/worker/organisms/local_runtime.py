"""Local LLM + tool runtime helpers for bounded organisms and coding CLIs."""

from __future__ import annotations

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
ToolRuntimeEventCallback = Callable[[dict[str, Any]], None]
ToolApprovalCallback = Callable[[str, dict[str, Any], dict[str, Any]], bool]


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

    lines = [
        "Local tool-use policy:",
        "- Prefer the most specific structured tool available for the job.",
        "- Keep tool calls targeted and incremental. Avoid duplicate discovery once you already have the needed fact.",
    ]
    if "list_directory" in available:
        lines.append("- Use `list_directory` for directory inspection instead of shell `ls`.")
    if "file_read" in available:
        lines.append("- Use `file_read` for file contents instead of shell `cat`, `head`, or similar fallbacks.")
    if "file_edit" in available:
        lines.append(
            "- Use `file_edit` for targeted line-based edits to existing files. Always include `path` and `start_line`, and include `content` for replace/insert edits. If you need multiple non-overlapping edits in the same file, prefer one `file_edit` call with `edits=[...]` over repeated single-edit calls."
        )
    if "file_write" in available:
        lines.append(
            "- Use `file_write` for creating new files or replacing/appending whole-file content. Do not use shell heredocs, redirection, or `cat > file` when `file_edit` or `file_write` is available."
        )
    if "web_search" in available:
        lines.append(
            "- Use `web_search` for live external lookups or lightweight web research instead of guessing current facts."
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
        kwargs = dict(arguments or {})
        if tool_id == "shell_command" and not str(kwargs.get("working_directory") or "").strip():
            kwargs["working_directory"] = str(self._workspace_root)
        elif tool_id in {"git_status", "git_diff", "git_log"} and "path" not in kwargs:
            kwargs["path"] = str(self._workspace_root)
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

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        model = str(request.model or self._default_model or "").strip()
        if not model:
            raise ValueError("Tool-loop completion provider requires a concrete model")
        worker_id = str(request.metadata.get("worker_id") or "").strip() or None

        tool_schemas = self._resolve_tool_schemas(request.tools)
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

        while True:
            self._emit_event(
                "model.requested",
                model=model,
                round=rounds + 1,
                tool_count=len(tool_schemas),
                worker_id=worker_id,
            )
            provider_kwargs = {
                "tools": tool_schemas or None,
                **self._provider_request_overrides,
            }
            last_result = await self._complete_text_response(
                messages=apply_cache_hints(self._provider, list(messages)),
                model=model,
                request=request,
                provider_kwargs=provider_kwargs,
                round_number=rounds + 1,
                worker_id=worker_id,
            )
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
