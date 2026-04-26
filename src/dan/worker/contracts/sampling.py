"""Sampling policy contracts for universal cells."""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, Field


class SamplingPolicy(BaseModel):
    """Provider-agnostic sampling hints selected by an orchestrator brief."""

    profile: str = "deterministic"
    temperature: float | None = Field(default=None, ge=0.0)
    max_tokens: int | None = Field(default=None, ge=1)
    reasoning: str | None = None
    provider_hints: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


deterministic = SamplingPolicy(
    profile="deterministic",
    temperature=0.2,
    provider_hints={"style": "stable"},
)
creative = SamplingPolicy(
    profile="creative",
    temperature=0.8,
    provider_hints={"style": "exploratory"},
)

BASELINE_SAMPLING_PROFILES: dict[str, SamplingPolicy] = {
    "deterministic": deterministic,
    "creative": creative,
}
SAMPLING_PROFILES = BASELINE_SAMPLING_PROFILES


def get_sampling_profile(name: str) -> SamplingPolicy:
    try:
        return BASELINE_SAMPLING_PROFILES[name].model_copy(deep=True)
    except KeyError as exc:
        known = ", ".join(sorted(BASELINE_SAMPLING_PROFILES))
        raise ValueError(f"Unknown sampling profile {name!r}; expected one of: {known}") from exc


def _payload_from(value: SamplingPolicy | Mapping[str, Any] | str | None) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, SamplingPolicy):
        return value.model_dump(mode="python", exclude_none=True)
    if isinstance(value, str):
        return {"profile": value}
    return dict(value)


def resolve_sampling_policy(
    policy: SamplingPolicy | Mapping[str, Any] | str | None = None,
    *,
    overrides: Mapping[str, Any] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning: str | None = None,
    provider_hints: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> SamplingPolicy:
    """Resolve a baseline sampling profile plus explicit brief-level overrides."""

    payload = _payload_from(policy)
    profile_name = str(payload.get("profile") or "deterministic").strip() or "deterministic"
    base = BASELINE_SAMPLING_PROFILES.get(profile_name)
    resolved = base.model_dump(mode="python", exclude_none=True) if base is not None else {"profile": profile_name}
    resolved.update({key: value for key, value in payload.items() if value is not None})
    if overrides:
        resolved.update({key: value for key, value in dict(overrides).items() if value is not None})
    if temperature is not None:
        resolved["temperature"] = temperature
    if max_tokens is not None:
        resolved["max_tokens"] = max_tokens
    if reasoning is not None:
        resolved["reasoning"] = reasoning
    if provider_hints is not None:
        resolved["provider_hints"] = {
            **dict(resolved.get("provider_hints") or {}),
            **dict(provider_hints),
        }
    if metadata is not None:
        resolved["metadata"] = {
            **dict(resolved.get("metadata") or {}),
            **dict(metadata),
        }
    resolved["profile"] = str(resolved.get("profile") or profile_name)
    return SamplingPolicy.model_validate(resolved)
