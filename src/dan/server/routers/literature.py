"""Staged batch imports, independent of the current project or chat."""
import hashlib
import os
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from dan.server import literature as intake, paper_library as library
from dan.server.routers.chat_v2 import AgentRunExecuteRequest

router = APIRouter(prefix="/api/literature", tags=["literature"])


def runner(request):
    if not hasattr(request.app.state, "literature_runner"):
        request.app.state.literature_runner = intake.ImportRunner(request.app)
    return request.app.state.literature_runner


class NewBatch(BaseModel):
    destination: str = Field(min_length=1, max_length=4096)
    bibtex: str = Field(default="", max_length=500000)


@router.get("/batches")
def list_batches():
    with library.LOCK:
        return [{"id": b["id"], "created": b["created"], "destination": b["destination"],
                 "count": len(b["items"]), "active": sum(i["status"] in intake.ACTIVE for i in b["items"])} for b in intake.batches()]


@router.post("/batches")
def new_batch(body: NewBatch):
    root = intake.destination(body.destination)
    try: intake.bib_entries(body.bibtex)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    batch = {"id": uuid.uuid4().hex, "created": intake.now(), "destination": str(root), "bibtex": body.bibtex, "items": [], "execution": {}}
    with library.LOCK: intake.save(batch)
    return batch


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str):
    with library.LOCK: return intake.get_batch(batch_id)


@router.post("/batches/{batch_id}/pdf")
async def upload(batch_id: str, request: Request, name: str, scope: Literal["paper", "book_overview"] = "paper"):
    name = Path(name.replace("\\", "/")).name
    if not name.lower().endswith(".pdf") or len(name) > 255:
        raise HTTPException(422, "Choose a PDF with a filename under 256 characters")
    with library.LOCK:
        batch = intake.get_batch(batch_id)
        if len(batch["items"]) >= 100: raise HTTPException(413, "Use at most 100 PDFs per batch")
    identifier = uuid.uuid4().hex
    path = intake.directory(batch_id) / (identifier + ".pdf")
    temporary = path.with_suffix(".upload")
    size, digest = 0, hashlib.sha256()
    try:
        with temporary.open("xb") as output:
            os.chmod(temporary, 0o600)
            async for chunk in request.stream():
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise HTTPException(413, "Each PDF must be under 100 MB")
                output.write(chunk)
                digest.update(chunk)
        with temporary.open("rb") as handle:
            if b"%PDF-" not in handle.read(1024):
                raise HTTPException(422, "This file does not contain a PDF header")
        with library.LOCK:
            batch = intake.get_batch(batch_id)
            if len(batch["items"]) >= 100 or sum(i["size"] for i in batch["items"]) + size > 1024**3:
                raise HTTPException(413, "Batch limit: 100 PDFs or 1 GB")
            if any(i["sha256"] == digest.hexdigest() for i in batch["items"]):
                raise HTTPException(409, "This PDF is already in this batch")
            temporary.rename(path)
            batch["items"].append({"id": identifier, "name": name, "staged_path": str(path.resolve()), "size": size, "sha256": digest.hexdigest(), "scope": scope,
                "status": "staged", "error": "", "manual_bibtex": "", "query": "", "candidates": []})
            try: intake.save(batch)
            except Exception:
                path.unlink(missing_ok=True)
                raise
        return batch
    finally:
        temporary.unlink(missing_ok=True)


class Start(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)
    execution: AgentRunExecuteRequest


@router.post("/batches/{batch_id}/prepare")
async def prepare(batch_id: str, body: Start, request: Request):
    with library.LOCK:
        batch = intake.get_batch(batch_id)
        items = [intake.item_at(batch, identifier) for identifier in body.ids]
        if any(i["status"] in intake.ACTIVE | {"imported", "importing"} for i in items):
            raise HTTPException(409, "These documents are already running or imported")
        # Execution is fixed for queued documents; an account change must not alter work already queued.
        if any(i["status"] in intake.ACTIVE for i in batch["items"]):
            raise HTTPException(409, "Wait for this batch to finish preparation before starting another selection")
        batch["paused"] = False
        batch["execution"] = body.execution.model_dump(mode="json")
        for item in items:
            item.update(status="queued", error="")
            for field in ("draft", "bibtex", "key", "run_id", "existing_paper_id"):
                item.pop(field, None)
        intake.save(batch)
    runner(request).start()
    return batch


class EditItem(BaseModel):
    bibtex: str = Field(default="", max_length=50000)
    query: str = Field(default="", max_length=500)
    scope: Literal["paper", "book_overview"] = "paper"


@router.put("/batches/{batch_id}/items/{item_id}")
def edit_item(batch_id: str, item_id: str, body: EditItem):
    try:
        if body.bibtex and len(intake.bib_entries(body.bibtex)) != 1:
            raise ValueError("Supply one complete citation for this PDF")
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    with library.LOCK:
        batch = intake.get_batch(batch_id)
        item = intake.item_at(batch, item_id)
        if item["status"] in intake.ACTIVE | {"imported", "importing"}:
            raise HTTPException(409, "Wait for preparation to finish before editing this document")
        item.update(manual_bibtex=body.bibtex, query=body.query, scope=body.scope, status="staged", error="")
        for field in ("draft", "bibtex", "key", "run_id", "existing_paper_id"): item.pop(field, None)
        intake.save(batch)
        return batch


@router.delete("/batches/{batch_id}/items/{item_id}")
def remove_item(batch_id: str, item_id: str):
    with library.LOCK:
        batch = intake.get_batch(batch_id)
        item = intake.item_at(batch, item_id)
        if item["status"] in intake.ACTIVE | {"importing"}:
            raise HTTPException(409, "Wait for preparation to finish before removing this document")
        batch["items"].remove(item)
        intake.save(batch)
        (intake.directory(batch_id) / (item_id + ".pdf")).unlink(missing_ok=True)
        return batch


@router.get("/batches/{batch_id}/items/{item_id}/pdf")
def pdf(batch_id: str, item_id: str):
    with library.LOCK:
        item = intake.item_at(intake.get_batch(batch_id), item_id)
        path = intake.directory(batch_id) / (item["id"] + ".pdf")
        if not path.is_file(): raise HTTPException(404, "Staged PDF is unavailable")
        return FileResponse(path, media_type="application/pdf", filename=item["name"], content_disposition_type="inline")


class Apply(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


@router.post("/batches/{batch_id}/apply")
def apply(batch_id: str, body: Apply):
    return intake.apply_ready(batch_id, body.ids)


@router.post("/batches/{batch_id}/stop")
def stop(batch_id: str, request: Request):
    from dan.server.chat_v2 import AgentRunCommand
    with library.LOCK:
        batch = intake.get_batch(batch_id)
        batch["paused"] = True
        for item in batch["items"]:
            if item["status"] == "queued":
                item["status"] = "staged"
            elif item["status"] in {"matching", "reading"}:
                item["status"] = "stopping"
                if item.get("run_id"):
                    store = request.app.state.chat_v2_store
                    run = store.get_run(item["run_id"])
                    if run:
                        store.record_agent_command(AgentRunCommand(command="stop", run_id=run.run_id, task_id=run.task_id))
        intake.save(batch)
        return batch
