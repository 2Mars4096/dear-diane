"""Shared filesystem paths for the DAN server (graphs, chats, runs)."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_graphs_dir() -> str:
    """Resolve the graph/chat persistence directory.

    Precedence:

    1. ``DAN_GRAPHS_DIR`` environment variable (after ``load_dotenv()`` in callers).
    2. First line of ``~/.dan/graphs_dir`` if that file exists (absolute path to your
       repo ``graphs`` folder). Keeps **terminal** ``dan-up`` / **DAN Desktop** on the
       same data without rebuilding the app.
    3. ``./graphs`` (relative to process working directory).
    """
    d = os.environ.get("DAN_GRAPHS_DIR", "").strip()
    if d:
        return str(Path(d).expanduser())
    marker = Path.home() / ".dan" / "graphs_dir"
    if marker.is_file():
        try:
            line = marker.read_text(encoding="utf-8").strip().split("\n")[0].strip()
            if line and not line.startswith("#"):
                return str(Path(line).expanduser())
        except OSError:
            pass
    return "./graphs"
