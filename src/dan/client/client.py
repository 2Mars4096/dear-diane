"""DanClient — async HTTP/WebSocket client for dan-serve gateway."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator

import httpx

from .errors import (
    ConnectionError,
    DanClientError,
    DispatchError,
    NotFoundError,
    RunLostError,
    ServerError,
)
from .models import ActivitySnapshot, CancelResult, DispatchResult, PendingInput, RunSummary

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8000"
_PING_TIMEOUT = 3.0
_MAX_RECONNECT_RETRIES = 5


class DanClient:
    """Async client for communicating with dan-serve gateway API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = (
            base_url
            or os.environ.get("DAN_SERVER_URL")
            or _DEFAULT_URL
        ).rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            headers: dict[str, str] = {}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers=headers,
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def __aenter__(self) -> "DanClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ── Server discovery ────────────────────────────────────────────

    async def ping(self) -> bool:
        try:
            http = await self._get_http()
            resp = await http.get("/health", timeout=_PING_TIMEOUT)
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException, OSError):
            return False

    async def is_server_available(self) -> bool:
        return await self.ping()

    # ── Dispatch ────────────────────────────────────────────────────

    async def dispatch(
        self,
        workflow_id: str | None = None,
        workflow_path: str | None = None,
        inputs: dict[str, Any] | None = None,
        text: str | None = None,
        surface_id: str | None = None,
        auto_approve: bool = False,
        use_meta: bool = False,
        config_overrides: dict[str, Any] | None = None,
        human_timeout: int | None = None,
    ) -> DispatchResult:
        body: dict[str, Any] = {}
        if workflow_id is not None:
            body["workflow_id"] = workflow_id
        if workflow_path is not None:
            body["workflow_path"] = workflow_path
        if inputs is not None:
            body["inputs"] = inputs
        if text is not None:
            body["text"] = text
        if surface_id is not None:
            body["surface_id"] = surface_id
        if auto_approve:
            body["auto_approve"] = True
        if use_meta:
            body["use_meta"] = True
        if config_overrides is not None:
            body["config_overrides"] = config_overrides
        if human_timeout is not None:
            body["human_timeout"] = human_timeout

        resp = await self._post("/api/gateway/dispatch", body)
        return DispatchResult.model_validate(resp)

    # ── Run lifecycle ───────────────────────────────────────────────

    async def get_run_status(self, run_id: str) -> dict[str, Any]:
        return await self._get(f"/api/runs/{run_id}")

    async def list_runs(self) -> list[RunSummary]:
        data = await self._get("/api/runs")
        runs_raw = data.get("runs", [])
        return [RunSummary.model_validate(r) for r in runs_raw]

    async def cancel_run(self, run_id: str) -> bool:
        resp = await self._post("/api/gateway/cancel", {"run_id": run_id})
        return resp.get("cancelled", False)

    async def resume_run(
        self, run_id: str, graph_id: str | None = None
    ) -> DispatchResult:
        body: dict[str, Any] = {}
        if graph_id:
            body["graph_id"] = graph_id
        resp = await self._post(f"/api/runs/{run_id}/resume", body)
        return DispatchResult(
            run_id=resp.get("run_id", run_id),
            workflow_name=resp.get("graph_id", ""),
            status=resp.get("status", ""),
        )

    async def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/api/runs/{run_id}/events")

    async def get_checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        data = await self._get(f"/api/runs/{run_id}/checkpoints")
        return data if isinstance(data, list) else data.get("checkpoints", [])

    # ── Event streaming ─────────────────────────────────────────────

    async def subscribe_run(self, run_id: str) -> AsyncIterator[dict[str, Any]]:
        import websockets

        ws_url = self._base_url.replace("http://", "ws://").replace(
            "https://", "wss://"
        )
        url = f"{ws_url}/api/runs/{run_id}/events"

        retries = 0
        while retries < _MAX_RECONNECT_RETRIES:
            try:
                async with websockets.connect(url) as ws:
                    received_any = False
                    async for msg in ws:
                        received_any = True
                        data = json.loads(msg)
                        if isinstance(data, dict) and data.get("event_type") == "_catchup":
                            emitted_pending_ids: set[str] = set()
                            pending_events = [
                                evt
                                for evt in (data.get("pending_human_inputs") or [])
                                if isinstance(evt, dict)
                            ]
                            pending_req_ids: set[str] = set()
                            for evt in pending_events:
                                evt_data = evt.get("data")
                                if isinstance(evt_data, dict):
                                    req_id = evt_data.get("request_id")
                                    if req_id:
                                        pending_req_ids.add(req_id)
                            replay = list(data.get("buffered_events") or [])
                            replay.extend(pending_events)
                            for evt in replay:
                                if not isinstance(evt, dict):
                                    continue
                                evt_type = evt.get("event_type")
                                req_id = None
                                evt_data = evt.get("data")
                                if isinstance(evt_data, dict):
                                    req_id = evt_data.get("request_id")
                                if evt_type == "human_input_needed" and req_id:
                                    # Replay only currently pending prompts.
                                    if req_id not in pending_req_ids:
                                        continue
                                    if req_id in emitted_pending_ids:
                                        continue
                                    emitted_pending_ids.add(req_id)
                                yield evt
                                if evt_type in (
                                    "run_completed",
                                    "run_failed",
                                    "run_cancelled",
                                ):
                                    return
                            continue
                        yield data
                        if data.get("event_type") in (
                            "run_completed",
                            "run_failed",
                            "run_cancelled",
                        ):
                            return
                retries += 1
                if retries >= _MAX_RECONNECT_RETRIES:
                    raise RunLostError(run_id)
                if received_any:
                    logger.warning(
                        "Run stream closed before terminal event for %s; reconnecting (%s/%s)",
                        run_id,
                        retries,
                        _MAX_RECONNECT_RETRIES,
                    )
                await asyncio.sleep(min(2 ** retries, 30))
            except (
                websockets.exceptions.ConnectionClosed,
                OSError,
                asyncio.TimeoutError,
            ):
                retries += 1
                if retries >= _MAX_RECONNECT_RETRIES:
                    raise RunLostError(run_id)
                await asyncio.sleep(min(2 ** retries, 30))

    async def subscribe_global(
        self,
        surface_filter: str | None = None,
        workflow_filter: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        import websockets

        ws_url = self._base_url.replace("http://", "ws://").replace(
            "https://", "wss://"
        )
        params = []
        if surface_filter:
            params.append(f"surface={surface_filter}")
        if workflow_filter:
            params.append(f"workflow={workflow_filter}")
        qs = f"?{'&'.join(params)}" if params else ""
        url = f"{ws_url}/api/gateway/events{qs}"

        async with websockets.connect(url) as ws:
            async for msg in ws:
                yield json.loads(msg)

    # ── HumanNode ───────────────────────────────────────────────────

    async def get_pending_inputs(self) -> list[PendingInput]:
        data = await self._get("/api/gateway/pending-inputs")
        return [PendingInput.model_validate(d) for d in data]

    async def submit_human_input(
        self,
        run_id: str,
        request_id: str,
        response: dict[str, Any],
        responder_surface: str | None = None,
    ) -> bool:
        body: dict[str, Any] = {
            "run_id": run_id,
            "request_id": request_id,
            "response": response,
        }
        if responder_surface:
            body["responder_surface"] = responder_surface
        try:
            await self._post("/api/gateway/submit-input", body)
            return True
        except NotFoundError:
            return False

    # ── Activity ────────────────────────────────────────────────────

    async def get_activity(self) -> ActivitySnapshot:
        data = await self._get("/api/gateway/activity")
        return ActivitySnapshot.model_validate(data)

    async def register_surface(
        self, surface_id: str, surface_type: str
    ) -> None:
        await self._post(
            "/api/gateway/surfaces/register",
            {"surface_id": surface_id, "surface_type": surface_type},
        )

    # ── HTTP helpers ────────────────────────────────────────────────

    async def _get(self, path: str) -> Any:
        http = await self._get_http()
        try:
            resp = await http.get(path)
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            raise ConnectionError(f"Cannot reach server: {exc}") from exc
        return self._handle_response(resp)

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        http = await self._get_http()
        try:
            resp = await http.post(path, json=body)
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            raise ConnectionError(f"Cannot reach server: {exc}") from exc
        return self._handle_response(resp)

    def _handle_response(self, resp: httpx.Response) -> Any:
        if resp.status_code == 404:
            raise NotFoundError(resp.text)
        if 400 <= resp.status_code < 500:
            raise DispatchError(resp.text, resp.status_code)
        if resp.status_code >= 500:
            raise ServerError(resp.text)
        try:
            return resp.json()
        except Exception:
            if resp.text:
                return {"raw": resp.text}
            return {}
