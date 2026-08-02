from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import time

import pytest

import dan.worker.organisms.local_runtime as local_runtime_module
from dan.providers import CompletionResult, StreamChunk
from dan.providers.openai_provider import OpenAIProvider
from dan.worker.core.contracts import OutputContract
from dan.worker.core.interfaces import CompletionRequest
from dan.worker.organisms.local_runtime import (
    LocalOrganismToolRuntime,
    ToolLoopCompletionProvider,
    attach_local_tooling_to_coding_organism,
    attach_local_tooling_to_reference_organism,
    available_local_organism_tools,
)
from dan.worker.organisms.coding_execution import coding_execution_organism
from dan.worker.organisms.project_execution import project_execution_reference_organism


def _latest_tool_round(messages: list[dict[str, object]]) -> tuple[list[str], list[dict[str, object]]]:
    assistant_indexes = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, dict)
        and message.get("role") == "assistant"
        and message.get("tool_calls")
    ]
    assert assistant_indexes
    assistant_index = assistant_indexes[-1]
    assistant_message = messages[assistant_index]
    tool_calls = assistant_message.get("tool_calls")
    assert isinstance(tool_calls, list)
    tool_call_ids = [
        str(call.get("id") or "")
        for call in tool_calls
        if isinstance(call, dict) and str(call.get("id") or "").strip()
    ]
    tool_messages: list[dict[str, object]] = []
    for message in messages[assistant_index + 1 :]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "assistant":
            break
        if role == "user" and tool_messages:
            break
        if role == "tool":
            tool_messages.append(message)
    return tool_call_ids, tool_messages


def test_completion_request_user_content_includes_image_attachments(tmp_path: Path) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    request = CompletionRequest(
        model="test",
        system_prompt="system",
        user_prompt="Review the screenshot.",
        metadata={"image_attachments": [{"kind": "image", "local_path": str(image)}]},
    )

    content = local_runtime_module._completion_request_user_content(request)

    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "Review the screenshot." in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def _event_names(events: list[dict[str, object]]) -> list[str]:
    return [
        str(event.get("event") or "")
        for event in events
        if event.get("event")
        not in {"context.capsule.emitted", "context.readiness.emitted"}
    ]


def _first_event(events: list[dict[str, object]], event_name: str) -> dict[str, object]:
    return next(event for event in events if event.get("event") == event_name)


class _FakeToolLoopProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
                "thinking": kwargs.get("thinking"),
            }
        )
        if len(self.calls) == 1:
            tool_call = {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "notes/live-organism.txt",
                            "content": "stem-cell runtime\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )

        last_message = messages[-1]
        assert last_message["role"] == "tool"
        assert "bytes_written" in str(last_message["content"])
        return CompletionResult(
            text=json.dumps({"candidate_id": "live-candidate"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "live-candidate"}),
            },
        )


class _ManyRoundToolLoopProvider:
    def __init__(self, *, rounds: int) -> None:
        self.rounds = rounds
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index <= self.rounds:
            tool_call = {
                "id": f"call-{call_index}",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "notes/existing.txt"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )

        return CompletionResult(
            text=json.dumps({"candidate_id": "after-many-rounds"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "after-many-rounds"}),
            },
        )


class _BudgetAuditExtensionProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if kwargs.get("tools") is None:
            assert "budget auditor" in str(messages[0]["content"]).lower()
            return CompletionResult(
                text=json.dumps(
                    {
                        "approved": True,
                        "extra_rounds": 1,
                        "extra_tool_calls": 2,
                        "reason": "The worker has concrete file-read progress and one more read should finish grounding.",
                    }
                ),
                model=model,
                raw_assistant_message={
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "approved": True,
                            "extra_rounds": 1,
                            "extra_tool_calls": 2,
                            "reason": "The worker has concrete file-read progress and one more read should finish grounding.",
                        }
                    ),
                },
            )
        worker_calls = [call for call in self.calls if call.get("tools") is not None]
        if len(worker_calls) == 1:
            tool_calls = [
                {
                    "id": "call-read-a",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "notes/a.txt"}),
                    },
                },
                {
                    "id": "call-read-b",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "notes/b.txt"}),
                    },
                },
            ]
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
            )
        return CompletionResult(
            text=json.dumps({"candidate_id": "budget-extended"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "budget-extended"}),
            },
        )


class _RepeatedFileReadThenSuccessProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index <= 2:
            tool_call = {
                "id": f"call-read-{call_index}",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "notes/existing.txt"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )

        return CompletionResult(
            text=json.dumps({"candidate_id": "cached-file-read-complete"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "cached-file-read-complete"}),
            },
        )


class _ReadWriteReadThenSuccessProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index == 1:
            tool_call = {
                "id": "call-read-before",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "notes/existing.txt"}),
                },
            }
        elif call_index == 2:
            tool_call = {
                "id": "call-write",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {"path": "notes/existing.txt", "content": "updated context\n"}
                    ),
                },
            }
        elif call_index == 3:
            tool_call = {
                "id": "call-read-after",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "notes/existing.txt"}),
                },
            }
        else:
            return CompletionResult(
                text=json.dumps({"candidate_id": "refreshed-after-write"}),
                model=model,
                raw_assistant_message={
                    "role": "assistant",
                    "content": json.dumps({"candidate_id": "refreshed-after-write"}),
                },
            )
        return CompletionResult(
            text="",
            model=model,
            tool_calls=[tool_call],
            raw_assistant_message={
                "role": "assistant",
                "content": None,
                "tool_calls": [tool_call],
            },
        )


class _PostwriteSoftBudgetContinuationProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        tool_names = [
            str(tool.get("function", {}).get("name") or "")
            for tool in (kwargs.get("tools") or [])
            if isinstance(tool, dict)
        ]
        if call_index <= 5:
            tool_call = {
                "id": f"call-read-{call_index}",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": f"notes/input-{call_index}.txt"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 6:
            assert any(
                "Stop auditing and make the first concrete project write now"
                in str(message.get("content") or "")
                for message in messages
                if isinstance(message, dict) and message.get("role") == "user"
            )
            assert "file_write" in tool_names
            tool_call = {
                "id": "call-write-first",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "notes/report.md",
                            "content": "# Report\n\nInitial section.\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 7:
            assert "file_write" in tool_names
            assert not any(
                "stop using tools and return"
                in str(message.get("content") or "").lower()
                for message in messages
                if isinstance(message, dict) and message.get("role") == "user"
            )
            tool_call = {
                "id": "call-write-second",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "notes/report.md",
                            "mode": "append",
                            "content": "\n## Valuation\n\nExpanded analysis.\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        return CompletionResult(
            text=json.dumps(
                {
                    "candidate_id": "postwrite-soft-budget-continued",
                    "change_summary": ["wrote the report in multiple post-write chunks"],
                    "target_files": ["notes/report.md"],
                    "test_plan": ["inspect notes/report.md"],
                    "risks": [],
                },
                sort_keys=True,
            ),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "postwrite-soft-budget-continued"}),
            },
        )


class _MissingRequiredToolArgThenSuccessProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index == 1:
            tool_call = {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 2:
            assert messages[-1]["role"] == "user"
            assert "Tool correction:" in str(messages[-1]["content"])
            assert "`file_read`" in str(messages[-1]["content"])
            assert "path" in str(messages[-1]["content"])
            tool_call = {
                "id": "call-2",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "notes/existing.txt"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        assert messages[-1]["role"] == "tool"
        assert "\"ok\": true" in str(messages[-1]["content"])
        return CompletionResult(
            text=json.dumps({"candidate_id": "recovered-after-nudge"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "recovered-after-nudge"}),
            },
        )


class _EmptyWebSearchArgsThenSuccessProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index == 1:
            tool_call = {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "web_search",
                    "arguments": json.dumps({}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 2:
            assert messages[-1]["role"] == "user"
            assert "Tool correction:" in str(messages[-1]["content"])
            assert "`web_search`" in str(messages[-1]["content"])
            assert "query" in str(messages[-1]["content"])
            assert "url" in str(messages[-1]["content"])
            assert '{"query":"Brent crude oil price April 2026"}' in str(messages[-1]["content"])
            assert "Do not send `{}`" in str(messages[-1]["content"])
            tool_call = {
                "id": "call-2",
                "type": "function",
                "function": {
                    "name": "web_search",
                    "arguments": json.dumps({"query": "Brent crude oil price April 2026"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        assert messages[-1]["role"] == "tool"
        assert "\"ok\": true" in str(messages[-1]["content"])
        return CompletionResult(
            text=json.dumps({"candidate_id": "web-search-recovered-after-nudge"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "web-search-recovered-after-nudge"}),
            },
        )


class _RepeatedDiscoveryThenSuccessProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index <= 2:
            tool_call = {
                "id": f"call-{call_index}",
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "arguments": json.dumps({}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        assert messages[-1]["role"] == "user"
        assert "already executed `list_directory`" in str(messages[-1]["content"])
        return CompletionResult(
            text=json.dumps({"candidate_id": "recovered-after-duplicate-nudge"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "recovered-after-duplicate-nudge"}),
            },
        )


class _ManyInvalidWebSearchCallsThenSuccessProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index == 1:
            tool_calls = []
            for idx in range(1, 4):
                tool_calls.append(
                    {
                        "id": f"call-{idx}",
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "arguments": json.dumps({}),
                        },
                    }
                )
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
            )
        if call_index == 2:
            tool_call_ids, tool_messages = _latest_tool_round(messages)
            assert tool_call_ids == ["call-1", "call-2", "call-3"]
            assert [str(message.get("tool_call_id") or "") for message in tool_messages] == tool_call_ids
            assert "\"ok\": false" in str(tool_messages[0]["content"])
            assert "tool_arguments_invalid:" in str(tool_messages[0]["content"])
            assert "tool_call_skipped:invalid_tool_argument_nudge" in str(tool_messages[1]["content"])
            assert "tool_call_skipped:invalid_tool_argument_nudge" in str(tool_messages[2]["content"])
            assert messages[-1]["role"] == "user"
            assert "Do not send `{}`" in str(messages[-1]["content"])
            tool_call = {
                "id": "call-4",
                "type": "function",
                "function": {
                    "name": "web_search",
                    "arguments": json.dumps({"query": "Qinhuangdao thermal coal price April 2026"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        return CompletionResult(
            text=json.dumps({"candidate_id": "web-search-batch-recovered"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "web-search-batch-recovered"}),
            },
        )


class _CaptureToolSchemaProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        return CompletionResult(
            text=json.dumps({"candidate_id": "captured-tool-schema"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "captured-tool-schema"}),
            },
        )


class _ValidatorFileWriteThenSuccessProvider:
    def __init__(self, *, external_path: Path) -> None:
        self.external_path = external_path
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        tools = kwargs.get("tools") or []
        tool_names = [
            str(tool.get("function", {}).get("name") or "")
            for tool in tools
            if isinstance(tool, dict)
        ]
        if call_index == 1:
            assert "file_write" not in tool_names
            assert "file_edit" not in tool_names
            assert "shell_command" not in tool_names
            tool_call = {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": str(self.external_path),
                            "content": "print('validator helper')\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )

        assert messages[-1]["role"] == "user"
        assert "`file_write` is temporarily disabled" in str(messages[-1]["content"])
        payload = {
            "passed": True,
            "overall_score": 0.94,
            "dimension_scores": {"correctness": 0.94},
            "repair_brief": "",
            "missing_requirements": [],
            "comparison_note": "The candidate is acceptable from the available evidence.",
        }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps(payload),
            },
        )


class _RepeatedInvalidToolThenDisabledProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index <= 2:
            tool_call = {
                "id": f"call-{call_index}",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        assert messages[-1]["role"] == "user"
        assert "temporarily disabled" in str(messages[-1]["content"])
        assert "`file_read`" in str(messages[-1]["content"])
        assert kwargs.get("tools") is None
        return CompletionResult(
            text=json.dumps({"candidate_id": "recovered-after-tool-disable"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "recovered-after-tool-disable"}),
            },
        )


class _RepeatedInvalidFileEditThenDisabledProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index <= 2:
            tool_call = {
                "id": f"call-{call_index}",
                "type": "function",
                "function": {
                    "name": "file_edit",
                    "arguments": json.dumps(
                        {
                            "path": "notes.txt",
                            "start_line": 1,
                            "mode": "insert_before",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        assert messages[-1]["role"] == "user"
        assert "temporarily disabled" in str(messages[-1]["content"])
        assert "`file_edit`" in str(messages[-1]["content"])
        assert kwargs.get("tools") is None
        return CompletionResult(
            text=json.dumps({"candidate_id": "file-edit-recovered-after-tool-disable"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "file-edit-recovered-after-tool-disable"}),
            },
        )


class _BatchedFileEditMissingAnchorThenSuccessProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index == 1:
            tool_call = {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "file_edit",
                    "arguments": json.dumps(
                        {
                            "path": "notes.txt",
                            "edits": [{"content": "gamma\n", "mode": "replace"}],
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 2:
            assert messages[-1]["role"] == "user"
            nudge = str(messages[-1]["content"])
            assert "Tool correction:" in nudge
            assert "`file_edit`" in nudge
            assert "Every item in `edits` must include `start_line`" in nudge
            assert "`old_string` plus `new_string`" in nudge
            assert "call `file_read` first" in nudge
            tool_call = {
                "id": "call-2",
                "type": "function",
                "function": {
                    "name": "file_edit",
                    "arguments": json.dumps(
                        {
                            "path": "notes.txt",
                            "edits": [
                                {
                                    "start_line": 2,
                                    "end_line": 2,
                                    "content": "gamma\n",
                                    "mode": "replace",
                                }
                            ],
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        assert messages[-1]["role"] == "tool"
        assert "\"ok\": true" in str(messages[-1]["content"])
        return CompletionResult(
            text=json.dumps({"candidate_id": "file-edit-recovered-after-batch-anchor-nudge"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "file-edit-recovered-after-batch-anchor-nudge"}),
            },
        )


class _RawJsonishFileEditThenSuccessProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index == 1:
            raw_arguments = (
                '{"path":"notes.txt","start_line":2,"end_line":2,'
                '"content":"gamma\n","mode":"replace"}'
            )
            tool_call = {
                "id": "call-raw-jsonish",
                "type": "function",
                "function": {
                    "name": "file_edit",
                    "arguments": raw_arguments,
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        assert messages[-1]["role"] == "tool"
        assert "\"ok\": true" in str(messages[-1]["content"])
        return CompletionResult(
            text=json.dumps({"candidate_id": "file-edit-recovered-from-jsonish-raw"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "file-edit-recovered-from-jsonish-raw"}),
            },
        )


class _ShrinkingRepairOverwriteThenFileEditProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index == 1:
            tool_call = {
                "id": "call-shrink",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "report.md",
                            "content": "# Baseline\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 2:
            assert messages[-1]["role"] == "user"
            nudge = str(messages[-1]["content"])
            assert "Repair policy blocked" in nudge
            assert "shrink the existing artifact" in nudge
            assert "turning the artifact into a smaller baseline" in nudge
            tool_call = {
                "id": "call-expand",
                "type": "function",
                "function": {
                    "name": "file_edit",
                    "arguments": json.dumps(
                        {
                            "path": "report.md",
                            "start_line": 2,
                            "end_line": 2,
                            "content": "Expanded valuation and risk discussion.\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        assert messages[-1]["role"] == "tool"
        assert "\"ok\": true" in str(messages[-1]["content"])
        return CompletionResult(
            text=json.dumps({"candidate_id": "repair-policy-recovered"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "repair-policy-recovered"}),
            },
        )


class _InvalidFileWriteThenSuccessProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index == 1:
            tool_call = {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "module.py",
                            "content": "def alpha(\n    return 1\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 2:
            assert messages[-1]["role"] == "user"
            correction_text = str(messages[-1]["content"])
            assert "Tool correction:" in correction_text or "Source-structure guard:" in correction_text
            assert "`file_write`" in correction_text
            assert (
                "syntactically invalid" in correction_text
                or "invalid content likely caused" in correction_text
            )
            tool_call = {
                "id": "call-2",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "module.py",
                            "content": "def alpha():\n    return 2\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        assert messages[-1]["role"] == "tool"
        assert "\"ok\": true" in str(messages[-1]["content"])
        return CompletionResult(
            text=json.dumps({"candidate_id": "file-write-recovered-after-nudge"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "file-write-recovered-after-nudge"}),
            },
        )


class _WriteStageRepeatedInvalidFileWriteRecoveryProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        tools = kwargs.get("tools") or []
        tool_names = [
            str(tool.get("function", {}).get("name") or "")
            for tool in tools
            if isinstance(tool, dict)
        ]
        if call_index == 1:
            assert "file_write" in tool_names
            tool_call = {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps({}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 2:
            assert messages[-1]["role"] == "user"
            assert "Tool correction:" in str(messages[-1]["content"])
            assert "`file_write`" in str(messages[-1]["content"])
            assert "temporarily disabled" not in str(messages[-1]["content"])
            tool_call = {
                "id": "call-2",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps({}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 3:
            user_messages = [
                str(message.get("content") or "")
                for message in messages
                if isinstance(message, dict) and message.get("role") == "user"
            ]
            assert any(
                "Tool correction:" in content and "`file_write`" in content
                for content in user_messages
            )
            assert not any(
                "temporarily disabled" in content and "`file_write`" in content
                for content in user_messages
            )
            assert "file_write" in tool_names
            tool_call = {
                "id": "call-3",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "module.py",
                            "content": "def alpha():\n    return 3\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        assert messages[-1]["role"] == "tool"
        assert "\"ok\": true" in str(messages[-1]["content"])
        payload = {
            "candidate_fragment": {"module.py": "def alpha():\n    return 3\n"},
            "change_summary": "Recovered from malformed write calls and wrote the module.",
            "target_files": ["module.py"],
            "test_plan": ["import module.alpha and confirm it returns 3"],
            "risks": [],
        }
        return CompletionResult(
            text=json.dumps(payload, sort_keys=True),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps(payload, sort_keys=True),
            },
        )


class _ExistingFileWriteDownshiftRecoveryProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        tools = kwargs.get("tools") or []
        tool_names = [
            str(tool.get("function", {}).get("name") or "")
            for tool in tools
            if isinstance(tool, dict)
        ]
        if call_index == 1:
            assert "file_write" in tool_names
            assert "file_edit" in tool_names
            tool_call = {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": (
                        '{"path":"module.py","content":"def alpha():\\n'
                        '    return 10\\n'
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                finish_reason="length",
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 2:
            user_messages = [
                str(message.get("content") or "")
                for message in messages
                if isinstance(message, dict) and message.get("role") == "user"
            ]
            assert any(
                "Tool correction:" in content and "`file_write`" in content
                for content in user_messages
            )
            assert any(
                "Downshift now: use `file_edit`" in content and "`module.py`" in content
                for content in user_messages
            )
            tool_call = {
                "id": "call-2",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "module.py",
                            "content": "def alpha():\n    return 11\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 3:
            user_messages = [
                str(message.get("content") or "")
                for message in messages
                if isinstance(message, dict) and message.get("role") == "user"
            ]
            assert any(
                "`file_write` to existing file `module.py` is blocked" in content
                for content in user_messages
            )
            assert "file_edit" in tool_names
            tool_call = {
                "id": "call-3",
                "type": "function",
                "function": {
                    "name": "file_edit",
                    "arguments": json.dumps(
                        {
                            "path": "module.py",
                            "start_line": 1,
                            "end_line": 2,
                            "content": "def alpha():\n    return 12\n",
                            "mode": "replace",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        assert messages[-1]["role"] == "tool"
        assert "\"ok\": true" in str(messages[-1]["content"])
        payload = {
            "candidate_fragment": {"module.py": "def alpha():\n    return 12\n"},
            "change_summary": "Recovered from a truncated whole-file overwrite by switching to file_edit.",
            "target_files": ["module.py"],
            "test_plan": ["import module.alpha and confirm it returns 12"],
            "risks": [],
        }
        return CompletionResult(
            text=json.dumps(payload, sort_keys=True),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps(payload, sort_keys=True),
            },
        )


class _ReadOnlyFinalizeProvider:
    def __init__(self, *, workspace_root, second_tool_name: str, second_tool_arguments: dict[str, object]) -> None:
        self.workspace_root = workspace_root
        self.second_tool_name = second_tool_name
        self.second_tool_arguments = second_tool_arguments
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        tools = kwargs.get("tools")
        if call_index == 1:
            assert tools
            tool_call = {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "arguments": json.dumps({"path": str(self.workspace_root)}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 2:
            assert tools
            tool_call = {
                "id": "call-2",
                "type": "function",
                "function": {
                    "name": self.second_tool_name,
                    "arguments": json.dumps(self.second_tool_arguments),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        assert not tools
        assert messages[-1]["role"] == "user"
        assert "Controller note: stop using tools" in str(messages[-1]["content"])
        payload = {
            "candidate_fragment": {"pyproject.toml": "[project]\nname = \"demo\"\n"},
            "change_summary": "Propose the initial scaffold for later aggregation.",
            "target_files": ["pyproject.toml"],
            "test_plan": ["Add focused scaffold tests later."],
            "risks": ["This worker is read-only and cannot materialize files directly."],
        }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps(payload),
            },
        )


class _ResearchFinalizeProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        tools = kwargs.get("tools")
        if call_index <= 2:
            assert tools
            tool_call = {
                "id": f"call-{call_index}",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "notes/evidence.txt"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        assert not tools
        assert messages[-1]["role"] == "user"
        assert "compact evidence note" in str(messages[-1]["content"])
        payload = {
            "findings": ["Reader gathered a bounded grounded note."],
            "evidence_refs": ["notes/evidence.txt"],
            "contradictions": [],
            "open_questions": [],
            "reasoning_notes": ["The local evidence file provided enough material to stop searching."],
            "follow_up_queries": [],
        }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps(payload),
            },
        )


class _PromptFilterThenSuccessProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        if len(self.calls) == 1:
            raise RuntimeError(
                "BadRequestError: request was rejected because it was considered high risk "
                "(type: content_filter, param: prompt)"
            )
        assert kwargs.get("tools") is None
        assert messages[0]["role"] == "system"
        assert "Provider safety fallback mode" in str(messages[0]["content"])
        payload = {
            "findings": ["Blocked by provider prompt safety filtering."],
            "evidence_summary": ["No substantive synthesis was returned on the first call."],
            "quality_gates": [
                {
                    "gate": "final_status",
                    "status": "warn",
                    "summary": "Recovered through the provider-safety retry path.",
                }
            ],
            "report_readiness": "provisional",
            "recommended_change": "No substantive recommendation generated.",
        }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps(payload),
            },
        )


class _PromptFilterAlwaysProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        raise RuntimeError(
            "BadRequestError: request was rejected because it was considered high risk "
            "(type: content_filter, param: prompt)"
        )


class _SlowProvider:
    def __init__(self, *, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        await asyncio.sleep(self.delay_seconds)
        return CompletionResult(
            text=json.dumps({"candidate_fragment": {"README.md": "slow"}}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_fragment": {"README.md": "slow"}}),
            },
        )


class _CancellationIgnoringSlowProvider:
    def __init__(self, *, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.calls: list[dict[str, object]] = []
        self.cancelled_calls = 0

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        try:
            await asyncio.sleep(self.delay_seconds)
        except asyncio.CancelledError:
            self.cancelled_calls += 1
            await asyncio.sleep(self.delay_seconds)
        return CompletionResult(
            text=json.dumps({"candidate_fragment": {"README.md": "slow"}}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_fragment": {"README.md": "slow"}}),
            },
        )


class _WriteThenSlowProvider:
    def __init__(self, *, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        if len(self.calls) == 1:
            tool_call = {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "src/demo.py",
                            "content": "print('demo')\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        await asyncio.sleep(self.delay_seconds)
        return CompletionResult(
            text="",
            model=model,
            raw_assistant_message={"role": "assistant", "content": ""},
        )


class _WriteStageTimeoutRecoveryProvider:
    def __init__(self, *, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index == 1:
            tool_calls = [
                {
                    "id": "call-read-1",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/demo.py"}),
                    },
                },
                {
                    "id": "call-read-2",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/demo.py"}),
                    },
                },
                {
                    "id": "call-read-3",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/demo.py"}),
                    },
                },
            ]
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
            )
        if call_index == 2:
            tools = kwargs.get("tools") or []
            assert [
                str(tool.get("function", {}).get("name") or "")
                for tool in tools
                if isinstance(tool, dict)
            ] == ["file_read", "file_write"]
            tool_call = {
                "id": "call-recovery-read",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "src/demo.py", "start_line": 1, "end_line": 1}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 3:
            await asyncio.sleep(self.delay_seconds)
            return CompletionResult(
                text="",
                model=model,
                raw_assistant_message={"role": "assistant", "content": ""},
            )
        if call_index == 4:
            tools = kwargs.get("tools") or []
            assert [
                str(tool.get("function", {}).get("name") or "")
                for tool in tools
                if isinstance(tool, dict)
            ] == ["file_write"]
            tool_call = {
                "id": "call-write",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "src/demo.py",
                            "content": "print('patched')\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        payload = {
            "candidate_fragment": {},
            "change_summary": "Patched src/demo.py after timeout recovery.",
            "target_files": ["src/demo.py"],
            "test_plan": ["Inspect src/demo.py."],
            "risks": [],
        }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps(payload),
            },
        )


class _ReadTwiceThenSlowProvider:
    def __init__(self, *, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        if len(self.calls) == 1:
            tool_calls = [
                {
                    "id": "call-read-a",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/a.py"}),
                    },
                },
                {
                    "id": "call-read-b",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/b.py"}),
                    },
                },
            ]
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
            )
        await asyncio.sleep(self.delay_seconds)
        return CompletionResult(
            text="",
            model=model,
            raw_assistant_message={"role": "assistant", "content": ""},
        )


class _ExclusiveOwnerReadThenWriteProvider:
    def __init__(
        self,
        *,
        extra_read_path: str | None = None,
        expected_full_read_marker: str | None = None,
        expected_write_tool: str = "file_edit",
    ) -> None:
        self.extra_read_path = extra_read_path
        self.expected_full_read_marker = expected_full_read_marker
        self.expected_write_tool = expected_write_tool
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index == 1:
            tool_call = {
                "id": "call-read-owned-file",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "src/demo.py"}),
                },
            }
            tool_calls = [tool_call]
            if self.extra_read_path is not None:
                tool_calls.append(
                    {
                        "id": "call-read-extra-file",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps({"path": self.extra_read_path}),
                        },
                    }
                )
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
            )
        if call_index == 2:
            tools = kwargs.get("tools") or []
            assert [
                str(tool.get("function", {}).get("name") or "")
                for tool in tools
                if isinstance(tool, dict)
            ] == [self.expected_write_tool]
            if self.expected_full_read_marker is not None:
                tool_messages = [
                    message
                    for message in messages
                    if isinstance(message, dict) and message.get("role") == "tool"
                ]
                assert tool_messages
                prompt_payload = json.loads(str(tool_messages[-1]["content"]))
                assert self.expected_full_read_marker in prompt_payload["result"]["content"]
                assert "prompt_payload_compacted" not in prompt_payload
            assert any(
                "exclusive write owner after first read" in str(message.get("content") or "").lower()
                for message in messages
                if isinstance(message, dict) and message.get("role") == "user"
            )
            tool_name = self.expected_write_tool
            arguments = (
                {
                    "path": "src/demo.py",
                    "start_line": 1,
                    "end_line": 1,
                    "content": "print('patched')\n",
                }
                if tool_name == "file_edit"
                else {
                    "path": "src/demo.py",
                    "content": "print('patched')\n",
                }
            )
            tool_call = {
                "id": "call-write-owned-file",
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(arguments),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        payload = {
            "candidate_fragment": {"src/demo.py": "print('patched')\n"},
            "change_summary": "Patched the exclusive owner file.",
            "target_files": ["src/demo.py"],
            "test_plan": ["Inspect src/demo.py."],
            "risks": [],
        }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps(payload),
            },
        )


class _ExclusiveOwnerTimeoutRecoveryProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index == 1:
            tool_call = {
                "id": "call-read-owned-file",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "src/demo.py"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 2:
            await asyncio.sleep(0.05)
            return CompletionResult(
                text="",
                model=model,
                raw_assistant_message={"role": "assistant", "content": ""},
            )
        if call_index == 3:
            tools = kwargs.get("tools") or []
            assert [
                str(tool.get("function", {}).get("name") or "")
                for tool in tools
                if isinstance(tool, dict)
            ] == ["file_edit"]
            assert any(
                "recovery note: the previous provider call timed out" in str(message.get("content") or "").lower()
                for message in messages
                if isinstance(message, dict) and message.get("role") == "user"
            )
            tool_call = {
                "id": "call-write-owned-file",
                "type": "function",
                "function": {
                    "name": "file_edit",
                    "arguments": json.dumps(
                        {
                            "path": "src/demo.py",
                            "start_line": 1,
                            "end_line": 1,
                            "content": "print('patched-after-timeout')\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        payload = {
            "candidate_fragment": {"src/demo.py": "print('patched-after-timeout')\n"},
            "change_summary": "Patched the exclusive owner file after timeout recovery.",
            "target_files": ["src/demo.py"],
            "test_plan": ["Inspect src/demo.py."],
            "risks": [],
        }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps(payload),
            },
        )


class _WriteStageFirstWriteNudgeProvider:
    def __init__(self, *, workspace_root) -> None:
        self.workspace_root = workspace_root
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        if len(self.calls) == 1:
            tool_call = {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "arguments": json.dumps({"path": str(self.workspace_root)}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )

        tools = kwargs.get("tools")
        assert tools
        assert [
            tool.get("function", {}).get("name")
            for tool in tools
            if isinstance(tool, dict)
        ] == ["file_write"]
        if len(self.calls) == 2:
            assert messages[-1]["role"] == "user"
            assert "make the first concrete project write now" in str(messages[-1]["content"])
            tool_call = {
                "id": "call-2",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "pyproject.toml",
                            "content": "[project]\nname = 'termboard'\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )

        assert len(self.calls) == 3
        assert messages[-1]["role"] == "tool"
        assert "\"ok\": true" in str(messages[-1]["content"])
        payload = {
            "candidate_id": "candidate-after-nudge",
            "change_summary": "Create the first bounded project files now that the empty workspace is confirmed.",
            "target_files": ["pyproject.toml"],
            "test_plan": ["Add focused board-model tests next."],
            "risks": ["Only the initial project slice is covered."],
        }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps(payload),
            },
        )


class _WriteStageStalledAnalysisNudgeProvider:
    def __init__(self, *, workspace_root, output_kind: str = "candidate_id") -> None:
        self.workspace_root = workspace_root
        self.output_kind = output_kind
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        if len(self.calls) == 1:
            tool_calls = [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/rst.py"}),
                    },
                },
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/fixedwidth.py"}),
                    },
                },
                {
                    "id": "call-3",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/core.py"}),
                    },
                },
            ]
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
            )
        tools = kwargs.get("tools")
        assert tools
        tool_names = [
            tool.get("function", {}).get("name")
            for tool in tools
            if isinstance(tool, dict)
        ]
        assert tool_names in (
            ["file_read", "file_write"],
            ["file_read", "file_write", "shell_command"],
        )
        assert messages[-1]["role"] == "user"
        assert "stalled analysis before first write" in str(messages[-1]["content"])
        if self.output_kind == "candidate_fragment":
            payload = {
                "candidate_fragment": {"src/rst.py": "class RST: pass\n"},
                "change_summary": "Stop auditing and materialize the bounded RST worker patch now.",
                "target_files": ["src/rst.py"],
                "test_plan": ["Run the focused ASCII writer tests after the first edit."],
                "risks": ["The worker contribution is still the first bounded patch slice."],
            }
        else:
            payload = {
                "candidate_id": "candidate-after-stalled-analysis-nudge",
                "change_summary": "Stop auditing and materialize the bounded RST writer patch now.",
                "target_files": ["src/rst.py"],
                "test_plan": ["Run the focused ASCII writer tests after the first edit."],
                "risks": ["The candidate is still the first bounded patch slice."],
            }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps(payload),
            },
        )


class _WriteStageShellStalledAnalysisNudgeProvider:
    def __init__(self, *, output_kind: str = "candidate_fragment") -> None:
        self.output_kind = output_kind
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        if len(self.calls) == 1:
            tool_calls = [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "shell_command",
                        "arguments": json.dumps({"command": "cat src/rst.py"}),
                    },
                },
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {
                        "name": "shell_command",
                        "arguments": json.dumps({"command": "cat src/fixedwidth.py"}),
                    },
                },
            ]
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
            )
        if len(self.calls) == 2:
            tool_calls = [
                {
                    "id": "call-3",
                    "type": "function",
                    "function": {
                        "name": "shell_command",
                        "arguments": json.dumps({"command": "cat src/core.py"}),
                    },
                },
                {
                    "id": "call-4",
                    "type": "function",
                    "function": {
                        "name": "shell_command",
                        "arguments": json.dumps({"command": "python -c \"print('probe')\""}),
                    },
                },
            ]
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
            )

        tools = kwargs.get("tools")
        assert tools
        assert [
            tool.get("function", {}).get("name")
            for tool in tools
            if isinstance(tool, dict)
        ] == ["file_read", "file_write", "shell_command"]
        assert messages[-1]["role"] == "user"
        assert "stalled analysis before first write" in str(messages[-1]["content"])
        if self.output_kind == "candidate_fragment":
            payload = {
                "candidate_fragment": {"src/rst.py": "class RST: pass\n"},
                "change_summary": "Stop shell probing and materialize the bounded RST worker patch now.",
                "target_files": ["src/rst.py"],
                "test_plan": ["Run the focused separability checks after the first edit."],
                "risks": ["The worker contribution is still the first bounded patch slice."],
            }
        else:
            payload = {
                "candidate_id": "candidate-after-shell-stalled-analysis-nudge",
                "change_summary": "Stop shell probing and materialize the bounded RST writer patch now.",
                "target_files": ["src/rst.py"],
                "test_plan": ["Run the focused separability checks after the first edit."],
                "risks": ["The candidate is still the first bounded patch slice."],
            }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps(payload),
            },
        )


class _WriteStageDisabledReadRecoveryProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        if len(self.calls) == 1:
            tool_calls = [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/rst.py"}),
                    },
                },
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/fixedwidth.py"}),
                    },
                },
                {
                    "id": "call-3",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/core.py"}),
                    },
                },
            ]
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
            )
        if len(self.calls) == 2:
            tools = kwargs.get("tools")
            assert tools
            assert [
                tool.get("function", {}).get("name")
                for tool in tools
                if isinstance(tool, dict)
            ] == ["file_read", "file_edit", "file_write"]
            assert messages[-1]["role"] == "user"
            assert "make the first concrete project write now" in str(messages[-1]["content"])
            tool_call = {
                "id": "call-4",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "src/rst.py"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if len(self.calls) == 3:
            tools = kwargs.get("tools")
            assert tools
            assert [
                tool.get("function", {}).get("name")
                for tool in tools
                if isinstance(tool, dict)
            ] == ["file_edit", "file_write"]
            assert messages[-1]["role"] == "user"
            assert "already used the final targeted `file_read`" in str(messages[-1]["content"])
            tool_call = {
                "id": "call-5",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "src/rst.py"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if len(self.calls) == 4:
            tools = kwargs.get("tools")
            assert tools
            assert [
                tool.get("function", {}).get("name")
                for tool in tools
                if isinstance(tool, dict)
            ] == ["file_edit", "file_write"]
            assert messages[-1]["role"] == "user"
            assert "temporarily disabled" in str(messages[-1]["content"])
            assert "`file_read`" in str(messages[-1]["content"])
            assert "`file_edit`" in str(messages[-1]["content"])
            assert "`file_write`" in str(messages[-1]["content"])
            tool_call = {
                "id": "call-6",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "src/rst.py",
                            "content": "class RST:\n    pass\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )

        assert messages[-1]["role"] == "tool"
        assert "\"ok\": true" in str(messages[-1]["content"])
        payload = {
            "candidate_id": "candidate-after-disabled-read-recovery",
            "change_summary": "Recovered from the disabled read tool by writing the bounded patch directly.",
            "target_files": ["src/rst.py"],
            "test_plan": ["Run the focused ASCII writer checks next."],
            "risks": ["Only the bounded source patch is materialized here."],
        }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps(payload),
            },
        )


class _WriteStageFinalReadBatchThenSuccessProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        if len(self.calls) == 1:
            tool_calls = [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/rst.py"}),
                    },
                },
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/fixedwidth.py"}),
                    },
                },
                {
                    "id": "call-3",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/core.py"}),
                    },
                },
            ]
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
            )
        if len(self.calls) == 2:
            tools = kwargs.get("tools")
            assert tools
            assert [
                tool.get("function", {}).get("name")
                for tool in tools
                if isinstance(tool, dict)
            ] == ["file_read", "file_edit", "file_write"]
            assert messages[-1]["role"] == "user"
            assert "make the first concrete project write now" in str(messages[-1]["content"])
            tool_calls = [
                {
                    "id": "call-4",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/core.py"}),
                    },
                },
                {
                    "id": "call-5",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/rst.py"}),
                    },
                },
            ]
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
            )
        if len(self.calls) == 3:
            tool_call_ids, tool_messages = _latest_tool_round(messages)
            assert tool_call_ids == ["call-4", "call-5"]
            assert [str(message.get("tool_call_id") or "") for message in tool_messages] == tool_call_ids
            assert "\"ok\": true" in str(tool_messages[0]["content"])
            assert "tool_call_skipped:write_stage_final_read_consumed" in str(
                tool_messages[1]["content"]
            )
            tools = kwargs.get("tools")
            assert tools
            assert [
                tool.get("function", {}).get("name")
                for tool in tools
                if isinstance(tool, dict)
            ] == ["file_edit", "file_write"]
            assert messages[-1]["role"] == "user"
            assert "already used the final targeted `file_read`" in str(messages[-1]["content"])
            tool_call = {
                "id": "call-6",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "src/rst.py",
                            "content": "class RST:\n    pass\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )

        assert messages[-1]["role"] == "tool"
        assert "\"ok\": true" in str(messages[-1]["content"])
        return CompletionResult(
            text=json.dumps({"candidate_id": "write-stage-final-read-batch-recovered"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "write-stage-final-read-batch-recovered"}),
            },
        )


class _WriteStageDirectWriteRepromptProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        if len(self.calls) == 1:
            tool_calls = [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": json.dumps({"path": "src/rst.py"})},
                },
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/fixedwidth.py"}),
                    },
                },
                {
                    "id": "call-3",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": json.dumps({"path": "src/core.py"})},
                },
                {
                    "id": "call-4",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": json.dumps({"path": "src/rst.py"})},
                },
                {
                    "id": "call-5",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/fixedwidth.py"}),
                    },
                },
                {
                    "id": "call-6",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": json.dumps({"path": "src/core.py"})},
                },
            ]
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
            )
        if len(self.calls) == 2:
            tools = kwargs.get("tools")
            assert tools
            assert [
                tool.get("function", {}).get("name")
                for tool in tools
                if isinstance(tool, dict)
            ] == ["file_read", "file_edit", "file_write"]
            assert messages[-1]["role"] == "user"
            assert "make the first concrete project write now" in str(messages[-1]["content"])
            tool_call = {
                "id": "call-7",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "src/rst.py"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if len(self.calls) == 3:
            tools = kwargs.get("tools")
            assert tools
            assert [
                tool.get("function", {}).get("name")
                for tool in tools
                if isinstance(tool, dict)
            ] == ["file_edit", "file_write"]
            assert messages[-1]["role"] == "user"
            assert "already used the final targeted `file_read`" in str(messages[-1]["content"])
            payload = {
                "candidate_fragment": "Change `src/rst.py` to materialize the bounded patch.",
                "change_summary": "Proposed the bounded RST patch in prose instead of writing it.",
                "target_files": ["src/rst.py"],
                "test_plan": ["Run the focused ASCII writer tests."],
                "risks": ["No workspace file has been modified yet."],
            }
            return CompletionResult(
                text=json.dumps(payload),
                model=model,
                raw_assistant_message={
                    "role": "assistant",
                    "content": json.dumps(payload),
                },
            )
        if len(self.calls) == 4:
            tools = kwargs.get("tools")
            assert tools
            assert [
                tool.get("function", {}).get("name")
                for tool in tools
                if isinstance(tool, dict)
            ] == ["file_edit", "file_write"]
            assert messages[-1]["role"] == "user"
            assert "Returning prose-only patch instructions is not enough here" in str(
                messages[-1]["content"]
            )
            tool_call = {
                "id": "call-8",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "src/rst.py",
                            "content": "class RST:\n    pass\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )

        assert messages[-1]["role"] == "tool"
        assert "\"ok\": true" in str(messages[-1]["content"])
        payload = {
            "candidate_fragment": {"src/rst.py": "class RST:\n    pass\n"},
            "change_summary": "Materialized the bounded RST patch after the direct-write reminder.",
            "target_files": ["src/rst.py"],
            "test_plan": ["Run the focused ASCII writer tests."],
            "risks": ["Only the bounded source patch is materialized here."],
        }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={"role": "assistant", "content": json.dumps(payload)},
        )


class _WriteStageDirectWriteTempHelperProvider:
    def __init__(self, *, helper_path: str) -> None:
        self.helper_path = helper_path
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        if len(self.calls) == 1:
            tool_calls = [
                {
                    "id": f"call-{index}",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": path}),
                    },
                }
                for index, path in enumerate(
                    [
                        "src/rst.py",
                        "src/fixedwidth.py",
                        "src/core.py",
                        "src/rst.py",
                        "src/fixedwidth.py",
                        "src/core.py",
                    ],
                    start=1,
                )
            ]
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
            )
        if len(self.calls) == 2:
            assert messages[-1]["role"] == "user"
            assert "make the first concrete project write now" in str(messages[-1]["content"])
            tool_call = {
                "id": "call-7",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "src/rst.py"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if len(self.calls) == 3:
            assert messages[-1]["role"] == "user"
            assert "already used the final targeted `file_read`" in str(messages[-1]["content"])
            tool_call = {
                "id": "call-8",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": self.helper_path,
                            "content": "print('helper')\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if len(self.calls) == 4:
            tool_call_ids, tool_messages = _latest_tool_round(messages)
            assert tool_call_ids == ["call-8"]
            assert "write_stage_helper_path_blocked:temporary_external_helper_before_workspace_patch" in str(
                tool_messages[0]["content"]
            )
            tools = kwargs.get("tools")
            assert [
                tool.get("function", {}).get("name")
                for tool in tools
                if isinstance(tool, dict)
            ] == ["file_edit", "file_write"]
            assert messages[-1]["role"] == "user"
            assert "was blocked because direct-write mode is active" in str(messages[-1]["content"])
            tool_call = {
                "id": "call-9",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "src/rst.py",
                            "content": "class RST:\n    pass\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )

        assert messages[-1]["role"] == "tool"
        assert "\"ok\": true" in str(messages[-1]["content"])
        payload = {
            "candidate_fragment": {"src/rst.py": "class RST:\n    pass\n"},
            "change_summary": "Materialized the bounded RST patch after a temporary helper write was blocked.",
            "target_files": ["src/rst.py"],
            "test_plan": ["Run the focused ASCII writer tests."],
            "risks": ["Only the bounded source patch is materialized here."],
        }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={"role": "assistant", "content": json.dumps(payload)},
        )


class _WriteStageDirectWriteGuardrailIgnoredProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        if len(self.calls) == 1:
            tool_calls = [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": json.dumps({"path": "src/rst.py"})},
                },
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/fixedwidth.py"}),
                    },
                },
                {
                    "id": "call-3",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": json.dumps({"path": "src/core.py"})},
                },
                {
                    "id": "call-4",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": json.dumps({"path": "src/rst.py"})},
                },
                {
                    "id": "call-5",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/fixedwidth.py"}),
                    },
                },
                {
                    "id": "call-6",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": json.dumps({"path": "src/core.py"})},
                },
            ]
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
            )
        if len(self.calls) == 2:
            tools = kwargs.get("tools")
            assert tools
            assert [
                tool.get("function", {}).get("name")
                for tool in tools
                if isinstance(tool, dict)
            ] == ["file_read", "file_edit", "file_write"]
            tool_call = {
                "id": "call-7",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "src/rst.py"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )

        payload = {
            "candidate_fragment": "Change `src/rst.py` to materialize the bounded patch.",
            "change_summary": "Returned a prose-only patch instead of writing it.",
            "target_files": ["src/rst.py"],
            "test_plan": ["Run the focused ASCII writer tests."],
            "risks": ["No workspace file has been modified yet."],
        }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps(payload),
            },
        )


class _ReadOnlyFinalizeAfterStalledAnalysisProvider:
    def __init__(self, *, output_kind: str = "candidate_fragment") -> None:
        self.calls: list[dict[str, object]] = []
        self.output_kind = output_kind

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        if len(self.calls) == 1:
            tool_calls = [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": json.dumps({"path": "src/rst.py"})},
                },
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/fixedwidth.py"}),
                    },
                },
                {
                    "id": "call-3",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": json.dumps({"path": "src/core.py"})},
                },
            ]
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={"role": "assistant", "content": None, "tool_calls": tool_calls},
            )
        if len(self.calls) == 2:
            tool_calls = [
                {
                    "id": "call-4",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": json.dumps({"path": "src/core.py"})},
                },
                {
                    "id": "call-5",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": json.dumps({"path": "src/tests.py"})},
                },
                {
                    "id": "call-6",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": json.dumps({"path": "src/rst.py"})},
                },
            ]
            return CompletionResult(
                text="",
                model=model,
                tool_calls=tool_calls,
                raw_assistant_message={"role": "assistant", "content": None, "tool_calls": tool_calls},
            )
        tools = kwargs.get("tools")
        assert not tools
        assert messages[-1]["role"] == "user"
        assert "stalled analysis in checked out repo" in str(messages[-1]["content"])
        if self.output_kind == "validator":
            payload = {
                "passed": True,
                "overall_score": 0.98,
                "dimension_scores": {"contract_exactness": 0.98},
                "repair_brief": "",
                "missing_requirements": [],
                "comparison_note": "The gathered repo evidence is already sufficient to validate the candidate.",
            }
        else:
            payload = {
                "candidate_fragment": {"src/rst.py": "class RST: pass\n"},
                "change_summary": "Finalize the worker contribution from the bounded repo inspection already completed.",
                "target_files": ["src/rst.py"],
                "test_plan": ["Validate the focused RST writer behavior in the aggregation stage."],
                "risks": ["The worker contribution is synthesized from read-only repo inspection."],
            }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={"role": "assistant", "content": json.dumps(payload)},
        )


class _WriteStageFinalizeAfterVerificationProvider:
    def __init__(self, *, ignore_forced_finalize: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.ignore_forced_finalize = ignore_forced_finalize

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        if len(self.calls) == 1:
            tool_call = {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "src/rst.py",
                            "content": "class RST:\n    pass\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if len(self.calls) == 2:
            tool_call = {
                "id": "call-2",
                "type": "function",
                "function": {
                    "name": "shell_command",
                    "arguments": json.dumps(
                        {"command": "python -m py_compile src/rst.py"}
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if len(self.calls) == 3:
            tool_call = {
                "id": "call-3",
                "type": "function",
                "function": {
                    "name": "shell_command",
                    "arguments": json.dumps(
                        {"command": "python -c \"import sys; print(sys.version)\""}
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )

        tools = kwargs.get("tools")
        assert not tools
        assert messages[-1]["role"] == "user"
        assert "materialized a bounded patch" in str(messages[-1]["content"])
        if self.ignore_forced_finalize:
            tool_call = {
                "id": "call-4",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "src/rst.py"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )

        payload = {
            "candidate_fragment": {"src/rst.py": "class RST:\n    pass\n"},
            "change_summary": "Finalize the bounded RST worker patch from the materialized edit and checks already completed.",
            "target_files": ["src/rst.py"],
            "test_plan": ["python -m py_compile src/rst.py"],
            "risks": ["The worker stopped after bounded post-write verification instead of more optional environment probing."],
        }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={"role": "assistant", "content": json.dumps(payload)},
        )


class _WriteStageFinalizeAfterTempHelperProvider:
    def __init__(self, *, external_helper_path: str) -> None:
        self.external_helper_path = external_helper_path
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        if len(self.calls) == 1:
            tool_call = {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "src/rst.py",
                            "content": "class RST:\n    pass\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if len(self.calls) == 2:
            tool_call = {
                "id": "call-2",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": self.external_helper_path,
                            "content": "print('tmp helper')\n",
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )

        tools = kwargs.get("tools")
        assert not tools
        assert messages[-1]["role"] == "user"
        assert "materialized a bounded patch" in str(messages[-1]["content"])
        assert "temporary external write after workspace patch" in str(
            messages[-1]["content"]
        )
        payload = {
            "candidate_fragment": {"src/rst.py": "class RST:\n    pass\n"},
            "change_summary": "Finalize the workspace patch now that the turn drifted into external temp helper writes.",
            "target_files": ["src/rst.py"],
            "test_plan": ["Run the focused RST validation next if needed."],
            "risks": ["External temp helper generation was stopped as low-value post-patch churn."],
        }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={"role": "assistant", "content": json.dumps(payload)},
        )


class _StreamingTextProvider:
    def __init__(self) -> None:
        self.complete_calls = 0
        self.stream_calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.complete_calls += 1
        raise AssertionError("stream-enabled text-only path should not call complete()")

    async def stream(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.stream_calls.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "tools": kwargs.get("tools"),
            }
        )
        yield StreamChunk(delta="Streaming", accumulated="Streaming")
        yield StreamChunk(delta=" preview", accumulated="Streaming preview")
        yield StreamChunk(delta="", accumulated="Streaming preview", done=True, usage={"total_tokens": 3})


class _ToolAwareStreamingProvider:
    def __init__(self) -> None:
        self.complete_calls = 0
        self.stream_calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.complete_calls += 1
        raise AssertionError("tool-enabled rounds should stream without falling back to complete()")

    async def stream(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.stream_calls.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "tools": kwargs.get("tools"),
            }
        )
        if len(self.stream_calls) == 1:
            tool_call = {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "notes/existing.txt"}),
                },
            }
            yield StreamChunk(
                delta="Inspecting files",
                accumulated="Inspecting files",
            )
            yield StreamChunk(
                delta="",
                accumulated="Inspecting files",
                done=True,
                tool_calls=[tool_call],
                finish_reason="tool_calls",
                raw_assistant_message={
                    "role": "assistant",
                    "content": "Inspecting files",
                    "tool_calls": [tool_call],
                },
                provider_metadata={"family": "openai_compatible"},
            )
            return

        text = json.dumps({"candidate_id": "tool-round-complete"})
        yield StreamChunk(delta=text, accumulated=text)
        yield StreamChunk(
            delta="",
            accumulated=text,
            done=True,
            finish_reason="stop",
            raw_assistant_message={
                "role": "assistant",
                "content": text,
            },
            provider_metadata={"family": "openai_compatible"},
        )


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_executes_local_tools(tmp_path) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _FakeToolLoopProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=4,
        max_tool_calls=4,
        provider_request_overrides={"thinking": {"type": "disabled"}},
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Write one bounded file.",
            user_prompt="Create the note and return JSON.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                }
            ],
        )
    )

    assert json.loads(response.text) == {"candidate_id": "live-candidate"}
    assert (tmp_path / "notes/live-organism.txt").read_text(encoding="utf-8") == "stem-cell runtime\n"
    raw = dict(response.raw)
    assert raw["stop_reason"] == "completed"
    assert raw["executed_tools"][0]["tool_id"] == "file_write"
    assert raw["executed_tools"][0]["ok"] is True
    assert provider_impl.calls[0]["thinking"] == {"type": "disabled"}
    assert provider_impl.calls[1]["thinking"] == {"type": "disabled"}
    first_messages = provider_impl.calls[0]["messages"]
    assert first_messages[1]["role"] == "system"
    assert "Use `file_write` for creating new files or replacing/appending whole-file content." in str(
        first_messages[1]["content"]
    )
    assert _event_names(events) == [
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.completed",
        "model.requested",
        "model.responded",
        "completion.completed",
    ]
    assert events[2]["tool_id"] == "file_write"
    assert events[0]["worker_id"] == "coding-build.worker-1"
    assert events[2]["worker_id"] == "coding-build.worker-1"
    assert events[-1]["worker_id"] == "coding-build.worker-1"
    assert events[3]["result"]["bytes_written"] == len("stem-cell runtime\n".encode("utf-8"))
    capsule_event = _first_event(events, "context.capsule.emitted")
    assert capsule_event["capsule_kinds"] == ["implementation_delta"]
    readiness_event = _first_event(events, "context.readiness.emitted")
    assert readiness_event["ready_for_downstream"] is True
    assert readiness_event["predicate"] == "tool_context_available"
    assert readiness_event["readiness"]["capsule_ids"] == capsule_event["capsule_ids"]
    assert raw["executed_tools"][0]["context_capsules"][0]["kind"] == "implementation_delta"
    assert raw["executed_tools"][0]["readiness_signal"]["ready_for_downstream"] is True


@pytest.mark.asyncio
async def test_tool_loop_super_dan_budget_audit_extends_tool_cap(tmp_path) -> None:
    events: list[dict[str, object]] = []
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "a.txt").write_text("alpha\n", encoding="utf-8")
    (notes_dir / "b.txt").write_text("beta\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _BudgetAuditExtensionProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=4,
        max_tool_calls=1,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Read the requested files and return JSON.",
            user_prompt="Read notes/a.txt and notes/b.txt, then summarize.",
            metadata={
                "worker_id": "super-dan.live.general-builder",
                "tool_budget_profile": "super_dan_live",
            },
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ],
        )
    )

    assert json.loads(response.text) == {"candidate_id": "budget-extended"}
    raw = dict(response.raw)
    assert raw["stop_reason"] == "completed"
    assert [tool["arguments"]["path"] for tool in raw["executed_tools"]] == [
        "notes/a.txt",
        "notes/b.txt",
    ]
    assert raw["budget_extensions"][0]["extra_tool_calls"] == 2
    assert any(event.get("event") == "toolloop.budget_review.started" for event in events)
    approved = _first_event(events, "toolloop.budget_extension.approved")
    assert approved["limit_kind"] == "tool_calls"
    assert approved["new_tool_call_limit"] == 3


@pytest.mark.asyncio
async def test_tool_loop_operator_no_mutation_policy_removes_write_and_shell_tools(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "workspace_check", "file_edit", "file_write", "shell_command"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _CaptureToolSchemaProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=2,
        max_tool_calls=4,
        event_callback=events.append,
    )
    request = CompletionRequest(
        model="gpt-test",
        system_prompt="Return the exact requested answer.",
        user_prompt=(
            "Blueprint smoke test verification: reply with exactly 'blueprint smoke test ok'. "
            "Do not edit files or run external commands."
        ),
        metadata={
            "worker_id": "super-dan.live.general-builder",
            "operator_intent_policy": {"active": True, "allow_workspace_mutation": False},
        },
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "file_read",
                    "description": "Read a file.",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "workspace_check",
                    "description": "Check workspace state.",
                    "parameters": {"type": "object", "properties": {"check": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_edit",
                    "description": "Edit a file.",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_write",
                    "description": "Write a file.",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "shell_command",
                    "description": "Run a command.",
                    "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
                },
            },
        ],
    )

    assert local_runtime_module._request_forbids_workspace_mutation(request) is True
    response = await provider.complete(request)

    assert json.loads(response.text) == {"candidate_id": "captured-tool-schema"}
    tool_names = [
        str(tool.get("function", {}).get("name") or "")
        for tool in list(provider_impl.calls[0]["tools"] or [])
        if isinstance(tool, dict)
    ]
    assert tool_names == ["file_read", "workspace_check"]
    rendered_messages = "\n".join(
        str(message.get("content") or "")
        for message in list(provider_impl.calls[0]["messages"] or [])
        if isinstance(message, dict)
    )
    assert "read-only/no-mutation turn" in rendered_messages
    narrowed_event = next(
        event for event in events if event["event"] == "toolloop.operator_read_only_tools_narrowed"
    )
    assert narrowed_event["enabled_tools"] == ["file_read", "workspace_check"]
    policy_dropped_tools = sorted(
        {
            str(tool)
            for event in events
            if event.get("event")
            in {
                "toolloop.operator_intent_tools_narrowed",
                "toolloop.operator_read_only_tools_narrowed",
            }
            for tool in list(event.get("dropped_tools") or [])
        }
    )
    assert policy_dropped_tools == [
        "file_edit",
        "file_write",
        "shell_command",
    ]


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_is_unbounded_by_default(tmp_path) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "existing.txt").write_text("present\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read"],
        workspace_root=tmp_path,
    )
    provider_impl = _ManyRoundToolLoopProvider(rounds=9)
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=16,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Keep reading until done.",
            user_prompt="Read the file a few times, then return JSON.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ],
        )
    )

    assert json.loads(response.text) == {"candidate_id": "after-many-rounds"}
    assert response.raw["stop_reason"] == "completed"
    assert len(response.raw["executed_tools"]) == 9


@pytest.mark.asyncio
async def test_tool_loop_reuses_unchanged_file_read_cache(tmp_path) -> None:
    events: list[dict[str, object]] = []
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "existing.txt").write_text("present\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _RepeatedFileReadThenSuccessProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=4,
        max_tool_calls=4,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Read the file twice, then return JSON.",
            user_prompt="Read the file twice.",
            metadata={"worker_id": "coding-build.worker-cache"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ],
        )
    )

    assert json.loads(response.text) == {"candidate_id": "cached-file-read-complete"}
    assert _event_names(events).count("tool.completed") == 1
    assert _event_names(events).count("tool.cache_hit") == 1
    assert response.raw["executed_tools"][1]["cache_hit"] is True


@pytest.mark.asyncio
async def test_tool_loop_refreshes_file_read_cache_after_mutation(tmp_path) -> None:
    events: list[dict[str, object]] = []
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "existing.txt").write_text("present\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _ReadWriteReadThenSuccessProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=5,
        max_tool_calls=5,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Read, update, read again, then return JSON.",
            user_prompt="Refresh after mutation.",
            metadata={"worker_id": "coding-build.worker-cache"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
        )
    )

    assert json.loads(response.text) == {"candidate_id": "refreshed-after-write"}
    assert _event_names(events).count("tool.cache_hit") == 0
    file_read_completed = [
        event
        for event in events
        if event.get("event") == "tool.completed" and event.get("tool_id") == "file_read"
    ]
    assert len(file_read_completed) == 2
    assert response.raw["executed_tools"][-1]["result"]["content"] == "updated context\n"


@pytest.mark.asyncio
async def test_tool_loop_super_dan_soft_budget_counts_postwrite_phase_locally(tmp_path) -> None:
    events: list[dict[str, object]] = []
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "existing.txt").write_text("seed context\n", encoding="utf-8")
    for index in range(1, 6):
        (notes_dir / f"input-{index}.txt").write_text(
            f"seed context {index}\n",
            encoding="utf-8",
        )
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _PostwriteSoftBudgetContinuationProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=20,
        max_tool_calls=20,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn JSON.",
            user_prompt="Inspect the context, then write a substantial report artifact.",
            metadata={
                "worker_id": "super-dan.live.general-repair",
                "tool_budget_profile": "super_dan_live",
            },
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                                "mode": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_id": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    assert json.loads(response.text)["candidate_id"] == "postwrite-soft-budget-continued"
    assert "Expanded analysis" in (notes_dir / "report.md").read_text(encoding="utf-8")
    assert len(provider_impl.calls) >= 8
    soft_budget_events = [
        event for event in events if event.get("event") == "toolloop.soft_budget_nudged"
    ]
    assert not any(
        event.get("phase") == "coding_postwrite"
        and event.get("action") == "force_finalize"
        for event in soft_budget_events
    )


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_nudges_after_missing_required_tool_arguments(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "existing.txt").write_text("present\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=_MissingRequiredToolArgThenSuccessProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn JSON.",
            user_prompt="Read the file and return JSON.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ],
        )
    )

    assert json.loads(response.text) == {"candidate_id": "recovered-after-nudge"}
    event_names = _event_names(events)
    assert event_names == [
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.failed",
        "toolloop.invalid_tool_arguments_nudged",
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.completed",
        "model.requested",
        "model.responded",
        "completion.completed",
    ]
    failed_event = next(event for event in events if event["event"] == "tool.failed")
    assert "tool_arguments_invalid: missing required arguments for file_read: path" in str(
        failed_event["error"]
    )
    nudge_event = next(
        event for event in events if event["event"] == "toolloop.invalid_tool_arguments_nudged"
    )
    assert nudge_event["tool_ids"] == ["file_read"]


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_nudges_after_repeated_identical_tool_calls(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["list_directory"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=_RepeatedDiscoveryThenSuccessProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn JSON.",
            user_prompt="Inspect the workspace and return JSON.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "list_directory",
                        "description": "List a directory.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )
    )

    assert json.loads(response.text) == {"candidate_id": "recovered-after-duplicate-nudge"}
    event_names = _event_names(events)
    assert event_names == [
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.completed",
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.completed",
        "toolloop.repeated_tool_call_nudged",
        "model.requested",
        "model.responded",
        "completion.completed",
    ]
    repeated_event = next(
        event for event in events if event["event"] == "toolloop.repeated_tool_call_nudged"
    )
    assert repeated_event["tool_ids"] == ["list_directory"]


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_nudges_after_empty_web_search_arguments(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["web_search"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    metadata = runtime._tools["web_search"][1]

    async def _fake_web_search(*, query: str, **kwargs):
        return {"count": 1, "provider": "fake", "results": [{"query": query, **kwargs}]}

    runtime._tools["web_search"] = (_fake_web_search, metadata)
    provider = ToolLoopCompletionProvider(
        provider=_EmptyWebSearchArgsThenSuccessProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: research_worker\nReturn JSON.",
            user_prompt="Find one current oil benchmark.",
            metadata={"worker_id": "deep-research.reader-a"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web.",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                    },
                }
            ],
        )
    )

    assert json.loads(response.text) == {"candidate_id": "web-search-recovered-after-nudge"}
    event_names = _event_names(events)
    assert event_names == [
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.failed",
        "toolloop.invalid_tool_arguments_nudged",
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.completed",
        "model.requested",
        "model.responded",
        "completion.completed",
    ]
    failed_event = next(event for event in events if event["event"] == "tool.failed")
    assert "tool_arguments_invalid:" in str(failed_event["error"])
    assert "web_search" in str(failed_event["error"])
    assert "query" in str(failed_event["error"])
    assert "url" in str(failed_event["error"])
    nudge_event = next(
        event for event in events if event["event"] == "toolloop.invalid_tool_arguments_nudged"
    )
    assert nudge_event["tool_ids"] == ["web_search"]


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_stops_after_first_invalid_web_search_in_batch(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["web_search"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    metadata = runtime._tools["web_search"][1]

    async def _fake_web_search(*, query: str, **kwargs):
        return {"count": 1, "provider": "fake", "results": [{"query": query, **kwargs}]}

    runtime._tools["web_search"] = (_fake_web_search, metadata)
    provider = ToolLoopCompletionProvider(
        provider=_ManyInvalidWebSearchCallsThenSuccessProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=6,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: research_worker\nReturn JSON.",
            user_prompt="Find one current China coal benchmark.",
            metadata={"worker_id": "deep-research.reader-b"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web.",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                    },
                }
            ],
        )
    )

    assert json.loads(response.text) == {"candidate_id": "web-search-batch-recovered"}
    failed_events = [event for event in events if event["event"] == "tool.failed"]
    assert len(failed_events) == 1
    started_events = [event for event in events if event["event"] == "tool.started"]
    completed_events = [event for event in events if event["event"] == "tool.completed"]
    assert len(started_events) == 2
    assert len(completed_events) == 1
    nudge_event = next(
        event for event in events if event["event"] == "toolloop.invalid_tool_arguments_nudged"
    )
    assert nudge_event["tool_ids"] == ["web_search"]
    assert nudge_event["blocked_by_tool_call_ids"] == ["call-1", "call-2", "call-3"]


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_temporarily_disables_repeatedly_invalid_tool(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=_RepeatedInvalidToolThenDisabledProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn JSON.",
            user_prompt="Inspect the target file and return JSON.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ],
        )
    )

    assert json.loads(response.text) == {"candidate_id": "recovered-after-tool-disable"}
    event_names = _event_names(events)
    assert event_names == [
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.failed",
        "toolloop.invalid_tool_arguments_nudged",
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.failed",
        "toolloop.invalid_tool_arguments_nudged",
        "toolloop.temporarily_disabled_tools",
        "model.requested",
        "model.responded",
        "completion.completed",
    ]
    disabled_event = next(
        event for event in events if event["event"] == "toolloop.temporarily_disabled_tools"
    )
    assert disabled_event["tool_ids"] == ["file_read"]
    assert disabled_event["enabled_tools"] == []


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_disables_repeatedly_invalid_file_edit_calls(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    (tmp_path / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_edit"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=_RepeatedInvalidFileEditThenDisabledProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn JSON.",
            user_prompt="Patch the file and return JSON.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "content": {"type": "string"},
                                "mode": {"type": "string"},
                            },
                            "required": ["path"],
                            "anyOf": [{"required": ["start_line"]}, {"required": ["edits"]}],
                        },
                    },
                }
            ],
        )
    )

    assert json.loads(response.text) == {
        "candidate_id": "file-edit-recovered-after-tool-disable"
    }
    failed_events = [event for event in events if event["event"] == "tool.failed"]
    assert len(failed_events) == 2
    assert "tool_arguments_invalid:" in str(failed_events[0]["error"])
    assert "content" in str(failed_events[0]["error"])
    event_names = _event_names(events)
    assert event_names == [
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.failed",
        "toolloop.invalid_tool_arguments_nudged",
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.failed",
        "toolloop.invalid_tool_arguments_nudged",
        "toolloop.temporarily_disabled_tools",
        "model.requested",
        "model.responded",
        "completion.completed",
    ]
    disabled_event = next(
        event for event in events if event["event"] == "toolloop.temporarily_disabled_tools"
    )
    assert disabled_event["tool_ids"] == ["file_edit"]
    assert disabled_event["enabled_tools"] == []


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_recovers_from_batched_file_edit_missing_anchor(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    target = tmp_path / "notes.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_edit"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=_BatchedFileEditMissingAnchorThenSuccessProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn JSON.",
            user_prompt="Patch the file and return JSON.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "content": {"type": "string"},
                                "edits": {"type": "array"},
                            },
                            "required": ["path"],
                            "anyOf": [{"required": ["start_line"]}, {"required": ["edits"]}],
                        },
                    },
                }
            ],
        )
    )

    assert json.loads(response.text) == {
        "candidate_id": "file-edit-recovered-after-batch-anchor-nudge"
    }
    assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"
    failed_event = next(event for event in events if event["event"] == "tool.failed")
    assert "edits[0] must include start_line or old_string" in str(failed_event["error"])
    event_names = _event_names(events)
    assert event_names == [
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.failed",
        "toolloop.invalid_tool_arguments_nudged",
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.completed",
        "model.requested",
        "model.responded",
        "completion.completed",
    ]


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_recovers_jsonish_raw_file_edit_arguments(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    target = tmp_path / "notes.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_edit"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=_RawJsonishFileEditThenSuccessProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=3,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn JSON.",
            user_prompt="Patch the file and return JSON.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"},
                                "content": {"type": "string"},
                                "mode": {"type": "string"},
                            },
                            "required": ["path"],
                        },
                    },
                }
            ],
        )
    )

    assert json.loads(response.text) == {
        "candidate_id": "file-edit-recovered-from-jsonish-raw"
    }
    assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"
    assert not any(event.get("event") == "tool.failed" for event in events)


@pytest.mark.asyncio
async def test_tool_loop_blocks_shrinking_overwrite_when_repair_policy_is_additive(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    target = tmp_path / "report.md"
    target.write_text(
        "# Report\n\nExisting financial table and valuation discussion.\n",
        encoding="utf-8",
    )
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_write", "file_edit"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=_ShrinkingRepairOverwriteThenFileEditProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: workspace_worker\nReturn JSON.",
            user_prompt="Repair the report by adding missing valuation substance.",
            metadata={
                "worker_id": "super-dan.live.general-repair",
                "repair_policy": {
                    "forbid_shrinking_existing_artifacts": True,
                    "target_paths": ["report.md"],
                },
            },
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                                "mode": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"},
                                "content": {"type": "string"},
                            },
                            "required": ["path"],
                        },
                    },
                },
            ],
        )
    )

    assert json.loads(response.text) == {"candidate_id": "repair-policy-recovered"}
    report_text = target.read_text(encoding="utf-8")
    assert "# Baseline" not in report_text
    assert "Existing financial table" in report_text
    assert "Expanded valuation" in report_text
    policy_event = next(
        event for event in events if event.get("event") == "toolloop.repair_policy_nudged"
    )
    assert policy_event["paths"] == ["report.md"]


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_nudges_after_invalid_file_write_overwrite(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    (tmp_path / "module.py").write_text(
        "def alpha():\n    return 1\n",
        encoding="utf-8",
    )
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=_InvalidFileWriteThenSuccessProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn JSON.",
            user_prompt="Patch the file and return JSON.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                }
            ],
        )
    )

    assert json.loads(response.text) == {
        "candidate_id": "file-write-recovered-after-nudge"
    }
    event_names = _event_names(events)
    assert event_names == [
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.failed",
        "toolloop.source_structure_nudged",
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.completed",
        "model.requested",
        "model.responded",
        "completion.completed",
    ]
    failed_event = next(event for event in events if event["event"] == "tool.failed")
    assert "tool_arguments_invalid:" in str(failed_event["error"])
    assert "syntactically invalid" in str(failed_event["error"])
    nudge_event = next(
        event for event in events if event["event"] == "toolloop.source_structure_nudged"
    )
    assert nudge_event["tool_ids"] == ["file_write"]
    assert (tmp_path / "module.py").read_text(encoding="utf-8") == "def alpha():\n    return 2\n"


@pytest.mark.asyncio
async def test_write_stage_keeps_direct_write_tool_enabled_after_repeated_invalid_arguments(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=_WriteStageRepeatedInvalidFileWriteRecoveryProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=6,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Write module.py and return the bounded candidate.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                }
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["target_files"] == ["module.py"]
    assert payload["candidate_fragment"]["module.py"] == "def alpha():\n    return 3\n"
    event_names = _event_names(events)
    assert event_names.count("toolloop.invalid_tool_arguments_nudged") == 2
    assert "toolloop.temporarily_disabled_tools" not in event_names
    assert "toolloop.write_stage_finalize_forced" not in event_names
    assert (tmp_path / "module.py").read_text(encoding="utf-8") == "def alpha():\n    return 3\n"


@pytest.mark.asyncio
async def test_tool_loop_downshifts_repeated_existing_file_overwrite_to_file_edit(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    (tmp_path / "module.py").write_text(
        "def alpha():\n    return 1\n",
        encoding="utf-8",
    )
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_edit", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=_ExistingFileWriteDownshiftRecoveryProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=6,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Patch module.py and return the bounded candidate.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "content": {"type": "string"},
                            },
                            "required": ["path"],
                            "anyOf": [{"required": ["start_line"]}, {"required": ["edits"]}],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["target_files"] == ["module.py"]
    assert payload["candidate_fragment"]["module.py"] == "def alpha():\n    return 12\n"
    assert (tmp_path / "module.py").read_text(encoding="utf-8") == "def alpha():\n    return 12\n"

    downshift_events = [
        event for event in events if event["event"] == "toolloop.file_write_downshift_nudged"
    ]
    assert len(downshift_events) >= 2
    assert downshift_events[0]["paths"] == ["module.py"]
    assert "model_output_truncated" in downshift_events[0]["reasons"]
    event_names = _event_names(events)
    assert event_names.count("toolloop.invalid_tool_arguments_nudged") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("second_tool_name", "second_tool_arguments", "expected_reason", "expected_tool_event"),
    [
        ("file_read", {"path": ".dan-code"}, "internal_state_probe_after_empty_workspace", "tool.failed"),
        ("list_directory", {"path": ".dan-code"}, "internal_state_probe_after_empty_workspace", "tool.failed"),
        ("file_read", {"path": "acceptance.md"}, "operator_artifact_read_after_empty_workspace", "tool.completed"),
        ("file_read", {"path": "src/taskforge/models.py"}, "missing_path_after_empty_workspace", "tool.failed"),
        (
            "web_search",
            {"query": "python kanban board data model Card Column Board class design"},
            "web_search_after_empty_workspace",
            "tool.completed",
        ),
    ],
)
async def test_tool_loop_completion_provider_forces_finalize_for_empty_read_only_coding_workers(
    tmp_path,
    second_tool_name: str,
    second_tool_arguments: dict[str, object],
    expected_reason: str,
    expected_tool_event: str,
) -> None:
    events: list[dict[str, object]] = []
    runtime_tool_ids = ["list_directory", "file_read", "file_write"]
    if second_tool_name == "web_search":
        runtime_tool_ids.append("web_search")
    if second_tool_arguments.get("path") == "acceptance.md":
        (tmp_path / "acceptance.md").write_text("- acceptance\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=runtime_tool_ids,
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    if second_tool_name == "web_search":
        metadata = runtime._tools["web_search"][1]

        async def _fake_web_search(**_kwargs):
            return {"count": 5, "provider": "fake", "results": []}

        runtime._tools["web_search"] = (_fake_web_search, metadata)
    provider_impl = _ReadOnlyFinalizeProvider(
        workspace_root=tmp_path,
        second_tool_name=second_tool_name,
        second_tool_arguments=second_tool_arguments,
    )
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=8,
        event_callback=events.append,
    )
    request_tools = [
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "List a directory.",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_read",
                "description": "Read a file.",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        },
    ]
    if second_tool_name == "web_search":
        request_tools.append(
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web.",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }
        )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Task:\nInvestigate the empty workspace and return a bounded candidate.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=request_tools,
        )
    )

    payload = json.loads(response.text)
    assert payload["target_files"] == ["pyproject.toml"]
    assert response.raw["stop_reason"] == "completed"
    assert _event_names(events) == [
        "model.requested",
        "model.responded",
        "tool.started",
        "tool.completed",
        "model.requested",
        "model.responded",
        "tool.started",
        expected_tool_event,
        "toolloop.read_only_finalize_forced",
        "model.requested",
        "model.responded",
        "completion.completed",
    ]
    finalize_event = _first_event(events, "toolloop.read_only_finalize_forced")
    assert finalize_event["reason"] == expected_reason
    assert provider_impl.calls[2]["tools"] is None


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_forces_finalize_for_research_notes(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "evidence.txt").write_text("bounded grounded evidence\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _ResearchFinalizeProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=8,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Return the bounded research note JSON.",
            user_prompt="Ground this lane with a few targeted reads, then finalize.",
            metadata={"worker_id": "deep-research.reader-a"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                }
            ],
            output_contract=OutputContract(
                definition_of_done="Return one compact evidence note.",
                expected_return_shape=json.dumps(
                    {
                        "findings": ["<required>"],
                        "evidence_refs": ["<required>"],
                        "contradictions": [],
                        "open_questions": [],
                        "reasoning_notes": [],
                        "follow_up_queries": [],
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["findings"] == ["Reader gathered a bounded grounded note."]
    assert response.raw["stop_reason"] == "completed"
    assert any(event["event"] == "toolloop.research_finalize_forced" for event in events)
    assert len(response.raw["executed_tools"]) == 2
    assert provider_impl.calls[2]["tools"] is None


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_retries_once_after_provider_prompt_filter_rejection(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["web_search"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _PromptFilterThenSuccessProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Return the required research JSON.",
            user_prompt="Synthesize the evidence into one research report.",
            metadata={"worker_id": "deep-research.lead"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web.",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                    },
                }
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded research report.",
                expected_return_shape=json.dumps(
                    {
                        "findings": "<required>",
                        "evidence_summary": "<required>",
                        "quality_gates": "<required>",
                        "report_readiness": "<required>",
                        "recommended_change": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["report_readiness"] == "provisional"
    assert response.raw["stop_reason"] == "completed_after_provider_safety_retry"
    assert len(provider_impl.calls) == 2
    assert provider_impl.calls[0]["tools"] is not None
    assert provider_impl.calls[1]["tools"] is None
    assert _event_names(events) == [
        "model.requested",
        "model.provider_prompt_rejected",
        "model.provider_safety_retry",
        "model.requested",
        "model.responded",
        "completion.completed",
    ]
    assert events[1]["retry"] is True
    assert events[-1]["stop_reason"] == "completed_after_provider_safety_retry"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_returns_structured_fallback_after_second_provider_prompt_rejection(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["web_search"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _PromptFilterAlwaysProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Return the required research JSON.",
            user_prompt="Synthesize the evidence into one research report.",
            metadata={"worker_id": "deep-research.lead"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web.",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                    },
                }
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded research report.",
                expected_return_shape=json.dumps(
                    {
                        "findings": "<required>",
                        "evidence_summary": "<required>",
                        "quality_gates": "<required>",
                        "report_readiness": "<required>",
                        "readiness_note": "<required>",
                        "recommended_change": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["report_readiness"] == "blocked"
    assert payload["quality_gates"][0]["gate"] == "final_status"
    assert response.raw["stop_reason"] == "provider_prompt_rejected_after_safety_retry"
    assert response.raw["provider_result"]["provider_metadata"]["provider_safety_fallback"] is True
    assert len(provider_impl.calls) == 2
    assert provider_impl.calls[0]["tools"] is not None
    assert provider_impl.calls[1]["tools"] is None
    assert _event_names(events) == [
        "model.requested",
        "model.provider_prompt_rejected",
        "model.provider_safety_retry",
        "model.requested",
        "model.provider_prompt_rejected",
        "completion.completed",
    ]
    assert events[1]["retry"] is True
    assert events[4]["retry"] is False


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_returns_structured_fallback_after_provider_timeout(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["web_search"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _SlowProvider(delay_seconds=0.05)
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        completion_timeout_seconds=0.01,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Create one bounded coding candidate.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web.",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                    },
                }
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding candidate.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["candidate_fragment"] == {}
    assert payload["target_files"] == []
    assert response.raw["stop_reason"] == "provider_completion_timeout"
    assert response.raw["provider_result"]["provider_metadata"]["provider_timeout_fallback"] is True
    assert response.raw["provider_result"]["provider_metadata"]["timeout_seconds"] == pytest.approx(0.01)
    assert _event_names(events) == [
        "model.requested",
        "model.timeout",
        "completion.completed",
    ]
    assert events[1]["timeout_seconds"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_uses_request_timeout_metadata(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=[],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _SlowProvider(delay_seconds=0.05)
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        completion_timeout_seconds=90.0,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: validator_reviewer\nReturn one validation verdict.",
            user_prompt="Review the candidate.",
            metadata={
                "worker_id": "coding-build.validator.reviewer-a",
                "completion_timeout_seconds": 0.01,
                "short_completion_timeout": True,
            },
            tools=[],
            output_contract=OutputContract(
                definition_of_done="Return one validation verdict.",
                expected_return_shape=json.dumps({"result": "<required>"}, sort_keys=True),
            ),
        )
    )

    assert response.raw["stop_reason"] == "provider_completion_timeout"
    assert response.raw["provider_result"]["provider_metadata"]["timeout_seconds"] == pytest.approx(0.01)
    assert _event_names(events) == [
        "model.requested",
        "model.timeout",
        "completion.completed",
    ]
    assert events[0]["timeout_seconds"] == pytest.approx(0.01)
    assert events[1]["timeout_seconds"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_timeout_detaches_cancellation_ignoring_provider(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["web_search"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _CancellationIgnoringSlowProvider(delay_seconds=0.05)
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        completion_timeout_seconds=0.01,
        event_callback=events.append,
    )

    started = time.monotonic()
    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Create one bounded coding candidate.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web.",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                    },
                }
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding candidate.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )
    elapsed = time.monotonic() - started

    payload = json.loads(response.text)
    assert payload["candidate_fragment"] == {}
    assert response.raw["stop_reason"] == "provider_completion_timeout"
    assert elapsed < 0.04
    await asyncio.sleep(0)
    assert provider_impl.cancelled_calls == 1
    assert _event_names(events) == [
        "model.requested",
        "model.timeout",
        "completion.completed",
    ]


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_returns_structured_validator_timeout_fallback(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _SlowProvider(delay_seconds=0.05)
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        completion_timeout_seconds=0.01,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: validator_synthesizer\nReturn the required validation JSON.",
            user_prompt="Validate the bounded coding candidate.",
            metadata={"worker_id": "coding-build.validator.lead"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                }
            ],
            output_contract=OutputContract(
                definition_of_done="Return the validation result for the aggregated coding candidate.",
                expected_return_shape=json.dumps(
                    {
                        "passed": "<required>",
                        "overall_score": "<required>",
                        "dimension_scores": "<required>",
                        "repair_brief": "<required>",
                        "missing_requirements": "<required>",
                        "comparison_note": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["passed"] is False
    assert payload["overall_score"] == 0.0
    assert payload["dimension_scores"] == {}
    assert "timed out" in payload["repair_brief"].lower()
    assert response.raw["stop_reason"] == "provider_completion_timeout"
    assert response.raw["provider_result"]["provider_metadata"]["provider_timeout_fallback"] is True


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_short_timeout_disables_timeout_recovery_retry(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "a.py").write_text("A = 1\n", encoding="utf-8")
    (src_dir / "b.py").write_text("B = 1\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_edit", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _ReadTwiceThenSlowProvider(delay_seconds=0.05)
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=8,
        completion_timeout_seconds=0.01,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_aggregator\nReturn the required candidate JSON.",
            user_prompt="Merge the bounded worker outputs.",
            metadata={
                "worker_id": "coding-build.aggregation.lead",
                "organism_stage": "aggregation",
                "short_completion_timeout": True,
                "completion_timeout_seconds": 0.01,
            },
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "content": {"type": "string"},
                            },
                            "required": ["path"],
                            "anyOf": [{"required": ["start_line"]}, {"required": ["edits"]}],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding candidate.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_id": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert "timed out" in payload["change_summary"].lower()
    assert response.raw["stop_reason"] == "provider_completion_timeout"
    assert len(provider_impl.calls) == 2
    event_names = [event["event"] for event in events]
    assert "model.timeout" in event_names
    assert "model.timeout_recovery_retry" not in event_names


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_salvages_written_files_after_provider_timeout(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _WriteThenSlowProvider(delay_seconds=0.05)
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        completion_timeout_seconds=0.01,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_aggregator\nReturn the required candidate JSON.",
            user_prompt="Materialize the bounded candidate and return final JSON.",
            metadata={"worker_id": "coding-build.aggregation.lead"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                }
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding candidate.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_id": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["candidate_id"] == "partial-candidate-from-tool-evidence"
    assert payload["target_files"] == ["src/demo.py"]
    assert payload["workspace_effect"] == "modified"
    assert payload["synthesized_from_tool_evidence"] is True
    assert "provider completion timed out" in payload["risks"][0].lower()
    assert response.raw["stop_reason"] == "provider_completion_timeout"
    assert response.raw["provider_result"]["provider_metadata"]["tool_evidence_fallback"] is True
    assert (tmp_path / "src" / "demo.py").read_text(encoding="utf-8") == "print('demo')\n"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_salvages_worker_fragment_after_provider_timeout(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _WriteThenSlowProvider(delay_seconds=0.05)
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        completion_timeout_seconds=0.01,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Materialize the bounded worker contribution and return JSON.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                }
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["candidate_fragment"] == {"src/demo.py": "print('demo')\n"}
    assert payload["readback_files"] == ["src/demo.py"]
    assert payload["target_files"] == ["src/demo.py"]
    assert "workspace_effect" not in payload
    assert payload["synthesized_from_tool_evidence"] is True
    assert response.raw["stop_reason"] == "provider_completion_timeout"
    assert response.raw["provider_result"]["provider_metadata"]["tool_evidence_fallback"] is True
    assert (tmp_path / "src" / "demo.py").read_text(encoding="utf-8") == "print('demo')\n"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_can_retry_after_final_read_timeout(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text("print('orig')\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _WriteStageTimeoutRecoveryProvider(delay_seconds=0.05)
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=12,
        completion_timeout_seconds=0.01,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Patch src/demo.py with the smallest correct change.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["target_files"] == ["src/demo.py"]
    assert payload["change_summary"] == "Patched src/demo.py after timeout recovery."
    assert response.raw["stop_reason"] == "completed"
    assert (tmp_path / "src" / "demo.py").read_text(encoding="utf-8") == "print('patched')\n"

    event_names = [event["event"] for event in events]
    assert "model.timeout" in event_names
    assert "model.timeout_recovery_retry" in event_names
    assert "toolloop.write_stage_final_read_consumed" in event_names
    assert len(provider_impl.calls) == 5

    timed_out_call = provider_impl.calls[2]
    timed_out_tools = [
        str(tool.get("function", {}).get("name") or "")
        for tool in list(timed_out_call["tools"] or [])
        if isinstance(tool, dict)
    ]
    assert timed_out_tools == ["file_write"]


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_forces_exclusive_owner_direct_write_after_one_read(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text("print('orig')\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_edit", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _ExclusiveOwnerReadThenWriteProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=6,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Patch src/demo.py with the smallest correct change.",
            metadata={
                "worker_id": "coding-build.worker-1",
                "exclusive_write_owner_path": "src/demo.py",
            },
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file by line range.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "start_line", "content"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["target_files"] == ["src/demo.py"]
    assert payload["change_summary"] == "Patched the exclusive owner file."
    assert response.raw["stop_reason"] == "completed"
    assert (tmp_path / "src" / "demo.py").read_text(encoding="utf-8") == "print('patched')\n"

    nudge_event = next(
        event for event in events if event["event"] == "toolloop.write_stage_first_write_nudged"
    )
    assert nudge_event["reason"] == "exclusive_write_owner_after_first_read"
    assert nudge_event["enabled_tools"] == ["file_edit"]
    assert [
        str(tool.get("function", {}).get("name") or "")
        for tool in list(provider_impl.calls[2]["tools"] or [])
        if isinstance(tool, dict)
    ] == []


def test_effective_completion_timeout_budget_caps_exclusive_owner_direct_write(
    tmp_path,
) -> None:
    request = CompletionRequest(
        model="gpt-test",
        system_prompt="Role: coding_worker\nReturn the required coding JSON.",
        user_prompt="Patch src/demo.py with the smallest correct change.",
        metadata={
            "worker_id": "coding-build.worker-1",
            "exclusive_write_owner_path": "src/demo.py",
        },
        output_contract=OutputContract(
            definition_of_done="Return one bounded coding contribution.",
            expected_return_shape=json.dumps(
                {
                    "candidate_fragment": "<required>",
                    "change_summary": "<required>",
                    "target_files": "<required>",
                    "test_plan": "<required>",
                    "risks": "<required>",
                },
                sort_keys=True,
            ),
        ),
    )

    timeout_seconds, timeout_strategy = local_runtime_module._effective_completion_timeout_budget(
        base_timeout_seconds=90.0,
        request=request,
        executed_tools=[],
        workspace_root=tmp_path,
        write_stage_direct_write_required=True,
    )

    assert timeout_seconds == pytest.approx(45.0)
    assert timeout_strategy == "exclusive_owner_direct_write"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_recovers_exclusive_owner_after_timeout_in_direct_write_stage(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text("print('orig')\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_edit", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _ExclusiveOwnerTimeoutRecoveryProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=8,
        completion_timeout_seconds=0.01,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Patch src/demo.py with the smallest correct change.",
            metadata={
                "worker_id": "coding-build.worker-1",
                "exclusive_write_owner_path": "src/demo.py",
                "completion_timeout_seconds": 90.0,
            },
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file by line range.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "start_line", "content"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["target_files"] == ["src/demo.py"]
    assert response.raw["stop_reason"] == "completed"
    assert (tmp_path / "src" / "demo.py").read_text(encoding="utf-8") == (
        "print('patched-after-timeout')\n"
    )
    assert len(provider_impl.calls) == 4
    event_names = [event["event"] for event in events]
    assert "model.timeout" in event_names
    assert "model.timeout_recovery_retry" in event_names


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_keeps_exclusive_owner_read_context_for_write(
    tmp_path,
) -> None:
    marker = "exclusive-owner-tail-marker"
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text(
        "x" * 2500 + marker + "\n",
        encoding="utf-8",
    )
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_write"],
        workspace_root=tmp_path,
        event_callback=lambda _event: None,
    )
    provider_impl = _ExclusiveOwnerReadThenWriteProvider(
        expected_full_read_marker=marker,
        expected_write_tool="file_write",
    )
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=6,
    )

    await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Patch src/demo.py with the smallest correct change.",
            metadata={
                "worker_id": "coding-build.worker-1",
                "exclusive_write_owner_path": "src/demo.py",
            },
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_prefers_exclusive_owner_file_edit_with_numbered_context(
    tmp_path,
) -> None:
    marker = "exclusive-owner-edit-tail-marker"
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text(
        "x" * 2500 + marker + "\n",
        encoding="utf-8",
    )
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_edit", "file_write"],
        workspace_root=tmp_path,
        event_callback=lambda _event: None,
    )
    provider_impl = _ExclusiveOwnerReadThenWriteProvider(
        expected_full_read_marker=marker,
    )
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=6,
    )

    await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Patch src/demo.py with the smallest correct change.",
            metadata={
                "worker_id": "coding-build.worker-1",
                "exclusive_write_owner_path": "src/demo.py",
            },
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file by line range.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "start_line", "content"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    tool_payload = json.loads(
        str(
            next(
                message["content"]
                for message in provider_impl.calls[1]["messages"]
                if isinstance(message, dict) and message.get("role") == "tool"
            )
        )
    )
    assert tool_payload["result"]["content_format"] == "line_numbered"
    assert "     1| " in tool_payload["result"]["content"]


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_caps_exclusive_owner_batched_reads(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text("print('orig')\n", encoding="utf-8")
    (tmp_path / "src" / "other.py").write_text("print('other')\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _ExclusiveOwnerReadThenWriteProvider(
        extra_read_path="src/other.py",
        expected_write_tool="file_write",
    )
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=6,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Patch src/demo.py with the smallest correct change.",
            metadata={
                "worker_id": "coding-build.worker-1",
                "exclusive_write_owner_path": "src/demo.py",
            },
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["target_files"] == ["src/demo.py"]
    assert (tmp_path / "src" / "demo.py").read_text(encoding="utf-8") == "print('patched')\n"

    read_tools = [
        tool for tool in response.raw["executed_tools"] if tool["tool_id"] == "file_read"
    ]
    assert read_tools[0]["ok"] is True
    assert read_tools[0]["arguments"]["path"] == "src/demo.py"
    assert read_tools[1]["ok"] is False
    assert str(read_tools[1]["error"]).startswith("exclusive_write_owner_read_scope:")
    assert any(
        event["event"] == "toolloop.exclusive_write_owner_read_scope_nudged"
        for event in events
    )


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_nudges_write_stage_after_empty_workspace_probe(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["list_directory", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _WriteStageFirstWriteNudgeProvider(workspace_root=tmp_path)
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_aggregator\nReturn the required candidate JSON.",
            user_prompt="Create the first bounded project slice.",
            metadata={"worker_id": "coding-build.aggregation.lead"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "list_directory",
                        "description": "List a directory.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding candidate.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_id": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["candidate_id"] == "candidate-after-nudge"
    assert any(event["event"] == "toolloop.write_stage_first_write_nudged" for event in events)


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_nudges_write_stage_after_stalled_analysis(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "rst.py").write_text("class RST: pass\n", encoding="utf-8")
    (src_dir / "fixedwidth.py").write_text("class FixedWidth: pass\n", encoding="utf-8")
    (src_dir / "core.py").write_text("class BaseReader: pass\n", encoding="utf-8")

    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _WriteStageStalledAnalysisNudgeProvider(workspace_root=tmp_path)
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=8,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Investigate the likely source files, then make the bounded patch.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding candidate.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_id": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["candidate_id"] == "candidate-after-stalled-analysis-nudge"
    nudge_event = next(
        event for event in events if event["event"] == "toolloop.write_stage_first_write_nudged"
    )
    assert nudge_event["reason"] == "stalled_analysis_before_first_write"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_nudges_worker_stage_after_stalled_analysis(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "rst.py").write_text("class RST: pass\n", encoding="utf-8")
    (src_dir / "fixedwidth.py").write_text("class FixedWidth: pass\n", encoding="utf-8")
    (src_dir / "core.py").write_text("class BaseReader: pass\n", encoding="utf-8")

    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _WriteStageStalledAnalysisNudgeProvider(
        workspace_root=tmp_path,
        output_kind="candidate_fragment",
    )
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=8,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Investigate the likely source files, then make the bounded worker patch.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["candidate_fragment"] == {"src/rst.py": "class RST: pass\n"}
    nudge_event = next(
        event for event in events if event["event"] == "toolloop.write_stage_first_write_nudged"
    )
    assert nudge_event["reason"] == "stalled_analysis_before_first_write"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_nudges_worker_stage_after_shell_stalled_analysis(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "rst.py").write_text("class RST: pass\n", encoding="utf-8")
    (src_dir / "fixedwidth.py").write_text("class FixedWidth: pass\n", encoding="utf-8")
    (src_dir / "core.py").write_text("class BaseReader: pass\n", encoding="utf-8")

    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_write", "shell_command"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _WriteStageShellStalledAnalysisNudgeProvider(
        output_kind="candidate_fragment",
    )
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=8,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Investigate the likely source files, then make the bounded worker patch.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "shell_command",
                        "description": "Execute a shell command.",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["candidate_fragment"] == {"src/rst.py": "class RST: pass\n"}
    nudge_event = next(
        event for event in events if event["event"] == "toolloop.write_stage_first_write_nudged"
    )
    assert nudge_event["reason"] == "stalled_analysis_before_first_write"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_recovers_after_disabled_read_in_write_only_stage(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "rst.py").write_text("class RST: pass\n", encoding="utf-8")
    (src_dir / "fixedwidth.py").write_text("class FixedWidth: pass\n", encoding="utf-8")
    (src_dir / "core.py").write_text("class BaseReader: pass\n", encoding="utf-8")

    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_edit", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _WriteStageDisabledReadRecoveryProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=10,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Investigate the likely source files, then make the bounded patch.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "content": {"type": "string"},
                            },
                            "required": ["path"],
                            "anyOf": [{"required": ["start_line"]}, {"required": ["edits"]}],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding candidate.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_id": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["candidate_id"] == "candidate-after-disabled-read-recovery"
    availability_event = next(
        event for event in events if event["event"] == "toolloop.tool_availability_nudged"
    )
    assert availability_event["tool_ids"] == ["file_read"]
    assert availability_event["enabled_tools"] == ["file_edit", "file_write"]
    read_budget_event = next(
        event for event in events if event["event"] == "toolloop.write_stage_final_read_consumed"
    )
    assert read_budget_event["enabled_tools"] == ["file_edit", "file_write"]
    assert (tmp_path / "src" / "rst.py").read_text(encoding="utf-8") == "class RST:\n    pass\n"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_replies_to_skipped_final_read_tool_calls(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "rst.py").write_text("class RST: pass\n", encoding="utf-8")
    (src_dir / "fixedwidth.py").write_text("class FixedWidth: pass\n", encoding="utf-8")
    (src_dir / "core.py").write_text("class BaseReader: pass\n", encoding="utf-8")

    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_edit", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=_WriteStageFinalReadBatchThenSuccessProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=10,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn JSON.",
            user_prompt="Inspect the likely files, then make the bounded patch.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "content": {"type": "string"},
                            },
                            "required": ["path"],
                            "anyOf": [{"required": ["start_line"]}, {"required": ["edits"]}],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding candidate.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_id": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    assert json.loads(response.text) == {
        "candidate_id": "write-stage-final-read-batch-recovered"
    }
    read_budget_event = next(
        event for event in events if event["event"] == "toolloop.write_stage_final_read_consumed"
    )
    assert read_budget_event["blocked_by_tool_call_ids"] == ["call-4", "call-5"]
    assert (tmp_path / "src" / "rst.py").read_text(encoding="utf-8") == "class RST:\n    pass\n"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_reprompts_after_prose_only_stop_in_direct_write_mode(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "rst.py").write_text("class RST: pass\n", encoding="utf-8")
    (src_dir / "fixedwidth.py").write_text("class FixedWidth: pass\n", encoding="utf-8")
    (src_dir / "core.py").write_text("class BaseReader: pass\n", encoding="utf-8")

    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_edit", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=_WriteStageDirectWriteRepromptProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=12,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn JSON.",
            user_prompt="Inspect the likely files, then make the bounded patch.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "content": {"type": "string"},
                            },
                            "required": ["path"],
                            "anyOf": [{"required": ["start_line"]}, {"required": ["edits"]}],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["target_files"] == ["src/rst.py"]
    assert payload["change_summary"] == (
        "Materialized the bounded RST patch after the direct-write reminder."
    )
    reprompt_event = next(
        event for event in events if event["event"] == "toolloop.write_stage_direct_write_nudged"
    )
    assert reprompt_event["reason"] == "returned_without_materializing_workspace_patch"
    assert reprompt_event["enabled_tools"] == ["file_edit", "file_write"]
    assert (tmp_path / "src" / "rst.py").read_text(encoding="utf-8") == "class RST:\n    pass\n"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_blocks_temp_helper_during_direct_write(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "rst.py").write_text("class RST: pass\n", encoding="utf-8")
    (src_dir / "fixedwidth.py").write_text("class FixedWidth: pass\n", encoding="utf-8")
    (src_dir / "core.py").write_text("class BaseReader: pass\n", encoding="utf-8")

    helper_path = "/tmp/dan-runtime-direct-helper.py"
    Path(helper_path).unlink(missing_ok=True)
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_edit", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=_WriteStageDirectWriteTempHelperProvider(helper_path=helper_path),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=14,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn JSON.",
            user_prompt="Inspect the likely files, then make the bounded patch.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "content": {"type": "string"},
                            },
                            "required": ["path"],
                            "anyOf": [{"required": ["start_line"]}, {"required": ["edits"]}],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["target_files"] == ["src/rst.py"]
    assert payload["change_summary"] == (
        "Materialized the bounded RST patch after a temporary helper write was blocked."
    )
    helper_event = next(
        event for event in events if event["event"] == "toolloop.write_stage_helper_path_blocked"
    )
    assert helper_event["reasons"] == ["temporary_external_helper_before_workspace_patch"]
    assert helper_event["enabled_tools"] == ["file_edit", "file_write"]
    assert not Path(helper_path).exists()
    assert (tmp_path / "src" / "rst.py").read_text(encoding="utf-8") == "class RST:\n    pass\n"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_falls_back_after_repeated_prose_only_direct_write_ignores(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "rst.py").write_text("class RST: pass\n", encoding="utf-8")
    (src_dir / "fixedwidth.py").write_text("class FixedWidth: pass\n", encoding="utf-8")
    (src_dir / "core.py").write_text("class BaseReader: pass\n", encoding="utf-8")

    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_edit", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=_WriteStageDirectWriteGuardrailIgnoredProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=12,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn JSON.",
            user_prompt="Inspect the likely files, then make the bounded patch.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "content": {"type": "string"},
                            },
                            "required": ["path"],
                            "anyOf": [{"required": ["start_line"]}, {"required": ["edits"]}],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["fallback_reason"] == "write_stage_direct_write_guardrail_unheeded"
    assert payload["target_files"] == ["src/rst.py"]
    assert payload["candidate_fragment"] == "Change `src/rst.py` to materialize the bounded patch."
    assert any(
        "No checked-out workspace file was modified" in risk
        for risk in payload["risks"]
    )
    assert response.raw["stop_reason"] == "write_stage_direct_write_guardrail_unheeded"
    assert (tmp_path / "src" / "rst.py").read_text(encoding="utf-8") == "class RST: pass\n"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_finalizes_read_only_worker_after_stalled_analysis(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "rst.py").write_text("class RST: pass\n", encoding="utf-8")
    (src_dir / "fixedwidth.py").write_text("class FixedWidth: pass\n", encoding="utf-8")
    (src_dir / "core.py").write_text("class BaseReader: pass\n", encoding="utf-8")
    (src_dir / "tests.py").write_text("def test_rst():\n    pass\n", encoding="utf-8")

    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _ReadOnlyFinalizeAfterStalledAnalysisProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=8,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Inspect the checked-out repo and return one bounded coding contribution.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["candidate_fragment"] == {"src/rst.py": "class RST: pass\n"}
    finalize_event = next(
        event for event in events if event["event"] == "toolloop.read_only_finalize_forced"
    )
    assert finalize_event["reason"] == "stalled_analysis_in_checked_out_repo"
    assert finalize_event["finalize_mode"] == "coding_worker"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_finalizes_read_only_validator_after_stalled_analysis(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "rst.py").write_text("class RST: pass\n", encoding="utf-8")
    (src_dir / "fixedwidth.py").write_text("class FixedWidth: pass\n", encoding="utf-8")
    (src_dir / "core.py").write_text("class BaseReader: pass\n", encoding="utf-8")
    (src_dir / "tests.py").write_text("def test_rst():\n    pass\n", encoding="utf-8")

    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _ReadOnlyFinalizeAfterStalledAnalysisProvider(output_kind="validator")
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=8,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: validator_synthesizer\nReturn the required validation JSON.",
            user_prompt="Inspect the checked-out repo and return the bounded validation report.",
            metadata={"worker_id": "coding-build.validator.lead"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return the validation result for the aggregated coding candidate.",
                expected_return_shape=json.dumps(
                    {
                        "passed": "<required>",
                        "overall_score": "<required>",
                        "dimension_scores": "<required>",
                        "repair_brief": "<required>",
                        "missing_requirements": "<required>",
                        "comparison_note": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["passed"] is True
    assert payload["overall_score"] == pytest.approx(0.98)
    assert payload["missing_requirements"] == []
    finalize_event = next(
        event for event in events if event["event"] == "toolloop.read_only_finalize_forced"
    )
    assert finalize_event["reason"] == "stalled_analysis_in_checked_out_repo"
    assert finalize_event["finalize_mode"] == "validator"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_finalizes_write_stage_after_verified_patch(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_write", "file_read", "shell_command"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _WriteStageFinalizeAfterVerificationProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=8,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Make the bounded RST patch and validate it enough to return the worker contribution.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "shell_command",
                        "description": "Run a shell command.",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["target_files"] == ["src/rst.py"]
    finalize_event = next(
        event for event in events if event["event"] == "toolloop.write_stage_finalize_forced"
    )
    assert finalize_event["reason"] == "non_verification_shell_after_verification"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_finalizes_write_stage_after_temp_helper_churn(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    external_helper_path = str(Path(tempfile.gettempdir()) / "dan_runtime_tmp_helper.py")

    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _WriteStageFinalizeAfterTempHelperProvider(
        external_helper_path=external_helper_path
    )
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=6,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Make the bounded RST patch and stop once the workspace diff is ready.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["target_files"] == ["src/rst.py"]
    finalize_event = next(
        event for event in events if event["event"] == "toolloop.write_stage_finalize_forced"
    )
    assert finalize_event["reason"] == "temporary_external_write_after_workspace_patch"


def test_write_capable_finalize_reason_ignores_disabled_direct_write_tool_after_workspace_patch(
    tmp_path,
) -> None:
    request = CompletionRequest(
        model="gpt-test",
        system_prompt="Role: coding_worker\nReturn the required coding JSON.",
        user_prompt="Finalize the bounded candidate.",
        metadata={"worker_id": "coding-build.worker-1"},
        output_contract=OutputContract(
            definition_of_done="Return one bounded coding contribution.",
            expected_return_shape=json.dumps(
                {
                    "candidate_fragment": "<required>",
                    "change_summary": "<required>",
                    "target_files": "<required>",
                    "test_plan": "<required>",
                    "risks": "<required>",
                },
                sort_keys=True,
            ),
        ),
    )

    reason = local_runtime_module._write_capable_coding_stage_finalize_reason(
        request=request,
        tool_ids=["file_edit", "file_write"],
        executed_tools=[
            {
                "tool_id": "file_edit",
                "ok": True,
                "arguments": {
                    "path": "src/rst.py",
                    "start_line": 1,
                    "end_line": 1,
                    "content": "class RST:\n    pass\n",
                },
                "result": {
                    "path": "src/rst.py",
                    "mode": "replace",
                    "start_line": 1,
                    "end_line": 1,
                },
            }
        ],
        workspace_root=tmp_path,
        disabled_tool_ids=["file_edit"],
    )

    assert reason is None


def test_workspace_helper_write_does_not_satisfy_write_stage_materialization(
    tmp_path,
) -> None:
    request = CompletionRequest(
        model="gpt-test",
        system_prompt="Role: coding_worker\nReturn the required coding JSON.",
        user_prompt="Apply the bounded patch.",
        metadata={"worker_id": "coding-build.worker-1"},
        output_contract=OutputContract(
            definition_of_done="Return one bounded coding contribution.",
            expected_return_shape=json.dumps(
                {
                    "candidate_fragment": "<required>",
                    "change_summary": "<required>",
                    "target_files": "<required>",
                    "test_plan": "<required>",
                    "risks": "<required>",
                },
                sort_keys=True,
            ),
        ),
    )
    executed_tools = [
        {
            "tool_id": "file_write",
            "ok": True,
            "arguments": {
                "path": "tmp_read.py",
                "content": "with open('src/rst.py') as handle:\n    print(handle.read())\n",
            },
            "result": {"path": str(tmp_path / "tmp_read.py"), "mode": "overwrite"},
        }
    ]

    assert (
        local_runtime_module._successful_workspace_mutation_paths(
            executed_tools,
            workspace_root=tmp_path,
        )
        == []
    )
    assert (
        local_runtime_module._write_capable_coding_stage_finalize_reason(
            request=request,
            tool_ids=["file_write"],
            executed_tools=executed_tools,
            workspace_root=tmp_path,
            disabled_tool_ids=["file_edit"],
        )
        is None
    )
    assert (
        local_runtime_module._write_capable_coding_stage_direct_write_required_reason(
            request=request,
            tool_ids=["file_write"],
            executed_tools=executed_tools,
            workspace_root=tmp_path,
            direct_write_required=True,
        )
        == "returned_without_materializing_workspace_patch"
    )


def test_noop_file_edit_and_dump_helper_do_not_satisfy_write_stage_materialization(
    tmp_path,
) -> None:
    request = CompletionRequest(
        model="gpt-test",
        system_prompt="Role: coding_worker\nReturn the required coding JSON.",
        user_prompt="Apply the bounded patch.",
        metadata={"worker_id": "coding-build.worker-1"},
        output_contract=OutputContract(
            definition_of_done="Return one bounded coding contribution.",
            expected_return_shape=json.dumps(
                {
                    "candidate_fragment": "<required>",
                    "change_summary": "<required>",
                    "target_files": "<required>",
                    "test_plan": "<required>",
                    "risks": "<required>",
                },
                sort_keys=True,
            ),
        ),
    )
    executed_tools = [
        {
            "tool_id": "file_edit",
            "ok": True,
            "arguments": {
                "path": "src/rst.py",
                "start_line": 1,
                "end_line": 1,
                "content": "# unchanged\n",
            },
            "result": {
                "path": "src/rst.py",
                "mode": "replace",
                "changed": False,
                "no_op": True,
            },
        },
        {
            "tool_id": "file_write",
            "ok": True,
            "arguments": {
                "path": "dump_separable_out.txt",
                "content": "placeholder",
            },
            "result": {"path": str(tmp_path / "dump_separable_out.txt"), "mode": "overwrite"},
        },
        {
            "tool_id": "file_write",
            "ok": True,
            "arguments": {
                "path": "run_dump.sh",
                "content": "python dump_separable.py\n",
            },
            "result": {"path": str(tmp_path / "run_dump.sh"), "mode": "overwrite"},
        },
    ]

    assert (
        local_runtime_module._successful_workspace_mutation_paths(
            executed_tools,
            workspace_root=tmp_path,
        )
        == []
    )
    assert (
        local_runtime_module._write_capable_coding_stage_finalize_reason(
            request=request,
            tool_ids=["file_write", "file_edit"],
            executed_tools=executed_tools,
            workspace_root=tmp_path,
            disabled_tool_ids=["file_write"],
        )
        is None
    )
    assert (
        local_runtime_module._write_capable_coding_stage_direct_write_required_reason(
            request=request,
            tool_ids=["file_write", "file_edit"],
            executed_tools=executed_tools,
            workspace_root=tmp_path,
            direct_write_required=True,
        )
        == "returned_without_materializing_workspace_patch"
    )


def test_write_stage_finalize_flags_workspace_helper_after_real_patch(
    tmp_path,
) -> None:
    request = CompletionRequest(
        model="gpt-test",
        system_prompt="Role: coding_worker\nReturn the required coding JSON.",
        user_prompt="Apply the bounded patch.",
        metadata={"worker_id": "coding-build.worker-1"},
        output_contract=OutputContract(
            definition_of_done="Return one bounded coding contribution.",
            expected_return_shape=json.dumps(
                {
                    "candidate_fragment": "<required>",
                    "change_summary": "<required>",
                    "target_files": "<required>",
                    "test_plan": "<required>",
                    "risks": "<required>",
                },
                sort_keys=True,
            ),
        ),
    )
    executed_tools = [
        {
            "tool_id": "file_edit",
            "ok": True,
            "arguments": {
                "path": "src/rst.py",
                "start_line": 1,
                "end_line": 1,
                "content": "class RST:\n    pass\n",
            },
            "result": {"path": "src/rst.py", "mode": "replace"},
        },
        {
            "tool_id": "file_write",
            "ok": True,
            "arguments": {
                "path": "tmp_read.py",
                "content": "with open('src/rst.py') as handle:\n    print(handle.read())\n",
            },
            "result": {"path": str(tmp_path / "tmp_read.py"), "mode": "overwrite"},
        },
    ]

    assert local_runtime_module._successful_workspace_mutation_paths(
        executed_tools,
        workspace_root=tmp_path,
    ) == ["src/rst.py"]
    assert (
        local_runtime_module._write_capable_coding_stage_finalize_reason(
            request=request,
            tool_ids=["file_write", "file_edit"],
            executed_tools=executed_tools,
            workspace_root=tmp_path,
        )
        == "temporary_workspace_helper_write_after_workspace_patch"
    )


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_synthesizes_candidate_when_forced_finalize_is_ignored(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_write", "file_read", "shell_command"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _WriteStageFinalizeAfterVerificationProvider(
        ignore_forced_finalize=True
    )
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=8,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Role: coding_worker\nReturn the required coding JSON.",
            user_prompt="Make the bounded RST patch and validate it enough to return the worker contribution.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "shell_command",
                        "description": "Run a shell command.",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                },
            ],
            output_contract=OutputContract(
                definition_of_done="Return one bounded coding contribution.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_fragment": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
        )
    )

    payload = json.loads(response.text)
    assert payload["candidate_fragment"] == {"src/rst.py": "class RST:\n    pass\n"}
    assert payload["readback_files"] == ["src/rst.py"]
    assert payload["target_files"] == ["src/rst.py"]
    assert payload["synthesized_from_tool_evidence"] is True
    assert payload["fallback_reason"] == "forced_finalize_guardrail_unheeded"


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_streams_text_only_responses_when_enabled(tmp_path) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _StreamingTextProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        stream_text_responses=True,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Answer directly.",
            user_prompt="Summarize the current state.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[],
        )
    )

    assert response.text == "Streaming preview"
    assert provider_impl.complete_calls == 0
    assert len(provider_impl.stream_calls) == 1
    assert _event_names(events) == [
        "model.requested",
        "model.stream.started",
        "model.stream.delta",
        "model.stream.delta",
        "model.stream.completed",
        "model.responded",
        "completion.completed",
    ]
    assert events[5]["streamed"] is True


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_streams_tool_rounds_without_losing_tool_calls(
    tmp_path,
) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "existing.txt").write_text("present\n", encoding="utf-8")
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _ToolAwareStreamingProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        stream_text_responses=True,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Use the tool if needed.",
            user_prompt="Read the file, then return JSON.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ],
        )
    )

    assert json.loads(response.text) == {"candidate_id": "tool-round-complete"}
    assert provider_impl.complete_calls == 0
    assert len(provider_impl.stream_calls) == 2
    assert _event_names(events) == [
        "model.requested",
        "model.stream.started",
        "model.stream.delta",
        "model.stream.completed",
        "model.responded",
        "tool.started",
        "tool.completed",
        "model.requested",
        "model.stream.started",
        "model.stream.delta",
        "model.stream.completed",
        "model.responded",
        "completion.completed",
    ]
    assert events[4]["tool_calls"] == ["file_read"]
    assert events[4]["streamed"] is True
    assert events[6]["result"]["line_count"] == 1


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_adds_structured_tool_policy(tmp_path) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "existing.txt").write_text("present\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["list_directory", "file_read", "file_edit", "file_write", "shell_command", "web_search", "git_status"],
        workspace_root=tmp_path,
    )
    provider_impl = _ManyRoundToolLoopProvider(rounds=0)
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=8,
    )

    await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Use local tools well.",
            user_prompt="Inspect and report.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "list_directory",
                        "description": "List a directory.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "shell_command",
                        "description": "Run a shell command.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "git_status",
                        "description": "Get git status.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ],
        )
    )

    messages = provider_impl.calls[0]["messages"]
    assert messages[1]["role"] == "system"
    policy = str(messages[1]["content"])
    assert "Prefer the most specific structured tool available" in policy
    assert "Use `list_directory` for directory inspection" in policy
    assert "Use `file_edit` for targeted edits to existing files" in policy
    assert "compact edit-intent check" in policy
    assert "delete text with `mode=\"delete\"` and no replacement fields" in policy
    assert "old_string` plus `new_string` copied from a recent `file_read` and no delete mode" in policy
    assert "For line-based edits, always include `path` and `start_line`" in policy
    assert "prefer one `file_edit` call with `edits=[...]`" in policy
    assert "every batch item must include `start_line` or a unique `old_string`/`new_string` pair" in policy
    assert "Do not use shell heredocs" in policy
    assert "Use `web_search` for live external lookups or lightweight web research" in policy
    assert "Do not use shell `git` commands when git tools are unavailable" in policy


def test_local_organism_runtime_filters_git_tools_outside_git_repo(tmp_path) -> None:
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "git_status", "git_diff", "git_log"],
        workspace_root=tmp_path,
    )

    assert runtime.tool_ids == ["file_read"]


def test_local_organism_runtime_default_basket_includes_web_search(tmp_path) -> None:
    runtime = LocalOrganismToolRuntime(workspace_root=tmp_path)

    assert runtime.tool_ids == [
        "list_directory",
        "file_read",
        "workspace_check",
        "file_edit",
        "file_write",
        "shell_command",
        "web_search",
    ]


@pytest.mark.asyncio
async def test_local_organism_runtime_normalizes_file_read_offset_limit_aliases(
    tmp_path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_file_read(**kwargs):
        captured["kwargs"] = dict(kwargs)
        return {"content": "", "line_count": 0, "size": 0, "path": kwargs["path"]}

    monkeypatch.setattr(
        local_runtime_module,
        "get_all_tools",
        lambda: {
            "file_read": (
                _fake_file_read,
                {
                    "tool_id": "file_read",
                    "category": "file",
                    "description": "fake",
                },
            )
        },
    )
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read"],
        workspace_root=tmp_path,
    )

    await runtime.call(
        "file_read",
        {
            "file_path": "src/demo.py",
            "offset": 10,
            "limit": 5,
        },
        worker_id="coding-build.worker-1",
    )

    assert captured["kwargs"] == {
        "path": "src/demo.py",
        "file_path": "src/demo.py",
        "offset": 10,
        "limit": 5,
        "start_line": 11,
        "end_line": 15,
    }


@pytest.mark.asyncio
async def test_local_organism_runtime_normalizes_web_search_queries_alias(
    tmp_path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_web_search(*, query: str, **kwargs):
        captured["query"] = query
        captured["kwargs"] = dict(kwargs)
        return {"results": [], "count": 0}

    monkeypatch.setattr(
        local_runtime_module,
        "get_all_tools",
        lambda: {
            "web_search": (
                _fake_web_search,
                {
                    "tool_id": "web_search",
                    "category": "web",
                    "description": "fake",
                },
            )
        },
    )
    runtime = LocalOrganismToolRuntime(
        tool_ids=["web_search"],
        workspace_root=tmp_path,
    )

    result = await runtime.call(
        "web_search",
        {
            "queries": [
                "first query",
                "second query",
            ]
        },
        worker_id="deep-research.reader-a",
    )

    assert result["count"] == 0
    assert captured["query"] == "first query"


@pytest.mark.asyncio
async def test_local_organism_runtime_rejects_web_search_without_query_or_url(
    tmp_path,
) -> None:
    runtime = LocalOrganismToolRuntime(
        tool_ids=["web_search"],
        workspace_root=tmp_path,
    )

    metadata = runtime.metadata_for("web_search")
    assert metadata["parameters"]["anyOf"] == [
        {"required": ["query"]},
        {"required": ["url"]},
    ]

    with pytest.raises(ValueError) as excinfo:
        await runtime.call(
            "web_search",
            {},
            worker_id="deep-research.reader-a",
        )

    error_text = str(excinfo.value)
    assert "tool_arguments_invalid:" in error_text
    assert "web_search" in error_text
    assert "query" in error_text
    assert "url" in error_text


@pytest.mark.asyncio
async def test_local_organism_runtime_rejects_missing_anyof_arguments_for_file_edit(
    tmp_path,
) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("hello\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_edit"],
        workspace_root=tmp_path,
    )

    with pytest.raises(ValueError) as excinfo:
        await runtime.call(
            "file_edit",
            {"path": "notes.txt"},
            worker_id="coding-build.worker-1",
        )

    error_text = str(excinfo.value)
    assert "tool_arguments_invalid:" in error_text
    assert "file_edit" in error_text
    assert "start_line" in error_text
    assert "edits" in error_text


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_upgrades_request_tool_schemas_from_runtime_metadata(
    tmp_path,
) -> None:
    provider = _CaptureToolSchemaProvider()
    runtime = LocalOrganismToolRuntime(
        tool_ids=["web_search", "file_read", "file_edit", "list_directory"],
        workspace_root=tmp_path,
    )
    runtime_file_edit_parameters = runtime.metadata_for("file_edit")["parameters"]
    assert "anyOf" in runtime_file_edit_parameters
    completion_provider = ToolLoopCompletionProvider(
        provider=provider,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=1,
        max_tool_calls=0,
    )
    request = CompletionRequest(
        model="gpt-test",
        system_prompt="Ground the task.",
        user_prompt="Use the available tools.",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "web_search",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_read",
                    "description": "file_read",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_edit",
                    "description": "file_edit",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ],
        metadata={"worker_id": "deep-research.reader-a"},
    )

    await completion_provider.complete(request)

    assert len(provider.calls) == 1
    tool_schemas = provider.calls[0]["tools"]
    assert isinstance(tool_schemas, list)
    schema_by_name = {
        tool["function"]["name"]: tool["function"]
        for tool in tool_schemas
        if isinstance(tool, dict)
    }
    assert schema_by_name["web_search"]["parameters"]["anyOf"] == [
        {"required": ["query"]},
        {"required": ["url"]},
    ]
    assert "query" in schema_by_name["web_search"]["parameters"]["properties"]
    assert "path" in schema_by_name["file_read"]["parameters"]["properties"]
    file_edit_parameters = schema_by_name["file_edit"]["parameters"]
    assert "anyOf" not in file_edit_parameters
    assert file_edit_parameters["required"] == ["path"]
    assert set(file_edit_parameters["properties"]) >= {
        "path",
        "start_line",
        "end_line",
        "content",
        "mode",
        "old_string",
        "new_string",
        "edits",
    }
    edit_item = file_edit_parameters["properties"]["edits"]["items"]
    assert "anyOf" not in edit_item
    assert "old_string" in edit_item["properties"]
    normalized_file_edit = OpenAIProvider._normalize_json_schema_for_openai_compatibility(
        file_edit_parameters
    )
    assert len(json.dumps(normalized_file_edit)) < 2500


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_forces_validator_rounds_to_read_only_tools(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    external_path = Path(tempfile.gettempdir()) / f"dan-validator-helper-{time.time_ns()}.py"
    if external_path.exists():
        external_path.unlink()

    provider_impl = _ValidatorFileWriteThenSuccessProvider(external_path=external_path)
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_edit", "file_write", "shell_command", "git_diff"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        event_callback=events.append,
    )

    try:
        response = await provider.complete(
            CompletionRequest(
                model="gpt-test",
                system_prompt="Role: validator_synthesizer\nReturn the required validation JSON.",
                user_prompt="Review the bounded candidate and return the validation report.",
                metadata={
                    "worker_id": "coding-build.worker-1",
                    "organ_id": "coding-build.validator",
                    "organism_stage": "workers",
                },
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "description": "Read a file.",
                            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "file_edit",
                            "description": "Edit a file.",
                            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "file_write",
                            "description": "Write a file.",
                            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "shell_command",
                            "description": "Run a shell command.",
                            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "git_diff",
                            "description": "Read the git diff.",
                            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                        },
                    },
                ],
                output_contract=OutputContract(
                    definition_of_done="Return the validation result for the aggregated coding candidate.",
                    expected_return_shape=json.dumps(
                        {
                            "passed": "<required>",
                            "overall_score": "<required>",
                            "dimension_scores": "<required>",
                            "repair_brief": "<required>",
                            "missing_requirements": "<required>",
                            "comparison_note": "<required>",
                        },
                        sort_keys=True,
                    ),
                ),
            )
        )
    finally:
        if external_path.exists():
            external_path.unlink()

    payload = json.loads(response.text)
    assert payload["passed"] is True
    assert payload["overall_score"] == pytest.approx(0.94)
    assert not external_path.exists()

    assert len(provider_impl.calls) == 2
    first_call_tools = provider_impl.calls[0]["tools"]
    assert isinstance(first_call_tools, list)
    first_call_tool_names = [
        str(tool.get("function", {}).get("name") or "")
        for tool in first_call_tools
        if isinstance(tool, dict)
    ]
    assert first_call_tool_names == ["file_read"]

    narrow_event = next(
        event for event in events if event["event"] == "toolloop.validator_tools_narrowed"
    )
    assert set(narrow_event["dropped_tools"]) == {"file_edit", "file_write", "shell_command"}
    availability_event = next(
        event for event in events if event["event"] == "toolloop.tool_availability_nudged"
    )
    assert availability_event["tool_ids"] == ["file_write"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_id", "raw_path"),
    [
        ("git_status", "."),
        ("git_diff", "."),
        ("git_log", "src/demo.py"),
    ],
)
async def test_local_organism_runtime_normalizes_git_paths_to_workspace(
    tmp_path,
    monkeypatch,
    tool_id: str,
    raw_path: str,
) -> None:
    captured: dict[str, object] = {}
    (tmp_path / ".git").mkdir()

    async def _fake_git_tool(**kwargs):
        captured["kwargs"] = dict(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        local_runtime_module,
        "get_all_tools",
        lambda: {
            tool_id: (
                _fake_git_tool,
                {
                    "tool_id": tool_id,
                    "category": "git",
                    "description": "fake",
                },
            )
        },
    )
    runtime = LocalOrganismToolRuntime(
        tool_ids=[tool_id],
        workspace_root=tmp_path,
    )

    await runtime.call(
        tool_id,
        {"path": raw_path},
        worker_id="coding-build.validator",
    )

    expected_path = tmp_path if raw_path == "." else (tmp_path / raw_path).resolve()
    assert captured["kwargs"] == {"path": str(expected_path)}


@pytest.mark.asyncio
async def test_tool_loop_completion_provider_warns_against_pattern_based_code_search(
    tmp_path,
) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "existing.txt").write_text("present\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["list_directory", "file_read", "file_edit", "file_write", "shell_command", "web_search", "git_status"],
        workspace_root=tmp_path,
    )
    provider_impl = _ManyRoundToolLoopProvider(rounds=0)
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=8,
    )

    await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Use local tools well.",
            user_prompt="Inspect and report.",
            metadata={"worker_id": "coding-build.worker-1"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "list_directory",
                        "description": "List a directory.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "description": "Edit a file.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "shell_command",
                        "description": "Run a shell command.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "git_status",
                        "description": "Get git status.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ],
        )
    )

    messages = provider_impl.calls[0]["messages"]
    policy = str(messages[1]["content"])
    assert "Do not rely on shell `grep`/`sed`/`awk`, regex searches" in policy
    assert "Do not depend on regex, shell pattern matching, or exact text-match replacement" in policy


@pytest.mark.asyncio
async def test_local_organism_tool_runtime_can_deny_a_tool_call(tmp_path) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_write"],
        workspace_root=tmp_path,
        approval_callback=lambda tool_id, arguments, metadata: False,
        event_callback=events.append,
    )

    with pytest.raises(PermissionError, match="tool_call_denied:file_write"):
        await runtime.call(
            "file_write",
            {
                "path": "notes/blocked.txt",
                "content": "blocked\n",
            },
            worker_id="coding-build.worker-2",
        )

    assert not (tmp_path / "notes/blocked.txt").exists()
    assert _event_names(events) == ["tool.started", "tool.denied"]
    assert events[1]["tool_id"] == "file_write"
    assert events[0]["worker_id"] == "coding-build.worker-2"
    assert events[1]["worker_id"] == "coding-build.worker-2"


def test_attach_local_tooling_to_reference_organism_keeps_planner_abstract() -> None:
    organism = attach_local_tooling_to_reference_organism(
        project_execution_reference_organism(model="gpt-test"),
        tool_ids=["file_read", "file_edit", "file_write", "shell_command"],
    )

    assert organism.planner_worker.tool_ids == []
    assert organism.coding_organ.lead_worker.tool_ids == ["file_read", "file_edit", "file_write", "shell_command"]
    assert organism.research_organ.lead_worker.tool_ids == ["file_read"]
    assert organism.synthesis_organ.lead_worker.tool_ids == ["file_read"]


def test_attach_local_tooling_to_reference_organism_gives_research_read_only_web_search() -> None:
    organism = attach_local_tooling_to_reference_organism(
        project_execution_reference_organism(model="gpt-test"),
        tool_ids=[
            "list_directory",
            "file_read",
            "file_write",
            "shell_command",
            "web_search",
            "git_status",
            "git_diff",
            "git_log",
        ],
    )

    assert organism.research_organ.lead_worker.tool_ids == [
        "web_search",
        "file_read",
        "list_directory",
    ]
    assert organism.research_organ.tissue is not None
    assert organism.research_organ.tissue.members[0].worker.tool_ids == [
        "web_search",
        "file_read",
        "list_directory",
    ]


def test_attach_local_tooling_to_coding_organism_keeps_roles_separated() -> None:
    organism = attach_local_tooling_to_coding_organism(
        coding_execution_organism(model="gpt-test"),
        tool_ids=[
            "list_directory",
            "file_read",
            "file_edit",
            "file_write",
            "shell_command",
            "web_search",
            "git_status",
            "git_diff",
            "git_log",
        ],
    )

    assert organism.orchestrator_worker.tool_ids == []
    assert organism.worker_tool_ids == [
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
    assert organism.parallel_worker_tool_ids == [
        "list_directory",
        "file_read",
        "web_search",
        "git_status",
        "git_diff",
        "git_log",
    ]
    assert organism.aggregator_organ.lead_worker.tool_ids == [
        "file_read",
        "file_edit",
        "file_write",
        "git_diff",
    ]
    assert organism.validator_organ.lead_worker.tool_ids == [
        "list_directory",
        "file_read",
        "web_search",
        "git_status",
        "git_diff",
        "git_log",
    ]


def test_tool_use_policy_warns_when_tool_set_is_read_only() -> None:
    policy = local_runtime_module._tool_use_policy(["file_read", "web_search"])

    assert "Runtime platform context" in policy
    assert "OS/platform:" in policy
    assert "Default shell:" in policy
    assert "This tool set is read-only" in policy
    assert "Do not try to create files through `file_read`" in policy
    assert "later write-capable stage" in policy
    assert "generic architecture brainstorming" in policy
    assert "library documentation" in policy


def test_tool_use_policy_describes_shell_command_as_real_terminal_tool() -> None:
    policy = local_runtime_module._tool_use_policy(
        ["file_read", "file_write", "shell_command"]
    )

    assert "Runtime platform context" in policy
    assert "command -v <name>" in policy
    assert "filesystem transfers" in policy
    assert "local platform's command-line toolbox" in policy
    assert "actively consider what existing CLI" in policy
    assert "`mkdir`, `cp`, `mv`, `rsync`, `find`, `du`" in policy
    assert "faithfully copy, move, archive, extract, checksum, or execute" in policy
    assert "Do not install new packages or CLIs unless" in policy


def test_write_stage_narrowing_keeps_shell_command_when_enabled() -> None:
    tool_schemas = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in ("list_directory", "file_read", "file_write", "file_edit", "shell_command")
    ]

    narrowed = local_runtime_module._write_stage_tool_schemas(
        tool_schemas,
        allow_final_read=False,
    )
    direct = local_runtime_module._direct_write_tool_schemas(tool_schemas)

    assert [
        tool.get("function", {}).get("name")
        for tool in narrowed
        if isinstance(tool, dict)
    ] == ["file_write", "file_edit", "shell_command"]
    assert [
        tool.get("function", {}).get("name")
        for tool in direct
        if isinstance(tool, dict)
    ] == ["file_write", "file_edit", "shell_command"]


def test_compact_tool_payload_for_prompt_truncates_oversized_nested_text() -> None:
    long_text = "x" * 25_000
    payload = {
        "ok": True,
        "result": {
            "path": "src/demo.py",
            "content": long_text,
            "nested": {"error": long_text},
        },
    }

    compacted = local_runtime_module._compact_tool_payload_for_prompt(payload)

    assert compacted["prompt_payload_compacted"] is True
    assert compacted["result"]["path"] == "src/demo.py"
    assert compacted["result"]["content"] != long_text
    assert "[truncated " in compacted["result"]["content"]
    assert compacted["result"]["nested"]["error"] != long_text


def test_compact_messages_for_provider_prompt_shrinks_older_file_reads() -> None:
    old_content = "old-line\n" * 700
    middle_content = "middle-line\n" * 700
    next_content = "next-line\n" * 700
    recent_content = "recent-line\n" * 700
    latest_content = "latest-line\n" * 700
    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "Inspect five files."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-old", "function": {"name": "file_read", "arguments": "{}"}},
                {"id": "call-middle", "function": {"name": "file_read", "arguments": "{}"}},
                {"id": "call-next", "function": {"name": "file_read", "arguments": "{}"}},
                {"id": "call-recent", "function": {"name": "file_read", "arguments": "{}"}},
                {"id": "call-latest", "function": {"name": "file_read", "arguments": "{}"}},
            ],
        },
        {
            "role": "tool",
            "name": "file_read",
            "tool_call_id": "call-old",
            "content": json.dumps(
                {"ok": True, "result": {"path": "src/old.py", "content": old_content}},
                sort_keys=True,
            ),
        },
        {
            "role": "tool",
            "name": "file_read",
            "tool_call_id": "call-middle",
            "content": json.dumps(
                {"ok": True, "result": {"path": "src/middle.py", "content": middle_content}},
                sort_keys=True,
            ),
        },
        {
            "role": "tool",
            "name": "file_read",
            "tool_call_id": "call-next",
            "content": json.dumps(
                {"ok": True, "result": {"path": "src/next.py", "content": next_content}},
                sort_keys=True,
            ),
        },
        {
            "role": "tool",
            "name": "file_read",
            "tool_call_id": "call-recent",
            "content": json.dumps(
                {"ok": True, "result": {"path": "src/recent.py", "content": recent_content}},
                sort_keys=True,
            ),
        },
        {
            "role": "tool",
            "name": "file_read",
            "tool_call_id": "call-latest",
            "content": json.dumps(
                {"ok": True, "result": {"path": "src/latest.py", "content": latest_content}},
                sort_keys=True,
            ),
        },
    ]

    compacted_messages, stats = local_runtime_module._compact_messages_for_provider_prompt(
        messages,
    )

    assert compacted_messages is not messages
    assert stats["prompt_context_file_read_messages"] == 5
    assert stats["prompt_context_compacted_file_reads"] == 1
    assert stats["prompt_context_saved_chars"] > 0
    assert compacted_messages[3]["tool_call_id"] == "call-old"
    old_payload = json.loads(str(compacted_messages[3]["content"]))
    assert old_payload["prompt_context_compacted"] is True
    assert old_payload["result"]["content_prompt_scope"] == "older_file_read_excerpt"
    assert "old-line" in old_payload["result"]["content"]
    assert old_payload["result"]["content"] != old_content
    middle_payload = json.loads(str(compacted_messages[4]["content"]))
    next_payload = json.loads(str(compacted_messages[5]["content"]))
    recent_payload = json.loads(str(compacted_messages[6]["content"]))
    latest_payload = json.loads(str(compacted_messages[7]["content"]))
    assert middle_payload["result"]["content"] == middle_content
    assert next_payload["result"]["content"] == next_content
    assert recent_payload["result"]["content"] == recent_content
    assert latest_payload["result"]["content"] == latest_content

    original_payload = json.loads(str(messages[3]["content"]))
    assert original_payload["result"]["content"] == old_content


def test_compact_messages_for_provider_prompt_hard_caps_large_user_packet() -> None:
    large_objective = "alpha\n" + ("x" * 240_000) + "\nomega"
    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": large_objective},
    ]

    compacted_messages, stats = local_runtime_module._compact_messages_for_provider_prompt(
        messages,
        budget_chars=20_000,
        emergency_budget_chars=30_000,
        tool_schema_chars=0,
    )

    assert stats["prompt_context_budget_triggered"] is True
    assert stats["prompt_context_hard_compaction"] is True
    assert stats["prompt_context_final_chars"] <= 20_000
    compacted_user = str(compacted_messages[1]["content"])
    assert compacted_user.startswith("alpha")
    assert "omega" in compacted_user
    assert "prompt replay compacted" in compacted_user
    assert compacted_messages is not messages
    assert messages[1]["content"] == large_objective


@pytest.mark.asyncio
async def test_shell_command_reports_workspace_changes_and_counts_as_mutation(
    tmp_path,
) -> None:
    runtime = LocalOrganismToolRuntime(
        tool_ids=["shell_command"],
        workspace_root=tmp_path,
    )

    result = await runtime.call(
        "shell_command",
        {"command": "printf shell-change > shell-created.txt", "timeout": 5},
    )

    changes = result.get("workspace_changes")
    assert result["exit_code"] == 0
    assert isinstance(changes, dict)
    assert "shell-created.txt" in changes["created"]
    assert "shell-created.txt" in changes["changed_paths"]
    assert local_runtime_module._successful_workspace_mutation_paths(
        [
            {
                "tool_id": "shell_command",
                "ok": True,
                "arguments": {"command": "printf shell-change > shell-created.txt"},
                "result": result,
            }
        ],
        workspace_root=tmp_path,
    ) == ["shell-created.txt"]


def test_read_only_finalize_reason_accepts_candidate_fragment_contract_without_role(tmp_path) -> None:
    request = CompletionRequest(
        model="gpt-test",
        system_prompt="Return structured output.",
        user_prompt="Task",
        tools=[],
        output_contract=OutputContract(
            definition_of_done="Return one bounded coding contribution.",
            expected_return_shape=json.dumps(
                {
                    "candidate_fragment": "<required>",
                    "change_summary": "<required>",
                    "target_files": "<required>",
                    "test_plan": "<required>",
                    "risks": "<required>",
                },
                sort_keys=True,
            ),
        ),
    )
    reason = local_runtime_module._read_only_finalize_reason(
        request=request,
        tool_ids=["list_directory", "file_read", "web_search"],
        executed_tools=[
            {
                "tool_id": "list_directory",
                "arguments": {"path": str(tmp_path)},
                "ok": True,
                "result": {"entries": [{"name": ".dan-code", "path": ".dan-code", "type": "directory", "size": 0}]},
            },
            {
                "tool_id": "file_read",
                "arguments": {"path": ".dan-code"},
                "ok": False,
                "error": "FileNotFoundError: File not found: '.dan-code'",
            },
        ],
        workspace_root=tmp_path,
    )

    assert reason == "internal_state_probe_after_empty_workspace"


def test_read_only_finalize_mode_prefers_validator_contract_over_worker_id() -> None:
    request = CompletionRequest(
        model="gpt-test",
        system_prompt="Role: validator_synthesizer\nReturn the required validation JSON.",
        user_prompt="Task",
        metadata={
            "worker_id": "coding-build.worker-1",
            "organ_id": "coding-build.validator",
            "organism_stage": "workers",
        },
        tools=[],
        output_contract=OutputContract(
            definition_of_done="Return the validation result for the aggregated coding candidate.",
            expected_return_shape=json.dumps(
                {
                    "passed": "<required>",
                    "overall_score": "<required>",
                    "dimension_scores": "<required>",
                    "repair_brief": "<required>",
                    "missing_requirements": "<required>",
                    "comparison_note": "<required>",
                },
                sort_keys=True,
            ),
        ),
    )

    assert local_runtime_module._read_only_finalize_mode(request) == "validator"


def test_validator_origin_metadata_does_not_strip_coding_worker_write_tools() -> None:
    request = CompletionRequest(
        model="gpt-test",
        system_prompt="Role: coding_worker\nReturn the required candidate fragment.",
        user_prompt="Repair the rejected candidate.",
        metadata={
            "worker_id": "coding-build.worker-1",
            "organ_id": "coding-build.validator",
            "organism_stage": "workers",
        },
        tools=[],
        output_contract=OutputContract(
            definition_of_done="Return one bounded coding contribution.",
            expected_return_shape=json.dumps(
                {
                    "candidate_fragment": "<required>",
                    "change_summary": "<required>",
                    "target_files": "<required>",
                    "test_plan": "<required>",
                    "risks": "<required>",
                },
                sort_keys=True,
            ),
        ),
    )
    tool_schemas = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in ("file_read", "file_edit", "file_write", "shell_command", "git_diff")
    ]

    narrowed = local_runtime_module._validator_read_only_tool_schemas(request, tool_schemas)

    assert local_runtime_module._read_only_finalize_mode(request) == "coding_worker"
    assert [
        tool.get("function", {}).get("name")
        for tool in narrowed
        if isinstance(tool, dict)
    ] == ["file_read", "file_edit", "file_write", "shell_command", "git_diff"]


def test_read_only_finalize_reason_accepts_live_coding_worker_id_without_contract_marker(tmp_path) -> None:
    request = CompletionRequest(
        model="gpt-test",
        system_prompt="Return structured output.",
        user_prompt="Task",
        metadata={"worker_id": "coding-build.worker-1"},
        tools=[],
        output_contract=OutputContract(
            definition_of_done="Return one bounded contribution.",
            expected_return_shape=json.dumps({"result": "<required>"}, sort_keys=True),
        ),
    )
    reason = local_runtime_module._read_only_finalize_reason(
        request=request,
        tool_ids=["list_directory", "file_read", "web_search"],
        executed_tools=[
            {
                "tool_id": "list_directory",
                "arguments": {"path": str(tmp_path)},
                "ok": True,
                "result": {"entries": [{"name": ".dan-code", "path": ".dan-code", "type": "directory", "size": 0}]},
            },
            {
                "tool_id": "list_directory",
                "arguments": {"path": str(tmp_path / ".dan-code")},
                "ok": True,
                "result": {"entries": [{"name": "runs", "path": ".dan-code/runs", "type": "directory", "size": 0}]},
            },
        ],
        workspace_root=tmp_path,
    )

    assert reason == "internal_state_probe_after_empty_workspace"


def test_tool_confirms_effectively_empty_workspace_ignores_live_test_operator_artifacts(tmp_path) -> None:
    tool = {
        "tool_id": "list_directory",
        "arguments": {"path": str(tmp_path)},
        "ok": True,
        "result": {
            "entries": [
                {"name": ".dan-code", "path": ".dan-code", "type": "directory", "size": 0},
                {"name": "prompt.md", "path": "prompt.md", "type": "file", "size": 100},
                {"name": "acceptance.md", "path": "acceptance.md", "type": "file", "size": 100},
            ]
        },
    }

    assert local_runtime_module._tool_confirms_effectively_empty_workspace(
        tool,
        workspace_root=tmp_path,
    )


def test_read_only_finalize_reason_flags_web_search_after_empty_workspace(tmp_path) -> None:
    request = CompletionRequest(
        model="gpt-test",
        system_prompt="Role: coding_worker\nReturn the required coding JSON.",
        user_prompt="Task",
        metadata={"worker_id": "coding-build.worker-1"},
        tools=[],
        output_contract=OutputContract(
            definition_of_done="Return one bounded coding contribution.",
            expected_return_shape=json.dumps(
                {
                    "candidate_fragment": "<required>",
                    "change_summary": "<required>",
                    "target_files": "<required>",
                    "test_plan": "<required>",
                    "risks": "<required>",
                },
                sort_keys=True,
            ),
        ),
    )
    reason = local_runtime_module._read_only_finalize_reason(
        request=request,
        tool_ids=["list_directory", "file_read", "web_search"],
        executed_tools=[
            {
                "tool_id": "list_directory",
                "arguments": {"path": str(tmp_path)},
                "ok": True,
                "result": {"entries": [{"name": ".dan-code", "path": ".dan-code", "type": "directory", "size": 0}]},
            },
            {
                "tool_id": "web_search",
                "arguments": {"query": "python kanban board data model Card Column Board class design"},
                "ok": True,
                "result": {"count": 5, "provider": "tavily"},
            },
        ],
        workspace_root=tmp_path,
    )

    assert reason == "web_search_after_empty_workspace"


def test_read_only_finalize_reason_flags_repeated_validator_reread_after_grounding(
    tmp_path,
) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    request = CompletionRequest(
        model="gpt-test",
        system_prompt="Role: validator_synthesizer\nReturn the required validation JSON.",
        user_prompt="Task",
        metadata={"worker_id": "coding-build.validator.lead"},
        tools=[],
        output_contract=OutputContract(
            definition_of_done="Return the validation result for the aggregated coding candidate.",
            expected_return_shape=json.dumps(
                {
                    "passed": "<required>",
                    "overall_score": "<required>",
                    "dimension_scores": "<required>",
                    "repair_brief": "<required>",
                    "missing_requirements": "<required>",
                    "comparison_note": "<required>",
                },
                sort_keys=True,
            ),
        ),
    )
    repeated_test_path = tests_dir / "test_rst.py"
    reason = local_runtime_module._read_only_finalize_reason(
        request=request,
        tool_ids=["file_read", "git_diff"],
        executed_tools=[
            {
                "tool_id": "git_diff",
                "ok": True,
                "result": {"diff": "diff --git a/src/rst.py b/src/rst.py"},
            },
            {
                "tool_id": "file_read",
                "arguments": {"path": "src/rst.py"},
                "ok": True,
                "result": {"path": "src/rst.py", "content": "class RST: pass\n"},
            },
            {
                "tool_id": "file_read",
                "arguments": {"path": str(repeated_test_path)},
                "ok": True,
                "result": {"path": str(repeated_test_path), "content": "def test_rst(): pass\n"},
            },
            {
                "tool_id": "file_read",
                "arguments": {"path": "tests/test_rst.py"},
                "ok": True,
                "result": {"path": "tests/test_rst.py", "content": "def test_rst(): pass\n"},
            },
        ],
        workspace_root=tmp_path,
    )

    assert reason == "repeated_validator_reread_after_grounding"


def test_partial_coding_candidate_from_tool_evidence_prefers_workspace_mutations(
    tmp_path,
) -> None:
    request = CompletionRequest(
        model="gpt-test",
        system_prompt="Role: coding_worker\nReturn the required coding JSON.",
        user_prompt="Task",
        tools=[],
        output_contract=OutputContract(
            definition_of_done="Return one bounded coding contribution.",
            expected_return_shape=json.dumps(
                {
                    "candidate_fragment": "<required>",
                    "change_summary": "<required>",
                    "target_files": "<required>",
                    "test_plan": "<required>",
                    "risks": "<required>",
                },
                sort_keys=True,
            ),
        ),
    )
    external_helper_path = Path(tempfile.gettempdir()) / "dan_runtime_external_helper.py"
    payload = local_runtime_module._partial_coding_candidate_from_tool_evidence(
        request=request,
        executed_tools=[
            {
                "tool_id": "file_write",
                "ok": True,
                "arguments": {"path": "src/rst.py"},
                "result": {"path": "src/rst.py"},
            },
            {
                "tool_id": "file_write",
                "ok": True,
                "arguments": {"path": str(external_helper_path)},
                "result": {"path": str(external_helper_path)},
            },
        ],
        stop_reason="forced_finalize_guardrail_unheeded",
        workspace_root=tmp_path,
    )

    assert payload is not None
    assert payload["target_files"] == ["src/rst.py"]


def test_partial_coding_candidate_from_tool_evidence_prefers_tracked_git_diff_paths(
    monkeypatch,
    tmp_path,
) -> None:
    request = CompletionRequest(
        model="gpt-test",
        system_prompt="Role: coding_worker\nReturn the required coding JSON.",
        user_prompt="Task",
        tools=[],
        output_contract=OutputContract(
            definition_of_done="Return one bounded coding contribution.",
            expected_return_shape=json.dumps(
                {
                    "candidate_fragment": "<required>",
                    "change_summary": "<required>",
                    "target_files": "<required>",
                    "test_plan": "<required>",
                    "risks": "<required>",
                },
                sort_keys=True,
            ),
        ),
    )
    monkeypatch.setattr(
        local_runtime_module,
        "_tracked_workspace_diff_paths",
        lambda *, workspace_root: ["src/rst.py"],
    )

    payload = local_runtime_module._partial_coding_candidate_from_tool_evidence(
        request=request,
        executed_tools=[
            {
                "tool_id": "file_edit",
                "ok": True,
                "arguments": {
                    "path": "src/rst.py",
                    "start_line": 1,
                    "end_line": 1,
                    "content": "class RST: pass\n",
                },
                "result": {"path": "src/rst.py"},
            },
            {
                "tool_id": "file_write",
                "ok": True,
                "arguments": {
                    "path": "tests/test_rst_header_rows.py",
                    "content": "def test_rst_header_rows(): pass\n",
                },
                "result": {"path": "tests/test_rst_header_rows.py"},
            },
        ],
        stop_reason="provider_completion_timeout",
        workspace_root=tmp_path,
    )

    assert payload is not None
    assert payload["target_files"] == ["src/rst.py"]
    assert payload["change_summary"].startswith("Materialized 1 file")


def test_tool_use_policy_skips_read_only_warning_when_file_write_exists() -> None:
    policy = local_runtime_module._tool_use_policy(["file_read", "file_write"])

    assert "This tool set is read-only" not in policy
    assert "later write-capable stage" not in policy


def test_available_local_organism_tools_surfaces_standalone_modules() -> None:
    catalog = available_local_organism_tools()

    assert "file_edit" in catalog
    assert "file_read" in catalog
    assert "file_write" in catalog
    assert "shell_command" in catalog


def test_available_local_organism_tools_surfaces_browser_and_desktop_wrappers() -> None:
    catalog = available_local_organism_tools()

    assert {
        "browser_inspect",
        "browser_select",
        "browser_tabs",
        "desktop_observe",
        "desktop_focus",
        "desktop_click",
        "desktop_type",
        "desktop_hotkey",
    }.issubset(set(catalog))


def test_read_only_tool_projection_keeps_only_observation_computer_tools() -> None:
    projected = local_runtime_module._read_only_tool_ids(
        [
            "browser_tabs",
            "browser_inspect",
            "browser_open",
            "browser_wait",
            "browser_extract",
            "browser_screenshot",
            "browser_click",
            "browser_fill",
            "browser_type",
            "browser_select",
            "browser_download",
            "desktop_observe",
            "desktop_focus",
            "desktop_click",
            "desktop_type",
            "desktop_hotkey",
            "file_read",
            "file_edit",
        ]
    )

    assert projected == [
        "browser_tabs",
        "browser_inspect",
        "browser_wait",
        "browser_extract",
        "browser_screenshot",
        "desktop_observe",
        "file_read",
    ]
