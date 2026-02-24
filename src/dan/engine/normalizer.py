"""Output normalization — parse/validate/re-prompt pipeline for LLM outputs.

Every LLM operator output passes through this pipeline:
  1. Extract JSON from raw LLM text (handles markdown fences, partial JSON)
  2. Validate against the declared output_json_schema
  3. On failure, produce an error message suitable for re-prompting
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class NormResult:
    """Result of a normalization attempt."""

    success: bool
    data: dict[str, Any] | None = None
    error_message: str | None = None


_JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)


def _extract_json(text: str) -> str | None:
    """Try to extract a JSON object/array from *text*.

    Strategies (in order):
      1. Fenced code block (```json ... ```)
      2. First { ... } or [ ... ] substring
      3. The raw text itself
    """
    m = _JSON_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()

    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = text.find(start_char)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == start_char:
                depth += 1
            elif c == end_char:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]

    return text.strip() if text.strip() else None


def _validate_type(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    """MVP schema validation — checks type and required fields recursively."""
    errors: list[str] = []
    schema_type = schema.get("type")

    if schema_type == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected object, got {type(value).__name__}"]
        required = set(schema.get("required", []))
        properties = schema.get("properties", {})
        for field_name in required:
            if field_name not in value:
                errors.append(f"{path}.{field_name}: required field missing")
        for field_name, field_schema in properties.items():
            if field_name in value:
                errors.extend(
                    _validate_type(value[field_name], field_schema, f"{path}.{field_name}")
                )

    elif schema_type == "array":
        if not isinstance(value, list):
            return [f"{path}: expected array, got {type(value).__name__}"]
        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(value):
                errors.extend(_validate_type(item, items_schema, f"{path}[{i}]"))

    elif schema_type == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: expected string, got {type(value).__name__}")

    elif schema_type == "number":
        if not isinstance(value, (int, float)):
            errors.append(f"{path}: expected number, got {type(value).__name__}")

    elif schema_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"{path}: expected integer, got {type(value).__name__}")

    elif schema_type == "boolean":
        if not isinstance(value, bool):
            errors.append(f"{path}: expected boolean, got {type(value).__name__}")

    return errors


class OutputNormalizer:
    """Stateless normalizer that extracts and validates structured output."""

    @staticmethod
    def normalize(raw_text: str, schema: dict[str, Any]) -> NormResult:
        """Attempt to extract and validate JSON from *raw_text*.

        Returns a NormResult with success=True and the parsed data on
        success, or success=False and an error_message suitable for
        re-prompting the LLM.
        """
        json_str = _extract_json(raw_text)
        if json_str is None:
            return NormResult(
                success=False,
                error_message=(
                    "Your response did not contain valid JSON. "
                    "Please respond with a JSON object matching this schema:\n"
                    f"{json.dumps(schema, indent=2)}"
                ),
            )

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as exc:
            return NormResult(
                success=False,
                error_message=(
                    f"Your response contained invalid JSON: {exc}. "
                    "Please respond with a valid JSON object matching this schema:\n"
                    f"{json.dumps(schema, indent=2)}"
                ),
            )

        validation_errors = _validate_type(parsed, schema, "$")
        if validation_errors:
            return NormResult(
                success=False,
                error_message=(
                    "Your JSON output did not match the expected schema.\n"
                    f"Errors: {'; '.join(validation_errors)}\n"
                    f"Expected schema:\n{json.dumps(schema, indent=2)}\n"
                    "Please fix the output and try again."
                ),
            )

        return NormResult(success=True, data=parsed)
