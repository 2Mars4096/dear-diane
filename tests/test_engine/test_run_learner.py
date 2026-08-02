"""Unit tests for RunLearner — post-run learning and cross-section reinforcement (29-6 §12-2, §12-3)."""

from __future__ import annotations

import pytest

from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryKernel,
    MemoryLifecycle,
    MemoryScope,
    MemoryType,
)
from dan.engine.run_learner import RunLearner


@pytest.fixture
def kernel(tmp_path) -> MemoryKernel:
    return MemoryKernel(base_dir=str(tmp_path / "mem"))


@pytest.fixture
def learner(kernel: MemoryKernel) -> RunLearner:
    return RunLearner(kernel)


# ---------------------------------------------------------------------------
# 12-2: Post-run learning
# ---------------------------------------------------------------------------


class TestSuccessPath:
    def test_success_creates_workflow_asset(self, learner: RunLearner, kernel: MemoryKernel):
        result = learner.extract_run_learnings(
            run_result={"success": True, "graph_id": "wf-1", "run_id": "r-1"},
        )
        assert any(r["action"] == "created_asset" for r in result)
        assets = kernel.list_by_type(MemoryType.WORKFLOW_ASSET)
        assert len(assets) == 1
        assert assets[0].metadata["workflow_id"] == "wf-1"

    def test_success_updates_existing_asset(self, learner: RunLearner, kernel: MemoryKernel):
        kernel.store_workflow_asset("wf-1 asset", workflow_id="wf-1", success_rate=1.0)
        result = learner.extract_run_learnings(
            run_result={"success": True, "graph_id": "wf-1", "run_id": "r-2"},
        )
        assert any(r["action"] == "updated_asset" for r in result)
        assets = kernel.list_by_type(MemoryType.WORKFLOW_ASSET)
        assert len(assets) == 1
        assert assets[0].metadata.get("last_run_id") == "r-2"

    def test_success_reinforces_matching_pattern(self, learner: RunLearner, kernel: MemoryKernel):
        kernel.store(MemoryItem(
            content="pattern: linear",
            memory_type=MemoryType.WORKFLOW_PATTERN,
            scope=MemoryScope.USER,
            metadata={"pattern_name": "linear"},
        ))
        workflow = {
            "nodes": [
                {"id": "a", "node_type": "input"},
                {"id": "b", "node_type": "llm_operator"},
            ],
            "edges": [{"source_node_id": "a", "target_node_id": "b"}],
        }
        result = learner.extract_run_learnings(
            run_result={"success": True, "graph_id": "wf-2", "run_id": "r-1"},
            workflow=workflow,
        )
        assert any(r.get("action") == "reinforced_pattern" for r in result)


class TestFailurePath:
    def test_failure_creates_failure_pattern(self, learner: RunLearner, kernel: MemoryKernel):
        result = learner.extract_run_learnings(
            run_result={
                "success": False,
                "graph_id": "wf-1",
                "run_id": "r-1",
                "errors": {"node-a": "validation error: schema mismatch"},
            },
            workflow={
                "nodes": [{"id": "node-a", "node_type": "llm_operator"}],
                "edges": [],
            },
        )
        assert any(r["action"] == "created_failure_pattern" for r in result)
        failures = kernel.list_by_type(MemoryType.FAILURE_PATTERN)
        assert len(failures) == 1
        assert "validation" in failures[0].content.lower()

    def test_failure_boosts_matching_principle(self, learner: RunLearner, kernel: MemoryKernel):
        kernel.store(MemoryItem(
            content="Always validate schema before processing",
            memory_type=MemoryType.PRINCIPLE,
            scope=MemoryScope.GLOBAL,
            metadata={"confidence": 0.5, "error_category": "validation"},
            tags=["validation"],
        ))
        result = learner.extract_run_learnings(
            run_result={
                "success": False,
                "graph_id": "wf-1",
                "run_id": "r-1",
                "errors": {"n1": "schema validation failed"},
            },
        )
        assert any(r.get("action") == "boosted_principle" for r in result)
        principles = kernel.list_by_type(MemoryType.PRINCIPLE)
        assert float(principles[0].metadata["confidence"]) == pytest.approx(0.6, abs=0.01)

    def test_failure_no_errors_uses_fallback(self, learner: RunLearner, kernel: MemoryKernel):
        result = learner.extract_run_learnings(
            run_result={
                "success": False,
                "graph_id": "wf-1",
                "run_id": "r-1",
                "error": "timeout on node X",
            },
        )
        assert any(r["action"] == "created_failure_pattern" for r in result)
        failures = kernel.list_by_type(MemoryType.FAILURE_PATTERN)
        assert "timeout" in failures[0].content.lower()


class TestRepairPath:
    def test_repair_success_links_strategy(self, learner: RunLearner, kernel: MemoryKernel):
        fp = kernel.store_failure_pattern("wf-1 failed at node-a: timeout")
        goal_context = {
            "repair_strategy": "retry with longer timeout",
            "failure_pattern_id": fp.id,
        }
        result = learner.extract_run_learnings(
            run_result={"success": True, "graph_id": "wf-1", "run_id": "r-2"},
            goal_context=goal_context,
        )
        assert any(r.get("action") == "linked_repair" for r in result)
        updated = kernel.get(fp.id)
        assert updated.metadata.get("repair_strategy") == "retry with longer timeout"
        assert updated.metadata.get("repair_success") is True

    def test_no_link_without_repair_strategy(self, learner: RunLearner, kernel: MemoryKernel):
        result = learner.extract_run_learnings(
            run_result={"success": True, "graph_id": "wf-1", "run_id": "r-2"},
            goal_context={},
        )
        assert not any(r.get("action") == "linked_repair" for r in result)


class TestEmptyInput:
    def test_none_run_result(self, learner: RunLearner):
        assert learner.extract_run_learnings(None) == []

    def test_missing_success_field(self, learner: RunLearner):
        assert learner.extract_run_learnings({"graph_id": "wf-1"}) == []


# ---------------------------------------------------------------------------
# 12-3: Cross-section reinforcement
# ---------------------------------------------------------------------------


class TestBoostReusedAsset:
    def test_boost_asset_importance(self, learner: RunLearner, kernel: MemoryKernel):
        asset = kernel.store_workflow_asset("asset content", workflow_id="wf-1")
        original_importance = asset.importance
        result = learner.boost_reused_asset("wf-1")
        assert len(result) >= 1
        updated = kernel.get(asset.id)
        assert updated.importance > original_importance

    def test_boost_also_boosts_related_pattern(self, learner: RunLearner, kernel: MemoryKernel):
        pattern = kernel.store(MemoryItem(
            content="pattern: linear",
            memory_type=MemoryType.WORKFLOW_PATTERN,
            scope=MemoryScope.USER,
        ))
        asset = kernel.store_workflow_asset(
            "asset content", workflow_id="wf-1",
            related_ids=[pattern.id],
        )
        result = learner.boost_reused_asset("wf-1")
        assert any(r["action"] == "boosted_related_pattern" for r in result)
        updated_pattern = kernel.get(pattern.id)
        assert updated_pattern.importance > 0.5

    def test_no_boost_for_unknown_workflow(self, learner: RunLearner):
        result = learner.boost_reused_asset("nonexistent")
        assert result == []


class TestBoostPreventingPrinciple:
    def test_boost_principle_confidence(self, learner: RunLearner, kernel: MemoryKernel):
        p = kernel.store(MemoryItem(
            content="Always validate input",
            memory_type=MemoryType.PRINCIPLE,
            scope=MemoryScope.GLOBAL,
            metadata={"confidence": 0.5},
        ))
        result = learner.boost_preventing_principle(p.id)
        assert len(result) == 1
        updated = kernel.get(p.id)
        assert float(updated.metadata["confidence"]) == pytest.approx(0.6, abs=0.01)

    def test_no_boost_for_unknown_principle(self, learner: RunLearner):
        result = learner.boost_preventing_principle("nonexistent")
        assert result == []


class TestDemoteOverriddenPreference:
    def test_demote_reduces_importance(self, learner: RunLearner, kernel: MemoryKernel):
        pref = kernel.store_preference("models: drafting -> gpt-4")
        original_importance = pref.importance
        result = learner.demote_overridden_preference(pref.id, "user chose Claude")
        assert len(result) == 1
        updated = kernel.get(pref.id)
        assert updated.importance < original_importance
        assert updated.metadata.get("overridden") is True
        assert updated.metadata.get("override_reason") == "user chose Claude"

    def test_no_demote_for_unknown(self, learner: RunLearner):
        result = learner.demote_overridden_preference("nonexistent")
        assert result == []


class TestUpdateContradictedFact:
    def test_update_preserves_old_content(self, learner: RunLearner, kernel: MemoryKernel):
        fact = kernel.store_fact("deadline is Friday")
        result = learner.update_contradicted_fact(fact.id, "deadline is Monday")
        assert len(result) == 1
        updated = kernel.get(fact.id)
        assert updated.content == "deadline is Monday"
        assert updated.metadata.get("previous_content") == "deadline is Friday"
        assert "contradicted_at" in updated.metadata

    def test_no_update_for_unknown(self, learner: RunLearner):
        result = learner.update_contradicted_fact("nonexistent", "new content")
        assert result == []


# -----------------------------------------------------------------------
# Task 2-6: _maybe_extract_new_principle
# -----------------------------------------------------------------------


def _make_workflow(
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
) -> dict:
    if nodes is None:
        nodes = [
            {"id": "a", "node_type": "input"},
            {"id": "b", "node_type": "llm_operator"},
        ]
    if edges is None:
        edges = [{"source_node_id": "a", "target_node_id": "b"}]
    return {"nodes": nodes, "edges": edges}


def _make_run_result(
    success: bool = True,
    graph_id: str = "wf-test",
    run_id: str = "r-1",
    errors: dict | None = None,
    error: str | None = None,
) -> dict:
    result: dict = {"success": success, "graph_id": graph_id, "run_id": run_id}
    if errors is not None:
        result["errors"] = errors
    if error is not None:
        result["error"] = error
    return result


class TestMaybeExtractNewPrinciple:
    def test_new_error_category_extracts_principle(self, kernel: MemoryKernel, learner: RunLearner):
        """When no existing principle covers this error category, a new one is created."""
        workflow = {
            "nodes": [
                {"id": "n1", "node_type": "input"},
                {"id": "n2", "node_type": "llm"},
            ],
            "edges": [{"source_node_id": "n1", "target_node_id": "n2"}],
        }
        result = learner._maybe_extract_new_principle(
            error_str="Connection refused by remote host",
            error_category="api_error",
            workflow=workflow,
        )
        assert len(result) == 1
        assert result[0]["action"] == "extracted_principle"
        assert result[0]["error_category"] == "api_error"

        principles = kernel.list_by_type(MemoryType.PRINCIPLE)
        assert len(principles) == 1
        assert "api_error" in principles[0].content
        assert float(principles[0].metadata["confidence"]) == pytest.approx(0.3)
        assert "api_error" in principles[0].tags

    def test_covered_error_category_no_new_principle(self, kernel: MemoryKernel, learner: RunLearner):
        """When an existing principle already covers this error category, skip."""
        kernel.store_principle(
            content="Handle timeout by retrying",
            confidence=0.6,
            tags=["timeout"],
        )
        result = learner._maybe_extract_new_principle(
            error_str="Request timed out",
            error_category="timeout",
            workflow=None,
        )
        assert result == []
        principles = kernel.list_by_type(MemoryType.PRINCIPLE)
        assert len(principles) == 1

    def test_covered_by_metadata_error_category(self, kernel: MemoryKernel, learner: RunLearner):
        """Existing principle with matching error_category in metadata also counts."""
        kernel.store(MemoryItem(
            content="Validate schemas before sending",
            memory_type=MemoryType.PRINCIPLE,
            scope=MemoryScope.GLOBAL,
            metadata={"confidence": 0.5, "error_category": "validation"},
        ))
        result = learner._maybe_extract_new_principle(
            error_str="Schema validation failed",
            error_category="validation",
            workflow=None,
        )
        assert result == []

    def test_principle_includes_workflow_node_types(self, kernel: MemoryKernel, learner: RunLearner):
        """Extracted principle mentions workflow node types for context."""
        wf = {
            "nodes": [
                {"id": "n1", "node_type": "input"},
                {"id": "n2", "node_type": "llm"},
                {"id": "n3", "node_type": "tool"},
            ],
            "edges": [],
        }
        result = learner._maybe_extract_new_principle(
            error_str="Rate limit exceeded",
            error_category="rate_limit",
            workflow=wf,
        )
        assert len(result) == 1
        principles = kernel.list_by_type(MemoryType.PRINCIPLE)
        assert "input" in principles[0].content
        assert "llm" in principles[0].content

    def test_no_workflow_still_extracts(self, kernel: MemoryKernel, learner: RunLearner):
        """Principle extraction works even without a workflow dict."""
        result = learner._maybe_extract_new_principle(
            error_str="Permission denied",
            error_category="permission",
            workflow=None,
        )
        assert len(result) == 1
        principles = kernel.list_by_type(MemoryType.PRINCIPLE)
        assert "permission" in principles[0].content

    def test_wired_into_on_failure(self, kernel: MemoryKernel, learner: RunLearner):
        """_on_failure should call _maybe_extract_new_principle for uncovered categories."""
        run_result = {
            "success": False,
            "graph_id": "wf-test",
            "run_id": "r-1",
            "errors": {"node-1": "Permission denied for /tmp/secret"},
        }
        workflow = {
            "nodes": [{"id": "node-1", "node_type": "tool"}],
            "edges": [],
        }
        stored = learner.extract_run_learnings(run_result, workflow=workflow)
        extracted = [s for s in stored if s["action"] == "extracted_principle"]
        assert len(extracted) == 1
        assert extracted[0]["error_category"] == "permission"

    def test_on_failure_no_duplicate_principle(self, kernel: MemoryKernel, learner: RunLearner):
        """If a principle already exists for this error category, _on_failure skips extraction."""
        kernel.store_principle(
            content="Handle timeouts gracefully",
            confidence=0.5,
            tags=["timeout"],
        )
        run_result = {
            "success": False,
            "graph_id": "wf-x",
            "run_id": "r-1",
            "errors": {"n1": "Request timed out after 30s"},
        }
        stored = learner.extract_run_learnings(run_result)
        extracted = [s for s in stored if s["action"] == "extracted_principle"]
        assert len(extracted) == 0
        boosted = [s for s in stored if s["action"] == "boosted_principle"]
        assert len(boosted) == 1
