"""Tests for BlockRegistry — scan, list, get, remove, index rebuild."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dan.blocks.models import MANIFEST_FILENAME, DanBlock
from dan.blocks.registry import BlockRegistry, _INDEX_FILENAME


def _create_installed_block(
    root: Path,
    name: str,
    version: str,
    *,
    block_type: str = "workflow",
    description: str = "",
) -> Path:
    """Helper — create a minimal installed block at root/name/version/."""
    d = root / name / version
    d.mkdir(parents=True, exist_ok=True)
    manifest = DanBlock(
        name=name, version=version, block_type=block_type, description=description
    )
    (d / MANIFEST_FILENAME).write_text(
        json.dumps(manifest.model_dump(), indent=2), encoding="utf-8"
    )
    return d


class TestScan:
    def test_discovers_blocks(self, tmp_path: Path) -> None:
        user_root = tmp_path / "user"
        _create_installed_block(user_root, "alpha", "1.0.0")
        _create_installed_block(user_root, "beta", "0.2.0")

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", user_root):
            reg = BlockRegistry()
            reg.scan()

        blocks = reg.list_blocks()
        names = [b.name for b in blocks]
        assert "alpha" in names
        assert "beta" in names

    def test_workspace_overrides_user(self, tmp_path: Path) -> None:
        user_root = tmp_path / "user"
        ws = tmp_path / "workspace"
        ws_root = ws / ".dan" / "blocks"

        _create_installed_block(user_root, "shared", "1.0.0", description="user copy")
        _create_installed_block(ws_root, "shared", "1.0.0", description="ws copy")

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", user_root):
            reg = BlockRegistry(workspace=ws)
            reg.scan()

        block = reg.get_block("shared", "1.0.0")
        assert block is not None
        assert block.metadata.description == "ws copy"

    def test_writes_index(self, tmp_path: Path) -> None:
        user_root = tmp_path / "user"
        _create_installed_block(user_root, "idx", "0.1.0")

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", user_root):
            reg = BlockRegistry()
            reg.scan()

        index_path = user_root / _INDEX_FILENAME
        assert index_path.exists()
        entries = json.loads(index_path.read_text())
        assert len(entries) == 1

    def test_skips_invalid_manifests(self, tmp_path: Path) -> None:
        user_root = tmp_path / "user"
        bad_dir = user_root / "bad" / "0.0.1"
        bad_dir.mkdir(parents=True)
        (bad_dir / MANIFEST_FILENAME).write_text("{invalid json", encoding="utf-8")

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", user_root):
            reg = BlockRegistry()
            reg.scan()

        assert len(reg.list_blocks()) == 0


class TestGetBlock:
    def test_get_by_name_version(self, tmp_path: Path) -> None:
        user_root = tmp_path / "user"
        _create_installed_block(user_root, "x", "1.0.0")
        _create_installed_block(user_root, "x", "2.0.0")

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", user_root):
            reg = BlockRegistry()
            reg.scan()

        assert reg.get_block("x", "1.0.0") is not None
        assert reg.get_block("x", "1.0.0").version == "1.0.0"

    def test_get_latest_when_no_version(self, tmp_path: Path) -> None:
        user_root = tmp_path / "user"
        _create_installed_block(user_root, "x", "1.0.0")
        _create_installed_block(user_root, "x", "2.0.0")

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", user_root):
            reg = BlockRegistry()
            reg.scan()

        latest = reg.get_block("x")
        assert latest is not None
        assert latest.version == "2.0.0"

    def test_get_nonexistent_returns_none(self, tmp_path: Path) -> None:
        user_root = tmp_path / "user"
        user_root.mkdir(parents=True)

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", user_root):
            reg = BlockRegistry()
            reg.scan()

        assert reg.get_block("nonexistent") is None


class TestRemoveBlock:
    def test_removes_and_cleans_up(self, tmp_path: Path) -> None:
        user_root = tmp_path / "user"
        _create_installed_block(user_root, "gone", "0.1.0")

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", user_root):
            reg = BlockRegistry()
            reg.scan()
            assert reg.remove_block("gone", "0.1.0") is True
            assert reg.get_block("gone") is None
            assert not (user_root / "gone" / "0.1.0").exists()

    def test_remove_nonexistent_returns_false(self, tmp_path: Path) -> None:
        user_root = tmp_path / "user"
        user_root.mkdir(parents=True)

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", user_root):
            reg = BlockRegistry()
            reg.scan()
            assert reg.remove_block("nope", "0.0.0") is False

    def test_multiple_versions_keep_siblings(self, tmp_path: Path) -> None:
        user_root = tmp_path / "user"
        _create_installed_block(user_root, "multi", "1.0.0")
        _create_installed_block(user_root, "multi", "2.0.0")

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", user_root):
            reg = BlockRegistry()
            reg.scan()
            reg.remove_block("multi", "1.0.0")

        assert not (user_root / "multi" / "1.0.0").exists()
        assert (user_root / "multi" / "2.0.0").exists()
