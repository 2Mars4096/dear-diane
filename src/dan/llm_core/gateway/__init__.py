"""ModelGateway — unified LLM call boundary with configurable concerns.

Wraps a :class:`ProviderRegistry` and layers PII tokenization, retry,
timeout, budget, telemetry, and fallback on top of raw provider calls.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from dan.llm_core.config import GatewayConfig
from dan.llm_core.gateway.dispatch import get_shared_dispatcher
from dan.llm_core.types import GatewayCall
from dan.providers import (
    BudgetExceededError,
    CompletionResult,
    LLMAuthenticationError,
    StreamChunk,
)
from dan.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from dan.providers import CostTracker, LLMProvider

logger = logging.getLogger(__name__)


def gateway_llm_call(
    gateway: ModelGateway, default_model: str = "gpt-4o"
) -> Callable[..., Any]:
    """Create an ``llm_call`` callable backed by the gateway.

    Returns an async function with signature::

        async def llm_call(messages, model=default_model, **kwargs) -> str

    Compatible with the ``meta/`` injection pattern where callers receive
    an ``llm_call`` callable rather than a gateway reference.
    """

    async def _call(
        messages: list[dict[str, Any]],
        model: str = default_model,
        **kwargs: Any,
    ) -> str:
        result = await gateway.complete(messages, model, **kwargs)
        return result.text

    return _call


def gateway_meta_llm_call(
    gateway: ModelGateway,
    *,
    fallback_model: str = "gpt-4o",
) -> Callable[..., Any]:
    """Build the ``(system_prompt, user_prompt, model, temperature) -> str`` async
    callable expected by :class:`~dan.meta.planner.WorkflowPlanner` and related
    meta modules.

    Same cross-cutting behavior as :func:`gateway_llm_call` (retries, timeout,
    telemetry, etc.) because calls go through :meth:`ModelGateway.complete`.
    """

    async def _call(
        system_prompt: str,
        user_prompt: str,
        model: str | None,
        temperature: float,
    ) -> str:
        model_name = model or fallback_model
        result = await gateway.complete(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model_name,
            temperature=temperature,
        )
        return result.text

    return _call


class ModelGateway:
    """Unified gateway for all LLM calls.

    Callers go through :meth:`complete` or :meth:`stream` instead of
    touching the provider registry directly.  Each cross-cutting concern
    (PII, retry, timeout, budget, telemetry, fallback) is applied
    uniformly and can be toggled per-call.
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        config: GatewayConfig | None = None,
        cost_tracker: CostTracker | None = None,
        pii_session: Any | None = None,
        telemetry_callback: Callable[[GatewayCall], Any] | None = None,
    ) -> None:
        self._registry = registry
        self._config = config or GatewayConfig()
        self._cost_tracker = cost_tracker
        self._pii_session = pii_session
        self._telemetry_callback = telemetry_callback

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float | None = None,
        retry: bool | None = None,
        pii: bool | None = None,
        budget_check: bool | None = None,
        provider_name: str | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        cfg = self._config
        do_pii = pii if pii is not None else cfg.pii_enabled
        do_retry = retry if retry is not None else cfg.retry_enabled
        do_budget = budget_check if budget_check is not None else cfg.budget_enabled
        effective_timeout = timeout if timeout is not None else cfg.timeout_seconds
        pii_session = kwargs.pop("pii_session", None) or self._pii_session

        call_meta = GatewayCall(model=model, started_at=time.monotonic())

        work_messages = messages
        if do_pii and pii_session is not None:
            work_messages = self._tokenize_messages(messages, pii_session)
            call_meta.pii_applied = True

        if do_budget and self._cost_tracker is not None:
            if self._cost_tracker.is_over_budget():
                raise BudgetExceededError(
                    f"Budget already exceeded before call "
                    f"(${self._cost_tracker.total_cost():.4f})"
                )

        async def _inner_call(target_model: str) -> CompletionResult:
            provider, resolved_provider_name = self._resolve_provider(
                target_model,
                provider_name=provider_name,
            )
            call_meta.provider_name = resolved_provider_name
            dispatcher = get_shared_dispatcher(cfg)
            if dispatcher is None:
                call_meta.dispatch_attempts += 1
                return await provider.complete(
                    messages=work_messages,
                    model=target_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
            async with dispatcher.slot() as lease:
                call_meta.dispatch_group = lease.dispatch_group
                call_meta.dispatch_attempts += 1
                call_meta.queue_wait_ms += lease.wait_ms
                call_meta.queue_depth_at_submit = max(
                    call_meta.queue_depth_at_submit,
                    lease.queue_depth_at_submit,
                )
                return await provider.complete(
                    messages=work_messages,
                    model=target_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )

        try:
            result = await self._with_timeout_and_retry(
                _inner_call,
                model,
                timeout=effective_timeout,
                do_retry=do_retry,
                max_attempts=cfg.retry_max_attempts,
                backoff_base=cfg.retry_backoff_base,
                call_meta=call_meta,
            )
        except Exception as exc:
            if "dispatch queue is full" in str(exc):
                call_meta.queue_rejected = True
            if cfg.fallback_model and cfg.fallback_model != model:
                logger.warning(
                    "Primary model %s failed; falling back to %s",
                    model,
                    cfg.fallback_model,
                )
                call_meta.fallback_used = True
                call_meta.retries = 0
                result = await self._with_timeout_and_retry(
                    _inner_call,
                    cfg.fallback_model,
                    timeout=effective_timeout,
                    do_retry=do_retry,
                    max_attempts=cfg.retry_max_attempts,
                    backoff_base=cfg.retry_backoff_base,
                    call_meta=call_meta,
                )
            else:
                raise

        if do_pii and pii_session is not None:
            result = self._detokenize_result(result, pii_session)

        elapsed = (time.monotonic() - call_meta.started_at) * 1000
        call_meta.elapsed_ms = elapsed
        if result.usage:
            call_meta.usage = result.usage

        if cfg.telemetry_enabled and self._cost_tracker is not None:
            self._cost_tracker.record(
                "gateway",
                result.model or model,
                result.usage,
                cached_input_tokens=result.cached_input_tokens,
                cache_write_tokens=result.cache_write_tokens,
            )

        if cfg.telemetry_enabled and self._telemetry_callback is not None:
            try:
                self._telemetry_callback(call_meta)
            except Exception:
                logger.debug("Telemetry callback error", exc_info=True)

        return result

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float | None = None,
        pii: bool | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        cfg = self._config
        do_pii = pii if pii is not None else cfg.pii_enabled
        effective_timeout = timeout if timeout is not None else cfg.timeout_seconds
        pii_session = kwargs.pop("pii_session", None) or self._pii_session

        work_messages = messages
        if do_pii and pii_session is not None:
            work_messages = self._tokenize_messages(messages, pii_session)

        provider, _ = self._resolve_provider(model, provider_name=None)
        dispatcher = get_shared_dispatcher(cfg)

        async def _produce() -> AsyncIterator[StreamChunk]:
            if dispatcher is None:
                stream_result = provider.stream(
                    messages=work_messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                if not hasattr(stream_result, "__aiter__"):
                    stream_result = await stream_result
                async for chunk in stream_result:
                    if do_pii and pii_session is not None and chunk.delta:
                        chunk = StreamChunk(
                            delta=self._detokenize_text(chunk.delta, pii_session),
                            accumulated=self._detokenize_text(
                                chunk.accumulated,
                                pii_session,
                            ),
                            done=chunk.done,
                            usage=chunk.usage,
                        )
                    yield chunk
                return

            async with dispatcher.slot():
                stream_result = provider.stream(
                    messages=work_messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                if not hasattr(stream_result, "__aiter__"):
                    stream_result = await stream_result
                async for chunk in stream_result:
                    if do_pii and pii_session is not None and chunk.delta:
                        chunk = StreamChunk(
                            delta=self._detokenize_text(chunk.delta, pii_session),
                            accumulated=self._detokenize_text(
                                chunk.accumulated,
                                pii_session,
                            ),
                            done=chunk.done,
                            usage=chunk.usage,
                        )
                    yield chunk

        if effective_timeout and effective_timeout > 0:
            gen = _produce()
            deadline = time.monotonic() + effective_timeout
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise asyncio.TimeoutError(
                            f"Stream timed out after {effective_timeout}s"
                        )
                    chunk = await asyncio.wait_for(
                        gen.__anext__(), timeout=remaining
                    )
                    yield chunk
            except StopAsyncIteration:
                return
        else:
            async for chunk in _produce():
                yield chunk

    def resolve(self, model: str) -> LLMProvider:
        """Direct provider resolution (escape hatch)."""
        return self._registry.resolve(model)

    @property
    def registry(self) -> ProviderRegistry:
        """Access the underlying registry for backward compat."""
        return self._registry

    @property
    def config(self) -> GatewayConfig:
        """Access the gateway configuration."""
        return self._config

    # ------------------------------------------------------------------
    # Provider helpers
    # ------------------------------------------------------------------

    def _resolve_provider(
        self,
        model: str,
        *,
        provider_name: str | None,
    ) -> tuple[LLMProvider, str | None]:
        if provider_name:
            provider = self._registry.get(provider_name)
            if provider is None:
                raise KeyError(
                    f"No provider registered under explicit name {provider_name!r}. "
                    f"Registered providers: {sorted(self._registry.provider_names())}"
                )
            return provider, provider_name

        provider = self._registry.resolve(model)
        resolved_name: str | None = None
        resolve_name = getattr(self._registry, "resolve_name", None)
        if callable(resolve_name):
            try:
                resolved_name = resolve_name(model)
            except Exception:
                logger.debug(
                    "Unable to resolve provider name for model %s",
                    model,
                    exc_info=True,
                )
        return provider, resolved_name

    # ------------------------------------------------------------------
    # PII helpers (duck-typed)
    # ------------------------------------------------------------------

    def _tokenize_messages(
        self, messages: list[dict[str, Any]], session: Any
    ) -> list[dict[str, Any]]:
        if not hasattr(session, "tokenize"):
            return messages
        out: list[dict[str, Any]] = []
        for msg in messages:
            m = copy.copy(msg)
            content = m.get("content")
            if isinstance(content, str):
                m["content"] = session.tokenize(content)
            out.append(m)
        return out

    def _detokenize_result(
        self, result: CompletionResult, session: Any
    ) -> CompletionResult:
        text = self._detokenize_text(result.text, session)
        return CompletionResult(
            text=text,
            usage=result.usage,
            model=result.model,
            tool_calls=result.tool_calls,
            cached_input_tokens=result.cached_input_tokens,
            cache_write_tokens=result.cache_write_tokens,
            finish_reason=result.finish_reason,
            raw_assistant_message=result.raw_assistant_message,
            provider_metadata=result.provider_metadata,
        )

    def _detokenize_text(self, text: str, session: Any) -> str:
        if not hasattr(session, "detokenize"):
            return text
        return session.detokenize(text)

    # ------------------------------------------------------------------
    # Retry + timeout orchestration
    # ------------------------------------------------------------------

    async def _with_timeout_and_retry(
        self,
        call: Callable[..., Any],
        model: str,
        *,
        timeout: float,
        do_retry: bool,
        max_attempts: int,
        backoff_base: float,
        call_meta: GatewayCall,
    ) -> CompletionResult:
        attempts = max_attempts if do_retry else 1
        last_exc: Exception | None = None

        for attempt in range(attempts):
            try:
                coro = call(model)
                if timeout and timeout > 0:
                    return await asyncio.wait_for(coro, timeout=timeout)
                return await coro
            except LLMAuthenticationError:
                raise
            except asyncio.TimeoutError:
                call_meta.retries = attempt
                call_meta.error = f"Timeout after {timeout}s (attempt {attempt + 1})"
                last_exc = asyncio.TimeoutError(call_meta.error)
                if attempt + 1 >= attempts:
                    raise last_exc
            except Exception as exc:
                call_meta.retries = attempt
                call_meta.error = f"{type(exc).__name__}: {exc}"
                last_exc = exc
                if attempt + 1 >= attempts:
                    raise

            delay = backoff_base * (2 ** attempt)
            logger.debug(
                "Retry %d/%d for model %s after %.1fs (error: %s)",
                attempt + 1,
                attempts,
                model,
                delay,
                call_meta.error,
            )
            await asyncio.sleep(delay)

        assert last_exc is not None
        raise last_exc
