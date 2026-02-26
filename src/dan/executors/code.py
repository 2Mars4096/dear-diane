"""Code executor — inline Python exec and subprocess sandbox for CodeOperator nodes."""

from __future__ import annotations

import builtins
import contextlib
import io
import logging
from typing import Any

from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.state import NodeStatus
from dan.models.nodes import CodeOperator, NodeBase
from dan.sandbox import SandboxConfig, SandboxResult
from dan.sandbox.runner import SandboxRunner

logger = logging.getLogger(__name__)

_ALLOWED_BUILTINS: dict[str, Any] = {
    "__import__": builtins.__import__,
    "NameError": NameError,
    "Exception": Exception,
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

_runner = SandboxRunner()


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

        namespace: dict[str, Any] = {"__builtins__": _ALLOWED_BUILTINS}
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
            result_value = namespace["result"]
            if isinstance(result_value, dict):
                outputs = result_value
            else:
                outputs = {"result": result_value}
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
            outputs = structured_output if isinstance(structured_output, dict) else {"result": structured_output}
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
