"""Opt-in native history discovery and fork preparation."""
from __future__ import annotations
import asyncio
import hashlib
import json
from pathlib import Path
import os
import sqlite3
import sys
from urllib.parse import unquote, urlparse
from .catalog import accounts, binary, user_home


def same_folder(left: str, right: str) -> bool:
    return bool(left and right) and Path(left).expanduser().resolve() == Path(right).expanduser().resolve()


def cursor_user_dir() -> Path:
    configured = os.environ.get("DAN_CURSOR_USER_DIR")
    if configured:
        return Path(configured)
    if sys.platform == "darwin":
        return user_home() / "Library/Application Support/Cursor/User"
    return user_home() / ".config/Cursor/User"


def _read_only(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)


def cursor_chats(workspace: str) -> list[tuple[str, str]]:
    """Cursor editor chats for one local folder: (composer id, title). Never writes Cursor's store."""
    base = cursor_user_dir()
    workspace_ids = []
    for meta in (base / "workspaceStorage").glob("*/workspace.json"):
        try:
            uri = urlparse(json.loads(meta.read_text()).get("folder", ""))
        except (OSError, ValueError, AttributeError):
            continue
        if uri.scheme == "file" and same_folder(unquote(uri.path), workspace):
            workspace_ids.append(meta.parent.name)
    db = base / "globalStorage/state.vscdb"
    if not workspace_ids or not db.is_file():
        return []
    chats: dict[str, str] = {}
    try:
        with _read_only(db) as con:
            has_headers = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'composerHeaders'").fetchone()
            if has_headers:
                marks = ",".join("?" * len(workspace_ids))
                for composer_id, value in con.execute(
                        f"SELECT composerId, value FROM composerHeaders WHERE workspaceId IN ({marks}) "
                        "AND COALESCE(isSubagent, 0) = 0 AND COALESCE(isArchived, 0) = 0 ORDER BY lastUpdatedAt DESC", workspace_ids):
                    try:
                        header = json.loads(value or "{}")
                    except ValueError:
                        header = {}
                    chats[composer_id] = str(header.get("name") or header.get("subtitle") or "Cursor chat")
            # Older Cursor versions list chats only in each workspace's own store.
            for workspace_id in workspace_ids:
                local = base / "workspaceStorage" / workspace_id / "state.vscdb"
                if not local.is_file():
                    continue
                try:
                    with _read_only(local) as workspace_con:
                        row = workspace_con.execute("SELECT value FROM ItemTable WHERE key = 'composer.composerData'").fetchone()
                    listed = json.loads(row[0]).get("allComposers", []) if row and row[0] else []
                except (sqlite3.Error, ValueError, AttributeError):
                    continue
                for item in listed:
                    composer_id = item.get("composerId") if isinstance(item, dict) else None
                    if composer_id and composer_id not in chats and not item.get("isArchived") and \
                            con.execute("SELECT 1 FROM cursorDiskKV WHERE key = ?", (f"composerData:{composer_id}",)).fetchone():
                        chats[composer_id] = str(item.get("name") or "Cursor chat")
    except sqlite3.Error:
        return []
    return list(chats.items())


def cursor_messages(db: Path, composer_id: str) -> list[dict]:
    with _read_only(db) as con:
        row = con.execute("SELECT value FROM cursorDiskKV WHERE key = ?", (f"composerData:{composer_id}",)).fetchone()
        if not row or not row[0]:
            raise ValueError("This Cursor chat is no longer stored; source was left unchanged")
        data = json.loads(row[0])
        bubbles = data.get("conversation") if isinstance(data.get("conversation"), list) and data["conversation"] else None
        if bubbles is None:
            order = [item.get("bubbleId") for item in data.get("fullConversationHeadersOnly") or [] if isinstance(item, dict)][:20000]
            stored: dict[str, dict] = {}
            for start in range(0, len(order), 500):
                chunk = [f"bubbleId:{composer_id}:{bubble}" for bubble in order[start:start + 500]]
                for key, value in con.execute(f"SELECT key, value FROM cursorDiskKV WHERE key IN ({','.join('?' * len(chunk))})", chunk):
                    try:
                        stored[key.rsplit(":", 1)[-1]] = json.loads(value)
                    except (TypeError, ValueError):
                        pass
            bubbles = [stored[bubble] for bubble in order if bubble in stored]
    result: list[dict] = []
    for bubble in bubbles:
        role = {1: "user", 2: "assistant"}.get(bubble.get("type"))
        text = bubble.get("text")
        if not role or not isinstance(text, str) or not text.strip():
            continue  # tool calls and thinking have no transcript text
        if result and result[-1]["role"] == role:
            result[-1]["content"] += "\n\n" + text
        else:
            result.append({"role": role, "content": text})
    return result


def discover(workspace: str) -> list[dict]:
    found: dict[str, dict] = {}
    for backend, profiles in accounts().items():
        for account_id, profile in profiles.items():
            if backend == "cursor":
                if account_id == "default":
                    db = cursor_user_dir() / "globalStorage/state.vscdb"
                    for composer_id, title in cursor_chats(workspace):
                        add(found, backend, account_id, composer_id, title, str(db), workspace)
                continue
            if backend == "codex":
                home = Path(profile.get("env", {}).get("CODEX_HOME", str(user_home() / ".codex")))
                db = home / "state_5.sqlite"
                if not db.exists():
                    continue
                try:
                    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as con:
                        con.row_factory = sqlite3.Row
                        columns = {row[1] for row in con.execute("PRAGMA table_info(threads)")}
                        # Codex also stores its own internal threads (subagents, guardian
                        # auto-reviews); only threads a person started are importable.
                        internal = "AND COALESCE(thread_source, 'user') NOT IN ('subagent', 'guardian_review', 'agent_created_thread')" if "thread_source" in columns else ""
                        rows = con.execute(f"SELECT id, cwd, title, rollout_path FROM threads WHERE archived = 0 {internal}").fetchall()
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
                  # Cursor editor chats cannot be resumed headlessly; the import is a transcript copy.
                  "continuation": "history" if backend == "cursor" else "native",
                  "reason": "Antigravity's documented fork is interactive; headless import is unavailable." if backend == "antigravity" else ""}


def messages(source: dict) -> list[dict]:
    result = []
    path = Path(source["path"])
    if not path.is_file():
        raise ValueError("Native session transcript is no longer available")
    if source["backend"] == "cursor":
        try:
            result = cursor_messages(path, source["session_id"])
        except (sqlite3.Error, ValueError) as exc:
            raise ValueError(str(exc) if isinstance(exc, ValueError) else "Cursor's chat store could not be read") from exc
        if not result:
            raise ValueError("No readable conversation messages found; source was left unchanged")
        return result
    # No size limit: the transcript is streamed line by line and never truncated.
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
