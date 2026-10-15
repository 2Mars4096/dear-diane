"""Workbench runtime discovery and parent-scoped worker inspection."""
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, SecretStr, Field
from diane.native_workers.sessions import discover, messages
from diane.server.chat_store import ChatMessage
import json
from diane.native_workers.catalog import catalog
from diane.native_workers.service import active_teams, read_workers, public_worker, continue_worker
from diane.server.paths import resolve_graphs_dir

router = APIRouter(tags=["native-workers"])

@router.get("/api/native-workers/catalog")
def runtime_catalog():
    return catalog()

class ProviderKeyInput(BaseModel):
    api_key: SecretStr


def _local_provider_settings(request: Request):
    from urllib.parse import urlsplit
    import os
    origin = request.headers.get("origin")
    host = urlsplit("//" + request.headers.get("host", "")).hostname
    if (os.environ.get("DAN_REMOTE_CONFIG") or not request.client
            or request.client.host not in {"127.0.0.1", "::1", "testclient"}
            or host not in {"localhost", "127.0.0.1", "testserver", "::1"}
            or (origin and urlsplit(origin).hostname not in {"localhost", "127.0.0.1", "::1"})):
        raise HTTPException(403, "Manage API keys from the local Dear Diane app on this host")


@router.get("/api/model-providers")
def model_providers(request: Request):
    _local_provider_settings(request)
    from diane.native_workers.models import API_PROVIDERS, provider_key
    from diane.native_workers.provider_credentials import saved_keys
    saved = saved_keys()
    return {"providers": [{"id": key, "label": spec["label"], "configured": bool(provider_key(key)),
                            "saved": bool(saved.get(key)), "key_env": spec["env"]} for key, spec in API_PROVIDERS.items()]}


@router.put("/api/model-providers/{provider}/key")
def save_provider_key(provider: str, body: ProviderKeyInput, request: Request):
    _local_provider_settings(request)
    from diane.native_workers.provider_credentials import save_key
    try:
        save_key(provider, body.api_key.get_secret_value())
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    return model_providers(request)


@router.get("/api/native-sessions")
def native_sessions(workspace: str):
    if not workspace or not Path(workspace).is_dir():
        raise HTTPException(400, "Choose an existing project folder first")
    return {"sessions": [{key: value for key, value in row.items() if key != "path"} for row in discover(workspace)]}

class SessionImport(BaseModel):
    source_id: str
    workspace: str
    workspace_id: str
    fork: bool = True

@router.post("/api/native-sessions/import")
def import_session(body: SessionImport, request: Request):
    if not body.fork:
        raise HTTPException(400, "Only forked imports are supported; originals stay untouched")
    source = next((row for row in discover(body.workspace) if row["id"] == body.source_id), None)
    if not source or not source["can_import"]:
        raise HTTPException(400, source["reason"] if source else "Session is no longer available for this folder")
    try:
        history = messages(source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not body.workspace_id or "/" in body.workspace_id or "\\" in body.workspace_id or body.workspace_id in {".", ".."}:
        raise HTTPException(400, "Invalid project ID")
    store = request.app.state.chat_store
    thread = store.create_thread(body.workspace_id, title=source["title"] + " (fork)")
    thread.messages = [ChatMessage(**message) for message in history]
    store.save_thread(thread)
    base = Path(resolve_graphs_dir()) / "native_imports"
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{thread.id}.json").write_text(json.dumps({**source, "fork": True}))
    return {"id": thread.id, "workflow_id": thread.workflow_id, "title": thread.title,
            "message_count": len(thread.messages), "created_at": thread.created_at.isoformat(), "updated_at": thread.updated_at.isoformat()}

@router.get("/api/worker-attention")
def worker_attention():
    base = Path(resolve_graphs_dir()) / "native_workers"
    workers = []
    for path in sorted(base.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:160]:
        try:
            row = json.loads(path.read_text())
            if row.get("thread_id"):
                item = {key: row.get(key, "") for key in ("worker_id", "thread_id", "status")}
                team = active_teams.get(row["parent_run_id"])
                client = team.steering.get(row["worker_id"]) if team else None
                if row["status"] in {"running", "needs_input"} and (not team or row["worker_id"] not in team.records):
                    item["status"] = "interrupted"
                item["attempt"] = str((row.get("actions") or [{}])[0].get("at", row.get("created_at", "")))
                item["request"] = ":".join(client.requests) if client and hasattr(client, "requests") else ""
                workers.append(item)
        except (ValueError, OSError):
            continue
    return {"workers": workers}


@router.get("/api/native-workers/{parent_id}")
def list_workers(parent_id: str):
    base = Path(resolve_graphs_dir())
    team = active_teams.get(parent_id)
    profiles = team.profiles if team else {}
    if not team and Path(parent_id).name == parent_id:
        try:
            run = json.loads((base / "chat_v2" / "runs" / f"{parent_id}.json").read_text())
            profiles = (run.get("metadata", {}).get("profile_policy") or {}).get("native_workers", {})
        except (OSError, ValueError):
            pass
    roster = []
    used = {str(profile.get("name") or "").strip() for profile in profiles.values()}
    number = 1
    for identity, profile in profiles.items():
        if not (profile.get("enabled") or profile.get("backend")):
            continue
        name = str(profile.get("name") or "").strip()
        if not name:
            while f"Worker {number}" in used:
                number += 1
            name = f"Worker {number}"
            used.add(name)
            number += 1
        roster.append({"profile_id": identity, "name": name, "backend": profile.get("backend", identity)})
    lead = getattr(team, "lead_team", None)
    lead_requests = [request for client in lead.steering.values() for request in client.requests.values()] if lead else []
    return {"workers": [public_worker(row) for row in read_workers(base / "native_workers", parent_id)], "roster": roster, "lead_requests": lead_requests}

@router.post("/api/native-workers/{parent_id}/{worker_id}/stop")
async def stop_worker(parent_id: str, worker_id: str):
    team = active_teams.get(parent_id)
    if not team or worker_id not in team.records:
        raise HTTPException(404, "No active worker with that parent")
    if team.records[worker_id].get("can_stop") is False:
        raise HTTPException(409, "This subagent is controlled by its Codex lead")
    return public_worker(await team.stop(worker_id))

class WorkerReply(BaseModel):
    prompt: str = Field(min_length=1, max_length=100000)


@router.post("/api/native-workers/{parent_id}/{worker_id}/reply")
async def reply_worker(parent_id: str, worker_id: str, body: WorkerReply):
    try:
        return await continue_worker(Path(resolve_graphs_dir()) / "native_workers", parent_id, worker_id, body.prompt)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/native-workers/{parent_id}/{worker_id}/changes")
def worker_changes(parent_id: str, worker_id: str):
    from diane.native_workers.workspaces import preview
    row = next((r for r in read_workers(Path(resolve_graphs_dir()) / "native_workers", parent_id) if r["worker_id"] == worker_id), None)
    if not row:
        raise HTTPException(404, "Unknown worker")
    try:
        return preview(row)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class WorkerApply(BaseModel):
    tree: str = Field(min_length=1, max_length=64)


@router.post("/api/native-workers/{parent_id}/{worker_id}/apply")
async def apply_worker(parent_id: str, worker_id: str, body: WorkerApply):
    import asyncio
    from diane.native_workers.workspaces import apply
    from diane.native_workers.service import NativeTeam
    base = Path(resolve_graphs_dir()) / "native_workers"
    row = next((r for r in read_workers(base, parent_id) if r["worker_id"] == worker_id), None)
    team = active_teams.get(parent_id)
    task = team.tasks.get(worker_id) if team else None
    if not row or row.get("can_stop") is False or (task and not task.done()):
        raise HTTPException(409, "Wait for this worker to finish")
    try:
        # Serialize apply against follow-up admission on this team.
        owner = team or NativeTeam(parent_id, row["workspace_root"], {}, base)
        active_teams.setdefault(parent_id, owner)
        owner.records.setdefault(worker_id, row)
        async with owner.admission_lock:
            task = owner.tasks.get(worker_id)
            if task and not task.done():
                raise ValueError("Worker is still running")
            live_row = owner.records.get(worker_id, row)
            await asyncio.to_thread(apply, live_row, body.tree)
            owner.save(live_row)
            return public_worker(live_row)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class WorkerAnswer(BaseModel):
    request_id: str
    decision: str = ""
    answers: dict[str, str] = Field(default_factory=dict)


@router.post("/api/native-lead/{parent_id}/answer")
async def answer_lead(parent_id: str, body: WorkerAnswer):
    lead = getattr(active_teams.get(parent_id), "lead_team", None)
    client = next((client for client in lead.steering.values() if body.request_id in client.requests), None) if lead else None
    if not client:
        raise HTTPException(409, "This permission request is no longer pending")
    try:
        await client.reply(body.request_id, body.decision, body.answers)
        return {"accepted": True}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/native-workers/{parent_id}/{worker_id}/answer")
async def answer_worker(parent_id: str, worker_id: str, body: WorkerAnswer):
    team = active_teams.get(parent_id)
    client = team.steering.get(worker_id) if team else None
    if not client:
        raise HTTPException(409, "Worker is no longer connected; resume it instead")
    if body.decision == "accept" and team.records[worker_id].get("profile", {}).get("permission") == "plan" and client.requests.get(body.request_id, {}).get("params", {}).get("tool_name") != "AskUserQuestion":
        raise HTTPException(409, "Plan mode cannot approve mutations")
    try:
        await client.reply(body.request_id, body.decision, body.answers)
        return public_worker(team.records[worker_id])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/native-workers/{parent_id}/{worker_id}/events")
def worker_events(parent_id: str, worker_id: str, before: int | None = None, limit: int = 100, after: int | None = None):
    base = Path(resolve_graphs_dir()) / "native_workers"
    if Path(worker_id).name != worker_id or worker_id in {".", ".."}:
        raise HTTPException(404, "Unknown worker")
    try:
        record = json.loads((base / f"{worker_id}.json").read_text())
    except (OSError, ValueError):
        raise HTTPException(404, "Unknown worker")
    if record.get("parent_run_id") != parent_id or record.get("worker_id") != worker_id:
        raise HTTPException(404, "Unknown worker")
    from diane.native_workers.event_pages import event_page
    return event_page(base / f"{worker_id}.jsonl", before=before, after=after, limit=limit)


@router.get("/api/usage")
def account_usage(limit: int | None = None):
    """Quota for discovered logins and configured API providers. Credentials stay server-side."""
    from diane.native_workers.usage import usage_report
    return usage_report(Path(resolve_graphs_dir()), max(1, min(limit, 100)) if limit is not None else None)


@router.get("/api/token-usage/sessions")
def token_usage_sessions(limit: int = 40):
    """Recent Claude Code and Codex sessions with token totals, read from their transcripts."""
    from diane.native_workers.token_usage import list_sessions
    return list_sessions(Path(resolve_graphs_dir()), max(1, min(limit, 200)))


@router.get("/api/token-usage/overview")
def token_usage_overview(limit: int = 120):
    """All recent sessions combined: per day, project, agent, model, and activity."""
    from diane.native_workers.token_usage import overview
    return overview(Path(resolve_graphs_dir()), max(1, min(limit, 300)))


@router.get("/api/token-usage/session")
def token_usage_session(id: str):
    """Rounds, activity breakdown, heaviest steps, flags, and advice for one session."""
    from diane.native_workers.token_usage import analyze
    try:
        return analyze(Path(resolve_graphs_dir()), id)
    except KeyError:
        raise HTTPException(404, "Session not found; refresh the list")


class TokenUsageClassify(BaseModel):
    id: str


@router.post("/api/token-usage/classify")
def token_usage_classify(body: TokenUsageClassify):
    """Label the session's steps with the decision model. Sends step summaries, never tool output."""
    from diane.native_workers.token_usage import classify
    try:
        return classify(Path(resolve_graphs_dir()), body.id)
    except KeyError:
        raise HTTPException(404, "Session not found; refresh the list")
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))


class TokenUsageSettings(BaseModel):
    model: str = ""


@router.get("/api/token-usage/settings")
def token_usage_settings():
    from diane.native_workers.token_usage import settings
    return settings(Path(resolve_graphs_dir()))


@router.put("/api/token-usage/settings")
def update_token_usage_settings(body: TokenUsageSettings):
    from diane.native_workers.token_usage import write_settings
    return write_settings(Path(resolve_graphs_dir()), body.model)


@router.get("/api/skills")
def skill_pool():
    """Skills installed for any agent CLI and which runtimes Diane shares them with."""
    from diane.native_workers.skills import report
    return report(Path(resolve_graphs_dir()))


class SkillPoolSettings(BaseModel):
    enabled: bool = True
    excluded: list[str] = []


@router.put("/api/skills")
def update_skill_pool(body: SkillPoolSettings):
    from diane.native_workers.skills import build_pool, report, write_settings, RECEIVERS
    base = Path(resolve_graphs_dir())
    write_settings(base, body.enabled, body.excluded)
    for runtime in RECEIVERS:
        build_pool(runtime, base)
    return report(base)


class ProcessStart(BaseModel):
    command: str
    cwd: str
    name: str = ""
    workspace_id: str = ""
    thread_id: str = ""


def _process_call(action):
    try:
        return action()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/processes")
def list_processes(workspace_id: str = "", cwd: str = ""):
    """Long-running processes Diane owns; they outlive agent runs and backend restarts."""
    from diane.processes import manager
    return {"processes": manager().list(workspace_id=workspace_id, cwd=cwd)}


@router.post("/api/processes")
def start_process(body: ProcessStart):
    from diane.processes import manager
    return _process_call(lambda: manager().start(body.command, body.cwd, name=body.name, workspace_id=body.workspace_id, thread_id=body.thread_id))


@router.post("/api/processes/{process_id}/stop")
def stop_process(process_id: str):
    from diane.processes import manager
    return _process_call(lambda: manager().stop(process_id))


@router.get("/api/processes/{process_id}/logs")
def process_logs(process_id: str, tail: int = 64000):
    from diane.processes import manager
    return _process_call(lambda: {"logs": manager().logs(process_id, tail)})


@router.delete("/api/processes/{process_id}")
def remove_process(process_id: str):
    from diane.processes import manager
    _process_call(lambda: manager().remove(process_id))
    return {"removed": process_id}


@router.get("/api/setup")
def setup_status(request: Request):
    from diane.native_workers.models import provider_key
    try:
        _local_provider_settings(request)
        local = True
    except HTTPException:
        local = False
    return {"ready": bool(provider_key('openrouter')), "local": local}


@router.post("/api/setup/openrouter")
async def connect_openrouter(body: ProviderKeyInput, request: Request):
    import httpx
    from diane.native_workers.provider_credentials import save_key
    _local_provider_settings(request)
    key = body.api_key.get_secret_value().strip()
    if not key or len(key) > 4096 or any(char.isspace() for char in key):
        raise HTTPException(400, 'Enter an OpenRouter API key without whitespace')
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get('https://openrouter.ai/api/v1/key', headers={'Authorization': f'Bearer {key}'})
        if response.status_code in {401, 403}:
            raise HTTPException(400, 'OpenRouter did not accept this key. Check it and try again.')
        if response.status_code != 200:
            raise HTTPException(502, 'OpenRouter is unavailable. Try again shortly.')
        data = response.json()['data']
        if data.get('is_management_key') or data.get('is_provisioning_key'):
            raise HTTPException(400, 'Use a model API key, not a management key.')
        if data.get('limit_remaining') is not None and data['limit_remaining'] <= 0:
            raise HTTPException(400, 'This key has reached its spending limit. Add credit or update the limit in OpenRouter.')
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise HTTPException(502, 'Could not verify the key. Check your connection and try again.') from None
    save_key('openrouter', key)
    return {"ready": True, "local": True}
