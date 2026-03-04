"""Node test-case schema and filesystem-backed persistence.

Test cases are per-node fixtures with optional expected outputs that can be
executed in isolation.  Storage layout mirrors RunStore::

    {base_dir}/
        test_cases/
            {workflow_id}/
                {node_id}.json   # JSON array of NodeTestCase dicts
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class NodeTestCase(BaseModel):
    """A single test-case fixture for a graph node."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    node_id: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_outputs: dict[str, Any] | None = None
    assertions: list[str] | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class TestCaseRunResult(BaseModel):
    """Result of executing a single test case."""

    passed: bool
    actual_outputs: dict[str, Any] = Field(default_factory=dict)
    expected_outputs: dict[str, Any] | None = None
    diff: dict[str, Any] | None = None
    execution_metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestCaseStore:
    """Filesystem-backed CRUD for node test cases.

    Each node's test cases are stored as a JSON array in a single file:
    ``{base_dir}/test_cases/{workflow_id}/{node_id}.json``
    """

    __test__ = False  # prevent pytest collection

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir) / "test_cases"
        self._base.mkdir(parents=True, exist_ok=True)

    def _node_path(self, workflow_id: str, node_id: str) -> Path:
        return self._base / workflow_id / f"{node_id}.json"

    def _load_raw(self, workflow_id: str, node_id: str) -> list[dict[str, Any]]:
        path = self._node_path(workflow_id, node_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            return []
        except Exception:
            logger.warning("Corrupted test-case file %s, returning empty", path)
            return []

    def _save_raw(self, workflow_id: str, node_id: str, cases: list[dict[str, Any]]) -> None:
        d = self._base / workflow_id
        d.mkdir(parents=True, exist_ok=True)
        path = self._node_path(workflow_id, node_id)
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(cases, default=str, indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            logger.exception("Failed to save test cases %s/%s", workflow_id, node_id)
            tmp.unlink(missing_ok=True)

    # -- Public API -----------------------------------------------------------

    def list_cases(self, workflow_id: str, node_id: str) -> list[NodeTestCase]:
        raw = self._load_raw(workflow_id, node_id)
        result: list[NodeTestCase] = []
        for item in raw:
            try:
                result.append(NodeTestCase.model_validate(item))
            except Exception:
                logger.warning("Skipping invalid test case in %s/%s", workflow_id, node_id)
        return result

    def get_case(self, workflow_id: str, node_id: str, case_id: str) -> NodeTestCase | None:
        for tc in self.list_cases(workflow_id, node_id):
            if tc.id == case_id:
                return tc
        return None

    def save_case(self, workflow_id: str, node_id: str, case: NodeTestCase) -> None:
        raw = self._load_raw(workflow_id, node_id)
        # Upsert: replace existing or append
        found = False
        for i, item in enumerate(raw):
            if item.get("id") == case.id:
                raw[i] = case.model_dump()
                found = True
                break
        if not found:
            raw.append(case.model_dump())
        self._save_raw(workflow_id, node_id, raw)

    def delete_case(self, workflow_id: str, node_id: str, case_id: str) -> bool:
        raw = self._load_raw(workflow_id, node_id)
        new_raw = [item for item in raw if item.get("id") != case_id]
        if len(new_raw) == len(raw):
            return False
        self._save_raw(workflow_id, node_id, new_raw)
        return True
