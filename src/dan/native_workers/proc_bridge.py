"""File RPC so sandboxed CLI leads can ask DAN to run long-lived processes.

Anything an agent starts from its own shell dies with its run. Commands sent through
this bridge are started by DAN's process manager instead, so they keep running after
the agent stops. The client is standalone stdlib Python (copied into the workspace).
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time
from uuid import uuid4


async def serve(directory: Path, workspace: str, workspace_id: str = "", thread_id: str = "", backend: str = "agent"):
    from dan.processes import manager
    while True:
        for request in directory.glob("*.proc-request"):
            response = request.with_suffix(".proc-response")
            try:
                if request.stat().st_size > 256 * 1024:
                    raise ValueError("Process request is too large")
                payload = json.loads(request.read_text())
                action = payload.get("action")
                processes = manager()
                if action == "start":
                    cwd = str(payload.get("cwd") or workspace)
                    result = processes.start(str(payload.get("command") or ""), cwd, name=str(payload.get("name") or ""),
                                             workspace_id=workspace_id, thread_id=thread_id, origin=backend)
                elif action == "list":
                    result = {"processes": processes.list(cwd=workspace)}
                elif action == "stop":
                    result = processes.stop(str(payload.get("id") or ""))
                elif action == "logs":
                    result = {"logs": processes.logs(str(payload.get("id") or ""), int(payload.get("tail") or 8000))}
                else:
                    raise ValueError("Unknown process action")
                output = {"ok": True, "result": result}
            except Exception as exc:
                output = {"ok": False, "error": str(exc)}
            temporary = response.with_suffix(".tmp")
            temporary.write_text(json.dumps(output))
            temporary.replace(response)
            request.unlink(missing_ok=True)
        await asyncio.sleep(.1)


def main():
    parser = argparse.ArgumentParser(description="Run long-lived processes through DAN so they outlive this agent run")
    parser.add_argument("--queue", required=True)
    parser.add_argument("action", choices=["start", "list", "stop", "logs"])
    parser.add_argument("--name", default="")
    parser.add_argument("--cwd", default="")
    parser.add_argument("--id", default="")
    parser.add_argument("--tail", type=int, default=8000)
    # Everything after a literal "--" is the command; options before it belong to this client.
    import shlex
    import sys
    argv = sys.argv[1:]
    split = argv.index("--") if "--" in argv else len(argv)
    args = parser.parse_args(argv[:split])
    command_parts = argv[split + 1:]
    directory = Path(args.queue)
    if not directory.is_dir():
        parser.error("This lead run has ended; its process bridge is closed")
    command = (command_parts[0] if len(command_parts) == 1 else " ".join(shlex.quote(part) for part in command_parts)) if args.action == "start" else ""
    request = directory / f"{uuid4().hex}.proc-request"
    temporary = request.with_suffix(".tmp")
    temporary.write_text(json.dumps({"action": args.action, "name": args.name, "cwd": args.cwd, "id": args.id, "tail": args.tail, "command": command}))
    temporary.replace(request)
    response = request.with_suffix(".proc-response")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if response.exists():
            output = json.loads(response.read_text())
            response.unlink(missing_ok=True)
            print(json.dumps(output))
            return 0 if output.get("ok") else 1
        if not directory.exists():
            break
        time.sleep(.1)
    print(json.dumps({"ok": False, "error": "Process bridge closed or timed out; run list before retrying a start"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
