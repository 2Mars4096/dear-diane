"""WorkflowBuilder — fluent Python DSL for defining DAN workflows.

Usage::

    from dan.builder import workflow

    wf = workflow("my_workflow")
    a = wf.llm("gen", model="claude-opus-4", prompt="Generate: {topic}")
    b = wf.llm("refine", prompt=f"Refine: {a}")
    a >> b
    graph = wf.build()
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

from dan.builder.compiler import (
    BuildError,
    _PendingEdge,
    _PendingNode,
    _PendingSubGraph,
    compile_graph,
    default_output_port,
    DEFAULT_INPUT_PORT,
)
from dan.builder.refs import NodeRef, PortRef
from dan.models.context import (
    ArtifactRef,
    CompactionRule,
    ContextDeclaration,
    ContextMode,
    FailurePolicy,
    MergeStrategy,
    SharedContextDeclaration,
)
from dan.models.graph import Graph


def workflow(name: str, *, description: str = "", tags: list[str] | None = None) -> WorkflowBuilder:
    """Create a new workflow builder — the primary entry point for the DSL."""
    return WorkflowBuilder(name, description=description, tags=tags or [])


class WorkflowBuilder:
    """Fluent builder that accumulates nodes/edges and compiles to a Graph.

    Node-creation methods return ``NodeRef`` objects usable in f-strings
    and with the ``>>`` operator for auto-wiring.
    """

    def __init__(
        self,
        name: str,
        *,
        description: str = "",
        tags: list[str] | None = None,
        _parent: WorkflowBuilder | None = None,
        _scope_type: str | None = None,
    ) -> None:
        self._name = name
        self._description = description
        self._tags = tags or []
        self._parent = _parent
        self._scope_type = _scope_type

        self._nodes: list[_PendingNode] = []
        self._edges: list[_PendingEdge] = []
        self._sub_graphs: list[_PendingSubGraph] = []
        self._port_ref_connections: list[tuple[PortRef, str, str]] = []
        self._shared_context: list[SharedContextDeclaration] = []
        self._artifact_refs: list[ArtifactRef] = []

        self._node_map: dict[str, _PendingNode] = {}

        # Virtual entry-point refs for sub-graph builders
        self._entry_input_ref: PortRef | None = None
        self._entry_item_ref: PortRef | None = None

    # ── Virtual entry refs for sub-graph scopes ────────────────────

    @property
    def input(self) -> PortRef:
        """Virtual ref to the sub-graph entry point (for while_loop bodies)."""
        if self._entry_input_ref is None:
            raise BuildError(["`.input` is only available inside a sub-graph context (while_loop, composite)"])
        return self._entry_input_ref

    @property
    def item(self) -> PortRef:
        """Virtual ref to the current iteration item (for for_each bodies)."""
        if self._entry_item_ref is None:
            raise BuildError(["`.item` is only available inside a for_each context"])
        return self._entry_item_ref

    # ── Node creation methods ──────────────────────────────────────

    def llm(
        self,
        node_id: str,
        *,
        model: str = "",
        prompt: str = "",
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        output_schema: dict[str, Any] | None = None,
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Add an LLM operator node."""
        from dan.models.ports import InputPort, OutputPort

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "model": model,
            "prompt_template": prompt,
            "system_prompt": system_prompt,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if output_schema is not None:
            kwargs["output_json_schema"] = output_schema

        pn = _PendingNode(
            id=node_id,
            node_type="llm_operator",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        return NodeRef(node_id, "llm_operator", self)

    def tool(
        self,
        node_id: str,
        *,
        tool_id: str,
        tool_config: dict[str, Any] | None = None,
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Add a tool operator node."""
        from dan.models.ports import InputPort, OutputPort

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "tool_id": tool_id,
            "tool_config": tool_config or {},
        }
        pn = _PendingNode(
            id=node_id,
            node_type="tool_operator",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        return NodeRef(node_id, "tool_operator", self)

    def code(
        self,
        node_id: str,
        *,
        code: str,
        language: str = "python",
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Add a code operator node."""
        from dan.models.ports import InputPort, OutputPort

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "code": code,
            "language": language,
        }
        pn = _PendingNode(
            id=node_id,
            node_type="code_operator",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        return NodeRef(node_id, "code_operator", self)

    def if_else(
        self,
        node_id: str,
        *,
        condition: str,
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Add an if/else branching node."""
        from dan.models.ports import InputPort, OutputPort

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "condition": condition,
        }
        pn = _PendingNode(
            id=node_id,
            node_type="if_else",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        return NodeRef(node_id, "if_else", self)

    def reduce(
        self,
        node_id: str,
        *,
        reducer: str,
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Add a reduce (fan-in) node."""
        from dan.models.ports import InputPort, OutputPort

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "reducer": reducer,
        }
        pn = _PendingNode(
            id=node_id,
            node_type="reduce",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        return NodeRef(node_id, "reduce", self)

    def router(
        self,
        node_id: str,
        *,
        model: str,
        route_descriptions: dict[str, str],
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Add a router node (LLM-powered routing)."""
        from dan.models.ports import InputPort, OutputPort

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "model": model,
            "route_descriptions": route_descriptions,
        }
        pn = _PendingNode(
            id=node_id,
            node_type="router",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        return NodeRef(node_id, "router", self)

    def human_in_the_loop(
        self,
        node_id: str,
        *,
        prompt: str = "",
        timeout_seconds: float | None = None,
        default_action: str | None = None,
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Add a human-in-the-loop node."""
        from dan.models.ports import InputPort, OutputPort

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "prompt": prompt,
        }
        if timeout_seconds is not None:
            kwargs["timeout_seconds"] = timeout_seconds
        if default_action is not None:
            kwargs["default_action"] = default_action

        pn = _PendingNode(
            id=node_id,
            node_type="human_in_the_loop",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        return NodeRef(node_id, "human_in_the_loop", self)

    # ── Import pre-built graph as composite ───────────────────────

    def import_workflow(
        self,
        node_id: str,
        graph: Graph,
        *,
        input_mappings: dict[str, str] | None = None,
        output_mappings: dict[str, str] | None = None,
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Import a pre-built ``Graph`` as a composite node.

        The imported graph's internal node IDs are namespaced with a
        prefix derived from *node_id* to avoid collisions.  Input and
        output ports are auto-derived from the graph's entry/exit nodes
        when not provided explicitly.

        This is the Python equivalent of the editor's
        ``graphAsCompositeNode()`` and enables progressive workflow
        wrapping — build Workflow A, import it as a node in Workflow B,
        then import B as a node in Workflow C, etc.
        """
        import re as _re

        from dan.builder.importer import derive_ports, namespace_graph
        from dan.models.ports import InputPort, OutputPort

        safe_id = _re.sub(r"[^a-zA-Z0-9_]", "_", node_id)
        prefix = f"wf_{safe_id}__"
        body_key = f"{node_id}_body"

        namespaced = namespace_graph(graph, prefix)
        auto_in, auto_out, auto_in_map, auto_out_map = derive_ports(namespaced)

        eff_in = [InputPort(**p) for p in input_ports] if input_ports else auto_in
        eff_out = [OutputPort(**p) for p in output_ports] if output_ports else auto_out
        eff_in_map = input_mappings if input_mappings is not None else auto_in_map
        eff_out_map = output_mappings if output_mappings is not None else auto_out_map

        kwargs: dict[str, Any] = {
            "name": name or graph.metadata.name or node_id,
            "description": description or graph.metadata.description,
            "body_graph": body_key,
            "input_mappings": eff_in_map,
            "output_mappings": eff_out_map,
        }

        pn = _PendingNode(
            id=node_id,
            node_type="composite",
            kwargs=kwargs,
            explicit_input_ports=list(eff_in),
            explicit_output_ports=list(eff_out),
        )
        self._add_node(pn)
        self._sub_graphs.append(_PendingSubGraph(
            parent_node_id=node_id,
            sub_graph_key=body_key,
            graph=namespaced,
        ))
        return NodeRef(node_id, "composite", self)

    # ── Sub-graph context managers ─────────────────────────────────

    @contextmanager
    def while_loop(
        self,
        node_id: str,
        *,
        condition: str,
        max_iterations: int = 10,
        name: str | None = None,
        description: str = "",
        compaction: CompactionRule | None = None,
        failure_policy: FailurePolicy | None = None,
        read_set: list[ContextDeclaration] | None = None,
        write_set: list[ContextDeclaration] | None = None,
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> Generator[WorkflowBuilder, None, None]:
        """Context manager for a while-loop sub-graph."""
        from dan.models.ports import InputPort, OutputPort

        sub_key = f"{node_id}_body"
        sub = WorkflowBuilder(
            sub_key, _parent=self, _scope_type="while_loop"
        )
        sub._entry_input_ref = PortRef("__entry__", "input", sub)

        yield sub

        sub_graph = sub._compile_as_subgraph()

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "condition": condition,
            "body_graph": sub_key,
            "max_iterations": max_iterations,
        }
        if compaction is not None:
            kwargs["compaction_rule"] = compaction
        if failure_policy is not None:
            kwargs["failure_policy"] = failure_policy
        if read_set is not None:
            kwargs["read_set"] = read_set
        if write_set is not None:
            kwargs["write_set"] = write_set

        pn = _PendingNode(
            id=node_id,
            node_type="while_loop",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        self._sub_graphs.append(_PendingSubGraph(
            parent_node_id=node_id,
            sub_graph_key=sub_key,
            graph=sub_graph,
        ))

    @contextmanager
    def for_each(
        self,
        node_id: str,
        *,
        items: PortRef | None = None,
        parallelism: int = 1,
        merge_strategy: MergeStrategy = MergeStrategy.APPEND,
        name: str | None = None,
        description: str = "",
        read_set: list[ContextDeclaration] | None = None,
        write_set: list[ContextDeclaration] | None = None,
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> Generator[WorkflowBuilder, None, None]:
        """Context manager for a for-each sub-graph."""
        from dan.models.ports import InputPort, OutputPort

        sub_key = f"{node_id}_body"
        sub = WorkflowBuilder(
            sub_key, _parent=self, _scope_type="for_each"
        )
        sub._entry_input_ref = PortRef("__entry__", "input", sub)
        sub._entry_item_ref = PortRef("__entry__", "item", sub)

        yield sub

        sub_graph = sub._compile_as_subgraph()

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "body_graph": sub_key,
            "parallelism": parallelism,
            "merge_strategy": merge_strategy,
        }
        if read_set is not None:
            kwargs["read_set"] = read_set
        if write_set is not None:
            kwargs["write_set"] = write_set

        pn = _PendingNode(
            id=node_id,
            node_type="for_each",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        self._sub_graphs.append(_PendingSubGraph(
            parent_node_id=node_id,
            sub_graph_key=sub_key,
            graph=sub_graph,
        ))

        if items is not None:
            self._port_ref_connections.append((items, node_id, "items"))

    @contextmanager
    def composite(
        self,
        node_id: str,
        *,
        input_mappings: dict[str, str] | None = None,
        output_mappings: dict[str, str] | None = None,
        name: str | None = None,
        description: str = "",
        read_set: list[ContextDeclaration] | None = None,
        write_set: list[ContextDeclaration] | None = None,
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> Generator[WorkflowBuilder, None, None]:
        """Context manager for a composite sub-graph."""
        from dan.models.ports import InputPort, OutputPort

        sub_key = f"{node_id}_body"
        sub = WorkflowBuilder(
            sub_key, _parent=self, _scope_type="composite"
        )
        sub._entry_input_ref = PortRef("__entry__", "input", sub)

        yield sub

        sub_graph = sub._compile_as_subgraph()

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "body_graph": sub_key,
            "input_mappings": input_mappings or {},
            "output_mappings": output_mappings or {},
        }
        if read_set is not None:
            kwargs["read_set"] = read_set
        if write_set is not None:
            kwargs["write_set"] = write_set

        pn = _PendingNode(
            id=node_id,
            node_type="composite",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        self._sub_graphs.append(_PendingSubGraph(
            parent_node_id=node_id,
            sub_graph_key=sub_key,
            graph=sub_graph,
        ))

    # ── Explicit edge wiring ───────────────────────────────────────

    def edge(self, source: PortRef, target: PortRef) -> None:
        """Explicitly wire a source port to a target port."""
        self._edges.append(_PendingEdge(
            source_node_id=source.node_id,
            source_port=source.port_name,
            target_node_id=target.node_id,
            target_port=target.port_name,
            edge_type="data",
        ))

    def control_edge(
        self,
        source: PortRef,
        target: PortRef,
        *,
        condition: str | None = None,
    ) -> None:
        """Add a control edge for branch activation/routing semantics."""
        self._edges.append(_PendingEdge(
            source_node_id=source.node_id,
            source_port=source.port_name,
            target_node_id=target.node_id,
            target_port=target.port_name,
            edge_type="control",
            condition=condition,
        ))

    def context_edge(
        self,
        source: PortRef,
        target: PortRef,
        *,
        context_key: str,
        mode: ContextMode | str,
    ) -> None:
        """Add a context edge for shared-context read/write channels."""
        self._edges.append(_PendingEdge(
            source_node_id=source.node_id,
            source_port=source.port_name,
            target_node_id=target.node_id,
            target_port=target.port_name,
            edge_type="context",
            context_key=context_key,
            mode=mode,
        ))

    def context(
        self,
        key: str,
        *,
        json_schema: dict[str, Any] | None = None,
        description: str = "",
    ) -> None:
        """Declare a shared context key at the graph level."""
        self._shared_context.append(SharedContextDeclaration(
            key=key,
            json_schema=json_schema or {},
            description=description,
        ))

    def artifact_ref(
        self,
        uri: str,
        *,
        content_hash: str | None = None,
        media_type: str = "application/octet-stream",
        description: str = "",
    ) -> None:
        """Declare an artifact reference at graph level."""
        self._artifact_refs.append(ArtifactRef(
            uri=uri,
            content_hash=content_hash,
            media_type=media_type,
            description=description,
        ))

    # ── Build / serialize ──────────────────────────────────────────

    def build(self) -> Graph:
        """Compile this builder into a validated Graph model."""
        return compile_graph(
            name=self._name,
            description=self._description,
            tags=self._tags,
            nodes=list(self._nodes),
            edges=list(self._edges),
            sub_graphs=list(self._sub_graphs),
            shared_context=list(self._shared_context),
            port_ref_connections=list(self._port_ref_connections),
            artifact_refs=list(self._artifact_refs),
        )

    def to_graph(self) -> Graph:
        """Alias for build()."""
        return self.build()

    def to_dict(self) -> dict[str, Any]:
        """Compile and return as a plain dict (JSON-serializable)."""
        return self.build().model_dump()

    def to_json(self, *, indent: int = 2) -> str:
        """Compile and return as a JSON string."""
        return self.build().model_dump_json(indent=indent)

    # ── Internal ───────────────────────────────────────────────────

    def _add_node(self, pn: _PendingNode) -> None:
        if pn.id in self._node_map:
            raise BuildError([f"Duplicate node ID: {pn.id!r}"])
        self._nodes.append(pn)
        self._node_map[pn.id] = pn

    def _register_chain(self, src: NodeRef, dst: NodeRef) -> None:
        """Called by NodeRef.__rshift__ to register a >> edge."""
        src_port = default_output_port(src.node_type)
        dst_port = DEFAULT_INPUT_PORT
        self._edges.append(_PendingEdge(
            source_node_id=src.node_id,
            source_port=src_port,
            target_node_id=dst.node_id,
            target_port=dst_port,
            edge_type="data",
        ))

    def _compile_as_subgraph(self) -> Graph:
        """Compile this builder's state into a Graph (for sub-graph use)."""
        return compile_graph(
            name=self._name,
            description=self._description,
            tags=self._tags,
            nodes=list(self._nodes),
            edges=list(self._edges),
            sub_graphs=list(self._sub_graphs),
            shared_context=list(self._shared_context),
            port_ref_connections=list(self._port_ref_connections),
            artifact_refs=list(self._artifact_refs),
            validate=True,
        )
