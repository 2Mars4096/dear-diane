"""Workflow generation quality evaluation harness (Phase 33).

Measures how well DAN generates and executes workflows from natural language.
Produces JSONL logs and summary reports — not pytest pass/fail verdicts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Prompt fixture schema (loaded from prompts.json)
# ---------------------------------------------------------------------------


class PromptFixture(BaseModel):
    id: str
    tier: str  # T1, T2, T2R, T3, T4, T5, pilot
    lane: str = "both"  # agent | build | both
    prompt: str
    tags: list[str] = []
    expected: dict[str, Any] = {}
    multi_turn_follow_ups: list[str] = []
    pilot: bool = False
    edge_case: bool = False
    expected_behavior: str | None = None


# ---------------------------------------------------------------------------
# Evaluation record (one per prompt+lane, written to JSONL)
# ---------------------------------------------------------------------------


class TimingInfo(BaseModel):
    prompt_sent_at: float = 0.0
    first_token_at: float | None = None
    complete_at: float | None = None
    total_ms: float = 0.0


class TokenInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0


class GraphSummary(BaseModel):
    node_count: int = 0
    node_types: list[str] = []
    edge_count: int = 0
    has_loop: bool = False
    has_review_loop: bool = False
    review_loop_count: int = 0
    has_conditional: bool = False
    has_fan_out: bool = False
    has_parallel_subagents: bool = False
    has_merge: bool = False


class ValidationResult(BaseModel):
    passed: bool = False
    errors: list[str] = []


class ExecutionResult(BaseModel):
    status: str = ""
    duration_ms: float = 0.0
    nodes_completed: int = 0
    error: str | None = None


class EvalRecord(BaseModel):
    """One record per prompt+lane evaluation."""

    id: str
    tier: str
    lane: str
    prompt: str
    model: str | None = None
    timestamp: str = ""
    timing: TimingInfo = Field(default_factory=TimingInfo)
    build_tokens: TokenInfo = Field(default_factory=TokenInfo)
    run_tokens: TokenInfo | None = None
    observed_events: list[dict[str, Any]] = []
    telemetry: dict[str, Any] | None = None
    graph_created: bool = False
    graph_id: str | None = None
    graph_summary: GraphSummary | None = None
    validation: ValidationResult | None = None
    quality_score: int | None = None
    quality_concerns: list[str] = []
    expectation_errors: list[str] = []
    judge_scores: dict[str, int] | None = None
    execution: ExecutionResult | None = None
    generation_path: str | None = None
    generation_path_taken: str | None = None
    fallback_chain: list[str] | None = None
    generation_wall_clock_ms: int | None = None
    complexity_tier: str | None = None
    retries_used: dict[str, Any] | None = None
    domain_detected: str | None = None
    guard_events: list[dict[str, Any]] = []
    status: str = ""
    failure_mode: str | None = None
    error: str | None = None
    response_text: str = ""
    multi_turn_history: list[dict[str, Any]] = []
    execution_path_requested: str | None = None  # inline|codegen|auto (plan 32-7)


# ---------------------------------------------------------------------------
# Shared paths
# ---------------------------------------------------------------------------

EVAL_DIR = Path(__file__).parent
RESULTS_DIR = EVAL_DIR / "results"
PROMPTS_FILE = EVAL_DIR / "prompts.json"
