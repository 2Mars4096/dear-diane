"""Tests for CodegenPromptBuilder (Plan 24-1, Task 1)."""

from __future__ import annotations

import pytest

from dan.meta.planner import CodegenPromptBuilder
from dan.workflow_generation_guidance import workflow_generation_contract_override


@pytest.fixture
def builder() -> CodegenPromptBuilder:
    return CodegenPromptBuilder()


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_contains_dsl_keywords(self, builder: CodegenPromptBuilder) -> None:
        prompt = builder.build_system_prompt()
        for kw in ("workflow", "wf.llm", "wf.tool", "wf.code", "wf.build()", ">>"):
            assert kw in prompt, f"System prompt missing keyword: {kw}"

    def test_contains_sub_graph_managers(self, builder: CodegenPromptBuilder) -> None:
        prompt = builder.build_system_prompt()
        for mgr in ("for_each", "while_loop", "composite", "parallel_subagents", "orchestrator"):
            assert mgr in prompt, f"System prompt missing sub-graph manager: {mgr}"

    def test_contains_node_methods(self, builder: CodegenPromptBuilder) -> None:
        prompt = builder.build_system_prompt()
        for method in ("wf.gate", "wf.rag", "wf.validator", "wf.reduce", "wf.router"):
            assert method in prompt, f"System prompt missing node method: {method}"

    def test_contains_edge_wiring(self, builder: CodegenPromptBuilder) -> None:
        prompt = builder.build_system_prompt()
        assert "wf.edge(" in prompt
        assert "NodeRef" in prompt

    def test_instructs_code_only_output(self, builder: CodegenPromptBuilder) -> None:
        prompt = builder.build_system_prompt()
        assert "ONLY Python code" in prompt

    def test_contains_workflow_generation_contract(self, builder: CodegenPromptBuilder) -> None:
        prompt = builder.build_system_prompt()
        assert "Workflow Generation Contract" in prompt
        assert "single-brace `{variable}` placeholders" in prompt
        assert "assign outputs through `result = ...`" in prompt
        assert "registered tool ids" in prompt

    def test_instructs_explicit_local_paths_to_use_tool_config(
        self,
        builder: CodegenPromptBuilder,
    ) -> None:
        prompt = builder.build_system_prompt()
        assert "concrete local file or directory path" in prompt
        assert 'tool_config`` (for example ``{"path": "tests/data.csv"}' in prompt

    def test_omits_workflow_generation_contract_when_override_disabled(
        self,
        builder: CodegenPromptBuilder,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DAN_WORKFLOW_GENERATION_CONTRACT_ENABLED", "1")
        with workflow_generation_contract_override(False):
            prompt = builder.build_system_prompt()
        assert "Workflow Generation Contract" not in prompt


# ---------------------------------------------------------------------------
# Few-shot examples
# ---------------------------------------------------------------------------


class TestFewShotExamples:
    def test_contains_all_six_patterns(self, builder: CodegenPromptBuilder) -> None:
        examples = builder.build_few_shot_examples()
        assert "Example 1" in examples  # chain
        assert "Example 2" in examples  # review loop
        assert "Example 3" in examples  # fan-out
        assert "Example 4" in examples  # RAG QA
        assert "Example 5" in examples  # while loop
        assert "Example 6" in examples  # composite

    def test_chain_example_has_three_nodes(self, builder: CodegenPromptBuilder) -> None:
        examples = builder.build_few_shot_examples()
        assert "3-node chain" in examples
        assert "wf.llm(" in examples
        assert "wf.code(" in examples

    def test_review_loop_example(self, builder: CodegenPromptBuilder) -> None:
        examples = builder.build_few_shot_examples()
        assert "while_loop" in examples
        assert "quality_score" in examples
        assert "CompactionRule" in examples

    def test_fan_out_example(self, builder: CodegenPromptBuilder) -> None:
        examples = builder.build_few_shot_examples()
        assert "for_each" in examples
        assert "MergeStrategy.APPEND" in examples
        assert "subtopics" in examples

    def test_rag_qa_example(self, builder: CodegenPromptBuilder) -> None:
        examples = builder.build_few_shot_examples()
        assert "RAG Q&A" in examples
        assert "file_read" in examples
        assert "text_chunk" in examples

    def test_while_loop_example(self, builder: CodegenPromptBuilder) -> None:
        examples = builder.build_few_shot_examples()
        assert "iterative_improve" in examples
        assert "score < 0.9" in examples

    def test_composite_example(self, builder: CodegenPromptBuilder) -> None:
        examples = builder.build_few_shot_examples()
        assert "composite" in examples
        assert "input_mappings" in examples
        assert "output_mappings" in examples

    def test_all_examples_call_build(self, builder: CodegenPromptBuilder) -> None:
        examples = builder.build_few_shot_examples()
        assert examples.count("graph = wf.build()") == 8


# ---------------------------------------------------------------------------
# User prompt
# ---------------------------------------------------------------------------


class TestUserPrompt:
    def test_formats_goal(self, builder: CodegenPromptBuilder) -> None:
        prompt = builder.build_user_prompt("Build a research pipeline")
        assert "## Goal" in prompt
        assert "Build a research pipeline" in prompt

    def test_includes_tools_when_provided(self, builder: CodegenPromptBuilder) -> None:
        prompt = builder.build_user_prompt(
            "Do research", tools=["web_search", "file_read"]
        )
        assert "## Available Tools" in prompt
        assert "web_search" in prompt
        assert "file_read" in prompt

    def test_omits_tools_when_none(self, builder: CodegenPromptBuilder) -> None:
        prompt = builder.build_user_prompt("Do research")
        assert "Available Tools" not in prompt

    def test_includes_skills_when_provided(self, builder: CodegenPromptBuilder) -> None:
        prompt = builder.build_user_prompt(
            "Write a paper", skills=["academic_writing", "latex"]
        )
        assert "## Available Skills" in prompt
        assert "academic_writing" in prompt
        assert "latex" in prompt

    def test_omits_skills_when_none(self, builder: CodegenPromptBuilder) -> None:
        prompt = builder.build_user_prompt("Write a paper")
        assert "Available Skills" not in prompt

    def test_includes_error_context(self, builder: CodegenPromptBuilder) -> None:
        prompt = builder.build_user_prompt(
            "Fix it", error_context="NameError: 'wf' is not defined on line 5"
        )
        assert "## Error from Prior Attempt" in prompt
        assert "NameError" in prompt
        assert "line 5" in prompt

    def test_omits_error_context_when_none(self, builder: CodegenPromptBuilder) -> None:
        prompt = builder.build_user_prompt("Build something")
        assert "Error from Prior Attempt" not in prompt

    def test_includes_constraints(self, builder: CodegenPromptBuilder) -> None:
        prompt = builder.build_user_prompt(
            "Pipeline",
            constraints={
                "inputs": ["topic", "file_path"],
                "outputs": ["report"],
                "max_nodes": 10,
            },
        )
        assert "## Constraints" in prompt
        assert "Required inputs" in prompt
        assert "topic" in prompt
        assert "Required outputs" in prompt
        assert "report" in prompt
        assert "max_nodes" in prompt

    def test_includes_self_knowledge_chunks(
        self, builder: CodegenPromptBuilder
    ) -> None:
        chunks = "[Source: llm-api-guide.md > ForEach]\nUse wf.for_each(...)..."
        prompt = builder.build_user_prompt("Fan out work", self_knowledge_chunks=chunks)
        assert "## Relevant API Reference" in prompt
        assert "wf.for_each" in prompt

    def test_omits_self_knowledge_when_none(
        self, builder: CodegenPromptBuilder
    ) -> None:
        prompt = builder.build_user_prompt("Simple task")
        assert "Relevant API Reference" not in prompt

    def test_ends_with_code_instruction(self, builder: CodegenPromptBuilder) -> None:
        prompt = builder.build_user_prompt("Build something")
        assert prompt.strip().endswith("executable Python code.")


# ---------------------------------------------------------------------------
# Full prompt
# ---------------------------------------------------------------------------


class TestFullPrompt:
    def test_returns_tuple(self, builder: CodegenPromptBuilder) -> None:
        result = builder.build_full_prompt("Build a chain")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_system_includes_examples(self, builder: CodegenPromptBuilder) -> None:
        system, _ = builder.build_full_prompt("Build a chain")
        assert "Example 1" in system
        assert "Example 6" in system

    def test_system_includes_dsl_reference(self, builder: CodegenPromptBuilder) -> None:
        system, _ = builder.build_full_prompt("Build a chain")
        assert "workflow" in system
        assert "wf.build()" in system

    def test_user_includes_goal(self, builder: CodegenPromptBuilder) -> None:
        _, user = builder.build_full_prompt("Build a research pipeline")
        assert "Build a research pipeline" in user

    def test_passes_through_all_params(self, builder: CodegenPromptBuilder) -> None:
        system, user = builder.build_full_prompt(
            "Research",
            tools=["web_search"],
            skills=["writing"],
            error_context="SyntaxError on line 3",
            constraints={"inputs": ["query"]},
            self_knowledge_chunks="API chunk text",
            graph_summary="Existing nodes: search -> summarize",
        )
        assert "Workflow Generation Contract" in system
        assert "single-brace `{variable}` placeholders" in system
        assert "assign outputs through `result = ...`" in system
        assert "web_search" in user
        assert "writing" in user
        assert "SyntaxError" in user
        assert "query" in user
        assert "API chunk text" in user
        assert "Existing Workflow (modify, don't rebuild from scratch)" in user
        assert "Existing nodes: search -> summarize" in user
