"""Phone handoff over the existing session, scheduler and native runtimes.

Receipts acknowledge durable admission, independently of execution. A lost HTTP
response can be reconciled without repeating a run or an approval. Uncertain
dispatch after a host restart requires review rather than speculative replay.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from diane._atomic_file import atomic_write_text
from diane.server.chat_request import ChatMessageRequest
from diane.server.chat_store import ChatStore, ChatThread, TaskRunRef
from diane.server.chat_v2 import AgentRunCommand, build_surface_turn_from_chat_request
from diane.server.routers.chat_v2 import (
    AgentRunExecuteRequest, _restart_execution, append_agent_run_command,
    create_agent_run, execute_agent_run,
)
from diane.server.routers.native_workers import WorkerAnswer, answer_lead, answer_worker

router = APIRouter(prefix="/api/phone", tags=["phone"])
_TERMINAL = {"completed", "failed", "blocked", "stopped"}
_ID_PATTERN = r"^[\w.-]{1,160}$"


class PhoneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    workflow_id: str = Field(pattern=_ID_PATTERN)
    thread_id: str = Field(default="", max_length=160)
    action: Literal["send", "steer", "queue", "stop", "answer"] = "send"
    message_id: str = Field(default="", max_length=200)
    text: str = Field(default="", max_length=100000)
    workspace_root: str = Field(default="", max_length=4096)
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    run_id: str = Field(default="", max_length=200)
    worker_id: str | None = Field(default=None, max_length=200)
    approval_request_id: str = Field(default="", max_length=200)
    decision: str = Field(default="", max_length=100)
    answers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_scope(self):
        for value in (self.workflow_id, self.thread_id, self.run_id, self.worker_id or ""):
            if value in {".", ".."} or "/" in value or "\\" in value:
                raise ValueError("Invalid session or run identifier")
        if self.action in {"send", "steer", "queue"} and not (self.text.strip() or self.attachments):
            raise ValueError("A message or attachment is required")
        if self.action != "send" and not (self.thread_id and self.run_id):
            raise ValueError("This action requires a fixed session and run")
        if self.action == "answer" and not self.approval_request_id:
            raise ValueError("An approval request ID is required")
        return self


def _error(code: str, message: str, *, receipt: dict | None = None, status: int = 409):
    raise HTTPException(status, {"code": code, "message": message,
                                 **({"receipt": receipt} if receipt else {})})


def _check_scope(workflow_id: str, thread_id: str) -> None:
    if any(not value or value in {".", ".."} or "/" in value or "\\" in value
           for value in (workflow_id, thread_id)):
        _error("invalid_scope", "Invalid conversation identity", status=422)


def _instance(request: Request) -> str:
    if not hasattr(request.app.state, "phone_instance"):
        request.app.state.phone_instance = uuid4().hex
    return request.app.state.phone_instance


def _receipt_directory(request: Request) -> Path:
    directory = request.app.state.chat_store.base_dir / "phone_requests"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _receipt_path(request: Request, request_id: str) -> Path:
    return _receipt_directory(request) / (hashlib.sha256(request_id.encode()).hexdigest() + ".json")


def _write(request: Request, record: dict) -> None:
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_text(_receipt_path(request, record["request_id"]), json.dumps(record, sort_keys=True), mode=0o600)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _error("receipt_not_found", "This request has not been accepted by this host", status=404)


def _session_runs(request: Request, workflow_id: str, thread_id: str):
    if not thread_id:
        return []
    runs = request.app.state.chat_v2_store.list_run_records(thread_id=thread_id, limit=1000)
    return sorted((run for run in runs if _run_belongs(request, run, workflow_id, thread_id)),
                  key=lambda run: (run.status not in _TERMINAL, run.updated_at), reverse=True)


def _run_belongs(request: Request, run, workflow_id: str, thread_id: str) -> bool:
    if run.thread_id != thread_id:
        return False
    chats = request.app.state.chat_store
    thread = chats.get_thread(workflow_id, thread_id)
    if thread is None:
        return False
    task = request.app.state.chat_v2_store.get_task(run.task_id)
    saved_workflow = (task.metadata.get("workflow_id") if task else "") or run.metadata.get("phone_workflow_id")
    if saved_workflow:
        return saved_workflow == workflow_id
    # Legacy phone sessions used workflow=_scratch, workspace_id=<folder> and
    # did not persist workflow on runs. Their globally generated thread ID is
    # enough only when that ID resolves to exactly one saved conversation.
    matches = list((chats.base_dir / "chats").glob(f"*/{thread_id}.json"))
    if len(matches) == 1 and matches[0].parent.name == workflow_id:
        return True
    return any(message.task_run_ref and message.task_run_ref.run_id == run.run_id for message in thread.messages)


def _resolve_context(request: Request, workflow_id: str, thread_id: str, root_hint: str = "") -> dict:
    store = request.app.state.chat_v2_store
    if _in_plan_mode(request, workflow_id, thread_id):
        return {"ready": False, "status": "setup_needed", "conversation_mode": "plan",
                "reason": "This conversation is in Plan mode. Review or refine its saved plan in Diane before sending work from the phone."}
    runs = _session_runs(request, workflow_id, thread_id)
    if runs:
        # Never fall back to a different agent just because the most recent
        # session configuration is incomplete.
        run = runs[0]
        execute = _restart_execution(store, run)
        if execute and execute.backend:
            return {"ready": True, "source": "session", "workspace_root": run.workspace_root,
                    "workspace_id": run.workspace_id or workflow_id,
                    "execute": execute.model_copy(update={"background": True}).model_dump(mode="json")}
        return {"ready": False, "status": "setup_needed", "reason": "Open this session in Diane and select its agent and account before continuing."}
    path = request.app.state.chat_store.base_dir / "sticky_todo" / "defaults.json"
    try:
        from diane.server.routers.sticky_todo import Defaults
        defaults = Defaults.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        defaults = None
    if defaults:
        project = next((p for p in defaults.projects if p.id == workflow_id), None)
        root = str(Path(root_hint).expanduser().resolve()) if root_hint else ""
        if project is None and root:
            project = next((p for p in defaults.projects if p.root and str(Path(p.root).expanduser().resolve()) == root), None)
        if project and project.root and (not root or str(Path(project.root).expanduser().resolve()) == root):
            if defaults.execute.backend and Path(project.root).is_dir():
                return {"ready": True, "source": "desktop_defaults", "workspace_root": project.root,
                        "workspace_id": project.id,
                        "execute": defaults.execute.model_copy(update={"background": True}).model_dump(mode="json")}
    return {"ready": False, "status": "setup_needed", "reason": "Open Diane on this host, select this project folder and agent/account, then retry."}


def _in_plan_mode(request: Request, workflow_id: str, thread_id: str) -> bool:
    if not thread_id or request.app.state.chat_store.get_thread(workflow_id, thread_id) is None:
        return False
    from diane.server.plan_mode import PlanStore
    return PlanStore(request.app.state.chat_store).read(workflow_id, thread_id).get("mode") == "plan"


def _public_context(context: dict) -> dict:
    execute = context.get("execute") or {}
    policy = execute.get("profile_policy") or {}
    lead = policy.get("lead_profile") or {}
    account = str(lead.get("account") or "")
    # The phone caches this projection. Native launch profiles can contain home
    # paths, provider URLs and credentials, so publish display fields only.
    account_label = account if account in {"app", "personal", "default", "terminal"} else ("Configured account" if account else "")
    return {key: value for key, value in context.items() if key != "execute"} | {
        "backend": execute.get("backend", ""), "model": str(lead.get("model") or ""),
        "account_label": account_label, "permission_mode": str(policy.get("permission_mode") or ""),
        "agent_name": str(lead.get("name") or execute.get("backend") or ""),
    }


def _pending_approvals(run_id: str) -> list[dict]:
    from diane.native_workers.service import active_teams
    team = active_teams.get(run_id)
    if not team:
        return []
    pending = []
    lead = getattr(team, "lead_team", None)
    clients = [(None, client) for client in getattr(lead, "steering", {}).values()]
    clients.extend(getattr(team, "steering", {}).items())
    for worker_id, client in clients:
        for identity, raw in dict(getattr(client, "requests", {})).items():
            pending.append({"run_id": run_id, "worker_id": worker_id, "request_id": str(identity),
                            "id": str(identity), "method": raw.get("method", ""), "params": raw.get("params") or {}})
    return pending


def _final_text(run) -> str:
    result = run.metadata.get("backend_result") or {}
    raw = result.get("raw_result") or {}
    return str(raw.get("final_text") or raw.get("final_response") or result.get("final_text") or result.get("summary") or "")


def _persist_result(request: Request, workflow_id: str, thread_id: str, run) -> None:
    if run is None or run.status not in _TERMINAL:
        return
    # A native completed event can precede the final persisted backend result.
    # Do not freeze its short progress summary as the conversation's answer.
    text = _final_text(run)
    if not text:
        return
    chats = request.app.state.chat_store
    thread = chats.get_thread(workflow_id, thread_id)
    if thread is None or any(message.role == "assistant" and message.content == text and message.task_run_ref
                             and message.task_run_ref.run_id == run.run_id and message.task_run_ref.status == run.status
                             for message in thread.messages):
        return
    if text:
        # Native retries can reuse a run ID. Distinct durable results get a
        # distinct stable message rather than overwriting a previous answer.
        result_id = hashlib.sha256((run.status + "\n" + text).encode()).hexdigest()[:16]
        chats.merge_messages(workflow_id, thread_id, [{"id": "phone-result-" + run.run_id + "-" + result_id,
            "role": "assistant", "content": text, "timestamp": run.updated_at,
            "task_run_ref": TaskRunRef(task_id=run.task_id, run_id=run.run_id, status=run.status,
                workspace_root=run.workspace_root, workspace_id=run.workspace_id).model_dump(),
            "token_usage": run.token_usage or None}])


def _reconcile(request: Request, record: dict) -> None:
    """Recover a receipt from durable run/queue evidence, never replay IO."""
    store = request.app.state.chat_v2_store
    previous = dict(record)
    if not record.get("run_id") and record.get("surface_turn_id"):
        matching = next((run for run in _session_runs(request, record["workflow_id"], record["thread_id"])
                         if run.surface_turn_id == record["surface_turn_id"]), None)
        if matching:
            record.update(run_id=matching.run_id, task_id=matching.task_id)
    run = store.get_run(record.get("run_id") or "") if record.get("run_id") else None
    if record["phase"] not in {"submitted", "rejected"} and (
        record.get("instance") != _instance(request) or record["phase"] == "needs_review"
    ):
        task = store.get_task(record.get("task_id") or "") if record.get("task_id") else None
        item = next((item for item in task.queue_items if item.dedup_key == record["request_id"]), None) if task else None
        if item:
            record.update(queue_item_id=item.id, phase="submitted")
        elif record["action"] == "stop" and run and run.metadata.get("stop_surface_turn_id") == record["request_id"]:
            record["phase"] = "submitted"
        elif (record["action"] == "send" and run and record.get("surface_turn_id") == run.surface_turn_id
              and (run.status != "queued" or run.metadata.get("background_started_reason"))):
            record["phase"] = "submitted"
        else:
            record.update(phase="needs_review", error_code="receipt_needs_review",
                error="Dispatch was interrupted after this host accepted the request. Review its saved session before sending it again.")
        if record["phase"] == "submitted":
            record.pop("error_code", None)
            record.pop("error", None)
        if record != previous:
            _write(request, record)


def _receipt(request: Request, record: dict, *, replayed: bool = False) -> dict:
    _reconcile(request, record)
    store = request.app.state.chat_v2_store
    run = store.get_run(record.get("run_id") or "") if record.get("run_id") else None
    task = store.get_task(record.get("task_id") or "") if record.get("task_id") else None
    item = next((item for item in task.queue_items if item.id == record.get("queue_item_id")), None) if task else None
    if item and item.metadata.get("delivered_run_id"):
        run = store.get_run(item.metadata["delivered_run_id"]) or run
    status = run.status if run else record.get("status", "queued")
    summary = run.latest_summary if run else record.get("summary", "Request accepted by this host")
    if item and item.status in {"queued", "completed", "cancelled"}:
        status = {"queued": "queued", "completed": run.status if run else "completed", "cancelled": "stopped"}[item.status]
        summary = f"Message {item.status.replace('cancelled', 'withdrawn')}"
    if record["action"] == "answer" and record["phase"] == "submitted":
        status, summary = "completed", "Decision delivered"
    if record["phase"] in {"needs_review", "rejected"}:
        status = "needs_review" if record["phase"] == "needs_review" else record.get("status", "failed")
        summary = record.get("error", "Review this request in Diane")
    _persist_result(request, record["workflow_id"], record["thread_id"], run)
    delivery = record["phase"] if record["phase"] in {"needs_review", "rejected"} else "accepted"
    return {"request_id": record["request_id"], "action": record["action"], "delivery_status": delivery,
        "status": status, "phase": record["phase"], "summary": summary,
        "workflow_id": record["workflow_id"], "thread_id": record["thread_id"],
        "run_id": run.run_id if run else record.get("run_id", ""), "target_run_id": record.get("run_id", ""),
        "task_id": record.get("task_id", ""), "queue_item_id": record.get("queue_item_id", ""),
        "queue_status": item.status if item else "", "workspace_root": record["context"].get("workspace_root", ""),
        "execution_context": _public_context(record["context"]), "replayed": replayed,
        "stopping": bool(record["action"] == "stop" and run and run.status not in _TERMINAL and run.metadata.get("stop_requested")),
        "error_code": record.get("error_code", ""), "created_at": record["created_at"], "updated_at": record["updated_at"]}


def _chat_request(request: Request, record: dict, body: PhoneRequest) -> ChatMessageRequest:
    thread = request.app.state.chat_store.get_thread(record["workflow_id"], record["thread_id"])
    return ChatMessageRequest(workflow_id=record["workflow_id"], thread_id=record["thread_id"],
        message=body.text, mode="agent", surface_type="frontend", surface_id="chunk-workspace",
        history=[{"role": message.role, "content": message.content} for message in thread.messages
                 if message.id != record["message_id"] and message.role in {"user", "assistant"}],
        surface_context={"workspace_root": record["context"].get("workspace_root", ""),
            "workspace_id": record["context"].get("workspace_id", record["workflow_id"]),
            "workspace_mode": "work", "source_surface": "phone", "appended_attachments": body.attachments,
            "native": {"message_id": body.request_id}})


@router.post("/requests")
async def submit_request(body: PhoneRequest, request: Request) -> dict:
    if not hasattr(request.app.state, "phone_lock"):
        request.app.state.phone_lock = asyncio.Lock()
    async with request.app.state.phone_lock:
        path = _receipt_path(request, body.request_id)
        fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if path.exists():
            record = _read(path)
            if record["fingerprint"] != fingerprint:
                _error("request_conflict", "This request ID already belongs to a different request")
            receipt = _receipt(request, record, replayed=True)
            if record["phase"] in {"needs_review", "rejected"}:
                _error(record.get("error_code", "receipt_needs_review"), receipt["summary"], receipt=receipt)
            return receipt
        chats, store = request.app.state.chat_store, request.app.state.chat_v2_store
        thread_id = body.thread_id or "phone-" + hashlib.sha256(body.request_id.encode()).hexdigest()[:24]
        if body.thread_id and chats.get_thread(body.workflow_id, thread_id) is None:
            _error("session_not_found", "This conversation no longer exists on this host", status=404)
        context = _resolve_context(request, body.workflow_id, body.thread_id, body.workspace_root)
        target = store.get_run(body.run_id) if body.run_id else None
        if body.action == "send" and not target:
            target = next((run for run in _session_runs(request, body.workflow_id, body.thread_id)
                           if run.status not in _TERMINAL), None)
        if body.run_id:
            if target is None or not _run_belongs(request, target, body.workflow_id, thread_id):
                _error("scope_mismatch", "This run belongs to a different conversation")
            if body.action in {"send", "steer", "queue", "stop"}:
                task = store.get_task(target.task_id)
                if target.status in _TERMINAL or not task or task.active_run_id != target.run_id:
                    _error("run_not_active", "This run has finished or been replaced; refresh the conversation")
            execute = _restart_execution(store, target)
            context = {"ready": bool(execute and execute.backend), "source": "session", "workspace_root": target.workspace_root,
                "workspace_id": target.workspace_id or body.workflow_id,
                "execute": execute.model_copy(update={"background": True}).model_dump(mode="json") if execute else {}}
        if body.action in {"send", "steer", "queue"} and not context["ready"]:
            code = "plan_mode_requires_desktop" if context.get("conversation_mode") == "plan" else "execution_context_unavailable"
            _error(code, context.get("reason", "Open this session in Diane and select its agent/account first"))
        if body.action in {"send", "steer", "queue"} and _in_plan_mode(request, body.workflow_id, thread_id):
            _error("plan_mode_requires_desktop", "This conversation is in Plan mode. Review or refine its saved plan in Diane before sending work from the phone.")
        if body.action == "answer" and not any(row["request_id"] == body.approval_request_id and row["worker_id"] == body.worker_id
                                               for row in _pending_approvals(body.run_id)):
            _error("approval_expired", "This decision is no longer pending; refresh the conversation")
        if body.action == "answer" and body.decision == "accept" and _in_plan_mode(request, body.workflow_id, thread_id):
            pending = next(row for row in _pending_approvals(body.run_id)
                           if row["request_id"] == body.approval_request_id and row["worker_id"] == body.worker_id)
            if pending["method"] != "item/tool/requestUserInput" and pending["params"].get("tool_name") != "AskUserQuestion":
                _error("plan_mode_requires_desktop", "Plan mode cannot approve additional tool access. Review the session in Diane.")
        now = datetime.now(timezone.utc).isoformat()
        record = {"request_id": body.request_id, "fingerprint": fingerprint, "body": body.model_dump(mode="json"),
            "action": body.action, "workflow_id": body.workflow_id, "thread_id": thread_id,
            "message_id": body.message_id or "phone-user-" + hashlib.sha256(body.request_id.encode()).hexdigest()[:24],
            "phase": "prepared", "context": context, "instance": _instance(request), "created_at": now,
            "run_id": target.run_id if target else "", "task_id": target.task_id if target else ""}
        _write(request, record)
        try:
            if not body.thread_id and chats.get_thread(body.workflow_id, thread_id) is None:
                chats.save_thread(ChatThread(id=thread_id, workflow_id=body.workflow_id))
                chats.set_thread_meta(body.workflow_id, thread_id, {"mode": "agent", "workspace_root": context["workspace_root"],
                                                                  "workspace_id": context["workspace_id"]})
            if body.action in {"send", "steer", "queue"}:
                chats.merge_messages(body.workflow_id, thread_id, [{"id": record["message_id"], "role": "user",
                    "content": body.text, "attachments": body.attachments}])
            if body.action == "send" and not target:
                chat = _chat_request(request, record, body)
                record.update(phase="admitting", surface_turn_id=build_surface_turn_from_chat_request(chat).id)
                _write(request, record)
                created = await create_agent_run(request=request, req=chat)
                ref = created.get("task_run_ref") or {}
                admission = created.get("admission") or {}
                record.update(run_id=ref.get("run_id") or admission.get("run_id") or "",
                              task_id=ref.get("task_id") or admission.get("task_id") or "",
                              queue_item_id=admission.get("queue_item_id") or "", phase="admitted")
                record["summary"] = (created.get("event") or {}).get("summary") or admission.get("reason", "")
                _write(request, record)
                if admission.get("action") == "start_parallel" and record["run_id"]:
                    record["phase"] = "dispatching"
                    _write(request, record)
                    await execute_agent_run(record["run_id"], execute=AgentRunExecuteRequest.model_validate(context["execute"]), request=request)
                elif not record["run_id"]:
                    record["status"] = "needs_input"
            elif body.action in {"send", "steer", "queue", "stop"}:
                record["phase"] = "dispatching"
                _write(request, record)
                command = {"send": "continue_after_current", "steer": "append_followup", "queue": "continue_after_current", "stop": "stop"}[body.action]
                result = await append_agent_run_command(target.run_id, AgentRunCommand(command=command,
                    run_id=target.run_id, task_id=target.task_id, idempotency_key=body.request_id, surface_turn_id=body.request_id,
                    payload={"text": body.text, "attachments": body.attachments, "client_message_id": record["message_id"],
                             "source_surface": "phone"}), request=request)
                record["queue_item_id"] = (result.get("event") or {}).get("payload", {}).get("queue_item_id", "")
            else:
                record["phase"] = "dispatching"
                _write(request, record)
                answer = WorkerAnswer(request_id=body.approval_request_id, decision=body.decision, answers=body.answers)
                if body.worker_id:
                    await answer_worker(body.run_id, body.worker_id, answer)
                else:
                    await answer_lead(body.run_id, answer)
            record["phase"] = "submitted"
            _write(request, record)
        except HTTPException as exc:
            pending = any(row["request_id"] == body.approval_request_id and row["worker_id"] == body.worker_id
                          for row in _pending_approvals(body.run_id)) if body.action == "answer" else False
            code = ("invalid_answer" if pending else "approval_expired") if body.action == "answer" else "request_rejected"
            record.update(phase="rejected", error_code=code,
                          error=str(exc.detail), status="failed")
            _write(request, record)
            _error(record["error_code"], record["error"], receipt=_receipt(request, record), status=exc.status_code)
        except ValueError as exc:
            record.update(phase="rejected", error_code="request_conflict", error=str(exc), status="failed")
            _write(request, record)
            _error("request_conflict", str(exc), receipt=_receipt(request, record))
        except (Exception, asyncio.CancelledError):
            record.update(phase="needs_review", error_code="receipt_needs_review",
                          error="The host accepted this request but could not confirm dispatch. Review its session before retrying.")
            _write(request, record)
            raise
        return _receipt(request, record)


@router.get("/requests/{request_id}")
async def get_receipt(request_id: str, request: Request) -> dict:
    return _receipt(request, _read(_receipt_path(request, request_id)), replayed=True)


@router.get("/sessions/{workflow_id}/{thread_id}/execution-context")
async def execution_context(workflow_id: str, thread_id: str, request: Request, workspace_root: str = "") -> dict:
    _check_scope(workflow_id, thread_id)
    return _public_context(_resolve_context(request, workflow_id, thread_id, workspace_root))


@router.get("/sessions/{workflow_id}/{thread_id}/state")
async def session_state(workflow_id: str, thread_id: str, request: Request) -> dict:
    _check_scope(workflow_id, thread_id)
    chats, store = request.app.state.chat_store, request.app.state.chat_v2_store
    thread = chats.get_thread(workflow_id, thread_id)
    if thread is None:
        _error("session_not_found", "This conversation no longer exists on this host", status=404)
    runs = _session_runs(request, workflow_id, thread_id)
    for run in reversed(runs[:20]):
        _persist_result(request, workflow_id, thread_id, run)
    thread = chats.get_thread(workflow_id, thread_id)
    run = runs[0] if runs else None
    task = store.get_task(run.task_id) if run else None
    records = []
    for path in _receipt_directory(request).glob("*.json"):
        record = _read(path)
        if record["workflow_id"] == workflow_id and record["thread_id"] == thread_id:
            records.append(record)
    records.sort(key=lambda record: record["created_at"], reverse=True)
    messages = [message.model_dump(mode="json", exclude={"run_events", "tool_calls"}) for message in thread.messages]
    return {"thread": {**thread.model_dump(mode="json", exclude={"messages"}), "messages": messages,
                       "revision": ChatStore.revision(thread)},
        "run": {key: getattr(run, key) for key in ("run_id", "task_id", "thread_id", "workspace_root", "workspace_id", "status", "latest_summary", "updated_at")} if run else None,
        "task": {"task_id": task.task_id, "status": task.status, "latest_progress": task.latest_progress,
                 "blocker": task.blocker, "artifacts": task.latest_artifact_refs,
                 "queue_items": [{"id": item.id, "lane": item.lane, "status": item.status, "position": item.position,
                                  "text": item.text[:500]} for item in task.queue_items if item.status in {"queued", "injected"}]} if task else None,
        "execution_context": _public_context(_resolve_context(request, workflow_id, thread_id)),
        "pending_approvals": [row for current in runs if current.status not in _TERMINAL for row in _pending_approvals(current.run_id)],
        "requests": [_receipt(request, record) for record in records[:50]], "host": {"status": "online"}}
