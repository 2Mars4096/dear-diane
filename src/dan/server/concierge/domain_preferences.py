"""Inspect and edit saved domain preferences from chat surfaces."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from dan.domain_taxonomy import (
    format_domain_label,
    normalize_domain_list,
    normalize_domain_name,
)

from .profile_domain_sync import persist_profile_domains

logger = logging.getLogger(__name__)

_DOMAINS_USAGE = (
    "Usage: `/domains`, `/domains known`, `/domains add <domain>`, "
    "`/domains remove <domain>`, or `/domains clear`."
)


def _parse_domain_targets(raw: str) -> list[tuple[str, str]]:
    cleaned = str(raw or "").strip()
    if not cleaned:
        return []
    pieces = [piece.strip() for piece in cleaned.split(",")] if "," in cleaned else [cleaned]
    parsed: list[tuple[str, str]] = []
    seen: set[str] = set()
    for piece in pieces:
        canonical = normalize_domain_name(piece)
        if not canonical or canonical in seen:
            continue
        parsed.append((piece, canonical))
        seen.add(canonical)
    return parsed


def _format_domain_line(domain: str, raw_input: str | None = None) -> str:
    line = f"- {format_domain_label(domain)} (`{domain}`)"
    raw = str(raw_input or "").strip()
    if raw:
        raw_lower = raw.lower()
        label_lower = format_domain_label(domain).lower()
        if raw_lower not in {domain, label_lower}:
            line += f" from `{raw}`"
    return line


def _touch_profile(user_profile: Any) -> None:
    if hasattr(user_profile, "updated_at"):
        user_profile.updated_at = datetime.now(timezone.utc)


def _current_profile_domains(user_profile: Any) -> list[str]:
    return normalize_domain_list(getattr(user_profile, "common_domains", []) or [])


def _known_domains(behavior_store: Any = None) -> list[str]:
    try:
        from dan.server.concierge.domain_learning import get_domain_keyword_map

        return sorted(normalize_domain_list(get_domain_keyword_map(behavior_store).keys()))
    except Exception:
        logger.debug("Failed to resolve known domains", exc_info=True)
        return []

def _render_saved_domains(user_profile: Any, behavior_store: Any = None) -> str:
    saved = _current_profile_domains(user_profile)
    lines: list[str] = []
    if saved:
        lines.append(f"Saved domains ({len(saved)}):")
        lines.extend(_format_domain_line(domain) for domain in saved)
    else:
        lines.append("Saved domains: none.")

    known = _known_domains(behavior_store)
    if known:
        preview = ", ".join(
            f"{format_domain_label(domain)} (`{domain}`)"
            for domain in known[:6]
        )
        extra = len(known) - min(len(known), 6)
        if extra > 0:
            preview += f", +{extra} more"
        lines.append(f"Known canonical domains: {preview}")

    lines.append(_DOMAINS_USAGE)
    return "\n".join(lines)


def handle_domains_command(
    text: str,
    user_profile: Any = None,
    *,
    memory_kernel: Any = None,
    behavior_store: Any = None,
) -> str:
    """Handle ``/domains`` inspection and edits for ``UserProfile.common_domains``."""
    if user_profile is None or not hasattr(user_profile, "common_domains"):
        return "Domain preferences are unavailable in this runtime."

    parts = str(text or "").strip().split(maxsplit=2)
    subcommand = parts[1].lower() if len(parts) >= 2 else "list"
    args = parts[2].strip() if len(parts) >= 3 else ""

    if subcommand in {"list", "show"}:
        return _render_saved_domains(user_profile, behavior_store)

    if subcommand == "known":
        known = _known_domains(behavior_store)
        if not known:
            return "No known canonical domains are registered right now."
        lines = [f"Known canonical domains ({len(known)}):"]
        lines.extend(_format_domain_line(domain) for domain in known)
        return "\n".join(lines)

    if subcommand == "add":
        targets = _parse_domain_targets(args)
        if not targets:
            return f"Please provide at least one domain to add. {_DOMAINS_USAGE}"
        current = _current_profile_domains(user_profile)
        current_set = set(current)
        added: list[tuple[str, str]] = []
        already: list[tuple[str, str]] = []
        for raw, canonical in targets:
            if canonical in current_set:
                already.append((raw, canonical))
                continue
            current.append(canonical)
            current_set.add(canonical)
            added.append((raw, canonical))
        if not added:
            lines = ["Those domains are already saved:"]
            lines.extend(_format_domain_line(domain, raw) for raw, domain in already)
            return "\n".join(lines)
        user_profile.common_domains = current
        _touch_profile(user_profile)
        try:
            persist_profile_domains(user_profile, memory_kernel)
        except Exception as exc:
            logger.debug("Failed to save added domains", exc_info=True)
            return f"Failed to save domains: {exc}"
        lines = [
            f"Saved {len(added)} domain preference(s)."
            if len(added) != 1
            else "Saved 1 domain preference."
        ]
        lines.extend(_format_domain_line(domain, raw) for raw, domain in added)
        if already:
            lines.append("Already saved:")
            lines.extend(_format_domain_line(domain, raw) for raw, domain in already)
        return "\n".join(lines)

    if subcommand == "remove":
        targets = _parse_domain_targets(args)
        if not targets:
            return f"Please provide at least one domain to remove. {_DOMAINS_USAGE}"
        current = _current_profile_domains(user_profile)
        current_set = set(current)
        removed: list[tuple[str, str]] = []
        missing: list[tuple[str, str]] = []
        for raw, canonical in targets:
            if canonical in current_set:
                removed.append((raw, canonical))
            else:
                missing.append((raw, canonical))
        if not removed:
            lines = ["No matching saved domains found."]
            if missing:
                lines.extend(_format_domain_line(domain, raw) for raw, domain in missing)
            return "\n".join(lines)
        removed_ids = {domain for _, domain in removed}
        user_profile.common_domains = [domain for domain in current if domain not in removed_ids]
        _touch_profile(user_profile)
        try:
            persist_profile_domains(user_profile, memory_kernel)
        except Exception as exc:
            logger.debug("Failed to save removed domains", exc_info=True)
            return f"Failed to save domains: {exc}"
        lines = [
            f"Removed {len(removed)} saved domain preference(s)."
            if len(removed) != 1
            else "Removed 1 saved domain preference."
        ]
        lines.extend(_format_domain_line(domain, raw) for raw, domain in removed)
        if missing:
            lines.append("Not currently saved:")
            lines.extend(_format_domain_line(domain, raw) for raw, domain in missing)
        return "\n".join(lines)

    if subcommand == "clear":
        current = _current_profile_domains(user_profile)
        if not current:
            return "Saved domains are already empty."
        user_profile.common_domains = []
        _touch_profile(user_profile)
        try:
            persist_profile_domains(user_profile, memory_kernel)
        except Exception as exc:
            logger.debug("Failed to clear domains", exc_info=True)
            return f"Failed to save domains: {exc}"
        return (
            f"Cleared {len(current)} saved domain preference(s)."
            if len(current) != 1
            else "Cleared 1 saved domain preference."
        )

    return f"Unknown `/domains` subcommand `{subcommand}`. {_DOMAINS_USAGE}"
