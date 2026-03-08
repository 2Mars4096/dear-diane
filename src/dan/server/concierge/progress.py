from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

from .identity import format_prefix
from .models import Project, Task

logger = logging.getLogger(__name__)

PushCallback = Callable[[str], Coroutine[Any, Any, None]]

_RUNNING_STATUSES = frozenset({"running", "started", "planning"})


@dataclass
class ProgressSnapshot:
    project_label: str
    run_id: str | None = None
    status: str = "idle"
    completed_nodes: int = 0
    total_nodes: int = 0
    current_node: str | None = None
    cost_usd: float = 0.0


class ProgressReporter:
    def __init__(self, run_manager: Any, activity_tracker: Any, meta_session_lookup: Any = None) -> None:
        self.run_manager = run_manager
        self.activity_tracker = activity_tracker
        self.meta_session_lookup = meta_session_lookup
        self._push_tasks: dict[str, asyncio.Task[None]] = {}

    def get_project_progress(self, project: Project, task: Task | None) -> ProgressSnapshot:
        runs = self.run_manager.list_runs() if self.run_manager is not None else []
        run_lookup = {run.get("run_id"): run for run in runs}
        for run_id in project.linked_run_ids:
            run = run_lookup.get(run_id)
            if run is None:
                continue
            progress = run.get("progress") or {}
            usage = run.get("usage") or {}
            return ProgressSnapshot(
                project_label=project.label,
                run_id=run_id,
                status=run.get("status", "unknown"),
                completed_nodes=int(progress.get("completed_nodes", 0)),
                total_nodes=int(progress.get("total_nodes", 0)),
                current_node=progress.get("current_node"),
                cost_usd=float(usage.get("total_cost_usd", 0.0) or 0.0),
            )
        if self.meta_session_lookup is not None:
            for session_id in project.linked_meta_session_ids:
                session = self.meta_session_lookup(session_id)
                if session is not None:
                    return ProgressSnapshot(
                        project_label=project.label,
                        run_id=session_id,
                        status=str(getattr(session, "status", "planning")),
                    )
        if project.linked_meta_session_ids:
            return ProgressSnapshot(
                project_label=project.label,
                run_id=project.linked_meta_session_ids[-1],
                status="planning",
            )
        return ProgressSnapshot(project_label=project.label)

    def format_for_surface(self, snapshot: ProgressSnapshot, surface: str) -> str:
        prefix = format_prefix(snapshot.project_label)
        if snapshot.run_id is None:
            return f"{prefix} Idle"
        return (
            f"{prefix} {snapshot.status}: "
            f"{snapshot.completed_nodes}/{snapshot.total_nodes} nodes"
            f" | current={snapshot.current_node or '-'}"
            f" | ${snapshot.cost_usd:.2f}"
        )

    async def start_push(
        self,
        project: Project,
        surface_id: str,
        *,
        interval: float = 10.0,
        callback: PushCallback | None = None,
    ) -> None:
        self.stop_push(project.project_id)
        if callback is None:
            return

        async def _loop() -> None:
            try:
                while True:
                    await asyncio.sleep(interval)
                    snapshot = self.get_project_progress(project, None)
                    if snapshot.status not in _RUNNING_STATUSES:
                        text = self.format_for_surface(snapshot, surface_id)
                        try:
                            await callback(text)
                        except Exception:
                            logger.debug("Push callback error (final)", exc_info=True)
                        break
                    text = self.format_for_surface(snapshot, surface_id)
                    try:
                        await callback(text)
                    except Exception:
                        logger.debug("Push callback error", exc_info=True)
            except asyncio.CancelledError:
                pass

        self._push_tasks[project.project_id] = asyncio.create_task(_loop())

    def stop_push(self, project_id: str) -> None:
        task = self._push_tasks.pop(project_id, None)
        if task is not None and not task.done():
            task.cancel()

    def stop_all(self) -> None:
        for project_id in list(self._push_tasks):
            self.stop_push(project_id)
