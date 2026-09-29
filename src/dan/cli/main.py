"""Unified CLI for the retained Diane product."""

from __future__ import annotations

import importlib
import sys

_SUBCOMMANDS = {
    "serve": ("dan.server.__main__", "main"),
    "up": ("dan.cli.up", "main"),
    "down": ("dan.cli.down", "main"),
    "editor": ("dan.cli.editor", "main"),
    "super-organism": ("dan.cli.super_organism", "main"),
    "super-tui": ("dan.cli.super_tui", "main"),
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        _print_help()
        return 0 if len(sys.argv) >= 2 else 1
    subcommand = sys.argv[1]
    target = _SUBCOMMANDS.get(subcommand)
    if target is None:
        print(f"Unknown subcommand: {subcommand}", file=sys.stderr)
        _print_help()
        return 1
    module_name, function_name = target
    sys.argv = [f"dan {subcommand}", *sys.argv[2:]]
    result = getattr(importlib.import_module(module_name), function_name)()
    return result if isinstance(result, int) else 0


def _print_help() -> None:
    print("Dear Diane — A workspace for research, code, and ideas.")
    print("Usage: dear-diane <serve|up|down|editor|super-organism|super-tui> [args...]")
    print("The dan command remains available for compatibility.")


if __name__ == "__main__":
    raise SystemExit(main())
