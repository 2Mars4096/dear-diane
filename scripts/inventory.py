#!/usr/bin/env python3
"""Generate the key-script inventory and structural guardrails report.

This report combines live repo scans with checked-in annotations so the
structural inventory stays current as the codebase moves.
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("docs/key-scripts.md")

BACKEND_ROOTS = (Path("src/dan"),)
FRONTEND_ROOTS = (Path("editor/src"),)

BACKEND_SUFFIXES = (".py",)
FRONTEND_SUFFIXES = (".ts", ".tsx")

BACKEND_TOP_N = 20
FRONTEND_TOP_N = 15
PROMPT_TOP_N = 12

PROMPT_TOKENS = ("prompt_context", "extra_system_instructions")

IMPORT_RULES = (
    ("dan.server.*", re.compile(r"^\s*(?:from|import)\s+dan\.server(?:\.|\b)")),
    ("dan.executors.*", re.compile(r"^\s*(?:from|import)\s+dan\.executors(?:\.|\b)")),
    ("dan.models.legacy", re.compile(r"^\s*(?:from|import)\s+dan\.models\.legacy(?:\.|\b)")),
)


@dataclass(frozen=True)
class KeyScript:
    path: str
    role: str
    authoritative_owner: str
    current_overreach: str
    target_boundary: str
    follow_on: str


KEY_SCRIPTS: tuple[KeyScript, ...] = (
    KeyScript(
        path="src/dan/server/concierge/runtime/__init__.py",
        role="control-plane sink",
        authoritative_owner="orchestration and state progression",
        current_overreach="dispatch policy, schedule rewrite, progress UX, and control-plane glue still share one file",
        target_boundary="runtime/dispatch_policy.py, runtime/schedule_commands.py, runtime/progress_ux.py",
        follow_on="50-5",
    ),
    KeyScript(
        path="src/dan/server/chat_manager.py",
        role="facade sink",
        authoritative_owner="chat boundary and import shim only",
        current_overreach="still owns workflow build/apply/save, smoke-run glue, and prompt plumbing",
        target_boundary="src/dan/server/chat/* plus src/dan/server/agent_runtime/*",
        follow_on="50-3",
    ),
    KeyScript(
        path="src/dan/server/run_manager.py",
        role="lifecycle owner",
        authoritative_owner="run lifecycle, start/resume/cancel, relay seam",
        current_overreach="run guards are caller-owned today and the completion tail still mixes reflection, telemetry, and learning",
        target_boundary="workflow_guards.py in 50-2, run_finalization.py in 50-4",
        follow_on="50-2, 50-4",
    ),
    KeyScript(
        path="src/dan/server/capability_handlers.py",
        role="compatibility facade",
        authoritative_owner="registration and schema wiring only",
        current_overreach="still owns graph delete, mutation apply/save, and latest-mutation lookup",
        target_boundary="src/dan/server/capabilities/*",
        follow_on="50-3",
    ),
    KeyScript(
        path="src/dan/server/concierge/tier_executors.py",
        role="prompt and handoff sink",
        authoritative_owner="slot-based prompt assembly and child-handoff derivation",
        current_overreach="duplicates prompt build/extract/apply chains and mixes execution, events, and handoff text",
        target_boundary="shared executor base plus typed envelopes",
        follow_on="46-6",
    ),
    KeyScript(
        path="src/dan/server/concierge/scheduler.py",
        role="scheduler daemon owner",
        authoritative_owner="lease authority and fire-time execution only",
        current_overreach="still carries user-facing schedule-command semantics and workflow normalization",
        target_boundary="schedule surface helpers outside the daemon boundary",
        follow_on="50-5",
    ),
    KeyScript(
        path="src/dan/server/concierge/dispatcher.py",
        role="ingress and queue adapter",
        authoritative_owner="ingress serialization and canonical queue lease model",
        current_overreach="shares queue and task ownership with tiered_dispatch, models.py, and task_registry.py",
        target_boundary="one documented queue owner with task/session ids demoted accordingly",
        follow_on="50-5",
    ),
    KeyScript(
        path="src/dan/server/concierge/models.py",
        role="user-facing state truth",
        authoritative_owner="SurfaceMessage, Task, Project, and ResolvedContext truth",
        current_overreach="project/task truth overlaps with ConciergeTask and Session ids in adjacent layers",
        target_boundary="keep user-facing truth here; demote lifecycle and trace mirrors",
        follow_on="50-5",
    ),
    KeyScript(
        path="src/dan/server/concierge/task_registry.py",
        role="task lifecycle tracker",
        authoritative_owner="concierge-level task state and persistence",
        current_overreach="overlaps with dispatcher queues and models.Task/Project on work identity",
        target_boundary="either canonical queue lease owner or thin state tracker, not both",
        follow_on="50-5",
    ),
    KeyScript(
        path="src/dan/server/concierge/session.py",
        role="execution trace owner",
        authoritative_owner="runtime session tree and execution traces",
        current_overreach="session ids bleed into user-facing work-state reasoning",
        target_boundary="keep runtime-only trace ownership here",
        follow_on="50-5",
    ),
    KeyScript(
        path="src/dan/server/agent_runtime/workflow_generation.py",
        role="authoring sink",
        authoritative_owner="workflow-generation phases with explicit package seams",
        current_overreach="planning, build, diagnosis, repair, and recovery still mix in one file",
        target_boundary="workflow_generation/ package split by phase",
        follow_on="50-6",
    ),
    KeyScript(
        path="src/dan/server/graph_mutator.py",
        role="mutation sink",
        authoritative_owner="apply engine separated from macro authoring and repair policy",
        current_overreach="apply, macros, migration policy, and repair logic still mix together",
        target_boundary="split by apply engine, macros, and repair helpers",
        follow_on="50-6",
    ),
    KeyScript(
        path="src/dan/server/routers/adapters.py",
        role="gateway sink",
        authoritative_owner="adapter protocol and streaming surface only",
        current_overreach="adapter state, routing, callback protocol, and streaming are still bundled together",
        target_boundary="smaller adapter-surface modules",
        follow_on="50-6",
    ),
    KeyScript(
        path="src/dan/worker/executor.py",
        role="worker adapter boundary",
        authoritative_owner="core worker compute dispatch behind adapter seams",
        current_overreach="still mixes engine-facing dispatch, cached legacy-template reuse, and DAN runtime compatibility modes beside the thinner worker-core membrane",
        target_boundary="dan/worker/core/ plus dan/worker/adapters/",
        follow_on="46-7",
    ),
    KeyScript(
        path="src/dan/executor_defaults.py",
        role="default runtime registry",
        authoritative_owner="default adapter registration only",
        current_overreach="ready llm/tool/code/input families now use dedicated worker-backed runtime executors, but default policy still mixes worker-backed paths with remaining explicit legacy registrations",
        target_boundary="keep only honest default policy wiring while any remaining compatibility projections shrink outward",
        follow_on="46-7",
    ),
    KeyScript(
        path="editor/src/components/ChatPanel.tsx",
        role="frontend shell sink",
        authoritative_owner="chat thread UI shell only",
        current_overreach="thread, stream, run, reconnect, persistence, and branch state all mix together",
        target_boundary="stream/reconnect/persistence hooks and message-list components",
        follow_on="50-8",
    ),
    KeyScript(
        path="editor/src/components/ConfigPanel.tsx",
        role="frontend config sink",
        authoritative_owner="node and edge config editing shell only",
        current_overreach="schema validation, editing logic, and form rendering still share one file",
        target_boundary="config subpanels and validation helpers",
        follow_on="50-8",
    ),
    KeyScript(
        path="editor/src/store/useGraphStore.ts",
        role="frontend state sink",
        authoritative_owner="graph-editor state only",
        current_overreach="workflow, chat-session, and broader workspace state still spill into the graph store",
        target_boundary="chat-session, run-history, and workspace-tab stores",
        follow_on="50-8",
    ),
    KeyScript(
        path="editor/src/store/useMessagingStore.ts",
        role="frontend messaging sink",
        authoritative_owner="messaging and transport state only",
        current_overreach="adapter and broader UI concerns still spill into one store",
        target_boundary="narrow messaging-only selectors and split stores if the inventory still justifies it",
        follow_on="50-8",
    ),
)

STATE_FLOW_ROWS = (
    (
        "SurfaceMessage",
        "src/dan/server/concierge/models.py",
        "authoritative ingress packet for surface text, ids, attachments, metadata",
        "metadata copies inside dispatcher and tiered dispatch",
        "keep here; delete duplicated prompt metadata copies during 50-5 and 46-6",
    ),
    (
        "ResolvedContext",
        "src/dan/server/concierge/models.py",
        "turn-level resolved project, task, workflow, and follow-up context",
        "runtime and tiered-dispatch re-resolution/materialization",
        "50-5 should reduce repeated resolution and make this the single materialized turn context",
    ),
    (
        "Task / Project",
        "src/dan/server/concierge/models.py",
        "user-facing unit of work and persisted project truth",
        "ConciergeTask and Session ids mirror some of the same concepts",
        "keep user-facing truth here; demote queue/trace layers to implementation detail",
    ),
    (
        "ConciergeTask",
        "src/dan/server/concierge/task_registry.py",
        "queue lease and concierge task lifecycle tracker",
        "dispatcher queues and models.Task overlap on work identity",
        "50-5 must decide whether this is the canonical queue lease or only a state tracker",
    ),
    (
        "Session / SessionManager",
        "src/dan/server/concierge/session.py",
        "runtime execution trace and child-session tree",
        "task ids and user-facing work state leak into prompt/log reasoning",
        "keep runtime trace ownership here; do not let Session become user-facing truth",
    ),
    (
        "Prompt carriers",
        "routers/chat.py, chat_manager.py, messages.py, tier_executors.py",
        "future typed PromptEnvelope / TurnExecutionEnvelope contract",
        "prompt_context, extra_system_instructions, metadata bag, attachment_prompt_context",
        "46-6 should retire free-form carriers and duplicate metadata strings",
    ),
)

DUPLICATION_ROWS = (
    (
        "run-readiness guard",
        "RunManager and workflow_guards",
        "routers/runs.py, capabilities/runs.py, gateway/router.py, chat surfaces",
        "50-2 moves the guard to RunManager and deletes caller-side copies",
    ),
    (
        "run launch and relay hookup",
        "RunManager launch seam",
        "run_relay.py wrappers, routes, gateway, startup, chat local",
        "50-2 consolidates one launch helper and deletes wrapper glue in the same patch",
    ),
    (
        "workflow apply-save path",
        "shared apply-ready helper beside workflow_guards.py",
        "routers/graphs.py, capability_handlers.py, chat_manager.py",
        "50-2 routes all apply-save paths through one helper",
    ),
    (
        "attachment prompt context",
        "single prompt slot in the 46-6 envelope",
        "routers/chat.py currently passes it into both prompt_context and extra_system_instructions",
        "50-3 removes the double-pass before 46-6 retires the old carriers",
    ),
    (
        "prompt assembly",
        "tier_executors.py shared prompt builder plus PromptEnvelope",
        "chat_manager.py, messages.py, raw metadata strings",
        "46-6 removes ad-hoc concatenation and duplicate metadata extraction",
    ),
    (
        "queue authority",
        "one concierge queue model",
        "dispatcher queues plus tiered_dispatch background queue/cap",
        "50-5 chooses one queue lease owner and demotes the other",
    ),
    (
        "work-state identity",
        "models.Task / Project for user truth",
        "ConciergeTask and Session partial mirrors",
        "50-5 documents which ids are canonical in logs, prompts, and follow-up resolution",
    ),
    (
        "legacy worker bridge",
        "Worker core plus DAN adapters",
        "compatibility-only adapters plus the remaining projection seams",
        "50-7 landed the default-path split; 46-7 can keep shrinking compatibility-only seams if external bundle extraction becomes active",
    ),
)

VALIDATION_BASKETS = (
    (
        "50-1",
        "`python3 scripts/inventory.py --write docs/key-scripts.md` and `python3 -m py_compile scripts/inventory.py`",
    ),
    (
        "50-2",
        "`tests/test_server/test_run_manager.py`, `tests/test_server/test_run_manager_reflection.py`, `tests/test_server/test_capability_run_lifecycle.py`, `tests/test_concierge/test_live_data.py`, `tests/test_post_tool_followup_recovery.py`, `tests/test_concierge/test_chat_router.py`",
    ),
    (
        "50-3",
        "`tests/test_server/test_chat_manager.py`, `tests/test_concierge/test_tiered_dispatch.py`, `tests/test_concierge/test_chat_router.py`",
    ),
    (
        "50-4",
        "`tests/test_server/test_run_manager.py`, `tests/test_server/test_run_manager_reflection.py`",
    ),
    (
        "50-5",
        "`tests/test_concierge/test_tiered_dispatch.py`, `tests/test_concierge/test_scheduler.py`, `tests/test_concierge/test_session_manager.py`, `tests/test_concierge/test_progress_ux.py`",
    ),
    (
        "46-6",
        "`tests/test_concierge/test_tiered_dispatch.py`, `tests/test_concierge/test_fast_commands.py`, `tests/test_worker/test_executor.py`",
    ),
    (
        "46-7",
        "`tests/test_executor_defaults.py`, `tests/test_worker/test_workflow.py`",
    ),
    (
        "50-8",
        "`npm --prefix editor run test` plus `npm --prefix editor run build:verify`",
    ),
)

PERFORMANCE_GUARDS = (
    (
        "workflow run start latency",
        "wall-clock from `start_run` call to first engine event",
        "<= 20% regression vs. pre-split baseline",
    ),
    (
        "scheduler fire-time execution latency",
        "cron fire to first node dispatch",
        "<= 20% regression vs. baseline",
    ),
    (
        "chat time-to-first-token",
        "foreground ask/agent/plan turn to first stream token",
        "<= 20% regression and no ack-only regressions",
    ),
    (
        "frontend bundle budgets",
        "`npm --prefix editor run build:verify`",
        "stay within current bundle budgets",
    ),
)

PRUNING_ROWS = (
    (
        "caller-owned run guards and launch glue",
        "50-2",
        "delete route, capability, gateway, and chat-local wrappers once RunManager owns guard and launch",
    ),
    (
        "attachment prompt double-pass in routers/chat.py",
        "50-3",
        "remove the extra_system_instructions duplicate once the single-slot path is wired",
    ),
    (
        "dispatch-policy, schedule rewrite, and progress UX code in runtime/__init__.py",
        "50-5",
        "delete embedded special cases when extracted neighbors own them",
    ),
    (
        "free-form prompt carriers and duplicated metadata strings",
        "46-6",
        "retire prompt_context, extra_system_instructions, and metadata prompt copies in the same patch series",
    ),
    (
        "remaining Worker/legacy projection glue and compatibility-only bridge registrations",
        "46-7",
        "keep shrinking compatibility-only adapter surfaces once an external bundle consumer justifies the extra extraction work",
    ),
)

SPLIT_THRESHOLDS = (
    ("backend control-plane files", "soft 1500", "hard 2200"),
    ("runtime and scheduler sinks", "soft 1800", "hard 2600"),
    ("routers and gateways", "soft 900", "hard 1500"),
    ("frontend components", "soft 1200", "hard 1800"),
    ("frontend stores", "soft 1000", "hard 1600"),
)


def md(text: str) -> str:
    return text.replace("|", "\\|")


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def count_lines(path: Path) -> int:
    return sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore"))


def iter_files(roots: tuple[Path, ...], suffixes: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        abs_root = REPO_ROOT / root
        if not abs_root.exists():
            continue
        for path in abs_root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in suffixes:
                continue
            parts = {part for part in path.parts}
            if "__tests__" in parts or "tests" in parts:
                continue
            files.append(path)
    return sorted(set(files))


def top_files(roots: tuple[Path, ...], suffixes: tuple[str, ...], top_n: int) -> list[tuple[str, int]]:
    ranked = [(rel(path), count_lines(path)) for path in iter_files(roots, suffixes)]
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked[:top_n]


def line_count_lookup() -> dict[str, int]:
    lookup: dict[str, int] = {}
    for path in iter_files(BACKEND_ROOTS, BACKEND_SUFFIXES) + iter_files(FRONTEND_ROOTS, FRONTEND_SUFFIXES):
        lookup[rel(path)] = count_lines(path)
    for script in (REPO_ROOT / "src/dan/executor_defaults.py",):
        if script.exists():
            lookup[rel(script)] = count_lines(script)
    return lookup


def collect_prompt_usage() -> list[tuple[str, int, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for path in iter_files(BACKEND_ROOTS, BACKEND_SUFFIXES):
        text = read_text(path)
        for token in PROMPT_TOKENS:
            count = text.count(token)
            if count:
                counts[rel(path)][token] = count
    rows = []
    for file_path, token_counts in counts.items():
        prompt_count = token_counts.get("prompt_context", 0)
        extra_count = token_counts.get("extra_system_instructions", 0)
        rows.append((file_path, prompt_count, extra_count))
    rows.sort(key=lambda item: (-(item[1] + item[2]), -item[2], -item[1], item[0]))
    return rows


def prompt_usage_summary(rows: list[tuple[str, int, int]]) -> tuple[int, int, int, int]:
    prompt_total = sum(item[1] for item in rows)
    extra_total = sum(item[2] for item in rows)
    prompt_files = sum(1 for item in rows if item[1])
    extra_files = sum(1 for item in rows if item[2])
    return prompt_total, prompt_files, extra_total, extra_files


def scan_import_boundary_violations() -> list[tuple[str, int, str, str]]:
    violations: list[tuple[str, int, str, str]] = []
    worker_root = REPO_ROOT / "src/dan/worker"
    for path in sorted(worker_root.rglob("*.py")):
        for line_no, raw_line in enumerate(read_text(path).splitlines(), start=1):
            for rule_name, pattern in IMPORT_RULES:
                if pattern.search(raw_line):
                    violations.append((rel(path), line_no, rule_name, raw_line.strip()))
                    break
    return violations


def scan_legacy_adapter_inventory() -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    defaults_path = REPO_ROOT / "src/dan/executor_defaults.py"
    adapter_nodes: list[tuple[str, int]] = []
    if defaults_path.exists():
        for line_no, raw_line in enumerate(read_text(defaults_path).splitlines(), start=1):
            match = re.search(r'\("([^"]+)",\s*legacy_worker_adapter\)', raw_line)
            if match:
                adapter_nodes.append((match.group(1), line_no))
    registration_callers: list[tuple[str, int]] = []
    for path in iter_files(BACKEND_ROOTS, BACKEND_SUFFIXES):
        for line_no, raw_line in enumerate(read_text(path).splitlines(), start=1):
            if "register_default_executors(" in raw_line:
                registration_callers.append((rel(path), line_no))
    return adapter_nodes, registration_callers


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(md(cell) for cell in row) + " |")
    return "\n".join(lines)


def build_markdown() -> str:
    counts = line_count_lookup()
    backend_top = top_files(BACKEND_ROOTS, BACKEND_SUFFIXES, BACKEND_TOP_N)
    frontend_top = top_files(FRONTEND_ROOTS, FRONTEND_SUFFIXES, FRONTEND_TOP_N)
    prompt_rows = collect_prompt_usage()
    prompt_total, prompt_files, extra_total, extra_files = prompt_usage_summary(prompt_rows)
    import_violations = scan_import_boundary_violations()
    adapter_nodes, registration_callers = scan_legacy_adapter_inventory()

    lines: list[str] = []
    lines.append("# Key Scripts Inventory")
    lines.append("")
    lines.append("Generated from live repo scans plus checked-in annotations in `scripts/inventory.py`.")
    lines.append("Refresh with `python3 scripts/inventory.py --write docs/key-scripts.md`.")
    lines.append("")
    lines.append("## Structural Hotspots")
    lines.append("")
    lines.append("### Backend")
    lines.append(
        render_table(
            ["File", "Lines"],
            [[path, str(line_count)] for path, line_count in backend_top],
        )
    )
    lines.append("")
    lines.append("### Frontend")
    lines.append(
        render_table(
            ["File", "Lines"],
            [[path, str(line_count)] for path, line_count in frontend_top],
        )
    )
    lines.append("")
    lines.append("## Key Script Ownership Map")
    lines.append("")
    lines.append(
        render_table(
            ["File", "Lines", "Role", "Authoritative Owner", "Current Overreach", "Target Boundary", "Follow-on"],
            [
                [
                    item.path,
                    str(counts.get(item.path, 0)),
                    item.role,
                    item.authoritative_owner,
                    item.current_overreach,
                    item.target_boundary,
                    item.follow_on,
                ]
                for item in KEY_SCRIPTS
            ],
        )
    )
    lines.append("")
    lines.append("## Authoritative State Flow")
    lines.append("")
    lines.append(
        render_table(
            ["Concept", "Current Truth", "What It Owns", "Transitional Neighbors", "Decision / Next Move"],
            [list(row) for row in STATE_FLOW_ROWS],
        )
    )
    lines.append("")
    lines.append("## Duplication / Deletion Matrix")
    lines.append("")
    lines.append(
        render_table(
            ["Concept", "Authoritative Owner", "Transitional Owners", "Delete / Demote Path"],
            [list(row) for row in DUPLICATION_ROWS],
        )
    )
    lines.append("")
    lines.append("## Prompt Carrier Inventory")
    lines.append("")
    lines.append(
        f"- `prompt_context`: {prompt_total} occurrences across {prompt_files} files"
    )
    lines.append(
        f"- `extra_system_instructions`: {extra_total} occurrences across {extra_files} files"
    )
    lines.append("- 46-6 target: retire the free-form carriers in favor of typed prompt slots and drive `extra_system_instructions` down to <=8 uses.")
    lines.append("")
    lines.append(
        render_table(
            ["File", "prompt_context", "extra_system_instructions", "Total"],
            [
                [path, str(prompt_count), str(extra_count), str(prompt_count + extra_count)]
                for path, prompt_count, extra_count in prompt_rows[:PROMPT_TOP_N]
            ],
        )
    )
    lines.append("")
    lines.append("### Concierge Prompt Pipeline")
    lines.append("")
    lines.append("- `src/dan/server/routers/chat.py` builds attachment context and currently duplicates it into both prompt carriers.")
    lines.append("- `src/dan/server/concierge/dispatcher.py` binds ingress work to queue/task state.")
    lines.append("- `src/dan/server/concierge/runtime/__init__.py` decides dispatch mode and orchestration handoff.")
    lines.append("- `src/dan/server/concierge/tiered_dispatch.py` materializes turn context and background or foreground execution.")
    lines.append("- `src/dan/server/concierge/tier_executors.py` assembles prompt fragments, stage overlays, and child handoff text.")
    lines.append("- `src/dan/server/chat_manager.py` and `src/dan/agent_runtime/messages.py` still carry the untyped prompt carriers into final message assembly.")
    lines.append("- `src/dan/worker/executor.py` remains the workflow-node compute boundary and should consume the normalized minimum contract from 46-6, not invent a second prompt shape.")
    lines.append("")
    lines.append("## Worker Import Boundary Violations")
    lines.append("")
    lines.append(
        render_table(
            ["File", "Line", "Forbidden Import", "Import"],
            [[path, str(line_no), rule_name, raw_line] for path, line_no, rule_name, raw_line in import_violations],
        )
    )
    lines.append("")
    lines.append("## Legacy Adapter Inventory")
    lines.append("")
    lines.append(f"- Default-path bridged node families: {len(adapter_nodes)}")
    lines.append(
        render_table(
            ["Node Type", "executor_defaults.py Line"],
            [[node_type, str(line_no)] for node_type, line_no in adapter_nodes],
        )
    )
    lines.append("")
    lines.append("### Default Registration Callers")
    lines.append("")
    lines.append(
        render_table(
            ["Caller", "Line"],
            [[path, str(line_no)] for path, line_no in registration_callers],
        )
    )
    lines.append("")
    lines.append("## Split Guardrails")
    lines.append("")
    lines.append(
        render_table(
            ["Category", "Soft Threshold", "Hard Threshold"],
            [list(row) for row in SPLIT_THRESHOLDS],
        )
    )
    lines.append("")
    lines.append("- Real subtraction means the old owner loses responsibility in the same patch, not just that code moved to a neighbor.")
    lines.append("- Wrappers and facades do not count as success unless they become thin import or registration shims.")
    lines.append("- Oversize alone is not enough to justify a split; mixed ownership, duplicated invariants, or queue/prompt ambiguity must also be present.")
    lines.append("")
    lines.append("## Validation Basket")
    lines.append("")
    lines.append(
        render_table(
            ["Plan", "Minimum Focused Validation"],
            [list(row) for row in VALIDATION_BASKETS],
        )
    )
    lines.append("")
    lines.append("## Performance Guardrails")
    lines.append("")
    lines.append(
        render_table(
            ["Metric", "How To Measure", "Pass Threshold"],
            [list(row) for row in PERFORMANCE_GUARDS],
        )
    )
    lines.append("")
    lines.append("## Pruning Candidates")
    lines.append("")
    lines.append(
        render_table(
            ["Delete Candidate", "Delete After", "Why"],
            [list(row) for row in PRUNING_ROWS],
        )
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        nargs="?",
        const=str(DEFAULT_OUTPUT),
        help="Write the generated markdown to the given repo-relative path.",
    )
    args = parser.parse_args()

    output = build_markdown()
    if args.write:
        target = (REPO_ROOT / args.write).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
