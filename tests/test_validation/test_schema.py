"""Port schema compatibility tests."""

from dan.validation.schema import check_schema_compatible


class TestPrimitiveTypes:
    def test_matching_string(self):
        assert check_schema_compatible({"type": "string"}, {"type": "string"}) == []

    def test_mismatched_type(self):
        errors = check_schema_compatible({"type": "integer"}, {"type": "string"})
        assert len(errors) == 1
        assert "type mismatch" in errors[0]


class TestObjectSchemas:
    def test_exact_match(self):
        schema = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        }
        assert check_schema_compatible(schema, schema) == []

    def test_source_superset_is_compatible(self):
        source = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "abstract": {"type": "string"},
            },
            "required": ["title", "abstract"],
        }
        target = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        }
        assert check_schema_compatible(source, target) == []

    def test_missing_required_field(self):
        source = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        target = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name", "age"],
        }
        errors = check_schema_compatible(source, target)
        assert len(errors) == 1
        assert "age" in errors[0]

    def test_field_type_mismatch(self):
        source = {
            "type": "object",
            "properties": {"score": {"type": "string"}},
            "required": ["score"],
        }
        target = {
            "type": "object",
            "properties": {"score": {"type": "number"}},
            "required": ["score"],
        }
        errors = check_schema_compatible(source, target)
        assert len(errors) == 1
        assert "score" in errors[0]


class TestArraySchemas:
    def test_matching_items(self):
        schema = {"type": "array", "items": {"type": "string"}}
        assert check_schema_compatible(schema, schema) == []

    def test_item_type_mismatch(self):
        source = {"type": "array", "items": {"type": "integer"}}
        target = {"type": "array", "items": {"type": "string"}}
        errors = check_schema_compatible(source, target)
        assert len(errors) == 1

    def test_target_items_source_none(self):
        source = {"type": "array"}
        target = {"type": "array", "items": {"type": "string"}}
        errors = check_schema_compatible(source, target)
        assert len(errors) == 1


class TestNestedObjects:
    def test_nested_compatible(self):
        source = {
            "type": "object",
            "properties": {
                "author": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "email": {"type": "string"}},
                    "required": ["name", "email"],
                },
            },
            "required": ["author"],
        }
        target = {
            "type": "object",
            "properties": {
                "author": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            "required": ["author"],
        }
        assert check_schema_compatible(source, target) == []

    def test_nested_missing_field(self):
        source = {
            "type": "object",
            "properties": {
                "author": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            "required": ["author"],
        }
        target = {
            "type": "object",
            "properties": {
                "author": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            "required": ["author"],
        }
        errors = check_schema_compatible(source, target)
        assert any("name" in e for e in errors)
