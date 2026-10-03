#!/usr/bin/env python3
"""Deterministic Hugo ingestion, adapted from ingest-paper-kb (2026-09-28).

Prepare an entire selected batch before applying. Exclusive writes and verified
copies protect existing papers; the Literature API always keeps source PDFs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


KEY_RE = re.compile(r"^@\s*[A-Za-z]+\s*[({]\s*([^,\s]+)\s*,", re.DOTALL)
SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
SAFE_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class IntakeError(ValueError):
    """A manifest or preflight error that should block the batch."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def yaml_string(value: str) -> str:
    """Return a JSON string, which is also a valid YAML quoted scalar."""
    return json.dumps(value, ensure_ascii=False)


def parse_bibtex_key(bibtex: str) -> str:
    match = KEY_RE.search(bibtex.strip())
    if not match:
        raise IntakeError("could not parse the BibTeX entry type and citation key")
    key = match.group(1).strip()
    if not SAFE_KEY_RE.fullmatch(key):
        raise IntakeError(
            f"BibTeX key {key!r} is unsafe for a Hugo folder/pageID; "
            "use letters, numbers, dot, underscore, colon, or hyphen"
        )
    return key


def bibtex_field(bibtex: str, field: str) -> str:
    """Extract a braced, quoted, or bare BibTeX field without dependencies."""
    match = re.search(
        rf"(?im)(?:^|,)\s*{re.escape(field)}\s*=\s*", bibtex
    )
    if not match:
        return ""
    start = match.end()
    if start >= len(bibtex):
        return ""

    opener = bibtex[start]
    if opener == "{":
        depth = 0
        escaped = False
        for index in range(start, len(bibtex)):
            char = bibtex[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return bibtex[start + 1 : index].strip()
        return ""

    if opener == '"':
        escaped = False
        for index in range(start + 1, len(bibtex)):
            char = bibtex[index]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                return bibtex[start + 1 : index].strip()
        return ""

    end = start
    while end < len(bibtex) and bibtex[end] not in ",\n\r}":
        end += 1
    return bibtex[start:end].strip()


def readable_title(raw: str) -> str:
    title = raw.replace(r"\&", "&")
    title = re.sub(r"[{}]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip()


def validate_source_pdf(path_text: Any) -> Path:
    if not isinstance(path_text, str) or not path_text.strip():
        raise IntakeError("pdf must be a non-empty path string")
    source = Path(path_text).expanduser().absolute()
    if not source.is_file():
        raise IntakeError(f"PDF not found: {source}")
    if source.suffix.lower() != ".pdf":
        raise IntakeError(f"source does not have a .pdf extension: {source}")
    with source.open("rb") as handle:
        header = handle.read(1024)
    if b"%PDF-" not in header:
        raise IntakeError(f"file does not appear to be a PDF: {source}")
    return source


def normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(tag, str) and tag.strip() for tag in value
    ):
        raise IntakeError("tags must be a list of non-empty strings")
    tags: list[str] = []
    for tag in value:
        normalized = tag.strip()
        if normalized not in tags:
            tags.append(normalized)
    return tags


def normalize_journal_code(value: Any) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise IntakeError("journal_code must be a string")
    code = value.strip().lstrip("-")
    if code.lower() == "auto":
        raise IntakeError(
            "journal_code='auto' is not supported; resolve it from project conventions "
            "or omit it when uncertain"
        )
    if not SAFE_CODE_RE.fullmatch(code):
        raise IntakeError(
            f"unsafe journal_code {code!r}; use letters, numbers, dot, underscore, or hyphen"
        )
    return code


def resolve_link(item: dict[str, Any], bibtex: str) -> str:
    explicit = item.get("link")
    if explicit is not None:
        if not isinstance(explicit, str):
            raise IntakeError("link must be a string")
        return explicit.strip()
    url = bibtex_field(bibtex, "url")
    if url:
        return url
    doi = bibtex_field(bibtex, "doi")
    return f"https://doi.org/{doi}" if doi else ""


def validate_notes(value: Any) -> str:
    if value is None or value == "":
        return "## Takeaways\n\n## Q&A"
    if not isinstance(value, str):
        raise IntakeError("notes_markdown must be a string")
    notes = value.strip()
    if notes.startswith("---"):
        raise IntakeError("notes_markdown must contain only the page body, not front matter")
    if re.search(r"(?m)^#\s+", notes):
        raise IntakeError("notes_markdown must start headings at ##, never #")
    return notes


def build_markdown(plan: dict[str, Any], date_value: str) -> str:
    bibtex_lines = "\n".join(
        f"    {line.rstrip()}" for line in plan["bibtex"].strip().splitlines()
    )
    tags_yaml = json.dumps(plan["tags"], ensure_ascii=False)
    return (
        "---\n"
        f"title: {yaml_string(plan['title'])}\n"
        'subtitle: ""\n'
        f"date: {date_value}\n"
        "draft: false\n"
        f"abstract: {yaml_string(plan['abstract'])}\n"
        f"link: {yaml_string(plan['link'])}\n"
        f"pageID: {yaml_string(plan['key'])}\n"
        "bibtex: >\n"
        f"{bibtex_lines}\n"
        f"tags: {tags_yaml}\n"
        "---\n\n"
        f'{{{{< paperPDF filename="{plan["pdf_filename"]}" height="800px" >}}}}\n\n'
        f"{plan['notes_markdown'].rstrip()}\n"
    )


def existing_key_pdfs(pdf_dir: Path, key: str) -> list[Path]:
    exact = pdf_dir / f"{key}.pdf"
    matches = [exact] if exact.is_file() else []
    matches.extend(sorted(path for path in pdf_dir.glob(f"{key}-*.pdf") if path.is_file()))
    return matches


def prepare_plan(
    root: Path, raw_items: list[dict[str, Any]], batch_date: str
) -> tuple[list[dict[str, Any]], list[str]]:
    root = root.resolve()
    pdf_dir = root / "static/papers"
    content_dir = root / "content/papers"
    if any(not directory.is_dir() or not directory.resolve().is_relative_to(root) for directory in (pdf_dir, content_dir)):
        raise IntakeError("Knowledge-base folders must exist within the destination root")
    plans: list[dict[str, Any]] = []
    batch_errors: list[str] = []
    seen_keys: dict[str, int] = {}
    seen_sources: dict[Path, int] = {}

    for number, item in enumerate(raw_items, start=1):
        errors: list[str] = []
        warnings: list[str] = []
        plan: dict[str, Any] = {"number": number, "errors": errors, "warnings": warnings}
        try:
            source = validate_source_pdf(item.get("pdf"))
            bibtex = item.get("bibtex")
            if not isinstance(bibtex, str) or not bibtex.strip():
                raise IntakeError("bibtex must be a non-empty string")
            bibtex = bibtex.strip().replace("\r\n", "\n").replace("\r", "\n")
            key = parse_bibtex_key(bibtex)
            code = normalize_journal_code(item.get("journal_code"))
            title_value = item.get("title")
            if title_value is not None and not isinstance(title_value, str):
                raise IntakeError("title must be a string")
            title = (title_value or readable_title(bibtex_field(bibtex, "title"))).strip()
            if not title:
                raise IntakeError("title is missing from both the item and BibTeX entry")
            abstract_value = item.get("abstract", bibtex_field(bibtex, "abstract"))
            if not isinstance(abstract_value, str):
                raise IntakeError("abstract must be a string")
            date_value = item.get("date", batch_date)
            if not isinstance(date_value, str) or not date_value.strip() or "\n" in date_value:
                raise IntakeError("date must be a non-empty, single-line string")

            requested_filename = f"{key}{'-' + code if code else ''}.pdf"
            requested_dest = pdf_dir / requested_filename
            source_hash = sha256(source)
            existing = existing_key_pdfs(pdf_dir, key)
            same_existing = [path for path in existing if sha256(path) == source_hash]
            different_existing = [path for path in existing if path not in same_existing]

            if different_existing:
                errors.append(
                    "a PDF for this key already exists with different bytes: "
                    + ", ".join(path.name for path in different_existing)
                )

            if len(same_existing) > 1:
                errors.append(
                    "multiple identical PDFs already exist for this key: "
                    + ", ".join(path.name for path in same_existing)
                )
                pdf_dest = requested_dest
                pdf_status = "blocked"
            elif same_existing:
                pdf_dest = same_existing[0]
                pdf_status = "reuse"
                if pdf_dest.name != requested_filename:
                    warnings.append(
                        f"reusing existing {pdf_dest.name} instead of creating {requested_filename}"
                    )
            else:
                pdf_dest = requested_dest
                pdf_status = "create"

            md_dir = content_dir / key
            md_path = md_dir / "index.md"
            if md_dir.is_symlink() or not md_dir.resolve().is_relative_to(content_dir.resolve()):
                raise IntakeError("Paper bundle must stay within content/papers")
            if requested_dest.is_symlink() or any(path.is_symlink() for path in existing):
                raise IntakeError("Existing PDF symlinks cannot be used for ingestion")
            if md_path.exists():
                errors.append(f"Markdown already exists and will not be overwritten: {md_path}")
            elif md_dir.exists() and any(md_dir.iterdir()):
                errors.append(f"paper bundle exists and is non-empty: {md_dir}")

            if key in seen_keys:
                errors.append(f"duplicate BibTeX key in batch; first used by item {seen_keys[key]}")
            else:
                seen_keys[key] = number
            source_identity = source.resolve()
            if source_identity in seen_sources:
                errors.append(
                    f"same source PDF appears twice; first used by item {seen_sources[source_identity]}"
                )
            else:
                seen_sources[source_identity] = number

            plan.update(
                {
                    "source": source,
                    "source_hash": source_hash,
                    "bibtex": bibtex,
                    "key": key,
                    "journal": readable_title(bibtex_field(bibtex, "journal")),
                    "journal_code": code,
                    "title": title,
                    "abstract": abstract_value.strip(),
                    "link": resolve_link(item, bibtex),
                    "tags": normalize_tags(item.get("tags")),
                    "date": date_value.strip(),
                    "notes_markdown": validate_notes(item.get("notes_markdown")),
                    "pdf_filename": pdf_dest.name,
                    "pdf_dest": pdf_dest,
                    "pdf_status": pdf_status,
                    "md_dir": md_dir,
                    "md_path": md_path,
                }
            )
        except (IntakeError, OSError) as exc:
            errors.append(str(exc))
        plans.append(plan)

    for plan in plans:
        for error in plan["errors"]:
            batch_errors.append(f"item {plan['number']}: {error}")
    return plans, batch_errors


def apply_plan(plans: list[dict[str, Any]], copy: bool) -> list[str]:
    created_pdfs: list[Path] = []
    created_markdown: list[Path] = []
    created_dirs: list[Path] = []
    warnings: list[str] = []

    try:
        for plan in plans:
            if plan["pdf_status"] != "create":
                continue
            destination: Path = plan["pdf_dest"]
            temporary = destination.with_name(
                f".{destination.name}.tmp-{uuid.uuid4().hex}"
            )
            try:
                shutil.copy2(plan["source"], temporary)
                if sha256(temporary) != plan["source_hash"]:
                    raise OSError(f"copy verification failed for {destination.name}")
                try:
                    os.link(temporary, destination)
                except FileExistsError as exc:
                    raise OSError(
                        f"destination appeared after preflight; refusing to overwrite: {destination}"
                    ) from exc
            finally:
                if temporary.exists():
                    temporary.unlink()
            created_pdfs.append(destination)

        for plan in plans:
            md_dir: Path = plan["md_dir"]
            if not md_dir.exists():
                md_dir.mkdir(parents=False)
                created_dirs.append(md_dir)
            md_path: Path = plan["md_path"]
            temporary = md_dir / f".index.md.tmp-{uuid.uuid4().hex}"
            try:
                temporary.write_text(
                    build_markdown(plan, plan["date"]), encoding="utf-8"
                )
                try:
                    os.link(temporary, md_path)
                except FileExistsError as exc:
                    raise OSError(
                        f"Markdown appeared after preflight; refusing to overwrite: {md_path}"
                    ) from exc
            finally:
                if temporary.exists():
                    temporary.unlink()
            created_markdown.append(md_path)
    except Exception:
        for path in reversed(created_markdown):
            if path.exists():
                path.unlink()
        for path in reversed(created_pdfs):
            if path.exists():
                path.unlink()
        for path in reversed(created_dirs):
            try:
                path.rmdir()
            except OSError:
                pass
        raise

    if not copy:
        for plan in plans:
            if plan["pdf_status"] not in {"create", "reuse"}:
                continue
            source: Path = plan["source"]
            if source.resolve() == plan["pdf_dest"].resolve():
                continue
            try:
                source.unlink()
            except OSError as exc:
                warnings.append(
                    f"created and verified {plan['pdf_dest']}, but could not remove "
                    f"source {source}: {exc}"
                )
    return warnings
