from __future__ import annotations

import pytest

from dan.server.capability_handlers import handle_shell_command
from dan.server.capability_registry import CapabilityContext


@pytest.mark.asyncio
async def test_handle_shell_command_treats_exit_code_zero_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_shell_command(**_kwargs):
        return {
            "exit_code": 0,
            "stdout": "hello\n",
            "stderr": "",
        }

    monkeypatch.setattr(
        "dan.tools.shell_command.shell_command",
        _fake_shell_command,
    )

    result = await handle_shell_command(
        {"command": "echo hello"},
        CapabilityContext(workflow_id="wf"),
    )

    assert result.success is True
    assert "hello" in result.message
    assert "exit code: 0" in result.message


@pytest.mark.asyncio
async def test_handle_shell_command_keeps_return_code_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_shell_command(**_kwargs):
        return {
            "return_code": 0,
            "stdout": "legacy\n",
            "stderr": "",
        }

    monkeypatch.setattr(
        "dan.tools.shell_command.shell_command",
        _fake_shell_command,
    )

    result = await handle_shell_command(
        {"command": "echo legacy"},
        CapabilityContext(workflow_id="wf"),
    )

    assert result.success is True
    assert "legacy" in result.message
    assert "exit code: 0" in result.message
