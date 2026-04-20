"""Tests for the ``dan-bot`` CLI."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

from dan.cli import bot as bot_cli


def test_create_command_saves_config(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    config_path = tmp_path / "telegram-config.json"
    answers = iter(
        [
            "123:abc-token",
            "Research specialist",
            "literature-review,paper-writing",
            "y",
        ],
    )

    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    monkeypatch.setattr(
        bot_cli,
        "_verify_token",
        lambda token: {
            "username": "research_bot",
            "first_name": "Research Bot",
            "can_join_groups": True,
            "can_read_all_group_messages": False,
        },
    )

    bot_cli._cmd_create(
        argparse.Namespace(name="research-bot", config=str(config_path)),
    )

    output = capsys.readouterr().out
    assert "Saved to" in output
    assert "Privacy mode is ON" in output

    raw = config_path.read_text()
    assert "research-bot" in raw
    assert "literature-review" in raw


def test_start_daemon_writes_pid_file(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    pid_file = tmp_path / "fleet.pid"
    log_file = tmp_path / "telegram-fleet.log"

    monkeypatch.setattr(bot_cli, "_FLEET_PID_FILE", pid_file)
    monkeypatch.setattr(bot_cli, "_FLEET_LOG_FILE", log_file)
    monkeypatch.setattr(bot_cli, "_TELEGRAM_DIR", tmp_path)

    class _FakeProc:
        pid = 43210

    monkeypatch.setattr(
        bot_cli.subprocess,
        "Popen",
        lambda *args, **kwargs: _FakeProc(),
    )

    bot_cli._start_daemon(
        argparse.Namespace(config=None, server=None, daemon=True),
    )

    assert pid_file.read_text() == "43210"
    output = capsys.readouterr().out
    assert "Fleet daemon started" in output


def test_create_new_default_clears_previous_default(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "telegram-config.json"
    config_path.write_text(
        (
            '{"_comment":"x","bots":{"old-bot":{"token":"old","personality":"","projects":[],"default":true}},'
            '"groups":{},"settings":{"streaming_edits":true,"use_reactions":true,"auto_pin_deliverables":false,'
            '"progress_throttle":5.0,"max_inbound_media_mb":20.0}}'
        ),
    )
    answers = iter(
        [
            "123:new-token",
            "",
            "",
            "y",
        ],
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    monkeypatch.setattr(
        bot_cli,
        "_verify_token",
        lambda token: {
            "username": "new_bot",
            "first_name": "New Bot",
            "can_join_groups": True,
            "can_read_all_group_messages": True,
        },
    )

    bot_cli._cmd_create(
        argparse.Namespace(name="new-bot", config=str(config_path)),
    )

    raw = config_path.read_text()
    assert '"old-bot"' in raw
    assert raw.count('"default": true') == 1


def test_stop_named_bot_writes_control_file(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    pid_file = tmp_path / "fleet.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(bot_cli, "_FLEET_PID_FILE", pid_file)
    monkeypatch.setattr(bot_cli, "_TELEGRAM_DIR", tmp_path)
    monkeypatch.setattr(bot_cli, "_fleet_daemon_running", lambda: True)

    bot_cli._cmd_stop(argparse.Namespace(name="research-bot", config=None))

    ctl_file = tmp_path / "fleet.ctl"
    assert ctl_file.exists()
    assert ctl_file.read_text() == "stop:research-bot"

    output = capsys.readouterr().out
    assert "stop signal" in output.lower()
    assert "research-bot" in output


def test_stop_named_bot_requires_daemon(capsys) -> None:
    bot_cli._cmd_stop(argparse.Namespace(name="research-bot", config=None))
    output = capsys.readouterr().out
    assert "No fleet daemon running" in output
