"""Universal Cell and Organism public API.

Imports stay lazy so loading one worker submodule does not initialize the full
Diane runtime.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "UNIVERSAL_CELL_SYSTEM_PROMPT": ("dan.worker.cell", "UNIVERSAL_CELL_SYSTEM_PROMPT"),
    "build_cell": ("dan.worker.cell", "build_cell"),
    "RoleSpec": ("dan.worker.brief", "RoleSpec"),
    "WorkerBrief": ("dan.worker.brief", "WorkerBrief"),
    "OrganismPlan": ("dan.worker.organisms.universal_organism", "OrganismPlan"),
    "OrganismPolicy": ("dan.worker.organisms.universal_organism", "OrganismPolicy"),
    "OrganismTask": ("dan.worker.organisms.universal_organism", "OrganismTask"),
    "OrganismResult": ("dan.worker.organisms.universal_organism", "OrganismResult"),
    "execute_universal_organism": (
        "dan.worker.organisms.universal_organism",
        "execute_universal_organism",
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
