"""dan-status — list active and recent DAN workflow runs.

Reads PID files and event logs from ``~/.dan/runs/`` to show status
of foreground and background runs.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from dan.cli.process_utils import is_process_alive


def _is_pid_alive(pid: int) -> bool:
    """Check if a process is still running."""
    return is_process_alive(pid)


def _parse_events_summary(events_path: Path) -> dict:
    """Read a .events.jsonl and extract summary info."""
    total_nodes = 0
    completed = 0
    failed = 0
    last_event = ""
    run_id = ""
    started_at = 0.0
    finished_at = 0.0
    success: bool | None = None

    try:
        with open(events_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                et = ev.get("event_type", "")
                ts = ev.get("timestamp", 0)
                if not run_id:
                    run_id = ev.get("run_id", "")
                if et == "run_started":
                    started_at = ts
                    total_nodes = ev.get("data", {}).get("total_nodes", 0)
                elif et == "node_completed":
                    completed += 1
                elif et == "node_failed":
                    failed += 1
                elif et == "run_completed":
                    finished_at = ts
                    success = True
                elif et == "run_failed":
                    finished_at = ts
                    success = False
                last_event = et
    except FileNotFoundError:
        pass

    return {
        "run_id": run_id,
        "total_nodes": total_nodes,
        "completed": completed,
        "failed": failed,
        "last_event": last_event,
        "started_at": started_at,
        "finished_at": finished_at,
        "success": success,
    }


def _format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "-"
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.1f}m"
    return f"{minutes / 60:.1f}h"


def _format_time(ts: float) -> str:
    if ts <= 0:
        return "-"
    return time.strftime("%H:%M:%S", time.localtime(ts))


def list_runs(runs_dir: Path) -> list[dict]:
    """Collect info on all runs in ~/.dan/runs/."""
    runs = []
    for pid_file in sorted(runs_dir.glob("*.pid"), key=lambda p: p.stat().st_mtime, reverse=True):
        run_id = pid_file.stem
        try:
            lines = pid_file.read_text().strip().split("\n")
            pid = int(lines[0])
        except (ValueError, IndexError):
            continue

        alive = _is_pid_alive(pid)
        events_path = runs_dir / f"{run_id}.events.jsonl"
        summary = _parse_events_summary(events_path)

        if summary["success"] is True:
            status = "completed"
        elif summary["success"] is False:
            status = "failed"
        elif alive:
            status = "running"
        else:
            status = "stopped"

        duration = 0.0
        if summary["started_at"]:
            end = summary["finished_at"] or time.time()
            duration = end - summary["started_at"]

        runs.append({
            "run_id": run_id,
            "pid": pid,
            "alive": alive,
            "status": status,
            "duration": duration,
            "nodes": f"{summary['completed']}/{summary['total_nodes']}",
            "failed": summary["failed"],
            "started": summary["started_at"],
        })
    return runs


def print_runs_rich(runs: list[dict]) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    console = Console()
    table = Table(title="DAN Runs")
    table.add_column("Run ID", style="bold")
    table.add_column("PID", justify="right")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Nodes", justify="right")
    table.add_column("Started")

    status_style = {
        "running": "bold yellow",
        "completed": "green",
        "failed": "bold red",
        "stopped": "dim",
    }

    for r in runs:
        table.add_row(
            r["run_id"],
            str(r["pid"]),
            Text(r["status"], style=status_style.get(r["status"], "")),
            _format_duration(r["duration"]),
            r["nodes"],
            _format_time(r["started"]),
        )

    if not runs:
        console.print("[dim]No runs found in ~/.dan/runs/[/]")
    else:
        console.print(table)


def print_runs_plain(runs: list[dict]) -> None:
    if not runs:
        print("No runs found in ~/.dan/runs/")
        return
    header = f"{'RUN ID':<12} {'PID':>6} {'STATUS':<10} {'DURATION':>8} {'NODES':>7} {'STARTED':>8}"
    print(header)
    print("-" * len(header))
    for r in runs:
        print(
            f"{r['run_id']:<12} {r['pid']:>6} {r['status']:<10} "
            f"{_format_duration(r['duration']):>8} {r['nodes']:>7} "
            f"{_format_time(r['started']):>8}"
        )


def print_runs_json(runs: list[dict]) -> None:
    print(json.dumps(runs, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dan-status",
        description="List active and recent DAN workflow runs.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--kill",
        metavar="RUN_ID",
        help="Send SIGTERM to a running background process",
    )
    args = parser.parse_args()

    from dan.cli import RUNS_DIR
    if not RUNS_DIR.exists():
        print("No runs directory found (~/.dan/runs/).")
        return

    if args.kill:
        pid_file = RUNS_DIR / f"{args.kill}.pid"
        if not pid_file.exists():
            print(f"Run not found: {args.kill}", file=sys.stderr)
            sys.exit(1)
        try:
            pid = int(pid_file.read_text().strip().split("\n")[0])
            os.kill(pid, signal.SIGTERM)
            print(f"Sent SIGTERM to PID {pid} (run {args.kill})")
        except (ValueError, ProcessLookupError, OSError) as exc:
            print(f"Cannot kill: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    runs = list_runs(RUNS_DIR)

    if args.json:
        print_runs_json(runs)
    else:
        try:
            print_runs_rich(runs)
        except ImportError:
            print_runs_plain(runs)


if __name__ == "__main__":
    main()
