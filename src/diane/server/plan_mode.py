"""Session-owned plan review, independent of skills and execution permissions."""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from diane.server.chat_store import ChatStore

_LOCK = threading.RLock()


class PlanDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=24000)
    title: str = Field(default="", max_length=200)
    outcome: str = ""
    scope: str = ""
    specifications: str = ""
    steps: str = ""
    assumptions: str = ""
    blockers: str = ""


class PlanQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=2000)
    question: str
    reason: str = ""
    recommended: str
    alternatives: list[str] = Field(default_factory=list, max_length=5)
    selection_mode: Literal["single", "multiple"] = "single"


class PlanProposal(BaseModel):
    document: PlanDocument
    summary: str = Field(default="Plan updated.", max_length=1200)
    question: PlanQuestion | None = None
    questions: list[PlanQuestion] = Field(default_factory=list, max_length=3)


class PlanUpdate(BaseModel):
    version: int = Field(ge=0)
    action: Literal["mode", "edit", "accept_proposal", "dismiss_proposal"]
    mode: Literal["plan", "work"] | None = None
    document: PlanDocument | None = None


def empty_state() -> dict:
    return dict(version=0, revision=0, mode="work", document=None, history=[],
                question=None, questions=[], questions_asked=0, proposal=None, execution=None,
                summary="", last_run_id="", selected_skills=[])


class PlanConflict(ValueError):
    pass


class PlanStore:
    def __init__(self, chats: ChatStore):
        self.chats = chats

    def path(self, workflow: str, thread: str) -> Path:
        if any(not value or value in {".", ".."} or "/" in value or "\\" in value for value in (workflow, thread)):
            raise ValueError("Invalid session identity")
        if self.chats.get_thread(workflow, thread) is None:
            raise KeyError("Session not found")
        return self.chats._chats_dir(workflow) / f"{thread}.plan.json"

    def read(self, workflow: str, thread: str) -> dict:
        with _LOCK:
            path = self.path(workflow, thread)
            return json.loads(path.read_text()) if path.exists() else empty_state()

    def write(self, workflow: str, thread: str, state: dict) -> dict:
        path = self.path(workflow, thread)
        state["version"] += 1
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        os.replace(temporary, path)
        return state

    @staticmethod
    def revise(state: dict, document: dict, summary: str) -> None:
        if state["document"] is not None:
            state["history"] = (state["history"] + [dict(revision=state["revision"],
                document=state["document"], summary=state["summary"])])[-20:]
        state["document"] = document
        state["revision"] += 1
        state["summary"] = summary

    def update(self, workflow: str, thread: str, update: PlanUpdate) -> dict:
        with _LOCK:
            state = self.read(workflow, thread)
            if state["version"] != update.version:
                raise PlanConflict("The plan changed. Reload the saved plan before applying your edit.")
            if update.action == "mode":
                if update.mode is None:
                    raise ValueError("Choose a conversation mode")
                state["mode"] = update.mode
            elif update.action == "edit":
                if update.document is None:
                    raise ValueError("A plan document is required")
                self.revise(state, update.document.model_dump(), "Your edits saved.")
            elif update.action == "accept_proposal":
                if state["proposal"] is None:
                    raise PlanConflict("There is no proposed revision to accept")
                self.revise(state, state["proposal"]["document"], state["proposal"]["summary"])
                state["question"] = state["proposal"]["question"]
                state["questions"] = state["proposal"].get("questions", [])
                state["proposal"] = None
            else:
                state["proposal"] = None
            return self.write(workflow, thread, state)

    def receive(self, workflow: str, thread: str, revision: int, run_id: str, proposal: PlanProposal, selected_skills: list[str] | None = None) -> dict:
        with _LOCK:
            state = self.read(workflow, thread)
            if state["last_run_id"] == run_id:
                return state
            if selected_skills:
                state["selected_skills"] = selected_skills
            payload = proposal.model_dump()
            questions = payload["questions"] or ([payload["question"]] if payload["question"] else [])
            questions = questions[:max(0, 3 - state["questions_asked"])]
            payload["questions"] = questions
            payload["question"] = questions[0] if questions else None
            state["questions_asked"] += len(questions)
            state["last_run_id"] = run_id
            if state["revision"] != revision:
                state["proposal"] = payload
                state["summary"] = "Diane proposed a revision while you edited. Review it before replacing your draft."
            else:
                self.revise(state, payload["document"], payload["summary"])
                state["question"] = payload["question"]
                state["questions"] = payload["questions"]
            return self.write(workflow, thread, state)

    def claim(self, workflow: str, thread: str, revision: int, run_id: str) -> dict:
        with _LOCK:
            state = self.read(workflow, thread)
            if state["execution"] and state["execution"]["revision"] == revision:
                raise PlanConflict("This revision has already been started. Review its run before starting new work.")
            if state["revision"] != revision or not state["document"]:
                raise PlanConflict("The reviewed revision changed. Review the current plan before starting.")
            document = PlanDocument.model_validate(state["document"])
            if document.blockers.strip() or not document.outcome.strip() or not document.steps.strip():
                raise PlanConflict("Resolve blockers and include an outcome and next steps before starting.")
            if state["proposal"]:
                raise PlanConflict("Review or dismiss the proposed revision before starting.")
            state["execution"] = dict(revision=revision, run_id=run_id, document=state["document"])
            state["mode"] = "work"
            self.write(workflow, thread, state)
            return state


def plan_context(request: Any) -> dict:
    value = request.surface_context.get("plan_review")
    return value if isinstance(value, dict) and value.get("action") in {"refine", "start"} else {}


def parse_proposal(text: str) -> PlanProposal:
    match = re.search(r"```(?:diane-plan|json)\s*\n([\s\S]*?)\n```", text)
    return PlanProposal.model_validate_json(match.group(1) if match else text.strip())


def planning_instructions(state: dict) -> str:
    return (
        "You are in Diane Plan mode, a conversation mode, not a skill. Research and refine the plan; "
        "do not implement, edit project files, delegate implementation, or start work. Selected skills "
        "may inform the plan but cannot override Plan mode. Do not invoke question/approval tools: put "
        "up to three concise independent questions together in the response data, within the remaining budget. Draft a useful plan now, even with uncertainty. "
        "Look up available facts yourself. Only ask about a decision that materially affects scope or behavior. "
        f"Questions remaining across this planning session: {max(0, 3-state['questions_asked'])}. "
        "When none remain, use labeled assumptions and blockers instead. No compound questionnaires. "
        "A deferred blocking decision stays in blockers. Optional assumptions do not block execution. "
        "Never represent a recommendation as a user-confirmed decision. Keep the plan concise and specific. "
        "Preserve user edits and revise only what the request requires. Return ONLY one fenced diane-plan JSON "
        "object with document, summary (brief description of changes), and questions (an array, empty unless essential). "
        "document has string fields title, outcome, scope (Includes and Later), specifications (behavior and done when), "
        "steps (numbered concrete deliverables), assumptions, blockers (empty string when none). "
        "Each questions entry has question, reason, recommended, alternatives (1–5 strings), and selection_mode (single or multiple). Use single for mutually exclusive choices, multiple for independent choices that can be combined. The recommended string is one selectable option, not a bundle of answers. Write each option as a short descriptive phrase stating what it includes or its consequence, rather than a bare label. The UI always supplies an Other choice and optional comments; do not add duplicate Other options. "
        "Do not put 'None' or 'No blockers' in blockers: use an empty string.\n"
        "Current reviewed document:\n" + json.dumps(state["document"], ensure_ascii=False)
        + "\nCurrent question (interpret short answers against this):\n" + json.dumps(state.get("questions") or ([state["question"]] if state["question"] else []), ensure_ascii=False)
    )


async def run_api_planner(request: Any, runtime: Any):
    """Diane API planning uses the selected model, with no mutation/tool executor."""
    from diane.server.chat_v2_backend import AgentBackendRunResult, _load_super_dan_cli, _objective_with_surface_context
    from diane.server.chat_v2 import normalize_token_usage
    from diane.native_workers.models import API_PROVIDERS, ReasoningProvider, dan_model_policy, provider_key
    cli = _load_super_dan_cli()
    policy = dict(request.profile_policy)
    if "lead_profile" in policy:
        policy.update(dan_model_policy(policy["lead_profile"]))
    model = cli._resolve_live_model(policy.get("model") or os.environ.get("DAN_CHAT_V2_AGENT_MODEL"))
    provider = cli._build_live_provider(model,
        api_key=provider_key(policy["provider"]) if policy.get("provider") in API_PROVIDERS else None,
        base_url=policy.get("base_url") or os.environ.get("DAN_CHAT_V2_AGENT_BASE_URL"))
    if policy.get("reasoning_effort") and policy.get("provider") in API_PROVIDERS:
        provider = ReasoningProvider(provider, policy["reasoning_effort"], policy["provider"])
    try:
        runtime.raise_if_interrupted("plan.start")
        response = await provider.complete(model=model, max_tokens=6000, messages=[
            {"role": "system", "content": "Plan from the supplied conversation and context. No filesystem tools are available in this API planning turn; label facts needing inspection as unverified, never invent inspection results."},
            *request.history[-12:], {"role": "user", "content": _objective_with_surface_context(request)}])
        runtime.raise_if_interrupted("plan.completed")
        return AgentBackendRunResult(status="completed", backend="super_dan", summary=response.text,
            token_usage=normalize_token_usage(response.usage or {}))
    finally:
        await cli._close_live_provider(provider)
