from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from dan.worker import (
    DurableAgentPolicy,
    DurableAgentRunner,
    StandaloneRunPolicy,
    StandaloneWorkerRunner,
)
from dan.worker.core.acquisition import LocalAcquisitionProvider
from dan.worker.core.contracts import (
    AcquisitionFamily,
    AcquisitionPolicy,
    AcquisitionSource,
    ExecutionRequest,
    ToolUseContract,
)
from dan.worker.core.executor import WorkerCoreExecutor
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse
from dan.worker.core.memory import InMemoryLifecycleProvider
from dan.worker.core.model import WorkerDefinition


class _RecordingCompletionProvider:
    def __init__(self) -> None:
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        return CompletionResponse(text="ok", raw={"request_count": len(self.requests)})


class _RecordingAcquisitionProvider(LocalAcquisitionProvider):
    def __init__(self, *, tool_catalogs: dict[str, object] | None = None) -> None:
        super().__init__(tool_catalogs=tool_catalogs)
        self.discover_calls: list[dict[str, object]] = []
        self.expand_calls: list[dict[str, object]] = []

    async def discover(self, request):
        self.discover_calls.append(
            {
                "source_id": request.source.source_id,
                "family": str(request.source.family),
            }
        )
        return await super().discover(request)

    async def expand(self, request):
        self.expand_calls.append(
            {
                "source_id": request.source.source_id,
                "family": str(request.source.family),
                "refs": [selection.ref_id for selection in request.selections],
            }
        )
        return await super().expand(request)


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_worker_core_stays_catalog_first_for_files_and_tools(tmp_path: Path) -> None:
    _write_file(tmp_path / "src/dan/worker/core/executor.py", "TARGET_FILE_DETAIL\nselected executor slice\n")
    _write_file(tmp_path / "src/dan/worker/core/contracts.py", "OTHER_FILE_DETAIL\nunselected contract slice\n")
    _write_file(tmp_path / "notes.md", "UNRELATED_NOTE_DETAIL\nshould never be expanded\n")

    completion_provider = _RecordingCompletionProvider()
    acquisition_provider = _RecordingAcquisitionProvider(
        tool_catalogs={
            "tools": [
                {
                    "id": "pytest_runner",
                    "summary": "Run focused worker-core tests",
                    "details": "TOOL_DETAIL_PYTEST",
                },
                {
                    "id": "web_search",
                    "summary": "Search public sources",
                    "details": "TOOL_DETAIL_WEB",
                },
            ]
        }
    )
    executor = WorkerCoreExecutor(
        completion_provider=completion_provider,
        acquisition_provider=acquisition_provider,
    )
    worker = WorkerDefinition(
        id="core-reviewer",
        role="reviewer",
        instruction="Use staged acquisition before acting.",
        model="stub-model",
        tool_ids=["pytest_runner", "web_search"],
        acquisition_policy=AcquisitionPolicy(
            max_selected_items_per_source=1,
            max_expanded_items_per_source=1,
        ),
    )
    request = ExecutionRequest.from_harness(
        task="Inspect executor.py and use pytest_runner for focused worker-core validation.",
        acquisition={
            "sources": [
                AcquisitionSource(
                    source_id="files",
                    family=AcquisitionFamily.FILE_INVENTORY,
                    metadata={"root": str(tmp_path)},
                ),
                AcquisitionSource(
                    source_id="tools",
                    family=AcquisitionFamily.TOOL_CATALOG,
                ),
            ]
        },
    )

    result = await executor.execute(worker, request)

    assert result.status == "completed"
    assert completion_provider.requests
    completion_request = completion_provider.requests[0]

    assert [call["source_id"] for call in acquisition_provider.discover_calls] == ["files", "tools"]
    assert len(acquisition_provider.expand_calls) == 2
    assert {call["source_id"] for call in acquisition_provider.expand_calls} == {"files", "tools"}
    assert all(len(call["refs"]) == 1 for call in acquisition_provider.expand_calls)

    prompt = completion_request.user_prompt
    assert "TARGET_FILE_DETAIL" in prompt
    assert "OTHER_FILE_DETAIL" not in prompt
    assert "UNRELATED_NOTE_DETAIL" not in prompt
    assert "TOOL_DETAIL_PYTEST" in prompt
    assert "TOOL_DETAIL_WEB" not in prompt
    assert [capability.capability_id for capability in completion_request.capabilities] == ["pytest_runner"]
    assert completion_request.capabilities[0].family == "other"
    assert completion_request.capabilities[0].input_schema["type"] == "object"
    assert [schema["function"]["name"] for schema in completion_request.tools] == ["pytest_runner"]

    assert result.continuation is not None
    assert len(result.continuation.catalogs) == 2
    assert len(result.continuation.selections) == 2
    assert len(
        [context for context in result.continuation.expanded_context if context.family != "evidence_source"]
    ) == 2
    assert any("executor.py" in selection.ref_id for selection in result.continuation.selections)
    assert any("pytest_runner" in selection.ref_id for selection in result.continuation.selections)


@pytest.mark.asyncio
async def test_worker_core_reuses_continuation_without_rediscovery(tmp_path: Path) -> None:
    _write_file(tmp_path / "plan.md", "CHECKPOINT_DETAIL\nresume from selected file\n")

    completion_provider = _RecordingCompletionProvider()
    first_acquisition_provider = _RecordingAcquisitionProvider()
    worker = WorkerDefinition(
        id="checkpoint-worker",
        model="stub-model",
        acquisition_policy=AcquisitionPolicy(
            max_selected_items_per_source=1,
            max_expanded_items_per_source=1,
        ),
    )
    request = ExecutionRequest.from_harness(
        task="Read plan.md before acting.",
        acquisition={
            "sources": [
                AcquisitionSource(
                    source_id="files",
                    family=AcquisitionFamily.FILE_INVENTORY,
                    metadata={"root": str(tmp_path)},
                )
            ]
        },
    )

    first_result = await WorkerCoreExecutor(
        completion_provider=completion_provider,
        acquisition_provider=first_acquisition_provider,
    ).execute(worker, request)

    assert first_result.continuation is not None
    assert first_acquisition_provider.discover_calls
    assert first_acquisition_provider.expand_calls

    resumed_completion_provider = _RecordingCompletionProvider()
    resumed_acquisition_provider = _RecordingAcquisitionProvider()
    resumed_request = ExecutionRequest.from_harness(
        task="Continue from the saved checkpoint without rescanning the world.",
        continuation=first_result.continuation,
    )

    resumed_result = await WorkerCoreExecutor(
        completion_provider=resumed_completion_provider,
        acquisition_provider=resumed_acquisition_provider,
    ).execute(worker, resumed_request)

    assert resumed_result.status == "completed"
    assert resumed_acquisition_provider.discover_calls == []
    assert resumed_acquisition_provider.expand_calls == []
    assert resumed_completion_provider.requests
    assert "CHECKPOINT_DETAIL" in resumed_completion_provider.requests[0].user_prompt
    assert resumed_result.continuation is not None
    assert resumed_result.continuation.selections == first_result.continuation.selections


@pytest.mark.asyncio
async def test_worker_core_can_stop_before_expand_when_policy_disables_it(tmp_path: Path) -> None:
    _write_file(tmp_path / "src/dan/worker/core/executor.py", "NO_EXPAND_FILE_DETAIL\n")

    completion_provider = _RecordingCompletionProvider()
    acquisition_provider = _RecordingAcquisitionProvider(
        tool_catalogs={
            "tools": [
                {
                    "id": "pytest_runner",
                    "summary": "Run focused worker-core tests",
                    "details": "NO_EXPAND_TOOL_DETAIL",
                }
            ]
        }
    )
    worker = WorkerDefinition(
        id="catalog-only-worker",
        model="stub-model",
        tool_ids=["pytest_runner"],
    )
    request = ExecutionRequest.from_harness(
        task="Inspect executor.py and consider pytest_runner.",
        acquisition={
            "sources": [
                AcquisitionSource(
                    source_id="files",
                    family=AcquisitionFamily.FILE_INVENTORY,
                    metadata={"root": str(tmp_path)},
                ),
                AcquisitionSource(
                    source_id="tools",
                    family=AcquisitionFamily.TOOL_CATALOG,
                ),
            ],
            "policy": {
                "auto_expand": False,
                "max_selected_items_per_source": 1,
                "max_expanded_items_per_source": 1,
            },
        },
    )

    result = await WorkerCoreExecutor(
        completion_provider=completion_provider,
        acquisition_provider=acquisition_provider,
    ).execute(worker, request)

    assert result.status == "completed"
    assert acquisition_provider.discover_calls
    assert acquisition_provider.expand_calls == []
    assert completion_provider.requests
    prompt = completion_provider.requests[0].user_prompt
    assert "NO_EXPAND_FILE_DETAIL" not in prompt
    assert "NO_EXPAND_TOOL_DETAIL" not in prompt
    assert "Selected refs:" in prompt
    assert result.continuation is not None
    assert result.continuation.selections
    assert [
        context for context in result.continuation.expanded_context if context.family != "evidence_source"
    ] == []


@pytest.mark.asyncio
async def test_worker_core_request_tool_contract_can_narrow_visible_tool_surface() -> None:
    completion_provider = _RecordingCompletionProvider()
    acquisition_provider = _RecordingAcquisitionProvider(
        tool_catalogs={
            "tools": [
                {
                    "id": "pytest_runner",
                    "summary": "Run focused worker-core tests",
                    "details": "TOOL_DETAIL_PYTEST",
                },
                {
                    "id": "web_search",
                    "summary": "Search public sources",
                    "details": "TOOL_DETAIL_WEB",
                },
            ]
        }
    )
    worker = WorkerDefinition(
        id="narrowed-worker",
        model="stub-model",
        tool_ids=["pytest_runner", "web_search"],
        acquisition_policy=AcquisitionPolicy(
            max_selected_items_per_source=1,
            max_expanded_items_per_source=1,
        ),
    )
    request = ExecutionRequest.from_harness(
        task="Run the focused pytest tool only.",
        acquisition={
            "sources": [
                AcquisitionSource(
                    source_id="tools",
                    family=AcquisitionFamily.TOOL_CATALOG,
                )
            ]
        },
        tooling=ToolUseContract(
            allowed_tool_ids=["pytest_runner"],
            preferred_tool_ids=["pytest_runner"],
        ),
    )

    result = await WorkerCoreExecutor(
        completion_provider=completion_provider,
        acquisition_provider=acquisition_provider,
    ).execute(worker, request)

    assert result.status == "completed"
    completion_request = completion_provider.requests[0]
    assert [capability.capability_id for capability in completion_request.capabilities] == ["pytest_runner"]
    assert [schema["function"]["name"] for schema in completion_request.tools] == ["pytest_runner"]
    assert "TOOL_DETAIL_PYTEST" in completion_request.user_prompt
    assert "TOOL_DETAIL_WEB" not in completion_request.user_prompt
    assert result.continuation is not None
    assert all("web_search" not in selection.ref_id for selection in result.continuation.selections)


class _StandaloneReviewCompletionProvider:
    def __init__(self) -> None:
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        payload = {
            "selected_refs": [block.ref_id for block in request.evidence],
            "capabilities": [capability.capability_id for capability in request.capabilities],
            "result": "standalone-review-ok",
        }
        return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw=payload)


class _FlakyCompletionProvider:
    def __init__(self, failures: int) -> None:
        self._remaining_failures = failures
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise RuntimeError("transient failure")
        return CompletionResponse(text="recovered", raw={"calls": self.calls})


class _SleepingCompletionProvider:
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        await asyncio.sleep(0.05)
        return CompletionResponse(text="late", raw=None)


class _SequentialCompletionProvider:
    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("No completion response queued")
        return CompletionResponse(
            text=self._responses.pop(0),
            raw={"request_count": len(self.requests)},
        )


@pytest.mark.asyncio
async def test_worker_core_repairs_structured_output_against_output_schema() -> None:
    provider = _SequentialCompletionProvider(
        '{"action":"respond"}',
        '{"action":"respond","public_response":"latest run status: failed"}',
    )
    worker = WorkerDefinition(id="structured-worker", model="stub-model")
    request = ExecutionRequest.from_harness(
        task="Answer with a structured turn decision.",
        output_contract={
            "definition_of_done": "Return a turn decision object.",
            "expected_return_shape": json.dumps(
                {
                    "action": "respond|clarify|code",
                    "public_response": "<required>",
                },
                sort_keys=True,
            ),
            "output_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["respond", "clarify", "code"]},
                    "public_response": {"type": "string"},
                },
                "required": ["action", "public_response"],
            },
        },
    )

    result = await WorkerCoreExecutor(completion_provider=provider).execute(worker, request)

    assert result.status == "completed"
    assert result.outputs["result"] == {
        "action": "respond",
        "public_response": "latest run status: failed",
    }
    assert result.metadata["structured_output"]["repaired"] is True
    assert len(provider.requests) == 2
    assert "Output schema (JSON Schema):" in provider.requests[0].system_prompt
    assert "Validation errors:" in provider.requests[1].user_prompt


@pytest.mark.asyncio
async def test_worker_core_fails_when_structured_output_repair_stays_invalid() -> None:
    provider = _SequentialCompletionProvider(
        '{"action":"respond"}',
        '{"action":"respond"}',
    )
    worker = WorkerDefinition(id="structured-worker", model="stub-model")
    request = ExecutionRequest.from_harness(
        task="Answer with a structured turn decision.",
        output_contract={
            "definition_of_done": "Return a turn decision object.",
            "expected_return_shape": json.dumps(
                {
                    "action": "respond|clarify|code",
                    "public_response": "<required>",
                },
                sort_keys=True,
            ),
            "output_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["respond", "clarify", "code"]},
                    "public_response": {"type": "string"},
                },
                "required": ["action", "public_response"],
            },
        },
    )

    result = await WorkerCoreExecutor(completion_provider=provider).execute(worker, request)

    assert result.status == "failed"
    assert "Structured output validation failed after one repair pass" in (result.error or "")
    assert result.metadata["structured_output"]["repaired"] is True
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_standalone_runner_reuses_session_state_and_surfaces_observability(tmp_path: Path) -> None:
    _write_file(tmp_path / "src/dan/worker/core/executor.py", "TARGET_FILE_DETAIL\nselected executor slice\n")
    _write_file(tmp_path / "notes.md", "UNRELATED_NOTE_DETAIL\nshould not be selected\n")

    worker = WorkerDefinition(
        id="standalone-reviewer",
        role="reviewer",
        instruction="Inspect only the selected evidence before answering.",
        model="stub-model",
        tool_ids=["pytest_runner"],
        acquisition_policy=AcquisitionPolicy(
            max_selected_items_per_source=1,
            max_expanded_items_per_source=1,
        ),
    )
    completion_provider = _StandaloneReviewCompletionProvider()
    acquisition_provider = _RecordingAcquisitionProvider(
        tool_catalogs={
            f"worker-tools::{worker.id}": [
                {
                    "id": "pytest_runner",
                    "summary": "Run focused worker-core tests",
                    "details": "TOOL_DETAIL_PYTEST",
                }
            ]
        }
    )
    runner = StandaloneWorkerRunner(
        completion_provider=completion_provider,
        acquisition_provider=acquisition_provider,
        memory_provider=InMemoryLifecycleProvider(),
    )
    session = runner.create_session(worker, metadata={"surface": "python-api"})

    first_request = ExecutionRequest.from_harness(
        task="Inspect executor.py and use pytest_runner for focused validation.",
        acquisition={
            "sources": [
                AcquisitionSource(
                    source_id="workspace",
                    family=AcquisitionFamily.FILE_INVENTORY,
                    metadata={"root": str(tmp_path)},
                )
            ]
        },
        output_contract={
            "definition_of_done": "Return a compact standalone review result.",
            "expected_return_shape": "JSON string with selected refs and result text.",
        },
    )

    first_result = await runner.run(worker, first_request, session=session)

    assert first_result.status == "completed"
    assert first_result.stop_reason == "completed"
    assert [attempt.status for attempt in first_result.attempts] == ["completed"]
    assert any(event.event == "worker.acquisition.discovered" for event in first_result.events)
    assert any(event.event == "worker.acquisition.expanded" for event in first_result.events)
    assert any(event.event == "runner.session.completed" for event in first_result.events)
    assert first_result.observability.capability_choices == ["pytest_runner"]
    assert any("executor.py" in ref for ref in first_result.observability.selected_refs)
    assert any("pytest_runner" in ref for ref in first_result.observability.selected_refs)
    assert completion_provider.requests
    assert "TARGET_FILE_DETAIL" in completion_provider.requests[0].user_prompt
    assert "UNRELATED_NOTE_DETAIL" not in completion_provider.requests[0].user_prompt
    assert [call["source_id"] for call in acquisition_provider.discover_calls] == [
        "workspace",
        f"worker-tools::{worker.id}",
    ]

    second_request = ExecutionRequest.from_harness(
        task="Continue the executor.py validation from the saved standalone session without rescanning the workspace.",
    )

    second_result = await runner.run(worker, second_request, session=session)

    assert second_result.status == "completed"
    assert [attempt.status for attempt in second_result.attempts] == ["completed"]
    assert second_result.observability.selected_memory_refs
    assert session.continuation is not None
    assert session.metadata["surface"] == "python-api"
    assert [call["source_id"] for call in acquisition_provider.discover_calls].count("workspace") == 1
    assert [call["source_id"] for call in acquisition_provider.discover_calls].count(
        f"worker-tools::{worker.id}"
    ) == 1
    assert any(call["family"] == "AcquisitionFamily.MEMORY_CATALOG" for call in acquisition_provider.discover_calls)
    assert any(event.event == "worker.acquisition.discovered" for event in second_result.events)
    assert completion_provider.requests[1].user_prompt.count("TARGET_FILE_DETAIL") == 1


@pytest.mark.asyncio
async def test_standalone_runner_retries_retryable_exceptions() -> None:
    runner = StandaloneWorkerRunner(completion_provider=_FlakyCompletionProvider(failures=1))
    worker = WorkerDefinition(id="retry-cell", model="stub-model")
    request = ExecutionRequest.from_harness(task="Recover after one transient failure.")

    result = await runner.run(
        worker,
        request,
        policy=StandaloneRunPolicy(max_attempts=2, retry_on_exception=True),
    )

    assert result.status == "completed"
    assert result.error is None
    assert [attempt.status for attempt in result.attempts] == ["exception", "completed"]
    assert [event.event for event in result.events].count("runner.attempt.retrying") == 1
    assert result.session.last_error is None


@pytest.mark.asyncio
async def test_standalone_runner_stops_after_failed_attempt_budget() -> None:
    runner = StandaloneWorkerRunner()
    worker = WorkerDefinition(id="budget-cell", model="stub-model")
    request = ExecutionRequest.from_harness(task="Need a completion provider to succeed.")

    result = await runner.run(
        worker,
        request,
        policy=StandaloneRunPolicy(max_attempts=2, retry_on_failure=True),
    )

    assert result.status == "failed"
    assert result.stop_reason == "attempt_budget_exhausted"
    assert [attempt.status for attempt in result.attempts] == ["failed", "failed"]
    assert "requires a completion provider" in (result.error or "")
    assert [event.event for event in result.events].count("runner.attempt.failed") == 2
    assert [event.event for event in result.events].count("runner.attempt.retrying") == 1


@pytest.mark.asyncio
async def test_standalone_runner_honors_per_attempt_timeout() -> None:
    runner = StandaloneWorkerRunner(completion_provider=_SleepingCompletionProvider())
    worker = WorkerDefinition(id="timeout-cell", model="stub-model")
    request = ExecutionRequest.from_harness(task="Time out quickly.")

    result = await runner.run(
        worker,
        request,
        policy=StandaloneRunPolicy(max_attempts=1, per_attempt_timeout_seconds=0.01),
    )

    assert result.status == "timed_out"
    assert result.stop_reason == "per_attempt_timeout"
    assert [attempt.status for attempt in result.attempts] == ["timed_out"]
    assert "timed out" in (result.error or "")
    assert any(event.event == "runner.attempt.timed_out" for event in result.events)


@pytest.mark.asyncio
async def test_standalone_runner_honors_total_runtime_budget_between_retries() -> None:
    runner = StandaloneWorkerRunner(completion_provider=_SleepingCompletionProvider())
    worker = WorkerDefinition(id="time-budget-cell", model="stub-model")
    request = ExecutionRequest.from_harness(task="Keep retrying until the total runtime budget is gone.")

    result = await runner.run(
        worker,
        request,
        policy=StandaloneRunPolicy(
            max_attempts=3,
            per_attempt_timeout_seconds=0.01,
            max_total_runtime_seconds=0.01,
            retry_on_timeout=True,
        ),
    )

    assert result.status == "budget_exhausted"
    assert result.stop_reason == "time_budget_exhausted"
    assert [attempt.status for attempt in result.attempts] == ["timed_out"]
    assert [event.event for event in result.events].count("runner.attempt.retrying") == 1
    assert any(
        event.event == "runner.session.stopped"
        and event.payload.get("stop_reason") == "time_budget_exhausted"
        for event in result.events
    )


@pytest.mark.asyncio
async def test_durable_agent_runner_processes_mailbox_in_order_and_reuses_continuation(
    tmp_path: Path,
) -> None:
    _write_file(tmp_path / "plan.md", "CHECKPOINT_DETAIL\nresume from saved evidence\n")

    completion_provider = _RecordingCompletionProvider()
    acquisition_provider = _RecordingAcquisitionProvider()
    runner = DurableAgentRunner(
        completion_provider=completion_provider,
        acquisition_provider=acquisition_provider,
    )
    worker = WorkerDefinition(
        id="durable-agent",
        model="stub-model",
        acquisition_policy=AcquisitionPolicy(
            max_selected_items_per_source=1,
            max_expanded_items_per_source=1,
        ),
    )
    session = runner.create_session(worker, metadata={"surface": "durable-test"})

    first_message = runner.enqueue_message(
        session,
        ExecutionRequest.from_harness(
            task="Read plan.md before acting.",
            acquisition={
                "sources": [
                    AcquisitionSource(
                        source_id="files",
                        family=AcquisitionFamily.FILE_INVENTORY,
                        metadata={"root": str(tmp_path)},
                    )
                ]
            },
        ),
    )
    second_message = runner.enqueue_message(
        session,
        ExecutionRequest.from_harness(
            task="Continue from the saved checkpoint without rescanning the workspace.",
        ),
    )

    turns = await runner.run_until_idle(worker, session)

    assert [turn.message_id for turn in turns] == [first_message.message_id, second_message.message_id]
    assert [message.message_index for message in session.mailbox] == [1, 2]
    assert [request.metadata["agent_message_index"] for request in completion_provider.requests] == [1, 2]
    assert all(request.metadata["durable_mode"] is True for request in completion_provider.requests)
    assert all(request.metadata["agent_session_id"] == session.session_id for request in completion_provider.requests)
    assert completion_provider.requests[1].continuation is not None
    assert [call["source_id"] for call in acquisition_provider.discover_calls].count("files") == 1
    assert session.metadata["surface"] == "durable-test"
    assert any(event.event == "agent.mailbox.completed" for event in session.events)


@pytest.mark.asyncio
async def test_durable_agent_runner_bounded_policy_reduces_to_one_message() -> None:
    runner = DurableAgentRunner(
        completion_provider=_RecordingCompletionProvider(),
        default_policy=DurableAgentPolicy.bounded(),
    )
    worker = WorkerDefinition(id="bounded-agent", model="stub-model")
    session = runner.create_session(worker)

    runner.enqueue_message(session, ExecutionRequest.from_harness(task="Answer exactly one turn."))
    turns = await runner.run_until_idle(worker, session)

    assert len(turns) == 1
    assert turns[0].status == "completed"
    assert session.status == "closed"
    assert session.closed_at is not None
    assert any(event.event == "agent.session.closed" for event in session.events)

    with pytest.raises(ValueError, match="already closed"):
        runner.enqueue_message(session, ExecutionRequest.from_harness(task="Second turn should be rejected."))


@pytest.mark.asyncio
async def test_durable_agent_runner_tracks_background_capacity_and_closes_when_idle() -> None:
    runner = DurableAgentRunner(completion_provider=_RecordingCompletionProvider())
    worker = WorkerDefinition(id="background-agent", model="stub-model")
    policy = DurableAgentPolicy(close_when_idle=True, max_background_tasks=1)
    session = runner.create_session(worker)
    release_background = asyncio.Event()

    async def _hold_background() -> None:
        await release_background.wait()

    background = runner.start_background_task(
        session,
        "background-sync",
        _hold_background(),
        policy=policy,
    )

    with pytest.raises(RuntimeError, match="background task limit"):
        runner.start_background_task(
            session,
            "background-overflow",
            _hold_background(),
            policy=policy,
        )

    runner.enqueue_message(
        session,
        ExecutionRequest.from_harness(task="Keep answering while the background task is still running."),
        policy=policy,
    )
    turn = await runner.process_next(worker, session, policy=policy)

    assert turn is not None
    assert turn.status == "completed"
    assert session.status == "running"
    assert runner.active_background_tasks(session)[0].task_id == background.task_id

    release_background.set()
    await runner.wait_for_background_tasks(session, policy=policy)

    assert background.status == "completed"
    assert session.status == "closed"
    assert session.closed_at is not None
    assert runner.active_background_tasks(session) == []
    assert any(event.event == "agent.background.completed" for event in session.events)
