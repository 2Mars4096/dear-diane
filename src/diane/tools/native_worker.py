"""Control a native child of the current Diane manager."""
from diane.native_workers.service import current_team, public_worker
from diane.native_workers.management import record_review, worker_summary

TOOL_METADATA = {
    "tool_id": "native_worker", "category": "agent",
    "description": "Use status to discover configured_workers and select profile_id when starting. Each configured worker can take one task at a time. Workers with allow_children may spawn children only from profile_id self; children inherit the parent configuration and share the root capacity. Delegate an independent task to an enabled Diane, Codex, Claude Code, Cursor, or Antigravity worker. Start returns immediately; independent workers can run in parallel. Status reports max_parallel_workers, a shared ceiling across all providers excluding the lead, not a target or spending limit. Use fewer workers when sufficient; multiple workers may use the same provider. Wait for a slot when full. Optionally supply task scope and acceptance criteria in contract. Team-wide status returns compact previews; fetch status with worker_id for full output and current attempt before reviewing. Runtime completion is not acceptance: record review with the current attempt, a decision and inspected evidence. This records the lead's judgment, not independent verification. Use resume for a follow-up only to the settled worker needing repair; it retains the task contract and clears the current review. Start isolates Git edits by default; set isolate=false for shared read-only work or non-Git folders. Review the owned worktree, then apply to integrate without overwriting changed source files. Inspect all results before ending the manager run; running children stop when the manager exits.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["start", "status", "stop", "resume", "apply", "review"]},
        "profile_id": {"type": "string", "description": "Configured worker ID from status; required when multiple profiles share a runtime."},
        "backend": {"type": "string", "enum": ["dan", "codex", "claude", "antigravity", "cursor"]},
        "isolate": {"type": "boolean", "default": True, "description": "Use an owned Git worktree for parallel edits. Set false for shared read-only work or non-Git folders."},
        "contract": {"type": "object", "additionalProperties": False, "properties": {
            "scope": {"type": "string", "maxLength": 4000},
            "acceptance": {"type": "array", "maxItems": 20, "items": {"type": "string", "minLength": 1, "maxLength": 2000}}},
            "description": "Optional start-only task scope and acceptance criteria; preserved on resume. Does not grant permissions."},
        "attempt": {"type": "integer", "description": "For review: current attempt from status; rejects stale reviews."},
        "decision": {"type": "string", "enum": ["accepted", "revise", "blocked"]},
        "evidence": {"type": "array", "items": {"type": "string"}, "description": "For review: checks, artifact references and unresolved criteria actually inspected by the lead."},
        "prompt": {"type": "string"}, "worker_id": {"type": "string"}}, "required": ["action"]},
    "examples": [{"input": {"action": "start", "backend": "codex", "prompt": "Review the test coverage"}, "output": {"status": "running"}}],
    "returns": {"type": "object", "description": "Team status gives compact previews with truncation flags. Status with worker_id returns full output/contract/attempt. Completion is not acceptance: inspect full results, then record review with the current attempt and evidence. Resume only the worker needing repair; review resets on resume."},
}

async def native_worker(action: str, backend: str = "", prompt: str = "", worker_id: str = "", isolate: bool = True,
                        contract: dict | None = None, attempt: int = -1, decision: str = "", evidence: list[str] | None = None, profile_id: str = "") -> dict:
    team = current_team.get()
    if team is None:
        raise ValueError("Native workers require a workbench manager run")
    if contract is not None and action != "start":
        raise ValueError("Task contracts are supplied only when starting a worker")
    if action == "start":
        return public_worker(await team.start(backend, prompt, isolate=isolate, contract=contract, profile_id=profile_id))
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
    if action == "review":
        async with team.admission_lock:
            row = team.records.get(worker_id)
            if not row:
                raise ValueError("Unknown child worker")
            task = team.tasks.get(worker_id)
            if task and not task.done():
                raise ValueError("Wait for this worker to settle before reviewing")
            review = record_review(row, attempt, decision, evidence)
            team.save(row)
            team.event(row, {"type": "worker.reviewed", "review": review})
            return public_worker(row)
    if action == "status":
        import asyncio
        pending = [task for key, task in team.tasks.items() if not task.done() and (not worker_id or key == worker_id)]
        if pending:
            await asyncio.wait(pending, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        if worker_id:
            if worker_id not in team.records:
                raise ValueError("Unknown child worker")
            return public_worker(team.records[worker_id])
        return {"configured_workers": [{"profile_id": key, "backend": value.get("backend", key), "name": value.get("name", key), "model": value.get("model", ""), "account": value.get("account", "default"), "allow_children": bool(value.get("allow_children"))} for key, value in team.profiles.items() if value.get("enabled")], "max_parallel_workers": team.max_parallel_workers, "workers": [worker_summary(public_worker(row)) for row in team.records.values()],
                "detail_hint": "Use status with worker_id for full output, task contract and lead review. Completion is not acceptance; inspect evidence before recording review. Resume only the worker needing revision.",
                "enabled_backends": sorted({profile.get("backend", key) for key, profile in team.profiles.items() if profile.get("enabled")})}
    raise ValueError("Unknown worker action")
