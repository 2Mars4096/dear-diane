"""Shared workflow loading utilities.

Consolidates graph loading from JSON, Markdown, and Python sources.
Used by CLI (dan-run), gateway dispatch, and publish (dan-publish).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class WorkflowLoadError(Exception):
    """Raised when a workflow cannot be loaded."""


@contextmanager
def _temporary_sys_path(paths: list[Path]) -> Any:
    originals = list(sys.path)
    for path in reversed(paths):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
    try:
        yield
    finally:
        sys.path[:] = originals


def detect_source_type(path: Path) -> str:
    """Detect workflow source type from file extension.

    Returns 'json', 'markdown', 'python', or raises WorkflowLoadError.
    """
    if path.is_dir():
        return "markdown"
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in (".md", ".markdown"):
        return "markdown"
    if suffix == ".py":
        return "python"
    raise WorkflowLoadError(f"Unsupported workflow file extension: {suffix}")


def load_graph(path: Path) -> Any:
    """Load a Graph from a file path (JSON, Markdown, or Python).

    Raises WorkflowLoadError if loading fails.
    """
    source_type = detect_source_type(path)
    if source_type == "json":
        return load_graph_from_json(path)
    if source_type == "markdown":
        return load_graph_from_markdown(path)
    if source_type == "python":
        return load_graph_from_python(path)
    raise WorkflowLoadError(f"Unknown source type: {source_type}")


def load_graph_from_json(path: Path) -> Any:
    """Load a Graph from a JSON file."""
    from dan.models.graph import Graph

    try:
        with open(path) as f:
            data = json.load(f)
        return Graph.model_validate(data)
    except (json.JSONDecodeError, FileNotFoundError, ValueError) as exc:
        raise WorkflowLoadError(f"Failed to load JSON workflow from {path}: {exc}") from exc


def load_graph_from_markdown(path: Path) -> Any:
    """Load a Graph from a Markdown agent file or directory."""
    from dan.loader import load

    try:
        return load(path)
    except Exception as exc:
        raise WorkflowLoadError(f"Failed to load Markdown workflow from {path}: {exc}") from exc


def load_graph_from_python(path: Path) -> Any:
    """Load a Graph from a Python file exporting 'graph' or 'build()'."""
    try:
        module_name = f"_dan_user_workflow_{abs(hash(path.resolve()))}"
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if spec is None or spec.loader is None:
            raise WorkflowLoadError(f"Cannot load Python module from: {path}")
        mod = importlib.util.module_from_spec(spec)
        import_roots = [path.resolve().parent]
        cwd = Path.cwd().resolve()
        if cwd not in import_roots:
            import_roots.append(cwd)
        with _temporary_sys_path(import_roots):
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        if hasattr(mod, "graph"):
            return mod.graph
        if hasattr(mod, "build") and callable(mod.build):
            return mod.build()
        raise WorkflowLoadError(
            f"Python file {path} must export 'graph' or 'build()' returning a Graph."
        )
    except WorkflowLoadError:
        raise
    except Exception as exc:
        raise WorkflowLoadError(f"Failed to load Python workflow from {path}: {exc}") from exc


def load_workflows_from_directory(directory: Path) -> list[tuple[Any, str | None]]:
    """Load all workflow graphs from a directory.

    Returns list of (Graph, name_override) tuples.
    Skips files that fail to load (logs warning).
    """
    results: list[tuple[Any, str | None]] = []
    for f in sorted(directory.iterdir()):
        if f.suffix.lower() in (".json", ".md", ".markdown", ".py"):
            try:
                graph = load_graph(f)
                results.append((graph, None))
            except WorkflowLoadError:
                logger.warning("Skipping invalid workflow file: %s", f)
    return results


def validate_workflow_path(path: Path, workspace: Path) -> Path:
    """Validate that a workflow path is within the workspace directory.

    Returns the resolved path. Raises WorkflowLoadError if path is
    outside the workspace (prevents path traversal).
    """
    resolved = path.resolve()
    workspace_resolved = workspace.resolve()
    try:
        resolved.relative_to(workspace_resolved)
    except ValueError:
        raise WorkflowLoadError(
            f"Workflow path is outside the workspace directory"
        )
    if not resolved.exists():
        raise WorkflowLoadError(f"Workflow not found")
    return resolved
