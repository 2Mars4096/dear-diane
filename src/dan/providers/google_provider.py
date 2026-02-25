"""Google Gemini LLM provider — wraps google.generativeai."""

from __future__ import annotations

from typing import Any, AsyncIterator

from dan.providers import CompletionResult, ProviderConfig, StreamChunk


class GoogleProvider:
    """Provider for Google Gemini models via the generativeai SDK."""

    def __init__(self, config: ProviderConfig) -> None:
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError(
                "GoogleProvider requires the 'google-generativeai' package. "
                "Install with: pip install google-generativeai"
            )
        genai.configure(api_key=config.api_key)
        self._genai = genai

    @staticmethod
    def _to_gemini_messages(
        messages: list[dict[str, str]],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Convert OpenAI-style messages to Gemini format.

        Gemini uses 'user'/'model' roles and a separate system_instruction.
        """
        system_parts: list[str] = []
        history: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_parts.append(content)
            elif role == "assistant":
                history.append({"role": "model", "parts": [content]})
            else:
                history.append({"role": "user", "parts": [content]})
        system_text = "\n\n".join(system_parts) if system_parts else None
        return system_text, history

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        system_text, history = self._to_gemini_messages(messages)

        gen_config: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            gen_config["max_output_tokens"] = max_tokens

        model_kwargs: dict[str, Any] = {}
        if system_text:
            model_kwargs["system_instruction"] = system_text

        gm = self._genai.GenerativeModel(model, **model_kwargs)
        chat = gm.start_chat(history=history[:-1] if len(history) > 1 else [])

        last_msg = history[-1]["parts"][0] if history else ""
        resp = await chat.send_message_async(
            last_msg, generation_config=gen_config,
        )
        text = resp.text or ""
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
        system_text, history = self._to_gemini_messages(messages)

        gen_config: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            gen_config["max_output_tokens"] = max_tokens

        model_kwargs: dict[str, Any] = {}
        if system_text:
            model_kwargs["system_instruction"] = system_text

        gm = self._genai.GenerativeModel(model, **model_kwargs)
        chat = gm.start_chat(history=history[:-1] if len(history) > 1 else [])

        last_msg = history[-1]["parts"][0] if history else ""
        accumulated = ""
        resp = await chat.send_message_async(
            last_msg, generation_config=gen_config, stream=True,
        )
        async for chunk in resp:
            delta = chunk.text or ""
            accumulated += delta
            yield StreamChunk(delta=delta, accumulated=accumulated)

        usage = self._extract_usage(resp)
        yield StreamChunk(
            delta="", accumulated=accumulated, done=True, usage=usage,
        )

    @staticmethod
    def _extract_usage(resp: Any) -> dict[str, int] | None:
        metadata = getattr(resp, "usage_metadata", None)
        if metadata is None:
            return None
        prompt = getattr(metadata, "prompt_token_count", 0) or 0
        completion = getattr(metadata, "candidates_token_count", 0) or 0
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }
