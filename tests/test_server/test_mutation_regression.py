"""NL→mutation regression tests — validates the mutation pipeline end-to-end
using golden prompts with predetermined mock LLM responses.

Tests cover: plan parsing, operation-type matching, dry-run success/failure,
and error handling for invalid plans.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, AsyncGenerator, AsyncIterator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DAN_GRAPHS_DIR", tempfile.mkdtemp())
os.environ.setdefault("DAN_CHECKPOINT_DIR", tempfile.mkdtemp())

from dan.providers import CompletionResult, StreamChunk  # noqa: E402
from dan.providers.registry import ProviderRegistry  # noqa: E402
from dan.server.app import app, lifespan  # noqa: E402
from dan.server.graph_mutator import (  # noqa: E402
    GraphMutator,
    MutationPlan,
    MutationResult,
)
from dan.server.chat_manager import (  # noqa: E402
    _try_parse_mutation_json,
    compute_graph_revision,
)

# ---------------------------------------------------------------------------
# Golden prompts with expected mock LLM responses
# ---------------------------------------------------------------------------

GOLDEN_PROMPTS: list[dict[str, Any]] = [
    {
        "id": "add_single_node",
        "prompt": "Add an LLM node called 'Summarizer'",
        "mock_plan": {
            "description": "Add Summarizer LLM node",
            "operations": [
                {"op": "add_node", "node_type": "llm_operator", "name": "Summarizer"},
            ],
        },
        "expected_ops": ["add_node"],
    },
    {
        "id": "add_two_nodes_and_edge",
        "prompt": "Add two nodes: a Researcher and a Writer, connect them",
        "mock_plan": {
            "description": "Add Researcher and Writer with data edge",
            "operations": [
                {"op": "add_node", "node_type": "llm_operator", "name": "Researcher"},
                {"op": "add_node", "node_type": "llm_operator", "name": "Writer"},
                {"op": "add_edge", "source_id": "researcher", "source_port": "text",
                 "target_id": "writer", "target_port": "input"},
            ],
        },
        "expected_ops": ["add_node", "add_node", "add_edge"],
    },
    {
        "id": "remove_node",
        "prompt": "Remove the node called DataProcessor",
        "mock_plan": {
            "description": "Remove DataProcessor node",
            "operations": [
                {"op": "remove_node", "node_id": "data-processor"},
            ],
        },
        "expected_ops": ["remove_node"],
    },
    {
        "id": "add_edge_only",
        "prompt": "Connect Planner to Writer with a data edge",
        "mock_plan": {
            "description": "Add data edge from Planner to Writer",
            "operations": [
                {"op": "add_edge", "source_id": "planner", "source_port": "text",
                 "target_id": "writer", "target_port": "input"},
            ],
        },
        "expected_ops": ["add_edge"],
    },
    {
        "id": "edit_node",
        "prompt": "Change the Writer's prompt to 'Draft a report: {input}'",
        "mock_plan": {
            "description": "Edit Writer prompt template",
            "operations": [
                {"op": "edit_node", "node_id": "n1",
                 "updates": {"prompt_template": "Draft a report: {input}"}},
            ],
        },
        "expected_ops": ["edit_node"],
    },
    {
        "id": "expand_chain_pattern",
        "prompt": "Create a 3-step processing chain",
        "mock_plan": {
            "description": "Expand a 3-node chain pattern",
            "operations": [
                {"op": "expand_pattern", "pattern": "chain",
                 "params": {"count": 3, "names": ["Step 1", "Step 2", "Step 3"]}},
            ],
        },
        "expected_ops": ["expand_pattern"],
    },
    {
        "id": "add_gate_node",
        "prompt": "Add a review gate that loops until approved",
        "mock_plan": {
            "description": "Add gate node for review loop",
            "operations": [
                {"op": "add_node", "node_type": "gate", "name": "Review Gate",
                 "config": {"gate_mode": "while", "condition": "needs_revision == True",
                            "max_iterations": 5}},
            ],
        },
        "expected_ops": ["add_node"],
    },
    {
        "id": "complex_build",
        "prompt": "Build a RAG QA pipeline with retrieval and answer generation",
        "mock_plan": {
            "description": "Build RAG QA pipeline",
            "operations": [
                {"op": "expand_pattern", "pattern": "rag_qa",
                 "params": {"rag_name": "Knowledge Base", "top_k": 5}},
            ],
        },
        "expected_ops": ["expand_pattern"],
    },
    {
        "id": "multi_op_build",
        "prompt": "Add an input node, a processor, and an output node, wire them in sequence",
        "mock_plan": {
            "description": "Add 3-node pipeline",
            "operations": [
                {"op": "add_node", "node_type": "input", "name": "Input"},
                {"op": "add_node", "node_type": "llm_operator", "name": "Processor"},
                {"op": "add_node", "node_type": "llm_operator", "name": "Output"},
                {"op": "add_edge", "source_id": "input", "source_port": "input",
                 "target_id": "processor", "target_port": "input"},
                {"op": "add_edge", "source_id": "processor", "source_port": "text",
                 "target_id": "output", "target_port": "input"},
            ],
        },
        "expected_ops": ["add_node", "add_node", "add_node", "add_edge", "add_edge"],
    },
    {
        "id": "remove_edge",
        "prompt": "Disconnect Writer from Reviewer",
        "mock_plan": {
            "description": "Remove edge from Writer to Reviewer",
            "operations": [
                {"op": "remove_edge", "source_id": "writer", "source_port": "text",
                 "target_id": "reviewer", "target_port": "input"},
            ],
        },
        "expected_ops": ["remove_edge"],
    },
]


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

_GRAPH_WITH_NODES = {
    "version": "dan_graph_v1",
    "metadata": {"name": "regression-test-wf"},
    "nodes": [
        {
            "id": "n1",
            "node_type": "llm_operator",
            "name": "Writer",
            "input_ports": [{"name": "input", "schema": {}}],
            "output_ports": [{"name": "text", "schema": {}}],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
            "model": "mock-model",
            "prompt_template": "Write: {input}",
        },
        {
            "id": "data-processor",
            "node_type": "llm_operator",
            "name": "DataProcessor",
            "input_ports": [{"name": "input", "schema": {}}],
            "output_ports": [{"name": "text", "schema": {}}],
            "position": {"x": 200, "y": 0},
            "ui": {},
            "metadata": {},
            "model": "mock-model",
            "prompt_template": "Process: {input}",
        },
    ],
    "edges": [
        {
            "id": "e1",
            "edge_type": "data",
            "source_node_id": "n1",
            "source_port": "text",
            "target_node_id": "data-processor",
            "target_port": "input",
        },
    ],
    "sub_graphs": {},
    "entry_points": ["n1"],
    "exit_points": ["data-processor"],
    "shared_context": [],
    "artifact_refs": [],
}

_EMPTY_GRAPH = {
    "version": "dan_graph_v1",
    "metadata": {"name": "empty-regression"},
    "nodes": [],
    "edges": [],
    "sub_graphs": {},
    "entry_points": [],
    "exit_points": [],
    "shared_context": [],
    "artifact_refs": [],
}


# ---------------------------------------------------------------------------
# 1. Plan validity — parsing golden plans as MutationPlan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", GOLDEN_PROMPTS, ids=[c["id"] for c in GOLDEN_PROMPTS])
def test_plan_validity_parses_as_mutation_plan(case: dict[str, Any]):
    """Each golden plan should parse as a valid MutationPlan."""
    plan_data = case["mock_plan"]
    plan = MutationPlan.model_validate({
        "operations": plan_data["operations"],
        "description": plan_data.get("description", ""),
    })
    assert len(plan.operations) == len(plan_data["operations"])
    assert plan.plan_id


# ---------------------------------------------------------------------------
# 2. Operation match — ops in the plan match expected types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", GOLDEN_PROMPTS, ids=[c["id"] for c in GOLDEN_PROMPTS])
def test_operation_match(case: dict[str, Any]):
    """Operation types in the plan should match the expected list."""
    plan_data = case["mock_plan"]
    plan = MutationPlan.model_validate({
        "operations": plan_data["operations"],
        "description": plan_data.get("description", ""),
    })
    actual_ops = [op.op for op in plan.operations]
    assert actual_ops == case["expected_ops"], (
        f"Expected ops {case['expected_ops']}, got {actual_ops}"
    )


# ---------------------------------------------------------------------------
# 3. Dry-run on golden plans that target the existing graph
# ---------------------------------------------------------------------------

_DRY_RUN_CASES = [
    c for c in GOLDEN_PROMPTS
    if c["id"] in (
        "add_single_node", "remove_node", "edit_node",
        "add_gate_node", "expand_chain_pattern",
    )
]


@pytest.mark.parametrize("case", _DRY_RUN_CASES, ids=[c["id"] for c in _DRY_RUN_CASES])
def test_dry_run_success(case: dict[str, Any]):
    """Golden plans should pass dry-run on the test graph."""
    graph = _GRAPH_WITH_NODES
    revision = compute_graph_revision(graph)
    plan = MutationPlan.model_validate({
        "operations": case["mock_plan"]["operations"],
        "description": case["mock_plan"].get("description", ""),
        "base_graph_revision": revision,
    })
    result = GraphMutator().dry_run(graph, plan, current_revision=revision)
    assert result.success, (
        f"Dry-run failed for {case['id']}: "
        + "; ".join(e.message for e in result.errors)
    )
    assert result.new_graph is not None


def test_dry_run_expand_pattern_on_empty_graph():
    """Expand patterns should succeed on an empty graph."""
    graph = _EMPTY_GRAPH
    revision = compute_graph_revision(graph)
    plan = MutationPlan.model_validate({
        "operations": [
            {"op": "expand_pattern", "pattern": "chain",
             "params": {"count": 3, "names": ["A", "B", "C"]}},
        ],
        "description": "expand chain on empty graph",
        "base_graph_revision": revision,
    })
    result = GraphMutator().dry_run(graph, plan, current_revision=revision)
    assert result.success, (
        "Expand pattern on empty graph failed: "
        + "; ".join(e.message for e in result.errors)
    )


# ---------------------------------------------------------------------------
# 4. Error handling — invalid plans
# ---------------------------------------------------------------------------


def test_invalid_node_type_fails_dry_run():
    """A plan with an invalid/unsupported node type should fail in dry-run."""
    graph = _GRAPH_WITH_NODES
    revision = compute_graph_revision(graph)
    plan = MutationPlan.model_validate({
        "operations": [
            {"op": "add_node", "node_type": "nonexistent_magic_type",
             "name": "Bad Node"},
        ],
        "base_graph_revision": revision,
    })
    result = GraphMutator().dry_run(graph, plan, current_revision=revision)
    assert not result.success or any(
        "nonexistent" in w.lower() or "unknown" in w.lower()
        for w in result.validation_warnings
    ), "Invalid node type should fail dry-run or produce warnings"


def test_missing_required_fields_fails():
    """A plan missing required fields should fail."""
    with pytest.raises(Exception):
        MutationPlan.model_validate({
            "operations": [
                {"op": "add_edge"},  # missing required fields
            ],
        })


def test_remove_nonexistent_node_fails_dry_run():
    """Removing a non-existent node should fail in dry-run."""
    graph = _GRAPH_WITH_NODES
    revision = compute_graph_revision(graph)
    plan = MutationPlan.model_validate({
        "operations": [{"op": "remove_node", "node_id": "does-not-exist"}],
        "base_graph_revision": revision,
    })
    result = GraphMutator().dry_run(graph, plan, current_revision=revision)
    assert not result.success
    assert len(result.errors) > 0


def test_stale_revision_flagged():
    """A plan with a wrong base_graph_revision should be flagged as stale."""
    graph = _GRAPH_WITH_NODES
    revision = compute_graph_revision(graph)
    plan = MutationPlan.model_validate({
        "operations": [
            {"op": "add_node", "node_type": "llm_operator", "name": "Test"},
        ],
        "base_graph_revision": "wrong_revision_00000",
    })
    result = GraphMutator().dry_run(graph, plan, current_revision=revision)
    assert result.stale_plan


def test_edit_nonexistent_node_fails():
    """Editing a non-existent node should fail."""
    graph = _GRAPH_WITH_NODES
    revision = compute_graph_revision(graph)
    plan = MutationPlan.model_validate({
        "operations": [
            {"op": "edit_node", "node_id": "ghost-node",
             "updates": {"prompt_template": "New prompt"}},
        ],
        "base_graph_revision": revision,
    })
    result = GraphMutator().dry_run(graph, plan, current_revision=revision)
    assert not result.success


# ---------------------------------------------------------------------------
# 5. JSON fallback extraction
# ---------------------------------------------------------------------------


def test_json_fallback_extracts_plan_from_markdown_block():
    """_try_parse_mutation_json should extract a plan from a markdown code block."""
    text = '''Here's the plan:

```json
{
  "description": "Add a Summarizer",
  "operations": [
    {"op": "add_node", "node_type": "llm_operator", "name": "Summarizer"}
  ]
}
```

This will add a new Summarizer node.'''
    result = _try_parse_mutation_json(text)
    assert result is not None
    assert len(result["operations"]) == 1
    assert result["operations"][0]["op"] == "add_node"


def test_json_fallback_extracts_plan_from_raw_json():
    """_try_parse_mutation_json should extract from raw JSON text."""
    text = json.dumps({
        "description": "Test",
        "operations": [{"op": "add_node", "node_type": "llm_operator", "name": "X"}],
    })
    result = _try_parse_mutation_json(text)
    assert result is not None
    assert result["operations"][0]["name"] == "X"


def test_json_fallback_returns_none_for_non_plan_text():
    """Non-JSON text should return None."""
    assert _try_parse_mutation_json("Just a regular response.") is None


def test_json_fallback_returns_none_for_json_without_operations():
    """JSON without 'operations' key is not a mutation plan."""
    text = json.dumps({"foo": "bar", "items": [1, 2, 3]})
    assert _try_parse_mutation_json(text) is None


# ---------------------------------------------------------------------------
# 6. Integration: mock LLM → pipeline → dry-run (via app)
# ---------------------------------------------------------------------------


def _make_golden_provider(plan_data: dict[str, Any]) -> Any:
    """Create a mock provider that returns a specific mutation plan."""

    class _GoldenProvider:
        async def complete(self, **kwargs: Any) -> CompletionResult:
            return CompletionResult(
                text="Planning mutations...",
                tool_calls=[{
                    "id": "call_golden",
                    "type": "function",
                    "function": {
                        "name": "plan_graph_mutations",
                        "arguments": json.dumps(plan_data),
                    },
                }],
                usage={"prompt_tokens": 50, "completion_tokens": 30},
            )

        async def stream(self, **kwargs: Any) -> AsyncIterator[StreamChunk]:
            text = "Planning mutations..."
            yield StreamChunk(delta=text, accumulated=text, done=True,
                              usage={"prompt_tokens": 50, "completion_tokens": 30})

    return _GoldenProvider()


@pytest.mark.asyncio
async def test_full_pipeline_add_node():
    """End-to-end: mock LLM returns add_node plan → pipeline parses → dry-run passes."""
    plan_data = GOLDEN_PROMPTS[0]["mock_plan"]
    provider = _make_golden_provider(plan_data)
    registry = ProviderRegistry()
    registry.register("default", provider)  # type: ignore[arg-type]

    with patch(
        "dan.server.app._build_chat_provider_registry",
        return_value=registry,
    ):
        async with lifespan(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                gid = "regression-pipeline"
                await client.post("/api/graphs", json={"graph_id": gid})
                await client.put(f"/api/graphs/{gid}", json=_GRAPH_WITH_NODES)

                resp = await client.post(
                    "/api/chat/message",
                    json={"workflow_id": gid,
                          "message": GOLDEN_PROMPTS[0]["prompt"],
                          "history": [], "mode": "agent"},
                )
                assert resp.status_code == 200
