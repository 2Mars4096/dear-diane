from types import SimpleNamespace

from dan.models.graph import Graph, GraphMetadata
from dan.models.nodes import CodeOperator
from dan.models.ports import OutputPort
from dan.models.ports import InputPort
from dan.server.scoped_run import build_scoped_graph, map_run_event_to_chat_block
from dan.server.workflow_guards import ensure_workflow_apply_ready_and_save
from dan.worker.model import Worker


def test_build_scoped_graph_workerizes_internal_input_helper() -> None:
    target = Worker(
        id="target",
        name="Target",
        input_ports=[
            InputPort(name="topic", required=True, json_schema={"type": "string"}),
            InputPort(name="config", required=True, json_schema={"type": "object"}),
        ],
    )
    graph = Graph(
        metadata=GraphMetadata(name="Scoped Test"),
        nodes=[target],
        entry_points=["target"],
        exit_points=["target"],
    )

    result = build_scoped_graph(
        graph,
        scope="node",
        target_node_id="target",
        inputs={"topic": "markets", "config": {"limit": 3}},
    )

    assert result.error is None
    assert result.graph is not None
    assert result.graph.entry_points[0].startswith("_scoped_input_")
    scoped_input = result.graph.node_by_id(result.graph.entry_points[0])
    assert isinstance(scoped_input, Worker)
    assert scoped_input.metadata["scoped_helper"] == "input"
    assert [port.name for port in scoped_input.output_ports] == ["input", "topic", "config"]
    assert scoped_input.output_ports[2].json_schema == {"type": "object"}
    assert scoped_input.metadata["input_variables"] == [
        {"name": "topic", "type": "string", "default": "markets", "description": ""},
        {"name": "config", "type": "string", "default": {"limit": 3}, "description": ""},
    ]
    assert len(result.graph.edges) == 2
    assert {edge.source_port for edge in result.graph.edges} == {"topic", "config"}


def test_map_run_event_to_chat_block_surfaces_automatic_recovery_started() -> None:
    block = map_run_event_to_chat_block(
        {
            "event_type": "automatic_recovery_started",
            "run_id": "run-1",
            "data": {"selected_action": "rerun_from_checkpoint"},
        },
        "full",
        None,
    )

    assert block is not None
    assert block["event_type"] == "automatic_recovery_started"
    assert block["summary"] == "Auto-repair started: rerun_from_checkpoint"


def test_map_run_event_to_chat_block_surfaces_automatic_recovery_exhausted() -> None:
    block = map_run_event_to_chat_block(
        {
            "event_type": "automatic_recovery_completed",
            "run_id": "run-1",
            "data": {
                "status": "exhausted",
                "escalation_summary": "Manual review required.",
            },
        },
        "full",
        None,
    )

    assert block is not None
    assert block["event_type"] == "automatic_recovery_completed"
    assert block["summary"] == "Auto-repair exhausted. Manual review required."


def test_map_run_event_to_chat_block_prefixes_recovery_child_node_events() -> None:
    block = map_run_event_to_chat_block(
        {
            "event_type": "node_completed",
            "run_id": "run-1",
            "node_id": "task",
            "data": {"automatic_recovery": True, "recovery_run_id": "rerun-1"},
        },
        "full",
        None,
    )

    assert block is not None
    assert block["summary"] == "Auto-repair rerun: Node 'task' completed"


def test_map_run_event_to_chat_block_surfaces_run_failed_errors_map() -> None:
    block = map_run_event_to_chat_block(
        {
            "event_type": "run_failed",
            "run_id": "run-1",
            "data": {"errors": {"exception": "Missing required input 'watchlist_path'"}},
        },
        "full",
        None,
    )

    assert block is not None
    assert block["summary"] == "Run failed: Missing required input 'watchlist_path'"
    assert block["detail"]["error"] == "Missing required input 'watchlist_path'"


def test_ensure_workflow_apply_ready_and_save_returns_saved_graph_metadata() -> None:
    graph = Graph(
        metadata=GraphMetadata(name="guard-test"),
        nodes=[
            CodeOperator(
                id="emit",
                name="emit",
                code="result = 'ok'",
                output_ports=[OutputPort(name="result")],
            )
        ],
        edges=[],
        entry_points=["emit"],
        exit_points=["emit"],
    )
    saved: dict[str, object] = {}

    def save_graph(workflow_id: str, graph_dict: dict) -> dict:
        saved["workflow_id"] = workflow_id
        saved["graph_dict"] = graph_dict
        return graph_dict

    result = ensure_workflow_apply_ready_and_save(
        SimpleNamespace(save_graph=save_graph),
        graph.model_dump(mode="json"),
        workflow_id="wf-apply",
    )

    assert saved["workflow_id"] == "wf-apply"
    assert saved["graph_dict"] == result.saved_graph
    assert result.workflow_id == "wf-apply"
    assert result.graph_revision
    assert result.warnings == []
