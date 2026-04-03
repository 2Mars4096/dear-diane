"""Result models for the handoff linter."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dan.linter.config import RuleSeverity
from dan.linter.rules import Tier


class LintDiagnostic(BaseModel):
    code: str
    message: str
    tier: Tier
    severity: RuleSeverity
    field_path: list[str | int] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LintResult(BaseModel):
    passed: bool
    auto_fixed: bool = False
    diagnostics: list[LintDiagnostic] = Field(default_factory=list)
    fixed_data: Any | None = None
    applied_fixes: list[str] = Field(default_factory=list)
    suggested_fixes: list[str] = Field(default_factory=list)
    retry_feedback: str | None = None
    refocus_feedback: str | None = None
    semantic_score: float | None = None
    intent_score: float | None = None
    tier_reached: Tier | None = None
    elapsed_ms: float | None = None
