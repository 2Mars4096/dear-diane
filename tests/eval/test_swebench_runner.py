from __future__ import annotations

import json
from pathlib import Path
import subprocess

from dan.cli.code import SweBenchInstance
from tests.eval.swebench_runner import load_swebench_instance, run_swebench_instance


def test_load_swebench_instance_from_local_jsonl(tmp_path) -> None:
    catalog = tmp_path / "instances.jsonl"
    catalog.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "instance_id": "first",
                        "repo": "owner/repo-one",
                        "base_commit": "abc123",
                        "problem_statement": "First issue",
                    }
                ),
                json.dumps(
                    {
                        "instance_id": "second",
                        "repo": "owner/repo-two",
                        "base_commit": "def456",
                        "problem_statement": "Second issue",
                        "FAIL_TO_PASS": '["tests/test_two.py::test_case"]',
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    instance = load_swebench_instance(
        instance_file=catalog,
        dataset_repo=None,
        split=None,
        instance_id="second",
    )

    assert instance.instance_id == "second"
    assert instance.repo == "owner/repo-two"
    assert instance.fail_to_pass == ["tests/test_two.py::test_case"]


def test_run_swebench_instance_records_paths_and_status(tmp_path, monkeypatch) -> None:
    instance = SweBenchInstance.model_validate(
        {
            "instance_id": "marshmallow-code__marshmallow-1359",
            "repo": "marshmallow-code/marshmallow",
            "base_commit": "abc123",
            "problem_statement": "Fix the DateTime inner field regression.",
        }
    )
    repo_cache_dir = tmp_path / "cache"
    results_root = tmp_path / "results"

    monkeypatch.setattr(
        "tests.eval.swebench_runner._ensure_repo_cache",
        lambda repo, repo_cache_dir: repo_cache_dir / repo.replace("/", "__"),
    )
    monkeypatch.setattr(
        "tests.eval.swebench_runner._ensure_commit",
        lambda cache_path, commit: None,
    )
    monkeypatch.setattr(
        "tests.eval.swebench_runner._prepare_workspace",
        lambda cache_path, workspace_root, commit: workspace_root.mkdir(parents=True, exist_ok=True),
    )

    def _fake_subprocess_run(command, cwd, text, capture_output, env):
        report_path = Path(command[command.index("--output") + 1])
        predictions_path = Path(command[command.index("--swebench-predictions-path") + 1])
        report_path.parent.mkdir(parents=True, exist_ok=True)
        predictions_path.parent.mkdir(parents=True, exist_ok=True)
        prediction_payload = {
            "instance_id": instance.instance_id,
            "model_name_or_path": "kimi-k2.5",
            "model_patch": "diff --git a/file.py b/file.py\n",
        }
        predictions_path.write_text(
            json.dumps(prediction_payload, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        report_payload = {
            "status": "completed",
            "outputs": {
                "swebench": {
                    "prediction_artifact_path": str(report_path.parent / "swebench-prediction.json"),
                    "patch_artifact_path": str(report_path.parent / "swebench.patch"),
                }
            },
        }
        report_path.write_text(json.dumps(report_payload), encoding="utf-8")
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout='{"status":"completed"}',
            stderr="",
        )

    monkeypatch.setattr(
        "tests.eval.swebench_runner.subprocess.run",
        _fake_subprocess_run,
    )

    record = run_swebench_instance(
        instance=instance,
        dataset_repo="princeton-nlp/SWE-bench_Lite",
        split="dev",
        results_root=results_root,
        repo_cache_dir=repo_cache_dir,
        model="kimi-k2.5",
        thinking_mode="disabled",
        completion_timeout_seconds=60.0,
        max_tool_rounds=8,
    )

    assert record.exit_code == 0
    assert record.report_status == "completed"
    assert Path(record.instance_file).exists()
    assert Path(record.stdout_path).exists()
    assert Path(record.stderr_path).exists()
    assert Path(record.predictions_path).exists()
    assert record.prediction_artifact_path is not None
    assert record.patch_artifact_path is not None
