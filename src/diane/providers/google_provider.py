"""Google Gemini LLM provider — wraps google.generativeai."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from diane.providers import (
    CompletionResult,
    LLMAuthenticationError,
    ProviderConfig,
    StreamChunk,
    resolve_provider_timeout,
)
from diane.providers.multimodal import gemini_parts_from_openai_content


class GoogleProvider:
    """Provider for Google Gemini models via the generativeai SDK."""

    supports_exact_tool_choice = True
    supports_tool_calls = True

    def __init__(self, config: ProviderConfig) -> None:
        try:
            import google.generativeai as genai
            from google.protobuf.json_format import MessageToDict
        except ImportError:
            raise ImportError(
                "GoogleProvider requires the 'google-generativeai' package. "
                "Install with: pip install google-generativeai"
            )
        genai.configure(api_key=config.api_key)
        self._genai = genai
        self._protos = genai.protos
        self._message_to_dict = MessageToDict
        self._timeout_seconds = resolve_provider_timeout(config)

    @staticmethod
    def apply_cache_hints(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """No-op — Gemini cached_content requires a separate API call.

        TODO: Integrate ``caching.CachedContent.create()`` for long system
        instructions shared across calls.  Deferred until usage patterns are
        clearer (requires TTL management and explicit cleanup).
        """
        return messages

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

    def _part_to_dict(self, part: Any) -> dict[str, Any]:
        obj = part._pb if hasattr(part, "_pb") else part
        return self._message_to_dict(obj, preserving_proto_field_name=True)

    @classmethod
    def _to_gemini_messages(
        cls,
        messages: list[dict[str, Any]],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Convert OpenAI-style messages to plain Gemini chat history dicts.

        This compatibility helper avoids requiring protobuf objects during
        tests or when using older ``start_chat()``-style Gemini clients.
        """
        system_parts: list[str] = []
        history: list[dict[str, Any]] = []
        pending_tool_parts: list[dict[str, Any]] = []
        tool_name_by_id: dict[str, str] = {}

        def flush_tool_parts() -> None:
            nonlocal pending_tool_parts
            if pending_tool_parts:
                history.append({"role": "user", "parts": pending_tool_parts})
                pending_tool_parts = []

        for msg in messages:
            role = str(msg.get("role") or "user")
            content = msg.get("content", "")
            if role == "system":
                system_parts.append(str(content or ""))
                continue

            if role == "tool":
                tool_call_id = str(msg.get("tool_call_id") or "").strip()
                tool_name = tool_name_by_id.get(tool_call_id, "")
                if tool_name:
                    pending_tool_parts.append({
                        "function_response": {
                            "name": tool_name,
                            "response": {"result": str(content or "")},
                        }
                    })
                continue

            flush_tool_parts()
            gemini_role = "model" if role == "assistant" else "user"
            parts = gemini_parts_from_openai_content(content)

            if role == "assistant":
                for tc in msg.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    func = tc.get("function")
                    if not isinstance(func, dict):
                        continue
                    name = str(func.get("name") or "").strip()
                    if not name:
                        continue
                    parts.append({
                        "function_call": {
                            "name": name,
                            "args": cls._parse_tool_arguments(func.get("arguments")),
                        }
                    })
                    tool_name_by_id[str(tc.get("id") or "").strip()] = name

            history.append({"role": gemini_role, "parts": parts})

        flush_tool_parts()
        system_text = "\n\n".join(part for part in system_parts if part) or None
        return system_text, history

    def _serialize_assistant_message(
        self,
        *,
        text: str,
        tool_calls: list[dict[str, Any]] | None,
        parts: list[Any],
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": text if text.strip() else (None if tool_calls else text),
            "gemini_parts": [self._part_to_dict(part) for part in parts],
        }
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message

    def _build_text_part(self, text: str) -> Any:
        return self._protos.Part({"text": text}, ignore_unknown_fields=True)

    def _build_inline_data_part(self, inline_data: dict[str, Any]) -> Any | None:
        payload = inline_data.get("inline_data") if isinstance(inline_data, dict) else None
        if not isinstance(payload, dict):
            return None
        mime_type = str(payload.get("mime_type") or "").strip()
        data = str(payload.get("data") or "").strip()
        if not mime_type or not data:
            return None
        return self._protos.Part(
            {"inline_data": {"mime_type": mime_type, "data": data}},
            ignore_unknown_fields=True,
        )

    def _build_function_call_part(self, tool_call: dict[str, Any]) -> Any | None:
        if not isinstance(tool_call, dict):
            return None
        func = tool_call.get("function")
        if not isinstance(func, dict):
            return None
        name = str(func.get("name") or "").strip()
        if not name:
            return None
        mapping: dict[str, Any] = {
            "name": name,
            "args": self._parse_tool_arguments(func.get("arguments")),
        }
        for key, value in func.items():
            if key not in {"name", "arguments"}:
                mapping[key] = value
        function_call = self._protos.FunctionCall(
            mapping,
            ignore_unknown_fields=True,
        )
        return self._protos.Part(
            {"function_call": function_call},
            ignore_unknown_fields=True,
        )

    def _build_function_response_part(self, name: str, content: str) -> Any:
        function_response = self._protos.FunctionResponse(
            {
                "name": name,
                "response": {"result": content},
            },
            ignore_unknown_fields=True,
        )
        return self._protos.Part(
            {"function_response": function_response},
            ignore_unknown_fields=True,
        )

    def _build_content(self, role: str, parts: list[Any]) -> Any:
        return self._protos.Content(
            {"role": role, "parts": parts},
            ignore_unknown_fields=True,
        )

    def _to_gemini_contents(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[str | None, list[Any]]:
        """Convert OpenAI-style messages to Gemini contents."""
        system_parts: list[str] = []
        contents: list[Any] = []
        pending_tool_parts: list[Any] = []
        tool_name_by_id: dict[str, str] = {}

        def flush_tool_parts() -> None:
            nonlocal pending_tool_parts
            if pending_tool_parts:
                contents.append(self._build_content("user", pending_tool_parts))
                pending_tool_parts = []

        for msg in messages:
            role = str(msg.get("role") or "user")
            content = msg.get("content", "")
            if role == "system":
                system_parts.append(str(content or ""))
                continue

            if role == "tool":
                tool_call_id = str(msg.get("tool_call_id") or "").strip()
                tool_name = tool_name_by_id.get(tool_call_id, "")
                if tool_name:
                    pending_tool_parts.append(
                        self._build_function_response_part(
                            tool_name,
                            str(content or ""),
                        )
                    )
                continue

            flush_tool_parts()
            gemini_role = "model" if role == "assistant" else "user"
            parts: list[Any] = []

            for part in gemini_parts_from_openai_content(content):
                if "text" in part:
                    parts.append(self._build_text_part(str(part.get("text") or "")))
                    continue
                inline_part = self._build_inline_data_part(part)
                if inline_part is not None:
                    parts.append(inline_part)

            if role == "assistant":
                for tc in msg.get("tool_calls") or []:
                    part = self._build_function_call_part(tc)
                    if part is None:
                        continue
                    parts.append(part)
                    if isinstance(tc, dict):
                        func = tc.get("function")
                        if isinstance(func, dict):
                            tool_name_by_id[str(tc.get("id") or "").strip()] = str(
                                func.get("name") or ""
                            ).strip()

            contents.append(self._build_content(gemini_role, parts))

        flush_tool_parts()
        system_text = "\n\n".join(part for part in system_parts if part) or None
        return system_text, contents

    @staticmethod
    def _legacy_prompt_from_history(
        history: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], str]:
        if history and history[-1].get("role") == "user":
            last_parts = history[-1].get("parts", [])
            prompt = "".join(
                str(part.get("text") or "")
                for part in last_parts
                if isinstance(part, dict) and "text" in part
            )
            return history[:-1], prompt
        return history, ""

    def _tool_schema_to_gemini_declaration(self, tool: dict[str, Any]) -> Any | None:
        if not isinstance(tool, dict):
            return None
        func = tool.get("function")
        if not isinstance(func, dict):
            return None
        name = str(func.get("name") or "").strip()
        if not name:
            return None
        mapping: dict[str, Any] = {"name": name}
        description = str(func.get("description") or "").strip()
        if description:
            mapping["description"] = description
        parameters = func.get("parameters")
        if isinstance(parameters, dict):
            mapping["parameters"] = self._normalize_json_schema_for_gemini(parameters)
        return self._protos.FunctionDeclaration(
            mapping,
            ignore_unknown_fields=True,
        )

    @classmethod
    def _normalize_json_schema_for_gemini(cls, schema: Any) -> Any:
        if isinstance(schema, list):
            return [cls._normalize_json_schema_for_gemini(item) for item in schema]
        if not isinstance(schema, dict):
            return schema
        normalized: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "type" and isinstance(value, str):
                normalized[key] = value.upper()
            else:
                normalized[key] = cls._normalize_json_schema_for_gemini(value)
        return normalized

    def _tools_to_gemini(self, tools: list[dict[str, Any]] | None) -> list[Any] | None:
        if not tools:
            return None
        declarations = [
            decl
            for decl in (
                self._tool_schema_to_gemini_declaration(tool) for tool in tools
            )
            if decl is not None
        ]
        if not declarations:
            return None
        return [
            self._protos.Tool(
                {"function_declarations": declarations},
                ignore_unknown_fields=True,
            )
        ]

    def _tool_choice_to_gemini(self, tool_choice: Any) -> Any | None:
        if tool_choice in (None, "", "auto"):
            return None
        config: dict[str, Any] = {"mode": "ANY"}
        if tool_choice == "required":
            pass
        elif isinstance(tool_choice, dict):
            func = tool_choice.get("function")
            if isinstance(func, dict):
                name = str(func.get("name") or "").strip()
                if name:
                    config["allowed_function_names"] = [name]
        else:
            return None
        return self._protos.ToolConfig(
            {"function_calling_config": config},
            ignore_unknown_fields=True,
        )

    def _extract_text_and_tool_calls(self, resp: Any) -> tuple[str, list[dict[str, Any]] | None]:
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        candidates = getattr(resp, "candidates", None) or []
        if not candidates:
            return "", None
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for index, part in enumerate(parts):
            part_dict = self._part_to_dict(part)
            if "text" in part_dict:
                text_parts.append(str(part_dict.get("text") or ""))
                continue
            function_call = part_dict.get("function_call")
            if isinstance(function_call, dict):
                payload = dict(function_call)
                name = str(payload.pop("name", "") or "").strip()
                args = payload.pop("args", {})
                tool_calls.append({
                    "id": str(payload.pop("id", "") or f"call_{index}"),
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": self._stringify_tool_arguments(args),
                        **payload,
                    },
                })
        return "".join(text_parts), tool_calls or None

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
        system_text, legacy_history = self._to_gemini_messages(messages)

        gen_config: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            gen_config["max_output_tokens"] = max_tokens

        model_kwargs: dict[str, Any] = {}
        if system_text:
            model_kwargs["system_instruction"] = system_text

        gm = self._genai.GenerativeModel(model, **model_kwargs)
        try:
            if hasattr(gm, "generate_content_async"):
                _, contents = self._to_gemini_contents(messages)
                gemini_tools = self._tools_to_gemini(tools)
                gemini_tool_choice = self._tool_choice_to_gemini(tool_choice)
                resp = await asyncio.wait_for(
                    gm.generate_content_async(
                        contents=contents,
                        generation_config=gen_config,
                        tools=gemini_tools,
                        tool_config=gemini_tool_choice,
                    ),
                    timeout=self._timeout_seconds,
                )
            elif hasattr(gm, "start_chat"):
                if tools or tool_choice not in (None, "", "auto"):
                    raise NotImplementedError(
                        "Legacy Gemini start_chat fallback does not support tool-calling"
                    )
                history, prompt = self._legacy_prompt_from_history(legacy_history)
                chat = gm.start_chat(history=history)
                resp = await asyncio.wait_for(
                    chat.send_message_async(prompt, generation_config=gen_config),
                    timeout=self._timeout_seconds,
                )
            else:
                raise AttributeError(
                    "Gemini client missing both generate_content_async and start_chat"
                )
        except Exception as exc:
            msg_lower = str(exc).lower()
            if "api key" in msg_lower or "403" in str(exc) or "401" in str(exc):
                raise LLMAuthenticationError(
                    f"LLM provider authentication failed for model '{model}': "
                    f"check your Google API key — {exc}"
                ) from exc
            raise
        if hasattr(gm, "generate_content_async"):
            text, tool_calls = self._extract_text_and_tool_calls(resp)
        else:
            text = str(getattr(resp, "text", "") or "")
            tool_calls = None
        usage = self._extract_usage(resp)
        finish_reason = ""
        raw_assistant_message = None
        try:
            candidates = getattr(resp, "candidates", None) or []
            if candidates:
                fr = getattr(candidates[0], "finish_reason", None)
                finish_reason = str(fr.name).lower() if fr else ""
                candidate_content = getattr(candidates[0], "content", None)
                candidate_parts = list(getattr(candidate_content, "parts", None) or [])
                raw_assistant_message = self._serialize_assistant_message(
                    text=text,
                    tool_calls=tool_calls,
                    parts=candidate_parts,
                )
        except Exception:
            pass
        return CompletionResult(
            text=text,
            usage=usage,
            model=model,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            raw_assistant_message=raw_assistant_message,
            provider_metadata={"family": "google"},
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
        system_text, contents = self._to_gemini_contents(messages)

        gen_config: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            gen_config["max_output_tokens"] = max_tokens

        model_kwargs: dict[str, Any] = {}
        if system_text:
            model_kwargs["system_instruction"] = system_text

        gm = self._genai.GenerativeModel(model, **model_kwargs)
        gemini_tools = self._tools_to_gemini(tools)
        gemini_tool_choice = self._tool_choice_to_gemini(tool_choice)
        accumulated = ""
        try:
            timeout_ctx = asyncio.timeout(self._timeout_seconds)
        except Exception:
            timeout_ctx = asyncio.timeout(120)
        async with timeout_ctx:
            try:
                resp = await gm.generate_content_async(
                    contents=contents,
                    generation_config=gen_config,
                    stream=True,
                    tools=gemini_tools,
                    tool_config=gemini_tool_choice,
                )
            except Exception as exc:
                msg_lower = str(exc).lower()
                if "api key" in msg_lower or "403" in str(exc) or "401" in str(exc):
                    raise LLMAuthenticationError(
                        f"LLM provider authentication failed for model '{model}': "
                        f"check your Google API key — {exc}"
                    ) from exc
                raise
            async for chunk in resp:
                delta = chunk.text or ""
                accumulated += delta
                yield StreamChunk(delta=delta, accumulated=accumulated)

        usage = self._extract_usage(resp)
        tool_calls: list[dict[str, Any]] | None = None
        final_text = accumulated
        finish_reason = ""
        raw_assistant_message = None
        try:
            final_text, tool_calls = self._extract_text_and_tool_calls(resp)
            candidates = getattr(resp, "candidates", None) or []
            if candidates:
                fr = getattr(candidates[0], "finish_reason", None)
                finish_reason = str(fr.name).lower() if fr else ""
                candidate_content = getattr(candidates[0], "content", None)
                candidate_parts = list(getattr(candidate_content, "parts", None) or [])
                raw_assistant_message = self._serialize_assistant_message(
                    text=final_text,
                    tool_calls=tool_calls,
                    parts=candidate_parts,
                )
        except Exception:
            pass
        yield StreamChunk(
            delta="",
            accumulated=final_text,
            done=True,
            usage=usage,
            model=model,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            raw_assistant_message=raw_assistant_message,
            provider_metadata={"family": "google"},
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
