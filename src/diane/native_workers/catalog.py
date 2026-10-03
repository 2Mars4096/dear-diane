"""Read local runtime metadata without exposing credentials to the browser."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib

from .models import API_PROVIDERS, provider_key, openrouter_key, source_catalog, validate_model_source

RUNTIMES = {"codex": "Codex", "claude": "Claude Code", "antigravity": "Antigravity", "cursor": "Cursor"}


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
    # Claude Code keys its keychain credentials by config dir: setting CLAUDE_CONFIG_DIR
    # explicitly (even to ~/.claude) makes a claude.ai login look logged out. Only
    # forward a value the user already set; named profiles set their own.
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        result["claude"]["default"]["env"] = {"CLAUDE_CONFIG_DIR": os.environ["CLAUDE_CONFIG_DIR"]}
    # "personal" is only distinct when CODEX_HOME points somewhere other than ~/.codex.
    if (user_home() / ".codex").is_dir() and Path(result["codex"]["default"]["env"]["CODEX_HOME"]).expanduser().resolve() != (user_home() / ".codex").resolve():
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
    configured = os.environ.get(f"DAN_{runtime.upper()}_BIN")
    if configured:
        return shutil.which(configured)
    if runtime == "cursor":
        # Cursor's CLI installs as `agent` (older releases: `cursor-agent`). `agent` is too
        # generic to trust by name alone, so it must resolve into Cursor's install.
        for name in ("cursor-agent", "agent"):
            for candidate in [shutil.which(name), str(user_home() / ".local/bin" / name)]:
                if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK) and \
                        (name == "cursor-agent" or "cursor-agent" in Path(os.path.realpath(candidate)).parts
                         or Path(os.path.realpath(candidate)).name == "cursor-agent"):
                    return candidate
        return None
    name = "agy" if runtime == "antigravity" else runtime
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
        model_efforts: dict[str, list[str]] = {}
        model_labels: dict[str, str] = {}  # names as each CLI's own model picker shows them
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
                            if model.get("display_name"):
                                model_labels[slug] = str(model["display_name"])
                            levels = [str(level.get("effort")) for level in model.get("supported_reasoning_levels") or [] if isinstance(level, dict) and level.get("effort")]
                            if levels:
                                model_efforts[slug] = levels
                except (OSError, ValueError, TypeError):
                    pass
        elif runtime == "claude":
            # Full IDs only: older CLIs lack a `fable` alias, and one entry per model keeps the menu unified.
            models = ["claude-fable-5-1", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"]
            model_labels = {"claude-fable-5-1": "Fable 5.1", "claude-opus-5": "Opus 5", "claude-sonnet-5": "Sonnet 5", "claude-haiku-4-5-20251001": "Haiku 4.5"}
            model_efforts = {model: ["low", "medium", "high", "max"] for model in models}
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
                       "models": models, "model_efforts": model_efforts, "model_labels": model_labels,
                       # Codex levels come from its model cache per model; Claude's --effort passes max through to the API.
                       "efforts": [] if runtime == "cursor" else ["low", "medium", "high"] + sorted({level for levels in model_efforts.values() for level in levels} - {"low", "medium", "high"}, key=["minimal", "xhigh", "max", "ultra"].index),
                       "fast": runtime == "codex" or (runtime == "claude" and claude_fast),
                       "setup": "" if executable else {"antigravity": "Install agy and sign in with agy.",
                                                        "cursor": "Install the Cursor CLI (curl https://cursor.com/install -fsS | bash), then run agent login."}.get(runtime, "")})
    result.insert(0, {"id": "dan", "label": "Default", "available": True, "version": "",
                      "accounts": [{"id": "default", "label": "Default configuration"}],
                      "models": [],
                      "efforts": [], "fast": False, "setup": ""})
    for runtime in result:
        runtime["sources"] = source_catalog(runtime["id"])
    return {"runtimes": result, "openrouter_configured": bool(openrouter_key())}


# Diane mode -> native setting. Plan reads and proposes; Auto edits/runs inside the
# project sandbox; Full removes sandbox and approvals. Antigravity keeps its CLI default.
PERMISSIONS = {"plan", "auto", "full"}


def launch(runtime: str, profile: dict, objective: str, workspace: str, session: str = "") -> tuple[list[str], dict[str, str]]:
    if runtime not in RUNTIMES:
        raise ValueError("Unknown native runtime")
    source = validate_model_source(runtime, profile)
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
    allowed = {"low", "medium", "high"} | ({"minimal", "xhigh", "max", "ultra"} if runtime == "codex" else {"max"} if runtime == "claude" else set())
    if effort and effort not in allowed:
        raise ValueError("Unsupported reasoning effort")
    if runtime == "cursor":
        if effort:
            raise ValueError("Cursor has no reasoning-effort setting")
        if fast:
            raise ValueError("Cursor has no supported fast-mode switch")
        cmd = [executable, "-p", "--output-format", "stream-json", "--workspace", workspace]
        # Plan -> read-only plan mode; Auto -> sandboxed with commands allowed; Full -> no sandbox.
        cmd += {"plan": ["--mode", "plan"], "auto": ["--sandbox", "enabled", "--force"], "full": ["--sandbox", "disabled", "--force"]}[permission]
        if model:
            cmd += ["--model", model]
        if session:
            cmd += ["--resume", session]
        return cmd + [objective], env
    if runtime == "codex":
        from .codex_home import prepare_home
        home = prepare_home(env.get("CODEX_HOME", str(user_home() / ".codex")))
        env["CODEX_HOME"] = str(home)
        env["CODEX_SQLITE_HOME"] = str(home)
        cmd = [executable, "exec"] + (["resume", session] if session else [])
        cmd += ["--json", "--skip-git-repo-check"]
        # Explicit overrides also defeat a source config pointing to desktop state.
        cmd += ["-c", f"sqlite_home={json.dumps(str(home))}", "-c", f"log_dir={json.dumps(str(home / 'log'))}", "-c", 'cli_auth_credentials_store="file"']
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
        if source != "native":
            spec = API_PROVIDERS[source]
            key_env = f"DAN_CODEX_{source.upper()}_KEY"
            env[key_env] = provider_key(source)
            # A process-local provider; never rewrite the user's CLI config.
            provider = '{name=' + json.dumps(spec["label"]) + ',base_url=' + json.dumps(spec["url"]) + ',wire_api="responses",env_key=' + json.dumps(key_env) + ',requires_openai_auth=false}'
            cmd += ["-c", f'model_provider="dan_{source}"', "-c", f"model_providers.dan_{source}={provider}"]
            if source == "moonshot" and model == "kimi-k3":
                cmd += ["-c", "model_context_window=1048576"]
            if effort:
                cmd += ["-c", "model_supports_reasoning_summaries=true"]
        elif profile.get("provider") == "native":
            cmd += ["-c", 'model_provider="openai"']
    else:
        cmd = [executable, "-p", objective, "--output-format", "stream-json"]
        if runtime == "claude":
            if source != "native":
                from diane.server.paths import resolve_graphs_dir
                # Isolate gateway login from cached subscription/keychain credentials.
                import hashlib
                account_id = hashlib.sha256(str(profile.get("account") or "default").encode()).hexdigest()[:16]
                config_dir = Path(resolve_graphs_dir()) / "model_accounts" / f"claude_{source}" / account_id
                config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                env.update(CLAUDE_CONFIG_DIR=str(config_dir), ANTHROPIC_BASE_URL=API_PROVIDERS[source]["anthropic_url"],
                           ANTHROPIC_AUTH_TOKEN=provider_key(source), ANTHROPIC_MODEL=model, ANTHROPIC_API_KEY="",
                           CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
                for key in ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_EFFORT_LEVEL", "ANTHROPIC_SMALL_FAST_MODEL"):
                    env.pop(key, None)
                for role in ("FABLE", "OPUS", "SONNET", "HAIKU"):
                    env[f"ANTHROPIC_DEFAULT_{role}_MODEL"] = model
                env["CLAUDE_CODE_SUBAGENT_MODEL"] = model
            cmd += ["--verbose", "--include-partial-messages"]
            cmd += ["--dangerously-skip-permissions"] if permission == "full" else ["--permission-mode", "plan" if permission == "plan" else "acceptEdits"]
            if fast and not any(row["fast"] for row in catalog()["runtimes"] if row["id"] == runtime):
                raise ValueError("Update Claude Code to use fast mode in headless workers")
            if fast and model not in {"opus", "claude-opus-5", "claude-opus-4-8", "claude-fable-5-1"}:
                raise ValueError("Claude fast mode requires a supported Opus model")
            settings: dict = {"fastMode": fast}
            if permission == "auto":
                # Headless runs cannot prompt; let Bash run inside Claude's workspace sandbox instead.
                settings["sandbox"] = {"enabled": True, "autoAllowBashIfSandboxed": True}
            cmd += ["--settings", json.dumps(settings)]
            # Skills installed for other CLIs, exposed through Diane's symlink pool (never ~/.claude or the project).
            try:
                from diane.server.paths import resolve_graphs_dir
                from .skills import build_pool
                pool = build_pool("claude", Path(resolve_graphs_dir()))
            except Exception:  # skills are optional; never block a run
                pool = None
            if pool:
                cmd += ["--add-dir", str(pool)]
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
