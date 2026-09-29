"""Long-running processes owned by Diane, not by a chat turn.

Dev servers, watchers and similar commands are started in their own session with
output going to a log file, so they survive the end of an agent run, Stop, and a
backend restart (they are re-attached by PID on the next listing). Records live in
graphs/processes/<id>.json with <id>.log beside them.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import time
from uuid import uuid4

MAX_RUNNING = 12
LOG_TAIL_BYTES = 64_000


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers signal 0; reap children we started ourselves.
    try:
        finished, _ = os.waitpid(pid, os.WNOHANG)
        if finished == pid:
            return False
    except ChildProcessError:
        pass
    return True


def _started_at(pid: int) -> str:
    """Start time as reported by ps; guards against a recycled PID after restart."""
    try:
        return subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


class ProcessManager:
    def __init__(self, base: Path):
        self.base = base / "processes"
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, process_id: str) -> Path:
        if not process_id or not process_id.isalnum():
            raise ValueError("Unknown process")
        return self.base / f"{process_id}.json"

    def _save(self, record: dict) -> None:
        path = self._path(record["id"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False))
        temporary.replace(path)

    def _refresh(self, record: dict) -> dict:
        if record["status"] == "running":
            pid = int(record.get("pid") or 0)
            same = pid and _alive(pid) and (not record.get("ps_started") or _started_at(pid) == record["ps_started"])
            if not same:
                record.update(status="exited", ended_at=record.get("ended_at") or time.time())
                self._save(record)
        return record

    def start(self, command: str, cwd: str, name: str = "", workspace_id: str = "", thread_id: str = "", origin: str = "user") -> dict:
        command = command.strip()
        if not command:
            raise ValueError("A command is required")
        folder = Path(cwd).expanduser()
        if not folder.is_dir():
            raise ValueError("The working folder does not exist")
        if sum(1 for row in self.list() if row["status"] == "running") >= MAX_RUNNING:
            raise ValueError(f"{MAX_RUNNING} processes are already running; stop one first")
        process_id = uuid4().hex[:12]
        log = self.base / f"{process_id}.log"
        with log.open("ab") as output:
            process = subprocess.Popen(command, shell=True, cwd=str(folder), stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True, env=dict(os.environ))
        record = {"id": process_id, "name": (name.strip() or command.split()[0])[:60], "command": command, "cwd": str(folder.resolve()),
                  "workspace_id": workspace_id, "thread_id": thread_id, "origin": origin, "pid": process.pid,
                  "ps_started": _started_at(process.pid), "status": "running", "started_at": time.time(), "ended_at": None}
        self._save(record)
        return record

    def list(self, workspace_id: str = "", cwd: str = "") -> list[dict]:
        rows = []
        for path in self.base.glob("*.json"):
            try:
                record = self._refresh(json.loads(path.read_text()))
            except (OSError, ValueError, KeyError):
                continue
            if workspace_id and record.get("workspace_id") not in {workspace_id, ""}:
                continue
            if cwd and not (record.get("cwd") == cwd or str(record.get("cwd", "")).startswith(cwd.rstrip("/") + "/")):
                continue
            rows.append(record)
        return sorted(rows, key=lambda row: (row["status"] != "running", -float(row.get("started_at") or 0)))

    def get(self, process_id: str) -> dict:
        path = self._path(process_id)
        if not path.is_file():
            raise ValueError("Unknown process")
        return self._refresh(json.loads(path.read_text()))

    def stop(self, process_id: str) -> dict:
        record = self.get(process_id)
        if record["status"] != "running":
            return record
        pid = int(record["pid"])
        for sig, wait in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
            try:
                os.killpg(pid, sig)
            except ProcessLookupError:
                break
            except PermissionError as exc:
                raise ValueError("Diane is not allowed to stop this process") from exc
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline and _alive(pid):
                time.sleep(0.1)
            if not _alive(pid):
                break
        record.update(status="stopped", ended_at=time.time())
        self._save(record)
        return record

    def logs(self, process_id: str, tail: int = LOG_TAIL_BYTES) -> str:
        self._path(process_id)
        log = self.base / f"{process_id}.log"
        if not log.is_file():
            return ""
        with log.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - max(1000, min(tail, 1_000_000))))
            return stream.read().decode(errors="replace")

    def remove(self, process_id: str) -> None:
        record = self.get(process_id)
        if record["status"] == "running":
            raise ValueError("Stop the process before removing it")
        self._path(process_id).unlink(missing_ok=True)
        (self.base / f"{process_id}.log").unlink(missing_ok=True)


def manager() -> ProcessManager:
    from dan.server.paths import resolve_graphs_dir
    return ProcessManager(Path(resolve_graphs_dir()))
