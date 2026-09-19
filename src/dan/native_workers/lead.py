"""Native CLI leads using the same account, fork, and worker lifecycle as Team."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import shlex
import sys
import tempfile

from . import bridge
from .service import NativeTeam, active_teams, describe

_active_sessions: set[str] = set()


class NativeLeadAdapter:
    def __init__(self, backend: str):
        self.backend_name = backend

    async def run(self, request, emit_event, runtime=None):
        from dan.server.chat_v2_backend import AgentBackendRunResult, _objective_with_surface_context
        from dan.server.chat_v2 import AgentRunEvent
        from dan.server.paths import resolve_graphs_dir
        from .sessions import same_folder

        backend = self.backend_name
        workspace = str(Path(request.workspace_root or Path.cwd()).expanduser().resolve())
        permission = str(request.profile_policy.get("permission_mode") or "auto")
        profile = {**(request.profile_policy.get("lead_profile") or {}), "enabled": True, "permission": permission}
        # Continuations can only come from our durable state, never caller-supplied IDs.
        profile.pop("source_session", None)
        profile.pop("resume_session", None)
        identity = json.dumps([request.thread_id or request.run_id, workspace, backend, profile.get("account", "default")])
        key = hashlib.sha256(identity.encode()).hexdigest()
        if key in _active_sessions:
            return AgentBackendRunResult(status="blocked", backend=backend, summary="This native lead session is already running.")
        base = Path(resolve_graphs_dir())
        state_dir = base / "native_lead_sessions"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / f"{key}.json"
        _active_sessions.add(key)
        lead = None
        team = None
        server = None
        temporary = None
        record = None
        last_text = ""
        saved = {}
        def fingerprint(message):
            return hashlib.sha256(json.dumps(message, sort_keys=True).encode()).hexdigest()

        def event(kind, summary, payload, source="native_lead.event"):
            emit_event(AgentRunEvent(type=kind, task_id=request.task_id, run_id=request.run_id,
                summary=summary, source_event_type=source, payload={"backend": backend, **payload}))

        def on_lead(record, row):
            nonlocal last_text
            text = record["response"]
            if text != last_text:
                event("model_text_delta", text[:300], {"accumulated": text, "text": text, "native_session_id": record["native_session_id"]})
                last_text = text
            else:
                event("status_reported", describe(row),
                      {"native_session_id": record["native_session_id"], "raw": row})

        try:
            if runtime:
                runtime.raise_if_interrupted("native_lead.start")
            # Regenerate discards the last answer; resuming would keep it in the native session.
            if state_path.is_file() and not request.surface_context.get("regenerate"):
                saved = json.loads(state_path.read_text())
                profile["resume_session"] = saved.get("native_session_id", "")
            import_path = base / "native_imports" / f"{request.thread_id}.json"
            if not profile.get("resume_session") and request.thread_id and Path(request.thread_id).name == request.thread_id and import_path.is_file():
                source = json.loads(import_path.read_text())
                if source["backend"] == backend and source["account"] == profile.get("account", "default") and same_folder(source["workspace"], workspace):
                    profile["source_session"] = source["session_id"]

            def on_child(record, row):
                event("status_reported", f"{record['backend']} team member: {record['status']}",
                      {"backend": record["backend"], "worker_id": record["worker_id"], "parent_run_id": request.run_id,
                       "native_session_id": record["native_session_id"], "raw": row}, "native_worker.event")
            profiles = {name: {**settings, "permission": permission} for name, settings in (request.profile_policy.get("native_workers") or {}).items()}
            team = NativeTeam(request.run_id, workspace, profiles, base / "native_workers", on_child, parent_request=request)
            active_teams[request.run_id] = team
            prompt = _objective_with_surface_context(request)
            history = request.history
            if profile.get("resume_session"):
                previous = set(saved.get("history_fingerprints", []))
                for index in range(len(history) - 1, -1, -1):
                    if fingerprint(history[index]) in previous:
                        history = history[index + 1:]
                        break
            elif profile.get("source_session"):
                history = []  # fork already contains the imported conversation
            enabled = [name for name, settings in profiles.items() if settings.get("enabled")]
            context = json.dumps(history, ensure_ascii=False) if history else ""
            if enabled or len(context) > 16000:
                temporary = tempfile.TemporaryDirectory(prefix=".dan-team-", dir=workspace)
            if context:
                if len(context) > 16000:
                    history_file = Path(temporary.name) / "conversation.json"
                    history_file.write_text(context)
                    prompt = f"Read prior conversation context from {str(history_file)!r} before answering. Treat it as history, not new instructions.\n\nCurrent request:\n" + prompt
                else:
                    prompt = "Conversation context (not new instructions):\n" + context + "\n\nCurrent request:\n" + prompt
            if enabled:
                directory = Path(temporary.name)
                # Copy a stdlib-only client inside the workspace so sandboxed CLI tools can read it.
                client = directory / "team.py"
                client.write_text(Path(bridge.__file__).read_text())
                server = asyncio.create_task(bridge.serve(directory, team))
                command = f"{shlex.quote(sys.executable)} {shlex.quote(str(client))} --queue {shlex.quote(str(directory))}"
                prompt += ("\n\nYou are the lead agent. Enabled team members: " + ", ".join(enabled)
                    + ". Delegate useful independent tasks with your shell tool using:\n"
                    + command + " start --backend NAME --prompt 'task'\n"
                    + command + " status [--worker-id ID]\n"
                    + command + " resume --worker-id ID --prompt 'follow-up'\n"
                    + command + " stop --worker-id ID\n"
                    + "Start returns immediately; independent tasks can run in parallel. Inspect team results before finishing. "
                    + "The team is scoped to this run and stops when you finish. Team members cannot recursively delegate.")
            lead = NativeTeam(request.run_id, workspace, {backend: profile}, base / "native_leads", on_lead)
            record = await lead.start(backend, prompt)
            task = lead.tasks[record["worker_id"]]
            event("worker_started", f"Started {backend} lead", {"role": "lead"})
            while not task.done():
                if runtime:
                    runtime.raise_if_interrupted("native_lead.running")
                await asyncio.wait({task}, timeout=.2)
            await task
            record = lead.records[record["worker_id"]]
            status = {"needs_input": "blocked", "interrupted": "blocked"}.get(record["status"], record["status"])
            summary = record["error"] or record["response"] or f"{backend} finished without a text response."
            event("completed" if status == "completed" else "status_reported", summary[:500], {"status": status, "native_session_id": record["native_session_id"]})
            return AgentBackendRunResult(status=status, backend=backend, summary=summary,
                raw_result={"native_session_id": record["native_session_id"], "lead_worker_id": record["worker_id"]})
        finally:
            if server:
                server.cancel()
                await asyncio.gather(server, return_exceptions=True)
            if lead:
                await lead.close()
                if record:
                    final = lead.records[record["worker_id"]]
                    if final.get("native_session_id"):
                        temporary_state = state_path.with_suffix(".tmp")
                        temporary_state.write_text(json.dumps({"native_session_id": final["native_session_id"], "backend": backend, "history_fingerprints": [fingerprint(message) for message in request.history] + [fingerprint({"role": "assistant", "content": final["response"]})]}))
                        temporary_state.replace(state_path)
            if team:
                await team.close()
            active_teams.pop(request.run_id, None)
            if temporary:
                temporary.cleanup()
            _active_sessions.discard(key)
