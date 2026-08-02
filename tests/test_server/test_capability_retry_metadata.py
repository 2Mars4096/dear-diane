import httpx
import pytest

from dan.server.capability_handlers import (
    handle_file_grep,
    handle_file_read,
    handle_get_activity,
    handle_http_request,
    handle_list_directory,
    handle_shell_command,
    handle_web_fetch,
    handle_web_search,
)
from dan.server.capability_registry import (
    CapabilityContext,
    ChatCapabilityRegistry,
    build_tool_schema,
)
from dan.server.gateway.models import ActivitySnapshot


@pytest.fixture
def capability_context(tmp_path, monkeypatch) -> CapabilityContext:
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(tmp_path))
    return CapabilityContext(workflow_id="wf-test")


@pytest.mark.asyncio
async def test_registry_marks_handler_exceptions_as_internal_and_non_retryable() -> None:
    registry = ChatCapabilityRegistry()

    async def failing_handler(args, ctx):
        raise RuntimeError("boom")

    registry.register(
        "explode",
        build_tool_schema(
            name="explode",
            description="Explode for testing.",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        failing_handler,
    )

    result = await registry.execute("explode", {}, CapabilityContext(workflow_id="wf"))

    assert result.success is False
    assert result.retryable is False
    assert result.error_type == "internal_exception"
    assert result.message == "Tool error: boom"


@pytest.mark.asyncio
async def test_shell_command_marks_nonzero_exit_as_non_retryable(
    capability_context: CapabilityContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_shell_command(**_kwargs):
        return {"exit_code": 2, "stdout": "", "stderr": "bad command"}

    monkeypatch.setattr("dan.tools.shell_command.shell_command", fake_shell_command)

    result = await handle_shell_command({"command": "bad-command"}, capability_context)

    assert result.success is False
    assert result.retryable is False
    assert result.error_type == "nonzero_exit"
    assert "stderr: bad command" in result.message
    assert "exit code: 2" in result.message


@pytest.mark.asyncio
async def test_shell_command_marks_internal_exceptions_as_non_retryable(
    capability_context: CapabilityContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_shell_command(**_kwargs):
        raise RuntimeError("subprocess unavailable")

    monkeypatch.setattr("dan.tools.shell_command.shell_command", fake_shell_command)

    result = await handle_shell_command({"command": "echo hi"}, capability_context)

    assert result.success is False
    assert result.retryable is False
    assert result.error_type == "internal_exception"
    assert result.message == "Command failed: subprocess unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "args", "expected_message"),
    [
        (handle_file_read, {"path": "missing.txt"}, "File not found: missing.txt"),
        (
            handle_file_grep,
            {"path": "missing-dir", "pattern": "needle"},
            "Directory not found: missing-dir",
        ),
        (
            handle_list_directory,
            {"path": "missing-dir"},
            "Directory not found: missing-dir",
        ),
    ],
)
async def test_local_path_handlers_mark_missing_targets_as_non_retryable(
    handler,
    args,
    expected_message: str,
    capability_context: CapabilityContext,
) -> None:
    result = await handler(args, capability_context)

    assert result.success is False
    assert result.retryable is False
    assert result.error_type == "target_missing"
    assert result.message == expected_message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "args", "patch_target"),
    [
        (handle_web_search, {"query": "dan"}, "dan.tools.web_search.web_search"),
        (handle_web_fetch, {"url": "https://example.com"}, "dan.tools.web_fetch.web_fetch"),
        (handle_http_request, {"url": "https://example.com"}, "dan.tools.http_request.http_request"),
    ],
)
async def test_network_handlers_mark_transient_failures_as_retryable(
    handler,
    args,
    patch_target: str,
    capability_context: CapabilityContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("GET", "https://example.com")

    async def failing_tool(**_kwargs):
        raise httpx.ConnectError("network down", request=request)

    monkeypatch.setattr(patch_target, failing_tool)

    result = await handler(args, capability_context)

    assert result.success is False
    assert result.retryable is True
    assert result.error_type == "network_error"


@pytest.mark.asyncio
async def test_get_activity_accepts_typed_snapshot(capability_context: CapabilityContext) -> None:
    capability_context.activity_tracker = type(
        "Tracker",
        (),
        {
            "get_activity": lambda self: ActivitySnapshot(
                active=[{"run_id": "run-1", "graph_id": "wf-1", "status": "running"}],
                recent=[],
                connected_surfaces=[{"surface_id": "cli", "surface_type": "terminal"}],
            )
        },
    )()

    result = await handle_get_activity({}, capability_context)

    assert result.success is True
    assert isinstance(result.data, dict)
    assert result.data["active"][0]["run_id"] == "run-1"
    assert "Active runs (1)" in result.message
