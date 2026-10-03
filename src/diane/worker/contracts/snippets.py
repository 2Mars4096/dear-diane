"""Pure prompt snippets shared by universal brief composers."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from pydantic import BaseModel, Field


class PacingPolicy(BaseModel):
    """Optional pacing guidance selected by the brief."""

    safe_file_write_word_limit: int | None = Field(default=None, ge=1)
    safe_file_write_line_limit: int | None = Field(default=None, ge=1)
    prefer_file_edit: bool = True
    avoid_scratch_files: bool = True
    cadence: str = "land one coherent valid slice before attempting the next"


def _as_pacing_policy(policy: PacingPolicy | Mapping[str, Any] | None) -> PacingPolicy:
    if isinstance(policy, PacingPolicy):
        return policy
    return PacingPolicy.model_validate(dict(policy or {}))


def pacing_contract(policy: PacingPolicy | Mapping[str, Any] | None = None) -> str:
    """Return paced execution text without inventing numeric caps."""

    resolved = _as_pacing_policy(policy)
    parts = [
        "Work at a paced incremental cadence.",
        resolved.cadence.rstrip(".") + ".",
    ]
    numeric_limits: list[str] = []
    if resolved.safe_file_write_word_limit is not None:
        numeric_limits.append(f"{resolved.safe_file_write_word_limit} words")
    if resolved.safe_file_write_line_limit is not None:
        numeric_limits.append(f"{resolved.safe_file_write_line_limit} lines")
    if numeric_limits:
        parts.append(
            "Treat single `file_write` payloads above roughly "
            + " / ".join(numeric_limits)
            + " as risky and split them into smaller coherent chunks."
        )
    if resolved.prefer_file_edit:
        parts.append("Prefer `file_edit` for incremental updates to existing files.")
    if resolved.avoid_scratch_files:
        parts.append("Avoid speculative scratch files unless the brief explicitly allows them.")
    return " ".join(parts)


def incremental_edit_contract(*, prefer_file_edit: bool = True) -> str:
    if prefer_file_edit:
        return (
            "When changing an existing file, prefer a grounded incremental edit over a whole-file rewrite. "
            "Before calling the file tool, map the edit intent to the schema: replace uses content, insert uses content, "
            "delete uses no replacement fields, and old_string/new_string means exact-text replacement."
        )
    return "When changing files, use the smallest mutation that satisfies the brief."


def no_scratch_files_contract(allowed_paths: Sequence[str] | None = None) -> str:
    if allowed_paths:
        return "Do not create scratch or throwaway files outside: " + ", ".join(str(path) for path in allowed_paths) + "."
    return "Do not create scratch or throwaway files unless the brief explicitly permits them."


def read_only_contract(allowed_tool_ids: Sequence[str] | None = None) -> str:
    if allowed_tool_ids:
        return "This role is read-only. Use only these tools when tools are needed: " + ", ".join(allowed_tool_ids) + "."
    return "This role is read-only. Do not mutate workspace files, external systems, or durable state."


def policy_bound_warning(title: str, warnings: Sequence[str]) -> str:
    items = [str(item).strip() for item in warnings if str(item).strip()]
    if not items:
        return str(title).strip()
    return str(title).strip() + "\n" + "\n".join(f"- {item}" for item in items)
