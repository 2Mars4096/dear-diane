"""Prompt-pressure calibration helpers for live organism traces."""

from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any, Iterable

from pydantic import BaseModel, Field


class PromptPressureCall(BaseModel):
    """One model request with provider-facing prompt-pressure telemetry."""

    timestamp: str = ""
    source_path: str = ""
    model_call_id: str = ""
    worker_id: str = ""
    model: str = ""
    round: int | None = None
    budget_chars: int | None = None
    emergency_budget_chars: int | None = None
    original_total_chars: int | None = None
    final_chars: int | None = None
    tool_schema_chars: int = 0
    prompt_tokens: int | None = None
    total_tokens: int | None = None
    chars_per_prompt_token: float | None = None
    final_to_budget_ratio: float | None = None
    final_to_emergency_ratio: float | None = None
    budget_triggered: bool = False
    emergency_compaction: bool = False
    compacted_file_reads: int = 0
    compacted_non_file_tools: int = 0
    compacted_tool_call_args: int = 0
    compacted_assistant_messages: int = 0
    omitted_chars: int = 0


class PromptPressureRetry(BaseModel):
    """One context-length emergency retry row."""

    timestamp: str = ""
    source_path: str = ""
    model_call_id: str = ""
    model: str = ""
    round: int | None = None
    retry_attempt: int | None = None
    error_type: str = ""
    error: str = ""
    final_chars: int | None = None
    emergency_budget_chars: int | None = None


class PromptPressureAnalysis(BaseModel):
    """Aggregate prompt-pressure calibration report."""

    source_file_count: int = 0
    source_row_count: int = 0
    model_request_count: int = 0
    context_length_retry_count: int = 0
    budget_triggered_count: int = 0
    emergency_compaction_count: int = 0
    prompt_token_observation_count: int = 0
    current_target_chars: int | None = None
    current_emergency_chars: int | None = None
    max_original_total_chars: int | None = None
    max_final_chars: int | None = None
    max_tool_schema_chars: int = 0
    max_prompt_tokens: int | None = None
    avg_chars_per_prompt_token: float | None = None
    max_final_to_budget_ratio: float | None = None
    max_final_to_emergency_ratio: float | None = None
    calibration_recommendation: str = "insufficient_data"
    highest_pressure_calls: list[PromptPressureCall] = Field(default_factory=list)
    context_length_retries: list[PromptPressureRetry] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def analyze_prompt_pressure_rows(
    rows: Iterable[dict[str, Any]],
    *,
    limit: int = 5,
    source_file_count: int = 0,
) -> PromptPressureAnalysis:
    """Analyze raw or normalized organism-log rows for prompt-pressure calibration."""

    materialized_rows = [dict(row) for row in rows if isinstance(row, dict)]
    responded_usage = _response_usage_by_model_call_id(materialized_rows)
    calls = [
        _call_from_requested_row(row, responded_usage=responded_usage)
        for row in materialized_rows
        if _event_name(row) == "model.requested"
    ]
    retries = [
        _retry_from_row(row)
        for row in materialized_rows
        if _event_name(row) == "model.context_length_retry"
    ]

    resolved_limit = max(1, int(limit))
    calls = [call for call in calls if call is not None]
    retries = [retry for retry in retries if retry is not None]
    target = _mode_positive_int(call.budget_chars for call in calls)
    emergency = _mode_positive_int(call.emergency_budget_chars for call in calls)
    token_ratios = [
        float(call.chars_per_prompt_token)
        for call in calls
        if call.chars_per_prompt_token is not None
    ]

    highest_pressure = sorted(
        calls,
        key=lambda call: (
            -(call.final_to_emergency_ratio or call.final_to_budget_ratio or 0.0),
            -(call.final_chars or 0),
        ),
    )[:resolved_limit]
    notes = _calibration_notes(
        calls=calls,
        retries=retries,
        token_ratios=token_ratios,
    )
    recommendation = _calibration_recommendation(calls=calls, retries=retries)

    return PromptPressureAnalysis(
        source_file_count=max(0, int(source_file_count)),
        source_row_count=len(materialized_rows),
        model_request_count=len(calls),
        context_length_retry_count=len(retries),
        budget_triggered_count=sum(1 for call in calls if call.budget_triggered),
        emergency_compaction_count=sum(1 for call in calls if call.emergency_compaction),
        prompt_token_observation_count=len(token_ratios),
        current_target_chars=target,
        current_emergency_chars=emergency,
        max_original_total_chars=_max_nullable(call.original_total_chars for call in calls),
        max_final_chars=_max_nullable(call.final_chars for call in calls),
        max_tool_schema_chars=max([call.tool_schema_chars for call in calls] or [0]),
        max_prompt_tokens=_max_nullable(call.prompt_tokens for call in calls),
        avg_chars_per_prompt_token=(
            round(float(mean(token_ratios)), 3) if token_ratios else None
        ),
        max_final_to_budget_ratio=_max_nullable_float(
            call.final_to_budget_ratio for call in calls
        ),
        max_final_to_emergency_ratio=_max_nullable_float(
            call.final_to_emergency_ratio for call in calls
        ),
        calibration_recommendation=recommendation,
        highest_pressure_calls=highest_pressure,
        context_length_retries=retries[:resolved_limit],
        notes=notes,
    )


def _event_name(row: dict[str, Any]) -> str:
    return str(_row_value(row, "event") or _row_value(row, "type") or "").strip()


def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


def _row_value(row: dict[str, Any], key: str) -> Any:
    if key in row:
        return row.get(key)
    payload = _row_payload(row)
    if key in payload:
        return payload.get(key)
    return None


def _source_path(row: dict[str, Any]) -> str:
    return str(row.get("_source_path") or _row_value(row, "source_path") or "").strip()


def _as_int(value: Any) -> int | None:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return None
    return coerced


def _positive_int(value: Any) -> int | None:
    coerced = _as_int(value)
    return coerced if coerced is not None and coerced > 0 else None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _round_ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(float(numerator) / float(denominator), 3)


def _usage_from_row(row: dict[str, Any]) -> dict[str, int]:
    usage = _row_value(row, "usage")
    if not isinstance(usage, dict):
        usage = {}
    prompt = _positive_int(
        usage.get("prompt_tokens")
        or usage.get("prompt")
        or _row_value(row, "usage_prompt_tokens")
    )
    completion = _positive_int(
        usage.get("completion_tokens")
        or usage.get("completion")
        or _row_value(row, "usage_completion_tokens")
    )
    total = _positive_int(
        usage.get("total_tokens")
        or usage.get("total")
        or _row_value(row, "usage_total_tokens")
    )
    normalized: dict[str, int] = {}
    if prompt is not None:
        normalized["prompt_tokens"] = prompt
    if completion is not None:
        normalized["completion_tokens"] = completion
    if total is not None:
        normalized["total_tokens"] = total
    return normalized


def _response_usage_by_model_call_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    usage_by_id: dict[str, dict[str, int]] = {}
    for row in rows:
        if _event_name(row) not in {
            "model.responded",
            "model.timeout",
            "model.provider_prompt_rejected",
        }:
            continue
        model_call_id = str(_row_value(row, "model_call_id") or "").strip()
        if not model_call_id:
            continue
        usage = _usage_from_row(row)
        if usage:
            usage_by_id[model_call_id] = usage
    return usage_by_id


def _call_from_requested_row(
    row: dict[str, Any],
    *,
    responded_usage: dict[str, dict[str, int]],
) -> PromptPressureCall | None:
    model_call_id = str(_row_value(row, "model_call_id") or "").strip()
    usage = responded_usage.get(model_call_id, {})
    prompt_tokens = usage.get("prompt_tokens")
    total_tokens = usage.get("total_tokens")
    final_chars = _positive_int(_row_value(row, "prompt_context_final_chars"))
    budget_chars = _positive_int(_row_value(row, "prompt_context_budget_chars"))
    emergency_budget_chars = _positive_int(
        _row_value(row, "prompt_context_emergency_budget_chars")
    )
    chars_per_prompt_token = None
    if final_chars is not None and prompt_tokens:
        chars_per_prompt_token = round(float(final_chars) / float(prompt_tokens), 3)

    return PromptPressureCall(
        timestamp=str(_row_value(row, "timestamp") or "").strip(),
        source_path=_source_path(row),
        model_call_id=model_call_id,
        worker_id=str(_row_value(row, "worker_id") or "").strip(),
        model=str(_row_value(row, "model") or "").strip(),
        round=_as_int(_row_value(row, "round")),
        budget_chars=budget_chars,
        emergency_budget_chars=emergency_budget_chars,
        original_total_chars=_positive_int(
            _row_value(row, "prompt_context_original_total_chars")
        ),
        final_chars=final_chars,
        tool_schema_chars=max(
            0, _as_int(_row_value(row, "prompt_context_tool_schema_chars")) or 0
        ),
        prompt_tokens=prompt_tokens,
        total_tokens=total_tokens,
        chars_per_prompt_token=chars_per_prompt_token,
        final_to_budget_ratio=_round_ratio(final_chars, budget_chars),
        final_to_emergency_ratio=_round_ratio(final_chars, emergency_budget_chars),
        budget_triggered=_as_bool(_row_value(row, "prompt_context_budget_triggered")),
        emergency_compaction=_as_bool(
            _row_value(row, "prompt_context_emergency_compaction")
        ),
        compacted_file_reads=max(
            0, _as_int(_row_value(row, "prompt_context_compacted_file_reads")) or 0
        ),
        compacted_non_file_tools=max(
            0,
            _as_int(_row_value(row, "prompt_context_compacted_non_file_tools")) or 0,
        ),
        compacted_tool_call_args=max(
            0,
            _as_int(_row_value(row, "prompt_context_compacted_tool_call_args")) or 0,
        ),
        compacted_assistant_messages=max(
            0,
            _as_int(_row_value(row, "prompt_context_compacted_assistant_messages"))
            or 0,
        ),
        omitted_chars=sum(
            max(0, _as_int(_row_value(row, key)) or 0)
            for key in (
                "prompt_context_omitted_file_read_chars",
                "prompt_context_omitted_non_file_tool_chars",
                "prompt_context_omitted_tool_call_arg_chars",
                "prompt_context_omitted_assistant_message_chars",
            )
        ),
    )


def _retry_from_row(row: dict[str, Any]) -> PromptPressureRetry | None:
    return PromptPressureRetry(
        timestamp=str(_row_value(row, "timestamp") or "").strip(),
        source_path=_source_path(row),
        model_call_id=str(_row_value(row, "model_call_id") or "").strip(),
        model=str(_row_value(row, "model") or "").strip(),
        round=_as_int(_row_value(row, "round")),
        retry_attempt=_as_int(_row_value(row, "retry_attempt")),
        error_type=str(_row_value(row, "error_type") or "").strip(),
        error=str(_row_value(row, "error") or "").strip(),
        final_chars=_positive_int(_row_value(row, "prompt_context_final_chars")),
        emergency_budget_chars=_positive_int(
            _row_value(row, "prompt_context_emergency_budget_chars")
        ),
    )


def _max_nullable(values: Iterable[int | None]) -> int | None:
    materialized = [int(value) for value in values if value is not None]
    return max(materialized) if materialized else None


def _max_nullable_float(values: Iterable[float | None]) -> float | None:
    materialized = [float(value) for value in values if value is not None]
    return round(max(materialized), 3) if materialized else None


def _mode_positive_int(values: Iterable[int | None]) -> int | None:
    positive = [int(value) for value in values if value is not None and value > 0]
    if not positive:
        return None
    return Counter(positive).most_common(1)[0][0]


def _calibration_recommendation(
    *,
    calls: list[PromptPressureCall],
    retries: list[PromptPressureRetry],
) -> str:
    if not calls:
        return "insufficient_data"
    if retries:
        return "review_lower_threshold_or_stronger_compaction"
    if any(
        call.final_to_emergency_ratio is not None and call.final_to_emergency_ratio > 1.0
        for call in calls
    ):
        return "review_stronger_emergency_compaction"
    if not any(call.budget_triggered for call in calls):
        return "insufficient_pressure_sample"
    if any(
        call.final_to_emergency_ratio is not None and call.final_to_emergency_ratio >= 0.9
        for call in calls
    ):
        return "monitor_near_emergency_envelope"
    return "keep_current_thresholds"


def _calibration_notes(
    *,
    calls: list[PromptPressureCall],
    retries: list[PromptPressureRetry],
    token_ratios: list[float],
) -> list[str]:
    notes: list[str] = []
    if not calls:
        return ["No model.requested prompt-pressure telemetry was found."]
    if not any(call.budget_triggered for call in calls):
        notes.append(
            "The trace did not trigger the prompt-pressure budget gate, so it cannot calibrate the live thresholds yet."
        )
    if retries:
        notes.append(
            "Context-length retry rows occurred; inspect the matching high-pressure calls before raising thresholds."
        )
    if any(
        call.final_to_emergency_ratio is not None and call.final_to_emergency_ratio > 1.0
        for call in calls
    ):
        notes.append(
            "At least one provider-facing replay remained above the emergency envelope after compaction."
        )
    if not token_ratios:
        notes.append(
            "Provider prompt-token usage was not present on matching model responses, so char-to-token calibration is unavailable."
        )
    else:
        notes.append(
            "Provider prompt-token usage was available for "
            f"{len(token_ratios)} model request(s); use chars_per_prompt_token to compare providers."
        )
    if any(call.tool_schema_chars >= max((call.final_chars or 0) * 0.2, 1) for call in calls):
        notes.append(
            "Tool-schema chars are a material share of at least one provider request; schema size should be considered with message replay size."
        )
    if not retries and any(call.budget_triggered for call in calls):
        notes.append(
            "Budget-triggered compaction ran without context-length retries in this sample."
        )
    return notes
