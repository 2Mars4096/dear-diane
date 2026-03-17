"""Local block registry — discovers, caches, and looks up installed blocks."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from dan.blocks.models import (
    MANIFEST_FILENAME,
    DanBlock,
    InstalledBlock,
)

logger = logging.getLogger(__name__)

_USER_BLOCKS_ROOT = Path.home() / ".dan" / "blocks"
_INDEX_FILENAME = "_index.json"


class BlockRegistry:
    """Discovers and queries installed blocks from user and workspace scopes.

    Workspace-level blocks override user-level when name+version collide.
    """

    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace = workspace
        self._blocks: dict[str, InstalledBlock] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> None:
        """Scan block directories and rebuild the in-memory index."""
        self._blocks.clear()

        self._scan_root(_USER_BLOCKS_ROOT)
        if self._workspace is not None:
            ws_root = self._workspace / ".dan" / "blocks"
            self._scan_root(ws_root, override=True)

        self._write_index(_USER_BLOCKS_ROOT)

    def list_blocks(self) -> list[InstalledBlock]:
        """Return all discovered blocks, sorted by name then version."""
        return sorted(self._blocks.values(), key=lambda b: (b.name, b.version))

    def get_block(self, name: str, version: str | None = None) -> InstalledBlock | None:
        """Look up a block by name. Returns latest version if *version* is ``None``."""
        if version is not None:
            return self._blocks.get(f"{name}@{version}")

        candidates = [b for b in self._blocks.values() if b.name == name]
        if not candidates:
            return None
        candidates.sort(key=lambda b: _version_tuple(b.version), reverse=True)
        return candidates[0]

    def remove_block(self, name: str, version: str) -> bool:
        """Uninstall a block by deleting its directory.

        Returns True if the block was found and removed.
        """
        key = f"{name}@{version}"
        block = self._blocks.pop(key, None)
        if block is None:
            return False

        if block.install_path.exists():
            shutil.rmtree(block.install_path)
            logger.info("Removed block %s@%s from %s", name, version, block.install_path)

        parent = block.install_path.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()

        self._write_index(_USER_BLOCKS_ROOT)
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _scan_root(self, root: Path, *, override: bool = False) -> None:
        if not root.exists():
            return

        for name_dir in sorted(root.iterdir()):
            if not name_dir.is_dir() or name_dir.name.startswith("_"):
                continue
            for ver_dir in sorted(name_dir.iterdir()):
                if not ver_dir.is_dir():
                    continue
                manifest_path = ver_dir / MANIFEST_FILENAME
                if not manifest_path.exists():
                    continue
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest = DanBlock.model_validate(data)
                except Exception:
                    logger.warning("Skipping invalid block at %s", ver_dir)
                    continue

                key = f"{manifest.name}@{manifest.version}"
                if key in self._blocks and not override:
                    continue

                self._blocks[key] = InstalledBlock(
                    name=manifest.name,
                    version=manifest.version,
                    install_path=ver_dir,
                    block_type=manifest.block_type,
                    metadata=manifest,
                )

    def _write_index(self, root: Path) -> None:
        try:
            root.mkdir(parents=True, exist_ok=True)
            index_path = root / _INDEX_FILENAME
            entries = [b.model_dump(mode="json") for b in self._blocks.values()]
            index_path.write_text(
                json.dumps(entries, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                "Failed to persist block registry index at %s; continuing with in-memory registry: %s",
                root,
                exc,
            )


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse ``"1.2.3"`` into ``(1, 2, 3)`` for comparison."""
    base = v.split("-")[0].split("+")[0]
    parts: list[int] = []
    for seg in base.split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(0)
    return tuple(parts)
