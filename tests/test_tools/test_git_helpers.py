from __future__ import annotations

import pytest

from dan.tools import _git_helpers as git_helpers


def test_git_binary_prefers_system_git_when_available(monkeypatch) -> None:
    monkeypatch.setattr(
        git_helpers.os.path,
        "exists",
        lambda path: path == "/usr/bin/git",
    )

    assert git_helpers._git_binary() == "/usr/bin/git"


@pytest.mark.asyncio
async def test_run_git_uses_preferred_binary(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"ok\n", b""

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(git_helpers, "_git_binary", lambda: "/usr/bin/git")
    monkeypatch.setattr(git_helpers.asyncio, "create_subprocess_exec", _fake_exec)

    stdout, stderr, rc = await git_helpers._run_git("/tmp/demo", "status")

    assert stdout == "ok\n"
    assert stderr == ""
    assert rc == 0
    assert captured["args"] == ("/usr/bin/git", "-C", "/tmp/demo", "status")

