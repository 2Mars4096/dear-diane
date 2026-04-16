"""dan-organism — local CLI for bounded worker-organism demos."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Sequence

from dan.cli import load_env, normalize_workspace_root, resolve_config
from dan.providers.factory import build_provider_registry
from dan.server.runtime_config import build_engine_config_from_env
from dan.worker.organisms.reference_demo import (
    DEFAULT_LIVE_ORGANISM_TOOL_IDS,
    DEFAULT_REFERENCE_ACCEPTANCE_CRITERIA,
    DEFAULT_REFERENCE_EVIDENCE_SUMMARIES,
    DEFAULT_REFERENCE_HARD_CONSTRAINTS,
    MAX_DEEP_RESEARCH_READERS,
    DEFAULT_REFERENCE_OBJECTIVE,
    DEFAULT_REFERENCE_SOFT_CONSTRAINTS,
    DEFAULT_REFERENCE_VALIDATION_COMMANDS,
    DeepResearchOrganDemoReport,
    available_local_organism_tools,
    build_reference_organism_demo_task,
    run_deep_research_organ_demo,
    run_deep_research_organ_live,
    run_reference_organism_demo,
    run_reference_organism_live,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dan-organism",
        description=(
            "Run the bounded project-execution reference organism locally. "
            "Use the default deterministic demo provider or opt into live local "
            "coding with --live. This does not require dan-serve."
        ),
    )
    parser.add_argument(
        "objective",
        nargs="?",
        default=DEFAULT_REFERENCE_OBJECTIVE,
        help="Bounded coding objective for the demo run.",
    )
    parser.add_argument(
        "--task-id",
        default="bounded-project-execution",
        help="Task identifier recorded in the organism trace.",
    )
    parser.add_argument(
        "--delivery-target",
        default="repo-change brief",
        help="Delivery target label attached to the demo task.",
    )
    parser.add_argument(
        "--acceptance-criterion",
        dest="acceptance_criteria",
        action="append",
        default=[],
        help="Append one acceptance criterion. Defaults to the reference demo basket.",
    )
    parser.add_argument(
        "--validation-command",
        dest="validation_commands",
        action="append",
        default=[],
        help="Append one focused validation command.",
    )
    parser.add_argument(
        "--hard-constraint",
        dest="hard_constraints",
        action="append",
        default=[],
        help="Append one hard constraint.",
    )
    parser.add_argument(
        "--soft-constraint",
        dest="soft_constraints",
        action="append",
        default=[],
        help="Append one soft constraint.",
    )
    parser.add_argument(
        "--evidence-summary",
        dest="evidence_summaries",
        action="append",
        default=[],
        help="Append one evidence summary note for the demo task.",
    )
    parser.add_argument(
        "--workdir",
        default=".tmp/organism-demo",
        help="Directory used for generated evidence note files.",
    )
    parser.add_argument(
        "--organism-id",
        default="reference-project-execution",
        help="Organism identifier to stamp into the trace.",
    )
    parser.add_argument(
        "--model",
        default="stub-model",
        help="Model label. Demo mode defaults to stub-model; live mode falls back to env/default runtime config when left at stub-model.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run the organism against a live provider plus local tools instead of the deterministic demo provider.",
    )
    parser.add_argument(
        "--research-only",
        action="store_true",
        help="Run only the bounded deep-research organ instead of the full reference organism.",
    )
    parser.add_argument(
        "--research-readers",
        type=int,
        help=(
            f"Override the deep-research reader count (1-{MAX_DEEP_RESEARCH_READERS}). "
            "By default the connector sizes reader fan-out from task breadth."
        ),
    )
    parser.add_argument(
        "--workspace",
        help="Workspace root for live local tool calls. Defaults to DAN_WORKSPACE_ROOT or the current directory.",
    )
    parser.add_argument(
        "--api-key",
        help="Optional default-provider API key override for live mode.",
    )
    parser.add_argument(
        "--base-url",
        help="Optional default-provider base URL override for live mode.",
    )
    parser.add_argument(
        "--tool",
        dest="tool_ids",
        action="append",
        default=[],
        help="Enable one local tool for live mode. Repeat to override the default coding basket.",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List the available local organism tools and exit.",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=8,
        help="Maximum live provider tool rounds per worker completion.",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=24,
        help="Maximum live provider tool calls per worker completion.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full report as JSON.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the JSON report.",
    )
    return parser


def _non_empty(values: list[str], defaults: list[str]) -> list[str]:
    cleaned = [value.strip() for value in values if str(value).strip()]
    return cleaned or list(defaults)


def _print_reference_report(report: dict[str, object]) -> None:
    print(f"Status: {report['status']}")
    print(f"Trace ID: {report['trace_id']}")
    if report.get("selected_attempt") is not None:
        print(f"Selected Attempt: {report['selected_attempt']}")
    print(
        "Validation: "
        f"{report.get('initial_score')} -> {report.get('final_score')} "
        f"({report.get('validation_attempts')} attempts)"
    )
    print(f"Improved Via Repair: {report.get('improved_via_repair')}")
    print(f"Handoffs: {report.get('handoff_count')} | Signals: {report.get('signal_count')}")
    candidates = ", ".join(report.get("build_candidate_ids") or []) or "(none)"
    print(f"Candidates: {candidates}")
    stages = " -> ".join(report.get("stage_sequence") or []) or "(none)"
    print(f"Stages: {stages}")
    print("\nFinal Output:")
    print(json.dumps(report.get("final_output") or {}, indent=2, ensure_ascii=False, sort_keys=True))


def _print_research_report(report: dict[str, object]) -> None:
    print(f"Status: {report['status']}")
    print(f"Trace ID: {report['trace_id']}")
    print(f"Handoffs: {report.get('handoff_count')} | Signals: {report.get('signal_count')}")
    if report.get("selected_reader_count") is not None:
        print(f"Readers: {report.get('selected_reader_count')}")
    print(f"Output Refs: {', '.join(report.get('output_ref_ids') or []) or '(none)'}")
    print("\nResearch Output:")
    print(json.dumps(report.get("final_output") or {}, indent=2, ensure_ascii=False, sort_keys=True))


def _effective_live_tool_ids(explicit: list[str]) -> list[str]:
    return _non_empty(explicit, DEFAULT_LIVE_ORGANISM_TOOL_IDS)


def _print_tool_catalog(*, as_json: bool) -> None:
    catalog = available_local_organism_tools()
    if as_json:
        print(
            json.dumps(
                [
                    {
                        "tool_id": tool_id,
                        "description": str(metadata.get("description") or ""),
                        "category": str(metadata.get("category") or ""),
                    }
                    for tool_id, metadata in sorted(catalog.items())
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    for tool_id, metadata in sorted(catalog.items()):
        category = str(metadata.get("category") or "other")
        description = str(metadata.get("description") or "").strip()
        print(f"{tool_id} [{category}]")
        if description:
            print(f"  {description}")


def _resolve_live_model(requested_model: str) -> str:
    text = str(requested_model or "").strip()
    if text and text != "stub-model":
        return text
    config = resolve_config()
    env_model = str(config.get("model") or "").strip()
    if env_model:
        return env_model
    engine_config = build_engine_config_from_env()
    fallback = str(engine_config.llm_default_model or "").strip()
    if fallback and fallback != "stub-model":
        return fallback
    raise ValueError("live mode requires --model or a configured DAN_MODEL/DAN_LLM_MODEL")


def _build_live_provider(model: str, *, api_key: str | None, base_url: str | None):
    engine_config = build_engine_config_from_env()
    if api_key:
        engine_config.llm_api_key = api_key
    if base_url:
        engine_config.llm_base_url = base_url
    registry = build_provider_registry(engine_config)
    return registry.resolve(model)


def main(argv: Sequence[str] | None = None) -> int:
    load_env()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.list_tools:
        _print_tool_catalog(as_json=bool(args.json))
        return 0
    if args.research_readers is not None and not (1 <= args.research_readers <= MAX_DEEP_RESEARCH_READERS):
        parser.error(
            f"--research-readers must be between 1 and {MAX_DEEP_RESEARCH_READERS}"
        )

    workdir = Path(args.workdir).expanduser()
    resolved_config = resolve_config(workspace=args.workspace)
    workspace_root = normalize_workspace_root(str(resolved_config["workspace"]))
    os.environ["DAN_WORKSPACE_ROOT"] = str(workspace_root)
    task = build_reference_organism_demo_task(
        workdir,
        task_id=str(args.task_id),
        objective=str(args.objective),
        acceptance_criteria=_non_empty(args.acceptance_criteria, DEFAULT_REFERENCE_ACCEPTANCE_CRITERIA),
        delivery_target=str(args.delivery_target),
        focused_validation_commands=_non_empty(args.validation_commands, DEFAULT_REFERENCE_VALIDATION_COMMANDS),
        hard_constraints=_non_empty(args.hard_constraints, DEFAULT_REFERENCE_HARD_CONSTRAINTS),
        soft_constraints=_non_empty(args.soft_constraints, DEFAULT_REFERENCE_SOFT_CONSTRAINTS),
        evidence_summaries=_non_empty(args.evidence_summaries, DEFAULT_REFERENCE_EVIDENCE_SUMMARIES),
    )
    if args.live:
        try:
            live_model = _resolve_live_model(str(args.model))
            provider = _build_live_provider(
                live_model,
                api_key=args.api_key,
                base_url=args.base_url,
            )
        except Exception as exc:
            parser.error(str(exc))
        if args.research_only:
            report = asyncio.run(
                run_deep_research_organ_live(
                    workdir,
                    llm_provider=provider,
                    task=task,
                    model=live_model,
                    organism_id=str(args.organism_id),
                    tool_ids=_effective_live_tool_ids(args.tool_ids),
                    workspace_root=workspace_root,
                    max_tool_rounds=args.max_tool_rounds,
                    max_tool_calls=args.max_tool_calls,
                    research_reader_count=args.research_readers,
                )
            )
        else:
            report = asyncio.run(
                run_reference_organism_live(
                    workdir,
                    llm_provider=provider,
                    task=task,
                    model=live_model,
                    organism_id=str(args.organism_id),
                    tool_ids=_effective_live_tool_ids(args.tool_ids),
                    workspace_root=workspace_root,
                    max_tool_rounds=args.max_tool_rounds,
                    max_tool_calls=args.max_tool_calls,
                    research_reader_count=args.research_readers,
                )
            )
    else:
        if args.research_only:
            report = asyncio.run(
                run_deep_research_organ_demo(
                    workdir,
                    task=task,
                    model=str(args.model),
                    organism_id=str(args.organism_id),
                    research_reader_count=args.research_readers,
                )
            )
        else:
            report = asyncio.run(
                run_reference_organism_demo(
                    workdir,
                    task=task,
                    model=str(args.model),
                    organism_id=str(args.organism_id),
                    research_reader_count=args.research_readers,
                )
            )
    payload = report.model_dump(mode="json")

    if args.output:
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        if isinstance(report, DeepResearchOrganDemoReport):
            _print_research_report(payload)
        else:
            _print_reference_report(payload)

    return 0 if report.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
