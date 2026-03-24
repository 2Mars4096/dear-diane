"""llm_core — unified LLM gateway wrapping providers/."""

from dan.llm_core.config import GatewayConfig
from dan.llm_core.gateway import ModelGateway, gateway_llm_call, gateway_meta_llm_call
from dan.llm_core.types import GatewayCall


def __getattr__(name: str):
    """Lazy re-exports from providers/ to avoid circular imports at init time."""
    _PROVIDER_REEXPORTS = {
        "BudgetExceededError": ("dan.providers", "BudgetExceededError"),
        "CompletionResult": ("dan.providers", "CompletionResult"),
        "CostTracker": ("dan.providers.cost_tracker", "CostTracker"),
        "LLMAuthenticationError": ("dan.providers", "LLMAuthenticationError"),
        "LLMProvider": ("dan.providers", "LLMProvider"),
        "ModelBehaviorProfile": ("dan.providers", "ModelBehaviorProfile"),
        "ModelSelector": ("dan.providers.model_selector", "ModelSelector"),
        "ProviderConfig": ("dan.providers", "ProviderConfig"),
        "ProviderRegistry": ("dan.providers.registry", "ProviderRegistry"),
        "StreamChunk": ("dan.providers", "StreamChunk"),
    }
    if name == "build_gateway":
        from dan.llm_core.factory import build_gateway as _bg
        return _bg
    if name in _PROVIDER_REEXPORTS:
        mod_path, attr = _PROVIDER_REEXPORTS[name]
        import importlib
        mod = importlib.import_module(mod_path)
        return getattr(mod, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BudgetExceededError",
    "CompletionResult",
    "CostTracker",
    "GatewayCall",
    "GatewayConfig",
    "LLMAuthenticationError",
    "LLMProvider",
    "ModelBehaviorProfile",
    "ModelGateway",
    "ModelSelector",
    "gateway_llm_call",
    "gateway_meta_llm_call",
    "ProviderConfig",
    "ProviderRegistry",
    "StreamChunk",
    "build_gateway",
]
