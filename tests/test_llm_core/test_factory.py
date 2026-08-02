"""Tests for the centralized gateway factory (llm_core.factory.build_gateway).

Validates:
  - Building a gateway with just api_key
  - Building a gateway with an engine_config
  - Applying model_provider_map overrides
  - Import boundary: llm_core.factory never imports server/engine/concierge/cli
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dan.llm_core import GatewayConfig, ModelGateway, build_gateway
from dan.providers import ProviderConfig
from dan.providers.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Minimal engine_config stub
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


# ---------------------------------------------------------------------------
# build_gateway with api_key only
# ---------------------------------------------------------------------------


class TestBuildGatewayWithApiKey:
    def test_returns_model_gateway(self) -> None:
        gw = build_gateway(api_key="sk-test")
        assert isinstance(gw, ModelGateway)

    def test_registry_has_default_provider(self) -> None:
        gw = build_gateway(api_key="sk-test")
        assert gw.registry.has_provider("default")

    def test_default_gateway_config(self) -> None:
        gw = build_gateway(api_key="sk-test")
        assert gw.config.retry_enabled is True
        assert gw.config.timeout_seconds == 120.0

    def test_custom_gateway_config(self) -> None:
        cfg = GatewayConfig(retry_enabled=False, timeout_seconds=30.0)
        gw = build_gateway(api_key="sk-test", gateway_config=cfg)
        assert gw.config.retry_enabled is False
        assert gw.config.timeout_seconds == 30.0

    def test_api_key_and_base_url(self) -> None:
        gw = build_gateway(api_key="sk-test", base_url="https://custom.api/v1")
        assert gw.registry.has_provider("default")

    def test_no_params_creates_empty_registry(self) -> None:
        gw = build_gateway()
        assert isinstance(gw, ModelGateway)
        assert gw.registry.provider_names() == []


# ---------------------------------------------------------------------------
# build_gateway with engine_config
# ---------------------------------------------------------------------------


class TestBuildGatewayWithEngineConfig:
    def test_returns_model_gateway(self) -> None:
        cfg = _make_engine_config()
        gw = build_gateway(cfg)
        assert isinstance(gw, ModelGateway)

    def test_registry_has_default(self) -> None:
        cfg = _make_engine_config()
        gw = build_gateway(cfg)
        assert gw.registry.has_provider("default")

    def test_extra_providers(self) -> None:
        cfg = _make_engine_config(
            providers={"openai": ProviderConfig(api_key="sk-openai")},
        )
        gw = build_gateway(cfg)
        assert gw.registry.has_provider("openai")

    def test_cost_tracker_forwarded(self) -> None:
        tracker = MagicMock()
        cfg = _make_engine_config()
        gw = build_gateway(cfg, cost_tracker=tracker)
        assert gw._cost_tracker is tracker

    def test_telemetry_callback_forwarded(self) -> None:
        cb = MagicMock()
        cfg = _make_engine_config()
        gw = build_gateway(cfg, telemetry_callback=cb)
        assert gw._telemetry_callback is cb


# ---------------------------------------------------------------------------
# model_provider_map overrides
# ---------------------------------------------------------------------------


class TestModelProviderMapOverrides:
    def test_overrides_applied_to_registry(self) -> None:
        gw = build_gateway(
            api_key="sk-test",
            model_provider_map={"gpt-4o": "default"},
        )
        assert gw.registry.resolve_name("gpt-4o") == "default"

    def test_overrides_with_engine_config(self) -> None:
        cfg = _make_engine_config()
        gw = build_gateway(
            cfg,
            model_provider_map={"custom-model": "default"},
        )
        assert gw.registry.resolve_name("custom-model") == "default"

    def test_multiple_overrides(self) -> None:
        gw = build_gateway(
            api_key="sk-test",
            model_provider_map={
                "model-a": "default",
                "model-b": "default",
            },
        )
        assert gw.registry.resolve_name("model-a") == "default"
        assert gw.registry.resolve_name("model-b") == "default"


# ---------------------------------------------------------------------------
# Providers dict parameter
# ---------------------------------------------------------------------------


class TestProvidersDict:
    def test_provider_config_value(self) -> None:
        gw = build_gateway(
            providers={"openai": ProviderConfig(api_key="sk-openai")},
        )
        assert gw.registry.has_provider("openai")

    def test_dict_value(self) -> None:
        gw = build_gateway(
            providers={"openai": {"api_key": "sk-openai"}},
        )
        assert gw.registry.has_provider("openai")

    def test_string_value_as_api_key(self) -> None:
        gw = build_gateway(
            providers={"openai": "sk-openai"},
        )
        assert gw.registry.has_provider("openai")


# ---------------------------------------------------------------------------
# Import boundary enforcement
# ---------------------------------------------------------------------------


_FACTORY_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "dan" / "llm_core" / "factory.py"
)
_FORBIDDEN_PREFIXES = ("dan.server", "dan.engine", "dan.concierge", "dan.cli")


class TestImportBoundary:
    """Ensure llm_core/factory.py never imports forbidden modules."""

    def test_no_forbidden_imports(self) -> None:
        source = _FACTORY_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(_FACTORY_PATH))
        violations: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(_FORBIDDEN_PREFIXES):
                        violations.append(
                            f"line {node.lineno}: import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(_FORBIDDEN_PREFIXES):
                    violations.append(
                        f"line {node.lineno}: from {node.module} import ..."
                    )
        assert not violations, (
            "llm_core/factory.py has forbidden imports:\n"
            + "\n".join(violations)
        )
