"""Pytest tests for dan.adapters.telegram_config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dan.adapters.telegram_config import (
    TelegramBotConfig,
    TelegramFleetConfig,
    TelegramGroupConfig,
    TelegramSettings,
    default_config_path,
    load_fleet_config,
    save_fleet_config,
)


def test_telegram_bot_config_defaults() -> None:
    cfg = TelegramBotConfig()
    assert cfg.token == ""
    assert cfg.personality == ""
    assert cfg.projects == []
    assert cfg.default is False
    assert cfg.allowed_users == []


def test_telegram_bot_config_custom_values() -> None:
    cfg = TelegramBotConfig(
        token="x:y",
        personality="helpful",
        projects=["p1", "p2"],
        default=True,
        allowed_users=[123, 456],
    )
    assert cfg.token == "x:y"
    assert cfg.personality == "helpful"
    assert cfg.projects == ["p1", "p2"]
    assert cfg.default is True
    assert cfg.allowed_users == [123, 456]


def test_telegram_group_config_defaults() -> None:
    cfg = TelegramGroupConfig()
    assert cfg.forum_topics is False
    assert cfg.topic_map == {}


def test_telegram_settings_defaults() -> None:
    cfg = TelegramSettings()
    assert cfg.streaming_edits is True
    assert cfg.use_reactions is True
    assert cfg.auto_pin_deliverables is False
    assert cfg.progress_throttle == 5.0
    assert cfg.max_inbound_media_mb == 20.0


def test_telegram_settings_custom_values() -> None:
    cfg = TelegramSettings(
        streaming_edits=False,
        use_reactions=False,
        auto_pin_deliverables=True,
        progress_throttle=10.0,
        max_inbound_media_mb=50.0,
    )
    assert cfg.streaming_edits is False
    assert cfg.use_reactions is False
    assert cfg.auto_pin_deliverables is True
    assert cfg.progress_throttle == 10.0
    assert cfg.max_inbound_media_mb == 50.0


def test_telegram_fleet_config_defaults() -> None:
    cfg = TelegramFleetConfig()
    assert cfg.comment == "DAN Telegram bot fleet configuration"
    assert cfg.bots == {}
    assert cfg.groups == {}
    assert cfg.settings.streaming_edits is True


def test_telegram_fleet_config_nested_models() -> None:
    cfg = TelegramFleetConfig(
        bots={"main": TelegramBotConfig(token="t1", default=True)},
        groups={"g1": TelegramGroupConfig(forum_topics=True, topic_map={"a": "b"})},
        settings=TelegramSettings(progress_throttle=3.0),
    )
    assert cfg.bots["main"].token == "t1"
    assert cfg.bots["main"].default is True
    assert cfg.groups["g1"].forum_topics is True
    assert cfg.groups["g1"].topic_map == {"a": "b"}
    assert cfg.settings.progress_throttle == 3.0


def test_telegram_fleet_config_comment_alias() -> None:
    cfg = TelegramFleetConfig.model_validate({"_comment": "custom comment"})
    assert cfg.comment == "custom comment"
    dumped = cfg.model_dump(by_alias=True)
    assert "_comment" in dumped
    assert dumped["_comment"] == "custom comment"


def test_load_fleet_config_nonexistent_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "missing" / "config.json"
    loaded = load_fleet_config(p)
    assert isinstance(loaded, TelegramFleetConfig)
    assert loaded.bots == {}
    assert loaded.groups == {}
    assert loaded.comment == "DAN Telegram bot fleet configuration"


def test_load_fleet_config_valid_json_loads(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps({
            "_comment": "test fleet",
            "bots": {"b1": {"token": "t1", "default": True}},
            "groups": {"g1": {"forum_topics": True, "topic_map": {"x": "y"}}},
            "settings": {"progress_throttle": 2.0},
        })
    )
    loaded = load_fleet_config(p)
    assert loaded.comment == "test fleet"
    assert loaded.bots["b1"].token == "t1"
    assert loaded.bots["b1"].default is True
    assert loaded.groups["g1"].forum_topics is True
    assert loaded.groups["g1"].topic_map == {"x": "y"}
    assert loaded.settings.progress_throttle == 2.0


def test_save_fleet_config_writes_valid_json(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "dir" / "config.json"
    cfg = TelegramFleetConfig(
        comment="saved",
        bots={"b1": TelegramBotConfig(token="t")},
    )
    save_fleet_config(cfg, p)
    assert p.exists()
    assert p.parent.exists()
    data = json.loads(p.read_text())
    assert data["_comment"] == "saved"
    assert data["bots"]["b1"]["token"] == "t"


def test_save_fleet_config_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "a" / "b" / "c" / "config.json"
    assert not p.parent.exists()
    save_fleet_config(TelegramFleetConfig(), p)
    assert p.parent.exists()
    assert p.exists()


def test_save_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    original = TelegramFleetConfig(
        comment="roundtrip",
        bots={"b1": TelegramBotConfig(token="t1", projects=["p1"])},
        groups={"g1": TelegramGroupConfig(topic_map={"k": "v"})},
        settings=TelegramSettings(max_inbound_media_mb=15.0),
    )
    save_fleet_config(original, p)
    loaded = load_fleet_config(p)
    assert loaded.comment == original.comment
    assert loaded.bots["b1"].token == original.bots["b1"].token
    assert loaded.bots["b1"].projects == original.bots["b1"].projects
    assert loaded.groups["g1"].topic_map == original.groups["g1"].topic_map
    assert loaded.settings.max_inbound_media_mb == original.settings.max_inbound_media_mb


def test_default_config_path_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAN_TELEGRAM_CONFIG", raising=False)
    result = default_config_path()
    assert result == Path.home() / ".dan" / "telegram" / "config.json"


def test_default_config_path_with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_TELEGRAM_CONFIG", "/custom/telegram/config.json")
    result = default_config_path()
    assert result == Path("/custom/telegram/config.json")
