"""dan — unified CLI entry point.

Dispatches to subcommands like ``dan bot``, while individual ``dan-*``
scripts remain available for direct use.
"""

from __future__ import annotations

import sys

_SUBCOMMANDS = {
    "bot": ("dan.cli.bot", "main"),
    "serve": ("dan.server.__main__", "main"),
    "run": ("dan.cli.run", "main"),
    "chat": ("dan.cli.chat", "main"),
    "ask": ("dan.cli.chat", "main_ask"),
    "adapter": ("dan.cli.adapter", "main"),
    "up": ("dan.cli.up", "main"),
    "down": ("dan.cli.down", "main"),
    "service": ("dan.cli.service", "main"),
    "status": ("dan.cli.status", "main"),
    "logs": ("dan.cli.logs", "main"),
    "editor": ("dan.cli.editor", "main"),
    "furnace": ("dan.cli.furnace", "main"),
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        _print_help()
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    subcmd = sys.argv[1]
    if subcmd not in _SUBCOMMANDS:
        print(f"Unknown subcommand: {subcmd}")
        _print_help()
        sys.exit(1)

    module_path, func_name = _SUBCOMMANDS[subcmd]
    sys.argv = [f"dan {subcmd}", *sys.argv[2:]]

    import importlib
    mod = importlib.import_module(module_path)
    getattr(mod, func_name)()


def _print_help() -> None:
    print("Usage: dan <subcommand> [args...]\n")
    print("Subcommands:")
    print("  bot        Manage Telegram bot fleet")
    print("  serve      Start the DAN server")
    print("  run        Run a workflow")
    print("  chat       Interactive chat session")
    print("  ask        One-shot question")
    print("  adapter    Start a messaging adapter")
    print("  up         Start server + services")
    print("  down       Stop server + services")
    print("  editor     Start server + visual editor")
    print("  furnace    Direct furnace control CLI")
    print("  service    Manage background services")
    print("  status     Show server status")
    print("  logs       View server logs")
    print("\nAll subcommands also available as dan-<subcommand> (e.g., dan-bot).")


if __name__ == "__main__":
    main()
