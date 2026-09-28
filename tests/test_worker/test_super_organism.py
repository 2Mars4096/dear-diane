from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
import shlex
import sys

import pytest

from dan import _atomic_file
from dan.tools.file_write import file_write
import dan.worker.organisms.local_runtime as local_runtime_module
from dan.providers import CompletionResult
from dan.worker.core.contracts import OutputContract
from dan.worker.core.interfaces import CompletionRequest
from dan.worker.organisms.local_runtime import (
    LocalOrganismToolRuntime,
    ToolLoopCompletionProvider,
)
from dan.worker.organisms.super_organism import (
    DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP,
    DEFAULT_SUPER_ORGANISM_CELL_COUNT,
    SuperOrgan,
    SuperOrganismScenario,
    build_super_organism_cells,
    resolve_super_organism_distribution,
    resolve_super_organism_scenario,
    run_super_organism_demo,
)


@dataclass
class _FakeCapabilityResult:
    success: bool = True
    message: str = "ok"
    output_preview: str = ""
    data: object = None
    retryable: bool = False
    error_type: str = ""


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_for_pid_exit(pid: int, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if not _pid_is_running(pid):
            return
        await asyncio.sleep(0.05)
    assert not _pid_is_running(pid)


def test_ranged_file_read_prompt_line_numbers_keep_source_offsets():
    payload = {
        "name": "file_read",
        "arguments": {"path": "src/example.py", "start_line": 700, "end_line": 702},
        "result": {
            "path": "src/example.py",
            "content": "alpha\nbeta\ngamma\n",
            "line_start": 700,
            "line_end": 702,
            "returned_line_count": 3,
            "total_line_count": 854,
        },
    }

    numbered = local_runtime_module._file_read_payload_with_line_numbers(payload)

    assert "   700| alpha" in numbered["result"]["content"]
    assert "   702| gamma" in numbered["result"]["content"]
    assert "     1|" not in numbered["result"]["content"]


def test_completion_request_user_content_includes_image_attachments(tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    request = CompletionRequest(
        model="test",
        system_prompt="system",
        user_prompt="Review the screenshot.",
        metadata={"image_attachments": [{"kind": "image", "local_path": str(image)}]},
    )

    content = local_runtime_module._completion_request_user_content(request)

    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "Review the screenshot." in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_structured_surface_policy_keeps_transient_browser_open_separate_from_workspace_write():
    request = CompletionRequest(
        model="test",
        system_prompt="Keep the workspace read-only.",
        user_prompt="Open the local browser demo and inspect it.",
        metadata={
            "surface_policy": {
                "permission_scope": "transient_execute",
                "capability_packs": ["browser_control"],
            }
        },
    )

    allowed = local_runtime_module._structured_surface_ui_tool_ids(
        request,
        ["browser_open", "browser_click", "file_write"],
    )

    assert allowed == ["browser_open", "browser_click"]


def test_structured_surface_policy_does_not_infer_browser_authority_from_prompt_text():
    request = CompletionRequest(
        model="test",
        system_prompt="You can discuss browser control.",
        user_prompt="Explain browser navigation.",
        metadata={},
    )

    allowed = local_runtime_module._structured_surface_ui_tool_ids(
        request,
        ["browser_open", "browser_click"],
    )

    assert allowed == []


def _first_event(events: list[dict[str, object]], event_name: str) -> dict[str, object]:
    return next(event for event in events if event.get("event") == event_name)


class _ContextLengthAfterOldWriteProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(
        self, messages, model, temperature=0.7, max_tokens=None, **kwargs
    ):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index == 1:
            tool_call = {
                "id": "call-old-write",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "notes/context-pressure.md",
                            "content": "old-write-payload\n" * 500,
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 2:
            assert "old-write-payload" in json.dumps(messages)
            tool_call = {
                "id": "call-latest-list",
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "arguments": json.dumps({"path": "."}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 3:
            assert "old-write-payload" in json.dumps(messages)
            raise RuntimeError("maximum context length exceeded: too many tokens")

        replay = json.dumps(messages)
        assert "old-write-payload" not in replay
        assert "prompt_replay_compacted" in replay
        assert "call-latest-list" in replay
        return CompletionResult(
            text=json.dumps({"candidate_id": "after-context-length-retry"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "after-context-length-retry"}),
            },
        )


class _RepeatedFileReadProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(
        self, messages, model, temperature=0.7, max_tokens=None, **kwargs
    ):
        self.calls.append(
            {"messages": messages, "model": model, "tools": kwargs.get("tools")}
        )
        call_index = len(self.calls)
        if call_index <= 2:
            tool_call = {
                "id": f"call-read-{call_index}",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "notes/context.txt"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        return CompletionResult(
            text=json.dumps({"candidate_id": "cached-read"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "cached-read"}),
            },
        )


class _CheckpointSteeringProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(
        self, messages, model, temperature=0.7, max_tokens=None, **kwargs
    ):
        self.calls.append(
            {"messages": messages, "model": model, "tools": kwargs.get("tools")}
        )
        if len(self.calls) == 1:
            tool_call = {
                "id": "call-read-before-steer",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps({"path": "notes/context.txt"}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        return CompletionResult(
            text=json.dumps({"candidate_id": "steered", "validated_with": "pytest"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps(
                    {"candidate_id": "steered", "validated_with": "pytest"}
                ),
            },
        )


def test_default_distribution_builds_20_logical_cells() -> None:
    distribution = resolve_super_organism_distribution(
        DEFAULT_SUPER_ORGANISM_CELL_COUNT
    )

    assert distribution == {
        "brain": 1,
        "scout": 5,
        "claim": 4,
        "immune": 4,
        "memory": 2,
        "experiment": 2,
        "synthesis": 2,
    }

    cells = build_super_organism_cells()

    assert len(cells) == 20
    assert cells[0].cell_id == "brain-001"
    assert cells[-1].cell_id == "synthesis-002"
    assert cells[0].organ == SuperOrgan.BRAIN


@pytest.mark.asyncio
async def test_super_dan_runtime_resolves_relative_shell_cwd_against_workspace(
    tmp_path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    events: list[dict[str, object]] = []

    async def _fake_shell_command(**kwargs):
        captured["kwargs"] = dict(kwargs)
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(
        local_runtime_module,
        "get_all_tools",
        lambda: {
            "shell_command": (
                _fake_shell_command,
                {
                    "tool_id": "shell_command",
                    "category": "system",
                    "description": "fake shell",
                    "parameters": {
                        "type": "object",
                        "required": ["command"],
                        "properties": {"command": {"type": "string"}},
                    },
                },
            )
        },
    )
    runtime = LocalOrganismToolRuntime(
        tool_ids=["shell_command"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )

    await runtime.call("shell_command", {"command": "pwd", "working_directory": "."})

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["working_directory"] == str(tmp_path.resolve())
    started = next(event for event in events if event.get("event") == "tool.started")
    assert isinstance(started.get("arguments"), dict)
    assert started["arguments"]["working_directory"] == str(tmp_path.resolve())


@pytest.mark.asyncio
async def test_atomic_file_write_preserves_original_when_replace_fails(
    tmp_path,
    monkeypatch,
) -> None:
    target = tmp_path / "out.txt"
    target.write_text("original\n", encoding="utf-8")
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(tmp_path))

    def _fail_replace(_source, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(_atomic_file.os, "replace", _fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        await file_write(path="out.txt", content="replacement\n")

    assert target.read_text(encoding="utf-8") == "original\n"
    assert list(tmp_path.glob(".out.txt.dan-tmp-*")) == []


@pytest.mark.asyncio
async def test_atomic_file_write_new_file_honors_process_umask(
    tmp_path,
    monkeypatch,
) -> None:
    previous_umask = os.umask(0o022)
    try:
        monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(tmp_path))
        await file_write(path="new.txt", content="new\n")
    finally:
        os.umask(previous_umask)

    assert (tmp_path / "new.txt").stat().st_mode & 0o777 == 0o644


@pytest.mark.asyncio
async def test_super_dan_runtime_blocks_in_place_shell_text_mutation(
    tmp_path,
) -> None:
    target = tmp_path / "index.html"
    original = "<main>" + ("stable-content\n" * 80) + "</main>\n"
    target.write_text(original, encoding="utf-8")
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "shell_command"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )

    await runtime.call("file_read", {"path": "index.html"})
    with pytest.raises(ValueError, match="in-place sed is disabled"):
        await runtime.call(
            "shell_command",
            {"command": "sed -i '' '0,/<\\/html>/!d' index.html"},
        )

    assert target.read_text(encoding="utf-8") == original
    assert any(
        event.get("event") == "tool.failed"
        and "unsafe direct shell mutation" in str(event.get("error") or "")
        for event in events
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "printf broken > index.html",
        "rm index.html",
        "env rm index.html",
        "find . -name index.html -delete",
        "git reset --hard HEAD",
        "command sed -i.bak 's/stable/broken/' index.html",
    ],
)
async def test_super_dan_runtime_blocks_direct_destructive_shell_commands(
    tmp_path,
    command,
) -> None:
    target = tmp_path / "index.html"
    original = "<main>stable</main>\n"
    target.write_text(original, encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["shell_command"],
        workspace_root=tmp_path,
    )

    with pytest.raises(ValueError, match="unsafe direct shell mutation"):
        await runtime.call("shell_command", {"command": command})

    assert target.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "git restore index.html",
        "git checkout HEAD -- index.html",
        "git checkout -- index.html",
        "env git restore index.html",
        "git -C . restore index.html",
    ],
)
async def test_super_dan_runtime_blocks_git_workspace_restoration(
    tmp_path,
    command,
) -> None:
    target = tmp_path / "index.html"
    target.write_text("<main>stable</main>\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["shell_command"],
        workspace_root=tmp_path,
    )

    with pytest.raises(ValueError, match="destructive git restoration"):
        await runtime.call("shell_command", {"command": command})


@pytest.mark.asyncio
async def test_super_dan_git_guard_ignores_non_executed_words(tmp_path) -> None:
    runtime = LocalOrganismToolRuntime(
        tool_ids=["shell_command"],
        workspace_root=tmp_path,
    )

    result = await runtime.call(
        "shell_command",
        {"command": "echo git restore index.html"},
    )

    assert result["exit_code"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "echo rm index.html",
        "printf truncate",
        "echo sed -i index.html",
    ],
)
async def test_super_dan_shell_guard_ignores_non_executed_words(
    tmp_path,
    command,
) -> None:
    runtime = LocalOrganismToolRuntime(
        tool_ids=["shell_command"],
        workspace_root=tmp_path,
    )

    result = await runtime.call("shell_command", {"command": command})

    assert result["exit_code"] == 0


@pytest.mark.asyncio
async def test_super_dan_runtime_rolls_back_catastrophic_shell_shrink(
    tmp_path,
) -> None:
    target = tmp_path / "index.html"
    original = "<main>" + ("stable-content\n" * 80) + "</main>\n"
    target.write_text(original, encoding="utf-8")
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "shell_command"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )

    await runtime.call("file_read", {"path": "index.html"})
    script = "from pathlib import Path; Path('index.html').write_text('')"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
    result = await runtime.call(
        "shell_command",
        {"command": command, "timeout": 5},
    )

    assert result["exit_code"] == 125
    assert result["transaction"]["status"] == "rolled_back"
    assert target.read_text(encoding="utf-8") == original
    assert result["workspace_changes"]["changed_paths"] == []
    assert any(event.get("event") == "tool.rolled_back" for event in events)


@pytest.mark.asyncio
async def test_super_dan_runtime_rolls_back_short_file_catastrophic_shrink(
    tmp_path,
) -> None:
    target = tmp_path / "config.txt"
    original = "x" * 200
    target.write_text(original, encoding="utf-8")
    original_mtime_ns = 1_700_000_000_000_000_000
    os.utime(target, ns=(original_mtime_ns, original_mtime_ns))
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "shell_command"],
        workspace_root=tmp_path,
    )

    await runtime.call("file_read", {"path": "config.txt"})
    script = "from pathlib import Path; Path('config.txt').write_text('x')"
    result = await runtime.call(
        "shell_command",
        {
            "command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
            "timeout": 5,
        },
    )

    assert result["exit_code"] == 125
    assert result["transaction"]["status"] == "rolled_back"
    assert target.read_text(encoding="utf-8") == original
    assert target.stat().st_mtime_ns == original_mtime_ns


@pytest.mark.asyncio
async def test_super_dan_runtime_protects_path_embedded_in_script(tmp_path) -> None:
    target = tmp_path / "notes.txt"
    original = "important\n" * 80
    target.write_text(original, encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["shell_command"],
        workspace_root=tmp_path,
    )

    script = "from pathlib import Path; Path('notes.txt').write_text('')"
    result = await runtime.call(
        "shell_command",
        {
            "command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
            "timeout": 5,
        },
    )

    assert result["exit_code"] == 125
    assert result["transaction"]["status"] == "rolled_back"
    assert target.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_super_dan_runtime_move_exemption_is_source_path_specific(
    tmp_path,
) -> None:
    protected = tmp_path / "protected.txt"
    protected.write_text("protected\n" * 80, encoding="utf-8")
    other = tmp_path / "other.txt"
    other.write_text("other\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "shell_command"],
        workspace_root=tmp_path,
    )

    await runtime.call("file_read", {"path": "protected.txt"})
    script = "from pathlib import Path; Path('protected.txt').unlink()"
    command = (
        "mv other.txt moved.txt && "
        f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
    )
    result = await runtime.call(
        "shell_command",
        {"command": command, "timeout": 5},
    )

    assert result["exit_code"] == 125
    assert result["transaction"]["status"] == "rolled_back"
    assert protected.read_text(encoding="utf-8") == "protected\n" * 80


@pytest.mark.asyncio
async def test_super_dan_runtime_commits_move_of_protected_source(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("move me\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "shell_command"],
        workspace_root=tmp_path,
    )

    await runtime.call("file_read", {"path": "source.txt"})
    result = await runtime.call(
        "shell_command",
        {"command": "mv source.txt renamed.txt", "timeout": 5},
    )

    assert result["exit_code"] == 0
    assert result["transaction"]["status"] == "committed"
    assert not source.exists()
    assert (tmp_path / "renamed.txt").read_text(encoding="utf-8") == "move me\n"


@pytest.mark.asyncio
async def test_super_dan_runtime_rolls_back_failed_shell_mutation(
    tmp_path,
) -> None:
    target = tmp_path / "notes.txt"
    original = "stable notes\n"
    target.write_text(original, encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "shell_command"],
        workspace_root=tmp_path,
    )

    await runtime.call("file_read", {"path": "notes.txt"})
    script = (
        "from pathlib import Path; "
        "Path('notes.txt').write_text('damaged notes\\n'); "
        "raise SystemExit(2)"
    )
    result = await runtime.call(
        "shell_command",
        {
            "command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
            "timeout": 5,
        },
    )

    assert result["exit_code"] == 2
    assert result["transaction"]["status"] == "rolled_back"
    assert target.read_text(encoding="utf-8") == original
    assert result["workspace_changes"]["changed_paths"] == []


@pytest.mark.asyncio
async def test_super_dan_runtime_commits_non_destructive_shell_update(
    tmp_path,
) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("draft notes\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "shell_command"],
        workspace_root=tmp_path,
    )

    await runtime.call("file_read", {"path": "notes.txt"})
    script = (
        "from pathlib import Path; "
        "path = Path('notes.txt'); "
        "path.write_text(path.read_text().replace('draft', 'final'))"
    )
    result = await runtime.call(
        "shell_command",
        {
            "command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
            "timeout": 5,
        },
    )

    assert result["exit_code"] == 0
    assert result["transaction"]["status"] == "committed"
    assert target.read_text(encoding="utf-8") == "final notes\n"


@pytest.mark.asyncio
async def test_super_dan_runtime_bounds_protected_shell_snapshot_memory(
    tmp_path,
    monkeypatch,
) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("a" * 8, encoding="utf-8")
    second.write_text("b" * 8, encoding="utf-8")
    monkeypatch.setattr(
        local_runtime_module,
        "_SHELL_PROTECTED_TOTAL_MAX_BYTES",
        10,
    )
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read", "shell_command"],
        workspace_root=tmp_path,
    )

    await runtime.call("file_read", {"path": "first.txt"})
    await runtime.call("file_read", {"path": "second.txt"})

    assert set(runtime._protected_workspace_files) == {str(second.resolve())}
    assert runtime._protected_workspace_bytes == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("sandbox_enabled", ["true", "0"])
async def test_super_dan_shell_timeout_kills_child_processes(
    tmp_path,
    monkeypatch,
    sandbox_enabled,
) -> None:
    monkeypatch.setenv("DAN_SANDBOX_SHELL", sandbox_enabled)
    monkeypatch.delenv("DAN_SANDBOX_TIMEOUT", raising=False)
    monkeypatch.delenv("DAN_SHELL_ALLOW", raising=False)
    pid_file = tmp_path / "child.pid"
    child_code = (
        "import os, pathlib, time; "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
        "time.sleep(30)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)}"

    from dan.tools.shell_command import shell_command

    result = await shell_command(
        command=command,
        working_directory=str(tmp_path),
        timeout=1,
    )

    assert result["exit_code"] == -1
    assert "timed out" in result["stderr"].lower()
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    await _wait_for_pid_exit(child_pid)


@pytest.mark.asyncio
async def test_tool_loop_reuses_unchanged_file_read_without_repeating_tool_events(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "context.txt").write_text(
        "stable context\n", encoding="utf-8"
    )
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider = ToolLoopCompletionProvider(
        provider=_RepeatedFileReadProvider(),
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=4,
        max_tool_calls=4,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Read the same file twice and return JSON.",
            user_prompt="Read twice.",
            metadata={"worker_id": "worker-cache"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ],
        )
    )

    event_names = [str(event.get("event") or "") for event in events]
    assert json.loads(response.text) == {"candidate_id": "cached-read"}
    assert event_names.count("tool.completed") == 1
    assert event_names.count("tool.cache_hit") == 1
    assert response.raw["executed_tools"][1]["cache_hit"] is True


@pytest.mark.asyncio
async def test_tool_loop_applies_checkpoint_operator_update_before_next_model_round(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "context.txt").write_text("initial scope\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_read"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _CheckpointSteeringProvider()
    polls = 0
    acknowledgements: list[tuple[list[str], str]] = []

    def _operator_messages():
        nonlocal polls
        polls += 1
        if polls != 2:
            return []
        return [
            {
                "queue_item_id": "queue-steer-1",
                "checkpoint": "tool.completed:file_read",
                "text": "Also cover keyboard navigation and validate the result with pytest.",
                "operator_context": {
                    "target_paths": ["README.md"],
                    "validation_requirements": ["pytest"],
                },
            }
        ]

    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=3,
        max_tool_calls=3,
        event_callback=events.append,
        operator_message_provider=_operator_messages,
        operator_message_acknowledger=lambda queue_item_ids, model_call_id: (
            acknowledgements.append((list(queue_item_ids), model_call_id))
        ),
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Inspect the workspace and return JSON.",
            user_prompt="Read the current scope.",
            metadata={"worker_id": "worker-steer"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ],
        )
    )

    assert json.loads(response.text)["candidate_id"] == "steered"
    assert len(provider_impl.calls) == 2
    second_messages = json.dumps(provider_impl.calls[1]["messages"])
    assert "keyboard navigation" in second_messages
    assert "validate the result with pytest" in second_messages
    assert "README.md" in second_messages
    applied = next(
        event
        for event in events
        if event.get("event") == "toolloop.operator_messages.applied"
    )
    assert applied["queue_item_ids"] == ["queue-steer-1"]
    assert applied["checkpoints"] == ["tool.completed:file_read"]
    assert acknowledgements == [(["queue-steer-1"], "model-call:0002")]
    delivered = next(
        event
        for event in events
        if event.get("event") == "toolloop.operator_messages.delivered"
    )
    assert delivered["queue_item_ids"] == ["queue-steer-1"]
    assert delivered["model_call_id"] == "model-call:0002"


def test_file_edit_stale_old_string_nudge_prefers_line_range_not_rewrite() -> None:
    message = local_runtime_module._tool_argument_failure_nudge(
        "file_edit",
        (
            "ValueError: tool_arguments_invalid: invalid arguments for file_edit: "
            "old_string was not found in the target file. Provide start_line/end_line explicitly."
        ),
    )

    assert message is not None
    assert "old_string anchor is stale or ambiguous" in message
    assert "Re-read the target file with a focused line range" in message
    assert "do not switch to a whole-file rewrite" in message


def test_source_structure_failure_nudge_allows_focused_recovery() -> None:
    message = local_runtime_module._source_structure_failure_nudge(
        "file_edit",
        (
            "ValueError: invalid edit shape for file_edit: suspicious source structure after edit: "
            "duplicate function definitions [profile]: spawn"
        ),
        {"path": "src/core/store.gd", "start_line": 140, "end_line": 160},
    )

    assert message is not None
    assert "do not retry the same patch" in message
    assert "one focused `file_read`" in message
    assert "If the requested invariant in that file already holds" in message
    assert "do not paste a second copy" in message


def test_source_structure_failure_nudge_covers_empty_control_blocks() -> None:
    message = local_runtime_module._source_structure_failure_nudge(
        "file_edit",
        "ValueError: invalid edit shape for file_edit: empty control block at line 295",
        {"path": "src/app.py", "start_line": 280, "end_line": 320},
    )

    assert message is not None
    assert "one focused `file_read`" in message
    assert "smaller line-range edit" in message


def test_source_structure_failure_nudge_covers_unexpected_indentation() -> None:
    message = local_runtime_module._source_structure_failure_nudge(
        "file_edit",
        "ValueError: invalid edit shape for file_edit: unexpected indentation at line(s): 157",
        {"path": "src/app.py", "start_line": 150, "end_line": 170},
    )

    assert message is not None
    assert "do not retry the same patch" in message
    assert "one focused `file_read`" in message


def test_100_cell_showcase_distribution_is_still_available() -> None:
    distribution = resolve_super_organism_distribution(100)

    assert distribution == {
        "brain": 6,
        "scout": 24,
        "claim": 20,
        "immune": 18,
        "memory": 12,
        "experiment": 10,
        "synthesis": 10,
    }


def test_scaled_distribution_keeps_all_organs_present() -> None:
    distribution = resolve_super_organism_distribution(14)

    assert sum(distribution.values()) == 14
    assert set(distribution) == {
        "brain",
        "scout",
        "claim",
        "immune",
        "memory",
        "experiment",
        "synthesis",
    }
    assert all(value >= 1 for value in distribution.values())


def test_too_few_cells_is_rejected() -> None:
    with pytest.raises(ValueError, match="cell_count must be at least"):
        resolve_super_organism_distribution(6)


def test_super_organism_report_shows_organized_synergy() -> None:
    report = run_super_organism_demo("LangGraph")

    assert report.status == "completed"
    assert report.mode == "deterministic_demo"
    assert report.scenario == SuperOrganismScenario.UNIVERSAL_AGENT
    assert report.cell_count == 20
    assert report.active_cell_cap == DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP
    assert report.max_active_observed <= DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP
    assert report.organ_counts["brain"] == 1
    assert report.organ_counts["scout"] == 5
    assert report.signal_counts["resource_request"] == 1
    assert report.signal_counts["reallocation"] == 2
    assert "brain_reallocation" in report.stage_sequence
    assert report.claim_graph == []
    assert len(report.delivery_plan) == 8
    assert report.shared_board is not None
    assert len(report.coordination_tickets) == 8
    assert len(report.handoff_packets) == 7
    assert report.final_audit is not None
    assert report.final_audit.status == "continue"
    assert report.final_audit.satisfied is False
    assert report.shared_board.waiting_ticket_ids == [
        "ticket-006",
        "ticket-007",
        "ticket-008",
    ]
    assert report.shared_board.pending_packet_ids == ["packet-006", "packet-007"]
    assert len(report.reallocation_decisions) == 2
    assert sum(1 for cell in report.cells if cell.status == "retired") == 2
    assert "LangGraph" in report.final_memo
    assert "Board tickets: 8." in report.final_memo
    assert (
        "deterministic coordination demo" in report.caveat
        or "deterministic" in report.caveat
    )


def test_default_super_organism_is_universal_agent_showcase() -> None:
    report = run_super_organism_demo()

    assert report.scenario == SuperOrganismScenario.UNIVERSAL_AGENT
    assert report.score_label == "Execution Readiness Score"
    assert report.final_verdict == "universal-agent execution contract ready"
    assert report.claim_graph == []
    assert len(report.delivery_plan) == 8
    assert "operator objective" in report.target


def test_scenario_resolver_compatibility_always_returns_universal_agent() -> None:
    assert resolve_super_organism_scenario("") == SuperOrganismScenario.UNIVERSAL_AGENT
    assert (
        resolve_super_organism_scenario(
            "please build our product website with cool animation dynamic effects"
        )
        == SuperOrganismScenario.UNIVERSAL_AGENT
    )
    assert (
        resolve_super_organism_scenario("LangGraph")
        == SuperOrganismScenario.UNIVERSAL_AGENT
    )
    assert (
        resolve_super_organism_scenario("audit LangGraph benchmark claims")
        == SuperOrganismScenario.UNIVERSAL_AGENT
    )
    assert (
        resolve_super_organism_scenario("LangGraph", scenario="universal-agent")
        == SuperOrganismScenario.UNIVERSAL_AGENT
    )


def test_universal_agent_report_uses_objective_contract_not_claim_audit() -> None:
    target = "please build our product website. make it cool, with cool animation dynamic effects"

    report = run_super_organism_demo(target)

    assert report.scenario == SuperOrganismScenario.UNIVERSAL_AGENT
    assert report.score_label == "Execution Readiness Score"
    assert report.final_verdict == "universal-agent execution contract ready"
    assert report.claim_graph == []
    assert len(report.delivery_plan) == 8
    assert report.signal_counts["resource_request"] == 1
    assert report.signal_counts["reallocation"] == 2
    assert "execution_probe" in report.stage_sequence
    assert any(node.title == "Native execution lane" for node in report.delivery_plan)
    assert any(node.status == "live_build_required" for node in report.delivery_plan)
    assert report.coordination_tickets[5].ticket_id == "ticket-006"
    assert report.coordination_tickets[5].status == "awaiting_live_execution"
    assert report.handoff_packets[-1].status == "pending"
    assert report.final_audit is not None
    assert report.final_audit.blocker_ticket_ids == [
        "ticket-006",
        "ticket-007",
        "ticket-008",
    ]
    assert "production-ready for complex agent orchestration" not in report.final_memo
    assert "accepts the objective" in report.final_memo


def test_super_organism_normalizes_pasted_multiline_objective() -> None:
    report = run_super_organism_demo(
        "please build our product\n"
        "  website. make it cool, with cool animation dynamic\n"
        "  effects"
    )

    assert report.target == (
        "please build our product website. make it cool, "
        "with cool animation dynamic effects"
    )
    assert "\n" not in report.final_memo


def test_active_cell_cap_controls_scheduler_waves() -> None:
    report = run_super_organism_demo("CrewAI", cell_count=100, active_cell_cap=7)

    assert report.active_cell_cap == 7
    assert report.max_active_observed <= 7
    assert any(len(wave.cell_ids) == 7 for wave in report.activity_waves)
    assert any(
        signal.phase == "contract_immune_check" for signal in report.board_signals
    )


def test_super_organism_shared_board_accounts_for_all_cells() -> None:
    report = run_super_organism_demo("build a cool website for this product")

    assert report.shared_board is not None
    accounted = set(report.shared_board.reserve_cell_ids)
    for ticket in report.coordination_tickets:
        accounted.update(ticket.cell_ids)

    assert accounted == {cell.cell_id for cell in report.cells}
    assert any(
        ticket.owner_cell_id == "brain-001"
        for ticket in report.coordination_tickets[:2]
    )
    assert report.shared_board.reserve_cell_ids == ["scout-005"]


def test_runtime_downshift_detects_truncated_existing_file_overwrite(tmp_path) -> None:
    (tmp_path / "module.py").write_text(
        "def alpha():\n    return 1\n", encoding="utf-8"
    )
    raw_arguments = '{"path":"module.py","content":"def alpha():\\n' "    return 10\\n"
    raw_call = {
        "function": {
            "name": "file_write",
            "arguments": raw_arguments,
        }
    }

    downshift = local_runtime_module._file_write_invalid_large_overwrite_downshift(
        raw_call,
        arguments={"raw_arguments": raw_arguments},
        finish_reason="length",
        workspace_root=tmp_path,
        file_edit_available=True,
    )

    assert downshift == ("module.py", "model_output_truncated")
    message = local_runtime_module._file_write_incremental_edit_required_message(
        "module.py",
        "model_output_truncated",
    )
    assert "`file_write` to existing file `module.py` is blocked" in message
    assert "Downshift now: use `file_edit`" in message


def test_super_dan_soft_budget_extends_only_for_real_progress() -> None:
    progress = {
        "distinct_read_paths": 5,
        "mutation_paths": 0,
        "verification_commands": 0,
        "discovery_tools": 5,
        "shell_commands": 0,
    }

    tool_limit = local_runtime_module._soft_budget_limit(
        "coding_prewrite",
        limit_kind="tool_calls",
        hard_limit=24,
        progress=progress,
    )
    round_limit = local_runtime_module._soft_budget_limit(
        "coding_prewrite",
        limit_kind="rounds",
        hard_limit=8,
        progress=progress,
    )

    assert tool_limit == 10
    assert round_limit == 5


def test_interactive_source_requests_force_earlier_first_write() -> None:
    normal_request = CompletionRequest(
        model="fake",
        system_prompt="",
        user_prompt="Build a project.",
        metadata={"organism_stage": "execution"},
    )
    interactive_request = CompletionRequest(
        model="fake",
        system_prompt="",
        user_prompt="Build an interactive project demo.",
        metadata={
            "organism_stage": "execution",
            "interactive_source_implementation": True,
        },
    )

    assert (
        local_runtime_module._prewrite_successful_read_nudge_threshold(normal_request)
        == 3
    )
    assert (
        local_runtime_module._prewrite_successful_read_nudge_threshold(
            interactive_request
        )
        == 2
    )


def test_operator_intent_policy_narrows_runtime_tools_before_model() -> None:
    request = CompletionRequest(
        model="fake",
        system_prompt="",
        user_prompt="What is this project?",
        metadata={
            "operator_intent_policy": {
                "active": True,
                "allow_workspace_mutation": False,
                "allow_shell_command": False,
            }
        },
    )
    tools = [
        {"type": "function", "function": {"name": "file_read"}},
        {"type": "function", "function": {"name": "file_edit"}},
        {"type": "function", "function": {"name": "file_write"}},
        {"type": "function", "function": {"name": "shell_command"}},
        {"type": "function", "function": {"name": "workspace_check"}},
    ]

    narrowed = local_runtime_module._operator_intent_tool_schemas(request, tools)

    assert [local_runtime_module._tool_schema_name(tool) for tool in narrowed] == [
        "file_read",
        "workspace_check",
    ]


def test_operator_intent_policy_suppresses_first_write_nudge(tmp_path) -> None:
    request = CompletionRequest(
        model="fake",
        system_prompt="",
        user_prompt="What is this project?",
        output_contract=OutputContract(
            expected_return_shape=json.dumps(
                {
                    "candidate_id": "",
                    "change_summary": [],
                    "target_files": [],
                    "test_plan": [],
                    "risks": [],
                }
            )
        ),
        metadata={
            "organism_stage": "execution",
            "operator_intent_policy": {
                "active": True,
                "allow_workspace_mutation": False,
                "allow_shell_command": False,
            },
        },
    )
    executed_tools = [
        {
            "ok": True,
            "tool_id": "file_read",
            "arguments": {"path": f"src/file_{index}.py"},
            "result": {"path": f"src/file_{index}.py"},
        }
        for index in range(3)
    ]

    reason = local_runtime_module._write_capable_coding_stage_first_write_nudge_reason(
        request=request,
        tool_ids=["file_read", "file_edit", "file_write", "shell_command"],
        executed_tools=executed_tools,
        workspace_root=tmp_path,
    )

    assert reason is None


def test_validation_repair_requires_all_required_paths_before_finalize(
    tmp_path,
) -> None:
    request = CompletionRequest(
        model="fake",
        system_prompt="Return compact JSON.",
        user_prompt="Repair the validation failure.",
        output_contract=OutputContract(
            expected_return_shape=json.dumps(
                {
                    "candidate_id": "",
                    "change_summary": [],
                    "target_files": [],
                    "test_plan": [],
                    "risks": [],
                }
            )
        ),
        metadata={
            "validation_repair": True,
            "required_repair_paths": [
                "src/core/GameLoop.gd",
                "src/core/PlayabilityValidator.gd",
            ],
        },
    )
    executed_tools = [
        {
            "ok": True,
            "tool_id": "file_edit",
            "arguments": {"path": "src/core/PlayabilityValidator.gd"},
            "result": {
                "path": "src/core/PlayabilityValidator.gd",
                "changed": True,
            },
        }
    ]

    reason = (
        local_runtime_module._write_capable_coding_stage_direct_write_required_reason(
            request=request,
            tool_ids=["file_read", "file_edit", "file_write"],
            executed_tools=executed_tools,
            workspace_root=tmp_path,
            direct_write_required=False,
        )
    )

    assert reason == "validation_repair_missing_required_paths:src/core/GameLoop.gd"
    message = (
        local_runtime_module._write_capable_coding_stage_direct_write_required_message(
            reason,
            request=request,
        )
    )
    assert (
        "validation-repair stage has not yet mutated every required repair target"
        in message
    )
    assert "src/core/GameLoop.gd" in message
    assert "Do not re-audit the whole project" in message


def test_prompt_replay_compaction_preserves_tool_call_structure_under_pressure() -> (
    None
):
    old_write_content = "old write body\n" * 500
    old_edit_content = "old edit body\n" * 500
    latest_write_content = "latest write body\n" * 500
    old_assistant_text = "old assistant analysis\n" * 300
    old_shell_stdout = "shell output\n" * 500
    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "Initial task prompt must remain exact."},
        {
            "role": "assistant",
            "content": old_assistant_text,
            "tool_calls": [
                {
                    "id": "call-old-write",
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "arguments": json.dumps(
                            {
                                "path": "notes/old.md",
                                "content": old_write_content,
                            },
                            sort_keys=True,
                        ),
                    },
                },
                {
                    "id": "call-old-edit",
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "arguments": json.dumps(
                            {
                                "path": "notes/old.md",
                                "start_line": 1,
                                "end_line": 2,
                                "content": old_edit_content,
                            },
                            sort_keys=True,
                        ),
                    },
                },
                {
                    "id": "call-old-shell",
                    "type": "function",
                    "function": {
                        "name": "shell_command",
                        "arguments": json.dumps({"command": "pytest -q"}),
                    },
                },
            ],
        },
        {
            "role": "tool",
            "name": "file_write",
            "tool_call_id": "call-old-write",
            "content": json.dumps({"ok": True, "result": {"bytes_written": 10}}),
        },
        {
            "role": "tool",
            "name": "file_edit",
            "tool_call_id": "call-old-edit",
            "content": json.dumps({"ok": True, "result": {"changed": True}}),
        },
        {
            "role": "tool",
            "name": "shell_command",
            "tool_call_id": "call-old-shell",
            "content": json.dumps(
                {"ok": True, "result": {"stdout": old_shell_stdout}},
                sort_keys=True,
            ),
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-middle-list",
                    "type": "function",
                    "function": {
                        "name": "list_directory",
                        "arguments": json.dumps({"path": "."}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "name": "list_directory",
            "tool_call_id": "call-middle-list",
            "content": json.dumps({"ok": True, "result": {"files": ["notes"]}}),
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-latest-write",
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "arguments": json.dumps(
                            {
                                "path": "notes/latest.md",
                                "content": latest_write_content,
                            },
                            sort_keys=True,
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "name": "file_write",
            "tool_call_id": "call-latest-write",
            "content": json.dumps({"ok": True, "result": {"bytes_written": 20}}),
        },
        {"role": "user", "content": "Latest validation feedback must remain exact."},
    ]

    compacted_messages, stats = (
        local_runtime_module._compact_messages_for_provider_prompt(
            messages,
            budget_chars=2_000,
            tool_schema_chars=500,
        )
    )

    assert stats["prompt_context_budget_triggered"] is True
    assert stats["prompt_context_compacted_non_file_tools"] == 1
    assert stats["prompt_context_compacted_tool_call_args"] == 2
    assert stats["prompt_context_compacted_assistant_messages"] == 1
    assert compacted_messages[1]["content"] == "Initial task prompt must remain exact."
    assert (
        compacted_messages[10]["content"]
        == "Latest validation feedback must remain exact."
    )

    old_tool_calls = compacted_messages[2]["tool_calls"]
    assert old_tool_calls[0]["id"] == "call-old-write"
    assert old_tool_calls[0]["function"]["name"] == "file_write"
    old_write_arguments = json.loads(old_tool_calls[0]["function"]["arguments"])
    assert old_write_arguments["prompt_replay_compacted"] is True
    assert old_write_arguments["retained"]["path"] == "notes/old.md"
    assert old_write_arguments["retained"]["content_chars"] == len(old_write_content)
    assert "old write body" not in old_tool_calls[0]["function"]["arguments"]

    old_edit_arguments = json.loads(old_tool_calls[1]["function"]["arguments"])
    assert old_edit_arguments["prompt_replay_compacted"] is True
    assert old_edit_arguments["retained"]["path"] == "notes/old.md"
    assert old_edit_arguments["retained"]["start_line"] == 1
    assert old_edit_arguments["retained"]["content_chars"] == len(old_edit_content)
    assert "old edit body" not in old_tool_calls[1]["function"]["arguments"]

    old_shell_payload = json.loads(str(compacted_messages[5]["content"]))
    assert old_shell_payload["prompt_context_compacted"] is True
    assert old_shell_payload["result"]["stdout"] != old_shell_stdout
    assert compacted_messages[5]["tool_call_id"] == "call-old-shell"

    assert compacted_messages[2]["content"] != old_assistant_text
    assert compacted_messages[8]["tool_calls"][0]["id"] == "call-latest-write"
    assert (
        "latest write body"
        in compacted_messages[8]["tool_calls"][0]["function"]["arguments"]
    )
    assert messages[2]["content"] == old_assistant_text
    assert "old write body" in messages[2]["tool_calls"][0]["function"]["arguments"]
    assert "old edit body" in messages[2]["tool_calls"][1]["function"]["arguments"]


@pytest.mark.asyncio
async def test_super_dan_context_length_retry_uses_emergency_prompt_compaction(
    tmp_path,
) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_write", "list_directory"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _ContextLengthAfterOldWriteProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Return compact JSON.",
            user_prompt="Create a note, inspect the workspace, then finish.",
            metadata={
                "worker_id": "super-dan-live-general.worker",
                "tool_budget_profile": "super_dan_live",
            },
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "list_directory",
                        "description": "List files.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                        },
                    },
                },
            ],
        )
    )

    assert json.loads(response.text)["candidate_id"] == "after-context-length-retry"
    assert len(provider_impl.calls) == 4
    retry_event = _first_event(events, "model.context_length_retry")
    assert retry_event["retry_attempt"] == 1
    requested_events = [
        event for event in events if event.get("event") == "model.requested"
    ]
    assert len(requested_events) == 4
    assert requested_events[-2]["prompt_context_emergency_compaction"] is False
    assert requested_events[-1]["prompt_context_emergency_compaction"] is True
    assert requested_events[-1]["prompt_context_compacted_tool_call_args"] >= 1
    assert (
        requested_events[-1]["prompt_context_final_chars"]
        < requested_events[-2]["prompt_context_final_chars"]
    )
