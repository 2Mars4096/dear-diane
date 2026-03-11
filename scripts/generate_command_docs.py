#!/usr/bin/env python3
"""Generate docs/commands.md from the canonical command registry.

Usage:
    python scripts/generate_command_docs.py > docs/commands.md
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path so imports work from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dan.server.concierge.command_registry import (
    CommandDescriptor,
    get_default_registry,
)

GROUP_DISPLAY_ORDER = [
    "workflow",
    "model_config",
    "memory",
    "scheduling",
    "safety",
    "continuity",
    "progress",
    "learning",
    "computer_use",
    "integration",
    "file_navigation",
    "session",
]

GROUP_LABELS = {
    "workflow": "Workflow",
    "model_config": "Model & Config",
    "memory": "Memory",
    "scheduling": "Scheduling",
    "safety": "Safety",
    "continuity": "Continuity",
    "progress": "Progress",
    "learning": "Learning",
    "computer_use": "Computer Use",
    "integration": "Integration",
    "file_navigation": "File & Navigation",
    "session": "Session",
}

ALL_SURFACES = ["cli", "editor", "telegram", "whatsapp"]

SURFACE_BADGES = {
    "cli": "CLI",
    "editor": "Editor",
    "telegram": "TG",
    "whatsapp": "WA",
}


def _surfaces_for(cmd: CommandDescriptor) -> list[str]:
    if "all" in cmd.surfaces:
        return ALL_SURFACES
    return [s for s in ALL_SURFACES if s in cmd.surfaces]


def _surface_badges(cmd: CommandDescriptor) -> str:
    return " ".join(f"`{SURFACE_BADGES.get(s, s)}`" for s in _surfaces_for(cmd))


def _surface_check(cmd: CommandDescriptor, surface: str) -> str:
    surfaces = _surfaces_for(cmd)
    return "Yes" if surface in surfaces else "—"


def _group_has_surface(cmds: list[CommandDescriptor], surface: str) -> bool:
    return any(surface in _surfaces_for(c) for c in cmds)


def _related_commands(
    cmd: CommandDescriptor,
    group_cmds: list[CommandDescriptor],
) -> list[str]:
    """Return names of other non-hidden commands in the same group."""
    return [
        c.name for c in group_cmds
        if c.name != cmd.name and not c.hidden
    ]


def generate() -> str:
    registry = get_default_registry()
    all_cmds = registry.list_all()

    groups: dict[str, list[CommandDescriptor]] = {}
    for cmd in all_cmds:
        groups.setdefault(cmd.group, []).append(cmd)
    for g in groups.values():
        g.sort(key=lambda c: c.name)

    ordered_groups = [g for g in GROUP_DISPLAY_ORDER if g in groups]
    for g in groups:
        if g not in ordered_groups:
            ordered_groups.append(g)

    lines: list[str] = []

    lines.append("# DAN Command Reference")
    lines.append("")
    lines.append(
        "> Auto-generated from the command registry. "
        "Do not edit manually — run "
        "`python scripts/generate_command_docs.py > docs/commands.md` to regenerate."
    )
    lines.append("")

    # Surface availability matrix
    lines.append("## Surface Availability")
    lines.append("")
    lines.append("| Group | CLI | Editor | Telegram | WhatsApp |")
    lines.append("|-------|-----|--------|----------|----------|")
    for g in ordered_groups:
        label = GROUP_LABELS.get(g, g.replace("_", " ").title())
        cmds = groups[g]
        row = f"| {label} |"
        for surface in ALL_SURFACES:
            has = _group_has_surface(cmds, surface)
            row += " Yes |" if has else " — |"
        lines.append(row)
    lines.append("")

    # Per-group command sections
    for g in ordered_groups:
        label = GROUP_LABELS.get(g, g.replace("_", " ").title())
        cmds = groups[g]
        lines.append(f"## {label} Commands")
        lines.append("")

        for cmd in cmds:
            if cmd.hidden:
                continue
            lines.append(f"### `{cmd.name}`")
            lines.append("")

            if cmd.aliases:
                lines.append(f"- **Aliases:** {', '.join(f'`{a}`' for a in cmd.aliases)}")

            lines.append(f"- **Surfaces:** {_surface_badges(cmd)}")
            lines.append(f"- **Kind:** {cmd.kind}")

            if cmd.args_schema:
                lines.append(f"- **Arguments:** `{cmd.args_schema}`")

            lines.append(f"- **Description:** {cmd.help_text}")

            if cmd.requires:
                lines.append(f"- **Requires:** {', '.join(f'`{r}`' for r in cmd.requires)}")

            if cmd.subcommands:
                lines.append("")
                lines.append("| Subcommand | Arguments | Description |")
                lines.append("|------------|-----------|-------------|")
                for sub in cmd.subcommands.values():
                    args = f"`{sub.args_schema}`" if sub.args_schema else "—"
                    lines.append(f"| `{cmd.name} {sub.name}` | {args} | {sub.help_text} |")

            if cmd.examples:
                lines.append("")
                lines.append("**Examples:**")
                for ex in cmd.examples:
                    lines.append(f"- `{ex}`")

            related = _related_commands(cmd, cmds)
            if related:
                lines.append("")
                lines.append(f"**Related:** {', '.join(f'`{r}`' for r in related)}")

            lines.append("")

    # Footer
    lines.append("---")
    lines.append("")
    lines.append(
        "*For CLI binary commands (`dan-serve`, `dan-chat`, `dan-run`, etc.), "
        "see [docs/cli.md](cli.md).*"
    )
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    print(generate())
