"""Decompiler — converts a Graph model back into Python builder DSL code.

Produces an executable Python module string that, when run, reconstructs
the graph via the builder API. Supports lossless round-tripping.

Lossless criteria:
  - All ui, metadata, shared_context, artifact_refs preserved
  - ControlEdge and ContextEdge emitted (not just DataEdge)
  - Deterministic output ordering (topological, then alphabetical tie-break)
"""

from __future__ import annotations

import keyword
import re
from collections import defaultdict
from typing import Any

from dan.builder.compiler import default_input_port, default_output_port
from dan.models.control_flow import (
    CompositeNode,
    ForEachNode,
    GateNode,
    OrchestratorNode,
    ParallelSubagentsNode,
    ValidatorNode,
    WhileLoopNode,
)
from dan.models.nodes import RAGOperator
from dan.models.edges import ContextEdge, ControlEdge, DataEdge
from dan.models.graph import Graph


def decompile(graph: Graph) -> str:
    """Convert a Graph model into executable Python builder DSL code."""
    return _Decompiler(graph).generate()


def _to_var_name(node_id: str) -> str:
    """Convert a node ID to a valid Python variable name."""
    name = re.sub(r"[^0-9a-zA-Z_]", "_", node_id)
    if name and name[0].isdigit():
        name = f"n_{name}"
    if keyword.iskeyword(name) or name in ("input", "item"):
        name = f"{name}_node"
    return name or "node"


class _Decompiler:
    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        self.node_map = {n.id: n for n in graph.nodes}
        self.var_names: dict[str, str] = {}
        self._assign_var_names()

    def _assign_var_names(self) -> None:
        used: set[str] = set()
        for node in self.graph.nodes:
            base = _to_var_name(node.id)
            name = base
            counter = 2
            while name in used:
                name = f"{base}_{counter}"
                counter += 1
            used.add(name)
            self.var_names[node.id] = name

    def generate(self) -> str:
        lines: list[str] = []
        lines.append("from dan.builder import workflow")
        lines.append("from dan.builder.refs import NodeRef")
        lines.append("")

        wf_name = self.graph.metadata.name or "unnamed"
        desc_arg = ""
        if self.graph.metadata.description:
            desc_arg = f', description={self.graph.metadata.description!r}'
        tags_arg = ""
        if self.graph.metadata.tags:
            tags_arg = f", tags={self.graph.metadata.tags!r}"
        lines.append(f"wf = workflow({wf_name!r}{desc_arg}{tags_arg})")
        lines.append("")

        # Shared context declarations
        for ctx in self.graph.shared_context:
            schema_arg = ""
            if ctx.json_schema:
                schema_arg = f", json_schema={ctx.json_schema!r}"
            desc_arg = ""
            if ctx.description:
                desc_arg = f", description={ctx.description!r}"
            lines.append(f"wf.context({ctx.key!r}{schema_arg}{desc_arg})")
        for art in self.graph.artifact_refs:
            args = [repr(art.uri)]
            if art.content_hash is not None:
                args.append(f"content_hash={art.content_hash!r}")
            if art.media_type != "application/octet-stream":
                args.append(f"media_type={art.media_type!r}")
            if art.description:
                args.append(f"description={art.description!r}")
            lines.append(f"wf.artifact_ref({', '.join(args)})")
        if self.graph.shared_context or self.graph.artifact_refs:
            lines.append("")

        # Topological order
        ordered = self._topological_sort()

        # Detect simple chains for >> sugar
        chains = self._detect_chains()

        # Emit nodes
        emitted_in_chain: set[str] = set()
        for node_id in ordered:
            if node_id in emitted_in_chain:
                continue
            node = self.node_map[node_id]
            var = self.var_names[node_id]

            if isinstance(node, (WhileLoopNode, ForEachNode, CompositeNode, ParallelSubagentsNode, OrchestratorNode)):
                body_var = f"_{var}_body"
                node_lines = self._emit_subgraph_node(node, body_var)
                lines.extend(node_lines)
                # Create a NodeRef for wiring edges to/from this sub-graph node
                lines.append(
                    f"{var} = NodeRef({node.id!r}, {node.node_type!r}, wf)"
                )
            else:
                lines.append(f"{var} = {self._emit_node_call(node)}")
            lines.append("")

        # Emit >> chains (only for non-subgraph nodes)
        subgraph_node_ids = {
            n.id for n in self.graph.nodes
            if isinstance(n, (WhileLoopNode, ForEachNode, CompositeNode, ParallelSubagentsNode, OrchestratorNode))
        }
        emitted_chain_pairs: set[tuple[str, str]] = set()
        for chain in chains:
            filtered = [nid for nid in chain if nid not in subgraph_node_ids]
            if len(filtered) < 2:
                continue
            current_segment: list[str] = [filtered[0]]
            for nxt in filtered[1:]:
                prev = current_segment[-1]
                if self._is_default_chain_edge(prev, nxt):
                    current_segment.append(nxt)
                    emitted_chain_pairs.add((prev, nxt))
                else:
                    if len(current_segment) >= 2:
                        chain_str = " >> ".join(self.var_names[nid] for nid in current_segment)
                        lines.append(chain_str)
                    current_segment = [nxt]
            if len(current_segment) >= 2:
                chain_str = " >> ".join(self.var_names[nid] for nid in current_segment)
                lines.append(chain_str)
        if chains:
            lines.append("")

        # Emit explicit edges that aren't covered by >> chains
        for edge in self.graph.edges:
            if isinstance(edge, DataEdge):
                pair = (edge.source_node_id, edge.target_node_id)
                if pair in emitted_chain_pairs and self._is_default_data_edge(edge):
                    continue
                src_var = self.var_names.get(edge.source_node_id, edge.source_node_id)
                tgt_var = self.var_names.get(edge.target_node_id, edge.target_node_id)
                spread_arg = ", spread=True" if getattr(edge, "spread", False) else ""
                lines.append(
                    f'wf.edge({src_var}["{edge.source_port}"], {tgt_var}["{edge.target_port}"]{spread_arg})'
                )
            elif isinstance(edge, ControlEdge):
                lines.append(self._emit_control_edge(edge))
            elif isinstance(edge, ContextEdge):
                lines.append(self._emit_context_edge(edge))

        lines.append("")
        lines.append("graph = wf.build()")
        if self.graph.metadata.created_at is not None:
            lines.append(f"graph.metadata.created_at = {self.graph.metadata.created_at!r}")
        if self.graph.metadata.updated_at is not None:
            lines.append(f"graph.metadata.updated_at = {self.graph.metadata.updated_at!r}")
        for node in self.graph.nodes:
            if (
                node.position.x != 0.0
                or node.position.y != 0.0
                or node.ui
                or node.metadata
            ):
                lines.append(f"_n = graph.node_by_id({node.id!r})")
                lines.append("if _n is not None:")
                if node.position.x != 0.0:
                    lines.append(f"    _n.position.x = {node.position.x!r}")
                if node.position.y != 0.0:
                    lines.append(f"    _n.position.y = {node.position.y!r}")
                if node.ui:
                    lines.append(f"    _n.ui = {node.ui!r}")
                if node.metadata:
                    lines.append(f"    _n.metadata = {node.metadata!r}")
        lines.append("")
        return "\n".join(lines)

    def _emit_node_call(self, node: Any) -> str:
        """Generate the wf.xxx(...) call for a non-subgraph node."""
        nt = node.node_type

        args: list[str] = [repr(node.id)]
        kwargs: list[str] = []

        if node.name and node.name != node.id:
            kwargs.append(f"name={node.name!r}")
        if node.description:
            kwargs.append(f"description={node.description!r}")

        if nt == "llm_operator":
            method = "wf.llm"
            if node.model:
                kwargs.append(f"model={node.model!r}")
            if node.prompt_template:
                kwargs.append(f"prompt={node.prompt_template!r}")
            if node.system_prompt:
                kwargs.append(f"system_prompt={node.system_prompt!r}")
            if node.temperature != 0.7:
                kwargs.append(f"temperature={node.temperature!r}")
            if node.max_tokens is not None:
                kwargs.append(f"max_tokens={node.max_tokens!r}")
            if node.output_json_schema is not None:
                kwargs.append(f"output_schema={node.output_json_schema!r}")
        elif nt == "tool_operator":
            method = "wf.tool"
            kwargs.append(f"tool_id={node.tool_id!r}")
            if node.tool_config:
                kwargs.append(f"tool_config={node.tool_config!r}")
        elif nt == "code_operator":
            method = "wf.code"
            kwargs.append(f"code={node.code!r}")
            if node.language != "python":
                kwargs.append(f"language={node.language!r}")
        elif nt == "if_else":
            method = "wf.if_else"
            kwargs.append(f"condition={node.condition!r}")
        elif nt == "gate":
            method = "wf.gate"
            kwargs.append(f"condition={node.condition!r}")
            if node.gate_mode != "if_else":
                kwargs.append(f"gate_mode={node.gate_mode!r}")
            if node.max_iterations != 10:
                kwargs.append(f"max_iterations={node.max_iterations!r}")
            if getattr(node, "state_schema", None):
                kwargs.append(f"state_schema={node.state_schema!r}")
            if getattr(node, "state_defaults", None):
                kwargs.append(f"state_defaults={node.state_defaults!r}")
        elif nt == "reduce":
            method = "wf.reduce"
            kwargs.append(f"reducer={node.reducer!r}")
        elif nt == "router":
            method = "wf.router"
            kwargs.append(f"model={node.model!r}")
            kwargs.append(f"route_descriptions={node.route_descriptions!r}")
        elif nt == "human_in_the_loop":
            method = "wf.human_in_the_loop"
            if node.prompt:
                kwargs.append(f"prompt={node.prompt!r}")
            if node.timeout_seconds is not None:
                kwargs.append(f"timeout_seconds={node.timeout_seconds!r}")
            if node.default_action is not None:
                kwargs.append(f"default_action={node.default_action!r}")
        elif nt == "rag_operator":
            method = "wf.rag"
            kwargs.append(f"collection={node.collection!r}")
            if node.top_k != 5:
                kwargs.append(f"top_k={node.top_k!r}")
            if node.similarity_threshold is not None:
                kwargs.append(f"similarity_threshold={node.similarity_threshold!r}")
            if node.embedding_model:
                kwargs.append(f"embedding_model={node.embedding_model!r}")
            if node.vector_store_config:
                kwargs.append(f"vector_store_config={node.vector_store_config!r}")
            if node.query_template != "{query}":
                kwargs.append(f"query_template={node.query_template!r}")
            if not node.include_metadata:
                kwargs.append(f"include_metadata={node.include_metadata!r}")
            if node.rerank:
                kwargs.append(f"rerank={node.rerank!r}")
        elif nt == "validator":
            method = "wf.validator"
            if node.validation_rules:
                rules_data = [r.model_dump() for r in node.validation_rules]
                kwargs.append(f"rules={rules_data!r}")
            if node.on_failure != "route":
                kwargs.append(f"on_failure={node.on_failure!r}")
            if node.strict_mode:
                kwargs.append(f"strict_mode={node.strict_mode!r}")
        else:
            method = f"wf.llm"  # fallback

        # Explicit ports
        if node.input_ports:
            port_dicts = [self._serialize_input_port(p) for p in node.input_ports]
            if port_dicts:
                kwargs.append(f"input_ports={port_dicts!r}")
        if node.output_ports:
            port_dicts = [self._serialize_output_port(p) for p in node.output_ports]
            if port_dicts:
                kwargs.append(f"output_ports={port_dicts!r}")
        # Context declarations (for nodes that consume/produce context)
        if hasattr(node, "read_set") and node.read_set:
            kwargs.append(
                f"read_set={[self._serialize_context_decl(d) for d in node.read_set]!r}"
            )
        if hasattr(node, "write_set") and node.write_set:
            kwargs.append(
                f"write_set={[self._serialize_context_decl(d) for d in node.write_set]!r}"
            )

        all_args = ", ".join(args + kwargs)
        return f"{method}({all_args})"

    def _emit_subgraph_node(self, node: Any, body_var: str, indent: int = 0) -> list[str]:
        """Generate a context-manager block for a sub-graph node."""
        lines: list[str] = []
        nt = node.node_type
        pad = "    " * indent

        if nt == "while_loop":
            kwargs_parts = [
                f"condition={node.condition!r}",
                f"max_iterations={node.max_iterations!r}",
            ]
            if node.name and node.name != node.id:
                kwargs_parts.insert(0, f"name={node.name!r}")
            if node.description:
                kwargs_parts.append(f"description={node.description!r}")
            if node.compaction_rule is not None:
                kwargs_parts.append(
                    f"compaction={node.compaction_rule.model_dump(mode='json', exclude_none=True)!r}"
                )
            fp = node.failure_policy.model_dump(mode="json", exclude_none=True)
            if fp:
                kwargs_parts.append(f"failure_policy={fp!r}")
            if node.read_set:
                kwargs_parts.append(
                    f"read_set={[self._serialize_context_decl(d) for d in node.read_set]!r}"
                )
            if node.write_set:
                kwargs_parts.append(
                    f"write_set={[self._serialize_context_decl(d) for d in node.write_set]!r}"
                )
            if node.input_ports:
                kwargs_parts.append(
                    f"input_ports={[self._serialize_input_port(p) for p in node.input_ports]!r}"
                )
            if node.output_ports:
                kwargs_parts.append(
                    f"output_ports={[self._serialize_output_port(p) for p in node.output_ports]!r}"
                )
            lines.append(f"{pad}with wf.while_loop({node.id!r}, {', '.join(kwargs_parts)}) as {body_var}:")

        elif nt == "for_each":
            kwargs_parts = [
                f"parallelism={node.parallelism!r}",
                f"merge_strategy={node.merge_strategy.value!r}",
            ]
            if node.name and node.name != node.id:
                kwargs_parts.insert(0, f"name={node.name!r}")
            if node.description:
                kwargs_parts.append(f"description={node.description!r}")
            if node.read_set:
                kwargs_parts.append(
                    f"read_set={[self._serialize_context_decl(d) for d in node.read_set]!r}"
                )
            if node.write_set:
                kwargs_parts.append(
                    f"write_set={[self._serialize_context_decl(d) for d in node.write_set]!r}"
                )
            if node.input_ports:
                kwargs_parts.append(
                    f"input_ports={[self._serialize_input_port(p) for p in node.input_ports]!r}"
                )
            if node.output_ports:
                kwargs_parts.append(
                    f"output_ports={[self._serialize_output_port(p) for p in node.output_ports]!r}"
                )
            lines.append(f"{pad}with wf.for_each({node.id!r}, {', '.join(kwargs_parts)}) as {body_var}:")

        elif nt == "composite":
            kwargs_parts = []
            if node.name and node.name != node.id:
                kwargs_parts.append(f"name={node.name!r}")
            if node.description:
                kwargs_parts.append(f"description={node.description!r}")
            if node.input_mappings:
                kwargs_parts.append(f"input_mappings={node.input_mappings!r}")
            if node.output_mappings:
                kwargs_parts.append(f"output_mappings={node.output_mappings!r}")
            if node.read_set:
                kwargs_parts.append(
                    f"read_set={[self._serialize_context_decl(d) for d in node.read_set]!r}"
                )
            if node.write_set:
                kwargs_parts.append(
                    f"write_set={[self._serialize_context_decl(d) for d in node.write_set]!r}"
                )
            if node.input_ports:
                kwargs_parts.append(
                    f"input_ports={[self._serialize_input_port(p) for p in node.input_ports]!r}"
                )
            if node.output_ports:
                kwargs_parts.append(
                    f"output_ports={[self._serialize_output_port(p) for p in node.output_ports]!r}"
                )
            kw_str = f", {', '.join(kwargs_parts)}" if kwargs_parts else ""
            lines.append(f"{pad}with wf.composite({node.id!r}{kw_str}) as {body_var}:")

        elif nt == "parallel_subagents":
            kwargs_parts = [
                f"merge_strategy={node.merge_strategy.value!r}",
                f"parallelism={node.parallelism!r}",
            ]
            if node.name and node.name != node.id:
                kwargs_parts.insert(0, f"name={node.name!r}")
            if node.description:
                kwargs_parts.append(f"description={node.description!r}")
            if node.failure_policy and any(
                getattr(node.failure_policy, k) is not None
                for k in ("max_iterations", "timeout_seconds", "stagnation_threshold")
            ):
                fp = node.failure_policy.model_dump(mode="json", exclude_none=True)
                kwargs_parts.append(f"failure_policy={fp!r}")
            if node.reducer:
                kwargs_parts.append(f"reducer={node.reducer!r}")
            if node.input_mappings:
                kwargs_parts.append(f"input_mappings={node.input_mappings!r}")
            if node.input_ports:
                kwargs_parts.append(
                    f"input_ports={[self._serialize_input_port(p) for p in node.input_ports]!r}"
                )
            if node.output_ports:
                kwargs_parts.append(
                    f"output_ports={[self._serialize_output_port(p) for p in node.output_ports]!r}"
                )
            lines.append(f"{pad}with wf.parallel_subagents({node.id!r}, {', '.join(kwargs_parts)}) as {body_var}:")

        elif nt == "orchestrator":
            kwargs_parts = []
            if node.name and node.name != node.id:
                kwargs_parts.append(f"name={node.name!r}")
            if node.orchestrator_prompt:
                kwargs_parts.append(f"orchestrator_prompt={node.orchestrator_prompt!r}")
            if node.orchestrator_model:
                kwargs_parts.append(f"orchestrator_model={node.orchestrator_model!r}")
            if node.completion_condition != "all_done":
                kwargs_parts.append(f"completion_condition={node.completion_condition!r}")
            if node.max_iterations != 100:
                kwargs_parts.append(f"max_iterations={node.max_iterations!r}")
            if node.timeout_seconds is not None:
                kwargs_parts.append(f"timeout_seconds={node.timeout_seconds!r}")
            if node.input_mappings:
                kwargs_parts.append(f"input_mappings={node.input_mappings!r}")
            if node.description:
                kwargs_parts.append(f"description={node.description!r}")
            kw_str = f", {', '.join(kwargs_parts)}" if kwargs_parts else ""
            lines.append(f"{pad}with wf.orchestrator({node.id!r}{kw_str}) as {body_var}:")

        # Resolve the body sub-graph from the root graph (handles nested graphs)
        body_graph_key = getattr(node, "body_graph", None)
        branch_graphs = getattr(node, "branch_graphs", None)
        teams = getattr(node, "teams", None)
        if nt == "parallel_subagents" and branch_graphs:
            sub_graph = None  # Handled below per-branch
        elif nt == "orchestrator" and teams:
            sub_graph = None  # Handled below per-team
        else:
            sub_graph = self._resolve_sub_graph(body_graph_key) if body_graph_key else None

        inner_pad = pad + "    "
        if nt == "orchestrator" and teams:
            for team_name, sub_key in teams.items():
                team_sub = self._resolve_sub_graph(sub_key)
                if team_sub and team_sub.nodes:
                    team_var = f"_{body_var}_{team_name}"
                    lines.append(f"{inner_pad}with {body_var}.team({team_name!r}) as {team_var}:")
                    team_lines = self._emit_parallel_branch_content(
                        team_sub, team_var, indent + 2
                    )
                    lines.extend(team_lines)
                else:
                    team_var = f"_{body_var}_{team_name}"
                    lines.append(f"{inner_pad}with {body_var}.team({team_name!r}) as {team_var}:")
                    lines.append(f"{inner_pad}    pass")
            return lines

        if nt == "parallel_subagents" and branch_graphs:
            prefix = f"{node.id}_"
            for sub_key in branch_graphs:
                branch_key = sub_key[len(prefix):] if sub_key.startswith(prefix) else sub_key
                sub_graph = self._resolve_sub_graph(sub_key)
                if sub_graph and sub_graph.nodes:
                    branch_var = f"_{body_var}_{branch_key}"
                    lines.append(f"{inner_pad}with {body_var}.branch({branch_key!r}) as {branch_var}:")
                    branch_lines = self._emit_parallel_branch_content(
                        sub_graph, branch_var, indent + 2
                    )
                    lines.extend(branch_lines)
                else:
                    lines.append(f"{inner_pad}with {body_var}.branch({branch_key!r}) as {branch_var}:")
                    lines.append(f"{inner_pad}    pass")
            return lines

        if sub_graph and sub_graph.nodes:
            sub_ordered = self._topological_sort_graph(sub_graph)
            emitted_in_chain: set[str] = set()
            for sub_nid in sub_ordered:
                if sub_nid in emitted_in_chain:
                    continue
                sub_node = sub_graph.node_by_id(sub_nid)
                if sub_node is None:
                    continue
                sub_var = _to_var_name(sub_nid)
                if isinstance(sub_node, (WhileLoopNode, ForEachNode, CompositeNode)):
                    nested_body_var = f"_{sub_var}_body"
                    nested_lines = self._emit_subgraph_node(sub_node, nested_body_var, indent=indent + 1)
                    lines.extend(nested_lines)
                    lines.append(f"{inner_pad}{sub_var} = NodeRef({sub_node.id!r}, {sub_node.node_type!r}, {body_var})")
                else:
                    call = self._emit_node_call_scoped(sub_node, body_var)
                    lines.append(f"{inner_pad}{sub_var} = {call}")

            sub_chains = self._detect_chains_in(sub_graph)
            emitted_chain_pairs: set[tuple[str, str]] = set()
            sub_node_map = {n.id: n for n in sub_graph.nodes}
            subgraph_node_ids = {
                n.id for n in sub_graph.nodes
                if isinstance(n, (WhileLoopNode, ForEachNode, CompositeNode))
            }
            for chain in sub_chains:
                filtered = [nid for nid in chain if nid not in subgraph_node_ids]
                if len(filtered) >= 2:
                    chain_str = " >> ".join(_to_var_name(nid) for nid in filtered)
                    lines.append(f"{inner_pad}{chain_str}")
                    for i in range(len(filtered) - 1):
                        emitted_chain_pairs.add((filtered[i], filtered[i + 1]))

            for edge in sub_graph.edges:
                if isinstance(edge, DataEdge):
                    pair = (edge.source_node_id, edge.target_node_id)
                    if pair in emitted_chain_pairs and self._is_default_data_edge_in(edge, sub_node_map):
                        continue
                    src_var = _to_var_name(edge.source_node_id)
                    tgt_var = _to_var_name(edge.target_node_id)
                    spread_arg = ", spread=True" if getattr(edge, "spread", False) else ""
                    lines.append(f'{inner_pad}{body_var}.edge({src_var}["{edge.source_port}"], {tgt_var}["{edge.target_port}"]{spread_arg})')
                elif isinstance(edge, ControlEdge):
                    src_var = _to_var_name(edge.source_node_id)
                    tgt_var = _to_var_name(edge.target_node_id)
                    cond_arg = f", condition={edge.condition!r}" if edge.condition else ""
                    lines.append(
                        f'{inner_pad}{body_var}.control_edge({src_var}["{edge.source_port}"], '
                        f'{tgt_var}["{edge.target_port}"]{cond_arg})'
                    )
                elif isinstance(edge, ContextEdge):
                    src_var = _to_var_name(edge.source_node_id)
                    tgt_var = _to_var_name(edge.target_node_id)
                    lines.append(
                        f'{inner_pad}{body_var}.context_edge({src_var}["{edge.source_port}"], '
                        f'{tgt_var}["{edge.target_port}"], '
                        f"context_key={edge.context_key!r}, mode={edge.mode.value!r})"
                    )
        else:
            lines.append(f"{inner_pad}pass")

        return lines

    def _emit_parallel_branch_content(
        self, sub_graph: Graph, scope_var: str, indent: int
    ) -> list[str]:
        """Emit the content of a parallel branch (nodes, chains, edges)."""
        lines: list[str] = []
        inner_pad = "    " * indent
        sub_ordered = self._topological_sort_graph(sub_graph)
        emitted_in_chain: set[str] = set()
        for sub_nid in sub_ordered:
            if sub_nid in emitted_in_chain:
                continue
            sub_node = sub_graph.node_by_id(sub_nid)
            if sub_node is None:
                continue
            sub_var = _to_var_name(sub_nid)
            if isinstance(
                sub_node,
                (WhileLoopNode, ForEachNode, CompositeNode, ParallelSubagentsNode, OrchestratorNode),
            ):
                nested_body_var = f"_{sub_var}_body"
                nested_lines = self._emit_subgraph_node(sub_node, nested_body_var, indent=indent)
                lines.extend(nested_lines)
                lines.append(
                    f"{inner_pad}{sub_var} = NodeRef({sub_node.id!r}, {sub_node.node_type!r}, {scope_var})"
                )
            else:
                call = self._emit_node_call(sub_node)
                call = call.replace("wf.", f"{scope_var}.", 1)
                lines.append(f"{inner_pad}{sub_var} = {call}")

        sub_chains = self._detect_chains_in(sub_graph)
        emitted_chain_pairs: set[tuple[str, str]] = set()
        sub_node_map = {n.id: n for n in sub_graph.nodes}
        subgraph_node_ids = {
            n.id for n in sub_graph.nodes
            if isinstance(n, (WhileLoopNode, ForEachNode, CompositeNode, ParallelSubagentsNode, OrchestratorNode))
        }
        for chain in sub_chains:
            filtered = [nid for nid in chain if nid not in subgraph_node_ids]
            if len(filtered) >= 2:
                chain_str = " >> ".join(_to_var_name(nid) for nid in filtered)
                lines.append(f"{inner_pad}{chain_str}")
                for i in range(len(filtered) - 1):
                    emitted_chain_pairs.add((filtered[i], filtered[i + 1]))

        for edge in sub_graph.edges:
            if isinstance(edge, DataEdge):
                pair = (edge.source_node_id, edge.target_node_id)
                if pair in emitted_chain_pairs and self._is_default_data_edge_in(
                    edge, sub_node_map
                ):
                    continue
                src_var = _to_var_name(edge.source_node_id)
                tgt_var = _to_var_name(edge.target_node_id)
                spread_arg = ", spread=True" if getattr(edge, "spread", False) else ""
                lines.append(
                    f'{inner_pad}{scope_var}.edge({src_var}["{edge.source_port}"], '
                    f'{tgt_var}["{edge.target_port}"]{spread_arg})'
                )
            elif isinstance(edge, ControlEdge):
                src_var = _to_var_name(edge.source_node_id)
                tgt_var = _to_var_name(edge.target_node_id)
                cond_arg = f", condition={edge.condition!r}" if edge.condition else ""
                lines.append(
                    f'{inner_pad}{scope_var}.control_edge({src_var}["{edge.source_port}"], '
                    f'{tgt_var}["{edge.target_port}"]{cond_arg})'
                )
            elif isinstance(edge, ContextEdge):
                src_var = _to_var_name(edge.source_node_id)
                tgt_var = _to_var_name(edge.target_node_id)
                lines.append(
                    f'{inner_pad}{scope_var}.context_edge({src_var}["{edge.source_port}"], '
                    f'{tgt_var}["{edge.target_port}"], '
                    f"context_key={edge.context_key!r}, mode={edge.mode.value!r})"
                )
        return lines

    def _resolve_sub_graph(self, key: str) -> Graph | None:
        """Find a sub-graph by key, searching recursively through nested sub-graphs."""
        if key in self.graph.sub_graphs:
            return self.graph.sub_graphs[key]
        for sg in self.graph.sub_graphs.values():
            if key in sg.sub_graphs:
                return sg.sub_graphs[key]
            for nested_sg in sg.sub_graphs.values():
                if key in nested_sg.sub_graphs:
                    return nested_sg.sub_graphs[key]
        return None

    @staticmethod
    def _is_default_data_edge_in(edge: DataEdge, node_map: dict[str, Any]) -> bool:
        src_node = node_map.get(edge.source_node_id)
        tgt_node = node_map.get(edge.target_node_id)
        if src_node is None or tgt_node is None:
            return False
        gate_mode = getattr(src_node, "gate_mode", None) if src_node else None
        return (
            edge.source_port == default_output_port(src_node.node_type, gate_mode)
            and edge.target_port == default_input_port(tgt_node.node_type)
        )

    def _emit_node_call_scoped(self, node: Any, scope_var: str) -> str:
        """Like _emit_node_call but uses scope_var instead of 'wf'."""
        call = self._emit_node_call(node)
        return call.replace("wf.", f"{scope_var}.", 1)

    def _emit_control_edge(self, edge: ControlEdge) -> str:
        src_var = self.var_names.get(edge.source_node_id, edge.source_node_id)
        tgt_var = self.var_names.get(edge.target_node_id, edge.target_node_id)
        cond_arg = f", condition={edge.condition!r}" if edge.condition else ""
        return (
            f'wf.control_edge({src_var}["{edge.source_port}"], '
            f'{tgt_var}["{edge.target_port}"]{cond_arg})'
        )

    def _emit_context_edge(self, edge: ContextEdge) -> str:
        src_var = self.var_names.get(edge.source_node_id, edge.source_node_id)
        tgt_var = self.var_names.get(edge.target_node_id, edge.target_node_id)
        return (
            f'wf.context_edge({src_var}["{edge.source_port}"], '
            f'{tgt_var}["{edge.target_port}"], '
            f"context_key={edge.context_key!r}, mode={edge.mode.value!r})"
        )

    def _topological_sort(self) -> list[str]:
        return self._topological_sort_graph(self.graph)

    @staticmethod
    def _topological_sort_graph(graph: Graph) -> list[str]:
        """Kahn's algorithm with back-edge detection for cycles (while loops).

        Removes back edges (target → gate that creates a cycle) before sorting
        so all nodes appear in the output. Appends any remaining cycle nodes
        at the end in alphabetical order.
        """
        node_ids = {n.id for n in graph.nodes}
        gate_ids = set()
        for n in graph.nodes:
            if isinstance(n, GateNode) and getattr(n, "gate_mode", None) == "while":
                gate_ids.add(n.id)

        back_edge_pairs: set[tuple[str, str]] = set()
        for edge in graph.edges:
            if isinstance(edge, DataEdge) and edge.target_node_id in gate_ids:
                if edge.source_node_id in node_ids and edge.source_node_id != edge.target_node_id:
                    for other_edge in graph.edges:
                        if (
                            isinstance(other_edge, DataEdge)
                            and other_edge.source_node_id == edge.target_node_id
                            and other_edge.target_node_id != edge.source_node_id
                        ):
                            back_edge_pairs.add((edge.source_node_id, edge.target_node_id))
                            break

        in_degree: dict[str, int] = {n.id: 0 for n in graph.nodes}
        dependents: dict[str, list[str]] = defaultdict(list)

        for edge in graph.edges:
            if not isinstance(edge, DataEdge) or edge.target_node_id not in in_degree:
                continue
            if (edge.source_node_id, edge.target_node_id) in back_edge_pairs:
                continue
            in_degree[edge.target_node_id] += 1
            dependents[edge.source_node_id].append(edge.target_node_id)

        queue = sorted(nid for nid, deg in in_degree.items() if deg == 0)
        order: list[str] = []

        while queue:
            nid = queue.pop(0)
            order.append(nid)
            for dep in sorted(dependents[nid]):
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    queue.append(dep)
            queue.sort()

        remaining = sorted(nid for nid in node_ids if nid not in set(order))
        order.extend(remaining)
        return order

    def _detect_chains(self) -> list[list[str]]:
        return self._detect_chains_in(self.graph)

    def _is_default_data_edge(self, edge: DataEdge) -> bool:
        src_node = self.node_map.get(edge.source_node_id)
        tgt_node = self.node_map.get(edge.target_node_id)
        if src_node is None or tgt_node is None:
            return False
        gate_mode = getattr(src_node, "gate_mode", None) if src_node else None
        return (
            edge.source_port == default_output_port(src_node.node_type, gate_mode)
            and edge.target_port == default_input_port(tgt_node.node_type)
        )

    def _is_default_chain_edge(self, src_node_id: str, tgt_node_id: str) -> bool:
        data_edges = [
            e for e in self.graph.edges
            if isinstance(e, DataEdge)
            and e.source_node_id == src_node_id
            and e.target_node_id == tgt_node_id
        ]
        if len(data_edges) != 1:
            return False
        return self._is_default_data_edge(data_edges[0])

    @staticmethod
    def _serialize_input_port(port: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": port.name}
        if port.json_schema:
            payload["json_schema"] = port.json_schema
        if port.required is not True:
            payload["required"] = port.required
        if port.description:
            payload["description"] = port.description
        return payload

    @staticmethod
    def _serialize_output_port(port: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": port.name}
        if port.json_schema:
            payload["json_schema"] = port.json_schema
        if port.description:
            payload["description"] = port.description
        return payload

    @staticmethod
    def _serialize_context_decl(decl: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "key": decl.key,
            "mode": decl.mode.value,
        }
        if getattr(decl, "json_schema", None) is not None:
            payload["json_schema"] = decl.json_schema
        return payload

    @staticmethod
    def _detect_chains_in(graph: Graph) -> list[list[str]]:
        """Find maximal sequential chains connected by single DataEdges.

        A chain is a sequence [A, B, C] where A has exactly one outgoing
        DataEdge to B, and B has exactly one incoming DataEdge from A, etc.
        """
        out_count: dict[str, int] = defaultdict(int)
        in_count: dict[str, int] = defaultdict(int)
        single_target: dict[str, str] = {}
        single_source: dict[str, str] = {}

        for edge in graph.edges:
            if isinstance(edge, DataEdge):
                out_count[edge.source_node_id] += 1
                in_count[edge.target_node_id] += 1
                if out_count[edge.source_node_id] == 1:
                    single_target[edge.source_node_id] = edge.target_node_id
                else:
                    single_target.pop(edge.source_node_id, None)

                if in_count[edge.target_node_id] == 1:
                    single_source[edge.target_node_id] = edge.source_node_id
                else:
                    single_source.pop(edge.target_node_id, None)

        # Build chains starting from heads (nodes not in single_source values)
        visited: set[str] = set()
        chains: list[list[str]] = []

        chain_starts = [
            nid for nid in single_target
            if nid not in single_source
        ]

        for start in sorted(chain_starts):
            chain = [start]
            visited.add(start)
            current = start
            while current in single_target:
                nxt = single_target[current]
                if nxt in visited or nxt not in single_source:
                    break
                chain.append(nxt)
                visited.add(nxt)
                current = nxt
            if len(chain) >= 2:
                chains.append(chain)

        return chains
