"""Built-in tool: return current date, time, timezone, and day of week."""

from __future__ import annotations

import datetime

TOOL_METADATA = {
    "tool_id": "current_datetime",
    "description": (
        "Return the current date, time, timezone, and day of week. "
        "Use this whenever you need to know the current time or date. "
        "The LLM does not have access to real-time information without this tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA timezone name (e.g. 'America/New_York'). Omit for local system timezone.",
            },
        },
        "required": [],
    },
    "examples": [
        {
            "input": {},
            "output": {
                "datetime": "2026-03-09T14:30:00-05:00",
                "date": "2026-03-09",
                "time": "14:30:00",
                "day_of_week": "Monday",
                "timezone": "America/New_York",
                "unix_timestamp": 1773175800,
            },
        },
    ],
    "category": "system",
    "returns": "dict with datetime (ISO), date, time, day_of_week, timezone, unix_timestamp",
}


async def current_datetime(timezone: str | None = None, **_kwargs) -> dict:
    if timezone:
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(timezone)
        except (ImportError, KeyError):
            raise ValueError(
                f"Unknown timezone '{timezone}'. Use IANA names like 'America/New_York', 'Europe/London', 'Asia/Tokyo'."
            )
        now = datetime.datetime.now(tz)
    else:
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()

    return {
        "datetime": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "day_of_week": now.strftime("%A"),
        "timezone": str(now.tzinfo),
        "unix_timestamp": int(now.timestamp()),
    }
