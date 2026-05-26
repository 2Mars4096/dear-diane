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

    def fake_check_health(_port: int, timeout: float = 2.0, *, host: str = "127.0.0.1") -> bool:
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
    monkeypatch.setattr(
        up_module,
        "check_health",
        lambda _port, timeout=2.0, host="127.0.0.1": False,
    )

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
        lambda _port, timeout=2.0, host="127.0.0.1": {"status": "ok", "pid": 24680},
    )

    assert up_module.find_running_server(8000) == (24680, 8000, False)


def test_resolve_bind_host_auto_binds_all_when_phone_present(monkeypatch):
    monkeypatch.delenv("DAN_UP_HOST", raising=False)
    monkeypatch.setenv("DAN_PHONE_WIREGUARD_HOST", "10.77.77.2")
    monkeypatch.setattr(up_module, "_host_has_interface_address", lambda host: host == "10.77.77.2")

    assert up_module.resolve_bind_host(None) == ("0.0.0.0", "desktop-phone-auto")


def test_server_url_uses_loopback_for_all_interfaces():
    assert up_module._server_url("0.0.0.0", 8000) == "http://127.0.0.1:8000"


def test_resolve_bind_host_auto_falls_back_to_local(monkeypatch):
    monkeypatch.delenv("DAN_UP_HOST", raising=False)
    monkeypatch.setattr(up_module, "_host_has_interface_address", lambda _host: False)

    assert up_module.resolve_bind_host(None) == ("127.0.0.1", "local-auto")


def test_resolve_bind_host_phone_alias_uses_phone_env(monkeypatch):
    monkeypatch.setenv("DAN_PHONE_WIREGUARD_HOST", "10.77.77.9")

    assert up_module.resolve_bind_host("phone") == ("10.77.77.9", "phone")


def test_main_reuses_healthy_server_without_pid_file(monkeypatch, capsys):
    monkeypatch.setattr(up_module, "acquire_start_lock", lambda: object())
    monkeypatch.setattr(up_module, "release_start_lock", lambda _handle: None)
    monkeypatch.setattr(up_module, "resolve_bind_host", lambda _value=None: ("127.0.0.1", "local-auto"))
    monkeypatch.setattr(up_module, "find_running_server", lambda _port, host="127.0.0.1": (24680, 8000, False))
    monkeypatch.setattr(
        up_module,
        "start_server",
        lambda _port: pytest.fail("start_server should not be called for a healthy existing server"),
    )
    captured: dict[str, str] = {}
    monkeypatch.setattr(up_module, "drop_into_chat", lambda server_url: captured.setdefault("server_url", server_url))
    monkeypatch.setattr(
        up_module,
        "ensure_telegram_adapter",
        lambda server_url, **_kwargs: captured.setdefault("telegram_url", server_url),
    )
    monkeypatch.setattr(up_module.sys, "argv", ["dan-up"])

    up_module.main()

    assert captured["server_url"] == "http://127.0.0.1:8000"
    assert captured["telegram_url"] == "http://127.0.0.1:8000"
    out = capsys.readouterr().out
    assert "reusing existing server" in out


def test_main_starts_phone_host_when_explicit(monkeypatch, capsys):
    monkeypatch.setattr(up_module, "acquire_start_lock", lambda: object())
    monkeypatch.setattr(up_module, "release_start_lock", lambda _handle: None)
    captured: dict[str, object] = {}

    def fake_resolve_bind_host(value=None):
        captured["host_arg"] = value
        return "10.77.77.2", "phone"

    monkeypatch.setattr(up_module, "resolve_bind_host", fake_resolve_bind_host)
    monkeypatch.setattr(up_module, "find_running_server", lambda _port, host="127.0.0.1": None)

    def fake_start_server(port, **kwargs):
        captured["start"] = (port, kwargs)
        return 13579

    monkeypatch.setattr(up_module, "start_server", fake_start_server)
    monkeypatch.setattr(up_module, "write_pid_file", lambda pid, port: None)
    monkeypatch.setattr(up_module, "wait_for_health", lambda port, **kwargs: True)
    monkeypatch.setattr(up_module, "ensure_telegram_adapter", lambda server_url: captured.setdefault("telegram_url", server_url))
    monkeypatch.setattr(up_module, "drop_into_chat", lambda server_url: captured.setdefault("chat_url", server_url))
    monkeypatch.setattr(up_module.sys, "argv", ["dan-up", "--host", "phone"])

    up_module.main()

    assert captured["host_arg"] == "phone"
    assert captured["start"] == (
        8000,
        {
            "host": "10.77.77.2",
            "disable_adapter_autostart": False,
            "disable_telegram_adapter_autostart": True,
        },
    )
    assert captured["telegram_url"] == "http://10.77.77.2:8000"
    assert captured["chat_url"] == "http://10.77.77.2:8000"
    assert "phone" in capsys.readouterr().out


def test_start_server_can_disable_adapter_autostart(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class _FakeProc:
        pid = 13579

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return _FakeProc()

    monkeypatch.setattr(up_module, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(up_module.subprocess, "Popen", fake_popen)

    assert up_module.start_server(8001, disable_adapter_autostart=True) == 13579
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["DAN_DISABLE_ADAPTER_AUTOSTART"] == "1"
    assert captured["cmd"][-3:] == ["--port", "8001", "--no-reload"]


def test_start_server_can_disable_telegram_adapter_autostart(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class _FakeProc:
        pid = 13579

    def fake_popen(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return _FakeProc()

    monkeypatch.setattr(up_module, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(up_module.subprocess, "Popen", fake_popen)

    assert up_module.start_server(8001, disable_telegram_adapter_autostart=True) == 13579
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["DAN_DISABLE_TELEGRAM_ADAPTER_AUTOSTART"] == "1"


def test_ensure_telegram_fleet_starts_daemon(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"bots":{"chief":{"token":"123:abc"}},"groups":{},"settings":{}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(up_module, "TELEGRAM_DIR", tmp_path)
    monkeypatch.setattr(up_module, "TELEGRAM_FLEET_PID_FILE", tmp_path / "fleet.pid")
    monkeypatch.setattr(up_module, "TELEGRAM_FLEET_LOG_FILE", tmp_path / "fleet.log")
    monkeypatch.setattr(up_module, "_stop_backend_telegram_adapters", lambda _url: 0)

    class _FakeProc:
        pid = 24680

    captured: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return _FakeProc()

    monkeypatch.setattr(up_module.subprocess, "Popen", fake_popen)

    up_module.ensure_telegram_fleet(
        "http://127.0.0.1:8000",
        mode="auto",
        config_path=str(config_path),
    )

    assert (tmp_path / "fleet.pid").read_text(encoding="utf-8") == "24680"
    assert captured["cmd"] == [
        up_module.sys.executable,
        "-m",
        "dan.cli.bot",
        "--config",
        str(config_path),
        "start-all",
        "--server",
        "http://127.0.0.1:8000",
    ]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["DAN_BOT_DAEMON_CHILD"] == "1"
    assert "Telegram fleet started" in capsys.readouterr().out


def test_ensure_telegram_adapter_starts_backend_adapter(monkeypatch, tmp_path, capsys):
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    monkeypatch.setattr(up_module, "TELEGRAM_FLEET_PID_FILE", tmp_path / "not-running.pid")

    class _Response:
        def __init__(self, status_code: int, payload: object, text: str = "") -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = text

        def json(self) -> object:
            return self._payload

    class _Httpx:
        @staticmethod
        def get(url: str, timeout: float) -> _Response:
            calls.append(("GET", url, None))
            return _Response(200, [])

        @staticmethod
        def post(url: str, json: dict[str, object], timeout: float) -> _Response:
            calls.append(("POST", url, json))
            return _Response(200, {"adapter_id": "abc123"})

    monkeypatch.setitem(up_module.sys.modules, "httpx", _Httpx)

    up_module.ensure_telegram_adapter("http://127.0.0.1:8000")

    assert calls == [
        ("GET", "http://127.0.0.1:8000/api/adapters/status", None),
        ("POST", "http://127.0.0.1:8000/api/adapters/start", {"type": "telegram", "config": {}}),
    ]
    assert "Telegram adapter started" in capsys.readouterr().out


def test_ensure_telegram_adapter_stops_fleet_first(monkeypatch, tmp_path, capsys):
    pid_file = tmp_path / "fleet.pid"
    pid_file.write_text("24680", encoding="utf-8")
    monkeypatch.setattr(up_module, "TELEGRAM_FLEET_PID_FILE", pid_file)
    alive = {"value": True}
    killed: list[tuple[int, object]] = []

    def fake_is_alive(pid: int) -> bool:
        return alive["value"]

    def fake_kill(pid: int, sig) -> None:
        killed.append((pid, sig))
        alive["value"] = False

    class _Response:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class _Httpx:
        @staticmethod
        def get(url: str, timeout: float) -> _Response:
            return _Response([])

        @staticmethod
        def post(url: str, json: dict[str, object], timeout: float) -> _Response:
            return _Response({"adapter_id": "abc123"})

    monkeypatch.setattr(up_module, "is_process_alive", fake_is_alive)
    monkeypatch.setattr(up_module.os, "kill", fake_kill)
    monkeypatch.setitem(up_module.sys.modules, "httpx", _Httpx)

    up_module.ensure_telegram_adapter("http://127.0.0.1:8000")

    assert killed == [(24680, up_module.signal.SIGTERM)]
    assert "Stopped Telegram fleet" in capsys.readouterr().out
