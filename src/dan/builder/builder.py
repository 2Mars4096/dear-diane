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
from dan.builder._aliases import (
    legacy_compute_pending_from_worker,
    worker_pending_alias,
)
from dan.builder._scopes import (
    _AgentTeamContext,
    _OrchestratorContext,
    _ParallelSubagentsContext,
    _ValidatedCompositeRef,
    _WorkerScopeContext,
    auto_rules,
    first_composite_input_port,
    first_composite_output_port,
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
from dan.models.node_taxonomy import worker_builder_uses_workers
from dan.validation.linting import IntentRefiner


def workflow(
    name: str,
    *,
    description: str = "",
    tags: list[str] | None = None,
    canonical_workers: bool | None = None,
    lint_autogen: str | None = None,
    lint_intent_refiner: IntentRefiner | None = None,
) -> WorkflowBuilder:
    """Create a new workflow builder — the primary entry point for the DSL."""
    return WorkflowBuilder(
        name,
        description=description,
        tags=tags or [],
        _canonical_workers=canonical_workers,
        _lint_autogen=lint_autogen,
        _lint_intent_refiner=lint_intent_refiner,
    )


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
        _canonical_workers: bool | None = None,
        _lint_autogen: str | None = None,
        _lint_intent_refiner: IntentRefiner | None = None,
    ) -> None:
        self._name = name
        self._description = description
        self._tags = tags or []
        self._parent = _parent
        self._scope_type = _scope_type
        inherited_canonical_workers = _parent._canonical_workers if _parent is not None else None
        self._canonical_workers = (
            inherited_canonical_workers if _canonical_workers is None else _canonical_workers
        )
        if self._canonical_workers is None:
            self._canonical_workers = worker_builder_uses_workers()
        self._lint_autogen = _parent._lint_autogen if _parent is not None and _lint_autogen is None else _lint_autogen
        self._lint_intent_refiner = (
            _parent._lint_intent_refiner
            if _parent is not None and _lint_intent_refiner is None
            else _lint_intent_refiner
        )

        self._nodes: list[_PendingNode] = []
        self._edges: list[_PendingEdge] = []
        self._sub_graphs: list[_PendingSubGraph] = []
        self._port_ref_connections: list[tuple[PortRef, str, str]] = []
        self._shared_context: list[SharedContextDeclaration] = []
        self._artifact_refs: list[ArtifactRef] = []
        self._worker_resources: dict[str, dict[str, Any]] = {}

        self._node_map: dict[str, _PendingNode] = {}
        self._hyperedges: list[dict[str, Any]] = []

        # Virtual entry-point refs for sub-graph builders
        self._entry_input_ref: PortRef | None = None
        self._entry_item_ref: PortRef | None = None

    def _emit_canonical_worker_alias(self) -> bool:
        return bool(self._canonical_workers)

    def _legacy_compute_pending_from_worker(
        self,
        node_id: str,
        *,
        expected_node_type: str,
        worker_kwargs: dict[str, Any],
        explicit_input_ports: list[Any],
        explicit_output_ports: list[Any],
    ) -> _PendingNode:
        return legacy_compute_pending_from_worker(
            node_id,
            expected_node_type=expected_node_type,
            worker_kwargs=worker_kwargs,
            explicit_input_ports=explicit_input_ports,
            explicit_output_ports=explicit_output_ports,
        )

    def _worker_pending_alias(
        self,
        node_id: str,
        *,
        worker_kwargs: dict[str, Any],
        explicit_input_ports: list[Any],
        explicit_output_ports: list[Any],
    ) -> _PendingNode:
        return worker_pending_alias(
            node_id,
            worker_kwargs=worker_kwargs,
            explicit_input_ports=explicit_input_ports,
            explicit_output_ports=explicit_output_ports,
        )

    # ── Virtual entry refs for sub-graph scopes ────────────────────

    @property
    def input(self) -> PortRef:
        """Virtual ref to the sub-graph entry point (for while_loop bodies)."""
        if self._entry_input_ref is None:
            raise BuildError(["`.input` is only available inside a sub-graph context (while_loop, composite)"])
        return self._entry_input_ref

    @property
    def entry_input(self) -> PortRef:
        """Explicit alias for the sub-graph entry input ref."""
        return self.input

    @property
    def item(self) -> PortRef:
        """Virtual ref to the current iteration item (for for_each bodies)."""
        if self._entry_item_ref is None:
            raise BuildError(["`.item` is only available inside a for_each context"])
        return self._entry_item_ref

    @property
    def entry_item(self) -> PortRef:
        """Explicit alias for the current iteration item ref."""
        return self.item

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

        explicit_input_ports = [InputPort(**p) for p in input_ports] if input_ports is not None else []
        explicit_output_ports = [OutputPort(**p) for p in output_ports] if output_ports is not None else []
        if output_ports is None and not explicit_output_ports:
            explicit_output_ports = [OutputPort(name="text")]
        default_input = explicit_input_ports[0].name if len(explicit_input_ports) == 1 else None
        default_output = explicit_output_ports[0].name if len(explicit_output_ports) == 1 else "text"
        worker_kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "model": model or None,
            "llm_hints": {
                "prompt_template": prompt,
                "system_prompt": system_prompt,
                "temperature": temperature,
            },
        }
        if max_tokens is not None:
            worker_kwargs["llm_hints"]["max_tokens"] = max_tokens
        if output_schema is not None:
            worker_kwargs["llm_hints"]["output_json_schema"] = output_schema

        if self._emit_canonical_worker_alias():
            pn = self._worker_pending_alias(
                node_id,
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(
                node_id,
                "worker",
                self,
                _default_input=default_input,
                _default_output=default_output,
            )
        else:
            pn = self._legacy_compute_pending_from_worker(
                node_id,
                expected_node_type="llm_operator",
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(
                node_id,
                "llm_operator",
                self,
                _default_input=default_input,
                _default_output=default_output,
            )
        self._add_node(pn)
        return ref

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
        explicit_input_ports = [InputPort(**p) for p in (input_ports or [])]
        explicit_output_ports = [OutputPort(**p) for p in (output_ports or [])]
        if not explicit_output_ports:
            explicit_output_ports = [OutputPort(name="result")]
        default_input = explicit_input_ports[0].name if len(explicit_input_ports) == 1 else None
        default_output = explicit_output_ports[0].name if len(explicit_output_ports) == 1 else "result"
        worker_kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "tool_config": effective_config,
            "tool_ids": [tool_id],
        }
        if self._emit_canonical_worker_alias():
            pn = self._worker_pending_alias(
                node_id,
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(
                node_id,
                "worker",
                self,
                _default_input=default_input,
                _default_output=default_output,
            )
        else:
            pn = self._legacy_compute_pending_from_worker(
                node_id,
                expected_node_type="tool_operator",
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(
                node_id,
                "tool_operator",
                self,
                _default_input=default_input,
                _default_output=default_output,
            )
        self._add_node(pn)
        return ref

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

        explicit_input_ports = [InputPort(**p) for p in (input_ports or [])]
        explicit_output_ports = [OutputPort(**p) for p in (output_ports or [])]
        if not explicit_output_ports:
            explicit_output_ports = [OutputPort(name="result")]
        default_input = explicit_input_ports[0].name if len(explicit_input_ports) == 1 else None
        default_output = explicit_output_ports[0].name if len(explicit_output_ports) == 1 else "result"
        worker_kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "code": code,
            "language": language,
        }
        if read_set is not None:
            worker_kwargs["read_set"] = read_set
        if write_set is not None:
            worker_kwargs["write_set"] = write_set
        if self._emit_canonical_worker_alias():
            pn = self._worker_pending_alias(
                node_id,
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(
                node_id,
                "worker",
                self,
                _default_input=default_input,
                _default_output=default_output,
            )
        else:
            pn = self._legacy_compute_pending_from_worker(
                node_id,
                expected_node_type="code_operator",
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(
                node_id,
                "code_operator",
                self,
                _default_input=default_input,
                _default_output=default_output,
            )
        self._add_node(pn)
        return ref

    def worker(
        self,
        node_id: str,
        *,
        role: str = "",
        instruction: str = "",
        persona: str = "",
        authority: str = "leaf",
        model: str | None = None,
        tool_ids: list[str] | None = None,
        tool_config: dict[str, Any] | None = None,
        code: str = "",
        language: str = "python",
        llm: dict[str, Any] | None = None,
        llm_hints: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        authority_policy: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
        control_flow: dict[str, Any] | None = None,
        body_graph: str | None = None,
        sub_workers: dict[str, str] | None = None,
        input_mappings: dict[str, str] | None = None,
        output_mappings: dict[str, str] | None = None,
        parallelism: int = 1,
        merge_strategy: MergeStrategy = MergeStrategy.APPEND,
        spawn_policy: dict[str, Any] | None = None,
        external_input_schema: dict[str, Any] | None = None,
        external_output_schema: dict[str, Any] | None = None,
        control_state_schema: dict[str, Any] | None = None,
        local_state: dict[str, Any] | None = None,
        compaction_rule: dict[str, Any] | None = None,
        failure_policy: dict[str, Any] | None = None,
        projections: list[dict[str, Any]] | None = None,
        boundary_contract: dict[str, Any] | None = None,
        validation_rules: list[dict[str, Any]] | None = None,
        name: str | None = None,
        description: str = "",
        read_set: list[ContextDeclaration] | None = None,
        write_set: list[ContextDeclaration] | None = None,
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> NodeRef:
        """Add a lightweight Worker node."""
        from dan.models.ports import InputPort, OutputPort

        if llm is not None and llm_hints is not None:
            raise BuildError(["worker() accepts either `llm` or `llm_hints`, not both"])

        kwargs: dict[str, Any] = {
            "name": name or node_id,
            "description": description,
            "role": role,
            "instruction": instruction,
            "persona": persona,
            "authority": authority,
            "tool_ids": tool_ids or [],
            "code": code,
            "language": language,
            "sub_workers": sub_workers or {},
        }
        if input_mappings:
            kwargs["input_mappings"] = dict(input_mappings)
        if output_mappings:
            kwargs["output_mappings"] = dict(output_mappings)
        if parallelism != 1:
            kwargs["parallelism"] = parallelism
        if merge_strategy != MergeStrategy.APPEND:
            kwargs["merge_strategy"] = merge_strategy
        if spawn_policy is not None:
            kwargs["spawn_policy"] = spawn_policy
        if external_input_schema is not None:
            kwargs["external_input_schema"] = external_input_schema
        if external_output_schema is not None:
            kwargs["external_output_schema"] = external_output_schema
        if control_state_schema is not None:
            kwargs["control_state_schema"] = control_state_schema
        if local_state is not None:
            kwargs["local_state"] = local_state
        if compaction_rule is not None:
            kwargs["compaction_rule"] = compaction_rule
        if failure_policy is not None:
            kwargs["failure_policy"] = failure_policy
        if projections is not None:
            kwargs["projections"] = projections
        if tool_config:
            kwargs["metadata"] = {"tool_config": dict(tool_config)}
        if model is not None:
            kwargs["model"] = model
        effective_llm_hints = llm_hints if llm_hints is not None else llm
        if effective_llm_hints is not None:
            kwargs["llm_hints"] = effective_llm_hints
        if context is not None:
            kwargs["context"] = context
        if authority_policy is not None:
            kwargs["authority_policy"] = authority_policy
        if execution is not None:
            kwargs["execution"] = execution
        if control_flow is not None:
            kwargs["control_flow"] = control_flow
        if body_graph is not None:
            kwargs["body_graph"] = body_graph
        if boundary_contract is not None:
            kwargs["boundary_contract"] = boundary_contract
        if validation_rules is not None:
            kwargs["validation_rules"] = validation_rules
        if read_set is not None:
            kwargs["read_set"] = read_set
        if write_set is not None:
            kwargs["write_set"] = write_set

        explicit_input_ports = [InputPort(**p) for p in (input_ports or [])]
        explicit_output_ports = [OutputPort(**p) for p in (output_ports or [])]
        default_input = explicit_input_ports[0].name if len(explicit_input_ports) == 1 else None
        default_output = explicit_output_ports[0].name if len(explicit_output_ports) == 1 else None

        pn = _PendingNode(
            id=node_id,
            node_type="worker",
            kwargs=kwargs,
            explicit_input_ports=explicit_input_ports,
            explicit_output_ports=explicit_output_ports,
        )
        self._add_node(pn)
        return NodeRef(
            node_id,
            "worker",
            self,
            _default_input=default_input,
            _default_output=default_output,
        )

    @contextmanager
    def worker_scope(
        self,
        node_id: str,
        *,
        role: str = "",
        instruction: str = "",
        persona: str = "",
        authority: str = "leaf",
        model: str | None = None,
        tool_ids: list[str] | None = None,
        tool_config: dict[str, Any] | None = None,
        code: str = "",
        language: str = "python",
        llm: dict[str, Any] | None = None,
        llm_hints: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        authority_policy: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
        control_flow: dict[str, Any] | None = None,
        input_mappings: dict[str, str] | None = None,
        output_mappings: dict[str, str] | None = None,
        parallelism: int = 1,
        merge_strategy: MergeStrategy = MergeStrategy.APPEND,
        spawn_policy: dict[str, Any] | None = None,
        external_input_schema: dict[str, Any] | None = None,
        external_output_schema: dict[str, Any] | None = None,
        control_state_schema: dict[str, Any] | None = None,
        local_state: dict[str, Any] | None = None,
        compaction_rule: dict[str, Any] | None = None,
        failure_policy: dict[str, Any] | None = None,
        projections: list[dict[str, Any]] | None = None,
        boundary_contract: dict[str, Any] | None = None,
        validation_rules: list[dict[str, Any]] | None = None,
        name: str | None = None,
        description: str = "",
        read_set: list[ContextDeclaration] | None = None,
        write_set: list[ContextDeclaration] | None = None,
        input_ports: list[dict[str, Any]] | None = None,
        output_ports: list[dict[str, Any]] | None = None,
    ) -> Generator["_WorkerScopeContext", None, None]:
        """Context manager for a Worker with an authored body graph and/or named sub-workers."""
        ctx = _WorkerScopeContext(
            self,
            node_id,
            role=role,
            instruction=instruction,
            persona=persona,
            authority=authority,
            model=model,
            tool_ids=tool_ids or [],
            tool_config=tool_config,
            code=code,
            language=language,
            llm=llm,
            llm_hints=llm_hints,
            context=context,
            authority_policy=authority_policy,
            execution=execution,
            control_flow=control_flow,
            input_mappings=input_mappings,
            output_mappings=output_mappings,
            parallelism=parallelism,
            merge_strategy=merge_strategy,
            spawn_policy=spawn_policy,
            external_input_schema=external_input_schema,
            external_output_schema=external_output_schema,
            control_state_schema=control_state_schema,
            local_state=local_state,
            compaction_rule=compaction_rule,
            failure_policy=failure_policy,
            projections=projections,
            boundary_contract=boundary_contract,
            validation_rules=validation_rules,
            name=name,
            description=description,
            read_set=read_set,
            write_set=write_set,
            input_ports=input_ports,
            output_ports=output_ports,
        )
        yield ctx
        ctx._finalize()

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

        explicit_input_ports = [InputPort(**p) for p in (input_ports or [])]
        explicit_output_ports = [OutputPort(**p) for p in (output_ports or [])]
        if not explicit_input_ports:
            explicit_input_ports = [InputPort(name="query", required=False)]
        if not explicit_output_ports:
            explicit_output_ports = [OutputPort(name="chunks")]

        metadata: dict[str, Any] = {
            "rag_collection": collection,
            "rag_top_k": top_k,
            "rag_query_template": query_template,
            "rag_include_metadata": include_metadata,
            "rag_rerank": rerank,
        }
        if similarity_threshold is not None:
            metadata["rag_similarity_threshold"] = similarity_threshold
        if embedding_model:
            metadata["rag_embedding_model"] = embedding_model
        if vector_store_config:
            metadata["rag_vector_store_config"] = vector_store_config

        worker_kwargs = {
            "name": name or node_id,
            "description": description,
            "role": "rag",
            "metadata": metadata,
        }
        if self._emit_canonical_worker_alias():
            pn = self._worker_pending_alias(
                node_id,
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(node_id, "worker", self, _default_input="query", _default_output="chunks")
        else:
            pn = self._legacy_compute_pending_from_worker(
                node_id,
                expected_node_type="rag_operator",
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(node_id, "rag_operator", self)
        self._add_node(pn)
        return ref

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

        explicit_input_ports = [InputPort(**p) for p in (input_ports or [])]
        explicit_output_ports = [OutputPort(**p) for p in (output_ports or [])]
        if not explicit_input_ports:
            explicit_input_ports = [InputPort(name="data")]
        if not explicit_output_ports:
            explicit_output_ports = [
                OutputPort(name="valid"),
                OutputPort(name="invalid"),
            ]

        pn = self._legacy_compute_pending_from_worker(
            node_id,
            expected_node_type="validator",
            worker_kwargs={
                "name": name or node_id,
                "description": description,
                "role": "validator",
                "metadata": {
                    "validation_rules": list(rules or []),
                    "validator_on_failure": on_failure,
                    "validator_strict_mode": strict_mode,
                },
            },
            explicit_input_ports=explicit_input_ports,
            explicit_output_ports=explicit_output_ports,
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

        explicit_input_ports = [InputPort(**p) for p in (input_ports or [])]
        explicit_output_ports = [OutputPort(**p) for p in (output_ports or [])]
        if not explicit_output_ports:
            explicit_output_ports = [OutputPort(name="principles")]

        metadata: dict[str, Any] = {
            "reflection_prompt": reflection_prompt,
            "reflection_source": source,
            "reflection_source_config": source_config or {},
            "reflection_output_format": output_format,
            "reflection_max_principles": max_principles,
            "reflection_min_confidence": min_confidence,
            "reflection_dedup_strategy": dedup_strategy,
        }
        if reflection_model is not None:
            metadata["reflection_model"] = reflection_model

        worker_kwargs = {
            "name": name or node_id,
            "description": description,
            "role": "reflection",
            "model": reflection_model,
            "metadata": metadata,
        }
        if self._emit_canonical_worker_alias():
            pn = self._worker_pending_alias(
                node_id,
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(node_id, "worker", self, _default_output="principles")
        else:
            pn = self._legacy_compute_pending_from_worker(
                node_id,
                expected_node_type="reflection",
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(node_id, "reflection", self)
        self._add_node(pn)
        return ref

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

        explicit_input_ports = [InputPort(**p) for p in (input_ports or [])]
        explicit_output_ports = [OutputPort(**p) for p in (output_ports or [])]
        worker_kwargs = {
            "name": name or node_id,
            "description": description,
            "role": "reduce",
            "metadata": {"reduce_expression": reducer},
        }
        if self._emit_canonical_worker_alias():
            pn = self._worker_pending_alias(
                node_id,
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(node_id, "worker", self)
        else:
            pn = self._legacy_compute_pending_from_worker(
                node_id,
                expected_node_type="reduce",
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(node_id, "reduce", self)
        self._add_node(pn)
        return ref

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

        explicit_input_ports = [InputPort(**p) for p in (input_ports or [])]
        explicit_output_ports = [OutputPort(**p) for p in (output_ports or [])]
        if not explicit_output_ports:
            explicit_output_ports = [
                OutputPort(name="route"),
                OutputPort(name="result"),
            ]

        pn = self._legacy_compute_pending_from_worker(
            node_id,
            expected_node_type="router",
            worker_kwargs={
                "name": name or node_id,
                "description": description,
                "role": "router",
                "model": model,
                "metadata": {"route_descriptions": dict(route_descriptions)},
            },
            explicit_input_ports=explicit_input_ports,
            explicit_output_ports=explicit_output_ports,
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
        from dan.models.legacy import InputVariable
        from dan.models.ports import OutputPort

        type_to_schema = {
            "string": {"type": "string"},
            "number": {"type": "number"},
            "boolean": {"type": "boolean"},
        }
        variable_models = [InputVariable(**v) for v in (variables or [])]
        output_ports: list[OutputPort] = []
        names = {var.name for var in variable_models}
        if "input" not in names:
            aggregate_schema = {"type": "object"} if variable_models else {}
            output_ports.append(OutputPort(name="input", json_schema=aggregate_schema))
        for var in variable_models:
            variable_schema = {} if var.name == "input" else dict(
                type_to_schema.get(var.type, {"type": "string"})
            )
            output_ports.append(
                OutputPort(
                    name=var.name,
                    json_schema=variable_schema,
                )
            )

        worker_kwargs = {
            "name": name or node_id,
            "description": description,
            "metadata": {
                "input_variables": [
                    variable.model_dump(mode="json")
                    for variable in variable_models
                ],
            },
        }
        if self._emit_canonical_worker_alias():
            pn = self._worker_pending_alias(
                node_id,
                worker_kwargs=worker_kwargs,
                explicit_input_ports=[],
                explicit_output_ports=output_ports,
            )
            ref = NodeRef(node_id, "worker", self, _default_output="input")
        else:
            pn = self._legacy_compute_pending_from_worker(
                node_id,
                expected_node_type="input",
                worker_kwargs=worker_kwargs,
                explicit_input_ports=[],
                explicit_output_ports=output_ports,
            )
            ref = NodeRef(node_id, "input", self)
        self._add_node(pn)
        return ref

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

        explicit_input_ports = [InputPort(**p) for p in (input_ports or [])]
        explicit_output_ports = [OutputPort(**p) for p in (output_ports or [])]
        if not explicit_input_ports:
            explicit_input_ports = [InputPort(name="input", required=False)]
        if not explicit_output_ports:
            explicit_output_ports = [OutputPort(name="response")]

        metadata: dict[str, Any] = {
            "human_prompt": prompt,
            "human_render_mode": render_mode,
            "human_instructions": instructions,
            "human_render_target": render_target,
        }
        if timeout_seconds is not None:
            metadata["human_timeout_seconds"] = timeout_seconds
        if default_action is not None:
            metadata["human_default_action"] = default_action
        if input_schema is not None:
            metadata["human_input_schema"] = input_schema
        if output_schema is not None:
            metadata["human_output_schema"] = output_schema
        if options is not None:
            metadata["human_options"] = options

        worker_kwargs = {
            "name": name or node_id,
            "description": description,
            "role": "human",
            "metadata": metadata,
        }
        if self._emit_canonical_worker_alias():
            pn = self._worker_pending_alias(
                node_id,
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(node_id, "worker", self, _default_output="response")
        else:
            pn = self._legacy_compute_pending_from_worker(
                node_id,
                expected_node_type="human",
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(node_id, "human", self)
        self._add_node(pn)
        return ref

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

        explicit_input_ports = [InputPort(**p) for p in (input_ports or [])]
        explicit_output_ports = [OutputPort(**p) for p in (output_ports or [])]
        if not explicit_input_ports:
            explicit_input_ports = [InputPort(name="input", required=False)]
        if not explicit_output_ports:
            explicit_output_ports = [OutputPort(name="response")]

        metadata: dict[str, Any] = {
            "human_prompt": prompt,
        }
        if timeout_seconds is not None:
            metadata["human_timeout_seconds"] = timeout_seconds
        if default_action is not None:
            metadata["human_default_action"] = default_action

        worker_kwargs = {
            "name": name or node_id,
            "description": description,
            "role": "human_in_the_loop",
            "metadata": metadata,
        }
        if self._emit_canonical_worker_alias():
            pn = self._worker_pending_alias(
                node_id,
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(node_id, "worker", self, _default_output="response")
        else:
            pn = self._legacy_compute_pending_from_worker(
                node_id,
                expected_node_type="human_in_the_loop",
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(node_id, "human_in_the_loop", self)
        self._add_node(pn)
        return ref

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

        explicit_input_ports = [InputPort(**p) for p in (input_ports or [])]
        explicit_output_ports = [OutputPort(**p) for p in (output_ports or [])]
        if not explicit_input_ports:
            explicit_input_ports = [InputPort(name="input", required=False)]
        if not explicit_output_ports:
            explicit_output_ports = [OutputPort(name="winner")]

        metadata: dict[str, Any] = {
            "vote_candidates": list(candidates),
            "vote_num_votes": num_votes,
            "vote_prompt_template": prompt,
            "vote_system_prompt": system_prompt,
            "vote_temperature": temperature,
            "vote_strategy": strategy,
            "vote_parallelism": parallelism,
        }
        if output_schema is not None:
            metadata["vote_output_json_schema"] = output_schema
        if vote_config is not None:
            metadata["vote_config"] = vote_config
        if timeout_seconds is not None:
            metadata["vote_timeout_seconds"] = timeout_seconds

        worker_kwargs = {
            "name": name or node_id,
            "description": description,
            "role": "vote",
            "metadata": metadata,
        }
        if self._emit_canonical_worker_alias():
            pn = self._worker_pending_alias(
                node_id,
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(node_id, "worker", self, _default_output="winner")
        else:
            pn = self._legacy_compute_pending_from_worker(
                node_id,
                expected_node_type="vote",
                worker_kwargs=worker_kwargs,
                explicit_input_ports=explicit_input_ports,
                explicit_output_ports=explicit_output_ports,
            )
            ref = NodeRef(node_id, "vote", self)
        self._add_node(pn)
        return ref

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

        composite_in = first_composite_input_port(input_ports, input_mappings)
        composite_out = first_composite_output_port(output_ports, output_mappings)

        if has_entry:
            e_rules = entry_rules or auto_rules(entry_schema)
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
            x_rules = exit_rules or auto_rules(exit_schema)
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

        # The composite node itself already exists in the pending node list.

    # ── Explicit edge wiring ───────────────────────────────────────

    def edge(
        self,
        source: PortRef,
        target: PortRef,
        *,
        spread: bool = False,
        lint: dict[str, Any] | None = None,
    ) -> None:
        """Explicitly wire a source port to a target port."""
        self._edges.append(_PendingEdge(
            source_node_id=source.node_id,
            source_port=source.port_name,
            target_node_id=target.node_id,
            target_port=target.port_name,
            edge_type="data",
            spread=spread,
            metadata={"lint": lint} if lint is not None else {},
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

    def resource(self, kind: str, ref: str, value: dict[str, Any]) -> None:
        """Register a named Worker-shared resource."""
        bucket = self._worker_resources.setdefault(kind, {})
        bucket[ref] = dict(value)

    # ── Build / serialize ──────────────────────────────────────────

    def build(self, *, lint_autogen: str | None = None) -> Graph:
        """Compile this builder into a validated Graph model."""
        return compile_graph(
            name=self._name,
            description=self._description,
            tags=self._tags,
            nodes=list(self._nodes),
            edges=list(self._edges),
            sub_graphs=list(self._sub_graphs),
            shared_context=list(self._shared_context),
            worker_resources={k: dict(v) for k, v in self._worker_resources.items()},
            port_ref_connections=list(self._port_ref_connections),
            artifact_refs=list(self._artifact_refs),
            hyperedge_specs=list(self._hyperedges),
            lint_autogen=self._lint_autogen if lint_autogen is None else lint_autogen,
            lint_intent_refiner=self._lint_intent_refiner,
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
            worker_resources={k: dict(v) for k, v in self._worker_resources.items()},
            port_ref_connections=list(self._port_ref_connections),
            artifact_refs=list(self._artifact_refs),
            hyperedge_specs=list(self._hyperedges),
            lint_autogen=self._lint_autogen,
            lint_intent_refiner=self._lint_intent_refiner,
            validate=True,
        )
