"""Dependency-free remote installer, sent over SSH with configuration on stdin."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


def run(args, **kwargs):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=480, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stdout[-4000:])
    return result.stdout.strip()


def private_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with open(temporary, "w", opener=lambda p, f: os.open(p, f, 0o600)) as handle:
        handle.write(text)
    temporary.replace(path)


def install(config):
    home = Path.home()
    root = home / ".local/share/dan-remote" / config["id"]
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    role = config["role"]
    unit = f"dan-{role}-{config['id']}.service"
    units = home / ".config/systemd/user"
    units.mkdir(parents=True, exist_ok=True)
    # Existing user tools/accounts are discovered in the same PATH as the daemon.
    node_dirs = sorted((home / ".nvm/versions/node").glob("*/bin"), reverse=True)
    service_path = ":".join(map(str, [home / ".local/bin", *node_dirs, home / ".npm-global/bin", Path("/usr/local/bin"), Path("/usr/bin"), Path("/bin")]))
    env = dict(os.environ, PATH=service_path)
    old_unit = (units / unit).read_text() if (units / unit).exists() else None
    old_access = (root / "access.json").read_text() if (root / "access.json").exists() else None
    active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit], capture_output=True).returncode == 0
    if not active:
        import socket
        address = config["relay_address"] if role == "relay" else config["address"]
        port = config["relay_port"] if role == "relay" else config["port"]
        try:
            with socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((address, port))
        except OSError as exc:
            raise RuntimeError(f"Cannot listen on {address}:{port}. Check the VPN address or choose another port in Settings: {exc}") from exc
    if role == "relay":
        private_write(root / "relay.py", config["relay_source"])
        command = [sys.executable, str(root / "relay.py"), "--bind", config["relay_address"], "--port", str(config["relay_port"]), "--target", config["address"], "--target-port", str(config["port"])]
        environment = ""
    else:
        release = root / "releases" / config["release"]
        release.mkdir(parents=True, exist_ok=True)
        archive = home / config["archive"]
        if archive.exists():
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():
                    if not (member.isfile() or member.isdir()) or not (release / member.name).resolve().is_relative_to(release.resolve()):
                        raise RuntimeError("Invalid release archive")
                tar.extractall(release)
            archive.unlink()
        elif not (release / "pyproject.toml").is_file():
            raise RuntimeError("Release archive is missing; upload it before installation")
        python = next((shutil.which(name, path=service_path) for name in ("python3.13", "python3.12", "python3.11") if shutil.which(name, path=service_path)), None)
        if not python:
            raise RuntimeError("Python 3.11+ is required. Install it on this host, then retry.")
        venv = release / "venv"
        index = "https://pypi.tuna.tsinghua.edu.cn/simple" if config.get("package_source") == "tsinghua" else "https://pypi.org/simple"
        packages = ["--no-index", "--find-links", str(release / "wheels")] if (release / "wheels").is_dir() else ["--index-url", index]
        uv = shutil.which("uv", path=service_path)
        if uv:
            if not (venv / "bin/python").exists():
                run([uv, "venv", "--python", python, str(venv)], env=env)
            run([uv, "pip", "install", *packages, "--python", str(venv / "bin/python"), str(release) + "[cli,all-tools]"], env=env)
        else:
            run([python, "-m", "venv", str(venv)], env=env)
            run([str(venv / "bin/pip"), "install", *packages, str(release) + "[cli,all-tools]"], env=env)
        workspace = Path(config["workspace"]).expanduser()
        if not workspace.is_dir():
            raise RuntimeError("The remote workspace must be an existing directory")
        state = root / "state"
        state.mkdir(exist_ok=True)
        private_write(root / "access.json", json.dumps({k: config[k] for k in ("id", "url", "access_key")}))
        if config.get("openrouter_key"):
            private_write(root / "provider.env", "DAN_LLM_BASE_URL=https://openrouter.ai/api/v1\nDAN_LLM_API_KEY=" + config["openrouter_key"] + "\n")
        command = [str(venv / "bin/python"), "-m", "dan.server", "--host", config["address"], "--port", str(config["port"]), "--no-reload"]
        variables = {"DAN_REMOTE_CONFIG": str(root / "access.json"), "DAN_GRAPHS_DIR": str(state), "DAN_WORKSPACE_ROOT": str(workspace), "DAN_STATIC_DIR": str(release / "web")}
        environment = "".join("Environment=" + quote(k + "=" + v) + "\n" for k, v in variables.items())
        environment += "EnvironmentFile=-" + str(root / "provider.env").replace("%", "%%") + "\n"
    unit_text = ("[Unit]\nDescription=DAN " + role + " " + config["id"] + "\nAfter=network-online.target\nStartLimitIntervalSec=0\n\n[Service]\nType=simple\n"
                 + "WorkingDirectory=" + str(root).replace("%", "%%") + "\nEnvironment=" + quote("PATH=" + service_path) + "\n" + environment
                 + "ExecStart=" + " ".join(quote(arg) for arg in command) + "\nRestart=always\nRestartSec=5\nUMask=0077\n\n[Install]\nWantedBy=default.target\n")
    private_write(units / unit, unit_text)
    try:
        run(["systemctl", "--user", "daemon-reload"])
        run(["systemctl", "--user", "enable", unit])
        run(["systemctl", "--user", "restart", unit])
        import time
        time.sleep(3)
        run(["systemctl", "--user", "is-active", unit])
        if role == "execution":
            import urllib.request
            host = config["address"]
            if ":" in host:
                host = "[" + host + "]"
            request = urllib.request.Request(f"http://{host}:{config['port']}/api/health", headers={"Authorization": "Bearer " + config["access_key"]})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for attempt in range(15):
                try:
                    with opener.open(request, timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    if attempt == 14:
                        raise RuntimeError("DAN did not become healthy; inspect journalctl --user -u " + unit)
                    time.sleep(1)
    except Exception:
        if old_unit:
            if old_access:
                private_write(root / "access.json", old_access)
            private_write(units / unit, old_unit)
            run(["systemctl", "--user", "daemon-reload"])
            # Do not hide the installation error if a previous unit was invalid.
            subprocess.run(["systemctl", "--user", "restart", unit], capture_output=True)
        else:
            subprocess.run(["systemctl", "--user", "disable", "--now", unit], capture_output=True)
        raise
    # Ordinary users can enable lingering for themselves on supported hosts.
    subprocess.run(["loginctl", "enable-linger", os.environ["USER"]], capture_output=True)
    linger = run(["loginctl", "show-user", os.environ["USER"], "-p", "Linger"])
    return {"unit": unit, "active": True, "boot_persistent": linger == "Linger=yes", "root": str(root)}


def quote(value):
    # systemd has its own quoting and specifier expansion, not shell quoting.
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


if __name__ == "__main__":
    try:
        print(json.dumps(install(json.load(sys.stdin))))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
