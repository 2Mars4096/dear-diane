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
    AgentTeamNode,
    CompositeNode,
    ForEachNode,
    GateNode,
    GoalLoopNode,
    OrchestratorNode,
    ParallelSubagentsNode,
    WhileLoopNode,
)
from dan.models.edges import ContextEdge, ControlEdge, DataEdge
from dan.models.graph import Graph
from dan.models.hyperedges import Hyperedge
from dan.models.legacy import RAGOperator, ReflectionNode, ValidatorNode
from dan.worker.model import LLMHints, Worker

_LEGACY_ALIAS_NODE_TYPES = frozenset(
    {
        "llm_operator",
        "tool_operator",
        "code_operator",
        "input",
        "router",
        "validator",
        "reflection",
        "rag_operator",
        "human",
        "human_in_the_loop",
        "vote",
        "reduce",
    }
)


def decompile(graph: Graph, *, use_convenience_aliases: bool = False) -> str:
    """Convert a Graph model into executable Python builder DSL code.

    The default output stays canonical and lossless for Worker nodes. When
    ``use_convenience_aliases`` is enabled, simple leaf Workers may be rendered
    as readability-oriented legacy aliases like ``wf.llm(...)``.
    """
    return _Decompiler(
        graph,
        use_convenience_aliases=use_convenience_aliases,
    ).generate()


def _to_var_name(node_id: str) -> str:
    """Convert a node ID to a valid Python variable name."""
    name = re.sub(r"[^0-9a-zA-Z_]", "_", node_id)
    if name and name[0].isdigit():
        name = f"n_{name}"
    if keyword.iskeyword(name) or name in ("input", "item"):
        name = f"{name}_node"
    return name or "node"


class _Decompiler:
    def __init__(self, graph: Graph, *, use_convenience_aliases: bool = False) -> None:
        self.graph = graph
        self.use_convenience_aliases = use_convenience_aliases
        self.node_map = {n.id: n for n in graph.nodes}
        self.var_names: dict[str, str] = {}
        self._assign_var_names()

    @staticmethod
    def _is_scoped_worker(node: Any) -> bool:
        return isinstance(node, Worker) and (
            node.body_graph is not None or bool(node.sub_workers)
        )

    def _is_scoped_node(self, node: Any) -> bool:
        return isinstance(
            node,
            (
                WhileLoopNode,
                GoalLoopNode,
                ForEachNode,
                CompositeNode,
                ParallelSubagentsNode,
                OrchestratorNode,
                AgentTeamNode,
            ),
        ) or self._is_scoped_worker(node)

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

    def _requires_legacy_builder_mode(self, graph: Graph | None = None) -> bool:
        current_graph = self.graph if graph is None else graph
        for node in current_graph.nodes:
            if isinstance(node, Worker):
                if self._emit_worker_convenience_call(node) is not None:
                    return True
            elif node.node_type in _LEGACY_ALIAS_NODE_TYPES:
                return True
        for subgraph in current_graph.sub_graphs.values():
            if self._requires_legacy_builder_mode(subgraph):
                return True
        return False

    @staticmethod
    def _edge_lint_payload(edge: DataEdge) -> dict[str, Any] | None:
        lint_meta = getattr(edge, "metadata", {}).get("lint")
        if isinstance(lint_meta, dict):
            return lint_meta
        if getattr(edge, "lint", None) is not None:
            return DataEdge._compact_lint_payload(edge.lint)
        return None

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
        builder_mode_arg = ", canonical_workers=False" if self._requires_legacy_builder_mode() else ""
        lines.append(f"wf = workflow({wf_name!r}{desc_arg}{tags_arg}{builder_mode_arg})")
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

        for kind, refs in sorted((self.graph.worker_resources or {}).items()):
            for ref_name, value in sorted(refs.items()):
                lines.append(f"wf.resource({kind!r}, {ref_name!r}, {value!r})")
        if self.graph.worker_resources:
            lines.append("")

        # Hyperedges
        for he in self.graph.hyperedges:
            lines.append(self._emit_hyperedge(he))
        if self.graph.hyperedges:
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

            if self._is_scoped_node(node):
                body_var = f"_{var}_body"
                node_lines = self._emit_subgraph_node(node, body_var, builder_var="wf")
                lines.extend(node_lines)
                # Create a NodeRef for wiring edges to/from this sub-graph node
                lines.append(
                    f"{var} = NodeRef({node.id!r}, {node.node_type!r}, wf)"
                )
            else:
                lines.append(f"{var} = {self._emit_node_call(node)}")
            lines.append("")

        # Emit >> chains (only for non-subgraph nodes)
        subgraph_node_ids = {n.id for n in self.graph.nodes if self._is_scoped_node(n)}
        emitted_chain_pairs: set[tuple[str, str]] = set()
        for chain in chains:
            current_segment: list[str] = []
            for nid in chain:
                if nid in subgraph_node_ids:
                    if len(current_segment) >= 2:
                        chain_str = " >> ".join(self.var_names[nid_] for nid_ in current_segment)
                        lines.append(chain_str)
                    current_segment = []
                    continue
                if not current_segment:
                    current_segment = [nid]
                    continue
                prev = current_segment[-1]
                if self._is_default_chain_edge(prev, nid):
                    current_segment.append(nid)
                    emitted_chain_pairs.add((prev, nid))
                else:
                    if len(current_segment) >= 2:
                        chain_str = " >> ".join(self.var_names[nid_] for nid_ in current_segment)
                        lines.append(chain_str)
                    current_segment = [nid]
            if len(current_segment) >= 2:
                chain_str = " >> ".join(self.var_names[nid_] for nid_ in current_segment)
                lines.append(chain_str)
        if chains:
            lines.append("")

        # Emit explicit edges that aren't covered by >> chains
        for edge in self.graph.edges:
            if isinstance(edge, DataEdge):
                pair = (edge.source_node_id, edge.target_node_id)
                if (
                    pair in emitted_chain_pairs
                    and self._is_default_data_edge(edge)
                    and self._edge_lint_payload(edge) is None
                ):
                    continue
                src_var = self.var_names.get(edge.source_node_id, edge.source_node_id)
                tgt_var = self.var_names.get(edge.target_node_id, edge.target_node_id)
                spread_arg = ", spread=True" if getattr(edge, "spread", False) else ""
                lint_meta = self._edge_lint_payload(edge)
                lint_arg = f", lint={lint_meta!r}" if lint_meta is not None else ""
                lines.append(
                    f'wf.edge({src_var}["{edge.source_port}"], {tgt_var}["{edge.target_port}"]{spread_arg}{lint_arg})'
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
            if node.output_ports == []:
                kwargs.append("output_ports=[]")
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
        elif nt == "input":
            method = "wf.input_node"
            if getattr(node, "variables", None):
                variables = [v.model_dump(mode="json", exclude_none=True) for v in node.variables]
                kwargs.append(f"variables={variables!r}")
        elif nt == "human":
            method = "wf.human"
            if node.prompt:
                kwargs.append(f"prompt={node.prompt!r}")
            if node.timeout_seconds is not None:
                kwargs.append(f"timeout_seconds={node.timeout_seconds!r}")
            if node.default_action is not None:
                kwargs.append(f"default_action={node.default_action!r}")
            if getattr(node, "input_schema", None) is not None:
                kwargs.append(f"input_schema={node.input_schema!r}")
            if getattr(node, "output_schema", None) is not None:
                kwargs.append(f"output_schema={node.output_schema!r}")
            if getattr(node, "render_mode", "text") != "text":
                kwargs.append(f"render_mode={node.render_mode!r}")
            if getattr(node, "options", None):
                kwargs.append(f"options={node.options!r}")
            if getattr(node, "instructions", ""):
                kwargs.append(f"instructions={node.instructions!r}")
            if getattr(node, "render_target", "dialog") != "dialog":
                kwargs.append(f"render_target={node.render_target!r}")
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
        elif nt == "reflection":
            method = "wf.reflection"
            if node.reflection_prompt:
                kwargs.append(f"reflection_prompt={node.reflection_prompt!r}")
            if node.reflection_model is not None:
                kwargs.append(f"reflection_model={node.reflection_model!r}")
            if node.source != "last_run":
                kwargs.append(f"source={node.source!r}")
            if node.source_config:
                kwargs.append(f"source_config={node.source_config!r}")
            if node.output_format != "principles":
                kwargs.append(f"output_format={node.output_format!r}")
            if node.max_principles != 10:
                kwargs.append(f"max_principles={node.max_principles!r}")
            if node.min_confidence != 0.3:
                kwargs.append(f"min_confidence={node.min_confidence!r}")
            if node.dedup_strategy != "embedding_similarity":
                kwargs.append(f"dedup_strategy={node.dedup_strategy!r}")
        elif nt == "vote":
            method = "wf.vote"
            kwargs.append(f"prompt={node.prompt_template!r}")
            kwargs.append(f"candidates={node.candidates!r}")
            if node.num_votes != 3:
                kwargs.append(f"num_votes={node.num_votes!r}")
            if node.vote_strategy != "majority":
                kwargs.append(f"strategy={node.vote_strategy!r}")
            if node.system_prompt:
                kwargs.append(f"system_prompt={node.system_prompt!r}")
            if node.temperature != 0.7:
                kwargs.append(f"temperature={node.temperature!r}")
            if node.output_json_schema is not None:
                kwargs.append(f"output_schema={node.output_json_schema!r}")
            if getattr(node, "vote_config", None) is not None:
                kwargs.append(f"vote_config={node.vote_config.model_dump(mode='json', exclude_none=True)!r}")
            if getattr(node, "parallelism", 3) != 3:
                kwargs.append(f"parallelism={node.parallelism!r}")
            if getattr(node, "timeout_seconds", None) is not None:
                kwargs.append(f"timeout_seconds={node.timeout_seconds!r}")
        elif nt == "worker":
            convenience_call = self._emit_worker_convenience_call(node)
            if convenience_call is not None:
                return convenience_call
            method = "wf.worker"
            if node.role:
                kwargs.append(f"role={node.role!r}")
            if node.instruction:
                kwargs.append(f"instruction={node.instruction!r}")
            if node.persona:
                kwargs.append(f"persona={node.persona!r}")
            if node.authority.value != "leaf":
                kwargs.append(f"authority={node.authority.value!r}")
            if node.model is not None:
                kwargs.append(f"model={node.model!r}")
            if node.tool_ids:
                kwargs.append(f"tool_ids={node.tool_ids!r}")
            if node.code:
                kwargs.append(f"code={node.code!r}")
            if node.language != "python":
                kwargs.append(f"language={node.language!r}")
            if node.llm_hints is not None:
                kwargs.append(f"llm_hints={node.llm_hints.model_dump(mode='json', exclude_none=True)!r}")
            if node.context is not None:
                kwargs.append(f"context={node.context.model_dump(mode='json', exclude_none=True)!r}")
            if node.authority_policy is not None:
                kwargs.append(
                    f"authority_policy={node.authority_policy.model_dump(mode='json', exclude_none=True)!r}"
                )
            if node.execution is not None:
                kwargs.append(f"execution={node.execution.model_dump(mode='json', exclude_none=True)!r}")
            if node.control_flow is not None:
                kwargs.append(
                    f"control_flow={node.control_flow.model_dump(mode='json', exclude_none=True)!r}"
                )
            if node.body_graph is not None:
                kwargs.append(f"body_graph={node.body_graph!r}")
            if node.sub_workers:
                kwargs.append(f"sub_workers={node.sub_workers!r}")
            if node.input_mappings:
                kwargs.append(f"input_mappings={node.input_mappings!r}")
            if node.output_mappings:
                kwargs.append(f"output_mappings={node.output_mappings!r}")
            if node.parallelism != 1:
                kwargs.append(f"parallelism={node.parallelism!r}")
            if node.merge_strategy.value != "append":
                kwargs.append(f"merge_strategy={node.merge_strategy.value!r}")
            if node.spawn_policy is not None:
                kwargs.append(
                    f"spawn_policy={node.spawn_policy.model_dump(mode='json', exclude_none=True)!r}"
                )
            if node.external_input_schema is not None:
                kwargs.append(f"external_input_schema={node.external_input_schema!r}")
            if node.external_output_schema is not None:
                kwargs.append(f"external_output_schema={node.external_output_schema!r}")
            if node.control_state_schema:
                kwargs.append(f"control_state_schema={node.control_state_schema!r}")
            local_state = node.local_state.model_dump(
                mode="json",
                exclude_none=True,
                exclude_defaults=True,
            )
            if local_state:
                kwargs.append(f"local_state={local_state!r}")
            if node.compaction_rule is not None:
                kwargs.append(
                    f"compaction_rule={node.compaction_rule.model_dump(mode='json', exclude_none=True)!r}"
                )
            failure_policy = node.failure_policy.model_dump(
                mode="json",
                exclude_none=True,
                exclude_defaults=True,
            )
            if failure_policy:
                kwargs.append(f"failure_policy={failure_policy!r}")
            if node.projections:
                kwargs.append(
                    f"projections={[projection.model_dump(mode='json', exclude_none=True) for projection in node.projections]!r}"
                )
            if node.boundary_contract is not None:
                kwargs.append(
                    f"boundary_contract={node.boundary_contract.model_dump(mode='json', exclude_none=True)!r}"
                )
            if node.validation_rules:
                kwargs.append(
                    f"validation_rules={[rule.model_dump(mode='json', exclude_none=True) for rule in node.validation_rules]!r}"
                )
        else:
            method = f"wf.llm"  # fallback

        # Explicit ports
        if nt != "input" and node.input_ports:
            port_dicts = [self._serialize_input_port(p) for p in node.input_ports]
            if port_dicts:
                kwargs.append(f"input_ports={port_dicts!r}")
        if nt != "input" and node.output_ports:
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

    def _emit_worker_convenience_call(self, node: Worker) -> str | None:
        if not self.use_convenience_aliases:
            return None

        if self._is_simple_input_worker(node):
            return self._emit_simple_input_worker(node)
        if self._is_simple_reduce_worker(node):
            return self._emit_simple_reduce_worker(node)
        if self._is_simple_rag_worker(node):
            return self._emit_simple_rag_worker(node)
        if self._is_simple_reflection_worker(node):
            return self._emit_simple_reflection_worker(node)
        if self._is_simple_human_worker(node):
            return self._emit_simple_human_worker(node)
        if self._is_simple_human_in_the_loop_worker(node):
            return self._emit_simple_human_in_the_loop_worker(node)
        if self._is_simple_vote_worker(node):
            return self._emit_simple_vote_worker(node)
        if self._is_simple_llm_worker(node):
            return self._emit_simple_llm_worker(node)
        if self._is_simple_tool_worker(node):
            return self._emit_simple_tool_worker(node)
        if self._is_simple_code_worker(node):
            return self._emit_simple_code_worker(node)
        return None

    @staticmethod
    def _has_non_aliasable_worker_state(node: Worker) -> bool:
        return any(
            [
                node.role,
                node.instruction,
                node.persona,
                node.authority.value != "leaf",
                node.context is not None,
                node.authority_policy is not None,
                node.execution is not None,
                node.control_flow is not None,
                node.body_graph is not None,
                bool(node.sub_workers),
                node.external_input_schema is not None,
                node.external_output_schema is not None,
                bool(node.control_state_schema),
                bool(
                    node.local_state.model_dump(
                        mode="json",
                        exclude_none=True,
                        exclude_defaults=True,
                    )
                ),
                node.compaction_rule is not None,
                bool(
                    node.failure_policy.model_dump(
                        mode="json",
                        exclude_none=True,
                        exclude_defaults=True,
                    )
                ),
                bool(node.projections),
                node.boundary_contract is not None,
            ]
        )

    @staticmethod
    def _has_non_aliasable_specialized_worker_state(
        node: Worker,
        *,
        expected_role: str,
    ) -> bool:
        return any(
            [
                node.role != expected_role,
                node.instruction,
                node.persona,
                node.authority.value != "leaf",
                node.context is not None,
                node.authority_policy is not None,
                node.execution is not None,
                node.control_flow is not None,
                node.body_graph is not None,
                bool(node.sub_workers),
                node.external_input_schema is not None,
                node.external_output_schema is not None,
                bool(node.control_state_schema),
                bool(
                    node.local_state.model_dump(
                        mode="json",
                        exclude_none=True,
                        exclude_defaults=True,
                    )
                ),
                node.compaction_rule is not None,
                bool(
                    node.failure_policy.model_dump(
                        mode="json",
                        exclude_none=True,
                        exclude_defaults=True,
                    )
                ),
                bool(node.projections),
                node.boundary_contract is not None,
                bool(node.read_set),
                bool(node.write_set),
                bool(node.tool_ids),
                bool(node.code),
                node.llm_hints is not None,
            ]
        )

    def _is_simple_llm_worker(self, node: Worker) -> bool:
        hints = node.llm_hints or LLMHints()
        return bool(
            node.model is not None
            and not node.code
            and not node.tool_ids
            and not node.read_set
            and not node.write_set
            and not self._has_non_aliasable_worker_state(node)
            and not hints.tools
            and hints.max_tool_rounds == 10
            and hints.task_tier is None
            and hints.history_policy is None
        )

    def _is_simple_tool_worker(self, node: Worker) -> bool:
        return bool(
            len(node.tool_ids) == 1
            and node.model is None
            and not node.code
            and not node.read_set
            and not node.write_set
            and node.llm_hints is None
            and not self._has_non_aliasable_worker_state(node)
        )

    def _is_simple_code_worker(self, node: Worker) -> bool:
        return bool(
            node.code
            and node.model is None
            and not node.tool_ids
            and node.llm_hints is None
            and not self._has_non_aliasable_worker_state(node)
        )

    def _is_simple_input_worker(self, node: Worker) -> bool:
        return bool(
            not node.model
            and not node.tool_ids
            and not node.code
            and node.llm_hints is None
            and not node.read_set
            and not node.write_set
            and not self._has_non_aliasable_worker_state(node)
            and set(node.metadata) <= {"input_variables"}
            and isinstance(node.metadata.get("input_variables"), list)
        )

    def _is_simple_reduce_worker(self, node: Worker) -> bool:
        return bool(
            not node.model
            and self._has_non_aliasable_specialized_worker_state(node, expected_role="reduce") is False
            and set(node.metadata) <= {"reduce_expression"}
            and isinstance(node.metadata.get("reduce_expression"), str)
            and node.metadata.get("reduce_expression")
        )

    def _is_simple_rag_worker(self, node: Worker) -> bool:
        allowed = {
            "rag_collection",
            "rag_top_k",
            "rag_similarity_threshold",
            "rag_embedding_model",
            "rag_vector_store_config",
            "rag_query_template",
            "rag_include_metadata",
            "rag_rerank",
        }
        return bool(
            not node.model
            and self._has_non_aliasable_specialized_worker_state(node, expected_role="rag") is False
            and set(node.metadata) <= allowed
            and "rag_collection" in node.metadata
        )

    def _is_simple_reflection_worker(self, node: Worker) -> bool:
        allowed = {
            "reflection_prompt",
            "reflection_source",
            "reflection_source_config",
            "reflection_output_format",
            "reflection_max_principles",
            "reflection_min_confidence",
            "reflection_dedup_strategy",
            "reflection_model",
        }
        return bool(
            self._has_non_aliasable_specialized_worker_state(node, expected_role="reflection") is False
            and set(node.metadata) <= allowed
            and "reflection_prompt" in node.metadata
        )

    def _is_simple_human_worker(self, node: Worker) -> bool:
        allowed = {
            "human_prompt",
            "human_timeout_seconds",
            "human_default_action",
            "human_input_schema",
            "human_output_schema",
            "human_render_mode",
            "human_options",
            "human_instructions",
            "human_render_target",
        }
        return bool(
            not node.model
            and self._has_non_aliasable_specialized_worker_state(node, expected_role="human") is False
            and set(node.metadata) <= allowed
        )

    def _is_simple_human_in_the_loop_worker(self, node: Worker) -> bool:
        allowed = {
            "human_prompt",
            "human_timeout_seconds",
            "human_default_action",
        }
        return bool(
            not node.model
            and self._has_non_aliasable_specialized_worker_state(node, expected_role="human_in_the_loop") is False
            and set(node.metadata) <= allowed
        )

    def _is_simple_vote_worker(self, node: Worker) -> bool:
        allowed = {
            "vote_candidates",
            "vote_num_votes",
            "vote_prompt_template",
            "vote_system_prompt",
            "vote_temperature",
            "vote_strategy",
            "vote_parallelism",
            "vote_output_json_schema",
            "vote_config",
            "vote_timeout_seconds",
        }
        return bool(
            not node.model
            and self._has_non_aliasable_specialized_worker_state(node, expected_role="vote") is False
            and set(node.metadata) <= allowed
            and "vote_candidates" in node.metadata
            and "vote_prompt_template" in node.metadata
        )

    def _emit_simple_llm_worker(self, node: Worker) -> str:
        args = [repr(node.id)]
        kwargs: list[str] = []
        hints = node.llm_hints or LLMHints()

        if node.name and node.name != node.id:
            kwargs.append(f"name={node.name!r}")
        if node.description:
            kwargs.append(f"description={node.description!r}")
        kwargs.append(f"model={node.model!r}")
        if hints.prompt_template:
            kwargs.append(f"prompt={hints.prompt_template!r}")
        if hints.system_prompt:
            kwargs.append(f"system_prompt={hints.system_prompt!r}")
        if hints.temperature != 0.7:
            kwargs.append(f"temperature={hints.temperature!r}")
        if hints.max_tokens is not None:
            kwargs.append(f"max_tokens={hints.max_tokens!r}")
        if hints.output_json_schema is not None:
            kwargs.append(f"output_schema={hints.output_json_schema!r}")
        kwargs.append(
            f"input_ports={[self._serialize_input_port(p) for p in node.input_ports]!r}"
        )
        kwargs.append(
            f"output_ports={[self._serialize_output_port(p) for p in node.output_ports]!r}"
        )
        return f"wf.llm({', '.join(args + kwargs)})"

    def _emit_simple_tool_worker(self, node: Worker) -> str:
        args = [repr(node.id)]
        kwargs: list[str] = []

        if node.name and node.name != node.id:
            kwargs.append(f"name={node.name!r}")
        if node.description:
            kwargs.append(f"description={node.description!r}")
        kwargs.append(f"tool_id={node.tool_ids[0]!r}")
        tool_config = dict(node.metadata.get("tool_config") or {})
        if tool_config:
            kwargs.append(f"tool_config={tool_config!r}")
        kwargs.append(
            f"input_ports={[self._serialize_input_port(p) for p in node.input_ports]!r}"
        )
        kwargs.append(
            f"output_ports={[self._serialize_output_port(p) for p in node.output_ports]!r}"
        )
        return f"wf.tool({', '.join(args + kwargs)})"

    def _emit_simple_code_worker(self, node: Worker) -> str:
        args = [repr(node.id)]
        kwargs: list[str] = []

        if node.name and node.name != node.id:
            kwargs.append(f"name={node.name!r}")
        if node.description:
            kwargs.append(f"description={node.description!r}")
        kwargs.append(f"code={node.code!r}")
        if node.language != "python":
            kwargs.append(f"language={node.language!r}")
        if node.read_set:
            kwargs.append(
                f"read_set={[self._serialize_context_decl(d) for d in node.read_set]!r}"
            )
        if node.write_set:
            kwargs.append(
                f"write_set={[self._serialize_context_decl(d) for d in node.write_set]!r}"
            )
        kwargs.append(
            f"input_ports={[self._serialize_input_port(p) for p in node.input_ports]!r}"
        )
        kwargs.append(
            f"output_ports={[self._serialize_output_port(p) for p in node.output_ports]!r}"
        )
        return f"wf.code({', '.join(args + kwargs)})"

    def _emit_simple_input_worker(self, node: Worker) -> str:
        args = [repr(node.id)]
        kwargs: list[str] = []

        if node.name and node.name != node.id:
            kwargs.append(f"name={node.name!r}")
        if node.description:
            kwargs.append(f"description={node.description!r}")
        variables = list(node.metadata.get("input_variables") or [])
        kwargs.append(f"variables={variables!r}")
        return f"wf.input_node({', '.join(args + kwargs)})"

    def _emit_simple_reduce_worker(self, node: Worker) -> str:
        args = [repr(node.id)]
        kwargs: list[str] = []

        if node.name and node.name != node.id:
            kwargs.append(f"name={node.name!r}")
        if node.description:
            kwargs.append(f"description={node.description!r}")
        kwargs.append(f"reducer={node.metadata['reduce_expression']!r}")
        kwargs.append(
            f"input_ports={[self._serialize_input_port(p) for p in node.input_ports]!r}"
        )
        kwargs.append(
            f"output_ports={[self._serialize_output_port(p) for p in node.output_ports]!r}"
        )
        return f"wf.reduce({', '.join(args + kwargs)})"

    def _emit_simple_rag_worker(self, node: Worker) -> str:
        args = [repr(node.id)]
        kwargs: list[str] = []
        metadata = node.metadata

        if node.name and node.name != node.id:
            kwargs.append(f"name={node.name!r}")
        if node.description:
            kwargs.append(f"description={node.description!r}")
        kwargs.append(f"collection={metadata['rag_collection']!r}")
        if metadata.get("rag_top_k", 5) != 5:
            kwargs.append(f"top_k={metadata['rag_top_k']!r}")
        if "rag_similarity_threshold" in metadata:
            kwargs.append(f"similarity_threshold={metadata['rag_similarity_threshold']!r}")
        if metadata.get("rag_embedding_model"):
            kwargs.append(f"embedding_model={metadata['rag_embedding_model']!r}")
        if metadata.get("rag_vector_store_config"):
            kwargs.append(f"vector_store_config={metadata['rag_vector_store_config']!r}")
        if metadata.get("rag_query_template", "{query}") != "{query}":
            kwargs.append(f"query_template={metadata['rag_query_template']!r}")
        if metadata.get("rag_include_metadata", True) is not True:
            kwargs.append(f"include_metadata={metadata['rag_include_metadata']!r}")
        if metadata.get("rag_rerank", False):
            kwargs.append(f"rerank={metadata['rag_rerank']!r}")
        kwargs.append(
            f"input_ports={[self._serialize_input_port(p) for p in node.input_ports]!r}"
        )
        kwargs.append(
            f"output_ports={[self._serialize_output_port(p) for p in node.output_ports]!r}"
        )
        return f"wf.rag({', '.join(args + kwargs)})"

    def _emit_simple_reflection_worker(self, node: Worker) -> str:
        args = [repr(node.id)]
        kwargs: list[str] = []
        metadata = node.metadata

        if node.name and node.name != node.id:
            kwargs.append(f"name={node.name!r}")
        if node.description:
            kwargs.append(f"description={node.description!r}")
        if metadata.get("reflection_prompt"):
            kwargs.append(f"reflection_prompt={metadata['reflection_prompt']!r}")
        if node.model is not None:
            kwargs.append(f"reflection_model={node.model!r}")
        if metadata.get("reflection_source", "last_run") != "last_run":
            kwargs.append(f"source={metadata['reflection_source']!r}")
        if metadata.get("reflection_source_config"):
            kwargs.append(f"source_config={metadata['reflection_source_config']!r}")
        if metadata.get("reflection_output_format", "principles") != "principles":
            kwargs.append(f"output_format={metadata['reflection_output_format']!r}")
        if metadata.get("reflection_max_principles", 10) != 10:
            kwargs.append(f"max_principles={metadata['reflection_max_principles']!r}")
        if metadata.get("reflection_min_confidence", 0.3) != 0.3:
            kwargs.append(f"min_confidence={metadata['reflection_min_confidence']!r}")
        if metadata.get("reflection_dedup_strategy", "embedding_similarity") != "embedding_similarity":
            kwargs.append(f"dedup_strategy={metadata['reflection_dedup_strategy']!r}")
        kwargs.append(
            f"input_ports={[self._serialize_input_port(p) for p in node.input_ports]!r}"
        )
        kwargs.append(
            f"output_ports={[self._serialize_output_port(p) for p in node.output_ports]!r}"
        )
        return f"wf.reflection({', '.join(args + kwargs)})"

    def _emit_simple_human_worker(self, node: Worker) -> str:
        args = [repr(node.id)]
        kwargs: list[str] = []
        metadata = node.metadata

        if node.name and node.name != node.id:
            kwargs.append(f"name={node.name!r}")
        if node.description:
            kwargs.append(f"description={node.description!r}")
        if metadata.get("human_prompt"):
            kwargs.append(f"prompt={metadata['human_prompt']!r}")
        if "human_timeout_seconds" in metadata:
            kwargs.append(f"timeout_seconds={metadata['human_timeout_seconds']!r}")
        if "human_default_action" in metadata:
            kwargs.append(f"default_action={metadata['human_default_action']!r}")
        if "human_input_schema" in metadata:
            kwargs.append(f"input_schema={metadata['human_input_schema']!r}")
        if "human_output_schema" in metadata:
            kwargs.append(f"output_schema={metadata['human_output_schema']!r}")
        if metadata.get("human_render_mode", "text") != "text":
            kwargs.append(f"render_mode={metadata['human_render_mode']!r}")
        if "human_options" in metadata:
            kwargs.append(f"options={metadata['human_options']!r}")
        if metadata.get("human_instructions"):
            kwargs.append(f"instructions={metadata['human_instructions']!r}")
        if metadata.get("human_render_target", "dialog") != "dialog":
            kwargs.append(f"render_target={metadata['human_render_target']!r}")
        kwargs.append(
            f"input_ports={[self._serialize_input_port(p) for p in node.input_ports]!r}"
        )
        kwargs.append(
            f"output_ports={[self._serialize_output_port(p) for p in node.output_ports]!r}"
        )
        return f"wf.human({', '.join(args + kwargs)})"

    def _emit_simple_human_in_the_loop_worker(self, node: Worker) -> str:
        args = [repr(node.id)]
        kwargs: list[str] = []
        metadata = node.metadata

        if node.name and node.name != node.id:
            kwargs.append(f"name={node.name!r}")
        if node.description:
            kwargs.append(f"description={node.description!r}")
        if metadata.get("human_prompt"):
            kwargs.append(f"prompt={metadata['human_prompt']!r}")
        if "human_timeout_seconds" in metadata:
            kwargs.append(f"timeout_seconds={metadata['human_timeout_seconds']!r}")
        if "human_default_action" in metadata:
            kwargs.append(f"default_action={metadata['human_default_action']!r}")
        kwargs.append(
            f"input_ports={[self._serialize_input_port(p) for p in node.input_ports]!r}"
        )
        kwargs.append(
            f"output_ports={[self._serialize_output_port(p) for p in node.output_ports]!r}"
        )
        return f"wf.human_in_the_loop({', '.join(args + kwargs)})"

    def _emit_simple_vote_worker(self, node: Worker) -> str:
        args = [repr(node.id)]
        kwargs: list[str] = []
        metadata = node.metadata

        if node.name and node.name != node.id:
            kwargs.append(f"name={node.name!r}")
        if node.description:
            kwargs.append(f"description={node.description!r}")
        kwargs.append(f"prompt={metadata['vote_prompt_template']!r}")
        kwargs.append(f"candidates={metadata['vote_candidates']!r}")
        if metadata.get("vote_num_votes", 3) != 3:
            kwargs.append(f"num_votes={metadata['vote_num_votes']!r}")
        if metadata.get("vote_strategy", "majority") != "majority":
            kwargs.append(f"strategy={metadata['vote_strategy']!r}")
        if metadata.get("vote_system_prompt"):
            kwargs.append(f"system_prompt={metadata['vote_system_prompt']!r}")
        if metadata.get("vote_temperature", 0.7) != 0.7:
            kwargs.append(f"temperature={metadata['vote_temperature']!r}")
        if "vote_output_json_schema" in metadata:
            kwargs.append(f"output_schema={metadata['vote_output_json_schema']!r}")
        if "vote_config" in metadata:
            kwargs.append(f"vote_config={metadata['vote_config']!r}")
        if metadata.get("vote_parallelism", 3) != 3:
            kwargs.append(f"parallelism={metadata['vote_parallelism']!r}")
        if "vote_timeout_seconds" in metadata:
            kwargs.append(f"timeout_seconds={metadata['vote_timeout_seconds']!r}")
        kwargs.append(
            f"input_ports={[self._serialize_input_port(p) for p in node.input_ports]!r}"
        )
        kwargs.append(
            f"output_ports={[self._serialize_output_port(p) for p in node.output_ports]!r}"
        )
        return f"wf.vote({', '.join(args + kwargs)})"

    def _emit_subgraph_node(
        self,
        node: Any,
        body_var: str,
        indent: int = 0,
        *,
        builder_var: str = "wf",
    ) -> list[str]:
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
            lines.append(
                f"{pad}with {builder_var}.while_loop({node.id!r}, {', '.join(kwargs_parts)}) as {body_var}:"
            )

        elif nt == "goal_loop":
            kwargs_parts = [
                f"goal_text={node.goal_text!r}",
                f"metric_name={node.metric_name!r}",
                f"target_value={node.target_value!r}",
                f"comparison={node.comparison!r}",
                f"max_iterations={node.max_iterations!r}",
                f"evaluator={node.evaluator!r}",
            ]
            if node.name and node.name != node.id:
                kwargs_parts.insert(0, f"name={node.name!r}")
            if node.description:
                kwargs_parts.append(f"description={node.description!r}")
            if node.success_criteria is not None:
                kwargs_parts.append(f"success_criteria={node.success_criteria!r}")
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
            lines.append(
                f"{pad}with {builder_var}.goal_loop({node.id!r}, {', '.join(kwargs_parts)}) as {body_var}:"
            )

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
            lines.append(
                f"{pad}with {builder_var}.for_each({node.id!r}, {', '.join(kwargs_parts)}) as {body_var}:"
            )

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
            lines.append(
                f"{pad}with {builder_var}.composite({node.id!r}{kw_str}) as {body_var}:"
            )

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
            lines.append(
                f"{pad}with {builder_var}.parallel_subagents({node.id!r}, {', '.join(kwargs_parts)}) as {body_var}:"
            )

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
            lines.append(
                f"{pad}with {builder_var}.orchestrator({node.id!r}{kw_str}) as {body_var}:"
            )

        elif nt == "agent_team":
            kwargs_parts = []
            if node.name and node.name != node.id:
                kwargs_parts.append(f"name={node.name!r}")
            if node.moderator_prompt:
                kwargs_parts.append(f"moderator_prompt={node.moderator_prompt!r}")
            if node.moderator_model:
                kwargs_parts.append(f"moderator_model={node.moderator_model!r}")
            if node.turn_strategy != "round_robin":
                kwargs_parts.append(f"turn_strategy={node.turn_strategy!r}")
            if node.max_turns != 20:
                kwargs_parts.append(f"max_turns={node.max_turns!r}")
            if node.completion_condition != "max_turns":
                kwargs_parts.append(f"completion_condition={node.completion_condition!r}")
            if node.timeout_seconds is not None:
                kwargs_parts.append(f"timeout_seconds={node.timeout_seconds!r}")
            if node.shared_context_keys:
                kwargs_parts.append(f"shared_context_keys={node.shared_context_keys!r}")
            if node.handoff_policy != "explicit":
                kwargs_parts.append(f"handoff_policy={node.handoff_policy!r}")
            if node.input_mappings:
                kwargs_parts.append(f"input_mappings={node.input_mappings!r}")
            if node.agent_inputs:
                kwargs_parts.append(f"agent_inputs={node.agent_inputs!r}")
            fp = node.failure_policy.model_dump(mode="json", exclude_none=True)
            if fp:
                kwargs_parts.append(f"failure_policy={fp!r}")
            if node.description:
                kwargs_parts.append(f"description={node.description!r}")
            if node.input_ports:
                kwargs_parts.append(
                    f"input_ports={[self._serialize_input_port(p) for p in node.input_ports]!r}"
                )
            if node.output_ports:
                kwargs_parts.append(
                    f"output_ports={[self._serialize_output_port(p) for p in node.output_ports]!r}"
                )
            kw_str = f", {', '.join(kwargs_parts)}" if kwargs_parts else ""
            lines.append(f"{pad}with {builder_var}.team({node.id!r}{kw_str}) as {body_var}:")

        elif nt == "worker":
            kwargs_parts = []
            if node.name and node.name != node.id:
                kwargs_parts.append(f"name={node.name!r}")
            if node.description:
                kwargs_parts.append(f"description={node.description!r}")
            if node.role:
                kwargs_parts.append(f"role={node.role!r}")
            if node.instruction:
                kwargs_parts.append(f"instruction={node.instruction!r}")
            if node.persona:
                kwargs_parts.append(f"persona={node.persona!r}")
            if node.authority.value != "leaf":
                kwargs_parts.append(f"authority={node.authority.value!r}")
            if node.model is not None:
                kwargs_parts.append(f"model={node.model!r}")
            if node.tool_ids:
                kwargs_parts.append(f"tool_ids={node.tool_ids!r}")
            tool_config = dict(node.metadata.get("tool_config") or {})
            if tool_config:
                kwargs_parts.append(f"tool_config={tool_config!r}")
            if node.code:
                kwargs_parts.append(f"code={node.code!r}")
            if node.language != "python":
                kwargs_parts.append(f"language={node.language!r}")
            if node.llm_hints is not None:
                kwargs_parts.append(
                    f"llm_hints={node.llm_hints.model_dump(mode='json', exclude_none=True)!r}"
                )
            if node.context is not None:
                kwargs_parts.append(
                    f"context={node.context.model_dump(mode='json', exclude_none=True)!r}"
                )
            if node.authority_policy is not None:
                kwargs_parts.append(
                    f"authority_policy={node.authority_policy.model_dump(mode='json', exclude_none=True)!r}"
                )
            if node.execution is not None:
                kwargs_parts.append(
                    f"execution={node.execution.model_dump(mode='json', exclude_none=True)!r}"
                )
            if node.control_flow is not None:
                kwargs_parts.append(
                    f"control_flow={node.control_flow.model_dump(mode='json', exclude_none=True)!r}"
                )
            if node.input_mappings:
                kwargs_parts.append(f"input_mappings={node.input_mappings!r}")
            if node.output_mappings:
                kwargs_parts.append(f"output_mappings={node.output_mappings!r}")
            if node.parallelism != 1:
                kwargs_parts.append(f"parallelism={node.parallelism!r}")
            if node.merge_strategy.value != "append":
                kwargs_parts.append(f"merge_strategy={node.merge_strategy.value!r}")
            if node.spawn_policy is not None:
                kwargs_parts.append(
                    f"spawn_policy={node.spawn_policy.model_dump(mode='json', exclude_none=True)!r}"
                )
            if node.external_input_schema is not None:
                kwargs_parts.append(f"external_input_schema={node.external_input_schema!r}")
            if node.external_output_schema is not None:
                kwargs_parts.append(f"external_output_schema={node.external_output_schema!r}")
            if node.control_state_schema:
                kwargs_parts.append(f"control_state_schema={node.control_state_schema!r}")
            local_state = node.local_state.model_dump(
                mode="json",
                exclude_none=True,
                exclude_defaults=True,
            )
            if local_state:
                kwargs_parts.append(f"local_state={local_state!r}")
            if node.compaction_rule is not None:
                kwargs_parts.append(
                    f"compaction_rule={node.compaction_rule.model_dump(mode='json', exclude_none=True)!r}"
                )
            failure_policy = node.failure_policy.model_dump(
                mode="json",
                exclude_none=True,
                exclude_defaults=True,
            )
            if failure_policy:
                kwargs_parts.append(f"failure_policy={failure_policy!r}")
            if node.projections:
                kwargs_parts.append(
                    f"projections={[projection.model_dump(mode='json', exclude_none=True) for projection in node.projections]!r}"
                )
            if node.boundary_contract is not None:
                kwargs_parts.append(
                    f"boundary_contract={node.boundary_contract.model_dump(mode='json', exclude_none=True)!r}"
                )
            if node.validation_rules:
                kwargs_parts.append(
                    f"validation_rules={[rule.model_dump(mode='json', exclude_none=True) for rule in node.validation_rules]!r}"
                )
            if node.input_ports:
                kwargs_parts.append(
                    f"input_ports={[self._serialize_input_port(p) for p in node.input_ports]!r}"
                )
            if node.output_ports:
                kwargs_parts.append(
                    f"output_ports={[self._serialize_output_port(p) for p in node.output_ports]!r}"
                )
            if node.read_set:
                kwargs_parts.append(
                    f"read_set={[self._serialize_context_decl(d) for d in node.read_set]!r}"
                )
            if node.write_set:
                kwargs_parts.append(
                    f"write_set={[self._serialize_context_decl(d) for d in node.write_set]!r}"
                )
            kw_str = f", {', '.join(kwargs_parts)}" if kwargs_parts else ""
            lines.append(
                f"{pad}with {builder_var}.worker_scope({node.id!r}{kw_str}) as {body_var}:"
            )

        # Resolve the body sub-graph from the root graph (handles nested graphs)
        body_graph_key = getattr(node, "body_graph", None)
        branch_graphs = getattr(node, "branch_graphs", None)
        teams = getattr(node, "teams", None)
        agents = getattr(node, "agents", None)
        if nt == "parallel_subagents" and branch_graphs:
            sub_graph = None  # Handled below per-branch
        elif nt == "orchestrator" and teams:
            sub_graph = None  # Handled below per-team
        elif nt == "agent_team" and agents:
            sub_graph = None  # Handled below per-agent
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

        if nt == "agent_team" and agents:
            for agent_name, sub_key in agents.items():
                agent_sub = self._resolve_sub_graph(sub_key)
                agent_var = f"_{body_var}_{agent_name}"
                if agent_sub and agent_sub.nodes:
                    lines.append(f"{inner_pad}with {body_var}.agent({agent_name!r}) as {agent_var}:")
                    agent_lines = self._emit_parallel_branch_content(
                        agent_sub, agent_var, indent + 2
                    )
                    lines.extend(agent_lines)
                else:
                    lines.append(f"{inner_pad}with {body_var}.agent({agent_name!r}) as {agent_var}:")
                    lines.append(f"{inner_pad}    pass")
            return lines

        handled_body = False
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
                if self._is_scoped_node(sub_node):
                    nested_body_var = f"_{sub_var}_body"
                    nested_lines = self._emit_subgraph_node(
                        sub_node,
                        nested_body_var,
                        indent=indent + 1,
                        builder_var=body_var,
                    )
                    lines.extend(nested_lines)
                    lines.append(
                        f"{inner_pad}{sub_var} = NodeRef({sub_node.id!r}, {sub_node.node_type!r}, {body_var})"
                    )
                else:
                    call = self._emit_node_call_scoped(sub_node, body_var)
                    lines.append(f"{inner_pad}{sub_var} = {call}")

            sub_chains = self._detect_chains_in(sub_graph)
            emitted_chain_pairs: set[tuple[str, str]] = set()
            sub_node_map = {n.id: n for n in sub_graph.nodes}
            subgraph_node_ids = {n.id for n in sub_graph.nodes if self._is_scoped_node(n)}
            for chain in sub_chains:
                current_segment = []
                for nid in chain:
                    if nid in subgraph_node_ids:
                        if len(current_segment) >= 2:
                            chain_str = " >> ".join(_to_var_name(nid_) for nid_ in current_segment)
                            lines.append(f"{inner_pad}{chain_str}")
                            for i in range(len(current_segment) - 1):
                                emitted_chain_pairs.add((current_segment[i], current_segment[i + 1]))
                        current_segment = []
                        continue
                    if not current_segment:
                        current_segment = [nid]
                        continue
                    prev = current_segment[-1]
                    if self._is_default_chain_edge(prev, nid):
                        current_segment.append(nid)
                    else:
                        if len(current_segment) >= 2:
                            chain_str = " >> ".join(_to_var_name(nid_) for nid_ in current_segment)
                            lines.append(f"{inner_pad}{chain_str}")
                            for i in range(len(current_segment) - 1):
                                emitted_chain_pairs.add((current_segment[i], current_segment[i + 1]))
                        current_segment = [nid]
                if len(current_segment) >= 2:
                    chain_str = " >> ".join(_to_var_name(nid_) for nid_ in current_segment)
                    lines.append(f"{inner_pad}{chain_str}")
                    for i in range(len(current_segment) - 1):
                        emitted_chain_pairs.add((current_segment[i], current_segment[i + 1]))

            for edge in sub_graph.edges:
                if isinstance(edge, DataEdge):
                    pair = (edge.source_node_id, edge.target_node_id)
                    if (
                        pair in emitted_chain_pairs
                        and self._is_default_data_edge_in(edge, sub_node_map)
                        and self._edge_lint_payload(edge) is None
                    ):
                        continue
                    src_var = _to_var_name(edge.source_node_id)
                    tgt_var = _to_var_name(edge.target_node_id)
                    spread_arg = ", spread=True" if getattr(edge, "spread", False) else ""
                    lint_meta = self._edge_lint_payload(edge)
                    lint_arg = f", lint={lint_meta!r}" if lint_meta is not None else ""
                    lines.append(
                        f'{inner_pad}{body_var}.edge({src_var}["{edge.source_port}"], '
                        f'{tgt_var}["{edge.target_port}"]{spread_arg}{lint_arg})'
                    )
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
            handled_body = True

        if nt == "worker" and getattr(node, "sub_workers", None):
            for alias, sub_key in sorted(node.sub_workers.items()):
                sub_worker_graph = self._resolve_sub_graph(sub_key)
                alias_var = f"_{body_var}_{alias}"
                lines.append(f"{inner_pad}with {body_var}.sub_worker({alias!r}) as {alias_var}:")
                if sub_worker_graph and sub_worker_graph.nodes:
                    lines.extend(
                        self._emit_parallel_branch_content(sub_worker_graph, alias_var, indent + 2)
                    )
                else:
                    lines.append(f"{inner_pad}    pass")
            handled_body = True

        if not handled_body:
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
            if self._is_scoped_node(sub_node):
                nested_body_var = f"_{sub_var}_body"
                nested_lines = self._emit_subgraph_node(
                    sub_node,
                    nested_body_var,
                    indent=indent,
                    builder_var=scope_var,
                )
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
        subgraph_node_ids = {n.id for n in sub_graph.nodes if self._is_scoped_node(n)}
        for chain in sub_chains:
            current_segment: list[str] = []
            for nid in chain:
                if nid in subgraph_node_ids:
                    if len(current_segment) >= 2:
                        chain_str = " >> ".join(_to_var_name(nid_) for nid_ in current_segment)
                        lines.append(f"{inner_pad}{chain_str}")
                        for i in range(len(current_segment) - 1):
                            emitted_chain_pairs.add((current_segment[i], current_segment[i + 1]))
                    current_segment = []
                    continue
                if not current_segment:
                    current_segment = [nid]
                    continue
                prev = current_segment[-1]
                if self._is_default_chain_edge_in(sub_graph, prev, nid):
                    current_segment.append(nid)
                else:
                    if len(current_segment) >= 2:
                        chain_str = " >> ".join(_to_var_name(nid_) for nid_ in current_segment)
                        lines.append(f"{inner_pad}{chain_str}")
                        for i in range(len(current_segment) - 1):
                            emitted_chain_pairs.add((current_segment[i], current_segment[i + 1]))
                    current_segment = [nid]
            if len(current_segment) >= 2:
                chain_str = " >> ".join(_to_var_name(nid_) for nid_ in current_segment)
                lines.append(f"{inner_pad}{chain_str}")
                for i in range(len(current_segment) - 1):
                    emitted_chain_pairs.add((current_segment[i], current_segment[i + 1]))

        for edge in sub_graph.edges:
            if isinstance(edge, DataEdge):
                pair = (edge.source_node_id, edge.target_node_id)
                if (
                    pair in emitted_chain_pairs
                    and self._is_default_data_edge_in(edge, sub_node_map)
                    and self._edge_lint_payload(edge) is None
                ):
                    continue
                src_var = _to_var_name(edge.source_node_id)
                tgt_var = _to_var_name(edge.target_node_id)
                spread_arg = ", spread=True" if getattr(edge, "spread", False) else ""
                lint_meta = self._edge_lint_payload(edge)
                lint_arg = f", lint={lint_meta!r}" if lint_meta is not None else ""
                lines.append(
                    f'{inner_pad}{scope_var}.edge({src_var}["{edge.source_port}"], '
                    f'{tgt_var}["{edge.target_port}"]{spread_arg}{lint_arg})'
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

    def _emit_hyperedge(self, he: Hyperedge) -> str:
        """Generate a wf.skill() or wf.rule() call for a hyperedge."""
        selector_kwargs: list[str] = []
        if he.attach_to:
            selector_kwargs.append(f"attach_to={he.attach_to!r}")
        if he.attach_to_type:
            selector_kwargs.append(f"attach_to_type={he.attach_to_type!r}")
        if he.attach_to_tags:
            selector_kwargs.append(f"attach_to_tags={he.attach_to_tags!r}")
        if he.attach_to_subgraph:
            selector_kwargs.append(f"attach_to_subgraph={he.attach_to_subgraph!r}")
        if he.attach_globally:
            selector_kwargs.append("attach_globally=True")
        if not he.propagate:
            selector_kwargs.append("propagate=False")

        if he.hyperedge_type == "skill":
            args = [repr(he.name), repr(he.content)]
            if selector_kwargs:
                args.extend(selector_kwargs)
            return f"wf.skill({', '.join(args)})"
        else:
            args = [repr(he.name), repr(he.hyperedge_type), repr(he.hook), repr(he.content)]
            severity = he.config.get("severity", "warning")
            block_on_fail = he.config.get("block_on_fail", False)
            if severity != "warning":
                args.append(f"severity={severity!r}")
            if block_on_fail:
                args.append(f"block_on_fail={block_on_fail!r}")
            if selector_kwargs:
                args.extend(selector_kwargs)
            return f"wf.rule({', '.join(args)})"

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
        return self._is_default_chain_edge_in(self.graph, src_node_id, tgt_node_id)

    @staticmethod
    def _is_default_chain_edge_in(graph: Graph, src_node_id: str, tgt_node_id: str) -> bool:
        data_edges = [
            e for e in graph.edges
            if isinstance(e, DataEdge)
            and e.source_node_id == src_node_id
            and e.target_node_id == tgt_node_id
        ]
        if len(data_edges) != 1:
            return False
        node_map = {n.id: n for n in graph.nodes}
        return _Decompiler._is_default_data_edge_in(data_edges[0], node_map)

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
