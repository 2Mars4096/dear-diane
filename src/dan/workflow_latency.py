"""Lightweight stage timing helpers for workflow-continuation paths."""

from __future__ import annotations

import time
from typing import Any

WF_STAGE_NAMES = frozenset(
    {
        "wf_route",
        "wf_resolve",
        "wf_context",
        "wf_mutate_compile",
        "wf_dryrun",
        "wf_dispatch",
    }
)


def workflow_stage_start_ns() -> int:
    return time.perf_counter_ns()


def workflow_stage_sample(
    stage: str,
    started_ns: int,
    *,
    metadata: dict[str, Any] | None = None,
    finished_ns: int | None = None,
) -> dict[str, Any]:
    end_ns = finished_ns if finished_ns is not None else time.perf_counter_ns()
    sample = {
        "stage": stage,
        "duration_ms": round(max(0, end_ns - started_ns) / 1_000_000, 3),
    }
    if metadata:
        sample["metadata"] = metadata
    return sample


def append_workflow_stage_sample(
    target: dict[str, Any],
    stage: str,
    started_ns: int,
    *,
    metadata: dict[str, Any] | None = None,
    key: str = "workflow_stage_timings",
) -> dict[str, Any]:
    samples = list(target.get(key) or [])
    samples.append(workflow_stage_sample(stage, started_ns, metadata=metadata))
    target[key] = samples
    return target


__all__ = [
    "WF_STAGE_NAMES",
    "append_workflow_stage_sample",
    "workflow_stage_sample",
    "workflow_stage_start_ns",
]
