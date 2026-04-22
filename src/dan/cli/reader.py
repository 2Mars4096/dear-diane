"""dan-reader - flat PDF reader CLI for extraction, summarization, and conversion."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Sequence

from dan.cli import load_env, normalize_workspace_root, resolve_config
from dan.cli import live_gateway
from dan.providers import LLMProvider
from dan.server.runtime_config import build_engine_config_from_env
from dan.tools._workspace import validate_path

DEFAULT_ACTION = "summarize"
DEFAULT_OUTPUT_FORMAT = "md"
DEFAULT_PDF_MODE = "text"
DEFAULT_PAGE_WINDOW = 6
DEFAULT_PARALLELISM = 4
DEFAULT_MAX_FRAGMENT_CHARS = 7000
DEFAULT_SUMMARY_SENTENCE_COUNT = 3
DEFAULT_VISION_MODEL = "gpt-4o"

_ROMAN_HEADING_RE = re.compile(r"^(?P<label>[IVXLCM]+)\s*\.?\s+(?P<title>.+)$")
_LETTER_HEADING_RE = re.compile(r"^(?P<label>[A-Z])\s*\.?\s+(?P<title>.+)$")
_NUMBERED_HEADING_RE = re.compile(r"^(?P<label>\d+(?:\s*\.\s*\d+)*)\s*\.?\s+(?P<title>.+)$")
_DOI_RE = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_JOURNAL_RE = re.compile(r"journal", re.IGNORECASE)

_EXACT_HEADINGS = {
    "abstract": ("Abstract", 1),
    "references": ("References", 1),
    "supporting information": ("Supporting Information", 1),
    "introduction": ("Introduction", 1),
    "conclusion": ("Conclusion", 1),
    "conclusions": ("Conclusions", 1),
    "appendix": ("Appendix", 1),
}

_FRONTMATTER_SKIP_RE = re.compile(
    r"^(?:the journal of |journal of |doi:|correspondence:|downloaded from )",
    re.IGNORECASE,
)
_RUNNING_HEADER_RE = re.compile(
    r"^(?:\d+\s+the journal of finance|.+\s+\d{4})$",
    re.IGNORECASE,
)
_PDF_BOILERPLATE_RE = re.compile(
    r"(?:downloaded from |wiley online library|terms and conditions|creative commons license)",
    re.IGNORECASE,
)
_PAGE_NUMBER_ONLY_RE = re.compile(r"^\d+$")
_HYPHENATED_LINE_BREAK_RE = re.compile(r"(?<=\w)-\s*\n\s*(?=[a-z])")
_INLINE_LINE_BREAK_RE = re.compile(r"(?<![.!?;:])\n(?!\n)")

_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


@dataclass
class ReaderTask:
    action: str
    source_path: str
    output_format: str
    workspace_root: str
    output_path: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    parallelism: int = DEFAULT_PARALLELISM
    include_backmatter: bool = False
    page_window: int = DEFAULT_PAGE_WINDOW
    max_fragment_chars: int = DEFAULT_MAX_FRAGMENT_CHARS
    use_llm: bool = True
    pdf_mode: str = DEFAULT_PDF_MODE
    vision_model: str = ""
    vision_prompt: str = ""
    start_page: int | None = None
    end_page: int | None = None


@dataclass
class ReaderSection:
    heading: str
    level: int
    page_start: int
    page_end: int
    span_id: str = ""
    parent_heading: str = ""
    section_path: list[str] = field(default_factory=list)
    child_headings: list[str] = field(default_factory=list)
    text: str = ""
    summary: str = ""
    key_points: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    confidence: float = 0.0
    needs_followup: bool = False
    needs_vision: bool = False


@dataclass
class ReaderDocument:
    source_path: str
    source_kind: str
    page_count: int
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: str = ""
    journal: str = ""
    doi: str = ""
    abstract: str = ""
    notes: list[str] = field(default_factory=list)
    sections: list[ReaderSection] = field(default_factory=list)


@dataclass
class ReaderResult:
    action: str
    output_format: str
    task: ReaderTask
    document: ReaderDocument
    rendered: str
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _DocumentLine:
    page_number: int
    text: str


@dataclass(frozen=True)
class _HeadingMarker:
    heading: str
    level: int
    line_index: int
    page_number: int
    explicit: bool = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dan-reader",
        description=(
            "Read a local PDF directly into a bounded extraction, summary, or conversion "
            "artifact without using the full DAN Research organism."
        ),
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("summarize", "extract", "convert"),
        default=DEFAULT_ACTION,
        help="Reader action. Defaults to summarize.",
    )
    parser.add_argument("source", help="Local PDF path.")
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("md", "latex", "json"),
        default=DEFAULT_OUTPUT_FORMAT,
        help="Output format. Defaults to md.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output path. When omitted, dan-reader prints to stdout.",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root used for relative path resolution. Defaults to the current directory.",
    )
    parser.add_argument("--model", default="", help="LLM model for summarize mode.")
    parser.add_argument("--api-key", default="", help="Override DAN/OpenAI API key.")
    parser.add_argument("--base-url", default="", help="Override provider base URL.")
    parser.add_argument("--start-page", type=int, default=None, help="Optional first page (0-indexed).")
    parser.add_argument(
        "--end-page",
        type=int,
        default=None,
        help="Optional end page (0-indexed, exclusive).",
    )
    parser.add_argument(
        "--pdf-mode",
        choices=("text", "hybrid", "vision"),
        default=DEFAULT_PDF_MODE,
        help="PDF extraction mode. text is fastest; hybrid/vision use the shared pdf_read vision path.",
    )
    parser.add_argument(
        "--vision-model",
        default="",
        help=(
            "Vision-capable model used when --pdf-mode is hybrid or vision. "
            f"Defaults to the configured model or {DEFAULT_VISION_MODEL}."
        ),
    )
    parser.add_argument(
        "--vision-prompt",
        default="",
        help="Optional override for the shared PDF vision prompt.",
    )
    parser.add_argument(
        "--parallelism",
        type=int,
        default=DEFAULT_PARALLELISM,
        help=f"Parallel section-summary concurrency. Defaults to {DEFAULT_PARALLELISM}.",
    )
    parser.add_argument(
        "--page-window",
        type=int,
        default=DEFAULT_PAGE_WINDOW,
        help=(
            "Fallback page chunk size used when heading extraction cannot find stable sections. "
            f"Defaults to {DEFAULT_PAGE_WINDOW}."
        ),
    )
    parser.add_argument(
        "--max-fragment-chars",
        type=int,
        default=DEFAULT_MAX_FRAGMENT_CHARS,
        help=(
            "Per-summary text budget before section summaries switch to fragment map-reduce. "
            f"Defaults to {DEFAULT_MAX_FRAGMENT_CHARS}."
        ),
    )
    parser.add_argument(
        "--include-backmatter",
        action="store_true",
        help="Include sections such as References and Supporting Information in summarize output.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable model summarization and fall back to deterministic snippet summaries.",
    )
    return parser


def _build_live_provider(model: str, *, api_key: str | None, base_url: str | None) -> LLMProvider:
    return live_gateway.build_gateway_backed_live_provider(
        model,
        api_key=api_key,
        base_url=base_url,
    )


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _clamp_confidence(value: Any, *, default: float) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(score, 1.0))


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value or "").strip().lower()
    return normalized in {"1", "true", "yes", "y", "on"}


def _normalize_string_list(value: Any, *, limit: int) -> list[str]:
    if isinstance(value, str):
        candidates = re.split(r"\n+|(?:^|\s)[-•]\s+", value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        candidates = [str(item or "") for item in value]
    else:
        candidates = []
    cleaned: list[str] = []
    for item in candidates:
        normalized = _normalize_text(item)
        if not normalized:
            continue
        cleaned.append(normalized)
        if len(cleaned) >= limit:
            break
    return cleaned


def _is_pdf_boilerplate(text: str) -> bool:
    cleaned = _normalize_text(text)
    if not cleaned:
        return True
    if _PAGE_NUMBER_ONLY_RE.fullmatch(cleaned):
        return True
    if _looks_like_frontmatter_skip(cleaned):
        return True
    if _PDF_BOILERPLATE_RE.search(cleaned):
        return True
    return False


def _clean_pdf_line(text: str) -> str:
    cleaned = _normalize_text(text)
    if not cleaned or _is_pdf_boilerplate(cleaned):
        return ""
    return cleaned


def _repair_pdf_text(text: str) -> str:
    repaired = str(text or "").strip()
    if not repaired:
        return ""
    repaired = _HYPHENATED_LINE_BREAK_RE.sub("", repaired)
    repaired = _INLINE_LINE_BREAK_RE.sub(" ", repaired)
    repaired = "\n".join(
        line
        for line in (_normalize_text(part) for part in repaired.splitlines())
        if line and not _is_pdf_boilerplate(line)
    )
    repaired = re.sub(r"\n{3,}", "\n\n", repaired)
    return repaired.strip()


def _default_quotes(text: str, *, count: int = 2) -> list[str]:
    return [sentence for sentence in _fallback_sentences(text, count=count) if sentence]


def _normalize_summary_packet(payload: dict[str, Any] | None, *, section: ReaderSection) -> dict[str, Any]:
    if payload is None:
        return {}
    key_points = _normalize_string_list(payload.get("key_points", []), limit=3)
    quotes = _normalize_string_list(payload.get("quotes", []), limit=2)
    claims = _normalize_string_list(payload.get("claims", []), limit=3)
    keywords = _normalize_string_list(payload.get("keywords", []), limit=5)
    summary = _normalize_text(str(payload.get("summary", "")))
    if not claims:
        claims = list(key_points[:3])
    if not quotes:
        quotes = _default_quotes(section.text, count=2)
    return {
        "summary": summary,
        "key_points": key_points,
        "keywords": keywords,
        "quotes": quotes[:2],
        "claims": claims[:3],
        "confidence": _clamp_confidence(payload.get("confidence"), default=0.6),
        "needs_followup": _coerce_bool(payload.get("needs_followup")),
        "needs_vision": _coerce_bool(payload.get("needs_vision")),
    }


def _apply_summary_packet(section: ReaderSection, packet: dict[str, Any]) -> None:
    section.summary = _normalize_text(str(packet.get("summary", "")))
    section.key_points = _normalize_string_list(packet.get("key_points", []), limit=3)
    section.keywords = _normalize_string_list(packet.get("keywords", []), limit=5)
    section.quotes = _normalize_string_list(packet.get("quotes", []), limit=2)
    section.claims = _normalize_string_list(packet.get("claims", []), limit=3)
    section.confidence = _clamp_confidence(packet.get("confidence"), default=0.0)
    section.needs_followup = _coerce_bool(packet.get("needs_followup"))
    section.needs_vision = _coerce_bool(packet.get("needs_vision"))


def _title_case_upper_name(text: str) -> str:
    cleaned = _normalize_text(text)
    if not cleaned:
        return ""
    if cleaned.upper() != cleaned:
        return cleaned
    return " ".join(part.capitalize() for part in cleaned.split())


def _is_probable_author_line(text: str) -> bool:
    cleaned = _normalize_text(text)
    if not cleaned:
        return False
    letters = [char for char in cleaned if char.isalpha()]
    if len(letters) < 5:
        return False
    if any(
        token in cleaned.lower()
        for token in (
            "university",
            "department",
            "correspondence",
            "doi",
            "journal",
            "vol.",
            "volume",
        )
    ):
        return False
    words = cleaned.replace(",", " ").split()
    if len(words) > 6:
        return False
    uppercase_ratio = sum(char.isupper() for char in letters) / len(letters)
    return uppercase_ratio > 0.7


def _looks_like_intro_body_start(text: str) -> bool:
    cleaned = _normalize_text(text)
    if len(cleaned) < 24:
        return False
    letters = [char for char in cleaned if char.isalpha()]
    if len(letters) < 12:
        return False
    head_words = [
        re.sub(r"[^A-Za-z]", "", word)
        for word in cleaned.split()[:6]
        if re.sub(r"[^A-Za-z]", "", word)
    ]
    if head_words:
        head_upper_ratio = sum(word.isupper() for word in head_words) / len(head_words)
        if head_upper_ratio >= 0.8 and len(head_words) >= 4:
            return not _looks_like_frontmatter_skip(cleaned)
    uppercase_ratio = sum(char.isupper() for char in letters) / len(letters)
    return uppercase_ratio > 0.75 and not _looks_like_frontmatter_skip(cleaned)


def _is_running_header(text: str) -> bool:
    cleaned = _normalize_text(text)
    if not cleaned:
        return False
    if _RUNNING_HEADER_RE.match(cleaned):
        return True
    if _JOURNAL_RE.search(cleaned) and any(char.isdigit() for char in cleaned):
        return True
    return False


def _looks_like_frontmatter_skip(text: str) -> bool:
    cleaned = _normalize_text(text)
    if not cleaned:
        return True
    if _FRONTMATTER_SKIP_RE.match(cleaned):
        return True
    if _is_running_header(cleaned):
        return True
    return False


def _extract_title(lines: Sequence[_DocumentLine], metadata: dict[str, str], abstract_index: int | None) -> str:
    metadata_title = _normalize_text(metadata.get("title", ""))
    if metadata_title:
        return metadata_title
    stop = abstract_index if abstract_index is not None else min(len(lines), 12)
    candidates: list[str] = []
    for idx in range(min(stop, len(lines))):
        text = _normalize_text(lines[idx].text)
        if not text or _looks_like_frontmatter_skip(text):
            continue
        if _is_probable_author_line(text):
            break
        if len(text) > 90:
            break
        candidates.append(text)
        if len(candidates) >= 3:
            break
    return _normalize_text(" ".join(candidates))


def _extract_authors(lines: Sequence[_DocumentLine], metadata: dict[str, str], abstract_index: int | None) -> list[str]:
    metadata_author = _normalize_text(metadata.get("author", ""))
    if metadata_author:
        parts = [part.strip() for part in re.split(r"\band\b|,", metadata_author) if part.strip()]
        return [_title_case_upper_name(part) for part in parts]
    stop = abstract_index if abstract_index is not None else min(len(lines), 16)
    for idx in range(min(stop, len(lines))):
        text = _normalize_text(lines[idx].text)
        if _is_probable_author_line(text):
            return [_title_case_upper_name(text)]
    return []


def _extract_year(lines: Sequence[_DocumentLine], metadata: dict[str, str]) -> str:
    for value in (
        metadata.get("subject", ""),
        metadata.get("title", ""),
        *(line.text for line in lines[:24]),
    ):
        match = _YEAR_RE.search(str(value or ""))
        if match:
            return match.group(0)
    return ""


def _extract_journal(lines: Sequence[_DocumentLine], metadata: dict[str, str]) -> str:
    for value in (metadata.get("subject", ""), *(line.text for line in lines[:12])):
        cleaned = _normalize_text(str(value or ""))
        if _JOURNAL_RE.search(cleaned):
            if "downloaded from" in cleaned.lower():
                continue
            if cleaned.upper() == cleaned:
                return cleaned.title()
            return cleaned
    return ""


def _extract_doi(lines: Sequence[_DocumentLine]) -> str:
    for line in lines[:32]:
        match = _DOI_RE.search(line.text)
        if match:
            return match.group(1)
    return ""


def _find_abstract_marker(lines: Sequence[_DocumentLine]) -> int | None:
    for idx, line in enumerate(lines[:80]):
        if _normalize_text(line.text).lower() == "abstract":
            return idx
    return None


def _extract_abstract(lines: Sequence[_DocumentLine], abstract_index: int | None) -> tuple[str, int | None]:
    if abstract_index is None:
        return "", None
    body_start: int | None = None
    collected: list[str] = []
    for idx in range(abstract_index + 1, min(len(lines), abstract_index + 80)):
        text = _normalize_text(lines[idx].text)
        if not text:
            continue
        if _looks_like_intro_body_start(text):
            body_start = idx
            break
        if _is_probable_author_line(text) or text.lower().startswith("correspondence:"):
            continue
        collected.append(text)
    return _repair_pdf_text("\n".join(collected)), body_start


def _looks_like_heading_title(title: str) -> bool:
    cleaned = _normalize_text(title)
    if not cleaned:
        return False
    if len(cleaned) > 110:
        return False
    if cleaned.endswith("."):
        return False
    if _is_running_header(cleaned):
        return False
    return True


def _looks_like_structured_heading_words(title: str) -> bool:
    cleaned = _normalize_text(title)
    if not cleaned:
        return False
    words = [re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9?]+$", "", word) for word in cleaned.split()]
    words = [word for word in words if word]
    if len(words) > 14:
        return False
    alpha_words = [word for word in words if any(char.isalpha() for char in word)]
    if not alpha_words:
        return False
    if not (alpha_words[0].isupper() or alpha_words[0][0].isupper()):
        return False
    if len(alpha_words) == 1:
        return True
    uppercase_starts = sum(
        1 for word in alpha_words if word.isupper() or word[0].isupper()
    )
    return uppercase_starts >= max(2, (len(alpha_words) + 1) // 2)


def _detect_heading_marker(text: str) -> tuple[str, int] | None:
    cleaned = _normalize_text(text)
    if not cleaned or _is_running_header(cleaned):
        return None
    exact = _EXACT_HEADINGS.get(cleaned.lower())
    if exact:
        return exact

    roman_match = _ROMAN_HEADING_RE.match(cleaned)
    roman_label = roman_match.group("label") if roman_match else ""
    if (
        roman_match
        and (len(roman_label) > 1 or roman_label in {"I", "V", "X"})
        and _looks_like_heading_title(roman_match.group("title"))
        and _looks_like_structured_heading_words(roman_match.group("title"))
    ):
        return (f"{roman_label}. {roman_match.group('title')}", 1)

    letter_match = _LETTER_HEADING_RE.match(cleaned)
    if (
        letter_match
        and _looks_like_heading_title(letter_match.group("title"))
        and _looks_like_structured_heading_words(letter_match.group("title"))
    ):
        return (f"{letter_match.group('label')}. {letter_match.group('title')}", 2)

    numbered_match = _NUMBERED_HEADING_RE.match(cleaned)
    if (
        numbered_match
        and _looks_like_heading_title(numbered_match.group("title"))
        and (
            "." in numbered_match.group("label")
            or _looks_like_structured_heading_words(numbered_match.group("title"))
        )
    ):
        label = re.sub(r"\s*\.\s*", ".", numbered_match.group("label").strip())
        return (f"{label} {numbered_match.group('title')}", label.count(".") + 1)

    return None


def _dedupe_markers(markers: Sequence[_HeadingMarker]) -> list[_HeadingMarker]:
    deduped: list[_HeadingMarker] = []
    seen: set[tuple[str, int, int]] = set()
    for marker in sorted(markers, key=lambda item: (item.line_index, item.level, item.heading)):
        key = (marker.heading.lower(), marker.page_number, marker.line_index)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(marker)
    return deduped


def _annotate_sections(sections: list[ReaderSection]) -> None:
    stack: list[ReaderSection] = []
    for index, section in enumerate(sections, start=1):
        while stack and stack[-1].level >= section.level:
            stack.pop()
        section.span_id = f"section-{index:03d}"
        section.parent_heading = stack[-1].heading if stack else ""
        section.section_path = [ancestor.heading for ancestor in stack] + [section.heading]
        stack.append(section)
    for index, section in enumerate(sections):
        subtree_end = section.page_end
        child_headings: list[str] = []
        for follower in sections[index + 1 :]:
            if follower.level <= section.level:
                break
            subtree_end = max(subtree_end, follower.page_end)
            if follower.parent_heading == section.heading:
                child_headings.append(follower.heading)
        section.page_end = max(section.page_end, subtree_end)
        section.child_headings = child_headings


def _build_sections_from_lines(
    source_path: str,
    pages_text: Sequence[str],
    metadata: dict[str, str],
    *,
    page_window: int,
) -> ReaderDocument:
    lines: list[_DocumentLine] = []
    for page_index, page_text in enumerate(pages_text, start=1):
        for raw_line in page_text.splitlines():
            cleaned = _clean_pdf_line(raw_line)
            if cleaned:
                lines.append(_DocumentLine(page_number=page_index, text=cleaned))

    abstract_index = _find_abstract_marker(lines)
    abstract_text, intro_start = _extract_abstract(lines, abstract_index)
    markers: list[_HeadingMarker] = []
    for idx, line in enumerate(lines):
        marker = _detect_heading_marker(line.text)
        if marker is None:
            continue
        markers.append(
            _HeadingMarker(
                heading=marker[0],
                level=marker[1],
                line_index=idx,
                page_number=line.page_number,
                explicit=True,
            )
        )

    if intro_start is not None:
        next_major_index = min(
            (marker.line_index for marker in markers if marker.line_index > intro_start),
            default=None,
        )
        if next_major_index is None or intro_start < next_major_index:
            markers.append(
                _HeadingMarker(
                    heading="Introduction",
                    level=1,
                    line_index=intro_start,
                    page_number=lines[intro_start].page_number,
                    explicit=False,
                )
            )

    markers = _dedupe_markers(markers)
    sections: list[ReaderSection] = []

    if not markers:
        sections = _page_window_sections(lines, page_window=page_window)
    else:
        for index, marker in enumerate(markers):
            next_line_index = markers[index + 1].line_index if index + 1 < len(markers) else len(lines)
            content_start = marker.line_index + 1 if marker.explicit else marker.line_index
            content_lines = lines[content_start:next_line_index]
            content_text = _repair_pdf_text("\n".join(line.text for line in content_lines))
            page_end = content_lines[-1].page_number if content_lines else marker.page_number
            sections.append(
                ReaderSection(
                    heading=marker.heading,
                    level=marker.level,
                    page_start=marker.page_number,
                    page_end=page_end,
                    text=content_text,
                )
            )
    _annotate_sections(sections)

    document = ReaderDocument(
        source_path=source_path,
        source_kind="pdf",
        page_count=len(pages_text),
        title=_extract_title(lines, metadata, abstract_index),
        authors=_extract_authors(lines, metadata, abstract_index),
        year=_extract_year(lines, metadata),
        journal=_extract_journal(lines, metadata),
        doi=_extract_doi(lines),
        abstract=abstract_text,
        notes=[],
        sections=sections,
    )
    if not document.title:
        document.notes.append("Title extraction fell back to empty metadata/body heuristics.")
    if not sections:
        document.notes.append("No stable headings were detected; fell back to fixed page windows.")
    return document


def _page_window_sections(lines: Sequence[_DocumentLine], *, page_window: int) -> list[ReaderSection]:
    sections: list[ReaderSection] = []
    if not lines:
        return sections
    max_page = max(line.page_number for line in lines)
    safe_window = max(page_window, 1)
    for start_page in range(1, max_page + 1, safe_window):
        end_page = min(start_page + safe_window - 1, max_page)
        chunk_lines = [line.text for line in lines if start_page <= line.page_number <= end_page]
        sections.append(
            ReaderSection(
                heading=f"Pages {start_page}-{end_page}",
                level=1,
                page_start=start_page,
                page_end=end_page,
                text=_repair_pdf_text("\n".join(chunk_lines)),
            )
        )
    _annotate_sections(sections)
    return sections


def _page_texts_from_pdf_payload(payload: dict[str, Any]) -> list[str]:
    pages = payload.get("pages")
    if isinstance(pages, list) and pages:
        ordered = sorted(
            (
                {
                    "page_number": int(item.get("page_number", 0)),
                    "text": str(item.get("text") or ""),
                }
                for item in pages
                if isinstance(item, dict)
            ),
            key=lambda item: item["page_number"],
        )
        return [item["text"] for item in ordered]
    combined = str(payload.get("text") or "")
    if not combined:
        return []
    chunks = re.split(r"(?m)^\[Page\s+\d+\]\n", combined)
    page_chunks = [chunk.strip() for chunk in chunks if chunk.strip()]
    if page_chunks:
        return page_chunks
    return [combined]


async def _read_pdf_document(task: ReaderTask) -> ReaderDocument:
    from dan.tools.pdf_read import read_pdf_file

    resolved = validate_path(task.source_path)
    payload = await read_pdf_file(
        resolved,
        mode=task.pdf_mode,
        start_page=task.start_page,
        end_page=task.end_page,
        vision_model=task.vision_model or task.model or DEFAULT_VISION_MODEL,
        vision_prompt=task.vision_prompt or None,
    )
    metadata = {
        key: str(value)
        for key, value in dict(payload.get("metadata") or {}).items()
        if value is not None
    }
    pages_text = _page_texts_from_pdf_payload(payload)
    document = _build_sections_from_lines(
        resolved,
        pages_text,
        metadata,
        page_window=task.page_window,
    )
    if task.pdf_mode != "text":
        document.notes.append(f"PDF extraction mode: {task.pdf_mode}.")
    warning = _normalize_text(str(payload.get("warning") or ""))
    if warning:
        document.notes.append(warning)
    return document


def _fallback_sentences(text: str, *, count: int = DEFAULT_SUMMARY_SENTENCE_COUNT) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+", normalized)
    sentences = [part.strip() for part in parts if part.strip()]
    if not sentences:
        return [normalized]
    return sentences[:count]


def _fallback_key_points(text: str, *, count: int = 3) -> list[str]:
    lines = [_normalize_text(line) for line in text.splitlines()]
    candidates = [line for line in lines if line and len(line) > 30]
    if not candidates:
        candidates = _fallback_sentences(text, count=count)
    return candidates[:count]


def _split_text_fragments(text: str, *, max_chars: int) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]
    paragraphs = [paragraph.strip() for paragraph in normalized.split("\n\n") if paragraph.strip()]
    if not paragraphs:
        paragraphs = [normalized]
    fragments: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if current and len(candidate) > max_chars:
            fragments.append(current)
            current = paragraph
            continue
        if len(paragraph) > max_chars:
            sentences = re.split(r"(?<=[.!?])\s+", paragraph)
            for sentence in sentences:
                candidate = f"{current} {sentence}".strip() if current else sentence
                if current and len(candidate) > max_chars:
                    fragments.append(current)
                    current = sentence
                else:
                    current = candidate
            continue
        current = candidate
    if current:
        fragments.append(current)
    return [fragment for fragment in fragments if fragment.strip()]


def _parse_json_payload(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fence_match:
        candidates.insert(0, fence_match.group(1).strip())
    brace_start = raw.find("{")
    brace_end = raw.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        candidates.append(raw[brace_start : brace_end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


async def _complete_json(
    provider: LLMProvider,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
) -> dict[str, Any] | None:
    result = await provider.complete(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model=model,
        temperature=0.0,
        max_tokens=max_tokens,
    )
    return _parse_json_payload(result.text)


async def _summarize_fragment(
    provider: LLMProvider,
    *,
    model: str,
    document: ReaderDocument,
    section: ReaderSection,
    fragment: str,
    fragment_index: int,
    fragment_count: int,
) -> dict[str, Any] | None:
    payload = await _complete_json(
        provider,
        model=model,
        max_tokens=800,
        system_prompt=(
            "Summarize one fragment from a document section. Return JSON only with keys "
            '"summary", "key_points", "quotes", "claims", "keywords", "confidence", '
            '"needs_followup", and "needs_vision". Ground every field in the supplied fragment only. '
            '"summary" should be 2-4 sentences. Keep "key_points" and "claims" to at most 3 items, '
            '"quotes" to at most 2 short verbatim snippets, "keywords" to at most 5 items, and '
            '"confidence" between 0 and 1.'
        ),
        user_prompt=(
            f"Document title: {document.title or '(unknown)'}\n"
            f"Section path: {' > '.join(section.section_path or [section.heading])}\n"
            f"Pages: {section.page_start}-{section.page_end}\n"
            f"Fragment: {fragment_index}/{fragment_count}\n"
            "Return JSON only.\n\n"
            f"{fragment}"
        ),
    )
    return _normalize_summary_packet(payload, section=section)


async def _summarize_section_with_llm(
    provider: LLMProvider,
    *,
    model: str,
    document: ReaderDocument,
    section: ReaderSection,
    max_fragment_chars: int,
) -> dict[str, Any]:
    fragments = _split_text_fragments(section.text, max_chars=max_fragment_chars)
    if not fragments:
        return {}
    if len(fragments) == 1:
        payload = await _complete_json(
            provider,
            model=model,
            max_tokens=1000,
            system_prompt=(
                "Summarize one document section. Return JSON only with keys "
                '"summary", "key_points", "quotes", "claims", "keywords", "confidence", '
                '"needs_followup", and "needs_vision". Ground every field in the supplied section only. '
                '"summary" should be 2-4 sentences. Keep "key_points" and "claims" to at most 3 items, '
                '"quotes" to at most 2 short verbatim snippets, "keywords" to at most 5 items, and '
                '"confidence" between 0 and 1.'
            ),
            user_prompt=(
                f"Document title: {document.title or '(unknown)'}\n"
                f"Section path: {' > '.join(section.section_path or [section.heading])}\n"
                f"Pages: {section.page_start}-{section.page_end}\n"
                f"Child headings: {', '.join(section.child_headings) or '(none)'}\n"
                "Return JSON only.\n\n"
                f"{fragments[0]}"
            ),
        )
        if payload is None:
            raise ValueError("Section summary did not decode as JSON.")
        return _normalize_summary_packet(payload, section=section)

    fragment_payloads = await asyncio.gather(
        *[
            _summarize_fragment(
                provider,
                model=model,
                document=document,
                section=section,
                fragment=fragment,
                fragment_index=index + 1,
                fragment_count=len(fragments),
            )
            for index, fragment in enumerate(fragments)
        ]
    )
    condensed_fragments = []
    for index, payload in enumerate(fragment_payloads, start=1):
        if payload is None:
            continue
        condensed_fragments.append(json.dumps({"fragment_index": index, **payload}, ensure_ascii=False))
    if not condensed_fragments:
        raise ValueError("Fragment summaries did not produce usable output.")
    payload = await _complete_json(
        provider,
        model=model,
        max_tokens=1200,
        system_prompt=(
            "Combine fragment summaries for one document section. Return JSON only with keys "
            '"summary", "key_points", "quotes", "claims", "keywords", "confidence", '
            '"needs_followup", and "needs_vision". Preserve the section hierarchy, core argument, '
            'evidence shape, and any unresolved gaps carried by the fragment packets.'
        ),
        user_prompt=(
            f"Document title: {document.title or '(unknown)'}\n"
            f"Section path: {' > '.join(section.section_path or [section.heading])}\n"
            f"Pages: {section.page_start}-{section.page_end}\n"
            f"Child headings: {', '.join(section.child_headings) or '(none)'}\n"
            "Combine these fragment packets into one section packet. Return JSON only.\n\n"
            + "\n\n".join(condensed_fragments)
        ),
    )
    if payload is None:
        raise ValueError("Combined section summary did not decode as JSON.")
    return _normalize_summary_packet(payload, section=section)


def _fallback_section_summary(section: ReaderSection) -> dict[str, Any]:
    if not section.text.strip() and section.child_headings:
        return {
            "summary": "Structural heading with no direct body text. The substantive content is organized under the child sections below.",
            "key_points": [f"Subsection: {heading}" for heading in section.child_headings[:3]],
            "keywords": [],
            "quotes": [],
            "claims": [f"This section branches into {', '.join(section.child_headings[:3])}."],
            "confidence": 0.45,
            "needs_followup": False,
            "needs_vision": False,
        }
    summary = " ".join(_fallback_sentences(section.text, count=DEFAULT_SUMMARY_SENTENCE_COUNT))
    key_points = _fallback_key_points(section.text)
    return {
        "summary": summary,
        "key_points": key_points,
        "keywords": [],
        "quotes": _default_quotes(section.text, count=2),
        "claims": list(key_points[:3]),
        "confidence": 0.25 if summary else 0.0,
        "needs_followup": not bool(summary or key_points),
        "needs_vision": False,
    }


def _section_is_backmatter(section: ReaderSection) -> bool:
    lowered = section.heading.strip().lower()
    return lowered in {"references", "supporting information", "appendix"}


async def _summarize_document(
    document: ReaderDocument,
    *,
    provider: LLMProvider | None,
    model: str,
    parallelism: int,
    max_fragment_chars: int,
    include_backmatter: bool,
) -> list[str]:
    notes: list[str] = []
    targets = [
        section
        for section in document.sections
        if include_backmatter or not _section_is_backmatter(section)
    ]
    if provider is None or not model:
        notes.append("LLM unavailable; summarize mode used deterministic snippet fallback.")
        for section in targets:
            _apply_summary_packet(section, _fallback_section_summary(section))
        return notes

    semaphore = asyncio.Semaphore(max(parallelism, 1))

    async def worker(section: ReaderSection) -> None:
        if not section.text.strip():
            _apply_summary_packet(section, _fallback_section_summary(section))
            return
        async with semaphore:
            try:
                packet = await _summarize_section_with_llm(
                    provider,
                    model=model,
                    document=document,
                    section=section,
                    max_fragment_chars=max_fragment_chars,
                )
                _apply_summary_packet(section, packet)
                if not section.summary:
                    _apply_summary_packet(section, _fallback_section_summary(section))
            except Exception:
                _apply_summary_packet(section, _fallback_section_summary(section))
                notes.append(f"Fell back to deterministic summary for section '{section.heading}'.")

    await asyncio.gather(*(worker(section) for section in targets))
    return notes


def _latex_escape(text: str) -> str:
    escaped = []
    for char in text:
        escaped.append(_LATEX_ESCAPES.get(char, char))
    return "".join(escaped)


def _section_command(level: int) -> str:
    if level <= 1:
        return "section"
    if level == 2:
        return "subsection"
    return "subsubsection"


def _render_extract_markdown(document: ReaderDocument) -> str:
    lines = [f"# {document.title or Path(document.source_path).name}", ""]
    lines.append(f"- Source: `{document.source_path}`")
    lines.append(f"- Pages: {document.page_count}")
    if document.authors:
        lines.append(f"- Authors: {', '.join(document.authors)}")
    if document.year:
        lines.append(f"- Year: {document.year}")
    if document.journal:
        lines.append(f"- Journal: {document.journal}")
    if document.doi:
        lines.append(f"- DOI: `{document.doi}`")
    lines.append("")
    if document.abstract:
        lines.extend(["## Abstract", "", document.abstract, ""])
    lines.extend(["## Headings", ""])
    for section in document.sections:
        label = " > ".join(section.section_path or [section.heading])
        lines.append(
            f"- `{section.page_start}-{section.page_end}` L{section.level}: {label}"
        )
    if document.notes:
        lines.extend(["", "## Notes", ""])
        lines.extend(f"- {note}" for note in document.notes)
    return "\n".join(lines).rstrip() + "\n"


def _render_extract_latex(document: ReaderDocument) -> str:
    lines = [
        r"\documentclass{article}",
        r"\usepackage[margin=1in]{geometry}",
        r"\begin{document}",
        rf"\section*{{{_latex_escape(document.title or Path(document.source_path).name)}}}",
        r"\begin{itemize}",
        rf"\item Source: \texttt{{{_latex_escape(document.source_path)}}}",
        rf"\item Pages: {document.page_count}",
    ]
    if document.authors:
        lines.append(rf"\item Authors: {_latex_escape(', '.join(document.authors))}")
    if document.year:
        lines.append(rf"\item Year: {_latex_escape(document.year)}")
    if document.journal:
        lines.append(rf"\item Journal: {_latex_escape(document.journal)}")
    if document.doi:
        lines.append(rf"\item DOI: \texttt{{{_latex_escape(document.doi)}}}")
    lines.extend([r"\end{itemize}", ""])
    if document.abstract:
        lines.extend([r"\section*{Abstract}", _latex_escape(document.abstract), ""])
    lines.extend([r"\section*{Headings}", r"\begin{itemize}"])
    for section in document.sections:
        label = " > ".join(section.section_path or [section.heading])
        lines.append(
            rf"\item [{section.page_start}--{section.page_end}] L{section.level}: {_latex_escape(label)}"
        )
    lines.extend([r"\end{itemize}", r"\end{document}", ""])
    return "\n".join(lines)


def _render_summary_markdown(document: ReaderDocument, notes: Sequence[str]) -> str:
    lines = [f"# {document.title or Path(document.source_path).name}", ""]
    lines.append(f"- Source: `{document.source_path}`")
    lines.append(f"- Pages: {document.page_count}")
    if document.authors:
        lines.append(f"- Authors: {', '.join(document.authors)}")
    if document.year:
        lines.append(f"- Year: {document.year}")
    if document.journal:
        lines.append(f"- Journal: {document.journal}")
    if document.doi:
        lines.append(f"- DOI: `{document.doi}`")
    lines.append("")
    if document.abstract:
        lines.extend(["## Abstract", "", document.abstract, ""])
    lines.extend(["## Section Summaries", ""])
    for section in document.sections:
        if not section.summary and not section.key_points:
            continue
        heading_marks = "#" * min(section.level + 2, 6)
        lines.append(
            f"{heading_marks} {section.heading} (pages {section.page_start}-{section.page_end})"
        )
        lines.append("")
        if section.summary:
            lines.append(section.summary)
            lines.append("")
        if section.key_points:
            lines.extend(f"- {item}" for item in section.key_points[:3])
            lines.append("")
        if section.needs_followup or section.needs_vision:
            flags = []
            if section.needs_followup:
                flags.append("needs follow-up")
            if section.needs_vision:
                flags.append("needs vision fallback")
            lines.append(f"_Flags: {', '.join(flags)}_")
            lines.append("")
    all_notes = [*document.notes, *notes]
    if all_notes:
        lines.extend(["## Notes", ""])
        lines.extend(f"- {note}" for note in all_notes)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_summary_latex(document: ReaderDocument, notes: Sequence[str]) -> str:
    lines = [
        r"\documentclass{article}",
        r"\usepackage[margin=1in]{geometry}",
        r"\begin{document}",
        rf"\title{{{_latex_escape(document.title or Path(document.source_path).name)}}}",
    ]
    if document.authors:
        lines.append(rf"\author{{{_latex_escape(', '.join(document.authors))}}}")
    lines.extend([r"\date{}", r"\maketitle", r"\begin{itemize}"])
    lines.append(rf"\item Source: \texttt{{{_latex_escape(document.source_path)}}}")
    lines.append(rf"\item Pages: {document.page_count}")
    if document.year:
        lines.append(rf"\item Year: {_latex_escape(document.year)}")
    if document.journal:
        lines.append(rf"\item Journal: {_latex_escape(document.journal)}")
    if document.doi:
        lines.append(rf"\item DOI: \texttt{{{_latex_escape(document.doi)}}}")
    lines.extend([r"\end{itemize}", ""])
    if document.abstract:
        lines.extend([r"\section*{Abstract}", _latex_escape(document.abstract), ""])
    for section in document.sections:
        if not section.summary and not section.key_points:
            continue
        lines.append(rf"\{_section_command(section.level)}{{{_latex_escape(section.heading)}}}")
        lines.append(rf"\textit{{Pages {section.page_start}--{section.page_end}}}")
        lines.append("")
        if section.summary:
            lines.append(_latex_escape(section.summary))
            lines.append("")
        if section.key_points:
            lines.append(r"\begin{itemize}")
            for item in section.key_points[:3]:
                lines.append(rf"\item {_latex_escape(item)}")
            lines.append(r"\end{itemize}")
            lines.append("")
        if section.needs_followup or section.needs_vision:
            flags = []
            if section.needs_followup:
                flags.append("needs follow-up")
            if section.needs_vision:
                flags.append("needs vision fallback")
            lines.append(rf"\textit{{Flags: {_latex_escape(', '.join(flags))}}}")
            lines.append("")
    all_notes = [*document.notes, *notes]
    if all_notes:
        lines.extend([r"\section*{Notes}", r"\begin{itemize}"])
        for note in all_notes:
            lines.append(rf"\item {_latex_escape(note)}")
        lines.extend([r"\end{itemize}", ""])
    lines.extend([r"\end{document}", ""])
    return "\n".join(lines)


def _render_convert_markdown(document: ReaderDocument) -> str:
    lines = [f"# {document.title or Path(document.source_path).name}", ""]
    if document.authors:
        lines.append(f"_Authors: {', '.join(document.authors)}_")
        lines.append("")
    for section in document.sections:
        heading_marks = "#" * max(min(section.level + 1, 6), 2)
        lines.append(f"{heading_marks} {section.heading}")
        lines.append("")
        lines.append(f"_Pages {section.page_start}-{section.page_end}_")
        lines.append("")
        if section.text:
            lines.append(section.text)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_convert_latex(document: ReaderDocument) -> str:
    lines = [
        r"\documentclass{article}",
        r"\usepackage[margin=1in]{geometry}",
        r"\begin{document}",
        rf"\title{{{_latex_escape(document.title or Path(document.source_path).name)}}}",
    ]
    if document.authors:
        lines.append(rf"\author{{{_latex_escape(', '.join(document.authors))}}}")
    lines.extend([r"\date{}", r"\maketitle", ""])
    if document.abstract:
        lines.extend([r"\section*{Abstract}", _latex_escape(document.abstract), ""])
    for section in document.sections:
        lines.append(rf"\{_section_command(section.level)}{{{_latex_escape(section.heading)}}}")
        lines.append(rf"\textit{{Pages {section.page_start}--{section.page_end}}}")
        lines.append("")
        if section.text:
            paragraphs = [paragraph.strip() for paragraph in section.text.split("\n\n") if paragraph.strip()]
            for paragraph in paragraphs:
                lines.append(_latex_escape(paragraph))
                lines.append("")
    lines.extend([r"\end{document}", ""])
    return "\n".join(lines)


def _document_payload(
    document: ReaderDocument,
    *,
    include_text: bool,
    include_summaries: bool,
) -> dict[str, Any]:
    payload = {
        "source_path": document.source_path,
        "source_kind": document.source_kind,
        "page_count": document.page_count,
        "title": document.title,
        "authors": list(document.authors),
        "year": document.year,
        "journal": document.journal,
        "doi": document.doi,
        "abstract": document.abstract,
        "notes": list(document.notes),
        "sections": [],
    }
    for section in document.sections:
        section_payload = {
            "span_id": section.span_id,
            "heading": section.heading,
            "parent_heading": section.parent_heading,
            "section_path": list(section.section_path),
            "child_headings": list(section.child_headings),
            "level": section.level,
            "page_start": section.page_start,
            "page_end": section.page_end,
            "char_count": len(section.text),
            "has_text": bool(section.text.strip()),
        }
        if include_text:
            section_payload["text"] = section.text
        if include_summaries:
            section_payload["summary"] = section.summary
            section_payload["key_points"] = list(section.key_points)
            section_payload["keywords"] = list(section.keywords)
            section_payload["quotes"] = list(section.quotes)
            section_payload["claims"] = list(section.claims)
            section_payload["confidence"] = section.confidence
            section_payload["needs_followup"] = section.needs_followup
            section_payload["needs_vision"] = section.needs_vision
        payload["sections"].append(section_payload)
    return payload


def _task_payload(task: ReaderTask) -> dict[str, Any]:
    return {
        "action": task.action,
        "source_path": task.source_path,
        "output_format": task.output_format,
        "workspace_root": task.workspace_root,
        "output_path": task.output_path,
        "model": task.model,
        "base_url": task.base_url,
        "api_key_configured": bool(task.api_key),
        "parallelism": task.parallelism,
        "include_backmatter": task.include_backmatter,
        "page_window": task.page_window,
        "max_fragment_chars": task.max_fragment_chars,
        "use_llm": task.use_llm,
        "pdf_mode": task.pdf_mode,
        "vision_model": task.vision_model,
        "vision_prompt_configured": bool(task.vision_prompt),
        "start_page": task.start_page,
        "end_page": task.end_page,
    }


def _render_result(result: ReaderResult) -> str:
    action = result.action
    output_format = result.output_format
    if output_format == "json":
        include_text = action == "convert"
        include_summaries = action == "summarize"
        payload = {
            "action": action,
            "format": output_format,
            "task": _task_payload(result.task),
            "document": _document_payload(
                result.document,
                include_text=include_text,
                include_summaries=include_summaries,
            ),
            "notes": list(result.notes),
        }
        if action == "convert":
            payload["content"] = result.rendered
        return json.dumps(payload, indent=2)
    if action == "extract":
        if output_format == "latex":
            return _render_extract_latex(result.document)
        return _render_extract_markdown(result.document)
    if action == "convert":
        if output_format == "latex":
            return _render_convert_latex(result.document)
        return _render_convert_markdown(result.document)
    if output_format == "latex":
        return _render_summary_latex(result.document, result.notes)
    return _render_summary_markdown(result.document, result.notes)


async def _run_reader_task(task: ReaderTask) -> ReaderResult:
    document = await _read_pdf_document(task)
    notes: list[str] = []

    provider: LLMProvider | None = None
    if task.action == "summarize" and task.use_llm and task.model:
        provider = _build_live_provider(
            task.model,
            api_key=task.api_key or None,
            base_url=task.base_url or None,
        )
    elif task.action == "summarize" and task.use_llm and not task.model:
        notes.append("No model configured; summarize mode used deterministic snippet fallback.")

    if task.action == "summarize":
        summary_notes = await _summarize_document(
            document,
            provider=provider,
            model=task.model,
            parallelism=task.parallelism,
            max_fragment_chars=task.max_fragment_chars,
            include_backmatter=task.include_backmatter,
        )
        notes.extend(summary_notes)

    placeholder = ReaderResult(
        action=task.action,
        output_format=task.output_format,
        task=task,
        document=document,
        rendered="",
        notes=notes,
    )
    rendered = _render_result(placeholder)
    return ReaderResult(
        action=task.action,
        output_format=task.output_format,
        task=task,
        document=document,
        rendered=rendered,
        notes=notes,
    )


def _write_output(text: str, output_path: str) -> None:
    if not output_path:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        return
    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _resolve_task(args: argparse.Namespace) -> ReaderTask:
    if args.parallelism < 1:
        raise SystemExit("--parallelism must be at least 1")
    if args.page_window < 1:
        raise SystemExit("--page-window must be at least 1")
    if args.max_fragment_chars < 1000:
        raise SystemExit("--max-fragment-chars must be at least 1000")
    if args.start_page is not None and args.start_page < 0:
        raise SystemExit("--start-page must be at least 0")
    if args.end_page is not None and args.end_page < 0:
        raise SystemExit("--end-page must be at least 0")
    if (
        args.start_page is not None
        and args.end_page is not None
        and args.end_page <= args.start_page
    ):
        raise SystemExit("--end-page must be greater than --start-page")

    config = resolve_config(
        api_key=args.api_key,
        model=args.model,
        base_url=args.base_url,
        workspace=args.workspace,
    )
    workspace_root = normalize_workspace_root(config["workspace"])
    os.environ["DAN_WORKSPACE_ROOT"] = str(workspace_root)

    return ReaderTask(
        action=args.action,
        source_path=args.source,
        output_format=args.output_format,
        workspace_root=str(workspace_root),
        output_path=args.output,
        model=str(config.get("model") or "").strip(),
        api_key=str(config.get("api_key") or "").strip(),
        base_url=str(config.get("base_url") or "").strip(),
        parallelism=args.parallelism,
        include_backmatter=bool(args.include_backmatter),
        page_window=args.page_window,
        max_fragment_chars=args.max_fragment_chars,
        use_llm=not bool(args.no_llm),
        pdf_mode=str(args.pdf_mode or DEFAULT_PDF_MODE),
        vision_model=str(args.vision_model or config.get("model") or DEFAULT_VISION_MODEL).strip(),
        vision_prompt=str(args.vision_prompt or "").strip(),
        start_page=args.start_page,
        end_page=args.end_page,
    )


def main(argv: Sequence[str] | None = None) -> int:
    load_env()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    task = _resolve_task(args)
    result = asyncio.run(_run_reader_task(task))
    _write_output(result.rendered, task.output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
