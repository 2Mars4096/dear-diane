"""Reusable recovery hints paired with prompt failure predicates."""

from __future__ import annotations


def split_oversized_write() -> str:
    return "If a whole-file write is rejected as oversized, split the change into smaller coherent edits."


def prefer_file_edit() -> str:
    return "If the target file already exists, read the relevant range and retry with a grounded `file_edit`."


def narrow_scope_then_retry() -> str:
    return "If the current scope is too broad, choose the smallest artifact slice that can make material progress and retry."


def switch_to_grounded_read() -> str:
    return "If an edit lacks a stable anchor, read the target file first and copy exact surrounding text into the next edit."
