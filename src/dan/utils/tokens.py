"""Token estimation utilities — extracted from chat_manager for shared use.

Part of Plan 14-3: reusable token counting across engine and server code.
"""

from __future__ import annotations

try:
    import tiktoken
    from tiktoken.model import MODEL_PREFIX_TO_ENCODING, MODEL_TO_ENCODING
    _tiktoken_available = True
except ImportError:
    tiktoken = None
    MODEL_PREFIX_TO_ENCODING = {}
    MODEL_TO_ENCODING = {}
    _tiktoken_available = False

_DEFAULT_ENCODING_NAME = "cl100k_base"


def _encoding_name_for_model(model: str) -> str:
    if not model:
        return _DEFAULT_ENCODING_NAME
    model_module = getattr(tiktoken, "model", None) if tiktoken is not None else None
    resolver = getattr(model_module, "encoding_name_for_model", None)
    if resolver is not None:
        try:
            return resolver(model)
        except KeyError:
            pass
        except Exception:
            pass
    normalized = model.lower()
    encoding_name = MODEL_TO_ENCODING.get(normalized)
    if encoding_name:
        return encoding_name
    for prefix, candidate in sorted(
        MODEL_PREFIX_TO_ENCODING.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if normalized.startswith(prefix):
            return candidate
    return _DEFAULT_ENCODING_NAME


def estimate_tokens(text: str, model: str = "") -> int:
    """Estimate token count for *text*.

    Uses ``tiktoken`` when available; falls back to ``len(text) // 4``
    (character approximation) otherwise.
    """
    if _tiktoken_available and tiktoken is not None:
        try:
            enc = tiktoken.get_encoding(_encoding_name_for_model(model))
            return len(enc.encode(text))
        except Exception:
            pass
    return len(text) // 4


def estimate_messages_tokens(
    messages: list[dict[str, str]], model: str = "",
) -> int:
    """Estimate total tokens for a list of chat messages.

    Includes ~4-token overhead per message for role/delimiters.
    """
    total = 0
    for msg in messages:
        total += 4 + estimate_tokens(msg.get("content", ""), model)
    return total
