"""Safe expression evaluator for IfElse/WhileLoop condition strings.

Evaluates Python expressions in a restricted namespace — no __builtins__,
only whitelisted safe functions. Variables are populated from upstream
port data (the node's resolved inputs).
"""

from __future__ import annotations

import re
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
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConditionError(Exception):
    """Raised when condition evaluation fails."""


def _collect_reducer_items(inputs: Any) -> list[Any]:
    if isinstance(inputs, dict):
        raw_items = list(inputs.values())
    elif isinstance(inputs, (list, tuple)):
        raw_items = list(inputs)
    else:
        raw_items = [inputs]

    flattened: list[Any] = []
    for item in raw_items:
        if isinstance(item, (list, tuple)):
            flattened.extend(item)
        else:
            flattened.append(item)
    return flattened


def _concatenate_reducer(inputs: Any) -> Any:
    items = _collect_reducer_items(inputs)
    if items and all(isinstance(item, str) for item in items):
        return "".join(items)
    if items and all(isinstance(item, dict) for item in items):
        parts: list[str] = []
        for item in items:
            part: str | None = None
            for key in ("text", "content", "result"):
                value = item.get(key)
                if isinstance(value, str):
                    part = value
                    break
            if part is None:
                parts = []
                break
            parts.append(part)
        if parts:
            return "".join(parts)
    return items


_NAMED_REDUCERS: dict[str, Any] = {
    "concatenate": _concatenate_reducer,
    "concat": _concatenate_reducer,
}


def evaluate_condition(expr: str, variables: dict[str, Any]) -> bool:
    """Evaluate *expr* as a Python expression and return a bool.

    *variables* maps names to values; these are the only names visible
    inside the expression (plus the safe builtins above).

    Raises ConditionError on any evaluation failure.
    """
    return bool(evaluate_expression(expr, variables))


def evaluate_expression(expr: str, variables: dict[str, Any]) -> Any:
    """Evaluate *expr* as a Python expression and return the raw result.

    *variables* maps names to values; these are the only names visible
    inside the expression (plus the safe builtins above).

    Raises ConditionError on any evaluation failure.
    """
    namespace: dict[str, Any] = {"__builtins__": {}}
    namespace.update(_SAFE_BUILTINS)
    namespace.update(variables)

    try:
        return eval(expr, namespace)  # noqa: S307
    except Exception as exc:
        raise ConditionError(
            f"Failed to evaluate expression '{expr}': {exc}"
        ) from exc


def evaluate_reducer(expr: str, inputs: Any) -> Any:
    """Evaluate a reducer expression or named reducer over *inputs*."""
    normalized = str(expr or "").strip()
    lowered = normalized.lower()
    if _IDENTIFIER_RE.match(normalized) and lowered in _NAMED_REDUCERS:
        return _NAMED_REDUCERS[lowered](inputs)
    return evaluate_expression(normalized, {"inputs": inputs})
