from __future__ import annotations

import asyncio
import io
import json
import tomllib
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


def test_unified_cli_registers_super_tui_without_changing_super_organism() -> None:
    assert _SUBCOMMANDS["super-tui"] == ("dan.cli.super_tui", "main")
    assert _SUBCOMMANDS["super-organism"] == ("dan.cli.super_organism", "main")

    pyproject = tomllib.loads(
        (Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )
    scripts = pyproject["project"]["scripts"]
    assert scripts["dan-super-tui"] == "dan.cli.super_tui:main"
    assert scripts["dan-super-organism"] == "dan.cli.super_organism:main"


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
    assert "Super DAN TUI" in stdout
    assert "Status: completed" in stdout
    assert "Phase: done" in stdout
    assert "Run: super-dan-live:1" in stdout
    assert "Objective: build dashboard" in stdout
    assert "Workspace: /tmp/ws" in stdout
    assert "Model: fake-live-model" in stdout
    assert "Validation: passed (0.92)" in stdout
    assert "Changed:" in stdout
    assert "- website/index.html" in stdout
    assert "Event Log: .dan-super/runs/turn-01/events.jsonl" in stdout


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
    assert "Validation: failed (0.41)" in output
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

    assert "Super DAN TUI" in text
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
        assert f"Mode: {lane} - test rationale" in plain
        assert "Activity:" in plain
        assert "Result:" in plain

        console = Console(file=io.StringIO(), force_terminal=False, width=120, record=True)
        console.print(state.rich_renderable())
        rich_text = console.export_text()
        assert f"Mode: {lane} - test rationale" in rich_text
        assert "Activity:" in rich_text
        assert "Result:" in rich_text


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
    assert "You asked: copy source files" in joined
    assert "Workspace context checked (2 items); latest:" in joined
    assert joined.count("Workspace context checked") >= 1
    assert "Terminal command started: copying files." in joined
    assert "Terminal command finished (exit 0):" in joined


def test_super_tui_conversation_footer_shows_elapsed_working_time(monkeypatch) -> None:
    state = super_tui.SuperTuiState(status="running", phase="model")
    state.started_at_monotonic = 10.0
    state.timeline.append("I'm working on it.")
    monkeypatch.setattr(super_tui.time, "monotonic", lambda: 72.0)

    assert state.recent_event_lines()[-1] == "Working: 1m 02s"

    state.status = "completed"
    state.ended_at_monotonic = 75.0
    assert state.recent_event_lines()[-1] == "Elapsed: 1m 05s"


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

    output = "\n".join(state.recent_event_lines())
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

    def fake_prompt(prompt, *, commands, skills):
        del commands, skills
        print(prompt, end="")
        raise EOFError

    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace: [])
    monkeypatch.setattr(super_tui, "_read_interactive_line", fake_prompt)

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Conversation" in stdout
    assert "user: first task" in stdout
    assert "assistant: Run completed; trace .dan-super/runs/turn-01/events.jsonl." in stdout
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
    assert "Run completed" in entries[0].text
    assert "website/index.html" in entries[0].text
    assert "turn-09/events.jsonl" in entries[0].text


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
    assert "Super DAN TUI" in stdout
    assert "Status: completed" in stdout


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
    assert "/append" in stdout
    assert "/continue" in stdout
    assert "/pause" in stdout
    assert "/cancel" in stdout
    assert "/status" in stdout
    assert "/skills" in stdout
    assert "/help" in stdout
    assert "Typing / opens command suggestions" in stdout
    assert "require Plan" in stdout
    assert "57-10" in stdout


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
    ]

    slash = super_tui._completion_candidates("/", skills=skills)
    assert "/status" in {item.value for item in slash}
    assert "/skills" in {item.value for item in slash}
    assert "/cancel" in {item.value for item in slash}

    command_prefix = super_tui._completion_candidates("/sk", skills=skills)
    assert [item.value for item in command_prefix] == ["/skills"]

    bare_dollar = super_tui._completion_candidates("$", skills=skills)
    assert [item.value for item in bare_dollar] == ["$idea-cart", "$frontend-design"]
    assert bare_dollar[0].start_position == -1

    dollar = super_tui._completion_candidates("$ide", skills=skills)
    assert [item.value for item in dollar] == ["$idea-cart"]
    assert dollar[0].start_position == -4
    assert "Capture important ideas" in dollar[0].meta
    assert super_tui._completion_candidates("$5", skills=skills) == []


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


def test_super_tui_prompt_toolkit_session_builds_with_installed_version() -> None:
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
    )

    assert isinstance(session, PromptSession)


def test_super_tui_run_command_parser_is_honest_for_direct_local_tui() -> None:
    append = super_tui._parse_tui_run_command("/append use the blue theme")
    assert append is not None
    assert append.command == "append"
    assert append.payload == "use the blue theme"
    assert "server-backed V2 active runs" in append.message

    cancel = super_tui._parse_tui_run_command("/cancel")
    assert cancel is not None
    assert cancel.command == "cancel"
    assert "not wired into direct local Super DAN runs yet" in cancel.message

    assert super_tui._parse_tui_run_command("/status") is None


def test_super_tui_skill_list_command_renders_available_mentions(capsys, monkeypatch) -> None:
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
    prompts = iter(["/skills ide"])

    def fake_prompt(prompt, *, commands, skills):
        del commands, skills
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

    def fake_prompt(prompt, *, commands, skills):
        del commands, skills
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
    prompts = iter(["$idea-cart capture the open questions"])
    observed: dict[str, object] = {}

    def fake_prompt(prompt, *, commands, skills):
        del commands, skills
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
    assert "Super DAN TUI" in stdout
    assert "objective: build a dashboard" in stdout
    assert "[tui] You asked: build a dashboard" in stdout
    assert "File changed: website/index.html" in stdout
    assert "Validation: passed (0.90)" in stdout
    assert "Elapsed:" in stdout
    assert "Event Log:" in stdout
    assert "[run] started:" not in stdout


def test_super_tui_direct_target_routes_read_only_without_live_runner(capsys, monkeypatch) -> None:
    observed = {}

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
    assert "Mode: complex read-only" in stdout
    assert "Result:" in stdout


def test_super_tui_intent_gate_classifies_four_lanes() -> None:
    assert super_tui._classify_tui_intent("show me the todo list").lane == "simple read-only"
    assert super_tui._classify_tui_intent("summarize docs/todo.md").lane == "complex read-only"
    assert super_tui._classify_tui_intent("copy docs/source.md to docs/copy.md").lane == "simple write"
    assert super_tui._classify_tui_intent("update the todo list").permission == "write"
    assert super_tui._classify_tui_intent("fix the bug in todo handling").lane == "complex write"
    assert (
        super_tui._classify_tui_intent(
            "show docs/todo.md",
            selected_skills=["idea-cart"],
        ).permission
        == "read-only"
    )
    assert super_tui._classify_tui_intent("$idea-cart").needs_clarification is True


def test_super_tui_core_progress_intent_routes_three_lanes() -> None:
    assert super_tui.classify_agent_turn_intent("tell me current progress?").lane == "narrator read-only"
    assert super_tui.classify_agent_turn_intent("what is happening right now").lane == "narrator read-only"
    assert super_tui.classify_agent_turn_intent("/progress").lane == "narrator read-only"
    assert super_tui.classify_agent_turn_intent("/last").lane == "narrator read-only"
    assert super_tui.classify_agent_turn_intent("summarize docs/todo.md").lane == "executor read-only"
    assert super_tui.classify_agent_turn_intent("show me the todo list").lane == "executor read-only"
    assert super_tui.classify_agent_turn_intent("update the todo list").lane == "executor write"
    assert super_tui.classify_agent_turn_intent("continue building this").lane == "executor write"
    assert super_tui.classify_agent_turn_intent("blue theme").needs_clarification is True


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
    assert "Mode: simple read-only" in stdout
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
    assert "Mode: complex read-only" in stdout
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
    assert "Mode: complex read-only" in stdout
    assert "Search completed: 2 match(es) for needle value." in stdout
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
    assert "Mode: complex read-only" in stdout
    assert "Read-only model loop started." in stdout
    assert "Workspace context checked" in stdout
    assert "Summary: Alpha finding from docs/info.md." in stdout
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
    assert "Mode: narrator read-only" in stdout
    assert "Answer:" in stdout
    assert "Status: complete" in stdout
    assert "Activity:" not in stdout
    assert "website/index.html" in stdout
    assert "Validation: passed (0.92)." in stdout
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
            "tell me current progress?",
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
    assert "Mode: simple write" in stdout
    assert "Simple write route selected." in stdout
    assert "Copied: docs/source.md -> docs/copy.md" in stdout
    assert "Changed:" in stdout
    assert "- docs/copy.md" in stdout


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

    parser = super_tui.build_parser()
    args = parser.parse_args(["--plain"])
    super_tui._prepare_args(args, [])
    args.target = "update the todo list"

    exit_code = super_tui._dispatch_tui_turn(args, parser, force_live=True)

    assert exit_code == 0
    assert observed == {
        "target": "update the todo list",
        "force_live": True,
        "lane": "complex write",
    }


def test_super_tui_ambiguous_input_asks_clarification(capsys, monkeypatch) -> None:
    def fail_run(*args, **kwargs):
        raise AssertionError("ambiguous input must not start a run")

    monkeypatch.setattr(super_tui, "_run_tui_turn", fail_run)

    exit_code = super_tui.main(["blue theme", "--plain"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Mode: clarification needed" in stdout
    assert "Should this be a read-only answer" in stdout


def test_super_tui_interactive_prompt_starts_without_idle_panel(capsys, monkeypatch) -> None:
    parser = super_tui.build_parser()
    args = parser.parse_args(["--workspace", "/tmp/ws", "--plain"])
    super_tui._prepare_args(args, [])

    def fake_prompt(prompt, *, commands, skills):
        del commands, skills
        print(prompt, end="")
        raise EOFError

    monkeypatch.setattr(super_tui, "_load_tui_skill_suggestions", lambda workspace: [])
    monkeypatch.setattr(super_tui, "_read_interactive_line", fake_prompt)

    exit_code = super_tui._interactive_loop(args, parser)

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "<idle-panel>" not in stdout
    assert "Message" in stdout
    assert "Type your objective in the prompt below." in stdout
    assert stdout.index("Message") < stdout.index("super-tui>")


def test_super_organism_parser_identity_is_unchanged() -> None:
    parser = super_organism.build_parser()

    assert parser.prog == "dan-super-organism"
    assert _SUBCOMMANDS["super-organism"] == ("dan.cli.super_organism", "main")
