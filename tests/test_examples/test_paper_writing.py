"""End-to-end motivating example: multi-agent paper-writing workflow.

Graph topology (from development-plan.md):

  [Idea Generator] → [Outline Planner] → [Section Writers (ForEach)]
                                                  ↓
                                        ┌─────────┼─────────┐
                                        ↓         ↓         ↓
                                  [Data Proc] [Analysis] [Fig Gen]
                                        └─────────┼─────────┘
                                                  ↓
                                        [Section Assembler]
                                                  ↓
                              ┌──→ [Reviewer Panel] ──→ [Reviser] ──┐
                              │       (WhileLoop — exit when           │
                              │        negligible comments or          │
                              │        max retries)                    │
                              └────────────────────────────────────────┘

This test constructs the full graph using the DAN type system and
asserts it passes all validation checks.
"""

import json

from dan.models.context import (
    CompactionRule,
    CompactionStrategy,
    ContextDeclaration,
    ContextMode,
    ContextProjection,
    FailurePolicy,
    MergeStrategy,
    NodeLocalState,
    SharedContextDeclaration,
)
from dan.models.control_flow import (
    ForEachNode,
    ReduceNode,
    WhileLoopNode,
)
from dan.models.edges import DataEdge, ContextEdge
from dan.models.graph import Graph, GraphMetadata
from dan.models.nodes import LLMOperator, ToolOperator
from dan.models.ports import InputPort, OutputPort
from dan.validation.graph import validate_graph


# ---- Shared schemas ----

_TEXT = {"type": "string"}
_SECTION_OBJ = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "content": {"type": "string"},
    },
    "required": ["title", "content"],
}
_SECTION_LIST = {"type": "array", "items": _SECTION_OBJ}
_OUTLINE = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "sections": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "sections"],
}
_REVIEW = {
    "type": "object",
    "properties": {
        "comments": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string"},
    },
    "required": ["comments", "verdict"],
}


def _build_section_writer_body() -> Graph:
    """Sub-graph executed per section by ForEachNode."""
    writer = LLMOperator(
        id="writer",
        name="Section Writer",
        model="claude-4",
        prompt_template="Write section '{section_title}' based on outline.",
        input_ports=[InputPort(name="section_title", json_schema=_TEXT)],
        output_ports=[OutputPort(name="section", json_schema=_SECTION_OBJ)],
    )
    return Graph(
        metadata=GraphMetadata(name="section_writer_body"),
        nodes=[writer],
        entry_points=["writer"],
        exit_points=["writer"],
    )


def _build_review_revise_body() -> Graph:
    """Sub-graph executed each iteration by the WhileLoop."""
    reviewer = LLMOperator(
        id="reviewer",
        name="Reviewer Panel",
        model="gpt-4o",
        prompt_template="Review this draft and provide comments.",
        input_ports=[InputPort(name="draft", json_schema=_TEXT)],
        output_ports=[
            OutputPort(name="review", json_schema=_REVIEW),
            OutputPort(name="draft_passthrough", json_schema=_TEXT),
        ],
    )
    reviser = LLMOperator(
        id="reviser",
        name="Reviser",
        model="claude-4",
        prompt_template="Revise draft based on review comments.",
        input_ports=[
            InputPort(name="draft", json_schema=_TEXT),
            InputPort(name="review", json_schema=_REVIEW),
        ],
        output_ports=[OutputPort(name="revised_draft", json_schema=_TEXT)],
    )
    return Graph(
        metadata=GraphMetadata(name="review_revise_body"),
        nodes=[reviewer, reviser],
        edges=[
            DataEdge(
                id="review_to_reviser",
                source_node_id="reviewer",
                source_port="review",
                target_node_id="reviser",
                target_port="review",
            ),
            DataEdge(
                id="draft_to_reviser",
                source_node_id="reviewer",
                source_port="draft_passthrough",
                target_node_id="reviser",
                target_port="draft",
            ),
        ],
        entry_points=["reviewer"],
        exit_points=["reviser"],
        shared_context=[
            SharedContextDeclaration(key="current_draft", json_schema=_TEXT),
        ],
    )


def build_paper_writing_graph() -> Graph:
    """Construct the full paper-writing workflow graph."""

    idea_gen = LLMOperator(
        id="idea_gen",
        name="Idea Generator",
        model="gpt-4o",
        prompt_template="Generate a research idea about {topic}.",
        input_ports=[InputPort(name="topic", json_schema=_TEXT)],
        output_ports=[OutputPort(name="idea", json_schema=_TEXT)],
    )

    outline_planner = LLMOperator(
        id="outline_planner",
        name="Outline Planner",
        model="gpt-4o",
        prompt_template="Create a paper outline for: {idea}",
        input_ports=[InputPort(name="idea", json_schema=_TEXT)],
        output_ports=[OutputPort(name="outline", json_schema=_OUTLINE)],
    )

    section_writers = ForEachNode(
        id="section_writers",
        name="Section Writers",
        body_graph="section_writer_body",
        parallelism=4,
        merge_strategy=MergeStrategy.APPEND,
        input_ports=[InputPort(name="outline", json_schema=_OUTLINE)],
        output_ports=[OutputPort(name="sections", json_schema=_SECTION_LIST)],
        external_input_schema=_OUTLINE,
        external_output_schema=_SECTION_LIST,
        read_set=[ContextDeclaration(key="outline", mode=ContextMode.READ)],
    )

    assembler = LLMOperator(
        id="assembler",
        name="Section Assembler",
        model="gpt-4o",
        prompt_template="Assemble these sections into a coherent paper.",
        input_ports=[InputPort(name="sections", json_schema=_SECTION_LIST)],
        output_ports=[OutputPort(name="draft", json_schema=_TEXT)],
    )

    review_loop = WhileLoopNode(
        id="review_loop",
        name="Review-Revise Loop",
        condition="review.verdict != 'accept' and iteration < max_iterations",
        body_graph="review_revise_body",
        max_iterations=3,
        input_ports=[InputPort(name="draft", json_schema=_TEXT)],
        output_ports=[OutputPort(name="final_draft", json_schema=_TEXT)],
        external_input_schema=_TEXT,
        external_output_schema=_TEXT,
        control_state_schema={
            "type": "object",
            "properties": {
                "iteration": {"type": "integer"},
                "last_verdict": {"type": "string"},
            },
        },
        local_state=NodeLocalState(
            json_schema={
                "type": "object",
                "properties": {
                    "revision_history": {"type": "array", "items": _TEXT},
                },
            },
            description="Tracks draft versions across iterations",
        ),
        read_set=[ContextDeclaration(key="outline", mode=ContextMode.READ)],
        write_set=[ContextDeclaration(key="current_draft", mode=ContextMode.WRITE)],
        compaction_rule=CompactionRule(
            strategy=CompactionStrategy.SLIDING_WINDOW, window_size=2,
        ),
        failure_policy=FailurePolicy(max_iterations=3, stagnation_threshold=2),
        projections=[
            ContextProjection(
                name="loop_controller",
                context_keys=["current_draft"],
                local_state_keys=["revision_history"],
            ),
            ContextProjection(
                name="reviser_view",
                context_keys=["outline", "current_draft"],
            ),
        ],
    )

    edges = [
        DataEdge(
            id="idea_to_outline",
            source_node_id="idea_gen",
            source_port="idea",
            target_node_id="outline_planner",
            target_port="idea",
        ),
        DataEdge(
            id="outline_to_writers",
            source_node_id="outline_planner",
            source_port="outline",
            target_node_id="section_writers",
            target_port="outline",
        ),
        DataEdge(
            id="writers_to_assembler",
            source_node_id="section_writers",
            source_port="sections",
            target_node_id="assembler",
            target_port="sections",
        ),
        DataEdge(
            id="assembler_to_review",
            source_node_id="assembler",
            source_port="draft",
            target_node_id="review_loop",
            target_port="draft",
        ),
    ]

    return Graph(
        version="dan_graph_v1",
        metadata=GraphMetadata(
            name="Paper Writing Workflow",
            description="Multi-agent paper writing with review-revise loop",
            tags=["paper-writing", "multi-agent"],
        ),
        nodes=[idea_gen, outline_planner, section_writers, assembler, review_loop],
        edges=edges,
        sub_graphs={
            "section_writer_body": _build_section_writer_body(),
            "review_revise_body": _build_review_revise_body(),
        },
        entry_points=["idea_gen"],
        exit_points=["review_loop"],
        shared_context=[
            SharedContextDeclaration(key="outline", json_schema=_OUTLINE, description="Paper outline"),
            SharedContextDeclaration(key="current_draft", json_schema=_TEXT, description="Latest draft"),
        ],
    )


# ---- Tests ----


class TestPaperWritingGraph:
    def test_construction(self):
        g = build_paper_writing_graph()
        assert len(g.nodes) == 5
        assert len(g.edges) == 4
        assert len(g.sub_graphs) == 2

    def test_passes_validation(self):
        g = build_paper_writing_graph()
        errors = validate_graph(g)
        assert errors == [], f"Validation errors: {errors}"

    def test_json_round_trip(self):
        g = build_paper_writing_graph()
        json_str = g.model_dump_json()
        restored = Graph.model_validate_json(json_str)

        assert restored.version == "dan_graph_v1"
        assert restored.metadata.name == "Paper Writing Workflow"
        assert len(restored.nodes) == 5
        assert len(restored.sub_graphs) == 2

        loop = restored.node_by_id("review_loop")
        assert loop is not None
        assert loop.node_type == "while_loop"
        assert loop.max_iterations == 3

    def test_json_is_parseable_by_any_language(self):
        """The JSON output must be valid JSON parseable without Pydantic."""
        g = build_paper_writing_graph()
        raw = json.loads(g.model_dump_json())
        assert raw["version"] == "dan_graph_v1"
        assert isinstance(raw["nodes"], list)
        assert isinstance(raw["sub_graphs"], dict)

    def test_sub_graph_validation(self):
        g = build_paper_writing_graph()
        for name, sg in g.sub_graphs.items():
            errors = validate_graph(sg)
            assert errors == [], f"Sub-graph '{name}' validation errors: {errors}"

    def test_schema_compatibility_chain(self):
        """Output schemas feed correctly into downstream input schemas."""
        g = build_paper_writing_graph()
        errors = validate_graph(g)
        assert not any("schema incompatibility" in e for e in errors)

    def test_context_declarations_present(self):
        g = build_paper_writing_graph()
        keys = {d.key for d in g.shared_context}
        assert "outline" in keys
        assert "current_draft" in keys

    def test_review_loop_contract(self):
        g = build_paper_writing_graph()
        loop = g.node_by_id("review_loop")
        assert loop.compaction_rule is not None
        assert loop.compaction_rule.strategy == CompactionStrategy.SLIDING_WINDOW
        assert loop.failure_policy.max_iterations == 3
        assert len(loop.projections) == 2
