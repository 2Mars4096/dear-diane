"""Universal Cell and Organism public API.

Imports stay lazy so loading one worker submodule does not initialize the full
Diane runtime.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "UNIVERSAL_CELL_SYSTEM_PROMPT": ("diane.worker.cell", "UNIVERSAL_CELL_SYSTEM_PROMPT"),
    "build_cell": ("diane.worker.cell", "build_cell"),
    "RoleSpec": ("diane.worker.brief", "RoleSpec"),
    "WorkerBrief": ("diane.worker.brief", "WorkerBrief"),
    "OrganismPlan": ("diane.worker.organisms.universal_organism", "OrganismPlan"),
    "OrganismPolicy": ("diane.worker.organisms.universal_organism", "OrganismPolicy"),
    "OrganismTask": ("diane.worker.organisms.universal_organism", "OrganismTask"),
    "OrganismResult": ("diane.worker.organisms.universal_organism", "OrganismResult"),
    "execute_universal_organism": (
        "diane.worker.organisms.universal_organism",
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
