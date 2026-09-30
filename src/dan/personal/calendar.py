"""Download-only calendar export; this never inserts a provider event."""
from datetime import date, datetime, time, timezone
from .models import resolved_instant


def escape(value):
    return str(value).replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def fold(line):
    """RFC 5545's 75-octet limit without splitting a UTF-8 character."""
    lines, current = [], ""
    for char in line:
        if len((current + char).encode()) > 75:
            lines.append(current)
            current = " "
        current += char
    return "\r\n".join([*lines, current])


def export_ics(record):
    if record["lifecycle"] != "active":
        raise ValueError("Confirm the commitment before downloading a calendar file")
    day = date.fromisoformat(record["date"])
    if record["all_day"]:
        start = "DTSTART;VALUE=DATE:" + day.strftime("%Y%m%d")
    else:
        instant = resolved_instant(day, time.fromisoformat(record["time"]), record["timezone"], record.get("offset"))
        start = "DTSTART:" + instant.strftime("%Y%m%dT%H%M%SZ")
    # No invented duration, attendees, alarms or external invitation semantics.
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Dear Diane//Commitments//EN", "CALSCALE:GREGORIAN",
             "BEGIN:VEVENT", "UID:" + record["id"] + "@dear-diane", "SEQUENCE:" + str(record["revision"]),
             "DTSTAMP:" + datetime.fromisoformat(record["updated_at"]).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
             start, "SUMMARY:" + escape(record["title"]), "LOCATION:" + escape(record["location"]),
             "DESCRIPTION:Exported from Dear Diane. Calendar insertion is not verified.", "END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(fold(line) for line in lines) + "\r\n").encode()
