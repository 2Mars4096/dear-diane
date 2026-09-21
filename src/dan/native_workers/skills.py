"""DAN-managed skill pool: let one agent CLI use skills installed for another.

Codex, Claude Code and Cursor all use the Agent Skills format (a folder with SKILL.md).
DAN never copies or edits skills and never writes into a CLI's own skill directory or the
project: it keeps a pool of symlinks under graphs/skill_pool/<runtime>/ and hands that
directory to the CLI at launch (Claude Code loads .claude/skills from --add-dir paths).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .catalog import user_home

SETTINGS_FILE = "skill_pool.json"
# Runtimes DAN can currently hand a pool to at launch.
RECEIVERS = {"claude": ".claude/skills"}


def sources() -> dict[str, Path]:
    codex_home = Path(os.environ.get("CODEX_HOME") or user_home() / ".codex")
    return {"codex": codex_home / "skills", "claude": user_home() / ".claude/skills", "cursor": user_home() / ".cursor/skills"}


def _frontmatter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(errors="replace")[:8000]
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    block = text[3:].split("\n---", 1)[0]
    fields: dict[str, str] = {}
    for line in block.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() in {"name", "description"} and not line.startswith((" ", "\t")):
            fields[key.strip()] = value.strip().strip("\"'")
    return fields


def discover() -> list[dict]:
    """Every installed skill per CLI (the same name can exist for several CLIs)."""
    found: list[dict] = []
    for source, directory in sources().items():
        if not directory.is_dir():
            continue
        for skill in sorted(directory.iterdir()):
            manifest = skill / "SKILL.md"
            if skill.name.startswith(".") or not manifest.is_file():
                continue
            meta = _frontmatter(manifest)
            found.append({"name": meta.get("name") or skill.name, "folder": skill.name, "description": meta.get("description", "")[:400],
                          "source": source, "path": str(skill.resolve())})
    return sorted(found, key=lambda row: (row["source"], row["name"].lower()))


def read_settings(base: Path) -> dict:
    try:
        saved = json.loads((base / SETTINGS_FILE).read_text())
    except (OSError, ValueError):
        saved = {}
    excluded = [str(name) for name in saved.get("excluded", []) if isinstance(name, str)]
    return {"enabled": bool(saved.get("enabled", True)), "excluded": sorted(set(excluded))}


def write_settings(base: Path, enabled: bool, excluded: list[str]) -> dict:
    base.mkdir(parents=True, exist_ok=True)
    settings = {"enabled": bool(enabled), "excluded": sorted({str(name) for name in excluded})}
    temporary = base / (SETTINGS_FILE + ".tmp")
    temporary.write_text(json.dumps(settings))
    temporary.replace(base / SETTINGS_FILE)
    return settings


def shared_for(runtime: str, base: Path) -> list[dict]:
    """Skills from other CLIs that this runtime would receive."""
    settings = read_settings(base)
    if runtime not in RECEIVERS or not settings["enabled"]:
        return []
    skills = discover()
    native = {row["folder"] for row in skills if row["source"] == runtime} | {row["name"] for row in skills if row["source"] == runtime}
    shared: dict[str, dict] = {}
    for row in skills:  # first CLI to provide a name wins
        if row["source"] != runtime and row["name"] not in settings["excluded"] and row["name"] not in native and row["folder"] not in native:
            shared.setdefault(row["name"], row)
    return list(shared.values())


def build_pool(runtime: str, base: Path) -> Path | None:
    """Refresh the symlink pool for one runtime; returns the directory to pass to the CLI."""
    if runtime not in RECEIVERS:
        return None
    root = base / "skill_pool" / runtime
    links = root / RECEIVERS[runtime]
    wanted = {row["folder"]: row["path"] for row in shared_for(runtime, base)}
    if links.is_dir():
        for entry in links.iterdir():
            if entry.is_symlink() and (entry.name not in wanted or os.readlink(entry) != wanted[entry.name]):
                entry.unlink()
    if not wanted:
        return None
    links.mkdir(parents=True, exist_ok=True)
    for folder, target in wanted.items():
        link = links / folder
        if not link.exists() and not link.is_symlink():
            link.symlink_to(target, target_is_directory=True)
    return root


def report(base: Path) -> dict:
    settings = read_settings(base)
    skills = discover()
    receiving = {runtime: {(row["source"], row["name"]) for row in shared_for(runtime, base)} for runtime in RECEIVERS}
    return {"enabled": settings["enabled"], "excluded": settings["excluded"], "receivers": sorted(RECEIVERS),
            "skills": [{key: row[key] for key in ("name", "description", "source")} | {"shared_with": sorted(r for r, names in receiving.items() if (row["source"], row["name"]) in names)} for row in skills]}
