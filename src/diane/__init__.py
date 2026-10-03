"""Dear Diane — a workspace for research, code, and ideas."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__version__ = "0.2.0"

_EXPORTS = {
    "build_cell": ("diane.worker.cell", "build_cell"),
    "OrganismPlan": ("diane.worker.organisms.universal_organism", "OrganismPlan"),
    "execute_universal_organism": (
        "diane.worker.organisms.universal_organism",
        "execute_universal_organism",
    ),
    "run_super_organism_demo": (
        "diane.worker.organisms.super_organism",
        "run_super_organism_demo",
    ),
}

__all__ = ["__version__", *sorted(_EXPORTS)]


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
