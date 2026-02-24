"""Safe expression evaluator for IfElse/WhileLoop condition strings.

Evaluates Python expressions in a restricted namespace — no __builtins__,
only whitelisted safe functions. Variables are populated from upstream
port data (the node's resolved inputs).
"""

from __future__ import annotations

from typing import Any

_SAFE_BUILTINS: dict[str, Any] = {
    "len": len,
    "min": min,
    "max": max,
    "abs": abs,
    "all": all,
    "any": any,
    "sum": sum,
    "round": round,
    "sorted": sorted,
    "isinstance": isinstance,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "True": True,
    "False": False,
    "None": None,
}


class ConditionError(Exception):
    """Raised when condition evaluation fails."""


def evaluate_condition(expr: str, variables: dict[str, Any]) -> bool:
    """Evaluate *expr* as a Python expression and return a bool.

    *variables* maps names to values; these are the only names visible
    inside the expression (plus the safe builtins above).

    Raises ConditionError on any evaluation failure.
    """
    namespace: dict[str, Any] = {"__builtins__": {}}
    namespace.update(_SAFE_BUILTINS)
    namespace.update(variables)

    try:
        result = eval(expr, namespace)  # noqa: S307
    except Exception as exc:
        raise ConditionError(
            f"Failed to evaluate condition '{expr}': {exc}"
        ) from exc

    return bool(result)
