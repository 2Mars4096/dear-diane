from __future__ import annotations

import pytest

from dan.cli import process_utils
from dan.cli import up as up_module


def test_wait_for_health_allows_slightly_slow_startup(monkeypatch):
    """A server that becomes healthy just after 15s should still be accepted."""
    now = 0.0

    def fake_monotonic() -> float:
        return now

    def fake_sleep(delay: float) -> None:
        nonlocal now
        now += delay

    def fake_check_health(_port: int, timeout: float = 2.0) -> bool:
        return now >= 15.5

    monkeypatch.setattr(up_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(up_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(up_module, "check_health", fake_check_health)

    assert up_module.wait_for_health(8000) is True


def test_wait_for_health_returns_false_after_deadline(monkeypatch):
    """If health never comes up before the deadline, the launcher should fail."""
    now = 0.0

    def fake_monotonic() -> float:
        return now

    def fake_sleep(delay: float) -> None:
        nonlocal now
        now += delay

    monkeypatch.setattr(up_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(up_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(up_module, "check_health", lambda _port, timeout=2.0: False)

    assert up_module.wait_for_health(8000, max_wait=2.0, poll_interval=0.5) is False


def test_is_process_alive_treats_zombie_as_dead(monkeypatch):
    monkeypatch.setattr(process_utils.os, "kill", lambda _pid, _sig: None)
    monkeypatch.setattr(process_utils.os, "name", "posix", raising=False)
    monkeypatch.setattr(
        process_utils.subprocess,
        "check_output",
        lambda *_args, **_kwargs: "Z+\n",
    )

    assert up_module.is_process_alive(12345) is False


def test_find_running_server_reuses_healthy_port_without_pid_file(monkeypatch):
    monkeypatch.setattr(up_module, "read_pid_file", lambda: (None, None))
    monkeypatch.setattr(
        up_module,
        "get_health_payload",
        lambda _port, timeout=2.0: {"status": "ok", "pid": 24680},
    )

    assert up_module.find_running_server(8000) == (24680, 8000, False)


def test_main_reuses_healthy_server_without_pid_file(monkeypatch, capsys):
    monkeypatch.setattr(up_module, "acquire_start_lock", lambda: object())
    monkeypatch.setattr(up_module, "release_start_lock", lambda _handle: None)
    monkeypatch.setattr(up_module, "find_running_server", lambda _port: (24680, 8000, False))
    monkeypatch.setattr(
        up_module,
        "start_server",
        lambda _port: pytest.fail("start_server should not be called for a healthy existing server"),
    )
    captured: dict[str, str] = {}
    monkeypatch.setattr(up_module, "drop_into_chat", lambda server_url: captured.setdefault("server_url", server_url))
    monkeypatch.setattr(up_module.sys, "argv", ["dan-up"])

    up_module.main()

    assert captured["server_url"] == "http://127.0.0.1:8000"
    out = capsys.readouterr().out
    assert "reusing existing server" in out
