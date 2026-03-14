"""Shared utility functions for capability handlers."""
from __future__ import annotations

import logging
import os
import re as _re
import shutil
import threading
from pathlib import Path
from typing import Any

import httpx

from dan.server.capability_registry import CapabilityResult

logger = logging.getLogger(__name__)

_EXPORT_CLEANUP_DELAY = 300

_FILE_READ_MAX = 100_000


def _schedule_export_cleanup(path: str) -> None:
    """Schedule removal of a temp export directory after a delay."""
    def _cleanup() -> None:
        shutil.rmtree(path, ignore_errors=True)
    timer = threading.Timer(_EXPORT_CLEANUP_DELAY, _cleanup)
    timer.daemon = True
    timer.start()


def _truncate(text: str, limit: int = 500) -> str:
    return text[:limit] + "\u2026" if len(text) > limit else text


_IMG_MD_RE = _re.compile(r"!\[[^\]]*\]\([^)]+\)")
_BARE_IMG_URL_RE = _re.compile(
    r"^\s*\(?https?://[^\s)]+\.(?:png|jpg|jpeg|gif|webp|svg|ico)\b[^\s)]*\)?\s*$",
    _re.MULTILINE | _re.IGNORECASE,
)
_NAV_LINK_BLOCK_RE = _re.compile(
    r"(?:^[ \t]*\[[^\]]{1,60}\]\(https?://[^)]+\)\s*){4,}",
    _re.MULTILINE,
)
_REPEATED_BLANK_RE = _re.compile(r"\n{4,}")


def _sanitize_web_content(text: str) -> str:
    """Strip image markdown, bare image URLs, and dense navigation link blocks."""
    text = _IMG_MD_RE.sub("", text)
    text = _BARE_IMG_URL_RE.sub("", text)
    text = _NAV_LINK_BLOCK_RE.sub("[... navigation links removed ...]", text)
    text = _REPEATED_BLANK_RE.sub("\n\n", text)
    return text.strip()


def _failure_result(
    message: str,
    *,
    error_type: str,
    retryable: bool = False,
    data: Any = None,
    output_preview: str = "",
    stream_channel_id: str | None = None,
) -> CapabilityResult:
    return CapabilityResult(
        success=False,
        message=message,
        data=data,
        output_preview=output_preview,
        stream_channel_id=stream_channel_id,
        retryable=retryable,
        error_type=error_type,
    )


def _classify_network_exception(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, ImportError):
        return "provider_unavailable", False
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else None
        if status == 429 or (status is not None and status >= 500):
            return "provider_error", True
        return "provider_error", False
    if isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.RequestError,
            TimeoutError,
            ConnectionError,
            OSError,
        ),
    ):
        return "network_error", True
    text = str(exc).lower()
    if any(
        marker in text
        for marker in (
            "rate limit",
            "temporar",
            "overloaded",
            "service unavailable",
            "try again",
            "timed out",
            "connection reset",
            "connection refused",
            "network",
        )
    ):
        return "provider_error", True
    return "internal_exception", False


def _resolve_user_path(raw_path: str) -> Path:
    """Resolve a user-provided path, expanding ~ and handling absolute/relative."""
    expanded = Path(raw_path).expanduser()
    if expanded.is_absolute():
        return expanded
    workspace = Path(os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd()))
    return (workspace / expanded).resolve()
