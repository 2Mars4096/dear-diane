"""Backward-compatible re-exports for PII tokenization.

Implementation lives in :mod:`dan.llm_core.pii_tokenizer` so workflow executors
and other non-server code can import PII without violating module boundaries
(plan 41-1 / 41-4).
"""

from __future__ import annotations

from dan.llm_core.pii_tokenizer import (  # noqa: F401
    PIICategory,
    PIISession,
    SensitiveWord,
    SensitiveWordRegistry,
    TokenizingProviderWrapper,
    clear_pii_session,
    current_pii_session,
    detect_auto_pii,
    detokenize,
    get_current_pii_session,
    get_pii_session,
    handle_pii_command,
    is_pii_enabled,
    set_current_pii_session,
    tokenize,
)

__all__ = [
    "PIICategory",
    "PIISession",
    "SensitiveWord",
    "SensitiveWordRegistry",
    "TokenizingProviderWrapper",
    "clear_pii_session",
    "current_pii_session",
    "detect_auto_pii",
    "detokenize",
    "get_current_pii_session",
    "get_pii_session",
    "handle_pii_command",
    "is_pii_enabled",
    "set_current_pii_session",
    "tokenize",
]
