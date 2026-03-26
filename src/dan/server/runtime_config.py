"""Shared runtime config helpers used by server and local chat bootstrap."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from dan.engine.executor import EngineConfig
from dan.providers import ProviderConfig
from dan.providers.factory import build_provider_registry
from dan.rag import DEFAULT_EMBEDDING_MODEL

logger = logging.getLogger(__name__)

_API_KEY_PLACEHOLDERS = frozenset({"your-api-key-here", "changeme", "replace-me"})
_PROVIDER_KEY_ENV_VARS: dict[str, tuple[str, ...]] = {
    "default": (
        "DAN_LLM_API_KEY",
        "LLM_API_KEY",
        "DAN_OPENAI_API_KEY",
        "OPENAI_API_KEY",
    ),
    "openai": ("DAN_OPENAI_API_KEY", "OPENAI_API_KEY"),
    "anthropic": ("DAN_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    "google": ("DAN_GOOGLE_API_KEY", "GOOGLE_API_KEY"),
}


def env_key_status(env_names: tuple[str, ...]) -> tuple[str, str | None]:
    """Return ``(status, source_env)`` for the first non-placeholder key."""
    saw_placeholder = False
    for env_name in env_names:
        raw = str(os.environ.get(env_name, "") or "").strip()
        if not raw:
            continue
        if raw.lower() in _API_KEY_PLACEHOLDERS:
            saw_placeholder = True
            continue
        return "configured", env_name
    return ("placeholder", None) if saw_placeholder else ("missing", None)


def configured_provider_key(provider_name: str) -> str:
    """Return the configured API key for a provider alias set."""
    env_names = _PROVIDER_KEY_ENV_VARS.get(provider_name, ())
    for env_name in env_names:
        raw = str(os.environ.get(env_name, "") or "").strip()
        if raw and raw.lower() not in _API_KEY_PLACEHOLDERS:
            return raw
    return ""


def append_runtime_degradation(
    degradations: list[dict[str, str]],
    subsystem: str,
    message: str,
) -> None:
    """Append a normalized degradation entry if it is not already present."""
    entry = {
        "subsystem": subsystem.strip() or "unknown",
        "message": message.strip(),
    }
    if entry["message"] and entry not in degradations:
        degradations.append(entry)


def runtime_degradation_summary(
    degradations: list[dict[str, str]],
) -> dict[str, Any]:
    """Return a normalized startup-summary payload."""
    issues: list[dict[str, str]] = []
    for item in degradations:
        subsystem = str(
            item.get("subsystem") or item.get("component") or "unknown"
        ).strip() or "unknown"
        message = str(item.get("message") or item.get("reason") or "").strip()
        if not message:
            continue
        append_runtime_degradation(issues, subsystem, message)
    return {
        "status": "degraded" if issues else "ok",
        "issues": issues,
    }


def log_runtime_degradation_summary(
    degradations: list[dict[str, str]],
    *,
    prefix: str = "Startup degradation summary",
) -> None:
    """Log a one-line summary when any subsystems degraded."""
    summary = runtime_degradation_summary(degradations)
    issues = summary["issues"]
    if not issues:
        return
    rendered = "; ".join(
        f"{issue['subsystem']}: {issue['message']}" for issue in issues
    )
    logger.warning(
        "%s | %d subsystem(s): %s",
        prefix,
        len(issues),
        rendered,
    )


def _parse_json_map_env(env_name: str) -> dict[str, str]:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        logger.warning("Failed to parse %s JSON, ignoring value", env_name)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("%s must be a JSON object, ignoring value", env_name)
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


def build_engine_config_from_env() -> EngineConfig:
    """Build :class:`EngineConfig` from environment variables."""

    providers: dict[str, ProviderConfig] = {}
    embedding_providers: dict[str, ProviderConfig] = {}

    openai_key = configured_provider_key("openai")
    if openai_key:
        providers["openai"] = ProviderConfig(api_key=openai_key)

    anthropic_key = configured_provider_key("anthropic")
    if anthropic_key:
        providers["anthropic"] = ProviderConfig(api_key=anthropic_key)

    google_key = configured_provider_key("google")
    if google_key:
        providers["google"] = ProviderConfig(api_key=google_key)

    default_embedding_model = os.environ.get(
        "DAN_DEFAULT_EMBEDDING_MODEL",
        DEFAULT_EMBEDDING_MODEL,
    )
    embedding_api_key = os.environ.get(
        "DAN_EMBEDDING_API_KEY",
        openai_key
        or os.environ.get("DAN_LLM_API_KEY", os.environ.get("LLM_API_KEY", "")),
    )
    embedding_base_url = os.environ.get(
        "DAN_EMBEDDING_BASE_URL",
        os.environ.get("DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1"),
    )
    if embedding_api_key:
        embedding_providers["default"] = ProviderConfig(
            api_key=embedding_api_key,
            base_url=embedding_base_url,
            default_model=default_embedding_model,
        )

    if os.environ.get("DAN_ENABLE_LOCAL_EMBEDDINGS", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        embedding_providers["local"] = ProviderConfig(
            default_model=os.environ.get(
                "DAN_LOCAL_EMBEDDING_MODEL",
                "all-MiniLM-L6-v2",
            ),
        )

    default_model_policy = None
    if os.environ.get("DAN_ENABLE_TIER_POLICY", "0").lower() in (
        "1",
        "true",
        "yes",
    ):
        from dan.providers.model_policy import TierPolicy

        tier_map = None
        tier_map_env = os.environ.get("DAN_TIER_MAP")
        if tier_map_env:
            try:
                raw = json.loads(tier_map_env)
                from dan.providers.tier_defaults import normalize_tier_map

                tier_map = normalize_tier_map(raw) or raw
            except Exception:
                logger.warning("Failed to parse DAN_TIER_MAP JSON, using defaults")
        default_model_policy = TierPolicy(tier_map=tier_map)

    return EngineConfig(
        llm_base_url=os.environ.get(
            "DAN_LLM_BASE_URL",
            "https://api.vectorengine.ai/v1",
        ),
        llm_api_key=os.environ.get(
            "DAN_LLM_API_KEY",
            os.environ.get("LLM_API_KEY", openai_key),
        ),
        llm_default_model=os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6"),
        checkpoint_dir=os.environ.get("DAN_CHECKPOINT_DIR", "./checkpoints"),
        checkpoint_enabled=True,
        providers=providers,
        model_provider_map=_parse_json_map_env("DAN_MODEL_PROVIDER_MAP"),
        embedding_providers=embedding_providers,
        embedding_model_provider_map=_parse_json_map_env(
            "DAN_EMBEDDING_MODEL_PROVIDER_MAP"
        ),
        default_embedding_model=default_embedding_model,
        default_model_policy=default_model_policy,
        cache_enabled=os.environ.get("DAN_CACHE_ENABLED", "true").lower()
        in ("1", "true", "yes"),
        cache_max_size_mb=int(os.environ.get("DAN_CACHE_MAX_SIZE_MB", "100")),
        cache_dir=os.environ.get("DAN_CACHE_DIR") or None,
        semantic_cache_threshold=float(
            os.environ.get("DAN_SEMANTIC_CACHE_THRESHOLD", "0.95")
        ),
        semantic_cache_ttl_hours=float(
            os.environ.get("DAN_SEMANTIC_CACHE_TTL_HOURS", "24")
        ),
    )


def build_chat_provider_registry(config: EngineConfig) -> Any:
    """Build the chat-facing provider registry for a runtime config."""

    return build_provider_registry(config)


def provider_readiness_summary(config: EngineConfig) -> dict[str, Any]:
    """Summarize provider/bootstrap readiness for health reporting."""
    providers: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []

    for provider_name, env_names in _PROVIDER_KEY_ENV_VARS.items():
        status, source_env = env_key_status(env_names)
        providers.append(
            {
                "name": provider_name,
                "status": status,
                "configured_via": source_env,
                "registered": provider_name == "default"
                or provider_name in getattr(config, "providers", {}),
            }
        )

    registry = build_provider_registry(config)
    default_model = getattr(config, "llm_default_model", "")
    resolved_provider = None
    if default_model:
        try:
            resolved_provider = registry.resolve_name(default_model)
        except KeyError as exc:
            issues.append(
                {
                    "subsystem": "providers",
                    "message": str(exc),
                }
            )

    for model, provider_name in (getattr(config, "model_provider_map", {}) or {}).items():
        if provider_name not in registry.provider_names():
            issues.append(
                {
                    "subsystem": "providers",
                    "message": (
                        f"Model override '{model}' targets provider '{provider_name}', "
                        "but that provider is not configured."
                    ),
                }
            )

    return {
        "status": "degraded" if issues else "ok",
        "default_model": default_model,
        "default_model_provider": resolved_provider,
        "providers": providers,
        "issues": issues,
    }
