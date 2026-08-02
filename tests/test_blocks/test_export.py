"""Tests for block export functions."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from dan.blocks.export import (
    export_agent_collection_block,
    export_composite_block,
    export_workflow_block,
    pack_block,
)
from dan.blocks.models import GRAPH_FILENAME, MANIFEST_FILENAME, TARBALL_SUFFIX, DanBlock
from dan.models.graph import Graph


class TestExportWorkflow:
    def test_creates_directory_structure(self, simple_workflow: Graph, tmp_path: Path) -> None:
        block_dir = export_workflow_block(simple_workflow, tmp_path, name="test-wf")

        assert block_dir.is_dir()
        assert (block_dir / MANIFEST_FILENAME).exists()
        assert (block_dir / GRAPH_FILENAME).exists()
        assert (block_dir / "README.md").exists()

    def test_manifest_content(self, simple_workflow: Graph, tmp_path: Path) -> None:
        block_dir = export_workflow_block(
            simple_workflow, tmp_path, name="my-pipe", version="1.0.0"
        )
        manifest = DanBlock.model_validate(
            json.loads((block_dir / MANIFEST_FILENAME).read_text())
        )
        assert manifest.name == "my-pipe"
        assert manifest.version == "1.0.0"
        assert manifest.block_type == "workflow"
        assert "topic" in manifest.input_schema.get("properties", {})

    def test_graph_roundtrip(self, simple_workflow: Graph, tmp_path: Path) -> None:
        block_dir = export_workflow_block(simple_workflow, tmp_path, name="rt")
        loaded = Graph.model_validate(
            json.loads((block_dir / GRAPH_FILENAME).read_text())
        )
        assert len(loaded.nodes) == len(simple_workflow.nodes)

    def test_auto_name_from_metadata(self, simple_workflow: Graph, tmp_path: Path) -> None:
        block_dir = export_workflow_block(simple_workflow, tmp_path)
        assert block_dir.name == "simple-pipe"

    def test_custom_tags(self, simple_workflow: Graph, tmp_path: Path) -> None:
        block_dir = export_workflow_block(
            simple_workflow, tmp_path, name="t", tags=["analysis"]
        )
        manifest = DanBlock.model_validate(
            json.loads((block_dir / MANIFEST_FILENAME).read_text())
        )
        assert "analysis" in manifest.tags


class TestExportComposite:
    def test_extracts_subgraph(self, composite_workflow: Graph, tmp_path: Path) -> None:
        block_dir = export_composite_block(
            composite_workflow, "comp", tmp_path, name="comp-block"
        )
        assert (block_dir / GRAPH_FILENAME).exists()

        manifest = DanBlock.model_validate(
            json.loads((block_dir / MANIFEST_FILENAME).read_text())
        )
        assert manifest.block_type == "composite"

        loaded = Graph.model_validate(
            json.loads((block_dir / GRAPH_FILENAME).read_text())
        )
        assert len(loaded.nodes) == 2

    def test_rejects_non_composite(self, simple_workflow: Graph, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not 'composite'"):
            export_composite_block(simple_workflow, "a", tmp_path)

    def test_rejects_missing_node(self, composite_workflow: Graph, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not found"):
            export_composite_block(composite_workflow, "nonexistent", tmp_path)


class TestExportAgentCollection:
    def test_bundles_md_files(self, tmp_path: Path) -> None:
        agents_src = tmp_path / "agents_src"
        agents_src.mkdir()
        (agents_src / "agent1.md").write_text("# Agent 1\n")
        (agents_src / "agent2.md").write_text("# Agent 2\n")
        (agents_src / "not_md.txt").write_text("ignored\n")

        out = tmp_path / "output"
        block_dir = export_agent_collection_block(agents_src, out, name="my-agents")

        assert (block_dir / MANIFEST_FILENAME).exists()
        assert (block_dir / "agents" / "agent1.md").exists()
        assert (block_dir / "agents" / "agent2.md").exists()
        assert not (block_dir / "agents" / "not_md.txt").exists()

        manifest = DanBlock.model_validate(
            json.loads((block_dir / MANIFEST_FILENAME).read_text())
        )
        assert manifest.block_type == "agent_collection"

    def test_rejects_empty_dir(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(ValueError, match="No .md files"):
            export_agent_collection_block(empty, tmp_path)

    def test_rejects_nonexistent_dir(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            export_agent_collection_block(tmp_path / "nope", tmp_path)


class TestPackBlock:
    def test_creates_tarball(self, simple_workflow: Graph, tmp_path: Path) -> None:
        block_dir = export_workflow_block(simple_workflow, tmp_path, name="packme")
        tarball = pack_block(block_dir)

        assert tarball.exists()
        assert tarball.name.endswith(TARBALL_SUFFIX)
        with tarfile.open(tarball, "r:gz") as tar:
            names = tar.getnames()
            assert any(MANIFEST_FILENAME in n for n in names)
            assert any(GRAPH_FILENAME in n for n in names)

    def test_rejects_dir_without_manifest(self, tmp_path: Path) -> None:
        bad = tmp_path / "no-manifest"
        bad.mkdir()
        with pytest.raises(ValueError, match=MANIFEST_FILENAME):
            pack_block(bad)
