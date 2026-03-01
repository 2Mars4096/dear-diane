"""dan.loader — markdown agent format loader.

Parses ``.md`` agent files and workflow files, compiles them to the
same ``dan_graph_v1`` JSON that ``dan.builder`` and the visual editor use.

Usage::

    from dan.loader import load

    graph = load("examples/paper_writing_md/workflow.md")
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dan.loader.diagnostics import CompileResult
    from dan.loader.models import AgentSpec
    from dan.models.graph import Graph

__all__ = ["load", "load_agents", "compile_workflow"]


def load(path: str | Path, *, strict: bool = False) -> "Graph":
    """Load a workflow markdown file and compile it to a Graph."""
    result = compile_workflow(path, strict=strict)
    if result.graph is None:
        from dan.loader.diagnostics import format_diagnostics

        raise ValueError(
            "Workflow compilation failed:\n"
            f"{format_diagnostics(result.diagnostics)}"
        )
    return result.graph


def compile_workflow(path: str | Path, *, strict: bool = False) -> "CompileResult":
    """Compile a workflow markdown file into a CompileResult.

    When strict=True, parse warnings and ambiguous bare-edge auto-wire become
    fatal errors; compilation stops and returns graph=None.
    """
    from dan.loader.compiler import compile_workflow as _compile_workflow

    return _compile_workflow(Path(path), strict=strict)


def load_agents(directory: str | Path) -> dict[str, "AgentSpec"]:
    """Load all agent ``.md`` files from a directory."""
    from dan.loader.parser import parse_agent_file

    result: dict[str, AgentSpec] = {}
    for md_file in sorted(Path(directory).glob("*.md")):
        if md_file.name.startswith("workflow"):
            continue
        spec = parse_agent_file(md_file)
        result[spec.name or md_file.stem] = spec
    return result
