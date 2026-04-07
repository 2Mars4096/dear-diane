"""Compatibility wrapper for workflow stage timing helpers."""

from dan.workflow_latency import (
    WF_STAGE_NAMES,
    append_workflow_stage_sample,
    workflow_stage_sample,
    workflow_stage_start_ns,
)

__all__ = [
    "WF_STAGE_NAMES",
    "append_workflow_stage_sample",
    "workflow_stage_sample",
    "workflow_stage_start_ns",
]
