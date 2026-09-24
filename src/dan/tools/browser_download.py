"""Built-in tool: trigger a browser download and save it to a path."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "browser_download",
    "description": (
        "Trigger a file download from the current browser page, usually by clicking "
        "a CSS selector, and save it to a destination path. "
        "Useful for downloading PDFs from authenticated sites that require browser automation. "
        "GUI runs require a new destination path inside the project workspace. "
        "Standalone calls without a destination save under ~/.dan/downloads/."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS selector of the element that triggers the download.",
            },
            "destination_path": {
                "type": "string",
                "description": (
                    "Final path for the downloaded file; required in GUI runs and must be a "
                    "new file inside the workspace. Relative paths resolve against the workspace."
                ),
            },
            "timeout_seconds": {
                "type": "number",
                "description": "Maximum time to wait for the download to start and finish.",
                "default": 30.0,
            },
        },
        "required": ["selector"],
    },
    "examples": [
        {
            "input": {
                "selector": "a.download-pdf",
                "destination_path": "~/Dropbox/my-knowledge-base/static/papers/acemoglu2012network.pdf",
            },
            "output": {
                "downloaded": True,
                "path": "~/Dropbox/my-knowledge-base/static/papers/acemoglu2012network.pdf",
                "selector": "a.download-pdf",
            },
        },
    ],
    "category": "browser",
    "returns": "dict with downloaded, path, selector, and destination_path",
}


async def browser_download(
    *,
    selector: str,
    destination_path: str | None = None,
    timeout_seconds: float = 30.0,
    **_kwargs: object,
) -> dict:
    from dan.tools._browser_session import download_destination, get_controller

    destination_path = download_destination(destination_path)
    ctrl = await get_controller()
    path = await ctrl.download(
        selector,
        destination_path=destination_path,
        timeout=timeout_seconds,
    )
    return {
        "downloaded": path is not None,
        "path": path,
        "selector": selector,
        "destination_path": destination_path,
    }
