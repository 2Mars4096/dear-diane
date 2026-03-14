"""Domain-specific tool implementations extracted from app.py.

All tool functions are re-exported here for convenience. Use
``register_server_tools(registry, ...)`` to wire them into a
:class:`ToolRegistry` with the canonical tool IDs.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable

from .latex import (
    _save_paper,
    _check_latex_deps,
    _compile_latex,
    _package_submission,
)
from .research import (
    _search_papers,
    _citation_verifier,
)
from .quant import (
    _run_strategy_script,
    _run_backtest,
    _save_grid_csv,
    _plot_backtest,
)
from .general import (
    _run_python,
    _get_department_state,
    _update_department_state,
    _rag_index_documents,
)

logger = logging.getLogger(__name__)

__all__ = [
    "register_server_tools",
    "_save_paper",
    "_search_papers",
    "_citation_verifier",
    "_check_latex_deps",
    "_compile_latex",
    "_package_submission",
    "_run_strategy_script",
    "_run_backtest",
    "_save_grid_csv",
    "_plot_backtest",
    "_run_python",
    "_get_department_state",
    "_update_department_state",
    "_rag_index_documents",
]


def register_server_tools(
    registry: Any,
    *,
    get_indexer: Callable[[], Awaitable[Any]] | None = None,
) -> None:
    """Register all domain-specific tools into *registry*.

    This mirrors the registration block that previously lived inside
    ``startup._build_tool_registry()``.  The caller is responsible for
    built-in tool registration and custom-tool discovery.
    """
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

    if get_indexer is not None:
        async def _rag_index_with_injected_indexer(
            pdf_dir: str = "",
            collection: str = "literature",
            glob_pattern: str = "*.pdf",
            **kwargs: Any,
        ) -> dict[str, Any]:
            return await _rag_index_documents(
                get_indexer,
                pdf_dir=pdf_dir,
                collection=collection,
                glob_pattern=glob_pattern,
                **kwargs,
            )

        registry.register("rag_index_documents", _rag_index_with_injected_indexer)
