"""Export workflows, composite nodes, and agent collections as shareable blocks."""

from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path
from typing import Any

from dan.blocks.models import (
    AGENTS_DIR,
    GRAPH_FILENAME,
    MANIFEST_FILENAME,
    TARBALL_SUFFIX,
    DanBlock,
)
from dan.models.graph import Graph
from dan.utils.workflow_interface import derive_workflow_interface


def export_composite_block(
    graph: Graph,
    node_id: str,
    output_dir: Path,
    *,
    name: str = "",
    version: str = "0.1.0",
    author: str = "",
    description: str = "",
    tags: list[str] | None = None,
) -> Path:
    """Extract a composite node's sub-graph and package it as a block.

    Returns the path to the created block directory.
    """
    node = graph.node_by_id(node_id)
    if node is None:
        raise ValueError(f"Node {node_id!r} not found in graph")
    if node.node_type != "composite":
        raise ValueError(f"Node {node_id!r} is {node.node_type!r}, not 'composite'")

    body_key = node.body_graph  # type: ignore[attr-defined]
    sub_graph = graph.sub_graphs.get(body_key)
    if sub_graph is None:
        raise ValueError(f"Sub-graph {body_key!r} not found for composite node {node_id!r}")

    block_name = name or node.name or node_id
    iface = derive_workflow_interface(sub_graph)

    manifest = DanBlock(
        name=block_name,
        version=version,
        description=description or iface.description or node.description,
        author=author,
        tags=tags or [],
        block_type="composite",
        entry_point=GRAPH_FILENAME,
        input_schema=iface.input_schema,
        output_schema=iface.output_schema,
    )

    return _write_block_dir(output_dir, block_name, manifest, sub_graph)


def export_workflow_block(
    graph: Graph,
    output_dir: Path,
    *,
    name: str = "",
    version: str = "0.1.0",
    author: str = "",
    description: str = "",
    tags: list[str] | None = None,
) -> Path:
    """Export an entire workflow graph as a block.

    Returns the path to the created block directory.
    """
    block_name = name or graph.metadata.name or "unnamed-workflow"
    iface = derive_workflow_interface(graph)

    manifest = DanBlock(
        name=block_name,
        version=version,
        description=description or iface.description or graph.metadata.description,
        author=author,
        tags=tags or list(graph.metadata.tags),
        block_type="workflow",
        entry_point=GRAPH_FILENAME,
        input_schema=iface.input_schema,
        output_schema=iface.output_schema,
    )

    return _write_block_dir(output_dir, block_name, manifest, graph)


def export_agent_collection_block(
    agents_dir: Path,
    output_dir: Path,
    *,
    name: str = "",
    version: str = "0.1.0",
    author: str = "",
    description: str = "",
    tags: list[str] | None = None,
) -> Path:
    """Bundle a directory of .md agent files as a block.

    Returns the path to the created block directory.
    """
    if not agents_dir.is_dir():
        raise ValueError(f"Agents directory {agents_dir} does not exist")

    md_files = sorted(agents_dir.glob("*.md"))
    if not md_files:
        raise ValueError(f"No .md files found in {agents_dir}")

    block_name = name or agents_dir.name

    manifest = DanBlock(
        name=block_name,
        version=version,
        description=description or f"Agent collection from {agents_dir.name}",
        author=author,
        tags=tags or [],
        block_type="agent_collection",
        entry_point=AGENTS_DIR,
    )

    block_dir = output_dir / block_name
    block_dir.mkdir(parents=True, exist_ok=True)

    _write_manifest(block_dir, manifest)

    dest_agents = block_dir / AGENTS_DIR
    dest_agents.mkdir(exist_ok=True)
    for md_file in md_files:
        shutil.copy2(md_file, dest_agents / md_file.name)

    _write_readme(block_dir, manifest)
    return block_dir


def pack_block(block_dir: Path) -> Path:
    """Compress a block directory to ``.dan-block.tar.gz``.

    Returns the path to the tarball.
    """
    block_dir = block_dir.resolve()
    if not (block_dir / MANIFEST_FILENAME).exists():
        raise ValueError(f"{block_dir} does not contain {MANIFEST_FILENAME}")

    tarball_path = block_dir.parent / f"{block_dir.name}{TARBALL_SUFFIX}"
    with tarfile.open(tarball_path, "w:gz") as tar:
        tar.add(block_dir, arcname=block_dir.name)
    return tarball_path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _write_block_dir(
    output_dir: Path,
    block_name: str,
    manifest: DanBlock,
    graph: Graph,
) -> Path:
    block_dir = output_dir / block_name
    block_dir.mkdir(parents=True, exist_ok=True)

    _write_manifest(block_dir, manifest)

    graph_path = block_dir / GRAPH_FILENAME
    graph_path.write_text(graph.model_dump_json(indent=2), encoding="utf-8")

    _write_readme(block_dir, manifest)
    return block_dir


def _write_manifest(block_dir: Path, manifest: DanBlock) -> None:
    manifest_path = block_dir / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(manifest.model_dump(), indent=2),
        encoding="utf-8",
    )


def _write_readme(block_dir: Path, manifest: DanBlock) -> None:
    readme_path = block_dir / "README.md"
    if readme_path.exists():
        return
    lines = [
        f"# {manifest.name}",
        "",
        manifest.description or "_No description provided._",
        "",
        f"- **Version:** {manifest.version}",
        f"- **Type:** {manifest.block_type}",
    ]
    if manifest.author:
        lines.append(f"- **Author:** {manifest.author}")
    if manifest.tags:
        lines.append(f"- **Tags:** {', '.join(manifest.tags)}")
    readme_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
