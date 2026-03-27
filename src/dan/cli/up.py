"""dan-up — start DAN server if needed and drop into dan-chat."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, IO

from dan.cli.process_utils import is_process_alive

DAN_DIR = Path.home() / ".dan"
PID_FILE = DAN_DIR / "server.pid"
LOCK_FILE = DAN_DIR / "server.lock"
LOGS_DIR = DAN_DIR / "logs"


def read_pid_file() -> tuple[int | None, int | None]:
    """Read PID and port from server.pid.  Returns ``(pid, port)`` or ``(None, None)``."""
    if not PID_FILE.exists():
        return None, None
    try:
        lines = PID_FILE.read_text().strip().split("\n")
        pid = int(lines[0])
        port = int(lines[1]) if len(lines) > 1 else 8000
        return pid, port
    except (ValueError, IndexError, OSError):
        return None, None


def write_pid_file(pid: int, port: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(f"{pid}\n{port}\n")


def remove_pid_file() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def acquire_start_lock() -> IO[str] | None:
    """Acquire a non-blocking process lock for ``dan up`` startup."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_FILE.open("a+", encoding="utf-8")
    try:
        import fcntl
    except ImportError:
        return handle
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def release_start_lock(handle: IO[str] | None) -> None:
    """Release startup lock handle."""
    if handle is None:
        return
    try:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass
    try:
        handle.close()
    except OSError:
        pass


def get_health_payload(port: int, timeout: float = 2.0) -> dict[str, Any] | None:
    """Return the health payload if the server responds with ``status=ok``."""
    try:
        import httpx
        resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=timeout)
        if resp.status_code != 200:
            return None
        payload = resp.json()
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            return None
        return payload
    except Exception:
        return None


def check_health(port: int, timeout: float = 2.0) -> bool:
    """Check if the server health endpoint responds."""
    return get_health_payload(port, timeout=timeout) is not None


def find_running_server(port: int) -> tuple[int | None, int, bool] | None:
    """Find a healthy DAN server to reuse.

    Returns ``(pid, port, managed_by_pid_file)`` when one is available.
    """
    pid, existing_port = read_pid_file()
    if pid:
        resolved_port = existing_port or port
        if is_process_alive(pid) and check_health(resolved_port):
            return pid, resolved_port, True
        remove_pid_file()

    payload = get_health_payload(port)
    if payload is None:
        return None

    healthy_pid = payload.get("pid")
    return (healthy_pid if isinstance(healthy_pid, int) else None), port, False


def start_server(port: int = 8000) -> int:
    """Start ``dan-serve`` in the background, return the child PID."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / "server.log"
    log_file = open(log_path, "a")  # noqa: SIM115 — kept open for subprocess
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "dan.server",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--no-reload",
        ],
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
    )
    log_file.close()
    return proc.pid


def wait_for_health(port: int, max_wait: float = 60.0, poll_interval: float = 0.5) -> bool:
    """Poll ``/health`` until the server is ready or the deadline expires."""
    deadline = time.monotonic() + max_wait
    while True:
        if check_health(port):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll_interval, remaining))


def drop_into_chat(server_url: str) -> None:
    """``exec`` into ``dan-chat`` connected to *server_url*."""
    os.execvp(
        sys.executable,
        [sys.executable, "-m", "dan.cli.chat", "--server", server_url],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dan-up",
        description="Start DAN server (if needed) and drop into dan-chat.",
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Server port (default: 8000)"
    )
    args = parser.parse_args()
    port: int = args.port
    lock_handle = acquire_start_lock()
    if lock_handle is None:
        print("dan-up is already running in another terminal.", file=sys.stderr)
        sys.exit(1)

    server_url: str | None = None
    try:
        running_server = find_running_server(port)
        if running_server is not None:
            running_pid, resolved_port, managed = running_server
            if managed and running_pid is not None:
                print(f"DAN server already running (PID {running_pid}, port {resolved_port})")
            elif running_pid is not None:
                print(
                    f"DAN server already running on port {resolved_port} "
                    f"(PID {running_pid}, reusing existing server)"
                )
            else:
                print(f"DAN server already running on port {resolved_port} (reusing existing server)")
            server_url = f"http://127.0.0.1:{resolved_port}"
        else:
            print(f"Starting DAN server on port {port}...")
            new_pid = start_server(port)
            write_pid_file(new_pid, port)

            if wait_for_health(port):
                print(f"Server ready (PID {new_pid})")
                server_url = f"http://127.0.0.1:{port}"
            else:
                if is_process_alive(new_pid):
                    print(
                        f"Server still starting (PID {new_pid}). "
                        "Run dan-up again in a moment.",
                        file=sys.stderr,
                    )
                    print(f"Logs: {LOGS_DIR / 'server.log'}", file=sys.stderr)
                else:
                    print("Server process exited before becoming healthy.", file=sys.stderr)
                    print(f"Check logs: {LOGS_DIR / 'server.log'}", file=sys.stderr)
                    remove_pid_file()
                sys.exit(1)
    finally:
        release_start_lock(lock_handle)

    if server_url:
        drop_into_chat(server_url)


if __name__ == "__main__":
    main()
