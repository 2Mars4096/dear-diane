"""Tests for WhatsApp inbound media handling (plan 26-7)."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helpers ──────────────────────────────────────────────────────────

_ENV_CLEAR = {
    "DAN_WHISPER_API_KEY": "",
    "DAN_WHISPER_BASE_URL": "",
    "DAN_WHISPER_MODEL": "",
    "DAN_OPENAI_API_KEY": "",
    "DAN_LLM_API_KEY": "",
    "DAN_LLM_BASE_URL": "",
    "LLM_API_KEY": "",
}


class _WhisperHttpClient:
    """Concrete mock for httpx.AsyncClient used by transcribe_audio."""

    def __init__(self, status_code: int = 200, json_data: dict | None = None):
        self._json = json_data or {}
        self._status = status_code
        self.post_calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        resp = MagicMock()
        resp.status_code = self._status
        resp.json.return_value = self._json
        resp.text = str(self._json)
        return resp


# ── 1. transcribe_audio: Whisper env vars ────────────────────────────


@pytest.mark.asyncio
async def test_transcribe_audio_uses_whisper_env_vars(tmp_path):
    from dan.adapters.whatsapp_web_adapter import transcribe_audio

    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"fake audio data")

    mock_client = _WhisperHttpClient(200, {"text": "hello world"})

    with patch.dict(os.environ, {
        **_ENV_CLEAR,
        "DAN_WHISPER_API_KEY": "test-key",
        "DAN_WHISPER_BASE_URL": "http://localhost:8080/v1",
        "DAN_WHISPER_MODEL": "whisper-large-v3",
    }, clear=False):
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await transcribe_audio(str(audio))

    assert result == "hello world"
    url, kwargs = mock_client.post_calls[0]
    assert "localhost:8080" in url
    assert "audio/transcriptions" in url
    assert kwargs["data"]["model"] == "whisper-large-v3"


# ── 2. transcribe_audio: OpenAI fallback ─────────────────────────────


@pytest.mark.asyncio
async def test_transcribe_audio_falls_back_to_openai_key(tmp_path):
    from dan.adapters.whatsapp_web_adapter import transcribe_audio

    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"fake audio data")

    mock_client = _WhisperHttpClient(200, {"text": "transcribed"})

    with patch.dict(os.environ, {
        **_ENV_CLEAR,
        "DAN_OPENAI_API_KEY": "openai-key",
    }, clear=False):
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await transcribe_audio(str(audio))

    assert result == "transcribed"
    url, kwargs = mock_client.post_calls[0]
    assert "api.openai.com" in url
    assert "audio/transcriptions" in url
    assert "openai-key" in kwargs["headers"]["Authorization"]


# ── 3. transcribe_audio: LLM key fallback ────────────────────────────


@pytest.mark.asyncio
async def test_transcribe_audio_falls_back_to_llm_key(tmp_path):
    from dan.adapters.whatsapp_web_adapter import transcribe_audio

    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"fake audio data")

    mock_client = _WhisperHttpClient(200, {"text": "from proxy"})

    with patch.dict(os.environ, {
        **_ENV_CLEAR,
        "DAN_LLM_API_KEY": "llm-key",
        "DAN_LLM_BASE_URL": "http://myproxy.com/v1",
    }, clear=False):
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await transcribe_audio(str(audio))

    assert result == "from proxy"
    url, _ = mock_client.post_calls[0]
    assert "myproxy.com" in url
    assert "audio/transcriptions" in url


# ── 4. transcribe_audio: no key ──────────────────────────────────────


@pytest.mark.asyncio
async def test_transcribe_audio_returns_none_without_key(tmp_path):
    from dan.adapters.whatsapp_web_adapter import transcribe_audio

    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"fake audio data")

    with patch.dict(os.environ, _ENV_CLEAR, clear=False):
        result = await transcribe_audio(str(audio))

    assert result is None


# ── 5. transcribe_audio: API error ───────────────────────────────────


@pytest.mark.asyncio
async def test_transcribe_audio_returns_none_on_api_error(tmp_path):
    from dan.adapters.whatsapp_web_adapter import transcribe_audio

    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"fake audio data")

    mock_client = _WhisperHttpClient(500, {"error": "server error"})

    with patch.dict(os.environ, {
        **_ENV_CLEAR,
        "DAN_OPENAI_API_KEY": "test-key",
    }, clear=False):
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await transcribe_audio(str(audio))

    assert result is None


# ── 6. transcribe_audio: missing file ────────────────────────────────


@pytest.mark.asyncio
async def test_transcribe_audio_returns_none_for_missing_file():
    from dan.adapters.whatsapp_web_adapter import transcribe_audio

    with patch.dict(os.environ, {
        **_ENV_CLEAR,
        "DAN_OPENAI_API_KEY": "test-key",
    }, clear=False):
        result = await transcribe_audio("/nonexistent/path/audio.ogg")

    assert result is None


# ── Integration test helpers ─────────────────────────────────────────


class _FakeLoop:
    def add_signal_handler(self, *_args, **_kwargs) -> None:
        return None


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self) -> dict:
        return self._payload


_captured_posts: list[dict] = []


class _CapturingHttpClient:
    """Mock httpx.AsyncClient that captures POST /api/chat/message bodies."""

    def __init__(self, *args, **kwargs) -> None:
        self.base_url = kwargs.get("base_url", "")
        _captured_posts.clear()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def aclose(self) -> None:
        pass

    async def get(self, path: str):
        return _FakeResponse(status_code=404)

    async def post(self, path: str, json: dict | None = None):
        if path == "/api/chat/message":
            _captured_posts.append(json)
            return _FakeResponse(200, {"content": "OK"})
        if "graphs" in path:
            return _FakeResponse(200, {"id": "_scratch"})
        return _FakeResponse(200, {})


class _FakeAdapter:
    def __init__(self) -> None:
        self.callback = None
        self.prompts: list[tuple[str, str]] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def set_message_callback(self, callback) -> None:
        self.callback = callback

    async def send_prompt(self, session_id: str, prompt: str, _schema) -> None:
        self.prompts.append((session_id, prompt))

    async def send_file(self, session_id: str, path: str) -> dict:
        return {"ok": True}


# ── 7. Adapter: voice note → transcription → server ──────────────────


@pytest.mark.asyncio
async def test_adapter_handles_voice_note_prefix():
    from dan.cli.adapter import _run_adapter_chat_mode

    adapter = _FakeAdapter()
    config = SimpleNamespace(
        server_url="http://127.0.0.1:8000",
        error_message="Something went wrong.",
        auto_approve=False,
    )

    with patch("dan.cli.adapter.asyncio.get_running_loop", return_value=_FakeLoop()):
        with patch("httpx.AsyncClient", _CapturingHttpClient):
            with patch(
                "dan.adapters.whatsapp_web_adapter.transcribe_audio",
                new_callable=AsyncMock,
                return_value="check AAPL stock price",
            ):
                task = asyncio.create_task(
                    _run_adapter_chat_mode(adapter, config, "whatsapp-web")
                )
                try:
                    while adapter.callback is None:
                        await asyncio.sleep(0)
                    await adapter.callback("u1", "[Voice note: /tmp/voice.ogg]")
                    for _ in range(80):
                        await asyncio.sleep(0.01)
                        if _captured_posts:
                            break
                    assert _captured_posts, "Expected POST to /api/chat/message"
                    assert "check AAPL stock price" in _captured_posts[0]["message"]
                finally:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task


# ── 8. Adapter: voice note transcription failure ──────────────────────


@pytest.mark.asyncio
async def test_adapter_handles_voice_note_transcription_failure():
    from dan.cli.adapter import _run_adapter_chat_mode

    adapter = _FakeAdapter()
    config = SimpleNamespace(
        server_url="http://127.0.0.1:8000",
        error_message="Something went wrong.",
        auto_approve=False,
    )

    with patch("dan.cli.adapter.asyncio.get_running_loop", return_value=_FakeLoop()):
        with patch("httpx.AsyncClient", _CapturingHttpClient):
            with patch(
                "dan.adapters.whatsapp_web_adapter.transcribe_audio",
                new_callable=AsyncMock,
                return_value=None,
            ):
                task = asyncio.create_task(
                    _run_adapter_chat_mode(adapter, config, "whatsapp-web")
                )
                try:
                    while adapter.callback is None:
                        await asyncio.sleep(0)
                    await adapter.callback("u1", "[Voice note: /tmp/voice.ogg]")
                    for _ in range(80):
                        await asyncio.sleep(0.01)
                        if adapter.prompts:
                            break
                    assert adapter.prompts, "Expected error prompt to adapter"
                    assert "couldn't transcribe" in adapter.prompts[-1][1].lower()
                finally:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task


# ── 9. Adapter: attachment prefix extraction ──────────────────────────


@pytest.mark.asyncio
async def test_adapter_handles_attachment_prefix():
    from dan.cli.adapter import _run_adapter_chat_mode

    adapter = _FakeAdapter()
    config = SimpleNamespace(
        server_url="http://127.0.0.1:8000",
        error_message="Something went wrong.",
        auto_approve=False,
    )

    with patch("dan.cli.adapter.asyncio.get_running_loop", return_value=_FakeLoop()):
        with patch("httpx.AsyncClient", _CapturingHttpClient):
            task = asyncio.create_task(
                _run_adapter_chat_mode(adapter, config, "whatsapp-web")
            )
            try:
                while adapter.callback is None:
                    await asyncio.sleep(0)
                await adapter.callback(
                    "u1",
                    "[Attachment: /tmp/report.pdf]\nUser sent file: report.pdf",
                )
                for _ in range(80):
                    await asyncio.sleep(0.01)
                    if _captured_posts:
                        break
                assert _captured_posts, "Expected POST to /api/chat/message"
                body = _captured_posts[0]
                assert body.get("attachment_path") == "/tmp/report.pdf"
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task


# ── 10. Adapter: PDF triggers review prefix ──────────────────────────


@pytest.mark.asyncio
async def test_adapter_handles_pdf_attachment_triggers_review():
    from dan.cli.adapter import _run_adapter_chat_mode

    adapter = _FakeAdapter()
    config = SimpleNamespace(
        server_url="http://127.0.0.1:8000",
        error_message="Something went wrong.",
        auto_approve=False,
    )

    with patch("dan.cli.adapter.asyncio.get_running_loop", return_value=_FakeLoop()):
        with patch("httpx.AsyncClient", _CapturingHttpClient):
            task = asyncio.create_task(
                _run_adapter_chat_mode(adapter, config, "whatsapp-web")
            )
            try:
                while adapter.callback is None:
                    await asyncio.sleep(0)
                await adapter.callback(
                    "u1",
                    "[Attachment: /tmp/report.pdf]\nUser sent file: report.pdf",
                )
                for _ in range(80):
                    await asyncio.sleep(0.01)
                    if _captured_posts:
                        break
                assert _captured_posts, "Expected POST to /api/chat/message"
                body = _captured_posts[0]
                assert "Please review this PDF" in body["message"]
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
