"""Shared retry wrapper for LLM providers.

Keep transient API failure handling in the provider layer so every agent can
reuse the same policy without coupling retry logic into orchestrators, CLIs,
or individual runtimes.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from dan.providers import CompletionResult, LLMAuthenticationError, LLMProvider, ProviderConfig, StreamChunk

_RETRYABLE_EXCEPTION_NAMES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "ServiceUnavailableError",
        "OverloadedError",
    }
)
_RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_RETRYABLE_MESSAGE_TOKENS = (
    "connection error",
    "connection reset",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "temporary failure",
    "service unavailable",
    "rate limit",
    "overloaded",
    "try again",
)


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _positive_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _status_code_from_exception(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    return None


def _is_retryable_provider_exception(exc: BaseException) -> bool:
    if isinstance(exc, asyncio.CancelledError):
        return False
    if isinstance(exc, LLMAuthenticationError):
        return False
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
        return True
    if isinstance(exc, (httpx.NetworkError, httpx.TransportError)):
        return True

    status_code = _status_code_from_exception(exc)
    if status_code is not None:
        return status_code in _RETRYABLE_STATUS_CODES or status_code >= 500

    if type(exc).__name__ in _RETRYABLE_EXCEPTION_NAMES:
        return True

    message = str(exc or "").strip().lower()
    return any(token in message for token in _RETRYABLE_MESSAGE_TOKENS)


@dataclass(frozen=True)
class ProviderRetryPolicy:
    """Retry policy shared by all wrapped LLM providers."""

    max_attempts: int = 3
    initial_delay_seconds: float = 1.0
    max_delay_seconds: float = 8.0


def resolve_provider_retry_policy(
    config: ProviderConfig,
    *,
    default_max_attempts: int = 3,
    default_initial_delay_seconds: float = 1.0,
    default_max_delay_seconds: float = 8.0,
) -> ProviderRetryPolicy:
    extra = dict(config.extra or {})
    max_attempts = _positive_int(
        extra.get("retry_max_attempts") or os.environ.get("DAN_PROVIDER_RETRY_MAX_ATTEMPTS"),
        default=default_max_attempts,
    )
    initial_delay = _positive_float(
        extra.get("retry_initial_delay_seconds")
        or os.environ.get("DAN_PROVIDER_RETRY_INITIAL_DELAY_SECONDS"),
        default=default_initial_delay_seconds,
    )
    max_delay = _positive_float(
        extra.get("retry_max_delay_seconds")
        or os.environ.get("DAN_PROVIDER_RETRY_MAX_DELAY_SECONDS"),
        default=default_max_delay_seconds,
    )
    if max_delay < initial_delay:
        max_delay = initial_delay
    return ProviderRetryPolicy(
        max_attempts=max_attempts,
        initial_delay_seconds=initial_delay,
        max_delay_seconds=max_delay,
    )


class RetryingLLMProvider:
    """Thin retry wrapper around any ``LLMProvider``.

    The wrapper keeps the building-block seam narrow:
    - agents keep depending on the plain ``LLMProvider`` protocol
    - factories opt into retry once
    - provider-specific implementations stay focused on one API family
    """

    def __init__(
        self,
        provider: LLMProvider,
        *,
        policy: ProviderRetryPolicy,
    ) -> None:
        self._provider = provider
        self._policy = policy

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def with_retry_policy(self, policy: ProviderRetryPolicy) -> RetryingLLMProvider:
        """Return a scoped retry policy without mutating another run's provider."""
        return RetryingLLMProvider(self._provider, policy=policy)

    def _delay_seconds(self, attempt_index: int) -> float:
        return min(
            self._policy.initial_delay_seconds * (2 ** max(attempt_index - 1, 0)),
            self._policy.max_delay_seconds,
        )

    async def _sleep_before_retry(self, attempt_index: int) -> None:
        await asyncio.sleep(self._delay_seconds(attempt_index))

    async def _recover_before_retry(self, exc: BaseException) -> None:
        recovery = getattr(self._provider, "recover_from_error", None)
        if not callable(recovery):
            return
        try:
            result = recovery(exc)
            if inspect.isawaitable(result):
                await result
        except Exception:
            return

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        last_exc: BaseException | None = None
        for attempt in range(1, self._policy.max_attempts + 1):
            try:
                return await self._provider.complete(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
            except Exception as exc:
                last_exc = exc
                if (
                    attempt >= self._policy.max_attempts
                    or not _is_retryable_provider_exception(exc)
                ):
                    raise
                await self._recover_before_retry(exc)
                await self._sleep_before_retry(attempt)
        assert last_exc is not None
        raise last_exc

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        last_exc: BaseException | None = None
        for attempt in range(1, self._policy.max_attempts + 1):
            saw_output = False
            stream_iter: AsyncIterator[StreamChunk] | None = None
            try:
                stream_iter = self._provider.stream(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                async for chunk in stream_iter:
                    saw_output = True
                    yield chunk
                return
            except Exception as exc:
                last_exc = exc
                if stream_iter is not None and hasattr(stream_iter, "aclose"):
                    try:
                        await stream_iter.aclose()  # type: ignore[union-attr]
                    except Exception:
                        pass
                if (
                    saw_output
                    or attempt >= self._policy.max_attempts
                    or not _is_retryable_provider_exception(exc)
                ):
                    raise
                await self._recover_before_retry(exc)
                await self._sleep_before_retry(attempt)
        assert last_exc is not None
        raise last_exc


def wrap_provider_with_retries(
    provider: LLMProvider,
    config: ProviderConfig,
) -> LLMProvider:
    policy = resolve_provider_retry_policy(config)
    if policy.max_attempts <= 1:
        return provider
    return RetryingLLMProvider(provider, policy=policy)


__all__ = [
    "ProviderRetryPolicy",
    "RetryingLLMProvider",
    "resolve_provider_retry_policy",
    "wrap_provider_with_retries",
]
