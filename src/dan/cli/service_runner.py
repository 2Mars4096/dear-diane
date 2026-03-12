"""Shared service runner for DAN server.

Handles log rotation and PID bookkeeping before launching uvicorn.
Used by dan-service start, launchd, and systemd.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the DAN server with log rotation and PID setup."""
    from dan.cli.service import _rotate_logs, LOGS_DIR, PID_FILE
    from dan.server.__main__ import build_uvicorn_log_config

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _rotate_logs()

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(f"{os.getpid()}\n{port}\n")

    try:
        import uvicorn

        uvicorn.run(
            "dan.server.app:app",
            host=host,
            port=port,
            reload=False,
            log_config=build_uvicorn_log_config(),
        )
    finally:
        try:
            PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(prog="dan-service-runner")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)
