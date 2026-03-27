"""CLI entry point for the DAN workflow generation quality evaluation.

Usage:
    python -m tests.eval                          # Run all prompts
    python -m tests.eval --tier T1                # Run only tier T1
    python -m tests.eval --tier T1 --tier T2      # Run T1 and T2
    python -m tests.eval --small-battery          # Honest baseline rerun: T1/T2/T2R/T3/T5
    python -m tests.eval --pilot                  # Run pilot subset (~10 prompts)
    python -m tests.eval --complex                # Run complex battery (T4 + multi-turn m1/m2/m3)
    python -m tests.eval --benchmark-prep         # Frozen-input benchmark-prep candidates, LR2-first
    python -m tests.eval --list-batteries         # Print named battery membership and exit
    python -m tests.eval --smoke-workflows        # Run small live workflow smoke battery
    python -m tests.eval --prompts-file tests/eval/workflow_contract_comparison_prompts.json --workflow-contract compare
    python -m tests.eval --prompt "Build a chain" # Single ad-hoc prompt
    python -m tests.eval --report results/X.jsonl # Regenerate report from JSONL
    python -m tests.eval --execute                # Enable execution testing
    python -m tests.eval --lane agent             # agent | build | both
    python -m tests.eval --workflow-contract compare  # baseline vs treatment on the same live server
    python -m tests.eval --execution-path inline  # inline | codegen | auto (plan 32-7)

See docs/eval-run-guide.md for full run commands.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from tests.eval import PROMPTS_FILE, PromptFixture
from tests.eval.durability_checks import run_durability_suite
from tests.eval.metrics import EvalLogger
from tests.eval.report import ReportGenerator
from tests.eval.runner import (
    AVAILABLE_BATTERIES,
    EvalRunner,
    describe_prompt_batteries,
    load_prompts,
)

SMOKE_PROMPTS_FILE = Path(__file__).parent / "workflow_smoke_prompts.json"


def _selected_batteries(args: argparse.Namespace) -> list[str]:
    selected = list(args.battery or [])
    if getattr(args, "small_battery", False):
        selected.append("small")
    if getattr(args, "complex", False):
        selected.append("complex")
    if getattr(args, "execution_friendly", False):
        selected.append("execution-friendly")
    if getattr(args, "benchmark_prep", False):
        selected.append("benchmark-prep")

    deduped: list[str] = []
    for name in selected:
        if name not in deduped:
            deduped.append(name)
    return deduped


def _default_run_tag(args: argparse.Namespace) -> str | None:
    explicit = getattr(args, "run_tag", None)
    if explicit:
        return explicit

    parts: list[str] = []
    batteries = _selected_batteries(args)
    if batteries:
        parts.extend(name.replace("-", "_") for name in batteries)
    elif getattr(args, "smoke_workflows", False):
        parts.append("smoke_workflows")
    elif getattr(args, "pilot", False):
        parts.append("pilot")
    elif getattr(args, "tier", None):
        parts.append(
            "tier_" + "_".join(sorted(str(t).lower() for t in args.tier)),
        )
    elif getattr(args, "tag", None):
        parts.append(
            "tags_" + "_".join(
                sorted(str(tag).strip().lower().replace("-", "_") for tag in args.tag),
            ),
        )
    elif getattr(args, "prompt", None):
        parts.append("adhoc")

    if (getattr(args, "lr2_first", False) or getattr(args, "benchmark_prep", False)) and "lr2_first" not in parts:
        parts.append("lr2_first")

    return "_".join(parts) if parts else None


def _print_battery_list(prompts_path: Path | None) -> None:
    catalog = describe_prompt_batteries(prompts_path)
    source = prompts_path or PROMPTS_FILE
    print(f"Prompt batteries from {source}:")
    for battery in AVAILABLE_BATTERIES:
        info = catalog.get(battery, {})
        ids = list(info.get("ids", []))
        print(f"  {battery}: {info.get('count', 0)} prompts")
        if battery == "benchmark-prep":
            lr2_ids = list(info.get("lr2_first_ids", []))
            remaining_ids = list(info.get("remaining_ids", []))
            if lr2_ids:
                print(f"    lr2-first: {', '.join(lr2_ids)}")
            if remaining_ids:
                print(f"    then: {', '.join(remaining_ids)}")
        elif ids:
            print(f"    {', '.join(ids)}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.eval",
        description="DAN Workflow Generation Quality Evaluation",
    )
    parser.add_argument(
        "--tier",
        action="append",
        metavar="TIER",
        help="Tier(s) to run (e.g. T1, T2). Repeatable.",
    )
    parser.add_argument(
        "--pilot",
        action="store_true",
        help="Run the pilot subset (~10 prompts).",
    )
    parser.add_argument(
        "--complex",
        action="store_true",
        help="Run complex battery only (T4 + multi-turn m1/m2/m3).",
    )
    parser.add_argument(
        "--small-battery",
        action="store_true",
        help="Run the honest-baseline small battery (T1/T2/T2R/T3/T5).",
    )
    parser.add_argument(
        "--execution-friendly",
        action="store_true",
        help="Run only prompts tagged as execution-friendly candidates.",
    )
    parser.add_argument(
        "--benchmark-prep",
        action="store_true",
        help="Run frozen-input benchmark-prep candidates with LR2-first ordering.",
    )
    parser.add_argument(
        "--battery",
        action="append",
        choices=AVAILABLE_BATTERIES,
        metavar="NAME",
        help="Named battery/subset to run. Repeatable.",
    )
    parser.add_argument(
        "--lr2-first",
        action="store_true",
        help="Bias selected prompts so fixtures tagged lr2_first run first.",
    )
    parser.add_argument(
        "--list-batteries",
        action="store_true",
        help="Print named battery membership from the prompts file and exit.",
    )
    parser.add_argument(
        "--smoke-workflows",
        action="store_true",
        help="Run the small workflow smoke battery intended for provider-backed live checks.",
    )
    parser.add_argument(
        "--prompts-file",
        type=str,
        metavar="PATH",
        help="Load prompts from a specific JSON file instead of tests/eval/prompts.json.",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        metavar="TEXT",
        help="Run a single ad-hoc prompt instead of the fixture battery.",
    )
    parser.add_argument(
        "--report",
        type=str,
        metavar="PATH",
        help="Regenerate report from an existing JSONL results file.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Enable execution testing for validated graphs.",
    )
    parser.add_argument(
        "--model",
        type=str,
        metavar="MODEL",
        help="Model identifier (stored in records; not yet wired to server).",
    )
    parser.add_argument(
        "--lane",
        type=str,
        choices=["agent", "build", "both"],
        default="both",
        help="Evaluation lane (default: both).",
    )
    parser.add_argument(
        "--workflow-contract",
        type=str,
        choices=["enabled", "disabled", "compare", "both"],
        default="enabled",
        help="Request-scoped workflow-generation contract variant (default: enabled).",
    )
    parser.add_argument(
        "--execution-path",
        type=str,
        choices=["inline", "codegen", "auto"],
        default="auto",
        help="Execution path for eval: inline (direct build), codegen (sandbox), auto (default). Sets DAN_EVAL_EXECUTION_PATH.",
    )
    parser.add_argument(
        "--keep-graphs",
        action="store_true",
        help="Don't delete created graphs after evaluation.",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="http://localhost:8000",
        help="DAN server URL (default: http://localhost:8000).",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        metavar="PATH",
        help="Path to telemetry DB (default: ~/.dan/telemetry.db).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        metavar="N",
        help="Delay in seconds between prompts (default: 2).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        metavar="N",
        help="Repeat battery N times for flakiness measurement (default: 1).",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Skip saving the JSON report file.",
    )
    parser.add_argument(
        "--durability",
        action="store_true",
        help="Run durability smoke checks (D1-D4) on first valid graph from battery (33-4).",
    )
    parser.add_argument(
        "--no-store-graphs",
        action="store_true",
        help="Skip storing generated graph JSON alongside results (33-2 task 5-3).",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Enable LLM-as-judge semantic scoring for generated graphs (advisory, does not affect pass/fail).",
    )
    parser.add_argument(
        "--tag",
        action="append",
        metavar="TAG",
        help="Filter prompts by tag (e.g. --tag practical). Repeatable.",
    )
    parser.add_argument(
        "--run-tag",
        type=str,
        metavar="TAG",
        help="Optional suffix tag for the JSONL/report output filenames.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    prompts_path = Path(args.prompts_file) if args.prompts_file else None
    if args.smoke_workflows:
        prompts_path = SMOKE_PROMPTS_FILE

    if args.list_batteries:
        _print_battery_list(prompts_path)
        return

    if args.report:
        _report_only(args, prompts_path=prompts_path)
        return

    if args.prompt:
        prompts = [PromptFixture(id="adhoc", tier="adhoc", prompt=args.prompt)]
    else:
        selected_batteries = _selected_batteries(args)
        prompts = load_prompts(
            path=prompts_path,
            tier=args.tier,
            pilot_only=args.pilot,
            complex_only=False,
            tags=args.tag,
            batteries=selected_batteries,
            prefer_lr2=bool(args.lr2_first or args.benchmark_prep),
        )

    if not prompts:
        print("No prompts matched the given filters.", file=sys.stderr)
        sys.exit(1)

    # Set execution path for eval (plan 32-7 task 6-4)
    os.environ["DAN_EVAL_EXECUTION_PATH"] = args.execution_path

    lanes: list[str] | None = None
    if args.lane and args.lane != "both":
        lanes = [args.lane]

    asyncio.run(_run(args, prompts, lanes, prompts_path=prompts_path))


async def _run_durability(
    args: argparse.Namespace,
    records: list,
    runner: EvalRunner,
) -> None:
    """Run D1-D4 durability checks on first valid graph (33-4 task 6)."""
    valid = next(
        (r for r in records if r.graph_created and r.graph_id and r.graph_id.strip()
         and r.validation and r.validation.passed),
        None,
    )
    if not valid:
        print("No valid graph for durability checks.", file=sys.stderr)
        return
    print(f"\n--- Durability (33-4) on {valid.graph_id} ---")
    try:
        result = await run_durability_suite(
            runner._client,
            valid.graph_id,
            follow_up="Add a review step after the summarize node.",
        )
        for check, data in result.items():
            print(f"  {check}: {data}")
    except Exception as exc:
        print(f"Durability error: {exc}", file=sys.stderr)


def _report_only(
    args: argparse.Namespace,
    *,
    prompts_path: Path | None = None,
) -> None:
    path = Path(args.report)
    if not path.exists():
        print(f"Results file not found: {path}", file=sys.stderr)
        sys.exit(1)

    records = EvalLogger.load_records(path)
    if not records:
        print("No records found in the results file.", file=sys.stderr)
        sys.exit(1)
    graphs_dir = path.parent / f"{path.stem}_graphs"
    rg = ReportGenerator(
        records,
        graphs_dir=graphs_dir if graphs_dir.exists() else None,
        prompts_path=prompts_path,
    )
    rg.print_report()
    if not args.no_save:
        out = path.with_suffix(".report.json")
        rg.save_json(out)
        print(f"\nReport saved to {out}")


async def _run(
    args: argparse.Namespace,
    prompts: list[PromptFixture],
    lanes: list[str] | None,
    *,
    prompts_path: Path | None = None,
) -> None:
    logger = EvalLogger(
        run_tag=_default_run_tag(args),
        store_graphs=not getattr(args, "no_store_graphs", False),
    )
    runner = EvalRunner(
        base_url=args.base_url,
        db_path=args.db_path,
        execute=args.execute,
        keep_graphs=args.keep_graphs,
        delay=args.delay,
        execution_path=getattr(args, "execution_path", "auto"),
        judge=getattr(args, "judge", False),
        workflow_contract=getattr(args, "workflow_contract", "enabled"),
    )
    all_records: list = []
    for run_idx in range(args.runs):
        if args.runs > 1:
            print(f"\n--- Run {run_idx + 1}/{args.runs} ---")
        records = await runner.run_battery(prompts, lanes=lanes, logger=logger)
        all_records.extend(records)
        if run_idx < args.runs - 1:
            await runner.cleanup(close_client=False)

    if args.durability and all_records:
        await _run_durability(args, all_records, runner)

    await runner.cleanup()

    rg = ReportGenerator(all_records, runs=args.runs, prompts_path=prompts_path)
    rg.print_report()
    if args.runs > 1:
        rg.print_flakiness_report(all_records, args.runs)

    if not args.no_save:
        out = logger.output_path.with_suffix(".report.json")
        rg.save_json(out)
        print(f"\nReport saved to {out}")


if __name__ == "__main__":
    main()
