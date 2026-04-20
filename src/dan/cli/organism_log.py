"""dan-organism-log — import and summarize ``organism_log_v1`` traces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from dan.worker.organism_log_analysis import analyze_organism_log_rows
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
