from __future__ import annotations

from diane.cli import down as down_module
from diane.cli import process_utils


def test_down_removes_stale_zombie_pid_file(
    monkeypatch,
    tmp_path,
    capsys,
):
    pid_file = tmp_path / "server.pid"
    pid_file.write_text("12345\n8000\n")

    monkeypatch.setattr(down_module, "PID_FILE", pid_file)
    monkeypatch.setattr(process_utils.os, "kill", lambda _pid, _sig: None)
    monkeypatch.setattr(process_utils.os, "name", "posix", raising=False)
    monkeypatch.setattr(
        process_utils.subprocess,
        "check_output",
        lambda *_args, **_kwargs: "Z+\n",
    )

    down_module.main()

    captured = capsys.readouterr()
    assert "stale PID file" in captured.out
    assert not pid_file.exists()


def test_down_reports_healthy_unmanaged_server_when_pid_missing(monkeypatch, capsys):
    monkeypatch.setattr(down_module, "PID_FILE", down_module.PID_FILE.parent / "missing.pid")
    monkeypatch.setattr(down_module, "check_health", lambda _port: True)

    down_module.main()

    captured = capsys.readouterr()
    assert "owned by Dear Diane Desktop or dan-service" in captured.out
