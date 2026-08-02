"""Tests for two-tier reference resolution (18-1 task 4-4).

When pass_by_reference=True on a ContextEdge, large values are stored in
ArtifactStore and a lightweight reference dict is passed. Downstream nodes
resolve refs from ArtifactStore before execution.
"""

import pytest

from dan.engine.context_runtime import (
    ArtifactStore,
    create_reference,
    resolve_reference,
)


class TestCreateReference:
    """Store a large value and get back a reference dict."""

    def test_store_large_value_returns_reference_dict(self):
        artifacts = ArtifactStore()
        value = "x" * 5000  # Large string
        ref = create_reference("ref:test:out:1", value, artifacts)
        assert isinstance(ref, dict)
        assert ref["__ref__"] == "ref:test:out:1"
        assert "__preview__" in ref
        assert "__size__" in ref
        assert ref["__size__"] == 5000
        assert len(ref["__preview__"]) <= 200

    def test_preview_chars_respected(self):
        artifacts = ArtifactStore()
        value = "abcdefghij" * 50
        ref = create_reference("ref:a:b:1", value, artifacts, preview_chars=50)
        assert len(ref["__preview__"]) == 50


class TestResolveReference:
    """Resolve reference dict to original value."""

    def test_resolve_returns_original_value(self):
        artifacts = ArtifactStore()
        original = {"data": [1, 2, 3], "nested": True}
        ref = create_reference("ref:node:port:1", original, artifacts)
        resolved = resolve_reference(ref, artifacts)
        assert resolved == original

    def test_non_ref_value_passthrough(self):
        artifacts = ArtifactStore()
        value = "plain string"
        assert resolve_reference(value, artifacts) == value
        assert resolve_reference(42, artifacts) == 42
        assert resolve_reference([1, 2], artifacts) == [1, 2]

    def test_dict_without_ref_passthrough(self):
        artifacts = ArtifactStore()
        value = {"a": 1, "b": 2}
        assert resolve_reference(value, artifacts) == value

    def test_missing_reference_returns_ref_unchanged(self):
        artifacts = ArtifactStore()
        ref = {"__ref__": "ref:nonexistent:port:999", "__preview__": "...", "__size__": 0}
        resolved = resolve_reference(ref, artifacts)
        assert resolved == ref


class TestRoundTrip:
    """Store -> reference -> resolve -> equals original."""

    def test_roundtrip_string(self):
        artifacts = ArtifactStore()
        original = "Hello, world! " * 100
        ref = create_reference("ref:src:out:1", original, artifacts)
        resolved = resolve_reference(ref, artifacts)
        assert resolved == original

    def test_roundtrip_dict(self):
        artifacts = ArtifactStore()
        original = {"key": "value", "list": [1, 2, 3], "nested": {"a": 1}}
        ref = create_reference("ref:src:out:2", original, artifacts)
        resolved = resolve_reference(ref, artifacts)
        assert resolved == original

    def test_roundtrip_list(self):
        artifacts = ArtifactStore()
        original = [{"id": i, "name": f"item_{i}"} for i in range(10)]
        ref = create_reference("ref:src:out:3", original, artifacts)
        resolved = resolve_reference(ref, artifacts)
        assert resolved == original


class TestSmallValuesPassThrough:
    """When value is below threshold, scheduler passes through unchanged."""

    def test_create_reference_stores_anyway(self):
        """create_reference always stores; threshold is applied at write time."""
        artifacts = ArtifactStore()
        small = "hi"
        ref = create_reference("ref:small:out:1", small, artifacts)
        resolved = resolve_reference(ref, artifacts)
        assert resolved == small
