"""Built-in tool: translate text between languages using an LLM."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

TOOL_METADATA = {
    "tool_id": "text_translate",
    "description": (
        "Translate text between languages using an LLM. "
        "Auto-detects the source language if not specified. "
        "Requires an OpenAI-compatible API key."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to translate.",
            },
            "target_language": {
                "type": "string",
                "description": "Target language (e.g., 'Spanish', 'Chinese', 'French').",
            },
            "source_language": {
                "type": "string",
                "description": "Source language. Omit to auto-detect.",
            },
            "model": {
                "type": "string",
                "description": "LLM model to use for translation.",
                "default": "gpt-4o-mini",
            },
        },
        "required": ["text", "target_language"],
    },
    "examples": [
        {
            "input": {"text": "Hello, how are you?", "target_language": "Spanish"},
            "output": {
                "translated": "Hola, ¿cómo estás?",
                "source_language": "English",
                "target_language": "Spanish",
            },
        },
    ],
    "category": "utility",
    "returns": "dict with translated (str), source_language (str), target_language (str)",
}


async def text_translate(
    text: str,
    target_language: str,
    source_language: str | None = None,
    model: str = "gpt-4o-mini",
    **_kwargs,
) -> dict:
    api_key = os.environ.get("DAN_OPENAI_API_KEY") or os.environ.get("DAN_LLM_API_KEY")
    base_url = os.environ.get("DAN_OPENAI_BASE_URL") or os.environ.get(
        "DAN_LLM_BASE_URL", "https://api.openai.com/v1"
    )

    if not api_key:
        raise RuntimeError(
            "No API key for translation. Set DAN_OPENAI_API_KEY or DAN_LLM_API_KEY."
        )

    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise RuntimeError(
            "openai package required for text_translate. Install: pip install openai"
        )

    source_clause = f" from {source_language}" if source_language else ""
    prompt = (
        f"Translate the following text{source_clause} to {target_language}. "
        "Return only the translation, nothing else.\n\n"
        f"{text}"
    )

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4096,
        temperature=0.3,
    )

    translated = (response.choices[0].message.content or "").strip()

    detected = source_language or "auto-detected"

    return {
        "translated": translated,
        "source_language": detected,
        "target_language": target_language,
    }
