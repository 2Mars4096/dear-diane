"""Backwards-compatibility shim — use ``dan.publish.runtime`` instead.

``PublishGatewayClient`` is superseded by ``GatewayRuntime`` / ``LocalRuntime``
via the unified ``PublishRuntime`` ABC.  This module re-exports the old name
so existing imports continue to work during the transition period.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
import warnings
from typing import Any

logger = logging.getLogger(__name__)


class PublishGatewayClient:
    """Deprecated — use :class:`dan.publish.runtime.GatewayRuntime` instead.

    Kept for backwards compatibility with code that imports
    ``from dan.publish.gateway_mode import PublishGatewayClient``.
    """

    def __init__(
        self,
        server_url: str | None = None,
        force_local: bool = False,
        engine_config: Any | None = None,
    ) -> None:
        warnings.warn(
            "PublishGatewayClient is deprecated; use PublishRuntime / "
            "create_publish_runtime() from dan.publish.runtime instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._server_url = server_url
        self._force_local = force_local
        self._engine_config = engine_config
        self._client: Any | None = None
        self._is_server: bool = False
        self._surface_id: str = f"mcp-publish-{uuid.uuid4().hex[:6]}"
        self._init_lock = asyncio.Lock()

    async def init(self) -> str:
        from dan.client.local import DanClientOrLocal

        self._client = DanClientOrLocal(
            server_url=self._server_url,
            force_local=self._force_local,
            engine_config=self._engine_config,
        )
        mode = await self._client.detect_mode()
        self._is_server = mode == "server"

        if self._is_server:
            await self._client.register_surface(
                surface_id=self._surface_id,
                surface_type="mcp",
            )
        return mode

    async def _ensure_init(self) -> None:
        if self._client is not None:
            return
        async with self._init_lock:
            if self._client is None:
                await self.init()

    @property
    def is_server_mode(self) -> bool:
        return self._is_server

    async def dispatch_and_wait(self, **kwargs: Any) -> dict[str, Any]:
        await self._ensure_init()
        if not self._is_server or self._client is None:
            raise RuntimeError("Not in server mode")
        result = await self._client.dispatch(**kwargs, surface_id=self._surface_id)
        run_id = result.run_id
        final: dict[str, Any] = {}
        async for event in self._client.subscribe_run(run_id):
            etype = event.get("event_type", "")
            if etype == "run_completed":
                final = event.get("data", {})
                break
            elif etype == "run_failed":
                raise RuntimeError(f"Workflow failed: {(event.get('data') or {}).get('error', '')}")
            elif etype == "run_cancelled":
                raise RuntimeError("Workflow was cancelled")
        return {"run_id": run_id, "status": "completed", "result": final}

    async def dispatch_async(self, **kwargs: Any) -> str:
        await self._ensure_init()
        if not self._is_server or self._client is None:
            raise RuntimeError("Not in server mode")
        result = await self._client.dispatch(**kwargs, surface_id=self._surface_id)
        return result.run_id

    async def get_status(self, run_id: str) -> dict[str, Any]:
        await self._ensure_init()
        if not self._is_server or self._client is None:
            raise RuntimeError("Not in server mode")
        return await self._client.get_run_status(run_id)

    async def submit_input(self, run_id: str, request_id: str, response: dict[str, Any]) -> bool:
        await self._ensure_init()
        if not self._is_server or self._client is None:
            return False
        return await self._client.submit_human_input(run_id, request_id, response)

    async def cancel(self, run_id: str) -> bool:
        await self._ensure_init()
        if not self._is_server or self._client is None:
            return False
        return await self._client.cancel_run(run_id)

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None
