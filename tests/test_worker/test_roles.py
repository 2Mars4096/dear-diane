from __future__ import annotations

import pytest

from dan.worker import gate, llm_agent, manager, observer, reviewer, router, script, tool_runner, validator
from dan.worker.executor import ExecutionMode, WorkerExecutor
from dan.worker.model import Worker, WorkerAuthority


def _modes(worker: Worker) -> list[ExecutionMode]:
    executor = WorkerExecutor()
    effective = executor._resolve_effective_config(worker, object())  # type: ignore[arg-type]
    return executor._detect_modes(worker, effective)


def test_llm_agent_shortcut_builds_standard_worker() -> None:
    worker = llm_agent(
        "draft",
        model="test-model",
        persona="Write a concise draft.",
        llm={"prompt_template": "Draft {input}", "temperature": 0.1},
    )

    assert isinstance(worker, Worker)
    assert worker.role == "llm_agent"
    assert worker.model == "test-model"
    assert worker.persona == "Write a concise draft."
    assert worker.llm_hints is not None
    assert worker.llm_hints.prompt_template == "Draft {input}"
    assert _modes(worker) == [ExecutionMode.LLM]


def test_tool_runner_shortcut_builds_direct_tool_worker() -> None:
    worker = tool_runner("fetch", tool_ids=["file_read"])

    assert worker.role == "tool_runner"
    assert worker.tool_ids == ["file_read"]
    assert _modes(worker) == [ExecutionMode.TOOL]


def test_script_shortcut_builds_code_worker() -> None:
    worker = script("normalize", code="result = input.strip()", language="python")

    assert worker.role == "script"
    assert worker.code == "result = input.strip()"
    assert worker.language == "python"
    assert _modes(worker) == [ExecutionMode.SCRIPT]


def test_reviewer_shortcut_sets_review_defaults() -> None:
    worker = reviewer("review", model="test-model")

    assert worker.role == "reviewer"
    assert worker.model == "test-model"
    assert worker.instruction == "Review the provided work carefully."
    assert _modes(worker) == [ExecutionMode.LLM]


def test_manager_shortcut_sets_lead_authority_and_subworkers() -> None:
    worker = manager(
        "lead",
        model="test-model",
        sub_workers={"research": "research_graph"},
    )

    assert worker.role == "manager"
    assert worker.authority == WorkerAuthority.LEAD
    assert worker.sub_workers == {"research": "research_graph"}
    assert _modes(worker) == [ExecutionMode.ORCHESTRATE, ExecutionMode.LLM]


def test_router_shortcut_seeds_route_ports() -> None:
    worker = router("route", model="test-model")

    assert worker.role == "router"
    assert worker.model == "test-model"
    assert [port.name for port in worker.output_ports] == ["route", "result"]
    assert _modes(worker) == [ExecutionMode.ROUTER]


def test_gate_shortcut_builds_control_flow_worker() -> None:
    worker = gate("route", condition="score > 0.5", gate_mode="if_else")

    assert worker.role == "gate"
    assert worker.control_flow is not None
    assert worker.control_flow.condition == "score > 0.5"
    assert worker.control_flow.gate_mode == "if_else"
    assert [port.name for port in worker.output_ports] == ["true", "false"]
    assert _modes(worker) == [ExecutionMode.GATE]


def test_validator_shortcut_builds_validation_worker() -> None:
    worker = validator(
        "validate",
        rules=[{"rule_type": "required_keys", "config": {"keys": ["summary"]}}],
        on_failure="halt",
        strict_mode=True,
    )

    assert worker.role == "validator"
    assert worker.metadata["validation_rules"][0]["rule_type"] == "required_keys"
    assert worker.metadata["validator_on_failure"] == "halt"
    assert worker.metadata["validator_strict_mode"] is True
    assert [port.name for port in worker.input_ports] == ["data"]
    assert [port.name for port in worker.output_ports] == ["valid", "invalid"]
    assert _modes(worker) == [ExecutionMode.VALIDATE]


def test_observer_shortcut_includes_human_input_tool() -> None:
    worker = observer("observe")

    assert worker.role == "observer"
    assert "human_input" in worker.tool_ids
    assert _modes(worker) == [ExecutionMode.TOOL]


def test_all_role_factories_produce_detectable_execution_modes() -> None:
    workers = [
        llm_agent(
            "draft",
            model="test-model",
            llm={"prompt_template": "Draft {input}"},
        ),
        tool_runner("fetch", tool_ids=["file_read"]),
        script("normalize", code="result = input"),
        reviewer("review", model="test-model"),
        manager("lead", model="test-model", sub_workers={"research": "research_graph"}),
        router("route", model="test-model"),
        gate("route_gate", condition="score > 0.5"),
        validator("validate", rules=[{"rule_type": "required_keys", "config": {"keys": ["summary"]}}]),
        observer("observe"),
    ]

    for worker in workers:
        modes = _modes(worker)
        assert modes, f"{worker.role} should resolve at least one execution mode"


def test_role_helpers_reject_conflicting_llm_aliases() -> None:
    with pytest.raises(ValueError, match="either `llm` or `llm_hints`"):
        llm_agent(
            "draft",
            model="test-model",
            llm={"prompt_template": "Draft {input}"},
            llm_hints={"prompt_template": "Other"},
        )
