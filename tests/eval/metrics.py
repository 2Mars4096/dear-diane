"""JSONL logging for evaluation records.

Part of the workflow generation quality evaluation system (Phase 33).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tests.eval import EvalRecord, RESULTS_DIR


class EvalLogger:
    """Appends EvalRecords to a timestamped JSONL file in results/."""

    def __init__(
        self,
        output_dir: Path | None = None,
        run_tag: str | None = None,
        store_graphs: bool = True,
    ):
        self._output_dir = output_dir or RESULTS_DIR
        self._output_dir.mkdir(parents=True, exist_ok=True)
        tag = run_tag or "run"
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self._path = self._output_dir / f"{stamp}_{tag}.jsonl"
        self._store_graphs = store_graphs
        self._graphs_dir: Path | None = None

    def log(self, record: EvalRecord) -> None:
        """Append one record as a JSON line."""
        with open(self._path, "a") as f:
            f.write(record.model_dump_json() + "\n")

    def log_graph(self, record_id: str, lane: str, graph_dict: dict) -> None:
        """33-2 task 5-3: Store generated graph JSON alongside results."""
        if not self._store_graphs:
            return
        if self._graphs_dir is None:
            self._graphs_dir = self._path.parent / f"{self._path.stem}_graphs"
            self._graphs_dir.mkdir(parents=True, exist_ok=True)
        safe_id = record_id.replace("/", "_").replace(" ", "_")
        out = self._graphs_dir / f"{safe_id}_{lane}.json"
        with open(out, "w") as f:
            json.dump(graph_dict, f, indent=2)

    @property
    def output_path(self) -> Path:
        return self._path

    @staticmethod
    def load_records(path: Path) -> list[EvalRecord]:
        """Read all EvalRecords from a JSONL file."""
        records: list[EvalRecord] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(EvalRecord(**json.loads(line)))
        return records
