"""Tests for dan.server.graph_mutator — graph mutation engine."""

from __future__ import annotations

import copy

import pytest

from dan.server.graph_mutator import (
    AddEdge,
    AddNode,
    EditNode,
    ExpandPattern,
    GraphMutator,
    MutationPlan,
    PATTERN_LIBRARY,
    RemoveEdge,
    RemoveNode,
    SetNodePosition,
)


def _make_test_graph_dict() -> dict:
    return {
        "version": "dan_graph_v1",
        "metadata": {"name": "test"},
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
                "model": "claude-sonnet-4-6",
                "prompt_template": "Write",
            },
            {
                "id": "n2",
                "node_type": "code_operator",
                "name": "Formatter",
                "input_ports": [{"name": "input", "schema": {}}],
                "output_ports": [{"name": "result", "schema": {}}],
                "position": {"x": 200, "y": 0},
                "ui": {},
                "metadata": {},
                "code": "print(x)",
                "language": "python",
                "sandbox_config": {},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "edge_type": "data",
                "source_node_id": "n1",
                "source_port": "text",
                "target_node_id": "n2",
                "target_port": "input",
                "ui": {},
                "metadata": {},
            },
        ],
        "sub_graphs": {},
        "entry_points": ["n1"],
        "exit_points": ["n2"],
        "shared_context": [],
        "artifact_refs": [],
    }


@pytest.fixture()
def graph_dict():
    return _make_test_graph_dict()


@pytest.fixture(autouse=True)
def _pin_legacy_graph_mutator_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DAN_WORKER_BUILDER", raising=False)


@pytest.fixture()
def mutator():
    return GraphMutator()


# ── AddNode ──────────────────────────────────────────────────────


class TestAddNode:
    def test_adds_llm_node_with_defaults(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddNode(node_type="llm_operator", name="Summarizer"),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        node = next(
            n for n in result.new_graph["nodes"] if n["id"] == "summarizer"
        )
        assert node["node_type"] == "worker"
        assert node["model"] == "claude-sonnet-4-6"
        assert "prompt_template" in node["llm_hints"]

    def test_adds_code_node_with_defaults(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddNode(node_type="code_operator", name="Runner"),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        node = next(
            n for n in result.new_graph["nodes"] if n["id"] == "runner"
        )
        assert node["language"] == "python"
        assert "code" in node

    def test_generated_id_is_unique_slug(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddNode(node_type="llm_operator", name="N1"),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        ids = [n["id"] for n in result.new_graph["nodes"]]
        assert len(ids) == len(set(ids))
        assert "n1-2" in ids

    def test_default_ports_for_if_else(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddNode(node_type="if_else", name="Branch"),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        node = next(
            n for n in result.new_graph["nodes"] if n["id"] == "branch"
        )
        out_names = [p["name"] for p in node["output_ports"]]
        assert "true" in out_names
        assert "false" in out_names

    def test_default_ports_for_rag_operator(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddNode(node_type="rag_operator", name="Retriever"),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        node = next(
            n for n in result.new_graph["nodes"] if n["id"] == "retriever"
        )
        in_names = [p["name"] for p in node["input_ports"]]
        out_names = [p["name"] for p in node["output_ports"]]
        assert "query" in in_names
        assert "chunks" in out_names
        assert "scores" in out_names

    def test_config_overrides_defaults(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddNode(
                node_type="llm_operator",
                name="Custom",
                config={"model": "gpt-4o", "temperature": 0.2},
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        node = next(
            n for n in result.new_graph["nodes"] if n["id"] == "custom"
        )
        assert node["model"] == "gpt-4o"
        assert node["llm_hints"]["temperature"] == 0.2

    def test_adds_to_empty_graph(self, mutator):
        empty = {"nodes": [], "edges": [], "entry_points": [], "exit_points": []}
        plan = MutationPlan(operations=[
            AddNode(node_type="input", name="Start"),
        ])
        result = mutator.apply(empty, plan)
        assert result.success
        assert len(result.new_graph["nodes"]) == 1

    def test_workerized_leaf_nodes_do_not_auto_create_empty_body_graphs(
        self,
        monkeypatch,
        mutator,
    ):
        monkeypatch.setenv("DAN_WORKER_BUILDER", "enabled")
        empty = {"nodes": [], "edges": [], "entry_points": [], "exit_points": []}
        plan = MutationPlan(operations=[
            AddNode(
                node_type="llm_operator",
                name="Draft",
                config={"model": "test-model", "prompt_template": "Draft {input}"},
            ),
        ])

        result = mutator.apply(empty, plan)

        assert result.success
        assert result.new_graph["nodes"][0]["node_type"] == "worker"
        assert result.new_graph.get("sub_graphs", {}) == {}

    def test_input_node_with_variables_keeps_aggregate_input_output(self, mutator):
        empty = {"nodes": [], "edges": [], "entry_points": [], "exit_points": []}
        plan = MutationPlan(operations=[
            AddNode(
                node_type="input",
                name="Inputs",
                config={"variables": [{"name": "ticker"}, {"name": "horizon"}]},
            ),
        ])
        result = mutator.apply(empty, plan)
        assert result.success
        node = result.new_graph["nodes"][0]
        out_names = [p["name"] for p in node["output_ports"]]
        assert "input" in out_names
        assert "ticker" in out_names
        assert "horizon" in out_names


# ── RemoveNode ───────────────────────────────────────────────────


class TestRemoveNode:
    def test_removes_node(self, graph_dict, mutator):
        plan = MutationPlan(operations=[RemoveNode(node_id="n2")])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        ids = {n["id"] for n in result.new_graph["nodes"]}
        assert "n2" not in ids
        assert "n1" in ids

    def test_cascades_connected_edges(self, graph_dict, mutator):
        plan = MutationPlan(operations=[RemoveNode(node_id="n2")])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        assert len(result.new_graph["edges"]) == 0

    def test_removes_from_entry_points(self, graph_dict, mutator):
        plan = MutationPlan(operations=[RemoveNode(node_id="n1")])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        assert "n1" not in result.new_graph.get("entry_points", [])

    def test_removes_from_exit_points(self, graph_dict, mutator):
        plan = MutationPlan(operations=[RemoveNode(node_id="n2")])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        assert "n2" not in result.new_graph.get("exit_points", [])

    def test_error_node_not_found(self, graph_dict, mutator):
        plan = MutationPlan(operations=[RemoveNode(node_id="nonexistent")])
        result = mutator.apply(graph_dict, plan)
        assert not result.success
        assert any("not found" in e.message for e in result.errors)


# ── EditNode ─────────────────────────────────────────────────────


class TestEditNode:
    def test_merges_updates(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            EditNode(node_id="n1", updates={"name": "Renamed Writer"}),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        node = next(n for n in result.new_graph["nodes"] if n["id"] == "n1")
        assert node["name"] == "Renamed Writer"
        assert node["model"] == "claude-sonnet-4-6"

    def test_updates_multiple_fields(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            EditNode(
                node_id="n1",
                updates={"model": "gpt-4o", "prompt_template": "Summarize"},
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        node = next(n for n in result.new_graph["nodes"] if n["id"] == "n1")
        assert node["model"] == "gpt-4o"
        assert node["prompt_template"] == "Summarize"

    def test_error_node_not_found(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            EditNode(node_id="ghost", updates={"name": "X"}),
        ])
        result = mutator.apply(graph_dict, plan)
        assert not result.success
        assert any("not found" in e.message for e in result.errors)


# ── AddEdge ──────────────────────────────────────────────────────


class TestAddEdge:
    def test_creates_edge(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddEdge(
                source_id="n2",
                source_port="result",
                target_id="n1",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        assert len(result.new_graph["edges"]) == 2

    def test_edge_has_correct_structure(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddEdge(
                edge_type="control",
                source_id="n2",
                source_port="result",
                target_id="n1",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        new_edge = next(
            e
            for e in result.new_graph["edges"]
            if e["source_node_id"] == "n2"
        )
        assert new_edge["edge_type"] == "control"
        assert new_edge["target_node_id"] == "n1"
        assert new_edge["id"] == "n2.result->n1.input"

    def test_error_source_not_found(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddEdge(
                source_id="missing",
                source_port="out",
                target_id="n1",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert not result.success
        assert any("Source" in e.message for e in result.errors)

    def test_error_target_not_found(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddEdge(
                source_id="n1",
                source_port="text",
                target_id="missing",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert not result.success
        assert any("Target" in e.message for e in result.errors)

    def test_deduplication_rejects_identical_edge(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddEdge(
                source_id="n2",
                source_port="result",
                target_id="n1",
                target_port="input",
            ),
            AddEdge(
                source_id="n2",
                source_port="result",
                target_id="n1",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert not result.success
        assert any("already exists" in e.message for e in result.errors)


# ── RemoveEdge ───────────────────────────────────────────────────


class TestRemoveEdge:
    def test_removes_matching_edge(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            RemoveEdge(
                source_id="n1",
                source_port="text",
                target_id="n2",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        assert len(result.new_graph["edges"]) == 0

    def test_error_edge_not_found(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            RemoveEdge(
                source_id="n1",
                source_port="nonexistent",
                target_id="n2",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert not result.success
        assert any("No edge" in e.message for e in result.errors)


# ── SetNodePosition ──────────────────────────────────────────────


class TestSetNodePosition:
    def test_updates_position(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            SetNodePosition(node_id="n1", x=100, y=200),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        node = next(n for n in result.new_graph["nodes"] if n["id"] == "n1")
        assert node["position"] == {"x": 100, "y": 200}

    def test_error_node_not_found(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            SetNodePosition(node_id="ghost", x=0, y=0),
        ])
        result = mutator.apply(graph_dict, plan)
        assert not result.success
        assert any("not found" in e.message for e in result.errors)


# ── MutationPlan: all_or_nothing ─────────────────────────────────


class TestAllOrNothing:
    def test_all_valid_ops_applied(self, graph_dict, mutator):
        plan = MutationPlan(
            apply_mode="all_or_nothing",
            operations=[
                EditNode(node_id="n1", updates={"name": "New Name"}),
                SetNodePosition(node_id="n2", x=50, y=50),
            ],
        )
        result = mutator.apply(graph_dict, plan)
        assert result.success
        assert len(result.applied_ops) == 2

    def test_one_invalid_rejects_entire_plan(self, graph_dict, mutator):
        original = copy.deepcopy(graph_dict)
        plan = MutationPlan(
            apply_mode="all_or_nothing",
            operations=[
                EditNode(node_id="n1", updates={"name": "New Name"}),
                EditNode(node_id="ghost", updates={"name": "Fail"}),
            ],
        )
        result = mutator.apply(graph_dict, plan)
        assert not result.success
        assert result.new_graph is None
        assert graph_dict == original


# ── MutationPlan: partial ────────────────────────────────────────


class TestPartialMode:
    def test_valid_ops_applied_invalid_skipped(self, graph_dict, mutator):
        plan = MutationPlan(
            apply_mode="partial",
            operations=[
                EditNode(node_id="n1", updates={"name": "Renamed"}),
                EditNode(node_id="ghost", updates={"name": "Fail"}),
            ],
        )
        result = mutator.apply(graph_dict, plan)
        assert not result.success
        assert result.new_graph is not None
        node = next(n for n in result.new_graph["nodes"] if n["id"] == "n1")
        assert node["name"] == "Renamed"
        assert len(result.errors) == 1
        assert len(result.applied_ops) == 1

    def test_all_valid_in_partial_mode(self, graph_dict, mutator):
        plan = MutationPlan(
            apply_mode="partial",
            operations=[
                EditNode(node_id="n1", updates={"name": "A"}),
                EditNode(node_id="n2", updates={"name": "B"}),
            ],
        )
        result = mutator.apply(graph_dict, plan)
        assert result.success
        assert len(result.applied_ops) == 2
        assert len(result.errors) == 0


# ── Concurrency check ───────────────────────────────────────────


class TestConcurrency:
    def test_matching_revision_applies(self, graph_dict, mutator):
        plan = MutationPlan(
            base_graph_revision="abc123",
            operations=[EditNode(node_id="n1", updates={"name": "OK"})],
        )
        result = mutator.apply(graph_dict, plan, current_revision="abc123")
        assert result.success

    def test_mismatching_revision_rejects(self, graph_dict, mutator):
        original = copy.deepcopy(graph_dict)
        plan = MutationPlan(
            base_graph_revision="old-rev",
            operations=[EditNode(node_id="n1", updates={"name": "Stale"})],
        )
        result = mutator.apply(graph_dict, plan, current_revision="new-rev")
        assert not result.success
        assert result.stale_plan is True
        assert result.new_graph is None
        assert graph_dict == original

    def test_no_revision_skips_check(self, graph_dict, mutator):
        plan = MutationPlan(
            operations=[EditNode(node_id="n1", updates={"name": "No Rev"})],
        )
        result = mutator.apply(graph_dict, plan)
        assert result.success


# ── Auto-sort ────────────────────────────────────────────────────


class TestAutoSort:
    def test_add_node_before_add_edge(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddEdge(
                source_id="n1",
                source_port="text",
                target_id="new-node",
                target_port="input",
            ),
            AddNode(node_type="code_operator", name="New Node"),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        ids = {n["id"] for n in result.new_graph["nodes"]}
        assert "new-node" in ids
        edge_targets = {e["target_node_id"] for e in result.new_graph["edges"]}
        assert "new-node" in edge_targets

    def test_remove_edge_before_remove_node(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            RemoveNode(node_id="n2"),
            RemoveEdge(
                source_id="n1",
                source_port="text",
                target_id="n2",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        ids = {n["id"] for n in result.new_graph["nodes"]}
        assert "n2" not in ids


# ── dry_run ──────────────────────────────────────────────────────


class TestDryRun:
    def test_does_not_modify_original(self, graph_dict, mutator):
        original = copy.deepcopy(graph_dict)
        plan = MutationPlan(operations=[
            AddNode(node_type="llm_operator", name="Temp"),
        ])
        result = mutator.dry_run(graph_dict, plan)
        assert result.success
        assert graph_dict == original
        assert len(result.new_graph["nodes"]) == 3

    def test_dry_run_returns_errors(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            RemoveNode(node_id="nonexistent"),
        ])
        result = mutator.dry_run(graph_dict, plan)
        assert not result.success
        assert len(result.errors) > 0

    def test_dry_run_with_concurrency_check(self, graph_dict, mutator):
        plan = MutationPlan(
            base_graph_revision="old",
            operations=[EditNode(node_id="n1", updates={"name": "X"})],
        )
        result = mutator.dry_run(graph_dict, plan, current_revision="new")
        assert not result.success
        assert result.stale_plan


class TestReplaceBodyGraph:
    def test_dry_run_infers_missing_body_entry_exit_points(self, mutator):
        empty = {
            "version": "dan_graph_v1",
            "metadata": {"name": "empty"},
            "nodes": [],
            "edges": [],
            "sub_graphs": {},
            "entry_points": [],
            "exit_points": [],
            "shared_context": [],
            "artifact_refs": [],
        }
        plan = MutationPlan.model_validate({
            "operations": [
                {
                    "op": "add_node",
                    "id": "process-items",
                    "node_type": "for_each",
                    "name": "Process Items",
                },
                {
                    "op": "replace_body_graph",
                    "node_id": "process-items",
                    "operations": [
                        {
                            "op": "add_node",
                            "node_type": "llm_operator",
                            "name": "Summarize Item",
                        },
                    ],
                },
            ],
        })

        result = mutator.dry_run(empty, plan)

        assert result.success, "; ".join(error.message for error in result.errors)
        assert result.new_graph is not None
        process_items = next(
            node for node in result.new_graph["nodes"] if node["id"] == "process-items"
        )
        body_key = process_items["body_graph"]
        body_graph = result.new_graph["sub_graphs"][body_key]
        assert body_graph["entry_points"] == ["summarize-item"]
        assert body_graph["exit_points"] == ["summarize-item"]


# ── Complex plan ─────────────────────────────────────────────────


class TestComplexPlan:
    def test_add_two_nodes_and_connect(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddNode(node_type="llm_operator", name="Analyzer"),
            AddNode(node_type="code_operator", name="Scorer"),
            AddEdge(
                source_id="analyzer",
                source_port="text",
                target_id="scorer",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        ids = {n["id"] for n in result.new_graph["nodes"]}
        assert "analyzer" in ids
        assert "scorer" in ids
        new_edges = [
            e
            for e in result.new_graph["edges"]
            if e["source_node_id"] == "analyzer"
        ]
        assert len(new_edges) == 1
        assert new_edges[0]["target_node_id"] == "scorer"

    def test_remove_node_and_rewire(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            RemoveNode(node_id="n2"),
            AddNode(node_type="code_operator", name="Replacement"),
            AddEdge(
                source_id="n1",
                source_port="text",
                target_id="replacement",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        ids = {n["id"] for n in result.new_graph["nodes"]}
        assert "n2" not in ids
        assert "replacement" in ids
        edges_from_n1 = [
            e
            for e in result.new_graph["edges"]
            if e["source_node_id"] == "n1"
        ]
        assert len(edges_from_n1) == 1
        assert edges_from_n1[0]["target_node_id"] == "replacement"


# ── Post-apply validation (Task 1) ──────────────────────────────


class TestPostApplyValidation:
    def test_dry_run_rejects_invalid_graph(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            EditNode(node_id="n1", updates={"output_ports": []}),
        ])
        result = mutator.dry_run(graph_dict, plan)
        assert not result.success
        assert any(e.op_type == "validation" for e in result.errors)

    def test_dry_run_collects_warnings(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            EditNode(node_id="n1", updates={"name": "Renamed Writer"}),
        ])
        result = mutator.dry_run(graph_dict, plan)
        assert result.success
        assert len(result.validation_warnings) > 0
        assert any("untyped" in w.lower() for w in result.validation_warnings)

    def test_existing_tests_still_pass(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            EditNode(node_id="n1", updates={"name": "No Change"}),
        ])
        result = mutator.dry_run(graph_dict, plan)
        assert result.success


# ── Port-aware AddEdge (Task 2) ─────────────────────────────────


class TestPortValidation:
    def test_add_edge_validates_source_port(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddEdge(
                source_id="n1",
                source_port="nonexistent",
                target_id="n2",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert not result.success
        assert any("available:" in e.message for e in result.errors)

    def test_add_edge_auto_creates_missing_target_port(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddEdge(
                source_id="n1",
                source_port="text",
                target_id="n2",
                target_port="custom_port",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        n2 = next(n for n in result.new_graph["nodes"] if n["id"] == "n2")
        port_names = [p["name"] for p in n2["input_ports"]]
        assert "custom_port" in port_names

    def test_add_edge_auto_create_adds_diagnostic(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddEdge(
                source_id="n1",
                source_port="text",
                target_id="n2",
                target_port="typo_port",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        assert len(result.diagnostics) == 1
        assert "typo_port" in result.diagnostics[0]
        assert "n2" in result.diagnostics[0]
        assert "Verify spelling" in result.diagnostics[0]

    def test_add_edge_strict_fails_on_missing_target_port(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddEdge(
                source_id="n1",
                source_port="text",
                target_id="n2",
                target_port="typo_port",
                strict=True,
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert not result.success
        assert any("has no input port 'typo_port'" in e.message for e in result.errors)
        assert any("Available ports:" in e.message for e in result.errors)
        assert any("strict=False" in e.message for e in result.errors)

    def test_add_edge_succeeds_with_valid_ports(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddEdge(
                source_id="n2",
                source_port="result",
                target_id="n1",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        assert len(result.new_graph["edges"]) == 2

    def test_input_node_compat_allows_source_port_input(self, mutator):
        graph = {
            "version": "dan_graph_v1",
            "metadata": {"name": "compat"},
            "nodes": [
                {
                    "id": "node_input",
                    "node_type": "input",
                    "name": "Input",
                    "input_ports": [],
                    "output_ports": [
                        {"name": "ticker", "schema": {}},
                        {"name": "company_name", "schema": {}},
                    ],
                    "position": {"x": 0, "y": 0},
                    "ui": {},
                    "metadata": {},
                    "variables": [
                        {"name": "ticker", "type": "string", "default": "", "description": ""},
                        {"name": "company_name", "type": "string", "default": "", "description": ""},
                    ],
                },
                {
                    "id": "n1",
                    "node_type": "llm_operator",
                    "name": "Analyzer",
                    "input_ports": [{"name": "input", "schema": {}, "required": False}],
                    "output_ports": [{"name": "text", "schema": {}}],
                    "position": {"x": 200, "y": 0},
                    "ui": {},
                    "metadata": {},
                    "model": "claude-sonnet-4-6",
                    "prompt_template": "",
                    "system_prompt": "",
                    "temperature": 0.7,
                },
            ],
            "edges": [],
            "sub_graphs": {},
            "entry_points": ["node_input"],
            "exit_points": ["n1"],
            "shared_context": [],
            "artifact_refs": [],
        }
        plan = MutationPlan(operations=[
            AddEdge(
                source_id="node_input",
                source_port="input",
                target_id="n1",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph, plan)
        assert result.success
        input_node = next(n for n in result.new_graph["nodes"] if n["id"] == "node_input")
        out_names = [p["name"] for p in input_node["output_ports"]]
        assert "input" in out_names


# ── Entry/exit point recomputation (Task 3) ─────────────────────


class TestEntryExitRecomputation:
    def test_add_node_becomes_entry_and_exit(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddNode(node_type="llm_operator", name="Isolated"),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        assert "isolated" in result.new_graph["entry_points"]
        assert "isolated" in result.new_graph["exit_points"]

    def test_add_edge_updates_entry_exit(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            AddNode(node_type="code_operator", name="Sink"),
            AddEdge(
                source_id="n1",
                source_port="text",
                target_id="sink",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        assert "sink" not in result.new_graph["entry_points"]
        assert "sink" in result.new_graph["exit_points"]

    def test_remove_edge_restores_entry_status(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            RemoveEdge(
                source_id="n1",
                source_port="text",
                target_id="n2",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        assert "n2" in result.new_graph["entry_points"]


# ── Pattern expansion (Task 7) ──────────────────────────────────


def _empty_graph() -> dict:
    return {
        "version": "dan_graph_v1",
        "metadata": {"name": "test-patterns"},
        "nodes": [],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [],
        "exit_points": [],
        "shared_context": [],
        "artifact_refs": [],
    }


class TestPatternExpansion:
    def test_chain_pattern_creates_nodes_and_edges(self, mutator):
        graph = _empty_graph()
        plan = MutationPlan(operations=[
            ExpandPattern(pattern="chain", params={"count": 3}),
        ])
        result = mutator.apply(graph, plan)
        assert result.success
        nodes = result.new_graph["nodes"]
        edges = result.new_graph["edges"]
        assert len(nodes) == 3
        assert len(edges) == 2
        assert result.new_graph["entry_points"] == ["step-1"]
        assert result.new_graph["exit_points"] == ["step-3"]

    def test_chain_pattern_custom_names(self, mutator):
        graph = _empty_graph()
        plan = MutationPlan(operations=[
            ExpandPattern(
                pattern="chain",
                params={
                    "names": ["Fetch", "Parse", "Store"],
                    "prompts": ["fetch data", "parse it", "store result"],
                },
            ),
        ])
        result = mutator.apply(graph, plan)
        assert result.success
        ids = {n["id"] for n in result.new_graph["nodes"]}
        assert ids == {"fetch", "parse", "store"}

    def test_review_loop_pattern_creates_cycle(self, mutator):
        graph = _empty_graph()
        plan = MutationPlan(operations=[
            ExpandPattern(pattern="review_loop"),
        ])
        result = mutator.apply(graph, plan)
        assert result.success
        nodes = result.new_graph["nodes"]
        edges = result.new_graph["edges"]
        node_ids = {n["id"] for n in nodes}
        assert "writer" in node_ids
        assert "reviewer" in node_ids
        assert "review-gate" in node_ids
        assert len(edges) == 3
        edge_pairs = {
            (e["source_node_id"], e["target_node_id"]) for e in edges
        }
        assert ("writer", "reviewer") in edge_pairs
        assert ("reviewer", "review-gate") in edge_pairs
        assert ("review-gate", "writer") in edge_pairs

    def test_rag_qa_pattern(self, mutator):
        graph = _empty_graph()
        plan = MutationPlan(operations=[
            ExpandPattern(
                pattern="rag_qa",
                params={"collection": "docs", "top_k": 10},
            ),
        ])
        result = mutator.apply(graph, plan)
        assert result.success
        nodes = result.new_graph["nodes"]
        edges = result.new_graph["edges"]
        types = {n["node_type"] for n in nodes}
        assert "worker" in types
        assert len(edges) == 1
        assert edges[0]["source_port"] == "chunks"

    def test_unknown_pattern_returns_error(self, mutator):
        graph = _empty_graph()
        plan = MutationPlan(operations=[
            ExpandPattern(pattern="nonexistent"),
        ])
        result = mutator.apply(graph, plan)
        assert not result.success
        err_msg = result.errors[0].message
        assert "Unknown pattern" in err_msg
        for name in sorted(PATTERN_LIBRARY.keys()):
            assert name in err_msg

    def test_pattern_combined_with_manual_ops(self, graph_dict, mutator):
        plan = MutationPlan(operations=[
            ExpandPattern(
                pattern="chain",
                params={"names": ["Alpha", "Beta"]},
            ),
            AddEdge(
                source_id="n1",
                source_port="text",
                target_id="alpha",
                target_port="input",
            ),
        ])
        result = mutator.apply(graph_dict, plan)
        assert result.success
        ids = {n["id"] for n in result.new_graph["nodes"]}
        assert "alpha" in ids
        assert "beta" in ids
        manual_edge = next(
            e for e in result.new_graph["edges"]
            if e["source_node_id"] == "n1" and e["target_node_id"] == "alpha"
        )
        assert manual_edge["source_port"] == "text"


# ── Stale-plan detection (Task 10) ──────────────────────────────


class TestStalePlanAndIdempotency:
    def test_stale_plan_detected(self, graph_dict, mutator):
        """Verify stale plan detection when revisions mismatch."""
        plan = MutationPlan(
            base_graph_revision="old-rev",
            operations=[EditNode(node_id="n1", updates={"name": "X"})],
        )
        result = mutator.apply(graph_dict, plan, current_revision="new-rev")
        assert not result.success
        assert result.stale_plan is True


# ── Apply idempotency request model (Task 10) ───────────────────


class TestApplyIdempotency:
    def test_idempotency_key_in_request_model(self):
        """Verify the request model accepts an idempotency key."""
        from dan.server.app import ApplyMutationRequest

        req = ApplyMutationRequest(
            mutation_plan={"operations": []},
            idempotency_key="test-key-123",
        )
        assert req.idempotency_key == "test-key-123"

    def test_idempotency_key_optional(self):
        from dan.server.app import ApplyMutationRequest

        req = ApplyMutationRequest(mutation_plan={"operations": []})
        assert req.idempotency_key is None
