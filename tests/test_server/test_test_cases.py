"""Tests for TestCaseStore persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dan.server.test_cases import NodeTestCase, TestCaseStore


@pytest.fixture
def store(tmp_path: Path) -> TestCaseStore:
    return TestCaseStore(base_dir=tmp_path)


def _make_case(**overrides) -> NodeTestCase:
    defaults = {
        "name": "test-1",
        "node_id": "node-a",
        "inputs": {"prompt": "hello"},
        "expected_outputs": {"output": "world"},
        "tags": ["smoke"],
        "notes": "A simple test",
    }
    defaults.update(overrides)
    return NodeTestCase(**defaults)


class TestCaseStoreBasics:
    def test_save_and_list(self, store: TestCaseStore):
        case = _make_case()
        store.save_case("wf-1", "node-a", case)
        cases = store.list_cases("wf-1", "node-a")
        assert len(cases) == 1
        assert cases[0].name == "test-1"
        assert cases[0].inputs == {"prompt": "hello"}

    def test_get_case(self, store: TestCaseStore):
        case = _make_case()
        store.save_case("wf-1", "node-a", case)
        loaded = store.get_case("wf-1", "node-a", case.id)
        assert loaded is not None
        assert loaded.id == case.id
        assert loaded.expected_outputs == {"output": "world"}

    def test_get_case_not_found(self, store: TestCaseStore):
        assert store.get_case("wf-1", "node-a", "nonexistent") is None

    def test_upsert(self, store: TestCaseStore):
        case = _make_case()
        store.save_case("wf-1", "node-a", case)
        # Update the same case
        case.name = "updated-test"
        case.inputs = {"prompt": "updated"}
        store.save_case("wf-1", "node-a", case)
        cases = store.list_cases("wf-1", "node-a")
        assert len(cases) == 1
        assert cases[0].name == "updated-test"
        assert cases[0].inputs == {"prompt": "updated"}

    def test_delete_case(self, store: TestCaseStore):
        case = _make_case()
        store.save_case("wf-1", "node-a", case)
        assert store.delete_case("wf-1", "node-a", case.id) is True
        assert store.list_cases("wf-1", "node-a") == []

    def test_delete_case_not_found(self, store: TestCaseStore):
        assert store.delete_case("wf-1", "node-a", "nonexistent") is False

    def test_multiple_cases(self, store: TestCaseStore):
        case1 = _make_case(name="test-1")
        case2 = _make_case(name="test-2")
        store.save_case("wf-1", "node-a", case1)
        store.save_case("wf-1", "node-a", case2)
        cases = store.list_cases("wf-1", "node-a")
        assert len(cases) == 2
        names = {c.name for c in cases}
        assert names == {"test-1", "test-2"}

    def test_different_nodes_isolated(self, store: TestCaseStore):
        case_a = _make_case(name="for-a", node_id="node-a")
        case_b = _make_case(name="for-b", node_id="node-b")
        store.save_case("wf-1", "node-a", case_a)
        store.save_case("wf-1", "node-b", case_b)
        assert len(store.list_cases("wf-1", "node-a")) == 1
        assert len(store.list_cases("wf-1", "node-b")) == 1
        assert store.list_cases("wf-1", "node-a")[0].name == "for-a"

    def test_empty_list(self, store: TestCaseStore):
        assert store.list_cases("wf-1", "node-x") == []

    def test_corrupted_file(self, store: TestCaseStore, tmp_path: Path):
        d = tmp_path / "test_cases" / "wf-1"
        d.mkdir(parents=True, exist_ok=True)
        (d / "node-a.json").write_text("not valid json", encoding="utf-8")
        cases = store.list_cases("wf-1", "node-a")
        assert cases == []

    def test_persistence_format(self, store: TestCaseStore, tmp_path: Path):
        case = _make_case()
        store.save_case("wf-1", "node-a", case)
        path = tmp_path / "test_cases" / "wf-1" / "node-a.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "test-1"

    def test_case_with_no_expected_outputs(self, store: TestCaseStore):
        case = _make_case(expected_outputs=None)
        store.save_case("wf-1", "node-a", case)
        loaded = store.get_case("wf-1", "node-a", case.id)
        assert loaded is not None
        assert loaded.expected_outputs is None
