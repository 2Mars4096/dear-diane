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
        self._error_memory_index = None
        self._principle_store = None
        self._rule_lifecycle_manager = None
        self._experience_index = None
        self._experience_store = None

    async def _safe_async(self, coro):
        try:
            await coro
        except Exception:
            logger.warning("Background task failed", exc_info=True)

    @property
    def engine_config(self) -> EngineConfig:
        return self._config

    @property
    def tool_registry(self) -> ToolRegistry:
        return self._tool_registry

    @property
    def run_store(self) -> RunStore | None:
        return self._run_store

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

    def _get_error_memory_index(self):
        """Lazily initialize ErrorMemoryIndex from engine config."""
        if self._error_memory_index is not None:
            return self._error_memory_index
        if not getattr(self._config, "error_memory_enabled", False):
            return None
        try:
            from dan.rag import build_embedding_registry
            from dan.rag.stores import VectorStoreConfig, VectorStoreFactory

            registry = build_embedding_registry(self._config)
            model = self._config.default_embedding_model
            provider = registry.resolve(model)

            backend = getattr(self._config, "error_memory_backend", "memory")
            store = VectorStoreFactory.create(
                VectorStoreConfig(backend=backend)
            )
            from dan.engine.error_memory import ErrorMemoryIndex
            self._error_memory_index = ErrorMemoryIndex(
                embedding_provider=provider,
                embedding_model=model,
                store=store,
            )
            return self._error_memory_index
        except Exception:
            logger.debug("Failed to initialize ErrorMemoryIndex", exc_info=True)
            return None

    def _get_principle_store(self):
        """Lazily initialize PrincipleStore from engine config."""
        if self._principle_store is not None:
            return self._principle_store
        try:
            from dan.engine.memory_store import FileSystemMemoryStore
            from dan.engine.error_memory import PrincipleStore

            memory_dir = getattr(self._config, "memory_dir", "./memory")
            memory_store = FileSystemMemoryStore(memory_dir)
            self._principle_store = PrincipleStore(memory_store)
            return self._principle_store
        except Exception:
            logger.debug("Failed to initialize PrincipleStore", exc_info=True)
            return None

    def _get_rule_lifecycle_manager(self):
        """Lazily initialize RuleLifecycleManager from engine config."""
        if self._rule_lifecycle_manager is not None:
            return self._rule_lifecycle_manager
        if not getattr(self._config, "self_evolving_rules_enabled", False):
            return None
        try:
            from dan.engine.rule_generator import RuleGenerator, RuleLifecycleManager

            generator = RuleGenerator(
                base_priority=getattr(self._config, "generated_rule_base_priority", 100),
            )
            self._rule_lifecycle_manager = RuleLifecycleManager(
                base_dir=getattr(self._config, "rules_dir", "./rules"),
                generator=generator,
                default_ttl_days=getattr(self._config, "generated_rule_ttl_days", 30),
                max_rules_per_workflow=getattr(self._config, "max_generated_rules_per_workflow", 20),
            )
            return self._rule_lifecycle_manager
        except Exception:
            logger.debug("Failed to initialize RuleLifecycleManager", exc_info=True)
            return None

    def _get_experience_index(self):
        """Lazily initialize ExperienceIndex from engine config."""
        if self._experience_index is not None:
            return self._experience_index
        try:
            from dan.rag import build_embedding_registry
            from dan.rag.stores import VectorStoreConfig, VectorStoreFactory
            from dan.engine.experience import ExperienceIndex

            registry = build_embedding_registry(self._config)
            model = self._config.default_embedding_model
            provider = registry.resolve(model)

            backend = os.environ.get(
                "DAN_EXPERIENCE_STORE_BACKEND",
                os.environ.get("DAN_RAG_STORE_BACKEND", "memory"),
            )
            persist_dir = os.environ.get("DAN_EXPERIENCE_PERSIST_DIR", "./rag_data")
            store = VectorStoreFactory.create(
                VectorStoreConfig(
                    backend=backend,
                    persist_directory=persist_dir,
                ),
            )
            self._experience_index = ExperienceIndex(
                embedding_provider=provider,
                vector_store=store,
                embedding_model=model,
            )
            return self._experience_index
        except Exception:
            logger.debug("Failed to initialize ExperienceIndex", exc_info=True)
            return None

    def _get_experience_store(self):
        """Lazily initialize ExperienceStore (with optional auto-indexing)."""
        if self._experience_store is not None:
            return self._experience_store
        try:
            from dan.engine.memory_store import FileSystemMemoryStore
            from dan.engine.experience import ExperienceStore

            memory_dir = getattr(self._config, "memory_dir", "./memory")
            memory_store = FileSystemMemoryStore(memory_dir)
            self._experience_store = ExperienceStore(
                memory_store=memory_store,
                experience_index=self._get_experience_index(),
            )
            return self._experience_store
        except Exception:
            logger.debug("Failed to initialize ExperienceStore", exc_info=True)
            return None

    def _build_error_context_provider(self):
        """Build an ErrorContextProvider using the shared ErrorMemoryIndex."""
        if not getattr(self._config, "error_memory_enabled", False):
            return None
        index = self._get_error_memory_index()
        if index is None:
            return None
        try:
            from dan.engine.error_memory import ErrorContextProvider

            ps = self._get_principle_store()
            return ErrorContextProvider(
                error_memory_index=index,
                max_tokens=getattr(self._config, "error_memory_max_tokens", 2000),
                top_k=getattr(self._config, "error_memory_top_k", 5),
                principle_store=ps,
            )
        except Exception:
            logger.debug("Failed to build ErrorContextProvider", exc_info=True)
            return None

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
    # Checkpoint portal: partial rerun
    # ------------------------------------------------------------------

    async def get_checkpoint_info(self, run_id: str) -> dict[str, Any] | None:
        """Load and return checkpoint metadata for a run.

        Returns a dict with checkpoint_data fields (timestamp,
        graph_revision, completed_node_ids, node_outputs keys) or
        None if no checkpoint exists.
        """
        engine = Engine(config=self._config)
        if engine.checkpoint_store is None:
            return None
        checkpoint = await engine.checkpoint_store.load(run_id)
        if checkpoint is None:
            return None
        cd = checkpoint.get("checkpoint_data", {})
        return {
            "run_id": run_id,
            "timestamp": cd.get("timestamp"),
            "graph_id": cd.get("graph_id", ""),
            "graph_revision": cd.get("graph_revision"),
            "completed_node_ids": cd.get("completed_node_ids", []),
            "node_output_keys": list((cd.get("node_outputs") or {}).keys()),
            "has_state": "state" in checkpoint,
        }

    async def list_checkpoint_runs(self) -> list[str]:
        """Return run_ids that have persisted checkpoints."""
        engine = Engine(config=self._config)
        if engine.checkpoint_store is None:
            return []
        return await engine.checkpoint_store.list_runs()

    async def rerun_from_checkpoint(
        self,
        graph: Graph,
        graph_id: str,
        source_run_id: str,
        scope: "RerunScope",
        session_id: str | None = None,
    ) -> RunRecord:
        """Start a partial rerun from a checkpoint.

        Creates a **new** run_id linked to the source checkpoint for
        provenance.  Validates scope against the checkpoint and the
        current graph.

        Raises ``ValueError`` for invalid scope.
        Raises ``RuntimeError`` if the checkpoint is stale (graph changed).
        """
        from dan.engine.checkpoint import (
            CheckpointData,
            RerunScope,
            check_checkpoint_staleness,
            compute_downstream_nodes,
            compute_subgraph_node_ids,
        )
        from dan.engine.state import NodeStatus

        engine = Engine(config=self._config)
        if engine.checkpoint_store is None:
            raise ValueError("No checkpoint store configured")

        checkpoint = await engine.checkpoint_store.load(source_run_id)
        if checkpoint is None:
            raise ValueError(f"No checkpoint found for run_id '{source_run_id}'")

        cd_raw = checkpoint.get("checkpoint_data", {})
        cd = CheckpointData(**cd_raw) if cd_raw else CheckpointData(run_id=source_run_id)

        # -- Staleness check -----------------------------------------------
        staleness = check_checkpoint_staleness(
            cd.graph_revision,
            graph,
            cd.completed_node_ids,
        )
        if staleness.stale:
            raise RuntimeError(staleness.message)

        # -- Determine nodes to rerun vs skip ------------------------------
        all_node_ids = {n.id for n in graph.nodes}
        nodes_to_rerun: set[str] = set()

        if scope.scope_type == "downstream_of":
            if not scope.target_node_id:
                raise ValueError("downstream_of scope requires target_node_id")
            if scope.target_node_id not in all_node_ids:
                raise ValueError(f"Target node '{scope.target_node_id}' not in graph")
            nodes_to_rerun = compute_downstream_nodes(
                scope.target_node_id, graph, include_target=True,
            )

        elif scope.scope_type == "single_node":
            if not scope.target_node_id:
                raise ValueError("single_node scope requires target_node_id")
            if scope.target_node_id not in all_node_ids:
                raise ValueError(f"Target node '{scope.target_node_id}' not in graph")
            nodes_to_rerun = {scope.target_node_id}

        elif scope.scope_type == "subgraph":
            if not scope.sub_graph_key:
                raise ValueError("subgraph scope requires sub_graph_key")
            nodes_to_rerun = compute_subgraph_node_ids(graph, scope.sub_graph_key)
            if not nodes_to_rerun:
                raise ValueError(f"Sub-graph '{scope.sub_graph_key}' not found or empty")

        else:
            raise ValueError(f"Unknown scope_type: {scope.scope_type}")

        nodes_to_skip = all_node_ids - nodes_to_rerun

        # -- Create new run ------------------------------------------------
        new_run_id = f"rerun-{int(time.time() * 1000)}"
        record = RunRecord(
            run_id=new_run_id,
            graph_id=graph_id,
            status=RunStatus.PENDING,
        )
        self._runs[new_run_id] = record

        task = asyncio.create_task(
            self._rerun_task(
                record, graph, checkpoint, cd,
                nodes_to_skip=nodes_to_skip,
                nodes_to_rerun=nodes_to_rerun,
                source_run_id=source_run_id,
                scope=scope,
                session_id=session_id,
            ),
            name=f"dan-rerun-{new_run_id}",
        )
        self._tasks[new_run_id] = task
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

    def _emit_learning_event(
        self,
        record: RunRecord,
        event_type: str,
        data: dict[str, Any],
        node_id: str | None = None,
    ) -> None:
        """Emit a self-evolving learning event to the record's event log.

        Post-run events cannot use Engine._emit() (the engine has returned),
        so this appends directly to the record's event list and broadcasts.
        """
        from dan.engine.events import EngineEvent, EventType

        event = EngineEvent(
            event_type=EventType(event_type),
            run_id=record.run_id,
            node_id=node_id,
            data={"workflow_id": record.graph_id, **data},
        )
        event_dict = event.to_dict()
        record.events.append(event_dict)

        if self._run_store is not None:
            self._run_store.append_event(record.graph_id, record.run_id, event_dict)

        for queue in self._subscribers.get(record.run_id, []):
            try:
                queue.put_nowait(event_dict)
            except asyncio.QueueFull:
                pass

    def emit_rule_lifecycle_event(
        self, workflow_id: str, event_type: str, data: dict[str, Any],
    ) -> None:
        """Emit a rule lifecycle event not tied to a specific run.
        Used by API endpoints for manual rule management actions.
        Persists to the most recent run for this workflow if available."""
        from dan.engine.events import EngineEvent, EventType

        logger.info("Rule lifecycle: %s %s", event_type, data)

        recent_run = None
        for record in reversed(list(self._runs.values())):
            if record.graph_id == workflow_id:
                recent_run = record
                break

        if recent_run is not None:
            self._emit_learning_event(recent_run, event_type, data)

    def emit_optimization_applied(
        self, workflow_id: str, data: dict[str, Any],
    ) -> None:
        """Emit OPTIMIZATION_APPLIED for the most recent run of a workflow."""
        recent_run = None
        for record in reversed(list(self._runs.values())):
            if record.graph_id == workflow_id:
                recent_run = record
                break
        if recent_run is not None:
            self._emit_learning_event(recent_run, "optimization_applied", data)

    async def _enrich_and_persist(self, record: RunRecord, graph: Graph | None = None) -> None:
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

        # -- 17-1: Index errors into error memory RAG --------------------------
        if (
            getattr(self._config, "error_memory_enabled", False)
            and record.result
            and record.result.errors
        ):
            self._index_run_errors(record)

        # -- 17-2: Schedule reflection if trigger is active --------------------
        if record.run_id and not record.run_id.startswith("reflection-"):
            trigger = getattr(self._config, "reflection_trigger", "disabled")
            if trigger == "on_failure" and record.status == RunStatus.FAILED:
                self._schedule_reflection_background(record)
            elif trigger == "on_every_run" and record.status in (
                RunStatus.COMPLETED, RunStatus.FAILED
            ):
                self._schedule_reflection_background(record)

        # -- 17-2: Persist reflection principles if this was a reflection run --
        if record.run_id and record.run_id.startswith("reflection-"):
            self._persist_reflection_principles(record)

        # -- 17-3: Track rule effectiveness ------------------------------------
        if getattr(self._config, "self_evolving_rules_enabled", False):
            self._track_rule_effectiveness(record)

        # -- 19-1: Incremental experience consolidation -----------------------
        await self._maybe_consolidate_experience(record, graph)

    def _index_run_errors(self, record: RunRecord) -> None:
        """Extract and index errors from a completed run (17-1)."""
        index = self._get_error_memory_index()
        if index is None:
            return
        try:
            from dan.engine.error_memory import extract_error_records

            snapshot = record.snapshot()
            events = list(record.events)
            errors = extract_error_records(snapshot, events)
            if errors:
                import asyncio
                try:
                    asyncio.get_running_loop().create_task(
                        self._safe_async(index.index_errors(record.graph_id, errors))
                    )
                except RuntimeError:
                    logger.debug("No running loop for error indexing", exc_info=True)
                logger.debug(
                    "Indexed %d error records for run %s",
                    len(errors), record.run_id,
                )
                self._emit_learning_event(record, "error_memory_indexed", {
                    "error_count": len(errors),
                    "tier": "error_memory",
                })
        except Exception:
            logger.debug("Error indexing failed for run %s", record.run_id, exc_info=True)

    def _persist_reflection_principles(self, record: RunRecord) -> None:
        """Extract principles from a completed reflection run and persist (17-2)."""
        ps = self._get_principle_store()
        if ps is None:
            return
        try:
            if not (record.result and record.result.metadata):
                return
            from dan.engine.error_memory import CausalPrinciple

            all_principles: list[CausalPrinciple] = []
            for _node_id, node_meta in record.result.metadata.items():
                if not isinstance(node_meta, dict):
                    continue
                raw_principles = node_meta.get("principles")
                if not isinstance(raw_principles, list):
                    continue
                for p in raw_principles:
                    if not isinstance(p, dict) or not p.get("condition"):
                        continue
                    try:
                        all_principles.append(CausalPrinciple.model_validate(p))
                    except Exception:
                        all_principles.append(CausalPrinciple(
                            condition=str(p.get("condition", "")),
                            action=str(p.get("action", "")),
                            reason=str(p.get("reason", "")),
                            confidence=float(p.get("confidence", 0.5)),
                            tags=p.get("tags") if isinstance(p.get("tags"), list) else [],
                            workflow_id=record.graph_id,
                        ))
            if not all_principles:
                return

            origin_record: RunRecord | None = None
            if record.run_id and record.run_id.startswith("reflection-"):
                origin_run_id = record.run_id[len("reflection-"):]
                origin_record = self._runs.get(origin_run_id)

            import asyncio
            try:
                asyncio.get_running_loop().create_task(
                    self._safe_async(ps.store_principles(record.graph_id, all_principles))
                )
            except RuntimeError:
                logger.debug("No running loop for principle storage", exc_info=True)
            logger.debug(
                "Persisted %d reflection principles for run %s",
                len(all_principles), record.run_id,
            )

            try:
                asyncio.get_running_loop().create_task(
                    self._safe_async(ps.compact(record.graph_id))
                )
            except RuntimeError:
                logger.debug("No running loop for principle compaction", exc_info=True)

            reflection_event = {
                "reflection_run_id": record.run_id,
                "principle_count": len(all_principles),
                "tier": "reflection",
            }
            self._emit_learning_event(record, "reflection_completed", reflection_event)
            if origin_record is not None:
                self._emit_learning_event(origin_record, "reflection_completed", reflection_event)

            # -- 17-3: Generate rules/mutations from newly persisted principles ----
            rlm = self._get_rule_lifecycle_manager()
            if rlm is not None:
                for principle in all_principles:
                    try:
                        repair_level = getattr(principle, "repair_level", "prompt_fix")
                        if repair_level == "parameter_fix":
                            mutation = rlm.create_mutation(principle, record.graph_id)
                            if mutation is not None:
                                rule_event = {
                                    "rule_id": mutation.mutation_id,
                                    "hyperedge_type": "parameter_mutation",
                                    "principle_id": mutation.source_principle_id,
                                    "tier": "rules",
                                }
                                self._emit_learning_event(record, "rule_generated", rule_event)
                                if origin_record is not None:
                                    self._emit_learning_event(origin_record, "rule_generated", rule_event)
                        else:
                            rule = rlm.create_rule(principle, record.graph_id)
                            if rule is not None:
                                rule_event = {
                                    "rule_id": rule.rule_id,
                                    "hyperedge_type": rule.hyperedge.hyperedge_type,
                                    "principle_id": rule.source_principle_id,
                                    "tier": "rules",
                                }
                                self._emit_learning_event(record, "rule_generated", rule_event)
                                if origin_record is not None:
                                    self._emit_learning_event(origin_record, "rule_generated", rule_event)
                    except Exception:
                        logger.debug(
                            "Rule generation failed for principle %s",
                            getattr(principle, "id", "?"),
                            exc_info=True,
                        )
        except Exception:
            logger.debug(
                "Principle persistence failed for %s", record.run_id,
                exc_info=True,
            )

    def _schedule_reflection_background(self, record: RunRecord) -> None:
        """Schedule a background reflection run (17-2)."""
        try:
            import asyncio

            reflection_run_id = f"reflection-{record.run_id}"
            if reflection_run_id in self._runs:
                return

            self._emit_learning_event(record, "reflection_started", {
                "source_run_id": record.run_id,
                "reflection_run_id": reflection_run_id,
                "tier": "reflection",
            })

            from dan.models.nodes import ReflectionNode
            from dan.models.graph import Graph

            errors_data = []
            if record.result and record.result.errors:
                for nid, msg in record.result.errors.items():
                    errors_data.append({
                        "node_id": nid,
                        "error": msg,
                        "node_type": record.node_statuses.get(nid, ""),
                    })

            reflection_node = ReflectionNode(
                id="reflection-auto",
                name="Auto Reflection",
                source="last_run",
            )

            graph = Graph(
                nodes=[reflection_node],
                entry_points=["reflection-auto"],
                exit_points=["reflection-auto"],
            )

            inputs = {
                "run_id": record.run_id,
                "run_errors": errors_data,
                "run_events": record.events[-100:],
                "node_statuses": dict(record.node_statuses),
            }

            async def _do_reflection():
                try:
                    await self.start_run(
                        graph,
                        graph_id=record.graph_id,
                        inputs=inputs,
                        run_id=reflection_run_id,
                    )
                except Exception:
                    logger.debug(
                        "Reflection run failed for %s", record.run_id,
                        exc_info=True,
                    )

            try:
                asyncio.get_running_loop().create_task(
                    self._safe_async(_do_reflection())
                )
            except RuntimeError:
                logger.debug("No running loop for reflection scheduling", exc_info=True)
        except Exception:
            logger.debug(
                "Failed to schedule reflection for %s", record.run_id,
                exc_info=True,
            )

    def _track_rule_effectiveness(self, record: RunRecord) -> None:
        """Update effectiveness counters for generated rules (17-3)."""
        manager = self._get_rule_lifecycle_manager()
        if manager is None:
            return
        try:
            active_rules = manager.list_rules(record.graph_id, status="active")
            if not active_rules:
                return

            current_errors = set()
            if record.result and record.result.errors:
                for nid in record.result.errors:
                    current_errors.add(nid)

            for rule in active_rules:
                manager.record_application(record.graph_id, rule.rule_id)

                he = rule.hyperedge
                target_nodes = set(he.attach_to) if he.attach_to else set()
                if not target_nodes:
                    continue

                error_in_targets = bool(target_nodes & current_errors)
                manager.record_outcome(
                    record.graph_id, rule.rule_id,
                    error_recurred=error_in_targets,
                )

                updated = manager._load_one(record.graph_id, rule.rule_id)
                r = updated if updated is not None else rule
                self._emit_learning_event(record, "rule_effectiveness_update", {
                    "rule_id": rule.rule_id,
                    "apply_count": r.apply_count,
                    "effectiveness_score": r.effectiveness_score,
                    "error_recurred": error_in_targets,
                    "tier": "rules",
                })

            pruned = manager.prune_ineffective(record.graph_id)
            for rule_id in pruned:
                self._emit_learning_event(record, "rule_pruned", {
                    "rule_id": rule_id,
                    "reason": "ineffective",
                    "tier": "rules",
                })

            all_rules = manager.list_rules(record.graph_id)
            for rule in all_rules:
                if rule.status == "expired":
                    if rule.expires_at and abs(time.time() - rule.expires_at) < 60:
                        self._emit_learning_event(record, "rule_expired", {
                            "rule_id": rule.rule_id,
                            "reason": "ttl_expired",
                            "tier": "rules",
                        })
        except Exception:
            logger.debug(
                "Rule effectiveness tracking failed for %s",
                record.run_id, exc_info=True,
            )

    async def _load_principle_dicts(self, workflow_id: str) -> list[dict[str, Any]]:
        """Load persisted principles for a workflow as plain dicts."""
        ps = self._get_principle_store()
        if ps is None:
            return []
        try:
            principles = await ps.load_principles(workflow_id)
            return [p.model_dump() for p in principles]
        except Exception:
            logger.debug("Failed to load principles for %s", workflow_id, exc_info=True)
            return []

    async def _maybe_consolidate_experience(
        self,
        record: RunRecord,
        graph: Graph | None = None,
    ) -> None:
        """Incrementally consolidate workflow experience based on configured triggers."""
        if record.run_id.startswith("reflection-"):
            return
        store = self._get_experience_store()
        if store is None:
            return

        interval = max(1, int(getattr(self._config, "experience_consolidation_interval", 5)))
        try:
            from dan.engine.experience import (
                consolidate_experience,
                extract_experience_from_graph,
            )

            workflow_id = record.graph_id
            exp = await store.load_experience(workflow_id)
            if exp is None:
                if graph is None:
                    return
                exp = extract_experience_from_graph(graph).model_copy(
                    update={"workflow_id": workflow_id},
                )

            current_success = bool(record.result and record.result.success)
            existing_failure_count = max(exp.run_count - exp.success_count, 0)
            needs_full_refresh = False
            if current_success and exp.success_count == 0:
                needs_full_refresh = True
            if (not current_success) and existing_failure_count == 0:
                needs_full_refresh = True
            if (exp.run_count + 1) % interval == 0:
                needs_full_refresh = True

            if record.run_id in exp.processed_run_ids:
                return

            snapshots: list[dict[str, Any]] = []
            if needs_full_refresh and self._run_store is not None:
                summaries = self._run_store.list_summaries(
                    workflow_id=workflow_id,
                    limit=10000,
                )
                snapshots = [s for s in summaries if isinstance(s, dict)]
            if not snapshots:
                snapshots = [record.snapshot()]

            principle_dicts = (
                await self._load_principle_dicts(workflow_id)
                if needs_full_refresh
                else []
            )
            updated = consolidate_experience(exp, snapshots, principle_dicts)
            await store.save_experience(updated)
            self._emit_learning_event(record, "experience_consolidated", {
                "workflow_id": workflow_id,
                "run_count": updated.run_count,
                "success_count": updated.success_count,
                "tier": "experience",
            })
            self._emit_learning_event(record, "experience_indexed", {
                "workflow_id": workflow_id,
                "tier": "experience",
            })
        except Exception:
            logger.debug(
                "Experience consolidation failed for run %s",
                record.run_id,
                exc_info=True,
            )

    def _emit_rule_generated(self, record: RunRecord, rule_id: str, hyperedge_type: str, principle_id: str) -> None:
        """Emit RULE_GENERATED event after a new rule is created."""
        self._emit_learning_event(record, "rule_generated", {
            "rule_id": rule_id,
            "hyperedge_type": hyperedge_type,
            "principle_id": principle_id,
            "tier": "rules",
        })

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
        # 17-1: Wire error context provider so LLMExecutor can augment prompts
        ecp = self._build_error_context_provider()
        if ecp is not None:
            engine.error_context_provider = ecp
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
            await self._enrich_and_persist(record, graph=graph)

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
            await self._enrich_and_persist(record, graph=graph)

    async def _rerun_task(
        self,
        record: RunRecord,
        graph: Graph,
        checkpoint: dict[str, Any],
        checkpoint_data: Any,
        *,
        nodes_to_skip: set[str],
        nodes_to_rerun: set[str],
        source_run_id: str,
        scope: Any,
        session_id: str | None = None,
    ) -> None:
        """Execute a partial rerun, injecting checkpoint outputs for skipped nodes."""
        from dan.engine.context_runtime import (
            ArtifactStore,
            LocalStateManager,
            SharedContextStore,
        )
        from dan.engine.state import ExecutionState, NodeStatus, PortDataStore

        record.status = RunStatus.RUNNING

        engine = Engine(
            config=self._config,
            executor_registry=self._make_executor_registry(),
            event_callback=self._event_callback,
            human_input_callback=self._make_human_input_callback(record.run_id),
        )
        ecp = self._build_error_context_provider()
        if ecp is not None:
            engine.error_context_provider = ecp

        try:
            # -- Build initial state from checkpoint -----------------------
            state = ExecutionState(graph, record.run_id)

            # Rehydrate PortDataStore from checkpoint's node_outputs for
            # skipped nodes so downstream nodes receive their inputs.
            node_outputs = checkpoint_data.node_outputs or {}
            for nid in nodes_to_skip:
                if nid in node_outputs and isinstance(node_outputs[nid], dict):
                    for port_name, value in node_outputs[nid].items():
                        state.port_data.set(nid, port_name, value)
                state.mark(nid, NodeStatus.SKIPPED)

            # Mark nodes-to-rerun as PENDING (default from __init__).
            for nid in nodes_to_rerun:
                state.mark(nid, NodeStatus.PENDING)

            # Restore shared context and artifacts from checkpoint.
            shared_context = SharedContextStore(graph.shared_context)
            shared_context.restore(checkpoint.get("shared_context", {}))

            artifacts = ArtifactStore()
            artifacts.restore(checkpoint.get("artifacts", {}))

            local_state = LocalStateManager()
            local_state.restore(checkpoint.get("local_state", {}))

            # -- Emit rerun provenance event before _execute fires RUN_STARTED -
            from dan.engine.events import EngineEvent, EventType

            scope_dict = scope.model_dump() if hasattr(scope, "model_dump") else {}
            await engine._emit(EngineEvent(
                event_type=EventType.RERUN_STARTED,
                run_id=record.run_id,
                data={
                    "rerun": True,
                    "provenance": {
                        "source_checkpoint_id": source_run_id,
                        "rerun_scope": scope_dict,
                    },
                    "nodes_to_rerun": sorted(nodes_to_rerun),
                    "nodes_skipped": sorted(nodes_to_skip),
                },
            ))

            # -- Execute via engine's internal _execute --------------------
            result = await engine._execute(
                graph, state, shared_context, artifacts, local_state,
                session_id=session_id, workflow_id=record.graph_id,
                cost_tracker_state=checkpoint.get("cost_tracker"),
            )

            # Tag provenance into result metadata.
            result.metadata["__rerun_provenance__"] = {
                "source_checkpoint_id": source_run_id,
                "rerun_scope": scope_dict,
                "nodes_rerun": sorted(nodes_to_rerun),
                "nodes_skipped": sorted(nodes_to_skip),
            }

            record.result = result
            record.status = RunStatus.COMPLETED if result.success else RunStatus.FAILED
            if result.node_statuses:
                record.node_statuses.update(result.node_statuses)

        except Exception as exc:
            logger.exception("Rerun %s failed with exception", record.run_id)
            record.status = RunStatus.FAILED
            record.result = RunResult(
                run_id=record.run_id, success=False,
                errors={"exception": str(exc)},
            )
        finally:
            await self._enrich_and_persist(record, graph=graph)
