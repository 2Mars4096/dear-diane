"""LLM executor — OpenAI-compatible chat completions with output normalization."""

from __future__ import annotations

import asyncio
import logging
import string
from typing import Any

from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.normalizer import OutputNormalizer
from dan.engine.state import NodeStatus
from dan.models.nodes import LLMOperator, NodeBase

logger = logging.getLogger(__name__)


def _render_template(template: str, variables: dict[str, Any]) -> str:
    """Render a prompt template with Python str.format_map.

    Uses safe_substitute semantics: missing keys are left as-is rather
    than raising KeyError.
    """
    try:
        return template.format_map(variables)
    except (KeyError, IndexError, ValueError):
        return string.Template(template).safe_substitute(variables)


class LLMExecutor:
    """Executes LLMOperator nodes via an OpenAI-compatible endpoint.

    Handles prompt rendering, chat completion calls, output normalization
    (parse/validate/re-prompt loop), and retry policy for API failures.
    """

    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        self._client = client

    def _get_client(self, context: ExecutionContext) -> AsyncOpenAI:
        if self._client is not None:
            return self._client
        return AsyncOpenAI(
            api_key=context.config.llm_api_key,
            base_url=context.config.llm_base_url,
        )

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, LLMOperator)
        client = self._get_client(context)
        model = node.model or context.config.llm_default_model

        rendered_prompt = _render_template(node.prompt_template, inputs)

        # -- 5-3: Rich logging -------------------------------------------------
        await context.emit_event(
            event_type="llm_thinking",
            node_id=node.id,
            node_type="llm_operator",
            data={"model": model, "prompt_preview": rendered_prompt[:2000]},
        )

        messages: list[dict[str, str]] = []
        if node.system_prompt:
            messages.append({"role": "system", "content": node.system_prompt})
        messages.append({"role": "user", "content": rendered_prompt})

        max_norm_retries = context.config.output_norm_max_retries
        has_schema = node.output_json_schema is not None

        last_error: str | None = None
        cumulative_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for attempt in range(1 + max_norm_retries):
            raw_text, api_error, usage = await self._call_llm(
                client, model, messages, node, context=context, attempt=attempt
            )
            if usage:
                for k in cumulative_usage:
                    cumulative_usage[k] += usage.get(k, 0)

            if api_error:
                last_error = api_error
                break

            meta = {"model": model, "attempts": attempt + 1, "usage": cumulative_usage}

            if not has_schema:
                return NodeResult(
                    outputs={"text": raw_text},
                    status=NodeStatus.COMPLETED,
                    metadata=meta,
                )

            result = OutputNormalizer.normalize(raw_text, node.output_json_schema)  # type: ignore[arg-type]
            if result.success:
                return NodeResult(
                    outputs=result.data or {},
                    status=NodeStatus.COMPLETED,
                    metadata=meta,
                )

            if attempt < max_norm_retries:
                messages.append({"role": "assistant", "content": raw_text})
                messages.append({"role": "user", "content": result.error_message or ""})
                logger.debug(
                    "Output normalization failed (attempt %d/%d): %s",
                    attempt + 1,
                    max_norm_retries + 1,
                    result.error_message,
                )
            else:
                last_error = result.error_message

        return NodeResult(
            outputs={},
            status=NodeStatus.FAILED,
            error=last_error or "LLM execution failed",
            metadata={"model": model, "usage": cumulative_usage},
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

    async def _call_llm(
        self,
        client: AsyncOpenAI,
        model: str,
        messages: list[dict[str, str]],
        node: LLMOperator,
        context: ExecutionContext | None = None,
        attempt: int = 0,
    ) -> tuple[str, str | None, dict[str, int] | None]:
        """Call the LLM with retry on transient API errors.

        Returns (response_text, error_message, usage_dict). On success
        error_message is None; on exhausted retries response_text is empty.
        """
        max_retries = 3
        backoff = 1.0

        for retry in range(max_retries):
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": node.temperature,
                }
                if node.max_tokens is not None:
                    kwargs["max_tokens"] = node.max_tokens

                try:
                    kwargs["stream"] = True
                    kwargs["stream_options"] = {"include_usage": True}
                    stream = await client.chat.completions.create(**kwargs)
                    accumulated = ""
                    chunk_count = 0
                    last_chunk = None
                    async for chunk in stream:
                        last_chunk = chunk
                        if chunk.choices:
                            delta = chunk.choices[0].delta.content or ""
                            accumulated += delta
                            chunk_count += 1
                            if context and chunk_count % 5 == 0:
                                await context.emit_event(
                                    event_type="intermediate_text",
                                    node_id=node.id,
                                    node_type="llm_operator",
                                    data={"delta": delta, "text": accumulated, "attempt": attempt},
                                )
                    if context and accumulated:
                        await context.emit_event(
                            event_type="intermediate_text",
                            node_id=node.id,
                            node_type="llm_operator",
                            data={"delta": "", "text": accumulated, "attempt": attempt, "done": True},
                        )
                    usage = self._extract_usage(last_chunk) if last_chunk else None
                    return accumulated, None, usage
                except Exception:
                    kwargs.pop("stream", None)
                    kwargs.pop("stream_options", None)
                    resp = await client.chat.completions.create(**kwargs)
                    content = resp.choices[0].message.content or ""
                    return content, None, self._extract_usage(resp)

            except (RateLimitError, APITimeoutError) as exc:
                if retry < max_retries - 1:
                    logger.warning(
                        "Transient API error (attempt %d/%d): %s",
                        retry + 1, max_retries, exc,
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2
                else:
                    return "", f"API error after {max_retries} retries: {exc}", None

            except APIError as exc:
                return "", f"API error: {exc}", None

            except Exception as exc:
                return "", f"Unexpected error calling LLM: {exc}", None

        return "", "LLM call failed", None
