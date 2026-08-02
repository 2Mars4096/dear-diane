from __future__ import annotations

from dataclasses import dataclass

import pytest

from dan.agent_runtime.capability_calls import (
    CapabilityExecutionOutcome,
    PendingCapabilityCall,
    annotate_capability_call_plan,
    build_pending_capability_calls,
    capability_cache_key,
    copy_capability_result,
    execute_capability_call,
    execute_capability_plan,
    expand_deduped_capability_results,
    extract_raw_capability_tool_calls,
    group_pending_capability_calls,
    normalize_file_read_range,
    split_inventory_then_delete_batch,
)


@dataclass
class _FakeCapResult:
    success: bool = True
    message: str = "ok"
    output_preview: str = ""
    data: object = None
    retryable: bool = False
    error_type: str = ""


def test_extract_raw_capability_tool_calls_filters_non_capability_tools() -> None:
    tool_calls = [
        {"id": "call_1", "function": {"name": "list_directory"}},
        {"id": "call_2", "function": {"name": "plan_graph_mutations"}},
        {"id": "call_3", "function": {"name": "unknown"}},
    ]

    extracted = extract_raw_capability_tool_calls(
        tool_calls,
        is_capability_tool=lambda name: name == "list_directory",
    )

    assert extracted == [tool_calls[0]]


def test_build_pending_capability_calls_preserves_raw_tool_call_ids() -> None:
    pending = build_pending_capability_calls(
        [("list_directory", {"path": "/tmp"})],
        [{"id": "call_1", "function": {"name": "list_directory"}}],
        call_id_factory=lambda: "event_1",
    )

    assert pending == [
        PendingCapabilityCall(
            tool_name="list_directory",
            args={"path": "/tmp"},
            args_preview='{"path": "/tmp"}',
            event_tool_call_id="event_1",
            raw_tool_call={"id": "call_1", "function": {"name": "list_directory"}},
            raw_tool_call_id="call_1",
        )
    ]


def test_split_inventory_then_delete_batch_defers_delete_graph() -> None:
    pending_calls = [
        PendingCapabilityCall(
            tool_name="list_graphs",
            args={},
            args_preview="{}",
            event_tool_call_id="event_1",
        ),
        PendingCapabilityCall(
            tool_name="delete_graph",
            args={"graph_id": "g1"},
            args_preview='{"graph_id": "g1"}',
            event_tool_call_id="event_2",
        ),
    ]

    executed, prompt = split_inventory_then_delete_batch(pending_calls)

    assert [call.tool_name for call in executed] == ["list_graphs"]
    assert prompt is not None
    assert "latest workflow inventory" in prompt


def test_annotate_capability_call_plan_marks_cacheable_duplicates_and_groups() -> None:
    pending_calls = [
        PendingCapabilityCall("file_read", {"path": "a"}, '{"path": "a"}', "event_1"),
        PendingCapabilityCall("file_read", {"path": "a"}, '{"path": "a"}', "event_2"),
        PendingCapabilityCall("web_search", {"q": "x"}, '{"q": "x"}', "event_3"),
    ]

    annotated = annotate_capability_call_plan(
        pending_calls,
        is_cacheable=lambda name: name == "file_read",
        cache_key_for=lambda name, args: f"{name}:{args}",
    )
    groups = group_pending_capability_calls(
        annotated,
        tool_family_for=lambda name: "io" if name == "file_read" else "web",
    )

    assert annotated[0].tool_is_cacheable is True
    assert annotated[0].dedupe_from is None
    assert annotated[1].dedupe_from == 0
    assert annotated[2].tool_is_cacheable is False
    assert [[call.tool_name for call in group] for group in groups] == [
        ["file_read"],
        ["web_search"],
    ]


def test_expand_deduped_capability_results_replays_source_result() -> None:
    pending_calls = annotate_capability_call_plan(
        [
            PendingCapabilityCall("file_read", {"path": "a"}, '{"path": "a"}', "event_1"),
            PendingCapabilityCall("file_read", {"path": "a"}, '{"path": "a"}', "event_2"),
        ],
        is_cacheable=lambda name: True,
        cache_key_for=lambda name, args: f"{name}:{args}",
    )
    unique_results_by_index = {
        0: CapabilityExecutionOutcome(
            pending=pending_calls[0],
            cap_result={"message": "ok"},
            duration_ms=12,
            status="success",
            output_preview="ok",
            cache_hit=False,
        )
    }

    expanded = expand_deduped_capability_results(
        pending_calls,
        unique_results_by_index=unique_results_by_index,
        copy_result=lambda result: dict(result),
    )

    assert len(expanded) == 2
    assert expanded[0].cache_hit is False
    assert expanded[1].cache_hit is True
    assert expanded[1].duration_ms == 0
    assert expanded[1].status == "success"
    assert expanded[1].pending.event_tool_call_id == "event_2"
    assert expanded[1].cap_result == {"message": "ok"}
    assert expanded[1].cap_result is not expanded[0].cap_result


def test_capability_cache_key_and_range_normalization() -> None:
    assert capability_cache_key("file_read", {"path": "a", "start_line": 2}).startswith("file_read:")
    assert normalize_file_read_range({"path": "a", "start_line": "2", "end_line": "4"}) == ("a", 2, 4)
    assert normalize_file_read_range({"path": "a", "grep": "needle"}) is None


def test_copy_capability_result_copies_dataclass_instances() -> None:
    result = _FakeCapResult(message="copied")
    copied = copy_capability_result(result)

    assert copied == result
    assert copied is not result


@pytest.mark.asyncio
async def test_execute_capability_call_uses_cache_before_dispatch() -> None:
    pending = annotate_capability_call_plan(
        [PendingCapabilityCall("web_search", {"query": "a"}, '{"query": "a"}', "event_1")],
        is_cacheable=lambda name: True,
        cache_key_for=capability_cache_key,
    )[0]
    cached = _FakeCapResult(success=True, message="cached contents")

    async def _dispatch(_tool_name: str, _args: object) -> _FakeCapResult:
        raise AssertionError("dispatch should not run on cache hit")

    outcome = await execute_capability_call(
        pending,
        dispatch=_dispatch,
        make_error_result=lambda exc: _FakeCapResult(success=False, message=str(exc)),
        tool_result_cache={pending.cache_key: cached},
        file_read_cache={},
        max_retryable_retries=1,
    )

    assert outcome.cache_hit is True
    assert outcome.status == "success"
    assert outcome.cap_result.message == "cached contents"


@pytest.mark.asyncio
async def test_execute_capability_call_reuses_file_read_only_when_fingerprint_matches() -> None:
    pending = annotate_capability_call_plan(
        [PendingCapabilityCall("file_read", {"path": "a"}, '{"path": "a"}', "event_1")],
        is_cacheable=lambda name: True,
        cache_key_for=capability_cache_key,
    )[0]
    cached = _FakeCapResult(success=True, message="cached contents")

    async def _dispatch(_tool_name: str, _args: object) -> _FakeCapResult:
        raise AssertionError("dispatch should not run on validated file_read cache hit")

    outcome = await execute_capability_call(
        pending,
        dispatch=_dispatch,
        make_error_result=lambda exc: _FakeCapResult(success=False, message=str(exc)),
        tool_result_cache={pending.cache_key: _FakeCapResult(success=True, message="stale exact cache")},
        file_read_cache={"a": [(1, float("inf"), ("sig", 1), cached)]},
        file_fingerprint_for=lambda path: ("sig", 1),
        max_retryable_retries=1,
    )

    assert outcome.cache_hit is True
    assert outcome.cap_result.message == "cached contents"


@pytest.mark.asyncio
async def test_execute_capability_call_ignores_stale_file_read_cache() -> None:
    pending = annotate_capability_call_plan(
        [PendingCapabilityCall("file_read", {"path": "a"}, '{"path": "a"}', "event_1")],
        is_cacheable=lambda name: True,
        cache_key_for=capability_cache_key,
    )[0]
    calls = 0

    async def _dispatch(_tool_name: str, _args: object) -> _FakeCapResult:
        nonlocal calls
        calls += 1
        return _FakeCapResult(
            success=True,
            message="fresh contents",
            data={
                "returned_start_line": 1,
                "returned_end_line": 3,
                "truncated": False,
            },
        )

    file_read_cache: dict[str, list[tuple[int, float, object, _FakeCapResult]]] = {
        "a": [(1, float("inf"), ("old", 1), _FakeCapResult(success=True, message="stale"))]
    }
    outcome = await execute_capability_call(
        pending,
        dispatch=_dispatch,
        make_error_result=lambda exc: _FakeCapResult(success=False, message=str(exc)),
        tool_result_cache={pending.cache_key: _FakeCapResult(success=True, message="stale exact cache")},
        file_read_cache=file_read_cache,
        file_fingerprint_for=lambda path: ("new", 2),
        max_retryable_retries=1,
    )

    assert calls == 1
    assert outcome.cache_hit is False
    assert outcome.cap_result.message == "fresh contents"
    assert file_read_cache["a"][-1][2] == ("new", 2)


@pytest.mark.asyncio
async def test_execute_capability_call_retries_retryable_failure_once() -> None:
    pending = annotate_capability_call_plan(
        [PendingCapabilityCall("file_read", {"path": "a"}, '{"path": "a"}', "event_1")],
        is_cacheable=lambda name: True,
        cache_key_for=capability_cache_key,
    )[0]
    attempts = 0

    async def _dispatch(_tool_name: str, _args: object) -> _FakeCapResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _FakeCapResult(
                success=False,
                message="temporary timeout",
                retryable=True,
                error_type="timeout",
            )
        return _FakeCapResult(success=True, message="recovered", output_preview="recovered")

    tool_cache: dict[str, object] = {}
    outcome = await execute_capability_call(
        pending,
        dispatch=_dispatch,
        make_error_result=lambda exc: _FakeCapResult(success=False, message=str(exc)),
        tool_result_cache=tool_cache,
        file_read_cache={},
        max_retryable_retries=1,
    )

    assert attempts == 2
    assert outcome.cache_hit is False
    assert outcome.status == "success"
    assert outcome.cap_result.message == "recovered"
    assert pending.cache_key not in tool_cache


@pytest.mark.asyncio
async def test_execute_capability_plan_groups_and_replays_deduped_results() -> None:
    pending_calls = annotate_capability_call_plan(
        [
            PendingCapabilityCall("file_read", {"path": "a"}, '{"path": "a"}', "event_1"),
            PendingCapabilityCall("file_read", {"path": "a"}, '{"path": "a"}', "event_2"),
            PendingCapabilityCall("web_search", {"q": "x"}, '{"q": "x"}', "event_3"),
        ],
        is_cacheable=lambda name: name == "file_read",
        cache_key_for=capability_cache_key,
    )
    executed: list[str] = []

    async def _execute_unique(call: PendingCapabilityCall) -> CapabilityExecutionOutcome:
        executed.append(call.event_tool_call_id)
        return CapabilityExecutionOutcome(
            pending=call,
            cap_result={"tool": call.tool_name},
            duration_ms=5,
            status="success",
            output_preview=call.tool_name,
            cache_hit=False,
        )

    outcomes = await execute_capability_plan(
        pending_calls,
        execute_unique_call=_execute_unique,
        tool_family_for=lambda name: "io" if name == "file_read" else "web",
        copy_result=lambda result: dict(result),
    )

    assert executed == ["event_1", "event_3"]
    assert len(outcomes) == 3
    assert outcomes[1].cache_hit is True
    assert outcomes[1].pending.event_tool_call_id == "event_2"
