"""Webhook callback channel — POST JSON payload to a user-configured URL."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class WebhookNotifier:
    """POST notification payloads to a webhook URL."""

    def __init__(self, config: Any) -> None:
        self._url: str = config.url
        self._headers: dict[str, str] = dict(config.headers) if config.headers else {}
        self._timeout: float = config.timeout
        self._event_types: list[str] = list(config.event_types) if config.event_types else []

    async def notify(self, event: dict[str, Any]) -> None:
        if not self._url:
            return
        event_type = event.get("event_type", "")
        if self._event_types and event_type not in self._event_types:
            return
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        payload = {
            "event_type": event_type,
            "run_id": event.get("run_id", ""),
            "workflow_name": event.get("workflow_name") or event.get("workflow_id", ""),
            "status": data.get("status", event_type),
            "message": self._build_message(event),
            "timestamp": event.get("timestamp", time.time()),
            "surface_id": event.get("surface_id"),
        }
        await self._post(payload)

    def _build_message(self, event: dict[str, Any]) -> str:
        event_type = event.get("event_type", "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if event_type == "run_completed":
            return "Run completed successfully"
        if event_type == "run_failed":
            error = event.get("error") or data.get("error")
            if not error:
                errors = data.get("errors")
                if isinstance(errors, list) and errors:
                    first = errors[0]
                    if isinstance(first, dict):
                        error = first.get("message") or str(first)
                    else:
                        error = str(first)
            return f"Run failed: {error or 'unknown'}"
        if event_type == "human_input_needed":
            return f"Input needed: {data.get('prompt', '')}"
        return event_type

    async def _post(self, payload: dict[str, Any], retries: int = 1) -> None:
        import httpx

        for attempt in range(1 + retries):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(
                        self._url, json=payload, headers=self._headers
                    )
                    if resp.status_code < 400:
                        return
                    logger.warning(
                        "Webhook returned %d on attempt %d",
                        resp.status_code,
                        attempt + 1,
                    )
                    if attempt < retries:
                        await asyncio.sleep(1.0 * (attempt + 1))
            except Exception:
                if attempt == retries:
                    logger.debug(
                        "Webhook POST failed after %d attempts",
                        attempt + 1,
                        exc_info=True,
                    )
                else:
                    await asyncio.sleep(1.0 * (attempt + 1))
