"""Capability handlers — thin registration hub.

Implementations live in ``dan.server.capabilities.*`` domain modules.
This file re-exports the ``register_*`` functions so existing callers
(``app.py``, ``chat_factory.py``) continue to work unchanged.

Each handler has signature:
    async def handler(args: dict, context: CapabilityContext) -> CapabilityResult
"""
from __future__ import annotations

import logging
import os
from typing import Any

from dan.server.capability_registry import (
    ALL_MODES,
    READ_ONLY_MODES,
    CapabilityContext,
    CapabilityResult,
    ChatCapabilityRegistry,
    build_tool_schema,
)

# ── Re-export shared helpers for backward compatibility ────────────
from dan.server.capabilities._helpers import (  # noqa: F401
    _classify_network_exception,
    _failure_result,
    _FILE_READ_MAX,
    _resolve_user_path,
    _sanitize_web_content,
    _schedule_export_cleanup,
    _truncate,
)

# ── Import all handler implementations from domain modules ─────────
from dan.server.capabilities.web import (
    handle_web_search,
    handle_web_fetch,
    handle_http_request,
)
from dan.server.capabilities.file_io import (
    handle_file_read,
    handle_file_write,
    handle_file_grep,
    handle_pdf_read,
    handle_list_directory,
    handle_file_copy,
    handle_file_move,
    handle_file_delete,
    handle_compress,
)
from dan.server.capabilities.git import (
    handle_git_status,
    handle_git_diff,
    handle_git_log,
    handle_git_branch,
    handle_git_commit,
    handle_git_worktree,
)
from dan.server.capabilities.shell import (
    handle_shell_command,
    handle_screenshot,
    handle_clipboard,
    handle_notify,
)
from dan.server.capabilities.data import (
    handle_python_eval,
    handle_csv_read,
    handle_spreadsheet_read,
    handle_json_extract,
    handle_regex_match,
    handle_text_chunk,
    handle_text_diff,
    handle_text_translate,
)
from dan.server.capabilities.media import (
    handle_image_describe,
    handle_audio_transcribe,
)
from dan.server.capabilities.config import (
    handle_get_config,
    handle_set_config,
    _update_env_file,
    _CONFIGURABLE_PREFIXES,
)
from dan.server.capabilities.experiences import (
    _format_experience_summary,
    handle_search_workflow_history,
    handle_get_workflow_details,
    handle_search_run_history,
    handle_get_learned_principles,
    handle_discover_capabilities,
    handle_list_my_workflows,
    handle_search_workflows,
    handle_show_workflow,
    handle_fork_workflow,
    handle_get_activity,
)
from dan.server.capabilities.publishing import (
    _resolve_graph,
    handle_publish_workflow,
    handle_unpublish_workflow,
    handle_export_workflow,
    handle_share_workflow,
    handle_list_published,
    handle_get_publish_status,
    handle_import_block,
    handle_list_blocks,
)
from dan.server.capabilities.runs import (
    _find_persisted_run,
    get_pending_run_disambiguation,
    resolve_run_reference,
    handle_start_run,
    handle_get_run_status,
    handle_list_active_runs,
    handle_cancel_run,
    handle_resume_run,
    handle_get_run_logs,
    handle_get_run_checkpoints,
    handle_rerun_from_checkpoint,
    handle_apply_pending_overlay,
    handle_submit_human_input,
)
from dan.server.capabilities.browser import (
    _get_controller,
    set_controller as _set_controller,
    handle_browser_open,
    handle_browser_click,
    handle_browser_type,
    handle_browser_fill,
    handle_browser_download,
    handle_browser_extract,
    handle_browser_screenshot,
    handle_browser_tabs,
    handle_desktop_observe,
    handle_desktop_focus,
    handle_desktop_click,
    handle_desktop_type,
    handle_desktop_hotkey,
)
from dan.server.capabilities.introspection import (
    handle_inspect_node,
    handle_list_test_cases,
    handle_run_test_case,
)
from dan.server.capabilities.misc import (
    handle_current_datetime,
    handle_load_prompt_detail,
    handle_telegram_poll,
    handle_send_email,
)

logger = logging.getLogger(__name__)

WRITE_MODES = ["agent", "build", "mutate"]

# ── Capability schemas ─────────────────────────────────────────────

WEB_SEARCH_CAPABILITY_SCHEMA = build_tool_schema(
    name="web_search",
    description=(
        "Search the web for current information. Use when the user asks about "
        "live data, recent events, or facts you don't have. "
        "For grounded factual answers or research tasks, use fetch_content=true "
        "to automatically read the top results in parallel — saves separate "
        "web_fetch calls."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query — be specific and include relevant keywords."},
            "num_results": {"type": "integer", "description": "Number of results to return (1-10). Default 3. Use 1 for quick lookups, more for research."},
            "fetch_content": {"type": "boolean", "description": "If true, automatically fetch and include content from the top search results in parallel. Use this for grounded live answers instead of relying only on snippets."},
        },
        "required": ["query"],
    },
)

FILE_READ_CAPABILITY_SCHEMA = build_tool_schema(
    name="file_read",
    description=(
        "Read a text file. Supports line ranges and keyword grep to read only "
        "what you need instead of loading the entire file. For large files, "
        "use grep first to find relevant sections, then read those line ranges."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file (absolute or relative to workspace)."},
            "start_line": {"type": "integer", "description": "Start reading from this line (1-based). Omit to start from beginning."},
            "end_line": {"type": "integer", "description": "Stop reading at this line (inclusive). Omit to read to end."},
            "grep": {"type": "string", "description": "Only return lines containing this keyword/pattern (case-insensitive). Returns matching lines with their line numbers and 2 lines of context."},
        },
        "required": ["path"],
    },
)

FILE_GREP_CAPABILITY_SCHEMA = build_tool_schema(
    name="file_grep",
    description=(
        "Search for a keyword or pattern across files in a directory. "
        "Returns matching lines with filenames, line numbers, and context. "
        "Use this to find relevant sections before reading specific line ranges with file_read."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to search in (absolute or relative)."},
            "pattern": {"type": "string", "description": "Keyword or regex pattern to search for (case-insensitive)."},
            "glob": {"type": "string", "description": "File glob filter, e.g. '*.tex', '*.py', '*.md'. Default: all text files."},
        },
        "required": ["path", "pattern"],
    },
)

PDF_READ_CAPABILITY_SCHEMA = build_tool_schema(
    name="pdf_read",
    description=(
        "Read a PDF file. Accepts absolute paths "
        "(~/Dropbox/..., /Users/...) or workspace-relative paths. "
        "Supports mode='text' for extraction or mode='vision' for page-by-page "
        "vision descriptions that preserve figures and tables."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the PDF file (absolute or relative to workspace)."},
            "mode": {"type": "string", "enum": ["text", "vision"], "description": "text: extract text; vision: describe each page with a vision model.", "default": "text"},
            "start_page": {"type": "integer", "description": "First page to read (0-indexed)."},
            "end_page": {"type": "integer", "description": "Exclusive end page (0-indexed)."},
            "vision_model": {"type": "string", "description": "Vision model to use when mode='vision'.", "default": "gpt-4o"},
            "vision_prompt": {"type": "string", "description": "Optional custom prompt for vision mode."},
        },
        "required": ["path"],
    },
)

CURRENT_DATETIME_CAPABILITY_SCHEMA = build_tool_schema(
    name="current_datetime",
    description=(
        "Get the current date and time. Use when the user asks about today's date, "
        "current time, day of the week, or needs time-relative calculations "
        "(e.g. 'how many days until June 1?', 'what day is it?')."
    ),
    parameters={"type": "object", "properties": {}},
)

LOAD_PROMPT_DETAIL_CAPABILITY_SCHEMA = build_tool_schema(
    name="load_prompt_detail",
    description=(
        "Load extra prompt guidance for a resolved prompt module when the system "
        "has exposed a detail_id for the current request."
    ),
    parameters={
        "type": "object",
        "properties": {
            "detail_id": {
                "type": "string",
                "description": "Prompt detail identifier from the current request context.",
            },
        },
        "required": ["detail_id"],
    },
)

TELEGRAM_POLL_CAPABILITY_SCHEMA = build_tool_schema(
    name="telegram_poll",
    description=(
        "Create a native Telegram poll in the current chat. Use for "
        "decision-making when there are 2-10 discrete options. On non-Telegram "
        "surfaces the options are rendered as numbered text instead."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The poll question (1-300 characters)."},
            "options": {"type": "array", "items": {"type": "string"}, "description": "Answer options (2-10 items, each 1-100 characters)."},
            "is_anonymous": {"type": "boolean", "description": "Whether votes are anonymous. Default: false."},
            "allows_multiple": {"type": "boolean", "description": "Whether users can select multiple options. Default: false."},
        },
        "required": ["question", "options"],
    },
)

SEND_EMAIL_CAPABILITY_SCHEMA = build_tool_schema(
    name="send_email",
    description=(
        "Send an email to a recipient. Requires DAN_SMTP_* env vars to be configured. "
        "Use when the user asks to email something to someone."
    ),
    parameters={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address."},
            "subject": {"type": "string", "description": "Email subject line."},
            "body": {"type": "string", "description": "Email body (plain text)."},
        },
        "required": ["to", "subject", "body"],
    },
)

SCREENSHOT_CAPABILITY_SCHEMA = build_tool_schema(
    name="screenshot",
    description="Take a screenshot of the current screen (macOS). Returns the path to the saved screenshot image. Use when the user asks to capture what's on screen.",
    parameters={"type": "object", "properties": {"filename": {"type": "string", "description": "Optional filename (default: screenshot_<timestamp>.png)."}}},
)

CLIPBOARD_CAPABILITY_SCHEMA = build_tool_schema(
    name="clipboard",
    description="Read from or write to the system clipboard (macOS). Use 'read' to get current clipboard contents. Use 'write' to copy text to clipboard.",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["read", "write"], "description": "read or write."},
            "content": {"type": "string", "description": "Text to copy (required for 'write')."},
        },
        "required": ["action"],
    },
)

SET_CONFIG_CAPABILITY_SCHEMA = build_tool_schema(
    name="set_config",
    description=(
        "Set a DAN configuration value. Updates the running server immediately "
        "and persists to .env for future restarts. Use when the user provides "
        "API keys, SMTP credentials, tool paths, or other settings. "
        "Allowed prefixes: DAN_SMTP_* (email), DAN_TAVILY_API_KEY, DAN_BRAVE_API_KEY, "
        "DAN_OPENAI_API_KEY, DAN_ANTHROPIC_API_KEY, DAN_GOOGLE_API_KEY, "
        "DAN_STATA_* (e.g. DAN_STATA_PATH for Stata binary), "
        "DAN_MCP_* (MCP server settings), DAN_TOOL_* (tool paths), "
        "DAN_PATH_* (general binary/executable paths), "
        "DAN_LLM_MODEL, DAN_CHAT_MODEL, DAN_LLM_BASE_URL."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Environment variable name (e.g. DAN_SMTP_HOST)."},
            "value": {"type": "string", "description": "Value to set."},
        },
        "required": ["key", "value"],
    },
)

GET_CONFIG_CAPABILITY_SCHEMA = build_tool_schema(
    name="get_config",
    description="Get the current DAN configuration, including active model, base URL, bot name, and active features.",
    parameters={"type": "object", "properties": {}},
)

LIST_DIRECTORY_CAPABILITY_SCHEMA = build_tool_schema(
    name="list_directory",
    description=(
        "List files and directories at a given path. Accepts absolute paths "
        "(~/Dropbox/...) or workspace-relative. Supports glob filtering, recursive "
        "traversal, page size limits, and continuation via `start_after`. Results "
        "are sorted by relative path; if a page is truncated, do not infer absence "
        "from the cutoff — continue with `start_after` or narrow the filter."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path (absolute or relative)."},
            "glob_pattern": {"type": "string", "description": "Optional glob filter (e.g. '*.pdf')."},
            "recursive": {"type": "boolean", "description": "Recurse into subdirectories."},
            "limit": {
                "type": "integer",
                "description": "Maximum entries to return in this page (default 200, max 1000).",
            },
            "start_after": {
                "type": "string",
                "description": "Return only entries whose relative path sorts after this value.",
            },
        },
        "required": ["path"],
    },
)

SPREADSHEET_READ_CAPABILITY_SCHEMA = build_tool_schema(
    name="spreadsheet_read",
    description="Read an Excel spreadsheet (.xlsx, .xlsm, .xltx, .xltm) into structured rows. Use when the user asks to inspect or analyze spreadsheet files.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Spreadsheet path (absolute or relative)."},
            "sheet": {"type": "string", "description": "Optional sheet name to read."},
            "max_rows": {"type": "integer", "description": "Maximum data rows to return."},
        },
        "required": ["path"],
    },
)

TEXT_TRANSLATE_CAPABILITY_SCHEMA = build_tool_schema(
    name="text_translate",
    description="Translate text between languages using an LLM. Use when the user asks to translate text or cross-language content.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to translate."},
            "target_language": {"type": "string", "description": "Target language."},
            "source_language": {"type": "string", "description": "Optional source language."},
            "model": {"type": "string", "description": "Optional model override."},
        },
        "required": ["text", "target_language"],
    },
)

IMAGE_DESCRIBE_CAPABILITY_SCHEMA = build_tool_schema(
    name="image_describe",
    description="Describe or analyze an image using a vision-capable model. Use for screenshots, charts, figures, and photos.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Image path (absolute or relative)."},
            "question": {"type": "string", "description": "Optional question about the image."},
            "model": {"type": "string", "description": "Optional model override."},
        },
        "required": ["path"],
    },
)

AUDIO_TRANSCRIBE_CAPABILITY_SCHEMA = build_tool_schema(
    name="audio_transcribe",
    description="Transcribe an audio or voice file to text. Use for voice notes, interviews, and spoken instructions.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Audio path (absolute or relative)."},
            "language": {"type": "string", "description": "Optional ISO language code."},
            "model": {"type": "string", "description": "Optional Whisper model override."},
        },
        "required": ["path"],
    },
)

WEB_FETCH_CAPABILITY_SCHEMA = build_tool_schema(
    name="web_fetch",
    description="Fetch content from a URL and return it as text. Use when the user shares a link and asks to read, summarize, or extract info from it. Use extract_only to get just the specific content you need and save context tokens.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch."},
            "extract_only": {"type": "string", "description": "If set, only return content matching this keyword (case-insensitive grep). Useful for pulling specific data from large pages."},
        },
        "required": ["url"],
    },
)

FILE_WRITE_CAPABILITY_SCHEMA = build_tool_schema(
    name="file_write",
    description="Write or append content to a file. Accepts absolute paths. Use when the user asks to save, create, or write content to a file.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path (absolute or relative)."},
            "content": {"type": "string", "description": "Content to write."},
            "mode": {"type": "string", "enum": ["overwrite", "append"], "description": "Write mode."},
        },
        "required": ["path", "content"],
    },
)

SHELL_COMMAND_CAPABILITY_SCHEMA = build_tool_schema(
    name="shell_command",
    description="Execute a shell command and return its output. Use when the user asks to run a command, check system info, or perform a terminal operation.",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute."},
            "working_directory": {"type": "string", "description": "Optional working directory."},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)."},
        },
        "required": ["command"],
    },
)

HTTP_REQUEST_CAPABILITY_SCHEMA = build_tool_schema(
    name="http_request",
    description="Send an HTTP request (GET, POST, PUT, DELETE, etc.). Use for REST API calls when the user asks to interact with an external service.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Request URL."},
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"], "description": "HTTP method."},
            "headers": {"type": "object", "description": "Optional request headers."},
            "body": {"type": "string", "description": "Optional request body."},
        },
        "required": ["url"],
    },
)

TEXT_CHUNK_CAPABILITY_SCHEMA = build_tool_schema(
    name="text_chunk",
    description="Split text into overlapping chunks by character or word count. Useful for processing long documents.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to chunk."},
            "chunk_size": {"type": "integer", "description": "Size per chunk (default 1000)."},
            "overlap": {"type": "integer", "description": "Overlap between chunks (default 200)."},
        },
        "required": ["text"],
    },
)

JSON_EXTRACT_CAPABILITY_SCHEMA = build_tool_schema(
    name="json_extract",
    description="Extract a value from JSON data using dot-notation path (e.g. 'a.b.0.name').",
    parameters={
        "type": "object",
        "properties": {
            "data": {"type": "string", "description": "JSON string to extract from."},
            "path": {"type": "string", "description": "Dot-notation path (e.g. 'results.0.title')."},
        },
        "required": ["data", "path"],
    },
)

REGEX_MATCH_CAPABILITY_SCHEMA = build_tool_schema(
    name="regex_match",
    description="Apply a regex pattern to text. Returns matches or performs substitution when 'replacement' is provided.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to match against."},
            "pattern": {"type": "string", "description": "Regular expression pattern."},
            "replacement": {"type": "string", "description": "Optional replacement string for substitution."},
        },
        "required": ["text", "pattern"],
    },
)

# ── Graph / activity schemas ───────────────────────────────────────

LIST_GRAPHS_SCHEMA = build_tool_schema(
    name="list_graphs",
    description="List all saved workflows. Use when the user asks 'what workflows exist?', 'show my workflows', or 'list graphs'.",
    parameters={"type": "object", "properties": {}},
)

GET_ACTIVITY_SCHEMA = build_tool_schema(
    name="get_activity",
    description="Show current run activity: active runs, recent completions, and connected surfaces. Use when the user asks 'what's running?', 'show activity', or 'any active runs?'.",
    parameters={"type": "object", "properties": {}},
)

async def handle_list_graphs(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    graphs = ctx.graph_store.list_graphs()
    if not graphs:
        return CapabilityResult(success=True, message="No workflows found.", data=[], output_preview="No workflows found.")
    lines = []
    for g in graphs:
        gid = g.get("graph_id", g.get("id", "?"))
        node_count = len(g.get("nodes", []))
        edge_count = len(g.get("edges", []))
        lines.append(f"- **{gid}** ({node_count} nodes, {edge_count} edges)")
    text = f"Found {len(graphs)} workflow(s):\n" + "\n".join(lines)
    return CapabilityResult(success=True, message=text, data=graphs, output_preview=_truncate(text))

# ── Experience schemas (25-2) ──────────────────────────────────────

SEARCH_WORKFLOW_HISTORY_SCHEMA = build_tool_schema(name="search_workflow_history", description="Search past workflows by semantic similarity. Use when the user asks 'have we done X before?', 'show workflows similar to Y', or 'find past work on Z'.", parameters={"type": "object", "properties": {"query": {"type": "string", "description": "Natural-language search query"}, "top_k": {"type": "integer", "description": "Max results to return", "default": 5}}, "required": ["query"]})
GET_WORKFLOW_DETAILS_SCHEMA = build_tool_schema(name="get_workflow_details", description="Get detailed info about a specific workflow: name, description, success rate, node types, tools used, failure/success patterns. Use when the user asks 'tell me about workflow X' or 'what's the success rate of Y?'.", parameters={"type": "object", "properties": {"workflow_id": {"type": "string", "description": "Workflow ID to look up"}}, "required": ["workflow_id"]})
SEARCH_RUN_HISTORY_SCHEMA = build_tool_schema(name="search_run_history", description="Search past runs by workflow, status, or date range. Use when the user asks 'show failed runs', 'recent runs', 'runs for workflow X', or 'runs from last week'.", parameters={"type": "object", "properties": {"workflow_id": {"type": "string", "description": "Filter by workflow ID (optional)"}, "status": {"type": "string", "enum": ["completed", "failed", "cancelled"], "description": "Filter by status"}, "limit": {"type": "integer", "description": "Max results", "default": 20}, "offset": {"type": "integer", "description": "Skip N results", "default": 0}, "after": {"type": "number", "description": "Unix timestamp: only runs after this time"}, "before": {"type": "number", "description": "Unix timestamp: only runs before this time"}}})
GET_LEARNED_PRINCIPLES_SCHEMA = build_tool_schema(name="get_learned_principles", description="Query causal principles learned from past failures. Use when the user asks 'what have we learned from failures?', 'show principles for workflow X', or 'what do we know about tool/timeout errors?'.", parameters={"type": "object", "properties": {"query": {"type": "string", "description": "Substring filter on condition/action/reason (optional)"}, "workflow_id": {"type": "string", "description": "Scope to workflow, or omit for global"}, "min_confidence": {"type": "number", "description": "Min confidence 0–1", "default": 0.3}, "limit": {"type": "integer", "description": "Max results", "default": 10}}})
DISCOVER_CAPABILITIES_SCHEMA = build_tool_schema(name="discover_capabilities", description="Discover available tools, skills, patterns, and relevant past workflows. Use when the user asks 'what can DAN do?', 'what patterns exist for RAG?', or 'what tools/skills are available?'.", parameters={"type": "object", "properties": {"query": {"type": "string", "description": "Search query for workflows and self-knowledge"}, "top_k": {"type": "integer", "description": "Max workflow matches", "default": 5}}, "required": ["query"]})

# ── Publish schemas (25-3) ─────────────────────────────────────────

PUBLISH_WORKFLOW_SCHEMA = build_tool_schema(name="publish_workflow", description="Publish a workflow to the MCP/HTTP endpoint. Use when the user asks 'publish this workflow', 'make it available as MCP', or 'publish as API'.", parameters={"type": "object", "properties": {"graph_id": {"type": "string", "description": "Workflow ID (defaults to active workflow)"}, "name_override": {"type": "string", "description": "Override workflow name in registry"}, "api_key": {"type": "string", "description": "Optional API key for auth"}, "rate_limit": {"type": "integer", "description": "Optional max requests per minute"}}})
UNPUBLISH_WORKFLOW_SCHEMA = build_tool_schema(name="unpublish_workflow", description="Remove a workflow from the publish registry. Use when the user asks 'unpublish', 'stop publishing', or 'remove from API'.", parameters={"type": "object", "properties": {"graph_id": {"type": "string", "description": "Workflow ID (defaults to active workflow)"}}})
EXPORT_WORKFLOW_SCHEMA = build_tool_schema(name="export_workflow", description="Export a workflow as block, markdown, or Python. Use when the user asks 'export as block', 'export to markdown', 'export as Python', or 'share as block'.", parameters={"type": "object", "properties": {"graph_id": {"type": "string", "description": "Workflow ID (defaults to active workflow)"}, "format": {"type": "string", "enum": ["block", "markdown", "python"], "description": "Export format"}, "name": {"type": "string", "description": "Block/workflow name (for block format)"}, "version": {"type": "string", "description": "Block version (for block format)", "default": "0.1.0"}, "node_id": {"type": "string", "description": "Composite node ID (for composite block export)"}}, "required": ["format"]})
SHARE_WORKFLOW_SCHEMA = build_tool_schema(name="share_workflow", description="Generate shareable config: MCP client config, API docs, or OpenAPI spec. Use when the user asks 'share as MCP config', 'API docs', 'OpenAPI spec', or 'how to connect'.", parameters={"type": "object", "properties": {"graph_id": {"type": "string", "description": "Workflow ID (defaults to active workflow)"}, "format": {"type": "string", "enum": ["mcp_config", "api_docs", "openapi"], "description": "Share format"}, "base_url": {"type": "string", "description": "Base URL for API docs", "default": "http://localhost:8001"}}, "required": ["format"]})
LIST_PUBLISHED_SCHEMA = build_tool_schema(name="list_published", description="List all currently published workflows. Use when the user asks 'what's published?', 'list published workflows', or 'show published APIs'.", parameters={"type": "object", "properties": {}})
GET_PUBLISH_STATUS_SCHEMA = build_tool_schema(name="get_publish_status", description="Check whether a workflow is published and return saved config. Use when the user asks 'is this published?', 'publish status', or 'check if published'.", parameters={"type": "object", "properties": {"graph_id": {"type": "string", "description": "Workflow ID (defaults to active workflow)"}}})
IMPORT_BLOCK_SCHEMA = build_tool_schema(name="import_block", description="Install a block from a path or URL. Use when the user asks 'import block', 'install block from X', or 'add block from path'.", parameters={"type": "object", "properties": {"source": {"type": "string", "description": "Path to block dir/tarball or URL"}, "scope": {"type": "string", "enum": ["user", "workspace"], "description": "Install scope", "default": "user"}, "workspace": {"type": "string", "description": "Workspace path (required when scope=workspace)"}, "force": {"type": "boolean", "description": "Overwrite existing", "default": False}}, "required": ["source"]})
LIST_BLOCKS_SCHEMA = build_tool_schema(name="list_blocks", description="List all installed blocks. Use when the user asks 'list blocks', 'what blocks are installed?', or 'show blocks'.", parameters={"type": "object", "properties": {"scope": {"type": "string", "description": "Filter by scope (optional, not yet used)"}}})

# ── Run lifecycle schemas (25-4) ───────────────────────────────────

RUN_WRITE_MODES = ["agent", "build", "mutate", "debug"]

RUN_POLICY_SCHEMA = {
    "type": "object",
    "description": "Optional per-run execution policy/profile override.",
    "properties": {
        "profile": {"type": "string", "enum": ["default", "long_running"], "description": "Named policy profile."},
        "max_duration": {"type": "number", "description": "Hard wall-clock ceiling in seconds."},
        "max_cost": {"type": "number", "description": "Hard cost ceiling in USD-equivalent tracked cost."},
        "checkpoint_batch_size": {"type": "integer", "description": "Checkpoint after this many completed nodes."},
        "checkpoint_interval_sec": {"type": "number", "description": "Checkpoint at least this often in seconds."},
        "checkpoint_on_critical_nodes": {"type": "boolean", "description": "Force checkpoints around critical-tagged nodes."},
        "critical_node_tags": {"type": "array", "items": {"type": "string"}, "description": "Node tags treated as critical checkpoints."},
        "partial_results": {"type": "boolean", "description": "Return partial results when a run stops on policy limits."},
        "progress_enabled": {"type": "boolean", "description": "Enable structured progress snapshots."},
        "progress_emit_events": {"type": "boolean", "description": "Emit progress events while the run executes."},
        "progress_eta_enabled": {"type": "boolean", "description": "Include ETA estimates in progress snapshots."},
        "progress_stage_labels": {"type": "boolean", "description": "Include heuristic stage labels in progress snapshots."},
        "fallback_model": {"type": "string", "description": "Fallback model to use for long-running retry defaults."},
    },
}

START_RUN_SCHEMA = build_tool_schema(name="start_run", description="Start a workflow run. Use when the user says 'run it', 'execute', or 'start the workflow'.", parameters={"type": "object", "properties": {"workflow_id": {"type": "string", "description": "Workflow/graph ID to run (default: current chat workflow)"}, "inputs": {"type": "object", "description": "Optional input values for the workflow"}, "run_id": {"type": "string", "description": "Optional custom run ID"}, "session_id": {"type": "string", "description": "Optional session ID"}, "run_policy": RUN_POLICY_SCHEMA}})
GET_RUN_STATUS_SCHEMA = build_tool_schema(name="get_run_status", description="Get status of a run. Supports run_id or 'latest', 'last_failed', 'paused'. Use when the user asks 'status of the run', 'how did it go?', or 'what's running?'.", parameters={"type": "object", "properties": {"run_id": {"type": "string", "description": "Run ID or 'latest'|'last_failed'|'paused'"}}, "required": ["run_id"]})
LIST_ACTIVE_RUNS_SCHEMA = build_tool_schema(name="list_active_runs", description="List active and recent runs. Use when the user asks 'what's running?', 'show active runs', or 'list runs'.", parameters={"type": "object", "properties": {}})
CANCEL_RUN_SCHEMA = build_tool_schema(name="cancel_run", description="Cancel a running workflow. Use when the user says 'cancel run X' or 'stop the run'.", parameters={"type": "object", "properties": {"run_id": {"type": "string", "description": "Run ID to cancel"}}, "required": ["run_id"]})
RESUME_RUN_SCHEMA = build_tool_schema(name="resume_run", description="Resume a checkpointed run. Use when the user says 'resume run X' or 'continue the run'.", parameters={"type": "object", "properties": {"run_id": {"type": "string", "description": "Run ID to resume"}, "workflow_id": {"type": "string", "description": "Workflow/graph ID"}, "session_id": {"type": "string", "description": "Optional session ID"}, "run_policy": RUN_POLICY_SCHEMA}, "required": ["run_id", "workflow_id"]})
GET_RUN_LOGS_SCHEMA = build_tool_schema(name="get_run_logs", description="Get event logs for a run. Use when the user asks 'show logs', 'what happened?', or 'run output'.", parameters={"type": "object", "properties": {"run_id": {"type": "string", "description": "Run ID or 'latest'|'last_failed'|'paused'"}, "limit": {"type": "integer", "description": "Max events to return", "default": 50}, "node_id": {"type": "string", "description": "Filter by node ID (optional)"}}, "required": ["run_id"]})
GET_RUN_CHECKPOINTS_SCHEMA = build_tool_schema(name="get_run_checkpoints", description="Get checkpoint info for a run (completed nodes, staleness). Use for partial reruns.", parameters={"type": "object", "properties": {"run_id": {"type": "string", "description": "Run ID or 'latest'|'last_failed'|'paused'"}}, "required": ["run_id"]})
RERUN_FROM_CHECKPOINT_SCHEMA = build_tool_schema(name="rerun_from_checkpoint", description="Partial rerun from a checkpoint. Use when the user says 'rerun from node X' or 'retry downstream'.", parameters={"type": "object", "properties": {"source_run_id": {"type": "string", "description": "Run ID with checkpoint"}, "workflow_id": {"type": "string", "description": "Workflow/graph ID"}, "scope_type": {"type": "string", "enum": ["downstream_of", "single_node", "subgraph"], "description": "Rerun scope type"}, "target_node_id": {"type": "string", "description": "Target node for downstream_of or single_node"}, "sub_graph_key": {"type": "string", "description": "Sub-graph key for subgraph scope"}, "session_id": {"type": "string", "description": "Optional session ID"}, "run_policy": RUN_POLICY_SCHEMA}, "required": ["source_run_id", "workflow_id", "scope_type"]})
APPLY_PENDING_OVERLAY_SCHEMA = build_tool_schema(name="apply_pending_overlay", description="Apply a bounded execution-local overlay to a still-pending node in a live run. Use for mid-run prompt/model/tool/config adaptation before the node starts.", parameters={"type": "object", "properties": {"run_id": {"type": "string", "description": "Run ID or 'latest'|'last_failed'|'paused'"}, "node_id": {"type": "string", "description": "Pending node ID to patch"}, "patch": {"type": "object", "description": "Whitelisted overlay patch for prompt/model/tool/config fields"}, "source": {"type": "string", "description": "Overlay source label", "default": "user"}, "reason": {"type": "string", "description": "Why the overlay is being applied"}}, "required": ["run_id", "node_id", "patch"]})
SUBMIT_HUMAN_INPUT_SCHEMA = build_tool_schema(name="submit_human_input", description="Submit response for a pending HumanNode. Use when the user provides input for a paused run.", parameters={"type": "object", "properties": {"run_id": {"type": "string", "description": "Run ID"}, "request_id": {"type": "string", "description": "Request ID from human_input_needed event"}, "response": {"type": "object", "description": 'User response (e.g. {"approved": true} or {"text": "..."})'}}, "required": ["run_id", "request_id", "response"]})

# ── Workflow catalog schemas (29-4) ────────────────────────────────

LIST_MY_WORKFLOWS_SCHEMA = build_tool_schema(name="list_my_workflows", description="List all saved workflows with names, descriptions, and sizes.", parameters={"type": "object", "properties": {}, "required": []})
SEARCH_WORKFLOWS_SCHEMA = build_tool_schema(name="search_workflows", description="Search saved workflows by keyword.", parameters={"type": "object", "properties": {"query": {"type": "string", "description": "Search keywords"}}, "required": ["query"]})
SHOW_WORKFLOW_SCHEMA = build_tool_schema(name="show_workflow", description="Show the structure of a specific workflow as an ASCII diagram.", parameters={"type": "object", "properties": {"workflow_id": {"type": "string", "description": "The workflow ID to display"}}, "required": ["workflow_id"]})
FORK_WORKFLOW_SCHEMA = build_tool_schema(name="fork_workflow", description="Duplicate an existing workflow with a new name as a starting point for adaptation.", parameters={"type": "object", "properties": {"workflow_id": {"type": "string", "description": "ID of the workflow to fork"}, "new_name": {"type": "string", "description": "Name for the forked copy"}}, "required": ["workflow_id"]})

# ── Extra tool schemas (DAN_FULL_TOOLS) ────────────────────────────

from dan.tools.python_eval import TOOL_METADATA as _META_PYTHON_EVAL
from dan.tools.csv_read import TOOL_METADATA as _META_CSV_READ
from dan.tools.compress import TOOL_METADATA as _META_COMPRESS
from dan.tools.file_copy import TOOL_METADATA as _META_FILE_COPY
from dan.tools.file_move import TOOL_METADATA as _META_FILE_MOVE
from dan.tools.file_delete import TOOL_METADATA as _META_FILE_DELETE
from dan.tools.git_status import TOOL_METADATA as _META_GIT_STATUS
from dan.tools.git_diff import TOOL_METADATA as _META_GIT_DIFF
from dan.tools.git_log import TOOL_METADATA as _META_GIT_LOG
from dan.tools.git_branch import TOOL_METADATA as _META_GIT_BRANCH
from dan.tools.git_commit import TOOL_METADATA as _META_GIT_COMMIT
from dan.tools.git_worktree import TOOL_METADATA as _META_GIT_WORKTREE
from dan.tools.notify import TOOL_METADATA as _META_NOTIFY
from dan.tools.text_diff import TOOL_METADATA as _META_TEXT_DIFF

PYTHON_EVAL_CAPABILITY_SCHEMA = build_tool_schema(name="python_eval", description=_META_PYTHON_EVAL["description"], parameters=_META_PYTHON_EVAL["parameters"])
CSV_READ_CAPABILITY_SCHEMA = build_tool_schema(name="csv_read", description=_META_CSV_READ["description"], parameters=_META_CSV_READ["parameters"])
COMPRESS_CAPABILITY_SCHEMA = build_tool_schema(name="compress", description=_META_COMPRESS["description"], parameters=_META_COMPRESS["parameters"])
FILE_COPY_CAPABILITY_SCHEMA = build_tool_schema(name="file_copy", description=_META_FILE_COPY["description"], parameters=_META_FILE_COPY["parameters"])
FILE_MOVE_CAPABILITY_SCHEMA = build_tool_schema(name="file_move", description=_META_FILE_MOVE["description"], parameters=_META_FILE_MOVE["parameters"])
FILE_DELETE_CAPABILITY_SCHEMA = build_tool_schema(name="file_delete", description=_META_FILE_DELETE["description"], parameters=_META_FILE_DELETE["parameters"])
GIT_STATUS_CAPABILITY_SCHEMA = build_tool_schema(name="git_status", description=_META_GIT_STATUS["description"], parameters=_META_GIT_STATUS["parameters"])
GIT_DIFF_CAPABILITY_SCHEMA = build_tool_schema(name="git_diff", description=_META_GIT_DIFF["description"], parameters=_META_GIT_DIFF["parameters"])
GIT_LOG_CAPABILITY_SCHEMA = build_tool_schema(name="git_log", description=_META_GIT_LOG["description"], parameters=_META_GIT_LOG["parameters"])
GIT_BRANCH_CAPABILITY_SCHEMA = build_tool_schema(name="git_branch", description=_META_GIT_BRANCH["description"], parameters=_META_GIT_BRANCH["parameters"])
GIT_COMMIT_CAPABILITY_SCHEMA = build_tool_schema(name="git_commit", description=_META_GIT_COMMIT["description"], parameters=_META_GIT_COMMIT["parameters"])
GIT_WORKTREE_CAPABILITY_SCHEMA = build_tool_schema(name="git_worktree", description=_META_GIT_WORKTREE["description"], parameters=_META_GIT_WORKTREE["parameters"])
NOTIFY_CAPABILITY_SCHEMA = build_tool_schema(name="notify", description=_META_NOTIFY["description"], parameters=_META_NOTIFY["parameters"])
TEXT_DIFF_CAPABILITY_SCHEMA = build_tool_schema(name="text_diff", description=_META_TEXT_DIFF["description"], parameters=_META_TEXT_DIFF["parameters"])

# ── Introspection schemas ──────────────────────────────────────────

from dan.server.variable_inspector import compute_upstream_variables  # noqa: F401

INSPECT_NODE_SCHEMA = build_tool_schema(name="inspect_node", description="Inspect a node in a workflow graph, including its config, ports, and upstream variables.", parameters={"type": "object", "properties": {"workflow_id": {"type": "string", "description": "The workflow ID"}, "node_id": {"type": "string", "description": "The node ID to inspect"}, "run_id": {"type": "string", "description": "Optional run ID to get runtime values"}}, "required": ["workflow_id", "node_id"]})
LIST_TEST_CASES_SCHEMA = build_tool_schema(name="list_test_cases", description="List test cases for a specific node in a workflow.", parameters={"type": "object", "properties": {"workflow_id": {"type": "string"}, "node_id": {"type": "string"}}, "required": ["workflow_id", "node_id"]})
RUN_TEST_CASE_SCHEMA = build_tool_schema(name="run_test_case", description="Run a specific test case for a node.", parameters={"type": "object", "properties": {"workflow_id": {"type": "string"}, "node_id": {"type": "string"}, "case_id": {"type": "string"}}, "required": ["workflow_id", "node_id", "case_id"]})

# ── Browser/desktop schemas (DAN_COMPUTER_CONTROL) ─────────────────

BROWSER_OPEN_SCHEMA = build_tool_schema(name="browser_open", description="Open a URL in the browser.", parameters={"type": "object", "properties": {"url": {"type": "string", "description": "URL to open"}}, "required": ["url"]})
BROWSER_CLICK_SCHEMA = build_tool_schema(name="browser_click", description="Click an element in the browser by CSS selector.", parameters={"type": "object", "properties": {"selector": {"type": "string", "description": "CSS selector"}}, "required": ["selector"]})
BROWSER_TYPE_SCHEMA = build_tool_schema(name="browser_type", description="Type text into a browser element (appends).", parameters={"type": "object", "properties": {"selector": {"type": "string", "description": "CSS selector"}, "text": {"type": "string", "description": "Text to type"}}, "required": ["selector", "text"]})
BROWSER_FILL_SCHEMA = build_tool_schema(name="browser_fill", description="Fill (clear + type) text into a browser element.", parameters={"type": "object", "properties": {"selector": {"type": "string", "description": "CSS selector"}, "text": {"type": "string", "description": "Text to fill"}}, "required": ["selector", "text"]})
BROWSER_DOWNLOAD_SCHEMA = build_tool_schema(name="browser_download", description="Trigger a browser download by clicking a CSS selector and optionally save it to a destination path.", parameters={"type": "object", "properties": {"selector": {"type": "string", "description": "CSS selector that triggers the download"}, "destination_path": {"type": "string", "description": "Optional final path for the downloaded file (supports ~/... and absolute paths)"}, "timeout_seconds": {"type": "number", "description": "Seconds to wait for the download", "default": 30.0}}, "required": ["selector"]})
BROWSER_EXTRACT_SCHEMA = build_tool_schema(name="browser_extract", description="Extract text from the browser page or a specific element.", parameters={"type": "object", "properties": {"selector": {"type": "string", "description": "CSS selector (optional, omit for full page)"}}})
BROWSER_SCREENSHOT_SCHEMA = build_tool_schema(name="browser_screenshot", description="Take a screenshot of the current browser page.", parameters={"type": "object", "properties": {}})
BROWSER_TABS_SCHEMA = build_tool_schema(name="browser_tabs", description="List open browser tabs.", parameters={"type": "object", "properties": {}})
DESKTOP_OBSERVE_SCHEMA = build_tool_schema(name="desktop_observe", description="Observe the current desktop UI state (screenshot + OCR).", parameters={"type": "object", "properties": {}})
DESKTOP_FOCUS_SCHEMA = build_tool_schema(name="desktop_focus", description="Focus (activate) a desktop application by name.", parameters={"type": "object", "properties": {"app": {"type": "string", "description": "Application name"}}, "required": ["app"]})
DESKTOP_CLICK_SCHEMA = build_tool_schema(name="desktop_click", description="Click at absolute screen coordinates.", parameters={"type": "object", "properties": {"x": {"type": "integer", "description": "X coordinate"}, "y": {"type": "integer", "description": "Y coordinate"}}, "required": ["x", "y"]})
DESKTOP_TYPE_SCHEMA = build_tool_schema(name="desktop_type", description="Type text using the keyboard (into the focused application).", parameters={"type": "object", "properties": {"text": {"type": "string", "description": "Text to type"}}, "required": ["text"]})
DESKTOP_HOTKEY_SCHEMA = build_tool_schema(name="desktop_hotkey", description="Press a keyboard shortcut (e.g. ['command', 'c'] for copy).", parameters={"type": "object", "properties": {"keys": {"type": "array", "items": {"type": "string"}, "description": "List of keys (modifiers + key)"}}, "required": ["keys"]})


# ═══════════════════════════════════════════════════════════════════
#  Registration functions  (public API — imported by app.py, chat_factory.py)
# ═══════════════════════════════════════════════════════════════════

def register_experience_capabilities(registry: ChatCapabilityRegistry) -> None:
    """Register experience/discovery tools (25-2)."""
    registry.register("search_workflow_history", SEARCH_WORKFLOW_HISTORY_SCHEMA, handle_search_workflow_history, modes=list(ALL_MODES), category="experience")
    registry.register("get_workflow_details", GET_WORKFLOW_DETAILS_SCHEMA, handle_get_workflow_details, modes=list(ALL_MODES), category="experience")
    registry.register("search_run_history", SEARCH_RUN_HISTORY_SCHEMA, handle_search_run_history, modes=list(ALL_MODES), category="experience")
    registry.register("get_learned_principles", GET_LEARNED_PRINCIPLES_SCHEMA, handle_get_learned_principles, modes=list(ALL_MODES), category="experience")
    registry.register("discover_capabilities", DISCOVER_CAPABILITIES_SCHEMA, handle_discover_capabilities, modes=list(ALL_MODES), category="experience")


def register_publish_capabilities(registry: ChatCapabilityRegistry) -> None:
    """Register publish/share/export tools (25-3)."""
    registry.register("publish_workflow", PUBLISH_WORKFLOW_SCHEMA, handle_publish_workflow, modes=WRITE_MODES, category="publish")
    registry.register("unpublish_workflow", UNPUBLISH_WORKFLOW_SCHEMA, handle_unpublish_workflow, modes=WRITE_MODES, category="publish")
    registry.register("export_workflow", EXPORT_WORKFLOW_SCHEMA, handle_export_workflow, modes=WRITE_MODES, category="publish")
    registry.register("share_workflow", SHARE_WORKFLOW_SCHEMA, handle_share_workflow, modes=list(ALL_MODES), category="publish")
    registry.register("list_published", LIST_PUBLISHED_SCHEMA, handle_list_published, modes=list(ALL_MODES), category="publish")
    registry.register("get_publish_status", GET_PUBLISH_STATUS_SCHEMA, handle_get_publish_status, modes=list(ALL_MODES), category="publish")
    registry.register("import_block", IMPORT_BLOCK_SCHEMA, handle_import_block, modes=WRITE_MODES, category="blocks")
    registry.register("list_blocks", LIST_BLOCKS_SCHEMA, handle_list_blocks, modes=list(ALL_MODES), category="blocks")


def register_run_lifecycle_capabilities(registry: ChatCapabilityRegistry) -> None:
    """Register run lifecycle tools (25-4)."""
    registry.register("start_run", START_RUN_SCHEMA, handle_start_run, modes=RUN_WRITE_MODES, category="run")
    registry.register("get_run_status", GET_RUN_STATUS_SCHEMA, handle_get_run_status, modes=list(ALL_MODES), category="run")
    registry.register("list_active_runs", LIST_ACTIVE_RUNS_SCHEMA, handle_list_active_runs, modes=list(ALL_MODES), category="run")
    registry.register("cancel_run", CANCEL_RUN_SCHEMA, handle_cancel_run, modes=RUN_WRITE_MODES, category="run")
    registry.register("resume_run", RESUME_RUN_SCHEMA, handle_resume_run, modes=RUN_WRITE_MODES, category="run")
    registry.register("get_run_logs", GET_RUN_LOGS_SCHEMA, handle_get_run_logs, modes=list(ALL_MODES), category="run")
    registry.register("get_run_checkpoints", GET_RUN_CHECKPOINTS_SCHEMA, handle_get_run_checkpoints, modes=list(ALL_MODES), category="run")
    registry.register("rerun_from_checkpoint", RERUN_FROM_CHECKPOINT_SCHEMA, handle_rerun_from_checkpoint, modes=RUN_WRITE_MODES, category="run")
    registry.register("apply_pending_overlay", APPLY_PENDING_OVERLAY_SCHEMA, handle_apply_pending_overlay, modes=RUN_WRITE_MODES, category="run")
    registry.register("submit_human_input", SUBMIT_HUMAN_INPUT_SCHEMA, handle_submit_human_input, modes=RUN_WRITE_MODES, category="run")


def register_workflow_catalog_capabilities(registry: ChatCapabilityRegistry) -> None:
    """Register workflow catalog browsing tools (29-4)."""
    registry.register("list_my_workflows", LIST_MY_WORKFLOWS_SCHEMA, handle_list_my_workflows, modes=list(ALL_MODES), category="catalog")
    registry.register("search_workflows", SEARCH_WORKFLOWS_SCHEMA, handle_search_workflows, modes=list(ALL_MODES), category="catalog")
    registry.register("show_workflow", SHOW_WORKFLOW_SCHEMA, handle_show_workflow, modes=list(ALL_MODES), category="catalog")
    registry.register("fork_workflow", FORK_WORKFLOW_SCHEMA, handle_fork_workflow, modes=list(ALL_MODES), category="catalog")


def register_base_capabilities(registry: ChatCapabilityRegistry) -> None:
    """Register the foundational read-only tools (25-1)."""
    registry.register("list_graphs", LIST_GRAPHS_SCHEMA, handle_list_graphs, modes=list(ALL_MODES), category="graph")
    registry.register("get_activity", GET_ACTIVITY_SCHEMA, handle_get_activity, modes=list(ALL_MODES), category="run")
    registry.register("web_search", WEB_SEARCH_CAPABILITY_SCHEMA, handle_web_search, modes=["ask", "agent", "build", "mutate", "conversation", "debug"], category="web", cacheable=True)
    registry.register("file_read", FILE_READ_CAPABILITY_SCHEMA, handle_file_read, modes=list(ALL_MODES), category="file", cacheable=True)
    registry.register("file_grep", FILE_GREP_CAPABILITY_SCHEMA, handle_file_grep, modes=list(ALL_MODES), category="file", cacheable=True)
    registry.register("pdf_read", PDF_READ_CAPABILITY_SCHEMA, handle_pdf_read, modes=list(ALL_MODES), category="file", cacheable=True)
    registry.register("current_datetime", CURRENT_DATETIME_CAPABILITY_SCHEMA, handle_current_datetime, modes=list(ALL_MODES), category="system")
    registry.register("load_prompt_detail", LOAD_PROMPT_DETAIL_CAPABILITY_SCHEMA, handle_load_prompt_detail, modes=list(ALL_MODES), category="system", cacheable=True)
    registry.register("send_email", SEND_EMAIL_CAPABILITY_SCHEMA, handle_send_email, modes=WRITE_MODES, category="communication")
    registry.register("telegram_poll", TELEGRAM_POLL_CAPABILITY_SCHEMA, handle_telegram_poll, modes=["agent", "conversation"], category="communication")
    registry.register("screenshot", SCREENSHOT_CAPABILITY_SCHEMA, handle_screenshot, modes=list(ALL_MODES), category="system")
    registry.register("clipboard", CLIPBOARD_CAPABILITY_SCHEMA, handle_clipboard, modes=list(ALL_MODES), category="system")
    registry.register("set_config", SET_CONFIG_CAPABILITY_SCHEMA, handle_set_config, modes=WRITE_MODES, category="system")
    registry.register("get_config", GET_CONFIG_CAPABILITY_SCHEMA, handle_get_config, modes=list(ALL_MODES), category="system", cacheable=True)
    registry.register("list_directory", LIST_DIRECTORY_CAPABILITY_SCHEMA, handle_list_directory, modes=list(ALL_MODES), category="file", cacheable=True)
    registry.register("spreadsheet_read", SPREADSHEET_READ_CAPABILITY_SCHEMA, handle_spreadsheet_read, modes=list(ALL_MODES), category="data", cacheable=True)
    registry.register("web_fetch", WEB_FETCH_CAPABILITY_SCHEMA, handle_web_fetch, modes=list(ALL_MODES), category="web", cacheable=True)
    registry.register("file_write", FILE_WRITE_CAPABILITY_SCHEMA, handle_file_write, modes=WRITE_MODES, category="file")
    registry.register("shell_command", SHELL_COMMAND_CAPABILITY_SCHEMA, handle_shell_command, modes=WRITE_MODES, category="system")
    registry.register("http_request", HTTP_REQUEST_CAPABILITY_SCHEMA, handle_http_request, modes=WRITE_MODES, category="web")
    registry.register("text_chunk", TEXT_CHUNK_CAPABILITY_SCHEMA, handle_text_chunk, modes=list(ALL_MODES), category="text", cacheable=True)
    registry.register("text_translate", TEXT_TRANSLATE_CAPABILITY_SCHEMA, handle_text_translate, modes=list(ALL_MODES), category="text")
    registry.register("image_describe", IMAGE_DESCRIBE_CAPABILITY_SCHEMA, handle_image_describe, modes=list(ALL_MODES), category="media")
    registry.register("audio_transcribe", AUDIO_TRANSCRIBE_CAPABILITY_SCHEMA, handle_audio_transcribe, modes=list(ALL_MODES), category="media")
    registry.register("json_extract", JSON_EXTRACT_CAPABILITY_SCHEMA, handle_json_extract, modes=list(ALL_MODES), category="text", cacheable=True)
    registry.register("regex_match", REGEX_MATCH_CAPABILITY_SCHEMA, handle_regex_match, modes=list(ALL_MODES), category="text", cacheable=True)


def register_tool_capabilities(registry: ChatCapabilityRegistry) -> None:
    if os.environ.get('DAN_FULL_TOOLS', '1') == '0':
        return
    logger.info('DAN_FULL_TOOLS enabled: Registering extra built-in tools.')
    registry.register("python_eval", PYTHON_EVAL_CAPABILITY_SCHEMA, handle_python_eval, modes=["agent", "debug"], category="extra")
    registry.register("csv_read", CSV_READ_CAPABILITY_SCHEMA, handle_csv_read, modes=["agent", "conversation"], category="extra", cacheable=True)
    registry.register("compress", COMPRESS_CAPABILITY_SCHEMA, handle_compress, modes=["agent"], category="extra")
    registry.register("file_copy", FILE_COPY_CAPABILITY_SCHEMA, handle_file_copy, modes=["agent"], category="extra")
    registry.register("file_move", FILE_MOVE_CAPABILITY_SCHEMA, handle_file_move, modes=["agent"], category="extra")
    registry.register("file_delete", FILE_DELETE_CAPABILITY_SCHEMA, handle_file_delete, modes=["agent"], category="extra")
    registry.register("git_status", GIT_STATUS_CAPABILITY_SCHEMA, handle_git_status, modes=["agent", "ask", "debug"], category="extra", cacheable=True)
    registry.register("git_diff", GIT_DIFF_CAPABILITY_SCHEMA, handle_git_diff, modes=["agent", "ask", "debug"], category="extra", cacheable=True)
    registry.register("git_log", GIT_LOG_CAPABILITY_SCHEMA, handle_git_log, modes=["agent", "ask", "debug"], category="extra", cacheable=True)
    registry.register("git_branch", GIT_BRANCH_CAPABILITY_SCHEMA, handle_git_branch, modes=["agent"], category="extra")
    registry.register("git_commit", GIT_COMMIT_CAPABILITY_SCHEMA, handle_git_commit, modes=["agent"], category="extra")
    registry.register("git_worktree", GIT_WORKTREE_CAPABILITY_SCHEMA, handle_git_worktree, modes=["agent"], category="extra")
    registry.register("notify", NOTIFY_CAPABILITY_SCHEMA, handle_notify, modes=list(ALL_MODES), category="extra")
    registry.register("text_diff", TEXT_DIFF_CAPABILITY_SCHEMA, handle_text_diff, modes=["agent", "ask", "debug"], category="extra")


def register_introspection_capabilities(registry: ChatCapabilityRegistry) -> None:
    registry.register("inspect_node", INSPECT_NODE_SCHEMA, handle_inspect_node, modes=["agent", "ask", "debug"], category="introspection")
    registry.register("list_test_cases", LIST_TEST_CASES_SCHEMA, handle_list_test_cases, modes=["agent", "ask", "debug"], category="introspection")
    registry.register("run_test_case", RUN_TEST_CASE_SCHEMA, handle_run_test_case, modes=["agent", "debug"], category="introspection")


def register_computer_capabilities(
    registry: ChatCapabilityRegistry,
    controller: Any = None,
) -> None:
    """Register browser and desktop computer-control tools.

    Gated behind ``DAN_COMPUTER_CONTROL`` env var.  If *controller* is
    provided it is stored as the module-level instance used by all handlers.
    """
    if os.getenv("DAN_COMPUTER_CONTROL", "0") == "0":
        return

    if controller is not None:
        _set_controller(controller)

    logger.info("DAN_COMPUTER_CONTROL enabled: registering 13 computer-control tools.")

    registry.register("browser_open", BROWSER_OPEN_SCHEMA, handle_browser_open, modes=["agent"], category="computer")
    registry.register("browser_click", BROWSER_CLICK_SCHEMA, handle_browser_click, modes=["agent"], category="computer")
    registry.register("browser_type", BROWSER_TYPE_SCHEMA, handle_browser_type, modes=["agent"], category="computer")
    registry.register("browser_fill", BROWSER_FILL_SCHEMA, handle_browser_fill, modes=["agent"], category="computer")
    registry.register("browser_download", BROWSER_DOWNLOAD_SCHEMA, handle_browser_download, modes=["agent"], category="computer")
    registry.register("browser_extract", BROWSER_EXTRACT_SCHEMA, handle_browser_extract, modes=["agent", "ask"], category="computer")
    registry.register("browser_screenshot", BROWSER_SCREENSHOT_SCHEMA, handle_browser_screenshot, modes=["agent"], category="computer")
    registry.register("browser_tabs", BROWSER_TABS_SCHEMA, handle_browser_tabs, modes=["agent", "ask"], category="computer")
    registry.register("desktop_observe", DESKTOP_OBSERVE_SCHEMA, handle_desktop_observe, modes=["agent", "ask"], category="computer")
    registry.register("desktop_focus", DESKTOP_FOCUS_SCHEMA, handle_desktop_focus, modes=["agent"], category="computer")
    registry.register("desktop_click", DESKTOP_CLICK_SCHEMA, handle_desktop_click, modes=["agent"], category="computer")
    registry.register("desktop_type", DESKTOP_TYPE_SCHEMA, handle_desktop_type, modes=["agent"], category="computer")
    registry.register("desktop_hotkey", DESKTOP_HOTKEY_SCHEMA, handle_desktop_hotkey, modes=["agent"], category="computer")
