"""Tests for Telegram fleet config loading and saving."""

from __future__ import annotations

import json
from pathlib import Path

from dan.adapters.telegram_config import (
    TelegramBotConfig,
    TelegramFleetConfig,
    TelegramGroupConfig,
    default_config_path,
    load_fleet_config,
    save_fleet_config,
)


def test_default_config_path_uses_env_override(monkeypatch) -> None:
    monkeypatch.setenv("DAN_TELEGRAM_CONFIG", "/tmp/custom-telegram-config.json")
    assert default_config_path() == Path("/tmp/custom-telegram-config.json")


def test_load_missing_config_returns_empty_defaults(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    config = load_fleet_config(path)
    assert config.bots == {}
    assert config.groups == {}
    assert config.settings.streaming_edits is True


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "telegram" / "config.json"
    config = TelegramFleetConfig(
        bots={
            "research-bot": TelegramBotConfig(
                token="token-1",
                personality="Research specialist",
                projects=["literature-review"],
                default=True,
            ),
        },
        groups={
            "-1001234": TelegramGroupConfig(
                forum_topics=True,
                topic_map={"101": "literature-review"},
            ),
        },
    )

    save_fleet_config(config, path)
    loaded = load_fleet_config(path)

    assert loaded.comment == "DAN Telegram bot fleet configuration"
    assert loaded.bots["research-bot"].token == "token-1"
    assert loaded.bots["research-bot"].projects == ["literature-review"]
    assert loaded.groups["-1001234"].forum_topics is True
    assert loaded.groups["-1001234"].topic_map == {"101": "literature-review"}

    raw = json.loads(path.read_text())
    assert raw["_comment"] == "DAN Telegram bot fleet configuration"
