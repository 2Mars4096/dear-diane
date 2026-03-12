"""Anthropic LLM provider — wraps AsyncAnthropic with message format translation."""

from __future__ import annotations

from typing import Any, AsyncIterator

from dan.providers import (
    CompletionResult,
    ProviderConfig,
    StreamChunk,
    resolve_provider_timeout,
)


class AnthropicProvider:
    """Provider for Anthropic's Claude models via the native API."""

    def __init__(self, config: ProviderConfig) -> None:
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            raise ImportError(
                "AnthropicProvider requires the 'anthropic' package. "
                "Install with: pip install anthropic"
            )
        self._timeout_seconds = resolve_provider_timeout(config)
        self._client = AsyncAnthropic(
            api_key=config.api_key,
            timeout=self._timeout_seconds,
        )

    _CACHE_TOKEN_THRESHOLD = 1024

    def apply_cache_hints(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Add ``cache_control`` breakpoints to long system messages."""
        for msg in messages:
            if msg.get("role") != "system":
                continue
            text = msg.get("content", "")
            estimated_tokens = len(text) // 4
            if estimated_tokens > self._CACHE_TOKEN_THRESHOLD:
                msg["cache_control"] = {"type": "ephemeral"}
        return messages

    @staticmethod
    def _split_system(
        messages: list[dict[str, Any]],
    ) -> tuple[str | list[dict[str, Any]] | None, list[dict[str, Any]]]:
        """Extract system messages (Anthropic uses a top-level ``system`` param).

        When any system message carries ``cache_control``, the system value is
        returned as a list of content blocks (required by the Anthropic API for
        cache breakpoints).  Otherwise a plain string is returned.
        """
        system_parts: list[tuple[str, dict[str, Any] | None]] = []
        non_system: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "system":
                system_parts.append(
                    (msg.get("content", ""), msg.get("cache_control"))
                )
            else:
                non_system.append(msg)

        if not system_parts:
            return None, non_system

        has_cache = any(cc for _, cc in system_parts)
        if has_cache:
            blocks: list[dict[str, Any]] = []
            for text, cc in system_parts:
                block: dict[str, Any] = {"type": "text", "text": text}
                if cc:
                    block["cache_control"] = cc
                blocks.append(block)
            return blocks, non_system

        system_text = "\n\n".join(text for text, _ in system_parts)
        return system_text, non_system

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        system_text, user_messages = self._split_system(messages)
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": user_messages,
            "temperature": temperature,
            "max_tokens": max_tokens or 4096,
            **kwargs,
        }
        if system_text:
            call_kwargs["system"] = system_text
        call_kwargs.setdefault("timeout", self._timeout_seconds)

        resp = await self._client.messages.create(**call_kwargs)
        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text += getattr(block, "text", "")
        usage = self._extract_usage(resp)
        cached_input, cache_write = self._extract_cache_tokens(resp)
        finish_reason = getattr(resp, "stop_reason", "") or ""
        return CompletionResult(
            text=text, usage=usage, model=model,
            cached_input_tokens=cached_input,
            cache_write_tokens=cache_write,
            finish_reason=finish_reason,
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        system_text, user_messages = self._split_system(messages)
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": user_messages,
            "temperature": temperature,
            "max_tokens": max_tokens or 4096,
            **kwargs,
        }
        if system_text:
            call_kwargs["system"] = system_text
        call_kwargs.setdefault("timeout", self._timeout_seconds)

        accumulated = ""
        async with self._client.messages.stream(**call_kwargs) as stream:
            async for text in stream.text_stream:
                accumulated += text
                yield StreamChunk(delta=text, accumulated=accumulated)

        final_message = await stream.get_final_message()
        usage = self._extract_usage(final_message)
        if usage is not None:
            cached_input, cache_write = self._extract_cache_tokens(final_message)
            usage["cached_input_tokens"] = cached_input
            usage["cache_write_tokens"] = cache_write
        yield StreamChunk(
            delta="", accumulated=accumulated, done=True, usage=usage,
        )

    @staticmethod
    def _extract_usage(resp: Any) -> dict[str, int] | None:
        usage = getattr(resp, "usage", None)
        if usage is None:
            return None
        cached_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
        return {
            "prompt_tokens": getattr(usage, "input_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "output_tokens", 0) or 0,
            "total_tokens": (getattr(usage, "input_tokens", 0) or 0)
            + (getattr(usage, "output_tokens", 0) or 0),
            "cached_input_tokens": cached_read,
            "cache_write_tokens": cache_create,
        }

    @staticmethod
    def _extract_cache_tokens(resp: Any) -> tuple[int, int]:
        """Return ``(cached_input_tokens, cache_write_tokens)``."""
        usage = getattr(resp, "usage", None)
        if usage is None:
            return 0, 0
        return (
            getattr(usage, "cache_read_input_tokens", 0) or 0,
            getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )
