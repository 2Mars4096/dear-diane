"""Streaming run progress tracker for dan-chat — displays node execution progress."""

from __future__ import annotations

import sys
import time
from typing import Any


_STATUS_ICONS = {
    "started": "⏳",
    "completed": "✓",
    "failed": "✗",
    "skipped": "⏭",
}

_STATUS_ICONS_ASCII = {
    "started": "[..]",
    "completed": "[ok]",
    "failed": "[!!]",
    "skipped": "[--]",
}


def _supports_unicode() -> bool:
    import sys
    enc = getattr(sys.stdout, "encoding", "") or ""
    return "utf" in enc.lower()


def _format_duration(seconds: float) -> str:
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m{secs:.0f}s"


class RunProgressTracker:
    """Track and display node execution progress during /run."""

    def __init__(self, *, use_rich: bool = False) -> None:
        self._use_rich = use_rich
        self._node_status: dict[str, str] = {}  # node_id -> status
        self._node_names: dict[str, str] = {}  # node_id -> display name
        self._node_start: dict[str, float] = {}  # node_id -> start timestamp
        self._node_elapsed: dict[str, float] = {}  # node_id -> elapsed seconds
        self._run_start: float = time.monotonic()
        self._run_status: str = "running"
        self._run_error: str | None = None
        self._icons = _STATUS_ICONS if _supports_unicode() else _STATUS_ICONS_ASCII

    def update(self, event: dict[str, Any]) -> str | None:
        """Process a run event and return a display line (or None for silent events)."""
        ev_type = event.get("event_type") or event.get("type", "")
        node_id = event.get("node_id", "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}

        if ev_type.startswith("node_") and not node_id:
            return None

        node_name = event.get("node_name") or event.get("name") or node_id
        if node_id and node_name:
            self._node_names[node_id] = node_name

        recovery_prefix = "Auto-repair rerun: " if data.get("automatic_recovery") else ""
        display_name = f"{recovery_prefix}{node_name}" if recovery_prefix else node_name

        if ev_type == "node_started":
            self._node_status[node_id] = "started"
            self._node_start[node_id] = time.monotonic()
            icon = self._icons["started"]
            return f"  {icon} {display_name}"

        elif ev_type == "node_completed":
            self._node_status[node_id] = "completed"
            elapsed = time.monotonic() - self._node_start.get(node_id, self._run_start)
            self._node_elapsed[node_id] = elapsed
            icon = self._icons["completed"]
            return f"  {icon} {display_name} ({_format_duration(elapsed)})"

        elif ev_type == "node_failed":
            self._node_status[node_id] = "failed"
            elapsed = time.monotonic() - self._node_start.get(node_id, self._run_start)
            self._node_elapsed[node_id] = elapsed
            error = event.get("error") or data.get("error")
            if not error:
                errors = data.get("errors")
                if isinstance(errors, list) and errors:
                    first = errors[0]
                    error = first.get("message") if isinstance(first, dict) else str(first)
            icon = self._icons["failed"]
            suffix = f": {error}" if error else ""
            return f"  {icon} {display_name} ({_format_duration(elapsed)}){suffix}"

        elif ev_type == "node_skipped":
            self._node_status[node_id] = "skipped"
            icon = self._icons["skipped"]
            return f"  {icon} {display_name} (skipped)"

        elif ev_type == "run_completed":
            self._run_status = "completed"
            return None

        elif ev_type == "run_failed":
            self._run_status = "failed"
            self._run_error = event.get("error", "")
            return None

        elif ev_type == "automatic_recovery_started":
            action = str(data.get("selected_action") or "automatic recovery")
            icon = self._icons["started"]
            return f"  {icon} Auto-repair started ({action})"

        elif ev_type == "automatic_recovery_completed":
            status = str(data.get("status") or data.get("last_outcome") or "completed")
            if status == "completed":
                icon = self._icons["completed"]
                return f"  {icon} Auto-repair completed"
            if status == "exhausted":
                icon = self._icons["failed"]
                escalation = str(data.get("escalation_summary") or "").strip()
                suffix = f". {escalation}" if escalation else ""
                return f"  {icon} Auto-repair exhausted{suffix}"
            icon = self._icons["skipped"]
            return f"  {icon} Auto-repair {status}"

        return None

    def finish(self) -> str:
        """Return final summary string."""
        total_elapsed = time.monotonic() - self._run_start
        completed = sum(1 for s in self._node_status.values() if s == "completed")
        failed = sum(1 for s in self._node_status.values() if s == "failed")
        skipped = sum(1 for s in self._node_status.values() if s == "skipped")
        total = len(self._node_status)

        unicode = _supports_unicode()
        sep = "─" * 40 if unicode else "-" * 40

        lines = [f"  {sep}"]

        if self._run_status == "completed":
            header = "  Run completed" if not unicode else "  ✓ Run completed"
        elif self._run_status == "failed":
            header = "  Run failed" if not unicode else "  ✗ Run failed"
            if self._run_error:
                header += f": {self._run_error}"
        else:
            header = "  Run finished"

        lines.append(header)
        lines.append(f"  Total time: {_format_duration(total_elapsed)}")

        parts = [f"{completed}/{total} completed"]
        if failed:
            parts.append(f"{failed} failed")
        if skipped:
            parts.append(f"{skipped} skipped")
        lines.append(f"  Nodes: {', '.join(parts)}")

        total_node_time = sum(
            self._node_elapsed.get(nid, 0)
            for nid in self._node_status
            if self._node_status[nid] == "completed"
        )
        if total > 0:
            avg = total_node_time / max(completed, 1)
            lines.append(f"  Avg node time: {_format_duration(avg)}")

        return "\n".join(lines)


class MultiProjectProgressTracker:
    """Aggregate progress across several concurrent projects."""

    def __init__(self, *, use_rich: bool = False) -> None:
        self._use_rich = use_rich
        self._trackers: dict[str, RunProgressTracker] = {}
        self._run_ids: dict[str, str] = {}

    def add_project(self, project_label: str, run_id: str) -> RunProgressTracker:
        tracker = RunProgressTracker(use_rich=self._use_rich)
        self._trackers[project_label] = tracker
        self._run_ids[project_label] = run_id
        return tracker

    def remove_project(self, project_label: str) -> None:
        self._trackers.pop(project_label, None)
        self._run_ids.pop(project_label, None)

    def handle_event(self, project_label: str, event: dict[str, Any]) -> str | None:
        tracker = self._trackers.get(project_label)
        if tracker is None:
            return None
        return tracker.update(event)

    def get_summary(self) -> str:
        if not self._trackers:
            return "  No active projects."
        lines: list[str] = []
        for label, tracker in self._trackers.items():
            completed = sum(1 for s in tracker._node_status.values() if s == "completed")
            total = len(tracker._node_status)
            elapsed = _format_duration(time.monotonic() - tracker._run_start)
            lines.append(f"  {label}: {tracker._run_status} ({completed}/{total} nodes, {elapsed})")
        return "\n".join(lines)

    def print_summary(self) -> None:
        sys.stdout.write(self.get_summary() + "\n")
        sys.stdout.flush()
