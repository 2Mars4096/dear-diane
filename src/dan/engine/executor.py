"""Node executor protocol, execution context, and executor registry."""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal, Protocol, runtime_checkable

from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.state import ExecutionState, NodeStatus
from dan.models.nodes import NodeBase

if TYPE_CHECKING:
    from dan.providers import ProviderConfig
    from dan.providers.registry import ProviderRegistry
    from dan.rag import EmbeddingRegistry


# ---------------------------------------------------------------------------
# Human rendering surface protocol (Plan 16-3)
# ---------------------------------------------------------------------------


@dataclass
class HumanRenderRequest:
    """Everything a rendering surface needs to present a human interaction."""

    request_id: str = field(default_factory=lambda: str(_uuid.uuid4()))
    node_id: str = ""
    node_name: str = ""
    render_mode: str = "text"
    prompt: str = ""
    instructions: str = ""
    input_data: dict[str, Any] = field(default_factory=dict)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    options: list[str] | None = None
    timeout_seconds: float | None = None
    default_action: str | None = None
    render_target: str = "dialog"


@dataclass
class HumanRenderResponse:
    """The human's validated response (or a default/timeout fallback)."""

    request_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    source: Literal["human", "default", "timeout"] = "human"


@runtime_checkable
class HumanRenderer(Protocol):
    """Contract for any rendering surface (web UI, CLI, Jupyter, API)."""

    async def render(self, request: HumanRenderRequest) -> HumanRenderResponse: ...


class LegacyCallbackRenderer:
    """Wraps an old-style ``Callable[[dict], Awaitable[dict]]`` as a ``HumanRenderer``."""

    def __init__(self, callback: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]) -> None:
        self._callback = callback

    async def render(self, request: HumanRenderRequest) -> HumanRenderResponse:
        meta = {
            "node_id": request.node_id,
            "prompt": request.prompt,
            "request_id": request.request_id,
        }
        raw = await self._callback(meta)
        data = raw if isinstance(raw, dict) else {"response": raw}
        return HumanRenderResponse(request_id=request.request_id, data=data, source="human")


class AutoRenderer:
    """Returns ``default_action`` when set; fails otherwise. For headless/background runs."""

    async def render(self, request: HumanRenderRequest) -> HumanRenderResponse:
        if request.default_action is not None:
            return HumanRenderResponse(
                request_id=request.request_id,
                data={"response": request.default_action},
                source="default",
            )
        raise RuntimeError(
            f"AutoRenderer: no default_action for node '{request.node_id}'"
        )


class ProgrammaticRenderer:
    """Returns pre-scripted responses keyed by ``node_id``. For automated testing."""

    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self._responses = responses

    async def render(self, request: HumanRenderRequest) -> HumanRenderResponse:
        if request.node_id in self._responses:
            return HumanRenderResponse(
                request_id=request.request_id,
                data=self._responses[request.node_id],
                source="human",
            )
        if request.default_action is not None:
            return HumanRenderResponse(
                request_id=request.request_id,
                data={"response": request.default_action},
                source="default",
            )
        raise KeyError(
            f"ProgrammaticRenderer: no scripted response for node '{request.node_id}'"
        )


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
    embedding_providers: dict[str, ProviderConfig] = field(default_factory=dict)
    embedding_model_provider_map: dict[str, str] = field(default_factory=dict)
    default_embedding_model: str = "text-embedding-3-small"
    # -- 14-1: Session memory ------------------------------------------------
    memory_dir: str = "./memory"
    memory_enabled: bool = True
    # -- 14-2: Context scoping -----------------------------------------------
    boundary_enforcement: bool = False
    # -- 14-3: Long-chain memory ---------------------------------------------
    memory_pipeline_enabled: bool = False
    # -- 15-1: Hyperedge runtime & cost tracking ------------------------------
    hyperedge_enforcement: str = "warn"  # "off" | "warn" | "strict"
    run_budget: float | None = None
    default_model_policy: Any | None = None
    on_budget_exceeded: str = "warn"  # "switch" | "warn" | "halt"


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
        # -- 9-1: Embedding provider registry -----------------------------------
        embedding_registry: EmbeddingRegistry | None = None,
        # -- 14-1: Session memory -----------------------------------------------
        session_id: str | None = None,
        memory_writes: list | None = None,
        # -- 14-3: Long-chain memory pipeline -----------------------------------
        short_term_memory: Any | None = None,
        # -- 15-1: Hyperedge runtime & cost tracking ----------------------------
        hyperedge_resolver: Any | None = None,
        model_selector: Any | None = None,
        cost_tracker: Any | None = None,
        # -- 16-3: Human rendering surface --------------------------------------
        human_renderer: HumanRenderer | None = None,
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
        self.embedding_registry = embedding_registry
        self.active_loop_scope_id: str | None = None
        self.session_id = session_id
        self._memory_writes: list = memory_writes if memory_writes is not None else []
        self.short_term_memory = short_term_memory
        self.hyperedge_resolver = hyperedge_resolver
        self.model_selector = model_selector
        self.cost_tracker = cost_tracker
        # -- 16-3: Auto-wrap legacy callback into renderer protocol
        if human_renderer is not None:
            self.human_renderer: HumanRenderer | None = human_renderer
        elif human_input_callback is not None:
            self.human_renderer = LegacyCallbackRenderer(human_input_callback)
        else:
            self.human_renderer = None

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

    # -- 14-3: Long-chain memory pipeline ------------------------------------
    def remember(
        self,
        content: str,
        *,
        source_node_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append an item to the short-term memory buffer.

        No-op when the memory pipeline is disabled.
        """
        if self.short_term_memory is None:
            return
        from dan.engine.memory_pipeline import MemoryItem

        self.short_term_memory.append(
            MemoryItem(
                content=content,
                source_node_id=source_node_id,
                source_run_id=self._run_id,
                metadata=metadata or {},
            )
        )

    def recall(self, n: int | None = None) -> str:
        """Retrieve recent short-term memory as concatenated text.

        Returns empty string when the memory pipeline is disabled.
        """
        if self.short_term_memory is None:
            return ""
        return self.short_term_memory.to_text(n)

    # -- 14-2: Context scoping — boundary signals ----------------------------
    def emit_signal(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
        *,
        sticky: bool = False,
    ) -> None:
        """Emit an upward signal from a child agent.

        Sticky signals are written to ``signal:<name>`` in shared context
        (propagates to global) and emitted as engine events.
        Non-sticky signals are stored in the local context under
        ``__signals__`` for consumption by the immediate parent only.
        """
        signal_key = f"signal:{name}"
        signal_data = payload or {}
        if sticky:
            try:
                self.shared_context.write(signal_key, signal_data)
            except KeyError:
                self.shared_context._store[signal_key] = signal_data

            if self._event_callback is not None:
                import asyncio
                from dan.engine.events import EngineEvent, EventType

                event = EngineEvent(
                    event_type=EventType.NODE_OUTPUT,
                    run_id=self._run_id,
                    node_id="__signal__",
                    data={
                        "signal_name": name,
                        "payload": signal_data,
                        "sticky": True,
                    },
                )
                try:
                    asyncio.get_event_loop().create_task(self._event_callback(event))
                except RuntimeError:
                    pass
        else:
            scope = self.local_state.get_scope("__signals__")
            scope.setdefault("pending", []).append({
                "signal_name": name,
                "payload": signal_data,
            })

    # -- 14-1: Session memory -------------------------------------------------
    def write_memory(
        self,
        key: str,
        value: Any,
        *,
        scope: str = "session",
        mode: str = "set",
        writer_node_id: str | None = None,
    ) -> None:
        """Queue a memory write for persistence at the next checkpoint.

        Writes are validated and flushed by the engine at checkpoint
        boundaries, not applied inline.  This keeps execution
        deterministic and avoids partial-write issues on failure.
        """
        from dan.engine.memory import MemoryScope, MemoryWriteRequest, WriteMode

        self._memory_writes.append(
            MemoryWriteRequest(
                key=key,
                value=value,
                scope=MemoryScope(scope),
                mode=WriteMode(mode),
                writer_node_id=writer_node_id,
            )
        )

    def drain_memory_writes(self) -> list:
        """Return and clear all queued memory writes."""
        writes = list(self._memory_writes)
        self._memory_writes.clear()
        return writes

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
