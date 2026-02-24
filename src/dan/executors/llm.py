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
            data={"model": model, "prompt_preview": rendered_prompt[:200]},
        )

        messages: list[dict[str, str]] = []
        if node.system_prompt:
            messages.append({"role": "system", "content": node.system_prompt})
        messages.append({"role": "user", "content": rendered_prompt})

        max_norm_retries = context.config.output_norm_max_retries
        has_schema = node.output_json_schema is not None

        last_error: str | None = None
        for attempt in range(1 + max_norm_retries):
            raw_text, api_error = await self._call_llm(
                client, model, messages, node
            )
            if api_error:
                last_error = api_error
                break

            if not has_schema:
                return NodeResult(
                    outputs={"text": raw_text},
                    status=NodeStatus.COMPLETED,
                    metadata={"model": model, "attempts": attempt + 1},
                )

            result = OutputNormalizer.normalize(raw_text, node.output_json_schema)  # type: ignore[arg-type]
            if result.success:
                return NodeResult(
                    outputs=result.data or {},
                    status=NodeStatus.COMPLETED,
                    metadata={"model": model, "attempts": attempt + 1},
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
            metadata={"model": model},
        )

    async def _call_llm(
        self,
        client: AsyncOpenAI,
        model: str,
        messages: list[dict[str, str]],
        node: LLMOperator,
    ) -> tuple[str, str | None]:
        """Call the LLM with retry on transient API errors.

        Returns (response_text, error_message). On success error_message
        is None; on exhausted retries response_text is empty.
        """
        max_retries = 3
        backoff = 1.0

        for attempt in range(max_retries):
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": node.temperature,
                }
                if node.max_tokens is not None:
                    kwargs["max_tokens"] = node.max_tokens

                resp = await client.chat.completions.create(**kwargs)
                content = resp.choices[0].message.content or ""
                return content, None

            except (RateLimitError, APITimeoutError) as exc:
                if attempt < max_retries - 1:
                    logger.warning(
                        "Transient API error (attempt %d/%d): %s",
                        attempt + 1, max_retries, exc,
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2
                else:
                    return "", f"API error after {max_retries} retries: {exc}"

            except APIError as exc:
                return "", f"API error: {exc}"

            except Exception as exc:
                return "", f"Unexpected error calling LLM: {exc}"

        return "", "LLM call failed"
