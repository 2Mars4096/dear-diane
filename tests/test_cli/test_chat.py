"""Tests for dan-chat CLI (Plan 21-6)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dan.engine.user_profile import UserProfile
from dan.cli.chat import (
    ChatClient,
    _close_client_quietly,
    _auto_generate_name,
    _compute_graph_revision,
    _format_graph_summary,
    _format_mutation_summary,
    _format_workflow_list,
    _InputQueue,
    _load_readline_history,
    _MAX_UNDO_DEPTH,
    _maybe_prompt_resume_workflow,
    _record_recent_workflow,
    _prompt_apply_mutation,
    _save_readline_history,
    _select_resume_workflow,
    _should_prompt_apply,
    _undo_stack,
    build_parser,
    main,
)


class TestChatCLIArgs:
    """Unit tests for CLI argument parsing."""

    def test_default_workflow_id(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.workflow_id == "_scratch"

    def test_workflow_id_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--workflow-id", "my-workflow"])
        assert args.workflow_id == "my-workflow"

    def test_scratch_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--scratch"])
        assert args.scratch is True

    def test_server_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--server", "http://custom:9000"])
        assert args.server == "http://custom:9000"

    def test_mode_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--mode", "mutate"])
        assert args.mode == "mutate"

    def test_confirm_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--confirm"])
        assert args.confirm is True

    def test_confirm_default_false(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.confirm is False


class TestUndoStack:
    """Unit tests for undo stack data structure."""

    def test_push_pop(self):
        _undo_stack.clear()
        graph = {"nodes": [{"id": "n1"}], "edges": []}
        rev = _compute_graph_revision(graph)
        _undo_stack.append((rev, graph))
        assert len(_undo_stack) == 1
        popped_rev, popped_graph = _undo_stack.pop()
        assert popped_rev == rev
        assert popped_graph["nodes"][0]["id"] == "n1"
        assert len(_undo_stack) == 0

    def test_max_depth(self):
        from dan.cli.chat import _MAX_UNDO_DEPTH

        _undo_stack.clear()
        for i in range(_MAX_UNDO_DEPTH + 3):
            g = {"nodes": [{"id": f"n{i}"}], "edges": []}
            r = _compute_graph_revision(g)
            _undo_stack.append((r, g))
            if len(_undo_stack) > _MAX_UNDO_DEPTH:
                _undo_stack.pop(0)
        assert len(_undo_stack) == _MAX_UNDO_DEPTH
        assert _undo_stack[0][1]["nodes"][0]["id"] == "n3"  # oldest kept


class TestUndoCommand:
    """Tests for /undo REPL command."""

    @pytest.mark.asyncio
    async def test_undo_empty_stack_shows_nothing_to_undo(self):
        import io

        from dan.cli.chat import _run_repl

        _undo_stack.clear()
        client = ChatClient()
        with patch.object(client, "get_graph", new_callable=AsyncMock, return_value={"nodes": [], "edges": []}):
            with patch("builtins.input", side_effect=["/undo", "/exit"]):
                with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                    await _run_repl(client, "_scratch", "build", confirm_mode=False)
        assert "Nothing to undo" in mock_stdout.getvalue()


class TestStreamHandoff:
    @pytest.mark.asyncio
    async def test_chat_complete_content_and_run_handoff(self):
        import io

        from dan.cli.chat import _run_repl

        class _FakeClient:
            def __init__(self):
                self.stream_calls: list[str] = []

            async def get_graph(self, graph_id: str):
                _ = graph_id
                return {"nodes": [], "edges": []}

            async def send_chat_message(self, *args, **kwargs):
                _ = args, kwargs
                return {"stream_channel_id": "chat-main"}

            async def stream_chat_events(self, channel_id: str):
                self.stream_calls.append(channel_id)
                if channel_id == "chat-main":
                    yield {
                        "type": "chat_complete",
                        "content": "**start_run**: Started run run-123",
                        "stream_channel_id": "run-123",
                        "graph_revision": "rev-1",
                    }
                    return
                if channel_id == "run-123":
                    yield {
                        "type": "chat_run_event",
                        "run_event": {
                            "event_type": "run_completed",
                            "summary": "Run completed successfully",
                            "detail": {"run_id": "run-123", "data": {}},
                        },
                    }
                    return

            async def submit_human_input(self, run_id: str, request_id: str, response: dict):
                _ = run_id, request_id, response
                return True

            async def cancel_run(self, run_id: str):
                _ = run_id
                return True

        client = _FakeClient()

        with patch("builtins.input", side_effect=["start run", "/exit"]):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                await _run_repl(client, "_scratch", "build", confirm_mode=False)

        output = mock_stdout.getvalue()
        assert "Started run run-123" in output
        assert "Run completed successfully" in output
        assert client.stream_calls == ["chat-main", "run-123"]

    @pytest.mark.asyncio
    async def test_bg_reader_pauses_during_human_input_prompt(self):
        import io
        import time

        from dan.cli.chat import _run_repl

        class _FakeStdin:
            closed = False

            def __init__(self):
                self.read_during_prompt = False
                self.prompt_active = False

            def readline(self):
                if self.prompt_active:
                    self.read_during_prompt = True
                return ""

        class _FakeClient:
            async def get_graph(self, graph_id: str):
                _ = graph_id
                return {"nodes": [], "edges": []}

            async def send_chat_message(self, *args, **kwargs):
                _ = args, kwargs
                return {"stream_channel_id": "chat-main"}

            async def stream_chat_events(self, channel_id: str):
                _ = channel_id
                yield {
                    "type": "chat_run_event",
                    "run_event": {
                        "event_type": "human_input_needed",
                        "summary": "Waiting for input",
                        "detail": {
                            "run_id": "run-123",
                            "data": {
                                "request_id": "req-1",
                                "render_mode": "approval",
                                "prompt": "Approve?",
                            },
                        },
                    },
                }
                yield {
                    "type": "chat_run_event",
                    "run_event": {
                        "event_type": "run_completed",
                        "summary": "Run completed successfully",
                        "detail": {"run_id": "run-123", "data": {}},
                    },
                }

            async def submit_human_input(self, run_id: str, request_id: str, response: dict):
                _ = run_id, request_id, response
                return True

            async def cancel_run(self, run_id: str):
                _ = run_id
                return True

        fake_stdin = _FakeStdin()
        client = _FakeClient()
        answers = iter(["start run", "y", "/exit"])

        def _fake_input(prompt: str = "") -> str:
            value = next(answers)
            if prompt == "[Y/n] ":
                fake_stdin.prompt_active = True
                time.sleep(0.15)
                fake_stdin.prompt_active = False
            return value

        with patch("builtins.input", side_effect=_fake_input):
            with patch("sys.stdin", fake_stdin):
                with patch("select.select", return_value=([fake_stdin], [], [])):
                    with patch("sys.stdout", new_callable=io.StringIO):
                        await _run_repl(client, "_scratch", "build", confirm_mode=False)

        assert fake_stdin.read_during_prompt is False

    @pytest.mark.asyncio
    async def test_run_handoff_waits_for_automatic_recovery_summary(self):
        import io

        from dan.cli.chat import _run_repl

        class _FakeClient:
            async def get_graph(self, graph_id: str):
                _ = graph_id
                return {"nodes": [], "edges": []}

            async def send_chat_message(self, *args, **kwargs):
                _ = args, kwargs
                return {"stream_channel_id": "chat-main"}

            async def stream_chat_events(self, channel_id: str):
                if channel_id == "chat-main":
                    yield {
                        "type": "chat_complete",
                        "content": "**start_run**: Started run run-123",
                        "stream_channel_id": "run-123",
                        "graph_revision": "rev-1",
                    }
                    return
                if channel_id == "run-123":
                    yield {
                        "type": "chat_run_event",
                        "run_event": {
                            "event_type": "run_failed",
                            "summary": "Run failed: boom",
                            "detail": {"run_id": "run-123", "data": {"error": "boom"}},
                        },
                    }
                    yield {
                        "type": "chat_run_event",
                        "run_event": {
                            "event_type": "automatic_recovery_started",
                            "summary": "Auto-repair started: rerun_from_checkpoint",
                            "detail": {"run_id": "run-123", "data": {"selected_action": "rerun_from_checkpoint"}},
                        },
                    }
                    yield {
                        "type": "chat_run_event",
                        "run_event": {
                            "event_type": "node_completed",
                            "summary": "Auto-repair rerun: Node 'task' completed",
                            "detail": {
                                "run_id": "run-123",
                                "node_id": "task",
                                "data": {"automatic_recovery": True, "recovery_run_id": "rerun-1"},
                            },
                        },
                    }
                    yield {
                        "type": "chat_run_event",
                        "run_event": {
                            "event_type": "automatic_recovery_completed",
                            "summary": "Auto-repair completed",
                            "detail": {"run_id": "run-123", "data": {"status": "completed"}},
                        },
                    }
                    return

            async def submit_human_input(self, run_id: str, request_id: str, response: dict):
                _ = run_id, request_id, response
                return True

            async def cancel_run(self, run_id: str):
                _ = run_id
                return True

        with patch("builtins.input", side_effect=["start run", "/exit"]):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                await _run_repl(_FakeClient(), "_scratch", "build", confirm_mode=False)

        output = mock_stdout.getvalue()
        assert "Run failed: boom" in output
        assert "Auto-repair started: rerun_from_checkpoint" in output
        assert "Auto-repair rerun: Node 'task' completed" in output
        assert "Auto-repair completed" in output


class TestLocalHealthBanner:
    @pytest.mark.asyncio
    async def test_run_repl_shows_local_limitations_when_health_reports_them(self):
        import io

        from dan.cli.chat import _run_repl

        class _FakeClient:
            async def get_health(self) -> dict[str, Any]:
                return {
                    "status": "ok",
                    "startup": {"status": "ok", "issues": []},
                    "mode_limitations": [
                        {
                            "category": "transport",
                            "message": "Local mode is in-process CLI only; it does not expose an HTTP or remote-client surface.",
                        },
                        {
                            "category": "streaming",
                            "message": "Stream channels are process-local and are not durable across restarts.",
                        },
                    ],
                }

            async def get_graph(self, graph_id: str):
                _ = graph_id
                return {"nodes": [], "edges": []}

        client = _FakeClient()

        with patch("builtins.input", side_effect=["/exit"]):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                await _run_repl(client, "_scratch", "build", confirm_mode=False)

        output = mock_stdout.getvalue()
        assert "Local limits: transport, streaming" in output


class TestChatClient:
    """Tests for ChatClient."""

    def test_default_url(self):
        client = ChatClient()
        assert client._base_url == "http://127.0.0.1:8000"

    def test_custom_url(self):
        client = ChatClient(base_url="http://myserver:9000")
        assert client._base_url == "http://myserver:9000"

    @pytest.mark.asyncio
    async def test_ping_success(self):
        client = ChatClient()
        mock_resp = httpx.Response(200, json={"status": "ok"})
        with patch.object(httpx.AsyncClient, "get", return_value=mock_resp):
            client._http = httpx.AsyncClient()
            ok, err = await client.ping()
            assert ok is True
            assert err is None

    @pytest.mark.asyncio
    async def test_ping_failure(self):
        client = ChatClient()
        with patch.object(
            httpx.AsyncClient, "get", side_effect=httpx.ConnectError("refused")
        ):
            client._http = httpx.AsyncClient()
            ok, err = await client.ping()
            assert ok is False
            assert "refused" in (err or "")

    @pytest.mark.asyncio
    async def test_send_chat_message_success(self):
        client = ChatClient()
        mock_resp = httpx.Response(
            200,
            json={
                "message_id": "msg-123",
                "stream_channel_id": "chat-abc123",
            },
        )
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            client._http = httpx.AsyncClient()
            result = await client.send_chat_message(
                "_scratch",
                "hello",
                mode="build",
            )
            assert result["message_id"] == "msg-123"
            assert result["stream_channel_id"] == "chat-abc123"

    @pytest.mark.asyncio
    async def test_send_chat_message_includes_cli_control_plane_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = ChatClient()
        mock_resp = httpx.Response(
            200,
            json={
                "message_id": "msg-123",
                "stream_channel_id": "chat-abc123",
            },
        )
        monkeypatch.setenv("DAN_CLI_CONTROL_PLANE", "new")
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            client._http = httpx.AsyncClient()
            await client.send_chat_message("_scratch", "hello", mode="build")

        assert mock_post.await_count == 1
        assert mock_post.await_args.kwargs["json"]["control_plane_mode"] == "v2"

    @pytest.mark.asyncio
    async def test_send_chat_message_connection_error(self):
        client = ChatClient()
        with patch.object(
            httpx.AsyncClient,
            "post",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            client._http = httpx.AsyncClient()
            with pytest.raises(RuntimeError, match="Cannot reach server"):
                await client.send_chat_message("_scratch", "hello")

    @pytest.mark.asyncio
    async def test_apply_mutation_success(self):
        client = ChatClient()
        mock_resp = httpx.Response(
            200,
            json={
                "success": True,
                "new_graph": {"nodes": [], "edges": []},
                "errors": [],
            },
        )
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            client._http = httpx.AsyncClient()
            result = await client.apply_mutation(
                "_scratch",
                {"description": "test", "operations": []},
            )
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_close_client_quietly_swallows_cancelled_error(self):
        class _FakeClient:
            async def close(self):
                raise asyncio.CancelledError()

        await _close_client_quietly(_FakeClient())


class TestChatMain:
    def test_main_swallows_keyboard_interrupt(self):
        fake_parser = MagicMock()
        fake_parser.parse_args.return_value = SimpleNamespace(
            scratch=False,
            workflow_id="_scratch",
            mode=None,
            confirm=False,
            local=False,
            server=None,
            ask=None,
            pipe=False,
            model=None,
            output=None,
        )

        def _raise_keyboard_interrupt(coro):
            coro.close()
            raise KeyboardInterrupt()

        with patch("dan.cli.load_env"):
            with patch("dan.cli.chat.build_parser", return_value=fake_parser):
                with patch("dan.cli.chat.asyncio.run", side_effect=_raise_keyboard_interrupt):
                    main()


class TestChatClientStreamEvents:
    """Tests for stream_chat_events with mocked websockets."""

    @pytest.mark.asyncio
    async def test_stream_yields_events(self):
        client = ChatClient()
        events = [
            {"type": "chat_token", "delta": "Hello"},
            {"type": "chat_token", "delta": " world"},
            {"type": "chat_complete", "graph_revision": "rev-1"},
        ]

        class _FakeSocket:
            def __init__(self, msgs):
                self._msgs = msgs
                self._idx = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._idx >= len(self._msgs):
                    raise StopAsyncIteration
                value = self._msgs[self._idx]
                self._idx += 1
                return value

        class _FakeConnectCtx:
            def __init__(self, msgs):
                self._msgs = msgs

            async def __aenter__(self):
                return _FakeSocket(self._msgs)

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _FakeWebsockets:
            @staticmethod
            def connect(url):  # noqa: ARG004
                return _FakeConnectCtx([json.dumps(e) for e in events])

        with patch.dict("sys.modules", {"websockets": _FakeWebsockets}):
            collected = []
            async for evt in client.stream_chat_events("chat-abc"):
                collected.append(evt)
            assert len(collected) == 3
            assert collected[0]["type"] == "chat_token"
            assert collected[0]["delta"] == "Hello"
            assert collected[2]["type"] == "chat_complete"

    @pytest.mark.asyncio
    async def test_stream_handles_none_payload(self):
        """Server sends None (stream end) — no AttributeError on data.get."""
        client = ChatClient()

        class _FakeSocket:
            def __init__(self):
                self._sent = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._sent:
                    self._sent = True
                    return "null"  # JSON null
                raise StopAsyncIteration

        class _FakeConnectCtx:
            async def __aenter__(self):
                return _FakeSocket()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _FakeWebsockets:
            @staticmethod
            def connect(url):  # noqa: ARG004
                return _FakeConnectCtx()

        with patch.dict("sys.modules", {"websockets": _FakeWebsockets}):
            collected = []
            async for evt in client.stream_chat_events("chat-abc"):
                collected.append(evt)
            assert collected == [None]


class TestFormatMutationSummary:
    def test_basic(self):
        plan = {
            "description": "Add a summarizer node",
            "operations": [
                {"op": "add_node", "name": "summarizer", "node_type": "llm_operator"},
                {"op": "add_edge", "source_id": "a", "target_id": "summarizer"},
            ],
        }
        out = _format_mutation_summary(plan)
        assert "Add a summarizer node" in out
        assert "Operations: 2" in out
        assert "add_node" in out
        assert "summarizer" in out

    def test_truncates_many_ops(self):
        plan = {
            "description": "Bulk ops",
            "operations": [{"op": "add_node", "name": f"n{i}"} for i in range(10)],
        }
        out = _format_mutation_summary(plan)
        assert "... and 5 more" in out


class TestPromptApplyMutation:
    def test_empty_input_means_yes(self):
        with patch("builtins.input", return_value=""):
            assert _prompt_apply_mutation() is True

    def test_y_means_yes(self):
        with patch("builtins.input", return_value="y"):
            assert _prompt_apply_mutation() is True

    def test_n_means_no(self):
        with patch("builtins.input", return_value="n"):
            assert _prompt_apply_mutation() is False

    def test_eof_means_no(self):
        with patch("builtins.input", side_effect=EOFError):
            assert _prompt_apply_mutation() is False


class TestResumeHelpers:
    def test_select_resume_workflow_accepts_valid_index(self):
        selected = _select_resume_workflow("2", ["wf-a", "wf-b"], default_workflow_id="_scratch")
        assert selected == "wf-b"

    def test_select_resume_workflow_returns_default_for_new_or_invalid(self):
        assert _select_resume_workflow("new", ["wf-a"], default_workflow_id="_scratch") == "_scratch"
        assert _select_resume_workflow("", ["wf-a"], default_workflow_id="_scratch") == "_scratch"
        assert _select_resume_workflow("9", ["wf-a"], default_workflow_id="_scratch") == "_scratch"

    def test_record_recent_workflow_updates_profile(self):
        profile = UserProfile()
        _record_recent_workflow(profile, "wf-1")
        assert profile.recent_workflows[0].workflow_id == "wf-1"

    def test_record_recent_workflow_skips_scratch(self):
        profile = UserProfile()
        _record_recent_workflow(profile, "_scratch")
        assert profile.recent_workflows == []

    @pytest.mark.asyncio
    async def test_maybe_prompt_resume_workflow_uses_available_recent(self):
        profile = UserProfile()
        profile.touch_workflow("wf-b")
        profile.touch_workflow("wf-a")

        class _Client:
            async def list_graphs(self):
                return [{"graph_id": "wf-a"}, {"graph_id": "wf-b"}]

        with patch("builtins.input", return_value="1"):
            selected = await _maybe_prompt_resume_workflow(_Client(), "_scratch", profile)
        assert selected == "wf-a"

    @pytest.mark.asyncio
    async def test_maybe_prompt_resume_workflow_returns_default_when_none_available(self):
        profile = UserProfile()
        profile.touch_workflow("missing-wf")

        class _Client:
            async def list_graphs(self):
                return [{"graph_id": "wf-a"}]

        selected = await _maybe_prompt_resume_workflow(_Client(), "_scratch", profile)
        assert selected == "_scratch"


class TestShouldPromptApply:
    def test_true_when_no_dry_run_result(self):
        assert _should_prompt_apply(None) is True

    def test_true_when_success_true(self):
        assert _should_prompt_apply({"success": True, "errors": []}) is True

    def test_false_when_success_false(self):
        assert _should_prompt_apply({"success": False, "errors": []}) is False

    def test_false_when_errors_present_without_success_field(self):
        assert _should_prompt_apply({"errors": [{"message": "bad edge"}]}) is False


class TestScratchBootstrap:
    """Unit test for scratch bootstrap: workflow_id=_scratch with missing graph → empty graph created."""

    def test_graph_store_creates_scratch(self, tmp_path):
        """GraphStore.save_graph with empty graph creates file; get_graph retrieves it."""
        from dan.server.graph_store import GraphStore

        store = GraphStore(base_dir=str(tmp_path))
        assert store.get_graph("_scratch") is None

        store.save_graph("_scratch", {"nodes": [], "edges": []})
        data = store.get_graph("_scratch")
        assert data is not None
        assert data.get("nodes") == []
        assert data.get("edges") == []

    @pytest.mark.asyncio
    async def test_chat_message_bootstrap_calls_save_when_missing(self):
        """When workflow_id=_scratch and graph missing, save_graph is called."""
        from unittest.mock import MagicMock

        from dan.server import app as app_module

        mock_store = MagicMock()
        mock_store.get_graph.side_effect = [None, {"nodes": [], "edges": []}]
        mock_store.list_graphs.return_value = []
        mock_store.get_last_opened.return_value = None

        with patch.object(app_module, "_graph_store", mock_store):
            # Simulate the bootstrap logic from chat_message handler
            workflow_id = "_scratch"
            graph_dict = mock_store.get_graph(workflow_id)
            if workflow_id == "_scratch" and graph_dict is None:
                mock_store.save_graph("_scratch", {"nodes": [], "edges": []})
                graph_dict = mock_store.get_graph("_scratch")

            mock_store.save_graph.assert_called_once_with(
                "_scratch", {"nodes": [], "edges": []}
            )
            assert graph_dict == {"nodes": [], "edges": []}


class TestChatClientGetGraph:
    """Tests for ChatClient.get_graph."""

    @pytest.mark.asyncio
    async def test_get_graph_success(self):
        client = ChatClient()
        graph_data = {
            "nodes": [{"id": "n1", "type": "llm_operator"}],
            "edges": [],
            "metadata": {"name": "test"},
        }
        mock_resp = httpx.Response(200, json={"graph_id": "_scratch", "data": graph_data})
        with patch.object(httpx.AsyncClient, "get", return_value=mock_resp):
            client._http = httpx.AsyncClient()
            result = await client.get_graph("_scratch")
            assert result is not None
            assert len(result["nodes"]) == 1
            assert result["metadata"]["name"] == "test"

    @pytest.mark.asyncio
    async def test_get_graph_unwraps_data(self):
        """get_graph extracts the inner 'data' field from the server response."""
        client = ChatClient()
        inner = {"nodes": [], "edges": [], "metadata": {"name": "wf1"}}
        mock_resp = httpx.Response(200, json={"graph_id": "wf1", "data": inner})
        with patch.object(httpx.AsyncClient, "get", return_value=mock_resp):
            client._http = httpx.AsyncClient()
            result = await client.get_graph("wf1")
            assert result == inner

    @pytest.mark.asyncio
    async def test_get_graph_not_found(self):
        client = ChatClient()
        mock_resp = httpx.Response(404, json={"detail": "not found"})
        with patch.object(httpx.AsyncClient, "get", return_value=mock_resp):
            client._http = httpx.AsyncClient()
            result = await client.get_graph("missing")
            assert result is None


class TestChatClientSubmitHumanInput:
    """Tests for ChatClient.submit_human_input."""

    @pytest.mark.asyncio
    async def test_submit_success(self):
        client = ChatClient()
        mock_resp = httpx.Response(
            200, json={"status": "submitted", "request_id": "req-1"}
        )
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            client._http = httpx.AsyncClient()
            ok = await client.submit_human_input("run-1", "req-1", {"response": "yes"})
            assert ok is True

    @pytest.mark.asyncio
    async def test_submit_not_found(self):
        client = ChatClient()
        mock_resp = httpx.Response(404, json={"detail": "not found"})
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            client._http = httpx.AsyncClient()
            ok = await client.submit_human_input("run-x", "req-x", {"response": "y"})
            assert ok is False


class TestChatClientCancelRun:
    """Tests for ChatClient.cancel_run."""

    @pytest.mark.asyncio
    async def test_cancel_success(self):
        client = ChatClient()
        mock_resp = httpx.Response(200, json={"cancelled": True})
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            client._http = httpx.AsyncClient()
            ok = await client.cancel_run("run-1")
            assert ok is True


class TestFormatGraphSummary:
    """Tests for _format_graph_summary."""

    def test_empty_graph(self):
        out = _format_graph_summary({"nodes": [], "edges": [], "metadata": {"name": "test"}})
        assert "(empty graph)" in out
        assert "Nodes: 0" in out

    def test_graph_with_nodes_and_edges(self):
        data = {
            "nodes": [
                {"id": "n1", "type": "llm_operator", "label": "Summarizer"},
                {"id": "n2", "type": "tool_executor", "name": "Scraper"},
            ],
            "edges": [{"source_id": "n1", "target_id": "n2"}],
            "metadata": {"name": "my-workflow"},
        }
        out = _format_graph_summary(data)
        assert "my-workflow" in out
        assert "Nodes: 2" in out
        assert "Summarizer" in out
        assert "Scraper" in out
        assert "n1 -> n2" in out

    def test_truncates_many_nodes(self):
        data = {
            "nodes": [{"id": f"n{i}", "type": "generic"} for i in range(20)],
            "edges": [],
            "metadata": {},
        }
        out = _format_graph_summary(data)
        assert "... and 5 more nodes" in out


class TestMapRunEventChatBlock:
    """Tests for human_input_needed and run_cancelled in map_run_event_to_chat_block."""

    def test_human_input_needed_mapped(self):
        from dan.server.scoped_run import map_run_event_to_chat_block

        event = {
            "event_type": "human_input_needed",
            "run_id": "run-1",
            "node_id": "human-1",
            "data": {
                "request_id": "req-1",
                "prompt": "Approve this plan?",
                "render_mode": "approval",
            },
        }
        result = map_run_event_to_chat_block(event, "full", None)
        assert result is not None
        assert result["event_type"] == "human_input_needed"
        assert "Approve this plan?" in result["summary"]
        assert result["detail"]["data"]["request_id"] == "req-1"

    def test_run_cancelled_mapped(self):
        from dan.server.scoped_run import map_run_event_to_chat_block

        event = {
            "event_type": "run_cancelled",
            "run_id": "run-1",
            "data": {"reason": "user_cancelled"},
        }
        result = map_run_event_to_chat_block(event, "full", None)
        assert result is not None
        assert result["event_type"] == "run_cancelled"
        assert "user_cancelled" in result["summary"]

    def test_unknown_event_still_filtered(self):
        from dan.server.scoped_run import map_run_event_to_chat_block

        event = {"event_type": "some_internal_event", "run_id": "run-1", "data": {}}
        result = map_run_event_to_chat_block(event, "full", None)
        assert result is None


# -------------------------------------------------------------------
# Workflow management commands (21-6 patch)
# -------------------------------------------------------------------


class TestChatClientListGraphs:
    """Tests for ChatClient.list_graphs."""

    @pytest.mark.asyncio
    async def test_list_graphs_success(self):
        client = ChatClient()
        graphs = [
            {"graph_id": "wf1", "name": "Workflow 1", "updated_at": "2026-03-05T12:00:00Z"},
            {"graph_id": "wf2", "name": "Workflow 2", "updated_at": "2026-03-04T10:00:00Z"},
        ]
        mock_resp = httpx.Response(200, json={"graphs": graphs, "last_opened": "wf1"})
        with patch.object(httpx.AsyncClient, "get", return_value=mock_resp):
            client._http = httpx.AsyncClient()
            result = await client.list_graphs()
            assert len(result) == 2
            assert result[0]["graph_id"] == "wf1"

    @pytest.mark.asyncio
    async def test_list_graphs_empty(self):
        client = ChatClient()
        mock_resp = httpx.Response(200, json={"graphs": [], "last_opened": None})
        with patch.object(httpx.AsyncClient, "get", return_value=mock_resp):
            client._http = httpx.AsyncClient()
            result = await client.list_graphs()
            assert result == []


class TestChatClientCreateGraph:
    """Tests for ChatClient.create_graph."""

    @pytest.mark.asyncio
    async def test_create_graph_success(self):
        client = ChatClient()
        resp_data = {
            "graph_id": "my-wf",
            "data": {"nodes": [], "edges": [], "metadata": {"name": "my-wf"}},
        }
        mock_resp = httpx.Response(200, json=resp_data)
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            client._http = httpx.AsyncClient()
            result = await client.create_graph("my-wf")
            assert result["graph_id"] == "my-wf"

    @pytest.mark.asyncio
    async def test_create_graph_conflict(self):
        client = ChatClient()
        mock_resp = httpx.Response(409, json={"detail": "already exists"})
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            client._http = httpx.AsyncClient()
            with pytest.raises(RuntimeError, match="already exists"):
                await client.create_graph("dup-wf")


class TestChatClientSaveGraph:
    """Tests for ChatClient.save_graph."""

    @pytest.mark.asyncio
    async def test_save_graph_success(self):
        client = ChatClient()
        mock_resp = httpx.Response(200, json={"graph_id": "wf1", "status": "saved"})
        with patch.object(
            httpx.AsyncClient, "put", new_callable=AsyncMock, return_value=mock_resp
        ):
            client._http = httpx.AsyncClient()
            await client.save_graph("wf1", {"nodes": [], "edges": []})

    @pytest.mark.asyncio
    async def test_save_graph_error(self):
        client = ChatClient()
        mock_resp = httpx.Response(500, text="Internal server error")
        with patch.object(
            httpx.AsyncClient, "put", new_callable=AsyncMock, return_value=mock_resp
        ):
            client._http = httpx.AsyncClient()
            with pytest.raises(RuntimeError, match="Save workflow failed"):
                await client.save_graph("wf1", {"nodes": []})


class TestFormatWorkflowList:
    """Tests for _format_workflow_list."""

    def test_empty_list(self):
        out = _format_workflow_list([], "_scratch")
        assert "No saved workflows" in out

    def test_lists_workflows_with_marker(self):
        graphs = [
            {"graph_id": "wf1", "name": "First", "updated_at": "2026-03-05T12:00:00Z"},
            {"graph_id": "wf2", "name": "Second", "updated_at": "2026-03-04T10:00:00Z"},
        ]
        out = _format_workflow_list(graphs, "wf1")
        assert "wf1" in out
        assert "wf2" in out
        assert "First" in out
        assert "Second" in out
        assert "*" in out  # current marker on wf1

    def test_no_marker_when_no_match(self):
        graphs = [{"graph_id": "wf1", "name": "First", "updated_at": None}]
        out = _format_workflow_list(graphs, "_scratch")
        assert "*" not in out


class TestAutoGenerateName:
    """Tests for _auto_generate_name."""

    def test_basic_intent(self):
        history = [{"role": "user", "content": "Build an equity research pipeline"}]
        name = _auto_generate_name(history)
        assert name == "equity-research"

    def test_strips_stop_words(self):
        history = [{"role": "user", "content": "Create a workflow that scrapes URLs and writes summaries"}]
        name = _auto_generate_name(history)
        assert "create" not in name
        assert "workflow" not in name.split("-")
        assert "scrapes" in name or "urls" in name

    def test_max_words_limit(self):
        history = [{"role": "user", "content": "Analyze sentiment from tweets about AI companies quarterly"}]
        name = _auto_generate_name(history, max_words=3)
        assert len(name.split("-")) <= 3

    def test_truncates_long_slug(self):
        history = [{"role": "user", "content": "superlongword " * 20}]
        name = _auto_generate_name(history)
        assert len(name) <= 48

    def test_empty_history(self):
        assert _auto_generate_name([]) == ""

    def test_only_assistant_messages(self):
        history = [{"role": "assistant", "content": "Hello, how can I help?"}]
        assert _auto_generate_name(history) == ""

    def test_slash_command_skipped(self):
        history = [
            {"role": "user", "content": "/run"},
            {"role": "user", "content": "Build a data ingestion system"},
        ]
        name = _auto_generate_name(history)
        assert name == "data-ingestion-system"

    def test_uses_first_non_command_message(self):
        history = [
            {"role": "user", "content": "/show"},
            {"role": "user", "content": "Scrape news articles and summarize them"},
        ]
        name = _auto_generate_name(history)
        assert "scrape" in name or "news" in name


# ── Message queue tests ────────────────────────────────────────────

class TestInputQueue:
    """Tests for the message queue (type while streaming)."""

    def test_drain_empty(self):
        q = _InputQueue()
        assert q.drain() == []

    def test_drain_returns_queued_items(self):
        q = _InputQueue()
        q._queue.put("hello")
        q._queue.put("world")
        items = q.drain()
        assert items == ["hello", "world"]
        assert q.drain() == []

    def test_drain_skips_none_sentinel(self):
        q = _InputQueue()
        q._queue.put("msg1")
        q._queue.put(None)
        q._queue.put("msg2")
        items = q.drain()
        assert items == ["msg1", "msg2"]

    def test_multiple_drains(self):
        q = _InputQueue()
        q._queue.put("a")
        assert q.drain() == ["a"]
        q._queue.put("b")
        q._queue.put("c")
        assert q.drain() == ["b", "c"]
        assert q.drain() == []


# ── Readline history tests ─────────────────────────────────────────

class TestReadlineHistory:
    """Tests for readline chat history persistence."""

    def test_load_creates_dir(self, tmp_path, monkeypatch):
        hist_file = tmp_path / "sub" / "chat_history"
        monkeypatch.setattr("dan.cli.chat._HISTORY_FILE", hist_file)
        _load_readline_history()
        assert hist_file.parent.exists()

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        import readline as rl
        hist_file = tmp_path / "chat_history"
        monkeypatch.setattr("dan.cli.chat._HISTORY_FILE", hist_file)
        rl.clear_history()
        rl.add_history("first message")
        rl.add_history("second message")
        _save_readline_history()
        assert hist_file.exists()
        rl.clear_history()
        _load_readline_history()
        count = rl.get_current_history_length()
        assert count >= 2
        rl.clear_history()

    def test_load_missing_file_no_error(self, tmp_path, monkeypatch):
        hist_file = tmp_path / "nonexistent" / "chat_history"
        monkeypatch.setattr("dan.cli.chat._HISTORY_FILE", hist_file)
        _load_readline_history()  # should not raise
