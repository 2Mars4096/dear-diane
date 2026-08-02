"""Tests for RunStore persistence and EventLog."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from dan.server.run_store import RunStore


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    run_store = RunStore(base_dir=tmp_path)
    try:
        yield run_store
    finally:
        run_store.close()


def _make_summary(run_id: str = "run-1", workflow_id: str = "wf-a", **overrides) -> dict:
    base = {
        "run_id": run_id,
        "graph_id": workflow_id,
        "status": "completed",
        "started_at": time.time(),
        "finished_at": time.time() + 5,
        "success": True,
        "errors": {},
        "outputs": {"result": "ok"},
        "total_prompt_tokens": 100,
        "total_completion_tokens": 50,
        "total_tokens": 150,
        "total_cost": 0.002,
        "elapsed_seconds": 5.0,
        "node_statuses": {"n1": "node_completed"},
        "node_usage": {"n1": {"prompt_tokens": 100, "completion_tokens": 50}},
    }
    base.update(overrides)
    return base


class TestSummaryWriteRead:
    def test_round_trip(self, store: RunStore):
        s = _make_summary()
        store.save_summary("wf-a", "run-1", s)
        loaded = store.load_summary("wf-a", "run-1")
        assert loaded is not None
        assert loaded["run_id"] == "run-1"
        assert loaded["total_tokens"] == 150
        assert loaded["total_cost"] == 0.002

    def test_missing_returns_none(self, store: RunStore):
        assert store.load_summary("wf-x", "run-x") is None

    def test_overwrite(self, store: RunStore):
        store.save_summary("wf-a", "run-1", _make_summary(total_tokens=100))
        store.save_summary("wf-a", "run-1", _make_summary(total_tokens=200))
        loaded = store.load_summary("wf-a", "run-1")
        assert loaded["total_tokens"] == 200

    def test_corrupted_json_returns_none(self, store: RunStore, tmp_path: Path):
        d = tmp_path / "wf-a"
        d.mkdir()
        (d / "run-bad.json").write_text("not json!!!")
        assert store.load_summary("wf-a", "run-bad") is None


class TestEventLog:
    def test_append_and_load(self, store: RunStore):
        events = [
            {"event_type": "node_started", "run_id": "run-1", "node_id": "n1"},
            {"event_type": "node_completed", "run_id": "run-1", "node_id": "n1"},
            {"event_type": "node_started", "run_id": "run-1", "node_id": "n2"},
        ]
        for e in events:
            store.append_event("wf-a", "run-1", e)
        loaded = store.load_events("wf-a", "run-1")
        assert len(loaded) == 3

    def test_filter_by_node_id(self, store: RunStore):
        store.append_event("wf-a", "run-1", {"event_type": "x", "node_id": "n1"})
        store.append_event("wf-a", "run-1", {"event_type": "x", "node_id": "n2"})
        assert len(store.load_events("wf-a", "run-1", node_id="n1")) == 1

    def test_filter_by_event_type(self, store: RunStore):
        store.append_event("wf-a", "run-1", {"event_type": "started", "node_id": "n1"})
        store.append_event("wf-a", "run-1", {"event_type": "completed", "node_id": "n1"})
        assert len(store.load_events("wf-a", "run-1", event_type="completed")) == 1

    def test_missing_returns_empty(self, store: RunStore):
        assert store.load_events("wf-x", "run-x") == []

    def test_save_summary_flushes_buffered_events(self, store: RunStore):
        store.append_event("wf-a", "run-1", {"event_type": "node_started", "node_id": "n1"})
        store.save_summary("wf-a", "run-1", _make_summary())

        events_path = store._base / "wf-a" / "run-1.events.jsonl"
        assert events_path.exists()
        lines = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 1
        assert lines[0]["event_type"] == "node_started"


class TestListSummaries:
    def test_list_all(self, store: RunStore):
        store.save_summary("wf-a", "run-1", _make_summary("run-1"))
        store.save_summary("wf-a", "run-2", _make_summary("run-2"))
        store.save_summary("wf-b", "run-3", _make_summary("run-3", workflow_id="wf-b"))
        all_runs = store.list_summaries()
        assert len(all_runs) == 3

    def test_list_by_workflow(self, store: RunStore):
        store.save_summary("wf-a", "run-1", _make_summary("run-1"))
        store.save_summary("wf-b", "run-2", _make_summary("run-2", workflow_id="wf-b"))
        assert len(store.list_summaries("wf-a")) == 1

    def test_filter_by_status(self, store: RunStore):
        store.save_summary("wf-a", "run-1", _make_summary("run-1", status="completed"))
        store.save_summary("wf-a", "run-2", _make_summary("run-2", status="failed"))
        result = store.list_summaries("wf-a", status="failed")
        assert len(result) == 1
        assert result[0]["run_id"] == "run-2"

    def test_pagination(self, store: RunStore):
        for i in range(5):
            store.save_summary("wf-a", f"run-{i}", _make_summary(f"run-{i}", started_at=float(i)))
        page = store.list_summaries("wf-a", limit=2, offset=0)
        assert len(page) == 2
        page2 = store.list_summaries("wf-a", limit=2, offset=2)
        assert len(page2) == 2
        assert page[0]["run_id"] != page2[0]["run_id"]


class TestRetentionCleanup:
    def test_cleanup_old_runs(self, store: RunStore):
        old = _make_summary("run-old", started_at=time.time() - 100 * 86400)
        new = _make_summary("run-new", started_at=time.time())
        store.save_summary("wf-a", "run-old", old)
        store.append_event("wf-a", "run-old", {"event_type": "x"})
        store.save_summary("wf-a", "run-new", new)
        removed = store.cleanup(max_age_days=30)
        assert removed == 1
        assert store.load_summary("wf-a", "run-old") is None
        assert store.load_summary("wf-a", "run-new") is not None

    def test_cleanup_removes_events(self, store: RunStore, tmp_path: Path):
        old = _make_summary("run-old", started_at=time.time() - 100 * 86400)
        store.save_summary("wf-a", "run-old", old)
        store.append_event("wf-a", "run-old", {"event_type": "x"})
        store.cleanup(max_age_days=30)
        events_path = tmp_path / "wf-a" / "run-old.events.jsonl"
        assert not events_path.exists()


class TestRunRecordHydration:
    def test_from_summary_round_trip(self):
        from dan.server.run_manager import RunRecord
        summary = _make_summary(
            goal_context={"origin": "scheduler"},
            launch_request={
                "kind": "rerun_from_checkpoint",
                "inputs": None,
                "session_id": "sched-1",
                "goal_context": {"origin": "scheduler"},
                "run_policy": {"profile": "long_running"},
                "guard_action": "run",
                "source_run_id": "run-source",
                "rerun_scope": {
                    "scope_type": "single_node",
                    "target_node_id": "writer",
                },
            }
        )
        rec = RunRecord.from_summary(summary)
        assert rec.run_id == "run-1"
        assert rec.total_tokens == 150
        assert rec.total_cost == 0.002
        assert rec.elapsed_seconds == 5.0
        assert rec.result is not None
        assert rec.result.success is True
        assert rec.goal_context == {"origin": "scheduler"}
        assert rec.launch_request == {
            "kind": "rerun_from_checkpoint",
            "inputs": None,
            "session_id": "sched-1",
            "goal_context": {"origin": "scheduler"},
            "run_policy": {"profile": "long_running"},
            "guard_action": "run",
            "source_run_id": "run-source",
            "rerun_scope": {
                "scope_type": "single_node",
                "target_node_id": "writer",
            },
        }
        snap = rec.snapshot()
        assert snap["total_tokens"] == 150
        assert snap["total_cost"] == 0.002
        assert snap["goal_context"] == rec.goal_context
        assert snap["launch_request"] == rec.launch_request


class TestRunComparison:
    """Tests for the comparison logic used by the /api/runs/compare endpoint."""

    def test_compare_basic_deltas(self):
        from dan.server.run_manager import RunRecord

        sum_a = _make_summary(
            "run-a",
            total_tokens=100,
            total_cost=0.001,
            elapsed_seconds=3.0,
            node_statuses={"n1": "node_completed", "n2": "node_completed"},
            node_usage={"n1": {"total_tokens": 60, "prompt_tokens": 40, "completion_tokens": 20},
                        "n2": {"total_tokens": 40, "prompt_tokens": 30, "completion_tokens": 10}},
        )
        sum_b = _make_summary(
            "run-b",
            total_tokens=200,
            total_cost=0.003,
            elapsed_seconds=5.0,
            node_statuses={"n1": "node_completed", "n2": "node_failed"},
            node_usage={"n1": {"total_tokens": 120, "prompt_tokens": 80, "completion_tokens": 40},
                        "n2": {"total_tokens": 80, "prompt_tokens": 60, "completion_tokens": 20}},
        )

        rec_a = RunRecord.from_summary(sum_a)
        rec_b = RunRecord.from_summary(sum_b)
        snap_a = rec_a.snapshot()
        snap_b = rec_b.snapshot()

        all_nodes = sorted(set(
            list(snap_a.get("node_statuses", {})) +
            list(snap_b.get("node_statuses", {}))
        ))
        assert all_nodes == ["n1", "n2"]

        usage_a = snap_a["node_usage"]
        usage_b = snap_b["node_usage"]
        n1_delta = usage_b["n1"]["total_tokens"] - usage_a["n1"]["total_tokens"]
        assert n1_delta == 60

        assert snap_a["node_statuses"]["n2"] != snap_b["node_statuses"]["n2"]

        elapsed_delta = (snap_b["elapsed_seconds"] or 0) - (snap_a["elapsed_seconds"] or 0)
        assert elapsed_delta == 2.0

    def test_compare_missing_node_in_one_run(self):
        from dan.server.run_manager import RunRecord

        sum_a = _make_summary(
            "run-a",
            node_statuses={"n1": "node_completed"},
            node_usage={"n1": {"total_tokens": 50, "prompt_tokens": 30, "completion_tokens": 20}},
        )
        sum_b = _make_summary(
            "run-b",
            node_statuses={"n1": "node_completed", "n2": "node_completed"},
            node_usage={"n1": {"total_tokens": 50, "prompt_tokens": 30, "completion_tokens": 20},
                        "n2": {"total_tokens": 100, "prompt_tokens": 70, "completion_tokens": 30}},
        )

        snap_a = RunRecord.from_summary(sum_a).snapshot()
        snap_b = RunRecord.from_summary(sum_b).snapshot()

        all_nodes = sorted(set(
            list(snap_a.get("node_statuses", {})) +
            list(snap_b.get("node_statuses", {}))
        ))
        assert "n2" in all_nodes
        assert snap_a["node_statuses"].get("n2") is None
        assert snap_b["node_statuses"]["n2"] == "node_completed"
