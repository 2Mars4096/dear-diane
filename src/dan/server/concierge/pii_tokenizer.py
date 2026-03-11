"""PII tokenization — replace sensitive data with semantic placeholders before LLM calls.

Prevents real names, addresses, passwords, financial data, and other PII from
being transmitted to external LLM APIs.  Opt-in via ``DAN_PII_PROTECTION=1``.

The ``PIISession`` mapping table exists only in memory — never persisted to
disk.  If the process crashes, the session is lost; deterministic mapping from
``SensitiveWordRegistry`` can reconstruct placeholders for known words but
previously-seen LLM responses containing placeholders cannot be retroactively
detokenized.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import time
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, Field

from dan.providers import CompletionResult, LLMProvider, StreamChunk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category taxonomy
# ---------------------------------------------------------------------------

PIICategory = Literal[
    "name", "address", "email", "phone", "password", "financial", "id_number", "custom"
]

_CATEGORY_PREFIX: dict[str, str] = {
    "name": "PERSON",
    "address": "ADDRESS",
    "email": "EMAIL",
    "phone": "PHONE",
    "password": "PASSWORD",
    "financial": "FINANCIAL",
    "id_number": "ID",
    "custom": "CUSTOM",
}

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SensitiveWord(BaseModel):
    """A single user-defined sensitive word/phrase."""

    value: str
    category: PIICategory


class SensitiveWordRegistry(BaseModel):
    """Loads / persists user-defined sensitive words from ``~/.dan/sensitive_words.json``."""

    words: list[SensitiveWord] = Field(default_factory=list)
    _path: Path | None = None

    def model_post_init(self, __context: Any) -> None:
        if self._path is None:
            object.__setattr__(self, "_path", Path.home() / ".dan" / "sensitive_words.json")

    @classmethod
    def load(cls, path: Path | None = None) -> SensitiveWordRegistry:
        p = path or Path.home() / ".dan" / "sensitive_words.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            reg = cls(words=[SensitiveWord(**w) for w in data.get("words", [])])
        else:
            reg = cls()
        object.__setattr__(reg, "_path", p)
        return reg

    def save(self, path: Path | None = None) -> None:
        p = path or self._path or Path.home() / ".dan" / "sensitive_words.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"words": [w.model_dump() for w in self.words]}, indent=2),
            encoding="utf-8",
        )
        os.chmod(p, 0o600)

    def add(self, value: str, category: PIICategory) -> None:
        if not any(w.value.lower() == value.lower() and w.category == category for w in self.words):
            self.words.append(SensitiveWord(value=value, category=category))

    def remove(self, value: str) -> bool:
        before = len(self.words)
        self.words = [w for w in self.words if w.value.lower() != value.lower()]
        return len(self.words) < before

    def list_words(self) -> list[SensitiveWord]:
        return list(self.words)


class PIISession:
    """Per-conversation mapping table — exists only in memory."""

    def __init__(self) -> None:
        self._to_placeholder: dict[str, str] = {}
        self._to_original: dict[str, str] = {}
        self._counters: dict[str, int] = {}

    def get_or_create_placeholder(self, value: str, category: PIICategory) -> str:
        key = value.lower()
        if key in self._to_placeholder:
            return self._to_placeholder[key]
        prefix = _CATEGORY_PREFIX.get(category, "CUSTOM")
        count = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = count
        placeholder = f"[{prefix}_{count}]"
        self._to_placeholder[key] = placeholder
        self._to_original[placeholder.lower()] = value
        return placeholder

    def lookup_original(self, placeholder: str) -> str | None:
        normalized = _normalize_placeholder(placeholder)
        return self._to_original.get(normalized)

    @property
    def placeholder_to_original(self) -> dict[str, str]:
        return dict(self._to_original)

    @property
    def original_to_placeholder(self) -> dict[str, str]:
        return dict(self._to_placeholder)

    def clear(self) -> None:
        self._to_placeholder.clear()
        self._to_original.clear()
        self._counters.clear()


_PII_SESSIONS: dict[str, PIISession] = {}
_PII_SESSION_LAST_USED: dict[str, float] = {}
_PII_SESSION_TTL_SECONDS = max(
    60,
    int(os.environ.get("DAN_PII_SESSION_TTL_SECONDS", "3600")),
)


def _purge_expired_pii_sessions() -> None:
    cutoff = time.time() - _PII_SESSION_TTL_SECONDS
    expired = [
        session_key
        for session_key, last_used in _PII_SESSION_LAST_USED.items()
        if last_used < cutoff
    ]
    for session_key in expired:
        _PII_SESSIONS.pop(session_key, None)
        _PII_SESSION_LAST_USED.pop(session_key, None)


def get_pii_session(session_key: str | None) -> PIISession:
    """Return the shared in-memory PII session for a conversation/workflow."""
    if not session_key:
        return PIISession()
    _purge_expired_pii_sessions()
    session = _PII_SESSIONS.get(session_key)
    if session is None:
        session = PIISession()
        _PII_SESSIONS[session_key] = session
    _PII_SESSION_LAST_USED[session_key] = time.time()
    return session


def clear_pii_session(session_key: str | None) -> bool:
    """Clear the shared PII session for *session_key* if it exists."""
    if not session_key:
        return False
    _PII_SESSION_LAST_USED.pop(session_key, None)
    return _PII_SESSIONS.pop(session_key, None) is not None


# ---------------------------------------------------------------------------
# ContextVar for request-scoped PIISession (31-10 §4-2)
# ---------------------------------------------------------------------------

current_pii_session: ContextVar[PIISession | None] = ContextVar(
    "current_pii_session", default=None,
)


def get_current_pii_session() -> PIISession | None:
    """Return the PIISession bound to the current async context, or None."""
    return current_pii_session.get()


def set_current_pii_session(session: PIISession | None) -> Token[PIISession | None]:
    """Bind *session* to the current async context; return a reset token."""
    return current_pii_session.set(session)


def _normalize_placeholder(text: str) -> str:
    """Normalize a placeholder to canonical lowercase form ``[PREFIX_N]``."""
    t = text.strip().lower()
    if not t.startswith("["):
        t = "[" + t
    if not t.endswith("]"):
        t = t + "]"
    return t


# ---------------------------------------------------------------------------
# Auto-detection patterns
# ---------------------------------------------------------------------------

_AUTO_PATTERNS: list[tuple[re.Pattern[str], PIICategory]] = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "email"),
    (re.compile(r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "phone"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "id_number"),  # SSN
    (re.compile(r"\b(?:\d[ -]?){15}\d\b"), "financial"),  # 16-digit credit card
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "id_number"),  # IPv4
]


def detect_auto_pii(text: str) -> list[tuple[str, PIICategory]]:
    """Return (matched_text, category) pairs for regex-detected PII."""
    found: list[tuple[str, PIICategory]] = []
    for pattern, category in _AUTO_PATTERNS:
        for m in pattern.finditer(text):
            found.append((m.group(0), category))
    return found


# ---------------------------------------------------------------------------
# Tokenizer (outbound)
# ---------------------------------------------------------------------------


_CODE_BLOCK_RE = re.compile(r"(```[\s\S]*?```|`[^`]+`)")


def tokenize(text: str, session: PIISession, registry: SensitiveWordRegistry) -> str:
    """Replace sensitive words and auto-detected PII with semantic placeholders.

    When ``DAN_PII_SKIP_CODE_BLOCKS=1``, fenced and inline code blocks are
    preserved verbatim — only the surrounding prose is tokenized.
    """
    if os.environ.get("DAN_PII_SKIP_CODE_BLOCKS") == "1":
        spans = list(_CODE_BLOCK_RE.finditer(text))
        if spans:
            parts: list[str] = []
            prev = 0
            for m in spans:
                parts.append(_tokenize_segment(text[prev:m.start()], session, registry))
                parts.append(m.group(0))
                prev = m.end()
            parts.append(_tokenize_segment(text[prev:], session, registry))
            return "".join(parts)

    return _tokenize_segment(text, session, registry)


def _tokenize_segment(text: str, session: PIISession, registry: SensitiveWordRegistry) -> str:
    """Core tokenization — replace registry words and auto-detected PII."""
    registry_words = sorted(registry.words, key=lambda w: -len(w.value))

    for word in registry_words:
        escaped = re.escape(word.value)
        pattern = re.compile(
            r"\b" + escaped + r"\b",
            re.IGNORECASE,
        )

        def _replace_word(m: re.Match[str], _cat: PIICategory = word.category) -> str:
            placeholder = session.get_or_create_placeholder(m.group(0), _cat)
            return placeholder

        # Handle possessives: "John's" → "[PERSON_1]'s"
        poss_pattern = re.compile(
            r"\b" + escaped + r"(?='s\b)",
            re.IGNORECASE,
        )
        text = poss_pattern.sub(
            lambda m, _cat=word.category: session.get_or_create_placeholder(m.group(0), _cat),
            text,
        )

        text = pattern.sub(_replace_word, text)

    auto_hits = detect_auto_pii(text)
    auto_hits.sort(key=lambda t: -len(t[0]))
    for matched, cat in auto_hits:
        placeholder = session.get_or_create_placeholder(matched, cat)
        text = text.replace(matched, placeholder)

    return text


# ---------------------------------------------------------------------------
# Detokenizer (inbound)
# ---------------------------------------------------------------------------


def detokenize(text: str, session: PIISession) -> str:
    """Restore placeholders to original values and warn on leaked PII."""
    _check_for_leaks(text, session)
    placeholder_re = re.compile(r"\[?([A-Za-z_]+_\d+)\]?")

    def _replace_placeholder(m: re.Match[str]) -> str:
        raw = m.group(0)
        normalized = _normalize_placeholder(m.group(1))
        original = session.lookup_original(normalized)
        if original is not None:
            return original
        return raw

    return placeholder_re.sub(_replace_placeholder, text)


def _check_for_leaks(text: str, session: PIISession) -> None:
    """Scan for original PII values that appear verbatim in the text."""
    text_lower = text.lower()
    for placeholder, original in session.placeholder_to_original.items():
        if original.lower() in text_lower:
            logger.warning(
                "PII leak detected: original value for %s found in response text",
                placeholder,
            )


# ---------------------------------------------------------------------------
# TokenizingProviderWrapper
# ---------------------------------------------------------------------------


class TokenizingProviderWrapper:
    """Wraps an ``LLMProvider`` to tokenize outbound messages and detokenize responses.

    If *session* is ``None``, the wrapper resolves the ``PIISession`` from the
    ``current_pii_session`` ``ContextVar`` on each call, enabling request-scoped
    tokenization without threading the session through every call site.
    """

    def __init__(
        self,
        provider: LLMProvider,
        session: PIISession | None = None,
        registry: SensitiveWordRegistry | None = None,
    ) -> None:
        self._provider = provider
        self._explicit_session = session
        self._registry = registry or SensitiveWordRegistry()

    @property
    def _session(self) -> PIISession:
        if self._explicit_session is not None:
            return self._explicit_session
        ctx_session = get_current_pii_session()
        if ctx_session is not None:
            return ctx_session
        return PIISession()

    def _tokenize_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for msg in messages:
            msg_copy = dict(msg)
            content = msg_copy.get("content")
            if isinstance(content, str):
                msg_copy["content"] = tokenize(content, self._session, self._registry)
            elif isinstance(content, list):
                new_parts: list[Any] = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        new_parts.append({**part, "text": tokenize(part["text"], self._session, self._registry)})
                    else:
                        new_parts.append(part)
                msg_copy["content"] = new_parts
            out.append(msg_copy)
        return out

    def _detokenize_value(self, value: Any) -> Any:
        """Recursively detokenize string leaves in provider responses."""
        if isinstance(value, str):
            return detokenize(value, self._session)
        if isinstance(value, list):
            return [self._detokenize_value(item) for item in value]
        if isinstance(value, dict):
            return {
                key: self._detokenize_value(item)
                for key, item in value.items()
            }
        return value

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        tokenized = self._tokenize_messages(messages)
        result = await self._provider.complete(tokenized, model, temperature, max_tokens, **kwargs)
        result.text = detokenize(result.text, self._session)
        if result.tool_calls is not None:
            result.tool_calls = self._detokenize_value(result.tool_calls)
        return result

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        tokenized = self._tokenize_messages(messages)
        accumulated_raw = ""
        stable_detokenized_acc = ""
        async for chunk in self._provider.stream(tokenized, model, temperature, max_tokens, **kwargs):
            accumulated_raw = chunk.accumulated
            # If the raw stream currently ends inside a placeholder token, only
            # emit the prefix before the unmatched "[". The placeholder segment
            # is unstable until the closing "]" arrives and can rewrite earlier
            # output when detokenized.
            stable_raw = accumulated_raw
            last_open = accumulated_raw.rfind("[")
            last_close = accumulated_raw.rfind("]")
            if last_open > last_close:
                stable_raw = accumulated_raw[:last_open]

            next_stable_acc = detokenize(stable_raw, self._session)
            if next_stable_acc.startswith(stable_detokenized_acc):
                detokenized_delta = next_stable_acc[len(stable_detokenized_acc):]
            else:
                detokenized_delta = ""
            stable_detokenized_acc = next_stable_acc

            detokenized_acc = detokenize(accumulated_raw, self._session)
            emitted_accumulated = detokenized_acc if chunk.done else next_stable_acc
            yield StreamChunk(
                delta=detokenized_delta,
                accumulated=emitted_accumulated,
                done=chunk.done,
                usage=chunk.usage,
            )


# ---------------------------------------------------------------------------
# Chat command handler
# ---------------------------------------------------------------------------


def is_pii_enabled() -> bool:
    return os.environ.get("DAN_PII_PROTECTION", "").strip() in ("1", "true", "yes")


def handle_pii_command(
    text: str,
    registry: SensitiveWordRegistry,
    *,
    session_key: str | None = None,
) -> str:
    """Handle ``/pii add|list|remove|clear-session`` commands.

    Returns a user-facing response string.
    """
    if not is_pii_enabled():
        return "PII protection is disabled. Set `DAN_PII_PROTECTION=1` to enable."

    parts = text.strip().split(None, 1)
    if len(parts) < 2:
        return (
            "Usage: `/pii add \"value\" --category <cat>`, "
            "`/pii list`, `/pii remove \"value\"`, `/pii clear-session`"
        )

    sub = parts[1].strip()

    if sub.startswith("add "):
        return _pii_add(sub[4:].strip(), registry)
    elif sub == "list":
        return _pii_list(registry)
    elif sub.startswith("remove "):
        return _pii_remove(sub[7:].strip(), registry)
    elif sub == "clear-session":
        if clear_pii_session(session_key):
            return "Session mappings cleared."
        return "No active PII session mappings for this conversation."
    else:
        return f"Unknown /pii subcommand: {sub.split()[0]}"


def _pii_add(args_str: str, registry: SensitiveWordRegistry) -> str:
    try:
        tokens = shlex.split(args_str)
    except ValueError:
        return "Invalid syntax. Use: `/pii add \"value\" --category <category>`"

    if not tokens:
        return "Missing value. Use: `/pii add \"value\" --category <category>`"

    value = tokens[0]
    category: PIICategory = "custom"

    i = 1
    while i < len(tokens):
        if tokens[i] == "--category" and i + 1 < len(tokens):
            cat = tokens[i + 1].lower()
            valid_categories = list(_CATEGORY_PREFIX.keys())
            if cat not in valid_categories:
                return f"Invalid category `{cat}`. Valid: {', '.join(valid_categories)}"
            category = cat  # type: ignore[assignment]
            i += 2
        else:
            i += 1

    registry.add(value, category)
    registry.save()
    return f"Added `{value}` as `{category}`."


def _pii_list(registry: SensitiveWordRegistry) -> str:
    words = registry.list_words()
    if not words:
        return "No sensitive words registered."
    lines = [f"- `{w.value}` ({w.category})" for w in words]
    return "**Protected words:**\n" + "\n".join(lines)


def _pii_remove(args_str: str, registry: SensitiveWordRegistry) -> str:
    try:
        tokens = shlex.split(args_str)
    except ValueError:
        return "Invalid syntax. Use: `/pii remove \"value\"`"

    if not tokens:
        return "Missing value. Use: `/pii remove \"value\"`"

    value = tokens[0]
    if registry.remove(value):
        registry.save()
        return f"Removed `{value}`."
    return f"`{value}` not found in registry."
