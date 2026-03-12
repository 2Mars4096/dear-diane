"""CLI entry point: ``python -m dan.server`` or ``dan-serve``."""

import argparse
import copy
import os
from pathlib import Path
from typing import Any

import uvicorn


def build_uvicorn_log_config(level: str | None = None) -> dict[str, Any]:
    """Configure uvicorn so DAN app logs are emitted at INFO by default."""
    resolved_level = (level or os.environ.get("DAN_LOG_LEVEL", "INFO")).upper()
    config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    config["disable_existing_loggers"] = False
    config.setdefault("root", {})
    config["root"]["level"] = resolved_level
    config.setdefault("loggers", {})
    config["loggers"]["dan"] = {
        "handlers": ["default"],
        "level": resolved_level,
        "propagate": False,
    }
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description="DAN visual editor server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--reload",
        action="store_true",
        default=True,
        help="Auto-reload on Python/workflow changes (default: on)",
    )
    parser.add_argument("--no-reload", action="store_true", help="Disable auto-reload (for production)")
    args = parser.parse_args()
    reload = args.reload and not args.no_reload
    project_root = Path(__file__).resolve().parents[3]
    reload_dirs = [str(project_root)] if reload else None
    reload_excludes = [
        str(project_root / "graphs"),
        str(project_root / "runs"),
        str(project_root / "checkpoints"),
    ] if reload else None
    uvicorn.run(
        "dan.server.app:app",
        host=args.host,
        port=args.port,
        reload=reload,
        reload_dirs=reload_dirs,
        reload_excludes=reload_excludes,
        log_config=build_uvicorn_log_config(),
    )


if __name__ == "__main__":
    main()
