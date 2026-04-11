"""Anthropic LLM provider — wraps AsyncAnthropic with message format translation."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from dan.providers import (
    CompletionResult,
    LLMAuthenticationError,
    ProviderConfig,
    StreamChunk,
    resolve_provider_timeout,
)


class AnthropicProvider:
    """Provider for Anthropic's Claude models via the native API."""

    supports_exact_tool_choice = True
    supports_tool_calls = True
    assistant_replay_mode = "raw"

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

    @staticmethod
    def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if raw is None:
            return {}
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                return {"value": raw}
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
        return {"value": raw}

    @staticmethod
    def _stringify_tool_arguments(raw: Any) -> str:
        try:
            return json.dumps(raw if raw is not None else {}, sort_keys=True)
        except TypeError:
            return json.dumps({})

    @staticmethod
    def _serialize_content_block(block: Any) -> dict[str, Any]:
        if isinstance(block, dict):
            return dict(block)
        model_dump = getattr(block, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump(mode="python")
            except TypeError:
                dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped
        block_type = getattr(block, "type", None)
        if block_type == "text":
            return {
                "type": "text",
                "text": getattr(block, "text", ""),
            }
        if block_type == "tool_use":
            return {
                "type": "tool_use",
                "id": str(getattr(block, "id", "") or ""),
                "name": str(getattr(block, "name", "") or ""),
                "input": getattr(block, "input", {}) or {},
            }
        payload: dict[str, Any] = {"type": str(block_type or "")}
        for key in (
            "text",
            "thinking",
            "signature",
            "id",
            "name",
            "input",
            "cited_text",
            "title",
            "url",
            "source",
            "content",
            "citations",
        ):
            if hasattr(block, key):
                value = getattr(block, key)
                if value is not None:
                    payload[key] = value
        return payload

    @classmethod
    def _serialize_assistant_message(
        cls,
        *,
        text: str,
        tool_calls: list[dict[str, Any]],
        content_blocks: list[Any],
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": text if text.strip() else (None if tool_calls else text),
            "anthropic_content": [
                cls._serialize_content_block(block) for block in content_blocks
            ],
        }
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message

    @classmethod
    def _tool_schema_to_anthropic(cls, tool: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(tool, dict):
            return None
        func = tool.get("function")
        if not isinstance(func, dict):
            return None
        name = str(func.get("name") or "").strip()
        if not name:
            return None
        payload: dict[str, Any] = {
            "name": name,
            "input_schema": func.get("parameters") or {"type": "object", "properties": {}},
        }
        description = str(func.get("description") or "").strip()
        if description:
            payload["description"] = description
        return payload

    @classmethod
    def _tool_choice_to_anthropic(
        cls,
        tool_choice: Any,
    ) -> dict[str, Any] | None:
        if tool_choice in (None, "", "auto"):
            return None
        if tool_choice == "required":
            return {"type": "any"}
        if isinstance(tool_choice, dict):
            func = tool_choice.get("function")
            if isinstance(func, dict):
                name = str(func.get("name") or "").strip()
                if name:
                    return {"type": "tool", "name": name}
        return None

    @classmethod
    def _convert_messages(
        cls,
        messages: list[dict[str, Any]],
    ) -> tuple[str | list[dict[str, Any]] | None, list[dict[str, Any]]]:
        system_text, non_system = cls._split_system(messages)
        converted: list[dict[str, Any]] = []
        pending_tool_results: list[dict[str, Any]] = []

        def flush_tool_results() -> None:
            nonlocal pending_tool_results
            if pending_tool_results:
                converted.append({"role": "user", "content": pending_tool_results})
                pending_tool_results = []

        for msg in non_system:
            role = str(msg.get("role") or "user")
            if role == "tool":
                tool_result_id = str(msg.get("tool_call_id") or "").strip()
                if tool_result_id:
                    tool_content = msg.get("anthropic_tool_result_content")
                    if not isinstance(tool_content, list):
                        tool_content = str(msg.get("content") or "")
                    pending_tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_result_id,
                        "content": tool_content,
                    })
                continue

            flush_tool_results()

            if role == "assistant":
                blocks: list[dict[str, Any]] = []
                raw_blocks = msg.get("anthropic_content")
                if isinstance(raw_blocks, list) and raw_blocks:
                    for part in raw_blocks:
                        if isinstance(part, dict):
                            blocks.append(dict(part))
                    converted.append({
                        "role": "assistant",
                        "content": blocks,
                    })
                    continue
                content = msg.get("content", "")
                if isinstance(content, str) and content:
                    blocks.append({"type": "text", "text": content})
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict):
                            blocks.append(dict(part))

                for tc in msg.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    func = tc.get("function")
                    if not isinstance(func, dict):
                        continue
                    name = str(func.get("name") or "").strip()
                    if not name:
                        continue
                    blocks.append({
                        "type": "tool_use",
                        "id": str(tc.get("id") or ""),
                        "name": name,
                        "input": cls._parse_tool_arguments(func.get("arguments")),
                    })

                converted.append({
                    "role": "assistant",
                    "content": blocks if blocks else str(content or ""),
                })
                continue

            converted.append({
                "role": "user",
                "content": msg.get("content", ""),
            })

        flush_tool_results()
        return system_text, converted

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        tools = kwargs.pop("tools", None)
        tool_choice = kwargs.pop("tool_choice", None)
        system_text, user_messages = self._convert_messages(messages)
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": user_messages,
            "temperature": temperature,
            "max_tokens": max_tokens or 4096,
            **kwargs,
        }
        if system_text:
            call_kwargs["system"] = system_text
        if tools:
            translated_tools = [
                payload
                for payload in (
                    self._tool_schema_to_anthropic(tool) for tool in tools
                )
                if payload is not None
            ]
            if translated_tools:
                call_kwargs["tools"] = translated_tools
        translated_tool_choice = self._tool_choice_to_anthropic(tool_choice)
        if translated_tool_choice is not None:
            call_kwargs["tool_choice"] = translated_tool_choice
        call_kwargs.setdefault("timeout", self._timeout_seconds)

        try:
            resp = await self._client.messages.create(**call_kwargs)
        except Exception as exc:
            _cls = type(exc).__name__
            if _cls == "AuthenticationError":
                raise LLMAuthenticationError(
                    f"LLM provider authentication failed for model '{model}': "
                    f"check your Anthropic API key — {exc}"
                ) from exc
            if _cls == "PermissionDeniedError":
                raise LLMAuthenticationError(
                    f"LLM provider denied access for model '{model}': "
                    f"your Anthropic API key may lack permissions — {exc}"
                ) from exc
            raise
        text = ""
        tool_calls: list[dict[str, Any]] = []
        for block in resp.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text += getattr(block, "text", "")
            elif block_type == "tool_use":
                tool_calls.append({
                    "id": str(getattr(block, "id", "") or ""),
                    "type": "function",
                    "function": {
                        "name": str(getattr(block, "name", "") or ""),
                        "arguments": self._stringify_tool_arguments(
                            getattr(block, "input", {}) or {}
                        ),
                    },
                })
        usage = self._extract_usage(resp)
        cached_input, cache_write = self._extract_cache_tokens(resp)
        finish_reason = getattr(resp, "stop_reason", "") or ""
        raw_assistant_message = self._serialize_assistant_message(
            text=text,
            tool_calls=tool_calls,
            content_blocks=list(getattr(resp, "content", None) or []),
        )
        return CompletionResult(
            text=text, usage=usage, model=model,
            tool_calls=tool_calls or None,
            cached_input_tokens=cached_input,
            cache_write_tokens=cache_write,
            finish_reason=finish_reason,
            raw_assistant_message=raw_assistant_message,
            provider_metadata={"family": "anthropic"},
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        tools = kwargs.pop("tools", None)
        tool_choice = kwargs.pop("tool_choice", None)
        system_text, user_messages = self._convert_messages(messages)
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": user_messages,
            "temperature": temperature,
            "max_tokens": max_tokens or 4096,
            **kwargs,
        }
        if system_text:
            call_kwargs["system"] = system_text
        if tools:
            translated_tools = [
                payload
                for payload in (
                    self._tool_schema_to_anthropic(tool) for tool in tools
                )
                if payload is not None
            ]
            if translated_tools:
                call_kwargs["tools"] = translated_tools
        translated_tool_choice = self._tool_choice_to_anthropic(tool_choice)
        if translated_tool_choice is not None:
            call_kwargs["tool_choice"] = translated_tool_choice
        call_kwargs.setdefault("timeout", self._timeout_seconds)

        accumulated = ""
        try:
            stream_ctx = self._client.messages.stream(**call_kwargs)
        except Exception as exc:
            _cls = type(exc).__name__
            if _cls == "AuthenticationError":
                raise LLMAuthenticationError(
                    f"LLM provider authentication failed for model '{model}': "
                    f"check your Anthropic API key — {exc}"
                ) from exc
            if _cls == "PermissionDeniedError":
                raise LLMAuthenticationError(
                    f"LLM provider denied access for model '{model}': "
                    f"your Anthropic API key may lack permissions — {exc}"
                ) from exc
            raise
        async with stream_ctx as stream:
            async for text in stream.text_stream:
                accumulated += text
                yield StreamChunk(delta=text, accumulated=accumulated)

        final_message = await stream.get_final_message()
        final_text = ""
        tool_calls: list[dict[str, Any]] = []
        for block in final_message.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                final_text += getattr(block, "text", "")
            elif block_type == "tool_use":
                tool_calls.append(
                    {
                        "id": str(getattr(block, "id", "") or ""),
                        "type": "function",
                        "function": {
                            "name": str(getattr(block, "name", "") or ""),
                            "arguments": self._stringify_tool_arguments(
                                getattr(block, "input", {}) or {}
                            ),
                        },
                    }
                )
        usage = self._extract_usage(final_message)
        if usage is not None:
            cached_input, cache_write = self._extract_cache_tokens(final_message)
            usage["cached_input_tokens"] = cached_input
            usage["cache_write_tokens"] = cache_write
        raw_assistant_message = self._serialize_assistant_message(
            text=final_text,
            tool_calls=tool_calls,
            content_blocks=list(getattr(final_message, "content", None) or []),
        )
        yield StreamChunk(
            delta="",
            accumulated=final_text or accumulated,
            done=True,
            usage=usage,
            model=model,
            tool_calls=tool_calls or None,
            finish_reason=str(getattr(final_message, "stop_reason", "") or ""),
            raw_assistant_message=raw_assistant_message,
            provider_metadata={"family": "anthropic"},
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
