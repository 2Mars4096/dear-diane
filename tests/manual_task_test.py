"""Manual task tester — sends individual tasks to the DAN server and reports results."""
import asyncio
import json
import sys
import time
import httpx
import websockets

BASE = "http://localhost:8000"
WS_BASE = "ws://localhost:8000"
REPORT_DIR = "/Users/lizhi/Dropbox/CUHK-phd/projects/supply-chain-report"
TIMEOUT = 180


async def send_and_monitor(task_name, message, mode="agent", wf_id=None, timeout=TIMEOUT):
    """Send a message and monitor stream until completion. Returns (success, summary)."""
    if wf_id is None:
        wf_id = f"test_{task_name}_{int(time.time())}"

    print(f"\n{'='*60}")
    print(f"TASK: {task_name}")
    print(f"MODE: {mode}  |  WF: {wf_id}")
    print(f"MSG:  {message[:120]}...")
    print(f"{'='*60}")

    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        try:
            await c.post("/api/graphs", json={"graph_id": wf_id})
        except Exception:
            pass

        r = await c.post("/api/chat/message", json={
            "workflow_id": wf_id,
            "message": message,
            "mode": mode,
        })
        data = r.json()
        channel = data.get("stream_channel_id")

    if not channel:
        print("  FAIL: no stream channel")
        return False, "no stream channel"

    start = time.time()
    tool_calls = []
    final_text = ""
    file_writes = []
    errors = []

    current_channel = channel
    while time.time() - start < timeout:
        uri = f"{WS_BASE}/api/chat/{current_channel}/events"
        try:
            async with websockets.connect(uri, ping_interval=30, ping_timeout=90) as ws:
                while True:
                    if time.time() - start > timeout:
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        ev = json.loads(raw)
                        etype = ev.get("type", "?")
                        elapsed = int(time.time() - start)

                        if etype == "chat_queued":
                            new_ch = ev.get("stream_channel_id", "")
                            print(f"  [{elapsed}s] REDIRECT -> {new_ch}")
                            current_channel = new_ch
                            break

                        elif etype == "chat_complete":
                            content = ev.get("content") or ""
                            mode_d = ev.get("detected_mode", "")
                            if mode_d == "progress_ack":
                                print(f"  [{elapsed}s] ... {content[:100]}")
                            else:
                                final_text = content
                                print(f"  [{elapsed}s] DONE ({len(content)} chars)")
                                if content:
                                    print(f"  Response: {content[:300]}")
                                return _evaluate(task_name, tool_calls, file_writes, final_text, errors, elapsed)

                        elif etype == "chat_tool_call_start":
                            tool = ev.get("tool_name", "?")
                            args = ev.get("arguments", {})
                            tool_calls.append(tool)
                            if tool == "file_write":
                                fp = args.get("path", "?")
                                clen = len(args.get("content", ""))
                                file_writes.append(fp)
                                print(f"  [{elapsed}s] FILE_WRITE: {fp} ({clen} chars)")
                            elif tool == "file_read":
                                print(f"  [{elapsed}s] FILE_READ: {args.get('path','?')[:80]}")
                            elif tool in ("web_search", "web_fetch"):
                                q = args.get("query", args.get("url", ""))[:60]
                                print(f"  [{elapsed}s] {tool}: {q}")
                            elif tool == "shell_command":
                                print(f"  [{elapsed}s] SHELL: {args.get('command','?')[:60]}")
                            else:
                                print(f"  [{elapsed}s] {tool}")

                        elif etype == "chat_tool_call_result":
                            pass
                        elif etype == "chat_error":
                            err = ev.get("error", "?")
                            errors.append(err)
                            print(f"  [{elapsed}s] ERROR: {err[:100]}")
                        elif etype == "chat_file_attachment":
                            fp = ev.get("file_path", "?")
                            print(f"  [{elapsed}s] ATTACHMENT: {fp}")
                        elif etype != "chat_token":
                            print(f"  [{elapsed}s] {etype}")

                    except asyncio.TimeoutError:
                        print(f"  [{int(time.time()-start)}s] timeout waiting for event")
                        return _evaluate(task_name, tool_calls, file_writes, final_text, errors, int(time.time()-start))

        except websockets.exceptions.ConnectionClosedOK:
            if final_text:
                return _evaluate(task_name, tool_calls, file_writes, final_text, errors, int(time.time()-start))
            await asyncio.sleep(1)
            continue
        except Exception as e:
            print(f"  WS error: {e}")
            if time.time() - start > 30:
                return _evaluate(task_name, tool_calls, file_writes, final_text, errors, int(time.time()-start))
            await asyncio.sleep(2)

    return _evaluate(task_name, tool_calls, file_writes, final_text, errors, int(time.time()-start))


def _evaluate(task_name, tool_calls, file_writes, final_text, errors, elapsed):
    """Evaluate whether the task succeeded."""
    summary = {
        "task": task_name,
        "tool_calls": len(tool_calls),
        "tools_used": list(set(tool_calls)),
        "file_writes": file_writes,
        "errors": errors,
        "elapsed_s": elapsed,
        "has_response": bool(final_text),
        "response_len": len(final_text),
    }

    success = False
    reason = ""

    if task_name == "read_file":
        success = "file_read" in tool_calls and len(final_text) > 50
        reason = "file_read used + got content" if success else "no file_read or empty response"
    elif task_name == "web_search":
        success = "web_search" in tool_calls and len(final_text) > 50
        reason = "web_search used + got results" if success else "no web_search or empty"
    elif task_name == "write_file":
        success = "file_write" in tool_calls and len(file_writes) > 0
        reason = f"wrote {file_writes}" if success else "no file_write tool call"
    elif task_name == "download_figure":
        success = ("web_search" in tool_calls or "web_fetch" in tool_calls) and "file_write" in tool_calls
        reason = "fetched + wrote" if success else "missing fetch or write"
    elif task_name == "read_transform_write":
        success = "file_read" in tool_calls and "file_write" in tool_calls
        reason = "read + wrote" if success else "missing read or write"
    elif task_name == "full_report":
        success = "file_write" in tool_calls and any(".tex" in fw for fw in file_writes)
        reason = f"wrote .tex: {file_writes}" if success else "no .tex written"
    else:
        success = bool(final_text)
        reason = "got response"

    status = "PASS" if success else "FAIL"
    print(f"\n  >>> {status}: {reason}")
    print(f"  >>> tools={len(tool_calls)}, writes={file_writes}, elapsed={elapsed}s")
    summary["success"] = success
    summary["reason"] = reason
    return success, summary


TASKS = [
    (
        "read_file",
        f"Please read the file at {REPORT_DIR}/auto_supply_chain_report_2025.tex and tell me what sections it contains. Just list the section titles.",
        "agent",
    ),
    (
        "web_search",
        "Search the web for '2026 automobile supply chain trends semiconductor EV battery' and summarize the top findings in 3 bullet points.",
        "agent",
    ),
    (
        "write_file",
        f"Write a simple test LaTeX file to {REPORT_DIR}/test_write_2026.tex with this content: \\documentclass{{article}} \\title{{Test}} \\begin{{document}} \\maketitle Hello World \\end{{document}}",
        "agent",
    ),
    (
        "download_figure",
        f"Search the web for a chart or graph about '2025 global semiconductor shortage automotive', then describe what you found. Do not download yet, just search and summarize.",
        "agent",
    ),
    (
        "read_transform_write",
        f"Read {REPORT_DIR}/auto_supply_chain_report_2025.tex, extract just the first 50 lines, change every '2025' to '2026', and write the result to {REPORT_DIR}/test_transform_2026.tex",
        "agent",
    ),
    (
        "full_report",
        f"Help me write a comprehensive auto supply chain report for 2026 in LaTeX. Read the existing report at {REPORT_DIR}/auto_supply_chain_report_2025.tex for structure reference. Then write a new 2026 version to {REPORT_DIR}/auto_supply_chain_report_2026.tex. Update all data and trends. Use web search for current 2026 data.",
        "agent",
    ),
]


async def run_all():
    results = []
    for name, msg, mode in TASKS:
        success, summary = await send_and_monitor(name, msg, mode=mode)
        results.append(summary)
        await asyncio.sleep(3)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for r in results:
        status = "PASS" if r["success"] else "FAIL"
        print(f"  {status:4s} | {r['task']:25s} | tools={r['tool_calls']:2d} | writes={r['file_writes']} | {r['elapsed_s']}s | {r['reason']}")
    
    passed = sum(1 for r in results if r["success"])
    print(f"\n  {passed}/{len(results)} tasks passed")
    return results


if __name__ == "__main__":
    if len(sys.argv) > 1:
        task_idx = int(sys.argv[1]) - 1
        if 0 <= task_idx < len(TASKS):
            name, msg, mode = TASKS[task_idx]
            asyncio.run(send_and_monitor(name, msg, mode=mode))
        else:
            print(f"Task index must be 1-{len(TASKS)}")
    else:
        asyncio.run(run_all())
