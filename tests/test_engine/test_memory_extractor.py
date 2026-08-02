"""Unit tests for MemoryExtractor (29-6 §12-1).

Covers fact, preference, and episode extraction via heuristic patterns.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.engine.memory_extractor import ExtractedMemory, MemoryExtractor


@pytest.fixture
def extractor() -> MemoryExtractor:
    return MemoryExtractor()


# ---------------------------------------------------------------------------
# Fact extraction
# ---------------------------------------------------------------------------


class TestFactExtraction:
    def test_file_location_at(self, extractor: MemoryExtractor):
        items = extractor.extract("file is at ~/data/trades.csv", "OK")
        facts = [i for i in items if i.memory_type == "fact"]
        assert any("~/data/trades.csv" in f.content for f in facts)

    def test_file_location_in(self, extractor: MemoryExtractor):
        items = extractor.extract("pdf is in /tmp/report.pdf", "Got it")
        facts = [i for i in items if i.memory_type == "fact"]
        assert any("/tmp/report.pdf" in f.content for f in facts)

    def test_data_format(self, extractor: MemoryExtractor):
        items = extractor.extract("the data is CSV", "OK")
        facts = [i for i in items if i.memory_type == "fact"]
        assert any("CSV" in f.content for f in facts)

    def test_delimiter(self, extractor: MemoryExtractor):
        items = extractor.extract("delimiter is tab", "noted")
        facts = [i for i in items if i.memory_type == "fact"]
        assert any("tab" in f.content for f in facts)

    def test_column_names(self, extractor: MemoryExtractor):
        items = extractor.extract("columns are price, volume, date", "OK")
        facts = [i for i in items if i.memory_type == "fact"]
        assert any("price" in f.content for f in facts)

    def test_project_context(self, extractor: MemoryExtractor):
        items = extractor.extract("project is about supply chain optimization", "OK")
        facts = [i for i in items if i.memory_type == "fact"]
        assert any("supply chain" in f.content for f in facts)

    def test_deadline(self, extractor: MemoryExtractor):
        items = extractor.extract("deadline is Friday", "OK")
        facts = [i for i in items if i.memory_type == "fact"]
        assert any("Friday" in f.content for f in facts)

    def test_standalone_path(self, extractor: MemoryExtractor):
        items = extractor.extract("check /Users/me/data.json please", "OK")
        facts = [i for i in items if i.memory_type == "fact"]
        assert any("/Users/me/data.json" in f.content for f in facts)

    def test_directory_paths_become_search_dir_facts(self, extractor: MemoryExtractor):
        items = extractor.extract(
            (
                "I save my papers under "
                "~/Dropbox/Projects/my-knowledge-base/static/papers/ "
                "and notes in "
                "~/Dropbox/Projects/my-knowledge-base/content/papers"
            ),
            "Got it.",
        )
        facts = [i for i in items if i.memory_type == "fact"]
        assert any(
            f.content.startswith("papers directory:")
            and "search_dir" in f.tags
            and f.metadata.get("is_directory") is True
            for f in facts
        )
        assert any(
            f.content.startswith("notes directory:")
            and "search_dir" in f.tags
            for f in facts
        )

    def test_empty_input_returns_no_facts(self, extractor: MemoryExtractor):
        items = extractor.extract("", "OK")
        assert items == []

    def test_no_duplicate_facts(self, extractor: MemoryExtractor):
        items = extractor.extract("file is at ~/data/trades.csv", "OK")
        facts = [i for i in items if i.memory_type == "fact"]
        contents = [f.content for f in facts]
        assert len(contents) == len(set(contents))


# ---------------------------------------------------------------------------
# Preference extraction
# ---------------------------------------------------------------------------


class TestPreferenceExtraction:
    def test_explicit_preference(self, extractor: MemoryExtractor):
        items = extractor.extract("I prefer Claude for writing tasks", "OK, noted.")
        prefs = [i for i in items if i.memory_type == "preference"]
        assert len(prefs) >= 1
        assert any("Claude" in p.content for p in prefs)

    def test_tool_preference(self, extractor: MemoryExtractor):
        items = extractor.extract("use pandas for data analysis", "OK")
        prefs = [i for i in items if i.memory_type == "preference"]
        assert any("pandas" in p.content for p in prefs)

    def test_behavioral_preference(self, extractor: MemoryExtractor):
        items = extractor.extract("don't ask me to confirm every step", "OK")
        prefs = [i for i in items if i.memory_type == "preference"]
        assert len(prefs) >= 1
        assert any("behavioral" in p.tags for p in prefs)

    def test_output_format(self, extractor: MemoryExtractor):
        items = extractor.extract("format as markdown", "Done")
        prefs = [i for i in items if i.memory_type == "preference"]
        assert any("markdown" in p.content.lower() for p in prefs)

    def test_no_preference_from_unrelated(self, extractor: MemoryExtractor):
        items = extractor.extract("What is the weather today?", "Sunny.")
        prefs = [i for i in items if i.memory_type == "preference"]
        assert len(prefs) == 0


# ---------------------------------------------------------------------------
# Episode extraction
# ---------------------------------------------------------------------------


class TestEpisodeExtraction:
    def test_basic_episode(self, extractor: MemoryExtractor):
        items = extractor.extract("build a pipeline", "Done, created pipeline.")
        episodes = [i for i in items if i.memory_type == "episode"]
        assert len(episodes) == 1
        assert "build a pipeline" in episodes[0].content

    def test_episode_with_tools(self, extractor: MemoryExtractor):
        items = extractor.extract(
            "analyze my data",
            "Analysis complete.",
            tool_calls=[{"name": "python_eval"}, {"name": "pdf_read"}],
        )
        episodes = [i for i in items if i.memory_type == "episode"]
        assert len(episodes) == 1
        assert "python_eval" in episodes[0].content
        assert "python_eval" in episodes[0].metadata.get("tools_used", [])

    def test_episode_truncation(self, extractor: MemoryExtractor):
        long_msg = "x" * 500
        items = extractor.extract(long_msg, "OK")
        episodes = [i for i in items if i.memory_type == "episode"]
        assert len(episodes) == 1
        assert len(episodes[0].content) <= MemoryExtractor._MAX_EPISODE_LEN

    def test_empty_user_message_no_episode(self, extractor: MemoryExtractor):
        items = extractor.extract("", "hello")
        assert len(items) == 0

    def test_whitespace_only_no_episode(self, extractor: MemoryExtractor):
        items = extractor.extract("   ", "hello")
        assert len(items) == 0


# ---------------------------------------------------------------------------
# Combined extraction
# ---------------------------------------------------------------------------


class TestCombinedExtraction:
    def test_message_with_fact_preference_and_episode(self, extractor: MemoryExtractor):
        items = extractor.extract(
            "I prefer Claude and my data file is at ~/data.csv",
            "Noted both.",
        )
        types = {i.memory_type for i in items}
        assert "preference" in types
        assert "fact" in types
        assert "episode" in types

    def test_extracted_memory_repr(self):
        m = ExtractedMemory("fact", "test content", tags=["t"])
        assert "fact" in repr(m)
        assert "test content" in repr(m)


# ---------------------------------------------------------------------------
# LLM-based extraction (29-6 task 1-5)
# ---------------------------------------------------------------------------


class TestLLMExtraction:
    def test_llm_extraction_gated_by_env_var(self, extractor: MemoryExtractor, monkeypatch):
        """When DAN_MEMORY_EXTRACTION_LLM=0, extract_with_llm uses heuristic."""
        monkeypatch.setenv("DAN_MEMORY_EXTRACTION_LLM", "0")
        import asyncio

        results = asyncio.run(extractor.extract_with_llm(
            user_message="the data is in CSV format",
            assistant_message="Got it.",
        ))
        facts = [r for r in results if r.memory_type == "fact"]
        assert len(facts) >= 1
        assert all(r.metadata.get("source") != "llm" for r in results)

    def test_llm_extraction_fallback_to_heuristic(self, extractor: MemoryExtractor, monkeypatch):
        """When LLM env is on but no API key, falls back to heuristic."""
        monkeypatch.setenv("DAN_MEMORY_EXTRACTION_LLM", "1")
        monkeypatch.delenv("DAN_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("DAN_LLM_API_KEY", raising=False)
        import asyncio

        results = asyncio.run(extractor.extract_with_llm(
            user_message="I prefer Claude for writing",
            assistant_message="Noted.",
        ))
        prefs = [r for r in results if r.memory_type == "preference"]
        assert len(prefs) >= 1
        assert all(r.metadata.get("source") != "llm" for r in results)

    def test_llm_extraction_default_env_off(self, extractor: MemoryExtractor, monkeypatch):
        """Default (no env var set) should use heuristic."""
        monkeypatch.delenv("DAN_MEMORY_EXTRACTION_LLM", raising=False)

        results = asyncio.run(extractor.extract_with_llm(
            user_message="deadline is Friday",
            assistant_message="OK.",
        ))
        facts = [r for r in results if r.memory_type == "fact"]
        assert len(facts) >= 1

    def test_llm_extraction_model_name_env_enables_llm(self, extractor: MemoryExtractor, monkeypatch):
        """A concrete model name should enable LLM extraction, not disable it."""
        monkeypatch.setenv("DAN_MEMORY_EXTRACTION_LLM", "claude-sonnet-4-6")
        monkeypatch.setenv("DAN_LLM_API_KEY", "test-key")
        monkeypatch.setenv("DAN_LLM_BASE_URL", "https://example.invalid/v1")

        response = MagicMock()
        response.choices = [
            MagicMock(
                message=MagicMock(
                    content='[{"type":"preference","content":"prefers bullet points","tags":["style"]}]'
                )
            )
        ]
        create = AsyncMock(return_value=response)

        with patch("openai.AsyncOpenAI") as mock_client:
            mock_client.return_value.chat.completions.create = create
            results = asyncio.run(extractor.extract_with_llm(
                user_message="Please remember I prefer bullet points",
                assistant_message="Got it.",
            ))

        create.assert_awaited_once()
        assert create.await_args.kwargs["model"] == "claude-sonnet-4-6"
        assert any(r.metadata.get("source") == "llm" for r in results)
        assert any(r.memory_type == "episode" for r in results)

    def test_llm_extraction_keeps_heuristic_directory_facts(
        self,
        extractor: MemoryExtractor,
        monkeypatch,
    ):
        monkeypatch.setenv("DAN_MEMORY_EXTRACTION_LLM", "claude-sonnet-4-6")
        monkeypatch.setenv("DAN_LLM_API_KEY", "test-key")
        monkeypatch.setenv("DAN_LLM_BASE_URL", "https://example.invalid/v1")

        response = MagicMock()
        response.choices = [
            MagicMock(message=MagicMock(content='[{"type":"episode","content":"saved directories","tags":["memory"]}]'))
        ]
        create = AsyncMock(return_value=response)

        with patch("openai.AsyncOpenAI") as mock_client:
            mock_client.return_value.chat.completions.create = create
            results = asyncio.run(extractor.extract_with_llm(
                user_message=(
                    "I save my papers under "
                    "~/Dropbox/Projects/my-knowledge-base/static/papers/ "
                    "and notes in "
                    "~/Dropbox/Projects/my-knowledge-base/content/papers"
                ),
                assistant_message="Understood.",
            ))

        facts = [r for r in results if r.memory_type == "fact"]
        assert any("papers directory:" in fact.content for fact in facts)
        assert any("notes directory:" in fact.content for fact in facts)

    def test_parse_llm_response_valid_json(self):
        raw = '[{"type": "fact", "content": "project uses Python 3.12", "tags": ["tech"]}]'
        results = MemoryExtractor._parse_llm_response(raw)
        assert len(results) == 1
        assert results[0].memory_type == "fact"
        assert results[0].content == "project uses Python 3.12"
        assert "tech" in results[0].tags
        assert results[0].metadata["source"] == "llm"

    def test_parse_llm_response_empty_array(self):
        assert MemoryExtractor._parse_llm_response("[]") == []

    def test_parse_llm_response_invalid_json(self):
        assert MemoryExtractor._parse_llm_response("not json") == []

    def test_parse_llm_response_markdown_fenced(self):
        raw = '```json\n[{"type": "preference", "content": "prefers latex", "tags": ["format"]}]\n```'
        results = MemoryExtractor._parse_llm_response(raw)
        assert len(results) == 1
        assert results[0].memory_type == "preference"

    def test_parse_llm_response_skips_invalid_types(self):
        raw = '[{"type": "unknown", "content": "x", "tags": []}, {"type": "fact", "content": "y", "tags": []}]'
        results = MemoryExtractor._parse_llm_response(raw)
        assert len(results) == 1
        assert results[0].memory_type == "fact"

    def test_parse_llm_response_skips_empty_content(self):
        raw = '[{"type": "fact", "content": "", "tags": []}]'
        results = MemoryExtractor._parse_llm_response(raw)
        assert len(results) == 0

    def test_parse_llm_response_multiple_items(self):
        raw = (
            '[{"type": "fact", "content": "uses CSV", "tags": ["data"]},'
            ' {"type": "preference", "content": "prefers Claude", "tags": ["model"]},'
            ' {"type": "episode", "content": "user asked about data", "tags": []}]'
        )
        results = MemoryExtractor._parse_llm_response(raw)
        assert len(results) == 3
        types = {r.memory_type for r in results}
        assert types == {"fact", "preference", "episode"}
