"""Integration tests for CLI — end-to-end with mock engine."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.cli.run import (
    CLIHumanRenderer,
    build_parser,
    detect_source_type,
    load_graph_from_json,
    load_graph_from_python,
    load_workflow,
    run_workflow,
)
from dan.engine.executor import HumanRenderRequest


def _minimal_graph_dict(name: str = "test") -> dict:
    return {
        "version": "dan_graph_v1",
        "metadata": {"name": name},
        "nodes": [],
        "edges": [],
    }


class TestLoadGraphFromJson:
    def test_valid_json(self, tmp_path: Path):
        f = tmp_path / "wf.json"
        f.write_text(json.dumps(_minimal_graph_dict("from-json")))
        loaded = load_graph_from_json(f)
        assert loaded.metadata.name == "from-json"
        assert loaded.version == "dan_graph_v1"


class TestLoadGraphFromPython:
    def test_graph_attribute(self, tmp_path: Path):
        code = '''
from dan.models.graph import Graph
graph = Graph(metadata={"name": "py-attr"}, nodes=[], edges=[])
'''
        f = tmp_path / "build_wf.py"
        f.write_text(code)
        loaded = load_graph_from_python(f)
        assert loaded.metadata.name == "py-attr"

    def test_build_function(self, tmp_path: Path):
        code = '''
from dan.models.graph import Graph
def build():
    return Graph(metadata={"name": "py-build"}, nodes=[], edges=[])
'''
        f = tmp_path / "build_fn.py"
        f.write_text(code)
        loaded = load_graph_from_python(f)
        assert loaded.metadata.name == "py-build"

    def test_missing_graph_and_build_exits(self, tmp_path: Path):
        code = "x = 42\n"
        f = tmp_path / "bad.py"
        f.write_text(code)
        with pytest.raises(SystemExit):
            load_graph_from_python(f)

    def test_wrapper_can_import_sibling_package(self, tmp_path: Path):
        pkg = tmp_path / "workflows"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "inner.py").write_text(
            "\n".join(
                [
                    "from dan.models.graph import Graph",
                    'graph = Graph(metadata={"name": "py-wrapper"}, nodes=[], edges=[])',
                ]
            )
        )
        wrapper = tmp_path / "wrapper.py"
        wrapper.write_text("from workflows.inner import graph\n")

        loaded = load_graph_from_python(wrapper)
        assert loaded.metadata.name == "py-wrapper"


class TestLoadWorkflow:
    def test_json(self, tmp_path: Path):
        f = tmp_path / "wf.json"
        f.write_text(json.dumps(_minimal_graph_dict("json-wf")))
        loaded = load_workflow(str(f), "json")
        assert loaded.metadata.name == "json-wf"

    def test_python(self, tmp_path: Path):
        code = '''
from dan.models.graph import Graph
graph = Graph(metadata={"name": "py-wf"}, nodes=[], edges=[])
'''
        f = tmp_path / "wf.py"
        f.write_text(code)
        loaded = load_workflow(str(f), "python")
        assert loaded.metadata.name == "py-wf"

    def test_rejects_workflow_outside_workspace(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text(
            "\n".join(
                [
                    "from dan.models.graph import Graph",
                    'graph = Graph(metadata={"name": "outside"}, nodes=[], edges=[])',
                ]
            )
        )

        with pytest.raises(SystemExit):
            load_workflow(str(outside), "python", workspace=workspace)


class TestRunWorkflowIntegration:
    @pytest.mark.asyncio
    async def test_headless_json_workflow(self, tmp_path: Path):
        """Full end-to-end: load JSON, run engine (mocked), check output."""
        f = tmp_path / "wf.json"
        f.write_text(json.dumps(_minimal_graph_dict("e2e")))

        parser = build_parser()
        args = parser.parse_args([
            str(f),
            "--headless",
            "--quiet",
            "--local",
            "--workspace",
            str(tmp_path),
        ])

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.run_id = "mock-run"
        mock_result.outputs = {"text": "hello"}
        mock_result.errors = {}
        mock_result.node_statuses = {}

        with patch("dan.engine.scheduler.Engine.run", new_callable=AsyncMock, return_value=mock_result) as mock_run:
            code = await run_workflow(args)

        assert code == 0
        mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_quiet_output_includes_execution_mode(self, tmp_path: Path, capsys):
        f = tmp_path / "wf.json"
        f.write_text(json.dumps(_minimal_graph_dict("mode-json")))

        parser = build_parser()
        args = parser.parse_args(
            [str(f), "--headless", "--quiet", "--local", "--workspace", str(tmp_path)]
        )

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.run_id = "mode-run"
        mock_result.outputs = {"text": "hello"}
        mock_result.errors = {}
        mock_result.node_statuses = {}
        mock_result.metadata = {}

        with patch("dan.engine.scheduler.Engine.run", new_callable=AsyncMock, return_value=mock_result):
            code = await run_workflow(args)

        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["execution_mode"] == "local"
        assert payload["server_requested"] is False

    @pytest.mark.asyncio
    async def test_failed_run_returns_1(self, tmp_path: Path):
        f = tmp_path / "wf.json"
        f.write_text(json.dumps(_minimal_graph_dict("fail")))

        parser = build_parser()
        args = parser.parse_args(
            [str(f), "--headless", "--quiet", "--local", "--workspace", str(tmp_path)]
        )

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.run_id = "fail-run"
        mock_result.outputs = {}
        mock_result.errors = {"n1": "boom"}
        mock_result.node_statuses = {}

        with patch("dan.engine.scheduler.Engine.run", new_callable=AsyncMock, return_value=mock_result):
            code = await run_workflow(args)

        assert code == 1

    @pytest.mark.asyncio
    async def test_explicit_server_failure_returns_2(self, tmp_path: Path):
        f = tmp_path / "wf.json"
        f.write_text(json.dumps(_minimal_graph_dict("strict-server")))

        parser = build_parser()
        args = parser.parse_args(
            [str(f), "--headless", "--quiet", "--server", "http://127.0.0.1:8000"]
        )

        with (
            patch("dan.cli.load_env"),
            patch(
                "dan.cli.resolve_config",
                return_value={
                    "api_key": "",
                    "model": None,
                    "base_url": None,
                    "workspace": str(tmp_path),
                },
            ),
            patch(
                "dan.client.local.DanClientOrLocal.detect_mode",
                new=AsyncMock(side_effect=RuntimeError("dan-serve not reachable")),
            ),
        ):
            code = await run_workflow(args)

        assert code == 2


class TestStatusAndLogs:
    def test_status_no_runs(self, tmp_path: Path):
        from dan.cli.status import list_runs
        runs = list_runs(tmp_path)
        assert runs == []

    def test_status_with_pid_file(self, tmp_path: Path):
        from dan.cli.status import list_runs
        pid_file = tmp_path / "abc123.pid"
        pid_file.write_text("99999\nabc123\n")
        events_file = tmp_path / "abc123.events.jsonl"
        events_file.write_text("")
        runs = list_runs(tmp_path)
        assert len(runs) == 1
        assert runs[0]["run_id"] == "abc123"
        assert runs[0]["pid"] == 99999

    def test_logs_parse_events(self, tmp_path: Path):
        from dan.cli.status import _parse_events_summary
        events_file = tmp_path / "run1.events.jsonl"
        events = [
            {"event_type": "run_started", "run_id": "r1", "timestamp": 1000, "data": {"total_nodes": 3}},
            {"event_type": "node_completed", "run_id": "r1", "timestamp": 1001, "data": {}},
            {"event_type": "node_completed", "run_id": "r1", "timestamp": 1002, "data": {}},
            {"event_type": "run_completed", "run_id": "r1", "timestamp": 1003, "data": {}},
        ]
        events_file.write_text("\n".join(json.dumps(e) for e in events))
        summary = _parse_events_summary(events_file)
        assert summary["run_id"] == "r1"
        assert summary["total_nodes"] == 3
        assert summary["completed"] == 2
        assert summary["success"] is True
