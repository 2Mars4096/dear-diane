"""Tests for text_chunk and pdf_read tools."""

from __future__ import annotations

import pytest

from dan.tools.text_chunk import text_chunk


class TestTextChunk:
    @pytest.mark.asyncio
    async def test_basic_chunking(self):
        text = "abcdefghij" * 10  # 100 chars
        result = await text_chunk(text=text, chunk_size=30, overlap=10)
        assert result["count"] > 1
        for c in result["chunks"]:
            assert len(c["chunk"]) <= 30

    @pytest.mark.asyncio
    async def test_empty_string(self):
        result = await text_chunk(text="")
        assert result["count"] == 0
        assert result["chunks"] == []

    @pytest.mark.asyncio
    async def test_chunk_larger_than_text(self):
        result = await text_chunk(text="short", chunk_size=1000)
        assert result["count"] == 1
        assert result["chunks"][0]["chunk"] == "short"

    @pytest.mark.asyncio
    async def test_overlap_preserved(self):
        text = "0123456789" * 5  # 50 chars
        result = await text_chunk(text=text, chunk_size=20, overlap=5)
        assert result["count"] > 1
        first_end = result["chunks"][0]["end_char"]
        second_start = result["chunks"][1]["start_char"]
        assert second_start < first_end  # overlap exists

    @pytest.mark.asyncio
    async def test_word_chunking(self):
        text = " ".join(f"word{i}" for i in range(20))
        result = await text_chunk(text=text, chunk_size=5, overlap=1, method="words")
        assert result["count"] > 1
        for c in result["chunks"]:
            assert len(c["chunk"].split()) <= 5

    @pytest.mark.asyncio
    async def test_chunk_indices_sequential(self):
        result = await text_chunk(text="a" * 100, chunk_size=10, overlap=0)
        for i, c in enumerate(result["chunks"]):
            assert c["index"] == i


class TestPdfRead:
    @pytest.mark.asyncio
    async def test_skip_if_no_pypdf(self):
        pypdf = pytest.importorskip("pypdf")
        from dan.tools.pdf_read import pdf_read
        # Just verify the import succeeds — actual PDF testing needs a fixture
