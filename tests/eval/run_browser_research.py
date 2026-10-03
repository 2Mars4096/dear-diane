"""Opt-in real-browser acceptance: python -m tests.eval.run_browser_research [--scholar].

Uses Diane's actual tool/controller path. Add --native codex to exercise a live
lead; combining it with --scholar gives that lead the real Scholar request.
The deterministic local fixture exercises
search, citation navigation, tab selection, extraction, and downloads. Scholar is
a separate external acceptance result, never substituted with fixture evidence.
"""
from __future__ import annotations

import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import quote
from uuid import uuid4

from diane.tools._browser_session import browser_scope
from diane.tools.browser_open import browser_open
from diane.tools.browser_inspect import browser_inspect
from diane.tools.browser_fill import browser_fill
from diane.tools.browser_click import browser_click
from diane.tools.browser_extract import browser_extract
from diane.tools.browser_download import browser_download
from diane.tools.browser_tabs import browser_tabs
from diane.tools.browser_wait import browser_wait
from diane.tools.browser_screenshot import browser_screenshot

BIB = '@article{fixture2026,\n title={Browser Research Fixture},\n author={Example, A.},\n year={2026}\n}\n'


async def run_in_app(base_url, request, backend, events):
    """Exercise the same admission/execution endpoint used by the GUI."""
    import urllib.request
    from diane.server.chat_v2 import AgentRunEvent
    from diane.server.chat_v2_backend import AgentBackendRunResult
    def call(path, payload=None):
        req = urllib.request.Request(base_url.rstrip("/") + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)
    admitted = await asyncio.to_thread(call, "/api/v2/agent-runs/admit", {
        "turn": {"id": request.run_id, "text": request.objective,
                 "workspace_root": request.workspace_root, "workspace_id": request.workspace_root,
                 "surface_type": "editor", "session_id": request.thread_id,
                 "thread_id": request.thread_id, "privacy_scope": "private"},
        "background": True,
        "execute": {"backend": "native_codex" if backend == "codex" else backend,
                    "profile_policy": request.profile_policy},
    })
    run_id = admitted["admission"].get("run_id")
    if not run_id or admitted["admission"]["action"] != "start_parallel":
        raise RuntimeError(f"Test request was not started: {admitted['admission']}")
    print(f"App browser test run: {run_id}", flush=True)
    try:
        async with asyncio.timeout(240):
            while True:
                run = (await asyncio.to_thread(call, f"/api/v2/agent-runs/{run_id}"))["run"]
                if run["status"] in {"completed", "failed", "blocked", "stopped", "cancelled"}:
                    break
                await asyncio.sleep(2)
    except BaseException:
        await asyncio.to_thread(call, f"/api/v2/agent-runs/{run_id}/commands", {"command": "stop"})
        raise
    rows = (await asyncio.to_thread(call, f"/api/v2/agent-runs/{run_id}/events"))["events"]
    events.extend(AgentRunEvent.model_validate(row) for row in rows)
    summary = next((e.summary for e in reversed(events) if e.summary and
                    (e.type in {"completed", "failed", "blocked"} or
                     e.payload.get("status") in {"completed", "failed", "blocked"})), "")
    return AgentBackendRunResult(status=run["status"], backend=backend, summary=summary)


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/download"):
            body, content_type = BIB, "application/x-bibtex"
        elif self.path.startswith("/bib"):
            body, content_type = BIB, "text/plain"
        elif self.path.startswith("/search"):
            body = '<h1>Browser Research Fixture</h1><button id="cite" onclick="document.querySelector(\'dialog\').showModal()">Cite</button><dialog><a id="bib" href="/bib" target="_blank">BibTeX</a><a id="download" href="/download" download="fixture.bib">Download</a></dialog>'
            content_type = "text/html"
        else:
            body, content_type = '<form action="/search"><input name="q" id="q"><button id="search">Search</button></form>', "text/html"
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        if self.path.startswith("/download"):
            self.send_header("Content-Disposition", 'attachment; filename="fixture.bib"')
        self.end_headers()
        self.wfile.write(body.encode())


async def fixture_check(workspace, run_id):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async with browser_scope(workspace, run_id) as scope:
            opened = await browser_open(url=f"http://127.0.0.1:{server.server_port}")
            page = await browser_inspect()
            assert any(e["selector"] == "#q" for e in page["elements"])
            await browser_fill(selector="#q", text="Browser Research Fixture")
            await browser_click(selector="#search")
            await browser_wait(selector="#cite")
            page = await browser_inspect()
            assert any(e["selector"] == "#cite" for e in page["elements"])
            await browser_click(selector="#cite")
            page = await browser_inspect()
            assert any(e["selector"] == "#bib" for e in page["elements"])
            await browser_click(selector="#bib")
            for _ in range(30):
                tabs = await browser_tabs()
                if tabs["count"] == 2:
                    break
                await asyncio.sleep(.1)
            assert tabs["count"] == 2
            await browser_tabs(index=1)
            extracted = await browser_extract()
            assert extracted["text"].strip() == BIB.strip()
            screenshot = await browser_screenshot()
            await browser_tabs(index=0)
            downloaded = await browser_download(selector="#download", destination_path=f"fixture-{run_id}.bib")
            assert downloaded["downloaded"] and Path(downloaded["path"]).read_text() == BIB
            controller = scope.controller
            for tab in list(controller._context.pages):
                await tab.close()
            try:
                await browser_extract()
            except RuntimeError as exc:
                assert "browser_open" in str(exc)
            else:
                raise AssertionError("A closed window must not silently replay browser actions")
            await browser_open(url=f"http://127.0.0.1:{server.server_port}")
            assert any(e["selector"] == "#q" for e in (await browser_inspect())["elements"])
        assert controller._context is None and controller._playwright is None
        return {"status": "passed", "session": opened["session"], "download": downloaded["path"], "screenshot": screenshot, "browser_closed": True}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


async def scholar_check(workspace, run_id):
    query = "Attention Is All You Need"
    async with browser_scope(workspace, run_id):
        await browser_open(url="https://scholar.google.com/scholar?hl=en&q=" + quote(query))
        page = await browser_inspect(element_limit=250)
        cite = next((e for e in page["elements"] if e.get("visible") and e.get("text") == "Cite"), None)
        if not cite:
            return {"status": "blocked", "stage": "search", "url": page["url"], "text": (await browser_extract(max_length=1800))["text"], "screenshot": await browser_screenshot()}
        await browser_click(selector=cite["selector"])
        for _ in range(20):
            page = await browser_inspect(element_limit=250)
            bib = next((e for e in page["elements"] if e.get("text") == "BibTeX" and e.get("href")), None)
            if bib:
                break
            await asyncio.sleep(.25)
        if not bib:
            return {"status": "blocked", "stage": "citation dialog", "text": (await browser_extract(max_length=1800))["text"]}
        from urllib.parse import urljoin
        await browser_open(url=urljoin(page["url"], bib["href"]))
        text = (await browser_extract())["text"]
        if not text.lstrip().startswith("@") or query.lower() not in text.lower():
            return {"status": "blocked", "stage": "BibTeX export", "text": text[:1800]}
        path = workspace / f"scholar-{run_id}.bib"
        path.write_text(text)
        return {"status": "passed", "query": query, "path": str(path), "citation": text}


async def run(args):
    workspace = Path(args.output).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    run_id = uuid4().hex[:12]
    result = {"fixture": await fixture_check(workspace, run_id)}
    if args.scholar:
        try:
            result["scholar"] = await scholar_check(workspace, run_id + "-scholar")
        except Exception as exc:
            result["scholar"] = {"status": "failed", "error": str(exc)}
    if args.native:
        from diane.native_workers.lead import NativeLeadAdapter
        from diane.server.chat_v2_backend import AgentBackendRunRequest
        native_root = workspace / f"native-{run_id}"
        native_root.mkdir()
        os.environ["DAN_GRAPHS_DIR"] = str(native_root / "graphs")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        events = []
        try:
            request = AgentBackendRunRequest(
                task_id=run_id, run_id=run_id, thread_id=run_id, workspace_root=str(native_root),
                objective=("Use the visible browser to search Google Scholar for Attention Is All You Need "
                           "by Vaswani et al. (2017). Open its citation options, retrieve the actual BibTeX, "
                           "and save it as references.bib in this project. Verify the saved citation. "
                           "If Google blocks access, report that rather than substituting another source. "
                           "Do not delegate." if args.scholar else
                           f"Use the visible browser at http://127.0.0.1:{server.server_port} to search for "
                           "Browser Research Fixture. Open its citation options, get the BibTeX, and save "
                           "it as references.bib in this project. Verify the saved citation. This is a "
                           "local test page, so use its actual page content. Do not delegate."),
                profile_policy={"permission_mode": "auto"},
            )
            answer = (await run_in_app(args.api_base, request, args.native, events) if args.api_base else
                      await asyncio.wait_for(NativeLeadAdapter(args.native).run(request, events.append), 240))
            citation = native_root / "references.bib"
            saved = citation.read_text() if citation.is_file() else ""
            valid = (saved.strip() == BIB.strip()) if not args.scholar else (
                saved.lstrip().startswith("@") and "attention is all you need" in saved.lower()
                and "Vaswani" in saved and "2017" in saved
            )
            passed = (answer.status == "completed" and valid
                      and any(e.source_event_type == "native_lead.browser" for e in events))
            result["native"] = {"status": "passed" if passed else "failed", "backend": args.native,
                                "target": "scholar" if args.scholar else "fixture",
                                "summary": answer.summary, "citation": str(citation)}
        except Exception as exc:
            result["native"] = {"status": "failed", "backend": args.native, "error": str(exc) or type(exc).__name__}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            (native_root / "events.json").write_text(json.dumps([e.model_dump(mode="json") for e in events], indent=2))
    (workspace / f"acceptance-{run_id}.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if all(item["status"] == "passed" for item in result.values()) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scholar", action="store_true")
    parser.add_argument("--native", choices=["codex", "claude"], help="Also test an authenticated native lead (uses its configured model)")
    parser.add_argument("--api-base", default="", help="Run the native test through a running local Dear Diane app's API")
    parser.add_argument("--output", default="output/playwright/browser-research")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
