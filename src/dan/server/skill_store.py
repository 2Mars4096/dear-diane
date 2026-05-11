"""Filesystem-backed skill store with IDE-compatible SKILL.md format.

Skills are markdown files with YAML frontmatter that inject domain knowledge
into LLM prompts via the hyperedge system.  The store scans three tiers of
directories on startup:

1. **User-level** — ``~/.dan/skills/<name>/SKILL.md`` (cross-project)
2. **Project-level** — ``<project>/.dan/skills/<name>/SKILL.md``
3. **External read-through** — existing Codex, Claude Code, Cursor, and legacy skill/rule directories

The frontmatter schema is a superset of the Cursor / Claude Code / Codex
convention (``name`` + ``description``) so skills authored for DAN remain
usable by other IDE agents, and vice versa.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dan.models.hyperedges import Hyperedge, HyperedgeType, HookType

logger = logging.getLogger(__name__)

__all__ = [
    "SkillDescriptor",
    "SkillStore",
    "default_external_skill_dirs",
    "parse_skill_md",
]


# ---------------------------------------------------------------------------
# Descriptor model
# ---------------------------------------------------------------------------

class SkillDescriptor(BaseModel):
    """IDE-compatible skill metadata plus DAN-specific extensions.

    The ``name`` and ``description`` fields are the shared schema that all
    major IDE agent platforms (Cursor, Claude Code, Codex) recognise.
    DAN-specific fields are optional and ignored by other tools.
    """

    # Shared / IDE-compatible fields
    name: str
    description: str = ""

    # DAN extensions (optional — skills imported from other IDEs won't have these)
    tags: list[str] = Field(default_factory=list)
    hyperedge_type: HyperedgeType = "skill"
    hook: HookType = "pre_prompt"
    inject_as: str = "system"

    attach_to: list[str] = Field(default_factory=list)
    attach_to_type: list[str] = Field(default_factory=list)
    attach_to_tags: list[str] = Field(default_factory=list)
    attach_to_subgraph: list[str] = Field(default_factory=list)
    attach_globally: bool = False
    propagate: bool = True
    enabled: bool = True

    # Content
    content: str = ""

    # Source tracking
    source_path: Path | None = None
    scope: str = "user"
    skill_id: str = ""

    def to_hyperedge(self) -> Hyperedge:
        """Convert to a ``Hyperedge`` instance for engine runtime use."""
        resolved_tags = self.attach_to_tags or self.tags
        has_selector = (
            self.attach_globally
            or bool(self.attach_to)
            or bool(self.attach_to_type)
            or bool(resolved_tags)
            or bool(self.attach_to_subgraph)
        )
        return Hyperedge(
            id=f"skill_{self.skill_id}" if self.skill_id else f"skill_{_slugify(self.name)}",
            name=self.name,
            description=self.description,
            hyperedge_type=self.hyperedge_type,
            hook=self.hook,
            content=self.content,
            attach_to=self.attach_to,
            attach_to_type=self.attach_to_type,
            attach_to_tags=resolved_tags,
            attach_to_subgraph=self.attach_to_subgraph,
            attach_globally=not has_selector or self.attach_globally,
            propagate=self.propagate,
            enabled=self.enabled,
        )

    def to_library_entry(self) -> dict[str, Any]:
        """Convert to the legacy ``SKILL_LIBRARY`` dict format."""
        return {
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "inject_as": self.inject_as,
            "text": self.content,
            "hook_points": [self.hook],
            "target_nodes": self.attach_to_type,
        }


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL)
_BRACKET_RE = re.compile(r"^\[(.*)\]$")
_BOOL_TRUE = frozenset({"true", "yes", "1", "on"})
_BOOL_FALSE = frozenset({"false", "no", "0", "off"})


def parse_skill_md(text: str, fallback_name: str = "") -> SkillDescriptor | None:
    """Parse a ``SKILL.md`` (or any skill ``.md``) into a descriptor.

    Accepts both the IDE-standard minimal frontmatter (``name`` +
    ``description``) and DAN's extended fields.  Returns ``None`` when
    no valid frontmatter block is found.
    """
    match = _FM_RE.match(text)
    if not match:
        return None

    raw_fm = match.group(1)
    body = match.group(2).strip()

    fm: dict[str, Any] = {}
    for line in raw_fm.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        if not key or key.startswith("#"):
            continue

        bracket_match = _BRACKET_RE.match(value)
        if bracket_match:
            items = [i.strip().strip("'\"") for i in bracket_match.group(1).split(",") if i.strip()]
            fm[key] = items
        elif value.lower() in _BOOL_TRUE:
            fm[key] = True
        elif value.lower() in _BOOL_FALSE:
            fm[key] = False
        else:
            fm[key] = value.strip("'\"")

    name = fm.get("name", fallback_name)
    if not name:
        return None

    descriptor = SkillDescriptor(
        name=name,
        description=fm.get("description", ""),
        tags=_ensure_list(fm.get("tags", [])),
        content=body,
        skill_id=fm.get("skill_id", _slugify(name)),
    )

    if "hyperedge_type" in fm:
        descriptor.hyperedge_type = fm["hyperedge_type"]
    if "hook" in fm:
        descriptor.hook = fm["hook"]
    if "inject_as" in fm:
        descriptor.inject_as = fm["inject_as"]
    if "attach_to" in fm:
        descriptor.attach_to = _ensure_list(fm["attach_to"])
    if "attach_to_type" in fm:
        descriptor.attach_to_type = _ensure_list(fm["attach_to_type"])
    if "attach_to_tags" in fm:
        descriptor.attach_to_tags = _ensure_list(fm["attach_to_tags"])
    if "attach_to_subgraph" in fm:
        descriptor.attach_to_subgraph = _ensure_list(fm["attach_to_subgraph"])
    if "attach_globally" in fm:
        descriptor.attach_globally = bool(fm["attach_globally"])
    if "propagate" in fm:
        descriptor.propagate = bool(fm["propagate"])
    if "enabled" in fm:
        descriptor.enabled = bool(fm["enabled"])
    if "scope" in fm:
        descriptor.scope = fm["scope"]

    # Backward compat: ``target_nodes`` → ``attach_to_type``
    if "target_nodes" in fm and not descriptor.attach_to_type:
        descriptor.attach_to_type = _ensure_list(fm["target_nodes"])
    # Backward compat: ``hook_points`` → ``hook`` (take first)
    if "hook_points" in fm and "hook" not in fm:
        pts = _ensure_list(fm["hook_points"])
        if pts:
            descriptor.hook = pts[0]

    return descriptor


# ---------------------------------------------------------------------------
# Skill store
# ---------------------------------------------------------------------------

_DEFAULT_USER_DIR = Path.home() / ".dan" / "skills"
def _candidate_home_dirs() -> list[Path]:
    """Return likely user homes, including account-scoped agent homes."""

    candidates = [Path.home()]
    try:
        import pwd

        candidates.append(Path(pwd.getpwuid(os.getuid()).pw_dir))
    except Exception:
        pass

    env_home = os.environ.get("DAN_USER_HOME", "").strip()
    if env_home:
        candidates.append(Path(env_home).expanduser())

    result: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve(strict=False))
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _default_external_dirs() -> list[Path]:
    roots: list[Path] = []
    for home in _candidate_home_dirs():
        roots.extend(
            [
                home / ".codex" / "skills",
                home / ".claude" / "skills",
                home / ".cursor" / "skills",
                home / ".cursor" / "rules",
            ]
        )
    return roots


def default_external_skill_dirs() -> list[Path]:
    """Return existing external skill/rule directories to read in place.

    ``DAN_EXTERNAL_SKILLS_DIRS`` may add more directories, separated by the
    platform path separator. Set ``DAN_AUTO_EXTERNAL_SKILLS=0`` to disable the
    built-in Codex/Claude/Cursor read-through roots while still allowing
    explicit directories from ``DAN_EXTERNAL_SKILLS_DIRS``.
    """

    result: list[Path] = []
    auto_enabled = os.environ.get("DAN_AUTO_EXTERNAL_SKILLS", "1").strip().lower()
    if auto_enabled not in {"0", "false", "no", "off"}:
        result.extend(path for path in _default_external_dirs() if path.is_dir())

    extra = os.environ.get("DAN_EXTERNAL_SKILLS_DIRS", "").strip()
    if extra:
        for item in extra.split(os.pathsep):
            path = Path(item).expanduser()
            if path.is_dir():
                result.append(path)

    seen: set[str] = set()
    deduped: list[Path] = []
    for path in result:
        key = str(path.resolve(strict=False))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


class SkillStore:
    """Multi-directory skill scanner with import capability.

    Parameters
    ----------
    user_dir
        User-level skills (default ``~/.dan/skills/``).
    project_dir
        Project-level skills (e.g. ``<project>/.dan/skills/``).
    extra_dirs
        Additional directories to scan in place (Codex/Claude/Cursor roots,
        legacy ``DAN_CUSTOM_SKILLS_DIR``, or custom external directories).
    """

    def __init__(
        self,
        user_dir: Path | None = None,
        project_dir: Path | None = None,
        extra_dirs: list[Path] | None = None,
    ) -> None:
        self._user_dir = user_dir or _DEFAULT_USER_DIR
        self._project_dir = project_dir
        self._extra_dirs = extra_dirs or []

        self._skills: dict[str, SkillDescriptor] = {}

    # -- public API --------------------------------------------------------

    @property
    def user_dir(self) -> Path:
        return self._user_dir

    @property
    def project_dir(self) -> Path | None:
        return self._project_dir

    def scan(self) -> int:
        """Scan all configured directories and return count of skills found.

        Skills from narrower scopes shadow broader ones (project > user > extra).
        """
        self._skills.clear()
        count = 0

        for d in self._extra_dirs:
            count += self._scan_dir(d, scope="extra")

        count += self._scan_dir(self._user_dir, scope="user")

        if self._project_dir:
            count += self._scan_dir(self._project_dir, scope="project")

        return count

    def get(self, name: str) -> SkillDescriptor | None:
        """Look up a skill by name or skill_id (case-insensitive)."""
        key = name.lower().replace("-", "_").replace(" ", "_")
        if key in self._skills:
            return self._skills[key]
        for desc in self._skills.values():
            if desc.name.lower() == name.lower():
                return desc
        return None

    def list_skills(self) -> list[SkillDescriptor]:
        """Return all loaded skills, sorted by name."""
        return sorted(self._skills.values(), key=lambda s: s.name.lower())

    def list_by_scope(self, scope: str) -> list[SkillDescriptor]:
        """Return skills from a specific scope."""
        return [s for s in self._skills.values() if s.scope == scope]

    def import_skill(
        self,
        source: Path,
        target_scope: str = "user",
    ) -> SkillDescriptor | None:
        """Import a skill from an external path into DAN's store.

        Supports:
        - Directory with SKILL.md (e.g. ``~/.cursor/skills/scientific-writer/``)
        - Single .md file

        Returns the imported descriptor, or None on failure.
        """
        source = Path(source).expanduser().resolve()

        if source.is_dir():
            skill_md = source / "SKILL.md"
            if not skill_md.exists():
                md_files = list(source.glob("*.md"))
                if md_files:
                    skill_md = md_files[0]
                else:
                    logger.warning("No .md files found in %s", source)
                    return None
        elif source.is_file() and source.suffix == ".md":
            skill_md = source
        else:
            logger.warning("Invalid skill source: %s", source)
            return None

        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Failed to read %s", skill_md)
            return None

        descriptor = parse_skill_md(text, fallback_name=source.stem)
        if descriptor is None:
            logger.warning("No valid frontmatter in %s", skill_md)
            return None

        target_dir = self._user_dir if target_scope == "user" else (self._project_dir or self._user_dir)
        skill_slug = _slugify(descriptor.name)
        dest_dir = target_dir / skill_slug
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest_skill = dest_dir / "SKILL.md"
        if source.is_dir():
            for f in source.iterdir():
                if f.is_file():
                    shutil.copy2(f, dest_dir / f.name)
            if not dest_skill.exists() and skill_md.name != "SKILL.md":
                shutil.copy2(skill_md, dest_skill)
        else:
            shutil.copy2(skill_md, dest_skill)

        descriptor.source_path = dest_skill
        descriptor.scope = target_scope
        descriptor.skill_id = skill_slug

        key = skill_slug
        self._skills[key] = descriptor
        logger.info("Imported skill '%s' from %s → %s", descriptor.name, source, dest_dir)
        return descriptor

    def get_hyperedges(self) -> list[Hyperedge]:
        """Convert all loaded skills to Hyperedge instances."""
        return [s.to_hyperedge() for s in self._skills.values() if s.enabled]

    def populate_skill_library(self) -> int:
        """Merge loaded skills into the global ``SKILL_LIBRARY`` dict.

        Returns count of skills added.
        """
        from dan.server.skill_library import SKILL_LIBRARY

        added = 0
        for desc in self._skills.values():
            key = desc.skill_id or _slugify(desc.name)
            if key not in SKILL_LIBRARY:
                SKILL_LIBRARY[key] = desc.to_library_entry()
                added += 1
        return added

    # -- scanning internals ------------------------------------------------

    def _scan_dir(self, directory: Path, scope: str) -> int:
        """Scan a single directory for skills.

        Supports two layouts:
        1. ``dir/skill-name/SKILL.md`` (directory convention)
        2. ``dir/skill-name.md`` or ``dir/skill-name.mdc`` (flat file convention)
        """
        if not directory.is_dir():
            return 0

        count = 0

        for child in sorted(directory.iterdir()):
            if child.name.startswith((".", "_")):
                continue

            skill_md: Path | None = None

            if child.is_dir():
                candidate = child / "SKILL.md"
                if candidate.exists():
                    skill_md = candidate
                else:
                    md_files = sorted([*child.glob("*.md"), *child.glob("*.mdc")])
                    if md_files:
                        skill_md = md_files[0]
            elif child.is_file() and child.suffix in {".md", ".mdc"}:
                skill_md = child

            if skill_md is None:
                continue

            try:
                text = skill_md.read_text(encoding="utf-8")
            except OSError:
                logger.warning("Failed to read %s", skill_md)
                continue

            fallback = child.stem if child.is_file() else child.name
            descriptor = parse_skill_md(text, fallback_name=fallback)
            if descriptor is None:
                continue

            descriptor.source_path = skill_md
            descriptor.scope = scope
            if not descriptor.skill_id:
                descriptor.skill_id = _slugify(descriptor.name)

            key = descriptor.skill_id
            self._skills[key] = descriptor
            count += 1

        if count:
            logger.info("Discovered %d skills in %s (scope=%s)", count, directory, scope)
        return count


# ---------------------------------------------------------------------------
# /skill command handler
# ---------------------------------------------------------------------------


async def handle_skill_command(
    text: str,
    store: SkillStore | None = None,
) -> str:
    """Dispatch ``/skill <subcommand>`` from chat."""
    parts = text.strip().split(None, 2)
    sub = parts[1].lower() if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""

    if store is None:
        store = SkillStore(extra_dirs=default_external_skill_dirs())
        store.scan()

    if sub == "list":
        return _skill_list(store, arg)
    if sub == "info":
        return _skill_info(store, arg)
    if sub == "import":
        return _skill_import(store, arg)
    if sub == "scan":
        return _skill_scan(store)

    return (
        "**Usage:** `/skill <list|info|import|scan>`\n"
        "- `/skill list` — list all loaded skills\n"
        "- `/skill list user|project` — list skills by scope\n"
        "- `/skill info <name>` — show skill details\n"
        "- `/skill import <path>` — import from external location\n"
        "- `/skill scan` — rescan all skill directories"
    )


def _skill_list(store: SkillStore, scope_filter: str) -> str:
    if scope_filter in ("user", "project", "extra"):
        skills = store.list_by_scope(scope_filter)
    else:
        skills = store.list_skills()

    if not skills:
        dirs = [f"`{store.user_dir}`"]
        if store.project_dir:
            dirs.append(f"`{store.project_dir}`")
        return (
            f"No skills found.\n\n"
            f"Skill directories: {', '.join(dirs)}\n"
            f"Add skills as `SKILL.md` files with YAML frontmatter, "
            f"or use `/skill import <path>` to import from Cursor/Claude/Codex."
        )

    lines = ["**Loaded skills:**\n"]
    current_scope = ""
    for s in sorted(skills, key=lambda x: (x.scope, x.name.lower())):
        if s.scope != current_scope:
            current_scope = s.scope
            lines.append(f"\n*{current_scope}:*")
        tags_str = f" `{', '.join(s.tags)}`" if s.tags else ""
        type_str = f" ({s.hyperedge_type})" if s.hyperedge_type != "skill" else ""
        lines.append(f"- **{s.name}**{type_str}{tags_str} — {s.description[:80] if s.description else 'no description'}")

    lines.append(f"\n{len(skills)} skill(s) loaded.")
    return "\n".join(lines)


def _skill_info(store: SkillStore, name: str) -> str:
    if not name:
        return "Usage: `/skill info <name>`"

    desc = store.get(name)
    if not desc:
        return f"Skill `{name}` not found. Use `/skill list` to see available skills."

    lines = [
        f"**{desc.name}**\n",
        f"- **Description:** {desc.description or 'none'}",
        f"- **Scope:** {desc.scope}",
        f"- **Type:** {desc.hyperedge_type}",
        f"- **Hook:** {desc.hook}",
    ]
    if desc.tags:
        lines.append(f"- **Tags:** {', '.join(desc.tags)}")
    if desc.attach_to_type:
        lines.append(f"- **Target types:** {', '.join(desc.attach_to_type)}")
    if desc.attach_to_tags:
        lines.append(f"- **Attach to tags:** {', '.join(desc.attach_to_tags)}")
    if desc.attach_globally:
        lines.append("- **Scope:** global (all nodes)")
    if desc.source_path:
        lines.append(f"- **Source:** `{desc.source_path}`")

    preview = desc.content[:300]
    if len(desc.content) > 300:
        preview += "..."
    lines.append(f"\n**Content preview:**\n```\n{preview}\n```")

    return "\n".join(lines)


def _skill_import(store: SkillStore, path_str: str) -> str:
    if not path_str:
        return (
            "**Usage:** `/skill import <path>`\n\n"
            "Examples:\n"
            "- `/skill import ~/.cursor/skills/scientific-writer`\n"
            "- `/skill import ~/.claude/skills/code-review`\n"
            "- `/skill import /path/to/custom-skill.md`"
        )

    source = Path(path_str).expanduser()
    if not source.exists():
        return f"Path not found: `{source}`"

    desc = store.import_skill(source)
    if desc is None:
        return f"Failed to import from `{source}`. Ensure the file has valid YAML frontmatter (`name` field required)."

    store.populate_skill_library()

    return (
        f"Imported **{desc.name}** → `{store.user_dir / desc.skill_id}/`\n"
        f"- Type: {desc.hyperedge_type}\n"
        f"- Tags: {', '.join(desc.tags) if desc.tags else 'none'}\n\n"
        f"Skill is now active and will be loaded on future startups."
    )


def _skill_scan(store: SkillStore) -> str:
    count = store.scan()
    added = store.populate_skill_library()
    return f"Scanned skill directories: found {count} skill(s), {added} new skill(s) added to library."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Convert a skill name to a filesystem-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug or "unnamed_skill"


def _ensure_list(val: Any) -> list[str]:
    """Coerce a value to list[str]."""
    if isinstance(val, list):
        return [str(v) for v in val]
    if isinstance(val, str):
        return [val] if val else []
    return []
