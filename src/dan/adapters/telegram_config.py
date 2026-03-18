"""Pydantic-based configuration for the DAN Telegram bot fleet."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field


def default_config_path() -> Path:
    path = os.environ.get("DAN_TELEGRAM_CONFIG")
    if path:
        return Path(path)
    return Path.home() / ".dan" / "telegram" / "config.json"


class TelegramBotConfig(BaseModel):
    token: str = ""
    username: str = ""
    personality: str = ""
    projects: list[str] = Field(default_factory=list)
    default: bool = False
    allowed_users: list[int | str] = Field(default_factory=list)
    auto_start: bool = False


class TelegramGroupConfig(BaseModel):
    forum_topics: bool = False
    topic_map: dict[str, str] = Field(default_factory=dict)


class TelegramSettings(BaseModel):
    streaming_edits: bool = True
    use_reactions: bool = True
    auto_pin_deliverables: bool = False
    progress_throttle: float = 5.0
    max_inbound_media_mb: float = 20.0
    mini_app_url: str = ""


class TelegramFleetConfig(BaseModel):
    comment: str = Field(
        default="DAN Telegram bot fleet configuration",
        alias="_comment",
    )
    bots: dict[str, TelegramBotConfig] = Field(default_factory=dict)
    groups: dict[str, TelegramGroupConfig] = Field(default_factory=dict)
    settings: TelegramSettings = Field(default_factory=TelegramSettings)

    model_config = {"populate_by_name": True}


def load_fleet_config(path: Path | str | None = None) -> TelegramFleetConfig:
    p = Path(path) if path is not None else default_config_path()
    if not p.exists():
        return TelegramFleetConfig()
    data = json.loads(p.read_text())
    return TelegramFleetConfig.model_validate(data)


def save_fleet_config(
    config: TelegramFleetConfig,
    path: Path | str | None = None,
) -> None:
    p = Path(path) if path is not None else default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(by_alias=True, exclude_none=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".config.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, p)
    except Exception:
        os.unlink(tmp)
        raise
