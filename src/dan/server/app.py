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
import time
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

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
from dan.server.chat_manager import (
    ChatManager,
    build_debug_context,
    compute_graph_revision,
    detect_chat_mode,
    normalize_chat_mode,
)
from dan.server.mention_resolver import MentionRef, MentionResolver, CodeResolver
from dan.server.chat_store import ChatMessage as StoreChatMessage, ChatStore
from dan.server.exec import execute_python
from dan.server.graph_mutator import GraphMutator, MutationPlan
from dan.server.graph_store import GraphStore
from dan.server.run_manager import RunManager, RunStatus
from dan.server.run_store import RunStore
from dan.server.test_cases import NodeTestCase, TestCaseRunResult, TestCaseStore
from dan.server.scoped_run import (
    ScopedRunRequest,
    ScopedRunResponse,
    build_scoped_graph,
    map_run_event_to_chat_block,
    parse_run_command,
)
from dan.server.mutation_metrics import mutation_metrics
from dan.validation.graph import validate_graph

logger = logging.getLogger(__name__)

_STRICT_MUTATION_VALIDATION = os.environ.get("DAN_STRICT_MUTATION_VALIDATION", "true").lower() == "true"
_MUTATION_AUTO_RETRY = os.environ.get("DAN_MUTATION_AUTO_RETRY", "true").lower() == "true"


def _require_run_manager() -> RunManager:
    if _run_manager is None:
        raise HTTPException(status_code=503, detail="Server not fully initialised")
    return _run_manager


def _resolve_cache_dir(config: EngineConfig) -> Path:
    if config.cache_dir:
        return Path(config.cache_dir).expanduser()
    return Path.home() / ".dan" / "cache"

_graphs_dir = os.environ.get("DAN_GRAPHS_DIR", "./graphs")
_graph_store = GraphStore(base_dir=_graphs_dir)
_chat_store = ChatStore(base_dir=_graphs_dir)
_test_case_store = TestCaseStore(base_dir=_graphs_dir)
_run_manager: RunManager | None = None
_chat_manager: ChatManager | None = None
_meta_tasks: dict[str, asyncio.Task[Any]] = {}
_meta_subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
_experience_index_cache: Any | None = None
_experience_index_bootstrap_done = False


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
        cache_enabled=os.environ.get("DAN_CACHE_ENABLED", "true").lower() in ("1", "true", "yes"),
        cache_max_size_mb=int(os.environ.get("DAN_CACHE_MAX_SIZE_MB", "100")),
        cache_dir=os.environ.get("DAN_CACHE_DIR") or None,
        semantic_cache_threshold=float(os.environ.get("DAN_SEMANTIC_CACHE_THRESHOLD", "0.95")),
        semantic_cache_ttl_hours=float(os.environ.get("DAN_SEMANTIC_CACHE_TTL_HOURS", "24")),
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
    safe_strategy_name = "".join(
        ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in str(strategy_name or "custom")
    )
    script_saved_path = ""
    script_save_error = ""

    def _attach_script_metadata(payload: dict[str, Any]) -> dict[str, Any]:
        if script_saved_path:
            payload["script_saved_path"] = script_saved_path
        if script_save_error:
            payload["script_save_error"] = script_save_error
        return payload

    for p in (project_root, vibe_root):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    # Save generated strategy script before execution for reproducibility.
    try:
        save_script_dir = kwargs.get("save_script_dir") or kwargs.get("script_dir")
        if not save_script_dir:
            save_script_dir = project_root / "examples" / "vibe_research_md" / "output" / "scripts"
        script_dir = Path(save_script_dir)
        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / f"{safe_strategy_name}.py"
        script_path.write_text(str(code), encoding="utf-8")
        script_saved_path = str(script_path)
    except Exception as e:
        script_save_error = str(e)

    try:
        from examples.vibe_research_md.quant_lib.config import CRSP_PATH, DATA_DIR
        from examples.vibe_research_md.quant_lib.load_crsp import load_crsp
        from examples.vibe_research_md.quant_lib.load_compustat import load_compustat
        from examples.vibe_research_md.quant_lib.factor_schema import validate_factor_df
        from examples.vibe_research_md.quant_lib.backtest import run_backtest_from_factor_df
        import pandas as pd
        import numpy as np
    except ImportError as e:
        return _attach_script_metadata({"quintiles": [], "error": f"quant_lib import failed: {e}"})

    def _normalize_compustat_param_aliases(raw: dict[str, Any]) -> dict[str, Any]:
        alias = {
            "at": "atq",
            "ceq": "ceqq",
            "seq": "seqq",
            "sale": "saleq",
            "revt": "revtq",
            "ib": "ibq",
            "ni": "niq",
            "csho": "cshoq",
            "mkvalt": "mkvaltq",
        }
        out = dict(raw)
        for key in ("compustat_book_equity_field", "numerator_field", "denominator_field", "asset_field"):
            val = out.get(key)
            if isinstance(val, str):
                out[key] = alias.get(val.lower(), val)
        return out

    original_merge_asof = pd.merge_asof

    def _normalize_asof_tolerance(val: Any) -> Any:
        """Normalize merge_asof tolerance to timedelta-like when possible.

        Generated scripts sometimes pass `pd.DateOffset(months=...)`, which is not
        accepted by pandas merge_asof with datetime64 keys. Convert to a concrete
        Timedelta approximation so joins proceed instead of hard-failing.
        """
        if val is None:
            return None
        if isinstance(val, pd.DateOffset):
            try:
                base = pd.Timestamp("2000-01-01")
                td = (base + val) - base
                if isinstance(td, pd.Timedelta):
                    return abs(td)
            except Exception:
                pass
            return None
        return val

    def safe_merge_asof(left, right, on, by=None, **kw):
        """merge_asof that auto-sorts and repairs common tolerance incompatibilities."""
        sort_cols = [on]
        if by is not None:
            if isinstance(by, (list, tuple)):
                sort_cols = [on] + list(by)
            else:
                sort_cols = [on, by]
        left = left.sort_values(sort_cols).reset_index(drop=True)
        right = right.sort_values(sort_cols).reset_index(drop=True)

        if "tolerance" in kw:
            tol = _normalize_asof_tolerance(kw.get("tolerance"))
            if tol is None:
                kw.pop("tolerance", None)
            else:
                kw["tolerance"] = tol

        try:
            return original_merge_asof(left, right, on=on, by=by, **kw)
        except Exception as exc:
            # Last-chance compatibility fallback for generated code variance.
            if "tolerance" in kw and "incompatible tolerance" in str(exc).lower():
                kw.pop("tolerance", None)
                return original_merge_asof(left, right, on=on, by=by, **kw)
            raise

    crsp_path = str(DATA_DIR / "crsp_security_month_returns.csv.gz")
    if data_dir:
        crsp_path = str(Path(data_dir) / "crsp_security_month_returns.csv.gz")
    params = _normalize_compustat_param_aliases(params or {})
    raw_min_price = kwargs.get("min_price")
    if raw_min_price is None and isinstance(params, dict):
        raw_min_price = params.get("min_price_filter", params.get("min_price"))
    try:
        min_price = float(raw_min_price) if raw_min_price is not None else 1.0
    except (TypeError, ValueError):
        min_price = 1.0

    namespace = {
        "pd": pd,
        "np": np,
        "Path": Path,
        "load_crsp": load_crsp,
        "load_compustat": load_compustat,
        "safe_merge_asof": safe_merge_asof,
        "CRSP_PATH": crsp_path,
        "build_factor": None,
    }

    pd.merge_asof = safe_merge_asof
    try:
        exec_out = execute_python(code, namespace, include_namespace=True)
        if exec_out.get("error"):
            return _attach_script_metadata({
                "quintiles": [],
                "error": f"Strategy script failed to compile/run: {exec_out['error']}",
            })

        build_factor = exec_out.get("namespace", {}).get("build_factor")
        if not callable(build_factor):
            return _attach_script_metadata({
                "quintiles": [],
                "error": "Code must define build_factor(crsp_path, start_year, end_year, **params) -> pd.DataFrame",
            })

        import contextlib
        import io

        bf_stdout = io.StringIO()
        bf_stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(bf_stdout), contextlib.redirect_stderr(bf_stderr):
                factor_df = build_factor(crsp_path, start_year, end_year, **params)
        except Exception as e:
            script_output = (bf_stdout.getvalue() + "\n" + bf_stderr.getvalue()).strip()
            msg = f"build_factor failed: {e}"
            if script_output:
                msg += f" | script_output: {script_output[:500]}"
            return _attach_script_metadata({"quintiles": [], "error": msg})
    finally:
        pd.merge_asof = original_merge_asof

    build_factor_log = (bf_stdout.getvalue() + "\n" + bf_stderr.getvalue()).strip()
    if factor_df is None or getattr(factor_df, "empty", True):
        msg = "build_factor returned empty factor dataframe"
        if build_factor_log:
            msg += f" | script_output: {build_factor_log[:500]}"
        return _attach_script_metadata({"quintiles": [], "error": msg})

    valid, err = validate_factor_df(factor_df)
    if not valid:
        return _attach_script_metadata({"quintiles": [], "error": f"Invalid factor format: {err}"})

    factor_saved_path = ""
    factor_save_error = ""
    try:
        save_factor_dir = kwargs.get("save_factor_dir") or kwargs.get("out_dir")
        if not save_factor_dir:
            save_factor_dir = project_root / "examples" / "vibe_research_md" / "output" / "factors"
        save_dir = Path(save_factor_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        factor_path = save_dir / f"{safe_strategy_name}.parquet"
        factor_df.to_parquet(factor_path, index=False)
        factor_saved_path = str(factor_path)
    except Exception as e:
        factor_save_error = str(e)

    out = run_backtest_from_factor_df(
        factor_df,
        return_series=return_series,
        strategy_name=strategy_name,
        crsp_path=crsp_path,
        min_price=min_price,
    )
    if build_factor_log:
        out["build_factor_log"] = build_factor_log[:2000]
    out["factor_saved_path"] = factor_saved_path
    if factor_save_error:
        out["factor_save_error"] = factor_save_error
    return _attach_script_metadata(out)


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
    strategy_name = ""
    raw_min_price = kwargs.get("min_price", kwargs.get("min_price_filter", 1.0))
    if isinstance(item, dict):
        lookback_months = int(item.get("lookback", lookback_months))
        skip_months = int(item.get("skip", skip_months))
        start_year = int(item.get("start_year", start_year))
        end_year = int(item.get("end_year", end_year))
        factor = item.get("factor_type", factor)
        strategy_name = str(item.get("name", "")).strip()
        raw_min_price = item.get("min_price_filter", item.get("min_price", raw_min_price))
    try:
        min_price = float(raw_min_price)
    except (TypeError, ValueError):
        min_price = 1.0
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
        "--min-price", str(min_price),
    ]
    if strategy_name:
        cmd.extend(["--name", strategy_name])
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
        payload = json.loads(out)
        if strategy_name and isinstance(payload, dict):
            payload["strategy_name"] = strategy_name
        return payload
    except asyncio.TimeoutError:
        return {"quintiles": [], "error": "Backtest timed out after 120s"}
    except json.JSONDecodeError as e:
        return {"quintiles": [], "error": f"Invalid JSON output: {e}"}


async def _save_grid_csv(
    results: list | None = None,
    input: dict | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Write grid_summary.csv from backtest results. Accepts results array or input (loop output) with result.results.

    DEPRECATED: Prefer run_python(code, results=...) with agent-generated CSV code.
    Kept for backwards compatibility.
    """
    project_root = Path(__file__).resolve().parents[3]
    out_path = project_root / "examples" / "vibe_research_md" / "output" / "grid_summary.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Prefer input when results is empty — workflow_inputs.results often passes [] and shadows loop output
    use_input = (results is None or (isinstance(results, list) and len(results) == 0)) and input
    if use_input:
        obj = input or {}
        # Unwrap: gate passes {"input": composite_output}; governor wraps in {"result": {results, ...}}
        obj = obj.get("input", obj) if isinstance(obj, dict) else obj
        obj = obj.get("result", obj) if isinstance(obj, dict) else obj
        if isinstance(obj, dict) and isinstance(obj.get("results"), list):
            results = list(obj["results"])
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
    return {"csv_path": str(out_path), "results": results}


async def _plot_backtest(
    item: dict,
    out_dir: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """Plot cumulative quintile and LS returns for one backtest result.

    DEPRECATED: Prefer run_python(code, item=..., out_dir=...) with agent-generated
    plotting code. Kept for backwards compatibility.
    """
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


async def _run_python(code: str = "", **context: Any) -> dict[str, Any]:
    """Execute Python code with injected context. Generic tool for agent-generated code.

    Code must assign to `result` or `output` for structured return.
    Context kwargs (e.g. item=..., results=..., out_dir=...) are injected as variables.
    """
    if not code or not code.strip():
        return {"result": None, "stdout": "", "stderr": "", "error": "No code provided"}

    namespace = dict(context)
    out = execute_python(code, namespace)
    return {
        "result": out["result"],
        "stdout": out["stdout"],
        "stderr": out["stderr"],
        "error": out.get("error"),
    }


async def _rag_index_documents(
    pdf_dir: str = "",
    collection: str = "literature",
    glob_pattern: str = "*.pdf",
    **kwargs: Any,
) -> dict[str, Any]:
    """Index PDF documents from a directory into a RAG collection."""
    import os
    from pathlib import Path

    if not pdf_dir:
        return {"error": "pdf_dir is required", "status": "error"}
    workspace = Path(os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd())).resolve()
    resolved = (workspace / pdf_dir).resolve()
    if not resolved.is_relative_to(workspace):
        return {"error": "Path escapes workspace root; use a relative path or copy files into the workspace", "status": "error"}
    if not resolved.is_dir():
        return {"error": f"Directory not found: {pdf_dir}", "status": "error"}

    pdf_files = sorted(resolved.glob(glob_pattern))
    if not pdf_files:
        return {"error": f"No files matching '{glob_pattern}' in {pdf_dir}", "status": "error"}

    documents: list[dict[str, Any]] = []
    for pdf_path in pdf_files:
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(pdf_path))
            text = "\n".join(
                page.extract_text() or "" for page in reader.pages
            )
            documents.append({
                "content": text,
                "metadata": {
                    "source": str(pdf_path.relative_to(resolved)),
                    "filename": pdf_path.name,
                },
            })
        except ImportError:
            return {"error": "pypdf not installed; run: pip install pypdf>=4.0", "status": "error"}
        except Exception as exc:
            logger.warning("Failed to read %s: %s", pdf_path, exc)

    if not documents:
        return {"error": "No documents could be read", "status": "error"}

    try:
        indexer = await _get_indexer()
        await indexer.create_index(
            name=collection,
            documents=[{"text": d["content"], "metadata": d["metadata"]} for d in documents],
        )
    except Exception as exc:
        return {"error": f"Indexing failed: {exc}", "status": "error"}

    return {
        "collection": collection,
        "documents_indexed": len(documents),
        "status": "ok",
    }


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
    registry.register("run_python", _run_python)
    registry.register("get_department_state", _get_department_state)
    registry.register("update_department_state", _update_department_state)

    # Legacy domain-specific tools — replaced by code nodes with inline logic
    # (plot_one.md and write_csv.md are now self-contained code nodes).
    # Kept registered for backward compatibility with older workflows that
    # still reference tool_id: plot_backtest / save_grid_csv.
    # TODO: Remove these registrations once all workflows have migrated to
    # code nodes. Track in docs/plans/7-5-general-tool-design.md.
    _legacy_silent = os.environ.get("DAN_USE_LEGACY_PLOT_CSV", "").strip() == "1"
    registry.register("plot_backtest", _plot_backtest)
    registry.register("save_grid_csv", _save_grid_csv)
    if not _legacy_silent:
        logger.warning(
            "plot_backtest and save_grid_csv tools are deprecated. "
            "Prefer code nodes with inline logic or run_python with "
            "agent-generated code. Set DAN_USE_LEGACY_PLOT_CSV=1 to "
            "suppress this warning.",
        )

    registry.register("rag_index_documents", _rag_index_documents)
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


_mention_resolver: MentionResolver | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _run_manager, _chat_manager, _mention_resolver
    _runs_dir = os.environ.get("DAN_RUNS_DIR", os.path.join(_graphs_dir, "runs"))
    _run_store = RunStore(base_dir=_runs_dir)
    _run_manager = RunManager(
        engine_config=_get_engine_config(),
        tool_registry=_build_tool_registry(),
        run_store=_run_store,
    )
    _mention_resolver = MentionResolver(
        workspace_root=os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd()),
        chat_store=_chat_store,
    )
    _chat_manager = ChatManager(
        provider_registry=_build_chat_provider_registry(),
        graph_store=_graph_store,
        mention_resolver=_mention_resolver,
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
    session_id: str | None = None


class ResumeRequest(BaseModel):
    graph_id: str
    session_id: str | None = None


class ChatMentionRef(BaseModel):
    type: str
    identifier: str


class ChatMessageRequest(BaseModel):
    workflow_id: str
    message: str
    thread_id: str | None = None
    history: list[dict[str, str]] = []
    client_graph_revision: str | None = None
    mode: Literal["ask", "agent", "plan", "debug", "auto", "mutate", "build"] = "agent"
    mentions: list[ChatMentionRef] = []


class ApplyMutationRequest(BaseModel):
    mutation_plan: dict[str, Any]
    idempotency_key: str | None = None
    source: str | None = None


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
_layout_on_load = os.environ.get("DAN_LAYOUT_ON_LOAD", "").lower() in ("1", "true", "yes")


@app.get("/api/graphs/{graph_id}")
async def get_graph(graph_id: str, layout: bool = False):
    """Load graph. If layout=true or DAN_LAYOUT_ON_LOAD=1, apply topological layout to nodes."""
    data = _graph_store.get_graph(graph_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    if _gate_migration_enabled and isinstance(data, dict):
        from dan.migration.gate_migration import migrate_graph
        data = migrate_graph(data)
    if (layout or _layout_on_load) and isinstance(data, dict):
        from dan.server.layout import apply_layout
        data = apply_layout(data)
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


_applied_mutation_keys: set[tuple[str, str]] = set()
_MAX_IDEMPOTENCY_KEYS = 1000


@app.post("/api/graphs/{graph_id}/apply-mutation")
async def apply_mutation(graph_id: str, req: ApplyMutationRequest):
    """Apply a chat-generated mutation plan to the graph. Returns new graph on success."""
    data = _graph_store.get_graph(graph_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    if _gate_migration_enabled and isinstance(data, dict):
        from dan.migration.gate_migration import migrate_graph
        data = migrate_graph(data)

    if req.idempotency_key:
        idem_key = (graph_id, req.idempotency_key)
        if idem_key in _applied_mutation_keys:
            return {
                "success": True,
                "new_graph": data,
                "errors": [],
                "warnings": [],
                "diagnostics": [],
                "stale_plan": False,
                "idempotent_hit": True,
            }

    try:
        plan = MutationPlan.model_validate(req.mutation_plan)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid mutation plan: {exc}") from exc

    revision = compute_graph_revision(data)
    result = GraphMutator().apply(data, plan, current_revision=revision)

    if not result.success:
        mutation_metrics.record_apply(False)
        resp: dict[str, Any] = {
            "success": False,
            "new_graph": None,
            "errors": [e.model_dump() for e in result.errors],
            "stale_plan": result.stale_plan,
        }
        if result.stale_plan:
            resp["message"] = (
                "The workflow has been modified since this plan was created. "
                "Please refresh and try again."
            )
        return resp

    if _STRICT_MUTATION_VALIDATION:
        try:
            graph = Graph.model_validate(result.new_graph)
        except Exception as exc:
            mutation_metrics.record_apply(False)
            mutation_metrics.record_validation(False)
            return {
                "success": False,
                "errors": [{"message": f"Graph parse error: {exc}"}],
                "stale_plan": False,
            }

        raw_errors = validate_graph(graph)
        warnings: list[str] = []
        fatal: list[str] = []
        for msg in raw_errors:
            lower = msg.lower()
            if any(p in lower for p in ("warning", "deprecated", "untyped")):
                warnings.append(msg)
            else:
                fatal.append(msg)

        mutation_metrics.record_validation(len(fatal) == 0)

        if fatal:
            mutation_metrics.record_apply(False)
            return {
                "success": False,
                "new_graph": None,
                "errors": [{"message": msg} for msg in fatal],
                "stale_plan": False,
            }

        mutation_metrics.record_apply(True)
        _graph_store.save_graph(graph_id, result.new_graph)

        if req.idempotency_key:
            _applied_mutation_keys.add((graph_id, req.idempotency_key))
            if len(_applied_mutation_keys) > _MAX_IDEMPOTENCY_KEYS:
                _applied_mutation_keys.pop()

        if req.source == "optimization" and _run_manager is not None:
            _run_manager.emit_optimization_applied(graph_id, {
                "graph_id": graph_id,
                "operations": len(plan.operations),
                "description": plan.description or "",
            })

        return {
            "success": True,
            "new_graph": result.new_graph,
            "errors": [],
            "warnings": warnings,
            "diagnostics": result.diagnostics,
            "stale_plan": False,
        }
    else:
        mutation_metrics.record_apply(True)
        _graph_store.save_graph(graph_id, result.new_graph)

        if req.idempotency_key:
            _applied_mutation_keys.add((graph_id, req.idempotency_key))
            if len(_applied_mutation_keys) > _MAX_IDEMPOTENCY_KEYS:
                _applied_mutation_keys.pop()

        if req.source == "optimization" and _run_manager is not None:
            _run_manager.emit_optimization_applied(graph_id, {
                "graph_id": graph_id,
                "operations": len(plan.operations),
                "description": plan.description or "",
            })

        return {
            "success": True,
            "new_graph": result.new_graph,
            "errors": [],
            "warnings": [],
            "diagnostics": result.diagnostics,
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


@app.get("/api/metrics/mutations")
async def get_mutation_metrics():
    return mutation_metrics.summary()


@app.post("/api/metrics/mutations/reset")
async def reset_mutation_metrics():
    return mutation_metrics.reset()


@app.post("/api/cache/clear")
async def clear_cache():
    config = _get_engine_config()
    base = _resolve_cache_dir(config)
    deleted_files = 0

    if base.exists():
        for pattern in ("*.json", "*.tmp", "**/*.json", "**/*.tmp"):
            for p in base.glob(pattern):
                if not p.is_file():
                    continue
                try:
                    p.unlink()
                    deleted_files += 1
                except OSError:
                    pass

    return {
        "status": "cleared",
        "cache_dir": str(base),
        "deleted_files": deleted_files,
    }


@app.get("/api/cache/stats")
async def cache_stats():
    config = _get_engine_config()
    base = _resolve_cache_dir(config)
    file_count = 0
    total_bytes = 0
    if base.exists():
        for p in base.rglob("*.json"):
            if p.is_file():
                file_count += 1
                try:
                    total_bytes += p.stat().st_size
                except OSError:
                    pass

    latest_run_cache: dict[str, Any] | None = None
    if _run_manager is not None:
        runs = _run_manager.list_runs()
        if runs:
            latest = max(runs, key=lambda r: float(r.get("started_at", 0.0) or 0.0))
            record = _run_manager.get_run(str(latest.get("run_id", "")))
            if record is not None and record.result is not None:
                meta = record.result.metadata
                if isinstance(meta, dict):
                    latest_run_cache = meta.get("__run_cache__")

    return {
        "cache_dir": str(base),
        "cache_enabled": config.cache_enabled,
        "cache_max_size_mb": config.cache_max_size_mb,
        "semantic_cache_threshold": config.semantic_cache_threshold,
        "semantic_cache_ttl_hours": config.semantic_cache_ttl_hours,
        "disk_file_count": file_count,
        "disk_size_bytes": total_bytes,
        "latest_run_cache": latest_run_cache,
    }


@app.get("/api/runs/{run_id}/token-breakdown")
async def token_breakdown(run_id: str):
    """Return per-node token composition breakdown for a completed run."""
    if _run_manager is None:
        raise HTTPException(status_code=503, detail="Run manager not initialized")
    record = _run_manager.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    node_breakdowns: dict[str, Any] = {}
    run_totals = {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost": record.total_cost or 0.0,
    }

    if record.result and isinstance(record.result.metadata, dict):
        cost_snapshot = record.result.metadata.get("__cost_tracker__")
        if isinstance(cost_snapshot, dict):
            node_breakdowns = cost_snapshot.get("node_breakdowns", {})
            for bd in node_breakdowns.values():
                run_totals["total_input_tokens"] += bd.get("total_input_tokens", 0)
                run_totals["total_output_tokens"] += bd.get("total_output_tokens", 0)

    if not node_breakdowns:
        for nid, usage in record.node_usage.items():
            node_breakdowns[nid] = {
                "total_input_tokens": usage.get("prompt_tokens", 0),
                "total_output_tokens": usage.get("completion_tokens", 0),
            }
            run_totals["total_input_tokens"] += usage.get("prompt_tokens", 0)
            run_totals["total_output_tokens"] += usage.get("completion_tokens", 0)

    return {
        "run_id": run_id,
        "nodes": node_breakdowns,
        "run_totals": run_totals,
    }


def _build_token_analysis_context(graph_id: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Extract node config and edge metadata for token waste analysis."""
    graph_data = _graph_store.get_graph(graph_id)
    if graph_data is None:
        return {}, []
    try:
        graph = Graph.model_validate(graph_data)
    except Exception:
        return {}, []

    node_configs: dict[str, dict[str, Any]] = {}
    for node in graph.nodes:
        tools: list[str] = []
        for tool in getattr(node, "tools", []) or []:
            if isinstance(tool, dict):
                fn = tool.get("function", {})
                name = fn.get("name")
                if isinstance(name, str) and name:
                    tools.append(name)
        node_configs[node.id] = {
            "prompt_template": getattr(node, "prompt_template", ""),
            "system_prompt": getattr(node, "system_prompt", ""),
            "input_ports": [p.name for p in getattr(node, "input_ports", [])],
            "tools": tools,
            "jit_tool_loading": bool(getattr(node, "jit_tool_loading", False)),
        }

    graph_edges: list[dict[str, Any]] = []
    for edge in graph.edges:
        graph_edges.append({
            "id": edge.id,
            "edge_type": getattr(edge, "edge_type", ""),
            "source": edge.source_node_id,
            "target": edge.target_node_id,
            "source_port": edge.source_port,
            "target_port": edge.target_port,
            "pass_by_reference": bool(getattr(edge, "pass_by_reference", False)),
        })
    return node_configs, graph_edges


@app.get("/api/runs/{run_id}/optimization-report")
async def optimization_report(run_id: str):
    """Run waste analysis on a completed run and return findings."""
    if _run_manager is None:
        raise HTTPException(status_code=503, detail="Run manager not initialized")
    record = _run_manager.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    from dan.engine.token_optimization import TokenWasteAnalyzer

    node_breakdowns: dict[str, dict[str, int]] = {}
    node_configs, graph_edges = _build_token_analysis_context(record.graph_id)

    if record.result and isinstance(record.result.metadata, dict):
        cost_snap = record.result.metadata.get("__cost_tracker__")
        if isinstance(cost_snap, dict):
            node_breakdowns = cost_snap.get("node_breakdowns", {})

    events_data = [e for e in record.events if isinstance(e, dict)]

    analyzer = TokenWasteAnalyzer()
    report = analyzer.analyze(
        node_breakdowns=node_breakdowns,
        node_configs=node_configs,
        events=events_data,
        graph_edges=graph_edges,
    )
    return {
        "run_id": run_id,
        "report": report.to_dict(),
    }


@app.get("/api/runs/{run_id}/optimization-mutations")
async def optimization_mutations(run_id: str):
    """Generate one-click-apply mutation operations from waste findings."""
    if _run_manager is None:
        raise HTTPException(status_code=503, detail="Run manager not initialized")
    record = _run_manager.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    from dan.engine.token_optimization import OptimizationPlaybook, TokenWasteAnalyzer

    node_breakdowns: dict[str, dict[str, int]] = {}
    node_configs, graph_edges = _build_token_analysis_context(record.graph_id)
    if record.result and isinstance(record.result.metadata, dict):
        cost_snap = record.result.metadata.get("__cost_tracker__")
        if isinstance(cost_snap, dict):
            node_breakdowns = cost_snap.get("node_breakdowns", {})

    events_data = [e for e in record.events if isinstance(e, dict)]

    analyzer = TokenWasteAnalyzer()
    report = analyzer.analyze(
        node_breakdowns=node_breakdowns,
        node_configs=node_configs,
        events=events_data,
        graph_edges=graph_edges,
    )

    approval_mode = getattr(_run_manager.engine_config, "optimization_rule_approval_mode", "always_approve")
    playbook = OptimizationPlaybook(approval_mode=approval_mode)
    mutations = []
    for finding in report.findings:
        mut = playbook.generate_mutation(finding)
        if mut is not None:
            mutation_plan = {
                "apply_mode": "all_or_nothing",
                "description": finding.suggestion,
                "operations": [mut],
            }
            try:
                MutationPlan.model_validate(mutation_plan)
            except Exception:
                continue
            mutations.append({
                "graph_id": record.graph_id,
                "mutation": mut,
                "mutation_plan": mutation_plan,
                "apply_request": {"mutation_plan": mutation_plan, "source": "optimization"},
                "finding": finding.to_dict(),
            })

    return {
        "run_id": run_id,
        "mutations": mutations,
        "count": len(mutations),
    }


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


# -- 13-2: Variable inspector — upstream inputs for a node --------------------


@app.get("/api/graphs/{graph_id}/nodes/{node_id}/inputs")
async def get_node_inputs(graph_id: str, node_id: str, run_id: str | None = None):
    """Return upstream variable descriptors (static wiring + optional runtime values)."""
    data = _graph_store.get_graph(graph_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    try:
        graph = Graph.model_validate(data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Graph parse error: {exc}")

    target = graph.node_by_id(node_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found in graph")

    from dan.server.variable_inspector import compute_upstream_variables

    variables = compute_upstream_variables(node_id, graph)

    # Optionally enrich with runtime values from a specific run
    runtime_values: dict[str, Any] = {}
    if run_id:
        rm = _require_run_manager()
        record = rm.get_run(run_id)
        if record is not None and rm.run_store is not None:
            # Load node_completed events for upstream source nodes to get their outputs
            source_node_ids = {v["source_node_id"] for v in variables if v.get("source_node_id")}
            for src_id in source_node_ids:
                events = rm.run_store.load_events(
                    record.graph_id, run_id, node_id=src_id, event_type="node_completed",
                )
                if events:
                    # Take the last completed event's output
                    last_evt = events[-1]
                    output = last_evt.get("output") or last_evt.get("outputs") or last_evt.get("data", {}).get("output")
                    if output is not None:
                        runtime_values[src_id] = output

    # Attach runtime values to variables
    for var in variables:
        src_id = var.get("source_node_id")
        if src_id and src_id in runtime_values:
            src_output = runtime_values[src_id]
            # Extract the specific port value if output is a dict
            if isinstance(src_output, dict) and var["source_port"] in src_output:
                var["runtime_value"] = src_output[var["source_port"]]
            else:
                var["runtime_value"] = src_output
        else:
            var["runtime_value"] = None

    return {"node_id": node_id, "graph_id": graph_id, "variables": variables}


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
        session_id=req.session_id,
    )
    return {"run_id": record.run_id, "status": record.status.value}


@app.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: str, req: ResumeRequest):
    rm = _require_run_manager()
    graph = _graph_store.load_as_model(req.graph_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{req.graph_id}' not found")
    record = await rm.resume_run(
        graph, graph_id=req.graph_id, run_id=run_id,
        session_id=req.session_id,
    )
    return {"run_id": record.run_id, "status": record.status.value}


# ------------------------------------------------------------------
# Checkpoint portals — partial rerun infrastructure
# ------------------------------------------------------------------


@app.get("/api/runs/{run_id}/checkpoints")
async def list_run_checkpoints(run_id: str):
    """List available checkpoint markers for a run.

    Returns timestamp, completed node count, graph_revision, and
    whether the checkpoint is compatible with the current graph.
    """
    rm = _require_run_manager()
    info = await rm.get_checkpoint_info(run_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"No checkpoint found for run '{run_id}'")

    # If we can determine the graph_id, check staleness against current graph.
    staleness_info: dict[str, Any] = {}
    record = rm.get_run(run_id)
    if record is not None and info.get("graph_revision"):
        graph = _graph_store.load_as_model(record.graph_id)
        if graph is not None:
            from dan.engine.checkpoint import check_checkpoint_staleness
            staleness = check_checkpoint_staleness(
                info["graph_revision"],
                graph,
                info.get("completed_node_ids"),
            )
            staleness_info = staleness.model_dump()

    return {
        "run_id": run_id,
        "checkpoints": [{
            "checkpoint_id": run_id,  # Currently 1 checkpoint per run
            "timestamp": info.get("timestamp"),
            "graph_id": info.get("graph_id", ""),
            "graph_revision": info.get("graph_revision"),
            "completed_node_count": len(info.get("completed_node_ids", [])),
            "has_state": info.get("has_state", False),
            **staleness_info,
        }],
    }


@app.get("/api/runs/{run_id}/checkpoints/{checkpoint_id}")
async def get_checkpoint_detail(run_id: str, checkpoint_id: str):
    """Get detailed checkpoint info: completed_node_ids, node_outputs keys."""
    rm = _require_run_manager()
    info = await rm.get_checkpoint_info(run_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"No checkpoint found for run '{run_id}'")
    return {
        "checkpoint_id": checkpoint_id,
        "run_id": run_id,
        "timestamp": info.get("timestamp"),
        "graph_id": info.get("graph_id", ""),
        "graph_revision": info.get("graph_revision"),
        "completed_node_ids": info.get("completed_node_ids", []),
        "node_output_keys": info.get("node_output_keys", []),
    }


class RerunRequest(BaseModel):
    graph_id: str
    scope_type: Literal["downstream_of", "single_node", "subgraph"]
    target_node_id: str | None = None
    sub_graph_key: str | None = None
    session_id: str | None = None


@app.post("/api/runs/{run_id}/rerun")
async def rerun_from_checkpoint(run_id: str, req: RerunRequest):
    """Start a partial rerun from a checkpoint.

    Accepts a RerunScope body, validates scope against checkpoint,
    and starts a partial rerun. Returns a new run_id for provenance.

    Returns 409 Conflict if the checkpoint is stale (graph changed).
    """
    rm = _require_run_manager()
    graph = _graph_store.load_as_model(req.graph_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{req.graph_id}' not found")

    from dan.engine.checkpoint import RerunScope

    scope = RerunScope(
        scope_type=req.scope_type,
        target_node_id=req.target_node_id,
        sub_graph_key=req.sub_graph_key,
    )

    try:
        record = await rm.rerun_from_checkpoint(
            graph, graph_id=req.graph_id,
            source_run_id=run_id,
            scope=scope,
            session_id=req.session_id,
        )
    except RuntimeError as exc:
        # Stale checkpoint — graph changed since checkpoint was taken.
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {
        "run_id": record.run_id,
        "status": record.status.value,
        "source_run_id": run_id,
        "scope": scope.model_dump(),
    }


@app.get("/api/runs/compare")
async def compare_runs(run_a: str, run_b: str):
    """Align two runs by node execution order and compute per-node diffs."""
    rm = _require_run_manager()
    rec_a = rm.get_run(run_a)
    rec_b = rm.get_run(run_b)
    if rec_a is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_a}' not found")
    if rec_b is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_b}' not found")

    snap_a = rec_a.snapshot()
    snap_b = rec_b.snapshot()

    all_nodes = sorted(
        set(list(snap_a.get("node_statuses", {})) + list(snap_b.get("node_statuses", {})))
    )
    usage_a = snap_a.get("node_usage", {})
    usage_b = snap_b.get("node_usage", {})

    node_diffs: list[dict[str, Any]] = []
    for nid in all_nodes:
        status_a = snap_a.get("node_statuses", {}).get(nid)
        status_b = snap_b.get("node_statuses", {}).get(nid)
        ua = usage_a.get(nid, {})
        ub = usage_b.get(nid, {})
        tok_a = ua.get("total_tokens", 0)
        tok_b = ub.get("total_tokens", 0)
        node_diffs.append(
            {
                "node_id": nid,
                "status_a": status_a,
                "status_b": status_b,
                "status_changed": status_a != status_b,
                "tokens_a": tok_a,
                "tokens_b": tok_b,
                "token_delta": tok_b - tok_a,
                "prompt_tokens_a": ua.get("prompt_tokens", 0),
                "prompt_tokens_b": ub.get("prompt_tokens", 0),
                "completion_tokens_a": ua.get("completion_tokens", 0),
                "completion_tokens_b": ub.get("completion_tokens", 0),
            }
        )

    elapsed_a = snap_a.get("elapsed_seconds") or 0
    elapsed_b = snap_b.get("elapsed_seconds") or 0
    cost_a = snap_a.get("total_cost") or 0
    cost_b = snap_b.get("total_cost") or 0

    return {
        "run_a": snap_a,
        "run_b": snap_b,
        "summary": {
            "elapsed_delta": round(elapsed_b - elapsed_a, 2),
            "token_delta": (snap_b.get("total_tokens", 0) or 0)
            - (snap_a.get("total_tokens", 0) or 0),
            "cost_delta": round(cost_b - cost_a, 6),
            "status_a": snap_a.get("status"),
            "status_b": snap_b.get("status"),
        },
        "node_diffs": node_diffs,
    }


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    rm = _require_run_manager()
    record = rm.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return record.snapshot()


@app.get("/api/runs")
async def list_runs(
    workflow_id: str | None = None,
    status: str | None = None,
    after: float | None = None,
    before: float | None = None,
    limit: int = 100,
    offset: int = 0,
):
    rm = _require_run_manager()
    runs = rm.list_runs()
    if workflow_id:
        runs = [r for r in runs if r.get("graph_id") == workflow_id]
    if status:
        runs = [r for r in runs if r.get("status") == status]
    if after:
        runs = [r for r in runs if (r.get("started_at") or 0) >= after]
    if before:
        runs = [r for r in runs if (r.get("started_at") or 0) <= before]
    runs.sort(key=lambda r: r.get("started_at", 0), reverse=True)
    return {"runs": runs[offset : offset + limit], "total": len(runs)}


@app.get("/api/runs/{run_id}/events")
async def get_run_events(
    run_id: str,
    node_id: str | None = None,
    event_type: str | None = None,
):
    """Load persisted event stream for a completed run (REST, not WS)."""
    rm = _require_run_manager()
    record = rm.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    if record.status in (RunStatus.PENDING, RunStatus.RUNNING):
        return {"events": list(record.events), "source": "live"}
    if rm.run_store is not None:
        events = rm.run_store.load_events(
            record.graph_id, run_id, node_id=node_id, event_type=event_type,
        )
        if events:
            return {"events": events, "source": "persisted"}
    return {"events": list(record.events), "source": "memory"}


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
# Memory REST endpoints (Plan 14-1)
# ------------------------------------------------------------------


_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_path_segment(value: str, name: str) -> str:
    if not _PATH_SEGMENT_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {name}: must be alphanumeric / dash / underscore",
        )
    return value


def _get_memory_store():
    from dan.engine.memory_store import FileSystemMemoryStore
    rm = _require_run_manager()
    memory_dir = rm.engine_config.memory_dir if rm.engine_config else "./memory"
    return FileSystemMemoryStore(memory_dir)


def _get_experience_index():
    global _experience_index_cache
    if _experience_index_cache is not None:
        return _experience_index_cache
    try:
        from dan.engine.experience import ExperienceIndex
        from dan.rag import build_embedding_registry
        from dan.rag.stores import VectorStoreConfig, VectorStoreFactory

        rm = _require_run_manager()
        config = rm.engine_config
        registry = build_embedding_registry(config)
        model = config.default_embedding_model
        provider = registry.resolve(model)
        backend = os.environ.get(
            "DAN_EXPERIENCE_STORE_BACKEND",
            os.environ.get("DAN_RAG_STORE_BACKEND", "memory"),
        )
        persist_dir = os.environ.get("DAN_EXPERIENCE_PERSIST_DIR", "./rag_data")
        vector_store = VectorStoreFactory.create(
            VectorStoreConfig(
                backend=backend,
                persist_directory=persist_dir,
            ),
        )
        _experience_index_cache = ExperienceIndex(
            embedding_provider=provider,
            vector_store=vector_store,
            embedding_model=model,
        )
        return _experience_index_cache
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Experience index unavailable: {exc}",
        ) from exc


def _get_experience_store(*, with_index: bool = False):
    from dan.engine.experience import ExperienceStore

    index = _get_experience_index() if with_index else None
    return ExperienceStore(_get_memory_store(), experience_index=index)


def _build_meta_llm_call(default_model: str | None = None):
    provider_registry = _chat_manager._providers if _chat_manager is not None else _build_chat_provider_registry()
    fallback_model = default_model or os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6")

    async def _llm_call(
        system_prompt: str,
        user_prompt: str,
        model: str | None,
        temperature: float,
    ) -> str:
        model_name = model or fallback_model
        provider = provider_registry.resolve(model_name)
        result = await provider.complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=model_name,
            temperature=temperature,
        )
        return result.text

    return _llm_call


def _build_meta_controller():
    rm = _require_run_manager()
    from dan.meta.controller import MetaController, MetaSessionStore
    from dan.meta.discovery import DiscoveryService
    from dan.meta.planner import WorkflowPlanner
    from dan.meta.repair import (
        RepairActionStore,
        RepairEscalator,
        StructuralRepairPlanner,
    )

    memory_store = _get_memory_store()
    try:
        exp_index = _get_experience_index()
    except HTTPException:
        exp_index = None
    exp_store = _get_experience_store(with_index=exp_index is not None)

    discovery = DiscoveryService(
        experience_index=exp_index,
        experience_store=exp_store,
        graph_store=_graph_store,
        tool_registry=rm.tool_registry,
    )
    llm_call = _build_meta_llm_call(default_model=rm.engine_config.llm_default_model)

    planner = WorkflowPlanner(
        discovery=discovery,
        graph_store=_graph_store,
        llm_call=llm_call,
        model=rm.engine_config.planner_model or rm.engine_config.llm_default_model,
        max_retries=rm.engine_config.planner_max_retries,
        temperature=rm.engine_config.planner_temperature,
        discovery_top_k=rm.engine_config.planner_discovery_top_k,
    )

    structural_planner = StructuralRepairPlanner(
        llm_call=llm_call,
        model=rm.engine_config.repair_model or rm.engine_config.planner_model or rm.engine_config.llm_default_model,
    )
    repair_store = RepairActionStore(memory_store)
    escalator = RepairEscalator(
        structural_planner=structural_planner,
        action_store=repair_store,
        max_attempts_per_level=rm.engine_config.max_repair_attempts_per_level,
        max_redesigns=rm.engine_config.max_redesigns_per_goal,
    )

    session_store = MetaSessionStore(memory_store)

    async def _run_workflow(plan: Any, session_id: str) -> dict[str, Any]:
        exec_result = await planner.execute_plan(plan)
        workflow_id = str(exec_result.get("workflow_id", "")).strip()
        graph_data = exec_result.get("graph")
        if not workflow_id:
            workflow_id = f"meta-{uuid.uuid4().hex[:10]}"
        if not isinstance(graph_data, dict):
            return {
                "success": False,
                "workflow_id": workflow_id,
                "error_context": "Planner execution did not return a graph",
            }

        _graph_store.save_graph(workflow_id, graph_data)

        graph_model = Graph.model_validate(graph_data)
        rec = await rm.start_run(
            graph_model,
            graph_id=workflow_id,
            session_id=session_id,
        )
        while True:
            current = rm.get_run(rec.run_id)
            if current is None:
                return {
                    "success": False,
                    "workflow_id": workflow_id,
                    "error_context": f"Run {rec.run_id} disappeared",
                }
            if current.status in (RunStatus.COMPLETED, RunStatus.FAILED):
                break
            await asyncio.sleep(0.1)

        current = rm.get_run(rec.run_id)
        if current is None:
            return {
                "success": False,
                "workflow_id": workflow_id,
                "error_context": f"Run {rec.run_id} missing after completion",
            }
        snapshot = current.snapshot()
        principle_dicts: list[dict[str, Any]] = []
        ps = rm._get_principle_store()
        if ps is not None:
            try:
                principles = await ps.load_principles(workflow_id)
                principle_dicts = [p.model_dump() for p in principles]
            except Exception:
                logger.debug("Failed loading principles for %s", workflow_id, exc_info=True)
        return {
            "success": bool(snapshot.get("success", False)),
            "run_id": rec.run_id,
            "workflow_id": workflow_id,
            "errors": snapshot.get("errors", {}),
            "error_context": str(snapshot.get("errors", "")),
            "principles": principle_dicts,
            "outputs": snapshot.get("outputs", {}),
        }

    async def _emit_meta_event(event: dict[str, Any]) -> None:
        sid = event.get("session_id", "")
        for queue in _meta_subscribers.get(sid, []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Meta subscriber queue full for session %s", sid)

    controller = MetaController(
        planner=planner,
        repair_escalator=escalator,
        experience_store=exp_store,
        session_store=session_store,
        run_workflow=_run_workflow,
        emit_event=_emit_meta_event,
        graph_loader=_graph_store.get_graph,
        graph_saver=_graph_store.save_graph,
    )
    return controller, planner, session_store


# -- Test Cases CRUD ---------------------------------------------------------


@app.get("/api/test-cases/{workflow_id}/{node_id}")
async def list_test_cases(workflow_id: str, node_id: str):
    """List test cases for a node."""
    _validate_path_segment(workflow_id, "workflow_id")
    cases = _test_case_store.list_cases(workflow_id, node_id)
    return {"cases": [c.model_dump() for c in cases]}


@app.post("/api/test-cases/{workflow_id}/{node_id}")
async def create_or_update_test_case(workflow_id: str, node_id: str, body: dict[str, Any]):
    """Create or update a test case for a node."""
    _validate_path_segment(workflow_id, "workflow_id")
    body.setdefault("node_id", node_id)
    body.setdefault("updated_at", time.time())
    if "id" not in body:
        body["id"] = str(uuid.uuid4())
    if "created_at" not in body:
        body["created_at"] = time.time()
    case = NodeTestCase.model_validate(body)
    _test_case_store.save_case(workflow_id, node_id, case)
    return {"case": case.model_dump()}


@app.delete("/api/test-cases/{workflow_id}/{node_id}/{case_id}")
async def delete_test_case(workflow_id: str, node_id: str, case_id: str):
    """Delete a single test case."""
    _validate_path_segment(workflow_id, "workflow_id")
    if not _test_case_store.delete_case(workflow_id, node_id, case_id):
        raise HTTPException(status_code=404, detail=f"Test case '{case_id}' not found")
    return {"status": "deleted", "case_id": case_id}


@app.post("/api/test-cases/{workflow_id}/{node_id}/{case_id}/run")
async def run_test_case(workflow_id: str, node_id: str, case_id: str):
    """Execute a single test case in isolation and return the result."""
    _validate_path_segment(workflow_id, "workflow_id")
    rm = _require_run_manager()

    # 1. Load the test case
    case = _test_case_store.get_case(workflow_id, node_id, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"Test case '{case_id}' not found")

    # 2. Load the full graph and extract the target node
    graph = _graph_store.load_as_model(workflow_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{workflow_id}' not found")

    target_node = graph.node_by_id(node_id)
    if target_node is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found in graph")

    # 3. Build a synthetic single-node graph
    from dan.models.graph import Graph as GraphModel

    synthetic = GraphModel(
        nodes=[target_node],
        entry_points=[node_id],
        exit_points=[node_id],
    )

    # 4. Execute via RunManager
    run_id = f"test-{case_id}-{int(time.time() * 1000)}"
    record = await rm.start_run(
        synthetic,
        graph_id=workflow_id,
        inputs=case.inputs,
        run_id=run_id,
    )

    # 5. Wait for completion (with timeout)
    deadline = time.time() + 120  # 2 minute timeout
    while True:
        current = rm.get_run(record.run_id)
        if current is None:
            break
        if current.status.value in ("completed", "failed"):
            break
        if time.time() > deadline:
            break
        await asyncio.sleep(0.1)

    # 6. Extract results
    current = rm.get_run(record.run_id)
    actual_outputs: dict[str, Any] = {}
    execution_metadata: dict[str, Any] = {}
    error_msg: str | None = None

    if current and current.result:
        actual_outputs = current.result.outputs or {}
        # Extract the node's outputs if nested
        if node_id in actual_outputs and isinstance(actual_outputs[node_id], dict):
            actual_outputs = actual_outputs[node_id]
        if current.result.errors:
            error_msg = "; ".join(
                f"{k}: {v}" for k, v in current.result.errors.items()
            )
        execution_metadata = {
            "run_id": record.run_id,
            "elapsed_seconds": current.elapsed_seconds,
            "total_tokens": current.total_tokens,
            "total_cost": current.total_cost,
            "model": current.model,
        }

    # 7. Compare against expected outputs
    passed = True
    diff: dict[str, Any] | None = None

    if error_msg:
        passed = False
    elif case.expected_outputs is not None:
        diff = {}
        for key, expected_val in case.expected_outputs.items():
            actual_val = actual_outputs.get(key)
            if actual_val != expected_val:
                diff[key] = {"expected": expected_val, "actual": actual_val}
        passed = len(diff) == 0
        if not diff:
            diff = None

    result = TestCaseRunResult(
        passed=passed,
        actual_outputs=actual_outputs,
        expected_outputs=case.expected_outputs,
        diff=diff,
        execution_metadata=execution_metadata,
        error=error_msg,
    )
    return result.model_dump()


@app.get("/api/memory/{workflow_id}/{session_id}")
async def list_memory_keys(workflow_id: str, session_id: str):
    _validate_path_segment(workflow_id, "workflow_id")
    _validate_path_segment(session_id, "session_id")
    store = _get_memory_store()
    keys = await store.list_keys(workflow_id, session_id)
    return {"workflow_id": workflow_id, "session_id": session_id, "keys": keys}


@app.get("/api/memory/{workflow_id}/{session_id}/{key:path}")
async def read_memory_entry(workflow_id: str, session_id: str, key: str):
    _validate_path_segment(workflow_id, "workflow_id")
    _validate_path_segment(session_id, "session_id")
    store = _get_memory_store()
    entry = await store.read(workflow_id, session_id, key)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Memory key '{key}' not found")
    return entry.model_dump()


@app.delete("/api/memory/{workflow_id}/{session_id}")
async def clear_session_memory(workflow_id: str, session_id: str):
    _validate_path_segment(workflow_id, "workflow_id")
    _validate_path_segment(session_id, "session_id")
    store = _get_memory_store()
    await store.clear_session(workflow_id, session_id)
    return {"status": "cleared", "workflow_id": workflow_id, "session_id": session_id}


@app.get("/api/memory/{workflow_id}")
async def list_sessions(workflow_id: str):
    _validate_path_segment(workflow_id, "workflow_id")
    store = _get_memory_store()
    sessions = await store.list_sessions(workflow_id)
    return {"workflow_id": workflow_id, "sessions": sessions}


# ------------------------------------------------------------------
# Error Memory REST endpoints (Plan 17-1)
# ------------------------------------------------------------------


@app.get("/api/errors/{workflow_id}")
async def list_error_memory(workflow_id: str, limit: int = 50):
    """List indexed errors for a workflow."""
    rm = _require_run_manager()
    index = rm._get_error_memory_index()
    if index is None:
        return {"errors": [], "message": "Error memory not enabled"}
    try:
        stats = await index.stats(workflow_id)
        return {"workflow_id": workflow_id, **stats}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/api/errors/{workflow_id}")
async def clear_error_memory(workflow_id: str):
    """Clear error memory for a workflow."""
    rm = _require_run_manager()
    index = rm._get_error_memory_index()
    if index is None:
        raise HTTPException(status_code=400, detail="Error memory not enabled")
    await index.clear(workflow_id)
    return {"status": "cleared", "workflow_id": workflow_id}


@app.get("/api/errors/{workflow_id}/search")
async def search_error_memory(workflow_id: str, q: str = "", top_k: int = 5):
    """Semantic search over indexed errors."""
    rm = _require_run_manager()
    index = rm._get_error_memory_index()
    if index is None:
        raise HTTPException(status_code=400, detail="Error memory not enabled")
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")
    results = await index.query_similar(workflow_id, q.strip(), top_k=top_k)
    return {"workflow_id": workflow_id, "query": q, "results": results}


# ------------------------------------------------------------------
# Generated Rules REST endpoints (Plan 17-3)
# ------------------------------------------------------------------


@app.get("/api/rules/{workflow_id}")
async def list_generated_rules(workflow_id: str, status: str | None = None):
    """List generated rules with effectiveness metrics."""
    _validate_path_segment(workflow_id, "workflow_id")
    rm = _require_run_manager()
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        return {"rules": [], "message": "Self-evolving rules not enabled"}
    rules = manager.list_rules(workflow_id, status=status)
    return {
        "workflow_id": workflow_id,
        "rules": [r.model_dump() for r in rules],
    }


@app.post("/api/rules/{workflow_id}/{rule_id}/disable")
async def disable_generated_rule(workflow_id: str, rule_id: str):
    _validate_path_segment(workflow_id, "workflow_id")
    _validate_path_segment(rule_id, "rule_id")
    rm = _require_run_manager()
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        raise HTTPException(status_code=400, detail="Self-evolving rules not enabled")
    if not manager.disable_rule(workflow_id, rule_id):
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    rm.emit_rule_lifecycle_event(workflow_id, "rule_disabled", {
        "rule_id": rule_id,
        "reason": "manual_api",
    })
    return {"status": "disabled", "rule_id": rule_id}


@app.post("/api/rules/{workflow_id}/{rule_id}/enable")
async def enable_generated_rule(workflow_id: str, rule_id: str):
    _validate_path_segment(workflow_id, "workflow_id")
    _validate_path_segment(rule_id, "rule_id")
    rm = _require_run_manager()
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        raise HTTPException(status_code=400, detail="Self-evolving rules not enabled")
    if not manager.enable_rule(workflow_id, rule_id):
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return {"status": "enabled", "rule_id": rule_id}


@app.post("/api/rules/{workflow_id}/{rule_id}/approve")
async def approve_generated_rule(workflow_id: str, rule_id: str):
    _validate_path_segment(workflow_id, "workflow_id")
    _validate_path_segment(rule_id, "rule_id")
    rm = _require_run_manager()
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        raise HTTPException(status_code=400, detail="Self-evolving rules not enabled")
    if not manager.enable_rule(workflow_id, rule_id):
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return {"status": "approved", "rule_id": rule_id}


@app.delete("/api/rules/{workflow_id}/{rule_id}")
async def delete_generated_rule(workflow_id: str, rule_id: str):
    _validate_path_segment(workflow_id, "workflow_id")
    _validate_path_segment(rule_id, "rule_id")
    rm = _require_run_manager()
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        raise HTTPException(status_code=400, detail="Self-evolving rules not enabled")
    if not manager.delete_rule(workflow_id, rule_id):
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return {"status": "deleted", "rule_id": rule_id}


@app.post("/api/rules/{workflow_id}/rollback")
async def rollback_generated_rules(workflow_id: str, body: dict[str, Any]):
    _validate_path_segment(workflow_id, "workflow_id")
    rm = _require_run_manager()
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        raise HTTPException(status_code=400, detail="Self-evolving rules not enabled")
    before = body.get("before")
    if not before:
        raise HTTPException(status_code=400, detail="'before' timestamp is required")
    disabled = manager.rollback(workflow_id, float(before))
    return {"disabled_count": len(disabled), "disabled_rule_ids": disabled}


@app.get("/api/rules/{workflow_id}/stats")
async def generated_rules_stats(workflow_id: str):
    _validate_path_segment(workflow_id, "workflow_id")
    rm = _require_run_manager()
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        return {"message": "Self-evolving rules not enabled"}
    return manager.stats(workflow_id)


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


@app.websocket("/api/meta/sessions/{session_id}/events/ws")
async def meta_session_events_ws(websocket: WebSocket, session_id: str):
    """Live stream of meta-orchestrator events for a session."""
    await websocket.accept()

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5000)
    _meta_subscribers[session_id].append(queue)
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("Meta WebSocket error for session %s", session_id, exc_info=True)
    finally:
        subs = _meta_subscribers.get(session_id)
        if subs and queue in subs:
            subs.remove(queue)
        if not _meta_subscribers.get(session_id):
            _meta_subscribers.pop(session_id, None)


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


class StopRequest(BaseModel):
    message_id: str | None = None


@app.post("/api/chat/{channel_id}/stop")
async def stop_chat_stream(channel_id: str, req: StopRequest | None = None):
    """Cancel an active LLM chat stream (run streams are not stoppable here)."""
    if _chat_manager is None:
        raise HTTPException(status_code=503, detail="Chat not initialised")
    if not channel_id.startswith("chat-"):
        raise HTTPException(status_code=404, detail="Stream not found or already finished")
    found = _chat_manager.cancel_stream(channel_id)
    if not found:
        raise HTTPException(status_code=404, detail="Stream not found or already finished")
    return {"status": "stopping", "channel_id": channel_id}


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
    cancel_event = _chat_manager.register_stream(stream_channel_id)

    structured_mentions = [
        MentionRef(type=m.type, identifier=m.identifier)
        for m in req.mentions
    ] if req.mentions else []

    async def _produce():
        try:
            normalized_mode = normalize_chat_mode(req.mode)
            graph_dict = _graph_store.get_graph(req.workflow_id)

            detected_mode: str | None = None
            if normalized_mode == "auto":
                recent_run_failed = False
                if _run_manager is not None:
                    runs = _run_manager.list_runs()
                    wf_runs = [r for r in runs if r.graph_id == req.workflow_id]
                    if wf_runs:
                        recent_run_failed = wf_runs[0].status.value == "failed"
                detected_mode = detect_chat_mode(
                    req.message, graph_dict, recent_run_failed,
                )
                normalized_mode = detected_mode

            # ask and plan modes use text-only path (no tool calling)
            use_tools = graph_dict is not None and normalized_mode not in ("ask", "plan")

            # Build debug context for debug mode
            debug_ctx = ""
            if normalized_mode == "debug" and _run_manager is not None:
                debug_ctx = build_debug_context(
                    _run_manager.list_runs(), req.workflow_id,
                )

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
                mode=normalized_mode,
                cancel_event=cancel_event,
                mentions=structured_mentions,
                debug_context=debug_ctx,
            ):
                payload = event.model_dump()
                if detected_mode and payload.get("type") in (
                    "chat_complete", "chat_mutation",
                ):
                    payload["detected_mode"] = detected_mode
                await queue.put(payload)
        except Exception as exc:
            await queue.put({"type": "chat_error", "error": str(exc)})
        finally:
            _chat_manager.unregister_stream(stream_channel_id)
            await queue.put(None)

    asyncio.create_task(_produce())
    return {"message_id": uuid.uuid4().hex[:12], "stream_channel_id": stream_channel_id}


async def _handle_run_command(
    req: ChatMessageRequest, run_cmd: dict[str, Any],
) -> dict[str, Any]:
    """Execute a /run chat command and stream run events through the chat WS."""
    import time as _time

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

    _reap_stale_chat_streams()
    stream_channel_id = f"run-{uuid.uuid4().hex[:10]}"
    queue: asyncio.Queue = asyncio.Queue()
    _chat_streams[stream_channel_id] = (queue, _time.monotonic())

    run_target = run_cmd.get("target_node_id") or run_cmd.get("target_subgraph_key")

    async def _pipe_run_events() -> None:
        """Subscribe to RunManager events and forward them as chat_run_event."""
        run_queue = rm.subscribe(record.run_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(run_queue.get(), timeout=300)
                except asyncio.TimeoutError:
                    break
                event_type = event.get("event_type", "")
                if event_type == "_catchup":
                    snapshot = event.get("snapshot", {})
                    if snapshot.get("status") in ("completed", "failed"):
                        for buf_evt in event.get("buffered_events", []):
                            blk = map_run_event_to_chat_block(buf_evt, scope, run_target)
                            if blk is not None:
                                await queue.put({"type": "chat_run_event", "run_event": blk})
                        break
                    continue
                chat_block = map_run_event_to_chat_block(event, scope, run_target)
                if chat_block is not None:
                    await queue.put({"type": "chat_run_event", "run_event": chat_block})
                if event_type in ("run_completed", "run_failed"):
                    break
        except Exception:
            logger.debug("Run event pipe error for %s", record.run_id, exc_info=True)
        finally:
            rm.unsubscribe(record.run_id, run_queue)
            await queue.put(None)

    asyncio.create_task(_pipe_run_events())

    return {
        "type": "run_started",
        "run_id": record.run_id,
        "scope": scope,
        "target": run_target,
        "message_id": uuid.uuid4().hex[:12],
        "stream_channel_id": stream_channel_id,
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


@app.get("/api/chats/search")
async def search_chat_threads(q: str = "", workflow_id: str | None = None):
    """Search message content across threads."""
    if not q.strip():
        return {"results": []}
    results = _chat_store.search_threads(q.strip(), workflow_id=workflow_id)
    return {"results": results}


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
    mode = _chat_store._normalize_mode((body or {}).get("mode"))
    if mode != "agent":
        _chat_store.set_mode(workflow_id, thread.id, mode)
    data = thread.model_dump(mode="json")
    data["mode"] = mode
    return data


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
    if "mode" in body:
        _chat_store.set_mode(workflow_id, thread_id, body["mode"])
    thread.updated_at = datetime.now(timezone.utc)
    _chat_store.save_thread(thread)
    return {"status": "updated"}


@app.delete("/api/chats/{workflow_id}/{thread_id}")
async def delete_chat_thread(workflow_id: str, thread_id: str):
    if not _chat_store.delete_thread(workflow_id, thread_id):
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"status": "deleted"}


@app.get("/api/chats/{workflow_id}/{thread_id}/export")
async def export_chat_thread(workflow_id: str, thread_id: str, format: str = "md"):
    """Export a chat thread as Markdown or JSON."""
    if format == "json":
        data = _chat_store.export_thread_json(workflow_id, thread_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        return {"content": json.dumps(data, indent=2), "format": "json"}
    content = _chat_store.export_thread_markdown(workflow_id, thread_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"content": content, "format": "md"}


@app.post("/api/chats/{workflow_id}/{thread_id}/pin")
async def pin_chat_thread(workflow_id: str, thread_id: str, body: dict[str, Any] | None = None):
    """Set or unset pin status on a thread."""
    pinned = (body or {}).get("pinned", True)
    if not _chat_store.set_pinned(workflow_id, thread_id, pinned):
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"status": "updated", "pinned": pinned}


@app.post("/api/chats/{workflow_id}/{thread_id}/checkpoint")
async def save_chat_checkpoint(workflow_id: str, thread_id: str, body: dict[str, Any]):
    """Save a graph state checkpoint for a chat thread."""
    message_id = body.get("message_id", "")
    graph_snapshot = body.get("graph_snapshot")
    if not graph_snapshot:
        raise HTTPException(status_code=422, detail="graph_snapshot required")
    filename = _chat_store.save_checkpoint(
        workflow_id, thread_id, message_id, graph_snapshot,
    )
    return {"status": "saved", "filename": filename}


# ------------------------------------------------------------------
# Mention context endpoints — file, docs, and code-ref listings
# ------------------------------------------------------------------


@app.get("/api/files/list")
async def list_workspace_files():
    if _mention_resolver is None:
        raise HTTPException(status_code=503, detail="Server not fully initialised")
    files = _mention_resolver.file_resolver.list_files()
    return {"files": files}


@app.get("/api/docs/list")
async def list_docs():
    if _mention_resolver is None:
        raise HTTPException(status_code=503, detail="Server not fully initialised")
    docs = _mention_resolver.docs_resolver.list_docs()
    return {"docs": docs}


@app.get("/api/code-refs/{workflow_id}")
async def list_code_refs(workflow_id: str):
    graph_dict = _graph_store.get_graph(workflow_id)
    if graph_dict is None:
        raise HTTPException(status_code=404, detail=f"Graph '{workflow_id}' not found")
    refs = CodeResolver.list_code_refs(graph_dict)
    return {"refs": refs}


# ------------------------------------------------------------------
# Experience memory endpoints (Plan 19-1)
# ------------------------------------------------------------------


@app.get("/api/experiences")
async def list_experiences():
    """List all workflow experience summaries."""
    try:
        store = _get_experience_store()
        experiences = await store.list_experiences()
        return {"experiences": [e.model_dump() for e in experiences]}
    except Exception as exc:
        logger.exception("Failed to list experiences")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/experiences/{workflow_id}")
async def get_experience(workflow_id: str):
    """Get detailed experience for a specific workflow."""
    _validate_path_segment(workflow_id, "workflow_id")
    store = _get_experience_store()
    exp = await store.load_experience(workflow_id)
    if exp is None:
        raise HTTPException(status_code=404, detail=f"No experience for '{workflow_id}'")
    return exp.model_dump()


@app.post("/api/experiences/search")
async def search_experiences(body: dict[str, Any]):
    """Semantic search over workflow experiences."""
    global _experience_index_bootstrap_done
    query = str(body.get("query", "")).strip()
    top_k = int(body.get("top_k", 5))
    if not query:
        raise HTTPException(status_code=422, detail="query is required")
    index = _get_experience_index()
    store = _get_experience_store(with_index=True)

    # Best-effort sync: make sure existing experiences are indexed.
    if not _experience_index_bootstrap_done:
        for exp in await store.list_experiences():
            await store.save_experience(exp)
        _experience_index_bootstrap_done = True

    hits = await index.search_similar(query, top_k=max(1, min(top_k, 20)))
    results: list[dict[str, Any]] = []
    for workflow_id, score in hits:
        exp = await store.load_experience(workflow_id)
        results.append({
            "workflow_id": workflow_id,
            "score": score,
            "experience": exp.model_dump() if exp is not None else None,
        })
    return {"query": query, "results": results}


@app.post("/api/experiences/{workflow_id}/refresh")
async def refresh_experience(workflow_id: str):
    """Force experience consolidation for a workflow."""
    _validate_path_segment(workflow_id, "workflow_id")
    from dan.engine.experience import (
        consolidate_experience,
        extract_experience_from_graph,
    )
    from dan.engine.error_memory import PrincipleStore

    rm = _require_run_manager()
    try:
        store = _get_experience_store(with_index=True)
    except HTTPException:
        store = _get_experience_store(with_index=False)
    graph_data = _graph_store.get_graph(workflow_id)
    if graph_data is None:
        raise HTTPException(status_code=404, detail=f"Graph '{workflow_id}' not found")

    graph = Graph.model_validate(graph_data)
    exp = await store.load_experience(workflow_id)
    if exp is None:
        exp = extract_experience_from_graph(graph)
        exp = exp.model_copy(update={"workflow_id": workflow_id})

    snapshots: list[dict[str, Any]] = []
    if rm.run_store is not None:
        snapshots = rm.run_store.list_summaries(workflow_id=workflow_id, limit=10000)

    principles: list[dict[str, Any]] = []
    try:
        ps = PrincipleStore(_get_memory_store())
        principles = [p.model_dump() for p in await ps.load_principles(workflow_id)]
    except Exception:
        logger.debug("Failed to load principles for experience refresh", exc_info=True)

    exp = consolidate_experience(exp, snapshots, principles)
    await store.save_experience(exp)
    return {"status": "refreshed", "experience": exp.model_dump()}


@app.delete("/api/experiences/{workflow_id}")
async def delete_experience(workflow_id: str):
    """Remove a workflow experience entry."""
    _validate_path_segment(workflow_id, "workflow_id")
    store = _get_experience_store()
    deleted = await store.delete_experience(workflow_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Experience not found")
    return {"status": "deleted"}


# ------------------------------------------------------------------
# Meta-orchestrator endpoints (Plans 19-2, 19-4)
# ------------------------------------------------------------------


@app.get("/api/meta/discover")
async def meta_discover(goal: str = "", top_k: int = 5):
    """List available tools, skills, and patterns for the planner."""
    rm = _require_run_manager()
    from dan.meta.discovery import DiscoveryService

    try:
        exp_index = _get_experience_index()
    except HTTPException:
        exp_index = None
    svc = DiscoveryService(
        tool_registry=rm.tool_registry,
        experience_index=exp_index,
        experience_store=_get_experience_store(with_index=exp_index is not None),
        graph_store=_graph_store,
    )
    workflow_matches = await svc.discover_workflows(
        goal if goal.strip() else "generic workflow",
        top_k=max(1, min(top_k, 20)),
    )
    return {
        "tools": [t.model_dump() for t in svc.discover_tools()],
        "skills": [s.model_dump() for s in svc.discover_skills()],
        "patterns": [p.model_dump() for p in svc.discover_patterns()],
        "workflows": [w.model_dump() for w in workflow_matches],
    }


@app.post("/api/meta/plan")
async def meta_plan(body: dict[str, Any]):
    """Create a workflow plan for a given goal."""
    goal = str(body.get("goal", "")).strip()
    error_context = body.get("error_context")
    if not goal:
        raise HTTPException(status_code=422, detail="goal is required")
    _, planner, _ = _build_meta_controller()
    output = await planner.plan(goal, error_context)
    return {
        "goal": goal,
        "plan": output.plan.model_dump(),
        "review": output.review.model_dump(),
    }


@app.post("/api/meta/validate-plan")
async def meta_validate_plan(body: dict[str, Any]):
    """Validate a candidate planner output without executing it."""
    _, planner, _ = _build_meta_controller()
    plan_data = body.get("plan", body)
    if not isinstance(plan_data, dict):
        raise HTTPException(status_code=422, detail="plan must be an object")

    action = str(plan_data.get("action", "")).upper()
    from dan.meta.planner import AdaptPlan, GeneratePlan, ReusePlan

    if action == "REUSE":
        plan = ReusePlan.model_validate(plan_data)
    elif action == "ADAPT":
        plan = AdaptPlan.model_validate(plan_data)
    elif action == "GENERATE":
        plan = GeneratePlan.model_validate(plan_data)
    else:
        raise HTTPException(status_code=422, detail="Unknown plan action")

    review = planner._validate_plan(plan)
    return {"valid": review.valid, "review": review.model_dump()}


@app.post("/api/meta/run")
async def meta_run(body: dict[str, Any]):
    """Start a background meta-orchestration run."""
    from dan.meta.controller import MetaControllerConfig

    goal = str(body.get("goal", "")).strip()
    if not goal:
        raise HTTPException(status_code=422, detail="goal is required")

    config = MetaControllerConfig.model_validate(body.get("config", {}))
    controller, _, _ = _build_meta_controller()
    session = await controller.create_session(goal, config)

    async def _runner() -> None:
        try:
            await controller.run_session(session, config)
        finally:
            _meta_tasks.pop(session.session_id, None)

    task = asyncio.create_task(_runner(), name=f"meta-session-{session.session_id}")
    _meta_tasks[session.session_id] = task
    return {
        "session_id": session.session_id,
        "status": "started",
        "session": session.model_dump(),
    }


@app.get("/api/meta/sessions")
async def list_meta_sessions():
    """List all meta-orchestration sessions."""
    _, _, store = _build_meta_controller()
    sessions = await store.list_sessions()
    return {"sessions": [s.model_dump() for s in sessions]}


@app.get("/api/meta/sessions/{session_id}")
async def get_meta_session(session_id: str):
    """Get a specific meta session by ID."""
    _validate_path_segment(session_id, "session_id")
    _, _, store = _build_meta_controller()
    session = await store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        **session.model_dump(),
        "is_running": session_id in _meta_tasks and not _meta_tasks[session_id].done(),
    }


@app.get("/api/meta/sessions/{session_id}/events")
async def get_meta_session_events(session_id: str, limit: int = 200):
    """Fetch persisted meta-controller events for a session."""
    _validate_path_segment(session_id, "session_id")
    _, _, store = _build_meta_controller()
    session = await store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    lim = max(1, min(limit, 5000))
    return {"session_id": session_id, "events": session.events[-lim:]}


@app.post("/api/meta/sessions/{session_id}/pause")
async def pause_meta_session(session_id: str):
    """Request pause at the next controller checkpoint."""
    _validate_path_segment(session_id, "session_id")
    _, _, store = _build_meta_controller()
    session = await store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    session.pause_requested = True
    await store.save(session)
    return {
        "session_id": session_id,
        "status": "pause_requested",
        "is_running": session_id in _meta_tasks and not _meta_tasks[session_id].done(),
    }


@app.post("/api/meta/sessions/{session_id}/resume")
async def resume_meta_session(session_id: str, body: dict[str, Any] | None = None):
    """Resume a paused meta session in the background."""
    _validate_path_segment(session_id, "session_id")
    if session_id in _meta_tasks and not _meta_tasks[session_id].done():
        return {"session_id": session_id, "status": "already_running"}

    from dan.meta.controller import HumanOverride

    controller, _, _ = _build_meta_controller()
    override = None
    if body and body.get("override") is not None:
        override = HumanOverride.model_validate(body["override"])

    async def _runner() -> None:
        try:
            await controller.resume(session_id, override=override)
        finally:
            _meta_tasks.pop(session_id, None)

    task = asyncio.create_task(_runner(), name=f"meta-resume-{session_id}")
    _meta_tasks[session_id] = task
    return {"session_id": session_id, "status": "resuming"}


@app.delete("/api/meta/sessions/{session_id}")
async def delete_meta_session(session_id: str):
    """Delete/abort a meta session."""
    _validate_path_segment(session_id, "session_id")
    task = _meta_tasks.pop(session_id, None)
    if task is not None and not task.done():
        task.cancel()

    _, _, store = _build_meta_controller()
    deleted = await store.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "task_cancelled": task is not None}


# ------------------------------------------------------------------
# Static file serving for the built editor (production)
# ------------------------------------------------------------------

_editor_dist = os.path.join(os.path.dirname(__file__), "..", "..", "..", "editor", "dist")
if os.path.isdir(_editor_dist):
    app.mount("/", StaticFiles(directory=_editor_dist, html=True), name="editor")
