"""Shared fixtures for publish tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dan.models.graph import Graph
from dan.utils.workflow_interface import WorkflowInterface


@pytest.fixture()
def simple_workflow() -> Graph:
    """Two-node LLM pipeline with an InputNode."""
    return Graph.model_validate({
        "version": "dan_graph_v1",
        "metadata": {"name": "simple-pipe", "description": "A simple pipeline"},
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
def human_workflow() -> Graph:
    """Workflow with a HumanNode — triggers multi-call pattern."""
    return Graph.model_validate({
        "version": "dan_graph_v1",
        "metadata": {"name": "human-review", "description": "Workflow with human review"},
        "nodes": [
            {
                "id": "gen",
                "name": "generate",
                "node_type": "llm_operator",
                "model": "test",
                "prompt_template": "Draft about {topic}",
                "input_ports": [{"name": "input"}],
                "output_ports": [{"name": "result"}],
            },
            {
                "id": "h",
                "name": "review",
                "node_type": "human",
                "prompt": "Please review this draft",
                "input_ports": [{"name": "input"}],
                "output_ports": [{"name": "result"}],
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source_node_id": "gen",
                "source_port": "result",
                "target_node_id": "h",
                "target_port": "input",
                "edge_type": "data",
            },
        ],
        "entry_points": ["gen"],
        "exit_points": ["h"],
    })


@pytest.fixture()
def placeholder_workflow() -> Graph:
    """Workflow without InputNode — uses prompt placeholders."""
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
def simple_interface(simple_workflow: Graph) -> WorkflowInterface:
    from dan.utils.workflow_interface import derive_workflow_interface
    return derive_workflow_interface(simple_workflow)


@pytest.fixture()
def human_interface(human_workflow: Graph) -> WorkflowInterface:
    from dan.utils.workflow_interface import derive_workflow_interface
    return derive_workflow_interface(human_workflow)


@pytest.fixture()
def workflow_json_file(tmp_path: Path, simple_workflow: Graph) -> Path:
    """Write simple_workflow to a JSON file and return the path."""
    p = tmp_path / "test_workflow.json"
    p.write_text(simple_workflow.model_dump_json(indent=2))
    return p


@pytest.fixture()
def workflow_dir(tmp_path: Path, simple_workflow: Graph, human_workflow: Graph) -> Path:
    """Directory containing two workflow JSON files."""
    d = tmp_path / "graphs"
    d.mkdir()
    (d / "simple.json").write_text(simple_workflow.model_dump_json(indent=2))
    (d / "human.json").write_text(human_workflow.model_dump_json(indent=2))
    return d
