"""Inspect and edit saved scheduling timezone preferences."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from dan.engine.user_profile import save_user_profile

from .scheduler import _normalize_timezone_name, _workspace_default_timezone

logger = logging.getLogger(__name__)

_TIMEZONE_USAGE = (
    "Usage: `/timezone`, `/timezone show`, `/timezone set <IANA zone>`, "
    "or `/timezone clear`."
)


def _touch_profile(user_profile: Any) -> None:
    if hasattr(user_profile, "updated_at"):
        user_profile.updated_at = datetime.now(timezone.utc)


def _current_timezone(user_profile: Any) -> str:
    return str(getattr(user_profile, "preferred_timezone", "") or "").strip()


def _render_timezone_status(user_profile: Any) -> str:
    saved = _current_timezone(user_profile)
    workspace = _workspace_default_timezone()
    lines = [
        f"Saved timezone preference: `{saved}`" if saved else "Saved timezone preference: none.",
        f"Workspace default timezone: `{workspace}`",
        "Scheduling precedence: explicit command timezone -> saved preference -> workspace default -> UTC.",
        _TIMEZONE_USAGE,
    ]
    return "\n".join(lines)


def handle_timezone_command(text: str, user_profile: Any = None) -> str:
    """Handle ``/timezone`` inspection and edits for ``UserProfile.preferred_timezone``."""
    if user_profile is None or not hasattr(user_profile, "preferred_timezone"):
        return "Timezone preferences are unavailable in this runtime."

    parts = str(text or "").strip().split(maxsplit=2)
    subcommand = parts[1].lower() if len(parts) >= 2 else "show"
    args = parts[2].strip() if len(parts) >= 3 else ""

    if subcommand in {"show", "list"}:
        return _render_timezone_status(user_profile)

    if subcommand == "set":
        normalized = _normalize_timezone_name(args)
        if normalized is None:
            return (
                f"Could not resolve timezone `{args}`. "
                "Use an IANA zone like `Asia/Hong_Kong` or `America/New_York`."
            )
        user_profile.preferred_timezone = normalized
        _touch_profile(user_profile)
        try:
            save_user_profile(user_profile)
        except Exception as exc:
            logger.debug("Failed to persist timezone preference", exc_info=True)
            return f"Failed to save timezone preference: {exc}"
        return (
            f"Saved timezone preference: `{normalized}`\n"
            "New schedules will use this timezone unless the command specifies a different one."
        )

    if subcommand == "clear":
        if not _current_timezone(user_profile):
            return "Timezone preference is already empty."
        user_profile.preferred_timezone = ""
        _touch_profile(user_profile)
        try:
            save_user_profile(user_profile)
        except Exception as exc:
            logger.debug("Failed to clear timezone preference", exc_info=True)
            return f"Failed to clear timezone preference: {exc}"
        return "Cleared saved timezone preference. New schedules will use the workspace default timezone."

    return _TIMEZONE_USAGE
