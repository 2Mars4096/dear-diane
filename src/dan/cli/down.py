"""dan-down — stop the background DAN server."""
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

from dan.cli.process_utils import is_process_alive

DAN_DIR = Path.home() / ".dan"
PID_FILE = DAN_DIR / "server.pid"


def main() -> None:
    if not PID_FILE.exists():
        print("No DAN server PID file found.")
        return

    try:
        lines = PID_FILE.read_text().strip().split("\n")
        pid = int(lines[0])
    except (ValueError, IndexError, OSError):
        print("Invalid PID file.", file=sys.stderr)
        PID_FILE.unlink(missing_ok=True)
        return

    if not is_process_alive(pid):
        print(f"Server process {pid} is not running (stale PID file).")
        PID_FILE.unlink(missing_ok=True)
        return

    print(f"Stopping DAN server (PID {pid})...")
    os.kill(pid, signal.SIGTERM)

    for _ in range(20):
        time.sleep(0.5)
        if not is_process_alive(pid):
            print("Server stopped.")
            PID_FILE.unlink(missing_ok=True)
            return

    print("Server did not stop within 10s. Sending SIGKILL...", file=sys.stderr)
    try:
        os.kill(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    PID_FILE.unlink(missing_ok=True)
    print("Server killed.")


if __name__ == "__main__":
    main()
