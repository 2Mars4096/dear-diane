"""Validator executor — data validation at agent boundaries.

Evaluates a sequence of validation rules against incoming data and
routes to ``valid`` or ``invalid`` output ports based on results.
Supports required_keys, non_empty, schema_conformance, type_check,
and custom_expression rule types.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from dan.engine.conditions import ConditionError, evaluate_condition
from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.state import NodeStatus
from dan.models.control_flow import ValidatorNode, ValidationRule
from dan.models.nodes import NodeBase

logger = logging.getLogger(__name__)

_TYPE_NAME_MAP: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
}


# ---------------------------------------------------------------------------
# Dotpath resolver
# ---------------------------------------------------------------------------


def resolve_dotpath(data: dict, path: str) -> Any:
    """Resolve a dotted path against a nested dict, supporting list indexing.

    ``"a.b.c"`` resolves ``data["a"]["b"]["c"]``.
    ``"items.0.name"`` resolves ``data["items"][0]["name"]``.

    Raises ``KeyError`` with a descriptive message on missing path segments.
    """
    if not path:
        return data

    current: Any = data
    segments = path.split(".")

    for i, seg in enumerate(segments):
        traversed = ".".join(segments[:i])
        if isinstance(current, dict):
            if seg not in current:
                raise KeyError(
                    f"Key '{seg}' not found at '{traversed or '<root>'}' "
                    f"(available: {list(current.keys())})"
                )
            current = current[seg]
        elif isinstance(current, (list, tuple)):
            try:
                idx = int(seg)
            except ValueError:
                raise KeyError(
                    f"Expected integer index at '{traversed}', got '{seg}'"
                ) from None
            if idx < 0 or idx >= len(current):
                raise KeyError(
                    f"Index {idx} out of range at '{traversed}' "
                    f"(length {len(current)})"
                )
            current = current[idx]
        else:
            raise KeyError(
                f"Cannot traverse into {type(current).__name__} at '{traversed}'"
            )

    return current


# ---------------------------------------------------------------------------
# Violation dataclass
# ---------------------------------------------------------------------------


@dataclass
class ValidationViolation:
    rule_type: str
    dotpath: str | None
    message: str
    expected: Any | None = None
    actual: Any | None = None


# ---------------------------------------------------------------------------
# Per-rule-type evaluation functions
# ---------------------------------------------------------------------------


def _eval_required_keys(
    data: dict, rule: ValidationRule,
) -> list[ValidationViolation]:
    keys: list[str] = rule.config.get("keys", [])
    violations: list[ValidationViolation] = []
    for key in keys:
        try:
            resolve_dotpath(data, key)
        except KeyError:
            violations.append(ValidationViolation(
                rule_type="required_keys",
                dotpath=key,
                message=f"Required key '{key}' is missing",
            ))
    return violations


def _eval_non_empty(
    data: dict, rule: ValidationRule,
) -> list[ValidationViolation]:
    keys: list[str] = rule.config.get("keys", [])
    violations: list[ValidationViolation] = []
    for key in keys:
        try:
            value = resolve_dotpath(data, key)
        except KeyError:
            violations.append(ValidationViolation(
                rule_type="non_empty",
                dotpath=key,
                message=f"Key '{key}' is missing (required to be non-empty)",
            ))
            continue

        if value is None or value == "" or value == [] or value == {}:
            violations.append(ValidationViolation(
                rule_type="non_empty",
                dotpath=key,
                message=f"Value at '{key}' is empty",
                actual=value,
            ))
    return violations


def _eval_schema_conformance(
    data: dict, rule: ValidationRule,
) -> list[ValidationViolation]:
    schema: dict | None = rule.config.get("schema")
    schema_ref: str | None = rule.config.get("schema_ref")

    if schema is None and schema_ref is not None:
        schema = {"$ref": schema_ref}

    if schema is None:
        return [ValidationViolation(
            rule_type="schema_conformance",
            dotpath=None,
            message="No schema or schema_ref provided in rule config",
        )]

    try:
        import jsonschema
        try:
            jsonschema.validate(instance=data, schema=schema)
        except jsonschema.ValidationError as exc:
            path_str = ".".join(str(p) for p in exc.absolute_path) or None
            return [ValidationViolation(
                rule_type="schema_conformance",
                dotpath=path_str,
                message=exc.message,
                expected=exc.schema,
                actual=exc.instance,
            )]
    except ImportError:
        return _fallback_schema_check(data, schema)

    return []


def _fallback_schema_check(
    data: Any, schema: dict,
) -> list[ValidationViolation]:
    """Pure-Python fallback for basic type/required JSON Schema checks."""
    violations: list[ValidationViolation] = []

    schema_type = schema.get("type")
    if schema_type:
        type_map = {
            "object": dict, "array": list, "string": str,
            "integer": int, "number": (int, float), "boolean": bool,
        }
        expected_type = type_map.get(schema_type)
        if expected_type and not isinstance(data, expected_type):
            violations.append(ValidationViolation(
                rule_type="schema_conformance",
                dotpath=None,
                message=f"Expected type '{schema_type}', got '{type(data).__name__}'",
                expected=schema_type,
                actual=type(data).__name__,
            ))
            return violations

    if schema_type == "object" and isinstance(data, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in data:
                violations.append(ValidationViolation(
                    rule_type="schema_conformance",
                    dotpath=key,
                    message=f"Required property '{key}' is missing",
                    expected=key,
                ))

        properties = schema.get("properties", {})
        for prop_name, prop_schema in properties.items():
            if prop_name in data:
                sub = _fallback_schema_check(data[prop_name], prop_schema)
                for v in sub:
                    v.dotpath = f"{prop_name}.{v.dotpath}" if v.dotpath else prop_name
                violations.extend(sub)

    return violations


def _eval_type_check(
    data: dict, rule: ValidationRule,
) -> list[ValidationViolation]:
    checks: dict[str, str] = rule.config.get("checks", {})
    violations: list[ValidationViolation] = []
    for dotpath, expected_type_name in checks.items():
        try:
            value = resolve_dotpath(data, dotpath)
        except KeyError:
            violations.append(ValidationViolation(
                rule_type="type_check",
                dotpath=dotpath,
                message=f"Key '{dotpath}' not found for type check",
                expected=expected_type_name,
            ))
            continue

        expected_type = _TYPE_NAME_MAP.get(expected_type_name)
        if expected_type is None:
            violations.append(ValidationViolation(
                rule_type="type_check",
                dotpath=dotpath,
                message=f"Unknown type name '{expected_type_name}'",
                expected=expected_type_name,
                actual=type(value).__name__,
            ))
            continue

        if not isinstance(value, expected_type):
            violations.append(ValidationViolation(
                rule_type="type_check",
                dotpath=dotpath,
                message=(
                    f"Expected type '{expected_type_name}' at '{dotpath}', "
                    f"got '{type(value).__name__}'"
                ),
                expected=expected_type_name,
                actual=type(value).__name__,
            ))
    return violations


def _eval_custom_expression(
    data: dict, rule: ValidationRule,
) -> list[ValidationViolation]:
    expression: str | None = rule.config.get("expression")
    if not expression:
        return [ValidationViolation(
            rule_type="custom_expression",
            dotpath=None,
            message="No expression provided in rule config",
        )]

    try:
        result = evaluate_condition(expression, data)
    except ConditionError as exc:
        return [ValidationViolation(
            rule_type="custom_expression",
            dotpath=None,
            message=f"Expression error: {exc}",
        )]

    if not result:
        return [ValidationViolation(
            rule_type="custom_expression",
            dotpath=None,
            message=f"Expression '{expression}' evaluated to falsy",
            actual=result,
        )]
    return []


_RULE_EVALUATORS = {
    "required_keys": _eval_required_keys,
    "non_empty": _eval_non_empty,
    "schema_conformance": _eval_schema_conformance,
    "type_check": _eval_type_check,
    "custom_expression": _eval_custom_expression,
}


# ---------------------------------------------------------------------------
# ValidatorExecutor
# ---------------------------------------------------------------------------


class ValidatorExecutor:
    """Evaluates validation rules and routes to valid/invalid ports."""

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, ValidatorNode)

        data = self._extract_data(inputs)
        passthrough_outputs = self._passthrough_outputs(inputs)

        all_violations: list[ValidationViolation] = []

        for rule in node.validation_rules:
            evaluator = _RULE_EVALUATORS.get(rule.rule_type)
            if evaluator is None:
                all_violations.append(ValidationViolation(
                    rule_type=rule.rule_type,
                    dotpath=None,
                    message=f"Unknown rule type '{rule.rule_type}'",
                ))
                if node.strict_mode:
                    break
                continue

            violations = evaluator(data, rule)
            all_violations.extend(violations)

            if violations and node.strict_mode:
                break

        passed = len(all_violations) == 0
        violation_dicts = [asdict(v) for v in all_violations]

        await context.emit_event(
            event_type="validation_result",
            node_id=node.id,
            node_type="validator",
            data={
                "passed": passed,
                "violation_count": len(all_violations),
                "violations": violation_dicts,
                "rule_count": len(node.validation_rules),
            },
        )

        if node.on_failure == "halt" and not passed:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Validation halted: {len(all_violations)} violation(s)",
                metadata={"halt": True, "violations": violation_dicts},
            )

        if node.on_failure == "warn" or passed:
            if not passed:
                for v in all_violations:
                    logger.warning(
                        "Validator '%s' [warn]: %s (path=%s)",
                        node.id, v.message, v.dotpath,
                    )
            return NodeResult(
                outputs={"valid": data, **passthrough_outputs},
                status=NodeStatus.COMPLETED,
                metadata={"passed": passed, "violations": violation_dicts},
            )

        # on_failure == "route" and not passed
        return NodeResult(
            outputs={"invalid": {"data": data, "errors": violation_dicts}},
            status=NodeStatus.COMPLETED,
            metadata={"passed": False, "violations": violation_dicts},
        )

    @staticmethod
    def _extract_data(inputs: dict[str, Any]) -> dict[str, Any]:
        """Unwrap input data: use ``inputs["data"]`` if present, else whole dict.

        Non-dict inputs are wrapped as ``{"value": inputs}``.
        """
        if not isinstance(inputs, dict):
            return {"value": inputs}

        # Backward-compatible unwrapping:
        # - Preferred: validator payload arrives on "data"
        # - Legacy/default-input graphs may still send on "input"
        if len(inputs) == 1 and "data" in inputs:
            inner = inputs["data"]
            return inner if isinstance(inner, dict) else {"value": inner}
        if len(inputs) == 1 and "input" in inputs:
            inner = inputs["input"]
            return inner if isinstance(inner, dict) else {"value": inner}

        return inputs

    @staticmethod
    def _passthrough_outputs(inputs: dict[str, Any]) -> dict[str, Any]:
        """Expose original input ports on success/warn for boundary wiring.

        This keeps the canonical ``valid``/``invalid`` routing while allowing
        boundary validators to preserve arbitrary composite port mappings.
        """
        if not isinstance(inputs, dict):
            return {}

        if len(inputs) == 1 and "data" in inputs and isinstance(inputs["data"], dict):
            passthrough = dict(inputs["data"])
        else:
            passthrough = dict(inputs)

        passthrough.pop("valid", None)
        passthrough.pop("invalid", None)
        return passthrough
