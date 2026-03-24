"""Supplementary types for gateway telemetry and call tracking."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GatewayCall:
    """Metadata about a gateway call for telemetry callbacks."""

    model: str
    started_at: float
    elapsed_ms: float | None = None
    retries: int = 0
    pii_applied: bool = False
    fallback_used: bool = False
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
