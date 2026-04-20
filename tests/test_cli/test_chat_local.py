"""Tests for Plan 26-1: Local Chat and Launcher.

Covers:
- PID file management (up.py / down.py)
- LocalChatRuntime interface parity with ChatClient
- Local graph operations (get, list, create, save, apply mutation)
- _scratch bootstrap
- Local fallback detection in chat.py main()
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# PID file management (dan up / dan down)
# ---------------------------------------------------------------------------


class TestPidFileManagement:
    """Tests for up.py PID file helpers."""

    def test_read_pid_file_missing(self, tmp_path: Path) -> None:
        from dan.cli.up import read_pid_file, PID_FILE

        with patch("dan.cli.up.PID_FILE", tmp_path / "server.pid"):
            pid, port = read_pid_file()
        assert pid is None
        assert port is None

    def test_write_and_read_pid_file(self, tmp_path: Path) -> None:
        from dan.cli.up import write_pid_file, read_pid_file

        pid_file = tmp_path / "server.pid"
        with (
            patch("dan.cli.up.PID_FILE", pid_file),
            patch("dan.cli.up.DAN_DIR", tmp_path),
        ):
            write_pid_file(12345, 9000)
            pid, port = read_pid_file()
        assert pid == 12345
        assert port == 9000

    def test_read_pid_file_default_port(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "server.pid"
        pid_file.write_text("42\n")
        with patch("dan.cli.up.PID_FILE", pid_file):
            from dan.cli.up import read_pid_file
            pid, port = read_pid_file()
        assert pid == 42
        assert port == 8000

    def test_read_pid_file_corrupt(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "server.pid"
        pid_file.write_text("not-a-number\n")
        with patch("dan.cli.up.PID_FILE", pid_file):
            from dan.cli.up import read_pid_file
            pid, port = read_pid_file()
        assert pid is None
        assert port is None

    def test_remove_pid_file(self, tmp_path: Path) -> None:
        from dan.cli.up import remove_pid_file

        pid_file = tmp_path / "server.pid"
        pid_file.write_text("123\n8000\n")
        with patch("dan.cli.up.PID_FILE", pid_file):
            remove_pid_file()
        assert not pid_file.exists()

    def test_remove_pid_file_missing(self, tmp_path: Path) -> None:
        from dan.cli.up import remove_pid_file

        with patch("dan.cli.up.PID_FILE", tmp_path / "no-such-file.pid"):
            remove_pid_file()  # should not raise

    def test_is_process_alive_current(self) -> None:
        from dan.cli.up import is_process_alive

        assert is_process_alive(os.getpid()) is True

    def test_is_process_alive_nonexistent(self) -> None:
        from dan.cli.up import is_process_alive

        assert is_process_alive(99999999) is False

    def test_stale_pid_detection(self, tmp_path: Path) -> None:
        """PID file exists but process is dead → stale."""
        from dan.cli.up import read_pid_file, is_process_alive

        pid_file = tmp_path / "server.pid"
        pid_file.write_text("99999999\n8000\n")
        with patch("dan.cli.up.PID_FILE", pid_file):
            pid, port = read_pid_file()
        assert pid == 99999999
        assert is_process_alive(pid) is False

    def test_start_lock_prevents_concurrent_dan_up(self, tmp_path: Path) -> None:
        from dan.cli.up import acquire_start_lock, release_start_lock

        lock_path = tmp_path / "server.lock"
        with (
            patch("dan.cli.up.DAN_DIR", tmp_path),
            patch("dan.cli.up.LOCK_FILE", lock_path),
        ):
            lock1 = acquire_start_lock()
            assert lock1 is not None
            lock2 = acquire_start_lock()
            assert lock2 is None
            release_start_lock(lock1)
            lock3 = acquire_start_lock()
            assert lock3 is not None
            release_start_lock(lock3)


# ---------------------------------------------------------------------------
# dan down
# ---------------------------------------------------------------------------


class TestDanDown:
    """Tests for down.py."""

    def test_down_no_pid_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from dan.cli.down import main

        with patch("dan.cli.down.PID_FILE", tmp_path / "missing.pid"):
            main()
        assert "No DAN server PID file found" in capsys.readouterr().out

    def test_down_invalid_pid_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from dan.cli.down import main

        pid_file = tmp_path / "server.pid"
        pid_file.write_text("garbage\n")
        with patch("dan.cli.down.PID_FILE", pid_file):
            main()
        assert "Invalid PID file" in capsys.readouterr().err

    def test_down_stale_pid(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from dan.cli.down import main

        pid_file = tmp_path / "server.pid"
        pid_file.write_text("99999999\n8000\n")
        with patch("dan.cli.down.PID_FILE", pid_file):
            main()
        out = capsys.readouterr().out
        assert "not running" in out
        assert not pid_file.exists()


# ---------------------------------------------------------------------------
# LocalChatRuntime — basic interface
# ---------------------------------------------------------------------------


def _make_runtime_with_tmp(tmp_path: Path) -> "LocalChatRuntime":
    """Create a LocalChatRuntime pointing at a tmp directory."""
    from dan.cli.chat_local import LocalChatRuntime

    rt = LocalChatRuntime()
    # Override the lazy init to use tmp_path
    graphs_dir = tmp_path / "graphs"
    graphs_dir.mkdir(parents=True, exist_ok=True)

    from dan.server.graph_store import GraphStore

    gs = GraphStore(base_dir=str(graphs_dir))
    rt._services = MagicMock()
    rt._services.graph_store = gs
    rt._services.chat_store = MagicMock()
    rt._services.chat_manager = MagicMock()
    rt._services.run_manager = MagicMock()
    rt._services.mention_resolver = None
    rt._services.engine_config = None
    rt._initialized = True
    return rt


class TestLocalChatRuntimePing:
    @pytest.mark.asyncio
    async def test_ping_returns_true(self) -> None:
        from dan.cli.chat_local import LocalChatRuntime

        rt = LocalChatRuntime()
        ok, err = await rt.ping()
        assert ok is True
        assert err is None


class TestLocalChatRuntimeGraphOps:
    @pytest.mark.asyncio
    async def test_get_graph_delegates_to_graph_store(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        rt._services.graph_store.save_graph("test_wf", {"nodes": [], "edges": []})

        result = await rt.get_graph("test_wf")
        assert result is not None
        assert result["nodes"] == []
        assert result["edges"] == []

    @pytest.mark.asyncio
    async def test_get_graph_returns_none_for_missing(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        result = await rt.get_graph("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_graphs_delegates(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        rt._services.graph_store.save_graph("alpha", {"nodes": [], "edges": []})
        rt._services.graph_store.save_graph("beta", {"nodes": [], "edges": []})

        graphs = await rt.list_graphs()
        ids = {g["graph_id"] for g in graphs}
        assert "alpha" in ids
        assert "beta" in ids

    @pytest.mark.asyncio
    async def test_create_graph(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        result = await rt.create_graph("new_wf")
        assert result["graph_id"] == "new_wf"
        stored = await rt.get_graph("new_wf")
        assert stored is not None

    @pytest.mark.asyncio
    async def test_create_graph_duplicate_raises(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        await rt.create_graph("dup")
        with pytest.raises(RuntimeError, match="already exists"):
            await rt.create_graph("dup")

    @pytest.mark.asyncio
    async def test_save_graph(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        rt._services.graph_store.save_graph("wf1", {"nodes": [], "edges": []})
        await rt.save_graph(
            "wf1",
            {
                "nodes": [
                    {
                        "id": "n1",
                        "node_type": "llm_operator",
                        "name": "n1",
                        "model": "gpt-4o",
                        "prompt_template": "hello",
                    }
                ],
                "entry_points": ["n1"],
                "exit_points": ["n1"],
                "edges": [],
            },
        )
        result = await rt.get_graph("wf1")
        assert len(result["nodes"]) == 1


class TestLocalChatRuntimeBootstrap:
    @pytest.mark.asyncio
    async def test_scratch_graph_bootstrapped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """_ensure_init creates _scratch graph if missing."""
        from dan.cli.chat_local import LocalChatRuntime

        local_root = tmp_path / "local"
        monkeypatch.setattr("dan.cli.chat_local.LOCAL_ROOT", local_root)
        monkeypatch.setenv("DAN_LLM_API_KEY", "test-key")

        rt = LocalChatRuntime()
        await rt._ensure_init()

        scratch = rt._services.graph_store.get_graph("_scratch")
        assert scratch is not None
        assert scratch["nodes"] == []
        assert scratch["edges"] == []

    @pytest.mark.asyncio
    async def test_ensure_init_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from dan.cli.chat_local import LocalChatRuntime

        local_root = tmp_path / "local"
        monkeypatch.setattr("dan.cli.chat_local.LOCAL_ROOT", local_root)
        monkeypatch.setenv("DAN_LLM_API_KEY", "test-key")
        monkeypatch.setattr(
            "dan.server.concierge.project_store.Path.home",
            lambda: (_ for _ in ()).throw(AssertionError("home() should not be used")),
        )

        rt = LocalChatRuntime()
        await rt._ensure_init()
        first_services = rt._services
        await rt._ensure_init()
        assert rt._services is first_services
        assert rt._services.concierge.project_store.base_dir == local_root / "projects"

    @pytest.mark.asyncio
    async def test_ensure_init_honors_dan_local_root_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from dan.cli.chat_local import LocalChatRuntime

        env_root = tmp_path / "env-local"
        monkeypatch.setattr("dan.cli.chat_local.LOCAL_ROOT", tmp_path / "fallback-local")
        monkeypatch.setenv("DAN_LOCAL_ROOT", str(env_root))
        monkeypatch.setenv("DAN_LLM_API_KEY", "test-key")

        rt = LocalChatRuntime()
        await rt._ensure_init()

        assert rt._services.graph_store.base_dir == env_root / "graphs"
        assert rt._services.concierge.project_store.base_dir == env_root / "projects"

    @pytest.mark.asyncio
    async def test_get_health_reports_gateway_degradation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from dan.cli.chat_local import LocalChatRuntime

        local_root = tmp_path / "local"
        monkeypatch.setattr("dan.cli.chat_local.LOCAL_ROOT", local_root)
        monkeypatch.setenv("DAN_LLM_API_KEY", "test-key")

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("gateway boom")

        monkeypatch.setattr("dan.llm_core.factory.build_gateway", _boom)

        rt = LocalChatRuntime()
        health = await rt.get_health()

        assert health["status"] == "ok"
        assert health["startup"]["status"] == "degraded"
        assert {
            "subsystem": "model_gateway",
            "message": "construction failed; shared gateway unavailable",
        } in health["startup"]["issues"]
        assert {
            "category": "transport",
            "message": "Local mode is in-process CLI only; it does not expose an HTTP or remote-client surface.",
        } in health["mode_limitations"]


# ---------------------------------------------------------------------------
# Mutation apply
# ---------------------------------------------------------------------------


class TestLocalChatRuntimeMutation:
    @pytest.mark.asyncio
    async def test_apply_mutation_success(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        rt._services.graph_store.save_graph("wf", {"nodes": [], "edges": []})

        mutation_plan = {
            "operations": [
                {
                    "op": "add_node",
                    "id": "new_node",
                    "name": "New Node",
                    "node_type": "llm_operator",
                    "config": {"prompt_template": "hello"},
                }
            ]
        }
        result = await rt.apply_mutation("wf", mutation_plan)
        assert result["success"] is True
        assert result.get("graph_revision")

        stored = await rt.get_graph("wf")
        node_ids = [n["id"] for n in stored["nodes"]]
        assert "new_node" in node_ids

    @pytest.mark.asyncio
    async def test_apply_mutation_missing_graph(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        result = await rt.apply_mutation("nonexistent", {"operations": []})
        assert result["success"] is False
        assert any("not found" in e["message"] for e in result["errors"])


# ---------------------------------------------------------------------------
# send_chat_message + stream
# ---------------------------------------------------------------------------


class TestLocalChatRuntimeChat:
    @pytest.mark.asyncio
    async def test_send_chat_returns_channel_id(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        rt._services.graph_store.save_graph("wf", {"nodes": [], "edges": []})

        async def _fake_stream(**kwargs):
            yield MagicMock(model_dump=lambda: {"type": "chat_token", "token": "hi"})
            yield MagicMock(model_dump=lambda: {"type": "chat_complete"})

        rt._services.chat_manager.send_message_with_tools = _fake_stream

        result = await rt.send_chat_message("wf", "hello")
        assert "stream_channel_id" in result

        events = []
        async for ev in rt.stream_chat_events(result["stream_channel_id"]):
            events.append(ev)
        assert any(e.get("type") == "chat_token" for e in events)

    @pytest.mark.asyncio
    async def test_stream_nonexistent_channel(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        events = []
        async for ev in rt.stream_chat_events("no_such_channel"):
            events.append(ev)
        assert events == []

    @pytest.mark.asyncio
    async def test_send_run_command_missing_workflow(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        result = await rt.send_chat_message("nonexistent", "/run")
        assert result.get("type") == "run_error"

    @pytest.mark.asyncio
    async def test_send_chat_message_passes_thread_id_and_normalized_mode_to_concierge(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        rt._services.graph_store.save_graph("wf", {"nodes": [], "edges": []})

        captured: dict[str, object] = {}

        class _FakeConcierge:
            async def process(self, msg):
                captured["surface"] = msg.surface
                captured["external_id"] = msg.external_id
                captured["mode"] = msg.metadata.get("mode")
                from dan.server.chat_manager import ChatCompleteEvent

                yield ChatCompleteEvent(
                    message_id="msg-local",
                    content="ok",
                    token_usage={},
                    context_window=0,
                    graph_revision="rev",
                )

        rt._services.concierge = _FakeConcierge()
        rt._services.run_manager.list_runs = MagicMock(return_value=[])

        result = await rt.send_chat_message("wf", "hello", thread_id="thread-1", mode="auto")
        events = []
        async for ev in rt.stream_chat_events(result["stream_channel_id"]):
            events.append(ev)

        assert any(e.get("type") == "chat_complete" for e in events)
        assert captured["surface"] == "cli"
        assert captured["external_id"] == "thread-1"
        assert captured["mode"] != "auto"


# ---------------------------------------------------------------------------
# submit_human_input / cancel_run
# ---------------------------------------------------------------------------


class TestLocalChatRuntimeRunOps:
    @pytest.mark.asyncio
    async def test_submit_human_input_delegates(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        rt._services.run_manager.submit_human_input = MagicMock(return_value=True)

        result = await rt.submit_human_input("run-1", "req-1", {"response": "yes"})
        assert result is True
        rt._services.run_manager.submit_human_input.assert_called_once_with(
            "run-1", "req-1", {"response": "yes"},
        )

    @pytest.mark.asyncio
    async def test_cancel_run_delegates(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        rt._services.run_manager.cancel_run = MagicMock(return_value=True)

        result = await rt.cancel_run("run-1")
        assert result is True
        rt._services.run_manager.cancel_run.assert_called_once_with("run-1")

    @pytest.mark.asyncio
    async def test_submit_human_input_no_run_manager(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        rt._services.run_manager = None
        result = await rt.submit_human_input("run-1", "req-1", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_run_no_run_manager(self, tmp_path: Path) -> None:
        rt = _make_runtime_with_tmp(tmp_path)
        rt._services.run_manager = None
        result = await rt.cancel_run("run-1")
        assert result is False


# ---------------------------------------------------------------------------
# chat.py fallback detection
# ---------------------------------------------------------------------------


class TestChatFallbackDetection:
    def test_local_flag_in_parser(self) -> None:
        from dan.cli.chat import build_parser

        parser = build_parser()
        args = parser.parse_args(["--local"])
        assert args.local is True

    def test_local_flag_default_false(self) -> None:
        from dan.cli.chat import build_parser

        parser = build_parser()
        args = parser.parse_args([])
        assert args.local is False

    def test_confirm_flag_in_parser(self) -> None:
        from dan.cli.chat import build_parser

        parser = build_parser()
        args = parser.parse_args(["--confirm"])
        assert args.confirm is True


# ---------------------------------------------------------------------------
# chat_factory
# ---------------------------------------------------------------------------


class TestChatFactory:
    def test_build_chat_services(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAN_LLM_API_KEY", "test-key")
        monkeypatch.setenv("DAN_FULL_TOOLS", "1")
        from dan.server.chat_factory import build_chat_services

        services = build_chat_services(
            graphs_dir=str(tmp_path / "graphs"),
            workspace_root=str(tmp_path),
            project_store_base_dir=tmp_path / "projects",
        )
        assert services.graph_store is not None
        assert services.chat_store is not None
        assert services.chat_manager is not None
        assert services.run_manager is not None
        assert services.engine_config is not None
        assert services.concierge.project_store.base_dir == tmp_path / "projects"
        assert services.capability_context.chat_manager is services.chat_manager
        assert "inspect_node" in services.concierge._capability_registry.list_tool_names("agent")
        assert "python_eval" in services.concierge._capability_registry.list_tool_names("agent")
        assert services.startup_degradations == []

    def test_build_chat_services_records_gateway_degradation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DAN_LLM_API_KEY", "test-key")
        monkeypatch.setenv("DAN_FULL_TOOLS", "1")
        from dan.server.chat_factory import build_chat_services

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("gateway boom")

        monkeypatch.setattr("dan.llm_core.factory.build_gateway", _boom)

        services = build_chat_services(
            graphs_dir=str(tmp_path / "graphs"),
            workspace_root=str(tmp_path),
            project_store_base_dir=tmp_path / "projects",
        )

        assert services.model_gateway is None
        assert {
            "subsystem": "model_gateway",
            "message": "construction failed; shared gateway unavailable",
        } in services.startup_degradations

    def test_build_chat_services_degrades_when_concierge_init_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DAN_LLM_API_KEY", "test-key")
        monkeypatch.setenv("DAN_FULL_TOOLS", "1")
        from dan.server.chat_factory import build_chat_services

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("concierge boom")

        monkeypatch.setattr("dan.server.concierge.build_concierge", _boom)

        services = build_chat_services(
            graphs_dir=str(tmp_path / "graphs"),
            workspace_root=str(tmp_path),
            project_store_base_dir=tmp_path / "projects",
        )

        assert services.chat_manager is not None
        assert services.concierge is None
        assert services.dispatcher is None
        assert {
            "subsystem": "concierge",
            "message": "concierge dispatcher failed to initialize",
        } in services.startup_degradations

    def test_build_engine_config_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAN_LLM_API_KEY", "sk-test")
        monkeypatch.setenv("DAN_LLM_MODEL", "gpt-4o")
        from dan.server.chat_factory import _build_engine_config

        config = _build_engine_config()
        assert config.llm_api_key == "sk-test"
        assert config.llm_default_model == "gpt-4o"

    def test_build_tool_registry(self) -> None:
        from dan.server.chat_factory import _build_tool_registry

        registry = _build_tool_registry()
        assert registry is not None


# ---------------------------------------------------------------------------
# up.py integration (mocked subprocess)
# ---------------------------------------------------------------------------


class TestDanUp:
    def test_check_health_no_server(self) -> None:
        from dan.cli.up import check_health

        assert check_health(59999, timeout=0.5) is False

    def test_wait_for_health_timeout(self) -> None:
        from dan.cli.up import wait_for_health

        result = wait_for_health(59999, max_wait=1.0)
        assert result is False

    def test_main_exits_when_start_lock_is_held(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from dan.cli.up import acquire_start_lock, release_start_lock, main

        lock_path = tmp_path / "server.lock"
        with (
            patch("dan.cli.up.DAN_DIR", tmp_path),
            patch("dan.cli.up.LOCK_FILE", lock_path),
            patch("sys.argv", ["dan-up"]),
        ):
            held = acquire_start_lock()
            assert held is not None
            try:
                with pytest.raises(SystemExit) as exc:
                    main()
                assert exc.value.code == 1
            finally:
                release_start_lock(held)
        err = capsys.readouterr().err
        assert "already running" in err.lower()
