from __future__ import annotations

import json
from pathlib import Path

from dan.cli.organism import build_parser, main
from dan.worker.organisms.reference_demo import (
    DeepResearchOrganDemoReport,
    ReferenceOrganismDemoReport,
)


def test_build_parser_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args([])

    assert args.task_id == "bounded-project-execution"
    assert args.delivery_target == "repo-change brief"
    assert args.organism_id == "reference-project-execution"
    assert args.model == "stub-model"
    assert args.live is False
    assert args.research_only is False
    assert args.research_readers is None
    assert args.json is False


def test_main_json_outputs_completed_report(tmp_path, capsys) -> None:
    output_path = tmp_path / "report.json"

    exit_code = main(
        [
            "--json",
            "--workdir",
            str(tmp_path / "workdir"),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert payload["status"] == "completed"
    assert payload["selected_attempt"] == 2
    assert payload["build_candidate_ids"] == ["candidate-1", "candidate-2"]

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["trace_id"] == payload["trace_id"]


def test_main_text_summary_mentions_candidates(tmp_path, capsys) -> None:
    exit_code = main(["--workdir", str(tmp_path / "summary-workdir")])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Status: completed" in stdout
    assert "Candidates: candidate-1, candidate-2" in stdout
    assert "Stages: planning:0 -> research:0 -> build:1" in stdout


def test_main_lists_local_tools(capsys) -> None:
    exit_code = main(["--list-tools"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "file_read [file]" in stdout
    assert "shell_command [system]" in stdout


def test_main_live_mode_uses_live_runner(tmp_path, capsys, monkeypatch) -> None:
    async def _fake_live_runner(*_args, **kwargs):
        assert kwargs["model"] == "gpt-test"
        assert kwargs["research_reader_count"] == 6
        assert kwargs["tool_ids"] == [
            "list_directory",
            "file_read",
            "file_edit",
            "file_write",
            "shell_command",
            "web_search",
            "git_status",
            "git_diff",
            "git_log",
        ]
        assert Path(kwargs["workspace_root"]) == (tmp_path / "workspace").resolve()
        return ReferenceOrganismDemoReport(
            status="completed",
            trace_id="trace-live",
            selected_attempt=1,
            final_output={"delivery_summary": "live-ok"},
            stage_sequence=["planning:0"],
        )

    monkeypatch.setattr("dan.cli.organism._build_live_provider", lambda *args, **kwargs: object())
    monkeypatch.setattr("dan.cli.organism.run_reference_organism_live", _fake_live_runner)

    exit_code = main(
        [
            "--live",
            "--model",
            "gpt-test",
            "--workspace",
            str(tmp_path / "workspace"),
            "--workdir",
            str(tmp_path / "workdir"),
            "--research-readers",
            "6",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["trace_id"] == "trace-live"
    assert payload["final_output"]["delivery_summary"] == "live-ok"


def test_main_research_only_mode_uses_research_runner(tmp_path, capsys, monkeypatch) -> None:
    async def _fake_research_runner(*_args, **kwargs):
        assert kwargs["model"] == "gpt-test"
        assert kwargs["research_reader_count"] == 5
        assert kwargs["tool_ids"] == [
            "list_directory",
            "file_read",
            "file_edit",
            "file_write",
            "shell_command",
            "web_search",
            "git_status",
            "git_diff",
            "git_log",
        ]
        return DeepResearchOrganDemoReport(
            status="completed",
            trace_id="trace-research",
            final_output={
                "findings": ["Grounded finding."],
                "confidence": 0.82,
            },
            output_ref_ids=["trace-research:organ-output"],
        )

    monkeypatch.setattr("dan.cli.organism._build_live_provider", lambda *args, **kwargs: object())
    monkeypatch.setattr("dan.cli.organism.run_deep_research_organ_live", _fake_research_runner)

    exit_code = main(
        [
            "--research-only",
            "--live",
            "--model",
            "gpt-test",
            "--workspace",
            str(tmp_path / "workspace"),
            "--workdir",
            str(tmp_path / "workdir"),
            "--research-readers",
            "5",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["trace_id"] == "trace-research"
    assert payload["final_output"]["confidence"] == 0.82
