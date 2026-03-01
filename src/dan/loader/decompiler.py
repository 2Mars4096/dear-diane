"""Decompile a ``dan_graph_v1`` Graph into markdown agent + workflow files."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from dan.loader.compiler import DEFAULT_INPUT_PORT, DEFAULT_OUTPUT_PORTS
from dan.loader.diagnostics import DecompileResult, Diagnostic
from dan.models.control_flow import (
    CompositeNode,
    ForEachNode,
    GateNode,
    HumanInTheLoopNode,
    InputNode,
    RouterNode,
)
from dan.models.edges import ContextEdge, ControlEdge, DataEdge
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator, LLMOperator, NodeBase, ToolOperator

_SUPPORTED_NODE_TYPES = frozenset({
    "llm_operator", "tool_operator", "code_operator",
    "human_in_the_loop", "router", "gate", "for_each",
    "composite", "input",
})

_SCHEMA_TO_TYPE: dict[str, str] = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}

_COMPILER_DEFAULT_INPUT_SCHEMA: dict[str, Any] = {"type": "object"}
_COMPILER_DEFAULT_OUTPUT_SCHEMA: dict[str, Any] = {"type": "string"}


def decompile_to_markdown(
    graph: Graph,
    output_dir: str | Path,
) -> DecompileResult:
    ctx = _DecompileContext(graph, Path(output_dir))
    ctx.run()
    return DecompileResult(files=ctx.files, diagnostics=ctx.diagnostics)


class _DecompileContext:
    def __init__(self, graph: Graph, output_dir: Path) -> None:
        self.graph = graph
        self.output_dir = output_dir
        self.files: list[Path] = []
        self.diagnostics: list[Diagnostic] = []
        self.node_map: dict[str, NodeBase] = {n.id: n for n in graph.nodes}
        self.agent_filenames: dict[str, str] = {}
        self._used_filenames: set[str] = {"workflow.md"}
        self._foreach_body_agents: dict[str, str] = {}

    def run(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._assign_filenames()
        self._prepare_foreach_bodies()

        for node in self.graph.nodes:
            if node.node_type in ("input", "gate", "for_each", "parallel_subagents", "orchestrator"):
                continue
            self._write_agent_file(node)

        self._write_subgraph_agents()
        self._write_workflow_file()

    def _assign_filenames(self) -> None:
        for node in self.graph.nodes:
            if node.node_type in ("input", "gate", "for_each", "parallel_subagents", "orchestrator"):
                continue
            self._allocate_filename(node.id)

    def _allocate_filename(self, node_id: str) -> str:
        if node_id in self.agent_filenames:
            return self.agent_filenames[node_id]
        base = _slugify(node_id)
        filename = f"{base}.md"
        if filename in self._used_filenames:
            counter = 2
            while f"{base}_{counter}.md" in self._used_filenames:
                counter += 1
            filename = f"{base}_{counter}.md"
        self._used_filenames.add(filename)
        self.agent_filenames[node_id] = filename
        return filename

    def _prepare_foreach_bodies(self) -> None:
        """For ForEach nodes with multi-node bodies, synthesize a composite
        agent name so the ``| each()`` line can reference it."""
        for node in self.graph.nodes:
            if node.node_type != "for_each":
                continue
            body_key = getattr(node, "body_graph", "")
            body_graph = self.graph.sub_graphs.get(body_key)
            if not body_graph:
                continue
            real_nodes = [n for n in body_graph.nodes if n.node_type != "input"]
            if len(real_nodes) <= 1:
                continue

            composite_id = f"{node.id}__body_composite"
            self._allocate_filename(composite_id)
            self._foreach_body_agents[node.id] = composite_id

    def _write_subgraph_agents(self) -> None:
        """Write agent files for nodes inside sub-graphs that are referenced
        from composite agent files or synthesised foreach composites."""
        written: set[str] = set()
        for node in self.graph.nodes:
            if isinstance(node, CompositeNode):
                sub = self.graph.sub_graphs.get(node.body_graph)
                if sub:
                    self._write_subgraph_node_files(sub, written)
            elif node.node_type == "for_each":
                body_key = getattr(node, "body_graph", "")
                sub = self.graph.sub_graphs.get(body_key)
                if sub:
                    self._write_subgraph_node_files(sub, written)
                    if node.id in self._foreach_body_agents:
                        self._write_foreach_composite(node, sub)
            elif node.node_type == "parallel_subagents":
                for branch_key in getattr(node, "branch_graphs", []):
                    sub = self.graph.sub_graphs.get(branch_key)
                    if sub:
                        self._write_subgraph_node_files(sub, written)
            elif node.node_type == "orchestrator":
                for sub_key in getattr(node, "teams", {}).values():
                    sub = self.graph.sub_graphs.get(sub_key)
                    if sub:
                        self._write_subgraph_node_files(sub, written)

    def _write_subgraph_node_files(self, sub_graph: Graph, written: set[str]) -> None:
        for sn in sub_graph.nodes:
            if sn.node_type == "input" or sn.id in written:
                continue
            written.add(sn.id)
            self._allocate_filename(sn.id)
            self._write_agent_file(sn)
            if isinstance(sn, CompositeNode):
                inner = self.graph.sub_graphs.get(sn.body_graph)
                if not inner:
                    inner = sub_graph.sub_graphs.get(sn.body_graph)
                if inner:
                    self._write_subgraph_node_files(inner, written)

    def _write_foreach_composite(self, fe_node: NodeBase, body_graph: Graph) -> None:
        """Write a synthetic composite agent file wrapping a multi-node
        ForEach body so the ``| each()`` line can reference one agent."""
        composite_id = self._foreach_body_agents[fe_node.id]
        real_nodes = [n for n in body_graph.nodes if n.node_type != "input"]

        parts: list[str] = []
        parts.append("---")
        parts.append("type: composite")
        parts.append("---")
        parts.append("")

        parts.append("## Agents")
        parts.append("")
        for sn in real_nodes:
            fname = self.agent_filenames.get(sn.id, f"{_slugify(sn.id)}.md")
            parts.append(f"- [{sn.id}]({fname})")
        parts.append("")

        flow_lines = _build_flow_lines(body_graph, set())
        if flow_lines:
            parts.append("## Flow")
            parts.append("")
            parts.extend(flow_lines)
            parts.append("")

        filename = self.agent_filenames[composite_id]
        path = self.output_dir / filename
        path.write_text("\n".join(parts), encoding="utf-8")
        self.files.append(path)

    def _write_agent_file(self, node: NodeBase) -> None:
        filename = self.agent_filenames.get(node.id)
        if filename is None:
            return
        path = self.output_dir / filename
        if path.exists():
            return
        content = self._render_agent(node)
        path.write_text(content, encoding="utf-8")
        self.files.append(path)

    def _render_agent(self, node: NodeBase) -> str:
        parts: list[str] = []

        fm = self._build_frontmatter(node)
        if fm:
            parts.append("---")
            parts.append(yaml.dump(fm, default_flow_style=False, sort_keys=False).rstrip())
            parts.append("---")
            parts.append("")

        ports_block = self._render_ports(node)
        if ports_block:
            parts.append(ports_block)
            parts.append("")

        body = self._render_body(node)
        if body:
            parts.append(body)
            parts.append("")

        return "\n".join(parts)

    def _build_frontmatter(self, node: NodeBase) -> dict[str, Any]:
        fm: dict[str, Any] = {}

        if isinstance(node, LLMOperator):
            fm["type"] = "llm"
            if node.model:
                fm["model"] = node.model
            if node.temperature != 0.7:
                fm["temperature"] = node.temperature
            if node.max_tokens is not None:
                fm["max_tokens"] = node.max_tokens
            if node.output_json_schema:
                fm["output_schema"] = node.output_json_schema
        elif isinstance(node, ToolOperator):
            fm["type"] = "tool"
            fm["tool_id"] = node.tool_id
            if node.tool_config:
                fm["tool_config"] = dict(node.tool_config)
        elif isinstance(node, CodeOperator):
            fm["type"] = "code"
            if node.language != "python":
                fm["language"] = node.language
        elif isinstance(node, HumanInTheLoopNode):
            fm["type"] = "human"
            if node.timeout_seconds is not None:
                fm["timeout_seconds"] = node.timeout_seconds
            if node.default_action is not None:
                fm["default_action"] = node.default_action
        elif isinstance(node, RouterNode):
            fm["type"] = "router"
            fm["model"] = node.model
            if node.route_descriptions:
                fm["route_descriptions"] = dict(node.route_descriptions)
        elif isinstance(node, CompositeNode):
            fm["type"] = "composite"
        else:
            fm["type"] = node.node_type
            self.diagnostics.append(Diagnostic(
                level="warning",
                message=f"Unsupported node type '{node.node_type}' for '{node.id}'; emitting stub",
            ))

        if node.retry_policy is not None:
            rp = node.retry_policy.model_dump(mode="json", exclude_defaults=True)
            if rp:
                fm["retry_policy"] = rp

        return fm

    def _render_ports(self, node: NodeBase) -> str:
        lines: list[str] = []
        default_out = DEFAULT_OUTPUT_PORTS.get(node.node_type)

        input_ports = list(node.input_ports)
        if (
            len(input_ports) == 1
            and input_ports[0].name == DEFAULT_INPUT_PORT
            and input_ports[0].json_schema == _COMPILER_DEFAULT_INPUT_SCHEMA
            and not input_ports[0].required
        ):
            input_ports = []

        output_ports = list(node.output_ports)
        if (
            len(output_ports) == 1
            and output_ports[0].name == default_out
            and output_ports[0].json_schema == _COMPILER_DEFAULT_OUTPUT_SCHEMA
        ):
            output_ports = []

        if input_ports:
            port_strs = [_port_to_str(p.name, p.json_schema) for p in input_ports]
            lines.append(f"> Accepts: {', '.join(port_strs)}")
        if output_ports:
            port_strs = [_port_to_str(p.name, p.json_schema) for p in output_ports]
            lines.append(f"> Returns: {', '.join(port_strs)}")
        return "\n".join(lines)

    def _render_body(self, node: NodeBase) -> str:
        parts: list[str] = []

        if isinstance(node, LLMOperator):
            if node.system_prompt:
                parts.append("## System")
                parts.append("")
                parts.append(node.system_prompt)
                parts.append("")
            if node.prompt_template:
                parts.append(node.prompt_template)
        elif isinstance(node, CodeOperator):
            lang = node.language or "python"
            parts.append(f"```{lang}")
            parts.append(node.code)
            parts.append("```")
        elif isinstance(node, HumanInTheLoopNode):
            if node.prompt:
                parts.append(node.prompt)
        elif isinstance(node, CompositeNode):
            sub_graph = self.graph.sub_graphs.get(node.body_graph)
            if sub_graph:
                parts.extend(self._render_composite_body(sub_graph))
            else:
                parts.append(f"<!-- UNSUPPORTED: missing sub-graph '{node.body_graph}' -->")
                self.diagnostics.append(Diagnostic(
                    level="warning",
                    message=f"Composite '{node.id}' references missing sub-graph '{node.body_graph}'",
                ))
        elif node.node_type not in _SUPPORTED_NODE_TYPES:
            parts.append(f"<!-- UNSUPPORTED: node_type \"{node.node_type}\" — manual conversion needed -->")

        return "\n".join(parts)

    def _render_composite_body(self, sub_graph: Graph) -> list[str]:
        parts: list[str] = []
        sub_nodes = [n for n in sub_graph.nodes if n.node_type != "input"]
        if sub_nodes:
            parts.append("## Agents")
            parts.append("")
            for sn in sub_nodes:
                fname = self.agent_filenames.get(sn.id, f"{_slugify(sn.id)}.md")
                parts.append(f"- [{sn.id}]({fname})")
            parts.append("")

        flow_lines = _build_flow_lines(sub_graph, set())
        if flow_lines:
            parts.append("## Flow")
            parts.append("")
            parts.extend(flow_lines)
        return parts

    def _write_workflow_file(self) -> None:
        path = self.output_dir / "workflow.md"
        parts: list[str] = []

        fm: dict[str, Any] = {}
        if self.graph.metadata.name:
            fm["name"] = self.graph.metadata.name
        if self.graph.metadata.description:
            fm["description"] = self.graph.metadata.description
        fm["format_version"] = 1
        if self.graph.metadata.tags:
            fm["tags"] = list(self.graph.metadata.tags)

        parts.append("---")
        parts.append(yaml.dump(fm, default_flow_style=False, sort_keys=False).rstrip())
        parts.append("---")
        parts.append("")

        agent_nodes = [
            n for n in self.graph.nodes
            if n.id in self.agent_filenames
        ]
        foreach_composites = list(self._foreach_body_agents.values())
        if agent_nodes or foreach_composites:
            parts.append("## Agents")
            parts.append("")
            for node in agent_nodes:
                fname = self.agent_filenames[node.id]
                parts.append(f"- [{node.id}]({fname})")
            for cid in foreach_composites:
                fname = self.agent_filenames[cid]
                parts.append(f"- [{cid}]({fname})")
            parts.append("")

        gate_ids = {n.id for n in self.graph.nodes if n.node_type == "gate"}
        flow_lines = _build_flow_lines(
            self.graph, gate_ids,
            foreach_body_agents=self._foreach_body_agents,
        )
        if flow_lines:
            parts.append("## Flow")
            parts.append("")
            parts.extend(flow_lines)
            parts.append("")

        if self.graph.shared_context:
            parts.append("## Context")
            parts.append("")
            for ctx in self.graph.shared_context:
                desc = f": {ctx.description}" if ctx.description else ""
                parts.append(f"- {ctx.key}{desc}")
            parts.append("")

        path.write_text("\n".join(parts), encoding="utf-8")
        self.files.append(path)


def _build_flow_lines(
    graph: Graph,
    gate_ids: set[str],
    *,
    foreach_body_agents: dict[str, str] | None = None,
) -> list[str]:
    lines: list[str] = []
    node_map = {n.id: n for n in graph.nodes}
    data_edges = [e for e in graph.edges if isinstance(e, DataEdge)]
    body_map = foreach_body_agents or {}

    emitted_edges: set[str] = set()

    for node in graph.nodes:
        if node.node_type == "for_each":
            line = _decompile_foreach(node, graph, data_edges, emitted_edges, body_map)
            if line:
                lines.append(line)

    for node in graph.nodes:
        if node.node_type == "parallel_subagents":
            line = _decompile_parallel(node, graph, data_edges, emitted_edges)
            if line:
                lines.append(line)

    for node in graph.nodes:
        if node.node_type != "gate":
            continue
        gate_mode = getattr(node, "gate_mode", "if_else")
        if gate_mode == "while":
            line = _decompile_while_gate(node, graph, data_edges, emitted_edges)
            if line:
                lines.append(line)
        elif gate_mode == "if_else":
            line = _decompile_if_gate(node, graph, data_edges, emitted_edges)
            if line:
                lines.append(line)

    out_edges: dict[str, list[DataEdge]] = defaultdict(list)
    in_edges: dict[str, list[DataEdge]] = defaultdict(list)
    for edge in data_edges:
        if edge.id in emitted_edges:
            continue
        out_edges[edge.source_node_id].append(edge)
        in_edges[edge.target_node_id].append(edge)

    skip_nodes = gate_ids | {
        n.id for n in graph.nodes if n.node_type in ("input", "for_each", "parallel_subagents", "orchestrator")
    }
    visited: set[str] = set()

    chain_heads = [
        n.id for n in graph.nodes
        if n.id not in skip_nodes
        and n.id not in visited
        and not any(
            e.source_node_id not in skip_nodes
            for e in in_edges.get(n.id, [])
        )
    ]

    for head in chain_heads:
        chain = _build_chain(head, out_edges, in_edges, skip_nodes, visited, node_map)
        if len(chain) >= 2:
            lines.append(" → ".join(chain))
            visited.update(chain)

    for edge in data_edges:
        if edge.id in emitted_edges:
            continue
        if edge.source_node_id in skip_nodes or edge.target_node_id in skip_nodes:
            continue
        if edge.source_node_id in visited and edge.target_node_id in visited:
            continue
        src_port = _port_suffix(edge.source_port, node_map.get(edge.source_node_id))
        tgt_port = _port_suffix(edge.target_port, node_map.get(edge.target_node_id), is_input=True)
        lines.append(f"{edge.source_node_id}{src_port} → {edge.target_node_id}{tgt_port}")

    for edge in graph.edges:
        if isinstance(edge, (ControlEdge, ContextEdge)) and edge.id not in emitted_edges:
            lines.append(f"# <!-- SKIPPED: {edge.edge_type} {edge.source_node_id} → {edge.target_node_id} -->")

    return lines


def _decompile_foreach(
    node: NodeBase,
    graph: Graph,
    data_edges: list[DataEdge],
    emitted: set[str],
    body_map: dict[str, str],
) -> str | None:
    body_key = getattr(node, "body_graph", "")
    body_graph = graph.sub_graphs.get(body_key)
    if not body_graph or not body_graph.nodes:
        return None

    if node.id in body_map:
        body_agent = body_map[node.id]
    else:
        real_nodes = [n for n in body_graph.nodes if n.node_type != "input"]
        if not real_nodes:
            return None
        body_agent = real_nodes[0].id

    parallel = getattr(node, "parallelism", 1)

    source_agent = None
    for edge in data_edges:
        if edge.target_node_id == node.id:
            source_agent = edge.source_node_id
            emitted.add(edge.id)
            break

    if source_agent is None:
        return None

    for edge in data_edges:
        if edge.source_node_id == node.id:
            emitted.add(edge.id)

    par_str = f", parallel: {parallel}" if parallel > 1 else ""
    return f"{source_agent} | each({body_agent}{par_str})"


def _decompile_parallel(
    node: NodeBase,
    graph: Graph,
    data_edges: list[DataEdge],
    emitted: set[str],
) -> str | None:
    branch_graphs = getattr(node, "branch_graphs", [])
    if not branch_graphs:
        return None

    branch_agents: list[str] = []
    for sub_key in branch_graphs:
        sub = graph.sub_graphs.get(sub_key)
        if not sub or not sub.nodes:
            continue
        real_nodes = [n for n in sub.nodes if n.node_type != "input"]
        if not real_nodes:
            continue
        branch_agents.append(real_nodes[0].id)

    if not branch_agents:
        return None

    parallel = getattr(node, "parallelism", 1)
    merge = getattr(node, "merge_strategy", "append")
    merge_val = merge.value if hasattr(merge, "value") else str(merge)

    source_agent = None
    for edge in data_edges:
        if edge.target_node_id == node.id:
            source_agent = edge.source_node_id
            emitted.add(edge.id)
            break

    if source_agent is None:
        return None

    for edge in data_edges:
        if edge.source_node_id == node.id:
            emitted.add(edge.id)

    args_str = ", ".join(branch_agents)
    extra: list[str] = []
    if merge_val != "append":
        extra.append(f"merge: {merge_val}")
    if parallel > 1:
        extra.append(f"parallel: {parallel}")
    full_args = f"{args_str}, {', '.join(extra)}" if extra else args_str
    return f"{source_agent} | parallel({full_args})"


def _escape_json_for_flow_string(raw: str) -> str:
    """Escape JSON string for embedding in a double-quoted flow kwarg.

    Escapes backslash first, then double-quote. Handles control chars
    (\\n, \\r, \\t) via the backslash pass since json.dumps emits them escaped.
    """
    return raw.replace("\\", "\\\\").replace('"', '\\"')


def _invert_until_condition(condition: str) -> str:
    """Strip one layer of not (...) from gate.condition for loop until round-trip.

    The compiler stores ``not (cond)`` in gate.condition for loop(until: cond).
    This helper inverts that so decompiled markdown emits the original until
    expression. Handles: not (x) → x, not (not (x)) → not (x), and nested parens.
    Unclosed parens (e.g. ``not (x``): returns original unchanged.
    """
    s = condition.strip()
    m = re.match(r"^not\s*\(\s*", s)
    if not m:
        return s
    start = m.end()
    depth = 1
    i = start
    while i < len(s) and depth > 0:
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
        i += 1
    if depth == 0:
        if s[i:].strip():
            return s
        return s[start : i - 1].strip()
    return s


def _decompile_while_gate(
    gate: NodeBase,
    graph: Graph,
    data_edges: list[DataEdge],
    emitted: set[str],
) -> str | None:
    raw_condition = getattr(gate, "condition", "")
    until_condition = _invert_until_condition(raw_condition)
    max_iter = getattr(gate, "max_iterations", 10)
    state_schema = getattr(gate, "state_schema", None)
    state_defaults = getattr(gate, "state_defaults", None)

    source_agent = None
    body_agent = None

    for edge in data_edges:
        if edge.target_node_id == gate.id and edge.source_node_id != gate.id:
            back_edge = False
            for other in data_edges:
                if other.source_node_id == edge.source_node_id and other.target_node_id == gate.id:
                    for yet_another in data_edges:
                        if yet_another.source_node_id == gate.id and yet_another.target_node_id == edge.source_node_id:
                            back_edge = True
                            break
                if back_edge:
                    break

            if back_edge:
                body_agent = edge.source_node_id
            else:
                source_agent = edge.source_node_id

    if body_agent is None:
        for edge in data_edges:
            if edge.source_node_id == gate.id and edge.source_port == "continue":
                body_agent = edge.target_node_id
                break

    if source_agent is None or body_agent is None:
        return None

    for edge in data_edges:
        if edge.target_node_id == gate.id or edge.source_node_id == gate.id:
            emitted.add(edge.id)

    escaped_condition = until_condition.replace('"', '\\"')
    kwargs_parts: list[str] = [f'until: "{escaped_condition}"']
    if max_iter != 10:
        kwargs_parts.append(f"max: {max_iter}")
    if state_schema:
        raw = json.dumps(state_schema)
        escaped_json = _escape_json_for_flow_string(raw)
        kwargs_parts.append(f'state: "{escaped_json}"')
    if state_defaults:
        raw = json.dumps(state_defaults)
        escaped_json = _escape_json_for_flow_string(raw)
        kwargs_parts.append(f'defaults: "{escaped_json}"')
    return f"{source_agent} | loop({body_agent}, {', '.join(kwargs_parts)})"


def _decompile_if_gate(
    gate: NodeBase,
    graph: Graph,
    data_edges: list[DataEdge],
    emitted: set[str],
) -> str | None:
    condition = getattr(gate, "condition", "")

    source_agent = None
    then_agent = None
    else_agent = None

    for edge in data_edges:
        if edge.target_node_id == gate.id:
            source_agent = edge.source_node_id
            emitted.add(edge.id)
        if edge.source_node_id == gate.id:
            if edge.source_port == "true":
                then_agent = edge.target_node_id
            elif edge.source_port == "false":
                else_agent = edge.target_node_id
            emitted.add(edge.id)

    if source_agent is None or then_agent is None or else_agent is None:
        return None

    escaped_condition = condition.replace('"', '\\"')
    return f'{source_agent} | if("{escaped_condition}", then: {then_agent}, else: {else_agent})'


def _build_chain(
    start: str,
    out_edges: dict[str, list[DataEdge]],
    in_edges: dict[str, list[DataEdge]],
    skip: set[str],
    visited: set[str],
    node_map: dict[str, NodeBase],
) -> list[str]:
    chain = [start]
    current = start
    while True:
        outs = [e for e in out_edges.get(current, []) if e.target_node_id not in skip]
        if len(outs) != 1:
            break
        nxt = outs[0].target_node_id
        if nxt in visited or nxt == start:
            break
        ins = [e for e in in_edges.get(nxt, []) if e.source_node_id not in skip]
        if len(ins) != 1:
            break
        chain.append(nxt)
        current = nxt
    return chain


def _port_suffix(port_name: str, node: NodeBase | None, is_input: bool = False) -> str:
    if node is None:
        return f".{port_name}" if port_name else ""
    if is_input:
        if node.input_ports and port_name == node.input_ports[0].name:
            return ""
        if port_name == DEFAULT_INPUT_PORT:
            return ""
    else:
        default = DEFAULT_OUTPUT_PORTS.get(node.node_type, "result")
        if port_name == default:
            return ""
        if node.output_ports and port_name == node.output_ports[0].name:
            return ""
    return f".{port_name}"


def _port_to_str(name: str, schema: dict[str, Any] | None) -> str:
    if not schema:
        return name
    type_name = schema.get("type", "")
    if type_name in _SCHEMA_TO_TYPE:
        return f"{name} ({_SCHEMA_TO_TYPE[type_name]})"
    return name


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    slug = slug.strip("-")
    return slug or "node"
