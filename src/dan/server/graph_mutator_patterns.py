"""Pattern library helpers for GraphMutator."""

from __future__ import annotations

from typing import Any

from dan.server.graph_mutator_helpers import _slugify

__all__ = ["PATTERN_LIBRARY"]


def _pattern_chain(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Chain of N LLM nodes connected sequentially."""
    count = params.get("count", 3)
    names = params.get("names", [f"Step {i+1}" for i in range(count)])
    prompts = params.get("prompts", ["" for _ in range(count)])
    ops: list[dict[str, Any]] = []
    for i, name in enumerate(names):
        prompt = prompts[i] if i < len(prompts) else ""
        ops.append({
            "op": "add_node",
            "node_type": "llm_operator",
            "name": name,
            "config": {"prompt_template": prompt},
        })
    for i in range(len(names) - 1):
        src_id = _slugify(names[i])
        tgt_id = _slugify(names[i + 1])
        ops.append({
            "op": "add_edge",
            "source_id": src_id,
            "source_port": "text",
            "target_id": tgt_id,
            "target_port": "input",
        })
    return ops


def _pattern_review_loop(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Writer -> Reviewer -> Gate (while) with back-edge to Writer."""
    writer_name = params.get("writer_name", "Writer")
    reviewer_name = params.get("reviewer_name", "Reviewer")
    gate_name = params.get("gate_name", "Review Gate")
    condition = params.get("condition", "needs_revision == True")
    max_iter = params.get("max_iterations", 5)
    return [
        {
            "op": "add_node",
            "node_type": "llm_operator",
            "name": writer_name,
            "config": {"prompt_template": params.get("writer_prompt", "")},
        },
        {
            "op": "add_node",
            "node_type": "llm_operator",
            "name": reviewer_name,
            "config": {"prompt_template": params.get("reviewer_prompt", "")},
        },
        {
            "op": "add_node",
            "node_type": "gate",
            "name": gate_name,
            "config": {
                "gate_mode": "while",
                "condition": condition,
                "max_iterations": max_iter,
            },
        },
        {
            "op": "add_edge",
            "source_id": _slugify(writer_name),
            "source_port": "text",
            "target_id": _slugify(reviewer_name),
            "target_port": "input",
        },
        {
            "op": "add_edge",
            "source_id": _slugify(reviewer_name),
            "source_port": "text",
            "target_id": _slugify(gate_name),
            "target_port": "input",
        },
        {
            "op": "add_edge",
            "edge_type": "control",
            "source_id": _slugify(gate_name),
            "source_port": "continue",
            "target_id": _slugify(writer_name),
            "target_port": "input",
        },
    ]


def _pattern_fan_out(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Source -> ForEach with body LLM -> downstream collector."""
    source_name = params.get("source_name", "Source")
    body_name = params.get("body_name", "Processor")
    body_id = _slugify(body_name)
    return [
        {
            "op": "add_node",
            "node_type": "llm_operator",
            "name": source_name,
            "config": {"prompt_template": params.get("source_prompt", "")},
        },
        {
            "op": "add_node",
            "node_type": "for_each",
            "name": "Fan Out",
            "config": {"parallelism": params.get("parallelism", 1)},
        },
        {
            "op": "add_edge",
            "source_id": _slugify(source_name),
            "source_port": "text",
            "target_id": "fan-out",
            "target_port": "items",
        },
        {
            "op": "replace_body_graph",
            "node_id": "fan-out",
            "operations": [
                {
                    "op": "add_node",
                    "id": body_id,
                    "node_type": "llm_operator",
                    "name": body_name,
                    "config": {
                        "prompt_template": params.get("body_prompt", "{item}"),
                        "input_ports": [
                            {"name": "item", "schema": {}, "required": False}
                        ],
                        "output_ports": [{"name": "text", "schema": {}}],
                    },
                },
            ],
            "entry_ids": [body_id],
            "exit_ids": [body_id],
        },
    ]


def _pattern_rag_qa(params: dict[str, Any]) -> list[dict[str, Any]]:
    """RAG retrieval -> LLM answer node."""
    rag_name = params.get("rag_name", "Knowledge Base")
    answer_name = params.get("answer_name", "Answer Generator")
    collection = params.get("collection", "")
    top_k = params.get("top_k", 5)
    return [
        {
            "op": "add_node",
            "node_type": "rag_operator",
            "name": rag_name,
            "config": {"collection": collection, "top_k": top_k},
        },
        {
            "op": "add_node",
            "node_type": "llm_operator",
            "name": answer_name,
            "config": {
                "prompt_template": params.get(
                    "answer_prompt", "Answer based on: {input}"
                ),
            },
        },
        {
            "op": "add_edge",
            "source_id": _slugify(rag_name),
            "source_port": "chunks",
            "target_id": _slugify(answer_name),
            "target_port": "input",
        },
    ]


def _pattern_data_ingest(params: dict[str, Any]) -> list[dict[str, Any]]:
    """PDF directory -> index into RAG collection -> retrieval-ready."""
    input_var = params.get("input_var", "pdf_dir")
    collection = params.get("collection", "literature")
    rag_name = params.get("rag_name", "Literature KB")
    top_k = params.get("top_k", 5)
    return [
        {"op": "add_node", "node_type": "input", "name": "PDF Input",
         "config": {"variables": [
             {"name": input_var, "type": "string", "default": "", "description": "Path to PDF directory"},
             {"name": "topic", "type": "string", "default": "", "description": "Research topic or query for retrieval"},
         ]}},
        {"op": "add_node", "node_type": "tool_operator", "name": "Index Documents",
         "config": {"tool_id": "rag_index_documents", "tool_config": {"collection": collection}}},
        {"op": "add_node", "node_type": "rag_operator", "name": rag_name,
         "config": {"collection": collection, "top_k": top_k}},
        {"op": "add_edge", "source_id": "pdf-input", "source_port": input_var,
         "target_id": "index-documents", "target_port": "pdf_dir"},
        {"op": "add_edge", "source_id": "pdf-input", "source_port": "topic",
         "target_id": _slugify(rag_name), "target_port": "query"},
        {"op": "add_edge", "edge_type": "control", "source_id": "index-documents",
         "source_port": "result", "target_id": _slugify(rag_name), "target_port": "query"},
    ]


def _pattern_data_analysis(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Data file -> read -> preprocess (code) -> LLM summary for methods/results."""
    input_var = params.get("input_var", "data_path")
    return [
        {"op": "add_node", "node_type": "input", "name": "Data Input",
         "config": {"variables": [{"name": input_var, "type": "string", "default": "", "description": "Path to data file(s)"}]}},
        {"op": "add_node", "node_type": "tool_operator", "name": "Read Data",
         "config": {"tool_id": "file_read"}},
        {"op": "add_node", "node_type": "code_operator", "name": "Preprocess Data",
         "config": {"code": "import json\ntry:\n    data = json.loads(input) if isinstance(input, str) else input\nexcept Exception:\n    data = input\nresult = {'summary': str(data)[:2000], 'raw': input}", "language": "python"}},
        {"op": "add_node", "node_type": "llm_operator", "name": "Data Summary",
         "config": {"prompt_template": "Analyze the following dataset and produce a structured summary suitable for the Methods and Results sections of an academic paper.\n\nData:\n{input}\n\nProvide: (1) descriptive statistics, (2) key variables, (3) notable patterns, (4) suggested analyses.", "system_prompt": "You are a quantitative research methods expert."}},
        {"op": "add_edge", "source_id": "data-input", "source_port": input_var,
         "target_id": "read-data", "target_port": "path"},
        {"op": "add_edge", "source_id": "read-data", "source_port": "result",
         "target_id": "preprocess-data", "target_port": "input"},
        {"op": "add_edge", "source_id": "preprocess-data", "source_port": "result",
         "target_id": "data-summary", "target_port": "input"},
    ]


PATTERN_LIBRARY: dict[str, Any] = {
    "chain": _pattern_chain,
    "review_loop": _pattern_review_loop,
    "fan_out": _pattern_fan_out,
    "rag_qa": _pattern_rag_qa,
    "data_ingest": _pattern_data_ingest,
    "data_analysis": _pattern_data_analysis,
}
