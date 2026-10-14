"""Worker-local delegation backed by the root team's admission and lifecycle."""
from __future__ import annotations


class WorkerScope:
    def __init__(self, team, parent):
        self.team, self.parent = team, parent
        self.admission_lock = team.admission_lock
        self.max_parallel_workers = team.max_parallel_workers
        self.profiles = {"self": {**parent["profile"], "backend": parent["backend"], "enabled": True}} if parent["profile"].get("allow_children") else {}

    @property
    def records(self):
        return {key: row for key, row in self.team.records.items() if row.get("parent_worker_id") == self.parent["worker_id"]}

    @property
    def tasks(self):
        return {key: task for key, task in self.team.tasks.items() if key in self.records}

    async def start(self, backend, prompt, worker_id="", isolate=False, contract=None, profile_id=""):
        async with self.admission_lock:
            if not self.parent["profile"].get("allow_children"):
                raise ValueError("Child sessions are disabled for this worker")
            if self.parent.get("status") != "running":
                raise ValueError("Parent worker is no longer running")
            if backend and backend != self.parent["backend"] or profile_id not in {"", "self"}:
                raise ValueError("Child sessions must use the parent's configuration")
            if worker_id and worker_id not in self.records:
                raise ValueError("Unknown child worker")
            return await self.team._start(self.parent["backend"], prompt, worker_id, isolate, contract,
                                          self.parent["profile_id"], parent=self.parent)

    async def stop(self, worker_id):
        if worker_id not in self.records:
            raise ValueError("Unknown child worker")
        return await self.team.stop(worker_id)

    def save(self, record):
        self.team.save(record)

    def event(self, record, row):
        self.team.event(record, row)


def worker_instructions(record, capacity):
    if not record["profile"].get("allow_children"):
        return "Child sessions are disabled for this worker. Complete your assignment yourself; do not launch agents through tools, CLI commands or scripts."
    return ("You may delegate subtasks only through Diane's child-session tool/bridge. "
            "Children inherit your agent, account, provider, model, reasoning, fast mode, permissions and child-session setting. "
            "Do not choose another agent or override configuration, and do not launch agents directly through CLI commands or native delegation tools. "
            f"All workers and descendants share {capacity} simultaneous slots, including you but excluding the main lead. "
            "If capacity is full, continue locally instead of waiting indefinitely for a child slot. "
            "Use native_worker start with profile_id='self' and your task; status shows only your direct children. "
            "Review results and apply isolated child changes to your workspace before finishing. Your active children stop when you finish.")
