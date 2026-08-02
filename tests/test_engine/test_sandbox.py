"""Tests for subprocess sandbox — SandboxRunner, adapters, CodeExecutor upgrade, shell_command."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from dan.engine.executor import ExecutionContext, EngineConfig, NodeResult
from dan.engine.state import ExecutionState, NodeStatus
from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator
from dan.models.ports import InputPort, OutputPort
from dan.sandbox import SandboxConfig, SandboxResult, DEFAULT_TIMEOUT, DEFAULT_MAX_OUTPUT
from dan.sandbox.adapters import ADAPTERS, PythonAdapter, ShellAdapter
from dan.sandbox.runner import SandboxRunner, _filter_env
from dan.executors.code import CodeExecutor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMPTY_GRAPH = Graph()


def _make_context(events: list | None = None) -> ExecutionContext:
    """Minimal ExecutionContext that captures emitted events."""
    captured = events if events is not None else []

    async def _capture(event: Any) -> None:
        captured.append(event)

    return ExecutionContext(
        state=ExecutionState(graph=_EMPTY_GRAPH, run_id="test-run"),
        config=EngineConfig(checkpoint_enabled=False),
        shared_context=SharedContextStore(),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        event_callback=_capture,
        run_id="test-run",
    )


def _code_node(
    code: str,
    *,
    node_id: str = "code_1",
    language: str = "python",
    sandbox_config: dict | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
) -> CodeOperator:
    return CodeOperator(
        id=node_id,
        name=node_id,
        code=code,
        language=language,
        sandbox_config=sandbox_config or {},
        input_ports=[InputPort(name=n) for n in (inputs or [])],
        output_ports=[OutputPort(name=n) for n in (outputs or [])],
    )


# ===========================================================================
# SandboxConfig / SandboxResult
# ===========================================================================


class TestSandboxConfig:
    def test_defaults(self):
        cfg = SandboxConfig()
        assert cfg.mode == "inline"
        assert cfg.timeout_seconds == DEFAULT_TIMEOUT
        assert cfg.memory_mb is None
        assert cfg.language == "python"
        assert cfg.pass_env == []
        assert cfg.max_output_bytes == DEFAULT_MAX_OUTPUT

    def test_from_dict(self):
        cfg = SandboxConfig(**{"mode": "subprocess", "timeout_seconds": 10, "language": "shell"})
        assert cfg.mode == "subprocess"
        assert cfg.timeout_seconds == 10
        assert cfg.language == "shell"

    def test_json_round_trip(self):
        cfg = SandboxConfig(mode="subprocess", pass_env=["DAN_*"])
        data = cfg.model_dump()
        restored = SandboxConfig(**data)
        assert restored == cfg


class TestSandboxResult:
    def test_defaults(self):
        r = SandboxResult()
        assert r.stdout == ""
        assert r.stderr == ""
        assert r.exit_code == 0
        assert r.output_files == []
        assert r.duration_ms == 0.0
        assert r.memory_peak_mb is None
        assert r.truncated is False


# ===========================================================================
# PythonAdapter
# ===========================================================================


class TestPythonAdapter:
    def test_prepare_writes_script(self):
        adapter = PythonAdapter()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cmd, script_name = adapter.prepare("x = 1", SandboxConfig(), td_path)
            assert script_name == "_script.py"
            content = (td_path / "_script.py").read_text()
            assert "x = 1" in content
            assert "_inputs.json" in content
            assert "_result.json" in content
            assert cmd[0] == sys.executable
            assert "-u" in cmd

    def test_bootstrap_preamble(self):
        adapter = PythonAdapter()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            adapter.prepare("result = x + 1", SandboxConfig(), td_path)
            content = (td_path / "_script.py").read_text()
            assert "locals().update(_inputs)" in content
            assert "result = x + 1" in content
            assert "json.dump(result" in content


# ===========================================================================
# ShellAdapter
# ===========================================================================


class TestShellAdapter:
    def test_prepare_writes_executable_script(self):
        adapter = ShellAdapter()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cmd, script_name = adapter.prepare("echo hi", SandboxConfig(language="shell"), td_path)
            assert script_name == "_script.sh"
            script_path = td_path / "_script.sh"
            assert script_path.exists()
            assert os.access(str(script_path), os.X_OK)
            assert cmd == ["/bin/sh", str(script_path)]
            assert script_path.read_text() == "echo hi"


# ===========================================================================
# Environment filtering
# ===========================================================================


class TestEnvFiltering:
    def test_base_env_only(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("HOME", "/home/test")
        monkeypatch.setenv("SECRET_KEY", "s3cret")
        env = _filter_env([])
        assert "PATH" in env
        assert "HOME" in env
        assert "SECRET_KEY" not in env

    def test_exact_match(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "hello")
        env = _filter_env(["MY_VAR"])
        assert env.get("MY_VAR") == "hello"

    def test_glob_match(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "key1")
        monkeypatch.setenv("OPENAI_ORG", "org1")
        monkeypatch.setenv("OTHER_VAR", "nope")
        env = _filter_env(["OPENAI_*"])
        assert "OPENAI_API_KEY" in env
        assert "OPENAI_ORG" in env
        assert "OTHER_VAR" not in env


# ===========================================================================
# SandboxRunner
# ===========================================================================


class TestSandboxRunner:
    @pytest.mark.asyncio
    async def test_successful_python(self):
        runner = SandboxRunner()
        config = SandboxConfig(mode="subprocess", language="python")
        result, structured = await runner.run("result = 42", config, {})
        assert result.exit_code == 0
        assert result.duration_ms > 0
        assert structured == 42

    @pytest.mark.asyncio
    async def test_input_injection(self):
        runner = SandboxRunner()
        config = SandboxConfig(mode="subprocess", language="python")
        result, structured = await runner.run("result = x + y", config, {"x": 3, "y": 7})
        assert result.exit_code == 0
        assert structured == 10

    @pytest.mark.asyncio
    async def test_structured_dict_output(self):
        runner = SandboxRunner()
        config = SandboxConfig(mode="subprocess", language="python")
        code = 'result = {"a": 1, "b": 2}'
        result, structured = await runner.run(code, config, {})
        assert structured == {"a": 1, "b": 2}

    @pytest.mark.asyncio
    async def test_no_result_variable(self):
        runner = SandboxRunner()
        config = SandboxConfig(mode="subprocess", language="python")
        result, structured = await runner.run("x = 42", config, {})
        assert result.exit_code == 0
        assert structured is None

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        runner = SandboxRunner()
        config = SandboxConfig(mode="subprocess", language="python", timeout_seconds=1)
        result, _ = await runner.run("import time; time.sleep(60)", config, {})
        assert result.exit_code == -1
        assert "timed out" in result.stderr.lower()

    @pytest.mark.asyncio
    async def test_output_truncation(self):
        runner = SandboxRunner()
        config = SandboxConfig(mode="subprocess", language="python", max_output_bytes=50)
        code = "print('A' * 200)"
        result, _ = await runner.run(code, config, {})
        assert result.truncated is True
        assert len(result.stdout) <= 50

    @pytest.mark.asyncio
    async def test_stderr_capture(self):
        runner = SandboxRunner()
        config = SandboxConfig(mode="subprocess", language="python")
        code = "import sys; print('err msg', file=sys.stderr)"
        result, _ = await runner.run(code, config, {})
        assert "err msg" in result.stderr

    @pytest.mark.asyncio
    async def test_nonzero_exit(self):
        runner = SandboxRunner()
        config = SandboxConfig(mode="subprocess", language="python")
        code = "import sys; sys.exit(42)"
        result, _ = await runner.run(code, config, {})
        assert result.exit_code == 42

    @pytest.mark.asyncio
    async def test_shell_execution(self):
        runner = SandboxRunner()
        config = SandboxConfig(mode="subprocess", language="shell")
        result, _ = await runner.run("echo hello world", config, {})
        assert result.exit_code == 0
        assert "hello world" in result.stdout

    @pytest.mark.asyncio
    async def test_shell_env_inputs(self):
        runner = SandboxRunner()
        config = SandboxConfig(mode="subprocess", language="shell")
        result, _ = await runner.run("echo $MY_INPUT", config, {"MY_INPUT": "injected"})
        assert "injected" in result.stdout

    @pytest.mark.asyncio
    async def test_shell_exit_code(self):
        runner = SandboxRunner()
        config = SandboxConfig(mode="subprocess", language="shell")
        result, _ = await runner.run("exit 7", config, {})
        assert result.exit_code == 7

    @pytest.mark.asyncio
    async def test_unsupported_language(self):
        runner = SandboxRunner()
        config = SandboxConfig(mode="subprocess", language="cobol")
        result, structured = await runner.run("DISPLAY 'HI'", config, {})
        assert result.exit_code == -1
        assert "cobol" in result.stderr.lower()
        assert structured is None

    @pytest.mark.asyncio
    async def test_temp_dir_cleanup(self):
        runner = SandboxRunner()
        config = SandboxConfig(mode="subprocess", language="python")
        import tempfile as _tf
        dirs_before = set(Path(_tf.gettempdir()).glob("dan_sandbox_*"))
        await runner.run("result = 1", config, {})
        dirs_after = set(Path(_tf.gettempdir()).glob("dan_sandbox_*"))
        new_dirs = dirs_after - dirs_before
        assert len(new_dirs) == 0, f"Leaked temp dirs: {new_dirs}"


# ===========================================================================
# Resource limits (best-effort, platform-dependent)
# ===========================================================================


class TestResourceLimits:
    @pytest.mark.asyncio
    @pytest.mark.skipif(sys.platform == "win32", reason="resource module unavailable on Windows")
    async def test_memory_limit_set(self):
        """Verify memory limit is attempted (may not be enforced on macOS)."""
        runner = SandboxRunner()
        config = SandboxConfig(
            mode="subprocess", language="python", memory_mb=256,
        )
        code = "result = 'ok'"
        result, structured = await runner.run(code, config, {})
        assert result.exit_code == 0
        assert structured == "ok"


# ===========================================================================
# CodeExecutor — inline mode (regression)
# ===========================================================================


class TestCodeExecutorInline:
    @pytest.mark.asyncio
    async def test_simple_code(self):
        executor = CodeExecutor()
        node = _code_node("result = {'value': 42}")
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.COMPLETED
        assert nr.outputs == {"value": 42, "result": {"value": 42}}

    @pytest.mark.asyncio
    async def test_with_inputs(self):
        executor = CodeExecutor()
        node = _code_node("result = x * 2", inputs=["x"])
        ctx = _make_context()
        nr = await executor.execute(node, {"x": 5}, ctx)
        assert nr.outputs == {"result": 10}

    @pytest.mark.asyncio
    async def test_inline_unsupported_language(self):
        executor = CodeExecutor()
        node = _code_node("console.log('hi')", language="javascript")
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.FAILED
        assert "javascript" in nr.error.lower()

    @pytest.mark.asyncio
    async def test_inline_error(self):
        executor = CodeExecutor()
        node = _code_node("x = 1 / 0")
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.FAILED
        assert "division by zero" in nr.error

    @pytest.mark.asyncio
    async def test_empty_sandbox_config_uses_inline(self):
        executor = CodeExecutor()
        node = _code_node("result = 99", sandbox_config={})
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.COMPLETED
        assert nr.outputs == {"result": 99}

    @pytest.mark.asyncio
    async def test_stdout_event_emitted(self):
        events: list = []
        executor = CodeExecutor()
        node = _code_node("print('hello from code')\nresult = 1")
        ctx = _make_context(events)
        await executor.execute(node, {}, ctx)
        code_events = [e for e in events if e.event_type.value == "code_output"]
        assert len(code_events) == 1
        assert "hello from code" in code_events[0].data["stdout"]

    @pytest.mark.asyncio
    async def test_relative_open_uses_workspace_root(self, monkeypatch, tmp_path: Path):
        workspace_file = tmp_path / "watchlist.csv"
        workspace_file.write_text("ticker\nRKLB\n", encoding="utf-8")
        monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(tmp_path))

        executor = CodeExecutor()
        node = _code_node("with open('watchlist.csv', 'r') as f:\n    result = f.read()")
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)

        assert nr.status == NodeStatus.COMPLETED
        assert nr.outputs == {"result": "ticker\nRKLB\n"}


# ===========================================================================
# CodeExecutor — subprocess mode
# ===========================================================================


class TestCodeExecutorSubprocess:
    @pytest.mark.asyncio
    async def test_python_subprocess(self):
        executor = CodeExecutor()
        node = _code_node(
            "result = x + 1",
            sandbox_config={"mode": "subprocess"},
            inputs=["x"],
        )
        ctx = _make_context()
        nr = await executor.execute(node, {"x": 10}, ctx)
        assert nr.status == NodeStatus.COMPLETED
        assert nr.outputs == {"result": 11}

    @pytest.mark.asyncio
    async def test_shell_subprocess(self):
        executor = CodeExecutor()
        node = _code_node(
            "echo done",
            language="shell",
            sandbox_config={"mode": "subprocess", "language": "shell"},
        )
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_subprocess_timeout(self):
        executor = CodeExecutor()
        node = _code_node(
            "import time; time.sleep(60)",
            sandbox_config={"mode": "subprocess", "timeout_seconds": 1},
        )
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.FAILED
        assert "timed out" in nr.error.lower()

    @pytest.mark.asyncio
    async def test_subprocess_error(self):
        executor = CodeExecutor()
        node = _code_node(
            "import sys; sys.exit(1)",
            sandbox_config={"mode": "subprocess"},
        )
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.FAILED

    @pytest.mark.asyncio
    async def test_subprocess_structured_output(self):
        executor = CodeExecutor()
        node = _code_node(
            'result = {"key": "value", "num": 42}',
            sandbox_config={"mode": "subprocess"},
        )
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.COMPLETED
        assert nr.outputs == {"key": "value", "num": 42, "result": {"key": "value", "num": 42}}

    @pytest.mark.asyncio
    async def test_subprocess_no_result(self):
        executor = CodeExecutor()
        node = _code_node(
            "x = 42",
            sandbox_config={"mode": "subprocess"},
        )
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.COMPLETED
        assert nr.outputs == {}


# ===========================================================================
# Event emission
# ===========================================================================


class TestSandboxEvents:
    @pytest.mark.asyncio
    async def test_sandbox_events_emitted(self):
        events: list = []
        executor = CodeExecutor()
        node = _code_node("result = 1", sandbox_config={"mode": "subprocess"})
        ctx = _make_context(events)
        await executor.execute(node, {}, ctx)

        event_types = [e.event_type.value for e in events]
        assert "sandbox_started" in event_types
        assert "sandbox_completed" in event_types

    @pytest.mark.asyncio
    async def test_sandbox_started_data(self):
        events: list = []
        executor = CodeExecutor()
        node = _code_node(
            "result = 1",
            sandbox_config={"mode": "subprocess", "timeout_seconds": 15},
        )
        ctx = _make_context(events)
        await executor.execute(node, {}, ctx)

        started = [e for e in events if e.event_type.value == "sandbox_started"][0]
        assert started.data["language"] == "python"
        assert started.data["mode"] == "subprocess"
        assert started.data["timeout_seconds"] == 15

    @pytest.mark.asyncio
    async def test_sandbox_completed_data(self):
        events: list = []
        executor = CodeExecutor()
        node = _code_node("result = 1", sandbox_config={"mode": "subprocess"})
        ctx = _make_context(events)
        await executor.execute(node, {}, ctx)

        completed = [e for e in events if e.event_type.value == "sandbox_completed"][0]
        assert completed.data["exit_code"] == 0
        assert completed.data["duration_ms"] > 0
        assert completed.data["truncated"] is False

    @pytest.mark.asyncio
    async def test_no_sandbox_events_for_inline(self):
        events: list = []
        executor = CodeExecutor()
        node = _code_node("result = 1")
        ctx = _make_context(events)
        await executor.execute(node, {}, ctx)

        sandbox_events = [
            e for e in events
            if e.event_type.value in ("sandbox_started", "sandbox_completed")
        ]
        assert sandbox_events == []


# ===========================================================================
# shell_command tool
# ===========================================================================


class TestShellCommandTool:
    @pytest.mark.asyncio
    async def test_backward_compat_no_sandbox(self, monkeypatch):
        monkeypatch.delenv("DAN_SANDBOX_SHELL", raising=False)
        from dan.tools.shell_command import shell_command
        result = await shell_command(command="echo backward_compat")
        assert result["exit_code"] == 0
        assert "backward_compat" in result["stdout"]

    @pytest.mark.asyncio
    async def test_sandbox_mode_enabled(self, monkeypatch):
        monkeypatch.setenv("DAN_SANDBOX_SHELL", "true")
        monkeypatch.delenv("DAN_SHELL_ALLOW", raising=False)
        from dan.tools.shell_command import shell_command
        result = await shell_command(command="echo sandbox_mode")
        assert result["exit_code"] == 0
        assert "sandbox_mode" in result["stdout"]

    @pytest.mark.asyncio
    async def test_sandbox_with_timeout_env(self, monkeypatch):
        monkeypatch.setenv("DAN_SANDBOX_SHELL", "true")
        monkeypatch.setenv("DAN_SANDBOX_TIMEOUT", "2")
        monkeypatch.delenv("DAN_SHELL_ALLOW", raising=False)
        from dan.tools.shell_command import shell_command
        result = await shell_command(command="echo fast")
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_allowlist_enforced_in_sandbox(self, monkeypatch):
        monkeypatch.setenv("DAN_SANDBOX_SHELL", "true")
        monkeypatch.setenv("DAN_SHELL_ALLOW", "echo,ls")
        from dan.tools.shell_command import shell_command
        with pytest.raises(PermissionError, match="not in the allowed"):
            await shell_command(command="rm -rf /")

    @pytest.mark.asyncio
    async def test_allowlist_permits_in_sandbox(self, monkeypatch):
        monkeypatch.setenv("DAN_SANDBOX_SHELL", "true")
        monkeypatch.setenv("DAN_SHELL_ALLOW", "echo,ls")
        from dan.tools.shell_command import shell_command
        result = await shell_command(command="echo allowed_in_sandbox")
        assert result["exit_code"] == 0
        assert "allowed_in_sandbox" in result["stdout"]

# ===========================================================================
# Backward compatibility
# ===========================================================================


class TestBackwardCompat:
    @pytest.mark.asyncio
    async def test_code_node_no_sandbox_config(self):
        """CodeOperator with empty sandbox_config behaves identically to pre-sandbox."""
        executor = CodeExecutor()
        node = CodeOperator(
            id="legacy", name="legacy", code="result = {'v': 1}",
        )
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.COMPLETED
        assert nr.outputs == {"v": 1, "result": {"v": 1}}

    @pytest.mark.asyncio
    async def test_inline_mode_explicit(self):
        executor = CodeExecutor()
        node = _code_node("result = 5", sandbox_config={"mode": "inline"})
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.COMPLETED
        assert nr.outputs == {"result": 5}

    @pytest.mark.asyncio
    async def test_invalid_sandbox_config_falls_back(self):
        executor = CodeExecutor()
        node = _code_node("result = 1", sandbox_config={"mode": "invalid_mode"})
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.COMPLETED
        assert nr.outputs == {"result": 1}

    @pytest.mark.asyncio
    async def test_on_failure_skip(self):
        from dan.models.nodes import RetryPolicy
        executor = CodeExecutor()
        node = _code_node(
            "raise Exception('fail')",
            sandbox_config={"mode": "subprocess"},
        )
        node.retry_policy = RetryPolicy(on_failure="skip")
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_on_failure_halt(self):
        from dan.models.nodes import RetryPolicy
        executor = CodeExecutor()
        node = _code_node(
            "import sys; sys.exit(1)",
            sandbox_config={"mode": "subprocess"},
        )
        node.retry_policy = RetryPolicy(on_failure="halt")
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.FAILED
        assert nr.metadata.get("halt") is True


class TestCodeExecutorDictResultPort:
    """Regression: dict results must expose individual keys AND the full dict on 'result'."""

    @pytest.mark.asyncio
    async def test_inline_dict_result_port(self):
        executor = CodeExecutor()
        node = _code_node("result = {'a': 1, 'b': 2}")
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.COMPLETED
        assert nr.outputs["a"] == 1
        assert nr.outputs["b"] == 2
        assert nr.outputs["result"] == {"a": 1, "b": 2}

    @pytest.mark.asyncio
    async def test_subprocess_dict_result_port(self):
        executor = CodeExecutor()
        node = _code_node(
            "result = {'a': 1, 'b': 2}",
            sandbox_config={"mode": "subprocess"},
        )
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.COMPLETED
        assert nr.outputs["a"] == 1
        assert nr.outputs["b"] == 2
        assert nr.outputs["result"] == {"a": 1, "b": 2}


class TestCodeExecutorSingleDeclaredOutputAlias:
    """Regression: one-port code nodes should expose bare result values on that port."""

    @pytest.mark.asyncio
    async def test_inline_aliases_single_declared_output_port(self):
        executor = CodeExecutor()
        node = _code_node(
            "result = {'total_revenue': 100.0, 'total_units': 4}",
            outputs=["computed_data"],
        )
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.COMPLETED
        assert nr.outputs["result"] == {"total_revenue": 100.0, "total_units": 4}
        assert nr.outputs["computed_data"] == {"total_revenue": 100.0, "total_units": 4}

    @pytest.mark.asyncio
    async def test_subprocess_aliases_single_declared_output_port(self):
        executor = CodeExecutor()
        node = _code_node(
            "result = {'total_revenue': 100.0, 'total_units': 4}",
            sandbox_config={"mode": "subprocess"},
            outputs=["computed_data"],
        )
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.COMPLETED
        assert nr.outputs["result"] == {"total_revenue": 100.0, "total_units": 4}
        assert nr.outputs["computed_data"] == {"total_revenue": 100.0, "total_units": 4}


class TestCodeExecutorBuiltinCoverage:
    """Regression: commonly generated builtin patterns should run in sandbox."""

    @pytest.mark.asyncio
    async def test_inline_supports_iter_and_next(self):
        executor = CodeExecutor()
        node = _code_node(
            "it = iter([3, 4, 5])\nfirst = next(it)\nresult = {'first': first}",
            sandbox_config={"mode": "inline"},
        )
        ctx = _make_context()
        nr = await executor.execute(node, {}, ctx)
        assert nr.status == NodeStatus.COMPLETED
        assert nr.outputs["first"] == 3
