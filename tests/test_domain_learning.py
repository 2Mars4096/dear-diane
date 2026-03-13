"""Tests for domain learning (plan 31-21)."""

from __future__ import annotations

import asyncio
import json
import time
from contextvars import ContextVar
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from dan.engine.memory_extractor import ExtractedMemory
from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryKernel,
    MemoryLifecycle,
    MemoryScope,
    MemoryType,
)
from dan.server.chat_manager import ChatCompleteEvent
from dan.server.capability_registry import CapabilityContext
from dan.server.concierge.context_resolver import ProjectContextResolver, ResolvedContext
from dan.server.concierge.domain_learning import (
    ContextPackage,
    DomainReflector,
    DomainPatternGeneralizer,
    DomainTemplate,
    DomainTemplateUpgrader,
    DomainTemplateConsolidator,
    DomainValidator,
    check_context_sufficiency,
    create_generic_template,
    detect_domain,
    load_domain_template,
    resolve_artifact_references,
    save_domain_template,
)
from dan.server.concierge.fan_out import fan_out, fan_out_dict
from dan.server.concierge.handlers import _augmented_prompt_context, _build_prompt_from_package
from dan.server.concierge.models import PendingAction, Project, SurfaceMessage, Task, TaskTurn
from dan.server.concierge.project_store import ProjectStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_domain_item(
    content: str,
    category: str = "best_practice",
    domain: str = "paper_rendering",
    importance: float = 0.7,
    memory_type: MemoryType = MemoryType.FACT,
) -> MemoryItem:
    return MemoryItem(
        content=content,
        memory_type=memory_type,
        scope=MemoryScope.USER,
        importance=importance,
        tags=["domain_knowledge"],
        metadata={"domain": domain, "category": category},
    )


# ---------------------------------------------------------------------------
# 1. detect_domain — keyword matching
# ---------------------------------------------------------------------------


class TestDetectDomainKeywords:
    def test_paper_rendering(self):
        assert detect_domain("render this LaTeX paper with tables", None) == "paper_rendering"

    def test_data_analysis(self):
        assert detect_domain("analyze this CSV dataset", None) == "data_analysis"

    def test_workflow_building(self):
        assert detect_domain("build a workflow pipeline", None) == "workflow_building"

    def test_no_domain(self):
        assert detect_domain("hello how are you", None) is None


# ---------------------------------------------------------------------------
# 2. detect_domain — project domain override
# ---------------------------------------------------------------------------


class TestDetectDomainProjectOverride:
    def test_project_domain_takes_priority(self):
        project = Project(
            surface_id="test", label="Test Project", domain="equity_research",
        )
        result = detect_domain("render a paper", project)
        assert result == "equity_research"


# ---------------------------------------------------------------------------
# 3. Domain template CRUD
# ---------------------------------------------------------------------------


class TestDomainTemplateCrud:
    def test_create_generic_template(self):
        tmpl = create_generic_template("test_domain")
        assert isinstance(tmpl, DomainTemplate)
        assert tmpl.domain == "test_domain"
        assert "structure" in tmpl.categories
        assert "best_practice" in tmpl.categories
        assert tmpl.version == 1

    def test_save_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "dan.server.concierge.domain_learning._USER_TEMPLATE_DIR", tmp_path,
        )
        tmpl = create_generic_template("roundtrip_test")
        tmpl.checklist = ["check A", "check B"]
        save_domain_template(tmpl)

        loaded = load_domain_template("roundtrip_test")
        assert loaded is not None
        assert loaded.domain == "roundtrip_test"
        assert loaded.checklist == ["check A", "check B"]
        assert loaded.version == 1


# ---------------------------------------------------------------------------
# 4. DomainValidator
# ---------------------------------------------------------------------------


class TestDomainValidator:
    def test_warns_when_key_term_missing(self):
        items = [
            _make_domain_item("Always use booktabs for tables"),
        ]
        warnings = DomainValidator().validate(
            "\\begin{table} \\hline data \\end{table}", items,
        )
        assert len(warnings) >= 1
        assert any("booktabs" in w for w in warnings)

    def test_no_warning_when_key_term_present(self):
        items = [
            _make_domain_item("Always use booktabs for tables"),
        ]
        warnings = DomainValidator().validate(
            "\\begin{table} \\toprule booktabs \\end{table}", items,
        )
        assert len(warnings) == 0

    def test_seeded_category_alias_participates_in_validation(self):
        items = [
            MemoryItem(
                content="Always use APA bibliography format",
                memory_type=MemoryType.FACT,
                scope=MemoryScope.USER,
                metadata={"domain": "paper_rendering", "category": "journal_style"},
            ),
        ]
        warnings = DomainValidator().validate(
            "A draft with plain references only.",
            items,
        )
        assert warnings


# ---------------------------------------------------------------------------
# 5. DomainTemplateConsolidator
# ---------------------------------------------------------------------------


class TestDomainTemplateConsolidator:
    def test_consolidate_increments_version(self):
        cats = ["structure", "formatting", "tooling", "pitfall", "best_practice"]
        items = [
            _make_domain_item(f"Rule {i}", category=cats[i % len(cats)])
            for i in range(15)
        ]
        tmpl = create_generic_template("test_consolidate")
        original_version = tmpl.version

        updated = DomainTemplateConsolidator().consolidate(
            "test_consolidate", items, tmpl,
        )
        assert updated is not None
        assert updated.version == original_version + 1
        assert updated.item_count == len(items)

    def test_consolidate_tracks_coverage_gaps(self):
        items = [
            _make_domain_item("Use booktabs for tables", category="formatting"),
        ]
        tmpl = create_generic_template("coverage_test")

        updated = DomainTemplateConsolidator().consolidate(
            "coverage_test", items, tmpl,
        )

        assert updated is not None
        assert "coverage_gaps" in updated.metadata
        assert "structure" in updated.metadata["coverage_gaps"]

    def test_consolidate_merges_near_duplicates(self):
        items = [
            _make_domain_item(
                "Always use booktabs for professional latex tables",
                category="best_practice",
            ),
            _make_domain_item(
                "Always use booktabs for professional LaTeX tables",
                category="best_practice",
            ),
        ]
        tmpl = create_generic_template("dedupe_test")

        updated = DomainTemplateConsolidator().consolidate(
            "dedupe_test", items, tmpl,
        )

        assert updated is not None
        assert updated.item_count == 1

    def test_memory_kernel_consolidation_removes_duplicate_items(
        self,
        tmp_path,
        monkeypatch,
    ):
        template_dir = tmp_path / "templates"
        monkeypatch.setattr(
            "dan.server.concierge.domain_learning._USER_TEMPLATE_DIR",
            template_dir,
        )
        monkeypatch.setattr(
            "dan.engine.learning_tiers.is_feature_enabled",
            lambda feature: feature == "domain_learning",
        )

        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        for i in range(10):
            kernel.store(
                _make_domain_item(
                    "Always use booktabs for tables",
                    category="best_practice",
                    domain="paper_rendering",
                    memory_type=MemoryType.FACT,
                )
            )

        consolidated = kernel._consolidate_domain_templates()

        remaining = [
            item
            for item in kernel.list_by_type(MemoryType.FACT, limit=20)
            if item.metadata.get("domain") == "paper_rendering"
        ]
        assert consolidated == 1
        assert len(remaining) == 1

    def test_memory_kernel_consolidation_keeps_project_scoped_items_isolated(
        self,
        tmp_path,
        monkeypatch,
    ):
        template_dir = tmp_path / "templates"
        monkeypatch.setattr(
            "dan.server.concierge.domain_learning._USER_TEMPLATE_DIR",
            template_dir,
        )
        monkeypatch.setattr(
            "dan.engine.learning_tiers.is_feature_enabled",
            lambda feature: feature == "domain_learning",
        )

        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        for project_id in ("proj-a", "proj-b"):
            for _ in range(5):
                kernel.store_fact(
                    "Always use booktabs for tables",
                    tags=["domain_knowledge"],
                    project_id=project_id,
                    metadata={"domain": "paper_rendering", "category": "best_practice"},
                )

        consolidated = kernel._consolidate_domain_templates()

        remaining = [
            item
            for item in kernel.list_by_type(MemoryType.FACT, limit=20)
            if item.metadata.get("domain") == "paper_rendering"
        ]
        remaining_project_ids = {item.metadata.get("project_id") for item in remaining}
        assert consolidated == 1
        assert len(remaining) == 2
        assert remaining_project_ids == {"proj-a", "proj-b"}


# ---------------------------------------------------------------------------
# 6. DomainPatternGeneralizer
# ---------------------------------------------------------------------------


class TestDomainPatternGeneralizer:
    def test_generalize_produces_durable_patterns(self):
        base_content = "Always use booktabs package for professional table formatting in LaTeX"
        items = [
            _make_domain_item(
                base_content,
                category="best_practice",
                importance=0.6 + i * 0.01,
            )
            for i in range(15)
        ]

        patterns = DomainPatternGeneralizer().generalize("paper_rendering", items)
        assert len(patterns) >= 1
        for p in patterns:
            assert p.importance == 0.9
            assert p.lifecycle == MemoryLifecycle.DURABLE
            assert p.memory_type == MemoryType.WORKFLOW_PATTERN
            assert "generalized_pattern" in p.tags


# ---------------------------------------------------------------------------
# 7. ContextPackage model
# ---------------------------------------------------------------------------


class TestContextPackageModel:
    def test_defaults(self):
        pkg = ContextPackage()
        assert pkg.domain is None
        assert pkg.domain_expertise == ""
        assert pkg.recent_artifacts == []
        assert pkg.unresolved_references == []
        assert pkg.auto_read_content == {}

    def test_roundtrip(self):
        pkg = ContextPackage(
            domain="paper_rendering",
            domain_expertise="Use booktabs",
            recent_artifacts=[{"type": "paper", "path": "/tmp/paper.tex"}],
            unresolved_references=["dataset"],
            task_state={"step": 1},
            memory_context="some context",
            auto_read_content={"main.tex": "\\documentclass{article}"},
        )
        data = pkg.model_dump()
        restored = ContextPackage.model_validate(data)
        assert restored.domain == "paper_rendering"
        assert restored.recent_artifacts == pkg.recent_artifacts
        assert restored.auto_read_content == {"main.tex": "\\documentclass{article}"}


# ---------------------------------------------------------------------------
# 8. resolve_artifact_references
# ---------------------------------------------------------------------------


class TestResolveArtifactReferences:
    def test_resolves_paper_from_facts(self):
        facts = [
            MemoryItem(
                content="The paper is at /tmp/output/paper.tex",
                memory_type=MemoryType.FACT,
                scope=MemoryScope.PROJECT,
                metadata={"file_location": "/tmp/output/paper.tex"},
            ),
        ]
        matched, unresolved = resolve_artifact_references(
            "update the paper", facts, {}, [],
        )
        assert len(matched) == 1
        assert matched[0]["type"] == "paper"
        assert matched[0]["source"] == "project_fact"
        assert not unresolved

    def test_unresolved_when_no_match(self):
        matched, unresolved = resolve_artifact_references(
            "update the spreadsheet", [], {}, [],
        )
        assert len(unresolved) == 1
        assert "spreadsheet" in unresolved
        assert not matched


# ---------------------------------------------------------------------------
# 9. check_context_sufficiency
# ---------------------------------------------------------------------------


class TestCheckContextSufficiency:
    def test_clarification_for_unresolved_refs(self):
        pkg = ContextPackage(
            unresolved_references=["report"],
            recent_artifacts=[],
        )
        result = check_context_sufficiency(pkg, "update the report")
        assert result is not None
        assert "report" in result

    def test_none_when_context_complete(self):
        pkg = ContextPackage(
            unresolved_references=[],
            recent_artifacts=[{"type": "paper", "path": "paper.tex"}],
        )
        result = check_context_sufficiency(pkg, "update the paper")
        assert result is None


# ---------------------------------------------------------------------------
# 10. retrieve_domain_expertise (integration-style)
# ---------------------------------------------------------------------------


class TestRetrieveDomainExpertise:
    def test_retrieves_formatted_block(self, tmp_path):
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        for i in range(5):
            kernel.store(
                _make_domain_item(
                    f"Domain rule {i}: use booktabs for tables",
                    category="best_practice",
                    domain="paper_rendering",
                )
            )

        from dan.engine.memory_kernel import MemoryType, _rank_fact

        type_rankers = {
            MemoryType.FACT: (_rank_fact, 0.30),
        }

        all_scored: list[tuple[float, Any]] = []
        for mem_type, (ranker, _weight) in type_rankers.items():
            items = kernel.list_by_type(mem_type)
            for item in items:
                if "domain_knowledge" not in (item.tags or []):
                    continue
                if item.metadata.get("domain") != "paper_rendering":
                    continue
                score = ranker(item, "booktabs tables") + 0.3
                all_scored.append((min(score, 1.0), item))

        all_scored.sort(key=lambda x: x[0], reverse=True)
        assert len(all_scored) == 5

        lines = ["[Domain Expertise: paper_rendering]"]
        for _score, item in all_scored[:8]:
            cat = (item.metadata.get("category") or "general").upper()
            lines.append(f"- [{cat}] {item.content[:200]}")

        block = "\n".join(lines)
        assert "[Domain Expertise: paper_rendering]" in block
        assert "[BEST_PRACTICE]" in block

    def test_reserves_slots_for_principles(self, tmp_path):
        from dan.server.concierge.runtime import Concierge

        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        for i in range(6):
            kernel.store(
                _make_domain_item(
                    f"Formatting fact {i}: tables use latex booktabs",
                    category="formatting",
                    domain="paper_rendering",
                    memory_type=MemoryType.FACT,
                )
            )
        kernel.store(
            _make_domain_item(
                "Always use booktabs for tables",
                category="best_practice",
                domain="paper_rendering",
                memory_type=MemoryType.PRINCIPLE,
            )
        )

        concierge = Concierge.__new__(Concierge)
        concierge.memory_kernel = kernel

        block = Concierge._retrieve_domain_expertise(
            concierge,
            "booktabs tables latex",
            "paper_rendering",
            max_items=2,
            max_chars=500,
        )

        assert "[BEST_PRACTICE]" in block

    def test_filters_project_scoped_domain_items(self, tmp_path):
        from dan.server.concierge.runtime import Concierge

        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        kernel.store_fact(
            "Project A rule: always use booktabs",
            tags=["domain_knowledge"],
            project_id="proj-a",
            metadata={"domain": "paper_rendering", "category": "best_practice"},
        )
        kernel.store_fact(
            "Project B rule: never use booktabs",
            tags=["domain_knowledge"],
            project_id="proj-b",
            metadata={"domain": "paper_rendering", "category": "best_practice"},
        )

        concierge = Concierge.__new__(Concierge)
        concierge.memory_kernel = kernel

        block = Concierge._retrieve_domain_expertise(
            concierge,
            "booktabs table rules",
            "paper_rendering",
            project_id="proj-a",
            max_items=4,
            max_chars=500,
        )

        assert "Project A rule" in block
        assert "Project B rule" not in block


# ---------------------------------------------------------------------------
# 10b. DomainReflector
# ---------------------------------------------------------------------------


class TestDomainReflector:
    def test_reflect_parses_llm_output(self, monkeypatch):
        monkeypatch.setenv("DAN_DOMAIN_LEARNING", "1")

        class FakeLLM:
            def complete(self, prompt: str, max_tokens: int = 1000) -> str:
                assert "paper_rendering" in prompt
                return (
                    '[{"category":"best_practice","content":"Always use booktabs for tables",'
                    '"importance":0.95}]'
                )

        reflector = DomainReflector(llm=FakeLLM())
        turns = [
            TaskTurn(role="user", content="Render the paper"),
            TaskTurn(role="assistant", content="Used booktabs for the tables"),
        ]

        items = reflector.reflect("paper_rendering", turns)

        assert len(items) == 1
        assert items[0].memory_type == MemoryType.PRINCIPLE
        assert items[0].importance == 0.9
        assert items[0].metadata["domain"] == "paper_rendering"


# ---------------------------------------------------------------------------
# 10c. Runtime integrations
# ---------------------------------------------------------------------------


class TestRuntimeDomainLearningIntegrations:
    @pytest.mark.asyncio
    async def test_domain_reflection_async_stores_items(self, tmp_path, monkeypatch):
        from dan.providers import CompletionResult
        from dan.server.concierge.runtime import Concierge

        monkeypatch.setenv("DAN_DOMAIN_LEARNING", "1")
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))

        class FakeProvider:
            async def complete(
                self,
                messages: list[dict[str, Any]],
                model: str,
                temperature: float = 0.7,
                max_tokens: int | None = None,
                **kwargs: Any,
            ) -> CompletionResult:
                return CompletionResult(
                    text='[{"category":"best_practice","content":"Always use booktabs for tables","importance":0.8}]',
                    model=model,
                )

        concierge = Concierge.__new__(Concierge)
        concierge.memory_kernel = kernel
        concierge.chat_manager = SimpleNamespace(
            _providers=SimpleNamespace(resolve=lambda _model: FakeProvider())
        )
        concierge._resolve_classifier_model = lambda: "gpt-4o-mini"

        context = SimpleNamespace(
            project=SimpleNamespace(project_id="proj-a"),
            task=SimpleNamespace(
                turns=[
                    TaskTurn(role="user", content="Render the paper"),
                    TaskTurn(role="assistant", content="I used booktabs for the table"),
                ]
            )
        )

        await Concierge._domain_reflect_async(concierge, context, "paper_rendering")

        stored = kernel.list_by_type(MemoryType.PRINCIPLE)
        assert len(stored) == 1
        assert stored[0].metadata["domain"] == "paper_rendering"
        assert stored[0].metadata["project_id"] == "proj-a"
        assert stored[0].scope == MemoryScope.PROJECT
        assert "domain_knowledge" in stored[0].tags

    def test_try_memory_extraction_adds_domain_metadata(self, tmp_path, monkeypatch):
        from dan.server.concierge.runtime import Concierge

        monkeypatch.setenv("DAN_MEMORY_EXTRACTION", "1")
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))

        class FakeExtractor:
            async def extract_with_llm(self, **kwargs: Any) -> list[ExtractedMemory]:
                return [
                    ExtractedMemory(
                        "fact",
                        "Use booktabs for tables",
                        tags=["style"],
                        metadata={"source": "llm"},
                    )
                ]

        monkeypatch.setattr(
            "dan.engine.memory_extractor.MemoryExtractor",
            FakeExtractor,
        )

        concierge = Concierge.__new__(Concierge)
        concierge.memory_kernel = kernel

        Concierge._try_memory_extraction(
            concierge,
            "Render the paper",
            "Used booktabs.",
            domain="paper_rendering",
        )

        stored = kernel.list_by_type(MemoryType.FACT)
        assert len(stored) == 1
        assert stored[0].metadata["domain"] == "paper_rendering"
        assert "domain_knowledge" in stored[0].tags

    @pytest.mark.asyncio
    async def test_post_process_response_uses_principles_for_validation(
        self,
        tmp_path,
        monkeypatch,
    ):
        from dan.server.concierge.runtime import Concierge

        monkeypatch.setenv("DAN_DOMAIN_VALIDATION", "1")
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        kernel.store(
            _make_domain_item(
                "Always use booktabs for tables",
                category="best_practice",
                domain="paper_rendering",
                memory_type=MemoryType.PRINCIPLE,
            )
        )

        concierge = Concierge.__new__(Concierge)
        concierge.memory_kernel = kernel
        concierge._last_context = SimpleNamespace(domain="paper_rendering")
        concierge._should_skip_completion_guard = lambda _msg: True

        msg = SurfaceMessage(
            surface="cli",
            external_id="msg-1",
            text="Render the paper table",
        )
        content = (
            "Here is the updated LaTeX table block using plain hline separators. "
            "\\begin{table}\\hline data \\end{table}"
        )

        processed = await Concierge._post_process_response(
            concierge,
            content,
            msg.text,
            msg=msg,
            entity_ctx=None,
        )

        assert "_Domain check:_" in processed
        assert "booktabs" in processed

    @pytest.mark.asyncio
    async def test_post_process_response_uses_message_scoped_project_context(
        self,
        tmp_path,
        monkeypatch,
    ):
        from dan.server.concierge.runtime import Concierge

        monkeypatch.setenv("DAN_DOMAIN_VALIDATION", "1")
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        kernel.store(
            MemoryItem(
                content="Always use booktabs for tables",
                memory_type=MemoryType.PRINCIPLE,
                scope=MemoryScope.PROJECT,
                importance=0.8,
                tags=["domain_knowledge"],
                metadata={
                    "domain": "paper_rendering",
                    "category": "best_practice",
                    "project_id": "proj-b",
                },
            )
        )

        concierge = Concierge.__new__(Concierge)
        concierge.memory_kernel = kernel
        concierge._last_context = SimpleNamespace(domain="paper_rendering")
        concierge._should_skip_completion_guard = lambda _msg: True

        msg = SurfaceMessage(
            surface="cli",
            external_id="msg-1",
            text="Render the paper table",
            metadata={
                "resolved_domain": "paper_rendering",
                "resolved_project_id": "proj-a",
            },
        )
        content = (
            "Here is the updated LaTeX table block using plain hline separators. "
            "\\begin{table}\\hline data \\end{table}"
        )

        processed = await Concierge._post_process_response(
            concierge,
            content,
            msg.text,
            msg=msg,
            entity_ctx=None,
        )

        assert "_Domain check:_" not in processed

    def test_pending_follow_up_replay_injects_resolved_domain_metadata(
        self,
        tmp_path,
    ):
        from dan.server.concierge.context_resolver import ProjectContextResolver
        from dan.server.concierge.runtime import Concierge

        project_store = ProjectStore(base_dir=tmp_path / "projects")
        concierge = Concierge(
            project_store=project_store,
            context_resolver=ProjectContextResolver(project_store=project_store),
            chat_manager=MagicMock(),
            capability_context=CapabilityContext(workflow_id="_scratch"),
        )

        project = concierge.project_store.create_project("paper-project", "cli-user")
        project.domain = "paper_rendering"
        concierge.project_store.save_project(project)
        concierge.project_store.add_task(project.project_id, "outline", "cli-user")
        project = concierge.project_store.get_project(project.project_id, "cli-user")
        assert project is not None
        project.pending_action = PendingAction(
            kind="confirm",
            intent="ask",
            original_text="continue the paper",
        )
        concierge.project_store.save_project(project)

        pending = concierge._resolve_pending_follow_up(
            SurfaceMessage(
                surface="cli",
                external_id="cli-user",
                text="yes",
            )
        )

        assert pending is not None
        immediate_event, context, classification, replay_msg = pending
        assert immediate_event is None
        assert context.domain == "paper_rendering"
        assert str(classification.intent.value) == "ask"
        assert replay_msg.metadata["resolved_domain"] == "paper_rendering"
        assert replay_msg.metadata["resolved_project_id"] == project.project_id

    @pytest.mark.asyncio
    async def test_pending_follow_up_replay_injects_domain_expertise_prompt(
        self,
        tmp_path,
    ):
        from dan.server.concierge.context_resolver import ProjectContextResolver
        from dan.server.concierge.runtime import Concierge

        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        project_store = ProjectStore(base_dir=tmp_path / "projects")
        captured: dict[str, Any] = {}

        async def _fake_send(**kwargs: Any):
            captured.update(kwargs)
            yield ChatCompleteEvent(
                message_id="m1",
                content="LLM response",
                token_usage={},
                context_window=0,
                graph_revision="",
            )

        chat_manager = MagicMock()
        chat_manager.send_message = MagicMock(side_effect=lambda **kw: _fake_send(**kw))
        chat_manager.send_message_with_tools = MagicMock(side_effect=lambda **kw: _fake_send(**kw))

        concierge = Concierge(
            project_store=project_store,
            context_resolver=ProjectContextResolver(project_store=project_store),
            chat_manager=chat_manager,
            capability_context=CapabilityContext(workflow_id="_scratch"),
            memory_kernel=kernel,
        )

        project = concierge.project_store.create_project("paper-project", "cli-user")
        project.domain = "paper_rendering"
        concierge.project_store.save_project(project)
        kernel.store_fact(
            "Always use booktabs for tables",
            tags=["domain_knowledge"],
            project_id=project.project_id,
            metadata={"domain": "paper_rendering", "category": "best_practice"},
        )
        concierge.project_store.add_task(project.project_id, "outline", "cli-user")
        project = concierge.project_store.get_project(project.project_id, "cli-user")
        assert project is not None
        project.pending_action = PendingAction(
            kind="confirm",
            intent="ask",
            original_text="continue the paper",
        )
        concierge.project_store.save_project(project)

        events = []
        async for event in concierge.process(
            SurfaceMessage(surface="cli", external_id="cli-user", text="yes")
        ):
            events.append(event)

        assert events
        assert "[Domain Expertise: paper_rendering]" in captured["prompt_context"]

    def test_pending_follow_up_replay_clears_context_clarification_metadata(
        self,
        tmp_path,
    ):
        from dan.server.concierge.context_resolver import ProjectContextResolver
        from dan.server.concierge.runtime import Concierge

        project_store = ProjectStore(base_dir=tmp_path / "projects")
        concierge = Concierge(
            project_store=project_store,
            context_resolver=ProjectContextResolver(project_store=project_store),
            chat_manager=MagicMock(),
            capability_context=CapabilityContext(workflow_id="_scratch"),
            memory_kernel=MemoryKernel(base_dir=str(tmp_path / "mem")),
        )

        project = concierge.project_store.create_project("paper-project", "cli-user")
        concierge.project_store.add_task(project.project_id, "outline", "cli-user")
        project = concierge.project_store.get_project(project.project_id, "cli-user")
        assert project is not None
        project.pending_action = PendingAction(
            kind="clarify",
            intent="agent",
            original_text="update the report",
            metadata={
                "solver_decision": True,
                "context_clarification": "Which report do you mean?",
            },
        )
        concierge.project_store.save_project(project)

        result = concierge._resolve_pending_follow_up(
            SurfaceMessage(surface="cli", external_id="cli-user", text="the quarterly report")
        )

        assert result is not None
        replay_msg = result[3]
        assert "context_clarification" not in replay_msg.metadata


# ---------------------------------------------------------------------------
# 10d. Prompt context
# ---------------------------------------------------------------------------


class TestPromptContext:
    def test_augmented_prompt_context_includes_domain_expertise(self):
        msg = SurfaceMessage(
            surface="cli",
            external_id="msg-1",
            text="Render the paper",
            metadata={
                "domain_expertise": (
                    "[Domain Expertise: paper_rendering]\n"
                    "- [BEST_PRACTICE] Always use booktabs for tables"
                )
            },
        )
        context = ResolvedContext(
            project=Project(surface_id="cli", label="Paper Project"),
            task=Task(label="Render task"),
            is_new_project=False,
            is_new_task=False,
            confidence=1.0,
            domain="paper_rendering",
        )

        prompt = _augmented_prompt_context(msg, context)

        assert "Domain Expertise" in prompt


# ---------------------------------------------------------------------------
# 11. fan_out semaphore
# ---------------------------------------------------------------------------


class TestFanOutSemaphore:
    @pytest.mark.asyncio
    async def test_tasks_complete_without_errors(self):
        results = []

        async def make_task(idx: int):
            await asyncio.sleep(0.01)
            results.append(idx)
            return idx

        tasks = [lambda i=i: make_task(i) for i in range(10)]
        out = await fan_out(tasks)
        assert len(out) == 10
        assert all(not isinstance(r, Exception) for r in out)

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self, monkeypatch):
        import sys
        mod = sys.modules["dan.server.concierge.fan_out"]
        monkeypatch.setattr(mod, "_global_semaphore", None)
        monkeypatch.setattr(mod, "_PARALLEL_TASK_LIMIT", 2)

        peak = {"current": 0, "max": 0}
        lock = asyncio.Lock()

        async def tracked_task():
            async with lock:
                peak["current"] += 1
                peak["max"] = max(peak["max"], peak["current"])
            await asyncio.sleep(0.05)
            async with lock:
                peak["current"] -= 1

        tasks = [lambda: tracked_task() for _ in range(6)]
        await fan_out(tasks)
        assert peak["max"] <= 2


# ---------------------------------------------------------------------------
# 12. fan_out telemetry
# ---------------------------------------------------------------------------


class TestFanOutTelemetry:
    @pytest.mark.asyncio
    async def test_telemetry_callback_receives_data(self):
        telemetry_log: list[tuple[str, float, bool]] = []

        def on_telemetry(name: str, duration_ms: float, success: bool):
            telemetry_log.append((name, duration_ms, success))

        async def fast_task():
            return 42

        result = await fan_out_dict(
            {"my_task": lambda: fast_task()},
            telemetry_emit=on_telemetry,
        )
        assert result["my_task"] == 42
        assert len(telemetry_log) == 1
        name, duration, success = telemetry_log[0]
        assert name == "my_task"
        assert duration > 0
        assert success is True


# ---------------------------------------------------------------------------
# 11-4. Domain reflection E2E: store → retrieve expertise
# ---------------------------------------------------------------------------


class TestDomainReflectionE2E:
    def test_domain_reflection_items_available_in_expertise(self, tmp_path):
        from dan.server.concierge.runtime import Concierge

        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        for i in range(3):
            kernel.store(
                _make_domain_item(
                    f"Paper rule {i}: use booktabs for all table formatting",
                    category="best_practice",
                    domain="paper_rendering",
                    memory_type=MemoryType.FACT,
                )
            )

        concierge = Concierge.__new__(Concierge)
        concierge.memory_kernel = kernel

        block = Concierge._retrieve_domain_expertise(
            concierge,
            "write a paper",
            "paper_rendering",
            max_items=8,
            max_chars=600,
        )

        assert "[Domain Expertise: paper_rendering]" in block
        assert "Paper rule" in block
        assert "booktabs" in block


# ---------------------------------------------------------------------------
# 11-5. Correction bridge creates domain knowledge
# ---------------------------------------------------------------------------


class TestCorrectionBridgeDomainFact:
    def test_correction_bridge_creates_domain_fact(self, tmp_path):
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))

        kernel.store(
            MemoryItem(
                content="Prefer CRSP monthly over daily for factor regressions",
                memory_type=MemoryType.FACT,
                scope=MemoryScope.USER,
                importance=0.8,
                tags=["domain_knowledge"],
                metadata={"domain": "equity_research", "category": "best_practice"},
            )
        )

        all_facts = kernel.list_by_type(MemoryType.FACT)
        domain_facts = [
            f for f in all_facts
            if f.metadata.get("domain") == "equity_research"
            and "domain_knowledge" in (f.tags or [])
        ]

        assert len(domain_facts) == 1
        assert domain_facts[0].importance == 0.8
        assert domain_facts[0].metadata["category"] == "best_practice"
        assert "CRSP" in domain_facts[0].content


# ---------------------------------------------------------------------------
# 11-6. Consolidation updates template at threshold
# ---------------------------------------------------------------------------


class TestConsolidationUpdatesTemplate:
    def test_consolidation_updates_template_at_threshold(
        self, tmp_path, monkeypatch,
    ):
        template_dir = tmp_path / "templates"
        monkeypatch.setattr(
            "dan.server.concierge.domain_learning._USER_TEMPLATE_DIR",
            template_dir,
        )
        monkeypatch.setattr(
            "dan.engine.learning_tiers.is_feature_enabled",
            lambda feature: feature == "domain_learning",
        )

        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        cats = ["structure", "formatting", "tooling", "pitfall", "best_practice"]
        for i in range(12):
            kernel.store(
                _make_domain_item(
                    f"Paper rendering fact {i}: {'booktabs' if i % 2 == 0 else 'pgfplots'} rule",
                    category=cats[i % len(cats)],
                    domain="paper_rendering",
                    memory_type=MemoryType.FACT,
                )
            )

        result = kernel.run_consolidation()
        assert result["domains_consolidated"] >= 1

        tmpl = load_domain_template("paper_rendering")
        assert tmpl is not None
        assert tmpl.item_count > 0
        assert tmpl.version >= 2


# ---------------------------------------------------------------------------
# 11-7. Artifact resolution with known files in recent turns
# ---------------------------------------------------------------------------


class TestArtifactResolutionRecentTurns:
    def test_artifact_resolution_finds_recent_turn_path(self):
        recent_turns = [
            TaskTurn(role="user", content="Generate the final report"),
            TaskTurn(
                role="assistant",
                content="Done — saved the report to /tmp/report.pdf",
            ),
        ]

        matched, unresolved = resolve_artifact_references(
            "update the report", [], {}, recent_turns,
        )

        assert len(matched) >= 1
        paths = [
            a.get("path", "") for a in matched if a.get("source") == "recent_turn"
        ]
        assert any("/tmp/report.pdf" in p for p in paths)
        assert not unresolved


# ---------------------------------------------------------------------------
# 11-8. Parallel prep fan-out timing
# ---------------------------------------------------------------------------


class TestFanOutConcurrentTiming:
    @pytest.mark.asyncio
    async def test_fan_out_tasks_run_concurrently(self):
        async def slow_task(label: str) -> str:
            await asyncio.sleep(0.1)
            return label

        tasks = {
            "task_a": lambda: slow_task("a"),
            "task_b": lambda: slow_task("b"),
        }

        start = time.monotonic()
        results = await fan_out_dict(tasks, timeout_per=1.0)
        elapsed = time.monotonic() - start

        assert results["task_a"] == "a"
        assert results["task_b"] == "b"
        assert elapsed < 0.3, f"Expected concurrent execution (<0.3s), got {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# 13. _build_context_package assembly (task 11-2)
# ---------------------------------------------------------------------------


class TestBuildContextPackage:
    def test_build_context_package_assembles_fields(self, tmp_path, monkeypatch):
        from dan.server.concierge.runtime import Concierge

        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        concierge = Concierge.__new__(Concierge)
        concierge.memory_kernel = kernel

        project = Project(surface_id="cli", label="Paper Project", summary="A paper")
        task = Task(label="Render task")
        task.turns = [TaskTurn(role="user", content="Render it")]
        context = SimpleNamespace(
            domain="paper_rendering",
            project=project,
            task=task,
        )
        msg = SurfaceMessage(surface="cli", external_id="msg-1", text="update the paper")

        pkg = Concierge._build_context_package(
            concierge,
            msg,
            context,
            domain_expertise="[Domain Expertise: paper_rendering]",
            memory_context="some memory",
            resolved_artifacts=[{"type": "paper", "source": "project_fact"}],
            unresolved_refs=["dataset"],
            auto_read_content={"main.tex": "\\documentclass{article}"},
        )

        assert pkg.domain == "paper_rendering"
        assert pkg.domain_expertise == "[Domain Expertise: paper_rendering]"
        assert pkg.project_summary == "A paper"
        assert len(pkg.recent_artifacts) == 1
        assert pkg.recent_artifacts[0]["type"] == "paper"
        assert pkg.unresolved_references == ["dataset"]
        assert pkg.memory_context == "some memory"
        assert pkg.auto_read_content == {"main.tex": "\\documentclass{article}"}


# ---------------------------------------------------------------------------
# 14. resolve_artifact_references — extended (task 11-2)
# ---------------------------------------------------------------------------


class TestResolveArtifactReferencesExtended:
    def test_matches_project_fact_with_file_location(self):
        facts = [
            MemoryItem(
                content="The report is stored at /tmp/report.pdf",
                memory_type=MemoryType.FACT,
                scope=MemoryScope.PROJECT,
                metadata={"file_location": "/tmp/report.pdf"},
            ),
        ]
        matched, unresolved = resolve_artifact_references(
            "update the report", facts, {}, [],
        )
        assert len(matched) == 1
        assert matched[0]["type"] == "report"
        assert matched[0]["source"] == "project_fact"
        assert not unresolved

    def test_unresolved_when_no_matching_facts(self):
        matched, unresolved = resolve_artifact_references(
            "update the paper", [], {}, [],
        )
        assert len(unresolved) == 1
        assert "paper" in unresolved
        assert not matched

    def test_matches_task_artifact(self):
        matched, unresolved = resolve_artifact_references(
            "update the report",
            [],
            {"report.pdf": "/tmp/report.pdf"},
            [],
        )
        assert len(matched) == 1
        assert matched[0]["type"] == "report"
        assert matched[0]["source"] == "task_artifact"
        assert matched[0]["path"] == "/tmp/report.pdf"
        assert not unresolved


# ---------------------------------------------------------------------------
# 15. check_context_sufficiency — extended (task 11-2)
# ---------------------------------------------------------------------------


class TestCheckContextSufficiencyExtended:
    def test_returns_clarification_for_unresolved_with_no_artifacts(self):
        pkg = ContextPackage(
            unresolved_references=["paper"],
            recent_artifacts=[],
        )
        result = check_context_sufficiency(pkg, "update the paper")
        assert result is not None
        assert "paper" in result

    def test_returns_none_when_artifacts_resolved(self):
        pkg = ContextPackage(
            unresolved_references=[],
            recent_artifacts=[{"type": "paper", "path": "paper.tex"}],
        )
        result = check_context_sufficiency(pkg, "update the paper")
        assert result is None


# ---------------------------------------------------------------------------
# 16. fan_out_dict — results and exceptions (task 11-3)
# ---------------------------------------------------------------------------


class TestFanOutDictBehavior:
    @pytest.mark.asyncio
    async def test_returns_results_for_all_keys(self):
        async def task_a():
            return 42

        async def task_b():
            return "hello"

        results = await fan_out_dict({
            "a": lambda: task_a(),
            "b": lambda: task_b(),
        })
        assert results["a"] == 42
        assert results["b"] == "hello"

    @pytest.mark.asyncio
    async def test_handles_exception_in_one_task(self):
        async def good_task():
            return "ok"

        async def bad_task():
            raise ValueError("boom")

        results = await fan_out_dict({
            "good": lambda: good_task(),
            "bad": lambda: bad_task(),
        })
        assert results["good"] == "ok"
        assert isinstance(results["bad"], Exception)


# ---------------------------------------------------------------------------
# 17. reuse_first_decision with domain_patterns (task 11-2)
# ---------------------------------------------------------------------------


class TestReuseDecisionWithDomainPatterns:
    def test_domain_patterns_accepted_without_crash(self):
        from dan.server.concierge.reuse_decision import reuse_first_decision

        mk = SimpleNamespace(
            retrieve_by_task=lambda query, task_type=None, limit=10: [
                SimpleNamespace(
                    item=SimpleNamespace(
                        memory_type=MemoryType.WORKFLOW_ASSET,
                        content="equity report analysis data sources",
                        metadata={
                            "workflow_id": "wf-123",
                            "success_rate": "0.7",
                        },
                    ),
                    score=0.5,
                )
            ]
        )

        decision, candidate = reuse_first_decision(
            mk,
            "build equity report",
            domain_patterns=[
                SimpleNamespace(content="equity report analysis data sources"),
            ],
        )
        assert decision in ("reuse", "adapt", "generate")
        if candidate is not None:
            assert candidate.workflow_id == "wf-123"


# ---------------------------------------------------------------------------
# 18. DomainTemplateUpgrader (task 11-2)
# ---------------------------------------------------------------------------


class TestDomainTemplateUpgrader:
    def test_applies_new_categories_and_checklist(self):
        class FakeLLM:
            def complete(self, prompt: str, max_tokens: int = 1200) -> str:
                return json.dumps({
                    "new_categories": ["risk_assessment"],
                    "new_checklist": ["Were risk factors identified?"],
                })

        upgrader = DomainTemplateUpgrader(llm=FakeLLM())
        tmpl = create_generic_template("equity_research")
        original_version = tmpl.version
        items = [
            _make_domain_item(f"Equity rule {i}", domain="equity_research")
            for i in range(20)
        ]

        result = upgrader.upgrade("equity_research", tmpl, items)

        assert result is not None
        assert "risk_assessment" in result.categories
        assert "Were risk factors identified?" in result.checklist
        assert result.version == original_version + 1


# ---------------------------------------------------------------------------
# 19. Review follow-up regressions
# ---------------------------------------------------------------------------


class TestPromptContextPackageWiring:
    def test_build_prompt_from_package_uses_message_metadata_default(self):
        msg = SurfaceMessage(
            surface="cli",
            external_id="msg-ctx",
            text="update the report",
            metadata={
                "context_package": ContextPackage(
                    domain="paper_rendering",
                    domain_expertise="[Domain Expertise: paper_rendering]\n- Use booktabs",
                    recent_artifacts=[{"type": "report", "path": "/tmp/report.tex"}],
                    auto_read_content={"/tmp/report.tex": "Report body"},
                    memory_context="Memory context",
                ),
            },
        )
        context = ResolvedContext(
            project=Project(surface_id="cli", label="Paper Project", summary="Summary"),
            task=Task(label="Render task"),
            is_new_project=False,
            is_new_task=False,
            confidence=1.0,
            domain="paper_rendering",
        )

        prompt = _build_prompt_from_package(msg, context)

        assert "Known artifacts in this project:" in prompt
        assert "[Auto-read: /tmp/report.tex]" in prompt
        assert "Memory context" in prompt


class TestArtifactResolutionRecentTurnMismatch:
    def test_recent_turn_fallback_rejects_mismatched_artifact_kind(self):
        recent_turns = [
            TaskTurn(
                role="assistant",
                content="I last edited /tmp/report.pdf and updated its formatting.",
            ),
        ]

        matched, unresolved = resolve_artifact_references(
            "update the dataset",
            [],
            {},
            recent_turns,
        )

        assert matched == []
        assert unresolved == ["dataset"]


class TestProjectDomainPersistence:
    def test_context_resolver_persists_detected_project_domain(self, tmp_path):
        store = ProjectStore(base_dir=tmp_path / "projects")
        project = store.create_project("Paper Project", "cli")
        store.add_task(project.project_id, "Render task", "cli")

        resolver = ProjectContextResolver(store)
        msg = SurfaceMessage(
            surface="cli",
            external_id="cli",
            text="render this latex paper with tables",
        )

        resolved = resolver.resolve(msg)
        reloaded = store.get_project(project.project_id, "cli")

        assert resolved.domain == "paper_rendering"
        assert reloaded is not None
        assert reloaded.domain == "paper_rendering"


class TestGeneralizedPatternProduction:
    def test_memory_kernel_consolidation_generates_generalized_patterns(
        self,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setattr(
            "dan.engine.learning_tiers.is_feature_enabled",
            lambda feature: feature == "domain_learning",
        )
        monkeypatch.setattr(
            "dan.server.concierge.domain_learning._USER_TEMPLATE_DIR",
            tmp_path / "templates",
        )

        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        for _ in range(10):
            kernel.store(
                _make_domain_item(
                    "Always use booktabs for professional tables",
                    category="best_practice",
                    domain="paper_rendering",
                )
            )

        kernel.run_consolidation()

        patterns = [
            item for item in kernel.list_by_type(MemoryType.WORKFLOW_PATTERN, limit=20)
            if "generalized_pattern" in (item.tags or [])
        ]
        assert patterns


class TestRuntimeTemplateUpgradeWiring:
    @pytest.mark.asyncio
    async def test_domain_reflect_async_runs_pending_template_upgrade(
        self,
        tmp_path,
        monkeypatch,
    ):
        from dan.providers import CompletionResult
        from dan.server.concierge.runtime import Concierge

        monkeypatch.setenv("DAN_DOMAIN_LEARNING", "1")
        monkeypatch.setenv("DAN_DOMAIN_TEMPLATE_UPGRADE", "1")
        monkeypatch.setattr(
            "dan.server.concierge.domain_learning._USER_TEMPLATE_DIR",
            tmp_path / "templates",
        )

        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        template = create_generic_template("paper_rendering")
        template.metadata["needs_llm_upgrade"] = True
        template.item_count = 20
        save_domain_template(template)

        for i in range(20):
            kernel.store(
                _make_domain_item(
                    f"Paper rule {i}",
                    category="best_practice",
                    domain="paper_rendering",
                )
            )

        class FakeProvider:
            async def complete(
                self,
                messages: list[dict[str, Any]],
                model: str,
                temperature: float = 0.7,
                max_tokens: int | None = None,
                **kwargs: Any,
            ) -> CompletionResult:
                prompt = messages[0]["content"]
                if "What categories of knowledge am I missing?" in prompt:
                    return CompletionResult(
                        text=json.dumps({
                            "new_categories": ["journal_style"],
                            "new_checklist": ["Did the output match journal style?"],
                        }),
                        model=model,
                    )
                return CompletionResult(
                    text='[{"category":"best_practice","content":"Always use booktabs for tables","importance":0.8}]',
                    model=model,
                )

        concierge = Concierge.__new__(Concierge)
        concierge.memory_kernel = kernel
        concierge.chat_manager = SimpleNamespace(
            _providers=SimpleNamespace(resolve=lambda _model: FakeProvider())
        )
        concierge._resolve_classifier_model = lambda: "gpt-4o-mini"

        context = SimpleNamespace(
            project=SimpleNamespace(project_id="proj-a"),
            task=SimpleNamespace(
                turns=[
                    TaskTurn(role="user", content="Render the paper"),
                    TaskTurn(role="assistant", content="I used booktabs for the table"),
                ]
            ),
        )

        await Concierge._domain_reflect_async(concierge, context, "paper_rendering")

        updated = load_domain_template("paper_rendering")
        assert updated is not None
        assert "journal_style" in updated.categories
        assert updated.metadata.get("needs_llm_upgrade") is not True


class TestRuntimeArtifactFactFiltering:
    def test_collect_project_artifact_facts_filters_other_projects(self, tmp_path):
        from dan.server.concierge.runtime import Concierge

        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        kernel.store_fact(
            "Project A report at /tmp/a-report.pdf",
            project_id="proj-a",
            metadata={"file_location": "/tmp/a-report.pdf"},
        )
        kernel.store_fact(
            "Project B report at /tmp/b-report.pdf",
            project_id="proj-b",
            metadata={"file_location": "/tmp/b-report.pdf"},
        )

        concierge = Concierge.__new__(Concierge)
        concierge.memory_kernel = kernel

        facts = Concierge._collect_project_artifact_facts(concierge, "proj-a")

        assert len(facts) == 1
        assert facts[0].metadata["project_id"] == "proj-a"

    def test_collect_project_artifact_facts_excludes_user_scope_artifacts(self, tmp_path):
        from dan.server.concierge.runtime import Concierge

        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        kernel.store(
            MemoryItem(
                content="file path: /tmp/shared-report.pdf",
                memory_type=MemoryType.FACT,
                scope=MemoryScope.USER,
                tags=["file_location"],
                metadata={},
            )
        )

        concierge = Concierge.__new__(Concierge)
        concierge.memory_kernel = kernel

        facts = Concierge._collect_project_artifact_facts(concierge, "proj-a")

        assert facts == []

    def test_collect_project_artifact_facts_accepts_tag_shaped_facts(self, tmp_path):
        from dan.server.concierge.runtime import Concierge

        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        kernel.store_fact(
            "file path: /tmp/report.pdf",
            project_id="proj-a",
            tags=["file_location"],
            metadata={"project_id": "proj-a"},
        )

        concierge = Concierge.__new__(Concierge)
        concierge.memory_kernel = kernel

        facts = Concierge._collect_project_artifact_facts(concierge, "proj-a")

        assert len(facts) == 1
        assert "file_location" in (facts[0].tags or [])


class TestRuntimeContextClarification:
    def test_handle_context_clarification_returns_event_and_pauses_task(self, tmp_path):
        from dan.server.concierge.runtime import Concierge

        store = ProjectStore(base_dir=tmp_path / "projects")
        project = store.create_project("Paper Project", "cli")
        task = store.add_task(project.project_id, "Render task", "cli")

        concierge = Concierge.__new__(Concierge)
        concierge.project_store = store
        concierge.conversation_memory = None
        concierge._complete_event = lambda content, **kwargs: SimpleNamespace(content=content)
        concierge._record_assistant_turn = Concierge._record_assistant_turn.__get__(concierge, Concierge)

        context = ResolvedContext(
            project=project,
            task=task,
            is_new_project=False,
            is_new_task=False,
            confidence=1.0,
        )
        msg = SurfaceMessage(surface="cli", external_id="cli", text="update the report")

        event = Concierge._handle_context_clarification(
            concierge,
            context,
            msg,
            "Which report do you mean?",
        )

        updated = store.get_project(project.project_id, "cli")

        assert event is not None
        assert event.content == "Which report do you mean?"
        assert updated is not None
        assert updated.tasks[0].status == "paused"
        assert updated.pending_action is not None
        assert updated.pending_action.kind == "clarify"
        assert updated.pending_action.original_text == "update the report"
        assert updated.pending_action.intent != "ask"


class TestMemoryContextProjectRefresh:
    def test_memory_context_refresh_filters_project_scoped_leaks(self, tmp_path):
        from dan.server.concierge.runtime import Concierge

        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        kernel.store_fact(
            "Project A private report path",
            project_id="proj-a",
            metadata={"project_id": "proj-a"},
        )
        kernel.store_fact(
            "Project B private report path",
            project_id="proj-b",
            metadata={"project_id": "proj-b"},
        )

        concierge = Concierge.__new__(Concierge)
        concierge.memory_kernel = kernel
        concierge._memory_context_var = ContextVar("test_memory_context", default="")
        concierge._memory_context = Concierge._retrieve_memory_context(
            concierge,
            "report path",
            project_id=None,
        )

        refreshed = Concierge._refresh_memory_context_for_project(
            concierge,
            "report path",
            project_id="proj-a",
            has_active_build=False,
        )

        assert "Project A private report path" in refreshed
        assert "Project B private report path" not in refreshed


class TestContextSufficiencyDataReference:
    def test_returns_clarification_for_unresolved_dataset_reference(self):
        pkg = ContextPackage(
            unresolved_references=["dataset"],
            recent_artifacts=[],
        )

        result = check_context_sufficiency(pkg, "analyze the dataset")

        assert result is not None
        assert "dataset" in result.lower()

    def test_returns_clarification_for_unresolved_data_reference(self):
        pkg = ContextPackage(
            unresolved_references=["data"],
            recent_artifacts=[],
        )

        result = check_context_sufficiency(pkg, "analyze the data")

        assert result is not None
        assert "data source" in result.lower() or "dataset" in result.lower()


class TestFinalizeTaskDomainReflection:
    def test_finalize_task_completes_agent_task_and_triggers_reflection(self, tmp_path):
        from dan.server.concierge.classifier import IntentCategory
        from dan.server.concierge.runtime import Concierge

        store = ProjectStore(base_dir=tmp_path / "projects")
        project = store.create_project("Paper Project", "cli")
        task = store.add_task(project.project_id, "Render task", "cli")

        concierge = Concierge.__new__(Concierge)
        concierge.project_store = store
        concierge._maybe_auto_summarize = lambda *args, **kwargs: None
        captured: dict[str, Any] = {"called": False}
        concierge._trigger_domain_reflection = lambda context, msg: captured.update(called=True)

        context = SimpleNamespace(
            project=project,
            task=task,
            domain="paper_rendering",
        )
        msg = SurfaceMessage(surface="cli", external_id="cli", text="update the paper")

        Concierge._finalize_task(
            concierge,
            context,
            msg,
            IntentCategory.AGENT,
            True,
        )

        updated_task = store.get_current_task(project.project_id, "cli")
        assert updated_task is not None
        assert updated_task.status == "completed"
        assert captured["called"] is True

    def test_finalize_task_refreshes_context_task_from_store(self, tmp_path):
        from dan.server.concierge.classifier import IntentCategory
        from dan.server.concierge.runtime import Concierge

        store = ProjectStore(base_dir=tmp_path / "projects")
        project = store.create_project("Paper Project", "cli")
        task = store.add_task(project.project_id, "Render task", "cli")
        context = SimpleNamespace(
            project=project,
            task=task,
            domain="paper_rendering",
        )

        store.append_turn(
            project.project_id,
            task.task_id,
            TaskTurn(role="assistant", content="Latest persisted turn"),
            "cli",
        )

        concierge = Concierge.__new__(Concierge)
        concierge.project_store = store
        concierge._maybe_auto_summarize = lambda *args, **kwargs: None
        concierge._trigger_domain_reflection = lambda *args, **kwargs: None

        msg = SurfaceMessage(surface="cli", external_id="cli", text="continue")
        Concierge._finalize_task(
            concierge,
            context,
            msg,
            IntentCategory.AGENT,
            True,
        )

        assert any(turn.content == "Latest persisted turn" for turn in context.task.turns)


class TestLongExtensionArtifactResolution:
    def test_recent_turn_resolution_supports_parquet_extension(self):
        recent_turns = [
            TaskTurn(
                role="assistant",
                content="The dataset is at /tmp/analysis-data.parquet and is ready.",
            ),
        ]

        matched, unresolved = resolve_artifact_references(
            "analyze the dataset",
            [],
            {},
            recent_turns,
        )

        assert len(matched) == 1
        assert matched[0]["path"].endswith(".parquet")
        assert unresolved == []


class TestReuseResolutionWithDomainPatterns:
    def test_resolve_reuse_decision_recomputes_when_domain_patterns_exist(self, tmp_path):
        from dan.server.concierge.runtime import Concierge

        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        kernel.store(
            MemoryItem(
                content="equity report analysis data sources",
                memory_type=MemoryType.WORKFLOW_ASSET,
                scope=MemoryScope.USER,
                metadata={"workflow_id": "wf-1", "success_rate": 0.7},
            )
        )
        kernel.store(
            MemoryItem(
                content="equity report analysis data sources",
                memory_type=MemoryType.WORKFLOW_PATTERN,
                scope=MemoryScope.PROJECT,
                tags=["domain_knowledge", "generalized_pattern"],
                metadata={"domain": "equity_research", "project_id": "proj-a"},
            )
        )

        concierge = Concierge.__new__(Concierge)
        concierge.memory_kernel = kernel

        decision, candidate = Concierge._resolve_reuse_decision_with_domain_patterns(
            concierge,
            goal_description="build equity report",
            speculative_reuse=("generate", None),
            resolved_domain="equity_research",
            project_id="proj-a",
        )

        assert decision in {"adapt", "reuse"}
        assert candidate is not None
