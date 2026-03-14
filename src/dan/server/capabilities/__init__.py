"""Capability handler domain modules.

Re-exports ``register_*`` functions lazily from the hub for convenience.
Canonical imports should use ``dan.server.capability_handlers``.
"""
from __future__ import annotations


def __getattr__(name: str):
    """Lazy re-export of register_* functions to avoid circular imports."""
    _REEXPORTS = {
        "register_base_capabilities",
        "register_computer_capabilities",
        "register_experience_capabilities",
        "register_introspection_capabilities",
        "register_publish_capabilities",
        "register_run_lifecycle_capabilities",
        "register_tool_capabilities",
        "register_workflow_catalog_capabilities",
    }
    if name in _REEXPORTS:
        import dan.server.capability_handlers as _hub
        return getattr(_hub, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
