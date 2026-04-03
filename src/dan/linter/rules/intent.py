"""Intent validation."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import re
from typing import Any, Awaitable, Callable

from dan.linter.config import IntentConfig, RuleSeverity
from dan.linter.result import LintDiagnostic
from dan.linter.rules import LLMJudge, RuleSpec, Tier

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_PASS_VERDICTS = {"pass", "passed", "yes", "true"}
_FAIL_VERDICTS = {"fail", "failed", "no", "false"}
_PARTIAL_VERDICTS = {"partial", "incomplete", "mixed"}
INTENT_RULES = (
    RuleSpec(code="intent_keywords", tier=Tier.INTENT, autofixable=True),
    RuleSpec(code="intent_partial", tier=Tier.INTENT, autofixable=False),
    RuleSpec(code="intent_completeness", tier=Tier.INTENT, autofixable=False),
    RuleSpec(code="intent_mismatch", tier=Tier.INTENT, autofixable=True),
)


@dataclass(frozen=True)
class JudgmentResult:
    verdict: str
    confidence: float | None
    reason: str
    missing: list[str]
    covered: list[str]


def _textify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return str(value)


def _coerce_list(value: Any) -> list[str]:
    if isinstance(value, str):
        pieces = [part.strip() for part in value.split(",")]
        return [part for part in pieces if part]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _preview_data(value: Any, *, limit: int = 1600) -> str:
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


def build_intent_judge_prompt(
    data: Any,
    config: IntentConfig,
    *,
    prompt_variant: str = "alignment",
    missing: list[str] | None = None,
) -> str:
    preview = _preview_data(data)
    keywords = ", ".join(config.required_keywords) if config.required_keywords else ""
    if prompt_variant == "completeness":
        focus = ", ".join(missing or []) or "all downstream requirements"
        prompt = (
            "Judge whether the output fully covers the missing downstream requirements.\n"
            "Return compact JSON only with keys verdict, score, reason, missing, covered.\n"
            'Use verdict "pass", "partial", or "fail".\n'
            f"Downstream intent:\n{config.intent}\n\n"
            f"Focus specifically on these requirements:\n{focus}\n\n"
            f"Output to judge:\n{preview}"
        )
        if keywords:
            prompt += f"\n\nRequired keywords to keep in scope:\n{keywords}"
        return prompt

    prompt = (
        "Judge whether the output is directly usable for the downstream intent.\n"
        "Return compact JSON only with keys verdict, score, reason, missing.\n"
        'Use verdict "pass", "partial", or "fail".\n'
        f"Downstream intent:\n{config.intent}\n\n"
        f"Output to judge:\n{preview}"
    )
    if keywords:
        prompt += f"\n\nRequired keywords to keep in scope:\n{keywords}"
    return prompt


def _judge_accepts_context(
    judge: LLMJudge | Callable[..., Awaitable[dict[str, Any]]],
) -> bool:
    try:
        signature = inspect.signature(judge)
    except (TypeError, ValueError):
        return True
    positional_params = 0
    for param in signature.parameters.values():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            return True
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional_params += 1
    return positional_params >= 3


async def _invoke_judge(
    judge: LLMJudge | Callable[..., Awaitable[dict[str, Any]]],
    data: Any,
    config: IntentConfig,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if context is not None and _judge_accepts_context(judge):
        return await judge(data, config, context)
    return await judge(data, config)


def _completeness_message(result: JudgmentResult) -> str:
    parts: list[str] = []
    if result.reason:
        parts.append(result.reason)
    if result.covered:
        parts.append(f"Covered: {', '.join(result.covered)}.")
    if result.missing:
        parts.append(f"Missing: {', '.join(result.missing)}.")
    return " ".join(parts).strip()


def _adds_completeness_detail(
    primary: JudgmentResult,
    follow_up: JudgmentResult,
) -> bool:
    if follow_up.covered:
        return True
    if follow_up.missing != primary.missing:
        return True
    normalized_reason = follow_up.reason.strip()
    return bool(normalized_reason and normalized_reason != primary.reason.strip())


def parse_judgment_result(payload: dict[str, Any]) -> JudgmentResult:
    raw_verdict = str(
        payload.get("verdict")
        or payload.get("judgment")
        or payload.get("result")
        or ""
    ).strip().lower()
    explicit_passed = payload.get("passed")
    if isinstance(explicit_passed, bool):
        verdict = "pass" if explicit_passed else "fail"
    elif raw_verdict in _PARTIAL_VERDICTS:
        verdict = "partial"
    elif raw_verdict in _PASS_VERDICTS:
        verdict = "pass"
    elif raw_verdict in _FAIL_VERDICTS:
        verdict = "fail"
    else:
        verdict = "pass" if bool(payload.get("passed")) else "fail"

    raw_score = payload.get("score", payload.get("confidence"))
    try:
        confidence = float(raw_score) if raw_score is not None else None
    except (TypeError, ValueError):
        confidence = None

    reason = str(payload.get("message") or payload.get("reason") or "").strip()
    missing = _coerce_list(payload.get("missing"))
    covered = _coerce_list(payload.get("covered"))
    return JudgmentResult(
        verdict=verdict,
        confidence=confidence,
        reason=reason,
        missing=missing,
        covered=covered,
    )


async def validate_intent(
    data: Any,
    config: IntentConfig,
    *,
    severity: RuleSeverity,
    judge: LLMJudge | Callable[..., Awaitable[dict[str, Any]]] | None = None,
) -> tuple[list[LintDiagnostic], float | None]:
    diagnostics: list[LintDiagnostic] = []
    text = _textify(data)
    lowered = text.lower()
    score: float | None = None

    required = list(config.required_keywords)
    if not required and config.intent and judge is None:
        required = list(_TOKEN_RE.findall(config.intent.lower()))[:4]

    if required:
        hits = sum(1 for token in required if token in lowered)
        score = hits / len(required)
        if hits < len(required):
            diagnostics.append(
                LintDiagnostic(
                    code="intent_keywords",
                    message=f"Intent coverage too low ({hits}/{len(required)} required keywords present)",
                    tier=Tier.INTENT,
                    severity=severity,
                    metadata={"score": score, "required_keywords": required},
                )
            )

    if judge is not None and config.intent:
        try:
            result = await _invoke_judge(judge, data, config)
        except Exception:
            return diagnostics, score
        judgment = parse_judgment_result(result)
        if judgment.confidence is not None:
            score = judgment.confidence
        if judgment.verdict == "partial":
            completeness = None
            if judgment.missing:
                try:
                    completeness_payload = await _invoke_judge(
                        judge,
                        data,
                        config,
                        {
                            "prompt_variant": "completeness",
                            "missing": judgment.missing,
                        },
                    )
                except Exception:
                    completeness_payload = None
                if completeness_payload is not None:
                    completeness = parse_judgment_result(completeness_payload)
            missing_suffix = (
                f" Missing: {', '.join(judgment.missing)}."
                if judgment.missing
                else ""
            )
            diagnostics.append(
                LintDiagnostic(
                    code="intent_partial",
                    message=(
                        judgment.reason
                        or f"Output only partially satisfies the downstream intent.{missing_suffix}"
                    ),
                    tier=Tier.INTENT,
                    severity=RuleSeverity.WARNING,
                    metadata={
                        "score": score,
                        "missing": completeness.missing if completeness and completeness.missing else judgment.missing,
                        "covered": completeness.covered if completeness is not None else [],
                        "verdict": judgment.verdict,
                    },
                )
            )
            if completeness is not None and _adds_completeness_detail(judgment, completeness):
                diagnostics.append(
                    LintDiagnostic(
                        code="intent_completeness",
                        message=_completeness_message(completeness),
                        tier=Tier.INTENT,
                        severity=RuleSeverity.WARNING,
                        metadata={
                            "score": completeness.confidence if completeness.confidence is not None else score,
                            "missing": completeness.missing,
                            "covered": completeness.covered,
                            "verdict": completeness.verdict,
                        },
                    )
                )
        elif judgment.verdict != "pass":
            missing_suffix = (
                f" Missing: {', '.join(judgment.missing)}."
                if judgment.missing
                else ""
            )
            diagnostics.append(
                LintDiagnostic(
                    code="intent_mismatch",
                    message=(
                        judgment.reason
                        or f"Output does not satisfy the downstream intent.{missing_suffix}"
                    ),
                    tier=Tier.INTENT,
                    severity=severity,
                    metadata={
                        "score": score,
                        "missing": judgment.missing,
                        "verdict": judgment.verdict,
                    },
                )
            )
        return diagnostics, score

    if required:
        return diagnostics, score

    return diagnostics, None


__all__ = [
    "INTENT_RULES",
    "JudgmentResult",
    "build_intent_judge_prompt",
    "parse_judgment_result",
    "validate_intent",
]
