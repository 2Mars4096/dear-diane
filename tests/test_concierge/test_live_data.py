"""Tests for plan 25-12: Tool-aware conversation & solver activation.

Layer 1: Capability registry — web_search available in conversation mode
Layer 3: Post-execution reflection — _NUMERIC_CLAIM_RE
"""

from __future__ import annotations

import asyncio
import importlib
import json
import httpx
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from dan.server.capability_registry import CapabilityContext, CapabilityResult, ChatCapabilityRegistry
from dan.server.chat.helpers import (
    _extract_cited_sources,
    _missing_action_hints,
    build_citation_records,
    extract_inline_citations,
    has_explicit_web_trigger,
    should_require_web_grounding,
    verify_response_citations,
)
from dan.server.chat_manager import compute_graph_revision
from dan.server.concierge.actions import _NUMERIC_CLAIM_RE
from dan.server.capability_handlers import (
    APPLY_LAST_MUTATION_SCHEMA,
    WEB_SEARCH_CAPABILITY_SCHEMA,
    handle_apply_last_mutation,
    handle_delete_graph,
    handle_web_fetch,
    handle_web_search,
    register_base_capabilities,
)
from dan.server.capabilities.web import _extract_relevant_excerpt
from dan.tools.browser_control import MockBrowserController

web_fetch_tool_mod = importlib.import_module("dan.tools.web_fetch")
web_search_tool_mod = importlib.import_module("dan.tools.web_search")


def _clear_tool_state(module, *names: str) -> None:
    for name in names:
        value = getattr(module, name, None)
        if hasattr(value, "clear"):
            value.clear()


@pytest.fixture(autouse=True)
def _clear_web_tool_state():
    _clear_tool_state(web_search_tool_mod, "_SEARCH_CACHE", "_SEARCH_INFLIGHT")
    _clear_tool_state(web_fetch_tool_mod, "_FETCH_CACHE", "_FETCH_INFLIGHT")
    yield
    _clear_tool_state(web_search_tool_mod, "_SEARCH_CACHE", "_SEARCH_INFLIGHT")
    _clear_tool_state(web_fetch_tool_mod, "_FETCH_CACHE", "_FETCH_INFLIGHT")


def _patch_fetch_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    real_async_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(web_fetch_tool_mod.httpx, "AsyncClient", client_factory)


def _http_status_error(url: str, status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


# ── Layer 1: Capability registry ──────────────────────────────────


class TestWebSearchCapabilityRegistration:
    def _registry(self) -> ChatCapabilityRegistry:
        reg = ChatCapabilityRegistry()
        register_base_capabilities(reg)
        return reg

    def test_web_search_registered_for_conversation_mode(self):
        reg = self._registry()
        assert "web_search" in reg.list_tool_names("conversation")

    @pytest.mark.skip(reason="web_search mode filtering deferred to tiered executor implementation")
    def test_web_search_not_in_ask_mode(self):
        reg = self._registry()
        assert "web_search" not in reg.list_tool_names("ask")

    def test_web_search_not_in_plan_mode(self):
        reg = self._registry()
        assert "web_search" not in reg.list_tool_names("plan")


class TestDeleteGraphCapabilityRegistration:
    def _registry(self) -> ChatCapabilityRegistry:
        reg = ChatCapabilityRegistry()
        register_base_capabilities(reg)
        return reg

    def test_delete_graph_registered_for_agent_mode(self):
        reg = self._registry()
        assert "delete_graph" in reg.list_tool_names("agent")

    def test_delete_graph_not_in_ask_mode(self):
        reg = self._registry()
        assert "delete_graph" not in reg.list_tool_names("ask")

    @pytest.mark.asyncio
    async def test_delete_graph_handler_deletes_named_workflow(self):
        deleted: list[str] = []

        def _delete_graph(graph_id: str) -> bool:
            deleted.append(graph_id)
            return True

        result = await handle_delete_graph(
            {"graph_id": "equity_report_orchestrator"},
            CapabilityContext(
                workflow_id="_scratch",
                graph_store=SimpleNamespace(delete_graph=_delete_graph),
            ),
        )

        assert result.success is True
        assert deleted == ["equity_report_orchestrator"]
        assert result.data == {
            "graph_id": "equity_report_orchestrator",
            "requested_graph_id": "equity_report_orchestrator",
            "deleted": True,
        }

    @pytest.mark.asyncio
    async def test_delete_graph_handler_resolves_graph_name_to_graph_id(self):
        deleted: list[str] = []

        def _delete_graph(graph_id: str) -> bool:
            deleted.append(graph_id)
            return True

        result = await handle_delete_graph(
            {"graph_id": "Equity Report Orchestrator"},
            CapabilityContext(
                workflow_id="_scratch",
                graph_store=SimpleNamespace(
                    delete_graph=_delete_graph,
                    list_graphs=lambda: [
                        {
                            "graph_id": "equity_report_orchestrator",
                            "name": "Equity Report Orchestrator",
                        },
                    ],
                ),
            ),
        )

        assert result.success is True
        assert deleted == ["equity_report_orchestrator"]
        assert "Matched workflow name" in result.message

    @pytest.mark.asyncio
    async def test_delete_graph_handler_treats_missing_workflow_as_already_absent(self):
        result = await handle_delete_graph(
            {"graph_id": "equity_report_orchestrator"},
            CapabilityContext(
                workflow_id="_scratch",
                graph_store=SimpleNamespace(
                    delete_graph=lambda _graph_id: False,
                    list_graphs=lambda: [],
                ),
            ),
        )

        assert result.success is True
        assert result.data == {
            "graph_id": "equity_report_orchestrator",
            "requested_graph_id": "equity_report_orchestrator",
            "deleted": False,
            "already_absent": True,
        }
        assert "nothing to delete" in result.message

    @pytest.mark.asyncio
    async def test_delete_graph_handler_rejects_scratch_workflow(self):
        result = await handle_delete_graph(
            {},
            CapabilityContext(
                workflow_id="_scratch",
                graph_store=SimpleNamespace(delete_graph=lambda _graph_id: True),
            ),
        )

        assert result.success is False
        assert result.error_type == "invalid_input"
        assert "Cannot delete" in result.message

class TestApplyLastMutationCapabilityRegistration:
    def _registry(self) -> ChatCapabilityRegistry:
        reg = ChatCapabilityRegistry()
        register_base_capabilities(reg)
        return reg

    def test_apply_last_mutation_registered_for_agent_mode(self):
        reg = self._registry()
        assert "apply_last_mutation" in reg.list_tool_names("agent")
        assert APPLY_LAST_MUTATION_SCHEMA["function"]["name"] == "apply_last_mutation"

    def test_apply_last_mutation_not_in_ask_mode(self):
        reg = self._registry()
        assert "apply_last_mutation" not in reg.list_tool_names("ask")

    @pytest.mark.asyncio
    async def test_apply_last_mutation_handler_applies_latest_preview(self):
        saved_graphs: list[dict[str, object]] = []
        saved_threads: list[object] = []
        saved_meta: list[dict[str, object]] = []
        mutation_plan = {
            "description": "Add a workflow input",
            "operations": [
                {
                    "op": "add_node",
                    "id": "workflow_input",
                    "node_type": "input",
                    "name": "Workflow Input",
                }
            ],
        }
        thread = SimpleNamespace(
            messages=[
                SimpleNamespace(
                    id="mut-1",
                    mutation_plan=mutation_plan,
                    dry_run_result={"success": True},
                    mutation_status="proposed",
                )
            ],
            updated_at=None,
        )
        chat_store = SimpleNamespace(
            get_thread=lambda workflow_id, thread_id: thread,
            save_thread=lambda thread_obj: saved_threads.append(thread_obj),
            get_thread_meta=lambda workflow_id, thread_id: {
                "latest_mutation_preview": {
                    "message_id": "mut-1",
                    "mutation_plan": mutation_plan,
                    "dry_run_result": {"success": True},
                }
            },
            set_thread_meta=lambda workflow_id, thread_id, meta: saved_meta.append(meta),
        )
        graph_store = SimpleNamespace(
            get_graph=lambda workflow_id: {
                "version": "dan_graph_v1",
                "metadata": {"name": "test"},
                "nodes": [
                    {
                        "id": "n1",
                        "name": "Node 1",
                        "node_type": "llm_operator",
                        "model": "test-model",
                        "prompt_template": "Hello",
                    }
                ],
                "edges": [],
            },
            save_graph=lambda workflow_id, graph: saved_graphs.append(graph),
        )

        result = await handle_apply_last_mutation(
            {},
            CapabilityContext(
                workflow_id="wf1",
                graph_store=graph_store,
                thread_id="thread-1",
                chat_manager=SimpleNamespace(_chat_store=chat_store),
            ),
        )

        assert result.success is True
        assert saved_graphs
        assert any(node["id"] == "workflow_input" for node in saved_graphs[0]["nodes"])
        assert thread.messages[0].mutation_status == "applied"
        assert saved_threads, "Applying from chat should persist the updated thread status"
        assert saved_meta and "latest_mutation_preview" not in saved_meta[-1]
        assert result.data["message_id"] == "mut-1"

    @pytest.mark.asyncio
    async def test_apply_last_mutation_handler_requires_existing_preview(self):
        chat_store = SimpleNamespace(
            get_thread=lambda workflow_id, thread_id: SimpleNamespace(messages=[]),
            get_thread_meta=lambda workflow_id, thread_id: {},
        )

        result = await handle_apply_last_mutation(
            {},
            CapabilityContext(
                workflow_id="wf1",
                graph_store=SimpleNamespace(),
                thread_id="thread-1",
                chat_manager=SimpleNamespace(_chat_store=chat_store),
            ),
        )

        assert result.success is False
        assert result.error_type == "not_found"
        assert "No proposed workflow preview" in result.message

    @pytest.mark.asyncio
    async def test_apply_last_mutation_ignores_timestamp_only_graph_changes(self):
        saved_graphs: list[dict[str, object]] = []
        base_graph = {
            "version": "dan_graph_v1",
            "metadata": {
                "name": "test",
                "description": "",
                "created_at": "2026-03-20T08:00:00Z",
                "updated_at": "2026-03-20T08:00:00Z",
            },
            "nodes": [
                {
                    "id": "n1",
                    "name": "Node 1",
                    "node_type": "llm_operator",
                    "model": "test-model",
                    "prompt_template": "Hello",
                    "input_ports": [{"name": "input", "schema": {}}],
                    "output_ports": [{"name": "text", "schema": {}}],
                }
            ],
            "edges": [],
            "sub_graphs": {},
            "entry_points": ["n1"],
            "exit_points": ["n1"],
            "shared_context": [],
            "artifact_refs": [],
        }
        mutation_plan = {
            "description": "Add a second node",
            "base_graph_revision": compute_graph_revision(base_graph),
            "operations": [
                {
                    "op": "add_node",
                    "id": "n2",
                    "node_type": "llm_operator",
                    "name": "Node 2",
                    "config": {
                        "model": "test-model",
                        "prompt_template": "World",
                        "input_ports": [{"name": "input", "schema": {}}],
                        "output_ports": [{"name": "text", "schema": {}}],
                    },
                }
            ],
        }
        thread = SimpleNamespace(
            messages=[
                SimpleNamespace(
                    id="mut-ts",
                    mutation_plan=mutation_plan,
                    dry_run_result={"success": True},
                    mutation_status="proposed",
                )
            ],
            updated_at=None,
        )
        current_graph = json.loads(json.dumps(base_graph))
        current_graph["metadata"]["updated_at"] = "2026-03-20T08:05:00Z"
        chat_store = SimpleNamespace(
            get_thread=lambda workflow_id, thread_id: thread,
            save_thread=lambda thread_obj: None,
            get_thread_meta=lambda workflow_id, thread_id: {
                "latest_mutation_preview": {
                    "message_id": "mut-ts",
                    "mutation_plan": mutation_plan,
                    "dry_run_result": {"success": True},
                }
            },
            set_thread_meta=lambda workflow_id, thread_id, meta: None,
        )
        graph_store = SimpleNamespace(
            get_graph=lambda workflow_id: current_graph,
            save_graph=lambda workflow_id, graph: saved_graphs.append(graph) or graph,
        )

        result = await handle_apply_last_mutation(
            {},
            CapabilityContext(
                workflow_id="wf1",
                graph_store=graph_store,
                thread_id="thread-1",
                chat_manager=SimpleNamespace(_chat_store=chat_store),
            ),
        )

        assert result.success is True
        assert result.error_type is None
        assert saved_graphs
        assert any(node["id"] == "n2" for node in saved_graphs[0]["nodes"])

    @pytest.mark.asyncio
    async def test_web_search_handler_returns_results(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "results": [{"title": "t", "url": "http://x", "snippet": "s"}],
            "provider": "tavily",
        }
        with patch("dan.tools.web_search.web_search", new_callable=AsyncMock, return_value=fake_result):
            result = await handle_web_search({"query": "test"}, None)
            assert result.success
            assert '[1] t' in result.message
            assert "Snippet: s" in result.message
            assert "URL: http://x" in result.message
            assert result.data["provider"] == "tavily"
            assert result.data["grounded_result_count"] == 0

    @pytest.mark.asyncio
    async def test_web_search_handler_surfaces_cache_and_fallback_metadata(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "results": [{"title": "t", "url": "http://x", "snippet": "s"}],
            "provider": "brave",
            "cache_hit": True,
            "provider_failures": [{"provider": "tavily", "summary": "HTTP 503"}],
        }
        with patch("dan.tools.web_search.web_search", new_callable=AsyncMock, return_value=fake_result):
            result = await handle_web_search({"query": "test"}, None)

        assert result.success
        assert 'Web search results for "test" (provider: brave; cache hit)' in result.message
        assert "Fallbacks: tavily: HTTP 503" in result.message
        assert result.data["provider_failures"] == [{"provider": "tavily", "summary": "HTTP 503"}]

    @pytest.mark.asyncio
    async def test_web_search_handler_fetches_top_results_in_parallel(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "results": [
                {"title": "r1", "url": "http://x1", "snippet": "s1"},
                {"title": "r2", "url": "http://x2", "snippet": "s2"},
                {"title": "r3", "url": "http://x3", "snippet": "s3"},
            ],
            "provider": "tavily",
        }

        async def _fake_fetch(*, url: str):
            return {"content": f"content for {url}"}

        with (
            patch("dan.tools.web_search.web_search", new_callable=AsyncMock, return_value=fake_result),
            patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, side_effect=_fake_fetch) as mock_fetch,
        ):
            result = await handle_web_search(
                {"query": "test", "num_results": 3, "fetch_content": True},
                None,
            )

        assert result.success
        assert mock_fetch.await_count == 2
        assert "Fetched page excerpts (bounded fetch attempts over top-ranked results)" in result.message
        assert "[1] Fetched content from http://x1" in result.message
        assert "[2] Fetched content from http://x2" in result.message
        assert result.data["grounded_result_count"] == 2
        assert len(result.data["fetched_results"]) == 2
        assert "search_result_set" in result.data
        assert result.data["search_result_set"]["grounded_result_count"] == 2

    @pytest.mark.asyncio
    async def test_web_search_handler_surfaces_fetch_failures(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "results": [
                {"title": "r1", "url": "http://x1", "snippet": "s1"},
                {"title": "r2", "url": "http://x2", "snippet": "s2"},
            ]
        }

        async def _fake_fetch(*, url: str):
            if url.endswith("x2"):
                raise RuntimeError("boom")
            return {"content": f"content for {url}"}

        with (
            patch("dan.tools.web_search.web_search", new_callable=AsyncMock, return_value=fake_result),
            patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, side_effect=_fake_fetch),
        ):
            result = await handle_web_search(
                {"query": "test", "num_results": 2, "fetch_content": True},
                None,
            )

        assert result.success
        assert "Fetch failed for http://x2: boom" in result.message
        assert result.data["grounded_result_count"] == 1

    @pytest.mark.asyncio
    async def test_web_search_handler_rejects_browser_gated_shell_content(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "results": [{"title": "r1", "url": "http://x1", "snippet": "s1"}],
            "provider": "tavily",
        }
        fake_fetch = {
            "url": "http://x1",
            "content": "Enable JavaScript to run this app.",
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "fetch_via": "http",
            "browser_fallback_used": False,
            "content_requires_browser": True,
        }

        with (
            patch("dan.tools.web_search.web_search", new_callable=AsyncMock, return_value=fake_result),
            patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, return_value=fake_fetch),
        ):
            result = await handle_web_search(
                {"query": "test", "num_results": 1, "fetch_content": True},
                None,
            )

        assert result.success
        assert result.data["grounded_result_count"] == 0
        assert "require a browser-rendered session" in result.message
        assert "Retry with browser_fallback=true" in result.message

    @pytest.mark.asyncio
    async def test_web_search_handler_reports_browser_fallback_usage(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "results": [{"title": "r1", "url": "http://x1", "snippet": "s1"}],
            "provider": "tavily",
        }
        fake_fetch = {
            "url": "http://x1",
            "content": "Quarterly revenue grew 12 percent year over year.",
            "status_code": 0,
            "content_type": "text/html; browser-rendered",
            "fetch_via": "browser",
            "browser_fallback_used": True,
            "content_requires_browser": False,
        }

        with (
            patch("dan.tools.web_search.web_search", new_callable=AsyncMock, return_value=fake_result),
            patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, return_value=fake_fetch),
        ):
            result = await handle_web_search(
                {
                    "query": "quarterly revenue",
                    "num_results": 1,
                    "fetch_content": True,
                    "browser_fallback": True,
                },
                None,
            )

        assert result.success
        assert result.data["grounded_result_count"] == 1
        assert result.data["browser_fallback_count"] == 1
        assert result.data["search_result_set"]["browser_fallback_count"] == 1
        assert "Browser fallback used for 1 page(s)." in result.message
        assert "Fetched content from http://x1 (browser-rendered)" in result.message

    @pytest.mark.asyncio
    async def test_web_search_handler_auto_promotes_fetch_for_grounding_required(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "results": [
                {"title": "r1", "url": "http://x1", "snippet": "s1"},
                {"title": "r2", "url": "http://x2", "snippet": "s2"},
            ],
            "provider": "tavily",
        }

        async def _fake_fetch(*, url: str):
            return {"content": f"content for {url}"}

        with (
            patch("dan.tools.web_search.web_search", new_callable=AsyncMock, return_value=fake_result),
            patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, side_effect=_fake_fetch) as mock_fetch,
        ):
            result = await handle_web_search(
                {"query": "latest dan updates"},
                CapabilityContext(workflow_id="_scratch", grounding_required=True),
            )

        assert result.success
        assert result.data["fetch_content_requested"] is True
        assert result.data["grounded_result_count"] == 2
        assert mock_fetch.await_count == 2

    @pytest.mark.asyncio
    async def test_web_search_handler_honors_explicit_fetch_false(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "results": [{"title": "r1", "url": "http://x1", "snippet": "s1"}],
            "provider": "tavily",
        }

        with (
            patch("dan.tools.web_search.web_search", new_callable=AsyncMock, return_value=fake_result),
            patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock) as mock_fetch,
        ):
            result = await handle_web_search(
                {"query": "latest dan updates", "fetch_content": False},
                CapabilityContext(workflow_id="_scratch", grounding_required=True),
            )

        assert result.success
        assert result.data["fetch_content_requested"] is False
        assert result.data["grounded_result_count"] == 0
        assert mock_fetch.await_count == 0

    @pytest.mark.asyncio
    async def test_web_search_handler_walks_down_results_when_early_fetches_fail(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "results": [
                {"title": "r1", "url": "http://x1", "snippet": "s1"},
                {"title": "r2", "url": "http://x2", "snippet": "s2"},
                {"title": "r3", "url": "http://x3", "snippet": "s3"},
                {"title": "r4", "url": "http://x4", "snippet": "s4"},
            ],
            "provider": "tavily",
        }

        async def _fake_fetch(*, url: str):
            if url in {"http://x1", "http://x2"}:
                raise RuntimeError("blocked")
            return {"content": f"content for {url}"}

        with (
            patch("dan.tools.web_search.web_search", new_callable=AsyncMock, return_value=fake_result),
            patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, side_effect=_fake_fetch) as mock_fetch,
        ):
            result = await handle_web_search(
                {"query": "test", "num_results": 4, "fetch_content": True},
                None,
            )

        assert result.success
        assert mock_fetch.await_count == 4
        assert "Fetched 2/2 target pages; 4 attempts made." in result.message
        assert result.data["grounded_result_count"] == 2

    @pytest.mark.asyncio
    async def test_web_search_handler_empty_query(self):
        result = await handle_web_search({"query": ""}, None)
        assert not result.success

    @pytest.mark.asyncio
    async def test_web_fetch_handler_surfaces_fetch_metadata(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "url": "https://example.com",
            "content": "hello world",
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "cache_hit": True,
        }
        with patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, return_value=fake_result):
            result = await handle_web_fetch({"url": "https://example.com"}, None)

        assert result.success
        assert result.message.startswith(
            "Fetched https://example.com (HTTP 200; text/html; cache hit)"
        )
        assert result.data["cache_hit"] is True

    @pytest.mark.asyncio
    async def test_web_fetch_handler_warns_when_page_looks_browser_gated(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "url": "https://example.com/app",
            "content": "Enable JavaScript to run this app.",
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "fetch_via": "http",
            "browser_fallback_used": False,
            "content_requires_browser": True,
        }
        with patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, return_value=fake_result):
            result = await handle_web_fetch({"url": "https://example.com/app"}, None)

        assert result.success
        assert "Page looks browser-rendered or JavaScript-gated." in result.message
        assert "Retry with browser_fallback=true" in result.message

    @pytest.mark.asyncio
    async def test_web_fetch_handler_reports_browser_recovery(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "url": "https://example.com/app",
            "content": "Rendered dashboard content",
            "status_code": 0,
            "content_type": "text/html; browser-rendered",
            "fetch_via": "browser",
            "browser_fallback_used": True,
            "content_requires_browser": False,
            "http_error": "HTTP 403 Forbidden",
        }
        with patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, return_value=fake_result):
            result = await handle_web_fetch(
                {"url": "https://example.com/app", "browser_fallback": True},
                None,
            )

        assert result.success
        assert "browser-rendered content" in result.message
        assert "Recovered via browser fallback after HTTP fetch error: HTTP 403 Forbidden" in result.message
        assert result.data["fetch_via"] == "browser"

    @pytest.mark.asyncio
    async def test_web_fetch_handler_http_error_returns_failure(self):
        from unittest.mock import AsyncMock, patch

        request = httpx.Request("GET", "https://example.com/missing")
        response = httpx.Response(404, request=request)
        error = httpx.HTTPStatusError("HTTP 404", request=request, response=response)

        with patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, side_effect=error):
            result = await handle_web_fetch({"url": "https://example.com/missing"}, None)

        assert not result.success
        assert result.error_type == "http_error"
        assert result.data["status_code"] == 404
        assert "HTTP 404" in result.message


class TestDirectWebToolBehavior:
    @pytest.mark.asyncio
    async def test_web_search_cache_hit_behavior(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("DAN_TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("DAN_BRAVE_API_KEY", raising=False)
        monkeypatch.setenv("DAN_WEB_SEARCH_CACHE_TTL_SECONDS", "900")

        mock_ddg = AsyncMock(return_value={
            "results": [{"title": "Original", "url": "https://example.com", "snippet": "snippet"}],
            "count": 1,
            "provider": "duckduckgo",
        })
        monkeypatch.setattr(web_search_tool_mod, "_ddg_search", mock_ddg)

        first = await web_search_tool_mod.web_search("example query", 3)
        second = await web_search_tool_mod.web_search("example query", 3)

        assert mock_ddg.await_count == 1
        assert first["cache_hit"] is False
        assert second["cache_hit"] is True

        second["results"][0]["title"] = "mutated"
        third = await web_search_tool_mod.web_search("example query", 3)
        assert third["results"][0]["title"] == "Original"
        assert third["cache_hit"] is True

    @pytest.mark.asyncio
    async def test_web_search_shares_inflight_request(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("DAN_TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("DAN_BRAVE_API_KEY", raising=False)

        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def slow_ddg(query: str, num_results: int) -> dict:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {
                "results": [{"title": "Shared", "url": "https://example.com", "snippet": "snippet"}],
                "count": 1,
                "provider": "duckduckgo",
            }

        monkeypatch.setattr(web_search_tool_mod, "_ddg_search", slow_ddg)

        first_task = asyncio.create_task(web_search_tool_mod.web_search("shared query", 1))
        await started.wait()
        second_task = asyncio.create_task(web_search_tool_mod.web_search("shared query", 1))
        await asyncio.sleep(0)
        release.set()

        first, second = await asyncio.gather(first_task, second_task)

        assert calls == 1
        assert first["shared_inflight"] is False
        assert second["shared_inflight"] is True
        assert first["cache_hit"] is False
        assert second["cache_hit"] is False

    @pytest.mark.asyncio
    async def test_web_search_records_provider_failures_on_fallback(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DAN_TAVILY_API_KEY", "tavily-key")
        monkeypatch.setenv("DAN_BRAVE_API_KEY", "brave-key")

        monkeypatch.setattr(
            web_search_tool_mod,
            "_tavily_search",
            AsyncMock(side_effect=_http_status_error("https://api.tavily.com/search", 503)),
        )
        monkeypatch.setattr(
            web_search_tool_mod,
            "_brave_search",
            AsyncMock(return_value={
                "results": [{"title": "Brave", "url": "https://brave.example", "snippet": "ok"}],
                "count": 1,
                "provider": "brave",
            }),
        )

        result = await web_search_tool_mod.web_search("fallback query", 2)

        assert result["provider"] == "brave"
        assert result["cache_hit"] is False
        assert len(result["provider_failures"]) == 1
        failure = result["provider_failures"][0]
        assert failure["provider"] == "tavily"
        assert failure["error_type"] == "provider_error"
        assert failure["retryable"] is True
        assert failure["status_code"] == 503
        assert failure["summary"].startswith("HTTP 503")
        assert failure["message"] == "HTTP 503"

    @pytest.mark.asyncio
    async def test_web_fetch_cache_hit_behavior(self, monkeypatch: pytest.MonkeyPatch):
        calls = 0
        user_agents: list[str | None] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            user_agents.append(request.headers.get("User-Agent"))
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<h1>Example Domain</h1><p>Hello world.</p>",
            )

        _patch_fetch_transport(monkeypatch, handler)
        monkeypatch.setenv("DAN_WEB_FETCH_CACHE_TTL_SECONDS", "900")

        first = await web_fetch_tool_mod.web_fetch("https://example.com")
        second = await web_fetch_tool_mod.web_fetch("https://example.com")

        assert calls == 1
        assert user_agents and user_agents[0] and "deep-agent-network" in user_agents[0]
        assert first["cache_hit"] is False
        assert second["cache_hit"] is True

        second["content"] = "mutated"
        third = await web_fetch_tool_mod.web_fetch("https://example.com")
        assert "Example Domain" in third["content"]
        assert third["cache_hit"] is True

    @pytest.mark.asyncio
    async def test_web_fetch_shares_inflight_request(self, monkeypatch: pytest.MonkeyPatch):
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/plain"},
                text="shared content",
            )

        _patch_fetch_transport(monkeypatch, handler)

        first_task = asyncio.create_task(web_fetch_tool_mod.web_fetch("https://example.com/shared"))
        await started.wait()
        second_task = asyncio.create_task(web_fetch_tool_mod.web_fetch("https://example.com/shared"))
        await asyncio.sleep(0)
        release.set()

        first, second = await asyncio.gather(first_task, second_task)

        assert calls == 1
        assert first["shared_inflight"] is False
        assert second["shared_inflight"] is True
        assert first["cache_hit"] is False
        assert second["cache_hit"] is False

    @pytest.mark.asyncio
    async def test_web_fetch_raises_on_http_error(self, monkeypatch: pytest.MonkeyPatch):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                request=request,
                headers={"content-type": "text/html"},
                text="<h1>Not Found</h1>",
            )

        _patch_fetch_transport(monkeypatch, handler)

        with pytest.raises(httpx.HTTPStatusError):
            await web_fetch_tool_mod.web_fetch("https://example.com/missing")

    @pytest.mark.asyncio
    async def test_web_fetch_detects_browser_shell_content(self, monkeypatch: pytest.MonkeyPatch):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<html><body>Please enable JavaScript to run this app.</body></html>",
            )

        _patch_fetch_transport(monkeypatch, handler)

        result = await web_fetch_tool_mod.web_fetch("https://example.com/app", browser_fallback=False)

        assert result["fetch_via"] == "http"
        assert result["content_requires_browser"] is True
        assert result["browser_fallback_used"] is False

    @pytest.mark.asyncio
    async def test_web_fetch_browser_fallback_recovers_http_error(self, monkeypatch: pytest.MonkeyPatch):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, request=request)

        _patch_fetch_transport(monkeypatch, handler)
        controller = MockBrowserController(responses={
            "open": {"status": "ok", "url": "https://example.com/app", "title": "Example App"},
            "extract_text": "Rendered dashboard content",
        })

        async def fake_get_controller() -> MockBrowserController:
            return controller

        monkeypatch.setattr("dan.tools._browser_session.get_controller", fake_get_controller)

        result = await web_fetch_tool_mod.web_fetch("https://example.com/app", browser_fallback=True)

        assert result["fetch_via"] == "browser"
        assert result["browser_fallback_used"] is True
        assert result["browser_fallback_reason"] == "http_fetch_failed"
        assert "403" in result["http_error"]
        assert [action["action"] for action in controller.actions] == ["open", "wait_for", "extract_text"]

    @pytest.mark.asyncio
    async def test_web_fetch_browser_fallback_recovers_browser_shell(self, monkeypatch: pytest.MonkeyPatch):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<html><body>Enable JavaScript to run this app.</body></html>",
            )

        _patch_fetch_transport(monkeypatch, handler)
        controller = MockBrowserController(responses={
            "open": {"status": "ok", "url": "https://example.com/app", "title": "Example App"},
            "extract_text": "Rendered dashboard content",
        })

        async def fake_get_controller() -> MockBrowserController:
            return controller

        monkeypatch.setattr("dan.tools._browser_session.get_controller", fake_get_controller)

        result = await web_fetch_tool_mod.web_fetch("https://example.com/app", browser_fallback=True)

        assert result["fetch_via"] == "browser"
        assert result["browser_fallback_used"] is True
        assert result["browser_fallback_reason"] == "http_fetch_unusable"


# ── Layer 3: Post-execution reflection ────────────────────────────


class TestNumericClaimRegex:
    def test_matches_dollar_amounts(self):
        assert _NUMERIC_CLAIM_RE.search("The price is $70.11")

    def test_matches_percentages(self):
        assert _NUMERIC_CLAIM_RE.search("Up 15.3% today")

    def test_no_match_plain_text(self):
        assert _NUMERIC_CLAIM_RE.search("Hello world") is None


class TestSearchWebGroundingGate:
    def test_search_web_requires_more_than_snippet_only_search(self):
        missing = _missing_action_hints(
            ["search_web"],
            {"web_search"},
            tool_results=[{
                "tool_name": "web_search",
                "status": "success",
                "cap_result": CapabilityResult(success=True, message="", data={"count": 3}),
            }],
        )

        assert missing == ["search_web"]

    def test_search_web_satisfied_by_grounded_search_results(self):
        missing = _missing_action_hints(
            ["search_web"],
            {"web_search"},
            tool_results=[{
                "tool_name": "web_search",
                "status": "success",
                "cap_result": CapabilityResult(
                    success=True,
                    message="",
                    data={"grounded_result_count": 2},
                ),
            }],
        )

        assert missing == []


class TestRelevantExcerptExtraction:
    def test_extract_relevant_excerpt_prefers_matching_paragraph(self):
        content = (
            "Intro paragraph about navigation.\n\n"
            "Revenue grew 24% in 2025 and operating margin expanded to 18%.\n\n"
            "Footer links and unrelated content."
        )
        excerpt = _extract_relevant_excerpt(content, "revenue margin 2025", budget=160)
        assert "Revenue grew 24% in 2025" in excerpt
        assert "Footer links" not in excerpt


class TestWebGroundingTriggers:
    def test_has_explicit_web_trigger_matches_standalone_token(self):
        assert has_explicit_web_trigger("Use @Web to verify the latest DAN release.")
        assert not has_explicit_web_trigger("The @webcam driver is broken.")

    def test_should_require_web_grounding_on_explicit_web_trigger(self):
        assert should_require_web_grounding(
            "Use @web to confirm the latest DAN release date.",
            mode="conversation",
        )

    def test_should_not_require_web_grounding_for_local_workflow_run_question(self):
        assert not should_require_web_grounding(
            "Can you test run this workflow?",
            mode="conversation",
            required_action_hints=["workflow_run"],
        )


class TestStructuredSearchSources:
    def test_extract_cited_sources_prefers_structured_search_payload(self):
        sources = _extract_cited_sources([{
            "tool_name": "web_search",
            "result_data": {
                "search_result_set": {
                    "query": "latest dan",
                    "results": [
                        {
                            "index": 1,
                            "title": "Update",
                            "url": "https://example.com/report?utm_source=newsletter",
                            "snippet": "Latest DAN update",
                            "provider": "serper",
                        }
                    ],
                }
            },
            "output_preview": "",
        }])

        assert sources == ["https://example.com/report"]


class TestCitationVerification:
    def test_extract_inline_citations_handles_ranges_and_adjacent_refs(self):
        citations = extract_inline_citations(
            "Revenue rose 24% in 2025 [1-2]. Margin improved too [3][4]."
        )
        assert [c.index for c in citations] == [1, 2, 3, 4]

    def test_verify_response_citations_checks_numeric_claims(self):
        from dan.server.search_models import SearchResult

        search_results = [
            SearchResult(
                index=1,
                title="Report",
                url="https://example.com/report",
                snippet="Revenue grew 24% in 2025.",
                fetched_content="Revenue grew 24% in 2025. Margin was 18%.",
                provider="tavily",
                result_kind="organic",
            )
        ]
        verifications = verify_response_citations(
            "Revenue grew 24% in 2025 [1].",
            search_results,
        )
        assert len(verifications) == 1
        assert verifications[0].verified is True

    def test_build_citation_records_uses_verification_metadata(self):
        from dan.server.search_models import CitationVerification, SearchResult

        results = [
            SearchResult(
                index=1,
                title="Report",
                url="https://example.com/report",
                snippet="Revenue grew 24% in 2025.",
                fetched_content="Revenue grew 24% in 2025.",
                provider="tavily",
            )
        ]
        records = build_citation_records(
            "Revenue grew 24% in 2025 [1].",
            results,
            verifications=[
                CitationVerification(
                    citation_index=1,
                    claim_text="Revenue grew 24% in 2025 [1]",
                    source_url="https://example.com/report",
                    source_excerpt_match="Revenue grew 24% in 2025.",
                    verified=True,
                    confidence=1.0,
                    reason="matched claim terms in source",
                )
            ],
        )
        assert len(records) == 1
        assert records[0].verified is True

    def test_native_anthropic_citations_are_captured_without_bracket_markers(self):
        from dan.server.search_models import SearchResult

        results = [
            SearchResult(
                index=1,
                title="Report",
                url="https://example.com/report",
                snippet="Revenue grew 24% in 2025.",
                fetched_content="Revenue grew 24% in 2025. Margin was 18%.",
                provider="serper",
            )
        ]
        raw_assistant_message = {
            "role": "assistant",
            "anthropic_content": [
                {
                    "type": "text",
                    "text": "The company reported stronger growth.",
                    "citations": [
                        {
                            "cited_text": "Revenue grew 24% in 2025.",
                            "url": "https://example.com/report",
                        }
                    ],
                }
            ],
        }

        verifications = verify_response_citations(
            "The company reported stronger growth.",
            results,
            raw_assistant_message=raw_assistant_message,
        )
        records = build_citation_records(
            "The company reported stronger growth.",
            results,
            verifications=verifications,
            raw_assistant_message=raw_assistant_message,
        )

        assert len(verifications) == 1
        assert verifications[0].verified is True
        assert len(records) == 1
        assert records[0].claim_text == "Revenue grew 24% in 2025."
        assert records[0].source_url == "https://example.com/report"

    def test_build_citation_records_does_not_reuse_verification_for_other_claims_same_source(self):
        from dan.server.search_models import CitationVerification, SearchResult

        results = [
            SearchResult(
                index=1,
                title="Report",
                url="https://example.com/report",
                snippet="Revenue grew 24% in 2025.",
                fetched_content="Revenue grew 24% in 2025. The company launched a new product.",
                provider="serper",
            )
        ]
        records = build_citation_records(
            "Revenue grew 24% in 2025 [1]. The company launched a new product [1].",
            results,
            verifications=[
                CitationVerification(
                    citation_index=1,
                    claim_text="Revenue grew 24% in 2025 [1].",
                    source_url="https://example.com/report",
                    source_excerpt_match="Revenue grew 24% in 2025.",
                    verified=True,
                    confidence=1.0,
                    reason="matched claim terms in source",
                )
            ],
        )

        assert len(records) == 2
        assert records[0].verified is True
        assert records[1].claim_text.startswith("The company launched a new product [1]")
        assert records[1].verified is None

    def test_search_web_satisfied_by_web_fetch(self):
        missing = _missing_action_hints(
            ["search_web"],
            {"web_search", "web_fetch"},
            tool_results=[{
                "tool_name": "web_fetch",
                "status": "success",
                "cap_result": CapabilityResult(success=True, message="", data={}),
            }],
        )

        assert missing == []


# Tests removed: TestNeedsLiveData, TestSolverHeuristic, TestCheckUnsourcedClaims
# depended on deleted legacy modules (solver.py, classifier.py, context_resolver.py)
