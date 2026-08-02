"""Unit tests for shipped built-in tools in dan.tools.

Covers: current_datetime, csv_read, python_eval, clipboard, file_move,
file_copy, file_delete, git_status, git_diff, git_log, compress,
text_diff, send_email, notify.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from unittest.mock import patch
from datetime import datetime

import pytest

# ---------------------------------------------------------------------------
# Fixture: workspace root scoped to tmp_path for file-sandboxed tools
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Create an isolated workspace and point DAN_WORKSPACE_ROOT at it."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(ws))
    return ws


# ---------------------------------------------------------------------------
# 8-2: current_datetime
# ---------------------------------------------------------------------------


class TestCurrentDatetime:
    @pytest.mark.asyncio
    async def test_returns_valid_iso(self):
        from dan.tools.current_datetime import current_datetime

        result = await current_datetime()
        assert "datetime" in result
        assert "date" in result
        assert "time" in result
        assert "day_of_week" in result
        assert "timezone" in result
        assert "unix_timestamp" in result
        datetime.fromisoformat(result["datetime"])

    @pytest.mark.asyncio
    async def test_with_explicit_timezone(self):
        from dan.tools.current_datetime import current_datetime

        result = await current_datetime(timezone="UTC")
        assert result["timezone"] == "UTC"
        assert result["datetime"].endswith("+00:00")

    @pytest.mark.asyncio
    async def test_invalid_timezone_raises(self):
        from dan.tools.current_datetime import current_datetime

        with pytest.raises(ValueError, match="Unknown timezone"):
            await current_datetime(timezone="Mars/Olympus_Mons")


# ---------------------------------------------------------------------------
# 8-3: csv_read
# ---------------------------------------------------------------------------


class TestCsvRead:
    @pytest.mark.asyncio
    async def test_basic(self, workspace):
        from dan.tools.csv_read import csv_read

        f = workspace / "data.csv"
        f.write_text("name,age\nAlice,30\nBob,25\n")
        result = await csv_read(path=str(f))
        assert result["headers"] == ["name", "age"]
        assert result["row_count"] == 2
        assert result["column_count"] == 2
        assert result["rows"][0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_tab_delimited(self, workspace):
        from dan.tools.csv_read import csv_read

        f = workspace / "data.tsv"
        f.write_text("name\tage\nAlice\t30\n")
        result = await csv_read(path=str(f), delimiter="\t")
        assert result["headers"] == ["name", "age"]
        assert result["row_count"] == 1

    @pytest.mark.asyncio
    async def test_max_rows_truncation(self, workspace):
        from dan.tools.csv_read import csv_read

        f = workspace / "big.csv"
        lines = ["id,val"] + [f"{i},{i * 10}" for i in range(100)]
        f.write_text("\n".join(lines))
        result = await csv_read(path=str(f), max_rows=5)
        assert result["row_count"] == 5
        assert result["truncated"] is True
        assert result["total_rows"] == 100

    @pytest.mark.asyncio
    async def test_column_selection(self, workspace):
        from dan.tools.csv_read import csv_read

        f = workspace / "cols.csv"
        f.write_text("a,b,c\n1,2,3\n4,5,6\n")
        result = await csv_read(path=str(f), columns=["a", "c"])
        assert result["headers"] == ["a", "c"]
        assert result["rows"][0] == {"a": "1", "c": "3"}

    @pytest.mark.asyncio
    async def test_missing_file_raises(self, workspace):
        from dan.tools.csv_read import csv_read

        with pytest.raises(FileNotFoundError):
            await csv_read(path=str(workspace / "nope.csv"))

    @pytest.mark.asyncio
    async def test_utf8_bom(self, workspace):
        from dan.tools.csv_read import csv_read

        f = workspace / "bom.csv"
        f.write_bytes(b"\xef\xbb\xbfname,age\nAlice,30\n")
        result = await csv_read(path=str(f))
        assert result["headers"] == ["name", "age"]


# ---------------------------------------------------------------------------
# 8-4: python_eval
# ---------------------------------------------------------------------------


class TestPythonEval:
    @pytest.mark.asyncio
    async def test_basic_result(self):
        from dan.tools.python_eval import python_eval

        result = await python_eval(code="result = 2 + 3")
        assert result["result"] == 5

    @pytest.mark.asyncio
    async def test_print_captured(self):
        from dan.tools.python_eval import python_eval

        result = await python_eval(code="print('hello')\nresult = 42")
        assert result["result"] == 42
        assert "hello" in result["stdout"]

    @pytest.mark.asyncio
    async def test_returns_dict_with_expected_keys(self):
        from dan.tools.python_eval import python_eval

        result = await python_eval(code="result = 1")
        assert isinstance(result, dict)
        assert "result" in result
        assert "stdout" in result
        assert "stderr" in result

    @pytest.mark.asyncio
    async def test_syntax_error(self):
        from dan.tools.python_eval import python_eval

        result = await python_eval(code="def :")
        assert result.get("error") or result.get("stderr")

    @pytest.mark.asyncio
    async def test_timeout_enforcement(self):
        from dan.tools.python_eval import python_eval

        result = await python_eval(code="import time; time.sleep(60)", timeout=2)
        assert result.get("error") or "timed out" in (result.get("stderr") or "").lower()


# ---------------------------------------------------------------------------
# 8-1: file_copy, file_move, file_delete
# ---------------------------------------------------------------------------


class TestFileCopy:
    @pytest.mark.asyncio
    async def test_copy_file(self, workspace):
        from dan.tools.file_copy import file_copy

        src = workspace / "src.txt"
        src.write_text("hello")
        dst = workspace / "dst.txt"
        result = await file_copy(source=str(src), destination=str(dst))
        assert result["copied"] is True
        assert dst.read_text() == "hello"
        assert src.exists()

    @pytest.mark.asyncio
    async def test_copy_requires_recursive_for_dir(self, workspace):
        from dan.tools.file_copy import file_copy

        d = workspace / "mydir"
        d.mkdir()
        (d / "a.txt").write_text("a")
        with pytest.raises(ValueError, match="recursive"):
            await file_copy(source=str(d), destination=str(workspace / "copy_dir"))

    @pytest.mark.asyncio
    async def test_copy_dir_recursive(self, workspace):
        from dan.tools.file_copy import file_copy

        d = workspace / "mydir"
        d.mkdir()
        (d / "a.txt").write_text("a")
        result = await file_copy(
            source=str(d), destination=str(workspace / "copy_dir"), recursive=True
        )
        assert result["copied"] is True
        assert (workspace / "copy_dir" / "a.txt").read_text() == "a"

    @pytest.mark.asyncio
    async def test_copy_missing_source_raises(self, workspace):
        from dan.tools.file_copy import file_copy

        with pytest.raises(FileNotFoundError):
            await file_copy(
                source=str(workspace / "ghost.txt"),
                destination=str(workspace / "dst.txt"),
            )


class TestFileMove:
    @pytest.mark.asyncio
    async def test_move_file(self, workspace):
        from dan.tools.file_move import file_move

        src = workspace / "src.txt"
        src.write_text("hello")
        dst = workspace / "moved.txt"
        result = await file_move(source=str(src), destination=str(dst))
        assert result["moved"] is True
        assert not src.exists()
        assert dst.read_text() == "hello"

    @pytest.mark.asyncio
    async def test_move_missing_source_raises(self, workspace):
        from dan.tools.file_move import file_move

        with pytest.raises(FileNotFoundError):
            await file_move(
                source=str(workspace / "ghost.txt"),
                destination=str(workspace / "dst.txt"),
            )


class TestFileDelete:
    @pytest.mark.asyncio
    async def test_delete_file(self, workspace):
        from dan.tools.file_delete import file_delete

        f = workspace / "delete_me.txt"
        f.write_text("bye")
        result = await file_delete(path=str(f))
        assert result["deleted"] is True
        assert not f.exists()

    @pytest.mark.asyncio
    async def test_delete_empty_dir(self, workspace):
        from dan.tools.file_delete import file_delete

        d = workspace / "empty_dir"
        d.mkdir()
        result = await file_delete(path=str(d))
        assert result["deleted"] is True
        assert not d.exists()

    @pytest.mark.asyncio
    async def test_delete_nonempty_dir_fails_without_recursive(self, workspace):
        from dan.tools.file_delete import file_delete

        d = workspace / "nonempty"
        d.mkdir()
        (d / "file.txt").write_text("x")
        with pytest.raises(ValueError, match="recursive"):
            await file_delete(path=str(d))

    @pytest.mark.asyncio
    async def test_delete_nonempty_dir_recursive(self, workspace):
        from dan.tools.file_delete import file_delete

        d = workspace / "nonempty"
        d.mkdir()
        (d / "file.txt").write_text("x")
        result = await file_delete(path=str(d), recursive=True)
        assert result["deleted"] is True
        assert not d.exists()

    @pytest.mark.asyncio
    async def test_delete_missing_path_raises(self, workspace):
        from dan.tools.file_delete import file_delete

        with pytest.raises(FileNotFoundError):
            await file_delete(path=str(workspace / "nope.txt"))


# ---------------------------------------------------------------------------
# 8-6: workspace sandbox enforcement
# ---------------------------------------------------------------------------


class TestWorkspaceSandbox:
    """validate_path allows explicit absolute paths (even outside workspace).
    Only *relative* paths are sandboxed.  These tests verify that behaviour.
    """

    @pytest.mark.asyncio
    async def test_relative_path_traversal_rejected(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "ok.txt").write_text("safe")
        monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(ws))

        from dan.tools._workspace import validate_path

        with pytest.raises(ValueError, match="outside the workspace"):
            validate_path("../../etc/passwd")

    @pytest.mark.asyncio
    async def test_absolute_path_outside_workspace_allowed(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(ws))

        from dan.tools._workspace import validate_path

        outside = tmp_path / "outside.txt"
        outside.write_text("data")
        resolved = validate_path(str(outside))
        assert resolved == str(outside.resolve())

    @pytest.mark.asyncio
    async def test_file_delete_works_inside_workspace(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(ws))

        from dan.tools.file_delete import file_delete

        target = ws / "deleteme.txt"
        target.write_text("bye")
        await file_delete(path="deleteme.txt")
        assert not target.exists()

    @pytest.mark.asyncio
    async def test_csv_read_works_inside_workspace(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(ws))

        from dan.tools.csv_read import csv_read

        csv_file = ws / "data.csv"
        csv_file.write_text("a,b\n1,2\n")
        result = await csv_read(path="data.csv")
        assert result["row_count"] >= 1


# ---------------------------------------------------------------------------
# 8-7: git_status, git_diff, git_log
# ---------------------------------------------------------------------------


def _init_git_repo(path):
    """Initialize a git repo with one commit at *path*."""
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(path), capture_output=True, check=True,
    )
    (path / "file.txt").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(path), capture_output=True, check=True,
    )


class TestGitStatus:
    @pytest.mark.asyncio
    async def test_clean_repo(self, tmp_path):
        _init_git_repo(tmp_path)

        from dan.tools.git_status import git_status

        result = await git_status(path=str(tmp_path))
        assert result["branch"] in ("main", "master")
        assert result["modified"] == []
        assert result["staged"] == []
        assert result["untracked"] == []

    @pytest.mark.asyncio
    async def test_modified_file_detected(self, tmp_path):
        _init_git_repo(tmp_path)
        (tmp_path / "file.txt").write_text("changed")

        from dan.tools.git_status import git_status

        result = await git_status(path=str(tmp_path))
        assert "file.txt" in result["modified"]

    @pytest.mark.asyncio
    async def test_untracked_file_detected(self, tmp_path):
        _init_git_repo(tmp_path)
        (tmp_path / "new.txt").write_text("new")

        from dan.tools.git_status import git_status

        result = await git_status(path=str(tmp_path))
        assert "new.txt" in result["untracked"]

    @pytest.mark.asyncio
    async def test_not_a_repo_raises(self, tmp_path):
        from dan.tools.git_status import git_status

        with pytest.raises(FileNotFoundError, match="Not inside a git repository"):
            await git_status(path=str(tmp_path))


class TestGitDiff:
    @pytest.mark.asyncio
    async def test_diff_working_tree(self, tmp_path):
        _init_git_repo(tmp_path)
        (tmp_path / "file.txt").write_text("changed content")

        from dan.tools.git_diff import git_diff

        result = await git_diff(path=str(tmp_path))
        assert "changed content" in result["diff_text"]
        assert result["files_changed"] >= 1

    @pytest.mark.asyncio
    async def test_diff_no_changes(self, tmp_path):
        _init_git_repo(tmp_path)

        from dan.tools.git_diff import git_diff

        result = await git_diff(path=str(tmp_path))
        assert result["diff_text"] == ""
        assert result["files_changed"] == 0


class TestGitLog:
    @pytest.mark.asyncio
    async def test_log_returns_commits(self, tmp_path):
        _init_git_repo(tmp_path)

        from dan.tools.git_log import git_log

        result = await git_log(path=str(tmp_path))
        assert len(result["commits"]) == 1
        assert result["commits"][0]["message"] == "init"
        assert result["commits"][0]["author"] == "Test"

    @pytest.mark.asyncio
    async def test_log_limit(self, tmp_path):
        _init_git_repo(tmp_path)
        for i in range(5):
            (tmp_path / "file.txt").write_text(f"v{i}")
            subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", f"commit {i}"],
                cwd=str(tmp_path), capture_output=True,
            )

        from dan.tools.git_log import git_log

        result = await git_log(path=str(tmp_path), limit=3)
        assert len(result["commits"]) == 3


# ---------------------------------------------------------------------------
# compress
# ---------------------------------------------------------------------------


class TestCompress:
    @pytest.mark.asyncio
    async def test_zip_single_file(self, workspace):
        from dan.tools.compress import compress

        f = workspace / "file.txt"
        f.write_text("content")
        out = workspace / "out.zip"
        result = await compress(paths=[str(f)], output=str(out))
        assert out.exists()
        assert result["file_count"] == 1
        assert result["size_bytes"] > 0
        assert result["archive_path"] == str(out)

    @pytest.mark.asyncio
    async def test_zip_directory(self, workspace):
        from dan.tools.compress import compress

        d = workspace / "mydir"
        d.mkdir()
        (d / "a.txt").write_text("aaa")
        (d / "b.txt").write_text("bbb")
        out = workspace / "dir.zip"
        result = await compress(paths=[str(d)], output=str(out))
        assert out.exists()
        assert result["file_count"] == 2

    @pytest.mark.asyncio
    async def test_tar_gz(self, workspace):
        from dan.tools.compress import compress

        f = workspace / "file.txt"
        f.write_text("content")
        out = workspace / "out.tar.gz"
        result = await compress(paths=[str(f)], output=str(out), format="tar.gz")
        assert out.exists()
        assert result["file_count"] == 1

    @pytest.mark.asyncio
    async def test_invalid_format_raises(self, workspace):
        from dan.tools.compress import compress

        f = workspace / "file.txt"
        f.write_text("x")
        with pytest.raises(ValueError, match="Unsupported format"):
            await compress(paths=[str(f)], output=str(workspace / "out.rar"), format="rar")


# ---------------------------------------------------------------------------
# text_diff
# ---------------------------------------------------------------------------


class TestTextDiff:
    @pytest.mark.asyncio
    async def test_identical_texts(self):
        from dan.tools.text_diff import text_diff

        result = await text_diff(a="hello world", b="hello world")
        assert result["is_identical"] is True
        assert result["additions"] == 0
        assert result["deletions"] == 0

    @pytest.mark.asyncio
    async def test_different_texts(self):
        from dan.tools.text_diff import text_diff

        result = await text_diff(a="hello\n", b="world\n")
        assert result["is_identical"] is False
        assert result["additions"] >= 1
        assert result["deletions"] >= 1
        assert "-hello" in result["unified_diff"]
        assert "+world" in result["unified_diff"]

    @pytest.mark.asyncio
    async def test_diff_files(self, workspace):
        from dan.tools.text_diff import text_diff

        a = workspace / "a.txt"
        b = workspace / "b.txt"
        a.write_text("line1\nline2\n")
        b.write_text("line1\nchanged\n")
        result = await text_diff(a=str(a), b=str(b))
        assert result["is_identical"] is False
        assert result["additions"] >= 1


# ---------------------------------------------------------------------------
# clipboard (skip if no utility available)
# ---------------------------------------------------------------------------


_has_clipboard = (
    (platform.system() == "Darwin" and shutil.which("pbcopy"))
    or (platform.system() == "Linux" and (shutil.which("xclip") or shutil.which("xsel")))
)


class TestClipboard:
    @pytest.mark.asyncio
    @pytest.mark.skipif(not _has_clipboard, reason="No clipboard utility on this system")
    async def test_copy_text(self):
        from dan.tools.clipboard import clipboard

        result = await clipboard(text="unit test payload")
        assert result["copied"] is True
        assert result["length"] == len("unit test payload")

    @pytest.mark.asyncio
    async def test_copy_text_reports_headless_failure(self, monkeypatch):
        from dan.tools import clipboard as clipboard_mod

        async def _fake_run(_cmd, *, input_bytes=None):
            return b"", "", 1

        monkeypatch.setattr(clipboard_mod, "_find_clipboard_cmd", lambda: ["pbcopy"])
        monkeypatch.setattr(clipboard_mod, "_find_clipboard_read_cmd", lambda: ["pbpaste"])
        monkeypatch.setattr(clipboard_mod, "_run_clipboard_command", _fake_run)

        with pytest.raises(RuntimeError, match="Clipboard utility exists but is unavailable in this session"):
            await clipboard_mod.clipboard(text="unit test payload")

    @pytest.mark.asyncio
    async def test_read_clipboard_reports_empty_stderr_failure(self, monkeypatch):
        from dan.tools import clipboard as clipboard_mod

        async def _fake_run(_cmd, *, input_bytes=None):
            return b"", "", 1

        monkeypatch.setattr(clipboard_mod, "_find_clipboard_read_cmd", lambda: ["pbpaste"])
        monkeypatch.setattr(clipboard_mod, "_run_clipboard_command", _fake_run)

        with pytest.raises(RuntimeError, match="Clipboard utility exists but is unavailable in this session"):
            await clipboard_mod.read_clipboard_text()


# ---------------------------------------------------------------------------
# send_email (only test the "not configured" error path)
# ---------------------------------------------------------------------------


class TestSendEmail:
    @pytest.mark.asyncio
    async def test_missing_config_raises(self, monkeypatch):
        monkeypatch.delenv("DAN_SMTP_HOST", raising=False)
        monkeypatch.delenv("DAN_SMTP_USER", raising=False)

        from dan.tools.send_email import send_email

        with pytest.raises(RuntimeError, match="Email not configured"):
            await send_email(to="a@b.com", subject="test", body="hi")


# ---------------------------------------------------------------------------
# notify (bell channel works everywhere)
# ---------------------------------------------------------------------------


class TestNotify:
    @pytest.mark.asyncio
    async def test_bell_channel(self):
        from dan.tools.notify import notify

        result = await notify(message="test notification", channel="bell")
        assert result["delivered"] is True
        assert result["channel"] == "bell"

    @pytest.mark.asyncio
    async def test_auto_channel_delivers(self):
        from dan.tools.notify import notify

        result = await notify(message="auto test")
        assert result["delivered"] is True
        assert result["channel"] is not None


# ---------------------------------------------------------------------------
# image_describe, audio_transcribe
# ---------------------------------------------------------------------------


def _raise_import_error_for_openai(name, *args, **kwargs):
    if name == "openai":
        raise ImportError("simulated missing openai")
    return _ORIGINAL_IMPORT(name, *args, **kwargs)


_ORIGINAL_IMPORT = __import__


class TestMediaToolsGracefulFailure:
    @pytest.mark.asyncio
    async def test_image_describe_without_api_key_raises_clear_error(
        self, workspace, monkeypatch
    ):
        monkeypatch.delenv("DAN_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("DAN_LLM_API_KEY", raising=False)

        from dan.tools.image_describe import image_describe

        image = workspace / "image.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")

        with pytest.raises(RuntimeError, match="No API key for vision model"):
            await image_describe(path=str(image))

    @pytest.mark.asyncio
    async def test_audio_transcribe_without_api_key_raises_clear_error(
        self, workspace, monkeypatch
    ):
        monkeypatch.delenv("DAN_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("DAN_LLM_API_KEY", raising=False)

        from dan.tools.audio_transcribe import audio_transcribe

        audio = workspace / "voice.wav"
        audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

        with pytest.raises(RuntimeError, match="No API key for Whisper"):
            await audio_transcribe(path=str(audio))

    @pytest.mark.asyncio
    async def test_image_describe_without_openai_package_raises_clear_error(
        self, workspace, monkeypatch
    ):
        monkeypatch.setenv("DAN_OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("DAN_LLM_API_KEY", raising=False)

        from dan.tools.image_describe import image_describe

        image = workspace / "image.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")

        with patch("builtins.__import__", side_effect=_raise_import_error_for_openai):
            with pytest.raises(RuntimeError, match="openai package required for image_describe"):
                await image_describe(path=str(image))

    @pytest.mark.asyncio
    async def test_audio_transcribe_without_openai_package_raises_clear_error(
        self, workspace, monkeypatch
    ):
        monkeypatch.setenv("DAN_OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("DAN_LLM_API_KEY", raising=False)

        from dan.tools.audio_transcribe import audio_transcribe

        audio = workspace / "voice.wav"
        audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

        with patch("builtins.__import__", side_effect=_raise_import_error_for_openai):
            with pytest.raises(
                RuntimeError, match="openai package required for audio_transcribe"
            ):
                await audio_transcribe(path=str(audio))


# ---------------------------------------------------------------------------
# spreadsheet_read
# ---------------------------------------------------------------------------

class TestSpreadsheetRead:
    @pytest.fixture(autouse=True)
    def _require_openpyxl(self):
        pytest.importorskip("openpyxl")

    @pytest.mark.asyncio
    async def test_basic(self, workspace):
        from openpyxl import Workbook

        from dan.tools.spreadsheet_read import spreadsheet_read

        wb = Workbook()
        ws = wb.active
        ws.append(["name", "age"])
        ws.append(["Alice", 30])
        ws.append(["Bob", 25])
        p = workspace / "data.xlsx"
        wb.save(str(p))

        result = await spreadsheet_read(path=str(p))
        assert result["headers"] == ["name", "age"]
        assert result["row_count"] == 2
        assert result["column_count"] == 2
        assert result["rows"][0]["name"] == "Alice"
        assert result["rows"][1]["age"] == 25

    @pytest.mark.asyncio
    async def test_named_sheet(self, workspace):
        from openpyxl import Workbook

        from dan.tools.spreadsheet_read import spreadsheet_read

        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(["x"])
        ws2 = wb.create_sheet("Sales")
        ws2.append(["product", "revenue"])
        ws2.append(["Widget", 500])
        p = workspace / "multi.xlsx"
        wb.save(str(p))

        result = await spreadsheet_read(path=str(p), sheet="Sales")
        assert result["headers"] == ["product", "revenue"]
        assert result["rows"][0]["product"] == "Widget"

    @pytest.mark.asyncio
    async def test_max_rows(self, workspace):
        from openpyxl import Workbook

        from dan.tools.spreadsheet_read import spreadsheet_read

        wb = Workbook()
        ws = wb.active
        ws.append(["id", "val"])
        for i in range(50):
            ws.append([i, i * 10])
        p = workspace / "big.xlsx"
        wb.save(str(p))

        result = await spreadsheet_read(path=str(p), max_rows=5)
        assert result["row_count"] == 5
        assert result["truncated"] is True
        assert result["total_rows"] == 50

    @pytest.mark.asyncio
    async def test_missing_file(self, workspace):
        from dan.tools.spreadsheet_read import spreadsheet_read

        with pytest.raises(FileNotFoundError):
            await spreadsheet_read(path=str(workspace / "nope.xlsx"))

    @pytest.mark.asyncio
    async def test_empty_sheet(self, workspace):
        from openpyxl import Workbook

        from dan.tools.spreadsheet_read import spreadsheet_read

        wb = Workbook()
        wb.save(str(workspace / "empty.xlsx"))

        result = await spreadsheet_read(path=str(workspace / "empty.xlsx"))
        assert result["row_count"] == 0
        assert result["headers"] == []

    @pytest.mark.asyncio
    async def test_bad_sheet_name(self, workspace):
        from openpyxl import Workbook

        from dan.tools.spreadsheet_read import spreadsheet_read

        wb = Workbook()
        ws = wb.active
        ws.append(["a"])
        p = workspace / "sheets.xlsx"
        wb.save(str(p))

        with pytest.raises(ValueError, match="not found"):
            await spreadsheet_read(path=str(p), sheet="NoSuchSheet")


# ---------------------------------------------------------------------------
# text_translate
# ---------------------------------------------------------------------------


class TestTextTranslate:
    @pytest.mark.asyncio
    async def test_no_api_key(self, monkeypatch):
        monkeypatch.delenv("DAN_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("DAN_LLM_API_KEY", raising=False)

        from dan.tools.text_translate import text_translate

        with pytest.raises(RuntimeError, match="No API key"):
            await text_translate(text="Hello", target_language="Spanish")
