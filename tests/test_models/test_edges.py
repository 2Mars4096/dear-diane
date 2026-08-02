from dan.models.edges import DataEdge, ControlEdge, ContextEdge
from dan.models.context import ContextMode


class TestDataEdge:
    def test_construction(self):
        e = DataEdge(
            id="e1",
            source_node_id="a",
            source_port="out",
            target_node_id="b",
            target_port="in",
        )
        assert e.edge_type == "data"

    def test_json_round_trip(self):
        e = DataEdge(id="e1", source_node_id="a", source_port="o", target_node_id="b", target_port="i")
        assert DataEdge.model_validate(e.model_dump()) == e


class TestControlEdge:
    def test_with_condition(self):
        e = ControlEdge(
            id="e2",
            source_node_id="if1",
            source_port="true",
            target_node_id="writer",
            target_port="trigger",
            condition="score > 0.8",
        )
        assert e.edge_type == "control"
        assert e.condition == "score > 0.8"

    def test_without_condition(self):
        e = ControlEdge(id="e3", source_node_id="a", source_port="o", target_node_id="b", target_port="i")
        assert e.condition is None


class TestContextEdge:
    def test_construction(self):
        e = ContextEdge(
            id="e4",
            source_node_id="writer",
            source_port="draft",
            target_node_id="reviewer",
            target_port="input",
            context_key="context.draft",
            mode=ContextMode.READ,
        )
        assert e.edge_type == "context"
        assert e.mode == ContextMode.READ

    def test_json_round_trip(self):
        e = ContextEdge(
            id="e5",
            source_node_id="a",
            source_port="o",
            target_node_id="b",
            target_port="i",
            context_key="k",
            mode=ContextMode.APPEND,
        )
        restored = ContextEdge.model_validate(e.model_dump())
        assert restored == e
        assert restored.mode == ContextMode.APPEND
