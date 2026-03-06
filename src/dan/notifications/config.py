"""Notification configuration: channels, env-var overrides, file persistence."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

DAN_DIR = Path.home() / ".dan"


class ChannelConfig(BaseModel):
    """Base channel configuration."""

    enabled: bool = False
    event_types: list[str] = Field(
        default_factory=lambda: ["run_completed", "run_failed", "human_input_needed"]
    )


class WebhookConfig(ChannelConfig):
    """Webhook channel with URL, custom headers, and timeout."""

    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = 10.0


class NotificationConfig(BaseModel):
    """Top-level notification configuration across all channels."""

    macos: ChannelConfig = Field(
        default_factory=lambda: ChannelConfig(enabled=sys.platform == "darwin")
    )
    bell: ChannelConfig = Field(default_factory=ChannelConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)


def _default_config_path() -> Path:
    return DAN_DIR / "notifications.json"


def load_notification_config(config_path: Path | None = None) -> NotificationConfig:
    """Load from env vars, then ``~/.dan/notifications.json``, then defaults.

    Env var overrides (highest priority):
      * ``DAN_NOTIFY_BELL=1``  → bell.enabled = True
      * ``DAN_NOTIFY_WEBHOOK_URL``  → webhook.url + webhook.enabled = True
      * ``DAN_NOTIFY_WEBHOOK_HEADERS``  → webhook.headers (JSON string)
    """
    path = config_path or _default_config_path()

    # Start from file if it exists, otherwise defaults
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            config = NotificationConfig.model_validate(data)
        except (json.JSONDecodeError, Exception):
            config = NotificationConfig()
    else:
        config = NotificationConfig()

    # Env-var overrides
    bell_env = os.environ.get("DAN_NOTIFY_BELL", "").strip().lower()
    if bell_env in ("1", "true", "yes"):
        config.bell.enabled = True

    webhook_url = os.environ.get("DAN_NOTIFY_WEBHOOK_URL", "").strip()
    if webhook_url:
        config.webhook.url = webhook_url
        config.webhook.enabled = True

    webhook_headers = os.environ.get("DAN_NOTIFY_WEBHOOK_HEADERS", "").strip()
    if webhook_headers:
        try:
            parsed = json.loads(webhook_headers)
            if isinstance(parsed, dict):
                config.webhook.headers = {str(k): str(v) for k, v in parsed.items()}
        except json.JSONDecodeError:
            pass

    return config


def save_notification_config(
    config: NotificationConfig, config_path: Path | None = None
) -> None:
    """Persist notification config to ``~/.dan/notifications.json``."""
    path = config_path or _default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.model_dump(), indent=2) + "\n", encoding="utf-8"
    )
