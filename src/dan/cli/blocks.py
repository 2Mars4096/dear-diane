"""dan-blocks — manage shareable DAN workflow blocks.

Subcommands: list, install, export, remove, pack, info.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="dan-blocks",
        description="Manage shareable DAN workflow blocks.",
    )
    sub = parser.add_subparsers(dest="command")

    # -- list ---------------------------------------------------------------
    p_list = sub.add_parser("list", help="List installed blocks")
    p_list.add_argument("--workspace", type=Path, default=None, help="Workspace directory")

    # -- install ------------------------------------------------------------
    p_install = sub.add_parser("install", help="Install a block from path or URL")
    p_install.add_argument("source", help="Path to block directory, .dan-block.tar.gz, or URL")
    p_install.add_argument("--scope", choices=["user", "workspace"], default="user")
    p_install.add_argument("--workspace", type=Path, default=None)
    p_install.add_argument("--force", action="store_true", help="Overwrite existing installation")

    # -- export -------------------------------------------------------------
    p_export = sub.add_parser("export", help="Export a workflow or composite node as a block")
    p_export.add_argument("workflow_path", help="Path to workflow JSON file")
    p_export.add_argument("--node", default=None, help="Composite node ID to extract")
    p_export.add_argument("--name", default="", help="Block name")
    p_export.add_argument("--version", default="0.1.0", help="Block version (semver)")
    p_export.add_argument("--output", "-o", type=Path, default=Path("."), help="Output directory")

    # -- remove -------------------------------------------------------------
    p_remove = sub.add_parser("remove", help="Remove an installed block")
    p_remove.add_argument("name", help="Block name")
    p_remove.add_argument("--version", default=None, help="Specific version (removes latest if omitted)")
    p_remove.add_argument("--workspace", type=Path, default=None)

    # -- pack ---------------------------------------------------------------
    p_pack = sub.add_parser("pack", help="Compress a block directory to .dan-block.tar.gz")
    p_pack.add_argument("block_dir", type=Path, help="Path to block directory")

    # -- info ---------------------------------------------------------------
    p_info = sub.add_parser("info", help="Show block metadata and schemas")
    p_info.add_argument("name", help="Block name")
    p_info.add_argument("--version", default=None, help="Specific version")
    p_info.add_argument("--workspace", type=Path, default=None)

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    handler = {
        "list": _cmd_list,
        "install": _cmd_install,
        "export": _cmd_export,
        "remove": _cmd_remove,
        "pack": _cmd_pack,
        "info": _cmd_info,
    }[args.command]
    handler(args)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_list(args: argparse.Namespace) -> None:
    from dan.blocks.registry import BlockRegistry

    reg = BlockRegistry(workspace=args.workspace)
    reg.scan()
    blocks = reg.list_blocks()

    if not blocks:
        print("No blocks installed.")
        return

    rows = [(b.name, b.version, b.block_type, b.metadata.description[:60]) for b in blocks]
    _print_table(["Name", "Version", "Type", "Description"], rows)


def _cmd_install(args: argparse.Namespace) -> None:
    from dan.blocks.importer import import_block

    installed = import_block(
        args.source,
        scope=args.scope,
        workspace=args.workspace,
        force=args.force,
    )
    print(f"Installed {installed.name}@{installed.version} -> {installed.install_path}")


def _cmd_export(args: argparse.Namespace) -> None:
    from dan.blocks.export import export_composite_block, export_workflow_block
    from dan.models.graph import Graph

    graph_data = json.loads(Path(args.workflow_path).read_text(encoding="utf-8"))
    graph = Graph.model_validate(graph_data)

    if args.node:
        block_dir = export_composite_block(
            graph,
            args.node,
            args.output,
            name=args.name,
            version=args.version,
        )
    else:
        block_dir = export_workflow_block(
            graph,
            args.output,
            name=args.name,
            version=args.version,
        )

    print(f"Exported block to {block_dir}")


def _cmd_remove(args: argparse.Namespace) -> None:
    from dan.blocks.registry import BlockRegistry

    reg = BlockRegistry(workspace=args.workspace)
    reg.scan()

    version = args.version
    if version is None:
        block = reg.get_block(args.name)
        if block is None:
            print(f"Block {args.name!r} not found.")
            sys.exit(1)
        version = block.version

    if reg.remove_block(args.name, version):
        print(f"Removed {args.name}@{version}")
    else:
        print(f"Block {args.name}@{version} not found.")
        sys.exit(1)


def _cmd_pack(args: argparse.Namespace) -> None:
    from dan.blocks.export import pack_block

    tarball = pack_block(args.block_dir)
    print(f"Packed block to {tarball}")


def _cmd_info(args: argparse.Namespace) -> None:
    from dan.blocks.registry import BlockRegistry

    reg = BlockRegistry(workspace=args.workspace)
    reg.scan()

    block = reg.get_block(args.name, args.version)
    if block is None:
        print(f"Block {args.name!r} not found.")
        sys.exit(1)

    m = block.metadata
    print(f"Name:        {m.name}")
    print(f"Version:     {m.version}")
    print(f"Type:        {m.block_type}")
    print(f"Author:      {m.author or '(none)'}")
    print(f"License:     {m.license or '(none)'}")
    print(f"Description: {m.description or '(none)'}")
    if m.tags:
        print(f"Tags:        {', '.join(m.tags)}")
    if m.dependencies:
        deps = ", ".join(f"{d.name}@{d.version}" for d in m.dependencies)
        print(f"Depends on:  {deps}")
    print(f"Install:     {block.install_path}")

    if m.input_schema.get("properties"):
        print("\nInput Schema:")
        print(json.dumps(m.input_schema, indent=2))
    if m.output_schema.get("properties"):
        print("\nOutput Schema:")
        print(json.dumps(m.output_schema, indent=2))

    readme = block.install_path / "README.md"
    if readme.exists():
        print(f"\n--- README ---\n{readme.read_text(encoding='utf-8')}")


# ---------------------------------------------------------------------------
# Table rendering (Rich with plain fallback)
# ---------------------------------------------------------------------------


def _print_table(headers: list[str], rows: list[tuple[str, ...]]) -> None:
    try:
        from rich.console import Console
        from rich.table import Table

        table = Table(show_header=True, header_style="bold cyan")
        for h in headers:
            table.add_column(h)
        for row in rows:
            table.add_row(*row)
        Console().print(table)
    except ImportError:
        widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
        hdr = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
        print(hdr)
        print("  ".join("-" * w for w in widths))
        for row in rows:
            print("  ".join(val.ljust(w) for val, w in zip(row, widths)))


if __name__ == "__main__":
    main()
