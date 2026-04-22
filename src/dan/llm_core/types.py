"""Supplementary types for gateway telemetry and call tracking."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GatewayCall:
    """Metadata about a gateway call for telemetry callbacks."""

    model: str
    started_at: float
    provider_name: str | None = None
    elapsed_ms: float | None = None
    retries: int = 0
    pii_applied: bool = False
    fallback_used: bool = False
    dispatch_group: str | None = None
    dispatch_attempts: int = 0
    queue_wait_ms: float = 0.0
    queue_depth_at_submit: int = 0
    queue_rejected: bool = False
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
