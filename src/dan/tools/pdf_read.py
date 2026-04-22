"""Built-in tool: extract text from PDF files, or describe pages via vision (figures/tables)."""

from __future__ import annotations

import base64
import logging
import os
import re

from dan.tools._workspace import validate_path

logger = logging.getLogger(__name__)

TOOL_METADATA = {
    "tool_id": "pdf_read",
    "description": (
        "Read a PDF file. Relative paths resolve against the workspace root; absolute and ~/ paths are allowed. "
        "mode='text': extract text only (fast; misses figures/tables). "
        "mode='vision': render each page as image and describe with a vision LLM — captures figures, tables, and layout. "
        "mode='hybrid': text-first, then vision on pages with figures/tables/equations (best quality). "
        "Supports optional page range. Requires 'pypdf' for text; for vision also 'pymupdf' and an API key."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "PDF path. Relative paths resolve against the workspace root; absolute and ~/ paths are allowed.",
            },
            "mode": {
                "type": "string",
                "enum": ["text", "vision", "hybrid", "structured"],
                "description": (
                    "text: extract text only (default). vision: describe each page with a vision model (figures, tables). "
                    "hybrid: text-first, then vision on pages with figures/tables/equations (best quality). "
                    "structured: placeholder for future marker/docling support."
                ),
                "default": "text",
            },
            "start_page": {
                "type": "integer",
                "description": "First page (0-indexed). Omit for first page.",
            },
            "end_page": {
                "type": "integer",
                "description": "Last page (0-indexed, exclusive). Omit for all pages.",
            },
            "vision_model": {
                "type": "string",
                "description": "Vision model for mode=vision (e.g. gpt-4o).",
                "default": "gpt-4o",
            },
            "vision_prompt": {
                "type": "string",
                "description": (
                    "Prompt for vision mode. Default asks for full page description including text, figures, tables."
                ),
            },
        },
        "required": ["path"],
    },
    "examples": [
        {
            "input": {"path": "docs/report.pdf", "start_page": 0, "end_page": 2},
            "output": {"text": "Page 1 text...\nPage 2 text...", "num_pages": 10, "metadata": {}},
        },
        {
            "input": {"path": "paper.pdf", "mode": "vision", "start_page": 0, "end_page": 3},
            "output": {"text": "[Page 1] ... [Page 2] ...", "num_pages": 10, "metadata": {}, "mode": "vision"},
        },
    ],
    "category": "document",
    "returns": "dict with text, num_pages, metadata; mode=vision adds per-page descriptions",
}

_DEFAULT_VISION_PROMPT = (
    "Describe this PDF page completely: all visible text (in reading order), "
    "figures, charts, tables (with structure and values where visible), and layout. "
    "Preserve section headings and list structure."
)
_EQUATION_AWARE_VISION_PROMPT = (
    "Extract this PDF page completely. For equations: use LaTeX notation (e.g. \\frac{a}{b}, \\sum_{i=1}^n), "
    "not natural language descriptions. For tables: use Markdown table format with | separators. "
    "Preserve section headings. Include all visible text in reading order, figures, charts, and layout."
)
_MAX_VISION_PAGES = 25


def _page_entries(
    pages_text: list[str],
    *,
    actual_start: int,
    source_mode: str,
    source_modes: dict[int, str] | None = None,
) -> list[dict]:
    entries: list[dict] = []
    for idx, text in enumerate(pages_text):
        entries.append(
            {
                "page_number": actual_start + idx + 1,
                "text": text,
                "source_mode": (source_modes or {}).get(idx, source_mode),
            }
        )
    return entries


def _classify_page_needs_vision(text: str) -> bool:
    if len(text.strip()) < 50:
        return True
    if re.search(r"(?:Figure|Table)\s+\d+", text, re.IGNORECASE):
        return True
    if re.search(r"\\begin\{|\\end\{", text):
        return True
    unicode_math = "∑∫∂∇∬∮√∞±×÷≤≥≠≈∈∉⊂⊃∪∩"
    if any(c in text for c in unicode_math):
        return True
    if re.search(r"\[(?:Figure|Chart|Diagram|Graph)\s*[\d\]]", text, re.IGNORECASE):
        return True
    return False


def _extract_structure(pages_text: list[str]) -> list[dict]:
    structure: list[dict] = []
    numbered = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")
    markdown = re.compile(r"^(#{1,6})\s+(.+)$")
    known = re.compile(r"^(Abstract|Introduction|Methods?|Results?|Discussion|Conclusion|References?|Appendix)\s*$", re.IGNORECASE)
    for i, page_text in enumerate(pages_text):
        page_num = i + 1
        for line in page_text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = markdown.match(line)
            if m:
                structure.append({"heading": m.group(2).strip(), "level": len(m.group(1)), "page_number": page_num})
                continue
            m = numbered.match(line)
            if m and m.group(1).count(".") < 4:
                structure.append({"heading": m.group(2).strip(), "level": m.group(1).count(".") + 1, "page_number": page_num})
                continue
            if known.match(line):
                structure.append({"heading": line, "level": 1, "page_number": page_num})
    return structure


def _bounded_page_window(
    total: int,
    start_page: int | None,
    end_page: int | None,
    *,
    max_pages: int | None = None,
) -> tuple[int, int, int, bool]:
    start = max(start_page if start_page is not None else 0, 0)
    end = min(end_page if end_page is not None else total, total)
    requested = max(end - start, 0)
    truncated = False
    if max_pages is not None and requested > max_pages:
        end = start + max_pages
        truncated = True
    return start, end, requested, truncated


async def pdf_read(
    path: str,
    mode: str = "text",
    start_page: int | None = None,
    end_page: int | None = None,
    vision_model: str = "gpt-4o",
    vision_prompt: str | None = None,
    **_kwargs,
) -> dict:
    resolved = validate_path(path)
    return await read_pdf_file(
        resolved,
        mode=mode,
        start_page=start_page,
        end_page=end_page,
        vision_model=vision_model,
        vision_prompt=vision_prompt,
    )


async def read_pdf_file(
    resolved_path: str,
    mode: str = "text",
    start_page: int | None = None,
    end_page: int | None = None,
    vision_model: str = "gpt-4o",
    vision_prompt: str | None = None,
    **_kwargs,
) -> dict:
    if not os.path.isfile(resolved_path):
        raise FileNotFoundError(f"PDF not found: '{resolved_path}'")

    if mode == "vision":
        return await _pdf_read_vision(
            resolved_path,
            start_page=start_page,
            end_page=end_page,
            model=vision_model,
            prompt=vision_prompt or _DEFAULT_VISION_PROMPT,
        )

    if mode == "hybrid":
        return await _pdf_read_hybrid(
            resolved_path,
            start_page=start_page,
            end_page=end_page,
            model=vision_model,
            vision_prompt=vision_prompt,
        )

    if mode == "structured":
        logger.info(
            "pdf_read mode='structured' is a placeholder; future marker/docling support. Falling back to text."
        )
        mode = "text"

    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError(
            "pdf_read (text mode) requires the 'pypdf' package. "
            "Install with: pip install 'pypdf>=4.0'"
        )

    reader = PdfReader(resolved_path)
    total = len(reader.pages)
    s = start_page if start_page is not None else 0
    e = end_page if end_page is not None else total

    pages_text = []
    actual_start = max(s, 0)
    actual_end = min(e, total)
    for i in range(actual_start, actual_end):
        pages_text.append(reader.pages[i].extract_text() or "")

    metadata = {}
    if reader.metadata:
        for key in ("title", "author", "subject", "creator"):
            val = getattr(reader.metadata, key, None)
            if val:
                metadata[key] = str(val)

    structure = _extract_structure(pages_text)

    return {
        "text": "\n".join(pages_text),
        "num_pages": total,
        "metadata": metadata,
        "mode": "text",
        "pages": _page_entries(pages_text, actual_start=actual_start, source_mode="text"),
        "pages_requested": max(e - s, 0),
        "pages_returned": max(actual_end - actual_start, 0),
        "truncated": False,
        "structure": structure,
    }


async def _pdf_read_hybrid(
    resolved_path: str,
    start_page: int | None = None,
    end_page: int | None = None,
    model: str = "gpt-4o",
    vision_prompt: str | None = None,
) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError(
            "pdf_read (hybrid mode) requires the 'pypdf' package. "
            "Install with: pip install 'pypdf>=4.0'"
        )

    reader = PdfReader(resolved_path)
    total = len(reader.pages)
    s = start_page if start_page is not None else 0
    e = end_page if end_page is not None else total
    actual_start = max(s, 0)
    actual_end = min(e, total)

    pages_text = []
    for i in range(actual_start, actual_end):
        pages_text.append(reader.pages[i].extract_text() or "")

    metadata = {}
    if reader.metadata:
        for key in ("title", "author", "subject", "creator"):
            val = getattr(reader.metadata, key, None)
            if val:
                metadata[key] = str(val)

    flagged = [i for i, t in enumerate(pages_text) if _classify_page_needs_vision(t)]
    vision_prompt_used = vision_prompt or _EQUATION_AWARE_VISION_PROMPT

    vision_results: dict[int, str] = {}
    try:
        import fitz
    except ImportError:
        logger.info("pdf_read hybrid: pymupdf not installed; using text-only for all pages.")
    else:
        openai_api_key = os.environ.get("DAN_OPENAI_API_KEY")
        llm_api_key = os.environ.get("DAN_LLM_API_KEY")
        api_key = openai_api_key or llm_api_key
        if not api_key:
            logger.info("pdf_read hybrid: no API key; using text-only for all pages.")
        else:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                logger.info("pdf_read hybrid: openai package not installed; using text-only for all pages.")
            else:
                base_url = (
                    os.environ.get("DAN_OPENAI_BASE_URL", "https://api.openai.com/v1")
                    if openai_api_key
                    else os.environ.get("DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1")
                )
                client = AsyncOpenAI(api_key=api_key, base_url=base_url)
                doc = fitz.open(resolved_path)
                try:
                    capped = flagged[: _MAX_VISION_PAGES]
                    for idx in capped:
                        i = actual_start + idx
                        page = doc[i]
                        pix = page.get_pixmap(dpi=150, alpha=False)
                        png_bytes = pix.tobytes("png")
                        b64 = base64.b64encode(png_bytes).decode("ascii")
                        response = await client.chat.completions.create(
                            model=model,
                            messages=[
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": f"Page {i + 1} of a PDF.\n{vision_prompt_used}"},
                                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                                    ],
                                }
                            ],
                            max_tokens=2048,
                        )
                        page_text = (response.choices[0].message.content or "").strip()
                        vision_results[idx] = page_text
                finally:
                    doc.close()

    parts = []
    page_entries = []
    for i, t in enumerate(pages_text):
        page_num = actual_start + i + 1
        if i in vision_results:
            page_text = vision_results[i]
            parts.append(f"[Page {page_num}]\n{page_text}")
            page_entries.append(
                {
                    "page_number": page_num,
                    "text": page_text,
                    "source_mode": "vision",
                }
            )
        else:
            parts.append(f"[Page {page_num}]\n{t}")
            page_entries.append(
                {
                    "page_number": page_num,
                    "text": t,
                    "source_mode": "text",
                }
            )

    structure = _extract_structure(pages_text)

    return {
        "text": "\n\n".join(parts),
        "num_pages": total,
        "metadata": metadata,
        "mode": "hybrid",
        "pages": page_entries,
        "pages_requested": max(e - s, 0),
        "pages_returned": max(actual_end - actual_start, 0),
        "truncated": False,
        "structure": structure,
    }


async def _pdf_read_vision(
    resolved_path: str,
    start_page: int | None = None,
    end_page: int | None = None,
    model: str = "gpt-4o",
    prompt: str = _DEFAULT_VISION_PROMPT,
) -> dict:
    """Render PDF pages to images and describe each with a vision LLM."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError(
            "pdf_read with mode='vision' requires 'pymupdf'. "
            "Install with: pip install pymupdf"
        )

    openai_api_key = os.environ.get("DAN_OPENAI_API_KEY")
    llm_api_key = os.environ.get("DAN_LLM_API_KEY")
    api_key = openai_api_key or llm_api_key
    if openai_api_key:
        base_url = os.environ.get("DAN_OPENAI_BASE_URL", "https://api.openai.com/v1")
    else:
        base_url = os.environ.get("DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1")
    if not api_key:
        raise RuntimeError(
            "Vision mode requires an API key. Set DAN_OPENAI_API_KEY or DAN_LLM_API_KEY."
        )

    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise RuntimeError("Vision mode requires the openai package. Install: pip install openai")

    doc = fitz.open(resolved_path)
    try:
        total = len(doc)
        metadata = {}
        if doc.metadata:
            for key in ("title", "author", "subject", "creator"):
                val = doc.metadata.get(key)
                if val:
                    metadata[key] = str(val)

        s, e, requested_pages, truncated = _bounded_page_window(
            total,
            start_page,
            end_page,
            max_pages=_MAX_VISION_PAGES,
        )
        if truncated:
            logger.warning("Vision mode capped to %d pages", _MAX_VISION_PAGES)

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        parts = []
        pages = []
        for i in range(s, e):
            page = doc[i]
            pix = page.get_pixmap(dpi=150, alpha=False)
            png_bytes = pix.tobytes("png")
            b64 = base64.b64encode(png_bytes).decode("ascii")

            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"Page {i + 1} of a PDF.\n{prompt}"},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64}"},
                            },
                        ],
                    }
                ],
                max_tokens=2048,
            )
            page_text = (response.choices[0].message.content or "").strip()
            parts.append(f"[Page {i + 1}]\n{page_text}")
            pages.append(
                {
                    "page_number": i + 1,
                    "text": page_text,
                    "source_mode": "vision",
                }
            )
        combined = "\n\n".join(parts)
    finally:
        doc.close()

    return {
        "text": combined,
        "num_pages": total,
        "metadata": metadata,
        "mode": "vision",
        "pages": pages,
        "pages_requested": requested_pages,
        "pages_returned": max(e - s, 0),
        "truncated": truncated,
        "warning": (
            f"Vision mode processed {max(e - s, 0)} of {requested_pages} requested pages."
            if truncated else ""
        ),
    }
