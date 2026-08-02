"""Tests for self-adaptive behavior infrastructure (31-22)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from dan.engine.behavior_store import (
    AdaptableParameter,
    AdaptableParameterRegistry,
    BehaviorArtifact,
    BehaviorChangeEntry,
    BehaviorChangeLog,
    BehaviorStore,
    ParameterDecisionLogger,
    PatternAccumulator,
    ThresholdCalibrator,
)
from dan.server.chat_manager import (
    generate_capability_reference,
    invalidate_capability_cache,
)
from dan.server.concierge.domain_learning import (
    apply_new_domain,
    detect_domain,
    propose_domain_discoveries,
    propose_keyword_expansion,
)
from dan.engine.adaptation_registry import AdaptationRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_behavior_dir(tmp_path):
    return tmp_path / "behavior"


@pytest.fixture
def store(tmp_behavior_dir):
    return BehaviorStore(
        base_path=tmp_behavior_dir,
        seed_defaults={
            "heuristics/test.threshold": 0.85,
            "prompts/test.system": "You are a test assistant.",
        },
    )


@pytest.fixture
def changelog(tmp_behavior_dir):
    return BehaviorChangeLog(base_path=tmp_behavior_dir)


@pytest.fixture
def registry():
    return AdaptableParameterRegistry()


@pytest.fixture
def param_low_risk():
    return AdaptableParameter(
        key="heuristics/test.threshold",
        category="thresholds",
        evidence_type="parameter_decision",
        min_evidence_count=5,
        risk_level="low",
        bounds={"min": 0.1, "max": 1.0},
    )


@pytest.fixture
def param_high_risk():
    return AdaptableParameter(
        key="heuristics/important.threshold",
        category="thresholds",
        evidence_type="parameter_decision",
        min_evidence_count=20,
        risk_level="high",
        bounds={"min": 0.5, "max": 0.99},
    )


# ===========================================================================
# 1. BehaviorArtifact
# ===========================================================================


class TestBehaviorArtifact:
    def test_defaults(self):
        a = BehaviorArtifact(key="heuristics/x", value=42)
        assert a.version == 1
        assert a.evidence == []
        assert a.previous_versions == []
        assert a.updated_at > 0

    def test_serialization_roundtrip(self):
        a = BehaviorArtifact(
            key="heuristics/x",
            value={"nested": True},
            version=3,
            evidence=["ev1"],
            previous_versions=[{"value": 1, "version": 1, "updated_at": 100.0}],
        )
        dumped = a.model_dump(mode="json")
        restored = BehaviorArtifact.model_validate(dumped)
        assert restored.key == a.key
        assert restored.value == a.value
        assert restored.version == a.version
        assert restored.evidence == a.evidence
        assert len(restored.previous_versions) == 1


# ===========================================================================
# 2. BehaviorStore
# ===========================================================================


class TestBehaviorStore:
    def test_get_returns_seed_default(self, store):
        assert store.get("heuristics/test.threshold") == 0.85

    def test_get_returns_explicit_default_when_no_seed(self, store):
        assert store.get("heuristics/unknown", 99) == 99

    def test_get_returns_file_value(self, store):
        store.set("heuristics/test.threshold", 0.90, reason="tuned")
        assert store.get("heuristics/test.threshold") == 0.90

    def test_set_creates_file(self, store, tmp_behavior_dir):
        store.set("heuristics/test.threshold", 0.75)
        path = tmp_behavior_dir / "heuristics" / "test.threshold.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["value"] == 0.75

    def test_set_returns_artifact(self, store):
        art = store.set("heuristics/test.threshold", 0.75, reason="initial")
        assert isinstance(art, BehaviorArtifact)
        assert art.value == 0.75
        assert art.version == 1

    def test_set_preserves_previous_versions(self, store):
        store.set("heuristics/test.threshold", 0.80)
        store.set("heuristics/test.threshold", 0.85)
        art = store.set("heuristics/test.threshold", 0.90)
        assert art.version == 3
        assert len(art.previous_versions) == 2
        assert art.previous_versions[0]["value"] == 0.80
        assert art.previous_versions[1]["value"] == 0.85

    def test_set_caps_previous_versions_at_10(self, store):
        for i in range(15):
            store.set("heuristics/test.threshold", float(i))
        art = store.get_artifact("heuristics/test.threshold")
        assert art is not None
        assert len(art.previous_versions) <= 10

    def test_get_artifact_returns_full_history(self, store):
        store.set("heuristics/test.threshold", 0.80)
        store.set("heuristics/test.threshold", 0.85)
        art = store.get_artifact("heuristics/test.threshold")
        assert art is not None
        assert art.value == 0.85
        assert art.version == 2
        assert len(art.previous_versions) == 1

    def test_get_artifact_returns_none_when_no_file(self, store):
        assert store.get_artifact("heuristics/nonexistent") is None

    def test_revert_restores_previous(self, store):
        store.set("heuristics/test.threshold", 0.80)
        store.set("heuristics/test.threshold", 0.90)
        reverted = store.revert("heuristics/test.threshold")
        assert reverted.value == 0.80
        assert store.get("heuristics/test.threshold") == 0.80

    def test_revert_raises_when_no_previous(self, store):
        store.set("heuristics/test.threshold", 0.80)
        with pytest.raises(KeyError, match="No previous version"):
            store.revert("heuristics/test.threshold")

    def test_revert_raises_when_no_file(self, store):
        with pytest.raises(KeyError, match="No previous version"):
            store.revert("heuristics/test.threshold")

    def test_list_keys(self, store):
        store.set("heuristics/a", 1)
        store.set("heuristics/b", 2)
        store.set("prompts/sys", "hello")
        keys = store.list_keys()
        assert "heuristics/a" in keys
        assert "heuristics/b" in keys
        assert "prompts/sys" in keys

    def test_list_keys_category_filter(self, store):
        store.set("heuristics/a", 1)
        store.set("prompts/sys", "hello")
        keys = store.list_keys(category="heuristics")
        assert "heuristics/a" in keys
        assert "prompts/sys" not in keys

    def test_list_changes_since_hours(self, store):
        store.set("heuristics/a", 1)
        store.set("heuristics/b", 2)
        changes = store.list_changes(since_hours=1.0)
        assert len(changes) == 2

    def test_list_changes_excludes_old(self, store):
        art = store.set("heuristics/a", 1)
        path = store._file_path("heuristics", "a")
        data = json.loads(path.read_text())
        data["updated_at"] = time.time() - 48 * 3600
        path.write_text(json.dumps(data))
        store._cache.pop("heuristics/a", None)
        changes = store.list_changes(since_hours=24.0)
        assert len(changes) == 0

    def test_hot_reload(self, store, tmp_behavior_dir):
        store.set("heuristics/test.threshold", 0.80)
        assert store.get("heuristics/test.threshold") == 0.80

        path = tmp_behavior_dir / "heuristics" / "test.threshold.json"
        data = json.loads(path.read_text())
        data["value"] = 0.99
        time.sleep(0.05)
        path.write_text(json.dumps(data))

        assert store.get("heuristics/test.threshold") == 0.99

    def test_thread_safety(self, store):
        errors: list[Exception] = []

        def writer(n: int):
            try:
                for i in range(20):
                    store.set("heuristics/concurrent", n * 100 + i)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(40):
                    store.get("heuristics/concurrent", 0)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(1,)),
            threading.Thread(target=writer, args=(2,)),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        val = store.get("heuristics/concurrent")
        assert isinstance(val, int)

    def test_invalid_key_format(self, store):
        with pytest.raises(ValueError, match="category/name"):
            store.get("noslash")


# ===========================================================================
# 3. AdaptableParameter + Registry
# ===========================================================================


class TestAdaptableParameterRegistry:
    def test_register_and_get_roundtrip(self, registry, param_low_risk):
        registry.register(param_low_risk)
        retrieved = registry.get_by_key("heuristics/test.threshold")
        assert retrieved is not None
        assert retrieved.key == param_low_risk.key
        assert retrieved.risk_level == "low"

    def test_get_by_key_returns_none(self, registry):
        assert registry.get_by_key("nonexistent") is None

    def test_list_by_category(self, registry, param_low_risk, param_high_risk):
        registry.register(param_low_risk)
        registry.register(param_high_risk)
        thresholds = registry.list_by_category("thresholds")
        assert len(thresholds) == 2
        prompts = registry.list_by_category("prompts")
        assert len(prompts) == 0

    def test_list_by_risk(self, registry, param_low_risk, param_high_risk):
        registry.register(param_low_risk)
        registry.register(param_high_risk)
        low = registry.list_by_risk("low")
        assert len(low) == 1
        assert low[0].key == param_low_risk.key
        high = registry.list_by_risk("high")
        assert len(high) == 1

    def test_has_sufficient_evidence_true(self, registry, param_low_risk):
        registry.register(param_low_risk)
        assert registry.has_sufficient_evidence("heuristics/test.threshold", 10)

    def test_has_sufficient_evidence_false(self, registry, param_low_risk):
        registry.register(param_low_risk)
        assert not registry.has_sufficient_evidence("heuristics/test.threshold", 3)

    def test_has_sufficient_evidence_unknown_key(self, registry):
        assert not registry.has_sufficient_evidence("unknown/key", 100)


# ===========================================================================
# 4. BehaviorChangeLog
# ===========================================================================


class TestBehaviorChangeLog:
    def test_append_writes_jsonl(self, changelog, tmp_behavior_dir):
        entry = BehaviorChangeEntry(
            category="heuristics",
            key="heuristics/test.threshold",
            action="set",
            before_summary="0.80",
            after_summary="0.90",
        )
        changelog.append(entry)
        path = tmp_behavior_dir / "changelog.jsonl"
        assert path.exists()
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["key"] == "heuristics/test.threshold"

    def test_list_recent_filters_by_time(self, changelog):
        old = BehaviorChangeEntry(
            category="heuristics",
            key="heuristics/old",
            action="set",
            timestamp=time.time() - 48 * 3600,
        )
        recent = BehaviorChangeEntry(
            category="heuristics",
            key="heuristics/recent",
            action="set",
        )
        changelog.append(old)
        changelog.append(recent)
        results = changelog.list_recent(hours=24.0)
        assert len(results) == 1
        assert results[0].key == "heuristics/recent"

    def test_list_all(self, changelog):
        for i in range(3):
            changelog.append(BehaviorChangeEntry(
                category="heuristics",
                key=f"heuristics/k{i}",
                action="set",
            ))
        assert len(changelog.list_all()) == 3

    def test_list_all_category_filter(self, changelog):
        changelog.append(BehaviorChangeEntry(
            category="heuristics", key="heuristics/a", action="set",
        ))
        changelog.append(BehaviorChangeEntry(
            category="prompts", key="prompts/b", action="set",
        ))
        assert len(changelog.list_all(category="prompts")) == 1

    def test_get_entry_by_id(self, changelog):
        entry = BehaviorChangeEntry(
            id="abc12345",
            category="heuristics",
            key="heuristics/test",
            action="set",
        )
        changelog.append(entry)
        found = changelog.get("abc12345")
        assert found is not None
        assert found.id == "abc12345"

    def test_get_returns_none_for_missing(self, changelog):
        assert changelog.get("nonexistent") is None

    def test_list_all_empty(self, changelog):
        assert changelog.list_all() == []


# ===========================================================================
# 5. ParameterDecisionLogger
# ===========================================================================


class TestParameterDecisionLogger:
    def test_log_decision_with_store(self):
        events: list[dict] = []

        class FakeStore:
            def append(self, event):
                events.append(event)

        logger_inst = ParameterDecisionLogger(telemetry_store=FakeStore())
        logger_inst.log_decision(
            parameter_key="heuristics/test.threshold",
            parameter_value=0.85,
            decision="accept",
            outcome="success",
        )
        assert len(events) == 1
        assert events[0]["type"] == "parameter_decision"
        assert events[0]["parameter_key"] == "heuristics/test.threshold"
        assert events[0]["decision"] == "accept"

    def test_log_decision_without_store(self):
        logger_inst = ParameterDecisionLogger(telemetry_store=None)
        logger_inst.log_decision(
            parameter_key="heuristics/test.threshold",
            parameter_value=0.85,
            decision="accept",
        )

    def test_log_decision_store_with_record(self):
        events: list[dict] = []

        class RecordStore:
            def record(self, event):
                events.append(event)

        logger_inst = ParameterDecisionLogger(telemetry_store=RecordStore())
        logger_inst.log_decision("heuristics/x", 1.0, "reject")
        assert len(events) == 1


# ===========================================================================
# 6. PatternAccumulator
# ===========================================================================


class TestPatternAccumulator:
    def test_record_unrecognized_creates_cluster(self, store):
        acc = PatternAccumulator(store)
        acc.record_unrecognized("analyze equity options", ["equity", "options"], "taxonomy")
        clusters = acc.get_clusters("taxonomy", min_count=1)
        assert len(clusters) == 1
        assert clusters[0]["count"] == 1
        assert "equity" in clusters[0]["keywords"]

    def test_get_clusters_min_count_filtering(self, store):
        acc = PatternAccumulator(store)
        acc.record_unrecognized("text1", ["alpha"], "taxonomy")
        acc.record_unrecognized("text2", ["alpha"], "taxonomy")
        acc.record_unrecognized("text3", ["alpha"], "taxonomy")
        acc.record_unrecognized("text4", ["beta"], "taxonomy")
        filtered = acc.get_clusters("taxonomy", min_count=3)
        assert len(filtered) == 1
        assert filtered[0]["count"] == 3

    def test_keyword_overlap_clustering(self, store):
        acc = PatternAccumulator(store)
        acc.record_unrecognized("msg1", ["ml", "train", "model"], "taxonomy")
        acc.record_unrecognized("msg2", ["ml", "train", "data"], "taxonomy")
        all_clusters = acc.get_clusters("taxonomy", min_count=1)
        assert len(all_clusters) == 1
        assert all_clusters[0]["count"] == 2

    def test_no_overlap_creates_separate_clusters(self, store):
        acc = PatternAccumulator(store)
        acc.record_unrecognized("msg1", ["alpha", "beta"], "taxonomy")
        acc.record_unrecognized("msg2", ["gamma", "delta"], "taxonomy")
        all_clusters = acc.get_clusters("taxonomy", min_count=1)
        assert len(all_clusters) == 2

    def test_domain_category_uses_domain_key(self, store):
        acc = PatternAccumulator(store)
        acc.record_unrecognized("domain text", ["finance"], "domains")
        acc.flush()
        val = store.get("domains/unrecognized_clusters", [])
        assert len(val) == 1

    def test_unexpected_category_logs_warning(self, store, caplog):
        import logging
        acc = PatternAccumulator(store)
        with caplog.at_level(logging.WARNING):
            acc.record_unrecognized("text", ["keyword"], "invalid_category")
        assert "unexpected category" in caplog.text.lower()
        assert "invalid_category" in caplog.text
        assert "taxonomy" in caplog.text and "domain" in caplog.text


# ===========================================================================
# 7. ThresholdCalibrator
# ===========================================================================


class TestThresholdCalibrator:
    @pytest.fixture
    def calibrator_env(self, store, changelog):
        reg = AdaptableParameterRegistry()
        reg.register(AdaptableParameter(
            key="heuristics/test.threshold",
            category="thresholds",
            evidence_type="parameter_decision",
            min_evidence_count=5,
            risk_level="low",
            bounds={"min": 0.1, "max": 1.0},
        ))
        store.set("heuristics/test.threshold", 0.80)
        return ThresholdCalibrator(store, reg, changelog), reg

    def _make_decisions(self, n_success: int, n_fail: int) -> list[dict]:
        decisions = [{"outcome": "success"} for _ in range(n_success)]
        decisions += [{"outcome": "failure"} for _ in range(n_fail)]
        return decisions

    def test_analyze_returns_proposal(self, calibrator_env):
        cal, _ = calibrator_env
        decisions = self._make_decisions(3, 7)
        result = cal.analyze("heuristics/test.threshold", decisions)
        assert result is not None
        assert result["current"] == 0.80
        assert result["proposed"] != 0.80
        assert result["evidence_count"] == 10

    def test_analyze_returns_none_high_success(self, calibrator_env):
        cal, _ = calibrator_env
        decisions = self._make_decisions(9, 1)
        result = cal.analyze("heuristics/test.threshold", decisions)
        assert result is None

    def test_analyze_returns_none_insufficient_data(self, calibrator_env):
        cal, _ = calibrator_env
        decisions = self._make_decisions(1, 1)
        result = cal.analyze("heuristics/test.threshold", decisions)
        assert result is None

    def test_analyze_returns_none_unregistered_key(self, calibrator_env):
        cal, _ = calibrator_env
        result = cal.analyze("heuristics/unknown", [{"outcome": "success"}] * 10)
        assert result is None

    def test_propose_tier_0_returns_none(self, calibrator_env):
        cal, _ = calibrator_env
        decisions = self._make_decisions(3, 7)
        result = cal.propose("heuristics/test.threshold", decisions, tier=0)
        assert result is None

    def test_propose_tier_1_returns_proposal(self, calibrator_env):
        cal, _ = calibrator_env
        decisions = self._make_decisions(3, 7)
        result = cal.propose("heuristics/test.threshold", decisions, tier=1)
        assert result is not None
        assert "proposed" in result

    def test_propose_tier_2_auto_applies(self, calibrator_env, store, changelog):
        cal, _ = calibrator_env
        decisions = self._make_decisions(3, 7)
        result = cal.propose("heuristics/test.threshold", decisions, tier=2)
        assert result is not None
        new_val = store.get("heuristics/test.threshold")
        assert new_val == result["proposed"]
        entries = changelog.list_all()
        assert len(entries) == 1
        assert entries[0].action == "calibrate"

    def test_bounded_change_respects_20pct(self, calibrator_env):
        cal, _ = calibrator_env
        result = cal._apply_bounded_change("heuristics/test.threshold", 1.0, 2.0)
        assert result <= 1.0 * 1.20 + 1e-9

    def test_bounded_change_respects_bounds(self, calibrator_env):
        cal, _ = calibrator_env
        result = cal._apply_bounded_change("heuristics/test.threshold", 0.95, 1.5)
        assert result <= 1.0

    def test_bounded_change_lower_bound(self, calibrator_env):
        cal, _ = calibrator_env
        result = cal._apply_bounded_change("heuristics/test.threshold", 0.15, -1.0)
        assert result >= 0.1




# ===========================================================================
# 14-5: ThresholdCalibrator — extended tests
# ===========================================================================


class TestThresholdCalibratorExtended:
    """Extended ThresholdCalibrator tests: analysis fields, tier behavior, bounds."""

    @pytest.fixture
    def cal_env(self, store, changelog):
        reg = AdaptableParameterRegistry()
        reg.register(AdaptableParameter(
            key="heuristics/test.threshold",
            category="thresholds",
            evidence_type="parameter_decision",
            min_evidence_count=5,
            risk_level="low",
            bounds={"min": 0.1, "max": 1.0},
        ))
        reg.register(AdaptableParameter(
            key="heuristics/tight.param",
            category="thresholds",
            evidence_type="parameter_decision",
            min_evidence_count=5,
            risk_level="low",
            bounds={"min": 0.70, "max": 0.80},
        ))
        store.set("heuristics/test.threshold", 0.80)
        store.set("heuristics/tight.param", 0.75)
        return ThresholdCalibrator(store, reg, changelog), reg

    @staticmethod
    def _decisions(n_ok: int, n_fail: int) -> list[dict]:
        return [{"outcome": "success"}] * n_ok + [{"outcome": "failure"}] * n_fail

    def test_analyze_contains_success_rate_and_evidence_count(self, cal_env):
        cal, _ = cal_env
        result = cal.analyze("heuristics/test.threshold", self._decisions(4, 6))
        assert result is not None
        assert "success_rate" in result
        assert result["success_rate"] == pytest.approx(0.4, abs=0.01)
        assert result["evidence_count"] == 10

    def test_propose_tier_0_logs_only(self, cal_env, monkeypatch):
        monkeypatch.setenv("DAN_LEARNING_TIER", "0")
        cal, _ = cal_env
        result = cal.propose("heuristics/test.threshold", self._decisions(3, 7), tier=0)
        assert result is None

    def test_propose_tier_1_does_not_persist(self, cal_env, store, changelog):
        cal, _ = cal_env
        result = cal.propose("heuristics/test.threshold", self._decisions(3, 7), tier=1)
        assert result is not None
        assert result["proposed"] != result["current"]
        assert store.get("heuristics/test.threshold") == 0.80
        assert changelog.list_all() == []

    def test_propose_tier_2_persists_store_and_changelog(self, cal_env, store, changelog):
        cal, _ = cal_env
        result = cal.propose("heuristics/test.threshold", self._decisions(3, 7), tier=2)
        assert result is not None
        assert store.get("heuristics/test.threshold") == result["proposed"]
        entries = changelog.list_all()
        assert len(entries) == 1
        assert entries[0].action == "calibrate"
        assert entries[0].source == "threshold_cal"

    def test_analyze_proposed_within_20pct(self, cal_env):
        cal, _ = cal_env
        result = cal.analyze("heuristics/test.threshold", self._decisions(2, 8))
        assert result is not None
        current, proposed = result["current"], result["proposed"]
        assert abs(proposed - current) <= abs(current) * 0.20 + 1e-9

    def test_bounded_change_respects_tight_bounds(self, cal_env):
        cal, _ = cal_env
        assert cal._apply_bounded_change("heuristics/tight.param", 0.75, 1.5) <= 0.80
        assert cal._apply_bounded_change("heuristics/tight.param", 0.75, 0.0) >= 0.70


# ===========================================================================
# 14-6: generate_capability_reference
# ===========================================================================


class TestGenerateCapabilityReference:

    def setup_method(self):
        invalidate_capability_cache()

    def teardown_method(self):
        invalidate_capability_cache()

    def test_returns_non_empty_string(self):
        result = generate_capability_reference()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_tool_names_from_metadata(self):
        result = generate_capability_reference()
        try:
            from dan.tools import get_all_tools
            tools = get_all_tools()
            if tools:
                assert any(tid in result for tid in tools), (
                    f"None of {list(tools.keys())[:5]} found in capability reference"
                )
        except Exception:
            assert "## Available tools" in result

    def test_contains_tool_use_section(self):
        result = generate_capability_reference()
        assert "## Tool Use" in result or "## Available tools" in result

    def test_invalidate_causes_regeneration(self):
        first = generate_capability_reference()
        invalidate_capability_cache()
        second = generate_capability_reference()
        assert isinstance(second, str)
        assert len(second) > 0
        assert "## Tool Use" in second or "## Available tools" in second
        assert second == first




# ===========================================================================
# 14-8: Domain auto-discovery
# ===========================================================================


class TestDomainDiscovery:

    @pytest.fixture
    def domain_acc(self, store):
        acc = PatternAccumulator(store)
        for i in range(4):
            acc.record_unrecognized(
                f"process genomics sequencing data {i}",
                ["genomics", "sequencing"],
                "domains",
            )
        return acc

    def test_propose_discovers_new_domain(self, domain_acc, store, monkeypatch):
        monkeypatch.setenv("DAN_LEARNING_TIER", "0")
        proposals = propose_domain_discoveries(domain_acc, behavior_store=store)
        assert len(proposals) >= 1
        assert proposals[0]["count"] >= 3

    def test_existing_domains_not_proposed(self, store, monkeypatch):
        monkeypatch.setenv("DAN_LEARNING_TIER", "0")
        acc = PatternAccumulator(store)
        for i in range(4):
            acc.record_unrecognized(
                f"equity research task {i}",
                ["equity", "research"],
                "domains",
            )
        proposals = propose_domain_discoveries(acc, behavior_store=store)
        proposed_names = [p["name"] for p in proposals]
        assert "equity_research" not in proposed_names

    def test_apply_new_domain_adds_keyword_map(self, store, monkeypatch):
        monkeypatch.setattr(
            "dan.server.concierge.domain_learning.save_domain_template",
            lambda t: None,
        )
        apply_new_domain("bioinformatics", ["genome", "protein", "sequencing"], store)
        kw_map = store.get("domains/keyword_maps")
        assert isinstance(kw_map, dict)
        assert "bioinformatics" in kw_map
        assert "genome" in kw_map["bioinformatics"]

    def test_propose_keyword_expansion_marginal_match(self, store, monkeypatch):
        monkeypatch.setenv("DAN_LEARNING_TIER", "0")
        store.set("domains/keyword_maps", {
            "test_domain": ["regression", "model"],
        })
        msg = "run a regression analysis with bootstrapping and crossvalidation"
        proposal = propose_keyword_expansion(
            "test_domain", msg, task_succeeded=True, behavior_store=store,
        )
        assert proposal is not None
        assert "new_keywords" in proposal
        assert len(proposal["new_keywords"]) > 0

    def test_propose_keyword_expansion_multiple_matches_returns_none(self, store, monkeypatch):
        monkeypatch.setenv("DAN_LEARNING_TIER", "0")
        store.set("domains/keyword_maps", {
            "test_domain": ["regression", "model"],
        })
        msg = "fit a regression model to the training data"
        proposal = propose_keyword_expansion(
            "test_domain", msg, task_succeeded=True, behavior_store=store,
        )
        assert proposal is None




# ===========================================================================
# Integration tests (14-9 through 14-13)
# ===========================================================================


class TestIntegrationPromptVariantLifecycle:
    """14-9: Correction signals → prompt variant proposed → approve → no regression."""

    def test_prompt_correction_to_proposal_to_approval(self, tmp_behavior_dir, monkeypatch):
        monkeypatch.setenv("DAN_PROMPT_OPTIMIZATION", "1")
        monkeypatch.setenv("DAN_LEARNING_TIER", "1")

        from dan.engine.outcome_trackers import PromptTracker
        from dan.engine.memory_kernel import MemoryKernel

        mk = MemoryKernel(base_dir=str(tmp_behavior_dir / "memory"))
        tracker = PromptTracker(mk)
        store = BehaviorStore(base_path=tmp_behavior_dir)
        changelog = BehaviorChangeLog(base_path=tmp_behavior_dir)
        registry = AdaptationRegistry(changelog=changelog)

        prompt_key = "prompts/test.system"
        store.register_seed(prompt_key, "You are a helpful assistant.")

        for i in range(25):
            outcome = i >= 20
            tracker.record_system_prompt_outcome(prompt_key, outcome, quality_score=1.0 if outcome else 0.0)

        proposal = tracker.propose_prompt_variant(
            prompt_key,
            adaptation_registry=registry,
            min_negative_signals=15,
        )
        assert proposal is not None
        assert proposal["negative_count"] >= 15
        assert proposal["proposal_id"] is not None

        pending = registry.list_pending()
        assert len(pending) == 1
        assert pending[0].parameter_key == prompt_key

        cid = pending[0].id
        registry.approve(cid, baseline_quality=0.2)

        applied = registry.list_applied()
        assert len(applied) == 1
        assert applied[0].status == "applied"

        for i in range(20):
            registry.record_post_adaptation_outcome(cid, quality_metric=0.7, interaction_count=i + 1)
        candidate = registry.get(cid)
        assert candidate.status == "applied"

        regression = tracker.check_prompt_regression(
            prompt_key,
            baseline_failure_rate=0.8,
            window_size=25,
        )
        assert regression is None

        for _ in range(30):
            tracker.record_system_prompt_outcome(prompt_key, outcome=False, quality_score=0.0)

        regression_detected = tracker.check_prompt_regression(
            prompt_key,
            baseline_failure_rate=0.3,
            window_size=30,
        )
        assert regression_detected is not None
        assert regression_detected["regression_detected"] is True
        assert regression_detected["current_failure_rate"] > 0.3 + 0.15




class TestIntegrationDomainDiscovery:
    """14-12: New domain tasks → proposed → approved → keyword map + template."""

    def test_domain_discovery_lifecycle(self, tmp_behavior_dir, monkeypatch):
        monkeypatch.setenv("DAN_LEARNING_TIER", "1")
        monkeypatch.setattr(
            "dan.server.concierge.domain_learning.save_domain_template",
            lambda t: None,
        )

        store = BehaviorStore(base_path=tmp_behavior_dir)
        changelog = BehaviorChangeLog(base_path=tmp_behavior_dir)
        registry = AdaptationRegistry(changelog=changelog)
        acc = PatternAccumulator(store)

        store.register_seed("domains/keyword_maps", {
            "paper_rendering": ["paper", "latex", "manuscript"],
            "data_analysis": ["data", "analysis", "statistics"],
        })

        for i in range(5):
            acc.record_unrecognized(
                f"optimize portfolio allocation for client {i} using monte carlo",
                ["portfolio", "allocation", "monte", "carlo"],
                "domain",
            )
        acc.flush()

        proposals = propose_domain_discoveries(acc, behavior_store=store, adaptation_registry=registry)
        assert len(proposals) >= 1
        assert proposals[0]["count"] >= 3

        pending = registry.list_pending()
        assert len(pending) >= 1
        candidate = pending[0]
        assert candidate.source == "domain_discovery"

        domain_name = proposals[0]["name"]
        registry.approve(candidate.id, baseline_quality=0.5)

        apply_new_domain(domain_name, proposals[0]["keywords"], store)

        kw_map = store.get("domains/keyword_maps")
        assert domain_name in kw_map
        assert len(kw_map[domain_name]) >= 2

        assert "paper_rendering" in kw_map
        assert "data_analysis" in kw_map

        detected = detect_domain(
            "optimize portfolio allocation using monte carlo simulation",
            behavior_store=store,
        )
        assert detected == domain_name


