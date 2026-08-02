"""AST-walking import-direction boundary tests.

Walks all Python files under ``src/dan/`` and checks that import statements
respect the desired module dependency directions.  Violations that exist in the
current codebase are recorded in a frozen allowlist so the test passes today
but will **fail** if any *new* violation is introduced.

Follow-up: as violations are resolved during the 41-* refactor family, entries
should be removed from the allowlist.  The test prints a notice when a listed
violation no longer appears in the source so the allowlist can be trimmed.

Pattern inspired by ``tests/test_concierge/test_import_boundaries.py``.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src" / "dan"

# ---------------------------------------------------------------------------
# Boundary rules: module -> set of forbidden import prefixes
# ---------------------------------------------------------------------------
# Each key is a subdirectory of ``src/dan/``.  Its value lists the ``dan.``
# import prefixes that the module must NOT depend on (in any direction --
# top-level, lazy, or TYPE_CHECKING).

FORBIDDEN_IMPORTS: dict[str, list[str]] = {
    "providers": [
        "dan.server.",
        "dan.server.concierge.",
        "dan.engine.",
        "dan.executors.",
        "dan.meta.",
        "dan.cli.",
    ],
    "models": [
        "dan.server.",
        "dan.server.concierge.",
        "dan.engine.",
        "dan.executors.",
        "dan.meta.",
        "dan.cli.",
        "dan.providers.",
    ],
    "builder": [
        "dan.server.",
        "dan.server.concierge.",
        "dan.cli.",
    ],
    "loader": [
        "dan.server.",
        "dan.server.concierge.",
        "dan.cli.",
    ],
    "engine": [
        "dan.server.",
        "dan.server.concierge.",
        "dan.cli.",
    ],
    "executors": [
        "dan.server.",
        "dan.server.concierge.",
        "dan.cli.",
    ],
    "meta": [
        "dan.server.",
        "dan.cli.",
    ],
    "agent_runtime": [
        "dan.server.",
        "dan.server.concierge.",
        "dan.engine.",
        "dan.executors.",
        "dan.meta.",
        "dan.cli.",
    ],
    "workflow_runtime": [
        "dan.server.",
        "dan.server.concierge.",
        "dan.cli.",
    ],
}

# ---------------------------------------------------------------------------
# Known violations (frozen allowlist)
# ---------------------------------------------------------------------------
# Tuples of (source_file_relative_to_src_dan, imported_module).
# Relative paths use forward slashes for cross-platform consistency.

KNOWN_VIOLATIONS: set[tuple[str, str]] = set()


def _iter_import_targets(path: Path) -> list[tuple[str, int]]:
    """Parse *path* and yield ``(dotted_module, lineno)`` for every import."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.module, node.lineno))
    return imports


def _collect_violations() -> list[tuple[str, str, str, int]]:
    """Return ``[(rel_path, module_name, imported_target, lineno), ...]``."""
    violations: list[tuple[str, str, str, int]] = []

    for module_name, forbidden_prefixes in FORBIDDEN_IMPORTS.items():
        module_dir = SRC_ROOT / module_name
        if not module_dir.is_dir():
            continue
        for py_file in sorted(module_dir.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            rel = py_file.relative_to(SRC_ROOT).as_posix()
            for target, lineno in _iter_import_targets(py_file):
                for prefix in forbidden_prefixes:
                    if target.startswith(prefix) or target == prefix.rstrip("."):
                        violations.append((rel, module_name, target, lineno))
                        break
    return violations


def test_no_new_import_boundary_violations() -> None:
    """Fail only when a *new* boundary violation appears.

    Known pre-existing violations are allowlisted in ``KNOWN_VIOLATIONS``.
    The test warns for each known violation that still exists (so progress is
    visible) and prints a notice for any allowlisted violation that has been
    resolved (so the set can be trimmed).
    """
    raw_violations = _collect_violations()

    found_pairs: set[tuple[str, str]] = set()
    new_violations: list[str] = []

    for rel_path, _module, target, lineno in raw_violations:
        pair = (rel_path, target)
        found_pairs.add(pair)
        if pair not in KNOWN_VIOLATIONS:
            new_violations.append(f"  NEW  {rel_path}:{lineno} -> {target}")

    still_present = found_pairs & KNOWN_VIOLATIONS
    resolved = KNOWN_VIOLATIONS - found_pairs

    if still_present:
        warnings.warn(
            f"{len(still_present)} known boundary violation(s) still present:\n"
            + "\n".join(f"  {src} -> {tgt}" for src, tgt in sorted(still_present)),
            stacklevel=1,
        )

    if resolved:
        for src, tgt in sorted(resolved):
            print(f"  RESOLVED (remove from allowlist): {src} -> {tgt}")

    assert not new_violations, (
        f"{len(new_violations)} NEW import-boundary violation(s) found:\n"
        + "\n".join(sorted(new_violations))
        + "\n\nIf these are intentional, add them to KNOWN_VIOLATIONS."
    )


# ---------------------------------------------------------------------------
# Bidirectional / circular boundary detection
# ---------------------------------------------------------------------------

def _module_key(dotted: str) -> str | None:
    """Map a ``dan.X.Y`` import target to its top-level module key.

    Returns e.g. ``"engine"`` for ``dan.engine.scheduler`` or
    ``"server.concierge"`` for ``dan.server.concierge.runtime``.
    Returns ``None`` for imports that don't start with ``dan.``.
    """
    if not dotted.startswith("dan."):
        return None
    parts = dotted.split(".")
    if len(parts) < 2:
        return None
    top = parts[1]
    if top == "server" and len(parts) >= 3 and parts[2] == "concierge":
        return "server.concierge"
    if top == "server":
        return "server"
    return top


_CIRCULAR_SCAN_MODULES: list[str] = [
    *FORBIDDEN_IMPORTS.keys(),
    "server",
    "server/concierge",
]


def _build_cross_module_edges() -> set[tuple[str, str]]:
    """Return ``{(source_module, target_module), ...}`` for all imports.

    Scans both boundary-protected modules and their typical counterparts
    (``server/``, ``server/concierge/``) so bidirectional edges are visible.
    """
    edges: set[tuple[str, str]] = set()
    for module_path in _CIRCULAR_SCAN_MODULES:
        module_dir = SRC_ROOT / Path(module_path)
        if not module_dir.is_dir():
            continue
        source_key = module_path.replace("/", ".")
        for py_file in sorted(module_dir.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            # For server/, skip concierge/ sub-tree to avoid double-counting
            if source_key == "server":
                try:
                    py_file.relative_to(SRC_ROOT / "server" / "concierge")
                    continue
                except ValueError:
                    pass
            for target, _lineno in _iter_import_targets(py_file):
                target_key = _module_key(target)
                if target_key and target_key != source_key:
                    edges.add((source_key, target_key))
    return edges


KNOWN_CIRCULAR_PAIRS: set[frozenset[str]] = {
    frozenset({"server", "server.concierge"}),
}


def test_no_circular_boundary_pairs() -> None:
    """Detect bidirectional imports across module boundaries.

    A→B *and* B→A across module boundaries signals tight coupling.
    Known pairs are allowlisted; new ones fail the test.
    """
    edges = _build_cross_module_edges()

    circular: set[frozenset[str]] = set()
    for a, b in edges:
        if (b, a) in edges:
            circular.add(frozenset({a, b}))

    new_circular = circular - KNOWN_CIRCULAR_PAIRS
    resolved_circular = KNOWN_CIRCULAR_PAIRS - circular

    if resolved_circular:
        for pair in sorted(resolved_circular, key=lambda s: tuple(sorted(s))):
            modules = " <-> ".join(sorted(pair))
            print(f"  RESOLVED circular pair (remove from allowlist): {modules}")

    if circular & KNOWN_CIRCULAR_PAIRS:
        present = circular & KNOWN_CIRCULAR_PAIRS
        warnings.warn(
            f"{len(present)} known circular boundary pair(s) still present:\n"
            + "\n".join(
                "  " + " <-> ".join(sorted(p))
                for p in sorted(present, key=lambda s: tuple(sorted(s)))
            ),
            stacklevel=1,
        )

    assert not new_circular, (
        f"{len(new_circular)} NEW circular boundary pair(s) found:\n"
        + "\n".join(
            "  " + " <-> ".join(sorted(p))
            for p in sorted(new_circular, key=lambda s: tuple(sorted(s)))
        )
        + "\n\nIf intentional, add to KNOWN_CIRCULAR_PAIRS."
    )


# ---------------------------------------------------------------------------
# God-module file-size watchpoints
# ---------------------------------------------------------------------------
# Baselines are the current line counts rounded up to the nearest 100, plus a
# 100-line buffer so normal feature work doesn't trip the test.  If a file
# exceeds its baseline, the refactor plan that should be extracting from it is
# noted in the failure message.

GOD_MODULE_BASELINES: dict[str, tuple[int, str]] = {
    "src/dan/server/chat_manager.py":              (4800, "41-2 (agent-runtime extraction)"),
    "src/dan/server/concierge/runtime/__init__.py": (3400, "41-3 (concierge-orchestrator extraction)"),
    "src/dan/engine/scheduler.py":                 (4200, "41-4 (workflow-runtime extraction)"),
    "src/dan/executors/control_flow.py":           (3000, "41-4 (workflow-runtime extraction)"),
    "src/dan/server/capability_handlers.py":       (1400, "41-2 (agent-runtime extraction)"),
    "src/dan/server/concierge/tier_executors.py":  (2200, "41-3 (concierge-orchestrator extraction)"),
    "src/dan/server/startup/__init__.py":         (1900, "41-5 (surface-adapter extraction)"),
}

GOD_MODULE_TOTAL_BASELINE = sum(limit for limit, _ in GOD_MODULE_BASELINES.values()) + 200


def _count_lines(rel_path: str) -> int:
    """Return the number of lines in *rel_path* relative to the project root."""
    return len((PROJECT_ROOT / rel_path).read_text(encoding="utf-8").splitlines())


def test_god_module_watchpoints() -> None:
    """Assert each god-module hotspot stays at or below its line-count baseline.

    Baselines are generous enough for normal feature work but will catch
    significant growth — a signal that code should be extracted per the
    41-* refactor plans instead of piling into the legacy file.
    """
    exceeded: list[str] = []
    report_lines: list[str] = []

    for rel_path, (baseline, plan) in sorted(GOD_MODULE_BASELINES.items()):
        actual = _count_lines(rel_path)
        status = "OK" if actual <= baseline else "EXCEEDED"
        report_lines.append(f"  {status:>8}  {actual:>5} / {baseline:>5}  {rel_path}")
        if actual > baseline:
            exceeded.append(
                f"  {rel_path}: {actual} lines (baseline {baseline}, +{actual - baseline} over)\n"
                f"    -> Extract from this file per plan {plan}"
            )

    summary = "\n".join(report_lines)
    assert not exceeded, (
        f"God-module watchpoint(s) exceeded baseline:\n\n"
        + "\n".join(exceeded)
        + f"\n\nFull report:\n{summary}"
    )


def test_god_module_total_lines() -> None:
    """Assert the combined size of all watchpoint files stays bounded.

    Even if no single file exceeds its individual baseline, distributed
    growth across all hotspots signals the refactor is stalling.
    """
    counts: dict[str, int] = {}
    for rel_path in sorted(GOD_MODULE_BASELINES):
        counts[rel_path] = _count_lines(rel_path)

    total = sum(counts.values())
    detail = "\n".join(f"  {c:>5}  {p}" for p, c in sorted(counts.items()))

    assert total <= GOD_MODULE_TOTAL_BASELINE, (
        f"Total god-module lines ({total}) exceeds baseline ({GOD_MODULE_TOTAL_BASELINE}).\n"
        f"Distributed growth detected — prioritise extraction per plans 41-1 through 41-5.\n\n"
        f"Per-file breakdown:\n{detail}"
    )
