"""Publish/share/export/block capability handlers."""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from dan.server.capability_registry import CapabilityContext, CapabilityResult
from dan.server.capabilities._helpers import _schedule_export_cleanup, _truncate

logger = logging.getLogger(__name__)


def _resolve_graph(ctx: CapabilityContext, graph_id: str | None) -> tuple[str, dict | None, "Any"]:
    """Resolve graph_id to graph data and Graph model. Returns (graph_id, raw_data, graph_model)."""
    gid = (graph_id or ctx.workflow_id or "").strip()
    if not gid:
        return ("", None, None)
    if ctx.graph_store is None:
        return (gid, None, None)
    data = ctx.graph_store.get_graph(gid)
    if data is None:
        return (gid, None, None)
    try:
        from dan.models.graph import Graph
        graph = Graph.model_validate(data)
        return (gid, data, graph)
    except Exception:
        return (gid, data, None)


async def handle_publish_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.publish_registry is None:
        return CapabilityResult(success=False, message="Publish registry not available.")
    _, _, graph = _resolve_graph(ctx, args.get("graph_id"))
    if graph is None:
        gid = (args.get("graph_id") or ctx.workflow_id or "").strip()
        return CapabilityResult(
            success=False,
            message=f"Graph not found: {gid or '(no workflow_id)'}",
        )
    gid = (args.get("graph_id") or ctx.workflow_id or "").strip()
    name_override = args.get("name_override")
    api_key = args.get("api_key")
    rate_limit = args.get("rate_limit")
    try:
        slug = ctx.publish_registry.register(
            graph,
            name_override=name_override,
            api_key=api_key,
            rate_limit=rate_limit,
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Publish failed: {exc}")
    graphs_dir = ctx.graphs_dir or "./graphs"
    pub_config = {"enabled": True, "api_key": api_key, "rate_limit": rate_limit}
    pub_path = Path(graphs_dir) / f"{gid}.publish.json"
    try:
        pub_path.write_text(json.dumps(pub_config, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to persist .publish.json: %s", exc)
    return CapabilityResult(
        success=True,
        message=f"Published workflow as **{slug}**. Config saved to {pub_path.name}.",
        data={"slug": slug, "graph_id": gid},
        output_preview=f"Published as {slug}",
    )


async def handle_unpublish_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.publish_registry is None:
        return CapabilityResult(success=False, message="Publish registry not available.")
    gid, _, graph = _resolve_graph(ctx, args.get("graph_id"))
    if graph is None:
        return CapabilityResult(
            success=False,
            message=f"Graph not found: {gid or '(no workflow_id)'}",
        )
    from dan.publish.schema import slugify as _slugify
    from dan.utils.workflow_interface import derive_workflow_interface
    try:
        iface = derive_workflow_interface(graph)
        slug = _slugify(iface.name)
    except Exception:
        slug = gid
    ctx.publish_registry.unregister(slug)
    graphs_dir = ctx.graphs_dir or "./graphs"
    pub_path = Path(graphs_dir) / f"{gid}.publish.json"
    if pub_path.exists():
        try:
            pub_path.unlink()
        except Exception as exc:
            logger.warning("Failed to remove .publish.json: %s", exc)
    return CapabilityResult(
        success=True,
        message=f"Unpublished workflow **{gid}**.",
        data={"graph_id": gid},
        output_preview=f"Unpublished {gid}",
    )


async def handle_export_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    _, _, graph = _resolve_graph(ctx, args.get("graph_id"))
    if graph is None:
        gid = (args.get("graph_id") or ctx.workflow_id or "").strip()
        return CapabilityResult(
            success=False,
            message=f"Graph not found: {gid or '(no workflow_id)'}",
        )
    fmt = (args.get("format") or "block").lower()
    name = args.get("name") or ""
    version = args.get("version") or "0.1.0"
    node_id = args.get("node_id")
    gid = (args.get("graph_id") or ctx.workflow_id or "").strip()

    if fmt == "block":
        from dan.blocks import export_workflow_block, export_composite_block
        output_dir = Path(tempfile.mkdtemp(prefix="dan-export-"))
        try:
            if node_id:
                block_dir = export_composite_block(graph, node_id, output_dir, name=name, version=version)
            else:
                block_dir = export_workflow_block(graph, output_dir, name=name, version=version)
            _schedule_export_cleanup(str(output_dir))
            return CapabilityResult(
                success=True,
                message=f"Exported as block to **{block_dir}**.",
                data={"path": str(block_dir), "format": "block"},
                output_preview=f"Block at {block_dir}",
            )
        except Exception as exc:
            shutil.rmtree(str(output_dir), ignore_errors=True)
            return CapabilityResult(success=False, message=f"Block export failed: {exc}")

    if fmt == "markdown":
        from dan.loader.decompiler import decompile_to_markdown
        output_dir = Path(tempfile.mkdtemp(prefix="dan-export-md-"))
        try:
            result = decompile_to_markdown(graph, output_dir)
            paths = [str(p) for p in result.files]
            _schedule_export_cleanup(str(output_dir))
            return CapabilityResult(
                success=True,
                message=f"Exported to markdown: {', '.join(paths)}",
                data={"paths": paths, "format": "markdown"},
                output_preview=f"Markdown at {paths[0] if paths else output_dir}",
            )
        except Exception as exc:
            shutil.rmtree(str(output_dir), ignore_errors=True)
            return CapabilityResult(success=False, message=f"Markdown export failed: {exc}")

    if fmt == "python":
        from dan.builder.decompiler import decompile as decompile_to_python
        output_dir = Path(tempfile.mkdtemp(prefix="dan-export-py-"))
        try:
            code = decompile_to_python(graph)
            out_file = output_dir / "workflow.py"
            out_file.write_text(code, encoding="utf-8")
            _schedule_export_cleanup(str(output_dir))
            return CapabilityResult(
                success=True,
                message=f"Exported to Python: **{out_file}**",
                data={"path": str(out_file), "format": "python"},
                output_preview=f"Python at {out_file}",
            )
        except Exception as exc:
            shutil.rmtree(str(output_dir), ignore_errors=True)
            return CapabilityResult(success=False, message=f"Python export failed: {exc}")

    return CapabilityResult(success=False, message=f"Unknown format: {fmt}")


async def handle_share_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    gid, _, graph = _resolve_graph(ctx, args.get("graph_id"))
    if graph is None:
        return CapabilityResult(
            success=False,
            message=f"Graph not found: {gid or '(no workflow_id)'}",
        )
    fmt = (args.get("format") or "mcp_config").lower()
    base_url = args.get("base_url") or "http://localhost:8001"
    graphs_dir = ctx.graphs_dir or "./graphs"
    workflow_path = str(Path(graphs_dir) / f"{gid}.json")

    from dan.publish.portal import generate_mcp_config, generate_api_docs, generate_openapi_spec
    from dan.utils.workflow_interface import derive_workflow_interface

    iface = derive_workflow_interface(graph)
    interfaces = [iface]

    if fmt == "mcp_config":
        config = generate_mcp_config(workflow_path, name=iface.name, interfaces=interfaces)
        return CapabilityResult(
            success=True,
            message="MCP client config (paste into .cursor/mcp.json or claude_desktop_config.json):\n```json\n" + json.dumps(config, indent=2) + "\n```",
            data=config,
            output_preview="MCP config generated",
        )
    if fmt == "api_docs":
        docs = generate_api_docs(interfaces, base_url=base_url)
        return CapabilityResult(
            success=True,
            message=f"API documentation:\n\n{docs}",
            data={"markdown": docs},
            output_preview=_truncate(docs),
        )
    if fmt == "openapi":
        spec = generate_openapi_spec(interfaces)
        return CapabilityResult(
            success=True,
            message="OpenAPI spec:\n```json\n" + json.dumps(spec, indent=2) + "\n```",
            data=spec,
            output_preview="OpenAPI spec generated",
        )
    return CapabilityResult(success=False, message=f"Unknown format: {fmt}")


async def handle_list_published(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.publish_registry is None:
        return CapabilityResult(success=False, message="Publish registry not available.")
    items = ctx.publish_registry.list_all()
    if not items:
        return CapabilityResult(
            success=True,
            message="No published workflows.",
            data=[],
            output_preview="No published workflows.",
        )
    lines = []
    for w in items:
        human = " (has HumanNode)" if w.has_human_nodes else ""
        lines.append(f"- **{w.workflow_id}** — {w.name}: {_truncate(w.description or '', 80)}{human}")
    text = f"Published workflows ({len(items)}):\n" + "\n".join(lines)
    return CapabilityResult(
        success=True,
        message=text,
        data=[{"workflow_id": w.workflow_id, "name": w.name, "description": w.description, "has_human_nodes": w.has_human_nodes} for w in items],
        output_preview=_truncate(text),
    )


async def handle_get_publish_status(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.publish_registry is None:
        return CapabilityResult(success=False, message="Publish registry not available.")
    gid, _, graph = _resolve_graph(ctx, args.get("graph_id"))
    if graph is None:
        return CapabilityResult(
            success=False,
            message=f"Graph not found: {gid or '(no workflow_id)'}",
        )
    from dan.publish.schema import slugify as _slugify
    from dan.utils.workflow_interface import derive_workflow_interface
    try:
        iface = derive_workflow_interface(graph)
        slug = _slugify(iface.name)
    except Exception:
        slug = gid
    published = ctx.publish_registry.is_published(slug)
    result = {"graph_id": gid, "published": published, "workflow_id": slug if published else None}
    graphs_dir = ctx.graphs_dir or "./graphs"
    pub_path = Path(graphs_dir) / f"{gid}.publish.json"
    if pub_path.exists():
        try:
            result["config"] = json.loads(pub_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    text = f"**{gid}**: {'published' if published else 'not published'}" + (f" (slug: {slug})" if published else "")
    return CapabilityResult(
        success=True,
        message=text,
        data=result,
        output_preview=text,
    )


async def handle_import_block(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    import os

    if ctx.block_registry is None:
        return CapabilityResult(success=False, message="Block registry not available.")
    source = str(args.get("source", "")).strip()
    if not source:
        return CapabilityResult(success=False, message="source is required")
    scope = args.get("scope") or "user"
    workspace_str = args.get("workspace")
    workspace = Path(workspace_str) if workspace_str else None
    if scope == "workspace" and workspace is None:
        workspace = Path(os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd()))
    force = bool(args.get("force", False))
    try:
        from dan.blocks import import_block as _import_block
        installed = _import_block(source, scope=scope, workspace=workspace, force=force)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Import failed: {exc}")
    ctx.block_registry.scan()
    return CapabilityResult(
        success=True,
        message=f"Imported block **{installed.name}@{installed.version}** to {installed.install_path}.",
        data={"name": installed.name, "version": installed.version, "block_type": installed.block_type, "install_path": str(installed.install_path)},
        output_preview=f"{installed.name}@{installed.version}",
    )


async def handle_list_blocks(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.block_registry is None:
        return CapabilityResult(success=False, message="Block registry not available.")
    ctx.block_registry.scan()
    blocks = ctx.block_registry.list_blocks()
    if not blocks:
        return CapabilityResult(
            success=True,
            message="No blocks installed.",
            data=[],
            output_preview="No blocks installed.",
        )
    lines = []
    for b in blocks:
        desc = (b.metadata.description or "")[:60] if b.metadata else ""
        lines.append(f"- **{b.name}@{b.version}** ({b.block_type}): {desc}")
    text = f"Installed blocks ({len(blocks)}):\n" + "\n".join(lines)
    return CapabilityResult(
        success=True,
        message=text,
        data=[{"name": b.name, "version": b.version, "block_type": b.block_type, "description": (b.metadata.description or "") if b.metadata else ""} for b in blocks],
        output_preview=_truncate(text),
    )
