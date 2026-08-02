from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import dan.server.app as app_module
import dan.server.routers.furnace as furnace_module


def test_furnace_resolve_provider_uses_public_chat_manager_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = object()
    resolve_calls: list[dict[str, Any]] = []

    class FakeChatManager:
        def resolve_llm_provider(
            self,
            *,
            model: str | None = None,
            pii_session_key: str | None = None,
        ) -> Any:
            resolve_calls.append(
                {
                    "model": model,
                    "pii_session_key": pii_session_key,
                }
            )
            return provider

    monkeypatch.setenv("DAN_LLM_MODEL", "gateway-model")
    fake_app = SimpleNamespace(
        state=SimpleNamespace(
            dan=SimpleNamespace(chat_manager=FakeChatManager())
        )
    )

    resolved_provider, model = furnace_module._resolve_provider(
        fake_app,
        pii_session_key="furnace-session",
    )

    assert resolved_provider is provider
    assert model == "gateway-model"
    assert resolve_calls == [
        {
            "model": "gateway-model",
            "pii_session_key": "furnace-session",
        }
    ]


@pytest.mark.asyncio
async def test_build_meta_controller_llm_call_uses_public_chat_manager_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = MagicMock()
    provider.complete = AsyncMock(return_value=SimpleNamespace(text="planned"))
    resolve_calls: list[dict[str, Any]] = []

    class FakeChatManager:
        def resolve_llm_provider(
            self,
            *,
            model: str | None = None,
            pii_session_key: str | None = None,
        ) -> Any:
            resolve_calls.append(
                {
                    "model": model,
                    "pii_session_key": pii_session_key,
                }
            )
            return provider

    planner_instances: list[Any] = []

    class FakeWorkflowPlanner:
        def __init__(self, **kwargs: Any) -> None:
            self.llm_call = kwargs["llm_call"]
            planner_instances.append(self)

    class FakeStructuralRepairPlanner:
        def __init__(self, **kwargs: Any) -> None:
            self.llm_call = kwargs["llm_call"]

    class FakeMetaController:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    monkeypatch.setenv("DAN_LLM_MODEL", "planner-default")
    monkeypatch.setattr(app_module, "_chat_manager", FakeChatManager())
    monkeypatch.setattr(
        app_module,
        "_require_run_manager",
        lambda: SimpleNamespace(
            tool_registry=object(),
            engine_config=SimpleNamespace(
                planner_model=None,
                llm_default_model="engine-default",
                planner_max_retries=2,
                planner_temperature=0.1,
                planner_discovery_top_k=3,
                repair_model=None,
                max_repair_attempts_per_level=1,
                max_redesigns_per_goal=1,
            ),
        ),
    )
    monkeypatch.setattr(app_module, "_get_memory_store", lambda: object())
    monkeypatch.setattr(app_module, "_get_experience_index", lambda: None)
    monkeypatch.setattr(app_module, "_get_experience_store", lambda with_index=False: object())
    monkeypatch.setattr(
        app_module,
        "_graph_store",
        SimpleNamespace(get_graph=lambda workflow_id: None, save_graph=lambda workflow_id, graph: None),
    )
    monkeypatch.setattr(app_module, "_self_knowledge_index", None)

    import dan.meta.controller as meta_controller_module
    import dan.meta.discovery as discovery_module
    import dan.meta.planner as planner_module
    import dan.meta.repair as repair_module

    monkeypatch.setattr(meta_controller_module, "MetaController", FakeMetaController)
    monkeypatch.setattr(
        meta_controller_module,
        "MetaSessionStore",
        lambda memory_store: SimpleNamespace(memory_store=memory_store),
    )
    monkeypatch.setattr(discovery_module, "DiscoveryService", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(planner_module, "WorkflowPlanner", FakeWorkflowPlanner)
    monkeypatch.setattr(
        repair_module,
        "RepairActionStore",
        lambda memory_store: SimpleNamespace(memory_store=memory_store),
    )
    monkeypatch.setattr(repair_module, "RepairEscalator", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        repair_module,
        "StructuralRepairPlanner",
        FakeStructuralRepairPlanner,
    )

    _controller, planner, _session_store = app_module._build_meta_controller()

    result = await planner.llm_call("system", "user", None, 0.2)

    assert result == "planned"
    assert planner_instances
    assert resolve_calls == [
        {
            "model": "planner-default",
            "pii_session_key": None,
        }
    ]

