"""OpenAI-compatible LLM provider — wraps AsyncOpenAI for any OpenAI-API endpoint."""

from __future__ import annotations

from dataclasses import dataclass
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
from dan.providers.multimodal import normalize_openai_messages_for_multimodal


@dataclass(frozen=True)
class _OpenAICompatibilityProfile:
    """Backend behavior knobs for OpenAI-compatible endpoints."""

    family: str = "generic"
    supports_exact_tool_choice: bool = True
    supports_required_tool_choice: bool = True
    forced_temperature: float | None = None
    disable_thinking_for_explicit_tool_choice: bool = False
    low_budget_text_thinking_threshold: int | None = None
    disabled_thinking_temperature: float | None = None
    stream_via_complete: bool = False
    requires_tool_parameters_object_type: bool = False
    flattens_root_tool_parameter_unions: bool = False


class OpenAIProvider:
    """Provider for OpenAI and any OpenAI-compatible endpoint (e.g. vectorengine.ai)."""

    supports_exact_tool_choice = True
    supports_tool_calls = True
    supports_required_tool_choice = True
    assistant_replay_mode = "raw"
    _CONNECT_TIMEOUT_CAP_SECONDS = 10.0
    _SDK_MAX_RETRIES = 0
    _IMPORTED_ASYNC_OPENAI = AsyncOpenAI
    _MOONSHOT_HOSTS = frozenset({"api.moonshot.ai"})

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

    async def close(self) -> None:
        client = getattr(self, "_client", None)
        if client is not None:
            await self._close_client_quietly(client)
            self._client = None

    async def aclose(self) -> None:
        await self.close()

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
        base_url_host = self._base_url_host()
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

    @staticmethod
    def _normalize_model_name(model: str) -> str:
        return str(model or "").strip().lower()

    @classmethod
    def _normalize_base_url_host(cls, base_url: Any) -> str | None:
        if base_url is None:
            return None
        parsed = urlparse(str(base_url))
        host = str(parsed.netloc or parsed.path or "").strip().lower()
        return host or None

    def _base_url_host(self) -> str | None:
        host = self._normalize_base_url_host(getattr(self, "_base_url", None))
        if host:
            return host
        client = getattr(self, "_client", None)
        return self._normalize_base_url_host(getattr(client, "base_url", None))

    def _compatibility_profile(self, model: str) -> _OpenAICompatibilityProfile:
        normalized = self._normalize_model_name(model)
        host = self._base_url_host()
        is_moonshot_host = bool(
            host
            and (host in self._MOONSHOT_HOSTS or any(host.endswith(f".{value}") for value in self._MOONSHOT_HOSTS))
        )
        is_moonshot_like = (
            is_moonshot_host
            or normalized.startswith("moonshot-")
            or normalized.startswith("kimi-")
        )
        if not is_moonshot_like:
            return _OpenAICompatibilityProfile()

        low_budget_threshold = 16_000 if normalized.startswith("kimi-k2") else None
        return _OpenAICompatibilityProfile(
            family="moonshot",
            supports_exact_tool_choice=False,
            supports_required_tool_choice=False,
            forced_temperature=1.0 if normalized.startswith("kimi-") else None,
            disable_thinking_for_explicit_tool_choice=True,
            low_budget_text_thinking_threshold=low_budget_threshold,
            disabled_thinking_temperature=0.6,
            stream_via_complete=normalized.startswith("kimi-"),
            requires_tool_parameters_object_type=True,
            flattens_root_tool_parameter_unions=True,
        )

    def get_model_behavior(self, model: str) -> ModelBehaviorProfile:
        profile = self._compatibility_profile(model)
        return ModelBehaviorProfile(
            supports_tool_calls=True,
            supports_exact_tool_choice=profile.supports_exact_tool_choice,
            supports_required_tool_choice=profile.supports_required_tool_choice,
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

    def _normalize_temperature(self, model: str, temperature: float | None) -> float | None:
        """Normalize provider-specific temperature constraints for compatible backends.

        Some Moonshot/Kimi-compatible routes reject any temperature other than
        ``1``. Coerce those requests here so higher-level chat/runtime code can
        keep provider-agnostic defaults.
        """
        if temperature is None:
            return None
        forced_temperature = self._compatibility_profile(model).forced_temperature
        if forced_temperature is not None:
            return forced_temperature
        return temperature

    def _apply_compatibility_defaults(
        self,
        model: str,
        call_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply backend-specific request defaults for compatible backends.

        Moonshot-style backends may enable thinking by default. That can either
        consume the entire output budget before visible text is returned or
        delay explicit tool calls unnecessarily. When callers have *not*
        explicitly opted into thinking, normalize the request at this provider
        seam instead of duplicating model-specific fixes at call sites.

        - For low-budget plain-text K2-family calls, disable thinking so the
          output budget is reserved for final content.
        - For Moonshot-style exact/required tool requests, disable thinking so
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

        profile = self._compatibility_profile(model)
        if "thinking" not in call_kwargs:
            tool_choice = call_kwargs.get("tool_choice")
            if (
                profile.disable_thinking_for_explicit_tool_choice
                and call_kwargs.get("tools")
                and tool_choice not in (None, "", "auto")
            ):
                call_kwargs["thinking"] = {"type": "disabled"}
            elif (
                profile.low_budget_text_thinking_threshold is not None
                and not call_kwargs.get("tools")
            ):
                max_tokens = call_kwargs.get("max_tokens")
                if (
                    isinstance(max_tokens, int)
                    and 0 < max_tokens < profile.low_budget_text_thinking_threshold
                ):
                    call_kwargs["thinking"] = {"type": "disabled"}
        thinking = call_kwargs.get("thinking")
        if (
            isinstance(thinking, dict)
            and thinking.get("type") == "disabled"
            and profile.disabled_thinking_temperature is not None
        ):
            call_kwargs["temperature"] = profile.disabled_thinking_temperature
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

    @classmethod
    def _normalize_json_schema_for_openai_compatibility(cls, schema: Any) -> Any:
        """Normalize JSON Schema for stricter OpenAI-compatible backends.

        Some backends reject schemas that combine a parent-level ``type`` with
        ``anyOf`` / ``oneOf`` variants that only contribute ``required`` keys.
        Preserve the existing schema semantics by recursively moving the parent
        ``type`` onto variants that do not already declare one.
        """
        if isinstance(schema, list):
            return [cls._normalize_json_schema_for_openai_compatibility(item) for item in schema]
        if not isinstance(schema, dict):
            return schema

        normalized = {
            key: cls._normalize_json_schema_for_openai_compatibility(value)
            for key, value in schema.items()
        }

        parent_type = normalized.get("type")
        parent_required = normalized.get("required")
        parent_properties = normalized.get("properties")
        parent_additional_properties = normalized.get("additionalProperties")
        parent_items = normalized.get("items")
        if isinstance(parent_required, list):
            parent_required = [
                str(name).strip() for name in parent_required if str(name).strip()
            ]
        else:
            parent_required = None

        moved_parent_type = False
        moved_parent_required = False
        moved_parent_properties = False
        moved_parent_additional_properties = False
        moved_parent_items = False
        for keyword in ("anyOf", "oneOf"):
            variants = normalized.get(keyword)
            if not isinstance(variants, list):
                continue
            rebuilt_variants: list[Any] = []
            moved_for_keyword = False
            for variant in variants:
                if not isinstance(variant, dict):
                    rebuilt_variants.append(variant)
                    continue
                rebuilt_variant = dict(variant)
                if isinstance(parent_type, str) and "type" not in rebuilt_variant:
                    rebuilt_variant["type"] = parent_type
                    moved_parent_type = True
                    moved_for_keyword = True
                if parent_type == "object":
                    if isinstance(parent_properties, dict) and "properties" not in rebuilt_variant:
                        rebuilt_variant["properties"] = dict(parent_properties)
                        moved_for_keyword = True
                        moved_parent_properties = True
                    if (
                        parent_additional_properties is not None
                        and "additionalProperties" not in rebuilt_variant
                    ):
                        rebuilt_variant["additionalProperties"] = (
                            cls._normalize_json_schema_for_openai_compatibility(
                                parent_additional_properties
                            )
                        )
                        moved_for_keyword = True
                        moved_parent_additional_properties = True
                elif parent_type == "array":
                    if parent_items is not None and "items" not in rebuilt_variant:
                        rebuilt_variant["items"] = cls._normalize_json_schema_for_openai_compatibility(
                            parent_items
                        )
                        moved_for_keyword = True
                        moved_parent_items = True
                if parent_required is not None:
                    variant_required = rebuilt_variant.get("required")
                    if isinstance(variant_required, list):
                        merged_required = [
                            *parent_required,
                            *[
                                str(name).strip()
                                for name in variant_required
                                if str(name).strip() and str(name).strip() not in parent_required
                            ],
                        ]
                    else:
                        merged_required = list(parent_required)
                    rebuilt_variant["required"] = merged_required
                    moved_parent_required = True
                    moved_for_keyword = True
                rebuilt_variants.append(rebuilt_variant)
            if moved_for_keyword:
                normalized[keyword] = rebuilt_variants
        if moved_parent_type:
            normalized.pop("type", None)
        if moved_parent_required:
            normalized.pop("required", None)
        if moved_parent_properties:
            normalized.pop("properties", None)
        if moved_parent_additional_properties:
            normalized.pop("additionalProperties", None)
        if moved_parent_items:
            normalized.pop("items", None)

        return normalized

    @classmethod
    def _normalize_tool_parameters_for_openai_compatibility(
        cls,
        parameters: Any,
        *,
        require_object_type: bool = False,
        flatten_root_unions: bool = False,
    ) -> Any:
        if not isinstance(parameters, dict):
            if require_object_type:
                return {"type": "object", "properties": {}}
            return parameters

        normalized = cls._normalize_json_schema_for_openai_compatibility(parameters)
        if not require_object_type:
            return normalized

        if not isinstance(normalized, dict):
            return {"type": "object", "properties": {}}

        if flatten_root_unions and (
            isinstance(normalized.get("anyOf"), list)
            or isinstance(normalized.get("oneOf"), list)
        ):
            normalized = {
                key: cls._normalize_json_schema_for_openai_compatibility(value)
                for key, value in parameters.items()
                if key not in {"anyOf", "oneOf"}
            }
            if not isinstance(normalized, dict):
                normalized = {}

        normalized = dict(normalized)
        normalized["type"] = "object"
        normalized.setdefault("properties", {})
        if normalized.get("required") == []:
            normalized.pop("required", None)
        return normalized

    @classmethod
    def _normalize_tool_schemas_for_openai_compatibility(
        cls,
        tools: Any,
        *,
        require_parameters_object_type: bool = False,
        flatten_root_parameter_unions: bool = False,
    ) -> Any:
        if not isinstance(tools, list):
            return tools

        normalized_tools: list[Any] = []
        for tool in tools:
            if not isinstance(tool, dict):
                normalized_tools.append(tool)
                continue
            normalized_tool = dict(tool)
            function_payload = normalized_tool.get("function")
            if isinstance(function_payload, dict):
                normalized_function = dict(function_payload)
                parameters = normalized_function.get("parameters")
                normalized_function["parameters"] = (
                    cls._normalize_tool_parameters_for_openai_compatibility(
                        parameters,
                        require_object_type=require_parameters_object_type,
                        flatten_root_unions=flatten_root_parameter_unions,
                    )
                )
                normalized_tool["function"] = normalized_function
            normalized_tools.append(normalized_tool)
        return normalized_tools

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
        messages = normalize_openai_messages_for_multimodal(messages)
        effective_temperature = self._normalize_temperature(model, temperature)
        profile = self._compatibility_profile(model)
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            **kwargs,
        }
        if "tools" in call_kwargs:
            call_kwargs["tools"] = self._normalize_tool_schemas_for_openai_compatibility(
                call_kwargs.get("tools"),
                require_parameters_object_type=profile.requires_tool_parameters_object_type,
                flatten_root_parameter_unions=profile.flattens_root_tool_parameter_unions,
            )
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
        messages = normalize_openai_messages_for_multimodal(messages)
        # Some OpenAI-compatible routes emit empty text deltas for models that
        # otherwise return valid completion text. Fall back to a regular
        # completion call and surface the full text as a synthetic stream.
        if self._compatibility_profile(model).stream_via_complete:
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
        profile = self._compatibility_profile(model)
        if "tools" in call_kwargs:
            call_kwargs["tools"] = self._normalize_tool_schemas_for_openai_compatibility(
                call_kwargs.get("tools"),
                require_parameters_object_type=profile.requires_tool_parameters_object_type,
                flatten_root_parameter_unions=profile.flattens_root_tool_parameter_unions,
            )
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
