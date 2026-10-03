"""Machine profiles are managed only by the local bootstrap instance."""
import asyncio
import os
import secrets
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from diane.remote import profiles

router = APIRouter(prefix="/api/remote")
jobs: dict[str, dict] = {}
tasks: set[asyncio.Task] = set()


def local(request):
    from urllib.parse import urlsplit
    if os.environ.get("DAN_REMOTE_CONFIG") or not request.client or request.client.host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(403, "SSH setup is available on the local Dear Diane app")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).hostname not in ("localhost", "127.0.0.1", "::1"):
        raise HTTPException(403, "SSH setup requires a local app origin")
    if request.url.hostname not in ("localhost", "127.0.0.1", "::1", "testserver"):
        raise HTTPException(403, "SSH setup requires a local app address")


@router.get("/connections")
async def connections(request: Request):
    if os.environ.get("DAN_REMOTE_CONFIG"):
        from diane.remote.access import access_config
        config = access_config()
        return {"local": False, "machine": config["id"], "url": config["url"], "connections": []}
    local(request)
    return {"local": True, "connections": [profiles.public_profile(p) for p in profiles.read_profiles().values()]}


@router.put("/connections/{profile_id}")
async def save(profile_id: str, profile: profiles.Profile, request: Request):
    local(request)
    if profile_id != profile.id:
        raise HTTPException(400, "Machine ID does not match")
    if request.headers.get("if-none-match") == "*" and profile_id in profiles.read_profiles():
        raise HTTPException(409, "A connection with this ID already exists. Refresh connections and try again.")
    if any(job["machine"] == profile_id and job["status"] == "running" for job in jobs.values()):
        raise HTTPException(409, "Wait for this installation to finish")
    try:
        return profiles.save_profile(profile)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/ssh-hosts")
async def ssh_hosts(request: Request):
    local(request)
    return {"hosts": await asyncio.to_thread(profiles.ssh_hosts)}


def saved(profile_id):
    value = profiles.read_profiles().get(profile_id)
    if not value:
        raise HTTPException(404, "Connection not found")
    return value


@router.post("/connections/{profile_id}/inspect")
async def inspect(profile_id: str, request: Request):
    local(request)
    try:
        return await profiles.inspect(profiles.Profile.model_validate(saved(profile_id)))
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/connections/{profile_id}/access-key")
async def access_key(profile_id: str, request: Request):
    local(request)
    from fastapi.responses import JSONResponse
    return JSONResponse({"access_key": saved(profile_id)["access_key"]}, headers={"Cache-Control": "no-store"})


class InstallRequest(BaseModel):
    share_openrouter: bool = False


@router.post("/connections/{profile_id}/install", status_code=202)
async def install(profile_id: str, body: InstallRequest, request: Request):
    local(request)
    value = saved(profile_id)
    if not value.get("relay_enabled", True):
        raise HTTPException(409, "Set up phone access before installing the persistent remote service")
    if any(job["status"] == "running" for job in jobs.values()):
        raise HTTPException(409, "Another installation is running")
    job_id = secrets.token_hex(12)
    job = jobs[job_id] = {"id": job_id, "machine": profile_id, "status": "running", "message": "Starting installation"}

    async def work():
        try:
            result = await profiles.deploy(value, share_openrouter=body.share_openrouter, progress=lambda message: job.update(message=message))
            job.update(status="complete", message="Connected. Open the remote workspace to continue.", result=result)
        except Exception as exc:
            job.update(status="failed", message=str(exc).replace(value["access_key"], "[redacted]"))
    task = asyncio.create_task(work())
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return job


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, request: Request):
    local(request)
    if job_id not in jobs:
        raise HTTPException(404, "Installer restarted; check the host before retrying")
    return jobs[job_id]
