"""Graph-aware chat manager — LLM conversations with workflow context."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field

from dan.models.graph import Graph
from dan.providers import CompletionResult, StreamChunk
from dan.providers.registry import ProviderRegistry
from dan.server.graph_mutator import GraphMutator, MutationPlan
from dan.server.graph_store import GraphStore

__all__ = [
    "NodeSummary",
    "EdgeSummary",
    "GraphSummary",
    "ChatTokenEvent",
    "ChatCompleteEvent",
    "ChatErrorEvent",
    "ChatMutationEvent",
    "ChatStreamEvent",
    "MUTATION_TOOL_SCHEMA",
    "ChatManager",
    "build_graph_summary",
    "serialize_for_prompt",
    "compute_graph_revision",
]

logger = logging.getLogger(__name__)

NODE_TYPES: list[str] = [
    "llm_operator",
    "tool_operator",
    "code_operator",
    "rag_operator",
    "input",
    "if_else",
    "gate",
    "while_loop",
    "for_each",
    "reduce",
    "router",
    "human_in_the_loop",
    "validator",
    "composite",
]

EDGE_TYPES: list[str] = ["data", "control", "context"]

MUTATION_TOOL_SCHEMA: dict[str, Any] = {
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
                        "type": "object",
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": [
                                    "add_node",
                                    "remove_node",
                                    "edit_node",
                                    "add_edge",
                                    "remove_edge",
                                    "set_position",
                                ],
                            },
                            "node_type": {"type": "string"},
                            "name": {"type": "string"},
                            "config": {"type": "object"},
                            "node_id": {"type": "string"},
                            "updates": {"type": "object"},
                            "edge_type": {"type": "string", "default": "data"},
                            "source_id": {"type": "string"},
                            "source_port": {"type": "string"},
                            "target_id": {"type": "string"},
                            "target_port": {"type": "string"},
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                        },
                        "required": ["op"],
                    },
                },
            },
            "required": ["description", "operations"],
        },
    },
}

SYSTEM_PROMPT_TEMPLATE = """\
You are a graph-aware assistant for DAN (Deep Agent Network), an agentic \
workflow builder. You help the user understand, debug, and improve their \
workflow graphs.

## Available node types
{node_types}

## Available edge types
- data: carries structured data between ports
- control: encodes routing / flow-control signals (branching, looping)
- context: connects a node to a shared-context key (read/write/append)

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
- Be concise. Use the node/edge vocabulary above.
"""


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
    graph_revision: str
    revision_mismatch: bool = False


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
    graph_revision: str
    revision_mismatch: bool = False


ChatStreamEvent = ChatTokenEvent | ChatCompleteEvent | ChatErrorEvent | ChatMutationEvent


# ---------------------------------------------------------------------------
# Graph revision hash
# ---------------------------------------------------------------------------


def compute_graph_revision(graph_dict: dict) -> str:
    """Stable SHA-256 hash of the graph for concurrency checks."""
    canonical = json.dumps(graph_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Graph summary builder
# ---------------------------------------------------------------------------


def build_graph_summary(graph: Graph, workflow_id: str) -> GraphSummary:
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


class ChatManager:
    def __init__(
        self,
        provider_registry: ProviderRegistry,
        graph_store: GraphStore,
    ) -> None:
        self._providers = provider_registry
        self._graph_store = graph_store
        self._chat_model = os.environ.get("DAN_CHAT_MODEL", "claude-sonnet-4-6")

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

            messages = self._build_messages(summary, message, history)

            provider = self._providers.resolve(self._chat_model)
            stream: AsyncIterator[StreamChunk] = await provider.stream(
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
                    token_usage = chunk.usage or {}

            yield ChatCompleteEvent(
                message_id=message_id,
                content=final_content,
                token_usage=token_usage,
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

            messages = self._build_messages(summary, message, history)
            provider = self._providers.resolve(self._chat_model)
            message_id = uuid.uuid4().hex[:12]

            try:
                result: CompletionResult = await provider.complete(
                    messages=messages,
                    model=self._chat_model,
                    temperature=0.7,
                    tools=[MUTATION_TOOL_SCHEMA],
                    tool_choice="auto",
                )
            except Exception as exc:
                logger.debug(
                    "Tool-calling complete() failed (%s), falling back to stream",
                    exc,
                )
                async for event in self._stream_with_json_fallback(
                    provider, messages, message_id,
                    revision, revision_mismatch, graph_dict,
                ):
                    yield event
                return

            mutation_data = self._extract_mutation_from_result(result)
            if mutation_data is not None:
                plan = MutationPlan.model_validate({
                    "operations": mutation_data.get("operations", []),
                    "description": mutation_data.get("description", ""),
                    "reasoning": mutation_data.get("reasoning", ""),
                    "base_graph_revision": revision,
                })
                dry_result = GraphMutator().dry_run(
                    graph_dict, plan, current_revision=revision,
                )
                yield ChatMutationEvent(
                    message_id=message_id,
                    content=mutation_data.get("reasoning", result.text or ""),
                    mutation_plan=plan.model_dump(),
                    dry_run_result=dry_result.model_dump(),
                    token_usage=result.usage or {},
                    graph_revision=revision,
                    revision_mismatch=revision_mismatch,
                )
                return

            content = result.text or ""
            if content:
                yield ChatTokenEvent(delta=content, accumulated=content)
            yield ChatCompleteEvent(
                message_id=message_id,
                content=content,
                token_usage=result.usage or {},
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
    ) -> AsyncIterator[ChatStreamEvent]:
        stream: AsyncIterator[StreamChunk] = await provider.stream(
            messages=messages,
            model=self._chat_model,
            temperature=0.7,
        )

        final_content = ""
        token_usage: dict[str, int] = {}

        async for chunk in stream:
            yield ChatTokenEvent(
                delta=chunk.delta,
                accumulated=chunk.accumulated,
            )
            if chunk.done:
                final_content = chunk.accumulated
                token_usage = chunk.usage or {}

        mutation_data = _try_parse_mutation_json(final_content)
        if mutation_data is not None:
            try:
                plan = MutationPlan.model_validate({
                    "operations": mutation_data.get("operations", []),
                    "description": mutation_data.get("description", ""),
                    "reasoning": mutation_data.get("reasoning", ""),
                    "base_graph_revision": revision,
                })
                dry_result = GraphMutator().dry_run(
                    graph_dict, plan, current_revision=revision,
                )
                yield ChatMutationEvent(
                    message_id=message_id,
                    content=mutation_data.get("reasoning", ""),
                    mutation_plan=plan.model_dump(),
                    dry_run_result=dry_result.model_dump(),
                    token_usage=token_usage,
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
    ) -> list[dict[str, str]]:
        graph_text = serialize_for_prompt(summary)
        node_type_list = ", ".join(NODE_TYPES)

        system_content = SYSTEM_PROMPT_TEMPLATE.format(
            node_types=node_type_list,
            graph_summary=graph_text,
        )

        messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        return messages
