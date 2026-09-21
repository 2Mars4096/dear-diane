"""Per-session token profiler for Claude Code and Codex transcripts.

Reads the transcripts the CLIs already keep on disk and rebuilds each session as
rounds -> model calls and steps, so DAN can show which sessions are expensive,
which activities caused it, and what to change. Model-call tokens are exact;
tokens added by tool results and prompts are estimated as chars/4.

The cost of a step is its *context burden*: tokens it added x the number of later
model calls that re-read them before the next compaction.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.request import Request, urlopen

from .catalog import accounts, user_home

ACTIONS = {
    "read": "Open a file or document",
    "search": "Find files, symbols, or text",
    "execute": "Run a command or program",
    "write": "Create or edit files",
    "reason": "Model thinking between tool calls",
    "delegate": "Hand work to a subagent",
    "web": "Fetch or search external sources",
    "manage": "Plans, todo lists, skills, fixed context",
    "respond": "Reply to the user",
}
STAGES = {
    "orient": "Understand the request and the project: instructions, docs, directory layout",
    "locate": "Find where the relevant code or data lives",
    "understand": "Read existing implementation in depth",
    "plan": "Design the approach, write or update plans and todo lists",
    "implement": "Write or edit product code",
    "verify": "Run tests, builds, linters, type checks, or the app to confirm behaviour",
    "debug": "Diagnose a failure: reproduce, inspect logs, read code after an error",
    "review": "Inspect diffs, status, or history; self-review before finishing",
    "environment": "Install dependencies, configure tools, manage files and processes",
    "vcs": "Commit, branch, push, open pull requests",
    "research": "External documentation, web search, API references",
    "delegate": "Brief subagents, wait for them, read their reports",
    "document": "Write docs, changelogs, comments for people",
    "data": "Run analyses, scripts, notebooks, experiments",
    "communicate": "Answer, summarise, or ask the user",
    "overhead": "System prompt, tool definitions, injected context, retries",
}
OVERSIZED = 10_000
CACHE_VERSION = 4  # bump when parsing changes so cached summaries are rebuilt
CLAUDE_TOOLS = {
    "Read": "read", "NotebookRead": "read", "Grep": "search", "Glob": "search", "LS": "search", "ToolSearch": "search",
    "Bash": "execute", "BashOutput": "execute", "KillShell": "execute", "Monitor": "execute",
    "Edit": "write", "Write": "write", "MultiEdit": "write", "NotebookEdit": "write",
    "Agent": "delegate", "Task": "delegate", "SendMessage": "delegate", "Workflow": "delegate", "TaskOutput": "delegate",
    "WebFetch": "web", "WebSearch": "web", "TodoWrite": "manage", "EnterPlanMode": "manage", "ExitPlanMode": "manage",
    "Skill": "manage", "AskUserQuestion": "respond",
}
COMMAND_RULES = [  # first match wins: (pattern, action, stage)
    (r"\b(pytest|vitest|jest|unittest|tox|npm (run )?test|yarn test|pnpm test|go test|cargo test|playwright)\b", "execute", "verify"),
    (r"\b(tsc|eslint|ruff|mypy|flake8|pyright|npm run (build|lint|typecheck)|cargo (build|check|clippy)|make)\b", "execute", "verify"),
    (r"\b(git (commit|push|pull|checkout|switch|branch|merge|rebase|stash|add|tag|restore|reset)|gh )", "execute", "vcs"),
    (r"\bgit (diff|status|log|show|blame)\b", "read", "review"),
    (r"\b(pip3? install|uv (add|sync|pip)|npm (i|ci|install)\b|yarn add|pnpm (add|install)|brew |apt(-get)? |poetry |conda )", "execute", "environment"),
    (r"(apply_patch|sed -i|\btee\b|cat\s*>+|<<\s*'?\w+'?\s*>|>\s*[\w./-]+\s*<<)", "write", "implement"),
    (r"^\s*(rg|grep|find|fd|ls|tree|ag)\b", "search", "locate"),
    (r"^\s*(cat|sed -n|head|tail|less|nl|wc|jq|bat|awk)\b", "read", "understand"),
    (r"\b(curl|wget)\b", "web", "research"),
    (r"\b(python3?|node|Rscript|stata|jupyter|duckdb|sqlite3|uv run)\b", "execute", "data"),
    (r"^\s*(rm|mv|cp|mkdir|touch|chmod|ln|kill|pkill|lsof|ps|export|source)\b", "execute", "environment"),
]
DOC_PATH = re.compile(r"(\.mdx?$|README|CHANGELOG|AGENTS|CLAUDE|/docs/)", re.I)


def _est(chars: int) -> int:
    return (chars + 3) // 4


def _size(value) -> int:
    """Characters of text inside a transcript content value."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_size(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") == "image" or "image_url" in value:
            return 4000
        return sum(_size(value.get(key)) for key in ("text", "content", "output") if key in value)
    return 0


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(filter(None, (_text(item) for item in value)))
    if isinstance(value, dict):
        return _text(value.get("text") or value.get("content") or "")
    return ""


def classify_command(command: str) -> tuple[str, str]:
    body = re.sub(r"^\s*((cd\s+\S+|echo\s+(\"[^\"]*\"|'[^']*'|\S+))\s*(&&|;)\s*)+", "", command.strip())
    for pattern, action, stage in COMMAND_RULES:
        if re.search(pattern, body):
            return action, stage
    return "execute", "data"


def classify_tool(tool: str, detail: str) -> tuple[str, str]:
    """Offline label for one tool call from its name and main argument."""
    if tool in ("Bash", "exec", "shell", "exec_command", "local_shell", "container.exec", "write_stdin"):
        return classify_command(detail)
    if tool == "apply_patch":
        return "write", "document" if DOC_PATH.search(detail) else "implement"
    if tool in ("update_plan", "TodoWrite", "EnterPlanMode", "ExitPlanMode"):
        return "manage", "plan"
    if tool in ("web_search", "web_search_call", "WebFetch", "WebSearch"):
        return "web", "research"
    if tool in ("spawn_agent", "wait_agent", "send_input", "close_agent", "wait") or CLAUDE_TOOLS.get(tool) == "delegate":
        return "delegate", "delegate"
    if tool == "view_image":
        return "read", "understand"
    action = CLAUDE_TOOLS.get(tool)
    if action == "read":
        return action, "orient" if DOC_PATH.search(detail) else "understand"
    if action == "search":
        return action, "locate"
    if action == "write":
        return action, "document" if DOC_PATH.search(detail) else "implement"
    if action == "execute":
        return action, "verify"
    if action == "manage":
        return action, "orient"
    if action == "respond":
        return action, "communicate"
    if tool.startswith("mcp__"):
        lowered = tool.lower()
        if any(word in lowered for word in ("browser", "chrome", "preview", "simulator")):
            return "execute", "verify"
        return ("web", "research") if any(word in lowered for word in ("search", "fetch", "read", "query")) else ("execute", "data")
    return "execute", "data"


def _detail(tool: str, args) -> str:
    """The one argument that says what the call did (path, command, pattern, prompt)."""
    if isinstance(args, str):
        match = re.search(r"cmd:\s*\"((?:[^\"\\]|\\.)*)\"", args)
        if match:
            return match.group(1).encode().decode("unicode_escape", "ignore")[:400]
        try:
            args = json.loads(args)
        except ValueError:
            return args[:400]
    if not isinstance(args, dict):
        return str(args)[:400]
    for key in ("file_path", "path", "notebook_path", "command", "cmd", "pattern", "query", "url", "description", "skill", "prompt", "input"):
        value = args.get(key)
        if value:
            if isinstance(value, list):
                value = " ".join(map(str, value[2:] if value[:2] == ["bash", "-lc"] else value))
            extra = f" [{args.get('offset')}+{args.get('limit')}]" if key == "file_path" and args.get("offset") else ""
            return (str(value) + extra)[:400]
    return json.dumps(args)[:400]


def _title(text: str) -> str:
    """The person's request; DAN leads wrap it after the conversation context."""
    if "\nCurrent request:\n" in text:
        text = text.split("\nCurrent request:\n", 1)[1].split("\n\nYou are the lead agent", 1)[0]
    return " ".join(text.split())[:160]


class _Builder:
    """Collects one transcript into an ordered timeline of calls, steps, and compactions."""

    def __init__(self):
        self.timeline: list[dict] = []
        self.rounds: list[dict] = []
        self.tools: dict[str, dict] = {}
        self.fresh_context = True  # next call starts a context (session start or after compaction)
        self.pending = 0           # tokens added since the last call
        self.last_call: dict | None = None

    def prompt(self, text: str, size: int, ts=None):
        self.rounds.append({"index": len(self.rounds), "title": _title(text), "started_at": ts})
        added = _est(size)
        self.pending += added
        self.timeline.append({"k": "step", "kind": "prompt", "tool": "", "action": "respond", "stage": "communicate",
                              "detail": _title(text), "added": added, "generated": 0, "round": len(self.rounds) - 1})

    def call(self, context: int, cached: int, cache_write: int, output: int, reasoning: int = 0, ts=None, model: str = ""):
        if not self.rounds:
            self.prompt("(session start)", 0, ts)
        if self.fresh_context and context:
            base = max(0, context - self.pending)
            self.timeline.append({"k": "step", "kind": "baseline", "tool": "", "action": "manage", "stage": "overhead",
                                  "detail": "System prompt, tool definitions, project instructions" if len([e for e in self.timeline if e["k"] == "call"]) == 0 else "Context carried over after compaction",
                                  "added": base, "generated": 0, "round": len(self.rounds) - 1})
            self.fresh_context = False
        self.pending = 0
        self.last_call = {"k": "call", "round": len(self.rounds) - 1, "context": context, "cached": cached, "cache_write": cache_write,
                          "output": output, "reasoning": reasoning, "ts": ts, "model": model, "args": 0, "text": False, "tool": False}
        self.timeline.append(self.last_call)
        return self.last_call

    def tool(self, call_id: str, name: str, args, ts=None):
        detail = _detail(name, args)
        action, stage = classify_tool(name, detail)
        args_tokens = _est(_size(args) if not isinstance(args, dict) else len(json.dumps(args)))
        step = {"k": "step", "kind": "tool", "tool": name, "action": action, "stage": stage, "detail": detail, "added": args_tokens,
                "generated": args_tokens, "result": 0, "error": False, "round": max(0, len(self.rounds) - 1), "ts": ts, "call_id": call_id}
        if self.last_call is not None:
            self.last_call["args"] += args_tokens
            self.last_call["tool"] = True
        self.pending += args_tokens
        self.tools[call_id] = step
        self.timeline.append(step)

    def result(self, call_id: str, size: int, error: bool = False):
        step = self.tools.get(call_id)
        if step is None:
            return
        tokens = _est(size)
        step["result"] += tokens
        step["added"] += tokens
        step["error"] = step["error"] or error
        self.pending += tokens

    def compact(self):
        self.timeline.append({"k": "compact"})
        self.fresh_context = True
        self.pending = 0


def parse_claude(path: Path) -> _Builder:
    build = _Builder()
    seen: set[str] = set()
    with path.open(errors="replace") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            kind = row.get("type")
            message = row.get("message") if isinstance(row.get("message"), dict) else {}
            if kind == "system" and row.get("subtype") == "compact_boundary" or row.get("isCompactSummary"):
                if not build.timeline or build.timeline[-1]["k"] != "compact":
                    build.compact()
                continue
            if kind == "assistant":
                usage = message.get("usage") or {}
                key = message.get("id") or row.get("requestId") or row.get("uuid")
                if key not in seen and usage and message.get("model") != "<synthetic>":
                    # Every content block is its own row repeating the message's usage; count it once.
                    seen.add(key)
                    read, write = int(usage.get("cache_read_input_tokens") or 0), int(usage.get("cache_creation_input_tokens") or 0)
                    build.call(int(usage.get("input_tokens") or 0) + read + write, read, write, int(usage.get("output_tokens") or 0),
                               ts=row.get("timestamp"), model=message.get("model") or "")
                for block in message.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        build.tool(block.get("id") or "", block.get("name") or "tool", block.get("input") or {}, row.get("timestamp"))
                    elif block.get("type") == "text" and build.last_call is not None and (block.get("text") or "").strip():
                        build.last_call["text"] = True
            elif kind == "user":
                content = message.get("content")
                results = [block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"] if isinstance(content, list) else []
                for block in results:
                    build.result(block.get("tool_use_id") or "", _size(block.get("content")), bool(block.get("is_error")))
                if not results and not row.get("isMeta") and _size(content):
                    build.prompt(_text(content), _size(content), row.get("timestamp"))
    return build


def parse_codex(path: Path) -> _Builder:
    build = _Builder()
    last_total = -1
    spoke = False
    model = ""
    with path.open(errors="replace") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            kind, payload = row.get("type"), row.get("payload") if isinstance(row.get("payload"), dict) else {}
            sub = payload.get("type")
            if kind == "compacted" or sub == "context_compacted":
                build.compact()
            elif kind == "turn_context":
                model = payload.get("model") or model
            elif kind == "event_msg" and sub == "user_message" or kind == "response_item" and sub == "message" and payload.get("role") == "user":
                # Older rollouts only keep the response item; newer ones write both. Injected context starts with a tag.
                text = (payload.get("message") or _text(payload.get("content"))).strip()
                title = _title(text)
                if text and not text.startswith(("<", "# AGENTS.md instructions")) and not (build.rounds and build.rounds[-1]["title"] == title and build.timeline[-1].get("kind") == "prompt"):
                    build.prompt(text, len(text), row.get("timestamp"))
            elif kind == "event_msg" and sub == "agent_message":
                spoke = True
            elif kind == "event_msg" and sub == "token_count":
                info = payload.get("info") or {}
                last, total = info.get("last_token_usage") or {}, (info.get("total_token_usage") or {}).get("total_tokens", -1)
                if not last or total == last_total:  # rate-limit pings repeat the previous totals
                    continue
                last_total = total
                call = build.call(int(last.get("input_tokens") or 0), int(last.get("cached_input_tokens") or 0), int(last.get("cache_write_input_tokens") or 0),
                                  int(last.get("output_tokens") or 0), int(last.get("reasoning_output_tokens") or 0), ts=row.get("timestamp"), model=model)
                call["text"], spoke = spoke, False
            elif kind == "response_item" and sub in ("function_call", "custom_tool_call", "local_shell_call", "web_search_call"):
                name = payload.get("name") or sub.removesuffix("_call")
                args = payload.get("arguments") or payload.get("input") or payload.get("action") or {}
                if name == "exec" and isinstance(args, str) and "apply_patch" in args[:200]:
                    name = "apply_patch"
                build.tool(payload.get("call_id") or payload.get("id") or "", name, args, row.get("timestamp"))
                if build.last_call is not None:
                    build.last_call["tool"] = True
            elif kind == "response_item" and sub in ("function_call_output", "custom_tool_call_output"):
                output = payload.get("output")
                text = _text(output) if not isinstance(output, str) else output
                failed = bool(re.search(r"(exit code|exited with code|Exit code:?) [1-9]|\"exit_code\":\s*[1-9]", text[:600]))
                build.result(payload.get("call_id") or "", _size(output), failed)
    return build


def _finish(build: _Builder) -> dict:
    """Timeline -> flags, debug relabelling, burden, rounds, and totals."""
    timeline = build.timeline
    # Output the model generated beyond tool arguments is thinking or a reply.
    expanded: list[dict] = []
    for event in timeline:
        expanded.append(event)
        if event["k"] == "call":
            rest = max(0, event["output"] - event["args"])
            if rest:
                reply = event["text"] and not event["tool"]
                expanded.append({"k": "step", "kind": "reply" if reply else "reasoning", "tool": "", "action": "respond" if reply else "reason",
                                 "stage": "communicate" if reply else "plan", "detail": "Reply to the user" if reply else "Thinking and narration between tool calls",
                                 "added": rest, "generated": rest, "round": event["round"]})
    reads: dict[str, int] = {}
    commands: dict[str, int] = {}
    failing = False
    for event in expanded:
        if event["k"] == "compact":
            reads.clear(); commands.clear()
            continue
        if event["k"] != "step" or event["kind"] != "tool":
            continue
        flags = event.setdefault("flags", [])
        if event["error"]:
            flags.append("failed_call")
        if event["result"] >= OVERSIZED:
            flags.append("oversized_output")
        if event["action"] == "write":
            reads.pop(event["detail"].split(" [")[0], None)
            commands.clear()
        elif event["action"] == "read" and event["tool"] in ("Read", "NotebookRead"):
            if event["detail"] in reads:
                flags.append("duplicate_read")
            reads[event["detail"]] = 1
        elif event["tool"] not in ("Read",) and event["action"] in ("execute", "read", "search") and event["detail"]:
            if commands.get(event["detail"]):
                flags.append("repeated_command")
            commands[event["detail"]] = 1
        if event["stage"] == "verify":
            failing = event["error"]
        elif failing and event["stage"] in ("understand", "locate", "data"):
            event["stage"] = "debug"
    _inherit_stage(expanded)
    after = 0
    for event in reversed(expanded):
        if event["k"] == "compact":
            after = 0
        elif event["k"] == "call":
            after += 1
        else:
            event["burden"] = event["added"] * after
            event["weight"] = event["burden"] + event.get("generated", 0)
    calls = [event for event in expanded if event["k"] == "call"]
    steps = [event for event in expanded if event["k"] == "step"]
    for index, step in enumerate(steps):
        step["i"] = index
    rounds = []
    for info in build.rounds:
        mine = [call for call in calls if call["round"] == info["index"]]
        own = [step for step in steps if step["round"] == info["index"]]
        rounds.append({**info, "calls": len(mine), "steps": sum(1 for step in own if step["kind"] == "tool"), **_totals(mine),
                       "weight": sum(step["weight"] for step in own),
                       "top_stage": max(_group(own, "stage"), key=lambda item: item["weight"], default={"key": ""})["key"]})
    gaps = [call for index, call in enumerate(calls) if index and call["context"] > 20_000 and call["cached"] < 0.2 * call["context"]]
    return {"totals": {**_totals(calls), "calls": len(calls), "rounds": len(rounds), "tool_calls": sum(1 for step in steps if step["kind"] == "tool"),
                       "compactions": sum(1 for event in expanded if event["k"] == "compact"),
                       "cold_rebuilds": len(gaps), "cold_rebuild_tokens": sum(call["context"] - call["cached"] for call in gaps),
                       "models": sorted({call["model"] for call in calls if call["model"]})},
            "rounds": rounds, "steps": steps, "context_curve": _downsample([call["context"] for call in calls], 160),
            "started_at": next((call["ts"] for call in calls if call["ts"]), None), "ended_at": next((call["ts"] for call in reversed(calls) if call["ts"]), None)}


def _inherit_stage(events: list[dict]):
    """Thinking belongs to the stage of the next action in the same round; a final thought is communication."""
    following = None
    for event in reversed(events):
        if event["k"] != "step":
            continue
        if event["kind"] == "tool":
            following = event
        elif event["kind"] == "prompt":
            following = None
        elif event["kind"] == "reasoning":
            event["stage"] = following["stage"] if following and following["round"] == event["round"] else "communicate"


def _totals(calls: list[dict]) -> dict:
    context = sum(call["context"] for call in calls)
    cached = sum(call["cached"] for call in calls)
    output = sum(call["output"] for call in calls)
    return {"input_fresh": context - cached, "cache_read": cached, "cache_write": sum(call["cache_write"] for call in calls), "output": output,
            "reasoning": sum(call["reasoning"] for call in calls), "over_100k": sum(max(0, call["context"] - 100_000) for call in calls), "total": context + output, "peak_context": max((call["context"] for call in calls), default=0),
            "cache_hit": round(cached / context, 3) if context else 0.0}


def _group(steps: list[dict], field: str) -> list[dict]:
    groups: dict[str, dict] = {}
    for step in steps:
        row = groups.setdefault(step[field], {"key": step[field], "count": 0, "added": 0, "burden": 0, "weight": 0})
        row["count"] += 1; row["added"] += step["added"]; row["burden"] += step["burden"]; row["weight"] += step["weight"]
    total = sum(row["weight"] for row in groups.values()) or 1
    return sorted(({**row, "share": round(row["weight"] / total, 4)} for row in groups.values()), key=lambda row: row["weight"], reverse=True)


def _downsample(values: list[int], size: int) -> list[int]:
    if len(values) <= size:
        return values
    width = len(values) / size
    return [max(values[int(index * width):max(int((index + 1) * width), int(index * width) + 1)]) for index in range(size)]


# ---- discovery -------------------------------------------------------------------------------

_heads: dict[str, dict] = {}


def codex_head(path: Path) -> dict:
    """Thread identity from the start of a rollout without parsing its (large) first line."""
    cached = _heads.get(str(path))
    if cached is None:
        try:
            with path.open("rb") as stream:
                head = stream.read(4096).decode("utf-8", "replace")
        except OSError:
            head = ""
        def field(name):
            match = re.search(rf'"{name}":\s*"((?:[^"\\]|\\.)*)"', head)
            return match.group(1) if match else ""
        cached = _heads[str(path)] = {"id": field("id"), "parent": field("parent_thread_id"), "cwd": field("cwd"), "source": field("thread_source")}
    return cached


def _stores() -> list[tuple[str, str, Path]]:
    """(backend, account, home) for each distinct transcript store."""
    found, seen = [], set()
    configured = accounts()
    for backend, default in (("claude", ".claude"), ("codex", ".codex")):
        profiles = configured.get(backend) or {"default": {}}
        for account, profile in profiles.items():
            env = profile.get("env", {})
            home = Path(env.get("CLAUDE_CONFIG_DIR" if backend == "claude" else "CODEX_HOME") or os.environ.get("CODEX_HOME" if backend == "codex" else "CLAUDE_CONFIG_DIR") or user_home() / default)
            store = (home / ("projects" if backend == "claude" else "sessions")).resolve()
            if store.is_dir() and store not in seen:
                seen.add(store)
                found.append((backend, account, home))
    return found


def _codex_files(home: Path):
    """Rollouts newest day first."""
    root = home / "sessions"
    for year in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        for month in sorted((p for p in year.iterdir() if p.is_dir()), reverse=True):
            for day in sorted((p for p in month.iterdir() if p.is_dir()), reverse=True):
                yield from sorted(day.glob("rollout-*.jsonl"), reverse=True)


def codex_children(home: Path, path: Path, thread_id: str) -> list[Path]:
    """Threads this one spawned. Children are written on or after the parent's day."""
    if not thread_id:
        return []
    try:
        floor = path.parent.relative_to(home / "sessions").parts
    except ValueError:
        return []
    children = []
    for other in _codex_files(home):
        if other.parent.relative_to(home / "sessions").parts < floor:
            break
        if other != path and codex_head(other)["parent"] == thread_id:
            children.append(other)
    return children


def _candidates(limit: int) -> list[dict]:
    rows = []
    for backend, account, home in _stores():
        if backend == "claude":
            for path in (home / "projects").glob("*/*.jsonl"):
                rows.append({"backend": backend, "account": account, "home": home, "path": path, "session_id": path.stem, "mtime": path.stat().st_mtime})
        else:
            count = 0
            for path in _codex_files(home):
                head = codex_head(path)
                if head["parent"] and head["parent"] != head["id"] or head["source"] in ("subagent", "guardian_review", "agent_created_thread"):
                    continue
                rows.append({"backend": backend, "account": account, "home": home, "path": path, "session_id": head["id"] or path.stem, "mtime": path.stat().st_mtime, "cwd": head["cwd"]})
                count += 1
                if count >= limit:
                    break
    return sorted(rows, key=lambda row: row["mtime"], reverse=True)[:limit]


def _children(row: dict) -> list[tuple[Path, dict]]:
    path: Path = row["path"]
    if row["backend"] == "claude":
        found = []
        for child in sorted((path.with_suffix("") / "subagents").glob("agent-*.jsonl")):
            try:
                meta = json.loads(child.with_suffix("").with_suffix(".meta.json").read_text())
            except (OSError, ValueError):
                meta = {}
            found.append((child, meta))
        return found
    return [(child, {"agentType": codex_head(child)["source"] or "subagent"}) for child in codex_children(row["home"], path, row["session_id"])]


def dan_links(base: Path) -> dict[str, dict]:
    """native CLI session id -> the DAN run that launched it."""
    links = {}
    for folder, role in (("native_leads", "lead"), ("native_workers", "team")):
        for path in (base / folder).glob("*.json"):
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if record.get("native_session_id"):
                links[str(record["native_session_id"])] = {"role": role, "run_id": record.get("parent_run_id") or "", "worker_id": record.get("worker_id") or path.stem}
    return links


def _parse(backend: str, path: Path) -> dict:
    return _finish(parse_claude(path) if backend == "claude" else parse_codex(path))


def _cwd(row: dict) -> str:
    if row.get("cwd"):
        return row["cwd"]
    try:
        with row["path"].open(errors="replace") as stream:
            for index, line in enumerate(stream):
                if index > 40:
                    break
                match = re.search(r'"cwd":\s*"((?:[^"\\]|\\.)*)"', line)
                if match:
                    return match.group(1)
    except OSError:
        pass
    return ""


def _signature(row: dict, children) -> str:
    parts = [str(CACHE_VERSION)]
    for path in [row["path"], *(child for child, _ in children)]:
        stat = path.stat()
        parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


def _store_dir(base: Path) -> Path:
    folder = base / "token_usage"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def list_sessions(base: Path, limit: int = 40) -> dict:
    """Most recent sessions with totals (subagents included). Summaries are cached until a transcript changes."""
    cache_path = _store_dir(base) / "summaries.json"
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    links = dan_links(base)
    fresh, sessions = {}, []
    for row in _candidates(limit):
        key = f"{row['backend']}-{row['session_id']}"
        try:
            children = _children(row)
            signature = _signature(row, children)
            entry = cache.get(key)
            if not entry or entry.get("signature") != signature:
                own = _parse(row["backend"], row["path"])
                kids = [_parse(row["backend"], child)["totals"] for child, _ in children]
                title = next((r["title"] for r in own["rounds"] if r["title"] and not r["title"].startswith(("(", "<"))), "") or row["session_id"]
                entry = {"signature": signature, "path": str(row["path"]), "home": str(row["home"]), "summary": {
                    "id": key, "backend": row["backend"], "account": row["account"], "session_id": row["session_id"], "title": title, "cwd": _cwd(row),
                    "updated_at": row["mtime"], "totals": own["totals"], "subagents": len(kids), "subagent_total": sum(kid["total"] for kid in kids),
                    "total": own["totals"]["total"] + sum(kid["total"] for kid in kids),
                    "heaviest_round": max((r["total"] for r in own["rounds"]), default=0),
                    "by_stage": {g["key"]: g["weight"] for g in _group(own["steps"], "stage")}, "by_action": {g["key"]: g["weight"] for g in _group(own["steps"], "action")},
                    "flags": _flag_totals(own["steps"])}}
        except OSError:
            continue
        fresh[key] = entry
        sessions.append({**entry["summary"], "dan": links.get(row["session_id"])})
    cache_path.write_text(json.dumps({**cache, **fresh}))
    return {"sessions": sessions, "actions": ACTIONS, "stages": STAGES}


def _flag_totals(steps: list[dict]) -> dict:
    found: dict[str, dict] = {}
    for step in steps:
        for flag in step.get("flags", []):
            row = found.setdefault(flag, {"count": 0, "burden": 0})
            row["count"] += 1; row["burden"] += step["burden"]
    return found


def _shares(weights: dict[str, float], counts: dict[str, int] | None = None) -> list[dict]:
    total = sum(weights.values()) or 1
    return sorted(({"key": key, "weight": int(value), "count": (counts or {}).get(key, 0), "added": 0, "burden": 0, "share": round(value / total, 4)}
                   for key, value in weights.items() if value > 0), key=lambda row: row["weight"], reverse=True)


def overview(base: Path, limit: int = 120) -> dict:
    """All recent sessions combined: totals, per day, per project, per agent and model, activity mix, avoidable patterns."""
    from datetime import datetime
    sessions = list_sessions(base, limit)["sessions"]
    sums = {key: 0 for key in ("input_fresh", "cache_read", "cache_write", "output", "calls", "rounds", "tool_calls", "compactions", "cold_rebuilds", "cold_rebuild_tokens", "over_100k")}
    days: dict[str, dict] = {}
    groups: dict[str, dict[str, float]] = {"project": {}, "agent": {}, "model": {}, "stage": {}, "action": {}}
    counts: dict[str, dict[str, int]] = {"project": {}, "agent": {}, "model": {}}
    flags: dict[str, dict] = {}
    for row in sessions:
        totals = row["totals"]
        for key in sums:
            sums[key] += totals.get(key, 0)
        day = datetime.fromtimestamp(row["updated_at"]).strftime("%Y-%m-%d")
        bucket = days.setdefault(day, {"day": day, "total": 0, "sessions": 0, "claude": 0, "codex": 0})
        bucket["total"] += row["total"]; bucket["sessions"] += 1; bucket[row["backend"]] = bucket.get(row["backend"], 0) + row["total"]
        for name, key in (("project", Path(row["cwd"]).name or "unknown"), ("agent", row["backend"]), ("model", ", ".join(totals.get("models") or []) or row["backend"])):
            groups[name][key] = groups[name].get(key, 0) + row["total"]
            counts[name][key] = counts[name].get(key, 0) + 1
        for name in ("stage", "action"):
            for key, weight in (row.get(f"by_{name}") or {}).items():
                groups[name][key] = groups[name].get(key, 0) + weight
        for flag, item in (row.get("flags") or {}).items():
            merged = flags.setdefault(flag, {"flag": flag, "count": 0, "burden": 0, "examples": []})
            merged["count"] += item["count"]; merged["burden"] += item["burden"]
    context = sums["input_fresh"] + sums["cache_read"]
    total = sum(row["total"] for row in sessions)
    report = {"sessions": len(sessions), "total": total, "subagent_total": sum(row["subagent_total"] for row in sessions),
              "totals": {**sums, "total": context + sums["output"], "cache_hit": round(sums["cache_read"] / context, 3) if context else 0.0,
                         "peak_context": max((row["totals"]["peak_context"] for row in sessions), default=0)},
              "days": sorted(days.values(), key=lambda item: item["day"]),
              "by_project": _shares(groups["project"], counts["project"])[:12], "by_agent": _shares(groups["agent"], counts["agent"]), "by_model": _shares(groups["model"], counts["model"])[:8],
              "by_stage": _shares(groups["stage"]), "by_action": _shares(groups["action"]),
              "flags": sorted(flags.values(), key=lambda item: item["burden"], reverse=True), "rounds": [],
              "top_sessions": [{key: row[key] for key in ("id", "title", "backend", "cwd", "total", "updated_at")} for row in sorted(sessions, key=lambda r: r["total"], reverse=True)[:8]],
              "concentration": round(sum(sorted((row["total"] for row in sessions), reverse=True)[:max(1, len(sessions) // 10)]) / total, 3) if total else 0.0,
              "actions": ACTIONS, "stages": STAGES}
    report["advice"] = advise(report)
    report["takeaway"] = takeaway(report)
    return report


def _locate(base: Path, key: str) -> dict:
    try:
        entry = json.loads((_store_dir(base) / "summaries.json").read_text()).get(key)
    except (OSError, ValueError):
        entry = None
    if not entry or not Path(entry["path"]).is_file():
        raise KeyError(key)
    summary = entry["summary"]
    return {"backend": summary["backend"], "account": summary["account"], "session_id": summary["session_id"], "path": Path(entry["path"]),
            "home": Path(entry["home"]), "summary": summary}


def _labels_path(base: Path, key: str) -> Path:
    folder = _store_dir(base) / "labels"
    folder.mkdir(exist_ok=True)
    return folder / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', key)}.json"


def _step_signature(step: dict) -> str:
    return f"{step['tool']}|{step['detail'][:160]}"


def analyze(base: Path, key: str, top: int = 60) -> dict:
    """Full profile of one session: rounds, activity breakdown, heaviest steps, flags, advice."""
    row = _locate(base, key)
    own = _parse(row["backend"], row["path"])
    try:
        saved = json.loads(_labels_path(base, key).read_text())
    except (OSError, ValueError):
        saved = {}
    labels = saved.get("labels", {})
    for step in own["steps"]:
        stage = labels.get(_step_signature(step)) if step["kind"] == "tool" else None
        if stage in STAGES:
            step["stage"] = stage
    subagents = []
    for child, meta in _children(row):
        parsed = _parse(row["backend"], child)
        subagents.append({"name": meta.get("description") or meta.get("agentType") or child.stem, "type": meta.get("agentType") or "", "totals": parsed["totals"],
                          "by_stage": _group(parsed["steps"], "stage")[:4], "tool_use_id": meta.get("toolUseId") or ""})
        for step in own["steps"]:
            if meta.get("toolUseId") and step.get("call_id") == meta["toolUseId"]:
                step["child_total"] = parsed["totals"]["total"]
    steps = own["steps"]
    flagged: dict[str, dict] = {}
    for step in steps:
        for flag in step.get("flags", []):
            group = flagged.setdefault(flag, {"flag": flag, "count": 0, "burden": 0, "examples": []})
            group["count"] += 1; group["burden"] += step["burden"]
            if len(group["examples"]) < 3:
                group["examples"].append(step["detail"][:120])
    context_total = own["totals"]["input_fresh"] + own["totals"]["cache_read"]
    attributed = sum(step["burden"] for step in steps)
    public = lambda step: {k: step.get(k) for k in ("i", "round", "kind", "tool", "action", "stage", "detail", "added", "result", "burden", "weight", "error", "flags", "child_total") if step.get(k) not in (None, [], False)}
    result = {**row["summary"], "totals": own["totals"], "rounds": own["rounds"], "context_curve": own["context_curve"],
              "started_at": own["started_at"], "ended_at": own["ended_at"],
              "by_action": _group(steps, "action"), "by_stage": _group(steps, "stage"),
              "unattributed": max(0, context_total - attributed), "context_total": context_total,
              "top_steps": [public(step) for step in sorted(steps, key=lambda s: s["weight"], reverse=True)[:top]],
              "flags": sorted(flagged.values(), key=lambda group: group["burden"], reverse=True),
              "subagents": subagents, "subagent_total": sum(agent["totals"]["total"] for agent in subagents),
              "labelled_by": saved.get("model") or "rules", "dan": dan_links(base).get(row["session_id"])}
    result["total"] = own["totals"]["total"] + result["subagent_total"]
    result["advice"] = advise(result)
    result["takeaway"] = takeaway(result)
    return result


def _examples(label: str, group: dict) -> str:
    return f" {label}: " + "; ".join(group["examples"]) if group.get("examples") else ""


def advise(report: dict) -> list[dict]:
    """Concrete changes ranked by the tokens they address."""
    totals, tips = report["totals"], []
    weight = sum(row["weight"] for row in report["by_stage"]) or 1
    stage = {row["key"]: row for row in report["by_stage"]}
    action = {row["key"]: row for row in report["by_action"]}
    flags = {group["flag"]: group for group in report["flags"]}
    def add(tokens, title, detail):
        if tokens > 0:
            tips.append({"tokens": int(tokens), "title": title, "detail": detail})
    overhead = stage.get("overhead", {}).get("weight", 0)
    if overhead / weight > 0.25:
        add(overhead, "Fixed context is a large share of every call", "System prompt, tool definitions, and project instructions are re-read on each of "
            f"{totals['calls']} calls. Shorten CLAUDE.md/AGENTS.md, disable unused MCP servers and skills, and start a new session per task instead of extending this one.")
    if "oversized_output" in flags:
        group = flags["oversized_output"]
        add(group["burden"], f"{group['count']} tool results over {OVERSIZED // 1000}k tokens", "Limit output at the source: read with offset/limit, pipe commands through head/tail, use quiet flags (pytest -q --tb=short)." + _examples("Largest", group))
    if "duplicate_read" in flags:
        group = flags["duplicate_read"]
        add(group["burden"], f"{group['count']} files re-read without changes", "The file was already in context. Usually follows a long session where the agent loses track; compact or split the task earlier." + _examples("Examples", group))
    if "repeated_command" in flags:
        group = flags["repeated_command"]
        add(group["burden"], f"{group['count']} repeated identical commands", "Same command run again with no edit in between." + _examples("Examples", group))
    if "failed_call" in flags and flags["failed_call"]["count"] >= max(5, 0.1 * totals["tool_calls"]):
        group = flags["failed_call"]
        add(group["burden"], f"{group['count']} failed tool calls", "Errors still enter the context. Recurring causes are wrong paths, missing dependencies, and permission denials; fix the environment or state it in the project instructions.")
    reading = action.get("read", {}).get("weight", 0) + action.get("search", {}).get("weight", 0)
    if reading / weight > 0.35:
        add(reading, "Reading and searching dominate", "Search before reading, read ranges instead of whole files, and delegate broad exploration to a subagent so only its summary enters this context.")
    checking = stage.get("verify", {}).get("weight", 0) + stage.get("debug", {}).get("weight", 0)
    if checking / weight > 0.3:
        add(checking, "Test and debug output dominates", "Run the failing test only, stop at first failure (-x), and keep output short; long logs are re-read on every later call.")
    if totals["cold_rebuilds"]:
        add(totals["cold_rebuild_tokens"], f"{totals['cold_rebuilds']} cold prompt-cache rebuilds", "The whole context was re-sent uncached, typically after an idle gap longer than the cache lifetime or a model switch. Finish a task in one sitting or start fresh after a break.")
    if "sessions" in report and totals["over_100k"] > 0.2 * totals["total"]:
        add(totals["over_100k"], "Much of the spend is context beyond 100k tokens", "Long-lived sessions keep re-reading old work. Start a new session per task and compact at natural boundaries; the amount shown is what was re-read above 100k across sessions.")
    elif "sessions" not in report and totals["calls"] > 120 and not totals["compactions"] and totals["peak_context"] > 120_000:
        add(totals["over_100k"], "Long session without compaction", f"{totals['calls']} calls grew the context to {totals['peak_context']:,} tokens; the amount shown is what was re-read above 100k. Split into smaller tasks or compact at natural boundaries.")
    if report["subagent_total"] > 0.4 * report["total"]:
        add(report["subagent_total"], "Subagents account for much of the spend", "Each subagent pays its own fixed context and re-discovers the project. Give narrower briefs with file paths, and avoid spawning one for work a single search would answer.")
    heavy = max(report["rounds"], key=lambda r: r["total"], default=None)
    if heavy and len(report["rounds"]) > 3 and heavy["total"] > 0.5 * totals["total"]:
        add(heavy["total"], "One chat round used over half the session", f"Round {heavy['index'] + 1} (“{heavy['title'][:80]}”). Broad requests in a long-lived session are the expensive combination; give it its own session.")
    return sorted(tips, key=lambda tip: tip["tokens"], reverse=True)


# One remedy per stage: the single most effective change when that stage dominates.
STAGE_REMEDY = {
    "orient": "Put the project layout and conventions in a short AGENTS.md/CLAUDE.md so the agent stops rediscovering them each session.",
    "locate": "Point to the files in your request, and have the agent search (grep/glob) before opening anything.",
    "understand": "Ask for ranged reads of the relevant functions instead of whole files, or delegate the reading to a subagent that returns a summary.",
    "plan": "Agree the plan once in a short note and tell the agent to follow it rather than re-planning every round.",
    "implement": "This is the productive part; keep edits targeted (patch, not rewrite) and split unrelated changes into separate sessions.",
    "verify": "Run only the affected tests with quiet flags (e.g. pytest -q -x path::test) so long logs are not re-read on every later call.",
    "debug": "Reproduce with the smallest failing command and cap its output (tail/head); start a fresh session once the cause is known.",
    "review": "Review diffs by file or with --stat first instead of printing full diffs into the context.",
    "environment": "Fix the environment once (dependencies, paths, permissions) and record it in the project instructions so setup is not repeated.",
    "vcs": "Batch commits and use short git output (--oneline, --stat); avoid dumping full logs or diffs.",
    "research": "Fetch the specific page or section you need and ask for a summary rather than pulling whole documents into context.",
    "delegate": "Give subagents narrow briefs with file paths and ask for short reports; each one pays its own fixed context.",
    "document": "Write docs in one pass at the end of the task instead of updating them after every change.",
    "data": "Print summaries (shape, head, key metrics) instead of full tables or logs from scripts and notebooks.",
    "communicate": "Ask for concise answers, and avoid restating earlier results the agent already has in context.",
    "overhead": "Trim fixed context: shorten CLAUDE.md/AGENTS.md, disable unused MCP servers and skills, and start a new session per task.",
}


def takeaway(report: dict) -> dict | None:
    """The one thing to know: which stage costs the most, and the single change that addresses it."""
    stages = [row for row in report.get("by_stage") or [] if row.get("weight", 0) > 0]
    if not stages:
        return None
    total = sum(row["weight"] for row in stages) or 1
    top = stages[0]
    share = top["weight"] / total
    runner = stages[1] if len(stages) > 1 else None
    label = top["key"]
    headline = f"{round(share * 100)}% of tokens went to {label}"
    if runner and runner["weight"] / total >= 0.15:
        headline += f", then {runner['key']} ({round(runner['weight'] / total * 100)}%)"
    return {"stage": label, "share": round(share, 4), "tokens": int(top["weight"]), "headline": headline + ".",
            "meaning": STAGES.get(label, ""), "action": STAGE_REMEDY.get(label, "")}


# ---- stage labelling with a decision model -----------------------------------------------------

DEFAULT_CLASSIFIER = "jev-latest"  # TypeSafe Jev on OpenRouter; served by the Decisions endpoint, not chat completions
MIN_CONFIDENCE = 0.4


def settings(base: Path) -> dict:
    try:
        saved = json.loads((_store_dir(base) / "settings.json").read_text())
    except (OSError, ValueError):
        saved = {}
    _load_env()
    return {"model": saved.get("model") or os.environ.get("DAN_USAGE_CLASSIFIER_MODEL") or DEFAULT_CLASSIFIER, "configured": bool(_api_key()), "base_url": _base_url()}


def write_settings(base: Path, model: str) -> dict:
    (_store_dir(base) / "settings.json").write_text(json.dumps({"model": model.strip()}))
    return settings(base)


def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def _api_key() -> str:
    return os.environ.get("DAN_OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("DAN_LLM_API_KEY") or ""


def _base_url() -> str:
    url = (os.environ.get("DAN_LLM_BASE_URL") or "").rstrip("/")
    return url if "openrouter.ai" in url else "https://openrouter.ai/api/v1"


def _decide(model: str, state: dict, questions: dict) -> dict:
    """One Decisions request: {question: {"choice", "confidence"}}."""
    body = json.dumps({"model": model, "state": state, "questions": questions}).encode()
    request = Request(_base_url().removesuffix("/v1") + "/alpha/decisions", data=body,
                      headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json", "X-Title": "DAN"})
    with urlopen(request, timeout=90) as response:
        return json.loads(response.read()).get("answers") or {}


def classify(base: Path, key: str, batch: int = 24, cap: int = 360, decide=None) -> dict:
    """Label the heaviest distinct tool steps with a stage; results are cached per session.

    Each batch sends the round's request plus, per step, the tool name, truncated main argument,
    result size, failure, and the two neighbouring steps. Tool output is never sent.
    """
    config = settings(base)
    if not decide and not config["configured"]:
        raise RuntimeError("Add an OpenRouter key (DAN_LLM_API_KEY) to use the classifier")
    decide = decide or (lambda state, questions: _decide(config["model"], state, questions))
    row = _locate(base, key)
    own = _parse(row["backend"], row["path"])
    tools = [step for step in own["steps"] if step["kind"] == "tool"]
    position = {id(step): index for index, step in enumerate(tools)}
    unique: dict[str, dict] = {}
    for step in sorted(tools, key=lambda s: s["weight"], reverse=True):
        unique.setdefault(_step_signature(step), step)
    chosen = list(unique.items())[:cap]
    brief = lambda step: f"{step['tool']} {step['detail'][:80]}" + (" (failed)" if step["error"] else "")
    labels: dict[str, str] = {}
    uncertain = 0
    for start in range(0, len(chosen), batch):
        part = chosen[start:start + batch]
        state, questions = {}, {}
        for index, (_, step) in enumerate(part):
            at = position[id(step)]
            state[f"s{index}"] = {"user_request": own["rounds"][step["round"]]["title"][:160] if own["rounds"] else "", "tool": step["tool"], "argument": step["detail"][:200],
                                  "result_tokens": step["result"], "failed": step["error"],
                                  "previous_step": brief(tools[at - 1]) if at else "", "next_step": brief(tools[at + 1]) if at + 1 < len(tools) else ""}
            questions[f"s{index}"] = {"type": "choice", "criteria": STAGES,
                                      "instructions": f"Step s{index} of a coding agent's session: which stage of work does it belong to? Judge intent from the user request and neighbouring steps; the same command can be verify, debug, or data."}
        try:
            answers = decide(state, questions)
        except (OSError, ValueError, KeyError) as exc:
            if not labels:
                raise RuntimeError(f"Classifier request failed: {type(exc).__name__}") from exc
            break
        for index, (signature, _) in enumerate(part):
            answer = answers.get(f"s{index}") or {}
            if answer.get("choice") in STAGES and float(answer.get("confidence") or 0) >= MIN_CONFIDENCE:
                labels[signature] = answer["choice"]
            else:
                uncertain += 1  # keep the rule-based label
    _labels_path(base, key).write_text(json.dumps({"model": config["model"], "labels": labels, "uncertain": uncertain}))
    return analyze(base, key)
