"""Universal Cell and Organism public API.

Imports stay lazy so loading one worker submodule does not initialize the full
Super DAN runtime.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "UNIVERSAL_CELL_SYSTEM_PROMPT": ("dan.worker.cell", "UNIVERSAL_CELL_SYSTEM_PROMPT"),
    "build_cell": ("dan.worker.cell", "build_cell"),
    "CellAcceptance": ("dan.worker.structured_cell", "CellAcceptance"),
    "CellContract": ("dan.worker.structured_cell", "CellContract"),
    "CellInvocation": ("dan.worker.structured_cell", "CellInvocation"),
    "CellRecord": ("dan.worker.structured_cell", "CellRecord"),
    "CellReport": ("dan.worker.structured_cell", "CellReport"),
    "CellRuntimeConfig": ("dan.worker.structured_cell", "CellRuntimeConfig"),
    "CellSpec": ("dan.worker.structured_cell", "CellSpec"),
    "CellTopology": ("dan.worker.structured_cell", "CellTopology"),
    "CompiledCellInvocation": ("dan.worker.structured_cell", "CompiledCellInvocation"),
    "ContextProjection": ("dan.worker.structured_cell", "ContextProjection"),
    "ContextView": ("dan.worker.structured_cell", "ContextView"),
    "ExecutorRef": ("dan.worker.structured_cell", "ExecutorRef"),
    "ForkJoinRelation": ("dan.worker.structured_cell", "ForkJoinRelation"),
    "JoinPolicy": ("dan.worker.structured_cell", "JoinPolicy"),
    "RecordRequirement": ("dan.worker.structured_cell", "RecordRequirement"),
    "SequenceRelation": ("dan.worker.structured_cell", "SequenceRelation"),
    "UniversalCellConfig": ("dan.worker.structured_cell", "UniversalCellConfig"),
    "bind_cell": ("dan.worker.structured_cell", "bind_cell"),
    "build_cell_spec": ("dan.worker.structured_cell", "build_cell_spec"),
    "build_structured_cell": ("dan.worker.structured_cell", "build_structured_cell"),
    "collapse_child_reports": ("dan.worker.structured_cell", "collapse_child_reports"),
    "compile_cell": ("dan.worker.structured_cell", "compile_cell"),
    "execute_structured_cell": (
        "dan.worker.structured_cell",
        "execute_structured_cell",
    ),
    "fan_out_and_collapse": ("dan.worker.structured_cell", "fan_out_and_collapse"),
    "handoff_to_next": ("dan.worker.structured_cell", "handoff_to_next"),
    "inherit_previous_report": (
        "dan.worker.structured_cell",
        "inherit_previous_report",
    ),
    "join_ready": ("dan.worker.structured_cell", "join_ready"),
    "link_linear": ("dan.worker.structured_cell", "link_linear"),
    "prepare_children": ("dan.worker.structured_cell", "prepare_children"),
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
