"""Composition-root parity tests (plan 41-5, tasks 4-1 through 4-3).

Validates that server startup and local/CLI bootstrap both construct a
ModelGateway and that the gateway wraps the same provider surface as the
legacy provider registry.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dan.llm_core import GatewayConfig, ModelGateway, build_gateway
from dan.providers import ProviderConfig
from dan.providers.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine_config(
    *,
    api_key: str = "test-key",
    base_url: str = "https://test.example.com/v1",
    providers: dict[str, ProviderConfig] | None = None,
    model_provider_map: dict[str, str] | None = None,
) -> MagicMock:
    cfg = MagicMock()
    cfg.llm_api_key = api_key
    cfg.llm_base_url = base_url
    cfg.providers = providers or {}
    cfg.model_provider_map = model_provider_map or {}
    return cfg


SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "dan"
STARTUP_SOURCE = SRC_ROOT / "server" / "startup" / "__init__.py"
CHAT_FACTORY_SOURCE = SRC_ROOT / "server" / "chat_factory" / "__init__.py"


# ---------------------------------------------------------------------------
# 4-1: Server startup builds gateway
# ---------------------------------------------------------------------------


class TestServerStartupBuildsGateway:
    """Verify the server path constructs and stores a ModelGateway."""

    def test_app_state_has_model_gateway_field(self) -> None:
        from dan.server.app_state import AppState

        state = AppState()
        assert hasattr(state, "model_gateway")
        assert state.model_gateway is None

    def test_startup_exposes_gateway_builder(self) -> None:
        """startup._build_model_gateway is importable and callable."""
        from dan.server.startup import _build_model_gateway

        cfg = _make_engine_config()
        gw = _build_model_gateway(engine_config=cfg)
        assert isinstance(gw, ModelGateway)
        assert isinstance(gw.registry, ProviderRegistry)

    def test_startup_gateway_uses_engine_config_providers(self) -> None:
        extra = {"anthropic": ProviderConfig(api_key="anthro-key")}
        cfg = _make_engine_config(providers=extra)
        from dan.server.startup import _build_model_gateway

        gw = _build_model_gateway(engine_config=cfg)
        assert gw.registry.has_provider("default")

    def test_startup_imports_build_gateway_from_llm_core(self) -> None:
        """startup._build_model_gateway delegates to llm_core.factory."""
        source = STARTUP_SOURCE.read_text()
        tree = ast.parse(source)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if (
                    node.module
                    and "llm_core" in node.module
                    and any(a.name == "build_gateway" for a in node.names)
                ):
                    found = True
                    break
        assert found, "server.startup should import build_gateway from llm_core"

    def test_startup_threads_gateway_to_run_manager_and_publish_runtime(self) -> None:
        source = STARTUP_SOURCE.read_text()

        assert "state.run_manager.model_gateway = state.model_gateway" in source
        assert "model_gateway=state.model_gateway" in source


# ---------------------------------------------------------------------------
# 4-1 (local): Local bootstrap builds gateway
# ---------------------------------------------------------------------------


class TestLocalBootstrapBuildsGateway:
    """Verify the local/CLI path constructs a ModelGateway via build_chat_services."""

    def test_chat_services_has_model_gateway_attr(self) -> None:
        from dan.server.chat_factory import ChatServices

        svc = ChatServices(
            graph_store=MagicMock(),
            chat_store=MagicMock(),
            chat_manager=MagicMock(),
        )
        assert hasattr(svc, "model_gateway")
        assert svc.model_gateway is None
        assert hasattr(svc, "startup_degradations")
        assert svc.startup_degradations == []

    def test_chat_factory_imports_build_gateway(self) -> None:
        """chat_factory.build_chat_services delegates to llm_core.factory."""
        source = CHAT_FACTORY_SOURCE.read_text()
        tree = ast.parse(source)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if (
                    node.module
                    and "llm_core" in node.module
                    and any(a.name == "build_gateway" for a in node.names)
                ):
                    found = True
                    break
        assert found, "server.chat_factory should import build_gateway from llm_core"

    def test_chat_factory_threads_gateway_to_run_manager(self) -> None:
        source = CHAT_FACTORY_SOURCE.read_text()

        assert "model_gateway=model_gateway" in source

    def test_local_chat_runtime_delegates_to_build_chat_services(self) -> None:
        """chat_local.py uses build_chat_services (which now builds a gateway)."""
        source = (SRC_ROOT / "cli" / "chat_local.py").read_text()
        assert "build_chat_services" in source


# ---------------------------------------------------------------------------
# 4-3: Gateway registry matches startup registry
# ---------------------------------------------------------------------------


class TestGatewayRegistryMatchesStartupRegistry:
    """When both a legacy registry and a gateway are built from the same
    engine config, they should produce equivalent provider sets."""

    def test_same_providers_registered(self) -> None:
        from dan.providers.factory import build_provider_registry

        cfg = _make_engine_config()
        legacy_reg = build_provider_registry(cfg)
        gw = build_gateway(engine_config=cfg)

        legacy_names = set(legacy_reg.provider_names())
        gw_names = set(gw.registry.provider_names())
        assert legacy_names == gw_names

    def test_same_providers_with_extras(self) -> None:
        from dan.providers.factory import build_provider_registry

        extra = {"anthropic": ProviderConfig(api_key="anthro-key")}
        cfg = _make_engine_config(providers=extra)

        legacy_reg = build_provider_registry(cfg)
        gw = build_gateway(engine_config=cfg)

        legacy_names = set(legacy_reg.provider_names())
        gw_names = set(gw.registry.provider_names())
        assert legacy_names == gw_names

    def test_model_overrides_applied_to_gateway(self) -> None:
        overrides = {"custom-model": "default"}
        cfg = _make_engine_config(model_provider_map=overrides)

        gw = build_gateway(engine_config=cfg)
        assert gw.registry.resolve_name("custom-model") == "default"

    def test_build_gateway_returns_default_config_when_none_given(self) -> None:
        cfg = _make_engine_config()
        gw = build_gateway(engine_config=cfg)
        assert isinstance(gw.config, GatewayConfig)
        assert gw.config.retry_enabled is True

    def test_build_gateway_accepts_custom_config(self) -> None:
        cfg = _make_engine_config()
        custom = GatewayConfig(retry_enabled=False, timeout_seconds=30.0)
        gw = build_gateway(engine_config=cfg, gateway_config=custom)
        assert gw.config.retry_enabled is False
        assert gw.config.timeout_seconds == 30.0


# ---------------------------------------------------------------------------
# Structural: both surfaces import from llm_core
# ---------------------------------------------------------------------------


class TestBothSurfacesUseLLMCore:
    """Verify server and local entry points both import from llm_core."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "server/startup/__init__.py",
            "server/chat_factory/__init__.py",
        ],
    )
    def test_surface_imports_from_llm_core(self, rel_path: str) -> None:
        source = (SRC_ROOT / rel_path).read_text()
        tree = ast.parse(source)
        llm_core_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and "llm_core" in node.module
        ]
        assert llm_core_imports, f"{rel_path} should import from llm_core"
