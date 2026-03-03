"""LLM executor — multi-provider chat completions with output normalization."""

from __future__ import annotations

import asyncio
import logging
import string
from typing import Any

from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.normalizer import OutputNormalizer
from dan.engine.state import NodeStatus
from dan.models.nodes import LLMOperator, NodeBase, RetryPolicy

logger = logging.getLogger(__name__)

_LLM_DEFAULT_RETRY = RetryPolicy(max_retries=3)


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
    """Executes LLMOperator nodes via the provider registry.

    Handles prompt rendering, chat completion calls, output normalization
    (parse/validate/re-prompt loop), and retry policy for API failures.
    Backward compatible: falls back to direct AsyncOpenAI if no provider
    registry is available.
    """

    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        self._client = client

    def _get_client(self, context: ExecutionContext) -> AsyncOpenAI:
        """Legacy fallback — used only when provider_registry is not available."""
        if self._client is not None:
            return self._client
        return AsyncOpenAI(
            api_key=context.config.llm_api_key,
            base_url=context.config.llm_base_url,
        )

    def _resolve_provider(self, model: str, context: ExecutionContext):
        """Resolve the LLM provider for a model, with backward compat fallback."""
        if context.provider_registry is not None:
            return context.provider_registry.resolve(model)

        from dan.providers import ProviderConfig
        from dan.providers.openai_provider import OpenAIProvider

        if self._client is not None:
            return OpenAIProvider.from_client(self._client)

        config = ProviderConfig(
            api_key=context.config.llm_api_key,
            base_url=context.config.llm_base_url,
        )
        return OpenAIProvider(config)

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, LLMOperator)
        policy = node.retry_policy or _LLM_DEFAULT_RETRY
        model = node.model or context.config.llm_default_model

        if context.model_selector is not None:
            effective_policy = context.model_selector.resolve_effective_policy(
                node, context.config,
            )
            if effective_policy is not None:
                selected = await context.model_selector.select(
                    effective_policy, node, context,
                )
                if selected:
                    model = selected
                    await context.emit_event(
                        event_type="model_selected",
                        node_id=node.id,
                        node_type="llm_operator",
                        data={"model": model, "policy_strategy": effective_policy.strategy},
                    )

        rendered_prompt = _render_template(node.prompt_template, inputs)

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

        # -- 15-1: Hyperedge pre-prompt injection ------------------------------
        if (
            getattr(context, "hyperedge_resolver", None)
            and getattr(context.config, "hyperedge_enforcement", "off") != "off"
        ):
            messages = context.hyperedge_resolver.apply_pre_prompt(node, messages)

        max_norm_retries = context.config.output_norm_max_retries
        has_schema = node.output_json_schema is not None

        last_error: str | None = None
        cumulative_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for attempt in range(1 + max_norm_retries):
            raw_text, api_error, usage = await self._call_llm(
                model, messages, node, context=context, attempt=attempt
            )
            if usage:
                for k in cumulative_usage:
                    cumulative_usage[k] += usage.get(k, 0)
                if context.cost_tracker is not None:
                    context.cost_tracker.record(node.id, model, usage)

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
                data = result.data or {}
                # Add "result" with full object so downstream gates can evaluate conditions + pass through
                outputs = {**data, "result": data}
                return NodeResult(
                    outputs=outputs,
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

        fail_meta = {"model": model, "usage": cumulative_usage}

        if policy.on_failure == "skip":
            return NodeResult(
                outputs={}, status=NodeStatus.SKIPPED, metadata=fail_meta,
            )
        if policy.on_failure == "halt":
            return NodeResult(
                outputs={}, status=NodeStatus.FAILED,
                error=last_error or "LLM execution failed",
                metadata={**fail_meta, "halt": True},
            )
        return NodeResult(
            outputs={}, status=NodeStatus.FAILED,
            error=last_error or "LLM execution failed",
            metadata=fail_meta,
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
        policy = node.retry_policy or _LLM_DEFAULT_RETRY
        max_retries = max(policy.max_retries, 1)
        backoff = policy.backoff
        current_model = model

        for retry in range(max_retries):
            try:
                provider = self._resolve_provider(current_model, context) if context else None

                if provider is not None:
                    text, usage = await self._call_via_provider(
                        provider, current_model, messages, node, context, attempt,
                    )
                    return text, None, usage
                else:
                    # Absolute fallback — no context available
                    return "", "No execution context available", None

            except (RateLimitError, APITimeoutError) as exc:
                if retry < max_retries - 1:
                    logger.warning(
                        "Transient API error (attempt %d/%d): %s",
                        retry + 1, max_retries, exc,
                    )
                    if context:
                        await context.emit_event(
                            event_type="retry_attempted",
                            node_id=node.id,
                            node_type="llm_operator",
                            data={
                                "attempt": retry + 1,
                                "max_retries": max_retries,
                                "error": str(exc),
                                "model": current_model,
                            },
                        )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, policy.backoff_max)
                else:
                    if policy.fallback_model and current_model != policy.fallback_model:
                        logger.info(
                            "Retries exhausted on '%s', trying fallback '%s'",
                            current_model, policy.fallback_model,
                        )
                        if context:
                            await context.emit_event(
                                event_type="retry_attempted",
                                node_id=node.id,
                                node_type="llm_operator",
                                data={
                                    "attempt": retry + 1,
                                    "max_retries": max_retries,
                                    "error": str(exc),
                                    "model": current_model,
                                    "fallback_model": policy.fallback_model,
                                },
                            )
                        current_model = policy.fallback_model
                        backoff = policy.backoff
                        try:
                            fb_provider = self._resolve_provider(current_model, context) if context else None
                            if fb_provider is not None:
                                from dan.providers import CompletionResult
                                result = await fb_provider.complete(
                                    messages=messages,
                                    model=current_model,
                                    temperature=node.temperature,
                                    max_tokens=node.max_tokens,
                                )
                                return result.text, None, result.usage
                            return "", f"No provider for fallback model '{current_model}'", None
                        except Exception as fb_exc:
                            return "", f"Fallback model '{current_model}' also failed: {fb_exc}", None
                    return "", f"API error after {max_retries} retries: {exc}", None

            except (APIError,) as exc:
                return "", f"API error: {exc}", None

            except Exception as exc:
                return "", f"Unexpected error calling LLM: {exc}", None

        return "", "LLM call failed", None

    async def _call_via_provider(
        self,
        provider: Any,
        model: str,
        messages: list[dict[str, str]],
        node: LLMOperator,
        context: ExecutionContext | None,
        attempt: int,
    ) -> tuple[str, dict[str, int] | None]:
        """Call LLM via provider — try streaming first, fall back to complete."""
        try:
            accumulated = ""
            chunk_count = 0
            last_usage = None
            async for chunk in provider.stream(
                messages=messages,
                model=model,
                temperature=node.temperature,
                max_tokens=node.max_tokens,
            ):
                accumulated = chunk.accumulated
                chunk_count += 1
                if context and chunk_count % 5 == 0 and not chunk.done:
                    await context.emit_event(
                        event_type="intermediate_text",
                        node_id=node.id,
                        node_type="llm_operator",
                        data={"delta": chunk.delta, "text": accumulated, "attempt": attempt},
                    )
                if chunk.done:
                    last_usage = chunk.usage

            if context and accumulated:
                await context.emit_event(
                    event_type="intermediate_text",
                    node_id=node.id,
                    node_type="llm_operator",
                    data={"delta": "", "text": accumulated, "attempt": attempt, "done": True},
                )
            return accumulated, last_usage
        except Exception:
            result = await provider.complete(
                messages=messages,
                model=model,
                temperature=node.temperature,
                max_tokens=node.max_tokens,
            )
            return result.text, result.usage
