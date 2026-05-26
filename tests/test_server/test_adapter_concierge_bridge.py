from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_adapter_concierge_routes_legacy_mode_through_chat_router_stream(monkeypatch):
    from dan.server.routers import adapters as adapters_module

    captured: dict[str, object] = {}

    async def _fake_chat_message(req, concierge=True):
        captured["req"] = req
        captured["concierge"] = concierge
        return {"stream_channel_id": "chat-v1-123"}

    async def _fake_iter_local_chat_stream_events(channel_id: str):
        assert channel_id == "chat-v1-123"
        yield {
            "type": "chat_queued",
            "stream_channel_id": "queued-123",
            "queue_position": 1,
        }
        yield {
            "type": "chat_complete",
            "content": "Queued reply delivered.",
        }

    sent: list[tuple[str, str]] = []

    async def _fake_send(_adapter, external_id: str, text: str) -> None:
        sent.append((external_id, text))

    monkeypatch.setenv("DAN_CONTROL_PLANE", "v1")
    monkeypatch.setenv("DAN_ADAPTERS_CONTROL_PLANE", "v1")
    monkeypatch.setattr(adapters_module, "_send_adapter_text", _fake_send)
    monkeypatch.setattr("dan.server.routers.chat.chat_message", _fake_chat_message)
    monkeypatch.setattr(
        "dan.server.routers.chat.iter_local_chat_stream_events",
        _fake_iter_local_chat_stream_events,
    )

    class _Adapter:
        pass

    await adapters_module._run_adapter_concierge(
        "wechat",
        _Adapter(),
        "wechat",
        "chat-123",
        "second message",
    )

    req = captured["req"]
    assert captured["concierge"] is True
    assert req.workflow_id == "_scratch"
    assert req.surface == "wechat:wechat"
    assert req.control_plane_mode == "v1"
    assert sent == [
        ("chat-123", "Queued (position 1) — I'll reply when ready."),
        ("chat-123", "Queued reply delivered."),
    ]


@pytest.mark.asyncio
async def test_adapter_concierge_v2_uses_chat_router_stream(monkeypatch):
    from dan.server.routers import adapters as adapters_module

    captured: dict[str, object] = {}

    async def _fake_chat_v2_message(req, concierge=True):
        captured["req"] = req
        captured["concierge"] = concierge
        return {"stream_channel_id": "chat-v2-123"}

    async def _fake_iter_local_chat_stream_events(channel_id: str):
        assert channel_id == "chat-v2-123"
        yield {
            "type": "chat_queued",
            "stream_channel_id": "queued-456",
            "queue_position": 2,
        }
        yield {
            "type": "chat_complete",
            "content": "V2 reply delivered.",
        }

    sent: list[tuple[str, str]] = []

    async def _fake_send(_adapter, external_id: str, text: str) -> None:
        sent.append((external_id, text))

    monkeypatch.setenv("DAN_ADAPTERS_CONTROL_PLANE", "v2")
    monkeypatch.setattr(adapters_module, "_send_adapter_text", _fake_send)
    monkeypatch.setattr("dan.server.routers.chat_v2.chat_v2_message", _fake_chat_v2_message)
    monkeypatch.setattr(
        "dan.server.routers.chat.iter_local_chat_stream_events",
        _fake_iter_local_chat_stream_events,
    )

    class _Adapter:
        pass

    await adapters_module._run_adapter_concierge(
        "wechat",
        _Adapter(),
        "wechat",
        "chat-123",
        "route through dan-v2",
    )

    req = captured["req"]
    assert captured["concierge"] is True
    assert req.workflow_id == "_scratch"
    assert req.surface == "wechat:wechat"
    assert req.thread_id == "chat-123"
    assert sent == [
        ("chat-123", "Queued (position 2) — I'll reply when ready."),
        ("chat-123", "V2 reply delivered."),
    ]


@pytest.mark.asyncio
async def test_adapter_concierge_surface_override_can_force_v2_when_global_is_v1(monkeypatch):
    from dan.server.routers import adapters as adapters_module

    captured: dict[str, object] = {}

    async def _fake_chat_v2_message(req, concierge=True):
        captured["req"] = req
        captured["concierge"] = concierge
        return {"stream_channel_id": "chat-v2-override"}

    async def _fake_iter_local_chat_stream_events(channel_id: str):
        assert channel_id == "chat-v2-override"
        yield {
            "type": "chat_complete",
            "content": "Surface override delivered.",
        }

    sent: list[tuple[str, str]] = []

    async def _fake_send(_adapter, external_id: str, text: str) -> None:
        sent.append((external_id, text))

    monkeypatch.setenv("DAN_CONTROL_PLANE", "v1")
    monkeypatch.setenv("DAN_WECHAT_CONTROL_PLANE", "new")
    monkeypatch.setattr(adapters_module, "_send_adapter_text", _fake_send)
    monkeypatch.setattr("dan.server.routers.chat_v2.chat_v2_message", _fake_chat_v2_message)
    monkeypatch.setattr(
        "dan.server.routers.chat.iter_local_chat_stream_events",
        _fake_iter_local_chat_stream_events,
    )

    class _Adapter:
        pass

    await adapters_module._run_adapter_concierge(
        "wechat",
        _Adapter(),
        "wechat",
        "chat-123",
        "route only wechat through dan-v2",
    )

    req = captured["req"]
    assert captured["concierge"] is True
    assert req.control_plane_mode == "v2"
    assert sent == [("chat-123", "Surface override delivered.")]


@pytest.mark.asyncio
async def test_adapter_concierge_telegram_legacy_override_is_ignored_for_pure_v2(monkeypatch):
    from dan.server.routers import adapters as adapters_module

    captured: dict[str, object] = {}
    events = [
        {
            "type": "accepted",
            "run_id": "run-1",
            "summary": "Accepted.",
        },
        {
            "type": "completed",
            "run_id": "run-1",
            "summary": "Pure V2 Agent reply.",
            "token_usage_total": {"input_tokens": 4, "output_tokens": 3},
        },
    ]

    async def _fake_create_agent_run(req):
        captured["create_req"] = req
        return {"v2_control_plane": {"run_id": "run-1"}}

    async def _fake_execute_agent_run(run_id, execute):
        captured["execute_run_id"] = run_id
        captured["execute"] = execute
        return {"status": "started"}

    class _Run:
        status = "completed"

    class _Store:
        def load_run_events(self, run_id: str):
            assert run_id == "run-1"
            return list(events)

        def get_run(self, run_id: str):
            assert run_id == "run-1"
            return _Run()

    monkeypatch.setenv("DAN_CONTROL_PLANE", "v2")
    monkeypatch.setenv("DAN_TELEGRAM_CONTROL_PLANE", "legacy")
    monkeypatch.delenv("DAN_TELEGRAM_ALLOW_V1", raising=False)
    monkeypatch.setattr("dan.server.routers.chat_v2.create_agent_run", _fake_create_agent_run)
    monkeypatch.setattr("dan.server.routers.chat_v2.execute_agent_run", _fake_execute_agent_run)
    monkeypatch.setattr("dan.server.routers.dependencies.get_chat_v2_store", lambda *_args: _Store())

    class _Adapter:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def send_or_edit(
            self,
            chat_id,
            text,
            message_id=None,
            *,
            thread_id=None,
        ):
            self.calls.append(
                {
                    "chat_id": chat_id,
                    "text": text,
                    "message_id": message_id,
                    "thread_id": thread_id,
                }
            )
            return 55 if message_id is None else message_id

    adapter = _Adapter()

    await adapters_module._run_adapter_concierge(
        "telegram",
        adapter,
        "telegram",
        "123",
        "/agent stay on legacy",
    )

    req = captured["create_req"]
    assert req.control_plane_mode == "v2"
    assert req.mode == "agent"
    assert captured["execute_run_id"] == "run-1"
    assert captured["execute"].background is True
    assert captured["execute"].backend == "super_dan"
    assert captured["execute"].surface_profile == "super_tui"
    assert captured["execute"].profile_policy["backend"] == "super_dan"
    assert captured["execute"].metadata["requested_from"] == "telegram"
    assert adapter.calls[0]["message_id"] is None
    assert all(call["chat_id"] == 123 for call in adapter.calls)
    assert any(call["message_id"] == 55 for call in adapter.calls[1:])
    assert "Done: Pure V2 Agent reply." in str(adapter.calls[-1]["text"])
    assert "Tokens: prompt=4, completion=3, total=7" in str(adapter.calls[-1]["text"])


@pytest.mark.asyncio
async def test_adapter_concierge_telegram_agent_run_receives_recent_history(monkeypatch):
    from dan.adapters.telegram_adapter import MessageContext
    from dan.server.routers import adapters as adapters_module

    adapters_module._adapter_conversation_history.clear()
    adapters_module._adapter_history_locks.clear()
    captured: list[object] = []

    async def _fake_create_agent_run(req):
        captured.append(req)
        return {"v2_control_plane": {"run_id": f"run-{len(captured)}"}}

    async def _fake_execute_agent_run(run_id, execute):
        return {"status": "started"}

    class _Run:
        status = "completed"

    class _Store:
        def load_run_events(self, run_id: str):
            summary = "First answer." if run_id == "run-1" else "Second answer."
            return [
                {"type": "accepted", "run_id": run_id, "summary": "Accepted."},
                {"type": "completed", "run_id": run_id, "summary": summary},
            ]

        def get_run(self, run_id: str):
            return _Run()

    class _Adapter:
        async def send_or_edit(
            self,
            chat_id,
            text,
            message_id=None,
            *,
            thread_id=None,
        ):
            return 55 if message_id is None else message_id

    monkeypatch.setenv("DAN_TELEGRAM_CONTROL_PLANE", "v2")
    monkeypatch.delenv("DAN_TELEGRAM_ALLOW_V1", raising=False)
    monkeypatch.setattr("dan.server.routers.chat_v2.create_agent_run", _fake_create_agent_run)
    monkeypatch.setattr("dan.server.routers.chat_v2.execute_agent_run", _fake_execute_agent_run)
    monkeypatch.setattr("dan.server.routers.dependencies.get_chat_v2_store", lambda *_args: _Store())

    adapter = _Adapter()
    ctx1 = MessageContext(chat_id=123, message_id=10, thread_id=7, chat_type="private")
    ctx2 = MessageContext(
        chat_id=123,
        message_id=11,
        thread_id=7,
        reply_to_message_id=10,
        reply_to_text="First answer.",
        chat_type="private",
    )

    await adapters_module._run_adapter_concierge(
        "telegram",
        adapter,
        "telegram",
        "123:7",
        "/agent first question",
        ctx=ctx1,
    )
    await adapters_module._run_adapter_concierge(
        "telegram",
        adapter,
        "telegram",
        "123:7",
        "/agent follow up question",
        ctx=ctx2,
    )

    first_req, second_req = captured
    assert first_req.history == [{"role": "user", "content": "first question"}]
    assert second_req.history == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "First answer."},
        {"role": "user", "content": "follow up question"},
    ]
    assert second_req.surface_context["telegram"]["reply_to_text"] == "First answer."
    assert second_req.surface_context["surface_profile"] == "super_tui"
    assert second_req.surface_context["agent_backend"] == "super_dan"
    assert second_req.surface_context["conversation"]["recent_turns"] == second_req.history
    assert (
        second_req.surface_context["conversation"]["history_turn_count"]
        == len(second_req.history)
    )


@pytest.mark.asyncio
async def test_adapter_concierge_telegram_plain_text_stays_v2_chat_with_history(monkeypatch):
    from dan.adapters.telegram_adapter import MessageContext
    from dan.server.routers import adapters as adapters_module

    adapters_module._adapter_conversation_history.clear()
    adapters_module._adapter_history_locks.clear()
    captured: list[object] = []
    sent: list[tuple[str, str]] = []

    async def _fake_chat_v2_message(req, concierge=True):
        captured.append(req)
        return {"stream_channel_id": f"chat-v2-{len(captured)}"}

    async def _fake_create_agent_run(req):
        raise AssertionError("plain Telegram chat should not create an Agent run")

    async def _fake_iter_local_chat_stream_events(channel_id: str):
        if channel_id == "chat-v2-1":
            yield {"type": "chat_complete", "content": "First chat answer."}
            return
        if channel_id == "chat-v2-2":
            yield {"type": "chat_complete", "content": "Follow-up chat answer."}
            return
        raise AssertionError(f"unexpected channel: {channel_id}")

    async def _fake_send(_adapter, external_id: str, text: str) -> None:
        sent.append((external_id, text))

    class _Adapter:
        pass

    monkeypatch.setenv("DAN_TELEGRAM_CONTROL_PLANE", "v2")
    monkeypatch.delenv("DAN_TELEGRAM_ALLOW_V1", raising=False)
    monkeypatch.setattr(adapters_module, "_send_adapter_text", _fake_send)
    monkeypatch.setattr("dan.server.routers.chat_v2.chat_v2_message", _fake_chat_v2_message)
    monkeypatch.setattr("dan.server.routers.chat_v2.create_agent_run", _fake_create_agent_run)
    monkeypatch.setattr(
        "dan.server.routers.chat.iter_local_chat_stream_events",
        _fake_iter_local_chat_stream_events,
    )

    adapter = _Adapter()
    ctx1 = MessageContext(chat_id=123, message_id=10, thread_id=7, chat_type="private")
    ctx2 = MessageContext(
        chat_id=123,
        message_id=11,
        thread_id=7,
        reply_to_message_id=10,
        reply_to_text="First chat answer.",
        chat_type="private",
    )

    await adapters_module._run_adapter_concierge(
        "telegram",
        adapter,
        "telegram",
        "123:7",
        "hi again",
        ctx=ctx1,
    )
    await adapters_module._run_adapter_concierge(
        "telegram",
        adapter,
        "telegram",
        "123:7",
        "so I was asking about the OPEC stuff",
        ctx=ctx2,
    )

    first_req, second_req = captured
    assert first_req.control_plane_mode == "v2"
    assert first_req.mode == "auto"
    assert first_req.history == [{"role": "user", "content": "hi again"}]
    assert second_req.mode == "auto"
    assert second_req.history == [
        {"role": "user", "content": "hi again"},
        {"role": "assistant", "content": "First chat answer."},
        {"role": "user", "content": "so I was asking about the OPEC stuff"},
    ]
    assert second_req.surface_context["telegram"]["reply_to_text"] == "First chat answer."
    assert sent == [
        ("123:7", "First chat answer."),
        ("123:7", "Follow-up chat answer."),
    ]


@pytest.mark.asyncio
async def test_adapter_concierge_telegram_workspace_menu_stays_local(monkeypatch, tmp_path):
    from dan.adapters.telegram_adapter import MessageContext
    from dan.server.routers import adapters as adapters_module

    monkeypatch.setenv("DAN_TELEGRAM_STATE_DIR", str(tmp_path / "telegram-state"))
    monkeypatch.setenv("DAN_TELEGRAM_CONTROL_PLANE", "v2")

    class _Adapter:
        def __init__(self) -> None:
            self.menus: list[dict[str, object]] = []

        async def send_menu(
            self,
            chat_id: int,
            text: str,
            buttons: list[list[tuple[str, str]]],
            *,
            message_id: int | None = None,
            reply_to: int | None = None,
            thread_id: int | None = None,
        ) -> int:
            self.menus.append(
                {
                    "chat_id": chat_id,
                    "text": text,
                    "buttons": buttons,
                    "message_id": message_id,
                    "reply_to": reply_to,
                    "thread_id": thread_id,
                }
            )
            return 99

    async def _fake_chat_v2_message(*_args, **_kwargs):
        raise AssertionError("/workspace should not enter Chat V2")

    monkeypatch.setattr("dan.server.routers.chat_v2.chat_v2_message", _fake_chat_v2_message)

    adapter = _Adapter()
    await adapters_module._run_adapter_concierge(
        "telegram-adapter",
        adapter,
        "telegram",
        "123:7",
        "/workspace",
        ctx=MessageContext(chat_id=123, message_id=10, thread_id=7, chat_type="private"),
    )

    assert adapter.menus
    assert "DAN Super workspace" in str(adapter.menus[-1]["text"])
    assert adapter.menus[-1]["reply_to"] == 10
    assert adapter.menus[-1]["thread_id"] == 7


@pytest.mark.asyncio
async def test_adapter_concierge_telegram_workspace_menu_accepts_start_path(monkeypatch, tmp_path):
    from dan.adapters.telegram_adapter import MessageContext
    from dan.server.routers import adapters as adapters_module

    monkeypatch.setenv("DAN_TELEGRAM_STATE_DIR", str(tmp_path / "telegram-state"))
    workspace = tmp_path / "project"
    child = workspace / "src"
    child.mkdir(parents=True)
    adapters_module._select_adapter_telegram_workspace(
        adapter_id="telegram-adapter",
        external_id="123:7",
        history_key=adapters_module._adapter_history_key("telegram-adapter", "telegram", "123:7"),
        root=str(child),
    )

    class _Adapter:
        def __init__(self) -> None:
            self.menus: list[dict[str, object]] = []

        async def send_menu(self, chat_id, text, buttons, **kwargs):
            self.menus.append({"text": text, "buttons": buttons, **kwargs})
            return 99

    adapter = _Adapter()
    await adapters_module._run_adapter_concierge(
        "telegram-adapter",
        adapter,
        "telegram",
        "123:7",
        "/workspace ..",
        ctx=MessageContext(chat_id=123, message_id=10, thread_id=7, chat_type="private"),
    )

    assert f"Browsing: {workspace.resolve()}" in str(adapter.menus[-1]["text"])
    flat_buttons = [label for row in adapter.menus[-1]["buttons"] for label, _data in row]
    assert "Down: src" in flat_buttons


@pytest.mark.asyncio
async def test_adapter_concierge_sessions_are_grouped_by_workspace(monkeypatch, tmp_path):
    from dan.adapters.telegram_adapter import MessageContext
    from dan.server.routers import adapters as adapters_module

    workspace_a = tmp_path / "alpha"
    workspace_b = tmp_path / "beta"
    workspace_a.mkdir()
    workspace_b.mkdir()

    class _Task:
        def __init__(self, task_id: str, status: str, workspace, title: str) -> None:
            self.task_id = task_id
            self.thread_id = "thread"
            self.workspace_root = str(workspace)
            self.workspace_id = workspace.name
            self.status = status
            self.phase = "running"
            self.active_run_id = f"run-{task_id}"
            self.latest_progress = "working"
            self.metadata = {}

        def snapshot(self):
            return SimpleNamespace(
                model_dump=lambda mode="json": {
                    "task_id": self.task_id,
                    "thread_id": self.thread_id,
                    "status": self.status,
                    "phase": self.phase,
                    "latest_progress": self.latest_progress,
                    "metadata": {
                        "workspace_root": self.workspace_root,
                        "workspace_id": self.workspace_id,
                        "active_run_id": self.active_run_id,
                    },
                }
            )

    class _Store:
        def __init__(self) -> None:
            self.tasks = [
                _Task("task-raw-alpha", "running", workspace_a, "Fix alpha tests"),
                _Task("task-raw-beta", "completed", workspace_b, "Write beta docs"),
            ]

        def list_task_records(self, limit=60):
            return self.tasks[:limit]

        def get_run(self, run_id: str):
            text = "Fix alpha tests" if "alpha" in run_id else "Write beta docs"
            return SimpleNamespace(command=SimpleNamespace(payload={"text": text}))

    class _Adapter:
        def __init__(self) -> None:
            self.menus: list[dict[str, object]] = []

        async def send_menu(self, chat_id, text, buttons, **kwargs):
            self.menus.append({"text": text, "buttons": buttons, **kwargs})
            return 99

    monkeypatch.setattr("dan.server.routers.dependencies.get_chat_v2_store", lambda *_args: _Store())
    adapter = _Adapter()
    await adapters_module._run_adapter_concierge(
        "telegram-adapter",
        adapter,
        "telegram",
        "123:7",
        "/sessions",
        ctx=MessageContext(chat_id=123, message_id=10, thread_id=7, chat_type="private"),
    )

    text = str(adapter.menus[-1]["text"])
    assert "[alpha]" in text
    assert "[beta]" in text
    assert "Fix alpha tests" in text
    assert "Write beta docs" in text
    assert "task-raw-alpha" not in text
    flat_buttons = [label for row in adapter.menus[-1]["buttons"] for label, _data in row]
    assert any(label.startswith("Steer: Fix alpha tests") for label in flat_buttons)


@pytest.mark.asyncio
async def test_adapter_concierge_selected_session_uses_agent_run_command(monkeypatch, tmp_path):
    from dan.adapters.telegram_adapter import MessageContext
    from dan.server.routers import adapters as adapters_module

    monkeypatch.setenv("DAN_TELEGRAM_STATE_DIR", str(tmp_path / "telegram-state"))
    monkeypatch.setenv("DAN_TELEGRAM_CONTROL_PLANE", "v2")
    history_key = adapters_module._adapter_history_key("telegram-adapter", "telegram", "123:7")
    adapters_module._set_adapter_telegram_session_binding(
        external_id="123:7",
        history_key=history_key,
        binding={
            "task_id": "task-1",
            "run_id": "run-1",
            "status": "running",
            "queue_action": "append",
        },
    )

    class _Store:
        def __init__(self) -> None:
            self.commands = []

        def get_run(self, run_id: str):
            assert run_id == "run-1"
            return SimpleNamespace(task_id="task-1")

        def record_agent_command(self, command):
            self.commands.append(command)
            return SimpleNamespace(summary="Steering note queued.")

    store = _Store()
    sent: list[tuple[str, str]] = []

    async def _fake_send(_adapter, external_id: str, text: str) -> None:
        sent.append((external_id, text))

    async def _fake_create_agent_run(*_args, **_kwargs):
        raise AssertionError("selected active session should not create a new run")

    monkeypatch.setattr("dan.server.routers.dependencies.get_chat_v2_store", lambda *_args: store)
    monkeypatch.setattr("dan.server.routers.chat_v2.create_agent_run", _fake_create_agent_run)
    monkeypatch.setattr(adapters_module, "_send_adapter_text", _fake_send)

    await adapters_module._run_adapter_concierge(
        "telegram-adapter",
        object(),
        "telegram",
        "123:7",
        "also update tests",
        ctx=MessageContext(chat_id=123, message_id=10, thread_id=7, chat_type="private"),
    )

    assert len(store.commands) == 1
    command = store.commands[0]
    assert command.command == "append_followup"
    assert command.task_id == "task-1"
    assert command.run_id == "run-1"
    assert command.payload["text"] == "also update tests"
    assert command.payload["surface_context"]["task_id"] == "task-1"
    assert sent == [("123:7", "Steering note queued.")]


@pytest.mark.asyncio
async def test_adapter_concierge_telegram_agent_uses_message_lane(monkeypatch, tmp_path):
    from dan.adapters.telegram_adapter import MessageContext
    from dan.server.chat_v2 import build_surface_turn_from_chat_request
    from dan.server.routers import adapters as adapters_module

    monkeypatch.setenv("DAN_TELEGRAM_STATE_DIR", str(tmp_path / "telegram-state"))
    monkeypatch.setenv("DAN_TELEGRAM_CONTROL_PLANE", "v2")
    captured: dict[str, object] = {}
    sent: list[tuple[str, str]] = []

    async def _fake_create_agent_run(req):
        captured["req"] = req
        return {"event": {"summary": "not created"}}

    async def _fake_send(_adapter, external_id: str, text: str) -> None:
        sent.append((external_id, text))

    monkeypatch.setattr("dan.server.routers.chat_v2.create_agent_run", _fake_create_agent_run)
    monkeypatch.setattr(adapters_module, "_send_adapter_text", _fake_send)

    await adapters_module._run_adapter_concierge(
        "telegram-adapter",
        object(),
        "telegram",
        "123:7",
        "/agent build alpha.py",
        ctx=MessageContext(chat_id=123, message_id=10, thread_id=7, chat_type="private"),
    )

    req = captured["req"]
    lane_key = req.surface_context["conversation"]["lane_key"]
    assert lane_key == "telegram:telegram-adapter:123:7:m10"
    turn = build_surface_turn_from_chat_request(req)
    assert turn.metadata["surface_topic_key"].endswith(lane_key)
    assert sent == [("123:7", "not created")]


@pytest.mark.asyncio
async def test_adapter_concierge_reset_forces_selected_run_stopped(monkeypatch, tmp_path):
    from dan.adapters.telegram_adapter import MessageContext
    from dan.server.routers import adapters as adapters_module

    monkeypatch.setenv("DAN_TELEGRAM_STATE_DIR", str(tmp_path / "telegram-state"))
    monkeypatch.setenv("DAN_TELEGRAM_CONTROL_PLANE", "v2")
    history_key = adapters_module._adapter_history_key("telegram-adapter", "telegram", "123:7")
    adapters_module._set_adapter_telegram_session_binding(
        external_id="123:7",
        history_key=history_key,
        binding={
            "task_id": "task-1",
            "run_id": "run-1",
            "status": "running",
            "queue_action": "append",
        },
    )
    adapters_module._adapter_conversation_history[history_key] = [
        {"role": "user", "content": "old stuck context"}
    ]

    class _Store:
        def __init__(self) -> None:
            self.commands = []
            self.confirmed = []

        def get_run(self, run_id: str):
            assert run_id == "run-1"
            return SimpleNamespace(run_id="run-1", task_id="task-1", status="running")

        def record_agent_command(self, command):
            self.commands.append(command)
            return SimpleNamespace(summary="Stop requested.")

        def confirm_agent_run_stopped(self, run_id: str, **kwargs):
            self.confirmed.append((run_id, kwargs))
            return SimpleNamespace(summary="Forced stopped.")

    store = _Store()
    sent: list[tuple[str, str]] = []

    async def _fake_send(_adapter, external_id: str, text: str) -> None:
        sent.append((external_id, text))

    monkeypatch.setattr("dan.server.routers.dependencies.get_chat_v2_store", lambda *_args: store)
    monkeypatch.setattr(adapters_module, "_send_adapter_text", _fake_send)

    await adapters_module._run_adapter_concierge(
        "telegram-adapter",
        object(),
        "telegram",
        "123:7",
        "/reset",
        ctx=MessageContext(chat_id=123, message_id=10, thread_id=7, chat_type="private"),
    )

    assert store.commands[0].command == "stop"
    assert store.confirmed[0][0] == "run-1"
    assert adapters_module._adapter_telegram_session_binding(
        external_id="123:7",
        history_key=history_key,
    ) == {}
    assert history_key not in adapters_module._adapter_conversation_history
    assert "starts fresh" in sent[-1][1]
