from __future__ import annotations

import json
from pathlib import Path

from dan.cli import organism_log as organism_log_cli
from dan.worker.organism_log import read_organism_log


def test_import_command_writes_normalized_organism_log(tmp_path: Path, capsys) -> None:
    source = tmp_path / "external.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-04-18T00:00:00Z",
                        "type": "llm_requested",
                        "llm_call_id": "model-1",
                        "run_id": "trace-1",
                        "agent_id": "planner",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-04-18T00:00:03Z",
                        "type": "llm_completed",
                        "llm_call_id": "model-1",
                        "run_id": "trace-1",
                        "agent_id": "planner",
                        "status": "completed",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output = tmp_path / "normalized.jsonl"
    exit_code = organism_log_cli.main(
        ["import", str(source), "--output", str(output), "--json"]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["adapter"] == "generic_json"
    assert Path(payload["output_path"]) == output.resolve()
    assert output.exists()

    rows = read_organism_log(output)
    assert rows[0]["schema"] == "organism_log_v1"
    assert rows[0]["stream"] == "run"


def test_summarize_command_supports_field_overrides(tmp_path: Path, capsys) -> None:
    source = tmp_path / "custom.json"
    source.write_text(
        json.dumps(
            [
                {
                    "created": "2026-04-18T00:00:00Z",
                    "kind_name": "custom_stage_started",
                    "op": "custom-1",
                },
                {
                    "created": "2026-04-18T00:00:02Z",
                    "kind_name": "custom_stage_completed",
                    "op": "custom-1",
                    "status": "completed",
                    "summary": "Custom stage",
                },
            ]
        ),
        encoding="utf-8",
    )

    exit_code = organism_log_cli.main(
        [
            "summarize",
            str(source),
            "--field",
            "timestamp=created",
            "--field",
            "event=kind_name",
            "--field",
            "span_id=op",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["adapter"] == "generic_json"
    assert payload["span_count"] >= 1
    assert any(item["summary"] == "Custom stage" for item in payload["slowest_spans"])


def test_analyze_command_outputs_timeline_and_graph_payload(tmp_path: Path, capsys) -> None:
    source = tmp_path / "analysis.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-04-18T00:00:00Z",
                        "type": "stage_started",
                        "op_id": "stage-1",
                        "worker": "planner",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-04-18T00:00:02Z",
                        "type": "stage_completed",
                        "op_id": "stage-1",
                        "worker": "planner",
                        "status": "completed",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-04-18T00:00:03Z",
                        "type": "stage_started",
                        "op_id": "stage-2",
                        "worker": "planner",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-04-18T00:00:05Z",
                        "type": "stage_completed",
                        "op_id": "stage-2",
                        "worker": "planner",
                        "status": "completed",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = organism_log_cli.main(
        [
            "analyze",
            str(source),
            "--field",
            "timestamp=ts",
            "--field",
            "event=type",
            "--field",
            "span_id=op_id",
            "--field",
            "worker_id=worker",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["adapter"] == "generic_json"
    assert payload["timeline"]["spans"]
    assert payload["graph"]["edges"]
    assert payload["graph"]["critical_path_span_ids"] == ["stage-1", "stage-2"]


def test_scheduler_replay_command_outputs_lower_bounds_and_barrier_payload(
    tmp_path: Path,
    capsys,
) -> None:
    source = tmp_path / "scheduler.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-04-23T00:00:00Z",
                        "type": "stage_started",
                        "op_id": "plan",
                        "worker": "planner",
                        "summary": "Plan",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-04-23T00:00:02Z",
                        "type": "stage_completed",
                        "op_id": "plan",
                        "worker": "planner",
                        "status": "completed",
                        "summary": "Plan",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-04-23T00:00:02Z",
                        "type": "stage_started",
                        "op_id": "build",
                        "worker": "planner",
                        "summary": "Build",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-04-23T00:00:06Z",
                        "type": "stage_completed",
                        "op_id": "build",
                        "worker": "planner",
                        "status": "completed",
                        "summary": "Build",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-04-23T00:00:02Z",
                        "type": "stage_started",
                        "op_id": "review",
                        "worker": "reviewer",
                        "summary": "Review",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-04-23T00:00:05Z",
                        "type": "stage_completed",
                        "op_id": "review",
                        "worker": "reviewer",
                        "status": "completed",
                        "summary": "Review",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-04-23T00:00:06Z",
                        "type": "stage_started",
                        "op_id": "aggregate",
                        "worker": "aggregator",
                        "summary": "Aggregate",
                        "blocked": ["build", "review"],
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-04-23T00:00:07Z",
                        "type": "stage_completed",
                        "op_id": "aggregate",
                        "worker": "aggregator",
                        "status": "completed",
                        "summary": "Aggregate",
                        "blocked": ["build", "review"],
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-04-23T00:00:07Z",
                        "type": "stage_started",
                        "op_id": "validate",
                        "worker": "aggregator",
                        "summary": "Validate",
                        "blocked": ["aggregate"],
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-04-23T00:00:08Z",
                        "type": "stage_completed",
                        "op_id": "validate",
                        "worker": "aggregator",
                        "status": "completed",
                        "summary": "Validate",
                        "blocked": ["aggregate"],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = organism_log_cli.main(
        [
            "scheduler-replay",
            str(source),
            "--field",
            "timestamp=ts",
            "--field",
            "event=type",
            "--field",
            "span_id=op_id",
            "--field",
            "worker_id=worker",
            "--field",
            "blocked_by=blocked",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["adapter"] == "generic_json"
    assert payload["capacity_source"] == "observed_parallelism"
    assert payload["critical_path_lower_bound_ms"] == 8000
    assert payload["scheduler_lower_bound_ms"] == 8000
    assert payload["terminal_barrier"]["span_ids"] == ["aggregate", "validate"]
