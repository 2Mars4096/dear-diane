from __future__ import annotations

from dan.cli.dispatch import select_orchestrator
from dan.cli.universal_progress import universal_progress_delta


def test_select_orchestrator_for_code_command() -> None:
    choice = select_orchestrator("implement a feature", {"command": "code"})

    assert choice.orchestrator_id == "dan-code"
    assert choice.brief_composer == "templates.coding_brief"
    assert choice.tool_policy["mode"] == "workspace-mutation"


def test_select_orchestrator_for_research_and_reader_commands() -> None:
    research = select_orchestrator("compare sources", {"command": "research"})
    reader = select_orchestrator("summarize this pdf", {"command": "reader"})

    assert research.orchestrator_id == "dan-research"
    assert reader.orchestrator_id == "dan-reader"
    assert reader.tool_policy["mode"] == "read-only"


def test_select_orchestrator_for_reference_organism() -> None:
    choice = select_orchestrator("run the reference demo", {"command": "organism"})

    assert choice.orchestrator_id == "dan-reference-organism"
    assert choice.organism_plan_template == "reference-project-execution"


def test_select_orchestrator_for_super_organism_website_and_generic_build() -> None:
    website = select_orchestrator("build a website with HTML and CSS", {"command": "super-organism"})
    generic = select_orchestrator("implement a small feature", {"command": "super-organism"})

    assert website.orchestrator_id == "super-dan-live-website"
    assert "website" in website.matched_cues
    assert website.artifact_policy["required_files"] == ["index.html", "styles.css", "app.js", "README.md"]
    assert generic.orchestrator_id == "super-dan-live-coding"
    assert generic.tool_policy["profile"] == "generic"


def test_select_orchestrator_respects_super_organism_execution_family() -> None:
    research = select_orchestrator(
        "compare market evidence",
        {"command": "super-organism", "execution_family": "research"},
    )
    coding = select_orchestrator(
        "fix the failing test",
        {"command": "super-organism", "execution_family": "code"},
    )

    assert research.orchestrator_id == "super-dan-showcase"
    assert research.acceptance_policy["requires_live_artifact"] is False
    assert coding.orchestrator_id == "super-dan-live-coding"


def test_select_orchestrator_keeps_non_code_super_organism_build_cues_read_only() -> None:
    choice = select_orchestrator(
        "build a market evidence map",
        {"command": "super-organism", "execution_family": "research"},
    )

    assert choice.orchestrator_id == "super-dan-showcase"
    assert choice.tool_policy["mode"] == "read-only"
    assert choice.acceptance_policy["requires_live_artifact"] is False


def test_select_orchestrator_fallback_website_cues_are_deterministic() -> None:
    first = select_orchestrator("please create a landing page")
    second = select_orchestrator("please create a landing page")

    assert first == second
    assert first.rationale.endswith("without an LLM classifier")


def test_universal_progress_delta_reads_event_stream_rows() -> None:
    delta = universal_progress_delta(
        {
            "event": "organism.run_state.delta",
            "run_id": "run-1",
            "plan_id": "plan-1",
            "state_version": 7,
            "payload": {
                "run_state": {
                    "status": "running",
                    "running_task_ids": ["worker-a"],
                    "capacity_available": 2,
                }
            },
        }
    )
    semantic = universal_progress_delta(
        {
            "event": "organism.status.semantic",
            "run_id": "run-1",
            "plan_id": "plan-1",
            "task_id": "worker-a",
            "state_version": 8,
            "payload": {
                "current_focus": "editing files",
                "risk_flags": ["large-diff"],
                "artifact_refs": ["artifact:patch"],
            },
        }
    )

    assert delta == {
        "kind": "run_state",
        "run_id": "run-1",
        "plan_id": "plan-1",
        "state_version": 7,
        "run_state": {
            "status": "running",
            "running_task_ids": ["worker-a"],
            "capacity_available": 2,
        },
    }
    assert semantic["kind"] == "semantic_status"
    assert semantic["current_focus"] == "editing files"
    assert semantic["artifact_refs"] == ["artifact:patch"]
