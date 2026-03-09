"""Built-in tool: sandboxed Python execution for math, data manipulation, and quick scripts."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

TOOL_METADATA = {
    "tool_id": "python_eval",
    "description": (
        "Execute Python code in a sandboxed subprocess. Use for calculations, "
        "data manipulation, or quick scripts. The code must assign to a variable "
        "named 'result' to return output. Standard library is available. "
        "Timeout defaults to 30 seconds."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute. Must assign to 'result' for return value.",
            },
            "timeout": {
                "type": "integer",
                "description": "Maximum execution time in seconds.",
                "default": 30,
            },
        },
        "required": ["code"],
    },
    "examples": [
        {
            "input": {"code": "result = sum(range(1, 101))"},
            "output": {"result": 5050, "stdout": "", "stderr": ""},
        },
        {
            "input": {"code": "import math\nresult = math.pi * 5**2"},
            "output": {"result": 78.53981633974483, "stdout": "", "stderr": ""},
        },
    ],
    "category": "system",
    "returns": "dict with result, stdout, stderr, and optional error",
}


async def python_eval(code: str, timeout: int = 30, **_kwargs) -> dict:
    from dan.sandbox import SandboxConfig, SandboxResult
    from dan.sandbox.runner import SandboxRunner

    config = SandboxConfig(
        timeout_seconds=timeout,
        memory_mb=256,
    )
    runner = SandboxRunner()

    wrapper = (
        "import sys as _sys, json as _json, io as _io\n"
        "_buf = _io.StringIO()\n"
        "_orig_print = print\n"
        "def print(*a, **kw):\n"
        "    kw.setdefault('file', _buf)\n"
        "    _orig_print(*a, **kw)\n"
        "result = None\n"
        f"{code}\n"
        "_orig_print(_json.dumps({{'result': result, 'stdout': _buf.getvalue()}}), file=_sys.stdout)\n"
    )

    sandbox_result: SandboxResult
    sandbox_result, _ = await runner.run(wrapper, config, {})

    if sandbox_result.exit_code != 0:
        return {
            "result": None,
            "stdout": sandbox_result.stdout or "",
            "stderr": sandbox_result.stderr or "",
            "error": sandbox_result.stderr.strip() if sandbox_result.stderr else f"Exit code {sandbox_result.exit_code}",
        }

    try:
        last_line = sandbox_result.stdout.strip().rsplit("\n", 1)[-1]
        parsed = json.loads(last_line)
        return {
            "result": parsed.get("result"),
            "stdout": parsed.get("stdout", ""),
            "stderr": sandbox_result.stderr or "",
        }
    except (ValueError, IndexError):
        return {
            "result": None,
            "stdout": sandbox_result.stdout or "",
            "stderr": sandbox_result.stderr or "",
        }
