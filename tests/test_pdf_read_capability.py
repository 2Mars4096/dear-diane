from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

from dan.server.capability_handlers import PDF_READ_CAPABILITY_SCHEMA, handle_pdf_read
from dan.server.capability_registry import CapabilityContext
from dan.tools.pdf_read import pdf_read


def test_pdf_read_capability_schema_exposes_vision_options():
    properties = PDF_READ_CAPABILITY_SCHEMA["function"]["parameters"]["properties"]

    assert "mode" in properties
    assert "start_page" in properties
    assert "end_page" in properties
    assert "vision_model" in properties
    assert "vision_prompt" in properties


@pytest.mark.asyncio
async def test_handle_pdf_read_forwards_vision_args_to_tool(tmp_path, monkeypatch):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfake\n")

    calls: dict[str, object] = {}
    pdf_tool_module = importlib.import_module("dan.tools.pdf_read")

    async def fake_pdf_read(**kwargs):
        calls.update(kwargs)
        return {
            "text": "vision output",
            "num_pages": 3,
            "metadata": {"title": "Paper"},
            "mode": "vision",
            "pages_requested": 3,
            "pages_returned": 3,
            "truncated": False,
        }

    class FakePdfReader:
        def __init__(self, _path):
            self.pages = [SimpleNamespace(extract_text=lambda: "text")]
            self.metadata = SimpleNamespace(title="Paper", author="Author", subject=None)

    monkeypatch.setattr(pdf_tool_module, "read_pdf_file", fake_pdf_read)
    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakePdfReader))

    result = await handle_pdf_read(
        {
            "path": str(pdf_path),
            "mode": "vision",
            "start_page": 2,
            "end_page": 5,
            "vision_model": "gpt-4o",
            "vision_prompt": "Describe the figures only.",
        },
        CapabilityContext(workflow_id=""),
    )

    assert result.success is True
    assert calls["resolved_path"] == str(pdf_path)
    assert calls["mode"] == "vision"
    assert calls["start_page"] == 2
    assert calls["end_page"] == 5
    assert calls["vision_model"] == "gpt-4o"
    assert calls["vision_prompt"] == "Describe the figures only."
    assert result.data["mode"] == "vision"


@pytest.mark.asyncio
async def test_pdf_read_vision_reports_page_truncation(tmp_path, monkeypatch):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfake\n")
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("DAN_OPENAI_API_KEY", "test-key")

    class FakePixmap:
        def tobytes(self, _format):
            return b"png-bytes"

    class FakePage:
        def get_pixmap(self, dpi=150, alpha=False):
            return FakePixmap()

    class FakeDoc:
        metadata = {"title": "Long Paper"}

        def __len__(self):
            return 30

        def __getitem__(self, _idx):
            return FakePage()

        def close(self):
            return None

    class FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="page description"))],
            )

    class FakeAsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=lambda _path: FakeDoc()))
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))

    result = await pdf_read("paper.pdf", mode="vision")

    assert result["mode"] == "vision"
    assert result["pages_requested"] == 30
    assert result["pages_returned"] == 25
    assert result["truncated"] is True
    assert "25 of 30" in result["warning"]


@pytest.mark.asyncio
async def test_pdf_read_vision_uses_dan_llm_base_url_without_openai_key(tmp_path, monkeypatch):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfake\n")
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("DAN_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DAN_LLM_API_KEY", "dan-key")
    monkeypatch.setenv("DAN_LLM_BASE_URL", "https://llm.example.test/v1")

    captured: dict[str, str] = {}

    class FakePixmap:
        def tobytes(self, _format):
            return b"png-bytes"

    class FakePage:
        def get_pixmap(self, dpi=150, alpha=False):
            return FakePixmap()

    class FakeDoc:
        metadata = {}

        def __len__(self):
            return 1

        def __getitem__(self, _idx):
            return FakePage()

        def close(self):
            return None

    class FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="page description"))],
            )

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=lambda _path: FakeDoc()))
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))

    await pdf_read("paper.pdf", mode="vision")

    assert captured["api_key"] == "dan-key"
    assert captured["base_url"] == "https://llm.example.test/v1"


@pytest.mark.asyncio
async def test_pdf_read_text_reports_actual_pages_returned(tmp_path, monkeypatch):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfake\n")
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(tmp_path))

    class FakePage:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class FakePdfReader:
        def __init__(self, _path):
            self.pages = [FakePage("one"), FakePage("two")]
            self.metadata = SimpleNamespace(title="Short Paper", author="Author", subject=None)

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakePdfReader))

    result = await pdf_read("paper.pdf", mode="text", start_page=0, end_page=5)

    assert result["pages_requested"] == 5
    assert result["pages_returned"] == 2
