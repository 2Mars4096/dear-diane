from __future__ import annotations

import logging
from pathlib import Path

import pytest

from dan.cli.chat import (
    _CliProgressDisplay,
    _format_api_key_status_label,
    _friendly_http_error_message,
    _progress_ack_text,
)
from dan.server.app_state import AppState
from dan.server.chat_manager import _friendly_chat_error
from dan.cli import adapter as adapter_module
from dan.engine.learning_tiers import SqliteBackend
from dan.server.concierge.dispatcher import _is_bypass_command
from dan.server.concierge.models import SurfaceMessage
from dan.server.startup import (
    _record_startup_degradation,
    get_llm_api_key_status,
    get_startup_degradation_summary,
    log_startup_configuration_warnings,
    log_startup_degradation_summary,
)
from dan.tools._workspace import validate_path
from dan.tools.shell_command import _use_sandbox, shell_sandbox_explicitly_disabled


def test_translate_slash_command_keeps_server_commands_raw() -> None:
    assert (
        adapter_module._translate_slash_command("/find report.csv")
        == "Find the file matching 'report.csv' on my computer"
    )


def test_registry_chat_commands_bypass_dispatcher_queue() -> None:
    msg = SurfaceMessage(surface="cli", external_id="user-1", text="/schedule list")
    assert _is_bypass_command(msg) is True


def test_validate_path_blocks_absolute_write_outside_workspace_in_strict_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("DAN_STRICT_SANDBOX", "1")

    with pytest.raises(PermissionError):
        validate_path(str(outside), operation="write")


def test_validate_path_allows_absolute_reads_outside_workspace_in_strict_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("DAN_STRICT_SANDBOX", "1")

    assert validate_path(str(outside), operation="read") == str(outside.resolve())


def test_validate_path_allows_relative_write_inside_workspace_in_strict_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("DAN_STRICT_SANDBOX", "1")

    assert validate_path("notes/output.txt", operation="write") == str(
        (workspace / "notes" / "output.txt").resolve()
    )


def test_validate_path_allows_absolute_write_inside_workspace_in_strict_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "inside.txt"
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("DAN_STRICT_SANDBOX", "1")

    assert validate_path(str(target), operation="write") == str(target.resolve())


def test_validate_path_blocks_symlink_escape_in_strict_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    link = workspace / "escaped.txt"
    link.symlink_to(outside)
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("DAN_STRICT_SANDBOX", "1")

    with pytest.raises(PermissionError):
        validate_path(str(link), operation="write")


def test_shell_sandbox_defaults_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAN_SANDBOX_SHELL", raising=False)

    assert _use_sandbox() is True
    assert shell_sandbox_explicitly_disabled() is False


def test_shell_sandbox_explicit_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_SANDBOX_SHELL", "0")

    assert _use_sandbox() is False
    assert shell_sandbox_explicitly_disabled() is True


def test_shell_sandbox_invalid_value_keeps_default_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_SANDBOX_SHELL", "definitely")

    assert _use_sandbox() is True
    assert shell_sandbox_explicitly_disabled() is False


def test_get_llm_api_key_status_detects_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_LLM_API_KEY", "changeme")

    assert get_llm_api_key_status() == "placeholder"


def test_get_llm_api_key_status_accepts_provider_specific_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DAN_LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("DAN_OPENAI_API_KEY", "sk-provider")

    assert get_llm_api_key_status() == "configured"


def test_get_llm_api_key_status_prefers_real_provider_key_over_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_LLM_API_KEY", "your-api-key-here")
    monkeypatch.setenv("DAN_ANTHROPIC_API_KEY", "real-key")

    assert get_llm_api_key_status() == "configured"


def test_startup_warnings_cover_missing_key_and_shell_opt_out(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("DAN_LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("DAN_SANDBOX_SHELL", "0")

    with caplog.at_level(logging.WARNING):
        log_startup_configuration_warnings()

    messages = [record.message for record in caplog.records]
    assert any("DAN_LLM_API_KEY is missing" in message for message in messages)
    assert any("Shell sandbox is explicitly disabled" in message for message in messages)


def test_startup_warnings_skip_missing_key_when_provider_key_configured(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("DAN_LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("DAN_OPENAI_API_KEY", "sk-provider")
    monkeypatch.delenv("DAN_SANDBOX_SHELL", raising=False)

    with caplog.at_level(logging.WARNING):
        log_startup_configuration_warnings()

    messages = [record.message for record in caplog.records]
    assert not any("DAN_LLM_API_KEY is missing" in message for message in messages)


def test_sqlite_backend_rejects_adversarial_json_filter_key(tmp_path: Path) -> None:
    backend = SqliteBackend(tmp_path / "memory.db")
    backend.upsert("m1", {"memory_type": "fact", "topic": "security"})

    with pytest.raises(ValueError):
        backend.query(**{"topic') = 1 OR 1=1 --": "security"})


def test_cli_api_key_status_label_uses_startup_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_LLM_API_KEY", "your-api-key-here")

    assert _format_api_key_status_label() == "placeholder"


def test_cli_friendly_http_error_message_maps_common_status_codes() -> None:
    assert "API key" in _friendly_http_error_message(401, "bad key", action="Chat request")
    assert "rate limited" in _friendly_http_error_message(429, "slow down", action="Chat request")
    assert "not found" in _friendly_http_error_message(404, "missing", action="Load workflow")
    assert "server error" in _friendly_http_error_message(500, "boom", action="Chat request")


def test_progress_ack_text_prefers_phase_label() -> None:
    event = {
        "type": "chat_complete",
        "detected_mode": "progress_ack",
        "phase_label": "Gathering relevant context",
        "content": "Working on: Gathering relevant context...",
    }

    assert _progress_ack_text(event) == "Thinking... Gathering relevant context"


@pytest.mark.asyncio
async def test_cli_progress_display_compact_dedupes_duplicate_phase_labels() -> None:
    display = _CliProgressDisplay(verbosity="compact")

    first = await display.render(
        {"type": "chat_complete", "detected_mode": "progress_ack", "phase_label": "Planning"}
    )
    second = await display.render(
        {"type": "chat_complete", "detected_mode": "progress_ack", "phase_label": "Planning"}
    )

    assert first == ["▶ Planning..."]
    assert second == []


@pytest.mark.asyncio
async def test_cli_progress_display_minimal_only_prints_first_update() -> None:
    display = _CliProgressDisplay(verbosity="minimal")

    first = await display.render(
        {"type": "chat_complete", "detected_mode": "progress_ack", "phase_label": "Planning"}
    )
    second = await display.render(
        {"type": "chat_complete", "detected_mode": "progress_ack", "phase_label": "Reviewing"}
    )

    assert first == ["▶ Planning..."]
    assert second == []


@pytest.mark.asyncio
async def test_cli_progress_display_full_prints_heartbeat_lines() -> None:
    display = _CliProgressDisplay(verbosity="full")

    lines = await display.render(
        {"type": "chat_complete", "detected_mode": "progress_ack", "content": "Still working"}
    )

    assert len(lines) == 1
    assert "Still working" in lines[0]


def test_friendly_chat_error_maps_key_error_to_config_hint() -> None:
    err = _friendly_chat_error(KeyError("openai"))
    assert "DAN_LLM_" in err
    assert "openai" in err


def test_friendly_chat_error_maps_rate_limit() -> None:
    assert "retry" in _friendly_chat_error(RuntimeError("HTTP 429 rate limit")).lower()


def test_friendly_chat_error_maps_timeout() -> None:
    assert "timed out" in _friendly_chat_error(TimeoutError("timed out")).lower()


def test_friendly_chat_error_maps_connection_refused() -> None:
    result = _friendly_chat_error(ConnectionError("Connection refused"))
    assert "connect" in result.lower()
    assert "DAN_LLM_BASE_URL" in result


def test_friendly_chat_error_truncates_long_messages() -> None:
    long_msg = "x" * 300
    result = _friendly_chat_error(RuntimeError(long_msg))
    assert len(result) < 250


def test_startup_degradation_summary_reports_ok_when_empty() -> None:
    state = AppState()

    assert get_startup_degradation_summary(state) == {
        "status": "ok",
        "issues": [],
    }


def test_startup_degradation_summary_collects_and_logs_issues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = AppState()
    _record_startup_degradation(
        state,
        "notifications",
        "notification manager failed to initialize",
    )
    _record_startup_degradation(
        state,
        "concierge",
        "concierge dispatcher failed to initialize",
    )

    summary = get_startup_degradation_summary(state)

    assert summary["status"] == "degraded"
    assert summary["issues"] == [
        {
            "subsystem": "notifications",
            "message": "notification manager failed to initialize",
        },
        {
            "subsystem": "concierge",
            "message": "concierge dispatcher failed to initialize",
        },
    ]

    with caplog.at_level(logging.WARNING):
        log_startup_degradation_summary(state)

    assert any("Startup degradation summary" in record.message for record in caplog.records)
    assert any("notifications" in record.message for record in caplog.records)
