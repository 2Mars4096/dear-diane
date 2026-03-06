from __future__ import annotations

import re
from typing import Any

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

_DOMAIN_KEYWORDS = {
    "supply chain": ["supply chain", "logistics", "inventory", "procurement", "warehouse"],
    "equity research": ["equity", "stock", "investment", "portfolio", "trading"],
    "operations management": ["operations", "manufacturing", "production", "lean"],
    "machine learning": ["machine learning", "ml", "deep learning", "neural", "model training"],
    "scientific writing": ["paper", "manuscript", "journal", "academic", "latex", "informs"],
}

_FORMAT_PATTERNS = {
    "latex": re.compile(r"\b(latex|tex|pdf)\b", re.IGNORECASE),
    "markdown": re.compile(r"\b(markdown|md)\b", re.IGNORECASE),
    "json": re.compile(r"\bjson\b", re.IGNORECASE),
}


class PreferenceExtractor:
    """Extract user preferences from conversation history using heuristics."""

    def extract_from_messages(
        self, messages: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Scan messages for implicit preferences. Returns preference deltas."""
        user_messages = [m["content"] for m in messages if m.get("role") == "user"]
        text = " ".join(user_messages)

        result: dict[str, Any] = {}

        models = self._extract_model_preferences(user_messages)
        if models:
            result["models"] = models

        domains = self._extract_domains(text)
        if domains:
            result["domains"] = domains

        fmt = self._extract_output_format(text)
        if fmt:
            result["output_format"] = fmt

        return result

    def _extract_model_preferences(
        self, messages: list[str]
    ) -> dict[str, str]:
        """Detect explicit model mentions like 'use Claude for drafting'."""
        prefs: dict[str, str] = {}
        for msg in messages:
            msg_lower = msg.lower()
            for model_family, pattern in _MODEL_PATTERNS.items():
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

    def _extract_domains(self, text: str) -> list[str]:
        """Detect domain keywords in conversation."""
        text_lower = text.lower()
        found = []
        for domain, keywords in _DOMAIN_KEYWORDS.items():
            for kw in keywords:
                if kw in text_lower:
                    found.append(domain)
                    break
        return found

    def _extract_output_format(self, text: str) -> str:
        """Detect preferred output format mentions."""
        for fmt, pattern in _FORMAT_PATTERNS.items():
            if pattern.search(text):
                return fmt
        return ""
