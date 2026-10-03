"""Structured-output validation helpers for worker-core completions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from diane.worker.core.contracts import OutputContract
from diane.worker.structured_payload import parse_jsonish_payload


def _json_path(error: ValidationError) -> str:
    path = "$"
    for segment in error.absolute_path:
        if isinstance(segment, int):
            path += f"[{segment}]"
        else:
            token = str(segment)
            path += f".{token}" if token else ""
    return path


def _schema_error_message(exc: SchemaError) -> str:
    return " ".join(str(exc).strip().split()) or "invalid JSON schema"


def _validation_error_message(error: ValidationError) -> str:
    return f"{_json_path(error)}: {' '.join(str(error.message).strip().split())}"


@dataclass(slots=True)
class StructuredOutputValidation:
    """Validation result for one model-authored structured payload."""

    parsed: Any
    errors: list[str]

    @property
    def valid(self) -> bool:
        return not self.errors


def has_structured_output_schema(contract: OutputContract) -> bool:
    return isinstance(contract.output_schema, dict) and bool(contract.output_schema)


def render_output_schema(contract: OutputContract) -> str:
    if not has_structured_output_schema(contract):
        return ""
    return json.dumps(contract.output_schema, indent=2, sort_keys=True)


def validate_structured_output(
    raw: Any,
    contract: OutputContract,
) -> StructuredOutputValidation:
    parsed = parse_jsonish_payload(raw)
    if not has_structured_output_schema(contract):
        return StructuredOutputValidation(parsed=parsed, errors=[])

    try:
        validator = Draft202012Validator(contract.output_schema or {})
    except SchemaError as exc:
        return StructuredOutputValidation(
            parsed=parsed,
            errors=[f"$schema: {_schema_error_message(exc)}"],
        )

    errors = sorted(
        (_validation_error_message(error) for error in validator.iter_errors(parsed)),
        key=str,
    )
    return StructuredOutputValidation(parsed=parsed, errors=errors)


__all__ = [
    "StructuredOutputValidation",
    "has_structured_output_schema",
    "render_output_schema",
    "validate_structured_output",
]
