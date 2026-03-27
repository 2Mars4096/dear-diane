"""Graph scheduler — topological sort, ready-queue dispatch, and the Engine API."""

from __future__ import annotations

import asyncio
import copy
import heapq
import hashlib
import json
import logging
import os
import time as _time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from dan.engine.cache import NodeResultCache, SemanticCache
from dan.engine.checkpoint import CheckpointStore, FileSystemCheckpointStore
from dan.engine.context_runtime import (
    ArtifactStore,
    LocalStateManager,
    ScopedContextView,
    SharedContextStore,
    create_reference,
    resolve_reference,
)
from dan.engine.events import EngineEvent, EventType
from dan.engine.runtime_composition import (
    RuntimeCompositionError,
    dynamic_topology_enabled,
    invoke_child_workflow,
)
from dan.engine.executor import EngineConfig, ExecutionContext, ExecutorRegistry, NodeResult
from dan.engine.runtime_policy import (
    EffectiveRunPolicy,
    RunPhase,
    RunPolicy,
    StopReason,
    is_critical_checkpoint_node,
    progress_snapshot,
    resolve_effective_run_policy,
    stable_invocation_key,
)
from dan.engine.runtime_repair import (
    RuntimeFailureContext,
    RuntimeRepairAttempt,
    RuntimeRepairKind,
    apply_pending_overlay,
    build_runtime_error_record,
    classify_runtime_failure,
    clear_pending_overlays,
    failure_signature,
    overlay_patch_for_node,
    plan_runtime_repair,
    record_repair_attempt,
    repair_summary_for_node,
    runtime_self_healing_enabled,
    select_automatic_recovery_candidate,
)
from dan.engine.memory import MemoryEntry, MemoryScope, MemoryWriteRequest
from dan.engine.memory_store import FileSystemMemoryStore, MemoryStore, NullMemoryStore
from dan.engine.state import ExecutionState, NodeStatus
from dan.engine.state_store import FileSystemStateStore, NodeExecutionSummary, NullStateStore
from dan.engine.token_optimization import TokenBudgetAdvisor
from dan.models.edges import ControlEdge, ContextEdge, DataEdge
from dan.models.graph import Graph
from dan.utils.template_render import render_runtime_template
from dan.utils.tokens import estimate_tokens

try:
    from dan.models.control_flow import GateNode  # noqa: F401 — added by another agent
    _HAS_GATE_NODE = True
except ImportError:
    _HAS_GATE_NODE = False

EventCallback = Callable[[EngineEvent], Awaitable[None]]

logger = logging.getLogger(__name__)
_NODE_SLOT_BYPASS_TYPES = frozenset({
    "while_loop",
    "for_each",
    "parallel_subagents",
    "orchestrator",
    "composite",
    "agent_team",
    "goal_loop",
    "llm_operator",
    "router",
    "vote",
    "reflection",
    "rag_operator",
})

_VALIDATION_WARNING_PATTERNS = (
    "schema safety bypassed",
    "untyped data edge",
    "deprecated",
    "warning:",
)


def _is_validation_warning(msg: str) -> bool:
    """True if *msg* is a non-fatal validation warning, not a blocking error."""
    lower = msg.lower()
    return any(p in lower for p in _VALIDATION_WARNING_PATTERNS)


def _render_template_for_cache(template: str, variables: dict[str, Any]) -> str:
    """Render prompt templates with safe fallback semantics for cache keys."""
    return render_runtime_template(template, variables)


def _stable_input_hash(payload: dict[str, Any]) -> str:
    """Stable hash for node input payloads used in runtime analytics."""
    try:
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        text = str(payload)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
        memory_store: MemoryStore | None = None,
        human_renderer: "HumanRenderer | None" = None,
        workflow_loader: Callable[[str], Any | None] | None = None,
        provider_registry: Any | None = None,
        model_gateway: Any | None = None,
        embedding_registry: Any | None = None,
    ) -> None:
        from dan.engine.executor import HumanRenderer as _HR  # noqa: F811
        self.config = config or EngineConfig()
        self.executor_registry = executor_registry or ExecutorRegistry()
        self.human_input_callback = human_input_callback
        self.human_renderer: _HR | None = human_renderer
        self.event_callback = event_callback
        self.workflow_loader = workflow_loader or getattr(self.config, "workflow_loader", None)

        if checkpoint_store is not None:
            self.checkpoint_store: CheckpointStore | None = checkpoint_store
        elif self.config.checkpoint_enabled:
            self.checkpoint_store = FileSystemCheckpointStore(self.config.checkpoint_dir)
        else:
            self.checkpoint_store = None

        if memory_store is not None:
            self.memory_store: MemoryStore = memory_store
        elif self.config.memory_enabled:
            self.memory_store = FileSystemMemoryStore(self.config.memory_dir)
        else:
            self.memory_store = NullMemoryStore()

        if self.config.state_store_enabled:
            state_dir = self.config.state_store_dir or os.path.join(
                self.config.checkpoint_dir, "state",
            )
            self.state_store = FileSystemStateStore(state_dir)
        else:
            self.state_store = NullStateStore()

        if provider_registry is not None:
            self.provider_registry = provider_registry
        else:
            self.provider_registry = self._build_provider_registry()
        if model_gateway is not None:
            self.model_gateway = model_gateway
        else:
            self.model_gateway = self._build_model_gateway()
        if embedding_registry is not None:
            self.embedding_registry = embedding_registry
        else:
            self.embedding_registry = self._build_embedding_registry()
        self._register_defaults()
        self._active_run_states: dict[str, ExecutionState] = {}

    def _build_provider_registry(self):
        """Create the ProviderRegistry from engine config."""
        from dan.providers.factory import build_provider_registry

        return build_provider_registry(self.config)

    def _build_model_gateway(self):
        """Create a gateway wrapper over the engine's provider registry when possible."""
        registry = getattr(self, "provider_registry", None)
        if registry is None or not hasattr(registry, "resolve"):
            return None
        from dan.llm_core.gateway import ModelGateway

        return ModelGateway(registry=registry)

    def _build_embedding_registry(self):
        """Create the EmbeddingRegistry from engine config."""
        from dan.rag import build_embedding_registry

        return build_embedding_registry(self.config)

    def _create_embedding_provider(self, name: str, config):
        """Instantiate an embedding provider by name."""
        from dan.rag import OpenAIEmbeddingProvider

        if name in {"default", "openai"}:
            api_key = config.api_key or self.config.llm_api_key
            base_url = (
                config.base_url if config.base_url is not None else self.config.llm_base_url
            )
            default_model = config.default_model or self.config.default_embedding_model
            try:
                return OpenAIEmbeddingProvider(
                    api_key=api_key,
                    base_url=base_url,
                    default_model=default_model,
                )
            except ImportError:
                logger.warning("openai package not installed; skipping embedding provider '%s'", name)
                return None

        if name in {"local", "sentence-transformers"}:
            try:
                from dan.rag import LocalEmbeddingProvider

                default_model = config.default_model or "all-MiniLM-L6-v2"
                return LocalEmbeddingProvider(default_model=default_model)
            except ImportError:
                logger.warning(
                    "sentence-transformers not installed; skipping embedding provider '%s'",
                    name,
                )
                return None

        # Unknown provider name — treat as OpenAI-compatible embedding API.
        logger.warning(
            "Unknown embedding provider '%s'; treating as OpenAI-compatible",
            name,
        )
        api_key = config.api_key or self.config.llm_api_key
        base_url = (
            config.base_url if config.base_url is not None else self.config.llm_base_url
        )
        default_model = config.default_model or self.config.default_embedding_model
        try:
            return OpenAIEmbeddingProvider(
                api_key=api_key,
                base_url=base_url,
                default_model=default_model,
            )
        except ImportError:
            logger.warning("openai package not installed; skipping embedding provider '%s'", name)
            return None

    def _register_defaults(self) -> None:
        """Register built-in executors for all standard node types."""
        from dan.executor_defaults import register_default_executors

        register_default_executors(self.executor_registry)

    @staticmethod
    def _apply_parameter_mutations(graph: Graph, mutations) -> Graph:
        """Apply ParameterMutation patches to a temporary graph copy.
        
        Creates a deep copy of affected nodes with their parameters patched.
        The original graph object is not modified.
        """
        import copy

        mutation_map: dict[str, dict] = {}
        for m in mutations:
            if m.target_node_id not in mutation_map:
                mutation_map[m.target_node_id] = {}
            mutation_map[m.target_node_id].update(m.changes)

        if not mutation_map:
            return graph

        patched_nodes = []
        changed = False
        for node in graph.nodes:
            if node.id in mutation_map:
                node_copy = copy.deepcopy(node)
                for field_name, value in mutation_map[node.id].items():
                    if hasattr(node_copy, field_name):
                        try:
                            setattr(node_copy, field_name, value)
                            changed = True
                        except (AttributeError, ValueError):
                            logger.debug(
                                "Cannot set %s=%r on node %s",
                                field_name, value, node.id,
                            )
                patched_nodes.append(node_copy)
            else:
                patched_nodes.append(node)

        if not changed:
            return graph

        return graph.model_copy(update={"nodes": patched_nodes})

    async def _emit(self, event: EngineEvent) -> None:
        if self.event_callback is not None:
            try:
                await self.event_callback(event)
            except Exception:
                logger.debug("Event callback failed for %s", event.event_type)

    async def _emit_post_run_analytics(
        self,
        run_id: str,
        graph: Graph,
        node_breakdowns: dict[str, dict[str, int]],
    ) -> None:
        """Run waste analysis on accumulated breakdowns and emit analytics events."""
        if not node_breakdowns:
            return
        try:
            from dan.engine.token_optimization import TokenWasteAnalyzer

            node_configs: dict[str, dict[str, Any]] = {}
            graph_edges: list[dict[str, Any]] = []
            for n in graph.nodes:
                nid = n.id
                cfg: dict[str, Any] = {"node_type": getattr(n, "node_type", "")}
                for attr in ("tools", "jit_tool_loading", "prompt_template",
                             "system_prompt", "input_ports", "agent_context_tools",
                             "memoize"):
                    val = getattr(n, attr, None)
                    if val is not None:
                        cfg[attr] = val if not hasattr(val, "model_dump") else val.model_dump()
                node_configs[nid] = cfg
            for e in graph.edges:
                graph_edges.append({
                    "id": e.id,
                    "source": e.source_node_id,
                    "target": e.target_node_id,
                    "edge_type": getattr(e, "edge_type", "data"),
                    "pass_by_reference": bool(getattr(e, "pass_by_reference", False)),
                })

            analyzer = TokenWasteAnalyzer()
            report = analyzer.analyze(
                node_breakdowns=node_breakdowns,
                node_configs=node_configs,
                graph_edges=graph_edges,
            )

            for finding in report.findings:
                await self._emit(EngineEvent(
                    event_type=EventType.WASTE_DETECTED,
                    run_id=run_id,
                    node_id=finding.node_id,
                    data=finding.to_dict(),
                ))

            await self._emit(EngineEvent(
                event_type=EventType.OPTIMIZATION_REPORT_READY,
                run_id=run_id,
                data=report.to_dict(),
            ))
        except Exception:
            logger.debug("Post-run analytics failed", exc_info=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        graph: Graph,
        inputs: dict[str, Any] | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        workflow_id: str | None = None,
        run_policy: RunPolicy | dict[str, Any] | None = None,
    ) -> RunResult:
        """Execute *graph* from entry points to exit points.

        *inputs* are injected as output-port values on entry-point nodes
        so that downstream nodes receive them via normal edge resolution.
        *session_id* enables cross-run memory; if provided, prior memory
        entries are pre-loaded into shared context.
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

        if session_id and workflow_id:
            await self._preload_memory(
                shared_context, workflow_id, session_id,
            )

        if inputs:
            self._inject_inputs(state, graph, inputs)

        try:
            return await self._execute(
                graph, state, shared_context, artifacts, local_state,
                session_id=session_id, workflow_id=workflow_id,
                run_policy=run_policy,
            )
        finally:
            self._active_run_states.pop(state.run_id, None)

    async def resume(
        self,
        graph: Graph,
        run_id: str,
        session_id: str | None = None,
        workflow_id: str | None = None,
        run_policy: RunPolicy | dict[str, Any] | None = None,
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

        try:
            return await self._execute(
                graph, state, shared_context, artifacts, local_state,
                session_id=session_id, workflow_id=workflow_id,
                cost_tracker_state=checkpoint.get("cost_tracker"),
                run_policy=run_policy,
                persisted_run_policy=(checkpoint.get("checkpoint_data", {}) or {}).get("effective_run_policy"),
            )
        finally:
            self._active_run_states.pop(state.run_id, None)

    async def queue_pending_overlay(
        self,
        run_id: str,
        node_id: str,
        patch: dict[str, Any],
        *,
        source: str = "user",
        reason: str = "",
    ) -> bool:
        """Apply an execution-local overlay to a currently pending node."""

        state = self._active_run_states.get(run_id)
        if state is None:
            return False
        status = state.node_statuses.get(node_id)
        if status not in (NodeStatus.PENDING, NodeStatus.WAITING):
            return False

        from dan.engine.runtime_repair import RuntimeOverlay

        overlay = RuntimeOverlay(
            node_id=node_id,
            patch=dict(patch),
            source=source,
            reason=reason,
            provenance={"external_request": True},
        )
        apply_pending_overlay(state, overlay)
        await self._emit(EngineEvent(
            event_type=EventType.NODE_OVERLAY_APPLIED,
            run_id=run_id,
            node_id=node_id,
            data={"overlay": overlay.model_dump()},
        ))
        return True

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

    @staticmethod
    def _check_halt(state: ExecutionState) -> bool:
        """Return True if any node signalled halt via metadata."""
        for meta in state.node_metadata.values():
            if isinstance(meta, dict) and meta.get("halt"):
                return True
        return False

    def _use_eager_dispatch(self) -> bool:
        return bool(getattr(self.config, "eager_dispatch", False)) or os.getenv(
            "DAN_EAGER_DISPATCH", "0",
        ) == "1"

    def _llm_max_concurrency(self) -> int | None:
        raw = os.getenv("DAN_MAX_CONCURRENT_LLM")
        if raw is not None and raw.strip():
            try:
                value = int(raw)
                return value if value > 0 else None
            except ValueError:
                logger.warning("Ignoring invalid DAN_MAX_CONCURRENT_LLM=%r", raw)
        configured = getattr(self.config, "llm_max_concurrency", None)
        if configured is not None and configured > 0:
            return configured
        if self.config.max_concurrency is not None and self.config.max_concurrency > 0:
            return self.config.max_concurrency
        return None

    @staticmethod
    def _build_dependency_graph(
        graph: Graph,
        node_ids: set[str] | None = None,
        excluded_edge_ids: set[str] | None = None,
    ) -> tuple[dict[str, int], dict[str, list[str]]]:
        """Build in-degree and successor maps from data edges."""
        active_nodes = node_ids or {node.id for node in graph.nodes}
        excluded = excluded_edge_ids or set()
        in_degree: dict[str, int] = defaultdict(int)
        dependents: dict[str, list[str]] = defaultdict(list)
        for node_id in active_nodes:
            in_degree.setdefault(node_id, 0)
        for edge in graph.edges:
            if not isinstance(edge, DataEdge):
                continue
            if edge.id in excluded:
                continue
            if edge.source_node_id not in active_nodes or edge.target_node_id not in active_nodes:
                continue
            in_degree[edge.target_node_id] += 1
            dependents[edge.source_node_id].append(edge.target_node_id)
        for node_id in dependents:
            dependents[node_id] = sorted(dependents[node_id])
        return in_degree, dependents

    @staticmethod
    def _prepare_state_for_dispatch(
        state: ExecutionState,
        in_degree: dict[str, int],
        dependents: dict[str, list[str]],
        node_ids: set[str],
    ) -> list[str]:
        """Normalize restored state and return the initial ready heap."""
        for node_id in node_ids:
            if state.node_statuses.get(node_id) == NodeStatus.RUNNING:
                state.mark(node_id, NodeStatus.PENDING)

        for node_id in sorted(node_ids):
            if state.is_terminal(node_id):
                for dep in dependents.get(node_id, []):
                    in_degree[dep] = max(0, in_degree[dep] - 1)

        ready: list[str] = []
        for node_id in sorted(node_ids):
            if state.node_statuses.get(node_id) == NodeStatus.PENDING and in_degree[node_id] == 0:
                heapq.heappush(ready, node_id)
        return ready

    async def _execute_ready_queue(
        self,
        graph: Graph,
        state: ExecutionState,
        context: ExecutionContext,
        *,
        node_ids: set[str] | None = None,
        on_node_finished: Callable[[str], Awaitable[None]] | None = None,
        before_dispatch: Callable[[list[str], list[str]], Awaitable[bool]] | None = None,
        on_progress: Callable[[list[str], list[str]], Awaitable[None]] | None = None,
        excluded_edge_ids: set[str] | None = None,
    ) -> None:
        """Execute acyclic graph regions eagerly as dependencies complete."""
        active_nodes = node_ids or {node.id for node in graph.nodes}
        in_degree, dependents = self._build_dependency_graph(
            graph,
            active_nodes,
            excluded_edge_ids=excluded_edge_ids,
        )
        ready = self._prepare_state_for_dispatch(state, in_degree, dependents, active_nodes)
        queued = set(ready)
        active: dict[str, asyncio.Task[None]] = {}

        while ready or active:
            if on_progress is not None:
                await on_progress(sorted(ready), sorted(active))
            while ready:
                if before_dispatch is not None:
                    should_continue = await before_dispatch(sorted(ready), sorted(active))
                    if not should_continue:
                        ready.clear()
                        break
                node_id = heapq.heappop(ready)
                queued.discard(node_id)
                if state.node_statuses.get(node_id) != NodeStatus.PENDING or node_id in active:
                    continue
                active[node_id] = asyncio.create_task(
                    self._guarded_execute_node(node_id, graph, state, context),
                )

            if not active:
                break

            done, _ = await asyncio.wait(
                set(active.values()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            finished = sorted(
                node_id for node_id, task in active.items() if task in done
            )

            for node_id in finished:
                task = active.pop(node_id)
                try:
                    await task
                except Exception as exc:
                    logger.exception("Scheduler task failed for node '%s'", node_id)
                    state.mark(node_id, NodeStatus.FAILED)
                    state.node_errors[node_id] = f"Scheduler task exception: {exc}"

                for dep in dependents.get(node_id, []):
                    in_degree[dep] = max(0, in_degree[dep] - 1)
                    if (
                        in_degree[dep] == 0
                        and state.node_statuses.get(dep) == NodeStatus.PENDING
                        and dep not in queued
                        and dep not in active
                    ):
                        heapq.heappush(ready, dep)
                        queued.add(dep)

                if on_node_finished is not None:
                    await on_node_finished(node_id)

                if self._check_halt(state):
                    for pending_task in active.values():
                        pending_task.cancel()
                    if active:
                        await asyncio.gather(*active.values(), return_exceptions=True)
                    return

    async def _execute(
        self,
        graph: Graph,
        state: ExecutionState,
        shared_context: SharedContextStore,
        artifacts: ArtifactStore,
        local_state: LocalStateManager,
        session_id: str | None = None,
        workflow_id: str | None = None,
        cost_tracker_state: dict | None = None,
        run_policy: RunPolicy | dict[str, Any] | None = None,
        persisted_run_policy: dict[str, Any] | None = None,
    ) -> RunResult:
        """Core scheduling loop: dispatch ready nodes, checkpoint, repeat."""
        run_start = _time.time()
        run_start_monotonic = _time.monotonic()
        memory_writes: list[MemoryWriteRequest] = []
        self._active_run_states[state.run_id] = state
        effective_run_policy = resolve_effective_run_policy(
            self.config,
            run_policy,
            persisted=persisted_run_policy,
        )
        state.run_state["effective_run_policy"] = effective_run_policy.snapshot()
        state.run_state["phase"] = RunPhase.ACTIVE.value
        if not state.run_state.get("resume_notes"):
            state.run_state["resume_notes"] = [
                "Subgraphs resume from their entry boundary.",
                "Dynamic child workflows replay from their invocation boundary.",
            ]

        short_term_mem = None
        if self.config.memory_pipeline_enabled:
            from dan.engine.memory_pipeline import ShortTermMemory
            short_term_mem = ShortTermMemory()

        await self._emit(EngineEvent(
            event_type=EventType.RUN_STARTED,
            run_id=state.run_id,
            data={
                "node_count": len(graph.nodes),
                "run_policy": effective_run_policy.snapshot(),
                "phase": RunPhase.ACTIVE.value,
            },
        ))

        # -- 15-1 + 17-3: Hyperedge resolver with self-evolving rules ----------
        hyperedge_resolver = None
        effective_hyperedges = list(graph.hyperedges)
        if getattr(self.config, "self_evolving_rules_enabled", False) and workflow_id:
            rlm = None
            try:
                from dan.engine.rule_generator import RuleLifecycleManager
                rlm = RuleLifecycleManager(
                    base_dir=getattr(self.config, "rules_dir", "./rules"),
                )
                generated = rlm.get_active_hyperedges(workflow_id)
                if generated:
                    effective_hyperedges = list(graph.hyperedges) + generated
                    logger.debug(
                        "Injected %d generated rules for workflow %s",
                        len(generated), workflow_id,
                    )
                    await self._emit(EngineEvent(
                        event_type=EventType.RULE_ACTIVATED,
                        run_id=state.run_id,
                        data={
                            "rule_count": len(generated),
                            "workflow_id": workflow_id,
                            "tier": "rules",
                        },
                    ))
            except Exception:
                logger.debug(
                    "Failed to load generated rules for workflow %s",
                    workflow_id, exc_info=True,
                )
            # -- 17-3 task 6-4: Apply parameter mutations --------------------------
            if rlm is not None:
                try:
                    mutations = rlm.get_active_mutations(workflow_id)
                    if mutations:
                        import copy
                        graph = self._apply_parameter_mutations(graph, mutations)
                        logger.debug(
                            "Applied %d parameter mutations for workflow %s",
                            len(mutations), workflow_id,
                        )
                except Exception:
                    logger.debug(
                        "Failed to apply parameter mutations for workflow %s",
                        workflow_id, exc_info=True,
                    )
        if effective_hyperedges:
            from dan.engine.hyperedge_runtime import HyperedgeResolver
            from dan.models.graph import Graph as _Graph
            augmented = graph.model_copy(update={"hyperedges": effective_hyperedges})
            hyperedge_resolver = HyperedgeResolver(augmented)

        # -- 15-3: Model selector & cost tracker --------------------------------
        from dan.providers.capabilities import ModelCapabilityRegistry
        from dan.providers.cost_tracker import CostTracker
        from dan.providers.model_selector import ModelSelector

        effective_run_budget = (
            effective_run_policy.max_cost
            if effective_run_policy.max_cost is not None
            else self.config.run_budget
        )
        budget_action = (
            "warn"
            if effective_run_policy.max_cost is not None
            else self.config.on_budget_exceeded
        )
        cost_tracker = CostTracker(
            run_budget=effective_run_budget,
            on_budget_exceeded=budget_action,
        )
        if cost_tracker_state is not None:
            cost_tracker.restore(cost_tracker_state)

        model_selector = ModelSelector(
            provider_registry=self.provider_registry,
            cost_tracker=cost_tracker,
            capability_registry=ModelCapabilityRegistry(),
        )
        node_result_cache = NodeResultCache(
            max_size_mb=getattr(self.config, "cache_max_size_mb", 100),
            cache_dir=getattr(self.config, "cache_dir", None),
            enabled=getattr(self.config, "cache_enabled", True),
        )
        semantic_cache = SemanticCache(
            embedding_registry=self.embedding_registry,
            embedding_model=getattr(self.config, "default_embedding_model", ""),
            threshold=getattr(self.config, "semantic_cache_threshold", 0.95),
            ttl_hours=getattr(self.config, "semantic_cache_ttl_hours", 24.0),
            enabled=getattr(self.config, "cache_enabled", True),
            cache_dir=getattr(self.config, "cache_dir", None),
        )
        budget_advisor = TokenBudgetAdvisor(
            total_budget=getattr(self.config, "token_budget", None),
            strategy="adaptive",
        )

        _run_token_budget = getattr(self.config, "token_budget", None)
        if _run_token_budget is not None:
            _node_count = len(graph.nodes)
            _per_node = max(1, _run_token_budget // max(1, _node_count))
            await self._emit(EngineEvent(
                event_type=EventType.BUDGET_ADVISORY,
                run_id=state.run_id,
                data={
                    "total_budget": _run_token_budget,
                    "node_count": _node_count,
                    "per_node_allocation": _per_node,
                    "phase": "run_start",
                    "advisory": True,
                },
            ))

        eager_dispatch = self._use_eager_dispatch()
        node_sem: asyncio.Semaphore | None = None
        if self.config.max_concurrency is not None and self.config.max_concurrency > 0:
            node_sem = asyncio.Semaphore(self.config.max_concurrency)
        llm_sem: asyncio.Semaphore | None = None
        llm_limit = self._llm_max_concurrency()
        if llm_limit is not None:
            llm_sem = asyncio.Semaphore(llm_limit)

        context = self._make_context(
            state, shared_context, artifacts, local_state, graph,
            session_id=session_id, memory_writes=memory_writes,
            short_term_memory=short_term_mem,
            hyperedge_resolver=hyperedge_resolver,
            model_selector=model_selector,
            cost_tracker=cost_tracker,
            tier_tracker=getattr(self.config, "tier_tracker", None),
            node_semaphore=node_sem,
            llm_semaphore=llm_sem,
            run_policy=effective_run_policy,
            runtime_repair_enabled=runtime_self_healing_enabled(self.config),
        )
        context._node_result_cache = node_result_cache
        context._semantic_cache = semantic_cache
        context._token_budget_advisor = budget_advisor

        # 17-1: Attach error context provider so LLMExecutor can inject
        # past-failure context into prompts.
        context.workflow_id = workflow_id or ""
        ecp = getattr(self, "error_context_provider", None)
        if ecp is not None:
            context._error_context_provider = ecp

        levels, back_edges, cycle_regions = _topological_levels_with_backedges(graph)

        checkpoint_task: asyncio.Task[None] | None = None
        completed_since_checkpoint = 0
        last_checkpoint_at = _time.monotonic()
        checkpoint_batch_size = max(1, int(effective_run_policy.checkpoint_batch_size))
        checkpoint_interval_sec = max(0.1, float(effective_run_policy.checkpoint_interval_sec))
        limit_stop_reason: StopReason = StopReason.NONE
        limit_stop_requested = False

        async def _emit_progress(
            ready_node_ids: list[str] | None = None,
            active_node_ids: list[str] | None = None,
        ) -> None:
            if not effective_run_policy.progress_enabled:
                return
            snapshot = progress_snapshot(
                graph,
                state,
                elapsed_seconds=_time.monotonic() - run_start_monotonic,
                ready_node_ids=ready_node_ids,
                active_node_ids=active_node_ids,
            )
            if not effective_run_policy.progress_stage_labels:
                snapshot["stage_label"] = ""
            if not effective_run_policy.progress_eta_enabled:
                snapshot["eta_seconds"] = None
            state.run_state["progress"] = snapshot
            if effective_run_policy.progress_emit_events:
                await self._emit(EngineEvent(
                    event_type=EventType.RUN_PROGRESS,
                    run_id=state.run_id,
                    data={
                        **snapshot,
                        "phase": state.run_state.get("phase", RunPhase.ACTIVE.value),
                    },
                ))

        async def _mark_limit_stop(reason: StopReason) -> None:
            nonlocal limit_stop_reason, limit_stop_requested
            if limit_stop_requested:
                return
            limit_stop_requested = True
            limit_stop_reason = reason
            state.run_state["phase"] = RunPhase.STOPPING_ON_LIMIT.value
            state.run_state["stop_reason"] = reason.value
            state.run_state["partial"] = True
            state.run_state["resumable"] = bool(self.checkpoint_store is not None)
            await self._emit(EngineEvent(
                event_type=EventType.RUN_LIMIT_REACHED,
                run_id=state.run_id,
                data={
                    "stop_reason": reason.value,
                    "elapsed_seconds": round(_time.monotonic() - run_start_monotonic, 3),
                    "total_cost": round(cost_tracker.total_cost(), 6),
                },
            ))
            await _emit_progress()
            await _maybe_checkpoint(reason.value, force=True)

        async def _check_run_limits() -> bool:
            if limit_stop_requested:
                return True
            max_duration = effective_run_policy.max_duration
            if max_duration is not None and (_time.monotonic() - run_start_monotonic) >= max_duration:
                await _mark_limit_stop(StopReason.DURATION_LIMIT)
                return True
            max_cost = effective_run_policy.max_cost
            if max_cost is not None and cost_tracker.total_cost() >= max_cost:
                await _mark_limit_stop(StopReason.COST_LIMIT)
                return True
            return False

        async def _await_checkpoint_task() -> None:
            nonlocal checkpoint_task
            if checkpoint_task is None:
                return
            try:
                await checkpoint_task
            except Exception:
                logger.warning("Background checkpoint save failed", exc_info=True)
            finally:
                checkpoint_task = None

        async def _maybe_checkpoint(trigger: str = "", *, force: bool = False) -> None:
            nonlocal checkpoint_task, completed_since_checkpoint, last_checkpoint_at
            if self.checkpoint_store is None:
                return
            if not force:
                completed_since_checkpoint += 1
                elapsed = _time.monotonic() - last_checkpoint_at
                if completed_since_checkpoint < checkpoint_batch_size and elapsed < checkpoint_interval_sec:
                    return
                trigger = "batch" if completed_since_checkpoint >= checkpoint_batch_size else "timer"

            await self._flush_memory_writes(
                context, workflow_id, session_id, state.run_id,
            )
            checkpoint = self._build_checkpoint_payload(
                state,
                shared_context,
                artifacts,
                local_state,
                cost_tracker=cost_tracker,
                graph=graph,
                graph_id=workflow_id or "",
                checkpoint_trigger=trigger,
            )
            await _await_checkpoint_task()
            checkpoint_task = asyncio.create_task(
                self._persist_checkpoint_payload(state.run_id, checkpoint),
            )
            completed_since_checkpoint = 0
            last_checkpoint_at = _time.monotonic()
            state.run_state["latest_checkpoint_trigger"] = trigger
            state.run_state["latest_checkpoint_timestamp"] = checkpoint.get("checkpoint_data", {}).get("timestamp")

        async def _before_dispatch(ready_node_ids: list[str], active_node_ids: list[str]) -> bool:
            await _emit_progress(ready_node_ids, active_node_ids)
            return not await _check_run_limits()

        async def _on_node_finished(node_id: str) -> None:
            node = graph.node_by_id(node_id)
            trigger = ""
            if node is not None and is_critical_checkpoint_node(node, effective_run_policy):
                trigger = "critical_node"
            elif getattr(node, "node_type", None) in {"human", "human_in_the_loop"}:
                trigger = "human_input"
            elif isinstance(state.node_metadata.get(node_id), dict) and state.node_metadata[node_id].get("halt"):
                trigger = "halt"

            if trigger:
                await _maybe_checkpoint(trigger, force=True)
            else:
                await _maybe_checkpoint(node_id, force=False)
            await _emit_progress()
            await _check_run_limits()

        if not back_edges:
            if eager_dispatch:
                await self._execute_ready_queue(
                    graph,
                    state,
                    context,
                    on_node_finished=_on_node_finished,
                    before_dispatch=_before_dispatch,
                    on_progress=_emit_progress,
                )
                if limit_stop_requested:
                    await _maybe_checkpoint(limit_stop_reason.value, force=True)
                elif self._check_halt(state):
                    await _maybe_checkpoint("halt", force=True)
                else:
                    await _maybe_checkpoint("run_end", force=True)
                await _await_checkpoint_task()
            else:
                for level in levels:
                    if await _check_run_limits():
                        break
                    ready = [
                        nid for nid in level
                        if state.node_statuses.get(nid) == NodeStatus.PENDING
                    ]
                    if not ready:
                        await _emit_progress()
                        continue

                    await _emit_progress(ready, [])
                    tasks = [
                        self._guarded_execute_node(nid, graph, state, context)
                        for nid in ready
                    ]
                    await asyncio.gather(*tasks)

                    await self._flush_memory_writes(
                        context, workflow_id, session_id, state.run_id,
                    )
                    if self.checkpoint_store is not None:
                        await self._save_checkpoint(
                            state, shared_context, artifacts, local_state,
                            cost_tracker=cost_tracker,
                            graph=graph, graph_id=workflow_id or "", checkpoint_trigger="level",
                        )

                    if await _check_run_limits():
                        if self.checkpoint_store is not None:
                            await self._save_checkpoint(
                                state, shared_context, artifacts, local_state,
                                cost_tracker=cost_tracker,
                                graph=graph,
                                graph_id=workflow_id or "",
                                checkpoint_trigger=limit_stop_reason.value,
                            )
                        break

                    if self._check_halt(state):
                        if self.checkpoint_store is not None:
                            await self._save_checkpoint(
                                state, shared_context, artifacts, local_state,
                                cost_tracker=cost_tracker,
                                graph=graph, graph_id=workflow_id or "", checkpoint_trigger="halt",
                            )
                        break
        else:
            await self._execute_with_cycles(
                graph, state, context, levels, back_edges, cycle_regions,
                shared_context, artifacts, local_state,
                cost_tracker=cost_tracker,
                graph_id=workflow_id or "",
                before_dispatch=_before_dispatch,
                on_node_finished=_on_node_finished,
                on_progress=_emit_progress,
                limit_checker=_check_run_limits,
            )

        await self._flush_memory_writes(
            context, workflow_id, session_id, state.run_id,
        )
        if limit_stop_requested:
            state.run_state["phase"] = (
                RunPhase.RESUMABLE.value
                if self.checkpoint_store is not None
                else RunPhase.PARTIAL.value
            )
            state.run_state["partial"] = True
            state.run_state["resumable"] = bool(self.checkpoint_store is not None)
            state.run_state["stop_reason"] = limit_stop_reason.value
            await _emit_progress()
            await _maybe_checkpoint(limit_stop_reason.value, force=True)
            await _await_checkpoint_task()
        elif self._check_halt(state):
            state.run_state["phase"] = RunPhase.PAUSED.value
            state.run_state["stop_reason"] = StopReason.HALT.value
            state.run_state["partial"] = True
            state.run_state["resumable"] = bool(self.checkpoint_store is not None)
            await _emit_progress()
        else:
            state.run_state["phase"] = (
                RunPhase.FAILED.value
                if any(s == NodeStatus.FAILED for s in state.node_statuses.values())
                else RunPhase.COMPLETED.value
            )
            await _emit_progress()
        result = self._build_result(graph, state)
        result.metadata["__run_cache__"] = {
            "memoization": node_result_cache.stats(),
            "semantic": semantic_cache.stats(),
            "provider": cost_tracker.cache_summary(),
        }
        node_breakdowns = cost_tracker.all_breakdowns()
        result.metadata["__cost_tracker__"] = {
            "node_breakdowns": node_breakdowns,
            "savings": cost_tracker.all_savings(),
            "savings_summary": cost_tracker.savings_summary(),
        }

        await self._emit_post_run_analytics(state.run_id, graph, node_breakdowns)

        if _run_token_budget is not None:
            _actual_total = sum(
                bd.get("total_input_tokens", 0) + bd.get("total_output_tokens", 0)
                for bd in node_breakdowns.values()
            )
            if _actual_total > _run_token_budget:
                logger.warning(
                    "Run %s exceeded advisory token budget: %d / %d",
                    state.run_id, _actual_total, _run_token_budget,
                )
                await self._emit(EngineEvent(
                    event_type=EventType.BUDGET_ADVISORY,
                    run_id=state.run_id,
                    data={
                        "total_budget": _run_token_budget,
                        "actual_tokens": _actual_total,
                        "exceeded": True,
                        "phase": "run_end",
                        "advisory": True,
                    },
                ))

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
                "phase": state.run_state.get("phase", RunPhase.ACTIVE.value),
                "stop_reason": state.run_state.get("stop_reason", ""),
                "partial": bool(state.run_state.get("partial", False)),
                "resumable": bool(state.run_state.get("resumable", False)),
                "cache_stats": result.metadata["__run_cache__"],
                **total_usage,
            },
        ))
        self._active_run_states.pop(state.run_id, None)
        return result

    async def _guarded_execute_node(
        self,
        node_id: str,
        graph: Graph,
        state: ExecutionState,
        context: ExecutionContext,
    ) -> None:
        """Run a node through the executor path."""
        await self._execute_node(node_id, graph, state, context)

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
        cost_tracker: Any | None = None,
        *,
        skip_checkpoint: bool = False,
        graph_id: str = "",
        before_dispatch: Callable[[list[str], list[str]], Awaitable[bool]] | None = None,
        on_node_finished: Callable[[str], Awaitable[None]] | None = None,
        on_progress: Callable[[list[str], list[str]], Awaitable[None]] | None = None,
        limit_checker: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        """Execute graph with cycle regions handled via bounded iteration."""
        if self._use_eager_dispatch():
            back_edge_ids = {
                edge.id
                for edge in graph.edges
                if (
                    isinstance(edge, DataEdge)
                    and edge.source_node_id in back_edges
                    and back_edges.get(edge.source_node_id) == edge.target_node_id
                    and edge.source_port in ("continue", "loop")
                )
            }
            cycle_gate_input_edge_ids = {
                edge.id
                for gate_id, nodes in cycle_regions.items()
                for edge in graph.edges
                if (
                    isinstance(edge, DataEdge)
                    and edge.target_node_id == gate_id
                    and edge.source_node_id in (nodes - {gate_id})
                )
            }
            blocked_cycle_nodes = {
                nid
                for gate_id, nodes in cycle_regions.items()
                for nid in nodes
                if nid != gate_id
            }
            in_degree, dependents = self._build_dependency_graph(
                graph,
                excluded_edge_ids=back_edge_ids | cycle_gate_input_edge_ids,
            )

            for node_id, status in list(state.node_statuses.items()):
                if status == NodeStatus.RUNNING:
                    state.mark(node_id, NodeStatus.PENDING)

            ready: list[str] = []
            queued: set[str] = set()
            cycle_resume_gates: list[str] = []
            for node_id in sorted(in_degree):
                if state.is_terminal(node_id):
                    gate_outputs = state.port_data.get_node_outputs(node_id)
                    if (
                        node_id in cycle_regions
                        and state.node_statuses.get(node_id) == NodeStatus.COMPLETED
                        and any(port in ("continue", "loop") for port in gate_outputs)
                    ):
                        cycle_resume_gates.append(node_id)
                        continue
                    for dep in dependents.get(node_id, []):
                        in_degree[dep] = max(0, in_degree[dep] - 1)
            for node_id in sorted(in_degree):
                if (
                    node_id not in blocked_cycle_nodes
                    and node_id not in cycle_resume_gates
                    and state.node_statuses.get(node_id) == NodeStatus.PENDING
                    and in_degree[node_id] == 0
                ):
                    heapq.heappush(ready, node_id)
                    queued.add(node_id)

            active: dict[str, asyncio.Task[None]] = {}
            cycle_tasks: dict[str, asyncio.Task[None]] = {}
            executed_gates: set[str] = set()

            async def _checkpoint(trigger: str) -> None:
                if skip_checkpoint or self.checkpoint_store is None:
                    return
                await self._save_checkpoint(
                    state,
                    shared_context,
                    artifacts,
                    local_state,
                    cost_tracker=cost_tracker,
                    graph=graph,
                    graph_id=graph_id,
                    checkpoint_trigger=trigger,
                )

            async def _mark_cycle_region_skipped(gate_id: str) -> None:
                for node_id in sorted(cycle_regions.get(gate_id, set()) - {gate_id}):
                    if state.node_statuses.get(node_id) != NodeStatus.PENDING:
                        continue
                    state.mark(node_id, NodeStatus.SKIPPED)
                    node = graph.node_by_id(node_id)
                    await self._emit(EngineEvent(
                        event_type=EventType.NODE_SKIPPED,
                        run_id=state.run_id,
                        node_id=node_id,
                        node_type=getattr(node, "node_type", None),
                    ))

            async def _run_cycle_task(gate_id: str, back_edge_target: str) -> None:
                await _checkpoint("cycle_boundary")
                await self._iterate_cycle(
                    graph,
                    state,
                    context,
                    gate_id,
                    cycle_regions[gate_id],
                    back_edge_target,
                    getattr(graph.node_by_id(gate_id), "max_iterations", 10),
                    levels,
                )
                await _checkpoint("cycle_boundary")

            for gate_id in cycle_resume_gates:
                if gate_id not in back_edges or gate_id in cycle_tasks:
                    continue
                cycle_tasks[gate_id] = asyncio.create_task(
                    _run_cycle_task(gate_id, back_edges[gate_id]),
                )

            while ready or active or cycle_tasks:
                if on_progress is not None:
                    await on_progress(sorted(ready), sorted(active))
                while ready:
                    if before_dispatch is not None:
                        should_continue = await before_dispatch(sorted(ready), sorted(active))
                        if not should_continue:
                            ready.clear()
                            break
                    node_id = heapq.heappop(ready)
                    queued.discard(node_id)
                    if state.node_statuses.get(node_id) != NodeStatus.PENDING or node_id in active:
                        continue
                    active[node_id] = asyncio.create_task(
                        self._guarded_execute_node(node_id, graph, state, context),
                    )

                if not active:
                    if not cycle_tasks:
                        break

                done, _ = await asyncio.wait(
                    set(active.values()) | set(cycle_tasks.values()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                finished = sorted(
                    node_id for node_id, task in active.items() if task in done
                )
                finished_cycles = sorted(
                    gate_id for gate_id, task in cycle_tasks.items() if task in done
                )

                for node_id in finished:
                    task = active.pop(node_id)
                    try:
                        await task
                    except Exception as exc:
                        logger.exception("Cycle-aware scheduler task failed for node '%s'", node_id)
                        state.mark(node_id, NodeStatus.FAILED)
                        state.node_errors[node_id] = f"Scheduler task exception: {exc}"

                    if node_id in cycle_regions and node_id not in executed_gates:
                        gate_outputs = state.port_data.get_node_outputs(node_id)
                        active_branch = None
                        for port_name in gate_outputs:
                            if port_name in ("continue", "loop"):
                                active_branch = port_name
                                break
                        back_edge_target = back_edges.get(node_id)
                        if active_branch is not None and back_edge_target:
                            cycle_tasks[node_id] = asyncio.create_task(
                                _run_cycle_task(node_id, back_edge_target),
                            )
                            continue
                        else:
                            await _mark_cycle_region_skipped(node_id)
                        executed_gates.add(node_id)

                    for dep in dependents.get(node_id, []):
                        in_degree[dep] = max(0, in_degree[dep] - 1)
                        if (
                            dep not in blocked_cycle_nodes
                            and in_degree[dep] == 0
                            and state.node_statuses.get(dep) == NodeStatus.PENDING
                            and dep not in queued
                            and dep not in active
                        ):
                            heapq.heappush(ready, dep)
                            queued.add(dep)

                    if on_node_finished is not None:
                        await on_node_finished(node_id)

                    if self._check_halt(state):
                        for pending_task in active.values():
                            pending_task.cancel()
                        for pending_task in cycle_tasks.values():
                            pending_task.cancel()
                        if active or cycle_tasks:
                            await asyncio.gather(
                                *active.values(),
                                *cycle_tasks.values(),
                                return_exceptions=True,
                            )
                        await _checkpoint("halt")
                        return

                for gate_id in finished_cycles:
                    task = cycle_tasks.pop(gate_id)
                    try:
                        await task
                    except Exception as exc:
                        logger.exception("Cycle iteration task failed for gate '%s'", gate_id)
                        state.mark(gate_id, NodeStatus.FAILED)
                        state.node_errors[gate_id] = f"Cycle iteration exception: {exc}"
                    executed_gates.add(gate_id)
                    for dep in dependents.get(gate_id, []):
                        in_degree[dep] = max(0, in_degree[dep] - 1)
                        if (
                            dep not in blocked_cycle_nodes
                            and in_degree[dep] == 0
                            and state.node_statuses.get(dep) == NodeStatus.PENDING
                            and dep not in queued
                            and dep not in active
                            and dep not in cycle_tasks
                        ):
                            heapq.heappush(ready, dep)
                            queued.add(dep)

                    if limit_checker is not None and await limit_checker():
                        for pending_task in active.values():
                            pending_task.cancel()
                        for pending_task in cycle_tasks.values():
                            pending_task.cancel()
                        if active or cycle_tasks:
                            await asyncio.gather(
                                *active.values(),
                                *cycle_tasks.values(),
                                return_exceptions=True,
                            )
                        await _checkpoint(state.run_state.get("stop_reason", "halt"))
                        return

                    if self._check_halt(state):
                        for pending_task in active.values():
                            pending_task.cancel()
                        for pending_task in cycle_tasks.values():
                            pending_task.cancel()
                        if active or cycle_tasks:
                            await asyncio.gather(
                                *active.values(),
                                *cycle_tasks.values(),
                                return_exceptions=True,
                            )
                        await _checkpoint("halt")
                        return

            await _checkpoint("level")
            return

        executed_gates: set[str] = set()

        for level in levels:
            if limit_checker is not None and await limit_checker():
                break
            ready = [
                nid for nid in level
                if state.node_statuses.get(nid) == NodeStatus.PENDING
            ]
            if not ready:
                if on_progress is not None:
                    await on_progress([], [])
                continue

            if on_progress is not None:
                await on_progress(ready, [])
            tasks = [
                self._guarded_execute_node(nid, graph, state, context)
                for nid in ready
            ]
            await asyncio.gather(*tasks)

            if on_node_finished is not None:
                for nid in ready:
                    await on_node_finished(nid)

            if self._check_halt(state):
                if not skip_checkpoint and self.checkpoint_store is not None:
                    await self._save_checkpoint(
                        state, shared_context, artifacts, local_state,
                            cost_tracker=cost_tracker,
                            graph=graph, graph_id=graph_id, checkpoint_trigger="halt",
                    )
                break

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
                        if not skip_checkpoint and self.checkpoint_store is not None:
                            await self._save_checkpoint(
                                state,
                                shared_context,
                                artifacts,
                                local_state,
                                cost_tracker=cost_tracker,
                                graph=graph,
                                graph_id=graph_id,
                                checkpoint_trigger="cycle_boundary",
                            )
                        await self._iterate_cycle(
                            graph, state, context, nid, cycle_nodes,
                            back_edge_target, max_iter, levels,
                        )
                        if not skip_checkpoint and self.checkpoint_store is not None:
                            await self._save_checkpoint(
                                state,
                                shared_context,
                                artifacts,
                                local_state,
                                cost_tracker=cost_tracker,
                                graph=graph,
                                graph_id=graph_id,
                                checkpoint_trigger="cycle_boundary",
                            )
                        executed_gates.add(nid)

            if limit_checker is not None and await limit_checker():
                if not skip_checkpoint and self.checkpoint_store is not None:
                    await self._save_checkpoint(
                        state, shared_context, artifacts, local_state,
                        cost_tracker=cost_tracker,
                        graph=graph, graph_id=graph_id, checkpoint_trigger=state.run_state.get("stop_reason", "halt"),
                    )
                break

            if not skip_checkpoint and self.checkpoint_store is not None:
                await self._save_checkpoint(
                    state, shared_context, artifacts, local_state,
                    cost_tracker=cost_tracker,
                    graph=graph, graph_id=graph_id, checkpoint_trigger="level",
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
        gate_node = graph.node_by_id(gate_id)
        back_edge_ids = {
            edge.id
            for edge in graph.edges
            if (
                isinstance(edge, DataEdge)
                and edge.source_node_id == gate_id
                and edge.target_node_id == back_edge_target
                and edge.source_port in ("continue", "loop")
            )
        }
        has_state_schema = (
            gate_node is not None
            and getattr(gate_node, 'state_schema', None) is not None
        )

        if has_state_schema:
            context.active_loop_scope_id = gate_id

        try:
            for iteration in range(1, max_iterations):
                if has_state_schema:
                    context.local_state.update_scope(gate_id, {"iteration": iteration})

                continue_data = state.port_data.get_node_outputs(gate_id).get("continue")
                if continue_data is None:
                    continue_data = state.port_data.get_node_outputs(gate_id).get("loop")

                _iter_tokens = estimate_tokens(
                    json.dumps(continue_data, default=str),
                ) if continue_data is not None else 0
                await self._emit(EngineEvent(
                    event_type=EventType.ITERATION_STARTED,
                    run_id=state.run_id,
                    node_id=gate_id,
                    node_type="gate",
                    data={
                        "iteration": iteration,
                        "max_iterations": max_iterations,
                        "context_tokens": _iter_tokens,
                    },
                ))

                # -- 16-4: artifact extraction & feedback filtering --------
                if isinstance(continue_data, dict):
                    _artifact_ports = getattr(gate_node, "artifact_ports", None)
                    if _artifact_ports:
                        _art_scope = context.local_state.get_scope(f"{gate_id}__artifacts")
                        _art_list = _art_scope.setdefault("items", [])
                        _iter_arts = {k: v for k, v in continue_data.items() if k in _artifact_ports}
                        if _iter_arts:
                            _art_list.append(_iter_arts)
                        continue_data = {k: v for k, v in continue_data.items() if k not in _artifact_ports}

                    _fb_sel = getattr(gate_node, "feedback_selector", None)
                    if _fb_sel is not None:
                        from dan.engine.conditions import apply_feedback_selector

                        continue_data = apply_feedback_selector(continue_data, _fb_sel)

                for cn in cycle_nodes:
                    state.port_data.clear_node(cn)
                    state.mark(cn, NodeStatus.PENDING)

                if isinstance(continue_data, dict):
                    for port_name, value in continue_data.items():
                        state.port_data.set(
                            f"__input__{back_edge_target}", port_name, value,
                        )

                if self._use_eager_dispatch():
                    await self._execute_ready_queue(
                        graph,
                        state,
                        context,
                        node_ids=cycle_nodes,
                        excluded_edge_ids=back_edge_ids,
                    )
                    if self._check_halt(state):
                        return
                else:
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
                            self._guarded_execute_node(nid, graph, state, context)
                            for nid in ready
                        ]
                        await asyncio.gather(*tasks)

                        if self._check_halt(state):
                            return

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
                    if has_state_schema:
                        final_scope = context.local_state.get_scope(gate_id)
                        for port_name, value in final_scope.items():
                            state.port_data.set(gate_id, port_name, value)
                    # -- 16-4: merge accumulated artifacts into gate output
                    _art_scope_id = f"{gate_id}__artifacts"
                    if context.local_state.has_scope(_art_scope_id):
                        _art_scope = context.local_state.get_scope(_art_scope_id)
                        _art_items = _art_scope.get("items", [])
                        if _art_items:
                            state.port_data.set(gate_id, "artifacts", _art_items)
                        context.local_state.delete_scope(_art_scope_id)
                    break
        finally:
            if has_state_schema:
                context.active_loop_scope_id = None

    @staticmethod
    def _materialize_node_for_execution(
        node: Any,
        state: ExecutionState,
        context: ExecutionContext,
    ) -> Any:
        """Apply run-policy defaults and pending overlays without mutating the graph."""

        updates: dict[str, Any] = {}
        run_policy = getattr(context, "run_policy", None)

        if run_policy is not None:
            if getattr(node, "retry_policy", None) is None and getattr(run_policy, "default_retry_policy", None) is not None:
                updates["retry_policy"] = run_policy.default_retry_policy.model_copy(deep=True)
            if hasattr(node, "failure_policy") and getattr(node, "failure_policy", None) is None and getattr(run_policy, "default_failure_policy", None) is not None:
                updates["failure_policy"] = copy.deepcopy(run_policy.default_failure_policy)
            if hasattr(node, "model_policy") and getattr(node, "model_policy", None) is None and getattr(run_policy, "default_model_policy", None) is not None:
                updates["model_policy"] = copy.deepcopy(run_policy.default_model_policy)

        overlay_patch = overlay_patch_for_node(state, getattr(node, "id", ""))
        if overlay_patch:
            for key, value in overlay_patch.items():
                updates[key] = copy.deepcopy(value)

        if not updates:
            return node
        return node.model_copy(update=updates, deep=True)

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
        node = self._materialize_node_for_execution(node, state, context)
        node_type_str = getattr(node, "node_type", node_type_str)

        if self._should_skip(node_id, graph, state):
            state.mark(node_id, NodeStatus.SKIPPED)
            await self._emit(EngineEvent(
                event_type=EventType.NODE_SKIPPED, run_id=state.run_id,
                node_id=node_id, node_type=node_type_str,
            ))
            return

        inputs = state.port_data.resolve_inputs(node_id, graph)

        for edge in graph.edges_to(node_id):
            if isinstance(edge, DataEdge):
                if not state.port_data.has(edge.source_node_id, edge.source_port):
                    source_node = graph.node_by_id(edge.source_node_id)
                    if source_node is not None and _is_gate_node(source_node):
                        continue
                    tgt_port = next(
                        (p for p in node.input_ports if p.name == edge.target_port),
                        None,
                    )
                    if tgt_port is not None and not tgt_port.required:
                        continue
                    await self._emit(EngineEvent(
                        event_type=EventType.DEAD_EDGE_WARNING,
                        run_id=state.run_id,
                        node_id=node_id,
                        node_type=node_type_str,
                        data={
                            "edge_id": edge.id,
                            "source_node_id": edge.source_node_id,
                            "source_port": edge.source_port,
                            "target_port": edge.target_port,
                            "message": (
                                f"Data edge '{edge.id}': source port "
                                f"'{edge.source_node_id}.{edge.source_port}' has no value"
                            ),
                        },
                    ))

        virtual_src = f"__input__{node_id}"
        # InputNode declares external variables in ``variables`` (not input_ports),
        # so seed those values explicitly from the virtual injected source.
        if getattr(node, "node_type", None) == "input":
            for var in getattr(node, "variables", []):
                var_name = getattr(var, "name", None)
                if var_name and state.port_data.has(virtual_src, var_name):
                    inputs[var_name] = state.port_data.get(virtual_src, var_name)
        for port in node.input_ports:
            if state.port_data.has(virtual_src, port.name):
                inputs[port.name] = state.port_data.get(virtual_src, port.name)

        self._read_context_edges(node_id, graph, context, inputs)
        self._resolve_input_references(inputs, context)

        if context.active_loop_scope_id:
            _gate = graph.node_by_id(context.active_loop_scope_id)
            if _gate and getattr(_gate, 'state_schema', None):
                _scope = context.local_state.get_scope(context.active_loop_scope_id)
                _defaults = getattr(_gate, 'state_defaults', None) or {}
                for _k in _gate.state_schema:
                    if _k not in inputs:
                        inputs[_k] = _scope.get(_k, _defaults.get(_k))

        state.mark(node_id, NodeStatus.RUNNING)
        _node_start_time = _time.time()
        await self._emit(EngineEvent(
            event_type=EventType.NODE_STARTED,
            run_id=state.run_id,
            node_id=node_id,
            node_type=node_type_str,
            data={
                "input_hash": _stable_input_hash(inputs),
                "input_port_count": len(inputs),
            },
        ))

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
        cache_key: str | None = None
        semantic_prompt: str | None = None
        advisory_tokens: int | None = None
        used_node_cache = False
        used_semantic_cache = False
        result: NodeResult

        node_cache = getattr(context, "_node_result_cache", None)
        semantic_cache = getattr(context, "_semantic_cache", None)
        budget_advisor = getattr(context, "_token_budget_advisor", None)

        if node_type_str == "llm_operator" and budget_advisor is not None:
            remaining = sum(
                1 for s in state.node_statuses.values()
                if s in (NodeStatus.PENDING, NodeStatus.RUNNING)
            )
            advisory_tokens = budget_advisor.compute_advisory(
                node_id=node_id,
                node_type=node_type_str,
                historical_usage=state.node_metadata.get(node_id, {}).get("usage", {}).get("prompt_tokens", 0)
                if isinstance(state.node_metadata.get(node_id, {}), dict)
                else None,
                priority=int(getattr(node, "metadata", {}).get("priority", 5) or 5)
                if isinstance(getattr(node, "metadata", {}), dict)
                else 5,
                remaining_nodes=max(1, remaining),
            )
            if advisory_tokens is not None and getattr(node, "target_input_tokens", None) is None:
                inputs["__advisory_target_tokens__"] = advisory_tokens
            if advisory_tokens is not None:
                await self._emit(EngineEvent(
                    event_type=EventType.BUDGET_ADVISORY,
                    run_id=state.run_id,
                    node_id=node_id,
                    node_type=node_type_str,
                    data={
                        "advisory_tokens": advisory_tokens,
                        "phase": "pre_execution",
                    },
                ))

        if (
            node_cache is not None
            and getattr(context.config, "cache_enabled", True)
            and bool(getattr(node, "memoize", False))
        ):
            policy_signature = (
                f"{getattr(context.config, 'hyperedge_enforcement', 'off')}:"
                f"{getattr(context.config, 'boundary_enforcement', False)}"
            )
            cache_key = node_cache.compute_cache_key(
                node, inputs, policy_signature=policy_signature,
            )
            cached_result, reason = node_cache.lookup(cache_key)
            if cached_result is not None:
                result = cached_result
                used_node_cache = True
                usage = (
                    result.metadata.get("usage", {})
                    if isinstance(result.metadata, dict)
                    else {}
                )
                saved_tokens = int(usage.get("total_tokens", 0) or 0)
                saved_cost = float(
                    result.metadata.get("cost", 0.0)
                    if isinstance(result.metadata, dict)
                    else 0.0
                )
                if (
                    context.cost_tracker is not None
                    and hasattr(context.cost_tracker, "record_cache_result")
                ):
                    context.cost_tracker.record_cache_result(
                        hit=True, semantic=False,
                        tokens_saved=saved_tokens, cost_saved=saved_cost,
                    )
                await self._emit(EngineEvent(
                    event_type=EventType.CACHE_HIT,
                    run_id=state.run_id,
                    node_id=node_id,
                    node_type=node_type_str,
                    data={
                        "cache_key_hash": cache_key,
                        "tokens_saved": saved_tokens,
                        "cost_saved": saved_cost,
                        "source": "memoization",
                    },
                ))
            else:
                if reason == "expired":
                    await self._emit(EngineEvent(
                        event_type=EventType.CACHE_INVALIDATED,
                        run_id=state.run_id,
                        node_id=node_id,
                        node_type=node_type_str,
                        data={"cache_key_hash": cache_key, "reason": "ttl_expired"},
                    ))
                if (
                    context.cost_tracker is not None
                    and hasattr(context.cost_tracker, "record_cache_result")
                ):
                    context.cost_tracker.record_cache_result(hit=False, semantic=False)
                await self._emit(EngineEvent(
                    event_type=EventType.CACHE_MISS,
                    run_id=state.run_id,
                    node_id=node_id,
                    node_type=node_type_str,
                    data={"cache_key_hash": cache_key, "reason": reason},
                ))

        if (
            not used_node_cache
            and semantic_cache is not None
            and getattr(context.config, "cache_enabled", True)
            and node_type_str == "llm_operator"
            and bool(getattr(node, "semantic_cache", False))
            and float(getattr(node, "temperature", 0.7)) == 0.0
        ):
            semantic_prompt = _render_template_for_cache(
                getattr(node, "prompt_template", ""), inputs,
            )
            semantic_hit = await semantic_cache.get(
                semantic_prompt,
                threshold=getattr(context.config, "semantic_cache_threshold", 0.95),
            )
            if semantic_hit is not None:
                result = semantic_hit
                used_semantic_cache = True
                usage = (
                    result.metadata.get("usage", {})
                    if isinstance(result.metadata, dict)
                    else {}
                )
                saved_tokens = int(usage.get("total_tokens", 0) or 0)
                saved_cost = float(
                    result.metadata.get("cost", 0.0)
                    if isinstance(result.metadata, dict)
                    else 0.0
                )
                if (
                    context.cost_tracker is not None
                    and hasattr(context.cost_tracker, "record_cache_result")
                ):
                    context.cost_tracker.record_cache_result(
                        hit=True, semantic=True,
                        tokens_saved=saved_tokens, cost_saved=saved_cost,
                    )
                await self._emit(EngineEvent(
                    event_type=EventType.SEMANTIC_CACHE_HIT,
                    run_id=state.run_id,
                    node_id=node_id,
                    node_type=node_type_str,
                    data={
                        "threshold": getattr(context.config, "semantic_cache_threshold", 0.95),
                        "tokens_saved": saved_tokens,
                        "cost_saved": saved_cost,
                    },
                ))
            else:
                if (
                    context.cost_tracker is not None
                    and hasattr(context.cost_tracker, "record_cache_result")
                ):
                    context.cost_tracker.record_cache_result(hit=False, semantic=True)

        if not used_node_cache and not used_semantic_cache:
            try:
                if node_type_str in _NODE_SLOT_BYPASS_TYPES:
                    result = await executor.execute(node, inputs, context)
                else:
                    async with context.node_slot():
                        result = await executor.execute(node, inputs, context)
            except Exception as exc:
                logger.exception("Executor raised for node '%s'", node_id)
                result = NodeResult(
                    outputs={},
                    status=NodeStatus.FAILED,
                    error=f"Executor exception: {exc}",
                )

        # -- 15-1: Hyperedge post-output & validation hooks --------------------
        if (
            result.status == NodeStatus.COMPLETED
            and getattr(context, "hyperedge_resolver", None)
            and getattr(context.config, "hyperedge_enforcement", "off") != "off"
        ):
            try:
                result, violations = context.hyperedge_resolver.apply_post_output(
                    node, result, context.config.hyperedge_enforcement,
                )
                for v in violations:
                    if not v.passed:
                        await self._emit(EngineEvent(
                            event_type=EventType.HYPEREDGE_VIOLATION,
                            run_id=state.run_id,
                            node_id=node_id,
                            node_type=node_type_str,
                            data={"hyperedge_id": v.hyperedge_id, "message": v.message},
                        ))
                val_results = context.hyperedge_resolver.apply_validation(
                    node, result.outputs, context.config.hyperedge_enforcement,
                )
                for v in val_results:
                    if not v.passed:
                        await self._emit(EngineEvent(
                            event_type=EventType.HYPEREDGE_VIOLATION,
                            run_id=state.run_id,
                            node_id=node_id,
                            node_type=node_type_str,
                            data={"hyperedge_id": v.hyperedge_id, "message": v.message},
                        ))
            except Exception as he_exc:
                from dan.models.hyperedges import HyperedgeViolation
                if isinstance(he_exc, HyperedgeViolation):
                    result = NodeResult(
                        outputs={},
                        status=NodeStatus.FAILED,
                        error=str(he_exc),
                    )
                else:
                    logger.warning(
                        "Hyperedge hook error on node '%s': %s", node_id, he_exc,
                    )

        if (
            node_cache is not None
            and cache_key is not None
            and not used_node_cache
            and result.status == NodeStatus.COMPLETED
            and bool(getattr(node, "memoize", False))
            and getattr(context.config, "cache_enabled", True)
        ):
            node_cache.put(
                cache_key,
                result,
                ttl=getattr(node, "cache_ttl", None),
            )

        if (
            semantic_cache is not None
            and semantic_prompt is not None
            and not used_semantic_cache
            and result.status == NodeStatus.COMPLETED
            and node_type_str == "llm_operator"
            and bool(getattr(node, "semantic_cache", False))
            and float(getattr(node, "temperature", 0.7)) == 0.0
            and getattr(context.config, "cache_enabled", True)
        ):
            await semantic_cache.put(
                semantic_prompt,
                result,
                model=getattr(node, "model", ""),
                ttl_hours=getattr(context.config, "semantic_cache_ttl_hours", 24.0),
            )

        if node_type_str == "llm_operator" and budget_advisor is not None:
            usage = (
                result.metadata.get("usage", {})
                if isinstance(result.metadata, dict)
                else {}
            )
            actual_prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
            budget_advisor.record_actual(node_id, actual_prompt_tokens)
            if advisory_tokens is not None:
                await self._emit(EngineEvent(
                    event_type=EventType.BUDGET_ADVISORY,
                    run_id=state.run_id,
                    node_id=node_id,
                    node_type=node_type_str,
                    data={
                        "advisory_tokens": advisory_tokens,
                        "actual_tokens": actual_prompt_tokens,
                        "phase": "post_execution",
                    },
                ))

        if not isinstance(result.metadata, dict):
            result.metadata = {}
        result.metadata.setdefault("elapsed_seconds", round(_time.time() - _node_start_time, 3))

        if getattr(context, "state_store", None) is not None:
            usage = (
                result.metadata.get("usage", {})
                if isinstance(result.metadata, dict)
                else {}
            )
            _node_elapsed = _time.time() - _node_start_time
            summary = NodeExecutionSummary(
                node_id=node_id,
                node_type=node_type_str or "",
                status=result.status.value,
                started_at=_node_start_time,
                elapsed_seconds=round(_node_elapsed, 3),
                duration_ms=round(_node_elapsed * 1000, 1),
                input_tokens=int(usage.get("prompt_tokens", 0) or 0),
                output_tokens=int(usage.get("completion_tokens", 0) or 0),
                cost=float(result.metadata.get("cost", 0.0) if isinstance(result.metadata, dict) else 0.0),
                output_keys=list(result.outputs.keys()),
                output_preview=str(result.outputs)[:200],
                error=result.error,
            )
            _state_key = f"node:{node_id}:summary"
            try:
                await context.state_store.write(
                    state.run_id, _state_key, summary,
                )
                await self._emit(EngineEvent(
                    event_type=EventType.STATE_EXTERNALIZED,
                    run_id=state.run_id,
                    node_id=node_id,
                    node_type=node_type_str,
                    data={
                        "scope": state.run_id,
                        "keys_written": [_state_key],
                        "tokens_saved": 0,
                    },
                ))
            except Exception:
                logger.debug("Failed to externalize node state for %s", node_id, exc_info=True)

        if result.status == NodeStatus.FAILED and getattr(context, "runtime_repair_enabled", False):
            error_message = result.error or "runtime node failure"
            category = classify_runtime_failure(node, error_message, result.metadata)
            signature = failure_signature(node, category, error_message)
            error_record = build_runtime_error_record(
                RuntimeFailureContext(
                    run_id=state.run_id,
                    workflow_id=context.workflow_id,
                    node_id=node_id,
                    node_type=node_type_str or "",
                    category=category,
                    failure_signature=signature,
                    error_message=error_message,
                    metadata=result.metadata or {},
                ),
                input_snapshot=dict(inputs) if isinstance(inputs, dict) else None,
            )
            failure_context = RuntimeFailureContext(
                run_id=state.run_id,
                workflow_id=context.workflow_id,
                node_id=node_id,
                node_type=node_type_str or "",
                category=category,
                failure_signature=signature,
                error_message=error_message,
                metadata={
                    **(result.metadata or {}),
                    "checkpoint_rerun_available": bool(self.checkpoint_store is not None),
                },
                error_record=error_record,
            )
            plan = plan_runtime_repair(state, node, failure_context)
            if plan.kind != RuntimeRepairKind.NONE:
                await self._emit(EngineEvent(
                    event_type=EventType.NODE_REPAIR_STARTED,
                    run_id=state.run_id,
                    node_id=node_id,
                    node_type=node_type_str,
                    data={
                        "category": category.value,
                        "repair_kind": plan.kind.value,
                        "failure_signature": signature,
                    },
                ))

            if plan.overlay is not None:
                apply_pending_overlay(state, plan.overlay)
                await self._emit(EngineEvent(
                    event_type=EventType.NODE_OVERLAY_APPLIED,
                    run_id=state.run_id,
                    node_id=node_id,
                    node_type=node_type_str,
                    data={
                        "overlay": plan.overlay.model_dump(),
                        "category": category.value,
                    },
                ))

            attempt = RuntimeRepairAttempt(
                kind=plan.kind,
                category=category,
                failure_signature=signature,
                outcome="applied" if plan.retry_current_node else "skipped",
                reason=error_message,
                cause=plan.summary.get("cause", category.value),
                next_step=plan.summary.get("next_step", ""),
                user_visible_message=plan.summary.get("user_visible_message", ""),
                post_run_repair_level=plan.summary.get("post_run_repair_level", ""),
                overlay=plan.overlay,
                automatic_recovery=plan.summary.get("automatic_recovery", {}) or {},
                diagnostic_record=error_record,
            )
            record_repair_attempt(state, node_id, attempt)

            if plan.retry_current_node and plan.kind != RuntimeRepairKind.ADVISORY:
                await self._emit(EngineEvent(
                    event_type=EventType.NODE_REPAIR_APPLIED,
                    run_id=state.run_id,
                    node_id=node_id,
                    node_type=node_type_str,
                    data={
                        "category": category.value,
                        "repair_kind": plan.kind.value,
                        "summary": plan.summary,
                    },
                ))
                state.mark(node_id, NodeStatus.PENDING)
                state.node_errors.pop(node_id, None)
                if plan.retry_delay_sec > 0:
                    await asyncio.sleep(plan.retry_delay_sec)
                return await self._execute_node(node_id, graph, state, context)

            clear_pending_overlays(state, node_id)
            await self._emit(EngineEvent(
                event_type=(
                    EventType.NODE_REPAIR_FAILED
                    if plan.kind != RuntimeRepairKind.NONE
                    else EventType.NODE_REPAIR_SKIPPED
                ),
                run_id=state.run_id,
                node_id=node_id,
                node_type=node_type_str,
                data={
                    "category": category.value,
                    "repair_kind": plan.kind.value,
                    "summary": plan.summary,
                },
            ))

        if result.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED):
            clear_pending_overlays(state, node_id)

        if not isinstance(result.metadata, dict):
            result.metadata = {}
        result.metadata["runtime_repair_summary"] = repair_summary_for_node(state, node_id)

        state.mark(node_id, result.status)
        if result.error:
            state.node_errors[node_id] = result.error
        if result.metadata:
            state.node_metadata[node_id] = result.metadata

        for port_name, value in result.outputs.items():
            state.port_data.set(node_id, port_name, value)

        if context.active_loop_scope_id:
            _gate = graph.node_by_id(context.active_loop_scope_id)
            if _gate and getattr(_gate, 'state_schema', None):
                _updates = {k: v for k, v in result.outputs.items() if k in _gate.state_schema}
                if _updates:
                    context.local_state.update_scope(context.active_loop_scope_id, _updates)

        # -- 18-1: Encode-to-memory pattern ------------------------------------
        encode_threshold = getattr(context.config, "encode_to_memory_threshold_tokens", None)
        if encode_threshold is not None and context.short_term_memory is not None:
            import json
            for port_name, value in result.outputs.items():
                check_val = value
                if isinstance(value, (dict, list)):
                    try:
                        check_val = json.dumps(value)
                    except TypeError:
                        continue
                
                if isinstance(check_val, str):
                    token_count = estimate_tokens(check_val)
                    if token_count > encode_threshold:
                        preview = check_val[:500] + f"... [truncated, total {token_count} tokens]"
                        context.remember(
                            content=f"Large artifact on port '{port_name}': {preview}",
                            source_node_id=node_id,
                            metadata={
                                "entry_type": "artifact_summary",
                                "port": port_name,
                                "token_count": token_count,
                            }
                        )

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

            if node_type_str == "llm_operator" and context.cost_tracker is not None:
                bd = context.cost_tracker.get_breakdown(node_id)
                if bd is not None:
                    await self._emit(EngineEvent(
                        event_type=EventType.TOKEN_BREAKDOWN_RECORDED,
                        run_id=state.run_id,
                        node_id=node_id,
                        node_type=node_type_str,
                        data=bd.to_dict(),
                    ))

    def _should_skip(
        self, node_id: str, graph: Graph, state: ExecutionState
    ) -> bool:
        """Skip a node if it's on an inactive branch.

        Handles both legacy ControlEdge-based branching and newer gate
        branch-port routing where the inactive port has no data.
        """
        node = graph.node_by_id(node_id)
        allow_dead_inputs = bool(
            getattr(node, "metadata", {}).get("allow_dead_inputs", False)
        )
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
            if source_node is not None and _is_gate_node(source_node):
                # While-gate continue/loop edges need special handling:
                # - Initial pass: body must wait until gate emits continue/loop.
                # - Iteration pass: scheduler injects loop feedback into virtual
                #   inputs (__input__<node_id>), so body should run even though
                #   gate outputs were cleared for the next iteration.
                gate_mode = getattr(source_node, "gate_mode", None)
                if gate_mode == "while" and edge.source_port in ("continue", "loop"):
                    virtual_src = f"__input__{node_id}"
                    if state.port_data.has(virtual_src, edge.target_port):
                        continue
            if not state.port_data.has(edge.source_node_id, edge.source_port):
                if source_node is None or _is_gate_node(source_node):
                    return True
                target_port = next(
                    (
                        port
                        for port in getattr(node, "input_ports", [])
                        if port.name == edge.target_port
                    ),
                    None,
                )
                if target_port is not None and not target_port.required:
                    continue
                if allow_dead_inputs:
                    continue
                source_status = state.node_statuses.get(edge.source_node_id)
                if source_status in (NodeStatus.FAILED, NodeStatus.SKIPPED):
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
    def _resolve_input_references(
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> None:
        """Resolve reference dicts (__ref__) in inputs from ArtifactStore."""
        for key in list(inputs):
            inputs[key] = resolve_reference(inputs[key], context.artifacts)

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
            value_to_write = value
            if edge.pass_by_reference:
                threshold = getattr(
                    context.config,
                    "pass_by_reference_threshold_tokens",
                    2000,
                )
                token_count = estimate_tokens(str(value))
                if token_count > threshold:
                    key = (
                        f"ref:{getattr(context.state, 'run_id', 'run')}:"
                        f"{node_id}:{edge.source_port}:{int(_time.time() * 1000)}"
                    )
                    try:
                        value_to_write = create_reference(
                            key, value, context.artifacts, preview_chars=200
                        )
                    except Exception:
                        value_to_write = value
            try:
                if edge.mode.value == "write":
                    context.shared_context.write(edge.context_key, value_to_write)
                elif edge.mode.value == "append":
                    context.shared_context.append(edge.context_key, value_to_write)
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
        session_id: str | None = None,
        memory_writes: list[MemoryWriteRequest] | None = None,
        short_term_memory: Any | None = None,
        hyperedge_resolver: Any | None = None,
        model_selector: Any | None = None,
        cost_tracker: Any | None = None,
        tier_tracker: Any | None = None,
        node_semaphore: asyncio.Semaphore | None = None,
        llm_semaphore: asyncio.Semaphore | None = None,
        run_policy: EffectiveRunPolicy | None = None,
        runtime_repair_enabled: bool = False,
    ) -> ExecutionContext:
        tool_registry = None
        if self.executor_registry.has("tool_operator"):
            tool_exec = self.executor_registry.get("tool_operator")
            tool_registry = getattr(tool_exec, "registry", None)

        async def run_subgraph(
            sub_graph_key: str,
            inputs: dict[str, Any],
            parent_node_id: str | None = None,
            targeted_inputs: dict[str, dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
            child_layer = layer_path + ((parent_node_id,) if parent_node_id else ())
            bc = None
            if parent_node_id is not None:
                parent_node = graph.node_by_id(parent_node_id)
                if parent_node is not None:
                    bc = getattr(parent_node, "boundary_contract", None)
            if dynamic_topology_enabled(self.config) and parent_node_id is not None and targeted_inputs is None:
                envelope = await run_child_workflow(
                    {"mode": "sub_graph", "ref": sub_graph_key},
                    inputs,
                    parent_node_id=parent_node_id,
                    source="engine",
                    boundary_contract=bc,
                )
                return dict(getattr(envelope, "outputs", {}) or {})
            return await self._run_subgraph(
                sub_graph_key, inputs, graph, state,
                shared_context, artifacts, local_state,
                child_layer, targeted_inputs,
                boundary_contract=bc,
                session_id=session_id,
                memory_writes=memory_writes,
                short_term_memory=short_term_memory,
                parent_hyperedge_resolver=hyperedge_resolver,
                parent_node_id=parent_node_id,
                model_selector=model_selector,
                cost_tracker=cost_tracker,
                tier_tracker=tier_tracker,
                parent_node_semaphore=node_semaphore,
                parent_llm_semaphore=llm_semaphore,
            )

        async def run_child_workflow(
            spec: Any,
            inputs: dict[str, Any],
            *,
            parent_node_id: str,
            source: str = "engine",
            boundary_contract: Any | None = None,
        ) -> Any:
            from dan.models.control_flow import ChildWorkflowCall, DynamicExpansionSpec

            if isinstance(spec, str):
                spec = DynamicExpansionSpec(ref=spec)
            elif isinstance(spec, dict):
                spec = DynamicExpansionSpec(**spec)

            if not dynamic_topology_enabled(self.config):
                raise RuntimeCompositionError(
                    "Dynamic topology is disabled. "
                    "Enable config.dynamic_topology_enabled or DAN_DYNAMIC_TOPOLOGY=1."
                )

            if spec.mode not in {"sub_graph", "template_branch", "workflow_ref"}:
                raise RuntimeCompositionError(
                    "Dynamic expansion mode "
                    f"'{spec.mode}' is not supported by the engine runtime yet. "
                    "Supported modes: ['sub_graph', 'template_branch', 'workflow_ref']."
                )

            call = ChildWorkflowCall(
                spec=spec,
                inputs=dict(inputs),
                parent_node_id=parent_node_id,
                call_id=stable_invocation_key(parent_node_id, spec.mode, spec.ref, inputs)[:12],
                source=source,
            )
            return await invoke_child_workflow(
                call=call,
                state=state,
                parent_run_id=state.run_id,
                layer_path=layer_path,
                boundary_contract=boundary_contract,
                emit_event=lambda event_type, data: self._emit(EngineEvent(
                    event_type=EventType(event_type),
                    run_id=state.run_id,
                    node_id=parent_node_id,
                    data=data,
                )),
                run_child=lambda ref, payload, **kwargs: self._run_child_overlay_graph(
                    ref=ref,
                    mode=kwargs.get("mode", spec.mode),
                    parent_graph=graph,
                    parent_state=state,
                    inputs=payload,
                    shared_context=shared_context,
                    artifacts=artifacts,
                    local_state=local_state,
                    layer_path=layer_path + ((parent_node_id,) if parent_node_id else ()),
                    boundary_contract=kwargs.get("boundary_contract"),
                    session_id=session_id,
                    memory_writes=memory_writes,
                    short_term_memory=short_term_memory,
                    parent_hyperedge_resolver=hyperedge_resolver,
                    parent_node_id=parent_node_id,
                    model_selector=model_selector,
                    cost_tracker=cost_tracker,
                    tier_tracker=tier_tracker,
                    parent_node_semaphore=node_semaphore,
                    parent_llm_semaphore=llm_semaphore,
                    child_run_id=kwargs.get("child_run_id"),
                ),
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
            provider_registry=self.provider_registry,
            model_gateway=self.model_gateway,
            tool_registry=tool_registry,
            embedding_registry=self.embedding_registry,
            state_store=self.state_store,
            session_id=session_id,
            memory_writes=memory_writes,
            short_term_memory=short_term_memory,
            hyperedge_resolver=hyperedge_resolver,
            model_selector=model_selector,
            cost_tracker=cost_tracker,
            human_renderer=self.human_renderer,
            graph=graph,
            tier_tracker=tier_tracker,
            node_semaphore=node_semaphore,
            llm_semaphore=llm_semaphore,
            run_policy=run_policy,
            runtime_repair_enabled=runtime_repair_enabled,
            run_child_workflow=run_child_workflow,
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
        boundary_contract: Any | None = None,
        session_id: str | None = None,
        memory_writes: list[MemoryWriteRequest] | None = None,
        short_term_memory: Any | None = None,
        parent_hyperedge_resolver: Any | None = None,
        parent_node_id: str | None = None,
        model_selector: Any | None = None,
        cost_tracker: Any | None = None,
        tier_tracker: Any | None = None,
        parent_node_semaphore: asyncio.Semaphore | None = None,
        parent_llm_semaphore: asyncio.Semaphore | None = None,
        child_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a named sub-graph and return its outputs."""
        max_subgraph_depth = int(getattr(self.config, "max_subgraph_depth", 32))
        if max_subgraph_depth > 0 and len(layer_path) > max_subgraph_depth:
            raise RuntimeError(
                f"Subgraph nesting depth {len(layer_path)} exceeds configured limit "
                f"{max_subgraph_depth}"
            )

        sub_graph = parent_graph.sub_graphs.get(sub_graph_key)
        if sub_graph is None and sub_graph_key.startswith("block:"):
            block_ref = sub_graph_key[len("block:"):]
            registry = self.config.block_registry
            if registry is not None:
                try:
                    from dan.blocks.executor import load_block_as_graph
                    sub_graph = load_block_as_graph(block_ref, registry)
                except ValueError as exc:
                    raise RuntimeError(f"Block '{block_ref}' not found: {exc}") from exc
        if sub_graph is None:
            raise RuntimeError(f"Sub-graph '{sub_graph_key}' not found")

        if self.config.boundary_enforcement and boundary_contract is not None:
            child_context_store: SharedContextStore = ScopedContextView(
                shared_context,
                sub_graph.shared_context,
                reads_global=boundary_contract.reads_global,
                writes_global=boundary_contract.writes_global,
            )
        else:
            child_context_store = shared_context

        # -- 15-1: Propagate hyperedges into child graph -----------------------
        child_resolver = None
        if parent_hyperedge_resolver is not None or sub_graph.hyperedges:
            from dan.engine.hyperedge_runtime import HyperedgeResolver
            parent_hes = (
                parent_hyperedge_resolver.active_hyperedges
                if parent_hyperedge_resolver is not None
                else None
            )
            child_resolver = HyperedgeResolver(
                sub_graph, parent_hyperedges=parent_hes,
                parent_scope_node_id=parent_node_id,
            )

        sub_state = ExecutionState(sub_graph, run_id=parent_state.run_id)
        sub_context = self._make_context(
            sub_state, child_context_store, artifacts, local_state, sub_graph, layer_path,
            session_id=session_id,
            memory_writes=memory_writes,
            short_term_memory=short_term_memory,
            hyperedge_resolver=child_resolver,
            model_selector=model_selector,
            cost_tracker=cost_tracker,
            tier_tracker=tier_tracker,
            node_semaphore=parent_node_semaphore,
            llm_semaphore=parent_llm_semaphore,
        )

        if inputs:
            for entry_id in sub_graph.entry_points:
                entry_node = sub_graph.node_by_id(entry_id)
                if entry_node is not None:
                    if getattr(entry_node, "node_type", None) == "input":
                        for var in getattr(entry_node, "variables", []):
                            var_name = getattr(var, "name", None)
                            if var_name and var_name in inputs:
                                sub_state.port_data.set(
                                    f"__input__{entry_id}", var_name, inputs[var_name]
                                )
                    for port in entry_node.input_ports:
                        if port.name in inputs:
                            sub_state.port_data.set(
                                f"__input__{entry_id}", port.name, inputs[port.name]
                            )

        if targeted_inputs:
            for node_id, port_values in targeted_inputs.items():
                for port_name, value in port_values.items():
                    sub_state.port_data.set(f"__input__{node_id}", port_name, value)

        levels, back_edges, cycle_regions = _topological_levels_with_backedges(sub_graph)

        if not back_edges:
            if self._use_eager_dispatch():
                await self._execute_ready_queue(
                    sub_graph,
                    sub_state,
                    sub_context,
                )
            else:
                for level in levels:
                    ready = [
                        nid for nid in level
                        if sub_state.node_statuses.get(nid) == NodeStatus.PENDING
                    ]
                    if not ready:
                        continue

                    tasks = [
                        self._guarded_execute_node(nid, sub_graph, sub_state, sub_context)
                        for nid in ready
                    ]
                    await asyncio.gather(*tasks)

                    if self._check_halt(sub_state):
                        break
        else:
            await self._execute_with_cycles(
                sub_graph, sub_state, sub_context, levels, back_edges, cycle_regions,
                shared_context, artifacts, local_state,
                cost_tracker=cost_tracker,
                skip_checkpoint=True, graph_id="",
            )

        if isinstance(child_context_store, ScopedContextView):
            child_context_store.propagate_to_parent()

        outputs: dict[str, Any] = {}
        for exit_id in sub_graph.exit_points:
            outputs.update(sub_state.port_data.get_node_outputs(exit_id))

        return outputs

    async def _run_child_overlay_graph(
        self,
        *,
        ref: str,
        mode: str,
        parent_graph: Graph,
        parent_state: ExecutionState,
        inputs: dict[str, Any],
        shared_context: SharedContextStore,
        artifacts: ArtifactStore,
        local_state: LocalStateManager,
        layer_path: tuple[str, ...] = (),
        boundary_contract: Any | None = None,
        session_id: str | None = None,
        memory_writes: list[MemoryWriteRequest] | None = None,
        short_term_memory: Any | None = None,
        parent_hyperedge_resolver: Any | None = None,
        parent_node_id: str | None = None,
        model_selector: Any | None = None,
        cost_tracker: Any | None = None,
        tier_tracker: Any | None = None,
        parent_node_semaphore: asyncio.Semaphore | None = None,
        parent_llm_semaphore: asyncio.Semaphore | None = None,
        child_run_id: str | None = None,
    ) -> dict[str, Any]:
        if mode in {"sub_graph", "template_branch"}:
            return await self._run_subgraph(
                ref,
                inputs,
                parent_graph,
                parent_state,
                shared_context,
                artifacts,
                local_state,
                layer_path,
                None,
                boundary_contract=boundary_contract,
                session_id=session_id,
                memory_writes=memory_writes,
                short_term_memory=short_term_memory,
                parent_hyperedge_resolver=parent_hyperedge_resolver,
                parent_node_id=parent_node_id,
                model_selector=model_selector,
                cost_tracker=cost_tracker,
                tier_tracker=tier_tracker,
                parent_node_semaphore=parent_node_semaphore,
                parent_llm_semaphore=parent_llm_semaphore,
                child_run_id=child_run_id,
            )

        if mode != "workflow_ref":
            raise RuntimeCompositionError(f"Unsupported dynamic child mode '{mode}'")

        loader = self.workflow_loader
        if loader is None:
            raise RuntimeCompositionError(
                "workflow_ref requires an engine workflow_loader, but none is configured."
            )

        raw_graph = loader(ref)
        if raw_graph is None:
            raise RuntimeCompositionError(
                f"workflow_ref '{ref}' could not be resolved by the engine workflow_loader."
            )
        if isinstance(raw_graph, Graph):
            child_graph = raw_graph
        else:
            try:
                child_graph = Graph.model_validate(raw_graph)
            except Exception as exc:
                raise RuntimeCompositionError(
                    f"workflow_ref '{ref}' did not resolve to a valid graph: {exc}"
                ) from exc

        return await self._run_graph_overlay(
            sub_graph=child_graph,
            inputs=inputs,
            parent_state=parent_state,
            shared_context=shared_context,
            artifacts=artifacts,
            local_state=local_state,
            layer_path=layer_path,
            targeted_inputs=None,
            boundary_contract=boundary_contract,
            session_id=session_id,
            memory_writes=memory_writes,
            short_term_memory=short_term_memory,
            parent_hyperedge_resolver=parent_hyperedge_resolver,
            parent_node_id=parent_node_id,
            model_selector=model_selector,
            cost_tracker=cost_tracker,
            tier_tracker=tier_tracker,
            parent_node_semaphore=parent_node_semaphore,
            parent_llm_semaphore=parent_llm_semaphore,
            child_run_id=child_run_id,
            workflow_id=ref,
        )

    async def _run_graph_overlay(
        self,
        *,
        sub_graph: Graph,
        inputs: dict[str, Any],
        parent_state: ExecutionState,
        shared_context: SharedContextStore,
        artifacts: ArtifactStore,
        local_state: LocalStateManager,
        layer_path: tuple[str, ...] = (),
        targeted_inputs: dict[str, dict[str, Any]] | None = None,
        boundary_contract: Any | None = None,
        session_id: str | None = None,
        memory_writes: list[MemoryWriteRequest] | None = None,
        short_term_memory: Any | None = None,
        parent_hyperedge_resolver: Any | None = None,
        parent_node_id: str | None = None,
        model_selector: Any | None = None,
        cost_tracker: Any | None = None,
        tier_tracker: Any | None = None,
        parent_node_semaphore: asyncio.Semaphore | None = None,
        parent_llm_semaphore: asyncio.Semaphore | None = None,
        child_run_id: str | None = None,
        workflow_id: str | None = None,
    ) -> dict[str, Any]:
        max_subgraph_depth = int(getattr(self.config, "max_subgraph_depth", 32))
        if max_subgraph_depth > 0 and len(layer_path) > max_subgraph_depth:
            raise RuntimeError(
                f"Subgraph nesting depth {len(layer_path)} exceeds configured limit "
                f"{max_subgraph_depth}"
            )

        if self.config.boundary_enforcement and boundary_contract is not None:
            child_context_store: SharedContextStore = ScopedContextView(
                shared_context,
                sub_graph.shared_context,
                reads_global=boundary_contract.reads_global,
                writes_global=boundary_contract.writes_global,
            )
        else:
            child_context_store = shared_context

        child_resolver = None
        if parent_hyperedge_resolver is not None or sub_graph.hyperedges:
            from dan.engine.hyperedge_runtime import HyperedgeResolver

            parent_hes = (
                parent_hyperedge_resolver.active_hyperedges
                if parent_hyperedge_resolver is not None
                else None
            )
            child_resolver = HyperedgeResolver(
                sub_graph,
                parent_hyperedges=parent_hes,
                parent_scope_node_id=parent_node_id,
            )

        sub_state = ExecutionState(sub_graph, run_id=parent_state.run_id)
        sub_context = self._make_context(
            sub_state,
            child_context_store,
            artifacts,
            local_state,
            sub_graph,
            layer_path,
            session_id=session_id,
            memory_writes=memory_writes,
            short_term_memory=short_term_memory,
            hyperedge_resolver=child_resolver,
            model_selector=model_selector,
            cost_tracker=cost_tracker,
            tier_tracker=tier_tracker,
            node_semaphore=parent_node_semaphore,
            llm_semaphore=parent_llm_semaphore,
        )
        sub_context.workflow_id = workflow_id or ""

        if inputs:
            for entry_id in sub_graph.entry_points:
                entry_node = sub_graph.node_by_id(entry_id)
                if entry_node is not None:
                    if getattr(entry_node, "node_type", None) == "input":
                        for var in getattr(entry_node, "variables", []):
                            var_name = getattr(var, "name", None)
                            if var_name and var_name in inputs:
                                sub_state.port_data.set(
                                    f"__input__{entry_id}",
                                    var_name,
                                    inputs[var_name],
                                )
                    for port in entry_node.input_ports:
                        if port.name in inputs:
                            sub_state.port_data.set(
                                f"__input__{entry_id}",
                                port.name,
                                inputs[port.name],
                            )

        if targeted_inputs:
            for node_id, port_values in targeted_inputs.items():
                for port_name, value in port_values.items():
                    sub_state.port_data.set(f"__input__{node_id}", port_name, value)

        levels, back_edges, cycle_regions = _topological_levels_with_backedges(sub_graph)

        if not back_edges:
            if self._use_eager_dispatch():
                await self._execute_ready_queue(sub_graph, sub_state, sub_context)
            else:
                for level in levels:
                    ready = [
                        nid for nid in level
                        if sub_state.node_statuses.get(nid) == NodeStatus.PENDING
                    ]
                    if not ready:
                        continue
                    tasks = [
                        self._guarded_execute_node(nid, sub_graph, sub_state, sub_context)
                        for nid in ready
                    ]
                    await asyncio.gather(*tasks)
                    if self._check_halt(sub_state):
                        break
        else:
            await self._execute_with_cycles(
                sub_graph,
                sub_state,
                sub_context,
                levels,
                back_edges,
                cycle_regions,
                shared_context,
                artifacts,
                local_state,
                cost_tracker=cost_tracker,
                skip_checkpoint=True,
                graph_id=workflow_id or "",
            )

        if isinstance(child_context_store, ScopedContextView):
            child_context_store.propagate_to_parent()

        outputs: dict[str, Any] = {}
        for exit_id in sub_graph.exit_points:
            outputs.update(sub_state.port_data.get_node_outputs(exit_id))
        return outputs

    async def _preload_memory(
        self,
        shared_context: SharedContextStore,
        workflow_id: str,
        session_id: str,
    ) -> None:
        """Load prior session memory entries into SharedContextStore.

        If the raw key is declared in the graph's shared context it is written
        there directly so context edges can read it normally.  Additionally,
        every entry is injected under ``memory:<key>`` for explicit access.
        """
        try:
            entries = await self.memory_store.read_all(workflow_id, session_id)
            for key, entry in entries.items():
                if key in shared_context.declared_keys:
                    shared_context.write(key, entry.value)
                prefixed = f"memory:{key}"
                if prefixed in shared_context.declared_keys:
                    shared_context.write(prefixed, entry.value)
                else:
                    shared_context._store[prefixed] = entry.value
        except Exception:
            logger.debug(
                "Memory pre-load failed for %s/%s", workflow_id, session_id,
                exc_info=True,
            )

    def _checkpoint_batch_size(self) -> int:
        raw = os.getenv("DAN_CHECKPOINT_BATCH_SIZE")
        if raw is not None and raw.strip():
            try:
                value = int(raw)
                return max(1, value)
            except ValueError:
                logger.warning("Ignoring invalid DAN_CHECKPOINT_BATCH_SIZE=%r", raw)
        return max(1, int(getattr(self.config, "checkpoint_batch_size", 5)))

    def _checkpoint_interval_sec(self) -> float:
        raw = os.getenv("DAN_CHECKPOINT_INTERVAL_SEC")
        if raw is not None and raw.strip():
            try:
                value = float(raw)
                return max(0.1, value)
            except ValueError:
                logger.warning("Ignoring invalid DAN_CHECKPOINT_INTERVAL_SEC=%r", raw)
        return max(0.1, float(getattr(self.config, "checkpoint_interval_sec", 10.0)))

    def _build_checkpoint_payload(
        self,
        state: ExecutionState,
        shared_context: SharedContextStore,
        artifacts: ArtifactStore,
        local_state: LocalStateManager,
        cost_tracker: Any | None = None,
        *,
        graph: Graph | None = None,
        graph_id: str = "",
        checkpoint_trigger: str = "",
    ) -> dict[str, Any]:
        checkpoint: dict[str, Any] = {
            "state": state.snapshot(),
            "shared_context": shared_context.snapshot(),
            "artifacts": artifacts.snapshot(),
            "local_state": local_state.snapshot(),
        }
        if cost_tracker is not None:
            checkpoint["cost_tracker"] = cost_tracker.snapshot()

        from dan.engine.checkpoint import CheckpointData, compute_graph_revision_hash

        completed_ids = [
            nid for nid, s in state.node_statuses.items()
            if s == NodeStatus.COMPLETED
        ]
        node_outputs: dict[str, Any] = {}
        for nid in completed_ids:
            outputs = state.port_data.get_node_outputs(nid)
            if outputs:
                node_outputs[nid] = outputs

        pending_ids = [
            nid for nid, s in state.node_statuses.items()
            if s in (NodeStatus.RUNNING, NodeStatus.PENDING, NodeStatus.WAITING)
        ]
        remaining_ids = [
            nid for nid, s in state.node_statuses.items()
            if s not in (NodeStatus.COMPLETED, NodeStatus.SKIPPED)
        ]

        graph_rev: str | None = None
        if graph is not None:
            try:
                graph_rev = compute_graph_revision_hash(graph)
            except Exception:
                pass

        checkpoint_data = CheckpointData(
            run_id=state.run_id,
            graph_id=graph_id,
            graph_revision=graph_rev,
            completed_node_ids=completed_ids,
            node_outputs=node_outputs,
            pending_node_ids=pending_ids,
            checkpoint_trigger=checkpoint_trigger,
            effective_run_policy=state.run_state.get("effective_run_policy"),
            stop_reason=state.run_state.get("stop_reason", ""),
            partial=bool(state.run_state.get("partial", False)),
            resumable=bool(state.run_state.get("resumable", False)),
            remaining_node_ids=remaining_ids,
            progress=dict(state.run_state.get("progress", {})),
        )
        checkpoint["checkpoint_data"] = checkpoint_data.model_dump()
        return checkpoint

    async def _persist_checkpoint_payload(
        self,
        run_id: str,
        checkpoint: dict[str, Any],
    ) -> None:
        if self.checkpoint_store is None:
            return
        await self.checkpoint_store.save(run_id, checkpoint)

    async def _flush_memory_writes(
        self,
        context: ExecutionContext,
        workflow_id: str | None,
        session_id: str | None,
        run_id: str,
    ) -> None:
        """Persist any queued memory writes from executors.

        Scope routing:
          - GLOBAL  → workflow_id="_global", session_id="_global"
          - WORKFLOW → workflow_id=<wf>,     session_id="_default"
          - SESSION  → workflow_id=<wf>,     session_id=<sess>  (requires both)
        """
        writes = context.drain_memory_writes()
        if not writes:
            return
        deferred_writes: list[MemoryWriteRequest] = []
        for req in writes:
            owner_node_id = req.owner_node_id or req.writer_node_id
            if (
                owner_node_id
                and owner_node_id in context.state.node_statuses
            ):
                owner_status = context.state.node_statuses[owner_node_id]
                if owner_status == NodeStatus.COMPLETED:
                    pass
                elif owner_status in (NodeStatus.FAILED, NodeStatus.SKIPPED):
                    logger.warning(
                        "Dropping memory write for key '%s' from non-completed owner '%s' (%s)",
                        req.key,
                        owner_node_id,
                        owner_status.value,
                    )
                    continue
                else:
                    deferred_writes.append(req)
                    continue

            if req.scope == MemoryScope.GLOBAL:
                target_wf, target_sess = "_global", "_global"
            elif req.scope == MemoryScope.WORKFLOW:
                if not workflow_id:
                    logger.warning("Dropping WORKFLOW-scope write (no workflow_id): %s", req.key)
                    continue
                target_wf, target_sess = workflow_id, "_default"
            else:
                if not workflow_id or not session_id:
                    logger.warning("Dropping SESSION-scope write (no session): %s", req.key)
                    continue
                target_wf, target_sess = workflow_id, session_id

            entry = MemoryEntry(
                key=req.key,
                value=req.value,
                scope=req.scope,
                source_run_id=run_id,
                writer_node_id=req.writer_node_id,
                owner_node_id=req.owner_node_id,
                write_mode=req.mode,
            )
            try:
                await self.memory_store.write(target_wf, target_sess, entry)
            except Exception:
                logger.warning(
                    "Memory write failed for key '%s'", req.key, exc_info=True,
                )
        context.restore_memory_writes(deferred_writes)

    async def _save_checkpoint(
        self,
        state: ExecutionState,
        shared_context: SharedContextStore,
        artifacts: ArtifactStore,
        local_state: LocalStateManager,
        cost_tracker: Any | None = None,
        *,
        graph: Graph | None = None,
        graph_id: str = "",
        checkpoint_trigger: str = "",
    ) -> None:
        if self.checkpoint_store is None:
            return
        checkpoint = self._build_checkpoint_payload(
            state,
            shared_context,
            artifacts,
            local_state,
            cost_tracker=cost_tracker,
            graph=graph,
            graph_id=graph_id,
            checkpoint_trigger=checkpoint_trigger,
        )
        await self._persist_checkpoint_payload(state.run_id, checkpoint)

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
        partial = bool(state.run_state.get("partial", False))
        completed_ids = [
            nid for nid, s in state.node_statuses.items()
            if s == NodeStatus.COMPLETED
        ]
        pending_ids = [
            nid for nid, s in state.node_statuses.items()
            if s in (NodeStatus.PENDING, NodeStatus.RUNNING, NodeStatus.WAITING)
        ]
        remaining_ids = [
            nid for nid, s in state.node_statuses.items()
            if s not in (NodeStatus.COMPLETED, NodeStatus.SKIPPED)
        ]
        metadata = dict(state.node_metadata)
        metadata.update({
            "run_phase": state.run_state.get("phase", RunPhase.ACTIVE.value),
            "stop_reason": state.run_state.get("stop_reason", ""),
            "partial": partial,
            "resumable": bool(state.run_state.get("resumable", False)),
            "completed_node_ids": completed_ids,
            "pending_node_ids": pending_ids,
            "remaining_node_ids": remaining_ids,
            "latest_checkpoint_trigger": state.run_state.get("latest_checkpoint_trigger", ""),
            "latest_checkpoint_timestamp": state.run_state.get("latest_checkpoint_timestamp"),
            "progress": dict(state.run_state.get("progress", {})),
            "effective_run_policy": state.run_state.get("effective_run_policy"),
            "resume_notes": list(state.run_state.get("resume_notes", [])),
            "repair_lineage": dict(state.run_state.get("repair_lineage", {})),
            "pending_overlays": dict(state.run_state.get("pending_overlays", {})),
            "dynamic_topology": dict(state.run_state.get("dynamic_topology", {})),
            "automatic_recovery": select_automatic_recovery_candidate(graph, state),
        })

        return RunResult(
            run_id=state.run_id,
            outputs=outputs,
            success=not has_failures,
            node_statuses={nid: s.value for nid, s in state.node_statuses.items()},
            errors=dict(state.node_errors),
            metadata=metadata,
        )
