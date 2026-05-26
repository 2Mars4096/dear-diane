"""Shared tool-catalog helpers for workflow generation prompts."""

from __future__ import annotations

from typing import Any

_PREFERRED_TOOL_IDS = [
    "web_search",
    "web_fetch",
    "file_read",
    "file_edit",
    "file_write",
    "csv_read",
    "pdf_read",
    "spreadsheet_read",
    "text_chunk",
    "send_email",
    "shell_command",
    "python_eval",
    "http_request",
    "list_directory",
    "image_describe",
    "audio_transcribe",
    "text_translate",
    "git_status",
    "notify",
    "browser_tabs",
    "browser_inspect",
    "browser_open",
    "browser_extract",
    "browser_download",
    "browser_screenshot",
    "desktop_observe",
]


def _load_tools() -> dict[str, tuple[Any, dict]]:
    from dan.tools import get_all_tools

    return get_all_tools()


def get_generation_tool_catalog(max_tools: int = 20) -> list[tuple[str, str]]:
    """Return a stable, generation-focused subset of registered tools."""
    try:
        tools = _load_tools()
    except Exception:
        return []

    ordered_ids: list[str] = []
    for tool_id in _PREFERRED_TOOL_IDS:
        if tool_id in tools and tool_id not in ordered_ids:
            ordered_ids.append(tool_id)
    for tool_id in sorted(tools):
        if tool_id not in ordered_ids:
            ordered_ids.append(tool_id)

    catalog: list[tuple[str, str]] = []
    for tool_id in ordered_ids[:max_tools]:
        metadata = tools[tool_id][1]
        description = (metadata.get("description") or "").strip() or "Registered tool"
        catalog.append((tool_id, description))
    return catalog


def render_tool_catalog_markdown(max_tools: int = 20) -> str:
    catalog = get_generation_tool_catalog(max_tools=max_tools)
    if not catalog:
        return "| tool_id | Description |\n|---------|-------------|\n| web_search | Search the web for information |\n"
    rows = ["| tool_id | Description |", "|---------|-------------|"]
    for tool_id, description in catalog:
        rows.append(f"| {tool_id} | {description} |")
    return "\n".join(rows)


def render_tool_id_list(max_tools: int = 20) -> str:
    catalog = get_generation_tool_catalog(max_tools=max_tools)
    if not catalog:
        return "web_search"
    return ", ".join(tool_id for tool_id, _description in catalog)
