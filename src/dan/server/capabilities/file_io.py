"""File I/O capability handlers."""
from __future__ import annotations

import re as _re
from typing import Any

from dan.server.capability_registry import CapabilityContext, CapabilityResult
from dan.server.capabilities._helpers import (
    _FILE_READ_MAX,
    _failure_result,
    _resolve_user_path,
    _truncate,
)
from dan.tools.file_edit import file_edit as _file_edit_tool

_TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".tex", ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css",
    ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".bash", ".zsh",
    ".r", ".R", ".do", ".ado", ".sas", ".csv", ".tsv", ".xml", ".sql", ".bib",
    ".rst", ".org", ".log", ".env", ".gitignore", ".dockerignore", ".makefile",
    ".c", ".h", ".cpp", ".hpp", ".java", ".go", ".rs", ".rb", ".pl", ".lua",
    ".swift", ".kt", ".scala", ".m", ".mm", ".hs", ".jl", ".ex", ".exs",
})

_PDF_READ_MAX = 12_000


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
            return _failure_result(
                f"File not found: {raw_path}",
                error_type="target_missing",
            )

        start_line = args.get("start_line")
        end_line = args.get("end_line")
        grep_pattern = (args.get("grep") or "").strip()

        raw = resolved.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()
        total_lines = len(lines)
        file_size = resolved.stat().st_size

        if grep_pattern:
            pat = _re.compile(_re.escape(grep_pattern), _re.IGNORECASE)
            context_lines = 2
            matched_ranges: list[tuple[int, int]] = []
            for i, line in enumerate(lines):
                if pat.search(line):
                    lo = max(0, i - context_lines)
                    hi = min(total_lines - 1, i + context_lines)
                    if matched_ranges and lo <= matched_ranges[-1][1] + 1:
                        matched_ranges[-1] = (matched_ranges[-1][0], hi)
                    else:
                        matched_ranges.append((lo, hi))

            if not matched_ranges:
                return CapabilityResult(
                    success=True,
                    message=f"No matches for '{grep_pattern}' in {resolved.name} ({total_lines} lines).",
                    data={
                        "path": str(resolved),
                        "size": file_size,
                        "total_lines": total_lines,
                        "matches": 0,
                        "truncated": False,
                    },
                )

            parts: list[str] = []
            match_count = 0
            for lo, hi in matched_ranges[:30]:
                parts.append(f"--- lines {lo + 1}-{hi + 1} ---")
                for j in range(lo, hi + 1):
                    marker = ">" if pat.search(lines[j]) else " "
                    parts.append(f"{marker} {j + 1:>6}| {lines[j]}")
                    if marker == ">":
                        match_count += 1
                parts.append("")

            content = "\n".join(parts)
            truncated = False
            if len(content) > _FILE_READ_MAX:
                content = content[:_FILE_READ_MAX] + "\n[...truncated]"
                truncated = True
            header = f"grep '{grep_pattern}' in {resolved.name}: {match_count} matches across {len(matched_ranges)} regions ({total_lines} total lines)\n\n"
            return CapabilityResult(
                success=True,
                message=header + content,
                data={
                    "path": str(resolved),
                    "size": file_size,
                    "total_lines": total_lines,
                    "matches": match_count,
                    "truncated": truncated,
                },
            )

        if start_line is not None or end_line is not None:
            sl = max(0, (start_line or 1) - 1)
            el = min(total_lines, end_line or total_lines)
            selected = lines[sl:el]
            numbered = [f"{sl + i + 1:>6}| {line}" for i, line in enumerate(selected)]
            content = "\n".join(numbered)
            truncated = False
            if len(content) > _FILE_READ_MAX:
                content = content[:_FILE_READ_MAX] + "\n[...truncated]"
                truncated = True
            header = f"{resolved.name} lines {sl + 1}-{el} of {total_lines}\n\n"
            return CapabilityResult(
                success=True,
                message=header + content,
                data={
                    "path": str(resolved),
                    "size": file_size,
                    "total_lines": total_lines,
                    "requested_start_line": sl + 1,
                    "requested_end_line": el,
                    "returned_start_line": sl + 1 if selected else None,
                    "returned_end_line": el if selected and not truncated else None,
                    "truncated": truncated,
                },
            )

        content = raw
        truncated = False
        if len(content) > _FILE_READ_MAX:
            content = content[:_FILE_READ_MAX] + f"\n\n[truncated — file is {len(content):,} chars, showing first {_FILE_READ_MAX:,}]"
            truncated = True
        return CapabilityResult(
            success=True,
            message=content,
            data={
                "path": str(resolved),
                "size": file_size,
                "total_lines": total_lines,
                "requested_start_line": 1,
                "requested_end_line": total_lines,
                "returned_start_line": 1,
                "returned_end_line": total_lines if not truncated else None,
                "truncated": truncated,
            },
        )
    except Exception as exc:
        return _failure_result(
            f"Failed to read file: {exc}",
            error_type="internal_exception",
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


async def handle_file_edit(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    raw_path = (args.get("path") or args.get("file_path") or "").strip()
    if not raw_path:
        return CapabilityResult(success=False, message="No file path provided.")
    try:
        result = await _file_edit_tool(
            path=raw_path,
            start_line=args.get("start_line"),
            end_line=args.get("end_line"),
            content=args.get("content"),
            mode=args.get("mode", "replace"),
            encoding=args.get("encoding", "utf-8"),
        )
        return CapabilityResult(
            success=True,
            message=(
                f"{result['mode']} {result['path']} lines "
                f"{result['start_line']}-{result['end_line']}"
            ),
            data=result,
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Failed to edit file: {exc}")


async def handle_file_grep(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    raw_path = args.get("path", "").strip()
    pattern_str = args.get("pattern", "").strip()
    glob_filter = args.get("glob", "").strip()
    if not raw_path or not pattern_str:
        return CapabilityResult(success=False, message="Both path and pattern are required.")
    try:
        resolved = _resolve_user_path(raw_path)
        if not resolved.is_dir():
            return _failure_result(
                f"Directory not found: {raw_path}",
                error_type="target_missing",
            )

        try:
            pat = _re.compile(pattern_str, _re.IGNORECASE)
        except _re.error:
            pat = _re.compile(_re.escape(pattern_str), _re.IGNORECASE)
        results: list[str] = []
        files_searched = 0
        files_matched = 0

        iterator = resolved.rglob(glob_filter) if glob_filter else resolved.rglob("*")
        for filepath in sorted(iterator):
            if not filepath.is_file():
                continue
            if not glob_filter and filepath.suffix.lower() not in _TEXT_EXTENSIONS:
                continue
            files_searched += 1
            try:
                file_lines = filepath.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue

            matched_ranges: list[tuple[int, int]] = []
            for i, line in enumerate(file_lines):
                if pat.search(line):
                    lo = max(0, i - 1)
                    hi = min(len(file_lines) - 1, i + 1)
                    if matched_ranges and lo <= matched_ranges[-1][1] + 1:
                        matched_ranges[-1] = (matched_ranges[-1][0], hi)
                    else:
                        matched_ranges.append((lo, hi))

            file_matches: list[str] = []
            for lo, hi in matched_ranges:
                for j in range(lo, hi + 1):
                    marker = ">" if pat.search(file_lines[j]) else " "
                    file_matches.append(f"{marker} {j + 1:>5}| {file_lines[j]}")
                file_matches.append("")

            if file_matches:
                files_matched += 1
                rel = filepath.relative_to(resolved) if filepath.is_relative_to(resolved) else filepath
                results.append(f"\u2500\u2500 {rel} \u2500\u2500\n" + "\n".join(file_matches))

            if files_searched > 500 or len(results) > 30:
                break

        if not results:
            return CapabilityResult(
                success=True,
                message=f"No matches for '{pattern_str}' in {files_searched} files under {resolved.name}/",
                data={"path": str(resolved), "files_searched": files_searched, "matches": 0},
            )

        content = "\n\n".join(results)
        if len(content) > _FILE_READ_MAX:
            content = content[:_FILE_READ_MAX] + "\n[...truncated]"
        header = f"grep '{pattern_str}' in {resolved.name}/: {files_matched} files matched ({files_searched} searched)\n\n"
        return CapabilityResult(
            success=True,
            message=header + content,
            data={"path": str(resolved), "files_searched": files_searched, "files_matched": files_matched},
        )
    except Exception as exc:
        return _failure_result(
            f"Search failed: {exc}",
            error_type="internal_exception",
        )


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


async def handle_list_directory(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    raw_path = args.get("path", "").strip()
    if not raw_path:
        return CapabilityResult(success=False, message="No directory path provided.")
    resolved = _resolve_user_path(raw_path)
    if not resolved.is_dir():
        return _failure_result(
            f"Directory not found: {raw_path}",
            error_type="target_missing",
        )
    glob_pattern = args.get("glob_pattern", "")
    recursive = args.get("recursive", False)
    limit = args.get("limit", 200)
    start_after = str(args.get("start_after") or "").strip() or None
    try:
        from dan.tools.list_directory import list_directory as _tool_list_directory

        result = await _tool_list_directory(
            path=str(resolved),
            glob_pattern=glob_pattern or None,
            recursive=bool(recursive),
            limit=limit,
            start_after=start_after,
        )
        import datetime as _dt

        rendered_entries = []
        for entry in result.get("entries", []):
            entry_path = entry.get("path")
            absolute_entry = (
                _resolve_user_path(entry_path)
                if isinstance(entry_path, str) and entry_path
                else resolved
            )
            kind = "dir" if entry.get("type") == "directory" else "file"
            try:
                st = absolute_entry.stat()
                size = st.st_size if absolute_entry.is_file() else 0
                mtime = _dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                size = 0
                mtime = "            "
            rel = str(entry_path or absolute_entry.name)
            rendered_entries.append(f"  {kind}  {size:>8}  {mtime}  {rel}")

        notes: list[str] = []
        if glob_pattern:
            notes.append(f"Filter: glob_pattern={glob_pattern}")
        if recursive:
            notes.append("Mode: recursive")
        if start_after:
            notes.append(f'Continuation cursor: start_after="{start_after}"')
        if result.get("truncated"):
            count = result.get("count", 0)
            total_count = result.get("total_count", count)
            remaining_count = result.get("remaining_count", 0)
            next_start_after = result.get("next_start_after")
            notes.append(
                "NOTE: This listing is partial. "
                f"Showing {count} of {total_count} matching entries, sorted by relative path. "
                "Do NOT infer absence from this cutoff."
            )
            if next_start_after:
                notes.append(
                    f'Continue with start_after="{next_start_after}" '
                    "or narrow with glob_pattern before concluding something is missing."
                )
            if remaining_count:
                notes.append(f"{remaining_count} more matching entries remain after this page.")
        elif start_after and not rendered_entries:
            notes.append("No additional entries remain after the requested continuation cursor.")

        if rendered_entries:
            body = "\n".join(rendered_entries)
        elif glob_pattern:
            body = f"{resolved}/ (no matches for glob_pattern)"
        else:
            body = f"{resolved}/ (empty)"

        message_parts = [f"{resolved}/"]
        if notes:
            message_parts.append("\n".join(notes))
        if rendered_entries:
            message_parts.append(body)
        else:
            message_parts[-1] = body if not notes else message_parts[-1] + "\n" + body

        return CapabilityResult(
            success=True,
            message="\n\n".join(part for part in message_parts if part.strip()),
            data=result,
            output_preview=_truncate(
                f"Listed {resolved} ({result.get('count', 0)}/{result.get('total_count', result.get('count', 0))} entries)"
                + (" [partial]" if result.get("truncated") else "")
            ),
        )
    except Exception as exc:
        return _failure_result(
            f"Failed to list directory: {exc}",
            error_type="internal_exception",
        )


async def handle_file_copy(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        if "source" in args:
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args:
            args["destination"] = str(_resolve_user_path(args["destination"]))
        from dan.tools.file_copy import file_copy as _tool_file_copy
        result = await _tool_file_copy(**args)
        return CapabilityResult(success=True, message="Success", data=result, output_preview=str(result)[:1000])
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in file_copy: {exc}")


async def handle_file_move(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        if "source" in args:
            args["source"] = str(_resolve_user_path(args["source"]))
        if "destination" in args:
            args["destination"] = str(_resolve_user_path(args["destination"]))
        from dan.tools.file_move import file_move as _tool_file_move
        result = await _tool_file_move(**args)
        return CapabilityResult(success=True, message="Success", data=result, output_preview=str(result)[:1000])
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in file_move: {exc}")


async def handle_file_delete(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        if "path" in args:
            args["path"] = str(_resolve_user_path(args["path"]))
        from dan.tools.file_delete import file_delete as _tool_file_delete
        result = await _tool_file_delete(**args)
        return CapabilityResult(success=True, message="Success", data=result, output_preview=str(result)[:1000])
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in file_delete: {exc}")


async def handle_compress(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        if "path" in args:
            args["path"] = str(_resolve_user_path(args["path"]))
        from dan.tools.compress import compress as _tool_compress
        result = await _tool_compress(**args)
        return CapabilityResult(success=True, message="Success", data=result, output_preview=str(result)[:1000])
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in compress: {exc}")
