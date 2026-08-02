"""Tests for dan.notifications.config — NotificationConfig, load, save."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from dan.notifications.config import (
    ChannelConfig,
    NotificationConfig,
    WebhookConfig,
    load_notification_config,
    save_notification_config,
)


# ── Default config ────────────────────────────────────────────────────────


class TestDefaultConfig:
    def test_macos_enabled_on_darwin(self):
        with patch.object(sys, "platform", "darwin"):
            cfg = NotificationConfig()
        assert cfg.macos.enabled is True

    def test_macos_disabled_on_linux(self):
        with patch("dan.notifications.config.sys") as mock_sys:
            mock_sys.platform = "linux"
            cfg = NotificationConfig(
                macos=ChannelConfig(enabled=("linux" == "darwin"))
            )
        assert cfg.macos.enabled is False

    def test_bell_disabled_by_default(self):
        cfg = NotificationConfig()
        assert cfg.bell.enabled is False

    def test_webhook_disabled_by_default(self):
        cfg = NotificationConfig()
        assert cfg.webhook.enabled is False
        assert cfg.webhook.url == ""

    def test_default_event_types(self):
        cfg = NotificationConfig()
        expected = [
            "run_completed",
            "run_failed",
            "human_input_needed",
            "schedule_result_ready",
        ]
        assert cfg.macos.event_types == expected
        assert cfg.bell.event_types == expected
        assert cfg.webhook.event_types == expected

    def test_webhook_default_timeout(self):
        cfg = NotificationConfig()
        assert cfg.webhook.timeout == 10.0


# ── Env var overrides ─────────────────────────────────────────────────────


class TestEnvVarOverrides:
    def test_bell_enabled_via_env(self, tmp_path: Path):
        with patch.dict("os.environ", {"DAN_NOTIFY_BELL": "1"}, clear=False):
            cfg = load_notification_config(tmp_path / "nope.json")
        assert cfg.bell.enabled is True

    def test_bell_true_string(self, tmp_path: Path):
        with patch.dict("os.environ", {"DAN_NOTIFY_BELL": "true"}, clear=False):
            cfg = load_notification_config(tmp_path / "nope.json")
        assert cfg.bell.enabled is True

    def test_bell_yes_string(self, tmp_path: Path):
        with patch.dict("os.environ", {"DAN_NOTIFY_BELL": "yes"}, clear=False):
            cfg = load_notification_config(tmp_path / "nope.json")
        assert cfg.bell.enabled is True

    def test_bell_disabled_by_default_env(self, tmp_path: Path):
        with patch.dict("os.environ", {}, clear=False):
            env = dict(**{k: v for k, v in __import__("os").environ.items() if k != "DAN_NOTIFY_BELL"})
            with patch.dict("os.environ", env, clear=True):
                cfg = load_notification_config(tmp_path / "nope.json")
        assert cfg.bell.enabled is False

    def test_webhook_url_enables_webhook(self, tmp_path: Path):
        with patch.dict(
            "os.environ",
            {"DAN_NOTIFY_WEBHOOK_URL": "https://hooks.example.com/dan"},
            clear=False,
        ):
            cfg = load_notification_config(tmp_path / "nope.json")
        assert cfg.webhook.enabled is True
        assert cfg.webhook.url == "https://hooks.example.com/dan"

    def test_webhook_headers_from_env(self, tmp_path: Path):
        headers = json.dumps({"Authorization": "Bearer tok123"})
        with patch.dict(
            "os.environ",
            {"DAN_NOTIFY_WEBHOOK_URL": "https://x.com", "DAN_NOTIFY_WEBHOOK_HEADERS": headers},
            clear=False,
        ):
            cfg = load_notification_config(tmp_path / "nope.json")
        assert cfg.webhook.headers == {"Authorization": "Bearer tok123"}

    def test_webhook_headers_invalid_json_ignored(self, tmp_path: Path):
        with patch.dict(
            "os.environ",
            {"DAN_NOTIFY_WEBHOOK_URL": "https://x.com", "DAN_NOTIFY_WEBHOOK_HEADERS": "not-json"},
            clear=False,
        ):
            cfg = load_notification_config(tmp_path / "nope.json")
        assert cfg.webhook.headers == {}


# ── File-based config ─────────────────────────────────────────────────────


class TestFileConfig:
    def test_load_from_json_file(self, tmp_path: Path):
        cfg_file = tmp_path / "notifications.json"
        data = {
            "macos": {"enabled": False},
            "bell": {"enabled": True, "event_types": ["run_failed"]},
            "webhook": {"enabled": False},
        }
        cfg_file.write_text(json.dumps(data))
        with patch.dict("os.environ", {}, clear=False):
            env = {k: v for k, v in __import__("os").environ.items() if not k.startswith("DAN_NOTIFY")}
            with patch.dict("os.environ", env, clear=True):
                cfg = load_notification_config(cfg_file)
        assert cfg.macos.enabled is False
        assert cfg.bell.enabled is True
        assert cfg.bell.event_types == ["run_failed"]

    def test_env_overrides_file(self, tmp_path: Path):
        cfg_file = tmp_path / "notifications.json"
        data = {"bell": {"enabled": False}}
        cfg_file.write_text(json.dumps(data))
        with patch.dict("os.environ", {"DAN_NOTIFY_BELL": "1"}, clear=False):
            cfg = load_notification_config(cfg_file)
        assert cfg.bell.enabled is True

    def test_invalid_json_falls_back_to_defaults(self, tmp_path: Path):
        cfg_file = tmp_path / "notifications.json"
        cfg_file.write_text("{bad json")
        with patch.dict("os.environ", {}, clear=False):
            env = {k: v for k, v in __import__("os").environ.items() if not k.startswith("DAN_NOTIFY")}
            with patch.dict("os.environ", env, clear=True):
                cfg = load_notification_config(cfg_file)
        assert cfg.bell.enabled is False

    def test_missing_file_uses_defaults(self, tmp_path: Path):
        with patch.dict("os.environ", {}, clear=False):
            env = {k: v for k, v in __import__("os").environ.items() if not k.startswith("DAN_NOTIFY")}
            with patch.dict("os.environ", env, clear=True):
                cfg = load_notification_config(tmp_path / "nonexistent.json")
        assert cfg.webhook.enabled is False


# ── Save / load round-trip ────────────────────────────────────────────────


class TestSaveLoadRoundTrip:
    def test_round_trip(self, tmp_path: Path):
        cfg_file = tmp_path / "notifications.json"
        original = NotificationConfig(
            macos=ChannelConfig(enabled=True),
            bell=ChannelConfig(enabled=True, event_types=["run_failed"]),
            webhook=WebhookConfig(
                enabled=True,
                url="https://hooks.example.com",
                headers={"X-Token": "abc"},
                timeout=5.0,
            ),
        )
        save_notification_config(original, cfg_file)
        assert cfg_file.exists()

        with patch.dict("os.environ", {}, clear=False):
            env = {k: v for k, v in __import__("os").environ.items() if not k.startswith("DAN_NOTIFY")}
            with patch.dict("os.environ", env, clear=True):
                loaded = load_notification_config(cfg_file)
        assert loaded.macos.enabled is True
        assert loaded.bell.enabled is True
        assert loaded.bell.event_types == ["run_failed"]
        assert loaded.webhook.url == "https://hooks.example.com"
        assert loaded.webhook.headers == {"X-Token": "abc"}
        assert loaded.webhook.timeout == 5.0

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        nested = tmp_path / "a" / "b" / "notifications.json"
        save_notification_config(NotificationConfig(), nested)
        assert nested.exists()
