"""Shared structured outcome normalization for bounded worker products."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

ExecutionEffect = Literal["modified", "verified"]

DEFAULT_EXECUTION_EFFECT_ALIASES: dict[str, ExecutionEffect] = {
    "modified": "modified",
    "change": "modified",
    "changed": "modified",
    "patch": "modified",
    "patched": "modified",
    "verified": "verified",
    "verify_only": "verified",
    "verified_only": "verified",
    "verification_only": "verified",
    "validation_only": "verified",
    "no_change": "verified",
    "no_changes": "verified",
}

DEFAULT_VERIFICATION_ONLY_MARKERS = (
    "no code changes required",
    "no changes required",
    "no code changes made",
    "no changes made",
    "no edits performed",
    "no edits needed",
    "verification only",
    "verification-only",
    "verify only",
    "validated only",
    "validation only",
    "inspection only",
    "read-only verification",
)


def strip_text(value: Any) -> str:
    return str(value or "").strip()


def clean_text(value: Any) -> str:
    return " ".join(strip_text(value).split())


def normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        texts = [value]
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
        if items and all(len(item) <= 1 for item in items):
            texts = ["".join(items)]
        else:
            texts = items
    elif isinstance(value, set):
        texts = [str(item) for item in value]
    else:
        texts = [str(value)]

    normalized: list[str] = []
    for text in texts:
        for line in str(text).splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            if cleaned.startswith("- "):
                cleaned = cleaned[2:].strip()
            normalized.append(cleaned)
    if normalized:
        return normalized

    cleaned = " ".join(str(text).strip() for text in texts).strip()
    return [cleaned] if cleaned else []


def first_text(*values: Any) -> str:
    for value in values:
        text = clean_text(value)
        if text:
            return text
    return ""


@dataclass(frozen=True, slots=True)
class StructuredOutcomeView:
    outcome_id: str | None = None
    summary: str = ""
    target_refs: tuple[str, ...] = ()
    validation_steps: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    execution_effect: ExecutionEffect | None = None

    @property
    def candidate_id(self) -> str | None:
        return self.outcome_id

    @property
    def change_summary(self) -> str:
        return self.summary

    @property
    def target_files(self) -> tuple[str, ...]:
        return self.target_refs

    @property
    def test_plan(self) -> tuple[str, ...]:
        return self.validation_steps

    @property
    def workspace_effect(self) -> ExecutionEffect | None:
        return self.execution_effect


def looks_like_verification_only_summary(
    summary: str,
    *,
    verification_markers: tuple[str, ...] = DEFAULT_VERIFICATION_ONLY_MARKERS,
) -> bool:
    text = clean_text(summary).lower()
    return any(marker in text for marker in verification_markers)


def structured_outcome_view(
    payload: Mapping[str, Any],
    *,
    id_field: str = "candidate_id",
    summary_field: str = "change_summary",
    target_refs_field: str = "target_files",
    validation_steps_field: str = "test_plan",
    risks_field: str = "risks",
    effect_field: str = "workspace_effect",
    effect_aliases: Mapping[str, ExecutionEffect] = DEFAULT_EXECUTION_EFFECT_ALIASES,
) -> StructuredOutcomeView:
    raw_effect = strip_text(payload.get(effect_field)).lower().replace("-", "_").replace(" ", "_")
    return StructuredOutcomeView(
        outcome_id=strip_text(payload.get(id_field)) or None,
        summary=clean_text(payload.get(summary_field)),
        target_refs=tuple(normalize_text_list(payload.get(target_refs_field))),
        validation_steps=tuple(normalize_text_list(payload.get(validation_steps_field))),
        risks=tuple(normalize_text_list(payload.get(risks_field))),
        execution_effect=effect_aliases.get(raw_effect),
    )


def infer_execution_effect(
    outcome: StructuredOutcomeView,
    *,
    verification_markers: tuple[str, ...] = DEFAULT_VERIFICATION_ONLY_MARKERS,
) -> ExecutionEffect | None:
    if outcome.execution_effect == "modified":
        return "modified"
    if outcome.execution_effect == "verified" and (
        not outcome.summary
        or looks_like_verification_only_summary(
            outcome.summary,
            verification_markers=verification_markers,
        )
    ):
        return "verified"
    if looks_like_verification_only_summary(
        outcome.summary,
        verification_markers=verification_markers,
    ):
        return "verified"
    if outcome.outcome_id or outcome.summary or outcome.target_refs:
        return "modified"
    return None


def normalize_structured_outcome_payload(
    payload: Mapping[str, Any],
    *,
    id_field: str = "candidate_id",
    summary_field: str = "change_summary",
    target_refs_field: str = "target_files",
    validation_steps_field: str = "test_plan",
    risks_field: str = "risks",
    effect_field: str = "workspace_effect",
    effect_aliases: Mapping[str, ExecutionEffect] = DEFAULT_EXECUTION_EFFECT_ALIASES,
    verification_markers: tuple[str, ...] = DEFAULT_VERIFICATION_ONLY_MARKERS,
) -> dict[str, Any]:
    normalized = dict(payload)
    outcome = structured_outcome_view(
        payload,
        id_field=id_field,
        summary_field=summary_field,
        target_refs_field=target_refs_field,
        validation_steps_field=validation_steps_field,
        risks_field=risks_field,
        effect_field=effect_field,
        effect_aliases=effect_aliases,
    )
    if outcome.outcome_id is not None:
        normalized[id_field] = outcome.outcome_id
    else:
        normalized.pop(id_field, None)
    normalized[summary_field] = outcome.summary
    normalized[target_refs_field] = list(outcome.target_refs)
    normalized[validation_steps_field] = list(outcome.validation_steps)
    normalized[risks_field] = list(outcome.risks)
    execution_effect = infer_execution_effect(
        outcome,
        verification_markers=verification_markers,
    )
    if execution_effect is not None:
        normalized[effect_field] = execution_effect
    else:
        normalized.pop(effect_field, None)
    return normalized


def has_material_structured_outcome(
    payload: Mapping[str, Any],
    *,
    id_field: str = "candidate_id",
    summary_field: str = "change_summary",
    target_refs_field: str = "target_files",
    validation_steps_field: str = "test_plan",
    risks_field: str = "risks",
    effect_field: str = "workspace_effect",
) -> bool:
    if not isinstance(payload, Mapping):
        return False
    outcome = structured_outcome_view(
        payload,
        id_field=id_field,
        summary_field=summary_field,
        target_refs_field=target_refs_field,
        validation_steps_field=validation_steps_field,
        risks_field=risks_field,
        effect_field=effect_field,
    )
    if outcome.outcome_id:
        return True
    if outcome.summary:
        return True
    return bool(outcome.target_refs)


def mutation_claim_requires_evidence(outcome: StructuredOutcomeView) -> bool:
    return bool(outcome.target_refs) and infer_execution_effect(outcome) != "verified"


__all__ = [
    "DEFAULT_EXECUTION_EFFECT_ALIASES",
    "DEFAULT_VERIFICATION_ONLY_MARKERS",
    "ExecutionEffect",
    "StructuredOutcomeView",
    "clean_text",
    "first_text",
    "has_material_structured_outcome",
    "infer_execution_effect",
    "looks_like_verification_only_summary",
    "mutation_claim_requires_evidence",
    "normalize_structured_outcome_payload",
    "normalize_text_list",
    "strip_text",
    "structured_outcome_view",
]
