"""Anthropic LLM provider — wraps AsyncAnthropic with message format translation."""

from __future__ import annotations

from typing import Any, AsyncIterator

from dan.providers import CompletionResult, ProviderConfig, StreamChunk


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
        self._client = AsyncAnthropic(api_key=config.api_key)

    @staticmethod
    def _split_system(
        messages: list[dict[str, str]],
    ) -> tuple[str | None, list[dict[str, str]]]:
        """Extract system messages (Anthropic uses a top-level `system` param)."""
        system_parts: list[str] = []
        non_system: list[dict[str, str]] = []
        for msg in messages:
            if msg.get("role") == "system":
                system_parts.append(msg.get("content", ""))
            else:
                non_system.append(msg)
        system_text = "\n\n".join(system_parts) if system_parts else None
        return system_text, non_system

    async def complete(
        self,
        messages: list[dict[str, str]],
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

        resp = await self._client.messages.create(**call_kwargs)
        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text += getattr(block, "text", "")
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

        accumulated = ""
        async with self._client.messages.stream(**call_kwargs) as stream:
            async for text in stream.text_stream:
                accumulated += text
                yield StreamChunk(delta=text, accumulated=accumulated)

        final_message = await stream.get_final_message()
        usage = self._extract_usage(final_message)
        yield StreamChunk(
            delta="", accumulated=accumulated, done=True, usage=usage,
        )

    @staticmethod
    def _extract_usage(resp: Any) -> dict[str, int] | None:
        usage = getattr(resp, "usage", None)
        if usage is None:
            return None
        return {
            "prompt_tokens": getattr(usage, "input_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "output_tokens", 0) or 0,
            "total_tokens": (getattr(usage, "input_tokens", 0) or 0)
            + (getattr(usage, "output_tokens", 0) or 0),
        }
