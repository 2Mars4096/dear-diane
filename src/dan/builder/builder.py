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
        self._hyperedges: list[dict[str, Any]] = []

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
        config: dict[str, Any] | None = None,
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Add a tool operator node.

        *config* is an alias for *tool_config* for convenience.
        """
        from dan.models.ports import InputPort, OutputPort

        effective_config = tool_config or config or {}
        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "tool_id": tool_id,
            "tool_config": effective_config,
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
        read_set: list[ContextDeclaration] | None = None,
        write_set: list[ContextDeclaration] | None = None,
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
        if read_set is not None:
            kwargs["read_set"] = read_set
        if write_set is not None:
            kwargs["write_set"] = write_set
        pn = _PendingNode(
            id=node_id,
            node_type="code_operator",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        return NodeRef(node_id, "code_operator", self)

    def rag(
        self,
        node_id: str,
        *,
        collection: str,
        top_k: int = 5,
        similarity_threshold: float | None = None,
        embedding_model: str = "",
        vector_store_config: dict[str, Any] | None = None,
        query_template: str = "{query}",
        include_metadata: bool = True,
        rerank: bool = False,
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Add a RAG operator node for vector-store retrieval."""
        from dan.models.ports import InputPort, OutputPort

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "collection": collection,
            "top_k": top_k,
            "query_template": query_template,
            "include_metadata": include_metadata,
            "rerank": rerank,
        }
        if similarity_threshold is not None:
            kwargs["similarity_threshold"] = similarity_threshold
        if embedding_model:
            kwargs["embedding_model"] = embedding_model
        if vector_store_config:
            kwargs["vector_store_config"] = vector_store_config

        pn = _PendingNode(
            id=node_id,
            node_type="rag_operator",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        return NodeRef(node_id, "rag_operator", self)

    def validator(
        self,
        node_id: str,
        *,
        rules: list[dict[str, Any]] | None = None,
        on_failure: str = "route",
        strict_mode: bool = False,
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Add a validator node for data validation at agent boundaries."""
        from dan.models.ports import InputPort, OutputPort

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "validation_rules": rules or [],
            "on_failure": on_failure,
            "strict_mode": strict_mode,
        }

        pn = _PendingNode(
            id=node_id,
            node_type="validator",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        return NodeRef(node_id, "validator", self)

    def reflection(
        self,
        node_id: str,
        *,
        reflection_prompt: str = "",
        reflection_model: str | None = None,
        source: str = "last_run",
        source_config: dict[str, Any] | None = None,
        output_format: str = "principles",
        max_principles: int = 10,
        min_confidence: float = 0.3,
        dedup_strategy: str = "embedding_similarity",
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Add a reflection node for post-run analysis."""
        from dan.models.ports import InputPort, OutputPort

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "reflection_prompt": reflection_prompt,
            "source": source,
            "source_config": source_config or {},
            "output_format": output_format,
            "max_principles": max_principles,
            "min_confidence": min_confidence,
            "dedup_strategy": dedup_strategy,
        }
        if reflection_model is not None:
            kwargs["reflection_model"] = reflection_model

        pn = _PendingNode(
            id=node_id,
            node_type="reflection",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        return NodeRef(node_id, "reflection", self)

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

    def gate(
        self,
        node_id: str,
        *,
        condition: str,
        gate_mode: str = "if_else",
        max_iterations: int = 10,
        state_schema: dict[str, Any] | None = None,
        state_defaults: dict[str, Any] | None = None,
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Add a GateNode to the workflow."""
        from dan.models.ports import InputPort, OutputPort

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "condition": condition,
            "gate_mode": gate_mode,
            "max_iterations": max_iterations,
        }
        if state_schema is not None:
            kwargs["state_schema"] = state_schema
        if state_defaults is not None:
            kwargs["state_defaults"] = state_defaults
        pn = _PendingNode(
            id=node_id,
            node_type="gate",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        return NodeRef(node_id, "gate", self, gate_mode=gate_mode)

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

    def input_node(
        self,
        node_id: str,
        *,
        variables: list[dict[str, Any]] | None = None,
        name: str | None = None,
        description: str = "",
    ) -> NodeRef:
        """Add an explicit workflow input node."""
        from dan.models.control_flow import InputVariable
        from dan.models.ports import OutputPort

        variable_models = [InputVariable(**v) for v in (variables or [])]
        output_ports: list[OutputPort] = []
        names = {var.name for var in variable_models}
        if "input" not in names:
            output_ports.append(OutputPort(name="input"))
        for var in variable_models:
            output_ports.append(OutputPort(name=var.name))

        pn = _PendingNode(
            id=node_id,
            node_type="input",
            kwargs={
                "name": name or node_id,
                "description": description,
                "variables": variable_models,
            },
            explicit_output_ports=output_ports,
        )
        self._add_node(pn)
        return NodeRef(node_id, "input", self)

    def human(
        self,
        node_id: str,
        *,
        prompt: str = "",
        timeout_seconds: float | None = None,
        default_action: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        render_mode: str = "text",
        options: list[str] | None = None,
        instructions: str = "",
        render_target: str = "dialog",
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Add a canonical human interaction node."""
        from dan.models.ports import InputPort, OutputPort

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "prompt": prompt,
            "render_mode": render_mode,
            "instructions": instructions,
            "render_target": render_target,
        }
        if timeout_seconds is not None:
            kwargs["timeout_seconds"] = timeout_seconds
        if default_action is not None:
            kwargs["default_action"] = default_action
        if input_schema is not None:
            kwargs["input_schema"] = input_schema
        if output_schema is not None:
            kwargs["output_schema"] = output_schema
        if options is not None:
            kwargs["options"] = options

        pn = _PendingNode(
            id=node_id,
            node_type="human",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        return NodeRef(node_id, "human", self)

    def approval(
        self,
        node_id: str,
        *,
        prompt: str = "Review and approve:",
        timeout_seconds: float | None = None,
        default_action: str | None = None,
        instructions: str = "",
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Shorthand for a human approval node."""
        return self.human(
            node_id,
            prompt=prompt,
            timeout_seconds=timeout_seconds,
            default_action=default_action,
            instructions=instructions,
            render_mode="approval",
            name=name,
            description=description,
            input_ports=input_ports,
            output_ports=output_ports,
        )

    def form(
        self,
        node_id: str,
        *,
        schema: dict[str, Any],
        prompt: str = "",
        timeout_seconds: float | None = None,
        default_action: str | None = None,
        instructions: str = "",
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Shorthand for a human form node."""
        return self.human(
            node_id,
            prompt=prompt,
            timeout_seconds=timeout_seconds,
            default_action=default_action,
            output_schema=schema,
            instructions=instructions,
            render_mode="form",
            name=name,
            description=description,
            input_ports=input_ports,
            output_ports=output_ports,
        )

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

    def vote(
        self,
        node_id: str,
        *,
        prompt: str,
        candidates: list[str],
        num_votes: int = 3,
        strategy: str = "majority",
        system_prompt: str = "",
        temperature: float = 0.7,
        output_schema: dict[str, Any] | None = None,
        vote_config: dict[str, Any] | None = None,
        parallelism: int = 3,
        timeout_seconds: float | None = None,
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Add a vote/ensemble node."""
        from dan.models.ports import InputPort, OutputPort

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "candidates": candidates,
            "num_votes": num_votes,
            "prompt_template": prompt,
            "system_prompt": system_prompt,
            "temperature": temperature,
            "vote_strategy": strategy,
            "parallelism": parallelism,
        }
        if output_schema is not None:
            kwargs["output_json_schema"] = output_schema
        if vote_config is not None:
            kwargs["vote_config"] = vote_config
        if timeout_seconds is not None:
            kwargs["timeout_seconds"] = timeout_seconds

        pn = _PendingNode(
            id=node_id,
            node_type="vote",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (output_ports or [])],
        )
        self._add_node(pn)
        return NodeRef(node_id, "vote", self)

    def ensemble(
        self,
        node_id: str,
        *,
        prompt: str,
        models: list[str],
        strategy: str = "judge",
        system_prompt: str = "",
        temperature: float = 0.7,
        output_schema: dict[str, Any] | None = None,
        vote_config: dict[str, Any] | None = None,
        parallelism: int | None = None,
        timeout_seconds: float | None = None,
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Alias for `vote()` using one vote per listed model."""
        resolved_parallelism = parallelism if parallelism is not None else max(1, len(models))
        return self.vote(
            node_id,
            prompt=prompt,
            candidates=models,
            num_votes=max(1, len(models)),
            strategy=strategy,
            system_prompt=system_prompt,
            temperature=temperature,
            output_schema=output_schema,
            vote_config=vote_config,
            parallelism=resolved_parallelism,
            timeout_seconds=timeout_seconds,
            name=name,
            description=description,
            input_ports=input_ports,
            output_ports=output_ports,
        )

    # ── Hyperedge creation methods ───────────────────────────────

    def skill(
        self,
        name: str,
        content: str,
        *,
        attach_to: list[str] | None = None,
        attach_to_type: list[str] | None = None,
        attach_to_tags: list[str] | None = None,
        attach_to_subgraph: list[str] | None = None,
        attach_globally: bool = False,
        propagate: bool = True,
    ) -> WorkflowBuilder:
        """Add a skill hyperedge. Returns self for chaining."""
        self._hyperedges.append({
            "name": name,
            "hyperedge_type": "skill",
            "hook": "pre_prompt",
            "content": content,
            "config": {},
            "attach_to": attach_to or [],
            "attach_to_type": attach_to_type or [],
            "attach_to_tags": attach_to_tags or [],
            "attach_to_subgraph": attach_to_subgraph or [],
            "attach_globally": attach_globally,
            "propagate": propagate,
        })
        return self

    def rule(
        self,
        name: str,
        rule_type: str,
        hook: str,
        content: str,
        *,
        severity: str = "warning",
        block_on_fail: bool = False,
        attach_to: list[str] | None = None,
        attach_to_type: list[str] | None = None,
        attach_to_tags: list[str] | None = None,
        attach_to_subgraph: list[str] | None = None,
        attach_globally: bool = False,
        propagate: bool = True,
    ) -> WorkflowBuilder:
        """Add a rule (guardrail/style/override) hyperedge. Returns self for chaining."""
        config: dict[str, Any] = {}
        if rule_type == "guardrail":
            config = {"severity": severity, "block_on_fail": block_on_fail}
        self._hyperedges.append({
            "name": name,
            "hyperedge_type": rule_type,
            "hook": hook,
            "content": content,
            "config": config,
            "attach_to": attach_to or [],
            "attach_to_type": attach_to_type or [],
            "attach_to_tags": attach_to_tags or [],
            "attach_to_subgraph": attach_to_subgraph or [],
            "attach_globally": attach_globally,
            "propagate": propagate,
        })
        return self

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

    # ── Convenience methods ──────────────────────────────────────

    def chain(
        self,
        *steps: tuple[str, str] | tuple[str, str, str],
        name_prefix: str = "",
    ) -> NodeRef:
        """Create a linear chain of LLM nodes with auto-wiring.

        Each step is ``(node_id, prompt)`` or ``(node_id, prompt, model)``.
        Returns the last node's NodeRef.
        """
        if not steps:
            raise BuildError(["chain() requires at least one step"])

        refs: list[NodeRef] = []
        for i, step in enumerate(steps):
            if len(step) == 2:
                node_id, prompt = step[0], step[1]
                model = ""
            elif len(step) == 3:
                node_id, prompt, model = step[0], step[1], step[2]
            else:
                raise BuildError([f"chain step {i} must be (id, prompt) or (id, prompt, model)"])

            if name_prefix:
                node_id = f"{name_prefix}_{node_id}"

            if refs:
                prompt = f"{prompt}\n\nInput: <<dan:{refs[-1].node_id}:{refs[-1].default_output}>>"

            ref = self.llm(node_id, prompt=prompt, model=model)
            if refs:
                refs[-1] >> ref
            refs.append(ref)

        return refs[-1]

    def review_loop(
        self,
        writer_prompt: str,
        reviewer_prompt: str,
        *,
        name: str = "review",
        max_rounds: int | None = None,
        writer_model: str = "",
        reviewer_model: str = "",
        condition: str | None = None,
        review_fields: dict[str, dict[str, Any]] | None = None,
        feedback_key: str | None = None,
    ) -> NodeRef:
        """Create a writer-reviewer loop in one call.

        Returns a NodeRef pointing to the while_loop node.
        ``max_rounds`` defaults to 2 for content-oriented prompts, 3 otherwise.

        Optional kwargs for custom review criteria:
        - ``condition``: loop condition (default: ``"quality_score < 8"``).
        - ``review_fields``: reviewer output schema fields (default:
          ``{"quality_score": {"type": "integer"}, "feedback": {"type": "string"}}``).
        - ``feedback_key``: which review field is injected back into the
          writer prompt (default: ``"feedback"``).
        """
        if max_rounds is None:
            from dan.builder._constants import CONTENT_KEYWORDS
            prompt_lower = writer_prompt.lower()
            max_rounds = 2 if any(kw in prompt_lower for kw in CONTENT_KEYWORDS) else 3

        eff_condition = condition or "quality_score < 8"
        eff_fields: dict[str, dict[str, Any]] = review_fields or {
            "quality_score": {"type": "integer"},
            "feedback": {"type": "string"},
        }
        eff_feedback_key = feedback_key or "feedback"

        writer_id = f"{name}_writer"
        reviewer_id = f"{name}_reviewer"
        loop_id = f"{name}_loop"

        loop_port_names = ["draft"] + list(eff_fields.keys())
        loop_input_ports = [{"name": p, "required": False} for p in loop_port_names]
        loop_output_ports = [{"name": p} for p in loop_port_names]

        _type_to_default: dict[str, Any] = {"integer": 0, "number": 0.0, "boolean": False}
        loop_state_schema = {"draft": {"type": "string"}}
        loop_state_schema.update(eff_fields)
        loop_state_defaults = {"draft": ""}
        for field_name, field_schema in eff_fields.items():
            loop_state_defaults[field_name] = _type_to_default.get(
                field_schema.get("type", "string"), ""
            )

        with self.while_loop(
            loop_id,
            condition=eff_condition,
            max_iterations=max_rounds,
            input_ports=loop_input_ports,
            output_ports=loop_output_ports,
            state_schema=loop_state_schema,
            state_defaults=loop_state_defaults,
        ) as body:
            writer = body.llm(
                writer_id,
                prompt=writer_prompt + f"\n\nPrevious {eff_feedback_key}: {{{eff_feedback_key}}}",
                model=writer_model,
                input_ports=[{"name": eff_feedback_key}],
            )
            reviewer = body.llm(
                reviewer_id,
                prompt=reviewer_prompt,
                model=reviewer_model,
                output_schema={
                    "type": "object",
                    "properties": dict(eff_fields),
                    "required": list(eff_fields.keys()),
                },
                input_ports=[{"name": "text"}],
            )
            body.edge(writer["text"], reviewer["text"])

        return NodeRef(loop_id, "while_loop", self,
                       _default_input="draft", _default_output="draft")

    def map_reduce(
        self,
        items_expr: str | PortRef,
        map_prompt: str,
        reduce_prompt: str,
        *,
        name: str = "map_reduce",
        map_model: str = "",
        reduce_model: str = "",
        parallelism: int = 3,
    ) -> NodeRef:
        """Fan-out over items with parallel processing and aggregation.

        Returns the reduce node's NodeRef.
        """
        fe_id = f"{name}_fan_out"
        map_id = f"{name}_map"
        reduce_id = f"{name}_reduce"

        items_ref = items_expr if isinstance(items_expr, PortRef) else None

        with self.for_each(
            fe_id,
            items=items_ref,
            parallelism=parallelism,
        ) as body:
            body.llm(map_id, prompt=map_prompt, model=map_model)

        fe_ref = NodeRef(fe_id, "for_each", self)
        reduce_ref = self.llm(
            reduce_id,
            prompt=reduce_prompt,
            model=reduce_model,
            input_ports=[{"name": "results"}],
        )
        self.edge(fe_ref["results"], reduce_ref["results"])

        return reduce_ref

    def tool_chain(
        self,
        *steps: tuple[str, str | None, str | dict],
    ) -> NodeRef:
        """Chain mixing LLM and tool nodes.

        Each step is ``(node_id, tool_id_or_None, prompt_or_config)``.
        When ``tool_id`` is None, creates an LLM node with the third arg as prompt.
        Otherwise creates a tool node with the third arg as config dict (or empty).
        Returns the last node's NodeRef.
        """
        if not steps:
            raise BuildError(["tool_chain() requires at least one step"])

        refs: list[NodeRef] = []
        for i, step in enumerate(steps):
            if len(step) != 3:
                raise BuildError([f"tool_chain step {i} must be (id, tool_id_or_None, prompt_or_config)"])
            node_id, tool_id, prompt_or_config = step

            if tool_id is None:
                prompt = prompt_or_config if isinstance(prompt_or_config, str) else ""
                ref = self.llm(node_id, prompt=prompt)
            else:
                config = prompt_or_config if isinstance(prompt_or_config, dict) else {}
                ref = self.tool(node_id, tool_id=tool_id, tool_config=config)

            if refs:
                refs[-1] >> ref
            refs.append(ref)

        return refs[-1]

    def branch(
        self,
        condition: str,
        then_prompt: str,
        else_prompt: str,
        *,
        name: str = "branch",
        then_model: str = "",
        else_model: str = "",
    ) -> tuple[NodeRef, NodeRef, NodeRef]:
        """Create a conditional branch: gate + then-LLM + else-LLM.

        Returns ``(gate_ref, then_ref, else_ref)``.  Wire upstream into
        ``gate_ref`` and downstream from ``then_ref`` / ``else_ref``.
        Compiles to a ``gate`` node with ``gate_mode="if_else"`` and
        two LLM nodes wired from its ``true``/``false`` output ports.
        """
        gate_id = f"{name}_gate"
        then_id = f"{name}_then"
        else_id = f"{name}_else"

        gate_ref = self.gate(
            gate_id,
            condition=condition,
            gate_mode="if_else",
        )
        then_ref = self.llm(then_id, prompt=then_prompt, model=then_model)
        else_ref = self.llm(else_id, prompt=else_prompt, model=else_model)

        self.edge(gate_ref["true"], then_ref["input"])
        self.edge(gate_ref["false"], else_ref["input"])

        return gate_ref, then_ref, else_ref

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
        state_schema: dict[str, Any] | None = None,
        state_defaults: dict[str, Any] | None = None,
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
        if state_schema is not None:
            kwargs["state_schema"] = state_schema
        if state_defaults is not None:
            kwargs["state_defaults"] = state_defaults

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
    def goal_loop(
        self,
        node_id: str,
        *,
        goal_text: str,
        metric_name: str = "score",
        target_value: float = 1.0,
        comparison: str = ">=",
        max_iterations: int = 10,
        evaluator: str = "llm_judge",
        success_criteria: str | None = None,
        name: str | None = None,
        description: str = "",
        compaction: CompactionRule | None = None,
        failure_policy: FailurePolicy | None = None,
        read_set: list[ContextDeclaration] | None = None,
        write_set: list[ContextDeclaration] | None = None,
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> Generator[WorkflowBuilder, None, None]:
        """Context manager for a goal-loop sub-graph."""
        from dan.models.ports import InputPort, OutputPort

        sub_key = f"{node_id}_body"
        sub = WorkflowBuilder(
            sub_key, _parent=self, _scope_type="goal_loop"
        )
        sub._entry_input_ref = PortRef("__entry__", "input", sub)

        yield sub

        sub_graph = sub._compile_as_subgraph()

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "goal_text": goal_text,
            "metric_name": metric_name,
            "target_value": target_value,
            "comparison": comparison,
            "body_graph": sub_key,
            "max_iterations": max_iterations,
            "evaluator": evaluator,
            "success_criteria": success_criteria,
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
            node_type="goal_loop",
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
    def parallel_subagents(
        self,
        node_id: str,
        *,
        merge_strategy: MergeStrategy = MergeStrategy.APPEND,
        parallelism: int = 1,
        failure_policy: FailurePolicy | None = None,
        reducer: str | None = None,
        input_mappings: dict[str, str] | None = None,
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> Generator["_ParallelSubagentsContext", None, None]:
        """Context manager for parallel subagent branches.

        Each branch is defined via ``parallel.branch(key)``. All branches run
        concurrently; results merge at fan-in per ``merge_strategy``.

        Example::

            with wf.parallel_subagents("teams", parallelism=2) as parallel:
                with parallel.branch("researcher") as sub:
                    sub.llm("r", prompt="Research: {input}")
                with parallel.branch("analyst") as sub:
                    sub.llm("a", prompt="Analyze: {input}")
        """
        from dan.models.ports import InputPort, OutputPort

        ctx = _ParallelSubagentsContext(
            self,
            node_id,
            merge_strategy=merge_strategy,
            parallelism=parallelism,
            failure_policy=failure_policy,
            reducer=reducer,
            input_mappings=input_mappings or {},
            name=name,
            description=description,
            input_ports=input_ports,
            output_ports=output_ports,
        )
        yield ctx
        ctx._finalize()

    @contextmanager
    def orchestrator(
        self,
        node_id: str,
        *,
        orchestrator_prompt: str = "",
        orchestrator_model: str | None = None,
        completion_condition: str = "all_done",
        max_iterations: int = 100,
        timeout_seconds: float | None = None,
        input_mappings: dict[str, str] | None = None,
        name: str | None = None,
        description: str = "",
        failure_policy: FailurePolicy | None = None,
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> Generator["_OrchestratorContext", None, None]:
        """Context manager for an async runtime orchestrator.

        Each team is defined via ``orch.team(name, key)``.  All teams run
        concurrently; the orchestrator monitors events asynchronously.

        Example::

            with wf.orchestrator("coord", completion_condition="all_done") as orch:
                with orch.team("researcher") as sub:
                    sub.llm("r", prompt="Research: {input}")
                with orch.team("analyst") as sub:
                    sub.llm("a", prompt="Analyze: {input}")
        """
        from dan.models.ports import InputPort, OutputPort

        ctx = _OrchestratorContext(
            self,
            node_id,
            orchestrator_prompt=orchestrator_prompt,
            orchestrator_model=orchestrator_model,
            completion_condition=completion_condition,
            max_iterations=max_iterations,
            timeout_seconds=timeout_seconds,
            input_mappings=input_mappings or {},
            failure_policy=failure_policy,
            name=name,
            description=description,
            input_ports=input_ports,
            output_ports=output_ports,
        )
        yield ctx
        ctx._finalize()

    @contextmanager
    def team(
        self,
        node_id: str,
        *,
        moderator_prompt: str = "",
        moderator_model: str | None = None,
        turn_strategy: str = "round_robin",
        max_turns: int = 20,
        completion_condition: str = "max_turns",
        timeout_seconds: float | None = None,
        shared_context_keys: list[str] | None = None,
        handoff_policy: str = "explicit",
        input_mappings: dict[str, str] | None = None,
        agent_inputs: dict[str, dict[str, Any]] | None = None,
        failure_policy: FailurePolicy | None = None,
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> Generator["_AgentTeamContext", None, None]:
        """Context manager for a group-chat style agent team."""
        from dan.models.ports import InputPort, OutputPort

        ctx = _AgentTeamContext(
            self,
            node_id,
            moderator_prompt=moderator_prompt,
            moderator_model=moderator_model,
            turn_strategy=turn_strategy,
            max_turns=max_turns,
            completion_condition=completion_condition,
            timeout_seconds=timeout_seconds,
            shared_context_keys=shared_context_keys or [],
            handoff_policy=handoff_policy,
            input_mappings=input_mappings or {},
            agent_inputs=agent_inputs or {},
            failure_policy=failure_policy,
            name=name,
            description=description,
            input_ports=input_ports,
            output_ports=output_ports,
        )
        yield ctx
        ctx._finalize()

    @contextmanager
    def group_chat(
        self,
        node_id: str,
        *,
        moderator_prompt: str = "",
        moderator_model: str | None = None,
        max_turns: int = 20,
        completion_condition: str = "max_turns",
        timeout_seconds: float | None = None,
        shared_context_keys: list[str] | None = None,
        handoff_policy: str = "explicit",
        input_mappings: dict[str, str] | None = None,
        agent_inputs: dict[str, dict[str, Any]] | None = None,
        failure_policy: FailurePolicy | None = None,
        name: str | None = None,
        description: str = "",
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> Generator["_AgentTeamContext", None, None]:
        """Alias for `team()` with `turn_strategy="free_form"`."""
        with self.team(
            node_id,
            moderator_prompt=moderator_prompt,
            moderator_model=moderator_model,
            turn_strategy="free_form",
            max_turns=max_turns,
            completion_condition=completion_condition,
            timeout_seconds=timeout_seconds,
            shared_context_keys=shared_context_keys,
            handoff_policy=handoff_policy,
            input_mappings=input_mappings,
            agent_inputs=agent_inputs,
            failure_policy=failure_policy,
            name=name,
            description=description,
            input_ports=input_ports,
            output_ports=output_ports,
        ) as ctx:
            yield ctx

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

    @contextmanager
    def validated_composite(
        self,
        node_id: str,
        *,
        entry_schema: dict[str, Any] | None = None,
        exit_schema: dict[str, Any] | None = None,
        entry_rules: list[dict[str, Any]] | None = None,
        exit_rules: list[dict[str, Any]] | None = None,
        on_failure: str = "route",
        input_mappings: dict[str, str] | None = None,
        output_mappings: dict[str, str] | None = None,
        name: str | None = None,
        description: str = "",
        read_set: list[ContextDeclaration] | None = None,
        write_set: list[ContextDeclaration] | None = None,
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> Generator["_ValidatedCompositeRef", None, None]:
        """Composite sub-graph with auto-inserted boundary validators.

        Wraps ``composite()`` and returns a ``_ValidatedCompositeRef``
        whose ``>>`` target is the entry validator and ``>>`` source is
        the exit validator.  External edges therefore always flow
        *through* the validators — the composite itself is internal.

        Parameters
        ----------
        entry_schema / exit_schema:
            JSON Schema dicts.  When provided, a validator is generated
            with ``required_keys`` + ``schema_conformance`` rules.
        entry_rules / exit_rules:
            Explicit ``ValidationRule`` dicts.  Takes precedence over
            auto-derived rules when provided.
        on_failure:
            ``"route"`` (default) | ``"warn"`` | ``"halt"``.
        """
        from dan.models.ports import InputPort, OutputPort

        composite_kwargs: dict[str, Any] = {}
        if input_ports is not None:
            composite_kwargs["input_ports"] = input_ports
        if output_ports is not None:
            composite_kwargs["output_ports"] = output_ports

        ref = _ValidatedCompositeRef(node_id, self)

        with self.composite(
            node_id,
            input_mappings=input_mappings,
            output_mappings=output_mappings,
            name=name,
            description=description,
            read_set=read_set,
            write_set=write_set,
            **composite_kwargs,
        ) as sub:
            yield ref._set_sub(sub)

        has_entry = bool(entry_schema or entry_rules)
        has_exit = bool(exit_schema or exit_rules)

        pn = next(n for n in self._nodes if n.id == node_id)
        composite_in = self._first_composite_input_port(input_ports, input_mappings)
        composite_out = self._first_composite_output_port(output_ports, output_mappings)

        if has_entry:
            e_rules = entry_rules or self._auto_rules(entry_schema)
            entry_id = f"{node_id}__entry_validator"
            self.validator(
                entry_id,
                rules=e_rules,
                on_failure=on_failure,
                name=f"Entry validator for {node_id}",
                input_ports=[{"name": "data", "schema": {}}],
                output_ports=[
                    {"name": "valid", "schema": {}},
                    {"name": "invalid", "schema": {}},
                ],
            )
            self._edges.append(_PendingEdge(
                source_node_id=entry_id,
                source_port="valid",
                target_node_id=node_id,
                target_port=composite_in,
                edge_type="data",
            ))
            ref._entry_node_id = entry_id

        if has_exit:
            x_rules = exit_rules or self._auto_rules(exit_schema)
            exit_id = f"{node_id}__exit_validator"
            self.validator(
                exit_id,
                rules=x_rules,
                on_failure=on_failure,
                name=f"Exit validator for {node_id}",
                input_ports=[{"name": "data", "schema": {}}],
                output_ports=[
                    {"name": "valid", "schema": {}},
                    {"name": "invalid", "schema": {}},
                ],
            )
            self._edges.append(_PendingEdge(
                source_node_id=node_id,
                source_port=composite_out,
                target_node_id=exit_id,
                target_port="data",
                edge_type="data",
            ))
            ref._exit_node_id = exit_id

        pn.kwargs.setdefault("external_input_schema", entry_schema)
        pn.kwargs.setdefault("external_output_schema", exit_schema)

    @staticmethod
    def _auto_rules(schema: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Derive validation rules from a JSON Schema dict."""
        if not schema or not isinstance(schema, dict):
            return []
        rules: list[dict[str, Any]] = []
        required_keys = schema.get("required", [])
        if required_keys:
            rules.append({"rule_type": "required_keys", "config": {"keys": required_keys}})
        rules.append({"rule_type": "schema_conformance", "config": {"schema": schema}})
        return rules

    @staticmethod
    def _first_composite_input_port(
        input_ports: list[dict[str, Any]] | None,
        input_mappings: dict[str, str] | None,
    ) -> str:
        """Derive the composite's first input port name for validator wiring."""
        if input_ports and len(input_ports) > 0:
            name = input_ports[0].get("name")
            if name:
                return name
        if input_mappings and len(input_mappings) > 0:
            return next(iter(input_mappings))
        return "input"

    @staticmethod
    def _first_composite_output_port(
        output_ports: list[dict[str, Any]] | None,
        output_mappings: dict[str, str] | None,
    ) -> str:
        """Derive the composite's first output port name for validator wiring."""
        if output_ports and len(output_ports) > 0:
            name = output_ports[0].get("name")
            if name:
                return name
        if output_mappings and len(output_mappings) > 0:
            return next(iter(output_mappings.values()))
        return "result"

    # ── Explicit edge wiring ───────────────────────────────────────

    def edge(self, source: PortRef, target: PortRef, *, spread: bool = False) -> None:
        """Explicitly wire a source port to a target port."""
        self._edges.append(_PendingEdge(
            source_node_id=source.node_id,
            source_port=source.port_name,
            target_node_id=target.node_id,
            target_port=target.port_name,
            edge_type="data",
            spread=spread,
        ))

    def spread_edge(self, source: PortRef, target: PortRef) -> None:
        """Shorthand for ``self.edge(source, target, spread=True)``."""
        return self.edge(source, target, spread=True)

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
            hyperedge_specs=list(self._hyperedges),
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
        src_port = src.default_output
        dst_port = dst.default_input
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
            hyperedge_specs=list(self._hyperedges),
            validate=True,
        )


class _ParallelSubagentsContext:
    """Context object for defining parallel subagent branches."""

    def __init__(
        self,
        builder: WorkflowBuilder,
        node_id: str,
        *,
        merge_strategy: MergeStrategy,
        parallelism: int,
        failure_policy: FailurePolicy | None,
        reducer: str | None,
        input_mappings: dict[str, str],
        name: str | None,
        description: str,
        input_ports: list[dict[str, Any]] | None,
        output_ports: list[dict[str, Any]] | None,
    ) -> None:
        self._builder = builder
        self._node_id = node_id
        self._merge_strategy = merge_strategy
        self._parallelism = parallelism
        self._failure_policy = failure_policy
        self._reducer = reducer
        self._input_mappings = input_mappings
        self._name = name
        self._description = description
        self._input_ports = input_ports
        self._output_ports = output_ports
        self._branch_keys: list[str] = []
        self._sub_graphs: list[tuple[str, Graph]] = []

    @contextmanager
    def branch(self, key: str) -> Generator[WorkflowBuilder, None, None]:
        """Define a branch sub-graph. Yields a WorkflowBuilder for the branch."""
        sub_key = f"{self._node_id}_{key}"
        sub = WorkflowBuilder(sub_key, _parent=self._builder, _scope_type="parallel_branch")
        sub._entry_input_ref = PortRef("__entry__", "input", sub)
        yield sub
        sub_graph = sub._compile_as_subgraph()
        self._branch_keys.append(key)
        self._sub_graphs.append((sub_key, sub_graph))

    def define_branch(self, key: str, graph: Graph) -> None:
        """Add a pre-built Graph as a branch (alternative to branch() context manager)."""
        sub_key = f"{self._node_id}_{key}"
        self._branch_keys.append(key)
        self._sub_graphs.append((sub_key, graph))

    def _finalize(self) -> None:
        """Create the parallel_subagents node and register sub_graphs."""
        from dan.models.ports import InputPort, OutputPort

        branch_graphs = [sub_key for sub_key, _ in self._sub_graphs]
        if not branch_graphs:
            raise BuildError([f"parallel_subagents({self._node_id!r}) has no branches"])

        kwargs: dict[str, Any] = {
            "name": self._name or self._node_id,
            "description": self._description,
            "branch_graphs": branch_graphs,
            "parallelism": self._parallelism,
            "merge_strategy": self._merge_strategy,
            "input_mappings": self._input_mappings,
        }
        if self._failure_policy is not None:
            kwargs["failure_policy"] = self._failure_policy
        if self._reducer is not None:
            kwargs["reducer"] = self._reducer

        pn = _PendingNode(
            id=self._node_id,
            node_type="parallel_subagents",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (self._input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (self._output_ports or [])],
        )
        self._builder._add_node(pn)
        for sub_key, sub_graph in self._sub_graphs:
            self._builder._sub_graphs.append(_PendingSubGraph(
                parent_node_id=self._node_id,
                sub_graph_key=sub_key,
                graph=sub_graph,
            ))


class _OrchestratorContext:
    """Context object for defining orchestrator teams."""

    def __init__(
        self,
        builder: WorkflowBuilder,
        node_id: str,
        *,
        orchestrator_prompt: str,
        orchestrator_model: str | None,
        completion_condition: str,
        max_iterations: int,
        timeout_seconds: float | None,
        input_mappings: dict[str, str],
        failure_policy: FailurePolicy | None,
        name: str | None,
        description: str,
        input_ports: list[dict[str, Any]] | None,
        output_ports: list[dict[str, Any]] | None,
    ) -> None:
        self._builder = builder
        self._node_id = node_id
        self._orchestrator_prompt = orchestrator_prompt
        self._orchestrator_model = orchestrator_model
        self._completion_condition = completion_condition
        self._max_iterations = max_iterations
        self._timeout_seconds = timeout_seconds
        self._input_mappings = input_mappings
        self._failure_policy = failure_policy
        self._name = name
        self._description = description
        self._input_ports = input_ports
        self._output_ports = output_ports
        self._teams: dict[str, str] = {}
        self._sub_graphs: list[tuple[str, Graph]] = []

    @contextmanager
    def team(self, team_name: str) -> Generator[WorkflowBuilder, None, None]:
        """Define a team sub-graph. Yields a WorkflowBuilder for the team."""
        sub_key = f"{self._node_id}_{team_name}"
        sub = WorkflowBuilder(sub_key, _parent=self._builder, _scope_type="orchestrator_team")
        sub._entry_input_ref = PortRef("__entry__", "input", sub)
        yield sub
        sub_graph = sub._compile_as_subgraph()
        self._teams[team_name] = sub_key
        self._sub_graphs.append((sub_key, sub_graph))

    def _finalize(self) -> None:
        """Create the orchestrator node and register sub_graphs."""
        from dan.models.ports import InputPort, OutputPort

        if not self._teams:
            raise BuildError([f"orchestrator({self._node_id!r}) has no teams"])

        kwargs: dict[str, Any] = {
            "name": self._name or self._node_id,
            "description": self._description,
            "teams": dict(self._teams),
            "orchestrator_prompt": self._orchestrator_prompt,
            "completion_condition": self._completion_condition,
            "max_iterations": self._max_iterations,
            "input_mappings": self._input_mappings,
        }
        if self._orchestrator_model is not None:
            kwargs["orchestrator_model"] = self._orchestrator_model
        if self._timeout_seconds is not None:
            kwargs["timeout_seconds"] = self._timeout_seconds
        if self._failure_policy is not None:
            kwargs["failure_policy"] = self._failure_policy

        pn = _PendingNode(
            id=self._node_id,
            node_type="orchestrator",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (self._input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (self._output_ports or [])],
        )
        self._builder._add_node(pn)
        for sub_key, sub_graph in self._sub_graphs:
            self._builder._sub_graphs.append(_PendingSubGraph(
                parent_node_id=self._node_id,
                sub_graph_key=sub_key,
                graph=sub_graph,
            ))


class _AgentTeamContext:
    """Context object for defining agent-team member sub-graphs."""

    def __init__(
        self,
        builder: WorkflowBuilder,
        node_id: str,
        *,
        moderator_prompt: str,
        moderator_model: str | None,
        turn_strategy: str,
        max_turns: int,
        completion_condition: str,
        timeout_seconds: float | None,
        shared_context_keys: list[str],
        handoff_policy: str,
        input_mappings: dict[str, str],
        agent_inputs: dict[str, dict[str, Any]],
        failure_policy: FailurePolicy | None,
        name: str | None,
        description: str,
        input_ports: list[dict[str, Any]] | None,
        output_ports: list[dict[str, Any]] | None,
    ) -> None:
        self._builder = builder
        self._node_id = node_id
        self._moderator_prompt = moderator_prompt
        self._moderator_model = moderator_model
        self._turn_strategy = turn_strategy
        self._max_turns = max_turns
        self._completion_condition = completion_condition
        self._timeout_seconds = timeout_seconds
        self._shared_context_keys = shared_context_keys
        self._handoff_policy = handoff_policy
        self._input_mappings = input_mappings
        self._agent_inputs = agent_inputs
        self._failure_policy = failure_policy
        self._name = name
        self._description = description
        self._input_ports = input_ports
        self._output_ports = output_ports
        self._agents: dict[str, str] = {}
        self._sub_graphs: list[tuple[str, Graph]] = []

    @contextmanager
    def agent(self, agent_name: str) -> Generator[WorkflowBuilder, None, None]:
        """Define an agent sub-graph. Yields a WorkflowBuilder for the agent."""
        sub_key = f"{self._node_id}_{agent_name}"
        sub = WorkflowBuilder(sub_key, _parent=self._builder, _scope_type="agent_team_member")
        sub._entry_input_ref = PortRef("__entry__", "input", sub)
        yield sub
        sub_graph = sub._compile_as_subgraph()
        self._agents[agent_name] = sub_key
        self._sub_graphs.append((sub_key, sub_graph))

    def _finalize(self) -> None:
        """Create the agent_team node and register subgraphs."""
        from dan.models.ports import InputPort, OutputPort

        if len(self._agents) < 2:
            raise BuildError([f"team({self._node_id!r}) requires at least 2 agents"])

        kwargs: dict[str, Any] = {
            "name": self._name or self._node_id,
            "description": self._description,
            "agents": dict(self._agents),
            "moderator_prompt": self._moderator_prompt,
            "turn_strategy": self._turn_strategy,
            "max_turns": self._max_turns,
            "completion_condition": self._completion_condition,
            "shared_context_keys": list(self._shared_context_keys),
            "handoff_policy": self._handoff_policy,
            "input_mappings": dict(self._input_mappings),
            "agent_inputs": dict(self._agent_inputs),
        }
        if self._moderator_model is not None:
            kwargs["moderator_model"] = self._moderator_model
        if self._timeout_seconds is not None:
            kwargs["timeout_seconds"] = self._timeout_seconds
        if self._failure_policy is not None:
            kwargs["failure_policy"] = self._failure_policy

        pn = _PendingNode(
            id=self._node_id,
            node_type="agent_team",
            kwargs=kwargs,
            explicit_input_ports=[InputPort(**p) for p in (self._input_ports or [])],
            explicit_output_ports=[OutputPort(**p) for p in (self._output_ports or [])],
        )
        self._builder._add_node(pn)
        for sub_key, sub_graph in self._sub_graphs:
            self._builder._sub_graphs.append(_PendingSubGraph(
                parent_node_id=self._node_id,
                sub_graph_key=sub_key,
                graph=sub_graph,
            ))


class _ValidatedCompositeRef:
    """Proxy returned by ``validated_composite`` that intercepts ``>>`` chains.

    - When used as a ``>>`` *target* (right-hand side), incoming data is
      routed to the **entry validator** (if present), else the composite.
    - When used as a ``>>`` *source* (left-hand side), outgoing data
      originates from the **exit validator** (if present), else the composite.

    This ensures user-level ``a >> block >> b`` automatically flows
    through the boundary validators without manual wiring.
    """

    def __init__(self, composite_id: str, builder: WorkflowBuilder) -> None:
        self._composite_id = composite_id
        self._builder = builder
        self._entry_node_id: str | None = None
        self._exit_node_id: str | None = None
        self._sub: WorkflowBuilder | None = None

    def _set_sub(self, sub: WorkflowBuilder) -> _ValidatedCompositeRef:
        self._sub = sub
        return self

    def __getattr__(self, name: str) -> Any:
        """Delegate builder methods (llm, code, etc.) to the sub-workflow."""
        if self._sub is not None and hasattr(self._sub, name):
            return getattr(self._sub, name)
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    @property
    def node_id(self) -> str:
        """Target node for incoming ``>>`` edges."""
        return self._entry_node_id or self._composite_id

    @property
    def source_node_id(self) -> str:
        """Source node for outgoing ``>>`` edges."""
        return self._exit_node_id or self._composite_id

    @property
    def node_type(self) -> str:
        if self._entry_node_id:
            return "validator"
        return "composite"

    @property
    def _source_node_type(self) -> str:
        if self._exit_node_id:
            return "validator"
        return "composite"

    def __getitem__(self, port_name: str) -> PortRef:
        return PortRef(self._composite_id, port_name, self._builder)

    def __rshift__(self, other: NodeRef | _ValidatedCompositeRef) -> NodeRef | _ValidatedCompositeRef:
        """Chain: use exit validator (or composite) as the source."""
        from dan.builder.compiler import default_output_port, default_input_port

        src_id = self.source_node_id
        src_type = self._source_node_type
        src_port = default_output_port(src_type)

        if isinstance(other, _ValidatedCompositeRef):
            dst_id = other.node_id
            dst_port = default_input_port(other.node_type)
        elif isinstance(other, NodeRef):
            dst_id = other.node_id
            dst_port = other.default_input
        else:
            return NotImplemented

        self._builder._edges.append(_PendingEdge(
            source_node_id=src_id,
            source_port=src_port,
            target_node_id=dst_id,
            target_port=dst_port,
            edge_type="data",
        ))
        return other

    def __rrshift__(self, other: NodeRef) -> _ValidatedCompositeRef:
        """Handle ``node_ref >> validated_block``."""
        from dan.builder.compiler import default_input_port

        if not isinstance(other, NodeRef):
            return NotImplemented

        src_port = other.default_output
        dst_id = self.node_id
        dst_type = self.node_type
        dst_port = default_input_port(dst_type)

        builder = self._builder or other._builder
        if builder is not None:
            builder._edges.append(_PendingEdge(
                source_node_id=other.node_id,
                source_port=src_port,
                target_node_id=dst_id,
                target_port=dst_port,
                edge_type="data",
            ))
        return self

    def __repr__(self) -> str:
        return f"_ValidatedCompositeRef({self._composite_id!r})"
