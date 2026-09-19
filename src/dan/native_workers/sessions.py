"""Opt-in native history discovery and fork preparation."""
from __future__ import annotations
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
from .catalog import accounts, binary, user_home


def same_folder(left: str, right: str) -> bool:
    return bool(left and right) and Path(left).expanduser().resolve() == Path(right).expanduser().resolve()


def discover(workspace: str) -> list[dict]:
    found: dict[str, dict] = {}
    for backend, profiles in accounts().items():
        for account_id, profile in profiles.items():
            if backend == "codex":
                home = Path(profile.get("env", {}).get("CODEX_HOME", str(user_home() / ".codex")))
                db = home / "state_5.sqlite"
                if not db.exists():
                    continue
                try:
                    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as con:
                        con.row_factory = sqlite3.Row
                        rows = con.execute("SELECT id, cwd, title, rollout_path FROM threads WHERE archived = 0").fetchall()
                    for row in rows:
                        if same_folder(row["cwd"], workspace):
                            add(found, backend, account_id, row["id"], row["title"], row["rollout_path"], workspace)
                except (sqlite3.Error, OSError):
                    continue
            elif backend == "claude":
                home = Path(profile.get("env", {}).get("CLAUDE_CONFIG_DIR", str(user_home() / ".claude")))
                projects = home / "projects"
                if not projects.is_dir():
                    continue
                for path in projects.glob("*/*.jsonl"):
                    cwd, title = "", ""
                    try:
                        with path.open() as stream:
                            for index, line in enumerate(stream):
                                if index > 100:
                                    break
                                row = json.loads(line)
                                cwd = row.get("cwd") or cwd
                                if row.get("type") == "user" and not title:
                                    content = row.get("message", {}).get("content", "")
                                    if isinstance(content, str):
                                        title = content[:100]
                                if cwd and title:
                                    break
                    except (OSError, ValueError, AttributeError):
                        continue
                    if same_folder(cwd, workspace):
                        add(found, backend, account_id, path.stem, title, str(path), workspace)
            elif backend == "antigravity":
                path = user_home() / ".gemini/antigravity-cli/cache/last_conversations.json"
                try:
                    data = json.loads(path.read_text())
                    for cwd, session_id in data.items():
                        if same_folder(cwd, workspace):
                            add(found, backend, account_id, session_id, "Antigravity conversation", "", workspace)
                except (OSError, ValueError, AttributeError):
                    pass
    return list(found.values())


def add(found, backend, account, session_id, title, path, workspace):
    # Shared histories can be visible under multiple accounts; preserve account choice.
    key = hashlib.sha256(f"{backend}:{account}:{session_id}".encode()).hexdigest()[:24]
    found[key] = {"id": key, "backend": backend, "account": account, "session_id": session_id,
                  "title": title or session_id, "path": path, "workspace": workspace,
                  "can_import": backend != "antigravity", "fork": True,
                  "reason": "Antigravity's documented fork is interactive; headless import is unavailable." if backend == "antigravity" else ""}


def messages(source: dict) -> list[dict]:
    result = []
    path = Path(source["path"])
    if not path.is_file():
        raise ValueError("Native session transcript is no longer available")
    # Bound imports and fail explicitly rather than silently truncate history.
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("This transcript exceeds the 128 MB import limit")
    with path.open() as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if source["backend"] == "codex":
                if row.get("type") != "response_item":
                    continue
                message = row.get("payload", {})
                if message.get("type") != "message":
                    continue
            else:
                if row.get("type") not in {"user", "assistant"}:
                    continue
                message = row.get("message", {})
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = message.get("content", "")
            if isinstance(content, list):
                content = "\n".join(block.get("text", "") for block in content if isinstance(block, dict))
            if isinstance(content, str) and content.strip():
                result.append({"role": role, "content": content})
    if not result:
        raise ValueError("No readable conversation messages found; source was left unchanged")
    return result


async def fork_codex(session_id: str, workspace: str, env: dict) -> str:
    """Use Codex's own fork API; never copy or rewrite its internal database."""
    process = await asyncio.create_subprocess_exec(binary("codex"), "app-server", "--listen", "stdio://",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=env, cwd=workspace, limit=8 * 1024 * 1024)
    async def request(request_id, method, params):
        process.stdin.write((json.dumps({"id": request_id, "method": method, "params": params}) + "\n").encode())
        await process.stdin.drain()
        while line := await process.stdout.readline():
            row = json.loads(line)
            if row.get("id") == request_id:
                if "error" in row:
                    raise ValueError(str(row["error"]))
                return row["result"]
        raise ValueError("Codex app server disconnected during fork")
    try:
        async with asyncio.timeout(20):
            await request(1, "initialize", {"clientInfo": {"name": "dan", "version": "0.2.0"}, "capabilities": {"experimentalApi": True}})
            process.stdin.write(b'{"method":"initialized","params":{}}\n')
            await process.stdin.drain()
            result = await request(2, "thread/fork", {"threadId": session_id, "cwd": workspace, "ephemeral": False})
            fork_id = result["thread"]["id"]
            if fork_id == session_id:
                raise ValueError("Codex did not return a separate fork")
            return fork_id
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
