"""Lightweight worker-core model definitions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dan.worker.core.contracts import AcquisitionPolicy


class CompletionHints(BaseModel):
    """Execution-time completion hints for the reusable core."""

    system_prompt: str = ""
    prompt_template: str = ""
    temperature: float = 0.7
    max_tokens: int | None = None


class WorkerDefinition(BaseModel):
    """DAN-independent worker description."""

    id: str
    role: str = ""
    instruction: str = ""
    persona: str = ""
    model: str | None = None
    tool_ids: list[str] = Field(default_factory=list)
    llm_hints: CompletionHints | None = None
    acquisition_policy: AcquisitionPolicy | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
