"""Server-side mention resolution and context budget management.

Resolves structured @mentions from the chat frontend into LLM-ready context
blocks.  Supports file, code, docs, and past-chat mention types with
token-budget-aware packing.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from dan.server.chat_store import ChatStore

logger = logging.getLogger(__name__)

try:
    import tiktoken

    _tiktoken_available = True
except ImportError:
    _tiktoken_available = False


# ---------------------------------------------------------------------------
# Token estimation (standalone — avoids circular import with chat_manager)
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str, model: str = "") -> int:
    if _tiktoken_available:
        try:
            enc = tiktoken.encoding_for_model(model)
            return len(enc.encode(text))
        except KeyError:
            try:
                enc = tiktoken.get_encoding("cl100k_base")
                return len(enc.encode(text))
            except Exception:
                pass
        except Exception:
            pass
    return len(text) // 4


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

MENTION_TYPE = Literal[
    "node", "workflow", "subgraph", "file", "code", "docs", "chat",
    "symbol", "folder",
]


class MentionRef(BaseModel):
    type: MENTION_TYPE
    identifier: str


class ResolvedMention(BaseModel):
    type: str
    identifier: str
    resolved_content: str
    token_count: int


# ---------------------------------------------------------------------------
# Security / file filtering
# ---------------------------------------------------------------------------

DENIED_PATTERNS: frozenset[str] = frozenset(
    {
        ".env",
        "credentials",
        "node_modules",
        "__pycache__",
        ".git",
        ".DS_Store",
        "venv",
        ".venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
    }
)

DENIED_SUBSTRINGS: tuple[str, ...] = (
    ".env",
    "credential",
    "secret",
    ".pem",
    ".key",
)

SAFE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".md",
        ".py",
        ".json",
        ".yaml",
        ".yml",
        ".txt",
        ".tex",
        ".csv",
        ".toml",
        ".cfg",
        ".html",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".sh",
    }
)

_MAX_FILE_LIST = 500


def _is_path_safe(path: Path, workspace: Path) -> bool:
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(workspace):
            return False
    except (ValueError, OSError):
        return False
    try:
        rel = resolved.relative_to(workspace)
    except ValueError:
        return False
    for part in rel.parts:
        lower = part.lower()
        if part in DENIED_PATTERNS:
            return False
        if any(tok in lower for tok in DENIED_SUBSTRINGS):
            return False
    return True


def _truncate_file(content: str, head: int = 100, tail: int = 50) -> str:
    lines = content.splitlines()
    if len(lines) <= head + tail:
        return content
    omitted = len(lines) - head - tail
    return (
        "\n".join(lines[:head])
        + f"\n\n[... {omitted} lines truncated ...]\n\n"
        + "\n".join(lines[-tail:])
    )


# ---------------------------------------------------------------------------
# Per-type resolvers
# ---------------------------------------------------------------------------


class FileResolver:
    """Resolve ``@file:path`` mentions by reading workspace files."""

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    def resolve(self, file_path: str, model: str = "") -> ResolvedMention:
        target = (self.workspace / file_path).resolve()
        if not _is_path_safe(target, self.workspace):
            return ResolvedMention(
                type="file",
                identifier=file_path,
                resolved_content=f"[Access denied: {file_path}]",
                token_count=10,
            )
        if not target.is_file():
            return ResolvedMention(
                type="file",
                identifier=file_path,
                resolved_content=f"[File not found: {file_path}]",
                token_count=10,
            )
        try:
            raw = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ResolvedMention(
                type="file",
                identifier=file_path,
                resolved_content=f"[Error reading {file_path}: {exc}]",
                token_count=10,
            )
        content = _truncate_file(raw)
        full = f"--- File: {file_path} ---\n{content}"
        return ResolvedMention(
            type="file",
            identifier=file_path,
            resolved_content=full,
            token_count=_estimate_tokens(full, model),
        )

    def resolve_folder(self, folder_path: str, model: str = "") -> ResolvedMention:
        target = (self.workspace / folder_path).resolve()
        if not _is_path_safe(target, self.workspace):
            return ResolvedMention(
                type="folder",
                identifier=folder_path,
                resolved_content=f"[Access denied: {folder_path}]",
                token_count=10,
            )
        if not target.is_dir():
            return ResolvedMention(
                type="folder",
                identifier=folder_path,
                resolved_content=f"[Folder not found: {folder_path}]",
                token_count=10,
            )
        try:
            entries = sorted(
                target.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
            listing: list[str] = []
            for entry in entries[:_MAX_FILE_LIST]:
                if entry.name in DENIED_PATTERNS or entry.name.startswith("."):
                    continue
                kind = "dir" if entry.is_dir() else "file"
                listing.append(f"  [{kind}] {entry.name}")
            content = "\n".join(listing) if listing else "(empty directory)"
            full = f"--- Folder: {folder_path} ---\n{content}"
            return ResolvedMention(
                type="folder",
                identifier=folder_path,
                resolved_content=full,
                token_count=_estimate_tokens(full, model),
            )
        except OSError as exc:
            return ResolvedMention(
                type="folder",
                identifier=folder_path,
                resolved_content=f"[Error reading {folder_path}: {exc}]",
                token_count=10,
            )

    def list_files(
        self, extensions: frozenset[str] | None = None
    ) -> list[str]:
        exts = extensions or SAFE_EXTENSIONS
        results: list[str] = []
        for root, dirs, files in os.walk(self.workspace):
            dirs[:] = [
                d
                for d in dirs
                if d not in DENIED_PATTERNS and not d.startswith(".")
            ]
            for f in sorted(files):
                if len(results) >= _MAX_FILE_LIST:
                    return results
                fp = Path(root) / f
                if (
                    fp.suffix.lower() in exts
                    and _is_path_safe(fp, self.workspace)
                ):
                    try:
                        results.append(str(fp.relative_to(self.workspace)))
                    except ValueError:
                        pass
        return results


class CodeResolver:
    """Resolve ``@code:NodeName.field`` mentions from the active graph."""

    EXTRACTABLE_FIELDS: frozenset[str] = frozenset(
        {
            "prompt_template",
            "system_prompt",
            "tool_code",
            "output_schema",
            "config",
            "condition",
        }
    )

    def resolve(
        self,
        identifier: str,
        graph_dict: dict[str, Any],
        model: str = "",
    ) -> ResolvedMention:
        parts = identifier.split(".", 1)
        if len(parts) != 2:
            return ResolvedMention(
                type="code",
                identifier=identifier,
                resolved_content=(
                    f"[Invalid code ref: {identifier} — use NodeName.field]"
                ),
                token_count=10,
            )
        node_name, field = parts
        node = self._find_node(node_name, graph_dict)
        if node is None:
            return ResolvedMention(
                type="code",
                identifier=identifier,
                resolved_content=f"[Node not found: {node_name}]",
                token_count=10,
            )
        config = node.get("config", {}) or {}
        value = config.get(field) or node.get(field)
        if value is None:
            avail = sorted(set(list(config.keys()) + list(node.keys())))
            return ResolvedMention(
                type="code",
                identifier=identifier,
                resolved_content=(
                    f"[Field '{field}' not found on '{node_name}'. "
                    f"Available: {', '.join(avail)}]"
                ),
                token_count=15,
            )
        content = (
            json.dumps(value, indent=2)
            if isinstance(value, (dict, list))
            else str(value)
        )
        full = f"--- Code: {identifier} ---\n{content}"
        return ResolvedMention(
            type="code",
            identifier=identifier,
            resolved_content=full,
            token_count=_estimate_tokens(full, model),
        )

    @staticmethod
    def list_code_refs(graph_dict: dict[str, Any]) -> list[str]:
        refs: list[str] = []
        for n in graph_dict.get("nodes", []):
            if not isinstance(n, dict):
                continue
            name = n.get("name", n.get("id", ""))
            config = n.get("config", {}) or {}
            for field in CodeResolver.EXTRACTABLE_FIELDS:
                if config.get(field) or n.get(field):
                    refs.append(f"{name}.{field}")
        return refs

    @staticmethod
    def _find_node(
        name: str, graph_dict: dict[str, Any]
    ) -> dict[str, Any] | None:
        for n in graph_dict.get("nodes", []):
            if not isinstance(n, dict):
                continue
            if (
                n.get("name", "").lower() == name.lower()
                or n.get("id", "") == name
            ):
                return n
        return None


class DocsResolver:
    """Resolve ``@docs:name`` mentions from project documentation."""

    def __init__(self, docs_dir: Path):
        self.docs_dir = docs_dir.resolve()
        self.project_root = self.docs_dir.parent

    def resolve(self, doc_name: str, model: str = "") -> ResolvedMention:
        candidates = [
            self.docs_dir / doc_name,
            self.docs_dir / f"{doc_name}.md",
        ]
        if doc_name.lower() in ("readme", "readme.md"):
            candidates.insert(0, self.project_root / "README.md")

        target = next((c for c in candidates if c.is_file()), None)
        if target is None:
            avail = self.list_docs()
            return ResolvedMention(
                type="docs",
                identifier=doc_name,
                resolved_content=(
                    f"[Doc not found: {doc_name}. "
                    f"Available: {', '.join(avail[:10])}]"
                ),
                token_count=15,
            )
        try:
            raw = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ResolvedMention(
                type="docs",
                identifier=doc_name,
                resolved_content=f"[Error reading doc {doc_name}: {exc}]",
                token_count=10,
            )
        lines = raw.splitlines()
        if len(lines) > 200:
            raw = (
                "\n".join(lines[:200])
                + f"\n\n[... {len(lines) - 200} lines truncated ...]"
            )
        full = f"--- Docs: {doc_name} ---\n{raw}"
        return ResolvedMention(
            type="docs",
            identifier=doc_name,
            resolved_content=full,
            token_count=_estimate_tokens(full, model),
        )

    def list_docs(self) -> list[str]:
        if not self.docs_dir.is_dir():
            return []
        results = [p.stem for p in sorted(self.docs_dir.glob("*.md"))]
        readme = self.project_root / "README.md"
        if readme.is_file() and "README" not in results:
            results.insert(0, "README")
        return results


class ChatHistoryResolver:
    """Resolve ``@chat:thread-id`` mentions from past conversation threads."""

    def __init__(self, chat_store: ChatStore):
        self._store = chat_store

    def resolve(
        self,
        thread_id: str,
        workflow_id: str,
        model: str = "",
        max_summary_tokens: int = 2000,
    ) -> ResolvedMention:
        thread = self._store.get_thread(workflow_id, thread_id)
        if thread is None:
            for t in self._store.list_threads(workflow_id):
                if thread_id.lower() in t.get("title", "").lower():
                    thread = self._store.get_thread(workflow_id, t["id"])
                    break
        if thread is None:
            return ResolvedMention(
                type="chat",
                identifier=thread_id,
                resolved_content=f"[Chat thread not found: {thread_id}]",
                token_count=10,
            )
        parts: list[str] = []
        for msg in thread.messages:
            if msg.role == "user":
                parts.append(f"[User]: {msg.content}")
            elif msg.role == "assistant" and msg.content:
                first = msg.content.split(". ")[0]
                if len(first) > 200:
                    first = first[:200] + "..."
                parts.append(f"[Assistant]: {first}")
        summary = "\n".join(parts)
        while (
            _estimate_tokens(summary, model) > max_summary_tokens and parts
        ):
            parts.pop()
            summary = "\n".join(parts)

        title = thread.title or "Untitled"
        full = f"--- Past Chat: {title} ---\n{summary}"
        return ResolvedMention(
            type="chat",
            identifier=thread_id,
            resolved_content=full,
            token_count=_estimate_tokens(full, model),
        )

    def list_threads(self, workflow_id: str) -> list[dict[str, str]]:
        return [
            {"id": t["id"], "title": t.get("title", "Untitled")}
            for t in self._store.list_threads(workflow_id)[:20]
        ]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class MentionResolver:
    """Dispatches mention resolution to per-type resolvers."""

    def __init__(
        self,
        workspace_root: str | Path,
        chat_store: ChatStore,
        docs_dir: str | Path = "",
    ):
        ws = Path(workspace_root).resolve()
        self.file_resolver = FileResolver(ws)
        self.code_resolver = CodeResolver()
        self.docs_resolver = DocsResolver(
            Path(docs_dir) if docs_dir else ws / "docs"
        )
        self.chat_resolver = ChatHistoryResolver(chat_store)

    def resolve(
        self,
        mention: MentionRef,
        workflow_id: str,
        graph_dict: dict[str, Any] | None = None,
        model: str = "",
    ) -> ResolvedMention:
        try:
            if mention.type in ("node", "workflow", "subgraph"):
                return ResolvedMention(
                    type=mention.type,
                    identifier=mention.identifier,
                    resolved_content="",
                    token_count=0,
                )
            if mention.type == "file":
                return self.file_resolver.resolve(mention.identifier, model)
            if mention.type == "code":
                if graph_dict is None:
                    return ResolvedMention(
                        type="code",
                        identifier=mention.identifier,
                        resolved_content="[No graph context for code resolution]",
                        token_count=10,
                    )
                return self.code_resolver.resolve(
                    mention.identifier, graph_dict, model
                )
            if mention.type == "docs":
                return self.docs_resolver.resolve(mention.identifier, model)
            if mention.type == "chat":
                return self.chat_resolver.resolve(
                    mention.identifier, workflow_id, model
                )
            if mention.type == "symbol":
                return ResolvedMention(
                    type="symbol",
                    identifier=mention.identifier,
                    resolved_content=f"[Symbol: {mention.identifier}]",
                    token_count=_estimate_tokens(mention.identifier, model) + 5,
                )
            if mention.type == "folder":
                return self.file_resolver.resolve_folder(
                    mention.identifier, model,
                )
            return ResolvedMention(
                type=mention.type,
                identifier=mention.identifier,
                resolved_content=f"[Unknown mention type: {mention.type}]",
                token_count=10,
            )
        except Exception as exc:
            logger.warning(
                "Mention resolution failed for %s:%s — %s",
                mention.type,
                mention.identifier,
                exc,
            )
            return ResolvedMention(
                type=mention.type,
                identifier=mention.identifier,
                resolved_content=f"[Resolution error: {mention.type}:{mention.identifier}]",
                token_count=10,
            )

    def resolve_all(
        self,
        mentions: list[MentionRef],
        workflow_id: str,
        graph_dict: dict[str, Any] | None = None,
        model: str = "",
    ) -> list[ResolvedMention]:
        return [
            r
            for m in mentions
            if (
                r := self.resolve(m, workflow_id, graph_dict, model)
            ).resolved_content
        ]


# ---------------------------------------------------------------------------
# Context budget packing
# ---------------------------------------------------------------------------


def pack_context(
    system_content: str,
    mention_blocks: list[ResolvedMention],
    history: list[dict[str, str]],
    user_message: str,
    context_window: int,
    max_ratio: float = 0.8,
    model: str = "",
    recent_count: int = 10,
) -> list[dict[str, str]]:
    """Build token-budget-aware message list.

    Priority (highest first):
    1. System prompt (incl. graph summary) — always kept in full
    2. Mentions — up to 35% of remaining budget, truncated longest-first
    3. Recent history messages
    4. Older history — truncated / dropped
    """
    max_tokens = int(context_window * max_ratio)
    sys_tokens = _estimate_tokens(system_content, model) + 4
    usr_tokens = _estimate_tokens(user_message, model) + 4
    remaining = max_tokens - sys_tokens - usr_tokens

    mention_budget = int(remaining * 0.35)
    mention_text = _build_mention_block(mention_blocks, mention_budget, model)
    m_tokens = _estimate_tokens(mention_text, model) if mention_text else 0
    remaining -= m_tokens

    if mention_text:
        full_system = f"{system_content}\n\n{mention_text}"
    else:
        full_system = system_content

    messages: list[dict[str, str]] = [
        {"role": "system", "content": full_system}
    ]

    if history:
        messages.extend(
            _fit_history(history, remaining, model, recent_count)
        )

    messages.append({"role": "user", "content": user_message})
    return messages


def _build_mention_block(
    mentions: list[ResolvedMention],
    budget: int,
    model: str = "",
) -> str:
    if not mentions:
        return ""
    total = sum(m.token_count for m in mentions)
    if total <= budget:
        return "## Context from mentions\n\n" + "\n\n".join(
            m.resolved_content for m in mentions
        )

    adjusted = sorted(
        mentions, key=lambda m: m.token_count, reverse=True
    )
    while (
        sum(m.token_count for m in adjusted) > budget and adjusted
    ):
        longest = adjusted[0]
        lines = longest.resolved_content.splitlines()
        if len(lines) > 20:
            half = len(lines) // 2
            trimmed = (
                "\n".join(lines[:half])
                + "\n[... truncated to fit budget ...]"
            )
            adjusted[0] = ResolvedMention(
                type=longest.type,
                identifier=longest.identifier,
                resolved_content=trimmed,
                token_count=_estimate_tokens(trimmed, model),
            )
        else:
            adjusted.pop(0)

    if not adjusted:
        return ""
    return "## Context from mentions\n\n" + "\n\n".join(
        m.resolved_content for m in adjusted
    )


def _fit_history(
    history: list[dict[str, str]],
    budget: int,
    model: str = "",
    recent_count: int = 10,
) -> list[dict[str, str]]:
    if not history:
        return []

    def _tok(msgs: list[dict[str, str]]) -> int:
        return sum(
            4 + _estimate_tokens(m.get("content", ""), model) for m in msgs
        )

    if _tok(history) <= budget:
        return list(history)

    if len(history) > recent_count:
        recent = history[-recent_count:]
        older = history[:-recent_count]
    else:
        recent = list(history)
        older = []

    r_tokens = _tok(recent)
    if r_tokens > budget:
        while len(recent) > 1 and _tok(recent) > budget:
            recent.pop(0)
        return recent

    if not older:
        return recent

    older_budget = budget - r_tokens
    truncated: list[dict[str, str]] = []
    for m in older:
        c = m.get("content", "")
        if m.get("role") == "assistant" and len(c) > 200:
            sents = c.split(". ")
            if len(sents) > 3:
                truncated.append(
                    {**m, "content": f"{sents[0]}. [...] {sents[-1]}"}
                )
            else:
                truncated.append(m)
        else:
            truncated.append(m)

    if _tok(truncated) <= older_budget:
        return truncated + recent

    while truncated and _tok(truncated) > older_budget:
        truncated.pop(0)
    return truncated + recent
