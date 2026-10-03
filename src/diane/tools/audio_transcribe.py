"""Built-in tool: transcribe audio/voice files using OpenAI Whisper."""

from __future__ import annotations

import logging
import os

from diane.tools._workspace import validate_path

logger = logging.getLogger(__name__)

TOOL_METADATA = {
    "tool_id": "audio_transcribe",
    "description": (
        "Transcribe an audio or voice file to text using OpenAI Whisper API. "
        "Supports MP3, WAV, M4A, OGG, WEBM, MP4, MPEG, MPGA. "
        "Requires DAN_OPENAI_API_KEY."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the audio file.",
            },
            "language": {
                "type": "string",
                "description": "Optional ISO-639-1 language code (e.g. 'en', 'zh', 'es').",
            },
            "model": {
                "type": "string",
                "description": "Whisper model to use.",
                "default": "whisper-1",
            },
        },
        "required": ["path"],
    },
    "examples": [
        {
            "input": {"path": "voice_note.ogg"},
            "output": {"text": "Hey, can you send me the latest report?", "language_detected": "en"},
        },
    ],
    "category": "media",
    "returns": "dict with text, language_detected",
}

_SUPPORTED_TYPES = {".mp3", ".wav", ".m4a", ".ogg", ".webm", ".mp4", ".mpeg", ".mpga"}
MAX_AUDIO_SIZE = 25_000_000  # 25 MB (Whisper limit)


async def audio_transcribe(
    path: str,
    language: str | None = None,
    model: str = "whisper-1",
    **_kwargs,
) -> dict:
    resolved = validate_path(path)

    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"Audio file not found: '{path}'")

    ext = os.path.splitext(resolved)[1].lower()
    if ext not in _SUPPORTED_TYPES:
        raise ValueError(f"Unsupported audio format '{ext}'. Supported: {_SUPPORTED_TYPES}")

    size = os.path.getsize(resolved)
    if size > MAX_AUDIO_SIZE:
        raise ValueError(f"Audio file is {size:,} bytes (limit {MAX_AUDIO_SIZE:,}).")

    api_key = os.environ.get("DAN_OPENAI_API_KEY") or os.environ.get("DAN_LLM_API_KEY")
    if not api_key:
        raise RuntimeError("No API key for Whisper. Set DAN_OPENAI_API_KEY.")

    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise RuntimeError("openai package required for audio_transcribe. Install: pip install openai")

    client = AsyncOpenAI(api_key=api_key)

    with open(resolved, "rb") as f:
        kwargs = {"model": model, "file": f, "response_format": "verbose_json"}
        if language:
            kwargs["language"] = language
        response = await client.audio.transcriptions.create(**kwargs)

    return {
        "text": response.text,
        "language_detected": getattr(response, "language", language or "unknown"),
        "duration_seconds": getattr(response, "duration", None),
    }
