"""Integration tests for codegen pipeline wired through WorkflowPlanner.

Covers: GENERATE_CODE with valid code, sandbox errors with diagnosis,
validation failures with diagnosis, legacy fallback, CodegenDiagnostics
population, and _llm_complete_for_repair helper.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.builder import workflow
import dan.meta.diagnosis as diagnosis_module
from dan.meta.diagnosis import (
    DiagnosisResult,
    GenerationError,
    GenerationErrorType,
    GenerationStage,
)
from dan.meta.planner import (
    AdaptPlan,
    CodegenDiagnostics,
    CodegenResult,
    ExecutionReadinessError,
    GeneratePlan,
    GenerateCodePlan,
    ValidationResult,
    WorkflowPlanner,
    _LEGACY_GENERATE_FALLBACK,
    validate_codegen_output,
)
from dan.meta.intent_compiler import DirectBuildError
from dan.meta.intent_schema import StageIntent, StageType, WorkflowIntent
from dan.sandbox import SandboxResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _build_valid_graph_dict() -> dict[str, Any]:
    wf = workflow("test_codegen")
    wf.llm("n1", prompt="Hello")
    return wf.build().model_dump(mode="json")


VALID_GRAPH_DICT = _build_valid_graph_dict()

INVALID_ENRICHED_GRAPH_DICT = {
    **VALID_GRAPH_DICT,
    "edges": [
        {
            "source_node_id": "n1",
            "source_port": "text",
            "target_node_id": "ghost",
            "target_port": "text",
        },
    ],
}

VALID_BUILDER_CODE = '''\
from dan.builder import workflow

wf = workflow("test_wf")
wf.llm("n1", prompt="Hello")
graph = wf.build()
'''


def _make_planner(**overrides: Any) -> WorkflowPlanner:
    """Create a WorkflowPlanner with a mock discovery service."""
    discovery = AsyncMock()
    discovery.discover_all = AsyncMock(return_value=MagicMock(
        workflows=[], tools=[], skills=[], patterns=[],
        self_knowledge_formatted="", self_knowledge_chunks=[],
    ))
    llm_call = overrides.pop("llm_call", AsyncMock(return_value="{}"))
    return WorkflowPlanner(
        discovery=discovery,
        llm_call=llm_call,
        **overrides,
    )


# ---------------------------------------------------------------------------
# 1. Valid builder code → plan() returns valid Graph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_code_returns_graph():
    """Mock LLM returns valid builder code → sandbox succeeds → graph returned."""
    planner = _make_planner()
    plan = GenerateCodePlan(code=VALID_BUILDER_CODE, description="test workflow")

    sandbox_result = SandboxResult(exit_code=0)
    structured = {"graph": VALID_GRAPH_DICT, "source_code": VALID_BUILDER_CODE}

    with patch("dan.sandbox.runner.SandboxRunner.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (sandbox_result, structured)

        with patch("dan.meta.planner.validate_codegen_output") as mock_validate:
            from dan.models.graph import Graph
            mock_validate.return_value = ValidationResult(
                success=True,
                graph=Graph.model_validate(VALID_GRAPH_DICT),
            )

            result = await planner.execute_plan(plan)

    assert result["code_generated"] is True
    assert result["generated"] is True
    assert "workflow_id" in result
    assert result["graph"]["version"] == "dan_graph_v1"


@pytest.mark.asyncio
async def test_codegen_revalidates_after_enrichment():
    """A graph invalidated by enrichment should not be returned as runnable."""
    planner = _make_planner()
    plan = GenerateCodePlan(code=VALID_BUILDER_CODE, description="test workflow")

    sandbox_result = SandboxResult(exit_code=0)
    structured = {"graph": VALID_GRAPH_DICT, "source_code": VALID_BUILDER_CODE}

    with patch("dan.sandbox.runner.SandboxRunner.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (sandbox_result, structured)
        with patch.object(
            planner,
            "_enrich_graph",
            return_value=INVALID_ENRICHED_GRAPH_DICT,
        ):
            with patch("dan.meta.planner._LEGACY_GENERATE_FALLBACK", False):
                with pytest.raises(
                    ValueError,
                    match="Enriched codegen graph failed execution-readiness validation",
                ):
                    await planner.execute_plan(plan)


@pytest.mark.asyncio
async def test_direct_build_revalidates_after_enrichment():
    """Direct-build graphs are revalidated after enrichment before being returned."""
    planner = _make_planner()
    plan = GenerateCodePlan(
        code="",
        description="direct build",
        intent=WorkflowIntent(
            goal="Build a simple transform",
            stages=[
                StageIntent(
                    name="step",
                    stage_type=StageType.transform,
                    description="Transform the input",
                ),
            ],
            global_inputs=[],
            global_outputs=[],
        ),
    )

    with patch.object(
        planner,
        "_enrich_graph",
        return_value=INVALID_ENRICHED_GRAPH_DICT,
    ):
        with pytest.raises(
            DirectBuildError,
            match="Enriched direct-build graph failed execution-readiness validation",
        ):
            await planner.execute_plan_direct(plan)


def test_enrich_graph_binds_explicit_file_paths_into_tool_config() -> None:
    planner = _make_planner()
    graph_data = {
        "version": "dan_graph_v1",
        "metadata": {"name": "sales_metrics_analysis"},
        "nodes": [
            {
                "id": "read_csv",
                "name": "read_csv",
                "node_type": "tool_operator",
                "tool_id": "csv_read",
                "tool_config": {},
                "input_ports": [{"name": "path", "required": True}],
                "output_ports": [{"name": "rows"}],
            },
            {
                "id": "read_expected",
                "name": "read_expected",
                "node_type": "tool_operator",
                "tool_id": "file_read",
                "tool_config": {},
                "input_ports": [{"name": "path", "required": True}],
                "output_ports": [{"name": "content"}],
            },
        ],
        "edges": [],
        "entry_points": ["read_csv", "read_expected"],
        "exit_points": ["read_expected"],
    }

    enriched = planner._enrich_graph(
        graph_data,
        domain=None,
        user_text=(
            "Build a workflow over tests/fixtures/benchmark_prep/lr2_data/. "
            "Read tests/fixtures/benchmark_prep/lr2_data/sales_metrics.csv and "
            "compare against tests/fixtures/benchmark_prep/lr2_data/expected_summary.json."
        ),
    )

    read_csv = next(node for node in enriched["nodes"] if node["id"] == "read_csv")
    read_expected = next(
        node for node in enriched["nodes"] if node["id"] == "read_expected"
    )
    assert read_csv["tool_config"]["path"] == (
        "tests/fixtures/benchmark_prep/lr2_data/sales_metrics.csv"
    )
    assert read_expected["tool_config"]["path"] == (
        "tests/fixtures/benchmark_prep/lr2_data/expected_summary.json"
    )


# ---------------------------------------------------------------------------
# 2. Syntax error → diagnosis invoked → LLM fix works → Graph returned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_syntax_error_diagnosis_repair_success():
    """Sandbox returns syntax error → diagnosis loop repairs → valid graph."""
    fixed_code = VALID_BUILDER_CODE

    async def fake_llm(system, user, model=None, temp=None):
        return fixed_code

    planner = _make_planner(llm_call=fake_llm)
    plan = GenerateCodePlan(code="def f(\n", description="test")

    sandbox_result = SandboxResult(exit_code=0)
    structured = {
        "error": {"type": "SyntaxError", "message": "invalid syntax", "line": 1}
    }

    diag_result = DiagnosisResult(
        success=True,
        final_graph=VALID_GRAPH_DICT,
        final_code=fixed_code,
    )

    with patch("dan.sandbox.runner.SandboxRunner.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (sandbox_result, structured)

        with patch("dan.meta.diagnosis.DiagnosisLoop.diagnose_and_repair", new_callable=AsyncMock) as mock_diag:
            mock_diag.return_value = diag_result

            result = await planner.execute_plan(plan)

    assert result["code_generated"] is True
    assert result["source_code"] == fixed_code
    mock_diag.assert_awaited_once()


# ---------------------------------------------------------------------------
# 3. Validation failure → diagnosis invoked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_failure_triggers_diagnosis():
    """Sandbox succeeds but validation finds recoverable errors → diagnosis invoked."""
    planner = _make_planner()
    plan = GenerateCodePlan(code=VALID_BUILDER_CODE, description="test")

    sandbox_result = SandboxResult(exit_code=0)
    structured = {"graph": VALID_GRAPH_DICT, "source_code": VALID_BUILDER_CODE}

    recoverable_error = GenerationError(
        stage=GenerationStage.validation,
        error_type=GenerationErrorType.reachability,
        message="Node 'orphan' is unreachable",
        recoverable=True,
    )
    failed_validation = ValidationResult(
        success=False,
        errors=[recoverable_error],
        recoverable_errors=[recoverable_error],
        fatal_errors=[],
    )

    diag_result = DiagnosisResult(
        success=True,
        final_graph=VALID_GRAPH_DICT,
        final_code=VALID_BUILDER_CODE,
    )

    with patch("dan.sandbox.runner.SandboxRunner.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (sandbox_result, structured)

        with patch("dan.meta.planner.validate_codegen_output") as mock_validate:
            from dan.models.graph import Graph

            mock_validate.side_effect = [
                failed_validation,
                ValidationResult(
                    success=True,
                    graph=Graph.model_validate(VALID_GRAPH_DICT),
                ),
            ]

            with patch("dan.meta.diagnosis.DiagnosisLoop.diagnose_and_repair", new_callable=AsyncMock) as mock_diag:
                mock_diag.return_value = diag_result

                result = await planner.execute_plan(plan)

    assert result["code_generated"] is True
    mock_diag.assert_awaited_once()
    call_kwargs = mock_diag.call_args
    assert call_kwargs[1]["errors"] == [recoverable_error] or call_kwargs[0][2] == [recoverable_error]


@pytest.mark.asyncio
async def test_run_readiness_gap_triggers_diagnosis_with_contract_report():
    """Schema-valid but non-runnable graphs should hand off into diagnosis."""
    planner = _make_planner()
    plan = GenerateCodePlan(code=VALID_BUILDER_CODE, description="test")

    sandbox_result = SandboxResult(exit_code=0)
    empty_graph_dict = workflow("empty").build().model_dump(mode="json")
    structured = {"graph": empty_graph_dict, "source_code": VALID_BUILDER_CODE}
    validation = validate_codegen_output(empty_graph_dict)

    assert validation.success is True
    assert validation.run_ready is False

    diag_result = DiagnosisResult(
        success=False,
        final_code=VALID_BUILDER_CODE,
        final_errors=[],
    )

    with patch("dan.sandbox.runner.SandboxRunner.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (sandbox_result, structured)
        with patch(
            "dan.meta.diagnosis.DiagnosisLoop.diagnose_and_repair",
            new_callable=AsyncMock,
        ) as mock_diag:
            mock_diag.return_value = diag_result
            with patch("dan.meta.planner._LEGACY_GENERATE_FALLBACK", False):
                with pytest.raises(
                    ValueError,
                    match="diagnosis repair unsuccessful",
                ):
                    await planner.execute_plan(plan)

    mock_diag.assert_awaited_once()
    kwargs = mock_diag.await_args.kwargs
    assert kwargs["contract_report"]["failure_bucket"] == "semantic_reprompt_or_diagnosis"
    assert "not run-ready" in kwargs["contract_report"]["handoff_reason"].lower()
    assert any(
        error.message == "Workflow has no nodes, so it is not run-ready."
        for error in kwargs["errors"]
    )


@pytest.mark.asyncio
async def test_validation_failure_diagnosis_uses_extended_budget_and_graph_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planner should pass structural validation into multi-round diagnosis."""
    planner = _make_planner()
    plan = GenerateCodePlan(code=VALID_BUILDER_CODE, description="test")

    sandbox_result = SandboxResult(exit_code=0)
    structured = {"graph": VALID_GRAPH_DICT, "source_code": VALID_BUILDER_CODE}

    recoverable_error = GenerationError(
        stage=GenerationStage.validation,
        error_type=GenerationErrorType.reachability,
        message="Node 'orphan' is unreachable",
        recoverable=True,
    )
    failed_validation = ValidationResult(
        success=False,
        errors=[recoverable_error],
        recoverable_errors=[recoverable_error],
        fatal_errors=[],
    )

    seen: dict[str, Any] = {}

    class FakeDiagnosisLoop:
        def __init__(self, max_attempts: int) -> None:
            seen["max_attempts"] = max_attempts

        async def diagnose_and_repair(self, **kwargs: Any) -> DiagnosisResult:
            seen["graph_validator_present"] = kwargs.get("graph_validator") is not None
            return DiagnosisResult(success=False, final_code=VALID_BUILDER_CODE, attempts=[])

    monkeypatch.setattr(diagnosis_module, "DiagnosisLoop", FakeDiagnosisLoop)
    monkeypatch.setattr(
        diagnosis_module,
        "generation_repair_attempt_budget",
        lambda default=4: 4,
    )

    with patch("dan.sandbox.runner.SandboxRunner.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (sandbox_result, structured)

        with patch("dan.meta.planner.validate_codegen_output", return_value=failed_validation):
            with patch("dan.meta.planner._LEGACY_GENERATE_FALLBACK", False):
                with pytest.raises(ValueError, match="diagnosis repair unsuccessful"):
                    await planner.execute_plan(plan)

    assert seen["max_attempts"] == 4
    assert seen["graph_validator_present"] is True


# ---------------------------------------------------------------------------
# 4. Fatal validation error → no diagnosis, raises with diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fatal_validation_no_diagnosis_raises():
    """Fatal validation errors skip diagnosis and raise immediately."""
    planner = _make_planner()
    plan = GenerateCodePlan(code=VALID_BUILDER_CODE, description="test")

    sandbox_result = SandboxResult(exit_code=0)
    structured = {"graph": VALID_GRAPH_DICT, "source_code": VALID_BUILDER_CODE}

    fatal_error = GenerationError(
        stage=GenerationStage.validation,
        error_type=GenerationErrorType.cycle,
        message="Cycle detected in graph",
        recoverable=False,
    )
    failed_validation = ValidationResult(
        success=False,
        errors=[fatal_error],
        recoverable_errors=[],
        fatal_errors=[fatal_error],
    )

    with patch("dan.sandbox.runner.SandboxRunner.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (sandbox_result, structured)

        with patch("dan.meta.planner.validate_codegen_output") as mock_validate:
            mock_validate.return_value = failed_validation

            with patch("dan.meta.planner._LEGACY_GENERATE_FALLBACK", False):
                with pytest.raises(ValueError, match="fatal error"):
                    await planner.execute_plan(plan)


# ---------------------------------------------------------------------------
# 5. Legacy fallback triggered when codegen fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_fallback_on_codegen_failure():
    """When _LEGACY_GENERATE_FALLBACK is True, GENERATE_CODE failure
    falls back to legacy GENERATE path."""
    planner = _make_planner()
    plan = GenerateCodePlan(code="invalid!", description="test fallback")

    sandbox_result = SandboxResult(exit_code=0)
    structured = {
        "error": {"type": "RuntimeError", "message": "boom", "line": None}
    }

    with patch("dan.sandbox.runner.SandboxRunner.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (sandbox_result, structured)

        with patch("dan.meta.planner._LEGACY_GENERATE_FALLBACK", True):
            result = await planner.execute_plan(plan)

    assert result.get("legacy_fallback") is True
    assert result["generated"] is True


# ---------------------------------------------------------------------------
# 6. CodegenDiagnostics populated correctly on failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_codegen_diagnostics_populated_on_failure():
    """When codegen fails and no legacy fallback, the raised exception
    carries a CodegenDiagnostics with the right fields."""
    planner = _make_planner()
    plan = GenerateCodePlan(code="invalid!", description="test diag")

    sandbox_result = SandboxResult(exit_code=0)
    structured = {
        "error": {"type": "RuntimeError", "message": "boom", "line": None}
    }

    with patch("dan.sandbox.runner.SandboxRunner.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (sandbox_result, structured)

        with patch("dan.meta.planner._LEGACY_GENERATE_FALLBACK", False):
            with pytest.raises(ValueError) as exc_info:
                await planner.execute_plan(plan)

    exc = exc_info.value
    assert hasattr(exc, "diagnostics")
    diag: CodegenDiagnostics = exc.diagnostics
    assert diag.attempts == 1
    assert len(diag.errors) >= 1
    assert diag.final_code == "invalid!"
    assert diag.errors[0].error_type == GenerationErrorType.runtime_error


# ---------------------------------------------------------------------------
# 7. Validation failure + failed diagnosis → diagnostics with both
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_failure_diagnosis_fails_diagnostics():
    """Validation fails with recoverable errors, diagnosis also fails →
    raises with diagnostics containing both validation_result and diagnosis_result."""
    planner = _make_planner()
    plan = GenerateCodePlan(code=VALID_BUILDER_CODE, description="test")

    sandbox_result = SandboxResult(exit_code=0)
    structured = {"graph": VALID_GRAPH_DICT, "source_code": VALID_BUILDER_CODE}

    recoverable_error = GenerationError(
        stage=GenerationStage.validation,
        error_type=GenerationErrorType.reachability,
        message="Node 'orphan' is unreachable",
        recoverable=True,
    )
    failed_validation = ValidationResult(
        success=False,
        errors=[recoverable_error],
        recoverable_errors=[recoverable_error],
        fatal_errors=[],
    )

    diag_result = DiagnosisResult(
        success=False,
        final_code=VALID_BUILDER_CODE,
        final_errors=[recoverable_error],
    )

    with patch("dan.sandbox.runner.SandboxRunner.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (sandbox_result, structured)

        with patch("dan.meta.planner.validate_codegen_output") as mock_validate:
            mock_validate.return_value = failed_validation

            with patch("dan.meta.diagnosis.DiagnosisLoop.diagnose_and_repair", new_callable=AsyncMock) as mock_diag:
                mock_diag.return_value = diag_result

                with patch("dan.meta.planner._LEGACY_GENERATE_FALLBACK", False):
                    with pytest.raises(ValueError) as exc_info:
                        await planner.execute_plan(plan)

    exc = exc_info.value
    diag: CodegenDiagnostics = exc.diagnostics
    assert diag.validation_result is not None
    assert diag.diagnosis_result is not None
    assert diag.diagnosis_result.success is False


# ---------------------------------------------------------------------------
# 8. _llm_complete_for_repair wraps _call_llm correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_complete_for_repair_wrapper():
    """The repair helper delegates to _call_llm (plain completion)."""
    call_log: list[tuple[str, str]] = []

    async def fake_llm(system, user, model=None, temp=None):
        call_log.append((system, user))
        return "fixed code"

    planner = _make_planner(llm_call=fake_llm)
    result = await planner._llm_complete_for_repair("sys prompt", "user prompt")

    assert result == "fixed code"
    assert len(call_log) == 1
    assert call_log[0] == ("sys prompt", "user prompt")


# ---------------------------------------------------------------------------
# 9. execute_plan routes GENERATE_CODE to codegen pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_plan_routes_generate_code():
    """execute_plan dispatches GenerateCodePlan to _execute_generate_code."""
    planner = _make_planner()
    plan = GenerateCodePlan(code=VALID_BUILDER_CODE, description="route test")

    sandbox_result = SandboxResult(exit_code=0)
    structured = {"graph": VALID_GRAPH_DICT, "source_code": VALID_BUILDER_CODE}

    with patch("dan.sandbox.runner.SandboxRunner.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (sandbox_result, structured)

        with patch("dan.meta.planner.validate_codegen_output") as mock_validate:
            from dan.models.graph import Graph
            mock_validate.return_value = ValidationResult(
                success=True,
                graph=Graph.model_validate(VALID_GRAPH_DICT),
            )

            result = await planner.execute_plan(plan)

    assert result["code_generated"] is True
    assert result["workflow_id"].startswith("meta-code-")


@pytest.mark.asyncio
async def test_execute_generate_requires_run_ready_graph() -> None:
    """GENERATE must reject graphs that validate structurally but are not runnable."""
    planner = _make_planner()
    plan = GeneratePlan(
        spec={
            **VALID_GRAPH_DICT,
            "nodes": [],
            "entry_points": [],
            "exit_points": [],
        },
        description="empty workflow",
    )

    with pytest.raises(
        ValueError,
        match="Generated workflow failed execution-readiness validation",
    ):
        await planner.execute_plan(plan)


@pytest.mark.asyncio
async def test_execute_generate_surfaces_unresolved_code_failure_mode() -> None:
    planner = _make_planner()
    plan = GeneratePlan(
        spec={
            "nodes": [{"node_type": "code_operator", "name": "Compute"}],
            "edges": [],
        },
        description="code workflow",
    )

    with pytest.raises(
        ValueError,
        match="unresolved_code",
    ):
        await planner.execute_plan(plan)


@pytest.mark.asyncio
async def test_execute_adapt_requires_run_ready_graph() -> None:
    """ADAPT must reject mutation outputs that are not run-ready."""
    graph_store = MagicMock()
    graph_store.get_graph.return_value = VALID_GRAPH_DICT
    planner = _make_planner(graph_store=graph_store)
    plan = AdaptPlan(
        workflow_id="base-workflow",
        mutations=[{"op": "add_node", "node_type": "input", "name": "My Input"}],
    )

    invalid_graph = {
        **VALID_GRAPH_DICT,
        "nodes": [],
        "entry_points": [],
        "exit_points": [],
    }

    with patch("dan.server.graph_mutator.GraphMutator.apply") as mock_apply:
        mock_apply.return_value = SimpleNamespace(
            success=True,
            new_graph=invalid_graph,
            errors=[],
        )
        with pytest.raises(
            ValueError,
            match="Adapted workflow failed execution-readiness validation",
        ):
            await planner.execute_plan(plan)


@pytest.mark.asyncio
async def test_diagnosis_repaired_graph_must_be_run_ready() -> None:
    """Diagnosis success should still fail when the repaired graph is not runnable."""
    planner = _make_planner()
    plan = GenerateCodePlan(code="def f(\n", description="test")

    sandbox_result = SandboxResult(exit_code=0)
    structured = {
        "error": {"type": "SyntaxError", "message": "invalid syntax", "line": 1}
    }

    invalid_graph = {
        **VALID_GRAPH_DICT,
        "nodes": [],
        "entry_points": [],
        "exit_points": [],
    }
    diag_result = DiagnosisResult(
        success=True,
        final_graph=invalid_graph,
        final_code=VALID_BUILDER_CODE,
    )

    with patch("dan.sandbox.runner.SandboxRunner.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (sandbox_result, structured)
        with patch(
            "dan.meta.diagnosis.DiagnosisLoop.diagnose_and_repair",
            new_callable=AsyncMock,
        ) as mock_diag:
            mock_diag.return_value = diag_result
            with patch("dan.meta.planner._LEGACY_GENERATE_FALLBACK", False):
                with pytest.raises(
                    ValueError,
                    match="Diagnosis-repaired codegen graph failed execution-readiness validation",
                ):
                    await planner.execute_plan(plan)

    mock_diag.assert_awaited_once()


@pytest.mark.asyncio
async def test_execution_readiness_failure_does_not_trigger_legacy_fallback() -> None:
    """Execution-readiness failures must surface instead of silently degrading."""
    planner = _make_planner()
    plan = GenerateCodePlan(code="def f(\n", description="test")

    sandbox_result = SandboxResult(exit_code=0)
    structured = {
        "error": {"type": "SyntaxError", "message": "invalid syntax", "line": 1}
    }

    invalid_graph = {
        **VALID_GRAPH_DICT,
        "nodes": [],
        "entry_points": [],
        "exit_points": [],
    }
    diag_result = DiagnosisResult(
        success=True,
        final_graph=invalid_graph,
        final_code=VALID_BUILDER_CODE,
    )

    with patch("dan.sandbox.runner.SandboxRunner.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (sandbox_result, structured)
        with patch(
            "dan.meta.diagnosis.DiagnosisLoop.diagnose_and_repair",
            new_callable=AsyncMock,
        ) as mock_diag:
            mock_diag.return_value = diag_result
            with patch("dan.meta.planner._LEGACY_GENERATE_FALLBACK", True):
                with pytest.raises(ExecutionReadinessError):
                    await planner.execute_plan(plan)

    mock_diag.assert_awaited_once()
