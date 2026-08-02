"""Snapshot tests for builder DSL output.

Compile known fixtures through the builder, then compare the output graph JSON
to stored snapshots. Run with UPDATE_SNAPSHOTS=1 to regenerate.
"""

from __future__ import annotations

from dan.builder import workflow
from dan.builder.refs import NodeRef
from dan.models.context import CompactionRule, CompactionStrategy, FailurePolicy, MergeStrategy

from tests.test_snapshots.conftest import assert_snapshot, update_snapshots


def build_simple_chain():
    """3 LLM nodes chained with >>."""
    wf = workflow("simple_chain")
    a = wf.llm("step1", prompt="Do step 1 on {input}")
    b = wf.llm("step2", prompt=f"Continue with: {a}")
    c = wf.llm("step3", prompt=f"Finish: {b}")
    a >> b >> c
    return wf.build()


def build_review_revise():
    """While-loop with draft + reviewer."""
    wf = workflow("review_revise")
    draft = wf.llm("draft", prompt="Write a paragraph about: {topic}", input_ports=[{"name": "topic"}])
    init = wf.code(
        "init_state",
        code='result = {"draft": text, "quality_score": 0, "feedback": "Initial."}',
        input_ports=[{"name": "text"}],
        output_ports=[{"name": "draft"}, {"name": "quality_score"}, {"name": "feedback"}],
    )
    wf.edge(draft["text"], init["text"])

    LOOP_IN = 'result = {"draft": draft, "quality_score": quality_score, "feedback": feedback}'
    LOOP_OUT = 'result = {"draft": revised_draft, "quality_score": quality_score, "feedback": feedback}'

    with wf.while_loop(
        "review_loop",
        condition="quality_score < 8",
        max_iterations=3,
        compaction=CompactionRule(strategy=CompactionStrategy.SLIDING_WINDOW, window_size=2),
        failure_policy=FailurePolicy(max_iterations=3, stagnation_threshold=2),
        input_ports=[{"name": "draft"}, {"name": "quality_score"}, {"name": "feedback"}],
        output_ports=[{"name": "draft"}, {"name": "quality_score"}, {"name": "feedback"}],
    ) as body:
        loop_in = body.code(
            "loop_in",
            code=LOOP_IN,
            input_ports=[{"name": "draft"}, {"name": "quality_score"}, {"name": "feedback"}],
            output_ports=[{"name": "draft"}, {"name": "quality_score"}, {"name": "feedback"}],
        )
        review = body.llm(
            "review",
            prompt="Review:\n{draft}\nRate 1-10 and give feedback.",
            output_schema={
                "type": "object",
                "properties": {"quality_score": {"type": "integer"}, "feedback": {"type": "string"}},
                "required": ["quality_score", "feedback"],
            },
            input_ports=[{"name": "draft"}],
        )
        body.edge(loop_in["draft"], review["draft"])

        revise = body.llm(
            "revise",
            prompt="Revise draft:\n{draft}\nFeedback: {feedback}",
            input_ports=[{"name": "draft"}, {"name": "quality_score"}, {"name": "feedback"}],
        )
        body.edge(loop_in["draft"], revise["draft"])
        body.edge(review["quality_score"], revise["quality_score"])
        body.edge(review["feedback"], revise["feedback"])

        loop_out = body.code(
            "loop_out",
            code=LOOP_OUT,
            input_ports=[{"name": "revised_draft"}, {"name": "quality_score"}, {"name": "feedback"}],
            output_ports=[{"name": "draft"}, {"name": "quality_score"}, {"name": "feedback"}],
        )
        body.edge(revise["text"], loop_out["revised_draft"])
        body.edge(review["quality_score"], loop_out["quality_score"])
        body.edge(review["feedback"], loop_out["feedback"])

    loop_ref = NodeRef("review_loop", "while_loop", wf)
    wf.edge(init["draft"], loop_ref["draft"])
    wf.edge(init["quality_score"], loop_ref["quality_score"])
    wf.edge(init["feedback"], loop_ref["feedback"])
    return wf.build()


def build_fan_out():
    """ForEach with parallel processing."""
    wf = workflow("fan_out")
    gen = wf.llm(
        "gen_subtopics",
        prompt="List 4 subtopics for: {topic}",
        output_schema={
            "type": "object",
            "properties": {"subtopics": {"type": "array", "items": {"type": "string"}}},
            "required": ["subtopics"],
        },
        input_ports=[{"name": "topic"}],
    )

    with wf.for_each("research", items=gen["subtopics"], parallelism=3, merge_strategy=MergeStrategy.APPEND) as body:
        body.llm("summarize", prompt="Summarize: {item}", input_ports=[{"name": "item"}, {"name": "index"}])

    ref = NodeRef("research", "for_each", wf)
    aggregate = wf.code(
        "aggregate",
        code='result = {"report": "\\n".join(str(r) for r in results)}',
        input_ports=[{"name": "results"}],
        output_ports=[{"name": "report"}],
    )
    wf.edge(ref["results"], aggregate["results"])
    return wf.build()


def build_tool_chain():
    """Input → tool → LLM → output."""
    wf = workflow("tool_chain")
    setup = wf.code(
        "setup",
        code='result = {"path": file_path, "question": question}',
        input_ports=[{"name": "file_path"}, {"name": "question"}],
        output_ports=[{"name": "path"}, {"name": "question"}],
    )
    read = wf.tool(
        "read_file",
        tool_id="file_read",
        input_ports=[{"name": "path"}],
        output_ports=[{"name": "content"}],
    )
    answer = wf.llm(
        "answer",
        prompt="Answer based on context:\nQuestion: {question}\nContext: {content}",
        input_ports=[{"name": "question"}, {"name": "content"}],
    )
    wf.edge(setup["path"], read["path"])
    wf.edge(setup["question"], answer["question"])
    wf.edge(read["content"], answer["content"])
    return wf.build()


def build_composite():
    """Composite node with sub-graph."""
    wf = workflow("composite")
    source = wf.llm("source", prompt="Describe topic: {topic}", input_ports=[{"name": "topic"}])

    with wf.composite(
        "research_block",
        input_mappings={"topic": "inner_topic"},
        output_mappings={"text": "result"},
    ) as sub:
        s1 = sub.llm("search", prompt="Research: {inner_topic}", input_ports=[{"name": "inner_topic"}])
        s2 = sub.llm("synthesize", prompt="Synthesize findings: {text}", input_ports=[{"name": "text"}])
        sub.edge(s1["text"], s2["text"])

    comp_ref = NodeRef("research_block", "composite", wf)
    wf.edge(source["text"], comp_ref["topic"])
    return wf.build()


def test_simple_chain():
    graph = build_simple_chain()
    assert_snapshot("builder_simple_chain", graph.model_dump(), update=update_snapshots())


def test_review_revise():
    graph = build_review_revise()
    assert_snapshot("builder_review_revise", graph.model_dump(), update=update_snapshots())


def test_fan_out():
    graph = build_fan_out()
    assert_snapshot("builder_fan_out", graph.model_dump(), update=update_snapshots())


def test_tool_chain():
    graph = build_tool_chain()
    assert_snapshot("builder_tool_chain", graph.model_dump(), update=update_snapshots())


def test_composite():
    graph = build_composite()
    assert_snapshot("builder_composite", graph.model_dump(), update=update_snapshots())
