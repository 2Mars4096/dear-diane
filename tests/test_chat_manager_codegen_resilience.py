"""Regression tests for Phase 33 codegen resilience branches in ChatManager."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import dan.meta.diagnosis as diagnosis_module
import dan.meta.graph_quality as graph_quality_module
import dan.meta.planner as planner_module
import dan.sandbox.runner as sandbox_runner_module
import dan.server.chat_manager as chat_manager_module
from dan.builder import workflow
from dan.meta.planner import CodegenResult, ValidationResult
from dan.providers import CompletionResult
from dan.providers.registry import ProviderRegistry
from dan.server.agent_runtime.workflow_generation_helpers import (
    exec_deterministic_builder_code,
    extract_code_from_response,
    parse_intent_from_result,
)
from dan.server.chat_manager import ChatManager


class SequenceProvider:
    """Return queued responses from ``complete()`` in call order."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
        self.requests.append(kwargs)
        if not self._responses:
            raise AssertionError("Unexpected provider.complete() call")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def stream(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("provider.stream() not expected in these tests")


async def _fast_sleep(_seconds: float) -> None:
    return None


def _make_manager(responses: list[Any]) -> ChatManager:
    registry = ProviderRegistry()
    registry.register("default", SequenceProvider(responses))
    return ChatManager(registry, graph_store=SimpleNamespace())


def _validation_success() -> ValidationResult:
    return ValidationResult(success=True, graph=SimpleNamespace())


def _default_provider(manager: ChatManager) -> SequenceProvider:
    provider = manager._providers.get("default")
    assert isinstance(provider, SequenceProvider)
    return provider


def test_parse_intent_from_result_reads_tool_call_arguments() -> None:
    result = CompletionResult(
        text="",
        tool_calls=[
            {
                "function": {
                    "name": "emit_workflow_intent",
                    "arguments": (
                        '{"goal":"Build a draft workflow","stages":[{"name":"draft"}]}'
                    ),
                }
            }
        ],
    )

    intent = parse_intent_from_result(result)

    assert intent is not None
    assert intent.goal == "Build a draft workflow"
    assert len(intent.stages) == 1
    assert intent.stages[0].name == "draft"


def test_parse_intent_from_result_reads_fenced_json() -> None:
    result = CompletionResult(
        text=(
            "Here is the intent\n"
            "```json\n"
            '{"goal":"Build a draft workflow","stages":[{"name":"draft"}]}\n'
            "```"
        ),
    )

    intent = parse_intent_from_result(result)

    assert intent is not None
    assert intent.goal == "Build a draft workflow"
    assert intent.stages[0].name == "draft"


def test_extract_code_from_response_strips_python_fences() -> None:
    assert (
        extract_code_from_response("```python\ngraph = {'nodes': [], 'edges': []}\n```")
        == "graph = {'nodes': [], 'edges': []}"
    )


def test_exec_deterministic_builder_code_returns_graph_dict() -> None:
    graph_dict = exec_deterministic_builder_code(
        "from dan.models.graph import Graph\n"
        "graph = Graph(nodes=[], edges=[])\n"
    )

    assert graph_dict is not None
    assert graph_dict["nodes"] == []
    assert graph_dict["edges"] == []


def _load_workflow_acceptance_helper() -> Any:
    module = pytest.importorskip(
        "dan.server.agent_runtime.workflow_generation_acceptance"
    )
    for name in (
        "accept_candidate_graph",
        "accept_generated_graph",
        "evaluate_candidate_graph",
    ):
        helper = getattr(module, name, None)
        if callable(helper):
            return helper
    pytest.skip("workflow_generation_acceptance helper entrypoint not available")


def _call_workflow_acceptance_helper(
    helper: Any,
    *,
    graph_dict: dict[str, Any],
    validation_result: Any,
    quality_report: Any,
    quality_threshold: int = 50,
) -> Any:
    emitted_events: list[Any] = []

    def _quality_error_for_graph(_graph: dict[str, Any]) -> Any | None:
        if quality_report.overall_score >= quality_threshold:
            return None
        return SimpleNamespace(
            error_type=SimpleNamespace(value="quality_error"),
            message=f"Quality score {quality_report.overall_score} below threshold {quality_threshold}",
        )

    return helper(
        graph_dict,
        validate_graph=lambda _graph: validation_result,
        build_validation_event=lambda validation: SimpleNamespace(
            success=getattr(validation, "success", False),
        ),
        emit_event=emitted_events.append,
        quality_error_for_graph=_quality_error_for_graph,
        fit_check=lambda _graph: None,
        record_gen_outcome=lambda *args, **kwargs: None,
        success_method="codegen",
        failure_method="codegen",
        failure_fix_needed=True,
        pattern="wf-test",
    )


def test_workflow_generation_acceptance_helper_accepts_valid_graph() -> None:
    helper = _load_workflow_acceptance_helper()
    graph_dict = {"nodes": [{"id": "n1"}], "edges": []}
    validation_result = SimpleNamespace(
        success=True,
        run_ready=True,
        errors=[],
        graph=SimpleNamespace(model_dump=lambda mode="json": graph_dict),
        contract_report=None,
    )
    quality_report = SimpleNamespace(
        overall_score=95,
        concerns=[],
    )

    result = _call_workflow_acceptance_helper(
        helper,
        graph_dict=graph_dict,
        validation_result=validation_result,
        quality_report=quality_report,
    )

    assert result.accepted_graph == graph_dict
    assert result.errors == ()


def test_workflow_generation_acceptance_helper_rejects_low_quality_graph() -> None:
    helper = _load_workflow_acceptance_helper()
    graph_dict = {"nodes": [{"id": "n1"}], "edges": []}
    validation_result = SimpleNamespace(
        success=True,
        run_ready=True,
        errors=[],
        graph=SimpleNamespace(model_dump=lambda mode="json": graph_dict),
        contract_report=None,
    )
    quality_report = SimpleNamespace(
        overall_score=10,
        concerns=["underspecified graph"],
    )

    result = _call_workflow_acceptance_helper(
        helper,
        graph_dict=graph_dict,
        validation_result=validation_result,
        quality_report=quality_report,
        quality_threshold=50,
    )

    assert result.accepted_graph is None
    assert result.errors
    assert result.errors[0].message == "Quality score 10 below threshold 50"


def test_workflow_generation_acceptance_helper_reports_validation_failure() -> None:
    helper = _load_workflow_acceptance_helper()
    graph_dict = {"nodes": [], "edges": []}
    validation_result = SimpleNamespace(
        success=False,
        run_ready=False,
        errors=[SimpleNamespace(message="missing required node")],
        graph=None,
        contract_report=None,
    )
    quality_report = SimpleNamespace(
        overall_score=0,
        concerns=[],
    )

    result = _call_workflow_acceptance_helper(
        helper,
        graph_dict=graph_dict,
        validation_result=validation_result,
        quality_report=quality_report,
    )

    assert result.accepted_graph is None
    assert result.errors
    assert result.errors[0].message == "missing required node"


def test_workflow_generation_acceptance_helper_surfaces_run_readiness_failures() -> None:
    helper = _load_workflow_acceptance_helper()
    graph_dict = {"nodes": [{"id": "compute"}], "edges": []}
    validation_result = SimpleNamespace(
        success=True,
        run_ready=False,
        errors=[],
        run_readiness_failure_mode="non_runnable_code",
        contract_report=SimpleNamespace(
            run_readiness_issues=[
                "Code node 'compute' contains placeholder status payload code instead of runnable logic."
            ]
        ),
        graph=None,
    )
    quality_report = SimpleNamespace(
        overall_score=0,
        concerns=[],
    )

    result = _call_workflow_acceptance_helper(
        helper,
        graph_dict=graph_dict,
        validation_result=validation_result,
        quality_report=quality_report,
    )

    assert result.accepted_graph is None
    assert result.errors
    assert (
        result.errors[0].message
        == "Code node 'compute' contains placeholder status payload code instead of runnable logic."
    )


@pytest.mark.asyncio
async def test_placeholder_code_failure_mode_surfaces_in_runtime_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _make_manager([
        CompletionResult(text="not json"),
        CompletionResult(text="graph = {}"),
    ])
    monkeypatch.setattr(chat_manager_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(manager, "_emit_intent_extraction_telemetry", lambda **kwargs: None)

    wf = workflow("placeholder_runtime")
    wf.code(
        "compute",
        code='result = {"status": "placeholder", "task": "compute metrics"}',
    )
    placeholder_graph = wf.build().model_dump(mode="json")

    async def fake_sandbox(code: str) -> tuple[dict | None, Any]:
        return (
            placeholder_graph,
            CodegenResult(success=True, graph=placeholder_graph, source_code=code),
        )

    monkeypatch.setattr(manager, "_sandbox_exec_builder_code", fake_sandbox)

    diagnosis_errors: list[Any] = []

    class FakeDiagnosisLoop:
        def __init__(self, max_attempts: int) -> None:
            self.max_attempts = max_attempts

        async def diagnose_and_repair(self, **kwargs: Any) -> Any:
            diagnosis_errors.extend(kwargs["errors"])
            return SimpleNamespace(success=False, final_graph=None)

    monkeypatch.setattr(diagnosis_module, "DiagnosisLoop", FakeDiagnosisLoop)
    monkeypatch.setattr(
        diagnosis_module,
        "generation_repair_attempt_budget",
        lambda default=4: 1,
    )

    graph, events = await manager._generate_workflow_from_intent(
        "Compute metrics from the uploaded data",
        "wf-placeholder",
        "ch-placeholder",
    )

    assert graph is None
    validation_events = [event for event in events if event.type == "chat_validation_result"]
    summary_events = [event for event in events if event.type == "chat_generation_summary"]
    assert validation_events
    assert summary_events
    assert validation_events[-1].failure_mode == "non_runnable_code"
    assert validation_events[-1].build_status == "validated"
    assert validation_events[-1].failure_bucket == "semantic_reprompt_or_diagnosis"
    assert validation_events[-1].handoff_reason == "run_readiness_gap"
    assert "not run-ready" in str(validation_events[-1].build_summary).lower()
    assert summary_events[-1].failure_mode == "non_runnable_code"
    assert summary_events[-1].build_status == "validated"
    assert summary_events[-1].failure_bucket == "semantic_reprompt_or_diagnosis"
    assert diagnosis_errors
    assert "placeholder" in diagnosis_errors[0].message.lower()


@pytest.mark.asyncio
async def test_sandbox_retry_success_runs_quality_gate_and_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """A graph that only appears after sandbox retry must still hit the quality gate."""
    manager = _make_manager([
        CompletionResult(text="not json"),
        CompletionResult(text="graph = {}"),
    ])
    monkeypatch.setenv("DAN_GRAPH_QUALITY_THRESHOLD", "50")
    monkeypatch.setattr(chat_manager_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(manager, "_emit_intent_extraction_telemetry", lambda **kwargs: None)

    sandbox_results = [
        (
            None,
            CodegenResult(
                success=False,
                source_code="graph = {}",
                error_type="timeout",
                error_message="sandbox timed out after 30s",
            ),
        ),
        ({"nodes": [{"id": "n1"}], "edges": []}, None),
    ]

    async def fake_sandbox(code: str) -> tuple[dict | None, Any]:
        return sandbox_results.pop(0)

    monkeypatch.setattr(manager, "_sandbox_exec_builder_code", fake_sandbox)
    monkeypatch.setattr(planner_module, "validate_codegen_output", lambda graph_dict: _validation_success())
    monkeypatch.setattr(
        graph_quality_module,
        "compute_quality_report",
        lambda graph_dict, prompt_text, tier=None: SimpleNamespace(
            overall_score=10,
            concerns=["too few nodes"],
        ),
    )

    diagnosis_calls: list[dict[str, Any]] = []

    class FakeDiagnosisLoop:
        def __init__(self, max_attempts: int) -> None:
            self.max_attempts = max_attempts

        async def diagnose_and_repair(self, **kwargs: Any) -> Any:
            diagnosis_calls.append(kwargs)
            return SimpleNamespace(success=False, final_graph=None)

    monkeypatch.setattr(diagnosis_module, "DiagnosisLoop", FakeDiagnosisLoop)

    graph, events = await manager._generate_workflow_from_intent("Build a chain", "wf-1", "ch-1")

    assert graph is None
    assert any(event.type == "chat_graph_quality" for event in events)
    assert diagnosis_calls


@pytest.mark.asyncio
async def test_intent_extraction_uses_canonical_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _make_manager([
        CompletionResult(text="not json"),
        CompletionResult(text="graph = {}"),
    ])
    provider = _default_provider(manager)
    monkeypatch.setattr(chat_manager_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(manager, "_emit_intent_extraction_telemetry", lambda **kwargs: None)

    async def fake_sandbox(_code: str) -> tuple[dict | None, Any]:
        return (
            None,
            CodegenResult(
                success=False,
                source_code="graph = {}",
                error_type="runtime_error",
                error_message="boom",
            ),
        )

    monkeypatch.setattr(manager, "_sandbox_exec_builder_code", fake_sandbox)

    class FakeDiagnosisLoop:
        def __init__(self, max_attempts: int) -> None:
            self.max_attempts = max_attempts

        async def diagnose_and_repair(self, **kwargs: Any) -> Any:
            return SimpleNamespace(success=False, final_graph=None)

    monkeypatch.setattr(diagnosis_module, "DiagnosisLoop", FakeDiagnosisLoop)

    graph, _events = await manager._generate_workflow_from_intent("Build a chain", "wf-intent", "ch-intent")

    assert graph is None
    assert provider.requests
    system_prompt = provider.requests[0]["messages"][0]["content"]
    assert "Workflow Generation Contract" in system_prompt
    assert "Do NOT invent tool_ids not in this list" in system_prompt
    assert "smallest complete runnable workflow" in system_prompt


@pytest.mark.asyncio
async def test_terminal_empty_codegen_response_emits_failure_event_and_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated empty codegen responses should not end in a silent failure path."""
    manager = _make_manager([
        CompletionResult(text="not json"),
        CompletionResult(text="   "),
        CompletionResult(text=" \n "),
        CompletionResult(text="\t"),
    ])
    monkeypatch.setattr(chat_manager_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(manager, "_emit_intent_extraction_telemetry", lambda **kwargs: None)

    outcomes: list[tuple[str, bool, str, bool, str]] = []

    def fake_record(
        method: str,
        success: bool = True,
        error_type: str = "",
        fix_needed: bool = False,
        pattern: str = "",
    ) -> None:
        outcomes.append((method, success, error_type, fix_needed, pattern))

    monkeypatch.setattr(manager, "_record_gen_outcome", fake_record)

    graph, events = await manager._generate_workflow_from_intent("Build a chain", "wf-2", "ch-2")

    assert graph is None
    validation_events = [event for event in events if event.type == "chat_validation_result"]
    assert validation_events
    assert any("empty" in err.lower() for err in validation_events[-1].errors)
    assert ("codegen", False, "no_output", False, "wf-2") in outcomes


@pytest.mark.asyncio
async def test_non_timeout_sandbox_failure_preserves_error_for_diagnosis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Diagnosis should see the real sandbox/runtime failure, not a generic no-output error."""
    manager = _make_manager([
        CompletionResult(text="not json"),
        CompletionResult(text="graph = {}"),
    ])
    monkeypatch.setattr(chat_manager_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(manager, "_emit_intent_extraction_telemetry", lambda **kwargs: None)

    async def fake_sandbox(code: str) -> tuple[dict | None, Any]:
        return (
            None,
            CodegenResult(
                success=False,
                source_code=code,
                error_type="runtime_error",
                error_message="NameError: missing symbol",
                error_line=7,
            ),
        )

    monkeypatch.setattr(manager, "_sandbox_exec_builder_code", fake_sandbox)

    diagnosis_errors: list[Any] = []
    diagnosis_meta: dict[str, Any] = {}

    class FakeDiagnosisLoop:
        def __init__(self, max_attempts: int) -> None:
            diagnosis_meta["max_attempts"] = max_attempts

        async def diagnose_and_repair(self, **kwargs: Any) -> Any:
            diagnosis_errors.extend(kwargs["errors"])
            diagnosis_meta["graph_validator_present"] = kwargs.get("graph_validator") is not None
            return SimpleNamespace(success=False, final_graph=None)

    monkeypatch.setattr(diagnosis_module, "DiagnosisLoop", FakeDiagnosisLoop)
    monkeypatch.setattr(
        diagnosis_module,
        "generation_repair_attempt_budget",
        lambda default=4: 4,
    )

    graph, _events = await manager._generate_workflow_from_intent("Build a chain", "wf-3", "ch-3")

    assert graph is None
    assert diagnosis_errors
    assert diagnosis_errors[0].message == "NameError: missing symbol"
    assert diagnosis_errors[0].error_type.value == "runtime_error"
    assert diagnosis_meta["max_attempts"] == 4
    assert diagnosis_meta["graph_validator_present"] is True


@pytest.mark.asyncio
async def test_post_diagnosis_quality_threshold_blocks_low_quality_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Diagnosis-repaired graphs should honor the same quality threshold as first-pass graphs."""
    manager = _make_manager([
        CompletionResult(text="not json"),
        CompletionResult(text="graph = {}"),
    ])
    monkeypatch.setenv("DAN_GRAPH_QUALITY_THRESHOLD", "60")
    monkeypatch.setattr(chat_manager_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(manager, "_emit_intent_extraction_telemetry", lambda **kwargs: None)

    async def fake_sandbox(code: str) -> tuple[dict | None, Any]:
        return (
            None,
            CodegenResult(
                success=False,
                source_code=code,
                error_type="runtime_error",
                error_message="NameError: missing symbol",
            ),
        )

    monkeypatch.setattr(manager, "_sandbox_exec_builder_code", fake_sandbox)
    monkeypatch.setattr(planner_module, "validate_codegen_output", lambda graph_dict: _validation_success())
    monkeypatch.setattr(
        graph_quality_module,
        "compute_quality_report",
        lambda graph_dict, prompt_text, tier=None: SimpleNamespace(
            overall_score=15,
            concerns=["underspecified graph"],
        ),
    )

    class FakeDiagnosisLoop:
        def __init__(self, max_attempts: int) -> None:
            self.max_attempts = max_attempts

        async def diagnose_and_repair(self, **kwargs: Any) -> Any:
            return SimpleNamespace(
                success=True,
                final_graph={"nodes": [{"id": "n1"}], "edges": []},
            )

    monkeypatch.setattr(diagnosis_module, "DiagnosisLoop", FakeDiagnosisLoop)

    graph, events = await manager._generate_workflow_from_intent("Build a chain", "wf-4", "ch-4")

    assert graph is None
    assert any(event.type == "chat_graph_quality" for event in events)


@pytest.mark.asyncio
async def test_terminal_llm_error_emits_failure_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal codegen LLM error should not return with an empty event list."""
    manager = _make_manager([
        CompletionResult(text="not json"),
        RuntimeError("provider exploded"),
    ])
    monkeypatch.setattr(chat_manager_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(manager, "_emit_intent_extraction_telemetry", lambda **kwargs: None)

    outcomes: list[tuple[str, bool, str, bool, str]] = []

    def fake_record(
        method: str,
        success: bool = True,
        error_type: str = "",
        fix_needed: bool = False,
        pattern: str = "",
    ) -> None:
        outcomes.append((method, success, error_type, fix_needed, pattern))

    monkeypatch.setattr(manager, "_record_gen_outcome", fake_record)

    graph, events = await manager._generate_workflow_from_intent("Build a chain", "wf-5", "ch-5")

    assert graph is None
    validation_events = [event for event in events if event.type == "chat_validation_result"]
    assert validation_events
    assert "provider exploded" in validation_events[-1].errors[0]
    assert ("codegen", False, "llm_error", False, "wf-5") in outcomes


@pytest.mark.asyncio
async def test_sandbox_exec_builder_code_returns_structured_error_on_runner_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sandbox bootstrap errors should come back as structured diagnostics."""

    async def fake_run(self: Any, code: str, config: Any, inputs: dict[str, Any]) -> Any:
        raise RuntimeError("sandbox bootstrap failed")

    monkeypatch.setattr(sandbox_runner_module.SandboxRunner, "run", fake_run)

    graph, codegen = await ChatManager._sandbox_exec_builder_code("graph = {}")

    assert graph is None
    assert codegen is not None
    assert codegen.error_type == "runtime_error"
    assert codegen.error_message == "sandbox bootstrap failed"
