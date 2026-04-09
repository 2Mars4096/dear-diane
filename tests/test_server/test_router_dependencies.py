from __future__ import annotations

from types import SimpleNamespace

from dan.server.app_state import AppState
from dan.server.routers import dependencies


def _request_with_state(state: AppState):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(dan=state)))


def test_request_backed_dependencies_prefer_typed_app_state(monkeypatch) -> None:
    state = AppState(
        graph_store=object(),
        chat_store=object(),
        test_case_store=object(),
        run_manager=object(),
        chat_manager=object(),
        publish_registry=object(),
        block_registry=object(),
        concierge=object(),
        dispatcher=object(),
        mention_resolver=object(),
        engine_config=object(),
        graphs_dir="/tmp/dan-graphs",
        furnace_session_store=object(),
        furnace_enabled=True,
    )
    request = _request_with_state(state)

    assert dependencies.get_graph_store(request) is state.graph_store
    assert dependencies.get_chat_store(request) is state.chat_store
    assert dependencies.get_test_case_store(request) is state.test_case_store
    assert dependencies.get_run_manager(request) is state.run_manager
    assert dependencies.get_chat_manager(request) is state.chat_manager
    assert dependencies.get_publish_registry(request) is state.publish_registry
    assert dependencies.get_block_registry(request) is state.block_registry
    assert dependencies.get_concierge(request) is state.concierge
    assert dependencies.get_dispatcher(request) is state.dispatcher
    assert dependencies.get_mention_resolver(request) is state.mention_resolver
    assert dependencies.get_engine_config(request) is state.engine_config
    assert dependencies.get_graphs_dir(request) == "/tmp/dan-graphs"
    assert dependencies.get_furnace_session_store(request) is state.furnace_session_store
    assert dependencies.is_furnace_enabled(request) is True


def test_no_request_dependencies_use_active_app_state(monkeypatch) -> None:
    state = AppState(
        graph_store=object(),
        chat_store=object(),
        test_case_store=object(),
        run_manager=object(),
        chat_manager=object(),
        publish_registry=object(),
        block_registry=object(),
        concierge=object(),
        dispatcher=object(),
        mention_resolver=object(),
        engine_config=object(),
        graphs_dir="/tmp/dan-graphs",
        furnace_session_store=object(),
        furnace_enabled=True,
    )

    monkeypatch.setattr(dependencies, "_get_active_app_state", lambda: state)

    assert dependencies.get_app_state() is state
    assert dependencies.get_graph_store() is state.graph_store
    assert dependencies.get_chat_store() is state.chat_store
    assert dependencies.get_test_case_store() is state.test_case_store
    assert dependencies.get_run_manager() is state.run_manager
    assert dependencies.get_chat_manager() is state.chat_manager
    assert dependencies.get_publish_registry() is state.publish_registry
    assert dependencies.get_block_registry() is state.block_registry
    assert dependencies.get_concierge() is state.concierge
    assert dependencies.get_dispatcher() is state.dispatcher
    assert dependencies.get_mention_resolver() is state.mention_resolver
    assert dependencies.get_engine_config() is state.engine_config
    assert dependencies.get_graphs_dir() == "/tmp/dan-graphs"
    assert dependencies.get_furnace_session_store() is state.furnace_session_store
    assert dependencies.is_furnace_enabled() is True


def test_no_request_optional_services_fall_back_to_app_module_globals(monkeypatch) -> None:
    legacy_graph_store = object()
    legacy_concierge = object()
    legacy_dispatcher = object()
    legacy_resolver = object()

    monkeypatch.setattr(dependencies, "_get_active_app_state", lambda: None)
    monkeypatch.setattr("dan.server.app._graph_store", legacy_graph_store)
    monkeypatch.setattr("dan.server.app._concierge", legacy_concierge)
    monkeypatch.setattr("dan.server.app._dispatcher", legacy_dispatcher)
    monkeypatch.setattr("dan.server.app._mention_resolver", legacy_resolver)

    assert dependencies.get_graph_store() is legacy_graph_store
    assert dependencies.get_concierge() is legacy_concierge
    assert dependencies.get_dispatcher() is legacy_dispatcher
    assert dependencies.get_mention_resolver() is legacy_resolver


def test_request_backed_run_manager_honors_explicit_global_override(monkeypatch) -> None:
    state_run_manager = object()
    legacy_run_manager = object()
    state = AppState(
        graph_store=object(),
        chat_store=object(),
        test_case_store=object(),
        run_manager=state_run_manager,
        chat_manager=object(),
        publish_registry=object(),
        block_registry=object(),
        concierge=object(),
        dispatcher=object(),
        mention_resolver=object(),
        engine_config=object(),
        graphs_dir="/tmp/dan-graphs",
        furnace_session_store=object(),
        furnace_enabled=True,
    )
    request = _request_with_state(state)

    monkeypatch.setattr("dan.server.app._run_manager", legacy_run_manager)

    assert dependencies.get_run_manager(request) is legacy_run_manager
