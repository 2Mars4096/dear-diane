from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "dan"


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def test_universal_cell_builder_is_only_imported_by_universal_organism_runner() -> None:
    importers: list[str] = []
    for path in sorted((SRC_ROOT / "worker" / "organisms").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "dan.worker.cell":
                continue
            if any(alias.name == "build_cell" for alias in node.names):
                importers.append(_rel(path))

    assert importers == ["src/dan/worker/organisms/universal_organism.py"]


def test_cli_cell_builder_and_router_legacy_allowlist_does_not_grow() -> None:
    allowed = {
        "src/dan/cli/super_organism.py": {
            "_LIVE_FILE_WRITE_SAFE_LINE_LIMIT",
            "_LIVE_FILE_WRITE_SAFE_WORD_LIMIT",
            "_is_website_objective",
            "_supports_live_execution",
        }
    }
    found: dict[str, set[str]] = defaultdict(set)

    for path in sorted((SRC_ROOT / "cli").glob("*.py")):
        rel_path = _rel(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _name(node.func) in {"CompletionHints", "WorkerDefinition"}:
                found[rel_path].add(_name(node.func))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if (
                    node.name.startswith("_is_")
                    and node.name.endswith("_objective")
                    or node.name.startswith("_supports_")
                    and node.name.endswith("_execution")
                ):
                    found[rel_path].add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if target.id.startswith("_LIVE_") or target.id == "_WEBSITE_TEMPLATE_PHRASES":
                        found[rel_path].add(target.id)

    unexpected = {
        path: sorted(markers - allowed.get(path, set()))
        for path, markers in found.items()
        if markers - allowed.get(path, set())
    }

    assert unexpected == {}


def test_organism_workerdefinition_constructor_allowlist_does_not_grow() -> None:
    allowed_direct_constructor_counts = {
        "src/dan/worker/organisms/coding_execution.py": 2,
        "src/dan/worker/organisms/project_execution.py": 1,
    }
    found: dict[str, int] = {}

    for path in sorted((SRC_ROOT / "worker" / "organisms").glob("*.py")):
        if path.name.endswith("_conversation.py") or path.name == "__init__.py":
            continue
        rel_path = _rel(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        count = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _name(node.func) == "WorkerDefinition"
        )
        if count:
            found[rel_path] = count

    assert found == allowed_direct_constructor_counts
