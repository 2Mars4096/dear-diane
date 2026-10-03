"""diane.cli — command-line interface for Diane workflows."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from diane.workspace_roots import normalize_workspace_root

DAN_DIR = Path.home() / ".dan"
RUNS_DIR = DAN_DIR / "runs"


def _safe_current_directory() -> str:
    """Return a best-effort current directory without requiring getcwd()."""

    pwd = str(os.environ.get("PWD") or "").strip()
    if pwd:
        return pwd
    try:
        return str(Path.cwd())
    except FileNotFoundError:
        return "."


def load_env() -> None:
    """Load .env file if present (same pattern as diane.server.app)."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def resolve_config(
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Resolve configuration from CLI flags -> .env -> environment -> defaults.

    Priority: explicit arg > DAN_* env var > OPENAI_* env var > default.
    """
    return {
        "api_key": (
            api_key
            or os.environ.get("DAN_LLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY", "")
        ),
        "model": model or os.environ.get("DAN_MODEL", ""),
        "base_url": (
            base_url
            or os.environ.get("DAN_LLM_BASE_URL")
            or os.environ.get("DAN_BASE_URL", "")
        ),
        "workspace": workspace or os.environ.get("DAN_WORKSPACE_ROOT") or _safe_current_directory(),
    }


def ensure_dan_dir() -> Path:
    """Create ~/.dan/runs/ if it doesn't exist."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return RUNS_DIR


def _not_implemented(command: str) -> None:
    print(f"dan-{command}: not yet implemented (Phase 12 — 21-2+)", file=sys.stderr)
    sys.exit(1)


def _try_import_rich():
    """Return (Console, module) or (None, None) if rich not installed."""
    try:
        from rich.console import Console
        import rich
        return Console, rich
    except ImportError:
        return None, None
