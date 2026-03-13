"""Tests for dan.server.concierge.identity — configurable bot name and prefix formatting."""
from __future__ import annotations

import os
from unittest import mock

import pytest

from dan.server.concierge.identity import (
    extract_label_from_prefix,
    format_bare_prefix,
    format_compact_label,
    format_prefix,
    get_bot_name,
    starts_with_prefix,
    strip_prefix,
    _cached_bot_name,
)
import dan.server.concierge.identity as identity_mod


@pytest.fixture(autouse=True)
def _reset_cache():
    """Clear the module-level caches before each test."""
    identity_mod._cached_bot_name = None
    identity_mod._PREFIX_RE = None
    yield
    identity_mod._cached_bot_name = None
    identity_mod._PREFIX_RE = None


class TestGetBotName:
    def test_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DAN_BOT_NAME", None)
            assert get_bot_name() == "DAN"

    def test_env_override(self):
        with mock.patch.dict(os.environ, {"DAN_BOT_NAME": "Jarvis"}):
            assert get_bot_name() == "Jarvis"

    def test_explicit_override_takes_precedence(self):
        with mock.patch.dict(os.environ, {"DAN_BOT_NAME": "Jarvis"}):
            assert get_bot_name("Friday") == "Friday"

    def test_empty_env_falls_back_to_default(self):
        with mock.patch.dict(os.environ, {"DAN_BOT_NAME": "  "}):
            assert get_bot_name() == "DAN"

    def test_caching(self):
        with mock.patch.dict(os.environ, {"DAN_BOT_NAME": "Alpha"}):
            assert get_bot_name() == "Alpha"
        with mock.patch.dict(os.environ, {"DAN_BOT_NAME": "Beta"}):
            assert get_bot_name() == "Alpha"


class TestFormatPrefix:
    def test_project_only(self):
        assert format_prefix("MyProject") == "[DAN - MyProject]"

    def test_project_and_task(self):
        assert format_prefix("MyProject", "TaskA") == "[DAN - MyProject / TaskA]"

    def test_custom_bot_name(self):
        assert format_prefix("Proj", bot_name="Jarvis") == "[Jarvis - Proj]"

    def test_custom_bot_with_task(self):
        assert format_prefix("Proj", "Task", bot_name="Friday") == "[Friday - Proj / Task]"


class TestFormatBarePrefix:
    def test_default(self):
        assert format_bare_prefix() == "[DAN]"

    def test_custom_name(self):
        assert format_bare_prefix(bot_name="Hal") == "[Hal]"


class TestStartsWithPrefix:
    def test_bare_prefix(self):
        assert starts_with_prefix("[DAN] hello")

    def test_scoped_prefix(self):
        assert starts_with_prefix("[DAN - Project] hello")

    def test_scoped_with_task(self):
        assert starts_with_prefix("[DAN - Project / Task] hello")

    def test_no_prefix(self):
        assert not starts_with_prefix("hello world")

    def test_mid_text_not_matched(self):
        assert not starts_with_prefix("Says [DAN] hello")

    def test_custom_bot_name(self):
        with mock.patch.dict(os.environ, {"DAN_BOT_NAME": "Jarvis"}):
            assert starts_with_prefix("[Jarvis - Proj] ok", bot_name="Jarvis")
            assert not starts_with_prefix("[DAN - Proj] ok", bot_name="Jarvis")


class TestStripPrefix:
    def test_bare_prefix(self):
        assert strip_prefix("[DAN] hello") == "hello"

    def test_scoped_prefix(self):
        assert strip_prefix("[DAN - Project] hello") == "hello"

    def test_scoped_with_task(self):
        assert strip_prefix("[DAN - Project / Task] hello") == "hello"

    def test_plain_text(self):
        assert strip_prefix("hello") == "hello"

    def test_double_prefix(self):
        assert strip_prefix("[DAN] [DAN] hello") == "hello"

    def test_mixed_prefix(self):
        assert strip_prefix("[DAN - Proj] [DAN] hello") == "hello"

    def test_no_space_after_bracket_preserved(self):
        assert strip_prefix("[DAN]text without space") == "[DAN]text without space"

    def test_incomplete_scoped_prefix(self):
        assert strip_prefix("[DAN - incomplete text") == "incomplete text"

    def test_custom_bot_name(self):
        assert strip_prefix("[Jarvis] hello", bot_name="Jarvis") == "hello"
        assert strip_prefix("[Jarvis - Proj] ok", bot_name="Jarvis") == "ok"


class TestExtractLabelFromPrefix:
    def test_scoped_prefix(self):
        label, rest = extract_label_from_prefix("[DAN - MyProject] hello")
        assert label == "MyProject"
        assert rest == "hello"

    def test_scoped_with_task(self):
        label, rest = extract_label_from_prefix("[DAN - Proj / Task] hello")
        assert label == "Proj / Task"
        assert rest == "hello"

    def test_bare_prefix_returns_empty_label(self):
        label, rest = extract_label_from_prefix("[DAN] hello")
        assert label == ""
        assert rest == "hello"

    def test_no_prefix_returns_empty_label(self):
        label, rest = extract_label_from_prefix("no prefix here")
        assert label == ""
        assert rest == "no prefix here"

    def test_custom_bot_name(self):
        label, rest = extract_label_from_prefix("[Jarvis - Proj] ok", bot_name="Jarvis")
        assert label == "Proj"
        assert rest == "ok"

    def test_unclosed_scoped_prefix(self):
        label, rest = extract_label_from_prefix("[DAN - incomplete text")
        assert label == ""
        assert rest == "incomplete text"

    def test_nested_bracket_in_label(self):
        label, rest = extract_label_from_prefix("[DAN - My [Project]] hello")
        assert label == "My [Project"
        assert "] hello" in rest or "hello" in rest

    def test_empty_label(self):
        label, rest = extract_label_from_prefix("[DAN - ] hello")
        assert label == ""
        assert rest == "hello"

    def test_whitespace_in_label_trimmed(self):
        label, rest = extract_label_from_prefix("[DAN -  Spaced  ] ok")
        assert label == "Spaced"
        assert rest == "ok"


class TestFormatCompactLabel:
    def test_project_only(self):
        assert format_compact_label("Research") == "[Research]"

    def test_project_and_task(self):
        assert format_compact_label("Research", "Outline") == "[Research / Outline]"


class TestAdapterIntegrationWithCustomName:
    """Verify the adapter's _strip_dan_prefix works with a custom bot name."""

    def test_adapter_strip_uses_identity(self):
        with mock.patch.dict(os.environ, {"DAN_BOT_NAME": "Friday"}):
            identity_mod._cached_bot_name = None
            from dan.cli.adapter import _strip_dan_prefix
            assert _strip_dan_prefix("[Friday] hello there") == "hello there"
            assert _strip_dan_prefix("[Friday - MyProject] result text") == "result text"
            assert _strip_dan_prefix("plain text") == "plain text"

    def test_env_override_available_for_adapter(self):
        with mock.patch.dict(os.environ, {"DAN_BOT_NAME": "Hal"}):
            identity_mod._cached_bot_name = None
            from dan.server.concierge.identity import get_bot_name
            assert get_bot_name() == "Hal"
