"""Model sources shared by Diane and native harnesses; credentials stay server-side."""
from __future__ import annotations

import os

OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS = {
    "deepseek/deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
    "moonshotai/kimi-k2.6": "Kimi K2.6",
}
OPENROUTER_EFFORTS = ["low", "medium", "high"]
OPENROUTER_HARNESSES = {"dan", "codex", "claude"}


API_PROVIDERS = {
    "openrouter": {"label": "OpenRouter", "url": OPENROUTER_URL, "anthropic_url": "https://openrouter.ai/api", "env": "OPENROUTER_API_KEY", "models": OPENROUTER_MODELS},
    "openai": {"label": "OpenAI", "url": "https://api.openai.com/v1", "env": "OPENAI_API_KEY", "models": {"gpt-6-astra": "GPT-6 Astra", "gpt-6-sol": "GPT-6 Sol", "gpt-6-luna": "GPT-6 Luna"}},
    "deepseek": {"label": "DeepSeek", "url": "https://api.deepseek.com", "anthropic_url": "https://api.deepseek.com/anthropic", "env": "DEEPSEEK_API_KEY", "models": {"deepseek-flash": "DeepSeek Flash", "deepseek-v4-pro": "DeepSeek V4 Pro"}},
    "moonshot": {"label": "Moonshot / Kimi", "url": "https://api.moonshot.ai/v1", "anthropic_url": "https://api.moonshot.ai/anthropic", "env": "MOONSHOT_API_KEY", "models": {"kimi-k3": "Kimi K3", "kimi-k2.6": "Kimi K2.6"}},
}


def model_source(profile: dict) -> str:
    source = profile.get("provider") or next((key for key, spec in API_PROVIDERS.items() if str(profile.get("base_url", "")).rstrip("/") == spec["url"]), "native")
    if source not in {"native", *API_PROVIDERS}:
        raise ValueError("Unknown model source")
    return source


def provider_key(source: str) -> str:
    from dan.cli import resolve_config
    from .provider_credentials import saved_keys
    spec = API_PROVIDERS[source]
    key = saved_keys().get(source) or os.environ.get("DAN_" + spec["env"]) or os.environ.get(spec["env"])
    if not key and source == "moonshot":
        key = os.environ.get("KIMI_API_KEY")
    if not key:
        configured = resolve_config()
        if str(configured.get("base_url") or "").rstrip("/") == spec["url"]:
            key = configured.get("api_key")
    return str(key or "")


def openrouter_key() -> str:
    return provider_key("openrouter")


def api_models(runtime: str, source: str) -> dict:
    if source == "openai" and runtime == "dan":
        # The default engine currently uses Chat Completions for tool calls.
        return {"gpt-4.1": "GPT-4.1", "gpt-4.1-mini": "GPT-4.1 mini"}
    if source == "moonshot" and runtime == "codex":
        return {"kimi-k3": "Kimi K3"}
    return API_PROVIDERS[source]["models"]


def api_efforts(runtime: str, source: str, model: str) -> list[str]:
    if source == "openrouter":
        return [] if runtime == "claude" and not model.startswith("anthropic/") else OPENROUTER_EFFORTS
    if source == "deepseek":
        return ["low", "high", "max"] if model in API_PROVIDERS[source]["models"] else []
    if source == "moonshot":
        return ["low", "high", "max"] if model == "kimi-k3" else []
    if source == "openai" and runtime == "codex" and model in API_PROVIDERS[source]["models"]:
        return ["low", "medium", "high", "xhigh", "max"]
    return []


def api_supported(runtime: str, source: str) -> bool:
    return runtime in OPENROUTER_HARNESSES and not (runtime == "claude" and source == "openai")


def validate_model_source(runtime: str, profile: dict) -> str:
    source = model_source(profile)
    if source == "native":
        return source
    spec = API_PROVIDERS[source]
    if not api_supported(runtime, source):
        raise ValueError(f"{spec['label']} is not supported by the {runtime} adapter")
    model = str(profile.get("model") or "").strip()
    if not model or any(char.isspace() for char in model) or (source == "openrouter" and "/" not in model):
        raise ValueError("Choose an OpenRouter model using its full provider/model ID" if source == "openrouter" else "Choose an API model ID")
    if source == "openai" and runtime == "dan" and model not in api_models(runtime, source):
        raise ValueError("Default currently supports GPT-4.1 API models; use Codex for GPT-6 tool calling")
    if profile.get("fast"):
        raise ValueError(f"Fast mode is not supported for {spec['label']} API selections")
    effort = profile.get("effort") or ""
    if effort and effort not in api_efforts(runtime, source, model):
        raise ValueError(f"{runtime} does not expose that reasoning control for this {spec['label']} model")
    if not provider_key(source):
        raise ValueError(f"Add a {spec['label']} key in Settings or set {spec['env']} on the Dear Diane server")
    return source


def source_catalog(runtime: str) -> list[dict]:
    sources = [{"id": "native", "label": "Default configuration" if runtime == "dan" else "Native", "supported": True}]
    for source, spec in API_PROVIDERS.items():
        models = api_models(runtime, source)
        supported = api_supported(runtime, source)
        reason = "" if supported else "This agent has no compatible API adapter for this provider."
        if runtime == "claude" and source == "openai":
            reason = "OpenAI does not provide the Anthropic API required by Claude Code. Use Codex or OpenRouter."
        sources.append({"id": source, "label": spec["label"], "supported": supported,
                        "configured": bool(provider_key(source)), "models": list(models), "model_labels": models,
                        "model_efforts": {model: api_efforts(runtime, source, model) for model in models},
                        "efforts": OPENROUTER_EFFORTS if source == "openrouter" else [], "fast": False,
                        "reason": reason, "key_env": spec["env"]})
    return sources


def dan_model_policy(profile: dict) -> dict:
    source = validate_model_source("dan", profile)
    policy = {"model": profile.get("model", ""), "provider": source}
    if source != "native":
        policy.update(base_url=API_PROVIDERS[source]["url"], reasoning_effort=profile.get("effort", ""))
    elif profile.get("base_url"):
        policy["base_url"] = profile["base_url"]
    return policy


class ReasoningProvider:
    """Apply one run's provider reasoning choice without changing global config."""
    def __init__(self, provider, effort: str, source: str = "openrouter"):
        self.provider, self.effort, self.source = provider, effort, source

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def options(self, kwargs):
        if self.source != "openrouter":
            return {**kwargs, "reasoning_effort": self.effort}
        extra = dict(kwargs.get("extra_body") or {})
        extra["reasoning"] = {"effort": self.effort}
        return {**kwargs, "extra_body": extra, "reasoning": {"effort": self.effort}}

    async def complete(self, *args, **kwargs):
        return await self.provider.complete(*args, **self.options(kwargs))

    async def stream(self, *args, **kwargs):
        async for chunk in self.provider.stream(*args, **self.options(kwargs)):
            yield chunk
