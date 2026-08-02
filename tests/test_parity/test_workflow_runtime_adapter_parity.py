from __future__ import annotations

from types import SimpleNamespace

from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.graph import Graph
from dan.publish.runtime import LocalRuntime
from dan.server.run_manager import RunManager
from dan.workflow_runtime import (
    ArtifactStore,
    Engine,
    EngineConfig,
    ExecutionState,
    ExecutorRegistry,
    LocalStateManager,
    SharedContextStore,
    register_default_executors,
)


def _minimal_engine(
    monkeypatch,
    *,
    executor_registry: ExecutorRegistry | None = None,
) -> Engine:
    monkeypatch.setattr(Engine, "_build_provider_registry", lambda self: SimpleNamespace())
    monkeypatch.setattr(Engine, "_build_embedding_registry", lambda self: SimpleNamespace())
    return Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            memory_enabled=False,
            state_store_enabled=False,
        ),
        executor_registry=executor_registry,
    )


def test_engine_and_run_manager_style_registry_compose_same_executor_surface(
    monkeypatch,
) -> None:
    default_engine = _minimal_engine(monkeypatch)

    manager = object.__new__(RunManager)
    manager._tool_registry = ToolRegistry()
    prewired_registry = RunManager._make_executor_registry(manager)
    tool_executor = prewired_registry.get("tool_operator")

    composed_engine = _minimal_engine(
        monkeypatch,
        executor_registry=prewired_registry,
    )

    assert set(default_engine.executor_registry.registered_types()) == set(
        composed_engine.executor_registry.registered_types()
    )
    assert composed_engine.executor_registry.get("tool_operator") is tool_executor


def test_default_executor_registration_preserves_prewired_tool_executor() -> None:
    registry = ExecutorRegistry()
    custom_tool_executor = ToolExecutor(ToolRegistry())
    registry.register("tool_operator", custom_tool_executor)

    register_default_executors(registry)

    assert registry.get("tool_operator") is custom_tool_executor


def test_engine_builds_model_gateway_from_provider_registry(monkeypatch) -> None:
    registry = SimpleNamespace(resolve=lambda _model: object())
    monkeypatch.setattr(Engine, "_build_provider_registry", lambda self: registry)
    monkeypatch.setattr(Engine, "_build_embedding_registry", lambda self: SimpleNamespace())

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            memory_enabled=False,
            state_store_enabled=False,
        )
    )

    assert engine.model_gateway is not None
    assert getattr(engine.model_gateway, "_registry", None) is registry


def test_engine_uses_injected_provider_registry_without_building_one(monkeypatch) -> None:
    calls = {"provider_registry": 0}
    injected_registry = SimpleNamespace(resolve=lambda _model: object())

    def _unexpected_registry_builder(self) -> SimpleNamespace:
        calls["provider_registry"] += 1
        return SimpleNamespace()

    monkeypatch.setattr(Engine, "_build_provider_registry", _unexpected_registry_builder)
    monkeypatch.setattr(Engine, "_build_embedding_registry", lambda self: SimpleNamespace())

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            memory_enabled=False,
            state_store_enabled=False,
        ),
        provider_registry=injected_registry,
    )

    assert engine.provider_registry is injected_registry
    assert calls["provider_registry"] == 0
    assert getattr(engine.model_gateway, "_registry", None) is injected_registry


def test_engine_preserves_injected_model_gateway(monkeypatch) -> None:
    registry = SimpleNamespace(resolve=lambda _model: object())
    injected_gateway = object()

    monkeypatch.setattr(Engine, "_build_embedding_registry", lambda self: SimpleNamespace())

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            memory_enabled=False,
            state_store_enabled=False,
        ),
        provider_registry=registry,
        model_gateway=injected_gateway,
    )

    assert engine.provider_registry is registry
    assert engine.model_gateway is injected_gateway


def test_engine_make_context_threads_model_gateway(monkeypatch) -> None:
    registry = SimpleNamespace(resolve=lambda _model: object())
    monkeypatch.setattr(Engine, "_build_provider_registry", lambda self: registry)
    monkeypatch.setattr(Engine, "_build_embedding_registry", lambda self: SimpleNamespace())

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            memory_enabled=False,
            state_store_enabled=False,
        )
    )
    graph = Graph(nodes=[], edges=[], entry_points=[], exit_points=[])
    state = ExecutionState(graph)

    context = engine._make_context(
        state=state,
        shared_context=SharedContextStore([]),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        graph=graph,
    )

    assert context.provider_registry is registry
    assert context.model_gateway is engine.model_gateway


def test_run_manager_make_engine_threads_injected_model_gateway(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeEngine:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("dan.server.run_manager.Engine", _FakeEngine)

    gateway = object()
    manager = RunManager(
        engine_config=EngineConfig(
            checkpoint_enabled=False,
            memory_enabled=False,
            state_store_enabled=False,
        ),
        model_gateway=gateway,
    )

    _ = manager._make_engine(executor_registry=SimpleNamespace())

    assert captured["model_gateway"] is gateway
    assert captured["config"] is manager.engine_config


def test_publish_local_runtime_make_engine_threads_injected_model_gateway(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeEngine:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    import dan.engine as engine_module

    monkeypatch.setattr(engine_module, "Engine", _FakeEngine)

    gateway = object()
    runtime = LocalRuntime(
        engine_config=EngineConfig(
            checkpoint_enabled=False,
            memory_enabled=False,
            state_store_enabled=False,
        ),
        model_gateway=gateway,
    )

    _ = runtime._make_engine()

    assert captured["model_gateway"] is gateway


def test_publish_local_runtime_constructible_without_server_startup() -> None:
    runtime = LocalRuntime(
        engine_config=EngineConfig(
            checkpoint_enabled=False,
            memory_enabled=False,
            state_store_enabled=False,
        )
    )

    assert runtime.mode == "local"
    assert runtime.session_store is not None
