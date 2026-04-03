from __future__ import annotations

from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.ports import InputPort, OutputPort
from dan.validation.linting import generate_lint_config
from dan.worker.model import Worker


def test_generate_lint_config_resolves_worker_instruction_refs() -> None:
    source = Worker(
        id="source",
        name="Source",
        output_ports=[OutputPort(name="result", json_schema={"type": "object"})],
    )
    target = Worker(
        id="target",
        name="Target",
        role="reviewer",
        context={
            "instruction_profile_ref": "review_profile",
            "context_bundle_refs": ["bundle_a"],
        },
        input_ports=[
            InputPort(
                name="input",
                required=True,
                json_schema={"type": "object", "required": ["summary"]},
                description="Concise summary draft",
            )
        ],
        output_ports=[OutputPort(name="result")],
        description="Reviews a summary for quality and accuracy.",
    )
    graph = Graph(
        nodes=[source, target],
        edges=[],
        entry_points=["source"],
        exit_points=["target"],
        worker_resources={
            "instruction_profiles": {
                "review_profile": {"instruction": "Check evidence and completeness."}
            },
            "context_bundles": {
                "bundle_a": {"instruction": "Stay concise and concrete."}
            },
        },
    )
    edge = DataEdge(
        id="e1",
        source_node_id="source",
        source_port="result",
        target_node_id="target",
        target_port="input",
    )

    config = generate_lint_config(source, target, edge, graph)

    assert config is not None
    assert config.structural is not None
    assert config.structural.required_keys == ["summary"]
    assert config.intent is not None
    assert "Check evidence and completeness." in config.intent.intent
    assert "Stay concise and concrete." in config.intent.intent
