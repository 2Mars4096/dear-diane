"""Block resolution — load installed blocks as Graph objects for the engine."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from dan.blocks.models import GRAPH_FILENAME, InstalledBlock
from dan.blocks.registry import BlockRegistry
from dan.models.graph import Graph

logger = logging.getLogger(__name__)

_BLOCK_REF_RE = re.compile(r"^(?P<name>[^@]+)@(?P<version>\d+\.\d+\.\d+.*)$")


class BlockResolver:
    """Resolves block references to loaded ``Graph`` objects.

    Backed by a ``BlockRegistry`` for lookups and an in-memory cache
    to avoid re-parsing the same block graph repeatedly.
    """

    def __init__(self, registry: BlockRegistry) -> None:
        self._registry = registry
        self._cache: dict[str, Graph] = {}

    def resolve(self, block_ref: str) -> Graph:
        """Load a block graph by ``"name@version"`` or just ``"name"`` (latest).

        Raises ``ValueError`` if the block is not installed or has no graph.
        """
        if block_ref in self._cache:
            return self._cache[block_ref]

        name, version = _parse_block_ref(block_ref)
        block = self._registry.get_block(name, version)
        if block is None:
            raise ValueError(f"Block {block_ref!r} is not installed")

        graph = _load_block_graph(block)
        self._cache[block_ref] = graph
        return graph


def load_block_as_graph(block_ref: str, registry: BlockRegistry) -> Graph:
    """Convenience function — resolve a block reference to a Graph.

    Equivalent to ``BlockResolver(registry).resolve(block_ref)`` but
    without caching (useful for one-shot resolution).
    """
    name, version = _parse_block_ref(block_ref)
    block = registry.get_block(name, version)
    if block is None:
        raise ValueError(f"Block {block_ref!r} is not installed")
    return _load_block_graph(block)


def resolve_node_block(
    node_metadata: dict,
    registry: BlockRegistry,
) -> Graph | None:
    """If *node_metadata* carries ``block_name``/``block_version``, load it.

    Returns ``None`` when the metadata does not reference a block.
    """
    block_name = node_metadata.get("block_name")
    if not block_name:
        return None
    block_version = node_metadata.get("block_version")
    ref = f"{block_name}@{block_version}" if block_version else block_name
    return load_block_as_graph(ref, registry)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_block_ref(ref: str) -> tuple[str, str | None]:
    m = _BLOCK_REF_RE.match(ref)
    if m:
        return m.group("name"), m.group("version")
    return ref, None


def _load_block_graph(block: InstalledBlock) -> Graph:
    graph_path = block.install_path / GRAPH_FILENAME
    if not graph_path.exists():
        raise ValueError(
            f"Block {block.name}@{block.version} has no {GRAPH_FILENAME} at {graph_path}"
        )
    data = json.loads(graph_path.read_text(encoding="utf-8"))
    return Graph.model_validate(data)
