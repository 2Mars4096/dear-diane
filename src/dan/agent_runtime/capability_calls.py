"""Canonical helpers for capability-call planning inside the tool loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import json
import logging
from typing import Any, Awaitable, Callable, Sequence

_DELETE_AFTER_INVENTORY_PROMPT = (
    "You now have the latest workflow inventory. If you still need to delete workflows, "
    "call `delete_graph` only for exact `graph_id` values returned by the latest inventory "
    "tool results in this conversation. Do not guess, reuse remembered IDs, or delete from "
    "stale names. If the intended workflow is not listed anymore, say it is already absent "
    "instead of calling `delete_graph`."
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingCapabilityCall:
    """Planned capability call metadata before handler execution."""

    tool_name: str
    args: Any
    args_preview: str
    event_tool_call_id: str
    raw_tool_call: dict[str, Any] | None = None
    raw_tool_call_id: str = ""
    tool_is_cacheable: bool = False
    cache_key: str | None = None
    dedupe_from: int | None = None


@dataclass(frozen=True)
class CapabilityExecutionOutcome:
    """Normalized capability execution outcome for one planned call."""

    pending: PendingCapabilityCall
    cap_result: Any
    duration_ms: int
    status: str
    output_preview: str
    cache_hit: bool = False


def capability_cache_key(tool_name: str, args: dict[str, Any]) -> str:
    """Build a stable cache key for one capability call."""
    try:
        return f"{tool_name}:{json.dumps(args or {}, sort_keys=True, default=str)}"
    except Exception:
        return f"{tool_name}:{str(args)}"


def copy_capability_result(result_obj: Any) -> Any:
    """Copy one capability result object when possible."""
    return replace(result_obj) if dataclass_is_instance(result_obj) else result_obj


def normalize_file_read_range(
    args: dict[str, Any],
) -> tuple[str | None, int, float] | None:
    """Normalize file-read range args for cache reuse."""
    if not isinstance(args, dict):
        return None
    if args.get("grep"):
        return None
    path = str(args.get("path") or args.get("file_path") or args.get("filepath") or "").strip()
    if not path:
        return None
    start_line = args.get("start_line")
    end_line = args.get("end_line")
    try:
        start = int(start_line) if start_line is not None else 1
    except (TypeError, ValueError):
        start = 1
    try:
        end = int(end_line) if end_line is not None else float("inf")
    except (TypeError, ValueError):
        end = float("inf")
    return path, start, end


def extract_raw_capability_tool_calls(
    tool_calls: Sequence[dict[str, Any]] | None,
    *,
    is_capability_tool: Callable[[str], bool],
) -> list[dict[str, Any]]:
    """Filter provider tool calls down to capability tools only."""
    raw_tool_calls: list[dict[str, Any]] = []
    for tool_call in tool_calls or []:
        func = tool_call.get("function") if isinstance(tool_call, dict) else None
        name = str(func.get("name") or "").strip() if isinstance(func, dict) else ""
        if is_capability_tool(name):
            raw_tool_calls.append(tool_call)
    return raw_tool_calls


def build_pending_capability_calls(
    cap_calls: Sequence[tuple[str, Any]],
    raw_tool_calls: Sequence[dict[str, Any]],
    *,
    call_id_factory: Callable[[], str],
    args_preview_limit: int = 200,
) -> list[PendingCapabilityCall]:
    """Build stable pending-call descriptors from parsed capability calls."""
    pending_calls: list[PendingCapabilityCall] = []
    for index, (tool_name, args) in enumerate(cap_calls):
        event_tool_call_id = call_id_factory()
        raw_tool_call = raw_tool_calls[index] if index < len(raw_tool_calls) else None
        raw_tool_call_id = str((raw_tool_call or {}).get("id") or event_tool_call_id)
        try:
            args_preview = json.dumps(args, default=str)[:args_preview_limit] if args else ""
        except Exception:
            args_preview = str(args or "")[:args_preview_limit]
        pending_calls.append(
            PendingCapabilityCall(
                tool_name=str(tool_name or "").strip(),
                args=args,
                args_preview=args_preview,
                event_tool_call_id=event_tool_call_id,
                raw_tool_call=raw_tool_call,
                raw_tool_call_id=raw_tool_call_id,
            )
        )
    return pending_calls


def split_inventory_then_delete_batch(
    pending_calls: Sequence[PendingCapabilityCall],
) -> tuple[list[PendingCapabilityCall], str | None]:
    """Defer destructive workflow deletions until after fresh inventory results."""
    inventory_tools = {"list_graphs", "list_my_workflows", "search_workflows"}
    destructive_tools = {"delete_graph"}
    tool_names = {call.tool_name for call in pending_calls}
    if tool_names.isdisjoint(inventory_tools) or tool_names.isdisjoint(destructive_tools):
        return list(pending_calls), None

    executed_calls = [
        call
        for call in pending_calls
        if call.tool_name not in destructive_tools
    ]
    deferred_count = len(pending_calls) - len(executed_calls)
    if not executed_calls or deferred_count == 0:
        return list(pending_calls), None
    return executed_calls, _DELETE_AFTER_INVENTORY_PROMPT


def annotate_capability_call_plan(
    pending_calls: Sequence[PendingCapabilityCall],
    *,
    is_cacheable: Callable[[str], bool],
    cache_key_for: Callable[[str, Any], str],
) -> list[PendingCapabilityCall]:
    """Attach cache/dedupe metadata to planned capability calls."""
    dedupe_sources: dict[str, int] = {}
    annotated_calls: list[PendingCapabilityCall] = []
    for index, call in enumerate(pending_calls):
        tool_is_cacheable = bool(is_cacheable(call.tool_name))
        cache_key = cache_key_for(call.tool_name, call.args) if tool_is_cacheable else None
        dedupe_from = dedupe_sources.get(cache_key) if cache_key is not None else None
        if cache_key is not None and dedupe_from is None:
            dedupe_sources[cache_key] = index
        annotated_calls.append(
            replace(
                call,
                tool_is_cacheable=tool_is_cacheable,
                cache_key=cache_key,
                dedupe_from=dedupe_from,
            )
        )
    return annotated_calls


def group_pending_capability_calls(
    pending_calls: Sequence[PendingCapabilityCall],
    *,
    tool_family_for: Callable[[str], str],
) -> list[list[PendingCapabilityCall]]:
    """Group unique planned capability calls by their parallel-execution family."""
    groups: list[list[PendingCapabilityCall]] = []
    for call in pending_calls:
        if call.dedupe_from is not None:
            continue
        family = tool_family_for(call.tool_name)
        if groups and tool_family_for(groups[-1][0].tool_name) == family:
            groups[-1].append(call)
        else:
            groups.append([call])
    return groups


def expand_deduped_capability_results(
    pending_calls: Sequence[PendingCapabilityCall],
    *,
    unique_results_by_index: dict[int, CapabilityExecutionOutcome],
    copy_result: Callable[[Any], Any],
) -> list[CapabilityExecutionOutcome]:
    """Re-expand deduped outcomes so every pending call has a result entry."""
    expanded_results: list[CapabilityExecutionOutcome] = []
    for index, call in enumerate(pending_calls):
        source_index = call.dedupe_from
        if source_index is None:
            expanded_results.append(unique_results_by_index[index])
            continue
        source = unique_results_by_index[source_index]
        expanded_results.append(
            CapabilityExecutionOutcome(
                pending=call,
                cap_result=copy_result(source.cap_result),
                duration_ms=0,
                status=source.status,
                output_preview=source.output_preview,
                cache_hit=True,
            )
        )
    return expanded_results


async def execute_capability_call(
    pending: PendingCapabilityCall,
    *,
    dispatch: Callable[[str, Any], Awaitable[Any]],
    make_error_result: Callable[[Exception], Any],
    tool_result_cache: dict[str, Any],
    file_read_cache: dict[str, list[tuple[int, float, Any, Any]]],
    file_fingerprint_for: Callable[[str], Any | None] | None = None,
    max_retryable_retries: int,
    logger_override: logging.Logger | None = None,
) -> CapabilityExecutionOutcome:
    """Execute one capability call with cache reuse and bounded retry handling."""
    log = logger_override or logger
    cap_start = asyncio.get_running_loop().time()
    tool_is_cacheable = bool(pending.tool_is_cacheable)
    cache_key = pending.cache_key or capability_cache_key(pending.tool_name, pending.args)
    normalized_range = (
        normalize_file_read_range(pending.args)
        if pending.tool_name == "file_read"
        else None
    )
    cached_result = None
    if tool_is_cacheable and pending.tool_name == "file_read":
        if normalized_range is not None:
            path, start, end = normalized_range
            current_fingerprint = None
            if file_fingerprint_for is not None:
                try:
                    current_fingerprint = file_fingerprint_for(path)
                except Exception:
                    current_fingerprint = None
            if current_fingerprint is not None:
                for cached_start, cached_end, cached_fingerprint, prior_result in file_read_cache.get(path, []):
                    if (
                        cached_start <= start
                        and cached_end >= end
                        and cached_fingerprint == current_fingerprint
                    ):
                        cached_result = prior_result
                        break
    elif tool_is_cacheable:
        cached_result = tool_result_cache.get(cache_key)
    if cached_result is not None:
        cap_result = copy_capability_result(cached_result)
        cap_status = "success" if cap_result.success else "error"
        cap_preview = cap_result.output_preview or cap_result.message[:500]
        return CapabilityExecutionOutcome(
            pending=pending,
            cap_result=cap_result,
            duration_ms=0,
            status=cap_status,
            output_preview=cap_preview,
            cache_hit=True,
        )

    retry_count = 0
    while True:
        try:
            cap_result = await dispatch(pending.tool_name, pending.args)
        except Exception as exc:
            log.exception(
                "Capability handler %s failed during parallel execution",
                pending.tool_name,
            )
            cap_result = make_error_result(exc)
        cap_retryable = bool(getattr(cap_result, "retryable", False))
        cap_error_type = str(getattr(cap_result, "error_type", "") or "").strip().lower()
        if (
            cap_result.success
            or not cap_retryable
            or retry_count >= max_retryable_retries
        ):
            break
        retry_count += 1
        log.info(
            "Retrying capability %s after retryable failure (%s) attempt %d/%d",
            pending.tool_name,
            cap_error_type or "unknown",
            retry_count,
            max_retryable_retries,
        )

    cap_elapsed = int((asyncio.get_running_loop().time() - cap_start) * 1000)
    cap_status = "success" if cap_result.success else "error"
    cap_preview = cap_result.output_preview or cap_result.message[:500]
    if cap_result.success and tool_is_cacheable:
        if pending.tool_name == "file_read":
            if normalized_range is not None and file_fingerprint_for is not None:
                path, _start, _end = normalized_range
                cap_data = cap_result.data if isinstance(cap_result.data, dict) else {}
                cached_start = cap_data.get("returned_start_line")
                cached_end = cap_data.get("returned_end_line")
                truncated = bool(cap_data.get("truncated"))
                try:
                    current_fingerprint = file_fingerprint_for(path)
                except Exception:
                    current_fingerprint = None
                if (
                    isinstance(cached_start, int)
                    and isinstance(cached_end, int)
                    and current_fingerprint is not None
                    and not truncated
                ):
                    file_read_cache.setdefault(path, []).append(
                        (
                            cached_start,
                            cached_end,
                            current_fingerprint,
                            copy_capability_result(cap_result),
                        )
                    )
        else:
            tool_result_cache[cache_key] = copy_capability_result(cap_result)
    elif cap_result.success and not tool_is_cacheable:
        tool_result_cache.clear()
        file_read_cache.clear()
    return CapabilityExecutionOutcome(
        pending=pending,
        cap_result=cap_result,
        duration_ms=cap_elapsed,
        status=cap_status,
        output_preview=cap_preview,
        cache_hit=False,
    )


async def execute_capability_plan(
    pending_calls: Sequence[PendingCapabilityCall],
    *,
    execute_unique_call: Callable[[PendingCapabilityCall], Awaitable[CapabilityExecutionOutcome]],
    tool_family_for: Callable[[str], str],
    copy_result: Callable[[Any], Any],
) -> list[CapabilityExecutionOutcome]:
    """Execute one planned capability batch with dedupe and family grouping."""
    groups = group_pending_capability_calls(
        pending_calls,
        tool_family_for=tool_family_for,
    )
    unique_results: list[CapabilityExecutionOutcome] = []
    for group in groups:
        if len(group) == 1:
            unique_results.append(await execute_unique_call(group[0]))
        else:
            unique_results.extend(
                await asyncio.gather(*[
                    execute_unique_call(call) for call in group
                ])
            )
    unique_result_by_index: dict[int, CapabilityExecutionOutcome] = {
        pending_calls.index(result_payload.pending): result_payload
        for result_payload in unique_results
    }
    return expand_deduped_capability_results(
        pending_calls,
        unique_results_by_index=unique_result_by_index,
        copy_result=copy_result,
    )


def dataclass_is_instance(value: Any) -> bool:
    """Return True when *value* is a dataclass instance, not a dataclass type."""
    return hasattr(value, "__dataclass_fields__") and not isinstance(value, type)


__all__ = [
    "CapabilityExecutionOutcome",
    "PendingCapabilityCall",
    "annotate_capability_call_plan",
    "capability_cache_key",
    "build_pending_capability_calls",
    "copy_capability_result",
    "execute_capability_call",
    "execute_capability_plan",
    "expand_deduped_capability_results",
    "extract_raw_capability_tool_calls",
    "group_pending_capability_calls",
    "normalize_file_read_range",
    "split_inventory_then_delete_batch",
]
