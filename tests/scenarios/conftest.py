"""Shared fixtures for real-world scenario tests.

These tests validate multi-turn tool chains through ChatManager.
They use a mock LLM provider that returns scripted tool calls, simulating
how a real LLM would use tools to complete complex tasks.

Run with: pytest tests/scenarios/ -xvs
Run smoke tests with real LLM: pytest tests/scenarios/ -xvs --run-live
"""

import pytest
from dataclasses import dataclass, field


@dataclass
class MockToolResponse:
    """A scripted LLM response — either tool calls or text."""
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)


@dataclass
class ScenarioTurn:
    """One turn in a scripted conversation."""
    user_message: str
    expected_tool_calls: list[str] = field(default_factory=list)
    mock_responses: list[MockToolResponse] = field(default_factory=list)


class ToolCallTracker:
    """Track all tool calls made during a scenario."""

    def __init__(self):
        self.calls: list[dict] = []

    def record(self, name: str, args: dict, result: str):
        self.calls.append({"name": name, "args": args, "result": result})

    @property
    def tool_names(self) -> list[str]:
        return [c["name"] for c in self.calls]

    def calls_for(self, tool_name: str) -> list[dict]:
        return [c for c in self.calls if c["name"] == tool_name]

    def count(self, tool_name: str) -> int:
        return len(self.calls_for(tool_name))


@pytest.fixture
def tool_tracker():
    return ToolCallTracker()


@pytest.fixture
def mock_web_search_results():
    """Realistic mock results for web_search."""
    return {
        "results": [
            {
                "title": "Export Controls and Firm Performance",
                "url": "https://www.semanticscholar.org/paper/123",
                "snippet": "We study the effect of U.S. export controls on domestic firms...",
            },
            {
                "title": "The Cost of Trade Wars on Supply Chains",
                "url": "https://www.nber.org/papers/w29000",
                "snippet": "Trade policy uncertainty disrupts global supply chains...",
            },
        ],
        "count": 2,
        "provider": "tavily",
    }


@pytest.fixture
def mock_pdf_content():
    """Realistic mock content from a PDF read."""
    return (
        "Title: Securing Technological Leadership? The Cost of Export Controls on Firms\n"
        "Authors: Matteo Crosignani, Lina Han, Marco Macchiavelli, André F. Silva\n"
        "Published: Journal of Financial Economics, 175, 104192, January 2026\n\n"
        "Abstract: We study the economic costs of U.S. export controls on domestic firms. "
        "Export controls effectively stop U.S. firms from selling to targeted Chinese companies. "
        "However, affected U.S. suppliers struggle to find new customers, resulting in $100 billion "
        "in total market cap losses across affected U.S. firms."
    )
