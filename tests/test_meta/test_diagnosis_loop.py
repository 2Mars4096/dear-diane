"""Tests for DiagnosisLoop, DiagnosisResult, DiagnosisAttempt, DiagnosisMetrics."""

from __future__ import annotations

import pytest

from dan.meta.diagnosis import (
    CorrectionStrategy,
    DiagnosisAttempt,
    DiagnosisLoop,
    DiagnosisMetrics,
    DiagnosisResult,
    GenerationError,
    GenerationErrorType,
    GenerationStage,
)

VALID_CODE = "x = 1 + 2\n"
SYNTAX_ERROR_CODE = "def f(\n"
IMPORT_ERROR_CODE = "from dan.builder import workflow\nx = 1\n"


def _make_error(
    error_type: GenerationErrorType = GenerationErrorType.syntax_error,
    message: str = "invalid syntax",
    stage: GenerationStage = GenerationStage.sandbox,
    source_line: int | None = 1,
) -> GenerationError:
    return GenerationError(
        stage=stage,
        error_type=error_type,
        message=message,
        source_line=source_line,
    )


# ── No errors → immediate success ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_errors_immediate_success():
    loop = DiagnosisLoop(max_attempts=2)
    result = await loop.diagnose_and_repair(
        goal="test",
        generated_code=VALID_CODE,
        errors=[],
    )
    assert result.success is True
    assert len(result.attempts) == 1
    assert result.attempts[0].result == "fixed"
    assert result.final_code == VALID_CODE


# ── Import error with DAN module → auto-fix ────────────────────────────────


@pytest.mark.asyncio
async def test_import_error_autofix_success():
    """Missing dan.builder import should be auto-fixed."""
    code_without_import = 'wf = workflow("test")\ngraph = wf.build()\n'
    import_err = _make_error(
        error_type=GenerationErrorType.import_error,
        message="ModuleNotFoundError: No module named 'dan.builder'",
    )

    loop = DiagnosisLoop(max_attempts=2)
    result = await loop.diagnose_and_repair(
        goal="build a workflow",
        generated_code=code_without_import,
        errors=[import_err],
    )

    assert result.success is True
    assert "from dan.builder import workflow" in result.final_code
    assert any("auto-fix" in c for a in result.attempts for c in a.corrections_applied)


# ── Syntax error with LLM fix → re-prompt ──────────────────────────────────


@pytest.mark.asyncio
async def test_syntax_error_reprompt_success():
    """Syntax error triggers re-prompt; mock LLM returns valid code."""
    syntax_err = _make_error(
        error_type=GenerationErrorType.syntax_error,
        message="SyntaxError: unexpected EOF while parsing",
        source_line=1,
    )

    fixed_code = (
        "from dan.builder import workflow\n"
        "wf = workflow('fixed')\n"
        "wf.llm('a', prompt='hello')\n"
        "graph = wf.build()\n"
    )

    async def mock_llm(system: str, user: str) -> str:
        return fixed_code

    loop = DiagnosisLoop(max_attempts=2)
    result = await loop.diagnose_and_repair(
        goal="compute something",
        generated_code=SYNTAX_ERROR_CODE,
        errors=[syntax_err],
        llm_complete=mock_llm,
    )

    assert result.success is True
    assert result.final_code == fixed_code.strip()
    assert any(
        "LLM re-prompt" in c for a in result.attempts for c in a.corrections_applied
    )


# ── Exceed max_attempts → failure ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_exceeds_max_attempts_failure():
    """Loop should stop after max_attempts and return failure."""
    syntax_err = _make_error(
        error_type=GenerationErrorType.syntax_error,
        message="SyntaxError: invalid syntax",
    )

    call_count = 0

    async def mock_llm_always_bad(system: str, user: str) -> str:
        nonlocal call_count
        call_count += 1
        return SYNTAX_ERROR_CODE

    loop = DiagnosisLoop(max_attempts=2)
    result = await loop.diagnose_and_repair(
        goal="test",
        generated_code=SYNTAX_ERROR_CODE,
        errors=[syntax_err],
        llm_complete=mock_llm_always_bad,
    )

    assert result.success is False
    assert len(result.attempts) == 2
    assert result.final_errors is not None
    assert len(result.final_errors) > 0


# ── No llm_complete → re_prompt errors become failures ─────────────────────


@pytest.mark.asyncio
async def test_no_llm_complete_reprompt_fails():
    """When no llm_complete is provided, re_prompt strategy fails."""
    syntax_err = _make_error(
        error_type=GenerationErrorType.syntax_error,
        message="SyntaxError: invalid syntax",
    )

    loop = DiagnosisLoop(max_attempts=2)
    result = await loop.diagnose_and_repair(
        goal="test",
        generated_code=SYNTAX_ERROR_CODE,
        errors=[syntax_err],
        llm_complete=None,
    )

    assert result.success is False
    assert all(a.result == "failed" for a in result.attempts)


# ── LLM raises exception → attempt marked failed ──────────────────────────


@pytest.mark.asyncio
async def test_llm_exception_handled():
    """If llm_complete raises, the attempt is marked 'failed' and loop continues."""
    err = _make_error(
        error_type=GenerationErrorType.syntax_error,
        message="SyntaxError: invalid",
    )

    async def exploding_llm(system: str, user: str) -> str:
        raise RuntimeError("LLM service unavailable")

    loop = DiagnosisLoop(max_attempts=2)
    result = await loop.diagnose_and_repair(
        goal="test",
        generated_code=SYNTAX_ERROR_CODE,
        errors=[err],
        llm_complete=exploding_llm,
    )

    assert result.success is False
    assert all(a.result == "failed" for a in result.attempts)


# ── LLM returns empty → attempt marked failed ─────────────────────────────


@pytest.mark.asyncio
async def test_llm_returns_empty():
    """If llm_complete returns empty string, attempt is 'failed'."""
    err = _make_error(
        error_type=GenerationErrorType.syntax_error,
        message="SyntaxError: invalid",
    )

    async def empty_llm(system: str, user: str) -> str:
        return ""

    loop = DiagnosisLoop(max_attempts=1)
    result = await loop.diagnose_and_repair(
        goal="test",
        generated_code=SYNTAX_ERROR_CODE,
        errors=[err],
        llm_complete=empty_llm,
    )

    assert result.success is False
    assert result.attempts[0].result == "failed"


# ── Partial fix then success on second attempt ─────────────────────────────


@pytest.mark.asyncio
async def test_partial_then_success():
    """First attempt partially fixes (import), second attempt (re-prompt) finishes."""
    import_err = _make_error(
        error_type=GenerationErrorType.import_error,
        message="ModuleNotFoundError: No module named 'dan.builder'",
    )
    code_with_both_issues = "wf = workflow('x')\ndef f(\n"

    async def mock_llm(system: str, user: str) -> str:
        return "from dan.builder import workflow\nx = 1\n"

    loop = DiagnosisLoop(max_attempts=3)
    result = await loop.diagnose_and_repair(
        goal="test",
        generated_code=code_with_both_issues,
        errors=[import_err],
        llm_complete=mock_llm,
    )

    assert len(result.attempts) >= 1
    assert result.final_code != code_with_both_issues


# ── Unresolvable auto-fix should fall back to re-prompt ───────────────────


@pytest.mark.asyncio
async def test_unresolvable_port_autofix_falls_back_to_reprompt():
    """Port auto-fix should degrade to re-prompt when no deterministic map exists."""
    err = _make_error(
        error_type=GenerationErrorType.edge_endpoint,
        stage=GenerationStage.validation,
        message="Edge 'e1': source node 'a' has no output port 'typo'",
    )

    repaired_code = (
        "from dan.builder import workflow\n"
        "wf = workflow('fixed')\n"
        "wf.llm('a', prompt='hello')\n"
        "graph = wf.build()\n"
    )
    llm_calls = 0

    async def mock_llm(system: str, user: str) -> str:
        nonlocal llm_calls
        llm_calls += 1
        return repaired_code

    loop = DiagnosisLoop(max_attempts=1)
    result = await loop.diagnose_and_repair(
        goal="test",
        generated_code="wf.connect('a', 'typo', 'b', 'input')\n",
        errors=[err],
        llm_complete=mock_llm,
    )

    assert llm_calls == 1
    assert result.success is True
    assert result.attempts[0].strategy_used == CorrectionStrategy.re_prompt


# ── Strategy selection: import → auto_fix, syntax → re_prompt ──────────────


@pytest.mark.asyncio
async def test_strategy_used_recorded():
    """DiagnosisAttempt records which strategy was selected."""
    err = _make_error(
        error_type=GenerationErrorType.import_error,
        message="ModuleNotFoundError: No module named 'dan.models'",
    )

    loop = DiagnosisLoop(max_attempts=1)
    result = await loop.diagnose_and_repair(
        goal="test",
        generated_code="import dan.models\nx = 1\n",
        errors=[err],
    )

    assert len(result.attempts) >= 1
    first = result.attempts[0]
    assert first.strategy_used is not None
    assert first.strategy_used in (
        CorrectionStrategy.auto_fix,
        CorrectionStrategy.re_prompt,
    )


# ── max_attempts=1 → only one attempt ─────────────────────────────────────


@pytest.mark.asyncio
async def test_single_attempt_limit():
    err = _make_error(
        error_type=GenerationErrorType.syntax_error,
        message="SyntaxError: invalid",
    )

    loop = DiagnosisLoop(max_attempts=1)
    result = await loop.diagnose_and_repair(
        goal="test",
        generated_code=SYNTAX_ERROR_CODE,
        errors=[err],
    )

    assert result.success is False
    assert len(result.attempts) == 1


# ── DiagnosisMetrics ───────────────────────────────────────────────────────


class TestDiagnosisMetrics:
    def test_record_success(self):
        m = DiagnosisMetrics()
        attempt = DiagnosisAttempt(
            attempt_number=1,
            errors_found=[
                _make_error(GenerationErrorType.import_error, "missing import")
            ],
            corrections_applied=["auto-fix applied"],
            strategy_used=CorrectionStrategy.auto_fix,
            result="fixed",
        )
        result = DiagnosisResult(
            success=True,
            final_code="x = 1",
            attempts=[attempt],
        )

        m.record(result)

        assert m.total_invocations == 1
        assert m.successes == 1
        assert m.failures == 0
        assert m.total_attempts == 1
        assert m.error_type_counts["import_error"] == 1
        assert m.strategy_counts["auto_fix"] == 1
        assert m.success_rate == 1.0

    def test_record_failure(self):
        m = DiagnosisMetrics()
        attempt1 = DiagnosisAttempt(
            attempt_number=1,
            errors_found=[
                _make_error(GenerationErrorType.syntax_error, "bad syntax")
            ],
            corrections_applied=[],
            strategy_used=CorrectionStrategy.re_prompt,
            result="failed",
        )
        attempt2 = DiagnosisAttempt(
            attempt_number=2,
            errors_found=[
                _make_error(GenerationErrorType.syntax_error, "bad syntax")
            ],
            corrections_applied=[],
            strategy_used=CorrectionStrategy.re_prompt,
            result="failed",
        )
        result = DiagnosisResult(
            success=False,
            final_code="bad code",
            attempts=[attempt1, attempt2],
            final_errors=[_make_error()],
        )

        m.record(result)

        assert m.total_invocations == 1
        assert m.successes == 0
        assert m.failures == 1
        assert m.total_attempts == 2
        assert m.error_type_counts["syntax_error"] == 2
        assert m.strategy_counts["re_prompt"] == 2
        assert m.success_rate == 0.0

    def test_multiple_records(self):
        m = DiagnosisMetrics()

        success_result = DiagnosisResult(
            success=True,
            final_code="ok",
            attempts=[
                DiagnosisAttempt(
                    attempt_number=1,
                    errors_found=[],
                    corrections_applied=[],
                    result="fixed",
                )
            ],
        )
        failure_result = DiagnosisResult(
            success=False,
            final_code="bad",
            attempts=[
                DiagnosisAttempt(
                    attempt_number=1,
                    errors_found=[
                        _make_error(GenerationErrorType.runtime_error, "crash")
                    ],
                    corrections_applied=[],
                    strategy_used=CorrectionStrategy.re_prompt,
                    result="failed",
                )
            ],
            final_errors=[_make_error(GenerationErrorType.runtime_error)],
        )

        m.record(success_result)
        m.record(failure_result)

        assert m.total_invocations == 2
        assert m.successes == 1
        assert m.failures == 1
        assert m.success_rate == 0.5

    def test_empty_metrics(self):
        m = DiagnosisMetrics()
        assert m.total_invocations == 0
        assert m.success_rate == 0.0
