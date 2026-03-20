"""Tests for the codegen harness, CodegenResult, and _parse_codegen_result."""

from __future__ import annotations

import pathlib

import pytest

from dan.meta.planner import (
    CodegenResult,
    WorkflowPlanner,
    _BUILDER_CODE_HARNESS,
)
from dan.sandbox import SandboxConfig, SandboxResult

_SRC_PATH = str(pathlib.Path(__file__).resolve().parents[2] / "src")

_SANDBOX_CFG = SandboxConfig(
    mode="subprocess",
    timeout_seconds=30,
    memory_mb=256,
    language="python",
    max_output_bytes=1_000_000,
)


# ---------------------------------------------------------------------------
# 1. Harness template is itself valid Python
# ---------------------------------------------------------------------------


class TestHarnessCompiles:
    def test_harness_template_compiles(self):
        compile(_BUILDER_CODE_HARNESS, "<harness>", "exec")


# ---------------------------------------------------------------------------
# 2. Unit tests for _parse_codegen_result
# ---------------------------------------------------------------------------


class TestParseCodegenResult:
    def test_success_with_graph(self):
        result = SandboxResult(exit_code=0)
        structured = {
            "graph": {"version": "dan_graph_v1", "nodes": []},
            "source_code": "graph = wf.build()",
        }
        cr = WorkflowPlanner._parse_codegen_result(result, structured, "graph = wf.build()")
        assert cr.success is True
        assert cr.graph == {"version": "dan_graph_v1", "nodes": []}
        assert cr.source_code == "graph = wf.build()"

    def test_source_code_from_structured_takes_precedence(self):
        result = SandboxResult(exit_code=0)
        structured = {
            "graph": {"version": "v1"},
            "source_code": "actual_code_from_harness",
        }
        cr = WorkflowPlanner._parse_codegen_result(result, structured, "fallback")
        assert cr.source_code == "actual_code_from_harness"

    def test_source_code_fallback_when_missing_from_structured(self):
        result = SandboxResult(exit_code=0)
        structured = {"graph": {"version": "v1"}}
        cr = WorkflowPlanner._parse_codegen_result(result, structured, "fallback_code")
        assert cr.source_code == "fallback_code"

    def test_structured_error(self):
        result = SandboxResult(exit_code=0)
        structured = {
            "error": {"type": "SyntaxError", "message": "invalid syntax", "line": 3}
        }
        cr = WorkflowPlanner._parse_codegen_result(result, structured, "bad code")
        assert cr.success is False
        assert cr.error_type == "SyntaxError"
        assert cr.error_message == "invalid syntax"
        assert cr.error_line == 3

    def test_structured_error_without_line(self):
        result = SandboxResult(exit_code=0)
        structured = {
            "error": {"type": "NameError", "message": "name 'x' is not defined", "line": None}
        }
        cr = WorkflowPlanner._parse_codegen_result(result, structured, "code")
        assert cr.success is False
        assert cr.error_type == "NameError"
        assert cr.error_line is None

    def test_sandbox_crash_no_result_file(self):
        result = SandboxResult(exit_code=-1, stderr="Killed")
        cr = WorkflowPlanner._parse_codegen_result(result, None, "some code")
        assert cr.success is False
        assert cr.error_type == "SandboxError"
        assert "Killed" in (cr.error_message or "")

    def test_sandbox_timeout(self):
        result = SandboxResult(exit_code=-1, stderr="Execution timed out after 30 seconds.")
        cr = WorkflowPlanner._parse_codegen_result(result, None, "code")
        assert cr.success is False
        assert "timed out" in (cr.error_message or "")

    def test_no_graph_or_error_key(self):
        result = SandboxResult(exit_code=0)
        structured = {"unexpected": "data"}
        cr = WorkflowPlanner._parse_codegen_result(result, structured, "code")
        assert cr.success is False
        assert cr.error_type == "SandboxError"

    def test_invalid_structured_type(self):
        result = SandboxResult(exit_code=0)
        cr = WorkflowPlanner._parse_codegen_result(result, "not a dict", "code")
        assert cr.success is False
        assert cr.error_type == "SandboxError"
        assert "expected dict" in (cr.error_message or "")


# ---------------------------------------------------------------------------
# 3. End-to-end harness tests via SandboxRunner
# ---------------------------------------------------------------------------


@pytest.fixture()
def sandbox_config():
    return _SANDBOX_CFG


async def _run_harness(user_code: str, config: SandboxConfig | None = None):
    from dan.sandbox.runner import SandboxRunner

    runner = SandboxRunner()
    return await runner.run(
        _BUILDER_CODE_HARNESS,
        config or _SANDBOX_CFG,
        {"src_path": _SRC_PATH, "user_code": user_code},
    )


_FAKE_GRAPH_CODE = """\
class _FakeGraph:
    def model_dump(self, mode=None):
        return {"version": "dan_graph_v1", "nodes": [], "edges": []}

graph = _FakeGraph()
"""


class TestHarnessValidCode:
    @pytest.mark.asyncio
    async def test_valid_code_produces_graph(self):
        result, structured = await _run_harness(_FAKE_GRAPH_CODE)
        assert result.exit_code == 0
        assert isinstance(structured, dict)
        assert "graph" in structured
        assert structured["graph"]["version"] == "dan_graph_v1"

    @pytest.mark.asyncio
    async def test_source_code_populated_on_success(self):
        result, structured = await _run_harness(_FAKE_GRAPH_CODE)
        assert structured["source_code"] == _FAKE_GRAPH_CODE

    @pytest.mark.asyncio
    async def test_alternative_variable_name_wf(self):
        code = """\
class _FakeGraph:
    def model_dump(self, mode=None):
        return {"version": "v1"}

wf = _FakeGraph()
"""
        result, structured = await _run_harness(code)
        assert result.exit_code == 0
        assert "graph" in structured


class TestHarnessSyntaxError:
    @pytest.mark.asyncio
    async def test_syntax_error_captured(self):
        result, structured = await _run_harness("def bad(\n")
        assert result.exit_code == 0
        assert isinstance(structured, dict)
        assert "error" in structured
        assert structured["error"]["type"] == "SyntaxError"

    @pytest.mark.asyncio
    async def test_syntax_error_reports_line(self):
        code = "x = 1\ny = 2\ndef bad(\n"
        result, structured = await _run_harness(code)
        assert structured["error"]["type"] == "SyntaxError"
        assert structured["error"]["line"] is not None
        assert isinstance(structured["error"]["line"], int)


class TestHarnessImportError:
    @pytest.mark.asyncio
    async def test_import_error_captured(self):
        result, structured = await _run_harness("import nonexistent_module_xyz_42")
        assert result.exit_code == 0
        assert structured["error"]["type"] == "ModuleNotFoundError"
        assert "nonexistent_module_xyz_42" in structured["error"]["message"]


class TestHarnessNameError:
    @pytest.mark.asyncio
    async def test_name_error_in_user_code(self):
        result, structured = await _run_harness("result = undefined_variable + 1")
        assert result.exit_code == 0
        assert structured["error"]["type"] == "NameError"
        assert structured["error"]["line"] == 1

    @pytest.mark.asyncio
    async def test_missing_graph_variable(self):
        result, structured = await _run_harness("x = 42")
        assert result.exit_code == 0
        assert structured["error"]["type"] == "NameError"
        assert "graph" in structured["error"]["message"].lower()


class TestHarnessRuntimeError:
    @pytest.mark.asyncio
    async def test_runtime_error_captured(self):
        code = "x = 1\ny = 1 / 0\n"
        result, structured = await _run_harness(code)
        assert result.exit_code == 0
        assert structured["error"]["type"] == "ZeroDivisionError"
        assert structured["error"]["line"] == 2

    @pytest.mark.asyncio
    async def test_attribute_error_captured(self):
        code = "x = None\nx.some_method()\n"
        result, structured = await _run_harness(code)
        assert result.exit_code == 0
        assert structured["error"]["type"] == "AttributeError"


# ---------------------------------------------------------------------------
# 4. CodegenResult dataclass basics
# ---------------------------------------------------------------------------


class TestCodegenResult:
    def test_success_defaults(self):
        cr = CodegenResult(success=True, graph={"v": 1}, source_code="code")
        assert cr.success is True
        assert cr.error_type is None
        assert cr.error_line is None

    def test_failure_defaults(self):
        cr = CodegenResult(success=False, error_type="SyntaxError", error_message="bad")
        assert cr.graph is None
        assert cr.source_code == ""
