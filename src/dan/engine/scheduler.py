"""Graph scheduler — topological sort, ready-queue dispatch, and the Engine API."""

from __future__ import annotations

import asyncio
import logging
import time as _time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from dan.engine.checkpoint import CheckpointStore, FileSystemCheckpointStore
from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.events import EngineEvent, EventType
from dan.engine.executor import EngineConfig, ExecutionContext, ExecutorRegistry, NodeResult
from dan.engine.state import ExecutionState, NodeStatus
from dan.models.edges import ControlEdge, ContextEdge, DataEdge
from dan.models.graph import Graph

try:
    from dan.models.control_flow import GateNode  # noqa: F401 — added by another agent
    _HAS_GATE_NODE = True
except ImportError:
    _HAS_GATE_NODE = False

EventCallback = Callable[[EngineEvent], Awaitable[None]]

logger = logging.getLogger(__name__)

_VALIDATION_WARNING_PATTERNS = (
    "schema safety bypassed",
    "untyped data edge",
    "deprecated",
)


def _is_validation_warning(msg: str) -> bool:
    """True if *msg* is a non-fatal validation warning, not a blocking error."""
    lower = msg.lower()
    return any(p in lower for p in _VALIDATION_WARNING_PATTERNS)


@dataclass
class RunResult:
    """Final result of an Engine.run() or Engine.resume() call."""

    run_id: str
    outputs: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    node_statuses: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def _topological_levels(graph: Graph) -> list[list[str]]:
    """Group nodes into parallel execution levels via Kahn's algorithm.

    Nodes in the same level have no data dependencies on each other and
    can run concurrently.
    """
    in_degree: dict[str, int] = defaultdict(int)
    dependents: dict[str, list[str]] = defaultdict(list)

    node_ids = {n.id for n in graph.nodes}
    for nid in node_ids:
        in_degree.setdefault(nid, 0)

    for edge in graph.edges:
        if isinstance(edge, DataEdge) and edge.target_node_id in node_ids:
            in_degree[edge.target_node_id] += 1
            dependents[edge.source_node_id].append(edge.target_node_id)

    levels: list[list[str]] = []
    queue = [nid for nid in node_ids if in_degree[nid] == 0]

    while queue:
        levels.append(sorted(queue))
        next_queue: list[str] = []
        for nid in queue:
            for dep in dependents[nid]:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    next_queue.append(dep)
        queue = next_queue

    return levels


def _is_gate_node(node) -> bool:
    """Check whether *node* is a gate node regardless of import availability."""
    if _HAS_GATE_NODE:
        from dan.models.control_flow import GateNode
        return isinstance(node, GateNode)
    return getattr(node, "node_type", None) == "gate"


def _topological_levels_with_backedges(
    graph: Graph,
) -> tuple[list[list[str]], dict[str, str], dict[str, set[str]]]:
    """Extended topo sort that identifies back-edges and cycle regions.

    Returns:
        levels: standard topo levels (with back-edges excluded from in-degree)
        back_edges: dict mapping gate_node_id -> back-edge target_node_id
        cycle_regions: dict mapping gate_node_id -> set of node IDs in its cycle
    """
    node_ids = {n.id for n in graph.nodes}
    node_map = {n.id: n for n in graph.nodes}

    # --- Step 1: identify candidate back-edges --------------------------
    # A back-edge is a DataEdge from a gate(while) node's continue-style port
    # pointing backward in the graph.
    candidate_back: list[DataEdge] = []
    forward_edges: list[DataEdge] = []

    for edge in graph.edges:
        if not isinstance(edge, DataEdge) or edge.target_node_id not in node_ids:
            continue
        src_node = node_map.get(edge.source_node_id)
        if (
            src_node is not None
            and _is_gate_node(src_node)
            and getattr(src_node, "gate_mode", None) == "while"
            and edge.source_port in ("continue", "loop")
        ):
            candidate_back.append(edge)
        else:
            forward_edges.append(edge)

    # Build a preliminary topo ordering ignoring candidates so we can confirm
    # which candidates truly point backward.
    in_deg: dict[str, int] = defaultdict(int)
    deps: dict[str, list[str]] = defaultdict(list)
    for nid in node_ids:
        in_deg.setdefault(nid, 0)
    for edge in forward_edges:
        if edge.source_node_id in node_ids:
            in_deg[edge.target_node_id] += 1
            deps[edge.source_node_id].append(edge.target_node_id)

    order: dict[str, int] = {}
    queue: list[str] = [nid for nid in node_ids if in_deg[nid] == 0]
    idx = 0
    while queue:
        next_q: list[str] = []
        for nid in sorted(queue):
            order[nid] = idx
            idx += 1
            for dep in deps[nid]:
                in_deg[dep] -= 1
                if in_deg[dep] == 0:
                    next_q.append(dep)
        queue = next_q

    # Confirm back-edges: target must appear *before* source in the ordering
    back_edges: dict[str, str] = {}
    for edge in candidate_back:
        src_ord = order.get(edge.source_node_id)
        tgt_ord = order.get(edge.target_node_id)
        if src_ord is not None and tgt_ord is not None and tgt_ord < src_ord:
            back_edges[edge.source_node_id] = edge.target_node_id
        else:
            forward_edges.append(edge)

    # --- Step 2: standard Kahn's with back-edges excluded ---------------
    in_degree: dict[str, int] = defaultdict(int)
    dependents: dict[str, list[str]] = defaultdict(list)
    for nid in node_ids:
        in_degree.setdefault(nid, 0)
    for edge in forward_edges:
        if edge.source_node_id in node_ids:
            in_degree[edge.target_node_id] += 1
            dependents[edge.source_node_id].append(edge.target_node_id)

    levels: list[list[str]] = []
    queue = [nid for nid in node_ids if in_degree[nid] == 0]
    while queue:
        levels.append(sorted(queue))
        next_q = []
        for nid in queue:
            for dep in dependents[nid]:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    next_q.append(dep)
        queue = next_q

    # --- Step 3: compute cycle regions per gate -------------------------
    # For each back-edge (gate -> loop_target), the cycle region is all nodes
    # reachable from loop_target that can reach the gate via forward edges.
    fwd_adj: dict[str, set[str]] = defaultdict(set)
    rev_adj: dict[str, set[str]] = defaultdict(set)
    for edge in forward_edges:
        if edge.source_node_id in node_ids and edge.target_node_id in node_ids:
            fwd_adj[edge.source_node_id].add(edge.target_node_id)
            rev_adj[edge.target_node_id].add(edge.source_node_id)

    cycle_regions: dict[str, set[str]] = {}
    for gate_id, loop_target in back_edges.items():
        reachable_fwd: set[str] = set()
        q: deque[str] = deque([loop_target])
        while q:
            n = q.popleft()
            if n in reachable_fwd:
                continue
            reachable_fwd.add(n)
            for succ in fwd_adj.get(n, set()):
                if succ not in reachable_fwd:
                    q.append(succ)

        reachable_rev: set[str] = set()
        q = deque([gate_id])
        while q:
            n = q.popleft()
            if n in reachable_rev:
                continue
            reachable_rev.add(n)
            for pred in rev_adj.get(n, set()):
                if pred not in reachable_rev:
                    q.append(pred)

        cycle_regions[gate_id] = reachable_fwd & reachable_rev

    return levels, back_edges, cycle_regions


class Engine:
    """The core graph execution engine.

    Usage::

        engine = Engine(config)
        result = await engine.run(graph, inputs={"idea": "..."})
        result = await engine.resume(graph, run_id="abc123")
    """

    def __init__(
        self,
        config: EngineConfig | None = None,
        executor_registry: ExecutorRegistry | None = None,
        checkpoint_store: CheckpointStore | None = None,
        human_input_callback: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        event_callback: EventCallback | None = None,
    ) -> None:
        self.config = config or EngineConfig()
        self.executor_registry = executor_registry or ExecutorRegistry()
        self.human_input_callback = human_input_callback
        self.event_callback = event_callback

        if checkpoint_store is not None:
            self.checkpoint_store: CheckpointStore | None = checkpoint_store
        elif self.config.checkpoint_enabled:
            self.checkpoint_store = FileSystemCheckpointStore(self.config.checkpoint_dir)
        else:
            self.checkpoint_store = None

        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register built-in executors for all standard node types."""
        from dan.executors.llm import LLMExecutor
        from dan.executors.tool import ToolExecutor
        from dan.executors.code import CodeExecutor
        from dan.executors.input import InputExecutor
        from dan.executors.control_flow import (
            CompositeExecutor,
            ForEachExecutor,
            GateExecutor,
            HumanInTheLoopExecutor,
            IfElseExecutor,
            ReduceExecutor,
            RouterExecutor,
            WhileLoopExecutor,
        )

        defaults: list[tuple[str, Any]] = [
            ("llm_operator", LLMExecutor()),
            ("tool_operator", ToolExecutor()),
            ("code_operator", CodeExecutor()),
            ("input", InputExecutor()),
            ("if_else", IfElseExecutor()),
            ("gate", GateExecutor()),
            ("while_loop", WhileLoopExecutor()),
            ("for_each", ForEachExecutor()),
            ("reduce", ReduceExecutor()),
            ("router", RouterExecutor()),
            ("human_in_the_loop", HumanInTheLoopExecutor()),
            ("composite", CompositeExecutor()),
        ]

        for node_type, executor in defaults:
            if not self.executor_registry.has(node_type):
                self.executor_registry.register(node_type, executor)

    async def _emit(self, event: EngineEvent) -> None:
        if self.event_callback is not None:
            try:
                await self.event_callback(event)
            except Exception:
                logger.debug("Event callback failed for %s", event.event_type)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        graph: Graph,
        inputs: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> RunResult:
        """Execute *graph* from entry points to exit points.

        *inputs* are injected as output-port values on entry-point nodes
        so that downstream nodes receive them via normal edge resolution.
        """
        from dan.validation.graph import validate_graph

        all_errors = validate_graph(graph)
        fatal = [e for e in all_errors if not _is_validation_warning(e)]
        if fatal:
            return RunResult(
                run_id=run_id or "invalid",
                success=False,
                errors={"validation": "; ".join(fatal)},
            )

        state = ExecutionState(graph, run_id)
        shared_context = SharedContextStore(graph.shared_context)
        artifacts = ArtifactStore()
        local_state = LocalStateManager()

        if inputs:
            self._inject_inputs(state, graph, inputs)

        return await self._execute(graph, state, shared_context, artifacts, local_state)

    async def resume(
        self,
        graph: Graph,
        run_id: str,
    ) -> RunResult:
        """Resume a previously checkpointed run."""
        if self.checkpoint_store is None:
            return RunResult(
                run_id=run_id,
                success=False,
                errors={"checkpoint": "No checkpoint store configured"},
            )

        checkpoint = await self.checkpoint_store.load(run_id)
        if checkpoint is None:
            return RunResult(
                run_id=run_id,
                success=False,
                errors={"checkpoint": f"No checkpoint found for run_id '{run_id}'"},
            )

        state = ExecutionState(graph, run_id)
        state.restore_from_snapshot(checkpoint["state"])

        shared_context = SharedContextStore(graph.shared_context)
        shared_context.restore(checkpoint.get("shared_context", {}))

        artifacts = ArtifactStore()
        artifacts.restore(checkpoint.get("artifacts", {}))

        local_state = LocalStateManager()
        local_state.restore(checkpoint.get("local_state", {}))

        return await self._execute(graph, state, shared_context, artifacts, local_state)

    # ------------------------------------------------------------------
    # Internal scheduling
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_inputs(
        state: ExecutionState,
        graph: Graph,
        inputs: dict[str, Any],
    ) -> None:
        """Inject external inputs as port values on entry-point nodes.

        Stores each input under a virtual ``__input__<node_id>`` source
        so that ``_resolve_entry_inputs`` can pick them up.
        """
        for entry_id in graph.entry_points:
            node = graph.node_by_id(entry_id)
            if node is None:
                continue
            for port_name, value in inputs.items():
                state.port_data.set(f"__input__{entry_id}", port_name, value)

    async def _execute(
        self,
        graph: Graph,
        state: ExecutionState,
        shared_context: SharedContextStore,
        artifacts: ArtifactStore,
        local_state: LocalStateManager,
    ) -> RunResult:
        """Core scheduling loop: dispatch ready nodes, checkpoint, repeat."""
        run_start = _time.time()
        await self._emit(EngineEvent(
            event_type=EventType.RUN_STARTED,
            run_id=state.run_id,
            data={"node_count": len(graph.nodes)},
        ))

        context = self._make_context(
            state, shared_context, artifacts, local_state, graph
        )

        levels, back_edges, cycle_regions = _topological_levels_with_backedges(graph)

        if not back_edges:
            # DAG fast-path — identical to original behaviour
            for level in levels:
                ready = [
                    nid for nid in level
                    if state.node_statuses.get(nid) == NodeStatus.PENDING
                ]
                if not ready:
                    continue

                tasks = [
                    self._execute_node(nid, graph, state, context)
                    for nid in ready
                ]
                await asyncio.gather(*tasks)

                if self.checkpoint_store is not None:
                    await self._save_checkpoint(
                        state, shared_context, artifacts, local_state
                    )
        else:
            await self._execute_with_cycles(
                graph, state, context, levels, back_edges, cycle_regions,
                shared_context, artifacts, local_state,
            )

        result = self._build_result(graph, state)
        elapsed = round(_time.time() - run_start, 2)
        total_usage = self._aggregate_usage(state)
        evt_type = EventType.RUN_COMPLETED if result.success else EventType.RUN_FAILED
        await self._emit(EngineEvent(
            event_type=evt_type,
            run_id=state.run_id,
            data={
                "success": result.success,
                "errors": result.errors,
                "elapsed_seconds": elapsed,
                **total_usage,
            },
        ))
        return result

    # ------------------------------------------------------------------
    # Cycle-aware execution
    # ------------------------------------------------------------------

    async def _execute_with_cycles(
        self,
        graph: Graph,
        state: ExecutionState,
        context: ExecutionContext,
        levels: list[list[str]],
        back_edges: dict[str, str],
        cycle_regions: dict[str, set[str]],
        shared_context: SharedContextStore,
        artifacts: ArtifactStore,
        local_state: LocalStateManager,
    ) -> None:
        """Execute graph with cycle regions handled via bounded iteration."""
        executed_gates: set[str] = set()

        for level in levels:
            ready = [
                nid for nid in level
                if state.node_statuses.get(nid) == NodeStatus.PENDING
            ]
            if not ready:
                continue

            tasks = [
                self._execute_node(nid, graph, state, context)
                for nid in ready
            ]
            await asyncio.gather(*tasks)

            for nid in ready:
                if nid not in cycle_regions or nid in executed_gates:
                    continue
                gate_node = graph.node_by_id(nid)
                if gate_node is None or not _is_gate_node(gate_node):
                    continue

                gate_outputs = state.port_data.get_node_outputs(nid)
                active_branch = None
                for port_name in gate_outputs:
                    if port_name in ("continue", "loop"):
                        active_branch = port_name
                        break

                if active_branch is not None:
                    max_iter = getattr(gate_node, "max_iterations", 10)
                    cycle_nodes = cycle_regions[nid]
                    back_edge_target = back_edges.get(nid)
                    if back_edge_target:
                        await self._iterate_cycle(
                            graph, state, context, nid, cycle_nodes,
                            back_edge_target, max_iter, levels,
                        )
                        executed_gates.add(nid)

            if self.checkpoint_store is not None:
                await self._save_checkpoint(
                    state, shared_context, artifacts, local_state,
                )

    async def _iterate_cycle(
        self,
        graph: Graph,
        state: ExecutionState,
        context: ExecutionContext,
        gate_id: str,
        cycle_nodes: set[str],
        back_edge_target: str,
        max_iterations: int,
        levels: list[list[str]],
    ) -> None:
        """Re-execute cycle region nodes until gate emits 'done' or max iterations."""
        for iteration in range(1, max_iterations):
            await self._emit(EngineEvent(
                event_type=EventType.ITERATION_STARTED,
                run_id=state.run_id,
                node_id=gate_id,
                node_type="gate",
                data={"iteration": iteration, "max_iterations": max_iterations},
            ))

            continue_data = state.port_data.get_node_outputs(gate_id).get("continue")
            if continue_data is None:
                continue_data = state.port_data.get_node_outputs(gate_id).get("loop")

            for cn in cycle_nodes:
                state.port_data.clear_node(cn)
                state.mark(cn, NodeStatus.PENDING)

            if isinstance(continue_data, dict):
                for port_name, value in continue_data.items():
                    state.port_data.set(
                        f"__input__{back_edge_target}", port_name, value,
                    )

            cycle_levels = [
                [nid for nid in level if nid in cycle_nodes]
                for level in levels
            ]

            for level in cycle_levels:
                ready = [
                    nid for nid in level
                    if state.node_statuses.get(nid) == NodeStatus.PENDING
                ]
                if not ready:
                    continue
                tasks = [
                    self._execute_node(nid, graph, state, context)
                    for nid in ready
                ]
                await asyncio.gather(*tasks)

            gate_outputs = state.port_data.get_node_outputs(gate_id)
            exiting = "done" in gate_outputs or "false" in gate_outputs
            await self._emit(EngineEvent(
                event_type=EventType.ITERATION_COMPLETED,
                run_id=state.run_id,
                node_id=gate_id,
                node_type="gate",
                data={
                    "iteration": iteration,
                    "max_iterations": max_iterations,
                    "exit": exiting,
                },
            ))
            if exiting:
                break

    async def _execute_node(
        self,
        node_id: str,
        graph: Graph,
        state: ExecutionState,
        context: ExecutionContext,
    ) -> None:
        """Resolve inputs, dispatch to executor, store outputs."""
        node = graph.node_by_id(node_id)
        if node is None:
            state.mark(node_id, NodeStatus.FAILED)
            state.node_errors[node_id] = f"Node '{node_id}' not found in graph"
            await self._emit(EngineEvent(
                event_type=EventType.NODE_FAILED, run_id=state.run_id,
                node_id=node_id, data={"error": state.node_errors[node_id]},
            ))
            return

        node_type_str = getattr(node, "node_type", None)

        if self._should_skip(node_id, graph, state):
            state.mark(node_id, NodeStatus.SKIPPED)
            await self._emit(EngineEvent(
                event_type=EventType.NODE_SKIPPED, run_id=state.run_id,
                node_id=node_id, node_type=node_type_str,
            ))
            return

        state.mark(node_id, NodeStatus.RUNNING)
        await self._emit(EngineEvent(
            event_type=EventType.NODE_STARTED, run_id=state.run_id,
            node_id=node_id, node_type=node_type_str,
        ))

        inputs = state.port_data.resolve_inputs(node_id, graph)

        virtual_src = f"__input__{node_id}"
        for port in node.input_ports:
            if state.port_data.has(virtual_src, port.name):
                inputs[port.name] = state.port_data.get(virtual_src, port.name)

        self._read_context_edges(node_id, graph, context, inputs)

        if node_type_str is None or not self.executor_registry.has(node_type_str):
            state.mark(node_id, NodeStatus.FAILED)
            state.node_errors[node_id] = f"No executor for node_type '{node_type_str}'"
            await self._emit(EngineEvent(
                event_type=EventType.NODE_FAILED, run_id=state.run_id,
                node_id=node_id, node_type=node_type_str,
                data={"error": state.node_errors[node_id]},
            ))
            return

        executor = self.executor_registry.get(node_type_str)

        try:
            result: NodeResult = await executor.execute(node, inputs, context)
        except Exception as exc:
            logger.exception("Executor raised for node '%s'", node_id)
            result = NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Executor exception: {exc}",
            )

        state.mark(node_id, result.status)
        if result.error:
            state.node_errors[node_id] = result.error
        if result.metadata:
            state.node_metadata[node_id] = result.metadata

        for port_name, value in result.outputs.items():
            state.port_data.set(node_id, port_name, value)

        self._write_context_edges(node_id, graph, context, result.outputs)

        if result.status == NodeStatus.FAILED:
            await self._emit(EngineEvent(
                event_type=EventType.NODE_FAILED, run_id=state.run_id,
                node_id=node_id, node_type=node_type_str,
                data={"error": result.error or ""},
            ))
        elif result.status == NodeStatus.COMPLETED:
            await self._emit(EngineEvent(
                event_type=EventType.NODE_COMPLETED, run_id=state.run_id,
                node_id=node_id, node_type=node_type_str,
                data={"metadata": result.metadata},
            ))
            if result.outputs:
                await self._emit(EngineEvent(
                    event_type=EventType.NODE_OUTPUT, run_id=state.run_id,
                    node_id=node_id, node_type=node_type_str,
                    data={"outputs": result.outputs},
                ))

    def _should_skip(
        self, node_id: str, graph: Graph, state: ExecutionState
    ) -> bool:
        """Skip a node if it's on an inactive branch.

        Handles both legacy ControlEdge-based branching and newer gate
        branch-port routing where the inactive port has no data.
        """
        for edge in graph.edges_to(node_id):
            if not isinstance(edge, ControlEdge):
                continue
            if edge.condition is None:
                continue
            source_outputs = state.port_data.get_node_outputs(edge.source_node_id)
            active_branch = source_outputs.get("branch")
            if active_branch is not None and active_branch != edge.condition:
                return True

        for edge in graph.edges_to(node_id):
            if not isinstance(edge, DataEdge):
                continue
            source_node = graph.node_by_id(edge.source_node_id)
            if source_node is None or not _is_gate_node(source_node):
                continue
            # Back-edge ports (continue/loop on while-gates) should not
            # trigger skipping — the target is a loop-back node, not an
            # inactive forward branch.
            gate_mode = getattr(source_node, "gate_mode", None)
            if gate_mode == "while" and edge.source_port in ("continue", "loop"):
                continue
            if not state.port_data.has(edge.source_node_id, edge.source_port):
                return True

        return False

    @staticmethod
    def _read_context_edges(
        node_id: str,
        graph: Graph,
        context: ExecutionContext,
        inputs: dict[str, Any],
    ) -> None:
        """Inject shared-context values into inputs via context edges."""
        for edge in graph.edges_to(node_id):
            if isinstance(edge, ContextEdge) and edge.mode.value == "read":
                try:
                    value = context.shared_context.read(edge.context_key)
                    if value is not None:
                        inputs[edge.target_port] = value
                except KeyError:
                    pass

    @staticmethod
    def _write_context_edges(
        node_id: str,
        graph: Graph,
        context: ExecutionContext,
        outputs: dict[str, Any],
    ) -> None:
        """Write node outputs to shared context via context edges."""
        for edge in graph.edges_from(node_id):
            if not isinstance(edge, ContextEdge):
                continue
            value = outputs.get(edge.source_port)
            if value is None:
                continue
            try:
                if edge.mode.value == "write":
                    context.shared_context.write(edge.context_key, value)
                elif edge.mode.value == "append":
                    context.shared_context.append(edge.context_key, value)
            except KeyError:
                logger.warning(
                    "Context write failed for key '%s' from node '%s'",
                    edge.context_key, node_id,
                )

    def _make_context(
        self,
        state: ExecutionState,
        shared_context: SharedContextStore,
        artifacts: ArtifactStore,
        local_state: LocalStateManager,
        graph: Graph,
        layer_path: tuple[str, ...] = (),
    ) -> ExecutionContext:
        async def run_subgraph(
            sub_graph_key: str,
            inputs: dict[str, Any],
            parent_node_id: str | None = None,
            targeted_inputs: dict[str, dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
            child_layer = layer_path + ((parent_node_id,) if parent_node_id else ())
            return await self._run_subgraph(
                sub_graph_key, inputs, graph, state,
                shared_context, artifacts, local_state,
                child_layer, targeted_inputs,
            )

        return ExecutionContext(
            state=state,
            config=self.config,
            shared_context=shared_context,
            artifacts=artifacts,
            local_state=local_state,
            human_input_callback=self.human_input_callback,
            run_subgraph=run_subgraph,
            event_callback=self.event_callback,
            run_id=state.run_id,
            layer_path=layer_path,
        )

    async def _run_subgraph(
        self,
        sub_graph_key: str,
        inputs: dict[str, Any],
        parent_graph: Graph,
        parent_state: ExecutionState,
        shared_context: SharedContextStore,
        artifacts: ArtifactStore,
        local_state: LocalStateManager,
        layer_path: tuple[str, ...] = (),
        targeted_inputs: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Execute a named sub-graph and return its outputs."""
        sub_graph = parent_graph.sub_graphs.get(sub_graph_key)
        if sub_graph is None:
            raise RuntimeError(f"Sub-graph '{sub_graph_key}' not found")

        sub_state = ExecutionState(sub_graph, run_id=parent_state.run_id)
        sub_context = self._make_context(
            sub_state, shared_context, artifacts, local_state, sub_graph, layer_path
        )

        if inputs:
            for entry_id in sub_graph.entry_points:
                entry_node = sub_graph.node_by_id(entry_id)
                if entry_node is not None:
                    for port in entry_node.input_ports:
                        if port.name in inputs:
                            sub_state.port_data.set(
                                f"__input__{entry_id}", port.name, inputs[port.name]
                            )

        if targeted_inputs:
            for node_id, port_values in targeted_inputs.items():
                for port_name, value in port_values.items():
                    sub_state.port_data.set(f"__input__{node_id}", port_name, value)

        levels = _topological_levels(sub_graph)
        for level in levels:
            ready = [
                nid for nid in level
                if sub_state.node_statuses.get(nid) == NodeStatus.PENDING
            ]
            if not ready:
                continue

            tasks = [
                self._execute_node(nid, sub_graph, sub_state, sub_context)
                for nid in ready
            ]
            await asyncio.gather(*tasks)

        outputs: dict[str, Any] = {}
        for exit_id in sub_graph.exit_points:
            outputs.update(sub_state.port_data.get_node_outputs(exit_id))

        return outputs

    async def _save_checkpoint(
        self,
        state: ExecutionState,
        shared_context: SharedContextStore,
        artifacts: ArtifactStore,
        local_state: LocalStateManager,
    ) -> None:
        if self.checkpoint_store is None:
            return
        checkpoint = {
            "state": state.snapshot(),
            "shared_context": shared_context.snapshot(),
            "artifacts": artifacts.snapshot(),
            "local_state": local_state.snapshot(),
        }
        await self.checkpoint_store.save(state.run_id, checkpoint)

    @staticmethod
    def _aggregate_usage(state: ExecutionState) -> dict[str, Any]:
        """Sum token usage from all node metadata entries."""
        totals = {"total_prompt_tokens": 0, "total_completion_tokens": 0, "total_tokens": 0}
        for meta in state.node_metadata.values():
            usage = meta.get("usage") if isinstance(meta, dict) else None
            if not usage:
                continue
            totals["total_prompt_tokens"] += usage.get("prompt_tokens", 0)
            totals["total_completion_tokens"] += usage.get("completion_tokens", 0)
            totals["total_tokens"] += usage.get("total_tokens", 0)
        return totals

    @staticmethod
    def _build_result(graph: Graph, state: ExecutionState) -> RunResult:
        outputs: dict[str, Any] = {}
        for exit_id in graph.exit_points:
            outputs.update(state.port_data.get_node_outputs(exit_id))

        has_failures = any(
            s == NodeStatus.FAILED for s in state.node_statuses.values()
        )

        return RunResult(
            run_id=state.run_id,
            outputs=outputs,
            success=not has_failures,
            node_statuses={nid: s.value for nid, s in state.node_statuses.items()},
            errors=dict(state.node_errors),
            metadata=dict(state.node_metadata),
        )
