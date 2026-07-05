from __future__ import annotations

import json

import pytest

import dan.worker.organisms.local_runtime as local_runtime_module
from dan.providers import CompletionResult
from dan.worker.core.contracts import OutputContract
from dan.worker.core.interfaces import CompletionRequest
from dan.worker.organisms.local_runtime import (
    LocalOrganismToolRuntime,
    ToolLoopCompletionProvider,
)


def _tool_names(tools: object) -> list[str]:
    return [
        str(tool.get("function", {}).get("name") or "")
        for tool in list(tools or [])
        if isinstance(tool, dict)
    ]


def _tool_schemas() -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "file_read",
                "description": "Read a file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
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
    ]


class _SourceStructureRecoveryProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append({"messages": messages, "model": model, "tools": kwargs.get("tools")})
        call_index = len(self.calls)
        if call_index == 1:
            return self._tool_result(
                model,
                "call-1",
                "file_read",
                {"path": "website/index.html"},
            )
        if call_index == 2:
            assert _tool_names(kwargs.get("tools")) == ["file_read", "file_edit", "file_write"]
            assert "make the first concrete project write now" in str(messages[-1]["content"])
            return self._tool_result(
                model,
                "call-2",
                "file_read",
                {"path": "website/styles.css"},
            )
        if call_index == 3:
            assert _tool_names(kwargs.get("tools")) == ["file_edit", "file_write"]
            assert "already used the final targeted `file_read`" in str(messages[-1]["content"])
            return self._tool_result(
                model,
                "call-3",
                "file_edit",
                {
                    "path": "website/styles.css",
                    "start_line": 2,
                    "end_line": 2,
                    "content": "}\n",
                },
            )
        if call_index == 4:
            assert _tool_names(kwargs.get("tools")) == ["file_read", "file_edit", "file_write"]
            latest = str(messages[-1]["content"])
            assert "Source-structure guard:" in latest
            assert "one focused `file_read`" in latest
            assert "already used the final targeted `file_read`" not in latest
            return self._tool_result(
                model,
                "call-4",
                "file_read",
                {"path": "website/styles.css", "start_line": 1, "end_line": 3},
            )
        if call_index == 5:
            assert _tool_names(kwargs.get("tools")) == ["file_edit", "file_write"]
            assert "already used the final targeted `file_read`" in str(messages[-1]["content"])
            return self._tool_result(
                model,
                "call-5",
                "file_edit",
                {
                    "path": "website/styles.css",
                    "start_line": 2,
                    "end_line": 2,
                    "content": "  color: blue;\n",
                },
            )

        payload = {
            "candidate_id": "source-structure-recovered-after-focused-read",
            "change_summary": "Recovered from the guarded CSS edit with a focused read.",
            "target_files": ["website/styles.css"],
            "test_plan": ["Run the focused CSS source-shape guard test."],
            "risks": ["Only the targeted CSS line is changed."],
        }
        return CompletionResult(
            text=json.dumps(payload),
            model=model,
            raw_assistant_message={"role": "assistant", "content": json.dumps(payload)},
        )

    @staticmethod
    def _tool_result(
        model: str,
        call_id: str,
        tool_name: str,
        arguments: dict[str, object],
    ) -> CompletionResult:
        tool_call = {
            "id": call_id,
            "type": "function",
            "function": {"name": tool_name, "arguments": json.dumps(arguments)},
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


@pytest.mark.asyncio
async def test_source_structure_rejection_reopens_focused_recovery_read(tmp_path) -> None:
    events: list[dict[str, object]] = []
    website_dir = tmp_path / "website"
    website_dir.mkdir()
    (website_dir / "index.html").write_text("<main class=\"card\">Demo</main>\n", encoding="utf-8")
    (website_dir / "styles.css").write_text(".card {\n  color: red;\n}\n", encoding="utf-8")

    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "file_edit", "file_write"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _SourceStructureRecoveryProvider()
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
            user_prompt="Patch the CSS card color with the smallest safe edit.",
            metadata={
                "worker_id": "coding-build.worker-1",
                "recommended_write_paths": ["website/styles.css"],
            },
            tools=_tool_schemas(),
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
    assert payload["candidate_id"] == "source-structure-recovered-after-focused-read"
    assert (website_dir / "styles.css").read_text(encoding="utf-8") == (
        ".card {\n  color: blue;\n}\n"
    )
    source_nudge_event = next(
        event for event in events if event["event"] == "toolloop.source_structure_nudged"
    )
    assert source_nudge_event["tool_ids"] == ["file_edit"]
    assert source_nudge_event["enabled_tools"] == ["file_read", "file_edit", "file_write"]
    assert _tool_names(provider_impl.calls[3]["tools"]) == [
        "file_read",
        "file_edit",
        "file_write",
    ]


def test_compact_messages_for_provider_prompt_preserves_image_data_urls() -> None:
    data_url = "data:image/png;base64," + ("a" * 50_000)
    messages = [
        {"role": "system", "content": "System prompt."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Please inspect this screenshot. " + ("x" * 5_000)},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    compacted_messages, stats = local_runtime_module._compact_messages_for_provider_prompt(
        messages,
        budget_chars=1_200,
        emergency_budget_chars=1_200,
        tool_schema_chars=0,
        emergency=True,
    )

    assert stats["prompt_context_hard_compaction"] is True
    compacted_content = compacted_messages[1]["content"]
    assert isinstance(compacted_content, list)
    assert compacted_content[1]["image_url"]["url"] == data_url
    assert "truncated" not in compacted_content[1]["image_url"]["url"]
    assert compacted_messages is not messages
    assert messages[1]["content"][1]["image_url"]["url"] == data_url


def test_debug_prompt_messages_summarizes_image_data_urls() -> None:
    data_url = "data:image/png;base64," + ("a" * 12_000)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Please inspect this screenshot."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]

    debug_messages = local_runtime_module._debug_prompt_messages(messages)

    debug_url = debug_messages[0]["content"][1]["image_url"]["url"]
    assert debug_url.startswith("<image data URL: image/png, base64_chars=12000")
    assert data_url not in json.dumps(debug_messages)
    assert messages[0]["content"][1]["image_url"]["url"] == data_url
