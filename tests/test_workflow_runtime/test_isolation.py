"""Verify workflow-runtime modules can be imported without server startup.

Part of 41-4 (Workflow Runtime Isolation) — task 4-2.

All engine/executor → server imports are lazy (deferred to function bodies
with try/except), so Python-level module import should succeed. These tests
lock that property in as a regression gate.
"""

from __future__ import annotations

import importlib
import sys


def _import_fresh(dotted: str) -> bool:
    """Attempt to import *dotted* and return True on success."""
    try:
        importlib.import_module(dotted)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------
# Tier 1: shared schema — zero server dependencies expected
# ------------------------------------------------------------------


def test_models_importable():
    """models/ has no server dependencies."""
    assert _import_fresh("dan.models"), "dan.models should import cleanly"
    assert _import_fresh("dan.models.graph"), "dan.models.graph should import cleanly"
    assert _import_fresh("dan.models.nodes"), "dan.models.nodes should import cleanly"
    assert _import_fresh("dan.models.edges"), "dan.models.edges should import cleanly"


def test_builder_importable():
    """builder/ has no server dependencies."""
    assert _import_fresh("dan.builder"), "dan.builder should import cleanly"


def test_loader_importable():
    """loader/ has no server dependencies."""
    assert _import_fresh("dan.loader"), "dan.loader should import cleanly"


# ------------------------------------------------------------------
# Tier 2: engine / executors — importable at module level despite
# having lazy server imports inside function bodies
# ------------------------------------------------------------------


def test_engine_module_importable():
    """engine/ module-level import succeeds.

    Reverse ``engine/ → server.*`` imports have been removed or injected at
    composition time (41-4). If this test *fails*, someone broke engine
    package initialization.
    """
    assert _import_fresh("dan.engine"), "dan.engine should import at module level"
    assert _import_fresh("dan.engine.scheduler"), "dan.engine.scheduler should import"


def test_workflow_runtime_module_importable():
    """workflow_runtime/ exposes the workflow facade without server startup."""
    assert _import_fresh("dan.workflow_runtime"), "dan.workflow_runtime should import at module level"


def test_executor_module_importable():
    """executors/ module-level import succeeds (no server imports in llm executor)."""
    assert _import_fresh("dan.executors"), "dan.executors should import at module level"


# ------------------------------------------------------------------
# Tier 3: verify specific violation files are individually importable
# ------------------------------------------------------------------


def test_skill_tracker_importable():
    """skill_tracker.py has only lazy imports from server."""
    assert _import_fresh("dan.engine.skill_tracker")


def test_behavior_store_importable():
    """behavior_store.py has only lazy imports from server."""
    assert _import_fresh("dan.engine.behavior_store")


def test_preference_extractor_importable():
    """preference_extractor.py has only lazy imports from server."""
    assert _import_fresh("dan.engine.preference_extractor")


def test_memory_kernel_importable():
    """memory_kernel.py must not depend on server (domain hook is injected)."""
    assert _import_fresh("dan.engine.memory_kernel")


def test_llm_executor_importable():
    """executors/llm.py has only lazy imports from server."""
    assert _import_fresh("dan.executors.llm")


# ------------------------------------------------------------------
# Tier 4: llm_core (should be fully independent)
# ------------------------------------------------------------------


def test_llm_core_importable():
    """llm_core/ has no server dependencies (by design, 41-1)."""
    assert _import_fresh("dan.llm_core"), "dan.llm_core should import cleanly"
