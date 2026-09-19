"""Read local runtime metadata without exposing credentials to the browser."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib

RUNTIMES = {"codex": "Codex", "claude": "Claude Code", "antigravity": "Antigravity"}


def user_home() -> Path:
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except ImportError:
        return Path.home()


def user_path(value: str) -> Path:
    return user_home() / value[2:] if value.startswith("~/") else Path(value)


def accounts() -> dict[str, dict[str, dict]]:
    result = {runtime: {"default": {"label": "Current CLI account", "env": {}}} for runtime in RUNTIMES}
    result["codex"]["default"]["env"] = {"CODEX_HOME": os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))}
    result["claude"]["default"]["env"] = {"CLAUDE_CONFIG_DIR": os.environ.get("CLAUDE_CONFIG_DIR", str(user_home() / ".claude"))}
    if (user_home() / ".codex").is_dir():
        result["codex"]["personal"] = {"label": "Personal Codex", "env": {"CODEX_HOME": str(user_home() / ".codex")}, "home": user_home() / ".codex"}
    config_path = Path(os.environ.get("DAN_CODEXX_CONFIG", str(user_home() / ".config/codexx/config.toml")))
    if config_path.is_file():
        config = tomllib.loads(config_path.read_text())
        directory = user_path(config.get("accounts_dir", str(user_home() / ".codex-accounts")))
        for name, item in config.get("accounts", {}).items():
            account = str(item.get("account") or name)
            if not account or Path(account).name != account or account in {".", ".."}:
                continue
            home = directory / account / ".codex"
            if home.is_dir():
                result["codex"][name] = {"label": name, "env": {**item.get("env", {}), "CODEX_HOME": str(home)}, "home": home}
    directory = user_home() / ".codex-accounts"
    if directory.is_dir():
        for profile in directory.iterdir():
            home = profile / ".codex"
            if home.is_dir() and (home / "auth.json").is_file() and profile.name not in result["codex"]:
                result["codex"][profile.name] = {"label": profile.name, "env": {"CODEX_HOME": str(home)}, "home": home}
    # Optional named Claude profiles use their own supported config directory.
    directory = Path(os.environ.get("DAN_CLAUDE_ACCOUNTS_DIR", str(user_home() / ".claude-accounts")))
    if directory.is_dir():
        for profile in sorted(directory.iterdir()):
            if profile.is_dir():
                home = profile / ".claude" if (profile / ".claude").is_dir() else profile
                result["claude"][profile.name] = {"label": profile.name, "env": {"CLAUDE_CONFIG_DIR": str(home)}}
    return result


def binary(runtime: str) -> str | None:
    name = "agy" if runtime == "antigravity" else runtime
    configured = os.environ.get(f"DAN_{runtime.upper()}_BIN")
    if configured:
        return shutil.which(configured)
    found = shutil.which(name)
    if found:
        return found
    for directory in [user_home() / ".local/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")]:
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def catalog() -> dict:
    configured = accounts()
    result = []
    for runtime, label in RUNTIMES.items():
        executable = binary(runtime)
        models = []
        if runtime == "codex":
            homes = [Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))]
            homes += [entry["home"] for entry in configured[runtime].values() if "home" in entry]
            for home in homes:
                try:
                    data = json.loads((home / "models_cache.json").read_text())
                    for model in data.get("models", []):
                        slug = model.get("slug")
                        if slug and slug not in models:
                            models.append(slug)
                except (OSError, ValueError, TypeError):
                    pass
        elif runtime == "claude":
            models = ["sonnet", "opus", "haiku"]
        version = ""
        if executable:
            try:
                version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=3).stdout.strip()[:100]
            except (OSError, subprocess.TimeoutExpired):
                pass
        # Installed Claude versions before 2.1.205 do not support print-mode fast settings.
        import re
        parsed = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
        claude_fast = bool(parsed and tuple(map(int, parsed.groups())) >= (2, 1, 205))
        result.append({"id": runtime, "label": label, "available": bool(executable), "version": version,
                       "accounts": [{"id": key, "label": value["label"]} for key, value in configured[runtime].items()],
                       "models": models, "efforts": ["low", "medium", "high"] + (["xhigh", "max", "ultra"] if runtime == "codex" else []),
                       "fast": runtime == "codex" or (runtime == "claude" and claude_fast),
                       "setup": "Install agy and sign in with agy." if not executable and runtime == "antigravity" else ""})
    result.insert(0, {"id": "dan", "label": "DAN", "available": True, "version": "",
                      "accounts": [{"id": "default", "label": "DAN configuration"}],
                      "models": ["deepseek/deepseek-v4.1-flash", "moonshotai/kimi-k2.6"],
                      "efforts": [], "fast": False, "setup": ""})
    from dan.cli import resolve_config
    configured = resolve_config()
    openrouter_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("DAN_OPENROUTER_API_KEY")
    if str(configured.get("base_url") or "").rstrip("/") == "https://openrouter.ai/api/v1":
        openrouter_key = openrouter_key or configured.get("api_key")
    return {"runtimes": result, "openrouter_configured": bool(openrouter_key)}


# DAN mode -> native setting. Plan reads and proposes; Auto edits/runs inside the
# project sandbox; Full removes sandbox and approvals. Antigravity keeps its CLI default.
PERMISSIONS = {"plan", "auto", "full"}


def launch(runtime: str, profile: dict, objective: str, workspace: str, session: str = "") -> tuple[list[str], dict[str, str]]:
    if runtime not in RUNTIMES:
        raise ValueError("Unknown native runtime")
    executable = binary(runtime)
    if not executable:
        raise ValueError(f"{RUNTIMES[runtime]} CLI is not installed")
    account = accounts()[runtime].get(profile.get("account") or "default")
    if account is None:
        raise ValueError("Selected account is no longer configured")
    env = dict(os.environ)
    env.update(account["env"])
    env.pop("CLAUDECODE", None)
    env["PATH"] = os.pathsep.join([str(user_home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin", env.get("PATH", "/usr/bin:/bin")])
    model, effort = str(profile.get("model") or ""), str(profile.get("effort") or "")
    fast = bool(profile.get("fast"))
    permission = str(profile.get("permission") or "auto")
    if permission not in PERMISSIONS:
        raise ValueError("Unsupported permission mode")
    allowed = {"low", "medium", "high"} | ({"xhigh", "max", "ultra"} if runtime == "codex" else set())
    if effort and effort not in allowed:
        raise ValueError("Unsupported reasoning effort")
    if runtime == "codex":
        cmd = [executable, "exec"] + (["resume", session] if session else [])
        cmd += ["--json", "--skip-git-repo-check"]
        if not session:
            cmd += ["--cd", workspace]
        # `exec resume` has no --sandbox flag, so set it through config on every turn.
        if permission == "full":
            cmd += ["--dangerously-bypass-approvals-and-sandbox"]
        else:
            cmd += ["-c", f'sandbox_mode="{"read-only" if permission == "plan" else "workspace-write"}"']
        if effort:
            cmd += ["-c", f'model_reasoning_effort="{effort}"']
        cmd += ["-c", 'service_tier="fast"' if fast else 'service_tier="default"']
        if fast:
            cmd += ["-c", "features.fast_mode=true"]
    else:
        cmd = [executable, "-p", objective, "--output-format", "stream-json"]
        if runtime == "claude":
            cmd += ["--verbose", "--include-partial-messages"]
            cmd += ["--dangerously-skip-permissions"] if permission == "full" else ["--permission-mode", "plan" if permission == "plan" else "acceptEdits"]
            if fast and not any(row["fast"] for row in catalog()["runtimes"] if row["id"] == runtime):
                raise ValueError("Update Claude Code to use fast mode in headless workers")
            if fast and model not in {"opus", "claude-opus-5", "claude-opus-4-8"}:
                raise ValueError("Claude fast mode requires a supported Opus model")
            settings: dict = {"fastMode": fast}
            if permission == "auto":
                # Headless runs cannot prompt; let Bash run inside Claude's workspace sandbox instead.
                settings["sandbox"] = {"enabled": True, "autoAllowBashIfSandboxed": True}
            cmd += ["--settings", json.dumps(settings)]
        elif fast:
            raise ValueError("Antigravity has no supported fast-mode switch")
        if effort:
            cmd += ["--effort", effort]
        if session:
            cmd += ["--resume" if runtime == "claude" else "--conversation", session]
    if model:
        cmd += ["--model", model]
    if runtime == "codex":
        cmd += ["--", objective]
    return cmd, env
