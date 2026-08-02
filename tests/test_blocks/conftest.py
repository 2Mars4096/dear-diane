"""Shared fixtures for block tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dan.blocks.models import MANIFEST_FILENAME, DanBlock
from dan.models.graph import Graph


@pytest.fixture()
def simple_workflow() -> Graph:
    """Two-node LLM pipeline with an InputNode, explicit entry/exit."""
    return Graph.model_validate({
        "version": "dan_graph_v1",
        "metadata": {"name": "simple-pipe", "description": "A→B pipeline"},
        "nodes": [
            {
                "id": "inp",
                "name": "inputs",
                "node_type": "input",
                "variables": [
                    {"name": "topic", "type": "string", "description": "The topic"},
                ],
                "output_ports": [{"name": "result"}],
            },
            {
                "id": "a",
                "name": "gen",
                "node_type": "llm_operator",
                "model": "test",
                "prompt_template": "Write about {topic}",
                "input_ports": [{"name": "input"}],
                "output_ports": [{"name": "result"}],
            },
            {
                "id": "b",
                "name": "refine",
                "node_type": "llm_operator",
                "model": "test",
                "prompt_template": "Refine: {text}",
                "input_ports": [{"name": "input"}],
                "output_ports": [{"name": "result"}],
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source_node_id": "inp",
                "source_port": "result",
                "target_node_id": "a",
                "target_port": "input",
                "edge_type": "data",
            },
            {
                "id": "e2",
                "source_node_id": "a",
                "source_port": "result",
                "target_node_id": "b",
                "target_port": "input",
                "edge_type": "data",
            },
        ],
        "entry_points": ["inp"],
        "exit_points": ["b"],
    })


@pytest.fixture()
def composite_workflow() -> Graph:
    """Workflow containing a composite node with a sub-graph."""
    return Graph.model_validate({
        "version": "dan_graph_v1",
        "metadata": {"name": "with-composite", "description": "Has composite"},
        "nodes": [
            {
                "id": "comp",
                "name": "my_composite",
                "node_type": "composite",
                "body_graph": "comp_body",
                "input_mappings": {"input": "inner_a::input"},
                "output_mappings": {"inner_b::result": "result"},
                "input_ports": [{"name": "input"}],
                "output_ports": [{"name": "result"}],
            },
        ],
        "edges": [],
        "entry_points": ["comp"],
        "exit_points": ["comp"],
        "sub_graphs": {
            "comp_body": {
                "version": "dan_graph_v1",
                "metadata": {"name": "comp-inner", "description": "Inner graph"},
                "nodes": [
                    {
                        "id": "inner_a",
                        "name": "a",
                        "node_type": "llm_operator",
                        "model": "test",
                        "prompt_template": "Process: {data}",
                        "input_ports": [{"name": "input"}],
                        "output_ports": [{"name": "result"}],
                    },
                    {
                        "id": "inner_b",
                        "name": "b",
                        "node_type": "llm_operator",
                        "model": "test",
                        "prompt_template": "Finish: {text}",
                        "input_ports": [{"name": "input"}],
                        "output_ports": [{"name": "result"}],
                    },
                ],
                "edges": [
                    {
                        "id": "ie1",
                        "source_node_id": "inner_a",
                        "source_port": "result",
                        "target_node_id": "inner_b",
                        "target_port": "input",
                        "edge_type": "data",
                    },
                ],
                "entry_points": ["inner_a"],
                "exit_points": ["inner_b"],
            },
        },
    })


@pytest.fixture()
def human_workflow() -> Graph:
    """Workflow with a HumanNode."""
    return Graph.model_validate({
        "version": "dan_graph_v1",
        "metadata": {"name": "human-wf", "description": "Has human"},
        "nodes": [
            {
                "id": "h",
                "name": "review",
                "node_type": "human",
                "prompt": "Please review",
                "output_ports": [{"name": "result"}],
            },
        ],
        "edges": [],
        "entry_points": ["h"],
        "exit_points": ["h"],
    })


@pytest.fixture()
def placeholder_workflow() -> Graph:
    """Workflow without InputNode — uses prompt placeholders for inputs."""
    return Graph.model_validate({
        "version": "dan_graph_v1",
        "metadata": {"name": "placeholder-wf", "description": "Uses placeholders"},
        "nodes": [
            {
                "id": "a",
                "name": "gen",
                "node_type": "llm_operator",
                "model": "test",
                "prompt_template": "Write about {topic} in {style}",
                "input_ports": [{"name": "input"}],
                "output_ports": [{"name": "result"}],
            },
        ],
        "edges": [],
        "entry_points": ["a"],
        "exit_points": ["a"],
    })


@pytest.fixture()
def installed_block_dir(tmp_path: Path) -> Path:
    """Create a minimal installed block directory."""
    block_dir = tmp_path / "test-block" / "0.1.0"
    block_dir.mkdir(parents=True)

    manifest = DanBlock(
        name="test-block",
        version="0.1.0",
        description="A test block",
        block_type="workflow",
    )
    (block_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest.model_dump(), indent=2), encoding="utf-8"
    )

    graph = Graph.model_validate({
        "version": "dan_graph_v1",
        "metadata": {"name": "test-block"},
        "nodes": [
            {
                "id": "n1",
                "name": "node",
                "node_type": "llm_operator",
                "model": "test",
                "prompt_template": "Hello {name}",
                "output_ports": [{"name": "result"}],
            },
        ],
        "edges": [],
        "entry_points": ["n1"],
        "exit_points": ["n1"],
    })
    (block_dir / "graph.json").write_text(
        graph.model_dump_json(indent=2), encoding="utf-8"
    )

    return tmp_path
