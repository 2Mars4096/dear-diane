"""Workflow-generation helper functions extracted from ``ChatManager``."""

from __future__ import annotations

import json
import logging
import pathlib
import re
from typing import Any

from dan.providers import CompletionResult

logger = logging.getLogger(__name__)


def parse_intent_from_result(result: CompletionResult) -> Any:
    """Extract a ``WorkflowIntent`` from a provider ``CompletionResult``."""

    from dan.meta.intent_schema import WorkflowIntent

    if result.tool_calls:
        for tc in result.tool_calls:
            func = tc.get("function", {})
            if func.get("name") == "emit_workflow_intent":
                try:
                    data = json.loads(func["arguments"])
                    return WorkflowIntent.model_validate(data)
                except (json.JSONDecodeError, KeyError, Exception):
                    pass

    text = result.text or ""
    if not text.strip():
        return None

    json_block_re = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)
    match = json_block_re.search(text)
    candidates: list[str] = []
    if match:
        candidates.append(match.group(1).strip())

    raw = text.strip()
    brace = raw.find("{")
    if brace >= 0:
        depth = 0
        for i, char in enumerate(raw[brace:], start=brace):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(raw[brace : i + 1])
                    break

    for raw_json in candidates:
        try:
            data = json.loads(raw_json)
            return WorkflowIntent.model_validate(data)
        except (json.JSONDecodeError, Exception):
            continue
    return None


def exec_deterministic_builder_code(
    code: str,
    *,
    log: logging.Logger | None = None,
) -> dict | None:
    """Execute deterministic intent-compiler builder code in-process."""

    active_log = log or logger
    try:
        from dan.executors.code import _ALLOWED_BUILTINS

        ns: dict[str, Any] = {"__builtins__": _ALLOWED_BUILTINS}
        exec(code, ns)  # noqa: S102
        for var_name in ("graph", "wf", "workflow", "g"):
            obj = ns.get(var_name)
            if obj is not None and hasattr(obj, "model_dump"):
                return obj.model_dump(mode="json")
        return None
    except Exception as exc:
        active_log.debug("Builder code execution failed: %s", exc)
        return None


async def sandbox_exec_builder_code(
    code: str,
    *,
    src_path: str | None = None,
    log: logging.Logger | None = None,
) -> tuple[dict | None, Any]:
    """Execute builder code in a sandboxed subprocess."""

    active_log = log or logger
    try:
        from dan.meta.planner import _BUILDER_CODE_HARNESS, _parse_codegen_result
        from dan.sandbox import SandboxConfig
        from dan.sandbox.runner import SandboxRunner

        runner = SandboxRunner()
        config = SandboxConfig(timeout_seconds=30, memory_mb=256)
        inputs = {
            "user_code": code,
            "src_path": src_path or str(pathlib.Path(__file__).resolve().parents[3]),
        }
        result, structured = await runner.run(_BUILDER_CODE_HARNESS, config, inputs)
        codegen_result = _parse_codegen_result(result, structured, code)
        if codegen_result.success and codegen_result.graph:
            return codegen_result.graph, None
        return None, codegen_result
    except Exception as exc:
        from dan.meta.planner import CodegenResult

        active_log.debug("Sandbox builder code execution failed: %s", exc)
        return None, CodegenResult(
            success=False,
            source_code=code,
            error_type="runtime_error",
            error_message=str(exc),
        )


def extract_code_from_response(text: str) -> str:
    """Extract Python code from an LLM response, stripping markdown fences."""

    fence_re = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)
    match = fence_re.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()
