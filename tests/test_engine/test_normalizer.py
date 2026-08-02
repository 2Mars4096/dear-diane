"""Tests for engine/normalizer.py — output normalization pipeline."""

from dan.engine.normalizer import OutputNormalizer


_OBJ_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string"},
        "score": {"type": "number"},
    },
    "required": ["verdict", "score"],
}


class TestOutputNormalizer:
    def test_valid_json(self):
        r = OutputNormalizer.normalize('{"verdict": "accept", "score": 0.9}', _OBJ_SCHEMA)
        assert r.success
        assert r.data == {"verdict": "accept", "score": 0.9}

    def test_fenced_json(self):
        text = '```json\n{"verdict": "revise", "score": 0.3}\n```'
        r = OutputNormalizer.normalize(text, _OBJ_SCHEMA)
        assert r.success
        assert r.data["verdict"] == "revise"

    def test_json_with_surrounding_text(self):
        text = 'Here is the result: {"verdict": "accept", "score": 0.95} Hope this helps!'
        r = OutputNormalizer.normalize(text, _OBJ_SCHEMA)
        assert r.success
        assert r.data["verdict"] == "accept"

    def test_missing_required_field(self):
        r = OutputNormalizer.normalize('{"verdict": "accept"}', _OBJ_SCHEMA)
        assert not r.success
        assert "score" in r.error_message
        assert "required" in r.error_message

    def test_type_mismatch(self):
        r = OutputNormalizer.normalize('{"verdict": 123, "score": 0.5}', _OBJ_SCHEMA)
        assert not r.success
        assert "string" in r.error_message

    def test_invalid_json(self):
        r = OutputNormalizer.normalize("{not valid json}", _OBJ_SCHEMA)
        assert not r.success
        assert "invalid JSON" in r.error_message.lower() or "JSON" in r.error_message

    def test_no_json_at_all(self):
        r = OutputNormalizer.normalize("I don't know the answer.", _OBJ_SCHEMA)
        assert not r.success

    def test_nested_object(self):
        schema = {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                }
            },
            "required": ["data"],
        }
        r = OutputNormalizer.normalize('{"data": {"value": 42}}', schema)
        assert r.success
        assert r.data["data"]["value"] == 42

    def test_array_schema(self):
        schema = {
            "type": "array",
            "items": {"type": "string"},
        }
        r = OutputNormalizer.normalize('["a", "b", "c"]', schema)
        assert r.success
        assert r.data == ["a", "b", "c"]
