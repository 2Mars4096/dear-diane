"""dan-publish — publish DAN workflows as MCP/HTTP services.

Usage examples::

    dan-publish workflow.json --type mcp          # MCP stdio server (default)
    dan-publish workflow.json --type http          # HTTP REST server
    dan-publish workflow.json --type both          # MCP + HTTP
    dan-publish --dir ./graphs/ --type mcp         # publish all in directory
    dan-publish workflow.json --generate-config    # output MCP client config
    dan-publish workflow.json --docs               # generate markdown API docs
    dan-publish workflow.json --openapi            # output OpenAPI 3.1 spec
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("dan.cli.publish")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dan-publish",
        description="Publish DAN workflows as MCP servers or HTTP APIs.",
    )
    p.add_argument(
        "source",
        nargs="?",
        default=None,
        help="Workflow file (.json, .md, .py) or directory",
    )
    p.add_argument(
        "--dir",
        dest="directory",
        metavar="PATH",
        help="Publish all workflows in a directory",
    )
    p.add_argument(
        "--type",
        dest="server_type",
        choices=["mcp", "http", "both"],
        default="mcp",
        help="Server type (default: mcp)",
    )
    p.add_argument(
        "--port",
        type=int,
        default=8001,
        help="HTTP server port (default: 8001)",
    )
    p.add_argument(
        "--host",
        default="0.0.0.0",
        help="HTTP server host (default: 0.0.0.0)",
    )
    p.add_argument(
        "--name",
        metavar="NAME",
        help="Override the published tool/API name",
    )
    p.add_argument(
        "--api-key",
        dest="api_key",
        metavar="KEY",
        help="Require API key for HTTP endpoints",
    )
    p.add_argument(
        "--human-timeout",
        type=float,
        default=300.0,
        metavar="SECS",
        help="HumanNode timeout in seconds (default: 300)",
    )

    # -- LLM config -----------------------------------------------------------
    p.add_argument("--llm-api-key", dest="llm_api_key", help="LLM API key")
    p.add_argument("--model", help="Default LLM model")
    p.add_argument("--base-url", dest="base_url", help="LLM base URL")

    # -- Output modes ---------------------------------------------------------
    p.add_argument(
        "--generate-config",
        action="store_true",
        help="Output MCP client config JSON (for Cursor / Claude Desktop)",
    )
    p.add_argument(
        "--docs",
        action="store_true",
        help="Generate markdown API documentation",
    )
    p.add_argument(
        "--openapi",
        action="store_true",
        help="Output OpenAPI 3.1 spec as JSON",
    )

    return p


def _die(msg: str) -> None:
    print(f"dan-publish: error: {msg}", file=sys.stderr)
    sys.exit(1)


def _resolve_source(args: argparse.Namespace) -> str:
    """Return the workflow source path from either positional arg or --dir."""
    if args.source:
        return args.source
    if args.directory:
        return args.directory
    _die("No workflow source specified. Provide a file path or --dir.")
    return ""  # unreachable


def _load_workflows(source: str) -> list[tuple[Any, str | None]]:
    from dan.publish.mcp_server import load_workflows_from_path
    try:
        return load_workflows_from_path(source)
    except Exception as exc:
        _die(f"Failed to load workflow(s): {exc}")
        return []  # unreachable


def _get_interfaces(
    workflows: list[tuple[Any, str | None]],
    name_override: str | None = None,
) -> list[Any]:
    from dan.utils.workflow_interface import derive_workflow_interface
    interfaces = []
    for graph, name in workflows:
        iface = derive_workflow_interface(graph)
        if name:
            iface.name = name
        if name_override and len(workflows) == 1:
            iface.name = name_override
        interfaces.append(iface)
    return interfaces


def _build_engine_config(args: argparse.Namespace) -> Any:
    from dan.cli import load_env, resolve_config
    from dan.engine.executor import EngineConfig

    load_env()
    cfg = resolve_config(
        api_key=args.llm_api_key,
        model=args.model,
        base_url=args.base_url,
    )
    return EngineConfig(
        llm_api_key=cfg["api_key"],
        llm_base_url=cfg["base_url"] or "https://api.vectorengine.ai/v1",
        llm_default_model=cfg["model"] or "claude-sonnet-4-6",
    )


def _print_startup_banner(
    workflows: list[tuple[Any, str | None]],
    server_type: str,
    port: int,
    interfaces: list[Any],
) -> None:
    """Print a startup banner, using Rich if available."""
    from dan.cli import _try_import_rich
    from dan.publish.schema import slugify

    Console, _ = _try_import_rich()
    if Console:
        console = Console(stderr=True)
        console.rule("[bold cyan]DAN Publish[/]")
        for iface in interfaces:
            slug = slugify(iface.name)
            console.print(f"  Workflow:  [bold]{iface.name}[/] ({slug})")
            console.print(f"  Human:    {'yes' if iface.has_human_nodes else 'no'}")
        console.print(f"  Transport: [bold]{server_type}[/]")
        if server_type in ("http", "both"):
            console.print(f"  HTTP:      http://localhost:{port}")
        if server_type in ("mcp", "both"):
            console.print(f"  MCP:       stdio")
        console.print()
        console.print("[dim]Press Ctrl+C to stop[/]")
    else:
        print("--- DAN Publish ---", file=sys.stderr)
        for iface in interfaces:
            slug = slugify(iface.name)
            print(f"  Workflow: {iface.name} ({slug})", file=sys.stderr)
        print(f"  Transport: {server_type}", file=sys.stderr)
        if server_type in ("http", "both"):
            print(f"  HTTP: http://localhost:{port}", file=sys.stderr)


def main() -> None:
    parser = build_parser()

    if len(sys.argv) < 2:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()
    source = _resolve_source(args)
    workflows = _load_workflows(source)

    if not workflows:
        _die("No workflows found.")

    interfaces = _get_interfaces(workflows, args.name)

    # -- Output-only modes (no server) ----------------------------------------

    if args.generate_config:
        from dan.publish.portal import generate_mcp_config
        config = generate_mcp_config(
            source, name=args.name, interfaces=interfaces,
        )
        print(json.dumps(config, indent=2))
        return

    if args.docs:
        from dan.publish.portal import generate_api_docs
        base_url = f"http://localhost:{args.port}"
        docs = generate_api_docs(interfaces, base_url=base_url)
        print(docs)
        return

    if args.openapi:
        from dan.publish.portal import generate_openapi_spec
        spec = generate_openapi_spec(interfaces)
        print(json.dumps(spec, indent=2))
        return

    # -- Server modes ---------------------------------------------------------

    engine_config = _build_engine_config(args)
    _print_startup_banner(workflows, args.server_type, args.port, interfaces)

    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    if args.server_type == "mcp":
        from dan.publish.mcp_server import run_mcp_stdio
        run_mcp_stdio(
            workflows,
            engine_config=engine_config,
            human_timeout=args.human_timeout,
        )

    elif args.server_type == "http":
        _run_http(workflows, engine_config, args)

    elif args.server_type == "both":
        _run_both(workflows, engine_config, args)


def _run_http(
    workflows: list[tuple[Any, str | None]],
    engine_config: Any,
    args: argparse.Namespace,
) -> None:
    import uvicorn
    from dan.publish.http_server import create_publish_app

    app = create_publish_app(
        workflows,
        engine_config=engine_config,
        global_api_key=args.api_key,
        human_timeout=args.human_timeout,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def _run_both(
    workflows: list[tuple[Any, str | None]],
    engine_config: Any,
    args: argparse.Namespace,
) -> None:
    """Run HTTP server in a thread while MCP stdio runs on the main thread."""
    import threading
    import uvicorn
    from dan.publish.http_server import create_publish_app
    from dan.publish.mcp_server import run_mcp_stdio

    app = create_publish_app(
        workflows,
        engine_config=engine_config,
        global_api_key=args.api_key,
        human_timeout=args.human_timeout,
    )

    http_thread = threading.Thread(
        target=uvicorn.run,
        kwargs={"app": app, "host": args.host, "port": args.port, "log_level": "info"},
        daemon=True,
    )
    http_thread.start()

    run_mcp_stdio(
        workflows,
        engine_config=engine_config,
        human_timeout=args.human_timeout,
    )


if __name__ == "__main__":
    main()
