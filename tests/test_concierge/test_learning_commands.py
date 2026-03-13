"""Tests for /corrections and /adaptations command handlers (31-15).

Skipped: the concierge.learning module was deleted in plan-34.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="dan.server.concierge.learning module deleted in plan-34")


class TestCorrectionsCommand:
    def test_empty_store(self) -> None:
        store = CorrectionStore()
        result = handle_corrections_command("/corrections", store)
        assert "No corrections" in result

    def test_with_records(self) -> None:
        store = CorrectionStore()
        store.add(CorrectionRecord(
            signal=CorrectionSignal(
                correction_type="negation",
                confidence=0.9,
                original_output="Bad output",
                correction_text="No, not that",
            ),
            actions=[{"type": "negative_evidence", "scope": "negation", "decrement": 0.1}],
        ))
        store.add(CorrectionRecord(
            signal=CorrectionSignal(
                correction_type="preference",
                confidence=0.9,
                extracted_preference="always use Stata",
            ),
            actions=[
                {"type": "preference", "value": "always use Stata"},
                {"type": "negative_evidence", "scope": "preference", "decrement": 0.1},
            ],
        ))
        result = handle_corrections_command("/corrections", store)
        assert "2 total" in result
        assert "negation" in result
        assert "preference" in result
        assert "Stata" in result

    def test_shows_principle(self) -> None:
        store = CorrectionStore()
        store.add(CorrectionRecord(
            signal=CorrectionSignal(
                correction_type="style",
                confidence=0.9,
                extracted_principle="summarize first",
            ),
            actions=[{"type": "principle", "value": "summarize first"}],
        ))
        result = handle_corrections_command("/corrections", store)
        assert "Principle:" in result
        assert "summarize first" in result

    def test_confidence_displayed(self) -> None:
        store = CorrectionStore()
        store.add(CorrectionRecord(
            signal=CorrectionSignal(correction_type="redo", confidence=0.7),
            actions=[],
        ))
        result = handle_corrections_command("/corrections", store)
        assert "70%" in result


class TestAdaptationsCommand:
    def test_empty_registry(self) -> None:
        reg = AdaptationRegistry()
        result = handle_adaptations_command("/adaptations", reg)
        assert "No adaptations" in result

    def test_pending_adaptations(self) -> None:
        reg = AdaptationRegistry()
        reg.add(AdaptationCandidate(
            source="prompt_opt",
            confidence=0.85,
            sample_size=15,
            scope="node_type:llm",
            description="Use structured output prompt",
        ))
        result = handle_adaptations_command("/adaptations", reg)
        assert "Pending" in result
        assert "prompt_opt" in result
        assert "85%" in result
        assert "15 samples" in result
        assert "node_type:llm" in result

    def test_applied_adaptations(self) -> None:
        reg = AdaptationRegistry()
        c = AdaptationCandidate(
            source="model_rec",
            confidence=0.9,
            sample_size=20,
            scope="global",
            description="Switch default model",
        )
        reg.add(c)
        reg.approve(c.id)
        result = handle_adaptations_command("/adaptations", reg)
        assert "Applied" in result
        assert "model_rec" in result

    def test_mixed_states(self) -> None:
        reg = AdaptationRegistry()
        c1 = AdaptationCandidate(
            source="prompt_opt", confidence=0.7, description="Pending one",
        )
        c2 = AdaptationCandidate(
            source="topology_adv", confidence=0.8, description="Applied one",
        )
        reg.add(c1)
        reg.add(c2)
        reg.approve(c2.id)
        result = handle_adaptations_command("/adaptations", reg)
        assert "Pending" in result
        assert "Applied" in result
        assert "Pending one" in result
        assert "Applied one" in result

    def test_rejected_not_shown(self) -> None:
        reg = AdaptationRegistry()
        c = AdaptationCandidate(
            source="skill_ref", confidence=0.5, description="Rejected",
        )
        reg.add(c)
        reg.reject(c.id)
        result = handle_adaptations_command("/adaptations", reg)
        assert "No adaptations" in result


class TestLearningModuleImports:
    def test_learning_module_imports_without_engine_dependencies(self, monkeypatch) -> None:
        sys.modules.pop("dan.server.concierge.learning", None)
        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name in {
                "dan.engine.correction_memory",
                "dan.engine.adaptation_registry",
            }:
                raise ImportError("engine import boom")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        module = importlib.import_module("dan.server.concierge.learning")

        assert module is not None
        assert hasattr(module, "handle_corrections_command")
