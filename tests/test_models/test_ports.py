from dan.models.ports import InputPort, OutputPort


class TestInputPort:
    def test_minimal(self):
        p = InputPort(name="text")
        assert p.name == "text"
        assert p.json_schema == {}
        assert p.required is True

    def test_with_schema(self):
        schema = {"type": "string"}
        p = InputPort(name="query", json_schema=schema, required=False, description="Search term")
        assert p.json_schema == schema
        assert p.required is False

    def test_json_round_trip(self):
        p = InputPort(name="data", json_schema={"type": "object"}, required=True)
        data = p.model_dump()
        restored = InputPort.model_validate(data)
        assert restored == p


class TestOutputPort:
    def test_minimal(self):
        p = OutputPort(name="result")
        assert p.json_schema == {}

    def test_json_round_trip(self):
        p = OutputPort(name="out", json_schema={"type": "array", "items": {"type": "string"}})
        assert OutputPort.model_validate(p.model_dump()) == p
