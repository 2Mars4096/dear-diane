"""Heuristic memory extraction from user/assistant interactions.

Extracts FACT, PREFERENCE, and EPISODE candidates from conversation turns
using regex patterns. Complements the existing PreferenceExtractor (which
handles model/domain/format preferences) by covering factual statements,
behavioral preferences, and richer episode summaries.

Part of Phase 29-6 (self-evolvement loop), tasks 1-1 through 1-7.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Extracted memory item
# ---------------------------------------------------------------------------


class ExtractedMemory:
    """A candidate memory item produced by heuristic extraction."""

    __slots__ = ("memory_type", "content", "tags", "metadata")

    def __init__(
        self,
        memory_type: str,
        content: str,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.memory_type = memory_type  # "fact", "preference", "episode"
        self.content = content
        self.tags = tags or []
        self.metadata = metadata or {}

    def __repr__(self) -> str:
        return f"ExtractedMemory({self.memory_type!r}, {self.content!r})"


# ---------------------------------------------------------------------------
# Pattern tables
# ---------------------------------------------------------------------------

_FACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # file_location MUST precede data_format — the broad (.+) in data_format
    # would otherwise swallow file paths like "~/data/trades.csv".
    (
        re.compile(
            r"(?:file|path|pdf|paper|document)\s+(?:is\s+)?(?:at\s+|in\s+)"
            r"((?:~/|/|\./).+?)(?:\s|,|$)",
            re.IGNORECASE,
        ),
        "file_location",
    ),
    (
        re.compile(
            r"(?:the |my )?(?:data|file|dataset|csv|pdf|paper)(?:\s+file)?"
            r"\s+(?:is|are)\s+(?:in |at |a )?(.+)",
            re.IGNORECASE,
        ),
        "data_format",
    ),
    (
        re.compile(
            r"(?:delimiter|separator)\s+(?:is|should be)\s+(.+)",
            re.IGNORECASE,
        ),
        "data_format",
    ),
    (
        re.compile(
            r"(?:column|field|variable)s?\s+(?:are|is|named?)\s+(.+)",
            re.IGNORECASE,
        ),
        "data_schema",
    ),
    (
        re.compile(
            r"(?:project|task|work)\s+(?:is about|concerns|deals with)\s+(.+)",
            re.IGNORECASE,
        ),
        "project_context",
    ),
    (
        re.compile(
            r"(?:deadline|due date)\s+(?:is|by)\s+(.+)",
            re.IGNORECASE,
        ),
        "timeline",
    ),
]

_STANDALONE_PATH_RE = re.compile(
    r"(?:^|\s)((?:~/|/Users/|/home/|/tmp/|\./)[\w/.~_-]+)",
)

_PREF_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?:I |i )?prefer\s+(?:to use\s+|using\s+)?(.+)",
            re.IGNORECASE,
        ),
        "explicit_preference",
    ),
    (
        re.compile(
            r"(?:always |please )?use\s+(.+?)\s+(?:for|instead|when)",
            re.IGNORECASE,
        ),
        "tool_preference",
    ),
    (
        re.compile(r"don'?t\s+(?:ask|prompt|confirm|check)", re.IGNORECASE),
        "behavioral",
    ),
    (
        re.compile(
            r"format\s+(?:as|in|using)\s+(markdown|latex|json|csv|table)",
            re.IGNORECASE,
        ),
        "output_format",
    ),
]


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class MemoryExtractor:
    """Extracts memory candidates from user/assistant interactions using heuristics.

    Designed to be called after every concierge turn. Returns a list of
    ``ExtractedMemory`` objects that the caller can store via the memory
    kernel's ``store_fact`` / ``store_preference`` / ``store_episode``
    convenience methods.
    """

    _MAX_EPISODE_LEN = 300

    def extract(
        self,
        user_message: str,
        assistant_message: str,
        tool_calls: list[dict[str, Any]] | None = None,
        goal_context: dict[str, Any] | None = None,
    ) -> list[ExtractedMemory]:
        candidates: list[ExtractedMemory | None] = []
        candidates.extend(self._extract_facts(user_message))
        candidates.extend(self._extract_preferences(user_message))
        episode = self._extract_episode(
            user_message, assistant_message, tool_calls,
        )
        if episode is not None:
            candidates.append(episode)
        return [c for c in candidates if c is not None]

    # -- LLM-based extraction -----------------------------------------------

    async def extract_with_llm(
        self,
        user_message: str,
        assistant_message: str,
        *,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> list[ExtractedMemory]:
        """Enhanced extraction using an LLM for nuanced fact/preference/episode detection.

        Falls back to heuristic extraction if LLM is unavailable or disabled.
        Gated by ``DAN_MEMORY_EXTRACTION_LLM=1`` env var (default ``0``).
        """
        if os.environ.get("DAN_MEMORY_EXTRACTION_LLM", "0") != "1":
            return self.extract(user_message, assistant_message)

        try:
            from openai import AsyncOpenAI
        except ImportError:
            logger.debug("openai package not installed; falling back to heuristic extraction")
            return self.extract(user_message, assistant_message)

        api_key = (
            api_key
            or os.environ.get("DAN_OPENAI_API_KEY")
            or os.environ.get("DAN_LLM_API_KEY")
        )
        base_url = (
            base_url
            or os.environ.get("DAN_OPENAI_BASE_URL")
            or os.environ.get("DAN_LLM_BASE_URL", "https://api.openai.com/v1")
        )

        if not api_key:
            logger.debug("No API key for LLM extraction; falling back to heuristic")
            return self.extract(user_message, assistant_message)

        try:
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            prompt = (
                "Extract structured memory items from this conversation turn.\n\n"
                f"User: {user_message[:500]}\n"
                f"Assistant: {assistant_message[:500]}\n\n"
                "Return a JSON array of items, each with:\n"
                '- "type": "fact" | "preference" | "episode"\n'
                '- "content": brief description\n'
                '- "tags": list of relevant tags\n\n'
                "Only include items worth remembering long-term. Return [] if nothing notable."
            )

            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.1,
            )

            raw = (response.choices[0].message.content or "").strip()
            return self._parse_llm_response(raw)
        except Exception:
            logger.debug("LLM extraction failed; falling back to heuristic", exc_info=True)
            return self.extract(user_message, assistant_message)

    @staticmethod
    def _parse_llm_response(raw: str) -> list[ExtractedMemory]:
        """Parse the JSON array returned by the LLM into ExtractedMemory objects."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            items = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return []

        if not isinstance(items, list):
            return []

        results: list[ExtractedMemory] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            mem_type = item.get("type", "")
            content = item.get("content", "")
            tags = item.get("tags", [])
            if mem_type not in ("fact", "preference", "episode") or not content:
                continue
            if not isinstance(tags, list):
                tags = []
            tags = [str(t) for t in tags if t]
            results.append(ExtractedMemory(
                memory_type=mem_type,
                content=str(content),
                tags=tags,
                metadata={"source": "llm"},
            ))
        return results

    # -- Fact extraction ----------------------------------------------------

    def _extract_facts(self, user_message: str) -> list[ExtractedMemory]:
        if not user_message or not user_message.strip():
            return []
        facts: list[ExtractedMemory] = []
        seen_contents: set[str] = set()
        for pattern, tag in _FACT_PATTERNS:
            m = pattern.search(user_message)
            if m:
                content = m.group(1).strip().rstrip(".,;")
                if content and content not in seen_contents:
                    seen_contents.add(content)
                    facts.append(
                        ExtractedMemory(
                            memory_type="fact",
                            content=content,
                            tags=[tag],
                            metadata={"source_pattern": tag},
                        )
                    )
        for m in _STANDALONE_PATH_RE.finditer(user_message):
            path = m.group(1).strip()
            if path and path not in seen_contents:
                already_captured = any(
                    path in f.content for f in facts if "file_location" in f.tags
                )
                if not already_captured:
                    seen_contents.add(path)
                    facts.append(
                        ExtractedMemory(
                            memory_type="fact",
                            content=f"file path: {path}",
                            tags=["file_location"],
                            metadata={"source_pattern": "standalone_path"},
                        )
                    )
        return facts

    # -- Preference extraction ----------------------------------------------

    def _extract_preferences(self, user_message: str) -> list[ExtractedMemory]:
        if not user_message or not user_message.strip():
            return []
        prefs: list[ExtractedMemory] = []
        seen: set[str] = set()
        for pattern, tag in _PREF_PATTERNS:
            m = pattern.search(user_message)
            if m:
                if tag == "behavioral":
                    content = m.group(0).strip()
                else:
                    content = m.group(1).strip().rstrip(".,;")
                if content and content not in seen:
                    seen.add(content)
                    prefs.append(
                        ExtractedMemory(
                            memory_type="preference",
                            content=content,
                            tags=[tag],
                            metadata={"source_pattern": tag},
                        )
                    )
        return prefs

    # -- Episode extraction -------------------------------------------------

    def _extract_episode(
        self,
        user_message: str,
        assistant_message: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> ExtractedMemory | None:
        if not user_message or not user_message.strip():
            return None

        user_summary = " ".join(user_message.split())[:120]

        tools_used: list[str] = []
        if tool_calls:
            for tc in tool_calls:
                name = tc.get("name") or tc.get("tool") or ""
                if name and name not in tools_used:
                    tools_used.append(name)
        tools_str = ", ".join(tools_used[:5]) if tools_used else "none"

        outcome = " ".join((assistant_message or "").split())[:80]

        episode = (
            f"User asked: {user_summary}. "
            f"Tools: {tools_str}. "
            f"Outcome: {outcome}"
        )
        episode = episode[: self._MAX_EPISODE_LEN]

        tags = ["episode"]
        if tools_used:
            tags.extend(tools_used[:3])

        return ExtractedMemory(
            memory_type="episode",
            content=episode,
            tags=tags,
            metadata={"tools_used": tools_used},
        )
