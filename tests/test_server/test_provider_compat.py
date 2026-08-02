"""Provider compatibility matrix — unit tests for provider interfaces,
tool-calling fallback, and mutation extraction from various response formats.

These are offline tests — no live API calls. They verify:
1. Each provider class can be instantiated (with mock config)
2. The tool-calling JSON fallback extraction works across provider output styles
3. Known limitations are documented per provider

Provider compatibility summary:
  - OpenAI: Full tool calling support. Returns structured tool_calls.
  - Anthropic: tool_calls may be empty; mutation plans returned as text JSON.
    Extraction relies on _try_parse_mutation_json / _extract_mutation_from_result.
  - Google: Similar to Anthropic — tool_calls may not be populated.
    JSON fallback extraction handles this case.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dan.providers import CompletionResult, ProviderConfig, StreamChunk
from dan.providers.registry import ProviderRegistry
from dan.server.chat_manager import ChatManager, _try_parse_mutation_json


# ---------------------------------------------------------------------------
# 1. Provider instantiation and interface
# ---------------------------------------------------------------------------


def test_openai_provider_has_expected_interface():
    """OpenAI provider can be instantiated and has complete/stream methods."""
    with patch("openai.AsyncOpenAI"):
        from dan.providers.openai_provider import OpenAIProvider
        config = ProviderConfig(api_key="test-key", base_url="https://api.example.com/v1")
        provider = OpenAIProvider(config)
        assert hasattr(provider, "complete")
        assert hasattr(provider, "stream")
        assert callable(provider.complete)
        assert callable(provider.stream)


def test_anthropic_provider_has_expected_interface():
    """Anthropic provider can be instantiated and has complete/stream methods."""
    try:
        with patch("anthropic.AsyncAnthropic"):
            from dan.providers.anthropic_provider import AnthropicProvider
            config = ProviderConfig(api_key="test-key")
            provider = AnthropicProvider(config)
            assert hasattr(provider, "complete")
            assert hasattr(provider, "stream")
    except ImportError:
        pytest.skip("anthropic package not installed")


def test_google_provider_has_expected_interface():
    """Google provider can be instantiated and has complete/stream methods."""
    try:
        mock_genai = MagicMock()
        with patch.dict("sys.modules", {"google.generativeai": mock_genai}):
            from dan.providers.google_provider import GoogleProvider
            config = ProviderConfig(api_key="test-key")
            provider = GoogleProvider(config)
            assert hasattr(provider, "complete")
            assert hasattr(provider, "stream")
    except ImportError:
        pytest.skip("google-generativeai package not installed")


# ---------------------------------------------------------------------------
# 2. Provider registry routing
# ---------------------------------------------------------------------------


def test_registry_routes_gpt_to_openai():
    registry = ProviderRegistry()
    mock_openai = MagicMock()
    mock_anthropic = MagicMock()
    registry.register("openai", mock_openai)
    registry.register("anthropic", mock_anthropic)
    assert registry.resolve("gpt-4o") is mock_openai


def test_registry_routes_claude_to_anthropic():
    registry = ProviderRegistry()
    mock_openai = MagicMock()
    mock_anthropic = MagicMock()
    registry.register("openai", mock_openai)
    registry.register("anthropic", mock_anthropic)
    assert registry.resolve("claude-sonnet-4-6") is mock_anthropic


def test_registry_routes_gemini_to_google():
    registry = ProviderRegistry()
    mock_google = MagicMock()
    registry.register("google", mock_google)
    assert registry.resolve("gemini-2.0-flash") is mock_google


def test_registry_falls_back_to_default():
    registry = ProviderRegistry()
    mock_default = MagicMock()
    registry.register("default", mock_default)
    assert registry.resolve("unknown-model-xyz") is mock_default


def test_registry_raises_when_no_provider():
    registry = ProviderRegistry()
    with pytest.raises(KeyError, match="No provider found"):
        registry.resolve("orphan-model")


# ---------------------------------------------------------------------------
# 3. Tool-calling fallback: extract mutation from text
# ---------------------------------------------------------------------------


def test_extract_mutation_from_structured_tool_calls():
    """OpenAI-style: tool_calls contains the plan directly."""
    mutation = {
        "description": "Add node",
        "operations": [{"op": "add_node", "node_type": "llm_operator", "name": "X"}],
    }
    result = CompletionResult(
        text="I'll add node X.",
        tool_calls=[{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "plan_graph_mutations",
                "arguments": json.dumps(mutation),
            },
        }],
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    extracted = ChatManager._extract_mutation_from_result(result)
    assert extracted is not None
    assert extracted["operations"][0]["name"] == "X"


def test_extract_mutation_from_text_json_fallback():
    """Anthropic/Google style: no tool_calls, plan embedded as JSON in text."""
    mutation = {
        "description": "Add node via text",
        "operations": [{"op": "add_node", "node_type": "llm_operator", "name": "Y"}],
    }
    text = f"Here's the mutation plan:\n```json\n{json.dumps(mutation)}\n```"
    result = CompletionResult(
        text=text,
        tool_calls=[],
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    extracted = ChatManager._extract_mutation_from_result(result)
    assert extracted is not None
    assert extracted["operations"][0]["name"] == "Y"


def test_extract_mutation_from_raw_json_text():
    """Plan as raw JSON without markdown code block wrapping."""
    mutation = {
        "description": "Raw JSON plan",
        "operations": [{"op": "add_node", "node_type": "llm_operator", "name": "Z"}],
    }
    result = CompletionResult(
        text=json.dumps(mutation),
        tool_calls=None,
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    extracted = ChatManager._extract_mutation_from_result(result)
    assert extracted is not None
    assert extracted["operations"][0]["name"] == "Z"


def test_extract_mutation_returns_none_for_plain_text():
    """Plain text response without mutation plan should return None."""
    result = CompletionResult(
        text="This workflow has 3 nodes and 2 edges. The Writer node...",
        tool_calls=[],
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    extracted = ChatManager._extract_mutation_from_result(result)
    assert extracted is None


def test_extract_mutation_from_tool_call_with_wrong_function_name():
    """Tool call with non-mutation function name should fall back to text."""
    result = CompletionResult(
        text="No mutation plan here.",
        tool_calls=[{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "some_other_function",
                "arguments": json.dumps({"foo": "bar"}),
            },
        }],
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    extracted = ChatManager._extract_mutation_from_result(result)
    assert extracted is None


# ---------------------------------------------------------------------------
# 4. Complex extraction cases
# ---------------------------------------------------------------------------


def test_extract_handles_multiple_json_blocks():
    """When text has multiple JSON blocks, extract the one with 'operations'."""
    text = '''Some analysis:
```json
{"summary": "3 nodes found"}
```

Here's the mutation plan:
```json
{"description": "Real plan", "operations": [{"op": "add_node", "node_type": "llm_operator", "name": "Real"}]}
```
'''
    result = _try_parse_mutation_json(text)
    assert result is not None
    assert result["operations"][0]["name"] == "Real"


def test_extract_handles_nested_json_in_operations():
    """Plans with nested config objects should parse correctly."""
    mutation = {
        "description": "Complex plan",
        "operations": [
            {
                "op": "add_node",
                "node_type": "gate",
                "name": "Loop Gate",
                "config": {
                    "gate_mode": "while",
                    "condition": "retry_count < 3",
                    "max_iterations": 10,
                },
            },
        ],
    }
    text = f"```json\n{json.dumps(mutation, indent=2)}\n```"
    result = _try_parse_mutation_json(text)
    assert result is not None
    assert result["operations"][0]["config"]["gate_mode"] == "while"


def test_extract_handles_malformed_json_gracefully():
    """Malformed JSON should return None without raising."""
    text = '```json\n{"operations": [{"op": "add_node", BROKEN}\n```'
    result = _try_parse_mutation_json(text)
    assert result is None


# ---------------------------------------------------------------------------
# 5. Provider-specific output simulation
# ---------------------------------------------------------------------------


def test_openai_style_response():
    """Simulate OpenAI's response format: text + tool_calls populated."""
    mutation = {
        "description": "OpenAI structured response",
        "operations": [{"op": "add_node", "node_type": "llm_operator", "name": "OAI"}],
    }
    result = CompletionResult(
        text="",
        tool_calls=[{
            "id": "call_oai",
            "type": "function",
            "function": {
                "name": "plan_graph_mutations",
                "arguments": json.dumps(mutation),
            },
        }],
        usage={"prompt_tokens": 100, "completion_tokens": 50},
    )
    extracted = ChatManager._extract_mutation_from_result(result)
    assert extracted is not None
    assert extracted["operations"][0]["name"] == "OAI"


def test_anthropic_style_response():
    """Simulate Anthropic's response: tool_calls empty, plan in text as JSON.

    Known limitation: Anthropic's API returns tool_calls as empty list even
    when tools are provided. The plan is returned as text with JSON block.
    """
    mutation = {
        "description": "Anthropic text-embedded plan",
        "operations": [{"op": "add_node", "node_type": "llm_operator", "name": "Claude"}],
    }
    result = CompletionResult(
        text=f"I'll create this plan:\n```json\n{json.dumps(mutation)}\n```",
        tool_calls=[],
        usage={"prompt_tokens": 100, "completion_tokens": 80},
    )
    extracted = ChatManager._extract_mutation_from_result(result)
    assert extracted is not None
    assert extracted["operations"][0]["name"] == "Claude"


def test_google_style_response():
    """Simulate Google Gemini's response: similar to Anthropic, plan in text.

    Known limitation: Google's generativeai SDK may not populate tool_calls
    in the same format. JSON fallback extraction handles this.
    """
    mutation = {
        "description": "Google text-embedded plan",
        "operations": [{"op": "add_node", "node_type": "llm_operator", "name": "Gemini"}],
    }
    result = CompletionResult(
        text=json.dumps(mutation),
        tool_calls=None,
        usage={"prompt_tokens": 80, "completion_tokens": 60},
    )
    extracted = ChatManager._extract_mutation_from_result(result)
    assert extracted is not None
    assert extracted["operations"][0]["name"] == "Gemini"
