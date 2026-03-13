from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

from dan.server.capability_handlers import (
    handle_cancel_run,
    handle_export_workflow,
    handle_get_activity,
    handle_get_publish_status,
    handle_get_run_status,
    handle_list_active_runs,
    handle_list_graphs,
    handle_resume_run,
    handle_search_workflow_history,
    handle_share_workflow,
    handle_start_run,
)
from dan.server.chat_manager import ChatStreamEvent

from .classifier import ClassificationResult, IntentCategory, RouteMode, search_local_files
from .classifier import _has_filesystem_path, _looks_like_complex_direct_task, _looks_like_direct_web_lookup
from .context_resolver import ResolvedContext
from .identity import format_prefix
from .models import SurfaceMessage
from .policy import ClarificationRequest
from .progress import ProgressReporter

logger = logging.getLogger(__name__)

_FILE_REF_ACTIONS = frozenset({"review", "read", "summarize", "proofread", "analyze"})
_FILE_REF_NOUNS = frozenset(
    {"paper", "file", "document", "report", "article", "thesis", "manuscript"},
)
_INLINE_DOCUMENT_CHAR_LIMIT = 12000
_PROMPT_CONTEXT_BUDGET = int(os.environ.get("DAN_PROMPT_CONTEXT_BUDGET", "6000"))
_INLINE_DOCUMENT_SYSTEM_INSTRUCTIONS = (
    "Inline document text provided in the user message counts as already read source material. "
    "Do not call pdf_read or file_read when the source text is already included inline. "
    "Summarize or answer directly from the provided text, and if the excerpt is marked truncated, "
    "be explicit that the answer is based only on the provided excerpt."
)


@dataclass
class HandlerResult:
    content: str = ""
    attachments: list[Path] | None = None
    project_update: dict[str, Any] | None = None
    task_update: dict[str, Any] | None = None
    stream_channel_id: str | None = None
    events: AsyncIterator[ChatStreamEvent] | None = None
    clarification: ClarificationRequest | None = None


class Handler(Protocol):
    async def handle(
        self,
        msg: SurfaceMessage,
        context: ResolvedContext,
        classification: ClassificationResult,
    ) -> HandlerResult:
        ...


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[IntentCategory, Handler] = {}

    def register(self, intent: IntentCategory, handler: Handler) -> None:
        self._handlers[intent] = handler

    def get(self, intent: IntentCategory) -> Handler:
        handler = self._handlers.get(intent)
        if handler is None:
            logger.warning("No handler for intent %s, falling back to ASK", intent)
            handler = self._handlers.get(IntentCategory.ASK)
        if handler is None:
            raise KeyError(f"No handler registered for intent {intent!r} and no fallback available")
        return handler


def _workflow_id_from(msg: SurfaceMessage, context: ResolvedContext) -> str:
    metadata_workflow_id = str(msg.metadata.get("workflow_id") or "").strip()
    if context.project.linked_workflow_ids:
        if not metadata_workflow_id or metadata_workflow_id == "_scratch":
            return context.project.linked_workflow_ids[-1]
    if metadata_workflow_id:
        return metadata_workflow_id
    return "_scratch"


def _project_history(context: ResolvedContext) -> list[dict[str, str]]:
    turns = context.task.turns[-20:]
    return [{"role": turn.role, "content": turn.content} for turn in turns]


def _message_history(msg: SurfaceMessage, context: ResolvedContext) -> list[dict[str, str]]:
    request_history = list(msg.metadata.get("request_history") or [])
    project_history = _project_history(context)
    system_messages = [
        item for item in request_history
        if isinstance(item, dict) and item.get("role") == "system" and item.get("content")
    ]
    if project_history:
        return [*system_messages, *project_history]
    trimmed_request = [
        item for item in request_history[-20:]
        if isinstance(item, dict) and item.get("role") in {"system", "user", "assistant"} and item.get("content")
    ]
    return trimmed_request or project_history


def _surface_identity_instructions(msg: SurfaceMessage) -> str:
    surface_context = msg.metadata.get("surface_context")
    if not isinstance(surface_context, dict):
        return ""

    identity = surface_context.get("identity")
    lines: list[str] = []

    if isinstance(identity, dict):
        lines.append("## Surface identity")
        name = str(identity.get("name") or "").strip()
        username = str(identity.get("username") or "").strip()
        personality = str(identity.get("personality") or "").strip()
        role = str(identity.get("role") or "").strip()
        project_focus = identity.get("project_focus") or []

        if name and username:
            lines.append(f"- Reply as bot `{name}` (`@{username}`)")
        elif name:
            lines.append(f"- Reply as bot `{name}`")

        if role:
            lines.append(f"- Surface role: {role}")

        if personality:
            lines.append(f"- Keep the persona aligned with: {personality}")

        focus_items = [str(item).strip() for item in project_focus if str(item).strip()]
        if focus_items:
            lines.append(f"- Primary project focus: {', '.join(focus_items[:5])}")

    peers = surface_context.get("peers") or []
    peer_labels: list[str] = []
    for peer in peers[:5]:
        if not isinstance(peer, dict):
            continue
        peer_name = str(peer.get("name") or "").strip()
        peer_username = str(peer.get("username") or "").strip()
        if peer_name and peer_username:
            peer_labels.append(f"{peer_name} (@{peer_username})")
        elif peer_name:
            peer_labels.append(peer_name)
    if peer_labels:
        if not lines:
            lines.append("## Surface identity")
        lines.append(
            "- Other bots on this surface: "
            + ", ".join(peer_labels)
            + ". Mention them only when routing context matters."
        )

    adapter_instructions = str(surface_context.get("adapter_instructions") or "").strip()
    if adapter_instructions:
        lines.append("## Surface delivery instructions")
        lines.append(adapter_instructions)

    return "\n".join(lines) if lines else ""


def _chat_system_instructions(msg: SurfaceMessage, *extra_blocks: str) -> str:
    blocks = [_surface_identity_instructions(msg), *extra_blocks]
    return "\n\n".join(block.strip() for block in blocks if block and block.strip())


def _deliberation_system_instructions(classification: ClassificationResult) -> str:
    deliberation = classification.deliberation
    route = classification.route
    if deliberation is None and route is None:
        return ""
    lines = [
        "## Execution contract",
        (
            "Before each tool/no-tool decision, briefly reassess whether you fully understand "
            "the goal, which required actions remain, and whether the completion criteria are "
            "actually satisfied."
        ),
    ]
    if deliberation is not None:
        if deliberation.goal:
            lines.append(f"- Goal: {deliberation.goal}")
        if deliberation.deliverable:
            lines.append(f"- Deliverable: {deliberation.deliverable}")
        if deliberation.constraints:
            lines.append("- Constraints:")
            lines.extend(f"  - {item}" for item in deliberation.constraints)
        if deliberation.required_action_hints:
            lines.append(
                f"- Required actions: {', '.join(deliberation.required_action_hints)}"
            )
        if deliberation.completion_checks:
            lines.append("- Completion criteria:")
            lines.extend(f"  - {item}" for item in deliberation.completion_checks)
        if deliberation.next_step:
            lines.append(f"- Immediate next step: {deliberation.next_step}")
    elif route is not None:
        hint_text = ", ".join(route.action_hints) if route.action_hints else "none"
        lines.append(f"- Route target: {route.target}")
        lines.append(f"- Required actions: {hint_text}")
    return "\n".join(lines)


def _project_prompt_context(context: ResolvedContext) -> str:
    return (
        f"Current project: {context.project.label}. Summary: {context.project.summary or '(none)'}\n"
        f"Current task: {context.task.label}. Linked workflows: {', '.join(context.project.linked_workflow_ids) or '(none)'}"
    )


def _augmented_prompt_context(msg: SurfaceMessage, context: ResolvedContext) -> str:
    """Include resume/handoff hints in the prompt context when available."""
    parts = [_project_prompt_context(context)]

    domain_expertise = str(msg.metadata.get("domain_expertise") or "").strip()
    if domain_expertise:
        parts.append(domain_expertise)

    resume_context = str(msg.metadata.get("resume_context") or "").strip()
    if resume_context:
        parts.append(f"Resume context: {resume_context}")

    handoff = msg.metadata.get("handoff_context")
    if handoff:
        task_snapshot = getattr(handoff, "task_snapshot", None)
        if task_snapshot is None and isinstance(handoff, dict):
            task_snapshot = handoff.get("task_snapshot")

        project_summary = getattr(handoff, "project_summary", "")
        if not project_summary and isinstance(handoff, dict):
            project_summary = str(handoff.get("project_summary") or "")

        handoff_lines: list[str] = ["Cross-surface handoff context:"]
        if task_snapshot is not None:
            task_name = getattr(task_snapshot, "task_name", None)
            task_status = getattr(task_snapshot, "status", None)
            pending_count = getattr(task_snapshot, "pending_count", None)
            if isinstance(task_snapshot, dict):
                task_name = task_name or task_snapshot.get("task_name")
                task_status = task_status or task_snapshot.get("status")
                pending_count = (
                    pending_count
                    if pending_count is not None
                    else task_snapshot.get("pending_count")
                )
            if task_name:
                status_str = f" [{task_status}]" if task_status else ""
                pending_str = (
                    f", {pending_count} pending"
                    if isinstance(pending_count, int)
                    else ""
                )
                handoff_lines.append(f"- Task: {task_name}{status_str}{pending_str}")
        if project_summary:
            handoff_lines.append(f"- Prior project summary: {project_summary}")
        parts.append("\n".join(handoff_lines))

    return "\n\n".join(part for part in parts if part)


def _build_prompt_from_package(
    msg: SurfaceMessage,
    context: ResolvedContext,
    *,
    context_package: Any | None = None,
) -> str:
    """Build enriched prompt context from ContextPackage (31-21 task 9-5).

    Falls back to _augmented_prompt_context when no package is available.

    Section precedence (31-23 task 4-3):
      1. Project context (always included, never truncated)
      2. Task state / resume context
      3. Boundary handoff context
      4. Domain expertise + artifacts
      5. Memory context
      6. Auto-read content
      7. Cross-surface handoff
      8. Unresolved reference warnings
    When the total exceeds _PROMPT_CONTEXT_BUDGET, lowest-precedence
    sections are dropped entirely (not mid-sentence cut).
    """
    if context_package is None:
        context_package = msg.metadata.get("context_package")
    if context_package is None:
        return _augmented_prompt_context(msg, context)

    ranked: list[tuple[int, str]] = []

    proj = _project_prompt_context(context)
    if proj:
        ranked.append((1, proj))

    task_state = getattr(context_package, 'task_state', None) or {}
    if task_state:
        state_parts = []
        if task_state.get("completed_steps"):
            state_parts.append(f"Completed: {', '.join(task_state['completed_steps'][:5])}")
        if task_state.get("pending_steps"):
            state_parts.append(f"Pending: {', '.join(task_state['pending_steps'][:5])}")
        if task_state.get("current_blocker"):
            state_parts.append(f"Blocker: {task_state['current_blocker']}")
        if state_parts:
            ranked.append((2, "Task state: " + "; ".join(state_parts)))

    resume_context = str(msg.metadata.get("resume_context") or "").strip()
    if resume_context:
        ranked.append((2, f"Resume context: {resume_context}"))

    # TODO(31-23): wire bridge in runtime.py to populate this from
    # session.context["last_handoff"] / executor.last_handoff
    boundary_handoff = msg.metadata.get("boundary_handoff_context")
    if boundary_handoff:
        ranked.append((3, boundary_handoff))

    if getattr(context_package, 'domain_expertise', ''):
        ranked.append((4, context_package.domain_expertise))

    artifacts = getattr(context_package, 'recent_artifacts', None) or []
    if artifacts:
        artifact_lines = ["Known artifacts in this project:"]
        for art in artifacts[:5]:
            name = art.get("name") or art.get("path") or "unknown"
            art_type = art.get("type", "file")
            artifact_lines.append(f"- [{art_type}] {name}")
        ranked.append((4, "\n".join(artifact_lines)))

    if getattr(context_package, 'memory_context', ''):
        ranked.append((5, context_package.memory_context))

    auto_read = getattr(context_package, 'auto_read_content', None) or {}
    if auto_read:
        for path, summary in list(auto_read.items())[:3]:
            ranked.append((6, f"[Auto-read: {path}]\n{summary[:500]}"))

    handoff = msg.metadata.get("handoff_context")
    if handoff:
        task_snapshot = getattr(handoff, "task_snapshot", None)
        if task_snapshot is None and isinstance(handoff, dict):
            task_snapshot = handoff.get("task_snapshot")
        project_summary = getattr(handoff, "project_summary", "")
        if not project_summary and isinstance(handoff, dict):
            project_summary = str(handoff.get("project_summary") or "")
        handoff_lines: list[str] = ["Cross-surface handoff context:"]
        if task_snapshot is not None:
            task_name = getattr(task_snapshot, "task_name", None)
            task_status = getattr(task_snapshot, "status", None)
            if isinstance(task_snapshot, dict):
                task_name = task_name or task_snapshot.get("task_name")
                task_status = task_status or task_snapshot.get("status")
            if task_name:
                status_str = f" [{task_status}]" if task_status else ""
                handoff_lines.append(f"- Task: {task_name}{status_str}")
        if project_summary:
            handoff_lines.append(f"- Prior project summary: {project_summary}")
        ranked.append((7, "\n".join(handoff_lines)))

    unresolved = getattr(context_package, 'unresolved_references', None) or []
    if unresolved:
        ranked.append((8, f"Note: could not resolve references to: {', '.join(unresolved[:5])}"))

    ranked.sort(key=lambda x: x[0])

    result_parts: list[str] = []
    total_len = 0
    for _rank, content in ranked:
        section_len = len(content) + 2
        if total_len + section_len > _PROMPT_CONTEXT_BUDGET and result_parts:
            logger.warning(
                "Prompt context budget exceeded (%d > %d), dropping %d lower-priority sections",
                total_len + section_len, _PROMPT_CONTEXT_BUDGET,
                len(ranked) - len(result_parts),
            )
            break
        result_parts.append(content)
        total_len += section_len

    return "\n\n".join(result_parts)


class FileHandler:
    def __init__(self, user_profile: Any = None, chat_manager: Any = None) -> None:
        default_dirs = [Path.home() / "Dropbox", Path.home() / "Documents", Path.home() / "Desktop"]
        profile_dirs = [Path(p).expanduser() for p in getattr(user_profile, "search_dirs", []) or []]
        self.search_dirs = profile_dirs or default_dirs
        self.chat_manager = chat_manager

    async def handle(self, msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) -> HandlerResult:
        selected_path = str(msg.metadata.get("selected_path") or "").strip()
        if selected_path:
            path = Path(selected_path)
            if self._should_review_document(msg.text, path):
                return await self._review_document(msg, context, path)
                
            content = self._auto_read_small_file(path)
            if content is not None:
                return HandlerResult(
                    content=f"File content:\n{content}\n\nUser message: {msg.text}",
                    attachments=[path]
                )
            return HandlerResult(content=f"Found file: {path}", attachments=[path])
            
        candidate_dirs, explicit_file = self._candidate_search_dirs(msg, context)
        if explicit_file is not None:
            if self._should_review_document(msg.text, explicit_file):
                return await self._review_document(msg, context, explicit_file)
                
            content = self._auto_read_small_file(explicit_file)
            if content is not None:
                return HandlerResult(
                    content=f"File content:\n{content}\n\nUser message: {msg.text}",
                    attachments=[explicit_file]
                )
            return HandlerResult(content=f"Found file: {explicit_file}", attachments=[explicit_file])
            
        scoped_search = self._has_explicit_path_reference(msg.text)
        query = classification.param or self._extract_filename_pattern(msg.text)
        if not query:
            query = self._extract_search_query(
                msg.text,
                scoped_search=scoped_search,
            )
        if not query and not scoped_search:
            query = msg.text
        matches: list[str] = []
        filename_pattern = self._extract_filename_pattern(msg.text)
        if filename_pattern and candidate_dirs:
            matches = self._search_pdf_filename_pattern(filename_pattern, candidate_dirs)
        prefix_match = re.search(r"starting with\s+([a-z0-9._-]+)", msg.text.lower())
        if not matches and prefix_match and candidate_dirs:
            prefix = prefix_match.group(1)
            for directory in candidate_dirs:
                matches.extend(
                    str(path)
                    for path in sorted(directory.glob(f"{prefix}*.pdf"))
                    if path.is_file()
                )
        elif not matches:
            matches = search_local_files(query, candidate_dirs or self.search_dirs)
            if not matches and candidate_dirs:
                matches = self._search_matching_directories(query, candidate_dirs)
        if not matches:
            if not query and len(candidate_dirs) == 1:
                target_dir = candidate_dirs[0]
                return HandlerResult(
                    content=f"Found folder: {target_dir}",
                    attachments=[target_dir],
                )
            return HandlerResult(content=f"No files found matching '{query}'.")
        if len(matches) == 1:
            path = Path(matches[0])
            if self._should_review_document(msg.text, path):
                return await self._review_document(msg, context, path)
                
            content = self._auto_read_small_file(path)
            if content is not None:
                return HandlerResult(
                    content=f"File content:\n{content}\n\nUser message: {msg.text}",
                    attachments=[path]
                )
            label = "folder" if path.is_dir() else "file"
            return HandlerResult(content=f"Found {label}: {path}", attachments=[path])
            
        numbered = "\n".join(f"{idx}. {path}" for idx, path in enumerate(matches[:10], 1))
        return HandlerResult(
            clarification=ClarificationRequest(
                question=f"Found {len(matches)} matches for '{query}':\n{numbered}\nWhich file?",
                options=matches[:10],
            )
        )

    def _auto_read_small_file(self, path: Path) -> str | None:
        """Read small non-PDF files (<100KB) as text for auto-context."""
        try:
            if not path.is_file() or path.suffix.lower() == ".pdf":
                return None
            if path.stat().st_size >= 102400:  # 100KB
                return None
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

    _PATH_RE = re.compile(
        r"(?P<path>(?:~|/)[A-Za-z0-9._~/-]+(?:/[A-Za-z0-9._~-]+)+|[A-Za-z0-9._-]+(?:/[A-Za-z0-9._~-]+)+)"
    )

    def _candidate_search_dirs(self, msg: SurfaceMessage, context: ResolvedContext) -> tuple[list[Path], Path | None]:
        # Scan user turns first (highest priority), then assistant turns
        user_texts = [msg.text] + [
            turn.content
            for turn in reversed(context.task.turns[-6:])
            if turn.role == "user"
        ]
        assistant_texts = [
            turn.content
            for turn in reversed(context.task.turns[-6:])
            if turn.role == "assistant"
        ]

        workspace_root = Path(os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd())).resolve()

        # Pass 1: exact path from user turns
        for text in user_texts:
            result = self._try_extract_path(text, workspace_root)
            if result is not None:
                return result

        # Pass 2: exact path from assistant turns (bot mentioned a dir/file earlier)
        for text in assistant_texts:
            result = self._try_extract_path(text, workspace_root)
            if result is not None:
                return result

        # Pass 3: extract paths from project summary (often contains the working dir)
        summary = context.project.summary or ""
        if summary:
            result = self._try_extract_path(summary, workspace_root)
            if result is not None:
                return result

        return self.search_dirs, None

    def _try_extract_path(self, text: str, workspace_root: Path) -> tuple[list[Path], Path | None] | None:
        for match in self._PATH_RE.finditer(text):
            raw = match.group("path").rstrip("\"'()[]{}.,;:!?")
            expanded = Path(raw).expanduser()
            candidate = expanded if expanded.is_absolute() else (workspace_root / expanded).resolve()
            if candidate.is_file():
                return [], candidate
            if candidate.is_dir():
                return [candidate], None
        return None

    @staticmethod
    def _extract_search_query(text: str, *, scoped_search: bool) -> str:
        """Use explicit paths as scope hints, not literal search terms."""
        if not scoped_search:
            return text
        query = re.sub(
            r"(?:(?:~|/)[A-Za-z0-9._~/-]+(?:/[A-Za-z0-9._~-]+)+|[A-Za-z0-9._-]+(?:/[A-Za-z0-9._~-]+)+)",
            " ",
            text,
        )
        query = re.sub(
            r"\b(?:i have|i've got)?\s*(?:notes|files|docs|documents|materials)\s+(?:in|under|inside|within)\b\s*$",
            "",
            query,
            flags=re.IGNORECASE,
        )
        query = re.sub(r"\s+", " ", query).strip(" ,.;:-")
        return query

    @staticmethod
    def _has_explicit_path_reference(text: str) -> bool:
        return bool(re.search(
            r"(?:(?:~|/)[A-Za-z0-9._~/-]+(?:/[A-Za-z0-9._~-]+)+|[A-Za-z0-9._-]+(?:/[A-Za-z0-9._~-]+)+)",
            text,
        ))

    @staticmethod
    def _search_matching_directories(query: str, search_dirs: list[Path], *, limit: int = 10) -> list[str]:
        query_norm = " ".join(re.findall(r"[a-z0-9]+", query.lower()))
        query_tokens = query_norm.split()
        if not query_tokens:
            return []

        candidates: dict[str, tuple[float, str]] = {}

        def _consider(path: Path) -> None:
            if not path.is_dir():
                return
            name_norm = " ".join(re.findall(r"[a-z0-9]+", path.name.lower()))
            if not name_norm:
                return
            coverage = sum(1 for token in query_tokens if token in name_norm)
            if coverage == 0:
                return
            contains_phrase = 1.0 if query_norm and query_norm in name_norm else 0.0
            score = (coverage * 10.0) + (contains_phrase * 5.0)
            key = str(path)
            current = candidates.get(key)
            if current is None or score > current[0]:
                candidates[key] = (score, path.name.lower())

        exactish_pattern = f"*{'*'.join(query_tokens)}*"
        for directory in search_dirs:
            if not directory.exists():
                continue
            try:
                for match in directory.rglob(exactish_pattern):
                    _consider(match)
            except Exception:
                continue

        if not candidates:
            token_patterns = [f"*{token}*" for token in query_tokens[:4]]
            for directory in search_dirs:
                if not directory.exists():
                    continue
                for pattern in token_patterns:
                    try:
                        for match in directory.rglob(pattern):
                            _consider(match)
                    except Exception:
                        continue

        ranked = sorted(
            candidates.items(),
            key=lambda item: (-item[1][0], item[1][1], item[0]),
        )
        return [path for path, _meta in ranked[:limit]]

    @staticmethod
    def _should_review_document(text: str, path: Path) -> bool:
        lower = text.lower()
        return path.suffix.lower() == ".pdf" and any(keyword in lower for keyword in ("review", "summarize", "summary", "read"))

    @staticmethod
    def _extract_filename_pattern(text: str) -> str | None:
        for raw in re.findall(r"([A-Za-z0-9*?._-]+\.pdf)\b", text, flags=re.IGNORECASE):
            candidate = raw.strip("\"'()[]{}.,;:!?")
            if candidate.lower().endswith(".pdf"):
                return Path(candidate).name
        return None

    def _search_pdf_filename_pattern(self, pattern: str, search_dirs: list[Path], *, limit: int = 10) -> list[str]:
        regex = self._compile_filename_pattern(pattern)
        matches: list[str] = []
        seen: set[str] = set()
        for directory in search_dirs:
            if not directory.exists():
                continue
            try:
                iterator = directory.rglob("*.pdf")
            except Exception:
                continue
            for path in iterator:
                if not path.is_file() or not regex.match(path.name):
                    continue
                key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                matches.append(key)
                if len(matches) >= limit:
                    break
            if len(matches) >= limit:
                break
        return sorted(matches)

    @staticmethod
    def _compile_filename_pattern(pattern: str) -> re.Pattern[str]:
        name = Path(pattern).name
        pieces: list[str] = []
        i = 0
        while i < len(name):
            char = name[i]
            if char == "*":
                pieces.append(".*")
                i += 1
                continue
            if char == "?":
                pieces.append(".")
                i += 1
                continue
            if char in {"x", "X"}:
                j = i
                while j < len(name) and name[j] in {"x", "X"}:
                    j += 1
                run = name[i:j]
                if len(run) >= 2:
                    pieces.append(r"[A-Za-z0-9._-]+")
                else:
                    pieces.append(re.escape(char))
                i = j
                continue
            pieces.append(re.escape(char))
            i += 1
        return re.compile("^" + "".join(pieces) + "$", re.IGNORECASE)

    async def _extract_pdf_text(self, path: Path) -> str:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    @staticmethod
    def _extract_user_intent(text: str) -> str:
        """Strip adapter-generated path/file prefixes to recover the user's actual request."""
        clean: list[str] = []
        for line in text.split("\n"):
            stripped = line.strip()
            if (
                stripped.startswith("Please review this PDF:")
                or stripped.startswith("[User sent file:")
                or stripped.startswith("[Attachment:")
            ):
                continue
            if stripped:
                clean.append(stripped)
        return " ".join(clean).strip() or "Please summarize this document."

    async def _review_document(self, msg: SurfaceMessage, context: ResolvedContext, path: Path) -> HandlerResult:
        if self.chat_manager is None:
            return HandlerResult(content=f"Found file: {path}", attachments=[path])
        try:
            text = await self._extract_pdf_text(path)
        except Exception as exc:
            return HandlerResult(content=f"Found file: {path}\n\nI couldn't read the PDF yet: {exc}")
        user_intent = self._extract_user_intent(msg.text)
        excerpt = text[:_INLINE_DOCUMENT_CHAR_LIMIT]
        truncated = len(text) > _INLINE_DOCUMENT_CHAR_LIMIT
        if truncated:
            document_intro = (
                "The following is an extracted excerpt from a PDF document.\n"
                f"The excerpt is truncated to the first {_INLINE_DOCUMENT_CHAR_LIMIT} characters.\n"
            )
            excerpt_note = (
                "If the requested summary depends on content outside the excerpt, say that you only "
                "reviewed the provided excerpt.\n\n"
            )
        else:
            document_intro = "The following is the full extracted text from a PDF document.\n"
            excerpt_note = ""
        review_message = (
            f"{document_intro}"
            f"User request: {user_intent}\n\n"
            f"IMPORTANT: The document text is already provided below — do NOT call pdf_read. "
            f"Respond directly to the user's request based on the text. "
            f"Do NOT reproduce or quote the raw document text verbatim — produce a clear, structured response.\n\n"
            f"{excerpt_note}"
            f"Source file: {path}\n\n"
            f"--- DOCUMENT TEXT ---\n{excerpt}\n--- END DOCUMENT TEXT ---"
        )
        events = self.chat_manager.send_message(
            workflow_id=_workflow_id_from(msg, context),
            message=review_message,
            history=_message_history(msg, context),
            thread_id=str(msg.metadata.get('thread_id') or "") or None,
            client_graph_revision=msg.metadata.get("client_graph_revision"),
            mode=str(msg.metadata.get("mode") or "ask"),
            cancel_event=msg.metadata.get("cancel_event"),
            debug_context=str(msg.metadata.get("debug_context") or ""),
            prompt_context=_build_prompt_from_package(msg, context),
            mentions=msg.metadata.get("mentions") or [],
            surface=msg.surface,
            extra_system_instructions=_chat_system_instructions(
                msg,
                _INLINE_DOCUMENT_SYSTEM_INSTRUCTIONS,
            ),
        )
        return HandlerResult(events=events)


class DirectTaskHandler:
    def __init__(self, chat_manager: Any) -> None:
        self.chat_manager = chat_manager

    async def handle(self, msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) -> HandlerResult:
        if self._references_file_without_context(msg, context):
            return HandlerResult(
                clarification=ClarificationRequest(
                    question="Which file should I review? Please provide a filename or path.",
                )
            )
        if self._should_search_web(msg.text):
            web_result = await self._search_web(msg.text)
            verified = self._verified_web_results(msg.text, web_result)
            if verified:
                return HandlerResult(content=self._format_web_results(verified))
            if not self._requires_live_verification(msg.text):
                return await self._fallback_to_chat(msg, context, classification)
            return HandlerResult(
                content="I couldn't verify live web data for that right now. Please try again in a moment or provide a source link."
            )
        return await self._fallback_to_chat(msg, context, classification)

    async def _fallback_to_chat(
        self,
        msg: SurfaceMessage,
        context: ResolvedContext,
        classification: ClassificationResult,
    ) -> HandlerResult:
        cleaned = msg.text.lower().strip()
        is_complex_direct_task = _looks_like_complex_direct_task(cleaned)
        route = classification.route
        if route is not None:
            if route.mode == RouteMode.AGENT:
                task_mode = "agent"
            elif route.mode == RouteMode.PLAN:
                task_mode = "plan"
            else:
                task_mode = "ask"
        else:
            task_mode = "agent" if is_complex_direct_task else "ask"
        extra_instructions = _chat_system_instructions(
            msg,
            _deliberation_system_instructions(classification),
        )
        if route is not None:
            hint_text = ", ".join(route.action_hints) if route.action_hints else "none"
            extra_instructions = (
                f"{extra_instructions}\n\n"
                f"Routing mode: {route.mode.value}. Target: {route.target}. "
                f"Action hints: {hint_text}."
            ).strip()
        if task_mode == "agent" or is_complex_direct_task:
            extra_instructions = (
                f"{extra_instructions}\n\n"
                "This is a direct task, not a workflow-editing request. "
                "Do not propose or apply graph mutations. Use capability tools to research, "
                "read, and write the requested artifact, then summarize progress/results clearly."
            ).strip()
        events = self.chat_manager.send_message_with_tools(
            workflow_id=_workflow_id_from(msg, context),
            message=msg.text,
            history=_message_history(msg, context),
            thread_id=str(msg.metadata.get("thread_id") or "") or None,
            client_graph_revision=msg.metadata.get("client_graph_revision"),
            mode=task_mode,
            cancel_event=msg.metadata.get("cancel_event"),
            debug_context=str(msg.metadata.get("debug_context") or ""),
            prompt_context=_build_prompt_from_package(msg, context),
            mentions=msg.metadata.get("mentions") or [],
            allow_mutation_tool=False,
            surface=msg.surface,
            audit_metadata={
                "project_id": context.project.project_id,
                "task_id": context.task.task_id,
                "intent": classification.intent.value,
                "reuse_decision": str(msg.metadata.get("reuse_choice") or ""),
            },
            extra_system_instructions=extra_instructions,
            required_action_hints=list(route.action_hints) if route is not None else None,
        )
        return HandlerResult(events=events)

    @staticmethod
    def _should_search_web(text: str) -> bool:
        return _looks_like_direct_web_lookup(text.lower().rstrip(".!?,"))

    async def _search_web(self, query: str) -> dict[str, Any]:
        from dan.tools.web_search import web_search

        try:
            return await web_search(query=query, num_results=3)
        except Exception:
            return {"results": [], "count": 0}

    @staticmethod
    def _verified_web_results(query: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            item
            for item in result.get("results", [])[:3]
            if DirectTaskHandler._result_matches_query(query, item)
            and (
                not DirectTaskHandler._requires_live_verification(query)
                or DirectTaskHandler._result_looks_live(item)
            )
        ]

    @staticmethod
    def _result_matches_query(query: str, item: dict[str, Any]) -> bool:
        combined = " ".join(
            str(item.get(key, "")).lower()
            for key in ("title", "snippet", "url")
        )
        subject_tokens = DirectTaskHandler._query_subject_tokens(query)
        if not subject_tokens:
            return False
        return any(token in combined for token in subject_tokens[:4])

    @staticmethod
    def _requires_live_verification(query: str) -> bool:
        lower = query.lower()
        return any(phrase in lower for phrase in ("stock price", "share price", "current ", "latest ", "right now", "today"))

    @staticmethod
    def _result_looks_live(item: dict[str, Any]) -> bool:
        combined = " ".join(
            str(item.get(key, "")).lower()
            for key in ("title", "snippet", "url")
        )
        price_like = bool(re.search(r"\$\s?\d+(?:\.\d+)?|\b\d+(?:\.\d+)?\b", combined))
        freshness_like = any(marker in combined for marker in ("current", "latest", "live", "today", "2025", "2026"))
        return price_like or freshness_like

    @staticmethod
    def _query_subject_tokens(query: str) -> list[str]:
        stopwords = {
            "what", "whats", "what's", "who", "is", "the", "a", "an", "of", "for", "about", "this", "that",
            "current", "latest", "right", "now", "today", "stock", "share", "price", "look", "up", "find",
            "fact", "facts", "info", "information",
        }
        return [
            token
            for token in re.findall(r"[a-z0-9]+", query.lower())
            if len(token) > 1 and token not in stopwords
        ]

    @staticmethod
    def _format_web_results(results: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for item in results[:3]:
            title = str(item.get("title", "")).strip()
            url = str(item.get("url", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            parts = [part for part in (title, snippet, url) if part]
            if parts:
                lines.append(" - ".join(parts))
        return "\n".join(lines) or "I couldn't find any web results."

    @staticmethod
    def _references_file_without_context(msg: SurfaceMessage, context: ResolvedContext) -> bool:
        if msg.metadata.get("selected_path"):
            return False
        if _has_filesystem_path(msg.text):
            return False
        tokens = set(re.findall(r"[a-z]+", msg.text.lower()))
        if not (tokens & _FILE_REF_ACTIONS and tokens & _FILE_REF_NOUNS):
            return False
        for turn in context.task.turns[-6:]:
            if re.search(r"\.(pdf|docx?|xlsx?|txt|csv)\b", turn.content, re.IGNORECASE):
                return False
        return True


class RunHandler:
    def __init__(self, capability_context: Any, chat_manager: Any = None) -> None:
        self.capability_context = capability_context
        self.chat_manager = chat_manager

    async def handle(self, msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) -> HandlerResult:
        workflow_id = _workflow_id_from(msg, context)
        ctx = replace(self.capability_context, workflow_id=workflow_id)
        lower = msg.text.lower()
        if "cancel" in lower or "stop" in lower:
            selected = msg.metadata.get("selected_option")
            if selected is not None and context.project.linked_run_ids:
                idx = min(int(selected), len(context.project.linked_run_ids) - 1)
                run_id = context.project.linked_run_ids[idx]
            elif (
                len(context.project.linked_run_ids) > 1
                and not self._text_specifies_run(lower, context)
            ):
                options = list(context.project.linked_run_ids)
                numbered = "\n".join(f"{i + 1}. {rid}" for i, rid in enumerate(options))
                return HandlerResult(
                    clarification=ClarificationRequest(
                        question=f"Multiple active runs \u2014 which one should I cancel?\n{numbered}",
                        options=options,
                    )
                )
            else:
                run_id = context.project.linked_run_ids[-1] if context.project.linked_run_ids else "paused"
            result = await handle_cancel_run({"run_id": run_id}, ctx)
            return HandlerResult(content=result.message)
        if "resume" in lower:
            run_id = context.project.linked_run_ids[-1] if context.project.linked_run_ids else ""
            result = await handle_resume_run({"run_id": run_id, "workflow_id": workflow_id}, ctx)
            return HandlerResult(content=result.message, stream_channel_id=result.stream_channel_id)
        result = await handle_start_run({"workflow_id": workflow_id}, ctx)
        if not result.success and self.chat_manager is not None:
            fallback = DirectTaskHandler(self.chat_manager)
            return await fallback._fallback_to_chat(msg, context, classification)
        project_update = {}
        if result.success and result.data and result.data.get("run_id"):
            project_update["linked_run_id"] = result.data["run_id"]
            project_update["linked_workflow_id"] = workflow_id
        return HandlerResult(
            content=result.message,
            project_update=project_update or None,
            stream_channel_id=result.stream_channel_id,
        )

    @staticmethod
    def _text_specifies_run(text: str, context: ResolvedContext) -> bool:
        return any(rid.lower() in text for rid in context.project.linked_run_ids)


class StatusHandler:
    def __init__(self, capability_context: Any, progress_reporter: ProgressReporter | None = None) -> None:
        self.capability_context = capability_context
        self.progress_reporter = progress_reporter

    async def handle(self, msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) -> HandlerResult:
        if self.progress_reporter and (
            context.project.linked_run_ids or context.project.linked_meta_session_ids
        ):
            snapshot = self.progress_reporter.get_project_progress(context.project, context.task)
            return HandlerResult(content=self.progress_reporter.format_for_surface(snapshot, msg.surface))
        workflow_id = _workflow_id_from(msg, context)
        ctx = replace(self.capability_context, workflow_id=workflow_id)
        if context.project.linked_run_ids:
            result = await handle_get_run_status({"run_id": context.project.linked_run_ids[-1]}, ctx)
            return HandlerResult(content=result.message)
        result = await handle_list_active_runs({}, ctx)
        if not result.success:
            result = await handle_get_activity({}, ctx)
        return HandlerResult(content=result.message)


class ExperienceHandler:
    def __init__(self, capability_context: Any, chat_manager: Any = None) -> None:
        self.capability_context = capability_context
        self.chat_manager = chat_manager

    async def handle(self, msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) -> HandlerResult:
        workflow_id = _workflow_id_from(msg, context)
        ctx = replace(self.capability_context, workflow_id=workflow_id)
        result = await handle_search_workflow_history({"query": msg.text, "top_k": 5}, ctx)
        no_results = not bool(getattr(result, "data", None)) or "No similar workflows found." in str(result.message)
        if no_results and self.chat_manager is not None:
            events = self.chat_manager.send_message_with_tools(
                workflow_id=workflow_id,
                message=(
                    "No similar workflows were found in saved workflow history.\n"
                    f"User request: {msg.text}\n\n"
                    "Tell the user there are no matching saved workflows yet, then answer helpfully with the best next step."
                ),
                history=_message_history(msg, context),
                thread_id=str(msg.metadata.get("thread_id") or "") or None,
                client_graph_revision=msg.metadata.get("client_graph_revision"),
                mode="ask",
                cancel_event=msg.metadata.get("cancel_event"),
                debug_context=str(msg.metadata.get("debug_context") or ""),
                prompt_context=_build_prompt_from_package(msg, context),
                mentions=msg.metadata.get("mentions") or [],
                surface=msg.surface,
                audit_metadata={
                    "project_id": context.project.project_id,
                    "task_id": context.task.task_id,
                    "intent": classification.intent.value,
                    "reuse_decision": str(msg.metadata.get("reuse_choice") or ""),
                },
                extra_system_instructions=_chat_system_instructions(
                    msg,
                    _deliberation_system_instructions(classification),
                ),
            )
            return HandlerResult(events=events)
        return HandlerResult(content=result.message)


class PublishHandler:
    def __init__(self, capability_context: Any) -> None:
        self.capability_context = capability_context

    async def handle(self, msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) -> HandlerResult:
        workflow_id = _workflow_id_from(msg, context)
        ctx = replace(self.capability_context, workflow_id=workflow_id)
        lower = msg.text.lower()
        if "export" in lower:
            result = await handle_export_workflow({"graph_id": workflow_id, "format": "markdown"}, ctx)
        elif "share" in lower:
            result = await handle_share_workflow({"graph_id": workflow_id}, ctx)
        else:
            result = await handle_get_publish_status({"graph_id": workflow_id}, ctx)
        return HandlerResult(content=result.message)


class WorkflowQueryHandler:
    def __init__(self, capability_context: Any) -> None:
        self.capability_context = capability_context

    async def handle(self, msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) -> HandlerResult:
        workflow_id = _workflow_id_from(msg, context)
        ctx = replace(self.capability_context, workflow_id=workflow_id)
        result = await handle_list_graphs({}, ctx)
        return HandlerResult(content=result.message)


class ConversationHandler:
    def __init__(self, chat_manager: Any) -> None:
        self.chat_manager = chat_manager

    async def handle(self, msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) -> HandlerResult:
        workflow_id = _workflow_id_from(msg, context)
        request_mode = str(msg.metadata.get("mode") or "")
        mode = request_mode if request_mode in ("ask", "plan", "debug", "agent") else "ask"
        events = self.chat_manager.send_message_with_tools(
            workflow_id=workflow_id,
            message=msg.text,
            history=_message_history(msg, context),
            thread_id=str(msg.metadata.get("thread_id") or "") or None,
            client_graph_revision=msg.metadata.get("client_graph_revision"),
            mode=mode,
            cancel_event=msg.metadata.get("cancel_event"),
            debug_context=str(msg.metadata.get("debug_context") or ""),
            prompt_context=_build_prompt_from_package(msg, context),
            mentions=msg.metadata.get("mentions") or [],
            surface=msg.surface,
            audit_metadata={
                "project_id": context.project.project_id,
                "task_id": context.task.task_id,
                "intent": classification.intent.value,
                "reuse_decision": str(msg.metadata.get("reuse_choice") or ""),
            },
            extra_system_instructions=_chat_system_instructions(
                msg,
                _deliberation_system_instructions(classification),
            ),
        )
        return HandlerResult(events=events)


class WorkflowBuildHandler:
    def __init__(self, chat_manager: Any) -> None:
        self.chat_manager = chat_manager

    async def handle(self, msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) -> HandlerResult:
        selected = msg.metadata.get("selected_option")
        if selected is not None:
            workflow_id = "_scratch" if int(selected) == 1 else _workflow_id_from(msg, context)
        elif context.project.linked_workflow_ids and self._has_creation_language(msg.text):
            return HandlerResult(
                clarification=ClarificationRequest(
                    question=(
                        "Do you want to: 1) Modify the current workflow, "
                        "or 2) Create a new workflow?"
                    ),
                    options=["modify", "create"],
                )
            )
        else:
            workflow_id = _workflow_id_from(msg, context)
        mode = str(msg.metadata.get("mode") or "agent")
        events = self.chat_manager.send_message_with_tools(
            workflow_id=workflow_id,
            message=msg.text,
            history=_message_history(msg, context),
            thread_id=str(msg.metadata.get("thread_id") or "") or None,
            client_graph_revision=msg.metadata.get("client_graph_revision"),
            mode=mode,
            cancel_event=msg.metadata.get("cancel_event"),
            debug_context=str(msg.metadata.get("debug_context") or ""),
            prompt_context=_build_prompt_from_package(msg, context),
            mentions=msg.metadata.get("mentions") or [],
            surface=msg.surface,
            audit_metadata={
                "project_id": context.project.project_id,
                "task_id": context.task.task_id,
                "intent": classification.intent.value,
                "reuse_decision": str(msg.metadata.get("reuse_choice") or ""),
            },
            extra_system_instructions=_chat_system_instructions(
                msg,
                _deliberation_system_instructions(classification),
            ),
        )
        return HandlerResult(
            events=events,
            project_update={"linked_workflow_id": workflow_id},
        )

    @staticmethod
    def _has_creation_language(text: str) -> bool:
        lower = text.lower()
        return any(kw in lower for kw in ("create", "new", "build", "make", "start fresh"))


class MetaGoalHandler:
    """Handles META_GOAL when concierge has no memory_kernel (legacy path).
    Creates a ConciergeGoal, runs via MetaController.run_session for execution.
    """

    def __init__(self, meta_controller: Any, capability_context: Any = None) -> None:
        self.meta_controller = meta_controller
        self.capability_context = capability_context

    async def handle(self, msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) -> HandlerResult:
        if self.meta_controller is None:
            return HandlerResult(content="Meta controller not available.")
        enriched_goal_text = self._enrich_with_experience(msg.text)
        from dan.server.concierge.models import ConciergeGoal
        from dan.meta.controller import MetaControllerConfig

        goal = ConciergeGoal(description=enriched_goal_text.strip(), status="active")
        session = await self.meta_controller.create_session_for_goal(goal)
        asyncio.create_task(self.meta_controller.run_session(session, MetaControllerConfig(max_iterations=goal.max_iterations)))
        await asyncio.sleep(0)
        return HandlerResult(
            content=f"{format_prefix(context.project.label)} Meta session started: {session.session_id}",
            project_update={"linked_meta_session_id": session.session_id},
            task_update={"status": "active"},
        )

    def _enrich_with_experience(self, text: str) -> str:
        experience_store = (
            getattr(self.capability_context, "experience_store", None)
            if self.capability_context else None
        )
        error_memory = (
            getattr(self.capability_context, "error_memory_index", None)
            if self.capability_context else None
        )
        parts: list[str] = [text]
        if experience_store:
            try:
                experiences = experience_store.search_similar(text, top_k=3)
                if experiences:
                    summaries = "\n".join(exp.summary for exp in experiences[:3])
                    parts.append(f"\n\nRelevant past workflows:\n{summaries}")
            except Exception:
                pass
        if error_memory:
            try:
                principles = error_memory.search(text, top_k=3)
                if principles:
                    items = "\n".join(str(p) for p in principles[:3])
                    parts.append(f"\n\nRelevant principles:\n{items}")
            except Exception:
                pass
        return "".join(parts)


class AskHandler:
    def __init__(
        self,
        *,
        user_profile: Any = None,
        capability_context: Any = None,
        progress_reporter: ProgressReporter | None = None,
        chat_manager: Any = None,
    ) -> None:
        self.file_handler = FileHandler(user_profile=user_profile, chat_manager=chat_manager)
        self.status_handler = StatusHandler(capability_context, progress_reporter)
        self.experience_handler = ExperienceHandler(capability_context, chat_manager=chat_manager)
        self.workflow_query_handler = WorkflowQueryHandler(capability_context)
        self.conversation_handler = ConversationHandler(chat_manager)

    async def handle(self, msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) -> HandlerResult:
        route = classification.route
        action_hints = set(route.action_hints if route is not None else [])
        target = route.target if route is not None else "general"
        if "read_file" in action_hints or target == "file":
            return await self.file_handler.handle(msg, context, classification)
        if "status_check" in action_hints or target == "run":
            return await self.status_handler.handle(msg, context, classification)
        if "workflow_query" in action_hints or target == "workflow":
            return await self.workflow_query_handler.handle(msg, context, classification)
        if "experience_lookup" in action_hints or target == "memory":
            return await self.experience_handler.handle(msg, context, classification)
        return await self.conversation_handler.handle(msg, context, classification)


class AgentHandler:
    def __init__(self, *, capability_context: Any = None, chat_manager: Any = None) -> None:
        self.direct_task_handler = DirectTaskHandler(chat_manager)
        self.run_handler = RunHandler(capability_context, chat_manager=chat_manager)
        self.publish_handler = PublishHandler(capability_context)

    async def handle(self, msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) -> HandlerResult:
        route = classification.route
        action_hints = set(route.action_hints if route is not None else [])
        if "run_control" in action_hints:
            return await self.run_handler.handle(msg, context, classification)
        if "publish" in action_hints:
            return await self.publish_handler.handle(msg, context, classification)
        return await self.direct_task_handler.handle(msg, context, classification)


class PlanHandler:
    def __init__(self, *, capability_context: Any = None, chat_manager: Any = None, meta_controller: Any = None) -> None:
        self.workflow_build_handler = WorkflowBuildHandler(chat_manager)
        self.meta_goal_handler = MetaGoalHandler(meta_controller, capability_context=capability_context)

    async def handle(self, msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) -> HandlerResult:
        route = classification.route
        action_hints = set(route.action_hints if route is not None else [])
        if "long_horizon_goal" in action_hints:
            return await self.meta_goal_handler.handle(msg, context, classification)
        return await self.workflow_build_handler.handle(msg, context, classification)
