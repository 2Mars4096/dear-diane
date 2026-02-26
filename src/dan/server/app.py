"""FastAPI application — graph CRUD, run management, and WebSocket events."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dan.builder.decompiler import decompile as decompile_to_python
from dan.engine.executor import EngineConfig
from dan.executors.tool import ToolRegistry
from dan.loader.decompiler import decompile_to_markdown
from dan.models.graph import Graph
from dan.server.chat_manager import ChatManager, compute_graph_revision
from dan.server.chat_store import ChatMessage as StoreChatMessage, ChatStore
from dan.server.graph_mutator import GraphMutator, MutationPlan
from dan.server.graph_store import GraphStore
from dan.server.run_manager import RunManager
from dan.server.scoped_run import (
    ScopedRunRequest,
    ScopedRunResponse,
    build_scoped_graph,
    map_run_event_to_chat_block,
    parse_run_command,
)
from dan.validation.graph import validate_graph

logger = logging.getLogger(__name__)


def _require_run_manager() -> RunManager:
    if _run_manager is None:
        raise HTTPException(status_code=503, detail="Server not fully initialised")
    return _run_manager

_graphs_dir = os.environ.get("DAN_GRAPHS_DIR", "./graphs")
_graph_store = GraphStore(base_dir=_graphs_dir)
_chat_store = ChatStore(base_dir=_graphs_dir)
_run_manager: RunManager | None = None
_chat_manager: ChatManager | None = None


def _get_engine_config() -> EngineConfig:
    from dan.providers import ProviderConfig

    providers: dict[str, ProviderConfig] = {}
    embedding_providers: dict[str, ProviderConfig] = {}

    openai_key = os.environ.get("DAN_OPENAI_API_KEY", "")
    if openai_key:
        providers["openai"] = ProviderConfig(api_key=openai_key)

    anthropic_key = os.environ.get("DAN_ANTHROPIC_API_KEY", "")
    if anthropic_key:
        providers["anthropic"] = ProviderConfig(api_key=anthropic_key)

    google_key = os.environ.get("DAN_GOOGLE_API_KEY", "")
    if google_key:
        providers["google"] = ProviderConfig(api_key=google_key)

    default_embedding_model = os.environ.get(
        "DAN_DEFAULT_EMBEDDING_MODEL", "text-embedding-3-small",
    )
    embedding_api_key = os.environ.get(
        "DAN_EMBEDDING_API_KEY",
        openai_key or os.environ.get("DAN_LLM_API_KEY", os.environ.get("LLM_API_KEY", "")),
    )
    embedding_base_url = os.environ.get(
        "DAN_EMBEDDING_BASE_URL",
        os.environ.get("DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1"),
    )
    if embedding_api_key:
        embedding_providers["default"] = ProviderConfig(
            api_key=embedding_api_key,
            base_url=embedding_base_url,
            default_model=default_embedding_model,
        )

    if os.environ.get("DAN_ENABLE_LOCAL_EMBEDDINGS", "").lower() in ("1", "true", "yes"):
        embedding_providers["local"] = ProviderConfig(
            default_model=os.environ.get("DAN_LOCAL_EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
        )

    return EngineConfig(
        llm_base_url=os.environ.get("DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1"),
        llm_api_key=os.environ.get("DAN_LLM_API_KEY", os.environ.get("LLM_API_KEY", "")),
        llm_default_model=os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6"),
        checkpoint_dir=os.environ.get("DAN_CHECKPOINT_DIR", "./checkpoints"),
        checkpoint_enabled=True,
        providers=providers,
        embedding_providers=embedding_providers,
        default_embedding_model=default_embedding_model,
    )


# -- Built-in tools available to all server-side runs -------------------------

def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


INFORMS3_CLS_URLS = (
    "https://raw.githubusercontent.com/dengfaheng/latex-INFORMS-journals-template/main/ts/informs3.cls",
)

_CITE_PATTERN = re.compile(
    r"\\cite[a-zA-Z*]*\s*(?:\[[^\]]*\]\s*)?(?:\[[^\]]*\]\s*)?\{([^}]*)\}"
)
_BIB_ENTRY_PATTERN = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,", re.IGNORECASE)


def _extract_citation_keys(tex_content: str) -> set[str]:
    keys: set[str] = set()
    for raw in _CITE_PATTERN.findall(tex_content or ""):
        for part in raw.split(","):
            key = part.strip()
            if key:
                keys.add(key)
    return keys


def _extract_bib_keys(bibtex: str) -> set[str]:
    return {m.strip() for m in _BIB_ENTRY_PATTERN.findall(bibtex or "") if m.strip()}


def _build_placeholder_bib_entries(keys: list[str]) -> str:
    entries: list[str] = []
    for key in keys:
        entries.append(
            "\n".join(
                [
                    f"@misc{{{key},",
                    "  author = {Unknown},",
                    f"  title = {{Placeholder reference for {key}}},",
                    "  year = {2024},",
                    "  note = {Auto-generated by compile_latex for missing citation key}",
                    "}",
                ]
            )
        )
    return "\n\n".join(entries)


def _normalize_latex_content(content: str) -> str:
    normalized = content or ""
    normalized = normalized.replace(
        r"\bibliographystyle{informs2014}",
        r"\bibliographystyle{plainnat}",
    )

    if r"\usepackage{hyperref}" not in normalized:
        if r"\usepackage{natbib}" in normalized:
            normalized = normalized.replace(
                r"\usepackage{natbib}",
                r"\usepackage{natbib}" + "\n" + r"\usepackage{hyperref}",
                1,
            )
        elif r"\begin{document}" in normalized:
            normalized = normalized.replace(
                r"\begin{document}",
                r"\usepackage{hyperref}" + "\n" + r"\begin{document}",
                1,
            )

    if r"\providecommand{\newblock}{}" not in normalized:
        if r"\usepackage{hyperref}" in normalized:
            normalized = normalized.replace(
                r"\usepackage{hyperref}",
                r"\usepackage{hyperref}" + "\n" + r"\providecommand{\newblock}{}",
                1,
            )
        elif r"\usepackage{natbib}" in normalized:
            normalized = normalized.replace(
                r"\usepackage{natbib}",
                r"\usepackage{natbib}" + "\n" + r"\providecommand{\newblock}{}",
                1,
            )
        elif r"\begin{document}" in normalized:
            normalized = normalized.replace(
                r"\begin{document}",
                r"\providecommand{\newblock}{}" + "\n" + r"\begin{document}",
                1,
            )
    return normalized


async def _ensure_informs3_cls(output_dir: Path) -> tuple[bool, str]:
    target = output_dir / "informs3.cls"
    if target.exists():
        return True, "informs3.cls found in output/"

    project_copy = Path.cwd() / "informs3.cls"
    if project_copy.exists():
        shutil.copy2(project_copy, target)
        return True, "Copied informs3.cls from project root"

    last_error = ""
    for url in INFORMS3_CLS_URLS:
        try:
            def _download() -> str:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "deep-agent-network/0.1"},
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    return resp.read().decode("utf-8", errors="replace")

            content = await asyncio.to_thread(_download)
            if "informs3" not in content.lower():
                raise RuntimeError("Downloaded content does not look like informs3.cls")
            target.write_text(content, encoding="utf-8")
            return True, f"Downloaded informs3.cls from {url}"
        except Exception as exc:
            last_error = str(exc)

    return False, f"Could not fetch informs3.cls automatically ({last_error})"


def _http_get_json(url: str, *, timeout: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "deep-agent-network/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


async def _run_command(cmd: list[str], cwd: Path) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    out = stdout.decode("utf-8", errors="replace") if stdout else ""
    return proc.returncode, out


async def _save_paper(
    content: str,
    title: str,
    bibtex: str = "",
    pdf_path: str = "",
    compile_log: str = "",
    output_dir: str = "output",
    **kwargs: Any,
) -> dict[str, Any]:
    """Save the final paper as tex/bib/pdf artifacts."""
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)
    slug = _slugify(title)
    tex_path = output_dir / f"{slug}.tex"
    bib_path = output_dir / f"{slug}.bib"
    log_path = output_dir / f"{slug}.compile.log.txt"

    tex_path.write_text(content, encoding="utf-8")
    bib_path.write_text(bibtex, encoding="utf-8")
    log_path.write_text(compile_log or "", encoding="utf-8")

    final_pdf_path = output_dir / f"{slug}.pdf"
    if pdf_path and Path(pdf_path).exists():
        source = Path(pdf_path)
        if source.resolve() != final_pdf_path.resolve():
            shutil.copy2(source, final_pdf_path)

    saved_path = str(final_pdf_path) if final_pdf_path.exists() else str(tex_path)
    return {
        "saved_path": saved_path,
        "title": title,
        "tex_path": str(tex_path),
        "bib_path": str(bib_path),
        "pdf_path": str(final_pdf_path) if final_pdf_path.exists() else "",
        "compile_log_path": str(log_path),
    }


async def _search_papers(
    query: str,
    num_results: int = 8,
    aspect: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    fields = "title,year,venue,authors,citationCount,externalIds,paperId,abstract"
    params = urllib.parse.urlencode(
        {"query": query, "limit": max(1, min(int(num_results), 20)), "fields": fields}
    )
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?{params}"
    papers: list[dict[str, Any]] = []
    error_text = ""
    try:
        data = await asyncio.to_thread(_http_get_json, url)
        for row in data.get("data", []):
            if not isinstance(row, dict):
                continue
            ext = row.get("externalIds") or {}
            if not isinstance(ext, dict):
                ext = {}
            papers.append(
                {
                    "title": str(row.get("title", "")),
                    "year": row.get("year"),
                    "venue": str(row.get("venue", "")),
                    "abstract": str(row.get("abstract", "")),
                    "citation_count": int(row.get("citationCount", 0) or 0),
                    "paper_id": str(row.get("paperId", "")),
                    "doi": str(ext.get("DOI", "")),
                }
            )
    except Exception as exc:
        error_text = str(exc)

    return {
        "aspect": aspect,
        "query": query,
        "papers": papers,
        "paper_count": len(papers),
        "error": error_text,
    }


async def _citation_verifier(key_papers: list[Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    papers = key_papers if isinstance(key_papers, list) else []
    verified: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for row in papers:
        if not isinstance(row, dict):
            invalid.append({"paper": str(row), "reason": "Not an object"})
            continue
        title = str(row.get("title", "")).strip()
        if not title:
            invalid.append({"paper": row, "reason": "Missing title"})
            continue
        key = re.sub(r"\s+", " ", title.lower())
        if key in seen_titles:
            continue
        seen_titles.add(key)
        doi = str(row.get("doi", "")).strip()
        paper_id = str(row.get("paper_id", "")).strip() or str(row.get("paperId", "")).strip()
        if doi or paper_id:
            verified.append(
                {
                    "title": title,
                    "year": row.get("year"),
                    "venue": row.get("venue", ""),
                    "doi": doi,
                    "paper_id": paper_id,
                }
            )
        else:
            invalid.append({"paper": row, "reason": "Missing DOI/paper_id"})
    return {
        "verified_papers": verified,
        "invalid_citations": invalid,
        "verification_notes": f"Verified {len(verified)}; invalid {len(invalid)}.",
    }


async def _check_latex_deps(template_dir: str = "output", **kwargs: Any) -> dict[str, Any]:
    missing_cmds = [cmd for cmd in ("pdflatex", "bibtex") if shutil.which(cmd) is None]
    template_candidates = [Path.cwd(), Path(template_dir)]
    has_cls = any((p / "informs3.cls").exists() for p in template_candidates)
    missing_templates = []
    if not has_cls:
        missing_templates.append("informs3.cls")
    # informs3.cls can be auto-fetched during compile_latex(), so dependency
    # readiness here only requires TeX commands.
    deps_ok = not missing_cmds
    msg = ""
    if missing_cmds:
        msg = (
            f"Missing commands={missing_cmds}. Install TeX binaries first."
        )
    elif missing_templates:
        msg = (
            "informs3.cls not found locally. compile_latex will try to download it "
            "into output/ automatically."
        )
    return {
        "deps_ok": deps_ok,
        "missing_commands": missing_cmds,
        "missing_templates": missing_templates,
        "dependency_message": msg or "LaTeX dependencies are ready.",
    }


async def _compile_latex(
    content: str,
    title: str,
    bibtex: str = "",
    output_dir: str = "output",
    **kwargs: Any,
) -> dict[str, Any]:
    out_dir = Path(output_dir)
    out_dir.mkdir(exist_ok=True, parents=True)
    slug = _slugify(title)
    tex_filename = f"{slug}.tex"
    tex_path = out_dir / tex_filename
    bib_path = out_dir / f"{slug}.bib"
    references_bib = out_dir / "references.bib"

    notes: list[str] = []

    template_ok, template_note = await _ensure_informs3_cls(out_dir)
    notes.append(template_note)
    if not template_ok:
        notes.append(
            "Compile may fail if informs3.cls is unavailable in your TeX tree."
        )

    normalized_content = _normalize_latex_content(content)
    tex_path.write_text(normalized_content, encoding="utf-8")

    cited_keys = _extract_citation_keys(normalized_content)
    existing_keys = _extract_bib_keys(bibtex)
    missing_keys = sorted(cited_keys - existing_keys)

    effective_bibtex = bibtex.strip()
    if missing_keys:
        placeholders = _build_placeholder_bib_entries(missing_keys)
        effective_bibtex = (
            f"{effective_bibtex}\n\n{placeholders}" if effective_bibtex else placeholders
        )
        notes.append(
            "Auto-added placeholder BibTeX entries for missing keys: "
            + ", ".join(missing_keys[:20])
            + (" ..." if len(missing_keys) > 20 else "")
        )

    if effective_bibtex:
        bib_path.write_text(effective_bibtex, encoding="utf-8")
        references_bib.write_text(effective_bibtex, encoding="utf-8")

    if shutil.which("pdflatex") is None:
        return {
            "compile_success": False,
            "pdf_path": "",
            "compile_log": "\n".join(notes + ["pdflatex not found."]),
            "tex_path": str(tex_path),
            "bib_path": str(bib_path) if bib_path.exists() else "",
        }

    logs: list[str] = []
    success = True
    rc, out = await _run_command(
        ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex_filename],
        out_dir,
    )
    logs.append(out)
    if rc != 0:
        success = False
    if success and bibtex and shutil.which("bibtex") is not None:
        rc, out = await _run_command(["bibtex", slug], out_dir)
        logs.append(out)
        if rc != 0:
            success = False
    if success:
        rc, out = await _run_command(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex_filename],
            out_dir,
        )
        logs.append(out)
        if rc != 0:
            success = False
    if success:
        rc, out = await _run_command(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex_filename],
            out_dir,
        )
        logs.append(out)
        if rc != 0:
            success = False

    pdf_path = out_dir / f"{slug}.pdf"
    if not pdf_path.exists():
        success = False

    return {
        "compile_success": success,
        "pdf_path": str(pdf_path) if pdf_path.exists() else "",
        "compile_log": ("\n".join(notes) + "\n\n" + "\n\n".join(logs))[-20000:],
        "tex_path": str(tex_path),
        "bib_path": str(bib_path) if bib_path.exists() else "",
        "autofilled_bib_keys": missing_keys,
    }


async def _package_submission(
    title: str,
    tex_path: str,
    bib_path: str,
    pdf_path: str = "",
    compile_log_path: str = "",
    output_dir: str = "output",
    **kwargs: Any,
) -> dict[str, Any]:
    out_dir = Path(output_dir)
    out_dir.mkdir(exist_ok=True, parents=True)
    slug = _slugify(title)
    bundle = out_dir / f"{slug}-submission.zip"
    manifest = {
        "title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": [],
    }
    files = [tex_path, bib_path, pdf_path, compile_log_path]
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fp in files:
            if not fp:
                continue
            p = Path(fp)
            if not p.exists():
                continue
            zf.write(p, arcname=p.name)
            manifest["files"].append(p.name)
        manifest_path = out_dir / f"{slug}.manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        zf.write(manifest_path, arcname=manifest_path.name)
    return {"bundle_path": str(bundle), "saved_path": str(bundle), "title": title}


async def _run_strategy_script(
    code: str = "",
    params: dict | None = None,
    start_year: int = 2008,
    end_year: int = 2024,
    data_dir: str = "",
    strategy_name: str = "custom",
    return_series: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute strategy code (script-as-param), validate factor format, run backtest.

    The code must define: def build_factor(crsp_path, start_year, end_year, **params) -> pd.DataFrame
    Factor DataFrame must have columns: date, permno, ret, and factor or mom.
    """
    if not code or not code.strip():
        return {"quintiles": [], "error": "No strategy code provided"}

    project_root = Path(__file__).resolve().parents[3]
    vibe_root = project_root / "examples" / "vibe_research_md"
    for p in (project_root, vibe_root):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    try:
        from examples.vibe_research_md.quant_lib.config import CRSP_PATH, DATA_DIR
        from examples.vibe_research_md.quant_lib.load_crsp import load_crsp
        from examples.vibe_research_md.quant_lib.factor_schema import validate_factor_df
        from examples.vibe_research_md.quant_lib.backtest import run_backtest_from_factor_df
        import pandas as pd
        import numpy as np
    except ImportError as e:
        return {"quintiles": [], "error": f"quant_lib import failed: {e}"}

    crsp_path = str(DATA_DIR / "crsp_security_month_returns.csv.gz")
    if data_dir:
        crsp_path = str(Path(data_dir) / "crsp_security_month_returns.csv.gz")
    params = params or {}

    namespace = {
        "pd": pd,
        "np": np,
        "Path": Path,
        "load_crsp": load_crsp,
        "CRSP_PATH": crsp_path,
        "build_factor": None,
    }

    try:
        exec(code, namespace)
    except Exception as e:
        return {"quintiles": [], "error": f"Strategy script failed to compile/run: {e}"}

    build_factor = namespace.get("build_factor")
    if not callable(build_factor):
        return {"quintiles": [], "error": "Code must define build_factor(crsp_path, start_year, end_year, **params) -> pd.DataFrame"}

    try:
        factor_df = build_factor(crsp_path, start_year, end_year, **params)
    except Exception as e:
        return {"quintiles": [], "error": f"build_factor failed: {e}"}

    valid, err = validate_factor_df(factor_df)
    if not valid:
        return {"quintiles": [], "error": f"Invalid factor format: {err}"}

    out = run_backtest_from_factor_df(
        factor_df, return_series=return_series, strategy_name=strategy_name
    )
    out["result"] = out
    return out


async def _get_department_state(**kwargs: Any) -> dict[str, Any]:
    """Load department state (active, deleted departments)."""
    project_root = Path(__file__).resolve().parents[3]
    state_path = project_root / "examples" / "vibe_research_md" / "output" / "department_state.json"
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            return {"active": data.get("active", []), "deleted": data.get("deleted", []), "max_departments": data.get("max_departments", 6)}
        except (json.JSONDecodeError, OSError):
            pass
    return {"active": [], "deleted": [], "max_departments": 6}


async def _update_department_state(
    active: list | None = None,
    deleted: list | None = None,
    to_delete: list | None = None,
    to_create: list | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Update department state. Pass to_delete/to_create or active/deleted directly."""
    project_root = Path(__file__).resolve().parents[3]
    state_path = project_root / "examples" / "vibe_research_md" / "output" / "department_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)

    if active is not None and deleted is not None:
        data = {"active": list(active), "deleted": list(deleted), "max_departments": 6}
    else:
        current = {"active": [], "deleted": []}
        if state_path.exists():
            try:
                current = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        if to_delete or to_create:
            to_del = set(to_delete or [])
            to_cre = set(to_create or [])
            new_deleted = list(set(current.get("deleted", [])) | to_del)
            new_active = [d for d in current.get("active", []) if d not in to_del]
            for d in to_cre:
                if d not in new_active and d not in new_deleted and len(new_active) < 6:
                    new_active.append(d)
            data = {"active": new_active, "deleted": new_deleted, "max_departments": 6}
        else:
            data = {**current, "max_departments": 6}
    state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


async def _run_backtest(
    factor: str = "momentum",
    factor_type: str = "",
    start_year: int = 2008,
    end_year: int = 2024,
    data_dir: str = "",
    lookback_months: int = 12,
    skip_months: int = 1,
    return_series: bool = False,
    item: dict | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run factor backtest using quant_lib (CRSP data from auto-quant)."""
    factor = factor_type or factor
    if isinstance(item, dict):
        lookback_months = int(item.get("lookback", lookback_months))
        skip_months = int(item.get("skip", skip_months))
        start_year = int(item.get("start_year", start_year))
        end_year = int(item.get("end_year", end_year))
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "examples" / "vibe_research_md" / "quant_lib" / "run_backtest.py"
    if not script.exists():
        return {"quintiles": [], "error": f"Backtest script not found: {script}"}
    cmd = [
        sys.executable,
        str(script),
        "--factor", factor,
        "--start", str(start_year),
        "--end", str(end_year),
        "--lookback", str(lookback_months),
        "--skip", str(skip_months),
    ]
    if return_series:
        cmd.append("--series")
    if data_dir:
        cmd.extend(["--data-dir", data_dir])
    save_factor_dir = kwargs.get("save_factor_dir") or kwargs.get("out_dir")
    if not save_factor_dir:
        save_factor_dir = project_root / "examples" / "vibe_research_md" / "output" / "factors"
    cmd.extend(["--save-factor-dir", str(save_factor_dir)])
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(project_root),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        out = stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            return {"quintiles": [], "error": stderr.decode("utf-8", errors="replace")[:500]}
        return json.loads(out)
    except asyncio.TimeoutError:
        return {"quintiles": [], "error": "Backtest timed out after 120s"}
    except json.JSONDecodeError as e:
        return {"quintiles": [], "error": f"Invalid JSON output: {e}"}


async def _save_grid_csv(
    results: list | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Write grid_summary.csv from backtest results."""
    project_root = Path(__file__).resolve().parents[3]
    out_path = project_root / "examples" / "vibe_research_md" / "output" / "grid_summary.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results = results or []
    cols = ["strategy", "lookback", "skip", "spread_q5_q1_bps", "n_dates", "error"]
    lines = [",".join(cols)]
    for r in results:
        if isinstance(r, dict):
            row = [
                str(r.get("strategy_name", "")),
                str(r.get("lookback_months", "")),
                str(r.get("skip_months", "")),
                str(r.get("spread_q5_q1_bps", "")),
                str(r.get("n_dates", "")),
                str(r.get("error", "")).replace(",", ";"),
            ]
        else:
            row = ["", "", "", "", "", str(r).replace(",", ";")]
        lines.append(",".join(row))
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return {"csv_path": str(out_path)}


async def _plot_backtest(
    item: dict,
    out_dir: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """Plot cumulative quintile and LS returns for one backtest result."""
    if not item or not isinstance(item, dict):
        return {"saved_path": "", "error": "item is None or not a dict"}
    project_root = Path(__file__).resolve().parents[3]
    default_out = project_root / "examples" / "vibe_research_md" / "output" / "plots"
    out_path = Path(out_dir) if out_dir else default_out
    out_path.mkdir(parents=True, exist_ok=True)
    name = item.get("strategy_name", "backtest")
    plot_file = out_path / f"{name}.png"
    dates = item.get("dates", [])
    cum = item.get("cumulative_quintiles", {})
    ls_cum = item.get("ls_cumulative", [])
    if not dates or not cum or not ls_cum:
        return {"saved_path": "", "error": "Missing dates, cumulative_quintiles, or ls_cumulative"}
    try:
        project_root_str = str(project_root)
        if project_root_str not in sys.path:
            sys.path.insert(0, project_root_str)
        from examples.vibe_research_md.quant_lib.visualize import plot_cumulative_and_ls
        plot_cumulative_and_ls(
            dates=dates,
            cumulative_quintiles=cum,
            ls_cumulative=ls_cum,
            out_path=plot_file,
            title=f"{name} — Quintile & LS Cumulative Returns",
            strategy_name=name,
        )
        if not plot_file.exists():
            return {"saved_path": "", "error": "Plot file was not created"}
        return {"saved_path": str(plot_file)}
    except ImportError as e:
        return {"saved_path": "", "error": f"matplotlib required: {e}. Install with: pip install matplotlib"}
    except Exception as e:
        return {"saved_path": "", "error": str(e)}


def _build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    builtin = registry.register_builtin_tools()
    logger.info("Registered %d built-in tools: %s", len(builtin), builtin)
    # Domain-specific tools override built-ins if they share an ID
    registry.register("save_paper", _save_paper)
    registry.register("search_papers", _search_papers)
    registry.register("citation_verifier", _citation_verifier)
    registry.register("check_latex_deps", _check_latex_deps)
    registry.register("compile_latex", _compile_latex)
    registry.register("package_submission", _package_submission)
    registry.register("run_backtest", _run_backtest)
    registry.register("run_strategy_script", _run_strategy_script)
    registry.register("get_department_state", _get_department_state)
    registry.register("update_department_state", _update_department_state)
    registry.register("plot_backtest", _plot_backtest)
    registry.register("save_grid_csv", _save_grid_csv)
    return registry


def _build_chat_provider_registry():
    from dan.providers import ProviderConfig
    from dan.providers.registry import ProviderRegistry
    from dan.providers.openai_provider import OpenAIProvider

    registry = ProviderRegistry()
    config = _get_engine_config()
    default_config = ProviderConfig(api_key=config.llm_api_key, base_url=config.llm_base_url)
    registry.register("default", OpenAIProvider(default_config))

    for name, pconfig in config.providers.items():
        if name == "default":
            continue
        if name == "anthropic":
            try:
                from dan.providers.anthropic_provider import AnthropicProvider
                registry.register(name, AnthropicProvider(pconfig))
            except ImportError:
                pass
        elif name == "google":
            try:
                from dan.providers.google_provider import GoogleProvider
                registry.register(name, GoogleProvider(pconfig))
            except ImportError:
                pass
        else:
            registry.register(name, OpenAIProvider(pconfig))
    return registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _run_manager, _chat_manager
    _run_manager = RunManager(
        engine_config=_get_engine_config(),
        tool_registry=_build_tool_registry(),
    )
    _chat_manager = ChatManager(
        provider_registry=_build_chat_provider_registry(),
        graph_store=_graph_store,
    )
    yield


app = FastAPI(title="Deep Agent Network", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# Request / response schemas
# ------------------------------------------------------------------


class CreateGraphRequest(BaseModel):
    graph_id: str
    data: dict[str, Any] | None = None


class RunRequest(BaseModel):
    graph_id: str
    inputs: dict[str, Any] | None = None
    run_id: str | None = None


class ResumeRequest(BaseModel):
    graph_id: str


class ChatMessageRequest(BaseModel):
    workflow_id: str
    message: str
    thread_id: str | None = None
    history: list[dict[str, str]] = []
    client_graph_revision: str | None = None


class ApplyMutationRequest(BaseModel):
    mutation_plan: dict[str, Any]


# ------------------------------------------------------------------
# Graph CRUD
# ------------------------------------------------------------------


@app.get("/api/graphs")
async def list_graphs():
    graphs = _graph_store.list_graphs()
    last_opened = _graph_store.get_last_opened()
    return {"graphs": graphs, "last_opened": last_opened}


@app.post("/api/graphs")
async def create_graph(req: CreateGraphRequest):
    if _graph_store.get_graph(req.graph_id) is not None:
        raise HTTPException(status_code=409, detail=f"Graph '{req.graph_id}' already exists")
    data = _graph_store.create_graph(req.graph_id, req.data)
    return {"graph_id": req.graph_id, "data": data}


_gate_migration_enabled = os.environ.get("DAN_GATE_MIGRATION_ENABLED", "").lower() in (
    "1", "true", "yes",
)


@app.get("/api/graphs/{graph_id}")
async def get_graph(graph_id: str):
    data = _graph_store.get_graph(graph_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    if _gate_migration_enabled and isinstance(data, dict):
        from dan.migration.gate_migration import migrate_graph
        data = migrate_graph(data)
    _graph_store.set_last_opened(graph_id)
    return {"graph_id": graph_id, "data": data}


@app.put("/api/graphs/{graph_id}")
async def update_graph(graph_id: str, body: dict[str, Any]):
    _graph_store.save_graph(graph_id, body)
    return {"graph_id": graph_id, "status": "saved"}


@app.delete("/api/graphs/{graph_id}")
async def delete_graph(graph_id: str):
    if not _graph_store.delete_graph(graph_id):
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    return {"graph_id": graph_id, "status": "deleted"}


@app.post("/api/graphs/{graph_id}/apply-mutation")
async def apply_mutation(graph_id: str, req: ApplyMutationRequest):
    """Apply a chat-generated mutation plan to the graph. Returns new graph on success."""
    data = _graph_store.get_graph(graph_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    if _gate_migration_enabled and isinstance(data, dict):
        from dan.migration.gate_migration import migrate_graph
        data = migrate_graph(data)
    try:
        plan = MutationPlan.model_validate(req.mutation_plan)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid mutation plan: {exc}") from exc

    revision = compute_graph_revision(data)
    result = GraphMutator().apply(data, plan, current_revision=revision)

    if not result.success:
        return {
            "success": False,
            "new_graph": None,
            "errors": [e.model_dump() for e in result.errors],
            "stale_plan": result.stale_plan,
        }

    _graph_store.save_graph(graph_id, result.new_graph)
    return {
        "success": True,
        "new_graph": result.new_graph,
        "errors": [],
        "stale_plan": False,
    }


@app.post("/api/graphs/{graph_id}/validate")
async def validate_graph_endpoint(graph_id: str):
    data = _graph_store.get_graph(graph_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    try:
        graph = Graph.model_validate(data)
    except Exception as exc:
        return {"errors": [{"message": f"Graph parse error: {exc}"}], "warnings": []}

    raw_errors = validate_graph(graph)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    for msg in raw_errors:
        entry: dict[str, str] = {"message": msg}
        edge_match = re.search(r"(?:Edge|ContextEdge) '([^']+)'", msg)
        node_match = re.search(r"Node '([^']+)'", msg)
        cycle_match = re.search(r"non-loop node '([^']+)'", msg)
        if edge_match:
            entry["edge_id"] = edge_match.group(1)
        elif node_match:
            entry["node_id"] = node_match.group(1)
        elif cycle_match:
            entry["node_id"] = cycle_match.group(1)
        lower = msg.lower()
        is_warning = any(
            p in lower
            for p in ("schema safety bypassed", "untyped data edge", "deprecated")
        )
        if is_warning:
            warnings.append(entry)
        else:
            errors.append(entry)

    return {"errors": errors, "warnings": warnings}


@app.post("/api/graphs/{graph_id}/nodes/{node_id}/add-boundary-validators")
async def add_boundary_validators(graph_id: str, node_id: str):
    data = _graph_store.get_graph(graph_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    try:
        graph = Graph.model_validate(data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Graph parse error: {exc}")

    target = graph.node_by_id(node_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    if not getattr(target, "external_input_schema", None) and not getattr(target, "external_output_schema", None):
        raise HTTPException(
            status_code=422,
            detail=f"Node '{node_id}' has no external_input_schema or external_output_schema — nothing to validate",
        )

    from dan.validation.boundaries import insert_boundary_validators
    new_graph = insert_boundary_validators(graph, node_id)

    _graph_store.save_graph(graph_id, new_graph.model_dump(mode="json"))
    return {"graph_id": graph_id, "node_id": node_id, "status": "validators_inserted"}


@app.get("/api/graphs/{graph_id}/export/markdown")
async def export_graph_markdown(graph_id: str):
    graph = _graph_store.load_as_model(graph_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    with tempfile.TemporaryDirectory() as tmpdir:
        result = decompile_to_markdown(graph, tmpdir)
        files: list[dict[str, str]] = []
        for p in result.files:
            content = p.read_text(encoding="utf-8")
            files.append({"path": p.name, "content": content})
        diagnostics: list[dict[str, Any]] = [
            {
                "level": d.level,
                "message": d.message,
                "source_file": str(d.source_file) if d.source_file else None,
                "source_line": d.source_line,
                "source_column": d.source_column,
                "hint": d.hint,
            }
            for d in result.diagnostics
        ]
        return {"files": files, "diagnostics": diagnostics}


@app.get("/api/graphs/{graph_id}/export/python")
async def export_graph_python(graph_id: str):
    graph = _graph_store.load_as_model(graph_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    code = decompile_to_python(graph)
    return {"code": code}


# ------------------------------------------------------------------
# RAG collection CRUD
# ------------------------------------------------------------------

_rag_indexer: "Indexer | None" = None
_rag_lock = asyncio.Lock()


async def _get_indexer() -> "Indexer":
    """Lazily create an Indexer backed by the configured embedding provider.

    Uses ``EmbeddingRegistry`` to resolve the provider from ``EngineConfig``,
    honouring both API and local embedding configurations (Option B).
    """
    global _rag_indexer
    if _rag_indexer is not None:
        return _rag_indexer

    async with _rag_lock:
        if _rag_indexer is not None:
            return _rag_indexer

        from dan.rag import build_embedding_registry
        from dan.rag.indexer import Indexer
        from dan.rag.stores import VectorStoreConfig, VectorStoreFactory

        config = _get_engine_config()
        model = config.default_embedding_model

        registry = build_embedding_registry(config)
        provider = registry.resolve(model)

        backend = os.environ.get("DAN_RAG_STORE_BACKEND", "memory")
        persist_dir = os.environ.get("DAN_RAG_PERSIST_DIR", "./rag_data")
        store = VectorStoreFactory.create(
            VectorStoreConfig(backend=backend, persist_directory=persist_dir),
        )
        _rag_indexer = Indexer(
            embedding_provider=provider,
            embedding_model=model,
            store=store,
        )
        return _rag_indexer


class RAGCreateRequest(BaseModel):
    name: str
    documents: list[dict[str, Any]]
    chunking_config: dict[str, Any] | None = None
    embedding_model: str = ""


class RAGAddDocsRequest(BaseModel):
    documents: list[dict[str, Any]]
    chunking_config: dict[str, Any] | None = None
    embedding_model: str = ""


@app.get("/api/rag/collections")
async def list_rag_collections():
    indexer = await _get_indexer()
    names = await indexer.list_indices()
    return {"collections": names}


@app.post("/api/rag/collections")
async def create_rag_collection(req: RAGCreateRequest):
    indexer = await _get_indexer()
    stats = await indexer.create_index(
        name=req.name,
        documents=req.documents,
        chunking_config=req.chunking_config,
        embedding_model=req.embedding_model,
    )
    return stats


@app.get("/api/rag/collections/{name}/stats")
async def rag_collection_stats(name: str):
    indexer = await _get_indexer()
    return await indexer.get_index_stats(name)


@app.post("/api/rag/collections/{name}/documents")
async def add_rag_documents(name: str, req: RAGAddDocsRequest):
    indexer = await _get_indexer()
    chunks_added = await indexer.add_documents(
        name=name,
        documents=req.documents,
        chunking_config=req.chunking_config,
        embedding_model=req.embedding_model,
    )
    return {"name": name, "chunks_added": chunks_added}


@app.delete("/api/rag/collections/{name}")
async def delete_rag_collection(name: str):
    indexer = await _get_indexer()
    await indexer.delete_index(name)
    return {"name": name, "status": "deleted"}


# ------------------------------------------------------------------
# Run management
# ------------------------------------------------------------------


@app.post("/api/runs")
async def start_run(req: RunRequest):
    rm = _require_run_manager()
    graph = _graph_store.load_as_model(req.graph_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{req.graph_id}' not found")
    record = await rm.start_run(
        graph, graph_id=req.graph_id, inputs=req.inputs, run_id=req.run_id,
    )
    return {"run_id": record.run_id, "status": record.status.value}


@app.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: str, req: ResumeRequest):
    rm = _require_run_manager()
    graph = _graph_store.load_as_model(req.graph_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{req.graph_id}' not found")
    record = await rm.resume_run(graph, graph_id=req.graph_id, run_id=run_id)
    return {"run_id": record.run_id, "status": record.status.value}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    rm = _require_run_manager()
    record = rm.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return record.snapshot()


@app.get("/api/runs")
async def list_runs():
    rm = _require_run_manager()
    return {"runs": rm.list_runs()}


@app.post("/api/runs/{run_id}/human-input")
async def submit_human_input(run_id: str, body: dict):
    rm = _require_run_manager()
    request_id = body.get("request_id")
    response = body.get("response", {})
    if not request_id:
        raise HTTPException(400, "request_id is required")
    record = rm.get_run(run_id)
    if record is None:
        raise HTTPException(404, f"Run '{run_id}' not found")
    if isinstance(response, str):
        response = {"response": response}
    ok = rm.submit_human_input(run_id, request_id, response)
    if not ok:
        raise HTTPException(404, f"No pending human-input request '{request_id}'")
    return {"status": "submitted", "request_id": request_id}


@app.post("/api/runs/scoped")
async def start_scoped_run(req: ScopedRunRequest):
    rm = _require_run_manager()
    graph = _graph_store.load_as_model(req.workflow_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{req.workflow_id}' not found")

    result = build_scoped_graph(
        graph, req.scope, req.target_node_id, req.target_subgraph_key, req.inputs,
    )
    if result.error:
        raise HTTPException(status_code=422, detail=result.error.model_dump())

    record = await rm.start_run(
        result.graph, graph_id=req.workflow_id, inputs=req.inputs,
    )
    return ScopedRunResponse(
        run_id=record.run_id,
        status=record.status.value,
        scope=req.scope,
        target=req.target_node_id or req.target_subgraph_key,
    ).model_dump()


# ------------------------------------------------------------------
# WebSocket — live run events
# ------------------------------------------------------------------


@app.websocket("/api/runs/{run_id}/events")
async def run_events_ws(websocket: WebSocket, run_id: str):
    rm = _require_run_manager()
    await websocket.accept()

    queue = rm.subscribe(run_id)
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("WebSocket error for run %s", run_id, exc_info=True)
    finally:
        rm.unsubscribe(run_id, queue)


# ------------------------------------------------------------------
# Chat — message endpoint + WebSocket streaming
# ------------------------------------------------------------------

_chat_streams: dict[str, tuple[asyncio.Queue, float]] = {}
_CHAT_STREAM_TTL_SECONDS = 120.0


def _reap_stale_chat_streams() -> None:
    """Remove chat stream entries older than TTL (guards against leaked queues)."""
    import time
    now = time.monotonic()
    stale = [k for k, (_, ts) in _chat_streams.items() if now - ts > _CHAT_STREAM_TTL_SECONDS]
    for k in stale:
        _chat_streams.pop(k, None)


@app.post("/api/chat/message")
async def chat_message(req: ChatMessageRequest):
    import time

    if _chat_manager is None:
        raise HTTPException(status_code=503, detail="Chat not initialised")

    _reap_stale_chat_streams()

    run_cmd = parse_run_command(req.message)
    if run_cmd is not None:
        return await _handle_run_command(req, run_cmd)

    stream_channel_id = f"chat-{uuid.uuid4().hex[:10]}"
    queue: asyncio.Queue = asyncio.Queue()
    _chat_streams[stream_channel_id] = (queue, time.monotonic())

    async def _produce():
        try:
            graph_dict = _graph_store.get_graph(req.workflow_id)
            use_tools = graph_dict is not None
            send = (
                _chat_manager.send_message_with_tools
                if use_tools
                else _chat_manager.send_message
            )
            async for event in send(
                workflow_id=req.workflow_id,
                message=req.message,
                history=req.history,
                thread_id=req.thread_id,
                client_graph_revision=req.client_graph_revision,
            ):
                await queue.put(event.model_dump())
        except Exception as exc:
            await queue.put({"type": "chat_error", "error": str(exc)})
        finally:
            await queue.put(None)

    asyncio.create_task(_produce())
    return {"message_id": uuid.uuid4().hex[:12], "stream_channel_id": stream_channel_id}


async def _handle_run_command(
    req: ChatMessageRequest, run_cmd: dict[str, Any],
) -> dict[str, Any]:
    """Execute a /run chat command and return immediate response."""
    rm = _require_run_manager()
    graph = _graph_store.load_as_model(req.workflow_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{req.workflow_id}' not found")

    scope = run_cmd.get("scope", "full")
    result = build_scoped_graph(
        graph,
        scope,
        run_cmd.get("target_node_id"),
        run_cmd.get("target_subgraph_key"),
    )
    if result.error:
        return {
            "type": "run_error",
            "error": result.error.model_dump(),
            "message_id": uuid.uuid4().hex[:12],
        }

    record = await rm.start_run(
        result.graph, graph_id=req.workflow_id, inputs=run_cmd.get("inputs"),
    )
    return {
        "type": "run_started",
        "run_id": record.run_id,
        "scope": scope,
        "target": run_cmd.get("target_node_id") or run_cmd.get("target_subgraph_key"),
        "message_id": uuid.uuid4().hex[:12],
    }


@app.websocket("/api/chat/{channel_id}/events")
async def chat_events_ws(websocket: WebSocket, channel_id: str):
    entry = _chat_streams.get(channel_id)
    if entry is None:
        await websocket.close(code=4004)
        return
    queue, _ = entry
    await websocket.accept()
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("Chat WebSocket error for channel %s", channel_id, exc_info=True)
    finally:
        _chat_streams.pop(channel_id, None)


# ------------------------------------------------------------------
# Chat thread CRUD
# ------------------------------------------------------------------


@app.get("/api/chats/{workflow_id}")
async def list_chat_threads(workflow_id: str):
    return {"threads": _chat_store.list_threads(workflow_id)}


@app.get("/api/chats/{workflow_id}/{thread_id}")
async def get_chat_thread(workflow_id: str, thread_id: str):
    thread = _chat_store.get_thread(workflow_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread.model_dump(mode="json")


@app.post("/api/chats/{workflow_id}")
async def create_chat_thread(workflow_id: str, body: dict[str, Any] | None = None):
    title = (body or {}).get("title", "")
    thread = _chat_store.create_thread(workflow_id, title=title)
    return thread.model_dump(mode="json")


@app.put("/api/chats/{workflow_id}/{thread_id}")
async def update_chat_thread(workflow_id: str, thread_id: str, body: dict[str, Any]):
    thread = _chat_store.get_thread(workflow_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    if "title" in body:
        thread.title = body["title"]
    if "messages" in body:
        thread.messages = [
            StoreChatMessage.model_validate(m) for m in body["messages"]
        ]
    thread.updated_at = datetime.now(timezone.utc)
    _chat_store.save_thread(thread)
    return {"status": "updated"}


@app.delete("/api/chats/{workflow_id}/{thread_id}")
async def delete_chat_thread(workflow_id: str, thread_id: str):
    if not _chat_store.delete_thread(workflow_id, thread_id):
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"status": "deleted"}


# ------------------------------------------------------------------
# Static file serving for the built editor (production)
# ------------------------------------------------------------------

_editor_dist = os.path.join(os.path.dirname(__file__), "..", "..", "..", "editor", "dist")
if os.path.isdir(_editor_dist):
    app.mount("/", StaticFiles(directory=_editor_dist, html=True), name="editor")
