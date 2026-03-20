"""Tests for Plan 19-4: Autonomous Execution Controller."""

from __future__ import annotations

import time
from typing import Any

import pytest

from dan.meta.architect import SystemPlan, WorkflowSpec
from dan.meta.controller import (
    HumanOverride,
    MetaController,
    MetaControllerConfig,
    MetaSession,
    MetaSessionStatus,
    MetaSessionStore,
    OverrideType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeMemoryEntry:
    def __init__(self, key: str, value: Any, scope: Any = None, source_run_id: str | None = None):
        self.key = key
        self.value = value


class FakeMemoryStore:
    def __init__(self):
        self._data: dict[str, dict[str, dict[str, Any]]] = {}

    async def read(self, wf_id, sess_id, key):
        val = self._data.get(wf_id, {}).get(sess_id, {}).get(key)
        return FakeMemoryEntry(key=key, value=val) if val else None

    async def write(self, wf_id, sess_id, entry):
        self._data.setdefault(wf_id, {}).setdefault(sess_id, {})[entry.key] = entry.value

    async def delete(self, wf_id, sess_id, key):
        bucket = self._data.get(wf_id, {}).get(sess_id, {})
        if key in bucket:
            del bucket[key]
            return True
        return False

    async def list_keys(self, wf_id, sess_id):
        return list(self._data.get(wf_id, {}).get(sess_id, {}).keys())


class FakePlannerOutput:
    def __init__(self, plan=None):
        self.plan = plan or FakePlan()
        self.review = FakeReview()


class FakePlan:
    action = "GENERATE"

    def model_dump(self):
        return {"action": "GENERATE", "spec": {}, "description": "fake"}


class FakeReview:
    valid = True
    errors = []


class FakePlanner:
    def __init__(self, output=None):
        self._output = output or FakePlannerOutput()

    async def plan(self, goal, error_context=None, plan_context=None):
        return self._output


class WorkflowAwarePlanner:
    def __init__(self) -> None:
        self.plan_calls: list[dict[str, Any]] = []

    async def plan(self, goal, error_context=None, plan_context=None):
        self.plan_calls.append({
            "goal": goal,
            "plan_context": plan_context,
        })

        class _Plan:
            def __init__(self, description: str) -> None:
                self.description = description

            def model_dump(self):
                return {
                    "action": "GENERATE",
                    "spec": {},
                    "description": self.description,
                }

        class _Review:
            valid = True
            errors = []

        class _Output:
            def __init__(self, description: str) -> None:
                self.plan = _Plan(description)
                self.review = _Review()

        return _Output(goal)

    async def execute_plan(self, plan, domain=None, user_text=None):
        workflow_id = str(user_text or getattr(plan, "description", "wf")).replace(" ", "_")
        return {
            "workflow_id": workflow_id,
            "graph": {
                "nodes": [],
                "edges": [],
                "entry_points": [],
                "exit_points": [],
            },
        }


# ---------------------------------------------------------------------------
# Tests: MetaSession model
# ---------------------------------------------------------------------------


class TestMetaSession:
    def test_defaults(self):
        s = MetaSession(goal="test goal")
        assert s.status == MetaSessionStatus.PLANNING
        assert s.iteration == 0
        assert s.max_iterations == 5
        assert s.goal == "test goal"
        assert s.session_id

    def test_serialization(self):
        s = MetaSession(goal="test")
        data = s.model_dump()
        restored = MetaSession.model_validate(data)
        assert restored.goal == "test"
        assert restored.session_id == s.session_id

    def test_valid_statuses(self):
        for status in MetaSessionStatus:
            s = MetaSession(goal="t", status=status)
            assert s.status == status


# ---------------------------------------------------------------------------
# Tests: MetaControllerConfig
# ---------------------------------------------------------------------------


class TestMetaControllerConfig:
    def test_defaults(self):
        cfg = MetaControllerConfig()
        assert cfg.max_iterations == 5
        assert cfg.auto_approve_levels == [1, 2]
        assert cfg.pause_on_redesign is True
        assert cfg.timeout_seconds is None

    def test_custom(self):
        cfg = MetaControllerConfig(max_iterations=3, pause_before_execute=True)
        assert cfg.max_iterations == 3
        assert cfg.pause_before_execute is True


# ---------------------------------------------------------------------------
# Tests: MetaSessionStore
# ---------------------------------------------------------------------------


class TestMetaSessionStore:
    @pytest.fixture
    def store(self):
        return MetaSessionStore(FakeMemoryStore())

    @pytest.mark.asyncio
    async def test_save_and_load(self, store):
        s = MetaSession(goal="test")
        await store.save(s)
        loaded = await store.load(s.session_id)
        assert loaded is not None
        assert loaded.goal == "test"

    @pytest.mark.asyncio
    async def test_load_missing(self, store):
        assert await store.load("nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_sessions(self, store):
        await store.save(MetaSession(goal="a"))
        await store.save(MetaSession(goal="b"))
        sessions = await store.list_sessions()
        assert len(sessions) == 2

    @pytest.mark.asyncio
    async def test_delete(self, store):
        s = MetaSession(goal="test")
        await store.save(s)
        assert await store.delete(s.session_id)
        assert await store.load(s.session_id) is None


# ---------------------------------------------------------------------------
# Tests: MetaController
# ---------------------------------------------------------------------------


class TestMetaController:
    @pytest.mark.asyncio
    async def test_happy_path_one_iteration(self):
        """Plan → execute → success in one iteration."""
        async def run_wf(plan, session_id):
            return {"success": True, "run_id": "r1", "workflow_id": "wf1"}

        events: list[dict] = []

        async def capture_event(evt):
            events.append(evt)

        ctrl = MetaController(
            planner=FakePlanner(),
            session_store=MetaSessionStore(FakeMemoryStore()),
            run_workflow=run_wf,
            emit_event=capture_event,
        )
        session = await ctrl.run("write a paper")
        assert session.status == MetaSessionStatus.COMPLETED
        assert session.iteration == 1
        assert "r1" in session.run_history

        event_types = [e["event_type"] for e in events]
        assert "META_SESSION_STARTED" in event_types
        assert "META_SESSION_COMPLETED" in event_types

    @pytest.mark.asyncio
    async def test_no_planner_fails(self):
        ctrl = MetaController(
            session_store=MetaSessionStore(FakeMemoryStore()),
        )
        session = await ctrl.run("test")
        assert session.status == MetaSessionStatus.FAILED
        assert "No planner" in (session.error_context or "")

    @pytest.mark.asyncio
    async def test_max_iterations_enforced(self):
        """Execution always fails with principles → should hit max iterations."""
        call_count = [0]

        async def failing_run(plan, session_id):
            call_count[0] += 1
            return {
                "success": False,
                "errors": "always fails",
                "run_id": f"r{call_count[0]}",
                "principles": [{"condition": "err", "action": "fix prompt", "confidence": 0.5}],
            }

        ctrl = MetaController(
            planner=FakePlanner(),
            run_workflow=failing_run,
            session_store=MetaSessionStore(FakeMemoryStore()),
        )
        cfg = MetaControllerConfig(max_iterations=3)
        session = await ctrl.run("doomed goal", config=cfg)
        assert session.status == MetaSessionStatus.FAILED
        assert session.iteration == 3

    @pytest.mark.asyncio
    async def test_pause_before_execute(self):
        ctrl = MetaController(
            planner=FakePlanner(),
            session_store=MetaSessionStore(FakeMemoryStore()),
        )
        cfg = MetaControllerConfig(pause_before_execute=True)
        session = await ctrl.run("test", config=cfg)
        assert session.status == MetaSessionStatus.PAUSED
        assert session.paused_at_stage == "before_execute"

    @pytest.mark.asyncio
    async def test_timeout_enforcement(self):
        """Second iteration should hit timeout."""
        call_count = [0]

        async def slow_run(plan, session_id):
            import asyncio
            call_count[0] += 1
            await asyncio.sleep(0.05)
            return {
                "success": False, "run_id": f"r{call_count[0]}",
                "principles": [{"condition": "e", "action": "fix", "confidence": 0.5}],
            }

        ctrl = MetaController(
            planner=FakePlanner(),
            run_workflow=slow_run,
            session_store=MetaSessionStore(FakeMemoryStore()),
        )
        cfg = MetaControllerConfig(timeout_seconds=0.01, max_iterations=5)
        session = await ctrl.run("test", config=cfg)
        assert session.status == MetaSessionStatus.FAILED
        assert "timeout" in (session.error_context or "").lower()

    @pytest.mark.asyncio
    async def test_no_run_workflow_fails(self):
        ctrl = MetaController(
            planner=FakePlanner(),
            session_store=MetaSessionStore(FakeMemoryStore()),
        )
        session = await ctrl.run("test")
        assert session.status == MetaSessionStatus.FAILED

    @pytest.mark.asyncio
    async def test_resume_abort(self):
        mem_store = FakeMemoryStore()
        session_store = MetaSessionStore(mem_store)

        s = MetaSession(goal="test", status=MetaSessionStatus.PAUSED, paused_at_stage="before_execute")
        await session_store.save(s)

        ctrl = MetaController(session_store=session_store)
        override = HumanOverride(override_type=OverrideType.ABORT)
        result = await ctrl.resume(s.session_id, override=override)
        assert result.status == MetaSessionStatus.FAILED
        assert "Aborted" in (result.error_context or "")

    @pytest.mark.asyncio
    async def test_resume_accept_as_is(self):
        mem_store = FakeMemoryStore()
        session_store = MetaSessionStore(mem_store)

        s = MetaSession(goal="test", status=MetaSessionStatus.PAUSED)
        await session_store.save(s)

        ctrl = MetaController(session_store=session_store)
        override = HumanOverride(override_type=OverrideType.ACCEPT_AS_IS)
        result = await ctrl.resume(s.session_id, override=override)
        assert result.status == MetaSessionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_resume_nonexistent_raises(self):
        ctrl = MetaController(session_store=MetaSessionStore(FakeMemoryStore()))
        with pytest.raises(ValueError, match="not found"):
            await ctrl.resume("nonexistent")

    @pytest.mark.asyncio
    async def test_resume_not_paused_raises(self):
        mem_store = FakeMemoryStore()
        session_store = MetaSessionStore(mem_store)
        s = MetaSession(goal="test", status=MetaSessionStatus.EXECUTING)
        await session_store.save(s)

        ctrl = MetaController(session_store=session_store)
        with pytest.raises(ValueError, match="not paused"):
            await ctrl.resume(s.session_id)

    @pytest.mark.asyncio
    async def test_resume_continues_same_session(self):
        mem_store = FakeMemoryStore()
        session_store = MetaSessionStore(mem_store)
        paused = MetaSession(
            goal="finish task",
            status=MetaSessionStatus.PAUSED,
            paused_at_stage="before_execute",
            session_id="sess-1",
        )
        await session_store.save(paused)

        async def run_wf(plan, session_id):
            return {"success": True, "run_id": "r1", "workflow_id": "wf1"}

        ctrl = MetaController(
            planner=FakePlanner(),
            run_workflow=run_wf,
            session_store=session_store,
        )
        resumed = await ctrl.resume("sess-1")
        assert resumed.session_id == "sess-1"
        assert resumed.status == MetaSessionStatus.COMPLETED


    @pytest.mark.asyncio
    async def test_failed_session_saves_experience(self):
        """19-4 task 4-2: failed session persists failed experience."""
        call_count = [0]

        async def failing_run(plan, session_id):
            call_count[0] += 1
            return {
                "success": False,
                "run_id": f"r{call_count[0]}",
                "workflow_id": "wf1",
                "principles": [{"condition": "err", "action": "fix prompt", "confidence": 0.5}],
            }

        saved: list[Any] = []

        class FakeExpStore:
            _index = None

            async def load_experience(self, wf_id):
                from dan.engine.experience import WorkflowExperience
                return WorkflowExperience(workflow_id=wf_id, name="test")

            async def save_experience(self, exp):
                saved.append(exp)

        ctrl = MetaController(
            planner=FakePlanner(),
            run_workflow=failing_run,
            experience_store=FakeExpStore(),
            session_store=MetaSessionStore(FakeMemoryStore()),
        )
        cfg = MetaControllerConfig(max_iterations=2)
        session = await ctrl.run("doomed goal", config=cfg)
        assert session.status == MetaSessionStatus.FAILED
        assert len(saved) > 0
        last_exp = saved[-1]
        assert "doomed" not in last_exp.workflow_id or last_exp.failure_patterns

    @pytest.mark.asyncio
    async def test_full_loop_fail_then_succeed(self):
        """19-4 task 7-6: goal → plan → execute → fail → diagnose → repair → re-execute → succeed."""
        attempt = [0]

        async def run_wf(plan, session_id):
            attempt[0] += 1
            if attempt[0] == 1:
                return {
                    "success": False,
                    "run_id": "r1",
                    "workflow_id": "wf1",
                    "error_context": "LLM output malformed",
                    "principles": [
                        {"condition": "Output JSON invalid", "action": "improve prompt wording",
                         "confidence": 0.8, "repair_level": "prompt_fix"},
                    ],
                }
            return {"success": True, "run_id": "r2", "workflow_id": "wf1"}

        events: list[dict] = []

        async def capture_event(evt):
            events.append(evt)

        ctrl = MetaController(
            planner=FakePlanner(),
            run_workflow=run_wf,
            session_store=MetaSessionStore(FakeMemoryStore()),
            emit_event=capture_event,
        )
        session = await ctrl.run("generate a report")
        assert session.status == MetaSessionStatus.COMPLETED
        assert session.iteration == 2
        assert "r1" in session.run_history
        assert "r2" in session.run_history

        event_types = [e["event_type"] for e in events]
        assert "META_SESSION_STARTED" in event_types
        assert "META_DIAGNOSIS_STARTED" in event_types
        assert "META_SESSION_COMPLETED" in event_types

    @pytest.mark.asyncio
    async def test_experience_feedback_roundtrip(self):
        """19-4 task 7-7: successful session saves experience; verifiable afterwards."""
        saved_exps: list[Any] = []

        class TrackingExpStore:
            _index = None

            async def load_experience(self, wf_id):
                return None

            async def save_experience(self, exp):
                saved_exps.append(exp)

        async def run_wf(plan, session_id):
            return {"success": True, "run_id": "r1", "workflow_id": "wf1"}

        ctrl = MetaController(
            planner=FakePlanner(),
            run_workflow=run_wf,
            experience_store=TrackingExpStore(),
            session_store=MetaSessionStore(FakeMemoryStore()),
        )
        session = await ctrl.run("write paper")
        assert session.status == MetaSessionStatus.COMPLETED
        assert len(saved_exps) == 1
        assert saved_exps[0].workflow_id == "wf1"

    @pytest.mark.asyncio
    async def test_run_system_passes_typed_dependency_inputs_when_supported(self):
        class FakeArchitect:
            async def plan(self, goal: str) -> SystemPlan:
                return SystemPlan(
                    name="reporting_system",
                    workflows=[
                        WorkflowSpec(
                            name="ingest",
                            goal="ingest goal",
                            outputs=[{"name": "dataset", "type": "object"}],
                        ),
                        WorkflowSpec(
                            name="report",
                            goal="report goal",
                            inputs=[{"name": "dataset", "type": "object"}],
                            depends_on=["ingest"],
                            trigger="upstream-output",
                        ),
                    ],
                )

        planner = WorkflowAwarePlanner()
        run_calls: list[dict[str, Any]] = []

        async def run_wf(plan, session_id, **kwargs):
            run_calls.append({
                "description": getattr(plan, "description", ""),
                "kwargs": kwargs,
            })
            if getattr(plan, "description", "") == "ingest goal":
                return {
                    "success": True,
                    "status": "completed",
                    "run_id": "run-ingest",
                    "workflow_id": "wf-ingest",
                    "outputs": {"dataset": {"rows": 3}, "ignored": "extra"},
                }
            return {
                "success": True,
                "status": "completed",
                "run_id": "run-report",
                "workflow_id": "wf-report",
                "outputs": {"report": "done"},
            }

        ctrl = MetaController(
            planner=planner,
            architect=FakeArchitect(),
            run_workflow=run_wf,
            session_store=MetaSessionStore(FakeMemoryStore()),
        )

        session = await ctrl.run_system("build a report")

        assert session.status == MetaSessionStatus.COMPLETED
        assert len(run_calls) == 2
        assert run_calls[1]["kwargs"]["workflow_inputs"] == {"dataset": {"rows": 3}}
        assert run_calls[1]["kwargs"]["upstream_handoffs"]["ingest"]["outputs"]["dataset"] == {"rows": 3}
        assert "## Upstream Results" in planner.plan_calls[1]["plan_context"]["upstream_handoffs"]

    @pytest.mark.asyncio
    async def test_run_system_falls_back_to_legacy_run_workflow_signature(self):
        class FakeArchitect:
            async def plan(self, goal: str) -> SystemPlan:
                return SystemPlan(
                    name="two_step_system",
                    workflows=[
                        WorkflowSpec(name="one", goal="one goal"),
                        WorkflowSpec(name="two", goal="two goal", depends_on=["one"]),
                    ],
                )

        planner = WorkflowAwarePlanner()
        calls: list[str] = []

        async def run_wf(plan, session_id):
            calls.append(getattr(plan, "description", ""))
            return {
                "success": True,
                "status": "completed",
                "run_id": f"run-{len(calls)}",
                "workflow_id": f"wf-{len(calls)}",
                "outputs": {"step": len(calls)},
            }

        ctrl = MetaController(
            planner=planner,
            architect=FakeArchitect(),
            run_workflow=run_wf,
            session_store=MetaSessionStore(FakeMemoryStore()),
        )

        session = await ctrl.run_system("legacy system")

        assert session.status == MetaSessionStatus.COMPLETED
        assert calls == ["one goal", "two goal"]

    @pytest.mark.asyncio
    async def test_run_system_passes_supported_subset_of_new_kwargs(self):
        class FakeArchitect:
            async def plan(self, goal: str) -> SystemPlan:
                return SystemPlan(
                    name="subset_system",
                    workflows=[
                        WorkflowSpec(
                            name="ingest",
                            goal="ingest goal",
                            outputs=[{"name": "dataset", "type": "object"}],
                        ),
                        WorkflowSpec(
                            name="report",
                            goal="report goal",
                            inputs=[{"name": "dataset", "type": "object"}],
                            depends_on=["ingest"],
                        ),
                    ],
                )

        planner = WorkflowAwarePlanner()
        seen_inputs: list[dict[str, Any] | None] = []

        async def run_wf(plan, session_id, workflow_inputs=None):
            seen_inputs.append(workflow_inputs)
            if getattr(plan, "description", "") == "ingest goal":
                return {
                    "success": True,
                    "status": "completed",
                    "run_id": "run-ingest",
                    "workflow_id": "wf-ingest",
                    "outputs": {"dataset": {"rows": 1}},
                }
            return {
                "success": True,
                "status": "completed",
                "run_id": "run-report",
                "workflow_id": "wf-report",
                "outputs": {"report": "done"},
            }

        ctrl = MetaController(
            planner=planner,
            architect=FakeArchitect(),
            run_workflow=run_wf,
            session_store=MetaSessionStore(FakeMemoryStore()),
        )

        session = await ctrl.run_system("subset system")

        assert session.status == MetaSessionStatus.COMPLETED
        assert seen_inputs == [None, {"dataset": {"rows": 1}}]

    @pytest.mark.asyncio
    async def test_run_system_reuses_prepared_graph_and_tracks_actual_workflow_ids(self):
        class FakeArchitect:
            async def plan(self, goal: str) -> SystemPlan:
                return SystemPlan(
                    name="prepared_system",
                    workflows=[WorkflowSpec(name="one", goal="one goal")],
                )

        planner = WorkflowAwarePlanner()
        seen_prepared: list[dict[str, Any] | None] = []
        seen_prepared_ids: list[str | None] = []

        async def run_wf(plan, session_id, prepared_graph=None, prepared_workflow_id=None):
            seen_prepared.append(prepared_graph)
            seen_prepared_ids.append(prepared_workflow_id)
            return {
                "success": True,
                "status": "completed",
                "run_id": "run-1",
                "workflow_id": "actual-workflow-id",
                "outputs": {"done": True},
            }

        ctrl = MetaController(
            planner=planner,
            architect=FakeArchitect(),
            run_workflow=run_wf,
            session_store=MetaSessionStore(FakeMemoryStore()),
        )

        session = await ctrl.run_system("prepared graph system")

        assert session.status == MetaSessionStatus.COMPLETED
        assert isinstance(seen_prepared[0], dict)
        assert seen_prepared_ids == ["one_goal"]
        assert session.workflow_ids == ["actual-workflow-id"]
        assert session.system_manifest["workflow_ids"] == ["actual-workflow-id"]


# ---------------------------------------------------------------------------
# Tests: HumanOverride
# ---------------------------------------------------------------------------


class TestHumanOverride:
    def test_serialization(self):
        o = HumanOverride(override_type=OverrideType.ABORT, data={"reason": "done"})
        data = o.model_dump()
        assert data["override_type"] == "abort"
        restored = HumanOverride.model_validate(data)
        assert restored.override_type == OverrideType.ABORT
