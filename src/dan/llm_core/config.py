"""Gateway configuration — concern toggles and per-call override defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int | None) -> int | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


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
    dispatch_enabled: bool = field(
        default_factory=lambda: _env_bool(
            "DAN_LLM_GATEWAY_DISPATCH_ENABLED",
            True,
        )
    )
    dispatch_group: str = field(
        default_factory=lambda: os.environ.get(
            "DAN_LLM_GATEWAY_DISPATCH_GROUP",
            "default",
        ).strip()
        or "default"
    )
    dispatch_max_in_flight: int | None = field(
        default_factory=lambda: _env_int(
            "DAN_LLM_GATEWAY_MAX_IN_FLIGHT",
            4,
        )
    )
    dispatch_max_queue_size: int | None = field(
        default_factory=lambda: _env_int(
            "DAN_LLM_GATEWAY_MAX_QUEUE_SIZE",
            32,
        )
    )
    dispatch_max_requests_per_second: float | None = field(
        default_factory=lambda: _env_float(
            "DAN_LLM_GATEWAY_MAX_REQUESTS_PER_SECOND",
            0.0,
        )
    )
    dispatch_queue_timeout_seconds: float | None = field(
        default_factory=lambda: _env_float(
            "DAN_LLM_GATEWAY_QUEUE_TIMEOUT_SECONDS",
            30.0,
        )
    )
