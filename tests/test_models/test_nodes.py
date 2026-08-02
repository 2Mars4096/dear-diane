import json

from dan.models.nodes import LLMOperator, ToolOperator, CodeOperator, Position
from dan.models.ports import InputPort, OutputPort


class TestLLMOperator:
    def test_construction(self):
        node = LLMOperator(
            id="llm1",
            name="Summarizer",
            model="gpt-4o",
            prompt_template="Summarize: {text}",
            input_ports=[InputPort(name="text", json_schema={"type": "string"})],
            output_ports=[OutputPort(name="summary", json_schema={"type": "string"})],
        )
        assert node.node_type == "llm_operator"
        assert node.temperature == 0.7
        assert node.position == Position(x=0, y=0)

    def test_json_round_trip(self):
        node = LLMOperator(
            id="llm1",
            name="Writer",
            model="claude-4",
            prompt_template="Write about {topic}",
            system_prompt="You are a writer.",
            temperature=0.9,
            max_tokens=4096,
        )
        data = node.model_dump()
        assert data["node_type"] == "llm_operator"
        restored = LLMOperator.model_validate(data)
        assert restored == node

    def test_json_string_round_trip(self):
        node = LLMOperator(id="n", name="N", model="m", prompt_template="p")
        json_str = node.model_dump_json()
        restored = LLMOperator.model_validate_json(json_str)
        assert restored == node


class TestToolOperator:
    def test_construction(self):
        node = ToolOperator(
            id="tool1",
            name="Web Search",
            tool_id="web_search",
            tool_config={"max_results": 10},
        )
        assert node.node_type == "tool_operator"
        assert node.tool_id == "web_search"

    def test_json_round_trip(self):
        node = ToolOperator(id="t1", name="DB", tool_id="postgres_query")
        assert ToolOperator.model_validate(node.model_dump()) == node


class TestCodeOperator:
    def test_construction(self):
        node = CodeOperator(
            id="code1",
            name="Transformer",
            code="result = input_data['x'] * 2",
        )
        assert node.node_type == "code_operator"
        assert node.language == "python"

    def test_json_round_trip(self):
        node = CodeOperator(id="c1", name="C", code="print(1)")
        data = json.loads(node.model_dump_json())
        assert data["node_type"] == "code_operator"
        assert CodeOperator.model_validate(data) == node
