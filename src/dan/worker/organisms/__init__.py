"""Universal Organism and Super DAN implementations."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "OrganismPlan": ("dan.worker.organisms.universal_organism", "OrganismPlan"),
    "OrganismPolicy": ("dan.worker.organisms.universal_organism", "OrganismPolicy"),
    "OrganismTask": ("dan.worker.organisms.universal_organism", "OrganismTask"),
    "OrganismResult": ("dan.worker.organisms.universal_organism", "OrganismResult"),
    "execute_universal_organism": (
        "dan.worker.organisms.universal_organism",
        "execute_universal_organism",
    ),
    "SuperOrganismReport": ("dan.worker.organisms.super_organism", "SuperOrganismReport"),
    "run_super_organism_demo": (
        "dan.worker.organisms.super_organism",
        "run_super_organism_demo",
    ),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
