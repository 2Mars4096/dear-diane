from __future__ import annotations

import json
from pathlib import Path

from dan.workflow_latency import WF_STAGE_NAMES


def test_workflow_followup_latency_baseline_covers_all_workflow_stages() -> None:
    baseline_path = (
        Path(__file__).resolve().parent / "baselines" / "workflow_followup_latency.json"
    )
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))

    assert set(payload["stages"]) == WF_STAGE_NAMES
    for stage, metrics in payload["stages"].items():
        assert metrics["p50_ms"] > 0, stage
        assert metrics["p95_ms"] >= metrics["p50_ms"], stage
