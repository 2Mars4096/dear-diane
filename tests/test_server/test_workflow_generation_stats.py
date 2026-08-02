from __future__ import annotations

from types import SimpleNamespace

from dan.server.agent_runtime.workflow_generation_stats import (
    get_generation_stats_hint,
    record_generation_outcome,
)


def test_record_generation_outcome_noops_without_memory_kernel() -> None:
    seen: list[tuple[object, str, bool, str, bool, str]] = []

    def fake_recorder(
        kernel,
        *,
        method: str,
        pattern: str,
        success: bool,
        error_type: str,
        fix_needed: bool,
    ) -> None:
        seen.append((kernel, method, success, error_type, fix_needed, pattern))

    record_generation_outcome(
        None,
        method="codegen",
        pattern="wf-1",
        recorder=fake_recorder,
    )

    assert seen == []


def test_record_generation_outcome_calls_injected_recorder() -> None:
    seen: list[tuple[object, str, bool, str, bool, str]] = []
    kernel = object()

    def fake_recorder(
        actual_kernel,
        *,
        method: str,
        pattern: str,
        success: bool,
        error_type: str,
        fix_needed: bool,
    ) -> None:
        seen.append(
            (
                actual_kernel,
                method,
                success,
                error_type,
                fix_needed,
                pattern,
            )
        )

    record_generation_outcome(
        kernel,
        method="diagnosis",
        success=False,
        error_type="repair_failed",
        fix_needed=True,
        pattern="wf-2",
        recorder=fake_recorder,
    )

    assert seen == [
        (kernel, "diagnosis", False, "repair_failed", True, "wf-2")
    ]


def test_get_generation_stats_hint_returns_empty_without_memory_kernel() -> None:
    assert get_generation_stats_hint(None) == ""


def test_get_generation_stats_hint_uses_loaded_stats() -> None:
    hint = get_generation_stats_hint(
        object(),
        load_stats=lambda _kernel: SimpleNamespace(
            format_for_prompt=lambda: "Avoid syntax_error"
        ),
    )

    assert hint == "Avoid syntax_error"
