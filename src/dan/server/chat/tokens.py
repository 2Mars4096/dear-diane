"""Token estimation and context window management."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

try:
    import tiktoken
    from tiktoken.model import MODEL_PREFIX_TO_ENCODING, MODEL_TO_ENCODING
    _tiktoken_available = True
except ImportError:
    tiktoken = None
    MODEL_PREFIX_TO_ENCODING = {}
    MODEL_TO_ENCODING = {}
    _tiktoken_available = False

_RECENT_MESSAGES_COUNT = int(os.environ.get("DAN_CHAT_RECENT_MESSAGES", "10"))

MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-5.4": 1_000_000,
    "gpt-3.5-turbo": 16_385,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o1-preview": 128_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    "o4-mini": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-haiku": 200_000,
    "gemini-3.1": 1_048_576,
    "gemini-1.5-pro": 1_000_000,
    "gemini-1.5-flash": 1_000_000,
    "gemini-2.0-flash": 1_000_000,
    "glm-4.7": 200_000,
    "glm-5": 200_000,
    "minimax-m2.5": 204_800,
    "kimi-k2.5": 256_000,
    "deepseek-chat": 64_000,
    "deepseek-reasoner": 64_000,
}

_DEFAULT_CONTEXT_WINDOW = 128_000
_DEFAULT_ENCODING_NAME = "cl100k_base"
_NORMALIZED_CONTEXT_WINDOWS: list[tuple[str, int]] = sorted(
    (
        (re.sub(r"[^a-z0-9]+", "", key.lower()), window)
        for key, window in MODEL_CONTEXT_WINDOWS.items()
    ),
    key=lambda item: len(item[0]),
    reverse=True,
)


def _get_context_window(model: str) -> int:
    """Look up the context window for a model, with prefix fallback."""
    normalized = re.sub(r"[^a-z0-9]+", "", model.lower())
    for key, window in _NORMALIZED_CONTEXT_WINDOWS:
        if normalized.startswith(key):
            return window
    return _DEFAULT_CONTEXT_WINDOW


def _completion_max_tokens(model: str, *, target_ratio: float = 0.5) -> int:
    """Reserve a generous but bounded output budget for chat completions."""
    context_window = _get_context_window(model)
    if context_window >= 1_000_000:
        soft_cap = 64_000
    elif context_window >= 200_000:
        soft_cap = 48_000
    elif context_window >= 128_000:
        soft_cap = 32_000
    else:
        soft_cap = 16_384
    return min(soft_cap, int(context_window * target_ratio))


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
    """Estimate token count. Uses tiktoken when available, character approximation otherwise."""
    if _tiktoken_available and tiktoken is not None:
        try:
            enc = tiktoken.get_encoding(_encoding_name_for_model(model))
            return len(enc.encode(text))
        except Exception:
            pass
    return len(text) // 4


def _estimate_messages_tokens(messages: list[dict[str, str]], model: str = "") -> int:
    """Estimate total tokens for a list of messages (includes per-message overhead)."""
    total = 0
    for msg in messages:
        total += 4 + estimate_tokens(msg.get("content", ""), model)
    return total


_TRUNCATE_RE = re.compile(r"(?<=[.!?])\s+")


def _truncate_assistant_message(text: str) -> str:
    """Extractive truncation: keep first and last sentence."""
    sentences = _TRUNCATE_RE.split(text.strip())
    if len(sentences) <= 3:
        return text
    return f"{sentences[0]} [...] {sentences[-1]}"


_TOOL_SCHEMA_OVERHEAD_TOKENS = 2000

_COMPACT_RATIO_OVERRIDE = float(os.environ.get("DAN_COMPACT_CONTEXT_RATIO", "0"))


def _default_compact_ratio(context_window: int) -> float:
    """Pick a compaction ratio based on window size.

    Larger windows can afford a higher ratio because the output budget
    is soft-capped (not proportional), leaving more room for input.
    """
    if context_window >= 1_000_000:
        return 0.80
    if context_window >= 200_000:
        return 0.70
    if context_window >= 128_000:
        return 0.65
    return 0.55


def _compact_context(
    messages: list[dict[str, Any]],
    model: str,
    *,
    target_ratio: float | None = None,
) -> list[dict[str, Any]]:
    """Dynamically compact messages to fit within target_ratio of context window.

    Accounts for tool schema overhead (~2K tokens) that isn't in messages.

    Strategy (applied in order until budget is met):
    1. Truncate tool results (oldest first, preserve most recent 2)
    2. Summarize old assistant messages to first+last sentence
    3. Drop oldest non-system bundles (keep system + last 6 bundles)

    Important: assistant messages that contain `tool_calls` and their following
    `tool` responses are treated as an atomic bundle so the transcript stays
    valid for tool-calling models. Any orphan `tool` messages are dropped so a
    previously malformed transcript does not keep propagating invalid tool-call
    state through later follow-up completions.
    """
    context_window = _get_context_window(model)
    if target_ratio is None:
        target_ratio = _COMPACT_RATIO_OVERRIDE if _COMPACT_RATIO_OVERRIDE > 0 else _default_compact_ratio(context_window)
    budget = int(context_window * target_ratio) - _TOOL_SCHEMA_OVERHEAD_TOKENS
    if budget < 4000:
        budget = 4000

    msgs: list[dict[str, Any]] = []
    allow_tool_messages = False
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            if allow_tool_messages:
                msgs.append(msg)
            else:
                logger.debug("Dropping orphan tool message during context compaction")
            continue
        msgs.append(msg)
        allow_tool_messages = (
            role == "assistant"
            and isinstance(msg.get("tool_calls"), list)
            and bool(msg.get("tool_calls"))
        )

    def _est() -> int:
        total = 0
        for m in msgs:
            c = m.get("content")
            if isinstance(c, str):
                total += 4 + estimate_tokens(c, model)
            tc = m.get("tool_calls")
            if isinstance(tc, list):
                total += len(json.dumps(tc, default=str)) // 4
        return total

    if _est() <= budget:
        return msgs

    tool_indices = [i for i, m in enumerate(msgs) if m.get("role") == "tool"]
    for cap in (1500, 500, 200):
        for i in tool_indices[:-2]:
            content = msgs[i].get("content", "")
            if len(content) > cap:
                msgs[i] = {**msgs[i], "content": content[:cap] + "\n[...truncated]"}
        if _est() <= budget:
            logger.debug("Context compacted at pass 1 (cap=%d), %d tokens", cap, _est())
            return msgs

    assistant_indices = [i for i, m in enumerate(msgs) if m.get("role") == "assistant"]
    for i in assistant_indices[:-2]:
        content = msgs[i].get("content", "")
        if len(content) > 200:
            msgs[i] = {**msgs[i], "content": _truncate_assistant_message(content)}
    if _est() <= budget:
        logger.debug("Context compacted at pass 2, %d tokens", _est())
        return msgs

    system = [m for m in msgs if m.get("role") == "system"]
    non_system = [m for m in msgs if m.get("role") != "system"]

    bundles: list[list[dict[str, Any]]] = []
    i = 0
    while i < len(non_system):
        msg = non_system[i]
        bundle = [msg]
        if msg.get("role") == "assistant" and isinstance(msg.get("tool_calls"), list) and msg.get("tool_calls"):
            i += 1
            while i < len(non_system) and non_system[i].get("role") == "tool":
                bundle.append(non_system[i])
                i += 1
            bundles.append(bundle)
            continue
        bundles.append(bundle)
        i += 1

    keep_tail = min(6, len(bundles))
    dropped = len(bundles) - keep_tail
    if dropped > 0:
        summary = f"[{dropped} earlier exchanges compacted to fit context window]"
        kept_tail: list[dict[str, Any]] = [{"role": "user", "content": summary}]
        for bundle in bundles[-keep_tail:]:
            kept_tail.extend(bundle)
        msgs = system + kept_tail
        logger.info(
            "Context compaction pass 3: dropped %d bundles, %d remain, ~%d tokens",
            dropped, len(msgs), _est(),
        )

    return msgs


def compact_history(
    messages: list[dict[str, str]],
    max_tokens: int,
    model: str = "",
    recent_count: int | None = None,
) -> list[dict[str, str]]:
    """Compact message history to fit within token budget.

    Priority (highest first):
    1. System prompt — always kept in full.
    2. Most recent ``recent_count`` messages — always kept in full.
    3. Older assistant messages — truncated to first + last sentence.
    4. Oldest messages — dropped entirely if still over budget.
    """
    if not messages:
        return messages

    if recent_count is None:
        recent_count = _RECENT_MESSAGES_COUNT

    total = _estimate_messages_tokens(messages, model)
    if total <= max_tokens:
        return messages

    system = [m for m in messages if m.get("role") == "system"]
    conv = [m for m in messages if m.get("role") != "system"]

    system_tokens = _estimate_messages_tokens(system, model)
    budget = max_tokens - system_tokens

    if budget <= 0:
        return system + conv[-1:] if conv else system

    if len(conv) > recent_count:
        recent = conv[-recent_count:]
        older = conv[:-recent_count]
    else:
        recent = conv
        older = []

    recent_tokens = _estimate_messages_tokens(recent, model)

    if recent_tokens > budget:
        result = list(recent)
        while len(result) > 1 and _estimate_messages_tokens(result, model) > budget:
            result.pop(0)
        logger.info(
            "Compacted history: kept %d of %d messages (recent-only mode)",
            len(system) + len(result), len(messages),
        )
        return system + result

    older_budget = budget - recent_tokens

    if not older:
        return system + recent

    truncated: list[dict[str, str]] = []
    for m in older:
        content = m.get("content", "")
        if m.get("role") == "assistant" and len(content) > 200:
            truncated.append({**m, "content": _truncate_assistant_message(content)})
        else:
            truncated.append(m)

    older_tokens = _estimate_messages_tokens(truncated, model)
    if older_tokens <= older_budget:
        logger.info(
            "Compacted history: truncated %d older assistant messages",
            sum(1 for o, t in zip(older, truncated) if o is not t),
        )
        return system + truncated + recent

    while truncated and _estimate_messages_tokens(truncated, model) > older_budget:
        truncated.pop(0)

    total_kept = len(system) + len(truncated) + len(recent)
    logger.info(
        "Compacted history: %d → %d messages (dropped %d oldest)",
        len(messages), total_kept, len(messages) - total_kept,
    )
    return system + truncated + recent


# ---------------------------------------------------------------------------
# Context pressure hint — injected when conversation is getting large
# ---------------------------------------------------------------------------

_CONTEXT_PRESSURE_THRESHOLD = float(os.environ.get("DAN_CONTEXT_PRESSURE_THRESHOLD", "0.60"))

_CONTEXT_PRESSURE_HINT = (
    "[System note: the conversation context is getting large. "
    "Save intermediate results to a file with file_write before making more tool calls. "
    "Keep your response concise and focused.]"
)


def context_pressure_hint(messages: list[dict[str, Any]], model: str) -> str | None:
    """Return a short behavioral hint if context usage exceeds the pressure threshold.

    Returns ``None`` when context is comfortable.  The caller should inject
    the returned string as a system-role message right before the LLM call
    so the model can adapt its behavior (write to file, be concise).
    """
    context_window = _get_context_window(model)
    if context_window <= 0:
        return None
    used = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            used += 4 + estimate_tokens(c, model)
        tc = m.get("tool_calls")
        if isinstance(tc, list):
            used += len(json.dumps(tc, default=str)) // 4
    ratio = used / context_window
    if ratio >= _CONTEXT_PRESSURE_THRESHOLD:
        logger.debug("Context pressure: %.1f%% of %dK window", ratio * 100, context_window // 1000)
        return _CONTEXT_PRESSURE_HINT
    return None
