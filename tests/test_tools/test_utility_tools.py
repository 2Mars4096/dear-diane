"""Tests for json_extract and regex_match tools."""

from __future__ import annotations

import pytest

from dan.tools.json_extract import json_extract
from dan.tools.regex_match import regex_match


# ── json_extract ───────────────────────────────────────────────────


class TestJsonExtract:
    @pytest.mark.asyncio
    async def test_nested_dict(self):
        data = {"user": {"address": {"city": "NYC"}}}
        result = await json_extract(data=data, path="user.address.city")
        assert result["found"] is True
        assert result["value"] == "NYC"

    @pytest.mark.asyncio
    async def test_string_input(self):
        result = await json_extract(data='{"a": 1}', path="a")
        assert result["found"] is True
        assert result["value"] == 1

    @pytest.mark.asyncio
    async def test_array_index(self):
        data = {"items": ["x", "y", "z"]}
        result = await json_extract(data=data, path="items.1")
        assert result["found"] is True
        assert result["value"] == "y"

    @pytest.mark.asyncio
    async def test_missing_key(self):
        result = await json_extract(data={"a": 1}, path="b")
        assert result["found"] is False
        assert result["value"] is None

    @pytest.mark.asyncio
    async def test_deep_missing_key(self):
        result = await json_extract(data={"a": {"b": 1}}, path="a.c.d")
        assert result["found"] is False

    @pytest.mark.asyncio
    async def test_invalid_json_string(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            await json_extract(data="not json", path="a")

    @pytest.mark.asyncio
    async def test_index_out_of_range(self):
        result = await json_extract(data={"a": [1]}, path="a.5")
        assert result["found"] is False


# ── regex_match ────────────────────────────────────────────────────


class TestRegexMatch:
    @pytest.mark.asyncio
    async def test_basic_match(self):
        result = await regex_match(text="Hello World 123", pattern=r"\d+")
        assert result["count"] == 1
        assert result["matches"][0]["match"] == "123"

    @pytest.mark.asyncio
    async def test_multiple_matches(self):
        result = await regex_match(text="cat bat hat", pattern=r"\b\w+at\b")
        assert result["count"] == 3

    @pytest.mark.asyncio
    async def test_capture_groups(self):
        result = await regex_match(text="2024-01-15", pattern=r"(\d{4})-(\d{2})-(\d{2})")
        m = result["matches"][0]
        assert m["groups"] == ["2024", "01", "15"]

    @pytest.mark.asyncio
    async def test_replacement(self):
        result = await regex_match(
            text="foo bar baz",
            pattern=r"\b(\w+)\b",
            replacement=r"[\1]",
        )
        assert result["result"] == "[foo] [bar] [baz]"
        assert result["count"] == 3

    @pytest.mark.asyncio
    async def test_no_match(self):
        result = await regex_match(text="hello", pattern=r"\d+")
        assert result["count"] == 0
        assert result["matches"] == []

    @pytest.mark.asyncio
    async def test_invalid_pattern(self):
        with pytest.raises(ValueError, match="Invalid regex"):
            await regex_match(text="test", pattern=r"[invalid")

    @pytest.mark.asyncio
    async def test_match_positions(self):
        result = await regex_match(text="abc123def", pattern=r"\d+")
        m = result["matches"][0]
        assert m["start"] == 3
        assert m["end"] == 6
