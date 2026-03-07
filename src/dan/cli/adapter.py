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
from difflib import SequenceMatcher
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_DAN_PREFIX = "[DAN]"
_DAN_SCOPED_PREFIX = "[DAN - "


# ---------------------------------------------------------------------------
# Pending action tracking (per-conversation)
# ---------------------------------------------------------------------------

@dataclass
class _PendingAction:
    kind: str          # 'find', 'send', 'apply_mutation'
    query: str         # search term or file path
    results: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _strip_dan_prefix(text: str) -> str:
    clean = text.strip()
    while clean.startswith(f"{_DAN_PREFIX} ") or clean.startswith(_DAN_SCOPED_PREFIX):
        idx = clean.find("] ")
        if idx >= 0:
            clean = clean[idx + 2:]
        elif clean.startswith(_DAN_SCOPED_PREFIX):
            clean = clean[len(_DAN_SCOPED_PREFIX):]
        else:
            clean = clean[len(_DAN_PREFIX) + 1:]
        clean = clean.strip()
    return clean


# ---------------------------------------------------------------------------
# Heuristic intent classifier (no LLM call)
# ---------------------------------------------------------------------------

_CONTINUATION_WORDS = frozenset({
    "yes", "do it", "just do it", "go ahead", "ok", "sure",
    "yes please", "send it", "just send it", "please do", "go",
    "yeah", "yep", "yup", "okay", "please", "do this", "do that",
})

_CONTINUATION_PHRASES = (
    "help me do", "just do", "can you do", "can you just",
    "do this task", "go for it", "send it to me", "what about the file",
    "i do not see", "good catch",
)


def _looks_like_apply_reply(text: str) -> bool:
    clean = text.strip().lower().rstrip(".!?,;:")
    if not clean:
        return False
    if re.fullmatch(r"apply(?:\s+it)?(?:\s+please)?", clean):
        return True
    if clean.startswith("apply"):
        return True
    return SequenceMatcher(None, clean, "apply").ratio() >= 0.88


def _extract_search_query_from_send_request(text: str) -> str | None:
    from dan.server.concierge import extract_search_query_from_send_request as _shared_extract

    return _shared_extract(text)


def _classify_adapter_intent(
    text: str,
    pending: _PendingAction | None,
) -> tuple[str, str]:
    """Classify user intent with keyword heuristics.

    Returns ``(intent, param)`` where *intent* is one of:

    - ``continue``      — user agrees to execute pending action
    - ``file_search``   — find a file locally (param = search query)
    - ``file_send``     — send a specific file (param = path or name)
    - ``workflow``      — build / edit a workflow
    - ``conversation``  — general question or chat (route to LLM)
    """
    lower = text.lower().strip()
    clean = lower.rstrip(".!?,")

    # ── Number selection from pending find results ─────────────────
    if pending is not None and pending.results:
        if clean.isdigit():
            idx = int(clean) - 1
            if 0 <= idx < len(pending.results):
                return "file_send", pending.results[idx]
        for prefix in ("send ", "number ", "#"):
            if clean.startswith(prefix):
                rest = clean[len(prefix):].strip()
                if rest.isdigit():
                    idx = int(rest) - 1
                    if 0 <= idx < len(pending.results):
                        return "file_send", pending.results[idx]

    # ── Continuation: user agrees to pending action ────────────────
    if pending is not None:
        if clean in _CONTINUATION_WORDS:
            return "continue", ""
        for phrase in _CONTINUATION_PHRASES:
            if phrase in clean:
                return "continue", ""

    polite_send_query = _extract_search_query_from_send_request(text)
    if polite_send_query:
        return "file_search", polite_send_query

    # ── File send: "send me X", "send the X" ──────────────────────
    for prefix in ("send me the ", "send me ", "send the ", "get me the ", "get me "):
        if lower.startswith(prefix):
            return "file_send", text[len(prefix):].strip().rstrip(".!?,")

    # ── File search: "find X", "look for X" ───────────────────────
    for prefix in (
        "find ", "search for ", "look for ", "locate ", "where is ",
        "can you find ", "help me find ",
    ):
        if lower.startswith(prefix):
            return "file_search", text[len(prefix):].strip().rstrip(".!?,")

    # ── File-related phrases anywhere ──────────────────────────────
    file_cues = (
        "the document", "the file", "that file", "that document",
        "the doc", "that doc", "do you have",
    )
    if any(cue in lower for cue in file_cues):
        return "file_search", text.strip().rstrip(".!?,")

    # ── Workflow build / edit ──────────────────────────────────────
    workflow_cues = (
        "build a", "create a workflow", "create a pipeline",
        "build me a", "make a workflow", "add a node",
        "add a step", "modify the workflow", "edit the workflow",
    )
    if any(cue in lower for cue in workflow_cues):
        return "workflow", ""

    # ── Default: general conversation ──────────────────────────────
    return "conversation", ""


# ---------------------------------------------------------------------------
# Local file search helpers
# ---------------------------------------------------------------------------

def _normalize_search_text(text: str) -> str:
    from dan.server.concierge.classifier import _normalize_search_text as _shared_normalize

    return _shared_normalize(text)


def _score_file_match(query_norm: str, query_tokens: list[str], path: Path) -> float:
    from dan.server.concierge.classifier import _score_file_match as _shared_score

    return _shared_score(query_norm, query_tokens, path)


def _search_local_files(
    query: str,
    search_dirs: list[Path],
    *,
    limit: int = 10,
) -> list[str]:
    from dan.server.concierge import search_local_files as _shared_search

    return _shared_search(query, search_dirs, limit=limit)


def _surface_name_for_adapter_type(adapter_type: str) -> str:
    known = {"email", "telegram", "whatsapp", "whatsapp-web"}
    return adapter_type if adapter_type in known else "server"


# ---------------------------------------------------------------------------
# Chat stream helpers
# ---------------------------------------------------------------------------

def _consume_chat_stream_events(
    events: list[dict[str, Any]],
) -> tuple[str, dict[str, Any] | None]:
    """Merge chat stream events into a final reply and optional mutation plan.

    Some chat flows stream incremental ``chat_token`` events, while others
    return the full assistant text only on ``chat_complete.content``.  The
    adapter must handle both.
    """
    collected: list[str] = []
    mutation_plan: dict[str, Any] | None = None
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
        elif evt_type == "chat_mutation":
            mutation_plan = event.get("mutation_plan")
            desc = ""
            if mutation_plan:
                desc = mutation_plan.get("description", "")
                ops = mutation_plan.get("operations", [])
                desc = f"{desc}\n({len(ops)} operations)"
            collected.append(f"\n📋 Mutation proposed: {desc}")
        elif evt_type == "chat_complete":
            complete_content = event.get("content", "") or ""
            break
        elif evt_type == "chat_error":
            error_message = str(event.get("error", "") or "").strip()
            break

    full_reply = "".join(collected).strip()
    complete_content = complete_content.strip()
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
    return full_reply, mutation_plan


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
    conversation_pending: dict[str, _PendingAction] = {}

    _ADAPTER_CONTEXT = f"""\
You are DAN, a personal AI assistant connected via messaging.
Keep replies short (1-3 sentences) — this is a chat app.
Do NOT suggest commands, numbered options, or ask "would you like me to…?".
Just answer directly. Do NOT add {_DAN_PREFIX} prefix."""

    def _server_unavailable_message() -> str:
        return (
            f"DAN server unavailable at {server_url}. "
            "Please start dan-serve and try again."
        )

    def _stream_unavailable_message() -> str:
        return (
            "DAN accepted the request, but the reply stream failed. "
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

    def _extract_embedded_commands(reply: str) -> list[str]:
        """Extract /find and /send commands embedded in LLM reply text."""
        cmds: list[str] = []
        for line in reply.splitlines():
            stripped = line.strip()
            # Match lines like: /find late payment, "Type: /send foo.pdf", etc.
            for prefix in ("/find ", "/send "):
                idx = stripped.find(prefix)
                if idx >= 0:
                    cmd = stripped[idx:].strip()
                    if cmd.endswith("`"):
                        cmd = cmd.rstrip("`")
                    if cmd.endswith("*"):
                        cmd = cmd.rstrip("*")
                    if len(cmd) > len(prefix):
                        cmds.append(cmd)
                    break
        return cmds

    async def _handle_local_command(external_id: str, text: str) -> bool:
        """Handle adapter-local commands. Returns True if handled."""
        stripped = text.strip()

        if stripped.startswith("/send "):
            await _handle_send_command(external_id, stripped[6:].strip())
            return True

        if stripped.startswith("/find "):
            await _handle_find_command(external_id, stripped[6:].strip())
            return True

        return False

    async def _handle_send_command(external_id: str, query: str) -> None:
        """Send a local file to the user via WhatsApp."""
        path = Path(query).expanduser()

        if not path.exists():
            candidates = _search_local_files(query, [Path.home()], limit=5)

            if not candidates:
                await adapter.send_prompt(
                    external_id,
                    f"File not found: {query}\n"
                    f"Try /find <keyword> to search, or use a full path.",
                    None,
                )
                return

            if len(candidates) == 1:
                path = Path(candidates[0])
            else:
                listing = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(candidates))
                await adapter.send_prompt(
                    external_id,
                    f"Multiple matches:\n{listing}\n\nUse /send <full_path> to pick one.",
                    None,
                )
                return

        if not path.is_file():
            await adapter.send_prompt(external_id, f"Not a file: {path}", None)
            return

        if hasattr(adapter, "send_file"):
            result = await adapter.send_file(external_id, str(path))
            if result.get("ok"):
                await adapter.send_prompt(
                    external_id,
                    f"Sent: {path.name} ({result.get('size_mb', '?')} MB)",
                    None,
                )
            else:
                await adapter.send_prompt(
                    external_id,
                    f"Failed to send: {result.get('error', 'unknown error')}",
                    None,
                )
        else:
            await adapter.send_prompt(
                external_id,
                f"File sending not supported on this adapter.",
                None,
            )

    async def _handle_find_command(external_id: str, query: str) -> None:
        """Search for local files matching a query — auto-send single match."""
        search_dirs = [
            Path.home() / "Dropbox",
            Path.home() / "Documents",
            Path.home() / "Desktop",
            Path.home() / "Downloads",
        ]

        results = _search_local_files(query, search_dirs, limit=10)

        conversation_pending[external_id] = _PendingAction("find", query, results)

        if not results:
            await adapter.send_prompt(
                external_id,
                f"No files found matching '{query}' in Dropbox, Documents, Desktop, Downloads.",
                None,
            )
            return

        if len(results) == 1:
            await _handle_send_command(external_id, results[0])
            return

        listing = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(results))
        await adapter.send_prompt(
            external_id,
            f"Found {len(results)} file(s):\n{listing}\n\nReply with a number to send.",
            None,
        )

    async def on_new_message(external_id: str, text: str) -> None:
        if hasattr(adapter, "register_session"):
            key = int(external_id) if external_id.isdigit() else external_id
            adapter.register_session(external_id, key)

        # ── Explicit /commands (/find, /send) ──────────────────────
        if await _handle_local_command(external_id, text):
            return

        # ── Reply to local /find clarification (number selection) ───
        # /find sets conversation_pending; server concierge has no access to it.
        pending = conversation_pending.get(external_id)
        if pending is not None and pending.kind == "find" and pending.results:
            clean = text.strip().lower().rstrip(".!?,")
            if clean.isdigit():
                idx = int(clean) - 1
                if 0 <= idx < len(pending.results):
                    await _handle_send_command(external_id, pending.results[idx])
                    conversation_pending.pop(external_id, None)
                    return
            for prefix in ("send ", "number ", "#"):
                if clean.startswith(prefix):
                    rest = clean[len(prefix):].strip()
                    if rest.isdigit():
                        idx = int(rest) - 1
                        if 0 <= idx < len(pending.results):
                            await _handle_send_command(external_id, pending.results[idx])
                            conversation_pending.pop(external_id, None)
                            return

        if pending is not None and pending.kind == "apply_mutation" and _looks_like_apply_reply(text):
            mutation_plan = pending.metadata.get("mutation_plan")
            workflow_id = pending.query
            if mutation_plan:
                try:
                    async with httpx.AsyncClient(base_url=server_url, timeout=120.0) as http:
                        apply_resp = await http.post(
                            f"/api/graphs/{workflow_id}/apply-mutation",
                            json={"mutation_plan": mutation_plan},
                        )
                    if apply_resp.status_code == 200:
                        await adapter.send_prompt(external_id, "Applied.", None)
                    else:
                        await adapter.send_prompt(
                            external_id,
                            f"Apply failed: {apply_resp.text[:200]}",
                            None,
                        )
                except Exception as exc:
                    await adapter.send_prompt(external_id, f"Apply error: {exc}", None)
                finally:
                    conversation_pending.pop(external_id, None)
                return

        # ── Route to server chat API (shared concierge) ─────────────
        # All other messages go through the server's concierge.
        try:
            async with httpx.AsyncClient(base_url=server_url, timeout=120.0) as http:
                wf_id = await _ensure_scratch(http, external_id)

                history = conversation_history.get(external_id, [])
                history.append({"role": "user", "content": text})
                if len(history) > 40:
                    history = history[-40:]
                conversation_history[external_id] = history

                history_with_context = [
                    {"role": "system", "content": _ADAPTER_CONTEXT},
                    *history,
                ]

                resp = await http.post("/api/chat/message", json={
                    "workflow_id": wf_id,
                    "message": text,
                    "history": history_with_context,
                    "thread_id": str(external_id),
                    "mode": "auto",
                    "surface": _surface_name_for_adapter_type(adapter_type),
                })
                if resp.status_code != 200:
                    await adapter.send_prompt(external_id, f"Error: {resp.text}", None)
                    return

                payload = resp.json()
                channel_id = payload.get("stream_channel_id")
                logger.info("Chat response for %s: channel=%s", external_id, channel_id)
                if not channel_id:
                    content = payload.get("content", "")
                    if content:
                        await adapter.send_prompt(external_id, content, None)
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
                    async with websockets.connect(url) as ws:
                        async for msg in ws:
                            event = json.loads(msg)
                            if isinstance(event, dict):
                                evt_type = event.get("type", "")
                                logger.info("WS event: %s", evt_type)
                                stream_events.append(event)
                            if isinstance(event, dict) and event.get("type") == "chat_complete":
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
                        await adapter.send_prompt(external_id, _stream_unavailable_message(), None)
                    except Exception:
                        pass
                    return

                full_reply, mutation_plan = _consume_chat_stream_events(stream_events)

                if mutation_plan and config.auto_approve:
                    try:
                        apply_resp = await http.post(
                            f"/api/graphs/{wf_id}/apply-mutation",
                            json={"mutation_plan": mutation_plan},
                        )
                        if apply_resp.status_code == 200:
                            full_reply += "\n\n✅ Mutation applied."
                        else:
                            full_reply += f"\n\n❌ Apply failed: {apply_resp.text[:200]}"
                    except Exception as apply_exc:
                        full_reply += f"\n\n❌ Apply error: {apply_exc}"
                elif mutation_plan:
                    conversation_pending[external_id] = _PendingAction(
                        "apply_mutation",
                        wf_id,
                        metadata={"mutation_plan": mutation_plan},
                    )
                    full_reply += "\n\n(Reply 'apply' to apply, or describe changes.)"

                if full_reply:
                    clean_reply = _strip_dan_prefix(full_reply)

                    # Concierge file response: "Found file: /path" — send the file instead of text
                    sent_file = False
                    if "Found file: " in clean_reply:
                        match = re.search(r"Found file:\s*(.+?)(?:\n|$)", clean_reply)
                        if match:
                            path_str = match.group(1).strip()
                            if Path(path_str).is_file() and hasattr(adapter, "send_file"):
                                await _handle_send_command(external_id, path_str)
                                sent_file = True

                    if not sent_file and clean_reply:
                        logger.info("Sending reply to %s (%d chars)", external_id, len(clean_reply))
                        await adapter.send_prompt(external_id, clean_reply, None)

                    embedded_cmds = _extract_embedded_commands(clean_reply)
                    for cmd in embedded_cmds:
                        logger.info("Auto-executing embedded command: %s", cmd)
                        await _handle_local_command(external_id, cmd)

                    history.append({"role": "assistant", "content": full_reply})
                else:
                    logger.warning("No reply content to send for %s (events: %d)", external_id, len(stream_events))

        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            logger.warning("Chat-mode server unavailable for %s: %s", external_id, exc)
            history = conversation_history.get(external_id, [])
            if history and history[-1].get("role") == "user":
                history.pop()
            try:
                await adapter.send_prompt(external_id, _server_unavailable_message(), None)
            except Exception:
                pass
        except Exception:
            logger.exception("Chat-mode message handling failed for %s", external_id)
            history = conversation_history.get(external_id, [])
            if history and history[-1].get("role") == "user":
                history.pop()
            try:
                await adapter.send_prompt(external_id, config.error_message, None)
            except Exception:
                pass

    adapter.set_message_callback(on_new_message)

    _print_status_chat(config, server_url)
    await stop_event.wait()
    await adapter.stop()


def _print_status_chat(config: Any, server_url: str) -> None:
    try:
        from rich.console import Console
        from rich.panel import Panel
        console = Console()
        console.print(Panel(
            f"[bold green]Adapter running (chat mode)[/]\n"
            f"Server: {server_url}\n"
            f"Send any message to start chatting with DAN.",
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
    p.add_argument("--bot-token", required=True)
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
        return adapter_type, TelegramAdapterConfig(
            **common,
            bot_token=args.bot_token,
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
