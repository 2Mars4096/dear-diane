"""Built-in tool: parse CSV/TSV files into structured data."""

from __future__ import annotations

import csv
import io
import os

from diane.tools._workspace import validate_path

TOOL_METADATA = {
    "tool_id": "csv_read",
    "description": (
        "Read and parse a CSV or TSV file into structured rows. "
        "Auto-detects delimiter if not specified. Returns headers and rows "
        "as a list of dicts. Handles UTF-8 BOM, quoted fields, and mixed encodings."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the CSV/TSV file.",
            },
            "delimiter": {
                "type": "string",
                "description": "Column delimiter. Omit to auto-detect (comma or tab).",
            },
            "max_rows": {
                "type": "integer",
                "description": "Maximum number of data rows to return.",
                "default": 1000,
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of column names to select. Omit to return all columns.",
            },
            "encoding": {
                "type": "string",
                "description": "File encoding.",
                "default": "utf-8-sig",
            },
        },
        "required": ["path"],
    },
    "examples": [
        {
            "input": {"path": "data.csv", "max_rows": 3},
            "output": {
                "headers": ["name", "age", "city"],
                "rows": [
                    {"name": "Alice", "age": "30", "city": "NYC"},
                    {"name": "Bob", "age": "25", "city": "LA"},
                ],
                "row_count": 2,
                "column_count": 3,
                "truncated": False,
            },
        },
    ],
    "category": "data",
    "returns": "dict with headers, rows (list of dicts), row_count, column_count, truncated",
}

MAX_FILE_SIZE = 50_000_000  # 50 MB


async def csv_read(
    path: str,
    delimiter: str | None = None,
    max_rows: int = 1000,
    columns: list[str] | None = None,
    encoding: str = "utf-8-sig",
    **_kwargs,
) -> dict:
    resolved = validate_path(path)

    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"File not found: '{path}'")

    size = os.path.getsize(resolved)
    if size > MAX_FILE_SIZE:
        raise ValueError(f"File '{path}' is {size:,} bytes (limit {MAX_FILE_SIZE:,}).")

    with open(resolved, encoding=encoding, newline="") as f:
        sample = f.read(8192)
        f.seek(0)

        if delimiter is None:
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
                delimiter = dialect.delimiter
            except csv.Error:
                delimiter = ","

        reader = csv.DictReader(f, delimiter=delimiter)
        headers = reader.fieldnames or []

        if columns:
            missing = [c for c in columns if c not in headers]
            if missing:
                raise ValueError(
                    f"Columns not found: {missing}. Available: {headers}"
                )

        rows = []
        total = 0
        for row in reader:
            total += 1
            if total <= max_rows:
                if columns:
                    rows.append({c: row[c] for c in columns})
                else:
                    rows.append(dict(row))

    selected_headers = columns if columns else headers
    return {
        "headers": selected_headers,
        "rows": rows,
        "row_count": len(rows),
        "total_rows": total,
        "column_count": len(selected_headers),
        "truncated": total > max_rows,
    }
