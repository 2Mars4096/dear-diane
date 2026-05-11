"""dan-organism-log — import and summarize ``organism_log_v1`` traces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from dan.worker.organism_log_analysis import analyze_organism_log_rows
from dan.worker.prompt_pressure_analysis import analyze_prompt_pressure_rows
from dan.worker.scheduler import analyze_scheduler_replay_rows
from dan.worker.organism_log_adapters import (
    OrganismLogImportConfig,
    SUPPORTED_ORGANISM_LOG_ADAPTERS,
    import_organism_log,
    normalize_import_rows,
    parse_field_aliases,
    read_import_rows,
    summarize_import_rows,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dan-organism-log",
        description=(
            "Normalize arbitrary agent logs into organism_log_v1 and summarize "
            "durations/blockers from the shared schema."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser(
        "import",
        help="Normalize one JSON/JSONL log into organism_log_v1 JSONL.",
    )
    _add_shared_args(import_parser)
    import_parser.add_argument("source", help="Source JSON or JSONL log file.")
    import_parser.add_argument(
        "--output",
        "-o",
        help="Destination JSONL path. Defaults to <source>.organism-log.jsonl.",
    )
    import_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the import result as JSON.",
    )
    import_parser.add_argument(
        "--limit",
        type=int,
        default=5,
        metavar="N",
        help="Max slowest/blocking spans to report (default: 5).",
    )
    import_parser.set_defaults(func=_cmd_import)

    summarize_parser = subparsers.add_parser(
        "summarize",
        help="Summarize a raw or normalized log without writing output.",
    )
    _add_shared_args(summarize_parser)
    summarize_parser.add_argument("source", help="Source JSON or JSONL log file.")
    summarize_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the summary as JSON.",
    )
    summarize_parser.add_argument(
        "--limit",
        type=int,
        default=5,
        metavar="N",
        help="Max slowest/blocking spans to report (default: 5).",
    )
    summarize_parser.set_defaults(func=_cmd_summarize)

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Build timeline + dependency analysis for a raw or normalized log.",
    )
    _add_shared_args(analyze_parser)
    analyze_parser.add_argument("source", help="Source JSON or JSONL log file.")
    analyze_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the analysis payload as JSON.",
    )
    analyze_parser.add_argument(
        "--limit",
        type=int,
        default=5,
        metavar="N",
        help="Max slowest/blocking spans to print in plain text (default: 5).",
    )
    analyze_parser.set_defaults(func=_cmd_analyze)

    scheduler_parser = subparsers.add_parser(
        "scheduler-replay",
        help="Replay scheduler diagnostics over a raw or normalized log.",
    )
    _add_shared_args(scheduler_parser)
    scheduler_parser.add_argument("source", help="Source JSON or JSONL log file.")
    scheduler_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the scheduler replay payload as JSON.",
    )
    scheduler_parser.add_argument(
        "--limit",
        type=int,
        default=5,
        metavar="N",
        help="Max bottlenecks and missed-parallelism items to print (default: 5).",
    )
    scheduler_parser.add_argument(
        "--capacity",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Optional scheduler capacity hint. Defaults to observed max_parallel_spans "
            "from the trace."
        ),
    )
    scheduler_parser.set_defaults(func=_cmd_scheduler_replay)

    prompt_pressure_parser = subparsers.add_parser(
        "prompt-pressure",
        help=(
            "Calibrate provider prompt-pressure budgets from Super DAN or "
            "organism-log JSONL traces."
        ),
    )
    prompt_pressure_parser.add_argument(
        "source",
        nargs="+",
        help=(
            "Trace file(s) or directory/directories. Directories are scanned for "
            "events.jsonl and *.events.jsonl files."
        ),
    )
    prompt_pressure_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the prompt-pressure calibration payload as JSON.",
    )
    prompt_pressure_parser.add_argument(
        "--limit",
        type=int,
        default=5,
        metavar="N",
        help="Max high-pressure model calls and retries to print (default: 5).",
    )
    prompt_pressure_parser.set_defaults(func=_cmd_prompt_pressure)
    return parser


def _add_shared_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--adapter",
        choices=SUPPORTED_ORGANISM_LOG_ADAPTERS,
        default="auto",
        help="Input adapter to use (default: auto).",
    )
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="CANONICAL=SOURCE",
        help=(
            "Override field detection for the generic adapter, for example "
            "--field timestamp=ts --field event=type --field span_id=call_id."
        ),
    )
    parser.add_argument("--product", default="", help="Override product on imported rows.")
    parser.add_argument(
        "--stream-kind",
        default="",
        help="Override stream kind on imported rows (run, bounded_run, control_plane).",
    )
    parser.add_argument("--session-id", default="", help="Override session_id.")
    parser.add_argument("--turn-id", default="", help="Override turn_id.")
    parser.add_argument("--task-id", default="", help="Override task_id.")
    parser.add_argument("--trace-id", default="", help="Override trace_id.")
    parser.add_argument("--organism-id", default="", help="Override organism_id.")
    parser.add_argument("--organ-id", default="", help="Override organ_id.")


def _build_import_config(args: argparse.Namespace) -> OrganismLogImportConfig:
    return OrganismLogImportConfig(
        adapter=args.adapter,
        product=str(args.product or "").strip(),
        stream_kind=str(args.stream_kind or "").strip(),
        session_id=str(args.session_id or "").strip(),
        turn_id=str(args.turn_id or "").strip(),
        task_id=str(args.task_id or "").strip(),
        trace_id=str(args.trace_id or "").strip(),
        organism_id=str(args.organism_id or "").strip(),
        organ_id=str(args.organ_id or "").strip(),
        field_aliases=parse_field_aliases(args.field),
    )


def _default_output_path(source: str | Path) -> Path:
    resolved = Path(source).expanduser().resolve()
    suffix = resolved.suffix
    if suffix:
        return resolved.with_name(f"{resolved.stem}.organism-log.jsonl")
    return resolved.with_name(f"{resolved.name}.organism-log.jsonl")


def _cmd_import(args: argparse.Namespace) -> int:
    config = _build_import_config(args)
    payload = import_organism_log(
        args.source,
        output_path=args.output or _default_output_path(args.source),
        config=config,
        limit=max(1, int(args.limit)),
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(payload, include_output=True)
    return 0


def _cmd_summarize(args: argparse.Namespace) -> int:
    config = _build_import_config(args)
    rows = read_import_rows(args.source)
    payload = summarize_import_rows(rows, config=config, limit=max(1, int(args.limit)))
    payload["source_path"] = str(Path(args.source).expanduser().resolve())
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(payload, include_output=False)
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    config = _build_import_config(args)
    source_rows = read_import_rows(args.source)
    adapter, normalized_rows = normalize_import_rows(source_rows, config=config)
    analysis = analyze_organism_log_rows(normalized_rows).model_dump(mode="json")
    analysis.update(
        {
            "adapter": adapter,
            "source_path": str(Path(args.source).expanduser().resolve()),
            "source_row_count": len(source_rows),
            "normalized_row_count": len(normalized_rows),
        }
    )
    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_analysis(analysis, limit=max(1, int(args.limit)))
    return 0


def _cmd_scheduler_replay(args: argparse.Namespace) -> int:
    config = _build_import_config(args)
    source_rows = read_import_rows(args.source)
    adapter, normalized_rows = normalize_import_rows(source_rows, config=config)
    analysis = analyze_scheduler_replay_rows(
        normalized_rows,
        capacity_hint=int(args.capacity) if int(args.capacity or 0) > 0 else None,
        limit=max(1, int(args.limit)),
    ).model_dump(mode="json")
    analysis.update(
        {
            "adapter": adapter,
            "source_path": str(Path(args.source).expanduser().resolve()),
            "source_row_count": len(source_rows),
            "normalized_row_count": len(normalized_rows),
        }
    )
    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_scheduler_replay(analysis, limit=max(1, int(args.limit)))
    return 0


def _cmd_prompt_pressure(args: argparse.Namespace) -> int:
    source_paths = _expand_prompt_pressure_sources(args.source)
    rows: list[dict[str, object]] = []
    for source_path in source_paths:
        for row in read_import_rows(source_path):
            tagged = dict(row)
            tagged["_source_path"] = str(source_path)
            rows.append(tagged)
    analysis = analyze_prompt_pressure_rows(
        rows,
        limit=max(1, int(args.limit)),
        source_file_count=len(source_paths),
    ).model_dump(mode="json")
    analysis["source_paths"] = [str(path) for path in source_paths]
    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_prompt_pressure(analysis, limit=max(1, int(args.limit)))
    return 0


def _expand_prompt_pressure_sources(raw_sources: Sequence[str]) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for raw_source in raw_sources:
        source = Path(raw_source).expanduser().resolve()
        candidates: list[Path]
        if source.is_dir():
            candidates = sorted(
                {
                    *source.rglob("events.jsonl"),
                    *source.rglob("*.events.jsonl"),
                    *source.glob("*.jsonl"),
                }
            )
        else:
            candidates = [source]
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            paths.append(candidate)
    return paths


def _print_summary(payload: dict[str, object], *, include_output: bool) -> None:
    adapter = str(payload.get("adapter") or "auto")
    source_path = str(payload.get("source_path") or "")
    print(f"Adapter: {adapter}")
    if source_path:
        print(f"Source: {source_path}")
    if include_output:
        output_path = str(payload.get("output_path") or "")
        if output_path:
            print(f"Output: {output_path}")
    print(
        "Rows: "
        f"{int(payload.get('source_row_count') or 0)} source -> "
        f"{int(payload.get('normalized_row_count') or 0)} normalized"
    )
    print(
        "Projection: "
        f"{int(payload.get('event_count') or 0)} events, "
        f"{int(payload.get('span_count') or 0)} spans"
    )

    slowest = list(payload.get("slowest_spans") or [])
    if slowest:
        print("Slowest:")
        for item in slowest:
            row = dict(item)
            summary = str(row.get("summary") or row.get("span_id") or "").strip()
            duration_ms = int(row.get("duration_ms") or 0)
            family = str(row.get("event_family") or "span").strip()
            suffix = f"  {summary}" if summary else ""
            print(f"  {duration_ms:>6} ms  {family}{suffix}")

    blocking = list(payload.get("blocking_spans") or [])
    if blocking:
        print("Blocking:")
        for item in blocking:
            row = dict(item)
            family = str(row.get("event_family") or "span").strip()
            status = str(row.get("status") or "").strip() or "blocked"
            summary = str(row.get("summary") or row.get("span_id") or "").strip()
            wait_reason = str(row.get("wait_reason") or "").strip()
            parts = [family, status]
            if summary:
                parts.append(summary)
            if wait_reason:
                parts.append(f"reason={wait_reason}")
            print(f"  {' | '.join(parts)}")


def _print_analysis(payload: dict[str, object], *, limit: int) -> None:
    adapter = str(payload.get("adapter") or "auto")
    source_path = str(payload.get("source_path") or "")
    timeline = dict(payload.get("timeline") or {})
    graph = dict(payload.get("graph") or {})
    spans = list(timeline.get("spans") or [])
    lanes = list(timeline.get("lanes") or [])
    blocker_chains = list(graph.get("blocker_chains") or [])
    critical_path_span_ids = list(graph.get("critical_path_span_ids") or [])
    span_index = {str(span.get("span_id") or ""): dict(span) for span in spans}

    print(f"Adapter: {adapter}")
    if source_path:
        print(f"Source: {source_path}")
    print(
        "Rows: "
        f"{int(payload.get('source_row_count') or 0)} source -> "
        f"{int(payload.get('normalized_row_count') or 0)} normalized"
    )
    print(
        "Timeline: "
        f"{int(payload.get('span_count') or 0)} spans across {len(lanes)} lanes; "
        f"window={int(timeline.get('duration_ms') or 0)} ms; "
        f"max_parallel={int(timeline.get('max_parallel_spans') or 0)}"
    )

    if critical_path_span_ids:
        print(
            "Critical path: "
            f"{int(graph.get('critical_path_duration_ms') or 0)} ms over "
            f"{len(critical_path_span_ids)} spans"
        )
        for span_id in critical_path_span_ids[:limit]:
            span = span_index.get(str(span_id), {})
            label = str(span.get("label") or span_id).strip()
            duration_ms = int(span.get("exclusive_duration_ms") or span.get("duration_ms") or 0)
            lane_id = str(span.get("lane_id") or "").strip()
            lane_part = f" [{lane_id}]" if lane_id else ""
            print(f"  {duration_ms:>6} ms{lane_part}  {label}")

    top_spans = sorted(
        [dict(span) for span in spans],
        key=lambda span: (
            -(int(span.get("exclusive_duration_ms") or span.get("duration_ms") or 0)),
            -(int(span.get("duration_ms") or 0)),
        ),
    )[:limit]
    if top_spans:
        print("Top spans:")
        for span in top_spans:
            label = str(span.get("label") or span.get("span_id") or "").strip()
            exclusive_ms = int(span.get("exclusive_duration_ms") or span.get("duration_ms") or 0)
            total_ms = int(span.get("duration_ms") or 0)
            status = str(span.get("status") or "").strip()
            suffix = f" status={status}" if status else ""
            print(f"  {exclusive_ms:>6} ms excl / {total_ms:>6} ms total  {label}{suffix}")

    if blocker_chains:
        print("Blockers:")
        for item in blocker_chains[:limit]:
            chain = [str(span_id) for span_id in list(item.get("blocker_chain_span_ids") or [])]
            target_span = span_index.get(str(item.get("target_span_id") or ""), {})
            target_label = str(target_span.get("label") or item.get("target_span_id") or "").strip()
            waiting_ms = item.get("waiting_duration_ms")
            wait_part = f" wait={int(waiting_ms or 0)} ms" if waiting_ms is not None else ""
            chain_labels = [
                str(span_index.get(span_id, {}).get("label") or span_id).strip()
                for span_id in chain
            ]
            if not chain_labels:
                chain_labels = [
                    str(span_index.get(span_id, {}).get("label") or span_id).strip()
                    for span_id in list(item.get("direct_blocker_span_ids") or [])
                ]
            chain_text = " -> ".join(chain_labels) if chain_labels else "(none)"
            print(f"  {target_label}:{wait_part}  {chain_text}")


def _print_scheduler_replay(payload: dict[str, object], *, limit: int) -> None:
    adapter = str(payload.get("adapter") or "auto")
    source_path = str(payload.get("source_path") or "")
    barrier = dict(payload.get("terminal_barrier") or {})
    top_bottlenecks = list(payload.get("top_bottlenecks") or [])
    missed_parallelism = list(payload.get("missed_parallelism") or [])

    print(f"Adapter: {adapter}")
    if source_path:
        print(f"Source: {source_path}")
    print(
        "Rows: "
        f"{int(payload.get('source_row_count') or 0)} source -> "
        f"{int(payload.get('normalized_row_count') or 0)} normalized"
    )
    print(
        "Scheduler replay: "
        f"makespan={int(payload.get('observed_makespan_ms') or 0)} ms; "
        f"capacity={int(payload.get('capacity_hint') or 1)} "
        f"({str(payload.get('capacity_source') or 'default')}); "
        f"work={int(payload.get('total_exclusive_work_ms') or 0)} ms; "
        f"avg_parallel={float(payload.get('average_parallelism') or 0.0):.3f}"
    )
    print(
        "Lower bounds: "
        f"critical_path={int(payload.get('critical_path_lower_bound_ms') or 0)} ms; "
        f"work/capacity={int(payload.get('work_capacity_lower_bound_ms') or 0)} ms; "
        f"effective={int(payload.get('scheduler_lower_bound_ms') or 0)} ms; "
        f"slack={int(payload.get('slack_ms') or 0)} ms"
    )

    barrier_labels = [str(label).strip() for label in list(barrier.get("labels") or []) if str(label).strip()]
    if barrier_labels:
        print(
            "Terminal barrier: "
            f"{int(barrier.get('duration_ms') or 0)} ms over "
            f"{len(barrier_labels)} spans"
        )
        print(f"  {' -> '.join(barrier_labels[:limit])}")

    if top_bottlenecks:
        print("Bottlenecks:")
        for item in top_bottlenecks[:limit]:
            row = dict(item)
            label = str(row.get("label") or row.get("span_id") or "").strip()
            lane_id = str(row.get("lane_id") or "").strip()
            lane_part = f" [{lane_id}]" if lane_id else ""
            reason = str(row.get("reason") or "").strip()
            reason_part = f" reason={reason}" if reason else ""
            print(
                f"  {int(row.get('exclusive_duration_ms') or 0):>6} ms{lane_part}  "
                f"{label}{reason_part}"
            )

    if missed_parallelism:
        print("Missed parallelism:")
        for item in missed_parallelism[:limit]:
            row = dict(item)
            source_label = str(row.get("source_label") or row.get("source_span_id") or "").strip()
            target_label = str(row.get("target_label") or row.get("target_span_id") or "").strip()
            lane_id = str(row.get("lane_id") or "").strip()
            lane_part = f" [{lane_id}]" if lane_id else ""
            wait_part = ""
            if row.get("waiting_duration_ms") is not None:
                wait_part = f" wait={int(row.get('waiting_duration_ms') or 0)} ms"
            print(
                f"  {int(row.get('estimated_gain_upper_bound_ms') or 0):>6} ms ub{lane_part}  "
                f"{source_label} -> {target_label}{wait_part}"
            )


def _print_prompt_pressure(payload: dict[str, object], *, limit: int) -> None:
    print(
        "Prompt pressure: "
        f"requests={int(payload.get('model_request_count') or 0)}; "
        f"budget_triggered={int(payload.get('budget_triggered_count') or 0)}; "
        f"emergency={int(payload.get('emergency_compaction_count') or 0)}; "
        f"context_retries={int(payload.get('context_length_retry_count') or 0)}"
    )
    print(
        "Rows: "
        f"{int(payload.get('source_row_count') or 0)} rows from "
        f"{int(payload.get('source_file_count') or 0)} file(s)"
    )
    target = payload.get("current_target_chars")
    emergency = payload.get("current_emergency_chars")
    peak = payload.get("max_final_chars")
    schemas = int(payload.get("max_tool_schema_chars") or 0)
    target_text = str(int(target)) if target is not None else "unknown"
    emergency_text = str(int(emergency)) if emergency is not None else "unknown"
    peak_text = str(int(peak)) if peak is not None else "unknown"
    print(
        "Budgets: "
        f"target={target_text} chars; emergency={emergency_text} chars; "
        f"peak_final={peak_text} chars; max_tool_schema={schemas} chars"
    )
    token_count = int(payload.get("prompt_token_observation_count") or 0)
    if token_count:
        avg_ratio = float(payload.get("avg_chars_per_prompt_token") or 0.0)
        max_tokens = int(payload.get("max_prompt_tokens") or 0)
        print(
            "Prompt tokens: "
            f"observations={token_count}; avg_chars_per_prompt_token={avg_ratio:.3f}; "
            f"max_prompt_tokens={max_tokens}"
        )
    print(f"Recommendation: {str(payload.get('calibration_recommendation') or '')}")

    calls = list(payload.get("highest_pressure_calls") or [])
    if calls:
        print("Highest pressure:")
        for item in calls[:limit]:
            row = dict(item)
            call_id = str(row.get("model_call_id") or "").strip() or "(no model_call_id)"
            worker_id = str(row.get("worker_id") or "").strip()
            final_chars = int(row.get("final_chars") or 0)
            ratio = row.get("final_to_emergency_ratio") or row.get("final_to_budget_ratio")
            ratio_part = f" ratio={float(ratio):.3f}" if ratio is not None else ""
            prompt_tokens = row.get("prompt_tokens")
            token_part = f" prompt_tokens={int(prompt_tokens)}" if prompt_tokens else ""
            worker_part = f" worker={worker_id}" if worker_id else ""
            print(
                f"  {final_chars:>8} chars{ratio_part}{token_part}{worker_part} "
                f"call={call_id}"
            )

    retries = list(payload.get("context_length_retries") or [])
    if retries:
        print("Context retries:")
        for item in retries[:limit]:
            row = dict(item)
            call_id = str(row.get("model_call_id") or "").strip() or "(no model_call_id)"
            error_type = str(row.get("error_type") or "").strip()
            final_chars = row.get("final_chars")
            final_part = f" final_chars={int(final_chars)}" if final_chars else ""
            error_part = f" error_type={error_type}" if error_type else ""
            print(f"  call={call_id}{final_part}{error_part}")

    notes = [str(note).strip() for note in list(payload.get("notes") or []) if str(note).strip()]
    if notes:
        print("Notes:")
        for note in notes[:limit]:
            print(f"  - {note}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
