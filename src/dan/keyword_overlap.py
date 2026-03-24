from __future__ import annotations


def query_keyword_overlap(query: str, content: str) -> float:
    """Return the fraction of query words that also appear in content."""
    if not query or not content:
        return 0.0
    query_words = set(query.lower().split())
    content_words = set(content.lower().split())
    if not query_words:
        return 0.0
    return len(query_words & content_words) / len(query_words)
