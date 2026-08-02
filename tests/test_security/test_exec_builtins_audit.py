"""Audit: every exec()/eval() call in src/dan/ must restrict __builtins__.

Intentionally exempt locations:
- src/dan/engine/conditions.py  (uses _SAFE_BUILTINS, even stricter)

Note: src/dan/meta/planner.py has exec() only inside a string template
(_BUILDER_CODE_HARNESS) that runs in a SandboxRunner subprocess — the AST
parser does not find it as a direct call.

All other exec()/eval() sites must use {"__builtins__": ...} in the namespace.
If this test fails, either add builtins restriction to the new call or add
the file to EXEMPT_FILES with a justification comment.
"""

from __future__ import annotations

import ast
import pathlib

SRC_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "dan"

EXEMPT_FILES: dict[str, str] = {
    "engine/conditions.py": "eval() uses _SAFE_BUILTINS (stricter than _ALLOWED_BUILTINS)",
}


def _find_exec_eval_calls(root: pathlib.Path) -> list[dict]:
    """Return every exec()/eval() call site under *root*."""
    results = []
    for py_file in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text("utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = None
            if isinstance(func, ast.Name) and func.id in ("exec", "eval"):
                name = func.id
            if name is None:
                continue
            rel = py_file.relative_to(root)
            results.append(
                {
                    "file": str(rel),
                    "line": node.lineno,
                    "func": name,
                    "args_count": len(node.args),
                }
            )
    return results


def _has_builtins_restriction(filepath: pathlib.Path, lineno: int) -> bool:
    """Heuristic: check the surrounding context for __builtins__ restriction."""
    lines = filepath.read_text("utf-8").splitlines()
    start = max(0, lineno - 20)
    end = min(len(lines), lineno + 5)
    window = "\n".join(lines[start:end])
    return "__builtins__" in window


def test_all_exec_eval_calls_have_builtins_restriction():
    """Every non-exempt exec()/eval() must restrict __builtins__."""
    calls = _find_exec_eval_calls(SRC_ROOT)
    assert calls, "Expected to find at least one exec()/eval() call in src/dan/"

    violations = []
    for call in calls:
        if call["file"] in EXEMPT_FILES:
            continue
        full_path = SRC_ROOT / call["file"]
        if not _has_builtins_restriction(full_path, call["line"]):
            violations.append(
                f"  {call['file']}:{call['line']} — {call['func']}() "
                f"without __builtins__ restriction"
            )

    assert not violations, (
        "exec()/eval() calls without __builtins__ restriction found:\n"
        + "\n".join(violations)
        + "\n\nEither add builtins restriction or add the file to EXEMPT_FILES "
        "in this test with a justification."
    )


def test_exempt_files_are_still_used():
    """Prevent stale exemptions — each exempt file must still contain exec/eval."""
    calls = _find_exec_eval_calls(SRC_ROOT)
    used_files = {c["file"] for c in calls}
    stale = [f for f in EXEMPT_FILES if f not in used_files]
    assert not stale, (
        f"EXEMPT_FILES entries no longer contain exec()/eval() — remove: {stale}"
    )
