"""Built-in tool: extract text content from the browser page."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "browser_extract",
    "description": (
        "Extract text content from the current browser page or a specific element. "
        "When no selector is provided, returns the full page body text. "
        "Useful for reading posts, articles, feeds, or any visible content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS selector of the element to extract from (optional — omit for full page).",
            },
            "max_length": {
                "type": "integer",
                "description": "Maximum characters to return.",
                "default": 50000,
            },
        },
        "required": [],
    },
    "examples": [
        {
            "input": {"selector": "article.post"},
            "output": {"text": "This is the post content...", "length": 28},
        },
        {
            "input": {},
            "output": {"text": "Full page body text...", "length": 22},
        },
    ],
    "category": "browser",
    "returns": "dict with text content and length",
}


async def browser_extract(
    *, selector: str | None = None, max_length: int = 50000, **_kwargs: object
) -> dict:
    from diane.tools._browser_session import get_controller

    ctrl = await get_controller()
    text = await ctrl.extract_text(selector)
    original_length = len(text)
    truncated = original_length > max_length
    if truncated:
        text = text[:max_length] + "... [truncated]"
    session_info = getattr(ctrl, "session_info", lambda: {})()
    return {"text": text, "length": original_length, "truncated": truncated, "session": session_info}
