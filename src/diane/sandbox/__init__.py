"""Subprocess sandbox — operational guardrails for code execution.

Provides configurable timeouts, resource caps, output limits, and
environment filtering for CodeOperator subprocess execution.  NOT a
security sandbox (no OS-level isolation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_OUTPUT = 1_048_576  # 1 MB
DEFAULT_MEMORY_MB = 512


class SandboxConfig(BaseModel):
    """Configuration for subprocess code execution."""

    mode: Literal["inline", "subprocess"] = "inline"
    timeout_seconds: int = DEFAULT_TIMEOUT
    memory_mb: int | None = None
    language: str = "python"
    pass_env: list[str] = Field(default_factory=list)
    filesystem_paths: list[str] = Field(default_factory=list)
    max_output_bytes: int = DEFAULT_MAX_OUTPUT


@dataclass
class SandboxResult:
    """Result of a subprocess sandbox execution."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    output_files: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    memory_peak_mb: float | None = None
    truncated: bool = False
