"""Unit tests for server-side workflow builder execution helpers."""

from __future__ import annotations

import json
from typing import Any

import pytest

from dan.meta.planner import CodegenResult
from dan.providers import CompletionResult

workflow_builder_execution = pytest.importorskip(
    "dan.server.agent_runtime.workflow_builder_execution"
)


def test_parse_intent_from_result_tool_call_json() -> None:
    """Tool-call arguments should deserialize into a WorkflowIntent."""
    result = CompletionResult(
        text="",
        tool_calls=[
            {
                "function": {
                    "name": "emit_workflow_intent",
                    "arguments": json.dumps(
                        {
                            "goal": "Build a chain",
                            "stages": [
                                {
                                    "name": "step1",
                                    "description": "First step",
                                    "stage_type": "transform",
                                }
                            ],
                        }
                    ),
                }
            }
        ],
    )

    intent = workflow_builder_execution.parse_intent_from_result(result)

    assert intent is not None
    assert intent.goal == "Build a chain"
    assert len(intent.stages) == 1
    assert intent.stages[0].name == "step1"
    assert intent.stages[0].stage_type.value == "transform"


def test_parse_intent_from_result_fenced_json_text() -> None:
    """Fenced JSON in the assistant content should be parsed as a WorkflowIntent."""
    result = CompletionResult(
        text=(
            "Here is the workflow intent:\n\n"
            "```json\n"
            '{"goal": "Build a chain", "stages": ['
            '{"name": "step1", "description": "First step", "stage_type": "transform"}'
            "]}\n"
            "```\n"
            "\nLet me know if you need changes."
        ),
        tool_calls=None,
    )

    intent = workflow_builder_execution.parse_intent_from_result(result)

    assert intent is not None
    assert intent.goal == "Build a chain"
    assert [stage.name for stage in intent.stages] == ["step1"]


def test_extract_code_from_response_strips_fences() -> None:
    """Python fences should be removed while preserving the code body."""
    text = (
        "Here is the builder:\n"
        "```python\n"
        "graph = {}\n"
        "```\n"
        "That should work."
    )

    code = workflow_builder_execution.extract_code_from_response(text)

    assert code == "graph = {}"


def test_exec_deterministic_builder_code_returns_graph_dict() -> None:
    """Deterministic builder code should return the dumped graph payload."""
    code = (
        "class Graph:\n"
        "    def model_dump(self, mode='json'):\n"
        "        return {'nodes': [{'id': 'n1'}], 'edges': []}\n"
        "\n"
        "graph = Graph()\n"
    )

    graph = workflow_builder_execution.exec_deterministic_builder_code(code)

    assert graph == {"nodes": [{"id": "n1"}], "edges": []}


@pytest.mark.asyncio
async def test_sandbox_exec_builder_code_returns_structured_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sandbox failures should be converted into a structured CodegenResult."""

    async def fake_run(self: Any, code: str, config: Any, inputs: dict[str, Any]) -> Any:
        raise RuntimeError("sandbox bootstrap failed")

    monkeypatch.setattr(workflow_builder_execution.SandboxRunner, "run", fake_run)

    graph, codegen = await workflow_builder_execution.sandbox_exec_builder_code(
        "graph = {}"
    )

    assert graph is None
    assert codegen is not None
    assert isinstance(codegen, CodegenResult)
    assert codegen.success is False
    assert codegen.error_type == "runtime_error"
    assert codegen.error_message == "sandbox bootstrap failed"
