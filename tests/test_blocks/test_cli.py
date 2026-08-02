"""Tests for dan-blocks CLI argument parsing and subcommands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dan.blocks.export import export_workflow_block
from dan.blocks.models import MANIFEST_FILENAME, DanBlock
from dan.cli.blocks import main
from dan.models.graph import Graph


class TestCLIParsing:
    def test_no_args_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "usage" in captured.out.lower() or "dan-blocks" in captured.out.lower()

    def test_list_no_blocks(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", tmp_path):
            main(["list"])
        captured = capsys.readouterr()
        assert "No blocks installed" in captured.out

    def test_list_with_blocks(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        user_root = tmp_path / "blocks"
        d = user_root / "demo" / "0.1.0"
        d.mkdir(parents=True)
        manifest = DanBlock(name="demo", version="0.1.0", description="A demo")
        (d / MANIFEST_FILENAME).write_text(
            json.dumps(manifest.model_dump(), indent=2), encoding="utf-8"
        )

        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", user_root):
            main(["list"])
        captured = capsys.readouterr()
        assert "demo" in captured.out

    def test_info_not_found(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", tmp_path):
            with pytest.raises(SystemExit):
                main(["info", "nonexistent"])

    def test_export_workflow(
        self, simple_workflow: Graph, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wf_path = tmp_path / "workflow.json"
        wf_path.write_text(simple_workflow.model_dump_json(indent=2), encoding="utf-8")

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        main(["export", str(wf_path), "--name", "cli-export", "-o", str(out_dir)])

        captured = capsys.readouterr()
        assert "Exported" in captured.out
        assert (out_dir / "cli-export" / MANIFEST_FILENAME).exists()

    def test_pack_subcommand(
        self, simple_workflow: Graph, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        block_dir = export_workflow_block(simple_workflow, tmp_path, name="packable")
        main(["pack", str(block_dir)])

        captured = capsys.readouterr()
        assert "Packed" in captured.out

    def test_remove_not_found(self, tmp_path: Path) -> None:
        with patch("dan.blocks.registry._USER_BLOCKS_ROOT", tmp_path):
            with pytest.raises(SystemExit):
                main(["remove", "nothing"])
