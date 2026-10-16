"""Control a native child of the current Diane manager."""
from diane.native_workers.service import current_team, public_worker
from diane.native_workers.management import record_review, worker_summary

TOOL_METADATA = {
    "tool_id": "native_worker", "category": "agent",
    "description": "Use status to discover previous_workers from this conversation/workspace as well as configured_workers. Inspect a previous worker with status or events; resume preserves its native session/worktree in a new run-owned record, so use the returned worker_id. Live/unrelated workers and changed account/access settings cannot be resumed. Use status to discover configured_workers and select profile_id when starting. Each configured worker can take one task at a time. Workers with allow_children may spawn children only from profile_id self; children inherit the parent configuration and share the root capacity. Delegate an independent task to an enabled Diane, Codex, Claude Code, Cursor, or Antigravity worker. Start returns immediately; independent workers can run in parallel. Status reports max_parallel_workers, a shared ceiling across all providers excluding the lead, not a target or spending limit. Use fewer workers when sufficient; multiple workers may use the same provider. Wait for a slot when full. Optionally supply task scope and acceptance criteria in contract. Team-wide status returns compact previews; fetch status with worker_id for full output and current attempt before reviewing. Runtime completion is not acceptance: record review with the current attempt, a decision and inspected evidence. This records the lead's judgment, not independent verification. Use a fresh start with a new task contract when scope or owned files change. Use resume only to repair the same task; it retains the task contract and clears the current review. Start isolates Git edits by default. Shared inspection requires read_only=true with isolate=false. Use changes to inspect the owned diff, then review with current attempt and evidence, then apply to integrate without overwriting changed source files. Inspect all results before ending the manager run; running children stop when the manager exits.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["start", "status", "stop", "resume", "changes", "apply", "review", "events"]},
        "profile_id": {"type": "string", "description": "Configured worker ID from status; required when multiple profiles share a runtime."},
        "backend": {"type": "string", "enum": ["dan", "codex", "claude", "antigravity", "cursor"]},
        "read_only": {"type": "boolean", "default": False, "description": "Use read-only runtime permissions for shared inspection. Editing tasks require an isolated worktree."},
        "isolate": {"type": "boolean", "default": True, "description": "Use an owned Git worktree for parallel edits. Set false for shared read-only work or non-Git folders."},
        "contract": {"type": "object", "additionalProperties": False, "properties": {
            "scope": {"type": "string", "maxLength": 4000},
            "acceptance": {"type": "array", "maxItems": 20, "items": {"type": "string", "minLength": 1, "maxLength": 2000}}},
            "description": "Optional start-only task scope and acceptance criteria; preserved on resume. Does not grant permissions."},
        "attempt": {"type": "integer", "description": "For review: current attempt from status; rejects stale reviews."},
        "decision": {"type": "string", "enum": ["accepted", "revise", "blocked"]},
        "evidence": {"type": "array", "items": {"type": "string"}, "description": "For review: checks, artifact references and unresolved criteria actually inspected by the lead."},
        "before": {"type": "integer", "description": "For events: fetch entries before this cursor."}, "after": {"type": "integer", "description": "For events: fetch entries after this cursor."}, "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
        "prompt": {"type": "string"}, "worker_id": {"type": "string"}}, "required": ["action"]},
    "examples": [{"input": {"action": "start", "backend": "codex", "prompt": "Review the test coverage"}, "output": {"status": "running"}}],
    "returns": {"type": "object", "description": "Team status gives compact previews with truncation flags. Status with worker_id returns full output/contract/attempt. Completion is not acceptance: inspect full results, then record review with the current attempt and evidence. Resume only the worker needing repair; review resets on resume."},
}

async def native_worker(action: str, backend: str = "", prompt: str = "", worker_id: str = "", isolate: bool = True,
                        contract: dict | None = None, attempt: int = -1, decision: str = "", evidence: list[str] | None = None, profile_id: str = "", read_only: bool = False, after: int | None = None, before: int | None = None, limit: int = 25) -> dict:
    team = current_team.get()
    if team is None:
        raise ValueError("Native workers require a workbench manager run")
    if contract is not None and action != "start":
        raise ValueError("Task contracts are supplied only when starting a worker")
    if action == "start":
        return public_worker(await team.start(backend, prompt, isolate=isolate, contract=contract, profile_id=profile_id, read_only=read_only))
    if action == "resume":
        previous = team.records.get(worker_id)
        if not previous:
            if hasattr(team, "resume_previous"):
                return public_worker(await team.resume_previous(worker_id, prompt))
            raise ValueError("Unknown child worker")
        return public_worker(await team.start(previous["backend"], prompt, worker_id))
    if action in {"changes", "apply"}:
        from diane.native_workers.workspaces import apply, preview
        import asyncio
        async with team.admission_lock:
            row = team.records.get(worker_id)
            if not row or (worker_id in team.tasks and not team.tasks[worker_id].done()):
                raise ValueError("Wait for this worker to finish before applying changes")
            if action == "changes":
                result = await asyncio.to_thread(preview, row)
                row["inspected_tree"] = result["tree"]
                row["inspected_attempt"] = row.get("attempt")
                team.save(row)
                return result
            review = row.get("lead_review") or {}
            if review.get("decision") != "accepted" or review.get("attempt") != row.get("attempt") or not review.get("tree"):
                raise ValueError("Inspect changes and record an accepted review for the current attempt before applying")
            result = await asyncio.to_thread(apply, row, review["tree"])
            team.save(row)
            return result
    if action == "stop":
        return public_worker(await team.stop(worker_id))
    if action == "review":
        import asyncio
        async with team.admission_lock:
            row = team.records.get(worker_id)
            if not row:
                raise ValueError("Unknown child worker")
            task = team.tasks.get(worker_id)
            if task and not task.done():
                raise ValueError("Wait for this worker to settle before reviewing")
            reviewed_tree = None
            if decision == "accepted" and row.get("baseline_tree"):
                from diane.native_workers.workspaces import preview
                reviewed_tree = (await asyncio.to_thread(preview, row))["tree"]
                if row.get("inspected_tree") != reviewed_tree or row.get("inspected_attempt") != attempt:
                    raise ValueError("Inspect changes for this attempt again before accepting; the diff is missing or has changed")
            review = record_review(row, attempt, decision, evidence)
            if reviewed_tree:
                row["lead_review"]["tree"] = reviewed_tree
                review["tree"] = reviewed_tree
            team.save(row)
            team.event(row, {"type": "worker.reviewed", "review": review})
            return public_worker(row)
    if action == "events":
        row = team.records.get(worker_id)
        if row is None and hasattr(team, "prior_worker"):
            row = team.prior_worker(worker_id)
        if row is None:
            raise ValueError("Unknown child worker")
        from diane.native_workers.event_pages import event_page
        import json
        owner = getattr(team, "team", team)
        if before is not None and after is not None:
            raise ValueError("Choose before or after, not both")
        result = event_page(owner.base / f"{row['worker_id']}.jsonl", before=before, after=after, limit=max(1, min(limit, 100)))
        entries = []
        for entry in result["entries"]:
            serialized = json.dumps(entry["event"], ensure_ascii=False)
            entries.append(entry if len(serialized) <= 8000 else {"cursor": entry["cursor"], "event": {"preview": serialized[:8000], "truncated": True}})
        return {"worker_id": row["worker_id"], "entries": entries, "before": result["before"], "has_more": result["has_more"]}
    if action == "status":
        import asyncio
        pending = [task for key, task in team.tasks.items() if not task.done() and (not worker_id or key == worker_id)]
        if pending:
            await asyncio.wait(pending, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        if worker_id:
            if worker_id not in team.records:
                if hasattr(team, "prior_worker"):
                    return {**public_worker(team.prior_worker(worker_id)), "can_stop": False, "can_apply": False, "can_reply": False}
                raise ValueError("Unknown child worker")
            return public_worker(team.records[worker_id])
        return {"previous_workers": [worker_summary({**public_worker(row), "can_stop": False, "can_apply": False, "can_reply": False}) for row in team.previous_workers()] if hasattr(team, "previous_workers") else [], "configured_workers": [{"profile_id": key, "backend": value.get("backend", key), "name": value.get("name", key), "model": value.get("model", ""), "account": value.get("account", "default"), "allow_children": bool(value.get("allow_children"))} for key, value in team.profiles.items() if value.get("enabled")], "max_parallel_workers": team.max_parallel_workers, "workers": [worker_summary(public_worker(row)) for row in team.records.values()],
                "detail_hint": "Use status with worker_id for full output, task contract and lead review. Completion is not acceptance; inspect evidence before recording review. Resume only the worker needing revision.",
                "enabled_backends": sorted({profile.get("backend", key) for key, profile in team.profiles.items() if profile.get("enabled")})}
    raise ValueError("Unknown worker action")
