"""Prompt text and small deterministic helpers for failure predicates."""

from __future__ import annotations

from typing import Sequence


def anti_template_predicate(phrases: Sequence[str]) -> str:
    clean = [str(phrase).strip() for phrase in phrases if str(phrase).strip()]
    if not clean:
        return "Fail if the output looks like an ungrounded generic template rather than the requested artifact."
    return (
        "Fail if the output still looks like a generic template containing multiple phrases such as: "
        + ", ".join(clean)
        + "."
    )


def contains_template_phrases(text: str, phrases: Sequence[str], *, threshold: int = 2) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    hits = [phrase for phrase in phrases if str(phrase).strip().lower() in normalized]
    return len(hits) >= threshold


def single_file_redesign_predicate(*, min_changed_files: int | None = None) -> str:
    if min_changed_files is None:
        return "Fail coordinated redesigns that only touch one file when the brief requires multi-file coverage."
    return (
        "Fail coordinated redesigns that change fewer than "
        f"{min_changed_files} required files when those files existed at run start."
    )


def prompt_echo_predicate(*, min_prompt_chars: int | None = None) -> str:
    if min_prompt_chars is None:
        return "Fail outputs that paste the raw operator prompt as final content."
    return f"Fail outputs that paste raw operator prompt text of {min_prompt_chars} or more characters as final content."


def echoes_prompt(content: str, prompt: str, *, min_prompt_chars: int = 24) -> bool:
    normalized_content = " ".join(str(content or "").lower().split())
    normalized_prompt = " ".join(str(prompt or "").lower().split())
    return len(normalized_prompt) >= min_prompt_chars and normalized_prompt in normalized_content


def low_mutation_coverage_predicate(
    *,
    required_files: Sequence[str] | None = None,
    min_changed_files: int | None = None,
) -> str:
    pieces = ["Fail outputs whose mutation evidence does not cover the artifact policy."]
    if required_files:
        pieces.append("Required files: " + ", ".join(str(path) for path in required_files) + ".")
    if min_changed_files is not None:
        pieces.append(f"Minimum changed required files: {min_changed_files}.")
    return " ".join(pieces)
