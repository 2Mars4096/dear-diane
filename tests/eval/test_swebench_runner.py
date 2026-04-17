from __future__ import annotations

import json
from pathlib import Path
import subprocess

from dan.cli.code import SweBenchInstance
from tests.eval.swebench_batch_runner import main as batch_main
from tests.eval.swebench_runner import (
    SweBenchRunRecord,
    load_swebench_instance,
    load_swebench_instances,
    run_swebench_instance,
)


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


def test_load_swebench_instances_from_local_jsonl(tmp_path) -> None:
    catalog = tmp_path / "instances.jsonl"
    catalog.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "instance_id": "second",
                        "repo": "owner/repo-two",
                        "base_commit": "def456",
                        "problem_statement": "Second issue",
                    }
                ),
                json.dumps(
                    {
                        "instance_id": "first",
                        "repo": "owner/repo-one",
                        "base_commit": "abc123",
                        "problem_statement": "First issue",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    instances = load_swebench_instances(
        instance_file=catalog,
        dataset_repo=None,
        split=None,
    )

    assert [instance.instance_id for instance in instances] == ["first", "second"]


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

    def _fake_subprocess_run(command, cwd, text, capture_output, env, timeout=None):
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
        run_timeout_seconds=120.0,
    )

    assert record.exit_code == 0
    assert record.report_status == "completed"
    assert Path(record.instance_file).exists()
    assert Path(record.stdout_path).exists()
    assert Path(record.stderr_path).exists()
    assert Path(record.predictions_path).exists()
    assert record.prediction_artifact_path is not None
    assert record.patch_artifact_path is not None


def test_batch_runner_seeds_and_skips_completed_predictions(tmp_path, monkeypatch) -> None:
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
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    seed = tmp_path / "seed.jsonl"
    seed.write_text(
        json.dumps(
            {
                "instance_id": "first",
                "model_name_or_path": "kimi-k2.5",
                "model_patch": "diff --git a/a.py b/a.py\n",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def _fake_run_swebench_instance(**kwargs):
        instance = kwargs["instance"]
        run_dir = tmp_path / "run" / instance.instance_id
        predictions_path = run_dir / "predictions.jsonl"
        predictions_path.parent.mkdir(parents=True, exist_ok=True)
        predictions_path.write_text(
            json.dumps(
                {
                    "instance_id": instance.instance_id,
                    "model_name_or_path": "kimi-k2.5",
                    "model_patch": "diff --git a/b.py b/b.py\n",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return SweBenchRunRecord(
            instance_id=instance.instance_id,
            repo=str(instance.repo or ""),
            base_commit=str(instance.base_commit or ""),
            model="kimi-k2.5",
            dataset_repo=None,
            split=None,
            started_at="2026-04-17T00:00:00+00:00",
            duration_seconds=1.0,
            exit_code=0,
            workspace_root=str(run_dir / "workspace"),
            run_dir=str(run_dir),
            instance_file=str(run_dir / "instance.json"),
            report_path=str(run_dir / "report.json"),
            stdout_path=str(run_dir / "stdout.log"),
            stderr_path=str(run_dir / "stderr.log"),
            predictions_path=str(predictions_path),
            prediction_artifact_path=None,
            patch_artifact_path=None,
            report_status="completed",
            command=[],
        )

    monkeypatch.setattr(
        "tests.eval.swebench_batch_runner.run_swebench_instance",
        _fake_run_swebench_instance,
    )
    batch_root = tmp_path / "batch"

    exit_code = batch_main(
        [
            "--instance-file",
            str(catalog),
            "--batch-root",
            str(batch_root),
            "--seed-prediction",
            str(seed),
        ]
    )

    assert exit_code == 0
    combined = [
        json.loads(line)
        for line in (batch_root / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["instance_id"] for row in combined] == ["first", "second"]
