"""Tests for GoogleProvider — message format, ImportError handling."""

from __future__ import annotations

import sys

import pytest

from dan.providers import ProviderConfig


class TestGeminiMessageFormat:
    def test_to_gemini_messages_basic(self):
        from dan.providers.google_provider import GoogleProvider

        messages = [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "bye"},
        ]
        system_text, history = GoogleProvider._to_gemini_messages(messages)
        assert system_text == "Be helpful"
        assert len(history) == 3
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "model"
        assert history[2]["role"] == "user"

    def test_to_gemini_messages_no_system(self):
        from dan.providers.google_provider import GoogleProvider

        messages = [{"role": "user", "content": "hi"}]
        system_text, history = GoogleProvider._to_gemini_messages(messages)
        assert system_text is None
        assert len(history) == 1

    def test_to_gemini_messages_translates_openai_image_block(self):
        from dan.providers.google_provider import GoogleProvider

        _system_text, history = GoogleProvider._to_gemini_messages(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "review this"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                    ],
                }
            ]
        )

        assert history[0]["parts"] == [
            {"text": "review this"},
            {"inline_data": {"mime_type": "image/png", "data": "abc"}},
        ]


def test_import_error_message():
    """Verify clear error when google-generativeai is not installed."""
    saved = sys.modules.get("google.generativeai")
    saved_google = sys.modules.get("google")
    sys.modules["google.generativeai"] = None  # type: ignore

    try:
        from dan.providers.google_provider import GoogleProvider
        with pytest.raises(ImportError, match="google-generativeai"):
            GoogleProvider(ProviderConfig(api_key="test"))
    finally:
        if saved is not None:
            sys.modules["google.generativeai"] = saved
        else:
            sys.modules.pop("google.generativeai", None)
        if saved_google is not None:
            sys.modules["google"] = saved_google
