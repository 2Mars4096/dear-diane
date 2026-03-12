"""OpenAI-compatible LLM provider — wraps AsyncOpenAI for any OpenAI-API endpoint."""

from __future__ import annotations

from typing import Any, AsyncIterator

from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from dan.providers import (
    CompletionResult,
    ProviderConfig,
    StreamChunk,
    resolve_provider_timeout,
)


class OpenAIProvider:
    """Provider for OpenAI and any OpenAI-compatible endpoint (e.g. vectorengine.ai)."""

    def __init__(self, config: ProviderConfig) -> None:
        self._timeout_seconds = resolve_provider_timeout(config)
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=self._timeout_seconds,
        )

    @classmethod
    def from_client(cls, client: AsyncOpenAI) -> OpenAIProvider:
        """Wrap an existing AsyncOpenAI client (for backward compat / test injection)."""
        instance = object.__new__(cls)
        instance._client = client
        timeout = getattr(client, "timeout", None)
        instance._timeout_seconds = (
            float(timeout) if isinstance(timeout, (int, float)) else None
        )
        return instance

    @staticmethod
    def apply_cache_hints(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Ensure system messages lead for stable prefix caching.

        OpenAI auto-caches identical request prefixes.  Placing all system
        messages first and keeping tool definitions in deterministic order
        maximises prefix overlap across calls.
        """
        system: list[dict[str, Any]] = []
        non_system: list[dict[str, Any]] = []
        for m in messages:
            (system if m.get("role") == "system" else non_system).append(m)
        return system + non_system

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        }
        if max_tokens is not None:
            call_kwargs["max_tokens"] = max_tokens
        if self._timeout_seconds is not None:
            call_kwargs.setdefault("timeout", self._timeout_seconds)

        resp = await self._client.chat.completions.create(**call_kwargs)
        message = resp.choices[0].message
        text = message.content or ""
        usage = self._extract_usage(resp)
        tool_calls = None
        if hasattr(message, "tool_calls") and message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
        cached_input = (usage or {}).get("cached_input_tokens", 0)
        finish_reason = getattr(resp.choices[0], "finish_reason", "") or ""
        return CompletionResult(
            text=text, usage=usage, model=model, tool_calls=tool_calls,
            cached_input_tokens=cached_input,
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
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
            **kwargs,
        }
        if max_tokens is not None:
            call_kwargs["max_tokens"] = max_tokens
        if self._timeout_seconds is not None:
            call_kwargs.setdefault("timeout", self._timeout_seconds)

        stream = await self._client.chat.completions.create(**call_kwargs)
        accumulated = ""
        last_usage = None
        async for chunk in stream:
            if chunk.choices:
                delta = chunk.choices[0].delta.content or ""
                accumulated += delta
                yield StreamChunk(delta=delta, accumulated=accumulated)
            usage = self._extract_usage(chunk)
            if usage:
                last_usage = usage

        yield StreamChunk(
            delta="", accumulated=accumulated, done=True, usage=last_usage
        )

    @staticmethod
    def _extract_usage(obj: Any) -> dict[str, int] | None:
        usage = getattr(obj, "usage", None)
        if usage is None:
            return None
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details:
            cached = getattr(details, "cached_tokens", 0) or 0
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
            "cached_input_tokens": cached,
        }
