from __future__ import annotations

from dan.cli import up as up_module


def test_resolve_bind_host_auto_binds_all_when_phone_present(monkeypatch) -> None:
    monkeypatch.delenv("DAN_UP_HOST", raising=False)
    monkeypatch.setenv("DAN_PHONE_WIREGUARD_HOST", "10.77.77.2")
    monkeypatch.setattr(up_module, "_host_has_interface_address", lambda host: host == "10.77.77.2")

    assert up_module.resolve_bind_host(None) == ("0.0.0.0", "desktop-phone-auto")
    assert up_module._server_url("0.0.0.0", 8000) == "http://127.0.0.1:8000"


def test_resolve_bind_host_phone_alias_uses_phone_env(monkeypatch) -> None:
    monkeypatch.setenv("DAN_PHONE_WIREGUARD_HOST", "10.77.77.9")

    assert up_module.resolve_bind_host("phone") == ("10.77.77.9", "phone")


def test_find_running_server_reuses_healthy_port_without_pid_file(monkeypatch) -> None:
    monkeypatch.setattr(up_module, "read_pid_file", lambda: (None, None))
    monkeypatch.setattr(
        up_module,
        "get_health_payload",
        lambda _port, timeout=2.0, host="127.0.0.1": {"status": "ok", "pid": 24680},
    )

    assert up_module.find_running_server(8000) == (24680, 8000, False)


def test_start_server_launches_minimal_server(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 13579

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(up_module, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(up_module.subprocess, "Popen", fake_popen)

    assert up_module.start_server(8010, host="127.0.0.1") == 13579
    assert captured["command"] == [
        up_module.sys.executable,
        "-m",
        "dan.server",
        "--host",
        "127.0.0.1",
        "--port",
        "8010",
        "--no-reload",
    ]


def test_main_reuses_healthy_server(monkeypatch, capsys) -> None:
    monkeypatch.setattr(up_module, "acquire_start_lock", lambda: object())
    monkeypatch.setattr(up_module, "release_start_lock", lambda _handle: None)
    monkeypatch.setattr(up_module, "resolve_bind_host", lambda _value=None: ("127.0.0.1", "local-auto"))
    monkeypatch.setattr(up_module, "find_running_server", lambda _port, host="127.0.0.1": (24680, 8000, False))
    monkeypatch.setattr(up_module.sys, "argv", ["dan-up"])

    up_module.main()

    assert "already running" in capsys.readouterr().out


def test_main_starts_and_waits_for_minimal_server(monkeypatch, capsys) -> None:
    monkeypatch.setattr(up_module, "acquire_start_lock", lambda: object())
    monkeypatch.setattr(up_module, "release_start_lock", lambda _handle: None)
    monkeypatch.setattr(up_module, "resolve_bind_host", lambda _value=None: ("127.0.0.1", "local-auto"))
    monkeypatch.setattr(up_module, "find_running_server", lambda _port, host="127.0.0.1": None)
    monkeypatch.setattr(up_module, "start_server", lambda _port, host="127.0.0.1": 13579)
    monkeypatch.setattr(up_module, "write_pid_file", lambda _pid, _port: None)
    monkeypatch.setattr(up_module, "wait_for_health", lambda _port, host="127.0.0.1": True)
    monkeypatch.setattr(up_module.sys, "argv", ["dan-up"])

    up_module.main()

    assert "server ready" in capsys.readouterr().out.lower()
