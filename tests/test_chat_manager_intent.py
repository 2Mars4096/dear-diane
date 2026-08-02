"""Unit tests for ChatManager intent parsing (33-6 JSON-in-content fallback)."""

from __future__ import annotations

from dan.providers import CompletionResult
from dan.server.chat_manager import ChatManager


def test_parse_intent_from_result_json_in_content_fallback() -> None:
    """_parse_intent_from_result parses WorkflowIntent from content when tool_calls empty."""
    # Model puts intent in content (no tool calling) — JSON-in-content fallback
    text = '''Here is the workflow intent:

```json
{"goal": "Build a chain", "stages": [{"name": "step1", "description": "First step", "stage_type": "transform"}]}
```

Let me know if you need changes.'''
    result = CompletionResult(text=text, tool_calls=None)
    intent = ChatManager._parse_intent_from_result(result)
    assert intent is not None
    assert intent.goal == "Build a chain"
    assert len(intent.stages) == 1
    assert intent.stages[0].name == "step1"
    assert intent.stages[0].stage_type.value == "transform"
