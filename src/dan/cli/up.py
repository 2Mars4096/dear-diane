"""dan-up — start DAN server if needed and drop into dan-chat."""
from __future__ import annotations

import argparse
import json
import os
import signal
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
TELEGRAM_DIR = DAN_DIR / "telegram"
TELEGRAM_CONFIG_FILE = TELEGRAM_DIR / "config.json"
TELEGRAM_FLEET_PID_FILE = TELEGRAM_DIR / "fleet.pid"
TELEGRAM_FLEET_LOG_FILE = LOGS_DIR / "telegram-fleet.log"
DEFAULT_PHONE_HOST = "10.77.77.2"


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


def _server_url(host: str, port: int) -> str:
    resolved = _connect_host_for_bind_host(host)
    if ":" in resolved and not resolved.startswith("["):
        resolved = f"[{resolved}]"
    return f"http://{resolved}:{port}"


def _connect_host_for_bind_host(host: str) -> str:
    text = str(host or "").strip()
    if text in {"", "0.0.0.0", "::"}:
        return "127.0.0.1"
    return text


def _phone_host() -> str:
    return (
        os.environ.get("DAN_UP_PHONE_HOST")
        or os.environ.get("DAN_PHONE_WIREGUARD_HOST")
        or DEFAULT_PHONE_HOST
    ).strip() or DEFAULT_PHONE_HOST


def _host_has_interface_address(target: str) -> bool:
    target = str(target or "").strip()
    if not target:
        return False
    commands = (["ifconfig"], ["ip", "addr", "show"])
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except Exception:
            continue
        output = f"{completed.stdout}\n{completed.stderr}"
        if target in output:
            return True
    return False


def resolve_bind_host(value: str | None = None) -> tuple[str, str]:
    requested = str(value or os.environ.get("DAN_UP_HOST") or "auto").strip() or "auto"
    normalized = requested.lower()
    phone_host = _phone_host()
    if normalized in {"phone", "wireguard", "wg"}:
        return phone_host, "phone"
    if normalized == "auto":
        if _host_has_interface_address(phone_host):
            return "0.0.0.0", "desktop-phone-auto"
        return "127.0.0.1", "local-auto"
    return requested, "explicit"


def get_health_payload(
    port: int,
    timeout: float = 2.0,
    *,
    host: str = "127.0.0.1",
) -> dict[str, Any] | None:
    """Return the health payload if the server responds with ``status=ok``."""
    try:
        import httpx
        resp = httpx.get(f"{_server_url(host, port)}/health", timeout=timeout)
        if resp.status_code != 200:
            return None
        payload = resp.json()
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            return None
        return payload
    except Exception:
        return None


def check_health(port: int, timeout: float = 2.0, *, host: str = "127.0.0.1") -> bool:
    """Check if the server health endpoint responds."""
    return get_health_payload(port, timeout=timeout, host=host) is not None


def find_running_server(port: int, *, host: str = "127.0.0.1") -> tuple[int | None, int, bool] | None:
    """Find a healthy DAN server to reuse.

    Returns ``(pid, port, managed_by_pid_file)`` when one is available.
    """
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
    return (healthy_pid if isinstance(healthy_pid, int) else None), port, False


def start_server(
    port: int = 8000,
    *,
    host: str = "127.0.0.1",
    disable_adapter_autostart: bool = False,
    disable_telegram_adapter_autostart: bool = False,
) -> int:
    """Start ``dan-serve`` in the background, return the child PID."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / "server.log"
    log_file = open(log_path, "a")  # noqa: SIM115 — kept open for subprocess
    env = os.environ.copy()
    if disable_adapter_autostart:
        env["DAN_DISABLE_ADAPTER_AUTOSTART"] = "1"
    if disable_telegram_adapter_autostart:
        env["DAN_DISABLE_TELEGRAM_ADAPTER_AUTOSTART"] = "1"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "dan.server",
            "--host", host,
            "--port", str(port),
            "--no-reload",
        ],
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
        env=env,
    )
    log_file.close()
    return proc.pid


def _telegram_config_path(config_path: str | None = None) -> Path:
    if config_path:
        return Path(config_path).expanduser()
    env_path = os.environ.get("DAN_TELEGRAM_CONFIG", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return TELEGRAM_CONFIG_FILE


def _telegram_fleet_configured(config_path: str | None = None) -> bool:
    path = _telegram_config_path(config_path)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    bots = payload.get("bots") if isinstance(payload, dict) else None
    if not isinstance(bots, dict):
        return False
    return any(
        isinstance(bot, dict) and str(bot.get("token") or "").strip()
        for bot in bots.values()
    )


def _telegram_fleet_running() -> bool:
    if not TELEGRAM_FLEET_PID_FILE.exists():
        return False
    try:
        pid = int(TELEGRAM_FLEET_PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if is_process_alive(pid):
        return True
    try:
        TELEGRAM_FLEET_PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    return False


def _stop_telegram_fleet_daemon(*, timeout: float = 5.0) -> bool:
    if not TELEGRAM_FLEET_PID_FILE.exists():
        return False
    try:
        pid = int(TELEGRAM_FLEET_PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        try:
            TELEGRAM_FLEET_PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    if not is_process_alive(pid):
        try:
            TELEGRAM_FLEET_PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        try:
            TELEGRAM_FLEET_PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    except OSError:
        return False

    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        if not is_process_alive(pid):
            try:
                TELEGRAM_FLEET_PID_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            return True
        time.sleep(0.1)
    return False


def _stop_backend_telegram_adapters(server_url: str) -> int:
    try:
        import httpx

        resp = httpx.get(f"{server_url}/api/adapters/status", timeout=2.0)
        if resp.status_code != 200:
            return 0
        adapters = resp.json()
        if not isinstance(adapters, list):
            return 0
        stopped = 0
        for adapter in adapters:
            if not isinstance(adapter, dict):
                continue
            if str(adapter.get("type") or "").strip().lower() != "telegram":
                continue
            adapter_id = str(adapter.get("adapter_id") or "").strip()
            if not adapter_id:
                continue
            stop_resp = httpx.post(
                f"{server_url}/api/adapters/stop",
                json={"adapter_id": adapter_id},
                timeout=3.0,
            )
            if stop_resp.status_code in {200, 404}:
                stopped += 1
        return stopped
    except Exception:
        return 0


def ensure_telegram_fleet(
    server_url: str,
    *,
    mode: str = "auto",
    config_path: str | None = None,
) -> None:
    """Start the Telegram fleet daemon for ``dan-up`` when configured."""

    normalized = str(mode or "auto").strip().lower()
    if normalized in {"0", "false", "no", "off", "none"}:
        return
    configured = _telegram_fleet_configured(config_path)
    if normalized == "auto" and not configured:
        return
    if normalized == "fleet" and not configured:
        print("Telegram fleet is not configured; run `dan-bot create <name>` first.")
        return
    if _telegram_fleet_running():
        try:
            pid = int(TELEGRAM_FLEET_PID_FILE.read_text(encoding="utf-8").strip())
            print(f"Telegram fleet already running (PID {pid}).")
        except (OSError, ValueError):
            print("Telegram fleet already running.")
        return

    stopped = _stop_backend_telegram_adapters(server_url)
    if stopped:
        print(f"Stopped {stopped} backend-owned Telegram adapter before starting the fleet.")

    TELEGRAM_DIR.mkdir(parents=True, exist_ok=True)
    TELEGRAM_FLEET_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "dan.cli.bot"]
    if config_path:
        cmd.extend(["--config", config_path])
    cmd.extend(["start-all", "--server", server_url])
    env = os.environ.copy()
    env["DAN_BOT_DAEMON_CHILD"] = "1"
    with TELEGRAM_FLEET_LOG_FILE.open("a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
    TELEGRAM_FLEET_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    print(f"Telegram fleet started (PID {proc.pid}).")
    print(f"Telegram logs: {TELEGRAM_FLEET_LOG_FILE}")


def ensure_telegram_adapter(server_url: str) -> None:
    """Ensure the backend-owned single Telegram adapter is running."""

    try:
        import httpx

        if TELEGRAM_FLEET_PID_FILE.exists() and _stop_telegram_fleet_daemon():
            print("Stopped Telegram fleet before starting the backend Telegram adapter.")
        elif _telegram_fleet_running():
            print(
                "Telegram fleet is still running; not starting the backend "
                "Telegram adapter because Telegram allows only one poller per bot token. "
                "Run `dan-up --telegram fleet` or stop the fleet first."
            )
            return

        status_resp = httpx.get(f"{server_url}/api/adapters/status", timeout=2.0)
        if status_resp.status_code == 200:
            adapters = status_resp.json()
            if isinstance(adapters, list):
                for adapter in adapters:
                    if not isinstance(adapter, dict):
                        continue
                    if str(adapter.get("type") or "").strip().lower() != "telegram":
                        continue
                    if bool(adapter.get("running")):
                        print("Telegram adapter already running.")
                        return

        start_resp = httpx.post(
            f"{server_url}/api/adapters/start",
            json={"type": "telegram", "config": {}},
            timeout=5.0,
        )
        if start_resp.status_code == 200:
            payload = start_resp.json()
            print(f"Telegram adapter started (id={payload.get('adapter_id')}).")
            return
        if start_resp.status_code == 409:
            print("Telegram adapter already running.")
            return
        print(f"Telegram adapter did not start: HTTP {start_resp.status_code} {start_resp.text[:200]}")
    except Exception as exc:
        print(f"Telegram adapter did not start: {exc}")


def wait_for_health(
    port: int,
    *,
    host: str = "127.0.0.1",
    max_wait: float = 60.0,
    poll_interval: float = 0.5,
) -> bool:
    """Poll ``/health`` until the server is ready or the deadline expires."""
    deadline = time.monotonic() + max_wait
    while True:
        if check_health(port, host=host):
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
    parser.add_argument(
        "--host",
        default=None,
        help=(
            "Server bind host. Defaults to auto: 127.0.0.1 locally, or "
            "0.0.0.0 when the phone WireGuard host exists so desktop and phone "
            "can share one server. Use 'phone' to bind only to "
            "DAN_PHONE_WIREGUARD_HOST."
        ),
    )
    parser.add_argument(
        "--telegram",
        choices=("adapter", "auto", "fleet", "off"),
        default=os.environ.get("DAN_UP_TELEGRAM", "adapter"),
        help=(
            "Start Telegram with dan-up: adapter starts the backend-owned single "
            "Telegram bot, fleet starts the multi-bot fleet, off skips Telegram "
            "(default: adapter)."
        ),
    )
    parser.add_argument(
        "--telegram-config",
        default=None,
        help="Telegram fleet config path (default: ~/.dan/telegram/config.json)",
    )
    args = parser.parse_args()
    port: int = args.port
    host, host_source = resolve_bind_host(args.host)
    connect_url = _server_url(host, port)
    telegram_mode = str(args.telegram or "auto").strip().lower()
    if telegram_mode == "auto":
        telegram_mode = "adapter"
    telegram_fleet_configured = (
        telegram_mode == "fleet"
        and _telegram_fleet_configured(args.telegram_config)
    )
    lock_handle = acquire_start_lock()
    if lock_handle is None:
        print("dan-up is already running in another terminal.", file=sys.stderr)
        sys.exit(1)

    server_url: str | None = None
    try:
        running_server = find_running_server(port, host=host)
        if running_server is not None:
            running_pid, resolved_port, managed = running_server
            connect_url = _server_url(host, resolved_port)
            if managed and running_pid is not None:
                print(f"DAN server already running (PID {running_pid}, port {resolved_port})")
            elif running_pid is not None:
                print(
                    f"DAN server already running on port {resolved_port} "
                    f"(PID {running_pid}, reusing existing server)"
                )
            else:
                print(f"DAN server already running on port {resolved_port} (reusing existing server)")
            server_url = connect_url
        else:
            print(f"Starting DAN server on {host}:{port} ({host_source})...")
            new_pid = start_server(
                port,
                host=host,
                disable_adapter_autostart=telegram_fleet_configured,
                disable_telegram_adapter_autostart=telegram_mode == "adapter",
            )
            write_pid_file(new_pid, port)

            if wait_for_health(port, host=host):
                print(f"Server ready (PID {new_pid})")
                server_url = connect_url
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
        if telegram_mode == "fleet":
            ensure_telegram_fleet(
                server_url,
                mode="fleet",
                config_path=args.telegram_config,
            )
        elif telegram_mode != "off":
            ensure_telegram_adapter(server_url)
        drop_into_chat(server_url)


if __name__ == "__main__":
    main()
