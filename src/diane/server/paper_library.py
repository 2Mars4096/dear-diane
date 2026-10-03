"""Read-only Hugo/PDF catalogue and durable, host-wide reading sessions."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

from fastapi import HTTPException

from diane.notes import default_workspace_notes_root, content_bootstrap_root_candidates
from diane._atomic_file import atomic_write_text
from diane.server.paths import resolve_graphs_dir, resolve_workspace_root
from diane.notes_frontmatter import parse_yaml_frontmatter, yaml_list

LOCK = threading.RLock()
_cache: dict[str, tuple[tuple, dict]] = {}


def storage() -> Path:
    return Path(resolve_graphs_dir()) / "papers"


def read_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Paper library state could not be read; existing data was preserved") from exc


def save_json(path: Path, value):
    atomic_write_text(path, json.dumps(value, ensure_ascii=False), mode=0o600)


def settings() -> dict:
    saved = read_json(storage() / "settings.json", None)
    if saved is not None:
        return saved
    notes = default_workspace_notes_root(workspace_root=resolve_workspace_root())
    project = notes.parent if notes.name == "content" else notes.parent.parent if notes.name == "papers" and notes.parent.name == "content" else notes
    candidates = [project, *content_bootstrap_root_candidates(workspace_root=resolve_workspace_root()),
                  *content_bootstrap_root_candidates(cwd=Path(__file__).resolve().parents[3])]
    selected = next((candidate.resolve() for candidate in candidates if (candidate / "content" / "papers").is_dir()), None)
    sources = [{"kind": "hugo", "path": str(selected), "name": selected.name}] if selected else []
    return {"sources": sources}


def safe_child(root: Path, relative: str) -> Path | None:
    target = (root / relative).resolve()
    return target if target.is_relative_to(root.resolve()) else None


def bib_fields(bib: str) -> dict[str, str]:
    """Read brace/quote values with nested TeX braces, retaining the original BibTeX."""
    values = {}
    for match in re.finditer(r"\b([\w-]+)\s*=\s*", bib):
        start = match.end()
        if start >= len(bib):
            continue
        opening = bib[start]
        end = start
        if opening == "{":
            depth = 1
            end += 1
            while end < len(bib) and depth:
                if bib[end] == "{" and bib[end - 1] != "\\": depth += 1
                if bib[end] == "}" and bib[end - 1] != "\\": depth -= 1
                end += 1
            value = bib[start + 1:end - 1]
        elif opening == '"':
            end = start + 1
            while end < len(bib) and (bib[end] != '"' or bib[end - 1] == "\\"): end += 1
            value = bib[start + 1:end]
        else:
            value = re.split(r"[,\n}]", bib[start:], maxsplit=1)[0]
        values[match.group(1).lower()] = re.sub(r"\s+", " ", value.replace("{", "").replace("}", "")).strip()
    return values


def metadata(raw: str) -> tuple[dict, str]:
    match = re.match(r"\ufeff?---\s*\n([\s\S]*?)\n---\s*(?:\n|$)", raw)
    if not match:
        return {}, raw
    header = match.group(1)
    data = parse_yaml_frontmatter(header)
    for block in re.finditer(r"^([\w-]+):\s*([>|])[-+]?\s*\n((?:(?:[ \t]+[^\n]*|)\n?)+?)(?=^[\w-]+:|\Z)", header, re.M):
        value = dedent(block.group(3)).strip()
        data[block.group(1)] = re.sub(r"\s+", " ", value) if block.group(2) == ">" else value
    return data, raw[match.end():]


def paper_id(source: Path, key: str) -> str:
    return hashlib.sha256(f"{source}\0{key}".encode()).hexdigest()[:32]


def catalogue() -> dict:
    papers, warnings, seen = [], [], set()
    for source in settings()["sources"]:
        root = Path(source["path"]).resolve()
        folder = root / "content" / "papers" if source["kind"] == "hugo" else root
        if not folder.is_dir():
            warnings.append(f"Source unavailable: {source['name']} ({folder})")
            continue
        paths = sorted(folder.rglob("*.md")) if source["kind"] == "hugo" else sorted(folder.rglob("*"))
        for path in paths:
            if not path.is_file() or not path.resolve().is_relative_to(root):
                continue
            if source["kind"] == "pdf" and path.suffix.lower() != ".pdf":
                continue
            if path.name == "_index.md":
                continue
            try:
                stat = path.stat()
                stamp = (stat.st_mtime_ns, stat.st_size, source["name"])
                cache_key = str(path)
                cached = _cache.get(cache_key)
                if cached and cached[0] == stamp:
                    paper = dict(cached[1])
                else:
                    if source["kind"] == "hugo":
                        if stat.st_size > 2_000_000:
                            warnings.append(f"Note too large to index: {path.name}")
                            continue
                        data, body = metadata(path.read_text(encoding="utf-8"))
                        bib = str(data.get("bibtex", ""))
                        fields = bib_fields(bib)
                        key_match = re.search(r"@\w+\s*[{(]\s*([^,\s]+)", bib)
                        key = str(data.get("pageID") or (key_match.group(1) if key_match else path.parent.name if path.name == "index.md" else path.stem))
                        pdf_match = re.search(r'{{[<%]\s*paperPDF\b[^}]*?filename\s*=\s*["\']([^"\']+)["\']', body, re.I)
                        pdf = safe_child(root / "static" / "papers", pdf_match.group(1)) if pdf_match else None
                        notes = re.sub(r"{{[<%][\s\S]*?[%>]}}", "", body).strip()
                        paper = {"id": paper_id(root, key), "key": key,
                                 "title": str(data.get("title") or fields.get("title") or key),
                                 "authors": fields.get("author", str(data.get("authors", ""))), "year": fields.get("year", ""),
                                 "journal": fields.get("journal", fields.get("booktitle", "")),
                                 "tags": yaml_list(data.get("tags", [])), "abstract": str(data.get("abstract") or fields.get("abstract", "")),
                                 "bibtex": bib, "notes": notes, "note_path": str(path),
                                 "path": str(pdf) if pdf and pdf.suffix.lower() == ".pdf" else "",
                                 "added": str(data.get("date") or datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat())}
                    else:
                        paper = {"id": paper_id(root, str(path.relative_to(root))), "key": path.stem, "title": path.stem,
                                 "authors": "", "year": "", "journal": "", "tags": [], "abstract": "", "bibtex": "", "notes": "",
                                 "note_path": "", "path": str(path.resolve()), "added": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}
                    paper.update(source=source["name"], source_root=str(root))
                    _cache[cache_key] = (stamp, dict(paper))
                # Recheck links even when metadata was cached, including symlink changes.
                pdf = Path(paper["path"]) if paper["path"] else None
                paper["available"] = bool(pdf and pdf.is_file() and pdf.resolve().is_relative_to(root))
                if paper["id"] in seen:
                    continue
                seen.add(paper["id"])
                papers.append(paper)
            except (OSError, UnicodeError, ValueError):
                warnings.append(f"Could not index {path.name}")
    live = {p["note_path"] or p["path"] for p in papers}
    for key in list(_cache):
        if key not in live: _cache.pop(key, None)
    return {"papers": papers, "warnings": warnings, "settings": settings()}


def find_paper(identifier: str) -> dict:
    paper = next((paper for paper in catalogue()["papers"] if paper["id"] == identifier), None)
    if not paper:
        raise HTTPException(404, "Paper is no longer in the library. Check its source folder.")
    return paper


def session_path(identifier: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise HTTPException(422, "Invalid reading session")
    return storage() / "sessions" / f"{identifier}.json"


def sessions() -> list[dict]:
    result = []
    for path in (storage() / "sessions").glob("*.json"):
        item = read_json(path, {})
        result.append({key: item.get(key) for key in ("id", "title", "paper_id", "last_opened", "position", "pinned", "revision")})
    return sorted(result, key=lambda item: item.get("last_opened") or "", reverse=True)
