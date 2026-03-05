"""dan.adapters — messaging adapter framework for DAN workflows.

Adapters render ``HumanNode`` I/O through messaging channels (email,
Telegram, WhatsApp).  The user on the other end of the channel IS the
human in the workflow — the engine is completely agnostic of the channel.

Quick-start::

    from dan.adapters import (
        AdapterConfig,
        AdapterSessionStore,
        EmailAdapter,
        EmailAdapterConfig,
        MessagingHumanRenderer,
        TelegramAdapter,
        TelegramAdapterConfig,
        WhatsAppAdapter,
        WhatsAppAdapterConfig,
    )
"""

from dan.adapters.base import (
    AdapterConfig,
    AdapterSession,
    AdapterSessionStore,
    MessagingAdapter,
    MessagingHumanRenderer,
    SessionState,
    format_prompt_for_messaging,
    parse_response_text,
    should_trigger,
)
from dan.adapters.gateway_mixin import GatewayAdapterMixin
from dan.adapters.email_adapter import EmailAdapter, EmailAdapterConfig
from dan.adapters.telegram_adapter import TelegramAdapter, TelegramAdapterConfig
from dan.adapters.whatsapp_adapter import WhatsAppAdapter, WhatsAppAdapterConfig

__all__ = [
    "AdapterConfig",
    "AdapterSession",
    "AdapterSessionStore",
    "EmailAdapter",
    "EmailAdapterConfig",
    "GatewayAdapterMixin",
    "MessagingAdapter",
    "MessagingHumanRenderer",
    "SessionState",
    "TelegramAdapter",
    "TelegramAdapterConfig",
    "WhatsAppAdapter",
    "WhatsAppAdapterConfig",
    "format_prompt_for_messaging",
    "parse_response_text",
    "should_trigger",
]
