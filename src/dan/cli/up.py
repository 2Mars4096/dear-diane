"""Start the minimal Diane Work/Notes + Dear Diane server."""

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
DEFAULT_PHONE_HOST = "10.77.77.2"


def read_pid_file() -> tuple[int | None, int | None]:
    if not PID_FILE.exists():
        return None, None
    try:
        lines = PID_FILE.read_text(encoding="utf-8").splitlines()
        return int(lines[0]), int(lines[1]) if len(lines) > 1 else 8000
    except (ValueError, IndexError, OSError):
        return None, None


def write_pid_file(pid: int, port: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(f"{pid}\n{port}\n", encoding="utf-8")


def remove_pid_file() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def acquire_start_lock() -> IO[str] | None:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_FILE.open("a+", encoding="utf-8")
    try:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        return handle
    except OSError:
        handle.close()
        return None
    return handle


def release_start_lock(handle: IO[str] | None) -> None:
    if handle is None:
        return
    try:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass
    handle.close()


def _phone_host() -> str:
    return (
        os.environ.get("DAN_UP_PHONE_HOST")
        or os.environ.get("DAN_PHONE_WIREGUARD_HOST")
        or DEFAULT_PHONE_HOST
    ).strip()


def _host_has_interface_address(target: str) -> bool:
    for command in (["ifconfig"], ["ip", "addr", "show"]):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=2, check=False)
        except Exception:
            continue
        if target and target in f"{result.stdout}\n{result.stderr}":
            return True
    return False


def resolve_bind_host(value: str | None = None) -> tuple[str, str]:
    requested = str(value or os.environ.get("DAN_UP_HOST") or "auto").strip()
    phone_host = _phone_host()
    if requested.lower() in {"phone", "wireguard", "wg"}:
        return phone_host, "phone"
    if requested.lower() == "auto":
        if _host_has_interface_address(phone_host):
            return "0.0.0.0", "desktop-phone-auto"
        return "127.0.0.1", "local-auto"
    return requested, "explicit"


def _connect_host(host: str) -> str:
    return "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host


def _server_url(host: str, port: int) -> str:
    connect_host = _connect_host(host)
    if ":" in connect_host and not connect_host.startswith("["):
        connect_host = f"[{connect_host}]"
    return f"http://{connect_host}:{port}"


def get_health_payload(port: int, timeout: float = 2.0, *, host: str = "127.0.0.1") -> dict[str, Any] | None:
    try:
        import httpx
        response = httpx.get(f"{_server_url(host, port)}/health", timeout=timeout)
        payload = response.json() if response.status_code == 200 else None
        return payload if isinstance(payload, dict) and payload.get("status") == "ok" else None
    except Exception:
        return None


def check_health(port: int, timeout: float = 2.0, *, host: str = "127.0.0.1") -> bool:
    return get_health_payload(port, timeout=timeout, host=host) is not None


def find_running_server(port: int, *, host: str = "127.0.0.1") -> tuple[int | None, int, bool] | None:
    pid, existing_port = read_pid_file()
    if pid:
        resolved_port = existing_port or port
        if is_process_alive(pid) and check_health(resolved_port, host=host):
            return pid, resolved_port, True
        remove_pid_file()
    payload = get_health_payload(port, host=host)
    if payload is None:
        return None
    healthy_pid = payload.get("pid")
    return healthy_pid if isinstance(healthy_pid, int) else None, port, False


def start_server(port: int = 8000, *, host: str = "127.0.0.1") -> int:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / "server.log"
    with log_path.open("a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-m", "dan.server", "--host", host, "--port", str(port), "--no-reload"],
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            env=os.environ.copy(),
        )
    return process.pid


def wait_for_health(port: int, *, host: str = "127.0.0.1", max_wait: float = 60.0) -> bool:
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        if check_health(port, host=host):
            return True
        time.sleep(0.5)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the Diane Work/Notes and Dear Diane server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default=None)
    args = parser.parse_args()
    host, host_source = resolve_bind_host(args.host)
    lock = acquire_start_lock()
    if lock is None:
        raise SystemExit("dan-up is already running in another terminal")
    try:
        running = find_running_server(args.port, host=host)
        if running is not None:
            pid, port, _managed = running
            print(f"Dear Diane server already running on {_server_url(host, port)}" + (f" (PID {pid})" if pid else ""))
            return
        print(f"Starting Dear Diane server on {host}:{args.port} ({host_source})...")
        pid = start_server(args.port, host=host)
        write_pid_file(pid, args.port)
        if not wait_for_health(args.port, host=host):
            if not is_process_alive(pid):
                remove_pid_file()
            raise SystemExit(f"Server did not become healthy; see {LOGS_DIR / 'server.log'}")
        print(f"Dear Diane server ready at {_server_url(host, args.port)} (PID {pid})")
    finally:
        release_start_lock(lock)


if __name__ == "__main__":
    main()
