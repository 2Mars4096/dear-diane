"""Snapshot tests for loader (markdown compiler) output.

Compile known markdown fixtures through dan.loader.compile_workflow(), then
compare the output graph JSON to stored snapshots. Run with UPDATE_SNAPSHOTS=1
to regenerate.
"""

from __future__ import annotations

from pathlib import Path

from dan.loader import compile_workflow

from tests.test_snapshots.conftest import assert_snapshot, update_snapshots

FIXTURES = Path(__file__).parent.parent / "fixtures" / "markdown"
EXAMPLES = Path(__file__).parent.parent.parent / "examples"


def test_loader_simple_workflow():
    """Compile simple_workflow.md and compare to snapshot."""
    result = compile_workflow(FIXTURES / "simple_workflow.md")
    assert result.graph is not None, f"Compilation failed: {result.diagnostics}"
    assert_snapshot("loader_simple_workflow", result.graph.model_dump(), update=update_snapshots())


def test_loader_complex_workflow():
    """Compile complex_workflow.md (foreach, loop, if) and compare to snapshot."""
    result = compile_workflow(FIXTURES / "complex_workflow.md")
    assert result.graph is not None, f"Compilation failed: {result.diagnostics}"
    assert_snapshot("loader_complex_workflow", result.graph.model_dump(), update=update_snapshots())


def test_loader_paper_writing():
    """Compile examples/paper_writing_md/workflow.md and compare to snapshot."""
    result = compile_workflow(EXAMPLES / "paper_writing_md" / "workflow.md")
    assert result.graph is not None, f"Compilation failed: {result.diagnostics}"
    assert_snapshot("loader_paper_writing", result.graph.model_dump(), update=update_snapshots())
