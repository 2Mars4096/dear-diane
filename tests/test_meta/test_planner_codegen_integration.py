"""Integration tests for codegen pipeline wired through WorkflowPlanner.

Covers: GENERATE_CODE with valid code, sandbox errors with diagnosis,
validation failures with diagnosis, legacy fallback, CodegenDiagnostics
population, and _llm_complete_for_repair helper.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.meta.diagnosis import (
    DiagnosisResult,
    GenerationError,
    GenerationErrorType,
    GenerationStage,
)
from dan.meta.planner import (
    CodegenDiagnostics,
    CodegenResult,
    GenerateCodePlan,
    ValidationResult,
    WorkflowPlanner,
    _LEGACY_GENERATE_FALLBACK,
)
from dan.sandbox import SandboxResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_GRAPH_DICT = {
    "version": "dan_graph_v1",
    "metadata": {"name": "test", "description": ""},
    "nodes": [
        {
            "id": "n1",
            "name": "Node 1",
            "node_type": "llm_operator",
            "model": "claude-sonnet-4-6",
            "prompt_template": "Hello",
            "system_prompt": "",
            "temperature": 0.3,
        }
    ],
    "edges": [],
    "entry_points": ["n1"],
    "exit_points": ["n1"],
    "hyperedges": [],
    "sub_graphs": {},
    "shared_context": [],
    "artifact_refs": [],
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
            mock_validate.return_value = failed_validation

            with patch("dan.meta.diagnosis.DiagnosisLoop.diagnose_and_repair", new_callable=AsyncMock) as mock_diag:
                mock_diag.return_value = diag_result

                result = await planner.execute_plan(plan)

    assert result["code_generated"] is True
    mock_diag.assert_awaited_once()
    call_kwargs = mock_diag.call_args
    assert call_kwargs[1]["errors"] == [recoverable_error] or call_kwargs[0][2] == [recoverable_error]


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
