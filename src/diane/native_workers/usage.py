"""Near-real-time quota/usage for the accounts Diane runs agents with.

Codex reports per-login rate limits through its app-server; Claude Code reports
usage through Anthropic's OAuth usage endpoint using the CLI's own login.
Credentials are read server-side only and never returned to the browser.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import time
import threading
from collections import defaultdict
from urllib.request import Request, urlopen

from .catalog import accounts, user_home

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_claude_cache: dict[str, tuple[float, dict]] = {}
CLAUDE_CACHE_SECONDS = 30


def _window_label(minutes: int | None) -> str:
    if not minutes:
        return "limit"
    if minutes >= 10080:
        return "week" if minutes == 10080 else f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


async def _codex_snapshot(home: Path) -> dict:
    """Read this login's quota without opening a thread or starting a model turn."""
    import asyncio
    from .catalog import binary, native_cli_path
    executable = binary("codex")
    if not executable:
        raise FileNotFoundError("Codex CLI unavailable")
    process = await asyncio.create_subprocess_exec(
        executable, "app-server", "--listen", "stdio://",
        cwd=str(home), env={**os.environ, "CODEX_HOME": str(home), "PATH": native_cli_path(os.environ.get("PATH", ""))},
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=2 * 1024 * 1024)

    async def request(sequence, method, params):
        process.stdin.write((json.dumps({"id": sequence, "method": method, "params": params}) + "\n").encode())
        await process.stdin.drain()
        while line := await process.stdout.readline():
            row = json.loads(line)
            if row.get("id") != sequence or "method" in row:
                continue
            if "error" in row:
                raise ValueError("Codex quota request failed")
            return row.get("result", {})
        raise ConnectionError("Codex quota connection closed")

    async def read():
        await request(1, "initialize", {"clientInfo": {"name": "diane_usage", "version": "1.0.0"}})
        process.stdin.write(b'{"method":"initialized","params":{}}\n')
        await process.stdin.drain()
        return await request(2, "account/rateLimits/read", {})

    try:
        return await asyncio.wait_for(read(), timeout=12)
    finally:
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


def _codex_windows(data: dict) -> dict:
    buckets = data.get("rateLimitsByLimitId") or {"codex": data.get("rateLimits", {})}
    windows, plan = [], ""
    for name, limits in buckets.items():
        if not isinstance(limits, dict):
            continue
        plan = plan or limits.get("planType") or ""
        for key in ("primary", "secondary"):
            bucket = limits.get(key)
            if not isinstance(bucket, dict) or bucket.get("usedPercent") is None:
                continue
            minutes = bucket.get("windowDurationMins")
            label = _window_label(minutes)
            if name != "codex":
                label = f"{limits.get('limitName') or name} · {label}"
            windows.append({"label": label, "used_percent": float(bucket["usedPercent"]),
                            "resets_at": bucket.get("resetsAt"), "minutes": minutes or 0})
    windows.sort(key=lambda window: window.pop("minutes"))
    return {"windows": windows, "plan": plan}


_codex_cache: dict[str, tuple[float, dict]] = {}
_codex_locks = defaultdict(threading.Lock)


def codex_usage(home: Path) -> dict:
    home = home.expanduser().resolve()
    with _codex_locks[str(home)]:
        return _read_codex_usage(home)


def _read_codex_usage(home: Path) -> dict:
    import asyncio
    home = home.expanduser().resolve()
    try:
        auth_stamp = (home / "auth.json").stat().st_mtime_ns
    except OSError:
        auth_stamp = 0
    key = f"{home}:{auth_stamp}"
    cached = _codex_cache.get(key)
    if cached and time.time() - cached[0] < 30:
        return dict(cached[1])
    try:
        result = {**_codex_windows(asyncio.run(_codex_snapshot(home))), "observed_at": time.time()}
    except Exception as exc:
        # Never substitute a shared transcript's quota for this account's quota.
        result = {"windows": [], "error": f"Account quota unavailable ({type(exc).__name__})"}
    _codex_cache[key] = (time.time(), result)
    return dict(result)


def _claude_token(env: dict) -> str:
    config_dir = env.get("CLAUDE_CONFIG_DIR")
    def from_file(directory: Path) -> str:
        try:
            data = json.loads((directory / ".credentials.json").read_text())
            return str(data.get("claudeAiOauth", {}).get("accessToken") or "")
        except (OSError, ValueError, AttributeError):
            return ""
    if config_dir:
        return from_file(Path(config_dir).expanduser())
    try:
        # An agent-launched desktop can inherit a redirected HOME. The default
        # profile belongs to the OS user; do not search that agent's Keychain.
        keychain = user_home() / "Library/Keychains/login.keychain-db"
        command = ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"]
        if keychain.is_file():
            command.append(str(keychain))
        raw = subprocess.run(command,
                             capture_output=True, text=True, timeout=5).stdout.strip()
        token = str(json.loads(raw).get("claudeAiOauth", {}).get("accessToken") or "")
        if token:
            return token
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
        pass
    return from_file(user_home() / ".claude")


def _pretty_bucket(key: str) -> str:
    return {"five_hour": "5h", "seven_day": "week"}.get(key, key.removeprefix("seven_day_").replace("_", " "))


def claude_usage(account: str, env: dict) -> dict:
    token = _claude_token(env)
    if not token:
        return {"windows": [], "error": "No readable Claude Code subscription login. Sign in to Claude Code or select a signed-in profile."}
    cache_key = hashlib.sha256(token.encode()).hexdigest()
    cached = _claude_cache.get(cache_key)
    if cached and time.time() - cached[0] < CLAUDE_CACHE_SECONDS:
        return cached[1]
    try:
        request = Request(CLAUDE_USAGE_URL, headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"})
        with urlopen(request, timeout=8) as response:
            data = json.loads(response.read())
    except Exception as exc:  # network or auth: report, never raise into the API
        return {"windows": [], "error": f"Usage unavailable ({type(exc).__name__})"}
    windows = _claude_windows(data)
    result = {"windows": windows, "observed_at": time.time()}
    _claude_cache[cache_key] = (time.time(), result)
    return result


def _claude_windows(data: dict) -> list[dict]:
    """Prefer the structured `limits` list (session, weekly, per-model); fall back to the named buckets."""
    windows = []
    for limit in data.get("limits") or []:
        if not isinstance(limit, dict) or limit.get("percent") is None:
            continue
        scope = limit.get("scope") if isinstance(limit.get("scope"), dict) else {}
        model = (scope.get("model") or {}).get("display_name") if isinstance(scope.get("model"), dict) else None
        kind = str(limit.get("kind") or "")
        label = "5h" if kind == "session" else "week" if kind == "weekly_all" else str(model or kind.replace("_", " ")).lower()
        resets = limit.get("resets_at")
        windows.append({"label": label, "used_percent": float(limit["percent"]), "resets_at": _iso_to_epoch(resets) if resets else None})
    if windows:
        return windows
    for key in ("five_hour", "seven_day", *[k for k in data if k.startswith("seven_day_")]):
        bucket = data.get(key)
        if isinstance(bucket, dict) and bucket.get("utilization") is not None:
            resets = bucket.get("resets_at")
            windows.append({"label": _pretty_bucket(key), "used_percent": float(bucket["utilization"]), "resets_at": _iso_to_epoch(resets) if resets else None})
    return windows


def _iso_to_epoch(value: str) -> float | None:
    from datetime import datetime
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def recent_accounts(base: Path, limit: int = 2) -> list[tuple[str, str]]:
    """(backend, account) pairs ordered by most recent lead/worker record."""
    seen: dict[tuple[str, str], float] = {}
    for folder in ("native_leads", "native_workers"):
        for path in (base / folder).glob("*.json"):
            try:
                record = json.loads(path.read_text())
                stamp = path.stat().st_mtime
            except (OSError, ValueError):
                continue
            key = (str(record.get("backend") or ""), str((record.get("profile") or {}).get("account") or "default"))
            if key[0] and stamp > seen.get(key, 0):
                seen[key] = stamp
    return [key for key, _ in sorted(seen.items(), key=lambda item: item[1], reverse=True)[:limit]]


def openrouter_usage(key: str) -> dict:
    try:
        request = Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
        with urlopen(request, timeout=8) as response:
            data = json.loads(response.read())["data"]
        # Deliberately omit the API's key label, IDs and other credential metadata.
        spend = {name: data.get(name) for name in ("usage", "usage_daily", "usage_monthly", "limit", "limit_remaining", "limit_reset")}
        return {"windows": [], "spend": spend, "observed_at": time.time()}
    except Exception as exc:
        return {"windows": [], "error": f"Usage unavailable ({type(exc).__name__})"}


def usage_report(base: Path, limit: int | None = None) -> dict:
    """All discovered logins and configured API providers; recency only sets order."""
    from concurrent.futures import ThreadPoolExecutor
    from .models import API_PROVIDERS, provider_key
    configured = accounts()
    # A desktop launch can inherit the app profile; still include the native CLI home.
    native = user_home() / ".codex"
    codex = dict(configured.get("codex", {}))
    if native.is_dir():
        codex.setdefault("personal", {"label": "Native CLI", "env": {"CODEX_HOME": str(native)}})
    configured = {**configured, "codex": codex}
    recent = recent_accounts(base, 1000)
    jobs, seen = [], {}
    for backend in ("codex", "claude"):
        for account, profile in configured.get(backend, {}).items():
            env = profile.get("env", {})
            home = Path(env.get("CODEX_HOME") or native).expanduser().resolve() if backend == "codex" else None
            # Deduplicate aliases by credential location, never by shared session storage.
            identity = str((home / "auth.json").resolve()) if home else str(Path(env["CLAUDE_CONFIG_DIR"]).expanduser().resolve()) if env.get("CLAUDE_CONFIG_DIR") else "keychain"
            store = (backend, identity)
            alias = "Native CLI" if backend == "codex" and home == native.resolve() else "Default profile" if account == "default" else account
            if store in seen:
                seen[store]["aliases"].append(alias)
                continue
            row = {"backend": backend, "account": account, "label": alias, "aliases": [alias]}
            seen[store] = row
            jobs.append((row, (lambda home=home: codex_usage(home)) if home else (lambda account=account, env=env: claude_usage(account, env))))
    for source in API_PROVIDERS:
        key = provider_key(source)
        if not key:
            continue
        row = {"backend": source, "account": "api", "label": "Configured API key", "aliases": []}
        jobs.append((row, (lambda key=key: openrouter_usage(key)) if source == "openrouter" else
                     (lambda: {"windows": [], "error": "Quota is available in the provider’s billing dashboard."})))
    jobs.sort(key=lambda job: min((recent.index((job[0]["backend"], a)) for a in [job[0]["account"], *job[0]["aliases"]] if (job[0]["backend"], a) in recent), default=len(recent)))
    if limit is not None:
        jobs = jobs[:max(1, limit)]
    def collect(job):
        row, read = job
        try:
            result = read()
        except Exception as exc:
            result = {"windows": [], "error": f"Usage unavailable ({type(exc).__name__})"}
        return {**row, **result}
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(collect, jobs))
    return {"accounts": rows, "fetched_at": time.time()}
