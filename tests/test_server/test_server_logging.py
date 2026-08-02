"""Regression tests for server logging configuration."""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

import dan.cli.service as service_module
import dan.cli.service_runner as service_runner_module
import dan.server.__main__ as server_main_module


def test_server_main_passes_log_config_to_uvicorn(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(server_main_module.uvicorn, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["dan-serve", "--no-reload"])

    server_main_module.main()

    assert "log_config" in captured
    log_config = captured["log_config"]
    assert isinstance(log_config, dict)
    assert log_config["root"]["level"] == "INFO"
    assert log_config["loggers"]["dan"]["level"] == "INFO"


def test_service_runner_passes_log_config_to_uvicorn(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(service_module, "_rotate_logs", lambda: None)
    monkeypatch.setattr(service_module, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(service_module, "PID_FILE", tmp_path / "server.pid")

    service_runner_module.run_server(port=8123)

    assert "log_config" in captured
    log_config = captured["log_config"]
    assert isinstance(log_config, dict)
    assert log_config["root"]["level"] == "INFO"
    assert log_config["loggers"]["dan"]["level"] == "INFO"
