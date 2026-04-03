from __future__ import annotations

from dan.engine import Engine, EngineConfig
from dan.linter import LintConfig
from dan.models.edges import DataEdge
from dan.models.graph import Graph, GraphMetadata
from dan.models.ports import InputPort, OutputPort
from dan.validation.linting import generate_lint_config
from dan.worker.model import LLMHints, Worker


def _base_edge() -> DataEdge:
    return DataEdge(
        id="e1",
        source_node_id="source",
        source_port="result",
        target_node_id="target",
        target_port="input",
    )


def test_generate_lint_config_covers_structural_semantic_and_intent() -> None:
    source = Worker(
        id="source",
        name="Source",
        output_ports=[OutputPort(name="result")],
    )
    target = Worker(
        id="target",
        name="Target",
        role="reviewer",
        instruction="Check evidence and completeness.",
        description="Reviews a summary for quality and accuracy.",
        input_ports=[
            InputPort(
                name="input",
                required=True,
                json_schema={
                    "type": "object",
                    "required": ["summary"],
                    "properties": {
                        "summary": {"type": "string", "maxLength": 500},
                        "score": {"type": "number", "minimum": 0, "maximum": 1},
                        "ticket_id": {"type": "string", "pattern": r"[A-Z]{3}-\d{4}"},
                    },
                },
                description="Concise summary draft",
            )
        ],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[],
        entry_points=["source"],
        exit_points=["target"],
    )

    config = generate_lint_config(source, target, _base_edge(), graph)

    assert config is not None
    assert config.structural is not None
    assert config.structural.required_keys == ["summary"]
    assert config.structural.string_max_lengths == {"summary": 500}
    assert config.structural.ranges == {"score": {"minimum": 0.0, "maximum": 1.0}}
    assert config.structural.format_patterns == {"ticket_id": r"[A-Z]{3}-\d{4}"}
    assert config.semantic is not None
    assert "reviews" in config.semantic.topic_keywords
    assert config.semantic.min_similarity == 0.7
    assert config.intent is not None
    assert "reviewer" in config.intent.intent
    assert "evidence" in config.intent.intent.lower()


def test_generate_lint_config_round_trips_through_json() -> None:
    source = Worker(id="source", name="Source", output_ports=[OutputPort(name="result")])
    target = Worker(
        id="target",
        name="Target",
        role="writer",
        description="Produces a concise finance summary.",
        input_ports=[
            InputPort(
                name="input",
                required=True,
                json_schema={
                    "type": "object",
                    "required": ["summary"],
                    "properties": {
                        "summary": {"type": "string", "maxLength": 200},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
                description="Finance summary input",
            )
        ],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[],
        entry_points=["source"],
        exit_points=["target"],
    )

    config = generate_lint_config(source, target, _base_edge(), graph)

    assert config is not None
    dumped = config.model_dump(mode="json")
    rebuilt = LintConfig.model_validate(dumped)
    assert rebuilt == config


def test_generate_lint_config_omits_weak_metadata() -> None:
    source = Worker(id="source", name="Source", output_ports=[OutputPort(name="result")])
    target = Worker(
        id="target",
        name="Target",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[],
        entry_points=["source"],
        exit_points=["target"],
    )

    config = generate_lint_config(source, target, _base_edge(), graph)

    assert config is None


def test_generate_lint_config_uses_warning_severity_for_heuristic_only_autogen() -> None:
    source = Worker(id="source", name="Source", output_ports=[OutputPort(name="result")])
    target = Worker(
        id="target",
        name="Target",
        role="reviewer",
        instruction="Check the draft for clarity and factual alignment.",
        description="Reviews a draft for clarity and factual alignment.",
        input_ports=[
            InputPort(
                name="input",
                required=False,
                description="Draft content for review",
            )
        ],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[],
        entry_points=["source"],
        exit_points=["target"],
    )

    config = generate_lint_config(source, target, _base_edge(), graph)

    assert config is not None
    assert config.structural is None
    assert config.semantic is not None
    assert config.intent is not None
    assert config.severity.value == "warning"
    assert config.autofix == []


def test_generate_lint_config_uses_stricter_threshold_for_critical_workers() -> None:
    source = Worker(id="source", name="Source", output_ports=[OutputPort(name="result")])
    target = Worker(
        id="target",
        name="Target",
        role="reviewer",
        instruction="Review this output carefully for executive delivery.",
        llm_hints=LLMHints(task_tier="critical"),
        input_ports=[
            InputPort(
                name="input",
                required=False,
                description="Executive-ready summary input",
            )
        ],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[],
        entry_points=["source"],
        exit_points=["target"],
    )

    config = generate_lint_config(source, target, _base_edge(), graph)

    assert config is not None
    assert config.semantic is not None
    assert config.semantic.min_similarity == 0.85


def test_generate_lint_config_can_optionally_refine_intent() -> None:
    calls: dict[str, object] = {}

    def refiner(base_intent: str, context: dict[str, object]) -> str:
        calls["base_intent"] = base_intent
        calls["context"] = context
        return "Evidence completeness reviewer handoff"

    source = Worker(
        id="source",
        name="Source",
        description="Produces raw evidence packets.",
        output_ports=[OutputPort(name="result")],
    )
    target = Worker(
        id="target",
        name="Target",
        role="reviewer",
        instruction="Check evidence and completeness.",
        description="Reviews a summary for quality and accuracy.",
        input_ports=[
            InputPort(
                name="input",
                required=True,
                description="Concise summary draft",
            )
        ],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        metadata=GraphMetadata(name="quality_flow", description="Quality review flow"),
        nodes=[source, target],
        edges=[],
        entry_points=["source"],
        exit_points=["target"],
    )

    config = generate_lint_config(source, target, _base_edge(), graph, intent_refiner=refiner)

    assert config is not None
    assert config.intent is not None
    assert config.intent.intent == "Evidence completeness reviewer handoff"
    assert config.intent.required_keywords == ["evidence", "completeness", "reviewer", "handoff"]
    assert calls["base_intent"] == (
        "reviewer Check evidence and completeness. Concise summary draft "
        "Reviews a summary for quality and accuracy. Produces raw evidence packets."
    )
    assert calls["context"] == {
        "graph_name": "quality_flow",
        "graph_description": "Quality review flow",
        "source_node_id": "source",
        "source_node_type": "worker",
        "source_description": "Produces raw evidence packets.",
        "target_node_id": "target",
        "target_node_type": "worker",
        "target_role": "reviewer",
        "target_description": "Reviews a summary for quality and accuracy.",
        "target_port": "input",
        "target_port_description": "Concise summary draft",
        "instruction": "Check evidence and completeness.",
    }


def test_scheduler_prefers_manual_edge_lint_override() -> None:
    source = Worker(id="source", name="Source", output_ports=[OutputPort(name="result")])
    target = Worker(
        id="target",
        name="Target",
        role="reviewer",
        description="Reviews the summary.",
        input_ports=[
            InputPort(
                name="input",
                required=True,
                json_schema={"type": "object", "required": ["summary"]},
                description="Review input",
            )
        ],
        output_ports=[OutputPort(name="result")],
    )
    edge = DataEdge(
        id="e1",
        source_node_id="source",
        source_port="result",
        target_node_id="target",
        target_port="input",
        lint={
            "structural": {"required_keys": ["title"]},
            "severity": "warning",
        },
        metadata={
            "lint": {
                "structural": {"required_keys": ["summary"]},
                "severity": "error",
            }
        },
    )
    graph = Graph(
        nodes=[source, target],
        edges=[edge],
        entry_points=["source"],
        exit_points=["target"],
    )

    engine = Engine(config=EngineConfig(checkpoint_enabled=False))
    resolved = engine._resolve_edge_lint_config(edge, source, target, graph)

    assert resolved is not None
    assert resolved.severity.value == "warning"
    assert resolved.structural is not None
    assert resolved.structural.required_keys == ["title"]


def test_data_edge_lifts_legacy_metadata_lint_to_typed_field() -> None:
    edge = DataEdge(
        id="e1",
        source_node_id="source",
        source_port="result",
        target_node_id="target",
        target_port="input",
        metadata={
            "lint": {
                "structural": {"required_keys": ["summary"]},
                "severity": "warning",
            }
        },
    )

    assert edge.lint is not None
    assert edge.lint.severity.value == "warning"
    assert edge.lint.structural is not None
    assert edge.lint.structural.required_keys == ["summary"]
    assert edge.metadata["lint"]["severity"] == "warning"


def test_data_edge_assignment_syncs_typed_lint_back_to_metadata() -> None:
    edge = DataEdge(
        id="e1",
        source_node_id="source",
        source_port="result",
        target_node_id="target",
        target_port="input",
    )

    edge.lint = LintConfig.model_validate(
        {
            "structural": {"required_keys": ["summary"]},
            "severity": "error",
        }
    )
    assert edge.metadata["lint"]["structural"]["required_keys"] == ["summary"]

    edge.lint = None
    assert "lint" not in edge.metadata
