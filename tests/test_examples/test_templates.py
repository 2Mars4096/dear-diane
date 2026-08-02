"""Tests for the 5 workflow templates added in plan 7-4."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import pytest

from dan.engine import Engine, EngineConfig
from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.executor import ExecutorRegistry, NodeResult
from dan.engine.state import NodeStatus
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.graph import Graph
from dan.models.nodes import LLMOperator, NodeBase

# ---------------------------------------------------------------------------
# Import template build functions
# ---------------------------------------------------------------------------

_examples_dir = os.path.join(os.path.dirname(__file__), "..", "..", "examples")
if _examples_dir not in sys.path:
    sys.path.insert(0, _examples_dir)

from simple_chain import build_simple_chain
from fan_out_fan_in import build_fan_out_fan_in
from review_revise import build_review_revise
from rag_qa import build_rag_qa
from react_agent import build_react_agent

if _examples_dir in sys.path:
    sys.path.remove(_examples_dir)


# ---------------------------------------------------------------------------
# Mock LLM executor
# ---------------------------------------------------------------------------


class TemplateMockLLM:
    """Returns canned responses keyed by node.id."""

    RESPONSES: dict[str, dict[str, Any]] = {
        # simple_chain
        "idea_gen": {"text": "1. Solar roads 2. Wind drones 3. Algae fuel"},
        "expand": {
            "best_idea": "Solar roads",
            "summary": "Solar roads embed photovoltaic cells into road surfaces to generate clean energy while serving transportation.",
        },
        # fan_out_fan_in
        "gen_subtopics": {
            "subtopics": ["solar panels", "wind turbines", "battery storage", "smart grids"],
        },
        "summarize": {"text": "This subtopic involves key advances in renewable energy technology."},
        # review_revise
        "draft": {"text": "Initial paragraph about sustainable energy and its global impact."},
        "review": {"quality_score": 9, "feedback": "Well structured, minor refinements possible."},
        "revise": {"text": "Revised paragraph with improved clarity and supporting evidence."},
        # rag_qa
        "answer": {
            "answer": "The main features include a visual editor, builder DSL, and multi-agent workflows.",
            "confidence": "high",
            "relevant_chunks_used": 3,
        },
        # react_agent
        "think": {
            "thought": "I should search for the current population.",
            "action": "search",
            "action_input": "Tokyo population 2025",
            "done": False,
            "final_answer": "",
        },
    }

    def __init__(self, extra_responses: dict[str, dict[str, Any]] | None = None) -> None:
        self._responses = {**self.RESPONSES, **(extra_responses or {})}
        self._call_count: dict[str, int] = {}

    async def execute(
        self, node: NodeBase, inputs: dict[str, Any], context: Any
    ) -> NodeResult:
        assert isinstance(node, LLMOperator)
        nid = node.id

        count = self._call_count.get(nid, 0)
        self._call_count[nid] = count + 1

        # react_agent: second think call finishes
        if nid == "think" and count >= 1:
            return NodeResult(
                outputs={
                    "thought": "I have the answer now.",
                    "action": "none",
                    "action_input": "",
                    "done": True,
                    "final_answer": "Tokyo population is approximately 14 million.",
                },
                status=NodeStatus.COMPLETED,
            )

        resp = self._responses.get(nid)
        if resp is None:
            return NodeResult(outputs={}, status=NodeStatus.FAILED, error=f"No mock for {nid}")

        if node.output_json_schema is not None:
            return NodeResult(outputs=dict(resp), status=NodeStatus.COMPLETED)
        return NodeResult(
            outputs={"text": resp.get("text", str(resp))},
            status=NodeStatus.COMPLETED,
        )


# ---------------------------------------------------------------------------
# Mock tools for rag_qa and react_agent
# ---------------------------------------------------------------------------


async def _mock_file_read(path: str = "", **kw: Any) -> dict[str, Any]:
    return {
        "content": "Deep Agent Network is a visual workflow platform. It supports LLM, tool, and code nodes.",
        "size_bytes": 80,
    }


async def _mock_text_chunk(
    text: str = "", chunk_size: int = 500, overlap: int = 50, **kw: Any
) -> dict[str, Any]:
    words = text.split()
    chunks = [" ".join(words[i:i + 10]) for i in range(0, len(words), 8)]
    return {"chunks": chunks, "chunk_count": len(chunks)}


async def _mock_web_search(query: str = "", **kw: Any) -> dict[str, Any]:
    return {"results": f"Search results for: {query}. Tokyo has 14 million people."}


async def _mock_web_fetch(url: str = "", **kw: Any) -> dict[str, Any]:
    return {"content": f"Fetched content from {url}"}


def _build_mock_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("file_read", _mock_file_read)
    registry.register("text_chunk", _mock_text_chunk)
    registry.register("web_search", _mock_web_search)
    registry.register("web_fetch", _mock_web_fetch)
    return registry


def _node_tool_ids(node: NodeBase) -> list[str]:
    if getattr(node, "tool_id", ""):
        return [str(node.tool_id)]
    tool_ids = getattr(node, "tool_ids", None)
    if isinstance(tool_ids, list):
        return [str(tool_id) for tool_id in tool_ids if tool_id]
    return []


def _build_engine(
    mock_llm: TemplateMockLLM | None = None,
    tool_registry: ToolRegistry | None = None,
) -> Engine:
    from dan.worker.executor import WorkerExecutor

    config = EngineConfig(checkpoint_enabled=False)
    tr = tool_registry or _build_mock_tool_registry()
    llm_executor = mock_llm or TemplateMockLLM()
    tool_executor = ToolExecutor(tr)
    er = ExecutorRegistry()
    er.register("llm_operator", llm_executor)
    er.register("tool_operator", tool_executor)
    er.register(
        "worker",
        WorkerExecutor(llm_executor=llm_executor, tool_executor=tool_executor),
    )
    return Engine(config=config, executor_registry=er, checkpoint_store=NullCheckpointStore())


# ===========================================================================
# Graph compilation tests
# ===========================================================================


class TestSimpleChain:
    def test_graph_compiles(self):
        g = build_simple_chain()
        assert g.version == "dan_graph_v1"
        assert len(g.nodes) == 3
        ids = {n.id for n in g.nodes}
        assert "idea_gen" in ids
        assert "expand" in ids
        assert "analyze" in ids
        assert "idea_gen" in g.entry_points
        assert "analyze" in g.exit_points

    def test_json_roundtrip(self):
        g = build_simple_chain()
        restored = Graph.model_validate_json(g.model_dump_json())
        assert len(restored.nodes) == len(g.nodes)
        assert len(restored.edges) == len(g.edges)

    @pytest.mark.asyncio
    async def test_mock_run(self):
        g = build_simple_chain()
        engine = _build_engine()
        result = await engine.run(g, inputs={"topic": "test"})
        assert result.success, f"Failed: {result.errors}"
        assert "analyze" in {nid for nid, s in result.node_statuses.items() if s == "completed"}


class TestFanOutFanIn:
    def test_graph_compiles(self):
        g = build_fan_out_fan_in()
        assert len(g.nodes) == 3
        assert len(g.sub_graphs) == 1
        assert "research_body" in g.sub_graphs
        assert "gen_subtopics" in g.entry_points
        assert "aggregate" in g.exit_points

    def test_json_roundtrip(self):
        g = build_fan_out_fan_in()
        restored = Graph.model_validate_json(g.model_dump_json())
        assert set(restored.sub_graphs.keys()) == set(g.sub_graphs.keys())

    @pytest.mark.asyncio
    async def test_mock_run(self):
        g = build_fan_out_fan_in()
        engine = _build_engine()
        result = await engine.run(g, inputs={"topic": "test"})
        assert result.success, f"Failed: {result.errors}"
        completed = {nid for nid, s in result.node_statuses.items() if s == "completed"}
        assert "gen_subtopics" in completed
        assert "research" in completed
        assert "aggregate" in completed


class TestReviewRevise:
    def test_graph_compiles(self):
        g = build_review_revise()
        assert len(g.nodes) == 3
        assert "review_loop_body" in g.sub_graphs
        ids = {n.id for n in g.nodes}
        assert "draft" in ids
        assert "review_loop" in ids
        assert "draft" in g.entry_points

    def test_json_roundtrip(self):
        g = build_review_revise()
        restored = Graph.model_validate_json(g.model_dump_json())
        assert len(restored.nodes) == len(g.nodes)

    @pytest.mark.asyncio
    async def test_mock_run(self):
        g = build_review_revise()
        engine = _build_engine()
        result = await engine.run(g, inputs={"topic": "test"})
        assert result.success, f"Failed: {result.errors}"
        completed = {nid for nid, s in result.node_statuses.items() if s == "completed"}
        assert "draft" in completed
        assert "review_loop" in completed


class TestRagQA:
    def test_graph_compiles(self):
        g = build_rag_qa()
        assert len(g.nodes) == 4
        node_ids = {n.id for n in g.nodes}
        assert {"setup", "read_file", "chunk", "answer"} == node_ids
        assert g.node_by_id("answer") is not None
        assert getattr(g.node_by_id("setup"), "code", "")

    def test_json_roundtrip(self):
        g = build_rag_qa()
        restored = Graph.model_validate_json(g.model_dump_json())
        assert len(restored.nodes) == len(g.nodes)

    def test_tool_nodes_present(self):
        g = build_rag_qa()
        tool_ids = {tool_id for node in g.nodes for tool_id in _node_tool_ids(node)}
        assert "file_read" in tool_ids
        assert "text_chunk" in tool_ids

    @pytest.mark.asyncio
    async def test_mock_run(self):
        g = build_rag_qa()
        engine = _build_engine()
        result = await engine.run(
            g, inputs={"question": "What is DAN?", "file_path": "README.md"}
        )
        assert result.success, f"Failed: {result.errors}"
        completed = {nid for nid, s in result.node_statuses.items() if s == "completed"}
        assert "read_file" in completed
        assert "chunk" in completed
        assert "answer" in completed


class TestReactAgent:
    def test_graph_compiles(self):
        g = build_react_agent()
        assert len(g.nodes) == 2
        assert "agent_loop_body" in g.sub_graphs
        sub = g.sub_graphs["agent_loop_body"]
        sub_ids = {n.id for n in sub.nodes}
        assert "think" in sub_ids
        assert "search" in sub_ids
        assert "fetch" in sub_ids
        assert "merge" in sub_ids

    def test_json_roundtrip(self):
        g = build_react_agent()
        restored = Graph.model_validate_json(g.model_dump_json())
        assert set(restored.sub_graphs.keys()) == set(g.sub_graphs.keys())

    def test_tool_nodes_in_subgraph(self):
        g = build_react_agent()
        sub = g.sub_graphs["agent_loop_body"]
        tool_ids = {tool_id for node in sub.nodes for tool_id in _node_tool_ids(node)}
        assert "web_search" in tool_ids
        assert "web_fetch" in tool_ids

    @pytest.mark.asyncio
    async def test_mock_run(self):
        g = build_react_agent()
        engine = _build_engine()
        result = await engine.run(g, inputs={"task": "Find Tokyo population"})
        assert result.success, f"Failed: {result.errors}"
        completed = {nid for nid, s in result.node_statuses.items() if s == "completed"}
        assert "init" in completed
        assert "agent_loop" in completed


# ===========================================================================
# Cross-template tests
# ===========================================================================


class TestAllTemplates:
    """Shared assertions that apply to every template."""

    BUILDERS = [
        ("simple_chain", build_simple_chain),
        ("fan_out_fan_in", build_fan_out_fan_in),
        ("review_revise", build_review_revise),
        ("rag_qa", build_rag_qa),
        ("react_agent", build_react_agent),
    ]

    @pytest.mark.parametrize("name,builder", BUILDERS)
    def test_version_is_set(self, name: str, builder):
        g = builder()
        assert g.version == "dan_graph_v1"

    @pytest.mark.parametrize("name,builder", BUILDERS)
    def test_has_entry_and_exit(self, name: str, builder):
        g = builder()
        assert len(g.entry_points) >= 1, f"{name} has no entry points"
        assert len(g.exit_points) >= 1, f"{name} has no exit points"

    @pytest.mark.parametrize("name,builder", BUILDERS)
    def test_json_parseable(self, name: str, builder):
        g = builder()
        raw = json.loads(g.model_dump_json())
        assert raw["version"] == "dan_graph_v1"
        assert isinstance(raw["nodes"], list)
        assert isinstance(raw["edges"], list)

    @pytest.mark.parametrize("name,builder", BUILDERS)
    def test_metadata_name_set(self, name: str, builder):
        g = builder()
        assert g.metadata.name == name
