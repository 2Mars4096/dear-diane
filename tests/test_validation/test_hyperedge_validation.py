"""Hyperedge validation, skill library conversion, and mutation operation tests."""

import copy

from dan.models.control_flow import CompositeNode, WhileLoopNode
from dan.models.graph import Graph, GraphMetadata
from dan.models.hyperedges import Hyperedge
from dan.models.nodes import LLMOperator
from dan.models.ports import InputPort, OutputPort
from dan.server.graph_mutator import (
    AddHyperedge,
    ApplySkill,
    EditHyperedge,
    GraphMutator,
    MutationPlan,
    RemoveHyperedge,
)
from dan.server.skill_library import SKILL_LIBRARY, get_builtin_hyperedges
from dan.validation.graph import validate_graph

_STR = {"type": "string"}


def _node(
    id: str,
    *,
    inp: list[InputPort] | None = None,
    out: list[OutputPort] | None = None,
    tags: list[str] | None = None,
) -> LLMOperator:
    return LLMOperator(
        id=id,
        name=id,
        model="m",
        prompt_template="p",
        input_ports=inp or [],
        output_ports=out or [],
        tags=tags or [],
    )


def _minimal_graph(**overrides) -> Graph:
    """Single-node graph that validates cleanly, with optional overrides."""
    defaults = dict(
        nodes=[_node("a")],
        entry_points=["a"],
        exit_points=["a"],
        hyperedges=[],
    )
    defaults.update(overrides)
    return Graph(**defaults)


# -----------------------------------------------------------------------
# 1. attach_to with non-existent node IDs
# -----------------------------------------------------------------------

class TestAttachToNodeIds:
    def test_warns_on_nonexistent_node(self):
        he = Hyperedge(
            id="he1", name="test", hyperedge_type="skill", hook="pre_prompt",
            content="x", attach_to=["missing_node"],
        )
        g = _minimal_graph(hyperedges=[he])
        errors = validate_graph(g)
        assert any("non-existent node 'missing_node'" in e for e in errors)

    def test_valid_attach_to(self):
        he = Hyperedge(
            id="he1", name="test", hyperedge_type="skill", hook="pre_prompt",
            content="x", attach_to=["a"],
        )
        g = _minimal_graph(hyperedges=[he])
        errors = validate_graph(g)
        assert not any("non-existent node" in e for e in errors)


# -----------------------------------------------------------------------
# 2. attach_to_type with unknown node types
# -----------------------------------------------------------------------

class TestAttachToType:
    def test_warns_on_unknown_type(self):
        he = Hyperedge(
            id="he1", name="test", hyperedge_type="skill", hook="pre_prompt",
            content="x", attach_to_type=["nonexistent_type"],
        )
        g = _minimal_graph(hyperedges=[he])
        errors = validate_graph(g)
        assert any("unknown node type 'nonexistent_type'" in e for e in errors)

    def test_valid_type(self):
        he = Hyperedge(
            id="he1", name="test", hyperedge_type="skill", hook="pre_prompt",
            content="x", attach_to_type=["llm_operator"],
        )
        g = _minimal_graph(hyperedges=[he])
        errors = validate_graph(g)
        assert not any("unknown node type" in e for e in errors)


# -----------------------------------------------------------------------
# 3. attach_to_subgraph with non-composite nodes
# -----------------------------------------------------------------------

class TestAttachToSubgraph:
    def test_warns_on_non_composite_node(self):
        he = Hyperedge(
            id="he1", name="test", hyperedge_type="skill", hook="pre_prompt",
            content="x", attach_to_subgraph=["a"],
        )
        g = _minimal_graph(hyperedges=[he])
        errors = validate_graph(g)
        assert any("not a composite-type node" in e for e in errors)

    def test_warns_on_missing_subgraph_node(self):
        he = Hyperedge(
            id="he1", name="test", hyperedge_type="skill", hook="pre_prompt",
            content="x", attach_to_subgraph=["ghost"],
        )
        g = _minimal_graph(hyperedges=[he])
        errors = validate_graph(g)
        assert any("non-existent node 'ghost'" in e for e in errors)

    def test_valid_composite_subgraph(self):
        inner = Graph(nodes=[_node("inner_a")], entry_points=["inner_a"], exit_points=["inner_a"])
        comp = CompositeNode(id="comp", name="comp", body_graph="inner")
        he = Hyperedge(
            id="he1", name="test", hyperedge_type="skill", hook="pre_prompt",
            content="x", attach_to_subgraph=["comp"],
        )
        g = Graph(
            nodes=[comp],
            sub_graphs={"inner": inner},
            entry_points=["comp"],
            exit_points=["comp"],
            hyperedges=[he],
        )
        errors = validate_graph(g)
        assert not any("not a composite-type node" in e for e in errors)

    def test_valid_while_loop_subgraph(self):
        inner = Graph(nodes=[_node("inner_a")], entry_points=["inner_a"], exit_points=["inner_a"])
        loop = WhileLoopNode(id="loop", name="loop", condition="True", body_graph="inner")
        he = Hyperedge(
            id="he1", name="test", hyperedge_type="skill", hook="pre_prompt",
            content="x", attach_to_subgraph=["loop"],
        )
        g = Graph(
            nodes=[loop],
            sub_graphs={"inner": inner},
            entry_points=["loop"],
            exit_points=["loop"],
            hyperedges=[he],
        )
        errors = validate_graph(g)
        assert not any("not a composite-type node" in e for e in errors)


# -----------------------------------------------------------------------
# 4. Duplicate hyperedge IDs
# -----------------------------------------------------------------------

class TestDuplicateHyperedgeIds:
    def test_warns_on_duplicates(self):
        he1 = Hyperedge(
            id="dup", name="first", hyperedge_type="skill", hook="pre_prompt",
            content="a", attach_globally=True,
        )
        he2 = Hyperedge(
            id="dup", name="second", hyperedge_type="skill", hook="pre_prompt",
            content="b", attach_globally=True,
        )
        g = _minimal_graph(hyperedges=[he1, he2])
        errors = validate_graph(g)
        assert any("duplicate hyperedge id 'dup'" in e for e in errors)

    def test_unique_ids_no_warning(self):
        he1 = Hyperedge(
            id="he1", name="first", hyperedge_type="skill", hook="pre_prompt",
            content="a", attach_globally=True,
        )
        he2 = Hyperedge(
            id="he2", name="second", hyperedge_type="skill", hook="pre_prompt",
            content="b", attach_globally=True,
        )
        g = _minimal_graph(hyperedges=[he1, he2])
        errors = validate_graph(g)
        assert not any("duplicate hyperedge" in e for e in errors)


# -----------------------------------------------------------------------
# 5. Valid hyperedges pass validation
# -----------------------------------------------------------------------

class TestValidHyperedges:
    def test_passes_for_valid_setup(self):
        he = Hyperedge(
            id="valid_skill", name="Valid", hyperedge_type="skill", hook="pre_prompt",
            content="some skill text", attach_to=["a"],
        )
        g = _minimal_graph(hyperedges=[he])
        errors = validate_graph(g)
        hyperedge_errors = [e for e in errors if "hyperedge" in e.lower()]
        assert hyperedge_errors == []

    def test_hook_type_compat_warning(self):
        """Force a hook/type mismatch at graph level using model_construct to bypass
        both Hyperedge and Graph model validators (simulates a raw-imported graph)."""
        bad_he = Hyperedge.model_construct(
            id="bad_hook", name="bad", hyperedge_type="skill",
            hook="tool_call", content="x", attach_globally=True,
            attach_to=[], attach_to_type=[], attach_to_tags=[],
            attach_to_subgraph=[], enabled=True, propagate=True,
            priority=None, description="", config={},
        )
        base = _minimal_graph()
        g = Graph.model_construct(
            version=base.version, metadata=base.metadata,
            nodes=base.nodes, edges=base.edges,
            sub_graphs=base.sub_graphs,
            entry_points=base.entry_points, exit_points=base.exit_points,
            shared_context=base.shared_context, artifact_refs=base.artifact_refs,
            hyperedges=[bad_he],
        )
        errors = validate_graph(g)
        assert any("not valid for type 'skill'" in e for e in errors)


# -----------------------------------------------------------------------
# 6. get_builtin_hyperedges returns valid Hyperedge instances
# -----------------------------------------------------------------------

class TestGetBuiltinHyperedges:
    def test_returns_list_of_hyperedges(self):
        result = get_builtin_hyperedges()
        assert isinstance(result, list)
        assert len(result) == 3
        for he in result:
            assert isinstance(he, Hyperedge)

    def test_management_science_writing(self):
        result = get_builtin_hyperedges()
        ms = next(h for h in result if h.id == "builtin_management_science_writing")
        assert ms.hyperedge_type == "skill"
        assert ms.hook == "pre_prompt"
        assert ms.attach_to_tags == ["writing", "review"]
        assert ms.propagate is True
        assert "Management Science" in ms.content

    def test_informs_latex_style(self):
        result = get_builtin_hyperedges()
        latex = next(h for h in result if h.id == "builtin_informs_latex_style")
        assert latex.hyperedge_type == "style"
        assert latex.hook == "pre_prompt"
        assert latex.attach_to_tags == ["latex"]
        assert "INFORMS" in latex.content

    def test_content_matches_skill_library(self):
        result = get_builtin_hyperedges()
        ms = next(h for h in result if h.id == "builtin_management_science_writing")
        assert ms.content == SKILL_LIBRARY["management_science_writing"]["text"]
        latex = next(h for h in result if h.id == "builtin_informs_latex_style")
        assert latex.content == SKILL_LIBRARY["informs_latex_style"]["text"]
        skill_creation = next(h for h in result if h.id == "builtin_skill_creation")
        assert skill_creation.content == SKILL_LIBRARY["skill_creation"]["text"]

    def test_skill_creation(self):
        result = get_builtin_hyperedges()
        skill_creation = next(h for h in result if h.id == "builtin_skill_creation")
        assert skill_creation.hyperedge_type == "skill"
        assert skill_creation.hook == "pre_prompt"
        assert skill_creation.attach_to_tags == ["skill", "skills", "authoring", "capability"]
        assert "SKILL.md" in skill_creation.content


# -----------------------------------------------------------------------
# 7. ApplySkill creates graph-level hyperedge (new path)
# -----------------------------------------------------------------------

class TestApplySkillHyperedgePath:
    def _base_graph_dict(self) -> dict:
        """Graph dict that triggers the hyperedge code path."""
        return {
            "version": "dan_graph_v1",
            "metadata": {"name": "test"},
            "nodes": [
                {
                    "id": "writer",
                    "node_type": "llm_operator",
                    "name": "Writer",
                    "model": "m",
                    "prompt_template": "p",
                    "system_prompt": "s",
                    "input_ports": [],
                    "output_ports": [{"name": "text", "schema": {}}],
                    "metadata": {"tags": ["writing"]},
                },
            ],
            "edges": [],
            "entry_points": ["writer"],
            "exit_points": ["writer"],
            "hyperedges": [],
        }

    def test_creates_hyperedge_with_target_nodes(self):
        gd = self._base_graph_dict()
        mutator = GraphMutator()
        plan = MutationPlan(operations=[
            ApplySkill(skill="management_science_writing", target_nodes=["writer"]),
        ])
        result = mutator.apply(gd, plan)
        assert result.success
        he_list = result.new_graph["hyperedges"]
        assert len(he_list) == 1
        assert he_list[0]["attach_to"] == ["writer"]
        assert he_list[0]["hyperedge_type"] == "skill"

    def test_creates_hyperedge_with_target_tag(self):
        gd = self._base_graph_dict()
        mutator = GraphMutator()
        plan = MutationPlan(operations=[
            ApplySkill(skill="management_science_writing", target_tag="writing"),
        ])
        result = mutator.apply(gd, plan)
        assert result.success
        he_list = result.new_graph["hyperedges"]
        assert len(he_list) == 1
        assert he_list[0]["attach_to_tags"] == ["writing"]

    def test_creates_hyperedge_with_default_tags(self):
        gd = self._base_graph_dict()
        mutator = GraphMutator()
        plan = MutationPlan(operations=[
            ApplySkill(skill="management_science_writing"),
        ])
        result = mutator.apply(gd, plan)
        assert result.success
        he_list = result.new_graph["hyperedges"]
        assert len(he_list) == 1
        assert he_list[0]["attach_to_tags"] == ["writing", "review"]

    def test_legacy_path_still_works(self):
        """Without hyperedges key, falls back to prompt injection."""
        gd = {
            "version": "dan_graph_v1",
            "metadata": {"name": "test"},
            "nodes": [
                {
                    "id": "writer",
                    "node_type": "llm_operator",
                    "name": "Writer",
                    "model": "m",
                    "prompt_template": "p",
                    "system_prompt": "s",
                    "input_ports": [],
                    "output_ports": [{"name": "text", "schema": {}}],
                    "metadata": {"tags": ["writing"]},
                },
            ],
            "edges": [],
            "entry_points": ["writer"],
            "exit_points": ["writer"],
        }
        mutator = GraphMutator()
        plan = MutationPlan(operations=[
            ApplySkill(skill="management_science_writing", target_tag="writing"),
        ])
        result = mutator.apply(gd, plan)
        assert result.success
        node = result.new_graph["nodes"][0]
        assert SKILL_LIBRARY["management_science_writing"]["text"] in node["system_prompt"]


# -----------------------------------------------------------------------
# 8. add_hyperedge / remove_hyperedge / edit_hyperedge mutation ops
# -----------------------------------------------------------------------

class TestHyperedgeMutationOps:
    def _base_graph_dict(self) -> dict:
        return {
            "version": "dan_graph_v1",
            "metadata": {"name": "test"},
            "nodes": [
                {
                    "id": "a",
                    "node_type": "llm_operator",
                    "name": "A",
                    "model": "m",
                    "prompt_template": "p",
                    "input_ports": [],
                    "output_ports": [{"name": "text", "schema": {}}],
                },
            ],
            "edges": [],
            "entry_points": ["a"],
            "exit_points": ["a"],
            "hyperedges": [],
        }

    def test_add_hyperedge(self):
        gd = self._base_graph_dict()
        mutator = GraphMutator()
        plan = MutationPlan(operations=[
            AddHyperedge(hyperedge={
                "id": "new_he",
                "name": "New",
                "hyperedge_type": "skill",
                "hook": "pre_prompt",
                "content": "test content",
                "attach_to": ["a"],
            }),
        ])
        result = mutator.apply(gd, plan)
        assert result.success
        assert len(result.new_graph["hyperedges"]) == 1
        assert result.new_graph["hyperedges"][0]["id"] == "new_he"

    def test_add_hyperedge_duplicate_id_fails(self):
        gd = self._base_graph_dict()
        gd["hyperedges"] = [{"id": "existing", "name": "E", "hyperedge_type": "skill",
                             "hook": "pre_prompt", "content": "x", "attach_globally": True}]
        mutator = GraphMutator()
        plan = MutationPlan(operations=[
            AddHyperedge(hyperedge={"id": "existing", "name": "Dup",
                                    "hyperedge_type": "skill", "hook": "pre_prompt",
                                    "content": "y", "attach_globally": True}),
        ])
        result = mutator.apply(gd, plan)
        assert not result.success
        assert any("already exists" in e.message for e in result.errors)

    def test_remove_hyperedge(self):
        gd = self._base_graph_dict()
        gd["hyperedges"] = [{"id": "to_remove", "name": "R", "hyperedge_type": "skill",
                             "hook": "pre_prompt", "content": "x", "attach_globally": True}]
        mutator = GraphMutator()
        plan = MutationPlan(operations=[RemoveHyperedge(hyperedge_id="to_remove")])
        result = mutator.apply(gd, plan)
        assert result.success
        assert len(result.new_graph["hyperedges"]) == 0

    def test_remove_hyperedge_not_found(self):
        gd = self._base_graph_dict()
        mutator = GraphMutator()
        plan = MutationPlan(operations=[RemoveHyperedge(hyperedge_id="ghost")])
        result = mutator.apply(gd, plan)
        assert not result.success
        assert any("not found" in e.message for e in result.errors)

    def test_edit_hyperedge(self):
        gd = self._base_graph_dict()
        gd["hyperedges"] = [{"id": "he1", "name": "Old Name", "hyperedge_type": "skill",
                             "hook": "pre_prompt", "content": "old", "attach_globally": True}]
        mutator = GraphMutator()
        plan = MutationPlan(operations=[
            EditHyperedge(hyperedge_id="he1", updates={"name": "New Name", "content": "new"}),
        ])
        result = mutator.apply(gd, plan)
        assert result.success
        he = result.new_graph["hyperedges"][0]
        assert he["name"] == "New Name"
        assert he["content"] == "new"
        assert he["id"] == "he1"

    def test_edit_hyperedge_not_found(self):
        gd = self._base_graph_dict()
        mutator = GraphMutator()
        plan = MutationPlan(operations=[
            EditHyperedge(hyperedge_id="ghost", updates={"name": "X"}),
        ])
        result = mutator.apply(gd, plan)
        assert not result.success
        assert any("not found" in e.message for e in result.errors)
