"""Autofix strategies for lint diagnostics."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Iterable

from dan.linter.autofix import AutoFix, FixResult
from dan.linter.config import LintConfig, StructuralConfig
from dan.linter.result import LintDiagnostic

DETERMINISTIC_FIX_ORDER = (
    "fill_defaults",
    "clamp",
    "truncate",
    "coerce",
)
MODEL_AUTOFIX_ORDER = (
    "retry_with_feedback",
    "refocus",
)
_SEMANTIC_REFOCUS_CODES = {"semantic_similarity", "keyword_presence", "entity_presence"}


def _clone(value: Any) -> Any:
    return deepcopy(value)


def _preview_payload(value: Any, *, limit: int = 1200) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, indent=2, sort_keys=True, default=str)
        except Exception:
            text = str(value)
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _get_path(payload: Any, path: Iterable[Any]) -> Any:
    current = payload
    for part in path:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
            current = current[part]
        else:
            return None
    return current


def _set_path(payload: Any, path: list[Any], value: Any) -> bool:
    if not path:
        return False
    current = payload
    for part in path[:-1]:
        if isinstance(current, dict):
            if part not in current or not isinstance(current[part], (dict, list)):
                return False
            current = current[part]
        elif isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
            current = current[part]
        else:
            return False
    last = path[-1]
    if isinstance(current, dict):
        current[last] = value
        return True
    if isinstance(current, list) and isinstance(last, int) and 0 <= last < len(current):
        current[last] = value
        return True
    return False


def _coerce_scalar(value: Any, expected_type: str) -> Any:
    normalized = expected_type.strip().lower()
    if normalized == "integer":
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped and stripped.lstrip("+-").isdigit():
                return int(stripped)
        return value
    if normalized == "number":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            try:
                return float(stripped)
            except (TypeError, ValueError):
                return value
        return value
    if normalized == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            stripped = value.strip().lower()
            if stripped in {"true", "1", "yes"}:
                return True
            if stripped in {"false", "0", "no"}:
                return False
        return value
    if normalized == "string":
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        return str(value)
    return value


def _default_for_schema(schema: dict[str, Any]) -> Any:
    if "default" in schema:
        return deepcopy(schema["default"])
    expected_type = schema.get("type")
    if expected_type == "string":
        return ""
    if expected_type == "array":
        return []
    if expected_type == "object":
        return {}
    if expected_type in {"integer", "number"}:
        return 0
    if expected_type == "boolean":
        return False
    return None


def _schema_property(config: StructuralConfig, key: str) -> dict[str, Any]:
    schema_props = (config.json_schema or {}).get("properties", {})
    value = schema_props.get(key)
    return value if isinstance(value, dict) else {}


def _truncate_to_boundary(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    candidate = value[:limit]
    whitespace_index = max(candidate.rfind(" "), candidate.rfind("\n"), candidate.rfind("\t"))
    if whitespace_index >= max(1, limit // 2):
        return candidate[:whitespace_index].rstrip()
    return candidate


def _collect_unique_metadata_values(
    diagnostics: list[LintDiagnostic],
    key: str,
) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        items = diagnostic.metadata.get(key, [])
        if isinstance(items, str):
            items = [items]
        if not isinstance(items, list):
            continue
        for item in items:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            values.append(text)
    return values


def _diagnostic_bullets(diagnostics: list[LintDiagnostic]) -> list[str]:
    return [
        f"- [{diagnostic.severity.value}] {diagnostic.code}: {diagnostic.message}"
        for diagnostic in diagnostics
    ]


def _semantic_focus_text(config: LintConfig, diagnostics: list[LintDiagnostic]) -> str:
    focus_parts: list[str] = []
    if config.intent is not None and config.intent.intent:
        focus_parts.append(f"Downstream intent: {config.intent.intent}")
    if config.semantic is not None:
        if config.semantic.reference_text:
            focus_parts.append(f"Reference topic: {config.semantic.reference_text}")
        if config.semantic.topic_keywords:
            focus_parts.append(
                f"Required topic keywords: {', '.join(config.semantic.topic_keywords)}"
            )
        if config.semantic.required_entities:
            focus_parts.append(
                f"Required entities: {', '.join(config.semantic.required_entities)}"
            )
    missing = _collect_unique_metadata_values(diagnostics, "missing")
    if missing:
        focus_parts.append(f"Still missing: {', '.join(missing)}")
    return "\n".join(focus_parts)


class FillDefaultsFix(AutoFix):
    name = "fill_defaults"

    def fix(self, data: Any, diagnostic: LintDiagnostic, config: StructuralConfig, **deps: Any) -> FixResult:
        if diagnostic.code != "required_keys" or not isinstance(data, dict):
            return FixResult(fixed=False, data=data, description="")
        missing = diagnostic.metadata.get("missing", [])
        if not missing:
            return FixResult(fixed=False, data=data, description="")
        working = _clone(data)
        changed: list[str] = []
        for key in missing:
            if key in working:
                continue
            working[key] = _default_for_schema(_schema_property(config, str(key)))
            changed.append(str(key))
        return FixResult(
            fixed=bool(changed),
            data=working if changed else data,
            description=f"filled defaults for {', '.join(changed)}" if changed else "",
        )


class TruncateFix(AutoFix):
    name = "truncate"

    def fix(self, data: Any, diagnostic: LintDiagnostic, config: StructuralConfig, **deps: Any) -> FixResult:
        if diagnostic.code != "string_max_length" or not isinstance(data, dict):
            return FixResult(fixed=False, data=data, description="")
        key = diagnostic.metadata.get("key")
        limit = diagnostic.metadata.get("max_length")
        if not isinstance(key, str) or not isinstance(limit, int):
            return FixResult(fixed=False, data=data, description="")
        value = data.get(key)
        if not isinstance(value, str) or len(value) <= limit:
            return FixResult(fixed=False, data=data, description="")
        working = _clone(data)
        working[key] = _truncate_to_boundary(value, limit)
        return FixResult(
            fixed=True,
            data=working,
            description=f"truncated '{key}' to max length {limit}",
        )


class ClampFix(AutoFix):
    name = "clamp"

    def fix(self, data: Any, diagnostic: LintDiagnostic, config: StructuralConfig, **deps: Any) -> FixResult:
        if diagnostic.code != "numeric_range" or not isinstance(data, dict):
            return FixResult(fixed=False, data=data, description="")
        key = diagnostic.metadata.get("key")
        minimum = diagnostic.metadata.get("minimum")
        maximum = diagnostic.metadata.get("maximum")
        if not isinstance(key, str) or key not in data:
            return FixResult(fixed=False, data=data, description="")
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return FixResult(fixed=False, data=data, description="")
        corrected = value
        if minimum is not None and value < minimum:
            corrected = minimum
        if maximum is not None and value > maximum:
            corrected = maximum
        if corrected == value:
            return FixResult(fixed=False, data=data, description="")
        working = _clone(data)
        working[key] = corrected
        return FixResult(
            fixed=True,
            data=working,
            description=f"clamped '{key}' into valid range",
        )


class CoerceFix(AutoFix):
    name = "coerce"

    def fix(self, data: Any, diagnostic: LintDiagnostic, config: StructuralConfig, **deps: Any) -> FixResult:
        if diagnostic.code != "schema_conformance":
            return FixResult(fixed=False, data=data, description="")
        if diagnostic.metadata.get("validator") != "type":
            return FixResult(fixed=False, data=data, description="")
        path = diagnostic.metadata.get("path", [])
        expected_type = diagnostic.metadata.get("expected_type")
        if not isinstance(path, list) or not path:
            return FixResult(fixed=False, data=data, description="")
        if isinstance(expected_type, list):
            target_type = next((item for item in expected_type if item != "null"), None)
        else:
            target_type = expected_type
        if not isinstance(target_type, str):
            return FixResult(fixed=False, data=data, description="")
        current_value = _get_path(data, path)
        if current_value is None and _get_path(data, path) is None:
            return FixResult(fixed=False, data=data, description="")
        coerced = _coerce_scalar(current_value, target_type)
        if coerced is current_value or coerced == current_value:
            return FixResult(fixed=False, data=data, description="")
        working = _clone(data)
        if not _set_path(working, path, coerced):
            return FixResult(fixed=False, data=data, description="")
        return FixResult(
            fixed=True,
            data=working,
            description=f"coerced {'.'.join(map(str, path))} to {target_type}",
        )


class RetryWithFeedbackFix(AutoFix):
    name = "retry_with_feedback"

    def fix(self, data: Any, diagnostic: LintDiagnostic, config: LintConfig, **deps: Any) -> FixResult:
        diagnostics = deps.get("diagnostics")
        if not isinstance(diagnostics, list) or not diagnostics:
            diagnostics = [diagnostic]
        blocking = [item for item in diagnostics if item.severity.value == "error"]
        relevant = blocking or diagnostics
        if not relevant:
            return FixResult(fixed=False, data=data, description="")

        lines = [
            "Your previous output failed downstream handoff lint validation.",
        ]
        if config.intent is not None and config.intent.intent:
            lines.append(f"Downstream intent: {config.intent.intent}")
            lines.append(f"Receiving agent's need: {config.intent.intent}")
        missing = _collect_unique_metadata_values(relevant, "missing")
        covered = _collect_unique_metadata_values(relevant, "covered")
        if covered:
            lines.append(f"Already covered: {', '.join(covered)}")
        if missing:
            lines.append(f"Still missing: {', '.join(missing)}")
        lines.append("Validation failures:")
        lines.extend(_diagnostic_bullets(relevant))
        lines.append("Previous output:")
        lines.append(_preview_payload(data))
        lines.append("Regenerate the full output so it satisfies the downstream contract.")
        feedback = "\n".join(lines)
        return FixResult(
            fixed=False,
            data=data,
            description="generated retry-with-feedback prompt",
            feedback=feedback,
        )


class RefocusFix(AutoFix):
    name = "refocus"

    def fix(self, data: Any, diagnostic: LintDiagnostic, config: LintConfig, **deps: Any) -> FixResult:
        diagnostics = deps.get("diagnostics")
        if not isinstance(diagnostics, list) or not diagnostics:
            diagnostics = [diagnostic]
        semantic_diags = [item for item in diagnostics if item.code in _SEMANTIC_REFOCUS_CODES]
        if not semantic_diags:
            return FixResult(fixed=False, data=data, description="")

        lines = [
            "Your previous output drifted from the downstream topic or omitted required semantic content.",
            "Refocus the response on the required downstream subject matter.",
        ]
        focus_text = _semantic_focus_text(config, semantic_diags)
        if focus_text:
            lines.append(focus_text)
        lines.append("Validation failures:")
        lines.extend(_diagnostic_bullets(semantic_diags))
        lines.append("Previous output:")
        lines.append(_preview_payload(data))
        lines.append("Return only the refocused corrected output payload.")
        feedback = "\n".join(lines)
        return FixResult(
            fixed=False,
            data=data,
            description="generated refocus prompt",
            feedback=feedback,
        )


STRUCTURAL_AUTOFIXES: dict[str, AutoFix] = {
    "fill_defaults": FillDefaultsFix(),
    "truncate": TruncateFix(),
    "clamp": ClampFix(),
    "coerce": CoerceFix(),
}
MODEL_AUTOFIXES: dict[str, AutoFix] = {
    "retry_with_feedback": RetryWithFeedbackFix(),
    "refocus": RefocusFix(),
}


def apply_structural_autofixes(
    data: Any,
    diagnostics: list[LintDiagnostic],
    config: StructuralConfig,
    requested: list[str] | None,
) -> tuple[Any, list[str], list[str]]:
    """Apply one deterministic autofix cycle in configured priority order."""
    if not requested:
        return data, [], []

    working = _clone(data)
    applied: list[str] = []
    descriptions: list[str] = []
    requested_set = {str(name).strip().lower().replace("-", "_") for name in requested}

    for fix_name in DETERMINISTIC_FIX_ORDER:
        if fix_name not in requested_set:
            continue
        fixer = STRUCTURAL_AUTOFIXES[fix_name]
        fix_applied = False
        for diagnostic in diagnostics:
            result = fixer.fix(working, diagnostic, config)
            if not result.fixed:
                continue
            working = result.data
            fix_applied = True
            if result.description:
                descriptions.append(result.description)
        if fix_applied:
            applied.append(fix_name)

    return working, applied, descriptions


def plan_model_autofixes(
    data: Any,
    diagnostics: list[LintDiagnostic],
    config: LintConfig,
    requested: list[str] | None,
) -> tuple[list[str], str | None, str | None]:
    """Plan linter-generated feedback for model-backed autofix strategies."""
    if not requested or not diagnostics:
        return [], None, None

    requested_set = {str(name).strip().lower().replace("-", "_") for name in requested}
    primary = next(
        (diagnostic for diagnostic in diagnostics if diagnostic.severity.value == "error"),
        diagnostics[0],
    )
    suggested: list[str] = []
    retry_feedback: str | None = None
    refocus_feedback: str | None = None

    for fix_name in MODEL_AUTOFIX_ORDER:
        if fix_name not in requested_set:
            continue
        fixer = MODEL_AUTOFIXES[fix_name]
        result = fixer.fix(data, primary, config, diagnostics=diagnostics)
        if not result.feedback:
            continue
        suggested.append(fix_name)
        if fix_name == "retry_with_feedback":
            retry_feedback = result.feedback
        elif fix_name == "refocus":
            refocus_feedback = result.feedback

    return suggested, retry_feedback, refocus_feedback
