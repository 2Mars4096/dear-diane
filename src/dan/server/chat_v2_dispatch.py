"""Durable named admission for background callers that can replay after a crash.

The SQLite journal serializes admissions across processes. Task/run JSON remains
Agent V2's authority. Deterministic IDs let a rolled-back journal transaction
recover a run file written before a process crash, without creating another run.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3

from dan.server.chat_v2 import AgentRunCommand
from dan.server.chat_v2_store import AgentRunRecord, V2TaskRecord


def reserve_dispatch(store, *, key: str, objective: str, thread_id: str, metadata: dict | None = None) -> AgentRunRecord:
    if not key or len(key) > 256:
        raise ValueError("A bounded durable dispatch key is required")
    payload = {"objective": objective, "thread_id": thread_id, "metadata": metadata or {}}
    request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    identity = hashlib.sha256(key.encode()).hexdigest()
    run_id, task_id = "arun-dispatch-" + identity, "task-dispatch-" + identity
    journal = store.base_dir / 'dispatches.sqlite3'
    fd = os.open(journal, os.O_CREAT | os.O_RDWR, 0o600); os.close(fd)
    with store._lock:
        db = sqlite3.connect(journal, timeout=10, isolation_level=None)
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS dispatches (key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, run_id TEXT NOT NULL)')
            previous = db.execute('SELECT request_hash FROM dispatches WHERE key=?', (key,)).fetchone()
            if previous and previous[0] != request_hash:
                raise ValueError('Dispatch key was already used with different inputs')
            run = store.get_run(run_id)
            if run is None and store._run_path(run_id).exists():
                raise RuntimeError('Admitted run cannot be read; review it before retrying')
            if run is not None:
                if run.metadata.get('dispatch_request_hash') != request_hash:
                    raise ValueError('Dispatch key was already used with different inputs')
            elif previous:
                # A missing durable run after successful admission is corruption,
                # not permission to repeat an operation whose outcome is unknown.
                raise RuntimeError('Admitted run is missing; review the dispatch before retrying')
            else:
                run = AgentRunRecord(
                    run_id=run_id, task_id=task_id, thread_id=thread_id, surface_turn_id=key,
                    command=AgentRunCommand(command='start', task_id=task_id, run_id=run_id,
                        surface_turn_id=key, idempotency_key=key,
                        payload={'text': objective, 'original_request': objective}),
                    metadata={**(metadata or {}), 'dispatch_key': key, 'dispatch_request_hash': request_hash,
                              'original_request': objective, 'privacy_scope': 'private'},
                )
                store._save_run(run)
            # Repair only a missing task. Re-admission never rewinds live/terminal state.
            if store.get_task(task_id) is None:
                store._save_task(V2TaskRecord(task_id=task_id, thread_id=thread_id, active_run_id=run_id,
                    status=run.status, phase='accepted', surface_turn_ids=[key],
                    metadata={'dispatch_key': key, 'privacy_scope': 'private', **(metadata or {})}))
            db.execute('INSERT OR IGNORE INTO dispatches VALUES (?,?,?)', (key, request_hash, run_id))
            db.commit()
            return run
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
