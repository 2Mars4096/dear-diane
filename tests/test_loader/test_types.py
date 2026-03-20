"""Tests for dan.loader.types — port type inference."""
import json
import pytest
from pathlib import Path
from dan.loader.types import (
    infer_port_schema, infer_schema_from_name,
    load_linked_schema, SchemaLoadError, DEFAULT_LLM_OUTPUT_SCHEMA,
)


class TestExplicitTypes:
    def test_string(self):
        assert infer_port_schema("x", "string") == {"type": "string"}

    def test_integer(self):
        assert infer_port_schema("x", "integer") == {"type": "integer"}

    def test_int_alias(self):
        assert infer_port_schema("x", "int") == {"type": "integer"}

    def test_number(self):
        assert infer_port_schema("x", "number") == {"type": "number"}

    def test_boolean(self):
        assert infer_port_schema("x", "boolean") == {"type": "boolean"}

    def test_array(self):
        assert infer_port_schema("x", "array") == {"type": "array"}

    def test_typed_array(self):
        result = infer_port_schema("x", "Paper[]")
        assert result["type"] == "array"

    def test_custom_type(self):
        result = infer_port_schema("x", "CustomObj")
        assert result["type"] == "object"
        assert "Custom type" in result.get("description", "")


class TestNameInference:
    def test_array_suffix(self):
        assert infer_schema_from_name("items[]")["type"] == "array"

    def test_count(self):
        assert infer_schema_from_name("count")["type"] == "integer"

    def test_num_prefix(self):
        assert infer_schema_from_name("num_pages")["type"] == "integer"

    def test_is_prefix(self):
        assert infer_schema_from_name("is_valid")["type"] == "boolean"

    def test_has_prefix(self):
        assert infer_schema_from_name("has_errors")["type"] == "boolean"

    def test_flag_suffix(self):
        assert infer_schema_from_name("debug_flag")["type"] == "boolean"

    def test_score(self):
        assert infer_schema_from_name("score")["type"] == "number"

    def test_rate_suffix(self):
        assert infer_schema_from_name("error_rate")["type"] == "number"

    def test_default_string(self):
        assert infer_schema_from_name("topic")["type"] == "string"

    def test_default_output_schema(self):
        assert DEFAULT_LLM_OUTPUT_SCHEMA == {"type": "string"}


class TestLinkedSchema:
    def test_load_valid(self, tmp_path):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        f = tmp_path / "test.json"
        f.write_text(json.dumps(schema))
        result = load_linked_schema("test.json", tmp_path)
        assert result == schema

    def test_missing_file(self, tmp_path):
        with pytest.raises(SchemaLoadError):
            load_linked_schema("missing.json", tmp_path)

    def test_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json")
        with pytest.raises(SchemaLoadError):
            load_linked_schema("bad.json", tmp_path)

    def test_non_object(self, tmp_path):
        f = tmp_path / "array.json"
        f.write_text("[1, 2, 3]")
        with pytest.raises(SchemaLoadError):
            load_linked_schema("array.json", tmp_path)
