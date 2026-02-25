"""Compiler diagnostics for the markdown loader."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from dan.models.graph import Graph


@dataclass
class Diagnostic:
    level: Literal["error", "warning", "info"]
    message: str
    source_file: Path | None = None
    source_line: int = 0
    source_column: int = 0
    hint: str | None = None


@dataclass
class CompileResult:
    """Returned by ``compile_workflow``.  ``graph`` is None when errors exist."""

    graph: Graph | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(d.level == "error" for d in self.diagnostics)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.level == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.level == "warning"]


@dataclass
class DecompileResult:
    """Returned by ``decompile_to_markdown``."""

    files: list[Path] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)


def format_diagnostics(diagnostics: list[Diagnostic]) -> str:
    """Format diagnostics like ``file.md:line: error: message``."""
    lines: list[str] = []
    for d in diagnostics:
        loc = ""
        if d.source_file:
            loc = f"{d.source_file}"
            if d.source_line:
                loc += f":{d.source_line}"
            loc += ": "
        text = f"{loc}{d.level}: {d.message}"
        if d.hint:
            text += f"\n  hint: {d.hint}"
        lines.append(text)
    return "\n".join(lines)
