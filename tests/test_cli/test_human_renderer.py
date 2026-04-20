"""Unit tests for CLIHumanRenderer — interactive HumanNode rendering."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from dan.cli.run import CLIHumanRenderer
from dan.engine.executor import HumanRenderRequest, HumanRenderResponse


@pytest.fixture
def renderer():
    return CLIHumanRenderer(timeout=5)


class TestCLIHumanRenderer:
    @pytest.mark.asyncio
    async def test_protocol_conformance(self, renderer):
        """Renderer satisfies the HumanRenderer protocol."""
        from dan.engine.executor import HumanRenderer
        assert isinstance(renderer, HumanRenderer)

    @pytest.mark.asyncio
    async def test_approval_yes(self, renderer):
        req = HumanRenderRequest(
            request_id="r1",
            render_mode="approval",
            prompt="Approve this plan?",
        )
        with patch.object(renderer, "_input_with_timeout", return_value="y"):
            resp = await renderer.render(req)
        assert resp.request_id == "r1"
        assert resp.data["approved"] is True
        assert resp.source == "human"

    @pytest.mark.asyncio
    async def test_approval_no(self, renderer):
        req = HumanRenderRequest(
            request_id="r2",
            render_mode="approval",
            prompt="Approve?",
        )
        with patch.object(renderer, "_input_with_timeout", return_value="n"):
            resp = await renderer.render(req)
        assert resp.data["approved"] is False

    @pytest.mark.asyncio
    async def test_approval_default_on_timeout(self, renderer):
        req = HumanRenderRequest(
            request_id="r3",
            render_mode="approval",
            prompt="Approve?",
            default_action="y",
        )
        with patch.object(renderer, "_input_with_timeout", return_value=None):
            resp = await renderer.render(req)
        assert resp.data["approved"] is True

    @pytest.mark.asyncio
    async def test_selection(self, renderer):
        req = HumanRenderRequest(
            request_id="r4",
            render_mode="selection",
            prompt="Pick one:",
            options=["Alpha", "Beta", "Gamma"],
        )
        with patch.object(renderer, "_input_with_timeout", return_value="2"):
            resp = await renderer.render(req)
        assert resp.data["response"] == "Beta"

    @pytest.mark.asyncio
    async def test_selection_default_on_timeout(self, renderer):
        req = HumanRenderRequest(
            request_id="r5",
            render_mode="selection",
            prompt="Pick one:",
            options=["A", "B"],
            default_action="A",
        )
        with patch.object(renderer, "_input_with_timeout", return_value=None):
            resp = await renderer.render(req)
        assert resp.data["response"] == "A"

    @pytest.mark.asyncio
    async def test_text_input(self, renderer):
        req = HumanRenderRequest(
            request_id="r6",
            render_mode="text",
            prompt="Enter something:",
        )
        with patch.object(renderer, "_input_with_timeout", return_value="hello world"):
            resp = await renderer.render(req)
        assert resp.data["response"] == "hello world"
        assert resp.source == "human"

    @pytest.mark.asyncio
    async def test_text_timeout_with_default(self, renderer):
        req = HumanRenderRequest(
            request_id="r7",
            render_mode="text",
            prompt="Enter:",
            default_action="fallback",
        )
        with patch.object(renderer, "_input_with_timeout", return_value=None):
            resp = await renderer.render(req)
        assert resp.data["response"] == "fallback"
        assert resp.source == "default"

    @pytest.mark.asyncio
    async def test_form_input(self, renderer):
        req = HumanRenderRequest(
            request_id="r8",
            render_mode="form",
            prompt="Fill in:",
            output_schema={
                "type": "object",
                "properties": {
                    "name": {"description": "Your name"},
                    "age": {"description": "Your age"},
                },
            },
        )
        responses = iter(["Alice", "30"])
        with patch.object(renderer, "_input_with_timeout", side_effect=lambda *a: asyncio.coroutine(lambda: next(responses))()):
            with patch.object(renderer, "_input_with_timeout", side_effect=["Alice", "30"]):
                resp = await renderer.render(req)
        assert resp.data.get("name") == "Alice"
        assert resp.data.get("age") == "30"

    @pytest.mark.asyncio
    async def test_eof_with_default(self, renderer):
        req = HumanRenderRequest(
            request_id="r9",
            render_mode="text",
            prompt="Input:",
            default_action="safe",
        )
        with patch.object(renderer, "_input_with_timeout", side_effect=EOFError):
            resp = await renderer.render(req)
        assert resp.data["response"] == "safe"
        assert resp.source == "default"
