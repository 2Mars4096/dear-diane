"""Minimal SWE-bench adapter for DAN Code.

Usage:
    PYTHONPATH=src:. python -m tests.eval.swebench_runner \
      --dataset-repo princeton-nlp/SWE-bench_Lite \
      --split dev \
      --instance-id marshmallow-code__marshmallow-1359 \
      --model kimi-k2.5
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

from huggingface_hub import hf_hub_download
import pandas as pd

from dan.cli.code import SweBenchInstance
from tests.eval import RESULTS_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO_CACHE_DIR = RESULTS_DIR / "swebench_repo_cache"
GIT_BIN = "/usr/bin/git" if Path("/usr/bin/git").exists() else "git"


@dataclass
class SweBenchRunRecord:
    instance_id: str
    repo: str
    base_commit: str
    model: str
    dataset_repo: str | None
    split: str | None
    started_at: str
    duration_seconds: float
    exit_code: int
    workspace_root: str
    run_dir: str
    instance_file: str
    report_path: str
    stdout_path: str
    stderr_path: str
    predictions_path: str
    prediction_artifact_path: str | None
    patch_artifact_path: str | None
    report_status: str | None
    command: list[str]
    error: str | None = None
    timed_out: bool = False


def _timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [GIT_BIN, *args],
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        capture_output=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def _github_clone_url(repo: str) -> str:
    return f"https://github.com/{repo}.git"


def _repo_cache_path(repo_cache_dir: Path, repo: str) -> Path:
    return repo_cache_dir / repo.replace("/", "__")


def _normalize_instance_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if "FAIL_TO_PASS" in normalized and "fail_to_pass" not in normalized:
        normalized["fail_to_pass"] = normalized["FAIL_TO_PASS"]
    if "PASS_TO_PASS" in normalized and "pass_to_pass" not in normalized:
        normalized["pass_to_pass"] = normalized["PASS_TO_PASS"]
    if "hints_text" in normalized and "requirements" not in normalized:
        normalized["requirements"] = normalized["hints_text"]
    return normalized


def _load_local_instance_file(
    path: Path,
    *,
    instance_id: str | None,
) -> SweBenchInstance:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        rows = [
            _normalize_instance_payload(json.loads(line))
            for line in raw.splitlines()
            if line.strip()
        ]
    else:
        decoded = json.loads(raw)
        if isinstance(decoded, list):
            rows = [_normalize_instance_payload(item) for item in decoded]
        elif isinstance(decoded, dict):
            rows = [_normalize_instance_payload(decoded)]
        else:
            raise ValueError(f"Unsupported instance payload in {path}")
    if not rows:
        raise ValueError(f"No instances found in {path}")
    if instance_id:
        rows = [row for row in rows if str(row.get("instance_id") or "") == instance_id]
        if not rows:
            raise ValueError(f"Instance {instance_id} not found in {path}")
    if len(rows) != 1:
        raise ValueError(
            f"Expected exactly one resolved instance from {path}, found {len(rows)}"
        )
    return SweBenchInstance.model_validate(rows[0])


def _download_dataset_instance(
    *,
    dataset_repo: str,
    split: str,
    instance_id: str,
) -> SweBenchInstance:
    parquet_path = Path(
        hf_hub_download(
            repo_id=dataset_repo,
            repo_type="dataset",
            filename=f"data/{split}-00000-of-00001.parquet",
        )
    )
    df = pd.read_parquet(parquet_path)
    rows = df.loc[df["instance_id"] == instance_id]
    if rows.empty:
        raise ValueError(
            f"Instance {instance_id} not found in dataset {dataset_repo} split {split}"
        )
    if len(rows.index) != 1:
        raise ValueError(
            f"Expected one row for {instance_id} in {dataset_repo}/{split}, "
            f"found {len(rows.index)}"
        )
    payload = _normalize_instance_payload(rows.iloc[0].to_dict())
    return SweBenchInstance.model_validate(payload)


def load_swebench_instances(
    *,
    instance_file: Path | None,
    dataset_repo: str | None,
    split: str | None,
    instance_ids: set[str] | None = None,
) -> list[SweBenchInstance]:
    if instance_file is not None:
        raw = instance_file.read_text(encoding="utf-8")
        if instance_file.suffix.lower() == ".jsonl":
            rows = [
                _normalize_instance_payload(json.loads(line))
                for line in raw.splitlines()
                if line.strip()
            ]
        else:
            decoded = json.loads(raw)
            if isinstance(decoded, list):
                rows = [_normalize_instance_payload(item) for item in decoded]
            elif isinstance(decoded, dict):
                rows = [_normalize_instance_payload(decoded)]
            else:
                raise ValueError(f"Unsupported instance payload in {instance_file}")
    elif dataset_repo and split:
        parquet_path = Path(
            hf_hub_download(
                repo_id=dataset_repo,
                repo_type="dataset",
                filename=f"data/{split}-00000-of-00001.parquet",
            )
        )
        df = pd.read_parquet(parquet_path)
        rows = [_normalize_instance_payload(row) for row in df.to_dict("records")]
    else:
        raise ValueError("Provide either --instance-file, or --dataset-repo + --split")
    if instance_ids is not None:
        rows = [row for row in rows if str(row.get("instance_id") or "") in instance_ids]
    instances = [SweBenchInstance.model_validate(row) for row in rows]
    instances.sort(key=lambda instance: instance.instance_id)
    return instances


def load_swebench_instance(
    *,
    instance_file: Path | None,
    dataset_repo: str | None,
    split: str | None,
    instance_id: str | None,
) -> SweBenchInstance:
    if instance_file is not None:
        return _load_local_instance_file(instance_file, instance_id=instance_id)
    if dataset_repo and split and instance_id:
        return _download_dataset_instance(
            dataset_repo=dataset_repo,
            split=split,
            instance_id=instance_id,
        )
    raise ValueError(
        "Provide either --instance-file, or --dataset-repo + --split + --instance-id"
    )


def _write_instance_artifact(instance: SweBenchInstance, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(instance.model_dump_json(indent=2), encoding="utf-8")


def _ensure_repo_cache(repo: str, repo_cache_dir: Path) -> Path:
    repo_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = _repo_cache_path(repo_cache_dir, repo)
    if cache_path.exists():
        _run_git(["fetch", "origin", "--tags", "--prune"], cwd=cache_path, check=False)
        return cache_path
    clone_url = _github_clone_url(repo)
    _run_git(["clone", clone_url, str(cache_path)], cwd=repo_cache_dir.parent)
    return cache_path


def _ensure_commit(cache_path: Path, commit: str) -> None:
    existing = _run_git(
        ["rev-parse", "--verify", f"{commit}^{{commit}}"],
        cwd=cache_path,
        check=False,
    )
    if existing.returncode == 0:
        return
    _run_git(["fetch", "origin", commit], cwd=cache_path)
    verify = _run_git(
        ["rev-parse", "--verify", f"{commit}^{{commit}}"],
        cwd=cache_path,
        check=False,
    )
    if verify.returncode != 0:
        raise RuntimeError(f"Commit {commit} is not available in {cache_path}")


def _prepare_workspace(
    *,
    cache_path: Path,
    workspace_root: Path,
    commit: str,
) -> None:
    if workspace_root.exists():
        _run_git(["worktree", "remove", "--force", str(workspace_root)], cwd=cache_path, check=False)
        shutil.rmtree(workspace_root, ignore_errors=True)
    workspace_root.parent.mkdir(parents=True, exist_ok=True)
    _run_git(
        ["worktree", "add", "--detach", str(workspace_root), commit],
        cwd=cache_path,
    )


def _build_dan_code_command(
    *,
    workspace_root: Path,
    workdir: Path,
    instance_file: Path,
    predictions_path: Path,
    report_path: Path,
    model: str,
    thinking_mode: str,
    completion_timeout_seconds: float | None,
    max_tool_rounds: int | None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "dan.cli.code",
        "--workspace",
        str(workspace_root),
        "--workdir",
        str(workdir),
        "--model",
        model,
        "--thinking-mode",
        thinking_mode,
        "--approval-mode",
        "auto",
        "--new-session",
        "--swebench-instance-file",
        str(instance_file),
        "--swebench-predictions-path",
        str(predictions_path),
        "--json",
        "--output",
        str(report_path),
    ]
    if completion_timeout_seconds is not None:
        command.extend(
            [
                "--completion-timeout-seconds",
                str(float(completion_timeout_seconds)),
            ]
        )
    if max_tool_rounds is not None:
        command.extend(["--max-tool-rounds", str(int(max_tool_rounds))])
    return command


def run_swebench_instance(
    *,
    instance: SweBenchInstance,
    dataset_repo: str | None,
    split: str | None,
    results_root: Path,
    repo_cache_dir: Path,
    model: str,
    thinking_mode: str,
    completion_timeout_seconds: float | None,
    max_tool_rounds: int | None,
    run_timeout_seconds: float | None = None,
) -> SweBenchRunRecord:
    if not str(instance.repo or "").strip():
        raise ValueError("SWE-bench instance is missing `repo`")
    if not str(instance.base_commit or "").strip():
        raise ValueError("SWE-bench instance is missing `base_commit`")
    slug = f"{_timestamp_slug()}_{instance.instance_id}"
    run_dir = results_root / slug
    workspace_root = run_dir / "workspace"
    workdir = run_dir / "dan-code"
    instance_file = run_dir / "instance.json"
    predictions_path = run_dir / "predictions.jsonl"
    report_path = run_dir / "report.json"
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_instance_artifact(instance, instance_file)

    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    prediction_artifact_path: str | None = None
    patch_artifact_path: str | None = None
    report_status: str | None = None
    error: str | None = None
    timed_out = False
    command: list[str] = []

    try:
        cache_path = _ensure_repo_cache(instance.repo or "", repo_cache_dir)
        _ensure_commit(cache_path, instance.base_commit or "")
        _prepare_workspace(
            cache_path=cache_path,
            workspace_root=workspace_root,
            commit=instance.base_commit or "",
        )
        command = _build_dan_code_command(
            workspace_root=workspace_root,
            workdir=workdir,
            instance_file=instance_file,
            predictions_path=predictions_path,
            report_path=report_path,
            model=model,
            thinking_mode=thinking_mode,
            completion_timeout_seconds=completion_timeout_seconds,
            max_tool_rounds=max_tool_rounds,
        )
        env = dict(os.environ)
        py_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = "src" + (os.pathsep + py_path if py_path else "")
        try:
            proc = subprocess.run(
                command,
                cwd=str(PROJECT_ROOT),
                text=True,
                capture_output=True,
                env=env,
                timeout=run_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            error = f"run timed out after {run_timeout_seconds} seconds"
            proc = subprocess.CompletedProcess(
                args=command,
                returncode=124,
                stdout=str(exc.stdout or ""),
                stderr=str(exc.stderr or ""),
            )
    except Exception as exc:
        proc = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        error = str(exc)

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(str(proc.stdout or ""), encoding="utf-8")
    stderr_payload = str(proc.stderr or "")
    if error:
        stderr_payload = (stderr_payload + "\n" + error).strip()
    stderr_path.write_text(stderr_payload, encoding="utf-8")

    if report_path.exists():
        try:
            report_payload = json.loads(report_path.read_text(encoding="utf-8"))
            report_status = str(report_payload.get("status") or "").strip() or None
            swebench_payload = dict((report_payload.get("outputs") or {}).get("swebench") or {})
            prediction_artifact_path = _maybe_path(swebench_payload.get("prediction_artifact_path"))
            patch_artifact_path = _maybe_path(swebench_payload.get("patch_artifact_path"))
        except Exception as exc:
            error = str(exc) if error is None else f"{error}; {exc}"

    duration_seconds = time.perf_counter() - started
    return SweBenchRunRecord(
        instance_id=instance.instance_id,
        repo=str(instance.repo or ""),
        base_commit=str(instance.base_commit or ""),
        model=model,
        dataset_repo=dataset_repo,
        split=split,
        started_at=started_at,
        duration_seconds=duration_seconds,
        exit_code=int(proc.returncode),
        workspace_root=str(workspace_root),
        run_dir=str(run_dir),
        instance_file=str(instance_file),
        report_path=str(report_path),
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        predictions_path=str(predictions_path),
        prediction_artifact_path=prediction_artifact_path,
        patch_artifact_path=patch_artifact_path,
        report_status=report_status,
        command=[str(part) for part in command],
        error=error,
        timed_out=timed_out,
    )


def _maybe_path(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _print_record(record: SweBenchRunRecord, *, as_json: bool) -> None:
    payload = asdict(record)
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return
    print(f"instance: {record.instance_id}")
    print(f"repo: {record.repo}")
    print(f"base commit: {record.base_commit}")
    print(f"model: {record.model}")
    print(f"exit code: {record.exit_code}")
    if record.report_status:
        print(f"report status: {record.report_status}")
    print(f"run dir: {record.run_dir}")
    print(f"workspace: {record.workspace_root}")
    print(f"predictions: {record.predictions_path}")
    if record.prediction_artifact_path:
        print(f"prediction artifact: {record.prediction_artifact_path}")
    if record.patch_artifact_path:
        print(f"patch artifact: {record.patch_artifact_path}")
    print(f"duration: {record.duration_seconds:.2f}s")
    if record.error:
        print(f"error: {record.error}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.eval.swebench_runner",
        description="Minimal SWE-bench adapter for DAN Code.",
    )
    parser.add_argument("--instance-file", help="Local SWE-bench JSON/JSONL file.")
    parser.add_argument(
        "--dataset-repo",
        help="Hugging Face dataset repo, e.g. princeton-nlp/SWE-bench_Lite.",
    )
    parser.add_argument(
        "--split",
        help="Dataset split name, e.g. dev or test.",
    )
    parser.add_argument(
        "--instance-id",
        help="Benchmark instance id to select.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("DAN_LLM_MODEL") or os.environ.get("DAN_MODEL") or "kimi-k2.5",
        help="Model passed through to dan code.",
    )
    parser.add_argument(
        "--thinking-mode",
        default="disabled",
        choices=["auto", "enabled", "disabled"],
        help="Provider thinking mode for the dan code run.",
    )
    parser.add_argument(
        "--completion-timeout-seconds",
        type=float,
        default=90.0,
        help="Bound per-completion provider wait for the dan code run.",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=None,
        help="Optional max tool rounds passed through to dan code.",
    )
    parser.add_argument(
        "--run-timeout-seconds",
        type=float,
        default=None,
        help="Optional wall-clock timeout for the whole dan code subprocess.",
    )
    parser.add_argument(
        "--results-root",
        default=str(RESULTS_DIR),
        help="Directory for benchmark run artifacts.",
    )
    parser.add_argument(
        "--repo-cache-dir",
        default=str(DEFAULT_REPO_CACHE_DIR),
        help="Directory for cached benchmark repository clones.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the final run record as JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        instance = load_swebench_instance(
            instance_file=Path(args.instance_file).resolve() if args.instance_file else None,
            dataset_repo=args.dataset_repo,
            split=args.split,
            instance_id=args.instance_id,
        )
        record = run_swebench_instance(
            instance=instance,
            dataset_repo=args.dataset_repo,
            split=args.split,
            results_root=Path(args.results_root).resolve(),
            repo_cache_dir=Path(args.repo_cache_dir).resolve(),
            model=str(args.model),
            thinking_mode=str(args.thinking_mode),
            completion_timeout_seconds=(
                float(args.completion_timeout_seconds)
                if args.completion_timeout_seconds is not None
                else None
            ),
            max_tool_rounds=args.max_tool_rounds,
            run_timeout_seconds=(
                float(args.run_timeout_seconds)
                if args.run_timeout_seconds is not None
                else None
            ),
        )
    except Exception as exc:
        parser.error(str(exc))
    _print_record(record, as_json=bool(args.json))
    return 0 if record.exit_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
