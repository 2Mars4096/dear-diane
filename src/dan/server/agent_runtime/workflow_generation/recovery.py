"""Recovery helpers for workflow generation."""

from __future__ import annotations

from typing import Any


def sandbox_failure_error(codegen_result: Any) -> Any:
    from dan.meta.diagnosis import GenerationError, GenerationErrorType, GenerationStage

    err_msg = ""
    err_type = GenerationErrorType.no_output
    if codegen_result is not None:
        err_msg = (
            getattr(codegen_result, "error_message", None) or ""
        ).strip()
        raw_type = str(
            getattr(codegen_result, "error_type", "") or ""
        ).strip().lower()
        try:
            err_type = GenerationErrorType(raw_type)
        except ValueError:
            if "import" in raw_type:
                err_type = GenerationErrorType.import_error
            elif "name" in raw_type:
                err_type = GenerationErrorType.name_error
            elif raw_type:
                err_type = GenerationErrorType.runtime_error
    return GenerationError(
        stage=GenerationStage.sandbox,
        error_type=err_type,
        message=err_msg or "Builder code produced no graph output",
        source_line=getattr(codegen_result, "error_line", None),
        recoverable=True,
    )


def diagnosis_attempt_summary(diag_result: Any) -> str:
    attempts = list(getattr(diag_result, "attempts", []) or [])
    if not attempts:
        return "Diagnosis exhausted without a successful repair attempt."
    lines: list[str] = []
    for attempt in attempts[:4]:
        strategy = getattr(getattr(attempt, "strategy_used", None), "value", None)
        strategy = strategy or str(getattr(attempt, "strategy_used", "") or "unknown")
        result = str(getattr(attempt, "result", "") or "failed")
        corrections = ", ".join(
            str(item).strip()
            for item in (getattr(attempt, "corrections_applied", None) or [])
            if str(item).strip()
        )
        learning = "; ".join(
            str(item).strip()
            for item in (getattr(attempt, "learning_points", None) or [])
            if str(item).strip()
        )
        line = f"- attempt {getattr(attempt, 'attempt_number', '?')}: strategy={strategy}, result={result}"
        if corrections:
            line += f", corrections={corrections}"
        if learning:
            line += f", learnings={learning}"
        lines.append(line)
    return "\n".join(lines)


def diagnosis_attempt_payload(diag_result: Any) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for attempt in list(getattr(diag_result, "attempts", []) or []):
        strategy = getattr(attempt, "strategy_used", None)
        if hasattr(strategy, "value"):
            strategy = getattr(strategy, "value")
        payload.append(
            {
                "kind": str(strategy or "unknown"),
                "outcome": str(getattr(attempt, "result", "") or "failed"),
                "cause": "diagnosis_repair",
                "corrections_applied": [
                    str(item).strip()
                    for item in (getattr(attempt, "corrections_applied", None) or [])
                    if str(item).strip()
                ],
                "learning_points": [
                    str(item).strip()
                    for item in (getattr(attempt, "learning_points", None) or [])
                    if str(item).strip()
                ],
            }
        )
    return payload


def automatic_recovery_failure_lines(
    *,
    codegen_errors: list[Any],
    diag_result: Any,
) -> list[str]:
    return [
        str(getattr(err, "message", err) or "").strip()
        for err in (
            list(getattr(diag_result, "final_errors", []) or [])
            or codegen_errors
        )
        if str(getattr(err, "message", err) or "").strip()
    ]


def build_automatic_recovery_context(
    *,
    codegen_errors: list[Any],
    diag_result: Any,
    prior_code: str,
    last_build_provenance: dict[str, Any],
) -> str:
    failure_lines = automatic_recovery_failure_lines(
        codegen_errors=codegen_errors,
        diag_result=diag_result,
    )
    recovery_summary = [
        "Automatic recovery handoff:",
        "- Previous generation exhausted codegen, sandbox validation, and bounded diagnosis.",
    ]
    if failure_lines:
        recovery_summary.append("- Primary failures:")
        recovery_summary.extend(f"  - {line}" for line in failure_lines[:5])
    diagnosis_summary = diagnosis_attempt_summary(diag_result)
    if diagnosis_summary:
        recovery_summary.append("- Diagnosis attempts:")
        recovery_summary.extend(diagnosis_summary.splitlines())
    build_summary = str(last_build_provenance.get("build_summary") or "").strip()
    if build_summary:
        recovery_summary.append(f"- Build/readiness summary: {build_summary}")
    failure_bucket = str(last_build_provenance.get("failure_bucket") or "").strip()
    if failure_bucket:
        recovery_summary.append(f"- Failure bucket: {failure_bucket}")
    if prior_code.strip():
        recovery_summary.append("- Previous builder code (repair the smallest necessary part, do not restart from scratch unless required):")
        recovery_summary.append("```python")
        recovery_summary.append(prior_code[:6000])
        recovery_summary.append("```")
    recovery_summary.append(
        "- Return executable Python builder code only. Fix the smallest issue set needed to produce a runnable workflow."
    )
    return "\n".join(recovery_summary)


__all__ = [
    "automatic_recovery_failure_lines",
    "build_automatic_recovery_context",
    "diagnosis_attempt_payload",
    "diagnosis_attempt_summary",
    "sandbox_failure_error",
]
