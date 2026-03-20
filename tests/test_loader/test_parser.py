"""Tests for dan.loader.parser — markdown file parsing."""
import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent.parent / "fixtures" / "markdown"


class TestParseAgentFile:
    def test_minimal_agent(self):
        """Parse simple_agent.md — frontmatter + ports + body."""
        from dan.loader.parser import parse_agent_file

        spec = parse_agent_file(FIXTURES / "simple_agent.md")
        assert spec.agent_type == "llm"
        assert spec.model == "claude-sonnet-4-20250514"
        assert spec.temperature == 0.7
        assert len(spec.input_ports) == 1
        assert spec.input_ports[0].name == "topic"
        assert len(spec.output_ports) == 1
        assert spec.output_ports[0].name == "ideas"
        assert "{topic}" in spec.prompt_body

    def test_full_agent(self):
        """Parse full_agent.md — all fields, system section, output schema."""
        from dan.loader.parser import parse_agent_file

        spec = parse_agent_file(FIXTURES / "full_agent.md")
        assert spec.agent_type == "llm"
        assert spec.max_tokens == 4000
        assert spec.output_schema is not None
        assert spec.retry_policy is not None
        assert spec.retry_policy["max_retries"] == 3
        assert "expert outline planner" in spec.system_prompt
        assert len(spec.input_ports) == 2
        assert len(spec.output_ports) == 1

    def test_tool_agent(self):
        from dan.loader.parser import parse_agent_file

        spec = parse_agent_file(FIXTURES / "tool_agent.md")
        assert spec.agent_type == "tool"
        assert spec.tool_id == "web_search"
        assert spec.tool_config == {"max_results": 5}

    def test_code_agent(self):
        from dan.loader.parser import parse_agent_file

        spec = parse_agent_file(FIXTURES / "code_agent.md")
        assert spec.agent_type == "code"
        assert spec.language == "python"
        assert "json.dumps" in spec.prompt_body

    def test_human_agent(self):
        from dan.loader.parser import parse_agent_file

        spec = parse_agent_file(FIXTURES / "human_agent.md")
        assert spec.agent_type == "human"
        assert spec.timeout_seconds == 300
        assert spec.default_action == "approve"

    def test_router_agent(self):
        from dan.loader.parser import parse_agent_file

        spec = parse_agent_file(FIXTURES / "router_agent.md")
        assert spec.agent_type == "router"
        assert "detailed" in spec.route_descriptions
        assert len(spec.route_descriptions) == 3

    def test_missing_file(self):
        from dan.loader.parser import parse_agent_file, ParseError

        with pytest.raises((ParseError, FileNotFoundError)):
            parse_agent_file(FIXTURES / "nonexistent.md")

    def test_malformed_frontmatter(self, tmp_path):
        from dan.loader.parser import parse_agent_file, ParseError

        bad = tmp_path / "bad.md"
        bad.write_text("---\ntype: [invalid yaml\n---\nHello")
        with pytest.raises(ParseError):
            parse_agent_file(bad)


class TestParseWorkflowFile:
    def test_simple_workflow(self):
        from dan.loader.parser import parse_workflow_file

        spec = parse_workflow_file(FIXTURES / "simple_workflow.md")
        assert spec.name == "Simple Chain"
        assert spec.format_version == 1
        assert len(spec.agents) == 3
        assert "generator" in spec.agents
        assert "planner" in spec.agents

    def test_complex_workflow(self):
        from dan.loader.parser import parse_workflow_file

        spec = parse_workflow_file(FIXTURES / "complex_workflow.md")
        assert len(spec.agents) == 6
        assert len(spec.context_declarations) == 2
        assert spec.context_declarations[0].key == "style_guide"

    def test_unsupported_version(self, tmp_path):
        from dan.loader.parser import parse_workflow_file, ParseError

        bad = tmp_path / "v99.md"
        bad.write_text("---\nformat_version: 99\n---\n## Agents\n## Flow\n")
        with pytest.raises(ParseError):
            parse_workflow_file(bad)
