"""Validated manual-review contract; missing dates are never guessed."""
from datetime import date as Date, datetime, time as Time, timezone, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CaptureInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    operation_id: str = Field(min_length=8, max_length=100)
    text: str = Field(default='', max_length=65536)
    source_ids: list[Annotated[str, Field(min_length=36, max_length=36)]] = Field(default_factory=list, max_length=5)
    locale: str = Field(default="", max_length=35)

    @field_validator("text")
    @classmethod
    def bounded_text(cls, value):
        if len(value.encode()) > 65536 or "\0" in value:
            raise ValueError("Capture must be UTF-8 text up to 64 KiB, without null bytes")
        return value

    @model_validator(mode='after')
    def has_source(self):
        if not self.text and not self.source_ids:
            raise ValueError('Paste text or attach a supported file')
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError('Each attached source must appear only once')
        return self


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    operation_id: str = Field(min_length=8, max_length=100)
    expected_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=240)
    date: Date | None = None
    time: Time | None = None
    timezone: str | None = Field(default=None, max_length=80)
    all_day: bool = False
    offset: str | None = Field(default=None, pattern=r"^[+-](?:0\d|1[0-4]):[0-5]\d$")
    location: str = Field(default="", max_length=1000)
    decision: Literal["save", "confirm", "dismiss"] = "save"
    confirm_as_new: bool = False

    @field_validator("timezone")
    @classmethod
    def known_zone(cls, value):
        if value:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("Choose an IANA timezone, such as Asia/Hong_Kong") from exc
        return value or None

    @field_validator("time")
    @classmethod
    def local_clock(cls, value):
        if value and (value.tzinfo is not None or value.second or value.microsecond):
            raise ValueError("Use local time with minute precision and a separate timezone")
        return value

    @model_validator(mode="after")
    def confirm_complete(self):
        if self.all_day and self.time is not None:
            raise ValueError("All-day commitments cannot also have a time")
        if self.decision == "confirm":
            if not self.date or not self.timezone or (not self.all_day and self.time is None):
                raise ValueError("Confirm a date, timezone, and time (or all day) first")
            if not self.all_day:
                resolved_instant(self.date, self.time, self.timezone, self.offset)
        return self


def resolved_instant(day: Date, clock: Time, zone: str, offset: str | None = None) -> datetime:
    local = datetime.combine(day, clock)
    tz = ZoneInfo(zone)
    candidates = set()
    requested = None
    if offset:
        requested = timedelta(minutes=(int(offset[1:3]) * 60 + int(offset[4:6])) * (-1 if offset[0] == '-' else 1))
    for fold in (0, 1):
        instant = local.replace(tzinfo=tz, fold=fold).astimezone(timezone.utc)
        if instant.astimezone(tz).replace(tzinfo=None) == local and (requested is None or instant.astimezone(tz).utcoffset() == requested):
            candidates.add(instant)
    if len(candidates) != 1:
        raise ValueError("This time is ambiguous or does not exist because of daylight saving; choose an unambiguous time")
    return candidates.pop()
