"""Resumable SWE-bench batch adapter for DAN Code.

The single-instance adapter owns checkout/run/export. This wrapper only loads a
set of instances, skips already completed predictions, and maintains one
combined prediction JSONL for the official SWE-bench harness.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any

from tests.eval import RESULTS_DIR
from tests.eval.swebench_runner import (
    DEFAULT_REPO_CACHE_DIR,
    SweBenchRunRecord,
    load_swebench_instances,
    run_swebench_instance,
)


def _timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _read_completed_prediction_ids(path: Path) -> set[str]:
    completed: set[str] = set()
    if not path.exists():
        return completed
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        instance_id = str(payload.get("instance_id") or "").strip()
        patch = str(payload.get("model_patch") or "")
        if instance_id and patch:
            completed.add(instance_id)
    return completed


def _read_attempted_instance_ids(path: Path) -> set[str]:
    attempted: set[str] = set()
    if not path.exists():
        return attempted
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        instance_id = str(payload.get("instance_id") or "").strip()
        if instance_id:
            attempted.add(instance_id)
    return attempted


def _normalize_optional_timeout(value: float | None) -> float | None:
    if value is None:
        return None
    normalized = float(value)
    if normalized <= 0:
        return None
    return normalized


def _append_prediction_once(
    *,
    source_path: Path,
    combined_path: Path,
    completed_ids: set[str],
) -> str | None:
    if not source_path.exists():
        return None
    for line in source_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        instance_id = str(payload.get("instance_id") or "").strip()
        patch = str(payload.get("model_patch") or "")
        if not instance_id or not patch or instance_id in completed_ids:
            continue
        combined_path.parent.mkdir(parents=True, exist_ok=True)
        with combined_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        completed_ids.add(instance_id)
        return instance_id
    return None


def _append_predictions(
    *,
    source_path: Path,
    combined_path: Path,
    completed_ids: set[str],
) -> list[str]:
    appended: list[str] = []
    if not source_path.exists():
        return appended
    for line in source_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        instance_id = str(payload.get("instance_id") or "").strip()
        patch = str(payload.get("model_patch") or "")
        if not instance_id or not patch or instance_id in completed_ids:
            continue
        combined_path.parent.mkdir(parents=True, exist_ok=True)
        with combined_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        completed_ids.add(instance_id)
        appended.append(instance_id)
    return appended


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _copy_existing_prediction(
    *,
    source_path: Path,
    combined_path: Path,
    completed_ids: set[str],
) -> list[str]:
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    return _append_predictions(
        source_path=source_path,
        combined_path=combined_path,
        completed_ids=completed_ids,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.eval.swebench_batch_runner",
        description="Run a resumable SWE-bench batch through DAN Code.",
    )
    parser.add_argument("--instance-file", help="Local SWE-bench JSON/JSONL file.")
    parser.add_argument(
        "--dataset-repo",
        default="princeton-nlp/SWE-bench_Lite",
        help="Hugging Face dataset repo.",
    )
    parser.add_argument("--split", default="dev", help="Dataset split name.")
    parser.add_argument(
        "--instance-id",
        action="append",
        default=[],
        help="Optional instance id filter. May be repeated.",
    )
    parser.add_argument("--limit", type=int, help="Optional max instances to run.")
    parser.add_argument(
        "--batch-root",
        default="",
        help="Existing or new batch directory. Defaults to tests/eval/results/swebench_lite_full_<timestamp>.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("DAN_LLM_MODEL") or os.environ.get("DAN_MODEL") or "kimi-k2.6",
        help="Model passed through to dan code.",
    )
    parser.add_argument(
        "--thinking-mode",
        default="enabled",
        choices=["auto", "enabled", "disabled"],
        help="Provider thinking mode for the dan code run.",
    )
    parser.add_argument(
        "--completion-timeout-seconds",
        type=float,
        default=90.0,
        help="Bound per-completion provider wait for each dan code run.",
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
        help=(
            "Optional wall-clock timeout for each dan code subprocess. "
            "Unset or <=0 disables the outer per-instance cap."
        ),
    )
    parser.add_argument(
        "--repo-cache-dir",
        default=str(DEFAULT_REPO_CACHE_DIR),
        help="Directory for cached benchmark repository clones.",
    )
    parser.add_argument(
        "--seed-prediction",
        action="append",
        default=[],
        help="Existing prediction JSONL to copy into the combined batch predictions before running.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop the batch after the first failed instance run.",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry instance ids that already have a prior record without a combined prediction row.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    batch_root = (
        Path(args.batch_root).resolve()
        if args.batch_root
        else (RESULTS_DIR / f"swebench_lite_full_{_timestamp_slug()}").resolve()
    )
    batch_runs_root = batch_root / "runs"
    combined_predictions_path = batch_root / "predictions.jsonl"
    manifest_path = batch_root / "manifest.json"
    records_path = batch_root / "records.jsonl"

    instance_ids = set(args.instance_id) if args.instance_id else None
    instances = load_swebench_instances(
        instance_file=Path(args.instance_file).resolve() if args.instance_file else None,
        dataset_repo=args.dataset_repo if not args.instance_file else None,
        split=args.split if not args.instance_file else None,
        instance_ids=instance_ids,
    )
    if args.limit is not None:
        instances = instances[: max(0, int(args.limit))]

    completed_ids = _read_completed_prediction_ids(combined_predictions_path)
    attempted_ids = _read_attempted_instance_ids(records_path)
    copied_seed_ids: list[str] = []
    for seed in args.seed_prediction:
        copied_seed_ids.extend(
            _copy_existing_prediction(
                source_path=Path(seed).resolve(),
                combined_path=combined_predictions_path,
                completed_ids=completed_ids,
            )
        )

    manifest = {
        "batch_root": str(batch_root),
        "combined_predictions_path": str(combined_predictions_path),
        "dataset_repo": args.dataset_repo if not args.instance_file else None,
        "split": args.split if not args.instance_file else None,
        "instance_file": str(Path(args.instance_file).resolve()) if args.instance_file else None,
        "model": str(args.model),
        "created_or_updated_at": datetime.now(UTC).isoformat(),
        "requested_instances": len(instances),
        "completed_prediction_count": len(completed_ids),
        "attempted_instance_count": len(attempted_ids),
        "seeded_prediction_ids": copied_seed_ids,
        "run_timeout_seconds": _normalize_optional_timeout(args.run_timeout_seconds),
    }
    _write_json(manifest_path, manifest)

    failures = 0
    for index, instance in enumerate(instances, start=1):
        if instance.instance_id in completed_ids:
            print(f"[skip] {index}/{len(instances)} {instance.instance_id}", flush=True)
            continue
        if not args.retry_failed and instance.instance_id in attempted_ids:
            print(f"[skip-failed] {index}/{len(instances)} {instance.instance_id}", flush=True)
            continue
        print(f"[run] {index}/{len(instances)} {instance.instance_id}", flush=True)
        record = run_swebench_instance(
            instance=instance,
            dataset_repo=args.dataset_repo if not args.instance_file else None,
            split=args.split if not args.instance_file else None,
            results_root=batch_runs_root,
            repo_cache_dir=Path(args.repo_cache_dir).resolve(),
            model=str(args.model),
            thinking_mode=str(args.thinking_mode),
            completion_timeout_seconds=(
                float(args.completion_timeout_seconds)
                if args.completion_timeout_seconds is not None
                else None
            ),
            max_tool_rounds=args.max_tool_rounds,
            run_timeout_seconds=_normalize_optional_timeout(args.run_timeout_seconds),
        )
        appended_id = _append_prediction_once(
            source_path=Path(record.predictions_path),
            combined_path=combined_predictions_path,
            completed_ids=completed_ids,
        )
        record_payload = asdict(record)
        record_payload["combined_prediction_appended"] = appended_id is not None
        _append_jsonl(records_path, record_payload)
        attempted_ids.add(instance.instance_id)
        if record.exit_code != 0 or appended_id is None:
            failures += 1
            print(f"[fail] {instance.instance_id} exit={record.exit_code}", flush=True)
            if args.stop_on_error:
                break
        else:
            print(f"[ok] {instance.instance_id}", flush=True)
        manifest["completed_prediction_count"] = len(completed_ids)
        manifest["attempted_instance_count"] = len(attempted_ids)
        manifest["created_or_updated_at"] = datetime.now(UTC).isoformat()
        _write_json(manifest_path, manifest)

    final_payload = dict(manifest)
    final_payload["failures"] = failures
    final_payload["records_path"] = str(records_path)
    final_payload["remaining_instances"] = len(
        [instance for instance in instances if instance.instance_id not in completed_ids]
    )
    _write_json(batch_root / "summary.json", final_payload)
    print(json.dumps(final_payload, indent=2, ensure_ascii=False, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
