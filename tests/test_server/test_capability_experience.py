"""Tests for experience/discovery capability handlers (25-2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.engine.experience import WorkflowExperience
from dan.engine.error_memory import CausalPrinciple
from dan.meta.discovery import DiscoveryResult, ToolInfo, SkillInfo, PatternInfo, WorkflowMatch
from dan.server.capability_registry import CapabilityContext, ChatCapabilityRegistry
from dan.server.capability_handlers import (
    handle_discover_capabilities,
    handle_get_learned_principles,
    handle_get_workflow_details,
    handle_search_run_history,
    handle_search_workflow_history,
    register_experience_capabilities,
)


# ── search_workflow_history ─────────────────────────────────────────

class TestSearchWorkflowHistory:

    @pytest.mark.asyncio
    async def test_query_required(self):
        ctx = CapabilityContext(workflow_id="test")
        result = await handle_search_workflow_history({"query": ""}, ctx)
        assert result.success is False
        assert "query" in result.message.lower()

    @pytest.mark.asyncio
    async def test_semantic_search_success(self):
        mock_index = AsyncMock()
        mock_index.search_similar.return_value = [("wf1", 0.9), ("wf2", 0.7)]
        mock_store = AsyncMock()
        exp1 = WorkflowExperience(
            workflow_id="wf1",
            name="Lit Review",
            description="Literature review workflow",
            run_count=4,
            success_count=3,
        )
        mock_store.load_experience.return_value = exp1
        ctx = CapabilityContext(
            workflow_id="test",
            experience_index=mock_index,
            experience_store=mock_store,
        )
        result = await handle_search_workflow_history(
            {"query": "literature review", "top_k": 5},
            ctx,
        )
        assert result.success is True
        assert "wf1" in result.message
        mock_index.search_similar.assert_called_once_with("literature review", top_k=5)

    @pytest.mark.asyncio
    async def test_lexical_fallback_when_no_index(self):
        mock_store = AsyncMock()
        exp = WorkflowExperience(
            workflow_id="lit-wf",
            name="Literature Review",
            description="Academic lit review",
            run_count=2,
            success_count=2,
        )
        mock_store.list_experiences.return_value = [exp]
        mock_store.load_experience.return_value = exp
        ctx = CapabilityContext(
            workflow_id="test",
            experience_index=None,
            experience_store=mock_store,
        )
        result = await handle_search_workflow_history(
            {"query": "literature", "top_k": 5},
            ctx,
        )
        assert result.success is True
        assert "lit-wf" in result.message
        mock_store.list_experiences.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_store_available(self):
        ctx = CapabilityContext(
            workflow_id="test",
            experience_index=None,
            experience_store=None,
        )
        result = await handle_search_workflow_history(
            {"query": "anything", "top_k": 5},
            ctx,
        )
        assert result.success is False
        assert "not available" in result.message.lower()


# ── get_workflow_details ────────────────────────────────────────────

class TestGetWorkflowDetails:

    @pytest.mark.asyncio
    async def test_workflow_id_required(self):
        ctx = CapabilityContext(workflow_id="test")
        result = await handle_get_workflow_details({"workflow_id": ""}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_no_store(self):
        ctx = CapabilityContext(workflow_id="test", experience_store=None)
        result = await handle_get_workflow_details({"workflow_id": "wf1"}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_found(self):
        exp = WorkflowExperience(
            workflow_id="wf1",
            name="Regression Pipeline",
            description="ML regression",
            run_count=10,
            success_count=8,
            node_types_used=["llm_operator", "tool_operator"],
        )
        mock_store = AsyncMock()
        mock_store.load_experience.return_value = exp
        ctx = CapabilityContext(workflow_id="test", experience_store=mock_store)
        result = await handle_get_workflow_details({"workflow_id": "wf1"}, ctx)
        assert result.success is True
        assert "Regression" in result.message
        assert "80%" in result.message

    @pytest.mark.asyncio
    async def test_not_found(self):
        mock_store = AsyncMock()
        mock_store.load_experience.return_value = None
        ctx = CapabilityContext(workflow_id="test", experience_store=mock_store)
        result = await handle_get_workflow_details({"workflow_id": "missing"}, ctx)
        assert result.success is True
        assert "No experience" in result.message


# ── search_run_history ─────────────────────────────────────────────

class TestSearchRunHistory:

    @pytest.mark.asyncio
    async def test_no_run_store(self):
        ctx = CapabilityContext(workflow_id="test", run_store=None)
        result = await handle_search_run_history({}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_with_summaries(self):
        mock_store = MagicMock()
        mock_store.list_summaries.return_value = [
            {
                "run_id": "r1",
                "workflow_id": "wf1",
                "status": "completed",
                "elapsed_seconds": 12.5,
            },
        ]
        ctx = CapabilityContext(workflow_id="test", run_store=mock_store)
        result = await handle_search_run_history(
            {"workflow_id": "wf1", "limit": 20},
            ctx,
        )
        assert result.success is True
        assert "r1" in result.message
        mock_store.list_summaries.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_results(self):
        mock_store = MagicMock()
        mock_store.list_summaries.return_value = []
        ctx = CapabilityContext(workflow_id="test", run_store=mock_store)
        result = await handle_search_run_history({}, ctx)
        assert result.success is True
        assert "No runs" in result.message


# ── get_learned_principles ─────────────────────────────────────────

class TestGetLearnedPrinciples:

    @pytest.mark.asyncio
    async def test_no_principle_store(self):
        ctx = CapabilityContext(workflow_id="test", principle_store=None)
        result = await handle_get_learned_principles({}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_with_principles(self):
        p1 = CausalPrinciple(
            condition="tool receives malformed JSON",
            action="validate input schema",
            confidence=0.85,
            workflow_id="wf1",
        )
        mock_store = AsyncMock()
        mock_store.load_principles.return_value = [p1]
        ctx = CapabilityContext(workflow_id="test", principle_store=mock_store)
        result = await handle_get_learned_principles(
            {"min_confidence": 0.3, "scope": "global"},
            ctx,
        )
        assert result.success is True
        assert "malformed JSON" in result.message
        assert "85%" in result.message

    @pytest.mark.asyncio
    async def test_query_filter(self):
        p1 = CausalPrinciple(
            condition="timeout in web_search",
            action="increase timeout",
            confidence=0.7,
        )
        mock_store = AsyncMock()
        mock_store.load_principles.return_value = [p1]
        ctx = CapabilityContext(workflow_id="test", principle_store=mock_store)
        result = await handle_get_learned_principles(
            {"query": "timeout", "min_confidence": 0.3},
            ctx,
        )
        assert result.success is True
        assert "timeout" in result.message


# ── discover_capabilities ──────────────────────────────────────────

class TestDiscoverCapabilities:

    @pytest.mark.asyncio
    async def test_no_discovery_service(self):
        ctx = CapabilityContext(workflow_id="test", discovery_service=None)
        result = await handle_discover_capabilities({"query": "rag"}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_success(self):
        mock_discovery = AsyncMock()
        mock_discovery.discover_all.return_value = DiscoveryResult(
            tools=[ToolInfo(tool_id="web_search", description="Search the web")],
            skills=[SkillInfo(name="rag", description="RAG pattern")],
            patterns=[PatternInfo(name="rag_qa", description="RAG pipeline")],
            workflows=[
                WorkflowMatch(workflow_id="wf1", name="RAG Demo", score=0.9),
            ],
        )
        ctx = CapabilityContext(workflow_id="test", discovery_service=mock_discovery)
        result = await handle_discover_capabilities(
            {"query": "rag", "top_k": 5},
            ctx,
        )
        assert result.success is True
        assert "web_search" in result.message
        assert "rag" in result.message
        mock_discovery.discover_all.assert_called_once_with("rag", top_k=5)


# ── Registry ────────────────────────────────────────────────────────

class TestExperienceCapabilitiesRegistered:

    def test_all_five_tools_registered(self):
        reg = ChatCapabilityRegistry()
        register_experience_capabilities(reg)
        names = reg.list_tool_names("ask")
        assert "search_workflow_history" in names
        assert "get_workflow_details" in names
        assert "search_run_history" in names
        assert "get_learned_principles" in names
        assert "discover_capabilities" in names
