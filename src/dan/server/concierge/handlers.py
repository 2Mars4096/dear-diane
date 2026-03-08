from __future__ import annotations

import asyncio
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

from .classifier import ClassificationResult, IntentCategory, search_local_files
from .classifier import _looks_like_direct_web_lookup
from .context_resolver import ResolvedContext
from .identity import format_prefix
from .models import SurfaceMessage
from .policy import ClarificationRequest
from .progress import ProgressReporter

_FILE_REF_ACTIONS = frozenset({"review", "read", "summarize", "proofread", "analyze"})
_FILE_REF_NOUNS = frozenset(
    {"paper", "file", "document", "report", "article", "thesis", "manuscript"},
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
        return self._handlers[intent]


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


def _project_prompt_context(context: ResolvedContext) -> str:
    return (
        f"Current project: {context.project.label}. Summary: {context.project.summary or '(none)'}\n"
        f"Current task: {context.task.label}. Linked workflows: {', '.join(context.project.linked_workflow_ids) or '(none)'}"
    )


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
            return HandlerResult(content=f"Found file: {path}", attachments=[path])
        candidate_dirs, explicit_file = self._candidate_search_dirs(msg, context)
        if explicit_file is not None:
            if self._should_review_document(msg.text, explicit_file):
                return await self._review_document(msg, context, explicit_file)
            return HandlerResult(content=f"Found file: {explicit_file}", attachments=[explicit_file])
        query = classification.param or self._extract_filename_pattern(msg.text) or msg.text
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
        if not matches:
            return HandlerResult(content=f"No files found matching '{query}'.")
        if len(matches) == 1:
            path = Path(matches[0])
            if self._should_review_document(msg.text, path):
                return await self._review_document(msg, context, path)
            return HandlerResult(content=f"Found file: {path}", attachments=[path])
        numbered = "\n".join(f"{idx}. {path}" for idx, path in enumerate(matches[:10], 1))
        return HandlerResult(
            clarification=ClarificationRequest(
                question=f"Found {len(matches)} matches for '{query}':\n{numbered}\nWhich file?",
                options=matches[:10],
            )
        )

    def _candidate_search_dirs(self, msg: SurfaceMessage, context: ResolvedContext) -> tuple[list[Path], Path | None]:
        texts = [msg.text] + [
            turn.content
            for turn in reversed(context.task.turns[-6:])
            if turn.role == "user"
        ]
        workspace_root = Path(os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd())).resolve()
        for text in texts:
            for match in re.finditer(
                r"(?P<path>(?:~|/)[A-Za-z0-9._~/-]+(?:/[A-Za-z0-9._~-]+)+|[A-Za-z0-9._-]+(?:/[A-Za-z0-9._~-]+)+)",
                text,
            ):
                raw = match.group("path").rstrip("\"'()[]{}.,;:!?")
                expanded = Path(raw).expanduser()
                candidate = expanded if expanded.is_absolute() else (workspace_root / expanded).resolve()
                if candidate.is_file():
                    return [], candidate
                if candidate.is_dir():
                    return [candidate], None
        return self.search_dirs, None

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

    async def _review_document(self, msg: SurfaceMessage, context: ResolvedContext, path: Path) -> HandlerResult:
        if self.chat_manager is None:
            return HandlerResult(content=f"Found file: {path}", attachments=[path])
        try:
            text = await self._extract_pdf_text(path)
        except Exception as exc:
            return HandlerResult(content=f"Found file: {path}\n\nI couldn't read the PDF yet: {exc}")
        review_message = (
            f"{msg.text}\n\n"
            f"Use this PDF as the source material. Do not ask for filesystem access.\n"
            f"Source file: {path}\n\n"
            f"{text[:12000]}"
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
            prompt_context=_project_prompt_context(context),
            mentions=msg.metadata.get("mentions") or [],
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
                return await self._fallback_to_chat(msg, context)
            return HandlerResult(
                content="I couldn't verify live web data for that right now. Please try again in a moment or provide a source link."
            )
        return await self._fallback_to_chat(msg, context)

    async def _fallback_to_chat(self, msg: SurfaceMessage, context: ResolvedContext) -> HandlerResult:
        events = self.chat_manager.send_message_with_tools(
            workflow_id=_workflow_id_from(msg, context),
            message=msg.text,
            history=_message_history(msg, context),
            thread_id=str(msg.metadata.get("thread_id") or "") or None,
            client_graph_revision=msg.metadata.get("client_graph_revision"),
            mode="conversation",
            cancel_event=msg.metadata.get("cancel_event"),
            debug_context=str(msg.metadata.get("debug_context") or ""),
            prompt_context=_project_prompt_context(context),
            mentions=msg.metadata.get("mentions") or [],
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
        tokens = set(re.findall(r"[a-z]+", msg.text.lower()))
        if not (tokens & _FILE_REF_ACTIONS and tokens & _FILE_REF_NOUNS):
            return False
        for turn in context.task.turns[-6:]:
            if re.search(r"\.(pdf|docx?|xlsx?|txt|csv)\b", turn.content, re.IGNORECASE):
                return False
        return True


class RunHandler:
    def __init__(self, capability_context: Any) -> None:
        self.capability_context = capability_context

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
    def __init__(self, capability_context: Any) -> None:
        self.capability_context = capability_context

    async def handle(self, msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) -> HandlerResult:
        workflow_id = _workflow_id_from(msg, context)
        ctx = replace(self.capability_context, workflow_id=workflow_id)
        result = await handle_search_workflow_history({"query": msg.text, "top_k": 5}, ctx)
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
        mode = request_mode if request_mode in ("ask", "plan", "debug") else "conversation"
        events = self.chat_manager.send_message_with_tools(
            workflow_id=workflow_id,
            message=msg.text,
            history=_message_history(msg, context),
            thread_id=str(msg.metadata.get("thread_id") or "") or None,
            client_graph_revision=msg.metadata.get("client_graph_revision"),
            mode=mode,
            cancel_event=msg.metadata.get("cancel_event"),
            debug_context=str(msg.metadata.get("debug_context") or ""),
            prompt_context=_project_prompt_context(context),
            mentions=msg.metadata.get("mentions") or [],
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
            prompt_context=_project_prompt_context(context),
            mentions=msg.metadata.get("mentions") or [],
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
    def __init__(self, meta_controller: Any, capability_context: Any = None) -> None:
        self.meta_controller = meta_controller
        self.capability_context = capability_context

    async def handle(self, msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) -> HandlerResult:
        if self.meta_controller is None:
            return HandlerResult(content="Meta controller not available.")
        enriched_goal = self._enrich_with_experience(msg.text)
        session = await self.meta_controller.create_session(enriched_goal)
        asyncio.create_task(self.meta_controller.run_session(session))
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
