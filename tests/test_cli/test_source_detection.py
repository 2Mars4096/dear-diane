"""Unit tests for workflow source type detection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from dan.cli.run import detect_source_type


class TestDetectSourceType:
    def test_json_file(self, tmp_path: Path):
        f = tmp_path / "wf.json"
        f.write_text("{}")
        assert detect_source_type(str(f)) == "json"

    def test_markdown_file(self, tmp_path: Path):
        f = tmp_path / "workflow.md"
        f.write_text("# Workflow")
        assert detect_source_type(str(f)) == "markdown"

    def test_markdown_directory(self, tmp_path: Path):
        d = tmp_path / "agents"
        d.mkdir()
        (d / "workflow.md").write_text("# WF")
        assert detect_source_type(str(d)) == "markdown"

    def test_python_file(self, tmp_path: Path):
        f = tmp_path / "build.py"
        f.write_text("graph = None")
        assert detect_source_type(str(f)) == "python"

    def test_nl_goal_no_extension(self):
        assert detect_source_type("write a paper about AI safety") == "nl"

    def test_nl_goal_nonexistent_path(self):
        assert detect_source_type("nonexistent_something") == "nl"

    def test_force_goal_flag(self, tmp_path: Path):
        f = tmp_path / "real.json"
        f.write_text("{}")
        assert detect_source_type(str(f), force_goal=True) == "nl"

    def test_missing_json_file_exits(self):
        with pytest.raises(SystemExit):
            detect_source_type("/nonexistent/path/workflow.json")

    def test_missing_md_file_exits(self):
        with pytest.raises(SystemExit):
            detect_source_type("/nonexistent/path/workflow.md")

    def test_missing_py_file_exits(self):
        with pytest.raises(SystemExit):
            detect_source_type("/nonexistent/path/build.py")
