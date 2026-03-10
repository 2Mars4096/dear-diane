"""Capability handlers — thin wrappers around existing subsystems.

Each handler has signature:
    async def handler(args: dict, context: CapabilityContext) -> CapabilityResult

Sub-plans (25-2, 25-3, 25-4) add domain-specific handlers here.
"""

from __future__ import annotations

import json
import logging
import os
import atexit
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from dan.server.capability_registry import (
    ALL_MODES,
    READ_ONLY_MODES,
    CapabilityContext,
    CapabilityResult,
    ChatCapabilityRegistry,
    build_tool_schema,
)

logger = logging.getLogger(__name__)

# Write modes for publish/export/import tools
WRITE_MODES = ["agent", "build", "mutate"]


# ── Formatting helpers ─────────────────────────────────────────────

_EXPORT_CLEANUP_DELAY = 300  # seconds before temp export dirs are removed


def _schedule_export_cleanup(path: str) -> None:
    """Schedule removal of a temp export directory after a delay."""
    def _cleanup() -> None:
        shutil.rmtree(path, ignore_errors=True)
    timer = threading.Timer(_EXPORT_CLEANUP_DELAY, _cleanup)
    timer.daemon = True
    timer.start()


def _truncate(text: str, limit: int = 500) -> str:
    return text[:limit] + "…" if len(text) > limit else text


# ── Web search tool ────────────────────────────────────────────────

WEB_SEARCH_CAPABILITY_SCHEMA = build_tool_schema(
    name="web_search",
    description=(
        "Search the web for current information. Use when the user asks about "
        "live data: stock prices, exchange rates, weather, sports scores, "
        "recent events, or any time-sensitive facts you don't have."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query — be specific and include relevant keywords.",
            },
        },
        "required": ["query"],
    },
)


async def handle_web_search(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    query = args.get("query", "").strip()
    if not query:
        return CapabilityResult(success=False, message="No search query provided.")
    try:
        from dan.tools.web_search import web_search
        result = await web_search(query=query, num_results=5)
    except ImportError:
        return CapabilityResult(
            success=False,
            message="Web search is not available. Set DAN_TAVILY_API_KEY or DAN_BRAVE_API_KEY, or install duckduckgo-search.",
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Web search failed: {exc}")

    results = result.get("results", [])
    if not results:
        return CapabilityResult(success=True, message="No web results found for that query.")

    lines = []
    for item in results[:5]:
        title = item.get("title", "").strip()
        snippet = item.get("snippet", "").strip()
        url = item.get("url", "").strip()
        parts = [p for p in (title, snippet, url) if p]
        if parts:
            lines.append(" — ".join(parts))

    return CapabilityResult(
        success=True,
        message="\n\n".join(lines),
        data=result,
    )


# ── File read tools ────────────────────────────────────────────────

FILE_READ_CAPABILITY_SCHEMA = build_tool_schema(
    name="file_read",
    description=(
        "Read a text file and return its contents. Accepts absolute paths "
        "(~/Dropbox/..., /Users/...) or workspace-relative paths. "
        "Use when the user references a specific file to read, review, or analyze."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file (absolute or relative to workspace).",
            },
        },
        "required": ["path"],
    },
)


def _resolve_user_path(raw_path: str) -> Path:
    """Resolve a user-provided path, expanding ~ and handling absolute/relative."""
    expanded = Path(raw_path).expanduser()
    if expanded.is_absolute():
        return expanded
    workspace = Path(os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd()))
    return (workspace / expanded).resolve()


_FILE_READ_MAX = 100_000  # ~100KB text cap for chat context


async def handle_file_read(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    raw_path = args.get("path", "").strip()
    if not raw_path:
        return CapabilityResult(success=False, message="No file path provided.")
    try:
        resolved = _resolve_user_path(raw_path)
        if not resolved.is_file():
            return CapabilityResult(success=False, message=f"File not found: {raw_path}")
        content = resolved.read_text(encoding="utf-8", errors="replace")
        if len(content) > _FILE_READ_MAX:
            content = content[:_FILE_READ_MAX] + f"\n\n[truncated — file is {len(content):,} chars, showing first {_FILE_READ_MAX:,}]"
        return CapabilityResult(
            success=True,
            message=content,
            data={"path": str(resolved), "size": resolved.stat().st_size},
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Failed to read file: {exc}")


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
            "path": {
                "type": "string",
                "description": "Path to the PDF file (absolute or relative to workspace).",
            },
            "mode": {
                "type": "string",
                "enum": ["text", "vision"],
                "description": "text: extract text; vision: describe each page with a vision model.",
                "default": "text",
            },
            "start_page": {
                "type": "integer",
                "description": "First page to read (0-indexed).",
            },
            "end_page": {
                "type": "integer",
                "description": "Exclusive end page (0-indexed).",
            },
            "vision_model": {
                "type": "string",
                "description": "Vision model to use when mode='vision'.",
                "default": "gpt-4o",
            },
            "vision_prompt": {
                "type": "string",
                "description": "Optional custom prompt for vision mode.",
            },
        },
        "required": ["path"],
    },
)

_PDF_READ_MAX = 12_000  # chars — fits in LLM context alongside system prompt


async def handle_pdf_read(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    raw_path = args.get("path", "").strip()
    if not raw_path:
        return CapabilityResult(success=False, message="No PDF path provided.")
    try:
        from dan.tools.pdf_read import read_pdf_file

        resolved = _resolve_user_path(raw_path)
        if not resolved.is_file():
            return CapabilityResult(success=False, message=f"PDF not found: {raw_path}")
        result = await read_pdf_file(
            resolved_path=str(resolved),
            mode=str(args.get("mode") or "text"),
            start_page=args.get("start_page"),
            end_page=args.get("end_page"),
            vision_model=str(args.get("vision_model") or "gpt-4o"),
            vision_prompt=args.get("vision_prompt"),
        )
        full_text = str(result.get("text") or "")
        body_truncated = len(full_text) > _PDF_READ_MAX
        if body_truncated:
            full_text = full_text[:_PDF_READ_MAX] + (
                f"\n\n[truncated — {result.get('num_pages', '?')} pages, "
                f"showing first {_PDF_READ_MAX:,} chars]"
            )
        metadata = dict(result.get("metadata") or {})
        header = ""
        if metadata.get("title"):
            header = f"Title: {metadata['title']}\n"
        if metadata.get("author"):
            header += f"Author: {metadata['author']}\n"
        warning = str(result.get("warning") or "").strip()
        if warning:
            header += f"Warning: {warning}\n"
        if header:
            header += "\n"
        return CapabilityResult(
            success=True,
            message=f"{header}{full_text}",
            data={
                "path": str(resolved),
                "num_pages": result.get("num_pages"),
                "metadata": metadata,
                "mode": result.get("mode", args.get("mode") or "text"),
                "pages_requested": result.get("pages_requested"),
                "pages_returned": result.get("pages_returned"),
                "truncated": bool(result.get("truncated", False) or body_truncated),
            },
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Failed to read PDF: {exc}")


# ── Current datetime tool ─────────────────────────────────────────

CURRENT_DATETIME_CAPABILITY_SCHEMA = build_tool_schema(
    name="current_datetime",
    description=(
        "Get the current date and time. Use when the user asks about today's date, "
        "current time, day of the week, or needs time-relative calculations "
        "(e.g. 'how many days until June 1?', 'what day is it?')."
    ),
    parameters={
        "type": "object",
        "properties": {},
    },
)


async def handle_current_datetime(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    from datetime import datetime, timezone

    now = datetime.now()
    utc = datetime.now(timezone.utc)
    return CapabilityResult(
        success=True,
        message=f"Local: {now.strftime('%A, %B %d, %Y %I:%M %p')} | UTC: {utc.strftime('%Y-%m-%d %H:%M:%S')}",
        data={"local": now.isoformat(), "utc": utc.isoformat()},
    )


# ── Telegram poll tool ────────────────────────────────────────────

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
            "question": {
                "type": "string",
                "description": "The poll question (1-300 characters).",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Answer options (2-10 items, each 1-100 characters).",
            },
            "is_anonymous": {
                "type": "boolean",
                "description": "Whether votes are anonymous. Default: false.",
            },
            "allows_multiple": {
                "type": "boolean",
                "description": "Whether users can select multiple options. Default: false.",
            },
        },
        "required": ["question", "options"],
    },
)


async def handle_telegram_poll(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    question = args.get("question", "").strip()
    options = args.get("options", [])
    is_anonymous = args.get("is_anonymous", False)
    allows_multiple = args.get("allows_multiple", False)

    if not question:
        return CapabilityResult(success=False, message="Poll question is required.")
    if len(question) > 300:
        return CapabilityResult(
            success=False, message="Poll question must be 300 characters or less.",
        )
    if not isinstance(options, list) or len(options) < 2:
        return CapabilityResult(
            success=False, message="At least 2 poll options are required.",
        )
    if len(options) > 10:
        return CapabilityResult(
            success=False, message="Maximum 10 poll options allowed.",
        )

    cleaned: list[str] = []
    for i, opt in enumerate(options):
        if not isinstance(opt, str) or not opt.strip():
            return CapabilityResult(
                success=False, message=f"Option {i + 1} is empty.",
            )
        if len(opt) > 100:
            return CapabilityResult(
                success=False,
                message=f"Option {i + 1} exceeds 100 character limit.",
            )
        cleaned.append(opt.strip())

    return CapabilityResult(
        success=True,
        message=f'Poll created: "{question}" with {len(cleaned)} options.',
        data={
            "poll_request": True,
            "question": question,
            "options": cleaned,
            "is_anonymous": is_anonymous,
            "allows_multiple": allows_multiple,
        },
    )


# ── Send email tool ───────────────────────────────────────────────

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


async def handle_send_email(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    to = args.get("to", "").strip()
    subject = args.get("subject", "").strip()
    body = args.get("body", "").strip()
    if not to or not subject or not body:
        return CapabilityResult(success=False, message="'to', 'subject', and 'body' are all required.")

    smtp_host = os.environ.get("DAN_SMTP_HOST", "").strip()
    smtp_user = os.environ.get("DAN_SMTP_USER", "").strip()
    smtp_password = os.environ.get("DAN_SMTP_PASSWORD", "").strip()
    if not smtp_host or not smtp_user or not smtp_password:
        return CapabilityResult(
            success=False,
            message="Email not configured. Set DAN_SMTP_HOST, DAN_SMTP_USER, and DAN_SMTP_PASSWORD in .env.",
        )

    try:
        import aiosmtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        from_name = os.environ.get("DAN_SMTP_FROM_NAME", "DAN")
        port = int(os.environ.get("DAN_SMTP_PORT", "587"))

        msg = MIMEMultipart("alternative")
        msg["From"] = f"{from_name} <{smtp_user}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=port,
            username=smtp_user,
            password=smtp_password,
            use_tls=True,
        )
        return CapabilityResult(success=True, message=f"Email sent to {to}.")
    except ImportError:
        return CapabilityResult(success=False, message="aiosmtplib not installed. Run: pip install 'dan[messaging]'")
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Failed to send email: {exc}")


# ── Screenshot tool (macOS) ──────────────────────────────────────

SCREENSHOT_CAPABILITY_SCHEMA = build_tool_schema(
    name="screenshot",
    description=(
        "Take a screenshot of the current screen (macOS). "
        "Returns the path to the saved screenshot image. "
        "Use when the user asks to capture what's on screen."
    ),
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Optional filename (default: screenshot_<timestamp>.png)."},
        },
    },
)


async def handle_screenshot(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    import asyncio
    import sys
    from datetime import datetime

    if sys.platform != "darwin":
        return CapabilityResult(success=False, message="Screenshot is only supported on macOS.")

    filename = args.get("filename", "").strip()
    if not filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{ts}.png"

    dan_dir = Path.home() / ".dan" / "screenshots"
    dan_dir.mkdir(parents=True, exist_ok=True)
    filepath = dan_dir / filename

    proc = await asyncio.create_subprocess_exec(
        "screencapture", "-x", str(filepath),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        return CapabilityResult(success=False, message=f"screencapture failed: {stderr.decode()}")
    if not filepath.exists():
        return CapabilityResult(success=False, message="Screenshot file was not created.")
    return CapabilityResult(
        success=True,
        message=f"Screenshot saved to {filepath}",
        data={"path": str(filepath)},
    )


# ── Clipboard tool (macOS) ───────────────────────────────────────

CLIPBOARD_CAPABILITY_SCHEMA = build_tool_schema(
    name="clipboard",
    description=(
        "Read from or write to the system clipboard (macOS). "
        "Use 'read' to get current clipboard contents. "
        "Use 'write' to copy text to clipboard."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["read", "write"], "description": "read or write."},
            "content": {"type": "string", "description": "Text to copy (required for 'write')."},
        },
        "required": ["action"],
    },
)


async def handle_clipboard(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    import asyncio
    import sys

    if sys.platform != "darwin":
        return CapabilityResult(success=False, message="Clipboard is only supported on macOS.")

    action = args.get("action", "read").strip()

    if action == "read":
        proc = await asyncio.create_subprocess_exec(
            "pbpaste",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        content = stdout.decode("utf-8", errors="replace")
        if not content:
            return CapabilityResult(success=True, message="(clipboard is empty)")
        if len(content) > 10_000:
            content = content[:10_000] + "\n\n[truncated]"
        return CapabilityResult(success=True, message=content)

    elif action == "write":
        text = args.get("content", "")
        if not text:
            return CapabilityResult(success=False, message="No content provided to copy.")
        proc = await asyncio.create_subprocess_exec(
            "pbcopy",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate(input=text.encode("utf-8"))
        return CapabilityResult(success=True, message=f"Copied {len(text)} chars to clipboard.")

    return CapabilityResult(success=False, message=f"Unknown action: {action}. Use 'read' or 'write'.")


# ── Config tool (set env vars at runtime + persist to .env) ──────

_CONFIGURABLE_PREFIXES = (
    "DAN_SMTP_", "DAN_BRAVE_API_KEY", "DAN_TAVILY_API_KEY",
    "DAN_GOOGLE_API_KEY", "DAN_ANTHROPIC_API_KEY", "DAN_OPENAI_API_KEY",
    "DAN_STATA_", "DAN_MCP_", "DAN_TOOL_", "DAN_PATH_",
    "DAN_LLM_MODEL", "DAN_CHAT_MODEL", "DAN_LLM_BASE_URL",
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
    parameters={
        "type": "object",
        "properties": {},
    },
)

async def handle_get_config(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    import os
    from urllib.parse import urlparse

    model = getattr(ctx, "chat_manager", None)
    current_model = model._chat_model if model else os.environ.get("DAN_CHAT_MODEL") or os.environ.get("DAN_LLM_MODEL", "unknown")
    
    base_url = os.environ.get("DAN_LLM_BASE_URL", "")
    if base_url:
        try:
            parsed = urlparse(base_url)
            base_url = parsed.hostname or base_url
        except Exception:
            pass

    features = []
    for f in ("DAN_PROMPT_OPTIMIZATION", "DAN_MODEL_LEARNING", "DAN_TOPOLOGY_LEARNING", "DAN_SKILL_LEARNING", "DAN_MEMORY_EXTRACTION_LLM", "DAN_MEMORY_DUAL_WRITE"):
        if os.environ.get(f, "0") == "1":
            features.append(f)
            
    tier_policy = os.environ.get("DAN_ENABLE_TIER_POLICY", "0") == "1"
    
    mcp_servers = []
    if getattr(ctx, "mcp_bridge", None):
        mcp_servers = list(ctx.mcp_bridge.get_connected_servers().keys())

    config = {
        "model": current_model,
        "base_url_host": base_url,
        "bot_name": os.environ.get("DAN_BOT_NAME", "DAN"),
        "active_learning_features": features,
        "tier_policy_enabled": tier_policy,
        "mcp_servers_connected": mcp_servers,
    }
    
    import json
    return CapabilityResult(success=True, message=json.dumps(config, indent=2))

def _update_env_file(key: str, value: str) -> None:
    """Update or append a key=value in the .env file."""
    env_path = Path(os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd())) / ".env"
    if not env_path.exists():
        env_path.write_text(f"{key}={value}\n")
        return

    lines = env_path.read_text().splitlines(keepends=True)
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            lines[i] = f"{key}={value}\n"
            found = True
            break
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"{key}={value}\n")
    env_path.write_text("".join(lines))


async def handle_set_config(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    key = args.get("key", "").strip().upper()
    value = args.get("value", "").strip()
    if not key:
        return CapabilityResult(success=False, message="No key provided.")

    if not any(key.startswith(p) if p.endswith("_") else key == p for p in _CONFIGURABLE_PREFIXES):
        allowed = ", ".join(_CONFIGURABLE_PREFIXES)
        return CapabilityResult(
            success=False,
            message=f"Key '{key}' is not in the allowed list. Configurable prefixes: {allowed}",
        )

    os.environ[key] = value
    
    if key in ("DAN_LLM_MODEL", "DAN_CHAT_MODEL") and hasattr(ctx, "chat_manager") and ctx.chat_manager:
        ctx.chat_manager._chat_model = value
        
    try:
        from dan.utils.env import update_env_file
        update_env_file(key, value)
    except Exception as exc:
        return CapabilityResult(
            success=True,
            message=f"Set {key} in running server (but failed to persist to .env: {exc}). Will be lost on restart.",
        )

    display_value = value[:4] + "..." if len(value) > 8 else value
    return CapabilityResult(
        success=True,
        message=f"Set {key}={display_value} (active now + saved to .env).",
    )


# ── Remaining built-in tools as capabilities ──────────────────────

LIST_DIRECTORY_CAPABILITY_SCHEMA = build_tool_schema(
    name="list_directory",
    description=(
        "List files and directories at a given path. Accepts absolute paths "
        "(~/Dropbox/...) or workspace-relative. Supports glob filtering and recursive traversal."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path (absolute or relative)."},
            "glob_pattern": {"type": "string", "description": "Optional glob filter (e.g. '*.pdf')."},
            "recursive": {"type": "boolean", "description": "Recurse into subdirectories."},
        },
        "required": ["path"],
    },
)


SPREADSHEET_READ_CAPABILITY_SCHEMA = build_tool_schema(
    name="spreadsheet_read",
    description=(
        "Read an Excel spreadsheet (.xlsx, .xlsm, .xltx, .xltm) into structured rows. "
        "Use when the user asks to inspect or analyze spreadsheet files."
    ),
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


async def handle_spreadsheet_read(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    raw_path = args.get("path", "").strip()
    if not raw_path:
        return CapabilityResult(success=False, message="No spreadsheet path provided.")
    try:
        from dan.tools.spreadsheet_read import spreadsheet_read

        resolved = _resolve_user_path(raw_path)
        if not resolved.is_file():
            return CapabilityResult(success=False, message=f"Spreadsheet not found: {raw_path}")
        result = await spreadsheet_read(
            path=str(resolved),
            sheet=args.get("sheet"),
            max_rows=int(args.get("max_rows") or 1000),
        )
        rows = result.get("rows", [])
        preview = json.dumps(rows[:10], indent=2, default=str)
        if len(preview) > _FILE_READ_MAX:
            preview = preview[:_FILE_READ_MAX] + "\n\n[truncated]"
        message = (
            f"Headers: {result.get('headers', [])}\n"
            f"Rows returned: {result.get('row_count', 0)}"
        )
        if result.get("truncated"):
            message += f" of {result.get('total_rows', result.get('row_count', 0))}"
        message += f"\n\n{preview}"
        return CapabilityResult(success=True, message=message, data={"path": str(resolved), **result})
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Failed to read spreadsheet: {exc}")


TEXT_TRANSLATE_CAPABILITY_SCHEMA = build_tool_schema(
    name="text_translate",
    description=(
        "Translate text between languages using an LLM. "
        "Use when the user asks to translate text or cross-language content."
    ),
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


async def handle_text_translate(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    text = str(args.get("text", "")).strip()
    target_language = str(args.get("target_language", "")).strip()
    if not text or not target_language:
        return CapabilityResult(success=False, message="'text' and 'target_language' are required.")
    try:
        from dan.tools.text_translate import text_translate

        result = await text_translate(
            text=text,
            target_language=target_language,
            source_language=args.get("source_language"),
            model=str(args.get("model") or "gpt-4o-mini"),
        )
        return CapabilityResult(success=True, message=result.get("translated", ""), data=result)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Translation failed: {exc}")


IMAGE_DESCRIBE_CAPABILITY_SCHEMA = build_tool_schema(
    name="image_describe",
    description=(
        "Describe or analyze an image using a vision-capable model. "
        "Use for screenshots, charts, figures, and photos."
    ),
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


async def handle_image_describe(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    raw_path = args.get("path", "").strip()
    if not raw_path:
        return CapabilityResult(success=False, message="No image path provided.")
    try:
        from dan.tools.image_describe import image_describe

        resolved = _resolve_user_path(raw_path)
        if not resolved.is_file():
            return CapabilityResult(success=False, message=f"Image not found: {raw_path}")
        result = await image_describe(
            path=str(resolved),
            question=str(args.get("question") or "Describe this image in detail."),
            model=str(args.get("model") or "gpt-4o"),
        )
        return CapabilityResult(
            success=True,
            message=str(result.get("description") or ""),
            data={"path": str(resolved), **result},
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Failed to describe image: {exc}")


AUDIO_TRANSCRIBE_CAPABILITY_SCHEMA = build_tool_schema(
    name="audio_transcribe",
    description=(
        "Transcribe an audio or voice file to text. "
        "Use for voice notes, interviews, and spoken instructions."
    ),
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


async def handle_audio_transcribe(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    raw_path = args.get("path", "").strip()
    if not raw_path:
        return CapabilityResult(success=False, message="No audio path provided.")
    try:
        from dan.tools.audio_transcribe import audio_transcribe

        resolved = _resolve_user_path(raw_path)
        if not resolved.is_file():
            return CapabilityResult(success=False, message=f"Audio file not found: {raw_path}")
        result = await audio_transcribe(
            path=str(resolved),
            language=args.get("language"),
            model=str(args.get("model") or "whisper-1"),
        )
        return CapabilityResult(
            success=True,
            message=str(result.get("text") or ""),
            data={"path": str(resolved), **result},
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Failed to transcribe audio: {exc}")


async def handle_list_directory(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    raw_path = args.get("path", "").strip()
    if not raw_path:
        return CapabilityResult(success=False, message="No directory path provided.")
    resolved = _resolve_user_path(raw_path)
    if not resolved.is_dir():
        return CapabilityResult(success=False, message=f"Directory not found: {raw_path}")
    glob_pattern = args.get("glob_pattern", "")
    recursive = args.get("recursive", False)
    try:
        if glob_pattern:
            iterator = resolved.rglob(glob_pattern) if recursive else resolved.glob(glob_pattern)
        else:
            iterator = resolved.rglob("*") if recursive else resolved.iterdir()
        entries = []
        for p in sorted(iterator):
            kind = "dir" if p.is_dir() else "file"
            size = p.stat().st_size if p.is_file() else 0
            entries.append(f"  {kind}  {size:>8}  {p.name}")
            if len(entries) >= 200:
                entries.append(f"  ... (truncated at 200 entries)")
                break
        return CapabilityResult(
            success=True,
            message=f"{resolved}/\n" + "\n".join(entries) if entries else f"{resolved}/ (empty)",
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Failed to list directory: {exc}")


WEB_FETCH_CAPABILITY_SCHEMA = build_tool_schema(
    name="web_fetch",
    description=(
        "Fetch content from a URL and return it as text. "
        "Use when the user shares a link and asks to read, summarize, or extract info from it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch."},
        },
        "required": ["url"],
    },
)


async def handle_web_fetch(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    url = args.get("url", "").strip()
    if not url:
        return CapabilityResult(success=False, message="No URL provided.")
    try:
        from dan.tools.web_fetch import web_fetch
        result = await web_fetch(url=url)
        content = result.get("content", "")
        if len(content) > _FILE_READ_MAX:
            content = content[:_FILE_READ_MAX] + "\n\n[truncated]"
        return CapabilityResult(success=True, message=content, data=result)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Failed to fetch URL: {exc}")


FILE_WRITE_CAPABILITY_SCHEMA = build_tool_schema(
    name="file_write",
    description=(
        "Write or append content to a file. Accepts absolute paths. "
        "Use when the user asks to save, create, or write content to a file."
    ),
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


async def handle_file_write(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    raw_path = args.get("path", "").strip()
    content = args.get("content", "")
    mode = args.get("mode", "overwrite")
    if not raw_path:
        return CapabilityResult(success=False, message="No file path provided.")
    try:
        resolved = _resolve_user_path(raw_path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with open(resolved, "a", encoding="utf-8") as f:
                f.write(content)
        else:
            resolved.write_text(content, encoding="utf-8")
        return CapabilityResult(
            success=True,
            message=f"Wrote {len(content):,} chars to {resolved}",
            data={"path": str(resolved), "size": len(content)},
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Failed to write file: {exc}")


SHELL_COMMAND_CAPABILITY_SCHEMA = build_tool_schema(
    name="shell_command",
    description=(
        "Execute a shell command and return its output. "
        "Use when the user asks to run a command, check system info, or perform a terminal operation."
    ),
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


async def handle_shell_command(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    command = args.get("command", "").strip()
    if not command:
        return CapabilityResult(success=False, message="No command provided.")
    try:
        from dan.tools.shell_command import shell_command
        result = await shell_command(
            command=command,
            working_directory=args.get("working_directory", ""),
            timeout=args.get("timeout", 30),
        )
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        code = result.get("return_code", -1)
        parts = []
        if stdout:
            parts.append(stdout[:_FILE_READ_MAX])
        if stderr:
            parts.append(f"stderr: {stderr[:2000]}")
        parts.append(f"exit code: {code}")
        return CapabilityResult(success=code == 0, message="\n".join(parts), data=result)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Command failed: {exc}")


HTTP_REQUEST_CAPABILITY_SCHEMA = build_tool_schema(
    name="http_request",
    description=(
        "Send an HTTP request (GET, POST, PUT, DELETE, etc.). "
        "Use for REST API calls when the user asks to interact with an external service."
    ),
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


async def handle_http_request(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    url = args.get("url", "").strip()
    if not url:
        return CapabilityResult(success=False, message="No URL provided.")
    try:
        from dan.tools.http_request import http_request
        result = await http_request(
            url=url,
            method=args.get("method", "GET"),
            headers=args.get("headers", {}),
            body=args.get("body", ""),
        )
        body = result.get("body", "")
        if len(body) > _FILE_READ_MAX:
            body = body[:_FILE_READ_MAX] + "\n\n[truncated]"
        status = result.get("status_code", 0)
        return CapabilityResult(
            success=200 <= status < 400,
            message=f"HTTP {status}\n\n{body}",
            data=result,
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"HTTP request failed: {exc}")


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


async def handle_text_chunk(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    text = args.get("text", "")
    if not text:
        return CapabilityResult(success=False, message="No text provided.")
    try:
        from dan.tools.text_chunk import text_chunk
        result = await text_chunk(
            text=text,
            chunk_size=args.get("chunk_size", 1000),
            overlap=args.get("overlap", 200),
        )
        chunks = result.get("chunks", [])
        return CapabilityResult(
            success=True,
            message=f"Split into {len(chunks)} chunks.",
            data=result,
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Chunking failed: {exc}")


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


async def handle_json_extract(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    data = args.get("data", "")
    path = args.get("path", "")
    if not data or not path:
        return CapabilityResult(success=False, message="Both 'data' and 'path' are required.")
    try:
        from dan.tools.json_extract import json_extract
        result = await json_extract(data=data, path=path)
        value = result.get("value")
        return CapabilityResult(success=True, message=json.dumps(value, indent=2, default=str), data=result)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"JSON extraction failed: {exc}")


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


async def handle_regex_match(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    text = args.get("text", "")
    pattern = args.get("pattern", "")
    if not text or not pattern:
        return CapabilityResult(success=False, message="Both 'text' and 'pattern' are required.")
    try:
        from dan.tools.regex_match import regex_match
        result = await regex_match(text=text, pattern=pattern, replacement=args.get("replacement"))
        if "result" in result:
            return CapabilityResult(success=True, message=result["result"][:_FILE_READ_MAX], data=result)
        matches = result.get("matches", [])
        return CapabilityResult(
            success=True,
            message=f"{len(matches)} matches found:\n" + "\n".join(str(m) for m in matches[:50]),
            data=result,
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Regex operation failed: {exc}")


# ── Graph tools ────────────────────────────────────────────────────

LIST_GRAPHS_SCHEMA = build_tool_schema(
    name="list_graphs",
    description=(
        "List all saved workflows. Use when the user asks "
        "'what workflows exist?', 'show my workflows', or 'list graphs'."
    ),
    parameters={
        "type": "object",
        "properties": {},
    },
)


async def handle_list_graphs(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    graphs = ctx.graph_store.list_graphs()
    if not graphs:
        return CapabilityResult(
            success=True,
            message="No workflows found.",
            data=[],
            output_preview="No workflows found.",
        )
    lines = []
    for g in graphs:
        gid = g.get("graph_id", g.get("id", "?"))
        node_count = len(g.get("nodes", []))
        edge_count = len(g.get("edges", []))
        lines.append(f"- **{gid}** ({node_count} nodes, {edge_count} edges)")
    text = f"Found {len(graphs)} workflow(s):\n" + "\n".join(lines)
    return CapabilityResult(
        success=True,
        message=text,
        data=graphs,
        output_preview=_truncate(text),
    )


# ── Activity tools ─────────────────────────────────────────────────

GET_ACTIVITY_SCHEMA = build_tool_schema(
    name="get_activity",
    description=(
        "Show current run activity: active runs, recent completions, and "
        "connected surfaces. Use when the user asks 'what's running?', "
        "'show activity', or 'any active runs?'."
    ),
    parameters={
        "type": "object",
        "properties": {},
    },
)


async def handle_get_activity(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.activity_tracker is None:
        return CapabilityResult(
            success=False,
            message="Activity tracker not available.",
        )
    snapshot = ctx.activity_tracker.get_activity()
    active = snapshot.get("active", [])
    recent = snapshot.get("recent", [])
    surfaces = snapshot.get("connected_surfaces", [])
    parts = []
    if active:
        parts.append(f"**Active runs ({len(active)}):**")
        for r in active[:10]:
            rid = r.get("run_id", "?")
            gid = r.get("graph_id", "?")
            status = r.get("status", "?")
            parts.append(f"  - {rid} ({gid}) — {status}")
    else:
        parts.append("No active runs.")

    if recent:
        parts.append(f"\n**Recent ({len(recent)}):**")
        for r in recent[:5]:
            rid = r.get("run_id", "?")
            gid = r.get("graph_id", "?")
            status = r.get("status", "?")
            parts.append(f"  - {rid} ({gid}) — {status}")

    if surfaces:
        parts.append(f"\n**Connected surfaces:** {len(surfaces)}")

    text = "\n".join(parts)
    return CapabilityResult(
        success=True,
        message=text,
        data=snapshot,
        output_preview=_truncate(text),
    )


# ── Experience tools (25-2) ─────────────────────────────────────────

SEARCH_WORKFLOW_HISTORY_SCHEMA = build_tool_schema(
    name="search_workflow_history",
    description=(
        "Search past workflows by semantic similarity. Use when the user asks "
        "'have we done X before?', 'show workflows similar to Y', or 'find past work on Z'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language search query"},
            "top_k": {"type": "integer", "description": "Max results to return", "default": 5},
        },
        "required": ["query"],
    },
)

GET_WORKFLOW_DETAILS_SCHEMA = build_tool_schema(
    name="get_workflow_details",
    description=(
        "Get detailed info about a specific workflow: name, description, success rate, "
        "node types, tools used, failure/success patterns. Use when the user asks "
        "'tell me about workflow X' or 'what's the success rate of Y?'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string", "description": "Workflow ID to look up"},
        },
        "required": ["workflow_id"],
    },
)

SEARCH_RUN_HISTORY_SCHEMA = build_tool_schema(
    name="search_run_history",
    description=(
        "Search past runs by workflow, status, or date range. Use when the user asks "
        "'show failed runs', 'recent runs', 'runs for workflow X', or 'runs from last week'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string", "description": "Filter by workflow ID (optional)"},
            "status": {"type": "string", "enum": ["completed", "failed", "cancelled"], "description": "Filter by status"},
            "limit": {"type": "integer", "description": "Max results", "default": 20},
            "offset": {"type": "integer", "description": "Skip N results", "default": 0},
            "after": {"type": "number", "description": "Unix timestamp: only runs after this time"},
            "before": {"type": "number", "description": "Unix timestamp: only runs before this time"},
        },
    },
)

GET_LEARNED_PRINCIPLES_SCHEMA = build_tool_schema(
    name="get_learned_principles",
    description=(
        "Query causal principles learned from past failures. Use when the user asks "
        "'what have we learned from failures?', 'show principles for workflow X', "
        "or 'what do we know about tool/timeout errors?'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Substring filter on condition/action/reason (optional)"},
            "workflow_id": {"type": "string", "description": "Scope to workflow, or omit for global"},
            "min_confidence": {"type": "number", "description": "Min confidence 0–1", "default": 0.3},
            "limit": {"type": "integer", "description": "Max results", "default": 10},
        },
    },
)

DISCOVER_CAPABILITIES_SCHEMA = build_tool_schema(
    name="discover_capabilities",
    description=(
        "Discover available tools, skills, patterns, and relevant past workflows. "
        "Use when the user asks 'what can DAN do?', 'what patterns exist for RAG?', "
        "or 'what tools/skills are available?'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query for workflows and self-knowledge"},
            "top_k": {"type": "integer", "description": "Max workflow matches", "default": 5},
        },
        "required": ["query"],
    },
)


def _format_experience_summary(exp: Any, score: float | None = None) -> str:
    """Format a WorkflowExperience for chat display."""
    parts = [f"**{exp.workflow_id}**"]
    if score is not None:
        parts[0] += f" (score: {score:.2f})"
    if exp.name:
        parts.append(f"  Name: {exp.name}")
    desc = _truncate(exp.description, 200) if exp.description else ""
    if desc:
        parts.append(f"  Description: {desc}")
    if exp.run_count > 0:
        rate = 100 * exp.success_count / exp.run_count
        parts.append(f"  Success rate: {rate:.0f}% ({exp.success_count}/{exp.run_count} runs)")
    if exp.node_types_used:
        parts.append(f"  Node types: {', '.join(exp.node_types_used[:5])}")
    if exp.tools_used:
        parts.append(f"  Tools: {', '.join(exp.tools_used[:5])}")
    return " — ".join(parts)


async def handle_search_workflow_history(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    query = str(args.get("query", "")).strip()
    top_k = max(1, min(int(args.get("top_k", 5)), 20))

    if not query:
        return CapabilityResult(success=False, message="query is required.")

    store = ctx.experience_store
    index = ctx.experience_index

    if store is None and index is None:
        return CapabilityResult(
            success=False,
            message="Experience store not available. Semantic search requires embedding provider.",
        )

    if index is not None:
        try:
            hits = await index.search_similar(query, top_k=top_k)
            results: list[tuple[str, float]] = hits
        except Exception as exc:
            logger.debug("ExperienceIndex.search_similar failed, falling back to lexical", exc_info=True)
            results = []
            index = None
    else:
        results = []

    if not results and store is not None:
        experiences = await store.list_experiences()
        q_lower = query.lower()
        scored: list[tuple[Any, float]] = []
        for exp in experiences:
            text = f"{exp.name} {exp.description} {' '.join(exp.tags or [])}".lower()
            if q_lower in text:
                pos = text.find(q_lower)
                rank = 1.0 - (pos / max(len(text), 1)) * 0.3
                scored.append((exp, min(1.0, rank)))
        scored.sort(key=lambda x: x[1], reverse=True)
        results = [(e.workflow_id, s) for e, s in scored[:top_k]]

    if not results:
        return CapabilityResult(
            success=True,
            message="No similar workflows found.",
            data=[],
            output_preview="No similar workflows found.",
        )

    lines = []
    data: list[dict[str, Any]] = []
    for wf_id, score in results:
        exp = None
        if store is not None:
            exp = await store.load_experience(wf_id)
        if exp is None:
            lines.append(f"- **{wf_id}** (score: {score:.2f}) — no experience record")
            data.append({"workflow_id": wf_id, "score": score})
        else:
            line = f"- {_format_experience_summary(exp, score)}"
            lines.append(line)
            data.append({
                "workflow_id": wf_id,
                "score": score,
                "name": exp.name,
                "description": exp.description,
                "success_rate": exp.success_count / exp.run_count if exp.run_count > 0 else None,
            })

    text = f"Found {len(lines)} similar workflow(s):\n" + "\n".join(lines)
    return CapabilityResult(
        success=True,
        message=text,
        data=data,
        output_preview=_truncate(text),
    )


async def handle_get_workflow_details(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    workflow_id = str(args.get("workflow_id", "")).strip()
    if not workflow_id:
        return CapabilityResult(success=False, message="workflow_id is required.")

    store = ctx.experience_store
    if store is None:
        return CapabilityResult(success=False, message="Experience store not available.")

    exp = await store.load_experience(workflow_id)
    if exp is None:
        return CapabilityResult(
            success=True,
            message=f"No experience recorded for workflow '{workflow_id}'.",
            data=None,
            output_preview=f"No experience recorded for '{workflow_id}'.",
        )

    parts = [
        f"**{exp.workflow_id}**",
        f"Name: {exp.name or '(none)'}",
        f"Description: {_truncate(exp.description or '', 300)}",
    ]
    if exp.run_count > 0:
        rate = 100 * exp.success_count / exp.run_count
        parts.append(f"Success rate: {rate:.0f}% ({exp.success_count}/{exp.run_count} runs)")
    if exp.last_run_at:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(exp.last_run_at, tz=timezone.utc)
        parts.append(f"Last run: {dt.isoformat()}")
    if exp.node_types_used:
        parts.append(f"Node types: {', '.join(exp.node_types_used)}")
    if exp.tools_used:
        parts.append(f"Tools used: {', '.join(exp.tools_used)}")
    if exp.failure_patterns:
        parts.append(f"Failure patterns: {'; '.join(exp.failure_patterns[:3])}")
    if exp.success_patterns:
        parts.append(f"Success patterns: {'; '.join(exp.success_patterns[:3])}")

    text = "\n".join(parts)
    return CapabilityResult(
        success=True,
        message=text,
        data=exp.model_dump() if hasattr(exp, "model_dump") else exp,
        output_preview=_truncate(text),
    )


async def handle_search_run_history(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    run_store = ctx.run_store
    if run_store is None:
        return CapabilityResult(success=False, message="Run store not available.")

    workflow_id = args.get("workflow_id") or None
    if workflow_id is not None:
        workflow_id = str(workflow_id).strip() or None
    status = args.get("status")
    limit = max(1, min(int(args.get("limit", 20)), 100))
    offset = max(0, int(args.get("offset", 0)))
    after = args.get("after")
    before = args.get("before")
    if after is not None:
        after = float(after)
    if before is not None:
        before = float(before)

    summaries = run_store.list_summaries(
        workflow_id=workflow_id,
        status=status,
        after=after,
        before=before,
        limit=limit,
        offset=offset,
    )

    if not summaries:
        return CapabilityResult(
            success=True,
            message="No runs found.",
            data=[],
            output_preview="No runs found.",
        )

    lines = []
    for s in summaries:
        rid = s.get("run_id", "?")
        gid = s.get("workflow_id", s.get("graph_id", "?"))
        st = s.get("status", "?")
        elapsed = s.get("elapsed_seconds")
        cost = s.get("total_cost")
        tokens = s.get("total_tokens")
        err = s.get("error", "")
        line = f"- **{rid}** ({gid}) — {st}"
        if elapsed is not None:
            line += f", {elapsed:.1f}s"
        if cost is not None:
            line += f", ${cost:.4f}"
        if tokens is not None:
            line += f", {tokens} tokens"
        if err:
            line += f" — error: {_truncate(err, 80)}"
        lines.append(line)

    text = f"Found {len(summaries)} run(s):\n" + "\n".join(lines)
    return CapabilityResult(
        success=True,
        message=text,
        data=summaries,
        output_preview=_truncate(text),
    )


async def handle_get_learned_principles(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    principle_store = ctx.principle_store
    if principle_store is None:
        return CapabilityResult(success=False, message="Principle store not available.")

    query = str(args.get("query", "")).strip()
    workflow_id = args.get("workflow_id")
    use_global = not (workflow_id and str(workflow_id).strip())
    scope = "global" if use_global else "workflow"
    wf_id = "_global" if use_global else str(workflow_id).strip()
    min_confidence = max(0.0, min(1.0, float(args.get("min_confidence", 0.3))))
    limit = max(1, min(int(args.get("limit", 10)), 50))

    principles = await principle_store.load_principles(
        wf_id,
        min_confidence=min_confidence,
        scope=scope,
    )

    if query:
        q_lower = query.lower()
        principles = [
            p for p in principles
            if q_lower in (p.condition or "").lower()
            or q_lower in (p.action or "").lower()
            or q_lower in (p.reason or "").lower()
        ]

    principles = sorted(principles, key=lambda p: p.confidence, reverse=True)[:limit]

    if not principles:
        return CapabilityResult(
            success=True,
            message="No learned principles found.",
            data=[],
            output_preview="No learned principles found.",
        )

    lines = []
    for i, p in enumerate(principles, 1):
        cond = _truncate(p.condition or "", 100)
        act = _truncate(p.action or "", 100)
        conf = int(p.confidence * 100)
        wf = p.workflow_id or "unknown"
        lines.append(f"{i}. If {cond} → {act} (confidence: {conf}%, from {wf})")

    scope_label = "global" if scope == "global" else f"workflow {wf_id}"
    text = f"{len(principles)} principle(s) ({scope_label}):\n" + "\n".join(lines)
    return CapabilityResult(
        success=True,
        message=text,
        data=[p.model_dump() if hasattr(p, "model_dump") else p for p in principles],
        output_preview=_truncate(text),
    )


async def handle_discover_capabilities(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    discovery = ctx.discovery_service
    if discovery is None:
        return CapabilityResult(success=False, message="Discovery service not available.")

    query = str(args.get("query", "")).strip()
    top_k = max(1, min(int(args.get("top_k", 5)), 20))

    result = await discovery.discover_all(query, top_k=top_k)

    parts = []
    if result.tools:
        parts.append("**Tools:**")
        for t in result.tools[:10]:
            parts.append(f"  - {t.tool_id}: {_truncate(t.description or '', 80)}")
    if result.skills:
        parts.append("\n**Skills:**")
        for s in result.skills[:10]:
            parts.append(f"  - {s.name}: {_truncate(s.description or '', 80)}")
    if result.patterns:
        parts.append("\n**Patterns:**")
        for p in result.patterns[:10]:
            parts.append(f"  - {p.name}: {_truncate(p.description or '', 80)}")
    if result.workflows:
        parts.append("\n**Relevant workflows:**")
        for w in result.workflows[:top_k]:
            rate = f", {int((w.success_rate or 0) * 100)}% success" if w.success_rate is not None else ""
            parts.append(f"  - {w.workflow_id} (score: {w.score:.2f}{rate})")
    if result.self_knowledge_formatted:
        sk = _truncate(result.self_knowledge_formatted, 500)
        parts.append(f"\n**Self-knowledge:** {sk}")

    text = "\n".join(parts) if parts else "No capabilities found."
    return CapabilityResult(
        success=True,
        message=text,
        data={
            "tools": [t.model_dump() for t in result.tools],
            "skills": [s.model_dump() for s in result.skills],
            "patterns": [p.model_dump() for p in result.patterns],
            "workflows": [w.model_dump() for w in result.workflows],
        },
        output_preview=_truncate(text),
    )


def register_experience_capabilities(registry: ChatCapabilityRegistry) -> None:
    """Register experience/discovery tools (25-2)."""
    registry.register(
        "search_workflow_history",
        SEARCH_WORKFLOW_HISTORY_SCHEMA,
        handle_search_workflow_history,
        modes=list(ALL_MODES),
        category="experience",
    )
    registry.register(
        "get_workflow_details",
        GET_WORKFLOW_DETAILS_SCHEMA,
        handle_get_workflow_details,
        modes=list(ALL_MODES),
        category="experience",
    )
    registry.register(
        "search_run_history",
        SEARCH_RUN_HISTORY_SCHEMA,
        handle_search_run_history,
        modes=list(ALL_MODES),
        category="experience",
    )
    registry.register(
        "get_learned_principles",
        GET_LEARNED_PRINCIPLES_SCHEMA,
        handle_get_learned_principles,
        modes=list(ALL_MODES),
        category="experience",
    )
    registry.register(
        "discover_capabilities",
        DISCOVER_CAPABILITIES_SCHEMA,
        handle_discover_capabilities,
        modes=list(ALL_MODES),
        category="experience",
    )


# ── Publish & Share tools (25-3) ─────────────────────────────────────

PUBLISH_WORKFLOW_SCHEMA = build_tool_schema(
    name="publish_workflow",
    description=(
        "Publish a workflow to the MCP/HTTP endpoint. Use when the user asks "
        "'publish this workflow', 'make it available as MCP', or 'publish as API'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "graph_id": {"type": "string", "description": "Workflow ID (defaults to active workflow)"},
            "name_override": {"type": "string", "description": "Override workflow name in registry"},
            "api_key": {"type": "string", "description": "Optional API key for auth"},
            "rate_limit": {"type": "integer", "description": "Optional max requests per minute"},
        },
    },
)

UNPUBLISH_WORKFLOW_SCHEMA = build_tool_schema(
    name="unpublish_workflow",
    description=(
        "Remove a workflow from the publish registry. Use when the user asks "
        "'unpublish', 'stop publishing', or 'remove from API'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "graph_id": {"type": "string", "description": "Workflow ID (defaults to active workflow)"},
        },
    },
)

EXPORT_WORKFLOW_SCHEMA = build_tool_schema(
    name="export_workflow",
    description=(
        "Export a workflow as block, markdown, or Python. Use when the user asks "
        "'export as block', 'export to markdown', 'export as Python', or 'share as block'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "graph_id": {"type": "string", "description": "Workflow ID (defaults to active workflow)"},
            "format": {
                "type": "string",
                "enum": ["block", "markdown", "python"],
                "description": "Export format",
            },
            "name": {"type": "string", "description": "Block/workflow name (for block format)"},
            "version": {"type": "string", "description": "Block version (for block format)", "default": "0.1.0"},
            "node_id": {"type": "string", "description": "Composite node ID (for composite block export)"},
        },
        "required": ["format"],
    },
)

SHARE_WORKFLOW_SCHEMA = build_tool_schema(
    name="share_workflow",
    description=(
        "Generate shareable config: MCP client config, API docs, or OpenAPI spec. "
        "Use when the user asks 'share as MCP config', 'API docs', 'OpenAPI spec', or 'how to connect'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "graph_id": {"type": "string", "description": "Workflow ID (defaults to active workflow)"},
            "format": {
                "type": "string",
                "enum": ["mcp_config", "api_docs", "openapi"],
                "description": "Share format",
            },
            "base_url": {"type": "string", "description": "Base URL for API docs", "default": "http://localhost:8001"},
        },
        "required": ["format"],
    },
)

LIST_PUBLISHED_SCHEMA = build_tool_schema(
    name="list_published",
    description=(
        "List all currently published workflows. Use when the user asks "
        "'what's published?', 'list published workflows', or 'show published APIs'."
    ),
    parameters={"type": "object", "properties": {}},
)

GET_PUBLISH_STATUS_SCHEMA = build_tool_schema(
    name="get_publish_status",
    description=(
        "Check whether a workflow is published and return saved config. Use when the user asks "
        "'is this published?', 'publish status', or 'check if published'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "graph_id": {"type": "string", "description": "Workflow ID (defaults to active workflow)"},
        },
    },
)

IMPORT_BLOCK_SCHEMA = build_tool_schema(
    name="import_block",
    description=(
        "Install a block from a path or URL. Use when the user asks "
        "'import block', 'install block from X', or 'add block from path'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Path to block dir/tarball or URL"},
            "scope": {"type": "string", "enum": ["user", "workspace"], "description": "Install scope", "default": "user"},
            "workspace": {"type": "string", "description": "Workspace path (required when scope=workspace)"},
            "force": {"type": "boolean", "description": "Overwrite existing", "default": False},
        },
        "required": ["source"],
    },
)

LIST_BLOCKS_SCHEMA = build_tool_schema(
    name="list_blocks",
    description=(
        "List all installed blocks. Use when the user asks "
        "'list blocks', 'what blocks are installed?', or 'show blocks'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "scope": {"type": "string", "description": "Filter by scope (optional, not yet used)"},
        },
    },
)


def _resolve_graph(ctx: CapabilityContext, graph_id: str | None) -> tuple[str, dict | None, "Graph | None"]:
    """Resolve graph_id to graph data and Graph model. Returns (graph_id, raw_data, graph_model)."""
    gid = (graph_id or ctx.workflow_id or "").strip()
    if not gid:
        return ("", None, None)
    if ctx.graph_store is None:
        return (gid, None, None)
    data = ctx.graph_store.get_graph(gid)
    if data is None:
        return (gid, None, None)
    try:
        from dan.models.graph import Graph
        graph = Graph.model_validate(data)
        return (gid, data, graph)
    except Exception:
        return (gid, data, None)


async def handle_publish_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.publish_registry is None:
        return CapabilityResult(success=False, message="Publish registry not available.")
    _, _, graph = _resolve_graph(ctx, args.get("graph_id"))
    if graph is None:
        gid = (args.get("graph_id") or ctx.workflow_id or "").strip()
        return CapabilityResult(
            success=False,
            message=f"Graph not found: {gid or '(no workflow_id)'}",
        )
    gid = (args.get("graph_id") or ctx.workflow_id or "").strip()
    name_override = args.get("name_override")
    api_key = args.get("api_key")
    rate_limit = args.get("rate_limit")
    try:
        slug = ctx.publish_registry.register(
            graph,
            name_override=name_override,
            api_key=api_key,
            rate_limit=rate_limit,
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Publish failed: {exc}")
    graphs_dir = ctx.graphs_dir or "./graphs"
    pub_config = {"enabled": True, "api_key": api_key, "rate_limit": rate_limit}
    pub_path = Path(graphs_dir) / f"{gid}.publish.json"
    try:
        pub_path.write_text(json.dumps(pub_config, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to persist .publish.json: %s", exc)
    return CapabilityResult(
        success=True,
        message=f"Published workflow as **{slug}**. Config saved to {pub_path.name}.",
        data={"slug": slug, "graph_id": gid},
        output_preview=f"Published as {slug}",
    )


async def handle_unpublish_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.publish_registry is None:
        return CapabilityResult(success=False, message="Publish registry not available.")
    gid, _, graph = _resolve_graph(ctx, args.get("graph_id"))
    if graph is None:
        return CapabilityResult(
            success=False,
            message=f"Graph not found: {gid or '(no workflow_id)'}",
        )
    from dan.publish.schema import slugify as _slugify
    from dan.utils.workflow_interface import derive_workflow_interface
    try:
        iface = derive_workflow_interface(graph)
        slug = _slugify(iface.name)
    except Exception:
        slug = gid
    ctx.publish_registry.unregister(slug)
    graphs_dir = ctx.graphs_dir or "./graphs"
    pub_path = Path(graphs_dir) / f"{gid}.publish.json"
    if pub_path.exists():
        try:
            pub_path.unlink()
        except Exception as exc:
            logger.warning("Failed to remove .publish.json: %s", exc)
    return CapabilityResult(
        success=True,
        message=f"Unpublished workflow **{gid}**.",
        data={"graph_id": gid},
        output_preview=f"Unpublished {gid}",
    )


async def handle_export_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    _, _, graph = _resolve_graph(ctx, args.get("graph_id"))
    if graph is None:
        gid = (args.get("graph_id") or ctx.workflow_id or "").strip()
        return CapabilityResult(
            success=False,
            message=f"Graph not found: {gid or '(no workflow_id)'}",
        )
    fmt = (args.get("format") or "block").lower()
    name = args.get("name") or ""
    version = args.get("version") or "0.1.0"
    node_id = args.get("node_id")
    gid = (args.get("graph_id") or ctx.workflow_id or "").strip()

    if fmt == "block":
        from dan.blocks import export_workflow_block, export_composite_block
        output_dir = Path(tempfile.mkdtemp(prefix="dan-export-"))
        try:
            if node_id:
                block_dir = export_composite_block(graph, node_id, output_dir, name=name, version=version)
            else:
                block_dir = export_workflow_block(graph, output_dir, name=name, version=version)
            _schedule_export_cleanup(str(output_dir))
            return CapabilityResult(
                success=True,
                message=f"Exported as block to **{block_dir}**.",
                data={"path": str(block_dir), "format": "block"},
                output_preview=f"Block at {block_dir}",
            )
        except Exception as exc:
            shutil.rmtree(str(output_dir), ignore_errors=True)
            return CapabilityResult(success=False, message=f"Block export failed: {exc}")

    if fmt == "markdown":
        from dan.loader.decompiler import decompile_to_markdown
        output_dir = Path(tempfile.mkdtemp(prefix="dan-export-md-"))
        try:
            result = decompile_to_markdown(graph, output_dir)
            paths = [str(p) for p in result.files]
            _schedule_export_cleanup(str(output_dir))
            return CapabilityResult(
                success=True,
                message=f"Exported to markdown: {', '.join(paths)}",
                data={"paths": paths, "format": "markdown"},
                output_preview=f"Markdown at {paths[0] if paths else output_dir}",
            )
        except Exception as exc:
            shutil.rmtree(str(output_dir), ignore_errors=True)
            return CapabilityResult(success=False, message=f"Markdown export failed: {exc}")

    if fmt == "python":
        from dan.builder.decompiler import decompile as decompile_to_python
        output_dir = Path(tempfile.mkdtemp(prefix="dan-export-py-"))
        try:
            code = decompile_to_python(graph)
            out_file = output_dir / "workflow.py"
            out_file.write_text(code, encoding="utf-8")
            _schedule_export_cleanup(str(output_dir))
            return CapabilityResult(
                success=True,
                message=f"Exported to Python: **{out_file}**",
                data={"path": str(out_file), "format": "python"},
                output_preview=f"Python at {out_file}",
            )
        except Exception as exc:
            shutil.rmtree(str(output_dir), ignore_errors=True)
            return CapabilityResult(success=False, message=f"Python export failed: {exc}")

    return CapabilityResult(success=False, message=f"Unknown format: {fmt}")


async def handle_share_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    gid, _, graph = _resolve_graph(ctx, args.get("graph_id"))
    if graph is None:
        return CapabilityResult(
            success=False,
            message=f"Graph not found: {gid or '(no workflow_id)'}",
        )
    fmt = (args.get("format") or "mcp_config").lower()
    base_url = args.get("base_url") or "http://localhost:8001"
    graphs_dir = ctx.graphs_dir or "./graphs"
    workflow_path = str(Path(graphs_dir) / f"{gid}.json")

    from dan.publish.portal import generate_mcp_config, generate_api_docs, generate_openapi_spec
    from dan.utils.workflow_interface import derive_workflow_interface

    iface = derive_workflow_interface(graph)
    interfaces = [iface]

    if fmt == "mcp_config":
        config = generate_mcp_config(workflow_path, name=iface.name, interfaces=interfaces)
        return CapabilityResult(
            success=True,
            message="MCP client config (paste into .cursor/mcp.json or claude_desktop_config.json):\n```json\n" + json.dumps(config, indent=2) + "\n```",
            data=config,
            output_preview="MCP config generated",
        )
    if fmt == "api_docs":
        docs = generate_api_docs(interfaces, base_url=base_url)
        return CapabilityResult(
            success=True,
            message=f"API documentation:\n\n{docs}",
            data={"markdown": docs},
            output_preview=_truncate(docs),
        )
    if fmt == "openapi":
        spec = generate_openapi_spec(interfaces)
        return CapabilityResult(
            success=True,
            message="OpenAPI spec:\n```json\n" + json.dumps(spec, indent=2) + "\n```",
            data=spec,
            output_preview="OpenAPI spec generated",
        )
    return CapabilityResult(success=False, message=f"Unknown format: {fmt}")


async def handle_list_published(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.publish_registry is None:
        return CapabilityResult(success=False, message="Publish registry not available.")
    items = ctx.publish_registry.list_all()
    if not items:
        return CapabilityResult(
            success=True,
            message="No published workflows.",
            data=[],
            output_preview="No published workflows.",
        )
    lines = []
    for w in items:
        human = " (has HumanNode)" if w.has_human_nodes else ""
        lines.append(f"- **{w.workflow_id}** — {w.name}: {_truncate(w.description or '', 80)}{human}")
    text = f"Published workflows ({len(items)}):\n" + "\n".join(lines)
    return CapabilityResult(
        success=True,
        message=text,
        data=[{"workflow_id": w.workflow_id, "name": w.name, "description": w.description, "has_human_nodes": w.has_human_nodes} for w in items],
        output_preview=_truncate(text),
    )


async def handle_get_publish_status(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.publish_registry is None:
        return CapabilityResult(success=False, message="Publish registry not available.")
    gid, _, graph = _resolve_graph(ctx, args.get("graph_id"))
    if graph is None:
        return CapabilityResult(
            success=False,
            message=f"Graph not found: {gid or '(no workflow_id)'}",
        )
    from dan.publish.schema import slugify as _slugify
    from dan.utils.workflow_interface import derive_workflow_interface
    try:
        iface = derive_workflow_interface(graph)
        slug = _slugify(iface.name)
    except Exception:
        slug = gid
    published = ctx.publish_registry.is_published(slug)
    result = {"graph_id": gid, "published": published, "workflow_id": slug if published else None}
    graphs_dir = ctx.graphs_dir or "./graphs"
    pub_path = Path(graphs_dir) / f"{gid}.publish.json"
    if pub_path.exists():
        try:
            result["config"] = json.loads(pub_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    text = f"**{gid}**: {'published' if published else 'not published'}" + (f" (slug: {slug})" if published else "")
    return CapabilityResult(
        success=True,
        message=text,
        data=result,
        output_preview=text,
    )


async def handle_import_block(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.block_registry is None:
        return CapabilityResult(success=False, message="Block registry not available.")
    source = str(args.get("source", "")).strip()
    if not source:
        return CapabilityResult(success=False, message="source is required")
    scope = args.get("scope") or "user"
    workspace_str = args.get("workspace")
    workspace = Path(workspace_str) if workspace_str else None
    if scope == "workspace" and workspace is None:
        workspace = Path(os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd()))
    force = bool(args.get("force", False))
    try:
        from dan.blocks import import_block as _import_block
        installed = _import_block(source, scope=scope, workspace=workspace, force=force)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Import failed: {exc}")
    ctx.block_registry.scan()
    return CapabilityResult(
        success=True,
        message=f"Imported block **{installed.name}@{installed.version}** to {installed.install_path}.",
        data={"name": installed.name, "version": installed.version, "block_type": installed.block_type, "install_path": str(installed.install_path)},
        output_preview=f"{installed.name}@{installed.version}",
    )


async def handle_list_blocks(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.block_registry is None:
        return CapabilityResult(success=False, message="Block registry not available.")
    ctx.block_registry.scan()
    blocks = ctx.block_registry.list_blocks()
    if not blocks:
        return CapabilityResult(
            success=True,
            message="No blocks installed.",
            data=[],
            output_preview="No blocks installed.",
        )
    lines = []
    for b in blocks:
        desc = (b.metadata.description or "")[:60] if b.metadata else ""
        lines.append(f"- **{b.name}@{b.version}** ({b.block_type}): {desc}")
    text = f"Installed blocks ({len(blocks)}):\n" + "\n".join(lines)
    return CapabilityResult(
        success=True,
        message=text,
        data=[{"name": b.name, "version": b.version, "block_type": b.block_type, "description": (b.metadata.description or "") if b.metadata else ""} for b in blocks],
        output_preview=_truncate(text),
    )


def register_publish_capabilities(registry: ChatCapabilityRegistry) -> None:
    """Register publish/share/export tools (25-3)."""
    registry.register(
        "publish_workflow",
        PUBLISH_WORKFLOW_SCHEMA,
        handle_publish_workflow,
        modes=WRITE_MODES,
        category="publish",
    )
    registry.register(
        "unpublish_workflow",
        UNPUBLISH_WORKFLOW_SCHEMA,
        handle_unpublish_workflow,
        modes=WRITE_MODES,
        category="publish",
    )
    registry.register(
        "export_workflow",
        EXPORT_WORKFLOW_SCHEMA,
        handle_export_workflow,
        modes=WRITE_MODES,
        category="publish",
    )
    registry.register(
        "share_workflow",
        SHARE_WORKFLOW_SCHEMA,
        handle_share_workflow,
        modes=list(ALL_MODES),
        category="publish",
    )
    registry.register(
        "list_published",
        LIST_PUBLISHED_SCHEMA,
        handle_list_published,
        modes=list(ALL_MODES),
        category="publish",
    )
    registry.register(
        "get_publish_status",
        GET_PUBLISH_STATUS_SCHEMA,
        handle_get_publish_status,
        modes=list(ALL_MODES),
        category="publish",
    )
    registry.register(
        "import_block",
        IMPORT_BLOCK_SCHEMA,
        handle_import_block,
        modes=WRITE_MODES,
        category="blocks",
    )
    registry.register(
        "list_blocks",
        LIST_BLOCKS_SCHEMA,
        handle_list_blocks,
        modes=list(ALL_MODES),
        category="blocks",
    )


# ── Run lifecycle tools (25-4) ──────────────────────────────────────

RUN_WRITE_MODES = ["agent", "build", "mutate", "debug"]


def _find_persisted_run(run_store: Any, run_id: str) -> dict[str, Any] | None:
    """Look up a single run by scanning workflow directories for its summary file."""
    base = getattr(run_store, "_base", None)
    if base is None:
        return None
    from pathlib import Path
    base = Path(base)
    if not base.exists():
        return None
    for wf_dir in base.iterdir():
        if not wf_dir.is_dir():
            continue
        summary = run_store.load_summary(wf_dir.name, run_id)
        if summary is not None:
            return summary
    return None


def get_pending_run_disambiguation(run_manager: Any) -> str | None:
    """Return a disambiguation message when multiple runs have pending HumanNode inputs."""
    pending = run_manager.get_all_pending_human_inputs()
    if len(pending) <= 1:
        return None
    lines = ["Multiple runs have pending input. Please specify a run_id:"]
    seen_runs = set()
    for p in pending:
        data = p.get("data", {})
        run_id = data.get("run_id") or p.get("run_id", "?")
        if run_id in seen_runs:
            continue
        seen_runs.add(run_id)
        node_id = data.get("node_id", p.get("node_id", "?"))
        prompt_preview = (data.get("prompt", "") or "")[:80]
        lines.append(f"  - **{run_id}** (node: {node_id}) — {prompt_preview}")
    return "\n".join(lines)


def resolve_run_reference(
    ref: str,
    run_manager: Any,
    run_store: Any | None = None,
) -> str | None:
    """Map 'latest', 'last_failed', 'paused' to concrete run_id."""
    if ref in ("latest", "last_failed", "paused"):
        runs = run_manager.list_runs()
        if ref == "latest":
            if not runs:
                if run_store:
                    summaries = run_store.list_summaries(limit=1)
                    return summaries[0]["run_id"] if summaries else None
                return None
            sorted_runs = sorted(runs, key=lambda r: r.get("started_at", 0), reverse=True)
            return sorted_runs[0]["run_id"]
        elif ref == "last_failed":
            failed = [r for r in runs if r.get("status") == "failed"]
            if failed:
                failed.sort(key=lambda r: r.get("started_at", 0), reverse=True)
                return failed[0]["run_id"]
            if run_store:
                summaries = run_store.list_summaries(status="failed", limit=1)
                return summaries[0]["run_id"] if summaries else None
            return None
        elif ref == "paused":
            pending = run_manager.get_all_pending_human_inputs()
            if len(pending) == 1:
                return (pending[0].get("data") or {}).get("run_id") or pending[0].get("run_id")
            # 0 or 2+ → return None (ambiguous or none)
            return None
    return ref


# Run lifecycle schemas
START_RUN_SCHEMA = build_tool_schema(
    name="start_run",
    description=(
        "Start a workflow run. Use when the user says 'run it', 'execute', or 'start the workflow'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string", "description": "Workflow/graph ID to run (default: current chat workflow)"},
            "inputs": {"type": "object", "description": "Optional input values for the workflow"},
            "run_id": {"type": "string", "description": "Optional custom run ID"},
            "session_id": {"type": "string", "description": "Optional session ID"},
        },
    },
)

GET_RUN_STATUS_SCHEMA = build_tool_schema(
    name="get_run_status",
    description=(
        "Get status of a run. Supports run_id or 'latest', 'last_failed', 'paused'. "
        "Use when the user asks 'status of the run', 'how did it go?', or 'what's running?'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID or 'latest'|'last_failed'|'paused'"},
        },
        "required": ["run_id"],
    },
)

LIST_ACTIVE_RUNS_SCHEMA = build_tool_schema(
    name="list_active_runs",
    description=(
        "List active and recent runs. Use when the user asks 'what's running?', "
        "'show active runs', or 'list runs'."
    ),
    parameters={"type": "object", "properties": {}},
)

CANCEL_RUN_SCHEMA = build_tool_schema(
    name="cancel_run",
    description="Cancel a running workflow. Use when the user says 'cancel run X' or 'stop the run'.",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID to cancel"},
        },
        "required": ["run_id"],
    },
)

RESUME_RUN_SCHEMA = build_tool_schema(
    name="resume_run",
    description="Resume a checkpointed run. Use when the user says 'resume run X' or 'continue the run'.",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID to resume"},
            "workflow_id": {"type": "string", "description": "Workflow/graph ID"},
            "session_id": {"type": "string", "description": "Optional session ID"},
        },
        "required": ["run_id", "workflow_id"],
    },
)

GET_RUN_LOGS_SCHEMA = build_tool_schema(
    name="get_run_logs",
    description="Get event logs for a run. Use when the user asks 'show logs', 'what happened?', or 'run output'.",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID or 'latest'|'last_failed'|'paused'"},
            "limit": {"type": "integer", "description": "Max events to return", "default": 50},
            "node_id": {"type": "string", "description": "Filter by node ID (optional)"},
        },
        "required": ["run_id"],
    },
)

GET_RUN_CHECKPOINTS_SCHEMA = build_tool_schema(
    name="get_run_checkpoints",
    description="Get checkpoint info for a run (completed nodes, staleness). Use for partial reruns.",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID or 'latest'|'last_failed'|'paused'"},
        },
        "required": ["run_id"],
    },
)

RERUN_FROM_CHECKPOINT_SCHEMA = build_tool_schema(
    name="rerun_from_checkpoint",
    description="Partial rerun from a checkpoint. Use when the user says 'rerun from node X' or 'retry downstream'.",
    parameters={
        "type": "object",
        "properties": {
            "source_run_id": {"type": "string", "description": "Run ID with checkpoint"},
            "workflow_id": {"type": "string", "description": "Workflow/graph ID"},
            "scope_type": {
                "type": "string",
                "enum": ["downstream_of", "single_node", "subgraph"],
                "description": "Rerun scope type",
            },
            "target_node_id": {"type": "string", "description": "Target node for downstream_of or single_node"},
            "sub_graph_key": {"type": "string", "description": "Sub-graph key for subgraph scope"},
            "session_id": {"type": "string", "description": "Optional session ID"},
        },
        "required": ["source_run_id", "workflow_id", "scope_type"],
    },
)

SUBMIT_HUMAN_INPUT_SCHEMA = build_tool_schema(
    name="submit_human_input",
    description="Submit response for a pending HumanNode. Use when the user provides input for a paused run.",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID"},
            "request_id": {"type": "string", "description": "Request ID from human_input_needed event"},
            "response": {"type": "object", "description": "User response (e.g. {\"approved\": true} or {\"text\": \"...\"})"},
        },
        "required": ["run_id", "request_id", "response"],
    },
)


async def handle_start_run(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    graph_id = str(args.get("workflow_id", "")).strip() or ctx.workflow_id
    if not graph_id:
        return CapabilityResult(success=False, message="workflow_id is required or set current workflow context.")
    graph_dict = ctx.graph_store.get_graph(graph_id)
    if graph_dict is None:
        return CapabilityResult(success=False, message=f"Workflow '{graph_id}' not found.")
    if not graph_dict.get("nodes"):
        return CapabilityResult(success=False, message="Workflow has no nodes — nothing to run. Try asking me directly instead.")
    from dan.models.graph import Graph
    try:
        graph = Graph.model_validate(graph_dict)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Invalid graph: {exc}")
    inputs = args.get("inputs")
    run_id = args.get("run_id")
    session_id = args.get("session_id")
    try:
        record = await ctx.run_manager.start_run(
            graph, graph_id=graph_id, inputs=inputs, run_id=run_id, session_id=session_id
        )
        if ctx.event_bus is not None:
            from dan.server.run_relay import relay_run_events_to_bus
            import asyncio
            asyncio.create_task(
                relay_run_events_to_bus(
                    rm=ctx.run_manager,
                    run_id=record.run_id,
                    workflow_name=graph_id,
                    surface_id=None,
                    bus=ctx.event_bus,
                )
            )
    except Exception as exc:
        logger.exception("start_run failed")
        return CapabilityResult(success=False, message=f"Start run failed: {exc}")
    return CapabilityResult(
        success=True,
        message=f"Run started: {record.run_id}",
        data={"run_id": record.run_id, "status": record.status.value},
        output_preview=f"Run {record.run_id} started.",
        stream_channel_id=f"run-{record.run_id}",
    )


async def handle_get_run_status(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    ref = str(args.get("run_id", "")).strip()
    if not ref:
        return CapabilityResult(success=False, message="run_id is required.")
    run_id = resolve_run_reference(ref, ctx.run_manager, ctx.run_store)
    if run_id is None:
        if ref == "paused":
            disambig = get_pending_run_disambiguation(ctx.run_manager)
            if disambig:
                return CapabilityResult(success=False, message=disambig)
        return CapabilityResult(success=False, message=f"Could not resolve run reference '{ref}'.")
    record = ctx.run_manager.get_run(run_id)
    if record is None and ctx.run_store:
        snap = _find_persisted_run(ctx.run_store, run_id)
        if snap is None:
            return CapabilityResult(success=False, message=f"Run '{run_id}' not found.")
    elif record is None:
        return CapabilityResult(success=False, message=f"Run '{run_id}' not found.")
    else:
        snap = record.snapshot()
    parts = [
        f"**{snap.get('run_id', '?')}** ({snap.get('graph_id', '?')})",
        f"Status: {snap.get('status', '?')}",
    ]
    node_statuses = snap.get("node_statuses", {})
    if node_statuses:
        done = sum(1 for s in node_statuses.values() if s in ("completed", "failed", "skipped"))
        parts.append(f"Nodes: {done}/{len(node_statuses)}")
    if snap.get("elapsed_seconds") is not None:
        parts.append(f"Elapsed: {snap['elapsed_seconds']:.1f}s")
    if snap.get("total_tokens"):
        parts.append(f"Tokens: {snap['total_tokens']}")
    if snap.get("total_cost") is not None:
        parts.append(f"Cost: ${snap['total_cost']:.4f}")
    if snap.get("error"):
        parts.append(f"Error: {_truncate(snap['error'], 200)}")
    text = " | ".join(parts)
    return CapabilityResult(
        success=True,
        message=text,
        data=snap,
        output_preview=_truncate(text),
    )


async def handle_list_active_runs(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.activity_tracker is None:
        return CapabilityResult(
            success=False,
            message="Activity tracker not available.",
        )
    snapshot = ctx.activity_tracker.get_activity()
    data = snapshot.model_dump() if hasattr(snapshot, "model_dump") else (snapshot if isinstance(snapshot, dict) else {})
    active = data.get("active", [])
    recent = data.get("recent", [])
    surfaces = data.get("connected_surfaces", [])
    parts = []
    if active:
        parts.append(f"**Active runs ({len(active)}):**")
        for r in active[:10]:
            rid = r.get("run_id", "?")
            gid = r.get("graph_id", "?")
            status = r.get("status", "?")
            parts.append(f"  - {rid} ({gid}) — {status}")
    else:
        parts.append("No active runs.")
    if recent:
        parts.append(f"\n**Recent ({len(recent)}):**")
        for r in recent[:5]:
            rid = r.get("run_id", "?")
            gid = r.get("graph_id", "?")
            status = r.get("status", "?")
            parts.append(f"  - {rid} ({gid}) — {status}")
    if surfaces:
        parts.append(f"\n**Connected surfaces:** {len(surfaces)}")
    text = "\n".join(parts)
    return CapabilityResult(
        success=True,
        message=text,
        data=data,
        output_preview=_truncate(text),
    )


async def handle_cancel_run(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    run_id = str(args.get("run_id", "")).strip()
    if not run_id:
        return CapabilityResult(success=False, message="run_id is required.")
    ref = run_id
    run_id = resolve_run_reference(run_id, ctx.run_manager, ctx.run_store)
    if run_id is None:
        if ref == "paused":
            disambig = get_pending_run_disambiguation(ctx.run_manager)
            if disambig:
                return CapabilityResult(success=False, message=disambig)
        return CapabilityResult(success=False, message="Could not resolve run reference.")
    ok = ctx.run_manager.cancel_run(run_id)
    return CapabilityResult(
        success=True,
        message=f"Run {run_id} {'cancelled' if ok else 'not found or already finished'}.",
        data={"run_id": run_id, "cancelled": ok},
        output_preview=f"Cancelled {run_id}" if ok else f"Run {run_id} not found.",
    )


async def handle_resume_run(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    run_id = str(args.get("run_id", "")).strip()
    workflow_id = str(args.get("workflow_id", "")).strip()
    if not run_id or not workflow_id:
        return CapabilityResult(success=False, message="run_id and workflow_id are required.")
    graph = ctx.graph_store.load_as_model(workflow_id)
    if graph is None:
        return CapabilityResult(success=False, message=f"Workflow '{workflow_id}' not found.")
    session_id = args.get("session_id")
    try:
        record = await ctx.run_manager.resume_run(
            graph, graph_id=workflow_id, run_id=run_id, session_id=session_id
        )
    except Exception as exc:
        logger.exception("resume_run failed")
        return CapabilityResult(success=False, message=f"Resume failed: {exc}")
    return CapabilityResult(
        success=True,
        message=f"Run resumed: {record.run_id}",
        data={"run_id": record.run_id, "status": record.status.value},
        output_preview=f"Run {record.run_id} resumed.",
    )


async def handle_get_run_logs(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    ref = str(args.get("run_id", "")).strip()
    if not ref:
        return CapabilityResult(success=False, message="run_id is required.")
    run_id = resolve_run_reference(ref, ctx.run_manager, ctx.run_store)
    if run_id is None:
        if ref == "paused":
            disambig = get_pending_run_disambiguation(ctx.run_manager)
            if disambig:
                return CapabilityResult(success=False, message=disambig)
        return CapabilityResult(success=False, message=f"Could not resolve run reference '{ref}'.")
    limit = max(1, min(int(args.get("limit", 50)), 500))
    node_id = args.get("node_id")
    if node_id is not None:
        node_id = str(node_id).strip() or None
    record = ctx.run_manager.get_run(run_id)
    workflow_id = None
    events = []
    if record is not None:
        workflow_id = record.graph_id
        events = list(record.events) if hasattr(record, "events") else []
    if workflow_id is None and ctx.run_store:
        persisted = _find_persisted_run(ctx.run_store, run_id)
        if persisted is not None:
            workflow_id = persisted.get("graph_id")
    if workflow_id and ctx.run_store:
        loaded = ctx.run_store.load_events(workflow_id, run_id, node_id=node_id)
        if loaded:
            events = loaded
    if node_id:
        events = [e for e in events if e.get("node_id") == node_id]
    events = events[-limit:]
    lines = []
    for e in events:
        etype = e.get("event_type", "?")
        nid = e.get("node_id", "")
        ts = e.get("timestamp", 0)
        data = e.get("data") or {}
        line = f"[{ts:.0f}] {etype}"
        if nid:
            line += f" node={nid}"
        if data:
            line += f" {_truncate(str(data), 80)}"
        lines.append(line)
    text = "\n".join(lines) if lines else "No events."
    return CapabilityResult(
        success=True,
        message=text,
        data={"events": events},
        output_preview=_truncate(text),
    )


async def handle_get_run_checkpoints(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    ref = str(args.get("run_id", "")).strip()
    if not ref:
        return CapabilityResult(success=False, message="run_id is required.")
    run_id = resolve_run_reference(ref, ctx.run_manager, ctx.run_store)
    if run_id is None:
        if ref == "paused":
            disambig = get_pending_run_disambiguation(ctx.run_manager)
            if disambig:
                return CapabilityResult(success=False, message=disambig)
        return CapabilityResult(success=False, message=f"Could not resolve run reference '{ref}'.")
    try:
        info = await ctx.run_manager.get_checkpoint_info(run_id)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Checkpoint info failed: {exc}")
    if info is None:
        return CapabilityResult(
            success=True,
            message="No checkpoint found for this run.",
            data=None,
            output_preview="No checkpoint.",
        )
    parts = [
        f"Run: {info.get('run_id', '?')}",
        f"Completed nodes: {info.get('completed_node_ids', [])}",
        f"Node output keys: {info.get('node_output_keys', [])}",
    ]
    if info.get("graph_revision"):
        parts.append(f"Graph revision: {info['graph_revision']}")
    text = "\n".join(parts)
    return CapabilityResult(
        success=True,
        message=text,
        data=info,
        output_preview=_truncate(text),
    )


async def handle_rerun_from_checkpoint(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    source_run_id = str(args.get("source_run_id", "")).strip()
    workflow_id = str(args.get("workflow_id", "")).strip()
    scope_type = str(args.get("scope_type", "")).strip()
    if not source_run_id or not workflow_id or not scope_type:
        return CapabilityResult(success=False, message="source_run_id, workflow_id, and scope_type are required.")
    from dan.engine.checkpoint import RerunScope
    scope = RerunScope(
        scope_type=scope_type,
        target_node_id=args.get("target_node_id") or None,
        sub_graph_key=args.get("sub_graph_key") or None,
    )
    graph = ctx.graph_store.load_as_model(workflow_id)
    if graph is None:
        return CapabilityResult(success=False, message=f"Workflow '{workflow_id}' not found.")
    session_id = args.get("session_id")
    try:
        record = await ctx.run_manager.rerun_from_checkpoint(
            graph, graph_id=workflow_id, source_run_id=source_run_id, scope=scope, session_id=session_id
        )
    except ValueError as exc:
        return CapabilityResult(success=False, message=f"Invalid scope or checkpoint: {exc}")
    except RuntimeError as exc:
        return CapabilityResult(success=False, message=f"Stale checkpoint: {exc}")
    except Exception as exc:
        logger.exception("rerun_from_checkpoint failed")
        return CapabilityResult(success=False, message=f"Rerun failed: {exc}")
    return CapabilityResult(
        success=True,
        message=f"Rerun started: {record.run_id}",
        data={"run_id": record.run_id, "status": record.status.value, "source_run_id": source_run_id},
        output_preview=f"Rerun {record.run_id} started from checkpoint.",
    )


async def handle_submit_human_input(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    run_id = str(args.get("run_id", "")).strip()
    request_id = str(args.get("request_id", "")).strip()
    response = args.get("response")
    if not run_id or not request_id:
        return CapabilityResult(success=False, message="run_id and request_id are required.")
    if response is None or not isinstance(response, dict):
        return CapabilityResult(success=False, message="response must be a dict.")
    ok = ctx.run_manager.submit_human_input(run_id, request_id, response)
    return CapabilityResult(
        success=True,
        message=f"Input {'submitted' if ok else 'rejected (already resolved or wrong run)'}.",
        data={"submitted": ok},
        output_preview="Submitted." if ok else "Rejected.",
    )


def register_run_lifecycle_capabilities(registry: ChatCapabilityRegistry) -> None:
    """Register run lifecycle tools (25-4)."""
    registry.register(
        "start_run",
        START_RUN_SCHEMA,
        handle_start_run,
        modes=RUN_WRITE_MODES,
        category="run",
    )
    registry.register(
        "get_run_status",
        GET_RUN_STATUS_SCHEMA,
        handle_get_run_status,
        modes=list(ALL_MODES),
        category="run",
    )
    registry.register(
        "list_active_runs",
        LIST_ACTIVE_RUNS_SCHEMA,
        handle_list_active_runs,
        modes=list(ALL_MODES),
        category="run",
    )
    registry.register(
        "cancel_run",
        CANCEL_RUN_SCHEMA,
        handle_cancel_run,
        modes=RUN_WRITE_MODES,
        category="run",
    )
    registry.register(
        "resume_run",
        RESUME_RUN_SCHEMA,
        handle_resume_run,
        modes=RUN_WRITE_MODES,
        category="run",
    )
    registry.register(
        "get_run_logs",
        GET_RUN_LOGS_SCHEMA,
        handle_get_run_logs,
        modes=list(ALL_MODES),
        category="run",
    )
    registry.register(
        "get_run_checkpoints",
        GET_RUN_CHECKPOINTS_SCHEMA,
        handle_get_run_checkpoints,
        modes=list(ALL_MODES),
        category="run",
    )
    registry.register(
        "rerun_from_checkpoint",
        RERUN_FROM_CHECKPOINT_SCHEMA,
        handle_rerun_from_checkpoint,
        modes=RUN_WRITE_MODES,
        category="run",
    )
    registry.register(
        "submit_human_input",
        SUBMIT_HUMAN_INPUT_SCHEMA,
        handle_submit_human_input,
        modes=RUN_WRITE_MODES,
        category="run",
    )


# ── Workflow catalog tools (29-4) ────────────────────────────────────

LIST_MY_WORKFLOWS_SCHEMA = build_tool_schema(
    name="list_my_workflows",
    description="List all saved workflows with names, descriptions, and sizes.",
    parameters={"type": "object", "properties": {}, "required": []},
)

SEARCH_WORKFLOWS_SCHEMA = build_tool_schema(
    name="search_workflows",
    description="Search saved workflows by keyword.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search keywords"},
        },
        "required": ["query"],
    },
)

SHOW_WORKFLOW_SCHEMA = build_tool_schema(
    name="show_workflow",
    description="Show the structure of a specific workflow as an ASCII diagram.",
    parameters={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string", "description": "The workflow ID to display"},
        },
        "required": ["workflow_id"],
    },
)


async def handle_list_my_workflows(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    graphs = ctx.graph_store.list_graphs()
    if not graphs:
        return CapabilityResult(
            success=True,
            message="No saved workflows yet. Use the build tools to create one!",
        )
    lines: list[str] = []
    for i, g in enumerate(graphs, 1):
        name = g.get("name", g.get("graph_id", "?"))
        desc = g.get("description", "")
        gid = g.get("graph_id", "?")
        data = ctx.graph_store.get_graph(gid)
        node_count = len(data.get("nodes", [])) if data else 0
        edge_count = len(data.get("edges", [])) if data else 0
        desc_part = f" — {_truncate(desc, 120)}" if desc else ""
        lines.append(f"{i}. **{name}** (id: `{gid}`, {node_count} nodes, {edge_count} edges){desc_part}")
    text = f"Found {len(graphs)} workflow(s):\n" + "\n".join(lines)
    return CapabilityResult(success=True, message=text, output_preview=_truncate(text))


async def handle_search_workflows(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    query = str(args.get("query", "")).strip()
    if not query:
        return CapabilityResult(success=False, message="query is required.")
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    graphs = ctx.graph_store.list_graphs()
    if not graphs:
        return CapabilityResult(success=True, message="No saved workflows to search.")
    q_lower = query.lower()
    matches: list[dict[str, Any]] = []
    for g in graphs:
        name = g.get("name", "")
        desc = g.get("description", "")
        searchable = f"{name} {desc} {g.get('graph_id', '')}".lower()
        if q_lower in searchable:
            matches.append(g)
    if not matches:
        return CapabilityResult(
            success=True,
            message=f"No workflows matched '{query}'.",
        )
    lines: list[str] = []
    for i, g in enumerate(matches, 1):
        name = g.get("name", g.get("graph_id", "?"))
        desc = g.get("description", "")
        gid = g.get("graph_id", "?")
        desc_part = f" — {_truncate(desc, 120)}" if desc else ""
        lines.append(f"{i}. **{name}** (id: `{gid}`){desc_part}")
    text = f"Found {len(matches)} workflow(s) matching '{query}':\n" + "\n".join(lines)
    return CapabilityResult(success=True, message=text, output_preview=_truncate(text))


async def handle_show_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    workflow_id = str(args.get("workflow_id", "")).strip()
    if not workflow_id:
        return CapabilityResult(success=False, message="workflow_id is required.")
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    data = ctx.graph_store.get_graph(workflow_id)
    if data is None:
        return CapabilityResult(
            success=False,
            message=f"Workflow '{workflow_id}' not found.",
        )
    meta = data.get("metadata", {})
    name = meta.get("name", workflow_id)
    desc = meta.get("description", "")
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    parts = [f"**{name}** (id: `{workflow_id}`)"]
    if desc:
        parts.append(f"Description: {desc}")
    parts.append(f"Nodes: {len(nodes)} | Edges: {len(edges)}")

    try:
        from dan.cli.dag_display import render_dag
        dag = render_dag(data)
        parts.append(f"\nStructure:\n{dag}")
    except Exception:
        if nodes:
            node_names = [n.get("name") or n.get("id", "?") for n in nodes[:20]]
            parts.append("Nodes: " + ", ".join(node_names))

    text = "\n".join(parts)
    return CapabilityResult(success=True, message=text, data=data, output_preview=_truncate(text))


FORK_WORKFLOW_SCHEMA = build_tool_schema(
    name="fork_workflow",
    description="Duplicate an existing workflow with a new name as a starting point for adaptation.",
    parameters={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string", "description": "ID of the workflow to fork"},
            "new_name": {"type": "string", "description": "Name for the forked copy"},
        },
        "required": ["workflow_id"],
    },
)


async def handle_fork_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    graph_store = ctx.graph_store
    if graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")

    workflow_id = args.get("workflow_id", "").strip()
    if not workflow_id:
        return CapabilityResult(success=False, message="workflow_id is required.")

    graph_dict = graph_store.get_graph(workflow_id)
    if graph_dict is None:
        return CapabilityResult(success=False, message=f"Workflow '{workflow_id}' not found.")

    new_name = args.get("new_name", "").strip() or f"{workflow_id}_fork"

    import uuid as _uuid

    new_id = _uuid.uuid4().hex[:12]
    forked = dict(graph_dict)
    if "metadata" in forked:
        forked["metadata"] = dict(forked["metadata"])
        forked["metadata"]["name"] = new_name
        forked["metadata"]["forked_from"] = workflow_id
    else:
        forked["metadata"] = {"name": new_name, "forked_from": workflow_id}

    graph_store.save_graph(new_id, forked)

    msg = f"Forked '{workflow_id}' as '{new_name}' (ID: {new_id})."
    return CapabilityResult(
        success=True,
        message=msg,
        data={"workflow_id": new_id, "name": new_name, "forked_from": workflow_id},
    )


def register_workflow_catalog_capabilities(registry: ChatCapabilityRegistry) -> None:
    """Register workflow catalog browsing tools (29-4)."""
    registry.register(
        "list_my_workflows",
        LIST_MY_WORKFLOWS_SCHEMA,
        handle_list_my_workflows,
        modes=list(ALL_MODES),
        category="catalog",
    )
    registry.register(
        "search_workflows",
        SEARCH_WORKFLOWS_SCHEMA,
        handle_search_workflows,
        modes=list(ALL_MODES),
        category="catalog",
    )
    registry.register(
        "show_workflow",
        SHOW_WORKFLOW_SCHEMA,
        handle_show_workflow,
        modes=list(ALL_MODES),
        category="catalog",
    )
    registry.register(
        "fork_workflow",
        FORK_WORKFLOW_SCHEMA,
        handle_fork_workflow,
        modes=list(ALL_MODES),
        category="catalog",
    )


# ── Registry setup ─────────────────────────────────────────────────

def register_base_capabilities(registry: ChatCapabilityRegistry) -> None:
    """Register the foundational read-only tools (25-1)."""
    registry.register(
        "list_graphs",
        LIST_GRAPHS_SCHEMA,
        handle_list_graphs,
        modes=list(ALL_MODES),
        category="graph",
    )
    registry.register(
        "get_activity",
        GET_ACTIVITY_SCHEMA,
        handle_get_activity,
        modes=list(ALL_MODES),
        category="run",
    )
    registry.register(
        "web_search",
        WEB_SEARCH_CAPABILITY_SCHEMA,
        handle_web_search,
        modes=["agent", "build", "mutate", "conversation", "debug"],
        category="web",
    )
    registry.register(
        "file_read",
        FILE_READ_CAPABILITY_SCHEMA,
        handle_file_read,
        modes=list(ALL_MODES),
        category="file",
    )
    registry.register(
        "pdf_read",
        PDF_READ_CAPABILITY_SCHEMA,
        handle_pdf_read,
        modes=list(ALL_MODES),
        category="file",
    )
    registry.register(
        "current_datetime",
        CURRENT_DATETIME_CAPABILITY_SCHEMA,
        handle_current_datetime,
        modes=list(ALL_MODES),
        category="system",
    )
    registry.register(
        "send_email",
        SEND_EMAIL_CAPABILITY_SCHEMA,
        handle_send_email,
        modes=WRITE_MODES,
        category="communication",
    )
    registry.register(
        "telegram_poll",
        TELEGRAM_POLL_CAPABILITY_SCHEMA,
        handle_telegram_poll,
        modes=["agent", "conversation"],
        category="communication",
    )
    registry.register(
        "screenshot",
        SCREENSHOT_CAPABILITY_SCHEMA,
        handle_screenshot,
        modes=list(ALL_MODES),
        category="system",
    )
    registry.register(
        "clipboard",
        CLIPBOARD_CAPABILITY_SCHEMA,
        handle_clipboard,
        modes=list(ALL_MODES),
        category="system",
    )
    registry.register(
        "set_config",
        SET_CONFIG_CAPABILITY_SCHEMA,
        handle_set_config,
        modes=WRITE_MODES,
        category="system",
    )
    registry.register(
        "get_config",
        GET_CONFIG_CAPABILITY_SCHEMA,
        handle_get_config,
        modes=list(ALL_MODES),
        category="system",
    )
    registry.register(
        "list_directory",
        LIST_DIRECTORY_CAPABILITY_SCHEMA,
        handle_list_directory,
        modes=list(ALL_MODES),
        category="file",
    )
    registry.register(
        "spreadsheet_read",
        SPREADSHEET_READ_CAPABILITY_SCHEMA,
        handle_spreadsheet_read,
        modes=list(ALL_MODES),
        category="data",
    )
    registry.register(
        "web_fetch",
        WEB_FETCH_CAPABILITY_SCHEMA,
        handle_web_fetch,
        modes=list(ALL_MODES),
        category="web",
    )
    registry.register(
        "file_write",
        FILE_WRITE_CAPABILITY_SCHEMA,
        handle_file_write,
        modes=WRITE_MODES,
        category="file",
    )
    registry.register(
        "shell_command",
        SHELL_COMMAND_CAPABILITY_SCHEMA,
        handle_shell_command,
        modes=WRITE_MODES,
        category="system",
    )
    registry.register(
        "http_request",
        HTTP_REQUEST_CAPABILITY_SCHEMA,
        handle_http_request,
        modes=WRITE_MODES,
        category="web",
    )
    registry.register(
        "text_chunk",
        TEXT_CHUNK_CAPABILITY_SCHEMA,
        handle_text_chunk,
        modes=list(ALL_MODES),
        category="text",
    )
    registry.register(
        "text_translate",
        TEXT_TRANSLATE_CAPABILITY_SCHEMA,
        handle_text_translate,
        modes=list(ALL_MODES),
        category="text",
    )
    registry.register(
        "image_describe",
        IMAGE_DESCRIBE_CAPABILITY_SCHEMA,
        handle_image_describe,
        modes=list(ALL_MODES),
        category="media",
    )
    registry.register(
        "audio_transcribe",
        AUDIO_TRANSCRIBE_CAPABILITY_SCHEMA,
        handle_audio_transcribe,
        modes=list(ALL_MODES),
        category="media",
    )
    registry.register(
        "json_extract",
        JSON_EXTRACT_CAPABILITY_SCHEMA,
        handle_json_extract,
        modes=list(ALL_MODES),
        category="text",
    )
    registry.register(
        "regex_match",
        REGEX_MATCH_CAPABILITY_SCHEMA,
        handle_regex_match,
        modes=list(ALL_MODES),
        category="text",
    )

# ── Extra tools (DAN_FULL_TOOLS) ───────────────────────────────────

import os
from dan.server.variable_inspector import compute_upstream_variables


from dan.tools.python_eval import python_eval as _tool_python_eval
from dan.tools.python_eval import TOOL_METADATA as _META_PYTHON_EVAL

PYTHON_EVAL_CAPABILITY_SCHEMA = build_tool_schema(
    name="python_eval",
    description=_META_PYTHON_EVAL["description"],
    parameters=_META_PYTHON_EVAL["parameters"],
)

async def handle_python_eval(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        # For paths, we might want to resolve them, but let's just pass kwargs for now.
        # Wait, csv_read needs path resolution. Let's do it generically if 'path' in args?
        # Actually, let's just pass args. The tools handle their own paths or we can wrap.
        if "path" in args and "python_eval" in ("csv_read", "file_copy", "file_move", "file_delete", "compress"):
            args["path"] = str(_resolve_user_path(args["path"]))
        if "source" in args and "python_eval" in ("file_copy", "file_move"):
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args and "python_eval" in ("file_copy", "file_move"):
            args["destination"] = str(_resolve_user_path(args["destination"]))
        
        result = await _tool_python_eval(**args)
        return CapabilityResult(
            success=True,
            message="Success",
            data=result,
            output_preview=str(result)[:1000]
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in python_eval: {exc}")


from dan.tools.csv_read import csv_read as _tool_csv_read
from dan.tools.csv_read import TOOL_METADATA as _META_CSV_READ

CSV_READ_CAPABILITY_SCHEMA = build_tool_schema(
    name="csv_read",
    description=_META_CSV_READ["description"],
    parameters=_META_CSV_READ["parameters"],
)

async def handle_csv_read(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        # For paths, we might want to resolve them, but let's just pass kwargs for now.
        # Wait, csv_read needs path resolution. Let's do it generically if 'path' in args?
        # Actually, let's just pass args. The tools handle their own paths or we can wrap.
        if "path" in args and "csv_read" in ("csv_read", "file_copy", "file_move", "file_delete", "compress"):
            args["path"] = str(_resolve_user_path(args["path"]))
        if "source" in args and "csv_read" in ("file_copy", "file_move"):
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args and "csv_read" in ("file_copy", "file_move"):
            args["destination"] = str(_resolve_user_path(args["destination"]))
        
        result = await _tool_csv_read(**args)
        return CapabilityResult(
            success=True,
            message="Success",
            data=result,
            output_preview=str(result)[:1000]
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in csv_read: {exc}")


from dan.tools.compress import compress as _tool_compress
from dan.tools.compress import TOOL_METADATA as _META_COMPRESS

COMPRESS_CAPABILITY_SCHEMA = build_tool_schema(
    name="compress",
    description=_META_COMPRESS["description"],
    parameters=_META_COMPRESS["parameters"],
)

async def handle_compress(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        # For paths, we might want to resolve them, but let's just pass kwargs for now.
        # Wait, csv_read needs path resolution. Let's do it generically if 'path' in args?
        # Actually, let's just pass args. The tools handle their own paths or we can wrap.
        if "path" in args and "compress" in ("csv_read", "file_copy", "file_move", "file_delete", "compress"):
            args["path"] = str(_resolve_user_path(args["path"]))
        if "source" in args and "compress" in ("file_copy", "file_move"):
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args and "compress" in ("file_copy", "file_move"):
            args["destination"] = str(_resolve_user_path(args["destination"]))
        
        result = await _tool_compress(**args)
        return CapabilityResult(
            success=True,
            message="Success",
            data=result,
            output_preview=str(result)[:1000]
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in compress: {exc}")


from dan.tools.file_copy import file_copy as _tool_file_copy
from dan.tools.file_copy import TOOL_METADATA as _META_FILE_COPY

FILE_COPY_CAPABILITY_SCHEMA = build_tool_schema(
    name="file_copy",
    description=_META_FILE_COPY["description"],
    parameters=_META_FILE_COPY["parameters"],
)

async def handle_file_copy(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        # For paths, we might want to resolve them, but let's just pass kwargs for now.
        # Wait, csv_read needs path resolution. Let's do it generically if 'path' in args?
        # Actually, let's just pass args. The tools handle their own paths or we can wrap.
        if "path" in args and "file_copy" in ("csv_read", "file_copy", "file_move", "file_delete", "compress"):
            args["path"] = str(_resolve_user_path(args["path"]))
        if "source" in args and "file_copy" in ("file_copy", "file_move"):
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args and "file_copy" in ("file_copy", "file_move"):
            args["destination"] = str(_resolve_user_path(args["destination"]))
        
        result = await _tool_file_copy(**args)
        return CapabilityResult(
            success=True,
            message="Success",
            data=result,
            output_preview=str(result)[:1000]
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in file_copy: {exc}")


from dan.tools.file_move import file_move as _tool_file_move
from dan.tools.file_move import TOOL_METADATA as _META_FILE_MOVE

FILE_MOVE_CAPABILITY_SCHEMA = build_tool_schema(
    name="file_move",
    description=_META_FILE_MOVE["description"],
    parameters=_META_FILE_MOVE["parameters"],
)

async def handle_file_move(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        # For paths, we might want to resolve them, but let's just pass kwargs for now.
        # Wait, csv_read needs path resolution. Let's do it generically if 'path' in args?
        # Actually, let's just pass args. The tools handle their own paths or we can wrap.
        if "path" in args and "file_move" in ("csv_read", "file_copy", "file_move", "file_delete", "compress"):
            args["path"] = str(_resolve_user_path(args["path"]))
        if "source" in args and "file_move" in ("file_copy", "file_move"):
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args and "file_move" in ("file_copy", "file_move"):
            args["destination"] = str(_resolve_user_path(args["destination"]))
        
        result = await _tool_file_move(**args)
        return CapabilityResult(
            success=True,
            message="Success",
            data=result,
            output_preview=str(result)[:1000]
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in file_move: {exc}")


from dan.tools.file_delete import file_delete as _tool_file_delete
from dan.tools.file_delete import TOOL_METADATA as _META_FILE_DELETE

FILE_DELETE_CAPABILITY_SCHEMA = build_tool_schema(
    name="file_delete",
    description=_META_FILE_DELETE["description"],
    parameters=_META_FILE_DELETE["parameters"],
)

async def handle_file_delete(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        # For paths, we might want to resolve them, but let's just pass kwargs for now.
        # Wait, csv_read needs path resolution. Let's do it generically if 'path' in args?
        # Actually, let's just pass args. The tools handle their own paths or we can wrap.
        if "path" in args and "file_delete" in ("csv_read", "file_copy", "file_move", "file_delete", "compress"):
            args["path"] = str(_resolve_user_path(args["path"]))
        if "source" in args and "file_delete" in ("file_copy", "file_move"):
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args and "file_delete" in ("file_copy", "file_move"):
            args["destination"] = str(_resolve_user_path(args["destination"]))
        
        result = await _tool_file_delete(**args)
        return CapabilityResult(
            success=True,
            message="Success",
            data=result,
            output_preview=str(result)[:1000]
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in file_delete: {exc}")


from dan.tools.git_status import git_status as _tool_git_status
from dan.tools.git_status import TOOL_METADATA as _META_GIT_STATUS

GIT_STATUS_CAPABILITY_SCHEMA = build_tool_schema(
    name="git_status",
    description=_META_GIT_STATUS["description"],
    parameters=_META_GIT_STATUS["parameters"],
)

async def handle_git_status(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        # For paths, we might want to resolve them, but let's just pass kwargs for now.
        # Wait, csv_read needs path resolution. Let's do it generically if 'path' in args?
        # Actually, let's just pass args. The tools handle their own paths or we can wrap.
        if "path" in args and "git_status" in ("csv_read", "file_copy", "file_move", "file_delete", "compress"):
            args["path"] = str(_resolve_user_path(args["path"]))
        if "source" in args and "git_status" in ("file_copy", "file_move"):
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args and "git_status" in ("file_copy", "file_move"):
            args["destination"] = str(_resolve_user_path(args["destination"]))
        
        result = await _tool_git_status(**args)
        return CapabilityResult(
            success=True,
            message="Success",
            data=result,
            output_preview=str(result)[:1000]
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in git_status: {exc}")


from dan.tools.git_diff import git_diff as _tool_git_diff
from dan.tools.git_diff import TOOL_METADATA as _META_GIT_DIFF

GIT_DIFF_CAPABILITY_SCHEMA = build_tool_schema(
    name="git_diff",
    description=_META_GIT_DIFF["description"],
    parameters=_META_GIT_DIFF["parameters"],
)

async def handle_git_diff(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        # For paths, we might want to resolve them, but let's just pass kwargs for now.
        # Wait, csv_read needs path resolution. Let's do it generically if 'path' in args?
        # Actually, let's just pass args. The tools handle their own paths or we can wrap.
        if "path" in args and "git_diff" in ("csv_read", "file_copy", "file_move", "file_delete", "compress"):
            args["path"] = str(_resolve_user_path(args["path"]))
        if "source" in args and "git_diff" in ("file_copy", "file_move"):
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args and "git_diff" in ("file_copy", "file_move"):
            args["destination"] = str(_resolve_user_path(args["destination"]))
        
        result = await _tool_git_diff(**args)
        return CapabilityResult(
            success=True,
            message="Success",
            data=result,
            output_preview=str(result)[:1000]
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in git_diff: {exc}")


from dan.tools.git_log import git_log as _tool_git_log
from dan.tools.git_log import TOOL_METADATA as _META_GIT_LOG

GIT_LOG_CAPABILITY_SCHEMA = build_tool_schema(
    name="git_log",
    description=_META_GIT_LOG["description"],
    parameters=_META_GIT_LOG["parameters"],
)

async def handle_git_log(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        # For paths, we might want to resolve them, but let's just pass kwargs for now.
        # Wait, csv_read needs path resolution. Let's do it generically if 'path' in args?
        # Actually, let's just pass args. The tools handle their own paths or we can wrap.
        if "path" in args and "git_log" in ("csv_read", "file_copy", "file_move", "file_delete", "compress"):
            args["path"] = str(_resolve_user_path(args["path"]))
        if "source" in args and "git_log" in ("file_copy", "file_move"):
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args and "git_log" in ("file_copy", "file_move"):
            args["destination"] = str(_resolve_user_path(args["destination"]))
        
        result = await _tool_git_log(**args)
        return CapabilityResult(
            success=True,
            message="Success",
            data=result,
            output_preview=str(result)[:1000]
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in git_log: {exc}")


from dan.tools.git_branch import git_branch as _tool_git_branch
from dan.tools.git_branch import TOOL_METADATA as _META_GIT_BRANCH

GIT_BRANCH_CAPABILITY_SCHEMA = build_tool_schema(
    name="git_branch",
    description=_META_GIT_BRANCH["description"],
    parameters=_META_GIT_BRANCH["parameters"],
)

async def handle_git_branch(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        # For paths, we might want to resolve them, but let's just pass kwargs for now.
        # Wait, csv_read needs path resolution. Let's do it generically if 'path' in args?
        # Actually, let's just pass args. The tools handle their own paths or we can wrap.
        if "path" in args and "git_branch" in ("csv_read", "file_copy", "file_move", "file_delete", "compress"):
            args["path"] = str(_resolve_user_path(args["path"]))
        if "source" in args and "git_branch" in ("file_copy", "file_move"):
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args and "git_branch" in ("file_copy", "file_move"):
            args["destination"] = str(_resolve_user_path(args["destination"]))
        
        result = await _tool_git_branch(**args)
        return CapabilityResult(
            success=True,
            message="Success",
            data=result,
            output_preview=str(result)[:1000]
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in git_branch: {exc}")


from dan.tools.git_commit import git_commit as _tool_git_commit
from dan.tools.git_commit import TOOL_METADATA as _META_GIT_COMMIT

GIT_COMMIT_CAPABILITY_SCHEMA = build_tool_schema(
    name="git_commit",
    description=_META_GIT_COMMIT["description"],
    parameters=_META_GIT_COMMIT["parameters"],
)

async def handle_git_commit(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        # For paths, we might want to resolve them, but let's just pass kwargs for now.
        # Wait, csv_read needs path resolution. Let's do it generically if 'path' in args?
        # Actually, let's just pass args. The tools handle their own paths or we can wrap.
        if "path" in args and "git_commit" in ("csv_read", "file_copy", "file_move", "file_delete", "compress"):
            args["path"] = str(_resolve_user_path(args["path"]))
        if "source" in args and "git_commit" in ("file_copy", "file_move"):
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args and "git_commit" in ("file_copy", "file_move"):
            args["destination"] = str(_resolve_user_path(args["destination"]))
        
        result = await _tool_git_commit(**args)
        return CapabilityResult(
            success=True,
            message="Success",
            data=result,
            output_preview=str(result)[:1000]
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in git_commit: {exc}")


from dan.tools.git_worktree import git_worktree as _tool_git_worktree
from dan.tools.git_worktree import TOOL_METADATA as _META_GIT_WORKTREE

GIT_WORKTREE_CAPABILITY_SCHEMA = build_tool_schema(
    name="git_worktree",
    description=_META_GIT_WORKTREE["description"],
    parameters=_META_GIT_WORKTREE["parameters"],
)

async def handle_git_worktree(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        # For paths, we might want to resolve them, but let's just pass kwargs for now.
        # Wait, csv_read needs path resolution. Let's do it generically if 'path' in args?
        # Actually, let's just pass args. The tools handle their own paths or we can wrap.
        if "path" in args and "git_worktree" in ("csv_read", "file_copy", "file_move", "file_delete", "compress"):
            args["path"] = str(_resolve_user_path(args["path"]))
        if "source" in args and "git_worktree" in ("file_copy", "file_move"):
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args and "git_worktree" in ("file_copy", "file_move"):
            args["destination"] = str(_resolve_user_path(args["destination"]))
        
        result = await _tool_git_worktree(**args)
        return CapabilityResult(
            success=True,
            message="Success",
            data=result,
            output_preview=str(result)[:1000]
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in git_worktree: {exc}")


from dan.tools.notify import notify as _tool_notify
from dan.tools.notify import TOOL_METADATA as _META_NOTIFY

NOTIFY_CAPABILITY_SCHEMA = build_tool_schema(
    name="notify",
    description=_META_NOTIFY["description"],
    parameters=_META_NOTIFY["parameters"],
)

async def handle_notify(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        # For paths, we might want to resolve them, but let's just pass kwargs for now.
        # Wait, csv_read needs path resolution. Let's do it generically if 'path' in args?
        # Actually, let's just pass args. The tools handle their own paths or we can wrap.
        if "path" in args and "notify" in ("csv_read", "file_copy", "file_move", "file_delete", "compress"):
            args["path"] = str(_resolve_user_path(args["path"]))
        if "source" in args and "notify" in ("file_copy", "file_move"):
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args and "notify" in ("file_copy", "file_move"):
            args["destination"] = str(_resolve_user_path(args["destination"]))
        
        result = await _tool_notify(**args)
        return CapabilityResult(
            success=True,
            message="Success",
            data=result,
            output_preview=str(result)[:1000]
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in notify: {exc}")


from dan.tools.text_diff import text_diff as _tool_text_diff
from dan.tools.text_diff import TOOL_METADATA as _META_TEXT_DIFF

TEXT_DIFF_CAPABILITY_SCHEMA = build_tool_schema(
    name="text_diff",
    description=_META_TEXT_DIFF["description"],
    parameters=_META_TEXT_DIFF["parameters"],
)

async def handle_text_diff(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        # For paths, we might want to resolve them, but let's just pass kwargs for now.
        # Wait, csv_read needs path resolution. Let's do it generically if 'path' in args?
        # Actually, let's just pass args. The tools handle their own paths or we can wrap.
        if "path" in args and "text_diff" in ("csv_read", "file_copy", "file_move", "file_delete", "compress"):
            args["path"] = str(_resolve_user_path(args["path"]))
        if "source" in args and "text_diff" in ("file_copy", "file_move"):
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args and "text_diff" in ("file_copy", "file_move"):
            args["destination"] = str(_resolve_user_path(args["destination"]))
        
        result = await _tool_text_diff(**args)
        return CapabilityResult(
            success=True,
            message="Success",
            data=result,
            output_preview=str(result)[:1000]
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in text_diff: {exc}")

def register_tool_capabilities(registry: ChatCapabilityRegistry) -> None:
    if os.environ.get('DAN_FULL_TOOLS') != '1':
        return
    logger.info('DAN_FULL_TOOLS=1: Registering 13 extra built-in tools.')

    registry.register(
        "python_eval",
        PYTHON_EVAL_CAPABILITY_SCHEMA,
        handle_python_eval,
        modes=["agent", "debug"],
        category="extra",
    )


    registry.register(
        "csv_read",
        CSV_READ_CAPABILITY_SCHEMA,
        handle_csv_read,
        modes=["agent", "conversation"],
        category="extra",
    )


    registry.register(
        "compress",
        COMPRESS_CAPABILITY_SCHEMA,
        handle_compress,
        modes=["agent"],
        category="extra",
    )


    registry.register(
        "file_copy",
        FILE_COPY_CAPABILITY_SCHEMA,
        handle_file_copy,
        modes=["agent"],
        category="extra",
    )


    registry.register(
        "file_move",
        FILE_MOVE_CAPABILITY_SCHEMA,
        handle_file_move,
        modes=["agent"],
        category="extra",
    )


    registry.register(
        "file_delete",
        FILE_DELETE_CAPABILITY_SCHEMA,
        handle_file_delete,
        modes=["agent"],
        category="extra",
    )


    registry.register(
        "git_status",
        GIT_STATUS_CAPABILITY_SCHEMA,
        handle_git_status,
        modes=["agent", "ask", "debug"],
        category="extra",
    )


    registry.register(
        "git_diff",
        GIT_DIFF_CAPABILITY_SCHEMA,
        handle_git_diff,
        modes=["agent", "ask", "debug"],
        category="extra",
    )


    registry.register(
        "git_log",
        GIT_LOG_CAPABILITY_SCHEMA,
        handle_git_log,
        modes=["agent", "ask", "debug"],
        category="extra",
    )


    registry.register(
        "git_branch",
        GIT_BRANCH_CAPABILITY_SCHEMA,
        handle_git_branch,
        modes=["agent"],
        category="extra",
    )


    registry.register(
        "git_commit",
        GIT_COMMIT_CAPABILITY_SCHEMA,
        handle_git_commit,
        modes=["agent"],
        category="extra",
    )


    registry.register(
        "git_worktree",
        GIT_WORKTREE_CAPABILITY_SCHEMA,
        handle_git_worktree,
        modes=["agent"],
        category="extra",
    )


    registry.register(
        "notify",
        NOTIFY_CAPABILITY_SCHEMA,
        handle_notify,
        modes=list(ALL_MODES),
        category="extra",
    )


    registry.register(
        "text_diff",
        TEXT_DIFF_CAPABILITY_SCHEMA,
        handle_text_diff,
        modes=["agent", "ask", "debug"],
        category="extra",
    )


# ── Introspection tools ────────────────────────────────────────────

INSPECT_NODE_SCHEMA = build_tool_schema(
    name="inspect_node",
    description="Inspect a node in a workflow graph, including its config, ports, and upstream variables.",
    parameters={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string", "description": "The workflow ID"},
            "node_id": {"type": "string", "description": "The node ID to inspect"},
            "run_id": {"type": "string", "description": "Optional run ID to get runtime values"}
        },
        "required": ["workflow_id", "node_id"]
    }
)

async def handle_inspect_node(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    workflow_id = args.get("workflow_id")
    node_id = args.get("node_id")
    run_id = args.get("run_id")
    if not workflow_id or not node_id:
        return CapabilityResult(success=False, message="Missing workflow_id or node_id")
    
    try:
        graph = ctx.graph_store.load_graph(workflow_id)
        if not graph:
            return CapabilityResult(success=False, message=f"Workflow not found: {workflow_id}")
        
        node = graph.get_node(node_id)
        if not node:
            return CapabilityResult(success=False, message=f"Node not found: {node_id}")
        
        upstream_vars = compute_upstream_variables(graph, node_id, run_id=run_id, run_store=ctx.run_store)
        
        data = {
            "node_id": node.id,
            "type": node.type,
            "config": node.config,
            "upstream_variables": upstream_vars,
        }
        return CapabilityResult(success=True, message="Node inspected", data=data)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error inspecting node: {exc}")

LIST_TEST_CASES_SCHEMA = build_tool_schema(
    name="list_test_cases",
    description="List test cases for a specific node in a workflow.",
    parameters={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string"},
            "node_id": {"type": "string"}
        },
        "required": ["workflow_id", "node_id"]
    }
)

async def handle_list_test_cases(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    workflow_id = args.get("workflow_id")
    node_id = args.get("node_id")
    if not workflow_id or not node_id:
        return CapabilityResult(success=False, message="Missing workflow_id or node_id")
    
    if not ctx.test_case_store:
        return CapabilityResult(success=False, message="Test case store not available")
    
    try:
        cases = ctx.test_case_store.list_cases(workflow_id, node_id)
        cases_data = [c.dict() if hasattr(c, "dict") else c.model_dump() for c in cases]
        return CapabilityResult(success=True, message=f"Found {len(cases)} test cases", data=cases_data)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error listing test cases: {exc}")

RUN_TEST_CASE_SCHEMA = build_tool_schema(
    name="run_test_case",
    description="Run a specific test case for a node.",
    parameters={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string"},
            "node_id": {"type": "string"},
            "case_id": {"type": "string"}
        },
        "required": ["workflow_id", "node_id", "case_id"]
    }
)

async def handle_run_test_case(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    workflow_id = args.get("workflow_id")
    node_id = args.get("node_id")
    case_id = args.get("case_id")
    
    if not all([workflow_id, node_id, case_id]):
        return CapabilityResult(success=False, message="Missing required arguments")
        
    if not ctx.test_case_store:
        return CapabilityResult(success=False, message="Test case store not available")
        
    try:
        case = ctx.test_case_store.get_case(workflow_id, node_id, case_id)
        if not case:
            return CapabilityResult(success=False, message=f"Test case {case_id} not found")
            
        graph = ctx.graph_store.load_graph(workflow_id)
        if not graph:
            return CapabilityResult(success=False, message=f"Workflow not found: {workflow_id}")
            
        node = graph.get_node(node_id)
        if not node:
            return CapabilityResult(success=False, message=f"Node not found: {node_id}")
            
        from dan.models.graph import Graph as GraphModel
        
        synthetic = GraphModel(
            nodes=[node],
            entry_points=[node_id],
            exit_points=[node_id],
        )
        
        import time
        import asyncio
        run_id = f"test-{case_id}-{int(time.time() * 1000)}"
        record = await ctx.run_manager.start_run(
            synthetic,
            graph_id=workflow_id,
            inputs=case.inputs,
            run_id=run_id,
        )
        
        deadline = time.time() + 120
        while True:
            record = ctx.run_manager.get_run(run_id)
            if record.status in ("completed", "failed", "cancelled"):
                break
            if time.time() > deadline:
                return CapabilityResult(success=False, message="Test case execution timed out")
            await asyncio.sleep(0.5)
            
        actual_outputs = record.state.get(node_id, {}) if record.state else {}
                
        # Compare with expected
        expected_outputs = case.expected_outputs or {}
        passed = True
        diffs = {}
        
        for k, v in expected_outputs.items():
            actual = actual_outputs.get(k)
            if actual != v:
                passed = False
                diffs[k] = {"expected": v, "actual": actual}
                
        data = {
            "run_id": run_id,
            "status": run.status,
            "passed": passed,
            "actual_outputs": actual_outputs,
            "expected_outputs": expected_outputs,
            "diffs": diffs
        }
        
        return CapabilityResult(
            success=True, 
            message="Test case passed" if passed else "Test case failed",
            data=data
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error running test case: {exc}")

def register_introspection_capabilities(registry: ChatCapabilityRegistry) -> None:
    registry.register(
        "inspect_node",
        INSPECT_NODE_SCHEMA,
        handle_inspect_node,
        modes=["agent", "ask", "debug"],
        category="introspection",
    )
    registry.register(
        "list_test_cases",
        LIST_TEST_CASES_SCHEMA,
        handle_list_test_cases,
        modes=["agent", "ask", "debug"],
        category="introspection",
    )
    registry.register(
        "run_test_case",
        RUN_TEST_CASE_SCHEMA,
        handle_run_test_case,
        modes=["agent", "debug"],
        category="introspection",
    )

