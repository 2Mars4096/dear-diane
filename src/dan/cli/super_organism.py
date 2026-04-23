"""dan-super-organism — Super DAN organism showcase and live executor."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from dan.cli import load_env, normalize_workspace_root, resolve_config
from dan.cli import live_gateway
from dan.providers import LLMProvider
from dan.server.runtime_config import build_engine_config_from_env
from dan.worker.core.contracts import ExecutionRequest
from dan.worker.core.executor import WorkerCoreExecutor
from dan.worker.core.interfaces import CallbackEventSink
from dan.worker.core.model import CompletionHints, WorkerDefinition
from dan.worker.organism_log import (
    ORGANISM_LOG_SCHEMA_VERSION,
    OrganismLogContext,
    OrganismLogWriter,
    new_trace_id,
)
from dan.worker.organisms.local_runtime import (
    LocalOrganismToolRuntime,
    ToolLoopCompletionProvider,
)
from dan.worker.organisms.super_organism import (
    DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP,
    DEFAULT_SUPER_ORGANISM_CELL_COUNT,
    DEFAULT_SUPER_ORGANISM_ID,
    DEFAULT_SUPER_ORGANISM_TARGET,
    SuperOrganismReport,
    run_super_organism_demo,
)


_LIVE_WEBSITE_TOOL_IDS = [
    "list_directory",
    "file_read",
    "file_write",
    "file_edit",
]
_LIVE_GENERIC_TOOL_IDS = [
    "list_directory",
    "file_read",
    "file_write",
    "file_edit",
    "shell_command",
    "git_status",
    "git_diff",
    "git_log",
]
_LIVE_WEBSITE_FILES = ("index.html", "styles.css", "app.js", "README.md")
_LIVE_FILE_WRITE_SAFE_WORD_LIMIT = 1200
_LIVE_FILE_WRITE_SAFE_LINE_LIMIT = 200
_LIVE_EXISTING_WEBSITE_MIN_CHANGED_FILES = 2
_WEBSITE_TEMPLATE_PHRASES = (
    "execution contract",
    "objective contract",
    "capability and authority contract",
    "native execution lane",
    "acceptance synthesis",
    "super dan turns one objective into coordinated execution",
)


def _live_pacing_contract() -> str:
    return (
        "Work at organism pace: no hurry, no giant monolithic rewrites, no speculative scratch files. "
        f"Treat single `file_write` payloads above roughly {_LIVE_FILE_WRITE_SAFE_WORD_LIMIT} words or "
        f"{_LIVE_FILE_WRITE_SAFE_LINE_LIMIT} lines as risky and split them into smaller coherent chunks. "
        "Prefer `file_edit` for incremental updates to existing files, and let each round land one valid slice "
        "before attempting the next."
    )


class SuperRunEventLogger:
    """Persist timestamped Super DAN live events to one JSONL file."""

    def __init__(
        self,
        *,
        path: Path,
        session_id: str = "",
        turn_id: str = "",
        task_id: str = "",
        organism_id: str = "",
        organ_id: str = "",
        trace_id: str = "",
    ) -> None:
        self._writer = OrganismLogWriter(
            path=path,
            context=OrganismLogContext(
                product="dan_super",
                stream_kind="bounded_run",
                session_id=session_id,
                turn_id=turn_id,
                task_id=task_id,
                trace_id=trace_id,
                organism_id=organism_id,
                organ_id=organ_id,
            ),
        )
        self.path = self._writer.path

    def emit(self, event: dict[str, Any]) -> None:
        self._writer.emit(event)

    def emit_trace_rows(self, trace_rows: Sequence[dict[str, Any]]) -> None:
        self._writer.emit_trace_rows(trace_rows)

    def update_context(self, **updates: Any) -> None:
        self._writer.update_context(**updates)

    def close(self) -> None:
        self._writer.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dan-super-organism",
        description=(
            "Run the Super DAN organism showcase. "
            "This accepts an objective, proves the coordination board, cell roles, "
            "delivery/claim contracts, and reallocation loop before any expensive "
            "live execution. Use --live for native tool-backed execution."
        ),
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help=(
            "Operator objective. Omit to run the default universal-agent objective "
            f"({DEFAULT_SUPER_ORGANISM_TARGET!r})."
        ),
    )
    parser.add_argument(
        "--organism-id",
        default=DEFAULT_SUPER_ORGANISM_ID,
        help="Organism identifier stamped into the report.",
    )
    parser.add_argument(
        "--cell-count",
        type=int,
        default=DEFAULT_SUPER_ORGANISM_CELL_COUNT,
        help="Logical cell count. Defaults to the cheaper 20-cell operator mode; use 100 for the showcase.",
    )
    parser.add_argument(
        "--active-cell-cap",
        type=int,
        default=DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP,
        help="Maximum logical cells active in one scheduler wave.",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Only print the organism contract; do not materialize deterministic artifacts.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Run Super DAN's native live execution lane with local tools. "
            "Supports website-like and general coding/build objectives and requires LLM configuration."
        ),
    )
    parser.add_argument(
        "--model",
        help="Model for --live. Defaults to DAN_MODEL or DAN_LLM_MODEL.",
    )
    parser.add_argument(
        "--api-key",
        help="Optional API key override for --live.",
    )
    parser.add_argument(
        "--base-url",
        help="Optional OpenAI-compatible base URL override for --live.",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=8,
        help="Maximum model/tool rounds for --live. Defaults to 8.",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=24,
        help="Maximum local tool calls for --live. Defaults to 24.",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root for Super DAN artifacts. Defaults to the current directory.",
    )
    parser.add_argument(
        "--artifact-dir",
        default="website",
        help="Directory, relative to --workspace unless absolute, for generated website artifacts.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output. Live mode includes both the report and live build result.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the full organism text report instead of the compact CLI summary.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the JSON report.",
    )
    return parser


def _print_text_report(report: SuperOrganismReport) -> None:
    lines = _text_report_lines(report)
    print("\n".join(lines), end="\n")


def _print_compact_report(
    report: SuperOrganismReport,
    *,
    artifact_paths: Sequence[Path] | None = None,
) -> None:
    paths = list(artifact_paths or [])
    if paths:
        lines = [
            "Status: completed",
            "Build: completed",
            f"Website: {paths[0]}",
            "Files:",
            *[f"- {path}" for path in paths],
            "",
            f"Organism: {_display_text(report.organism_id)} ({report.cell_count} cells, active cap {report.active_cell_cap})",
            f"Target: {_display_text(report.target)}",
            "Full trace: rerun with --verbose or --json.",
        ]
        print("\n".join(lines), end="\n\n")
        return

    lines = [
        f"Status: {_display_text(report.status)}",
        f"Organism: {_display_text(report.organism_id)} ({report.cell_count} cells, active cap {report.active_cell_cap})",
        f"Target: {_display_text(report.target)}",
        f"Verdict: {_display_text(report.final_verdict)}",
        f"{_display_text(report.score_label)}: {report.credibility_score:.2f}",
        f"Execution Family: {_display_text(report.execution_family)}",
        "Organs: " + ", ".join(f"{key}={value}" for key, value in sorted(report.organ_counts.items())),
        "Coordination: "
        + " -> ".join(
            [
                "objective",
                "context",
                "decompose",
                "memory",
                "immune",
                "reallocate",
                "probe",
                "synthesize",
            ]
        ),
        "",
        "Full trace: rerun with --verbose or --json.",
    ]
    print("\n".join(lines), end="\n")


def _print_live_report(
    report: SuperOrganismReport,
    live_result: dict[str, Any],
    *,
    verbose: bool = False,
) -> None:
    files = [str(path) for path in live_result.get("files") or []]
    missing = [str(path) for path in live_result.get("missing_files") or []]
    status = _display_text(live_result.get("status") or "failed")
    summary_label = _display_text(live_result.get("summary_label") or "Live Run")
    lines = [
        "Status: completed" if status == "completed" else "Status: failed",
        f"{summary_label}: {status}",
    ]
    website = str(live_result.get("website") or "").strip()
    if website:
        lines.append(f"Website: {website}")
    if files and website:
        lines.append("Files:")
        lines.extend(f"- {path}" for path in files)
    elif files:
        lines.append("Mutated Paths:")
        lines.extend(f"- {path}" for path in files)
    if missing:
        lines.append("Missing Files:")
        lines.extend(f"- {path}" for path in missing)
    error = str(live_result.get("error") or "").strip()
    if error:
        lines.append(f"Error: {_display_text(error)}")
    validation = dict(live_result.get("validation") or {})
    if validation:
        verdict = "passed" if validation.get("passed") else "failed"
        score = _coerce_float(validation.get("overall_score"))
        lines.append(f"Validation: {verdict} ({score:.2f})")
    event_log_path = str(live_result.get("event_log_path") or "").strip()
    if event_log_path:
        lines.append(f"Event Log: {event_log_path}")
    lines.extend(
        [
            f"Model: {_display_text(live_result.get('model') or '')}",
            f"Tool Calls: {int(live_result.get('tool_calls') or 0)}",
            f"Token Usage: {_format_token_usage(live_result.get('token_usage'))}",
            "",
            f"Organism: {_display_text(report.organism_id)} ({report.cell_count} cells, active cap {report.active_cell_cap})",
            f"Target: {_display_text(report.target)}",
            "Full trace shown below." if verbose else "Full trace: rerun with --verbose or --json.",
        ]
    )
    print("\n".join(lines), end="\n")
    if verbose:
        print()
        _print_text_report(report)


def _text_report_lines(report: SuperOrganismReport) -> list[str]:
    lines = [
        f"Status: {_display_text(report.status)}",
        f"Mode: {_display_text(report.mode)}",
        f"Organism Contract: {_display_text(report.scenario.value)}",
        f"Organism: {_display_text(report.organism_id)}",
        f"Target: {_display_text(report.target)}",
        f"Cells: {report.cell_count} logical | Active cap: {report.active_cell_cap}",
        f"Max Active Observed: {report.max_active_observed}",
        f"Verdict: {_display_text(report.final_verdict)}",
        f"{_display_text(report.score_label)}: {report.credibility_score:.2f}",
        f"Execution Family: {_display_text(report.execution_family)}",
        f"Stages: {_display_text(' -> '.join(report.stage_sequence))}",
        "Organs: " + ", ".join(f"{key}={value}" for key, value in sorted(report.organ_counts.items())),
        "Signals: " + ", ".join(f"{key}={value}" for key, value in sorted(report.signal_counts.items())),
    ]
    if report.claim_graph:
        lines.extend(["", "Claim Graph:"])
        for claim in report.claim_graph:
            lines.append(
                f"- {claim.claim_id} [{claim.status.value}, {claim.confidence:.2f}] "
                f"{_display_text(claim.text)}"
            )
    if report.delivery_plan:
        lines.extend(["", "Delivery Plan:"])
        for node in report.delivery_plan:
            lines.append(
                f"- {node.node_id} | {node.status} | {node.assigned_cell_count} cells | "
                f"{_display_text(node.title)}"
            )
    if report.shared_board is not None:
        lines.extend(["", "Board:"])
        lines.append(
            "- completed="
            + str(len(report.shared_board.completed_ticket_ids))
            + ", waiting="
            + str(len(report.shared_board.waiting_ticket_ids))
            + ", blocked="
            + str(len(report.shared_board.blocked_ticket_ids))
            + ", reserve="
            + str(len(report.shared_board.reserve_cell_ids))
        )
        lines.append(
            "- packets: published="
            + str(len(report.shared_board.published_packet_ids))
            + ", pending="
            + str(len(report.shared_board.pending_packet_ids))
        )
    if report.coordination_tickets:
        lines.extend(["", "Tickets:"])
        for ticket in report.coordination_tickets:
            lines.append(
                f"- {ticket.ticket_id} | {ticket.status} | owner {ticket.owner_cell_id} | "
                f"{_display_text(ticket.title)}"
            )
    if report.handoff_packets:
        lines.extend(["", "Handoffs:"])
        for packet in report.handoff_packets:
            lines.append(
                f"- {packet.packet_id} | {packet.status} | {packet.from_ticket_id}->{packet.to_ticket_id} | "
                f"{packet.packet_type}"
            )
    lines.extend(["", "Reallocations:"])
    for decision in report.reallocation_decisions:
        lines.append(
            f"- {decision.decision_id}: {decision.cell_count} "
            f"{decision.from_organ.value}->{decision.to_organ.value}; {_display_text(decision.reason)}"
        )
    if report.final_audit is not None:
        lines.extend(
            [
                "",
                "Final Audit:",
                f"- {report.final_audit.status} | satisfied={str(report.final_audit.satisfied).lower()} | blockers: "
                f"{_display_text(', '.join(report.final_audit.blocker_ticket_ids) or 'none')}",
                _display_text(report.final_audit.summary),
            ]
        )
    lines.extend(["", "Final Memo:", _display_text(report.final_memo)])
    lines.extend(["", f"Caveat: {_display_text(report.caveat)}"])
    return lines


def _display_text(value: object) -> str:
    return " ".join(str(value).split())


def _is_website_objective(report: SuperOrganismReport) -> bool:
    text = str(report.target or "").lower()
    return report.execution_family in {"code", "code_plus_research"} and any(
        cue in text for cue in ("website", "landing page", "homepage", "product page")
    )


def _supports_live_execution(report: SuperOrganismReport) -> bool:
    if _is_website_objective(report):
        return True
    text = str(report.target or "").lower()
    mutation_cues = (
        "build",
        "implement",
        "code",
        "app",
        "frontend",
        "backend",
        "fix",
        "patch",
        "refactor",
        "create",
        "write",
        "edit",
        "modify",
        "update",
        "generate",
        "scaffold",
        "feature",
    )
    return report.execution_family in {"code", "code_plus_research"} or any(
        cue in text for cue in mutation_cues
    )


def _should_materialize_website(report: SuperOrganismReport, args: argparse.Namespace) -> bool:
    if (
        bool(getattr(args, "plan_only", False))
        or bool(getattr(args, "json", False))
        or bool(getattr(args, "live", False))
    ):
        return False
    return _is_website_objective(report)


def _artifact_root(args: argparse.Namespace) -> Path:
    workspace = Path(str(args.workspace)).expanduser()
    artifact_dir = Path(str(args.artifact_dir)).expanduser()
    if artifact_dir.is_absolute():
        return artifact_dir
    return workspace / artifact_dir


def _materialize_website_artifact(report: SuperOrganismReport, args: argparse.Namespace) -> list[Path]:
    root = _artifact_root(args)
    root.mkdir(parents=True, exist_ok=True)
    files = {
        root / "index.html": _render_website_html(report),
        root / "styles.css": _render_website_css(),
        root / "app.js": _render_website_js(report),
        root / "README.md": _render_website_readme(report),
    }
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    return list(files)


def _resolve_live_model(requested_model: str | None) -> str:
    text = str(requested_model or "").strip()
    if text:
        return text
    config = resolve_config()
    env_model = str(config.get("model") or "").strip()
    if env_model:
        return env_model
    engine_config = build_engine_config_from_env()
    fallback = str(engine_config.llm_default_model or "").strip()
    if fallback and fallback != "stub-model":
        return fallback
    raise ValueError(
        "Super DAN live mode requires --model or a configured DAN_MODEL/DAN_LLM_MODEL"
    )


def _build_live_provider(
    model: str,
    *,
    api_key: str | None,
    base_url: str | None,
) -> LLMProvider:
    return live_gateway.build_gateway_backed_live_provider(
        model,
        api_key=api_key,
        base_url=base_url,
    )


def _live_artifact_layout(args: argparse.Namespace) -> tuple[Path, Path, list[str], list[Path]]:
    artifact_dir = Path(str(args.artifact_dir)).expanduser()
    if artifact_dir.is_absolute():
        workspace_root = artifact_dir.resolve(strict=False)
        artifact_root = workspace_root
        relative_files = list(_LIVE_WEBSITE_FILES)
    else:
        workspace_root = normalize_workspace_root(str(args.workspace))
        artifact_root = (workspace_root / artifact_dir).resolve(strict=False)
        relative_files = [(artifact_dir / filename).as_posix() for filename in _LIVE_WEBSITE_FILES]
    required_paths = [(workspace_root / relative_path).resolve(strict=False) for relative_path in relative_files]
    return workspace_root, artifact_root, relative_files, required_paths


def _live_expected_return_shape() -> str:
    return json.dumps(
        {
            "candidate_id": "super-dan-live-website-001",
            "change_summary": ["short summary of concrete files written"],
            "target_files": ["website/index.html", "website/styles.css"],
            "test_plan": ["open the generated index.html in a browser"],
            "risks": ["remaining limitations or assumptions"],
            "files_created": ["website/index.html", "website/styles.css"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _live_validation_return_shape() -> str:
    return json.dumps(
        {
            "passed": True,
            "overall_score": 0.9,
            "dimension_scores": {
                "objective_alignment": 0.9,
                "artifact_specificity": 0.9,
                "execution_quality": 0.9,
            },
            "repair_brief": "",
            "missing_requirements": [],
            "comparison_note": "The result materially satisfies the operator objective and is not just a generic demo shell.",
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _build_live_website_worker(model: str) -> WorkerDefinition:
    return WorkerDefinition(
        id="super-dan.live.website-builder",
        role="coding_worker",
        model=model,
        tool_ids=list(_LIVE_WEBSITE_TOOL_IDS),
        instruction=(
            "You are the native live execution lane inside Super DAN. "
            "You are not DAN Code and you must not call or mention DAN Code or DAN Research as an internal handoff. "
            "Turn the operator objective into a concrete static website by actually writing files with "
            "`file_write` or `file_edit` before your final answer. Do not stop after a plan. "
            "Use the required file paths exactly unless the input payload says otherwise. "
            f"{_live_pacing_contract()}"
        ),
        llm_hints=CompletionHints(
            system_prompt=(
                "Super DAN live build contract:\n"
                "- Build a polished static product website with distinctive layout, motion, and concise copy.\n"
                "- Required files: index.html, styles.css, app.js, README.md at the requested artifact paths.\n"
                "- Treat the supplied coordination tickets as the working backlog and satisfy the final audit gate.\n"
                f"- {_live_pacing_contract()}\n"
                "- If the required files already exist, improve them incrementally instead of replacing everything at once.\n"
                "- If this is an existing website redesign, update a coordinated set of files: HTML structure plus CSS visual language and/or JS motion. Do not finalize after changing only one required file unless the operator explicitly asked for a one-file tweak.\n"
                "- After one failed or truncated large write, immediately switch to a smaller section-level strategy.\n"
                "- Avoid rereading the same file unless the next edit truly needs exact line grounding.\n"
                "- Do not create scratch files, marker files, or throwaway artifacts outside the required website file set.\n"
                "- Use local file tools for every required file before finalizing.\n"
                "- Keep dependencies zero; no package install, no external CDN requirement.\n"
                "- The result must be inspectable by opening index.html directly."
            ),
            temperature=0.35,
            max_tokens=2800,
        ),
        metadata={"worker_id": "super-dan.live.website-builder"},
    )


def _build_live_website_validator(model: str) -> WorkerDefinition:
    return WorkerDefinition(
        id="super-dan.live.website.validator",
        role="validator_website",
        model=model,
        tool_ids=list(_LIVE_WEBSITE_TOOL_IDS),
        instruction=(
            "You are the read-only validator lane inside Super DAN. "
            "Inspect the materialized website with read-only tools and decide whether it actually satisfies the operator objective. "
            "Do not write files. Fail if the result is still a generic Super DAN execution-contract demo or if it mainly echoes the raw operator prompt."
        ),
        llm_hints=CompletionHints(
            system_prompt=(
                "Role: validator_website\n"
                "Super DAN live website validator contract:\n"
                "- Read the generated files directly before deciding.\n"
                "- Judge whether the execution and acceptance tickets can actually be closed.\n"
                "- Fail if the artifact is mostly a generic contract/demo template instead of a real product website.\n"
                "- Fail if the artifact mainly repeats the raw operator prompt as hero copy.\n"
                "- Prefer concrete missing requirements and repair guidance over vague critique.\n"
                "- Return only the required validation JSON."
            ),
            temperature=0.0,
            max_tokens=1600,
        ),
        metadata={
            "worker_id": "super-dan.live.website.validator",
            "organism_stage": "validation",
        },
    )


def _build_live_generic_worker(model: str) -> WorkerDefinition:
    return WorkerDefinition(
        id="super-dan.live.coding-builder",
        role="coding_worker",
        model=model,
        tool_ids=list(_LIVE_GENERIC_TOOL_IDS),
        instruction=(
            "You are the native live execution lane inside Super DAN. "
            "You are not DAN Code and you must not call or mention DAN Code or DAN Research as an internal handoff. "
            "Inspect the workspace, make the requested implementation directly, and actually mutate files before your final answer. "
            "Use shell_command only for focused verification or repo inspection, not for sprawling exploration. "
            "Do not stop at a plan. "
            f"{_live_pacing_contract()}"
        ),
        llm_hints=CompletionHints(
            system_prompt=(
                "Super DAN live coding contract:\n"
                "- Execute the operator objective in the current workspace.\n"
                "- Treat the supplied coordination tickets as the working backlog and satisfy the final audit gate.\n"
                f"- {_live_pacing_contract()}\n"
                "- Inspect first, then make a bounded coherent implementation.\n"
                "- Prefer `file_edit` over whole-file `file_write` when the target file already exists.\n"
                "- After one failed or truncated large write, immediately switch to a smaller patch strategy.\n"
                "- Avoid rereading the same files unless the next edit truly needs exact grounding.\n"
                "- Actually create or edit workspace files with file_write or file_edit before finalizing.\n"
                "- Prefer the smallest correct change that clearly advances the objective.\n"
                "- Use shell_command only when it materially verifies or inspects the workspace.\n"
                "- Return a concise JSON-like completion summary at the end."
            ),
            temperature=0.30,
            max_tokens=2800,
        ),
        metadata={"worker_id": "super-dan.live.coding-builder"},
    )


def _build_live_generic_validator(model: str) -> WorkerDefinition:
    return WorkerDefinition(
        id="super-dan.live.coding.validator",
        role="validator_coding",
        model=model,
        tool_ids=list(_LIVE_GENERIC_TOOL_IDS),
        instruction=(
            "You are the read-only validator lane inside Super DAN. "
            "Inspect the changed workspace files and decide whether the live implementation materially advances the operator objective. "
            "Do not write files."
        ),
        llm_hints=CompletionHints(
            system_prompt=(
                "Role: validator_coding\n"
                "Super DAN live coding validator contract:\n"
                "- Inspect the changed files and any relevant git/read-only evidence before deciding.\n"
                "- Judge whether the execution, result handoff, and acceptance tickets can actually be closed.\n"
                "- Fail if the run made only cosmetic, placeholder-style, or otherwise non-responsive changes.\n"
                "- Prefer concrete missing requirements and repair guidance over vague critique.\n"
                "- Return only the required validation JSON."
            ),
            temperature=0.0,
            max_tokens=1600,
        ),
        metadata={
            "worker_id": "super-dan.live.coding.validator",
            "organism_stage": "validation",
        },
    )


def _live_website_task(
    report: SuperOrganismReport,
    *,
    artifact_root: Path,
    relative_files: Sequence[str],
) -> str:
    files = ", ".join(relative_files)
    return (
        "Build the requested product website now. "
        f"Operator objective: {report.target}. "
        f"Artifact root: {artifact_root}. "
        f"Required relative files: {files}. "
        "Honor the supplied ticket ownership and handoff packets instead of freeforming a generic demo shell. "
        f"{_live_pacing_contract()} "
        "If the website files already exist, improve them incrementally instead of rewriting the whole site in one response. "
        "For an existing website redesign, update a coordinated set of required files instead of changing only one page shell. "
        "Do not create extra scratch files outside the required artifact set. "
        f"The website should make the {report.cell_count}-cell Super DAN organism feel credible: "
        "show coordinated cells, organs, synchronization, live execution, and an organized payoff. "
        "Actually create the files, then return the requested compact JSON-like completion summary."
    )


def _live_website_validation_task(
    report: SuperOrganismReport,
    *,
    artifact_root: Path,
    relative_files: Sequence[str],
) -> str:
    files = ", ".join(relative_files)
    return (
        "Validate the materialized website now in read-only mode. "
        f"Operator objective: {report.target}. "
        f"Artifact root: {artifact_root}. "
        f"Required relative files: {files}. "
        "Inspect the generated files and decide whether the result is a real product website aligned with the objective, "
        "not just a generic Super DAN execution-contract demo shell."
    )


def _live_generic_task(
    report: SuperOrganismReport,
    *,
    workspace_root: Path,
) -> str:
    return (
        "Execute the operator objective in the current workspace now. "
        f"Operator objective: {report.target}. "
        f"Workspace root: {workspace_root}. "
        "Honor the supplied ticket ownership and handoff packets instead of freeforming a generic build summary. "
        f"{_live_pacing_contract()} "
        "Inspect the existing project as needed, make a bounded implementation that materially advances the objective, "
        "and run focused verification if useful. Actually mutate workspace files before finalizing, then return the "
        "requested compact JSON-like completion summary."
    )


def _live_generic_validation_task(
    report: SuperOrganismReport,
    *,
    workspace_root: Path,
) -> str:
    return (
        "Validate the live implementation now in read-only mode. "
        f"Operator objective: {report.target}. "
        f"Workspace root: {workspace_root}. "
        "Inspect the mutated files and relevant read-only git evidence, then decide whether the result materially advances the objective."
    )


def _super_run_root(workspace_root: Path) -> Path:
    return workspace_root / ".dan-super" / "runs"


def _next_super_run_number(run_root: Path) -> int:
    highest = 0
    if run_root.exists():
        for child in run_root.iterdir():
            if not child.is_dir():
                continue
            name = child.name
            if not name.startswith("turn-"):
                continue
            try:
                highest = max(highest, int(name.split("-", 1)[1]))
            except (TypeError, ValueError):
                continue
    return highest + 1


def _build_super_run_workdir(workspace_root: Path) -> tuple[Path, int]:
    run_root = _super_run_root(workspace_root)
    run_root.mkdir(parents=True, exist_ok=True)
    turn_number = _next_super_run_number(run_root)
    workdir = run_root / f"turn-{turn_number:02d}"
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir, turn_number


def _super_session_id(workspace_root: Path) -> str:
    digest = hashlib.sha1(
        str(workspace_root.resolve(strict=False)).encode("utf-8")
    ).hexdigest()[:12]
    return f"super-dan:{digest}"


def _log_live_event(
    logger: SuperRunEventLogger | None,
    event: str,
    **payload: Any,
) -> None:
    if logger is None:
        return
    logger.emit({"event": event, **payload})


async def _execute_live_request(
    *,
    worker: WorkerDefinition,
    request: ExecutionRequest,
    tool_ids: Sequence[str],
    workspace_root: Path,
    args: argparse.Namespace,
    model: str,
    provider: LLMProvider,
    event_logger: SuperRunEventLogger | None = None,
) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []

    def record_event(event: dict[str, Any]) -> None:
        payload = dict(event)
        events.append(payload)
        if event_logger is None:
            return
        trace_row = payload.get("trace_row")
        if (
            str(payload.get("event") or "").strip() == "trace.row"
            and isinstance(trace_row, dict)
        ):
            event_logger.emit_trace_rows([trace_row])
            return
        event_logger.emit(payload)

    tool_runtime = LocalOrganismToolRuntime(
        tool_ids=list(tool_ids),
        workspace_root=workspace_root,
        event_callback=record_event,
    )
    completion_provider = ToolLoopCompletionProvider(
        provider=provider,
        tool_runtime=tool_runtime,
        default_model=model,
        max_rounds=int(args.max_tool_rounds),
        max_tool_calls=int(args.max_tool_calls),
        event_callback=record_event,
    )
    executor = WorkerCoreExecutor(
        completion_provider=completion_provider,
        event_sink=CallbackEventSink(record_event),
    )
    result = await executor.execute(worker, request)
    raw_response = result.metadata.get("raw_response")
    executed_tools = (
        list(raw_response.get("executed_tools") or [])
        if isinstance(raw_response, dict)
        else []
    )
    return result, executed_tools, events


def _file_digest(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except Exception:
        return "__unreadable__"
    return hashlib.sha256(data).hexdigest()


def _snapshot_file_state(paths: Sequence[Path]) -> dict[str, str | None]:
    return {
        str(path.resolve(strict=False)): _file_digest(path)
        for path in paths
    }


def _changed_paths_from_snapshot(
    snapshot: dict[str, str | None],
    paths: Sequence[Path],
) -> list[str]:
    changed: list[str] = []
    seen: set[str] = set()
    for path in paths:
        rendered = str(path.resolve(strict=False))
        if rendered in seen:
            continue
        seen.add(rendered)
        if snapshot.get(rendered) != _file_digest(path):
            changed.append(rendered)
    return changed


def _coerce_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _normalize_token_usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, value in raw.items():
        try:
            normalized[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    prompt = int(raw.get("prompt_tokens", raw.get("prompt", 0)) or 0)
    completion = int(raw.get("completion_tokens", raw.get("completion", 0)) or 0)
    total = int(raw.get("total_tokens", prompt + completion) or (prompt + completion))
    normalized["prompt_tokens"] = prompt
    normalized["completion_tokens"] = completion
    normalized["total_tokens"] = total
    return normalized


def _merge_token_usage(*usage_maps: Any) -> dict[str, int]:
    merged: dict[str, int] = {}
    for usage_map in usage_maps:
        for key, value in _normalize_token_usage(usage_map).items():
            merged[key] = int(merged.get(key, 0) or 0) + int(value)
    return merged


def _format_token_usage(raw: Any) -> str:
    usage = _normalize_token_usage(raw)
    if not usage:
        return "unavailable"
    parts = [
        f"prompt={usage.get('prompt_tokens', 0)}",
        f"completion={usage.get('completion_tokens', 0)}",
        f"total={usage.get('total_tokens', 0)}",
    ]
    if usage.get("cached_input_tokens"):
        parts.append(f"cached_input={usage['cached_input_tokens']}")
    if usage.get("cache_write_tokens"):
        parts.append(f"cache_write={usage['cache_write_tokens']}")
    return ", ".join(parts)


def _extract_completion_usage_from_raw(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    usage_totals = raw.get("usage_totals")
    if usage_totals is not None:
        return _normalize_token_usage(usage_totals)
    provider_result = raw.get("provider_result")
    if isinstance(provider_result, dict):
        return _normalize_token_usage(provider_result.get("usage"))
    return {}


def _extract_execution_usage(result: Any) -> dict[str, int]:
    metadata = dict(getattr(result, "metadata", {}) or {})
    return _merge_token_usage(
        _extract_completion_usage_from_raw(metadata.get("raw_response")),
        _extract_completion_usage_from_raw(metadata.get("initial_raw_response")),
        _extract_completion_usage_from_raw(metadata.get("repair_raw_response")),
    )


def _failed_validation_payload(
    *,
    reason: str,
    missing_requirements: Sequence[str] | None = None,
) -> dict[str, Any]:
    items = [str(item).strip() for item in (missing_requirements or []) if str(item).strip()]
    if not items:
        items = [reason]
    return {
        "passed": False,
        "overall_score": 0.0,
        "dimension_scores": {},
        "repair_brief": reason,
        "missing_requirements": items,
        "comparison_note": reason,
    }


def _normalize_validation_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _failed_validation_payload(
            reason="validator did not return a structured validation payload"
        )
    dimension_scores = payload.get("dimension_scores")
    missing_requirements = payload.get("missing_requirements")
    return {
        "passed": bool(payload.get("passed")),
        "overall_score": _coerce_float(payload.get("overall_score")),
        "dimension_scores": dict(dimension_scores) if isinstance(dimension_scores, dict) else {},
        "repair_brief": str(payload.get("repair_brief") or "").strip(),
        "missing_requirements": (
            [str(item).strip() for item in missing_requirements if str(item).strip()]
            if isinstance(missing_requirements, list)
            else []
        ),
        "comparison_note": str(payload.get("comparison_note") or "").strip(),
    }


def _extract_validation_payload(outputs: Any) -> Any:
    if not isinstance(outputs, dict):
        return outputs
    if any(key in outputs for key in ("passed", "overall_score", "missing_requirements")):
        return outputs
    for key in ("result", "text"):
        raw = outputs.get(key)
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text:
            continue
        try:
            decoded = json.loads(text)
        except Exception:
            continue
        if isinstance(decoded, dict):
            return decoded
    return outputs


def _merge_validation_failures(
    validation: dict[str, Any],
    failures: Sequence[str],
) -> dict[str, Any]:
    issues = [str(item).strip() for item in failures if str(item).strip()]
    if not issues:
        return dict(validation)
    merged = _normalize_validation_payload(validation)
    existing = list(merged.get("missing_requirements") or [])
    for issue in issues:
        if issue not in existing:
            existing.append(issue)
    merged["passed"] = False
    merged["overall_score"] = min(_coerce_float(merged.get("overall_score")), 0.49)
    merged["missing_requirements"] = existing
    if not str(merged.get("repair_brief") or "").strip():
        merged["repair_brief"] = issues[0]
    if not str(merged.get("comparison_note") or "").strip():
        merged["comparison_note"] = issues[0]
    return merged


def _existing_required_files_from_snapshot(
    snapshot: dict[str, str | None],
    required_paths: Sequence[Path],
) -> list[str]:
    existing: list[str] = []
    for path in required_paths:
        rendered = str(path.resolve(strict=False))
        if snapshot.get(rendered) is not None:
            existing.append(rendered)
    return existing


def _coordinated_website_change_failures(
    *,
    snapshot: dict[str, str | None],
    required_paths: Sequence[Path],
    changed_required_paths: Sequence[str],
) -> list[str]:
    existing_required = _existing_required_files_from_snapshot(snapshot, required_paths)
    if len(existing_required) < _LIVE_EXISTING_WEBSITE_MIN_CHANGED_FILES:
        return []
    changed_existing = {
        path for path in changed_required_paths if path in set(existing_required)
    }
    minimum = min(_LIVE_EXISTING_WEBSITE_MIN_CHANGED_FILES, len(existing_required))
    if len(changed_existing) >= minimum:
        return []
    return [
        (
            "Existing website redesign changed only "
            f"{len(changed_existing)} preexisting required file(s); update at least "
            f"{minimum} coordinated required files, such as index.html plus styles.css or app.js."
        )
    ]


def _website_static_validation_failures(
    report: SuperOrganismReport,
    *,
    required_paths: Sequence[Path],
    changed_required_paths: Sequence[str],
    file_snapshot: dict[str, str | None],
) -> list[str]:
    failures: list[str] = []
    if not changed_required_paths:
        failures.append("The live run did not change any required website files.")
    failures.extend(
        _coordinated_website_change_failures(
            snapshot=file_snapshot,
            required_paths=required_paths,
            changed_required_paths=changed_required_paths,
        )
    )
    if not required_paths:
        return failures
    index_path = Path(required_paths[0])
    if not index_path.exists():
        return failures
    try:
        html = " ".join(index_path.read_text(encoding="utf-8", errors="ignore").lower().split())
    except Exception as exc:
        failures.append(f"Failed to inspect {index_path}: {type(exc).__name__}: {exc}")
        return failures
    target = " ".join(str(report.target or "").lower().split())
    if target and len(target) >= 24 and target in html:
        failures.append("The generated website still echoes the raw operator prompt as page copy.")
    template_hits = [phrase for phrase in _WEBSITE_TEMPLATE_PHRASES if phrase in html]
    if len(template_hits) >= 2:
        failures.append(
            "The generated website still looks like the generic Super DAN contract/demo template."
        )
    return failures


def _log_final_validation_event(
    event_logger: SuperRunEventLogger | None,
    *,
    worker_id: str,
    model: str,
    validation: dict[str, Any],
    deterministic_failures: Sequence[str] | None = None,
) -> None:
    _log_live_event(
        event_logger,
        "live.validation.completed",
        worker_id=worker_id,
        model=model,
        status=validation.get("status"),
        passed=validation.get("passed"),
        overall_score=validation.get("overall_score"),
        deterministic_failures=list(deterministic_failures or []) or None,
        tool_calls=int(validation.get("tool_calls") or 0),
        event_count=int(validation.get("event_count") or 0),
    )


async def _run_live_validation(
    *,
    worker: WorkerDefinition,
    request: ExecutionRequest,
    tool_ids: Sequence[str],
    workspace_root: Path,
    args: argparse.Namespace,
    model: str,
    provider: LLMProvider,
    event_logger: SuperRunEventLogger | None = None,
) -> dict[str, Any]:
    _log_live_event(
        event_logger,
        "live.validation.started",
        worker_id=worker.id,
        model=model,
        tool_ids=list(tool_ids),
    )
    result, executed_tools, events = await _execute_live_request(
        worker=worker,
        request=request,
        tool_ids=tool_ids,
        workspace_root=workspace_root,
        args=args,
        model=model,
        provider=provider,
        event_logger=event_logger,
    )
    token_usage = _extract_execution_usage(result)
    validation = _normalize_validation_payload(
        _extract_validation_payload(dict(result.outputs))
    )
    if result.status != "completed":
        validation = _merge_validation_failures(
            validation,
            [result.error or "validator did not complete successfully"],
        )
    validation["status"] = result.status
    validation["tool_calls"] = len(executed_tools)
    validation["event_count"] = len(events)
    validation["error"] = result.error
    validation["token_usage"] = token_usage
    _log_live_event(
        event_logger,
        "live.validation.model_completed",
        worker_id=worker.id,
        model=model,
        status=validation.get("status"),
        passed=validation.get("passed"),
        tool_calls=len(executed_tools),
        event_count=len(events),
    )
    return validation


async def _run_live_website_build(
    report: SuperOrganismReport,
    args: argparse.Namespace,
    *,
    model: str,
    provider: LLMProvider,
    run_trace_id: str,
    run_task_id: str,
    event_logger: SuperRunEventLogger | None = None,
) -> dict[str, Any]:
    workspace_root, artifact_root, relative_files, required_paths = _live_artifact_layout(args)
    workspace_root.mkdir(parents=True, exist_ok=True)
    file_snapshot = _snapshot_file_state(required_paths)
    worker = _build_live_website_worker(model)
    request = ExecutionRequest.from_handoff(
        task=_live_website_task(
            report,
            artifact_root=artifact_root,
            relative_files=relative_files,
        ),
        scope=f"workspace={workspace_root}; artifact_root={artifact_root}; native Super DAN live website build",
        hard_constraints=[
            "Actually create or update all required website files with file_write or file_edit.",
            "Do not invoke the separate DAN Code or DAN Research product shells.",
            "Keep writes inside the requested workspace/artifact paths.",
            "Do not install dependencies or require a build step.",
            "Do not create scratch or throwaway files outside the required website artifact set.",
            (
                "If two or more required website files already existed at run start, "
                "treat the task as a coordinated redesign/update and materially change at least "
                f"{_LIVE_EXISTING_WEBSITE_MIN_CHANGED_FILES} required files."
            ),
        ],
        soft_constraints=[
            "Favor a visually distinctive, non-generic landing page.",
            "Use motion meaningfully to show cells synchronizing rather than decorative noise.",
            f"Keep copy focused on organized {report.cell_count}-cell execution and synergy.",
            (
                "Move at a paced incremental cadence: land one small valid section or file change at a time "
                "instead of attempting one giant rewrite."
            ),
            (
                f"Treat single file_write payloads above roughly {_LIVE_FILE_WRITE_SAFE_WORD_LIMIT} words or "
                f"{_LIVE_FILE_WRITE_SAFE_LINE_LIMIT} lines as risky and split them."
            ),
        ],
        evidence_blocks=[
            {
                "label": f"Super DAN {report.cell_count}-cell contract",
                "content": report.final_memo,
                "source": "super_organism_report",
                "trust_label": "advisory",
            },
            {
                "label": "Delivery nodes",
                "content": json.dumps(
                    [node.model_dump(mode="json") for node in report.delivery_plan],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "source": "super_organism_report",
                "trust_label": "advisory",
            },
            {
                "label": "Coordination tickets",
                "content": json.dumps(
                    [ticket.model_dump(mode="json") for ticket in report.coordination_tickets],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "source": "super_organism_report",
                "trust_label": "advisory",
            },
            {
                "label": "Handoff packets",
                "content": json.dumps(
                    [packet.model_dump(mode="json") for packet in report.handoff_packets],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "source": "super_organism_report",
                "trust_label": "advisory",
            },
            {
                "label": "Final audit gate",
                "content": json.dumps(
                    (report.final_audit.model_dump(mode="json") if report.final_audit is not None else {}),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "source": "super_organism_report",
                "trust_label": "advisory",
            },
        ],
        tooling={
            "allowed_tool_ids": list(_LIVE_WEBSITE_TOOL_IDS),
            "preferred_tool_ids": ["file_write", "file_edit", "file_read", "list_directory"],
            "max_tool_calls": int(args.max_tool_calls),
        },
        definition_of_done=(
            "All required files exist on disk and the final response names the files created, "
            "a concise validation plan, and any remaining risks."
        ),
        expected_return_shape=_live_expected_return_shape(),
        input_payload={
            "objective": report.target,
            "workspace_root": str(workspace_root),
            "artifact_root": str(artifact_root),
            "artifact_files": list(relative_files),
            "organism_id": report.organism_id,
            "cell_count": report.cell_count,
            "active_cell_cap": report.active_cell_cap,
            "organ_counts": dict(report.organ_counts),
            "delivery_plan": [node.model_dump(mode="json") for node in report.delivery_plan],
            "write_pacing": {
                "safe_file_write_word_limit": _LIVE_FILE_WRITE_SAFE_WORD_LIMIT,
                "safe_file_write_line_limit": _LIVE_FILE_WRITE_SAFE_LINE_LIMIT,
                "prefer_incremental_file_edit_on_existing_files": True,
                "forbid_scratch_files_outside_required_artifacts": True,
            },
            "existing_required_files": _existing_required_files_from_snapshot(
                file_snapshot,
                required_paths,
            ),
            "existing_website_min_changed_required_files": _LIVE_EXISTING_WEBSITE_MIN_CHANGED_FILES,
            "shared_board": (
                report.shared_board.model_dump(mode="json") if report.shared_board is not None else None
            ),
            "coordination_tickets": [
                ticket.model_dump(mode="json") for ticket in report.coordination_tickets
            ],
            "handoff_packets": [packet.model_dump(mode="json") for packet in report.handoff_packets],
            "final_audit": (
                report.final_audit.model_dump(mode="json") if report.final_audit is not None else None
            ),
        },
        metadata={
            "surface": "super_organism",
            "mode": "live",
            "tool_budget_profile": "super_dan_live",
            "trace_id": run_trace_id,
            "root_task_id": run_task_id,
            "organism_id": report.organism_id,
            "organ_id": "super-dan.live.website",
            "organism_stage": "execution",
            "worker_id": worker.id,
        },
    )
    _log_live_event(
        event_logger,
        "live.website_build.started",
        model=model,
        workspace_root=str(workspace_root),
        artifact_root=str(artifact_root),
        tool_ids=list(_LIVE_WEBSITE_TOOL_IDS),
    )
    result, executed_tools, events = await _execute_live_request(
        worker=worker,
        request=request,
        tool_ids=_LIVE_WEBSITE_TOOL_IDS,
        workspace_root=workspace_root,
        args=args,
        model=model,
        provider=provider,
        event_logger=event_logger,
    )
    _log_live_event(
        event_logger,
        "live.website_build.completed",
        model=model,
        status=result.status,
        tool_calls=len(executed_tools),
        event_count=len(events),
    )
    build_token_usage = _extract_execution_usage(result)
    existing_paths = [path for path in required_paths if path.exists()]
    missing_paths = [path for path in required_paths if not path.exists()]
    changed_required_paths = _changed_paths_from_snapshot(file_snapshot, required_paths)
    mutated_paths = _mutation_paths_from_tools(executed_tools, workspace_root=workspace_root)
    error = result.error
    if missing_paths and not error:
        error = (
            "live build finished without creating required files: "
            + ", ".join(str(path) for path in missing_paths)
        )
    validation = _failed_validation_payload(
        reason=error or "live website build did not meet the exit contract",
        missing_requirements=[str(path) for path in missing_paths],
    )
    if not missing_paths and changed_required_paths:
        validator_request = ExecutionRequest.from_handoff(
            task=_live_website_validation_task(
                report,
                artifact_root=artifact_root,
                relative_files=relative_files,
            ),
            scope=f"workspace={workspace_root}; artifact_root={artifact_root}; native Super DAN website validation",
            hard_constraints=[
                "Read-only validation only; do not write or edit files.",
                "Inspect the required website files directly before deciding.",
                "Fail if the result is still a generic execution-contract/demo template.",
                "Fail if the page mostly echoes the raw operator prompt.",
            ],
            soft_constraints=[
                "Prefer concrete missing requirements over vague criticism.",
                "Judge objective alignment, not just file existence.",
            ],
            tooling={
                "allowed_tool_ids": list(_LIVE_WEBSITE_TOOL_IDS),
                "preferred_tool_ids": ["file_read", "list_directory"],
                "max_tool_calls": max(4, min(int(args.max_tool_calls), 12)),
            },
            definition_of_done="Return the validation report only.",
            expected_return_shape=_live_validation_return_shape(),
            input_payload={
                "objective": report.target,
                "workspace_root": str(workspace_root),
                "artifact_root": str(artifact_root),
                "artifact_files": list(relative_files),
                "required_files": [str(path) for path in required_paths],
                "changed_required_files": list(changed_required_paths),
            },
            metadata={
                "surface": "super_organism",
                "mode": "live",
                "tool_budget_profile": "super_dan_live",
                "trace_id": run_trace_id,
                "root_task_id": run_task_id,
                "organism_id": report.organism_id,
                "organ_id": "super-dan.live.website",
                "worker_id": "super-dan.live.website.validator",
                "organism_stage": "validation",
            },
        )
        validation = await _run_live_validation(
            worker=_build_live_website_validator(model),
            request=validator_request,
            tool_ids=_LIVE_WEBSITE_TOOL_IDS,
            workspace_root=workspace_root,
            args=args,
            model=model,
            provider=provider,
            event_logger=event_logger,
        )
    static_validation_failures = _website_static_validation_failures(
        report,
        required_paths=required_paths,
        changed_required_paths=changed_required_paths,
        file_snapshot=file_snapshot,
    )
    validation = _merge_validation_failures(
        validation,
        static_validation_failures,
    )
    validation["deterministic_failures"] = list(static_validation_failures)
    _log_final_validation_event(
        event_logger,
        worker_id="super-dan.live.website.validator",
        model=model,
        validation=validation,
        deterministic_failures=static_validation_failures,
    )
    if not changed_required_paths and not error:
        error = "live build finished without changing any required website files"
    if not validation.get("passed") and not error:
        error = (
            str(validation.get("repair_brief") or "").strip()
            or "live build failed validation"
        )
    status = (
        "completed"
        if result.status == "completed"
        and not missing_paths
        and bool(changed_required_paths)
        and bool(validation.get("passed"))
        else "failed"
    )
    token_usage = _merge_token_usage(
        build_token_usage,
        validation.get("token_usage"),
    )
    return {
        "status": status,
        "mode": "live",
        "model": model,
        "workspace_root": str(workspace_root),
        "artifact_root": str(artifact_root),
        "website": str(required_paths[0]),
        "files": [str(path) for path in existing_paths],
        "required_files": [str(path) for path in required_paths],
        "missing_files": [str(path) for path in missing_paths],
        "changed_required_files": list(changed_required_paths),
        "tool_calls": len(executed_tools) + int(validation.get("tool_calls") or 0),
        "mutated_paths": mutated_paths,
        "event_count": len(events) + int(validation.get("event_count") or 0),
        "summary": result.outputs.get("result") or result.outputs.get("text") or "",
        "error": error,
        "summary_label": "Live Build",
        "objective_kind": "website",
        "token_usage": token_usage,
        "validation": validation,
    }


async def _run_live_generic_execution(
    report: SuperOrganismReport,
    args: argparse.Namespace,
    *,
    model: str,
    provider: LLMProvider,
    run_trace_id: str,
    run_task_id: str,
    event_logger: SuperRunEventLogger | None = None,
) -> dict[str, Any]:
    workspace_root = normalize_workspace_root(str(args.workspace))
    workspace_root.mkdir(parents=True, exist_ok=True)
    worker = _build_live_generic_worker(model)
    request = ExecutionRequest.from_handoff(
        task=_live_generic_task(report, workspace_root=workspace_root),
        scope=f"workspace={workspace_root}; native Super DAN live coding/build execution",
        hard_constraints=[
            "Actually mutate workspace files before finalizing.",
            "Do not invoke the separate DAN Code or DAN Research product shells.",
            "Keep the work inside the current workspace root.",
            "Do not use destructive git reset, checkout, or rm-style cleanup.",
        ],
        soft_constraints=[
            "Prefer a bounded implementation over a broad speculative rewrite.",
            "Use shell_command only when it materially verifies or inspects the workspace.",
            "Keep the final summary concise and inspectable.",
            (
                "Move at a paced incremental cadence: land one small valid patch at a time instead of "
                "attempting one giant rewrite."
            ),
            (
                f"Treat single file_write payloads above roughly {_LIVE_FILE_WRITE_SAFE_WORD_LIMIT} words or "
                f"{_LIVE_FILE_WRITE_SAFE_LINE_LIMIT} lines as risky and split them."
            ),
        ],
        evidence_blocks=[
            {
                "label": f"Super DAN {report.cell_count}-cell contract",
                "content": report.final_memo,
                "source": "super_organism_report",
                "trust_label": "advisory",
            },
            {
                "label": "Delivery nodes",
                "content": json.dumps(
                    [node.model_dump(mode="json") for node in report.delivery_plan],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "source": "super_organism_report",
                "trust_label": "advisory",
            },
            {
                "label": "Coordination tickets",
                "content": json.dumps(
                    [ticket.model_dump(mode="json") for ticket in report.coordination_tickets],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "source": "super_organism_report",
                "trust_label": "advisory",
            },
            {
                "label": "Handoff packets",
                "content": json.dumps(
                    [packet.model_dump(mode="json") for packet in report.handoff_packets],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "source": "super_organism_report",
                "trust_label": "advisory",
            },
            {
                "label": "Final audit gate",
                "content": json.dumps(
                    (report.final_audit.model_dump(mode="json") if report.final_audit is not None else {}),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "source": "super_organism_report",
                "trust_label": "advisory",
            },
        ],
        tooling={
            "allowed_tool_ids": list(_LIVE_GENERIC_TOOL_IDS),
            "preferred_tool_ids": [
                "list_directory",
                "file_read",
                "file_edit",
                "file_write",
                "git_diff",
                "shell_command",
            ],
            "max_tool_calls": int(args.max_tool_calls),
        },
        definition_of_done=(
            "At least one workspace file was created or edited and the final response names the changed files, "
            "a concise validation plan, and remaining risks."
        ),
        expected_return_shape=_live_expected_return_shape(),
        input_payload={
            "objective": report.target,
            "workspace_root": str(workspace_root),
            "organism_id": report.organism_id,
            "cell_count": report.cell_count,
            "active_cell_cap": report.active_cell_cap,
            "execution_family": report.execution_family,
            "organ_counts": dict(report.organ_counts),
            "delivery_plan": [node.model_dump(mode="json") for node in report.delivery_plan],
            "write_pacing": {
                "safe_file_write_word_limit": _LIVE_FILE_WRITE_SAFE_WORD_LIMIT,
                "safe_file_write_line_limit": _LIVE_FILE_WRITE_SAFE_LINE_LIMIT,
                "prefer_incremental_file_edit_on_existing_files": True,
            },
            "shared_board": (
                report.shared_board.model_dump(mode="json") if report.shared_board is not None else None
            ),
            "coordination_tickets": [
                ticket.model_dump(mode="json") for ticket in report.coordination_tickets
            ],
            "handoff_packets": [packet.model_dump(mode="json") for packet in report.handoff_packets],
            "final_audit": (
                report.final_audit.model_dump(mode="json") if report.final_audit is not None else None
            ),
        },
        metadata={
            "surface": "super_organism",
            "mode": "live",
            "tool_budget_profile": "super_dan_live",
            "trace_id": run_trace_id,
            "root_task_id": run_task_id,
            "organism_id": report.organism_id,
            "organ_id": "super-dan.live.coding",
            "organism_stage": "execution",
            "worker_id": worker.id,
        },
    )
    _log_live_event(
        event_logger,
        "live.generic_build.started",
        model=model,
        workspace_root=str(workspace_root),
        tool_ids=list(_LIVE_GENERIC_TOOL_IDS),
    )
    result, executed_tools, events = await _execute_live_request(
        worker=worker,
        request=request,
        tool_ids=_LIVE_GENERIC_TOOL_IDS,
        workspace_root=workspace_root,
        args=args,
        model=model,
        provider=provider,
        event_logger=event_logger,
    )
    _log_live_event(
        event_logger,
        "live.generic_build.completed",
        model=model,
        status=result.status,
        tool_calls=len(executed_tools),
        event_count=len(events),
    )
    build_token_usage = _extract_execution_usage(result)
    mutated_paths = _mutation_paths_from_tools(executed_tools, workspace_root=workspace_root)
    error = result.error
    if not mutated_paths and not error:
        error = "live execution finished without any workspace file mutations"
    validation = _failed_validation_payload(
        reason=error or "live execution did not meet the exit contract",
        missing_requirements=(
            ["No workspace file mutations were observed."]
            if not mutated_paths
            else None
        ),
    )
    if mutated_paths:
        validator_request = ExecutionRequest.from_handoff(
            task=_live_generic_validation_task(report, workspace_root=workspace_root),
            scope=f"workspace={workspace_root}; native Super DAN coding/build validation",
            hard_constraints=[
                "Read-only validation only; do not write or edit files.",
                "Inspect the mutated files and relevant read-only git evidence before deciding.",
                "Fail if the run made only placeholder-style or otherwise non-responsive changes.",
            ],
            soft_constraints=[
                "Prefer concrete missing requirements over vague criticism.",
                "Judge material advancement against the operator objective.",
            ],
            tooling={
                "allowed_tool_ids": list(_LIVE_GENERIC_TOOL_IDS),
                "preferred_tool_ids": ["git_diff", "file_read", "git_status", "list_directory"],
                "max_tool_calls": max(4, min(int(args.max_tool_calls), 12)),
            },
            definition_of_done="Return the validation report only.",
            expected_return_shape=_live_validation_return_shape(),
            input_payload={
                "objective": report.target,
                "workspace_root": str(workspace_root),
                "mutated_paths": list(mutated_paths),
            },
            metadata={
                "surface": "super_organism",
                "mode": "live",
                "tool_budget_profile": "super_dan_live",
                "trace_id": run_trace_id,
                "root_task_id": run_task_id,
                "organism_id": report.organism_id,
                "organ_id": "super-dan.live.coding",
                "worker_id": "super-dan.live.coding.validator",
                "organism_stage": "validation",
            },
        )
        validation = await _run_live_validation(
            worker=_build_live_generic_validator(model),
            request=validator_request,
            tool_ids=_LIVE_GENERIC_TOOL_IDS,
            workspace_root=workspace_root,
            args=args,
            model=model,
            provider=provider,
            event_logger=event_logger,
        )
    _log_final_validation_event(
        event_logger,
        worker_id="super-dan.live.coding.validator",
        model=model,
        validation=validation,
    )
    if not validation.get("passed") and not error:
        error = (
            str(validation.get("repair_brief") or "").strip()
            or "live execution failed validation"
        )
    status = (
        "completed"
        if result.status == "completed" and mutated_paths and bool(validation.get("passed"))
        else "failed"
    )
    token_usage = _merge_token_usage(
        build_token_usage,
        validation.get("token_usage"),
    )
    return {
        "status": status,
        "mode": "live",
        "model": model,
        "workspace_root": str(workspace_root),
        "files": list(mutated_paths),
        "required_files": [],
        "missing_files": [],
        "tool_calls": len(executed_tools) + int(validation.get("tool_calls") or 0),
        "mutated_paths": list(mutated_paths),
        "event_count": len(events) + int(validation.get("event_count") or 0),
        "summary": result.outputs.get("result") or result.outputs.get("text") or "",
        "error": error,
        "summary_label": "Live Run",
        "objective_kind": "coding",
        "token_usage": token_usage,
        "validation": validation,
    }


def _mutation_paths_from_tools(
    executed_tools: Sequence[dict[str, Any]],
    *,
    workspace_root: Path,
) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for tool in executed_tools:
        if not tool.get("ok") or str(tool.get("tool_id") or "") not in {"file_write", "file_edit"}:
            continue
        arguments = tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {}
        result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
        raw_path = str(result.get("path") or arguments.get("path") or arguments.get("file_path") or "").strip()
        if not raw_path:
            continue
        candidate = Path(raw_path).expanduser()
        path = candidate if candidate.is_absolute() else workspace_root / candidate
        rendered = str(path.resolve(strict=False))
        if rendered in seen:
            continue
        seen.add(rendered)
        paths.append(rendered)
    return paths


def _render_website_html(report: SuperOrganismReport) -> str:
    objective = _html_escape(report.target)
    cell_count = int(report.cell_count)
    active_cell_cap = int(report.active_cell_cap)
    nodes = "\n".join(
        f"""          <article class="node-card" data-status="{_html_escape(node.status)}">
            <span>{_html_escape(node.node_id)}</span>
            <h3>{_html_escape(node.title)}</h3>
            <p>{_html_escape('; '.join(node.notes[:1]) or 'A coordinated cell contract.')}</p>
          </article>"""
        for node in report.delivery_plan
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Super DAN Organism</title>
  <link rel="stylesheet" href="./styles.css" />
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="hero-copy">
        <p class="eyebrow">{cell_count}-cell universal agent organism</p>
        <h1>Super DAN turns one objective into coordinated execution.</h1>
        <p class="lede">{objective}</p>
        <div class="metrics">
          <span>{cell_count} logical cells</span>
          <span>{active_cell_cap} active cap</span>
          <span>{_html_escape(report.score_label)} {report.credibility_score:.2f}</span>
        </div>
      </div>
      <div class="organism-stage" aria-label="Animated {cell_count}-cell organism">
        <div class="cell-field"></div>
        <div class="core">DAN</div>
      </div>
    </section>

    <section class="organs">
      <p class="eyebrow">Organ contracts</p>
      <h2>Cells specialize, then synchronize.</h2>
      <div class="organ-grid">
        <div>Brain<br><strong>objective + authority</strong></div>
        <div>Scout<br><strong>context acquisition</strong></div>
        <div>Claim<br><strong>work graph atoms</strong></div>
        <div>Immune<br><strong>risk pressure</strong></div>
        <div>Memory<br><strong>shared board</strong></div>
        <div>Experiment<br><strong>execution probe</strong></div>
        <div>Synthesis<br><strong>done / continue / clarify</strong></div>
      </div>
    </section>

    <section class="contract">
      <p class="eyebrow">Execution contract</p>
      <h2>{_html_escape(report.final_verdict)}</h2>
      <div class="node-grid">
{nodes}
      </div>
    </section>
  </main>
  <script src="./app.js"></script>
</body>
</html>
"""


def _render_website_css() -> str:
    return """:root {
  color-scheme: dark;
  --bg: #10130f;
  --panel: #182017;
  --ink: #f2ead8;
  --muted: #b7ad96;
  --signal: #f2b84b;
  --immune: #ff6b4a;
  --memory: #7ed7b5;
  --brain: #a7c7ff;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background:
    radial-gradient(circle at 20% 10%, rgba(242, 184, 75, .22), transparent 28rem),
    radial-gradient(circle at 90% 20%, rgba(126, 215, 181, .14), transparent 24rem),
    linear-gradient(135deg, #10130f, #070806 70%);
  color: var(--ink);
  font: 16px/1.5 "Iowan Old Style", "Palatino Linotype", Palatino, serif;
}
.shell { width: min(1180px, calc(100vw - 40px)); margin: 0 auto; padding: 56px 0; }
.hero { display: grid; grid-template-columns: 1fr 520px; gap: 48px; align-items: center; min-height: 78vh; }
.eyebrow { color: var(--signal); letter-spacing: .18em; text-transform: uppercase; font: 700 12px/1.2 "Avenir Next", "Gill Sans", sans-serif; }
h1, h2 { line-height: .94; margin: 0; letter-spacing: -.05em; }
h1 { font-size: clamp(48px, 8vw, 104px); max-width: 820px; }
h2 { font-size: clamp(36px, 6vw, 72px); }
.lede { color: var(--muted); font-size: clamp(18px, 2vw, 24px); max-width: 680px; }
.metrics { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 28px; }
.metrics span, .node-card, .organ-grid div {
  border: 1px solid rgba(242, 234, 216, .16);
  background: rgba(24, 32, 23, .74);
  box-shadow: 0 20px 80px rgba(0,0,0,.24);
}
.metrics span { padding: 10px 14px; border-radius: 999px; color: var(--ink); }
.organism-stage { position: relative; aspect-ratio: 1; border-radius: 48px; overflow: hidden; background: rgba(24, 32, 23, .64); border: 1px solid rgba(242, 234, 216, .14); }
.cell-field { position: absolute; inset: 0; }
.cell { position: absolute; width: 10px; height: 10px; border-radius: 999px; background: var(--signal); transform: translate(-50%, -50%); animation: pulse 2.8s ease-in-out infinite; box-shadow: 0 0 22px currentColor; }
.cell:nth-child(3n) { color: var(--memory); background: var(--memory); }
.cell:nth-child(5n) { color: var(--immune); background: var(--immune); }
.cell:nth-child(7n) { color: var(--brain); background: var(--brain); }
.core { position: absolute; inset: 50% auto auto 50%; transform: translate(-50%, -50%); width: 112px; height: 112px; border-radius: 50%; display: grid; place-items: center; background: var(--ink); color: #111; font: 900 26px/1 "Avenir Next", "Gill Sans", sans-serif; }
.organs, .contract { padding: 84px 0 0; }
.organ-grid, .node-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-top: 28px; }
.organ-grid div, .node-card { border-radius: 24px; padding: 22px; min-height: 132px; }
.node-card span { color: var(--signal); font: 800 12px/1 "Avenir Next", "Gill Sans", sans-serif; }
.node-card h3 { margin: 12px 0 8px; font-size: 22px; line-height: 1.05; }
.node-card p { margin: 0; color: var(--muted); }

@keyframes pulse {
  0%, 100% { transform: translate(-50%, -50%) scale(.74); opacity: .45; }
  45% { transform: translate(-50%, -50%) scale(1.4); opacity: 1; }
}
@media (max-width: 900px) {
  .hero { grid-template-columns: 1fr; }
  .organ-grid, .node-grid { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  .cell { animation: none; }
}
"""


def _render_website_js(report: SuperOrganismReport) -> str:
    total = max(1, int(report.cell_count))
    return f"""const field = document.querySelector('.cell-field');
if (field) {{
  const total = {total};
  const rings = Math.max(1, Math.ceil(total / 20));
  const perRing = Math.max(1, Math.ceil(total / rings));
  for (let index = 0; index < total; index += 1) {{
    const cell = document.createElement('i');
    cell.className = 'cell';
    const ring = Math.floor(index / perRing) + 1;
    const angle = (index % perRing) / perRing * Math.PI * 2 + ring * 0.22;
    const radius = 8 + (ring / (rings + 1)) * 34;
    const x = 50 + Math.cos(angle) * radius;
    const y = 50 + Math.sin(angle) * radius;
    cell.style.left = `${{x}}%`;
    cell.style.top = `${{y}}%`;
    cell.style.animationDelay = `${{(index % 20) * 70}}ms`;
    field.appendChild(cell);
  }}
}}
"""


def _render_website_readme(report: SuperOrganismReport) -> str:
    return f"""# Super DAN Website Artifact

Generated by `dan super-organism` from this objective:

```text
{report.target}
```

Open `index.html` in a browser, or serve this folder with any static server.

The artifact is deterministic and self-contained. It does not depend on DAN Code
or DAN Research.
"""


def _html_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def main(argv: Sequence[str] | None = None) -> int:
    load_env()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if bool(getattr(args, "live", False)) and bool(getattr(args, "plan_only", False)):
        parser.error("--live cannot be combined with --plan-only")

    try:
        report = run_super_organism_demo(
            args.target,
            organism_id=str(args.organism_id),
            cell_count=int(args.cell_count),
            active_cell_cap=int(args.active_cell_cap),
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    payload = report.model_dump(mode="json")
    live_result: dict[str, Any] | None = None
    if bool(getattr(args, "live", False)):
        if not _supports_live_execution(report):
            parser.error(
                "--live currently supports website-like and general coding/build objectives"
            )
        live_workspace_root = normalize_workspace_root(str(args.workspace))
        live_workdir, live_turn_number = _build_super_run_workdir(live_workspace_root)
        live_trace_id = new_trace_id()
        live_task_id = f"super-dan-live:{live_turn_number}"
        objective_kind = "website" if _is_website_objective(report) else "coding"
        event_logger = SuperRunEventLogger(
            path=live_workdir / "events.jsonl",
            session_id=_super_session_id(live_workspace_root),
            turn_id=str(live_turn_number),
            task_id=live_task_id,
            organism_id=report.organism_id,
            organ_id="super-dan.live",
            trace_id=live_trace_id,
        )
        try:
            _log_live_event(
                event_logger,
                "run.log.started",
                trace_id=live_trace_id,
                task_id=live_task_id,
                turn_number=live_turn_number,
                objective=report.target,
                objective_kind=objective_kind,
                workspace_root=str(live_workspace_root),
                workdir=str(live_workdir),
                requested_model=str(args.model or "").strip() or None,
            )
            try:
                model = _resolve_live_model(args.model)
                _log_live_event(
                    event_logger,
                    "provider.build.started",
                    requested_model=str(args.model or "").strip() or None,
                    model=model,
                    base_url=str(args.base_url or "").strip() or None,
                )
                provider = _build_live_provider(
                    model,
                    api_key=args.api_key,
                    base_url=args.base_url,
                )
                _log_live_event(
                    event_logger,
                    "provider.build.completed",
                    model=model,
                    base_url=str(args.base_url or "").strip() or None,
                )
            except Exception as exc:
                _log_live_event(
                    event_logger,
                    "provider.build.failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    requested_model=str(args.model or "").strip() or None,
                    base_url=str(args.base_url or "").strip() or None,
                )
                _log_live_event(
                    event_logger,
                    "run.log.failed",
                    trace_id=live_trace_id,
                    task_id=live_task_id,
                    status="failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    event_log_path=str(event_logger.path),
                    event_log_schema=ORGANISM_LOG_SCHEMA_VERSION,
                )
                parser.error(str(exc))
                return 2
            try:
                if _is_website_objective(report):
                    live_result = asyncio.run(
                        _run_live_website_build(
                            report,
                            args,
                            model=model,
                            provider=provider,
                            run_trace_id=live_trace_id,
                            run_task_id=live_task_id,
                            event_logger=event_logger,
                        )
                    )
                else:
                    live_result = asyncio.run(
                        _run_live_generic_execution(
                            report,
                            args,
                            model=model,
                            provider=provider,
                            run_trace_id=live_trace_id,
                            run_task_id=live_task_id,
                            event_logger=event_logger,
                        )
                    )
            except Exception as exc:
                _log_live_event(
                    event_logger,
                    "run.log.failed",
                    trace_id=live_trace_id,
                    task_id=live_task_id,
                    status="failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    event_log_path=str(event_logger.path),
                    event_log_schema=ORGANISM_LOG_SCHEMA_VERSION,
                )
                print(
                    f"Live Super DAN failed: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                return 1
            live_result = {
                **dict(live_result or {}),
                "event_log_path": str(event_logger.path),
                "event_log_schema": ORGANISM_LOG_SCHEMA_VERSION,
            }
            _log_live_event(
                event_logger,
                "run.log.completed",
                trace_id=live_trace_id,
                task_id=live_task_id,
                status=live_result.get("status"),
                objective_kind=objective_kind,
                validation_passed=bool(
                    dict(live_result.get("validation") or {}).get("passed")
                ),
                tool_calls=int(live_result.get("tool_calls") or 0),
                event_count=int(live_result.get("event_count") or 0),
                event_log_path=str(event_logger.path),
                event_log_schema=ORGANISM_LOG_SCHEMA_VERSION,
            )
        finally:
            event_logger.close()
        payload = {"report": payload, "live_build": live_result}

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        if live_result is not None and live_result.get("status") != "completed":
            return 1
    elif live_result is not None:
        _print_live_report(
            report,
            live_result,
            verbose=bool(getattr(args, "verbose", False)),
        )
        if live_result.get("status") != "completed":
            return 1
    else:
        paths = (
            _materialize_website_artifact(report, args)
            if _should_materialize_website(report, args)
            else []
        )
        if bool(getattr(args, "verbose", False)):
            _print_text_report(report)
            if paths:
                artifact_lines = ["", "Materialized Artifacts:", *[f"- {path}" for path in paths], ""]
                print("\n".join(artifact_lines), end="\n")
        else:
            _print_compact_report(report, artifact_paths=paths)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
