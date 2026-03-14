"""General-purpose tools: run_python, department state, RAG indexing."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Awaitable

from dan.server.exec import execute_python

logger = logging.getLogger(__name__)


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


async def _get_department_state(**kwargs: Any) -> dict[str, Any]:
    """Load department state (active, deleted departments)."""
    project_root = Path(__file__).resolve().parents[4]
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
    project_root = Path(__file__).resolve().parents[4]
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


async def _rag_index_documents(
    get_indexer: Callable[[], Awaitable[Any]],
    pdf_dir: str = "",
    collection: str = "literature",
    glob_pattern: str = "*.pdf",
    **kwargs: Any,
) -> dict[str, Any]:
    """Index PDF documents from a directory into a RAG collection."""
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
        indexer = await get_indexer()
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
