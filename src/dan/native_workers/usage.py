"""Near-real-time quota/usage for the accounts DAN runs agents with.

Codex writes its rate limits into every session rollout; Claude Code reports
usage through Anthropic's OAuth usage endpoint using the CLI's own login.
Credentials are read server-side only and never returned to the browser.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen

from .catalog import accounts, user_home

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_claude_cache: dict[str, tuple[float, dict]] = {}
CLAUDE_CACHE_SECONDS = 15


def _window_label(minutes: int | None) -> str:
    if not minutes:
        return "limit"
    if minutes >= 10080:
        return "week" if minutes == 10080 else f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def codex_usage(home: Path) -> dict:
    """Latest rate limits recorded by any Codex session under this account's home."""
    sessions = home / "sessions"
    newest = None
    if sessions.is_dir():
        for path in sessions.glob("*/*/*/*.jsonl"):
            try:
                stamp = path.stat().st_mtime
            except OSError:
                continue
            if newest is None or stamp > newest[0]:
                newest = (stamp, path)
    if newest is None:
        return {"windows": [], "error": "No Codex sessions yet"}
    stamp, path = newest
    limits = None
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - 512_000))
            for line in stream.read().decode(errors="replace").splitlines():
                if '"rate_limits"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
                found = payload.get("rate_limits") or (payload.get("info") or {}).get("rate_limits") if isinstance(payload, dict) else None
                if isinstance(found, dict):
                    limits = found
    except OSError:
        return {"windows": [], "error": "Could not read the Codex session log"}
    if not limits:
        return {"windows": [], "error": "No rate-limit data in the latest session"}
    windows = []
    for key in ("secondary", "primary"):  # short window first
        bucket = limits.get(key)
        if isinstance(bucket, dict) and bucket.get("used_percent") is not None:
            windows.append({"label": _window_label(bucket.get("window_minutes")), "used_percent": float(bucket["used_percent"]),
                            "resets_at": float(bucket["resets_at"]) if bucket.get("resets_at") else None})
    return {"windows": windows, "plan": limits.get("plan_type") or "", "observed_at": stamp}


def _claude_token(env: dict) -> str:
    config_dir = env.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        try:
            data = json.loads((Path(config_dir) / ".credentials.json").read_text())
            return str(data.get("claudeAiOauth", {}).get("accessToken") or "")
        except (OSError, ValueError):
            return ""
    try:
        raw = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return str(json.loads(raw).get("claudeAiOauth", {}).get("accessToken") or "")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""


def _pretty_bucket(key: str) -> str:
    return {"five_hour": "5h", "seven_day": "week"}.get(key, key.removeprefix("seven_day_").replace("_", " "))


def claude_usage(account: str, env: dict) -> dict:
    cached = _claude_cache.get(account)
    if cached and time.time() - cached[0] < CLAUDE_CACHE_SECONDS:
        return cached[1]
    token = _claude_token(env)
    if not token:
        return {"windows": [], "error": "Not logged in to Claude Code"}
    try:
        request = Request(CLAUDE_USAGE_URL, headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"})
        with urlopen(request, timeout=8) as response:
            data = json.loads(response.read())
    except Exception as exc:  # network or auth: report, never raise into the API
        return {"windows": [], "error": f"Usage unavailable ({type(exc).__name__})"}
    windows = []
    for key in ("five_hour", "seven_day", *[k for k in data if k.startswith("seven_day_") ]):
        bucket = data.get(key)
        if isinstance(bucket, dict) and bucket.get("utilization") is not None:
            resets = bucket.get("resets_at")
            windows.append({"label": _pretty_bucket(key), "used_percent": float(bucket["utilization"]),
                            "resets_at": _iso_to_epoch(resets) if resets else None})
    result = {"windows": windows, "observed_at": time.time()}
    _claude_cache[account] = (time.time(), result)
    return result


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


def usage_report(base: Path, limit: int = 2) -> dict:
    configured = accounts()
    chosen = [pair for pair in recent_accounts(base, limit) if pair[0] in configured and pair[1] in configured[pair[0]]]
    for backend in ("codex", "claude"):  # always have something to show
        if len(chosen) >= limit:
            break
        if backend in configured and (backend, "default") not in chosen:
            chosen.append((backend, "default"))
    rows = []
    for backend, account in chosen[:limit]:
        env = configured[backend][account].get("env", {})
        if backend == "codex":
            usage = codex_usage(Path(env.get("CODEX_HOME") or os.environ.get("CODEX_HOME") or user_home() / ".codex"))
        elif backend == "claude":
            usage = claude_usage(account, env)
        else:
            usage = {"windows": [], "error": "This CLI does not expose usage locally"}
        rows.append({"backend": backend, "account": account, "label": configured[backend][account].get("label", account), **usage})
    return {"accounts": rows, "fetched_at": time.time()}
