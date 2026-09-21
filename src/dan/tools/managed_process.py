"""Start and manage long-lived processes that outlive the current run."""
import os

TOOL_METADATA = {
    "tool_id": "managed_process", "category": "system",
    "description": "Run a long-lived command (dev server, watcher, tunnel) under DAN's process manager so it keeps running after this run ends or is stopped. Use shell_command for anything that finishes; use this for anything that must stay up. Read logs to confirm it started.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["start", "list", "stop", "logs"]},
        "command": {"type": "string", "description": "Shell command for start"},
        "name": {"type": "string"}, "cwd": {"type": "string"}, "id": {"type": "string"},
        "tail": {"type": "integer", "description": "Bytes of log to return"}}, "required": ["action"]},
    "examples": [{"input": {"action": "start", "name": "dev server", "command": "npm run dev"}, "output": {"status": "running"}}],
    "returns": {"type": "object", "description": "Process record(s) or a log tail."},
}


async def managed_process(action: str, command: str = "", name: str = "", cwd: str = "", id: str = "", tail: int = 8000) -> dict:
    from dan.processes import manager
    processes = manager()
    if action == "start":
        return processes.start(command, cwd or os.getcwd(), name=name, origin="dan")
    if action == "list":
        return {"processes": processes.list()}
    if action == "stop":
        return processes.stop(id)
    if action == "logs":
        return {"logs": processes.logs(id, tail)}
    raise ValueError("Unknown process action")
