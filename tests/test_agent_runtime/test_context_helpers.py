from __future__ import annotations

import dan.agent_runtime.graph_summary as runtime_graph_summary
import dan.agent_runtime.mutation_fallback as runtime_mutation_fallback
import dan.agent_runtime.mutation_parsing as runtime_mutation_parsing
import dan.agent_runtime.text_runtime as runtime_text_runtime
import dan.agent_runtime.tokens as runtime_tokens
import dan.server.chat.graph_summary as legacy_graph_summary
import dan.server.chat.mutation_fallback as legacy_mutation_fallback
import dan.server.chat.mutation_parser as legacy_mutation_parser
import dan.server.chat.text_runtime as legacy_text_runtime
import dan.server.chat.tokens as legacy_tokens
from dan.models.graph import Graph

EMPTY_GRAPH = {
    "version": "dan_graph_v1",
    "metadata": {"name": "empty"},
    "nodes": [],
    "edges": [],
}

MINIMAL_GRAPH = {
    "version": "dan_graph_v1",
    "metadata": {"name": "test"},
    "nodes": [
        {
            "id": "n1",
            "name": "Node 1",
            "node_type": "llm_operator",
            "model": "test-model",
            "prompt_template": "Hello",
        },
    ],
    "edges": [],
}


def test_server_chat_helper_modules_alias_agent_runtime_modules() -> None:
    assert legacy_graph_summary is runtime_graph_summary
    assert legacy_tokens is runtime_tokens
    assert legacy_text_runtime is runtime_text_runtime
    assert legacy_mutation_fallback is runtime_mutation_fallback


def test_server_chat_mutation_parser_reuses_agent_runtime_pure_helpers() -> None:
    assert legacy_mutation_parser._normalize_usage is runtime_mutation_parsing._normalize_usage
    assert legacy_mutation_parser._try_parse_mutation_json is runtime_mutation_parsing._try_parse_mutation_json
    import dan.agent_runtime.mutation_preview as runtime_mutation_preview

    assert legacy_mutation_parser._coerce_strict_edges is runtime_mutation_preview._coerce_strict_edges
    assert (
        legacy_mutation_parser._normalize_generated_mutation_ops
        is runtime_mutation_preview._normalize_generated_mutation_ops
    )
    assert (
        legacy_mutation_parser.normalize_mutation_ops_for_chat
        is runtime_mutation_preview.normalize_mutation_ops_for_chat
    )


def test_build_graph_summary_handles_empty_graph_from_agent_runtime() -> None:
    graph = Graph.model_validate(EMPTY_GRAPH)

    summary = runtime_graph_summary.build_graph_summary(graph, "wf-empty")

    assert summary.workflow_id == "wf-empty"
    assert summary.node_count == 0
    assert summary.edge_count == 0
    assert summary.revision == runtime_graph_summary.compute_graph_revision(EMPTY_GRAPH)


def test_serialize_for_prompt_uses_agent_runtime_graph_summary() -> None:
    graph = Graph.model_validate(MINIMAL_GRAPH)
    summary = runtime_graph_summary.build_graph_summary(graph, "wf-test")

    text = runtime_graph_summary.serialize_for_prompt(summary)

    assert 'Workflow: "test" (1 nodes, 0 edges)' in text
    assert "n1 [llm_operator]" in text
