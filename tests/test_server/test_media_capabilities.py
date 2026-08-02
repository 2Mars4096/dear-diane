from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from dan.server.capability_handlers import (
    handle_audio_transcribe,
    handle_image_describe,
    register_base_capabilities,
)
from dan.server.capability_registry import CapabilityContext, ChatCapabilityRegistry


class TestMediaCapabilities:
    @pytest.mark.asyncio
    async def test_handle_image_describe_forwards_result(self, tmp_path):
        image = tmp_path / "figure.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")

        with patch(
            "dan.tools.image_describe.image_describe",
            new=AsyncMock(return_value={"description": "A line chart."}),
        ) as mock_tool:
            result = await handle_image_describe(
                {"path": str(image), "question": "What is shown?"},
                CapabilityContext(workflow_id="wf"),
            )

        assert result.success is True
        assert "line chart" in result.message.lower()
        mock_tool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_audio_transcribe_forwards_result(self, tmp_path):
        audio = tmp_path / "note.wav"
        audio.write_bytes(b"RIFF0000WAVE")

        with patch(
            "dan.tools.audio_transcribe.audio_transcribe",
            new=AsyncMock(
                return_value={
                    "text": "hello world",
                    "language_detected": "en",
                    "duration_seconds": 1.23,
                }
            ),
        ) as mock_tool:
            result = await handle_audio_transcribe(
                {"path": str(audio)},
                CapabilityContext(workflow_id="wf"),
            )

        assert result.success is True
        assert result.message == "hello world"
        assert result.data["duration_seconds"] == 1.23
        mock_tool.assert_awaited_once()

    def test_base_registry_registers_media_tools(self):
        reg = ChatCapabilityRegistry()
        register_base_capabilities(reg)
        names = reg.list_tool_names()
        assert "image_describe" in names
        assert "audio_transcribe" in names
