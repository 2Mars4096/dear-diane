"""OpenAI-compatible LLM provider — wraps AsyncOpenAI for any OpenAI-API endpoint."""

from __future__ import annotations

import inspect
import json
import httpx
import os
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import openai
from openai import (
    AsyncOpenAI,
    AuthenticationError,
    PermissionDeniedError,
)

from dan.providers import (
    CompletionResult,
    LLMAuthenticationError,
    ModelBehaviorProfile,
    ProviderConfig,
    StreamChunk,
    resolve_provider_timeout,
)


class OpenAIProvider:
    """Provider for OpenAI and any OpenAI-compatible endpoint (e.g. vectorengine.ai)."""

    supports_exact_tool_choice = True
    supports_tool_calls = True
    supports_required_tool_choice = True
    assistant_replay_mode = "raw"
    _CONNECT_TIMEOUT_CAP_SECONDS = 10.0
    _SDK_MAX_RETRIES = 0
    _IMPORTED_ASYNC_OPENAI = AsyncOpenAI

    @classmethod
    def _resolve_async_openai_cls(cls) -> type[Any]:
        """Honor runtime patches regardless of import order.

        Some tests patch ``openai.AsyncOpenAI`` directly while others patch the
        already-imported ``dan.providers.openai_provider.AsyncOpenAI`` alias.
        Prefer whichever target was patched away from the original imported
        class so backward-compat tests remain stable even after this module was
        imported earlier in the suite.
        """
        module_async_openai = AsyncOpenAI
        runtime_async_openai = getattr(openai, "AsyncOpenAI", None)

        if module_async_openai is not cls._IMPORTED_ASYNC_OPENAI:
            return module_async_openai
        if runtime_async_openai is not None and runtime_async_openai is not cls._IMPORTED_ASYNC_OPENAI:
            return runtime_async_openai
        return module_async_openai

    def __init__(self, config: ProviderConfig) -> None:
        self._api_key = config.api_key
        self._base_url = config.base_url
        self._timeout_seconds = resolve_provider_timeout(config)
        self._request_timeout = self._build_request_timeout(self._timeout_seconds)
        self._client_factory = self._build_client
        self._client = self._build_client()

    @classmethod
    def from_client(cls, client: AsyncOpenAI) -> OpenAIProvider:
        """Wrap an existing AsyncOpenAI client (for backward compat / test injection)."""
        instance = object.__new__(cls)
        instance._client = client
        instance._client_factory = None
        instance._api_key = ""
        instance._base_url = getattr(client, "base_url", None)
        timeout = getattr(client, "timeout", None)
        instance._request_timeout = timeout
        instance._timeout_seconds = (
            float(timeout) if isinstance(timeout, (int, float)) else None
        )
        return instance

    def _build_client(self) -> AsyncOpenAI:
        client_cls = self._resolve_async_openai_cls()
        return client_cls(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._request_timeout,
            max_retries=self._SDK_MAX_RETRIES,
        )

    @staticmethod
    async def _close_client_quietly(client: Any) -> None:
        for attr_name in ("close", "aclose"):
            closer = getattr(client, attr_name, None)
            if not callable(closer):
                continue
            try:
                result = closer()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                return
            return

    async def recover_from_error(self, exc: BaseException) -> None:
        _ = exc
        factory = getattr(self, "_client_factory", None)
        if not callable(factory):
            return
        old_client = getattr(self, "_client", None)
        self._client = factory()
        if old_client is not None:
            await self._close_client_quietly(old_client)

    @classmethod
    def _build_request_timeout(
        cls,
        timeout_seconds: float | None,
    ) -> float | httpx.Timeout | None:
        """Keep total request timeout while bounding slow connect/DNS phases."""
        if timeout_seconds is None:
            return None
        connect_timeout = min(timeout_seconds, cls._CONNECT_TIMEOUT_CAP_SECONDS)
        if connect_timeout >= timeout_seconds:
            return timeout_seconds
        return httpx.Timeout(timeout_seconds, connect=connect_timeout)

    @classmethod
    def _request_message_stats(cls, messages: list[dict[str, Any]]) -> dict[str, int]:
        counts = {
            "system": 0,
            "user": 0,
            "assistant": 0,
            "tool": 0,
            "other": 0,
        }
        chars = {key: 0 for key in counts}
        for message in messages:
            role = str(message.get("role") or "other").strip().lower() or "other"
            if role not in counts:
                role = "other"
            content = message.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = json.dumps(
                    cls._serialize_jsonish(content),
                    ensure_ascii=False,
                    default=str,
                )
            elif content is None:
                text = ""
            else:
                text = str(content)
            counts[role] += 1
            chars[role] += len(text)
        return {
            "message_count": sum(counts.values()),
            "system_message_count": counts["system"],
            "user_message_count": counts["user"],
            "assistant_message_count": counts["assistant"],
            "tool_message_count": counts["tool"],
            "other_message_count": counts["other"],
            "total_input_chars": sum(chars.values()),
            "system_chars": chars["system"],
            "user_chars": chars["user"],
            "assistant_chars": chars["assistant"],
            "tool_chars": chars["tool"],
            "other_chars": chars["other"],
        }

    def _request_details(
        self,
        *,
        messages: list[dict[str, Any]],
        call_kwargs: dict[str, Any],
        effective_temperature: float | None,
        request_mode: str,
    ) -> dict[str, Any]:
        extra_body = dict(call_kwargs.get("extra_body") or {})
        reasoning = call_kwargs.get("reasoning", extra_body.get("reasoning"))
        thinking = call_kwargs.get("thinking", extra_body.get("thinking"))
        base_url_host: str | None = None
        base_url = getattr(self._client, "base_url", None)
        if base_url is not None:
            parsed = urlparse(str(base_url))
            base_url_host = parsed.netloc or None
        details: dict[str, Any] = {
            "provider_name": type(self).__name__,
            "provider_base_url_host": base_url_host,
            "request_timeout_seconds": self._timeout_seconds,
            "request_mode": request_mode,
            "effective_temperature": effective_temperature,
            "effective_max_tokens": call_kwargs.get("max_tokens"),
            "tool_schema_count": len(list(call_kwargs.get("tools") or [])),
            **self._request_message_stats(messages),
        }
        if isinstance(reasoning, dict) and "enabled" in reasoning:
            details["reasoning_enabled"] = bool(reasoning.get("enabled"))
        elif reasoning is not None:
            details["reasoning_enabled"] = bool(reasoning)
        if isinstance(thinking, dict):
            thinking_type = str(thinking.get("type") or "").strip()
            if thinking_type:
                details["thinking_type"] = thinking_type
        elif thinking is not None:
            details["thinking_type"] = str(thinking)
        return details

    @classmethod
    def get_model_behavior(cls, model: str) -> ModelBehaviorProfile:
        normalized = str(model or "").strip().lower()
        is_kimi = normalized.startswith("kimi-")
        return ModelBehaviorProfile(
            supports_tool_calls=True,
            supports_exact_tool_choice=not is_kimi,
            supports_required_tool_choice=not is_kimi,
            assistant_replay_mode="raw",
        )

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

    @staticmethod
    def _normalize_temperature(model: str, temperature: float | None) -> float | None:
        """Normalize provider-specific temperature constraints for compatible backends.

        Some Kimi models exposed via OpenAI-compatible endpoints reject any
        temperature other than ``1``. Coerce those requests here so higher-level
        chat/runtime code can keep its provider-agnostic defaults.
        """
        if temperature is None:
            return None
        if str(model or "").strip().lower().startswith("kimi-"):
            return 1.0
        return temperature

    @staticmethod
    def _apply_compatibility_defaults(
        model: str,
        call_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply provider-specific request defaults for compatible backends.

        Moonshot/Kimi models may enable thinking by default. That can either
        consume the entire output budget before visible text is returned or
        delay explicit tool calls unnecessarily. When callers have *not*
        explicitly opted into thinking:

        - For low-budget plain-text ``kimi-k2.5`` calls, disable thinking so
          the output budget is reserved for final content.
        - For Moonshot/Kimi exact/required tool requests, disable thinking so
          the model can emit the requested tool call promptly.
        """
        reasoning_override = (
            os.environ.get("DAN_OPENAI_COMPAT_REASONING")
            or os.environ.get("DAN_OPENROUTER_REASONING")
            or ""
        )
        normalized_reasoning = str(reasoning_override).strip().lower()
        if "reasoning" not in call_kwargs:
            existing_extra_reasoning = dict(call_kwargs.get("extra_body") or {}).get("reasoning")
            if existing_extra_reasoning is None:
                if normalized_reasoning in {"1", "true", "enabled", "on"}:
                    call_kwargs["reasoning"] = {"enabled": True}
                elif normalized_reasoning in {"0", "false", "disabled", "off"}:
                    call_kwargs["reasoning"] = {"enabled": False}

        normalized = str(model or "").strip().lower()
        is_moonshot_family = normalized.startswith("moonshot-") or normalized.startswith("kimi-")
        if not is_moonshot_family:
            return call_kwargs
        if "thinking" not in call_kwargs:
            tool_choice = call_kwargs.get("tool_choice")
            if call_kwargs.get("tools") and tool_choice not in (None, "", "auto"):
                call_kwargs["thinking"] = {"type": "disabled"}
            elif normalized.startswith("kimi-k2.5") and not call_kwargs.get("tools"):
                max_tokens = call_kwargs.get("max_tokens")
                if isinstance(max_tokens, int) and 0 < max_tokens < 16000:
                    call_kwargs["thinking"] = {"type": "disabled"}
        thinking = call_kwargs.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "disabled":
            call_kwargs["temperature"] = 0.6
        return call_kwargs

    @staticmethod
    def _move_provider_fields_to_extra_body(
        call_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Move provider-specific request fields into ``extra_body`` for SDK calls."""
        extra_body = dict(call_kwargs.get("extra_body") or {})
        for field in ("thinking", "reasoning"):
            if field in call_kwargs:
                extra_body[field] = call_kwargs.pop(field)
        if extra_body:
            call_kwargs["extra_body"] = extra_body
        return call_kwargs

    @staticmethod
    def _dump_model_object(obj: Any) -> dict[str, Any]:
        if isinstance(obj, dict):
            return dict(obj)
        if obj is None:
            return {}
        if hasattr(obj, "model_dump"):
            try:
                dumped = obj.model_dump(mode="python", exclude_none=True)
            except TypeError:
                dumped = obj.model_dump(exclude_none=True)
            if isinstance(dumped, dict):
                return dumped
        return {}

    @classmethod
    def _serialize_jsonish(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): cls._serialize_jsonish(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._serialize_jsonish(item) for item in value]
        dumped = cls._dump_model_object(value)
        if dumped:
            dumped = cls._merge_model_extra(dumped, value)
            return {str(key): cls._serialize_jsonish(item) for key, item in dumped.items()}
        return value

    @staticmethod
    def _merge_model_extra(base: dict[str, Any], obj: Any) -> dict[str, Any]:
        extra = getattr(obj, "model_extra", None)
        if isinstance(extra, dict):
            for key, value in extra.items():
                base.setdefault(key, value)
        return base

    @classmethod
    def _serialize_tool_call(cls, tool_call: Any) -> dict[str, Any]:
        raw = cls._merge_model_extra(cls._dump_model_object(tool_call), tool_call)
        raw.setdefault("id", getattr(tool_call, "id", ""))
        raw.setdefault("type", getattr(tool_call, "type", "function"))

        function_obj = getattr(tool_call, "function", None)
        function_payload = raw.get("function")
        if not isinstance(function_payload, dict):
            function_payload = cls._dump_model_object(function_obj)
        function_payload = cls._merge_model_extra(function_payload or {}, function_obj)
        if function_obj is not None:
            function_payload.setdefault("name", getattr(function_obj, "name", ""))
            function_payload.setdefault("arguments", getattr(function_obj, "arguments", ""))
        raw["function"] = function_payload
        return raw

    @classmethod
    def _serialize_assistant_message(cls, message: Any) -> dict[str, Any]:
        raw = cls._merge_model_extra(cls._dump_model_object(message), message)
        raw["role"] = "assistant"
        if getattr(message, "tool_calls", None):
            raw["tool_calls"] = [cls._serialize_tool_call(tc) for tc in message.tool_calls]
        for field_name in ("reasoning_content", "reasoning_details"):
            if field_name in raw:
                raw[field_name] = cls._serialize_jsonish(raw[field_name])
                continue
            field_value = getattr(message, field_name, None)
            if field_value is not None:
                raw[field_name] = cls._serialize_jsonish(field_value)
        content = raw.get("content", getattr(message, "content", None))
        if isinstance(content, str) and not content.strip() and raw.get("tool_calls"):
            raw["content"] = None
        elif content is None and raw.get("tool_calls"):
            raw["content"] = None
        else:
            raw["content"] = content
        return raw

    @staticmethod
    def _append_stream_text(current: str, piece: Any) -> str:
        if piece in {None, ""}:
            return current
        return current + str(piece)

    @classmethod
    def _merge_stream_tool_call_delta(
        cls,
        tool_calls_by_index: dict[int, dict[str, Any]],
        raw_tool_call: Any,
    ) -> None:
        index_raw = getattr(raw_tool_call, "index", None)
        try:
            index = int(index_raw) if index_raw is not None else len(tool_calls_by_index)
        except Exception:
            index = len(tool_calls_by_index)
        payload = tool_calls_by_index.setdefault(
            index,
            {
                "id": "",
                "type": "function",
                "function": {
                    "name": "",
                    "arguments": "",
                },
            },
        )
        tool_call_id = getattr(raw_tool_call, "id", None)
        if tool_call_id:
            payload["id"] = str(tool_call_id)
        tool_call_type = getattr(raw_tool_call, "type", None)
        if tool_call_type:
            payload["type"] = str(tool_call_type)

        function_payload = payload.setdefault("function", {})
        function_delta = getattr(raw_tool_call, "function", None)
        if function_delta is not None:
            function_payload["name"] = cls._append_stream_text(
                str(function_payload.get("name") or ""),
                getattr(function_delta, "name", None),
            )
            function_payload["arguments"] = cls._append_stream_text(
                str(function_payload.get("arguments") or ""),
                getattr(function_delta, "arguments", None),
            )

    @classmethod
    def _final_stream_tool_calls(
        cls,
        tool_calls_by_index: dict[int, dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        if not tool_calls_by_index:
            return None
        ordered: list[dict[str, Any]] = []
        for index in sorted(tool_calls_by_index):
            payload = dict(tool_calls_by_index[index])
            function_payload = dict(payload.get("function") or {})
            payload["function"] = {
                "name": str(function_payload.get("name") or ""),
                "arguments": str(function_payload.get("arguments") or ""),
            }
            payload["type"] = str(payload.get("type") or "function")
            ordered.append(payload)
        return ordered

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        effective_temperature = self._normalize_temperature(model, temperature)
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            **kwargs,
        }
        if effective_temperature is not None:
            call_kwargs["temperature"] = effective_temperature
        if max_tokens is not None:
            call_kwargs["max_tokens"] = max_tokens
        if self._request_timeout is not None:
            call_kwargs.setdefault("timeout", self._request_timeout)
        call_kwargs = self._apply_compatibility_defaults(model, call_kwargs)
        call_kwargs = self._move_provider_fields_to_extra_body(call_kwargs)
        request_details = self._request_details(
            messages=messages,
            call_kwargs=call_kwargs,
            effective_temperature=effective_temperature,
            request_mode="complete",
        )

        try:
            resp = await self._client.chat.completions.create(**call_kwargs)
        except AuthenticationError as exc:
            raise LLMAuthenticationError(
                f"LLM provider authentication failed for model '{model}': "
                f"check your API key — {exc}"
            ) from exc
        except PermissionDeniedError as exc:
            raise LLMAuthenticationError(
                f"LLM provider denied access for model '{model}': "
                f"your API key may lack permissions or the model may be unavailable — {exc}"
            ) from exc
        message = resp.choices[0].message
        text = message.content or ""
        usage = self._extract_usage(resp)
        tool_calls = None
        if hasattr(message, "tool_calls") and message.tool_calls:
            tool_calls = [self._serialize_tool_call(tc) for tc in message.tool_calls]
        cached_input = (usage or {}).get("cached_input_tokens", 0)
        finish_reason = getattr(resp.choices[0], "finish_reason", "") or ""
        raw_assistant_message = self._serialize_assistant_message(message)
        return CompletionResult(
            text=text, usage=usage, model=model, tool_calls=tool_calls,
            cached_input_tokens=cached_input,
            finish_reason=finish_reason,
            raw_assistant_message=raw_assistant_message,
            provider_metadata={
                "family": "openai_compatible",
                "request_details": request_details,
            },
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        # Some Kimi/OpenAI-compatible endpoints accept streaming requests but
        # emit empty text deltas, which leaves downstream workflows with blank
        # outputs despite non-zero completion tokens. Fall back to a regular
        # completion call and surface the full text as a synthetic stream.
        if str(model or "").strip().lower().startswith("kimi-"):
            result = await self.complete(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            accumulated = result.text or ""
            if accumulated:
                yield StreamChunk(delta=accumulated, accumulated=accumulated)
            yield StreamChunk(
                delta="",
                accumulated=accumulated,
                done=True,
                usage=result.usage,
                model=result.model or model,
                tool_calls=result.tool_calls,
                finish_reason=result.finish_reason,
                raw_assistant_message=result.raw_assistant_message,
                provider_metadata=result.provider_metadata,
            )
            return

        effective_temperature = self._normalize_temperature(model, temperature)
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            **kwargs,
        }
        if effective_temperature is not None:
            call_kwargs["temperature"] = effective_temperature
        if max_tokens is not None:
            call_kwargs["max_tokens"] = max_tokens
        if self._request_timeout is not None:
            call_kwargs.setdefault("timeout", self._request_timeout)
        call_kwargs = self._apply_compatibility_defaults(model, call_kwargs)
        call_kwargs = self._move_provider_fields_to_extra_body(call_kwargs)
        request_details = self._request_details(
            messages=messages,
            call_kwargs=call_kwargs,
            effective_temperature=effective_temperature,
            request_mode="stream",
        )

        try:
            stream = await self._client.chat.completions.create(**call_kwargs)
        except AuthenticationError as exc:
            raise LLMAuthenticationError(
                f"LLM provider authentication failed for model '{model}': "
                f"check your API key — {exc}"
            ) from exc
        except PermissionDeniedError as exc:
            raise LLMAuthenticationError(
                f"LLM provider denied access for model '{model}': "
                f"your API key may lack permissions or the model may be unavailable — {exc}"
            ) from exc
        accumulated = ""
        last_usage = None
        finish_reason = ""
        tool_calls_by_index: dict[int, dict[str, Any]] = {}
        reasoning_content = ""
        reasoning_details: Any = None
        async for chunk in stream:
            if chunk.choices:
                choice = chunk.choices[0]
                delta_obj = getattr(choice, "delta", None)
                delta = getattr(delta_obj, "content", "") or ""
                accumulated += delta
                delta_reasoning_content = getattr(delta_obj, "reasoning_content", None)
                if delta_reasoning_content not in {None, ""}:
                    reasoning_content = cls._append_stream_text(
                        reasoning_content,
                        delta_reasoning_content,
                    )
                delta_reasoning_details = getattr(delta_obj, "reasoning_details", None)
                if delta_reasoning_details is not None:
                    reasoning_details = cls._serialize_jsonish(delta_reasoning_details)
                for raw_tool_call in list(getattr(delta_obj, "tool_calls", None) or []):
                    self._merge_stream_tool_call_delta(tool_calls_by_index, raw_tool_call)
                choice_finish_reason = getattr(choice, "finish_reason", None)
                if choice_finish_reason:
                    finish_reason = str(choice_finish_reason)
                yield StreamChunk(delta=delta, accumulated=accumulated)
            usage = self._extract_usage(chunk)
            if usage:
                last_usage = usage

        tool_calls = self._final_stream_tool_calls(tool_calls_by_index)
        raw_assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": accumulated if accumulated.strip() else (None if tool_calls else accumulated),
        }
        if tool_calls:
            raw_assistant_message["tool_calls"] = tool_calls
        if reasoning_content:
            raw_assistant_message["reasoning_content"] = reasoning_content
        if reasoning_details is not None:
            raw_assistant_message["reasoning_details"] = reasoning_details

        yield StreamChunk(
            delta="",
            accumulated=accumulated,
            done=True,
            usage=last_usage,
            model=model,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            raw_assistant_message=raw_assistant_message,
            provider_metadata={
                "family": "openai_compatible",
                "request_details": request_details,
            },
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
