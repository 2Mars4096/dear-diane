"""Data processing capability handlers."""
from __future__ import annotations

import json
from typing import Any

from dan.server.capability_registry import CapabilityContext, CapabilityResult
from dan.server.capabilities._helpers import _FILE_READ_MAX, _resolve_user_path


async def handle_python_eval(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        from dan.tools.python_eval import python_eval as _tool_python_eval
        result = await _tool_python_eval(**args)
        return CapabilityResult(success=True, message="Success", data=result, output_preview=str(result)[:1000])
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in python_eval: {exc}")


async def handle_csv_read(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        if "path" in args:
            args["path"] = str(_resolve_user_path(args["path"]))
        from dan.tools.csv_read import csv_read as _tool_csv_read
        result = await _tool_csv_read(**args)
        return CapabilityResult(success=True, message="Success", data=result, output_preview=str(result)[:1000])
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in csv_read: {exc}")


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


async def handle_text_diff(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        from dan.tools.text_diff import text_diff as _tool_text_diff
        result = await _tool_text_diff(**args)
        return CapabilityResult(success=True, message="Success", data=result, output_preview=str(result)[:1000])
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in text_diff: {exc}")


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
