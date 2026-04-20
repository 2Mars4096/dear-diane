"""Helpers for collapsing chat event streams into terminal-facing content."""

from __future__ import annotations

from typing import Any, AsyncIterable


def _event_value(event: Any, field: str, default: Any = "") -> Any:
    if isinstance(event, dict):
        return event.get(field, default)
    return getattr(event, field, default)


async def collect_terminal_content(
    event_stream: AsyncIterable[Any],
    *,
    reassurance_messages: set[str] | None = None,
) -> str:
    """Collapse terminal chat events into a meaningful transcript."""
    reassurance_messages = reassurance_messages or set()
    parts: list[str] = []
    async for event in event_stream:
        evt_type = str(_event_value(event, "type", "") or "")
        if evt_type == "chat_error":
            raise RuntimeError(_event_value(event, "error", "Unknown concierge error"))
        if evt_type == "chat_interrupted":
            raise RuntimeError("Scheduled action was interrupted")
        if evt_type == "chat_notice":
            continue
        if evt_type != "chat_complete":
            continue
        if _event_value(event, "detected_mode", None) == "progress_ack":
            continue
        content = str(_event_value(event, "content", "") or "")
        if not content or content in reassurance_messages:
            continue
        parts.append(content)
    if not parts:
        raise RuntimeError("No terminal response produced")
    return "\n\n".join(parts)
