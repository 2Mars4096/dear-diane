"""Opt-in personal capture/review. Authentication is the deployment boundary."""
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from dan.personal.intake import MAX_FILE_BYTES

from dan.personal.calendar import export_ics
from dan.personal.models import CaptureInput, ReviewInput
from dan.personal.store import Conflict, PersonalStore
from dan.personal.extraction_queue import ExtractionQueue
from dan.personal.runner import extraction_model
from dan.personal.reminders import Reminders, ReminderRequest
from dan.personal.lifecycle import TaskDecision, decide
from dan.personal.budgets import status as budget_status, BudgetSettings, update_settings
from pydantic import BaseModel, ConfigDict, Field
from dan.server.paths import resolve_graphs_dir

router = APIRouter(prefix="/api/personal")
OWNER = "operator"  # One operator per deployment; clients cannot select an owner.


def enabled():
    return os.environ.get("DAN_PERSONAL_ENABLED", "0").lower() in {"1", "true", "yes"}


def store(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    if not enabled():
        raise HTTPException(404, "Personal commitments are not enabled on this host")
    return PersonalStore(Path(resolve_graphs_dir()) / "personal")


def run(operation):
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(404, "Commitment not found") from exc
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/capabilities')
def capabilities(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return {"enabled": enabled(), "capture": ["paste", "pdf", "eml", "txt", "png", "jpeg"], "extraction": "worker" if extraction_model() else "manual", "model": extraction_model(), "calendar": "download",
            "reminders": "inbox", "connections": False}


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str = Field(min_length=8, max_length=100)
    expected_revision: int = Field(ge=1)


class TranscriptionRequest(ExtractionRequest):
    text: str = Field(min_length=1, max_length=65536)


@router.post('/sources/{source_id}/transcription')
def transcription(source_id: str, body: TranscriptionRequest, db: PersonalStore = Depends(store)):
    return run(lambda: db.transcribe_source(OWNER, source_id, body.operation_id, body.expected_revision, body.text))


@router.get('/sources')
def source_list(db: PersonalStore = Depends(store)):
    return db.list_sources(OWNER)


@router.delete('/sources/{source_id}')
def delete_source(source_id: str, body: ExtractionRequest, db: PersonalStore = Depends(store)):
    return run(lambda: db.delete_original(OWNER, source_id, body.operation_id, body.expected_revision))


@router.get('/commitments/{record_id}/sources/{source_id}/preview')
def source_preview(record_id: str, source_id: str, db: PersonalStore = Depends(store)):
    content, name = run(lambda: db.original_source(OWNER, record_id, source_id))
    suffix = Path(name).suffix.lower()
    if suffix not in {'.png', '.jpg', '.jpeg'}:
        raise HTTPException(422, 'Only image sources have an inline preview')
    return Response(content, media_type='image/png' if suffix == '.png' else 'image/jpeg', headers={
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'"})


@router.post('/commitments/{record_id}/reminder')
def reminder(record_id: str, body: ReminderRequest, db: PersonalStore = Depends(store)):
    return run(lambda: Reminders(db).change(OWNER, record_id, body))


@router.patch('/budget')
def change_budget(body: BudgetSettings, db: PersonalStore = Depends(store)):
    return run(lambda: update_settings(db, OWNER, body))


@router.get('/budget')
def budget(db: PersonalStore = Depends(store)):
    return run(lambda: budget_status(db, OWNER))


@router.get('/notifications')
def notifications(db: PersonalStore = Depends(store)):
    return Reminders(db).inbox(OWNER)


@router.post('/notifications/{notification_id}/read')
def read_notification(notification_id: str, body: ExtractionRequest, db: PersonalStore = Depends(store)):
    return run(lambda: Reminders(db).acknowledge(OWNER, notification_id, body.operation_id, body.expected_revision))


@router.post('/commitments/{record_id}/extract')
def extract(record_id: str, body: ExtractionRequest, db: PersonalStore = Depends(store)):
    model = extraction_model()
    if not model:
        raise HTTPException(409, 'Automatic extraction is not configured on this host')
    return run(lambda: ExtractionQueue(db).enqueue(OWNER, record_id, body.operation_id, body.expected_revision, model))


@router.post('/commitments/{record_id}/extract/stop')
def stop_extraction(record_id: str, body: ExtractionRequest, db: PersonalStore = Depends(store)):
    return run(lambda: ExtractionQueue(db).stop(OWNER, record_id, body.operation_id, body.expected_revision))


@router.get('/commitments')
def snapshot(db: PersonalStore = Depends(store)):
    return db.snapshot(OWNER)


@router.get('/events')
def events(after: int = Query(default=0, ge=0), db: PersonalStore = Depends(store)):
    return db.events(OWNER, after)


@router.post('/commitments/{record_id}/decision')
def task_decision(record_id: str, body: TaskDecision, db: PersonalStore = Depends(store)):
    return run(lambda: decide(db, OWNER, record_id, body))


@router.post('/captures')
def capture(body: CaptureInput, db: PersonalStore = Depends(store)):
    return run(lambda: db.capture(OWNER, body))


@router.post('/sources')
async def source_upload(request: Request, name: str = Query(min_length=1, max_length=1000), operation_id: str = Query(min_length=8, max_length=100), db: PersonalStore = Depends(store)):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_FILE_BYTES:
            raise HTTPException(413, 'Each file must be no larger than 10 MiB')
        data.extend(chunk)
    return await run_in_threadpool(lambda: run(lambda: db.add_source(OWNER, operation_id, name, bytes(data))))


@router.get('/commitments/{record_id}/sources/{source_id}')
def original_source(record_id: str, source_id: str, db: PersonalStore = Depends(store)):
    content, name = run(lambda: db.original_source(OWNER, record_id, source_id))
    return Response(content, media_type='application/octet-stream', headers={
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Disposition': "attachment; filename=\"diane-source\"; filename*=UTF-8''" + quote(name, safe='')})


@router.get('/commitments/{record_id}')
def detail(record_id: str, db: PersonalStore = Depends(store)):
    return run(lambda: db.detail(OWNER, record_id))


@router.patch('/commitments/{record_id}')
def review(record_id: str, body: ReviewInput, db: PersonalStore = Depends(store)):
    return run(lambda: db.review(OWNER, record_id, body))


@router.get('/commitments/{record_id}/calendar.ics')
def calendar(record_id: str, db: PersonalStore = Depends(store)):
    content = run(lambda: export_ics(db.detail(OWNER, record_id)["commitment"]))
    return Response(content, media_type="text/calendar; charset=utf-8", headers={
        "Content-Disposition": 'attachment; filename="diane-commitment.ics"', "Cache-Control": "no-store"})


# Conversation stays on the same operator/authentication boundary as records.
from dan.personal.conversation import Conversation, ChatInput


@router.get('/conversation')
def conversation(db: PersonalStore = Depends(store)):
    turns = [{**{key: value for key, value in turn.items() if key in {'id','text','reply','state','created_at','record_id','calendar_url'}}, 'sources': [{'id': source['id'], 'name': source['name']} for source in turn.get('sources', [])]} for turn in Conversation(db).history(OWNER)]
    return {'turns': turns, 'model': extraction_model(), 'unread': sum(not item['read'] for item in Reminders(db).inbox(OWNER)['notifications'])}


@router.post('/conversation')
def converse(body: ChatInput, db: PersonalStore = Depends(store)):
    return run(lambda: Conversation(db).submit(OWNER, body, extraction_model()))


@router.post('/conversation/{turn_id}/stop')
def stop_conversation(turn_id: str, db: PersonalStore = Depends(store)):
    run(lambda: Conversation(db).stop(OWNER, turn_id))
    return {'ok': True}
