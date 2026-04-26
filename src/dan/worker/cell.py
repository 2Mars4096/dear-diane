"""Universal cell factory for brief-driven worker execution."""

from __future__ import annotations

import re
from typing import Any, Mapping

from dan.worker.contracts.sampling import SamplingPolicy, resolve_sampling_policy
from dan.worker.core.model import CompletionHints, WorkerDefinition


UNIVERSAL_CELL_SYSTEM_PROMPT = """You are a universal execution cell.

Follow the role, policies, constraints, evidence, tool policy, runtime policy, and output contract supplied in the current brief.
Treat those brief fields as the source of task-specific behavior.
Keep the invariant cell discipline: preserve scope boundaries, follow tool and safety policies exactly, make material progress when allowed, report blockers honestly, and return the requested output shape.
If requirements conflict or the brief is underspecified, choose the least-destructive compliant action and surface the blocker instead of inventing hidden rules.
Do not assume product-specific rules, file sets, thresholds, budgets, validation criteria, or domain facts unless the current brief supplies them."""


def _slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "cell").strip().lower())
    return text.strip("-") or "cell"


def build_cell(
    model: str | None = None,
    sampling_policy: SamplingPolicy | Mapping[str, Any] | str | None = None,
    role_label: str = "cell",
) -> WorkerDefinition:
    """Build the single canonical cell definition used by universal organisms."""

    resolved_sampling = resolve_sampling_policy(sampling_policy)
    return WorkerDefinition(
        id=f"universal-cell.{_slug(role_label)}",
        role=str(role_label or "cell"),
        model=model,
        instruction="",
        persona="",
        tool_ids=[],
        llm_hints=CompletionHints(
            system_prompt=UNIVERSAL_CELL_SYSTEM_PROMPT,
            temperature=resolved_sampling.temperature if resolved_sampling.temperature is not None else 0.7,
            max_tokens=resolved_sampling.max_tokens,
        ),
        metadata={
            "universal_cell": True,
            "system_constitution_id": "universal-cell-system-v1",
            "sampling_policy": resolved_sampling.model_dump(mode="json", exclude_none=True),
            "provider_hints": dict(resolved_sampling.provider_hints),
        },
    )
