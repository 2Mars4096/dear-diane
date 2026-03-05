"""Activity tracking across all surfaces."""

from __future__ import annotations

import time
from typing import Any

from dan.server.run_manager import RunManager

from .models import ActivitySnapshot


class ActivityTracker:
    """Wraps RunManager to provide surface-aware activity tracking."""

    def __init__(self, run_manager: RunManager) -> None:
        self._rm = run_manager
        self._surfaces: dict[str, dict[str, Any]] = {}

    def register_surface(self, surface_id: str, surface_type: str) -> None:
        self._surfaces[surface_id] = {
            "surface_id": surface_id,
            "surface_type": surface_type,
            "connected_since": time.time(),
            "last_active": time.time(),
        }

    def touch_surface(self, surface_id: str) -> None:
        if surface_id in self._surfaces:
            self._surfaces[surface_id]["last_active"] = time.time()

    def remove_surface(self, surface_id: str) -> None:
        self._surfaces.pop(surface_id, None)

    def get_active_surfaces(self, stale_threshold: float = 300.0) -> list[dict[str, Any]]:
        now = time.time()
        return [
            s for s in self._surfaces.values()
            if now - s["last_active"] < stale_threshold
        ]

    def get_activity(self) -> ActivitySnapshot:
        runs = self._rm.list_runs()
        active = [r for r in runs if r.get("status") in ("running", "pending")]
        recent = [r for r in runs if r.get("status") not in ("running", "pending")]
        recent = sorted(recent, key=lambda r: r.get("start_time", 0), reverse=True)[:20]
        return ActivitySnapshot(
            active=active,
            recent=recent,
            connected_surfaces=self.get_active_surfaces(),
        )
