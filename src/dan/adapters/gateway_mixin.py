"""Gateway mixin for messaging adapters.

Provides server-mode dispatch, event relay, and HumanNode bridging
for any adapter that extends GatewayAdapterMixin.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

PROGRESS_THROTTLE_SECONDS = 5.0


class GatewayAdapterMixin:
    """Mixin that adds server-mode dispatch to messaging adapters.
    
    Requires the adapter to implement:
    - send_message(session_id: str, text: str) -> None
    - adapter_config: AdapterConfig property
    """

    _dan_client: Any | None = None

    async def init_gateway_client(self) -> str:
        """Initialize DanClient. Returns 'server' or 'local'."""
        from dan.client.local import DanClientOrLocal

        config = self.adapter_config  # type: ignore[attr-defined]
        self._dan_client = DanClientOrLocal(
            server_url=config.server_url,
            force_local=config.local_mode,
        )
        mode = await self._dan_client.detect_mode()
        self._pending_requests: dict[str, dict[str, Any]] = {}
        self._relay_tasks: set[asyncio.Task] = set()
        if mode == "server":
            surface_id = getattr(self, "surface_id", f"adapter-{id(self)}")
            surface_type = getattr(self, "surface_type", "messaging")
            await self._dan_client.register_surface(surface_id, surface_type)
            logger.info("Adapter connected to dan-serve (server mode)")
        else:
            logger.info("Adapter running in local mode")
        return mode

    async def dispatch_via_gateway(
        self,
        session_id: str,
        workflow_path: str | None = None,
        workflow_id: str | None = None,
        inputs: dict[str, Any] | None = None,
        text: str | None = None,
    ) -> str | None:
        """Dispatch workflow via server and start event relay.
        
        Returns run_id if dispatch succeeded, None otherwise.
        """
        if self._dan_client is None or not self._dan_client.is_server_mode:
            return None

        surface_id = getattr(self, "surface_id", None)
        result = await self._dan_client.dispatch(
            workflow_path=workflow_path,
            workflow_id=workflow_id,
            inputs=inputs,
            text=text,
            surface_id=surface_id,
        )

        if not hasattr(self, "_relay_tasks"):
            self._relay_tasks: set[asyncio.Task] = set()
        task = asyncio.create_task(
            self._relay_events(session_id, result.run_id)
        )
        self._relay_tasks.add(task)
        task.add_done_callback(self._relay_tasks.discard)

        return result.run_id

    async def _relay_events(self, session_id: str, run_id: str) -> None:
        """Subscribe to run events and relay to messaging channel."""
        if self._dan_client is None:
            return

        last_progress = 0.0
        try:
            async for event in self._dan_client.subscribe_run(run_id):
                event_type = event.get("event_type", "")

                if event_type == "human_input_needed":
                    await self._handle_human_input_event(session_id, run_id, event)

                elif event_type == "node_started":
                    import time
                    now = time.time()
                    if now - last_progress >= PROGRESS_THROTTLE_SECONDS:
                        node_id = event.get("node_id", "?")
                        await self._send_message_safe(
                            session_id, f"⏳ Processing: {node_id}"
                        )
                        last_progress = now

                elif event_type == "run_completed":
                    data = event.get("data", {})
                    result_text = str(data.get("result", "Done"))
                    if len(result_text) > 4000:
                        result_text = result_text[:4000] + "... (truncated)"
                    await self._send_message_safe(session_id, f"✅ {result_text}")

                elif event_type == "run_failed":
                    error = (event.get("data") or {}).get("error", "Unknown error")
                    await self._send_message_safe(
                        session_id, f"❌ Run failed: {error}"
                    )

                elif event_type == "run_cancelled":
                    await self._send_message_safe(
                        session_id, "🚫 Run cancelled"
                    )

                if event_type in ("run_completed", "run_failed", "run_cancelled"):
                    break

        except (OSError, asyncio.TimeoutError) as exc:
            logger.error("Event relay connection error for run %s: %s", run_id, exc)
            await self._send_message_safe(
                session_id, f"⚠️ Connection to server lost: {exc}"
            )
        except Exception as exc:
            logger.error("Unexpected error in event relay for run %s: %s", run_id, exc, exc_info=True)
            await self._send_message_safe(
                session_id, f"⚠️ Error processing run events: {exc}"
            )

    async def _handle_human_input_event(
        self, session_id: str, run_id: str, event: dict[str, Any]
    ) -> None:
        """Handle a human_input_needed event from the server."""
        data = event.get("data", {})
        request_id = data.get("request_id")
        prompt = data.get("prompt", "Input required:")
        
        from dan.adapters.base import format_prompt_for_messaging
        from dan.engine.executor import HumanRenderRequest
        
        try:
            request = HumanRenderRequest(
                prompt=prompt,
                render_mode=data.get("render_mode", "text"),
                schema=data.get("schema"),
                options=data.get("options"),
                node_id=event.get("node_id"),
            )
            formatted = format_prompt_for_messaging(request)
        except Exception:
            formatted = prompt

        await self._send_message_safe(session_id, formatted)
        
        if not hasattr(self, "_pending_requests"):
            self._pending_requests: dict[str, dict[str, Any]] = {}
        self._pending_requests[session_id] = {
            "run_id": run_id,
            "request_id": request_id,
        }

    async def handle_gateway_response(
        self, session_id: str, response_text: str
    ) -> bool:
        """Handle a user response that might be for a pending HumanNode request.
        
        Returns True if the response was handled (submitted to server),
        False if there's no pending request for this session.
        """
        pending = self._pending_requests.pop(session_id, None)
        if pending is None or self._dan_client is None:
            return False

        run_id = pending["run_id"]
        request_id = pending["request_id"]
        
        await self._dan_client.submit_human_input(
            run_id, request_id, {"response": response_text}
        )
        return True

    async def cancel_gateway_run(self, session_id: str, run_id: str) -> bool:
        """Cancel a server-managed run."""
        if self._dan_client is None or not self._dan_client.is_server_mode:
            return False
        return await self._dan_client.cancel_run(run_id)

    async def _send_message_safe(self, session_id: str, text: str) -> None:
        """Send a message, catching exceptions."""
        try:
            if hasattr(self, "send_message"):
                await self.send_message(session_id, text)  # type: ignore[attr-defined]
            elif hasattr(self, "adapter") and hasattr(self.adapter, "send_result"):  # type: ignore[attr-defined]
                await self.adapter.send_result(session_id, text)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.error("Failed to send message to %s: %s", session_id, exc)

    async def close_gateway(self) -> None:
        """Close the DanClient connection."""
        if self._dan_client:
            await self._dan_client.close()
