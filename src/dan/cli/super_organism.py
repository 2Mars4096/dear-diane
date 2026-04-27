"""dan-super-organism — Super DAN organism showcase and live executor."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from dan.cli import load_env, normalize_workspace_root, resolve_config
from dan.cli import live_gateway
from dan.cli.dispatch import OrchestratorChoice, select_orchestrator
from dan.cli.super_hooks import (
    SuperHookRuntime,
    format_super_queue_status,
)
from dan.providers import LLMProvider
from dan.server.runtime_config import build_engine_config_from_env
from dan.worker.brief import RoleSpec, WorkerBrief, request_from_brief
from dan.worker.cell import build_cell
from dan.worker.contracts import snippets
from dan.worker.contracts.templates import coding_brief, review_brief
from dan.worker.core.contracts import ExecutionRequest, OutputContract
from dan.worker.core.executor import WorkerCoreExecutor
from dan.worker.core.interfaces import CallbackEventSink
from dan.worker.core.model import WorkerDefinition
from dan.worker.organism_log import (
    ORGANISM_LOG_SCHEMA_VERSION,
    OrganismLogContext,
    OrganismLogWriter,
    new_trace_id,
)
from dan.worker.organisms.local_runtime import (
    LocalOrganismToolRuntime,
    ToolLoopCompletionProvider,
)
from dan.worker.organisms.super_organism import (
    DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP,
    DEFAULT_SUPER_ORGANISM_CELL_COUNT,
    DEFAULT_SUPER_ORGANISM_ID,
    DEFAULT_SUPER_ORGANISM_TARGET,
    SuperOrganismReport,
    run_super_organism_demo,
)


_LIVE_FILE_WRITE_SAFE_WORD_LIMIT = 1200
_LIVE_FILE_WRITE_SAFE_LINE_LIMIT = 200


def _live_pacing_policy(*, forbid_scratch_files: bool = False) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "safe_file_write_word_limit": _LIVE_FILE_WRITE_SAFE_WORD_LIMIT,
        "safe_file_write_line_limit": _LIVE_FILE_WRITE_SAFE_LINE_LIMIT,
        "prefer_incremental_file_edit_on_existing_files": True,
    }
    if forbid_scratch_files:
        policy["forbid_scratch_files_outside_required_artifacts"] = True
    return policy


def _live_pacing_contract(policy: Mapping[str, Any] | None = None) -> str:
    return snippets.pacing_contract(policy or _live_pacing_policy())


class SuperRunEventLogger:
    """Persist timestamped Super DAN live events to one JSONL file."""

    def __init__(
        self,
        *,
        path: Path,
        session_id: str = "",
        turn_id: str = "",
        task_id: str = "",
        organism_id: str = "",
        organ_id: str = "",
        trace_id: str = "",
        progress_callback=None,
        hook_runtime: SuperHookRuntime | None = None,
    ) -> None:
        self._writer = OrganismLogWriter(
            path=path,
            context=OrganismLogContext(
                product="dan_super",
                stream_kind="bounded_run",
                session_id=session_id,
                turn_id=turn_id,
                task_id=task_id,
                trace_id=trace_id,
                organism_id=organism_id,
                organ_id=organ_id,
            ),
        )
        self._progress_callback = progress_callback
        self._hook_runtime = hook_runtime
        self.path = self._writer.path

    def emit(self, event: dict[str, Any]) -> None:
        payload = dict(event)
        row = self._writer.emit(payload)
        if self._progress_callback is not None:
            self._progress_callback(dict(row))
        self._emit_hook_rows(row)

    def _emit_hook_rows(self, row: dict[str, Any]) -> None:
        if self._hook_runtime is None:
            return
        for hook_event in self._hook_runtime.process_event(dict(row)):
            hook_row = self._writer.emit(dict(hook_event))
            if self._progress_callback is not None:
                self._progress_callback(dict(hook_row))

    def emit_trace_rows(self, trace_rows: Sequence[dict[str, Any]]) -> None:
        rows = [dict(row) for row in trace_rows]
        self._writer.emit_trace_rows(rows)
        if self._progress_callback is not None:
            for row in rows:
                self._progress_callback(dict(row))

    def update_context(self, **updates: Any) -> None:
        self._writer.update_context(**updates)

    def hook_state_snapshot(self) -> dict[str, Any] | None:
        if self._hook_runtime is None:
            return None
        return self._hook_runtime.snapshot()

    def close(self) -> None:
        self._writer.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dan-super-organism",
        description=(
            "Run Super DAN from the command line. With an objective and configured "
            "model, the CLI runs the native live execution lane; use --plan-only for "
            "the coordination contract/showcase."
        ),
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help=(
            "Operator objective. Omit in a terminal to start an interactive Super DAN session; "
            "non-interactive calls still run the default universal-agent objective "
            f"({DEFAULT_SUPER_ORGANISM_TARGET!r})."
        ),
    )
    parser.add_argument(
        "--organism-id",
        default=DEFAULT_SUPER_ORGANISM_ID,
        help="Organism identifier stamped into the report.",
    )
    parser.add_argument(
        "--cell-count",
        type=int,
        default=DEFAULT_SUPER_ORGANISM_CELL_COUNT,
        help="Logical cell count. Defaults to the cheaper 20-cell operator mode; use 100 for the showcase.",
    )
    parser.add_argument(
        "--active-cell-cap",
        type=int,
        default=DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP,
        help="Maximum logical cells active in one scheduler wave.",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Only print the organism contract; do not materialize deterministic artifacts.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Force Super DAN's native live execution lane with local tools. "
            "Objectives auto-enter this lane when a live model is configured."
        ),
    )
    parser.add_argument(
        "--model",
        help="Model for --live. Defaults to DAN_MODEL or DAN_LLM_MODEL.",
    )
    parser.add_argument(
        "--api-key",
        help="Optional API key override for --live.",
    )
    parser.add_argument(
        "--base-url",
        help="Optional OpenAI-compatible base URL override for --live.",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=10,
        help="Maximum model/tool rounds for --live. Defaults to 10.",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=64,
        help="Maximum local tool calls for --live. Defaults to 64.",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root for Super DAN artifacts. Defaults to the current directory.",
    )
    parser.add_argument(
        "--artifact-dir",
        default="website",
        help=(
            "Directory, relative to --workspace unless absolute, for generated website artifacts. "
            "In code-like live mode, a website-named workspace is treated as the artifact root when this is omitted."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output. Live mode includes both the report and live build result.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the full organism text report instead of the compact CLI summary.",
    )
    parser.add_argument(
        "--quiet-progress",
        action="store_true",
        help="Disable live progress lines for non-JSON runs.",
    )
    parser.add_argument(
        "--reactivity",
        choices=("immediate", "balanced", "batch"),
        default="balanced",
        help=(
            "Super DAN hook/inbox reactivity profile for live runs. "
            "Immediate wakes organs quickly, balanced coalesces short bursts, batch waits longer."
        ),
    )
    parser.add_argument(
        "--queue-status",
        action="store_true",
        help="Print Super DAN hook/inbox queue state. With no objective, only prints status.",
    )
    parser.add_argument(
        "--worktree-parallelism",
        type=int,
        default=0,
        help=(
            "Maximum planned isolated worktree patch lanes for conflicting owners. "
            "Current live execution remains main-lane authoritative."
        ),
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the JSON report.",
    )
    return parser


def _print_text_report(report: SuperOrganismReport) -> None:
    lines = _text_report_lines(report)
    print("\n".join(lines), end="\n")


def _print_compact_report(
    report: SuperOrganismReport,
    *,
    artifact_paths: Sequence[Path] | None = None,
) -> None:
    paths = list(artifact_paths or [])
    if paths:
        lines = [
            "Status: completed",
            "Build: completed",
            f"Website: {paths[0]}",
            "Files:",
            *[f"- {path}" for path in paths],
            "",
            f"Organism: {_display_text(report.organism_id)} ({report.cell_count} cells, active cap {report.active_cell_cap})",
            f"Target: {_display_text(report.target)}",
            "Full trace: rerun with --verbose or --json.",
        ]
        print("\n".join(lines), end="\n\n")
        return

    lines = [
        f"Status: {_display_text(report.status)}",
        f"Organism: {_display_text(report.organism_id)} ({report.cell_count} cells, active cap {report.active_cell_cap})",
        f"Target: {_display_text(report.target)}",
        f"Verdict: {_display_text(report.final_verdict)}",
        f"{_display_text(report.score_label)}: {report.credibility_score:.2f}",
        f"Execution Family: {_display_text(report.execution_family)}",
        "Organs: " + ", ".join(f"{key}={value}" for key, value in sorted(report.organ_counts.items())),
        "Coordination: "
        + " -> ".join(
            [
                "objective",
                "context",
                "decompose",
                "memory",
                "immune",
                "reallocate",
                "probe",
                "synthesize",
            ]
        ),
        "",
        "Full trace: rerun with --verbose or --json.",
    ]
    print("\n".join(lines), end="\n")


def _print_live_report(
    report: SuperOrganismReport,
    live_result: dict[str, Any],
    *,
    verbose: bool = False,
) -> None:
    files = [str(path) for path in live_result.get("files") or []]
    missing = [str(path) for path in live_result.get("missing_files") or []]
    status = _display_text(live_result.get("status") or "failed")
    summary_label = _display_text(live_result.get("summary_label") or "Live Run")
    lines = [
        "Status: completed" if status == "completed" else "Status: failed",
        f"{summary_label}: {status}",
    ]
    website = str(live_result.get("website") or "").strip()
    if website:
        lines.append(f"Website: {website}")
    if files and website:
        lines.append("Files:")
        lines.extend(f"- {path}" for path in files)
    elif files:
        lines.append("Mutated Paths:")
        lines.extend(f"- {path}" for path in files)
    if missing:
        lines.append("Missing Files:")
        lines.extend(f"- {path}" for path in missing)
    error = str(live_result.get("error") or "").strip()
    if error:
        lines.append(f"Error: {_display_text(error)}")
    failed_step = str(live_result.get("failed_step") or "").strip()
    if failed_step and status != "completed":
        lines.append(f"Failed Step: {_display_text(failed_step)}")
    validation = dict(live_result.get("validation") or {})
    if validation:
        verdict = "passed" if validation.get("passed") else "failed"
        score = _coerce_float(validation.get("overall_score"))
        lines.append(f"Validation: {verdict} ({score:.2f})")
    event_log_path = str(live_result.get("event_log_path") or "").strip()
    if event_log_path:
        lines.append(f"Event Log: {event_log_path}")
    hook_state = live_result.get("hook_state")
    if bool(live_result.get("show_queue_status")) and isinstance(hook_state, dict):
        inboxes = hook_state.get("inboxes") if isinstance(hook_state.get("inboxes"), dict) else {}
        lines.append("Hook Queues:")
        for inbox_id in sorted(inboxes):
            inbox = inboxes.get(inbox_id) if isinstance(inboxes.get(inbox_id), dict) else {}
            metrics = inbox.get("metrics") if isinstance(inbox.get("metrics"), dict) else {}
            pending = len(list(inbox.get("pending_packet_ids") or []))
            active = len(list(inbox.get("active_lease_ids") or []))
            lines.append(
                f"- {inbox_id}: pending={pending} active={active} "
                f"enqueued={int(metrics.get('enqueued') or 0)} "
                f"leased={int(metrics.get('leased') or 0)} "
                f"coalesced={int(metrics.get('coalesced') or 0)}"
            )
    lines.extend(
        [
            f"Model: {_display_text(live_result.get('model') or '')}",
            f"Tool Calls: {int(live_result.get('tool_calls') or 0)}",
            f"Token Usage: {_format_token_usage(live_result.get('token_usage'))}",
            "",
            f"Organism: {_display_text(report.organism_id)} ({report.cell_count} cells, active cap {report.active_cell_cap})",
            f"Target: {_display_text(report.target)}",
            "Full trace shown below." if verbose else "Full trace: rerun with --verbose or --json.",
        ]
    )
    print("\n".join(lines), end="\n")
    if verbose:
        print()
        _print_text_report(report)


def _text_report_lines(report: SuperOrganismReport) -> list[str]:
    lines = [
        f"Status: {_display_text(report.status)}",
        f"Mode: {_display_text(report.mode)}",
        f"Organism Contract: {_display_text(report.scenario.value)}",
        f"Organism: {_display_text(report.organism_id)}",
        f"Target: {_display_text(report.target)}",
        f"Cells: {report.cell_count} logical | Active cap: {report.active_cell_cap}",
        f"Max Active Observed: {report.max_active_observed}",
        f"Verdict: {_display_text(report.final_verdict)}",
        f"{_display_text(report.score_label)}: {report.credibility_score:.2f}",
        f"Execution Family: {_display_text(report.execution_family)}",
        f"Stages: {_display_text(' -> '.join(report.stage_sequence))}",
        "Organs: " + ", ".join(f"{key}={value}" for key, value in sorted(report.organ_counts.items())),
        "Signals: " + ", ".join(f"{key}={value}" for key, value in sorted(report.signal_counts.items())),
    ]
    if report.claim_graph:
        lines.extend(["", "Claim Graph:"])
        for claim in report.claim_graph:
            lines.append(
                f"- {claim.claim_id} [{claim.status.value}, {claim.confidence:.2f}] "
                f"{_display_text(claim.text)}"
            )
    if report.delivery_plan:
        lines.extend(["", "Delivery Plan:"])
        for node in report.delivery_plan:
            lines.append(
                f"- {node.node_id} | {node.status} | {node.assigned_cell_count} cells | "
                f"{_display_text(node.title)}"
            )
    if report.shared_board is not None:
        lines.extend(["", "Board:"])
        lines.append(
            "- completed="
            + str(len(report.shared_board.completed_ticket_ids))
            + ", waiting="
            + str(len(report.shared_board.waiting_ticket_ids))
            + ", blocked="
            + str(len(report.shared_board.blocked_ticket_ids))
            + ", reserve="
            + str(len(report.shared_board.reserve_cell_ids))
        )
        lines.append(
            "- packets: published="
            + str(len(report.shared_board.published_packet_ids))
            + ", pending="
            + str(len(report.shared_board.pending_packet_ids))
        )
    if report.coordination_tickets:
        lines.extend(["", "Tickets:"])
        for ticket in report.coordination_tickets:
            lines.append(
                f"- {ticket.ticket_id} | {ticket.status} | owner {ticket.owner_cell_id} | "
                f"{_display_text(ticket.title)}"
            )
    if report.handoff_packets:
        lines.extend(["", "Handoffs:"])
        for packet in report.handoff_packets:
            lines.append(
                f"- {packet.packet_id} | {packet.status} | {packet.from_ticket_id}->{packet.to_ticket_id} | "
                f"{packet.packet_type}"
            )
    lines.extend(["", "Reallocations:"])
    for decision in report.reallocation_decisions:
        lines.append(
            f"- {decision.decision_id}: {decision.cell_count} "
            f"{decision.from_organ.value}->{decision.to_organ.value}; {_display_text(decision.reason)}"
        )
    if report.final_audit is not None:
        lines.extend(
            [
                "",
                "Final Audit:",
                f"- {report.final_audit.status} | satisfied={str(report.final_audit.satisfied).lower()} | blockers: "
                f"{_display_text(', '.join(report.final_audit.blocker_ticket_ids) or 'none')}",
                _display_text(report.final_audit.summary),
            ]
        )
    lines.extend(["", "Final Memo:", _display_text(report.final_memo)])
    lines.extend(["", f"Caveat: {_display_text(report.caveat)}"])
    return lines


def _display_text(value: object) -> str:
    return " ".join(str(value).split())


def _truncate_text(value: Any, *, limit: int = 160) -> str:
    text = _display_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _path_basename(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return Path(text).name or text
    except Exception:
        return text


def _tool_request_summary(tool_id: str, arguments: Mapping[str, Any]) -> str:
    args = dict(arguments or {})
    if tool_id == "file_edit":
        mode = str(args.get("mode") or "replace")
        path = str(args.get("path") or args.get("file_path") or "(missing path)")
        start_line = args.get("start_line")
        end_line = args.get("end_line", start_line)
        if start_line is not None:
            return f"{mode} {_path_basename(path)}:{start_line}-{end_line}"
        old_string = str(args.get("old_string") or "")
        if old_string:
            return f"{mode} {_path_basename(path)} ({len(old_string)} chars anchor)"
        edits = args.get("edits")
        if isinstance(edits, list):
            return f"{mode} {_path_basename(path)} ({len(edits)} edits)"
        return f"{mode} {_path_basename(path)}"
    if tool_id == "file_write":
        mode = str(args.get("mode") or "overwrite")
        path = str(args.get("path") or "(missing path)")
        content = str(args.get("content") or "")
        return f"{mode} {_path_basename(path)} ({len(content.encode('utf-8'))} bytes)"
    if tool_id == "file_read":
        path = str(args.get("path") or "(missing path)")
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        if start_line is not None or end_line is not None:
            return f"{_path_basename(path)}:{start_line or 1}-{end_line or 'end'}"
        return _path_basename(path)
    if tool_id == "list_directory":
        return str(args.get("path") or ".")
    if tool_id == "shell_command":
        return _truncate_text(args.get("command") or "(missing command)", limit=160)
    if tool_id in {"git_status", "git_diff", "git_log"}:
        return f"path={args.get('path') or '.'}"
    if "path" in args:
        return _truncate_text(args.get("path") or "", limit=160)
    try:
        return _truncate_text(
            json.dumps(args, ensure_ascii=False, sort_keys=True, default=str),
            limit=160,
        )
    except Exception:
        return _truncate_text(str(args), limit=160)


def _tool_result_summary(tool_id: str, payload: Mapping[str, Any]) -> str:
    result = payload.get("result")
    if str(payload.get("status") or "").strip() in {"failed", "denied"}:
        return str(payload.get("error") or "tool failed")
    if isinstance(result, dict):
        if tool_id == "list_directory":
            count = result.get("count")
            total_count = result.get("total_count")
            remaining = result.get("remaining_count")
            summary = f"entries={count if count is not None else len(result.get('entries') or [])}"
            if total_count not in {None, count}:
                summary += f"/{total_count}"
            if result.get("truncated") and remaining is not None:
                summary += f" remaining={remaining}"
            return summary
        if tool_id == "file_read":
            parts: list[str] = []
            if result.get("line_count") is not None:
                parts.append(f"lines={result.get('line_count')}")
            if result.get("size") is not None:
                parts.append(f"bytes={result.get('size')}")
            path = result.get("path")
            if path:
                parts.append(f"path={_path_basename(path)}")
            return " ".join(parts) or "read file"
        if tool_id == "file_write":
            path = result.get("path")
            bytes_written = result.get("bytes_written")
            if bytes_written is not None:
                return f"wrote {bytes_written} bytes to {_path_basename(path)}"
            return f"wrote {_path_basename(path)}"
        if tool_id == "file_edit":
            path = result.get("path")
            mode = result.get("mode") or "edit"
            start_line = result.get("start_line")
            end_line = result.get("end_line")
            changed = result.get("changed")
            suffix = " changed=no" if changed is False else ""
            if start_line is not None:
                return f"{mode} {_path_basename(path)}:{start_line}-{end_line}{suffix}"
            return f"{mode} {_path_basename(path)}{suffix}"
        if tool_id == "shell_command":
            stdout = str(result.get("stdout") or "")
            stderr = str(result.get("stderr") or "")
            return (
                f"exit={result.get('exit_code', '?')} "
                f"stdout={len(stdout)} chars stderr={len(stderr)} chars"
            )
        if tool_id == "git_diff":
            return (
                f"files={result.get('files_changed', 0)} "
                f"+{result.get('additions', 0)} -{result.get('deletions', 0)}"
            )
        if tool_id == "git_status":
            return (
                f"modified={len(result.get('modified', []) or [])} "
                f"untracked={len(result.get('untracked', []) or [])}"
            )
        if "path" in result:
            return _truncate_text(result.get("path") or "", limit=160)
    if payload.get("error"):
        return _truncate_text(payload.get("error"), limit=160)
    try:
        return _truncate_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, default=str),
            limit=160,
        )
    except Exception:
        return _truncate_text(str(result), limit=160)


class SuperProgressRenderer:
    """Render concise live Super DAN progress in the terminal."""

    def __init__(self, *, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._seen: set[tuple[str, str, str]] = set()

    @staticmethod
    def _timestamp(event: Mapping[str, Any]) -> str:
        raw = str(event.get("timestamp") or "").strip()
        if raw:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
            except ValueError:
                pass
        return datetime.now().astimezone().strftime("%H:%M:%S")

    @staticmethod
    def _scope(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if text.startswith("super-dan.live."):
            text = text[len("super-dan.live.") :]
        if text.endswith("-builder"):
            return "builder"
        if text.endswith("-validator"):
            return "validator"
        if "." in text:
            text = text.split(".")[-1]
        return text

    def _emit(self, event: Mapping[str, Any], message: str) -> None:
        print(f"[{self._timestamp(event)}] {message}", flush=True)

    def _print_once(self, key: tuple[str, str, str], event: Mapping[str, Any], message: str) -> None:
        if key in self._seen:
            return
        self._seen.add(key)
        self._emit(event, message)

    def __call__(self, event: dict[str, Any]) -> None:
        if not self._enabled:
            return
        name = str(event.get("event") or "").strip()
        if not name:
            return
        if name == "run.log.started":
            objective = _truncate_text(event.get("objective") or "", limit=180)
            self._emit(event, f"[run] started: {objective}")
            return
        if name == "super.hook.runtime.started":
            profile = str(event.get("reactivity_profile") or "balanced")
            inboxes = ",".join(str(item) for item in list(event.get("inboxes") or [])[:6])
            suffix = f" inboxes={inboxes}" if inboxes else ""
            self._emit(event, f"[hooks] runtime started: reactivity={profile}{suffix}")
            return
        if name == "super.worktree.policy.configured":
            parallelism = int(event.get("worktree_parallelism") or 0)
            root = _truncate_text(event.get("worktree_root") or "", limit=120)
            self._emit(event, f"[hooks] worktree lane configured: parallelism={parallelism} root={root}")
            return
        if name == "super.hook.packet_enqueued":
            inbox_id = str(event.get("inbox_id") or "inbox")
            source = str(event.get("source_event") or "event")
            packet_type = str(event.get("packet_type") or "packet")
            depth = int(event.get("queue_depth") or 0)
            self._emit(event, f"[hooks] {inbox_id} <= {source} ({packet_type}, depth={depth})")
            return
        if name.startswith("super.inbox."):
            inbox_id = str(event.get("inbox_id") or "inbox")
            reason = _truncate_text(event.get("reason") or event.get("queue_full_action") or "", limit=120)
            action = name.rsplit(".", 1)[-1].replace("packet_", "")
            suffix = f": {reason}" if reason else ""
            self._emit(event, f"[hooks] {inbox_id} {action}{suffix}")
            return
        if name == "live.objective.normalized":
            reason = str(event.get("reason") or "objective").replace("_", " ")
            self._emit(event, f"[run] normalized {reason}")
            hint = _truncate_text(event.get("previous_failure_hint") or "", limit=180)
            if hint:
                self._emit(event, f"[run] previous validation feedback: {hint}")
            return
        if name == "provider.build.started":
            model = str(event.get("model") or event.get("requested_model") or "").strip()
            if model:
                self._emit(event, f"[model] preparing provider: {model}")
            return
        if name == "live.website_build.started":
            root = _truncate_text(event.get("artifact_root") or "", limit=120)
            self._emit(event, f"[build] website lane started: {root}")
            return
        if name in {"live.generic_execution.started", "live.generic_build.started"}:
            root = _truncate_text(event.get("workspace_root") or "", limit=120)
            self._emit(event, f"[build] coding lane started: {root}")
            return
        if name == "model.requested":
            span_id = str(event.get("span_id") or event.get("model_call_id") or "")
            round_id = str(event.get("round") or "?")
            scope = self._scope(event.get("worker_id"))
            prefix = f"[{scope}][model]" if scope else "[model]"
            model = str(event.get("model") or "(unknown)")
            tools = int(event.get("tool_count") or 0)
            self._print_once(
                ("model.requested", span_id, round_id),
                event,
                f"{prefix} request round={round_id} model={model} tools={tools}",
            )
            return
        if name == "model.responded":
            span_id = str(event.get("span_id") or event.get("model_call_id") or "")
            round_id = str(event.get("round") or "?")
            scope = self._scope(event.get("worker_id"))
            prefix = f"[{scope}][model]" if scope else "[model]"
            finish = str(event.get("finish_reason") or "").strip()
            tool_calls = [
                str(item).strip()
                for item in (event.get("tool_calls") or [])
                if str(item).strip()
            ]
            details = [f"round={round_id}"]
            if finish:
                details.append(f"finish={finish}")
            if tool_calls:
                details.append("tools=" + ",".join(tool_calls[:4]))
            self._print_once(
                ("model.responded", span_id, round_id),
                event,
                f"{prefix} response {' '.join(details)}",
            )
            return
        if name == "tool.started":
            span_id = str(event.get("span_id") or event.get("tool_call_id") or "")
            tool_id = str(event.get("tool_id") or "tool")
            scope = self._scope(event.get("worker_id"))
            prefix = f"[{scope}][tool]" if scope else "[tool]"
            summary = _tool_request_summary(tool_id, dict(event.get("arguments") or {}))
            self._print_once(
                ("tool.started", span_id, tool_id),
                event,
                f"{prefix} {tool_id}: {summary}",
            )
            return
        if name == "tool.completed":
            span_id = str(event.get("span_id") or event.get("tool_call_id") or "")
            tool_id = str(event.get("tool_id") or "tool")
            status = str(event.get("status") or "completed")
            scope = self._scope(event.get("worker_id"))
            prefix = f"[{scope}][tool]" if scope else "[tool]"
            summary = _tool_result_summary(tool_id, event)
            label = "ok" if status == "completed" else "failed"
            self._print_once(
                ("tool.completed", span_id, tool_id),
                event,
                f"{prefix} {label} {tool_id}: {summary}",
            )
            return
        if name in {"tool.failed", "tool.denied"}:
            span_id = str(event.get("span_id") or event.get("tool_call_id") or "")
            tool_id = str(event.get("tool_id") or "tool")
            scope = self._scope(event.get("worker_id"))
            prefix = f"[{scope}][tool]" if scope else "[tool]"
            message = _truncate_text(event.get("error") or name, limit=180)
            self._print_once(
                (name, span_id, tool_id),
                event,
                f"{prefix} {name.split('.')[-1]} {tool_id}: {message}",
            )
            return
        if name == "toolloop.soft_budget_nudged":
            action = str(event.get("action") or "nudge")
            phase = str(event.get("phase") or "phase")
            self._emit(event, f"[status] soft budget: {action} ({phase})")
            return
        if name == "live.validation.started":
            self._emit(event, "[validation] started")
            return
        if name == "live.validation.model_completed":
            verdict = "passed" if event.get("passed") else "failed"
            self._emit(event, f"[validation] model {verdict}")
            return
        if name == "live.validation.completed":
            verdict = "passed" if event.get("passed") else "failed"
            score = _coerce_float(event.get("overall_score"))
            self._emit(event, f"[validation] {verdict} {score:.2f}")
            failures = [
                _truncate_text(item, limit=180)
                for item in (event.get("deterministic_failures") or [])
                if str(item).strip()
            ]
            for failure in failures[:2]:
                self._emit(event, f"[validation] gap: {failure}")
            return
        if name == "live.website_first_write_recovery.started":
            attempt = int(event.get("attempt") or 1)
            reason = _truncate_text(event.get("reason") or "no required files changed", limit=160)
            self._emit(event, f"[retry] first-write recovery {attempt} started: {reason}")
            return
        if name == "live.website_first_write_recovery.completed":
            attempt = int(event.get("attempt") or 1)
            status = str(event.get("status") or "completed")
            changed = [
                _path_basename(path)
                for path in (event.get("changed_required_files") or [])
                if str(path).strip()
            ]
            suffix = f" changed={','.join(changed)}" if changed else " changed=none"
            self._emit(event, f"[retry] first-write recovery {attempt} {status}{suffix}")
            return
        if name == "live.website_repair.started":
            attempt = int(event.get("attempt") or 1)
            reason = _truncate_text(event.get("reason") or "validation failed", limit=160)
            self._emit(event, f"[repair] attempt {attempt} started: {reason}")
            return
        if name == "live.website_repair.completed":
            attempt = int(event.get("attempt") or 1)
            status = str(event.get("status") or "completed")
            changed = [
                _path_basename(path)
                for path in (event.get("changed_required_files") or [])
                if str(path).strip()
            ]
            suffix = f" changed={','.join(changed)}" if changed else ""
            self._emit(event, f"[repair] attempt {attempt} {status}{suffix}")
            return
        if name in {
            "live.website_build.completed",
            "live.generic_execution.completed",
            "live.generic_build.completed",
        }:
            status = str(event.get("status") or "completed")
            self._emit(event, f"[build] {status}")
            return
        if name == "run.log.completed":
            status = str(event.get("status") or "completed")
            self._emit(event, f"[done] {status}")
            return
        if name == "run.log.failed":
            message = _truncate_text(event.get("error") or "failed", limit=180)
            self._emit(event, f"[done] failed: {message}")
            return
        if name == "super.heartbeat":
            phase = str(event.get("phase") or "running")
            detail = _truncate_text(event.get("detail") or "", limit=160)
            elapsed = int(event.get("elapsed_seconds") or 0)
            suffix = f": {detail}" if detail else ""
            self._emit(event, f"[status] still running {phase} ({elapsed}s idle){suffix}")


class SuperHeartbeatMonitor:
    """Emit sparse terminal/log heartbeats while live model calls are quiet."""

    def __init__(
        self,
        *,
        event_callback,
        enabled: bool,
        idle_seconds: float = 10.0,
        repeat_seconds: float = 15.0,
        poll_seconds: float = 2.0,
    ) -> None:
        self._event_callback = event_callback
        self._enabled = bool(enabled) and event_callback is not None
        self._idle_seconds = max(0.0, float(idle_seconds))
        self._repeat_seconds = max(self._idle_seconds, float(repeat_seconds), 0.0)
        self._poll_seconds = max(0.001, float(poll_seconds))
        self._last_activity = 0.0
        self._last_heartbeat = 0.0
        self._phase = "starting"
        self._worker_id = ""
        self._detail = ""
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    def observe(self, event: dict[str, Any]) -> None:
        if not self._enabled:
            return
        name = str(event.get("event") or "")
        if not name or name == "super.heartbeat":
            return
        loop = asyncio.get_running_loop()
        self._last_activity = loop.time()
        worker_id = str(event.get("worker_id") or "").strip()
        if worker_id:
            self._worker_id = worker_id
        if name == "model.requested":
            self._phase = "model"
            self._detail = (
                f"round={event.get('round', '?')} "
                f"model={event.get('model') or '(unknown)'} "
                f"tools={event.get('tool_count', 0)}"
            )
            return
        if name == "model.responded":
            self._phase = "model-response"
            self._detail = f"finish={event.get('finish_reason') or 'stop'}"
            return
        if name == "tool.started":
            self._phase = "tool"
            self._detail = _tool_request_summary(
                str(event.get("tool_id") or ""),
                dict(event.get("arguments") or {}),
            )
            return
        if name == "tool.completed":
            self._phase = "post-tool"
            self._detail = str(event.get("tool_id") or "tool")
            return
        if name == "live.validation.started":
            self._phase = "validation"
            self._detail = "read-only validator"
            return
        if name == "completion.completed":
            self._phase = "completion"
            self._detail = str(event.get("stop_reason") or "completed")
            return

    async def start(self) -> None:
        if not self._enabled or self._task is not None:
            return
        loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._last_activity = loop.time()
        self._last_heartbeat = 0.0
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        try:
            await self._task
        finally:
            self._task = None
            self._stop_event = None

    async def _run(self) -> None:
        stop_event = self._stop_event
        if stop_event is None:
            return
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_seconds)
                break
            except asyncio.TimeoutError:
                pass
            loop = asyncio.get_running_loop()
            now = loop.time()
            idle = now - self._last_activity
            since_last = now - self._last_heartbeat if self._last_heartbeat else float("inf")
            if idle < self._idle_seconds or since_last < self._repeat_seconds:
                continue
            self._last_heartbeat = now
            self._event_callback(
                {
                    "event": "super.heartbeat",
                    "phase": self._phase,
                    "detail": self._detail,
                    "worker_id": self._worker_id,
                    "elapsed_seconds": int(idle),
                }
            )


def _is_website_objective(
    report: SuperOrganismReport,
    args: argparse.Namespace | None = None,
) -> bool:
    choice = _super_live_choice(report, args)
    return choice.orchestrator_id == "super-dan-live-website"


def _super_live_choice(
    report: SuperOrganismReport,
    args: argparse.Namespace | None = None,
) -> OrchestratorChoice:
    context: dict[str, Any] = {
        "command": "super-organism",
        "execution_family": report.execution_family,
    }
    if (
        args is not None
        and bool(getattr(args, "_code_like_live", False))
        and _existing_website_workspace_context(args)
    ):
        context["existing_website_workspace"] = True
        context["workspace_kind"] = "website"
    return select_orchestrator(
        str(report.target or ""),
        context,
    )


def _supports_live_execution(
    report: SuperOrganismReport,
    args: argparse.Namespace | None = None,
) -> bool:
    choice = _super_live_choice(report, args)
    return choice.orchestrator_id in {"super-dan-live-website", "super-dan-live-coding"}


def _live_choice_tool_ids(choice: OrchestratorChoice) -> list[str]:
    return [str(tool_id) for tool_id in choice.tool_policy.get("allowed_tool_ids") or []]


def _live_choice_preferred_tool_ids(choice: OrchestratorChoice, fallback: Sequence[str]) -> list[str]:
    preferred = choice.tool_policy.get("preferred_tool_ids")
    if isinstance(preferred, (list, tuple)):
        return [str(tool_id) for tool_id in preferred]
    return list(fallback)


def _live_choice_read_only_tool_ids(choice: OrchestratorChoice) -> list[str]:
    read_only = {"list_directory", "file_read", "git_status", "git_diff", "git_log"}
    return [tool_id for tool_id in _live_choice_tool_ids(choice) if tool_id in read_only]


def _live_choice_required_files(choice: OrchestratorChoice) -> list[str]:
    files = choice.artifact_policy.get("required_files")
    if isinstance(files, (list, tuple)) and files:
        return [str(filename) for filename in files]
    return ["index.html", "styles.css", "app.js", "README.md"]


def _live_choice_existing_preferred_coordinated_files(choice: OrchestratorChoice) -> int:
    value = choice.artifact_policy.get(
        "existing_website_preferred_coordinated_files",
        choice.artifact_policy.get("existing_website_min_changed_files", 2),
    )
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return 2


def _live_choice_template_phrases(choice: OrchestratorChoice) -> list[str]:
    phrases = choice.acceptance_policy.get("template_phrases")
    if isinstance(phrases, (list, tuple)):
        return [str(phrase) for phrase in phrases]
    return []


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _has_any_word(text: str, words: Sequence[str]) -> bool:
    padded = f" {text} "
    return any(f" {word} " in padded for word in words)


def _vague_website_continuation_applies(
    objective: str,
    *,
    existing_website: bool,
) -> bool:
    text = _single_line(objective).lower()
    if not text:
        return False
    site_referents = ("website", "site", "page", "frontend", "landing page")
    has_site_referent = any(phrase in text for phrase in site_referents)
    if not has_site_referent and not existing_website:
        return False
    continuation_phrases = (
        "keep patching",
        "continue patching",
        "keep improving",
        "continue improving",
        "keep working",
        "keep going",
        "patch this",
        "patch it",
        "make it better",
        "improve it",
        "continue",
    )
    if not any(phrase in text for phrase in continuation_phrases):
        return False
    specific_words = (
        "add",
        "remove",
        "fix",
        "redesign",
        "rewrite",
        "animation",
        "responsive",
        "mobile",
        "copy",
        "color",
        "layout",
        "section",
        "component",
        "changelog",
        "readme",
        "docs",
        "button",
        "form",
        "pricing",
        "hero",
    )
    return not _has_any_word(text, specific_words)


def _latest_super_dan_failure_hint(
    workspace_root: Path,
    *,
    max_chars: int = 420,
) -> str:
    run_root = _super_run_root(workspace_root)
    if not run_root.exists():
        return ""
    event_logs = sorted(
        run_root.glob("turn-*/events.jsonl"),
        key=lambda path: path.parent.name,
        reverse=True,
    )
    for event_log in event_logs:
        try:
            lines = event_log.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            event = str(row.get("event") or "")
            failed = row.get("passed") is False or str(row.get("status") or "") == "failed"
            if event not in {"live.validation.completed", "run.log.completed"} or not failed:
                continue
            parts: list[str] = []
            deterministic_failures = row.get("deterministic_failures")
            if isinstance(deterministic_failures, list):
                parts.extend(str(item).strip() for item in deterministic_failures if str(item).strip())
            for key in ("error", "repair_brief", "comparison_note"):
                value = str(row.get(key) or "").strip()
                if value:
                    parts.append(value)
            hint = _single_line(" ".join(parts))
            if hint:
                return hint[:max_chars]
    return ""


def _live_website_objective_context(
    report: SuperOrganismReport,
    *,
    workspace_root: Path,
    required_paths: Sequence[Path],
) -> dict[str, Any]:
    original = _single_line(report.target)
    existing_required_files = [
        path.name for path in required_paths if path.exists()
    ]
    existing_website = "index.html" in existing_required_files or len(existing_required_files) >= 2
    previous_failure_hint = (
        _latest_super_dan_failure_hint(workspace_root) if existing_website else ""
    )
    if not _vague_website_continuation_applies(
        original,
        existing_website=existing_website,
    ):
        return {
            "normalized": False,
            "reason": "",
            "original_objective": original,
            "effective_objective": original,
            "existing_required_files": existing_required_files,
            "previous_failure_hint": previous_failure_hint,
        }
    default_brief = (
        "Continue patching this existing website with a concrete maintainability pass. "
        "Prefer coordinated, inspectable changes across index.html, styles.css, app.js, and README.md. "
        "Preserve the current site subject unless the existing files make it clearly obsolete. "
        "Add or improve modular sections, editable content blocks or a component map, a changelog or patch-notes area, "
        "and explicit patch/extension guidance so future edits are easy. "
        "Keep the patch bounded, static, and dependency-free."
    )
    if previous_failure_hint:
        default_brief = (
            f"{default_brief} Address this previous validation feedback: {previous_failure_hint}"
        )
    return {
        "normalized": True,
        "reason": "vague_website_continuation",
        "original_objective": original,
        "effective_objective": default_brief,
        "existing_required_files": existing_required_files,
        "previous_failure_hint": previous_failure_hint,
    }


def _argv_has_option(argv: Sequence[str], option: str) -> bool:
    return any(token == option or token.startswith(f"{option}=") for token in argv)


def _live_model_configured(requested_model: str | None) -> bool:
    if str(requested_model or "").strip():
        return True
    config = resolve_config()
    if str(config.get("model") or "").strip():
        return True
    engine_config = build_engine_config_from_env()
    fallback = str(engine_config.llm_default_model or "").strip()
    return bool(fallback and fallback != "stub-model")


def _should_implicit_live(report: SuperOrganismReport, args: argparse.Namespace) -> bool:
    if (
        bool(getattr(args, "live", False))
        or bool(getattr(args, "plan_only", False))
        or not str(getattr(args, "target", "") or "").strip()
        or not _supports_live_execution(report, args)
    ):
        return False
    if not (
        bool(getattr(args, "_stdin_is_tty", False))
        or bool(getattr(args, "_model_explicit", False))
    ):
        return False
    return _live_model_configured(getattr(args, "model", None))


def _should_materialize_website(report: SuperOrganismReport, args: argparse.Namespace) -> bool:
    if (
        bool(getattr(args, "plan_only", False))
        or bool(getattr(args, "json", False))
        or bool(getattr(args, "live", False))
    ):
        return False
    return _is_website_objective(report, args)


def _artifact_root(args: argparse.Namespace) -> Path:
    workspace = Path(str(args.workspace)).expanduser()
    artifact_dir = Path(str(args.artifact_dir)).expanduser()
    if artifact_dir.is_absolute():
        return artifact_dir
    return workspace / artifact_dir


def _materialize_website_artifact(report: SuperOrganismReport, args: argparse.Namespace) -> list[Path]:
    root = _artifact_root(args)
    root.mkdir(parents=True, exist_ok=True)
    files = {
        root / "index.html": _render_website_html(report),
        root / "styles.css": _render_website_css(),
        root / "app.js": _render_website_js(report),
        root / "README.md": _render_website_readme(report),
    }
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    return list(files)


def _resolve_live_model(requested_model: str | None) -> str:
    text = str(requested_model or "").strip()
    if text:
        return text
    config = resolve_config()
    env_model = str(config.get("model") or "").strip()
    if env_model:
        return env_model
    engine_config = build_engine_config_from_env()
    fallback = str(engine_config.llm_default_model or "").strip()
    if fallback and fallback != "stub-model":
        return fallback
    raise ValueError(
        "Super DAN live mode requires --model or a configured DAN_MODEL/DAN_LLM_MODEL"
    )


def _build_live_provider(
    model: str,
    *,
    api_key: str | None,
    base_url: str | None,
) -> LLMProvider:
    return live_gateway.build_gateway_backed_live_provider(
        model,
        api_key=api_key,
        base_url=base_url,
    )


def _existing_website_workspace_context(args: argparse.Namespace) -> bool:
    workspace_root = normalize_workspace_root(str(args.workspace))
    if workspace_root.name.lower() in {"website", "site", "web", "public", "dist"}:
        return True
    artifact_dir = Path(str(getattr(args, "artifact_dir", "website") or "website")).expanduser()
    candidate_roots = [workspace_root]
    if artifact_dir.is_absolute():
        candidate_roots.append(artifact_dir.resolve(strict=False))
    else:
        candidate_roots.append((workspace_root / artifact_dir).resolve(strict=False))
    for root in candidate_roots:
        if not (root / "index.html").exists():
            continue
        companion_count = sum(
            1
            for filename in ("styles.css", "app.js", "README.md")
            if (root / filename).exists()
        )
        if companion_count >= 1:
            return True
    return False


def _workspace_should_be_website_artifact_root(
    args: argparse.Namespace,
    choice: OrchestratorChoice,
) -> bool:
    if bool(getattr(args, "_artifact_dir_explicit", False)):
        return False
    if not bool(getattr(args, "_code_like_live", False)):
        return False
    artifact_dir = Path(str(args.artifact_dir or "")).expanduser()
    if artifact_dir.is_absolute() or artifact_dir.as_posix().strip("/") not in {
        "website",
        "",
    }:
        return False
    workspace_root = normalize_workspace_root(str(args.workspace))
    if workspace_root.name.lower() in {"website", "site", "web", "public", "dist"}:
        return True
    required_files = _live_choice_required_files(choice)
    existing = [
        filename for filename in required_files if (workspace_root / filename).exists()
    ]
    return "index.html" in existing or len(existing) >= 3


def _live_artifact_layout(
    args: argparse.Namespace,
    choice: OrchestratorChoice,
) -> tuple[Path, Path, list[str], list[Path]]:
    artifact_dir = Path(str(args.artifact_dir)).expanduser()
    required_files = _live_choice_required_files(choice)
    if _workspace_should_be_website_artifact_root(args, choice):
        workspace_root = normalize_workspace_root(str(args.workspace))
        artifact_root = workspace_root
        relative_files = list(required_files)
    elif artifact_dir.is_absolute():
        workspace_root = artifact_dir.resolve(strict=False)
        artifact_root = workspace_root
        relative_files = list(required_files)
    else:
        workspace_root = normalize_workspace_root(str(args.workspace))
        artifact_root = (workspace_root / artifact_dir).resolve(strict=False)
        relative_files = [(artifact_dir / filename).as_posix() for filename in required_files]
    required_paths = [(workspace_root / relative_path).resolve(strict=False) for relative_path in relative_files]
    return workspace_root, artifact_root, relative_files, required_paths


def _live_expected_return_shape() -> str:
    return json.dumps(
        {
            "candidate_id": "super-dan-live-website-001",
            "change_summary": ["short summary of concrete files written"],
            "target_files": ["website/index.html", "website/styles.css"],
            "test_plan": ["open the generated index.html in a browser"],
            "risks": ["remaining limitations or assumptions"],
            "files_created": ["website/index.html", "website/styles.css"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _live_validation_return_shape() -> str:
    return json.dumps(
        {
            "passed": True,
            "overall_score": 0.9,
            "dimension_scores": {
                "objective_alignment": 0.9,
                "artifact_specificity": 0.9,
                "execution_quality": 0.9,
            },
            "repair_brief": "",
            "missing_requirements": [],
            "comparison_note": "The result materially satisfies the operator objective and is not just a generic demo shell.",
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _super_report_evidence_blocks(report: SuperOrganismReport) -> list[dict[str, Any]]:
    return [
        {
            "label": f"Super DAN {report.cell_count}-cell contract",
            "content": report.final_memo,
            "source": "super_organism_report",
            "trust_label": "advisory",
        },
        {
            "label": "Delivery nodes",
            "content": json.dumps(
                [node.model_dump(mode="json") for node in report.delivery_plan],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "source": "super_organism_report",
            "trust_label": "advisory",
        },
        {
            "label": "Coordination tickets",
            "content": json.dumps(
                [ticket.model_dump(mode="json") for ticket in report.coordination_tickets],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "source": "super_organism_report",
            "trust_label": "advisory",
        },
        {
            "label": "Handoff packets",
            "content": json.dumps(
                [packet.model_dump(mode="json") for packet in report.handoff_packets],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "source": "super_organism_report",
            "trust_label": "advisory",
        },
        {
            "label": "Final audit gate",
            "content": json.dumps(
                (report.final_audit.model_dump(mode="json") if report.final_audit is not None else {}),
                ensure_ascii=False,
                sort_keys=True,
            ),
            "source": "super_organism_report",
            "trust_label": "advisory",
        },
    ]


def _request_from_live_brief(brief: WorkerBrief) -> ExecutionRequest:
    return request_from_brief(brief)


def _live_cell_from_brief(
    *,
    model: str,
    brief: WorkerBrief,
    worker_id: str,
    organism_stage: str,
) -> WorkerDefinition:
    worker = build_cell(model, brief.sampling_policy, brief.role.role_label)
    metadata = {
        **dict(worker.metadata),
        "worker_id": worker_id,
        "organism_stage": organism_stage,
        "brief_driven": True,
    }
    return worker.model_copy(update={"id": worker_id, "metadata": metadata})


def _live_website_task(
    report: SuperOrganismReport,
    *,
    artifact_root: Path,
    relative_files: Sequence[str],
    objective_context: Mapping[str, Any] | None = None,
) -> str:
    files = ", ".join(relative_files)
    context = dict(objective_context or {})
    effective_objective = str(context.get("effective_objective") or report.target).strip()
    original_objective = str(context.get("original_objective") or report.target).strip()
    normalized_note = ""
    if context.get("normalized"):
        normalized_note = (
            f"Original operator wording: {original_objective}. "
            "The request was vague continuation language, so apply the expanded default patch brief above. "
        )
    return (
        "Build the requested product website now. "
        f"Operator objective: {effective_objective}. "
        f"{normalized_note}"
        f"Artifact root: {artifact_root}. "
        f"Required relative files: {files}. "
        "Honor the supplied ticket ownership and handoff packets instead of freeforming a generic demo shell. "
        f"{_live_pacing_contract()} "
        "If the website files already exist, improve them incrementally instead of rewriting the whole site in one response. "
        "For an existing website patch, prefer coordinated edits when the objective spans structure, style, behavior, or docs; "
        "a focused single-file patch is acceptable when it fully satisfies the objective. "
        "Do not create extra scratch files outside the required artifact set. "
        "Make the website's actual product or subject clear. Preserve the existing site subject when patching an existing site. "
        "Do not pivot to a generic Super DAN execution-contract/demo site unless the operator explicitly asks for Super DAN. "
        "Actually create the files, then return the requested compact JSON-like completion summary."
    )


def _live_website_validation_task(
    report: SuperOrganismReport,
    *,
    artifact_root: Path,
    relative_files: Sequence[str],
    objective_context: Mapping[str, Any] | None = None,
) -> str:
    files = ", ".join(relative_files)
    context = dict(objective_context or {})
    effective_objective = str(context.get("effective_objective") or report.target).strip()
    original_objective = str(context.get("original_objective") or report.target).strip()
    normalized_note = ""
    if context.get("normalized"):
        normalized_note = (
            f"Original operator wording: {original_objective}. "
            "Validate against the expanded patch brief, not only the terse continuation wording. "
        )
    return (
        "Validate the materialized website now in read-only mode. "
        f"Operator objective: {effective_objective}. "
        f"{normalized_note}"
        f"Artifact root: {artifact_root}. "
        f"Required relative files: {files}. "
        "Inspect the generated files and decide whether the result is a real product website aligned with the objective, "
        "not just a generic Super DAN execution-contract demo shell. "
        "Allow a focused single-file patch when it materially satisfies the objective; require broader coverage only when "
        "the request or evidence truly spans multiple files."
    )


def _validation_repair_brief(
    validation: Mapping[str, Any],
    deterministic_failures: Sequence[str],
) -> str:
    parts: list[str] = []
    for key in ("repair_brief", "comparison_note", "error"):
        value = _display_text(validation.get(key) or "")
        if value:
            parts.append(value)
    missing = validation.get("missing_requirements")
    if isinstance(missing, list):
        parts.extend(_display_text(item) for item in missing if str(item).strip())
    parts.extend(_display_text(item) for item in deterministic_failures if str(item).strip())
    seen: set[str] = set()
    compact: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.add(part)
            compact.append(part)
    return " ".join(compact)


def _live_website_repair_task(
    report: SuperOrganismReport,
    *,
    artifact_root: Path,
    relative_files: Sequence[str],
    validation: Mapping[str, Any],
    deterministic_failures: Sequence[str],
    changed_required_paths: Sequence[str],
    objective_context: Mapping[str, Any] | None = None,
) -> str:
    files = ", ".join(relative_files)
    changed = ", ".join(_path_basename(path) for path in changed_required_paths) or "none"
    repair_brief = _validation_repair_brief(validation, deterministic_failures)
    context = dict(objective_context or {})
    effective_objective = str(context.get("effective_objective") or report.target).strip()
    return (
        "Repair the previous website patch now; do not stop with another summary-only response. "
        f"Operator objective: {effective_objective}. "
        f"Artifact root: {artifact_root}. "
        f"Required relative files: {files}. "
        f"Files already changed this run: {changed}. "
        f"Previous validation failure: {repair_brief or 'validator rejected the previous patch'}. "
        "Make concrete edits in the required files. If the feedback asks for broader coordination, update one or more "
        "required files that were not changed yet. For a maintainability patch, update the visible HTML, CSS guidance/tokens, "
        "JS module or patch notes behavior, and README patch instructions as needed. "
        "If the feedback lists Template phrase hits, remove or rename those exact hits in index.html; static validation "
        "passes only when fewer than 2 exact template hits remain. "
        "Keep the repair bounded, static, dependency-free, and inside the required artifact set. "
        "Actually write the repair with file_write or file_edit, then return the compact completion summary."
    )


def _live_website_first_write_recovery_task(
    report: SuperOrganismReport,
    *,
    artifact_root: Path,
    relative_files: Sequence[str],
    validation: Mapping[str, Any],
    deterministic_failures: Sequence[str],
    objective_context: Mapping[str, Any] | None = None,
) -> str:
    files = ", ".join(relative_files)
    recovery_brief = _validation_repair_brief(validation, deterministic_failures)
    context = dict(objective_context or {})
    effective_objective = str(context.get("effective_objective") or report.target).strip()
    return (
        "Recover from the previous website build pass now. The prior builder returned without changing any required "
        "website file, so this is not a validation repair; it is a first-write recovery. "
        f"Operator objective: {effective_objective}. "
        f"Artifact root: {artifact_root}. "
        f"Required relative files: {files}. "
        f"Failure reason: {recovery_brief or 'no required website files changed'}. "
        "Do not summarize. Make at least one concrete required-file edit with file_write or file_edit before finalizing. "
        "For an existing website, prefer a small coordinated patch that improves visible HTML plus styling or behavior. "
        "Keep the recovery bounded, static, dependency-free, and inside the required artifact set."
    )


def _live_generic_task(
    report: SuperOrganismReport,
    *,
    workspace_root: Path,
) -> str:
    return (
        "Execute the operator objective in the current workspace now. "
        f"Operator objective: {report.target}. "
        f"Workspace root: {workspace_root}. "
        "Honor the supplied ticket ownership and handoff packets instead of freeforming a generic build summary. "
        f"{_live_pacing_contract()} "
        "Inspect the existing project as needed, make a bounded implementation that materially advances the objective, "
        "and run focused verification if useful. Actually mutate workspace files before finalizing, then return the "
        "requested compact JSON-like completion summary."
    )


def _live_generic_validation_task(
    report: SuperOrganismReport,
    *,
    workspace_root: Path,
) -> str:
    return (
        "Validate the live implementation now in read-only mode. "
        f"Operator objective: {report.target}. "
        f"Workspace root: {workspace_root}. "
        "Inspect the mutated files and relevant read-only git evidence, then decide whether the result materially advances the objective."
    )


def _super_run_root(workspace_root: Path) -> Path:
    return workspace_root / ".dan-super" / "runs"


def _next_super_run_number(run_root: Path) -> int:
    highest = 0
    if run_root.exists():
        for child in run_root.iterdir():
            if not child.is_dir():
                continue
            name = child.name
            if not name.startswith("turn-"):
                continue
            try:
                highest = max(highest, int(name.split("-", 1)[1]))
            except (TypeError, ValueError):
                continue
    return highest + 1


def _build_super_run_workdir(workspace_root: Path) -> tuple[Path, int]:
    run_root = _super_run_root(workspace_root)
    run_root.mkdir(parents=True, exist_ok=True)
    turn_number = _next_super_run_number(run_root)
    workdir = run_root / f"turn-{turn_number:02d}"
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir, turn_number


def _super_session_id(workspace_root: Path) -> str:
    digest = hashlib.sha1(
        str(workspace_root.resolve(strict=False)).encode("utf-8")
    ).hexdigest()[:12]
    return f"super-dan:{digest}"


def _log_live_event(
    logger: SuperRunEventLogger | None,
    event: str,
    **payload: Any,
) -> None:
    if logger is None:
        return
    logger.emit({"event": event, **payload})


async def _execute_live_request(
    *,
    worker: WorkerDefinition,
    request: ExecutionRequest,
    tool_ids: Sequence[str],
    workspace_root: Path,
    args: argparse.Namespace,
    model: str,
    provider: LLMProvider,
    event_logger: SuperRunEventLogger | None = None,
) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    heartbeat_monitor: SuperHeartbeatMonitor | None = None

    def record_event(event: dict[str, Any]) -> None:
        payload = dict(event)
        events.append(payload)
        if event_logger is None:
            if heartbeat_monitor is not None:
                heartbeat_monitor.observe(payload)
            return
        trace_row = payload.get("trace_row")
        if (
            str(payload.get("event") or "").strip() == "trace.row"
            and isinstance(trace_row, dict)
        ):
            event_logger.emit_trace_rows([trace_row])
            if heartbeat_monitor is not None:
                heartbeat_monitor.observe(dict(trace_row))
            return
        event_logger.emit(payload)
        if heartbeat_monitor is not None:
            heartbeat_monitor.observe(payload)

    tool_runtime = LocalOrganismToolRuntime(
        tool_ids=list(tool_ids),
        workspace_root=workspace_root,
        event_callback=record_event,
    )
    completion_provider = ToolLoopCompletionProvider(
        provider=provider,
        tool_runtime=tool_runtime,
        default_model=model,
        max_rounds=int(args.max_tool_rounds),
        max_tool_calls=int(args.max_tool_calls),
        event_callback=record_event,
    )
    executor = WorkerCoreExecutor(
        completion_provider=completion_provider,
        event_sink=CallbackEventSink(record_event),
    )
    heartbeat_monitor = SuperHeartbeatMonitor(
        event_callback=record_event,
        enabled=event_logger is not None,
    )
    await heartbeat_monitor.start()
    try:
        result = await executor.execute(worker, request)
    finally:
        await heartbeat_monitor.stop()
    raw_response = result.metadata.get("raw_response")
    executed_tools = (
        list(raw_response.get("executed_tools") or [])
        if isinstance(raw_response, dict)
        else []
    )
    return result, executed_tools, events


def _file_digest(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except Exception:
        return "__unreadable__"
    return hashlib.sha256(data).hexdigest()


def _snapshot_file_state(paths: Sequence[Path]) -> dict[str, str | None]:
    return {
        str(path.resolve(strict=False)): _file_digest(path)
        for path in paths
    }


def _changed_paths_from_snapshot(
    snapshot: dict[str, str | None],
    paths: Sequence[Path],
) -> list[str]:
    changed: list[str] = []
    seen: set[str] = set()
    for path in paths:
        rendered = str(path.resolve(strict=False))
        if rendered in seen:
            continue
        seen.add(rendered)
        if snapshot.get(rendered) != _file_digest(path):
            changed.append(rendered)
    return changed


def _coerce_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _normalize_token_usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, value in raw.items():
        try:
            normalized[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    prompt = int(raw.get("prompt_tokens", raw.get("prompt", 0)) or 0)
    completion = int(raw.get("completion_tokens", raw.get("completion", 0)) or 0)
    total = int(raw.get("total_tokens", prompt + completion) or (prompt + completion))
    normalized["prompt_tokens"] = prompt
    normalized["completion_tokens"] = completion
    normalized["total_tokens"] = total
    return normalized


def _merge_token_usage(*usage_maps: Any) -> dict[str, int]:
    merged: dict[str, int] = {}
    for usage_map in usage_maps:
        for key, value in _normalize_token_usage(usage_map).items():
            merged[key] = int(merged.get(key, 0) or 0) + int(value)
    return merged


def _format_token_usage(raw: Any) -> str:
    usage = _normalize_token_usage(raw)
    if not usage:
        return "unavailable"
    parts = [
        f"prompt={usage.get('prompt_tokens', 0)}",
        f"completion={usage.get('completion_tokens', 0)}",
        f"total={usage.get('total_tokens', 0)}",
    ]
    if usage.get("cached_input_tokens"):
        parts.append(f"cached_input={usage['cached_input_tokens']}")
    if usage.get("cache_write_tokens"):
        parts.append(f"cache_write={usage['cache_write_tokens']}")
    return ", ".join(parts)


def _extract_completion_usage_from_raw(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    usage_totals = raw.get("usage_totals")
    if usage_totals is not None:
        return _normalize_token_usage(usage_totals)
    provider_result = raw.get("provider_result")
    if isinstance(provider_result, dict):
        return _normalize_token_usage(provider_result.get("usage"))
    return {}


def _extract_execution_usage(result: Any) -> dict[str, int]:
    metadata = dict(getattr(result, "metadata", {}) or {})
    return _merge_token_usage(
        _extract_completion_usage_from_raw(metadata.get("raw_response")),
        _extract_completion_usage_from_raw(metadata.get("initial_raw_response")),
        _extract_completion_usage_from_raw(metadata.get("repair_raw_response")),
    )


def _failed_validation_payload(
    *,
    reason: str,
    missing_requirements: Sequence[str] | None = None,
) -> dict[str, Any]:
    items = [str(item).strip() for item in (missing_requirements or []) if str(item).strip()]
    if not items:
        items = [reason]
    return {
        "passed": False,
        "overall_score": 0.0,
        "dimension_scores": {},
        "repair_brief": reason,
        "missing_requirements": items,
        "comparison_note": reason,
    }


def _normalize_validation_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _failed_validation_payload(
            reason="validator did not return a structured validation payload"
        )
    dimension_scores = payload.get("dimension_scores")
    missing_requirements = payload.get("missing_requirements")
    return {
        "passed": bool(payload.get("passed")),
        "overall_score": _coerce_float(payload.get("overall_score")),
        "dimension_scores": dict(dimension_scores) if isinstance(dimension_scores, dict) else {},
        "repair_brief": str(payload.get("repair_brief") or "").strip(),
        "missing_requirements": (
            [str(item).strip() for item in missing_requirements if str(item).strip()]
            if isinstance(missing_requirements, list)
            else []
        ),
        "comparison_note": str(payload.get("comparison_note") or "").strip(),
    }


def _extract_validation_payload(outputs: Any) -> Any:
    if not isinstance(outputs, dict):
        return outputs
    if any(key in outputs for key in ("passed", "overall_score", "missing_requirements")):
        return outputs
    for key in ("result", "text"):
        raw = outputs.get(key)
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text:
            continue
        try:
            decoded = json.loads(text)
        except Exception:
            continue
        if isinstance(decoded, dict):
            return decoded
    return outputs


def _merge_validation_failures(
    validation: dict[str, Any],
    failures: Sequence[str],
) -> dict[str, Any]:
    issues = [str(item).strip() for item in failures if str(item).strip()]
    if not issues:
        return dict(validation)
    merged = _normalize_validation_payload(validation)
    existing = list(merged.get("missing_requirements") or [])
    for issue in issues:
        if issue not in existing:
            existing.append(issue)
    merged["passed"] = False
    merged["overall_score"] = min(_coerce_float(merged.get("overall_score")), 0.49)
    merged["missing_requirements"] = existing
    if not str(merged.get("repair_brief") or "").strip():
        merged["repair_brief"] = issues[0]
    if not str(merged.get("comparison_note") or "").strip():
        merged["comparison_note"] = issues[0]
    return merged


def _existing_required_files_from_snapshot(
    snapshot: dict[str, str | None],
    required_paths: Sequence[Path],
) -> list[str]:
    existing: list[str] = []
    for path in required_paths:
        rendered = str(path.resolve(strict=False))
        if snapshot.get(rendered) is not None:
            existing.append(rendered)
    return existing


def _website_static_validation_failures(
    report: SuperOrganismReport,
    *,
    required_paths: Sequence[Path],
    changed_required_paths: Sequence[str],
    template_phrases: Sequence[str],
) -> list[str]:
    failures: list[str] = []
    if not changed_required_paths:
        failures.append("The live run did not change any required website files.")
    if not required_paths:
        return failures
    index_path = Path(required_paths[0])
    if not index_path.exists():
        return failures
    try:
        html = " ".join(index_path.read_text(encoding="utf-8", errors="ignore").lower().split())
    except Exception as exc:
        failures.append(f"Failed to inspect {index_path}: {type(exc).__name__}: {exc}")
        return failures
    target = " ".join(str(report.target or "").lower().split())
    if target and len(target) >= 24 and target in html:
        failures.append("The generated website still echoes the raw operator prompt as page copy.")
    template_hits = _website_template_phrase_hits(html, template_phrases)
    if len(template_hits) >= 2:
        failures.append(
            "The generated website still looks like the generic Super DAN contract/demo template. "
            f"Template phrase hits in index.html: {_display_template_phrase_hits(template_hits)}. "
            "Remove or rename enough exact hits so fewer than 2 remain."
        )
    return failures


def _website_template_phrase_hits(
    normalized_html: str,
    template_phrases: Sequence[str],
) -> list[str]:
    hits: list[str] = []
    seen: set[str] = set()
    for phrase in template_phrases:
        display = " ".join(str(phrase or "").split())
        normalized = display.lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if normalized in normalized_html:
            hits.append(display)
    return hits


def _display_template_phrase_hits(hits: Sequence[str], *, limit: int = 8) -> str:
    visible = [f"'{hit}'" for hit in hits[:limit]]
    if len(hits) > limit:
        visible.append(f"+{len(hits) - limit} more")
    return ", ".join(visible)


def _log_final_validation_event(
    event_logger: SuperRunEventLogger | None,
    *,
    worker_id: str,
    model: str,
    validation: dict[str, Any],
    deterministic_failures: Sequence[str] | None = None,
    changed_required_files: Sequence[str] | None = None,
    first_write_recovery_attempted: bool = False,
    repair_attempted: bool = False,
    repair_exhausted: bool = False,
) -> None:
    _log_live_event(
        event_logger,
        "live.validation.completed",
        worker_id=worker_id,
        model=model,
        status=validation.get("status"),
        passed=validation.get("passed"),
        overall_score=validation.get("overall_score"),
        deterministic_failures=list(deterministic_failures or []) or None,
        changed_required_files=list(changed_required_files or []),
        first_write_recovery_attempted=bool(first_write_recovery_attempted),
        repair_attempted=bool(repair_attempted),
        repair_exhausted=bool(repair_exhausted),
        tool_calls=int(validation.get("tool_calls") or 0),
        event_count=int(validation.get("event_count") or 0),
    )


async def _run_live_validation(
    *,
    worker: WorkerDefinition,
    request: ExecutionRequest,
    tool_ids: Sequence[str],
    workspace_root: Path,
    args: argparse.Namespace,
    model: str,
    provider: LLMProvider,
    event_logger: SuperRunEventLogger | None = None,
) -> dict[str, Any]:
    _log_live_event(
        event_logger,
        "live.validation.started",
        worker_id=worker.id,
        model=model,
        tool_ids=list(tool_ids),
    )
    result, executed_tools, events = await _execute_live_request(
        worker=worker,
        request=request,
        tool_ids=tool_ids,
        workspace_root=workspace_root,
        args=args,
        model=model,
        provider=provider,
        event_logger=event_logger,
    )
    token_usage = _extract_execution_usage(result)
    validation = _normalize_validation_payload(
        _extract_validation_payload(dict(result.outputs))
    )
    if result.status != "completed":
        validation = _merge_validation_failures(
            validation,
            [result.error or "validator did not complete successfully"],
        )
    validation["status"] = result.status
    validation["tool_calls"] = len(executed_tools)
    validation["event_count"] = len(events)
    validation["error"] = result.error
    validation["token_usage"] = token_usage
    _log_live_event(
        event_logger,
        "live.validation.model_completed",
        worker_id=worker.id,
        model=model,
        status=validation.get("status"),
        passed=validation.get("passed"),
        tool_calls=len(executed_tools),
        event_count=len(events),
    )
    return validation


async def _run_live_website_build(
    report: SuperOrganismReport,
    args: argparse.Namespace,
    *,
    model: str,
    provider: LLMProvider,
    run_trace_id: str,
    run_task_id: str,
    event_logger: SuperRunEventLogger | None = None,
) -> dict[str, Any]:
    choice = _super_live_choice(report, args)
    website_tool_ids = _live_choice_tool_ids(choice)
    website_preferred_tool_ids = _live_choice_preferred_tool_ids(
        choice,
        ["file_write", "file_edit", "file_read", "list_directory"],
    )
    website_read_only_tool_ids = _live_choice_read_only_tool_ids(choice)
    existing_preferred_coordinated_files = _live_choice_existing_preferred_coordinated_files(choice)
    template_phrases = _live_choice_template_phrases(choice)
    pacing_policy = _live_pacing_policy(forbid_scratch_files=True)
    worker_id = "super-dan.live.website-builder"
    workspace_root, artifact_root, relative_files, required_paths = _live_artifact_layout(args, choice)
    workspace_root.mkdir(parents=True, exist_ok=True)
    file_snapshot = _snapshot_file_state(required_paths)
    objective_context = _live_website_objective_context(
        report,
        workspace_root=workspace_root,
        required_paths=required_paths,
    )
    if objective_context.get("normalized"):
        _log_live_event(
            event_logger,
            "live.objective.normalized",
            reason=objective_context.get("reason"),
            original_objective=objective_context.get("original_objective"),
            effective_objective=objective_context.get("effective_objective"),
            previous_failure_hint=objective_context.get("previous_failure_hint") or None,
            existing_required_files=list(objective_context.get("existing_required_files") or []),
        )
    worker_brief = coding_brief(
            role=RoleSpec(
                role_label="coding_worker",
                responsibility="Build the requested Super DAN static website artifact.",
                success_criteria=[
                    "All required website files exist.",
                    "Existing websites receive material required-file changes; multi-file coordination is preferred for broad patches.",
                    "The result is inspectable by opening index.html directly.",
                ],
                artifact_targets=list(relative_files),
                trace_role="super-dan.live.website-builder",
            ),
            task=_live_website_task(
                report,
                artifact_root=artifact_root,
                relative_files=relative_files,
                objective_context=objective_context,
            ),
            scope=f"workspace={workspace_root}; artifact_root={artifact_root}; native Super DAN live website build",
            hard_constraints=[
                "Actually create or update all required website files with file_write or file_edit.",
                "Do not invoke the separate DAN Code or DAN Research product shells.",
                "Keep writes inside the requested workspace/artifact paths.",
                "Do not install dependencies or require a build step.",
                "Do not create scratch or throwaway files outside the required website artifact set.",
                "For existing websites, materially change at least one required file; prefer broader coordinated edits when the requested patch naturally spans files.",
            ],
            soft_constraints=[
                "Build a polished static product website with distinctive layout, motion, and concise copy.",
                "Treat the supplied coordination tickets as the working backlog and satisfy the final audit gate.",
                "If the required files already exist, improve them incrementally instead of replacing everything at once.",
                "If this is an existing website redesign, update HTML structure plus CSS visual language and/or JS motion.",
                "After one failed or truncated large write, immediately switch to a smaller section-level strategy.",
                "Avoid rereading the same file unless the next edit truly needs exact line grounding.",
                "Use local file tools for every required file before finalizing.",
                "Keep dependencies zero; no package install, no external CDN requirement.",
            ],
            pacing_policy=pacing_policy,
            tool_policy={
                "allowed_tool_ids": list(website_tool_ids),
                "preferred_tool_ids": list(website_preferred_tool_ids),
                "max_tool_calls": int(args.max_tool_calls),
            },
            output_contract=OutputContract(
                definition_of_done=(
                    "All required files exist on disk and the final response names the files created, "
                    "a concise validation plan, and any remaining risks."
                ),
                expected_return_shape=_live_expected_return_shape(),
            ),
            sampling_policy={"profile": choice.sampling_policy, "temperature": 0.35, "max_tokens": 2800},
            evidence=_super_report_evidence_blocks(report),
            input_payload={
                "objective": objective_context.get("effective_objective") or report.target,
                "original_objective": report.target,
                "objective_normalization": dict(objective_context),
                "workspace_root": str(workspace_root),
                "artifact_root": str(artifact_root),
                "artifact_files": list(relative_files),
                "organism_id": report.organism_id,
                "cell_count": report.cell_count,
                "active_cell_cap": report.active_cell_cap,
                "organ_counts": dict(report.organ_counts),
                "delivery_plan": [node.model_dump(mode="json") for node in report.delivery_plan],
                "write_pacing": dict(pacing_policy),
                "existing_required_files": _existing_required_files_from_snapshot(
                    file_snapshot,
                    required_paths,
                ),
                "existing_website_change_policy": {
                    "minimum_required_changed_files": 1,
                    "preferred_coordinated_required_files": existing_preferred_coordinated_files,
                    "prefer_coordinated_when_objective_spans_files": True,
                },
                "shared_board": (
                    report.shared_board.model_dump(mode="json") if report.shared_board is not None else None
                ),
                "coordination_tickets": [
                    ticket.model_dump(mode="json") for ticket in report.coordination_tickets
                ],
                "handoff_packets": [packet.model_dump(mode="json") for packet in report.handoff_packets],
                "final_audit": (
                    report.final_audit.model_dump(mode="json") if report.final_audit is not None else None
                ),
            },
            metadata={
                "surface": "super_organism",
                "mode": "live",
                "tool_budget_profile": "super_dan_live",
                "trace_id": run_trace_id,
                "root_task_id": run_task_id,
                "organism_id": report.organism_id,
                "organ_id": "super-dan.live.website",
                "organism_stage": "execution",
                "worker_id": worker_id,
            },
        )
    worker = _live_cell_from_brief(
        model=model,
        brief=worker_brief,
        worker_id=worker_id,
        organism_stage="execution",
    )
    request = _request_from_live_brief(worker_brief)
    _log_live_event(
        event_logger,
        "live.website_build.started",
        model=model,
        workspace_root=str(workspace_root),
        artifact_root=str(artifact_root),
        tool_ids=list(website_tool_ids),
    )
    result, executed_tools, events = await _execute_live_request(
        worker=worker,
        request=request,
        tool_ids=website_tool_ids,
        workspace_root=workspace_root,
        args=args,
        model=model,
        provider=provider,
        event_logger=event_logger,
    )
    _log_live_event(
        event_logger,
        "live.website_build.completed",
        model=model,
        status=result.status,
        tool_calls=len(executed_tools),
        event_count=len(events),
    )
    build_token_usage = _extract_execution_usage(result)
    existing_paths = [path for path in required_paths if path.exists()]
    missing_paths = [path for path in required_paths if not path.exists()]
    changed_required_paths = _changed_paths_from_snapshot(file_snapshot, required_paths)
    mutated_paths = _mutation_paths_from_tools(executed_tools, workspace_root=workspace_root)
    error = result.error
    if missing_paths and not error:
        error = (
            "live build finished without creating required files: "
            + ", ".join(str(path) for path in missing_paths)
        )
    validation = _failed_validation_payload(
        reason=error or "live website build did not meet the exit contract",
        missing_requirements=[str(path) for path in missing_paths],
    )

    async def run_website_validator(
        current_changed_required_paths: Sequence[str],
    ) -> dict[str, Any]:
        validator_worker_id = "super-dan.live.website.validator"
        validator_brief = review_brief(
            role=RoleSpec(
                role_label="validator_website",
                responsibility="Validate the Super DAN website artifact in read-only mode.",
                success_criteria=[
                    "Required files were read directly.",
                    "The artifact materially satisfies the operator objective.",
                    "The result is not a generic Super DAN execution-contract demo.",
                ],
                artifact_targets=list(relative_files),
                trace_role="super-dan.live.website.validator",
            ),
            task=_live_website_validation_task(
                report,
                artifact_root=artifact_root,
                relative_files=relative_files,
                objective_context=objective_context,
            ),
            scope=f"workspace={workspace_root}; artifact_root={artifact_root}; native Super DAN website validation",
            hard_constraints=[
                "Read-only validation only; do not write or edit files.",
                "Inspect the required website files directly before deciding.",
                "Fail if the result is still a generic execution-contract/demo template.",
                "Fail if the page mostly echoes the raw operator prompt.",
            ],
            soft_constraints=[
                "Prefer concrete missing requirements over vague criticism.",
                "Judge objective alignment, not just file existence.",
            ],
            allowed_tool_ids=website_read_only_tool_ids,
            failure_phrases=template_phrases,
            tool_policy={
                "allowed_tool_ids": list(website_read_only_tool_ids),
                "preferred_tool_ids": ["file_read", "list_directory"],
                "max_tool_calls": max(4, min(int(args.max_tool_calls), 12)),
            },
            sampling_policy={"profile": "deterministic", "temperature": 0.0, "max_tokens": 1600},
            output_contract=OutputContract(
                definition_of_done="Return the validation report only.",
                expected_return_shape=_live_validation_return_shape(),
            ),
            input_payload={
                "objective": objective_context.get("effective_objective") or report.target,
                "original_objective": report.target,
                "objective_normalization": dict(objective_context),
                "workspace_root": str(workspace_root),
                "artifact_root": str(artifact_root),
                "artifact_files": list(relative_files),
                "required_files": [str(path) for path in required_paths],
                "changed_required_files": list(current_changed_required_paths),
            },
            metadata={
                "surface": "super_organism",
                "mode": "live",
                "tool_budget_profile": "super_dan_live",
                "trace_id": run_trace_id,
                "root_task_id": run_task_id,
                "organism_id": report.organism_id,
                "organ_id": "super-dan.live.website",
                "worker_id": validator_worker_id,
                "organism_stage": "validation",
            },
        )
        validator_worker = _live_cell_from_brief(
            model=model,
            brief=validator_brief,
            worker_id=validator_worker_id,
            organism_stage="validation",
        )
        validator_request = _request_from_live_brief(validator_brief)
        return await _run_live_validation(
            worker=validator_worker,
            request=validator_request,
            tool_ids=website_read_only_tool_ids,
            workspace_root=workspace_root,
            args=args,
            model=model,
            provider=provider,
            event_logger=event_logger,
        )

    if not missing_paths and changed_required_paths:
        validation = await run_website_validator(changed_required_paths)
    static_validation_failures = _website_static_validation_failures(
        report,
        required_paths=required_paths,
        changed_required_paths=changed_required_paths,
        template_phrases=template_phrases,
    )
    validation = _merge_validation_failures(
        validation,
        static_validation_failures,
    )
    validation["deterministic_failures"] = list(static_validation_failures)
    validation_tool_calls_total = int(validation.get("tool_calls") or 0)
    validation_event_count_total = int(validation.get("event_count") or 0)
    validation_token_usage = _merge_token_usage(validation.get("token_usage"))
    first_write_recovery_attempts = 0
    if result.status == "completed" and not changed_required_paths:
        first_write_recovery_attempts = 1
        recovery_reason = _validation_repair_brief(validation, static_validation_failures)
        _log_live_event(
            event_logger,
            "live.website_first_write_recovery.started",
            attempt=first_write_recovery_attempts,
            model=model,
            reason=recovery_reason or "no required website files changed",
            changed_required_files=list(changed_required_paths),
        )
        recovery_worker_id = "super-dan.live.website-first-write-recovery"
        recovery_brief = coding_brief(
            role=RoleSpec(
                role_label="coding_worker",
                responsibility="Recover a Super DAN website build that produced no required-file edits.",
                success_criteria=[
                    "At least one required website file is concretely edited.",
                    "The recovery directly advances the operator objective.",
                    "The result remains static and dependency-free.",
                ],
                artifact_targets=list(relative_files),
                trace_role="super-dan.live.website-first-write-recovery",
            ),
            task=_live_website_first_write_recovery_task(
                report,
                artifact_root=artifact_root,
                relative_files=relative_files,
                validation=validation,
                deterministic_failures=static_validation_failures,
                objective_context=objective_context,
            ),
            scope=f"workspace={workspace_root}; artifact_root={artifact_root}; Super DAN website first-write recovery",
            hard_constraints=[
                "Actually edit at least one required website file; do not return a summary-only response.",
                "Keep writes inside the requested workspace/artifact paths.",
                "Do not install dependencies or require a build step.",
                "Do not create scratch or throwaway files outside the required website artifact set.",
            ],
            soft_constraints=[
                "Prefer a small, visible edit over another broad analysis pass.",
                "Use file_edit for existing files when exact grounding is available; use bounded file_write when creating a missing required file.",
                "Avoid rereading unchanged context unless exact edit grounding is needed.",
            ],
            pacing_policy=pacing_policy,
            tool_policy={
                "allowed_tool_ids": list(website_tool_ids),
                "preferred_tool_ids": list(website_preferred_tool_ids),
                "max_tool_calls": int(args.max_tool_calls),
            },
            output_contract=OutputContract(
                definition_of_done=(
                    "A concrete required-file edit has been made and the final response names changed files, "
                    "validation plan, and remaining risks."
                ),
                expected_return_shape=_live_expected_return_shape(),
            ),
            sampling_policy={"profile": choice.sampling_policy, "temperature": 0.2, "max_tokens": 2200},
            evidence=_super_report_evidence_blocks(report),
            input_payload={
                "objective": objective_context.get("effective_objective") or report.target,
                "original_objective": report.target,
                "objective_normalization": dict(objective_context),
                "workspace_root": str(workspace_root),
                "artifact_root": str(artifact_root),
                "artifact_files": list(relative_files),
                "required_files": [str(path) for path in required_paths],
                "changed_required_files": list(changed_required_paths),
                "validation": dict(validation),
                "deterministic_failures": list(static_validation_failures),
                "write_pacing": dict(pacing_policy),
                "existing_required_files": _existing_required_files_from_snapshot(
                    file_snapshot,
                    required_paths,
                ),
                "existing_website_change_policy": {
                    "minimum_required_changed_files": 1,
                    "preferred_coordinated_required_files": existing_preferred_coordinated_files,
                    "prefer_coordinated_when_objective_spans_files": True,
                },
            },
            metadata={
                "surface": "super_organism",
                "mode": "live",
                "tool_budget_profile": "super_dan_live",
                "trace_id": run_trace_id,
                "root_task_id": run_task_id,
                "organism_id": report.organism_id,
                "organ_id": "super-dan.live.website",
                "organism_stage": "execution",
                "worker_id": recovery_worker_id,
            },
        )
        recovery_worker = _live_cell_from_brief(
            model=model,
            brief=recovery_brief,
            worker_id=recovery_worker_id,
            organism_stage="execution",
        )
        recovery_result, recovery_tools, recovery_events = await _execute_live_request(
            worker=recovery_worker,
            request=_request_from_live_brief(recovery_brief),
            tool_ids=website_tool_ids,
            workspace_root=workspace_root,
            args=args,
            model=model,
            provider=provider,
            event_logger=event_logger,
        )
        result = recovery_result
        executed_tools.extend(recovery_tools)
        events.extend(recovery_events)
        build_token_usage = _merge_token_usage(
            build_token_usage,
            _extract_execution_usage(recovery_result),
        )
        error = recovery_result.error
        existing_paths = [path for path in required_paths if path.exists()]
        missing_paths = [path for path in required_paths if not path.exists()]
        changed_required_paths = _changed_paths_from_snapshot(file_snapshot, required_paths)
        mutated_paths = _mutation_paths_from_tools(executed_tools, workspace_root=workspace_root)
        _log_live_event(
            event_logger,
            "live.website_first_write_recovery.completed",
            attempt=first_write_recovery_attempts,
            model=model,
            status=recovery_result.status,
            tool_calls=len(recovery_tools),
            event_count=len(recovery_events),
            changed_required_files=list(changed_required_paths),
        )
        validation = _failed_validation_payload(
            reason=error or "live website first-write recovery did not meet the exit contract",
            missing_requirements=[str(path) for path in missing_paths],
        )
        if not missing_paths and changed_required_paths:
            validation = await run_website_validator(changed_required_paths)
        static_validation_failures = _website_static_validation_failures(
            report,
            required_paths=required_paths,
            changed_required_paths=changed_required_paths,
            template_phrases=template_phrases,
        )
        validation = _merge_validation_failures(
            validation,
            static_validation_failures,
        )
        validation["deterministic_failures"] = list(static_validation_failures)
        validation_tool_calls_total += int(validation.get("tool_calls") or 0)
        validation_event_count_total += int(validation.get("event_count") or 0)
        validation_token_usage = _merge_token_usage(
            validation_token_usage,
            validation.get("token_usage"),
        )
    repair_attempts = 0
    if (
        result.status == "completed"
        and not missing_paths
        and bool(changed_required_paths)
        and not bool(validation.get("passed"))
    ):
        repair_attempts = 1
        repair_reason = _validation_repair_brief(validation, static_validation_failures)
        _log_live_event(
            event_logger,
            "live.website_repair.started",
            attempt=repair_attempts,
            model=model,
            reason=repair_reason or "validation failed",
            changed_required_files=list(changed_required_paths),
        )
        repair_worker_id = "super-dan.live.website-repair"
        repair_brief = coding_brief(
            role=RoleSpec(
                role_label="coding_worker",
                responsibility="Repair the Super DAN website artifact after validation failure.",
                success_criteria=[
                    "Validation feedback is addressed with concrete file edits.",
                    "The repair changes required website files that directly address the failed validation.",
                    "The result remains static and dependency-free.",
                ],
                artifact_targets=list(relative_files),
                trace_role="super-dan.live.website-repair",
            ),
            task=_live_website_repair_task(
                report,
                artifact_root=artifact_root,
                relative_files=relative_files,
                validation=validation,
                deterministic_failures=static_validation_failures,
                changed_required_paths=changed_required_paths,
                objective_context=objective_context,
            ),
            scope=f"workspace={workspace_root}; artifact_root={artifact_root}; Super DAN website validation repair",
            hard_constraints=[
                "Actually edit required website files; do not return a summary-only response.",
                "Keep writes inside the requested workspace/artifact paths.",
                "Do not install dependencies or require a build step.",
                "Do not create scratch or throwaway files outside the required website artifact set.",
                "Change additional required files only when the validation feedback or objective calls for broader coverage.",
            ],
            soft_constraints=[
                "Prefer small targeted edits over rewriting the whole site.",
                "Prioritize required files that have not changed yet.",
                "Address validator feedback directly before polishing unrelated details.",
                "Avoid rereading unchanged context unless exact edit grounding is needed.",
            ],
            pacing_policy=pacing_policy,
            tool_policy={
                "allowed_tool_ids": list(website_tool_ids),
                "preferred_tool_ids": list(website_preferred_tool_ids),
                "max_tool_calls": int(args.max_tool_calls),
            },
            output_contract=OutputContract(
                definition_of_done=(
                    "Validation feedback is addressed with concrete required-file edits and the final response names "
                    "changed files, validation plan, and remaining risks."
                ),
                expected_return_shape=_live_expected_return_shape(),
            ),
            sampling_policy={"profile": choice.sampling_policy, "temperature": 0.25, "max_tokens": 2400},
            evidence=_super_report_evidence_blocks(report),
            input_payload={
                "objective": objective_context.get("effective_objective") or report.target,
                "original_objective": report.target,
                "objective_normalization": dict(objective_context),
                "workspace_root": str(workspace_root),
                "artifact_root": str(artifact_root),
                "artifact_files": list(relative_files),
                "required_files": [str(path) for path in required_paths],
                "changed_required_files": list(changed_required_paths),
                "validation": dict(validation),
                "deterministic_failures": list(static_validation_failures),
                "write_pacing": dict(pacing_policy),
                "existing_required_files": _existing_required_files_from_snapshot(
                    file_snapshot,
                    required_paths,
                ),
                "existing_website_change_policy": {
                    "minimum_required_changed_files": 1,
                    "preferred_coordinated_required_files": existing_preferred_coordinated_files,
                    "prefer_coordinated_when_objective_spans_files": True,
                },
            },
            metadata={
                "surface": "super_organism",
                "mode": "live",
                "tool_budget_profile": "super_dan_live",
                "trace_id": run_trace_id,
                "root_task_id": run_task_id,
                "organism_id": report.organism_id,
                "organ_id": "super-dan.live.website",
                "organism_stage": "execution",
                "worker_id": repair_worker_id,
            },
        )
        repair_worker = _live_cell_from_brief(
            model=model,
            brief=repair_brief,
            worker_id=repair_worker_id,
            organism_stage="execution",
        )
        repair_result, repair_tools, repair_events = await _execute_live_request(
            worker=repair_worker,
            request=_request_from_live_brief(repair_brief),
            tool_ids=website_tool_ids,
            workspace_root=workspace_root,
            args=args,
            model=model,
            provider=provider,
            event_logger=event_logger,
        )
        result = repair_result
        executed_tools.extend(repair_tools)
        events.extend(repair_events)
        build_token_usage = _merge_token_usage(
            build_token_usage,
            _extract_execution_usage(repair_result),
        )
        if repair_result.error:
            error = repair_result.error
        existing_paths = [path for path in required_paths if path.exists()]
        missing_paths = [path for path in required_paths if not path.exists()]
        changed_required_paths = _changed_paths_from_snapshot(file_snapshot, required_paths)
        mutated_paths = _mutation_paths_from_tools(executed_tools, workspace_root=workspace_root)
        _log_live_event(
            event_logger,
            "live.website_repair.completed",
            attempt=repair_attempts,
            model=model,
            status=repair_result.status,
            tool_calls=len(repair_tools),
            event_count=len(repair_events),
            changed_required_files=list(changed_required_paths),
        )
        validation = _failed_validation_payload(
            reason=error or "live website repair did not meet the exit contract",
            missing_requirements=[str(path) for path in missing_paths],
        )
        if not missing_paths and changed_required_paths:
            validation = await run_website_validator(changed_required_paths)
        static_validation_failures = _website_static_validation_failures(
            report,
            required_paths=required_paths,
            changed_required_paths=changed_required_paths,
            template_phrases=template_phrases,
        )
        validation = _merge_validation_failures(
            validation,
            static_validation_failures,
        )
        validation["deterministic_failures"] = list(static_validation_failures)
        validation_tool_calls_total += int(validation.get("tool_calls") or 0)
        validation_event_count_total += int(validation.get("event_count") or 0)
        validation_token_usage = _merge_token_usage(
            validation_token_usage,
            validation.get("token_usage"),
        )
    _log_final_validation_event(
        event_logger,
        worker_id="super-dan.live.website.validator",
        model=model,
        validation=validation,
        deterministic_failures=static_validation_failures,
        changed_required_files=changed_required_paths,
        first_write_recovery_attempted=bool(first_write_recovery_attempts),
        repair_attempted=bool(repair_attempts),
        repair_exhausted=bool(repair_attempts and not validation.get("passed")),
    )
    if not changed_required_paths and not error:
        error = "live build finished without changing any required website files"
    if not validation.get("passed") and not error:
        error = (
            str(validation.get("repair_brief") or "").strip()
            or "live build failed validation"
        )
    status = (
        "completed"
        if result.status == "completed"
        and not missing_paths
        and bool(changed_required_paths)
        and bool(validation.get("passed"))
        else "failed"
    )
    token_usage = _merge_token_usage(
        build_token_usage,
        validation_token_usage,
    )
    return {
        "status": status,
        "mode": "live",
        "model": model,
        "workspace_root": str(workspace_root),
        "artifact_root": str(artifact_root),
        "website": str(required_paths[0]),
        "files": [str(path) for path in existing_paths],
        "required_files": [str(path) for path in required_paths],
        "missing_files": [str(path) for path in missing_paths],
        "changed_required_files": list(changed_required_paths),
        "tool_calls": len(executed_tools) + validation_tool_calls_total,
        "mutated_paths": mutated_paths,
        "event_count": len(events) + validation_event_count_total,
        "summary": result.outputs.get("result") or result.outputs.get("text") or "",
        "error": error,
        "summary_label": "Live Build",
        "objective_kind": "website",
        "repair_attempts": repair_attempts,
        "first_write_recovery_attempts": first_write_recovery_attempts,
        "failed_step": "validation" if not bool(validation.get("passed")) else "",
        "token_usage": token_usage,
        "validation": validation,
    }


async def _run_live_generic_execution(
    report: SuperOrganismReport,
    args: argparse.Namespace,
    *,
    model: str,
    provider: LLMProvider,
    run_trace_id: str,
    run_task_id: str,
    event_logger: SuperRunEventLogger | None = None,
) -> dict[str, Any]:
    choice = _super_live_choice(report, args)
    generic_tool_ids = _live_choice_tool_ids(choice)
    generic_preferred_tool_ids = _live_choice_preferred_tool_ids(
        choice,
        ["list_directory", "file_read", "file_edit", "file_write", "git_diff", "shell_command"],
    )
    generic_read_only_tool_ids = _live_choice_read_only_tool_ids(choice)
    pacing_policy = _live_pacing_policy()
    worker_id = "super-dan.live.coding-builder"
    workspace_root = normalize_workspace_root(str(args.workspace))
    workspace_root.mkdir(parents=True, exist_ok=True)
    worker_brief = coding_brief(
            role=RoleSpec(
                role_label="coding_worker",
                responsibility="Execute the requested Super DAN coding/build change directly in the workspace.",
                success_criteria=[
                    "At least one workspace file is created or edited.",
                    "The change materially advances the operator objective.",
                    "The final answer names changed files, validation plan, and remaining risks.",
                ],
                trace_role="super-dan.live.coding-builder",
            ),
            task=_live_generic_task(report, workspace_root=workspace_root),
            scope=f"workspace={workspace_root}; native Super DAN live coding/build execution",
            hard_constraints=[
                "Actually mutate workspace files before finalizing.",
                "Do not invoke the separate DAN Code or DAN Research product shells.",
                "Keep the work inside the current workspace root.",
                "Do not use destructive git reset, checkout, or rm-style cleanup.",
            ],
            soft_constraints=[
                "Prefer a bounded implementation over a broad speculative rewrite.",
                "Use shell_command only when it materially verifies or inspects the workspace.",
                "Keep the final summary concise and inspectable.",
                "Inspect first, then make a bounded coherent implementation.",
                "Prefer `file_edit` over whole-file `file_write` when the target file already exists.",
                "After one failed or truncated large write, immediately switch to a smaller patch strategy.",
                "Avoid rereading the same files unless the next edit truly needs exact grounding.",
            ],
            pacing_policy=pacing_policy,
            tool_policy={
                "allowed_tool_ids": list(generic_tool_ids),
                "preferred_tool_ids": list(generic_preferred_tool_ids),
                "max_tool_calls": int(args.max_tool_calls),
            },
            output_contract=OutputContract(
                definition_of_done=(
                    "At least one workspace file was created or edited and the final response names the changed files, "
                    "a concise validation plan, and remaining risks."
                ),
                expected_return_shape=_live_expected_return_shape(),
            ),
            sampling_policy={"profile": choice.sampling_policy, "temperature": 0.30, "max_tokens": 2800},
            evidence=_super_report_evidence_blocks(report),
            input_payload={
                "objective": report.target,
                "workspace_root": str(workspace_root),
                "organism_id": report.organism_id,
                "cell_count": report.cell_count,
                "active_cell_cap": report.active_cell_cap,
                "execution_family": report.execution_family,
                "organ_counts": dict(report.organ_counts),
                "delivery_plan": [node.model_dump(mode="json") for node in report.delivery_plan],
                "write_pacing": dict(pacing_policy),
                "shared_board": (
                    report.shared_board.model_dump(mode="json") if report.shared_board is not None else None
                ),
                "coordination_tickets": [
                    ticket.model_dump(mode="json") for ticket in report.coordination_tickets
                ],
                "handoff_packets": [packet.model_dump(mode="json") for packet in report.handoff_packets],
                "final_audit": (
                    report.final_audit.model_dump(mode="json") if report.final_audit is not None else None
                ),
            },
            metadata={
                "surface": "super_organism",
                "mode": "live",
                "tool_budget_profile": "super_dan_live",
                "trace_id": run_trace_id,
                "root_task_id": run_task_id,
                "organism_id": report.organism_id,
                "organ_id": "super-dan.live.coding",
                "organism_stage": "execution",
                "worker_id": worker_id,
            },
        )
    worker = _live_cell_from_brief(
        model=model,
        brief=worker_brief,
        worker_id=worker_id,
        organism_stage="execution",
    )
    request = _request_from_live_brief(worker_brief)
    _log_live_event(
        event_logger,
        "live.generic_build.started",
        model=model,
        workspace_root=str(workspace_root),
        tool_ids=list(generic_tool_ids),
    )
    result, executed_tools, events = await _execute_live_request(
        worker=worker,
        request=request,
        tool_ids=generic_tool_ids,
        workspace_root=workspace_root,
        args=args,
        model=model,
        provider=provider,
        event_logger=event_logger,
    )
    _log_live_event(
        event_logger,
        "live.generic_build.completed",
        model=model,
        status=result.status,
        tool_calls=len(executed_tools),
        event_count=len(events),
    )
    build_token_usage = _extract_execution_usage(result)
    mutated_paths = _mutation_paths_from_tools(executed_tools, workspace_root=workspace_root)
    error = result.error
    if not mutated_paths and not error:
        error = "live execution finished without any workspace file mutations"
    validation = _failed_validation_payload(
        reason=error or "live execution did not meet the exit contract",
        missing_requirements=(
            ["No workspace file mutations were observed."]
            if not mutated_paths
            else None
        ),
    )
    if mutated_paths:
        validator_worker_id = "super-dan.live.coding.validator"
        validator_brief = review_brief(
                role=RoleSpec(
                    role_label="validator_coding",
                    responsibility="Validate the Super DAN coding/build mutation in read-only mode.",
                    success_criteria=[
                        "Mutated files and relevant git evidence were inspected.",
                        "The implementation materially advances the operator objective.",
                        "Placeholder-style or non-responsive changes are rejected.",
                    ],
                    artifact_targets=list(mutated_paths),
                    trace_role="super-dan.live.coding.validator",
                ),
                task=_live_generic_validation_task(report, workspace_root=workspace_root),
                scope=f"workspace={workspace_root}; native Super DAN coding/build validation",
                hard_constraints=[
                    "Read-only validation only; do not write or edit files.",
                    "Inspect the mutated files and relevant read-only git evidence before deciding.",
                    "Fail if the run made only placeholder-style or otherwise non-responsive changes.",
                ],
                soft_constraints=[
                    "Prefer concrete missing requirements over vague criticism.",
                    "Judge material advancement against the operator objective.",
                ],
                allowed_tool_ids=generic_read_only_tool_ids,
                tool_policy={
                    "allowed_tool_ids": list(generic_read_only_tool_ids),
                    "preferred_tool_ids": ["git_diff", "file_read", "git_status", "list_directory"],
                    "max_tool_calls": max(4, min(int(args.max_tool_calls), 12)),
                },
                sampling_policy={"profile": "deterministic", "temperature": 0.0, "max_tokens": 1600},
                output_contract=OutputContract(
                    definition_of_done="Return the validation report only.",
                    expected_return_shape=_live_validation_return_shape(),
                ),
                input_payload={
                    "objective": report.target,
                    "workspace_root": str(workspace_root),
                    "mutated_paths": list(mutated_paths),
                },
                metadata={
                    "surface": "super_organism",
                    "mode": "live",
                    "tool_budget_profile": "super_dan_live",
                    "trace_id": run_trace_id,
                    "root_task_id": run_task_id,
                    "organism_id": report.organism_id,
                    "organ_id": "super-dan.live.coding",
                    "worker_id": validator_worker_id,
                    "organism_stage": "validation",
                },
            )
        validator_worker = _live_cell_from_brief(
            model=model,
            brief=validator_brief,
            worker_id=validator_worker_id,
            organism_stage="validation",
        )
        validator_request = _request_from_live_brief(validator_brief)
        validation = await _run_live_validation(
            worker=validator_worker,
            request=validator_request,
            tool_ids=generic_read_only_tool_ids,
            workspace_root=workspace_root,
            args=args,
            model=model,
            provider=provider,
            event_logger=event_logger,
        )
    _log_final_validation_event(
        event_logger,
        worker_id="super-dan.live.coding.validator",
        model=model,
        validation=validation,
    )
    if not validation.get("passed") and not error:
        error = (
            str(validation.get("repair_brief") or "").strip()
            or "live execution failed validation"
        )
    status = (
        "completed"
        if result.status == "completed" and mutated_paths and bool(validation.get("passed"))
        else "failed"
    )
    token_usage = _merge_token_usage(
        build_token_usage,
        validation.get("token_usage"),
    )
    return {
        "status": status,
        "mode": "live",
        "model": model,
        "workspace_root": str(workspace_root),
        "files": list(mutated_paths),
        "required_files": [],
        "missing_files": [],
        "tool_calls": len(executed_tools) + int(validation.get("tool_calls") or 0),
        "mutated_paths": list(mutated_paths),
        "event_count": len(events) + int(validation.get("event_count") or 0),
        "summary": result.outputs.get("result") or result.outputs.get("text") or "",
        "error": error,
        "summary_label": "Live Run",
        "objective_kind": "coding",
        "token_usage": token_usage,
        "validation": validation,
    }


def _mutation_paths_from_tools(
    executed_tools: Sequence[dict[str, Any]],
    *,
    workspace_root: Path,
) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for tool in executed_tools:
        if not tool.get("ok") or str(tool.get("tool_id") or "") not in {"file_write", "file_edit"}:
            continue
        arguments = tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {}
        result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
        raw_path = str(result.get("path") or arguments.get("path") or arguments.get("file_path") or "").strip()
        if not raw_path:
            continue
        candidate = Path(raw_path).expanduser()
        path = candidate if candidate.is_absolute() else workspace_root / candidate
        rendered = str(path.resolve(strict=False))
        if rendered in seen:
            continue
        seen.add(rendered)
        paths.append(rendered)
    return paths


def _render_website_html(report: SuperOrganismReport) -> str:
    objective = _html_escape(report.target)
    cell_count = int(report.cell_count)
    active_cell_cap = int(report.active_cell_cap)
    nodes = "\n".join(
        f"""          <article class="node-card" data-status="{_html_escape(node.status)}">
            <span>{_html_escape(node.node_id)}</span>
            <h3>{_html_escape(node.title)}</h3>
            <p>{_html_escape('; '.join(node.notes[:1]) or 'A coordinated cell contract.')}</p>
          </article>"""
        for node in report.delivery_plan
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Super DAN Organism</title>
  <link rel="stylesheet" href="./styles.css" />
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="hero-copy">
        <p class="eyebrow">{cell_count}-cell universal agent organism</p>
        <h1>Super DAN turns one objective into coordinated execution.</h1>
        <p class="lede">{objective}</p>
        <div class="metrics">
          <span>{cell_count} logical cells</span>
          <span>{active_cell_cap} active cap</span>
          <span>{_html_escape(report.score_label)} {report.credibility_score:.2f}</span>
        </div>
      </div>
      <div class="organism-stage" aria-label="Animated {cell_count}-cell organism">
        <div class="cell-field"></div>
        <div class="core">DAN</div>
      </div>
    </section>

    <section class="organs">
      <p class="eyebrow">Organ contracts</p>
      <h2>Cells specialize, then synchronize.</h2>
      <div class="organ-grid">
        <div>Brain<br><strong>objective + authority</strong></div>
        <div>Scout<br><strong>context acquisition</strong></div>
        <div>Claim<br><strong>work graph atoms</strong></div>
        <div>Immune<br><strong>risk pressure</strong></div>
        <div>Memory<br><strong>shared board</strong></div>
        <div>Experiment<br><strong>execution probe</strong></div>
        <div>Synthesis<br><strong>done / continue / clarify</strong></div>
      </div>
    </section>

    <section class="contract">
      <p class="eyebrow">Execution contract</p>
      <h2>{_html_escape(report.final_verdict)}</h2>
      <div class="node-grid">
{nodes}
      </div>
    </section>
  </main>
  <script src="./app.js"></script>
</body>
</html>
"""


def _render_website_css() -> str:
    return """:root {
  color-scheme: dark;
  --bg: #10130f;
  --panel: #182017;
  --ink: #f2ead8;
  --muted: #b7ad96;
  --signal: #f2b84b;
  --immune: #ff6b4a;
  --memory: #7ed7b5;
  --brain: #a7c7ff;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background:
    radial-gradient(circle at 20% 10%, rgba(242, 184, 75, .22), transparent 28rem),
    radial-gradient(circle at 90% 20%, rgba(126, 215, 181, .14), transparent 24rem),
    linear-gradient(135deg, #10130f, #070806 70%);
  color: var(--ink);
  font: 16px/1.5 "Iowan Old Style", "Palatino Linotype", Palatino, serif;
}
.shell { width: min(1180px, calc(100vw - 40px)); margin: 0 auto; padding: 56px 0; }
.hero { display: grid; grid-template-columns: 1fr 520px; gap: 48px; align-items: center; min-height: 78vh; }
.eyebrow { color: var(--signal); letter-spacing: .18em; text-transform: uppercase; font: 700 12px/1.2 "Avenir Next", "Gill Sans", sans-serif; }
h1, h2 { line-height: .94; margin: 0; letter-spacing: -.05em; }
h1 { font-size: clamp(48px, 8vw, 104px); max-width: 820px; }
h2 { font-size: clamp(36px, 6vw, 72px); }
.lede { color: var(--muted); font-size: clamp(18px, 2vw, 24px); max-width: 680px; }
.metrics { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 28px; }
.metrics span, .node-card, .organ-grid div {
  border: 1px solid rgba(242, 234, 216, .16);
  background: rgba(24, 32, 23, .74);
  box-shadow: 0 20px 80px rgba(0,0,0,.24);
}
.metrics span { padding: 10px 14px; border-radius: 999px; color: var(--ink); }
.organism-stage { position: relative; aspect-ratio: 1; border-radius: 48px; overflow: hidden; background: rgba(24, 32, 23, .64); border: 1px solid rgba(242, 234, 216, .14); }
.cell-field { position: absolute; inset: 0; }
.cell { position: absolute; width: 10px; height: 10px; border-radius: 999px; background: var(--signal); transform: translate(-50%, -50%); animation: pulse 2.8s ease-in-out infinite; box-shadow: 0 0 22px currentColor; }
.cell:nth-child(3n) { color: var(--memory); background: var(--memory); }
.cell:nth-child(5n) { color: var(--immune); background: var(--immune); }
.cell:nth-child(7n) { color: var(--brain); background: var(--brain); }
.core { position: absolute; inset: 50% auto auto 50%; transform: translate(-50%, -50%); width: 112px; height: 112px; border-radius: 50%; display: grid; place-items: center; background: var(--ink); color: #111; font: 900 26px/1 "Avenir Next", "Gill Sans", sans-serif; }
.organs, .contract { padding: 84px 0 0; }
.organ-grid, .node-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-top: 28px; }
.organ-grid div, .node-card { border-radius: 24px; padding: 22px; min-height: 132px; }
.node-card span { color: var(--signal); font: 800 12px/1 "Avenir Next", "Gill Sans", sans-serif; }
.node-card h3 { margin: 12px 0 8px; font-size: 22px; line-height: 1.05; }
.node-card p { margin: 0; color: var(--muted); }

@keyframes pulse {
  0%, 100% { transform: translate(-50%, -50%) scale(.74); opacity: .45; }
  45% { transform: translate(-50%, -50%) scale(1.4); opacity: 1; }
}
@media (max-width: 900px) {
  .hero { grid-template-columns: 1fr; }
  .organ-grid, .node-grid { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  .cell { animation: none; }
}
"""


def _render_website_js(report: SuperOrganismReport) -> str:
    total = max(1, int(report.cell_count))
    return f"""const field = document.querySelector('.cell-field');
if (field) {{
  const total = {total};
  const rings = Math.max(1, Math.ceil(total / 20));
  const perRing = Math.max(1, Math.ceil(total / rings));
  for (let index = 0; index < total; index += 1) {{
    const cell = document.createElement('i');
    cell.className = 'cell';
    const ring = Math.floor(index / perRing) + 1;
    const angle = (index % perRing) / perRing * Math.PI * 2 + ring * 0.22;
    const radius = 8 + (ring / (rings + 1)) * 34;
    const x = 50 + Math.cos(angle) * radius;
    const y = 50 + Math.sin(angle) * radius;
    cell.style.left = `${{x}}%`;
    cell.style.top = `${{y}}%`;
    cell.style.animationDelay = `${{(index % 20) * 70}}ms`;
    field.appendChild(cell);
  }}
}}
"""


def _render_website_readme(report: SuperOrganismReport) -> str:
    return f"""# Super DAN Website Artifact

Generated by `dan super-organism` from this objective:

```text
{report.target}
```

Open `index.html` in a browser, or serve this folder with any static server.

The artifact is deterministic and self-contained. It does not depend on DAN Code
or DAN Research.
"""


def _html_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _run_super_turn(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if bool(getattr(args, "live", False)) and bool(getattr(args, "plan_only", False)):
        parser.error("--live cannot be combined with --plan-only")

    try:
        report = run_super_organism_demo(
            args.target,
            organism_id=str(args.organism_id),
            cell_count=int(args.cell_count),
            active_cell_cap=int(args.active_cell_cap),
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    implicit_live = _should_implicit_live(report, args)
    if implicit_live:
        setattr(args, "live", True)
        setattr(args, "_implicit_live", True)
        setattr(args, "_code_like_live", True)
    elif not hasattr(args, "_implicit_live"):
        setattr(args, "_implicit_live", False)
    if bool(getattr(args, "live", False)):
        setattr(args, "_code_like_live", True)

    payload = report.model_dump(mode="json")
    live_result: dict[str, Any] | None = None
    if bool(getattr(args, "live", False)):
        if not _supports_live_execution(report, args):
            parser.error(
                "--live currently supports website-like and general coding/build objectives"
            )
        live_workspace_root = normalize_workspace_root(str(args.workspace))
        live_workdir, live_turn_number = _build_super_run_workdir(live_workspace_root)
        live_trace_id = new_trace_id()
        live_task_id = f"super-dan-live:{live_turn_number}"
        objective_kind = "website" if _is_website_objective(report, args) else "coding"
        progress_renderer = SuperProgressRenderer(
            enabled=not bool(getattr(args, "json", False))
            and not bool(getattr(args, "quiet_progress", False))
        )
        hook_runtime = SuperHookRuntime(
            state_root=live_workspace_root / ".dan-super" / "state",
            run_id=live_task_id,
            turn_id=str(live_turn_number),
            task_id=live_task_id,
            trace_id=live_trace_id,
            reactivity_profile=str(getattr(args, "reactivity", "balanced") or "balanced"),
            worktree_parallelism=max(0, int(getattr(args, "worktree_parallelism", 0) or 0)),
        )
        event_logger = SuperRunEventLogger(
            path=live_workdir / "events.jsonl",
            session_id=_super_session_id(live_workspace_root),
            turn_id=str(live_turn_number),
            task_id=live_task_id,
            organism_id=report.organism_id,
            organ_id="super-dan.live",
            trace_id=live_trace_id,
            progress_callback=progress_renderer,
            hook_runtime=hook_runtime,
        )
        try:
            _log_live_event(
                event_logger,
                "run.log.started",
                trace_id=live_trace_id,
                task_id=live_task_id,
                turn_number=live_turn_number,
                objective=report.target,
                objective_kind=objective_kind,
                workspace_root=str(live_workspace_root),
                workdir=str(live_workdir),
                requested_model=str(args.model or "").strip() or None,
            )
            try:
                model = _resolve_live_model(args.model)
                _log_live_event(
                    event_logger,
                    "provider.build.started",
                    requested_model=str(args.model or "").strip() or None,
                    model=model,
                    base_url=str(args.base_url or "").strip() or None,
                )
                provider = _build_live_provider(
                    model,
                    api_key=args.api_key,
                    base_url=args.base_url,
                )
                _log_live_event(
                    event_logger,
                    "provider.build.completed",
                    model=model,
                    base_url=str(args.base_url or "").strip() or None,
                )
            except Exception as exc:
                _log_live_event(
                    event_logger,
                    "provider.build.failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    requested_model=str(args.model or "").strip() or None,
                    base_url=str(args.base_url or "").strip() or None,
                )
                _log_live_event(
                    event_logger,
                    "run.log.failed",
                    trace_id=live_trace_id,
                    task_id=live_task_id,
                    status="failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    event_log_path=str(event_logger.path),
                    event_log_schema=ORGANISM_LOG_SCHEMA_VERSION,
                )
                parser.error(str(exc))
                return 2
            try:
                if _is_website_objective(report, args):
                    live_result = asyncio.run(
                        _run_live_website_build(
                            report,
                            args,
                            model=model,
                            provider=provider,
                            run_trace_id=live_trace_id,
                            run_task_id=live_task_id,
                            event_logger=event_logger,
                        )
                    )
                else:
                    live_result = asyncio.run(
                        _run_live_generic_execution(
                            report,
                            args,
                            model=model,
                            provider=provider,
                            run_trace_id=live_trace_id,
                            run_task_id=live_task_id,
                            event_logger=event_logger,
                        )
                    )
            except Exception as exc:
                _log_live_event(
                    event_logger,
                    "run.log.failed",
                    trace_id=live_trace_id,
                    task_id=live_task_id,
                    status="failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    event_log_path=str(event_logger.path),
                    event_log_schema=ORGANISM_LOG_SCHEMA_VERSION,
                )
                print(
                    f"Live Super DAN failed: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                return 1
            live_result = {
                **dict(live_result or {}),
                "event_log_path": str(event_logger.path),
                "event_log_schema": ORGANISM_LOG_SCHEMA_VERSION,
                "hook_state": event_logger.hook_state_snapshot(),
                "show_queue_status": bool(getattr(args, "queue_status", False)),
            }
            _log_live_event(
                event_logger,
                "run.log.completed",
                trace_id=live_trace_id,
                task_id=live_task_id,
                status=live_result.get("status"),
                objective_kind=objective_kind,
                validation_passed=bool(
                    dict(live_result.get("validation") or {}).get("passed")
                ),
                tool_calls=int(live_result.get("tool_calls") or 0),
                event_count=int(live_result.get("event_count") or 0),
                event_log_path=str(event_logger.path),
                event_log_schema=ORGANISM_LOG_SCHEMA_VERSION,
            )
        finally:
            event_logger.close()
        payload = {"report": payload, "live_build": live_result}

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        if live_result is not None and live_result.get("status") != "completed":
            return 1
    elif live_result is not None:
        _print_live_report(
            report,
            live_result,
            verbose=bool(getattr(args, "verbose", False)),
        )
        if live_result.get("status") != "completed":
            return 1
    else:
        paths = (
            _materialize_website_artifact(report, args)
            if _should_materialize_website(report, args)
            else []
        )
        if bool(getattr(args, "verbose", False)):
            _print_text_report(report)
            if paths:
                artifact_lines = ["", "Materialized Artifacts:", *[f"- {path}" for path in paths], ""]
                print("\n".join(artifact_lines), end="\n")
        else:
            _print_compact_report(report, artifact_paths=paths)
    return 0


def _interactive_loop(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    workspace_root = normalize_workspace_root(str(args.workspace))
    print("Super DAN interactive")
    print(f"workspace: {workspace_root}")
    print("Type an objective, /plan <objective> for a dry contract, /status for queues, or /exit.")
    while True:
        try:
            text = input("super-dan> ")
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 130
        objective = text.strip()
        if not objective:
            continue
        lowered = objective.lower()
        if lowered in {"/exit", "/quit", "exit", "quit"}:
            return 0
        if lowered in {"/status", "/queues", "status"}:
            print(format_super_queue_status(workspace_root))
            continue
        plan_only = False
        if lowered.startswith("/plan "):
            objective = objective[6:].strip()
            plan_only = True
        if not objective:
            continue
        turn_args = copy.copy(args)
        turn_args.target = objective
        turn_args.plan_only = plan_only
        turn_args.live = bool(getattr(args, "live", False)) or not plan_only
        turn_args.json = False
        turn_args.output = None
        turn_args._code_like_live = not plan_only
        try:
            exit_code = _run_super_turn(turn_args, parser)
        except SystemExit as exc:
            exit_code = int(exc.code or 0) if isinstance(exc.code, int) else 2
        if exit_code not in {0, 1}:
            return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    load_env()
    parser = build_parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(raw_argv)
    setattr(args, "_live_explicit", _argv_has_option(raw_argv, "--live"))
    setattr(args, "_model_explicit", _argv_has_option(raw_argv, "--model"))
    setattr(args, "_artifact_dir_explicit", _argv_has_option(raw_argv, "--artifact-dir"))
    setattr(args, "_implicit_live", False)
    setattr(args, "_code_like_live", False)
    setattr(args, "_stdin_is_tty", sys.stdin.isatty())

    no_objective = not str(getattr(args, "target", "") or "").strip()
    if no_objective and bool(getattr(args, "queue_status", False)):
        workspace_root = normalize_workspace_root(str(args.workspace))
        print(format_super_queue_status(workspace_root))
        return 0
    report_mode_requested = any(
        bool(getattr(args, field, False))
        for field in ("plan_only", "json", "verbose")
    ) or bool(getattr(args, "output", None))
    if no_objective and not report_mode_requested and sys.stdin.isatty():
        return _interactive_loop(args, parser)

    return _run_super_turn(args, parser)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
