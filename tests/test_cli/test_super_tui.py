from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
import threading
import tomllib
from argparse import Namespace
from pathlib import Path

import pytest

from dan.cli.main import _SUBCOMMANDS
from dan.cli import super_tui
from dan.cli import super_organism
from dan.providers import CompletionResult
from dan.skills import invocation as skill_invocation


def _write_event_log(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def _patch_tui_route(
    monkeypatch,
    *,
    permission: str,
    complexity: str,
    routed_with_model: bool = False,
    confidence: float = 0.9,
    rationale: str = "test model route",
    clarification: str = "",
) -> None:
    decision = super_tui.TuiIntentDecision(
        permission=permission,
        complexity=complexity,
        confidence=confidence,
        rationale=rationale,
        clarification=clarification,
    )
    monkeypatch.setattr(
        super_tui,
        "_route_tui_intent_with_model",
        lambda args: (decision, routed_with_model),
    )


def test_unified_cli_registers_super_tui_without_changing_super_organism() -> None:
    assert _SUBCOMMANDS["super-tui"] == ("dan.cli.super_tui", "main")
    assert _SUBCOMMANDS["super-organism"] == ("dan.cli.super_organism", "main")

    pyproject = tomllib.loads(
        (Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )
    scripts = pyproject["project"]["scripts"]
    assert scripts["dan-super-tui"] == "dan.cli.super_tui:main"
    assert scripts["dan-super-organism"] == "dan.cli.super_organism:main"


def test_unified_cli_propagates_subcommand_exit_code(monkeypatch) -> None:
    from dan.cli import main as main_cli

    module_name = "fake_dan_subcommand_for_test"
    monkeypatch.setitem(sys.modules, module_name, Namespace(main=lambda: 7))
    monkeypatch.setitem(main_cli._SUBCOMMANDS, "fake", (module_name, "main"))
    monkeypatch.setattr(sys, "argv", ["dan", "fake"])

    assert main_cli.main() == 7


def test_super_tui_plain_status_renders_event_log_projection(tmp_path, capsys) -> None:
    event_log = tmp_path / "events.jsonl"
    _write_event_log(
        event_log,
        [
            {
                "schema": "organism_log_v1",
                "event": "run.log.started",
                "objective": "build dashboard",
                "task_id": "super-dan-live:1",
                "workspace_root": "/tmp/ws",
                "requested_model": "fake-live-model",
            },
            {
                "schema": "organism_log_v1",
                "event": "live.planning.started",
                "plan_root": ".dan-super/runs/turn-01/plans",
            },
            {
                "schema": "organism_log_v1",
                "event": "model.requested",
                "round": 1,
                "model": "fake-live-model",
                "tool_count": 4,
                "worker_id": "super-dan.live.builder",
            },
            {
                "schema": "organism_log_v1",
                "event": "tool.completed",
                "tool_id": "file_write",
                "status": "completed",
                "result": {"path": "website/index.html"},
            },
            {
                "schema": "organism_log_v1",
                "event": "live.validation.completed",
                "passed": True,
                "overall_score": 0.92,
            },
            {
                "schema": "organism_log_v1",
                "event": "run.log.completed",
                "status": "completed",
                "event_log_path": ".dan-super/runs/turn-01/events.jsonl",
            },
        ],
    )

    exit_code = super_tui.main(["--event-log", str(event_log), "--plain"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Answer:" in stdout
    assert "Outcome:" in stdout
    assert "Run finished successfully" in stdout
    assert "Checks: passed" in stdout
    assert "Changed: website/index.html" in stdout
    assert "Event Log:" not in stdout


def test_super_tui_final_success_hides_resolved_validation_blocker(tmp_path, capsys) -> None:
    event_log = tmp_path / "events.jsonl"
    _write_event_log(
        event_log,
        [
            {
                "schema": "organism_log_v1",
                "event": "run.log.started",
                "objective": "repair demo",
                "task_id": "super-dan-live:1",
                "workspace_root": "/tmp/ws",
            },
            {
                "schema": "organism_log_v1",
                "event": "live.validation.shell_check.completed",
                "passed": False,
                "exit_code": 1,
                "command": "pytest -q",
                "failure": "pytest failed before repair",
            },
            {
                "schema": "organism_log_v1",
                "event": "tool.completed",
                "tool_id": "file_edit",
                "status": "completed",
                "result": {"path": "src/app.py"},
            },
            {
                "schema": "organism_log_v1",
                "event": "live.validation.completed",
                "passed": True,
                "overall_score": 0.88,
            },
            {
                "schema": "organism_log_v1",
                "event": "run.log.completed",
                "status": "completed",
            },
        ],
    )

    exit_code = super_tui.main(["--event-log", str(event_log), "--plain"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Checks: passed" in stdout
    assert "Changed: src/app.py" in stdout
    assert "Blocker:" not in stdout
    assert "pytest failed before repair" not in stdout


def test_super_tui_failed_event_log_renders_error_and_trace(tmp_path, capsys) -> None:
    event_log = tmp_path / ".dan-super" / "runs" / "turn-01" / "events.jsonl"
    _write_event_log(
        event_log,
        [
            {
                "schema": "organism_log_v1",
                "event": "run.log.started",
                "objective": "repair workspace",
                "task_id": "super-dan-live:1",
                "workspace_root": str(tmp_path),
            },
            {
                "schema": "organism_log_v1",
                "event": "run.log.failed",
                "status": "failed",
                "error_type": "APIConnectionError",
                "error": "Connection error.",
                "event_log_path": str(event_log),
            },
        ],
    )

    exit_code = super_tui.main(["--event-log", str(event_log), "--plain"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Answer:" in stdout
    assert "APIConnectionError: Connection error" in stdout
    assert "Connection error.." not in stdout
    assert "Trace:" in stdout
    assert str(event_log) in stdout


def test_super_tui_projection_tracks_validation_failures_and_repair_state() -> None:
    state = super_tui.SuperTuiState()

    for row in [
        {
            "event": "run.log.started",
            "objective": "fix report",
            "task_id": "super-dan-live:2",
            "workspace_root": "/tmp/ws",
        },
        {
            "event": "live.validation.completed",
            "passed": False,
            "overall_score": 0.41,
            "deterministic_failures": [
                "missing required README.md",
                "index.html still contains template copy",
            ],
        },
        {
            "event": "live.generic_repair.started",
            "attempt": 1,
            "reason": "validation failed",
        },
        {"event": "run.log.completed", "status": "failed"},
    ]:
        state.observe(row)

    output = state.plain_snapshot()
    assert "Status: failed" in output
    assert "Checks: failed" in output
    assert "missing required README.md" in output
    assert "index.html still contains template copy" in output
    assert "Repair pass started: validation failed" in "\n".join(state.recent)
    assert "The run failed" in "\n".join(state.recent)


def test_super_tui_projection_tracks_hook_queue_state() -> None:
    state = super_tui.SuperTuiState()

    line = state.observe(
        {
            "event": "super.hook.packet_enqueued",
            "inbox_id": "validation",
            "source_event": "tool.completed",
            "packet_type": "material_write",
            "queue_depth": 1,
        }
    )

    assert line == "Follow-up queued for validation (depth 1)."
    assert "Queue: validation depth=1" in state.plain_snapshot()


def test_super_tui_board_projection_renders_admission_lanes_without_raw_noise() -> None:
    state = super_tui.SuperTuiState()

    state.observe(
        {
            "event": "tui.board.admission",
            "task_id": "task-A",
            "run_id": "run-A",
            "objective": "build script A as script_a.py",
            "decision": "accepted; running",
            "phase": "building",
            "action": "editing script_a.py",
            "trace_ref": ".dan-super/runs/run-A/events.jsonl",
        }
    )
    state.observe(
        {
            "event": "tui.board.admission",
            "task_id": "task-B",
            "run_id": "run-B",
            "objective": "build script B as script_b.py",
            "decision": "started in parallel",
            "against_task_id": "task-A",
            "phase": "building",
            "action": "editing script_b.py",
        }
    )
    state.observe(
        {
            "event": "tui.board.admission",
            "task_id": "task-C",
            "objective": "also modify script.py",
            "decision": "queued behind task-A",
            "status": "queued",
            "reason": "path conflict: script.py",
        }
    )
    state.observe({"event": "model.requested", "round": 2, "model": "fake-model"})

    text = "\n".join(super_tui._format_tui_board_lines(state, width=96))

    assert "Admission: task-C: queued behind task-A" in text
    assert "Board:" in text
    assert "Active:" in text
    assert "task-A" in text
    assert "task-B" in text
    assert "Queued:" in text
    assert "task-C [queued]" in text
    assert "path conflict: script.py" in text
    assert "Intervene:" in text
    assert "/stop" in text
    assert "executor work currently in flight" not in text
    assert "model.requested" not in text


def test_super_tui_board_sections_are_stable_and_include_progress() -> None:
    state = super_tui.SuperTuiState()
    state.observe(
        {
            "event": "tui.board.admission",
            "task_id": "task-A",
            "run_id": "run-A",
            "objective": "merge source data",
            "decision": "accepted; running",
            "phase": "building",
            "action": "checking input tables",
        }
    )

    text = "\n".join(super_tui._format_tui_board_lines(state, width=100))

    admission_at = text.index("Admission:")
    board_at = text.index("Board:")
    progress_at = text.index("Progress:")
    intervene_at = text.index("Intervene:")
    assert admission_at < board_at < progress_at < intervene_at
    assert "Focus task-A: checking input tables" in text


def test_super_tui_task_overview_hides_raw_board_and_json_details() -> None:
    state = super_tui.SuperTuiState(workspace="/tmp/ws")
    state.observe(
        {
            "event": "tui.board.admission",
            "task_id": "76aac894a7acd2ad",
            "run_id": "run-A",
            "objective": "what now",
            "status": "completed",
            "phase": "background_running",
            "action": '{"candidate_fragment":{"ToDo.md":"# ToDo\\n..."}}',
            "reason": "no active or queued executor work in this board scope",
            "trace_ref": "/tmp/ws/.dan-super/runs/turn-09/events.jsonl",
            "changed_paths": [
                "/tmp/ws/codes/generate-regression-dyadic-data.py",
                "/tmp/ws/.dan-super/result-packet-006.json",
            ],
        }
    )

    text = "\n".join(super_tui._format_tui_task_overview_lines(state))

    assert "Recent" in text
    assert "Done: what now" in text
    assert "What:" in text
    assert "Next:" in text
    assert "You can: /status <id> for details" in text
    assert "codes/generate-regression-dyadic-data.py" in text
    assert "/tmp/ws" not in text
    assert "Board:" not in text
    assert "Intervene:" not in text
    assert "candidate_fragment" not in text
    assert "events.jsonl" not in text
    assert "result-packet" not in text
    assert "queue depth" not in text


def test_super_tui_task_overview_skips_status_only_admission_rows() -> None:
    state = super_tui.SuperTuiState(workspace="/tmp/ws")
    state.observe(
        {
            "event": "tui.board.admission",
            "task_id": "admission",
            "objective": "show current status",
            "status": "chat_or_status",
            "phase": "chat_or_status",
            "action": "No active or queued Agent runs.",
            "reason": "explicit status request",
        }
    )
    state.observe(
        {
            "event": "tui.board.admission",
            "task_id": "done-task",
            "run_id": "run-done",
            "objective": "finished report",
            "status": "completed",
            "changed_paths": ["/tmp/ws/report.md"],
        }
    )

    text = "\n".join(super_tui._format_tui_task_overview_lines(state, width=100))

    assert "Active" not in text
    assert "Running: show current status" not in text
    assert "No active executor work right now." in text
    assert "Recent" in text
    assert "Done: finished report" in text
    assert "report.md" in text


def test_super_tui_chat_or_status_response_does_not_create_fake_board_row() -> None:
    events = super_tui._tui_async_board_events_from_response(
        {
            "status": "reported",
            "admission": {
                "action": "chat_or_status",
                "status_text": "No active or queued Agent runs.",
                "reason": "explicit status request",
            },
            "board": {
                "active_runs": [],
                "queued_runs": [],
                "completed_runs": [],
            },
        }
    )

    assert events == ()


def test_super_tui_inside_command_reads_recent_task_activity(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    trace = workspace / ".dan-super" / "runs" / "turn-01" / "events.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "event": "run.log.started",
                    "objective": "test the current game using Godot",
                    "task_id": "task-A",
                    "event_log_path": str(trace),
                },
                {
                    "event": "tool.started",
                    "tool_id": "shell_command",
                    "arguments": {"command": "godot --headless --path . --quit"},
                },
                {
                    "event": "tool.completed",
                    "tool_id": "shell_command",
                    "result": {"exit_code": 0, "stdout": "ok"},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    super_tui._append_tui_transcript_entry(
        workspace,
        role="assistant_progress",
        text="Started background task.",
        metadata={
            "tui_board_event": {
                "event": "tui.board.admission",
                "task_id": "task-A",
                "run_id": "run-A",
                "objective": "test the current game using Godot",
                "status": "running",
                "phase": "background_running",
                "action": "checking Godot launch",
                "trace_ref": ".dan-super/runs/turn-01/events.jsonl",
            }
        },
    )

    state = super_tui._load_tui_board_source_state(workspace)
    text = "\n".join(super_tui._format_tui_inside_lines(state, workspace_root=workspace, target="task-A"))

    assert "Looking inside `task-A`." in text
    assert "It is working on: test the current game using Godot" in text
    assert "Latest visible movement: checking Godot launch" in text
    assert "Recent internal activity:" in text
    assert "Terminal command started" in text
    assert "Terminal command finished" in text
    assert "Trace: .dan-super/runs/turn-01/events.jsonl" in text
    assert "Controls: `/status task-A`, `/append ...`, `/stop task-A`." in text


def test_super_tui_outbox_records_submitted_and_acknowledged_messages(tmp_path, capsys, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", str(workspace), "--plain", "--no-async-agent"])
    super_tui._prepare_args(args, [])
    inputs = iter(["/help", "/exit"])

    monkeypatch.setattr(super_tui, "_read_interactive_line", lambda *args, **kwargs: next(inputs))
    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace_root: [])
    monkeypatch.setattr(super_tui, "_load_tui_path_suggestions", lambda workspace_root: [])

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    capsys.readouterr()
    rows = [
        json.loads(line)
        for line in (workspace / ".dan-super" / "tui" / "outbox.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["status"] for row in rows] == ["submitted", "acknowledged"]
    assert rows[-1]["route"] == "help"
    assert not (workspace / ".dan-super" / "tui" / "draft.json").exists()


def test_super_tui_background_dispatch_marks_outbox(tmp_path, monkeypatch) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", str(tmp_path), "--plain"])
    super_tui._prepare_args(args, [])
    args.target = "check the game"

    def fake_dispatch(run_args, run_parser, *, force_live=False):
        assert run_args.target == "check the game"
        assert run_parser is parser
        assert force_live is True
        return 0

    monkeypatch.setattr(super_tui, "_dispatch_tui_turn", fake_dispatch)

    thread = super_tui._start_tui_background_dispatch(
        args,
        parser,
        workspace_root=tmp_path,
        message_id="msg-1",
        objective="check the game",
        forced_new=False,
        plan_only=False,
    )
    thread.join(timeout=2)

    assert not thread.is_alive()
    rows = [
        json.loads(line)
        for line in (tmp_path / ".dan-super" / "tui" / "outbox.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["status"] == "acknowledged"
    assert rows[-1]["route"] == "turn"
    assert rows[-1]["metadata"]["background_dispatch"] is True


def test_super_tui_background_dispatch_gate_requires_tty(tmp_path, monkeypatch) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", str(tmp_path), "--plain"])
    super_tui._prepare_args(args, [])

    class FakeStdin:
        def __init__(self, tty: bool) -> None:
            self.tty = tty

        def isatty(self) -> bool:
            return self.tty

    monkeypatch.setattr(super_tui.sys, "stdin", FakeStdin(True))
    assert super_tui._should_dispatch_tui_turn_in_background(args, async_agent_enabled=True)

    args.plan_only = True
    assert not super_tui._should_dispatch_tui_turn_in_background(args, async_agent_enabled=True)

    args.plan_only = False
    monkeypatch.setattr(super_tui.sys, "stdin", FakeStdin(False))
    assert not super_tui._should_dispatch_tui_turn_in_background(args, async_agent_enabled=True)
    assert not super_tui._should_dispatch_tui_turn_in_background(args, async_agent_enabled=False)


def test_super_tui_queue_summary_hides_internal_counters_when_idle() -> None:
    raw = "\n".join(
        [
            "Super DAN queues",
            "reactivity: balanced",
            "Inboxes:",
            "- validation: pending=0 active=0 enqueued=5 leased=5 coalesced=0 backpressured=0 dead=0",
            "- repair: pending=0 active=0 enqueued=0 leased=0 coalesced=0 backpressured=0 dead=0",
            "Worktrees: tasks=0 diffs=0",
        ]
    )

    text = super_tui._format_tui_queue_summary(raw)

    assert "No active internal queue work." in text
    assert "Reactivity: balanced" in text
    assert "enqueued=" not in text
    assert "leased=" not in text
    assert "Use /tasks" in text


def test_super_tui_board_focus_append_and_cancel_persist_as_interventions(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission_event = {
        "event": "tui.board.admission",
        "task_id": "task-A",
        "run_id": "run-A",
        "objective": "build script A",
        "decision": "accepted; running",
        "phase": "building",
        "action": "editing script_a.py",
    }
    super_tui._append_tui_transcript_entry(
        workspace,
        role="system_notice",
        text="task-A admitted",
        metadata={"tui_board_event": admission_event},
    )

    focus_result = super_tui._handle_tui_board_command(
        workspace,
        super_tui.TuiRunCommand(command="focus", payload="run-A"),
    )
    assert "Focused task-A" in focus_result.message
    super_tui._append_tui_transcript_entry(
        workspace,
        role="system_notice",
        text=focus_result.message,
        metadata=super_tui._tui_board_command_transcript_metadata(
            super_tui.TuiRunCommand(command="focus", payload="run-A"),
            focus_result,
        ),
    )

    append_result = super_tui._handle_tui_board_command(
        workspace,
        super_tui.TuiRunCommand(command="append", payload="please add validation"),
    )
    assert "append requested; checkpoint pending" in append_result.message
    assert "please add validation" in append_result.message
    super_tui._append_tui_transcript_entry(
        workspace,
        role="system_notice",
        text=append_result.message,
        metadata=super_tui._tui_board_command_transcript_metadata(
            super_tui.TuiRunCommand(command="append", payload="please add validation"),
            append_result,
        ),
    )

    cancel_result = super_tui._handle_tui_board_command(
        workspace,
        super_tui.TuiRunCommand(command="cancel"),
    )
    assert "cancel requested; checkpoint pending" in cancel_result.message
    assert "task-A [cancel-pending" in cancel_result.message


def test_super_tui_narrator_snapshot_separates_board_lanes() -> None:
    state = super_tui.SuperTuiState(objective="build two scripts")
    state.observe(
        {
            "event": "tui.board.admission",
            "task_id": "task-A",
            "objective": "build script A",
            "decision": "accepted; running",
            "phase": "building",
            "action": "editing script_a.py",
        }
    )
    state.observe(
        {
            "event": "tui.board.admission",
            "task_id": "task-B",
            "objective": "build script B",
            "decision": "queued behind task-A",
            "status": "queued",
            "reason": "path conflict: script.py",
        }
    )

    snapshot = state._narrator_snapshot()
    activity = "\n".join(snapshot.activity)

    assert "Board lane task-A: running/building - editing script_a.py" in activity
    assert "Board lane task-B: queued - path conflict: script.py" in activity
    assert "Board lane task-B" in snapshot.queued_work


def test_super_tui_terminal_result_updates_target_row_without_overwriting_active_row() -> None:
    state = super_tui.SuperTuiState(objective="build two scripts")
    state.observe(
        {
            "event": "tui.board.admission",
            "task_id": "task-A",
            "run_id": "run-A",
            "objective": "build script A",
            "decision": "accepted; running",
            "phase": "building",
            "action": "editing script_a.py",
        }
    )
    state.observe(
        {
            "event": "tui.board.admission",
            "task_id": "task-B",
            "run_id": "run-B",
            "objective": "build script B",
            "decision": "started in parallel",
            "phase": "building",
            "action": "editing script_b.py",
        }
    )

    state.observe(
        {
            "event": "run.log.completed",
            "task_id": "task-A",
            "run_id": "run-A",
            "status": "completed",
            "event_log_path": ".dan-super/runs/run-A/events.jsonl",
        }
    )

    row_a = state.find_board_row("task-A")
    row_b = state.find_board_row("task-B")
    assert row_a is not None
    assert row_b is not None
    assert row_a.status == "completed"
    assert row_b.status == "running"
    assert state.focused_board_id == "task-B"
    assert row_a.answer_lines
    assert not row_b.answer_lines


def test_super_tui_repeated_model_waits_do_not_create_default_visible_board_rows() -> None:
    state = super_tui.SuperTuiState()
    state.observe(
        {
            "event": "tui.board.admission",
            "task_id": "task-A",
            "objective": "build script A",
            "decision": "accepted; running",
            "phase": "building",
            "action": "editing script_a.py",
        }
    )
    initial_count = len(state.board_order)

    state.observe({"event": "model.requested", "round": 1, "model": "fake-model"})
    state.observe({"event": "model.requested", "round": 2, "model": "fake-model"})
    state.observe({"event": "super.heartbeat", "phase": "model", "detail": "round=2 model=fake tools=4"})

    assert len(state.board_order) == initial_count
    assert super_tui._tui_board_update_event_is_visible({"event": "model.requested"}) is False
    assert super_tui._tui_board_update_event_is_visible({"event": "super.heartbeat"}) is False


def test_super_tui_append_does_not_target_completed_board_row() -> None:
    state = super_tui.SuperTuiState()
    state.observe(
        {
            "event": "tui.board.admission",
            "task_id": "task-A",
            "objective": "build script A",
            "status": "completed",
            "phase": "done",
            "action": "run completed",
        }
    )

    result = super_tui._apply_tui_board_command(
        state,
        super_tui.TuiRunCommand(command="append", payload="task-A please add validation"),
    )

    assert "already completed" in result.message
    assert not result.event


def test_super_tui_async_admission_payload_targets_v2_background(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    parser = super_tui.build_parser()
    args = parser.parse_args(
        [
            "--workspace",
            str(workspace),
            "--server",
            "http://dan.test",
            "--async-agent",
            "--async-agent-backend",
            "deterministic",
            "--model",
            "fake-model",
        ]
    )
    super_tui._prepare_args(args, [])

    payload = super_tui._tui_async_admission_payload(
        args,
        workspace_root=workspace,
        text="build script_a.py",
        background=True,
    )

    assert payload["background"] is True
    assert payload["chat_request"]["message"] == "build script_a.py"
    assert payload["chat_request"]["mode"] == "agent"
    assert payload["chat_request"]["surface"] == "cli:super-tui"
    assert payload["chat_request"]["surface_context"]["workspace_root"] == str(workspace)
    assert payload["execute"]["backend"] == "deterministic"
    assert payload["execute"]["background"] is True
    assert payload["execute"]["profile_policy"]["model"] == "fake-model"


def test_super_tui_interactive_async_turn_records_admission_without_dispatch(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parser = super_tui.build_parser()
    args = parser.parse_args(
        [
            "--workspace",
            str(workspace),
            "--plain",
            "--async-agent",
            "--server",
            "http://dan.test",
            "--async-agent-backend",
            "deterministic",
        ]
    )
    super_tui._prepare_args(args, [])
    _patch_tui_route(monkeypatch, permission="write", complexity="complex", routed_with_model=True)
    inputs = iter(["build script_a.py", "/exit"])
    captured_payloads: list[dict] = []

    def fake_post(*, server_url, payload, timeout):
        captured_payloads.append(dict(payload))
        assert server_url == "http://dan.test"
        assert timeout == args.async_agent_timeout
        return {
            "status": "started",
            "admission": {
                "action": "start_parallel",
                "task_id": "task-A",
                "run_id": "run-A",
                "reason": "explicit target paths do not overlap active or queued runs",
            },
            "board": {
                "active_runs": [
                    {
                        "task_id": "task-A",
                        "run_id": "run-A",
                        "objective": "build script_a.py",
                        "status": "running",
                        "phase": "background_running",
                        "latest_summary": "Background Agent run started.",
                        "admission_action": "start_parallel",
                        "admission_reason": "explicit target paths do not overlap active or queued runs",
                        "owned_paths": ["script_a.py"],
                    }
                ],
                "queued_runs": [],
                "completed_runs": [],
            },
        }

    def fail_dispatch(*args, **kwargs):
        raise AssertionError("async admission should not block in direct local dispatch")

    monkeypatch.setattr(super_tui, "_read_interactive_line", lambda *args, **kwargs: next(inputs))
    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace_root: [])
    monkeypatch.setattr(super_tui, "_post_tui_async_admission", fake_post)
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_dispatch)

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    assert captured_payloads[0]["background"] is True
    stdout = capsys.readouterr().out
    assert "DAN · Answer:" in stdout
    assert "Started background task `task-A` to work on: build script_a.py" in stdout
    assert "The requested files do not overlap visible active work" in stdout
    assert "The visible scope is `script_a.py`." in stdout
    assert "Board:" not in stdout
    entries = super_tui._read_tui_transcript(workspace, limit=10)
    assert [entry.role for entry in entries[-2:]] == ["user", "assistant_progress"]
    assert entries[-1].metadata["async_agent"] is True
    assert entries[-1].metadata["tui_board_events"][0]["run_id"] == "run-A"


def test_super_tui_async_write_followup_starts_independent_without_lane_prompt(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", str(workspace), "--plain"])
    super_tui._prepare_args(args, [])
    args.target = "use the recommended options"
    args._tui_async_interactive = True
    super_tui._append_tui_transcript_entry(
        workspace,
        role="assistant_progress",
        text="Background run started.",
        metadata={
            "async_agent": True,
            "tui_board_event": {
                "event": "tui.board.admission",
                "task_id": "task-A",
                "run_id": "run-A",
                "objective": "build the phase plan",
                "status": "running",
                "phase": "background_running",
                "action": "model requested",
                "decision": "start_parallel",
            },
        },
    )
    _patch_tui_route(monkeypatch, permission="write", complexity="complex", routed_with_model=True)
    submitted: dict[str, object] = {}

    def fake_submit(run_args, *, workspace_root, text, forced_new=False, background=True):
        submitted.update(
            {
                "workspace_root": workspace_root,
                "text": text,
                "forced_new": forced_new,
                "background": background,
            }
        )
        return super_tui.TuiAsyncAdmissionResult(
            ok=True,
            message="started",
            response={
                "status": "started",
                "admission": {
                    "action": "start_parallel",
                    "task_id": "task-B",
                    "run_id": "run-B",
                },
                "task": {
                    "task_id": "task-B",
                    "run_id": "run-B",
                    "objective": "use the recommended options",
                },
                "board": {"active_runs": [], "queued_runs": [], "completed_runs": []},
            },
        )

    monkeypatch.setattr(super_tui, "_submit_tui_async_admission", fake_submit)
    monkeypatch.setattr(
        super_tui,
        "_run_tui_turn",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("async turn should not block")),
    )

    exit_code = super_tui._dispatch_tui_turn(args, parser)

    assert exit_code == 0
    assert submitted == {
        "workspace_root": workspace,
        "text": "use the recommended options",
        "forced_new": True,
        "background": True,
    }
    stdout = capsys.readouterr().out
    assert "Started background task `task-B` to work on: use the recommended options" in stdout
    assert "/append" not in submitted["text"]


def test_super_tui_forced_new_async_followup_does_not_surface_lane_question(tmp_path) -> None:
    from dan.server.chat_v2_async_core import mark_background_run_started

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", str(workspace), "--plain"])
    super_tui._prepare_args(args, [])

    first = super_tui._submit_tui_local_async_admission(
        args,
        workspace_root=workspace,
        text="build the Godot compile check",
        background=False,
    )
    first_run_id = str(first.response["admission"]["run_id"])
    store = super_tui._open_tui_local_async_store(workspace)
    mark_background_run_started(store, first_run_id, backend="test")

    second = super_tui._submit_tui_local_async_admission(
        args,
        workspace_root=workspace,
        text="try again with the Godot compilation verification",
        forced_new=True,
        background=False,
    )

    assert second.response["admission"]["action"] == "start_parallel"
    assert "Should this be appended" not in "\n".join(super_tui._tui_async_admission_compact_lines(second))


def test_super_tui_async_admission_compact_lines_include_task_objective() -> None:
    result = super_tui.TuiAsyncAdmissionResult(
        ok=True,
        message="started",
        response={
            "status": "started",
            "admission": {"action": "start_parallel", "task_id": "task-A", "run_id": "run-A"},
            "board": {
                "active_runs": [
                    {
                        "task_id": "task-A",
                        "run_id": "run-A",
                        "objective": "build tracking docs and launch the command-line smoke",
                    }
                ],
                "queued_runs": [],
                "completed_runs": [],
            },
        },
    )

    lines = super_tui._tui_async_admission_compact_lines(result)

    assert lines[0] == "Started background task `task-A` to work on: build tracking docs and launch the command-line smoke"
    assert any("composer stays open" in line for line in lines)


def test_super_tui_stop_shortcut_targets_visible_active_task(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", str(workspace), "--plain"])
    super_tui._prepare_args(args, [])
    super_tui._append_tui_transcript_entry(
        workspace,
        role="assistant_progress",
        text="Background run started.",
        metadata={
            "async_agent": True,
            "tui_board_event": {
                "event": "tui.board.admission",
                "task_id": "task-A",
                "run_id": "run-A",
                "objective": "build the phase plan",
                "status": "running",
                "phase": "background_running",
                "action": "model requested",
                "decision": "start_parallel",
            },
        },
    )
    submitted: dict[str, object] = {}

    def fake_submit(run_args, *, workspace_root, text, forced_new=False, background=True):
        submitted.update(
            {
                "workspace_root": workspace_root,
                "text": text,
                "forced_new": forced_new,
                "background": background,
            }
        )
        return super_tui.TuiAsyncAdmissionResult(
            ok=True,
            message="stopping",
            response={
                "status": "accepted",
                "admission": {
                    "action": "append_to_active",
                    "task_id": "task-A",
                    "run_id": "run-A",
                },
                "board": {"active_runs": [], "queued_runs": [], "completed_runs": []},
            },
        )

    monkeypatch.setattr(super_tui, "_submit_tui_async_admission", fake_submit)

    assert super_tui._request_tui_stop_from_shortcut(args, workspace, source="ctrl-c") is True
    assert submitted == {
        "workspace_root": workspace,
        "text": "/stop task-A",
        "forced_new": False,
        "background": False,
    }
    assert "DAN · Command:" in capsys.readouterr().out


def test_super_tui_default_async_turn_uses_local_store_without_server(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DAN_SERVER_URL", raising=False)
    monkeypatch.delenv("DAN_SUPER_TUI_ASYNC", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parser = super_tui.build_parser()
    args = parser.parse_args(
        [
            "--workspace",
            str(workspace),
            "--plain",
            "--async-agent-backend",
            "deterministic",
        ]
    )
    super_tui._prepare_args(args, [])
    _patch_tui_route(monkeypatch, permission="write", complexity="complex", routed_with_model=True)
    inputs = iter(["build script_a.py", "/exit"])
    started_runs: list[str] = []

    def fake_start_background(*, workspace_root, run_id, backend_name, overrides, remaining_continuations):
        assert workspace_root == workspace
        assert backend_name == "deterministic"
        assert overrides["metadata"]["surface"] == "super-tui"
        assert remaining_continuations == 8
        started_runs.append(run_id)

    def fail_post(*args, **kwargs):
        raise AssertionError("default TUI async admission should not require HTTP")

    def fail_dispatch(*args, **kwargs):
        raise AssertionError("local async admission should not fall back to blocking dispatch")

    monkeypatch.setattr(super_tui, "_read_interactive_line", lambda *args, **kwargs: next(inputs))
    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace_root: [])
    monkeypatch.setattr(super_tui, "_load_tui_path_suggestions", lambda workspace_root: [])
    monkeypatch.setattr(super_tui, "_start_tui_local_async_background_run", fake_start_background)
    monkeypatch.setattr(super_tui, "_post_tui_async_admission", fail_post)
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_dispatch)

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    assert started_runs
    stdout = capsys.readouterr().out
    assert "DAN · Answer:" in stdout
    assert "Started background task" in stdout
    assert "Board:" not in stdout
    store = super_tui._open_tui_local_async_store(workspace)
    assert store.get_run(started_runs[0]) is not None
    entries = super_tui._read_tui_transcript(workspace, limit=10)
    assert [entry.role for entry in entries[-2:]] == ["user", "assistant_progress"]
    assert entries[-1].metadata["async_agent"] is True
    assert entries[-1].metadata["status"] == "started"


def test_super_tui_background_completion_lines_are_human() -> None:
    run = Namespace(
        run_id="run-123456",
        task_id="task-abc",
        status="completed",
        latest_summary="Created docs/tracking.md and updated README.md.",
        metadata={"event_log_path": ".dan-super/runs/turn-01/events.jsonl"},
    )

    lines = super_tui._tui_background_completion_lines(run, [])

    assert lines[0] == (
        "Background run `task-abc` finished. Created docs/tracking.md and updated README.md."
    )
    assert "Trace: .dan-super/runs/turn-01/events.jsonl" in lines
    assert lines[-1] == "Use `/tasks` for the latest board."


def test_super_tui_plan_mode_renders_questions_without_dispatch(tmp_path, capsys, monkeypatch) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", str(tmp_path), "--plain"])
    super_tui._prepare_args(args, [])
    args._tui_transcript_workspace = str(tmp_path)

    def fake_draft(args, objective, workspace_root):
        del args
        assert objective == "refine the Godot development plan"
        assert workspace_root == tmp_path
        return (
            super_tui.TuiPlanDraft(
                summary="Refine the Godot plan before implementation.",
                questions=(
                    super_tui.TuiPlanQuestion(
                        question="What should the first playable slice prove?",
                        recommended="Validate movement, hit feedback, and one enemy loop.",
                        choices=("Movement feel", "Combat readability", "Level pacing"),
                        custom_label="Other: define the proof point",
                    ),
                ),
                next_step="Reply with `1A`, `1B`, or your own answer.",
            ),
            "",
        )

    monkeypatch.setattr(super_tui, "_run_tui_plan_model_draft", fake_draft)
    monkeypatch.setattr(
        super_tui,
        "_run_tui_turn",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("plan mode must not dispatch")),
    )

    exit_code = super_tui._dispatch_tui_turn(
        Namespace(**{**vars(args), "target": "/plan refine the Godot development plan"}),
        parser,
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "DAN · Plan:" in stdout
    assert "Refine the Godot plan before implementation." in stdout
    assert "What should the first playable slice prove?" in stdout
    assert "Recommended: Validate movement, hit feedback, and one enemy loop." in stdout
    assert "A. Movement feel" in stdout
    assert "D. Other: define the proof point" in stdout
    assert "No files changed." in stdout
    pending = super_tui._load_tui_pending_plan_state(tmp_path)
    assert pending is not None
    assert pending["objective"] == "refine the Godot development plan"


def test_super_tui_model_routed_plan_mode_does_not_start_background(
    tmp_path,
    monkeypatch,
) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(
        [
            "this game will use Godot; help me refine the development plan",
            "--workspace",
            str(tmp_path),
            "--plain",
        ]
    )
    super_tui._prepare_args(args, [])
    args._tui_transcript_workspace = str(tmp_path)
    observed: dict[str, object] = {}

    _patch_tui_route(monkeypatch, permission="mode", complexity="plan", routed_with_model=True)

    def fake_plan_mode(run_args, objective):
        del run_args
        observed["objective"] = objective
        return 0

    monkeypatch.setattr(
        super_tui,
        "_run_tui_plan_mode",
        fake_plan_mode,
    )
    monkeypatch.setattr(
        super_tui,
        "_submit_tui_async_admission",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("plan mode must not admit background work")),
    )
    monkeypatch.setattr(
        super_tui,
        "_run_tui_turn",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("plan mode must not dispatch executor")),
    )

    exit_code = super_tui._dispatch_tui_turn(args, parser)

    assert exit_code == 0
    assert observed["objective"] == "this game will use Godot; help me refine the development plan"


def test_super_tui_plan_reply_uses_pending_plan_state_without_routing(
    tmp_path,
    monkeypatch,
) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", str(tmp_path), "--plain"])
    super_tui._prepare_args(args, [])
    draft = super_tui.TuiPlanDraft(
        summary="Choose the game direction.",
        questions=(
            super_tui.TuiPlanQuestion(
                question="What should the first slice prove?",
                choices=("Movement", "Combat"),
            ),
        ),
        next_step="Reply with a choice.",
    )
    super_tui._write_tui_pending_plan_state(tmp_path, objective="refine the Godot plan", draft=draft)
    inputs = iter(["1A, 2D more the better", "/exit"])
    observed: dict[str, object] = {}

    monkeypatch.setattr(super_tui, "_read_interactive_line", lambda *args, **kwargs: next(inputs))
    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace_root: [])
    monkeypatch.setattr(super_tui, "_load_tui_path_suggestions", lambda workspace_root: [])
    monkeypatch.setattr(
        super_tui,
        "_route_tui_intent_with_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("plan replies must not be front-door routed")),
    )

    def fake_reply(run_args, user_reply, pending, run_parser):
        del run_args, run_parser
        observed["reply"] = user_reply
        observed["objective"] = pending["objective"]
        return 0

    monkeypatch.setattr(super_tui, "_run_tui_plan_reply", fake_reply)

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    assert observed == {
        "reply": "1A, 2D more the better",
        "objective": "refine the Godot plan",
    }


def test_super_tui_new_command_bypasses_pending_plan_reply(
    tmp_path,
    monkeypatch,
) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", str(tmp_path), "--plain", "--no-async-agent"])
    super_tui._prepare_args(args, [])
    draft = super_tui.TuiPlanDraft(
        summary="Choose the game direction.",
        questions=(
            super_tui.TuiPlanQuestion(
                question="What should the first slice prove?",
                choices=("Movement", "Combat"),
            ),
        ),
        next_step="Reply with a choice.",
    )
    super_tui._write_tui_pending_plan_state(tmp_path, objective="refine the Godot plan", draft=draft)
    inputs = iter(["/new implement the accepted plan", "/exit"])
    observed: dict[str, object] = {}

    monkeypatch.setattr(super_tui, "_read_interactive_line", lambda *args, **kwargs: next(inputs))
    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace_root: [])
    monkeypatch.setattr(super_tui, "_load_tui_path_suggestions", lambda workspace_root: [])
    monkeypatch.setattr(
        super_tui,
        "_run_tui_plan_reply",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("/new must not answer pending plan questions")),
    )

    def fake_dispatch(run_args, run_parser, *, force_live=False):
        del run_parser, force_live
        observed["target"] = run_args.target
        observed["forced_new"] = bool(getattr(run_args, "_tui_forced_new", False))
        return 0

    monkeypatch.setattr(super_tui, "_dispatch_tui_turn", fake_dispatch)

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    assert observed == {"target": "implement the accepted plan", "forced_new": True}
    assert super_tui._load_tui_pending_plan_state(tmp_path) is None


def test_super_tui_plan_reply_renders_and_clears_pending_state(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", str(tmp_path), "--plain"])
    super_tui._prepare_args(args, [])
    draft = super_tui.TuiPlanDraft(
        summary="Choose the game direction.",
        questions=(super_tui.TuiPlanQuestion(question="Pick direction.", choices=("A", "B")),),
        next_step="Reply with a choice.",
    )
    super_tui._write_tui_pending_plan_state(tmp_path, objective="refine the plan", draft=draft)
    pending = super_tui._load_tui_pending_plan_state(tmp_path)
    assert pending is not None
    monkeypatch.setattr(
        super_tui,
        "_run_tui_plan_reply_model",
        lambda run_args, plan, reply, root: (
            super_tui.TuiPlanReplyDecision(
                lines=("I will carry this direction forward.", "- First slice: Movement"),
                action="record_only",
            ),
            "",
        ),
    )

    exit_code = super_tui._run_tui_plan_reply(args, "1A", pending)

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "DAN · Plan:" in stdout
    assert "I will carry this direction forward." in stdout
    assert "No files changed." in stdout
    assert super_tui._load_tui_pending_plan_state(tmp_path) is None


def test_super_tui_plan_reply_can_model_authorize_async_execution(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", str(tmp_path), "--plain"])
    super_tui._prepare_args(args, [])
    draft = super_tui.TuiPlanDraft(
        summary="Choose the implementation direction.",
        questions=(super_tui.TuiPlanQuestion(question="Pick scope.", choices=("Small", "Broad")),),
        next_step="Reply with a choice.",
    )
    super_tui._write_tui_pending_plan_state(tmp_path, objective="refine the implementation plan", draft=draft)
    pending = super_tui._load_tui_pending_plan_state(tmp_path)
    assert pending is not None
    submitted: dict[str, object] = {}
    monkeypatch.setattr(
        super_tui,
        "_run_tui_plan_reply_model",
        lambda run_args, plan, reply, root: (
            super_tui.TuiPlanReplyDecision(
                lines=("I will start the agreed first slice now.",),
                action="start_execution",
                execution_objective="implement the agreed first slice",
            ),
            "",
        ),
    )

    def fake_submit(run_args, *, workspace_root, text, forced_new=False, background=True):
        submitted.update(
            {
                "workspace_root": workspace_root,
                "text": text,
                "forced_new": forced_new,
                "background": background,
            }
        )
        return super_tui.TuiAsyncAdmissionResult(
            ok=True,
            message="started",
            response={
                "status": "started",
                "admission": {
                    "action": "start_parallel",
                    "task_id": "task-A",
                    "run_id": "run-A",
                },
                "board": {"active_runs": [], "queued_runs": [], "completed_runs": []},
            },
        )

    monkeypatch.setattr(super_tui, "_submit_tui_async_admission", fake_submit)

    exit_code = super_tui._run_tui_plan_reply(args, "do as you recommend", pending, parser)

    assert exit_code == 0
    assert submitted == {
        "workspace_root": tmp_path,
        "text": "implement the agreed first slice",
        "forced_new": True,
        "background": True,
    }
    stdout = capsys.readouterr().out
    assert "I will start the agreed first slice now." in stdout
    assert "Started background task" in stdout
    assert super_tui._load_tui_pending_plan_state(tmp_path) is None


def test_super_tui_interactive_plan_command_does_not_start_background(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", str(tmp_path), "--plain"])
    super_tui._prepare_args(args, [])
    inputs = iter(["/plan refine the Godot development plan", "/exit"])
    seen: list[str] = []

    monkeypatch.setattr(super_tui, "_read_interactive_line", lambda *args, **kwargs: next(inputs))
    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace_root: [])
    monkeypatch.setattr(super_tui, "_load_tui_path_suggestions", lambda workspace_root: [])
    monkeypatch.setattr(
        super_tui,
        "_run_tui_plan_mode",
        lambda run_args, objective: seen.append(objective) or print("DAN · Plan: questions") or 0,
    )
    monkeypatch.setattr(
        super_tui,
        "_submit_tui_async_admission",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("plan mode must not admit background work")),
    )

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    assert seen == ["refine the Godot development plan"]
    assert "DAN · Plan: questions" in capsys.readouterr().out


def test_super_tui_one_shot_slash_help_bypasses_model_router(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["/help", "--workspace", str(tmp_path), "--plain"])
    super_tui._prepare_args(args, [])
    monkeypatch.setattr(
        super_tui,
        "_route_tui_intent_with_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("slash help must stay local")),
    )

    exit_code = super_tui._dispatch_tui_turn(args, parser)

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "DAN · Help:" in stdout
    assert "/stop or Esc" in stdout


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/new", "Usage: /new <objective>"),
        ("/append add validation", "No focused or running task is visible"),
        ("/stop", "No focused or running task is visible"),
    ],
)
def test_super_tui_one_shot_local_commands_bypass_model_router(
    tmp_path,
    capsys,
    monkeypatch,
    target: str,
    expected: str,
) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args([target, "--workspace", str(tmp_path), "--plain", "--no-async-agent"])
    super_tui._prepare_args(args, [])
    monkeypatch.setattr(
        super_tui,
        "_route_tui_intent_with_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local command must stay local")),
    )

    exit_code = super_tui._dispatch_tui_turn(args, parser)

    assert exit_code == 0
    assert expected in capsys.readouterr().out


def test_super_tui_one_shot_reset_bypasses_model_router(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".dan-super" / "state").mkdir(parents=True)
    parser = super_tui.build_parser()
    args = parser.parse_args(["/reset state", "--workspace", str(workspace), "--plain", "--no-async-agent"])
    super_tui._prepare_args(args, [])
    monkeypatch.setattr(
        super_tui,
        "_route_tui_intent_with_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("reset must stay local")),
    )

    exit_code = super_tui._dispatch_tui_turn(args, parser)

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "DAN · System:" in stdout
    assert "Visible TUI transcript preserved." in stdout


def test_super_tui_async_append_uses_server_control_path(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parser = super_tui.build_parser()
    args = parser.parse_args(
        [
            "--workspace",
            str(workspace),
            "--plain",
            "--async-agent",
            "--server",
            "http://dan.test",
        ]
    )
    super_tui._prepare_args(args, [])
    _patch_tui_route(monkeypatch, permission="write", complexity="complex", routed_with_model=True)
    inputs = iter(["/append add validation", "/exit"])
    captured_payloads: list[dict] = []

    def fake_post(*, server_url, payload, timeout):
        del server_url, timeout
        captured_payloads.append(dict(payload))
        return {
            "status": "accepted",
            "admission": {
                "action": "append_to_active",
                "task_id": "task-A",
                "run_id": "run-A",
                "queue_item_id": "queue-1",
                "queue_position": 1,
                "reason": "explicit append command targets the active run",
            },
            "board": {
                "active_runs": [
                    {
                        "task_id": "task-A",
                        "run_id": "run-A",
                        "objective": "build script_a.py",
                        "status": "running",
                        "phase": "background_running",
                        "latest_summary": "Append queued at checkpoint.",
                        "admission_action": "append_to_active",
                        "admission_reason": "explicit append command targets the active run",
                    }
                ],
                "queued_runs": [],
                "completed_runs": [],
            },
        }

    monkeypatch.setattr(super_tui, "_read_interactive_line", lambda *args, **kwargs: next(inputs))
    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace_root: [])
    monkeypatch.setattr(super_tui, "_post_tui_async_admission", fake_post)

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    assert captured_payloads[0]["background"] is False
    assert captured_payloads[0]["chat_request"]["message"] == "/append add validation"
    stdout = capsys.readouterr().out
    assert "queued that for `task-A`" in stdout
    assert "Board:" not in stdout


def test_super_tui_board_status_targets_one_task() -> None:
    state = super_tui.SuperTuiState()
    state.observe(
        {
            "event": "tui.board.admission",
            "task_id": "task-B",
            "run_id": "run-B",
            "objective": "build script B",
            "decision": "started in parallel",
            "phase": "building",
            "action": "editing script_b.py",
        }
    )

    text = super_tui._format_tui_board_status(state, target="run-B", width=80)

    assert text.startswith("Status: task-B")
    assert "task-B [running/building] editing script_b.py" in text
    assert "/status [task]" in text


def test_super_tui_task_overview_matches_displayed_short_id() -> None:
    state = super_tui.SuperTuiState()
    task_id = "ba9db3f3d7f8400aa9c2b49caa671999"
    state.observe(
        {
            "event": "tui.board.admission",
            "task_id": task_id,
            "run_id": "run-1234567890abcdef",
            "objective": "/new please make sure godot compiles",
            "decision": "start_parallel",
            "phase": "background_running",
            "action": "released",
            "changed_paths": ["godot/src/core/GameLoop.gd"],
        }
    )

    text = "\n".join(super_tui._format_tui_task_overview_lines(state, target="ba9db3f3", width=100))

    assert "No visible task matches" not in text
    assert "please make sure godot compiles [ba9db3f3]" in text
    assert "godot/src/core/GameLoop.gd" in text


def test_super_tui_board_narrow_rows_preserve_id_status_and_action() -> None:
    row = super_tui.TuiBoardRow(
        task_id="task-A",
        status="running",
        phase="building",
        objective="build a very long generated script with several secondary files",
        action="editing script_a.py",
    )

    lines = super_tui._format_tui_board_row(row, width=42)

    assert "task-A" in lines[0]
    assert "[running]" in lines[0]
    assert "editing script_a.py" in lines[0]
    assert all(len(line) <= 40 for line in lines)


def test_super_tui_rich_render_contains_panel_titles_when_rich_is_available() -> None:
    try:
        from rich.console import Console
    except ImportError:
        pytest.skip("rich not installed")

    state = super_tui.SuperTuiState(
        objective="build dashboard",
        workspace="/tmp/ws",
        status="running",
        phase="validation",
        event_log_path=".dan-super/runs/turn-01/events.jsonl",
    )
    state.changed_files.append("website/index.html")
    console = Console(file=io.StringIO(), force_terminal=False, width=100, record=True)
    console.print(state.rich_renderable())
    text = console.export_text()

    assert "DAN · Session:" in text
    assert "Recent Events" in text
    assert "Context: workspace=/tmp/ws" in text
    assert "Changed: website/index.html" in text
    assert "Trace: .dan-super/runs/turn-01/events.jsonl" in text


def test_super_tui_rich_and_plain_render_show_four_lane_sections() -> None:
    try:
        from rich.console import Console
    except ImportError:
        pytest.skip("rich not installed")

    lanes = ("simple read-only", "complex read-only", "simple write", "complex write")
    for lane in lanes:
        state = super_tui.SuperTuiState(
            objective=f"{lane} objective",
            workspace="/tmp/ws",
            status="completed",
            phase="done",
            mode_line=f"{lane} - test rationale",
        )
        state._record_progress(f"{lane} activity")
        state._record_answer(f"{lane} answer")
        state._record_result(f"{lane} result")

        plain = state.plain_snapshot()
        assert "Answer:" in plain
        assert f"{lane} answer" in plain
        assert "Activity:" not in plain

        console = Console(file=io.StringIO(), force_terminal=False, width=120, record=True)
        console.print(state.rich_renderable())
        rich_text = console.export_text()
        assert "Summary" in rich_text
        assert f"{lane} answer" in rich_text
        assert "Activity:" not in rich_text


def test_super_tui_rich_semantic_text_highlights_operational_terms() -> None:
    try:
        from rich.text import Text
    except ImportError:
        pytest.skip("rich not installed")

    line = "Tool completed: file_write - docs/plan.md -> failed $scaffold-research on kimi-k2.6"
    rendered = super_tui._rich_semantic_text(line)

    assert isinstance(rendered, Text)
    assert rendered.plain == line

    def has_style(term: str, expected: str) -> bool:
        start = rendered.plain.index(term)
        end = start + len(term)
        return any(
            span.start <= start and span.end >= end and expected in str(span.style)
            for span in rendered.spans
        )

    assert has_style("Tool completed", "green")
    assert has_style("file_write", "blue")
    assert has_style("docs/plan.md", "cyan")
    assert has_style("$scaffold-research", "magenta")
    assert has_style("failed", "red")
    assert has_style("kimi-k2.6", "cyan")


def test_super_tui_rich_semantic_text_highlights_stream_prefix_and_answer_label() -> None:
    try:
        from rich.text import Text
    except ImportError:
        pytest.skip("rich not installed")

    line = "dan: Answer: changed docs/todo.md with file_read -> passed $idea-cart"
    rendered = super_tui._rich_semantic_text(line)

    assert isinstance(rendered, Text)
    assert rendered.plain == line

    def has_style(term: str, expected: str) -> bool:
        start = rendered.plain.index(term)
        end = start + len(term)
        return any(
            span.start <= start and span.end >= end and expected in str(span.style)
            for span in rendered.spans
        )

    assert has_style("dan:", "cyan")
    assert has_style("Answer", "white")
    assert has_style("docs/todo.md", "cyan")
    assert has_style("file_read", "blue")
    assert has_style("passed", "green")
    assert has_style("$idea-cart", "magenta")


def test_super_tui_rich_semantic_text_dims_progress_but_keeps_answers_white() -> None:
    try:
        from rich.text import Text
    except ImportError:
        pytest.skip("rich not installed")

    progress = super_tui._rich_semantic_text("dan: Thinking through the next step for this project.")
    answer = super_tui._rich_semantic_text("This is the actual project summary.", base_style="white")

    assert isinstance(progress, Text)
    assert isinstance(answer, Text)
    assert "grey50" in str(progress.style)
    assert "white" in str(answer.style)


def test_super_tui_split_answer_preserves_bullets_and_long_text() -> None:
    long_text = "This paragraph should stay visible because the terminal renderer wraps the answer block. " * 6
    lines = super_tui._split_answer_lines(
        f"{long_text}\n- Pipeline: docs/plan.md\n- Next: inspect src/app.py"
    )

    assert lines[0].startswith("This paragraph should stay visible")
    assert len(lines[0]) > 220
    assert lines[1] == "- Pipeline: docs/plan.md"
    assert lines[2] == "- Next: inspect src/app.py"


def test_super_tui_stream_block_wraps_long_answer_lines() -> None:
    line = "This answer should wrap across several visible terminal rows instead of disappearing past the right edge. " * 3

    wrapped = super_tui._wrap_tui_stream_lines([line], width=72)

    assert len(wrapped) > 1
    assert " ".join(part.strip() for part in wrapped) == line.strip()


def test_super_tui_stream_answer_plain_renders_single_block(capsys) -> None:
    args = Namespace(plain=True, json=False, quiet_progress=False, raw_events=False)
    state = super_tui.SuperTuiState()
    state._record_answer("This is the project overview.")
    state._record_answer("- Pipeline: docs/plan.md")

    super_tui._emit_tui_stream_answer(args, state)

    stdout = capsys.readouterr().out
    assert stdout.count("DAN · Answer:") == 1
    assert "DAN · Answer: This is the project overview." not in stdout
    assert "  This is the project overview." in stdout
    assert "  - Pipeline: docs/plan.md" in stdout


def test_super_tui_transcript_summary_keeps_full_answer() -> None:
    state = super_tui.SuperTuiState(status="completed", phase="done")
    state._record_answer("This is the project overview.")
    state._record_answer("- Pipeline: docs/plan.md")

    summary = state.transcript_summary()

    assert "This is the project overview." in summary
    assert "- Pipeline: docs/plan.md" in summary


def test_super_tui_transcript_history_separates_roles_and_collapses_long_answers() -> None:
    long_answer = "This answer should stay visible in the conversation transcript. " * 8
    entry = super_tui.TuiTranscriptEntry(
        role="assistant_final",
        text=long_answer,
        created_at="",
        metadata={},
    )

    formatted = super_tui._format_transcript_line(entry)

    assert formatted.startswith("DAN:")
    assert "This answer should stay visible" in formatted
    assert len(formatted) < len(long_answer)
    assert "..." in formatted


def test_super_tui_transcript_history_collapses_async_board_entries() -> None:
    entry = super_tui.TuiTranscriptEntry(
        role="assistant_progress",
        text=(
            "Admitted task-123; background run started.\n"
            "Admission:\n"
            "Board:\n"
            "Active:\n"
            "- task-123 [running]\n"
            "Intervene: /tasks | /status"
        ),
        created_at="",
        metadata={
            "async_agent": True,
            "admission": {
                "action": "start_parallel",
                "task_id": "task-123",
            },
        },
    )

    formatted = super_tui._format_transcript_line(entry)

    assert formatted.startswith("DAN:")
    assert "Background work started" in formatted
    assert "Board:" not in formatted
    assert "Intervene:" not in formatted


def test_super_tui_stream_narrator_blocks_render_as_compact_lines(capsys) -> None:
    super_tui._print_tui_stream_block(
        "Narrator",
        [
            "The executor is checking README.md before making changes.",
            "No files have changed yet.",
        ],
        plain=True,
    )

    stdout = capsys.readouterr().out
    assert "DAN · Narrator: The executor is checking README.md before making changes." in stdout
    assert "  No files have changed yet." in stdout
    assert "DAN · No files have changed yet." not in stdout
    assert "╭" not in stdout


def test_super_tui_final_outcome_groups_figure_artifacts() -> None:
    state = super_tui.SuperTuiState(status="completed", phase="done")
    state.changed_files.append("docs/report.md")
    state.artifacts.append("figures/main-effect.png")
    state.artifacts.append("tables/regression.csv")

    outcome = "\n".join(state.final_outcome_lines())

    assert "Changed: docs/report.md" in outcome
    assert "Figures: figures/main-effect.png" in outcome
    assert "Tables: tables/regression.csv" in outcome


def test_super_tui_terminal_summary_keeps_many_answer_lines() -> None:
    state = super_tui.SuperTuiState(status="completed", phase="done")
    for index in range(30):
        state._record_answer(f"Answer detail {index}")

    visible = "\n".join(state.recent_event_lines())

    assert "Answer detail 0" in visible
    assert "Answer detail 29" in visible


def test_super_tui_static_terminal_render_omits_redundant_header(capsys) -> None:
    state = super_tui.SuperTuiState(status="completed", phase="done")
    state._record_answer("This is the final answer.")

    super_tui._render_static_state(state, plain=False)

    stdout = capsys.readouterr().out
    assert "This is the final answer." in stdout
    assert "DAN · Session:" not in stdout


def test_super_tui_working_clock_refreshes_during_blocking_model_wait(capsys, monkeypatch) -> None:
    args = Namespace(json=False, quiet_progress=False)
    current = {"value": 10.0}
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: current["value"])

    def callback() -> str:
        current["value"] = 12.0
        threading.Event().wait(0.3)
        return "done"

    result = super_tui._run_with_tui_working_clock(args, callback)

    stdout = capsys.readouterr().out
    assert result == "done"
    assert "Working 2s" in stdout
    assert "Working 0sWorking" not in stdout
    assert "dan: Working" not in stdout
    assert stdout.endswith("\n")
    assert "\x1b" not in stdout
    assert "[2K" not in stdout
    assert "[0m" not in stdout


def test_super_tui_background_clock_uses_line_safe_output(capsys, monkeypatch) -> None:
    args = Namespace(json=False, quiet_progress=False, _tui_background_dispatch=True)
    current = {"value": 10.0}
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: current["value"])
    monkeypatch.setattr(super_tui, "_tui_stdout_supports_control_sequences", lambda: True)

    def callback() -> str:
        current["value"] = 12.0
        threading.Event().wait(0.3)
        return "done"

    result = super_tui._run_with_tui_working_clock(args, callback, label="Thinking")

    stdout = capsys.readouterr().out
    assert result == "done"
    assert "Thinking 2s\n" in stdout
    assert "\x1b" not in stdout
    assert "[2K" not in stdout


def test_super_tui_background_narrator_wait_clock_uses_line_safe_output(capsys, monkeypatch) -> None:
    args = Namespace(
        json=False,
        quiet_progress=False,
        plain=True,
        _tui_background_dispatch=True,
    )
    state = super_tui.SuperTuiState(status="running", phase="model")
    state.started_at_monotonic = 10.0
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: 12.0)
    monkeypatch.setattr(super_tui, "_tui_stdout_supports_control_sequences", lambda: True)

    thread = threading.Thread(target=lambda: threading.Event().wait(0.3))
    thread.start()
    super_tui._wait_for_tui_narrator_model(args, state, thread)
    thread.join()

    stdout = capsys.readouterr().out
    assert "Preparing answer 2s\n" in stdout
    assert "\x1b" not in stdout
    assert "[2K" not in stdout


def test_super_tui_clock_labels_separate_routing_and_narrator_waits() -> None:
    assert super_tui._format_tui_clock_text("Working: 8s", label="Thinking") == "Thinking 8s"
    assert (
        super_tui._format_tui_clock_text("Working: 8s", label="Preparing answer")
        == "Preparing answer 8s"
    )


def test_super_tui_display_softens_internal_validation_scores() -> None:
    assert (
        super_tui._hide_internal_run_metadata_from_display_line("The run failed validation at 0.25.")
        == "The run failed its checks."
    )
    assert (
        super_tui._hide_internal_run_metadata_from_display_line("Validation: failed (0.25).")
        == "Checks: failed."
    )


def test_super_tui_narrative_timeline_coalesces_noisy_tools() -> None:
    state = super_tui.SuperTuiState()

    for row in [
        {
            "event": "run.log.started",
            "objective": "copy source files",
            "task_id": "super-dan-live:5",
            "workspace_root": "/tmp/ws",
        },
        {
            "event": "tool.completed",
            "tool_id": "file_read",
            "result": {"path": "scripts/a.py", "line_count": 12},
        },
        {
            "event": "tool.completed",
            "tool_id": "file_read",
            "result": {"path": "scripts/b.py", "line_count": 18},
        },
        {
            "event": "tool.started",
            "tool_id": "shell_command",
            "arguments": {"command": "cp source.py scripts/source.py"},
        },
        {
            "event": "tool.completed",
            "tool_id": "shell_command",
            "result": {"exit_code": 0, "stdout": "copied\n", "stderr": ""},
        },
    ]:
        state.observe(row)

    lines = state.recent_event_lines()
    joined = "\n".join(lines)
    assert "Progress:" not in joined
    assert "Round " not in joined
    assert "tool(s) available" not in joined
    assert "file_read" not in joined
    assert "shell_command" not in joined
    assert "You asked: copy source files" not in joined
    assert "Narrator:" in joined
    assert "Relevant context is available for copy source files" in joined
    assert "The terminal command finished; using that result for copy source files." in joined
    assert "Activity:" not in joined


def test_super_tui_narrator_reports_follow_executor_events() -> None:
    state = super_tui.SuperTuiState(mode_line="complex write - test")

    for row in [
        {
            "event": "run.log.started",
            "objective": "update the todo list",
            "task_id": "super-dan-live:7",
            "workspace_root": "/tmp/ws",
        },
        {
            "event": "tool.completed",
            "tool_id": "file_write",
            "result": {"path": "docs/todo.md"},
        },
        {
            "event": "live.validation.started",
        },
        {
            "event": "super.heartbeat",
            "phase": "validation",
            "detail": "read-only validator",
            "elapsed_seconds": 10,
        },
        {
            "event": "live.validation.completed",
            "passed": True,
            "overall_score": 0.92,
        },
        {
            "event": "run.log.completed",
            "status": "completed",
        },
    ]:
        state.observe(row)

    joined = "\n".join(state.narrator_lines)
    assert "Got it. Starting with the relevant context." not in joined
    assert "Changed docs/todo.md for update the todo list." in joined
    assert "Still working after 10s." in joined
    assert "Validation passed" in joined
    assert "Run finished successfully" in joined
    assert state.answer_lines[-1].startswith("Run finished successfully")
    visible = "\n".join(state.recent_event_lines())
    assert "Answer:" in visible
    assert "Outcome:" in visible
    assert visible.count("Run finished successfully") == 1


def test_super_tui_plain_renderer_suppresses_fallback_lines_after_narrator(capsys) -> None:
    renderer = super_tui.SuperTuiProgressRenderer(enabled=True, plain=True)

    with renderer:
        renderer(
            {
                "event": "run.log.started",
                "objective": "check docs",
                "task_id": "super-dan-live:8",
                "workspace_root": "/tmp/ws",
            }
        )
        renderer({"event": "provider.build.completed", "model": "fake-live-model"})
        renderer({"event": "tool.started", "tool_id": "file_read", "arguments": {"path": "README.md"}})
        renderer({"event": "tool.started", "tool_id": "file_read", "arguments": {"path": "docs/todo.md"}})

    stdout = capsys.readouterr().out
    assert "Got it. Starting with the relevant context." not in stdout
    assert stdout.count("Checking the relevant workspace context") == 1
    assert "Model provider ready." not in stdout
    assert "You asked:" not in stdout
    assert "Reading workspace context." not in stdout


def test_super_tui_executor_sidecar_prints_model_narration(tmp_path, capsys, monkeypatch) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["build docs", "--workspace", str(tmp_path), "--model", "fake-model", "--plain"])
    super_tui._prepare_args(args, [])
    args._tui_routed_with_model = True

    class FakeProvider:
        def __init__(self) -> None:
            self.kwargs = {}

        async def complete(self, **kwargs):
            self.kwargs = dict(kwargs)
            return CompletionResult(
                text="The executor has started from the project context and is checking what needs to change before it edits anything."
            )

    provider = FakeProvider()
    monkeypatch.setattr(super_tui, "_build_tui_narrator_live_provider", lambda args, model: provider)

    renderer = super_tui.SuperTuiProgressRenderer(enabled=True, objective="build docs", workspace=str(tmp_path), plain=True)
    renderer.configure_model_sidecar(args)
    with renderer:
        renderer(
            {
                "event": "run.log.started",
                "objective": "build docs",
                "task_id": "super-dan-live:11",
                "workspace_root": str(tmp_path),
            }
        )
        assert renderer._sidecar_thread is not None
        renderer._sidecar_thread.join(timeout=2)

    stdout = capsys.readouterr().out
    assert "DAN · Narrator:" in stdout
    assert "The executor has started from the project context" in stdout
    assert "You asked:" not in stdout
    assert "tools" not in provider.kwargs


def test_super_tui_executor_sidecar_discards_stale_model_narration(tmp_path, capsys, monkeypatch) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["build docs", "--workspace", str(tmp_path), "--model", "fake-model", "--plain"])
    super_tui._prepare_args(args, [])

    renderer = super_tui.SuperTuiProgressRenderer(enabled=True, objective="build docs", workspace=str(tmp_path), plain=True)
    renderer.state.observe(
        {
            "event": "run.log.started",
            "objective": "build docs",
            "task_id": "super-dan-live:12",
            "workspace_root": str(tmp_path),
        }
    )
    request = super_tui.NarratorRequest(
        request_id="sidecar-old",
        question="build docs",
        snapshot=renderer.state._narrator_snapshot(),
        surface="super-tui",
    )
    renderer.state.observe(
        {
            "event": "tool.completed",
            "tool_id": "file_read",
            "result": {"path": "README.md"},
        }
    )
    monkeypatch.setattr(
        super_tui,
        "_run_tui_narrator_model_text",
        lambda *args, **kwargs: ("This old narrator answer should not be printed.", ""),
    )

    renderer._run_model_sidecar(args, request, "progress")

    stdout = capsys.readouterr().out
    assert "This old narrator answer should not be printed" not in stdout
    assert renderer.state.narrator_reports[-1]["stale"] is True
    assert renderer.state.narrator_reports[-1]["source_snapshot_id"] == request.snapshot.snapshot_id


def test_super_tui_conversation_footer_shows_elapsed_working_time(monkeypatch) -> None:
    state = super_tui.SuperTuiState(status="running", phase="model")
    state.started_at_monotonic = 10.0
    state.timeline.append("I'm working on it.")
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: 72.0)

    assert state.recent_event_lines()[-1] == "Working: 1m 02s"

    state.status = "completed"
    state.ended_at_monotonic = 75.0
    assert state.recent_event_lines()[-1] == "Elapsed: 1m 05s"


def test_super_tui_renderer_clock_tick_prints_live_elapsed(capsys, monkeypatch) -> None:
    renderer = super_tui.SuperTuiProgressRenderer(enabled=True, plain=True)
    renderer.state.status = "running"
    renderer.state.phase = "model"
    renderer.state.started_at_monotonic = 10.0
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: 12.0)

    renderer._clock_tick_once()

    stdout = capsys.readouterr().out
    assert "Working 2s" in stdout
    assert "dan: Working" not in stdout


def test_super_tui_renderer_clock_tick_refreshes_one_line_when_not_plain(capsys, monkeypatch) -> None:
    renderer = super_tui.SuperTuiProgressRenderer(enabled=True, plain=False)
    renderer.state.status = "running"
    renderer.state.phase = "model"
    renderer.state.started_at_monotonic = 10.0
    current_time = {"value": 12.0}
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: current_time["value"])

    renderer._clock_tick_once()
    current_time["value"] = 13.0
    renderer._clock_tick_once()
    renderer._clear_clock_line()

    stdout = capsys.readouterr().out
    assert "Working 2s" in stdout
    assert "Working 3s" in stdout
    assert "Working 2sWorking 3s" not in stdout
    assert "dan: Working" not in stdout
    assert stdout.endswith("\n")
    assert "\x1b" not in stdout
    assert "[2K" not in stdout


def test_super_tui_renderer_quiet_clock_starts_narrator_sidecar(capsys, monkeypatch, tmp_path) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["merge data", "--workspace", str(tmp_path), "--model", "fake-model", "--plain"])
    super_tui._prepare_args(args, [])
    args._tui_routed_with_model = True
    calls: list[str] = []

    def fake_narrator(*_args, **kwargs):
        calls.append(str(kwargs.get("purpose") or ""))
        return (
            "The executor is still deciding how to inspect the data before it changes anything; no file update is visible yet.",
            "",
        )

    monkeypatch.setattr(super_tui, "_run_tui_narrator_model_text", fake_narrator)
    current_time = {"value": 22.0}
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: current_time["value"])

    renderer = super_tui.SuperTuiProgressRenderer(enabled=True, objective="merge data", workspace=str(tmp_path), plain=True)
    renderer.configure_model_sidecar(args)
    renderer.state.started_at_monotonic = 10.0
    renderer.state.observe(
        {
            "event": "run.log.started",
            "objective": "merge data",
            "task_id": "super-dan-live:13",
            "workspace_root": str(tmp_path),
        }
    )
    renderer.state.observe({"event": "model.requested", "round": 1, "model": "fake-model"})

    renderer._clock_tick_once()
    assert renderer._sidecar_thread is not None
    renderer._sidecar_thread.join(timeout=2)

    stdout = capsys.readouterr().out
    assert calls == ["executor-heartbeat"]
    assert "DAN · Narrator:" in stdout
    assert "still deciding how to inspect the data" in stdout
    assert "Working 12s" in stdout
    assert "dan: Working" not in stdout


def test_super_tui_renderer_quiet_narrator_heartbeat_is_throttled(monkeypatch, tmp_path) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["merge data", "--workspace", str(tmp_path), "--model", "fake-model", "--plain"])
    super_tui._prepare_args(args, [])
    args._tui_routed_with_model = True
    calls: list[str] = []

    def fake_narrator(*_args, **kwargs):
        calls.append(str(kwargs.get("purpose") or ""))
        return ("Still waiting on the model decision from the current snapshot.", "")

    monkeypatch.setattr(super_tui, "_run_tui_narrator_model_text", fake_narrator)
    current_time = {"value": 22.0}
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: current_time["value"])

    renderer = super_tui.SuperTuiProgressRenderer(enabled=True, objective="merge data", workspace=str(tmp_path), plain=True)
    renderer.configure_model_sidecar(args)
    renderer.state.started_at_monotonic = 10.0
    renderer.state.observe(
        {
            "event": "run.log.started",
            "objective": "merge data",
            "task_id": "super-dan-live:14",
            "workspace_root": str(tmp_path),
        }
    )

    renderer._clock_tick_once()
    assert renderer._sidecar_thread is not None
    renderer._sidecar_thread.join(timeout=2)
    current_time["value"] = 26.0
    renderer._clock_tick_once()
    if renderer._sidecar_thread is not None:
        renderer._sidecar_thread.join(timeout=2)

    assert calls == ["executor-heartbeat"]


def test_super_tui_renderer_skips_duplicate_quiet_narrator_snapshot(monkeypatch, tmp_path) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["merge data", "--workspace", str(tmp_path), "--model", "fake-model", "--plain"])
    super_tui._prepare_args(args, [])
    args._tui_routed_with_model = True
    calls: list[str] = []

    def fake_narrator(*_args, **kwargs):
        calls.append(str(kwargs.get("purpose") or ""))
        return ("Still waiting on the same visible model decision.", "")

    monkeypatch.setattr(super_tui, "_run_tui_narrator_model_text", fake_narrator)
    current_time = {"value": 22.0}
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: current_time["value"])

    renderer = super_tui.SuperTuiProgressRenderer(enabled=True, objective="merge data", workspace=str(tmp_path), plain=True)
    renderer.configure_model_sidecar(args)
    renderer.state.started_at_monotonic = 10.0
    renderer.state.observe(
        {
            "event": "run.log.started",
            "objective": "merge data",
            "task_id": "super-dan-live:15",
            "workspace_root": str(tmp_path),
        }
    )
    renderer.state.observe({"event": "model.requested", "round": 1, "model": "fake-model"})

    renderer._clock_tick_once()
    assert renderer._sidecar_thread is not None
    renderer._sidecar_thread.join(timeout=2)
    current_time["value"] = 45.0
    renderer._clock_tick_once()
    if renderer._sidecar_thread is not None:
        renderer._sidecar_thread.join(timeout=2)

    assert calls == ["executor-heartbeat"]


def test_super_tui_renderer_suppresses_deterministic_heartbeat_when_model_sidecar_enabled(
    capsys,
    monkeypatch,
    tmp_path,
) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["merge data", "--workspace", str(tmp_path), "--model", "fake-model", "--plain"])
    super_tui._prepare_args(args, [])
    args._tui_routed_with_model = True
    monkeypatch.setattr(super_tui, "_run_tui_narrator_model_text", lambda *args, **kwargs: ("", ""))

    renderer = super_tui.SuperTuiProgressRenderer(enabled=True, objective="merge data", workspace=str(tmp_path), plain=True)
    renderer.configure_model_sidecar(args)
    with renderer:
        renderer(
            {
                "event": "run.log.started",
                "objective": "merge data",
                "task_id": "super-dan-live:16",
                "workspace_root": str(tmp_path),
            }
        )
        if renderer._sidecar_thread is not None:
            renderer._sidecar_thread.join(timeout=2)
        renderer({"event": "super.heartbeat", "phase": "model", "elapsed_seconds": 10})
        if renderer._sidecar_thread is not None:
            renderer._sidecar_thread.join(timeout=2)

    stdout = capsys.readouterr().out
    assert "Still working after" not in stdout


def test_super_tui_raw_events_mode_keeps_debug_telemetry() -> None:
    state = super_tui.SuperTuiState(debug_events=True)

    state.observe(
        {
            "event": "tool.started",
            "tool_id": "file_read",
            "worker_id": "super-dan.live.general-builder",
            "arguments": {"path": "README.md"},
        }
    )

    lines = state.recent_event_lines()
    assert lines == [
        "tool.started | tool_id=file_read | worker_id=super-dan.live.general-builder | path=README.md"
    ]


def test_super_tui_workspace_check_summary_hides_raw_payload() -> None:
    state = super_tui.SuperTuiState()

    state.observe(
        {
            "event": "tool.completed",
            "tool_id": "workspace_check",
            "result": {
                "check": "exists",
                "passed": True,
                "results": [
                    {
                        "path": "/tmp/ws/docs/todo.md",
                        "exists": True,
                        "is_file": True,
                    }
                ],
            },
        }
    )

    output = "\n".join(state.activity_lines)
    assert "Workspace context checked" in output
    assert "exists passed: /tmp/ws/docs/todo.md" in output
    assert "results" not in output
    assert "{" not in output


def test_super_tui_transcript_history_renders_before_composer(tmp_path, capsys, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    super_tui._append_tui_transcript_entry(workspace, role="user", text="first task")
    super_tui._append_tui_transcript_entry(
        workspace,
        role="assistant_final",
        text="Run completed; trace .dan-super/runs/turn-01/events.jsonl.",
    )
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", str(workspace), "--plain"])
    super_tui._prepare_args(args, [])

    def fake_prompt(prompt, *, commands, skills, paths=()):
        del commands, skills, paths
        print(prompt, end="")
        raise EOFError

    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace: [])
    monkeypatch.setattr(super_tui, "_read_interactive_line", fake_prompt)

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Conversation" in stdout
    assert "You: first task" in stdout
    assert "DAN: Run completed; trace .dan-super/runs/turn-01/events.jsonl." in stdout
    assert stdout.index("Conversation") < stdout.index("Message")


def test_super_tui_run_turn_persists_assistant_transcript_summary(
    tmp_path,
    monkeypatch,
) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(
        [
            "build a dashboard",
            "--workspace",
            str(tmp_path),
            "--live",
            "--plain",
        ]
    )
    super_tui._prepare_args(args, [])
    args._tui_transcript_workspace = str(tmp_path)

    def fake_run(args, parser):
        del parser
        renderer = args._progress_renderer_factory(enabled=True, args=args)
        renderer(
            {
                "event": "run.log.started",
                "objective": args.target,
                "task_id": "super-dan-live:9",
                "workspace_root": str(tmp_path),
            }
        )
        renderer(
            {
                "event": "tool.completed",
                "tool_id": "file_write",
                "result": {"path": "website/index.html"},
            }
        )
        args._live_report_printer(
            object(),
            {
                "status": "completed",
                "files": ["website/index.html"],
                "validation": {"passed": True, "overall_score": 0.9},
                "event_log_path": str(tmp_path / ".dan-super" / "runs" / "turn-09" / "events.jsonl"),
            },
            verbose=False,
        )
        return 0

    monkeypatch.setattr(super_tui.super_cli, "_run_super_turn", fake_run)

    exit_code = super_tui._run_tui_turn(args, parser, force_live=True)

    assert exit_code == 0
    entries = super_tui._read_tui_transcript(tmp_path)
    assert len(entries) == 1
    assert entries[0].role == "assistant_final"
    assert "Run finished successfully" in entries[0].text
    assert "website/index.html" in entries[0].text
    assert "turn-09/events.jsonl" in entries[0].text
    assert entries[0].metadata["narrator_final_summary"].startswith("Run finished successfully")


def test_super_tui_failed_turn_replays_latest_event_log_without_live_report(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(
        [
            "repair this workspace",
            "--workspace",
            str(tmp_path),
            "--live",
            "--plain",
            "--quiet-progress",
        ]
    )
    super_tui._prepare_args(args, ["--live", "--quiet-progress"])
    args._tui_transcript_workspace = str(tmp_path)
    event_log = tmp_path / ".dan-super" / "runs" / "turn-01" / "events.jsonl"

    def fake_run(args, parser):
        del args, parser
        _write_event_log(
            event_log,
            [
                {
                    "event": "run.log.started",
                    "objective": "repair this workspace",
                    "task_id": "super-dan-live:1",
                    "workspace_root": str(tmp_path),
                },
                {
                    "event": "run.log.failed",
                    "status": "failed",
                    "error_type": "APIConnectionError",
                    "error": "Connection error.",
                    "event_log_path": str(event_log),
                },
            ],
        )
        return 1

    monkeypatch.setattr(super_tui.super_cli, "_run_super_turn", fake_run)

    exit_code = super_tui._run_tui_turn(args, parser, force_live=True)

    assert exit_code == 1
    stdout = capsys.readouterr().out
    assert "DAN · Answer:" in stdout
    assert "APIConnectionError: Connection error" in stdout
    assert "Trace:" in stdout
    assert str(event_log) in stdout
    entries = super_tui._read_tui_transcript(tmp_path)
    assert "APIConnectionError: Connection error" in entries[-1].text
    assert str(event_log) in entries[-1].text


def test_super_tui_model_routed_run_uses_model_final_answer(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(
        [
            "help me review, if there are two readmes consolidate them",
            "--workspace",
            str(tmp_path),
            "--model",
            "fake-model",
            "--live",
            "--plain",
        ]
    )
    super_tui._prepare_args(args, [])
    args._tui_routed_with_model = True

    class FakeProvider:
        def __init__(self) -> None:
            self.kwargs = {}

        async def complete(self, **kwargs):
            self.kwargs = dict(kwargs)
            return CompletionResult(
                text=(
                    "I consolidated the README content into the main `README.md` and kept the file-map in sync. "
                    "Validation passed, so the project now has a single README entry point."
                )
            )

    provider = FakeProvider()

    def fake_run(run_args, parser):
        del parser
        renderer = run_args._progress_renderer_factory(enabled=True, args=run_args)
        renderer(
            {
                "event": "run.log.started",
                "objective": run_args.target,
                "task_id": "super-dan-live:10",
                "workspace_root": str(tmp_path),
                "requested_model": "fake-model",
            }
        )
        renderer(
            {
                "event": "tool.completed",
                "tool_id": "file_write",
                "result": {"path": "README.md"},
            }
        )
        run_args._live_report_printer(
            object(),
            {
                "status": "completed",
                "files": ["README.md"],
                "validation": {"passed": True, "overall_score": 0.88},
            },
            verbose=False,
        )
        return 0

    monkeypatch.setattr(super_tui.super_cli, "_run_super_turn", fake_run)
    monkeypatch.setattr(
        super_tui,
        "_build_tui_final_answer_live_provider",
        lambda args, model: provider,
    )

    exit_code = super_tui._run_tui_turn(args, parser, force_live=True)

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "I consolidated the README content into the main" in stdout
    assert "README.md" in stdout
    assert "Completed help me review" not in stdout
    assert "tools" not in provider.kwargs


def test_super_tui_reset_state_preserves_visible_transcript(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".dan-super" / "state").mkdir(parents=True)
    super_tui._append_tui_transcript_entry(workspace, role="user", text="keep this")

    message = super_tui._reset_tui_context(workspace, "state")

    assert "Visible TUI transcript preserved." in message
    assert super_tui._read_tui_transcript(workspace)[0].text == "keep this"


def test_super_tui_plain_fallback_when_rich_unavailable(tmp_path, capsys, monkeypatch) -> None:
    event_log = tmp_path / "events.jsonl"
    _write_event_log(
        event_log,
        [
            {"event": "run.log.started", "objective": "build", "workspace_root": "/tmp/ws"},
            {"event": "run.log.completed", "status": "completed"},
        ],
    )
    monkeypatch.setattr(super_tui, "_try_import_rich", lambda: (None, None))

    exit_code = super_tui.main(["--event-log", str(event_log)])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Answer:" in stdout
    assert "Run finished successfully" in stdout


def test_super_tui_status_missing_event_log_is_actionable(tmp_path, capsys) -> None:
    exit_code = super_tui.main(["--event-log", str(tmp_path / "missing.jsonl"), "--status"])

    assert exit_code == 2
    assert "event log not found:" in capsys.readouterr().err


def test_super_tui_help_lists_first_slice_commands_without_claiming_steering(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        super_tui.main(["--help"])

    assert exc.value.code == 0
    stdout = capsys.readouterr().out
    assert "dan-super-tui" in stdout
    assert "--event-log" in stdout
    assert "--raw-events" in stdout
    assert "--workspace" in stdout
    assert "--status" in stdout
    assert "/tasks" in stdout
    assert "/new" in stdout
    assert "/append" in stdout
    assert "/pause" in stdout
    assert "/resume" in stdout
    assert "/stop" in stdout
    assert "/continue" not in stdout
    assert "/cancel" not in stdout
    assert "/focus" in stdout
    assert "/status" in stdout
    assert "/skills" in stdout
    assert "/help" in stdout
    assert "suggestions" in stdout
    assert "prompt_toolkit" in stdout
    assert "local V2" in stdout
    assert "async admission" in stdout


def test_super_tui_completion_candidates_include_commands_and_skills() -> None:
    skills = [
        super_tui.TuiSkillSuggestion(
            token="idea-cart",
            name="idea-cart",
            description="Capture important ideas",
            source_scope="extra",
        ),
        super_tui.TuiSkillSuggestion(
            token="frontend-design",
            name="frontend-design",
            description="Frontend design guidance",
            source_scope="extra",
        ),
        super_tui.TuiSkillSuggestion(
            token="scaffold-dev",
            name="scaffold-dev",
            description="Create developer tracking docs",
            source_scope="extra",
        ),
    ]

    paths = [
        super_tui.TuiPathSuggestion("README.md", "report"),
        super_tui.TuiPathSuggestion("docs/todo.md", "report"),
    ]

    slash = super_tui._completion_candidates("/", skills=skills, paths=paths)
    assert "/tasks" in {item.value for item in slash}
    assert "/status" in {item.value for item in slash}
    assert "/inside" in {item.value for item in slash}
    assert "/new" in {item.value for item in slash}
    assert "/skills" in {item.value for item in slash}
    assert "/stop" in {item.value for item in slash}
    assert "/cancel" not in {item.value for item in slash}
    assert "/continue" not in {item.value for item in slash}

    command_prefix = super_tui._completion_candidates("/sk", skills=skills, paths=paths)
    assert [item.value for item in command_prefix] == ["/skills"]

    bare_dollar = super_tui._completion_candidates("$", skills=skills, paths=paths)
    assert [item.value for item in bare_dollar] == ["$idea-cart", "$frontend-design", "$scaffold-dev"]
    assert bare_dollar[0].start_position == -1

    dollar = super_tui._completion_candidates("$ide", skills=skills, paths=paths)
    assert [item.value for item in dollar] == ["$idea-cart"]
    assert dollar[0].start_position == -4
    assert "Capture important ideas" in dollar[0].meta
    substring_skill = super_tui._completion_candidates("$dev", skills=skills, paths=paths)
    assert [item.value for item in substring_skill] == ["$scaffold-dev"]
    assert substring_skill[0].start_position == -4
    assert super_tui._completion_candidates("$5", skills=skills, paths=paths) == []

    bare_path = super_tui._completion_candidates("@", skills=skills, paths=paths)
    assert [item.value for item in bare_path] == ["@README.md", "@docs/todo.md"]
    assert bare_path[0].start_position == -1

    path_prefix = super_tui._completion_candidates("review @docs", skills=skills, paths=paths)
    assert [item.value for item in path_prefix] == ["@docs/todo.md"]
    assert path_prefix[0].start_position == -5


def test_super_tui_visible_command_list_hides_duplicate_aliases() -> None:
    visible = {command for command, _meta in super_tui._TUI_COMMANDS}

    assert {"/progress", "/inside", "/stop", "/tasks", "/status", "/append"} <= visible
    assert "/last" not in visible
    assert "/trace" not in visible
    assert "/cancel" not in visible
    assert "/continue" not in visible
    assert "/queues" not in visible

    assert super_tui._parse_tui_run_command("/cancel task-A") is not None
    assert super_tui._parse_tui_run_command("/continue after tests pass") is not None


def test_super_tui_prompt_enter_accepts_partial_completion() -> None:
    class Document:
        text_before_cursor = "/ta"

    class Buffer:
        document = Document()
        complete_state = None

        def __init__(self) -> None:
            self.applied = ""

        def apply_completion(self, completion) -> None:
            start = len(self.document.text_before_cursor) + completion.start_position
            self.applied = self.document.text_before_cursor[:start] + completion.text

    buffer = Buffer()

    assert super_tui._apply_prompt_completion_if_available(buffer)
    assert buffer.applied == "/tasks"


def test_super_tui_path_suggestions_include_workspace_files_and_figures(tmp_path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "figures").mkdir()
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    (tmp_path / "docs" / "todo.md").write_text("todo", encoding="utf-8")
    (tmp_path / "figures" / "plot.png").write_text("not real png", encoding="utf-8")

    values = {item.value: item.meta for item in super_tui._load_tui_path_suggestions(tmp_path)}

    assert "README.md" in values
    assert "docs/" in values
    assert values["figures/plot.png"] == "figures"


def test_super_tui_prompt_toolkit_keybindings_open_completion_menu() -> None:
    try:
        from prompt_toolkit.key_binding import KeyBindings
    except ImportError:
        pytest.skip("prompt_toolkit not installed")

    bindings = super_tui._build_prompt_toolkit_key_bindings()

    assert isinstance(bindings, KeyBindings)
    keys = {tuple(binding.keys) for binding in bindings.bindings}
    assert ("/",) in keys
    assert ("$",) in keys
    assert ("@",) in keys
    assert ("up",) in keys
    assert ("down",) in keys
    assert ("escape",) in keys
    assert ("enter",) in keys or any(str(key).lower().endswith("controlm") for row in keys for key in row)


def test_super_tui_prompt_toolbar_is_compact() -> None:
    text = "".join(fragment for _style, fragment in super_tui._tui_prompt_bottom_toolbar())

    assert "Enter send" in text
    assert "Esc stop" in text
    assert "$ skills" in text
    assert "@ files" in text
    assert "/append inserts" not in text


def test_super_tui_prompt_toolkit_session_builds_with_installed_version(tmp_path) -> None:
    try:
        from prompt_toolkit import PromptSession
    except ImportError:
        pytest.skip("prompt_toolkit not installed")

    session = super_tui._build_prompt_toolkit_session(
        commands=super_tui._TUI_COMMANDS,
        skills=[
            super_tui.TuiSkillSuggestion(
                token="idea-cart",
                name="idea-cart",
                description="Capture important ideas",
            )
        ],
        history_path=tmp_path / "prompt-history.txt",
    )

    assert isinstance(session, PromptSession)
    assert session.history is not None


def test_super_tui_prompt_stdout_bridge_preserves_ansi(monkeypatch, tmp_path) -> None:
    try:
        import prompt_toolkit.patch_stdout as patch_stdout_module
    except ImportError:
        pytest.skip("prompt_toolkit not installed")

    class FakeSession:
        def prompt(self, *_args, **_kwargs) -> str:
            return "typed text"

    raw_values: list[bool] = []

    @contextlib.contextmanager
    def fake_patch_stdout(*, raw: bool = False):
        raw_values.append(raw)
        yield

    monkeypatch.setattr(super_tui.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(super_tui, "_build_prompt_toolkit_session", lambda **_kwargs: FakeSession())
    monkeypatch.setattr(patch_stdout_module, "patch_stdout", fake_patch_stdout)

    result = super_tui._read_interactive_line(
        "super-tui> ",
        commands=super_tui._TUI_COMMANDS,
        skills=[],
        paths=[],
        workspace_root=tmp_path,
    )

    assert result == "typed text"
    assert raw_values == [True]


def test_super_tui_run_command_parser_is_honest_for_direct_local_tui() -> None:
    append = super_tui._parse_tui_run_command("/append use the blue theme")
    assert append is not None
    assert append.command == "append"
    assert append.payload == "use the blue theme"
    assert "async V2 active runs" in append.message

    cancel = super_tui._parse_tui_run_command("/cancel")
    assert cancel is not None
    assert cancel.command == "cancel"
    assert "not wired into direct local Super DAN runs yet" in cancel.message

    stop = super_tui._parse_tui_run_command("/stop task-A")
    assert stop is not None
    assert stop.command == "cancel"
    assert stop.payload == "task-A"
    assert "Stop/cancel" in stop.message

    resume = super_tui._parse_tui_run_command("/resume task-A")
    assert resume is not None
    assert resume.command == "resume"
    assert resume.payload == "task-A"
    assert "direct local TUI backend" in resume.message

    focus = super_tui._parse_tui_run_command("/focus task-B")
    assert focus is not None
    assert focus.command == "focus"
    assert focus.payload == "task-B"
    assert "Use /tasks" in focus.message

    assert super_tui._parse_tui_run_command("/status") is None


def test_super_tui_skill_list_command_renders_available_mentions(capsys, monkeypatch) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", "/tmp/ws", "--plain", "--no-async-agent"])
    super_tui._prepare_args(args, [])
    skills = [
        super_tui.TuiSkillSuggestion(
            token="idea-cart",
            name="idea-cart",
            description="Capture important ideas",
            source_scope="extra",
        )
    ]
    prompts = iter(["/skills ide"])

    def fake_prompt(prompt, *, commands, skills, paths=()):
        del commands, skills, paths
        print(prompt, end="")
        try:
            return next(prompts)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace: skills)
    monkeypatch.setattr(super_tui, "_read_interactive_line", fake_prompt)

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Available skill mentions:" in stdout
    assert "$idea-cart" in stdout
    assert "Capture important ideas" in stdout


def test_super_tui_standalone_skill_mention_selects_without_running(capsys, monkeypatch) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", "/tmp/ws", "--plain"])
    super_tui._prepare_args(args, [])
    skills = [
        super_tui.TuiSkillSuggestion(
            token="idea-cart",
            name="idea-cart",
            description="Capture important ideas",
            source_scope="extra",
        )
    ]
    prompts = iter(["$idea-cart"])

    def fake_prompt(prompt, *, commands, skills, paths=()):
        del commands, skills, paths
        print(prompt, end="")
        try:
            return next(prompts)
        except StopIteration:
            raise EOFError

    def fail_run(*args, **kwargs):
        raise AssertionError("standalone skill mention must not start a run")

    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace: skills)
    monkeypatch.setattr(super_tui, "_render_static_state", lambda state, *, plain: print("<idle-panel>"))
    monkeypatch.setattr(super_tui, "_read_interactive_line", fake_prompt)
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_run)

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Selected skill: $idea-cart" in stdout
    assert "Add objective text after the mention" in stdout


def test_super_tui_leading_skill_mention_is_stripped_and_recorded(capsys, monkeypatch) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", "/tmp/ws", "--plain", "--no-async-agent"])
    super_tui._prepare_args(args, [])
    skills = [
        super_tui.TuiSkillSuggestion(
            token="idea-cart",
            name="idea-cart",
            description="Capture important ideas",
            source_scope="extra",
        )
    ]
    prompts = iter(["$idea-cart capture the open questions"])
    observed: dict[str, object] = {}

    def fake_prompt(prompt, *, commands, skills, paths=()):
        del commands, skills, paths
        print(prompt, end="")
        try:
            return next(prompts)
        except StopIteration:
            raise EOFError

    def fake_run(turn_args, parser, *, force_live):
        del parser
        observed["target"] = turn_args.target
        observed["mentions"] = turn_args._tui_selected_skill_mentions
        observed["force_live"] = force_live
        return 0

    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace: skills)
    monkeypatch.setattr(super_tui, "_render_static_state", lambda state, *, plain: print("<idle-panel>"))
    monkeypatch.setattr(super_tui, "_read_interactive_line", fake_prompt)
    monkeypatch.setattr(super_tui, "_run_tui_turn", fake_run)
    _patch_tui_route(monkeypatch, permission="write", complexity="complex")

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    assert observed == {
        "target": "capture the open questions",
        "mentions": ["idea-cart"],
        "force_live": True,
    }
    assert "Selected skill: $idea-cart; objective: capture the open questions" in capsys.readouterr().out


def test_super_tui_selected_skill_passes_core_metadata_without_surface_preflight(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "research-project"
    skill_root = tmp_path / "skills" / "scaffold-research"
    scripts = skill_root / "scripts"
    scripts.mkdir(parents=True)
    skill_path = skill_root / "SKILL.md"
    skill_path.write_text("# Scaffold Research\n", encoding="utf-8")
    (scripts / "scaffold_research_docs.py").write_text(
        "\n".join(
            [
                "import sys",
                "from pathlib import Path",
                "root = Path(sys.argv[1])",
                "(root / 'docs').mkdir(parents=True, exist_ok=True)",
                "(root / 'docs' / 'research-questions.md').write_text('# Research Questions\\n')",
                "(root / 'AGENTS.md').write_text('# Agents\\n')",
                "print('summary: created=2 overwritten=0 skipped=0')",
            ]
        ),
        encoding="utf-8",
    )
    catalog = [
        {
            "id": "scaffold_research",
            "name": "scaffold-research",
            "description": "Create research scaffold",
            "source_path": str(skill_path),
            "source_scope": "test",
            "content": "Create the research scaffold.",
        }
    ]
    observed: dict[str, object] = {}

    def fake_run(args, parser):
        del parser
        observed["target"] = args.target
        observed["mentions"] = list(args._selected_skill_mentions)
        observed["surface_mentions"] = list(args._tui_selected_skill_mentions)
        assert not hasattr(args, "_tui_skill_preflight_notes")
        assert not (workspace / "AGENTS.md").exists()
        assert not (workspace / "docs" / "research-questions.md").exists()
        return 0

    monkeypatch.setattr(skill_invocation, "load_skill_catalog", lambda workspace_root: catalog)
    monkeypatch.setattr(super_tui.super_cli, "_run_super_turn", fake_run)
    _patch_tui_route(monkeypatch, permission="write", complexity="complex")

    exit_code = super_tui.main(
        [
            "$scaffold-research build tracking docs",
            "--workspace",
            str(workspace),
            "--live",
            "--plain",
        ]
    )

    assert exit_code == 0
    assert observed["target"] == "build tracking docs"
    assert observed["mentions"] == ["scaffold-research"]
    assert observed["surface_mentions"] == ["scaffold-research"]
    assert "Selected skill: $scaffold-research; objective: build tracking docs" in capsys.readouterr().out


def test_super_tui_selected_skill_bypasses_exact_simple_write_helper(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    skill_root = tmp_path / "skills" / "scaffold-dev"
    skill_root.mkdir(parents=True)
    skill_path = skill_root / "SKILL.md"
    skill_path.write_text("# Scaffold Dev\n", encoding="utf-8")
    catalog = [
        {
            "id": "scaffold_dev",
            "name": "scaffold-dev",
            "description": "Create or normalize software project tracking docs.",
            "source_path": str(skill_path),
            "source_scope": "test",
            "content": "Create tracking docs for a software project.",
        }
    ]
    observed: dict[str, object] = {}

    def fake_run(args, parser, *, force_live):
        del parser
        observed["target"] = args.target
        observed["mentions"] = list(args._selected_skill_mentions)
        observed["surface_mentions"] = list(args._tui_selected_skill_mentions)
        observed["lane"] = args._tui_intent_decision.lane
        observed["force_live"] = force_live
        return 0

    monkeypatch.setattr(skill_invocation, "load_skill_catalog", lambda workspace_root: catalog)
    monkeypatch.setattr(super_tui, "_run_tui_turn", fake_run)
    _patch_tui_route(monkeypatch, permission="write", complexity="simple", routed_with_model=True)

    exit_code = super_tui.main(
        [
            "$scaffold-dev please build tracking doc",
            "--workspace",
            str(workspace),
            "--plain",
            "--no-async-agent",
        ]
    )

    assert exit_code == 0
    assert observed == {
        "target": "please build tracking doc",
        "mentions": ["scaffold-dev"],
        "surface_mentions": ["scaffold-dev"],
        "lane": "complex write",
        "force_live": True,
    }
    stdout = capsys.readouterr().out
    assert "Selected skill: $scaffold-dev; objective: please build tracking doc" in stdout
    assert "Use an exact operation" not in stdout


def test_super_tui_simple_write_helper_hands_selected_skills_to_executor(
    tmp_path,
    monkeypatch,
) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["please build tracking doc", "--workspace", str(tmp_path), "--plain"])
    super_tui._prepare_args(args, [])
    args._tui_selected_skill_mentions = ["scaffold-dev"]
    args._selected_skill_mentions = ["scaffold-dev"]
    decision = super_tui.TuiIntentDecision(
        permission="write",
        complexity="simple",
        confidence=0.9,
        rationale="test",
    )
    observed: dict[str, object] = {}

    def fake_run(run_args, run_parser, *, force_live):
        observed["target"] = run_args.target
        observed["mentions"] = list(run_args._tui_selected_skill_mentions)
        observed["parser"] = run_parser
        observed["force_live"] = force_live
        return 0

    monkeypatch.setattr(super_tui, "_run_tui_turn", fake_run)

    exit_code = super_tui._run_tui_simple_write(args, decision, parser)

    assert exit_code == 0
    assert observed == {
        "target": "please build tracking doc",
        "mentions": ["scaffold-dev"],
        "parser": parser,
        "force_live": True,
    }


def test_super_tui_unparsed_simple_write_falls_back_to_executor(
    tmp_path,
    monkeypatch,
) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["can you launch it in command lines?", "--workspace", str(tmp_path), "--plain"])
    super_tui._prepare_args(args, [])
    decision = super_tui.TuiIntentDecision(
        permission="write",
        complexity="simple",
        confidence=0.72,
        rationale="model route",
    )
    observed: dict[str, object] = {}

    def fake_run(run_args, run_parser, *, force_live):
        observed["target"] = run_args.target
        observed["lane"] = run_args._tui_intent_decision.lane
        observed["parser"] = run_parser
        observed["force_live"] = force_live
        return 0

    monkeypatch.setattr(super_tui, "_run_tui_turn", fake_run)

    exit_code = super_tui._run_tui_simple_write(args, decision, parser)

    assert exit_code == 0
    assert observed == {
        "target": "can you launch it in command lines?",
        "lane": "complex write",
        "parser": parser,
        "force_live": True,
    }


def test_super_dan_core_runs_generic_selected_skill_preflight_hook(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    skill_root = tmp_path / "skills" / "demo-skill"
    scripts = skill_root / "scripts"
    scripts.mkdir(parents=True)
    skill_path = skill_root / "SKILL.md"
    skill_path.write_text("# Demo Skill\n", encoding="utf-8")
    (scripts / "demo_skill_preflight.py").write_text(
        "\n".join(
            [
                "import os",
                "import sys",
                "from pathlib import Path",
                "root = Path(sys.argv[1])",
                "root.mkdir(parents=True, exist_ok=True)",
                "(root / 'demo-skill-preflight.txt').write_text(os.environ['DAN_SKILL_OBJECTIVE'])",
                "print('summary: demo hook ran')",
            ]
        ),
        encoding="utf-8",
    )
    catalog = [
        {
            "id": "demo_skill",
            "name": "demo-skill",
            "description": "Demo skill",
            "source_path": str(skill_path),
            "source_scope": "test",
            "content": "Run the demo skill.",
        }
    ]
    monkeypatch.setattr(super_organism, "_load_super_dan_skill_catalog", lambda workspace_root: catalog)
    args = super_organism.build_parser().parse_args(
        ["do the generic thing", "--workspace", str(workspace), "--live"]
    )
    args._selected_skill_mentions = ["demo-skill"]
    args._selected_skill_source = "cli"

    ok, notes = super_organism._run_selected_super_dan_skill_preflights(args, workspace_root=workspace)

    assert ok is True
    assert (workspace / "demo-skill-preflight.txt").read_text(encoding="utf-8") == "do the generic thing"
    assert "summary: demo hook ran" in "\n".join(notes)
    assert args._selected_skill_preflight_notes == notes


def test_super_dan_core_selected_skill_without_hook_still_activates_prompt_contract(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    skill_root = tmp_path / "skills" / "idea-cart"
    skill_root.mkdir(parents=True)
    skill_path = skill_root / "SKILL.md"
    skill_path.write_text("# Idea Cart\n", encoding="utf-8")
    catalog = [
        {
            "id": "idea_cart",
            "name": "idea-cart",
            "description": "Capture ideas",
            "source_path": str(skill_path),
            "source_scope": "test",
            "content": "Capture ideas.",
        }
    ]
    monkeypatch.setattr(super_organism, "_load_super_dan_skill_catalog", lambda workspace_root: catalog)
    args = super_organism.build_parser().parse_args(
        ["collect notes", "--workspace", str(workspace), "--live"]
    )
    args._selected_skill_mentions = ["idea-cart"]
    args._selected_skill_source = "cli"

    ok, notes = super_organism._run_selected_super_dan_skill_preflights(args, workspace_root=workspace)

    assert ok is True
    assert notes == ["skill activated: $idea-cart (prompt contract)"]
    assert args._selected_skill_preflight_notes == notes


def test_super_tui_direct_standalone_skill_mention_does_not_run(capsys, monkeypatch) -> None:
    skills = [
        super_tui.TuiSkillSuggestion(
            token="idea-cart",
            name="idea-cart",
            description="Capture important ideas",
            source_scope="extra",
        )
    ]

    def fail_run(*args, **kwargs):
        raise AssertionError("standalone skill mention must not start a run")

    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace: skills)
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_run)

    exit_code = super_tui.main(["$idea-cart", "--plain"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Selected skill: $idea-cart" in stdout
    assert "no Super DAN run" not in stdout


def test_super_tui_unknown_standalone_skill_mention_is_actionable(capsys, monkeypatch) -> None:
    def fail_run(*args, **kwargs):
        raise AssertionError("unknown standalone skill mention must not start a run")

    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace: [])
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_run)

    exit_code = super_tui.main(["$not-a-skill", "--plain"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Unknown skill mention: $not-a-skill" in stdout
    assert "Use /skills" in stdout


def test_super_tui_ambiguous_skill_prefix_does_not_run(capsys, monkeypatch) -> None:
    skills = [
        super_tui.TuiSkillSuggestion(
            token="scaffold-dev",
            name="scaffold-dev",
            description="Project docs scaffold",
            source_scope="extra",
        ),
        super_tui.TuiSkillSuggestion(
            token="scaffold-research",
            name="scaffold-research",
            description="Research docs scaffold",
            source_scope="extra",
        ),
    ]

    def fail_run(*args, **kwargs):
        raise AssertionError("ambiguous standalone skill mention must not start a run")

    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace: skills)
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_run)

    exit_code = super_tui.main(["$scaffold", "--plain"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Ambiguous skill mention: $scaffold" in stdout
    assert "$scaffold-dev" in stdout
    assert "$scaffold-research" in stdout


def test_super_tui_main_wraps_super_runner_with_plain_renderer(tmp_path, capsys, monkeypatch) -> None:
    _patch_tui_route(monkeypatch, permission="write", complexity="complex")

    def fake_run(args, parser):
        del parser
        assert args.live is True
        renderer = args._progress_renderer_factory(enabled=True, args=args)
        renderer(
            {
                "event": "run.log.started",
                "objective": args.target,
                "task_id": "super-dan-live:1",
                "workspace_root": str(tmp_path),
            }
        )
        renderer(
            {
                "event": "tool.completed",
                "tool_id": "file_write",
                "status": "completed",
                "result": {"path": "website/index.html"},
            }
        )
        renderer(
            {
                "event": "live.validation.completed",
                "passed": True,
                "overall_score": 0.9,
            }
        )
        args._live_report_printer(
            object(),
            {
                "status": "completed",
                "files": ["website/index.html"],
                "validation": {"passed": True, "overall_score": 0.9},
                "event_log_path": str(tmp_path / ".dan-super" / "runs" / "turn-01" / "events.jsonl"),
            },
            verbose=False,
        )
        return 0

    monkeypatch.setattr(super_tui.super_cli, "_run_super_turn", fake_run)

    exit_code = super_tui.main(
        [
            "build a dashboard",
            "--workspace",
            str(tmp_path),
            "--model",
            "fake-live-model",
            "--live",
            "--plain",
        ]
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "DAN · Session:" in stdout
    assert "objective: build a dashboard" in stdout
    assert "Got it. Starting with the relevant context." not in stdout
    assert "dan: You asked:" not in stdout
    assert "DAN · Answer:" in stdout
    assert "DAN · Outcome:" in stdout
    assert "Run finished successfully" in stdout
    assert "Changed website/index.html" in stdout
    assert "Checks: passed" in stdout
    assert "Elapsed:" in stdout
    assert "Event Log:" not in stdout
    assert "[run] started:" not in stdout


def test_super_tui_direct_target_routes_read_only_without_live_runner(capsys, monkeypatch) -> None:
    observed = {}
    _patch_tui_route(monkeypatch, permission="read-only", complexity="complex")

    def fake_run(args, parser):
        del parser
        observed["live"] = args.live
        observed["target"] = args.target
        assert callable(args._progress_renderer_factory)
        assert callable(args._live_report_printer)
        return 0

    monkeypatch.setattr(super_tui.super_cli, "_run_super_turn", fake_run)

    exit_code = super_tui.main(["explain the workspace", "--plain"])

    assert exit_code == 0
    assert observed == {}
    stdout = capsys.readouterr().out
    assert "Answer:" in stdout
    assert "Mode: complex read-only" not in stdout


def test_super_tui_current_project_review_routes_to_read_only(tmp_path, capsys, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo\n\nA small project.\n", encoding="utf-8")
    _patch_tui_route(monkeypatch, permission="read-only", complexity="complex")

    def fail_narrator(*args, **kwargs):
        raise AssertionError("project review requests must not use the progress narrator lane")

    def fail_live_runner(*args, **kwargs):
        raise AssertionError("read-only project review must not start a write/executor run")

    monkeypatch.setattr(super_tui, "_run_tui_narrator", fail_narrator)
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_live_runner)

    exit_code = super_tui.main(
        [
            "help me review current project",
            "--workspace",
            str(workspace),
            "--plain",
        ]
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Answer:" in stdout
    assert "narrator read-only" not in stdout
    assert not list(workspace.glob(".dan-super/runs/**/plans"))


def test_super_tui_intent_gate_does_not_keyword_route_free_text() -> None:
    assert super_tui._classify_tui_intent("show me the todo list").needs_clarification is True
    assert super_tui._classify_tui_intent("summarize docs/todo.md").needs_clarification is True
    assert super_tui._classify_tui_intent("help me review current project").needs_clarification is True
    assert super_tui._classify_tui_intent("copy docs/source.md to docs/copy.md").needs_clarification is True
    assert super_tui._classify_tui_intent("update the todo list").needs_clarification is True
    assert super_tui._classify_tui_intent("fix the bug in todo handling").needs_clarification is True
    assert super_tui._classify_tui_intent("can you help me consolidate the readme files").needs_clarification is True
    assert super_tui._classify_tui_intent("$idea-cart").needs_clarification is True


def test_super_tui_core_command_router_does_not_keyword_route_free_text() -> None:
    assert super_tui.classify_agent_turn_intent("/progress").lane == "narrator read-only"
    assert super_tui.classify_agent_turn_intent("/last").lane == "narrator read-only"
    assert super_tui.classify_agent_turn_intent("tell me current progress?").needs_clarification is True
    assert super_tui.classify_agent_turn_intent("help me review current project").needs_clarification is True
    assert super_tui.classify_agent_turn_intent("summarize docs/todo.md").needs_clarification is True
    assert super_tui.classify_agent_turn_intent("show me the todo list").needs_clarification is True
    assert super_tui.classify_agent_turn_intent("update the todo list").needs_clarification is True
    assert super_tui.classify_agent_turn_intent("continue building this").needs_clarification is True
    assert super_tui.classify_agent_turn_intent("can you help me consolidate the readme files").needs_clarification is True
    assert super_tui.classify_agent_turn_intent("blue theme").needs_clarification is True


def test_super_tui_core_model_router_parses_structured_lane() -> None:
    class FakeProvider:
        async def complete(self, **kwargs):
            assert "tools" not in kwargs
            assert "keyword" in kwargs["messages"][0]["content"]
            return CompletionResult(
                text=json.dumps(
                    {
                        "lane": "executor_write",
                        "complexity": "complex",
                        "confidence": 0.91,
                        "rationale": "The user asks to consolidate existing files, which requires workspace changes.",
                        "clarification": "",
                    }
                )
            )

    decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            FakeProvider(),
            "can you help me consolidate the readme files",
            model="fake-router",
        )
    )

    assert decision.lane == "executor write"
    assert decision.executor_effort == "complex"
    assert decision.confidence == 0.91


def test_super_tui_core_model_router_parses_communication_policy() -> None:
    class FakeProvider:
        async def complete(self, **kwargs):
            prompt = kwargs["messages"][0]["content"]
            assert "communication_policy.answer_budget" in prompt
            assert "communication_policy.progress_detail" in prompt
            return CompletionResult(
                text=json.dumps(
                    {
                        "lane": "executor_write",
                        "complexity": "complex",
                        "confidence": 0.93,
                        "rationale": "The user wants a broad implementation pass.",
                        "clarification": "",
                        "communication_policy": {
                            "answer_budget": "detailed",
                            "latency_preference": "deep",
                            "progress_detail": "verbose",
                            "interaction_style": "review",
                        },
                    }
                )
            )

    decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            FakeProvider(),
            "fully review the patch and implement the needed fixes",
            model="fake-router",
        )
    )

    assert decision.lane == "executor write"
    assert decision.communication_policy.answer_budget == "detailed"
    assert decision.communication_policy.latency_preference == "deep"
    assert decision.communication_policy.progress_detail == "verbose"
    assert decision.communication_policy.interaction_style == "review"
    assert decision.communication_policy.needs_progress_detail is True


def test_super_tui_core_model_router_defaults_policy_from_structured_lane() -> None:
    class FakeProvider:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        async def complete(self, **kwargs):
            del kwargs
            return CompletionResult(text=json.dumps(self.payload))

    read_decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            FakeProvider(
                {
                    "lane": "executor_read_only",
                    "complexity": "simple",
                    "confidence": 0.9,
                    "rationale": "Single-source read.",
                    "clarification": "",
                }
            ),
            "show one file",
            model="fake-router",
        )
    )
    plan_decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            FakeProvider(
                {
                    "lane": "plan_mode",
                    "complexity": "complex",
                    "confidence": 0.9,
                    "rationale": "Planning before execution.",
                    "clarification": "",
                }
            ),
            "help me choose the architecture first",
            model="fake-router",
        )
    )

    assert read_decision.communication_policy.answer_budget == "brief"
    assert read_decision.communication_policy.latency_preference == "fast"
    assert read_decision.communication_policy.progress_detail == "quiet"
    assert read_decision.communication_policy.interaction_style == "answer_only"
    assert plan_decision.communication_policy.answer_budget == "detailed"
    assert plan_decision.communication_policy.latency_preference == "deep"
    assert plan_decision.communication_policy.progress_detail == "verbose"
    assert plan_decision.communication_policy.interaction_style == "review"


def test_super_tui_core_model_router_normalizes_invalid_communication_policy() -> None:
    class FakeProvider:
        async def complete(self, **kwargs):
            del kwargs
            return CompletionResult(
                text=json.dumps(
                    {
                        "lane": "executor_write",
                        "complexity": "complex",
                        "confidence": 0.83,
                        "rationale": "Workspace-changing work.",
                        "clarification": "",
                        "communication_policy": {
                            "answer_budget": "unsupported",
                            "latency_preference": "nonsense",
                            "needs_progress_detail": False,
                            "interaction_style": "mystery",
                        },
                    }
                )
            )

    decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            FakeProvider(),
            "change the project",
            model="fake-router",
        )
    )

    assert decision.communication_policy.answer_budget == "normal"
    assert decision.communication_policy.latency_preference == "balanced"
    assert decision.communication_policy.progress_detail == "quiet"
    assert decision.communication_policy.interaction_style == "act_then_report"


def test_super_tui_core_model_router_repairs_malformed_route_with_policy() -> None:
    class FakeProvider:
        def __init__(self) -> None:
            self.calls = []

        async def complete(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return CompletionResult(text="This should be handled as a deep review.")
            return CompletionResult(
                text=json.dumps(
                    {
                        "lane": "executor_read_only",
                        "complexity": "complex",
                        "confidence": 0.88,
                        "rationale": "The user asked for review only.",
                        "clarification": "",
                        "communication_policy": {
                            "answer_budget": "detailed",
                            "latency_preference": "deep",
                            "progress_detail": "compact",
                            "interaction_style": "review",
                        },
                    }
                )
            )

    provider = FakeProvider()
    decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            provider,
            "review the current communication policy without editing",
            model="fake-router",
        )
    )

    assert len(provider.calls) == 2
    assert decision.lane == "executor read-only"
    assert decision.communication_policy.answer_budget == "detailed"
    assert decision.communication_policy.interaction_style == "review"
    assert "communication_policy" in provider.calls[1]["messages"][0]["content"]


def test_super_tui_communication_policy_accepts_needs_progress_detail_compatibility() -> None:
    quiet = super_tui.normalize_agent_communication_policy(
        {"communication_policy": {"needs_progress_detail": False}},
        lane="executor_write",
        executor_effort="complex",
    )
    verbose = super_tui.normalize_agent_communication_policy(
        {"communication_policy": {"needs_progress_detail": True}},
        lane="executor_write",
        executor_effort="complex",
    )

    assert quiet.progress_detail == "quiet"
    assert quiet.needs_progress_detail is False
    assert verbose.progress_detail == "verbose"
    assert verbose.needs_progress_detail is True


def test_super_tui_communication_policy_autonomous_progress_alias() -> None:
    policy = super_tui.normalize_agent_communication_policy(
        {
            "communication_policy": {
                "answer_budget": "autonomous_progress",
                "progress_detail": "autonomous_progress",
            }
        },
        lane="executor_write",
        executor_effort="complex",
    )

    assert policy.answer_budget == "brief"
    assert policy.progress_detail == "compact"
    assert policy.interaction_style == "autonomous_progress"


def test_super_tui_tui_decision_preserves_core_communication_policy() -> None:
    core_decision = super_tui.classify_agent_turn_intent("/progress")
    tui_decision = super_tui._tui_decision_from_core_decision(core_decision)

    assert tui_decision.communication_policy.answer_budget == "normal"
    assert tui_decision.communication_policy.interaction_style == "answer_only"


def test_super_tui_core_model_router_can_select_plan_mode() -> None:
    class FakeProvider:
        async def complete(self, **kwargs):
            assert "plan_mode" in kwargs["messages"][0]["content"]
            return CompletionResult(
                text=json.dumps(
                    {
                        "lane": "plan_mode",
                        "complexity": "complex",
                        "confidence": 0.88,
                        "rationale": "The user wants to refine the approach before implementation.",
                        "clarification": "",
                    }
                )
            )

    decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            FakeProvider(),
            "this game will use Godot, help me refine the development plan",
            model="fake-router",
        )
    )

    assert decision.lane == "plan mode"
    assert decision.confidence == 0.88


def test_super_tui_core_model_router_rechecks_plan_mode_for_review_request() -> None:
    class FakeProvider:
        def __init__(self) -> None:
            self.calls = []

        async def complete(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return CompletionResult(
                    text=json.dumps(
                        {
                            "lane": "plan_mode",
                            "complexity": "complex",
                            "confidence": 0.78,
                            "rationale": "The request is broad.",
                            "clarification": "",
                        }
                    )
                )
            assert "not merely for review" in kwargs["messages"][1]["content"]
            return CompletionResult(
                text=json.dumps(
                    {
                        "lane": "executor_read_only",
                        "complexity": "complex",
                        "confidence": 0.86,
                        "rationale": "The user asked for a review of the current workspace.",
                        "clarification": "",
                        "communication_policy": {
                            "answer_budget": "detailed",
                            "latency_preference": "deep",
                            "progress_detail": "compact",
                            "interaction_style": "review",
                        },
                    }
                )
            )

    provider = FakeProvider()
    decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            provider,
            "help me review this project",
            model="fake-router",
        )
    )

    assert len(provider.calls) == 2
    assert decision.lane == "executor read-only"
    assert decision.communication_policy.interaction_style == "review"


def test_super_tui_core_model_router_repairs_malformed_route() -> None:
    class FakeProvider:
        def __init__(self) -> None:
            self.calls = []

        async def complete(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return CompletionResult(text="This should use the executor to do analysis work.")
            return CompletionResult(
                text=json.dumps(
                    {
                        "lane": "executor_write",
                        "complexity": "complex",
                        "confidence": 0.86,
                        "rationale": "The request needs data processing and likely workspace changes.",
                        "clarification": "",
                    }
                )
            )

    provider = FakeProvider()
    decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            provider,
            "yeah can you combine the tnic data with panjiva and then run regression to answer the research question?",
            model="fake-router",
        )
    )

    assert decision.lane == "executor write"
    assert decision.executor_effort == "complex"
    assert len(provider.calls) == 2
    repair_prompt = provider.calls[1]["messages"][1]["content"]
    assert "Previous router output was invalid" in repair_prompt
    assert "Return JSON only" in repair_prompt


def test_super_tui_core_model_router_reconsiders_write_confirmation_clarification() -> None:
    class FakeProvider:
        def __init__(self) -> None:
            self.calls = []

        async def complete(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return CompletionResult(
                    text=json.dumps(
                        {
                            "lane": "clarification",
                            "complexity": "complex",
                            "confidence": 0.72,
                            "rationale": "The request may change files.",
                            "clarification": "Are you authorizing write operations?",
                        }
                    )
                )
            return CompletionResult(
                text=json.dumps(
                    {
                        "lane": "executor_write",
                        "complexity": "complex",
                        "confidence": 0.9,
                        "rationale": "The user directly requested workspace-changing analysis.",
                        "clarification": "",
                    }
                )
            )

    provider = FakeProvider()
    decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            provider,
            "ok whats next, can you merge the tnic data with panjiva input similarity data",
            model="fake-router",
        )
    )

    assert decision.lane == "executor write"
    assert decision.executor_effort == "complex"
    assert len(provider.calls) == 2
    review_prompt = provider.calls[1]["messages"][1]["content"]
    assert "verify that clarification is truly necessary" in review_prompt


def test_super_tui_core_model_router_preserves_long_clarification() -> None:
    long_clarification = (
        "I need a specific workspace path or dataset name before I can choose a safe lane. "
        * 8
    ) + "tail marker"

    class FakeProvider:
        def __init__(self) -> None:
            self.calls = []

        async def complete(self, **kwargs):
            self.calls.append(kwargs)
            return CompletionResult(
                text=json.dumps(
                    {
                        "lane": "clarification",
                        "complexity": "complex",
                        "confidence": 0.68,
                        "rationale": "The target is underspecified.",
                        "clarification": long_clarification,
                    }
                )
            )

    provider = FakeProvider()
    decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            provider,
            "merge that data",
            model="fake-router",
        )
    )

    assert decision.needs_clarification is True
    assert "tail marker" in decision.clarification
    assert len(decision.clarification) > 300
    assert len(provider.calls) == 2


def test_super_tui_core_model_router_clarifies_after_failed_repair() -> None:
    class FakeProvider:
        def __init__(self) -> None:
            self.calls = []

        async def complete(self, **kwargs):
            self.calls.append(kwargs)
            return CompletionResult(text="not a route")

    provider = FakeProvider()
    decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            provider,
            "please handle this",
            model="fake-router",
        )
    )

    assert decision.needs_clarification is True
    assert decision.lane == "clarification"
    assert "could not route" in decision.clarification
    assert len(provider.calls) == 2


def test_super_tui_live_route_failure_is_actionable(monkeypatch) -> None:
    class FailingProvider:
        async def complete(self, **kwargs):
            del kwargs
            raise RuntimeError("Connection error.")

    monkeypatch.delenv("DAN_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DAN_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("DAN_BASE_URL", raising=False)
    monkeypatch.setattr(
        super_tui,
        "_build_tui_intent_router_live_provider",
        lambda args, model: FailingProvider(),
    )
    args = Namespace(
        target="fix the demo",
        model="fake-router",
        api_key=None,
        base_url=None,
        json=False,
        quiet_progress=True,
        _tui_turn_started_at=None,
        _tui_transcript_workspace="",
        _tui_selected_skill_mentions=[],
    )

    decision, routed_with_model = super_tui._route_tui_intent_with_model(args)

    assert routed_with_model is False
    assert decision.needs_clarification is True
    assert "Connection error" in decision.clarification
    assert "Connection error.." not in decision.clarification
    assert "provider/configuration problem" in decision.clarification
    assert "model name, API key, base URL, and network access" in decision.clarification
    assert "/progress" in decision.clarification


def test_super_tui_explicit_live_route_failure_falls_to_executor(monkeypatch) -> None:
    class FailingProvider:
        async def complete(self, **kwargs):
            del kwargs
            raise RuntimeError("Connection error.")

    monkeypatch.setattr(
        super_tui,
        "_build_tui_intent_router_live_provider",
        lambda args, model: FailingProvider(),
    )
    args = Namespace(
        target="repair the current workspace",
        model="fake-router",
        api_key=None,
        base_url=None,
        json=False,
        quiet_progress=True,
        _live_explicit=True,
        _tui_turn_started_at=None,
        _tui_transcript_workspace="",
        _tui_selected_skill_mentions=[],
    )

    decision, routed_with_model = super_tui._route_tui_intent_with_model(args)

    assert routed_with_model is False
    assert decision.needs_clarification is False
    assert decision.permission == "write"
    assert decision.complexity == "complex"
    assert "explicit --live requested" in decision.rationale


def test_super_tui_brief_answer_budget_limits_visible_lines() -> None:
    state = super_tui.SuperTuiState(status="completed")
    state.communication_policy = super_tui.AgentCommunicationPolicy(answer_budget="brief")
    for index in range(8):
        state._record_answer(f"line {index}")

    assert state.final_answer_lines() == ["line 0", "line 1", "line 2", "line 3", "line 4"]


def test_super_tui_normal_answer_budget_limits_visible_lines() -> None:
    state = super_tui.SuperTuiState(status="completed")
    state.communication_policy = super_tui.AgentCommunicationPolicy(answer_budget="normal")
    for index in range(20):
        state._record_answer(f"line {index}")

    assert len(state.final_answer_lines()) == 12
    assert state.final_answer_lines()[-1] == "line 11"


def test_super_tui_detailed_answer_budget_preserves_longer_findings() -> None:
    state = super_tui.SuperTuiState(status="completed")
    state.communication_policy = super_tui.AgentCommunicationPolicy(answer_budget="detailed")
    for index in range(25):
        state._record_answer(f"finding {index}")

    assert len(state.final_answer_lines()) == 25
    assert state.final_answer_lines()[-1] == "finding 24"


def test_super_tui_transcript_summary_obeys_answer_budget() -> None:
    state = super_tui.SuperTuiState(status="completed")
    state.communication_policy = super_tui.AgentCommunicationPolicy(answer_budget="brief")
    for index in range(7):
        state._record_answer(f"visible {index}")

    summary = state.transcript_summary(exit_code=0)

    assert "visible 0" in summary
    assert "visible 4" in summary
    assert "visible 5" not in summary


def test_super_tui_final_answer_prompt_includes_policy_and_budget(monkeypatch) -> None:
    state = super_tui.SuperTuiState(
        objective="review the current work",
        status="completed",
        validation="passed",
    )
    state.communication_policy = super_tui.AgentCommunicationPolicy(
        answer_budget="detailed",
        latency_preference="deep",
        progress_detail="verbose",
        interaction_style="review",
    )

    messages = super_tui._tui_final_answer_messages(state)

    assert "do not exceed 80 visible lines" in messages[0]["content"]
    assert "lead with concrete findings or risks" in messages[0]["content"]
    assert '"answer_budget": "detailed"' in messages[1]["content"]
    assert '"interaction_style": "review"' in messages[1]["content"]


def test_super_tui_final_answer_model_enforces_brief_budget(monkeypatch) -> None:
    class FakeProvider:
        def __init__(self) -> None:
            self.calls = []

        async def complete(self, **kwargs):
            self.calls.append(kwargs)
            return CompletionResult(
                text="\n".join(f"The answer line {index} summarizes useful completed work." for index in range(9))
            )

    async def fake_close(provider):
        del provider

    provider = FakeProvider()
    monkeypatch.setattr(super_tui.super_cli, "_resolve_live_model", lambda model: model or "fake")
    monkeypatch.setattr(super_tui.super_cli, "_close_live_provider", fake_close)
    monkeypatch.setattr(
        super_tui,
        "_build_tui_final_answer_live_provider",
        lambda args, model: provider,
    )
    args = Namespace(
        model="fake",
        json=False,
        quiet_progress=True,
        raw_events=False,
        plain=True,
        _tui_routed_with_model=True,
        _tui_turn_started_at=None,
    )
    state = super_tui.SuperTuiState(objective="tiny status", status="completed")
    state.communication_policy = super_tui.AgentCommunicationPolicy(
        answer_budget="brief",
        latency_preference="fast",
        progress_detail="quiet",
        interaction_style="answer_only",
    )

    assert super_tui._run_tui_final_model_answer(args, state) is True

    assert len(state.answer_lines) == 5
    assert state.answer_lines[-1] == "The answer line 4 summarizes useful completed work."
    assert provider.calls[0]["max_tokens"] == 360
    assert "Communication policy" in provider.calls[0]["messages"][1]["content"]


def test_super_tui_quiet_progress_policy_suppresses_model_sidecar() -> None:
    renderer = super_tui.SuperTuiProgressRenderer(enabled=True, objective="build", workspace="/tmp/ws")
    renderer.state.communication_policy = super_tui.AgentCommunicationPolicy(progress_detail="quiet")
    renderer.configure_model_sidecar(Namespace(_tui_routed_with_model=True))

    assert renderer._model_sidecar_enabled() is False


def test_super_tui_quiet_progress_policy_suppresses_noncritical_narrator_reports() -> None:
    state = super_tui.SuperTuiState(status="running", phase="model")
    state.communication_policy = super_tui.AgentCommunicationPolicy(progress_detail="quiet")

    state.observe({"event": "super.heartbeat", "phase": "model", "detail": "still working"})

    assert state.narrator_lines == []
    assert state.narrator_reports == []


def test_super_tui_verbose_progress_policy_allows_narrator_reports() -> None:
    state = super_tui.SuperTuiState(status="running", phase="model")
    state.communication_policy = super_tui.AgentCommunicationPolicy(progress_detail="verbose")

    state.observe({"event": "super.heartbeat", "phase": "model", "detail": "still working"})

    assert state.narrator_lines
    assert state.narrator_reports


def test_super_tui_read_only_limits_follow_latency_policy() -> None:
    fast = super_tui.AgentCommunicationPolicy(latency_preference="fast")
    balanced = super_tui.AgentCommunicationPolicy(latency_preference="balanced")
    deep = super_tui.AgentCommunicationPolicy(latency_preference="deep")

    assert super_tui._tui_read_only_model_limits(fast) == (2, 4)
    assert super_tui._tui_read_only_model_limits(balanced) == (4, 12)
    assert super_tui._tui_read_only_model_limits(deep) == (6, 20)


def test_super_tui_async_surface_turn_carries_communication_policy(tmp_path) -> None:
    args = Namespace(_tui_selected_skill_mentions=["skill-a"], _tui_communication_policy={
        "answer_budget": "brief",
        "latency_preference": "fast",
        "progress_detail": "quiet",
        "interaction_style": "answer_only",
    })

    turn = super_tui._build_tui_async_surface_turn(
        args,
        workspace_root=tmp_path,
        text="show status",
    )

    policy = turn.metadata["communication_policy"]
    assert policy["answer_budget"] == "brief"
    assert policy["latency_preference"] == "fast"
    assert turn.metadata["surface_context"]["communication_policy"] == policy


def test_super_tui_async_admission_payload_carries_communication_policy(tmp_path) -> None:
    args = Namespace(
        _tui_selected_skill_mentions=[],
        _tui_communication_policy={
            "answer_budget": "detailed",
            "latency_preference": "deep",
            "progress_detail": "verbose",
            "interaction_style": "review",
        },
        model="",
        base_url="",
        artifact_dir="",
        max_tool_calls=0,
        async_agent_backend="super_dan",
        async_agent_max_parallel=4,
    )

    payload = super_tui._tui_async_admission_payload(
        args,
        workspace_root=tmp_path,
        text="review this",
    )

    policy = payload["execute"]["metadata"]["communication_policy"]
    assert policy["answer_budget"] == "detailed"
    assert payload["chat_request"]["surface_context"]["communication_policy"] == policy


def test_super_tui_execution_overrides_carry_communication_policy() -> None:
    args = Namespace(
        _tui_selected_skill_mentions=[],
        _tui_communication_policy={
            "answer_budget": "normal",
            "latency_preference": "balanced",
            "progress_detail": "compact",
            "interaction_style": "act_then_report",
        },
        model="",
        base_url="",
        artifact_dir="",
        max_tool_calls=0,
        plain=True,
    )

    overrides = super_tui._tui_async_execution_overrides(args)

    assert overrides["metadata"]["communication_policy"]["answer_budget"] == "normal"
    assert overrides["metadata"]["communication_policy"]["interaction_style"] == "act_then_report"


def test_super_tui_async_admission_transcript_records_communication_policy(tmp_path) -> None:
    result = super_tui.TuiAsyncAdmissionResult(
        ok=True,
        message="Started background work.",
        response={
            "status": "started",
            "admission": {"action": "start_parallel", "task_id": "task-1"},
        },
    )
    policy = super_tui.AgentCommunicationPolicy(
        answer_budget="brief",
        latency_preference="fast",
        progress_detail="quiet",
        interaction_style="answer_only",
    )

    super_tui._append_tui_async_admission_transcript(
        tmp_path,
        result,
        text="show status",
        communication_policy=policy,
    )

    entries = super_tui._read_tui_transcript(tmp_path, limit=5)
    assert entries[-1].metadata["communication_policy"]["answer_budget"] == "brief"
    assert entries[-1].metadata["communication_policy"]["progress_detail"] == "quiet"


def test_super_tui_policy_words_do_not_create_deterministic_free_text_route() -> None:
    assert super_tui.classify_agent_turn_intent("brief update please").needs_clarification is True
    assert super_tui.classify_agent_turn_intent("give detailed progress").needs_clarification is True
    assert super_tui.classify_agent_turn_intent("verbose review of the project").needs_clarification is True
    assert super_tui._classify_tui_intent("fast answer only").needs_clarification is True
    assert super_tui._classify_tui_intent("autonomous progress until done").needs_clarification is True


def test_super_tui_non_mapping_communication_policy_defaults_safely() -> None:
    policy = super_tui.normalize_agent_communication_policy(
        {"communication_policy": ["brief", "fast"]},
        lane="executor_write",
        executor_effort="complex",
    )

    assert policy.to_payload() == {
        "answer_budget": "normal",
        "latency_preference": "balanced",
        "progress_detail": "compact",
        "interaction_style": "act_then_report",
        "needs_progress_detail": False,
    }


def test_super_tui_response_policy_helpers_normalize_mapping_payload() -> None:
    payload = {
        "answer_budget": "short",
        "latency_preference": "quick",
        "progress_detail": "minimal",
        "interaction_style": "chat",
    }

    assert super_tui._tui_policy_payload(payload) == {
        "answer_budget": "brief",
        "latency_preference": "fast",
        "progress_detail": "quiet",
        "interaction_style": "answer_only",
        "needs_progress_detail": False,
    }
    assert super_tui._tui_answer_line_limit(payload) == 5
    assert super_tui._tui_answer_max_tokens(payload) == 360


def test_super_tui_core_narrator_response_uses_snapshot_only() -> None:
    snapshot = super_tui.RunNarratorSnapshot(
        snapshot_id="run-1:5",
        run_id="super-dan-live:1",
        objective="build dashboard",
        workspace="/tmp/ws",
        status="running",
        phase="validation",
        current_step="Validator is checking the result",
        activity=("File changed: website/index.html",),
        changed_files=("website/index.html",),
        validation="passed",
        validation_score="0.92",
        trace_refs=(".dan-super/runs/turn-01/events.jsonl",),
        source_event_count=5,
    )
    request = super_tui.NarratorRequest(
        question="current progress?",
        snapshot=snapshot,
        request_id="req-1",
    )

    response = super_tui.deterministic_narrator_response(request)

    assert response.fallback_used is True
    assert response.snapshot_id == "run-1:5"
    assert "running" in response.text
    assert "Next: validation should finish" in response.text
    assert "Validator is checking the result" in response.text
    assert "website/index.html" in response.text
    assert "Validation: passed (0.92)." in response.text
    assert ".dan-super/runs/turn-01/events.jsonl" in response.cited_refs


def test_super_tui_core_narrator_next_step_answers_directly() -> None:
    snapshot = super_tui.RunNarratorSnapshot(
        snapshot_id="run-1:6",
        run_id="super-dan-live:1",
        status="running",
        phase="validation",
        current_step="Validator is checking the result",
        queued_work="validation depth=1",
    )
    request = super_tui.NarratorRequest(
        question="whats next step",
        snapshot=snapshot,
        request_id="req-next",
    )

    response = super_tui.deterministic_narrator_response(request)

    first_line = response.text.splitlines()[0]
    assert first_line.startswith("Next:")
    assert "validation depth=1" in first_line
    assert "Status: running" in response.text


def test_super_tui_core_deterministic_narrator_report_is_snapshot_only() -> None:
    snapshot = super_tui.RunNarratorSnapshot(
        snapshot_id="run-2:4",
        run_id="super-dan-live:2",
        objective="update the todo list",
        status="running",
        phase="validation",
        current_step="Validator is checking the result",
        changed_files=("docs/todo.md",),
        validation="running",
    )

    report = super_tui.deterministic_narrator_report(
        snapshot,
        trigger="checkpoint",
        event_name="live.validation.started",
    )

    assert report is not None
    assert report.kind == "checkpoint"
    assert "Validation is checking" in report.text
    assert "update the todo list" in report.text
    assert report.source_snapshot_id == "run-2:4"


def test_super_tui_core_heartbeat_report_avoids_recursive_status_text() -> None:
    snapshot = super_tui.RunNarratorSnapshot(
        snapshot_id="run-2:5",
        run_id="super-dan-live:2",
        objective="create status note",
        status="running",
        phase="model",
        current_step="Still running: model",
        elapsed_seconds=10,
    )

    report = super_tui.deterministic_narrator_report(
        snapshot,
        trigger="heartbeat",
        event_name="super.heartbeat",
        event_payload={"elapsed_seconds": 10},
    )

    assert report is not None
    assert "Still working after 10s." in report.text
    assert "waiting for the model response" in report.text
    assert "Still running: model" not in report.text


def test_super_tui_core_model_narrator_gets_no_tools() -> None:
    class FakeProvider:
        def __init__(self) -> None:
            self.kwargs = {}

        async def complete(self, **kwargs):
            self.kwargs = dict(kwargs)
            return CompletionResult(text="The run is validating the dashboard.")

    provider = FakeProvider()
    request = super_tui.NarratorRequest(
        question="what is happening?",
        snapshot=super_tui.RunNarratorSnapshot(status="running", phase="validation"),
        request_id="req-2",
    )

    response = asyncio.run(
        super_tui.generate_narrator_response(provider, request, model="fake-model")
    )

    assert response.status == "completed"
    assert response.text == "The run is validating the dashboard."
    assert provider.kwargs["model"] == "fake-model"
    assert "messages" in provider.kwargs
    assert "tools" not in provider.kwargs


def test_super_tui_heartbeat_narrator_prompt_asks_for_non_repeating_sentence() -> None:
    request = super_tui.NarratorRequest(
        question="merge data",
        snapshot=super_tui.RunNarratorSnapshot(
            objective="merge data",
            status="running",
            phase="model",
            current_step="waiting for the model response",
        ),
        surface="super-tui",
    )

    messages = super_tui._tui_narrator_model_messages(
        request,
        purpose="executor-heartbeat",
        recent_narrator=("The executor has loaded ToDo.md and README.md.",),
    )

    combined = "\n".join(message["content"] for message in messages)
    assert "exactly one short natural sentence" in combined
    assert "Do not relist stable files" in combined
    assert "The executor has loaded ToDo.md and README.md." in combined


def test_super_tui_core_narrator_job_emits_stale_event() -> None:
    class FakeProvider:
        async def complete(self, **kwargs):
            return CompletionResult(text="Snapshot answer before newer events.")

    events: list[dict] = []
    request = super_tui.NarratorRequest(
        question="current progress?",
        snapshot=super_tui.RunNarratorSnapshot(snapshot_id="old", status="running"),
        request_id="req-stale",
    )
    latest = super_tui.RunNarratorSnapshot(snapshot_id="new", status="running")

    async def run_job():
        handle = super_tui.start_narrator_job(
            FakeProvider(),
            request,
            model="fake-model",
            latest_snapshot_getter=lambda: latest,
            event_callback=events.append,
        )
        return await handle.wait()

    response = asyncio.run(run_job())

    assert response.stale is True
    assert response.status == "stale"
    assert [event["event"] for event in events] == [
        "narrator.requested",
        "narrator.started",
        "narrator.stale",
    ]
    assert events[-1]["snapshot_id"] == "old"


def test_super_tui_core_narrator_job_can_cancel_previous() -> None:
    class SlowProvider:
        async def complete(self, **kwargs):
            await asyncio.sleep(10)
            return CompletionResult(text="too late")

    class FastProvider:
        async def complete(self, **kwargs):
            return CompletionResult(text="new answer")

    async def run_jobs():
        request = super_tui.NarratorRequest(
            question="current progress?",
            snapshot=super_tui.RunNarratorSnapshot(snapshot_id="same"),
            request_id="req-1",
        )
        old = super_tui.start_narrator_job(SlowProvider(), request, model="fake-model")
        new = super_tui.start_narrator_job(
            FastProvider(),
            request,
            model="fake-model",
            previous=old,
        )
        response = await new.wait()
        await asyncio.sleep(0)
        return old, response

    old_handle, response = asyncio.run(run_jobs())

    assert old_handle.task.cancelled()
    assert response.text == "new answer"


def test_super_tui_narrator_stale_event_renders_result() -> None:
    state = super_tui.SuperTuiState(mode_line="narrator read-only - test")

    line = state.observe({"event": "narrator.stale", "request_id": "req-1"})

    assert line == "Narrator answer is based on an older snapshot."
    assert "Narrator answer is based on an older snapshot." in state.plain_snapshot()


def test_super_tui_simple_read_only_shows_matching_file_without_run_plans(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "todo.md").write_text(
        "# Todo\n\n- [ ] Implement the TUI gate\n",
        encoding="utf-8",
    )

    def fail_run(*args, **kwargs):
        raise AssertionError("read-only request must not start Super DAN runner")

    _patch_tui_route(monkeypatch, permission="read-only", complexity="simple")
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_run)

    exit_code = super_tui.main(
        [
            "show me the todo list",
            "--workspace",
            str(workspace),
            "--plain",
        ]
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Answer:" in stdout
    assert "Source: docs/todo.md" in stdout
    assert "Implement the TUI gate" in stdout
    assert not list(workspace.glob(".dan-super/runs/**/plans"))


def test_super_tui_complex_read_only_summarizes_file_without_writes(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "todo.md").write_text(
        "# Todo\n\n## Phase 1\n- [ ] Open task\n- [x] Done task\n",
        encoding="utf-8",
    )

    def fail_run(*args, **kwargs):
        raise AssertionError("read-only summary must not start Super DAN runner")

    _patch_tui_route(monkeypatch, permission="read-only", complexity="complex")
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_run)

    exit_code = super_tui.main(
        [
            "summarize docs/todo.md",
            "--workspace",
            str(workspace),
            "--plain",
        ]
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Answer:" in stdout
    assert "Tasks: 1 open, 1 completed." in stdout
    assert "Open task" in stdout
    assert not list(workspace.glob(".dan-super/runs/**/plans"))


def test_super_tui_complex_read_only_searches_workspace_without_writes(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "a.md").write_text("alpha\nneedle value\n", encoding="utf-8")
    (workspace / "docs" / "b.md").write_text("other needle value\n", encoding="utf-8")

    def fail_run(*args, **kwargs):
        raise AssertionError("read-only search must not start Super DAN runner")

    _patch_tui_route(monkeypatch, permission="read-only", complexity="complex")
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_run)

    exit_code = super_tui.main(
        [
            'find "needle value" in docs/',
            "--workspace",
            str(workspace),
            "--plain",
        ]
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Answer:" in stdout
    assert "docs/a.md:2: needle value" in stdout
    assert "docs/b.md:1: other needle value" in stdout
    assert not list(workspace.glob(".dan-super/runs/**/plans"))


def test_super_tui_complex_read_only_live_uses_model_tool_loop(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "info.md").write_text("# Info\n\nAlpha finding.\n", encoding="utf-8")

    class FakeProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return CompletionResult(
                    text="",
                    model=kwargs.get("model"),
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "id": "call-read",
                            "type": "function",
                            "function": {
                                "name": "file_read",
                                "arguments": '{"path":"docs/info.md"}',
                            },
                        }
                    ],
                    raw_assistant_message={
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-read",
                                "type": "function",
                                "function": {
                                    "name": "file_read",
                                    "arguments": '{"path":"docs/info.md"}',
                                },
                            }
                        ],
                    },
                )
            return CompletionResult(
                text="Summary: Alpha finding from docs/info.md.",
                model=kwargs.get("model"),
                finish_reason="stop",
            )

    provider = FakeProvider()

    def fail_run(*args, **kwargs):
        raise AssertionError("read-only live lane must not start Super DAN runner")

    _patch_tui_route(monkeypatch, permission="read-only", complexity="complex")
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_run)
    monkeypatch.setattr(
        super_tui,
        "_build_tui_read_only_live_provider",
        lambda args, model: provider,
    )

    exit_code = super_tui.main(
        [
            "explain docs/info.md",
            "--workspace",
            str(workspace),
            "--model",
            "fake-model",
            "--live",
            "--plain",
        ]
    )

    assert exit_code == 0
    assert provider.calls == 2
    stdout = capsys.readouterr().out
    assert "Answer:" in stdout
    assert "Summary: Alpha finding from docs/info.md." in stdout
    assert not list(workspace.glob(".dan-super/runs/**/plans"))


def test_super_tui_complex_read_only_streams_answer_when_model_returns_empty_text(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "info.md").write_text("# Info\n\nAlpha finding.\n", encoding="utf-8")

    class FakeProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return CompletionResult(
                    text="",
                    model=kwargs.get("model"),
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "id": "call-read",
                            "type": "function",
                            "function": {
                                "name": "file_read",
                                "arguments": '{"path":"docs/info.md"}',
                            },
                        }
                    ],
                    raw_assistant_message={
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-read",
                                "type": "function",
                                "function": {
                                    "name": "file_read",
                                    "arguments": '{"path":"docs/info.md"}',
                                },
                            }
                        ],
                    },
                )
            if self.calls == 2:
                return CompletionResult(text="", model=kwargs.get("model"), finish_reason="stop")
            return CompletionResult(
                text="The review found one source: docs/info.md contains the Alpha finding.",
                model=kwargs.get("model"),
                finish_reason="stop",
            )

    provider = FakeProvider()

    def fail_run(*args, **kwargs):
        raise AssertionError("read-only live lane must not start Super DAN runner")

    _patch_tui_route(monkeypatch, permission="read-only", complexity="complex")
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_run)
    monkeypatch.setattr(
        super_tui,
        "_build_tui_read_only_live_provider",
        lambda args, model: provider,
    )

    exit_code = super_tui.main(
        [
            "explain docs/info.md",
            "--workspace",
            str(workspace),
            "--model",
            "fake-model",
            "--live",
        ]
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert provider.calls == 3
    assert "Read-only review" not in stdout
    assert "Answer" in stdout
    assert "The review found one source" in stdout
    assert "Source: docs/info.md" not in stdout
    assert "Headings: # Info" not in stdout
    assert "Recent Events" not in stdout
    assert "DAN · Session:\nStatus:" not in stdout
    assert not list(workspace.glob(".dan-super/runs/**/plans"))


def test_super_tui_progress_question_routes_to_narrator_without_executor(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    event_log = workspace / ".dan-super" / "runs" / "turn-01" / "events.jsonl"
    _write_event_log(
        event_log,
        [
            {
                "event": "run.log.started",
                "objective": "build dashboard",
                "task_id": "super-dan-live:1",
                "workspace_root": str(workspace),
            },
            {
                "event": "tool.completed",
                "tool_id": "file_write",
                "result": {"path": "website/index.html"},
            },
            {
                "event": "live.validation.completed",
                "passed": True,
                "overall_score": 0.92,
            },
            {
                "event": "run.log.completed",
                "status": "completed",
                "event_log_path": str(event_log),
            },
        ],
    )
    super_tui._append_tui_transcript_entry(
        workspace,
        role="assistant_final",
        text="Run completed.",
        metadata={"event_log_path": str(event_log)},
    )

    def fail_executor(*args, **kwargs):
        raise AssertionError("progress questions must not start executor lanes")

    _patch_tui_route(monkeypatch, permission="read-only", complexity="narrator")
    monkeypatch.setattr(super_tui, "_run_tui_read_only", fail_executor)
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_executor)

    exit_code = super_tui.main(
        [
            "tell me current progress?",
            "--workspace",
            str(workspace),
            "--plain",
        ]
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Answer:" in stdout
    assert "Status: complete" in stdout
    assert "Activity:" not in stdout
    assert "website/index.html" in stdout
    assert "Checks: passed" in stdout
    assert str(event_log) not in stdout
    assert not list(workspace.glob(".dan-super/runs/**/plans"))


def test_super_tui_progress_narrator_model_call_gets_no_tools(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class FakeProvider:
        def __init__(self) -> None:
            self.kwargs = {}

        async def complete(self, **kwargs):
            self.kwargs = dict(kwargs)
            return CompletionResult(text="Model answer: the previous run state is idle.")

    provider = FakeProvider()
    _patch_tui_route(monkeypatch, permission="read-only", complexity="narrator")
    monkeypatch.setattr(
        super_tui,
        "_build_tui_narrator_live_provider",
        lambda args, model: provider,
    )

    exit_code = super_tui.main(
        [
            "tell me current progress?",
            "--workspace",
            str(workspace),
            "--model",
            "fake-model",
            "--plain",
        ]
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Activity:" not in stdout
    assert "Narrator model call started" not in stdout
    assert "Model answer: the previous run state is idle." in stdout
    assert provider.kwargs["model"] == "fake-model"
    assert "tools" not in provider.kwargs


def test_super_tui_progress_narrator_persists_flat_transcript(tmp_path) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(
        [
            "/progress",
            "--workspace",
            str(tmp_path),
            "--plain",
        ]
    )
    super_tui._prepare_args(args, [])
    args._tui_transcript_workspace = str(tmp_path)

    exit_code = super_tui._dispatch_tui_turn(args, parser)

    assert exit_code == 0
    entries = super_tui._read_tui_transcript(tmp_path)
    assert entries[-1].role == "assistant_narrator"
    assert "no active or recent run" in entries[-1].text


def test_super_tui_progress_narrator_reuses_latest_visible_narrator_answer(
    tmp_path,
    capsys,
) -> None:
    parser = super_tui.build_parser()
    super_tui._append_tui_transcript_entry(
        tmp_path,
        role="assistant_narrator",
        text="The previous run is still validating the README update.\nNext useful step: wait for validation.",
        metadata={"snapshot_id": "old"},
    )
    args = parser.parse_args(
        [
            "/progress",
            "--workspace",
            str(tmp_path),
            "--plain",
        ]
    )
    super_tui._prepare_args(args, [])
    args._tui_transcript_workspace = str(tmp_path)

    exit_code = super_tui._dispatch_tui_turn(args, parser)

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "The previous run is still validating the README update." in stdout
    assert "Next useful step: wait for validation." in stdout
    assert "no active or recent run" not in stdout


def test_super_tui_simple_write_copies_file_without_live_runner(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "source.md").write_text("# Source\n", encoding="utf-8")

    def fail_run(*args, **kwargs):
        raise AssertionError("simple write must not start Super DAN runner")

    _patch_tui_route(monkeypatch, permission="write", complexity="simple")
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_run)

    exit_code = super_tui.main(
        [
            "copy docs/source.md to docs/copy.md",
            "--workspace",
            str(workspace),
            "--plain",
        ]
    )

    assert exit_code == 0
    assert (workspace / "docs" / "copy.md").read_text(encoding="utf-8") == "# Source\n"
    stdout = capsys.readouterr().out
    assert "Answer:" in stdout
    assert "Outcome:" in stdout
    assert "Changed: docs/copy.md" in stdout


def test_super_tui_simple_write_blocks_outside_workspace(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "source.md"
    source.write_text("# Source\n", encoding="utf-8")

    def fail_run(*args, **kwargs):
        raise AssertionError("blocked simple write must not start Super DAN runner")

    _patch_tui_route(monkeypatch, permission="write", complexity="simple")
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_run)

    exit_code = super_tui.main(
        [
            f"copy {source} to docs/copy.md",
            "--workspace",
            str(workspace),
            "--plain",
        ]
    )

    assert exit_code == 1
    stdout = capsys.readouterr().out
    assert "Status: blocked" in stdout
    assert "path is outside workspace" in stdout


def test_super_tui_write_intent_enters_live_dispatch_path(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(args, parser, *, force_live):
        del parser
        observed["target"] = args.target
        observed["force_live"] = force_live
        observed["lane"] = args._tui_intent_decision.lane
        return 0

    monkeypatch.setattr(super_tui, "_run_tui_turn", fake_run)
    _patch_tui_route(monkeypatch, permission="write", complexity="complex", routed_with_model=True)

    parser = super_tui.build_parser()
    args = parser.parse_args(["--plain"])
    super_tui._prepare_args(args, [])
    args.target = "can you help me consolidate the readme files"

    exit_code = super_tui._dispatch_tui_turn(args, parser, force_live=True)

    assert exit_code == 0
    assert observed == {
        "target": "can you help me consolidate the readme files",
        "force_live": True,
        "lane": "complex write",
    }


def test_super_tui_ambiguous_input_asks_clarification(capsys, monkeypatch) -> None:
    def fail_run(*args, **kwargs):
        raise AssertionError("ambiguous input must not start a run")

    _patch_tui_route(
        monkeypatch,
        permission="",
        complexity="",
        clarification="Should this be a read-only answer, or should Super DAN change the workspace?",
    )
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_run)

    exit_code = super_tui.main(["blue theme", "--plain"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Answer:" in stdout
    assert "Status: blocked" in stdout
    assert "Should this be a read-only answer" in stdout


def test_super_tui_interactive_prompt_starts_without_idle_panel(capsys, monkeypatch) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", "/tmp/ws", "--plain"])
    super_tui._prepare_args(args, [])

    def fake_prompt(prompt, *, commands, skills, paths=()):
        del commands, skills, paths
        print(prompt, end="")
        raise EOFError

    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace: [])
    monkeypatch.setattr(super_tui, "_read_interactive_line", fake_prompt)

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "<idle-panel>" not in stdout
    assert "Message" in stdout
    assert "Type in the composer below. Submitted text is saved before routing." in stdout
    assert stdout.index("Message") < stdout.index("super-tui>")


def test_super_organism_parser_identity_is_unchanged() -> None:
    parser = super_organism.build_parser()

    assert parser.prog == "dan-super-organism"
    assert _SUBCOMMANDS["super-organism"] == ("dan.cli.super_organism", "main")
