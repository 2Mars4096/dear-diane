"""Node executor protocol, execution context, and executor registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol, runtime_checkable

from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.state import ExecutionState, NodeStatus
from dan.models.nodes import NodeBase

if TYPE_CHECKING:
    from dan.providers import ProviderConfig
    from dan.providers.registry import ProviderRegistry


@dataclass
class EngineConfig:
    """Top-level configuration for the execution engine."""

    llm_base_url: str = "https://api.vectorengine.ai/v1"
    llm_api_key: str = ""
    llm_default_model: str = "claude-sonnet-4-6"
    checkpoint_dir: str = "./checkpoints"
    checkpoint_enabled: bool = True
    output_norm_max_retries: int = 3
    max_concurrency: int | None = None
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    model_provider_map: dict[str, str] = field(default_factory=dict)


@dataclass
class NodeResult:
    """Result of executing a single node."""

    outputs: dict[str, Any] = field(default_factory=dict)
    status: NodeStatus = NodeStatus.COMPLETED
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ExecutionContext:
    """Wraps runtime state and config, exposed to executors.

    Provides scoped access to shared context, artifacts, local state,
    and engine configuration. Executors use this instead of touching
    ExecutionState directly.
    """

    def __init__(
        self,
        state: ExecutionState,
        config: EngineConfig,
        shared_context: SharedContextStore,
        artifacts: ArtifactStore,
        local_state: LocalStateManager,
        human_input_callback: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        run_subgraph: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        # -- 5-3: Rich logging --------------------------------------------------
        event_callback: Callable[[Any], Awaitable[None]] | None = None,
        run_id: str = "",
        layer_path: tuple[str, ...] = (),
        # -- 7-2: Multi-provider LLM registry -----------------------------------
        provider_registry: ProviderRegistry | None = None,
    ) -> None:
        self.state = state
        self.config = config
        self.shared_context = shared_context
        self.artifacts = artifacts
        self.local_state = local_state
        self.human_input_callback = human_input_callback
        self._run_subgraph = run_subgraph
        self._event_callback = event_callback
        self._run_id = run_id
        self.layer_path = layer_path
        self.provider_registry = provider_registry

    # -- 5-3: Rich logging -----------------------------------------------------
    async def emit_event(
        self,
        event_type: str,
        node_id: str,
        node_type: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Emit a structured event to the event callback."""
        if self._event_callback is None:
            return
        from dan.engine.events import EngineEvent, EventType
        enriched = dict(data or {})
        if self.layer_path:
            enriched["layer_path"] = list(self.layer_path)
        event = EngineEvent(
            event_type=EventType(event_type),
            run_id=self._run_id,
            node_id=node_id,
            node_type=node_type,
            data=enriched,
        )
        try:
            await self._event_callback(event)
        except Exception:
            pass

    async def run_subgraph(
        self,
        sub_graph_key: str,
        inputs: dict[str, Any],
        parent_node_id: str | None = None,
        targeted_inputs: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Execute a named sub-graph and return its outputs.

        Delegates to the engine's internal sub-graph runner, which handles
        scoped state, recursive scheduling, and checkpoint integration.
        *parent_node_id* is threaded into the child layer_path for event
        disambiguation.
        """
        if self._run_subgraph is None:
            raise RuntimeError("Sub-graph execution not available in this context")
        return await self._run_subgraph(sub_graph_key, inputs, parent_node_id, targeted_inputs)


@runtime_checkable
class NodeExecutor(Protocol):
    """Protocol that all node executors must satisfy."""

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult: ...


class ExecutorRegistry:
    """Maps node_type strings to NodeExecutor instances.

    The engine looks up the executor for each node's node_type and
    dispatches execution to it.
    """

    def __init__(self) -> None:
        self._executors: dict[str, NodeExecutor] = {}

    def register(self, node_type: str, executor: NodeExecutor) -> None:
        self._executors[node_type] = executor

    def get(self, node_type: str) -> NodeExecutor:
        try:
            return self._executors[node_type]
        except KeyError:
            raise KeyError(
                f"No executor registered for node type '{node_type}'. "
                f"Registered: {sorted(self._executors)}"
            )

    def has(self, node_type: str) -> bool:
        return node_type in self._executors

    def registered_types(self) -> list[str]:
        return sorted(self._executors)
