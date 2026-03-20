"""Token estimation utilities — extracted from chat_manager for shared use.

Part of Plan 14-3: reusable token counting across engine and server code.
"""

from __future__ import annotations

try:
    import tiktoken
    _tiktoken_available = True
except ImportError:
    _tiktoken_available = False


def estimate_tokens(text: str, model: str = "") -> int:
    """Estimate token count for *text*.

    Uses ``tiktoken`` when available; falls back to ``len(text) // 4``
    (character approximation) otherwise.
    """
    if _tiktoken_available:
        try:
            enc = tiktoken.encoding_for_model(model)
            return len(enc.encode(text))
        except KeyError:
            try:
                enc = tiktoken.get_encoding("cl100k_base")
                return len(enc.encode(text))
            except Exception:
                pass
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
