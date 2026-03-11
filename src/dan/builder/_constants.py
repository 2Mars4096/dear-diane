"""Shared constants for the builder and meta modules.

This is a leaf module with no project imports — safe to import from
anywhere without triggering circular dependency chains.
"""

CONTENT_KEYWORDS: frozenset[str] = frozenset({
    "write", "report", "paper", "memo", "draft", "article",
    "essay", "blog", "document", "letter", "proposal",
})
