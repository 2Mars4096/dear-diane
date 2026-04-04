"""Async HTTP/WebSocket client for the DAN server API.

Part of the workflow generation quality evaluation system (Phase 33).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import websockets


class DanClient:
    def __init__(self, base_url: str = "http://localhost:8080"):
        self._base_url = base_url.rstrip("/")
        self._http: httpx.AsyncClient | None = None

    def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=60.0,
            )
        return self._http

    def _ws_url(self, path: str) -> str:
        scheme = "ws" if self._base_url.startswith("http://") else "wss"
        base = self._base_url.replace("http://", "").replace("https://", "")
        return f"{scheme}://{base}{path}"

    async def create_graph(self, graph_id: str, data: dict | None = None) -> dict:
        body = {"graph_id": graph_id, "data": data or {"nodes": [], "edges": []}}
        resp = await self._get_http().post("/api/graphs", json=body)
        resp.raise_for_status()
        return resp.json()

    async def get_graph(self, graph_id: str) -> dict | None:
        resp = await self._get_http().get(f"/api/graphs/{graph_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def validate_graph(self, graph_id: str) -> dict:
        resp = await self._get_http().post(f"/api/graphs/{graph_id}/validate")
        resp.raise_for_status()
        return resp.json()

    async def delete_graph(self, graph_id: str) -> bool:
        resp = await self._get_http().delete(f"/api/graphs/{graph_id}")
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return True

    async def send_message(
        self,
        workflow_id: str,
        message: str,
        *,
        mode: str = "agent",
        history: list[dict] | None = None,
        client_graph_revision: str | None = None,
        thread_id: str | None = None,
        surface_context: dict[str, Any] | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "workflow_id": workflow_id,
            "message": message,
            "mode": mode,
            "history": history or [],
            "client_graph_revision": client_graph_revision,
            "thread_id": thread_id,
            "surface_context": surface_context or {},
        }
        resp = await self._get_http().post("/api/chat/message", json=body)
        resp.raise_for_status()
        return resp.json()

    async def stream_events(
        self, channel_id: str, *, timeout: float = 120.0
    ) -> AsyncIterator[dict]:
        """Stream chat events. Follows chat_queued redirects and reconnects once on close."""
        import logging

        log = logging.getLogger(__name__)
        seen_channels: set[str] = set()
        next_channel: str | None = channel_id
        reconnect_attempts = 0

        while next_channel:
            current = next_channel
            next_channel = None
            if current in seen_channels:
                log.warning("chat_queued redirect loop detected at %s", current)
                break
            seen_channels.add(current)
            url = self._ws_url(f"/api/chat/{current}/events")
            try:
                async with websockets.connect(
                    url, close_timeout=5.0, ping_timeout=timeout
                ) as ws:
                    async for msg in ws:
                        data = json.loads(msg)
                        if data is None:
                            return  # Server sends null to signal end of stream
                        if data.get("type") == "chat_queued":
                            redir = str(
                                data.get("stream_channel_id") or ""
                            ).strip()
                            if redir and redir not in seen_channels:
                                next_channel = redir
                                log.debug("Following chat_queued to %s", redir)
                            yield data
                            if next_channel:
                                break
                            continue
                        yield data
                    if next_channel is None:
                        return
            except Exception as exc:
                if next_channel is not None:
                    raise
                if reconnect_attempts < 1:
                    log.warning("WebSocket closed, reconnecting: %s", exc)
                    reconnect_attempts += 1
                    next_channel = current
                    continue
                raise

    async def start_run(self, graph_id: str, inputs: dict | None = None) -> dict:
        body = {"graph_id": graph_id, "inputs": inputs}
        resp = await self._get_http().post("/api/runs", json=body)
        resp.raise_for_status()
        return resp.json()

    async def get_run(self, run_id: str) -> dict:
        resp = await self._get_http().get(f"/api/runs/{run_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_run_events(
        self, run_id: str, *, node_id: str | None = None
    ) -> list[dict]:
        params = {"node_id": node_id} if node_id else {}
        resp = await self._get_http().get(
            f"/api/runs/{run_id}/events", params=params
        )
        resp.raise_for_status()
        return resp.json().get("events", [])

    async def export_python(self, graph_id: str) -> str:
        resp = await self._get_http().get(
            f"/api/graphs/{graph_id}/export/python"
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("code", data.get("python", ""))

    async def export_markdown(self, graph_id: str) -> str:
        resp = await self._get_http().get(
            f"/api/graphs/{graph_id}/export/markdown"
        )
        resp.raise_for_status()
        data = resp.json()
        files = data.get("files", [])
        return "\n\n".join(f.get("content", "") for f in files)

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> DanClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
