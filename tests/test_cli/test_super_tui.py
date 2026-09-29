from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
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


def test_super_tui_terminal_run_clears_internal_hook_queue_from_snapshot() -> None:
    state = super_tui.SuperTuiState(status="running")
    state.observe(
        {
            "event": "super.hook.packet_enqueued",
            "inbox_id": "immune",
            "source_event": "super.heartbeat",
            "queue_depth": 1,
        }
    )
    assert state.queue_status == "immune depth=1"

    state.observe({"event": "run.log.completed", "status": "failed"})

    assert state.queue_status == ""
    assert state._narrator_snapshot().queued_work == ""
    assert "Queue:" not in state.plain_snapshot()


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


def test_super_tui_default_snapshots_do_not_auto_dump_board() -> None:
    state = super_tui.SuperTuiState(workspace="/tmp/ws")
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

    snapshot = state.plain_snapshot()
    recent = "\n".join(state.recent_event_lines())
    explicit_board = "\n".join(super_tui._format_tui_board_lines(state))

    assert "Board:" not in snapshot
    assert "Intervene:" not in snapshot
    assert "Board:" not in recent
    assert "Intervene:" not in recent
    assert "Board:" in explicit_board
    assert "task-A" in explicit_board


def test_super_tui_plain_renderer_does_not_auto_print_board(capsys) -> None:
    renderer = super_tui.SuperTuiProgressRenderer(
        enabled=True,
        plain=True,
        suppress_clock=True,
    )

    with renderer:
        renderer(
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

    stdout = capsys.readouterr().out
    assert "Diane · Session:" not in stdout
    assert "Diane · Board:" not in stdout
    assert "Board:" not in stdout
    assert "task-A" in "\n".join(super_tui._format_tui_board_lines(renderer.state))


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


def test_super_tui_chatbox_scheduler_queues_second_turn(tmp_path, capsys, monkeypatch) -> None:
    parser = super_tui.build_parser()
    calls: list[str] = []
    release_first = threading.Event()

    def fake_start(
        turn_args,
        run_parser,
        *,
        workspace_root,
        message_id,
        objective,
        forced_new,
        plan_only,
    ):
        del turn_args, run_parser, workspace_root, message_id, forced_new, plan_only
        calls.append(objective)

        def run() -> None:
            if objective == "first turn":
                release_first.wait(timeout=2)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    monkeypatch.setattr(super_tui, "_start_tui_background_dispatch", fake_start)
    scheduler = super_tui.TuiChatboxTurnScheduler(
        parser=parser,
        workspace_root=tmp_path,
        plain=True,
    )

    first = super_tui.TuiChatboxQueuedTurn(
        turn_args=Namespace(workspace=str(tmp_path)),
        message_id="msg-1",
        objective="first turn",
        forced_new=False,
        plan_only=False,
    )
    second = super_tui.TuiChatboxQueuedTurn(
        turn_args=Namespace(workspace=str(tmp_path)),
        message_id="msg-2",
        objective="second turn",
        forced_new=False,
        plan_only=False,
    )

    assert scheduler.submit(first) is True
    first_stdout = capsys.readouterr().out
    assert "Chat -> Routing" in first_stdout
    assert "Routing 0s..." in first_stdout
    assert "Thinking 0s: first turn" not in first_stdout
    assert scheduler.submit(second) is False
    assert calls == ["first turn"]
    queued_stdout = capsys.readouterr().out
    assert "Chat -> Queue" in queued_stdout
    assert "Queued behind the active turn." in queued_stdout

    release_first.set()
    for _ in range(40):
        if calls == ["first turn", "second turn"]:
            break
        threading.Event().wait(0.05)
    assert calls == ["first turn", "second turn"]


def test_super_tui_chatbox_scheduler_does_not_emit_loose_clock(tmp_path, monkeypatch) -> None:
    parser = super_tui.build_parser()
    release_first = threading.Event()

    def fake_start(
        turn_args,
        run_parser,
        *,
        workspace_root,
        message_id,
        objective,
        forced_new,
        plan_only,
    ):
        del turn_args, run_parser, workspace_root, message_id, objective, forced_new, plan_only

        thread = threading.Thread(target=lambda: release_first.wait(timeout=2), daemon=True)
        thread.start()
        return thread

    def fail_clock(*_args, **_kwargs):
        raise AssertionError("chatbox scheduler should not print a loose Thinking clock")

    monkeypatch.setattr(super_tui, "_start_tui_background_dispatch", fake_start)
    monkeypatch.setattr(super_tui, "_write_tui_clock_line", fail_clock)
    scheduler = super_tui.TuiChatboxTurnScheduler(
        parser=parser,
        workspace_root=tmp_path,
        plain=False,
    )

    assert scheduler.submit(
        super_tui.TuiChatboxQueuedTurn(
            turn_args=Namespace(workspace=str(tmp_path)),
            message_id="msg-1",
            objective="first turn",
            forced_new=False,
            plan_only=False,
        )
    )
    release_first.set()


def test_super_tui_chatbox_scheduler_refreshes_routing_in_box(tmp_path, capsys, monkeypatch) -> None:
    parser = super_tui.build_parser()
    current = {"value": 100.0}
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: current["value"])

    class FakeThread:
        def __init__(self) -> None:
            self.joins = 0

        def is_alive(self) -> bool:
            return self.joins < 2

        def join(self, timeout=None) -> None:
            del timeout
            self.joins += 1
            current["value"] = 101.2 if self.joins == 1 else 103.0

    fake_thread = FakeThread()
    scheduler = super_tui.TuiChatboxTurnScheduler(
        parser=parser,
        workspace_root=tmp_path,
        plain=True,
    )
    scheduler._active_thread = fake_thread  # exercise the watcher loop without a real sleeping thread

    scheduler._watch_thread(fake_thread, 100.0)

    stdout = capsys.readouterr().out
    assert "Diane · Chat -> Routing:" in stdout
    assert "Routing 1s..." in stdout
    assert "Narrator:" not in stdout
    assert "Still working on this turn." not in stdout


def test_super_tui_chatbox_scheduler_does_not_add_fixed_routing_fallback(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    parser = super_tui.build_parser()
    current = {"value": 100.0}
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: current["value"])
    super_tui._forget_tui_chatbox_thinking_block()
    scheduler = super_tui.TuiChatboxTurnScheduler(
        parser=parser,
        workspace_root=tmp_path,
        plain=True,
    )

    scheduler._print_thinking_block(100.0)
    first_stdout = capsys.readouterr().out
    assert "Routing 0s..." in first_stdout
    assert "waiting for the model response" not in first_stdout

    current["value"] = 111.0
    scheduler._print_thinking_block(100.0, replace_existing=True)
    second_stdout = capsys.readouterr().out
    assert "Routing 11s..." in second_stdout
    assert "waiting for the model response" not in second_stdout
    assert "Narrator:" not in second_stdout


def test_super_tui_chatbox_status_refresh_replaces_previous_box(tmp_path, capsys, monkeypatch) -> None:
    parser = super_tui.build_parser()
    monkeypatch.setattr(super_tui, "_tui_stdout_supports_control_sequences", lambda: True)
    scheduler = super_tui.TuiChatboxTurnScheduler(
        parser=parser,
        workspace_root=tmp_path,
        plain=False,
    )
    super_tui._TUI_CHATBOX_THINKING_LINE_COUNT = 4

    scheduler._print_thinking_block(
        100.0,
        replace_existing=True,
    )

    stdout = capsys.readouterr().out
    assert "\x1b[4F\x1b[J" in stdout
    assert "Chat -> Routing" in stdout
    assert "Routing" in stdout
    assert "Narrator:" not in stdout
    assert "Still working on this turn." not in stdout
    super_tui._forget_tui_chatbox_thinking_block()


def test_super_tui_clear_previous_output_can_defer_flush(monkeypatch) -> None:
    events: list[tuple[str, str]] = []

    class FakeStdout:
        def write(self, text: str) -> int:
            events.append(("write", text))
            return len(text)

        def flush(self) -> None:
            events.append(("flush", ""))

    monkeypatch.setattr(super_tui, "_tui_stdout_supports_control_sequences", lambda: True)
    monkeypatch.setattr(super_tui.sys, "stdout", FakeStdout())

    assert super_tui._clear_tui_previous_output_lines(3, flush=False)
    assert events == [("write", "\x1b[3F\x1b[J")]


def test_super_tui_rich_panel_line_count_matches_rendered_output(capsys, monkeypatch) -> None:
    pytest.importorskip("rich")
    monkeypatch.setattr(super_tui.shutil, "get_terminal_size", lambda fallback=(120, 24): super_tui.os.terminal_size(fallback))
    lines = [
        "The last run failed validation with a score of 0.10, so the GameLoop.gd edits from turn 22 did not fully stick. The spatial-hash timer was added, but the flow-field timer logic is half-wired and the compile log still shows errors.",
        "The practical next step is letting the queued validation run finish so the actual compile errors can drive another targeted edit cycle.",
    ]

    super_tui._print_tui_stream_block("Answer", lines, plain=False)

    stdout = capsys.readouterr().out
    non_empty = [line for line in stdout.splitlines() if line.strip()]
    assert non_empty[-1].startswith("╰")
    assert super_tui._tui_stream_block_line_count("Answer", lines, plain=False) == len(stdout.splitlines())
    assert stdout.endswith("\n\n")


def test_super_tui_chatbox_replacement_defers_clear_flush(monkeypatch) -> None:
    events: list[tuple[str, object]] = []

    def fake_clear(line_count: int, *, flush: bool = True) -> bool:
        events.append(("clear", (line_count, flush)))
        return True

    def fake_print(title: str, lines: list[str], *, plain: bool = False) -> None:
        events.append(("print", (title, tuple(lines), plain)))

    super_tui._forget_tui_chatbox_thinking_block()
    super_tui._TUI_CHATBOX_THINKING_LINE_COUNT = 4
    monkeypatch.setattr(super_tui, "_clear_tui_previous_output_lines", fake_clear)
    monkeypatch.setattr(super_tui, "_print_tui_stream_block", fake_print)
    monkeypatch.setattr(super_tui, "_tui_stream_block_line_count", lambda *args, **kwargs: 4)

    try:
        super_tui._print_tui_chatbox_thinking_block(
            ["Working 5s...", "Running validation command."],
            plain=False,
            replace_existing=True,
            status_label="Working",
        )
    finally:
        super_tui._forget_tui_chatbox_thinking_block()

    assert events == [
        ("clear", (4, False)),
        ("print", ("Chat -> Working", ("Working 5s...", "Running validation command."), False)),
    ]


def test_super_tui_queue_prefix_stays_with_replaceable_status_lane(capsys, monkeypatch) -> None:
    monkeypatch.setattr(super_tui, "_tui_stdout_supports_control_sequences", lambda: True)
    super_tui._forget_tui_chatbox_thinking_block()

    try:
        super_tui._print_tui_chatbox_thinking_block(
            ["Working 0s...", "Running validation command."],
            plain=True,
            status_label="Working",
        )
        capsys.readouterr()
        super_tui._set_tui_chatbox_prefix_blocks(
            [
                (
                    "Chat -> Queue",
                    [
                        "Queued behind the active turn.",
                        "Position: 1. Composer stays open.",
                    ],
                )
            ]
        )
        super_tui._refresh_tui_chatbox_status_block(
            100.0,
            status_label="Working",
            plain=True,
            replace_existing=True,
        )

        first_refresh = capsys.readouterr().out
        assert "\x1b[3F\x1b[J" in first_refresh
        assert "Diane · Chat -> Queue:" in first_refresh
        assert "Diane · Chat -> Working:" in first_refresh

        super_tui._refresh_tui_chatbox_status_block(
            100.0,
            status_label="Working",
            plain=True,
            replace_existing=True,
        )

        second_refresh = capsys.readouterr().out
        assert "\x1b[6F\x1b[J" in second_refresh
        assert "Diane · Chat -> Queue:" in second_refresh
        assert "Diane · Chat -> Working:" in second_refresh
    finally:
        super_tui._forget_tui_chatbox_thinking_block()


def test_super_tui_background_progress_replaces_chatbox_thinking_lane(capsys, monkeypatch) -> None:
    current = {"value": 101.0}
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: current["value"])
    monkeypatch.setattr(super_tui, "_tui_stdout_supports_control_sequences", lambda: True)
    super_tui._forget_tui_chatbox_thinking_block()
    renderer = super_tui.SuperTuiProgressRenderer(
        enabled=True,
        plain=True,
        chatbox_progress=True,
    )
    renderer.state.started_at_monotonic = 100.0

    renderer.note("Checking the relevant workspace context.")
    current["value"] = 106.0
    renderer.note("Using a terminal command because it advances this request.")

    stdout = capsys.readouterr().out
    assert "\x1b[3F\x1b[J" in stdout
    assert "Working 6s..." in stdout
    assert "Using a terminal command because it advances this request." in stdout
    super_tui._forget_tui_chatbox_thinking_block()


def test_super_tui_progress_note_does_not_echo_objective(capsys, monkeypatch) -> None:
    current = {"value": 104.0}
    objective = "can you continue to work until it really compiles"
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: current["value"])
    monkeypatch.setattr(super_tui, "_tui_stdout_supports_control_sequences", lambda: True)
    super_tui._forget_tui_chatbox_thinking_block()
    renderer = super_tui.SuperTuiProgressRenderer(
        enabled=True,
        objective=objective,
        plain=True,
        chatbox_progress=True,
    )
    renderer.state.started_at_monotonic = 100.0

    renderer.note(f"Using a terminal command because it is the direct way to advance {objective}.")

    stdout = capsys.readouterr().out
    assert objective not in stdout
    assert "advance the current task" in stdout
    super_tui._forget_tui_chatbox_thinking_block()


def test_super_tui_scheduler_refresh_preserves_working_detail(capsys, monkeypatch) -> None:
    current = {"value": 100.0}
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: current["value"])
    monkeypatch.setattr(super_tui, "_tui_stdout_supports_control_sequences", lambda: True)
    super_tui._forget_tui_chatbox_thinking_block()

    super_tui._print_tui_chatbox_thinking_block(
        [
            super_tui._tui_chatbox_status_text(100.0, status_label="Working"),
            "Running validation command.",
        ],
        plain=True,
        status_label="Working",
    )
    current["value"] = 110.0
    super_tui._refresh_tui_chatbox_status_block(
        100.0,
        status_label="Routing",
        plain=True,
        replace_existing=True,
    )

    stdout = capsys.readouterr().out
    assert "Diane · Chat -> Working:" in stdout
    assert "Working 10s..." in stdout
    assert "Running validation command." in stdout
    assert "Chat -> Routing" not in stdout
    super_tui._forget_tui_chatbox_thinking_block()


def test_super_tui_background_progress_notes_use_chatbox_thinking_lane(capsys) -> None:
    renderer = super_tui.SuperTuiProgressRenderer(
        enabled=True,
        plain=True,
        chatbox_progress=True,
    )

    renderer.note("Checking the relevant workspace context.")

    stdout = capsys.readouterr().out
    assert "Diane · Chat -> Working:" in stdout
    assert "Working 0s..." in stdout
    assert "Checking the relevant workspace context." in stdout
    assert "Diane · Checking the relevant workspace context." not in stdout


def test_super_tui_background_progress_uses_elapsed_chatbox_time(capsys, monkeypatch) -> None:
    current = {"value": 105.0}
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: current["value"])
    renderer = super_tui.SuperTuiProgressRenderer(
        enabled=True,
        plain=True,
        chatbox_progress=True,
    )
    renderer.state.started_at_monotonic = 100.0

    renderer.note("Checking the relevant workspace context.")

    stdout = capsys.readouterr().out
    assert "Diane · Chat -> Working:" in stdout
    assert "Working 5s..." in stdout
    assert "Thinking 0s..." not in stdout


def test_super_tui_run_turn_progress_keeps_submit_timer(tmp_path, capsys, monkeypatch) -> None:
    current = {"value": 130.0}
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: current["value"])
    super_tui._forget_tui_chatbox_thinking_block()
    parser = super_tui.build_parser()
    args = parser.parse_args(["repair the project", "--workspace", str(tmp_path), "--live", "--plain"])
    super_tui._prepare_args(args, ["--live"])
    args._tui_background_dispatch = True
    args._tui_turn_started_at = 100.0
    observed_starts: list[float] = []

    def fake_run(run_args, parser):
        del parser
        renderer = run_args._progress_renderer_factory(enabled=True, args=run_args)
        observed_starts.append(renderer.state.started_at_monotonic)
        renderer(
            {
                "event": "run.log.started",
                "objective": run_args.target,
                "task_id": "super-dan-live:11",
                "workspace_root": str(tmp_path),
            }
        )
        observed_starts.append(renderer.state.started_at_monotonic)
        renderer.note("Running validation command.")
        return 0

    monkeypatch.setattr(super_tui.super_cli, "_run_super_turn", fake_run)

    assert super_tui._run_tui_turn(args, parser, force_live=True) == 0
    stdout = capsys.readouterr().out
    assert observed_starts == [100.0, 100.0]
    assert "Working 30s..." in stdout
    super_tui._forget_tui_chatbox_thinking_block()


def test_super_tui_background_stream_progress_uses_chatbox_lane(capsys, monkeypatch) -> None:
    current = {"value": 105.0}
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: current["value"])
    args = Namespace(
        plain=False,
        json=False,
        quiet_progress=False,
        raw_events=False,
        _tui_background_dispatch=True,
        _tui_turn_started_at=100.0,
    )

    super_tui._emit_tui_stream_line(args, "Source inspected: report.md")

    stdout = capsys.readouterr().out
    assert "Diane · Chat -> Working:" in stdout
    assert "Working 5s..." in stdout
    assert "Source inspected: report.md" in stdout
    assert "Diane · Source inspected" not in stdout


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

    explicit_args = parser.parse_args(["--workspace", str(tmp_path), "--plain", "--async-agent"])
    super_tui._prepare_args(explicit_args, ["--async-agent"])
    monkeypatch.setattr(super_tui.sys, "stdin", FakeStdin(True))
    assert super_tui._should_dispatch_tui_turn_in_background(explicit_args, async_agent_enabled=True)

    active_workspace = tmp_path / "active"
    active_workspace.mkdir()
    super_tui._append_tui_transcript_entry(
        active_workspace,
        role="assistant_progress",
        text="Background run started.",
        metadata={
            "tui_board_event": {
                "event": "tui.board.admission",
                "task_id": "task-A",
                "run_id": "run-A",
                "objective": "existing work",
                "status": "running",
                "phase": "background_running",
                "decision": "start_parallel",
            }
        },
    )
    active_args = parser.parse_args(["--workspace", str(active_workspace), "--plain"])
    super_tui._prepare_args(active_args, [])
    monkeypatch.setattr(super_tui.sys, "stdin", FakeStdin(True))
    assert super_tui._should_dispatch_tui_turn_in_background(active_args, async_agent_enabled=True)

    explicit_args.plan_only = True
    assert not super_tui._should_dispatch_tui_turn_in_background(explicit_args, async_agent_enabled=True)

    explicit_args.plan_only = False
    monkeypatch.setattr(super_tui.sys, "stdin", FakeStdin(True))
    setattr(explicit_args, "_stdin_is_tty", True)
    assert super_tui._should_dispatch_tui_turn_in_background(explicit_args, async_agent_enabled=False)

    monkeypatch.setattr(super_tui.sys, "stdin", FakeStdin(False))
    setattr(explicit_args, "_stdin_is_tty", False)
    assert not super_tui._should_dispatch_tui_turn_in_background(explicit_args, async_agent_enabled=True)
    assert not super_tui._should_dispatch_tui_turn_in_background(explicit_args, async_agent_enabled=False)


def test_super_tui_transcript_history_payload_excludes_current_turn(tmp_path) -> None:
    super_tui._append_tui_transcript_entry(tmp_path, role="user", text="combine the two reports")
    super_tui._append_tui_transcript_entry(
        tmp_path,
        role="assistant_final",
        text="Created opec-uae-combined-report-2025.md.",
    )
    super_tui._append_tui_transcript_entry(tmp_path, role="user", text="review it now")

    history = super_tui._tui_transcript_history_payload(tmp_path, current_text="review it now")

    assert history == [
        {"role": "user", "content": "combine the two reports"},
        {"role": "assistant", "content": "Created opec-uae-combined-report-2025.md."},
    ]


def test_super_tui_queue_summary_hides_internal_counters_when_idle() -> None:
    raw = "\n".join(
        [
            "Diane queues",
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
    recent = "\n".join(state.recent_event_lines())
    assert "Board:" not in recent
    assert "round=2" not in recent


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
    super_tui._append_tui_transcript_entry(workspace, role="user", text="what changed?")
    super_tui._append_tui_transcript_entry(
        workspace,
        role="assistant_final",
        text="The report was merged into one file.",
    )
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
    assert payload["chat_request"]["history"] == [
        {"role": "user", "content": "what changed?"},
        {"role": "assistant", "content": "The report was merged into one file."},
    ]
    assert (
        payload["chat_request"]["surface_context"]["conversation"]["recent_turns"]
        == payload["chat_request"]["history"]
    )
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
    assert "Diane · Answer:" in stdout
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
    assert "Diane · Command:" in capsys.readouterr().out


def test_super_tui_explicit_async_turn_uses_local_store_without_server(
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
            "--async-agent",
            "--async-agent-backend",
            "deterministic",
        ]
    )
    super_tui._prepare_args(args, ["--async-agent"])
    _patch_tui_route(monkeypatch, permission="write", complexity="complex", routed_with_model=True)
    inputs = iter(["build script_a.py", "/exit"])
    started_runs: list[str] = []

    def fake_start_background(*, workspace_root, run_id, backend_name, overrides, remaining_continuations):
        assert workspace_root == workspace
        assert backend_name == "deterministic"
        assert overrides["metadata"]["surface"] == "super-tui"
        assert remaining_continuations == 16
        started_runs.append(run_id)

    def fail_post(*args, **kwargs):
        raise AssertionError("explicit local TUI async admission should not require HTTP")

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
    assert "Diane · Answer:" in stdout
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


def test_super_tui_background_completion_formats_structured_summary() -> None:
    run = Namespace(
        run_id="run-123456",
        task_id="task-abc",
        status="completed",
        latest_summary=(
            "{'candidate_id':'super-dan-live-general-001',"
            "'change_summary':'Created opec-report-2025.md with current evidence.',"
            "'files_created':['opec-report-2025.md'],"
            "'risks':['Some claims need verification.']}"
        ),
        metadata={},
    )

    lines = super_tui._tui_background_completion_lines(run, [])

    assert lines[0] == (
        "Background run `task-abc` finished. Created `opec-report-2025.md`: "
        "Created opec-report-2025.md with current evidence."
    )
    assert lines[1] == "Note: Some claims need verification."
    assert "candidate_id" not in "\n".join(lines)


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
    assert "Diane · Plan:" in stdout
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
    assert "Diane · Plan:" in stdout
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
        lambda run_args, objective: seen.append(objective) or print("Diane · Plan: questions") or 0,
    )
    monkeypatch.setattr(
        super_tui,
        "_submit_tui_async_admission",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("plan mode must not admit background work")),
    )

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    assert seen == ["refine the Godot development plan"]
    assert "Diane · Plan: questions" in capsys.readouterr().out


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
    assert "Diane · Help:" in stdout
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
    assert "Diane · System:" in stdout
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


def test_super_tui_async_continue_uses_append_path(
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
    inputs = iter(["/continue add validation", "/exit"])
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
    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace: [])
    monkeypatch.setattr(super_tui, "_post_tui_async_admission", fake_post)

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    assert captured_payloads[0]["background"] is False
    assert captured_payloads[0]["chat_request"]["message"] == "/append add validation"


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

    assert "Diane · Session:" in text
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
    assert all(len(part) <= 56 for part in wrapped)
    assert " ".join(part.strip() for part in wrapped) == line.strip()


def test_super_tui_transcript_preview_marks_clipped_long_answers() -> None:
    entry = super_tui.TuiTranscriptEntry(
        role="assistant_narrator",
        text="This long narrator answer should be marked as clipped in transcript replay. " * 40,
        metadata={},
    )

    lines = super_tui._transcript_entry_body_lines(entry)

    assert lines[-1] == "... more in transcript"


def test_super_tui_stream_answer_plain_renders_single_block(capsys) -> None:
    args = Namespace(plain=True, json=False, quiet_progress=False, raw_events=False)
    state = super_tui.SuperTuiState()
    state._record_answer("This is the project overview.")
    state._record_answer("- Pipeline: docs/plan.md")

    super_tui._emit_tui_stream_answer(args, state)

    stdout = capsys.readouterr().out
    assert stdout.count("Diane · Answer:") == 1
    assert "Diane · Answer: This is the project overview." not in stdout
    assert "  This is the project overview." in stdout
    assert "  - Pipeline: docs/plan.md" in stdout


def test_super_tui_transcript_summary_keeps_full_answer() -> None:
    state = super_tui.SuperTuiState(status="completed", phase="done")
    state._record_answer("This is the project overview.")
    state._record_answer("- Pipeline: docs/plan.md")

    summary = state.transcript_summary()

    assert "This is the project overview." in summary
    assert "- Pipeline: docs/plan.md" in summary


def test_super_tui_transcript_history_keeps_useful_recent_answers_visible() -> None:
    long_answer = (
        "I can control a browser through the browser_control capability pack, which is available here but not currently active. "
        "When activated, it gives me persistent browser tabs and lets me navigate URLs, inspect page titles, DOM and HTML, "
        "read visible text, take screenshots, click and fill form elements, type, select dropdowns, wait for conditions, "
        "and handle downloads. If you want to use it, I can open a browser session and start interacting with a specific site."
    )
    entry = super_tui.TuiTranscriptEntry(
        role="assistant_final",
        text=long_answer,
        created_at="",
        metadata={},
    )

    formatted = super_tui._format_transcript_line(entry)

    assert formatted.startswith("Diane:")
    assert "I can control a browser" in formatted
    assert "handle downloads" in formatted
    assert "... more in transcript" not in formatted


def test_super_tui_transcript_history_still_bounds_massive_answers() -> None:
    long_answer = "This answer is intentionally enormous and should still be bounded. " * 90
    entry = super_tui.TuiTranscriptEntry(
        role="assistant_final",
        text=long_answer,
        created_at="",
        metadata={},
    )

    formatted = super_tui._format_transcript_line(entry)

    assert formatted.startswith("Diane:")
    assert len(formatted) < len(long_answer)
    assert "... more in transcript" in formatted


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

    assert formatted.startswith("Diane:")
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
    assert "Diane · Narrator: The executor is checking README.md before making changes." in stdout
    assert "  No files have changed yet." in stdout
    assert "Diane · No files have changed yet." not in stdout
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
    assert "Diane · Session:" not in stdout


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


def test_super_tui_clock_detects_prompt_toolkit_wrapped_stdout(monkeypatch) -> None:
    class FakeStream:
        def __init__(self, tty: bool) -> None:
            self.tty = tty

        def isatty(self) -> bool:
            return self.tty

    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(super_tui.sys, "stdout", FakeStream(False))
    monkeypatch.setattr(super_tui.sys, "stdin", FakeStream(True))

    assert super_tui._tui_stdout_supports_control_sequences()


def test_super_tui_background_clock_is_suppressed_above_live_prompt(capsys, monkeypatch) -> None:
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
    assert stdout == ""
    assert "\x1b" not in stdout
    assert "[2K" not in stdout


def test_super_tui_background_narrator_wait_clock_is_suppressed(capsys, monkeypatch) -> None:
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
    assert stdout == ""
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
    assert "Relevant context is available for copy source files" not in joined
    assert "Checking the relevant workspace context." not in joined
    assert "The terminal command finished; using that result for the current task." in joined
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
    assert "Changed docs/todo.md for this request." in joined
    assert "Still working after 10s." in joined
    assert "Validation passed" in joined
    assert "Run finished successfully" in joined
    assert state.answer_lines[-1].startswith("Run finished successfully")
    visible = "\n".join(state.recent_event_lines())
    assert "Answer:" in visible
    assert "Outcome:" in visible
    assert visible.count("Run finished successfully") == 1


def test_super_tui_narrator_reports_dedupe_semantic_repeats() -> None:
    state = super_tui.SuperTuiState(mode_line="complex write - test")

    state._record_narrator_report(
        super_tui.NarratorReport(
            kind="progress",
            text=(
                "The executor ran two short terminal commands back-to-back, "
                "likely checking what report files exist."
            ),
        )
    )
    state._record_narrator_report(
        super_tui.NarratorReport(
            kind="checkpoint",
            text=(
                "The executor ran two short terminal commands back to back and "
                "is likely checking what report files exist."
            ),
        )
    )

    assert len(state.narrator_lines) == 1


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
    assert "Diane · Narrator:" in stdout
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
    assert "Diane · Narrator:" in stdout
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


def test_super_tui_executor_sidecar_progress_waits_ten_seconds(monkeypatch, tmp_path) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["merge data", "--workspace", str(tmp_path), "--model", "fake-model", "--plain"])
    super_tui._prepare_args(args, [])
    args._tui_routed_with_model = True
    current_time = {"value": 109.0}
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: current_time["value"])

    renderer = super_tui.SuperTuiProgressRenderer(enabled=True, objective="merge data", workspace=str(tmp_path), plain=True)
    renderer.configure_model_sidecar(args)
    renderer.state.observe(
        {
            "event": "run.log.started",
            "objective": "merge data",
            "task_id": "super-dan-live:14b",
            "workspace_root": str(tmp_path),
        }
    )
    renderer._sidecar_last_started_at = 100.0

    assert renderer._maybe_start_model_sidecar({"event": "tool.completed", "tool_id": "file_read"}) is False


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
    assert "Diane: Run completed; trace .dan-super/runs/turn-01/events.jsonl." in stdout
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
    assert "Diane · Answer:" in stdout
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
    assert "chat box" in stdout
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
    assert ("c-v",) in keys
    assert ("escape",) in keys
    assert ("enter",) in keys or any(str(key).lower().endswith("controlm") for row in keys for key in row)


def test_super_tui_chatbox_prompt_has_inner_margin_only() -> None:
    text = "".join(fragment for _style, fragment in super_tui._tui_chatbox_prompt("super-tui> "))

    assert "super-tui> " in text
    assert text == " super-tui> "
    assert "Diane · Chat" not in text
    assert "╭" not in text
    assert "╰" not in text


def test_super_tui_panel_title_stays_in_border() -> None:
    title = super_tui._tui_panel_title("Answer")

    assert title == "Dear Diane · Answer:"


def test_super_tui_vertical_ornament_matches_content_height() -> None:
    assert super_tui._tui_vertical_ornament(1) == "◆"
    assert super_tui._tui_vertical_ornament(2) == "╭\n╰"
    assert super_tui._tui_vertical_ornament(3) == "╭\n│\n╰"


def test_super_tui_prompt_rounded_frame_border_restores_prompt_toolkit_border() -> None:
    try:
        import importlib

        from prompt_toolkit.layout.containers import Window
        from prompt_toolkit.widgets import base as widgets_base
    except ImportError:
        pytest.skip("prompt_toolkit not installed")

    prompt_module = importlib.import_module("prompt_toolkit.shortcuts.prompt")
    border = widgets_base.Border
    original = (border.TOP_LEFT, border.TOP_RIGHT, border.BOTTOM_LEFT, border.BOTTOM_RIGHT)
    original_frame = prompt_module.Frame
    with super_tui._tui_prompt_rounded_frame_border():
        assert (border.TOP_LEFT, border.TOP_RIGHT, border.BOTTOM_LEFT, border.BOTTOM_RIGHT) == ("╭", "╮", "╰", "╯")
        assert prompt_module.Frame is not original_frame
        frame = prompt_module.Frame(Window())
        assert getattr(frame, "_dan_tui_frame_title") == "Dear Diane · Chat:"
        assert "|" not in getattr(frame, "_dan_tui_frame_title")
    assert (border.TOP_LEFT, border.TOP_RIGHT, border.BOTTOM_LEFT, border.BOTTOM_RIGHT) == original
    assert prompt_module.Frame is original_frame


def test_super_tui_ctrl_v_clipboard_image_capture_from_env(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "shot.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setenv("DAN_SUPER_TUI_CLIPBOARD_IMAGE_PATH", str(source))

    result = super_tui._capture_tui_clipboard_image(workspace)

    assert result.ok
    assert result.backend == "DAN_SUPER_TUI_CLIPBOARD_IMAGE_PATH"
    assert result.display_path.startswith(".dan-super/tui/attachments/")
    captured = Path(result.path)
    assert captured.exists()
    assert captured.read_bytes() == source.read_bytes()


def test_super_tui_image_mentions_become_attachment_payloads(tmp_path) -> None:
    image = tmp_path / ".dan-super" / "tui" / "attachments" / "shot.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    payloads = super_tui._tui_image_attachment_payloads_from_text(
        "please review @.dan-super/tui/attachments/shot.png",
        tmp_path,
    )

    assert len(payloads) == 1
    assert payloads[0]["kind"] == "image"
    assert payloads[0]["local_path"] == str(image.resolve())
    assert payloads[0]["mime_type"] == "image/png"
    assert payloads[0]["metadata"]["relative_path"] == ".dan-super/tui/attachments/shot.png"


def test_super_tui_async_surface_turn_carries_image_attachments(tmp_path) -> None:
    image = tmp_path / ".dan-super" / "tui" / "attachments" / "shot.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    super_tui._append_tui_transcript_entry(tmp_path, role="user", text="summarize the screenshot context")
    super_tui._append_tui_transcript_entry(
        tmp_path,
        role="assistant_final",
        text="The previous screenshot showed a prompt collision.",
    )
    args = Namespace(_tui_selected_skill_mentions=[], _tui_communication_policy=None)

    turn = super_tui._build_tui_async_surface_turn(
        args,
        workspace_root=tmp_path,
        text="review @.dan-super/tui/attachments/shot.png",
    )

    assert len(turn.attachments) == 1
    assert turn.attachments[0].kind == "image"
    assert turn.attachments[0].local_path == str(image.resolve())
    assert turn.metadata["surface_context"]["appended_attachments"][0]["kind"] == "image"
    assert turn.metadata["history"] == [
        {"role": "user", "content": "summarize the screenshot context"},
        {"role": "assistant", "content": "The previous screenshot showed a prompt collision."},
    ]
    assert turn.metadata["surface_context"]["conversation"]["recent_turns"] == turn.metadata["history"]


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
    assert getattr(session, "_dan_reserve_space_for_menu") == 0


def test_super_tui_prompt_toolkit_output_disables_cpr_warning(monkeypatch) -> None:
    try:
        import prompt_toolkit.output.defaults as output_defaults
    except ImportError:
        pytest.skip("prompt_toolkit not installed")

    class FakeOutput:
        enable_cpr = True

    fake_output = FakeOutput()
    monkeypatch.setattr(output_defaults, "create_output", lambda: fake_output)

    output = super_tui._build_prompt_toolkit_output()

    assert output is fake_output
    assert fake_output.enable_cpr is False


def test_super_tui_prompt_stdout_bridge_preserves_ansi(monkeypatch, tmp_path) -> None:
    try:
        import prompt_toolkit.patch_stdout as patch_stdout_module
    except ImportError:
        pytest.skip("prompt_toolkit not installed")

    prompt_messages = []
    prompt_kwargs = []

    class FakeSession:
        def prompt(self, *_args, **kwargs) -> str:
            prompt_messages.append(_args[0] if _args else None)
            prompt_kwargs.append(kwargs)
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
    assert prompt_messages
    assert " super-tui> " == "".join(fragment for _style, fragment in prompt_messages[0])
    assert prompt_kwargs
    assert prompt_kwargs[0].get("show_frame") is True
    assert "bottom_toolbar" not in prompt_kwargs[0]


def test_super_tui_run_command_parser_is_honest_for_direct_local_tui() -> None:
    append = super_tui._parse_tui_run_command("/append use the blue theme")
    assert append is not None
    assert append.command == "append"
    assert append.payload == "use the blue theme"
    assert "async V2 active runs" in append.message

    continue_command = super_tui._parse_tui_run_command("/continue finalize the test")
    assert continue_command is not None
    assert continue_command.command == "append"
    assert continue_command.payload == "finalize the test"
    assert "Continue is treated as an append" in continue_command.message

    cancel = super_tui._parse_tui_run_command("/cancel")
    assert cancel is not None
    assert cancel.command == "cancel"
    assert "not wired into direct local Diane runs yet" in cancel.message

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
    assert "no Diane run" not in stdout


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
    assert "Diane · Session:" not in stdout
    assert "objective: build a dashboard" not in stdout
    assert "Got it. Starting with the relevant context." not in stdout
    assert "dan: You asked:" not in stdout
    assert "Diane · Answer:" in stdout
    assert "Diane · Outcome:" in stdout
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


def test_super_tui_core_model_router_parses_communication_and_execution_policy() -> None:
    class FakeProvider:
        async def complete(self, **kwargs):
            prompt = kwargs["messages"][0]["content"]
            assert "controller_lane" in prompt
            assert "permission_scope" in prompt
            assert "phase_shape" in prompt
            assert "communication_policy.answer_budget" in prompt
            assert "communication_policy.progress_detail" in prompt
            assert "execution_policy.autonomy_mode" in prompt
            assert "execution_policy.max_auto_fix_rounds" in prompt
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
                        "execution_policy": {
                            "autonomy_mode": "continuous",
                            "stop_condition": "validation_passes",
                            "max_work_seconds": 1200,
                            "max_auto_fix_rounds": 5,
                            "max_validation_cycles": 6,
                            "allow_repair_cycles": True,
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
    assert decision.execution_policy.autonomy_mode == "continuous"
    assert decision.execution_policy.stop_condition == "validation_passes"
    assert decision.execution_policy.max_work_seconds == 1200
    assert decision.execution_policy.max_auto_fix_rounds == 5
    assert decision.execution_policy.max_validation_cycles == 6


def test_super_tui_core_model_router_parses_canonical_surface_policy_without_legacy_lane() -> None:
    class FakeProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            prompt = kwargs["messages"][0]["content"]
            if self.calls == 1:
                assert "The canonical policy fields are orthogonal and complete" in prompt
                assert "capability questions" in prompt
                assert "do not claim those capabilities are unavailable" in prompt
                assert "Do not choose workspace_write merely because browser navigation" in prompt
            else:
                assert "the route selected validation_gate" in kwargs["messages"][1]["content"]
            return CompletionResult(
                text=json.dumps(
                    {
                        "controller_lane": "execute",
                        "permission_scope": "transient_execute",
                        "evidence_policy": {
                            "freshness": "fresh",
                            "scope": "targeted",
                            "sources": ["validation"],
                        },
                        "phase_shape": "validation_gate",
                        "autonomy": "guided",
                        "latency_policy": {"class": "fast", "max_work_seconds": 30},
                        "response_policy": {
                            "answer_budget": "brief",
                            "interaction_style": "answer_only",
                        },
                        "progress_policy": {"detail": "quiet", "heartbeat_seconds": 10},
                        "admission_policy": {"task_relation": "auto"},
                        "confidence": 0.94,
                        "rationale": "A fresh compile check is a targeted transient validation.",
                        "clarification": "",
                    }
                )
            )

    provider = FakeProvider()
    decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            provider,
            "does it compile?",
            model="fake-router",
        )
    )

    assert provider.calls == 2
    assert decision.lane == "executor write"
    assert decision.surface_policy.controller_lane == "execute"
    assert decision.surface_policy.permission_scope == "transient_execute"
    assert decision.surface_policy.phase_shape == "validation_gate"
    assert decision.surface_policy.evidence_policy.sources == ("validation",)
    assert decision.surface_policy.latency_policy.latency_class == "fast"
    assert decision.surface_policy.latency_policy.max_work_seconds == 30
    assert decision.surface_policy.capability_packs == ()
    assert decision.communication_policy.answer_budget == "brief"
    assert decision.communication_policy.latency_preference == "fast"
    assert decision.communication_policy.progress_detail == "quiet"
    assert decision.execution_policy.stop_condition == "validation_passes"
    assert decision.execution_policy.allow_repair_cycles is False


def test_super_tui_core_model_router_uses_canonical_policy_over_legacy_lane() -> None:
    class FakeProvider:
        async def complete(self, **kwargs):
            del kwargs
            return CompletionResult(
                text=json.dumps(
                    {
                        "controller_lane": "execute",
                        "permission_scope": "workspace_write",
                        "phase_shape": "one_pass",
                        "lane": "executor_read_only",
                        "complexity": "complex",
                        "confidence": 0.89,
                        "rationale": "Canonical policy says this needs execution despite a stale projection.",
                        "clarification": "",
                    }
                )
            )

    decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            FakeProvider(),
            "make the requested patch",
            model="fake-router",
        )
    )

    assert decision.lane == "executor write"
    assert decision.surface_policy.controller_lane == "execute"
    assert decision.surface_policy.permission_scope == "workspace_write"


def test_super_tui_core_model_router_allows_browser_answer_without_workspace_write() -> None:
    class FakeProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            del kwargs
            return CompletionResult(
                text=json.dumps(
                    {
                        "controller_lane": "inspect",
                        "permission_scope": "transient_execute",
                        "evidence_policy": {
                            "freshness": "fresh",
                            "scope": "targeted",
                            "sources": ["external"],
                        },
                        "phase_shape": "probe",
                        "autonomy": "guided",
                        "latency_policy": {"class": "fast", "max_work_seconds": 45},
                        "response_policy": {
                            "answer_budget": "normal",
                            "interaction_style": "answer_only",
                        },
                        "progress_policy": {"detail": "compact", "heartbeat_seconds": 10},
                        "admission_policy": {"task_relation": "auto"},
                        "capability_packs": ["browser_control"],
                        "confidence": 0.91,
                        "rationale": "Fresh Reuters headlines need transient browser navigation and external evidence, not a workspace artifact.",
                        "clarification": "",
                    }
                )
            )

    provider = FakeProvider()
    decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            provider,
            "go to Reuters and feed me the latest news",
            model="fake-router",
        )
    )

    assert provider.calls == 1
    assert decision.lane == "executor read-only"
    assert decision.surface_policy.controller_lane == "inspect"
    assert decision.surface_policy.permission_scope == "transient_execute"
    assert decision.surface_policy.evidence_policy.sources == ("external",)
    assert decision.surface_policy.capability_packs == ("browser_control",)


def test_super_tui_core_model_router_rechecks_validation_gate_for_proposal_request() -> None:
    bad_payload = {
        "controller_lane": "execute",
        "permission_scope": "transient_execute",
        "evidence_policy": {
            "freshness": "fresh",
            "scope": "targeted",
            "sources": ["run_state", "validation", "workspace"],
        },
        "phase_shape": "validation_gate",
        "autonomy": "guided",
        "latency_policy": {"class": "normal", "max_work_seconds": 30},
        "response_policy": {"answer_budget": "normal", "interaction_style": "act_then_report"},
        "progress_policy": {"detail": "compact", "heartbeat_seconds": 10},
        "admission_policy": {"task_relation": "append_active"},
        "lane": "executor_write",
        "complexity": "complex",
        "confidence": 0.91,
        "rationale": "The user reports lag and wants a proposal, but this bad route picks validation.",
        "clarification": "",
        "execution_policy": {
            "autonomy_mode": "guided",
            "stop_condition": "validation_passes",
            "max_work_seconds": 300,
            "max_auto_fix_rounds": 1,
            "max_validation_cycles": 2,
            "allow_repair_cycles": True,
        },
    }
    repaired_payload = {
        "controller_lane": "plan",
        "permission_scope": "none",
        "evidence_policy": {"freshness": "known_state", "scope": "targeted", "sources": ["conversation"]},
        "phase_shape": "none",
        "autonomy": "manual",
        "latency_policy": {"class": "fast", "max_work_seconds": 30},
        "response_policy": {"answer_budget": "normal", "interaction_style": "answer_only"},
        "progress_policy": {"detail": "quiet", "heartbeat_seconds": 10},
        "admission_policy": {"task_relation": "auto"},
        "lane": "plan_mode",
        "complexity": "simple",
        "confidence": 0.9,
        "rationale": "The user asks what to propose after lag, so answer with a plan instead of validation.",
        "clarification": "",
        "execution_policy": {
            "autonomy_mode": "manual",
            "stop_condition": "objective_satisfied",
            "max_work_seconds": 30,
            "max_auto_fix_rounds": 0,
            "max_validation_cycles": 1,
            "allow_repair_cycles": False,
        },
    }

    class FakeProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return CompletionResult(text=json.dumps(bad_payload))
            assert "the route selected validation_gate" in kwargs["messages"][1]["content"]
            return CompletionResult(text=json.dumps(repaired_payload))

    provider = FakeProvider()
    decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            provider,
            "good good. but it still feels very laggy, what do you propose",
            model="fake-router",
        )
    )

    assert provider.calls == 2
    assert decision.lane == "plan mode"
    assert decision.surface_policy.phase_shape != "validation_gate"


def test_super_tui_core_model_router_rechecks_read_only_action_policy() -> None:
    bad_payload = {
        "lane": "executor_read_only",
        "complexity": "simple",
        "confidence": 0.83,
        "rationale": "The user asks for fresh validation truth about whether the project compiles.",
        "clarification": "",
        "communication_policy": {
            "answer_budget": "brief",
            "latency_preference": "fast",
            "progress_detail": "compact",
            "interaction_style": "act_then_report",
        },
    }
    repaired_payload = {
        "controller_lane": "execute",
        "permission_scope": "transient_execute",
        "evidence_policy": {"freshness": "fresh", "scope": "targeted", "sources": ["validation"]},
        "phase_shape": "validation_gate",
        "autonomy": "guided",
        "latency_policy": {"class": "fast", "max_work_seconds": 30},
        "response_policy": {"answer_budget": "brief", "interaction_style": "answer_only"},
        "progress_policy": {"detail": "quiet", "heartbeat_seconds": 10},
        "admission_policy": {"task_relation": "auto"},
        "lane": "executor_write",
        "complexity": "simple",
        "confidence": 0.92,
        "rationale": "Fresh compile truth requires a transient validation command.",
        "clarification": "",
        "execution_policy": {
            "autonomy_mode": "guided",
            "stop_condition": "validation_passes",
            "max_work_seconds": 30,
            "max_auto_fix_rounds": 0,
            "max_validation_cycles": 1,
            "allow_repair_cycles": False,
        },
    }

    class FakeProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return CompletionResult(text=json.dumps(bad_payload))
            assert "no-execution/read-only compatibility lane" in kwargs["messages"][1]["content"]
            return CompletionResult(text=json.dumps(repaired_payload))

    provider = FakeProvider()
    decision = asyncio.run(
        super_tui.route_agent_turn_intent_with_model(
            provider,
            "can you make sure it successfully compiles?",
            model="fake-router",
        )
    )

    assert provider.calls == 2
    assert decision.lane == "executor write"
    assert decision.surface_policy.controller_lane == "execute"
    assert decision.surface_policy.permission_scope == "transient_execute"
    assert decision.surface_policy.phase_shape == "validation_gate"


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


def test_super_tui_tui_decision_preserves_core_policies() -> None:
    core_decision = super_tui.classify_agent_turn_intent("/progress")
    tui_decision = super_tui._tui_decision_from_core_decision(core_decision)

    assert tui_decision.communication_policy.answer_budget == "normal"
    assert tui_decision.communication_policy.interaction_style == "answer_only"
    assert tui_decision.execution_policy.autonomy_mode == "guided"


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


def test_super_tui_brief_answer_budget_keeps_complete_visible_answer() -> None:
    state = super_tui.SuperTuiState(status="completed")
    state.communication_policy = super_tui.AgentCommunicationPolicy(answer_budget="brief")
    for index in range(8):
        state._record_answer(f"line {index}")

    assert state.final_answer_lines() == [f"line {index}" for index in range(8)]


def test_super_tui_normal_answer_budget_keeps_complete_visible_answer() -> None:
    state = super_tui.SuperTuiState(status="completed")
    state.communication_policy = super_tui.AgentCommunicationPolicy(answer_budget="normal")
    for index in range(20):
        state._record_answer(f"line {index}")

    assert len(state.final_answer_lines()) == 20
    assert state.final_answer_lines()[-1] == "line 19"


def test_super_tui_detailed_answer_budget_preserves_longer_findings() -> None:
    state = super_tui.SuperTuiState(status="completed")
    state.communication_policy = super_tui.AgentCommunicationPolicy(answer_budget="detailed")
    for index in range(25):
        state._record_answer(f"finding {index}")

    assert len(state.final_answer_lines()) == 25
    assert state.final_answer_lines()[-1] == "finding 24"


def test_super_tui_transcript_summary_keeps_complete_visible_answer() -> None:
    state = super_tui.SuperTuiState(status="completed")
    state.communication_policy = super_tui.AgentCommunicationPolicy(answer_budget="brief")
    for index in range(7):
        state._record_answer(f"visible {index}")

    summary = state.transcript_summary(exit_code=0)

    assert "visible 0" in summary
    assert "visible 4" in summary
    assert "visible 5" in summary
    assert "visible 6" in summary


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


def test_super_tui_final_answer_model_keeps_complete_text_with_brief_budget(monkeypatch) -> None:
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

    assert len(state.answer_lines) == 9
    assert state.answer_lines[-1] == "The answer line 8 summarizes useful completed work."
    assert provider.calls[0]["max_tokens"] == 360
    assert "do not exceed 5 visible lines" in provider.calls[0]["messages"][0]["content"]
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


def test_super_tui_validation_command_discovery_caches_by_script_fingerprint(tmp_path) -> None:
    script = tmp_path / "godot" / "compile_check.sh"
    script.parent.mkdir()
    script.write_text("echo ok\n", encoding="utf-8")

    first = super_tui._discover_tui_validation_command(tmp_path)
    second = super_tui._discover_tui_validation_command(tmp_path)

    assert first is not None
    assert first.cache_hit is False
    assert first.label == "godot/compile_check.sh"
    assert first.command == "cd godot && bash ./compile_check.sh"
    assert second is not None
    assert second.cache_hit is True
    assert second.command == first.command


def test_super_tui_validation_command_cache_invalidates_when_script_changes(tmp_path) -> None:
    script = tmp_path / "compile_check.sh"
    script.write_text("echo first\n", encoding="utf-8")
    assert super_tui._discover_tui_validation_command(tmp_path) is not None

    script.write_text("echo second\n", encoding="utf-8")
    refreshed = super_tui._discover_tui_validation_command(tmp_path)

    assert refreshed is not None
    assert refreshed.cache_hit is False


def test_super_tui_direct_validation_gate_runs_discovered_command(tmp_path, capsys) -> None:
    script = tmp_path / "compile_check.sh"
    script.write_text("echo compile ok\n", encoding="utf-8")
    decision = super_tui.TuiIntentDecision(
        permission="write",
        complexity="complex",
        confidence=0.95,
        rationale="fresh validation requested",
        communication_policy=super_tui.AgentCommunicationPolicy(
            answer_budget="brief",
            latency_preference="fast",
            progress_detail="quiet",
            interaction_style="answer_only",
        ),
        surface_policy=super_tui.normalize_agent_surface_policy(
            {
                "controller_lane": "execute",
                "permission_scope": "transient_execute",
                "phase_shape": "validation_gate",
                "evidence_policy": {"freshness": "fresh", "scope": "targeted", "sources": ["validation"]},
                "latency_policy": {"class": "fast", "max_work_seconds": 30},
            }
        ),
    )
    args = Namespace(
        workspace=str(tmp_path),
        target="does it compile now?",
        plan_only=False,
        raw_events=False,
        plain=True,
        json=False,
        quiet_progress=True,
        validation_command=[],
        _tui_turn_started_at=None,
        _tui_transcript_workspace="",
    )

    exit_code = super_tui._run_tui_direct_validation_gate_if_applicable(args, decision)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Yes - fresh validation passed" in output
    assert "compile_check.sh" in output
    assert "Outcome" not in output


def test_super_tui_direct_validation_gate_skips_non_validation_policy(tmp_path) -> None:
    decision = super_tui.TuiIntentDecision(
        permission="write",
        complexity="complex",
        confidence=0.8,
        rationale="regular write",
    )
    args = Namespace(workspace=str(tmp_path), plan_only=False)

    assert super_tui._run_tui_direct_validation_gate_if_applicable(args, decision) is None


def test_super_tui_direct_validation_gate_skips_repair_loop_policy(tmp_path) -> None:
    decision = super_tui.TuiIntentDecision(
        permission="write",
        complexity="complex",
        confidence=0.9,
        rationale="mixed performance repair route",
        communication_policy=super_tui.AgentCommunicationPolicy(
            answer_budget="normal",
            latency_preference="balanced",
            progress_detail="compact",
            interaction_style="act_then_report",
        ),
        execution_policy=super_tui.AgentExecutionPolicy(
            autonomy_mode="guided",
            stop_condition="validation_passes",
            max_work_seconds=300,
            max_auto_fix_rounds=1,
            max_validation_cycles=2,
            allow_repair_cycles=True,
        ),
        surface_policy=super_tui.normalize_agent_surface_policy(
            {
                "controller_lane": "execute",
                "permission_scope": "transient_execute",
                "phase_shape": "validation_gate",
                "evidence_policy": {
                    "freshness": "fresh",
                    "scope": "targeted",
                    "sources": ["run_state", "validation", "workspace"],
                },
            }
        ),
    )
    args = Namespace(workspace=str(tmp_path), plan_only=False)

    assert super_tui._run_tui_direct_validation_gate_if_applicable(args, decision) is None


def test_super_tui_dispatch_checks_validation_before_read_only(monkeypatch, tmp_path) -> None:
    surface_policy = super_tui.normalize_agent_surface_policy(
        {
            "controller_lane": "execute",
            "permission_scope": "transient_execute",
            "evidence_policy": {"freshness": "fresh", "scope": "targeted", "sources": ["validation"]},
            "phase_shape": "validation_gate",
            "latency_policy": {"class": "fast", "max_work_seconds": 30},
        }
    )
    execution_policy = super_tui.normalize_agent_execution_policy(
        surface_policy.to_payload(),
        lane="executor_write",
    )
    decision = super_tui.TuiIntentDecision(
        permission="read-only",
        complexity="simple",
        confidence=0.87,
        rationale="conflicting compatibility projection",
        execution_policy=execution_policy,
        surface_policy=surface_policy,
    )
    args = Namespace(
        workspace=str(tmp_path),
        target="can you make sure it successfully compiles?",
        plain=True,
        raw_events=False,
        plan_only=False,
        _tui_transcript_workspace=str(tmp_path),
        _tui_selected_skill_mentions=[],
        _model_explicit=False,
    )
    called: list[bool] = []

    monkeypatch.setattr(super_tui, "_route_tui_intent_with_model", lambda args: (decision, True))
    monkeypatch.setattr(
        super_tui,
        "_run_tui_direct_validation_gate_if_applicable",
        lambda args, routed: called.append(True) or 0,
    )
    monkeypatch.setattr(
        super_tui,
        "_run_tui_read_only",
        lambda *args, **kwargs: pytest.fail("read-only fallback should not preempt validation"),
    )

    exit_code = super_tui._dispatch_tui_turn(args, super_tui.build_parser())

    assert exit_code == 0
    assert called == [True]


def test_super_tui_async_surface_turn_carries_communication_policy(tmp_path) -> None:
    args = Namespace(_tui_selected_skill_mentions=["skill-a"], _tui_communication_policy={
        "answer_budget": "brief",
        "latency_preference": "fast",
        "progress_detail": "quiet",
        "interaction_style": "answer_only",
    }, _tui_execution_policy={
        "autonomy_mode": "continuous",
        "stop_condition": "validation_passes",
        "max_auto_fix_rounds": 4,
    }, _tui_surface_policy={
        "controller_lane": "execute",
        "permission_scope": "workspace_write",
        "phase_shape": "repair_loop",
        "autonomy": "continuous",
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
    assert turn.metadata["execution_policy"]["autonomy_mode"] == "continuous"
    assert turn.metadata["surface_context"]["execution_policy"]["max_auto_fix_rounds"] == 4
    assert turn.metadata["surface_policy"]["controller_lane"] == "execute"
    assert turn.metadata["surface_context"]["surface_policy"]["phase_shape"] == "repair_loop"
    assert turn.metadata["surface_context"]["notes_feature"]["kind"] == "hugo_notes"
    assert "hugo_notes" in turn.capabilities


def test_super_tui_async_admission_payload_carries_communication_policy(tmp_path) -> None:
    args = Namespace(
        _tui_selected_skill_mentions=[],
        _tui_communication_policy={
            "answer_budget": "detailed",
            "latency_preference": "deep",
            "progress_detail": "verbose",
            "interaction_style": "review",
        },
        _tui_execution_policy={
            "autonomy_mode": "continuous",
            "stop_condition": "validation_passes",
            "max_auto_fix_rounds": 5,
        },
        _tui_surface_policy={
            "controller_lane": "execute",
            "permission_scope": "workspace_write",
            "phase_shape": "repair_loop",
            "autonomy": "continuous",
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
    assert payload["execute"]["metadata"]["execution_policy"]["max_auto_fix_rounds"] == 5
    assert payload["chat_request"]["surface_context"]["execution_policy"]["autonomy_mode"] == "continuous"
    assert payload["execute"]["metadata"]["surface_policy"]["controller_lane"] == "execute"
    assert payload["chat_request"]["surface_context"]["surface_policy"]["phase_shape"] == "repair_loop"


def test_super_tui_execution_overrides_carry_communication_policy() -> None:
    args = Namespace(
        _tui_selected_skill_mentions=[],
        _tui_communication_policy={
            "answer_budget": "normal",
            "latency_preference": "balanced",
            "progress_detail": "compact",
            "interaction_style": "act_then_report",
        },
        _tui_execution_policy={
            "autonomy_mode": "guided",
            "stop_condition": "objective_satisfied",
            "max_auto_fix_rounds": 2,
        },
        _tui_surface_policy={
            "controller_lane": "inspect",
            "permission_scope": "read_only",
            "phase_shape": "one_pass",
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
    assert overrides["metadata"]["execution_policy"]["max_auto_fix_rounds"] == 2
    assert overrides["metadata"]["surface_policy"]["controller_lane"] == "inspect"


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
    surface_policy = super_tui.AgentSurfacePolicy(
        controller_lane="narrate_run",
        permission_scope="none",
        phase_shape="snapshot",
    )

    super_tui._append_tui_async_admission_transcript(
        tmp_path,
        result,
        text="show status",
        communication_policy=policy,
        surface_policy=surface_policy,
    )

    entries = super_tui._read_tui_transcript(tmp_path, limit=5)
    assert entries[-1].metadata["communication_policy"]["answer_budget"] == "brief"
    assert entries[-1].metadata["communication_policy"]["progress_detail"] == "quiet"
    assert entries[-1].metadata["surface_policy"]["controller_lane"] == "narrate_run"


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


def test_super_tui_surface_policy_normalizes_structured_capability_packs() -> None:
    policy = super_tui.normalize_agent_surface_policy(
        {
            "surface_policy": {
                "controller_lane": "execute",
                "permission_scope": "workspace_write",
                "capability_packs": ["browser", "desktop_control", "browser"],
            }
        }
    )

    assert policy.capability_packs == ("browser_control", "desktop_control")
    assert policy.to_payload()["capability_packs"] == ["browser_control", "desktop_control"]


def test_super_tui_read_only_tool_ids_expand_observation_tools_from_capability_packs(monkeypatch, tmp_path) -> None:
    available = {
        "list_directory": {},
        "file_read": {},
        "workspace_check": {},
        "browser_tabs": {},
        "browser_inspect": {},
        "browser_wait": {},
        "browser_extract": {},
        "browser_screenshot": {},
        "browser_click": {},
        "desktop_observe": {},
        "desktop_click": {},
    }
    monkeypatch.setattr(super_tui, "available_local_organism_tools", lambda: available)
    policy = super_tui.AgentSurfacePolicy(capability_packs=("computer_control",))

    tool_ids = super_tui._tui_read_only_tool_ids(tmp_path, policy)

    assert "browser_inspect" in tool_ids
    assert "browser_screenshot" in tool_ids
    assert "desktop_observe" in tool_ids
    assert "browser_click" not in tool_ids
    assert "desktop_click" not in tool_ids


def test_super_tui_read_only_tool_ids_expand_browser_actions_by_permission(monkeypatch, tmp_path) -> None:
    available = {
        "list_directory": {},
        "file_read": {},
        "workspace_check": {},
        "browser_open": {},
        "browser_click": {},
        "browser_fill": {},
        "browser_type": {},
        "browser_select": {},
        "browser_download": {},
        "browser_inspect": {},
    }
    monkeypatch.setattr(super_tui, "available_local_organism_tools", lambda: available)
    transient = super_tui.AgentSurfacePolicy(
        permission_scope="transient_execute",
        capability_packs=("browser_control",),
    )
    external_write = super_tui.AgentSurfacePolicy(
        permission_scope="external_write",
        capability_packs=("browser_control",),
    )

    transient_ids = super_tui._tui_read_only_tool_ids(tmp_path, transient)
    external_write_ids = super_tui._tui_read_only_tool_ids(tmp_path, external_write)

    assert "browser_open" in transient_ids
    assert "browser_click" in transient_ids
    assert "browser_fill" in transient_ids
    assert "browser_download" not in transient_ids
    assert "browser_open" in external_write_ids
    assert "browser_click" in external_write_ids
    assert "browser_fill" in external_write_ids


def test_super_tui_external_browser_probe_omits_workspace_tools(monkeypatch, tmp_path) -> None:
    available = {
        "list_directory": {},
        "file_read": {},
        "workspace_check": {},
        "web_search": {},
        "browser_tabs": {},
        "browser_inspect": {},
        "browser_open": {},
        "browser_extract": {},
        "browser_screenshot": {},
    }
    monkeypatch.setattr(super_tui, "available_local_organism_tools", lambda: available)
    policy = super_tui.normalize_agent_surface_policy(
        {
            "controller_lane": "inspect",
            "permission_scope": "transient_execute",
            "phase_shape": "probe",
            "capability_packs": ["browser_control"],
            "evidence_policy": {
                "sources": ["conversation", "external"],
                "freshness": "fresh",
                "scope": "targeted",
            },
        }
    )

    tool_ids = super_tui._tui_read_only_tool_ids(tmp_path, policy)

    assert "browser_open" in tool_ids
    assert "browser_extract" in tool_ids
    assert "browser_inspect" in tool_ids
    assert "file_read" not in tool_ids
    assert "list_directory" not in tool_ids
    assert "workspace_check" not in tool_ids
    assert "web_search" not in tool_ids


def test_super_tui_workspace_browser_probe_keeps_workspace_tools(monkeypatch, tmp_path) -> None:
    available = {
        "list_directory": {},
        "file_read": {},
        "workspace_check": {},
        "browser_open": {},
        "browser_extract": {},
    }
    monkeypatch.setattr(super_tui, "available_local_organism_tools", lambda: available)
    policy = super_tui.normalize_agent_surface_policy(
        {
            "controller_lane": "inspect",
            "permission_scope": "transient_execute",
            "phase_shape": "probe",
            "capability_packs": ["browser_control"],
            "evidence_policy": {
                "sources": ["workspace", "external"],
                "freshness": "fresh",
                "scope": "targeted",
            },
        }
    )

    tool_ids = super_tui._tui_read_only_tool_ids(tmp_path, policy)

    assert "browser_open" in tool_ids
    assert "file_read" in tool_ids
    assert "list_directory" in tool_ids


def test_super_tui_read_only_prompts_disclose_optional_computer_capabilities() -> None:
    prompt = super_tui._tui_read_only_system_prompt(["list_directory"])

    assert "browser_control" in prompt
    assert "desktop_control" in prompt
    assert "computer_control" in prompt
    assert "DAN_COMPUTER_CONTROL=1" in prompt
    assert "Do not deny browser or desktop capability" in prompt


def test_super_tui_browser_prompt_requires_recovery_after_empty_dynamic_pages() -> None:
    prompt = super_tui._tui_read_only_system_prompt(["browser_open", "browser_extract", "browser_wait", "browser_screenshot"])

    assert "empty page text or empty element inspection is not a final result" in prompt
    assert "recover with browser_wait" in prompt
    assert "Do not answer by saying you will wait" in prompt
    assert "CAPTCHA" in prompt
    assert "report that observed blocker" in prompt
    assert "non-headless session" in prompt


def test_super_tui_browser_tool_evidence_mentions_gui_preview() -> None:
    evidence = super_tui._read_only_tool_evidence_text(
        [
            {
                "event": "tool.completed",
                "tool_id": "browser_open",
                "result": {
                    "status": "ok",
                    "url": "https://example.com",
                    "title": "Example",
                    "session": {
                        "headless": False,
                        "profile": "demo",
                        "gui_preview_available": True,
                        "preview": "local GUI browser window",
                    },
                },
            }
        ]
    )

    assert "local GUI browser window" in evidence
    assert "profile=demo" in evidence
    assert "https://example.com" in evidence


def test_super_tui_playwright_session_info_exposes_non_headless_preview(monkeypatch) -> None:
    import dan.tools.browser_control as browser_control

    monkeypatch.setattr(browser_control, "is_playwright_available", lambda: True)
    controller = browser_control.PlaywrightBrowserController(headless=False, profile="visible-demo")

    info = controller.session_info()

    assert info["headless"] is False
    assert info["gui_preview_available"] is True
    assert info["preview"] == "local GUI browser window"
    assert info["profile"] == "visible-demo"


def test_super_tui_playwright_launch_options_fallback_to_system_browser(
    monkeypatch,
    tmp_path,
) -> None:
    import dan.tools.browser_control as browser_control

    system_browser = tmp_path / "Google Chrome"
    system_browser.write_text("#!/bin/sh\n", encoding="utf-8")
    managed_browser = tmp_path / "missing-playwright-chromium"

    class FakeChromium:
        executable_path = str(managed_browser)

    monkeypatch.delenv("DAN_BROWSER_EXECUTABLE", raising=False)
    monkeypatch.setattr(browser_control, "_system_browser_candidates", lambda: [str(system_browser)])

    options = browser_control._browser_launch_options(FakeChromium(), headless=True)

    assert options["headless"] is True
    assert options["executable_path"] == str(system_browser)


def test_super_tui_execution_policy_normalizes_caps_without_free_text_routing() -> None:
    policy = super_tui.normalize_agent_execution_policy(
        {
            "execution_policy": {
                "autonomy_mode": "loop_until_condition",
                "stop_condition": "tests_pass",
                "max_work_seconds": 99999,
                "max_auto_fix_rounds": 99,
                "max_validation_cycles": 99,
            }
        },
        lane="executor_write",
    )

    assert policy.autonomy_mode == "continuous"
    assert policy.stop_condition == "validation_passes"
    assert policy.max_work_seconds == 3600
    assert policy.max_auto_fix_rounds == 8
    assert policy.max_validation_cycles == 12
    assert super_tui.classify_agent_turn_intent("keep repairing until tests pass").needs_clarification is True


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
        raise AssertionError("read-only request must not start Diane runner")

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
        raise AssertionError("read-only summary must not start Diane runner")

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


def test_super_tui_read_only_model_failure_falls_back_to_workspace_summary(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "opec-report-2025.md").write_text(
        "# OPEC Report 2025\n\n## Findings\nVerified production summary.\n",
        encoding="utf-8",
    )

    def fail_run(*args, **kwargs):
        raise AssertionError("read-only fallback must not start Diane runner")

    _patch_tui_route(monkeypatch, permission="read-only", complexity="complex", routed_with_model=True)
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_run)
    monkeypatch.setattr(super_tui, "_run_tui_read_only_model_answer", lambda *args, **kwargs: False)

    exit_code = super_tui.main(
        [
            "help me review the report?",
            "--workspace",
            str(workspace),
            "--plain",
        ]
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Source: opec-report-2025.md" in stdout
    assert "Headings: # OPEC Report 2025; ## Findings" in stdout
    assert "I could not get a usable model-written answer" not in stdout


def test_super_tui_read_only_followup_uses_prior_takeaway_context(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "opec-uae-combined-report-2026.md").write_text(
        "\n".join(
            [
                "# OPEC & UAE Membership Report - 2026 Combined Status",
                "",
                "**Report date:** 2026-08-11",
                "**Scope:** OPEC+ production policy, UAE membership status, and market outlook.",
                "**Sources:** OPEC press releases, Reuters, and market data.",
                "",
                "> Consolidation Note: This report supersedes prior OPEC/UAE briefing files.",
                "",
                "The central point is that UAE remains inside OPEC while tensions around quota targets persist.",
                "Near-term market impact depends on whether OPEC+ keeps discipline around planned production changes.",
                "",
                "## Membership Status",
                "The UAE is still a member.",
                "## Market Outlook",
                "Supply discipline remains the main uncertainty.",
            ]
        ),
        encoding="utf-8",
    )
    super_tui._append_tui_transcript_entry(
        workspace,
        role="user",
        text="can you review that report for me for some takeaways",
    )

    def fail_run(*args, **kwargs):
        raise AssertionError("read-only follow-up must not start Diane runner")

    _patch_tui_route(monkeypatch, permission="read-only", complexity="simple")
    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_run)

    exit_code = super_tui.main(
        [
            "I mean the open report",
            "--workspace",
            str(workspace),
            "--plain",
        ]
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Source: opec-uae-combined-report-2026.md" in stdout
    assert "Scope: OPEC+ production policy" in stdout
    assert "Takeaways:" in stdout
    assert "UAE remains inside OPEC" in stdout
    assert "No matching workspace file" not in stdout


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
        raise AssertionError("read-only search must not start Diane runner")

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
        raise AssertionError("read-only live lane must not start Diane runner")

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


def test_super_tui_browser_probe_uses_browser_tool_loop_not_workspace_sources(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    from dan.worker.organisms import local_runtime

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "local-note.md").write_text("Local workspace text that must not be previewed.\n", encoding="utf-8")
    tool_calls_seen: list[tuple[str, dict]] = []

    async def fake_browser_open(*, url: str, **_kwargs: object) -> dict:
        tool_calls_seen.append(("browser_open", {"url": url}))
        return {"status": "ok", "url": url, "title": "Browser Demo"}

    async def fake_browser_extract(**_kwargs: object) -> dict:
        tool_calls_seen.append(("browser_extract", {}))
        return {"length": 35, "text": "Browser demo page text after action."}

    metadata = {
        "browser_open": (
            fake_browser_open,
            {
                "tool_id": "browser_open",
                "description": "Open a URL in a persistent browser session.",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            },
        ),
        "browser_extract": (
            fake_browser_extract,
            {
                "tool_id": "browser_extract",
                "description": "Extract visible page text.",
                "parameters": {"type": "object", "properties": {}},
            },
        ),
        "file_read": (
            lambda **_kwargs: pytest.fail("browser probe must not read workspace files"),
            {
                "tool_id": "file_read",
                "description": "Read a workspace file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        ),
    }

    class FakeProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.tool_names_by_call: list[list[str]] = []

        async def complete(self, **kwargs):
            self.calls += 1
            self.tool_names_by_call.append(
                [
                    row.get("function", {}).get("name")
                    for row in kwargs.get("tools", [])
                    if isinstance(row, dict)
                ]
            )
            if self.calls == 1:
                return CompletionResult(
                    text="",
                    model=kwargs.get("model"),
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "id": "call-open",
                            "type": "function",
                            "function": {
                                "name": "browser_open",
                                "arguments": '{"url":"data:text/html,<title>Browser Demo</title><button>ok</button>"}',
                            },
                        }
                    ],
                    raw_assistant_message={
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-open",
                                "type": "function",
                                "function": {
                                    "name": "browser_open",
                                    "arguments": '{"url":"data:text/html,<title>Browser Demo</title><button>ok</button>"}',
                                },
                            }
                        ],
                    },
                )
            if self.calls == 2:
                return CompletionResult(
                    text="",
                    model=kwargs.get("model"),
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "id": "call-extract",
                            "type": "function",
                            "function": {"name": "browser_extract", "arguments": "{}"},
                        }
                    ],
                    raw_assistant_message={
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-extract",
                                "type": "function",
                                "function": {"name": "browser_extract", "arguments": "{}"},
                            }
                        ],
                    },
                )
            return CompletionResult(
                text="Browser demo ready: Browser demo page text after action.",
                model=kwargs.get("model"),
                finish_reason="stop",
            )

    provider = FakeProvider()
    surface_policy = super_tui.normalize_agent_surface_policy(
        {
            "controller_lane": "inspect",
            "permission_scope": "transient_execute",
            "phase_shape": "probe",
            "capability_packs": ["browser_control"],
            "evidence_policy": {
                "sources": ["conversation", "external"],
                "freshness": "fresh",
                "scope": "targeted",
            },
        }
    )
    decision = super_tui.TuiIntentDecision(
        permission="read-only",
        complexity="simple",
        confidence=0.9,
        rationale="browser probe",
        surface_policy=surface_policy,
    )

    monkeypatch.setattr(super_tui, "_route_tui_intent_with_model", lambda args: (decision, True))
    monkeypatch.setattr(
        super_tui,
        "available_local_organism_tools",
        lambda: {tool_id: tool_metadata for tool_id, (_fn, tool_metadata) in metadata.items()},
    )
    monkeypatch.setattr(local_runtime, "get_all_tools", lambda: metadata)
    monkeypatch.setattr(super_tui, "_build_tui_read_only_live_provider", lambda args, model: provider)
    monkeypatch.setattr(super_tui, "_run_tui_turn", lambda *args, **kwargs: pytest.fail("must stay in read-only tool loop"))

    exit_code = super_tui.main(
        [
            "ok, can you show me browser manipulation",
            "--workspace",
            str(workspace),
            "--model",
            "fake-model",
            "--live",
            "--plain",
        ]
    )

    assert exit_code == 0
    assert tool_calls_seen == [
        ("browser_open", {"url": "data:text/html,<title>Browser Demo</title><button>ok</button>"}),
        ("browser_extract", {}),
    ]
    assert "file_read" not in provider.tool_names_by_call[0]
    stdout = capsys.readouterr().out
    assert "Browser demo ready" in stdout
    assert "Local workspace text" not in stdout
    assert "Source: local-note.md" not in stdout


def test_super_tui_browser_probe_recovers_empty_dynamic_page_before_answer(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    from dan.worker.organisms import local_runtime

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool_calls_seen: list[str] = []

    async def fake_browser_open(*, url: str, **_kwargs: object) -> dict:
        tool_calls_seen.append("browser_open")
        return {"status": "ok", "url": url, "title": "Reuters"}

    async def fake_browser_extract(**_kwargs: object) -> dict:
        tool_calls_seen.append("browser_extract")
        return {"length": 0, "text": ""}

    async def fake_browser_wait(**_kwargs: object) -> dict:
        tool_calls_seen.append("browser_wait")
        return {"status": "ok", "selector": None}

    async def fake_browser_inspect(**_kwargs: object) -> dict:
        tool_calls_seen.append("browser_inspect")
        return {
            "url": "https://www.reuters.com",
            "title": "reuters.com",
            "html": "<iframe src='captcha-delivery.com'></iframe><p>DataDome CAPTCHA</p>",
            "html_length": 68,
            "elements": [],
            "element_count": 0,
        }

    async def fake_browser_screenshot(**_kwargs: object) -> dict:
        tool_calls_seen.append("browser_screenshot")
        return {"path": "/tmp/reuters-captcha.png"}

    def metadata(tool_id: str, properties: dict | None = None) -> dict:
        return {
            "tool_id": tool_id,
            "description": tool_id,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": [],
            },
        }

    tools = {
        "browser_open": (
            fake_browser_open,
            metadata("browser_open", {"url": {"type": "string"}}) | {"parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
        ),
        "browser_extract": (fake_browser_extract, metadata("browser_extract")),
        "browser_wait": (fake_browser_wait, metadata("browser_wait")),
        "browser_inspect": (fake_browser_inspect, metadata("browser_inspect")),
        "browser_screenshot": (fake_browser_screenshot, metadata("browser_screenshot")),
    }

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
                            "id": "call-open",
                            "type": "function",
                            "function": {"name": "browser_open", "arguments": '{"url":"https://www.reuters.com"}'},
                        }
                    ],
                    raw_assistant_message={
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-open",
                                "type": "function",
                                "function": {"name": "browser_open", "arguments": '{"url":"https://www.reuters.com"}'},
                            }
                        ],
                    },
                )
            if self.calls == 2:
                return CompletionResult(
                    text="",
                    model=kwargs.get("model"),
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "id": "call-extract",
                            "type": "function",
                            "function": {"name": "browser_extract", "arguments": "{}"},
                        }
                    ],
                    raw_assistant_message={
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-extract",
                                "type": "function",
                                "function": {"name": "browser_extract", "arguments": "{}"},
                            }
                        ],
                    },
                )
            if self.calls == 3:
                return CompletionResult(
                    text="I will wait and inspect next.",
                    model=kwargs.get("model"),
                    finish_reason="stop",
                )
            if self.calls >= 4:
                user_prompts = [
                    str(message.get("content") or "")
                    for message in kwargs.get("messages", [])
                    if str(message.get("role") or "").strip() == "user"
                ]
                assert any("DataDome CAPTCHA" in text for text in user_prompts)
                return CompletionResult(
                    text="Reuters is blocked by a DataDome CAPTCHA in the browser; no headlines were visible.",
                    model=kwargs.get("model"),
                    finish_reason="stop",
                )
            return CompletionResult(
                text="I will inspect in a second.",
                model=kwargs.get("model"),
                finish_reason="stop",
            )

    surface_policy = super_tui.normalize_agent_surface_policy(
        {
            "controller_lane": "inspect",
            "permission_scope": "transient_execute",
            "phase_shape": "probe",
            "capability_packs": ["browser_control"],
            "evidence_policy": {"sources": ["external"], "freshness": "fresh", "scope": "targeted"},
        }
    )
    decision = super_tui.TuiIntentDecision(
        permission="read-only",
        complexity="simple",
        confidence=0.9,
        rationale="browser probe",
        surface_policy=surface_policy,
    )

    monkeypatch.setattr(super_tui, "_route_tui_intent_with_model", lambda args: (decision, True))
    monkeypatch.setattr(super_tui, "available_local_organism_tools", lambda: {tool_id: data[1] for tool_id, data in tools.items()})
    monkeypatch.setattr(local_runtime, "get_all_tools", lambda: tools)
    monkeypatch.setattr(super_tui, "_build_tui_read_only_live_provider", lambda args, model: FakeProvider())
    monkeypatch.setattr(super_tui, "_run_tui_turn", lambda *args, **kwargs: pytest.fail("must stay in read-only tool loop"))

    exit_code = super_tui.main(
        [
            "fetch Reuters headlines",
            "--workspace",
            str(workspace),
            "--model",
            "fake-model",
            "--live",
            "--plain",
        ]
    )

    assert exit_code == 0
    assert tool_calls_seen == [
        "browser_open",
        "browser_extract",
        "browser_wait",
        "browser_inspect",
        "browser_screenshot",
    ]
    stdout = capsys.readouterr().out
    assert "DataDome CAPTCHA" in stdout
    assert "I will wait and inspect next" not in stdout


def test_super_tui_browser_probe_does_not_fallback_to_workspace_preview(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "local-note.md").write_text("Local workspace text that must not be previewed.\n", encoding="utf-8")
    surface_policy = super_tui.normalize_agent_surface_policy(
        {
            "controller_lane": "inspect",
            "permission_scope": "transient_execute",
            "phase_shape": "probe",
            "capability_packs": ["browser_control"],
            "evidence_policy": {
                "sources": ["conversation", "external"],
                "freshness": "fresh",
                "scope": "targeted",
            },
        }
    )
    decision = super_tui.TuiIntentDecision(
        permission="read-only",
        complexity="complex",
        confidence=0.9,
        rationale="browser probe",
        surface_policy=surface_policy,
    )

    monkeypatch.setattr(super_tui, "_route_tui_intent_with_model", lambda args: (decision, True))
    monkeypatch.setattr(super_tui, "_run_tui_read_only_model_answer", lambda *args, **kwargs: False)
    monkeypatch.setattr(super_tui, "_run_tui_turn", lambda *args, **kwargs: pytest.fail("must stay in read-only lane"))

    exit_code = super_tui.main(
        [
            "ok, can you show me browser manipulation",
            "--workspace",
            str(workspace),
            "--model",
            "fake-model",
            "--live",
            "--plain",
        ]
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "could not complete the browser/desktop tool probe" in stdout
    assert "No workspace files" in stdout
    assert "were inspected as a substitute" in stdout
    assert "Local workspace text" not in stdout
    assert "Source: local-note.md" not in stdout


@pytest.mark.skipif(
    os.environ.get("DAN_RUN_BROWSER_LIVE_TESTS") != "1",
    reason="set DAN_RUN_BROWSER_LIVE_TESTS=1 to run local Playwright browser manipulation smoke",
)
@pytest.mark.asyncio
async def test_super_tui_live_browser_manipulation_smoke() -> None:
    from urllib.parse import quote

    from dan.tools.browser_control import PlaywrightBrowserController

    html = """
    <html>
      <head><title>Diane Browser Smoke</title></head>
      <body>
        <input id="name" oninput="document.querySelector('#status').textContent = 'typed ' + this.value" />
        <button id="go" onclick="document.querySelector('#status').textContent += ' clicked'">Go</button>
        <p id="status">ready</p>
      </body>
    </html>
    """
    controller = PlaywrightBrowserController(headless=True, profile=None)
    try:
        opened = await controller.open("data:text/html," + quote(html))
        assert opened["status"] == "ok"
        assert opened["title"] == "Diane Browser Smoke"

        await controller.fill("#name", "Ada")
        await controller.click("#go")
        assert "typed Ada clicked" in await controller.extract_text()

        inspected = await controller.inspect_dom(include_elements=True)
        selectors = {row.get("selector") for row in inspected["elements"]}
        assert "#name" in selectors
        assert "#go" in selectors
    finally:
        await controller.close()


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
        raise AssertionError("read-only live lane must not start Diane runner")

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
    assert "Diane · Session:\nStatus:" not in stdout
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
        raise AssertionError("simple write must not start Diane runner")

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
        raise AssertionError("blocked simple write must not start Diane runner")

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
        clarification="Should this be a read-only answer, or should Diane change the workspace?",
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


def test_super_tui_interactive_prompt_toolkit_uses_chatbox_not_startup_panel(capsys, monkeypatch) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", "/tmp/ws", "--plain"])
    super_tui._prepare_args(args, [])

    def fake_prompt(prompt, *, commands, skills, paths=(), workspace_root=None):
        del commands, skills, paths, workspace_root
        print(prompt, end="")
        raise EOFError

    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace: [])
    monkeypatch.setattr(super_tui, "_load_tui_path_suggestions", lambda workspace: [])
    monkeypatch.setattr(super_tui, "_prompt_toolkit_chatbox_available", lambda: True)
    monkeypatch.setattr(super_tui, "_read_interactive_line", fake_prompt)

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Message" not in stdout
    assert "Type in the composer below" not in stdout
    assert "super-tui>" in stdout


def test_super_organism_parser_identity_is_unchanged() -> None:
    parser = super_organism.build_parser()

    assert parser.prog == "dan-super-organism"
    assert _SUBCOMMANDS["super-organism"] == ("dan.cli.super_organism", "main")
