"""Code executor — inline Python exec and subprocess sandbox for CodeOperator nodes."""

from __future__ import annotations

import builtins
import contextlib
import io
import logging
import os
from pathlib import Path
from typing import Any

from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.state import NodeStatus
from dan.models.legacy import CodeOperator
from dan.models.nodes import NodeBase
from dan.sandbox import SandboxConfig, SandboxResult
from dan.sandbox.runner import SandboxRunner

logger = logging.getLogger(__name__)

_STATIC_ALLOWED_BUILTINS: dict[str, Any] = {
    "__import__": builtins.__import__,
    "NameError": NameError,
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "AttributeError": AttributeError,
    "ImportError": ImportError,
    "ModuleNotFoundError": ModuleNotFoundError,
    "hasattr": hasattr,
    "getattr": getattr,
    "json": __import__("json"),
    "Path": __import__("pathlib").Path,
    "len": len,
    "min": min,
    "max": max,
    "abs": abs,
    "all": all,
    "any": any,
    "sum": sum,
    "round": round,
    "sorted": sorted,
    "reversed": reversed,
    "enumerate": enumerate,
    "zip": zip,
    "iter": iter,
    "next": next,
    "map": map,
    "filter": filter,
    "range": range,
    "isinstance": isinstance,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "print": print,
    "True": True,
    "False": False,
    "None": None,
}

# Compatibility alias for older diagnosis/runtime imports that still reference
# the historical module-level name.
_ALLOWED_BUILTINS = _STATIC_ALLOWED_BUILTINS

_runner = SandboxRunner()


def _workspace_root() -> Path | None:
    raw = str(os.environ.get("DAN_WORKSPACE_ROOT", "") or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except OSError:
        return None


def _workspace_open(file: Any, *args: Any, **kwargs: Any) -> Any:
    if isinstance(file, (str, os.PathLike)):
        path = Path(file).expanduser()
        if not path.is_absolute():
            workspace_root = _workspace_root()
            if workspace_root is not None:
                file = workspace_root / path
    return builtins.open(file, *args, **kwargs)


def _allowed_builtins() -> dict[str, Any]:
    allowed = dict(_STATIC_ALLOWED_BUILTINS)
    allowed["open"] = _workspace_open
    return allowed


def _default_for_schema(json_schema: dict | None) -> Any:
    """Return a type-appropriate default for an optional port based on its JSON schema."""
    if not json_schema:
        return None
    schema_type = json_schema.get("type")
    if schema_type == "array":
        return []
    if schema_type == "object":
        return {}
    if schema_type in ("number", "integer"):
        return 0
    if schema_type == "string":
        return ""
    if schema_type == "boolean":
        return False
    return None


def _materialize_outputs(node: CodeOperator, result_value: Any) -> dict[str, Any]:
    """Expand ``result`` into runtime outputs with narrow single-port compatibility.

    Historically, generated code often returned a bare ``result`` value even when the
    node declared one non-``result`` output port. Preserve the existing ``result`` key
    behavior and, when there is exactly one declared output port, mirror the value onto
    that port if it is otherwise missing.
    """
    if isinstance(result_value, dict):
        outputs = dict(result_value)
        outputs["result"] = result_value
    else:
        outputs = {"result": result_value}

    if len(node.output_ports) == 1:
        output_name = node.output_ports[0].name
        if output_name != "result" and output_name not in outputs:
            outputs[output_name] = result_value
    return outputs


class CodeExecutor:
    """Executes CodeOperator nodes via inline exec or subprocess sandbox.

    When ``sandbox_config`` is absent or ``mode="inline"``, the fast-path
    in-process ``exec()`` is used (deterministic, no subprocess overhead).
    When ``mode="subprocess"``, code runs in a child process via
    :class:`SandboxRunner` with configurable timeouts, memory caps, and
    output limits.
    """

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, CodeOperator)

        config = self._parse_config(node)

        if config.mode == "subprocess":
            return await self._execute_subprocess(node, inputs, context, config)
        return await self._execute_inline(node, inputs, context)

    # -- inline path (unchanged from original) --------------------------------

    async def _execute_inline(
        self,
        node: CodeOperator,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        if node.language != "python":
            return self._fail_result(
                node,
                f"Unsupported language: '{node.language}' (only 'python' is supported)",
            )

        namespace: dict[str, Any] = {"__builtins__": _allowed_builtins()}
        for port in node.input_ports:
            if port.name not in inputs and not port.required:
                inputs[port.name] = _default_for_schema(port.json_schema)
        namespace["inputs"] = dict(inputs)
        namespace.update(inputs)

        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        try:
            with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                exec(node.code, namespace)  # noqa: S102
        except Exception as exc:
            logger.exception("Code execution failed for node '%s'", node.id)
            stdout_str = stdout_capture.getvalue()
            stderr_str = stderr_capture.getvalue()
            if stdout_str or stderr_str:
                await context.emit_event(
                    event_type="code_output",
                    node_id=node.id,
                    node_type="code_operator",
                    data={"stdout": stdout_str[:2000], "stderr": stderr_str[:2000]},
                )
            return self._fail_result(node, f"Code execution failed: {exc}")

        stdout_str = stdout_capture.getvalue()
        stderr_str = stderr_capture.getvalue()
        if stdout_str or stderr_str:
            await context.emit_event(
                event_type="code_output",
                node_id=node.id,
                node_type="code_operator",
                data={"stdout": stdout_str[:2000], "stderr": stderr_str[:2000]},
            )

        if "result" in namespace:
            outputs = _materialize_outputs(node, namespace["result"])
        else:
            outputs = {}

        return NodeResult(outputs=outputs, status=NodeStatus.COMPLETED)

    # -- subprocess path ------------------------------------------------------

    async def _execute_subprocess(
        self,
        node: CodeOperator,
        inputs: dict[str, Any],
        context: ExecutionContext,
        config: SandboxConfig,
    ) -> NodeResult:
        await context.emit_event(
            event_type="sandbox_started",
            node_id=node.id,
            node_type="code_operator",
            data={
                "language": node.language,
                "mode": "subprocess",
                "timeout_seconds": config.timeout_seconds,
                "memory_mb": config.memory_mb,
            },
        )

        for port in node.input_ports:
            if port.name not in inputs and not port.required:
                inputs[port.name] = _default_for_schema(port.json_schema)

        sandbox_result, structured_output = await _runner.run(
            code=node.code, config=config, inputs=inputs,
        )

        await context.emit_event(
            event_type="sandbox_completed",
            node_id=node.id,
            node_type="code_operator",
            data={
                "exit_code": sandbox_result.exit_code,
                "duration_ms": sandbox_result.duration_ms,
                "memory_peak_mb": sandbox_result.memory_peak_mb,
                "truncated": sandbox_result.truncated,
                "output_size_bytes": len(sandbox_result.stdout) + len(sandbox_result.stderr),
            },
        )

        if sandbox_result.stdout or sandbox_result.stderr:
            await context.emit_event(
                event_type="code_output",
                node_id=node.id,
                node_type="code_operator",
                data={
                    "stdout": sandbox_result.stdout[:2000],
                    "stderr": sandbox_result.stderr[:2000],
                },
            )

        if sandbox_result.exit_code != 0:
            error_msg = sandbox_result.stderr or f"Process exited with code {sandbox_result.exit_code}"
            return self._fail_result(node, f"Subprocess execution failed: {error_msg}")

        if structured_output is not None:
            outputs = _materialize_outputs(node, structured_output)
        else:
            outputs = {}

        return NodeResult(outputs=outputs, status=NodeStatus.COMPLETED)

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _parse_config(node: CodeOperator) -> SandboxConfig:
        if not node.sandbox_config:
            return SandboxConfig()
        try:
            cfg = SandboxConfig(**node.sandbox_config)
        except Exception:
            logger.warning(
                "Invalid sandbox_config on node '%s', falling back to inline",
                node.id,
            )
            return SandboxConfig()
        if not cfg.language or cfg.language == "python":
            cfg = cfg.model_copy(update={"language": node.language})
        return cfg

    @staticmethod
    def _fail_result(node: CodeOperator, error: str) -> NodeResult:
        policy = node.retry_policy
        on_failure = policy.on_failure if policy else "error"
        if on_failure == "skip":
            return NodeResult(outputs={}, status=NodeStatus.SKIPPED)
        if on_failure == "halt":
            return NodeResult(
                outputs={}, status=NodeStatus.FAILED,
                error=error, metadata={"halt": True},
            )
        return NodeResult(outputs={}, status=NodeStatus.FAILED, error=error)
