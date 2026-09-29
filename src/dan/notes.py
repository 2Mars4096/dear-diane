"""Shared Hugo notes workspace conventions for Diane surfaces."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

DEFAULT_NOTES_WORKSPACE_ROOT = "~/.codex/memories"
DEFAULT_CONTENT_PROJECT_NAME = "my-knowledge-base"
CONTENT_ROOTS_ENV_VAR = "DAN_DEFAULT_CONTENT_ROOTS"
NOTES_ROOT_ENV_VARS: tuple[str, ...] = ("DAN_NOTES_WORKSPACE_ROOT", "DAN_NOTES_ROOT")
HUGO_NOTES_CAPABILITIES: tuple[str, ...] = (
    "notes",
    "hugo_notes",
    "markdown_notes",
    "markdown_preview",
    "page_id_citations",
)
HUGO_NOTES_RULES: tuple[str, ...] = (
    "Treat the notes root as a Hugo content tree, not a scratch folder.",
    "Use bundle pages such as folder/index.md or index.mdx when creating durable notes.",
    "Preserve existing front matter and fields such as title, date, lastmod, pageID, tags, categories, aliases, images, math, toc, and draft.",
    "Use pageID and @pageID citations for cross-note references when they are available.",
    "Preview display may strip front matter, but edits must preserve it unless the user asks to change metadata.",
    "Keep note reads, writes, and moves inside the configured notes root.",
)
HUGO_NOTES_WRITE_POLICY = (
    "Use notes as available context by default; create or edit them only when the operator asks for notes, "
    "memory, documentation, or a saved artifact."
)


def expand_workspace_home(raw: str) -> Path:
    return Path(raw).expanduser()


def content_bootstrap_root_candidates(
    *,
    workspace_root: str | Path | None = None,
    cwd: str | Path | None = None,
) -> list[Path]:
    """Return likely Hugo content projects in the same order the desktop uses."""

    configured = [
        expand_workspace_home(value)
        for value in str(os.environ.get(CONTENT_ROOTS_ENV_VAR) or "").split(os.pathsep)
        if value.strip()
    ]
    home = Path.home()
    local_named = [
        Path("/Volumes/data/Dropbox/Projects") / DEFAULT_CONTENT_PROJECT_NAME,
        home / "Dropbox" / "Projects" / DEFAULT_CONTENT_PROJECT_NAME,
        home / "Projects" / DEFAULT_CONTENT_PROJECT_NAME,
    ]

    base_candidates: list[Path] = []
    if cwd is not None:
        base_candidates.append(Path(cwd))
    else:
        base_candidates.append(Path.cwd())
    if workspace_root is not None:
        base_candidates.append(Path(workspace_root))

    heuristic: list[Path] = []
    for base in base_candidates:
        try:
            resolved = base.expanduser().resolve()
        except OSError:
            resolved = base.expanduser()
        heuristic.extend(
            [
                resolved.parent / DEFAULT_CONTENT_PROJECT_NAME,
                resolved.parent.parent / DEFAULT_CONTENT_PROJECT_NAME,
            ],
        )

    seen: set[str] = set()
    candidates: list[Path] = []
    for candidate in [*configured, *local_named, *heuristic]:
        normalized = str(candidate.expanduser())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(candidate.expanduser())
    return candidates


def notes_root_from_content_project(project_root: Path) -> Path:
    """Resolve a Hugo project into the content tree Diane should expose as notes."""

    content_root = project_root / "content"
    if content_root.exists() and content_root.is_dir():
        return content_root
    content_notes = project_root / "content" / "notes"
    if content_notes.exists() and content_notes.is_dir():
        return content_notes
    return project_root


def resolve_workspace_notes_root(
    *,
    workspace_root: str | Path | None = None,
    cwd: str | Path | None = None,
) -> tuple[Path, str]:
    """Resolve the default notes root and explain where it came from."""

    for env_name in NOTES_ROOT_ENV_VARS:
        raw = str(os.environ.get(env_name) or "").strip()
        if raw:
            return _safe_resolve(expand_workspace_home(raw)), f"env:{env_name}"

    for candidate in content_bootstrap_root_candidates(workspace_root=workspace_root, cwd=cwd):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.exists() and resolved.is_dir():
            return _safe_resolve(notes_root_from_content_project(resolved)), "content_bootstrap"

    return _safe_resolve(Path(DEFAULT_NOTES_WORKSPACE_ROOT).expanduser()), "fallback"


def default_workspace_notes_root(
    *,
    workspace_root: str | Path | None = None,
    cwd: str | Path | None = None,
) -> Path:
    root, _source = resolve_workspace_notes_root(workspace_root=workspace_root, cwd=cwd)
    return root


def workspace_notes_root(
    *,
    workspace_root: str | Path | None = None,
    cwd: str | Path | None = None,
    create: bool = True,
) -> Path:
    root, _source = resolve_workspace_notes_root(workspace_root=workspace_root, cwd=cwd)
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def hugo_notes_feature_descriptor(
    *,
    notes_root: str | Path | None = None,
    active_note: Mapping[str, Any] | None = None,
    workspace_root: str | Path | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    if notes_root:
        root = _safe_resolve(expand_workspace_home(str(notes_root)))
        root_source = "surface_context"
    else:
        root, root_source = resolve_workspace_notes_root(workspace_root=workspace_root, cwd=cwd)

    descriptor: dict[str, Any] = {
        "kind": "hugo_notes",
        "enabled": True,
        "root": str(root),
        "root_source": root_source,
        "content_project_name": DEFAULT_CONTENT_PROJECT_NAME,
        "capabilities": list(HUGO_NOTES_CAPABILITIES),
        "rules": list(HUGO_NOTES_RULES),
        "write_policy": HUGO_NOTES_WRITE_POLICY,
    }
    if active_note:
        descriptor["active_note"] = {
            key: value
            for key, value in dict(active_note).items()
            if value not in (None, "", [], {})
        }
    return descriptor


def enrich_notes_surface_context(
    surface_context: Mapping[str, Any] | None,
    *,
    workspace_root: str | Path | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Attach the shared Hugo notes feature contract to an agent surface context."""

    context = dict(surface_context or {})
    existing_feature = _mapping(context.get("notes_feature"))
    active_note = _mapping(context.get("active_note"))
    root_hint = (
        context.get("notes_root")
        or existing_feature.get("root")
        or existing_feature.get("notes_root")
    )
    workspace_hint = (
        workspace_root
        or context.get("workspace_root")
        or context.get("workspace_path")
        or context.get("cwd")
    )
    descriptor = hugo_notes_feature_descriptor(
        notes_root=str(root_hint).strip() if root_hint else None,
        active_note=active_note,
        workspace_root=workspace_hint,
        cwd=cwd,
    )
    if existing_feature:
        preserved = {
            key: value
            for key, value in existing_feature.items()
            if value not in (None, "", [], {})
        }
        descriptor.update(preserved)
        descriptor.setdefault("rules", list(HUGO_NOTES_RULES))
        descriptor.setdefault("write_policy", HUGO_NOTES_WRITE_POLICY)
        descriptor.setdefault("capabilities", list(HUGO_NOTES_CAPABILITIES))

    context["notes_root"] = str(descriptor.get("root") or "")
    context["notes_feature"] = descriptor
    context["capabilities"] = _unique_strings(
        [
            *_string_sequence(context.get("capabilities")),
            *_string_sequence(descriptor.get("capabilities")),
        ]
    )
    return context


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_sequence(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, Iterable):
        return []
    return [str(item) for item in value if str(item).strip()]


def _unique_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        token = str(value or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _safe_resolve(path: Path) -> Path:
    try:
        return path.expanduser().resolve(strict=False)
    except OSError:
        return path.expanduser()
