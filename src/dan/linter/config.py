"""Configuration models for the handoff linter."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class RuleSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class StructuralConfig(BaseModel):
    json_schema: dict[str, Any] | None = None
    required_keys: list[str] = Field(default_factory=list)
    non_empty_keys: list[str] = Field(default_factory=list)
    string_max_lengths: dict[str, int] = Field(default_factory=dict)
    ranges: dict[str, dict[str, float]] = Field(default_factory=dict)
    format_patterns: dict[str, str] = Field(default_factory=dict)


class SemanticConfig(BaseModel):
    topic_keywords: list[str] = Field(default_factory=list)
    required_entities: list[str] = Field(default_factory=list)
    entity_match_mode: Literal["exact", "fuzzy", "embedding"] = "exact"
    entity_fuzzy_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    entity_embedding_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    expected_language: str | None = None
    reference_text: str = ""
    contradiction_reference_text: str = ""
    contradiction_min_claim_overlap: float = Field(default=0.6, ge=0.0, le=1.0)
    contradiction_similarity_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    embedding_model: str | None = None
    min_similarity: float | None = None
    min_keyword_ratio: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence_aggregation: Literal["min", "mean"] = "min"


class IntentConfig(BaseModel):
    intent: str = ""
    required_keywords: list[str] = Field(default_factory=list)
    judge_model: str | None = None


class LintConfig(BaseModel):
    structural: StructuralConfig | None = None
    semantic: SemanticConfig | None = None
    intent: IntentConfig | None = None
    tier3_threshold: float = 0.6
    severity: RuleSeverity = RuleSeverity.ERROR
    autofix: list[str] = Field(default_factory=list)
    max_retries: int = 0
    retry_budget_ms: int = 0
    enabled: bool = True
