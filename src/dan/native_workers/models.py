"""Model sources shared by DAN and native harnesses; credentials stay server-side."""
from __future__ import annotations

import os

OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS = {
    "deepseek/deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
    "moonshotai/kimi-k2.6": "Kimi K2.6",
}
OPENROUTER_EFFORTS = ["low", "medium", "high"]
OPENROUTER_HARNESSES = {"dan", "codex", "claude"}


def model_source(profile: dict) -> str:
    source = profile.get("provider") or ("openrouter" if str(profile.get("base_url", "")).rstrip("/") == OPENROUTER_URL else "native")
    if source not in {"native", "openrouter"}:
        raise ValueError("Unknown model source")
    return source


def openrouter_key() -> str:
    from dan.cli import resolve_config
    key = os.environ.get("DAN_OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        configured = resolve_config()
        if str(configured.get("base_url") or "").rstrip("/") == OPENROUTER_URL:
            key = configured.get("api_key")
    return str(key or "")


def validate_model_source(runtime: str, profile: dict) -> str:
    source = model_source(profile)
    if source == "openrouter":
        if runtime not in OPENROUTER_HARNESSES:
            raise ValueError(f"OpenRouter is not supported by the {runtime} adapter")
        model = str(profile.get("model") or "").strip()
        if "/" not in model or any(char.isspace() for char in model):
            raise ValueError("Choose an OpenRouter model using its full provider/model ID")
        if profile.get("fast"):
            raise ValueError("Fast mode is not supported for OpenRouter selections")
        effort = profile.get("effort") or ""
        if effort and effort not in OPENROUTER_EFFORTS:
            raise ValueError("OpenRouter reasoning must be low, medium, or high")
        # Claude's effort flag is an Anthropic model feature, not a generic gateway control.
        if runtime == "claude" and effort and not model.startswith("anthropic/"):
            raise ValueError("Claude Code does not expose reasoning control for this OpenRouter model")
        if not openrouter_key():
            raise ValueError("Set OPENROUTER_API_KEY on the DAN server to use OpenRouter models")
    return source


def source_catalog(runtime: str) -> list[dict]:
    return [
        {"id": "native", "label": "DAN configuration" if runtime == "dan" else "Native / CLI configuration", "supported": True},
        {"id": "openrouter", "label": "OpenRouter", "supported": runtime in OPENROUTER_HARNESSES,
         "configured": bool(openrouter_key()), "models": list(OPENROUTER_MODELS),
         "model_labels": OPENROUTER_MODELS, "efforts": OPENROUTER_EFFORTS, "fast": False,
         "reason": "" if runtime in OPENROUTER_HARNESSES else "This CLI adapter has no verified OpenRouter connection."},
    ]


def dan_model_policy(profile: dict) -> dict:
    source = validate_model_source("dan", profile)
    policy = {"model": profile.get("model", ""), "provider": source}
    if source == "openrouter":
        policy.update(base_url=OPENROUTER_URL, reasoning_effort=profile.get("effort", ""))
    elif profile.get("base_url"):
        policy["base_url"] = profile["base_url"]
    return policy


class ReasoningProvider:
    """Apply one run's OpenRouter reasoning choice without changing global config."""
    def __init__(self, provider, effort: str):
        self.provider, self.effort = provider, effort

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def options(self, kwargs):
        extra = dict(kwargs.get("extra_body") or {})
        extra["reasoning"] = {"effort": self.effort}
        return {**kwargs, "extra_body": extra, "reasoning": {"effort": self.effort}}

    async def complete(self, *args, **kwargs):
        return await self.provider.complete(*args, **self.options(kwargs))

    async def stream(self, *args, **kwargs):
        async for chunk in self.provider.stream(*args, **self.options(kwargs)):
            yield chunk
