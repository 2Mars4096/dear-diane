"""Prompt construction, templates, and system prompt assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any, Awaitable, Callable, Literal

from dan.server.graph_mutator import (
    _default_node_config,
    _default_ports,
)

logger = logging.getLogger(__name__)


def _normalize_autonomy_preference(
    value: str | None,
    *,
    default: str = "balanced",
) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"auto", "careful", "balanced", "aggressive"}:
        return raw
    return default

# ---------------------------------------------------------------------------
# Node / edge type constants
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Mutation tool schema
# ---------------------------------------------------------------------------


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

# ---------------------------------------------------------------------------
# System prompt templates
# ---------------------------------------------------------------------------

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

BUILD_FROM_INTENT_PROMPT = """\
You are DAN's workflow architect. Decompose intent into nodes, edges, and data flow, then produce a mutation plan.

## Process
1. Identify goal → 2. Break into stages (input→process→output) → 3. Map to node types → 4. Define port connections

## Node types
{node_type_reference}

## Edge types: data (structured), control (routing), context (shared state)

## Patterns (expand_pattern op)
- chain: N sequential LLM nodes (count, names, prompts)
- review_loop: Writer→Reviewer→Gate (writer_name, reviewer_name, condition, max_iterations)
- fan_out: Source→ForEach→processor (source_name, body_name, parallelism)
- rag_qa: RAG→LLM answer (rag_name, collection, top_k, answer_prompt)
- data_ingest: PDF dir→index→RAG (input_var, collection, rag_name, top_k)
- data_analysis: Data file→preprocess→LLM summary (input_var)

## Intent mapping
- Paper writing: data_ingest + review_loop + chain
- Paper + data: data_ingest + data_analysis + review_loop
- RAG QA: rag_qa or data_ingest
- Multi-step analysis: chain
- Parallel items: fan_out
- "PDFs at X" / "data at Y": data_ingest / data_analysis with input_var

## Templates
informs_paper_writing | rag_research | paper_writing | rag_qa | chain_3

## Skills (apply_skill op)
- management_science_writing: INFORMS conventions (nodes tagged "writing"/"review")
- informs_latex_style: LaTeX formatting (nodes tagged "latex")
Apply when user mentions a specific journal.

## File paths
"Data at X" / "PDFs at Y" → InputNode with path variable → wire to data_ingest/data_analysis.

## State
{graph_summary}

## Rules
- Produce a complete runnable workflow.
- Prefer expand_pattern for known shapes; add_node/add_edge for custom.
- strict=true on edges. Exact port names only.
- Paper workflows: include full pipeline through LaTeX compile + package.
- Ambiguous intent → sensible defaults (intro, methods, results, discussion).
"""

# ---------------------------------------------------------------------------
# Capability reference (tool family summary for system prompt)
# ---------------------------------------------------------------------------

_capability_reference_cache: str | None = None

_CATEGORY_ORDER = [
    ("file", "File"),
    ("document", "Document"),
    ("web", "Web"),
    ("browser", "Browser"),
    ("system", "System"),
    ("communication", "Communication"),
    ("text", "Text"),
    ("utility", "Utility"),
    ("data", "Data"),
    ("media", "Media"),
    ("git", "Git"),
]


def generate_capability_reference() -> str:
    """Build a compact tool-family summary for the system prompt."""
    global _capability_reference_cache
    if _capability_reference_cache is not None:
        return _capability_reference_cache

    try:
        from dan.tools import get_all_tools
        all_tools = get_all_tools()
    except Exception:
        all_tools = {}

    by_category: dict[str, list[str]] = {}
    for tool_id, (_fn, meta) in all_tools.items():
        cat = meta.get("category", "other")
        by_category.setdefault(cat, []).append(tool_id)

    def _family_preview(tool_ids: list[str], *, limit: int = 4) -> str:
        ordered = sorted(tool_ids)
        preview = ordered[:limit]
        extra = len(ordered) - len(preview)
        suffix = f", +{extra} more" if extra > 0 else ""
        return ", ".join(preview) + suffix

    lines = [
        "## Tool Use",
        "",
        "Use the provided tool schemas instead of guessing. Prefer tools when you need grounded facts, "
        "file contents, web/system actions, or to write deliverables to disk.",
        "",
    ]

    seen: set[str] = set()
    for cat_key, cat_label in _CATEGORY_ORDER:
        entries = by_category.get(cat_key)
        if not entries:
            continue
        seen.add(cat_key)
        lines.append(f"**{cat_label}:** {_family_preview(entries)}")

    for cat_key in sorted(by_category.keys()):
        if cat_key in seen:
            continue
        entries = by_category[cat_key]
        label = cat_key.replace("_", " ").title()
        lines.append(f"**{label}:** {_family_preview(entries)}")

    lines.append("")
    lines.append(
        "file_read, pdf_read, list_directory accept absolute paths (~/...)."
    )
    lines.append(
        "For large files: use file_read with grep to find sections, then use specific "
        "start_line/end_line ranges instead of re-reading the whole file."
    )
    lines.append(
        "For long outputs (reports, code, documents): use file_write to save to disk "
        "section by section. First outline the structure, then write one bounded chunk "
        "at a time. First call: mode='overwrite' with preamble + first section. "
        "Then mode='append' for each subsequent section. Keep each write scoped to a "
        "small, coherent chunk such as one section at a time. "
        "Do NOT put long content in chat — write it to a file."
    )
    lines.append(
        "For large files, locate relevant sections first and read targeted ranges."
    )

    _capability_reference_cache = "\n".join(lines)
    return _capability_reference_cache


def invalidate_capability_cache() -> None:
    """Clear cached tool reference so it regenerates on next access."""
    global _capability_reference_cache
    _capability_reference_cache = None


# ---------------------------------------------------------------------------
# Surface hints
# ---------------------------------------------------------------------------

_WHATSAPP_SURFACE_HINTS = (
    "## Surface: WhatsApp\n"
    "- Current model: {model_name}\n"
    "- Keep replies concise (1-5 sentences for simple tasks, structured sections for reports)\n"
    "- Use *bold* for headers (not **markdown**). Bullet points with \u2022\n"
    "- ABSOLUTELY NO HTML tags \u2014 WhatsApp renders these as raw text\n"
    "- No code blocks, no markdown tables \u2014 plain text only\n"
    "- URLs on their own line (auto-linkified)\n"
    "- For long reports, organize into clearly separated sections"
)

_RESEARCH_REPORT_PROMPT_HINT = """\
## Research & Report Behavior

When asked for a research report, literature review, equity analysis, or deep-dive topic:

- Ground factual claims in tool results; if support is missing, say so.
- Search multiple angles, then read the strongest sources instead of relying only on snippets.
- Outline longer deliverables before writing.
- For long documents, write incrementally to disk one section at a time instead of trying to emit everything in one pass.
- For academic topics, check for relevant local PDFs when likely available.
"""

_RESEARCH_REPORT_PROMPT_DETAIL = """\
### Phase 1 — Research
1. Use the current date shown above to anchor words like "recent"; include the year in time-sensitive searches.
2. Search multiple angles (typically 3-8 distinct web_search queries). Batch same-type calls: \
multiple web_search calls in one response is fine, but do not mix with file_write.
3. For promising results, use web_fetch or web_search with fetch_content=true to read the page instead of relying only on snippets.
4. Every factual claim or citation must come from a tool result. If you cannot source it, say so.
5. If the current evidence is only search snippets, say the answer is tentative or fetch more before concluding.
6. When search results are numbered, cite them inline as [1], [2] and include markdown links to source URLs.
7. **Never paste raw search snippets or fetched page text into your response.** Synthesize findings in your own words and cite source URLs in markdown links.
8. For academic topics, check for relevant local PDFs when likely available.

### Phase 2 — Structure
Before writing, outline the document: list sections and subsections. \
If the user requested a specific format (e.g. .tex, .md), plan the preamble/header separately.

### Phase 3 — Incremental writing (MANDATORY for documents > ~1000 words)
Write the document **chunk by chunk**, one section per file_write call:
1. **First call:** file_write mode='overwrite' — preamble / header + first section only (~1500-2500 chars).
2. **Each subsequent call:** file_write mode='append' — one section at a time.
3. **Final call:** file_write mode='append' — closing matter (bibliography, \\end{document}, etc.).
Do NOT write the entire document in a single file_write call — it will time out or degrade quality.
This is analogous to file_read with start_line/end_line: produce and consume content in bounded chunks.
"""

_RESEARCH_REPORT_STRONG_PHRASES = (
    "literature review",
    "literature survey",
    "research report",
    "investment memo",
    "stock pitch",
    "equity research",
    "research note",
)

_RESEARCH_REPORT_DELIVERABLE_CUES = (
    "report",
    "memo",
    "brief",
    "review",
    "survey",
    "write-up",
    "writeup",
)

_RESEARCH_REPORT_EVIDENCE_CUES = (
    "cite",
    "citation",
    "citations",
    "source",
    "sources",
    "reference",
    "references",
    "papers",
    "recent",
    "latest",
    "evidence",
)

_RESEARCH_REPORT_FORMAT_CUES = (
    ".md",
    ".tex",
    "markdown",
    "latex",
    "outline",
    "sections",
    "bibliography",
)

_RESEARCH_REPORT_SYNTHESIS_CUES = (
    "analysis",
    "analyze",
    "compare",
    "comparison",
    "deep dive",
    "deep-dive",
    "company",
    "industry",
    "market",
)

_RESEARCH_HINT_CLASSIFIER_SYSTEM_PROMPT = """\
You decide whether DAN should enable its special research/report behavior.

Return ONLY `YES` or `NO`.

Return `YES` only when the user's request is for a substantial researched synthesis or structured written deliverable that likely benefits from multi-source gathering, citations/sources, outline-first planning, or incremental long-form writing.

Return `NO` for ordinary Q&A, simple summaries, short explanations, or generic analysis requests that do not clearly need that heavier research/report workflow.
"""

_EXPLORATION_STRONG_PHRASES = (
    "help me understand",
    "walk me through",
    "trace the flow",
    "trace how",
    "where is",
    "what handles",
    "map the codebase",
    "explore the codebase",
)

_EXPLORATION_DISCOVERY_CUES = (
    "explore",
    "inspect",
    "investigate",
    "trace",
    "understand",
    "map",
    "skim",
    "walk through",
)

_EXPLORATION_TARGET_CUES = (
    "codebase",
    "repo",
    "repository",
    "module",
    "file",
    "files",
    "folder",
    "folders",
    "directory",
    "directories",
    "symbol",
    "symbols",
    "function",
    "class",
    "flow",
    "call path",
    "execution path",
)

_EXPLORATION_NEGATIVE_CUES = (
    "report",
    "memo",
    "literature review",
    "write",
    "implement",
    "fix",
    "patch",
    "edit",
    "add",
    "refactor",
)

_EXPLORATION_HINT_CLASSIFIER_SYSTEM_PROMPT = """\
You decide whether DAN should enable its special exploration behavior.

Return ONLY `YES` or `NO`.

Return `YES` only when the user's request is mainly about understanding, mapping, tracing, or locating existing code/files/system behavior before making changes.

Return `NO` for requests whose main goal is writing, fixing, implementing, mutating workflows, or producing a research/report deliverable.
"""

SURFACE_HINTS = {
    "whatsapp": _WHATSAPP_SURFACE_HINTS,
    "whatsapp-web": _WHATSAPP_SURFACE_HINTS,
    "telegram": (
        "## Surface: Telegram\n"
        "- Current model: {model_name}\n"
        "- Keep replies concise (1-5 sentences for simple tasks, structured sections for reports)\n"
        "- Telegram supports Markdown: **bold**, _italic_, `code`, ```code blocks```, [links](url), ~~strikethrough~~, ||spoilers||\n"
        "- Use code blocks for data/code output — they render properly in Telegram\n"
        "- No raw HTML tags — use Markdown only\n"
        "- URLs on their own line (auto-linkified)\n"
        "- For long reports, organize into clearly separated sections"
    ),
    "server": (
        "## Surface: Editor\n"
        "- Current model: {model_name}\n"
        "- Detailed responses welcome. Full markdown supported.\n"
        "- Include code blocks, tables, and structured formatting."
    ),
    "cli": (
        "## Surface: CLI\n"
        "- Current model: {model_name}\n"
        "- Concise but can be detailed when asked.\n"
        "- Terminal-friendly formatting."
    ),
}


def _resolve_surface_hints(surface: str | None, model_name: str) -> str:
    """Resolve exact or family surface hints, including `telegram:<bot>`."""
    surface_key = surface or "server"
    base_key = surface_key.split(":", 1)[0]
    hints = SURFACE_HINTS.get(surface_key) or SURFACE_HINTS.get(base_key, SURFACE_HINTS["server"])
    if surface_key.startswith("telegram:"):
        bot_name = surface_key.split(":", 1)[1]
        hints = (
            f"{hints}\n"
            f"- You are speaking as the Telegram bot `{bot_name}`\n"
            f"- Keep this bot's persona and project focus consistent across the whole reply"
        )
    if "{model_name}" in hints:
        hints = hints.format(model_name=model_name)
    return hints


def _contains_prompt_signal(message: str, cue: str) -> bool:
    if not cue:
        return False
    if re.fullmatch(r"[a-z0-9_]+", cue):
        return re.search(rf"\b{re.escape(cue)}\b", message) is not None
    return cue in message


def _count_prompt_signals(message: str, cues: tuple[str, ...]) -> int:
    return sum(1 for cue in cues if _contains_prompt_signal(message, cue))


def _classify_research_prompt_signal(
    user_message: str,
) -> Literal["yes", "no", "maybe"]:
    msg = (user_message or "").lower()
    if any(_contains_prompt_signal(msg, phrase) for phrase in _RESEARCH_REPORT_STRONG_PHRASES):
        return "yes"

    has_deliverable = _count_prompt_signals(msg, _RESEARCH_REPORT_DELIVERABLE_CUES) > 0
    has_evidence = _count_prompt_signals(msg, _RESEARCH_REPORT_EVIDENCE_CUES) > 0
    has_format = _count_prompt_signals(msg, _RESEARCH_REPORT_FORMAT_CUES) > 0
    has_synthesis = _count_prompt_signals(msg, _RESEARCH_REPORT_SYNTHESIS_CUES) > 0

    if has_deliverable and (has_evidence or has_format):
        return "yes"
    if has_evidence and has_format and has_synthesis:
        return "yes"

    if (has_deliverable and has_synthesis) or (has_evidence and has_synthesis):
        return "maybe"
    if has_deliverable or (has_evidence and has_format):
        return "maybe"
    return "no"


def _looks_like_research_report_request(user_message: str) -> bool:
    return _classify_research_prompt_signal(user_message) == "yes"


def _classify_exploration_prompt_signal(
    user_message: str,
) -> Literal["yes", "no", "maybe"]:
    msg = (user_message or "").lower()
    if any(_contains_prompt_signal(msg, phrase) for phrase in _EXPLORATION_STRONG_PHRASES):
        return "yes"

    discovery_score = _count_prompt_signals(msg, _EXPLORATION_DISCOVERY_CUES)
    target_score = _count_prompt_signals(msg, _EXPLORATION_TARGET_CUES)
    negative_score = _count_prompt_signals(msg, _EXPLORATION_NEGATIVE_CUES)

    if negative_score >= 2 and discovery_score == 0:
        return "no"
    if discovery_score >= 1 and target_score >= 1 and negative_score == 0:
        return "yes"
    if target_score >= 1 and negative_score == 0:
        return "maybe"
    if discovery_score >= 1 and negative_score <= 1:
        return "maybe"
    return "no"


# ---------------------------------------------------------------------------
# Prompt module resolver
# ---------------------------------------------------------------------------

PromptLayer = Literal["interaction_policy", "surface_presentation", "task_specializer"]


@dataclass
class PromptContext:
    mode: str
    surface: str
    model: str
    user_message: str
    workflow_id: str
    autonomy_resolution: Any | None = None
    tools_available: bool = True
    project_metadata: dict[str, Any] = field(default_factory=dict)
    precomputed_hint_flags: dict[str, bool] = field(default_factory=dict)


@dataclass
class ResolvedPromptModule:
    module_id: str
    layer: PromptLayer
    priority: int
    content: str
    detail_id: str | None = None
    detail_body: str = ""


PromptModuleResolverFn = Callable[[PromptContext], Awaitable[ResolvedPromptModule | None]]


@dataclass
class PromptModule:
    module_id: str
    layer: PromptLayer
    priority: int
    resolver: PromptModuleResolverFn


class PromptModuleResolver:
    _LAYER_ORDER: tuple[PromptLayer, ...] = (
        "interaction_policy",
        "surface_presentation",
        "task_specializer",
    )

    def __init__(self) -> None:
        self._modules: list[PromptModule] = []

    def register(self, module: PromptModule) -> None:
        self._modules.append(module)

    async def resolve(
        self,
        context: PromptContext,
    ) -> tuple[list[ResolvedPromptModule], dict[str, str]]:
        resolved: list[ResolvedPromptModule] = []
        details: dict[str, str] = {}
        for module in self._modules:
            item = await module.resolver(context)
            if item is None or not item.content.strip():
                continue
            resolved.append(item)
            if item.detail_id and item.detail_body.strip():
                details[item.detail_id] = item.detail_body.strip()
        resolved.sort(
            key=lambda item: (
                self._LAYER_ORDER.index(item.layer),
                item.priority,
                item.module_id,
            ),
        )
        return resolved, details


def _resolve_interaction_policy_module_content(context: PromptContext) -> str:
    mode_key = (context.mode or "agent").strip().lower()
    mode_key = {
        "auto": "agent",
        "build": "agent",
        "mutate": "agent",
    }.get(mode_key, mode_key)
    base_mode_hints = _MODE_HINTS.get(mode_key, _MODE_HINTS["agent"]).strip()
    autonomy_level = _normalize_autonomy_preference(
        getattr(context.autonomy_resolution, "effective_level", None),
        default="balanced",
    )
    autonomy_block = {
        "careful": """\
## Autonomy Behavior: Careful
- Prefer the smallest safe next step.
- Ask focused questions before ambiguous edits or irreversible actions.
- Summarize what you completed and surface uncertainties before stopping.
""",
        "balanced": """\
## Autonomy Behavior: Balanced
- Match the current default DAN operating style.
- Move forward on clear next steps, but avoid unnecessary extra work.
""",
        "aggressive": """\
## Autonomy Behavior: Aggressive
- Keep pushing the task forward with minimal blocking questions.
- State key assumptions briefly, then continue with the next concrete step.
- Before stopping, self-review whether verification, testing, or one obvious follow-up should be done now.
""",
    }[autonomy_level].strip()
    return f"{base_mode_hints}\n\n{autonomy_block}"


async def _resolve_interaction_policy_module(
    context: PromptContext,
) -> ResolvedPromptModule | None:
    return ResolvedPromptModule(
        module_id="interaction_policy",
        layer="interaction_policy",
        priority=10,
        content=_resolve_interaction_policy_module_content(context),
    )


async def _resolve_surface_presentation_module(
    context: PromptContext,
) -> ResolvedPromptModule | None:
    return ResolvedPromptModule(
        module_id="surface_presentation",
        layer="surface_presentation",
        priority=10,
        content=_resolve_surface_hints(context.surface, context.model),
    )


async def _resolve_research_specializer_module(
    context: PromptContext,
) -> ResolvedPromptModule | None:
    if not context.precomputed_hint_flags.get("research_specializer", False):
        return None
    content = _RESEARCH_REPORT_PROMPT_HINT.strip()
    detail_body = _RESEARCH_REPORT_PROMPT_DETAIL.strip()
    if context.tools_available:
        content = (
            f"{content}\n"
            "\nAdditional prompt detail is available via `load_prompt_detail` with "
            "detail_id=`prompt:research_specializer:full` if you need more guidance."
        )
    return ResolvedPromptModule(
        module_id="research_specializer",
        layer="task_specializer",
        priority=20,
        content=content,
        detail_id="prompt:research_specializer:full",
        detail_body=detail_body,
    )


async def _resolve_exploration_specializer_module(
    context: PromptContext,
) -> ResolvedPromptModule | None:
    if not context.precomputed_hint_flags.get("exploration_specializer", False):
        return None
    content = """\
## Exploration & Understanding Behavior
- Start by mapping the relevant area before proposing changes.
- Prefer targeted reads/searches that build a coherent model of the current code or system.
- Name the key files, symbols, or execution flow you inspected and how they connect.
- If you are still uncertain, say what remains unclear instead of guessing.
""".strip()
    detail_body = """\
### Exploration workflow
1. Start broad enough to find the right area: use targeted search or directory inspection to locate the relevant files, symbols, or subsystems.
2. Narrow to the smallest set of sources that explain the behavior. Prefer reading targeted ranges over dumping whole files.
3. Explain the current behavior in terms of ownership, data flow, and dependencies: what calls what, where state enters/leaves, and which modules are responsible.
4. For "where is X handled?" or "how does Y work?" questions, answer from inspected evidence instead of intuition.
5. If a fix or refactor is eventually needed, summarize the current structure first so later edits are grounded in the real design.
""".strip()
    if context.tools_available:
        content = (
            f"{content}\n"
            "\nAdditional prompt detail is available via `load_prompt_detail` with "
            "detail_id=`prompt:exploration_specializer:full` if you need more guidance."
        )
    return ResolvedPromptModule(
        module_id="exploration_specializer",
        layer="task_specializer",
        priority=30,
        content=content,
        detail_id="prompt:exploration_specializer:full",
        detail_body=detail_body,
    )


def build_default_prompt_module_resolver() -> PromptModuleResolver:
    resolver = PromptModuleResolver()
    resolver.register(PromptModule(
        module_id="interaction_policy",
        layer="interaction_policy",
        priority=10,
        resolver=_resolve_interaction_policy_module,
    ))
    resolver.register(PromptModule(
        module_id="surface_presentation",
        layer="surface_presentation",
        priority=10,
        resolver=_resolve_surface_presentation_module,
    ))
    resolver.register(PromptModule(
        module_id="research_specializer",
        layer="task_specializer",
        priority=20,
        resolver=_resolve_research_specializer_module,
    ))
    resolver.register(PromptModule(
        module_id="exploration_specializer",
        layer="task_specializer",
        priority=30,
        resolver=_resolve_exploration_specializer_module,
    ))
    return resolver


DEFAULT_PROMPT_MODULE_RESOLVER = build_default_prompt_module_resolver()

_PROMPT_DETAIL_BODIES = {
    "prompt:research_specializer:full": _RESEARCH_REPORT_PROMPT_DETAIL.strip(),
    "prompt:exploration_specializer:full": (
        "### Exploration workflow\n"
        "1. Start broad enough to find the right area: use targeted search or directory inspection to locate the relevant files, symbols, or subsystems.\n"
        "2. Narrow to the smallest set of sources that explain the behavior. Prefer reading targeted ranges over dumping whole files.\n"
        "3. Explain the current behavior in terms of ownership, data flow, and dependencies: what calls what, where state enters/leaves, and which modules are responsible.\n"
        "4. For \"where is X handled?\" or \"how does Y work?\" questions, answer from inspected evidence instead of intuition.\n"
        "5. If a fix or refactor is eventually needed, summarize the current structure first so later edits are grounded in the real design."
    ),
}


def get_prompt_detail_body(detail_id: str) -> str | None:
    return _PROMPT_DETAIL_BODIES.get(str(detail_id or "").strip())


# ---------------------------------------------------------------------------
# Mode behavior hints
# ---------------------------------------------------------------------------

_MODE_HINTS = {
    "agent": """\
## Mode Behavior: Agent
- Apply the self-management loop above after each action.
- For tasks needing 3+ concrete actions, start with a brief numbered plan, then execute it.
- Do not stop at diagnosis if you can still implement and validate the fix yourself.
""",
    "plan": """\
## Mode Behavior: Plan
- Produce an explicit plan before any execution.
- Do not call tools, mutate workflows, or change files until the user approves.
- Include key assumptions, trade-offs, and open questions only when they materially affect the plan.
""",
    "ask": """\
## Mode Behavior: Ask
- Answer directly and stay read-only.
- Explain the current state, behavior, or options without making changes.
- Do not modify files, workflows, or other state in this mode.
""",
    "debug": """\
## Mode Behavior: Debug
- Start from the observed failure and narrow the most likely root cause.
- State the leading diagnostic hypothesis before proposing a fix.
- Prefer the smallest fix that matches the evidence, then explain how to validate it.
""",
}


def _resolve_mode_hints(mode: str | None) -> str:
    mode_key = (mode or "agent").strip().lower()
    mode_key = {
        "auto": "agent",
        "build": "agent",
        "mutate": "agent",
    }.get(mode_key, mode_key)
    return _MODE_HINTS.get(mode_key, _MODE_HINTS["agent"])


# ---------------------------------------------------------------------------
# Unified system prompt
# ---------------------------------------------------------------------------

UNIFIED_SYSTEM_PROMPT = """\
You are DAN, a personal AI assistant with full tool access. You help with anything: \
research, file operations, web search, computation, communication, workflow building.

**{current_date}**

{capability_reference}

## Rules — NON-NEGOTIABLE

1. NEVER fabricate live data (prices, dates, weather, scores). Call web_search.
2. NEVER summarize a file you haven't read. Call pdf_read or file_read first.
3. The current date is shown above. Use current_datetime only when you need the exact time or a specific timezone.
4. NEVER guess file contents or directory listings. Call the tool.
5. If a tool fails, tell the user what happened. Don't silently make something up.
6. If you can't do something, say so. Suggest what the user can do instead.
7. NEVER dump raw tool output to the user. Always summarize or extract the relevant facts. \
This applies to all tools — web_fetch pages, file_read contents, list_directory listings, \
shell_command output, pdf_read text. Present clean, structured answers, not raw data.
8. NEVER include image markdown (![alt](url)), navigation link blocks, or raw HTML in your response. \
Summarize the information from web pages; do not reproduce their markup.
9. Work autonomously. After each action, silently assess:
   (a) Did the last step succeed or fail?
   (b) If it failed, what specifically went wrong? Do not retry the identical action without changing something.
   (c) What is still needed to fully satisfy the user's request?
   (d) Is the next step clear enough to execute immediately, or should I state a brief plan first?
   Continue until the task is complete or you are genuinely blocked.
10. For multi-step tasks (3+ actions), state a brief numbered plan before the first action. Update it if the plan changes. For single-step tasks, act directly.
11. If the user's request is ambiguous and the next action is hard to reverse, ask focused clarifying questions before acting. Ask the minimum set together once.
12. If the request is ambiguous but the next step is reversible, choose the safest reasonable interpretation, state it briefly, and proceed.
13. When the user asks for a review, provide findings first. Do not patch, rewrite, or broaden scope unless the user also asks you to fix or implement.
14. Keep progress updates brief and action-oriented. Do not narrate internal deliberation or speculative reasoning.
15. If the user mentions a known project by name (listed in the context below), respond using project context and memory. Do NOT search externally unless explicitly asked to search online/externally.
16. When assumptions materially affect the result, state them briefly so the user can correct you.
17. For long files or documents, design the structure first and write incrementally. \
Use file_write in bounded chunks: first call mode='overwrite', later calls mode='append'. \
Do not dump an entire long file in one tool call.
18. For live/current claims, answer ONLY from retrieved tool evidence. If a fact is not in the tool results, say you could not verify it.
19. Prefer fetched page content over search snippets. If you only have snippets, say the answer is tentative or fetch more before concluding.
20. When web search results are numbered, cite them inline as [1], [2] and include markdown links to the source URLs when helpful.
21. If `list_directory` says a listing is partial/truncated, do NOT infer absence from the cutoff. Continue with `start_after` or narrow the listing with `glob_pattern` before concluding a file or directory is missing.

{module_hints}

{context_block}

{workflow_block}
"""

EMPTY_GRAPH_SUMMARY_PLACEHOLDER = (
    "Workflow is empty (0 nodes, 0 edges). Build from scratch."
)

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
        {"op": "add_edge", "source_id": "literature-kb", "source_port": "chunks",
         "target_id": "outline-planner", "target_port": "input"},
        {"op": "add_edge", "source_id": "data-summary", "source_port": "text",
         "target_id": "outline-planner", "target_port": "context"},
        {"op": "add_edge", "source_id": "outline-planner", "source_port": "text",
         "target_id": "research-interview", "target_port": "input"},
        {"op": "add_edge", "source_id": "research-interview", "source_port": "response",
         "target_id": "section-drafter", "target_port": "input"},
        {"op": "add_edge", "source_id": "review-gate", "source_port": "done",
         "target_id": "latex-assembler", "target_port": "input"},
        {"op": "add_edge", "source_id": "latex-assembler", "source_port": "text",
         "target_id": "check-latex-deps", "target_port": "input"},
        {"op": "add_edge", "source_id": "latex-assembler", "source_port": "text",
         "target_id": "verify-citations", "target_port": "input"},
        {"op": "add_edge", "source_id": "latex-assembler", "source_port": "text",
         "target_id": "compile-latex", "target_port": "content"},
        {"op": "add_edge", "source_id": "paper-config", "source_port": "title",
         "target_id": "compile-latex", "target_port": "title"},
        {"op": "add_edge", "source_id": "latex-assembler", "source_port": "text",
         "target_id": "save-paper", "target_port": "content"},
        {"op": "add_edge", "source_id": "paper-config", "source_port": "title",
         "target_id": "save-paper", "target_port": "title"},
        {"op": "add_edge", "source_id": "compile-latex", "source_port": "pdf_path",
         "target_id": "save-paper", "target_port": "pdf_path"},
        {"op": "add_edge", "source_id": "save-paper", "source_port": "title",
         "target_id": "package-submission", "target_port": "title"},
        {"op": "add_edge", "source_id": "save-paper", "source_port": "tex_path",
         "target_id": "package-submission", "target_port": "tex_path"},
        {"op": "add_edge", "source_id": "save-paper", "source_port": "bib_path",
         "target_id": "package-submission", "target_port": "bib_path"},
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
