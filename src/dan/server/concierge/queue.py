"""Deprecated: queueing is now handled entirely by ConcurrentDispatcher.

This module is kept as a pass-through wrapper for backward compatibility.
All methods are no-ops that return safe defaults.
"""
from __future__ import annotations

import warnings
from enum import Enum
from typing import Any

from .models import SurfaceMessage


class QueueDecision(str, Enum):
    IMMEDIATE = "immediate"
    QUEUED = "queued"
    PARALLEL = "parallel"


class ProjectMessageQueue:
    """Deprecated pass-through — all queueing is in ConcurrentDispatcher."""

    def __init__(self, **kwargs: Any) -> None:
        pass

    def mark_active(self, surface_id: str, project_id: str) -> None:
        pass

    def mark_completed(self, surface_id: str, project_id: str) -> None:
        pass

    def enqueue(self, msg: Any, context: Any, classification: Any) -> QueueDecision:
        return QueueDecision.IMMEDIATE

    def drain(self, project_id: str, task_id: str, surface_id: str) -> list[SurfaceMessage]:
        return []

    def drain_overflow(self, surface_id: str) -> list[SurfaceMessage]:
        return []

    def peek(self, surface_id: str) -> list[SurfaceMessage]:
        return []
