from __future__ import annotations

from dan.server.chat.helpers import (
    _build_tool_followup_error_intro,
    _build_tool_followup_recovery_prompt,
    _classify_llm_error_kind,
    _is_transient_llm_error,
)


def test_tool_history_error_is_not_treated_as_transient_timeout():
    exc = RuntimeError(
        "Error code: 429 - {'error': {'message': 'Function call is missing a thought_signature "
        "in functionCall parts. function call default_api:list_directory'}}"
    )

    assert _classify_llm_error_kind(exc) == "tool_history_incompatible"
    assert _is_transient_llm_error(exc) is False
    assert "rejected the tool-call transcript metadata" in _build_tool_followup_recovery_prompt(exc)
    assert "rejected the tool-call transcript" in _build_tool_followup_error_intro(exc)
