"""Control a native child of the current Diane manager."""
from diane.native_workers.service import current_team, public_worker

TOOL_METADATA = {
    "tool_id": "native_worker", "category": "agent",
    "description": "Delegate an independent task to an enabled Diane, Codex, Claude Code, or Antigravity worker. Start returns immediately; start multiple workers to run in parallel. Poll status to read results. Use resume for a follow-up to a settled worker. Start isolates Git edits by default; set isolate=false for shared read-only work or non-Git folders. Review the owned worktree, then apply to integrate without overwriting changed source files. Inspect all results before ending the manager run; running children stop when the manager exits.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["start", "status", "stop", "resume", "apply"]},
        "backend": {"type": "string", "enum": ["dan", "codex", "claude", "antigravity", "cursor"]},
        "isolate": {"type": "boolean", "default": True, "description": "Use an owned Git worktree for parallel edits. Set false for shared read-only work or non-Git folders."},
        "prompt": {"type": "string"}, "worker_id": {"type": "string"}}, "required": ["action"]},
    "examples": [{"input": {"action": "start", "backend": "codex", "prompt": "Review the test coverage"}, "output": {"status": "running"}}],
    "returns": {"type": "object", "description": "Worker identity, native session, status and output, or a list of workers."},
}

async def native_worker(action: str, backend: str = "", prompt: str = "", worker_id: str = "", isolate: bool = True) -> dict:
    team = current_team.get()
    if team is None:
        raise ValueError("Native workers require a workbench manager run")
    if action == "start":
        return public_worker(await team.start(backend, prompt, isolate=isolate))
    if action == "resume":
        previous = team.records.get(worker_id)
        if not previous:
            raise ValueError("Unknown child worker")
        return public_worker(await team.start(previous["backend"], prompt, worker_id))
    if action == "apply":
        from diane.native_workers.workspaces import apply
        import asyncio
        async with team.admission_lock:
            row = team.records.get(worker_id)
            if not row or (worker_id in team.tasks and not team.tasks[worker_id].done()):
                raise ValueError("Wait for this worker to finish before applying changes")
            result = await asyncio.to_thread(apply, row)
            team.save(row)
            return result
    if action == "stop":
        return public_worker(await team.stop(worker_id))
    if action == "status":
        import asyncio
        pending = [task for key, task in team.tasks.items() if not task.done() and (not worker_id or key == worker_id)]
        if pending:
            await asyncio.wait(pending, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        if worker_id:
            if worker_id not in team.records:
                raise ValueError("Unknown child worker")
            return public_worker(team.records[worker_id])
        return {"workers": [public_worker(row) for row in team.records.values()], "enabled_backends": [key for key, profile in team.profiles.items() if profile.get("enabled")]}
    raise ValueError("Unknown worker action")
