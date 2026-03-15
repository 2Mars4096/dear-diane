"""dan-editor — start DAN server + visual editor in one terminal."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from dan.cli.up import (
    check_health,
    is_process_alive,
    read_pid_file,
    remove_pid_file,
    start_server,
    wait_for_health,
    write_pid_file,
)

_EDITOR_DIR = Path(__file__).resolve().parent.parent.parent.parent / "editor"


def _find_editor_dir() -> Path | None:
    candidates = [
        _EDITOR_DIR,
        Path.cwd() / "editor",
        Path(__file__).resolve().parent.parent.parent.parent.parent / "editor",
    ]
    for p in candidates:
        if (p / "package.json").exists():
            return p
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dan-editor",
        description="Start DAN server and visual editor together.",
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Server port (default: 8000)",
    )
    parser.add_argument(
        "--editor-port", type=int, default=None,
        help="Editor dev server port (passed via env to Vite/Electron)",
    )
    args = parser.parse_args()
    port: int = args.port

    editor_dir = _find_editor_dir()
    if editor_dir is None:
        print(
            "Could not find the editor/ directory. "
            "Run this command from the project root or ensure editor/ exists.",
            file=sys.stderr,
        )
        sys.exit(1)

    pid, existing_port = read_pid_file()
    server_proc: subprocess.Popen | None = None
    we_started_server = False

    if pid and is_process_alive(pid) and check_health(existing_port or port):
        resolved_port = existing_port or port
        print(f"DAN server already running (PID {pid}, port {resolved_port})")
    else:
        if pid:
            remove_pid_file()
        print(f"Starting DAN server on port {port}...")
        new_pid = start_server(port)
        write_pid_file(new_pid, port)
        we_started_server = True

        if wait_for_health(port):
            print(f"Server ready (PID {new_pid})")
        else:
            print("Server failed to start within timeout.", file=sys.stderr)
            if is_process_alive(new_pid):
                try:
                    os.kill(new_pid, signal.SIGTERM)
                except OSError:
                    pass
            remove_pid_file()
            sys.exit(1)

    editor_env = {**os.environ}
    if args.editor_port:
        editor_env["PORT"] = str(args.editor_port)

    print(f"Starting editor dev server in {editor_dir}...")
    editor_proc: subprocess.Popen | None = None
    try:
        editor_proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=str(editor_dir),
            env=editor_env,
        )
        editor_proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        if editor_proc is not None and editor_proc.poll() is None:
            editor_proc.send_signal(signal.SIGTERM)
            try:
                editor_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                editor_proc.kill()

        if we_started_server:
            server_pid, _ = read_pid_file()
            if server_pid and is_process_alive(server_pid):
                print(f"Stopping DAN server (PID {server_pid})...")
                try:
                    os.kill(server_pid, signal.SIGTERM)
                    for _ in range(20):
                        time.sleep(0.25)
                        if not is_process_alive(server_pid):
                            break
                    if is_process_alive(server_pid):
                        os.kill(server_pid, signal.SIGKILL)
                except OSError:
                    pass
                remove_pid_file()


if __name__ == "__main__":
    main()
