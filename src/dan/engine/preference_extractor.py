from __future__ import annotations

import re
from typing import Any

from dan.engine.domain_taxonomy import normalize_domain_keyword_map

_MODEL_PATTERNS = {
    "claude": re.compile(r"\bclaude[-\s]?(opus|sonnet|haiku|3\.5|3|4)[\w.-]*\b", re.IGNORECASE),
    "gpt": re.compile(r"\bgpt[-\s]?(4|4o|3\.5|o1|o3|o4)[\w.-]*\b", re.IGNORECASE),
    "gemini": re.compile(r"\bgemini[-\s]?(pro|ultra|flash|1\.5|2)[\w.-]*\b", re.IGNORECASE),
}

_TASK_KEYWORDS = {
    "drafting": ["draft", "write", "compose", "author"],
    "review": ["review", "critique", "feedback", "evaluate"],
    "coding": ["code", "implement", "program", "script", "debug"],
    "analysis": ["analyze", "research", "investigate", "study"],
    "summarization": ["summarize", "summary", "condense", "tldr"],
}

# Shared domain maps can contain very generic task words. Preference extraction is
# intentionally more conservative so one-off mentions like "paper" or "workflow"
# do not become durable profile preferences.
_LOW_SIGNAL_DOMAIN_KEYWORDS = {
    "abstract",
    "api",
    "appendix",
    "class",
    "code",
    "dataset",
    "edge",
    "figure",
    "function",
    "graph",
    "module",
    "node",
    "paper",
    "pipeline",
    "review",
    "table",
    "test",
    "workflow",
}

_FORMAT_PATTERNS = {
    "latex": re.compile(r"\b(latex|tex)\b", re.IGNORECASE),
    "markdown": re.compile(r"\bmarkdown\b", re.IGNORECASE),
    "json": re.compile(r"\bjson\b", re.IGNORECASE),
}

_ABSOLUTE_PATH_RE = re.compile(r"(?:~\/|\/|\.\/|\.\.\/)[^\s,;]+")
_RELATIVE_PATH_RE = re.compile(r"\b[\w.-]+(?:/[\w.-]+){1,}\b")
_FILENAME_RE = re.compile(r"\b[\w.-]+\.(?:md|markdown|tex|pdf|json|csv)\b", re.IGNORECASE)


class PreferenceExtractor:
    """Extract user preferences from conversation history using heuristics."""

    def __init__(
        self,
        *,
        domain_keywords: dict[str, list[str]] | None = None,
        behavior_store: Any | None = None,
    ) -> None:
        self._domain_keywords = (
            normalize_domain_keyword_map(domain_keywords)
            if domain_keywords is not None
            else None
        )
        self._behavior_store = behavior_store

    def extract_from_messages(
        self, messages: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Scan messages for implicit preferences. Returns preference deltas."""
        user_messages = [m["content"] for m in messages if m.get("role") == "user"]
        text = " ".join(user_messages)
        sanitized_text = self._sanitize_for_preference_scan(text)

        result: dict[str, Any] = {}

        models = self._extract_model_preferences(user_messages)
        if models:
            result["models"] = models

        domains = self._extract_domains(sanitized_text)
        if domains:
            result["domains"] = domains

        fmt = self._extract_output_format(sanitized_text)
        if fmt:
            result["output_format"] = fmt

        return result

    def _sanitize_for_preference_scan(self, text: str) -> str:
        """Remove filesystem-looking tokens before inferring global preferences."""
        cleaned = _ABSOLUTE_PATH_RE.sub(" ", text)
        cleaned = _RELATIVE_PATH_RE.sub(" ", cleaned)
        cleaned = _FILENAME_RE.sub(" ", cleaned)
        return " ".join(cleaned.split())

    def _extract_model_preferences(
        self, messages: list[str]
    ) -> dict[str, str]:
        """Detect explicit model mentions like 'use Claude for drafting'."""
        prefs: dict[str, str] = {}
        for msg in messages:
            msg_lower = msg.lower()
            for _model_family, pattern in _MODEL_PATTERNS.items():
                match = pattern.search(msg)
                if not match:
                    continue
                model_name = match.group(0).strip()
                for task_type, keywords in _TASK_KEYWORDS.items():
                    for kw in keywords:
                        if kw in msg_lower:
                            prefs[task_type] = model_name
                            break
        return prefs

    def _resolve_domain_keywords(self) -> dict[str, list[str]]:
        if self._domain_keywords is not None:
            return self._domain_keywords
        try:
            from dan.server.concierge.domain_learning import get_domain_keyword_map

            return get_domain_keyword_map(self._behavior_store)
        except Exception:
            return {}

    @staticmethod
    def _domain_keyword_matches(text_lower: str, keyword: str) -> bool:
        cleaned = keyword.strip().lower()
        if not cleaned or cleaned in _LOW_SIGNAL_DOMAIN_KEYWORDS:
            return False
        return re.search(rf"\b{re.escape(cleaned)}\b", text_lower) is not None

    def _extract_domains(self, text: str) -> list[str]:
        """Detect domain keywords in conversation."""
        text_lower = text.lower()
        found: list[str] = []
        for domain, keywords in self._resolve_domain_keywords().items():
            if any(self._domain_keyword_matches(text_lower, kw) for kw in keywords):
                found.append(domain)
        return found

    def _extract_output_format(self, text: str) -> str:
        """Detect preferred output format mentions."""
        for fmt, pattern in _FORMAT_PATTERNS.items():
            if pattern.search(text):
                return fmt
        return ""
