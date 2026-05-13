"""Workspace instruction discovery for universal worker briefs."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_WORKSPACE_INSTRUCTION_FILENAMES = ("AGENTS.md",)
DEFAULT_WORKSPACE_INSTRUCTION_CHAR_LIMIT = 24_000


@dataclass(frozen=True)
class WorkspaceInstructions:
    path: Path
    workspace_root: Path
    content: str
    content_sha256: str
    truncated: bool = False

    @property
    def relative_path(self) -> str:
        try:
            return self.path.relative_to(self.workspace_root).as_posix()
        except ValueError:
            return self.path.name

    def metadata(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "relative_path": self.relative_path,
            "content_sha256": self.content_sha256,
            "content_sha256_prefix": self.content_sha256[:16],
            "truncated": self.truncated,
            "chars": len(self.content),
        }


def workspace_instructions_enabled() -> bool:
    value = os.environ.get("DAN_WORKSPACE_INSTRUCTIONS", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def workspace_root_from_payloads(*payloads: Mapping[str, Any]) -> Path | None:
    for payload in payloads:
        for key in ("workspace_root", "workspace", "root"):
            value = payload.get(key)
            if value:
                return Path(str(value)).expanduser().resolve(strict=False)
    return None


def load_workspace_instructions(
    workspace_root: str | Path | None,
    *,
    filenames: Sequence[str] = DEFAULT_WORKSPACE_INSTRUCTION_FILENAMES,
    char_limit: int = DEFAULT_WORKSPACE_INSTRUCTION_CHAR_LIMIT,
) -> WorkspaceInstructions | None:
    """Load the first supported workspace instruction file from a workspace root."""

    if not workspace_instructions_enabled() or workspace_root is None:
        return None
    root = Path(workspace_root).expanduser().resolve(strict=False)
    if not root.exists() or not root.is_dir():
        return None

    for filename in filenames:
        if not filename or "/" in filename or "\\" in filename:
            continue
        path = (root / filename).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        full_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        content = raw.strip()
        truncated = False
        if len(content) > char_limit:
            content = content[:char_limit].rstrip()
            truncated = True
        if not content:
            continue
        return WorkspaceInstructions(
            path=path,
            workspace_root=root,
            content=content,
            content_sha256=full_sha,
            truncated=truncated,
        )
    return None


def render_workspace_instruction_snippet(instructions: WorkspaceInstructions) -> str:
    meta = instructions.metadata()
    truncated_note = "\n[truncated]" if instructions.truncated else ""
    return (
        "Workspace instructions loaded from AGENTS.md:\n"
        f"- source: {meta['relative_path']}\n"
        f"- sha256: {meta['content_sha256_prefix']}\n"
        f"- truncated: {str(meta['truncated']).lower()}\n\n"
        "Precedence:\n"
        "- DAN safety and tool boundaries override these workspace instructions.\n"
        "- Direct operator instructions override these workspace instructions when both are safe.\n"
        "- Otherwise, follow these workspace instructions for project tracking, coding style, and run hygiene.\n\n"
        "Instructions:\n"
        f"{instructions.content}"
        f"{truncated_note}"
    )

