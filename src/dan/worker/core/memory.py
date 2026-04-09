"""Layered memory helpers for resumable worker-core execution."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from typing import Any
from urllib.parse import quote

from dan.worker.core.contracts import (
    AcquisitionFamily,
    AcquisitionSource,
    MemoryCompactionPolicy,
    MemoryLayer,
    MemoryRecord,
    MemorySnapshot,
    MemoryWrite,
    TrustLabel,
)

_MEMORY_SEQUENCE = count(1)


def build_memory_ref_id(worker_id: str, record_id: str) -> str:
    """Build a stable ref for stored memory records."""

    return f"{AcquisitionFamily.MEMORY_CATALOG.value}:{quote(worker_id, safe='')}:{quote(record_id, safe='')}"


def build_memory_sources(
    worker_id: str,
    snapshot: MemorySnapshot,
    *,
    include_layers: set[MemoryLayer] | None = None,
) -> list[AcquisitionSource]:
    """Project compact memory records into acquisition sources."""

    include_layers = include_layers or {MemoryLayer.EPISODIC, MemoryLayer.RETAINED}
    sources: list[AcquisitionSource] = []
    for layer, records in (
        (MemoryLayer.WORKING, snapshot.working_memory),
        (MemoryLayer.EPISODIC, snapshot.episodic_memory),
        (MemoryLayer.RETAINED, snapshot.retained_memory),
    ):
        if layer not in include_layers or not records:
            continue
        sources.append(
            AcquisitionSource(
                source_id=f"memory::{worker_id}::{layer.value}",
                family=AcquisitionFamily.MEMORY_CATALOG,
                label=f"{layer.value} memory",
                selectors=[layer.value],
                metadata={
                    "memory_layer": layer.value,
                    "records": [record.model_dump(mode="json") for record in records],
                },
                discover_limit=len(records),
                max_selected_items=len(records),
                max_expanded_items=len(records),
            )
        )
    return sources


def working_memory_evidence(snapshot: MemorySnapshot) -> list:
    """Return the active working-memory evidence blocks."""

    return [record.as_evidence_block() for record in snapshot.working_memory]


class InMemoryLifecycleProvider:
    """Simple cell-local memory provider used by the worker-core tests and runner."""

    def __init__(
        self,
        *,
        snapshots: dict[str, MemorySnapshot | dict[str, Any]] | None = None,
        default_policy: MemoryCompactionPolicy | None = None,
    ) -> None:
        self._snapshots = {
            worker_id: snapshot if isinstance(snapshot, MemorySnapshot) else MemorySnapshot.model_validate(snapshot)
            for worker_id, snapshot in (snapshots or {}).items()
        }
        self._default_policy = default_policy or MemoryCompactionPolicy()

    async def recall(self, worker, request) -> MemorySnapshot:
        snapshot = self._snapshots.get(worker.id)
        if snapshot is None:
            return MemorySnapshot()
        return snapshot.model_copy(deep=True)

    async def persist(
        self,
        worker,
        request,
        outputs: dict[str, Any],
        *,
        status: str,
        continuation=None,
    ) -> MemorySnapshot:
        policy = self._resolve_policy(request)
        snapshot = self._snapshots.get(worker.id, MemorySnapshot()).model_copy(deep=True)
        writes = [self._record_from_write(worker.id, write) for write in request.memory.pending_writes]
        if request.memory.auto_persist_episode and status == "completed":
            episode = self._episode_record(worker.id, request.task, outputs, continuation)
            if self._write_scope_allowed(episode.write_scope, request.memory.allowed_write_scopes):
                writes.append(episode)
        writes = [
            record
            for record in writes
            if self._write_scope_allowed(record.write_scope, request.memory.allowed_write_scopes)
        ]
        for record in writes:
            self._append_record(snapshot, record)
        compacted = self._compact_snapshot(snapshot, policy)
        self._snapshots[worker.id] = compacted
        return compacted.model_copy(deep=True)

    def _resolve_policy(self, request) -> MemoryCompactionPolicy:
        return (
            request.memory.compaction_policy.model_copy(deep=True)
            if request.memory.compaction_policy is not None
            else self._default_policy.model_copy(deep=True)
        )

    @staticmethod
    def _append_record(snapshot: MemorySnapshot, record: MemoryRecord) -> None:
        if record.layer == MemoryLayer.WORKING:
            snapshot.working_memory.insert(0, record)
        elif record.layer == MemoryLayer.EPISODIC:
            snapshot.episodic_memory.insert(0, record)
        else:
            snapshot.retained_memory.insert(0, record)

    @staticmethod
    def _record_from_write(worker_id: str, write: MemoryWrite) -> MemoryRecord:
        record_id = f"write-{next(_MEMORY_SEQUENCE)}"
        return MemoryRecord(
            ref_id=build_memory_ref_id(worker_id, record_id),
            layer=write.layer,
            write_scope=write.write_scope,
            title=write.title,
            summary=write.summary or write.title,
            content=write.content,
            trust_label=write.trust_label,
            source=write.source,
            freshness=write.freshness,
            provenance=dict(write.provenance),
            compacted=write.content is None,
            metadata=dict(write.metadata),
        )

    @staticmethod
    def _episode_record(worker_id: str, task: str, outputs: dict[str, Any], continuation) -> MemoryRecord:
        record_id = f"episode-{next(_MEMORY_SEQUENCE)}"
        result_value = outputs.get("result", outputs)
        summary = str(outputs.get("text") or result_value)
        if len(summary) > 240:
            summary = summary[:240].rstrip() + "..."
        selected_refs = []
        if continuation is not None:
            selected_refs = [selection.ref_id for selection in continuation.selections]
        return MemoryRecord(
            ref_id=build_memory_ref_id(worker_id, record_id),
            layer=MemoryLayer.EPISODIC,
            write_scope="memory.cell",
            title=f"Task memory: {task[:80]}",
            summary=summary or task,
            content={"task": task, "outputs": outputs, "selected_refs": selected_refs},
            trust_label=TrustLabel.HISTORICAL,
            source="worker-run",
            freshness=datetime.now(UTC).isoformat(),
            provenance={"selected_refs": selected_refs},
            metadata={"auto_persisted": True},
        )

    def _compact_snapshot(
        self,
        snapshot: MemorySnapshot,
        policy: MemoryCompactionPolicy,
    ) -> MemorySnapshot:
        working = list(snapshot.working_memory[: policy.max_working_items])
        episodic = list(snapshot.episodic_memory)
        retained = list(snapshot.retained_memory)

        if len(episodic) > policy.max_episodic_items and policy.promote_overflow_to_retained:
            overflow = episodic[policy.max_episodic_items :]
            episodic = episodic[: policy.max_episodic_items]
            retained = [self._compact_record(record, layer=MemoryLayer.RETAINED) for record in overflow] + retained

        if policy.summary_only_retained:
            retained = [self._compact_record(record, layer=MemoryLayer.RETAINED) for record in retained]
        retained = retained[: policy.max_retained_items]

        return MemorySnapshot(
            working_memory=working,
            episodic_memory=episodic,
            retained_memory=retained,
        )

    @staticmethod
    def _compact_record(record: MemoryRecord, *, layer: MemoryLayer) -> MemoryRecord:
        summary = record.summary or record.title
        if not summary and record.content is not None:
            summary = str(record.content)
        return record.model_copy(
            update={
                "layer": layer,
                "summary": summary,
                "content": None,
                "compacted": True,
            }
        )

    @staticmethod
    def _write_scope_allowed(write_scope: str, allowed_write_scopes: list[str]) -> bool:
        if write_scope.startswith("memory.cell"):
            return True
        if not allowed_write_scopes:
            return False
        return write_scope in allowed_write_scopes
