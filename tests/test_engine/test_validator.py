"""Comprehensive tests for the Validator executor, boundaries, and round-trip."""

from __future__ import annotations

import pytest

from dan.engine.executor import EngineConfig, ExecutionContext, NodeResult
from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.events import EngineEvent
from dan.engine.state import ExecutionState, NodeStatus
from dan.executors.validator import (
    ValidationViolation,
    ValidatorExecutor,
    resolve_dotpath,
    _eval_required_keys,
    _eval_non_empty,
    _eval_schema_conformance,
    _eval_type_check,
    _eval_custom_expression,
)
from dan.models.control_flow import ValidationRule, ValidatorNode
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.ports import InputPort, OutputPort


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    event_log: list[EngineEvent] | None = None,
) -> ExecutionContext:
    """Build a minimal ExecutionContext with optional event capture."""
    graph = Graph(nodes=[], edges=[], entry_points=[], exit_points=[])
    state = ExecutionState(graph)

    captured = event_log if event_log is not None else []

    async def capture_event(event: EngineEvent) -> None:
        captured.append(event)

    return ExecutionContext(
        state=state,
        config=EngineConfig(checkpoint_enabled=False),
        shared_context=SharedContextStore([]),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        event_callback=capture_event,
        run_id="test-run",
    )


def _make_validator(
    rules: list[dict] | None = None,
    on_failure: str = "route",
    strict_mode: bool = False,
    node_id: str = "v1",
) -> ValidatorNode:
    parsed_rules = [ValidationRule(**r) for r in (rules or [])]
    return ValidatorNode(
        id=node_id,
        name="test_validator",
        validation_rules=parsed_rules,
        on_failure=on_failure,
        strict_mode=strict_mode,
    )


# ===========================================================================
# resolve_dotpath
# ===========================================================================


class TestResolveDotpath:
    def test_nested_dict(self):
        data = {"a": {"b": {"c": 42}}}
        assert resolve_dotpath(data, "a.b.c") == 42

    def test_list_indexing(self):
        data = {"items": [{"name": "alpha"}, {"name": "beta"}]}
        assert resolve_dotpath(data, "items.0.name") == "alpha"
        assert resolve_dotpath(data, "items.1.name") == "beta"

    def test_missing_key_raises(self):
        data = {"a": {"b": 1}}
        with pytest.raises(KeyError, match="not found"):
            resolve_dotpath(data, "a.x")

    def test_empty_path_returns_whole_dict(self):
        data = {"x": 1}
        assert resolve_dotpath(data, "") == data

    def test_index_out_of_range(self):
        data = {"items": [1, 2]}
        with pytest.raises(KeyError, match="out of range"):
            resolve_dotpath(data, "items.5")

    def test_traverse_into_non_container(self):
        data = {"a": 42}
        with pytest.raises(KeyError, match="Cannot traverse"):
            resolve_dotpath(data, "a.b")

    def test_top_level_key(self):
        data = {"name": "hello"}
        assert resolve_dotpath(data, "name") == "hello"


# ===========================================================================
# Rule evaluation — unit tests
# ===========================================================================


class TestRequiredKeys:
    def test_present(self):
        rule = ValidationRule(rule_type="required_keys", config={"keys": ["a", "b"]})
        violations = _eval_required_keys({"a": 1, "b": 2}, rule)
        assert violations == []

    def test_missing(self):
        rule = ValidationRule(rule_type="required_keys", config={"keys": ["a", "b"]})
        violations = _eval_required_keys({"a": 1}, rule)
        assert len(violations) == 1
        assert violations[0].dotpath == "b"

    def test_nested_missing(self):
        rule = ValidationRule(rule_type="required_keys", config={"keys": ["a.b.c"]})
        violations = _eval_required_keys({"a": {"b": {}}}, rule)
        assert len(violations) == 1


class TestNonEmpty:
    def test_valid(self):
        rule = ValidationRule(rule_type="non_empty", config={"keys": ["name"]})
        violations = _eval_non_empty({"name": "Alice"}, rule)
        assert violations == []

    def test_none_value(self):
        rule = ValidationRule(rule_type="non_empty", config={"keys": ["name"]})
        violations = _eval_non_empty({"name": None}, rule)
        assert len(violations) == 1

    def test_empty_string(self):
        rule = ValidationRule(rule_type="non_empty", config={"keys": ["name"]})
        violations = _eval_non_empty({"name": ""}, rule)
        assert len(violations) == 1

    def test_empty_list(self):
        rule = ValidationRule(rule_type="non_empty", config={"keys": ["items"]})
        violations = _eval_non_empty({"items": []}, rule)
        assert len(violations) == 1

    def test_empty_dict(self):
        rule = ValidationRule(rule_type="non_empty", config={"keys": ["meta"]})
        violations = _eval_non_empty({"meta": {}}, rule)
        assert len(violations) == 1

    def test_missing_key(self):
        rule = ValidationRule(rule_type="non_empty", config={"keys": ["missing"]})
        violations = _eval_non_empty({}, rule)
        assert len(violations) == 1


class TestSchemaConformance:
    def test_matching(self):
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }
        rule = ValidationRule(rule_type="schema_conformance", config={"schema": schema})
        violations = _eval_schema_conformance({"name": "Alice"}, rule)
        assert violations == []

    def test_mismatching(self):
        schema = {
            "type": "object",
            "required": ["name", "age"],
        }
        rule = ValidationRule(rule_type="schema_conformance", config={"schema": schema})
        violations = _eval_schema_conformance({"name": "Alice"}, rule)
        assert len(violations) == 1

    def test_no_schema_provided(self):
        rule = ValidationRule(rule_type="schema_conformance", config={})
        violations = _eval_schema_conformance({"a": 1}, rule)
        assert len(violations) == 1
        assert "No schema" in violations[0].message


class TestTypeCheck:
    def test_correct_type(self):
        rule = ValidationRule(
            rule_type="type_check",
            config={"checks": {"name": "str", "age": "int"}},
        )
        violations = _eval_type_check({"name": "Alice", "age": 30}, rule)
        assert violations == []

    def test_wrong_type(self):
        rule = ValidationRule(
            rule_type="type_check",
            config={"checks": {"age": "int"}},
        )
        violations = _eval_type_check({"age": "not_an_int"}, rule)
        assert len(violations) == 1
        assert violations[0].expected == "int"
        assert violations[0].actual == "str"

    def test_missing_key(self):
        rule = ValidationRule(
            rule_type="type_check",
            config={"checks": {"missing": "str"}},
        )
        violations = _eval_type_check({}, rule)
        assert len(violations) == 1

    def test_unknown_type_name(self):
        rule = ValidationRule(
            rule_type="type_check",
            config={"checks": {"x": "imaginary_type"}},
        )
        violations = _eval_type_check({"x": 1}, rule)
        assert len(violations) == 1
        assert "Unknown type" in violations[0].message


class TestCustomExpression:
    def test_truthy(self):
        rule = ValidationRule(
            rule_type="custom_expression",
            config={"expression": "x > 0"},
        )
        violations = _eval_custom_expression({"x": 5}, rule)
        assert violations == []

    def test_falsy(self):
        rule = ValidationRule(
            rule_type="custom_expression",
            config={"expression": "x > 10"},
        )
        violations = _eval_custom_expression({"x": 5}, rule)
        assert len(violations) == 1
        assert "falsy" in violations[0].message

    def test_error(self):
        rule = ValidationRule(
            rule_type="custom_expression",
            config={"expression": "undefined_var > 0"},
        )
        violations = _eval_custom_expression({}, rule)
        assert len(violations) == 1
        assert "error" in violations[0].message.lower()

    def test_no_expression(self):
        rule = ValidationRule(
            rule_type="custom_expression",
            config={},
        )
        violations = _eval_custom_expression({}, rule)
        assert len(violations) == 1


# ===========================================================================
# ValidatorExecutor — integration tests
# ===========================================================================


class TestValidatorExecutorRouting:
    @pytest.mark.asyncio
    async def test_all_pass_routes_to_valid(self):
        node = _make_validator(rules=[
            {"rule_type": "required_keys", "config": {"keys": ["name"]}},
            {"rule_type": "type_check", "config": {"checks": {"name": "str"}}},
        ])
        ctx = _make_context()
        executor = ValidatorExecutor()
        result = await executor.execute(node, {"name": "Alice"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "valid" in result.outputs
        assert result.outputs["valid"]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_all_pass_exposes_passthrough_ports(self):
        node = _make_validator(rules=[
            {"rule_type": "required_keys", "config": {"keys": ["topic", "context"]}},
        ])
        ctx = _make_context()
        executor = ValidatorExecutor()
        result = await executor.execute(
            node, {"topic": "AI", "context": "ml systems"}, ctx,
        )

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["valid"]["topic"] == "AI"
        assert result.outputs["topic"] == "AI"
        assert result.outputs["context"] == "ml systems"

    @pytest.mark.asyncio
    async def test_fail_routes_to_invalid(self):
        node = _make_validator(rules=[
            {"rule_type": "required_keys", "config": {"keys": ["name", "age"]}},
        ])
        ctx = _make_context()
        executor = ValidatorExecutor()
        result = await executor.execute(node, {"name": "Alice"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "invalid" in result.outputs
        assert result.outputs["invalid"]["data"]["name"] == "Alice"
        assert len(result.outputs["invalid"]["errors"]) == 1

    @pytest.mark.asyncio
    async def test_strict_mode_early_stop(self):
        node = _make_validator(
            rules=[
                {"rule_type": "required_keys", "config": {"keys": ["missing1"]}},
                {"rule_type": "required_keys", "config": {"keys": ["missing2"]}},
            ],
            strict_mode=True,
        )
        ctx = _make_context()
        executor = ValidatorExecutor()
        result = await executor.execute(node, {}, ctx)

        assert "invalid" in result.outputs
        errors = result.outputs["invalid"]["errors"]
        assert len(errors) == 1
        assert errors[0]["dotpath"] == "missing1"

    @pytest.mark.asyncio
    async def test_multi_rule_composition(self):
        node = _make_validator(rules=[
            {"rule_type": "required_keys", "config": {"keys": ["a", "b"]}},
            {"rule_type": "type_check", "config": {"checks": {"a": "int"}}},
            {"rule_type": "custom_expression", "config": {"expression": "a > 0"}},
        ])
        ctx = _make_context()
        executor = ValidatorExecutor()
        result = await executor.execute(node, {"a": 5, "b": "hello"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "valid" in result.outputs


# ===========================================================================
# on_failure modes
# ===========================================================================


class TestOnFailureModes:
    @pytest.mark.asyncio
    async def test_route_valid(self):
        node = _make_validator(
            rules=[{"rule_type": "required_keys", "config": {"keys": ["x"]}}],
            on_failure="route",
        )
        ctx = _make_context()
        result = await ValidatorExecutor().execute(node, {"x": 1}, ctx)
        assert "valid" in result.outputs

    @pytest.mark.asyncio
    async def test_route_invalid(self):
        node = _make_validator(
            rules=[{"rule_type": "required_keys", "config": {"keys": ["x"]}}],
            on_failure="route",
        )
        ctx = _make_context()
        result = await ValidatorExecutor().execute(node, {}, ctx)
        assert "invalid" in result.outputs
        assert result.status == NodeStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_warn_always_valid(self):
        node = _make_validator(
            rules=[{"rule_type": "required_keys", "config": {"keys": ["missing"]}}],
            on_failure="warn",
        )
        ctx = _make_context()
        result = await ValidatorExecutor().execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "valid" in result.outputs
        assert result.metadata["passed"] is False
        assert len(result.metadata["violations"]) == 1

    @pytest.mark.asyncio
    async def test_halt_fails_on_violation(self):
        node = _make_validator(
            rules=[{"rule_type": "required_keys", "config": {"keys": ["missing"]}}],
            on_failure="halt",
        )
        ctx = _make_context()
        result = await ValidatorExecutor().execute(node, {}, ctx)

        assert result.status == NodeStatus.FAILED
        assert result.metadata.get("halt") is True
        assert "violation" in result.error.lower()

    @pytest.mark.asyncio
    async def test_halt_passes_when_valid(self):
        node = _make_validator(
            rules=[{"rule_type": "required_keys", "config": {"keys": ["x"]}}],
            on_failure="halt",
        )
        ctx = _make_context()
        result = await ValidatorExecutor().execute(node, {"x": 1}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "valid" in result.outputs


# ===========================================================================
# Event emission
# ===========================================================================


class TestEventEmission:
    @pytest.mark.asyncio
    async def test_validation_result_event_pass(self):
        events: list[EngineEvent] = []
        node = _make_validator(
            rules=[{"rule_type": "required_keys", "config": {"keys": ["x"]}}],
        )
        ctx = _make_context(event_log=events)
        await ValidatorExecutor().execute(node, {"x": 1}, ctx)

        assert len(events) == 1
        evt = events[0]
        assert evt.event_type.value == "validation_result"
        assert evt.data["passed"] is True
        assert evt.data["violation_count"] == 0
        assert evt.data["rule_count"] == 1

    @pytest.mark.asyncio
    async def test_validation_result_event_fail(self):
        events: list[EngineEvent] = []
        node = _make_validator(rules=[
            {"rule_type": "required_keys", "config": {"keys": ["a", "b"]}},
        ])
        ctx = _make_context(event_log=events)
        await ValidatorExecutor().execute(node, {}, ctx)

        assert len(events) == 1
        evt = events[0]
        assert evt.data["passed"] is False
        assert evt.data["violation_count"] == 2
        assert len(evt.data["violations"]) == 2


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_rules_trivially_passes(self):
        node = _make_validator(rules=[])
        ctx = _make_context()
        result = await ValidatorExecutor().execute(node, {"any": "data"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "valid" in result.outputs

    @pytest.mark.asyncio
    async def test_non_dict_data_wrapped(self):
        """When inputs is a single-key dict with 'data' pointing to a non-dict."""
        node = _make_validator(rules=[
            {"rule_type": "required_keys", "config": {"keys": ["value"]}},
        ])
        ctx = _make_context()
        result = await ValidatorExecutor().execute(node, {"data": "string_value"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "valid" in result.outputs
        assert result.outputs["valid"] == {"value": "string_value"}

    @pytest.mark.asyncio
    async def test_data_key_unwrap(self):
        """When inputs has a single 'data' key with a dict, use that dict."""
        node = _make_validator(rules=[
            {"rule_type": "required_keys", "config": {"keys": ["name"]}},
        ])
        ctx = _make_context()
        result = await ValidatorExecutor().execute(
            node, {"data": {"name": "Alice"}}, ctx,
        )

        assert result.status == NodeStatus.COMPLETED
        assert "valid" in result.outputs
        assert result.outputs["valid"]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_validator_no_incoming_edges(self):
        """Validator with empty inputs should still work (empty dict passed)."""
        node = _make_validator(rules=[])
        ctx = _make_context()
        result = await ValidatorExecutor().execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "valid" in result.outputs


# ===========================================================================
# Boundary auto-insert
# ===========================================================================


class TestBoundaryValidators:
    def test_generate_entry_validator(self):
        from dan.models.control_flow import CompositeNode
        from dan.validation.boundaries import generate_entry_validator

        composite = CompositeNode(
            id="comp1",
            name="My Composite",
            body_graph="comp1_body",
            external_input_schema={
                "type": "object",
                "required": ["topic"],
                "properties": {"topic": {"type": "string"}},
            },
        )

        validator, edges = generate_entry_validator(composite)

        assert validator.id == "comp1__entry_validator"
        assert validator.node_type == "worker"
        assert validator.role == "validator"
        assert len(validator.validation_rules) == 2
        assert validator.validation_rules[0].rule_type == "required_keys"
        assert validator.validation_rules[1].rule_type == "schema_conformance"

        assert len(edges) == 1
        assert edges[0].target_node_id == "comp1"

    def test_generate_exit_validator(self):
        from dan.models.control_flow import CompositeNode
        from dan.validation.boundaries import generate_exit_validator

        composite = CompositeNode(
            id="comp1",
            name="My Composite",
            body_graph="comp1_body",
            external_output_schema={
                "type": "object",
                "required": ["result"],
            },
        )

        validator, edges = generate_exit_validator(composite)

        assert validator.id == "comp1__exit_validator"
        assert validator.node_type == "worker"
        assert validator.role == "validator"
        assert len(validator.validation_rules) == 2
        assert len(edges) == 1
        assert edges[0].source_node_id == "comp1"

    def test_generate_entry_validator_no_schema(self):
        from dan.models.control_flow import CompositeNode
        from dan.validation.boundaries import generate_entry_validator

        composite = CompositeNode(
            id="comp1",
            name="No Schema",
            body_graph="comp1_body",
        )

        validator, edges = generate_entry_validator(composite)
        assert len(validator.validation_rules) == 0

    def test_insert_boundary_validators(self):
        from dan.models.control_flow import CompositeNode
        from dan.models.nodes import LLMOperator
        from dan.validation.boundaries import insert_boundary_validators

        comp = CompositeNode(
            id="comp1",
            name="Comp",
            body_graph="comp1_body",
            external_input_schema={
                "type": "object",
                "required": ["topic"],
            },
            external_output_schema={
                "type": "object",
                "required": ["result"],
            },
            input_ports=[InputPort(name="input")],
            output_ports=[OutputPort(name="result")],
        )
        upstream = LLMOperator(
            id="llm1", name="LLM", model="test",
            prompt_template="Generate",
            output_ports=[OutputPort(name="text")],
        )
        downstream = LLMOperator(
            id="llm2", name="LLM2", model="test",
            prompt_template="Refine",
            input_ports=[InputPort(name="input")],
        )

        graph = Graph(
            nodes=[upstream, comp, downstream],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="llm1", source_port="text",
                    target_node_id="comp1", target_port="input",
                ),
                DataEdge(
                    id="e2",
                    source_node_id="comp1", source_port="result",
                    target_node_id="llm2", target_port="input",
                ),
            ],
            entry_points=["llm1"],
            exit_points=["llm2"],
        )

        result = insert_boundary_validators(graph, "comp1")

        node_ids = {n.id for n in result.nodes}
        assert "comp1__entry_validator" in node_ids
        assert "comp1__exit_validator" in node_ids
        assert len(result.nodes) == 5

        edge_pairs = [
            (e.source_node_id, e.target_node_id)
            for e in result.edges
            if isinstance(e, DataEdge)
        ]
        assert ("llm1", "comp1__entry_validator") in edge_pairs
        assert ("comp1__entry_validator", "comp1") in edge_pairs
        assert ("comp1", "comp1__exit_validator") in edge_pairs
        assert ("comp1__exit_validator", "llm2") in edge_pairs

    def test_insert_boundary_missing_node(self):
        from dan.validation.boundaries import insert_boundary_validators

        graph = Graph(nodes=[], edges=[], entry_points=[], exit_points=[])
        with pytest.raises(ValueError, match="not found"):
            insert_boundary_validators(graph, "nonexistent")

    def test_insert_boundary_validators_idempotent(self):
        """Repeated calls return the same graph; no duplicate validators."""
        from dan.models.control_flow import CompositeNode
        from dan.models.nodes import LLMOperator
        from dan.validation.boundaries import insert_boundary_validators

        comp = CompositeNode(
            id="comp1",
            name="Comp",
            body_graph="comp1_body",
            external_input_schema={"type": "object", "required": ["topic"]},
            external_output_schema={"type": "object", "required": ["result"]},
            input_ports=[InputPort(name="input")],
            output_ports=[OutputPort(name="result")],
        )
        upstream = LLMOperator(
            id="llm1", name="LLM", model="test",
            prompt_template="Generate",
            output_ports=[OutputPort(name="text")],
        )
        downstream = LLMOperator(
            id="llm2", name="LLM2", model="test",
            prompt_template="Refine",
            input_ports=[InputPort(name="input")],
        )
        graph = Graph(
            nodes=[upstream, comp, downstream],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="llm1", source_port="text",
                    target_node_id="comp1", target_port="input",
                ),
                DataEdge(
                    id="e2",
                    source_node_id="comp1", source_port="result",
                    target_node_id="llm2", target_port="input",
                ),
            ],
            entry_points=["llm1"],
            exit_points=["llm2"],
        )

        result1 = insert_boundary_validators(graph, "comp1")
        result2 = insert_boundary_validators(result1, "comp1")

        node_ids_1 = {n.id for n in result1.nodes}
        node_ids_2 = {n.id for n in result2.nodes}
        assert node_ids_1 == node_ids_2
        assert "comp1__entry_validator" in node_ids_2
        assert "comp1__exit_validator" in node_ids_2
        assert len(result2.nodes) == 5

    def test_insert_boundary_validators_preserves_custom_ports(self):
        from dan.models.control_flow import CompositeNode
        from dan.models.nodes import LLMOperator
        from dan.validation.boundaries import insert_boundary_validators

        up_topic = LLMOperator(
            id="up_topic", name="Up Topic", model="test", prompt_template="topic",
            output_ports=[OutputPort(name="text")],
        )
        up_context = LLMOperator(
            id="up_context", name="Up Context", model="test", prompt_template="context",
            output_ports=[OutputPort(name="text")],
        )
        comp = CompositeNode(
            id="comp_custom",
            name="Comp Custom",
            body_graph="comp_custom_body",
            external_input_schema={"type": "object", "required": ["topic", "context"]},
            external_output_schema={"type": "object", "required": ["summary", "confidence"]},
            input_ports=[InputPort(name="topic"), InputPort(name="context")],
            output_ports=[OutputPort(name="summary"), OutputPort(name="confidence")],
        )
        down = LLMOperator(
            id="down", name="Down", model="test", prompt_template="downstream",
            input_ports=[InputPort(name="summary_in"), InputPort(name="confidence_in")],
        )

        graph = Graph(
            nodes=[up_topic, up_context, comp, down],
            edges=[
                DataEdge(
                    id="e_topic",
                    source_node_id="up_topic", source_port="text",
                    target_node_id="comp_custom", target_port="topic",
                ),
                DataEdge(
                    id="e_context",
                    source_node_id="up_context", source_port="text",
                    target_node_id="comp_custom", target_port="context",
                ),
                DataEdge(
                    id="e_summary",
                    source_node_id="comp_custom", source_port="summary",
                    target_node_id="down", target_port="summary_in",
                ),
                DataEdge(
                    id="e_conf",
                    source_node_id="comp_custom", source_port="confidence",
                    target_node_id="down", target_port="confidence_in",
                ),
            ],
            entry_points=["up_topic", "up_context"],
            exit_points=["down"],
        )

        result = insert_boundary_validators(graph, "comp_custom")

        data_edges = [e for e in result.edges if isinstance(e, DataEdge)]
        edge_tuples = {
            (e.source_node_id, e.source_port, e.target_node_id, e.target_port)
            for e in data_edges
        }

        assert ("up_topic", "text", "comp_custom__entry_validator", "topic") in edge_tuples
        assert ("up_context", "text", "comp_custom__entry_validator", "context") in edge_tuples
        assert (
            "comp_custom__entry_validator", "topic", "comp_custom", "topic"
        ) in edge_tuples
        assert (
            "comp_custom__entry_validator", "context", "comp_custom", "context"
        ) in edge_tuples
        assert ("comp_custom", "summary", "comp_custom__exit_validator", "summary") in edge_tuples
        assert (
            "comp_custom", "confidence", "comp_custom__exit_validator", "confidence"
        ) in edge_tuples
        assert (
            "comp_custom__exit_validator", "summary", "down", "summary_in"
        ) in edge_tuples
        assert (
            "comp_custom__exit_validator", "confidence", "down", "confidence_in"
        ) in edge_tuples

    def test_insert_boundary_validators_accepts_worker_composites(self):
        from dan.models.nodes import LLMOperator
        from dan.validation.boundaries import insert_boundary_validators
        from dan.worker import Worker

        comp = Worker(
            id="worker_comp",
            name="Worker Composite",
            role="manager",
            body_graph="worker_comp_body",
            external_input_schema={"type": "object", "required": ["topic"]},
            external_output_schema={"type": "object", "required": ["summary"]},
            input_ports=[InputPort(name="topic")],
            output_ports=[OutputPort(name="summary")],
        )
        upstream = LLMOperator(
            id="llm1",
            name="LLM",
            model="test",
            prompt_template="Generate",
            output_ports=[OutputPort(name="text")],
        )
        downstream = LLMOperator(
            id="llm2",
            name="LLM2",
            model="test",
            prompt_template="Consume",
            input_ports=[InputPort(name="input")],
        )

        graph = Graph(
            nodes=[upstream, comp, downstream],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="llm1",
                    source_port="text",
                    target_node_id="worker_comp",
                    target_port="topic",
                ),
                DataEdge(
                    id="e2",
                    source_node_id="worker_comp",
                    source_port="summary",
                    target_node_id="llm2",
                    target_port="input",
                ),
            ],
            entry_points=["llm1"],
            exit_points=["llm2"],
        )

        result = insert_boundary_validators(graph, "worker_comp")

        node_ids = {n.id for n in result.nodes}
        assert "worker_comp__entry_validator" in node_ids
        assert "worker_comp__exit_validator" in node_ids


# ===========================================================================
# Builder / decompiler round-trip
# ===========================================================================


class TestBuilderRoundTrip:
    def test_validator_build_decompile_rebuild(self):
        from dan.builder import workflow
        from dan.builder.decompiler import decompile
        from dan.worker.model import Worker

        wf = workflow("roundtrip_test")
        wf.validator(
            "v1",
            rules=[
                {"rule_type": "required_keys", "config": {"keys": ["name"]}},
                {"rule_type": "type_check", "config": {"checks": {"name": "str"}}},
            ],
            on_failure="route",
            strict_mode=True,
        )

        graph1 = wf.build()
        code = decompile(graph1)

        ns: dict = {}
        exec(code, ns)
        graph2 = ns["graph"]

        v1_node = graph1.node_by_id("v1")
        v2_node = graph2.node_by_id("v1")

        assert v1_node is not None
        assert v2_node is not None
        assert isinstance(v1_node, Worker)
        assert isinstance(v2_node, Worker)
        assert v1_node.node_type == v2_node.node_type == "worker"
        assert v1_node.role == v2_node.role == "validator"
        assert v1_node.metadata["validator_on_failure"] == v2_node.metadata["validator_on_failure"] == "route"
        assert v1_node.metadata["validator_strict_mode"] == v2_node.metadata["validator_strict_mode"] is True
        assert len(v1_node.validation_rules) == len(v2_node.validation_rules)

        for r1, r2 in zip(v1_node.validation_rules, v2_node.validation_rules):
            assert r1.rule_type == r2.rule_type
            assert r1.config == r2.config

    def test_validator_chaining(self):
        from dan.builder import workflow

        wf = workflow("chain_test")
        a = wf.llm("gen", model="test", prompt="Generate")
        v = wf.validator(
            "check",
            rules=[{"rule_type": "required_keys", "config": {"keys": ["text"]}}],
        )
        b = wf.llm("refine", model="test", prompt="Refine {check}")
        a >> v >> b

        graph = wf.build()

        assert len(graph.nodes) == 3
        data_edges = [e for e in graph.edges if isinstance(e, DataEdge)]
        gen_to_check = [
            e for e in data_edges
            if e.source_node_id == "gen" and e.target_node_id == "check"
        ]
        assert gen_to_check
        assert gen_to_check[0].target_port == "data"
        assert any(
            e.source_node_id == "check" and e.target_node_id == "refine"
            for e in data_edges
        )


# ===========================================================================
# Graph validation — edge port diagnostics
# ===========================================================================


class TestEdgePortDiagnostics:
    def test_bad_output_port_lists_available(self):
        from dan.models.nodes import LLMOperator
        from dan.validation.graph import validate_graph

        src = LLMOperator(
            id="src", name="Src", model="test", prompt_template="go",
            output_ports=[OutputPort(name="alpha"), OutputPort(name="beta")],
        )
        tgt = LLMOperator(
            id="tgt", name="Tgt", model="test", prompt_template="go",
            input_ports=[InputPort(name="data")],
        )
        graph = Graph(
            nodes=[src, tgt],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="src", source_port="nonexistent",
                    target_node_id="tgt", target_port="data",
                ),
            ],
            entry_points=["src"],
            exit_points=["tgt"],
        )

        errors = validate_graph(graph)
        port_errors = [e for e in errors if "nonexistent" in e]
        assert len(port_errors) == 1
        assert "available:" in port_errors[0]
        assert "alpha" in port_errors[0]
        assert "beta" in port_errors[0]

    def test_bad_input_port_lists_available(self):
        from dan.models.nodes import LLMOperator
        from dan.validation.graph import validate_graph

        src = LLMOperator(
            id="src", name="Src", model="test", prompt_template="go",
            output_ports=[OutputPort(name="text")],
        )
        tgt = LLMOperator(
            id="tgt", name="Tgt", model="test", prompt_template="go",
            input_ports=[InputPort(name="x"), InputPort(name="y")],
        )
        graph = Graph(
            nodes=[src, tgt],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="src", source_port="text",
                    target_node_id="tgt", target_port="missing",
                ),
            ],
            entry_points=["src"],
            exit_points=["tgt"],
        )

        errors = validate_graph(graph)
        port_errors = [e for e in errors if "missing" in e]
        assert len(port_errors) == 1
        assert "available:" in port_errors[0]
        assert "x" in port_errors[0]
        assert "y" in port_errors[0]
