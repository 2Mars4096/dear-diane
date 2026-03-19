"""dan-chat — REPL that talks to the server's chat API for conversational workflow authoring."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
import os
import queue
import re
import readline
import threading
import time as _time
import sys
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from dan.cli.adapter import _is_progress_ack_event
from dan.server.startup import get_llm_api_key_status


def _compute_graph_revision(graph_dict: dict) -> str:
    """Approximate graph revision hash (fallback when server doesn't return one).

    The server normalizes through Pydantic before hashing, so this raw-dict
    hash may differ.  Prefer the ``graph_revision`` field from apply responses.
    """
    canonical = json.dumps(graph_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]

logger = logging.getLogger("dan.cli.chat")

_DEFAULT_URL = "http://127.0.0.1:8000"
_PING_TIMEOUT = 3.0


def _format_api_key_status_label() -> str:
    status = get_llm_api_key_status()
    if status == "configured":
        return "configured"
    if status == "placeholder":
        return "placeholder"
    return "missing"


def _format_estimated_cost(value: float) -> str:
    return f"~${value:.4f}"


def _friendly_http_error_message(
    status_code: int,
    body: str,
    *,
    action: str,
) -> str:
    detail = (body or "").strip()
    if status_code == 401:
        message = f"{action} failed: check your API key and provider/model configuration."
    elif status_code == 429:
        message = f"{action} failed: rate limited, please retry shortly."
    elif status_code == 404:
        message = f"{action} failed: requested resource or route was not found."
    elif status_code >= 500:
        message = f"{action} failed: server error, check logs."
    else:
        message = f"{action} failed ({status_code})."
    if detail:
        logger.debug("HTTP %s during %s: %s", status_code, action, detail)
    return message


def _progress_ack_text(event: dict[str, Any]) -> str:
    phase_label = str(event.get("phase_label", "") or "").strip()
    content = str(event.get("content", "") or "").strip()
    if phase_label:
        return f"Thinking... {phase_label}"
    if content:
        return content
    return "Thinking..."


class _CliProgressDisplay:
    """Render streamed progress events through the shared CLI renderer."""

    def __init__(self, verbosity: str | None = None) -> None:
        from dan.server.concierge.progress_ux import CLIProgressRenderer, resolve_verbosity

        self._renderer = CLIProgressRenderer()
        self._verbosity = verbosity or resolve_verbosity("cli")
        self._next_index = 0
        self._last_phase_label: str | None = None
        self._printed_any = False

    async def render(self, event: dict[str, Any]) -> list[str]:
        if not _is_progress_ack_event(event):
            return []

        phase_label = str(event.get("phase_label", "") or "").strip()
        message = _progress_ack_text(event)

        if self._verbosity == "minimal":
            if self._printed_any:
                return []
            self._printed_any = True
            if phase_label:
                await self._renderer.phase_update("progress", phase_label)
            else:
                await self._renderer.heartbeat(0.0, message)
            return self._drain()

        if phase_label:
            if self._verbosity == "compact" and phase_label == self._last_phase_label:
                return []
            self._last_phase_label = phase_label
            self._printed_any = True
            await self._renderer.phase_update("progress", phase_label)
            return self._drain()

        if self._verbosity == "full":
            self._printed_any = True
            await self._renderer.heartbeat(0.0, message)
            return self._drain()

        return []

    def reset(self) -> None:
        self._renderer.output.clear()
        self._next_index = 0
        self._last_phase_label = None
        self._printed_any = False

    def _drain(self) -> list[str]:
        lines = self._renderer.output[self._next_index :]
        self._next_index = len(self._renderer.output)
        return lines


def _looks_like_path_input(line: str) -> bool:
    """Treat absolute/relative paths as plain message text, not slash commands."""
    stripped = line.strip()
    if not stripped.startswith("/"):
        return False
    token = stripped.split()[0]
    if token in {"/", "/help", "/exit"}:
        return False
    if token.startswith("//"):
        return False
    if token.startswith(("/Users/", "/home/", "/tmp/", "/var/", "/opt/", "/usr/")):
        return True
    if "/" in token[1:]:
        return True
    return False


def _is_slash_command_input(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("/") and not _looks_like_path_input(stripped)


class ChatClient:
    """Standalone chat client using httpx + websockets. Does not extend DanClient (21-7)."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = (
            base_url
            or os.environ.get("DAN_SERVER_URL")
            or _DEFAULT_URL
        ).rstrip("/")
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def ping(self) -> tuple[bool, str | None]:
        """Check if server is available. Returns (ok, error_message)."""
        try:
            http = await self._get_http()
            resp = await http.get("/health", timeout=_PING_TIMEOUT)
            return (resp.status_code == 200, None)
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as e:
            return (False, str(e))

    async def get_health(self) -> dict[str, Any]:
        http = await self._get_http()
        resp = await http.get("/health", timeout=_PING_TIMEOUT)
        if resp.status_code != 200:
            raise RuntimeError(
                _friendly_http_error_message(
                    resp.status_code,
                    resp.text,
                    action="Health check",
                )
            )
        payload = resp.json()
        return payload if isinstance(payload, dict) else {"status": "unknown"}

    async def send_chat_message(
        self,
        workflow_id: str,
        message: str,
        *,
        history: list[dict[str, str]] | None = None,
        thread_id: str | None = None,
        client_graph_revision: str | None = None,
        mode: str = "build",
    ) -> dict[str, Any]:
        """POST /api/chat/message. Returns {message_id, stream_channel_id} or run_started payload."""
        body: dict[str, Any] = {
            "workflow_id": workflow_id,
            "message": message,
            "history": history or [],
            "thread_id": thread_id,
            "client_graph_revision": client_graph_revision,
            "mode": mode,
            "surface": "cli",
        }
        http = await self._get_http()
        try:
            resp = await http.post("/api/chat/message", json=body)
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            raise RuntimeError(f"Cannot reach server: {exc}") from exc
        if resp.status_code != 200:
            raise RuntimeError(
                _friendly_http_error_message(
                    resp.status_code,
                    resp.text,
                    action="Chat request",
                )
            )
        return resp.json()

    async def stream_chat_events(
        self,
        channel_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Connect to WS /api/chat/{channel_id}/events and yield events."""
        import websockets

        ws_url = self._base_url.replace("http://", "ws://").replace(
            "https://", "wss://"
        )
        url = f"{ws_url}/api/chat/{channel_id}/events"

        async with websockets.connect(url) as ws:
            async for msg in ws:
                data = json.loads(msg)
                if data is None or not isinstance(data, dict):
                    yield data
                    return
                yield data
                if (
                    data.get("type") == "chat_complete"
                    and not _is_progress_ack_event(data)
                ):
                    return

    async def apply_mutation(
        self,
        graph_id: str,
        mutation_plan: dict[str, Any],
    ) -> dict[str, Any]:
        """POST /api/graphs/{id}/apply-mutation."""
        body = {"mutation_plan": mutation_plan}
        http = await self._get_http()
        resp = await http.post(f"/api/graphs/{graph_id}/apply-mutation", json=body)
        if resp.status_code != 200:
            raise RuntimeError(
                _friendly_http_error_message(
                    resp.status_code,
                    resp.text,
                    action="Apply mutation",
                )
            )
        return resp.json()

    async def get_graph(self, graph_id: str) -> dict[str, Any] | None:
        """GET /api/graphs/{id}. Returns the inner graph dict or None if 404."""
        http = await self._get_http()
        try:
            resp = await http.get(f"/api/graphs/{graph_id}")
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            raise RuntimeError(f"Cannot reach server: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise RuntimeError(
                _friendly_http_error_message(
                    resp.status_code,
                    resp.text,
                    action="Load workflow",
                )
            )
        payload = resp.json()
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    async def submit_human_input(
        self,
        run_id: str,
        request_id: str,
        response: dict[str, Any],
    ) -> bool:
        """POST /api/runs/{run_id}/human-input. Returns True if accepted."""
        body = {"request_id": request_id, "response": response}
        http = await self._get_http()
        resp = await http.post(f"/api/runs/{run_id}/human-input", json=body)
        if resp.status_code == 404:
            return False
        if resp.status_code != 200:
            raise RuntimeError(
                _friendly_http_error_message(
                    resp.status_code,
                    resp.text,
                    action="Submit human input",
                )
            )
        return True

    async def cancel_run(self, run_id: str) -> bool:
        """POST /api/gateway/cancel."""
        http = await self._get_http()
        resp = await http.post("/api/gateway/cancel", json={"run_id": run_id})
        return resp.status_code == 200

    async def list_graphs(self) -> list[dict[str, Any]]:
        """GET /api/graphs. Returns list of {graph_id, name, description, updated_at}."""
        http = await self._get_http()
        try:
            resp = await http.get("/api/graphs")
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            raise RuntimeError(f"Cannot reach server: {exc}") from exc
        if resp.status_code != 200:
            raise RuntimeError(
                _friendly_http_error_message(
                    resp.status_code,
                    resp.text,
                    action="List workflows",
                )
            )
        return resp.json().get("graphs", [])

    async def create_graph(self, graph_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST /api/graphs. Returns {graph_id, data}."""
        body: dict[str, Any] = {"graph_id": graph_id}
        if data is not None:
            body["data"] = data
        http = await self._get_http()
        try:
            resp = await http.post("/api/graphs", json=body)
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            raise RuntimeError(f"Cannot reach server: {exc}") from exc
        if resp.status_code == 409:
            raise RuntimeError(f"Workflow '{graph_id}' already exists")
        if resp.status_code != 200:
            raise RuntimeError(
                _friendly_http_error_message(
                    resp.status_code,
                    resp.text,
                    action="Create workflow",
                )
            )
        return resp.json()

    async def save_graph(self, graph_id: str, data: dict[str, Any]) -> None:
        """PUT /api/graphs/{id}. Saves graph data."""
        http = await self._get_http()
        try:
            resp = await http.put(f"/api/graphs/{graph_id}", json=data)
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            raise RuntimeError(f"Cannot reach server: {exc}") from exc
        if resp.status_code != 200:
            raise RuntimeError(
                _friendly_http_error_message(
                    resp.status_code,
                    resp.text,
                    action="Save workflow",
                )
            )


async def _close_client_quietly(client: Any) -> None:
    """Best-effort client shutdown for Ctrl-C driven CLI exits."""
    with contextlib.suppress(asyncio.CancelledError):
        await client.close()


_MAX_HISTORY_MESSAGES = 40

# ── Readline chat history (up/down arrow) ──────────────────────────

_HISTORY_FILE = Path.home() / ".dan" / "chat_history"
_MAX_READLINE_HISTORY = 500


def _load_readline_history() -> None:
    """Load previous chat inputs so up/down arrows recall them."""
    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _HISTORY_FILE.exists():
            readline.read_history_file(str(_HISTORY_FILE))
    except (OSError, PermissionError):
        pass
    readline.set_history_length(_MAX_READLINE_HISTORY)


def _setup_command_completer() -> None:
    """Set up readline tab-completion for slash commands from the registry."""
    try:
        from dan.server.concierge.command_registry import get_default_registry
        candidates = get_default_registry().completion_candidates("cli")
    except Exception:
        candidates = []

    def _completer(text: str, state: int) -> str | None:
        if not text.startswith("/"):
            return None
        matches = [c for c in candidates if c.startswith(text)]
        return matches[state] if state < len(matches) else None

    readline.set_completer(_completer)
    readline.set_completer_delims(" \t\n")
    readline.parse_and_bind("tab: complete")


def _save_readline_history() -> None:
    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _HISTORY_FILE.with_suffix(".tmp")
        readline.write_history_file(str(tmp))
        tmp.replace(_HISTORY_FILE)
    except (OSError, PermissionError):
        pass


# ── Message queue (type while LLM is streaming) ───────────────────

class _InputQueue:
    """Non-blocking input reader: collects lines typed while the LLM streams."""

    def __init__(self) -> None:
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def drain(self) -> list[str]:
        """Return all queued messages (non-blocking)."""
        items: list[str] = []
        while True:
            try:
                item = self._queue.get_nowait()
                if item is not None:
                    items.append(item)
            except queue.Empty:
                break
        return items
_MAX_UNDO_DEPTH = 10

_undo_stack: list[tuple[str, dict]] = []  # (graph_revision, graph_dict)

_GRAPH_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$")


def _is_valid_graph_id(graph_id: str) -> bool:
    return bool(graph_id) and ".." not in graph_id and "/" not in graph_id and _GRAPH_ID_RE.match(graph_id) is not None


_INVALID_ID_MSG = "Invalid workflow ID: must be alphanumeric with ._- only (max 64 chars)"


def _format_mutation_summary(plan: dict[str, Any]) -> str:
    """Produce a brief diff summary from mutation plan."""
    ops = plan.get("operations", [])
    desc = plan.get("description", "Graph changes")
    lines = [f"  {desc}", f"  Operations: {len(ops)}"]
    for i, op in enumerate(ops[:5]):
        op_type = op.get("op", "?")
        name = op.get("name", op.get("node_id", ""))
        lines.append(f"    {i + 1}. {op_type}" + (f" ({name})" if name else ""))
    if len(ops) > 5:
        lines.append(f"    ... and {len(ops) - 5} more")
    return "\n".join(lines)


def _format_graph_summary(data: dict[str, Any]) -> str:
    """One-screen summary of a graph dict for /show."""
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    meta = data.get("metadata", {})
    name = meta.get("name", "(unnamed)")
    lines = [f"  Workflow: {name}", f"  Nodes: {len(nodes)}  Edges: {len(edges)}"]
    if not nodes:
        lines.append("  (empty graph)")
        return "\n".join(lines)
    lines.append("  ---")
    for n in nodes[:15]:
        nid = n.get("id", "?")
        ntype = n.get("node_type") or n.get("type", "?")
        label = n.get("label") or n.get("name") or nid
        lines.append(f"  [{ntype}] {label} ({nid})")
    if len(nodes) > 15:
        lines.append(f"  ... and {len(nodes) - 15} more nodes")
    if edges:
        lines.append("  ---")
        for e in edges[:10]:
            src = e.get("source_id") or e.get("source", "?")
            tgt = e.get("target_id") or e.get("target", "?")
            lines.append(f"  {src} -> {tgt}")
        if len(edges) > 10:
            lines.append(f"  ... and {len(edges) - 10} more edges")
    return "\n".join(lines)


def _prompt_apply_mutation() -> bool:
    """Prompt 'Apply mutation? [Y/n]' and return True for Y/yes/Enter."""
    try:
        line = input("Apply mutation? [Y/n] ").strip().lower()
        return line in ("", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _should_prompt_apply(dry_run_result: dict[str, Any] | None) -> bool:
    """Only allow apply prompt when dry-run passed."""
    if not isinstance(dry_run_result, dict):
        return True
    if "success" in dry_run_result:
        return bool(dry_run_result.get("success"))
    errs = dry_run_result.get("errors")
    return not (isinstance(errs, list) and len(errs) > 0)


_STOP_WORDS = frozenset(
    "a an the to for of and or but in on with that which is are was were "
    "be been being have has had do does did will would shall should can could "
    "may might must i me my we our you your it its this from by create build "
    "make write generate please help want need workflow agent pipeline using "
    "use add new some get set up".split()
)


def _auto_generate_name(history: list[dict[str, str]], max_words: int = 4) -> str:
    """Derive a short slug from the first substantive user message."""
    text = ""
    for msg in history:
        if msg.get("role") == "user" and not msg["content"].startswith("/"):
            text = msg["content"]
            break
    if not text:
        return ""
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    keywords = [w for w in words if w not in _STOP_WORDS and len(w) > 1]
    slug = "-".join(keywords[:max_words])
    return slug[:48] if slug else ""


def _format_workflow_list(graphs: list[dict[str, Any]], current_id: str) -> str:
    """Format graph list for /list display."""
    if not graphs:
        return "  No saved workflows."
    lines = ["  ID                          Name                 Updated"]
    lines.append("  " + "-" * 65)
    for g in graphs:
        gid = g.get("graph_id", "?")
        name = g.get("name", "")
        updated = (g.get("updated_at") or "")[:19]
        marker = " *" if gid == current_id else ""
        lines.append(f"  {gid:<28} {name:<20} {updated}{marker}")
    return "\n".join(lines)


def _select_resume_workflow(
    choice: str,
    candidate_ids: list[str],
    *,
    default_workflow_id: str,
) -> str:
    """Resolve startup resume choice to a workflow ID."""
    normalized = choice.strip().lower()
    if normalized in ("", "new", "n"):
        return default_workflow_id
    if normalized.isdigit():
        idx = int(normalized) - 1
        if 0 <= idx < len(candidate_ids):
            return candidate_ids[idx]
    return default_workflow_id


def _record_recent_workflow(
    profile: Any | None,
    workflow_id: str,
    *,
    profile_path: Path | None = None,
) -> None:
    """Persist workflow recency in UserProfile when available."""
    if profile is None or not workflow_id or workflow_id == "_scratch":
        return
    try:
        from dan.engine.user_profile import save_user_profile

        profile.touch_workflow(workflow_id)
        save_user_profile(profile, profile_path)
    except Exception:
        logger.debug("Failed to update recent workflows for %s", workflow_id, exc_info=True)


def _format_relative_age(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(1, int(seconds / 60))}m ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h ago"
    return f"{int(seconds / 86400)}d ago"


async def _maybe_prompt_resume_workflow(
    client: Any,
    workflow_id: str,
    profile: Any | None,
) -> str:
    """Offer quick-resume from recent workflows when starting from scratch."""
    if workflow_id != "_scratch" or profile is None:
        return workflow_id
    recent_workflows = list(getattr(profile, "recent_workflows", []) or [])
    if not recent_workflows:
        return workflow_id

    try:
        graphs = await client.list_graphs()
        available_ids = {
            str(g.get("graph_id", ""))
            for g in graphs
            if isinstance(g, dict) and g.get("graph_id")
        }
    except Exception:
        available_ids = set()

    if not available_ids:
        return workflow_id

    candidates: list[Any] = []
    for item in recent_workflows:
        wid = str(getattr(item, "workflow_id", "") or "")
        if not wid or wid == "_scratch" or wid not in available_ids:
            continue
        candidates.append(item)
        if len(candidates) >= 5:
            break
    if not candidates:
        return workflow_id

    print("Recent workflows:")
    now = _time.time()
    for idx, item in enumerate(candidates, start=1):
        wid = str(getattr(item, "workflow_id", ""))
        opened_at = float(getattr(item, "opened_at", now) or now)
        age = max(0.0, now - opened_at)
        print(f"  ({idx}) {wid} [{_format_relative_age(age)}]")

    try:
        raw = input("Resume? [1-5/new] ").strip()
    except (EOFError, KeyboardInterrupt):
        print("")
        return workflow_id

    candidate_ids = [str(getattr(item, "workflow_id", "")) for item in candidates]
    return _select_resume_workflow(
        raw,
        candidate_ids,
        default_workflow_id=workflow_id,
    )


async def _run_repl(
    client: ChatClient,
    workflow_id: str,
    mode: str,
    *,
    confirm_mode: bool = False,
    profile: Any | None = None,
    profile_path: Path | None = None,
) -> None:
    """REPL loop: read input, POST message, stream WS events, handle mutations."""
    _undo_stack.clear()
    _load_readline_history()
    _setup_command_completer()
    msg_queue = _InputQueue()
    history: list[dict[str, str]] = []
    client_graph_revision: str | None = None
    preference_extractor: Any | None = None
    preference_suggestion_shown = False
    session_estimated_cost = 0.0
    startup_summary: dict[str, Any] | None = None

    Console = None
    try:
        from rich.console import Console
    except ImportError:
        pass

    console = Console() if Console else None

    try:
        from dan.notifications.terminal import maybe_ring_on_event
    except Exception:
        def maybe_ring_on_event(event_type: str) -> None:  # type: ignore[no-redef]
            _ = event_type

    def _print(text: str, style: str | None = None) -> None:
        if console and style:
            console.print(text, style=style)
        else:
            print(text)

    def _mark_workflow_recent(wid: str) -> None:
        _record_recent_workflow(profile, wid, profile_path=profile_path)

    if profile is not None:
        try:
            from dan.engine.behavior_store import BehaviorStore
            from dan.engine.preference_extractor import PreferenceExtractor
            from dan.server.concierge.domain_learning import register_seed_domains

            behavior_store = BehaviorStore()
            register_seed_domains(behavior_store)
            preference_extractor = PreferenceExtractor(behavior_store=behavior_store)
        except Exception:
            logger.debug("PreferenceExtractor unavailable in chat CLI", exc_info=True)

    def _count_model_task_mentions(task: str, model: str) -> int:
        task_keywords = {
            "drafting": ("draft", "write", "compose", "author"),
            "review": ("review", "critique", "feedback", "evaluate"),
            "coding": ("code", "implement", "program", "script", "debug"),
            "analysis": ("analyze", "research", "investigate", "study"),
            "summarization": ("summarize", "summary", "condense", "tldr"),
        }
        keywords = task_keywords.get(task, (task,))
        model_lower = model.lower()
        count = 0
        for msg in history:
            if msg.get("role") != "user":
                continue
            text = str(msg.get("content", "")).lower()
            if model_lower in text and any(kw in text for kw in keywords):
                count += 1
        return count

    def _maybe_update_profile_preferences() -> None:
        nonlocal preference_suggestion_shown
        if profile is None or preference_extractor is None:
            return
        changed = False
        try:
            extracted = preference_extractor.extract_from_messages(history)
        except Exception:
            logger.debug("Preference extraction failed", exc_info=True)
            return

        domains = extracted.get("domains")
        output_format = extracted.get("output_format")
        before_domains = list(getattr(profile, "common_domains", []))
        before_output = getattr(profile, "preferred_output_format", "")
        if domains or output_format:
            profile.merge_preferences(
                models=None,
                domains=domains if isinstance(domains, list) else None,
                output_format=str(output_format) if output_format else None,
            )
            changed = (
                before_domains != list(getattr(profile, "common_domains", []))
                or before_output != getattr(profile, "preferred_output_format", "")
            )

        model_candidates = extracted.get("models")
        if (
            not preference_suggestion_shown
            and isinstance(model_candidates, dict)
            and model_candidates
        ):
            for task, model in model_candidates.items():
                if (
                    not isinstance(task, str)
                    or not isinstance(model, str)
                    or not task
                    or not model
                    or task in getattr(profile, "preferred_models", {})
                ):
                    continue
                if _count_model_task_mentions(task, model) < 3:
                    continue
                try:
                    answer = input(
                        f"You usually use {model} for {task} — set this as default? [Y/n] ",
                    ).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    answer = "n"
                    _print("")
                preference_suggestion_shown = True
                if answer in ("", "y", "yes"):
                    profile.preferred_models[task] = model
                    changed = True
                    _print(f"Saved preference: {task} -> {model}")
                break

        if changed:
            try:
                from dan.engine.user_profile import save_user_profile

                save_user_profile(profile, profile_path)
            except Exception:
                logger.debug("Failed to persist profile preferences", exc_info=True)

    def _status() -> None:
        _print(
            "dan-chat — "
            f"workflow: {workflow_id} (mode: {mode}) | "
            f"session cost: {_format_estimated_cost(session_estimated_cost)}"
        )

    def _banner() -> None:
        model_name = os.environ.get("DAN_CHAT_MODEL") or os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6")
        tier_policy = "on" if os.environ.get("DAN_ENABLE_TIER_POLICY") == "1" else "off"
        learning = "on" if os.environ.get("DAN_LEARNING_MODE") == "1" else "off"
        api_key = _format_api_key_status_label()
        mcp_names = []
        try:
            mcp_conf = Path.home() / ".dan" / "mcp.json"
            if mcp_conf.exists():
                data = json.loads(mcp_conf.read_text())
                mcp_names = [k for k, v in data.get("mcpServers", {}).items() if v.get("autoConnect", True) is not False]
        except Exception:
            pass
        mcp_str = ",".join(mcp_names) if mcp_names else "none"
        _print(
            f"Model: {model_name} | Tier: {tier_policy} | Learning: {learning} | "
            f"API key: {api_key} | MCP: {mcp_str}"
        )
        if startup_summary:
            issues = startup_summary.get("issues", [])
            if isinstance(issues, list) and issues:
                labels = [
                    str(issue.get("subsystem", "unknown"))
                    for issue in issues[:3]
                    if isinstance(issue, dict)
                ]
                extra = f" +{len(issues) - len(labels)} more" if len(issues) > len(labels) else ""
                _print(
                    f"Startup: degraded ({', '.join(labels)}{extra})",
                    style="yellow" if console else None,
                )
            else:
                _print("Startup: ok")

    def _help() -> None:
        from dan.server.concierge.command_registry import get_default_registry
        _registry = get_default_registry()
        _help_text = _registry.format_help("cli")
        _print(_help_text)

    # Fetch graph on startup for non-scratch workflows
    try:
        startup_summary = await client.get_health()
        startup_summary = (
            startup_summary.get("startup")
            if isinstance(startup_summary, dict)
            else None
        )
    except Exception:
        startup_summary = None

    if workflow_id != "_scratch":
        try:
            graph_data = await client.get_graph(workflow_id)
            if graph_data is None:
                _print(f"Workflow '{workflow_id}' not found on server.", style="red" if console else None)
                return
            client_graph_revision = _compute_graph_revision(graph_data)
            _print(f"Loaded workflow: {workflow_id}")
            _mark_workflow_recent(workflow_id)
        except RuntimeError as e:
            _print(f"Error loading workflow: {e}", style="red" if console else None)
            return

    _banner()
    _status()
    _print("Type /help for commands, /exit to quit.")
    _print("(You can type while the assistant is responding — messages will be queued.)")
    _print("")

    pending_lines: list[str] = []

    _background_listeners: list[asyncio.Task[None]] = []

    async def _consume_stream_to_terminal(
        channel: str,
        *,
        stream_tokens: bool = True,
    ) -> None:
        """Consume a stream channel and print to terminal.

        When *stream_tokens* is False, buffer the full response and print
        as a single labeled block (used for background/queued responses).
        """
        nonlocal client_graph_revision
        nonlocal session_estimated_cost
        acc = ""
        progress_display = _CliProgressDisplay()
        try:
            async for event in client.stream_chat_events(channel):
                if event is None:
                    break
                ev_type = event.get("type", "")
                if ev_type == "chat_token":
                    delta = event.get("delta", "")
                    if delta:
                        if stream_tokens:
                            print(delta, end="", flush=True)
                        acc += delta
                elif ev_type == "chat_complete":
                    if _is_progress_ack_event(event):
                        for line in await progress_display.render(event):
                            _print(line, style="dim" if console else None)
                        continue
                    content = acc or str(event.get("content", "") or "")
                    estimated_cost = event.get("estimated_cost")
                    if isinstance(estimated_cost, (int, float)):
                        session_estimated_cost += float(estimated_cost)
                    if stream_tokens:
                        if acc:
                            print()
                        elif content:
                            _print(content)
                    elif content:
                        _print(f"\n{content}")
                    if content:
                        history.append({"role": "assistant", "content": content})
                        if len(history) > _MAX_HISTORY_MESSAGES:
                            history[:] = history[-_MAX_HISTORY_MESSAGES:]
                    rev = event.get("graph_revision")
                    if rev:
                        client_graph_revision = rev
                    break
                elif ev_type == "chat_error":
                    _print(f"\nError: {event.get('error', 'Unknown')}", style="red" if console else None)
                    break
        except Exception as e:
            _print(f"\nStream error: {e}", style="red" if console else None)

    async def _replay_queued_through_api(queued_text: str) -> None:
        """Send a queued message through the server chat API.

        If the server returns ``status: "queued"``, spawn a background
        listener that will print the response as a complete block when
        it arrives.  Otherwise stream tokens inline as before.
        """
        _print(f"[queued] > {queued_text}", style="dim" if console else None)
        readline.add_history(queued_text)
        try:
            q_resp = await client.send_chat_message(
                workflow_id,
                queued_text,
                history=history,
                thread_id=workflow_id,
                client_graph_revision=client_graph_revision,
                mode=mode,
            )
        except RuntimeError as e:
            _print(f"Error: {e}", style="red" if console else None)
            return
        history.append({"role": "user", "content": queued_text})
        if len(history) > _MAX_HISTORY_MESSAGES:
            history[:] = history[-_MAX_HISTORY_MESSAGES:]
        q_channel = q_resp.get("stream_channel_id")
        if not q_channel:
            return

        if q_resp.get("status") == "queued":
            _print(f"  [queued — response will arrive when ready]", style="dim" if console else None)
            task = asyncio.create_task(
                _consume_stream_to_terminal(q_channel, stream_tokens=False),
            )
            _background_listeners.append(task)
            return

        await _consume_stream_to_terminal(q_channel, stream_tokens=True)

    while True:
        if pending_lines:
            line = pending_lines.pop(0)
            _print(f"[queued] > {line}", style="dim" if console else None)
        else:
            try:
                line = input(f"[{_format_estimated_cost(session_estimated_cost)}] > ").strip()
            except (EOFError, KeyboardInterrupt):
                _print("")
                break

        if not line:
            continue

        readline.add_history(line)

        if line == "/exit":
            if workflow_id == "_scratch":
                try:
                    g = await client.get_graph("_scratch")
                    has_nodes = g is not None and len(g.get("nodes", [])) > 0
                except RuntimeError:
                    has_nodes = False
                if has_nodes:
                    suggested = _auto_generate_name(history)
                    if suggested:
                        prompt_msg = f"Save as '{suggested}'? [Y/n/custom name] "
                    else:
                        prompt_msg = "Save workflow before exiting? [name / Enter to skip] "
                    try:
                        raw = input(prompt_msg).strip()
                    except (EOFError, KeyboardInterrupt):
                        raw = "n"
                    if suggested:
                        if raw.lower() in ("", "y", "yes"):
                            save_name = suggested
                        elif raw.lower() in ("n", "no"):
                            save_name = ""
                        else:
                            save_name = raw
                    else:
                        save_name = raw
                    if save_name:
                        try:
                            g.setdefault("metadata", {})["name"] = save_name
                            await client.create_graph(save_name, g)
                            _mark_workflow_recent(save_name)
                            _print(f"Saved as '{save_name}'.")
                        except RuntimeError as e:
                            _print(f"Save failed: {e}", style="red" if console else None)
            break

        if line == "/help":
            _help()
            continue

        # --- /undo ---
        if line == "/undo":
            if not _undo_stack:
                _print("Nothing to undo.")
            else:
                prev_rev, prev_graph = _undo_stack.pop()
                try:
                    await client.save_graph(workflow_id, prev_graph)
                    client_graph_revision = prev_rev
                    _print("Undone. Graph restored to previous state.")
                except RuntimeError as e:
                    _print(f"Undo failed: {e}", style="red" if console else None)
                    _undo_stack.append((prev_rev, prev_graph))  # restore on failure
            continue

        # --- /save [name] ---
        if line == "/save" or line.startswith("/save "):
            parts = line.split(maxsplit=1)
            save_id = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None

            if workflow_id != "_scratch" and save_id is None:
                _print(f"Workflow '{workflow_id}' is already saved (auto-persisted on each mutation apply).")
                continue

            if save_id is None:
                suggested = _auto_generate_name(history)
                prompt_suffix = f" ({suggested}) " if suggested else ": "
                try:
                    raw = input(f"Workflow name{prompt_suffix}").strip()
                except (EOFError, KeyboardInterrupt):
                    _print("")
                    continue
                save_id = raw if raw else suggested
                if not save_id:
                    _print("No name provided — not saved.")
                    continue

            if not _is_valid_graph_id(save_id):
                _print(_INVALID_ID_MSG, style="red" if console else None)
                continue

            try:
                current_graph = await client.get_graph(workflow_id)
                if current_graph is None:
                    _print("Nothing to save (graph is empty).", style="red" if console else None)
                    continue
                current_graph.setdefault("metadata", {})["name"] = save_id
                await client.create_graph(save_id, current_graph)
                workflow_id = save_id
                client_graph_revision = _compute_graph_revision(current_graph)
                history = []
                mode = "mutate"
                _undo_stack.clear()
                _mark_workflow_recent(workflow_id)
                _print(f"Saved as '{save_id}'.")
                _status()
            except RuntimeError as e:
                _print(f"Error: {e}", style="red" if console else None)
            continue

        # --- /list ---
        if line == "/list":
            try:
                graphs = await client.list_graphs()
                from dan.cli.dag_display import format_workflow_table
                _print(format_workflow_table(graphs, current_id=workflow_id))
            except RuntimeError as e:
                _print(f"Error: {e}", style="red" if console else None)
            continue

        # --- /open <id> ---
        if line == "/open" or line.startswith("/open "):
            parts = line.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                _print("Usage: /open <workflow-id>")
                continue
            target_id = parts[1].strip()
            if not _is_valid_graph_id(target_id):
                _print(_INVALID_ID_MSG, style="red" if console else None)
                continue
            try:
                graph_data = await client.get_graph(target_id)
                if graph_data is None:
                    _print(f"Workflow '{target_id}' not found.", style="red" if console else None)
                    continue
                workflow_id = target_id
                client_graph_revision = _compute_graph_revision(graph_data)
                history = []
                mode = "build" if workflow_id == "_scratch" else "mutate"
                _undo_stack.clear()
                _mark_workflow_recent(workflow_id)
                _status()
            except RuntimeError as e:
                _print(f"Error: {e}", style="red" if console else None)
            continue

        # --- /saveas <id> ---
        if line == "/saveas" or line.startswith("/saveas "):
            parts = line.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                _print("Usage: /saveas <new-workflow-id>")
                continue
            new_id = parts[1].strip()
            if not _is_valid_graph_id(new_id):
                _print(_INVALID_ID_MSG, style="red" if console else None)
                continue
            try:
                current_graph = await client.get_graph(workflow_id)
                if current_graph is None:
                    _print("Current workflow has no graph to copy.", style="red" if console else None)
                    continue
                current_graph.setdefault("metadata", {})["name"] = new_id
                await client.create_graph(new_id, current_graph)
                workflow_id = new_id
                client_graph_revision = _compute_graph_revision(current_graph)
                history = []
                mode = "mutate"
                _undo_stack.clear()
                _mark_workflow_recent(workflow_id)
                _print(f"Saved and switched to '{new_id}'.")
                _status()
            except RuntimeError as e:
                _print(f"Error: {e}", style="red" if console else None)
            continue

        # --- /new [id] ---
        if line == "/new" or line.startswith("/new "):
            parts = line.split(maxsplit=1)
            new_id = parts[1].strip() if len(parts) > 1 and parts[1].strip() else f"workflow-{int(_time.time())}"
            if not _is_valid_graph_id(new_id):
                _print(_INVALID_ID_MSG, style="red" if console else None)
                continue
            try:
                result = await client.create_graph(new_id)
                workflow_id = new_id
                new_data = result.get("data", {"nodes": [], "edges": []})
                client_graph_revision = _compute_graph_revision(new_data)
                history = []
                mode = "build"
                _undo_stack.clear()
                _mark_workflow_recent(workflow_id)
                _print(f"Created '{new_id}'.")
                _status()
            except RuntimeError as e:
                _print(f"Error: {e}", style="red" if console else None)
            continue

        # --- /rename <name> ---
        if line == "/rename" or line.startswith("/rename "):
            parts = line.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                _print("Usage: /rename <display-name>")
                continue
            new_name = parts[1].strip()
            try:
                current_graph = await client.get_graph(workflow_id)
                if current_graph is None:
                    _print("No graph to rename.", style="red" if console else None)
                    continue
                current_graph.setdefault("metadata", {})["name"] = new_name
                await client.save_graph(workflow_id, current_graph)
                _print(f"Renamed to '{new_name}'.")
            except RuntimeError as e:
                _print(f"Error: {e}", style="red" if console else None)
            continue

        # --- /show [--code|--json|--stats] ---
        if line == "/show" or line.startswith("/show "):
            flags = line[5:].strip()
            try:
                graph_data = await client.get_graph(workflow_id)
                if graph_data is None:
                    _print("No graph found (not yet created).")
                elif flags == "--code":
                    from dan.builder import decompile
                    from dan.models.graph import Graph
                    g = Graph.model_validate(graph_data)
                    _print(decompile(g))
                elif flags == "--json":
                    _print(json.dumps(graph_data, indent=2))
                elif flags == "--stats":
                    from dan.cli.dag_display import render_stats
                    _print(render_stats(graph_data))
                else:
                    from dan.cli.dag_display import render_dag
                    _print(f"  Workflow ID: {workflow_id}")
                    _print(render_dag(graph_data))
            except Exception as e:
                _print(f"Error: {e}", style="red" if console else None)
            continue

        # --- Unknown slash-command suggestion ---
        if _is_slash_command_input(line):
            from dan.server.concierge.command_registry import get_default_registry
            _reg = get_default_registry()
            if _reg.match(line) is None:
                cmd_word = line.split()[0]
                suggestions = _reg.suggest(line)
                if suggestions:
                    _print(
                        f"Unknown command '{cmd_word}'. "
                        f"Did you mean: {', '.join(suggestions)}?",
                    )
                else:
                    _print(
                        f"Unknown command '{cmd_word}'. Type /help for available commands.",
                    )
                continue

        # Send message
        try:
            resp = await client.send_chat_message(
                workflow_id,
                line,
                history=history,
                thread_id=workflow_id,
                client_graph_revision=client_graph_revision,
                mode=mode,
            )
        except RuntimeError as e:
            _print(f"Error: {e}", style="red" if console else None)
            continue

        # Handle run_started (different response shape)
        stream_channel_id: str | None = None
        active_run_id: str | None = None
        if resp.get("type") == "run_started":
            _print(f"Started {resp.get('scope', 'full')} run.")
            stream_channel_id = resp.get("stream_channel_id")
            active_run_id = resp.get("run_id")
        elif resp.get("type") == "run_error":
            err = resp.get("error", {})
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            _print(f"Run failed: {msg}", style="red" if console else None)
            continue
        else:
            stream_channel_id = resp.get("stream_channel_id")

        if not stream_channel_id:
            _print("No stream channel returned.", style="red" if console else None)
            continue

        # Append user message to history for next turn (bounded)
        history.append({"role": "user", "content": line})
        if len(history) > _MAX_HISTORY_MESSAGES:
            history = history[-_MAX_HISTORY_MESSAGES:]

        # Stream events (accept queued input in background)
        accumulated = ""
        mutation_plan: dict[str, Any] | None = None
        mutation_dry_run: dict[str, Any] | None = None
        pending_mutation_graph_id: str | None = None

        bg_stop = threading.Event()
        bg_pause = threading.Event()
        stdin_lock = threading.Lock()

        def _bg_reader() -> None:
            """Read lines in background while LLM streams; feed into msg_queue."""
            while not bg_stop.is_set():
                try:
                    if bg_pause.is_set():
                        if bg_stop.wait(0.05):
                            return
                        continue
                    if bg_stop.wait(0.05):
                        return
                    if sys.stdin.closed:
                        return
                    import select
                    with stdin_lock:
                        ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                        if ready:
                            raw = sys.stdin.readline()
                            if raw:
                                stripped = raw.strip()
                                if stripped:
                                    msg_queue._queue.put(stripped)
                except Exception:
                    return

        bg_thread = threading.Thread(target=_bg_reader, daemon=True)
        bg_thread.start()

        pending_stream_ids: list[str] = [stream_channel_id]
        seen_stream_ids: set[str] = set()
        progress_display = _CliProgressDisplay()
        try:
            while pending_stream_ids:
                current_stream_id = pending_stream_ids.pop(0)
                if current_stream_id in seen_stream_ids:
                    continue
                seen_stream_ids.add(current_stream_id)

                async for event in client.stream_chat_events(current_stream_id):
                    if event is None:
                        break

                    ev_type = event.get("type", "")

                    if ev_type == "chat_token":
                        delta = event.get("delta", "")
                        if delta:
                            print(delta, end="", flush=True)
                            accumulated += delta

                    elif ev_type == "chat_complete":
                        if _is_progress_ack_event(event):
                            for line in await progress_display.render(event):
                                _print(line, style="dim" if console else None)
                            continue
                        complete_content = accumulated or str(event.get("content", "") or "")
                        estimated_cost = event.get("estimated_cost")
                        if isinstance(estimated_cost, (int, float)):
                            session_estimated_cost += float(estimated_cost)
                        if accumulated:
                            print()  # newline after streamed tokens
                        elif complete_content:
                            _print(complete_content)
                        if complete_content:
                            history.append({"role": "assistant", "content": complete_content})
                            if len(history) > _MAX_HISTORY_MESSAGES:
                                history = history[-_MAX_HISTORY_MESSAGES:]
                        rev = event.get("graph_revision")
                        if rev:
                            client_graph_revision = rev
                        handoff_stream_id = event.get("stream_channel_id")
                        if (
                            isinstance(handoff_stream_id, str)
                            and handoff_stream_id
                            and handoff_stream_id not in seen_stream_ids
                        ):
                            pending_stream_ids.append(handoff_stream_id)
                        break

                    elif ev_type == "chat_error":
                        err = event.get("error", "Unknown error")
                        _print(f"\nError: {err}", style="red" if console else None)
                        break

                    elif ev_type == "chat_mutation":
                        if accumulated:
                            print()
                        mutation_plan = event.get("mutation_plan")
                        dry_run = event.get("dry_run_result", {})
                        mutation_dry_run = dry_run if isinstance(dry_run, dict) else None
                        if mutation_plan:
                            _print("\n--- Mutation proposed ---")
                            from dan.cli.mutation_diff import format_mutation_diff
                            _print(format_mutation_diff(mutation_plan))
                            if dry_run:
                                errs = dry_run.get("errors", [])
                                if errs:
                                    _print("Dry-run issues:", style="yellow" if console else None)
                                    for e in errs[:3]:
                                        _print(f"  - {e.get('message', e)}")
                            pending_mutation_graph_id = workflow_id

                    elif ev_type == "chat_run_event":
                        re = event.get("run_event", {})
                        summary = re.get("summary", "")
                        run_event_type = re.get("event_type", "")
                        maybe_ring_on_event(run_event_type)

                        detail = re.get("detail", {}) if isinstance(re.get("detail"), dict) else {}
                        detail_run_id = detail.get("run_id")
                        if not active_run_id and isinstance(detail_run_id, str) and detail_run_id:
                            active_run_id = detail_run_id

                        if run_event_type == "human_input_needed" and active_run_id:
                            evt_data = detail.get("data", {}) if isinstance(detail.get("data"), dict) else {}
                            request_id = evt_data.get("request_id")
                            render_mode = evt_data.get("render_mode", "text")
                            prompt_text = evt_data.get("prompt", "Input required:")
                            _print(f"\n{prompt_text}")
                            bg_pause.set()
                            try:
                                loop = asyncio.get_running_loop()

                                def _input_approval() -> str:
                                    with stdin_lock:
                                        return input("[Y/n] ")

                                def _input_text() -> str:
                                    with stdin_lock:
                                        return input("> ")

                                if render_mode == "approval":
                                    raw = await asyncio.wait_for(
                                        loop.run_in_executor(None, _input_approval),
                                        timeout=300,
                                    )
                                    answer = raw.strip().lower() if raw else "y"
                                    approved = answer in ("y", "yes", "true", "1", "approve", "")
                                    hi_response = {
                                        "approved": approved,
                                        "response": "approve" if approved else "reject",
                                    }
                                else:
                                    user_input = await asyncio.wait_for(
                                        loop.run_in_executor(None, _input_text),
                                        timeout=300,
                                    )
                                    hi_response = {"response": user_input}
                                if request_id:
                                    ok = await client.submit_human_input(
                                        active_run_id, request_id, hi_response,
                                    )
                                    if not ok:
                                        _print("Input request expired.", style="yellow" if console else None)
                                else:
                                    _print("Missing request_id — cannot submit input.", style="red" if console else None)
                            except asyncio.TimeoutError:
                                _print("\nInput timed out.", style="yellow" if console else None)
                                await client.cancel_run(active_run_id)
                            except (EOFError, KeyboardInterrupt):
                                _print("")
                                await client.cancel_run(active_run_id)
                            finally:
                                bg_pause.clear()
                        elif summary:
                            _print(summary)

                        if run_event_type in ("run_completed", "run_failed", "run_cancelled"):
                            break

        except Exception as e:
            _print(f"\nStream error: {e}", style="red" if console else None)
        finally:
            bg_stop.set()
            bg_thread.join(timeout=1.0)

        # Mutation confirmation / auto-apply
        if mutation_plan and pending_mutation_graph_id:
            if not _should_prompt_apply(mutation_dry_run):
                _print(
                    "Skipping apply: dry-run failed. Send a follow-up message to repair the plan.",
                    style="yellow" if console else None,
                )
            else:
                # Dry-run passed — snapshot for undo, then apply (prompt in confirm mode)

                async def _do_apply() -> bool:
                    nonlocal client_graph_revision
                    pre_graph = await client.get_graph(pending_mutation_graph_id)
                    if pre_graph is None:
                        return False
                    rev = _compute_graph_revision(pre_graph)
                    snapshot = json.loads(json.dumps(pre_graph))  # deep copy
                    _undo_stack.append((rev, snapshot))
                    if len(_undo_stack) > _MAX_UNDO_DEPTH:
                        _undo_stack.pop(0)
                    try:
                        result = await client.apply_mutation(
                            pending_mutation_graph_id,
                            mutation_plan,
                        )
                        if result.get("success"):
                            if result.get("graph_revision"):
                                client_graph_revision = result["graph_revision"]
                            else:
                                new_graph = result.get("new_graph")
                                if new_graph and isinstance(new_graph, dict):
                                    client_graph_revision = _compute_graph_revision(new_graph)
                            return True
                        errs = result.get("errors", [])
                        _print(f"Apply failed: {errs}", style="red" if console else None)
                        _undo_stack.pop()  # remove snapshot since we didn't apply
                        return False
                    except RuntimeError as e:
                        _print(f"Apply failed: {e}", style="red" if console else None)
                        _undo_stack.pop()
                        return False

                if confirm_mode:
                    if _prompt_apply_mutation():
                        applied = await _do_apply()
                        if applied:
                            _print("Mutation applied.")
                    else:
                        _print("Mutation rejected.")
                else:
                    applied = await _do_apply()
                    if applied:
                        _print("Applied.")

        # Drain messages typed during streaming and replay through server API
        queued_replay = msg_queue.drain()
        for q_line in queued_replay:
            if _is_slash_command_input(q_line):
                pending_lines.append(q_line)
            else:
                await _replay_queued_through_api(q_line)

        _maybe_update_profile_preferences()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dan-chat",
        description="REPL for conversational workflow authoring via dan-serve chat API.",
    )
    p.add_argument(
        "--workflow-id",
        dest="workflow_id",
        default="_scratch",
        help="Workflow ID (default: _scratch for build-from-scratch)",
    )
    p.add_argument(
        "--scratch",
        action="store_true",
        help="Use scratch workflow (_scratch); same as default",
    )
    p.add_argument(
        "--server",
        default=None,
        help="Server URL (default: DAN_SERVER_URL or http://127.0.0.1:8000)",
    )
    p.add_argument(
        "--mode",
        default=None,
        choices=["build", "mutate", "agent", "ask", "plan", "debug", "auto"],
        help="Chat mode (default: build for scratch, mutate for existing workflows)",
    )
    p.add_argument(
        "--local",
        action="store_true",
        help="Force local mode (no server required)",
    )
    p.add_argument(
        "--confirm",
        action="store_true",
        default=False,
        help="Require explicit approval for mutations",
    )
    p.add_argument(
        "--ask",
        help="Send a single question, print the response, and exit",
    )
    p.add_argument(
        "--pipe",
        action="store_true",
        help="Read question from stdin, print response to stdout, and exit",
    )
    p.add_argument(
        "--output",
        help="Write the response text to a file in addition to stdout",
    )
    p.add_argument(
        "--model",
        help="Override the model for this session",
    )
    return p


async def _run_one_shot(
    client: ChatClient,
    workflow_id: str,
    mode: str,
    question: str,
    model: str | None,
    output_path: str | None,
) -> None:
    """Run a single question and exit."""
    history: list[dict[str, str]] = []
    
    if model:
        # Send /model <name> first
        try:
            resp = await client.send_chat_message(
                workflow_id,
                f"/model {model}",
                history=history,
                thread_id=workflow_id,
                mode=mode,
            )
            channel = resp.get("stream_channel_id")
            if channel:
                async for event in client.stream_chat_events(channel):
                    if (
                        event
                        and event.get("type") == "chat_complete"
                        and not _is_progress_ack_event(event)
                    ):
                        content = event.get("content", "")
                        if content:
                            history.append({"role": "user", "content": f"/model {model}"})
                            history.append({"role": "assistant", "content": content})
        except Exception as e:
            print(f"Error setting model: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        resp = await client.send_chat_message(
            workflow_id,
            question,
            history=history,
            thread_id=workflow_id,
            mode=mode,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    channel = resp.get("stream_channel_id")
    if not channel:
        print("No stream channel returned.", file=sys.stderr)
        sys.exit(1)

    full_response = ""
    try:
        async for event in client.stream_chat_events(channel):
            if event is None:
                break
            ev_type = event.get("type", "")
            if ev_type == "chat_token":
                delta = event.get("delta", "")
                if delta:
                    print(delta, end="", flush=True)
                    full_response += delta
            elif ev_type == "chat_complete":
                if _is_progress_ack_event(event):
                    continue
                content = event.get("content", "")
                if not full_response and content:
                    print(content)
                    full_response = content
                else:
                    print()
                break
            elif ev_type == "chat_error":
                err = event.get("error", "Unknown error")
                print(f"\nError: {err}", file=sys.stderr)
                sys.exit(1)
    except Exception as e:
        print(f"\nStream error: {e}", file=sys.stderr)
        sys.exit(1)

    if output_path and full_response:
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(full_response)
        except Exception as e:
            print(f"Failed to write output to {output_path}: {e}", file=sys.stderr)


def main() -> None:
    from dan.cli import load_env
    load_env()

    parser = build_parser()
    args = parser.parse_args()

    workflow_id = "_scratch" if args.scratch else args.workflow_id
    mode = args.mode
    if mode is None:
        mode = "build" if workflow_id == "_scratch" else "mutate"
    if workflow_id == "_scratch" and mode == "agent":
        mode = "build"

    confirm_mode = args.confirm or os.environ.get("DAN_MUTATION_CONFIRM", "").strip() == "1"
    force_local = args.local
    base_url = args.server or os.environ.get("DAN_SERVER_URL") or _DEFAULT_URL

    async def _main() -> None:
        client: Any
        profile: Any | None = None
        profile_path: Path | None = None

        if force_local:
            from dan.cli.chat_local import LocalChatRuntime
            client = LocalChatRuntime()
            print("dan-chat — local mode (no server)")
        else:
            client = ChatClient(base_url=base_url)
            ok, err = await client.ping()
            if not ok:
                await client.close()
                if args.server:
                    msg = (
                        f"dan-chat: Server unavailable at {base_url}. "
                        "Start dan-serve first, or set DAN_SERVER_URL / --server. "
                        "(Tip: use 127.0.0.1 instead of localhost if you see timeouts.)"
                    )
                    if err:
                        msg += f"\n  Error: {err}"
                    print(msg, file=sys.stderr)
                    sys.exit(1)
                from dan.cli.chat_local import LocalChatRuntime
                client = LocalChatRuntime()
                print("dan-chat — local mode (server unavailable)")

        try:
            try:
                from dan.engine.user_profile import (
                    DEFAULT_PROFILE_PATH,
                    load_user_profile,
                    save_user_profile,
                )

                env_profile_path = os.environ.get("DAN_PROFILE_PATH", "").strip()
                profile_path = (
                    Path(env_profile_path).expanduser()
                    if env_profile_path
                    else DEFAULT_PROFILE_PATH
                )
                profile = load_user_profile(profile_path)
                profile.increment_session()
                save_user_profile(profile, profile_path)
            except Exception:
                logger.debug("UserProfile unavailable in chat CLI", exc_info=True)

            active_workflow_id = workflow_id
            active_mode = mode
            active_workflow_id = await _maybe_prompt_resume_workflow(
                client,
                active_workflow_id,
                profile,
            )
            if active_workflow_id != "_scratch" and active_mode == "build":
                active_mode = "mutate"

            if args.ask or args.pipe:
                if args.pipe:
                    question = sys.stdin.read().strip()
                else:
                    question = args.ask.strip()
                
                if not question:
                    print("No input provided.", file=sys.stderr)
                    sys.exit(1)
                
                await _run_one_shot(
                    client,
                    active_workflow_id,
                    active_mode,
                    question,
                    args.model,
                    args.output,
                )
                return

            # Apply model override if provided for REPL session
            if args.model:
                try:
                    resp = await client.send_chat_message(
                        active_workflow_id,
                        f"/model {args.model}",
                        history=[],
                        thread_id=active_workflow_id,
                        mode=active_mode,
                    )
                    channel = resp.get("stream_channel_id")
                    if channel:
                        async for event in client.stream_chat_events(channel):
                            if (
                                event
                                and event.get("type") == "chat_complete"
                                and not _is_progress_ack_event(event)
                            ):
                                print(f"Model set to {args.model}")
                                break
                except Exception as e:
                    print(f"Error setting model: {e}", file=sys.stderr)

            await _run_repl(
                client,
                active_workflow_id,
                active_mode,
                confirm_mode=confirm_mode,
                profile=profile,
                profile_path=profile_path,
            )
        finally:
            _save_readline_history()
            await _close_client_quietly(client)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print()


def main_ask() -> None:
    """Entry point for dan-ask."""
    # If stdin is piped, use --pipe, else use --ask with the first positional arg
    args = sys.argv[1:]
    new_args = ["dan-chat"]
    
    # Check if we have piped input
    if not sys.stdin.isatty():
        new_args.append("--pipe")
        new_args.extend(args)
    else:
        # We need a positional argument for the question
        question = None
        other_args = []
        for arg in args:
            if not arg.startswith("-") and question is None:
                question = arg
            else:
                other_args.append(arg)
        
        if question:
            new_args.extend(["--ask", question])
        new_args.extend(other_args)
        
    sys.argv = new_args
    main()

if __name__ == "__main__":
    main()
