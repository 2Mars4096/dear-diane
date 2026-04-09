"""Minimal standalone runner for the hardened worker core."""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from typing import Any, Awaitable, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from dan.worker.core.capabilities import CapabilityManifest, capability_manifest_from_descriptor
from dan.worker.core.contracts import ContinuationPayload, ExecutionRequest, OutputContract
from dan.worker.core.executor import WorkerCoreExecutor, WorkerExecutionResult
from dan.worker.core.interfaces import (
    AcquisitionProvider,
    CompletionProvider,
    MemoryProvider,
    ToolProvider,
)
from dan.worker.core.model import WorkerDefinition

RunnerStatus = Literal["completed", "failed", "timed_out", "budget_exhausted"]
AttemptStatus = Literal["completed", "failed", "timed_out", "exception"]
MailboxMessageStatus = Literal["queued", "running", "completed", "failed"]
BackgroundTaskStatus = Literal["running", "completed", "failed", "cancelled"]
DurableAgentStatus = Literal["idle", "running", "closed"]


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class StandaloneRunPolicy(BaseModel):
    """Retry, timeout, and attempt-budget rules for one standalone invocation."""

    max_attempts: int = Field(default=1, ge=1)
    per_attempt_timeout_seconds: float | None = Field(default=None, gt=0)
    max_total_runtime_seconds: float | None = Field(default=None, gt=0)
    retry_on_failure: bool = False
    retry_on_timeout: bool = False
    retry_on_exception: bool = False


class StandaloneRunEvent(BaseModel):
    """Compact operator-visible event emitted by the standalone runner."""

    event: str
    attempt: int | None = None
    timestamp: str = Field(default_factory=_utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)


class StandaloneRunAttempt(BaseModel):
    """One execution attempt inside a standalone runner invocation."""

    attempt: int
    status: AttemptStatus
    started_at: str
    finished_at: str
    duration_seconds: float
    error: str | None = None
    output_keys: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StandaloneSessionState(BaseModel):
    """Durable session state for a standalone worker cell."""

    session_id: str = Field(default_factory=lambda: f"worker-session-{uuid4().hex}")
    worker_id: str
    status: RunnerStatus | Literal["idle", "running"] = "idle"
    started_at: str | None = None
    finished_at: str | None = None
    continuation: ContinuationPayload | None = None
    attempts: list[StandaloneRunAttempt] = Field(default_factory=list)
    events: list[StandaloneRunEvent] = Field(default_factory=list)
    last_outputs: dict[str, Any] = Field(default_factory=dict)
    last_error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StandaloneObservability(BaseModel):
    """Compact run summary intended for operators and tests."""

    session_id: str
    worker_id: str
    output_contract: OutputContract = Field(default_factory=OutputContract)
    catalog_sources: list[dict[str, Any]] = Field(default_factory=list)
    selected_refs: list[str] = Field(default_factory=list)
    expanded_refs: list[str] = Field(default_factory=list)
    capability_choices: list[str] = Field(default_factory=list)
    selected_memory_refs: list[str] = Field(default_factory=list)
    output_keys: list[str] = Field(default_factory=list)


class StandaloneRunResult(BaseModel):
    """Outcome of one standalone runner invocation."""

    status: RunnerStatus
    stop_reason: str
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    continuation: ContinuationPayload | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    session: StandaloneSessionState
    attempts: list[StandaloneRunAttempt] = Field(default_factory=list)
    events: list[StandaloneRunEvent] = Field(default_factory=list)
    observability: StandaloneObservability


class DurableAgentPolicy(BaseModel):
    """Mailbox and background-work rules for a durable agent instance.

    The durable runtime is the primary model. A bounded run is just a stricter
    policy preset over the same runtime: one accepted user message, no further
    mailbox growth, and auto-close once the agent becomes idle again.
    """

    max_user_messages: int | None = Field(default=None, ge=1)
    max_background_tasks: int = Field(default=0, ge=0)
    close_when_idle: bool = False
    standalone_policy: StandaloneRunPolicy = Field(default_factory=StandaloneRunPolicy)

    @classmethod
    def bounded(
        cls,
        *,
        standalone_policy: StandaloneRunPolicy | None = None,
        max_background_tasks: int = 0,
    ) -> "DurableAgentPolicy":
        return cls(
            max_user_messages=1,
            max_background_tasks=max_background_tasks,
            close_when_idle=True,
            standalone_policy=(
                standalone_policy.model_copy(deep=True)
                if standalone_policy is not None
                else StandaloneRunPolicy()
            ),
        )


class DurableMailboxMessage(BaseModel):
    """One ordered mailbox item accepted by a durable agent instance."""

    message_id: str = Field(default_factory=lambda: f"agent-message-{uuid4().hex}")
    message_index: int = Field(ge=1)
    task: str
    request: ExecutionRequest
    status: MailboxMessageStatus = "queued"
    enqueued_at: str = Field(default_factory=_utcnow)
    started_at: str | None = None
    finished_at: str | None = None
    result_status: RunnerStatus | None = None
    error: str | None = None
    output_keys: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DurableBackgroundTask(BaseModel):
    """Tracked background task owned by a durable agent session."""

    task_id: str = Field(default_factory=lambda: f"agent-background-{uuid4().hex}")
    label: str
    status: BackgroundTaskStatus = "running"
    started_at: str = Field(default_factory=_utcnow)
    finished_at: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DurableAgentSessionState(BaseModel):
    """Durable mailbox-backed session for one agent instance."""

    session_id: str = Field(default_factory=lambda: f"agent-session-{uuid4().hex}")
    agent_id: str
    status: DurableAgentStatus = "idle"
    created_at: str = Field(default_factory=_utcnow)
    closed_at: str | None = None
    accepted_message_count: int = 0
    active_message_id: str | None = None
    worker_session: StandaloneSessionState
    mailbox: list[DurableMailboxMessage] = Field(default_factory=list)
    background_tasks: list[DurableBackgroundTask] = Field(default_factory=list)
    events: list[StandaloneRunEvent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DurableAgentTurnResult(BaseModel):
    """Result of one processed mailbox message."""

    message_id: str
    status: RunnerStatus
    stop_reason: str
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    session: DurableAgentSessionState
    run_result: StandaloneRunResult


class _SessionEventSink:
    def __init__(
        self,
        session: StandaloneSessionState,
        *,
        attempt: int,
        events: list[StandaloneRunEvent],
    ) -> None:
        self._session = session
        self._attempt = attempt
        self._events = events

    async def record(self, event: str, payload: dict[str, Any]) -> None:
        entry = StandaloneRunEvent(event=event, attempt=self._attempt, payload=dict(payload))
        self._session.events.append(entry)
        self._events.append(entry)


class StandaloneWorkerRunner:
    """Wrap the hardened worker core in one small standalone session runner."""

    def __init__(
        self,
        *,
        completion_provider: CompletionProvider | None = None,
        tool_provider: ToolProvider | None = None,
        memory_provider: MemoryProvider | None = None,
        acquisition_provider: AcquisitionProvider | None = None,
        default_policy: StandaloneRunPolicy | None = None,
    ) -> None:
        self._completion_provider = completion_provider
        self._tool_provider = tool_provider
        self._memory_provider = memory_provider
        self._acquisition_provider = acquisition_provider
        self._default_policy = default_policy or StandaloneRunPolicy()

    def create_session(
        self,
        worker: WorkerDefinition | str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> StandaloneSessionState:
        worker_id = str(getattr(worker, "id", worker))
        return StandaloneSessionState(worker_id=worker_id, metadata=dict(metadata or {}))

    async def run(
        self,
        worker: WorkerDefinition,
        request: ExecutionRequest,
        *,
        session: StandaloneSessionState | None = None,
        policy: StandaloneRunPolicy | None = None,
    ) -> StandaloneRunResult:
        resolved_policy = (
            self._default_policy.model_copy(deep=True)
            if policy is None
            else self._default_policy.model_copy(update=policy.model_dump(exclude_unset=True))
        )
        session = session or self.create_session(worker)
        self._ensure_session_worker(session, worker)

        run_events: list[StandaloneRunEvent] = []
        run_attempts: list[StandaloneRunAttempt] = []
        start_monotonic = asyncio.get_running_loop().time()
        session.status = "running"
        session.started_at = session.started_at or _utcnow()
        session.finished_at = None
        session.last_outputs = {}
        session.last_error = None
        session.metadata = {
            **dict(session.metadata),
            "last_policy": resolved_policy.model_dump(mode="json"),
        }
        self._record_runner_event(
            session,
            run_events,
            "runner.session.started",
            payload={
                "worker_id": worker.id,
                "session_id": session.session_id,
                "max_attempts": resolved_policy.max_attempts,
                "per_attempt_timeout_seconds": resolved_policy.per_attempt_timeout_seconds,
                "max_total_runtime_seconds": resolved_policy.max_total_runtime_seconds,
            },
        )

        last_result: WorkerExecutionResult | None = None
        last_error: str | None = None
        terminal_status: RunnerStatus = "failed"
        stop_reason = "worker_failed"

        for attempt_offset in range(resolved_policy.max_attempts):
            if self._time_budget_exhausted(resolved_policy, start_monotonic):
                terminal_status = "budget_exhausted"
                stop_reason = "time_budget_exhausted"
                last_error = last_error or "Standalone runner exhausted its total runtime budget before another attempt."
                self._record_runner_event(
                    session,
                    run_events,
                    "runner.session.stopped",
                    payload={"reason": stop_reason},
                )
                break

            attempt_number = len(session.attempts) + 1
            effective_request = self._bind_request(worker, request, session, attempt_number)
            self._record_runner_event(
                session,
                run_events,
                "runner.attempt.started",
                attempt=attempt_number,
                payload={"task": effective_request.task},
            )
            attempt_started_at = _utcnow()
            attempt_start_monotonic = asyncio.get_running_loop().time()
            sink = _SessionEventSink(session, attempt=attempt_number, events=run_events)
            executor = WorkerCoreExecutor(
                completion_provider=self._completion_provider,
                tool_provider=self._tool_provider,
                memory_provider=self._memory_provider,
                acquisition_provider=self._acquisition_provider,
                event_sink=sink,
            )

            try:
                result = await self._execute_with_timeout(executor, worker, effective_request, resolved_policy)
            except asyncio.TimeoutError:
                attempt = self._build_attempt(
                    attempt=attempt_number,
                    status="timed_out",
                    started_at=attempt_started_at,
                    start_monotonic=attempt_start_monotonic,
                    error="Standalone runner timed out waiting for the worker core to finish.",
                )
                run_attempts.append(attempt)
                session.attempts.append(attempt)
                last_error = attempt.error
                self._record_runner_event(
                    session,
                    run_events,
                    "runner.attempt.timed_out",
                    attempt=attempt_number,
                    payload={"timeout_seconds": resolved_policy.per_attempt_timeout_seconds},
                )
                if attempt_offset + 1 < resolved_policy.max_attempts and resolved_policy.retry_on_timeout:
                    self._record_runner_event(
                        session,
                        run_events,
                        "runner.attempt.retrying",
                        attempt=attempt_number,
                        payload={"reason": "timeout"},
                    )
                    continue
                terminal_status = "timed_out"
                stop_reason = (
                    "attempt_budget_exhausted"
                    if resolved_policy.retry_on_timeout and attempt_offset + 1 >= resolved_policy.max_attempts
                    else "per_attempt_timeout"
                )
                break
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                attempt = self._build_attempt(
                    attempt=attempt_number,
                    status="exception",
                    started_at=attempt_started_at,
                    start_monotonic=attempt_start_monotonic,
                    error=error,
                )
                run_attempts.append(attempt)
                session.attempts.append(attempt)
                last_error = error
                self._record_runner_event(
                    session,
                    run_events,
                    "runner.attempt.exception",
                    attempt=attempt_number,
                    payload={"error": error},
                )
                if attempt_offset + 1 < resolved_policy.max_attempts and resolved_policy.retry_on_exception:
                    self._record_runner_event(
                        session,
                        run_events,
                        "runner.attempt.retrying",
                        attempt=attempt_number,
                        payload={"reason": "exception"},
                    )
                    continue
                terminal_status = "failed"
                stop_reason = (
                    "attempt_budget_exhausted"
                    if resolved_policy.retry_on_exception and attempt_offset + 1 >= resolved_policy.max_attempts
                    else "exception"
                )
                break

            last_result = result
            session.continuation = result.continuation.model_copy(deep=True) if result.continuation is not None else None
            attempt = self._build_attempt(
                attempt=attempt_number,
                status="completed" if result.status == "completed" else "failed",
                started_at=attempt_started_at,
                start_monotonic=attempt_start_monotonic,
                error=result.error,
                outputs=result.outputs,
                metadata=result.metadata,
            )
            run_attempts.append(attempt)
            session.attempts.append(attempt)
            session.last_outputs = dict(result.outputs)
            session.last_error = result.error

            if result.status == "completed":
                terminal_status = "completed"
                stop_reason = "completed"
                last_error = result.error
                self._record_runner_event(
                    session,
                    run_events,
                    "runner.attempt.completed",
                    attempt=attempt_number,
                    payload={"output_keys": sorted(result.outputs.keys())},
                )
                break

            last_error = result.error
            self._record_runner_event(
                session,
                run_events,
                "runner.attempt.failed",
                attempt=attempt_number,
                payload={"error": result.error},
            )
            if attempt_offset + 1 < resolved_policy.max_attempts and resolved_policy.retry_on_failure:
                self._record_runner_event(
                    session,
                    run_events,
                    "runner.attempt.retrying",
                    attempt=attempt_number,
                    payload={"reason": "worker_failed"},
                )
                continue
            terminal_status = "failed"
            stop_reason = (
                "attempt_budget_exhausted"
                if resolved_policy.retry_on_failure and attempt_offset + 1 >= resolved_policy.max_attempts
                else "worker_failed"
            )
            break

        session.status = terminal_status
        session.finished_at = _utcnow()
        session.last_error = last_error
        self._record_runner_event(
            session,
            run_events,
            "runner.session.completed" if terminal_status == "completed" else "runner.session.stopped",
            payload={
                "status": terminal_status,
                "stop_reason": stop_reason,
                "attempt_count": len(run_attempts),
            },
        )

        observability = self._build_observability(worker, request, last_result, session)
        return StandaloneRunResult(
            status=terminal_status,
            stop_reason=stop_reason,
            outputs=dict(last_result.outputs) if last_result is not None else {},
            error=last_error,
            continuation=(
                last_result.continuation.model_copy(deep=True)
                if last_result is not None and last_result.continuation is not None
                else session.continuation.model_copy(deep=True)
                if session.continuation is not None
                else None
            ),
            metadata=dict(last_result.metadata) if last_result is not None else {},
            session=session,
            attempts=run_attempts,
            events=run_events,
            observability=observability,
        )

    async def _execute_with_timeout(
        self,
        executor: WorkerCoreExecutor,
        worker: WorkerDefinition,
        request: ExecutionRequest,
        policy: StandaloneRunPolicy,
    ) -> WorkerExecutionResult:
        coro = executor.execute(worker, request)
        if policy.per_attempt_timeout_seconds is None:
            return await coro
        return await asyncio.wait_for(coro, timeout=policy.per_attempt_timeout_seconds)

    @staticmethod
    def _ensure_session_worker(session: StandaloneSessionState, worker: WorkerDefinition) -> None:
        if session.worker_id != worker.id:
            raise ValueError(
                f"Standalone session '{session.session_id}' belongs to worker '{session.worker_id}', "
                f"not '{worker.id}'."
            )

    @staticmethod
    def _bind_request(
        worker: WorkerDefinition,
        request: ExecutionRequest,
        session: StandaloneSessionState,
        attempt: int,
    ) -> ExecutionRequest:
        continuation = request.continuation
        if continuation is None and session.continuation is not None:
            continuation = session.continuation.model_copy(deep=True)
        metadata = {
            **dict(request.metadata),
            "worker_id": worker.id,
            "runner_session_id": session.session_id,
            "runner_attempt": attempt,
        }
        return request.model_copy(update={"continuation": continuation, "metadata": metadata})

    @staticmethod
    def _time_budget_exhausted(policy: StandaloneRunPolicy, started_at: float) -> bool:
        if policy.max_total_runtime_seconds is None:
            return False
        elapsed = asyncio.get_running_loop().time() - started_at
        return elapsed >= policy.max_total_runtime_seconds

    @staticmethod
    def _build_attempt(
        *,
        attempt: int,
        status: AttemptStatus,
        started_at: str,
        start_monotonic: float,
        error: str | None = None,
        outputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StandaloneRunAttempt:
        finished_at = _utcnow()
        return StandaloneRunAttempt(
            attempt=attempt,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=max(asyncio.get_running_loop().time() - start_monotonic, 0.0),
            error=error,
            output_keys=sorted((outputs or {}).keys()),
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def _record_runner_event(
        session: StandaloneSessionState,
        events: list[StandaloneRunEvent],
        event: str,
        *,
        attempt: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        entry = StandaloneRunEvent(event=event, attempt=attempt, payload=dict(payload or {}))
        session.events.append(entry)
        events.append(entry)

    @staticmethod
    def _build_observability(
        worker: WorkerDefinition,
        request: ExecutionRequest,
        result: WorkerExecutionResult | None,
        session: StandaloneSessionState,
    ) -> StandaloneObservability:
        continuation = (
            result.continuation
            if result is not None and result.continuation is not None
            else session.continuation
        )
        metadata = result.metadata if result is not None else {}
        selected_refs = [selection.ref_id for selection in continuation.selections] if continuation is not None else []
        expanded_refs = [context.ref_id for context in continuation.expanded_context] if continuation is not None else []
        capability_choices = StandaloneWorkerRunner._capability_choices(continuation, worker.tool_ids)
        memory_summary = dict(metadata.get("memory") or {})
        acquisition_summary = dict(metadata.get("acquisition") or {})
        outputs = result.outputs if result is not None else {}
        return StandaloneObservability(
            session_id=session.session_id,
            worker_id=worker.id,
            output_contract=request.output_contract.model_copy(deep=True),
            catalog_sources=list(acquisition_summary.get("catalog_sources") or []),
            selected_refs=selected_refs,
            expanded_refs=expanded_refs,
            capability_choices=capability_choices,
            selected_memory_refs=list(memory_summary.get("selected_memory_refs") or []),
            output_keys=sorted(outputs.keys()),
        )

    @staticmethod
    def _capability_choices(
        continuation: ContinuationPayload | None,
        fallback_tool_ids: list[str],
    ) -> list[str]:
        if continuation is None:
            return list(fallback_tool_ids)

        choices: list[str] = []
        seen: set[str] = set()
        for context in continuation.expanded_context:
            if context.family != "tool_catalog":
                continue
            try:
                capability = CapabilityManifest.model_validate(context.content)
            except Exception:
                tool_id = str(context.metadata.get("tool_id") or context.ref_id.rsplit(":", 1)[-1])
                capability = capability_manifest_from_descriptor(tool_id)
            if capability.capability_id in seen:
                continue
            seen.add(capability.capability_id)
            choices.append(capability.capability_id)

        for tool_id in fallback_tool_ids:
            if tool_id not in seen:
                seen.add(tool_id)
                choices.append(tool_id)
        return choices


class DurableAgentRunner:
    """Mailbox-backed durable agent runtime built on ``StandaloneWorkerRunner``.

    One durable runtime serves both chat-like and bounded execution. Bounded
    behavior is just a stricter ``DurableAgentPolicy`` over the same ordered
    mailbox, background-task tracking, and session state model.
    """

    def __init__(
        self,
        *,
        completion_provider: CompletionProvider | None = None,
        tool_provider: ToolProvider | None = None,
        memory_provider: MemoryProvider | None = None,
        acquisition_provider: AcquisitionProvider | None = None,
        default_policy: DurableAgentPolicy | None = None,
    ) -> None:
        self._default_policy = default_policy or DurableAgentPolicy()
        self._standalone_runner = StandaloneWorkerRunner(
            completion_provider=completion_provider,
            tool_provider=tool_provider,
            memory_provider=memory_provider,
            acquisition_provider=acquisition_provider,
            default_policy=self._default_policy.standalone_policy,
        )
        self._background_handles: dict[str, dict[str, asyncio.Task[None]]] = {}

    def create_session(
        self,
        worker: WorkerDefinition | str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> DurableAgentSessionState:
        worker_id = str(getattr(worker, "id", worker))
        worker_session = self._standalone_runner.create_session(
            worker_id,
            metadata=dict(metadata or {}),
        )
        session = DurableAgentSessionState(
            agent_id=worker_id,
            worker_session=worker_session,
            metadata=dict(metadata or {}),
        )
        self._remember_policy(session, self._default_policy)
        self._record_agent_event(
            session,
            "agent.session.created",
            payload={"agent_id": worker_id, "worker_session_id": worker_session.session_id},
        )
        return session

    def enqueue_message(
        self,
        session: DurableAgentSessionState,
        request: ExecutionRequest,
        *,
        policy: DurableAgentPolicy | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DurableMailboxMessage:
        resolved_policy = self._resolve_policy(policy)
        self._remember_policy(session, resolved_policy)
        if session.status == "closed" or session.closed_at is not None:
            raise ValueError(f"Durable agent session '{session.session_id}' is already closed.")
        if (
            resolved_policy.max_user_messages is not None
            and session.accepted_message_count >= resolved_policy.max_user_messages
        ):
            raise ValueError(
                f"Durable agent session '{session.session_id}' already accepted the "
                f"maximum {resolved_policy.max_user_messages} user messages."
            )
        message_index = session.accepted_message_count + 1
        message = DurableMailboxMessage(
            message_index=message_index,
            task=request.task,
            request=request.model_copy(deep=True),
            metadata=dict(metadata or {}),
        )
        session.mailbox.append(message)
        session.accepted_message_count = message_index
        self._record_agent_event(
            session,
            "agent.mailbox.enqueued",
            payload={
                "message_id": message.message_id,
                "message_index": message.message_index,
                "task": message.task,
                "accepted_message_count": session.accepted_message_count,
            },
        )
        self._refresh_session_status(session)
        return message

    async def process_next(
        self,
        worker: WorkerDefinition,
        session: DurableAgentSessionState,
        *,
        policy: DurableAgentPolicy | None = None,
    ) -> DurableAgentTurnResult | None:
        self._ensure_agent_session_worker(session, worker)
        resolved_policy = self._resolve_policy(policy)
        self._remember_policy(session, resolved_policy)
        if session.status == "closed":
            return None
        if session.active_message_id is not None:
            raise RuntimeError(
                f"Durable agent session '{session.session_id}' is already processing "
                f"message '{session.active_message_id}'."
            )
        message = next((item for item in session.mailbox if item.status == "queued"), None)
        if message is None:
            self._maybe_close_session(session, resolved_policy)
            self._refresh_session_status(session)
            return None

        message.status = "running"
        message.started_at = _utcnow()
        session.active_message_id = message.message_id
        self._record_agent_event(
            session,
            "agent.mailbox.started",
            payload={"message_id": message.message_id, "task": message.task},
        )
        bound_request = self._bind_mailbox_request(
            request=message.request,
            session=session,
            message=message,
        )
        run_result = await self._standalone_runner.run(
            worker,
            bound_request,
            session=session.worker_session,
            policy=resolved_policy.standalone_policy,
        )

        message.finished_at = _utcnow()
        message.result_status = run_result.status
        message.error = run_result.error
        message.output_keys = sorted(run_result.outputs.keys())
        message.status = "completed" if run_result.status == "completed" else "failed"
        session.active_message_id = None
        self._record_agent_event(
            session,
            "agent.mailbox.completed" if message.status == "completed" else "agent.mailbox.failed",
            payload={
                "message_id": message.message_id,
                "task": message.task,
                "status": run_result.status,
                "stop_reason": run_result.stop_reason,
                "output_keys": list(message.output_keys),
                "error": run_result.error,
            },
        )
        self._maybe_close_session(session, resolved_policy)
        self._refresh_session_status(session)
        return DurableAgentTurnResult(
            message_id=message.message_id,
            status=run_result.status,
            stop_reason=run_result.stop_reason,
            outputs=dict(run_result.outputs),
            error=run_result.error,
            session=session,
            run_result=run_result,
        )

    async def run_until_idle(
        self,
        worker: WorkerDefinition,
        session: DurableAgentSessionState,
        *,
        policy: DurableAgentPolicy | None = None,
    ) -> list[DurableAgentTurnResult]:
        turns: list[DurableAgentTurnResult] = []
        while True:
            result = await self.process_next(worker, session, policy=policy)
            if result is None and self._active_background_count(session) > 0:
                await self.wait_for_background_tasks(session, policy=policy)
                if session.status == "closed":
                    break
                if self.pending_messages(session):
                    continue
                break
            if result is None:
                break
            turns.append(result)
            if session.status == "closed":
                break
        return turns

    def start_background_task(
        self,
        session: DurableAgentSessionState,
        label: str,
        awaitable: Awaitable[Any],
        *,
        policy: DurableAgentPolicy | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DurableBackgroundTask:
        resolved_policy = self._resolve_policy(policy)
        self._remember_policy(session, resolved_policy)
        if session.status == "closed" or session.closed_at is not None:
            self._close_rejected_awaitable(awaitable)
            raise ValueError(f"Durable agent session '{session.session_id}' is already closed.")
        if self._active_background_count(session) >= resolved_policy.max_background_tasks:
            self._close_rejected_awaitable(awaitable)
            raise RuntimeError(
                f"Durable agent session '{session.session_id}' is already at its "
                f"background task limit ({resolved_policy.max_background_tasks})."
            )
        record = DurableBackgroundTask(label=str(label).strip() or "background-task", metadata=dict(metadata or {}))
        session.background_tasks.append(record)
        handle = asyncio.create_task(
            self._run_background_task(session, record, awaitable),
            name=f"{session.session_id}:{record.task_id}",
        )
        self._background_handles.setdefault(session.session_id, {})[record.task_id] = handle
        self._record_agent_event(
            session,
            "agent.background.started",
            payload={"task_id": record.task_id, "label": record.label},
        )
        self._refresh_session_status(session)
        return record

    async def wait_for_background_tasks(
        self,
        session: DurableAgentSessionState,
        *,
        policy: DurableAgentPolicy | None = None,
    ) -> None:
        resolved_policy = self._resolve_policy(policy) if policy is not None else self._stored_policy(session)
        self._remember_policy(session, resolved_policy)
        handles = list(self._background_handles.get(session.session_id, {}).values())
        if not handles:
            self._maybe_close_session(session, resolved_policy)
            self._refresh_session_status(session)
            return
        await asyncio.gather(*handles, return_exceptions=True)
        self._background_handles.pop(session.session_id, None)
        self._maybe_close_session(session, resolved_policy)
        self._refresh_session_status(session)

    def pending_messages(
        self,
        session: DurableAgentSessionState,
    ) -> list[DurableMailboxMessage]:
        return [item for item in session.mailbox if item.status == "queued"]

    def active_background_tasks(
        self,
        session: DurableAgentSessionState,
    ) -> list[DurableBackgroundTask]:
        return [item for item in session.background_tasks if item.status == "running"]

    async def _run_background_task(
        self,
        session: DurableAgentSessionState,
        record: DurableBackgroundTask,
        awaitable: Awaitable[Any],
    ) -> None:
        try:
            await awaitable
        except asyncio.CancelledError:
            record.status = "cancelled"
            record.error = "cancelled"
            raise
        except Exception as exc:
            record.status = "failed"
            record.error = f"{type(exc).__name__}: {exc}"
            self._record_agent_event(
                session,
                "agent.background.failed",
                payload={"task_id": record.task_id, "label": record.label, "error": record.error},
            )
        else:
            record.status = "completed"
            self._record_agent_event(
                session,
                "agent.background.completed",
                payload={"task_id": record.task_id, "label": record.label},
            )
        finally:
            record.finished_at = _utcnow()
            handles = self._background_handles.get(session.session_id, {})
            handles.pop(record.task_id, None)
            if not handles:
                self._background_handles.pop(session.session_id, None)
            self._maybe_close_session(session, self._stored_policy(session))
            self._refresh_session_status(session)

    @staticmethod
    def _bind_mailbox_request(
        *,
        request: ExecutionRequest,
        session: DurableAgentSessionState,
        message: DurableMailboxMessage,
    ) -> ExecutionRequest:
        metadata = {
            **dict(request.metadata),
            "agent_id": session.agent_id,
            "agent_session_id": session.session_id,
            "agent_message_id": message.message_id,
            "agent_message_index": message.message_index,
            "durable_mode": True,
        }
        return request.model_copy(update={"metadata": metadata})

    def _resolve_policy(self, policy: DurableAgentPolicy | None) -> DurableAgentPolicy:
        if policy is None:
            return self._default_policy.model_copy(deep=True)
        return self._default_policy.model_copy(
            update=policy.model_dump(exclude_unset=True)
        )

    @staticmethod
    def _close_rejected_awaitable(awaitable: Awaitable[Any]) -> None:
        if inspect.iscoroutine(awaitable):
            awaitable.close()

    @staticmethod
    def _remember_policy(
        session: DurableAgentSessionState,
        policy: DurableAgentPolicy,
    ) -> None:
        session.metadata = {
            **dict(session.metadata),
            "durable_policy": policy.model_dump(mode="json"),
        }

    def _stored_policy(self, session: DurableAgentSessionState) -> DurableAgentPolicy:
        raw_policy = session.metadata.get("durable_policy")
        if raw_policy is None:
            return self._default_policy.model_copy(deep=True)
        return DurableAgentPolicy.model_validate(raw_policy)

    @staticmethod
    def _ensure_agent_session_worker(
        session: DurableAgentSessionState,
        worker: WorkerDefinition,
    ) -> None:
        if session.agent_id != worker.id:
            raise ValueError(
                f"Durable agent session '{session.session_id}' belongs to agent "
                f"'{session.agent_id}', not '{worker.id}'."
            )

    @staticmethod
    def _record_agent_event(
        session: DurableAgentSessionState,
        event: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        session.events.append(StandaloneRunEvent(event=event, payload=dict(payload or {})))

    @staticmethod
    def _active_background_count(session: DurableAgentSessionState) -> int:
        return sum(1 for item in session.background_tasks if item.status == "running")

    def _refresh_session_status(self, session: DurableAgentSessionState) -> None:
        if session.closed_at is not None:
            session.status = "closed"
            return
        if session.active_message_id is not None or self._active_background_count(session) > 0:
            session.status = "running"
            return
        session.status = "idle"

    def _maybe_close_session(
        self,
        session: DurableAgentSessionState,
        policy: DurableAgentPolicy,
    ) -> None:
        if session.closed_at is not None:
            return
        has_queued = any(item.status == "queued" for item in session.mailbox)
        if has_queued or session.active_message_id is not None or self._active_background_count(session) > 0:
            return
        message_limit_reached = (
            policy.max_user_messages is not None
            and session.accepted_message_count >= policy.max_user_messages
        )
        if policy.close_when_idle or message_limit_reached:
            session.closed_at = _utcnow()
            session.status = "closed"
            self._record_agent_event(
                session,
                "agent.session.closed",
                payload={
                    "accepted_message_count": session.accepted_message_count,
                    "message_limit_reached": message_limit_reached,
                },
            )
