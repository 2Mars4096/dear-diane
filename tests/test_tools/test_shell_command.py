from __future__ import annotations

import pytest

from dan.tools.shell_command import shell_command


@pytest.mark.asyncio
async def test_shell_command_sandbox_respects_working_directory_and_pythonpath(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("DAN_SANDBOX_SHELL", raising=False)

    package_dir = tmp_path / "src" / "demo_pkg"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("VALUE = 41\n", encoding="utf-8")

    result = await shell_command(
        command='python -c "import demo_pkg; print(demo_pkg.VALUE)"',
        working_directory=str(tmp_path),
    )

    assert result["exit_code"] == 0
    assert result["stderr"] == ""
    assert result["stdout"].strip() == "41"


@pytest.mark.asyncio
async def test_shell_command_sandbox_exposes_standard_system_bins(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("DAN_SANDBOX_SHELL", raising=False)
    monkeypatch.setenv("PATH", "")
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")

    result = await shell_command(
        command="cat note.txt",
        working_directory=str(tmp_path),
    )

    assert result["exit_code"] == 0
    assert result["stderr"] == ""
    assert result["stdout"] == "hello\n"
