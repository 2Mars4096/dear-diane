"""Auto-fix protocols and result models for the handoff linter."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from dan.linter.result import LintDiagnostic


@dataclass(frozen=True)
class FixResult:
    fixed: bool
    data: Any
    description: str
    feedback: str | None = None


class AutoFix(ABC):
    """Base protocol for deterministic or model-backed lint fixes."""

    name: str

    @abstractmethod
    def fix(
        self,
        data: Any,
        diagnostic: LintDiagnostic,
        config: Any,
        **deps: Any,
    ) -> FixResult:
        """Return a fixed payload or a no-op result."""
