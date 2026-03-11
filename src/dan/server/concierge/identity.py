"""Configurable bot identity — single source of truth for prefix formatting."""
from __future__ import annotations

import os
import re

_DEFAULT_BOT_NAME = "DAN"
_cached_bot_name: str | None = None


def get_bot_name(override: str | None = None) -> str:
    """Return the configured bot name.

    Resolution order: explicit *override* > ``DAN_BOT_NAME`` env var > ``"DAN"``.
    The env-var lookup is cached after the first call.
    """
    if override:
        return override
    global _cached_bot_name
    if _cached_bot_name is None:
        _cached_bot_name = os.environ.get("DAN_BOT_NAME", "").strip() or _DEFAULT_BOT_NAME
    return _cached_bot_name


def format_prefix(
    project_label: str,
    task_label: str | None = None,
    *,
    bot_name: str | None = None,
) -> str:
    """Format a scoped reply prefix: ``[Bot - Project]`` or ``[Bot - Project / Task]``."""
    name = get_bot_name(bot_name)
    if task_label:
        return f"[{name} - {project_label} / {task_label}]"
    return f"[{name} - {project_label}]"


def format_bare_prefix(*, bot_name: str | None = None) -> str:
    """Format an unscoped reply prefix: ``[Bot]``."""
    return f"[{get_bot_name(bot_name)}]"


def extract_label_from_prefix(
    text: str,
    *,
    bot_name: str | None = None,
) -> tuple[str, str]:
    """Extract project/task label from a prefix string.

    Returns ``(label, remaining_text)``.  *label* is ``""`` when the text
    has no recognised bot prefix.

    Examples::

        "[DAN - MyProj] hello"   → ("MyProj", "hello")
        "[DAN - P / T] hello"    → ("P / T", "hello")
        "[DAN] hello"            → ("", "hello")
        "no prefix"              → ("", "no prefix")
    """
    name = get_bot_name(bot_name)
    bare = f"[{name}]"
    scoped = f"[{name} - "
    stripped = text.strip()

    if stripped.startswith(scoped):
        end_idx = stripped.find("]", len(scoped))
        if end_idx >= 0:
            label = stripped[len(scoped):end_idx].strip()
            remaining = stripped[end_idx + 1:].strip()
            return label, remaining
        remaining = stripped[len(scoped):].strip()
        return "", remaining

    if stripped.startswith(f"{bare} "):
        return "", stripped[len(bare) + 1:].strip()

    return "", text


def format_compact_label(
    project_label: str,
    task_label: str | None = None,
) -> str:
    """Compact project header for Telegram: ``[Project]`` or ``[Project / Task]``."""
    if task_label:
        return f"[{project_label} / {task_label}]"
    return f"[{project_label}]"


_PREFIX_RE: re.Pattern[str] | None = None


def _get_prefix_re(bot_name: str | None = None) -> re.Pattern[str]:
    """Regex matching any prefix variant: ``[Bot]``, ``[Bot - X]``, ``[Bot - X / Y]``."""
    global _PREFIX_RE
    name = get_bot_name(bot_name)
    if _PREFIX_RE is not None and name == get_bot_name():
        return _PREFIX_RE
    escaped = re.escape(name)
    pattern = re.compile(rf"^\[{escaped}(?:\s*-\s*[^\]]+)?\]")
    if name == get_bot_name():
        _PREFIX_RE = pattern
    return pattern


def starts_with_prefix(text: str, *, bot_name: str | None = None) -> bool:
    """Check whether *text* starts with any bot prefix variant."""
    return bool(_get_prefix_re(bot_name).match(text))


def strip_prefix(text: str, *, bot_name: str | None = None) -> str:
    """Remove leading bot prefix(es) from *text*.

    Mirrors the legacy ``_strip_dan_prefix`` contract:
    - ``[Bot] hello`` → ``hello``  (normal prefix with space)
    - ``[Bot - Proj] hello`` → ``hello``  (scoped prefix)
    - ``[Bot - incomplete text`` → ``incomplete text``  (unclosed prefix)
    - ``[Bot]text`` → ``[Bot]text``  (no space → not a real prefix, keep)
    """
    name = get_bot_name(bot_name)
    bare = f"[{name}]"
    scoped = f"[{name} - "
    clean = text.strip()
    while clean.startswith(f"{bare} ") or clean.startswith(scoped):
        idx = clean.find("] ")
        if idx >= 0:
            clean = clean[idx + 2:]
        elif clean.startswith(scoped):
            clean = clean[len(scoped):]
        else:
            clean = clean[len(bare) + 1:]
        clean = clean.strip()
    return clean
