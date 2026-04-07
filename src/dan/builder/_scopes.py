"""Private builder scope/context helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

from dan.builder.compiler import BuildError, _PendingEdge, _PendingNode, _PendingSubGraph, compile_graph
from dan.builder.compiler import default_input_port, default_output_port
from dan.builder.refs import NodeRef, PortRef
from dan.models.context import (
    CompactionRule,
    ContextDeclaration,
    FailurePolicy,
    MergeStrategy,
)
from dan.models.graph import Graph


def _has_material_content(graph: Graph) -> bool:
    return bool(
        graph.nodes
        or graph.edges
        or graph.sub_graphs
        or graph.shared_context
        or graph.artifact_refs
        or graph.hyperedges
        or graph.worker_resources
    )


def auto_rules(schema: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Derive validation rules from a JSON Schema dict."""
    if not schema or not isinstance(schema, dict):
        return []
    rules: list[dict[str, Any]] = []
    required_keys = schema.get("required", [])
    if required_keys:
        rules.append({"rule_type": "required_keys", "config": {"keys": required_keys}})
    rules.append({"rule_type": "schema_conformance", "config": {"schema": schema}})
    return rules


def first_composite_input_port(
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


def first_composite_output_port(
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


class _WorkerScopeContext:
    """Context object for defining a Worker body graph and named sub-workers."""

    def __init__(
        self,
        builder: Any,
        node_id: str,
        *,
        role: str,
        instruction: str,
        persona: str,
        authority: str,
        model: str | None,
        tool_ids: list[str],
        tool_config: dict[str, Any] | None,
        code: str,
        language: str,
        llm: dict[str, Any] | None,
        llm_hints: dict[str, Any] | None,
        context: dict[str, Any] | None,
        authority_policy: dict[str, Any] | None,
        execution: dict[str, Any] | None,
        control_flow: dict[str, Any] | None,
        input_mappings: dict[str, str] | None,
        output_mappings: dict[str, str] | None,
        parallelism: int,
        merge_strategy: MergeStrategy,
        spawn_policy: dict[str, Any] | None,
        external_input_schema: dict[str, Any] | None,
        external_output_schema: dict[str, Any] | None,
        control_state_schema: dict[str, Any] | None,
        local_state: dict[str, Any] | None,
        compaction_rule: dict[str, Any] | None,
        failure_policy: dict[str, Any] | None,
        projections: list[dict[str, Any]] | None,
        boundary_contract: dict[str, Any] | None,
        validation_rules: list[dict[str, Any]] | None,
        name: str | None,
        description: str,
        read_set: list[ContextDeclaration] | None,
        write_set: list[ContextDeclaration] | None,
        input_ports: list[dict[str, Any]] | None,
        output_ports: list[dict[str, Any]] | None,
    ) -> None:
        self._builder = builder
        self._node_id = node_id
        self._body_key = f"{node_id}_body"
        builder_cls = type(builder)
        self._body = builder_cls(
            self._body_key,
            _parent=builder,
            _scope_type="worker",
        )
        self._body._entry_input_ref = PortRef("__entry__", "input", self._body)
        self._sub_workers: dict[str, str] = {}
        self._sub_graphs: list[tuple[str, Graph]] = []
        self._node_kwargs: dict[str, Any] = {
            "role": role,
            "instruction": instruction,
            "persona": persona,
            "authority": authority,
            "model": model,
            "tool_ids": tool_ids,
            "tool_config": tool_config,
            "code": code,
            "language": language,
            "llm": llm,
            "llm_hints": llm_hints,
            "context": context,
            "authority_policy": authority_policy,
            "execution": execution,
            "control_flow": control_flow,
            "input_mappings": input_mappings,
            "output_mappings": output_mappings,
            "parallelism": parallelism,
            "merge_strategy": merge_strategy,
            "spawn_policy": spawn_policy,
            "external_input_schema": external_input_schema,
            "external_output_schema": external_output_schema,
            "control_state_schema": control_state_schema,
            "local_state": local_state,
            "compaction_rule": compaction_rule,
            "failure_policy": failure_policy,
            "projections": projections,
            "boundary_contract": boundary_contract,
            "validation_rules": validation_rules,
            "name": name,
            "description": description,
            "read_set": read_set,
            "write_set": write_set,
            "input_ports": input_ports,
            "output_ports": output_ports,
        }

    def __getattr__(self, name: str) -> Any:
        if hasattr(self._body, name):
            return getattr(self._body, name)
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    @contextmanager
    def sub_worker(self, alias: str) -> Generator[Any, None, None]:
        """Define a named sub-worker graph owned by this Worker."""
        sub_key = f"{self._node_id}_{alias}"
        builder_cls = type(self._builder)
        sub = builder_cls(
            sub_key,
            _parent=self._builder,
            _scope_type="worker_sub",
        )
        sub._entry_input_ref = PortRef("__entry__", "input", sub)
        yield sub
        sub_graph = sub._compile_as_subgraph()
        self._sub_workers[alias] = sub_key
        self._sub_graphs.append((sub_key, sub_graph))

    @staticmethod
    def _has_material_content(graph: Graph) -> bool:
        return _has_material_content(graph)

    def _finalize(self) -> None:
        body_graph = self._body._compile_as_subgraph()
        body_graph_key: str | None = None
        if self._has_material_content(body_graph):
            body_graph_key = self._body_key

        self._builder.worker(
            self._node_id,
            role=self._node_kwargs["role"],
            instruction=self._node_kwargs["instruction"],
            persona=self._node_kwargs["persona"],
            authority=self._node_kwargs["authority"],
            model=self._node_kwargs["model"],
            tool_ids=self._node_kwargs["tool_ids"],
            tool_config=self._node_kwargs["tool_config"],
            code=self._node_kwargs["code"],
            language=self._node_kwargs["language"],
            llm=self._node_kwargs["llm"],
            llm_hints=self._node_kwargs["llm_hints"],
            context=self._node_kwargs["context"],
            authority_policy=self._node_kwargs["authority_policy"],
            execution=self._node_kwargs["execution"],
            control_flow=self._node_kwargs["control_flow"],
            input_mappings=self._node_kwargs["input_mappings"],
            output_mappings=self._node_kwargs["output_mappings"],
            parallelism=self._node_kwargs["parallelism"],
            merge_strategy=self._node_kwargs["merge_strategy"],
            spawn_policy=self._node_kwargs["spawn_policy"],
            external_input_schema=self._node_kwargs["external_input_schema"],
            external_output_schema=self._node_kwargs["external_output_schema"],
            control_state_schema=self._node_kwargs["control_state_schema"],
            local_state=self._node_kwargs["local_state"],
            compaction_rule=self._node_kwargs["compaction_rule"],
            failure_policy=self._node_kwargs["failure_policy"],
            projections=self._node_kwargs["projections"],
            body_graph=body_graph_key,
            sub_workers=self._sub_workers or None,
            boundary_contract=self._node_kwargs["boundary_contract"],
            validation_rules=self._node_kwargs["validation_rules"],
            name=self._node_kwargs["name"],
            description=self._node_kwargs["description"],
            read_set=self._node_kwargs["read_set"],
            write_set=self._node_kwargs["write_set"],
            input_ports=self._node_kwargs["input_ports"],
            output_ports=self._node_kwargs["output_ports"],
        )

        if body_graph_key is not None:
            self._builder._sub_graphs.append(_PendingSubGraph(
                parent_node_id=self._node_id,
                sub_graph_key=body_graph_key,
                graph=body_graph,
            ))
        for sub_key, sub_graph in self._sub_graphs:
            self._builder._sub_graphs.append(_PendingSubGraph(
                parent_node_id=self._node_id,
                sub_graph_key=sub_key,
                graph=sub_graph,
            ))


class _ParallelSubagentsContext:
    """Context object for defining parallel subagent branches."""

    def __init__(
        self,
        builder: Any,
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
    def branch(self, key: str) -> Generator[Any, None, None]:
        """Define a branch sub-graph. Yields a WorkflowBuilder for the branch."""
        sub_key = f"{self._node_id}_{key}"
        builder_cls = type(self._builder)
        sub = builder_cls(sub_key, _parent=self._builder, _scope_type="parallel_branch")
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
        builder: Any,
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
    def team(self, team_name: str) -> Generator[Any, None, None]:
        """Define a team sub-graph. Yields a WorkflowBuilder for the team."""
        sub_key = f"{self._node_id}_{team_name}"
        builder_cls = type(self._builder)
        sub = builder_cls(sub_key, _parent=self._builder, _scope_type="orchestrator_team")
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
        builder: Any,
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
    def agent(self, agent_name: str) -> Generator[Any, None, None]:
        """Define an agent sub-graph. Yields a WorkflowBuilder for the agent."""
        sub_key = f"{self._node_id}_{agent_name}"
        builder_cls = type(self._builder)
        sub = builder_cls(sub_key, _parent=self._builder, _scope_type="agent_team_member")
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
    """Proxy returned by ``validated_composite`` that intercepts ``>>`` chains."""

    def __init__(self, composite_id: str, builder: Any) -> None:
        self._composite_id = composite_id
        self._builder = builder
        self._entry_node_id: str | None = None
        self._exit_node_id: str | None = None
        self._sub: Any | None = None

    def _set_sub(self, sub: Any) -> _ValidatedCompositeRef:
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
