"""dan-furnace — dedicated CLI portal for furnace sessions.

Bypasses conversational routing and talks to furnace API endpoints directly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

_DEFAULT_SERVER = os.environ.get("DAN_SERVER_URL", "http://127.0.0.1:8000").rstrip("/")


def _die(message: str, exit_code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(exit_code)


def _fmt_status_line(event: dict[str, Any]) -> str:
    et = str(event.get("type", "")).strip()
    if et == "source_status":
        sid = str(event.get("source_id", "?"))
        st = str(event.get("status", "?"))
        reason = str(event.get("reason", "")).strip()
        return f"[source] {sid}: {st}" + (f" ({reason})" if reason else "")
    if et == "source_chunk":
        sid = str(event.get("source_id", "?"))
        start_page = event.get("start_page")
        end_page = event.get("end_page")
        summary = str(event.get("summary", "")).strip()
        base = f"[chunk] {sid}: pages {start_page}-{end_page}"
        return f"{base} | {summary}" if summary else base
    if et in {"phase_started", "phase_completed", "phase_failed"}:
        phase = str(event.get("phase", "?"))
        msg = f"[phase] {phase}: {et.replace('phase_', '')}"
        error = str(event.get("error", "")).strip()
        return f"{msg} ({error})" if error else msg
    if et.startswith("session_"):
        err = str(event.get("error", "")).strip()
        return f"[session] {et}" + (f" ({err})" if err else "")
    if et == "budget_exceeded":
        return f"[budget] exceeded at ${event.get('total_cost_usd', '?')}"
    return json.dumps(event, ensure_ascii=False)


class FurnaceClient:
    def __init__(self, server: str, timeout_seconds: float = 60.0) -> None:
        self.server = server.rstrip("/")
        self._http = httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        self._http.close()

    def _url(self, path: str) -> str:
        return f"{self.server}{path}"

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self._http.get(self._url(path), params=params)
        self._ensure_ok(resp)
        return self._parse_json(resp)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self._http.post(self._url(path), json=payload or {})
        self._ensure_ok(resp)
        return self._parse_json(resp)

    def watch(self, session_id: str, *, timeout_seconds: float = 0.0) -> int:
        url = self._url(f"/api/furnace/sessions/{session_id}/events")
        start = time.monotonic()
        terminal = {
            "session_completed",
            "session_failed",
            "session_cancelled",
        }
        print(f"Watching session {session_id} events...")
        with self._http.stream("GET", url, headers={"Accept": "text/event-stream"}) as resp:
            self._ensure_ok(resp)
            for raw_line in resp.iter_lines():
                if not raw_line:
                    if timeout_seconds > 0 and (time.monotonic() - start) >= timeout_seconds:
                        print(f"Watch timeout reached ({timeout_seconds:.0f}s).")
                        return 124
                    continue
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    print(data)
                    continue
                print(_fmt_status_line(event))
                if str(event.get("type", "")).strip() in terminal:
                    return 0
                if timeout_seconds > 0 and (time.monotonic() - start) >= timeout_seconds:
                    print(f"Watch timeout reached ({timeout_seconds:.0f}s).")
                    return 124
        return 0

    @staticmethod
    def _parse_json(resp: httpx.Response) -> dict[str, Any]:
        try:
            payload = resp.json()
        except Exception as exc:  # pragma: no cover - defensive parsing
            _die(f"Non-JSON response from server: {exc}")
        if not isinstance(payload, dict):
            _die("Unexpected API response shape (expected object).")
        return payload

    @staticmethod
    def _ensure_ok(resp: httpx.Response) -> None:
        if 200 <= resp.status_code < 300:
            return
        detail = ""
        try:
            payload = resp.json()
            if isinstance(payload, dict):
                detail = str(payload.get("detail", "")).strip()
        except Exception:
            detail = ""
        text = detail or resp.text.strip() or f"HTTP {resp.status_code}"
        _die(f"API error {resp.status_code}: {text}")


def _add_sources_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "source_ids": list(args.source_id or []),
        "pdf_paths": [os.path.expanduser(p) for p in list(args.pdf or [])],
        "urls": list(args.url or []),
    }


def _split_sources_positional(values: list[str]) -> tuple[list[str], list[str], list[str]]:
    source_ids: list[str] = []
    pdfs: list[str] = []
    urls: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        low = text.lower()
        if low.startswith(("http://", "https://")):
            urls.append(text)
            continue
        expanded = os.path.expanduser(text)
        looks_like_pdf = low.endswith(".pdf")
        looks_like_path = (
            os.path.isabs(expanded)
            or text.startswith("~/")
            or "/" in text
            or "\\" in text
        )
        if looks_like_pdf or looks_like_path:
            pdfs.append(expanded)
            continue
        source_ids.append(text)
    return source_ids, pdfs, urls


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _cmd_ignite(client: FurnaceClient, args: argparse.Namespace) -> int:
    create_payload = {
        "name": args.name or args.topic or "",
        "topic": args.topic or "",
        "description": args.description or "",
        "target_count": int(args.target_count),
    }
    created = client.post("/api/furnace/sessions", create_payload)
    session = created.get("session") or {}
    if not isinstance(session, dict):
        _die("Invalid create-session response: missing session object.")
    session_id = str(session.get("session_id", "")).strip()
    if not session_id:
        _die("Invalid create-session response: missing session_id.")

    added = None
    sources_payload = _add_sources_payload(args)
    if sources_payload["source_ids"] or sources_payload["pdf_paths"] or sources_payload["urls"]:
        added = client.post(f"/api/furnace/sessions/{session_id}/sources", sources_payload)

    if args.budget is not None:
        client.post(
            f"/api/furnace/sessions/{session_id}/budget",
            {"budget_limit_usd": float(args.budget)},
        )

    started = client.post(f"/api/furnace/sessions/{session_id}/start", {})
    print(f"Session started: {session_id} | status={started.get('status', 'unknown')}")
    if added is not None:
        print(f"Sources added: {added.get('added', 0)} (total={added.get('total_sources', 0)})")

    if args.json:
        _print_json({
            "created": created,
            "added": added,
            "started": started,
        })

    if args.watch:
        return client.watch(session_id, timeout_seconds=float(args.watch_timeout))
    return 0


def _cmd_add(client: FurnaceClient, args: argparse.Namespace) -> int:
    result = client.post(
        f"/api/furnace/sessions/{args.session_id}/sources",
        _add_sources_payload(args),
    )
    _print_json(result) if args.json else print(
        f"Added {result.get('added', 0)} sources to {result.get('session_id', args.session_id)}",
    )
    return 0


def _cmd_run_simple(client: FurnaceClient, args: argparse.Namespace) -> int:
    source_ids, pdfs, urls = _split_sources_positional(list(args.sources or []))
    shim = argparse.Namespace(
        name=args.name,
        topic=args.topic,
        description=args.description,
        target_count=args.target_count,
        source_id=source_ids,
        pdf=pdfs,
        url=urls,
        budget=args.budget,
        watch=args.watch,
        watch_timeout=args.watch_timeout,
        json=args.json,
    )
    return _cmd_ignite(client, shim)


def _cmd_start_like(client: FurnaceClient, args: argparse.Namespace, action: str) -> int:
    result = client.post(f"/api/furnace/sessions/{args.session_id}/{action}", {})
    _print_json(result) if args.json else print(
        f"{action} -> {result.get('status', 'unknown')} ({result.get('session_id', args.session_id)})",
    )
    if action in {"start", "resume"} and args.watch:
        return client.watch(args.session_id, timeout_seconds=float(args.watch_timeout))
    return 0


def _cmd_status(client: FurnaceClient, args: argparse.Namespace) -> int:
    result = client.get(f"/api/furnace/sessions/{args.session_id}")
    if args.json:
        _print_json(result)
        return 0
    session = result.get("session") or {}
    if not isinstance(session, dict):
        _die("Invalid session payload.")
    print(f"session_id: {session.get('session_id', args.session_id)}")
    print(f"name:       {session.get('name', '')}")
    print(f"topic:      {session.get('topic', '')}")
    print(f"status:     {session.get('status', '')}")
    print(f"phase:      {session.get('current_phase', '')}")
    pq = session.get("paper_queue") or {}
    print(f"sources:    {len(pq) if isinstance(pq, dict) else 0}")
    print(f"cost_usd:   {session.get('total_cost_usd', 0.0)}")
    return 0


def _cmd_list(client: FurnaceClient, args: argparse.Namespace) -> int:
    params: dict[str, Any] = {}
    if args.status:
        params["status"] = args.status
    result = client.get("/api/furnace/sessions", params=params)
    sessions = result.get("sessions") or []
    if args.json:
        _print_json(result)
        return 0
    if not sessions:
        print("No furnace sessions found.")
        return 0
    for s in sessions:
        if not isinstance(s, dict):
            continue
        print(
            f"{s.get('session_id','?')}  {s.get('status','?'):<10}  "
            f"{s.get('name','')[:30]:<30}  sources={s.get('source_count', 0)}  "
            f"processed={s.get('processed_count', 0)}",
        )
    return 0


def _cmd_estimate(client: FurnaceClient, args: argparse.Namespace) -> int:
    result = client.get(f"/api/furnace/sessions/{args.session_id}/estimate")
    _print_json(result)
    return 0


def _cmd_budget(client: FurnaceClient, args: argparse.Namespace) -> int:
    result = client.post(
        f"/api/furnace/sessions/{args.session_id}/budget",
        {"budget_limit_usd": float(args.amount)},
    )
    _print_json(result) if args.json else print(
        f"budget_limit_usd={result.get('budget_limit_usd')} for {result.get('session_id', args.session_id)}",
    )
    return 0


def _cmd_recipe(client: FurnaceClient, args: argparse.Namespace) -> int:
    result = client.get(f"/api/furnace/sessions/{args.session_id}/recipe")
    recipe_md = str(result.get("recipe_md") or "")
    skill_md = str(result.get("skill_md") or "")
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        if recipe_md:
            (out_dir / "recipe.md").write_text(recipe_md, encoding="utf-8")
        if skill_md:
            (out_dir / "skill.md").write_text(skill_md, encoding="utf-8")
        print(f"Exported recipe artifacts to {out_dir}")
        return 0
    if recipe_md:
        print("\n# recipe.md\n")
        print(recipe_md)
    if skill_md:
        print("\n# skill.md\n")
        print(skill_md)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dan-furnace",
        description="Dedicated furnace control-plane CLI (direct API, no concierge routing).",
        epilog=(
            "Examples:\n"
            "  dan-furnace run \"supply chain risk\" /abs/path/paper.pdf --watch\n"
            "  dan-furnace run \"topic\" https://example.com/post --budget 200\n"
            "  dan-furnace status <session_id>"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--server", default=_DEFAULT_SERVER, help=f"Server URL (default: {_DEFAULT_SERVER})")
    p.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout seconds (default: 60)")
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON where supported")

    sub = p.add_subparsers(dest="command", required=True)

    ignite = sub.add_parser("ignite", help="Create session, add sources, optionally set budget, and start.")
    ignite.add_argument("--name", default="", help="Session display name")
    ignite.add_argument("--topic", default="", help="Session topic")
    ignite.add_argument("--description", default="", help="Session description")
    ignite.add_argument("--target-count", type=int, default=10, help="Target source count (default: 10)")
    ignite.add_argument("--source-id", action="append", default=[], help="Logical source id (repeatable)")
    ignite.add_argument("--pdf", action="append", default=[], help="PDF path (absolute or ~/..., repeatable)")
    ignite.add_argument("--url", action="append", default=[], help="URL source (repeatable)")
    ignite.add_argument("--budget", type=float, default=None, help="Budget ceiling USD")
    ignite.add_argument("--watch", action="store_true", help="Watch SSE events after start")
    ignite.add_argument("--watch-timeout", type=float, default=0.0, help="Optional watch timeout seconds")

    add = sub.add_parser("add", help="Add sources to an existing session.")
    add.add_argument("session_id")
    add.add_argument("--source-id", action="append", default=[], help="Logical source id (repeatable)")
    add.add_argument("--pdf", action="append", default=[], help="PDF path (repeatable)")
    add.add_argument("--url", action="append", default=[], help="URL source (repeatable)")

    start = sub.add_parser("start", help="Start a paused session.")
    start.add_argument("session_id")
    start.add_argument("--watch", action="store_true", help="Watch SSE events after start")
    start.add_argument("--watch-timeout", type=float, default=0.0, help="Optional watch timeout seconds")

    resume = sub.add_parser("resume", help="Resume a paused/failed session.")
    resume.add_argument("session_id")
    resume.add_argument("--watch", action="store_true", help="Watch SSE events after resume")
    resume.add_argument("--watch-timeout", type=float, default=0.0, help="Optional watch timeout seconds")

    pause = sub.add_parser("pause", help="Pause an active session.")
    pause.add_argument("session_id")

    cancel = sub.add_parser("cancel", help="Cancel a session.")
    cancel.add_argument("session_id")

    watch = sub.add_parser("watch", help="Watch a session SSE stream.")
    watch.add_argument("session_id")
    watch.add_argument("--watch-timeout", type=float, default=0.0, help="Optional watch timeout seconds")

    status = sub.add_parser("status", help="Get full session details.")
    status.add_argument("session_id")

    ls = sub.add_parser("list", help="List sessions.")
    ls.add_argument("--status", default=None, help="Optional status filter")

    est = sub.add_parser("estimate", help="Cost estimate for a session.")
    est.add_argument("session_id")

    budget = sub.add_parser("budget", help="Set session budget.")
    budget.add_argument("session_id")
    budget.add_argument("amount", type=float)

    recipe = sub.add_parser("recipe", help="Get recipe artifacts.")
    recipe.add_argument("session_id")
    recipe.add_argument("--out-dir", default="", help="Write recipe.md/skill.md to this directory")

    run = sub.add_parser(
        "run",
        help="Simple one-liner: run <topic> [sources...] (auto-detect pdf/url/source-id).",
    )
    run.add_argument("topic", help="Session topic")
    run.add_argument("sources", nargs="*", help="Sources (PDF paths, URLs, or source IDs)")
    run.add_argument("--name", default="", help="Optional session display name")
    run.add_argument("--description", default="", help="Optional session description")
    run.add_argument("--target-count", type=int, default=10, help="Target source count (default: 10)")
    run.add_argument("--budget", type=float, default=None, help="Budget ceiling USD")
    run.add_argument("--watch", action="store_true", help="Watch SSE events after start")
    run.add_argument("--watch-timeout", type=float, default=0.0, help="Optional watch timeout seconds")

    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    client = FurnaceClient(args.server, timeout_seconds=float(args.timeout))
    try:
        cmd = args.command
        if cmd == "ignite":
            code = _cmd_ignite(client, args)
        elif cmd == "add":
            code = _cmd_add(client, args)
        elif cmd in {"start", "resume", "pause", "cancel"}:
            code = _cmd_start_like(client, args, cmd)
        elif cmd == "watch":
            code = client.watch(args.session_id, timeout_seconds=float(args.watch_timeout))
        elif cmd == "status":
            code = _cmd_status(client, args)
        elif cmd == "list":
            code = _cmd_list(client, args)
        elif cmd == "estimate":
            code = _cmd_estimate(client, args)
        elif cmd == "budget":
            code = _cmd_budget(client, args)
        elif cmd == "recipe":
            code = _cmd_recipe(client, args)
        elif cmd == "run":
            code = _cmd_run_simple(client, args)
        else:  # pragma: no cover - argparse enforces commands
            _die(f"Unknown command: {cmd}")
        raise SystemExit(code)
    finally:
        client.close()


if __name__ == "__main__":
    main()
