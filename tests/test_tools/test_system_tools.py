"""Tests for shell_command tool."""

from __future__ import annotations

import pytest

from dan.tools.shell_command import shell_command


class TestShellCommand:
    @pytest.mark.asyncio
    async def test_echo(self):
        result = await shell_command(command="echo hello")
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "hello"

    @pytest.mark.asyncio
    async def test_stderr(self):
        result = await shell_command(command="echo err >&2")
        assert "err" in result["stderr"]

    @pytest.mark.asyncio
    async def test_exit_code(self):
        result = await shell_command(command="exit 42")
        assert result["exit_code"] == 42

    @pytest.mark.asyncio
    async def test_timeout(self):
        result = await shell_command(command="sleep 60", timeout=1)
        assert result["exit_code"] == -1
        assert "timed out" in result["stderr"]

    @pytest.mark.asyncio
    async def test_allowlist_blocks(self, monkeypatch):
        monkeypatch.setenv("DAN_SHELL_ALLOW", "echo,ls")
        with pytest.raises(PermissionError, match="not in the allowed"):
            await shell_command(command="rm -rf /")

    @pytest.mark.asyncio
    async def test_allowlist_permits(self, monkeypatch):
        monkeypatch.setenv("DAN_SHELL_ALLOW", "echo,ls")
        result = await shell_command(command="echo allowed")
        assert result["exit_code"] == 0
        assert "allowed" in result["stdout"]

    @pytest.mark.asyncio
    async def test_no_allowlist_allows_all(self, monkeypatch):
        monkeypatch.delenv("DAN_SHELL_ALLOW", raising=False)
        result = await shell_command(command="echo open")
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_env_vars(self):
        result = await shell_command(
            command="echo $MY_VAR",
            env={"MY_VAR": "custom_value"},
        )
        assert "custom_value" in result["stdout"]
