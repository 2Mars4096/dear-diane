"""dan-logs — tail event logs for a DAN workflow run.

Reads ``~/.dan/runs/{run_id}.events.jsonl`` and displays events,
optionally following in real time (``--follow``).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _format_event_rich(ev: dict, console: object) -> None:
    """Pretty-print a single event via Rich."""
    from rich.text import Text

    et = ev.get("event_type", "?")
    nid = ev.get("node_id", "")
    ts = ev.get("timestamp", 0)
    data = ev.get("data", {})
    ts_str = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "??:??:??"

    style_map = {
        "run_started": "bold green",
        "run_completed": "bold green",
        "run_failed": "bold red",
        "node_started": "yellow",
        "node_completed": "green",
        "node_failed": "bold red",
        "node_skipped": "dim",
        "llm_thinking": "cyan",
        "tool_call_started": "magenta",
        "tool_call_result": "magenta",
        "intermediate_text": "blue",
    }
    style = style_map.get(et, "")

    line = Text()
    line.append(f"{ts_str} ", style="dim")
    line.append(f"{et:<24}", style=style)
    if nid:
        line.append(f" [{nid}]", style="bold")

    text = data.get("text", data.get("error", data.get("message", "")))
    if isinstance(text, str) and text:
        line.append(f"  {text[:120]}")

    console.print(line)  # type: ignore[union-attr]


def _format_event_plain(ev: dict) -> str:
    et = ev.get("event_type", "?")
    nid = ev.get("node_id", "")
    ts = ev.get("timestamp", 0)
    data = ev.get("data", {})
    ts_str = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "??:??:??"
    text = data.get("text", data.get("error", data.get("message", "")))
    node_part = f" [{nid}]" if nid else ""
    text_part = f"  {text[:120]}" if isinstance(text, str) and text else ""
    return f"{ts_str} {et:<24}{node_part}{text_part}"


def tail_events(events_path: Path, *, follow: bool, last_n: int, json_out: bool) -> None:
    """Read and display events from a JSONL file."""
    Console = None
    console = None
    if not json_out:
        try:
            from rich.console import Console as _C
            Console = _C
            console = Console()
        except ImportError:
            pass

    if not events_path.exists():
        print(f"Events file not found: {events_path}", file=sys.stderr)
        sys.exit(1)

    lines_seen = 0
    all_lines: list[str] = []

    with open(events_path) as f:
        all_lines = f.readlines()

    start = max(0, len(all_lines) - last_n) if last_n > 0 else 0
    for line in all_lines[start:]:
        _display_line(line, json_out, console)
        lines_seen += 1

    if not follow:
        return

    lines_seen = len(all_lines)
    try:
        while True:
            with open(events_path) as f:
                current_lines = f.readlines()
            if len(current_lines) > lines_seen:
                for line in current_lines[lines_seen:]:
                    _display_line(line, json_out, console)
                lines_seen = len(current_lines)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


def _display_line(line: str, json_out: bool, console: object | None) -> None:
    line = line.strip()
    if not line:
        return
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        print(line)
        return

    if json_out:
        print(line, flush=True)
    elif console is not None:
        _format_event_rich(ev, console)
    else:
        print(_format_event_plain(ev))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dan-logs",
        description="Tail event logs for a DAN workflow run.",
    )
    parser.add_argument(
        "run_id",
        help="Run ID to tail (from dan-status or dan-run --bg output)",
    )
    parser.add_argument(
        "--follow", "-f",
        action="store_true",
        help="Stream new events as they arrive (like tail -f)",
    )
    parser.add_argument(
        "--last", "-n",
        type=int,
        default=50,
        metavar="N",
        help="Show last N events (default: 50, 0 for all)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSONL",
    )
    args = parser.parse_args()

    from dan.cli import RUNS_DIR

    events_path = RUNS_DIR / f"{args.run_id}.events.jsonl"
    if not events_path.exists():
        alt = Path(args.run_id)
        if alt.exists() and alt.suffix == ".jsonl":
            events_path = alt
        else:
            print(f"Events file not found: {events_path}", file=sys.stderr)
            print(f"Available runs:", file=sys.stderr)
            if RUNS_DIR.exists():
                for p in sorted(RUNS_DIR.glob("*.events.jsonl")):
                    print(f"  {p.stem.replace('.events', '')}", file=sys.stderr)
            sys.exit(1)

    tail_events(events_path, follow=args.follow, last_n=args.last, json_out=args.json)


if __name__ == "__main__":
    main()
