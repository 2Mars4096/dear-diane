"""Tests for the golden intent fixture loader."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.quality_suite.loader import load_golden_intents, _validate_against_schema

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "golden_intents"

FAMILIES = {"paper_writing", "rag_qa", "multi_step_analysis", "review_loop", "fan_out_fan_in"}
VARIANTS = {"simple", "standard", "complex"}


class TestLoadGoldenIntents:
    """All 18 fixtures load, validate, and sort correctly."""

    def test_loads_all_18_fixtures(self):
        fixtures = load_golden_intents()
        assert len(fixtures) == 18

    def test_each_fixture_has_required_fields(self):
        fixtures = load_golden_intents()
        for f in fixtures:
            assert "intent" in f, f"Missing 'intent' in {f.get('_source_file')}"
            assert "family" in f, f"Missing 'family' in {f.get('_source_file')}"
            assert "variant" in f, f"Missing 'variant' in {f.get('_source_file')}"
            assert "expected" in f, f"Missing 'expected' in {f.get('_source_file')}"

    def test_each_fixture_has_valid_family(self):
        fixtures = load_golden_intents()
        for f in fixtures:
            assert f["family"] in FAMILIES, (
                f"Invalid family {f['family']!r} in {f.get('_source_file')}"
            )

    def test_each_fixture_has_valid_variant(self):
        fixtures = load_golden_intents()
        for f in fixtures:
            assert f["variant"] in VARIANTS, (
                f"Invalid variant {f['variant']!r} in {f.get('_source_file')}"
            )

    def test_expected_has_required_subfields(self):
        fixtures = load_golden_intents()
        for f in fixtures:
            exp = f["expected"]
            assert "min_nodes" in exp, f"Missing 'min_nodes' in {f.get('_source_file')}"
            assert "node_types" in exp, f"Missing 'node_types' in {f.get('_source_file')}"
            assert "topology" in exp, f"Missing 'topology' in {f.get('_source_file')}"
            assert isinstance(exp["min_nodes"], int)
            assert exp["min_nodes"] >= 1

    def test_sorted_by_family_then_variant(self):
        fixtures = load_golden_intents()
        variant_order = {"simple": 0, "standard": 1, "complex": 2}
        keys = [(f["family"], variant_order[f["variant"]]) for f in fixtures]
        assert keys == sorted(keys)

    def test_15_standard_plus_3_edge_cases(self):
        fixtures = load_golden_intents()
        edge = [f for f in fixtures if f.get("edge_case") is True]
        standard = [f for f in fixtures if not f.get("edge_case")]
        assert len(edge) == 3
        assert len(standard) == 15

    def test_3_fixtures_per_family_in_standard(self):
        fixtures = load_golden_intents()
        standard = [f for f in fixtures if not f.get("edge_case")]
        from collections import Counter
        family_counts = Counter(f["family"] for f in standard)
        for fam in FAMILIES:
            assert family_counts[fam] == 3, f"Expected 3 fixtures for {fam}, got {family_counts[fam]}"


class TestEdgeCaseFixtures:
    """Edge-case fixtures are correctly marked and have expected properties."""

    def test_edge_empty_inputs(self):
        fixtures = load_golden_intents()
        empty = [f for f in fixtures if f.get("_source_file") == "edge_empty_inputs.json"]
        assert len(empty) == 1
        assert empty[0]["edge_case"] is True
        assert "vague" in empty[0].get("notes", "").lower() or "underspecified" in empty[0].get("notes", "").lower()

    def test_edge_conflicting(self):
        fixtures = load_golden_intents()
        conf = [f for f in fixtures if f.get("_source_file") == "edge_conflicting.json"]
        assert len(conf) == 1
        assert conf[0]["edge_case"] is True

    def test_edge_very_large(self):
        fixtures = load_golden_intents()
        large = [f for f in fixtures if f.get("_source_file") == "edge_very_large.json"]
        assert len(large) == 1
        assert large[0]["edge_case"] is True
        assert large[0]["expected"]["min_nodes"] >= 30


class TestSchemaValidation:
    """Schema validation catches invalid fixtures."""

    def test_validates_against_schema_json(self):
        schema_path = FIXTURES_DIR / "schema.json"
        with open(schema_path) as f:
            schema = json.load(f)
        fixtures = load_golden_intents()
        for f in fixtures:
            clean = {k: v for k, v in f.items() if k != "_source_file"}
            _validate_against_schema(clean, schema)

    def test_rejects_missing_intent(self):
        schema_path = FIXTURES_DIR / "schema.json"
        with open(schema_path) as f:
            schema = json.load(f)
        bad = {"family": "rag_qa", "variant": "simple", "expected": {"min_nodes": 1, "node_types": [], "topology": {}}}
        with pytest.raises((Exception,)):
            _validate_against_schema(bad, schema)

    def test_rejects_invalid_family(self):
        schema_path = FIXTURES_DIR / "schema.json"
        with open(schema_path) as f:
            schema = json.load(f)
        bad = {
            "intent": "test",
            "family": "nonexistent_family",
            "variant": "simple",
            "expected": {"min_nodes": 1, "node_types": [], "topology": {}},
        }
        with pytest.raises((Exception,)):
            _validate_against_schema(bad, schema)

    def test_rejects_invalid_variant(self):
        schema_path = FIXTURES_DIR / "schema.json"
        with open(schema_path) as f:
            schema = json.load(f)
        bad = {
            "intent": "test",
            "family": "rag_qa",
            "variant": "mega",
            "expected": {"min_nodes": 1, "node_types": [], "topology": {}},
        }
        with pytest.raises((Exception,)):
            _validate_against_schema(bad, schema)


class TestLoaderEdgeCases:
    """Loader handles missing directories and non-JSON files."""

    def test_raises_on_missing_schema(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_golden_intents(str(tmp_path))

    def test_skips_non_json_files(self, tmp_path):
        schema_src = FIXTURES_DIR / "schema.json"
        (tmp_path / "schema.json").write_text(schema_src.read_text())
        (tmp_path / "readme.txt").write_text("not a fixture")
        (tmp_path / "valid.json").write_text(json.dumps({
            "intent": "test",
            "family": "rag_qa",
            "variant": "simple",
            "expected": {"min_nodes": 1, "node_types": [], "topology": {}},
        }))
        result = load_golden_intents(str(tmp_path))
        assert len(result) == 1
        assert result[0]["intent"] == "test"

    def test_custom_fixtures_dir(self, tmp_path):
        schema_src = FIXTURES_DIR / "schema.json"
        (tmp_path / "schema.json").write_text(schema_src.read_text())
        for i in range(3):
            (tmp_path / f"test_{i}.json").write_text(json.dumps({
                "intent": f"intent {i}",
                "family": "rag_qa",
                "variant": "simple",
                "expected": {"min_nodes": 1, "node_types": [], "topology": {}},
            }))
        result = load_golden_intents(str(tmp_path))
        assert len(result) == 3
