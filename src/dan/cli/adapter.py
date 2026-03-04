"""dan-adapter — start messaging adapters for DAN workflows.

Usage::

    dan-adapter email   --workflow wf.md --imap-host imap.example.com …
    dan-adapter telegram --workflow wf.md --bot-token TOKEN
    dan-adapter whatsapp --workflow wf.md --access-token TOKEN …
    dan-adapter --config adapter-config.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Adapter runner — shared async bootstrap
# ---------------------------------------------------------------------------

async def _run_adapter(adapter: Any, renderer: Any, config: Any) -> None:
    """Start *adapter*, register signal handlers, and block until stopped."""
    from dan.adapters.base import (
        AdapterSessionStore,
        MessagingHumanRenderer,
        SessionState,
        should_trigger,
    )
    from dan.engine.executor import EngineConfig
    from dan.engine.scheduler import Engine
    from dan.loader import load

    session_store = AdapterSessionStore()
    human_renderer = MessagingHumanRenderer(adapter, session_store)

    graph = load(config.workflow_path)

    await adapter.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    async def on_new_message(external_id: str, text: str) -> None:
        if not should_trigger(text, config):
            return

        session = await session_store.get_by_external(external_id)
        if session is not None and session.state in (
            SessionState.RUNNING,
            SessionState.AWAITING_HUMAN,
        ):
            return

        session = await session_store.create(external_id)
        human_renderer.active_session_id = session.session_id

        if hasattr(adapter, "register_session"):
            key = int(external_id) if external_id.isdigit() else external_id
            adapter.register_session(session.session_id, key)

        await session_store.update_state(session.session_id, SessionState.RUNNING)

        engine = Engine(
            config=EngineConfig(),
            human_input_callback=human_renderer.as_callback(),
        )

        try:
            result = await engine.run(graph, inputs={"message": text})
            await adapter.send_result(session.session_id, result.outputs)
            await session_store.update_state(session.session_id, SessionState.COMPLETED)
        except Exception as exc:
            logger.exception("Workflow run failed")
            await session_store.update_state(session.session_id, SessionState.FAILED)
            try:
                await adapter.send_prompt(
                    session.session_id, config.error_message, None,
                )
            except Exception:
                pass
        finally:
            if hasattr(adapter, "unregister_session"):
                adapter.unregister_session(session.session_id)

    adapter._on_new_message = on_new_message

    _print_status(config)
    await stop_event.wait()
    await adapter.stop()


def _print_status(config: Any) -> None:
    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        console.print(Panel(
            f"[bold green]Adapter running[/]\n"
            f"Workflow: {config.workflow_path}\n"
            f"Trigger:  {config.trigger_mode}",
            title="dan-adapter",
        ))
    except ImportError:
        print(f"dan-adapter: running (workflow={config.workflow_path}, trigger={config.trigger_mode})")


# ---------------------------------------------------------------------------
# Subcommand builders
# ---------------------------------------------------------------------------

def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workflow", "-w", required=True, help="Path to workflow .md file")
    parser.add_argument("--timeout", type=float, default=300, help="Response timeout (seconds)")
    parser.add_argument(
        "--trigger-mode", choices=["keyword", "always", "pattern"], default="always",
    )
    parser.add_argument("--trigger-pattern", default=None)
    parser.add_argument("--welcome-message", default=None)
    parser.add_argument("--error-message", default=None)


def _build_email_parser(sub: Any) -> None:
    p = sub.add_parser("email", help="Start email adapter (IMAP + SMTP)")
    _add_common_args(p)
    p.add_argument("--imap-host", required=True)
    p.add_argument("--imap-port", type=int, default=993)
    p.add_argument("--imap-user", required=True)
    p.add_argument("--imap-password", required=True)
    p.add_argument("--smtp-host", required=True)
    p.add_argument("--smtp-port", type=int, default=587)
    p.add_argument("--smtp-user", default=None)
    p.add_argument("--smtp-password", default=None)
    p.add_argument("--target-email", required=True, help="Recipient email address")
    p.add_argument("--subject-filter", default="")
    p.add_argument("--poll-interval", type=float, default=10.0)
    p.set_defaults(adapter_type="email")


def _build_telegram_parser(sub: Any) -> None:
    p = sub.add_parser("telegram", help="Start Telegram bot adapter")
    _add_common_args(p)
    p.add_argument("--bot-token", required=True)
    p.add_argument("--allowed-chat-ids", nargs="*", type=int, default=[])
    p.add_argument("--webhook-url", default=None)
    p.set_defaults(adapter_type="telegram")


def _build_whatsapp_parser(sub: Any) -> None:
    p = sub.add_parser("whatsapp", help="Start WhatsApp Business API adapter")
    _add_common_args(p)
    p.add_argument("--access-token", required=True)
    p.add_argument("--phone-number-id", required=True)
    p.add_argument("--webhook-url", default="")
    p.add_argument("--verify-token", default="dan-verify")
    p.add_argument("--app-secret", default="")
    p.set_defaults(adapter_type="whatsapp")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_from_config_file(path: str) -> tuple[str, Any]:
    """Load a JSON config file and return (adapter_type, config_object)."""
    data = json.loads(Path(path).read_text())
    adapter_type = data.pop("adapter_type", "")
    if not adapter_type:
        print("Error: config file must include 'adapter_type' (email/telegram/whatsapp)", file=sys.stderr)
        sys.exit(1)

    if adapter_type == "email":
        from dan.adapters.email_adapter import EmailAdapterConfig
        return adapter_type, EmailAdapterConfig(**data)
    elif adapter_type == "telegram":
        from dan.adapters.telegram_adapter import TelegramAdapterConfig
        return adapter_type, TelegramAdapterConfig(**data)
    elif adapter_type == "whatsapp":
        from dan.adapters.whatsapp_adapter import WhatsAppAdapterConfig
        return adapter_type, WhatsAppAdapterConfig(**data)
    else:
        print(f"Error: unknown adapter_type '{adapter_type}'", file=sys.stderr)
        sys.exit(1)


def _build_config_from_args(args: argparse.Namespace) -> tuple[str, Any]:
    """Build a typed config from parsed CLI arguments."""
    adapter_type = args.adapter_type
    common = {
        "workflow_path": args.workflow,
        "timeout": args.timeout,
        "trigger_mode": args.trigger_mode,
    }
    if args.trigger_pattern:
        common["trigger_pattern"] = args.trigger_pattern
    if args.welcome_message:
        common["welcome_message"] = args.welcome_message
    if args.error_message:
        common["error_message"] = args.error_message

    if adapter_type == "email":
        from dan.adapters.email_adapter import EmailAdapterConfig
        return adapter_type, EmailAdapterConfig(
            **common,
            imap_host=args.imap_host,
            imap_port=args.imap_port,
            imap_user=args.imap_user,
            imap_password=args.imap_password,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
            smtp_user=args.smtp_user or args.imap_user,
            smtp_password=args.smtp_password or args.imap_password,
            target_email=args.target_email,
            subject_filter=args.subject_filter,
            poll_interval=args.poll_interval,
        )
    elif adapter_type == "telegram":
        from dan.adapters.telegram_adapter import TelegramAdapterConfig
        return adapter_type, TelegramAdapterConfig(
            **common,
            bot_token=args.bot_token,
            allowed_chat_ids=args.allowed_chat_ids or [],
            webhook_url=args.webhook_url,
        )
    elif adapter_type == "whatsapp":
        from dan.adapters.whatsapp_adapter import WhatsAppAdapterConfig
        return adapter_type, WhatsAppAdapterConfig(
            **common,
            access_token=args.access_token,
            phone_number_id=args.phone_number_id,
            webhook_url=args.webhook_url,
            verify_token=args.verify_token,
            app_secret=args.app_secret,
        )
    else:
        print(f"Error: unknown adapter type '{adapter_type}'", file=sys.stderr)
        sys.exit(1)


def _create_adapter(adapter_type: str, config: Any) -> Any:
    if adapter_type == "email":
        from dan.adapters.email_adapter import EmailAdapter
        return EmailAdapter(config)
    elif adapter_type == "telegram":
        from dan.adapters.telegram_adapter import TelegramAdapter
        return TelegramAdapter(config)
    elif adapter_type == "whatsapp":
        from dan.adapters.whatsapp_adapter import WhatsAppAdapter
        return WhatsAppAdapter(config)
    raise ValueError(f"Unknown adapter type: {adapter_type}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    from dan.cli import load_env

    load_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(
        prog="dan-adapter",
        description="Start a messaging adapter for DAN workflows",
    )
    parser.add_argument(
        "--config", "-c", default=None,
        help="Path to JSON config file (alternative to subcommand flags)",
    )

    sub = parser.add_subparsers(dest="subcommand")
    _build_email_parser(sub)
    _build_telegram_parser(sub)
    _build_whatsapp_parser(sub)

    args = parser.parse_args()

    if args.config:
        adapter_type, config = _load_from_config_file(args.config)
    elif args.subcommand:
        adapter_type, config = _build_config_from_args(args)
    else:
        parser.print_help()
        sys.exit(1)

    adapter = _create_adapter(adapter_type, config)

    try:
        asyncio.run(_run_adapter(adapter, None, config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
