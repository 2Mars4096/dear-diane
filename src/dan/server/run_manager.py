"""Async run manager — executes Engine runs as background tasks and
multiplexes events to WebSocket subscribers with catch-up support.
"""

from __future__ import annotations

import asyncio
import logging
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
        }


class RunManager:
    """Manages background engine runs and fans out events to subscribers."""

    def __init__(
        self,
        engine_config: EngineConfig | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._config = engine_config or EngineConfig()
        self._tool_registry = tool_registry or ToolRegistry()
        self._runs: dict[str, RunRecord] = {}
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._max_event_buffer = 10000
        self._pending_human_inputs: dict[str, asyncio.Event] = {}
        self._human_input_responses: dict[str, dict[str, Any]] = {}

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
    ) -> RunRecord:
        record = RunRecord(
            run_id=run_id or f"run-{int(time.time() * 1000)}",
            graph_id=graph_id,
            status=RunStatus.PENDING,
        )
        self._runs[record.run_id] = record
        task = asyncio.create_task(
            self._run_task(record, graph, inputs),
            name=f"dan-run-{record.run_id}",
        )
        self._tasks[record.run_id] = task
        return record

    async def resume_run(
        self,
        graph: Graph,
        graph_id: str,
        run_id: str,
    ) -> RunRecord:
        record = RunRecord(
            run_id=run_id,
            graph_id=graph_id,
            status=RunStatus.PENDING,
        )
        self._runs[run_id] = record
        task = asyncio.create_task(
            self._resume_task(record, graph),
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

        for queue in self._subscribers.get(run_id, []):
            try:
                queue.put_nowait(event_dict)
            except asyncio.QueueFull:
                logger.warning("Subscriber queue full for run %s", run_id)

    def _make_executor_registry(self) -> ExecutorRegistry:
        reg = ExecutorRegistry()
        reg.register("tool_operator", ToolExecutor(self._tool_registry))
        return reg

    async def _run_task(
        self,
        record: RunRecord,
        graph: Graph,
        inputs: dict[str, Any] | None,
    ) -> None:
        record.status = RunStatus.RUNNING
        engine = Engine(
            config=self._config,
            executor_registry=self._make_executor_registry(),
            event_callback=self._event_callback,
            human_input_callback=self._make_human_input_callback(record.run_id),
        )
        try:
            result = await engine.run(graph, inputs=inputs, run_id=record.run_id)
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
            record.finished_at = time.time()

    async def _resume_task(self, record: RunRecord, graph: Graph) -> None:
        record.status = RunStatus.RUNNING
        engine = Engine(
            config=self._config,
            executor_registry=self._make_executor_registry(),
            event_callback=self._event_callback,
            human_input_callback=self._make_human_input_callback(record.run_id),
        )
        try:
            result = await engine.resume(graph, run_id=record.run_id)
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
            record.finished_at = time.time()
