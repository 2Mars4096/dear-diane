from __future__ import annotations

import json

import pytest

import dan.worker.organisms.local_runtime as local_runtime_module
from dan.providers import CompletionResult
from dan.worker.core.interfaces import CompletionRequest
from dan.worker.organisms.local_runtime import (
    LocalOrganismToolRuntime,
    ToolLoopCompletionProvider,
)
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


def _first_event(events: list[dict[str, object]], event_name: str) -> dict[str, object]:
    return next(event for event in events if event.get("event") == event_name)


class _ContextLengthAfterOldWriteProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": kwargs.get("tools"),
            }
        )
        call_index = len(self.calls)
        if call_index == 1:
            tool_call = {
                "id": "call-old-write",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps(
                        {
                            "path": "notes/context-pressure.md",
                            "content": "old-write-payload\n" * 500,
                        }
                    ),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 2:
            assert "old-write-payload" in json.dumps(messages)
            tool_call = {
                "id": "call-latest-list",
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "arguments": json.dumps({"path": "."}),
                },
            }
            return CompletionResult(
                text="",
                model=model,
                tool_calls=[tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )
        if call_index == 3:
            assert "old-write-payload" in json.dumps(messages)
            raise RuntimeError("maximum context length exceeded: too many tokens")

        replay = json.dumps(messages)
        assert "old-write-payload" not in replay
        assert "prompt_replay_compacted" in replay
        assert "call-latest-list" in replay
        return CompletionResult(
            text=json.dumps({"candidate_id": "after-context-length-retry"}),
            model=model,
            raw_assistant_message={
                "role": "assistant",
                "content": json.dumps({"candidate_id": "after-context-length-retry"}),
            },
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


def test_prompt_replay_compaction_preserves_tool_call_structure_under_pressure() -> None:
    old_write_content = "old write body\n" * 500
    old_edit_content = "old edit body\n" * 500
    latest_write_content = "latest write body\n" * 500
    old_assistant_text = "old assistant analysis\n" * 300
    old_shell_stdout = "shell output\n" * 500
    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "Initial task prompt must remain exact."},
        {
            "role": "assistant",
            "content": old_assistant_text,
            "tool_calls": [
                {
                    "id": "call-old-write",
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "arguments": json.dumps(
                            {
                                "path": "notes/old.md",
                                "content": old_write_content,
                            },
                            sort_keys=True,
                        ),
                    },
                },
                {
                    "id": "call-old-edit",
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "arguments": json.dumps(
                            {
                                "path": "notes/old.md",
                                "start_line": 1,
                                "end_line": 2,
                                "content": old_edit_content,
                            },
                            sort_keys=True,
                        ),
                    },
                },
                {
                    "id": "call-old-shell",
                    "type": "function",
                    "function": {
                        "name": "shell_command",
                        "arguments": json.dumps({"command": "pytest -q"}),
                    },
                },
            ],
        },
        {
            "role": "tool",
            "name": "file_write",
            "tool_call_id": "call-old-write",
            "content": json.dumps({"ok": True, "result": {"bytes_written": 10}}),
        },
        {
            "role": "tool",
            "name": "file_edit",
            "tool_call_id": "call-old-edit",
            "content": json.dumps({"ok": True, "result": {"changed": True}}),
        },
        {
            "role": "tool",
            "name": "shell_command",
            "tool_call_id": "call-old-shell",
            "content": json.dumps(
                {"ok": True, "result": {"stdout": old_shell_stdout}},
                sort_keys=True,
            ),
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-middle-list",
                    "type": "function",
                    "function": {
                        "name": "list_directory",
                        "arguments": json.dumps({"path": "."}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "name": "list_directory",
            "tool_call_id": "call-middle-list",
            "content": json.dumps({"ok": True, "result": {"files": ["notes"]}}),
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-latest-write",
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "arguments": json.dumps(
                            {
                                "path": "notes/latest.md",
                                "content": latest_write_content,
                            },
                            sort_keys=True,
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "name": "file_write",
            "tool_call_id": "call-latest-write",
            "content": json.dumps({"ok": True, "result": {"bytes_written": 20}}),
        },
        {"role": "user", "content": "Latest validation feedback must remain exact."},
    ]

    compacted_messages, stats = local_runtime_module._compact_messages_for_provider_prompt(
        messages,
        budget_chars=2_000,
        tool_schema_chars=500,
    )

    assert stats["prompt_context_budget_triggered"] is True
    assert stats["prompt_context_compacted_non_file_tools"] == 1
    assert stats["prompt_context_compacted_tool_call_args"] == 2
    assert stats["prompt_context_compacted_assistant_messages"] == 1
    assert compacted_messages[1]["content"] == "Initial task prompt must remain exact."
    assert compacted_messages[10]["content"] == "Latest validation feedback must remain exact."

    old_tool_calls = compacted_messages[2]["tool_calls"]
    assert old_tool_calls[0]["id"] == "call-old-write"
    assert old_tool_calls[0]["function"]["name"] == "file_write"
    old_write_arguments = json.loads(old_tool_calls[0]["function"]["arguments"])
    assert old_write_arguments["prompt_replay_compacted"] is True
    assert old_write_arguments["retained"]["path"] == "notes/old.md"
    assert old_write_arguments["retained"]["content_chars"] == len(old_write_content)
    assert "old write body" not in old_tool_calls[0]["function"]["arguments"]

    old_edit_arguments = json.loads(old_tool_calls[1]["function"]["arguments"])
    assert old_edit_arguments["prompt_replay_compacted"] is True
    assert old_edit_arguments["retained"]["path"] == "notes/old.md"
    assert old_edit_arguments["retained"]["start_line"] == 1
    assert old_edit_arguments["retained"]["content_chars"] == len(old_edit_content)
    assert "old edit body" not in old_tool_calls[1]["function"]["arguments"]

    old_shell_payload = json.loads(str(compacted_messages[5]["content"]))
    assert old_shell_payload["prompt_context_compacted"] is True
    assert old_shell_payload["result"]["stdout"] != old_shell_stdout
    assert compacted_messages[5]["tool_call_id"] == "call-old-shell"

    assert compacted_messages[2]["content"] != old_assistant_text
    assert compacted_messages[8]["tool_calls"][0]["id"] == "call-latest-write"
    assert "latest write body" in compacted_messages[8]["tool_calls"][0]["function"]["arguments"]
    assert messages[2]["content"] == old_assistant_text
    assert "old write body" in messages[2]["tool_calls"][0]["function"]["arguments"]
    assert "old edit body" in messages[2]["tool_calls"][1]["function"]["arguments"]


@pytest.mark.asyncio
async def test_super_dan_context_length_retry_uses_emergency_prompt_compaction(tmp_path) -> None:
    events: list[dict[str, object]] = []
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_write", "list_directory"],
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    provider_impl = _ContextLengthAfterOldWriteProvider()
    provider = ToolLoopCompletionProvider(
        provider=provider_impl,
        tool_runtime=runtime,
        default_model="gpt-test",
        max_rounds=None,
        max_tool_calls=4,
        event_callback=events.append,
    )

    response = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Return compact JSON.",
            user_prompt="Create a note, inspect the workspace, then finish.",
            metadata={
                "worker_id": "super-dan-live-general.worker",
                "tool_budget_profile": "super_dan_live",
            },
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "list_directory",
                        "description": "List files.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                        },
                    },
                },
            ],
        )
    )

    assert json.loads(response.text)["candidate_id"] == "after-context-length-retry"
    assert len(provider_impl.calls) == 4
    retry_event = _first_event(events, "model.context_length_retry")
    assert retry_event["retry_attempt"] == 1
    requested_events = [
        event for event in events if event.get("event") == "model.requested"
    ]
    assert len(requested_events) == 4
    assert requested_events[-2]["prompt_context_emergency_compaction"] is False
    assert requested_events[-1]["prompt_context_emergency_compaction"] is True
    assert requested_events[-1]["prompt_context_compacted_tool_call_args"] >= 1
    assert (
        requested_events[-1]["prompt_context_final_chars"]
        < requested_events[-2]["prompt_context_final_chars"]
    )
