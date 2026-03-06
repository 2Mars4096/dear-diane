"""Graph-aware chat manager — LLM conversations with workflow context."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field

try:
    import tiktoken
    _tiktoken_available = True
except ImportError:
    _tiktoken_available = False

from dan.models.graph import Graph
from dan.providers import CompletionResult, StreamChunk
from dan.providers.registry import ProviderRegistry
from dan.server.graph_mutator import (
    GraphMutator,
    MutationPlan,
    PATTERN_LIBRARY,
    _default_node_config,
    _default_ports,
)
from dan.server.graph_store import GraphStore
from dan.server.mutation_metrics import mutation_metrics

_MUTATION_AUTO_RETRY = os.environ.get("DAN_MUTATION_AUTO_RETRY", "true").lower() == "true"
try:
    _MUTATION_AUTO_RETRY_MAX = max(0, int(os.environ.get("DAN_MUTATION_AUTO_RETRY_MAX", "2")))
except ValueError:
    _MUTATION_AUTO_RETRY_MAX = 2
_MAX_CONTEXT_RATIO = float(os.environ.get("DAN_CHAT_MAX_CONTEXT_RATIO", "0.8"))
_RECENT_MESSAGES_COUNT = int(os.environ.get("DAN_CHAT_RECENT_MESSAGES", "10"))

__all__ = [
    "NodeSummary",
    "EdgeSummary",
    "GraphSummary",
    "ChatTokenEvent",
    "ChatCompleteEvent",
    "ChatErrorEvent",
    "ChatMutationEvent",
    "ChatInterruptedEvent",
    "ChatToolCallStartEvent",
    "ChatToolCallResultEvent",
    "ChatStreamEvent",
    "MUTATION_TOOL_SCHEMA",
    "ChatManager",
    "build_graph_summary",
    "serialize_for_prompt",
    "compute_graph_revision",
    "BUILD_FROM_INTENT_PROMPT",
    "ASK_PROMPT",
    "PLAN_PROMPT",
    "DEBUG_PROMPT",
    "EMPTY_GRAPH_SUMMARY_PLACEHOLDER",
    "WORKFLOW_TEMPLATES",
    "normalize_chat_mode",
    "build_debug_context",
    "_coerce_strict_edges",
    "_normalize_generated_mutation_ops",
    "estimate_tokens",
    "compact_history",
    "MODEL_CONTEXT_WINDOWS",
]

logger = logging.getLogger(__name__)

NODE_TYPES: list[str] = [
    "llm_operator",
    "tool_operator",
    "code_operator",
    "rag_operator",
    "input",
    "gate",
    "for_each",
    "parallel_subagents",
    "orchestrator",
    "reduce",
    "router",
    "human_in_the_loop",
    "validator",
    "composite",
]

EDGE_TYPES: list[str] = ["data", "control", "context"]

def _build_mutation_tool_schema() -> dict[str, Any]:
    """Generate the mutation tool schema from source-of-truth tables.

    Uses ``oneOf`` discriminated by ``op`` so each operation carries only the
    fields it needs, with correct ``required`` constraints.
    NOTE: some LLM providers don't handle ``oneOf`` well.  If needed, the
    flat-schema approach (single object with all fields, only ``op`` required)
    can be substituted here as a fallback.
    """
    add_node_schema = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "const": "add_node"},
            "id": {"type": "string", "description": "Stable node ID used in edges and set_position (e.g. 'node_1'). If omitted, auto-generated from name."},
            "node_type": {"type": "string", "enum": NODE_TYPES},
            "name": {"type": "string", "description": "Human-readable node name"},
            "config": {"type": "object", "description": "Node-type-specific configuration"},
        },
        "required": ["op", "node_type", "name"],
    }

    remove_node_schema = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "const": "remove_node"},
            "node_id": {"type": "string", "description": "ID of the node to remove"},
        },
        "required": ["op", "node_id"],
    }

    edit_node_schema = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "const": "edit_node"},
            "node_id": {"type": "string"},
            "updates": {"type": "object", "description": "Fields to merge into the node"},
        },
        "required": ["op", "node_id", "updates"],
    }

    add_edge_schema = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "const": "add_edge"},
            "edge_type": {
                "type": "string",
                "enum": ["data", "control", "context"],
                "default": "data",
            },
            "source_id": {"type": "string"},
            "source_port": {"type": "string"},
            "target_id": {"type": "string"},
            "target_port": {"type": "string"},
            "spread": {
                "type": "boolean",
                "default": False,
                "description": "If true, the source dict is destructured and its keys are spread into the target node's matching input ports",
            },
            "strict": {
                "type": "boolean",
                "default": False,
                "description": "If true, fail when target_port does not exist instead of auto-creating. Use for programmatic use to catch typos.",
            },
        },
        "required": ["op", "source_id", "source_port", "target_id", "target_port"],
    }

    remove_edge_schema = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "const": "remove_edge"},
            "source_id": {"type": "string"},
            "source_port": {"type": "string"},
            "target_id": {"type": "string"},
            "target_port": {"type": "string"},
        },
        "required": ["op", "source_id", "source_port", "target_id", "target_port"],
    }

    set_position_schema = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "const": "set_position"},
            "node_id": {"type": "string"},
            "x": {"type": "number"},
            "y": {"type": "number"},
        },
        "required": ["op", "node_id", "x", "y"],
    }

    expand_pattern_schema = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "const": "expand_pattern"},
            "pattern": {
                "type": "string",
                "enum": ["chain", "review_loop", "fan_out", "rag_qa", "data_ingest", "data_analysis"],
                "description": "Named pattern to expand into nodes and edges",
            },
            "params": {
                "type": "object",
                "description": "Pattern-specific parameters (e.g., count, names, prompts)",
            },
        },
        "required": ["op", "pattern"],
    }

    apply_skill_schema = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "const": "apply_skill"},
            "skill": {
                "type": "string",
                "enum": ["management_science_writing", "informs_latex_style"],
                "description": "Name of the skill to apply (domain-specific prompt injection)",
            },
            "target_nodes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of node IDs to apply the skill to",
                "default": [],
            },
            "target_tag": {
                "type": "string",
                "description": "Apply to all nodes with this tag in metadata.tags",
                "default": "",
            },
        },
        "required": ["op", "skill"],
    }

    return {
        "type": "function",
        "function": {
            "name": "plan_graph_mutations",
            "description": (
                "Plan a sequence of graph operations to modify the workflow. "
                "Use this when the user asks to add, remove, or modify nodes or edges."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Brief description of the changes",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Step-by-step reasoning for why these operations are needed",
                    },
                    "operations": {
                        "type": "array",
                        "items": {
                            "oneOf": [
                                add_node_schema,
                                remove_node_schema,
                                edit_node_schema,
                                add_edge_schema,
                                remove_edge_schema,
                                set_position_schema,
                                expand_pattern_schema,
                                apply_skill_schema,
                            ],
                        },
                    },
                },
                "required": ["description", "operations"],
            },
        },
    }


MUTATION_TOOL_SCHEMA: dict[str, Any] = _build_mutation_tool_schema()

def _build_node_type_reference() -> str:
    """Build a concise per-type reference for the system prompt."""
    lines = []
    for nt in NODE_TYPES:
        inp, out = _default_ports(nt)
        cfg = _default_node_config(nt)
        in_names = [p["name"] for p in inp]
        out_names = [p["name"] for p in out]
        cfg_keys = sorted(cfg.keys()) if cfg else []
        if nt == "gate":
            inp_w, out_w = _default_ports("gate", {"gate_mode": "while"})
            out_w_names = [p["name"] for p in out_w]
            line = (
                f"- gate (if_else mode): in=[{', '.join(in_names)}] out=[{', '.join(out_names)}]"
                f" config={{{', '.join(cfg_keys)}}}\n"
                f"- gate (while mode): in=[{', '.join(in_names)}] out=[{', '.join(out_w_names)}]"
                f" config={{{', '.join(cfg_keys)}}}"
            )
        else:
            line = f"- {nt}: in=[{', '.join(in_names)}] out=[{', '.join(out_names)}]"
            if cfg_keys:
                line += f" config={{{', '.join(cfg_keys)}}}"
        lines.append(line)
    return "\n".join(lines)


NODE_TYPE_REFERENCE: str = _build_node_type_reference()

SYSTEM_PROMPT_TEMPLATE = """\
You are a graph-aware assistant for DAN (Deep Agent Network), an agentic \
workflow builder. You help the user understand, debug, and improve their \
workflow graphs.

## Available node types (with default ports and config)
{node_type_reference}

## Available edge types
- data: carries structured data between ports
- control: encodes routing / flow-control signals (branching, looping)
- context: connects a node to a shared-context key (read/write/append)

## Operation examples

Add an LLM node:
{{"op": "add_node", "node_type": "llm_operator", "name": "Summarizer", "config": {{"prompt_template": "Summarize: {{input}}"}}}}

Wire two nodes:
{{"op": "add_edge", "source_id": "writer", "source_port": "text", "target_id": "reviewer", "target_port": "input"}}

Create a while-loop gate (output ports: continue, done):
{{"op": "add_node", "node_type": "gate", "name": "Review Gate", "config": {{"gate_mode": "while", "condition": "needs_revision == True", "max_iterations": 5}}}}

## Available patterns (use expand_pattern op)
- chain: Sequential chain of N LLM nodes (params: count, names, prompts)
- review_loop: Writer → Reviewer → Gate with back-edge (params: writer_name, reviewer_name, condition, max_iterations)
- fan_out: Source → ForEach → body processor (params: source_name, body_name, parallelism)
- rag_qa: RAG retrieval → LLM answer (params: rag_name, collection, top_k, answer_prompt)
- data_ingest: PDF directory → index into RAG → retrieval-ready (params: input_var, collection, rag_name, top_k)
- data_analysis: Data file → read → preprocess → LLM summary (params: input_var)

## Available skills (use apply_skill op)
- management_science_writing: INFORMS MS writing conventions (target_tag: "writing")
- informs_latex_style: INFORMS LaTeX formatting (target_tag: "latex")

## Current workflow
{graph_summary}

## Guidelines
- Answer questions about the current workflow — explain what nodes do, \
how data flows, potential issues.
- When the user asks to modify the graph (add, remove, change nodes/edges), \
use the plan_graph_mutations tool with a precise list of operations. \
For questions and explanations, respond in plain text without using the tool.
- If the workflow is empty and the user asks to create one, use the \
plan_graph_mutations tool to build it from scratch.
- Always use exact port names from the reference above. Do not guess.
- Be concise. Use the node/edge vocabulary above.
"""

# Build-from-intent mode: intent-first workflow creation (no existing graph context)
BUILD_FROM_INTENT_PROMPT = """\
You are a workflow architect for DAN (Deep Agent Network). The user wants to \
create a new workflow from scratch. Your job is to decompose their intent into \
tasks, stages, node types, and data flow, then produce a mutation plan.

## Task decomposition
1. Identify the high-level goal (e.g. "paper writing", "RAG QA", "multi-step analysis")
2. Break into stages: input → processing → output; add review/iteration loops if needed
3. Map stages to node types: llm_operator, rag_operator, gate, for_each, etc.
4. Define data flow: which ports connect (input → text, text → input, etc.)

## Available node types (with default ports and config)
{node_type_reference}

## Available edge types
- data: carries structured data between ports
- control: routing / flow-control (branching, looping)
- context: shared-context key (read/write/append)

## Pattern library (use expand_pattern op)
- chain: Sequential N LLM nodes (params: count, names, prompts)
- review_loop: Writer → Reviewer → Gate with back-edge (params: writer_name, reviewer_name, condition, max_iterations)
- fan_out: Source → ForEach → body processor (params: source_name, body_name, parallelism)
- rag_qa: RAG retrieval → LLM answer (params: rag_name, collection, top_k, answer_prompt)
- data_ingest: PDF directory → list → index into RAG → retrieval-ready (params: input_var, collection, rag_name, top_k)
- data_analysis: Data file → read → preprocess → LLM summary for methods/results (params: input_var)

## Intent → pattern mapping
- Paper writing / document drafting: use data_ingest + review_loop + chain (literature + outline → draft → review → compile)
- Paper writing with data: use data_ingest + data_analysis + review_loop (literature + data analysis → methods/results → review → compile)
- RAG QA / knowledge retrieval: use rag_qa or data_ingest pattern
- Multi-step analysis / summarization: use chain with count and prompts
- Parallel processing over items: use fan_out
- "I have PDFs at path X": use data_ingest pattern with input_var matching path variable
- "I have data at path Y": use data_analysis pattern with input_var matching path variable

## Available templates
Pre-built workflow templates (use expand_pattern with template operations):
- informs_paper_writing: Full INFORMS paper pipeline — data_ingest + data_analysis + outline + section drafting + review loop + LaTeX compile + package
- rag_research: data_ingest → RAG retrieval → LLM synthesis (for literature review / understanding papers)
- paper_writing: review_loop + chain (simple paper drafting without data/PDF ingestion)
- rag_qa: RAG retrieval → LLM answer
- chain_3: simple 3-node sequential chain

## Available skills (use apply_skill op)
- management_science_writing: INFORMS Management Science submission guidelines and writing conventions. Apply to nodes tagged "writing" or "review".
- informs_latex_style: INFORMS LaTeX formatting conventions. Apply to nodes tagged "latex".

When the user mentions a specific journal (e.g., "Management Science", "INFORMS"), apply the corresponding skill after building the workflow.

## File path handling
- When the user says "data at path X" or "PDFs at Y", create an InputNode with a variable for that path.
- Wire the InputNode to data_ingest (for PDFs) or data_analysis (for data files).
- All file paths are relative to the workspace root. If the user provides an absolute path outside the workspace, ask them to copy/symlink files into the workspace first.

## Current state
{graph_summary}

## Guidelines
- Use plan_graph_mutations to produce a complete workflow. Target the empty graph.
- When intent is ambiguous, propose sensible defaults (e.g. paper sections: intro, methods, results, discussion).
- Prefer expand_pattern for known shapes; use add_node/add_edge for custom flows.
- Use strict=true in add_edge operations when building from intent (fail fast on typos).
- Always use exact port names from the reference. Do not guess.
- When building a paper-writing workflow, include the full pipeline to LaTeX compilation (use compile_latex, save_paper, package_submission tools).
- Apply domain skills (apply_skill op) when the user mentions a specific journal or academic domain.
- Be concise. Produce a runnable workflow in one plan.
"""

# Placeholder for build-from-intent mode (no graph context)
EMPTY_GRAPH_SUMMARY_PLACEHOLDER = (
    "Workflow is empty (0 nodes, 0 edges). Create from scratch using plan_graph_mutations."
)

# ---------------------------------------------------------------------------
# Mode-specific prompt templates
# ---------------------------------------------------------------------------

CHAT_MODE_ALIASES: dict[str, str] = {"build": "agent", "mutate": "agent"}


def normalize_chat_mode(mode: str) -> str:
    """Normalize deprecated mode aliases to canonical mode names.

    ``"auto"`` is passed through as-is (the caller resolves it via
    ``detect_chat_mode``).
    """
    if mode == "auto":
        return "auto"
    return CHAT_MODE_ALIASES.get(mode, mode)


def detect_chat_mode(
    message: str,
    graph_state: dict | None = None,
    recent_run_failed: bool = False,
) -> str:
    """Heuristic mode detection from message content.

    Priority order: debug > ask > plan > agent (default).
    """
    msg_lower = message.lower().strip()
    words = set(msg_lower.split())

    is_question = msg_lower.endswith("?") or any(
        msg_lower.startswith(q) for q in (
            "what", "how", "why", "where", "when", "which",
            "explain", "describe", "tell me about",
        )
    )

    debug_word_patterns = {"bug", "broken"}
    debug_stem_patterns = ("crash", "fail")
    debug_phrase_patterns = ["not working", "wrong output"]
    debug_via_word = bool(words & debug_word_patterns)
    debug_via_stem = any(
        any(w.startswith(s) for w in words) for s in debug_stem_patterns
    )
    debug_via_phrase = any(p in msg_lower for p in debug_phrase_patterns)
    debug_via_context = "error" in words and not is_question
    debug_via_fix = "fix" in words and not is_question
    if recent_run_failed or debug_via_word or debug_via_stem or debug_via_phrase or debug_via_context or debug_via_fix:
        return "debug"

    if is_question:
        return "ask"

    plan_patterns = ["approach", "strategy", "architect", "propose", "how should"]
    plan_word_patterns = {"plan", "design"}
    if any(p in msg_lower for p in plan_patterns) or bool(words & plan_word_patterns):
        return "plan"

    return "agent"


ASK_PROMPT = """\
You are a graph-aware assistant for DAN (Deep Agent Network). The user is \
asking questions about their workflow — answer clearly and concisely.

## Your role
- Explain the current graph: describe topology, node connections, data flow.
- Answer "what does X do?", "how does data flow from A to B?", \
"what inputs does this need?"
- Summarize the workflow purpose, entry/exit points, and processing stages.
- Do NOT suggest or make any modifications. You are read-only.

## Available node types
{node_type_reference}

## Available edge types
- data: carries structured data between ports
- control: routing / flow-control (branching, looping)
- context: shared-context key (read/write/append)

## Current workflow
{graph_summary}

## Guidelines
- Be concise and precise. Use node IDs and port names when referencing \
the graph.
- If the workflow is empty, say so and suggest the user switch to Agent mode \
to build one.
- Do not use any tools. Respond in plain text only.
"""

PLAN_PROMPT = """\
You are a planning assistant for DAN (Deep Agent Network). The user wants to \
modify their workflow, and you will help them plan the approach first.

## Your role (two-step flow)
**Step 1 — Plan proposal (this step):**
- Analyze the user's request and the current workflow.
- Propose a step-by-step approach in natural language.
- Explain what nodes/edges will be added, removed, or modified and why.
- Discuss trade-offs or alternatives if relevant.
- Do NOT call any tools or generate mutation plans yet.
- End with: "Would you like me to proceed with this plan?"

**Step 2 — Execution (after user approval):**
- When the user confirms, generate the mutation plan using \
plan_graph_mutations.
- Follow the approved plan faithfully.

## Available node types
{node_type_reference}

## Available edge types
- data: carries structured data between ports
- control: routing / flow-control (branching, looping)
- context: shared-context key (read/write/append)

## Available patterns (use expand_pattern op)
- chain, review_loop, fan_out, rag_qa, data_ingest, data_analysis

## Current workflow
{graph_summary}

## Guidelines
- In Step 1, respond ONLY with a natural-language plan. No tool calls.
- Be specific: name the nodes, ports, and edge types you intend to use.
- After the user approves, proceed to generate mutations.
"""

DEBUG_PROMPT = """\
You are a debugging assistant for DAN (Deep Agent Network). The user needs \
help diagnosing and fixing issues with their workflow.

## Your role
- Analyze run failures: identify root causes from error messages and \
node outputs.
- Explain what went wrong in accessible terms.
- Suggest specific fixes (node config changes, edge rewiring, missing inputs).
- When suggesting fixes, use the plan_graph_mutations tool.

## Available node types
{node_type_reference}

## Available edge types
- data: carries structured data between ports
- control: routing / flow-control (branching, looping)
- context: shared-context key (read/write/append)

## Current workflow
{graph_summary}

## Recent run failures
{debug_context}

## Guidelines
- Start by diagnosing the error before proposing fixes.
- If no recent failures exist, ask the user to describe the issue or run \
the workflow first.
- Suggest targeted fixes — prefer minimal changes over rebuilding.
- Use plan_graph_mutations when you have a concrete fix to propose.
"""


def build_debug_context(runs: list[dict[str, Any]], workflow_id: str) -> str:
    """Build debug context string from run records for a workflow."""
    failed = [
        r for r in runs
        if r.get("graph_id") == workflow_id and r.get("status") == "failed"
    ]
    if not failed:
        return "No recent run failures found for this workflow."

    latest = failed[-1]
    parts = [f"Last failed run: {latest.get('run_id', 'unknown')}"]

    errors = latest.get("errors", {})
    if errors:
        parts.append("Errors:")
        for key, val in errors.items():
            parts.append(f"  - {key}: {val}")

    events = latest.get("events", [])
    error_events = [
        e for e in events
        if isinstance(e, dict) and "error" in str(e.get("type", "")).lower()
    ]
    if error_events:
        parts.append("Error events (most recent):")
        for ev in error_events[-5:]:
            parts.append(f"  - {json.dumps(ev, default=str)[:500]}")

    outputs = latest.get("outputs", {})
    if outputs:
        parts.append("Run outputs:")
        for key, val in outputs.items():
            parts.append(f"  - {key}: {str(val)[:200]}")

    return "\n".join(parts)

# ---------------------------------------------------------------------------
# Workflow templates — pre-built mutation operation sequences
# ---------------------------------------------------------------------------

WORKFLOW_TEMPLATES: dict[str, list[dict]] = {
    "informs_paper_writing": [
        {"op": "expand_pattern", "pattern": "data_ingest", "params": {
            "input_var": "pdf_dir", "collection": "literature", "rag_name": "Literature KB", "top_k": 10,
        }},
        {"op": "expand_pattern", "pattern": "data_analysis", "params": {
            "input_var": "data_path",
        }},
        {"op": "add_node", "node_type": "input", "name": "Paper Config",
         "config": {"variables": [
             {"name": "title", "type": "string", "default": "paper", "description": "Paper title slug used for output filenames"},
         ]}},
        {"op": "add_node", "node_type": "llm_operator", "name": "Outline Planner",
         "config": {"prompt_template": "Based on the literature and data analysis below, create a detailed outline for a Management Science paper.\n\nLiterature context: {input}\nData summary: {context}\n\nInclude: Introduction, Literature Review, Model/Framework, Data & Methods, Results, Discussion, Conclusion.",
                    "system_prompt": "",
                    "metadata": {"tags": ["writing"]},
                    "input_ports": [
                        {"name": "input", "schema": {}, "required": False},
                        {"name": "context", "schema": {}, "required": False},
                    ]}},
        {"op": "add_node", "node_type": "human_in_the_loop", "name": "Research Interview",
         "config": {"prompt": "Review the proposed outline and provide feedback on research positioning, methodology choices, and contribution framing. You can modify the outline or approve it."}},
        {"op": "expand_pattern", "pattern": "review_loop", "params": {
            "writer_name": "Section Drafter",
            "reviewer_name": "Academic Reviewer",
            "condition": "needs_revision == True",
            "max_iterations": 3,
            "writer_prompt": "Write the next section of the paper following the outline. Use evidence from the literature and data analysis.\n\nOutline: {input}",
            "reviewer_prompt": "Review this draft section for a Management Science submission. Check: (1) contribution clarity, (2) methodological rigor, (3) evidence quality, (4) writing quality. Output JSON with 'needs_revision' (bool) and 'feedback' (str).",
        }},
        {"op": "add_node", "node_type": "llm_operator", "name": "LaTeX Assembler",
         "config": {"prompt_template": "Assemble the reviewed sections into a complete LaTeX manuscript using INFORMS formatting.\n\nSections: {input}",
                    "system_prompt": "", "metadata": {"tags": ["writing", "latex"]}}},
        {"op": "add_node", "node_type": "tool_operator", "name": "Check LaTeX Deps",
         "config": {"tool_id": "check_latex_deps"}},
        {"op": "add_node", "node_type": "tool_operator", "name": "Verify Citations",
         "config": {"tool_id": "citation_verifier"}},
        {"op": "add_node", "node_type": "tool_operator", "name": "Compile LaTeX",
         "config": {"tool_id": "compile_latex"}},
        {"op": "add_node", "node_type": "tool_operator", "name": "Save Paper",
         "config": {"tool_id": "save_paper"}},
        {"op": "add_node", "node_type": "tool_operator", "name": "Package Submission",
         "config": {"tool_id": "package_submission"}},
        # --- Inter-pattern: literature + data → planner ---
        {"op": "add_edge", "source_id": "literature-kb", "source_port": "chunks",
         "target_id": "outline-planner", "target_port": "input"},
        {"op": "add_edge", "source_id": "data-summary", "source_port": "text",
         "target_id": "outline-planner", "target_port": "context"},
        # --- Planner → human review → writing loop ---
        {"op": "add_edge", "source_id": "outline-planner", "source_port": "text",
         "target_id": "research-interview", "target_port": "input"},
        {"op": "add_edge", "source_id": "research-interview", "source_port": "response",
         "target_id": "section-drafter", "target_port": "input"},
        {"op": "add_edge", "source_id": "review-gate", "source_port": "done",
         "target_id": "latex-assembler", "target_port": "input"},
        # --- LaTeX validation fan-out (informational, parallel) ---
        {"op": "add_edge", "source_id": "latex-assembler", "source_port": "text",
         "target_id": "check-latex-deps", "target_port": "input"},
        {"op": "add_edge", "source_id": "latex-assembler", "source_port": "text",
         "target_id": "verify-citations", "target_port": "input"},
        # --- Compile LaTeX (content + title) ---
        {"op": "add_edge", "source_id": "latex-assembler", "source_port": "text",
         "target_id": "compile-latex", "target_port": "content"},
        {"op": "add_edge", "source_id": "paper-config", "source_port": "title",
         "target_id": "compile-latex", "target_port": "title"},
        # --- Save Paper (content + title + compiled PDF) ---
        {"op": "add_edge", "source_id": "latex-assembler", "source_port": "text",
         "target_id": "save-paper", "target_port": "content"},
        {"op": "add_edge", "source_id": "paper-config", "source_port": "title",
         "target_id": "save-paper", "target_port": "title"},
        {"op": "add_edge", "source_id": "compile-latex", "source_port": "pdf_path",
         "target_id": "save-paper", "target_port": "pdf_path"},
        # --- Package Submission (structured ports from save-paper) ---
        {"op": "add_edge", "source_id": "save-paper", "source_port": "title",
         "target_id": "package-submission", "target_port": "title"},
        {"op": "add_edge", "source_id": "save-paper", "source_port": "tex_path",
         "target_id": "package-submission", "target_port": "tex_path"},
        {"op": "add_edge", "source_id": "save-paper", "source_port": "bib_path",
         "target_id": "package-submission", "target_port": "bib_path"},
        # --- Skill injection ---
        {"op": "apply_skill", "skill": "management_science_writing", "target_tag": "writing"},
        {"op": "apply_skill", "skill": "informs_latex_style", "target_tag": "latex"},
    ],
    "rag_research": [
        {"op": "expand_pattern", "pattern": "data_ingest", "params": {
            "input_var": "pdf_dir", "collection": "research", "rag_name": "Research KB",
        }},
        {"op": "add_node", "node_type": "llm_operator", "name": "Research Synthesizer",
         "config": {"prompt_template": "Based on the retrieved literature chunks, provide a comprehensive synthesis addressing the research question.\n\nContext: {input}",
                    "system_prompt": "You are an academic research assistant. Synthesize information from multiple sources, identify key themes, contradictions, and gaps in the literature."}},
        {"op": "add_edge", "source_id": "research-kb", "source_port": "chunks",
         "target_id": "research-synthesizer", "target_port": "input"},
    ],
    "paper_writing": [
        {"op": "expand_pattern", "pattern": "review_loop", "params": {
            "writer_name": "Drafter",
            "reviewer_name": "Reviewer",
            "condition": "needs_revision == True",
            "max_iterations": 3,
        }},
        {"op": "expand_pattern", "pattern": "chain", "params": {
            "count": 3,
            "names": ["Outline", "Draft", "Final Polish"],
            "prompts": [
                "Create a structured outline for: {input}",
                "Write a full draft based on: {input}",
                "Polish and finalize the document: {input}",
            ],
        }},
    ],
    "rag_qa": [
        {"op": "expand_pattern", "pattern": "rag_qa", "params": {
            "rag_name": "Knowledge Base",
            "answer_name": "Answer Generator",
            "top_k": 5,
            "answer_prompt": "Answer the question based on the retrieved context:\n{input}",
        }},
    ],
    "chain_3": [
        {"op": "expand_pattern", "pattern": "chain", "params": {
            "count": 3,
            "names": ["Step 1", "Step 2", "Step 3"],
        }},
    ],
}


# ---------------------------------------------------------------------------
# Token counting & context window management
# ---------------------------------------------------------------------------

MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o1-preview": 128_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    "o4-mini": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-haiku": 200_000,
    "gemini-1.5-pro": 1_000_000,
    "gemini-1.5-flash": 1_000_000,
    "gemini-2.0-flash": 1_000_000,
    "deepseek-chat": 64_000,
    "deepseek-reasoner": 64_000,
}

_DEFAULT_CONTEXT_WINDOW = 128_000


def _get_context_window(model: str) -> int:
    """Look up the context window for a model, with prefix fallback."""
    if model in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[model]
    for key, window in MODEL_CONTEXT_WINDOWS.items():
        if model.startswith(key):
            return window
    return _DEFAULT_CONTEXT_WINDOW


def estimate_tokens(text: str, model: str = "") -> int:
    """Estimate token count. Uses tiktoken when available, character approximation otherwise."""
    if _tiktoken_available:
        try:
            enc = tiktoken.encoding_for_model(model)
        except KeyError:
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    return len(text) // 4


def _estimate_messages_tokens(messages: list[dict[str, str]], model: str = "") -> int:
    """Estimate total tokens for a list of messages (includes per-message overhead)."""
    total = 0
    for msg in messages:
        total += 4 + estimate_tokens(msg.get("content", ""), model)
    return total


_TRUNCATE_RE = re.compile(r"(?<=[.!?])\s+")


def _truncate_assistant_message(text: str) -> str:
    """Extractive truncation: keep first and last sentence."""
    sentences = _TRUNCATE_RE.split(text.strip())
    if len(sentences) <= 3:
        return text
    return f"{sentences[0]} [...] {sentences[-1]}"


def compact_history(
    messages: list[dict[str, str]],
    max_tokens: int,
    model: str = "",
    recent_count: int | None = None,
) -> list[dict[str, str]]:
    """Compact message history to fit within token budget.

    Priority (highest first):
    1. System prompt — always kept in full.
    2. Most recent ``recent_count`` messages — always kept in full.
    3. Older assistant messages — truncated to first + last sentence.
    4. Oldest messages — dropped entirely if still over budget.
    """
    if not messages:
        return messages

    if recent_count is None:
        recent_count = _RECENT_MESSAGES_COUNT

    total = _estimate_messages_tokens(messages, model)
    if total <= max_tokens:
        return messages

    system = [m for m in messages if m.get("role") == "system"]
    conv = [m for m in messages if m.get("role") != "system"]

    system_tokens = _estimate_messages_tokens(system, model)
    budget = max_tokens - system_tokens

    if budget <= 0:
        return system + conv[-1:] if conv else system

    if len(conv) > recent_count:
        recent = conv[-recent_count:]
        older = conv[:-recent_count]
    else:
        recent = conv
        older = []

    recent_tokens = _estimate_messages_tokens(recent, model)

    if recent_tokens > budget:
        result = list(recent)
        while len(result) > 1 and _estimate_messages_tokens(result, model) > budget:
            result.pop(0)
        logger.info(
            "Compacted history: kept %d of %d messages (recent-only mode)",
            len(system) + len(result), len(messages),
        )
        return system + result

    older_budget = budget - recent_tokens

    if not older:
        return system + recent

    # Phase 3: truncate older assistant messages
    truncated: list[dict[str, str]] = []
    for m in older:
        content = m.get("content", "")
        if m.get("role") == "assistant" and len(content) > 200:
            truncated.append({**m, "content": _truncate_assistant_message(content)})
        else:
            truncated.append(m)

    older_tokens = _estimate_messages_tokens(truncated, model)
    if older_tokens <= older_budget:
        logger.info(
            "Compacted history: truncated %d older assistant messages",
            sum(1 for o, t in zip(older, truncated) if o is not t),
        )
        return system + truncated + recent

    # Phase 4: drop oldest until we fit
    while truncated and _estimate_messages_tokens(truncated, model) > older_budget:
        truncated.pop(0)

    total_kept = len(system) + len(truncated) + len(recent)
    logger.info(
        "Compacted history: %d → %d messages (dropped %d oldest)",
        len(messages), total_kept, len(messages) - total_kept,
    )
    return system + truncated + recent


# ---------------------------------------------------------------------------
# Graph summary models
# ---------------------------------------------------------------------------


class NodeSummary(BaseModel):
    id: str
    name: str
    node_type: str
    description: str = ""
    input_ports: list[str] = Field(default_factory=list)
    output_ports: list[str] = Field(default_factory=list)
    model: str | None = None
    prompt_snippet: str | None = None


class EdgeSummary(BaseModel):
    edge_type: str
    source_node_id: str
    source_port: str
    target_node_id: str
    target_port: str


class GraphSummary(BaseModel):
    workflow_id: str
    name: str
    description: str
    node_count: int
    edge_count: int
    nodes: list[NodeSummary] = Field(default_factory=list)
    edges: list[EdgeSummary] = Field(default_factory=list)
    entry_points: list[str] = Field(default_factory=list)
    exit_points: list[str] = Field(default_factory=list)
    revision: str


# ---------------------------------------------------------------------------
# Chat stream events
# ---------------------------------------------------------------------------


class ChatTokenEvent(BaseModel):
    type: str = "chat_token"
    delta: str
    accumulated: str


class ChatCompleteEvent(BaseModel):
    type: str = "chat_complete"
    message_id: str
    content: str
    token_usage: dict[str, int] = Field(default_factory=dict)
    context_window: int = 0
    graph_revision: str
    revision_mismatch: bool = False
    detected_mode: str | None = None


class ChatErrorEvent(BaseModel):
    type: str = "chat_error"
    error: str


class ChatMutationEvent(BaseModel):
    type: str = "chat_mutation"
    message_id: str
    content: str
    mutation_plan: dict[str, Any]
    dry_run_result: dict[str, Any]
    token_usage: dict[str, int] = Field(default_factory=dict)
    context_window: int = 0
    graph_revision: str
    revision_mismatch: bool = False
    detected_mode: str | None = None


class ChatInterruptedEvent(BaseModel):
    type: str = "chat_interrupted"
    message_id: str
    content: str
    token_usage: dict[str, int] = Field(default_factory=dict)


class ChatToolCallStartEvent(BaseModel):
    type: str = "chat_tool_call_start"
    tool_call_id: str
    tool_name: str
    args_preview: str


class ChatToolCallResultEvent(BaseModel):
    type: str = "chat_tool_call_result"
    tool_call_id: str
    tool_name: str
    status: str  # "success" | "error"
    output_preview: str
    duration_ms: int


ChatStreamEvent = (
    ChatTokenEvent
    | ChatCompleteEvent
    | ChatErrorEvent
    | ChatMutationEvent
    | ChatInterruptedEvent
    | ChatToolCallStartEvent
    | ChatToolCallResultEvent
)


# ---------------------------------------------------------------------------
# Graph revision hash
# ---------------------------------------------------------------------------


def compute_graph_revision(graph_dict: dict) -> str:
    """Stable SHA-256 hash of the graph for concurrency checks.

    Normalizes through the Pydantic ``Graph`` model so that missing default
    fields (``version``, ``sub_graphs``, ``entry_points``, …) do not cause
    a revision mismatch between callers that round-trip through Pydantic and
    those that hash the raw dict read from disk.
    """
    try:
        normalized = json.loads(Graph.model_validate(graph_dict).model_dump_json())
    except Exception:
        # Fallback for partially-formed dicts used in tests or diagnostics.
        # Runtime graph-store payloads should validate and use normalized hashing.
        normalized = graph_dict
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Graph summary builder
# ---------------------------------------------------------------------------


def build_graph_summary(graph: Graph, workflow_id: str) -> GraphSummary:
    """Build a GraphSummary from a Graph. Handles empty graph (nodes=[], edges=[]).

    For empty graph, returns valid GraphSummary with node_count=0, edge_count=0,
    and revision from hash of the canonical empty structure.
    """
    nodes: list[NodeSummary] = []
    for n in graph.nodes:
        model_val: str | None = getattr(n, "model", None)
        snippet: str | None = None
        prompt_template = getattr(n, "prompt_template", None)
        if prompt_template:
            snippet = prompt_template[:200]

        nodes.append(NodeSummary(
            id=n.id,
            name=n.name,
            node_type=n.node_type,
            description=n.description,
            input_ports=[p.name for p in n.input_ports],
            output_ports=[p.name for p in n.output_ports],
            model=model_val,
            prompt_snippet=snippet,
        ))

    edges = [
        EdgeSummary(
            edge_type=e.edge_type,
            source_node_id=e.source_node_id,
            source_port=e.source_port,
            target_node_id=e.target_node_id,
            target_port=e.target_port,
        )
        for e in graph.edges
    ]

    graph_dict = json.loads(graph.model_dump_json())
    revision = compute_graph_revision(graph_dict)

    return GraphSummary(
        workflow_id=workflow_id,
        name=graph.metadata.name,
        description=graph.metadata.description,
        node_count=len(graph.nodes),
        edge_count=len(graph.edges),
        nodes=nodes,
        edges=edges,
        entry_points=graph.entry_points,
        exit_points=graph.exit_points,
        revision=revision,
    )


# ---------------------------------------------------------------------------
# Prompt serialisation
# ---------------------------------------------------------------------------


def _format_node_line(n: NodeSummary) -> str:
    parts = [f"  - {n.id} [{n.node_type}]"]
    if n.model:
        parts.append(f"model={n.model}")
    if n.input_ports:
        parts.append(f"in: {', '.join(n.input_ports)}")
    if n.output_ports:
        parts.append(f"out: {', '.join(n.output_ports)}")
    return " | ".join(parts)


def serialize_for_prompt(summary: GraphSummary, max_tokens: int = 4000) -> str:
    max_chars = max_tokens * 4

    header = (
        f'Workflow: "{summary.name}" ({summary.node_count} nodes, '
        f"{summary.edge_count} edges)"
    )
    if summary.description:
        header += f"\nDescription: {summary.description}"

    node_lines = [_format_node_line(n) for n in summary.nodes]
    edge_lines = [
        f"  {e.source_node_id}.{e.source_port} -> "
        f"{e.target_node_id}.{e.target_port} [{e.edge_type}]"
        for e in summary.edges
    ]

    entry_str = ", ".join(summary.entry_points) if summary.entry_points else "(none)"
    exit_str = ", ".join(summary.exit_points) if summary.exit_points else "(none)"
    footer = f"Entry: {entry_str} | Exit: {exit_str}"

    sections = [
        header,
        "Nodes:\n" + "\n".join(node_lines) if node_lines else "Nodes: (none)",
        "Edges:\n" + "\n".join(edge_lines) if edge_lines else "Edges: (none)",
        footer,
    ]
    text = "\n".join(sections)

    if len(text) <= max_chars:
        return text

    # Progressive truncation: drop prompt snippets, then descriptions,
    # then port details until we fit.
    for level in range(3):
        truncated_lines: list[str] = []
        for n in summary.nodes:
            parts = [f"  - {n.id} [{n.node_type}]"]
            if n.model:
                parts.append(f"model={n.model}")
            if level < 2:
                if n.input_ports:
                    parts.append(f"in: {', '.join(n.input_ports)}")
                if n.output_ports:
                    parts.append(f"out: {', '.join(n.output_ports)}")
            truncated_lines.append(" | ".join(parts))

        sections = [
            header,
            "Nodes:\n" + "\n".join(truncated_lines),
            "Edges:\n" + "\n".join(edge_lines) if edge_lines else "Edges: (none)",
            footer,
        ]
        text = "\n".join(sections)
        if len(text) <= max_chars:
            return text

    # Last resort: truncate edge list
    while len(text) > max_chars and edge_lines:
        edge_lines.pop()
        sections[-2] = "Edges:\n" + "\n".join(edge_lines) + "\n  ... (truncated)"
        text = "\n".join(sections)

    return text[:max_chars]


# ---------------------------------------------------------------------------
# Chat manager
# ---------------------------------------------------------------------------


def _normalize_usage(raw: dict[str, int] | None) -> dict[str, int]:
    """Normalize provider usage dicts to {prompt, completion} keys."""
    if not raw:
        return {}
    return {
        "prompt": raw.get("prompt", 0) or raw.get("prompt_tokens", 0) or 0,
        "completion": raw.get("completion", 0) or raw.get("completion_tokens", 0) or 0,
    }


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def _try_parse_mutation_json(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a mutation plan from freeform LLM text."""
    for match in _JSON_BLOCK_RE.finditer(text):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict) and isinstance(data.get("operations"), list):
                return data
        except (json.JSONDecodeError, KeyError):
            continue
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict) and isinstance(data.get("operations"), list):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _coerce_strict_edges(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a shallow-copied operation list without forcing strict edges.

    Build-from-intent plans frequently wire semantic target ports
    (e.g. ``fundamental_analysis``) that are intended to be auto-created
    on downstream nodes. Forcing ``strict=True`` causes valid generated plans
    to fail dry-run/apply with port-not-found errors.
    """
    return [dict(op) for op in operations]


def _normalize_generated_mutation_ops(
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize common LLM schema drifts in mutation operations.

    This is intentionally conservative: it only repairs a few high-frequency
    shape mismatches so dry-run can proceed and auto-repair has a chance to
    converge.
    """
    normalized: list[dict[str, Any]] = []
    for op in operations:
        if not isinstance(op, dict):
            continue
        op_norm = dict(op)
        if op_norm.get("op") == "add_edge":
            # Chat tool schema doesn't carry ContextEdge-specific fields
            # (context_key/mode). If the model emits edge_type=context here,
            # resulting graph edges are schema-invalid. Treat as data edge.
            if op_norm.get("edge_type") == "context":
                op_norm["edge_type"] = "data"
            normalized.append(op_norm)
            continue

        if op_norm.get("op") != "add_node":
            normalized.append(op_norm)
            continue

        config = op_norm.get("config")
        if not isinstance(config, dict):
            normalized.append(op_norm)
            continue
        cfg = dict(config)
        node_type = op_norm.get("node_type")

        if node_type == "parallel_subagents":
            branch_graphs = cfg.get("branch_graphs")
            if isinstance(branch_graphs, dict):
                cfg["branch_graphs"] = list(branch_graphs.keys())
            elif isinstance(branch_graphs, str):
                cfg["branch_graphs"] = [branch_graphs]

            branch_inputs = cfg.get("branch_inputs")
            if isinstance(branch_inputs, dict):
                remapped: dict[str, dict[str, Any]] = {}
                for branch, val in branch_inputs.items():
                    if isinstance(val, dict):
                        remapped[branch] = val
                    elif isinstance(val, str):
                        remapped[branch] = {"input": val}
                    else:
                        remapped[branch] = {"input": val}
                cfg["branch_inputs"] = remapped

        elif node_type == "validator":
            on_failure = cfg.get("on_failure")
            if isinstance(on_failure, str):
                alias = {
                    "retry": "route",
                    "continue": "warn",
                    "ignore": "warn",
                    "stop": "halt",
                    "fail": "halt",
                }
                cfg["on_failure"] = alias.get(on_failure, on_failure)

            rules = cfg.get("validation_rules")
            if isinstance(rules, list):
                fixed_rules: list[dict[str, Any]] = []
                for item in rules:
                    if not isinstance(item, dict):
                        continue
                    rule = dict(item)
                    rule_type = rule.get("rule_type")
                    if rule_type in ("required_field", "required_fields"):
                        rule["rule_type"] = "required_keys"
                        rule_cfg = rule.get("config")
                        if not isinstance(rule_cfg, dict):
                            rule_cfg = {}
                        if "keys" not in rule_cfg:
                            if isinstance(rule_cfg.get("field"), str):
                                rule_cfg["keys"] = [rule_cfg["field"]]
                            elif isinstance(rule_cfg.get("key"), str):
                                rule_cfg["keys"] = [rule_cfg["key"]]
                            elif isinstance(rule_cfg.get("fields"), list):
                                rule_cfg["keys"] = list(rule_cfg["fields"])
                            elif isinstance(rule_cfg.get("required"), list):
                                rule_cfg["keys"] = list(rule_cfg["required"])
                            elif isinstance(rule.get("field"), str):
                                rule_cfg["keys"] = [rule["field"]]
                            elif isinstance(rule.get("key"), str):
                                rule_cfg["keys"] = [rule["key"]]
                        rule["config"] = rule_cfg
                    fixed_rules.append(rule)
                cfg["validation_rules"] = fixed_rules

        op_norm["config"] = cfg
        normalized.append(op_norm)

    return normalized


def _build_args_preview(mutation_data: dict[str, Any]) -> str:
    """Build a short human-readable summary of mutation arguments."""
    desc = mutation_data.get("description", "")
    ops = mutation_data.get("operations", [])
    n_ops = len(ops)
    if desc:
        return f"{desc} ({n_ops} operation{'s' if n_ops != 1 else ''})"
    if n_ops > 0:
        op_types = [op.get("op", "?") for op in ops[:3]]
        suffix = f" +{n_ops - 3} more" if n_ops > 3 else ""
        return ", ".join(op_types) + suffix
    return f"{n_ops} operations"


def _build_dry_run_preview(dry_result: Any) -> tuple[str, str]:
    """Build (status, output_preview) from a dry-run result."""
    if dry_result.success:
        n_ops = len(dry_result.applied_operations) if hasattr(dry_result, "applied_operations") else 0
        return "success", f"Dry run passed ({n_ops} operations applied)"
    errors = [e.message for e in dry_result.errors] if dry_result.errors else ["Unknown error"]
    return "error", f"Dry run failed: {errors[0]}"


class ChatManager:
    def __init__(
        self,
        provider_registry: ProviderRegistry,
        graph_store: GraphStore,
        mention_resolver: Any | None = None,
    ) -> None:
        self._providers = provider_registry
        self._graph_store = graph_store
        self._mention_resolver = mention_resolver
        self._chat_model = os.environ.get(
            "DAN_CHAT_MODEL",
            os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6"),
        )
        self._cancel_events: dict[str, asyncio.Event] = {}

    def register_stream(self, channel_id: str) -> asyncio.Event:
        """Register a cancellation event for an active stream."""
        evt = asyncio.Event()
        self._cancel_events[channel_id] = evt
        return evt

    def cancel_stream(self, channel_id: str) -> bool:
        """Signal a running stream to stop. Returns True if stream was found."""
        evt = self._cancel_events.get(channel_id)
        if evt is None:
            return False
        evt.set()
        return True

    def unregister_stream(self, channel_id: str) -> None:
        """Clean up a finished stream's cancellation event."""
        self._cancel_events.pop(channel_id, None)

    # ------------------------------------------------------------------
    # Text-only streaming path (original)
    # ------------------------------------------------------------------

    async def send_message(
        self,
        workflow_id: str,
        message: str,
        history: list[dict[str, str]],
        thread_id: str | None = None,
        client_graph_revision: str | None = None,
        mode: str = "agent",
        cancel_event: asyncio.Event | None = None,
        debug_context: str = "",
        mentions: list[Any] | None = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Stream a text-only LLM response (no function calling)."""
        try:
            graph_dict = self._graph_store.get_graph(workflow_id)
            if graph_dict is None:
                yield ChatErrorEvent(error=f"Workflow '{workflow_id}' not found")
                return

            graph = Graph.model_validate(graph_dict)
            summary = build_graph_summary(graph, workflow_id)

            revision_mismatch = (
                client_graph_revision is not None
                and client_graph_revision != summary.revision
            )
            if revision_mismatch:
                logger.warning(
                    "Graph revision mismatch for %s: client=%s current=%s",
                    workflow_id,
                    client_graph_revision,
                    summary.revision,
                )

            messages = self._build_messages(
                summary, message, history, mode=mode, debug_context=debug_context,
                mentions=mentions, workflow_id=workflow_id, graph_dict=graph_dict,
            )

            provider = self._providers.resolve(self._chat_model)
            message_id = uuid.uuid4().hex[:12]
            final_content = ""
            token_usage: dict[str, int] = {}
            interrupted = False

            async for chunk in provider.stream(
                messages=messages,
                model=self._chat_model,
                temperature=0.7,
            ):
                if cancel_event and cancel_event.is_set():
                    final_content = chunk.accumulated
                    token_usage = _normalize_usage(chunk.usage)
                    interrupted = True
                    break
                yield ChatTokenEvent(
                    delta=chunk.delta,
                    accumulated=chunk.accumulated,
                )
                if chunk.done:
                    final_content = chunk.accumulated
                    token_usage = _normalize_usage(chunk.usage)

            if interrupted:
                yield ChatInterruptedEvent(
                    message_id=message_id,
                    content=final_content,
                    token_usage=token_usage,
                )
            else:
                yield ChatCompleteEvent(
                    message_id=message_id,
                    content=final_content,
                    token_usage=token_usage,
                    context_window=_get_context_window(self._chat_model),
                    graph_revision=summary.revision,
                    revision_mismatch=revision_mismatch,
                )

        except KeyError as exc:
            logger.error("Provider resolution failed: %s", exc)
            yield ChatErrorEvent(error=f"LLM provider error: {exc}")
        except Exception as exc:
            logger.exception("Chat error for workflow %s", workflow_id)
            yield ChatErrorEvent(error=str(exc))

    # ------------------------------------------------------------------
    # Function-calling path (mutations via tool use)
    # ------------------------------------------------------------------

    async def send_message_with_tools(
        self,
        workflow_id: str,
        message: str,
        history: list[dict[str, str]],
        thread_id: str | None = None,
        client_graph_revision: str | None = None,
        mode: str = "agent",
        cancel_event: asyncio.Event | None = None,
        debug_context: str = "",
        mentions: list[Any] | None = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Process a user message using LLM function calling for graph mutations.

        Falls back to the text-streaming path when the provider does not
        support the ``tools`` parameter.
        """
        try:
            graph_dict = self._graph_store.get_graph(workflow_id)
            if graph_dict is None:
                yield ChatErrorEvent(error=f"Workflow '{workflow_id}' not found")
                return

            graph = Graph.model_validate(graph_dict)
            summary = build_graph_summary(graph, workflow_id)
            revision = summary.revision
            is_empty_graph = summary.node_count == 0 and summary.edge_count == 0

            revision_mismatch = (
                client_graph_revision is not None
                and client_graph_revision != revision
            )
            if revision_mismatch:
                logger.warning(
                    "Graph revision mismatch for %s: client=%s current=%s",
                    workflow_id,
                    client_graph_revision,
                    revision,
                )

            messages = self._build_messages(
                summary, message, history, mode=mode, debug_context=debug_context,
                mentions=mentions, workflow_id=workflow_id, graph_dict=graph_dict,
            )
            provider = self._providers.resolve(self._chat_model)
            message_id = uuid.uuid4().hex[:12]

            try:
                complete_task: asyncio.Task[CompletionResult] = asyncio.create_task(
                    provider.complete(
                        messages=messages,
                        model=self._chat_model,
                        temperature=0.7,
                        tools=[MUTATION_TOOL_SCHEMA],
                        tool_choice="auto",
                    ),
                )
                cancel_wait_task: asyncio.Task[bool] | None = None
                if cancel_event is not None:
                    cancel_wait_task = asyncio.create_task(cancel_event.wait())
                    done, pending = await asyncio.wait(
                        {complete_task, cancel_wait_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if cancel_wait_task in done and cancel_event.is_set():
                        complete_task.cancel()
                        try:
                            await complete_task
                        except asyncio.CancelledError:
                            pass
                        yield ChatInterruptedEvent(
                            message_id=message_id,
                            content="",
                            token_usage={},
                        )
                        return
                    for task in pending:
                        task.cancel()
                result: CompletionResult = await complete_task
            except Exception as exc:
                logger.debug(
                    "Tool-calling complete() failed (%s), falling back to stream",
                    exc,
                )
                async for event in self._stream_with_json_fallback(
                    provider, messages, message_id,
                    revision, revision_mismatch, graph_dict,
                    cancel_event=cancel_event,
                    mode=mode,
                ):
                    yield event
                return

            mutation_data = self._extract_mutation_from_result(result)
            if mutation_data is not None:
                tool_call_id = f"tc_{uuid.uuid4().hex[:10]}"
                tool_start_time = time.monotonic()

                yield ChatToolCallStartEvent(
                    tool_call_id=tool_call_id,
                    tool_name="plan_graph_mutations",
                    args_preview=_build_args_preview(mutation_data),
                )

                ops = _normalize_generated_mutation_ops(
                    mutation_data.get("operations", []),
                )
                if is_empty_graph:
                    ops = _coerce_strict_edges(ops)
                plan = MutationPlan.model_validate({
                    "operations": ops,
                    "description": mutation_data.get("description", ""),
                    "reasoning": mutation_data.get("reasoning", ""),
                    "base_graph_revision": revision,
                })
                dry_result = GraphMutator().dry_run(
                    graph_dict, plan, current_revision=revision,
                )

                if (
                    not dry_result.success
                    and not dry_result.stale_plan
                    and _MUTATION_AUTO_RETRY
                    and _MUTATION_AUTO_RETRY_MAX > 0
                ):
                    retry_assistant = result.text or ""
                    for attempt in range(_MUTATION_AUTO_RETRY_MAX):
                        mutation_metrics.record_retry()
                        error_summary = "; ".join(e.message for e in dry_result.errors)
                        logger.info(
                            "Dry-run failed for plan %s, attempting auto-retry %d/%d: %s",
                            plan.plan_id,
                            attempt + 1,
                            _MUTATION_AUTO_RETRY_MAX,
                            error_summary,
                        )
                        retry_messages = messages + [
                            {"role": "assistant", "content": retry_assistant},
                            {
                                "role": "user",
                                "content": (
                                    f"The mutation plan produced these errors:\n{error_summary}\n\n"
                                    "Please produce a corrected plan_graph_mutations call "
                                    "that fixes these issues."
                                ),
                            },
                        ]
                        try:
                            retry_result: CompletionResult = await provider.complete(
                                messages=retry_messages,
                                model=self._chat_model,
                                temperature=0.5,
                                tools=[MUTATION_TOOL_SCHEMA],
                                tool_choice="auto",
                            )
                        except Exception as retry_exc:
                            logger.debug("Auto-retry LLM call failed: %s", retry_exc)
                            break

                        retry_assistant = retry_result.text or retry_assistant
                        retry_mutation = self._extract_mutation_from_result(retry_result)
                        if retry_mutation is None:
                            continue

                        retry_ops = _normalize_generated_mutation_ops(
                            retry_mutation.get("operations", []),
                        )
                        if is_empty_graph:
                            retry_ops = _coerce_strict_edges(retry_ops)
                        retry_plan = MutationPlan.model_validate({
                            "operations": retry_ops,
                            "description": retry_mutation.get("description", ""),
                            "reasoning": retry_mutation.get("reasoning", ""),
                            "base_graph_revision": revision,
                        })
                        retry_dry = GraphMutator().dry_run(
                            graph_dict, retry_plan, current_revision=revision,
                        )

                        # Keep latest candidate so the user sees the most
                        # recent attempted fix if retries still fail.
                        plan = retry_plan
                        dry_result = retry_dry
                        mutation_data = retry_mutation
                        result = retry_result

                        if retry_dry.success:
                            logger.info(
                                "Auto-retry succeeded for plan %s on attempt %d",
                                plan.plan_id,
                                attempt + 1,
                            )
                            break
                        if retry_dry.stale_plan:
                            break

                if dry_result.stale_plan:
                    mutation_metrics.record_stale_plan()
                    logger.info(
                        "Stale plan for %s, re-planning against current revision",
                        plan.plan_id,
                    )
                    graph_dict = self._graph_store.get_graph(workflow_id)
                    if graph_dict is not None:
                        graph = Graph.model_validate(graph_dict)
                        summary = build_graph_summary(graph, workflow_id)
                        revision = summary.revision
                        replan_messages = self._build_messages(summary, message, history, mode=mode)
                        replan_messages.append({
                            "role": "user",
                            "content": (
                                "The graph has changed since your last plan. "
                                "Please re-plan the requested changes against "
                                "the updated workflow."
                            ),
                        })
                        try:
                            replan_result = await provider.complete(
                                messages=replan_messages,
                                model=self._chat_model,
                                temperature=0.5,
                                tools=[MUTATION_TOOL_SCHEMA],
                                tool_choice="auto",
                            )
                            replan_mutation = self._extract_mutation_from_result(
                                replan_result,
                            )
                            if replan_mutation is not None:
                                replan_ops = _normalize_generated_mutation_ops(
                                    replan_mutation.get("operations", []),
                                )
                                if is_empty_graph:
                                    replan_ops = _coerce_strict_edges(replan_ops)
                                replan_plan = MutationPlan.model_validate({
                                    "operations": replan_ops,
                                    "description": replan_mutation.get("description", ""),
                                    "reasoning": replan_mutation.get("reasoning", ""),
                                    "base_graph_revision": revision,
                                })
                                replan_dry = GraphMutator().dry_run(
                                    graph_dict,
                                    replan_plan,
                                    current_revision=revision,
                                )
                                if replan_dry.success:
                                    plan = replan_plan
                                    dry_result = replan_dry
                                    logger.info("Stale-plan re-planning succeeded")
                        except Exception as replan_exc:
                            logger.debug(
                                "Stale-plan re-planning failed: %s", replan_exc,
                            )

                dr_status, dr_preview = _build_dry_run_preview(dry_result)
                elapsed = int((time.monotonic() - tool_start_time) * 1000)
                yield ChatToolCallResultEvent(
                    tool_call_id=tool_call_id,
                    tool_name="plan_graph_mutations",
                    status=dr_status,
                    output_preview=dr_preview,
                    duration_ms=elapsed,
                )

                normalized_usage = _normalize_usage(result.usage)
                plan_dump = plan.model_dump()
                if mode == "debug":
                    plan_dump.setdefault("metadata", {})["source"] = "debug-fix"
                yield ChatMutationEvent(
                    message_id=message_id,
                    content=mutation_data.get("reasoning", result.text or ""),
                    mutation_plan=plan_dump,
                    dry_run_result=dry_result.model_dump(),
                    token_usage=normalized_usage,
                    context_window=_get_context_window(self._chat_model),
                    graph_revision=revision,
                    revision_mismatch=revision_mismatch,
                )
                return

            content = result.text or ""
            normalized_usage = _normalize_usage(result.usage)
            if content:
                yield ChatTokenEvent(delta=content, accumulated=content)
            yield ChatCompleteEvent(
                message_id=message_id,
                content=content,
                token_usage=normalized_usage,
                context_window=_get_context_window(self._chat_model),
                graph_revision=revision,
                revision_mismatch=revision_mismatch,
            )

        except KeyError as exc:
            logger.error("Provider resolution failed: %s", exc)
            yield ChatErrorEvent(error=f"LLM provider error: {exc}")
        except Exception as exc:
            logger.exception("Chat error for workflow %s", workflow_id)
            yield ChatErrorEvent(error=str(exc))

    # ------------------------------------------------------------------
    # Fallback: stream text, then try to parse JSON as mutation plan
    # ------------------------------------------------------------------

    async def _stream_with_json_fallback(
        self,
        provider: Any,
        messages: list[dict[str, str]],
        message_id: str,
        revision: str,
        revision_mismatch: bool,
        graph_dict: dict[str, Any],
        cancel_event: asyncio.Event | None = None,
        mode: str = "agent",
    ) -> AsyncIterator[ChatStreamEvent]:
        final_content = ""
        token_usage: dict[str, int] = {}
        interrupted = False

        async for chunk in provider.stream(
            messages=messages,
            model=self._chat_model,
            temperature=0.7,
        ):
            if cancel_event and cancel_event.is_set():
                final_content = chunk.accumulated
                token_usage = _normalize_usage(chunk.usage)
                interrupted = True
                break
            yield ChatTokenEvent(
                delta=chunk.delta,
                accumulated=chunk.accumulated,
            )
            if chunk.done:
                final_content = chunk.accumulated
                token_usage = _normalize_usage(chunk.usage)

        if interrupted:
            yield ChatInterruptedEvent(
                message_id=message_id,
                content=final_content,
                token_usage=token_usage,
            )
            return

        mutation_data = _try_parse_mutation_json(final_content)
        if mutation_data is not None:
            try:
                plan = MutationPlan.model_validate({
                    "operations": _normalize_generated_mutation_ops(
                        mutation_data.get("operations", []),
                    ),
                    "description": mutation_data.get("description", ""),
                    "reasoning": mutation_data.get("reasoning", ""),
                    "base_graph_revision": revision,
                })
                dry_result = GraphMutator().dry_run(
                    graph_dict, plan, current_revision=revision,
                )
                if not dry_result.success and not dry_result.stale_plan:
                    error_summary = "; ".join(e.message for e in dry_result.errors)
                    logger.info(
                        "Dry-run failed in fallback path for plan %s "
                        "(no auto-retry in fallback): %s",
                        plan.plan_id,
                        error_summary,
                    )
                plan_dump = plan.model_dump()
                if mode == "debug":
                    plan_dump.setdefault("metadata", {})["source"] = "debug-fix"
                yield ChatMutationEvent(
                    message_id=message_id,
                    content=mutation_data.get("reasoning", ""),
                    mutation_plan=plan_dump,
                    dry_run_result=dry_result.model_dump(),
                    token_usage=token_usage,
                    context_window=_get_context_window(self._chat_model),
                    graph_revision=revision,
                    revision_mismatch=revision_mismatch,
                )
                return
            except Exception as exc:
                logger.debug("JSON fallback mutation parse failed: %s", exc)

        yield ChatCompleteEvent(
            message_id=message_id,
            content=final_content,
            token_usage=token_usage,
            context_window=_get_context_window(self._chat_model),
            graph_revision=revision,
            revision_mismatch=revision_mismatch,
        )

    # ------------------------------------------------------------------
    # Mutation extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_mutation_from_result(
        result: CompletionResult,
    ) -> dict[str, Any] | None:
        """Extract a mutation plan dict from a CompletionResult.

        Checks native tool_calls first, then falls back to parsing
        structured JSON from the response text.
        """
        if result.tool_calls:
            for tc in result.tool_calls:
                func = tc.get("function", {})
                if func.get("name") == "plan_graph_mutations":
                    try:
                        data = json.loads(func["arguments"])
                        if isinstance(data.get("operations"), list):
                            return data
                    except (json.JSONDecodeError, KeyError):
                        pass
        return _try_parse_mutation_json(result.text or "")

    # ------------------------------------------------------------------
    # Message building
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        summary: GraphSummary,
        user_message: str,
        history: list[dict[str, str]],
        mode: str = "agent",
        debug_context: str = "",
        mentions: list[Any] | None = None,
        workflow_id: str = "",
        graph_dict: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        is_empty = summary.node_count == 0 and summary.edge_count == 0
        graph_text = (
            EMPTY_GRAPH_SUMMARY_PLACEHOLDER
            if is_empty
            else serialize_for_prompt(summary)
        )

        if mode == "ask":
            system_content = ASK_PROMPT.format(
                node_type_reference=NODE_TYPE_REFERENCE,
                graph_summary=graph_text,
            )
        elif mode == "plan":
            system_content = PLAN_PROMPT.format(
                node_type_reference=NODE_TYPE_REFERENCE,
                graph_summary=graph_text,
            )
        elif mode == "debug":
            system_content = DEBUG_PROMPT.format(
                node_type_reference=NODE_TYPE_REFERENCE,
                graph_summary=graph_text,
                debug_context=debug_context or "No recent run failures found. Ask the user to describe the issue.",
            )
        else:
            template = BUILD_FROM_INTENT_PROMPT if is_empty else SYSTEM_PROMPT_TEMPLATE
            system_content = template.format(
                node_type_reference=NODE_TYPE_REFERENCE,
                graph_summary=graph_text,
            )

        context_window = _get_context_window(self._chat_model)

        resolved_mentions = []
        if mentions and self._mention_resolver and workflow_id:
            try:
                resolved_mentions = self._mention_resolver.resolve_all(
                    mentions, workflow_id, graph_dict, model=self._chat_model
                )
            except Exception as exc:
                logger.warning("Mention resolution failed: %s", exc)

        if resolved_mentions:
            from dan.server.mention_resolver import pack_context

            messages = pack_context(
                system_content=system_content,
                mention_blocks=resolved_mentions,
                history=history,
                user_message=user_message,
                context_window=context_window,
                max_ratio=_MAX_CONTEXT_RATIO,
                model=self._chat_model,
            )
        else:
            messages = [{"role": "system", "content": system_content}]
            messages.extend(history)
            messages.append({"role": "user", "content": user_message})
            max_tokens = int(context_window * _MAX_CONTEXT_RATIO)
            messages = compact_history(messages, max_tokens, model=self._chat_model)

        return messages

    # ------------------------------------------------------------------
    # Multi-turn clarification
    # ------------------------------------------------------------------

    async def clarify_intent(
        self,
        workflow_id: str,
        message: str,
        history: list[dict[str, str]],
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Stream clarifying questions when build-mode intent is ambiguous."""
        clarify_prompt = (
            "The user wants to create a workflow but their intent is not specific enough "
            "to produce a reliable plan. Ask 1-2 focused clarifying questions to understand:\n"
            "1. What is the primary goal? (paper writing, data analysis, RAG QA, etc.)\n"
            "2. What inputs do they have? (PDFs, data files, topic only)\n"
            "3. What output do they want? (paper, report, analysis summary)\n"
            "Be concise. Do not produce a mutation plan yet."
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": clarify_prompt},
        ]
        messages.extend(history)
        messages.append({"role": "user", "content": message})

        try:
            provider = self._providers.resolve(self._chat_model)
            stream = provider.stream(
                messages=messages,
                model=self._chat_model,
                temperature=0.7,
            )

            message_id = uuid.uuid4().hex[:12]
            final_content = ""
            token_usage: dict[str, int] = {}

            async for chunk in stream:
                yield ChatTokenEvent(
                    delta=chunk.delta,
                    accumulated=chunk.accumulated,
                )
                if chunk.done:
                    final_content = chunk.accumulated
                    token_usage = _normalize_usage(chunk.usage)

            yield ChatCompleteEvent(
                message_id=message_id,
                content=final_content,
                token_usage=token_usage,
                context_window=_get_context_window(self._chat_model),
                graph_revision="",
            )
        except Exception as exc:
            logger.exception("Clarify error")
            yield ChatErrorEvent(error=str(exc))
