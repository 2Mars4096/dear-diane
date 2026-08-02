"""Tests for dan.llm_core.gateway — ModelGateway concerns."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest

from dan.llm_core import (
    CompletionResult,
    GatewayCall,
    GatewayConfig,
    ModelGateway,
    StreamChunk,
    gateway_llm_call,
    gateway_meta_llm_call,
)
from dan.providers import LLMAuthenticationError, BudgetExceededError
from dan.providers.cost_tracker import CostTracker
from dan.providers.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeProvider:
    """Minimal LLMProvider for testing."""

    def __init__(
        self,
        text: str = "ok",
        usage: dict[str, int] | None = None,
        fail_times: int = 0,
        fail_exc: type[Exception] = RuntimeError,
        latency: float = 0.0,
    ) -> None:
        self._text = text
        self._usage = usage or {"prompt_tokens": 10, "completion_tokens": 5}
        self._fail_times = fail_times
        self._fail_exc = fail_exc
        self._latency = latency
        self.call_count = 0
        self.last_messages: list[dict[str, Any]] = []
        self.last_model: str = ""
        self.in_flight = 0
        self.max_in_flight = 0
        self.start_times: list[float] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        self.call_count += 1
        self.last_messages = messages
        self.last_model = model
        self.start_times.append(asyncio.get_running_loop().time())
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self._latency:
                await asyncio.sleep(self._latency)
            if self.call_count <= self._fail_times:
                raise self._fail_exc(f"Fail #{self.call_count}")
            return CompletionResult(
                text=self._text, usage=self._usage, model=model
            )
        finally:
            self.in_flight -= 1

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        self.call_count += 1
        self.last_messages = messages
        if self._latency:
            await asyncio.sleep(self._latency)

        async def _gen():
            words = self._text.split()
            acc = ""
            for i, w in enumerate(words):
                acc += ("" if i == 0 else " ") + w
                yield StreamChunk(
                    delta=w,
                    accumulated=acc,
                    done=(i == len(words) - 1),
                    usage=self._usage if i == len(words) - 1 else None,
                )

        return _gen()


@dataclass
class FakePIISession:
    """Duck-typed PII session with tokenize/detokenize."""

    def tokenize(self, text: str) -> str:
        return text.replace("secret", "[PII]")

    def detokenize(self, text: str) -> str:
        return text.replace("[PII]", "secret")


def _make_gateway(
    provider: FakeProvider | None = None,
    config: GatewayConfig | None = None,
    cost_tracker: CostTracker | None = None,
    pii_session: Any | None = None,
    telemetry_callback: Any | None = None,
    fallback_provider: FakeProvider | None = None,
) -> ModelGateway:
    registry = ProviderRegistry()
    registry.register("default", provider or FakeProvider())
    if fallback_provider:
        registry.register("openai", fallback_provider)
        registry.set_model_override("gpt-4o-mini", "openai")
    return ModelGateway(
        registry=registry,
        config=config or GatewayConfig(
            pii_enabled=False,
            retry_enabled=False,
            telemetry_enabled=False,
        ),
        cost_tracker=cost_tracker,
        pii_session=pii_session,
        telemetry_callback=telemetry_callback,
    )


# ---------------------------------------------------------------------------
# Basic delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_delegates_to_provider():
    provider = FakeProvider(text="hello world")
    gw = _make_gateway(provider)
    result = await gw.complete(
        [{"role": "user", "content": "hi"}],
        "test-model",
        temperature=0.5,
    )
    assert result.text == "hello world"
    assert result.model == "test-model"
    assert provider.call_count == 1
    assert provider.last_model == "test-model"


@pytest.mark.asyncio
async def test_stream_delegates_to_provider():
    provider = FakeProvider(text="one two three")
    gw = _make_gateway(provider)
    chunks: list[StreamChunk] = []
    async for chunk in gw.stream(
        [{"role": "user", "content": "hi"}],
        "test-model",
    ):
        chunks.append(chunk)
    assert len(chunks) == 3
    assert chunks[-1].done is True
    assert chunks[-1].accumulated == "one two three"


@pytest.mark.asyncio
async def test_resolve_returns_raw_provider():
    provider = FakeProvider()
    gw = _make_gateway(provider)
    assert gw.resolve("test-model") is provider


def test_registry_property():
    registry = ProviderRegistry()
    gw = ModelGateway(registry)
    assert gw.registry is registry


# ---------------------------------------------------------------------------
# Dispatch broker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_limits_concurrency_across_gateway_instances():
    provider = FakeProvider(latency=0.03)
    registry = ProviderRegistry()
    registry.register("default", provider)
    config = GatewayConfig(
        retry_enabled=False,
        pii_enabled=False,
        telemetry_enabled=False,
        dispatch_enabled=True,
        dispatch_group="shared-test",
        dispatch_max_in_flight=1,
        dispatch_max_queue_size=8,
    )
    gw_a = ModelGateway(registry=registry, config=config)
    gw_b = ModelGateway(registry=registry, config=config)

    await asyncio.gather(
        gw_a.complete([{"role": "user", "content": "a"}], "test-model"),
        gw_b.complete([{"role": "user", "content": "b"}], "test-model"),
    )

    assert provider.max_in_flight == 1


@pytest.mark.asyncio
async def test_dispatcher_records_queue_wait_and_provider_name():
    provider = FakeProvider(latency=0.03)
    telemetry_calls: list[GatewayCall] = []
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            retry_enabled=False,
            pii_enabled=False,
            telemetry_enabled=True,
            dispatch_enabled=True,
            dispatch_group="telemetry-test",
            dispatch_max_in_flight=1,
            dispatch_max_queue_size=8,
        ),
        telemetry_callback=telemetry_calls.append,
    )

    await asyncio.gather(
        gw.complete([{"role": "user", "content": "a"}], "test-model"),
        gw.complete([{"role": "user", "content": "b"}], "test-model"),
    )

    assert any(call.provider_name == "default" for call in telemetry_calls)
    assert any(call.dispatch_group == "telemetry-test" for call in telemetry_calls)
    assert any(call.queue_wait_ms > 0 for call in telemetry_calls)


@pytest.mark.asyncio
async def test_dispatcher_rejects_when_queue_is_full():
    provider = FakeProvider(latency=0.05)
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            retry_enabled=False,
            pii_enabled=False,
            telemetry_enabled=False,
            dispatch_enabled=True,
            dispatch_group="queue-full-test",
            dispatch_max_in_flight=1,
            dispatch_max_queue_size=1,
        ),
    )

    first = asyncio.create_task(
        gw.complete([{"role": "user", "content": "a"}], "test-model")
    )
    await asyncio.sleep(0)
    second = asyncio.create_task(
        gw.complete([{"role": "user", "content": "b"}], "test-model")
    )
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="dispatch queue is full"):
        await gw.complete([{"role": "user", "content": "c"}], "test-model")

    await asyncio.gather(first, second)


@pytest.mark.asyncio
async def test_dispatcher_can_rate_limit_call_starts():
    provider = FakeProvider(latency=0.0)
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            retry_enabled=False,
            pii_enabled=False,
            telemetry_enabled=False,
            dispatch_enabled=True,
            dispatch_group="rate-test",
            dispatch_max_in_flight=4,
            dispatch_max_queue_size=8,
            dispatch_max_requests_per_second=20.0,
        ),
    )

    await asyncio.gather(
        gw.complete([{"role": "user", "content": "a"}], "test-model"),
        gw.complete([{"role": "user", "content": "b"}], "test-model"),
    )

    assert len(provider.start_times) == 2
    assert provider.start_times[1] - provider.start_times[0] >= 0.04


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_failure():
    provider = FakeProvider(text="recovered", fail_times=2)
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            retry_enabled=True,
            retry_max_attempts=3,
            retry_backoff_base=0.01,
            pii_enabled=False,
            telemetry_enabled=False,
        ),
    )
    result = await gw.complete(
        [{"role": "user", "content": "hi"}], "test-model"
    )
    assert result.text == "recovered"
    assert provider.call_count == 3


@pytest.mark.asyncio
async def test_retry_exhausted_raises():
    provider = FakeProvider(fail_times=5)
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            retry_enabled=True,
            retry_max_attempts=2,
            retry_backoff_base=0.01,
            pii_enabled=False,
            telemetry_enabled=False,
        ),
    )
    with pytest.raises(RuntimeError, match="Fail #2"):
        await gw.complete(
            [{"role": "user", "content": "hi"}], "test-model"
        )
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_retry_skipped_for_auth_error():
    provider = FakeProvider(
        fail_times=5, fail_exc=LLMAuthenticationError
    )
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            retry_enabled=True,
            retry_max_attempts=3,
            retry_backoff_base=0.01,
            pii_enabled=False,
            telemetry_enabled=False,
        ),
    )
    with pytest.raises(LLMAuthenticationError):
        await gw.complete(
            [{"role": "user", "content": "hi"}], "test-model"
        )
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_per_call_retry_override_disables_retry():
    provider = FakeProvider(fail_times=1)
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            retry_enabled=True,
            retry_max_attempts=3,
            retry_backoff_base=0.01,
            pii_enabled=False,
            telemetry_enabled=False,
        ),
    )
    with pytest.raises(RuntimeError):
        await gw.complete(
            [{"role": "user", "content": "hi"}], "test-model", retry=False
        )
    assert provider.call_count == 1


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_enforced():
    provider = FakeProvider(latency=5.0)
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            timeout_seconds=0.05,
            retry_enabled=False,
            pii_enabled=False,
            telemetry_enabled=False,
        ),
    )
    with pytest.raises(asyncio.TimeoutError):
        await gw.complete(
            [{"role": "user", "content": "hi"}], "test-model"
        )


@pytest.mark.asyncio
async def test_per_call_timeout_override():
    provider = FakeProvider(latency=0.02)
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            timeout_seconds=10.0,
            retry_enabled=False,
            pii_enabled=False,
            telemetry_enabled=False,
        ),
    )
    result = await gw.complete(
        [{"role": "user", "content": "hi"}], "test-model", timeout=5.0
    )
    assert result.text == "ok"


# ---------------------------------------------------------------------------
# PII
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_tokenize_and_detokenize():
    provider = FakeProvider(text="response with [PII] data")
    pii = FakePIISession()
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            pii_enabled=True,
            retry_enabled=False,
            telemetry_enabled=False,
        ),
        pii_session=pii,
    )
    result = await gw.complete(
        [{"role": "user", "content": "my secret info"}],
        "test-model",
    )
    assert provider.last_messages[0]["content"] == "my [PII] info"
    assert result.text == "response with secret data"


@pytest.mark.asyncio
async def test_pii_disabled_per_call():
    provider = FakeProvider(text="plain")
    pii = FakePIISession()
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            pii_enabled=True,
            retry_enabled=False,
            telemetry_enabled=False,
        ),
        pii_session=pii,
    )
    result = await gw.complete(
        [{"role": "user", "content": "my secret info"}],
        "test-model",
        pii=False,
    )
    assert provider.last_messages[0]["content"] == "my secret info"
    assert result.text == "plain"


@pytest.mark.asyncio
async def test_pii_no_session_is_noop():
    provider = FakeProvider(text="plain")
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            pii_enabled=True,
            retry_enabled=False,
            telemetry_enabled=False,
        ),
    )
    result = await gw.complete(
        [{"role": "user", "content": "my secret info"}],
        "test-model",
    )
    assert provider.last_messages[0]["content"] == "my secret info"
    assert result.text == "plain"


@pytest.mark.asyncio
async def test_pii_applied_to_stream():
    provider = FakeProvider(text="[PII] result")
    pii = FakePIISession()
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            pii_enabled=True,
            retry_enabled=False,
            telemetry_enabled=False,
        ),
        pii_session=pii,
    )
    chunks: list[StreamChunk] = []
    async for chunk in gw.stream(
        [{"role": "user", "content": "my secret info"}],
        "test-model",
    ):
        chunks.append(chunk)

    assert provider.last_messages[0]["content"] == "my [PII] info"
    assert chunks[0].delta == "secret"


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_check_blocks_when_over():
    tracker = CostTracker(run_budget=0.001, on_budget_exceeded="warn")
    tracker.record("prior", "gpt-4o", {"prompt_tokens": 100000, "completion_tokens": 50000})

    provider = FakeProvider()
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            budget_enabled=True,
            retry_enabled=False,
            pii_enabled=False,
            telemetry_enabled=False,
        ),
        cost_tracker=tracker,
    )
    with pytest.raises(BudgetExceededError):
        await gw.complete(
            [{"role": "user", "content": "hi"}], "test-model"
        )
    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_budget_check_disabled_per_call():
    tracker = CostTracker(run_budget=0.001, on_budget_exceeded="warn")
    tracker.record("prior", "gpt-4o", {"prompt_tokens": 100000, "completion_tokens": 50000})

    provider = FakeProvider()
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            budget_enabled=True,
            retry_enabled=False,
            pii_enabled=False,
            telemetry_enabled=False,
        ),
        cost_tracker=tracker,
    )
    result = await gw.complete(
        [{"role": "user", "content": "hi"}],
        "test-model",
        budget_check=False,
    )
    assert result.text == "ok"


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telemetry_callback_invoked():
    calls: list[GatewayCall] = []
    provider = FakeProvider(text="done", usage={"prompt_tokens": 20, "completion_tokens": 10})
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            telemetry_enabled=True,
            retry_enabled=False,
            pii_enabled=False,
        ),
        telemetry_callback=calls.append,
    )
    result = await gw.complete(
        [{"role": "user", "content": "hi"}], "test-model"
    )
    assert result.text == "done"
    assert len(calls) == 1
    assert calls[0].model == "test-model"
    assert calls[0].elapsed_ms is not None
    assert calls[0].elapsed_ms > 0
    assert calls[0].usage == {"prompt_tokens": 20, "completion_tokens": 10}


@pytest.mark.asyncio
async def test_telemetry_records_to_cost_tracker():
    tracker = CostTracker()
    provider = FakeProvider(usage={"prompt_tokens": 100, "completion_tokens": 50})
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            telemetry_enabled=True,
            retry_enabled=False,
            pii_enabled=False,
        ),
        cost_tracker=tracker,
    )
    await gw.complete(
        [{"role": "user", "content": "hi"}], "gpt-4o"
    )
    assert tracker.total_cost() > 0


@pytest.mark.asyncio
async def test_telemetry_callback_error_is_swallowed():
    def bad_callback(call: GatewayCall) -> None:
        raise ValueError("boom")

    provider = FakeProvider()
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            telemetry_enabled=True,
            retry_enabled=False,
            pii_enabled=False,
        ),
        telemetry_callback=bad_callback,
    )
    result = await gw.complete(
        [{"role": "user", "content": "hi"}], "test-model"
    )
    assert result.text == "ok"


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_on_primary_failure():
    primary = FakeProvider(fail_times=5)
    fallback = FakeProvider(text="fallback ok")
    gw = _make_gateway(
        primary,
        config=GatewayConfig(
            fallback_model="gpt-4o-mini",
            retry_enabled=False,
            pii_enabled=False,
            telemetry_enabled=False,
        ),
        fallback_provider=fallback,
    )
    result = await gw.complete(
        [{"role": "user", "content": "hi"}], "test-model"
    )
    assert result.text == "fallback ok"
    assert primary.call_count == 1
    assert fallback.call_count == 1


@pytest.mark.asyncio
async def test_no_fallback_when_same_model():
    provider = FakeProvider(fail_times=5)
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            fallback_model="test-model",
            retry_enabled=False,
            pii_enabled=False,
            telemetry_enabled=False,
        ),
    )
    with pytest.raises(RuntimeError):
        await gw.complete(
            [{"role": "user", "content": "hi"}], "test-model"
        )


# ---------------------------------------------------------------------------
# Combined concerns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_and_retry_combined():
    provider = FakeProvider(text="[PII] recovered", fail_times=1)
    pii = FakePIISession()
    gw = _make_gateway(
        provider,
        config=GatewayConfig(
            pii_enabled=True,
            retry_enabled=True,
            retry_max_attempts=3,
            retry_backoff_base=0.01,
            telemetry_enabled=False,
        ),
        pii_session=pii,
    )
    result = await gw.complete(
        [{"role": "user", "content": "my secret info"}],
        "test-model",
    )
    assert provider.last_messages[0]["content"] == "my [PII] info"
    assert result.text == "secret recovered"
    assert provider.call_count == 2


# ---------------------------------------------------------------------------
# build_gateway factory
# ---------------------------------------------------------------------------


def test_build_gateway_from_params():
    from dan.llm_core import build_gateway

    gw = build_gateway(api_key="test-key")
    assert isinstance(gw, ModelGateway)
    assert gw.registry.has_provider("default")


def test_build_gateway_with_config_override():
    from dan.llm_core import build_gateway

    cfg = GatewayConfig(retry_max_attempts=5, pii_enabled=False)
    gw = build_gateway(api_key="test-key", gateway_config=cfg)
    assert gw._config.retry_max_attempts == 5
    assert gw._config.pii_enabled is False


# ---------------------------------------------------------------------------
# Import boundary: llm_core must not import from server/engine/cli
# ---------------------------------------------------------------------------


def test_llm_core_import_boundary():
    """llm_core must not import from server/, engine/, cli/, concierge/."""
    import ast
    from pathlib import Path

    llm_core_root = Path(__file__).resolve().parents[2] / "src" / "dan" / "llm_core"
    forbidden_prefixes = ("dan.server", "dan.engine", "dan.cli", "dan.concierge")
    violations: list[str] = []

    for py_file in sorted(llm_core_root.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(forbidden_prefixes):
                    violations.append(
                        f"{py_file.name}:{node.lineno} -> {node.module}"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden_prefixes):
                        violations.append(
                            f"{py_file.name}:{node.lineno} -> {alias.name}"
                        )

    assert not violations, (
        "llm_core has forbidden imports:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# gateway_llm_call helper (meta/-compatible callable factory)
# ---------------------------------------------------------------------------


class TestGatewayLLMCallHelper:
    """Test the meta/-compatible llm_call factory."""

    @pytest.mark.asyncio
    async def test_returns_text(self):
        provider = FakeProvider(text="hello from gateway")
        gw = _make_gateway(provider)
        llm_call = gateway_llm_call(gw)
        result = await llm_call(
            [{"role": "user", "content": "hi"}], "test-model"
        )
        assert result == "hello from gateway"
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_uses_default_model(self):
        provider = FakeProvider(text="ok")
        gw = _make_gateway(provider)
        llm_call = gateway_llm_call(gw, default_model="custom-model")
        await llm_call([{"role": "user", "content": "hi"}])
        assert provider.last_model == "custom-model"

    @pytest.mark.asyncio
    async def test_explicit_model_overrides_default(self):
        provider = FakeProvider(text="ok")
        gw = _make_gateway(provider)
        llm_call = gateway_llm_call(gw, default_model="custom-model")
        await llm_call(
            [{"role": "user", "content": "hi"}], "override-model"
        )
        assert provider.last_model == "override-model"

    @pytest.mark.asyncio
    async def test_forwards_kwargs(self):
        provider = FakeProvider(text="ok")
        gw = _make_gateway(provider)
        llm_call = gateway_llm_call(gw)
        await llm_call(
            [{"role": "user", "content": "hi"}],
            "test-model",
            temperature=0.2,
            max_tokens=100,
        )
        assert provider.call_count == 1

    @pytest.mark.asyncio
    async def test_gateway_concerns_still_apply(self):
        """PII, retry, etc. from the gateway should still work through the helper."""
        provider = FakeProvider(text="[PII] recovered", fail_times=1)
        pii = FakePIISession()
        gw = _make_gateway(
            provider,
            config=GatewayConfig(
                pii_enabled=True,
                retry_enabled=True,
                retry_max_attempts=3,
                retry_backoff_base=0.01,
                telemetry_enabled=False,
            ),
            pii_session=pii,
        )
        llm_call = gateway_llm_call(gw)
        result = await llm_call(
            [{"role": "user", "content": "my secret info"}], "test-model"
        )
        assert result == "secret recovered"
        assert provider.last_messages[0]["content"] == "my [PII] info"
        assert provider.call_count == 2


class TestGatewayMetaLLMCallHelper:
    """Meta planner signature: (system_prompt, user_prompt, model, temperature)."""

    @pytest.mark.asyncio
    async def test_meta_signature_returns_text(self):
        provider = FakeProvider(text="plan json")
        gw = _make_gateway(provider)
        llm = gateway_meta_llm_call(gw, fallback_model="fallback-m")
        result = await llm("sys", "user", None, 0.5)
        assert result == "plan json"
        assert len(provider.last_messages) == 2
        assert provider.last_messages[0]["role"] == "system"
        assert provider.last_messages[0]["content"] == "sys"
        assert provider.last_messages[1]["content"] == "user"
        assert provider.last_model == "fallback-m"

    @pytest.mark.asyncio
    async def test_explicit_model_overrides_fallback(self):
        provider = FakeProvider(text="ok")
        gw = _make_gateway(provider)
        llm = gateway_meta_llm_call(gw, fallback_model="fb")
        await llm("s", "u", "explicit-model", 0.1)
        assert provider.last_model == "explicit-model"
