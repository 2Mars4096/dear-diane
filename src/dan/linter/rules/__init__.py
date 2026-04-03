"""Rule helpers for linter tiers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Awaitable, Protocol

if TYPE_CHECKING:
    from dan.linter.result import LintDiagnostic


class Tier(IntEnum):
    STRUCTURAL = 1
    SEMANTIC = 2
    INTENT = 3


@dataclass(frozen=True)
class RuleSpec:
    code: str
    tier: Tier
    autofixable: bool = False


class Rule(Protocol):
    code: str
    tier: Tier

    async def check(
        self,
        data: Any,
        config: Any,
        **deps: Any,
    ) -> tuple[list["LintDiagnostic"], float | None]:
        """Evaluate a single lint rule against the candidate handoff."""


class Embedder(Protocol):
    async def __call__(self, text: str, model: str | None = None) -> list[float]:
        """Return an embedding vector for the supplied text."""


class LLMJudge(Protocol):
    async def __call__(
        self,
        data: Any,
        config: Any,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a structured intent judgment payload."""


__all__ = ["Embedder", "LLMJudge", "Rule", "RuleSpec", "Tier"]
