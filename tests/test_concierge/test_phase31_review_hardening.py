from __future__ import annotations

from dan.cli import adapter as adapter_module
from dan.server.concierge.dispatcher import _is_bypass_command
from dan.server.concierge.models import SurfaceMessage


def test_translate_slash_command_keeps_server_commands_raw() -> None:
    assert (
        adapter_module._translate_slash_command("/find report.csv")
        == "Find the file matching 'report.csv' on my computer"
    )


def test_registry_chat_commands_bypass_dispatcher_queue() -> None:
    msg = SurfaceMessage(surface="cli", external_id="user-1", text="/schedule list")
    assert _is_bypass_command(msg) is True
