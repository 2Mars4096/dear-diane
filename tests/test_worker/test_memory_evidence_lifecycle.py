from __future__ import annotations

import pytest

from dan.worker.core.acquisition import LocalAcquisitionProvider
from dan.worker.core.contracts import (
    AcquisitionPolicy,
    ExecutionRequest,
    MemoryExtractionMode,
    MemoryLayer,
    MemoryRecord,
    MemorySnapshot,
    MemoryWrite,
)
from dan.worker.core.executor import WorkerCoreExecutor
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse
from dan.worker.core.memory import InMemoryLifecycleProvider, build_memory_ref_id
from dan.worker.core.model import WorkerDefinition


class _RecordingCompletionProvider:
    def __init__(self) -> None:
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        return CompletionResponse(text="memory-ok", raw=None)


async def _run_with_memory(
    provider: InMemoryLifecycleProvider,
    *,
    task: str,
    memory: dict | None = None,
) -> tuple[CompletionRequest, object]:
    completion_provider = _RecordingCompletionProvider()
    worker = WorkerDefinition(
        id="cell",
        model="stub-model",
        acquisition_policy=AcquisitionPolicy(
            max_selected_items_per_source=1,
            max_expanded_items_per_source=1,
        ),
    )
    request = ExecutionRequest.from_harness(task=task, memory=memory)
    result = await WorkerCoreExecutor(
        completion_provider=completion_provider,
        acquisition_provider=LocalAcquisitionProvider(),
        memory_provider=provider,
    ).execute(worker, request)
    return completion_provider.requests[0], result


@pytest.mark.asyncio
async def test_memory_catalog_preserves_layer_freshness_and_provenance() -> None:
    provider = InMemoryLifecycleProvider(
        snapshots={
            "cell": MemorySnapshot(
                episodic_memory=[
                    MemoryRecord(
                        ref_id=build_memory_ref_id("cell", "episodic-1"),
                        layer=MemoryLayer.EPISODIC,
                        title="Planner regression",
                        summary="Regression reproduced in planner flow.",
                        content={"notes": "full episodic detail"},
                        freshness="2026-04-08T00:00:00+00:00",
                        provenance={"origin": "incident-42"},
                    )
                ]
            )
        }
    )

    completion_request, result = await _run_with_memory(
        provider,
        task="Continue the planner regression investigation.",
        memory={"auto_persist_episode": False},
    )

    assert "Regression reproduced in planner flow." in completion_request.user_prompt
    assert result.metadata["memory"]["episodic_refs"] == [build_memory_ref_id("cell", "episodic-1")]
    selected = result.metadata["memory"]["selected_memory_refs"]
    assert selected == [build_memory_ref_id("cell", "episodic-1")]
    expanded = result.continuation.expanded_context[0]
    assert expanded.metadata["memory_layer"] == "episodic"
    assert expanded.metadata["freshness"] == "2026-04-08T00:00:00+00:00"
    assert expanded.metadata["provenance"] == {"origin": "incident-42"}


@pytest.mark.asyncio
async def test_memory_writeback_auto_persists_episode_and_reuses_it() -> None:
    provider = InMemoryLifecycleProvider()

    first_request, first_result = await _run_with_memory(provider, task="Draft a rollout note for the worker cell.")
    second_request, second_result = await _run_with_memory(
        provider,
        task="Continue the rollout note from the saved task memory.",
    )

    assert first_result.metadata["memory"]["episodic_refs"]
    assert "memory-ok" in second_request.user_prompt
    assert second_result.metadata["memory"]["episodic_refs"]
    assert second_result.metadata["memory"]["selected_memory_refs"]


@pytest.mark.asyncio
async def test_memory_compaction_moves_overflow_to_retained_summary() -> None:
    provider = InMemoryLifecycleProvider()
    for index in range(3):
        await _run_with_memory(
            provider,
            task=f"Persist observation {index}",
            memory={
                "auto_persist_episode": False,
                "compaction_policy": {
                    "max_episodic_items": 1,
                    "max_retained_items": 4,
                },
                "pending_writes": [
                    MemoryWrite(
                        layer=MemoryLayer.EPISODIC,
                        title=f"Observation {index}",
                        summary=f"Summary {index}",
                        content={"detail": f"FULL_DETAIL_{index}"},
                    ).model_dump(mode="json")
                ],
            },
        )

    snapshot = await provider.recall(WorkerDefinition(id="cell"), ExecutionRequest.from_harness(task="inspect"))

    assert [record.title for record in snapshot.episodic_memory] == ["Observation 2"]
    assert [record.title for record in snapshot.retained_memory] == ["Observation 1", "Observation 0"]
    assert all(record.compacted for record in snapshot.retained_memory)
    assert all(record.content is None for record in snapshot.retained_memory)


@pytest.mark.asyncio
async def test_memory_extraction_mode_can_skip_rehydration() -> None:
    provider = InMemoryLifecycleProvider(
        snapshots={
            "cell": MemorySnapshot(
                episodic_memory=[
                    MemoryRecord(
                        ref_id=build_memory_ref_id("cell", "episodic-1"),
                        layer=MemoryLayer.EPISODIC,
                        title="Stored note",
                        summary="EPISODIC_SUMMARY",
                        content={"detail": "EPISODIC_DETAIL"},
                    )
                ]
            )
        }
    )

    completion_request, result = await _run_with_memory(
        provider,
        task="Ignore prior memory and answer fresh.",
        memory={
            "auto_persist_episode": False,
            "extraction_mode": MemoryExtractionMode.NONE.value,
        },
    )

    assert "EPISODIC_SUMMARY" not in completion_request.user_prompt
    assert result.metadata["memory"]["selected_memory_refs"] == []


@pytest.mark.asyncio
async def test_memory_write_scopes_require_explicit_allowance_for_non_local_writes() -> None:
    provider = InMemoryLifecycleProvider()

    await _run_with_memory(
        provider,
        task="Attempt shared write without permission.",
        memory={
            "auto_persist_episode": False,
            "pending_writes": [
                MemoryWrite(
                    layer=MemoryLayer.EPISODIC,
                    title="Shared note denied",
                    summary="DENIED_SHARED_NOTE",
                    write_scope="memory.team",
                ).model_dump(mode="json")
            ],
        },
    )
    denied_snapshot = await provider.recall(
        WorkerDefinition(id="cell"),
        ExecutionRequest.from_harness(task="inspect denied"),
    )
    assert denied_snapshot.episodic_memory == []

    await _run_with_memory(
        provider,
        task="Persist shared write with permission.",
        memory={
            "auto_persist_episode": False,
            "allowed_write_scopes": ["memory.team"],
            "pending_writes": [
                MemoryWrite(
                    layer=MemoryLayer.EPISODIC,
                    title="Shared note allowed",
                    summary="ALLOWED_SHARED_NOTE",
                    write_scope="memory.team",
                ).model_dump(mode="json")
            ],
        },
    )
    allowed_snapshot = await provider.recall(
        WorkerDefinition(id="cell"),
        ExecutionRequest.from_harness(task="inspect allowed"),
    )
    assert [record.title for record in allowed_snapshot.episodic_memory] == ["Shared note allowed"]
    assert allowed_snapshot.episodic_memory[0].write_scope == "memory.team"
