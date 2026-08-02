"""Tests for provider-level prompt caching (Plan 18-2, Task 1).

Covers:
- Anthropic cache_control hint injection and token extraction
- OpenAI stable prefix ordering
- Google no-op pass-through
- CompletionResult cached token fields
- CostTracker cache metrics accumulation and cache_summary()
- Backward compat: providers without apply_cache_hints work via fallback
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dan.providers import CompletionResult, apply_cache_hints
from dan.providers.cost_tracker import CostTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_anthropic_provider():
    """Instantiate AnthropicProvider with a mocked SDK client."""
    with patch("anthropic.AsyncAnthropic"):
        from dan.providers.anthropic_provider import AnthropicProvider
        from dan.providers import ProviderConfig

        return AnthropicProvider(ProviderConfig(api_key="test"))


def _make_openai_provider():
    from dan.providers.openai_provider import OpenAIProvider
    from dan.providers import ProviderConfig

    with patch("openai.AsyncOpenAI"):
        return OpenAIProvider(ProviderConfig(api_key="test"))


def _make_google_provider():
    mock_genai = MagicMock()
    with patch.dict("sys.modules", {"google.generativeai": mock_genai}):
        from dan.providers.google_provider import GoogleProvider
        from dan.providers import ProviderConfig

        return GoogleProvider(ProviderConfig(api_key="test"))


def _long_system_text(chars: int = 8192) -> str:
    """Return text that exceeds the 1024-token estimate threshold (chars // 4 > 1024)."""
    return "x" * chars


def _short_system_text() -> str:
    """Return text below the 1024-token threshold."""
    return "short system prompt"


# ---------------------------------------------------------------------------
# 1. Anthropic cache_control hints
# ---------------------------------------------------------------------------


class TestAnthropicCacheHints:
    def test_long_system_message_gets_cache_control(self):
        provider = _make_anthropic_provider()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _long_system_text()},
            {"role": "user", "content": "hello"},
        ]
        result = provider.apply_cache_hints(messages)
        sys_msg = result[0]
        assert sys_msg["cache_control"] == {"type": "ephemeral"}

    def test_short_system_message_not_modified(self):
        provider = _make_anthropic_provider()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _short_system_text()},
            {"role": "user", "content": "hello"},
        ]
        result = provider.apply_cache_hints(messages)
        assert "cache_control" not in result[0]

    def test_user_messages_never_modified(self):
        provider = _make_anthropic_provider()
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": _long_system_text()},
        ]
        result = provider.apply_cache_hints(messages)
        assert "cache_control" not in result[0]

    def test_split_system_returns_blocks_with_cache_control(self):
        from dan.providers.anthropic_provider import AnthropicProvider

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": "cached prompt",
                "cache_control": {"type": "ephemeral"},
            },
            {"role": "user", "content": "hi"},
        ]
        system, non_system = AnthropicProvider._split_system(messages)
        assert isinstance(system, list)
        assert system[0]["type"] == "text"
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert len(non_system) == 1

    def test_split_system_returns_string_without_cache_control(self):
        from dan.providers.anthropic_provider import AnthropicProvider

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "normal prompt"},
            {"role": "user", "content": "hi"},
        ]
        system, _ = AnthropicProvider._split_system(messages)
        assert isinstance(system, str)
        assert system == "normal prompt"

    def test_extract_usage_includes_cache_fields(self):
        from dan.providers.anthropic_provider import AnthropicProvider

        resp = MagicMock()
        resp.usage.input_tokens = 100
        resp.usage.output_tokens = 50
        resp.usage.cache_read_input_tokens = 80
        resp.usage.cache_creation_input_tokens = 20

        usage = AnthropicProvider._extract_usage(resp)
        assert usage is not None
        assert usage["cached_input_tokens"] == 80
        assert usage["cache_write_tokens"] == 20

    def test_extract_cache_tokens(self):
        from dan.providers.anthropic_provider import AnthropicProvider

        resp = MagicMock()
        resp.usage.cache_read_input_tokens = 42
        resp.usage.cache_creation_input_tokens = 7

        cached, written = AnthropicProvider._extract_cache_tokens(resp)
        assert cached == 42
        assert written == 7

    def test_extract_cache_tokens_missing_fields(self):
        from dan.providers.anthropic_provider import AnthropicProvider

        resp = MagicMock()
        resp.usage = None
        assert AnthropicProvider._extract_cache_tokens(resp) == (0, 0)


# ---------------------------------------------------------------------------
# 2. OpenAI stable ordering
# ---------------------------------------------------------------------------


class TestOpenAICacheHints:
    def test_system_messages_moved_to_front(self):
        provider = _make_openai_provider()
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "hello"},
            {"role": "system", "content": "sys1"},
            {"role": "assistant", "content": "ok"},
            {"role": "system", "content": "sys2"},
        ]
        result = provider.apply_cache_hints(messages)
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "system"
        assert result[2]["role"] == "user"
        assert result[3]["role"] == "assistant"

    def test_already_ordered_messages_unchanged(self):
        provider = _make_openai_provider()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        result = provider.apply_cache_hints(messages)
        assert result == messages

    def test_extract_usage_includes_cached_tokens(self):
        from dan.providers.openai_provider import OpenAIProvider

        obj = MagicMock()
        obj.usage.prompt_tokens = 200
        obj.usage.completion_tokens = 50
        obj.usage.total_tokens = 250
        obj.usage.prompt_tokens_details.cached_tokens = 120

        usage = OpenAIProvider._extract_usage(obj)
        assert usage is not None
        assert usage["cached_input_tokens"] == 120

    def test_extract_usage_no_details(self):
        from dan.providers.openai_provider import OpenAIProvider

        obj = MagicMock()
        obj.usage.prompt_tokens = 100
        obj.usage.completion_tokens = 50
        obj.usage.total_tokens = 150
        obj.usage.prompt_tokens_details = None

        usage = OpenAIProvider._extract_usage(obj)
        assert usage is not None
        assert usage["cached_input_tokens"] == 0


# ---------------------------------------------------------------------------
# 3. Google pass-through
# ---------------------------------------------------------------------------


class TestGoogleCacheHints:
    def test_passthrough(self):
        provider = _make_google_provider()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _long_system_text()},
            {"role": "user", "content": "hi"},
        ]
        result = provider.apply_cache_hints(messages)
        assert result is messages
        assert "cache_control" not in result[0]


# ---------------------------------------------------------------------------
# 4. CompletionResult carries cached token fields
# ---------------------------------------------------------------------------


class TestCompletionResult:
    def test_default_cache_fields_zero(self):
        r = CompletionResult(text="hi")
        assert r.cached_input_tokens == 0
        assert r.cache_write_tokens == 0

    def test_explicit_cache_fields(self):
        r = CompletionResult(
            text="hi",
            cached_input_tokens=100,
            cache_write_tokens=20,
        )
        assert r.cached_input_tokens == 100
        assert r.cache_write_tokens == 20


# ---------------------------------------------------------------------------
# 5. CostTracker cache metrics
# ---------------------------------------------------------------------------


class TestCostTrackerCacheMetrics:
    def test_record_accumulates_cache_tokens(self):
        tracker = CostTracker()
        tracker.record(
            "node_a", "gpt-4o",
            {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
            cached_input_tokens=500,
            cache_write_tokens=100,
        )
        tracker.record(
            "node_b", "gpt-4o",
            {"prompt_tokens": 800, "completion_tokens": 100, "total_tokens": 900},
            cached_input_tokens=300,
            cache_write_tokens=0,
        )
        summary = tracker.cache_summary()
        assert summary["cached_tokens"] == 800
        assert summary["cache_write_tokens"] == 100

    def test_cache_hit_rate(self):
        tracker = CostTracker()
        tracker.record(
            "n1", "gpt-4o",
            {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
            cached_input_tokens=600,
        )
        summary = tracker.cache_summary()
        assert summary["cache_hit_rate"] == pytest.approx(0.6)

    def test_cache_hit_rate_zero_when_no_prompts(self):
        tracker = CostTracker()
        summary = tracker.cache_summary()
        assert summary["cache_hit_rate"] == 0.0

    def test_record_backward_compat_no_cache_args(self):
        tracker = CostTracker()
        cost = tracker.record(
            "n1", "gpt-4o",
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )
        assert cost > 0
        summary = tracker.cache_summary()
        assert summary["cached_tokens"] == 0
        assert summary["cache_write_tokens"] == 0

    def test_snapshot_restore_includes_cache(self):
        tracker = CostTracker()
        tracker.record(
            "n1", "gpt-4o",
            {"prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600},
            cached_input_tokens=200,
            cache_write_tokens=50,
        )
        snap = tracker.snapshot()

        tracker2 = CostTracker()
        tracker2.restore(snap)
        summary = tracker2.cache_summary()
        assert summary["cached_tokens"] == 200
        assert summary["cache_write_tokens"] == 50


# ---------------------------------------------------------------------------
# 6. apply_cache_hints fallback for unknown providers
# ---------------------------------------------------------------------------


class TestApplyCacheHintsFallback:
    def test_provider_without_method_returns_messages_unchanged(self):
        class BareProvider:
            pass

        provider = BareProvider()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "hello"},
        ]
        result = apply_cache_hints(provider, messages)
        assert result is messages

    def test_provider_with_method_is_called(self):
        class CachingProvider:
            def apply_cache_hints(self, messages):
                return [{"role": "system", "content": "MODIFIED"}]

        provider = CachingProvider()
        result = apply_cache_hints(provider, [{"role": "user", "content": "hi"}])
        assert result[0]["content"] == "MODIFIED"


# ---------------------------------------------------------------------------
# 7. Threshold boundary tests
# ---------------------------------------------------------------------------


class TestAnthropicThresholdBoundary:
    def test_exactly_at_threshold_not_cached(self):
        """1024 tokens * 4 chars = 4096 chars. Exactly at threshold should NOT cache."""
        provider = _make_anthropic_provider()
        text = "x" * 4096  # 4096 // 4 == 1024, not > 1024
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": text},
        ]
        result = provider.apply_cache_hints(messages)
        assert "cache_control" not in result[0]

    def test_one_char_over_threshold_cached(self):
        """4097 chars → 1024 tokens (int division), still not > 1024."""
        provider = _make_anthropic_provider()
        text = "x" * 4100  # 4100 // 4 == 1025, > 1024
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": text},
        ]
        result = provider.apply_cache_hints(messages)
        assert result[0]["cache_control"] == {"type": "ephemeral"}
