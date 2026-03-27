"""Workflow-generation helper functions extracted from ``ChatManager``."""

from __future__ import annotations

import ast
import json
import logging
import pathlib
import re
from typing import Any

from dan.providers import CompletionResult

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)
_UNQUOTED_JSON_KEY_RE = re.compile(r'([{\[,]\s*)([A-Za-z_][A-Za-z0-9_-]*)(\s*:)', re.MULTILINE)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _get_mapping_value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _iter_json_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    for block in _JSON_BLOCK_RE.findall(text):
        candidate = block.strip()
        if candidate and candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)

    raw = text.strip()
    search_start = 0
    while True:
        brace = raw.find("{", search_start)
        if brace < 0:
            break
        depth = 0
        for index, char in enumerate(raw[brace:], start=brace):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = raw[brace : index + 1].strip()
                    if candidate and candidate not in seen:
                        candidates.append(candidate)
                        seen.add(candidate)
                    search_start = index + 1
                    break
        else:
            break
    return candidates


def _repair_json_text(raw_text: str) -> str:
    repaired = raw_text.strip()
    fence_match = _JSON_BLOCK_RE.fullmatch(repaired)
    if fence_match:
        repaired = fence_match.group(1).strip()
    repaired = _TRAILING_COMMA_RE.sub(r"\1", repaired)
    repaired = _UNQUOTED_JSON_KEY_RE.sub(r'\1"\2"\3', repaired)
    return repaired


def _decode_json_like_payload(raw_payload: Any) -> dict[str, Any] | None:
    if isinstance(raw_payload, dict):
        return raw_payload
    if not isinstance(raw_payload, str):
        return None

    candidates = [raw_payload.strip()]
    repaired = _repair_json_text(raw_payload)
    if repaired and repaired not in candidates:
        candidates.append(repaired)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            loaded = json.loads(candidate)
        except json.JSONDecodeError:
            try:
                loaded = ast.literal_eval(candidate)
            except (SyntaxError, ValueError):
                continue
        if isinstance(loaded, dict):
            return loaded
    return None


def parse_intent_from_result(result: CompletionResult) -> Any:
    """Extract a ``WorkflowIntent`` from a provider ``CompletionResult``."""

    from dan.meta.intent_schema import WorkflowIntent

    if result.tool_calls:
        for tc in result.tool_calls:
            func = _get_mapping_value(tc, "function")
            if _get_mapping_value(func, "name") != "emit_workflow_intent":
                continue
            data = _decode_json_like_payload(_get_mapping_value(func, "arguments"))
            if data is None:
                continue
            try:
                return WorkflowIntent.model_validate(data)
            except Exception:
                continue

    text = result.text or ""
    if not text.strip():
        return None

    for raw_json in _iter_json_candidates(text):
        data = _decode_json_like_payload(raw_json)
        if data is None:
            continue
        try:
            return WorkflowIntent.model_validate(data)
        except Exception:
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
