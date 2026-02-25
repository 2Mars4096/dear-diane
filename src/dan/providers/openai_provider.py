"""OpenAI-compatible LLM provider — wraps AsyncOpenAI for any OpenAI-API endpoint."""

from __future__ import annotations

from typing import Any, AsyncIterator

from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from dan.providers import CompletionResult, ProviderConfig, StreamChunk


class OpenAIProvider:
    """Provider for OpenAI and any OpenAI-compatible endpoint (e.g. vectorengine.ai)."""

    def __init__(self, config: ProviderConfig) -> None:
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    @classmethod
    def from_client(cls, client: AsyncOpenAI) -> OpenAIProvider:
        """Wrap an existing AsyncOpenAI client (for backward compat / test injection)."""
        instance = object.__new__(cls)
        instance._client = client
        return instance

    async def complete(
        self,
        messages: list[dict[str, str]],
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

        resp = await self._client.chat.completions.create(**call_kwargs)
        text = resp.choices[0].message.content or ""
        usage = self._extract_usage(resp)
        return CompletionResult(text=text, usage=usage, model=model)

    async def stream(
        self,
        messages: list[dict[str, str]],
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
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }
