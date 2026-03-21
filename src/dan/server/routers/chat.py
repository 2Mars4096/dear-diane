"""Chat endpoints: message, stop, CRUD, stream state, WebSocket, search,
export, pin, checkpoint, and run-command handling."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, model_validator

from dan.server.chat_manager import (
    compute_graph_revision,
    detect_chat_mode,
    normalize_chat_mode,
    recent_run_failed_for_workflow,
    build_debug_context,
)
from dan.server.chat_store import ChatMessage as StoreChatMessage
from dan.server.chat_titles import (
    autogenerate_thread_title,
    ensure_fallback_title,
    mark_manual_title,
)
from dan.server.mention_resolver import MentionRef
from dan.server.chat_stream_buffer import (
    ReconnectableChatStream,
    should_preserve_chat_stream,
)
from dan.server.scoped_run import (
    ScopedRunRequest,
    ScopedRunResponse,
    build_scoped_graph,
    map_run_event_to_chat_block,
    parse_run_command,
)
from dan.server.run_manager import RunStatus
from dan.server.routers.dependencies import (
    get_graph_store,
    get_run_manager,
    get_chat_manager,
    get_chat_store,
    get_dispatcher,
    get_concierge,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_CHAT_STREAM_MISSING_TERMINAL_FALLBACK = (
    "The response stream ended before a final answer was produced. "
    "Please ask me to continue from the latest progress."
)


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------


class ChatMentionRef(BaseModel):
    type: str
    identifier: str


class ChatMessageRequest(BaseModel):
    workflow_id: str
    message: str
    thread_id: str | None = None
    history: list[dict[str, str]] = []
    client_graph_revision: str | None = None
    mode: Literal["ask", "agent", "plan", "debug", "auto", "mutate", "build", "conversation"] = "auto"
    mentions: list[ChatMentionRef] = []
    surface: str | None = None
    surface_type: str | None = None
    surface_id: str | None = None
    session_id: str | None = None
    attachment_path: str | None = None
    surface_context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize_identifiers(self) -> "ChatMessageRequest":
        surface = (self.surface or "").strip() or None
        surface_type = (self.surface_type or "").strip() or None
        surface_id = (self.surface_id or "").strip() or None
        session_id = (self.session_id or "").strip() or None
        thread_id = (self.thread_id or "").strip() or None

        if surface and ":" in surface:
            parsed_type, parsed_id = surface.split(":", 1)
            if not parsed_type or not parsed_id:
                raise ValueError(
                    "surface must use the canonical 'surface_type:surface_id' format",
                )
            if surface_type and surface_type != parsed_type:
                raise ValueError(
                    "surface conflicts with surface_type; provide matching values",
                )
            if surface_id and surface_id != parsed_id:
                raise ValueError(
                    "surface conflicts with surface_id; provide matching values",
                )
            surface_type = surface_type or parsed_type
            surface_id = surface_id or parsed_id

        if bool(surface_type) != bool(surface_id):
            raise ValueError(
                "surface_type and surface_id must be provided together",
            )

        if surface_type and surface_id:
            canonical_surface = f"{surface_type}:{surface_id}"
            if surface and surface != canonical_surface:
                raise ValueError(
                    "surface must match the canonical '{surface_type}:{surface_id}' alias",
                )
            surface = canonical_surface

        if session_id is None:
            session_id = thread_id
        if thread_id is None:
            thread_id = session_id

        self.surface = surface
        self.surface_type = surface_type
        self.surface_id = surface_id
        self.session_id = session_id
        self.thread_id = thread_id

        allowed_history_roles = {"user", "assistant"}
        filtered: list[dict[str, str]] = []
        stripped_system = sum(1 for m in self.history if m.get("role") == "system")
        stripped_invalid = 0
        stripped_empty = 0
        for message in self.history:
            role = message.get("role")
            if role == "system":
                continue
            if role not in allowed_history_roles:
                stripped_invalid += 1
                continue
            raw_content = message.get("content")
            if raw_content is None:
                stripped_empty += 1
                continue
            content = raw_content if isinstance(raw_content, str) else str(raw_content)
            if not content.strip():
                stripped_empty += 1
                continue
            filtered.append({"role": role, "content": content})
        if stripped_system or stripped_invalid:
            _log = logging.getLogger(__name__)
            if stripped_system:
                _log.warning(
                    "Stripped %d system message(s) from chat history — "
                    "system messages must not be injected via the history field",
                    stripped_system,
                )
            if stripped_invalid:
                _log.warning(
                    "Stripped %d non-user/assistant message(s) from chat history — "
                    "history only accepts 'user' and 'assistant' roles",
                    stripped_invalid,
                )
            if stripped_empty:
                _log.warning(
                    "Stripped %d empty user/assistant message(s) from chat history — "
                    "history turns must have non-empty content",
                    stripped_empty,
                )
            self.history = filtered
        elif stripped_empty:
            logging.getLogger(__name__).warning(
                "Stripped %d empty user/assistant message(s) from chat history — "
                "history turns must have non-empty content",
                stripped_empty,
            )
            self.history = filtered

        return self


class EditorCompletionRequest(BaseModel):
    prefix: str
    suffix: str = ""
    language: str = "text"
    filePath: str | None = None
    maxTokens: int = 100


def _attachment_tool_hint(path: str, kind: str | None = None) -> str:
    lower = path.lower()
    if kind == "figure" or lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return "image_describe"
    if lower.endswith(".pdf"):
        return "pdf_read"
    if lower.endswith(".csv"):
        return "csv_read"
    return "file_read"


def _build_attachment_prompt_context(req: ChatMessageRequest) -> str:
    lines: list[str] = []
    seen: set[str] = set()

    if req.attachment_path:
        hint = _attachment_tool_hint(req.attachment_path)
        token = f"path:{req.attachment_path}"
        seen.add(token)
        lines.append(
            f"- Primary attached file path: `{req.attachment_path}`. "
            f"If the user is asking about the attachment, inspect it with `{hint}` before claiming no file was attached."
        )

    surface_context = req.surface_context if isinstance(req.surface_context, dict) else {}
    raw_attachments = surface_context.get("appended_attachments")
    if isinstance(raw_attachments, list):
        for item in raw_attachments[:8]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "Attachment").strip() or "Attachment"
            kind = str(item.get("kind") or "file").strip() or "file"
            path = str(item.get("path") or "").strip()
            caption = str(item.get("caption") or "").strip()
            source = str(item.get("source") or "").strip()
            token = f"{kind}:{path or name}:{caption}"
            if token in seen:
                continue
            seen.add(token)
            if path:
                hint = _attachment_tool_hint(path, kind=kind)
                detail_parts = [f"name={name}", f"path={path}", f"suggested_tool={hint}"]
                if caption:
                    detail_parts.append(f"caption={caption}")
                if source:
                    detail_parts.append(f"source={source}")
                lines.append(
                    "- Appended attachment: "
                    + ", ".join(detail_parts)
                    + ". If the user refers to this file/figure, inspect it rather than saying no attachment was provided."
                )
            else:
                detail_parts = [f"name={name}", f"kind={kind}"]
                if caption:
                    detail_parts.append(f"caption={caption}")
                if source:
                    detail_parts.append(f"source={source}")
                lines.append(
                    "- Appended attachment metadata: "
                    + ", ".join(detail_parts)
                    + ". No filesystem path was exposed for this attachment, so rely on the metadata already provided."
                )

    if not lines:
        return ""
    return (
        "## Appended attachments\n"
        "FACT: the current user turn includes one or more attached files/figures. "
        "Do not say that no file/image was attached when this section is present. "
        "If the user asks about an attached PDF/image/file, use the attachment metadata below and inspect the path with the suggested tool when needed.\n"
        + "\n".join(lines)
    )


class StopRequest(BaseModel):
    message_id: str | None = None


# ------------------------------------------------------------------
# Chat stream state (module-level)
# ------------------------------------------------------------------

_chat_streams: dict[str, tuple[ReconnectableChatStream, float]] = {}
_chat_produce_tasks: dict[str, asyncio.Task] = {}
_CHAT_STREAM_TTL_SECONDS = 120.0
_CHAT_STREAM_MAX_BUFFERED_EVENTS = 128


def _register_chat_stream(
    channel_id: str,
    queue: ReconnectableChatStream,
    *,
    task: asyncio.Task | None = None,
) -> None:
    _chat_streams[channel_id] = (queue, time.monotonic())
    if task is not None:
        _chat_produce_tasks[channel_id] = task


def _touch_chat_stream(channel_id: str) -> None:
    entry = _chat_streams.get(channel_id)
    if entry is None:
        return
    queue, _ = entry
    _chat_streams[channel_id] = (queue, time.monotonic())


async def _put_chat_stream_event(
    channel_id: str,
    queue: ReconnectableChatStream,
    event: Any,
) -> None:
    await queue.put(event)
    _touch_chat_stream(channel_id)


def _reap_stale_chat_streams() -> None:
    now = time.monotonic()
    stale = []
    for k, (queue, ts) in _chat_streams.items():
        task = _chat_produce_tasks.get(k)
        if queue.has_consumer:
            continue
        if task is not None and not task.done():
            continue
        if now - ts > _CHAT_STREAM_TTL_SECONDS:
            stale.append(k)
    for k in stale:
        _chat_streams.pop(k, None)
        task = _chat_produce_tasks.pop(k, None)
        if task is not None and not task.done():
            task.cancel()


# ------------------------------------------------------------------
# Stop endpoint
# ------------------------------------------------------------------


@router.post("/api/chat/{channel_id}/stop")
async def stop_chat_stream(channel_id: str, req: StopRequest | None = None):
    cm = get_chat_manager()
    if not channel_id.startswith("chat-"):
        raise HTTPException(status_code=404, detail="Stream not found or already finished")
    found = cm.cancel_stream(channel_id)
    if not found:
        raise HTTPException(status_code=404, detail="Stream not found or already finished")
    return {"status": "stopping", "channel_id": channel_id}


class InjectMessageRequest(BaseModel):
    content: str
    inject_id: str


@router.post("/api/chat/{channel_id}/inject")
async def inject_chat_message(channel_id: str, req: InjectMessageRequest):
    """Inject a user message into an active chat stream.

    The message will be picked up at the next tool-loop breakpoint and
    appended to the LLM context so the assistant sees it in its next turn.
    """
    cm = get_chat_manager()
    if not channel_id.startswith("chat-"):
        raise HTTPException(status_code=404, detail="Stream not found or already finished")
    found = cm.inject_message(channel_id, req.content, req.inject_id)
    if not found:
        raise HTTPException(status_code=404, detail="Stream not found or already finished")
    return {"status": "injected", "channel_id": channel_id, "inject_id": req.inject_id}


# ------------------------------------------------------------------
# Chat message (the big one)
# ------------------------------------------------------------------


@router.post("/api/chat/message")
async def chat_message(req: ChatMessageRequest, concierge: bool = True):
    cm = get_chat_manager()
    gs = get_graph_store()

    _reap_stale_chat_streams()

    run_cmd = parse_run_command(req.message)
    if run_cmd is not None:
        return await _handle_run_command(req, run_cmd)

    stream_channel_id = f"chat-{uuid.uuid4().hex[:10]}"
    queue = ReconnectableChatStream(
        max_buffered_events=_CHAT_STREAM_MAX_BUFFERED_EVENTS
    )
    _register_chat_stream(stream_channel_id, queue)
    cancel_event = cm.register_stream(stream_channel_id)
    attachment_prompt_context = _build_attachment_prompt_context(req)
    surface_context = req.surface_context if isinstance(req.surface_context, dict) else {}

    structured_mentions = [
        MentionRef(type=m.type, identifier=m.identifier)
        for m in req.mentions
    ] if req.mentions else []

    _concierge = get_concierge()
    _dispatcher = get_dispatcher()
    from dan.server.app import _run_manager

    async def _produce():
        terminal_event_emitted = False

        async def _emit_missing_terminal_fallback() -> None:
            nonlocal terminal_event_emitted
            if terminal_event_emitted:
                return
            terminal_event_emitted = True
            logger.warning(
                "Chat stream %s ended without a terminal event; synthesizing fallback",
                stream_channel_id,
            )
            await _put_chat_stream_event(
                stream_channel_id,
                queue,
                {
                    "type": "chat_complete",
                    "message_id": uuid.uuid4().hex[:12],
                    "content": _CHAT_STREAM_MISSING_TERMINAL_FALLBACK,
                    "token_usage": {},
                    "context_window": 0,
                    "graph_revision": "",
                },
            )

        try:
            normalized_mode = normalize_chat_mode(req.mode)
            effective_mode = req.mode if req.mode in ("build", "mutate") else normalized_mode
            graph_dict = gs.get_graph(req.workflow_id)

            if req.workflow_id == "_scratch" and graph_dict is None:
                gs.save_graph("_scratch", {"nodes": [], "edges": []})
                graph_dict = gs.get_graph("_scratch")

            detected_mode: str | None = None
            if normalized_mode == "auto":
                recent_run_failed = False
                if _run_manager is not None:
                    runs = _run_manager.list_runs()
                    recent_run_failed = recent_run_failed_for_workflow(runs, req.workflow_id)
                detected_mode = detect_chat_mode(
                    req.message, recent_run_failed,
                )
                normalized_mode = detected_mode

            debug_ctx = ""
            if normalized_mode == "debug" and _run_manager is not None:
                debug_ctx = build_debug_context(
                    _run_manager.list_runs(), req.workflow_id,
                )

            if concierge and (_dispatcher is not None or _concierge is not None):
                from dan.server.concierge import SurfaceMessage

                _surface_msg = SurfaceMessage(
                    surface=req.surface or "server",
                    surface_type=req.surface_type or "",
                    surface_id=req.surface_id or "",
                    session_id=req.session_id or req.thread_id or "",
                    external_id=req.session_id or req.thread_id or req.workflow_id or "server-chat",
                    text=req.message,
                    metadata={
                        "workflow_id": req.workflow_id,
                        "thread_id": req.thread_id,
                        "request_history": req.history,
                        "client_graph_revision": req.client_graph_revision,
                        "mode": normalized_mode,
                        "requested_mode": req.mode,
                        "debug_context": debug_ctx,
                        "mentions": structured_mentions,
                        "cancel_event": cancel_event,
                        "selected_path": req.attachment_path,
                        "attachment_prompt_context": attachment_prompt_context,
                        "surface_context": surface_context,
                        "stream_channel_id": stream_channel_id,
                    },
                )
                if _dispatcher is not None:
                    event_stream = _dispatcher.dispatch(_surface_msg)
                else:
                    event_stream = _concierge.process(_surface_msg)
            else:
                use_tools = graph_dict is not None and effective_mode not in ("ask", "plan")

                send = (
                    cm.send_message_with_tools
                    if use_tools
                    else cm.send_message
                )
                extra_kwargs: dict[str, Any] = {}
                if use_tools:
                    extra_kwargs["stream_channel_id"] = stream_channel_id
                if attachment_prompt_context:
                    extra_kwargs["prompt_context"] = attachment_prompt_context
                    extra_kwargs["extra_system_instructions"] = attachment_prompt_context
                if surface_context:
                    extra_kwargs["surface_context"] = surface_context
                event_stream = send(
                    workflow_id=req.workflow_id,
                    message=req.message,
                    history=req.history,
                    thread_id=req.session_id or req.thread_id,
                    client_graph_revision=req.client_graph_revision,
                    mode=effective_mode,
                    cancel_event=cancel_event,
                    mentions=structured_mentions,
                    debug_context=debug_ctx,
                    surface=req.surface or "server",
                    **extra_kwargs,
                )
            async for event in event_stream:
                payload = event.model_dump()
                evt_type = payload.get("type", "")
                if evt_type == "chat_complete":
                    if payload.get("detected_mode") != "progress_ack":
                        terminal_event_emitted = True
                elif evt_type in {"chat_mutation", "chat_error", "chat_interrupted"}:
                    terminal_event_emitted = True

                if evt_type == "chat_queued" and _dispatcher is not None:
                    queued_channel = payload.get("stream_channel_id", "")
                    if queued_channel:
                        _reap_stale_chat_streams()
                        queued_q = ReconnectableChatStream(
                            max_buffered_events=_CHAT_STREAM_MAX_BUFFERED_EVENTS
                        )

                        async def _pipe_queued(ch: str, qq: asyncio.Queue) -> None:
                            bus = _dispatcher.get_response_bus(ch)
                            if bus is None:
                                return
                            try:
                                while True:
                                    bus_event = await bus.get()
                                    if bus_event is None:
                                        break
                                    await _put_chat_stream_event(ch, qq, bus_event.model_dump())
                            except Exception:
                                logger.warning("Queued stream pipe error for %s", ch, exc_info=True)
                            finally:
                                await _put_chat_stream_event(ch, qq, None)
                                _dispatcher.cleanup_response_bus(ch)

                        _pipe_task = asyncio.create_task(_pipe_queued(queued_channel, queued_q))
                        _register_chat_stream(queued_channel, queued_q, task=_pipe_task)
                        _dispatcher._track_task(_pipe_task)
                    payload["status"] = "queued"
                    await _put_chat_stream_event(stream_channel_id, queue, payload)
                    continue

                if (
                    detected_mode
                    and evt_type in ("chat_complete", "chat_mutation")
                    and not payload.get("detected_mode")
                ):
                    payload["detected_mode"] = detected_mode
                run_stream_id = payload.get("stream_channel_id")
                if (
                    run_stream_id
                    and run_stream_id.startswith("run-")
                    and _run_manager is not None
                ):
                    run_id = run_stream_id[4:]
                    _reap_stale_chat_streams()
                    run_queue = ReconnectableChatStream(
                        max_buffered_events=_CHAT_STREAM_MAX_BUFFERED_EVENTS
                    )

                    async def _pipe_tool_run_events() -> None:
                        rq = _run_manager.subscribe(run_id)
                        try:
                            while True:
                                try:
                                    evt = await asyncio.wait_for(rq.get(), timeout=30.0)
                                except asyncio.TimeoutError:
                                    rec = _run_manager.get_run(run_id)
                                    if rec is None or rec.status in (
                                        RunStatus.COMPLETED,
                                        RunStatus.FAILED,
                                        RunStatus.CANCELLED,
                                    ):
                                        break
                                    continue
                                etype = evt.get("event_type", "")
                                if etype == "_catchup":
                                    snap = evt.get("snapshot", {})
                                    if snap.get("status") in ("completed", "failed", "cancelled"):
                                        for buf in evt.get("buffered_events", []):
                                            blk = map_run_event_to_chat_block(buf, "full", None)
                                            if blk is not None:
                                                await _put_chat_stream_event(
                                                    run_stream_id,
                                                    run_queue,
                                                    {"type": "chat_run_event", "run_event": blk},
                                                )
                                        break
                                    continue
                                blk = map_run_event_to_chat_block(evt, "full", None)
                                if blk is not None:
                                    await _put_chat_stream_event(
                                        run_stream_id,
                                        run_queue,
                                        {"type": "chat_run_event", "run_event": blk},
                                    )
                                if etype in ("run_completed", "run_failed", "run_cancelled"):
                                    break
                        except Exception:
                            logger.debug("Run event pipe error for %s", run_id, exc_info=True)
                        finally:
                            _run_manager.unsubscribe(run_id, rq)
                            await _put_chat_stream_event(run_stream_id, run_queue, None)

                    pipe_task = asyncio.create_task(_pipe_tool_run_events())
                    _register_chat_stream(run_stream_id, run_queue, task=pipe_task)
                await _put_chat_stream_event(stream_channel_id, queue, payload)
            if not terminal_event_emitted:
                await _emit_missing_terminal_fallback()
        except asyncio.CancelledError:
            if not cancel_event.is_set() and not terminal_event_emitted:
                with suppress(Exception):
                    await asyncio.shield(_emit_missing_terminal_fallback())
            raise
        except Exception as exc:
            logger.exception("Chat _produce() error for channel %s", stream_channel_id)
            from dan.server.chat_manager import _friendly_chat_error
            await _put_chat_stream_event(
                stream_channel_id,
                queue,
                {"type": "chat_error", "error": _friendly_chat_error(exc)},
            )
        finally:
            cm.unregister_stream(stream_channel_id)
            await _put_chat_stream_event(stream_channel_id, queue, None)

    produce_task = asyncio.create_task(_produce())
    _register_chat_stream(stream_channel_id, queue, task=produce_task)
    return {"message_id": uuid.uuid4().hex[:12], "stream_channel_id": stream_channel_id, "status": "processing"}


# ------------------------------------------------------------------
# /run command handler
# ------------------------------------------------------------------


async def _handle_run_command(
    req: ChatMessageRequest, run_cmd: dict[str, Any],
) -> dict[str, Any]:
    rm = get_run_manager()
    gs = get_graph_store()
    graph = gs.load_as_model(req.workflow_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{req.workflow_id}' not found")

    scope = run_cmd.get("scope", "full")
    result = build_scoped_graph(
        graph,
        scope,
        run_cmd.get("target_node_id"),
        run_cmd.get("target_subgraph_key"),
    )
    if result.error:
        return {
            "type": "run_error",
            "error": result.error.model_dump(),
            "message_id": uuid.uuid4().hex[:12],
        }

    record = await rm.start_run(
        result.graph, graph_id=req.workflow_id, inputs=run_cmd.get("inputs"),
    )

    _reap_stale_chat_streams()
    stream_channel_id = f"run-{uuid.uuid4().hex[:10]}"
    queue = ReconnectableChatStream(
        max_buffered_events=_CHAT_STREAM_MAX_BUFFERED_EVENTS
    )
    _register_chat_stream(stream_channel_id, queue)

    run_target = run_cmd.get("target_node_id") or run_cmd.get("target_subgraph_key")

    async def _pipe_run_events() -> None:
        run_queue = rm.subscribe(record.run_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(run_queue.get(), timeout=300)
                except asyncio.TimeoutError:
                    break
                event_type = event.get("event_type", "")
                if event_type == "_catchup":
                    snapshot = event.get("snapshot", {})
                    if snapshot.get("status") in ("completed", "failed"):
                        for buf_evt in event.get("buffered_events", []):
                            blk = map_run_event_to_chat_block(buf_evt, scope, run_target)
                            if blk is not None:
                                await _put_chat_stream_event(
                                    stream_channel_id,
                                    queue,
                                    {"type": "chat_run_event", "run_event": blk},
                                )
                        break
                    continue
                chat_block = map_run_event_to_chat_block(event, scope, run_target)
                if chat_block is not None:
                    await _put_chat_stream_event(
                        stream_channel_id,
                        queue,
                        {"type": "chat_run_event", "run_event": chat_block},
                    )
                if event_type in ("run_completed", "run_failed", "run_cancelled"):
                    break
        except Exception:
            logger.debug("Run event pipe error for %s", record.run_id, exc_info=True)
        finally:
            rm.unsubscribe(record.run_id, run_queue)
            await _put_chat_stream_event(stream_channel_id, queue, None)

    pipe_task = asyncio.create_task(_pipe_run_events())
    _register_chat_stream(stream_channel_id, queue, task=pipe_task)

    return {
        "type": "run_started",
        "run_id": record.run_id,
        "scope": scope,
        "target": run_target,
        "message_id": uuid.uuid4().hex[:12],
        "stream_channel_id": stream_channel_id,
    }


# ------------------------------------------------------------------
# Legacy editor completion endpoint
# ------------------------------------------------------------------


@router.post("/api/chat/editor/complete")
async def editor_inline_complete(req: EditorCompletionRequest):
    cm = get_chat_manager()
    gs = get_graph_store()

    if gs.get_graph("_scratch") is None:
        gs.save_graph("_scratch", {"nodes": [], "edges": []})

    prompt = (
        "You are an inline code completion model.\n"
        "Return ONLY the exact text to insert at the cursor.\n"
        "Do not wrap the answer in markdown fences.\n"
        "Do not repeat the prefix or suffix.\n\n"
        f"Language: {req.language}\n"
        f"File: {req.filePath or 'unknown'}\n"
        f"Max tokens: {req.maxTokens}\n\n"
        "[Prefix]\n"
        "```\n"
        f"{req.prefix}\n"
        "```\n\n"
        "[Suffix]\n"
        "```\n"
        f"{req.suffix}\n"
        "```\n"
    )

    accumulated = ""
    async for event in cm.send_message(
        workflow_id="_scratch",
        message=prompt,
        history=[],
        thread_id=f"editor-complete-{uuid.uuid4().hex[:8]}",
        mode="ask",
    ):
        payload = event.model_dump()
        event_type = payload.get("type")
        if event_type == "chat_token":
            accumulated = str(payload.get("accumulated") or accumulated)
        elif event_type in {"chat_complete", "chat_interrupted"}:
            accumulated = str(payload.get("content") or accumulated)
            break
        elif event_type == "chat_error":
            raise HTTPException(
                status_code=500,
                detail=str(payload.get("error") or "Inline completion failed"),
            )

    return {"completion": accumulated.strip()}


# ------------------------------------------------------------------
# Chat events WebSocket
# ------------------------------------------------------------------


@router.websocket("/api/chat/{channel_id}/events")
async def chat_events_ws(websocket: WebSocket, channel_id: str):
    entry = _chat_streams.get(channel_id)
    if entry is None:
        await websocket.accept()
        await websocket.close(code=4004)
        return
    queue, _ = entry
    current_event: Any | None = None
    await websocket.accept()
    queue.attach_consumer()
    task = _chat_produce_tasks.get(channel_id)
    queue.prime_reconnect_snapshot(producer_running=task is not None and not task.done())
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            current_event = event
            if event is None:
                break
            await websocket.send_json(event)
            current_event = None
            _touch_chat_stream(channel_id)
        await websocket.close()
    except WebSocketDisconnect:
        if current_event is not None:
            queue.requeue_front(current_event)
            current_event = None
            _touch_chat_stream(channel_id)
    except Exception:
        if current_event is not None:
            queue.requeue_front(current_event)
            current_event = None
            _touch_chat_stream(channel_id)
        logger.debug("Chat WebSocket error for channel %s", channel_id, exc_info=True)
    finally:
        queue.detach_consumer()
        task = _chat_produce_tasks.get(channel_id)
        producer_running = task is not None and not task.done()
        preserve_stream = should_preserve_chat_stream(
            queue,
            producer_running=producer_running,
        )
        if preserve_stream:
            _chat_streams[channel_id] = (queue, time.monotonic())
            if not producer_running:
                _chat_produce_tasks.pop(channel_id, None)
        else:
            _chat_streams.pop(channel_id, None)
            task = _chat_produce_tasks.pop(channel_id, None)
            if task is not None and not task.done():
                task.cancel()


# ------------------------------------------------------------------
# Chat thread CRUD
# ------------------------------------------------------------------


@router.get("/api/chats/search")
async def search_chat_threads(q: str = "", workflow_id: str | None = None):
    if not q.strip():
        return {"results": []}
    cs = get_chat_store()
    results = cs.search_threads(q.strip(), workflow_id=workflow_id)
    return {"results": results}


@router.get("/api/chats/{workflow_id}")
async def list_chat_threads(workflow_id: str):
    cs = get_chat_store()
    return {"threads": cs.list_threads(workflow_id)}


@router.get("/api/chats/{workflow_id}/{thread_id}")
async def get_chat_thread(workflow_id: str, thread_id: str):
    cs = get_chat_store()
    thread = cs.get_thread(workflow_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread.model_dump(mode="json")


@router.post("/api/chats/{workflow_id}")
async def create_chat_thread(workflow_id: str, body: dict[str, Any] | None = None):
    cs = get_chat_store()
    b = body or {}
    title = b.get("title", "")
    thread = cs.create_thread(workflow_id, title=title)
    mode = cs._normalize_mode(b.get("mode"))
    if str(title or "").strip():
        meta = cs.get_thread_meta(workflow_id, thread.id)
        meta["title_source"] = "fallback"
        meta["title_generation_started"] = False
        cs.set_thread_meta(workflow_id, thread.id, meta)
    if mode != "agent":
        cs.set_mode(workflow_id, thread.id, mode)

    parent_thread_id = b.get("parent_thread_id")
    if parent_thread_id:
        cs.set_branch_lineage(
            workflow_id,
            thread.id,
            parent_thread_id=parent_thread_id,
            branch_point_message_id=b.get("branch_point_message_id", ""),
            branch_type=b.get("branch_type", "explore"),
        )

    data = thread.model_dump(mode="json")
    data["mode"] = mode
    return data


@router.put("/api/chats/{workflow_id}/{thread_id}")
async def update_chat_thread(workflow_id: str, thread_id: str, body: dict[str, Any]):
    cs = get_chat_store()
    cm = get_chat_manager()

    thread = cs.get_thread(workflow_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    schedule_title_generation = False
    if "title" in body:
        mark_manual_title(workflow_id=workflow_id, thread_id=thread_id, store=cs, title=str(body["title"] or ""))
        thread = cs.get_thread(workflow_id, thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")
    if "messages" in body:
        latest_thread = cs.get_thread(workflow_id, thread_id)
        if latest_thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        latest_thread.messages = [
            StoreChatMessage.model_validate(m) for m in body["messages"]
        ]
        latest_thread.updated_at = datetime.now(timezone.utc)
        cs.save_thread(latest_thread)
        ensure_fallback_title(cs, workflow_id, thread_id)
        refreshed = cs.get_thread(workflow_id, thread_id)
        if refreshed is not None:
            thread = refreshed
        meta = cs.get_thread_meta(workflow_id, thread_id)
        first_user_present = any(msg.role == "user" for msg in thread.messages)
        if first_user_present:
            meta.pop("title_generation_failed", None)
        if (
            first_user_present
            and not meta.get("title_locked")
            and meta.get("title_source") != "generated"
            and not meta.get("title_generation_started")
            and not meta.get("title_generation_failed")
        ):
            meta["title_generation_started"] = True
            cs.set_thread_meta(workflow_id, thread_id, meta)
            schedule_title_generation = True
    if "mode" in body:
        cs.set_mode(workflow_id, thread_id, body["mode"])
    if schedule_title_generation and cm is not None:
        providers = getattr(cm, "_providers", None)
        chat_model = str(getattr(cm, "_chat_model", "") or "")

        async def _generate_title() -> None:
            try:
                await autogenerate_thread_title(
                    cs,
                    workflow_id,
                    thread_id,
                    providers=providers,
                    chat_model=chat_model,
                    mark_started=False,
                )
            except Exception:
                logger.debug("Background thread title generation failed", exc_info=True)
                meta = cs.get_thread_meta(workflow_id, thread_id)
                meta["title_generation_started"] = False
                meta["title_generation_failed"] = True
                cs.set_thread_meta(workflow_id, thread_id, meta)

        asyncio.create_task(_generate_title())
    return {"status": "updated"}


@router.delete("/api/chats/{workflow_id}/{thread_id}")
async def delete_chat_thread(workflow_id: str, thread_id: str):
    cs = get_chat_store()
    if not cs.delete_thread(workflow_id, thread_id):
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"status": "deleted"}


@router.get("/api/chats/{workflow_id}/{thread_id}/export")
async def export_chat_thread(workflow_id: str, thread_id: str, format: str = "md"):
    cs = get_chat_store()
    if format == "json":
        data = cs.export_thread_json(workflow_id, thread_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        return {"content": json.dumps(data, indent=2), "format": "json"}
    content = cs.export_thread_markdown(workflow_id, thread_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"content": content, "format": "md"}


@router.post("/api/chats/{workflow_id}/{thread_id}/pin")
async def pin_chat_thread(workflow_id: str, thread_id: str, body: dict[str, Any] | None = None):
    cs = get_chat_store()
    pinned = (body or {}).get("pinned", True)
    if not cs.set_pinned(workflow_id, thread_id, pinned):
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"status": "updated", "pinned": pinned}


@router.post("/api/chats/{workflow_id}/{thread_id}/checkpoint")
async def save_chat_checkpoint(workflow_id: str, thread_id: str, body: dict[str, Any]):
    cs = get_chat_store()
    message_id = body.get("message_id", "")
    graph_snapshot = body.get("graph_snapshot")
    if not graph_snapshot:
        raise HTTPException(status_code=422, detail="graph_snapshot required")
    filename = cs.save_checkpoint(
        workflow_id, thread_id, message_id, graph_snapshot,
    )
    return {"status": "saved", "filename": filename}
