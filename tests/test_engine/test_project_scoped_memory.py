"""Tests for project-scoped memory (31-18).

Covers: retrieve filtering by project_id, store convenience helpers with
project_id, score boosting for matching project, backward compatibility.
"""

from __future__ import annotations

import pytest

from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryKernel,
    MemoryScope,
    MemoryType,
)


@pytest.fixture()
def kernel(tmp_path):
    return MemoryKernel(base_dir=str(tmp_path / "memory"))


class TestRetrieveProjectFilter:
    """retrieve() with project_id should filter PROJECT-scoped items by project."""

    def test_filters_out_other_projects(self, kernel: MemoryKernel):
        kernel.store_fact("INFORMS uses APA style", project_id="proj-informs")
        kernel.store_fact("arXiv uses BibTeX", project_id="proj-arxiv")

        scored = kernel.retrieve("citation style", project_id="proj-informs")
        contents = [s.item.content for s in scored]
        assert "INFORMS uses APA style" in contents
        assert "arXiv uses BibTeX" not in contents

    def test_returns_matching_project_items(self, kernel: MemoryKernel):
        kernel.store_fact("data at /research/arxiv/data", project_id="proj-arxiv")

        scored = kernel.retrieve("data path", project_id="proj-arxiv")
        assert any("arxiv" in s.item.content for s in scored)

    def test_user_scoped_items_always_returned(self, kernel: MemoryKernel):
        kernel.store_fact("I prefer dark mode")  # USER scope, no project
        kernel.store_fact("INFORMS style", project_id="proj-informs")

        scored = kernel.retrieve("preferences", project_id="proj-informs")
        contents = [s.item.content for s in scored]
        assert "I prefer dark mode" in contents
        assert "INFORMS style" in contents

    def test_global_scoped_items_always_returned(self, kernel: MemoryKernel):
        kernel.store_fact("always cite sources", scope=MemoryScope.GLOBAL)
        kernel.store_fact("use Times New Roman", project_id="proj-informs")

        scored = kernel.retrieve("writing rules", project_id="proj-informs")
        contents = [s.item.content for s in scored]
        assert "always cite sources" in contents

    def test_no_project_id_returns_all_items(self, kernel: MemoryKernel):
        """Backward compat: without project_id, all items returned."""
        kernel.store_fact("INFORMS style", project_id="proj-informs")
        kernel.store_fact("arXiv style", project_id="proj-arxiv")
        kernel.store_fact("general fact")

        scored = kernel.retrieve("style")
        contents = [s.item.content for s in scored]
        assert len(contents) == 3

    def test_project_items_without_project_id_on_query(self, kernel: MemoryKernel):
        """PROJECT-scoped items with no project_id filter still appear."""
        kernel.store_fact("project-specific fact", project_id="proj-a")

        scored = kernel.retrieve("fact")
        assert len(scored) >= 1
        assert any(s.item.scope == MemoryScope.PROJECT for s in scored)


class TestRetrieveProjectBoost:
    """Matching project items get a score bonus."""

    def test_matching_project_gets_score_boost(self, kernel: MemoryKernel):
        kernel.store_fact("generic fact about style", scope=MemoryScope.USER)
        kernel.store_fact("project-specific style rule", project_id="proj-a")

        scored = kernel.retrieve("style", project_id="proj-a")
        project_items = [s for s in scored if s.item.scope == MemoryScope.PROJECT]
        user_items = [s for s in scored if s.item.scope == MemoryScope.USER]

        assert len(project_items) >= 1
        assert len(user_items) >= 1
        assert project_items[0].score > user_items[0].score


class TestRetrieveByTaskProjectId:
    """retrieve_by_task() passes project_id through."""

    def test_retrieve_by_task_with_project_id(self, kernel: MemoryKernel):
        kernel.store_fact("INFORMS data", project_id="proj-informs")
        kernel.store_fact("arXiv data", project_id="proj-arxiv")

        scored = kernel.retrieve_by_task("data", project_id="proj-informs")
        contents = [s.item.content for s in scored]
        assert "INFORMS data" in contents
        assert "arXiv data" not in contents


class TestStoreWithProjectId:
    """Convenience store methods set scope and metadata when project_id given."""

    def test_store_fact_with_project_id(self, kernel: MemoryKernel):
        item = kernel.store_fact("data at /path", project_id="proj-a")
        assert item.scope == MemoryScope.PROJECT
        assert item.metadata["project_id"] == "proj-a"

    def test_store_fact_without_project_id(self, kernel: MemoryKernel):
        item = kernel.store_fact("general fact")
        assert item.scope == MemoryScope.USER
        assert "project_id" not in item.metadata

    def test_store_preference_with_project_id(self, kernel: MemoryKernel):
        item = kernel.store_preference("use APA citations", project_id="proj-informs")
        assert item is not None
        assert item.scope == MemoryScope.PROJECT
        assert item.metadata["project_id"] == "proj-informs"

    def test_store_preference_without_project_id(self, kernel: MemoryKernel):
        item = kernel.store_preference("I prefer concise answers")
        assert item is not None
        assert item.scope == MemoryScope.USER

    def test_store_principle_with_project_id(self, kernel: MemoryKernel):
        item = kernel.store_principle(
            "double-spacing required", project_id="proj-informs",
        )
        assert item.scope == MemoryScope.PROJECT
        assert item.metadata["project_id"] == "proj-informs"
        assert item.metadata["confidence"] == 0.5

    def test_store_principle_without_project_id(self, kernel: MemoryKernel):
        item = kernel.store_principle("be helpful")
        assert item.scope == MemoryScope.GLOBAL

    def test_store_fact_preserves_existing_metadata(self, kernel: MemoryKernel):
        item = kernel.store_fact(
            "data path",
            project_id="proj-a",
            metadata={"source": "user"},
        )
        assert item.metadata["project_id"] == "proj-a"
        assert item.metadata["source"] == "user"

    def test_store_fact_preserves_tags(self, kernel: MemoryKernel):
        item = kernel.store_fact("tagged fact", project_id="proj-a", tags=["path"])
        assert item.scope == MemoryScope.PROJECT
        assert "path" in item.tags


class TestEndToEndProjectScoping:
    """Simulate the user's use cases: INFORMS vs arXiv differentiation."""

    def test_informs_vs_arxiv_isolation(self, kernel: MemoryKernel):
        kernel.store_preference("citation style: APA", project_id="proj-informs")
        kernel.store_preference("font: Times New Roman 12pt", project_id="proj-informs")
        kernel.store_fact("data path: /research/informs/data", project_id="proj-informs")

        kernel.store_preference("citation style: BibTeX", project_id="proj-arxiv")
        kernel.store_preference("format: single-column LaTeX", project_id="proj-arxiv")
        kernel.store_fact("data path: /research/arxiv/data", project_id="proj-arxiv")

        informs_results = kernel.retrieve("citation style format", project_id="proj-informs")
        arxiv_results = kernel.retrieve("citation style format", project_id="proj-arxiv")

        informs_contents = " ".join(s.item.content for s in informs_results)
        arxiv_contents = " ".join(s.item.content for s in arxiv_results)

        assert "APA" in informs_contents
        assert "BibTeX" not in informs_contents

        assert "BibTeX" in arxiv_contents
        assert "APA" not in arxiv_contents

    def test_project_file_path_retrieval(self, kernel: MemoryKernel):
        kernel.store_fact(
            "project files at /Users/lizhi/research/informs-paper",
            project_id="proj-informs",
        )
        kernel.store_fact(
            "look for data in /Users/lizhi/Dropbox/Data/informs",
            project_id="proj-informs",
        )

        scored = kernel.retrieve("where is the data", project_id="proj-informs")
        contents = " ".join(s.item.content for s in scored)
        assert "/Users/lizhi" in contents

        other_scored = kernel.retrieve("where is the data", project_id="proj-arxiv")
        other_contents = " ".join(s.item.content for s in other_scored)
        assert "/Users/lizhi" not in other_contents

    def test_user_global_memories_available_everywhere(self, kernel: MemoryKernel):
        """User-level preferences apply to all projects (like always-applied rules)."""
        kernel.store_preference("I prefer concise responses")
        kernel.store_principle("never fabricate citations")

        for proj in ["proj-informs", "proj-arxiv", "proj-thesis"]:
            scored = kernel.retrieve("writing style", project_id=proj)
            contents = " ".join(s.item.content for s in scored)
            assert "concise" in contents or "fabricate" in contents
