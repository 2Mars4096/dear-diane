"""Gateway configuration — concern toggles and per-call override defaults."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GatewayConfig:
    """Configuration for gateway concerns.

    Every flag can be overridden per-call via keyword arguments to
    :meth:`ModelGateway.complete` and :meth:`ModelGateway.stream`.
    """

    pii_enabled: bool = True
    retry_enabled: bool = True
    retry_max_attempts: int = 3
    retry_backoff_base: float = 1.0
    timeout_seconds: float = 120.0
    telemetry_enabled: bool = True
    budget_enabled: bool = False
    fallback_model: str | None = None
