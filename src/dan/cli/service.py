"""dan-service — OS-level service management for DAN server."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from dan.cli.process_utils import is_process_alive

DAN_DIR = Path.home() / ".dan"
PID_FILE = DAN_DIR / "server.pid"
LOGS_DIR = DAN_DIR / "logs"
DEFAULT_PORT = 8000
DEFAULT_HOST = "127.0.0.1"

# --- Plist template (macOS launchd) ---

LAUNCHD_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.dan.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>-m</string>
        <string>dan.server</string>
        <string>--host</string>
        <string>{host}</string>
        <string>--port</string>
        <string>{port}</string>
        <string>--no-reload</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{logs_dir}/server.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{logs_dir}/server.stderr.log</string>
    <key>WorkingDirectory</key>
    <string>{working_dir}</string>
</dict>
</plist>
"""

PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.dan.server.plist"

# --- Systemd unit template (Linux) ---

SYSTEMD_UNIT_TEMPLATE = """\
[Unit]
Description=DAN Server
After=network.target

[Service]
Type=simple
ExecStart={python_path} -m dan.server --host {host} --port {port} --no-reload
WorkingDirectory={working_dir}
Restart=on-failure
RestartSec=5
StandardOutput=append:{logs_dir}/server.stdout.log
StandardError=append:{logs_dir}/server.stderr.log

[Install]
WantedBy=default.target
"""

SYSTEMD_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
SYSTEMD_UNIT_PATH = SYSTEMD_UNIT_DIR / "dan-server.service"


def generate_plist(host: str, port: int) -> str:
    """Generate macOS launchd plist content."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LAUNCHD_PLIST_TEMPLATE.format(
        python_path=sys.executable,
        host=host,
        port=port,
        logs_dir=str(LOGS_DIR),
        working_dir=str(Path.cwd()),
    )


def generate_systemd_unit(host: str, port: int) -> str:
    """Generate Linux systemd user unit content."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return SYSTEMD_UNIT_TEMPLATE.format(
        python_path=sys.executable,
        host=host,
        port=port,
        logs_dir=str(LOGS_DIR),
        working_dir=str(Path.cwd()),
    )


def _rotate_logs() -> None:
    """Rotate existing log files, keeping last 5."""
    if not LOGS_DIR.exists():
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        return

    for name in ("server.stdout.log", "server.stderr.log"):
        log = LOGS_DIR / name
        if not log.exists():
            continue
        for i in range(4, 0, -1):
            src = LOGS_DIR / f"{name}.{i}"
            dst = LOGS_DIR / f"{name}.{i + 1}"
            if src.exists():
                if dst.exists():
                    dst.unlink()
                src.rename(dst)
        log.rename(LOGS_DIR / f"{name}.1")

    _enforce_log_budget()


def _enforce_log_budget() -> None:
    """Delete oldest log files when total size exceeds budget."""
    if not LOGS_DIR.exists():
        return
    max_bytes = int(os.environ.get("DAN_LOG_MAX_SIZE", 50 * 1024 * 1024))
    files = [f for f in LOGS_DIR.iterdir() if f.is_file()]
    total = sum(f.stat().st_size for f in files)
    if total <= max_bytes:
        return
    files.sort(key=lambda f: f.stat().st_mtime)
    for f in files:
        if total <= max_bytes:
            break
        if f.name == "_index.json":
            continue
        sz = f.stat().st_size
        f.unlink()
        total -= sz


def _check_health(port: int) -> tuple[bool, str]:
    """Check server health. Returns (ok, message)."""
    try:
        import httpx

        resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=3.0)
        if resp.status_code == 200:
            return True, "healthy"
        return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)


def _read_pid() -> tuple[int | None, int | None]:
    """Read PID and port from server.pid."""
    if not PID_FILE.exists():
        return None, None
    try:
        lines = PID_FILE.read_text().strip().split("\n")
        pid = int(lines[0])
        port = int(lines[1]) if len(lines) > 1 else DEFAULT_PORT
        return pid, port
    except (ValueError, IndexError, OSError):
        return None, None


def _is_alive(pid: int) -> bool:
    return is_process_alive(pid)


# === Subcommands ===


def cmd_install(args: argparse.Namespace) -> None:
    host = args.host
    port = args.port

    if sys.platform == "darwin":
        content = generate_plist(host, port)
        PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        PLIST_PATH.write_text(content)
        print(f"Installed launchd plist: {PLIST_PATH}")
        print(f"  Server will start at login on {host}:{port}")
        print(f"  Logs: {LOGS_DIR}/")
        print("  To load now: dan-service start")
    elif sys.platform == "linux":
        content = generate_systemd_unit(host, port)
        SYSTEMD_UNIT_DIR.mkdir(parents=True, exist_ok=True)
        SYSTEMD_UNIT_PATH.write_text(content)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(
            ["systemctl", "--user", "enable", "dan-server"], check=False
        )
        print(f"Installed systemd unit: {SYSTEMD_UNIT_PATH}")
        print(f"  Server will start at login on {host}:{port}")
        print(f"  Logs: {LOGS_DIR}/")
        print("  To start now: dan-service start")
    else:
        print(
            f"Platform '{sys.platform}' not supported for service install.",
            file=sys.stderr,
        )
        print("Use 'dan-up' for manual server management.")
        sys.exit(1)


def cmd_uninstall(args: argparse.Namespace) -> None:
    if sys.platform == "darwin":
        if PLIST_PATH.exists():
            subprocess.run(
                [
                    "launchctl",
                    "bootout",
                    f"gui/{os.getuid()}",
                    str(PLIST_PATH),
                ],
                check=False,
                capture_output=True,
            )
            PLIST_PATH.unlink()
            print("Uninstalled launchd plist.")
        else:
            print("No plist found.")
    elif sys.platform == "linux":
        subprocess.run(
            ["systemctl", "--user", "stop", "dan-server"],
            check=False,
            capture_output=True,
        )
        subprocess.run(
            ["systemctl", "--user", "disable", "dan-server"],
            check=False,
            capture_output=True,
        )
        if SYSTEMD_UNIT_PATH.exists():
            SYSTEMD_UNIT_PATH.unlink()
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"], check=False
            )
            print("Uninstalled systemd unit.")
        else:
            print("No systemd unit found.")
    else:
        print(f"Platform '{sys.platform}' not supported.", file=sys.stderr)


def cmd_start(args: argparse.Namespace) -> None:
    _rotate_logs()
    if sys.platform == "darwin" and PLIST_PATH.exists():
        subprocess.run(
            [
                "launchctl",
                "bootstrap",
                f"gui/{os.getuid()}",
                str(PLIST_PATH),
            ],
            check=False,
        )
        print("Service started via launchd.")
    elif sys.platform == "linux" and SYSTEMD_UNIT_PATH.exists():
        subprocess.run(
            ["systemctl", "--user", "start", "dan-server"], check=True
        )
        print("Service started via systemd.")
    else:
        print("No service installed. Use 'dan-up' for manual start.")
        sys.exit(1)


def cmd_stop(args: argparse.Namespace) -> None:
    if sys.platform == "darwin" and PLIST_PATH.exists():
        subprocess.run(
            [
                "launchctl",
                "bootout",
                f"gui/{os.getuid()}",
                str(PLIST_PATH),
            ],
            check=False,
        )
        print("Service stopped via launchd.")
    elif sys.platform == "linux" and SYSTEMD_UNIT_PATH.exists():
        subprocess.run(
            ["systemctl", "--user", "stop", "dan-server"], check=True
        )
        print("Service stopped via systemd.")
    else:
        pid, _ = _read_pid()
        if pid and _is_alive(pid):
            import signal

            os.kill(pid, signal.SIGTERM)
            print(f"Sent SIGTERM to PID {pid}.")
        else:
            print("No running server found.")


def cmd_status(args: argparse.Namespace) -> None:
    pid, port = _read_pid()
    port = port or DEFAULT_PORT
    running = pid is not None and _is_alive(pid)
    healthy, health_msg = (
        _check_health(port) if running else (False, "not running")
    )

    try:
        from dan.cli import _try_import_rich

        Console, _ = _try_import_rich()
    except Exception:
        Console = None

    if Console:
        console = Console()
        console.print("[bold]DAN Server Status[/bold]")
        status_icon = "[green]●[/green]" if running else "[red]●[/red]"
        console.print(
            f"  Status: {status_icon} {'running' if running else 'stopped'}"
        )
        if pid:
            console.print(f"  PID: {pid}")
        console.print(f"  Port: {port}")
        health_icon = "[green]✓[/green]" if healthy else "[red]✗[/red]"
        console.print(f"  Health: {health_icon} {health_msg}")
        if healthy:
            try:
                import httpx

                resp = httpx.get(
                    f"http://127.0.0.1:{port}/api/gateway/activity",
                    timeout=3.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    active = len(data.get("active", []))
                    console.print(f"  Active runs: {active}")
            except Exception:
                pass
    else:
        print("DAN Server Status")
        print(f"  Status: {'running' if running else 'stopped'}")
        if pid:
            print(f"  PID: {pid}")
        print(f"  Port: {port}")
        print(f"  Health: {health_msg}")


def cmd_health(args: argparse.Namespace) -> None:
    _, port = _read_pid()
    port = port or DEFAULT_PORT
    ok, msg = _check_health(port)
    if ok:
        print(f"OK — {msg}")
    else:
        print(f"FAIL — {msg}", file=sys.stderr)
        sys.exit(1)


def cmd_logs(args: argparse.Namespace) -> None:
    stdout_log = LOGS_DIR / "server.stdout.log"
    stderr_log = LOGS_DIR / "server.stderr.log"

    if not stdout_log.exists() and not stderr_log.exists():
        print(f"No logs found in {LOGS_DIR}/")
        return

    if args.follow:
        files = [str(f) for f in (stdout_log, stderr_log) if f.exists()]
        try:
            subprocess.run(["tail", "-f"] + files)
        except KeyboardInterrupt:
            pass
    else:
        n = args.lines
        for log_file in (stdout_log, stderr_log):
            if log_file.exists():
                print(f"--- {log_file.name} ---")
                lines = log_file.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                for line in lines[-n:]:
                    print(line)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dan-service",
        description="Manage DAN server as an OS service.",
    )
    sub = parser.add_subparsers(dest="command")

    p_install = sub.add_parser(
        "install", help="Install as OS service (launchd/systemd)"
    )
    p_install.add_argument("--host", default=DEFAULT_HOST)
    p_install.add_argument("--port", type=int, default=DEFAULT_PORT)

    sub.add_parser("uninstall", help="Remove OS service")
    sub.add_parser("start", help="Start the service")
    sub.add_parser("stop", help="Stop the service")
    sub.add_parser("status", help="Show server status")
    sub.add_parser("health", help="Health check probe")

    p_logs = sub.add_parser("logs", help="Tail server logs")
    p_logs.add_argument(
        "-f", "--follow", action="store_true", help="Follow log output"
    )
    p_logs.add_argument(
        "-n",
        "--lines",
        type=int,
        default=50,
        help="Number of lines to show",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "health": cmd_health,
        "logs": cmd_logs,
    }
    cmds[args.command](args)
