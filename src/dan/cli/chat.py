"""dan-chat — REPL that talks to the server's chat API for conversational workflow authoring."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger("dan.cli.chat")

_DEFAULT_URL = "http://localhost:8000"
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

    async def ping(self) -> bool:
        """Check if server is available."""
        try:
            http = await self._get_http()
            resp = await http.get("/health", timeout=_PING_TIMEOUT)
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException, OSError):
            return False

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
                yield data
                if data is None:
                    return
                # Stream ends with None sent by server
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


def _prompt_apply_mutation() -> bool:
    """Prompt 'Apply mutation? [Y/n]' and return True for Y/yes/Enter."""
    try:
        line = input("Apply mutation? [Y/n] ").strip().lower()
        return line in ("", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


async def _run_repl(
    client: ChatClient,
    workflow_id: str,
    mode: str,
) -> None:
    """REPL loop: read input, POST message, stream WS events, handle mutations."""
    history: list[dict[str, str]] = []
    client_graph_revision: str | None = None

    Console, rich = None, None
    try:
        from rich.console import Console
        import rich
        Console, rich = Console, rich
    except ImportError:
        pass

    console = Console() if Console else None

    def _print(text: str, style: str | None = None) -> None:
        if console and style:
            console.print(text, style=style)
        else:
            print(text)

    def _help() -> None:
        _print("Commands:")
        _print("  /run         Run the workflow (full)")
        _print("  /run-node @[Name](node:id)  Run a single node")
        _print("  /run-subgraph @[Name](subgraph:key)  Run a subgraph")
        _print("  /exit        Exit the chat")
        _print("  /help        Show this help")

    _print(f"dan-chat — workflow: {workflow_id} (mode: {mode})")
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
            break

        if line == "/help":
            _help()
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
        if resp.get("type") == "run_started":
            _print(f"Started {resp.get('scope', 'full')} run.")
            stream_channel_id = resp.get("stream_channel_id")
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

        # Append user message to history for next turn
        history.append({"role": "user", "content": line})

        # Stream events
        accumulated = ""
        mutation_plan: dict[str, Any] | None = None
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
                    if summary:
                        _print(summary)

        except Exception as e:
            _print(f"\nStream error: {e}", style="red" if console else None)

        # Mutation confirmation
        if mutation_plan and pending_mutation_graph_id:
            if _prompt_apply_mutation():
                try:
                    result = await client.apply_mutation(
                        pending_mutation_graph_id,
                        mutation_plan,
                    )
                    if result.get("success"):
                        _print("Mutation applied.")
                        if result.get("new_graph"):
                            # Update revision from new graph if returned
                            pass  # Server doesn't return revision in apply response
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
        help="Server URL (default: DAN_SERVER_URL or http://localhost:8000)",
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
            if not await client.ping():
                print(
                    "dan-chat: Server unavailable. Start dan-serve first, or set DAN_SERVER_URL.",
                    file=sys.stderr,
                )
                sys.exit(1)

            await _run_repl(client, workflow_id, mode)
        finally:
            await client.close()

    asyncio.run(_main())


if __name__ == "__main__":
    main()
