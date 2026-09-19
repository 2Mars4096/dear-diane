"""Control a native child of the current DAN manager."""
from dan.native_workers.service import current_team

TOOL_METADATA = {
    "tool_id": "native_worker", "category": "agent",
    "description": "Delegate an independent task to an enabled DAN, Codex, Claude Code, or Antigravity worker. Start returns immediately; start multiple workers to run in parallel. Poll status to read results. Use resume for a follow-up to a settled worker. Inspect all results before ending the manager run; running children stop when the manager exits.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["start", "status", "stop", "resume"]},
        "backend": {"type": "string", "enum": ["dan", "codex", "claude", "antigravity", "cursor"]},
        "prompt": {"type": "string"}, "worker_id": {"type": "string"}}, "required": ["action"]},
    "examples": [{"input": {"action": "start", "backend": "codex", "prompt": "Review the test coverage"}, "output": {"status": "running"}}],
    "returns": {"type": "object", "description": "Worker identity, native session, status and output, or a list of workers."},
}

async def native_worker(action: str, backend: str = "", prompt: str = "", worker_id: str = "") -> dict:
    team = current_team.get()
    if team is None:
        raise ValueError("Native workers require a workbench manager run")
    if action == "start":
        return await team.start(backend, prompt)
    if action == "resume":
        previous = team.records.get(worker_id)
        if not previous:
            raise ValueError("Unknown child worker")
        return await team.start(previous["backend"], prompt, worker_id)
    if action == "stop":
        return await team.stop(worker_id)
    if action == "status":
        import asyncio
        pending = [task for key, task in team.tasks.items() if not task.done() and (not worker_id or key == worker_id)]
        if pending:
            await asyncio.wait(pending, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        if worker_id:
            if worker_id not in team.records:
                raise ValueError("Unknown child worker")
            return dict(team.records[worker_id])
        return {"workers": list(team.records.values()), "enabled_backends": [key for key, profile in team.profiles.items() if profile.get("enabled")]}
    raise ValueError("Unknown worker action")
