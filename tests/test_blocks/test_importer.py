"""Tests for block import (installation) logic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dan.blocks.export import export_workflow_block, pack_block
from dan.blocks.importer import import_block
from dan.blocks.models import MANIFEST_FILENAME, DanBlock
from dan.models.graph import Graph


class TestImportFromDirectory:
    def test_installs_to_user_scope(self, simple_workflow: Graph, tmp_path: Path) -> None:
        src = export_workflow_block(simple_workflow, tmp_path / "src", name="dir-test")
        user_root = tmp_path / "home"

        installed = import_block(
            src,
            scope="user",
            _user_root_override=user_root,
        )
        assert installed.name == "dir-test"
        assert installed.version == "0.1.0"
        assert installed.install_path.exists()
        assert (installed.install_path / MANIFEST_FILENAME).exists()

    def test_installs_to_workspace_scope(self, simple_workflow: Graph, tmp_path: Path) -> None:
        src = export_workflow_block(simple_workflow, tmp_path / "src", name="ws-test")
        ws = tmp_path / "workspace"

        installed = import_block(src, scope="workspace", workspace=ws)
        assert "workspace" in str(installed.install_path)
        assert installed.install_path.exists()

    def test_skip_if_already_installed(self, simple_workflow: Graph, tmp_path: Path) -> None:
        src = export_workflow_block(simple_workflow, tmp_path / "src", name="dup")
        user_root = tmp_path / "home"

        import_block(src, scope="user", _user_root_override=user_root)
        installed2 = import_block(src, scope="user", _user_root_override=user_root)
        assert installed2.install_path.exists()

    def test_force_overwrites(self, simple_workflow: Graph, tmp_path: Path) -> None:
        src = export_workflow_block(simple_workflow, tmp_path / "src", name="force")
        user_root = tmp_path / "home"

        import_block(src, scope="user", _user_root_override=user_root)
        installed = import_block(
            src, scope="user", force=True, _user_root_override=user_root
        )
        assert installed.install_path.exists()


class TestImportFromTarball:
    def test_installs_from_tarball(self, simple_workflow: Graph, tmp_path: Path) -> None:
        block_dir = export_workflow_block(simple_workflow, tmp_path / "src", name="tar-test")
        tarball = pack_block(block_dir)
        user_root = tmp_path / "home"

        installed = import_block(
            tarball,
            scope="user",
            _user_root_override=user_root,
        )
        assert installed.name == "tar-test"
        assert (installed.install_path / MANIFEST_FILENAME).exists()


class TestImportErrors:
    def test_rejects_invalid_source(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not a directory"):
            import_block(tmp_path / "nonexistent.txt")

    def test_rejects_workspace_without_path(self, simple_workflow: Graph, tmp_path: Path) -> None:
        src = export_workflow_block(simple_workflow, tmp_path / "src", name="e")
        with pytest.raises(ValueError, match="workspace path required"):
            import_block(src, scope="workspace", workspace=None)
