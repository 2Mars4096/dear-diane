from __future__ import annotations

import json
import subprocess
from pathlib import Path

from dan.models.graph import Graph
from dan.validation.graph import validate_graph


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _tracked_graph_files() -> list[Path]:
    repo = _repo_root()
    output = subprocess.check_output(
        ["git", "ls-files", "graphs/*.json"],
        cwd=repo,
        text=True,
    )
    return [repo / line for line in output.splitlines() if line]


def _split_validation_messages(messages: list[str]) -> tuple[list[str], list[str]]:
    fatal: list[str] = []
    warnings: list[str] = []
    for msg in messages:
        lower = msg.lower()
        if any(token in lower for token in ("warning", "deprecated", "untyped")):
            warnings.append(msg)
        else:
            fatal.append(msg)
    return fatal, warnings


def test_tracked_graph_corpus_parses_and_model_validates() -> None:
    for path in _tracked_graph_files():
        data = json.loads(path.read_text(encoding="utf-8"))
        graph = Graph.model_validate(data)
        assert graph.version == "dan_graph_v1", path.relative_to(_repo_root()).as_posix()


def test_tracked_graph_corpus_has_no_fatal_validation_errors() -> None:
    fatals_by_file: dict[str, list[str]] = {}
    for path in _tracked_graph_files():
        rel = path.relative_to(_repo_root()).as_posix()
        data = json.loads(path.read_text(encoding="utf-8"))
        graph = Graph.model_validate(data)
        fatal, _warnings = _split_validation_messages(validate_graph(graph))
        if fatal:
            fatals_by_file[rel] = fatal

    assert fatals_by_file == {}
