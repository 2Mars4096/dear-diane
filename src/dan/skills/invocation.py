"""Shared skill invocation contract for CLI, TUI, GUI, and agent runners."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


DEFAULT_SKILL_CONTENT_LIMIT = 3_500
DEFAULT_EXPLICIT_SKILL_REFERENCE_LIMIT = 4_000
DEFAULT_EXPLICIT_SKILL_REFERENCE_FILE_LIMIT = 4
_LEADING_SKILL_INVOCATION_RE = re.compile(r"^\s*\$([a-z][a-z0-9_-]{1,63})(?=\s|$)")
_SKILL_CACHE: dict[str, list[dict[str, Any]]] = {}


@dataclass(frozen=True)
class SkillInvocationParse:
    objective: str
    selected_tokens: tuple[str, ...] = ()
    unknown_tokens: tuple[str, ...] = ()
    should_run: bool = True
    message: str = ""


@dataclass(frozen=True)
class SkillPreflightResult:
    token: str
    ok: bool
    notes: tuple[str, ...] = ()
    ran_hook: bool = False


def skill_token(value: Mapping[str, Any]) -> str:
    skill_id = str(value.get("id") or "").strip()
    name = str(value.get("name") or "").strip()
    for candidate in (skill_id.replace("_", "-"), name.replace("_", "-")):
        token = re.sub(r"[^a-z0-9-]+", "-", candidate.lower()).strip("-")
        if token:
            return token
    return ""


def skill_lookup_keys(value: Any) -> set[str]:
    text = str(value or "").strip().lower().lstrip("$")
    if not text:
        return set()
    hyphen = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    compact = re.sub(r"[^a-z0-9]+", "", text)
    keys = {text, compact}
    if hyphen:
        keys.add(hyphen)
        keys.add(hyphen.replace("-", "_"))
    return {key for key in keys if key}


def coerce_skill_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,\s]+", value) if item.strip()]
    if isinstance(value, Mapping):
        return []
    if isinstance(value, Sequence):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def skill_cache_key(workspace_root: str | Path) -> str:
    return str(Path(workspace_root or ".").expanduser().resolve(strict=False))


def load_skill_catalog(workspace_root: str | Path) -> list[dict[str, Any]]:
    """Load DAN, Codex, Claude, and Cursor skills as shared invocation packets."""

    cache_key = skill_cache_key(workspace_root)
    if cache_key in _SKILL_CACHE:
        return [dict(item) for item in _SKILL_CACHE[cache_key]]

    catalog: dict[str, dict[str, Any]] = {}
    try:
        from dan.server.skill_store import SkillStore, default_external_skill_dirs

        root = Path(workspace_root).expanduser() if workspace_root else Path.cwd()
        project_skill_dir = root / ".dan" / "skills"
        store = SkillStore(
            project_dir=project_skill_dir if project_skill_dir.is_dir() else None,
            extra_dirs=default_external_skill_dirs(),
        )
        store.scan()
        for desc in store.list_skills():
            if not desc.enabled:
                continue
            skill_id = desc.skill_id or str(desc.name).lower().replace("-", "_")
            content = str(desc.content or "").strip()
            if not content:
                continue
            catalog[skill_id] = {
                "id": skill_id,
                "name": desc.name,
                "description": desc.description,
                "tags": list(desc.tags),
                "source_path": str(desc.source_path or ""),
                "source_scope": desc.scope,
                "content": content,
            }
    except Exception:
        pass

    try:
        from dan.server.skill_library import SKILL_LIBRARY

        for key, entry in SKILL_LIBRARY.items():
            if key in catalog:
                continue
            content = str(entry.get("text") or "").strip()
            if not content:
                continue
            catalog[key] = {
                "id": key,
                "name": str(entry.get("name") or key),
                "description": str(entry.get("description") or ""),
                "tags": list(entry.get("tags") or []),
                "source_path": "builtin:dan.server.skill_library",
                "source_scope": "builtin",
                "content": content,
            }
    except Exception:
        pass

    values = list(catalog.values())
    _SKILL_CACHE[cache_key] = [dict(item) for item in values]
    return values


def catalog_by_token(catalog: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    by_token: dict[str, dict[str, Any]] = {}
    for item in catalog:
        token = skill_token(item)
        if token and token not in by_token:
            by_token[token] = dict(item)
    return by_token


def parse_skill_invocation_text(
    text: str,
    *,
    catalog: Sequence[Mapping[str, Any]] | None = None,
    workspace_root: str | Path | None = None,
    browse_hint: str = "Pick a full skill name",
) -> SkillInvocationParse:
    """Parse leading ``$skill-name`` syntax without treating prices/env vars as skills."""

    original = str(text or "").strip()
    remaining = original
    resolved_catalog = list(catalog) if catalog is not None else load_skill_catalog(workspace_root or ".")
    by_token = catalog_by_token(resolved_catalog)
    selected: list[str] = []
    unknown: list[str] = []
    saw_invocation = False

    while True:
        match = _LEADING_SKILL_INVOCATION_RE.match(remaining)
        if not match:
            break
        token = match.group(1).lower()
        skill = by_token.get(token)
        prefix_matches = [candidate for candidate in sorted(by_token) if candidate.startswith(token)]
        if skill is None and not prefix_matches and "-" not in token and "_" not in token:
            break
        saw_invocation = True
        if skill is None:
            if prefix_matches:
                rendered = ", ".join(f"${candidate}" for candidate in prefix_matches[:8])
                suffix = " ..." if len(prefix_matches) > 8 else ""
                return SkillInvocationParse(
                    objective=remaining,
                    should_run=False,
                    message=(
                        f"Ambiguous skill mention: ${token}. Matches: {rendered}{suffix}. "
                        f"{browse_hint}; no Super DAN run was started."
                    ),
                )
            unknown.append(token)
        elif token not in selected:
            selected.append(token)
        remaining = remaining[match.end() :].strip()

    if not saw_invocation:
        return SkillInvocationParse(objective=original)

    labels = ", ".join(f"${token}" for token in selected)
    if unknown:
        bad = ", ".join(f"${token}" for token in unknown)
        return SkillInvocationParse(
            objective=remaining,
            selected_tokens=tuple(selected),
            unknown_tokens=tuple(unknown),
            should_run=False,
            message=f"Unknown skill mention: {bad}. {browse_hint}; no Super DAN run was started.",
        )
    if not remaining:
        return SkillInvocationParse(
            objective="",
            selected_tokens=tuple(selected),
            should_run=False,
            message=f"Selected skill: {labels}. Add objective text after the mention to run with it.",
        )
    return SkillInvocationParse(
        objective=remaining,
        selected_tokens=tuple(selected),
        message=f"Selected skill: {labels}; objective: {remaining}",
    )


def prepare_skill_invocation_args(
    args: Any,
    *,
    catalog: Sequence[Mapping[str, Any]] | None = None,
    workspace_root: str | Path | None = None,
    source: str = "operator",
    browse_hint: str = "Pick a full skill name",
) -> SkillInvocationParse:
    parsed = parse_skill_invocation_text(
        str(getattr(args, "target", "") or ""),
        catalog=catalog,
        workspace_root=workspace_root or getattr(args, "workspace", "."),
        browse_hint=browse_hint,
    )
    if parsed.selected_tokens:
        selected = list(parsed.selected_tokens)
        setattr(args, "_selected_skill_mentions", selected)
        setattr(args, "selected_skill_mentions", selected)
        setattr(args, "_selected_skill_source", source)
    if parsed.should_run:
        args.target = parsed.objective
    return parsed


def selected_skill_mentions_from_args(args: Any | None) -> list[str]:
    if args is None:
        return []
    selected: list[str] = []
    seen: set[str] = set()
    for attr in (
        "_selected_skill_mentions",
        "selected_skill_mentions",
        "selected_skill_ids",
        "_tui_selected_skill_mentions",
    ):
        raw = getattr(args, attr, None)
        for value in coerce_skill_values(raw):
            normalized = value.lower().lstrip("$")
            if normalized and normalized not in seen:
                selected.append(normalized)
                seen.add(normalized)
    return selected


def selected_skill_preflight_notes_from_args(args: Any | None) -> list[str]:
    if args is None:
        return []
    notes: list[str] = []
    seen: set[str] = set()
    for attr in (
        "_selected_skill_preflight_notes",
        "selected_skill_preflight",
        "_tui_skill_preflight_notes",
    ):
        for item in getattr(args, attr, None) or []:
            text = str(item).strip()
            if text and text not in seen:
                notes.append(text)
                seen.add(text)
    return notes


def selected_catalog_items(
    catalog: Sequence[Mapping[str, Any]],
    tokens: Sequence[str],
) -> list[dict[str, Any]]:
    wanted = {str(token or "").strip().lower().lstrip("$") for token in tokens}
    wanted.discard("")
    if not wanted:
        return []
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in catalog:
        token = skill_token(item)
        if token in wanted and token not in seen:
            selected.append(dict(item))
            seen.add(token)
    return selected


def select_explicit_skill_items(
    requested: Sequence[str],
    catalog: Sequence[Mapping[str, Any]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    if not requested:
        return []
    lookup: dict[str, Mapping[str, Any]] = {}
    for skill in catalog:
        keys: set[str] = set()
        keys.update(skill_lookup_keys(skill.get("id")))
        keys.update(skill_lookup_keys(skill.get("name")))
        for key in keys:
            lookup.setdefault(key, skill)

    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for value in requested:
        skill = next((lookup.get(key) for key in skill_lookup_keys(value) if lookup.get(key)), None)
        if skill is None:
            continue
        skill_id = str(skill.get("id") or skill.get("name") or "")
        if not skill_id or skill_id in seen_ids:
            continue
        item = dict(skill)
        item["match_score"] = "explicit"
        item["match_reason"] = "explicit_skill_mention"
        selected.append(item)
        seen_ids.add(skill_id)
        if len(selected) >= limit:
            break
    return selected


def skill_dir(skill: Mapping[str, Any]) -> Path | None:
    source_path = str(skill.get("source_path") or "").strip()
    if not source_path or source_path.startswith("builtin:"):
        return None
    try:
        path = Path(source_path).expanduser()
        if path.is_file():
            return path.parent
    except OSError:
        return None
    return None


def explicit_skill_reference_excerpt(
    skill: Mapping[str, Any],
    *,
    char_limit: int = DEFAULT_EXPLICIT_SKILL_REFERENCE_LIMIT,
    file_limit: int = DEFAULT_EXPLICIT_SKILL_REFERENCE_FILE_LIMIT,
) -> str:
    if skill.get("match_reason") != "explicit_skill_mention":
        return ""
    directory = skill_dir(skill)
    if directory is None:
        return ""
    references_dir = directory / "references"
    if not references_dir.is_dir():
        return ""
    blocks: list[str] = []
    remaining = char_limit
    for path in sorted(references_dir.glob("*.md"))[:file_limit]:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not text or remaining <= 0:
            break
        excerpt = text[:remaining].rstrip()
        remaining -= len(excerpt)
        truncated = " [truncated]" if len(text) > len(excerpt) else ""
        blocks.append(f"Reference excerpt: references/{path.name}{truncated}\n{excerpt}")
    if not blocks:
        return ""
    return "\n\nSelected skill companion reference excerpts:\n" + "\n\n".join(blocks)


def render_skill_packet(
    skill: Mapping[str, Any],
    *,
    content_limit: int = DEFAULT_SKILL_CONTENT_LIMIT,
    reference_limit: int = DEFAULT_EXPLICIT_SKILL_REFERENCE_LIMIT,
    reference_file_limit: int = DEFAULT_EXPLICIT_SKILL_REFERENCE_FILE_LIMIT,
    sha256_prefix: Callable[[str], str] | None = None,
) -> str:
    content = str(skill.get("content") or "").strip()
    truncated = False
    if len(content) > content_limit:
        content = content[:content_limit].rstrip()
        truncated = True
    digest = sha256_prefix(str(skill.get("content") or "")) if sha256_prefix else ""
    meta = {
        "id": skill.get("id"),
        "name": skill.get("name"),
        "description": skill.get("description"),
        "source": skill.get("source_path"),
        "source_scope": skill.get("source_scope"),
        "match_reason": skill.get("match_reason"),
        "content_sha256": digest,
        "truncated": truncated,
    }
    if skill.get("match_reason") == "explicit_skill_mention":
        meta["invocation"] = "explicit_operator_selection"
        directory = skill_dir(skill)
        if directory is not None:
            meta["skill_dir"] = str(directory)
    import json

    return (
        "Active DAN skill packet:\n"
        + json.dumps(meta, ensure_ascii=False, sort_keys=True)
        + "\nInstructions:\n"
        + content
        + explicit_skill_reference_excerpt(
            skill,
            char_limit=reference_limit,
            file_limit=reference_file_limit,
        )
    )


def explicit_skill_constraints(selected: Sequence[Mapping[str, Any]]) -> list[str]:
    explicit = [
        skill
        for skill in selected
        if skill.get("match_reason") == "explicit_skill_mention" or skill.get("match_score") == "explicit"
    ]
    if not explicit:
        return []
    labels = ", ".join(
        f"${str(skill.get('name') or skill.get('id') or '').replace('_', '-')}"
        for skill in explicit
        if str(skill.get("name") or skill.get("id") or "").strip()
    )
    return [
        (
            f"The operator explicitly selected these DAN skills: {labels}. "
            "Treat their skill-packet instructions as required execution guidance for this run, "
            "not optional background."
        ),
        (
            "Materially apply each explicitly selected skill to workspace files when the objective asks for artifacts; "
            "do not merely mention the skill in the final answer."
        ),
        (
            "If an explicitly selected skill is a scaffold skill, create or update the concrete tracking files, "
            "numbered work files, dated plans, and output folders named by the skill unless existing project "
            "conventions require a narrower compatible update."
        ),
    ]


def selected_skill_script_candidates(skill: Mapping[str, Any]) -> list[str]:
    token = skill_token(skill)
    skill_id = str(skill.get("id") or token).strip().lower().replace("-", "_")
    token_id = token.replace("-", "_")
    names = [
        "dan_preflight.py",
        "preflight.py",
        "activate.py",
        "skill_preflight.py",
        f"{skill_id}_preflight.py",
        f"{token_id}_preflight.py",
        f"{skill_id}_docs.py",
        f"{token_id}_docs.py",
        f"{skill_id}_scaffold.py",
        f"{token_id}_scaffold.py",
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name and name not in seen:
            deduped.append(name)
            seen.add(name)
    return deduped


def selected_skill_preflight_script(skill: Mapping[str, Any]) -> Path | None:
    directory = skill_dir(skill)
    if directory is None:
        return None
    scripts_dir = directory / "scripts"
    for name in selected_skill_script_candidates(skill):
        script = scripts_dir / name
        if script.is_file() and script.suffix == ".py":
            return script
    return None


def _clip(value: Any, *, limit: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def run_skill_preflight(
    *,
    workspace_root: Path,
    skill: Mapping[str, Any],
    objective: str,
) -> SkillPreflightResult:
    token = skill_token(skill) or str(skill.get("name") or skill.get("id") or "skill")
    script = selected_skill_preflight_script(skill)
    if script is None:
        return SkillPreflightResult(
            token=token,
            ok=True,
            notes=(f"skill activated: ${token} (prompt contract)",),
        )
    command = [
        sys.executable,
        str(script),
        str(workspace_root),
    ]
    env = {
        **dict(os.environ),
        "DAN_SELECTED_SKILL": token,
        "DAN_SKILL_TOKEN": token,
        "DAN_SKILL_SOURCE": str(skill.get("source_path") or ""),
        "DAN_SKILL_OBJECTIVE": str(objective or ""),
        "DAN_WORKSPACE_ROOT": str(workspace_root),
    }
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SkillPreflightResult(
            token=token,
            ok=False,
            notes=(f"skill preflight failed: ${token}: {_clip(exc)}",),
            ran_hook=True,
        )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if str(part or "").strip())
    summary = next(
        (line.strip() for line in output.splitlines() if line.strip().startswith("summary:")),
        _clip(output),
    )
    if completed.returncode != 0:
        return SkillPreflightResult(
            token=token,
            ok=False,
            notes=(f"skill preflight failed: ${token} exit={completed.returncode}: {_clip(summary)}",),
            ran_hook=True,
        )
    return SkillPreflightResult(
        token=token,
        ok=True,
        notes=(f"skill preflight applied: ${token} {summary}".strip(),),
        ran_hook=True,
    )


def run_skill_preflights_for_tokens(
    *,
    workspace_root: Path,
    tokens: Sequence[str],
    objective: str,
    catalog: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[bool, list[str]]:
    if not tokens:
        return True, []
    resolved_catalog = list(catalog) if catalog is not None else load_skill_catalog(workspace_root)
    notes: list[str] = []
    ok = True
    for skill in selected_catalog_items(resolved_catalog, tokens):
        result = run_skill_preflight(
            workspace_root=workspace_root,
            skill=skill,
            objective=objective,
        )
        notes.extend(result.notes)
        ok = ok and result.ok
    return ok, notes
