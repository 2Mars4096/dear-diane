from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from dan.providers import CompletionResult
from dan.providers.registry import ProviderRegistry
from dan.server.chat.events import GraphSummary
from dan.chat_prompts import (
    NODE_TYPE_REFERENCE,
    SYSTEM_PROMPT_TEMPLATE,
    UNIFIED_SYSTEM_PROMPT,
    _classify_research_prompt_signal,
    _RESEARCH_REPORT_PROMPT_HINT,
)
from dan.server.chat_manager import ChatManager


def test_unified_prompt_defaults_to_autonomous_forward_motion() -> None:
    assert "Work autonomously by default." in UNIFIED_SYSTEM_PROMPT
    assert "ambiguous and the next action is hard to reverse" in UNIFIED_SYSTEM_PROMPT
    assert "safest reasonable interpretation" in UNIFIED_SYSTEM_PROMPT
    assert "provide findings first" in UNIFIED_SYSTEM_PROMPT


def test_unified_prompt_no_longer_prefers_asking_over_guessing() -> None:
    assert "Prefer asking over guessing" not in UNIFIED_SYSTEM_PROMPT
    assert "ASK for clarification before acting" not in UNIFIED_SYSTEM_PROMPT


def test_research_prompt_heuristic_is_more_precise() -> None:
    assert _classify_research_prompt_signal(
        "Write a literature review on AI chips with citations.",
    ) == "yes"
    assert _classify_research_prompt_signal(
        "Compare Nvidia and AMD with sources.",
    ) == "maybe"
    assert _classify_research_prompt_signal(
        "Give me a deep dive on Nvidia.",
    ) == "no"


def test_node_type_reference_lists_common_tool_manifests() -> None:
    assert "csv_read: in=[path, delimiter, max_rows, columns, encoding]" in NODE_TYPE_REFERENCE
    assert "file_write: in=[path, content, mode, encoding]" in NODE_TYPE_REFERENCE
    assert "web_fetch: in=[url, timeout, max_length]" in NODE_TYPE_REFERENCE


def test_operation_examples_use_runtime_placeholder_syntax() -> None:
    rendered = SYSTEM_PROMPT_TEMPLATE.format(
        node_type_reference="llm_operator: in=[input] out=[text]",
        graph_summary="(empty workflow)",
    )
    assert "Summarize: {input}" in rendered
    assert "Summarize {{input}}" not in rendered


class _ClassificationProvider:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> CompletionResult:
        self.calls.append(kwargs)
        return CompletionResult(text=self.answer, usage={"prompt_tokens": 5, "completion_tokens": 1})

    async def stream(self, **kwargs: Any) -> Any:
        raise AssertionError("stream() not expected in prompt-classifier tests")


def _make_manager(provider: _ClassificationProvider) -> ChatManager:
    registry = ProviderRegistry()
    registry.register("default", provider)
    manager = ChatManager(registry, graph_store=SimpleNamespace())
    manager._chat_model = "test-model"
    return manager


def _empty_summary() -> GraphSummary:
    return GraphSummary(
        workflow_id="_scratch",
        name="scratch",
        description="",
        node_count=0,
        edge_count=0,
        nodes=[],
        edges=[],
        entry_points=[],
        exit_points=[],
        revision="rev-1",
    )


@pytest.mark.asyncio
async def test_research_prompt_llm_fallback_used_for_borderline_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _ClassificationProvider("YES")
    manager = _make_manager(provider)
    
    async def _noop_preflight(*_args: Any, **_kwargs: Any) -> str:
        return ""

    monkeypatch.setattr(manager, "_run_preflight_hooks", _noop_preflight)

    messages = await manager._build_messages(
        summary=_empty_summary(),
        user_message="Compare Nvidia and AMD with sources.",
        history=[],
        workflow_id="_scratch",
        model="test-model",
    )

    assert provider.calls
    assert provider.calls[0]["max_tokens"] == 3
    assert _RESEARCH_REPORT_PROMPT_HINT in messages[0]["content"]


@pytest.mark.asyncio
async def test_research_prompt_llm_fallback_skipped_for_clear_non_report_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _ClassificationProvider("YES")
    manager = _make_manager(provider)
    
    async def _noop_preflight(*_args: Any, **_kwargs: Any) -> str:
        return ""

    monkeypatch.setattr(manager, "_run_preflight_hooks", _noop_preflight)

    messages = await manager._build_messages(
        summary=_empty_summary(),
        user_message="Give me a deep dive on Nvidia.",
        history=[],
        workflow_id="_scratch",
        model="test-model",
    )

    assert provider.calls == []
    assert _RESEARCH_REPORT_PROMPT_HINT not in messages[0]["content"]


@pytest.mark.asyncio
async def test_research_prompt_llm_fallback_can_reject_borderline_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _ClassificationProvider("NO")
    manager = _make_manager(provider)

    async def _noop_preflight(*_args: Any, **_kwargs: Any) -> str:
        return ""

    monkeypatch.setattr(manager, "_run_preflight_hooks", _noop_preflight)

    messages = await manager._build_messages(
        summary=_empty_summary(),
        user_message="Compare Nvidia and AMD with sources.",
        history=[],
        workflow_id="_scratch",
        model="test-model",
    )

    assert provider.calls
    assert _RESEARCH_REPORT_PROMPT_HINT not in messages[0]["content"]
