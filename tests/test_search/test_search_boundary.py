from __future__ import annotations

import ast
from pathlib import Path

from dan.server.search_models import (
    SEARCH_RESULT_ADAPTER_METADATA_FIELDS,
    SEARCH_RESULT_HARD_COMPATIBILITY_FIELDS,
    SEARCH_RESULT_SET_ADAPTER_METADATA_FIELDS,
    SEARCH_RESULT_SET_HARD_COMPATIBILITY_FIELDS,
    SEARCH_RESULT_SET_CONTRACT_VERSION,
    search_result_contract_manifest,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "dan"
_ALLOWED_WEB_SEARCH_IMPORTS = {
    Path("search/broker.py"),
    Path("tools/web_search.py"),
}


def _direct_web_search_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "dan.tools.web_search":
                    hits.append(alias.name)
        if isinstance(node, ast.ImportFrom):
            if node.module == "dan.tools.web_search":
                hits.append(node.module)
            if node.module == "dan.tools" and any(alias.name == "web_search" for alias in node.names):
                hits.append("dan.tools.web_search")
    return hits


def test_search_contract_manifest_matches_expected_membrane() -> None:
    manifest = search_result_contract_manifest()

    assert manifest["search_result_set"]["version"] == SEARCH_RESULT_SET_CONTRACT_VERSION
    assert manifest["search_result"]["hard_compatibility_fields"] == list(
        SEARCH_RESULT_HARD_COMPATIBILITY_FIELDS
    )
    assert manifest["search_result"]["adapter_metadata_fields"] == list(
        SEARCH_RESULT_ADAPTER_METADATA_FIELDS
    )
    assert manifest["search_result_set"]["hard_compatibility_fields"] == list(
        SEARCH_RESULT_SET_HARD_COMPATIBILITY_FIELDS
    )
    assert manifest["search_result_set"]["adapter_metadata_fields"] == list(
        SEARCH_RESULT_SET_ADAPTER_METADATA_FIELDS
    )


def test_direct_web_search_imports_stay_behind_beacon_broker_boundary() -> None:
    violations: list[str] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(_SRC_ROOT)
        if relative in _ALLOWED_WEB_SEARCH_IMPORTS:
            continue
        hits = _direct_web_search_imports(path)
        if hits:
            violations.append(str(relative))

    assert violations == []
