"""Read Codex's recorded subagent activity; never control or modify native sessions.

codex exec --json omits SubAgentActivity in CLI 0.155.1. The selected
account's rollout contains these typed events and links to child rollouts.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import time


def timestamp(row: dict) -> float:
    try:
        return datetime.fromisoformat(row.get("timestamp", "").replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return 0


class RolloutReader:
    def __init__(self, home: Path):
        self.home = home.resolve()
        self.paths: dict[str, Path] = {}
        self.offsets: dict[str, int] = {}
        self.discarding: set[str] = set()

    def locate(self, session: str) -> Path | None:
        if session in self.paths:
            return self.paths[session]
        # Exact indexed lookup avoids scanning other conversations or accounts.
        for db in sorted(self.home.glob("state_*.sqlite"), reverse=True):
            try:
                with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=.1)) as con:
                    row = con.execute("SELECT rollout_path FROM threads WHERE id = ?", (session,)).fetchone()
                if row:
                    path = Path(row[0]).resolve()
                    if path.is_relative_to((self.home / "sessions").resolve()) and path.is_file():
                        self.paths[session] = path
                        return path
            except (sqlite3.Error, OSError):
                continue
        return None

    def rows(self, session: str):
        path = self.locate(session)
        if path is None:
            return
        try:
            with path.open("rb") as stream:
                offset = self.offsets.get(session, 0)
                stream.seek(offset)
                # Bound each poll; resume on the next poll, including partial lines.
                while stream.tell() - offset < 2 * 1024 * 1024:
                    line = stream.readline(2 * 1024 * 1024)
                    if not line:
                        break
                    if session in self.discarding or len(line) == 2 * 1024 * 1024:
                        self.offsets[session] = stream.tell()
                        if line.endswith(b"\n"):
                            self.discarding.discard(session)
                        else:
                            self.discarding.add(session)
                        continue
                    if not line.endswith(b"\n"):
                        break
                    self.offsets[session] = stream.tell()
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(row, dict):
                        yield row
        except OSError:
            return


class CodexChildren:
    def __init__(self, team, home: Path, since: float | None = None):
        self.team = team
        self.reader = RolloutReader(home)
        self.since = time.time() if since is None else since
        self.children: dict[str, dict] = {}
        self.child_turns: dict[str, str] = {}

    def poll(self, session: str):
        if not session:
            return
        for row in self.reader.rows(session):
            if timestamp(row) < self.since:
                continue
            payload = row.get("payload", {})
            if not isinstance(payload, dict):
                continue
            item = payload.get("item") or {}
            if not isinstance(item, dict):
                continue
            if (row.get("type") != "event_msg" or payload.get("thread_id") != session
                    or item.get("type") != "SubAgentActivity"):
                continue
            child = item.get("agent_thread_id")
            if not isinstance(child, str) or not child or child == session:
                continue
            kind = item.get("kind")
            # Completion for a previous turn must not attach old work to this run.
            if child not in self.children and kind != "started":
                continue
            if child not in self.children:
                worker_id = hashlib.sha256(f"{self.team.parent_id}:{child}".encode()).hexdigest()[:32]
                name = str(item.get("agent_path") or "Codex subagent").rsplit("/", 1)[-1].replace("_", " ")
                record = dict(worker_id=worker_id, parent_run_id=self.team.parent_id,
                              backend="codex", origin="codex_subagent", can_stop=False,
                              native_session_id=child, workspace_root=self.team.workspace,
                              created_at=timestamp(row), prompt=name, status="running",
                              response="", error="", activity="Starting", actions=[])
                self.children[child] = record
                self.team.records[worker_id] = record
            record = self.children[child]
            if kind == "started":
                record["status"] = "running"
            elif kind == "completed":
                record["status"] = "completed"
            self.team.save(record)
        for child, record in self.children.items():
            for row in self.reader.rows(child):
                if timestamp(row) < max(self.since, record["created_at"]):
                    continue
                payload = row.get("payload", {})
                if row.get("type") != "event_msg" or not isinstance(payload, dict):
                    continue
                kind = payload.get("type")
                if kind == "task_started" and isinstance(payload.get("started_at"), (int, float)) and payload["started_at"] >= int(record["created_at"]):
                    self.child_turns[child] = str(payload.get("turn_id") or "")
                    record["status"] = "running"
                elif kind == "task_complete" and (not payload.get("turn_id") or payload["turn_id"] == self.child_turns.get(child)):
                    record["status"] = "completed"
                    record["response"] = str(payload.get("last_agent_message") or record["response"])[-32000:]
                elif kind == "turn_aborted" and (not payload.get("turn_id") or payload["turn_id"] == self.child_turns.get(child)):
                    record["status"] = "interrupted"
                elif kind == "item_completed" and payload.get("thread_id") == child:
                    item = payload.get("item") or {}
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "CommandExecution":
                        command = item.get("command", [])
                        command = " ".join(map(str, command)) if isinstance(command, list) else str(command)
                        self.team.event(record, {"type": "item.started", "item": {"type": "command_execution", "command": command}})
                    elif item.get("type") == "AgentMessage" and item.get("phase") == "final_answer":
                        record["response"] = "\n".join(str(block.get("text", "")) for block in item.get("content", []) if isinstance(block, dict))[-32000:]
                self.team.save(record)

    def close(self):
        for record in self.children.values():
            if record["status"] == "running":
                record["status"] = "interrupted"
                self.team.save(record)
