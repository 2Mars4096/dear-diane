"""dan-bot — create, manage, and run Telegram bot fleets.

Usage::

    dan-bot create research-bot      # interactive setup
    dan-bot list                     # show configured bots
    dan-bot start research-bot       # start a single bot
    dan-bot start-all                # start all bots as a fleet
    dan-bot remove research-bot      # remove a bot
    dan-bot setup-guide              # BotFather setup instructions
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TELEGRAM_DIR = Path.home() / ".dan" / "telegram"
_FLEET_PID_FILE = _TELEGRAM_DIR / "fleet.pid"
_FLEET_LOG_FILE = Path.home() / ".dan" / "logs" / "telegram-fleet.log"


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

def _verify_token(token: str) -> dict[str, Any] | None:
    """Verify a bot token via getMe. Returns bot info dict or None."""
    async def _check() -> dict[str, Any] | None:
        try:
            from telegram import Bot

            bot = Bot(token=token)
            async with bot:
                me = await bot.get_me()
                return {
                    "username": me.username,
                    "first_name": me.first_name,
                    "can_join_groups": me.can_join_groups,
                    "can_read_all_group_messages": me.can_read_all_group_messages,
                }
        except Exception as exc:
            logger.debug("Token verification failed: %s", exc)
            return None

    return asyncio.run(_check())


def _privacy_warning(username: str) -> str:
    return (
        f"\n⚠️  Privacy mode is ON for @{username}.\n"
        "For multi-bot group chat, disable it:\n"
        "  1. Message @BotFather\n"
        "  2. Send /setprivacy\n"
        f"  3. Select @{username}\n"
        "  4. Choose \"Disable\"\n"
    )


def verify_bot_token(token: str) -> dict[str, Any] | None:
    return _verify_token(token)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def _cmd_create(args: argparse.Namespace) -> None:
    from dan.adapters.telegram_config import (
        TelegramBotConfig,
        load_fleet_config,
        save_fleet_config,
    )

    name = args.name
    config_path = args.config or None
    config = load_fleet_config(config_path)

    if name in config.bots:
        print(f"Bot '{name}' already exists. Use 'dan-bot edit {name}' to modify.")
        sys.exit(1)

    print(f"🤖 Let's set up \"{name}\"!\n")
    print("1. Open Telegram and message @BotFather")
    print("2. Send /newbot")
    print("3. Choose a name and username")
    print("4. Copy the token BotFather gives you\n")

    token = input("Paste your bot token: ").strip()
    if not token:
        print("No token provided. Aborting.")
        sys.exit(1)

    print("\nVerifying token...", end=" ", flush=True)
    info = _verify_token(token)
    if info is None:
        print("❌ Invalid token.")
        sys.exit(1)

    username = info.get("username", "")
    print(f"✅ Verified — @{username}\n")

    if not info.get("can_read_all_group_messages"):
        print(_privacy_warning(username))

    personality = input("Personality (optional, press Enter to skip): ").strip()
    projects_raw = input(
        "Assign projects (comma-separated, press Enter to skip): ",
    ).strip()
    projects = (
        [p.strip() for p in projects_raw.split(",") if p.strip()]
        if projects_raw
        else []
    )

    is_default = False
    if not any(b.default for b in config.bots.values()):
        is_default = True
        print(f"  (Setting as default bot since no default exists)")
    else:
        default_input = input("Make this the default bot? [y/N]: ").strip().lower()
        is_default = default_input in ("y", "yes")

    bot_config = TelegramBotConfig(
        token=token,
        personality=personality,
        projects=projects,
        default=is_default,
    )
    if is_default:
        _clear_other_defaults(config, name)
    config.bots[name] = bot_config
    save_fleet_config(config, config_path)

    from dan.adapters.telegram_config import default_config_path

    saved_path = config_path or default_config_path()
    print(f"\n✅ Saved to {saved_path}")
    print(f"\nStart chatting: dan-bot start {name}")
    print("Start all bots: dan-bot start-all")


def _cmd_list(args: argparse.Namespace) -> None:
    from dan.adapters.telegram_config import load_fleet_config

    config = load_fleet_config(args.config or None)

    if not config.bots:
        print("No bots configured. Run 'dan-bot create <name>' to add one.")
        return

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(show_header=True, header_style="bold")
        table.add_column("Name")
        table.add_column("Username")
        table.add_column("Projects")
        table.add_column("Default")
        table.add_column("Status")
        table.add_column("Personality")

        status = "running" if _fleet_daemon_running() else "stopped"

        for name, bot in config.bots.items():
            info = _verify_token(bot.token) or {}
            table.add_row(
                name,
                f"@{info.get('username', 'unknown')}",
                ", ".join(bot.projects) or "—",
                "✓" if bot.default else "",
                status,
                (bot.personality[:40] + "…") if len(bot.personality) > 40 else (bot.personality or "—"),
            )

        console.print(table)

        if config.groups:
            console.print(f"\nGroups: {len(config.groups)} configured")
    except ImportError:
        status = "running" if _fleet_daemon_running() else "stopped"
        for name, bot in config.bots.items():
            default = " (default)" if bot.default else ""
            projects = ", ".join(bot.projects) or "none"
            info = _verify_token(bot.token) or {}
            username = info.get("username", "unknown")
            print(f"  {name}{default} (@{username}) — {status} — projects: {projects}")


def _cmd_start(args: argparse.Namespace) -> None:
    from dan.adapters.telegram_config import load_fleet_config
    from dan.adapters.telegram_adapter import TelegramAdapter, TelegramAdapterConfig

    config = load_fleet_config(args.config or None)
    name = args.name

    if name not in config.bots:
        print(f"Bot '{name}' not found. Run 'dan-bot list' to see configured bots.")
        sys.exit(1)

    bot_cfg = config.bots[name]
    adapter_config = TelegramAdapterConfig(
        bot_token=bot_cfg.token,
        progress_throttle=config.settings.progress_throttle,
        max_inbound_media_mb=config.settings.max_inbound_media_mb,
        server_url=args.server,
        bot_name=name,
        personality=bot_cfg.personality,
        projects=list(bot_cfg.projects),
    )
    adapter = TelegramAdapter(adapter_config)

    from dan.cli.adapter import _run_adapter_chat_mode

    print(f"Starting {name}...")
    try:
        asyncio.run(
            _run_adapter_chat_mode(adapter, adapter_config, f"telegram:{name}"),
        )
    except KeyboardInterrupt:
        print("\nStopped.")


def _cmd_start_all(args: argparse.Namespace) -> None:
    from dan.adapters.telegram_fleet import run_fleet

    if args.daemon:
        _start_daemon(args)
        return

    _warn_privacy_for_fleet(args.config or None)
    try:
        asyncio.run(run_fleet(args.config or None, args.server))
    except KeyboardInterrupt:
        print("\nFleet stopped.")


def _cmd_stop(args: argparse.Namespace) -> None:
    if args.name:
        print(
            "Per-bot stop is not supported yet because the fleet runs as one process. "
            "Use 'dan-bot stop' to stop the whole fleet.",
        )
        return
    if not _FLEET_PID_FILE.exists():
        print("No fleet daemon running.")
        return
    try:
        pid = int(_FLEET_PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"Sent stop signal to fleet (PID {pid}).")
        _FLEET_PID_FILE.unlink(missing_ok=True)
    except (ProcessLookupError, ValueError):
        print("Fleet process not found. Cleaning up PID file.")
        _FLEET_PID_FILE.unlink(missing_ok=True)


def _cmd_remove(args: argparse.Namespace) -> None:
    from dan.adapters.telegram_config import load_fleet_config, save_fleet_config

    config = load_fleet_config(args.config or None)
    name = args.name

    if name not in config.bots:
        print(f"Bot '{name}' not found.")
        sys.exit(1)

    confirm = input(f"Remove '{name}'? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Cancelled.")
        return

    del config.bots[name]
    save_fleet_config(config, args.config or None)
    print(f"Removed '{name}'.")


def _cmd_edit(args: argparse.Namespace) -> None:
    from dan.adapters.telegram_config import load_fleet_config, save_fleet_config

    config = load_fleet_config(args.config or None)
    name = args.name

    if name not in config.bots:
        print(f"Bot '{name}' not found.")
        sys.exit(1)

    bot = config.bots[name]
    print(f"Editing '{name}' (press Enter to keep current value)\n")

    personality = input(f"Personality [{bot.personality or '(none)'}]: ").strip()
    if personality:
        bot.personality = personality

    projects_raw = input(
        f"Projects [{', '.join(bot.projects) or '(none)'}]: ",
    ).strip()
    if projects_raw:
        bot.projects = [p.strip() for p in projects_raw.split(",") if p.strip()]

    default_input = input(
        f"Default [{'yes' if bot.default else 'no'}]: ",
    ).strip().lower()
    if default_input:
        bot.default = default_input in ("y", "yes")
    if bot.default:
        _clear_other_defaults(config, name)

    save_fleet_config(config, args.config or None)
    print(f"Updated '{name}'.")


def _cmd_assign(args: argparse.Namespace) -> None:
    from dan.adapters.telegram_config import load_fleet_config, save_fleet_config

    config = load_fleet_config(args.config or None)
    name = args.name

    if name not in config.bots:
        print(f"Bot '{name}' not found.")
        sys.exit(1)

    projects = [p.strip() for p in args.projects.split(",") if p.strip()]
    config.bots[name].projects = projects
    save_fleet_config(config, args.config or None)
    print(f"Assigned projects to '{name}': {', '.join(projects)}")


def _cmd_group_set(args: argparse.Namespace) -> None:
    from dan.adapters.telegram_config import (
        TelegramGroupConfig,
        load_fleet_config,
        save_fleet_config,
    )

    config = load_fleet_config(args.config or None)
    chat_id = str(args.chat_id)
    config.groups[chat_id] = TelegramGroupConfig()
    save_fleet_config(config, args.config or None)
    print(f"Group {chat_id} added to config.")


def _cmd_group_info(args: argparse.Namespace) -> None:
    from dan.adapters.telegram_config import load_fleet_config

    config = load_fleet_config(args.config or None)
    if not config.groups:
        print("No groups configured. Use 'dan-bot group set <chat_id>'.")
        return
    bot_token = next((bot.token for bot in config.bots.values()), "")
    for gid, gcfg in config.groups.items():
        topics = "enabled" if gcfg.forum_topics else "disabled"
        chat_info = _fetch_group_info(bot_token, gid)
        title = chat_info.get("title", "")
        if title:
            print(
                f"  Group {gid} ({title}): forum topics {topics}, "
                f"{len(gcfg.topic_map)} topic mappings",
            )
        else:
            print(
                f"  Group {gid}: forum topics {topics}, "
                f"{len(gcfg.topic_map)} topic mappings",
            )


def _cmd_token(args: argparse.Namespace) -> None:
    from dan.adapters.telegram_config import load_fleet_config

    config = load_fleet_config(args.config or None)
    name = args.name

    if name not in config.bots:
        print(f"Bot '{name}' not found.")
        sys.exit(1)

    token = config.bots[name].token
    if args.reveal:
        print(f"Token for '{name}': {token}")
    else:
        masked = token[:8] + "…" + token[-4:] if len(token) > 12 else "***"
        print(f"Token for '{name}': {masked}")
        print("  Use --reveal to show full token.")


def _cmd_setup_guide(_args: argparse.Namespace) -> None:
    print("""
BotFather Setup Guide
=====================

1. CREATE A BOT
   Open Telegram → message @BotFather → /newbot
   Choose a display name and username (must end in 'bot')
   Copy the token

2. DISABLE PRIVACY MODE (required for group chat)
   Message @BotFather → /setprivacy
   Select your bot → Choose "Disable"
   (This lets the bot see all messages in groups, not just commands)

3. SET BOT COMMANDS (optional)
   /setcommands → select your bot → paste:
   help - Show available commands
   status - Check task status
   cancel - Cancel current task
   find - Find a file
   send - Send a file

4. SET DESCRIPTION (optional)
   /setdescription → select your bot → enter a description

5. ADD TO GROUP
   Open your group → Add Members → search for your bot's username

6. CONFIGURE IN DAN
   dan-bot create <name>   (paste your token when prompted)
   dan-bot start <name>    (start chatting)
""")


def _cmd_status(args: argparse.Namespace) -> None:
    if _fleet_daemon_running():
        pid = int(_FLEET_PID_FILE.read_text().strip())
        print(f"Fleet daemon running (PID {pid}).")
        return
    print("No fleet daemon running.")


def _cmd_logs(_args: argparse.Namespace) -> None:
    if not _FLEET_LOG_FILE.exists():
        print(f"No log file found at {_FLEET_LOG_FILE}")
        return
    lines = _FLEET_LOG_FILE.read_text().splitlines()
    for line in lines[-50:]:
        print(line)


def _start_daemon(args: argparse.Namespace) -> None:
    _TELEGRAM_DIR.mkdir(parents=True, exist_ok=True)
    _FLEET_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    if _FLEET_PID_FILE.exists():
        try:
            pid = int(_FLEET_PID_FILE.read_text().strip())
            os.kill(pid, 0)
            print(f"Fleet daemon already running (PID {pid}).")
            return
        except Exception:
            _FLEET_PID_FILE.unlink(missing_ok=True)

    cmd = [sys.executable, "-m", "dan.cli.bot"]
    if args.config:
        cmd.extend(["--config", args.config])
    cmd.append("start-all")
    if args.server:
        cmd.extend(["--server", args.server])

    env = os.environ.copy()
    env["DAN_BOT_DAEMON_CHILD"] = "1"

    with _FLEET_LOG_FILE.open("a") as log_file:
        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )

    _FLEET_PID_FILE.write_text(str(proc.pid))
    print(f"Fleet daemon started (PID {proc.pid}).")
    print(f"Logs: {_FLEET_LOG_FILE}")


def _warn_privacy_for_fleet(config_path: str | None) -> None:
    from dan.adapters.telegram_config import load_fleet_config

    config = load_fleet_config(config_path)
    warned = False
    for bot in config.bots.values():
        info = _verify_token(bot.token)
        if info and not info.get("can_read_all_group_messages"):
            print(_privacy_warning(info.get("username", "bot")).rstrip())
            warned = True
    if warned:
        print()


def _clear_other_defaults(config, selected_name: str) -> None:
    for name, bot in config.bots.items():
        if name != selected_name:
            bot.default = False


def _fleet_daemon_running() -> bool:
    if not _FLEET_PID_FILE.exists():
        return False
    try:
        pid = int(_FLEET_PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError, OSError):
        return False


def _fetch_group_info(bot_token: str, chat_id: str | None) -> dict[str, Any]:
    if not bot_token or chat_id is None:
        return {}

    async def _get() -> dict[str, Any]:
        try:
            from telegram import Bot

            bot = Bot(token=bot_token)
            async with bot:
                chat = await bot.get_chat(int(chat_id))
                return {
                    "chat_id": str(chat.id),
                    "title": getattr(chat, "title", "") or "",
                    "is_forum": bool(getattr(chat, "is_forum", False)),
                }
        except Exception:
            return {}

    return asyncio.run(_get())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        from dan.cli import load_env
        load_env()
    except ImportError:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="dan-bot",
        description="Create, manage, and run Telegram bot fleets",
    )
    parser.add_argument(
        "--config", "-c", default=None,
        help="Path to fleet config file (default: ~/.dan/telegram/config.json)",
    )

    sub = parser.add_subparsers(dest="command")

    # create
    p = sub.add_parser("create", help="Create a new bot (interactive)")
    p.add_argument("name", help="Bot name (e.g. research-bot)")
    p.set_defaults(func=_cmd_create)

    # list
    p = sub.add_parser("list", help="List configured bots")
    p.set_defaults(func=_cmd_list)

    # start
    p = sub.add_parser("start", help="Start a single bot")
    p.add_argument("name", help="Bot name to start")
    p.add_argument("--server", default=None, help="DAN server URL")
    p.set_defaults(func=_cmd_start)

    # start-all
    p = sub.add_parser("start-all", help="Start all bots as a fleet")
    p.add_argument("--server", default=None, help="DAN server URL")
    p.add_argument(
        "--daemon", action="store_true", help="Run as background daemon",
    )
    p.set_defaults(func=_cmd_start_all)

    # stop
    p = sub.add_parser("stop", help="Stop the fleet daemon")
    p.add_argument("name", nargs="?", help="Optional bot name within the fleet")
    p.set_defaults(func=_cmd_stop)

    # remove
    p = sub.add_parser("remove", help="Remove a bot from config")
    p.add_argument("name", help="Bot name to remove")
    p.set_defaults(func=_cmd_remove)

    # edit
    p = sub.add_parser("edit", help="Edit bot configuration")
    p.add_argument("name", help="Bot name to edit")
    p.set_defaults(func=_cmd_edit)

    # assign
    p = sub.add_parser("assign", help="Assign projects to a bot")
    p.add_argument("name", help="Bot name")
    p.add_argument("projects", help="Comma-separated project names")
    p.set_defaults(func=_cmd_assign)

    # group
    group_parser = sub.add_parser("group", help="Manage group chat settings")
    group_sub = group_parser.add_subparsers(dest="group_command")

    p = group_sub.add_parser("set", help="Set a group chat ID")
    p.add_argument("chat_id", type=int, help="Telegram group chat ID")
    p.set_defaults(func=_cmd_group_set)

    p = group_sub.add_parser("info", help="Show group info")
    p.set_defaults(func=_cmd_group_info)

    # token
    p = sub.add_parser("token", help="Show bot token")
    p.add_argument("name", help="Bot name")
    p.add_argument("--reveal", action="store_true", help="Show full token")
    p.set_defaults(func=_cmd_token)

    # setup-guide
    p = sub.add_parser("setup-guide", help="Print BotFather setup guide")
    p.set_defaults(func=_cmd_setup_guide)

    # status
    p = sub.add_parser("status", help="Check fleet daemon status")
    p.set_defaults(func=_cmd_status)

    # logs
    p = sub.add_parser("logs", help="Tail fleet log file")
    p.set_defaults(func=_cmd_logs)

    args = parser.parse_args()

    if hasattr(args, "func"):
        args.func(args)
    elif hasattr(args, "group_command") and args.group_command:
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
