"""dan-chat — REPL that talks to the server's chat API for conversational workflow authoring."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import readline  # noqa: F401 — enables arrow-key editing, history, word-skip in input()
import time as _time
import sys
from typing import Any, AsyncIterator

import httpx


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
        }
        http = await self._get_http()
        try:
            resp = await http.post("/api/chat/message", json=body)
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            raise RuntimeError(f"Cannot reach server: {exc}") from exc
        if resp.status_code != 200:
            raise RuntimeError(f"Chat API error {resp.status_code}: {resp.text}")
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
                if data.get("type") == "chat_complete":
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
            raise RuntimeError(f"Apply mutation error {resp.status_code}: {resp.text}")
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
            raise RuntimeError(f"Get graph error {resp.status_code}: {resp.text}")
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
            raise RuntimeError(f"Submit input error {resp.status_code}: {resp.text}")
        return True

    async def cancel_run(self, run_id: str) -> bool:
        """POST /api/runs/{run_id}/cancel."""
        http = await self._get_http()
        resp = await http.post(f"/api/runs/{run_id}/cancel")
        return resp.status_code == 200

    async def list_graphs(self) -> list[dict[str, Any]]:
        """GET /api/graphs. Returns list of {graph_id, name, description, updated_at}."""
        http = await self._get_http()
        try:
            resp = await http.get("/api/graphs")
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            raise RuntimeError(f"Cannot reach server: {exc}") from exc
        if resp.status_code != 200:
            raise RuntimeError(f"List graphs error {resp.status_code}: {resp.text}")
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
            raise RuntimeError(f"Create graph error {resp.status_code}: {resp.text}")
        return resp.json()

    async def save_graph(self, graph_id: str, data: dict[str, Any]) -> None:
        """PUT /api/graphs/{id}. Saves graph data."""
        http = await self._get_http()
        try:
            resp = await http.put(f"/api/graphs/{graph_id}", json=data)
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            raise RuntimeError(f"Cannot reach server: {exc}") from exc
        if resp.status_code != 200:
            raise RuntimeError(f"Save graph error {resp.status_code}: {resp.text}")


_MAX_HISTORY_MESSAGES = 40


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


async def _run_repl(
    client: ChatClient,
    workflow_id: str,
    mode: str,
) -> None:
    """REPL loop: read input, POST message, stream WS events, handle mutations."""
    history: list[dict[str, str]] = []
    client_graph_revision: str | None = None

    Console = None
    try:
        from rich.console import Console
    except ImportError:
        pass

    console = Console() if Console else None

    def _print(text: str, style: str | None = None) -> None:
        if console and style:
            console.print(text, style=style)
        else:
            print(text)

    def _status() -> None:
        _print(f"dan-chat — workflow: {workflow_id} (mode: {mode})")

    def _help() -> None:
        _print("Commands:")
        _print("  /run         Run the workflow (full)")
        _print("  /run-node @[Name](node:id)  Run a single node")
        _print("  /run-subgraph @[Name](subgraph:key)  Run a subgraph")
        _print("  /show        Show current graph (nodes & edges)")
        _print("  /save [name] Save workflow (prompts for name if on _scratch)")
        _print("  /list        List all saved workflows")
        _print("  /open <id>   Open an existing workflow")
        _print("  /saveas <id> Copy workflow to a new ID and switch")
        _print("  /new [id]    Create a new empty workflow")
        _print("  /rename <n>  Rename current workflow's display name")
        _print("  /exit        Exit the chat")
        _print("  /help        Show this help")

    _status()
    _print("Type /help for commands, /exit to quit.")
    _print("")

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            _print("")
            break

        if not line:
            continue

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
                            _print(f"Saved as '{save_name}'.")
                        except RuntimeError as e:
                            _print(f"Save failed: {e}", style="red" if console else None)
            break

        if line == "/help":
            _help()
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
                _print(f"Saved as '{save_id}'.")
                _status()
            except RuntimeError as e:
                _print(f"Error: {e}", style="red" if console else None)
            continue

        # --- /list ---
        if line == "/list":
            try:
                graphs = await client.list_graphs()
                _print(_format_workflow_list(graphs, workflow_id))
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
            try:
                graph_data = await client.get_graph(target_id)
                if graph_data is None:
                    _print(f"Workflow '{target_id}' not found.", style="red" if console else None)
                    continue
                workflow_id = target_id
                client_graph_revision = _compute_graph_revision(graph_data)
                history = []
                mode = "build" if workflow_id == "_scratch" else "mutate"
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
                _print(f"Saved and switched to '{new_id}'.")
                _status()
            except RuntimeError as e:
                _print(f"Error: {e}", style="red" if console else None)
            continue

        # --- /new [id] ---
        if line == "/new" or line.startswith("/new "):
            parts = line.split(maxsplit=1)
            new_id = parts[1].strip() if len(parts) > 1 and parts[1].strip() else f"workflow-{int(_time.time())}"
            try:
                result = await client.create_graph(new_id)
                workflow_id = new_id
                new_data = result.get("data", {"nodes": [], "edges": []})
                client_graph_revision = _compute_graph_revision(new_data)
                history = []
                mode = "build"
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

        # --- /show ---
        if line == "/show":
            try:
                graph_data = await client.get_graph(workflow_id)
                if graph_data is None:
                    _print("No graph found (not yet created).")
                else:
                    _print(f"  Workflow ID: {workflow_id}")
                    _print(_format_graph_summary(graph_data))
            except RuntimeError as e:
                _print(f"Error: {e}", style="red" if console else None)
            continue

        # Send message
        try:
            resp = await client.send_chat_message(
                workflow_id,
                line,
                history=history,
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

        # Stream events
        accumulated = ""
        mutation_plan: dict[str, Any] | None = None
        mutation_dry_run: dict[str, Any] | None = None
        pending_mutation_graph_id: str | None = None

        try:
            async for event in client.stream_chat_events(stream_channel_id):
                if event is None:
                    break

                ev_type = event.get("type", "")

                if ev_type == "chat_token":
                    delta = event.get("delta", "")
                    if delta:
                        print(delta, end="", flush=True)
                        accumulated += delta

                elif ev_type == "chat_complete":
                    if accumulated:
                        print()  # newline after stream
                    history.append({"role": "assistant", "content": accumulated})
                    if len(history) > _MAX_HISTORY_MESSAGES:
                        history = history[-_MAX_HISTORY_MESSAGES:]
                    rev = event.get("graph_revision")
                    if rev:
                        client_graph_revision = rev
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
                        _print(_format_mutation_summary(mutation_plan))
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

                    if run_event_type == "human_input_needed" and active_run_id:
                        detail = re.get("detail", {})
                        evt_data = detail.get("data", {})
                        request_id = evt_data.get("request_id")
                        render_mode = evt_data.get("render_mode", "text")
                        prompt_text = evt_data.get("prompt", "Input required:")
                        _print(f"\n{prompt_text}")
                        try:
                            loop = asyncio.get_running_loop()
                            if render_mode == "approval":
                                raw = await asyncio.wait_for(
                                    loop.run_in_executor(None, lambda: input("[Y/n] ")),
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
                                    loop.run_in_executor(None, lambda: input("> ")),
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
                    elif summary:
                        _print(summary)

                    if run_event_type in ("run_completed", "run_failed", "run_cancelled"):
                        break

        except Exception as e:
            _print(f"\nStream error: {e}", style="red" if console else None)

        # Mutation confirmation
        if mutation_plan and pending_mutation_graph_id:
            if not _should_prompt_apply(mutation_dry_run):
                _print(
                    "Skipping apply: dry-run failed. Send a follow-up message to repair the plan.",
                    style="yellow" if console else None,
                )
            elif _prompt_apply_mutation():
                try:
                    result = await client.apply_mutation(
                        pending_mutation_graph_id,
                        mutation_plan,
                    )
                    if result.get("success"):
                        _print("Mutation applied.")
                        if result.get("graph_revision"):
                            client_graph_revision = result["graph_revision"]
                        else:
                            new_graph = result.get("new_graph")
                            if new_graph and isinstance(new_graph, dict):
                                client_graph_revision = _compute_graph_revision(new_graph)
                    else:
                        errs = result.get("errors", [])
                        _print(f"Apply failed: {errs}", style="red" if console else None)
                except RuntimeError as e:
                    _print(f"Apply failed: {e}", style="red" if console else None)
            else:
                _print("Mutation rejected.")


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
        default="build",
        choices=["build", "mutate", "agent", "ask", "plan", "debug", "auto"],
        help="Chat mode (default: build for scratch)",
    )
    return p


def main() -> None:
    from dan.cli import load_env
    load_env()

    parser = build_parser()
    args = parser.parse_args()

    workflow_id = "_scratch" if args.scratch else args.workflow_id
    mode = args.mode
    if workflow_id == "_scratch" and mode == "agent":
        mode = "build"  # Scratch defaults to build-from-scratch

    base_url = args.server or os.environ.get("DAN_SERVER_URL") or _DEFAULT_URL

    async def _main() -> None:
        client = ChatClient(base_url=base_url)
        try:
            ok, err = await client.ping()
            if not ok:
                msg = (
                    f"dan-chat: Server unavailable at {base_url}. "
                    "Start dan-serve first, or set DAN_SERVER_URL / --server. "
                    "(Tip: use 127.0.0.1 instead of localhost if you see timeouts.)"
                )
                if err:
                    msg += f"\n  Error: {err}"
                print(msg, file=sys.stderr)
                sys.exit(1)

            await _run_repl(client, workflow_id, mode)
        finally:
            await client.close()

    asyncio.run(_main())


if __name__ == "__main__":
    main()
