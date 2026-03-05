"""dan-run — execute DAN workflows from the terminal.

Supports JSON, markdown, Python, and natural-language goal sources.
Rich TUI when ``rich`` is installed; graceful plain-text fallback.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("dan.cli")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dan-run",
        description="Execute a DAN workflow from the terminal.",
    )
    p.add_argument(
        "source",
        help=(
            "Workflow source: JSON file, markdown directory/file, "
            "Python file, or a natural-language goal string."
        ),
    )
    # -- Configuration -------------------------------------------------------
    p.add_argument("--api-key", dest="api_key", help="LLM API key")
    p.add_argument("--model", help="Default LLM model name")
    p.add_argument("--base-url", dest="base_url", help="LLM base URL")
    p.add_argument(
        "--workspace",
        help="Workspace root directory (default: cwd)",
    )
    # -- Inputs --------------------------------------------------------------
    p.add_argument(
        "--input", "-i",
        dest="inputs",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Workflow input (repeatable). Example: --input topic='AI safety'",
    )
    p.add_argument(
        "--input-json",
        dest="input_json",
        metavar="JSON",
        help="Workflow inputs as a JSON object string",
    )
    # -- Interaction mode ----------------------------------------------------
    p.add_argument(
        "--interactive",
        action="store_true",
        default=None,
        help="Enable interactive HumanNode prompts (default when TTY)",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        help="Auto-skip HumanNode prompts with defaults",
    )
    p.add_argument(
        "--human-timeout",
        type=int,
        default=300,
        metavar="SECS",
        help="Timeout for interactive prompts (default: 300s)",
    )
    p.add_argument(
        "--auto-approve",
        action="store_true",
        help="Skip meta-orchestrator plan confirmation",
    )
    # -- Display mode --------------------------------------------------------
    p.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress TUI; output only final result JSON",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show all engine events",
    )
    p.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    # -- Output / artifacts --------------------------------------------------
    p.add_argument(
        "--output", "-o",
        metavar="PATH",
        help="Write final output JSON to file",
    )
    p.add_argument(
        "--artifacts-dir",
        default="./output",
        metavar="DIR",
        help="Directory for generated artifacts (default: ./output/)",
    )
    # -- Background mode -----------------------------------------------------
    p.add_argument(
        "--background", "--bg",
        action="store_true",
        dest="background",
        help="Run workflow in background; print run ID and exit",
    )
    # -- NL override ---------------------------------------------------------
    p.add_argument(
        "--goal",
        action="store_true",
        help="Force interpretation of source as natural-language goal",
    )
    # -- Server mode ---------------------------------------------------------
    p.add_argument(
        "--local",
        action="store_true",
        help="Force local engine mode (skip server)",
    )
    p.add_argument(
        "--server",
        type=str,
        default=None,
        help="Server URL (default: http://localhost:8000)",
    )
    return p


# ---------------------------------------------------------------------------
# Source detection
# ---------------------------------------------------------------------------

def detect_source_type(source: str, *, force_goal: bool = False) -> str:
    """Classify a source string into json | markdown | python | nl."""
    if force_goal:
        return "nl"
    p = Path(source)
    if p.suffix == ".json" and p.exists():
        return "json"
    if p.suffix == ".md" and p.exists():
        return "markdown"
    if p.is_dir() and p.exists():
        return "markdown"
    if p.suffix == ".py" and p.exists():
        return "python"
    if p.exists():
        ext = p.suffix.lower()
        if ext in (".json",):
            return "json"
        if ext in (".md",):
            return "markdown"
        if ext in (".py",):
            return "python"
    if not p.suffix and not p.exists():
        return "nl"
    if not p.exists():
        if source.endswith(".json"):
            _die(f"File not found: {source}")
        if source.endswith(".md"):
            _die(f"File not found: {source}")
        if source.endswith(".py"):
            _die(f"File not found: {source}")
        return "nl"
    return "nl"


# ---------------------------------------------------------------------------
# Workflow loading
# ---------------------------------------------------------------------------

def load_graph_from_json(path: Path) -> Any:
    from dan.utils.workflow_loader import load_graph_from_json as _load, WorkflowLoadError
    try:
        return _load(path)
    except WorkflowLoadError as e:
        _die(str(e))


def load_graph_from_markdown(path: Path) -> Any:
    from dan.utils.workflow_loader import load_graph_from_markdown as _load, WorkflowLoadError
    try:
        return _load(path)
    except WorkflowLoadError as e:
        _die(str(e))


def load_graph_from_python(path: Path) -> Any:
    from dan.utils.workflow_loader import load_graph_from_python as _load, WorkflowLoadError
    try:
        return _load(path)
    except WorkflowLoadError as e:
        _die(str(e))


def load_workflow(source: str, source_type: str) -> Any:
    """Load a Graph object from the given source."""
    from dan.utils.workflow_loader import load_graph, WorkflowLoadError
    if source_type in ("json", "markdown", "python"):
        try:
            return load_graph(Path(source))
        except WorkflowLoadError as e:
            _die(str(e))
    _die(f"Cannot load workflow for source type: {source_type}")


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

def parse_inputs(
    input_args: list[str],
    input_json: str | None = None,
) -> dict[str, Any]:
    """Merge --input key=value pairs with --input-json."""
    result: dict[str, Any] = {}
    if input_json:
        try:
            parsed = json.loads(input_json)
        except json.JSONDecodeError as exc:
            _die(f"Invalid --input-json: {exc}")
        if not isinstance(parsed, dict):
            _die("--input-json must be a JSON object")
        result.update(parsed)
    for pair in input_args:
        if "=" not in pair:
            _die(f"Invalid --input format (expected key=value): {pair}")
        key, _, value = pair.partition("=")
        result[key.strip()] = value.strip()
    return result


# ---------------------------------------------------------------------------
# CLIHumanRenderer
# ---------------------------------------------------------------------------

class CLIHumanRenderer:
    """Interactive HumanNode renderer for the terminal.

    Conforms to the ``HumanRenderer`` protocol
    (``async render(HumanRenderRequest) -> HumanRenderResponse``).
    """

    def __init__(self, *, timeout: int = 300) -> None:
        self._timeout = timeout
        Console, _ = _try_import_rich()
        self._console = Console(stderr=True) if Console else None

    async def render(self, request: Any) -> Any:
        from dan.engine.executor import HumanRenderResponse

        mode = getattr(request, "render_mode", "text")
        prompt = getattr(request, "prompt", "") or getattr(request, "node_name", "Input needed")
        request_id = getattr(request, "request_id", "")
        default = getattr(request, "default_action", None)
        options = getattr(request, "options", None)
        schema = getattr(request, "output_schema", None)

        try:
            if mode == "approval":
                return await self._render_approval(request_id, prompt, default)
            if mode == "selection" and options:
                return await self._render_selection(request_id, prompt, options, default)
            if mode == "form" and schema:
                return await self._render_form(request_id, prompt, schema, default)
            return await self._render_text(request_id, prompt, default)
        except (EOFError, KeyboardInterrupt):
            if default is not None:
                return HumanRenderResponse(
                    request_id=request_id,
                    data={"response": default},
                    source="default",
                )
            return HumanRenderResponse(
                request_id=request_id,
                data={"response": ""},
                source="timeout",
            )

    async def _render_approval(
        self, request_id: str, prompt: str, default: str | None,
    ) -> Any:
        from dan.engine.executor import HumanRenderResponse
        self._print_header("Approval Required")
        self._print(prompt)
        raw = await self._input_with_timeout("[Y/n] ", self._timeout)
        if raw is None:
            answer = default or "y"
        else:
            answer = raw.strip().lower() or "y"
        approved = answer in ("y", "yes", "true", "1", "approve")
        return HumanRenderResponse(
            request_id=request_id,
            data={"approved": approved, "response": "approve" if approved else "reject"},
            source="human",
        )

    async def _render_selection(
        self, request_id: str, prompt: str, options: list[str], default: str | None,
    ) -> Any:
        from dan.engine.executor import HumanRenderResponse
        self._print_header("Selection")
        self._print(prompt)
        for idx, opt in enumerate(options, 1):
            self._print(f"  {idx}. {opt}")
        raw = await self._input_with_timeout("Enter number: ", self._timeout)
        if raw is None:
            chosen = default or options[0] if options else ""
        else:
            try:
                chosen = options[int(raw.strip()) - 1]
            except (ValueError, IndexError):
                chosen = raw.strip()
        return HumanRenderResponse(
            request_id=request_id,
            data={"response": chosen},
            source="human",
        )

    async def _render_form(
        self, request_id: str, prompt: str, schema: dict, default: str | None,
    ) -> Any:
        from dan.engine.executor import HumanRenderResponse
        self._print_header("Form Input")
        self._print(prompt)
        props = schema.get("properties", {})
        data: dict[str, str] = {}
        for key, spec in props.items():
            desc = spec.get("description", key)
            raw = await self._input_with_timeout(f"  {desc}: ", self._timeout)
            data[key] = raw.strip() if raw else ""
        return HumanRenderResponse(
            request_id=request_id,
            data=data,
            source="human",
        )

    async def _render_text(
        self, request_id: str, prompt: str, default: str | None,
    ) -> Any:
        from dan.engine.executor import HumanRenderResponse
        self._print_header("Input")
        self._print(prompt)
        raw = await self._input_with_timeout("> ", self._timeout)
        if raw is None:
            value = default or ""
            source = "timeout" if default is None else "default"
        else:
            value = raw.strip()
            source = "human"
        return HumanRenderResponse(
            request_id=request_id,
            data={"response": value},
            source=source,
        )

    # -- helpers -------------------------------------------------------------

    async def _input_with_timeout(self, prompt: str, timeout: int) -> str | None:
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, lambda: input(prompt)),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, EOFError):
            return None

    def _print_header(self, title: str) -> None:
        if self._console:
            self._console.rule(f"[bold cyan]{title}[/]")
        else:
            print(f"\n--- {title} ---", file=sys.stderr)

    def _print(self, text: str) -> None:
        if self._console:
            self._console.print(text)
        else:
            print(text, file=sys.stderr)


# ---------------------------------------------------------------------------
# TUI display (Rich)
# ---------------------------------------------------------------------------

class TUIDisplay:
    """Rich-based TUI for showing execution progress."""

    def __init__(self, *, verbose: bool = False) -> None:
        from rich.console import Console
        from rich.table import Table
        from rich.live import Live

        self._console = Console(stderr=True)
        self._verbose = verbose
        self._nodes: dict[str, dict[str, Any]] = {}
        self._total_tokens = 0
        self._total_cost = 0.0
        self._start_time = time.time()
        self._live: Live | None = None
        self._Table = Table
        self._Live = Live
        self._completed_count = 0
        self._total_count = 0
        self._last_text: str = ""

    def start(self, total_nodes: int) -> None:
        self._total_count = total_nodes
        self._live = self._Live(
            self._build_table(),
            console=self._console,
            refresh_per_second=4,
        )
        self._live.start()

    def stop(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None

    def handle_event(self, event: Any) -> None:
        from dan.engine.events import EventType

        et = event.event_type
        nid = event.node_id or ""
        data = event.data

        if et == EventType.NODE_STARTED:
            self._nodes[nid] = {
                "name": data.get("name", nid),
                "type": data.get("node_type", ""),
                "status": "running",
                "start": time.time(),
                "duration": "",
                "tokens": 0,
            }
        elif et == EventType.NODE_COMPLETED:
            if nid in self._nodes:
                elapsed = time.time() - self._nodes[nid].get("start", time.time())
                self._nodes[nid]["status"] = "completed"
                self._nodes[nid]["duration"] = f"{elapsed:.1f}s"
                self._completed_count += 1
        elif et == EventType.NODE_FAILED:
            if nid in self._nodes:
                self._nodes[nid]["status"] = "failed"
                self._completed_count += 1
        elif et == EventType.NODE_SKIPPED:
            if nid in self._nodes:
                self._nodes[nid]["status"] = "skipped"
                self._completed_count += 1
            else:
                self._nodes[nid] = {
                    "name": nid,
                    "type": "",
                    "status": "skipped",
                    "start": time.time(),
                    "duration": "",
                    "tokens": 0,
                }
                self._completed_count += 1
        elif et == EventType.COST_RECORDED:
            tokens = data.get("total_tokens", 0)
            cost = data.get("cost", 0.0)
            self._total_tokens += tokens
            self._total_cost += cost
            if nid in self._nodes:
                self._nodes[nid]["tokens"] += tokens
        elif et == EventType.LLM_THINKING:
            chunk = data.get("text", data.get("chunk", ""))
            if chunk:
                self._last_text = chunk[:200]
        elif et == EventType.INTERMEDIATE_TEXT:
            text = data.get("text", "")
            if text:
                self._last_text = text[:200]

        if self._verbose:
            self._console.print(
                f"[dim]{et.value}[/] node={nid} {json.dumps(data, default=str)[:120]}"
            )

        if self._live:
            self._live.update(self._build_table())

    def _build_table(self) -> Any:
        from rich.table import Table
        from rich.text import Text

        elapsed = time.time() - self._start_time
        table = Table(
            title=f"DAN Run  [{self._completed_count}/{self._total_count}]  {elapsed:.0f}s",
            show_lines=False,
        )
        table.add_column("Node", style="bold")
        table.add_column("Type", style="dim")
        table.add_column("Status")
        table.add_column("Duration", justify="right")
        table.add_column("Tokens", justify="right")

        status_style = {
            "running": "bold yellow",
            "completed": "green",
            "failed": "bold red",
            "skipped": "dim",
            "pending": "dim",
        }

        for _nid, info in self._nodes.items():
            st = info["status"]
            table.add_row(
                info["name"],
                info["type"],
                Text(st, style=status_style.get(st, "")),
                info["duration"],
                str(info["tokens"]) if info["tokens"] else "",
            )
        return table

    def print_summary(self, result: Any) -> None:
        elapsed = time.time() - self._start_time
        self._console.print()
        self._console.rule("[bold]Run Summary")
        self._console.print(f"  Status:  {'[green]success[/]' if result.success else '[red]failed[/]'}")
        self._console.print(f"  Time:    {elapsed:.1f}s")
        self._console.print(f"  Tokens:  {self._total_tokens:,}")
        if self._total_cost > 0:
            self._console.print(f"  Cost:    ${self._total_cost:.4f}")
        ok = sum(1 for n in self._nodes.values() if n["status"] == "completed")
        fail = sum(1 for n in self._nodes.values() if n["status"] == "failed")
        skip = sum(1 for n in self._nodes.values() if n["status"] == "skipped")
        self._console.print(f"  Nodes:   {ok} completed, {fail} failed, {skip} skipped")
        if result.errors:
            self._console.print()
            self._console.print("[bold red]Errors:[/]")
            for nid, err in result.errors.items():
                self._console.print(f"  {nid}: {err}")


class PlainDisplay:
    """Fallback display when Rich is not available."""

    def __init__(self, *, verbose: bool = False) -> None:
        self._verbose = verbose
        self._completed = 0
        self._total = 0
        self._start = time.time()

    def start(self, total_nodes: int) -> None:
        self._total = total_nodes
        print(f"Starting DAN run ({total_nodes} nodes)...", file=sys.stderr)

    def stop(self) -> None:
        pass

    def handle_event(self, event: Any) -> None:
        from dan.engine.events import EventType

        et = event.event_type
        nid = event.node_id or ""
        if et == EventType.NODE_COMPLETED:
            self._completed += 1
            print(f"  [{self._completed}/{self._total}] {nid} completed", file=sys.stderr)
        elif et == EventType.NODE_FAILED:
            self._completed += 1
            err = event.data.get("error", "unknown")
            print(f"  [{self._completed}/{self._total}] {nid} FAILED: {err}", file=sys.stderr)
        elif self._verbose:
            print(f"  {et.value} node={nid}", file=sys.stderr)

    def print_summary(self, result: Any) -> None:
        elapsed = time.time() - self._start
        status = "SUCCESS" if result.success else "FAILED"
        print(f"\n{status} ({elapsed:.1f}s)", file=sys.stderr)
        if result.errors:
            for nid, err in result.errors.items():
                print(f"  ERROR {nid}: {err}", file=sys.stderr)


class QuietDisplay:
    """No display — only outputs final JSON."""

    def start(self, total_nodes: int) -> None:
        pass

    def stop(self) -> None:
        pass

    def handle_event(self, event: Any) -> None:
        pass

    def print_summary(self, result: Any) -> None:
        pass


class JSONLDisplay:
    """Outputs engine events as JSONL to stdout."""

    def start(self, total_nodes: int) -> None:
        pass

    def stop(self) -> None:
        pass

    def handle_event(self, event: Any) -> None:
        print(json.dumps(event.to_dict(), default=str), flush=True)

    def print_summary(self, result: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Display factory
# ---------------------------------------------------------------------------

def make_display(
    *, quiet: bool, verbose: bool, output_format: str,
) -> TUIDisplay | PlainDisplay | QuietDisplay | JSONLDisplay:
    if output_format == "json":
        return JSONLDisplay()
    if quiet:
        return QuietDisplay()
    Console, _ = _try_import_rich()
    if Console:
        return TUIDisplay(verbose=verbose)
    return PlainDisplay(verbose=verbose)


# ---------------------------------------------------------------------------
# Background mode
# ---------------------------------------------------------------------------

def run_background(argv: list[str]) -> None:
    """Spawn a new process in background, save PID to ~/.dan/runs/."""
    from dan.cli import ensure_dan_dir
    runs_dir = ensure_dan_dir()

    run_id = str(uuid.uuid4())[:8]
    log_path = runs_dir / f"{run_id}.log"
    pid_path = runs_dir / f"{run_id}.pid"
    events_path = runs_dir / f"{run_id}.events.jsonl"

    filtered = [a for a in argv if a not in ("--background", "--bg")]
    filtered.extend(["--output-format", "json", "--quiet"])

    with open(log_path, "w") as log_f, open(events_path, "w"):
        proc = subprocess.Popen(
            [sys.executable, "-m", "dan.cli.run"] + filtered,
            stdout=open(events_path, "w"),
            stderr=log_f,
            start_new_session=True,
        )
    pid_path.write_text(f"{proc.pid}\n{run_id}\n")
    print(f"Background run started: {run_id} (PID {proc.pid})")
    print(f"  Events: {events_path}")
    print(f"  Logs:   {log_path}")
    print(f"  Status: dan-status")
    print(f"  Tail:   dan-logs {run_id} --follow")


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

_shutdown_requested = False


def _signal_handler(signum: int, frame: Any) -> None:
    global _shutdown_requested
    if _shutdown_requested:
        sys.exit(1)
    _shutdown_requested = True
    print("\nGraceful shutdown requested (Ctrl+C again to force)...", file=sys.stderr)


# ---------------------------------------------------------------------------
# Server mode execution
# ---------------------------------------------------------------------------

async def _run_server_mode(
    *,
    client: Any,
    source: str,
    source_type: str,
    inputs: dict[str, Any],
    display: Any,
    interactive: bool,
    human_timeout: int,
) -> int:
    """Run workflow via DanClientOrLocal in server mode."""
    from dan.engine.events import EngineEvent

    if source_type == "nl":
        result = await client.dispatch(text=source, surface_id="cli")
    else:
        result = await client.dispatch(
            workflow_path=str(Path(source).resolve()),
            inputs=inputs or None,
            surface_id="cli",
        )

    run_id = result.run_id

    if hasattr(display, "start"):
        display.start(0)

    exit_code = 0
    async for raw_event in client.subscribe_run(run_id):
        event = EngineEvent.from_dict(raw_event)
        display.handle_event(event)

        if event.event_type.value == "human_input_needed" and interactive:
            data = event.data or {}
            request_id = data.get("request_id")
            prompt = data.get("prompt", "Input required:")
            print(f"\n{prompt}")
            try:
                loop = asyncio.get_running_loop()
                user_input = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, lambda: input("> ")
                    ),
                    timeout=human_timeout,
                )
                if not request_id:
                    print(
                        "Missing request_id for human input event; cancelling run.",
                        file=sys.stderr,
                    )
                    await client.cancel_run(run_id)
                    return 1
                submitted = await client.submit_human_input(
                    run_id,
                    request_id,
                    {"response": user_input},
                )
                if not submitted:
                    print(
                        "Human input request is no longer pending; cancelling run.",
                        file=sys.stderr,
                    )
                    await client.cancel_run(run_id)
                    return 1
            except asyncio.TimeoutError:
                print(
                    f"\nHuman input timed out after {human_timeout}s; cancelling run.",
                    file=sys.stderr,
                )
                await client.cancel_run(run_id)
                return 1
            except (EOFError, KeyboardInterrupt):
                await client.cancel_run(run_id)
                return 1

        if event.event_type.value in ("run_completed", "run_failed", "run_cancelled"):
            if event.event_type.value != "run_completed":
                exit_code = 1
            break

    if hasattr(display, "stop"):
        display.stop()

    return exit_code


# ---------------------------------------------------------------------------
# Core execution
# ---------------------------------------------------------------------------

async def run_workflow(args: argparse.Namespace) -> int:
    """Execute the workflow and return exit code."""
    from dan.cli import load_env, resolve_config

    load_env()
    cfg = resolve_config(
        api_key=args.api_key,
        model=args.model,
        base_url=args.base_url,
        workspace=args.workspace,
    )

    source_type = detect_source_type(args.source, force_goal=args.goal)
    inputs = parse_inputs(args.inputs, args.input_json)

    is_interactive = args.interactive
    if is_interactive is None:
        is_interactive = not args.headless and sys.stdin.isatty()

    display = make_display(
        quiet=args.quiet,
        verbose=args.verbose,
        output_format=args.output_format,
    )

    # -- Server mode detection -----------------------------------------------
    force_local = getattr(args, "local", False)
    server_url = getattr(args, "server", None)

    if not force_local:
        from dan.client.local import DanClientOrLocal

        client = DanClientOrLocal(server_url=server_url)
        mode = await client.detect_mode()
        if mode == "server":
            if not args.quiet:
                print(
                    f"Connected to dan-serve at {server_url or 'localhost:8000'}",
                    file=sys.stderr,
                )
            try:
                return await _run_server_mode(
                    client=client,
                    source=args.source,
                    source_type=source_type,
                    inputs=inputs,
                    display=display,
                    interactive=is_interactive,
                    human_timeout=args.human_timeout,
                )
            finally:
                await client.close()
        else:
            if not args.quiet:
                print(
                    "dan-serve not detected \u2014 running in local mode.",
                    file=sys.stderr,
                )

    # -- NL goal path -------------------------------------------------------
    if source_type == "nl":
        return await _run_nl_goal(
            goal=args.source,
            cfg=cfg,
            inputs=inputs,
            display=display,
            interactive=is_interactive,
            auto_approve=args.auto_approve,
            human_timeout=args.human_timeout,
            args=args,
        )

    # -- Workflow file path --------------------------------------------------
    graph = load_workflow(args.source, source_type)
    return await _run_graph(
        graph=graph,
        cfg=cfg,
        inputs=inputs,
        display=display,
        interactive=is_interactive,
        human_timeout=args.human_timeout,
        args=args,
    )


async def _run_graph(
    *,
    graph: Any,
    cfg: dict[str, Any],
    inputs: dict[str, Any],
    display: Any,
    interactive: bool,
    human_timeout: int,
    args: argparse.Namespace,
) -> int:
    """Execute a loaded Graph and return exit code."""
    from dan.engine import Engine, EngineConfig, AutoRenderer

    engine_config = EngineConfig(
        llm_api_key=cfg["api_key"],
        llm_base_url=cfg["base_url"] or "https://api.vectorengine.ai/v1",
        llm_default_model=cfg["model"] or "claude-sonnet-4-6",
    )

    renderer: Any
    if interactive:
        renderer = CLIHumanRenderer(timeout=human_timeout)
    else:
        renderer = AutoRenderer()

    total_nodes = len(graph.nodes) if hasattr(graph, "nodes") else 0

    async def event_cb(event: Any) -> None:
        if _shutdown_requested:
            return
        display.handle_event(event)

    engine = Engine(
        config=engine_config,
        event_callback=event_cb,
        human_renderer=renderer,
    )

    display.start(total_nodes)
    try:
        result = await engine.run(graph, inputs=inputs or None)
    except KeyboardInterrupt:
        display.stop()
        print("\nRun interrupted.", file=sys.stderr)
        return 130
    finally:
        display.stop()

    display.print_summary(result)
    _handle_output(result, args)
    return 0 if result.success else 1


async def _run_nl_goal(
    *,
    goal: str,
    cfg: dict[str, Any],
    inputs: dict[str, Any],
    display: Any,
    interactive: bool,
    auto_approve: bool,
    human_timeout: int,
    args: argparse.Namespace,
) -> int:
    """Natural-language goal: use MetaController to plan and execute."""
    from dan.engine import EngineConfig

    engine_config = EngineConfig(
        llm_api_key=cfg["api_key"],
        llm_base_url=cfg["base_url"] or "https://api.vectorengine.ai/v1",
        llm_default_model=cfg["model"] or "claude-sonnet-4-6",
    )

    Console, _ = _try_import_rich()
    console = Console(stderr=True) if Console else None

    def _info(msg: str) -> None:
        if console:
            console.print(f"[bold blue]>[/] {msg}")
        elif not args.quiet:
            print(f"> {msg}", file=sys.stderr)

    _info(f"Interpreting as natural-language goal: {goal!r}")
    _info("Planning workflow...")

    try:
        from dan.meta import MetaController, MetaControllerConfig

        meta_cfg = MetaControllerConfig(
            pause_before_execute=interactive and not auto_approve,
        )
        controller = MetaController()
        session = await controller.create_session(goal, meta_cfg)

        if interactive and not auto_approve:
            _info("Plan created. Proceed? [Y/n]")
            answer = input().strip().lower()
            if answer and answer not in ("y", "yes"):
                _info("Aborted.")
                return 1

        session = await controller.run_session(session, meta_cfg)

        from dan.meta.controller import MetaSessionStatus
        if session.status == MetaSessionStatus.COMPLETED:
            _info("Meta session completed successfully.")
            if session.final_output:
                _handle_meta_output(session.final_output, args)
            return 0
        elif session.status == MetaSessionStatus.FAILED:
            _info(f"Meta session failed: {session.error_context or 'unknown error'}")
            if session.repair_history:
                last = session.repair_history[-1]
                _info(f"Last repair: {last.get('level', '?')} — {last.get('summary', '?')}")
            return 1
        else:
            _info(f"Meta session ended with status: {session.status.value}")
            return 1
    except ImportError:
        _die("Meta-orchestrator not available. Please specify a workflow file.")
    except Exception as exc:
        _die(f"Meta-orchestrator error: {exc}")
    return 1


# ---------------------------------------------------------------------------
# Output handling
# ---------------------------------------------------------------------------

def _handle_output(result: Any, args: argparse.Namespace) -> None:
    output_data = {
        "run_id": result.run_id,
        "success": result.success,
        "outputs": result.outputs,
        "errors": result.errors,
        "node_statuses": result.node_statuses,
    }

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2, default=str)
        print(f"Output written to: {args.output}", file=sys.stderr)
    elif args.quiet or args.output_format == "json":
        print(json.dumps(output_data, indent=2, default=str))
    else:
        Console, _ = _try_import_rich()
        if Console:
            c = Console()
            if result.outputs:
                c.print("\n[bold]Outputs:[/]")
                c.print_json(json.dumps(result.outputs, default=str))
        else:
            if result.outputs:
                print("\nOutputs:")
                print(json.dumps(result.outputs, indent=2, default=str))

    artifacts_dir = Path(args.artifacts_dir)
    if artifacts_dir.exists() and any(artifacts_dir.iterdir()):
        _print_artifact_manifest(artifacts_dir)


def _handle_meta_output(output: dict[str, Any], args: argparse.Namespace) -> None:
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2, default=str)
        print(f"Output written to: {args.output}", file=sys.stderr)
    elif args.quiet or args.output_format == "json":
        print(json.dumps(output, indent=2, default=str))
    else:
        Console, _ = _try_import_rich()
        if Console:
            c = Console()
            c.print("\n[bold]Meta Output:[/]")
            c.print_json(json.dumps(output, default=str))
        else:
            print("\nMeta Output:")
            print(json.dumps(output, indent=2, default=str))


def _print_artifact_manifest(artifacts_dir: Path) -> None:
    files = sorted(artifacts_dir.rglob("*"))
    files = [f for f in files if f.is_file()]
    if not files:
        return
    Console, _ = _try_import_rich()
    if Console:
        c = Console(stderr=True)
        c.print("\n[bold]Artifacts:[/]")
        for f in files:
            size = f.stat().st_size
            c.print(f"  {f.relative_to(artifacts_dir)}  ({_human_size(size)})")
    else:
        print("\nArtifacts:", file=sys.stderr)
        for f in files:
            size = f.stat().st_size
            print(f"  {f.relative_to(artifacts_dir)}  ({_human_size(size)})", file=sys.stderr)


def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.0f}{unit}"
        nbytes /= 1024
    return f"{nbytes:.1f}TB"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _try_import_rich():
    try:
        from rich.console import Console
        import rich
        return Console, rich
    except ImportError:
        return None, None


def _die(msg: str) -> None:
    print(f"dan-run: error: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = build_parser()

    if len(sys.argv) < 2:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()

    if args.background:
        run_background(sys.argv[1:])
        return

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(name)s: %(message)s",
    )

    exit_code = asyncio.run(run_workflow(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
