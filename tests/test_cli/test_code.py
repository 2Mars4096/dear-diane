from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dan.cli import normalize_workspace_root
from dan.cli.code import (
    CodeHeartbeatMonitor,
    CodeProgressRenderer,
    _filter_diff_excluding_paths,
    _load_swebench_instance,
    _swebench_prediction_excluded_paths,
    _conversation_context,
    _effective_coding_organism_max_repair_rounds,
    _effective_supervision_loop_count,
    _normalize_structured_workspace_check_acceptance_criteria,
    _normalize_structured_workspace_check_objective,
    _provider_request_overrides_for_thinking_mode,
    _project_planner_context,
    _load_or_create_session,
    _print_session_rollup,
    build_parser,
    main,
    run_coding_organism_live,
)
from dan.cli.code_product import (
    CODE_PRODUCT_RUNTIME_BUILD_ID,
    CODE_PRODUCT_SESSION_FORMAT_VERSION,
    CodingCliSession,
    CodingOrganismReport,
    load_code_product_session,
    resolve_code_product_paths,
    save_code_product_session,
)
from dan.worker.organism_log import ORGANISM_LOG_SCHEMA_VERSION
from dan.worker.organisms.coding_conversation import CodingConversationReportSummary
from dan.worker.organisms import (
    CodingConversationReviewDecision,
    CodingConversationTurnDecision,
    CodingProjectMilestone,
    CodingProjectPlannerDecision,
)


@pytest.fixture(autouse=True)
def _clear_code_env(monkeypatch) -> None:
    monkeypatch.setenv("DAN_CODE_THINKING_MODE", "")
    monkeypatch.setenv("DAN_CODE_COMPLETION_TIMEOUT_SECONDS", "")


def test_build_parser_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args([])

    assert args.objective is None
    assert args.task_id == "coding-organ-task"
    assert args.organ_id == "coding-build"
    assert args.organism_id == "coding-organism"
    assert args.model is None
    assert args.max_tool_rounds is None
    assert args.max_tool_calls is None
    assert args.completion_timeout_seconds is None
    assert args.approval_mode is None
    assert args.thinking_mode is None
    assert args.quiet_progress is False
    assert args.show_model_trace is False
    assert args.init is False
    assert args.json is False
    assert args.swebench_instance_file is None
    assert args.swebench_predictions_path is None


def test_provider_request_overrides_disable_thinking_for_short_auto_timeout() -> None:
    assert _provider_request_overrides_for_thinking_mode(
        "auto",
        completion_timeout_seconds=45.0,
    ) == {"thinking": {"type": "disabled"}, "timeout": 45.0}


def test_provider_request_overrides_preserve_explicit_thinking_mode() -> None:
    assert _provider_request_overrides_for_thinking_mode(
        "enabled",
        completion_timeout_seconds=45.0,
    ) == {"thinking": {"type": "enabled"}, "timeout": 45.0}


def test_provider_request_overrides_keep_auto_for_long_timeout() -> None:
    assert _provider_request_overrides_for_thinking_mode(
        "auto",
        completion_timeout_seconds=90.0,
    ) == {"timeout": 90.0}


def test_short_completion_timeout_caps_live_repair_rounds() -> None:
    assert (
        _effective_coding_organism_max_repair_rounds(
            1,
            benchmark_context=None,
            completion_timeout_seconds=60.0,
        )
        == 0
    )
    assert (
        _effective_coding_organism_max_repair_rounds(
            1,
            benchmark_context={"benchmark_name": "SWE-bench"},
            completion_timeout_seconds=45.0,
        )
        == 0
    )
    assert (
        _effective_coding_organism_max_repair_rounds(
            1,
            benchmark_context={"benchmark_name": "SWE-bench"},
            completion_timeout_seconds=None,
        )
        == 2
    )


def test_short_completion_timeout_caps_outer_supervision_loop() -> None:
    assert (
        _effective_supervision_loop_count(
            benchmark_context=None,
            completion_timeout_seconds=45.0,
        )
        == 1
    )
    assert (
        _effective_supervision_loop_count(
            benchmark_context={"benchmark_name": "SWE-bench"},
            completion_timeout_seconds=60.0,
        )
        == 1
    )
    assert (
        _effective_supervision_loop_count(
            benchmark_context=None,
            completion_timeout_seconds=None,
        )
        == 2
    )
    assert (
        _effective_supervision_loop_count(
            benchmark_context={"benchmark_name": "SWE-bench"},
            completion_timeout_seconds=None,
        )
        == 4
    )


def test_main_lists_local_tools(capsys) -> None:
    exit_code = main(["--list-tools"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "file_read [file]" in stdout
    assert "shell_command [system]" in stdout


def test_load_swebench_instance_from_json_file(tmp_path) -> None:
    instance_path = tmp_path / "instance.json"
    instance_path.write_text(
        json.dumps(
            {
                "instance_id": "sympy__sympy-20590",
                "repo": "sympy/sympy",
                "base_commit": "abc123",
                "problem_statement": "Fix sympify to catch AttributeError.",
                "requirements": "Keep the patch minimal.",
                "selected_test_files_to_run": ["sympy/core/tests/test_sympify.py"],
            }
        ),
        encoding="utf-8",
    )

    instance = _load_swebench_instance(instance_path, base_dir=tmp_path)

    assert instance.instance_id == "sympy__sympy-20590"
    assert instance.repo == "sympy/sympy"
    assert instance.selected_test_files_to_run == [
        "sympy/core/tests/test_sympify.py"
    ]


def test_swebench_prediction_patch_excludes_instance_test_files(tmp_path) -> None:
    instance_path = tmp_path / "instance.json"
    instance_path.write_text(
        json.dumps(
            {
            "instance_id": "astropy__astropy-12907",
            "repo": "astropy/astropy",
            "base_commit": "abc123",
            "problem_statement": "Fix nested separability.",
            "selected_test_files_to_run": ["astropy/modeling/tests/test_separable.py"],
            "fail_to_pass": [
                "astropy/modeling/tests/test_separable.py::test_separable[compound_model6-result6]"
            ],
            "pass_to_pass": [
                "astropy/modeling/tests/test_separable.py::test_separable[compound_model1-result1]"
            ],
            }
        ),
        encoding="utf-8",
    )
    instance = _load_swebench_instance(instance_path, base_dir=tmp_path)
    patch_text = (
        "diff --git a/astropy/modeling/separable.py b/astropy/modeling/separable.py\n"
        "--- a/astropy/modeling/separable.py\n"
        "+++ b/astropy/modeling/separable.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/astropy/modeling/tests/test_separable.py b/astropy/modeling/tests/test_separable.py\n"
        "--- a/astropy/modeling/tests/test_separable.py\n"
        "+++ b/astropy/modeling/tests/test_separable.py\n"
        "@@ -1 +1 @@\n"
        "-old test\n"
        "+new test\n"
    )

    filtered = _filter_diff_excluding_paths(
        patch_text,
        _swebench_prediction_excluded_paths(instance),
    )

    assert "diff --git a/astropy/modeling/separable.py" in filtered
    assert "diff --git a/astropy/modeling/tests/test_separable.py" not in filtered


def test_main_json_uses_coding_organism_runner(tmp_path, capsys, monkeypatch) -> None:
    async def _fake_runner(*_args, **kwargs):
        assert kwargs["objective"] == "Create the first bounded implementation slice."
        assert kwargs["model"] == "gpt-test"
        assert kwargs["organism_id"] == "coding-organism"
        assert kwargs["organ_id"] == "coding-build"
        assert kwargs["tool_ids"] == [
            "list_directory",
            "file_read",
            "workspace_check",
            "file_edit",
            "file_write",
            "shell_command",
            "web_search",
            "git_status",
            "git_diff",
            "git_log",
        ]
        assert kwargs["max_tool_rounds"] is None
        assert kwargs["max_tool_calls"] == 24
        assert kwargs["thinking_mode"] == "auto"
        assert Path(kwargs["workspace_root"]) == (tmp_path / "workspace").resolve()
        assert kwargs["approval_callback"].mode == "auto"
        assert kwargs["session_context"]["workspace_root"] == str((tmp_path / "workspace").resolve())
        assert kwargs["session_context"]["tool_ids"] == [
            "list_directory",
            "file_read",
            "workspace_check",
            "file_edit",
            "file_write",
            "shell_command",
            "web_search",
            "git_status",
            "git_diff",
            "git_log",
        ]
        assert kwargs["session_context"]["approval_mode"] == "auto"
        assert kwargs["session_context"]["thinking_mode"] == "auto"
        assert kwargs["acceptance_criteria"] == [
            "Return exactly one bounded candidate.",
            "Name the target files explicitly.",
            "Include a focused validation plan.",
            "Keep the change summary and risks inspectable.",
            "Use the milestone objective.",
        ]
        return {
            "status": "completed",
            "trace_id": "trace-code",
            "organism_id": "coding-organism",
            "organ_id": "coding-build",
            "task_id": "coding-organ-task:1",
            "objective": "Create the first bounded implementation slice.",
            "candidate_id": "candidate-1",
            "change_summary": "repair",
            "target_files": ["src/example.py"],
            "test_plan": ["pytest -q"],
            "risks": [],
            "outputs": {"candidate_id": "candidate-1"},
            "handoff_count": 4,
            "signal_count": 8,
            "error": None,
            "trace_rows": [],
        }

    class _FakeReport:
        def __init__(self, payload):
            self._payload = dict(payload)
            self.status = self._payload["status"]
            for key, value in self._payload.items():
                setattr(self, key, value)

        def model_dump(self, mode="json"):
            return dict(self._payload)

        def model_dump_json(self, indent=2):
            return json.dumps(self._payload, indent=indent)

    monkeypatch.setattr("dan.cli.code._build_live_provider", lambda *args, **kwargs: object())

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            CodingConversationTurnDecision(
                action="code",
                public_response="Starting one bounded coding run.",
                coding_objective=user_message,
            ),
            durable_session,
        )

    async def _fake_review(self, *, session, objective, report_summary, context):
        return (
            CodingConversationReviewDecision(
                action="done",
                public_response="This bounded pass is done.",
            ),
            session,
        )

    async def _fake_plan(self, *, session, user_message, requested_objective, requested_acceptance_criteria, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            CodingProjectPlannerDecision(
                public_response="I split this into milestones and I’m starting with the first slice.",
                project_goal=requested_objective,
                plan_summary="One milestone plan for the current test.",
                milestones=[
                    CodingProjectMilestone(
                        milestone_id="m1",
                        title="Current slice",
                        objective="Create the first bounded implementation slice.",
                        acceptance_criteria=["Use the milestone objective."],
                        status="active",
                    )
                ],
                active_milestone_id="m1",
                active_objective="Create the first bounded implementation slice.",
                active_acceptance_criteria=["Use the milestone objective."],
            ),
            durable_session,
        )

    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.decide_user_turn",
        _fake_decide,
    )
    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.review_coding_result",
        _fake_review,
    )
    monkeypatch.setattr(
        "dan.cli.code.CodingProjectPlannerController.plan_project",
        _fake_plan,
    )

    async def _wrapped_runner(*args, **kwargs):
        return _FakeReport(await _fake_runner(*args, **kwargs))

    monkeypatch.setattr("dan.cli.code.run_coding_organism_live", _wrapped_runner)

    exit_code = main(
        [
            "--model",
            "gpt-test",
            "--workspace",
            str(tmp_path / "workspace"),
            "--workdir",
            str(tmp_path / "workdir"),
            "--json",
            "Fix it",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["trace_id"] == "trace-code"
    assert payload["organism_id"] == "coding-organism"
    assert payload["candidate_id"] == "candidate-1"
    session_payload = json.loads((tmp_path / "workspace" / ".dan-code" / "session.json").read_text())
    assert session_payload["workspace_root"] == str((tmp_path / "workspace").resolve())
    assert len(session_payload["turns"]) == 1
    assert (
        session_payload["orchestrator_state"]["project_plan"]["active_milestone_id"]
        == "m1"
    )
    transcript_lines = (tmp_path / "workspace" / ".dan-code" / "transcript.jsonl").read_text().strip().splitlines()
    assert len(transcript_lines) == 1


def test_main_fast_lane_bypasses_project_planner_and_review_calls(
    tmp_path, capsys, monkeypatch
) -> None:
    async def _fake_runner(*_args, **kwargs):
        return {
            "status": "completed",
            "trace_id": "trace-fast-code",
            "organism_id": "coding-organism",
            "organ_id": "coding-build",
            "task_id": "coding-organ-task:1",
            "objective": kwargs["objective"],
            "candidate_id": "candidate-fast",
            "change_summary": "No code changes required; summarized the current issues.",
            "target_files": ["src/example.py"],
            "test_plan": ["pytest -q"],
            "risks": [],
            "outputs": {"candidate_id": "candidate-fast"},
            "handoff_count": 2,
            "signal_count": 4,
            "error": None,
            "trace_rows": [],
        }

    class _FakeReport:
        def __init__(self, payload):
            self._payload = dict(payload)
            self.status = self._payload["status"]
            for key, value in self._payload.items():
                setattr(self, key, value)

        def model_dump(self, mode="json"):
            return dict(self._payload)

        def model_dump_json(self, indent=2):
            return json.dumps(self._payload, indent=indent)

    monkeypatch.setattr("dan.cli.code._build_live_provider", lambda *args, **kwargs: object())

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            CodingConversationTurnDecision(
                action="code",
                public_response="Starting one bounded coding review run.",
                coding_objective=user_message,
            ),
            durable_session,
        )

    async def _unexpected_plan(*_args, **_kwargs):
        raise AssertionError("fast lane should bypass project planner calls")

    async def _unexpected_review(*_args, **_kwargs):
        raise AssertionError("fast lane should bypass review controller calls")

    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.decide_user_turn",
        _fake_decide,
    )
    monkeypatch.setattr(
        "dan.cli.code.CodingProjectPlannerController.plan_project",
        _unexpected_plan,
    )
    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.review_coding_result",
        _unexpected_review,
    )

    async def _wrapped_runner(*args, **kwargs):
        return _FakeReport(await _fake_runner(*args, **kwargs))

    monkeypatch.setattr("dan.cli.code.run_coding_organism_live", _wrapped_runner)

    exit_code = main(
        [
            "--model",
            "gpt-test",
            "--workspace",
            str(tmp_path / "workspace"),
            "--workdir",
            str(tmp_path / "workdir"),
            "--json",
            "Review the current project and summarize the main issues across these scripts.",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["trace_id"] == "trace-fast-code"
    session_payload = json.loads(
        (tmp_path / "workspace" / ".dan-code" / "session.json").read_text()
    )
    assert (
        session_payload["orchestrator_state"]["project_plan"]["active_milestone_id"]
        == "m1"
    )
    assert "project_planner_session" not in session_payload["orchestrator_state"]


def test_main_json_persists_per_run_event_log(tmp_path, capsys, monkeypatch) -> None:
    async def _fake_runner(*_args, **kwargs):
        event_callback = kwargs["event_callback"]
        event_callback(
            {
                "event": "organism.started",
                "objective": kwargs["objective"],
            }
        )
        event_callback(
            {
                "event": "status.update",
                "attempt": 1,
                "actor": "orchestrator",
                "phase": "orchestration",
                "message": "Planning the bounded pass.",
                "worker_count": 1,
            }
        )
        event_callback(
            {
                "event": "tool.started",
                "worker_id": "aggregation.lead",
                "tool_id": "file_read",
                "arguments": {"path": "src/example.py"},
            }
        )
        event_callback(
            {
                "event": "tool.completed",
                "worker_id": "aggregation.lead",
                "tool_id": "file_read",
                "result": {
                    "path": "src/example.py",
                    "line_count": 12,
                    "size": 144,
                },
            }
        )
        return {
            "status": "completed",
            "trace_id": "trace-events",
            "organism_id": "coding-organism",
            "organ_id": "coding-build",
            "task_id": "coding-organ-task:1",
            "objective": kwargs["objective"],
            "candidate_id": "candidate-events",
            "change_summary": "event log check",
            "target_files": ["src/example.py"],
            "test_plan": ["pytest -q tests/test_cli/test_code.py"],
            "risks": [],
            "outputs": {"candidate_id": "candidate-events"},
            "handoff_count": 2,
            "signal_count": 4,
            "error": None,
            "trace_rows": [
                {
                    "kind": "handoff",
                    "message_id": "packet-events",
                    "trace_id": "trace-events",
                    "parent_packet_id": None,
                    "sender_cell_id": "user.request",
                    "recipient_cell_id": "coding-build.orchestrator",
                    "task_id": "coding-organ-task:1:orchestrate:1",
                    "evidence_refs": [],
                    "created_at": "2026-04-18T00:00:02Z",
                }
            ],
        }

    class _FakeReport:
        def __init__(self, payload):
            self._payload = dict(payload)
            self.status = self._payload["status"]
            for key, value in self._payload.items():
                setattr(self, key, value)

        def model_dump(self, mode="json"):
            return dict(self._payload)

        def model_dump_json(self, indent=2):
            return json.dumps(self._payload, indent=indent)

    monkeypatch.setattr("dan.cli.code._build_live_provider", lambda *args, **kwargs: object())

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            CodingConversationTurnDecision(
                action="code",
                public_response="Launching the bounded run.",
                coding_objective=user_message,
            ),
            durable_session,
        )

    async def _fake_review(self, *, session, objective, report_summary, context):
        return (
            CodingConversationReviewDecision(
                action="done",
                public_response="The bounded run is complete.",
            ),
            session,
        )

    async def _fake_plan(self, *, session, user_message, requested_objective, requested_acceptance_criteria, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            CodingProjectPlannerDecision(
                public_response="I’m starting with the current milestone.",
                project_goal=requested_objective,
                plan_summary="Current milestone only.",
                milestones=[
                    CodingProjectMilestone(
                        milestone_id="m1",
                        title="Current slice",
                        objective="Create the current bounded slice.",
                        acceptance_criteria=["Stay bounded."],
                        status="active",
                    )
                ],
                active_milestone_id="m1",
                active_objective="Create the current bounded slice.",
                active_acceptance_criteria=["Stay bounded."],
            ),
            durable_session,
        )

    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.decide_user_turn",
        _fake_decide,
    )
    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.review_coding_result",
        _fake_review,
    )
    monkeypatch.setattr(
        "dan.cli.code.CodingProjectPlannerController.plan_project",
        _fake_plan,
    )

    async def _wrapped_runner(*args, **kwargs):
        return _FakeReport(await _fake_runner(*args, **kwargs))

    monkeypatch.setattr("dan.cli.code.run_coding_organism_live", _wrapped_runner)

    exit_code = main(
        [
            "--model",
            "gpt-test",
            "--workspace",
            str(tmp_path / "workspace"),
            "--workdir",
            str(tmp_path / "workdir"),
            "--json",
            "Fix it with trace",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    event_log_path = Path(payload["event_log_path"])
    control_log_path = Path(payload["control_log_path"])
    assert payload["event_log_schema"] == ORGANISM_LOG_SCHEMA_VERSION
    assert payload["control_log_schema"] == ORGANISM_LOG_SCHEMA_VERSION
    assert event_log_path == (tmp_path / "workdir" / "turn-01" / "events.jsonl").resolve()
    assert control_log_path == (
        tmp_path / "workspace" / ".dan-code" / "control-plane-events.jsonl"
    ).resolve()
    assert event_log_path.exists()
    assert control_log_path.exists()

    event_rows = [
        json.loads(line)
        for line in event_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["sequence"] for row in event_rows] == list(range(1, len(event_rows) + 1))
    assert all(str(row["timestamp"]).endswith("Z") for row in event_rows)
    assert event_rows[0]["event"] == "run.log.started"
    assert any(row["event"] == "status.update" for row in event_rows)
    assert any(row["event"] == "tool.started" for row in event_rows)
    assert any(row["event"] == "tool.completed" for row in event_rows)
    assert any(row["event"] == "trace.handoff" for row in event_rows)
    assert all(row["schema"] == ORGANISM_LOG_SCHEMA_VERSION for row in event_rows)
    assert any(
        row["event"] == "assistant.message"
        and row["message"] == "The bounded run is complete."
        for row in event_rows
    )
    assert event_rows[-1]["event"] == "run.log.completed"
    assert event_rows[-1]["event_log_path"] == str(event_log_path)

    control_rows = [
        json.loads(line)
        for line in control_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    control_events = [row["event"] for row in control_rows]
    assert [row["sequence"] for row in control_rows] == list(
        range(1, len(control_rows) + 1)
    )
    assert all(row["schema"] == ORGANISM_LOG_SCHEMA_VERSION for row in control_rows)
    assert all(row["stream"] == "control_plane" for row in control_rows)
    assert control_rows[0]["event"] == "cli.started"
    assert "orchestrator.turn.started" in control_events
    assert "orchestrator.turn.decision.started" in control_events
    assert "orchestrator.turn.decision.completed" in control_events
    assert "orchestrator.project_planner.started" in control_events
    assert "orchestrator.project_planner.completed" in control_events
    assert "run.turn.started" in control_events
    assert "run.turn.completed" in control_events
    assert "orchestrator.turn.completed" in control_events
    assert control_rows[-1]["event"] == "cli.completed"
    assert any(
        row["event"] == "assistant.message"
        and row["message"] == "Launching the bounded run."
        for row in control_rows
    )

    transcript_line = json.loads(
        (tmp_path / "workspace" / ".dan-code" / "transcript.jsonl")
        .read_text(encoding="utf-8")
        .strip()
    )
    assert transcript_line["event_log_path"] == str(event_log_path)
    assert transcript_line["event_log_schema"] == ORGANISM_LOG_SCHEMA_VERSION
    assert transcript_line["control_log_path"] == str(control_log_path)
    assert transcript_line["control_log_schema"] == ORGANISM_LOG_SCHEMA_VERSION


def test_main_swebench_instance_without_objective_writes_prediction_artifacts(
    tmp_path, capsys, monkeypatch
) -> None:
    instance_path = tmp_path / "instance.json"
    predictions_path = tmp_path / "predictions.jsonl"
    workspace = (tmp_path / "workspace").resolve()
    instance_path.write_text(
        json.dumps(
            {
                "instance_id": "sympy__sympy-20590",
                "repo": "sympy/sympy",
                "base_commit": "abc123",
                "problem_statement": "Fix sympify to catch AttributeError.",
                "requirements": "Keep the patch minimal and avoid unrelated cleanup.",
                "selected_test_files_to_run": [
                    "sympy/core/tests/test_sympify.py"
                ],
                "fail_to_pass": [
                    "sympy/core/tests/test_sympify.py::test_catches_attribute_error"
                ],
                "pass_to_pass": [
                    "sympy/core/tests/test_sympify.py::test_existing_non_error_behavior"
                ],
            }
        ),
        encoding="utf-8",
    )

    async def _fake_runner(*_args, **kwargs):
        assert kwargs["task_id"] == "sympy__sympy-20590:1"
        assert "Fix sympify to catch AttributeError." in kwargs["objective"]
        assert kwargs["approval_callback"].mode == "auto"
        assert kwargs["session_context"]["benchmark_context"] == {
            "benchmark_name": "SWE-bench",
            "instance_id": "sympy__sympy-20590",
            "repo": "sympy/sympy",
            "base_commit": "abc123",
            "selected_test_files_to_run": ["sympy/core/tests/test_sympify.py"],
            "fail_to_pass": [
                "sympy/core/tests/test_sympify.py::test_catches_attribute_error"
            ],
            "pass_to_pass": [
                "sympy/core/tests/test_sympify.py::test_existing_non_error_behavior"
            ],
        }
        assert any(
            "SWE-bench problem statement" in item
            for item in kwargs["evidence_summaries"]
        )
        assert any(
            "SWE-bench FAIL_TO_PASS tests" in item
            for item in kwargs["evidence_summaries"]
        )
        assert any(
            "SWE-bench PASS_TO_PASS tests" in item
            for item in kwargs["evidence_summaries"]
        )
        assert any(
            "Leave the resulting workspace diff intact" in item
            for item in kwargs["acceptance_criteria"]
        )
        assert any(
            "Make the benchmark's FAIL_TO_PASS coverage pass" in item
            for item in kwargs["acceptance_criteria"]
        )
        assert any(
            "Keep the benchmark's PASS_TO_PASS coverage green" in item
            for item in kwargs["acceptance_criteria"]
        )
        assert any(
            "benchmark test files" in item
            for item in kwargs["acceptance_criteria"]
        )
        assert any(
            "illustrative rather than exhaustive" in item
            for item in kwargs["acceptance_criteria"]
        )
        assert any(
            "FAIL_TO_PASS tests define the exact target behavior change" in item
            for item in kwargs["research_findings"]
        )
        assert any(
            "harness-supplied contract evidence" in item
            for item in kwargs["research_findings"]
        )
        assert any(
            "helpers, and parametrizations" in item
            for item in kwargs["research_findings"]
        )
        assert any(
            "Do not invent new warning/error identifiers" in item
            for item in kwargs["hard_constraints"]
        )
        assert any(
            "literal repro string" in item
            for item in kwargs["hard_constraints"]
        )
        assert any(
            "Do not add or edit benchmark test files" in item
            for item in kwargs["hard_constraints"]
        )
        assert any(
            "Do not add missing benchmark test ids or parametrizations" in item
            for item in kwargs["hard_constraints"]
        )
        assert any(
            "public API semantics" in item
            for item in kwargs["soft_constraints"]
        )
        assert any(
            "round-trip tests" in item
            for item in kwargs["soft_constraints"]
        )
        return {
            "status": "completed",
            "trace_id": "trace-swebench",
            "organism_id": "coding-organism",
            "organ_id": "coding-build",
            "task_id": "sympy__sympy-20590:1",
            "objective": kwargs["objective"],
            "candidate_id": "candidate-swebench",
            "change_summary": "Patched sympify error handling.",
            "target_files": ["sympy/core/sympify.py"],
            "test_plan": ["pytest -q sympy/core/tests/test_sympify.py"],
            "risks": [],
            "outputs": {"candidate_id": "candidate-swebench"},
            "handoff_count": 2,
            "signal_count": 4,
            "error": None,
            "trace_rows": [],
        }

    class _FakeReport:
        def __init__(self, payload):
            self._payload = dict(payload)
            self.status = self._payload["status"]
            for key, value in self._payload.items():
                setattr(self, key, value)

        def model_dump(self, mode="json"):
            return dict(self._payload)

        def model_dump_json(self, indent=2):
            return json.dumps(self._payload, indent=indent)

    monkeypatch.setattr("dan.cli.code._build_live_provider", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        "dan.cli.code._capture_workspace_patch_text",
        lambda workspace_root, **_kwargs: (
            "diff --git a/sympy/core/sympify.py b/sympy/core/sympify.py\n"
            "--- a/sympy/core/sympify.py\n"
            "+++ b/sympy/core/sympify.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-old\n"
            "+new\n"
        ),
    )

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            CodingConversationTurnDecision(
                action="code",
                public_response="Launching the SWE-bench coding run.",
                coding_objective=user_message,
            ),
            durable_session,
        )

    async def _fake_review(self, *, session, objective, report_summary, context):
        return (
            CodingConversationReviewDecision(
                action="done",
                public_response="The SWE-bench pass is complete.",
            ),
            session,
        )

    async def _fake_plan(
        self,
        *,
        session,
        user_message,
        requested_objective,
        requested_acceptance_criteria,
        context,
    ):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            CodingProjectPlannerDecision(
                public_response="I’m keeping this to one benchmark-scoped slice.",
                project_goal=requested_objective,
                plan_summary="Single benchmark milestone.",
                milestones=[
                    CodingProjectMilestone(
                        milestone_id="m1",
                        title="Benchmark fix",
                        objective=requested_objective,
                        acceptance_criteria=["Use the milestone objective."],
                        status="active",
                    )
                ],
                active_milestone_id="m1",
                active_objective=requested_objective,
                active_acceptance_criteria=["Use the milestone objective."],
            ),
            durable_session,
        )

    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.decide_user_turn",
        _fake_decide,
    )
    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.review_coding_result",
        _fake_review,
    )
    monkeypatch.setattr(
        "dan.cli.code.CodingProjectPlannerController.plan_project",
        _fake_plan,
    )

    async def _wrapped_runner(*args, **kwargs):
        return _FakeReport(await _fake_runner(*args, **kwargs))

    monkeypatch.setattr("dan.cli.code.run_coding_organism_live", _wrapped_runner)

    exit_code = main(
        [
            "--model",
            "kimi-k2.5",
            "--workspace",
            str(workspace),
            "--workdir",
            str(tmp_path / "workdir"),
            "--json",
            "--swebench-instance-file",
            str(instance_path),
            "--swebench-predictions-path",
            str(predictions_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    swebench_output = payload["outputs"]["swebench"]
    assert swebench_output["instance_id"] == "sympy__sympy-20590"
    assert swebench_output["model_name_or_path"] == "kimi-k2.5"
    assert Path(swebench_output["instance_artifact_path"]).exists()
    assert Path(swebench_output["patch_artifact_path"]).exists()
    assert Path(swebench_output["prediction_artifact_path"]).exists()
    prediction_lines = predictions_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(prediction_lines) == 1
    prediction = json.loads(prediction_lines[0])
    assert prediction["instance_id"] == "sympy__sympy-20590"
    assert prediction["model_name_or_path"] == "kimi-k2.5"
    assert "diff --git a/sympy/core/sympify.py" in prediction["model_patch"]


def test_main_swebench_review_continue_exhausts_without_prediction(
    tmp_path, capsys, monkeypatch
) -> None:
    instance_path = tmp_path / "instance.json"
    predictions_path = tmp_path / "predictions.jsonl"
    workspace = (tmp_path / "workspace").resolve()
    instance_path.write_text(
        json.dumps(
            {
                "instance_id": "astropy__astropy-14365",
                "repo": "astropy/astropy",
                "base_commit": "abc123",
                "problem_statement": "Handle lowercase qdp NO markers.",
                "selected_test_files_to_run": ["astropy/io/ascii/tests/test_qdp.py"],
                "fail_to_pass": [
                    "astropy/io/ascii/tests/test_qdp.py::test_roundtrip"
                ],
                "pass_to_pass": [
                    "astropy/io/ascii/tests/test_qdp.py::test_get_tables_from_qdp_file"
                ],
            }
        ),
        encoding="utf-8",
    )
    runner_calls: list[str] = []

    async def _fake_runner(*_args, **kwargs):
        runner_calls.append(kwargs["task_id"])
        turn_number = len(runner_calls)
        return CodingOrganismReport(
            status="completed",
            trace_id=f"trace-{turn_number}",
            organism_id="coding-organism",
            organ_id="coding-build",
            task_id=kwargs["task_id"],
            objective=kwargs["objective"],
            candidate_id=f"candidate-{turn_number}",
            change_summary="Produced a candidate patch that still needs validation.",
            target_files=["astropy/io/ascii/qdp.py"],
            test_plan=["pytest -q astropy/io/ascii/tests/test_qdp.py"],
            risks=[],
            outputs={"candidate_id": f"candidate-{turn_number}"},
            handoff_count=1,
            signal_count=1,
            error=None,
            trace_rows=[],
        )

    def _unexpected_patch_capture(_workspace_root, **_kwargs):
        raise AssertionError("SWE-bench artifacts should not be exported on continue")

    monkeypatch.setattr("dan.cli.code._build_live_provider", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        "dan.cli.code._capture_workspace_patch_text",
        _unexpected_patch_capture,
    )

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            CodingConversationTurnDecision(
                action="code",
                public_response="Launching the SWE-bench coding run.",
                coding_objective=user_message,
            ),
            durable_session,
        )

    async def _fake_review(self, *, session, objective, report_summary, context):
        return (
            CodingConversationReviewDecision(
                action="continue",
                public_response="The candidate still needs benchmark validation.",
                next_objective="Run the target regression tests and repair any failures.",
                repair_brief="The patch is not validated enough to export.",
            ),
            session,
        )

    async def _fake_plan(
        self,
        *,
        session,
        user_message,
        requested_objective,
        requested_acceptance_criteria,
        context,
    ):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            CodingProjectPlannerDecision(
                public_response="Using one benchmark-scoped milestone.",
                project_goal=requested_objective,
                plan_summary="Single benchmark milestone.",
                milestones=[
                    CodingProjectMilestone(
                        milestone_id="m1",
                        title="Benchmark fix",
                        objective=requested_objective,
                        acceptance_criteria=["Use the milestone objective."],
                        status="active",
                    )
                ],
                active_milestone_id="m1",
                active_objective=requested_objective,
                active_acceptance_criteria=["Use the milestone objective."],
            ),
            durable_session,
        )

    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.decide_user_turn",
        _fake_decide,
    )
    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.review_coding_result",
        _fake_review,
    )
    monkeypatch.setattr(
        "dan.cli.code.CodingProjectPlannerController.plan_project",
        _fake_plan,
    )
    monkeypatch.setattr("dan.cli.code.run_coding_organism_live", _fake_runner)

    exit_code = main(
        [
            "--model",
            "kimi-k2.5",
            "--workspace",
            str(workspace),
            "--workdir",
            str(tmp_path / "workdir"),
            "--json",
            "--swebench-instance-file",
            str(instance_path),
            "--swebench-predictions-path",
            str(predictions_path),
        ]
    )

    assert exit_code == 1
    assert len(runner_calls) == 4
    assert not predictions_path.exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "incomplete"
    assert "refusing to export an unfinished SWE-bench prediction" in payload["error"]
    assert "swebench" not in payload["outputs"]


def test_run_coding_organism_live_benchmark_increases_repair_rounds(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeToolRuntime:
        def __init__(self, **kwargs):
            self.tool_ids = list(kwargs.get("tool_ids") or [])

    class _FakeCompletionProvider:
        def __init__(self, *args, **kwargs):
            self.kwargs = dict(kwargs)

    class _FakeExecutor:
        def __init__(self, completion_provider, event_sink=None):
            self.completion_provider = completion_provider
            self.event_sink = event_sink

    class _FakeOrganism:
        def __init__(self, *, max_repair_rounds: int = 1):
            self.max_repair_rounds = max_repair_rounds

        def model_copy(self, update=None, **kwargs):
            updated = dict(update or {})
            return _FakeOrganism(
                max_repair_rounds=int(
                    updated.get("max_repair_rounds", self.max_repair_rounds)
                )
            )

    async def _fake_execute_coding_organism(
        *,
        executor,
        organism,
        task,
        trace_log,
        event_callback,
    ):
        _ = (executor, trace_log, event_callback)
        captured["organism"] = organism
        captured["task"] = task
        return SimpleNamespace(
            result=SimpleNamespace(
                status="completed",
                final_output={
                    "candidate_id": "candidate-benchmark",
                    "change_summary": "bounded benchmark candidate",
                    "target_files": ["src/example.py"],
                    "test_plan": ["pytest -q tests/test_cli/test_code.py"],
                    "risks": [],
                },
                observability=SimpleNamespace(
                    trace_id="trace-benchmark",
                    trace_rows=[],
                ),
                error=None,
            )
        )

    monkeypatch.setattr("dan.cli.code.LocalOrganismToolRuntime", _FakeToolRuntime)
    monkeypatch.setattr("dan.cli.code.ToolLoopCompletionProvider", _FakeCompletionProvider)
    monkeypatch.setattr("dan.cli.code.WorkerCoreExecutor", _FakeExecutor)
    monkeypatch.setattr(
        "dan.cli.code.coding_execution_organism",
        lambda **kwargs: _FakeOrganism(),
    )
    monkeypatch.setattr(
        "dan.cli.code.attach_local_tooling_to_coding_organism",
        lambda organism, tool_ids: organism,
    )
    monkeypatch.setattr("dan.cli.code._build_evidence_refs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "dan.cli.code.execute_coding_organism",
        _fake_execute_coding_organism,
    )

    report = asyncio.run(
        run_coding_organism_live(
            tmp_path / "workdir",
            llm_provider=object(),
            objective="Fix the benchmark issue.",
            model="gpt-test",
            tool_ids=["file_read"],
            hard_constraints=["keep the contract exact"],
            soft_constraints=["prefer the smallest viable change"],
            session_context={
                "benchmark_context": {
                    "benchmark_name": "SWE-bench",
                    "instance_id": "demo__demo-1",
                }
            },
        )
    )

    organism = captured["organism"]
    task = captured["task"]
    assert isinstance(organism, _FakeOrganism)
    assert organism.max_repair_rounds == 2
    assert task.hard_constraints == ["keep the contract exact"]
    assert task.soft_constraints == ["prefer the smallest viable change"]
    assert report.candidate_id == "candidate-benchmark"


def test_main_without_objective_enters_interactive_loop(tmp_path, monkeypatch) -> None:
    calls: dict[str, object] = {}

    monkeypatch.setattr("dan.cli.code._build_live_provider", lambda *args, **kwargs: object())

    def _fake_loop(
        *,
        args,
        controller,
        project_planner,
        llm_provider,
        workspace_root,
        model,
        thinking_mode,
        session,
        product_paths,
        tool_ids,
        acceptance_criteria,
        max_tool_rounds,
        max_tool_calls,
        completion_timeout_seconds,
        persist_session,
        run_root,
        approval_state,
        approval_mode,
        progress_renderer,
        control_logger,
    ):
        calls["objective"] = args.objective
        calls["controller"] = controller
        calls["project_planner"] = project_planner
        calls["workspace_root"] = workspace_root
        calls["model"] = model
        calls["provider"] = llm_provider
        calls["session_id"] = session.session_id
        calls["product_paths"] = product_paths
        calls["tool_ids"] = tool_ids
        calls["acceptance_criteria"] = acceptance_criteria
        calls["max_tool_rounds"] = max_tool_rounds
        calls["max_tool_calls"] = max_tool_calls
        calls["completion_timeout_seconds"] = completion_timeout_seconds
        calls["persist_session"] = persist_session
        calls["run_root"] = run_root
        calls["thinking_mode"] = thinking_mode
        calls["approval_state"] = approval_state
        calls["approval_mode"] = approval_mode
        calls["progress_renderer"] = progress_renderer
        calls["control_logger_path"] = control_logger.path
        return 0

    monkeypatch.setattr("dan.cli.code._interactive_loop", _fake_loop)

    exit_code = main(
        [
            "--model",
            "gpt-test",
            "--workspace",
            str(tmp_path / "workspace"),
        ]
    )

    assert exit_code == 0
    assert calls["objective"] is None
    assert Path(calls["workspace_root"]) == (tmp_path / "workspace").resolve()
    assert calls["model"] == "gpt-test"
    assert Path(calls["product_paths"].root) == (tmp_path / "workspace" / ".dan-code").resolve()
    assert calls["tool_ids"] == [
        "list_directory",
        "file_read",
        "workspace_check",
        "file_edit",
        "file_write",
        "shell_command",
        "web_search",
        "git_status",
        "git_diff",
        "git_log",
    ]
    assert calls["acceptance_criteria"] == [
        "Return exactly one bounded candidate.",
        "Name the target files explicitly.",
        "Include a focused validation plan.",
        "Keep the change summary and risks inspectable.",
    ]
    assert calls["max_tool_rounds"] is None
    assert calls["max_tool_calls"] == 24
    assert calls["completion_timeout_seconds"] == 90.0
    assert calls["persist_session"] is True
    assert calls["thinking_mode"] == "auto"
    assert calls["approval_mode"] == "confirm-risky"
    assert Path(calls["run_root"]) == (tmp_path / "workspace" / ".dan-code" / "runs").resolve()
    control_log_path = tmp_path / "workspace" / ".dan-code" / "control-plane-events.jsonl"
    assert Path(calls["control_logger_path"]) == control_log_path.resolve()
    control_rows = [
        json.loads(line)
        for line in control_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["event"] for row in control_rows] == ["cli.started", "cli.completed"]
    assert all(row["stream"] == "control_plane" for row in control_rows)


def test_structured_workspace_check_objective_replaces_stale_html_shell_counts() -> None:
    stale_objective = (
        "Read /tmp/site/index.html from disk. Use shell_command grep -c to report "
        "exact counts of <html>, </html>, <head>, </head>, <body>, </body>, "
        "<main>, and </main> before repairing duplicates."
    )

    normalized, changed = _normalize_structured_workspace_check_objective(
        stale_objective,
        tool_ids=["file_read", "workspace_check", "shell_command"],
    )

    assert changed is True
    assert "workspace_check" in normalized
    assert 'check="html_tags"' in normalized
    assert 'path="index.html"' in normalized
    assert "grep -c" not in normalized


def test_structured_workspace_check_acceptance_replaces_stale_shell_counts() -> None:
    stale_objective = (
        "Use shell_command to report counts for <html>, </html>, <head>, </head>, "
        "<body>, </body>, <main>, and </main> in index.html."
    )
    criteria = [
        "Return exactly one bounded candidate.",
        "Use shell_command grep counts for index.html structural tags.",
    ]

    normalized, changed = _normalize_structured_workspace_check_acceptance_criteria(
        criteria,
        objective=stale_objective,
        tool_ids=["workspace_check", "shell_command"],
    )

    assert changed is True
    assert "Return exactly one bounded candidate." in normalized
    assert not any("grep" in criterion for criterion in normalized)
    assert any("workspace_check" in criterion for criterion in normalized)


def test_main_init_writes_workspace_product_config(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "--workspace",
            str(tmp_path / "workspace"),
            "--model",
            "gpt-test",
            "--init",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["initialized"] is True
    config_path = tmp_path / "workspace" / ".dan-code" / "config.json"
    config_payload = json.loads(config_path.read_text())
    assert config_payload["default_model"] == "gpt-test"
    assert config_payload["thinking_mode"] == "auto"
    assert config_payload["default_tool_ids"] == [
        "list_directory",
        "file_read",
        "workspace_check",
        "file_edit",
        "file_write",
        "shell_command",
        "web_search",
        "git_status",
        "git_diff",
        "git_log",
    ]
    assert config_payload["max_tool_rounds"] is None
    assert payload["max_tool_rounds"] is None


def test_main_non_task_message_routes_through_orchestrator(capsys, monkeypatch) -> None:
    async def _unexpected_runner(*args, **kwargs):
        raise AssertionError("coding runner should not be called for a conversational greeting")

    monkeypatch.setattr("dan.cli.code._build_live_provider", lambda *args, **kwargs: object())
    monkeypatch.setattr("dan.cli.code.run_coding_organism_live", _unexpected_runner)

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            CodingConversationTurnDecision(
                action="respond",
                public_response="I can chat, inspect the repo, and launch bounded coding runs when you ask for concrete work.",
            ),
            durable_session,
        )

    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.decide_user_turn",
        _fake_decide,
    )

    exit_code = main(["--model", "gpt-test", "hi"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "I can chat, inspect the repo, and launch bounded coding runs" in stdout


def test_main_typo_greeting_still_uses_normal_run_path(capsys, monkeypatch) -> None:
    calls: list[str] = []
    payload = {
        "status": "completed",
        "trace_id": "trace-code",
        "organism_id": "coding-organism",
        "organ_id": "coding-build",
        "task_id": "coding-organ-task:1",
        "objective": "hii",
        "candidate_id": "candidate-1",
        "change_summary": "repair",
        "target_files": [],
        "test_plan": [],
        "risks": [],
        "outputs": {},
        "handoff_count": 0,
        "signal_count": 0,
        "error": None,
        "trace_rows": [],
    }

    class _FakeReport:
        def __init__(self) -> None:
            self.status = "completed"
            for key, value in payload.items():
                setattr(self, key, value)

        def model_dump_json(self, indent=2):
            return json.dumps(payload, indent=indent)

        def model_dump(self, mode="json"):
            return dict(payload)

    monkeypatch.setattr("dan.cli.code._build_live_provider", lambda *args, **kwargs: object())

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            CodingConversationTurnDecision(
                action="code",
                public_response="Treating this as a coding turn.",
                coding_objective=user_message,
            ),
            durable_session,
        )

    async def _fake_review(self, *, session, objective, report_summary, context):
        return (
            CodingConversationReviewDecision(
                action="done",
                public_response="This pass is done.",
            ),
            session,
        )

    async def _fake_plan(self, *, session, user_message, requested_objective, requested_acceptance_criteria, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            CodingProjectPlannerDecision(
                public_response="This stays as one milestone.",
                project_goal=requested_objective,
                plan_summary="Single milestone.",
                milestones=[
                    CodingProjectMilestone(
                        milestone_id="m1",
                        title="Single slice",
                        objective=requested_objective,
                        acceptance_criteria=list(requested_acceptance_criteria),
                        status="active",
                    )
                ],
                active_milestone_id="m1",
                active_objective=requested_objective,
                active_acceptance_criteria=list(requested_acceptance_criteria),
            ),
            durable_session,
        )

    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.decide_user_turn",
        _fake_decide,
    )
    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.review_coding_result",
        _fake_review,
    )
    monkeypatch.setattr(
        "dan.cli.code.CodingProjectPlannerController.plan_project",
        _fake_plan,
    )

    async def _fake_runner(*args, **kwargs):
        calls.append(str(kwargs["objective"]))
        return _FakeReport()

    monkeypatch.setattr("dan.cli.code.run_coding_organism_live", _fake_runner)

    exit_code = main(["--model", "gpt-test", "--json", "hii"])

    assert exit_code == 0
    assert calls == ["hii"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["objective"] == "hii"


def test_main_clarifying_turn_persists_pending_question(tmp_path, capsys, monkeypatch) -> None:
    workspace = (tmp_path / "workspace").resolve()
    monkeypatch.setattr("dan.cli.code._build_live_provider", lambda *args, **kwargs: object())

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            CodingConversationTurnDecision(
                action="clarify",
                public_response="I need one concrete clarification before I start coding.",
                clarifying_question="Do you want this as a standalone demo folder or inside the existing app?",
            ),
            durable_session,
        )

    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.decide_user_turn",
        _fake_decide,
    )

    exit_code = main(
        [
            "--workspace",
            str(workspace),
            "--model",
            "gpt-test",
            "can you help me build a demo website about sharks?",
        ]
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "I need one concrete clarification before I start coding." in stdout
    session_payload = json.loads((workspace / ".dan-code" / "session.json").read_text())
    assert (
        session_payload["pending_clarification"]
        == "Do you want this as a standalone demo folder or inside the existing app?"
    )
    assert session_payload["conversation"][-1]["role"] == "assistant"


def test_main_workspace_query_routes_through_orchestrator_with_shared_facts(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    provider_calls: list[str] = []

    monkeypatch.setattr(
        "dan.cli.code._build_live_provider",
        lambda model, **kwargs: provider_calls.append(str(model)) or object(),
    )

    async def _unexpected_runner(*args, **kwargs):
        raise AssertionError("coding runner should not be called for a workspace question")

    monkeypatch.setattr("dan.cli.code.run_coding_organism_live", _unexpected_runner)

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        assert user_message == "what is the root dir now"
        assert context.facts.workspace_root == str(workspace)
        assert context.facts.effective_working_directory == str(workspace)
        assert context.facts.active_model == "gpt-test"
        return (
            CodingConversationTurnDecision(
                action="respond",
                public_response=(
                    f"workspace root: {context.facts.workspace_root}; "
                    "effective working directory: "
                    f"{context.facts.effective_working_directory}"
                ),
            ),
            durable_session,
        )

    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.decide_user_turn",
        _fake_decide,
    )

    exit_code = main(
        [
            "--workspace",
            str(workspace),
            "--model",
            "gpt-test",
            "what is the root dir now",
        ]
    )

    assert exit_code == 0
    assert provider_calls == ["gpt-test"]
    stdout = capsys.readouterr().out
    assert f"workspace root: {workspace}" in stdout
    assert "effective working directory:" in stdout


def test_main_working_dir_query_routes_through_orchestrator_with_shared_facts(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    provider_calls: list[str] = []

    monkeypatch.setattr(
        "dan.cli.code._build_live_provider",
        lambda model, **kwargs: provider_calls.append(str(model)) or object(),
    )

    async def _unexpected_runner(*args, **kwargs):
        raise AssertionError(
            "coding runner should not be called for a working-directory question"
        )

    monkeypatch.setattr("dan.cli.code.run_coding_organism_live", _unexpected_runner)

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        assert user_message == "what is the current working dir?"
        return (
            CodingConversationTurnDecision(
                action="respond",
                public_response=(
                    "effective working directory: "
                    f"{context.facts.effective_working_directory}; "
                    f"shell process directory: {context.facts.shell_process_directory}"
                ),
            ),
            durable_session,
        )

    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.decide_user_turn",
        _fake_decide,
    )

    exit_code = main(
        [
            "--workspace",
            str(workspace),
            "--model",
            "gpt-test",
            "what is the current working dir?",
        ]
    )

    assert exit_code == 0
    assert provider_calls == ["gpt-test"]
    stdout = capsys.readouterr().out
    assert f"effective working directory: {workspace}" in stdout
    assert "shell process directory:" in stdout


def test_main_status_query_routes_through_orchestrator_with_saved_report_facts(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    paths = resolve_code_product_paths(workspace)
    session = CodingCliSession(workspace_root=str(workspace))
    session.record_turn(
        CodingOrganismReport(
            status="failed",
            trace_id="trace-code",
            organism_id="coding-organism",
            organ_id="coding-build",
            task_id="coding-organ-task:1",
            objective="Build the website",
            candidate_id=None,
            change_summary="",
            target_files=[],
            test_plan=[],
            risks=[],
            outputs={},
            handoff_count=0,
            signal_count=0,
            error="did not produce a validated candidate",
            trace_rows=[],
        )
    )
    save_code_product_session(paths, session)
    provider_calls: list[str] = []

    monkeypatch.setattr(
        "dan.cli.code._build_live_provider",
        lambda model, **kwargs: provider_calls.append(str(model)) or object(),
    )

    async def _unexpected_runner(*args, **kwargs):
        raise AssertionError("coding runner should not be called for a status question")

    monkeypatch.setattr("dan.cli.code.run_coding_organism_live", _unexpected_runner)

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        assert user_message == "so what is the current status"
        assert context.facts.latest_report_status == "failed"
        assert context.facts.latest_report_objective == "Build the website"
        return (
            CodingConversationTurnDecision(
                action="respond",
                public_response=(
                    f"latest run status: {context.facts.latest_report_status}; "
                    f"latest objective: {context.facts.latest_report_objective}"
                ),
            ),
            durable_session,
        )

    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.decide_user_turn",
        _fake_decide,
    )

    exit_code = main(
        [
            "--workspace",
            str(workspace),
            "--model",
            "gpt-test",
            "so what is the current status",
        ]
    )

    assert exit_code == 0
    assert provider_calls == ["gpt-test"]
    stdout = capsys.readouterr().out
    assert "latest run status: failed" in stdout
    assert "latest objective: Build the website" in stdout


def test_main_results_query_routes_through_orchestrator_with_recent_reports(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    paths = resolve_code_product_paths(workspace)
    session = CodingCliSession(workspace_root=str(workspace))
    session.record_turn(
        CodingOrganismReport(
            status="completed",
            trace_id="trace-code",
            organism_id="coding-organism",
            organ_id="coding-build",
            task_id="coding-organ-task:1",
            objective="Fix it",
            candidate_id="candidate-1",
            change_summary="repair",
            target_files=["src/example.py"],
            test_plan=["pytest -q"],
            risks=["low residual risk"],
            outputs={"candidate_id": "candidate-1"},
            handoff_count=4,
            signal_count=8,
            trace_rows=[],
        )
    )
    save_code_product_session(paths, session)
    provider_calls: list[str] = []

    monkeypatch.setattr(
        "dan.cli.code._build_live_provider",
        lambda model, **kwargs: provider_calls.append(str(model)) or object(),
    )

    async def _unexpected_runner(*args, **kwargs):
        raise AssertionError("coding runner should not be called for a results question")

    monkeypatch.setattr("dan.cli.code.run_coding_organism_live", _unexpected_runner)

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        assert user_message == "ok, tell me the results?"
        assert len(context.recent_reports) == 1
        report = context.recent_reports[0]
        assert report.candidate_id == "candidate-1"
        assert report.target_files == ["src/example.py"]
        return (
            CodingConversationTurnDecision(
                action="respond",
                public_response=(
                    f"latest status: {report.status}; "
                    f"candidate: {report.candidate_id}; "
                    f"files: {', '.join(report.target_files)}"
                ),
            ),
            durable_session,
        )

    monkeypatch.setattr(
        "dan.cli.code.CodingConversationController.decide_user_turn",
        _fake_decide,
    )

    exit_code = main(
        [
            "--workspace",
            str(workspace),
            "--model",
            "gpt-test",
            "ok, tell me the results?",
        ]
    )

    assert exit_code == 0
    assert provider_calls == ["gpt-test"]
    stdout = capsys.readouterr().out
    assert "latest status: completed" in stdout
    assert "candidate: candidate-1" in stdout
    assert "files: src/example.py" in stdout


def test_progress_renderer_can_show_public_model_trace(capsys) -> None:
    renderer = CodeProgressRenderer(enabled=True, show_model_trace=True)

    renderer(
        {
            "event": "model.requested",
            "round": 1,
            "model": "gpt-test",
            "tool_count": 3,
            "worker_id": "coding-build.worker-1",
        }
    )
    renderer(
        {
            "event": "model.responded",
            "round": 1,
            "model": "gpt-test",
            "tool_calls": ["file_read", "git_status"],
            "finish_reason": "tool_calls",
            "text": "Inspecting the workspace before choosing the next action.",
            "worker_id": "coding-build.worker-1",
        }
    )

    stdout = capsys.readouterr().out
    assert "[worker-1][model] request: round=1 model=gpt-test tools=3" in stdout
    assert (
        "[worker-1][model] response: round=1 model=gpt-test tool_calls=file_read, git_status "
        "finish_reason=tool_calls"
    ) in stdout
    assert "[worker-1][model] preview: Inspecting the workspace before choosing the next action." in stdout


def test_progress_renderer_can_show_streamed_public_model_trace(capsys) -> None:
    renderer = CodeProgressRenderer(enabled=True, show_model_trace=True)

    renderer(
        {
            "event": "model.stream.started",
            "round": 1,
            "model": "gpt-test",
            "worker_id": "coding-build.worker-1",
        }
    )
    renderer(
        {
            "event": "model.stream.delta",
            "round": 1,
            "model": "gpt-test",
            "delta": "Streaming",
            "worker_id": "coding-build.worker-1",
        }
    )
    renderer(
        {
            "event": "model.stream.delta",
            "round": 1,
            "model": "gpt-test",
            "delta": " preview",
            "worker_id": "coding-build.worker-1",
        }
    )
    renderer(
        {
            "event": "model.stream.completed",
            "round": 1,
            "model": "gpt-test",
            "worker_id": "coding-build.worker-1",
        }
    )
    renderer(
        {
            "event": "model.responded",
            "round": 1,
            "model": "gpt-test",
            "tool_calls": [],
            "finish_reason": "stream",
            "text": "Streaming preview",
            "streamed": True,
            "worker_id": "coding-build.worker-1",
        }
    )

    stdout = capsys.readouterr().out
    assert "[worker-1][model] stream: Streaming preview" in stdout
    assert "[worker-1][model] response: round=1 model=gpt-test finish_reason=stream" in stdout
    assert "[worker-1][model] preview:" not in stdout


def test_progress_renderer_falls_back_to_preview_when_streamed_turn_has_no_visible_delta(capsys) -> None:
    renderer = CodeProgressRenderer(enabled=True, show_model_trace=True)

    renderer(
        {
            "event": "model.stream.started",
            "round": 1,
            "model": "gpt-test",
            "worker_id": "coding-build.aggregation.lead",
        }
    )
    renderer(
        {
            "event": "model.stream.completed",
            "round": 1,
            "model": "gpt-test",
            "worker_id": "coding-build.aggregation.lead",
        }
    )
    renderer(
        {
            "event": "model.responded",
            "round": 1,
            "model": "gpt-test",
            "tool_calls": [],
            "finish_reason": "stream",
            "text": "Merged the candidate without any extra narration.",
            "streamed": True,
            "worker_id": "coding-build.aggregation.lead",
        }
    )

    stdout = capsys.readouterr().out
    assert "[aggregation.lead][model] stream:" not in stdout
    assert "[aggregation.lead][model] response: round=1 model=gpt-test finish_reason=stream" in stdout
    assert "[aggregation.lead][model] preview: Merged the candidate without any extra narration." in stdout


def test_progress_renderer_shows_public_stage_feedback_by_default(capsys) -> None:
    renderer = CodeProgressRenderer(enabled=True)

    renderer(
        {
            "event": "organism.started",
            "objective": "Inspect the failing worker tests and produce one bounded fix.",
        }
    )
    renderer(
        {
            "event": "attempt.started",
            "attempt": 1,
            "reason": "initial",
        }
    )
    renderer(
        {
            "event": "stage.started",
            "stage": "workers",
            "attempt": 1,
            "message": "Running 2 coding workers.",
        }
    )
    renderer(
        {
            "event": "status.update",
            "actor": "orchestrator",
            "phase": "orchestration",
            "attempt": 1,
            "message": "Split the fix into a validator-path patch and an explicit delivery summary.",
            "worker_count": 2,
        }
    )
    renderer(
        {
            "event": "status.update",
            "actor": "worker-1",
            "phase": "workers",
            "attempt": 1,
            "message": "Narrow validator-path patch.",
            "target_files": ["src/dan/worker/organisms/coding_execution.py"],
            "test_plan": ["PYTHONPATH=src pytest -q tests/test_worker/test_coding_organism.py"],
        }
    )
    renderer(
        {
            "event": "stage.completed",
            "stage": "validation",
            "attempt": 1,
            "status": "completed",
            "message": "Validator scored the candidate at 0.76.",
            "score": 0.76,
            "passed": False,
            "missing_requirements": ["focused validation command"],
        }
    )
    renderer(
        {
            "event": "status.update",
            "actor": "validator",
            "phase": "validation",
            "attempt": 1,
            "message": "Candidate 1 is narrow but still under-specifies validation and delivery.",
            "score": 0.76,
            "missing_requirements": ["focused validation command"],
        }
    )
    renderer(
        {
            "event": "repair.requested",
            "attempt": 1,
            "score": 0.76,
            "repair_brief": "Make the focused validation command explicit.",
            "missing_requirements": ["focused validation command"],
        }
    )
    renderer(
        {
            "event": "status.update",
            "actor": "validator",
            "phase": "repair",
            "attempt": 1,
            "message": "Make the focused validation command explicit.",
            "missing_requirements": ["focused validation command"],
        }
    )
    renderer(
        {
            "event": "organism.completed",
            "status": "completed",
            "selected_attempt": 2,
            "candidate_id": "candidate-2",
        }
    )

    stdout = capsys.readouterr().out
    assert "[status] starting coding run:" in stdout
    assert "[status] attempt 1: starting" in stdout
    assert "[status] attempt 1 / workers: running" in stdout
    assert (
        "[assistant] attempt 1 / orchestrator / orchestration: "
        "Split the fix into a validator-path patch and an explicit delivery summary. (workers=2)"
    ) in stdout
    assert (
        "[assistant] attempt 1 / worker-1 / workers: Narrow validator-path patch. "
        "(files=1, validation=1)"
    ) in stdout
    assert "[status] attempt 1 / validation: completed (score=0.76, passed=no, gaps=1)" in stdout
    assert (
        "[assistant] attempt 1 / validator / validation: Candidate 1 is narrow but still under-specifies "
        "validation and delivery. (score=0.76, gaps=1)"
    ) in stdout
    assert "[status] validator requested repair after attempt 1 (score=0.76, missing=1)" in stdout
    assert (
        "[assistant] attempt 1 / validator / repair: Make the focused validation command explicit. (gaps=1)"
    ) in stdout
    assert "[status] coding run completed (attempt=2, candidate=candidate-2)" in stdout


def test_progress_renderer_summarizes_tool_output(capsys) -> None:
    renderer = CodeProgressRenderer(enabled=True)

    renderer(
        {
            "event": "tool.completed",
            "tool_id": "list_directory",
            "worker_id": "coding-build.worker-2",
            "result": {
                "entries": [{"path": "src/dan"}],
                "count": 34,
                "total_count": 120,
                "remaining_count": 86,
                "truncated": True,
            },
        }
    )
    renderer(
        {
            "event": "tool.completed",
            "tool_id": "file_read",
            "result": {
                "path": "src/dan/cli/code.py",
                "line_count": 42,
                "size": 900,
                "content": "ignored",
            },
        }
    )

    stdout = capsys.readouterr().out
    assert "[worker-2][tool] ok list_directory: entries=34/120 remaining=86" in stdout
    assert "[tool] ok file_read: lines=42 bytes=900 path=src/dan/cli/code.py" in stdout


def test_progress_renderer_renders_heartbeat_and_timeout(capsys) -> None:
    renderer = CodeProgressRenderer(enabled=True)

    renderer(
        {
            "event": "code.heartbeat",
            "worker_id": "coding-build.worker-2",
            "phase": "tool",
            "detail": "web_search {\"query\": \"textual sqlite demo\"}",
            "elapsed_seconds": 14,
        }
    )
    renderer(
        {
            "event": "model.timeout",
            "worker_id": "coding-build.worker-2",
            "round": 3,
            "model": "gpt-test",
            "timeout_seconds": 90.0,
        }
    )

    stdout = capsys.readouterr().out
    assert "[status] heartbeat: tool [worker-2]" in stdout
    assert "idle=14s" in stdout
    assert "[status] model timeout [worker-2]: round=3 model=gpt-test timeout=90.00s" in stdout


def test_main_show_config_resolves_thinking_mode_from_env(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("DAN_CODE_THINKING_MODE", "disabled")

    exit_code = main(
        [
            "--workspace",
            str(tmp_path / "workspace"),
            "--model",
            "gpt-test",
            "--show-config",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved_thinking_mode"] == "disabled"


def test_main_show_config_resolves_completion_timeout_from_env(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("DAN_CODE_COMPLETION_TIMEOUT_SECONDS", "45")

    exit_code = main(
        [
            "--workspace",
            str(tmp_path / "workspace"),
            "--model",
            "gpt-test",
            "--show-config",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["completion_timeout_seconds"] == 45.0


@pytest.mark.asyncio
async def test_code_heartbeat_monitor_emits_idle_events() -> None:
    events: list[dict[str, object]] = []
    monitor = CodeHeartbeatMonitor(
        event_callback=events.append,
        idle_seconds=0.01,
        repeat_seconds=0.01,
        poll_seconds=0.01,
    )

    await monitor.start()
    monitor.observe(
        {
            "event": "model.requested",
            "worker_id": "coding-build.worker-1",
            "round": 1,
            "tool_count": 2,
        }
    )
    await asyncio.sleep(0.05)
    await monitor.stop()

    heartbeat_events = [
        event for event in events if event.get("event") == "code.heartbeat"
    ]
    assert heartbeat_events
    assert heartbeat_events[0]["worker_id"] == "coding-build.worker-1"
    assert heartbeat_events[0]["phase"] == "model"
    assert "round=1" in str(heartbeat_events[0]["detail"])


def test_normalize_workspace_root_uses_pwd_when_getcwd_is_unavailable(tmp_path, monkeypatch) -> None:
    expected = (tmp_path / "workspace").resolve()
    monkeypatch.setenv("PWD", str(expected))

    def _missing_cwd(cls):
        raise FileNotFoundError("cwd missing")

    monkeypatch.setattr(Path, "cwd", classmethod(_missing_cwd))

    assert normalize_workspace_root(".") == expected


def test_coding_report_normalizes_multiline_string_test_plan() -> None:
    report = CodingOrganismReport.model_validate(
        {
            "status": "failed",
            "trace_id": "trace-1",
            "organism_id": "coding-organism",
            "organ_id": "coding-build",
            "task_id": "coding-organ-task:1",
            "objective": "Verify the site",
            "candidate_id": "candidate-1",
            "change_summary": "verification only",
            "target_files": "/tmp/example.html",
            "test_plan": "1. Open page\n2. Check navbar styling\n3. Verify animations",
            "risks": "No risks identified",
            "outputs": {},
            "handoff_count": 1,
            "signal_count": 2,
        }
    )

    assert report.target_files == ["/tmp/example.html"]
    assert report.test_plan == [
        "1. Open page",
        "2. Check navbar styling",
        "3. Verify animations",
    ]
    assert report.risks == ["No risks identified"]


def test_session_rollup_repairs_old_character_split_test_plan(capsys) -> None:
    session = CodingCliSession(
        turns=[
            CodingOrganismReport.model_validate(
                {
                    "status": "failed",
                    "trace_id": "trace-1",
                    "organism_id": "coding-organism",
                    "organ_id": "coding-build",
                    "task_id": "coding-organ-task:1",
                    "objective": "Verify the site",
                    "candidate_id": "candidate-1",
                    "change_summary": "verification only",
                    "target_files": ["/tmp/example.html"],
                    "test_plan": list("1. Open page\n2. Check navbar styling"),
                    "risks": [],
                    "outputs": {},
                    "handoff_count": 1,
                    "signal_count": 2,
                }
            )
        ]
    )

    _print_session_rollup(session)

    stdout = capsys.readouterr().out
    assert "Session summary: 1 turns, 1 files, 2 validation commands" in stdout
    assert "- 1. Open page" in stdout
    assert "- 2. Check navbar styling" in stdout


def test_save_code_product_session_compacts_report_payload(tmp_path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    paths = resolve_code_product_paths(workspace)
    session = CodingCliSession(workspace_root=str(workspace))
    session.record_turn(
        CodingOrganismReport(
            status="completed",
            trace_id="trace-1",
            organism_id="coding-organism",
            organ_id="coding-build",
            task_id="coding-organ-task:1",
            objective="Fix the header",
            candidate_id="candidate-1",
            change_summary="Updated the navbar styling.",
            target_files=["src/example.py"],
            test_plan=["pytest -q tests/test_cli/test_code.py"],
            risks=[],
            outputs={"candidate_id": "candidate-1", "patch": "large output"},
            handoff_count=1,
            signal_count=2,
            trace_rows=[{"kind": "signal", "signal_type": "done"}],
        )
    )

    save_code_product_session(paths, session)

    payload = json.loads(Path(paths.session).read_text(encoding="utf-8"))
    assert payload["session_format_version"] == CODE_PRODUCT_SESSION_FORMAT_VERSION
    assert payload["runtime_build_id"] == CODE_PRODUCT_RUNTIME_BUILD_ID
    assert "outputs" not in payload["turns"][0]
    assert "trace_rows" not in payload["turns"][0]

    loaded = load_code_product_session(paths)
    assert loaded is not None
    assert loaded.turns[0].outputs == {}
    assert loaded.turns[0].trace_rows == []


def test_load_or_create_session_refreshes_stale_runtime_state(tmp_path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    paths = resolve_code_product_paths(workspace)
    Path(paths.root).mkdir(parents=True, exist_ok=True)
    Path(paths.session).write_text(
        json.dumps(
            {
                "session_format_version": 1,
                "runtime_build_id": "stale-build",
                "session_id": "coding-session-stale",
                "workspace_root": str(workspace),
                "created_at": "2026-04-09T13:37:11+00:00",
                "updated_at": "2026-04-09T13:37:11+00:00",
                "turns": [],
                "conversation": [
                    {
                        "role": "assistant",
                        "text": "Please paste the file contents.",
                        "kind": "message",
                        "created_at": "2026-04-09T13:37:11+00:00",
                    }
                ],
                "pending_clarification": "Which file should I inspect?",
                "orchestrator_state": {"durable_session": {"id": "old-session"}},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    loaded = _load_or_create_session(
        product_paths=paths,
        workspace_root=workspace,
        new_session=False,
        persist_session=True,
    )

    assert loaded.session_format_version == CODE_PRODUCT_SESSION_FORMAT_VERSION
    assert loaded.runtime_build_id == CODE_PRODUCT_RUNTIME_BUILD_ID
    assert loaded.conversation == []
    assert loaded.pending_clarification is None
    assert loaded.orchestrator_state == {}

    persisted = json.loads(Path(paths.session).read_text(encoding="utf-8"))
    assert persisted["runtime_build_id"] == CODE_PRODUCT_RUNTIME_BUILD_ID
    assert persisted["conversation"] == []
    assert persisted["orchestrator_state"] == {}


def test_conversation_context_filters_failed_no_output_reports(tmp_path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    session = CodingCliSession(workspace_root=str(workspace))
    session.record_turn(
        CodingOrganismReport(
            status="completed",
            trace_id="trace-1",
            organism_id="coding-organism",
            organ_id="coding-build",
            task_id="coding-organ-task:1",
            objective="Fix the navbar",
            candidate_id="candidate-1",
            change_summary="Updated the header styles.",
            target_files=["src/example.py"],
            test_plan=["pytest -q"],
            risks=[],
            outputs={"candidate_id": "candidate-1"},
            handoff_count=1,
            signal_count=2,
            trace_rows=[],
        )
    )
    session.record_turn(
        CodingOrganismReport(
            status="failed",
            trace_id="trace-2",
            organism_id="coding-organism",
            organ_id="coding-build",
            task_id="coding-organ-task:2",
            objective="Retry the same fix",
            candidate_id=None,
            change_summary="",
            target_files=[],
            test_plan=[],
            risks=[],
            outputs={},
            handoff_count=1,
            signal_count=2,
            error="did not produce a validated candidate",
            trace_rows=[],
        )
    )

    context = _conversation_context(
        session=session,
        workspace_root=workspace,
        model="gpt-test",
        thinking_mode="auto",
        tool_ids=["file_read"],
        approval_mode="confirm-risky",
        acceptance_criteria=[],
    )

    assert context.facts.latest_report_status == "failed"
    assert context.facts.latest_report_objective == "Retry the same fix"
    assert context.recent_reports == [
        CodingConversationReportSummary(
            status="completed",
            task_id="coding-organ-task:1",
            objective="Fix the navbar",
            candidate_id="candidate-1",
            change_summary="Updated the header styles.",
            target_files=["src/example.py"],
            test_plan=["pytest -q"],
            risks=[],
            error=None,
        )
    ]


def test_conversation_context_marks_benchmark_mode(tmp_path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    session = CodingCliSession(workspace_root=str(workspace))

    context = _conversation_context(
        session=session,
        workspace_root=workspace,
        model="gpt-test",
        thinking_mode="disabled",
        tool_ids=["file_read"],
        approval_mode="auto",
        acceptance_criteria=[],
        benchmark_context={"instance_id": "marshmallow-code__marshmallow-1359"},
    )

    assert context.facts.benchmark_mode is True


def test_project_planner_context_marks_benchmark_mode(tmp_path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    session = CodingCliSession(workspace_root=str(workspace))

    context = _project_planner_context(
        session=session,
        workspace_root=workspace,
        model="gpt-test",
        thinking_mode="disabled",
        tool_ids=["file_read"],
        approval_mode="auto",
        acceptance_criteria=[],
        existing_plan=None,
        benchmark_context={"instance_id": "marshmallow-code__marshmallow-1343"},
    )

    assert context.facts.benchmark_mode is True
