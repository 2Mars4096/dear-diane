"""Tests for BlockResolver and load_block_as_graph."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dan.blocks.executor import BlockResolver, load_block_as_graph, resolve_node_block
from dan.blocks.models import MANIFEST_FILENAME, DanBlock
from dan.blocks.registry import BlockRegistry
from dan.models.graph import Graph


def _install_block(root: Path, name: str, version: str, graph: Graph) -> None:
    d = root / name / version
    d.mkdir(parents=True, exist_ok=True)
    manifest = DanBlock(name=name, version=version)
    (d / MANIFEST_FILENAME).write_text(
        json.dumps(manifest.model_dump(), indent=2), encoding="utf-8"
    )
    (d / "graph.json").write_text(graph.model_dump_json(indent=2), encoding="utf-8")


class TestBlockResolver:
    def test_resolve_by_name_version(self, simple_workflow: Graph, tmp_path: Path) -> None:
        _install_block(tmp_path, "test-block", "0.1.0", simple_workflow)

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", tmp_path):
            reg = BlockRegistry()
            reg.scan()
            resolver = BlockResolver(reg)
            graph = resolver.resolve("test-block@0.1.0")

        assert len(graph.nodes) == len(simple_workflow.nodes)

    def test_resolve_by_name_only(self, simple_workflow: Graph, tmp_path: Path) -> None:
        _install_block(tmp_path, "latest-test", "1.0.0", simple_workflow)

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", tmp_path):
            reg = BlockRegistry()
            reg.scan()
            resolver = BlockResolver(reg)
            graph = resolver.resolve("latest-test")

        assert graph.metadata.name == simple_workflow.metadata.name

    def test_resolve_caches(self, simple_workflow: Graph, tmp_path: Path) -> None:
        _install_block(tmp_path, "cached", "0.1.0", simple_workflow)

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", tmp_path):
            reg = BlockRegistry()
            reg.scan()
            resolver = BlockResolver(reg)
            g1 = resolver.resolve("cached@0.1.0")
            g2 = resolver.resolve("cached@0.1.0")

        assert g1 is g2

    def test_resolve_missing_raises(self, tmp_path: Path) -> None:
        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", tmp_path):
            reg = BlockRegistry()
            reg.scan()
            resolver = BlockResolver(reg)
            with pytest.raises(ValueError, match="not installed"):
                resolver.resolve("ghost@1.0.0")


class TestLoadBlockAsGraph:
    def test_loads_graph(self, simple_workflow: Graph, tmp_path: Path) -> None:
        _install_block(tmp_path, "loader-test", "0.1.0", simple_workflow)

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", tmp_path):
            reg = BlockRegistry()
            reg.scan()
            graph = load_block_as_graph("loader-test@0.1.0", reg)

        assert len(graph.nodes) > 0


class TestResolveNodeBlock:
    def test_returns_graph_when_block_ref_present(
        self, simple_workflow: Graph, tmp_path: Path
    ) -> None:
        _install_block(tmp_path, "node-blk", "0.1.0", simple_workflow)

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", tmp_path):
            reg = BlockRegistry()
            reg.scan()
            result = resolve_node_block(
                {"block_name": "node-blk", "block_version": "0.1.0"}, reg
            )

        assert result is not None
        assert len(result.nodes) > 0

    def test_returns_none_when_no_block_ref(self, tmp_path: Path) -> None:
        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", tmp_path):
            reg = BlockRegistry()
            reg.scan()
            assert resolve_node_block({}, reg) is None
