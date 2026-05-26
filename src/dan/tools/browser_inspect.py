"""Built-in tool: inspect browser page metadata, HTML, and interactive elements."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "browser_inspect",
    "description": (
        "Inspect the current browser page: URL, title, tabs, optional HTML, and "
        "structured metadata for interactive elements with selectors and bounding boxes. "
        "Use this before direct element actions when the selector is not already known."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "Optional CSS selector limiting inspection to part of the page.",
            },
            "include_html": {
                "type": "boolean",
                "default": False,
                "description": "Whether to include raw current HTML for the page or selected element.",
            },
            "include_elements": {
                "type": "boolean",
                "default": True,
                "description": "Whether to include parsed interactive element metadata.",
            },
            "max_html_length": {
                "type": "integer",
                "default": 50000,
                "description": "Maximum HTML characters to return.",
            },
            "element_limit": {
                "type": "integer",
                "default": 100,
                "description": "Maximum interactive elements to return.",
            },
        },
        "required": [],
    },
    "examples": [
        {
            "input": {"include_elements": True},
            "output": {
                "url": "https://example.com",
                "title": "Example",
                "elements": [{"selector": "button:nth-of-type(1)", "text": "Submit"}],
            },
        },
    ],
    "category": "browser",
    "returns": "dict with page metadata, optional html, and interactive element metadata",
}


async def browser_inspect(
    *,
    selector: str | None = None,
    include_html: bool = False,
    include_elements: bool = True,
    max_html_length: int = 50000,
    element_limit: int = 100,
    **_kwargs: object,
) -> dict:
    from dan.tools._browser_session import get_controller

    ctrl = await get_controller()
    return await ctrl.inspect_dom(
        selector=selector,
        include_html=bool(include_html),
        include_elements=bool(include_elements),
        max_html_length=int(max_html_length),
        element_limit=int(element_limit),
    )
