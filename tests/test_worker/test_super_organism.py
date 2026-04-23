from __future__ import annotations

import pytest

import dan.worker.organisms.local_runtime as local_runtime_module
from dan.worker.organisms.super_organism import (
    DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP,
    DEFAULT_SUPER_ORGANISM_CELL_COUNT,
    SuperOrgan,
    SuperOrganismScenario,
    build_super_organism_cells,
    resolve_super_organism_distribution,
    resolve_super_organism_scenario,
    run_super_organism_demo,
)


def test_default_distribution_builds_20_logical_cells() -> None:
    distribution = resolve_super_organism_distribution(DEFAULT_SUPER_ORGANISM_CELL_COUNT)

    assert distribution == {
        "brain": 1,
        "scout": 5,
        "claim": 4,
        "immune": 4,
        "memory": 2,
        "experiment": 2,
        "synthesis": 2,
    }

    cells = build_super_organism_cells()

    assert len(cells) == 20
    assert cells[0].cell_id == "brain-001"
    assert cells[-1].cell_id == "synthesis-002"
    assert cells[0].organ == SuperOrgan.BRAIN


def test_100_cell_showcase_distribution_is_still_available() -> None:
    distribution = resolve_super_organism_distribution(100)

    assert distribution == {
        "brain": 6,
        "scout": 24,
        "claim": 20,
        "immune": 18,
        "memory": 12,
        "experiment": 10,
        "synthesis": 10,
    }


def test_scaled_distribution_keeps_all_organs_present() -> None:
    distribution = resolve_super_organism_distribution(14)

    assert sum(distribution.values()) == 14
    assert set(distribution) == {
        "brain",
        "scout",
        "claim",
        "immune",
        "memory",
        "experiment",
        "synthesis",
    }
    assert all(value >= 1 for value in distribution.values())


def test_too_few_cells_is_rejected() -> None:
    with pytest.raises(ValueError, match="cell_count must be at least"):
        resolve_super_organism_distribution(6)


def test_super_organism_report_shows_organized_synergy() -> None:
    report = run_super_organism_demo("LangGraph")

    assert report.status == "completed"
    assert report.mode == "deterministic_demo"
    assert report.scenario == SuperOrganismScenario.UNIVERSAL_AGENT
    assert report.cell_count == 20
    assert report.active_cell_cap == DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP
    assert report.max_active_observed <= DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP
    assert report.organ_counts["brain"] == 1
    assert report.organ_counts["scout"] == 5
    assert report.signal_counts["resource_request"] == 1
    assert report.signal_counts["reallocation"] == 2
    assert "brain_reallocation" in report.stage_sequence
    assert report.claim_graph == []
    assert len(report.delivery_plan) == 8
    assert report.shared_board is not None
    assert len(report.coordination_tickets) == 8
    assert len(report.handoff_packets) == 7
    assert report.final_audit is not None
    assert report.final_audit.status == "continue"
    assert report.final_audit.satisfied is False
    assert report.shared_board.waiting_ticket_ids == [
        "ticket-006",
        "ticket-007",
        "ticket-008",
    ]
    assert report.shared_board.pending_packet_ids == ["packet-006", "packet-007"]
    assert len(report.reallocation_decisions) == 2
    assert sum(1 for cell in report.cells if cell.status == "retired") == 2
    assert "LangGraph" in report.final_memo
    assert "Board tickets: 8." in report.final_memo
    assert "deterministic coordination demo" in report.caveat or "deterministic" in report.caveat


def test_default_super_organism_is_universal_agent_showcase() -> None:
    report = run_super_organism_demo()

    assert report.scenario == SuperOrganismScenario.UNIVERSAL_AGENT
    assert report.score_label == "Execution Readiness Score"
    assert report.final_verdict == "universal-agent execution contract ready"
    assert report.claim_graph == []
    assert len(report.delivery_plan) == 8
    assert "operator objective" in report.target


def test_scenario_resolver_compatibility_always_returns_universal_agent() -> None:
    assert resolve_super_organism_scenario("") == SuperOrganismScenario.UNIVERSAL_AGENT
    assert (
        resolve_super_organism_scenario(
            "please build our product website with cool animation dynamic effects"
        )
        == SuperOrganismScenario.UNIVERSAL_AGENT
    )
    assert resolve_super_organism_scenario("LangGraph") == SuperOrganismScenario.UNIVERSAL_AGENT
    assert (
        resolve_super_organism_scenario("audit LangGraph benchmark claims")
        == SuperOrganismScenario.UNIVERSAL_AGENT
    )
    assert (
        resolve_super_organism_scenario("LangGraph", scenario="universal-agent")
        == SuperOrganismScenario.UNIVERSAL_AGENT
    )


def test_universal_agent_report_uses_objective_contract_not_claim_audit() -> None:
    target = "please build our product website. make it cool, with cool animation dynamic effects"

    report = run_super_organism_demo(target)

    assert report.scenario == SuperOrganismScenario.UNIVERSAL_AGENT
    assert report.score_label == "Execution Readiness Score"
    assert report.final_verdict == "universal-agent execution contract ready"
    assert report.claim_graph == []
    assert len(report.delivery_plan) == 8
    assert report.signal_counts["resource_request"] == 1
    assert report.signal_counts["reallocation"] == 2
    assert "execution_probe" in report.stage_sequence
    assert any(node.title == "Native execution lane" for node in report.delivery_plan)
    assert any(node.status == "live_build_required" for node in report.delivery_plan)
    assert report.coordination_tickets[5].ticket_id == "ticket-006"
    assert report.coordination_tickets[5].status == "awaiting_live_execution"
    assert report.handoff_packets[-1].status == "pending"
    assert report.final_audit is not None
    assert report.final_audit.blocker_ticket_ids == [
        "ticket-006",
        "ticket-007",
        "ticket-008",
    ]
    assert "production-ready for complex agent orchestration" not in report.final_memo
    assert "accepts the objective" in report.final_memo


def test_super_organism_normalizes_pasted_multiline_objective() -> None:
    report = run_super_organism_demo(
        "please build our product\n"
        "  website. make it cool, with cool animation dynamic\n"
        "  effects"
    )

    assert report.target == (
        "please build our product website. make it cool, "
        "with cool animation dynamic effects"
    )
    assert "\n" not in report.final_memo


def test_active_cell_cap_controls_scheduler_waves() -> None:
    report = run_super_organism_demo("CrewAI", cell_count=100, active_cell_cap=7)

    assert report.active_cell_cap == 7
    assert report.max_active_observed <= 7
    assert any(len(wave.cell_ids) == 7 for wave in report.activity_waves)
    assert any(signal.phase == "contract_immune_check" for signal in report.board_signals)


def test_super_organism_shared_board_accounts_for_all_cells() -> None:
    report = run_super_organism_demo("build a cool website for this product")

    assert report.shared_board is not None
    accounted = set(report.shared_board.reserve_cell_ids)
    for ticket in report.coordination_tickets:
        accounted.update(ticket.cell_ids)

    assert accounted == {cell.cell_id for cell in report.cells}
    assert any(ticket.owner_cell_id == "brain-001" for ticket in report.coordination_tickets[:2])
    assert report.shared_board.reserve_cell_ids == ["scout-005"]


def test_runtime_downshift_detects_truncated_existing_file_overwrite(tmp_path) -> None:
    (tmp_path / "module.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    raw_arguments = (
        '{"path":"module.py","content":"def alpha():\\n'
        '    return 10\\n'
    )
    raw_call = {
        "function": {
            "name": "file_write",
            "arguments": raw_arguments,
        }
    }

    downshift = local_runtime_module._file_write_invalid_large_overwrite_downshift(
        raw_call,
        arguments={"raw_arguments": raw_arguments},
        finish_reason="length",
        workspace_root=tmp_path,
        file_edit_available=True,
    )

    assert downshift == ("module.py", "model_output_truncated")
    message = local_runtime_module._file_write_incremental_edit_required_message(
        "module.py",
        "model_output_truncated",
    )
    assert "`file_write` to existing file `module.py` is blocked" in message
    assert "Downshift now: use `file_edit`" in message


def test_super_dan_soft_budget_extends_only_for_real_progress() -> None:
    progress = {
        "distinct_read_paths": 5,
        "mutation_paths": 0,
        "verification_commands": 0,
        "discovery_tools": 5,
        "shell_commands": 0,
    }

    tool_limit = local_runtime_module._soft_budget_limit(
        "coding_prewrite",
        limit_kind="tool_calls",
        hard_limit=24,
        progress=progress,
    )
    round_limit = local_runtime_module._soft_budget_limit(
        "coding_prewrite",
        limit_kind="rounds",
        hard_limit=8,
        progress=progress,
    )

    assert tool_limit == 10
    assert round_limit == 5
