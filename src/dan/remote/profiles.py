"""Local SSH connection directory and explicit, resumable-by-retry bootstrap."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import shlex
import tarfile
import tempfile
import time
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from dan.remote.relay import private_address
from dan.server.paths import resolve_graphs_dir


class Profile(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    name: str = Field(min_length=1, max_length=80)
    ssh_alias: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,127}$")
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    identity_file: str = Field(default="", max_length=1024)
    relay_enabled: bool = True  # Existing profiles already use the private relay.
    relay_ssh_alias: str = Field(default="ny", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,127}$")
    ssh_via_relay: bool = True
    package_source: Literal["pypi", "tsinghua"] = "pypi"
    address: str = ""
    port: int = Field(default=8765, ge=1024, le=65535)
    relay_address: str = "10.77.77.1"
    relay_port: int = Field(default=8765, ge=1024, le=65535)
    workspace: str = Field(default="~/Downloads/local_projects", min_length=1, max_length=1024)

    @field_validator("address", "relay_address")
    @classmethod
    def private_ip(cls, value):
        return private_address(value) if value else ""

    @field_validator("identity_file")
    @classmethod
    def safe_identity(cls, value):
        if value and (not value.startswith(("/", "~/")) or any(ord(c) < 32 for c in value)):
            raise ValueError("Use an absolute identity file path or ~/.ssh/key")
        return value

    @model_validator(mode="after")
    def relay_ready(self):
        if self.relay_enabled and (not self.address or not self.relay_address):
            raise ValueError("Phone access requires the remote and relay private addresses")
        return self

    @field_validator("workspace")
    @classmethod
    def safe_workspace(cls, value):
        if any(ord(char) < 32 for char in value) or not value.startswith(("/", "~/")):
            raise ValueError("Use an absolute remote folder or ~/folder")
        return value

    @property
    def url(self):
        if not self.relay_enabled:
            return None
        host = f"[{self.relay_address}]" if ":" in self.relay_address else self.relay_address
        return f"http://{host}:{self.relay_port}"


def directory():
    path = Path(resolve_graphs_dir()) / "remote_connections"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def read_profiles():
    path = directory() / "profiles.json"
    return json.loads(path.read_text()) if path.exists() else {}


def write_profiles(profiles):
    from dan._atomic_file import atomic_write_text
    atomic_write_text(directory() / "profiles.json", json.dumps(profiles, indent=2), mode=0o600)


def public_profile(value):
    return {k: v for k, v in value.items() if k != "access_key"}


def save_profile(profile):
    values = read_profiles()
    for key, value in values.items():
        if profile.relay_enabled and value.get("relay_enabled", True) and key != profile.id and (value["relay_address"], value["relay_port"]) == (profile.relay_address, profile.relay_port):
            raise ValueError("Another machine already uses that relay address and port")
    old = values.get(profile.id, {})
    normalized = Profile.model_validate(old).model_dump() if old else {}
    changed = any(normalized.get(k) != v for k, v in profile.model_dump().items() if k != "name")
    values[profile.id] = {**old, **profile.model_dump(), "url": profile.url, "access_key": old.get("access_key") or secrets.token_urlsafe(32), "installed": old.get("installed", False) and not changed}
    write_profiles(values)
    return public_profile(values[profile.id])


async def command(args, *, data=None, timeout=30):
    process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE if data else asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(data), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise RuntimeError("Remote operation timed out; inspect the service before retrying")
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="replace")[-3000:] or stdout.decode(errors="replace")[-3000:])
    return stdout.decode()


def ssh(alias, jump=None, *, port=None, identity_file=""):
    return ["ssh", *ssh_options(jump, port=port, identity_file=identity_file), alias]


def ssh_options(jump=None, *, port=None, identity_file="", scp=False):
    return ["-o", "ControlPath=none", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", *(["-J", jump] if jump else []), *(["-P" if scp else "-p", str(port)] if port else []), *(["-i", str(Path(identity_file).expanduser())] if identity_file else [])]


def ssh_hosts():
    """Suggest literal aliases, including Include files; never return key/config contents."""
    import glob
    import re
    hosts, visited = set(), set()
    base = Path.home() / ".ssh"
    def read(path, depth=0):
        path = Path(path).expanduser().resolve()
        if depth > 8 or path in visited or len(visited) >= 100:
            return
        visited.add(path)
        try:
            if path.stat().st_size > 1024 * 1024:
                return
            lines = path.read_text().splitlines()
        except (OSError, UnicodeError):
            return
        for line in lines:
            try:
                words = shlex.split(line, comments=True)
            except ValueError:
                continue
            if not words:
                continue
            directive, *args = words
            if "=" in directive:
                directive, value = directive.split("=", 1)
                args = [value, *args]
            args = [word for word in args if word and word != "="]
            if directive.lower() == "host":
                hosts.update(word for word in args if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", word))
            elif directive.lower() == "include":
                for pattern in args:
                    pattern = Path(pattern).expanduser()
                    for included in glob.iglob(str(pattern if pattern.is_absolute() else base / pattern)):
                        read(included, depth + 1)
    read(base / "config")
    return sorted(hosts, key=str.lower)


async def inspect(profile):
    # No account tokens, config contents, shell startup scripts, or private keys.
    script = '''import json,os,platform,shutil,subprocess
from pathlib import Path
h=Path.home()
p=":".join(map(str,[h/".local/bin",*sorted((h/".nvm/versions/node").glob("*/bin"),reverse=True),h/".npm-global/bin",Path("/usr/local/bin"),Path("/usr/bin"),Path("/bin")]))
print(json.dumps({"hostname":platform.node(),"platform":platform.system(),"home":str(h),"tools":{n:shutil.which(n,path=p) for n in ["python3.11","python3.12","python3.13","uv","node","codex","claude","cursor","agy"]},"linger":subprocess.run(["loginctl","show-user",os.environ.get("USER",""),"-p","Linger"],capture_output=True,text=True).stdout.strip() if shutil.which("loginctl") else "Boot persistence not checked"}))'''
    output = await command(ssh(profile.ssh_alias, profile.relay_ssh_alias if profile.ssh_via_relay else None, port=profile.ssh_port, identity_file=profile.identity_file) + ["python3 -c " + shlex.quote(script)])
    return json.loads(output)


def build_bundle():
    root = Path(os.environ.get("DAN_SOURCE_ROOT", str(Path(__file__).resolve().parents[3])))
    web = root / "editor/dist"
    if not (root / "pyproject.toml").is_file() or not (web / "index.html").is_file():
        raise RuntimeError("Remote install needs a DAN source checkout and built editor. Run npm run build:verify in editor; set DAN_SOURCE_ROOT if needed.")
    handle, filename = tempfile.mkstemp(suffix=".tar.gz", prefix="dan-release-")
    os.close(handle)
    def filtered(info):
        return None if "__pycache__" in info.name or info.name.endswith(".pyc") else info
    with tarfile.open(filename, "w:gz") as archive:
        for source, target in [(root / "src", "src"), (root / "pyproject.toml", "pyproject.toml"), (root / "README.md", "README.md"), (web, "web")]:
            archive.add(source, arcname=target, filter=filtered)
        wheelhouse = os.environ.get("DAN_REMOTE_WHEELHOUSE")
        if wheelhouse:
            for wheel in sorted(Path(wheelhouse).glob("*.whl")):
                if wheel.is_file() and not wheel.is_symlink():
                    archive.add(wheel, arcname="wheels/" + wheel.name)
    return Path(filename)


async def deploy(value, *, share_openrouter=False, progress=lambda message: None):
    profile = Profile.model_validate(value)
    if not profile.relay_enabled:
        raise ValueError("Set up phone access before installing the persistent remote service")
    release = time.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)
    config = {**profile.model_dump(), "url": profile.url, "access_key": value["access_key"], "release": release, "archive": f".dan-release-{release}.tar.gz"}
    installer = Path(__file__).with_name("install.py").read_text()
    progress("Packaging DAN and its browser app")
    archive = await asyncio.to_thread(build_bundle)
    try:
        progress("Uploading the release to " + profile.ssh_alias)
        await command(["scp", "-q", *ssh_options(profile.relay_ssh_alias if profile.ssh_via_relay else None, port=profile.ssh_port, identity_file=profile.identity_file, scp=True), str(archive), f"{profile.ssh_alias}:{config['archive']}"], timeout=600)
    finally:
        archive.unlink(missing_ok=True)
    if share_openrouter:
        from dan.native_workers.models import openrouter_key
        # Only the explicitly selected provider credential is provisioned.
        config["openrouter_key"] = openrouter_key()
        if not config["openrouter_key"]:
            raise RuntimeError("No local OpenRouter key is configured")
    progress("Installing the persistent execution service")
    execution = json.loads(await command(ssh(profile.ssh_alias, profile.relay_ssh_alias if profile.ssh_via_relay else None, port=profile.ssh_port, identity_file=profile.identity_file) + ["python3 -c " + shlex.quote(installer)], data=json.dumps({**config, "role": "execution"}).encode(), timeout=600))
    progress("Installing the relay on " + profile.relay_ssh_alias)
    relay_config = {**profile.model_dump(), "role": "relay", "relay_source": Path(__file__).with_name("relay.py").read_text()}
    relay = json.loads(await command(ssh(profile.relay_ssh_alias) + ["python3 -c " + shlex.quote(installer)], data=json.dumps(relay_config).encode(), timeout=90))
    progress("Checking authenticated access through the relay")
    profiles = read_profiles()
    profiles[profile.id].update(installed=True, execution=execution, relay=relay, connection_error="")
    write_profiles(profiles)
    import httpx
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(profile.url + "/api/health", headers={"Authorization": "Bearer " + value["access_key"]}, timeout=20)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        message = f"Services are installed, but this computer cannot reach the authenticated relay at {profile.url}. Check your VPN and the relay firewall's private port {profile.relay_port}."
        profiles[profile.id]["connection_error"] = message
        write_profiles(profiles)
        raise RuntimeError(message) from exc
    return {"url": profile.url, "execution": execution, "relay": relay}
