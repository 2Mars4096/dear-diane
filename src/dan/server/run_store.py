"""Filesystem-backed persistence for run summaries and event logs.

RunStore writes one JSON per completed run and an append-only JSONL event log.
Layout:
    {base_dir}/
        {workflow_id}/
            {run_id}.json          # enriched snapshot (summary + metrics)
            {run_id}.events.jsonl  # raw engine events, one per line
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class RunStore:
    """Persist and query run summaries and event streams on the filesystem."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._pending_events: dict[tuple[str, str], list[str]] = {}
        self._lock = threading.RLock()
        self._flush_condition = threading.Condition(self._lock)
        self._flush_interval = max(
            0.05,
            float(os.environ.get("DAN_RUNSTORE_FLUSH_INTERVAL_SECONDS", "0.25")),
        )
        self._flush_batch_size = max(
            1,
            int(os.environ.get("DAN_RUNSTORE_FLUSH_BATCH_SIZE", "50")),
        )
        self._closed = False
        self._writer = threading.Thread(
            target=self._writer_loop,
            name="dan-runstore-writer",
            daemon=True,
        )
        self._writer.start()

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def save_summary(self, workflow_id: str, run_id: str, summary: dict[str, Any]) -> None:
        """Write (or overwrite) the run summary JSON."""
        self.flush(workflow_id, run_id)
        d = self._base / workflow_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{run_id}.json"
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(summary, default=str), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            logger.exception("Failed to save run summary %s/%s", workflow_id, run_id)
            tmp.unlink(missing_ok=True)

    def append_event(self, workflow_id: str, run_id: str, event_dict: dict[str, Any]) -> None:
        """Queue an event for ordered background persistence."""
        line = json.dumps(event_dict, default=str) + "\n"
        with self._flush_condition:
            key = (workflow_id, run_id)
            bucket = self._pending_events.setdefault(key, [])
            bucket.append(line)
            if len(bucket) >= self._flush_batch_size:
                self._flush_condition.notify_all()

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def load_summary(self, workflow_id: str, run_id: str) -> dict[str, Any] | None:
        path = self._base / workflow_id / f"{run_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Corrupted run summary %s, skipping", path)
            return None

    def load_events(
        self,
        workflow_id: str,
        run_id: str,
        *,
        node_id: str | None = None,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Load persisted events, optionally filtering by node or type."""
        self.flush(workflow_id, run_id)
        path = self._base / workflow_id / f"{run_id}.events.jsonl"
        if not path.exists():
            return []
        results: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if node_id and evt.get("node_id") != node_id:
                    continue
                if event_type and evt.get("event_type") != event_type:
                    continue
                results.append(evt)
        except Exception:
            logger.warning("Failed to read events for %s/%s", workflow_id, run_id)
        return results

    # ------------------------------------------------------------------
    # Index / listing
    # ------------------------------------------------------------------

    def list_summaries(
        self,
        workflow_id: str | None = None,
        *,
        status: str | None = None,
        after: float | None = None,
        before: float | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List persisted run summaries with optional filters."""
        dirs = [self._base / workflow_id] if workflow_id else sorted(self._base.iterdir())
        summaries: list[dict[str, Any]] = []
        for d in dirs:
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                if f.name.endswith(".tmp"):
                    continue
                s = self._try_load(f)
                if s is None:
                    continue
                if status and s.get("status") != status:
                    continue
                started = s.get("started_at", 0)
                if after and started < after:
                    continue
                if before and started > before:
                    continue
                summaries.append(s)
        summaries.sort(key=lambda x: x.get("started_at", 0), reverse=True)
        return summaries[offset : offset + limit]

    def cleanup(self, max_age_days: int) -> int:
        """Delete runs older than max_age_days. Returns count of removed runs."""
        self.flush()
        cutoff = time.time() - (max_age_days * 86400)
        removed = 0
        for d in self._base.iterdir():
            if not d.is_dir():
                continue
            for f in list(d.glob("*.json")):
                if f.name.endswith(".tmp"):
                    f.unlink(missing_ok=True)
                    continue
                s = self._try_load(f)
                if s is None:
                    continue
                if s.get("started_at", 0) < cutoff:
                    f.unlink(missing_ok=True)
                    events_file = f.with_name(f.stem + ".events.jsonl")
                    events_file.unlink(missing_ok=True)
                    removed += 1
            if d.exists() and not any(d.iterdir()):
                d.rmdir()
        return removed

    @staticmethod
    def _try_load(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Buffered writer
    # ------------------------------------------------------------------

    def flush(self, workflow_id: str | None = None, run_id: str | None = None) -> None:
        with self._flush_condition:
            payload = self._drain_pending_locked(workflow_id, run_id)
        self._write_pending(payload)

    def close(self) -> None:
        with self._flush_condition:
            self._closed = True
            self._flush_condition.notify_all()
        if self._writer.is_alive():
            self._writer.join(timeout=1.0)
        self.flush()

    def _writer_loop(self) -> None:
        while True:
            with self._flush_condition:
                if self._closed and not self._pending_events:
                    return
                if not self._pending_events:
                    self._flush_condition.wait(timeout=self._flush_interval)
                else:
                    self._flush_condition.wait(timeout=self._flush_interval)
                payload = self._drain_pending_locked()
                should_stop = self._closed and not self._pending_events
            self._write_pending(payload)
            if should_stop:
                return

    def _drain_pending_locked(
        self,
        workflow_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[tuple[str, str], list[str]]:
        if workflow_id is None and run_id is None:
            payload = self._pending_events
            self._pending_events = {}
            return payload

        key = (str(workflow_id or ""), str(run_id or ""))
        lines = self._pending_events.pop(key, [])
        return {key: lines} if lines else {}

    def _write_pending(self, payload: dict[tuple[str, str], list[str]]) -> None:
        for (workflow_id, run_id), lines in payload.items():
            if not lines:
                continue
            d = self._base / workflow_id
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{run_id}.events.jsonl"
            try:
                with path.open("a", encoding="utf-8") as handle:
                    handle.writelines(lines)
            except Exception:
                logger.exception("Failed to flush buffered events for %s/%s", workflow_id, run_id)
