from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dan.providers import ProviderConfig
from dan.providers.multimodal import content_with_image_attachments, normalize_openai_messages_for_multimodal
from dan.providers.openai_provider import OpenAIProvider


def _make_provider(base_url: str | None = None) -> OpenAIProvider:
    provider = object.__new__(OpenAIProvider)
    provider._base_url = base_url
    provider._client = SimpleNamespace(base_url=base_url)
    provider._timeout_seconds = 30
    provider._request_timeout = 30
    return provider


@pytest.mark.parametrize("model", ["kimi-k2.5", "kimi-k2.6"])
def test_apply_compatibility_defaults_disables_thinking_for_kimi_low_budget_text(
    model: str,
) -> None:
    provider = _make_provider("https://api.moonshot.ai/v1")
    call_kwargs = {
        "max_tokens": 3000,
        "temperature": 1.0,
    }

    result = provider._apply_compatibility_defaults(model, dict(call_kwargs))

    assert result["thinking"] == {"type": "disabled"}
    assert result["temperature"] == 0.6


def test_apply_compatibility_defaults_disables_thinking_for_moonshot_exact_tool_request() -> None:
    provider = _make_provider("https://api.moonshot.ai/v1")
    call_kwargs = {
        "tools": [{"type": "function", "function": {"name": "plan_graph_mutations"}}],
        "tool_choice": {"type": "function", "function": {"name": "plan_graph_mutations"}},
        "temperature": 0.7,
        "max_tokens": 32000,
    }

    result = provider._apply_compatibility_defaults("custom-tool-model", dict(call_kwargs))

    assert result["thinking"] == {"type": "disabled"}
    assert result["temperature"] == 0.6


def test_apply_compatibility_defaults_keeps_auto_tool_choice_unchanged() -> None:
    provider = _make_provider("https://api.moonshot.ai/v1")
    call_kwargs = {
        "tools": [{"type": "function", "function": {"name": "plan_graph_mutations"}}],
        "tool_choice": "auto",
        "temperature": 0.7,
        "max_tokens": 32000,
    }

    result = provider._apply_compatibility_defaults("custom-tool-model", dict(call_kwargs))

    assert "thinking" not in result
    assert result["temperature"] == 0.7


def test_openai_provider_disables_sdk_retries() -> None:
    with patch("dan.providers.openai_provider.AsyncOpenAI") as mock_cls:
        OpenAIProvider(ProviderConfig(api_key="test-key", base_url="https://example.com/v1"))

    _, kwargs = mock_cls.call_args
    assert kwargs["max_retries"] == 0


def test_openai_multimodal_helper_builds_image_blocks(tmp_path) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    content = content_with_image_attachments(
        "What is wrong with this UI?",
        [{"kind": "image", "local_path": str(image)}],
    )

    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "Attached images:" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_openai_message_normalizer_accepts_short_image_url_alias() -> None:
    messages = normalize_openai_messages_for_multimodal(
        [{"role": "user", "content": [{"type": "image_url", "url": "data:image/png;base64,abc"}]}]
    )

    assert messages[0]["content"][0] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,abc"},
    }
