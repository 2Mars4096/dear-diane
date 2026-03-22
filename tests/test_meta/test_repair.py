"""Tests for Plan 19-3: Structural Repair Engine."""

from __future__ import annotations

import json
from typing import Any

import pytest

from dan.meta.repair import (
    NoAction,
    ParameterFix,
    ParameterRepairGenerator,
    PromptFix,
    Redesign,
    RedesignResult,
    RepairActionRecord,
    RepairActionStore,
    RepairClassifier,
    RepairEscalator,
    RepairLevel,
    RedesignTrigger,
    StructuralFix,
    StructuralRepairPlanner,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakePrinciple:
    """Mimics CausalPrinciple for testing without importing the real model."""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", "p1")
        self.condition = kwargs.get("condition", "error occurred")
        self.action = kwargs.get("action", "fix the prompt")
        self.reason = kwargs.get("reason", "")
        self.confidence = kwargs.get("confidence", 0.5)
        self.workflow_id = kwargs.get("workflow_id", "wf1")
        self.repair_level = kwargs.get("repair_level", "prompt_fix")
        self.source_node_ids = kwargs.get("source_node_ids", [])
        self.suggested_parameter_changes = kwargs.get("suggested_parameter_changes", {})
        self.structural_description = kwargs.get("structural_description", "")
        self.tags = kwargs.get("tags", [])
        self.source_run_ids = kwargs.get("source_run_ids", [])

    def model_dump(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


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


# ---------------------------------------------------------------------------
# Tests: RepairLevel
# ---------------------------------------------------------------------------


class TestRepairLevel:
    def test_values(self):
        assert RepairLevel.PROMPT == 1
        assert RepairLevel.PARAMETER == 2
        assert RepairLevel.STRUCTURAL == 3
        assert RepairLevel.REDESIGN == 4

    def test_should_escalate(self):
        assert RepairLevel.should_escalate(1, 0) == RepairLevel.PROMPT
        assert RepairLevel.should_escalate(1, 2) == RepairLevel.PARAMETER
        assert RepairLevel.should_escalate(2, 2) == RepairLevel.STRUCTURAL
        assert RepairLevel.should_escalate(3, 2) == RepairLevel.REDESIGN
        assert RepairLevel.should_escalate(4, 10) == RepairLevel.REDESIGN


# ---------------------------------------------------------------------------
# Tests: RepairClassifier
# ---------------------------------------------------------------------------


class TestRepairClassifier:
    def test_classify_prompt(self):
        clf = RepairClassifier()
        p = FakePrinciple(action="improve the prompt wording", repair_level="prompt_fix")
        assert clf.classify(p) == RepairLevel.PROMPT

    def test_classify_parameter_from_text(self):
        clf = RepairClassifier()
        p = FakePrinciple(action="change the model to gpt-4", repair_level="prompt_fix")
        assert clf.classify(p) == RepairLevel.PARAMETER

    def test_classify_structural_from_text(self):
        clf = RepairClassifier()
        p = FakePrinciple(action="add node for validation step", repair_level="prompt_fix")
        assert clf.classify(p) == RepairLevel.STRUCTURAL

    def test_classify_redesign_from_text(self):
        clf = RepairClassifier()
        p = FakePrinciple(action="fundamentally wrong approach", repair_level="prompt_fix")
        assert clf.classify(p) == RepairLevel.REDESIGN

    def test_classify_respects_explicit_level(self):
        clf = RepairClassifier()
        p = FakePrinciple(action="something vague", repair_level="parameter_fix")
        assert clf.classify(p) == RepairLevel.PARAMETER

    def test_escalation_on_failures(self):
        clf = RepairClassifier()
        p = FakePrinciple(action="fix prompt", repair_level="prompt_fix")
        history = [
            RepairActionRecord(workflow_id="wf1", repair_level=1, status="failed"),
            RepairActionRecord(workflow_id="wf1", repair_level=1, status="failed"),
        ]
        assert clf.classify(p, failure_history=history) == RepairLevel.PARAMETER


# ---------------------------------------------------------------------------
# Tests: ParameterRepairGenerator
# ---------------------------------------------------------------------------


class TestParameterRepairGenerator:
    def test_generate_from_suggested(self):
        gen = ParameterRepairGenerator()
        from dan.models.graph import Graph
        graph = Graph()
        p = FakePrinciple(
            suggested_parameter_changes={"n1": {"model": "gpt-4", "temperature": 0.1}},
        )
        plan = gen.generate_mutation(p, graph)
        assert len(plan.operations) == 1
        op = plan.operations[0]
        assert op.op == "edit_node"
        assert op.updates["model"] == "gpt-4"

    def test_whitelist_enforcement(self):
        gen = ParameterRepairGenerator()
        from dan.models.graph import Graph
        p = FakePrinciple(
            suggested_parameter_changes={"n1": {"model": "gpt-4", "dangerous_key": "bad"}},
        )
        plan = gen.generate_mutation(p, Graph())
        op = plan.operations[0]
        assert "dangerous_key" not in op.updates
        assert "model" in op.updates

    def test_infer_from_action_text(self):
        gen = ParameterRepairGenerator()
        from dan.models.graph import Graph
        p = FakePrinciple(
            action="lower the temperature",
            source_node_ids=["n1"],
        )
        plan = gen.generate_mutation(p, Graph())
        assert len(plan.operations) == 1
        assert plan.operations[0].updates["temperature"] == 0.2

    def test_empty_when_no_info(self):
        gen = ParameterRepairGenerator()
        from dan.models.graph import Graph
        p = FakePrinciple(action="do something vague")
        plan = gen.generate_mutation(p, Graph())
        assert len(plan.operations) == 0


# ---------------------------------------------------------------------------
# Tests: StructuralRepairPlanner
# ---------------------------------------------------------------------------


class TestStructuralRepairPlanner:
    @pytest.mark.asyncio
    async def test_no_llm_returns_none(self):
        planner = StructuralRepairPlanner()
        from dan.models.graph import Graph
        result = await planner.plan_repair(FakePrinciple(), Graph())
        assert result is None

    @pytest.mark.asyncio
    async def test_valid_plan_from_llm(self):
        calls: list[tuple[Any, ...]] = []

        async def fake_llm(*args):
            calls.append(args)
            return json.dumps({
                "operations": [
                    {"op": "add_node", "node_type": "llm_operator", "name": "Validator",
                     "config": {"model": "gpt-4", "prompt_template": "Validate"}},
                ],
                "description": "Add validation step",
            })

        planner = StructuralRepairPlanner(llm_call=fake_llm)
        from dan.models.graph import Graph
        plan = await planner.plan_repair(FakePrinciple(), Graph())
        assert plan is not None
        assert len(plan.operations) == 1
        assert calls
        assert "Workflow Generation Contract" in str(calls[0][0])
        assert "replace_body_graph" in str(calls[0][0])
        assert '"remove_edge", "source_id"' in str(calls[0][0])
        assert '"edge_id"' not in str(calls[0][0])


# ---------------------------------------------------------------------------
# Tests: RepairActionStore
# ---------------------------------------------------------------------------


class TestRepairActionStore:
    @pytest.fixture
    def store(self):
        return RepairActionStore(FakeMemoryStore())

    @pytest.mark.asyncio
    async def test_save_and_load(self, store):
        rec = RepairActionRecord(workflow_id="wf1", repair_level=2)
        await store.save_action(rec)
        loaded = await store.load_actions("wf1")
        assert len(loaded) == 1
        assert loaded[0].repair_level == 2

    @pytest.mark.asyncio
    async def test_filter_by_level(self, store):
        await store.save_action(RepairActionRecord(workflow_id="wf1", repair_level=1))
        await store.save_action(RepairActionRecord(workflow_id="wf1", repair_level=2))
        await store.save_action(RepairActionRecord(workflow_id="wf1", repair_level=2))
        level2 = await store.get_actions_by_level("wf1", 2)
        assert len(level2) == 2


# ---------------------------------------------------------------------------
# Tests: RedesignTrigger
# ---------------------------------------------------------------------------


class TestRedesignTrigger:
    def test_no_redesign_when_few_failures(self):
        trigger = RedesignTrigger(max_structural_failures=3)
        history = [
            RepairActionRecord(repair_level=3, status="failed"),
            RepairActionRecord(repair_level=3, status="active"),
        ]
        assert not trigger.should_redesign("wf1", history)

    def test_redesign_on_structural_exhaustion(self):
        trigger = RedesignTrigger(max_structural_failures=2)
        history = [
            RepairActionRecord(repair_level=3, status="failed"),
            RepairActionRecord(repair_level=3, status="failed"),
        ]
        assert trigger.should_redesign("wf1", history)

    def test_redesign_on_consecutive_failures(self):
        trigger = RedesignTrigger()
        history = [
            RepairActionRecord(repair_level=1, status="failed"),
            RepairActionRecord(repair_level=2, status="failed"),
            RepairActionRecord(repair_level=2, status="failed"),
        ]
        assert trigger.should_redesign("wf1", history)


# ---------------------------------------------------------------------------
# Tests: RepairEscalator
# ---------------------------------------------------------------------------


class TestRepairEscalator:
    @pytest.mark.asyncio
    async def test_prompt_fix(self):
        escalator = RepairEscalator()
        p = FakePrinciple(action="improve prompt wording")
        action = await escalator.repair(p, None)
        assert isinstance(action, PromptFix)

    @pytest.mark.asyncio
    async def test_parameter_fix(self):
        escalator = RepairEscalator()
        p = FakePrinciple(action="change temperature", repair_level="parameter_fix")
        from dan.models.graph import Graph
        action = await escalator.repair(p, Graph())
        assert isinstance(action, ParameterFix)

    @pytest.mark.asyncio
    async def test_structural_no_planner(self):
        escalator = RepairEscalator()
        p = FakePrinciple(action="add validation step", repair_level="structural_fix")
        from dan.models.graph import Graph
        action = await escalator.repair(p, Graph())
        assert isinstance(action, NoAction)
        assert "not configured" in action.reason

    @pytest.mark.asyncio
    async def test_redesign(self):
        escalator = RepairEscalator()
        p = FakePrinciple(action="fundamentally wrong", repair_level="redesign")
        action = await escalator.repair(p, None)
        assert isinstance(action, Redesign)

    @pytest.mark.asyncio
    async def test_max_redesigns_cap(self):
        store = RepairActionStore(FakeMemoryStore())
        for _ in range(3):
            await store.save_action(
                RepairActionRecord(workflow_id="wf1", repair_level=4),
            )
        escalator = RepairEscalator(action_store=store, max_redesigns=2)
        p = FakePrinciple(action="fundamentally wrong", repair_level="redesign", workflow_id="wf1")
        action = await escalator.repair(p, None)
        assert isinstance(action, NoAction)
        assert "Max redesigns" in action.reason

    @pytest.mark.asyncio
    async def test_persist_action_with_action_id(self):
        store = RepairActionStore(FakeMemoryStore())
        escalator = RepairEscalator(action_store=store)
        p = FakePrinciple(action="improve prompt wording", workflow_id="wf1")
        action = await escalator.repair(p, None)
        assert isinstance(action, PromptFix)
        assert action.action_id
        loaded = await store.load_action(action.action_id)
        assert loaded is not None
        assert loaded.workflow_id == "wf1"

    @pytest.mark.asyncio
    async def test_record_outcome_marks_failure(self):
        store = RepairActionStore(FakeMemoryStore())
        escalator = RepairEscalator(action_store=store)
        p = FakePrinciple(action="improve prompt wording", workflow_id="wf1")
        action = await escalator.repair(p, None)
        await escalator.record_outcome("wf1", action.action_id, success=False)
        loaded = await store.load_action(action.action_id)
        assert loaded is not None
        assert loaded.failure_count == 1
        assert loaded.status == "failed"

    @pytest.mark.asyncio
    async def test_record_outcome_marks_success(self):
        store = RepairActionStore(FakeMemoryStore())
        escalator = RepairEscalator(action_store=store)
        p = FakePrinciple(action="change temperature", workflow_id="wf1",
                          repair_level="parameter_fix")
        from dan.models.graph import Graph
        action = await escalator.repair(p, Graph())
        await escalator.record_outcome("wf1", action.action_id, success=True)
        loaded = await store.load_action(action.action_id)
        assert loaded is not None
        assert loaded.success_count == 1
        assert loaded.status == "active"


# ---------------------------------------------------------------------------
# Tests: RedesignResult
# ---------------------------------------------------------------------------


class TestRedesignResult:
    def test_defaults(self):
        r = RedesignResult()
        assert r.new_graph == {}
        assert r.old_workflow_id == ""
        assert r.reason == ""
        assert r.changes_summary == ""

    def test_serialization(self):
        r = RedesignResult(
            new_graph={"version": "dan_graph_v1"},
            old_workflow_id="wf-old",
            reason="Fundamental approach flawed",
            changes_summary="Replaced chain with review loop",
        )
        data = r.model_dump()
        restored = RedesignResult.model_validate(data)
        assert restored.old_workflow_id == "wf-old"
        assert restored.reason == "Fundamental approach flawed"
