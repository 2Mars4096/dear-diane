"""Integration tests for the block export→import→registry→load pipeline.

Verifies the end-to-end flow:
1. Export a workflow graph as a block
2. Import the exported block into a fresh registry root
3. Scan the registry and verify the block is discoverable
4. Load the block's graph and verify it's valid
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dan.blocks.export import export_workflow_block, pack_block
from dan.blocks.importer import import_block
from dan.blocks.models import GRAPH_FILENAME, MANIFEST_FILENAME, DanBlock
from dan.blocks.registry import BlockRegistry
from dan.blocks.executor import BlockResolver, load_block_as_graph
from dan.models.graph import Graph


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def pipeline_graph() -> Graph:
    """Simple two-node pipeline with explicit InputNode for interface derivation."""
    return Graph.model_validate({
        "version": "dan_graph_v1",
        "metadata": {"name": "test-pipeline", "description": "Export-import test"},
        "nodes": [
            {
                "id": "inp",
                "name": "inputs",
                "node_type": "input",
                "variables": [{"name": "topic", "type": "string"}],
                "output_ports": [{"name": "result"}],
            },
            {
                "id": "gen",
                "name": "generator",
                "node_type": "llm_operator",
                "model": "test-model",
                "prompt_template": "Write about {topic}",
                "input_ports": [{"name": "input"}],
                "output_ports": [{"name": "text"}],
            },
            {
                "id": "refine",
                "name": "refiner",
                "node_type": "llm_operator",
                "model": "test-model",
                "prompt_template": "Refine: {text}",
                "input_ports": [{"name": "input"}],
                "output_ports": [{"name": "text"}],
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source_node_id": "inp",
                "source_port": "result",
                "target_node_id": "gen",
                "target_port": "input",
                "edge_type": "data",
            },
            {
                "id": "e2",
                "source_node_id": "gen",
                "source_port": "text",
                "target_node_id": "refine",
                "target_port": "input",
                "edge_type": "data",
            },
        ],
        "entry_points": ["inp"],
        "exit_points": ["refine"],
    })


# ---------------------------------------------------------------------------
# Export → directory structure
# ---------------------------------------------------------------------------


class TestExportBlockStructure:
    def test_export_creates_valid_block(self, pipeline_graph: Graph, tmp_path: Path):
        block_dir = export_workflow_block(
            pipeline_graph, tmp_path, name="my-block", version="0.2.0",
        )
        assert block_dir.is_dir()
        assert (block_dir / MANIFEST_FILENAME).exists()
        assert (block_dir / GRAPH_FILENAME).exists()

        manifest = DanBlock.model_validate(
            json.loads((block_dir / MANIFEST_FILENAME).read_text())
        )
        assert manifest.name == "my-block"
        assert manifest.version == "0.2.0"
        assert manifest.block_type == "workflow"

    def test_exported_graph_is_valid(self, pipeline_graph: Graph, tmp_path: Path):
        block_dir = export_workflow_block(pipeline_graph, tmp_path, name="rt-test")
        loaded = Graph.model_validate(
            json.loads((block_dir / GRAPH_FILENAME).read_text())
        )
        assert len(loaded.nodes) == len(pipeline_graph.nodes)
        assert len(loaded.edges) == len(pipeline_graph.edges)


# ---------------------------------------------------------------------------
# Export → Import → Registry → Load (full pipeline)
# ---------------------------------------------------------------------------


class TestExportImportRegistryPipeline:
    def test_full_pipeline(self, pipeline_graph: Graph, tmp_path: Path):
        export_dir = tmp_path / "export"
        registry_root = tmp_path / "registry"

        block_dir = export_workflow_block(
            pipeline_graph, export_dir, name="e2e-block", version="1.0.0",
        )

        installed = import_block(
            block_dir, scope="user", _user_root_override=registry_root,
        )
        assert installed.name == "e2e-block"
        assert installed.version == "1.0.0"
        assert installed.install_path.exists()
        assert (installed.install_path / MANIFEST_FILENAME).exists()
        assert (installed.install_path / GRAPH_FILENAME).exists()

        registry = BlockRegistry()
        registry._blocks.clear()
        registry._scan_root(registry_root)

        blocks = registry.list_blocks()
        assert len(blocks) == 1
        assert blocks[0].name == "e2e-block"

        found = registry.get_block("e2e-block", "1.0.0")
        assert found is not None

        graph = load_block_as_graph("e2e-block@1.0.0", registry)
        assert isinstance(graph, Graph)
        assert len(graph.nodes) == len(pipeline_graph.nodes)

    def test_tarball_export_import(self, pipeline_graph: Graph, tmp_path: Path):
        export_dir = tmp_path / "export"
        registry_root = tmp_path / "registry"

        block_dir = export_workflow_block(
            pipeline_graph, export_dir, name="tar-block", version="0.5.0",
        )
        tarball = pack_block(block_dir)
        assert tarball.exists()
        assert tarball.name.endswith(".dan-block.tar.gz")

        installed = import_block(
            tarball, scope="user", _user_root_override=registry_root,
        )
        assert installed.name == "tar-block"
        assert installed.version == "0.5.0"

    def test_block_resolver_caching(self, pipeline_graph: Graph, tmp_path: Path):
        export_dir = tmp_path / "export"
        registry_root = tmp_path / "registry"

        export_workflow_block(
            pipeline_graph, export_dir, name="cache-block", version="0.1.0",
        )
        import_block(
            export_dir / "cache-block",
            scope="user",
            _user_root_override=registry_root,
        )

        registry = BlockRegistry()
        registry._blocks.clear()
        registry._scan_root(registry_root)

        resolver = BlockResolver(registry)
        graph1 = resolver.resolve("cache-block@0.1.0")
        graph2 = resolver.resolve("cache-block@0.1.0")
        assert graph1 is graph2  # same object from cache

    def test_import_block_not_found(self, tmp_path: Path):
        registry_root = tmp_path / "registry"
        registry = BlockRegistry()
        registry._blocks.clear()
        registry._scan_root(registry_root)

        with pytest.raises(ValueError, match="not installed"):
            load_block_as_graph("nonexistent@0.1.0", registry)
