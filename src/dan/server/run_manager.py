"""Async run manager — executes Engine runs as background tasks and
multiplexes events to WebSocket subscribers with catch-up support.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from dan.engine.events import EngineEvent, EventType
from dan.engine.executor import EngineConfig, ExecutorRegistry
from dan.engine.scheduler import Engine, RunResult
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.graph import Graph
from dan.providers.costs import estimate_cost
from dan.server.run_store import RunStore

logger = logging.getLogger(__name__)


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class RunRecord:
    """Tracks a single run's state for the manager."""

    run_id: str
    graph_id: str
    status: RunStatus = RunStatus.PENDING
    result: RunResult | None = None
    node_statuses: dict[str, str] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost: float | None = None
    elapsed_seconds: float | None = None
    node_usage: dict[str, dict[str, Any]] = field(default_factory=dict)
    model: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "graph_id": self.graph_id,
            "status": self.status.value,
            "node_statuses": dict(self.node_statuses),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "success": self.result.success if self.result else None,
            "errors": self.result.errors if self.result else {},
            "outputs": self.result.outputs if self.result else {},
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_cost": self.total_cost,
            "elapsed_seconds": self.elapsed_seconds,
            "node_usage": dict(self.node_usage),
        }

    @staticmethod
    def from_summary(summary: dict[str, Any]) -> "RunRecord":
        """Reconstruct a record from a persisted summary (for startup hydration)."""
        rec = RunRecord(
            run_id=summary["run_id"],
            graph_id=summary.get("graph_id", ""),
            status=RunStatus(summary.get("status", "completed")),
            started_at=summary.get("started_at", 0),
            finished_at=summary.get("finished_at"),
            total_prompt_tokens=summary.get("total_prompt_tokens", 0),
            total_completion_tokens=summary.get("total_completion_tokens", 0),
            total_tokens=summary.get("total_tokens", 0),
            total_cost=summary.get("total_cost"),
            elapsed_seconds=summary.get("elapsed_seconds"),
            node_usage=summary.get("node_usage", {}),
        )
        rec.node_statuses = summary.get("node_statuses", {})
        errors = summary.get("errors", {})
        outputs = summary.get("outputs", {})
        success = summary.get("success", True)
        rec.result = RunResult(
            run_id=rec.run_id,
            success=success if success is not None else True,
            errors=errors,
            outputs=outputs,
        )
        return rec


class RunManager:
    """Manages background engine runs and fans out events to subscribers."""

    def __init__(
        self,
        engine_config: EngineConfig | None = None,
        tool_registry: ToolRegistry | None = None,
        run_store: RunStore | None = None,
    ) -> None:
        self._config = engine_config or EngineConfig()
        self._tool_registry = tool_registry or ToolRegistry()
        self._run_store = run_store
        self._runs: dict[str, RunRecord] = {}
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._max_event_buffer = 10000
        self._pending_human_inputs: dict[str, asyncio.Event] = {}
        self._human_input_responses: dict[str, dict[str, Any]] = {}
        self._hydrate_from_store()

    def _hydrate_from_store(self) -> None:
        """Load historical run summaries from RunStore into the in-memory index."""
        if self._run_store is None:
            return
        retention_days = int(os.environ.get("DAN_RUN_RETENTION_DAYS", "0"))
        if retention_days > 0:
            removed = self._run_store.cleanup(retention_days)
            if removed:
                logger.info("Retention cleanup: removed %d runs older than %d days", removed, retention_days)
        for summary in self._run_store.list_summaries(limit=10000):
            rid = summary.get("run_id")
            if rid and rid not in self._runs:
                self._runs[rid] = RunRecord.from_summary(summary)

    def get_run(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def list_runs(self) -> list[dict[str, Any]]:
        return [r.snapshot() for r in self._runs.values()]

    def submit_human_input(self, run_id: str, request_id: str, response: dict[str, Any]) -> bool:
        """Submit a response for a pending human-input request."""
        evt = self._pending_human_inputs.get(request_id)
        if evt is None:
            return False
        self._human_input_responses[request_id] = response
        evt.set()
        return True

    def get_pending_human_inputs(self, run_id: str) -> list[dict[str, Any]]:
        """Return pending (unresolved) human-input requests for a run."""
        record = self._runs.get(run_id)
        if record is None:
            return []
        pending = []
        for evt_dict in record.events:
            if evt_dict.get("event_type") == "human_input_needed":
                rid = (evt_dict.get("data") or {}).get("request_id")
                if rid and rid in self._pending_human_inputs and not self._pending_human_inputs[rid].is_set():
                    pending.append(evt_dict)
        return pending

    def _make_human_input_callback(self, run_id: str):
        async def callback(request_meta: dict[str, Any]) -> dict[str, Any]:
            request_id = request_meta["request_id"]
            evt = asyncio.Event()
            self._pending_human_inputs[request_id] = evt
            await evt.wait()
            response = self._human_input_responses.pop(request_id, {})
            self._pending_human_inputs.pop(request_id, None)
            return response
        return callback

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Subscribe to events for a run. Returns a queue that receives
        a catch-up snapshot followed by live events."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        record = self._runs.get(run_id)
        if record is not None:
            queue.put_nowait({
                "event_type": "_catchup",
                "run_id": run_id,
                "snapshot": record.snapshot(),
                "buffered_events": list(record.events[-self._max_event_buffer:]),
                "pending_human_inputs": self.get_pending_human_inputs(run_id),
            })
        self._subscribers[run_id].append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subs = self._subscribers.get(run_id)
        if subs and queue in subs:
            subs.remove(queue)

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    async def start_run(
        self,
        graph: Graph,
        graph_id: str,
        inputs: dict[str, Any] | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> RunRecord:
        record = RunRecord(
            run_id=run_id or f"run-{int(time.time() * 1000)}",
            graph_id=graph_id,
            status=RunStatus.PENDING,
        )
        self._runs[record.run_id] = record
        task = asyncio.create_task(
            self._run_task(record, graph, inputs, session_id=session_id),
            name=f"dan-run-{record.run_id}",
        )
        self._tasks[record.run_id] = task
        return record

    async def resume_run(
        self,
        graph: Graph,
        graph_id: str,
        run_id: str,
        session_id: str | None = None,
    ) -> RunRecord:
        record = RunRecord(
            run_id=run_id,
            graph_id=graph_id,
            status=RunStatus.PENDING,
        )
        self._runs[run_id] = record
        task = asyncio.create_task(
            self._resume_task(record, graph, session_id=session_id),
            name=f"dan-resume-{run_id}",
        )
        self._tasks[run_id] = task
        return record

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _event_callback(self, event: EngineEvent) -> None:
        """Receives events from the engine and fans out to subscribers."""
        event_dict = event.to_dict()
        run_id = event.run_id
        record = self._runs.get(run_id)
        if record is not None:
            if event.event_type == EventType.INTERMEDIATE_TEXT and event.node_id:
                for i in range(len(record.events) - 1, -1, -1):
                    old = record.events[i]
                    if (old.get("event_type") == "intermediate_text"
                            and old.get("node_id") == event.node_id
                            and not (old.get("data") or {}).get("done")):
                        record.events[i] = event_dict
                        break
                else:
                    record.events.append(event_dict)
            else:
                record.events.append(event_dict)
            if len(record.events) > self._max_event_buffer:
                record.events = record.events[-self._max_event_buffer:]
            if event.node_id and event.event_type in (
                EventType.NODE_STARTED, EventType.NODE_COMPLETED,
                EventType.NODE_FAILED, EventType.NODE_SKIPPED,
            ):
                record.node_statuses[event.node_id] = event.event_type.value

            if self._run_store is not None:
                self._run_store.append_event(record.graph_id, run_id, event_dict)

        for queue in self._subscribers.get(run_id, []):
            try:
                queue.put_nowait(event_dict)
            except asyncio.QueueFull:
                logger.warning("Subscriber queue full for run %s", run_id)

    def _make_executor_registry(self) -> ExecutorRegistry:
        reg = ExecutorRegistry()
        reg.register("tool_operator", ToolExecutor(self._tool_registry))
        return reg

    def _enrich_and_persist(self, record: RunRecord) -> None:
        """Extract usage metrics from RunResult and persist to RunStore."""
        record.finished_at = time.time()
        record.elapsed_seconds = round(record.finished_at - record.started_at, 2)
        if record.result and record.result.metadata:
            meta = record.result.metadata
            for node_id, node_meta in meta.items():
                if not isinstance(node_meta, dict):
                    continue
                usage = node_meta.get("usage")
                if usage:
                    record.node_usage[node_id] = usage
                    record.total_prompt_tokens += usage.get("prompt_tokens", 0)
                    record.total_completion_tokens += usage.get("completion_tokens", 0)
                    record.total_tokens += usage.get("total_tokens", 0)
                    node_model = node_meta.get("model") or self._config.default_model
                    if node_model:
                        cost = estimate_cost(
                            node_model,
                            usage.get("prompt_tokens", 0),
                            usage.get("completion_tokens", 0),
                        )
                        if cost is not None:
                            record.total_cost = (record.total_cost or 0.0) + cost
        if self._run_store is not None:
            self._run_store.save_summary(record.graph_id, record.run_id, record.snapshot())

    async def _run_task(
        self,
        record: RunRecord,
        graph: Graph,
        inputs: dict[str, Any] | None,
        session_id: str | None = None,
    ) -> None:
        record.status = RunStatus.RUNNING
        engine = Engine(
            config=self._config,
            executor_registry=self._make_executor_registry(),
            event_callback=self._event_callback,
            human_input_callback=self._make_human_input_callback(record.run_id),
        )
        try:
            result = await engine.run(
                graph, inputs=inputs, run_id=record.run_id,
                session_id=session_id, workflow_id=record.graph_id,
            )
            record.result = result
            record.status = RunStatus.COMPLETED if result.success else RunStatus.FAILED
            if result.node_statuses:
                record.node_statuses.update(result.node_statuses)
        except Exception as exc:
            logger.exception("Run %s failed with exception", record.run_id)
            record.status = RunStatus.FAILED
            record.result = RunResult(
                run_id=record.run_id, success=False,
                errors={"exception": str(exc)},
            )
        finally:
            self._enrich_and_persist(record)

    async def _resume_task(
        self,
        record: RunRecord,
        graph: Graph,
        session_id: str | None = None,
    ) -> None:
        record.status = RunStatus.RUNNING
        engine = Engine(
            config=self._config,
            executor_registry=self._make_executor_registry(),
            event_callback=self._event_callback,
            human_input_callback=self._make_human_input_callback(record.run_id),
        )
        try:
            result = await engine.resume(
                graph, run_id=record.run_id,
                session_id=session_id, workflow_id=record.graph_id,
            )
            record.result = result
            record.status = RunStatus.COMPLETED if result.success else RunStatus.FAILED
            if result.node_statuses:
                record.node_statuses.update(result.node_statuses)
        except Exception as exc:
            logger.exception("Resume %s failed with exception", record.run_id)
            record.status = RunStatus.FAILED
            record.result = RunResult(
                run_id=record.run_id, success=False,
                errors={"exception": str(exc)},
            )
        finally:
            self._enrich_and_persist(record)
