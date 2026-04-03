"""Deterministic structural linting."""

from __future__ import annotations

import re
from typing import Any

from jsonschema import Draft7Validator

from dan.linter.autofix.strategies import apply_structural_autofixes
from dan.linter.config import RuleSeverity, StructuralConfig
from dan.linter.result import LintDiagnostic
from dan.linter.rules import RuleSpec, Tier

STRUCTURAL_RULES = (
    RuleSpec(code="schema_conformance", tier=Tier.STRUCTURAL, autofixable=True),
    RuleSpec(code="required_keys", tier=Tier.STRUCTURAL, autofixable=True),
    RuleSpec(code="non_empty", tier=Tier.STRUCTURAL, autofixable=False),
    RuleSpec(code="string_max_length", tier=Tier.STRUCTURAL, autofixable=True),
    RuleSpec(code="numeric_range", tier=Tier.STRUCTURAL, autofixable=True),
    RuleSpec(code="format_pattern", tier=Tier.STRUCTURAL, autofixable=False),
)


def _schema_property(config: StructuralConfig, key: str) -> dict[str, Any]:
    schema_props = (config.json_schema or {}).get("properties", {})
    value = schema_props.get(key)
    return value if isinstance(value, dict) else {}


def _coerce_number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _collect_structural_diagnostics(
    data: Any,
    config: StructuralConfig,
    *,
    severity: RuleSeverity,
) -> list[LintDiagnostic]:
    diagnostics: list[LintDiagnostic] = []

    if config.json_schema is not None:
        validator = Draft7Validator(config.json_schema)
        for error in sorted(validator.iter_errors(data), key=lambda err: list(err.path)):
            metadata = {
                "path": list(error.path),
                "validator": error.validator,
            }
            if error.validator == "type":
                metadata["expected_type"] = error.validator_value
            diagnostics.append(
                LintDiagnostic(
                    code="schema_conformance",
                    message=error.message,
                    tier=Tier.STRUCTURAL,
                    severity=severity,
                    field_path=list(error.path),
                    metadata=metadata,
                )
            )

    if isinstance(data, dict):
        missing = [key for key in config.required_keys if key not in data]
        if missing:
            diagnostics.append(
                LintDiagnostic(
                    code="required_keys",
                    message=f"Missing required keys: {missing}",
                    tier=Tier.STRUCTURAL,
                    severity=severity,
                    metadata={"missing": list(missing)},
                )
            )

        for key in config.non_empty_keys:
            if key in data and data[key] in ("", [], {}, None):
                diagnostics.append(
                    LintDiagnostic(
                        code="non_empty",
                        message=f"Key '{key}' must be non-empty",
                        tier=Tier.STRUCTURAL,
                        severity=severity,
                        field_path=[key],
                        metadata={"key": key},
                    )
                )

        for key, limit in config.string_max_lengths.items():
            if key not in data or not isinstance(data[key], str):
                continue
            if len(data[key]) <= limit:
                continue
            diagnostics.append(
                LintDiagnostic(
                    code="string_max_length",
                    message=f"Key '{key}' exceeds max length {limit}",
                    tier=Tier.STRUCTURAL,
                    severity=severity,
                    field_path=[key],
                    metadata={"key": key, "max_length": limit},
                )
            )

        for key, bounds in config.ranges.items():
            if key not in data:
                continue
            numeric = _coerce_number(data[key])
            if numeric is None:
                continue
            minimum = bounds.get("minimum")
            maximum = bounds.get("maximum")
            if minimum is not None and numeric < minimum:
                diagnostics.append(
                    LintDiagnostic(
                        code="numeric_range",
                        message=f"Key '{key}' is below minimum {minimum}",
                        tier=Tier.STRUCTURAL,
                        severity=severity,
                        field_path=[key],
                        metadata={"key": key, "minimum": minimum, "maximum": maximum},
                    )
                )
                continue
            if maximum is not None and numeric > maximum:
                diagnostics.append(
                    LintDiagnostic(
                        code="numeric_range",
                        message=f"Key '{key}' exceeds maximum {maximum}",
                        tier=Tier.STRUCTURAL,
                        severity=severity,
                        field_path=[key],
                        metadata={"key": key, "minimum": minimum, "maximum": maximum},
                    )
                )

        for key, pattern in config.format_patterns.items():
            if key not in data or not isinstance(data[key], str):
                continue
            if re.fullmatch(pattern, data[key]):
                continue
            diagnostics.append(
                LintDiagnostic(
                    code="format_pattern",
                    message=f"Key '{key}' does not match required pattern",
                    tier=Tier.STRUCTURAL,
                    severity=severity,
                    field_path=[key],
                    metadata={"key": key, "pattern": pattern},
                )
            )

    return diagnostics


def validate_structural(
    data: Any,
    config: StructuralConfig,
    *,
    severity: RuleSeverity,
    autofix: list[str] | None = None,
) -> tuple[list[LintDiagnostic], Any | None, list[str]]:
    diagnostics = _collect_structural_diagnostics(data, config, severity=severity)
    current_data = data
    applied_fixes: list[str] = []
    autofix = autofix or []
    if not autofix:
        return diagnostics, None, []

    max_passes = max(1, len(set(autofix))) * 2
    for _ in range(max_passes):
        if not diagnostics:
            break
        next_data, cycle_fixes, _descriptions = apply_structural_autofixes(
            current_data,
            diagnostics,
            config,
            autofix,
        )
        if not cycle_fixes:
            break
        current_data = next_data
        for fix_name in cycle_fixes:
            if fix_name not in applied_fixes:
                applied_fixes.append(fix_name)
        diagnostics = _collect_structural_diagnostics(current_data, config, severity=severity)

    fixed_data: Any | None = current_data if applied_fixes else None
    return diagnostics, fixed_data, applied_fixes


__all__ = ["STRUCTURAL_RULES", "validate_structural"]
