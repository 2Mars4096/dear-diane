"""Cheap semantic linting."""

from __future__ import annotations

from difflib import SequenceMatcher
import json
import math
import re
from typing import Any, Awaitable, Callable

from dan.linter.config import RuleSeverity, SemanticConfig
from dan.linter.result import LintDiagnostic
from dan.linter.rules import Embedder, RuleSpec, Tier

SEMANTIC_RULES = (
    RuleSpec(code="keyword_presence", tier=Tier.SEMANTIC, autofixable=False),
    RuleSpec(code="entity_presence", tier=Tier.SEMANTIC, autofixable=False),
    RuleSpec(code="language_detection", tier=Tier.SEMANTIC, autofixable=False),
    RuleSpec(code="contradiction_detection", tier=Tier.SEMANTIC, autofixable=False),
    RuleSpec(code="semantic_similarity", tier=Tier.SEMANTIC, autofixable=False),
)
_CLAIM_SPLIT_RE = re.compile(r"[.!?;\n]+")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?%?")
_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_LANGUAGE_HINTS: dict[str, tuple[str, ...]] = {
    "en": ("the", "and", "for", "with", "this", "that", "from", "into"),
    "es": ("el", "la", "los", "las", "para", "con", "una", "este"),
    "fr": ("le", "la", "les", "pour", "avec", "une", "dans", "est"),
    "de": ("der", "die", "das", "und", "mit", "für", "eine", "ist"),
}
_SEMANTIC_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "were",
    "with",
}
_NEGATION_TOKENS = {"no", "not", "never", "none", "without"}
_POLARITY_TOKENS: dict[str, tuple[str, int]] = {
    "accept": ("decision", 1),
    "accepted": ("decision", 1),
    "active": ("state", 1),
    "approved": ("decision", 1),
    "approve": ("decision", 1),
    "available": ("state", 1),
    "decline": ("trend", -1),
    "declined": ("trend", -1),
    "decrease": ("trend", -1),
    "decreased": ("trend", -1),
    "decreasing": ("trend", -1),
    "deny": ("decision", -1),
    "denied": ("decision", -1),
    "disable": ("state", -1),
    "disabled": ("state", -1),
    "down": ("trend", -1),
    "drop": ("trend", -1),
    "dropped": ("trend", -1),
    "enable": ("state", 1),
    "enabled": ("state", 1),
    "fail": ("outcome", -1),
    "failed": ("outcome", -1),
    "failure": ("outcome", -1),
    "false": ("state", -1),
    "gain": ("outcome", 1),
    "grew": ("trend", 1),
    "grow": ("trend", 1),
    "growth": ("trend", 1),
    "higher": ("trend", 1),
    "inactive": ("state", -1),
    "increase": ("trend", 1),
    "increased": ("trend", 1),
    "increasing": ("trend", 1),
    "loss": ("outcome", -1),
    "lower": ("trend", -1),
    "offline": ("state", -1),
    "online": ("state", 1),
    "pass": ("outcome", 1),
    "passed": ("outcome", 1),
    "present": ("state", 1),
    "reject": ("decision", -1),
    "rejected": ("decision", -1),
    "rise": ("trend", 1),
    "rose": ("trend", 1),
    "success": ("outcome", 1),
    "true": ("state", 1),
    "unavailable": ("state", -1),
    "up": ("trend", 1),
}


def text_from_data(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, sort_keys=True, default=str)
        except Exception:
            return str(value)
    return str(value)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _normalized_tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _normalized_phrase(text: str) -> str:
    return " ".join(_normalized_tokens(text))


def _split_claims(text: str) -> list[str]:
    return [chunk.strip() for chunk in _CLAIM_SPLIT_RE.split(text) if chunk.strip()]


def _claim_content_tokens(text: str) -> set[str]:
    return {
        token
        for token in _normalized_tokens(text)
        if token not in _SEMANTIC_STOPWORDS
        and token not in _NEGATION_TOKENS
        and token not in _POLARITY_TOKENS
        and not token.isdigit()
    }


def _claim_overlap(reference_claim: str, output_claim: str) -> float:
    reference_tokens = _claim_content_tokens(reference_claim)
    output_tokens = _claim_content_tokens(output_claim)
    if not reference_tokens or not output_tokens:
        return 0.0
    return len(reference_tokens & output_tokens) / max(min(len(reference_tokens), len(output_tokens)), 1)


def _claim_numbers(text: str) -> set[str]:
    return set(_NUMBER_RE.findall(text))


def _claim_is_negated(text: str) -> bool:
    return any(token in _NEGATION_TOKENS for token in _normalized_tokens(text))


def _claim_polarities(text: str) -> dict[str, int]:
    votes: dict[str, int] = {}
    for token in _normalized_tokens(text):
        group_sign = _POLARITY_TOKENS.get(token)
        if group_sign is None:
            continue
        group, sign = group_sign
        votes[group] = votes.get(group, 0) + sign
    return {
        group: 1 if total > 0 else -1
        for group, total in votes.items()
        if total != 0
    }


async def _claim_similarity(
    reference_claim: str,
    output_claim: str,
    config: SemanticConfig,
    embed: Embedder | Callable[[str, str | None], Awaitable[list[float]]] | None,
    *,
    cache: dict[str, list[float]],
) -> float | None:
    if embed is None:
        return None

    async def embed_cached(claim: str) -> list[float]:
        if claim not in cache:
            cache[claim] = await embed(claim, config.embedding_model)
        return cache[claim]

    try:
        reference_vec = await embed_cached(reference_claim)
        output_vec = await embed_cached(output_claim)
    except Exception:
        return None
    if not reference_vec or not output_vec:
        return None
    return cosine_similarity(reference_vec, output_vec)


def _detect_claim_contradiction(
    reference_claim: str,
    output_claim: str,
    *,
    lexical_overlap: float,
    claim_similarity: float | None,
) -> tuple[str | None, dict[str, Any]]:
    reference_numbers = sorted(_claim_numbers(reference_claim))
    output_numbers = sorted(_claim_numbers(output_claim))
    reference_negated = _claim_is_negated(reference_claim)
    output_negated = _claim_is_negated(output_claim)
    reference_polarities = _claim_polarities(reference_claim)
    output_polarities = _claim_polarities(output_claim)
    opposing_groups = sorted(
        group
        for group in (set(reference_polarities) & set(output_polarities))
        if reference_polarities[group] != output_polarities[group]
    )
    metadata: dict[str, Any] = {
        "reference_claim": reference_claim,
        "output_claim": output_claim,
        "lexical_overlap": lexical_overlap,
        "claim_similarity": claim_similarity,
        "reference_numbers": reference_numbers,
        "output_numbers": output_numbers,
        "reference_negated": reference_negated,
        "output_negated": output_negated,
        "opposing_groups": opposing_groups,
    }
    if opposing_groups:
        return "opposing_polarity", metadata
    if reference_negated != output_negated:
        return "negation_mismatch", metadata
    if reference_numbers and output_numbers and set(reference_numbers) != set(output_numbers):
        return "numeric_mismatch", metadata
    return None, metadata


def _entity_fuzzy_match_score(entity: str, text: str) -> float:
    entity_norm = _normalized_phrase(entity)
    text_tokens = _normalized_tokens(text)
    if not entity_norm or not text_tokens:
        return 0.0
    entity_tokens = entity_norm.split()
    window_size = max(1, len(entity_tokens))
    candidates = [" ".join(text_tokens[index:index + window_size]) for index in range(len(text_tokens) - window_size + 1)]
    if not candidates:
        candidates = [" ".join(text_tokens)]
    return max(SequenceMatcher(None, entity_norm, candidate).ratio() for candidate in candidates)


async def _entity_matches(
    entity: str,
    text: str,
    lowered: str,
    config: SemanticConfig,
    embed: Embedder | Callable[[str, str | None], Awaitable[list[float]]] | None,
    *,
    source_vec: list[float] | None,
) -> tuple[bool, float | None]:
    entity_lower = entity.lower()
    if config.entity_match_mode == "exact":
        matched = entity_lower in lowered
        return matched, 1.0 if matched else 0.0
    if config.entity_match_mode == "fuzzy":
        score = _entity_fuzzy_match_score(entity, text)
        return score >= config.entity_fuzzy_threshold, score
    if embed is None:
        matched = entity_lower in lowered
        return matched, 1.0 if matched else 0.0
    entity_vec = await embed(entity, config.embedding_model)
    if not entity_vec or not source_vec:
        return False, None
    score = cosine_similarity(entity_vec, source_vec)
    return score >= config.entity_embedding_threshold, score


def _aggregate_confidence(
    scores: list[float],
    mode: str,
) -> float | None:
    if not scores:
        return None
    if mode == "mean":
        return sum(scores) / len(scores)
    return min(scores)


def _contains_char_range(text: str, start: int, end: int) -> bool:
    return any(start <= ord(char) <= end for char in text)


def detect_language(text: str) -> tuple[str | None, float]:
    lowered = text.lower()
    if _contains_char_range(lowered, 0x3040, 0x30FF):
        return "ja", 0.95
    if _contains_char_range(lowered, 0xAC00, 0xD7AF):
        return "ko", 0.95
    if _contains_char_range(lowered, 0x4E00, 0x9FFF):
        return "zh", 0.9

    tokens = _normalized_tokens(lowered)
    if not tokens:
        return None, 0.0

    best_language: str | None = None
    best_score = 0.0
    token_set = set(tokens)
    for language, hints in _LANGUAGE_HINTS.items():
        overlap = len(token_set & set(hints))
        score = overlap / max(len(hints), 1)
        if score > best_score:
            best_language = language
            best_score = score

    if best_language is not None and best_score > 0:
        return best_language, best_score
    if all(ord(char) < 128 for char in lowered):
        return "en", 0.2
    return None, 0.0


async def validate_semantic(
    data: Any,
    config: SemanticConfig,
    *,
    severity: RuleSeverity,
    embed: Embedder | Callable[[str, str | None], Awaitable[list[float]]] | None = None,
) -> tuple[list[LintDiagnostic], float | None]:
    diagnostics: list[LintDiagnostic] = []
    text = text_from_data(data)
    lowered = text.lower()
    source_vec: list[float] | None = None
    contradiction_score: float | None = None
    if config.required_entities and config.entity_match_mode == "embedding" and embed is not None:
        try:
            source_vec = await embed(text, config.embedding_model)
        except Exception:
            source_vec = None

    keyword_ratio: float | None = None
    if config.topic_keywords:
        matched_keywords = [keyword for keyword in config.topic_keywords if keyword.lower() in lowered]
        missing_keywords = [keyword for keyword in config.topic_keywords if keyword not in matched_keywords]
        keyword_ratio = len(matched_keywords) / max(len(config.topic_keywords), 1)
        if keyword_ratio < config.min_keyword_ratio:
            diagnostics.append(
                LintDiagnostic(
                    code="keyword_presence",
                    message=(
                        f"Matched {len(matched_keywords)}/{len(config.topic_keywords)} topic keywords; "
                        f"minimum ratio is {config.min_keyword_ratio:.2f}"
                    ),
                    tier=Tier.SEMANTIC,
                    severity=severity,
                    metadata={
                        "matched": matched_keywords,
                        "missing": missing_keywords,
                        "ratio": keyword_ratio,
                        "threshold": config.min_keyword_ratio,
                    },
                )
            )

    entity_scores: list[float] = []
    missing_entities: list[str] = []
    for entity in config.required_entities:
        matched, entity_score = await _entity_matches(
            entity,
            text,
            lowered,
            config,
            embed,
            source_vec=source_vec,
        )
        if entity_score is not None:
            entity_scores.append(entity_score)
        if not matched:
            missing_entities.append(entity)
            diagnostics.append(
                LintDiagnostic(
                    code="entity_presence",
                    message=f"Missing entity '{entity}'",
                    tier=Tier.SEMANTIC,
                    severity=severity,
                    metadata={
                        "entity": entity,
                        "match_mode": config.entity_match_mode,
                        "score": entity_score,
                        "threshold": (
                            config.entity_fuzzy_threshold
                            if config.entity_match_mode == "fuzzy"
                            else config.entity_embedding_threshold
                            if config.entity_match_mode == "embedding"
                            else 1.0
                        ),
                    },
                )
            )
    entity_ratio: float | None = None
    if config.required_entities:
        entity_ratio = (
            (len(config.required_entities) - len(missing_entities))
            / max(len(config.required_entities), 1)
        )

    if config.expected_language:
        detected_language, language_confidence = detect_language(text)
        expected_language = config.expected_language.lower()
        if detected_language is not None and detected_language != expected_language:
            diagnostics.append(
                LintDiagnostic(
                    code="language_detection",
                    message=(
                        f"Detected language '{detected_language}' does not match expected "
                        f"'{expected_language}'"
                    ),
                    tier=Tier.SEMANTIC,
                    severity=RuleSeverity.WARNING,
                    metadata={
                        "detected_language": detected_language,
                        "expected_language": expected_language,
                        "confidence": language_confidence,
                    },
                )
            )

    contradiction_reference = config.contradiction_reference_text.strip()
    if contradiction_reference:
        reference_claims = _split_claims(contradiction_reference)
        output_claims = _split_claims(text)
        embedding_cache: dict[str, list[float]] = {}
        comparable_pairs = 0
        contradiction_found = False
        for output_claim in output_claims:
            for reference_claim in reference_claims:
                lexical_overlap = _claim_overlap(reference_claim, output_claim)
                claim_similarity: float | None = None
                comparable = lexical_overlap >= config.contradiction_min_claim_overlap
                if not comparable and lexical_overlap > 0.0:
                    claim_similarity = await _claim_similarity(
                        reference_claim,
                        output_claim,
                        config,
                        embed,
                        cache=embedding_cache,
                    )
                    comparable = (
                        claim_similarity is not None
                        and claim_similarity >= config.contradiction_similarity_threshold
                    )
                if not comparable:
                    continue
                comparable_pairs += 1
                reason, metadata = _detect_claim_contradiction(
                    reference_claim,
                    output_claim,
                    lexical_overlap=lexical_overlap,
                    claim_similarity=claim_similarity,
                )
                if reason is None:
                    continue
                contradiction_found = True
                metadata["reason"] = reason
                diagnostics.append(
                    LintDiagnostic(
                        code="contradiction_detection",
                        message=f"Output contradicts reference claim via {reason.replace('_', ' ')}",
                        tier=Tier.SEMANTIC,
                        severity=severity,
                        metadata=metadata,
                    )
                )
        if comparable_pairs:
            contradiction_score = 0.0 if contradiction_found else 1.0

    similarity_score: float | None = None
    reference_text = config.reference_text.strip()
    if not reference_text and config.topic_keywords:
        reference_text = " ".join(config.topic_keywords)
    if reference_text and config.min_similarity is not None and embed is not None:
        try:
            if source_vec is None:
                source_vec = await embed(text, config.embedding_model)
            ref_vec = await embed(reference_text, config.embedding_model)
        except Exception:
            ref_vec = []
        if not source_vec or not ref_vec:
            similarity_score = None
        else:
            similarity_score = cosine_similarity(source_vec, ref_vec)
        if similarity_score is not None and similarity_score < config.min_similarity:
            diagnostics.append(
                LintDiagnostic(
                    code="semantic_similarity",
                    message=(
                        f"Semantic similarity {similarity_score:.3f} below threshold "
                        f"{config.min_similarity:.3f}"
                    ),
                    tier=Tier.SEMANTIC,
                    severity=severity,
                    metadata={"score": similarity_score, "threshold": config.min_similarity},
                )
            )

    confidence_inputs = [
        score
        for score in (keyword_ratio, entity_ratio, contradiction_score, similarity_score)
        if score is not None
    ]
    aggregated_score = _aggregate_confidence(confidence_inputs, config.confidence_aggregation)

    return diagnostics, aggregated_score


__all__ = [
    "SEMANTIC_RULES",
    "cosine_similarity",
    "detect_language",
    "text_from_data",
    "validate_semantic",
]
