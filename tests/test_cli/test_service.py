"""Tests for dan-service CLI (Plan 26-2)."""
from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dan.cli.service import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    LAUNCHD_PLIST_TEMPLATE,
    SYSTEMD_UNIT_TEMPLATE,
    _check_health,
    _enforce_log_budget,
    _is_alive,
    _read_pid,
    _rotate_logs,
    build_parser,
    cmd_health,
    cmd_logs,
    cmd_status,
    generate_plist,
    generate_systemd_unit,
)


# ---------------------------------------------------------------------------
# generate_plist
# ---------------------------------------------------------------------------


class TestGeneratePlist:
    def test_contains_python_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path / "logs")
        monkeypatch.setattr("sys.executable", "/usr/bin/python3.12")
        content = generate_plist("127.0.0.1", 8000)
        assert "/usr/bin/python3.12" in content

    def test_contains_host_and_port(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path / "logs")
        content = generate_plist("0.0.0.0", 9999)
        assert "<string>0.0.0.0</string>" in content
        assert "<string>9999</string>" in content

    def test_contains_log_paths(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", logs)
        content = generate_plist("127.0.0.1", 8000)
        assert f"{logs}/server.stdout.log" in content
        assert f"{logs}/server.stderr.log" in content

    def test_creates_logs_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        logs = tmp_path / "deep" / "logs"
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", logs)
        generate_plist("127.0.0.1", 8000)
        assert logs.is_dir()

    def test_contains_label(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path / "logs")
        content = generate_plist("127.0.0.1", 8000)
        assert "com.dan.server" in content

    def test_contains_keep_alive(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path / "logs")
        content = generate_plist("127.0.0.1", 8000)
        assert "<key>KeepAlive</key>" in content

    def test_contains_working_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path / "logs")
        content = generate_plist("127.0.0.1", 8000)
        assert str(Path.cwd()) in content

    def test_valid_xml_structure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path / "logs")
        content = generate_plist("127.0.0.1", 8000)
        assert content.startswith("<?xml version=")
        assert content.strip().endswith("</plist>")


# ---------------------------------------------------------------------------
# generate_systemd_unit
# ---------------------------------------------------------------------------


class TestGenerateSystemdUnit:
    def test_contains_exec_start(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path / "logs")
        monkeypatch.setattr("sys.executable", "/usr/bin/python3")
        content = generate_systemd_unit("127.0.0.1", 8000)
        assert "ExecStart=/usr/bin/python3 -m dan.server --host 127.0.0.1 --port 8000 --no-reload" in content

    def test_contains_restart_policy(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path / "logs")
        content = generate_systemd_unit("127.0.0.1", 8000)
        assert "Restart=on-failure" in content
        assert "RestartSec=5" in content

    def test_contains_log_paths(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", logs)
        content = generate_systemd_unit("127.0.0.1", 8000)
        assert f"StandardOutput=append:{logs}/server.stdout.log" in content
        assert f"StandardError=append:{logs}/server.stderr.log" in content

    def test_contains_host_and_port(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path / "logs")
        content = generate_systemd_unit("0.0.0.0", 3000)
        assert "--host 0.0.0.0" in content
        assert "--port 3000" in content

    def test_creates_logs_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        logs = tmp_path / "nested" / "logs"
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", logs)
        generate_systemd_unit("127.0.0.1", 8000)
        assert logs.is_dir()

    def test_contains_wanted_by(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path / "logs")
        content = generate_systemd_unit("127.0.0.1", 8000)
        assert "WantedBy=default.target" in content

    def test_unit_sections(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path / "logs")
        content = generate_systemd_unit("127.0.0.1", 8000)
        assert "[Unit]" in content
        assert "[Service]" in content
        assert "[Install]" in content


# ---------------------------------------------------------------------------
# _rotate_logs
# ---------------------------------------------------------------------------


class TestRotateLogs:
    def test_creates_logs_dir_if_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        logs = tmp_path / "nonexistent"
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", logs)
        _rotate_logs()
        assert logs.is_dir()

    def test_no_op_when_no_logs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path)
        _rotate_logs()  # should not raise

    def test_rotates_stdout_log(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path)
        log = tmp_path / "server.stdout.log"
        log.write_text("current")
        _rotate_logs()
        assert not log.exists()
        assert (tmp_path / "server.stdout.log.1").read_text() == "current"

    def test_rotates_both_logs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path)
        (tmp_path / "server.stdout.log").write_text("out")
        (tmp_path / "server.stderr.log").write_text("err")
        _rotate_logs()
        assert (tmp_path / "server.stdout.log.1").read_text() == "out"
        assert (tmp_path / "server.stderr.log.1").read_text() == "err"

    def test_cascades_existing_rotations(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path)
        (tmp_path / "server.stdout.log").write_text("v3")
        (tmp_path / "server.stdout.log.1").write_text("v2")
        (tmp_path / "server.stdout.log.2").write_text("v1")
        _rotate_logs()
        assert (tmp_path / "server.stdout.log.1").read_text() == "v3"
        assert (tmp_path / "server.stdout.log.2").read_text() == "v2"
        assert (tmp_path / "server.stdout.log.3").read_text() == "v1"

    def test_caps_at_5_rotations(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path)
        (tmp_path / "server.stdout.log").write_text("newest")
        for i in range(1, 6):
            (tmp_path / f"server.stdout.log.{i}").write_text(f"v{i}")
        _rotate_logs()
        assert (tmp_path / "server.stdout.log.1").read_text() == "newest"
        assert (tmp_path / "server.stdout.log.5").read_text() == "v4"
        assert not (tmp_path / "server.stdout.log.6").exists()


# ---------------------------------------------------------------------------
# _enforce_log_budget
# ---------------------------------------------------------------------------


class TestEnforceLogBudget:
    def test_no_op_under_budget(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path)
        monkeypatch.setenv("DAN_LOG_MAX_SIZE", "1000")
        (tmp_path / "a.log").write_text("small")
        _enforce_log_budget()
        assert (tmp_path / "a.log").exists()

    def test_deletes_oldest_first(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path)
        monkeypatch.setenv("DAN_LOG_MAX_SIZE", "50")
        old = tmp_path / "old.log"
        old.write_text("x" * 40)
        import time
        time.sleep(0.05)
        new = tmp_path / "new.log"
        new.write_text("x" * 20)
        _enforce_log_budget()
        assert not old.exists()
        assert new.exists()

    def test_preserves_index_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path)
        monkeypatch.setenv("DAN_LOG_MAX_SIZE", "10")
        idx = tmp_path / "_index.json"
        idx.write_text("{}")
        big = tmp_path / "big.log"
        big.write_text("x" * 100)
        _enforce_log_budget()
        assert idx.exists()

    def test_no_op_when_dir_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path / "nope")
        _enforce_log_budget()  # should not raise


# ---------------------------------------------------------------------------
# _check_health
# ---------------------------------------------------------------------------


class TestCheckHealth:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_httpx = MagicMock()
        mock_httpx.get.return_value = mock_resp
        import sys
        monkeypatch.setitem(sys.modules, "httpx", mock_httpx)
        ok, msg = _check_health(8000)
        assert ok is True
        assert msg == "healthy"

    def test_non_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_httpx = MagicMock()
        mock_httpx.get.return_value = mock_resp
        import sys
        monkeypatch.setitem(sys.modules, "httpx", mock_httpx)
        ok, msg = _check_health(8000)
        assert ok is False
        assert "503" in msg

    def test_connection_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_httpx = MagicMock()
        mock_httpx.get.side_effect = ConnectionError("refused")
        import sys
        monkeypatch.setitem(sys.modules, "httpx", mock_httpx)
        ok, msg = _check_health(8000)
        assert ok is False
        assert "refused" in msg


# ---------------------------------------------------------------------------
# _read_pid
# ---------------------------------------------------------------------------


class TestReadPid:
    def test_valid_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pid_file = tmp_path / "server.pid"
        pid_file.write_text("12345\n8080\n")
        monkeypatch.setattr("dan.cli.service.PID_FILE", pid_file)
        pid, port = _read_pid()
        assert pid == 12345
        assert port == 8080

    def test_pid_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pid_file = tmp_path / "server.pid"
        pid_file.write_text("54321\n")
        monkeypatch.setattr("dan.cli.service.PID_FILE", pid_file)
        pid, port = _read_pid()
        assert pid == 54321
        assert port == DEFAULT_PORT

    def test_missing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.PID_FILE", tmp_path / "nope.pid")
        pid, port = _read_pid()
        assert pid is None
        assert port is None

    def test_corrupt_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pid_file = tmp_path / "server.pid"
        pid_file.write_text("not-a-number\n")
        monkeypatch.setattr("dan.cli.service.PID_FILE", pid_file)
        pid, port = _read_pid()
        assert pid is None
        assert port is None

    def test_empty_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pid_file = tmp_path / "server.pid"
        pid_file.write_text("")
        monkeypatch.setattr("dan.cli.service.PID_FILE", pid_file)
        pid, port = _read_pid()
        assert pid is None
        assert port is None


# ---------------------------------------------------------------------------
# _is_alive
# ---------------------------------------------------------------------------


class TestIsAlive:
    def test_current_process(self) -> None:
        assert _is_alive(os.getpid()) is True

    def test_nonexistent_pid(self) -> None:
        assert _is_alive(999999999) is False


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------


class TestCmdStatus:
    def test_stopped_no_pid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        monkeypatch.setattr("dan.cli.service.PID_FILE", tmp_path / "nope.pid")
        monkeypatch.setattr("dan.cli._try_import_rich", lambda: (None, None))
        args = MagicMock()
        cmd_status(args)
        out = capsys.readouterr().out
        assert "stopped" in out

    def test_running_with_health(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        pid_file = tmp_path / "server.pid"
        pid_file.write_text(f"{os.getpid()}\n8000\n")
        monkeypatch.setattr("dan.cli.service.PID_FILE", pid_file)
        monkeypatch.setattr("dan.cli.service._check_health", lambda p: (True, "healthy"))
        monkeypatch.setattr("dan.cli._try_import_rich", lambda: (None, None))
        args = MagicMock()
        cmd_status(args)
        out = capsys.readouterr().out
        assert "running" in out
        assert "healthy" in out


# ---------------------------------------------------------------------------
# cmd_health
# ---------------------------------------------------------------------------


class TestCmdHealth:
    def test_healthy(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        monkeypatch.setattr("dan.cli.service.PID_FILE", tmp_path / "nope.pid")
        monkeypatch.setattr("dan.cli.service._check_health", lambda p: (True, "healthy"))
        args = MagicMock()
        cmd_health(args)
        out = capsys.readouterr().out
        assert "OK" in out

    def test_unhealthy_exits_1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("dan.cli.service.PID_FILE", tmp_path / "nope.pid")
        monkeypatch.setattr("dan.cli.service._check_health", lambda p: (False, "connection refused"))
        args = MagicMock()
        with pytest.raises(SystemExit) as exc_info:
            cmd_health(args)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# cmd_logs
# ---------------------------------------------------------------------------


class TestCmdLogs:
    def test_no_logs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path / "empty")
        args = MagicMock(follow=False, lines=50)
        cmd_logs(args)
        out = capsys.readouterr().out
        assert "No logs found" in out

    def test_shows_last_n_lines(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", tmp_path)
        log = tmp_path / "server.stdout.log"
        log.write_text("\n".join(f"line{i}" for i in range(100)))
        args = MagicMock(follow=False, lines=5)
        cmd_logs(args)
        out = capsys.readouterr().out
        assert "line95" in out
        assert "line99" in out
        assert "line0" not in out


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_all_subcommands_registered(self) -> None:
        parser = build_parser()
        expected = {"install", "uninstall", "start", "stop", "status", "health", "logs"}
        # Parse each subcommand to verify it's registered
        for cmd in expected:
            args = parser.parse_args([cmd])
            assert args.command == cmd

    def test_install_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["install"])
        assert args.host == DEFAULT_HOST
        assert args.port == DEFAULT_PORT

    def test_install_custom_port(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["install", "--port", "9090", "--host", "0.0.0.0"])
        assert args.port == 9090
        assert args.host == "0.0.0.0"

    def test_logs_follow_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["logs", "-f"])
        assert args.follow is True

    def test_logs_lines_default(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["logs"])
        assert args.lines == 50

    def test_logs_custom_lines(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["logs", "-n", "100"])
        assert args.lines == 100

    def test_no_command_prints_help(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------


class TestMain:
    def test_no_args_exits_1(self) -> None:
        from dan.cli.service import main

        with patch("sys.argv", ["dan-service"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# service_runner
# ---------------------------------------------------------------------------


class TestServiceRunner:
    def test_run_server_writes_and_cleans_pid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pid_file = tmp_path / "server.pid"
        logs_dir = tmp_path / "logs"
        monkeypatch.setattr("dan.cli.service.PID_FILE", pid_file)
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", logs_dir)

        mock_uvicorn = MagicMock()
        import sys
        monkeypatch.setitem(sys.modules, "uvicorn", mock_uvicorn)

        import dan.cli.service_runner as runner

        runner.run_server(host="127.0.0.1", port=9000)

        mock_uvicorn.run.assert_called_once()
        call_args = mock_uvicorn.run.call_args
        assert call_args[0][0] == "dan.server.app:app"
        assert call_args[1]["host"] == "127.0.0.1"
        assert call_args[1]["port"] == 9000
        assert call_args[1]["reload"] is False
        assert not pid_file.exists()

    def test_pid_file_cleaned_on_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pid_file = tmp_path / "server.pid"
        logs_dir = tmp_path / "logs"
        monkeypatch.setattr("dan.cli.service.PID_FILE", pid_file)
        monkeypatch.setattr("dan.cli.service.LOGS_DIR", logs_dir)

        mock_uvicorn = MagicMock()
        mock_uvicorn.run.side_effect = RuntimeError("port in use")
        import sys
        monkeypatch.setitem(sys.modules, "uvicorn", mock_uvicorn)

        import dan.cli.service_runner as runner
        with pytest.raises(RuntimeError, match="port in use"):
            runner.run_server()
        assert not pid_file.exists()
