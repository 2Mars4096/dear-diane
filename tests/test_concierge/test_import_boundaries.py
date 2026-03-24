from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src" / "dan"
CONCIERGE_ROOT = SRC_ROOT / "server" / "concierge"
MODELS_ROOT = SRC_ROOT / "models"


def _iter_import_targets(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.module, node.lineno))
    return imports


def _iter_class_method_import_targets(
    path: Path,
    class_name: str,
    method_name: str,
) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                    imports: list[tuple[str, int]] = []
                    for inner in ast.walk(child):
                        if isinstance(inner, ast.Import):
                            for alias in inner.names:
                                imports.append((alias.name, inner.lineno))
                        elif isinstance(inner, ast.ImportFrom) and inner.module:
                            imports.append((inner.module, inner.lineno))
                    return imports
    raise AssertionError(f"Could not find {class_name}.{method_name} in {path}")


def _format_violations(violations: list[tuple[Path, int, str]]) -> str:
    lines = []
    for path, lineno, target in violations:
        rel = path.relative_to(PROJECT_ROOT)
        lines.append(f"{rel}:{lineno} -> {target}")
    return "\n".join(lines)


def test_concierge_modules_do_not_import_chat_manager_directly() -> None:
    violations: list[tuple[Path, int, str]] = []
    for path in sorted(CONCIERGE_ROOT.rglob("*.py")):
        for target, lineno in _iter_import_targets(path):
            if target == "dan.server.chat_manager":
                violations.append((path, lineno, target))

    assert not violations, _format_violations(violations)


def test_concierge_init_does_not_import_engine_learning_modules_directly() -> None:
    path = CONCIERGE_ROOT / "runtime" / "__init__.py"
    violations: list[tuple[Path, int, str]] = []

    for target, lineno in _iter_class_method_import_targets(path, "Concierge", "__init__"):
        if target.startswith("dan.engine"):
            violations.append((path, lineno, target))

    assert not violations, _format_violations(violations)


def test_concierge_runtime_helpers_do_not_import_raw_llm_resolution_primitives() -> None:
    forbidden = {"resolve_llm_provider", "resolve_model_gateway"}
    target_paths = [
        CONCIERGE_ROOT / "runtime" / "__init__.py",
        CONCIERGE_ROOT / "tier_executors.py",
    ]
    violations: list[tuple[Path, int, str]] = []

    for path in target_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "dan.llm_surface":
                for alias in node.names:
                    if alias.name in forbidden:
                        violations.append((path, node.lineno, alias.name))

    assert not violations, _format_violations(violations)


def test_concierge_runtime_helpers_do_not_import_provider_registry_or_server_gateway_directly() -> None:
    forbidden_prefixes = (
        "dan.providers.registry",
        "dan.providers.factory",
        "dan.server.llm_gateway",
    )
    target_paths = [
        CONCIERGE_ROOT / "runtime" / "__init__.py",
        CONCIERGE_ROOT / "tier_executors.py",
    ]
    violations: list[tuple[Path, int, str]] = []

    for path in target_paths:
        for target, lineno in _iter_import_targets(path):
            if target.startswith(forbidden_prefixes):
                violations.append((path, lineno, target))

    assert not violations, _format_violations(violations)


def test_concierge_tier_executors_do_not_import_llm_surface_directly() -> None:
    path = CONCIERGE_ROOT / "tier_executors.py"
    violations: list[tuple[Path, int, str]] = []

    for target, lineno in _iter_import_targets(path):
        if target == "dan.llm_surface":
            violations.append((path, lineno, target))

    assert not violations, _format_violations(violations)


def test_concierge_runtime_has_no_direct_engine_imports() -> None:
    path = CONCIERGE_ROOT / "runtime" / "__init__.py"
    violations: list[tuple[Path, int, str]] = []

    for target, lineno in _iter_import_targets(path):
        if target.startswith("dan.engine"):
            violations.append((path, lineno, target))

    assert not violations, _format_violations(violations)


def test_domain_preferences_has_no_direct_engine_imports() -> None:
    path = CONCIERGE_ROOT / "domain_preferences.py"
    violations: list[tuple[Path, int, str]] = []

    for target, lineno in _iter_import_targets(path):
        if target.startswith("dan.engine"):
            violations.append((path, lineno, target))

    assert not violations, _format_violations(violations)


def test_domain_learning_does_not_import_private_memory_kernel_overlap() -> None:
    path = CONCIERGE_ROOT / "domain_learning.py"
    violations: list[tuple[Path, int, str]] = []

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "dan.engine.memory_kernel":
            for alias in node.names:
                if alias.name == "_keyword_overlap":
                    violations.append((path, node.lineno, alias.name))

    assert not violations, _format_violations(violations)


def test_domain_learning_does_not_import_learning_tiers_or_adaptation_registry() -> None:
    path = CONCIERGE_ROOT / "domain_learning.py"
    forbidden_modules = {
        "dan.engine.adaptation_registry",
        "dan.engine.learning_tiers",
    }
    violations: list[tuple[Path, int, str]] = []

    for target, lineno in _iter_import_targets(path):
        if target in forbidden_modules:
            violations.append((path, lineno, target))

    assert not violations, _format_violations(violations)


def test_models_do_not_import_engine_server_or_meta_layers() -> None:
    forbidden_prefixes = ("dan.engine", "dan.server", "dan.meta")
    violations: list[tuple[Path, int, str]] = []
    for path in sorted(MODELS_ROOT.rglob("*.py")):
        for target, lineno in _iter_import_targets(path):
            if target.startswith(forbidden_prefixes):
                violations.append((path, lineno, target))

    assert not violations, _format_violations(violations)
