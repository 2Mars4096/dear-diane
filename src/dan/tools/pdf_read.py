"""Built-in tool: extract text from PDF files."""

from __future__ import annotations

from dan.tools._workspace import validate_path

TOOL_METADATA = {
    "tool_id": "pdf_read",
    "description": (
        "Extract text from a PDF file within the workspace. "
        "Supports optional page range selection. "
        "Requires the optional 'pypdf' package."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the PDF file.",
            },
            "start_page": {
                "type": "integer",
                "description": "First page to extract (0-indexed). Omit to start from the first page.",
            },
            "end_page": {
                "type": "integer",
                "description": "Last page to extract (0-indexed, exclusive). Omit to read all pages.",
            },
        },
        "required": ["path"],
    },
    "examples": [
        {
            "input": {"path": "docs/report.pdf", "start_page": 0, "end_page": 2},
            "output": {"text": "Page 1 text...\nPage 2 text...", "num_pages": 10, "metadata": {}},
        },
    ],
    "category": "document",
    "returns": "dict with text, num_pages, and metadata",
}


async def pdf_read(
    path: str,
    start_page: int | None = None,
    end_page: int | None = None,
    **_kwargs,
) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError(
            "pdf_read requires the 'pypdf' package. "
            "Install with: pip install 'pypdf>=4.0'"
        )

    resolved = validate_path(path)
    reader = PdfReader(resolved)
    total = len(reader.pages)

    s = start_page if start_page is not None else 0
    e = end_page if end_page is not None else total

    pages_text = []
    for i in range(max(s, 0), min(e, total)):
        pages_text.append(reader.pages[i].extract_text() or "")

    metadata = {}
    if reader.metadata:
        for key in ("title", "author", "subject", "creator"):
            val = getattr(reader.metadata, key, None)
            if val:
                metadata[key] = str(val)

    return {
        "text": "\n".join(pages_text),
        "num_pages": total,
        "metadata": metadata,
    }
