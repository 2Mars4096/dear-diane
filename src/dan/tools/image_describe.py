"""Built-in tool: describe image content using a vision-capable LLM."""

from __future__ import annotations

import base64
import logging
import mimetypes
import os

from dan.tools._workspace import validate_path

logger = logging.getLogger(__name__)

TOOL_METADATA = {
    "tool_id": "image_describe",
    "description": (
        "Describe or analyze an image using a vision-capable LLM. "
        "Supports JPEG, PNG, WebP, GIF. Optionally ask a specific question about the image. "
        "Requires a vision-capable model (GPT-4o, Claude with vision) and an API key."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the image file.",
            },
            "question": {
                "type": "string",
                "description": "Optional question about the image.",
                "default": "Describe this image in detail.",
            },
            "model": {
                "type": "string",
                "description": "Vision model to use.",
                "default": "gpt-4o",
            },
        },
        "required": ["path"],
    },
    "examples": [
        {
            "input": {"path": "chart.png", "question": "What trend does this chart show?"},
            "output": {"description": "The chart shows an upward trend in revenue over Q1-Q4 2025."},
        },
    ],
    "category": "media",
    "returns": "dict with description (str)",
}

_SUPPORTED_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_IMAGE_SIZE = 20_000_000  # 20 MB


async def image_describe(
    path: str,
    question: str = "Describe this image in detail.",
    model: str = "gpt-4o",
    **_kwargs,
) -> dict:
    resolved = validate_path(path)

    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"Image not found: '{path}'")

    ext = os.path.splitext(resolved)[1].lower()
    if ext not in _SUPPORTED_TYPES:
        raise ValueError(f"Unsupported image format '{ext}'. Supported: {_SUPPORTED_TYPES}")

    size = os.path.getsize(resolved)
    if size > MAX_IMAGE_SIZE:
        raise ValueError(f"Image is {size:,} bytes (limit {MAX_IMAGE_SIZE:,}).")

    with open(resolved, "rb") as f:
        image_data = f.read()

    b64 = base64.b64encode(image_data).decode("ascii")
    mime = mimetypes.guess_type(resolved)[0] or "image/png"

    api_key = os.environ.get("DAN_OPENAI_API_KEY") or os.environ.get("DAN_LLM_API_KEY")
    base_url = os.environ.get("DAN_LLM_BASE_URL", "https://api.openai.com/v1")

    if not api_key:
        raise RuntimeError(
            "No API key for vision model. Set DAN_OPENAI_API_KEY or DAN_LLM_API_KEY."
        )

    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise RuntimeError("openai package required for image_describe. Install: pip install openai")

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ],
        max_tokens=1024,
    )

    text = response.choices[0].message.content or ""
    return {"description": text.strip()}
