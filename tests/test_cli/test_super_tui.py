from __future__ import annotations

import io
import json
import tomllib
from pathlib import Path

import pytest

from dan.cli.main import _SUBCOMMANDS
from dan.cli import super_tui
from dan.cli import super_organism
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
    assert "Repair started: validation failed" in "\n".join(state.recent)
    assert "Run failed" in "\n".join(state.recent)


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

    assert line == "Queue update: validation has depth 1"
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
    assert "[tui] Started run super-dan-live:1: build a dashboard" in stdout
    assert "Tool completed: file_write - wrote index.html" in stdout
    assert "Validation: passed (0.90)" in stdout
    assert "Event Log:" in stdout
    assert "[run] started:" not in stdout


def test_super_tui_direct_target_does_not_force_live_without_flag(monkeypatch) -> None:
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
    assert observed == {"live": False, "target": "explain the workspace"}


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
