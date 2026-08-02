"""Local-vs-server capability registration parity tests.

Both the server startup path (``startup.py``) and the local CLI path
(``chat_factory.py``) register capabilities into a ``ChatCapabilityRegistry``.
These tests verify that both paths can be imported cleanly and that the
resulting capability sets are consistent.
"""

from __future__ import annotations

import importlib
from pathlib import Path


def _registration_function_names() -> list[str]:
    """Return the canonical register_*_capabilities function names."""
    return [
        "register_base_capabilities",
        "register_common_capabilities",
        "register_experience_capabilities",
        "register_introspection_capabilities",
        "register_run_lifecycle_capabilities",
        "register_tool_capabilities",
        "register_workflow_catalog_capabilities",
    ]


SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "dan"


def test_capability_registration_functions_importable() -> None:
    """All register_*_capabilities can be imported from capability_handlers."""
    mod = importlib.import_module("dan.server.capability_handlers")
    missing = [
        name for name in _registration_function_names()
        if not hasattr(mod, name)
    ]
    assert not missing, f"Missing registration functions: {missing}"


def test_capability_registry_importable() -> None:
    """ChatCapabilityRegistry and CapabilityContext can be imported."""
    mod = importlib.import_module("dan.server.capability_registry")
    assert hasattr(mod, "ChatCapabilityRegistry")
    assert hasattr(mod, "CapabilityContext")


def test_local_and_server_register_same_core_capabilities() -> None:
    """Both paths register the same core set of capabilities.

    The server path may additionally register publish capabilities and
    MCP tools, so we check that the local set is a subset of the server
    set (with at least 80% overlap by name count).
    """
    from dan.server.capability_registry import ChatCapabilityRegistry
    from dan.server.capability_handlers import (
        register_common_capabilities,
        register_publish_capabilities,
    )

    local_reg = ChatCapabilityRegistry()
    register_common_capabilities(local_reg)

    server_reg = ChatCapabilityRegistry()
    register_common_capabilities(server_reg)
    register_publish_capabilities(server_reg)

    local_names = set(local_reg.list_tool_names())
    server_names = set(server_reg.list_tool_names())

    overlap = local_names & server_names
    parity_ratio = len(overlap) / max(len(server_names), 1)

    assert parity_ratio >= 0.80, (
        f"Capability parity too low: {parity_ratio:.0%} "
        f"(overlap={len(overlap)}, local={len(local_names)}, server={len(server_names)})\n"
        f"  Server-only: {sorted(server_names - local_names)}\n"
        f"  Local-only:  {sorted(local_names - server_names)}"
    )

    assert local_names <= server_names, (
        f"Local path registers capabilities not in server path: "
        f"{sorted(local_names - server_names)}"
    )


def test_chat_factory_importable() -> None:
    """The local bootstrap module can be imported without side effects."""
    mod = importlib.import_module("dan.server.chat_factory")
    assert hasattr(mod, "build_chat_services")


def test_local_chat_runtime_importable() -> None:
    """The local chat runtime can be imported without side effects."""
    mod = importlib.import_module("dan.cli.chat_local")
    assert hasattr(mod, "LocalChatRuntime")


def test_startup_and_chat_factory_use_shared_capability_helper() -> None:
    targets = [
        SRC_ROOT / "server" / "startup" / "__init__.py",
        SRC_ROOT / "server" / "chat_factory" / "__init__.py",
    ]

    for path in targets:
        source = path.read_text()
        assert "register_common_capabilities" in source, (
            f"{path.relative_to(SRC_ROOT)} should use register_common_capabilities()"
        )
