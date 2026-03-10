"""dan-adapter — start messaging adapters for DAN workflows.

Usage::

    # Chat mode (general-purpose, like dan-chat over messaging):
    dan-adapter telegram --bot-token TOKEN
    dan-adapter whatsapp-web

    # Workflow mode (run a specific workflow per message):
    dan-adapter telegram --bot-token TOKEN --workflow wf.md
    dan-adapter whatsapp --workflow wf.md --access-token TOKEN …

    dan-adapter --config adapter-config.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import signal
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from dan.server.concierge.identity import (
    get_bot_name,
    format_bare_prefix,
    starts_with_prefix,
    strip_prefix,
)


def _strip_dan_prefix(text: str) -> str:
    return strip_prefix(text)


def _surface_name_for_adapter_type(adapter_type: str) -> str:
    if adapter_type.startswith("telegram:"):
        return adapter_type
    known = {"email", "telegram", "whatsapp", "whatsapp-web"}
    return adapter_type if adapter_type in known else "server"


# ---------------------------------------------------------------------------
# Chat stream helpers
# ---------------------------------------------------------------------------

_FUNCTION_CALL_XML_RE = re.compile(
    r"<FunctionCall>.*?</FunctionCall>",
    re.DOTALL,
)


def _strip_function_call_xml(text: str) -> str:
    """Remove <FunctionCall>...</FunctionCall> XML leaked by LLM fallback text streams."""
    return _FUNCTION_CALL_XML_RE.sub("", text).strip()


def _translate_slash_command(text: str) -> str | None:
    """Translate /slash commands to natural language for server dispatch."""
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    translations = {
        "/find": f"Find the file matching '{arg}' on my computer" if arg else None,
        "/send": f"Send me the file at {arg}" if arg else None,
        "/status": "What's the status of my current runs?",
        "/cancel": "Cancel the current run",
        "/show": "Show me the current workflow",
        "/list": "List my saved workflows",
        "/help": None,
    }
    return translations.get(cmd, None)


def _strip_reply_prefix_lines(text: str) -> str:
    lines = text.splitlines()
    while lines and lines[0].startswith('[Replying to: "'):
        lines.pop(0)
    return "\n".join(lines).strip()


def _split_reply_prefix_lines(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    prefix_lines: list[str] = []
    while lines and lines[0].startswith('[Replying to: "'):
        prefix_lines.append(lines.pop(0))
    prefix = "\n".join(prefix_lines).strip()
    body = "\n".join(lines).strip()
    return prefix, body


def _consume_chat_stream_events(
    events: list[dict[str, Any]],
) -> tuple[str, dict[str, Any] | None, list[str], list[dict[str, Any]]]:
    """Merge chat stream events into (reply, mutation_plan, file_paths, poll_requests).

    Some chat flows stream incremental ``chat_token`` events, while others
    return the full assistant text only on ``chat_complete.content``.  The
    adapter must handle both.
    """
    collected: list[str] = []
    mutation_plan: dict[str, Any] | None = None
    file_paths: list[str] = []
    poll_requests: list[dict[str, Any]] = []
    complete_content = ""
    error_message = ""
    last_tool_name = ""

    for event in events:
        if not isinstance(event, dict):
            continue
        evt_type = event.get("type", "")
        if evt_type == "chat_token":
            token = event.get("token", "")
            collected.append(token)
        elif evt_type == "chat_tool_call_start":
            last_tool_name = str(event.get("tool_name", "") or "").strip()
        elif evt_type == "chat_file_attachment":
            path = event.get("path", "")
            if path:
                file_paths.append(path)
        elif evt_type == "chat_poll_request":
            poll_requests.append(event)
        elif evt_type == "chat_mutation":
            mutation_plan = event.get("mutation_plan")
            desc = ""
            if mutation_plan:
                desc = mutation_plan.get("description", "")
                ops = mutation_plan.get("operations", [])
                desc = f"{desc}\n({len(ops)} operations)"
            collected.append(f"\n📋 Mutation proposed: {desc}")
        elif evt_type in ("chat_complete", "chat_interrupted"):
            complete_content = event.get("content", "") or ""
            break
        elif evt_type == "chat_error":
            error_message = str(event.get("error", "") or "").strip()
            break

    full_reply = "".join(collected).strip()
    full_reply = _strip_function_call_xml(full_reply)
    complete_content = complete_content.strip()
    complete_content = _strip_function_call_xml(complete_content)
    if complete_content:
        if not full_reply:
            full_reply = complete_content
        elif complete_content not in full_reply:
            full_reply = f"{full_reply}\n\n{complete_content}".strip()
    elif error_message:
        prefix = "I hit an internal error while processing that request"
        if last_tool_name:
            prefix = f"{prefix} with `{last_tool_name}`"
        if full_reply:
            full_reply = f"{full_reply}\n\n{prefix}: {error_message}".strip()
        else:
            full_reply = f"{prefix}: {error_message}"
    return full_reply, mutation_plan, file_paths, poll_requests


# ---------------------------------------------------------------------------
# Chat-mode runner — route messages through the server chat API
# ---------------------------------------------------------------------------

async def _run_adapter_chat_mode(adapter: Any, config: Any, adapter_type: str = "server") -> None:
    """Route adapter messages through the dan-serve chat API (like dan-chat).

    Each messaging conversation gets its own workflow (scratch by default).
    Messages are sent to POST /api/chat/message and streamed responses are
    relayed back to the messaging user.
    """
    import httpx

    server_url = (
        config.server_url
        or os.environ.get("DAN_SERVER_URL")
        or "http://127.0.0.1:8000"
    ).rstrip("/")

    await adapter.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    _force_exit_armed = False

    def _handle_signal() -> None:
        nonlocal _force_exit_armed
        if _force_exit_armed:
            print("\nForce exit.", file=sys.stderr)
            os._exit(1)
        _force_exit_armed = True
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    conversation_workflows: dict[str, str] = {}
    conversation_history: dict[str, list[dict[str, str]]] = {}
    shared_http = httpx.AsyncClient(base_url=server_url, timeout=120.0)
    _adapter_bg_tasks: set[asyncio.Task[None]] = set()

    configured_bot_name = getattr(config, "bot_name", "") or None
    configured_personality = getattr(config, "personality", "") or ""
    configured_projects = getattr(config, "projects", []) or []

    _bot = get_bot_name(configured_bot_name)
    _bare = format_bare_prefix(bot_name=configured_bot_name)
    _ADAPTER_CONTEXT = (
        f"You are {_bot}, a messaging AI assistant. Short replies (1-3 sentences). "
        f"Answer directly — no commands, no numbered options, no {_bare} prefix."
    )
    if configured_personality:
        _ADAPTER_CONTEXT += f" Personality: {configured_personality}."
    if configured_projects:
        _ADAPTER_CONTEXT += (
            f" You focus especially on these projects: "
            f"{', '.join(configured_projects)}."
        )

    def _server_unavailable_message() -> str:
        return (
            f"{_bot} server unavailable at {server_url}. "
            "Please start dan-serve and try again."
        )

    def _stream_unavailable_message() -> str:
        return (
            f"{_bot} accepted the request, but the reply stream failed. "
            "Please try again."
        )

    async def _ensure_scratch(http: httpx.AsyncClient, conv_id: str) -> str:
        """Get or create a per-conversation workflow on the server."""
        wf_id = conversation_workflows.get(conv_id)
        if wf_id:
            return wf_id

        sanitized = re.sub(r"[^A-Za-z0-9]", "", conv_id)[-12:] or "default"
        wf_id = f"_adapter_{sanitized}"
        try:
            resp = await http.get(f"/api/graphs/{wf_id}")
            if resp.status_code == 404:
                await http.post(
                    "/api/graphs",
                    json={"graph_id": wf_id, "data": {"nodes": [], "edges": []}},
                )
        except Exception:
            pass
        conversation_workflows[conv_id] = wf_id
        return wf_id

    async def _dispatch_to_server(ext_id: str, txt: str, att_path: str | None = None) -> None:
        try:
            wf_id = await _ensure_scratch(shared_http, ext_id)

            history = conversation_history.get(ext_id, [])
            history.append({"role": "user", "content": txt})
            if len(history) > 40:
                history = history[-40:]
            conversation_history[ext_id] = history

            history_with_context = [
                {"role": "system", "content": _ADAPTER_CONTEXT},
                *history,
            ]

            if att_path and att_path.lower().endswith(".pdf"):
                txt = f"Please review this PDF: {att_path}\n{txt}" if txt else f"Please review this PDF: {att_path}"

            body: dict[str, Any] = {
                "workflow_id": wf_id,
                "message": txt,
                "history": history_with_context,
                "thread_id": str(ext_id),
                "mode": "auto",
                "surface": _surface_name_for_adapter_type(adapter_type),
            }
            if att_path:
                body["attachment_path"] = att_path

            resp = await shared_http.post("/api/chat/message", json=body)
            if resp.status_code != 200:
                await adapter.send_prompt(ext_id, f"Error: {resp.text}", None)
                return

            payload = resp.json()
            channel_id = payload.get("stream_channel_id")
            logger.info("Chat response for %s: channel=%s status=%s", ext_id, channel_id, payload.get("status"))
            if not channel_id:
                content = payload.get("content", "")
                if content:
                    await adapter.send_prompt(ext_id, content, None)
                    history.append({"role": "assistant", "content": content})
                else:
                    logger.warning("No channel and no content in response: %s", payload)
                return

            stream_events: list[dict[str, Any]] = []

            try:
                import websockets
                ws_url = server_url.replace("http://", "ws://").replace("https://", "wss://")
                url = f"{ws_url}/api/chat/{channel_id}/events"
                logger.info("Connecting to WS: %s", url)
                async with websockets.connect(url, ping_interval=None, ping_timeout=None) as ws:
                    async for ws_msg in ws:
                        event = json.loads(ws_msg)
                        if isinstance(event, dict):
                            evt_type = event.get("type", "")
                            logger.info("WS event: %s", evt_type)
                            stream_events.append(event)
                        if isinstance(event, dict) and event.get("type") in ("chat_complete", "chat_interrupted", "chat_error"):
                            break
                logger.info("WS stream finished, %d events collected", len(stream_events))
            except ImportError:
                logger.error("websockets not installed — cannot stream chat events")
                stream_events.append({
                    "type": "chat_complete",
                    "content": "(streaming unavailable — websockets not installed)",
                })
            except Exception as ws_exc:
                logger.error("WS stream error: %s", ws_exc, exc_info=True)
                if history and history[-1].get("role") == "user":
                    history.pop()
                try:
                    await adapter.send_prompt(ext_id, _stream_unavailable_message(), None)
                except Exception:
                    pass
                return

            full_reply, mutation_plan, attachment_paths, poll_requests = _consume_chat_stream_events(stream_events)

            if mutation_plan and config.auto_approve:
                try:
                    apply_resp = await shared_http.post(
                        f"/api/graphs/{wf_id}/apply-mutation",
                        json={"mutation_plan": mutation_plan},
                    )
                    if apply_resp.status_code == 200:
                        full_reply += "\n\n✅ Mutation applied."
                    else:
                        full_reply += f"\n\n❌ Apply failed: {apply_resp.text[:200]}"
                except Exception as apply_exc:
                    full_reply += f"\n\n❌ Apply error: {apply_exc}"

            if full_reply:
                clean_reply = _strip_dan_prefix(full_reply)
                if clean_reply:
                    from dan.server.concierge.actions import split_message_for_surface, strip_html_for_messaging
                    if adapter_type in ("whatsapp", "whatsapp-web", "telegram"):
                        clean_reply = strip_html_for_messaging(clean_reply)
                    parts = split_message_for_surface(clean_reply, adapter_type)
                    for part in parts:
                        logger.info("Sending reply to %s (%d chars)", ext_id, len(part))
                        await adapter.send_prompt(ext_id, part, None)
                        if len(parts) > 1:
                            await asyncio.sleep(0.5)
                history.append({"role": "assistant", "content": full_reply})
            else:
                logger.warning("No reply content to send for %s (events: %d)", ext_id, len(stream_events))

            for fp in attachment_paths:
                if hasattr(adapter, "send_file"):
                    try:
                        await adapter.send_file(ext_id, fp)
                    except Exception as exc:
                        logger.warning("Failed to send file %s: %s", fp, exc)

            for pr in poll_requests:
                if hasattr(adapter, "send_poll_for_session"):
                    try:
                        await adapter.send_poll_for_session(
                            ext_id,
                            pr.get("question", ""),
                            pr.get("options", []),
                            is_anonymous=pr.get("is_anonymous", False),
                            allows_multiple=pr.get("allows_multiple", False),
                        )
                    except Exception as exc:
                        logger.warning("Failed to send poll: %s", exc)

        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            logger.warning("Chat-mode server unavailable for %s: %s", ext_id, exc)
            history = conversation_history.get(ext_id, [])
            if history and history[-1].get("role") == "user":
                history.pop()
            try:
                await adapter.send_prompt(ext_id, _server_unavailable_message(), None)
            except Exception:
                pass
        except Exception:
            logger.exception("Chat-mode message handling failed for %s", ext_id)
            history = conversation_history.get(ext_id, [])
            if history and history[-1].get("role") == "user":
                history.pop()
            try:
                await adapter.send_prompt(ext_id, config.error_message, None)
            except Exception:
                pass

    async def on_new_message(external_id: str, text: str) -> None:
        if hasattr(adapter, "register_session"):
            key = int(external_id) if external_id.isdigit() else external_id
            adapter.register_session(external_id, key)

        reply_prefix, media_text = _split_reply_prefix_lines(text)
        attachment_path = None
        if media_text.startswith("[Attachment: "):
            end = media_text.find("]\n")
            if end > 0:
                attachment_path = media_text[len("[Attachment: "):end].strip()
                remaining_text = media_text[end + 2:].strip()
            elif media_text.endswith("]"):
                attachment_path = media_text[len("[Attachment: "):-1].strip()
                remaining_text = ""
            else:
                remaining_text = media_text
            text = "\n".join(part for part in (reply_prefix, remaining_text) if part).strip()

        if media_text.startswith("[Voice note: "):
            end = media_text.find("]", len("[Voice note: "))
            voice_path = media_text[len("[Voice note: "):end].strip() if end > 0 else media_text[len("[Voice note: "):].strip()
            remaining_text = media_text[end + 1:].strip() if end > 0 else ""
            try:
                from dan.adapters.whatsapp_web_adapter import transcribe_audio
                transcription = await transcribe_audio(voice_path)
            except ImportError:
                transcription = None
            if transcription:
                text = transcription
            else:
                await adapter.send_prompt(
                    external_id,
                    "I received your voice message but couldn't transcribe it. "
                    "Please send as text.",
                    None,
                )
                return
            text = "\n".join(
                part
                for part in (reply_prefix, transcription, remaining_text)
                if part
            ).strip()

        stripped = text.strip()

        if stripped.startswith("/"):
            cmd = stripped.split()[0].lower()
            if cmd == "/help":
                await adapter.send_prompt(
                    external_id,
                    "Available commands: /find <query>, /send <path>, /status, /cancel, /show, /list, /mcp, /model [name]\n"
                    "Or just type naturally — I can do anything!",
                    None,
                )
                return
            translated = _translate_slash_command(stripped)
            if translated:
                _task = asyncio.create_task(_dispatch_to_server(external_id, translated))
                _adapter_bg_tasks.add(_task)
                _task.add_done_callback(_adapter_bg_tasks.discard)
            else:
                # Forward unrecognized slash commands to the server —
                # the concierge handles /mcp, /save, /build-*, /memory-*, etc.
                _task = asyncio.create_task(_dispatch_to_server(external_id, stripped))
                _adapter_bg_tasks.add(_task)
                _task.add_done_callback(_adapter_bg_tasks.discard)
            return

        _task = asyncio.create_task(_dispatch_to_server(external_id, text, attachment_path))
        _adapter_bg_tasks.add(_task)
        _task.add_done_callback(_adapter_bg_tasks.discard)

    adapter.set_message_callback(on_new_message)

    _print_status_chat(config, server_url)
    await stop_event.wait()
    await shared_http.aclose()
    await adapter.stop()


def _print_status_chat(config: Any, server_url: str) -> None:
    try:
        from rich.console import Console
        from rich.panel import Panel
        console = Console()
        console.print(Panel(
            f"[bold green]Adapter running (chat mode)[/]\n"
            f"Server: {server_url}\n"
            f"Send any message to start chatting with "
            f"{get_bot_name(getattr(config, 'bot_name', None) or None)}.",
            title="dan-adapter",
        ))
    except ImportError:
        print(f"dan-adapter: running in chat mode (server={server_url})")


# ---------------------------------------------------------------------------
# Workflow-mode runner — run a specific workflow per message (legacy)
# ---------------------------------------------------------------------------

async def _run_adapter(adapter: Any, renderer: Any, config: Any) -> None:
    """Start *adapter*, register signal handlers, and block until stopped."""
    from dan.adapters.base import (
        AdapterSessionStore,
        MessagingHumanRenderer,
        SessionState,
        should_trigger,
    )
    from dan.engine.executor import EngineConfig
    from dan.engine.scheduler import Engine
    from dan.loader import load

    session_store = AdapterSessionStore()
    human_renderer = MessagingHumanRenderer(adapter, session_store)

    graph = load(config.workflow_path)

    await adapter.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    async def on_new_message(external_id: str, text: str) -> None:
        if not should_trigger(text, config):
            return

        session = await session_store.get_by_external(external_id)
        if session is not None and session.state in (
            SessionState.RUNNING,
            SessionState.AWAITING_HUMAN,
        ):
            return

        session = await session_store.create(external_id)
        human_renderer.active_session_id = session.session_id

        if hasattr(adapter, "register_session"):
            key = int(external_id) if external_id.isdigit() else external_id
            adapter.register_session(session.session_id, key)

        await session_store.update_state(session.session_id, SessionState.RUNNING)

        engine = Engine(
            config=EngineConfig(),
            human_input_callback=human_renderer.as_callback(),
        )

        try:
            result = await engine.run(graph, inputs={"message": text})
            await adapter.send_result(session.session_id, result.outputs)
            await session_store.update_state(session.session_id, SessionState.COMPLETED)
        except Exception as exc:
            logger.exception("Workflow run failed")
            await session_store.update_state(session.session_id, SessionState.FAILED)
            try:
                await adapter.send_prompt(
                    session.session_id, config.error_message, None,
                )
            except Exception:
                pass
        finally:
            if hasattr(adapter, "unregister_session"):
                adapter.unregister_session(session.session_id)

    adapter._on_new_message = on_new_message

    _print_status(config)
    await stop_event.wait()
    await adapter.stop()


def _print_status(config: Any) -> None:
    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        console.print(Panel(
            f"[bold green]Adapter running (workflow mode)[/]\n"
            f"Workflow: {config.workflow_path}\n"
            f"Trigger:  {config.trigger_mode}",
            title="dan-adapter",
        ))
    except ImportError:
        print(f"dan-adapter: running (workflow={config.workflow_path}, trigger={config.trigger_mode})")


# ---------------------------------------------------------------------------
# Subcommand builders
# ---------------------------------------------------------------------------

def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workflow", "-w", default=None,
        help="Path to workflow file. When omitted, runs in chat mode (like dan-chat over messaging).",
    )
    parser.add_argument("--timeout", type=float, default=300, help="Response timeout (seconds)")
    parser.add_argument(
        "--trigger-mode", choices=["keyword", "always", "pattern"], default="always",
    )
    parser.add_argument("--trigger-pattern", default=None)
    parser.add_argument("--welcome-message", default=None)
    parser.add_argument("--error-message", default=None)
    parser.add_argument(
        "--server", default=None,
        help="DAN server URL for chat mode (default: $DAN_SERVER_URL or http://127.0.0.1:8000)",
    )


def _build_email_parser(sub: Any) -> None:
    p = sub.add_parser("email", help="Start email adapter (IMAP + SMTP)")
    _add_common_args(p)
    p.add_argument("--imap-host", required=True)
    p.add_argument("--imap-port", type=int, default=993)
    p.add_argument("--imap-user", required=True)
    p.add_argument("--imap-password", required=True)
    p.add_argument("--smtp-host", required=True)
    p.add_argument("--smtp-port", type=int, default=587)
    p.add_argument("--smtp-user", default=None)
    p.add_argument("--smtp-password", default=None)
    p.add_argument("--target-email", required=True, help="Recipient email address")
    p.add_argument("--subject-filter", default="")
    p.add_argument("--poll-interval", type=float, default=10.0)
    p.set_defaults(adapter_type="email")


def _build_telegram_parser(sub: Any) -> None:
    p = sub.add_parser("telegram", help="Start Telegram bot adapter")
    _add_common_args(p)
    p.add_argument(
        "--bot-token", default=None,
        help="Bot token from BotFather (or set DAN_TELEGRAM_BOT_TOKEN env var)",
    )
    p.add_argument("--allowed-chat-ids", nargs="*", type=int, default=[])
    p.add_argument("--webhook-url", default=None)
    p.set_defaults(adapter_type="telegram")


def _build_whatsapp_parser(sub: Any) -> None:
    p = sub.add_parser("whatsapp", help="Start WhatsApp Business API adapter")
    _add_common_args(p)
    p.add_argument("--access-token", required=True)
    p.add_argument("--phone-number-id", required=True)
    p.add_argument("--webhook-url", default="")
    p.add_argument("--verify-token", default="dan-verify")
    p.add_argument("--app-secret", default="")
    p.set_defaults(adapter_type="whatsapp")


def _build_whatsapp_web_parser(sub: Any) -> None:
    p = sub.add_parser("whatsapp-web", help="Start WhatsApp Web adapter (personal QR pairing)")
    _add_common_args(p)
    p.add_argument("--db-path", default="", help="SQLite DB path for session (default: ~/.dan/whatsapp-web/session.sqlite3)")
    p.add_argument("--allowed-jids", nargs="*", default=[], help="Restrict to these phone JIDs (e.g. 1234567890)")
    p.set_defaults(adapter_type="whatsapp-web")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_from_config_file(path: str) -> tuple[str, Any]:
    """Load a JSON config file and return (adapter_type, config_object)."""
    data = json.loads(Path(path).read_text())
    adapter_type = data.pop("adapter_type", "")
    if not adapter_type:
        print("Error: config file must include 'adapter_type' (email/telegram/whatsapp)", file=sys.stderr)
        sys.exit(1)

    if adapter_type == "email":
        from dan.adapters.email_adapter import EmailAdapterConfig
        return adapter_type, EmailAdapterConfig(**data)
    elif adapter_type == "telegram":
        from dan.adapters.telegram_adapter import TelegramAdapterConfig
        return adapter_type, TelegramAdapterConfig(**data)
    elif adapter_type == "whatsapp":
        from dan.adapters.whatsapp_adapter import WhatsAppAdapterConfig
        return adapter_type, WhatsAppAdapterConfig(**data)
    elif adapter_type == "whatsapp-web":
        from dan.adapters.whatsapp_web_adapter import WhatsAppWebAdapterConfig
        return adapter_type, WhatsAppWebAdapterConfig(**data)
    else:
        print(f"Error: unknown adapter_type '{adapter_type}'", file=sys.stderr)
        sys.exit(1)


def _build_config_from_args(args: argparse.Namespace) -> tuple[str, Any]:
    """Build a typed config from parsed CLI arguments."""
    adapter_type = args.adapter_type
    common: dict[str, Any] = {
        "workflow_path": args.workflow or "",
        "timeout": args.timeout,
        "trigger_mode": args.trigger_mode,
    }
    if args.trigger_pattern:
        common["trigger_pattern"] = args.trigger_pattern
    if args.welcome_message:
        common["welcome_message"] = args.welcome_message
    if args.error_message:
        common["error_message"] = args.error_message
    if getattr(args, "server", None):
        common["server_url"] = args.server

    if adapter_type == "email":
        from dan.adapters.email_adapter import EmailAdapterConfig
        return adapter_type, EmailAdapterConfig(
            **common,
            imap_host=args.imap_host,
            imap_port=args.imap_port,
            imap_user=args.imap_user,
            imap_password=args.imap_password,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
            smtp_user=args.smtp_user or args.imap_user,
            smtp_password=args.smtp_password or args.imap_password,
            target_email=args.target_email,
            subject_filter=args.subject_filter,
            poll_interval=args.poll_interval,
        )
    elif adapter_type == "telegram":
        from dan.adapters.telegram_adapter import TelegramAdapterConfig
        token = args.bot_token or os.environ.get("DAN_TELEGRAM_BOT_TOKEN", "")
        if not token:
            print(
                "Error: bot token required. Pass --bot-token or set DAN_TELEGRAM_BOT_TOKEN.",
                file=sys.stderr,
            )
            sys.exit(1)
        return adapter_type, TelegramAdapterConfig(
            **common,
            bot_token=token,
            allowed_chat_ids=args.allowed_chat_ids or [],
            webhook_url=args.webhook_url,
        )
    elif adapter_type == "whatsapp":
        from dan.adapters.whatsapp_adapter import WhatsAppAdapterConfig
        return adapter_type, WhatsAppAdapterConfig(
            **common,
            access_token=args.access_token,
            phone_number_id=args.phone_number_id,
            webhook_url=args.webhook_url,
            verify_token=args.verify_token,
            app_secret=args.app_secret,
        )
    elif adapter_type == "whatsapp-web":
        from dan.adapters.whatsapp_web_adapter import WhatsAppWebAdapterConfig
        return adapter_type, WhatsAppWebAdapterConfig(
            **common,
            db_path=args.db_path,
            allowed_jids=args.allowed_jids or [],
        )
    else:
        print(f"Error: unknown adapter type '{adapter_type}'", file=sys.stderr)
        sys.exit(1)


def _create_adapter(adapter_type: str, config: Any) -> Any:
    if adapter_type == "email":
        from dan.adapters.email_adapter import EmailAdapter
        return EmailAdapter(config)
    elif adapter_type == "telegram":
        from dan.adapters.telegram_adapter import TelegramAdapter
        return TelegramAdapter(config)
    elif adapter_type == "whatsapp":
        from dan.adapters.whatsapp_adapter import WhatsAppAdapter
        return WhatsAppAdapter(config)
    elif adapter_type == "whatsapp-web":
        from dan.adapters.whatsapp_web_adapter import WhatsAppWebAdapter
        return WhatsAppWebAdapter(config)
    raise ValueError(f"Unknown adapter type: {adapter_type}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    from dan.cli import load_env

    load_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(
        prog="dan-adapter",
        description="Start a messaging adapter for DAN workflows",
    )
    parser.add_argument(
        "--config", "-c", default=None,
        help="Path to JSON config file (alternative to subcommand flags)",
    )

    sub = parser.add_subparsers(dest="subcommand")
    _build_email_parser(sub)
    _build_telegram_parser(sub)
    _build_whatsapp_parser(sub)
    _build_whatsapp_web_parser(sub)

    args = parser.parse_args()

    if args.config:
        adapter_type, config = _load_from_config_file(args.config)
    elif args.subcommand:
        adapter_type, config = _build_config_from_args(args)
    else:
        parser.print_help()
        sys.exit(1)

    adapter = _create_adapter(adapter_type, config)

    chat_mode = not config.workflow_path
    try:
        if chat_mode:
            asyncio.run(_run_adapter_chat_mode(adapter, config, adapter_type))
        else:
            asyncio.run(_run_adapter(adapter, None, config))
    except KeyboardInterrupt:
        print("\nShutting down...", file=sys.stderr)
        try:
            asyncio.get_event_loop().run_until_complete(adapter.stop())
        except Exception:
            pass
        os._exit(0)


if __name__ == "__main__":
    main()
