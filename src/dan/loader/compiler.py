"""Compile markdown workflow specs into ``dan_graph_v1`` graphs."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from dan.loader.diagnostics import CompileResult, Diagnostic, format_diagnostics
from dan.loader.flow_parser import FlowParseError, is_block_reference, parse_flow_line
from dan.loader.models import (
    AgentSpec,
    ChainStatement,
    ContextSpec,
    EachStatement,
    FlowStatement,
    HyperedgeSpec,
    IfStatement,
    LoopStatement,
    ParallelStatement,
    PortSpec,
    SourceLocation,
)
from dan.loader.parser import ParseError, load_hyperedge, parse_agent_file, parse_workflow_file
from dan.loader.types import (
    SchemaLoadError,
    infer_port_schema,
    infer_schema_from_name,
    load_linked_schema,
)
from dan.models.context import MergeStrategy, SharedContextDeclaration
from dan.models.control_flow import (
    CompositeNode,
    ForEachNode,
    GateNode,
    HumanInTheLoopNode,
    InputNode,
    InputVariable,
    OrchestratorNode,
    ParallelSubagentsNode,
    RouterNode,
)
from dan.models.edges import DataEdge
from dan.models.graph import Graph, GraphMetadata
from dan.models.hyperedges import Hyperedge
from dan.models.nodes import CodeOperator, LLMOperator, NodeBase, ReflectionNode, RetryPolicy, ToolOperator
from dan.models.ports import InputPort, OutputPort

_BLOCK_REF_RE = re.compile(r"^(?P<name>[^@]+)@(?P<version>\d+\.\d+\.\d+[\w.+-]*)$")


def _try_resolve_block(
    ref: str,
    nodes_by_id: dict[str, "NodeBase"],
    sub_graphs: dict[str, "Graph"],
    diagnostics: list["Diagnostic"],
    *,
    strict: bool = False,
) -> "NodeBase | None":
    """If *ref* is a block reference (``name@version``), resolve it via BlockRegistry.

    On success, creates a ``CompositeNode`` backed by the block's graph and
    inserts it into *nodes_by_id* and *sub_graphs*.  Returns the node or
    ``None`` on failure.
    """
    m = _BLOCK_REF_RE.match(ref)
    if m is None:
        return None

    block_name, block_version = m.group("name"), m.group("version")

    try:
        from dan.blocks.registry import BlockRegistry
        from dan.blocks.executor import load_block_as_graph
    except ImportError:
        _emit_error(
            diagnostics,
            f"Block reference '{ref}' found but dan.blocks is not available",
        )
        return None

    registry = BlockRegistry()
    registry.scan()

    block = registry.get_block(block_name, block_version)
    if block is None:
        if strict:
            _emit_error(
                diagnostics,
                f"Block '{ref}' is not installed",
            )
        else:
            _emit_warning(
                diagnostics,
                f"Block '{ref}' is not installed — node will be unresolved",
                hint=f"Install with: dan-blocks install <path> or ensure {block_name}@{block_version} is in ~/.dan/blocks/",
            )
        return None

    try:
        block_graph = load_block_as_graph(ref, registry)
    except ValueError as exc:
        _emit_error(diagnostics, f"Failed to load block graph for '{ref}': {exc}")
        return None

    node_id = ref.replace("@", "_v")
    sub_key = f"{node_id}__body"
    sub_graphs[sub_key] = block_graph

    node = CompositeNode(
        id=node_id,
        name=block_name,
        body_graph=sub_key,
        input_ports=[
            InputPort(name=p.name, json_schema=p.json_schema, required=getattr(p, "required", True))
            for p in (block_graph.nodes[0].input_ports if block_graph.nodes else [])
        ] or [InputPort(name=DEFAULT_INPUT_PORT, json_schema={"type": "object"}, required=False)],
        output_ports=[
            OutputPort(name=p.name, json_schema=p.json_schema)
            for p in (block_graph.nodes[-1].output_ports if block_graph.nodes else [])
        ] or [OutputPort(name="result", json_schema={"type": "string"})],
        metadata={"block_name": block_name, "block_version": block_version},
    )
    nodes_by_id[node_id] = node
    return node


DEFAULT_OUTPUT_PORTS: dict[str, str] = {
    "llm_operator": "text",
    "tool_operator": "result",
    "code_operator": "result",
    "gate": "true",
    "for_each": "results",
    "parallel_subagents": "results",
    "orchestrator": "results",
    "router": "route",
    "human_in_the_loop": "response",
    "composite": "result",
    "reflection": "principles",
    "input": "output",
}
DEFAULT_INPUT_PORT = "input"

_VALIDATION_WARNING_PATTERNS = (
    "schema safety bypassed",
    "untyped data edge",
    "deprecated",
    "warning:",
)

_HYPEREDGE_LINE_RE = re.compile(
    r"^-\s+"
    r"(?:"
    r"inline:\s*\"([^\"]+)\""   # group(1): inline content
    r"|"
    r"(.+?\.md)"               # group(2): file reference (allows spaces)
    r")"
    r"(?:\s*->\s*(.+))?"       # group(3): scope overrides
    r"\s*$",
)

_SCOPE_RE = re.compile(r"@(\w+)\(([^)]*)\)")
_SCOPE_GLOBAL_RE = re.compile(r"@global")


def compile(path: str | Path) -> Graph:
    """Compile workflow markdown and raise on errors."""
    result = compile_workflow(path)
    if result.graph is None:
        raise ValueError(f"Workflow compilation failed:\n{format_diagnostics(result.diagnostics)}")
    return result.graph


def compile_workflow(workflow_path: str | Path, *, strict: bool = False) -> CompileResult:
    """Compile a workflow markdown file into a Graph with diagnostics.

    When strict=True, parse warnings and ambiguous bare-edge auto-wire become
    fatal errors; compilation stops and returns graph=None.
    """
    workflow_file = Path(workflow_path)
    diagnostics: list[Diagnostic] = []

    try:
        workflow_spec = parse_workflow_file(workflow_file)
    except (ParseError, FlowParseError, FileNotFoundError, OSError) as exc:
        _emit_error(
            diagnostics,
            f"Failed to parse workflow file: {exc}",
            source_file=workflow_file,
        )
        return CompileResult(graph=None, diagnostics=diagnostics)

    for msg, src in workflow_spec.parse_warnings:
        if strict:
            _emit_error(diagnostics, msg, source=src)
        else:
            _emit_warning(diagnostics, msg, source=src)

    if strict and workflow_spec.parse_warnings:
        return CompileResult(graph=None, diagnostics=diagnostics)

    workflow_dir = workflow_file.parent
    agent_specs: dict[str, AgentSpec] = {}
    for agent_id, rel_path in workflow_spec.agents.items():
        agent_path = (workflow_dir / rel_path).resolve()
        if not agent_path.exists():
            _emit_error(
                diagnostics,
                f"Agent file not found for '{agent_id}': {agent_path}",
                source_file=workflow_file,
            )
            continue
        try:
            spec = parse_agent_file(agent_path)
            spec.name = agent_id
            agent_specs[agent_id] = spec
        except (ParseError, FileNotFoundError, OSError) as exc:
            _emit_error(
                diagnostics,
                f"Failed to parse agent file '{agent_id}': {exc}",
                source_file=agent_path,
            )

    nodes_by_id: dict[str, NodeBase] = {}
    sub_graphs: dict[str, Graph] = {}
    for name, spec in agent_specs.items():
        if name in nodes_by_id:
            _emit_error(
                diagnostics,
                f"Duplicate node id '{name}' in workflow agents",
                source=spec.source,
            )
            continue
        node, nested_sub_graphs = _compile_agent(name, spec, diagnostics, strict=strict)
        if node is None:
            continue
        nodes_by_id[name] = node
        for key, sub_graph in nested_sub_graphs.items():
            if key in sub_graphs:
                _emit_error(
                    diagnostics,
                    f"Duplicate sub-graph key '{key}' generated during agent compilation",
                    source=spec.source,
                )
            else:
                sub_graphs[key] = sub_graph

    edge_counter = [0]
    flow_edges, flow_nodes, flow_subgraphs = _compile_flow(
        workflow_spec.flow_statements,
        nodes_by_id,
        agent_specs,
        diagnostics,
        edge_counter,
        strict=strict,
    )
    for node in flow_nodes:
        if node.id in nodes_by_id:
            # Already added during flow (loop/if/each add to nodes_by_id for later refs)
            continue
        nodes_by_id[node.id] = node
    for key, sub_graph in flow_subgraphs.items():
        if key in sub_graphs:
            _emit_error(
                diagnostics,
                f"Duplicate sub-graph key '{key}' generated by flow compilation",
                source_file=workflow_file,
            )
            continue
        sub_graphs[key] = sub_graph

    _auto_wire(list(nodes_by_id.values()), flow_edges, diagnostics, strict=strict)
    input_node = _create_input_node(
        list(nodes_by_id.values()),
        flow_edges,
        diagnostics,
        edge_counter,
    )
    if input_node is not None:
        nodes_by_id[input_node.id] = input_node

    shared_context = _compile_context(workflow_spec.context_declarations, diagnostics)

    hyperedge_specs = list(workflow_spec.hyperedges)
    from dan.loader.parser import _split_frontmatter as _split_fm  # noqa: F811

    sections = _extract_workflow_sections(workflow_file)
    for section_name, default_type in (("skills", "skill"), ("rules", None)):
        section_text = sections.get(section_name)
        if section_text is None:
            continue
        _parse_hyperedge_section(
            section_text,
            default_type=default_type,
            workflow_dir=workflow_dir,
            diagnostics=diagnostics,
            out=hyperedge_specs,
            source_file=workflow_file,
        )

    compiled_hyperedges = _compile_hyperedges(hyperedge_specs, diagnostics)

    nodes = list(nodes_by_id.values())
    entry_points = _find_entry_points(nodes, flow_edges)
    if input_node is not None:
        entry_points = [input_node.id]
    exit_points = _find_exit_points(nodes, flow_edges)

    graph = Graph(
        metadata=GraphMetadata(
            name=workflow_spec.name,
            description=workflow_spec.description,
            tags=list(workflow_spec.tags),
        ),
        nodes=nodes,
        edges=flow_edges,
        sub_graphs=sub_graphs,
        entry_points=entry_points,
        exit_points=exit_points,
        shared_context=shared_context,
        hyperedges=compiled_hyperedges,
    )

    try:
        from dan.validation.graph import validate_graph

        validation_messages = validate_graph(graph)
        for msg in validation_messages:
            level = "warning" if _is_validation_warning(msg) else "error"
            diagnostics.append(
                Diagnostic(
                    level=level,
                    message=msg,
                    source_file=workflow_file,
                )
            )
    except Exception as exc:
        _emit_error(
            diagnostics,
            f"Graph validation failed unexpectedly: {exc}",
            source_file=workflow_file,
        )

    if any(d.level == "error" for d in diagnostics):
        return CompileResult(graph=None, diagnostics=diagnostics)
    return CompileResult(graph=graph, diagnostics=diagnostics)


def _compile_agent(
    name: str,
    spec: AgentSpec,
    diagnostics: list[Diagnostic],
    *,
    strict: bool = False,
) -> tuple[NodeBase | None, dict[str, Graph]]:
    input_ports = _build_input_ports(spec, diagnostics)
    output_ports = _build_output_ports(spec, diagnostics)
    metadata = _source_metadata(spec.source, spec.file_path)
    nested_sub_graphs: dict[str, Graph] = {}
    node: NodeBase | None = None

    if spec.agent_type == "llm":
        node = LLMOperator(
            id=name,
            name=name,
            model=spec.model,
            prompt_template=spec.prompt_body,
            system_prompt=spec.system_prompt,
            temperature=spec.temperature,
            max_tokens=spec.max_tokens,
            output_json_schema=spec.output_schema,
            input_ports=input_ports,
            output_ports=output_ports,
            metadata=metadata,
        )
    elif spec.agent_type == "tool":
        node = ToolOperator(
            id=name,
            name=name,
            tool_id=spec.tool_id,
            tool_config=dict(spec.tool_config),
            input_ports=input_ports,
            output_ports=output_ports,
            metadata=metadata,
        )
    elif spec.agent_type == "code":
        node = CodeOperator(
            id=name,
            name=name,
            code=spec.prompt_body,
            language=spec.language or "python",
            input_ports=input_ports,
            output_ports=output_ports,
            metadata=metadata,
        )
    elif spec.agent_type == "human":
        node = HumanInTheLoopNode(
            id=name,
            name=name,
            prompt=spec.prompt_body,
            timeout_seconds=spec.timeout_seconds,
            default_action=spec.default_action,
            input_ports=input_ports,
            output_ports=output_ports,
            metadata=metadata,
        )
    elif spec.agent_type == "router":
        node = RouterNode(
            id=name,
            name=name,
            model=spec.model,
            route_descriptions=dict(spec.route_descriptions),
            input_ports=input_ports,
            output_ports=output_ports,
            metadata=metadata,
        )
    elif spec.agent_type == "composite":
        sub_key = f"{name}__body"
        sub_graph = _compile_composite_subgraph(name, spec, diagnostics, strict=strict)
        nested_sub_graphs[sub_key] = sub_graph
        node = CompositeNode(
            id=name,
            name=name,
            body_graph=sub_key,
            input_ports=input_ports,
            output_ports=output_ports,
            metadata=metadata,
        )
    elif spec.agent_type == "reflection":
        raw = spec.raw_frontmatter
        config: dict[str, Any] = {}
        if spec.model:
            config["reflection_model"] = spec.model
        for key in (
            "reflection_prompt",
            "source",
            "source_config",
            "output_format",
            "max_principles",
            "min_confidence",
            "dedup_strategy",
        ):
            if key in raw:
                config[key] = raw[key]
        reflection_prompt = config.get("reflection_prompt", spec.prompt_body or "")
        node = ReflectionNode(
            id=name,
            name=name,
            reflection_prompt=reflection_prompt,
            reflection_model=config.get("reflection_model"),
            source=config.get("source", "last_run"),
            source_config=config.get("source_config") or {},
            output_format=config.get("output_format", "principles"),
            max_principles=config.get("max_principles", 10),
            min_confidence=config.get("min_confidence", 0.3),
            dedup_strategy=config.get("dedup_strategy", "embedding_similarity"),
            input_ports=input_ports,
            output_ports=output_ports,
            metadata=metadata,
        )
    else:
        _emit_error(
            diagnostics,
            f"Unsupported agent type '{spec.agent_type}' on '{name}'",
            source=spec.source,
        )
        return None, nested_sub_graphs

    if spec.retry_policy:
        try:
            node.retry_policy = RetryPolicy(**spec.retry_policy)
        except Exception as exc:
            _emit_error(
                diagnostics,
                f"Invalid retry_policy on '{name}': {exc}",
                source=spec.source,
            )

    return node, nested_sub_graphs


def _agents_used_outside_each_body(statements: list[FlowStatement]) -> set[str]:
    """Agents that appear in chains, if/else, loop, or parallel (not only as each body)."""
    used: set[str] = set()
    for stmt in statements:
        if isinstance(stmt, ChainStatement):
            used.update(stmt.agents)
        elif isinstance(stmt, EachStatement):
            used.add(stmt.source_agent)  # body lives in subgraph only
        elif isinstance(stmt, IfStatement):
            used.update((stmt.then_agent, stmt.else_agent))
        elif isinstance(stmt, LoopStatement):
            used.add(stmt.body_agent)
        elif isinstance(stmt, ParallelStatement):
            used.add(stmt.source_agent)  # branches live in subgraphs only
    return used


def _compile_flow(
    statements: list[FlowStatement],
    nodes_by_id: dict[str, NodeBase],
    agent_specs: dict[str, AgentSpec],
    diagnostics: list[Diagnostic],
    edge_counter: list[int],
    *,
    strict: bool = False,
) -> tuple[list[DataEdge], list[NodeBase], dict[str, Graph]]:
    edges: list[DataEdge] = []
    generated_nodes: list[NodeBase] = []
    generated_subgraphs: dict[str, Graph] = {}
    generated_ids: set[str] = set()
    generated_subgraph_ids: set[str] = set()
    agents_used_outside_each = _agents_used_outside_each_body(statements)

    for stmt in statements:
        if isinstance(stmt, ChainStatement):
            edges.extend(
                _compile_chain_statement(
                    stmt,
                    nodes_by_id,
                    diagnostics,
                    edge_counter,
                    strict=strict,
                    sub_graphs=generated_subgraphs,
                )
            )
            continue

        if isinstance(stmt, EachStatement):
            if stmt.source_agent not in nodes_by_id:
                _emit_error(
                    diagnostics,
                    f"Unknown source agent '{stmt.source_agent}' in each()",
                    source=stmt.source,
                )
                continue
            if stmt.body_agent not in nodes_by_id or stmt.body_agent not in agent_specs:
                _emit_error(
                    diagnostics,
                    f"Unknown body agent '{stmt.body_agent}' in each()",
                    source=stmt.source,
                )
                continue

            foreach_id = _ensure_unique_id(
                f"{stmt.source_agent}_each_{stmt.body_agent}",
                set(nodes_by_id) | generated_ids,
            )
            generated_ids.add(foreach_id)

            body_spec = agent_specs[stmt.body_agent]
            body_node, nested = _compile_agent(stmt.body_agent, body_spec, diagnostics)
            if body_node is None:
                _emit_error(
                    diagnostics,
                    f"Could not compile body agent '{stmt.body_agent}' for each()",
                    source=stmt.source,
                )
                continue

            sub_key = _ensure_unique_id(
                f"{foreach_id}__body",
                set(generated_subgraphs) | generated_subgraph_ids,
            )
            generated_subgraph_ids.add(sub_key)

            body_graph = Graph(
                metadata=GraphMetadata(name=f"{foreach_id}_body"),
                nodes=[body_node],
                edges=[],
                sub_graphs=nested,
                entry_points=[body_node.id],
                exit_points=[body_node.id],
            )
            generated_subgraphs[sub_key] = body_graph

            foreach_node = ForEachNode(
                id=foreach_id,
                name=foreach_id,
                body_graph=sub_key,
                parallelism=max(1, stmt.parallel),
                input_ports=[
                    InputPort(
                        name="items",
                        json_schema={},
                    )
                ],
                output_ports=[
                    OutputPort(
                        name="results",
                        json_schema={},
                    )
                ],
                metadata=_source_metadata(stmt.source),
            )
            generated_nodes.append(foreach_node)
            nodes_by_id[foreach_id] = foreach_node  # so subsequent flow can reference each node (e.g. each.results → merge)

            source_node = nodes_by_id[stmt.source_agent]
            source_port = (
                stmt.source_port
                if stmt.source_port
                and any(p.name == stmt.source_port for p in source_node.output_ports)
                else _default_output_port(source_node)
            )
            edges.append(
                _make_data_edge(
                    stmt.source_agent,
                    source_port,
                    foreach_id,
                    "items",
                    edge_counter,
                    stmt.source,
                )
            )
            # Body used only in this each: remove from main graph so we don't create
            # workflow_inputs.item → body (body receives items from ForEach at runtime)
            if stmt.body_agent not in agents_used_outside_each:
                nodes_by_id.pop(stmt.body_agent, None)
            continue

        if isinstance(stmt, LoopStatement):
            if stmt.source_agent not in nodes_by_id:
                _emit_error(
                    diagnostics,
                    f"Unknown source agent '{stmt.source_agent}' in loop()",
                    source=stmt.source,
                )
                continue
            if stmt.body_agent not in nodes_by_id:
                _emit_error(
                    diagnostics,
                    f"Unknown body agent '{stmt.body_agent}' in loop()",
                    source=stmt.source,
                )
                continue

            gate_id = _ensure_unique_id(
                f"{stmt.source_agent}_loop_{stmt.body_agent}",
                set(nodes_by_id) | generated_ids,
            )
            generated_ids.add(gate_id)
            # The flow syntax is `until: <condition>` (stop when true),
            # but the gate executor uses while semantics (continue while true).
            # Negate the until-condition to get the while-condition.
            while_condition = f"not ({stmt.condition})"
            gate_node = GateNode(
                id=gate_id,
                name=gate_id,
                gate_mode="while",
                condition=while_condition,
                max_iterations=max(1, stmt.max_iterations),
                input_ports=[InputPort(name=DEFAULT_INPUT_PORT, required=True)],
                state_schema=stmt.state_schema,
                state_defaults=stmt.state_defaults,
                metadata=_source_metadata(stmt.source),
            )
            nodes_by_id[gate_id] = gate_node  # add immediately so subsequent flow can reference gate (e.g. gate.done → next)
            # Do not append to generated_nodes — already in nodes_by_id; avoids duplicate-id error

            source_node = nodes_by_id[stmt.source_agent]
            body_node = nodes_by_id[stmt.body_agent]
            body_target_port = _default_input_port(body_node)
            body_source_port = _default_output_port(body_node)

            edges.append(
                _make_data_edge(
                    stmt.source_agent,
                    _default_output_port(source_node),
                    gate_id,
                    DEFAULT_INPUT_PORT,
                    edge_counter,
                    stmt.source,
                )
            )
            edges.append(
                _make_data_edge(
                    gate_id,
                    "continue",
                    stmt.body_agent,
                    body_target_port,
                    edge_counter,
                    stmt.source,
                )
            )
            edges.append(
                _make_data_edge(
                    stmt.body_agent,
                    body_source_port,
                    gate_id,
                    DEFAULT_INPUT_PORT,
                    edge_counter,
                    stmt.source,
                )
            )
            continue

        if isinstance(stmt, IfStatement):
            if stmt.source_agent not in nodes_by_id:
                _emit_error(
                    diagnostics,
                    f"Unknown source agent '{stmt.source_agent}' in if()",
                    source=stmt.source,
                )
                continue
            if stmt.then_agent not in nodes_by_id:
                _emit_error(
                    diagnostics,
                    f"Unknown then agent '{stmt.then_agent}' in if()",
                    source=stmt.source,
                )
                continue
            if stmt.else_agent not in nodes_by_id:
                _emit_error(
                    diagnostics,
                    f"Unknown else agent '{stmt.else_agent}' in if()",
                    source=stmt.source,
                )
                continue

            gate_id = _ensure_unique_id(
                f"{stmt.source_agent}_if_{stmt.then_agent}_{stmt.else_agent}",
                set(nodes_by_id) | generated_ids,
            )
            generated_ids.add(gate_id)
            gate_node = GateNode(
                id=gate_id,
                name=gate_id,
                gate_mode="if_else",
                condition=stmt.condition,
                input_ports=[InputPort(name=DEFAULT_INPUT_PORT, required=True)],
                metadata=_source_metadata(stmt.source),
            )
            nodes_by_id[gate_id] = gate_node  # so subsequent flow can reference gate (e.g. gate.false → run_strategy)
            generated_nodes.append(gate_node)

            source_node = nodes_by_id[stmt.source_agent]
            then_node = nodes_by_id[stmt.then_agent]
            else_node = nodes_by_id[stmt.else_agent]
            # Prefer "result" when source has it (full structured output for condition + branch pass-through)
            source_port = _default_output_port(source_node)
            if any(p.name == "result" for p in source_node.output_ports):
                source_port = "result"
            edges.append(
                _make_data_edge(
                    stmt.source_agent,
                    source_port,
                    gate_id,
                    DEFAULT_INPUT_PORT,
                    edge_counter,
                    stmt.source,
                )
            )
            edges.append(
                _make_data_edge(
                    gate_id,
                    "true",
                    stmt.then_agent,
                    _default_input_port(then_node),
                    edge_counter,
                    stmt.source,
                )
            )
            edges.append(
                _make_data_edge(
                    gate_id,
                    "false",
                    stmt.else_agent,
                    _default_input_port(else_node),
                    edge_counter,
                    stmt.source,
                )
            )
            continue

        if isinstance(stmt, ParallelStatement):
            if stmt.source_agent not in nodes_by_id:
                _emit_error(
                    diagnostics,
                    f"Unknown source agent '{stmt.source_agent}' in parallel()",
                    source=stmt.source,
                )
                continue
            for branch_agent in stmt.branch_agents:
                if branch_agent not in nodes_by_id or branch_agent not in agent_specs:
                    _emit_error(
                        diagnostics,
                        f"Unknown branch agent '{branch_agent}' in parallel()",
                        source=stmt.source,
                    )
                    continue

            parallel_id = _ensure_unique_id(
                f"{stmt.source_agent}_parallel",
                set(nodes_by_id) | generated_ids,
            )
            generated_ids.add(parallel_id)

            branch_graphs: list[str] = []
            for branch_agent in stmt.branch_agents:
                branch_spec = agent_specs[branch_agent]
                branch_node, nested = _compile_agent(
                    branch_agent, branch_spec, diagnostics
                )
                if branch_node is None:
                    _emit_error(
                        diagnostics,
                        f"Could not compile branch agent '{branch_agent}' for parallel()",
                        source=stmt.source,
                    )
                    continue

                sub_key = _ensure_unique_id(
                    f"{parallel_id}__{branch_agent}",
                    set(generated_subgraphs) | generated_subgraph_ids,
                )
                generated_subgraph_ids.add(sub_key)

                branch_graph = Graph(
                    metadata=GraphMetadata(name=f"{parallel_id}_{branch_agent}"),
                    nodes=[branch_node],
                    edges=[],
                    sub_graphs=nested,
                    entry_points=[branch_node.id],
                    exit_points=[branch_node.id],
                )
                generated_subgraphs[sub_key] = branch_graph
                branch_graphs.append(sub_key)

            if not branch_graphs:
                continue

            _VALID_MERGE_STRATEGIES = {"append", "last_write_wins", "reducer"}
            merge_str = stmt.merge or "append"
            if merge_str not in _VALID_MERGE_STRATEGIES:
                _emit_error(
                    diagnostics,
                    f"Unknown merge strategy '{merge_str}' in parallel(); expected one of {sorted(_VALID_MERGE_STRATEGIES)}",
                    source=stmt.source,
                )
            merge_strategy = MergeStrategy.APPEND
            if merge_str == "last_write_wins":
                merge_strategy = MergeStrategy.LAST_WRITE_WINS
            elif merge_str == "reducer":
                merge_strategy = MergeStrategy.REDUCER

            parallel_node = ParallelSubagentsNode(
                id=parallel_id,
                name=parallel_id,
                branch_graphs=branch_graphs,
                parallelism=max(1, stmt.parallel),
                merge_strategy=merge_strategy,
                input_ports=[
                    InputPort(name=DEFAULT_INPUT_PORT, required=True),
                ],
                output_ports=[
                    OutputPort(name="results", json_schema={}),
                ],
                metadata=_source_metadata(stmt.source),
            )
            generated_nodes.append(parallel_node)
            nodes_by_id[parallel_id] = parallel_node

            source_node = nodes_by_id[stmt.source_agent]
            edges.append(
                _make_data_edge(
                    stmt.source_agent,
                    _default_output_port(source_node),
                    parallel_id,
                    DEFAULT_INPUT_PORT,
                    edge_counter,
                    stmt.source,
                )
            )
            for branch_agent in stmt.branch_agents:
                if branch_agent not in agents_used_outside_each:
                    nodes_by_id.pop(branch_agent, None)
            continue

        _emit_error(
            diagnostics,
            f"Unsupported flow statement '{type(stmt).__name__}'",
            source=getattr(stmt, "source", None),
        )

    return edges, generated_nodes, generated_subgraphs


def _compile_chain_statement(
    stmt: ChainStatement,
    nodes_by_id: dict[str, NodeBase],
    diagnostics: list[Diagnostic],
    edge_counter: list[int],
    *,
    strict: bool = False,
    sub_graphs: dict[str, Graph] | None = None,
) -> list[DataEdge]:
    edges: list[DataEdge] = []
    if len(stmt.agents) < 2:
        _emit_error(
            diagnostics,
            "Chain statement requires at least two agents",
            source=stmt.source,
        )
        return edges

    _sub_graphs = sub_graphs if sub_graphs is not None else {}

    for index in range(len(stmt.agents) - 1):
        source_id = stmt.agents[index]
        target_id = stmt.agents[index + 1]

        if source_id not in nodes_by_id and is_block_reference(source_id):
            node = _try_resolve_block(source_id, nodes_by_id, _sub_graphs, diagnostics, strict=strict)
            if node is not None:
                source_id = node.id
                stmt.agents[index] = node.id

        if target_id not in nodes_by_id and is_block_reference(target_id):
            node = _try_resolve_block(target_id, nodes_by_id, _sub_graphs, diagnostics, strict=strict)
            if node is not None:
                target_id = node.id
                stmt.agents[index + 1] = node.id

        if source_id not in nodes_by_id:
            _emit_error(
                diagnostics,
                f"Unknown source agent '{source_id}' in chain",
                source=stmt.source,
            )
            continue
        if target_id not in nodes_by_id:
            _emit_error(
                diagnostics,
                f"Unknown target agent '{target_id}' in chain",
                source=stmt.source,
            )
            continue

        source_node = nodes_by_id[source_id]
        target_node = nodes_by_id[target_id]
        explicit_source_port = None
        explicit_target_port = None
        if index < len(stmt.port_pairs):
            explicit_source_port, explicit_target_port = stmt.port_pairs[index]

        source_port, target_port = _resolve_chain_ports(
            source_node,
            target_node,
            explicit_source_port,
            explicit_target_port,
            diagnostics,
            stmt.source,
            strict=strict,
        )
        edges.append(
            _make_data_edge(
                source_id,
                source_port,
                target_id,
                target_port,
                edge_counter,
                stmt.source,
            )
        )
    return edges


def _resolve_chain_ports(
    source_node: NodeBase,
    target_node: NodeBase,
    explicit_source_port: str | None,
    explicit_target_port: str | None,
    diagnostics: list[Diagnostic],
    source: SourceLocation | None,
    *,
    strict: bool = False,
) -> tuple[str, str]:
    source_names = {port.name for port in source_node.output_ports}
    target_names = {port.name for port in target_node.input_ports}

    if explicit_source_port is not None and explicit_target_port is not None:
        if explicit_source_port not in source_names:
            _emit_error(
                diagnostics,
                f"Source node '{source_node.id}' has no output port '{explicit_source_port}'"
                f" (available: {sorted(source_names)})",
                source=source,
            )
        if explicit_target_port not in target_names:
            _emit_error(
                diagnostics,
                f"Target node '{target_node.id}' has no input port '{explicit_target_port}'"
                f" (available: {sorted(target_names)})",
                source=source,
            )
        return explicit_source_port, explicit_target_port

    if explicit_source_port is not None:
        target_port = explicit_source_port if explicit_source_port in target_names else _default_input_port(target_node)
        return explicit_source_port, target_port

    if explicit_target_port is not None:
        source_port = explicit_target_port if explicit_target_port in source_names else _default_output_port(source_node)
        return source_port, explicit_target_port

    matches = sorted(source_names & target_names)
    if len(matches) == 1:
        return matches[0], matches[0]
    if len(matches) > 1:
        ports_str = ", ".join(matches)
        if strict:
            _emit_error(
                diagnostics,
                (
                    f"Ambiguous: {source_node.id} → {target_node.id} has multiple matching ports "
                    f"{{{ports_str}}}. Use explicit .port syntax."
                ),
                source=source,
            )
        else:
            _emit_warning(
                diagnostics,
                (
                    f"Ambiguous auto-wire for '{source_node.id} -> {target_node.id}': "
                    f"multiple shared ports {matches}; using '{matches[0]}'"
                ),
                source=source,
                hint="Use explicit 'A.port_x → B.port_y' to wire all intended ports.",
            )
        return matches[0], matches[0]

    return _default_output_port(source_node), _default_input_port(target_node)


def _auto_wire(
    nodes: list[NodeBase],
    edges: list[DataEdge],
    diagnostics: list[Diagnostic],
    *,
    strict: bool = False,
) -> None:
    node_map = {node.id: node for node in nodes}
    for edge in edges:
        source_node = node_map.get(edge.source_node_id)
        target_node = node_map.get(edge.target_node_id)
        if source_node is None or target_node is None:
            continue

        source_names = {port.name for port in source_node.output_ports}
        target_names = {port.name for port in target_node.input_ports}
        source_valid = edge.source_port in source_names
        target_valid = edge.target_port in target_names
        if source_valid and target_valid:
            continue

        matches = sorted(source_names & target_names)
        if len(matches) == 1:
            edge.source_port = matches[0]
            edge.target_port = matches[0]
            continue
        if len(matches) > 1:
            edge.source_port = matches[0]
            edge.target_port = matches[0]
            ports_str = ", ".join(matches)
            if strict:
                _emit_error(
                    diagnostics,
                    (
                        f"Ambiguous: {edge.source_node_id} → {edge.target_node_id} has multiple "
                        f"matching ports {{{ports_str}}}. Use explicit .port syntax."
                    ),
                )
            else:
                _emit_warning(
                    diagnostics,
                    (
                        f"Ambiguous auto-wire fallback for '{edge.source_node_id} -> {edge.target_node_id}': "
                        f"using shared port '{matches[0]}'"
                    ),
                    hint="Use explicit 'A.port_x → B.port_y' to wire all intended ports.",
                )
            continue

        if not source_valid:
            edge.source_port = _default_output_port(source_node)
        if not target_valid:
            edge.target_port = _default_input_port(target_node)


def _create_input_node(
    nodes: list[NodeBase],
    edges: list[DataEdge],
    diagnostics: list[Diagnostic],
    edge_counter: list[int],
) -> InputNode | None:
    incoming: dict[str, set[str]] = {}
    for edge in edges:
        incoming.setdefault(edge.target_node_id, set()).add(edge.target_port)

    missing: dict[str, list[tuple[NodeBase, InputPort]]] = {}
    for node in nodes:
        if node.node_type == "input":
            continue
        connected = incoming.get(node.id, set())
        for port in node.input_ports:
            if not port.required:
                continue
            if port.name not in connected:
                missing.setdefault(port.name, []).append((node, port))

    if not missing:
        return None

    variables: list[InputVariable] = []
    output_ports: list[OutputPort] = []
    for variable_name in sorted(missing):
        consumer_ports = [port for _, port in missing[variable_name]]
        var_type = _infer_input_variable_type(consumer_ports)
        port_schema = _infer_input_port_schema(consumer_ports)
        variables.append(InputVariable(name=variable_name, type=var_type))
        output_ports.append(
            OutputPort(
                name=variable_name,
                json_schema=port_schema,
            )
        )

    input_node = InputNode(
        id="workflow_inputs",
        name="Workflow Inputs",
        variables=variables,
        output_ports=output_ports,
        metadata={"generated": True},
    )
    for variable_name, consumers in missing.items():
        for node, port in consumers:
            edges.append(
                _make_data_edge(
                    input_node.id,
                    variable_name,
                    node.id,
                    port.name,
                    edge_counter,
                )
            )

    _emit_warning(
        diagnostics,
        (
            f"Auto-generated InputNode with {len(variables)} variable(s): "
            f"{', '.join(v.name for v in variables)}"
        ),
    )
    return input_node


def _compile_context(
    context_specs: list[ContextSpec],
    diagnostics: list[Diagnostic],
) -> list[SharedContextDeclaration]:
    declarations: list[SharedContextDeclaration] = []
    for spec in context_specs:
        try:
            declarations.append(
                SharedContextDeclaration(
                    key=spec.key,
                    description=spec.description,
                )
            )
        except Exception as exc:
            _emit_error(
                diagnostics,
                f"Invalid context declaration '{spec.key}': {exc}",
                source=spec.source,
            )
    return declarations


def _compile_composite_subgraph(
    parent_name: str,
    spec: AgentSpec,
    diagnostics: list[Diagnostic],
    *,
    strict: bool = False,
) -> Graph:
    base_dir = spec.file_path.parent if spec.file_path else Path.cwd()
    internal_specs: dict[str, AgentSpec] = {}
    for alias, rel_path in spec.internal_agents.items():
        nested_path = (base_dir / rel_path).resolve()
        if not nested_path.exists():
            _emit_error(
                diagnostics,
                f"Composite '{parent_name}' references missing agent file: {nested_path}",
                source=spec.source,
            )
            continue
        try:
            nested_spec = parse_agent_file(nested_path)
            nested_spec.name = alias
            internal_specs[alias] = nested_spec
        except (ParseError, FileNotFoundError, OSError) as exc:
            _emit_error(
                diagnostics,
                f"Failed to parse composite internal agent '{alias}': {exc}",
                source=spec.source,
            )

    statements: list[FlowStatement] = []
    for index, flow_line in enumerate(spec.internal_flow_lines):
        try:
            flow_source = SourceLocation(
                file=spec.file_path or Path("<unknown>"),
                line=(spec.source.line if spec.source else 0) + index + 1,
            )
            statements.append(parse_flow_line(flow_line, source=flow_source))
        except FlowParseError as exc:
            _emit_error(
                diagnostics,
                f"Failed to parse composite flow line '{flow_line}': {exc}",
                source=spec.source,
            )

    nodes_by_id: dict[str, NodeBase] = {}
    sub_graphs: dict[str, Graph] = {}
    for alias, nested_spec in internal_specs.items():
        node, nested_subgraphs = _compile_agent(alias, nested_spec, diagnostics)
        if node is None:
            continue
        nodes_by_id[alias] = node
        sub_graphs.update(nested_subgraphs)

    edge_counter = [0]
    edges, flow_nodes, flow_subgraphs = _compile_flow(
        statements,
        nodes_by_id,
        internal_specs,
        diagnostics,
        edge_counter,
        strict=strict,
    )
    for node in flow_nodes:
        if node.id not in nodes_by_id:
            nodes_by_id[node.id] = node
    sub_graphs.update(flow_subgraphs)

    _auto_wire(list(nodes_by_id.values()), edges, diagnostics, strict=strict)
    input_node = _create_input_node(list(nodes_by_id.values()), edges, diagnostics, edge_counter)
    if input_node is not None:
        nodes_by_id[input_node.id] = input_node

    nodes = list(nodes_by_id.values())
    entry_points = _find_entry_points(nodes, edges)
    if input_node is not None:
        entry_points = [input_node.id]
    exit_points = _find_exit_points(nodes, edges)
    return Graph(
        metadata=GraphMetadata(name=f"{parent_name}_composite"),
        nodes=nodes,
        edges=edges,
        sub_graphs=sub_graphs,
        entry_points=entry_points,
        exit_points=exit_points,
    )


def _extract_workflow_sections(workflow_file: Path) -> dict[str, str]:
    """Re-read the workflow file and extract raw section text for ## Skills / ## Rules."""
    import re as _re

    text = workflow_file.read_text(encoding="utf-8")
    if text.startswith("---"):
        lines = text.split("\n")
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is not None:
            text = "\n".join(lines[end + 1:])

    section_re = _re.compile(r"^##\s+(.+)$", _re.MULTILINE)
    headings = list(section_re.finditer(text))
    sections: dict[str, str] = {}
    for i, m in enumerate(headings):
        name = m.group(1).strip().lower()
        start = m.end()
        end_pos = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        sections[name] = text[start:end_pos].strip()
    return sections


def _parse_scope_overrides(scope_str: str) -> dict[str, Any]:
    """Parse ``@nodes(a, b) @type(llm) @global`` into selector dict."""
    result: dict[str, Any] = {}
    if _SCOPE_GLOBAL_RE.search(scope_str):
        result["attach_globally"] = True

    for m in _SCOPE_RE.finditer(scope_str):
        kind = m.group(1).lower()
        args = [a.strip() for a in m.group(2).split(",") if a.strip()]
        if kind == "nodes":
            result["attach_to"] = args
        elif kind == "type":
            result["attach_to_type"] = args
        elif kind == "tags":
            result["attach_to_tags"] = args
        elif kind == "subgraph":
            result["attach_to_subgraph"] = args
    return result


def _parse_hyperedge_section(
    section_text: str,
    *,
    default_type: str | None,
    workflow_dir: Path,
    diagnostics: list[Diagnostic],
    out: list[HyperedgeSpec],
    source_file: Path,
) -> None:
    """Parse lines from a ## Skills or ## Rules section into HyperedgeSpec objects."""
    for line in section_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _HYPEREDGE_LINE_RE.match(line)
        if not m:
            continue

        inline_content = m.group(1)
        file_ref = m.group(2)
        scope_str = m.group(3)

        scope_overrides = _parse_scope_overrides(scope_str) if scope_str else {}

        if inline_content:
            spec = HyperedgeSpec(
                name=f"inline_{hashlib.sha256(inline_content.encode()).hexdigest()[:8]}",
                hyperedge_type=default_type or "skill",
                hook="pre_prompt",
                content=inline_content,
                attach_globally=scope_overrides.get("attach_globally", True),
                attach_to=scope_overrides.get("attach_to", []),
                attach_to_type=scope_overrides.get("attach_to_type", []),
                attach_to_tags=scope_overrides.get("attach_to_tags", []),
                attach_to_subgraph=scope_overrides.get("attach_to_subgraph", []),
            )
            out.append(spec)
            continue

        if file_ref:
            he_path = (workflow_dir / file_ref).resolve()
            if not he_path.exists():
                _emit_error(
                    diagnostics,
                    f"Hyperedge file not found: {he_path}",
                    source_file=source_file,
                )
                continue
            try:
                spec = load_hyperedge(he_path)
                if default_type and spec.hyperedge_type == "skill" and default_type != "skill":
                    pass  # respect the file's own type
                for key, val in scope_overrides.items():
                    setattr(spec, key, val)
                if not scope_overrides and not spec.attach_globally and not any([
                    spec.attach_to, spec.attach_to_type,
                    spec.attach_to_tags, spec.attach_to_subgraph,
                ]):
                    spec.attach_globally = True
                out.append(spec)
            except (ParseError, FileNotFoundError, OSError) as exc:
                _emit_error(
                    diagnostics,
                    f"Failed to load hyperedge '{file_ref}': {exc}",
                    source_file=source_file,
                )


def _hyperedge_id(spec: HyperedgeSpec) -> str:
    """Generate a deterministic ID for a hyperedge from its defining attributes."""
    import hashlib
    if spec.source_file:
        seed = spec.source_file
    else:
        seed = spec.content
    selectors = (
        ",".join(sorted(spec.attach_to))
        + "|" + ",".join(sorted(spec.attach_to_type))
        + "|" + ",".join(sorted(spec.attach_to_tags))
        + "|" + ",".join(sorted(spec.attach_to_subgraph))
        + "|" + str(spec.attach_globally)
    )
    raw = f"{seed}:{selectors}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"he_{spec.name}_{digest}"


def _compile_hyperedges(
    specs: list[HyperedgeSpec],
    diagnostics: list[Diagnostic],
) -> list[Hyperedge]:
    """Convert HyperedgeSpec IR objects into Hyperedge model instances."""
    result: list[Hyperedge] = []
    for spec in specs:
        try:
            he = Hyperedge(
                id=_hyperedge_id(spec),
                name=spec.name,
                hyperedge_type=spec.hyperedge_type,
                hook=spec.hook,
                content=spec.content,
                config=dict(spec.config),
                attach_to=list(spec.attach_to),
                attach_to_type=list(spec.attach_to_type),
                attach_to_tags=list(spec.attach_to_tags),
                attach_to_subgraph=list(spec.attach_to_subgraph),
                attach_globally=spec.attach_globally,
                propagate=spec.propagate,
                priority=spec.priority,
            )
            result.append(he)
        except Exception as exc:
            _emit_error(
                diagnostics,
                f"Failed to compile hyperedge '{spec.name}': {exc}",
            )
    return result


def _build_input_ports(spec: AgentSpec, diagnostics: list[Diagnostic]) -> list[InputPort]:
    base_dir = spec.file_path.parent if spec.file_path else Path.cwd()
    by_name: dict[str, InputPort] = {}
    for port_spec in spec.input_ports:
        schema = _resolve_port_schema(port_spec, base_dir, diagnostics)
        by_name[port_spec.name] = InputPort(
            name=port_spec.name,
            json_schema=schema,
            required=True,
        )

    placeholders = _find_placeholders(spec.prompt_body)
    for placeholder in placeholders:
        if placeholder in by_name:
            continue
        by_name[placeholder] = InputPort(
            name=placeholder,
            json_schema=infer_schema_from_name(placeholder),
            required=True,
        )

    if not by_name:
        by_name[DEFAULT_INPUT_PORT] = InputPort(
            name=DEFAULT_INPUT_PORT,
            json_schema={"type": "object"},
            required=False,
        )
    return list(by_name.values())


def _build_output_ports(spec: AgentSpec, diagnostics: list[Diagnostic]) -> list[OutputPort]:
    base_dir = spec.file_path.parent if spec.file_path else Path.cwd()
    by_name: dict[str, OutputPort] = {}
    for port_spec in spec.output_ports:
        schema = _resolve_port_schema(port_spec, base_dir, diagnostics)
        by_name[port_spec.name] = OutputPort(
            name=port_spec.name,
            json_schema=schema,
        )

    if spec.agent_type == "router":
        if "route" not in by_name:
            by_name["route"] = OutputPort(name="route", json_schema={"type": "string"})
        for route_name in sorted(spec.route_descriptions):
            if route_name not in by_name:
                by_name[route_name] = OutputPort(
                    name=route_name,
                    json_schema={"type": "object"},
                )

    if spec.agent_type == "llm" and spec.output_schema and "result" not in by_name:
        by_name["result"] = OutputPort(
            name="result",
            json_schema={"type": "object", "description": "Full structured output for gate conditions and pass-through"},
        )

    default_name = _default_output_port_for_agent_type(spec.agent_type)
    if default_name not in by_name:
        by_name[default_name] = OutputPort(
            name=default_name,
            json_schema={"type": "string"},
        )
    return list(by_name.values())


def _resolve_port_schema(
    port_spec: PortSpec,
    base_dir: Path,
    diagnostics: list[Diagnostic],
) -> dict[str, Any]:
    if port_spec.schema_path:
        try:
            return load_linked_schema(port_spec.schema_path, base_dir)
        except SchemaLoadError as exc:
            _emit_error(
                diagnostics,
                f"Failed to load schema for port '{port_spec.name}': {exc}",
                source=port_spec.source,
            )
            return {"type": "string"}
    return infer_port_schema(port_spec.name, port_spec.type_annotation)


def _find_placeholders(template: str) -> list[str]:
    return sorted(set(re.findall(r"\{(\w+)\}", template or "")))


def _default_output_port_for_agent_type(agent_type: str) -> str:
    if agent_type == "llm":
        return DEFAULT_OUTPUT_PORTS["llm_operator"]
    if agent_type == "tool":
        return DEFAULT_OUTPUT_PORTS["tool_operator"]
    if agent_type == "code":
        return DEFAULT_OUTPUT_PORTS["code_operator"]
    if agent_type == "router":
        return DEFAULT_OUTPUT_PORTS["router"]
    if agent_type == "human":
        return DEFAULT_OUTPUT_PORTS["human_in_the_loop"]
    if agent_type == "composite":
        return DEFAULT_OUTPUT_PORTS["composite"]
    if agent_type == "reflection":
        return "principles"
    return "result"


def _default_output_port(node: NodeBase) -> str:
    if node.node_type == "gate":
        gate_mode = getattr(node, "gate_mode", "if_else")
        return "done" if gate_mode == "while" else "true"
    # For composite or tool with "results" (array), prefer it for ForEach wiring
    if node.node_type in ("composite", "tool_operator") and any(
        p.name == "results" for p in node.output_ports
    ):
        return "results"
    mapped = DEFAULT_OUTPUT_PORTS.get(node.node_type)
    if mapped and any(port.name == mapped for port in node.output_ports):
        return mapped
    if node.output_ports:
        return node.output_ports[0].name
    return mapped or "result"


def _default_input_port(node: NodeBase) -> str:
    if node.input_ports:
        return node.input_ports[0].name
    return DEFAULT_INPUT_PORT


def _infer_input_variable_type(ports: list[InputPort]) -> str:
    """Infer InputVariable.type (constrained to string|number|boolean)."""
    types = {port.json_schema.get("type") for port in ports if isinstance(port.json_schema, dict)}
    types.discard(None)
    if "boolean" in types:
        return "boolean"
    if "number" in types or "integer" in types:
        return "number"
    return "string"


def _infer_input_port_schema(ports: list[InputPort]) -> dict[str, Any]:
    """Infer the JSON schema for an auto-generated InputNode output port,
    using the actual target port schemas (not constrained to InputVariable types)."""
    schemas = [port.json_schema for port in ports if isinstance(port.json_schema, dict) and port.json_schema]
    if len(schemas) == 1:
        return dict(schemas[0])
    types = {s.get("type") for s in schemas}
    types.discard(None)
    if len(types) == 1:
        return {"type": next(iter(types))}
    return {"type": "string"}


def _make_data_edge(
    source_node_id: str,
    source_port: str,
    target_node_id: str,
    target_port: str,
    edge_counter: list[int],
    source: SourceLocation | None = None,
) -> DataEdge:
    index = edge_counter[0]
    edge_counter[0] += 1
    return DataEdge(
        id=f"edge_{source_node_id}_{target_node_id}_{index}",
        source_node_id=source_node_id,
        source_port=source_port,
        target_node_id=target_node_id,
        target_port=target_port,
        metadata=_source_metadata(source),
    )


def _find_entry_points(nodes: list[NodeBase], edges: list[DataEdge]) -> list[str]:
    has_incoming = {edge.target_node_id for edge in edges}
    return [node.id for node in nodes if node.id not in has_incoming]


def _find_exit_points(nodes: list[NodeBase], edges: list[DataEdge]) -> list[str]:
    has_outgoing = {edge.source_node_id for edge in edges}
    return [node.id for node in nodes if node.id not in has_outgoing]


def _ensure_unique_id(candidate: str, existing: set[str]) -> str:
    if candidate not in existing:
        return candidate
    suffix = 2
    while f"{candidate}_{suffix}" in existing:
        suffix += 1
    return f"{candidate}_{suffix}"


def _source_metadata(
    source: SourceLocation | None,
    fallback_file: Path | None = None,
) -> dict[str, Any]:
    file_path = source.file if source is not None else fallback_file
    line = source.line if source is not None else 0
    if file_path is None:
        return {}
    return {"source": {"file": str(file_path), "line": line}}


def _is_validation_warning(message: str) -> bool:
    lowered = message.lower()
    return any(pattern in lowered for pattern in _VALIDATION_WARNING_PATTERNS)


def _emit_error(
    diagnostics: list[Diagnostic],
    message: str,
    *,
    source: SourceLocation | None = None,
    source_file: Path | None = None,
    hint: str | None = None,
) -> None:
    diagnostics.append(
        Diagnostic(
            level="error",
            message=message,
            source_file=source_file or (source.file if source else None),
            source_line=source.line if source else 0,
            source_column=source.column if source else 0,
            hint=hint,
        )
    )


def _emit_warning(
    diagnostics: list[Diagnostic],
    message: str,
    *,
    source: SourceLocation | None = None,
    source_file: Path | None = None,
    hint: str | None = None,
) -> None:
    diagnostics.append(
        Diagnostic(
            level="warning",
            message=message,
            source_file=source_file or (source.file if source else None),
            source_line=source.line if source else 0,
            source_column=source.column if source else 0,
            hint=hint,
        )
    )
