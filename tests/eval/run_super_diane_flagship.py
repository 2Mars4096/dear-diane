"""Run a frozen flagship case through the local Diane control plane.

This runner is dry-run by default. Live execution requires both ``--execute``
and the explicit provider-disclosure acknowledgement because the local server
can forward the prompt, relevant repository/tool context, and generated
artifact context to its configured model provider.

The runner injects the frozen steering message after the first persisted
``artifact_changed`` event, waits for terminal settlement plus queue
acknowledgement, and exports the durable Agent event stream as JSONL for the
flagship acceptance gate.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from tests.eval.super_diane_flagship_acceptance import (
    FlagshipAcceptanceCase,
    get_flagship_case,
)

DISCLOSURE_ACK = (
    "I approve sending the premium-site and RTS prompts, relevant repository "
    "context, tool outputs, and generated artifact context to "
    "https://api.moonshot.ai/v1 using the configured Diane credential."
)
_TERMINAL_TYPES = {"completed", "failed", "blocked", "stopped"}
_TERMINAL_SOURCE_EVENT_TYPES = {
    "run.log.completed",
    "run.log.failed",
    "chat_v2.background_run.completed",
    "chat_v2.background_run.failed",
    "chat_v2.background_run.blocked",
    "chat_v2.background_run.stopped",
}


def is_terminal_run_event(event: Mapping[str, Any]) -> bool:
    """Distinguish run settlement from recoverable worker-level failures."""

    event_type = str(event.get("type") or event.get("event") or "")
    if event_type not in _TERMINAL_TYPES:
        return False
    source_event_type = str(event.get("source_event_type") or "").strip()
    # Synthetic tests and older control-plane payloads may not name a source.
    # When a source is present, require an actual run/background-run terminal
    # event so model timeouts and contract-repair transitions do not detach the
    # live runner while Diane is still recovering.
    return not source_event_type or source_event_type in _TERMINAL_SOURCE_EVENT_TYPES


def build_admission_payload(
    case: FlagshipAcceptanceCase,
    workspace: Path,
    *,
    run_nonce: str,
) -> dict[str, Any]:
    root = str(workspace.resolve())
    thread_id = f"flagship-{case.case_id}-{run_nonce}"
    surface_id = f"flagship-{run_nonce}"
    return {
        "turn": {
            "id": f"turn-{run_nonce}",
            "text": case.prompt,
            "workspace_root": root,
            "workspace_id": f"workspace-{run_nonce}",
            "surface_type": "editor",
            "surface_id": surface_id,
            "surface": f"editor:{surface_id}",
            "session_id": thread_id,
            "thread_id": thread_id,
            "privacy_scope": "private",
            "capabilities": ["workspace_write", "browser_control"],
            "metadata": {
                "flagship_case_id": case.case_id,
                "acceptance_required": True,
                "required_test_ids": list(case.required_test_ids),
                "provider_disclosure_scope": {
                    "provider_base_url": "https://api.moonshot.ai/v1",
                    "prompt": True,
                    "repository_context": True,
                    "tool_outputs": True,
                    "generated_artifact_context": True,
                },
            },
        },
        "background": True,
        "execute": {
            "backend": "super_dan",
            "surface_profile": "super_tui",
            "background": True,
            "auto_execute_continuations": True,
            "max_promoted_continuations": 16,
            "profile_policy": {
                "surface_profile": "super_tui",
                "acceptance_case": case.case_id,
            },
            "mutation_policy": {
                "mode": "workspace_mutation",
                "permission": "workspace_mutation",
                "operator_mutation_policy": "required",
                "source": "flagship_acceptance",
            },
            "tool_policy": {
                "capability_packs": ["browser_control"],
            },
            "metadata": {
                "flagship_case_id": case.case_id,
                "acceptance_required": True,
            },
        },
        "max_parallel_runs": 1,
    }


def should_inject_steering(
    events: Sequence[Mapping[str, Any]],
    *,
    already_injected: bool,
) -> bool:
    if already_injected:
        return False
    if any(is_terminal_run_event(event) for event in events):
        return False
    for event in events:
        event_type = str(event.get("type") or event.get("event") or "")
        artifact_refs = event.get("artifact_refs")
        if (
            event_type == "artifact_changed"
            and isinstance(artifact_refs, Sequence)
            and not isinstance(artifact_refs, (str, bytes))
            and bool(artifact_refs)
        ):
            return True
    return False


def build_steering_command(
    case: FlagshipAcceptanceCase,
    *,
    run_id: str,
    run_nonce: str,
) -> dict[str, Any]:
    return {
        "command": "append_followup",
        "run_id": run_id,
        "surface_turn_id": f"steer-{run_nonce}",
        "idempotency_key": f"flagship-steer-{case.case_id}-{run_nonce}",
        "payload": {
            "text": case.steering_message,
            "original_request": case.prompt,
            "satisfaction_gap": "Apply the frozen mid-run direction before final validation.",
            "surface_context": {
                "flagship_case_id": case.case_id,
                "steering_trigger": case.steering_trigger,
            },
        },
    }


def run_live_case(
    case: FlagshipAcceptanceCase,
    workspace: Path,
    *,
    server_url: str,
    timeout_seconds: float,
    poll_seconds: float,
    evidence_dir: Path,
) -> tuple[int, Path]:
    nonce = uuid.uuid4().hex[:12]
    admitted = _request_json(
        server_url,
        "/api/v2/agent-runs/admit",
        method="POST",
        payload=build_admission_payload(case, workspace, run_nonce=nonce),
    )
    admission = (
        admitted.get("admission")
        if isinstance(admitted.get("admission"), Mapping)
        else {}
    )
    run_id = str(admission.get("run_id") or "")
    if not run_id:
        raise RuntimeError(f"Control plane did not return a run id: {admitted}")

    deadline = time.monotonic() + timeout_seconds
    injected = False
    events: list[Mapping[str, Any]] = []
    while time.monotonic() < deadline:
        response = _request_json(
            server_url,
            f"/api/v2/agent-runs/{urllib.parse.quote(run_id)}/events",
        )
        raw_events = response.get("events")
        events = (
            [event for event in raw_events if isinstance(event, Mapping)]
            if isinstance(raw_events, Sequence)
            else []
        )
        if should_inject_steering(events, already_injected=injected):
            _request_json(
                server_url,
                f"/api/v2/agent-runs/{urllib.parse.quote(run_id)}/commands",
                method="POST",
                payload=build_steering_command(
                    case,
                    run_id=run_id,
                    run_nonce=nonce,
                ),
            )
            injected = True

        terminal_index = next(
            (
                index
                for index, event in enumerate(events)
                if is_terminal_run_event(event)
            ),
            None,
        )
        event_types = [str(event.get("type") or "") for event in events]
        steering_settled = "queue_item_completed" in event_types
        if terminal_index is not None and (steering_settled or not injected):
            break
        time.sleep(poll_seconds)
    else:
        raise TimeoutError(
            f"Diane flagship run {run_id} did not settle within "
            f"{timeout_seconds:.0f}s"
        )

    evidence_dir.mkdir(parents=True, exist_ok=True)
    event_log = evidence_dir / "agent-events.jsonl"
    event_log.write_text(
        "\n".join(json.dumps(dict(event), sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    final_type = next(
        (
            str(event.get("type") or "")
            for event in reversed(events)
            if is_terminal_run_event(event)
        ),
        "unknown",
    )
    return (0 if final_type == "completed" and injected else 1), event_log


def _request_json(
    server_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    url = server_url.rstrip("/") + path
    data = json.dumps(dict(payload)).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{method} {url} failed with HTTP {exc.code}: {body}"
        ) from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{method} {url} returned a non-object JSON payload")
    return parsed


def _assert_loopback_server(server_url: str) -> None:
    parsed = urllib.parse.urlparse(server_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("server URL must use http or https")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("flagship runner only talks to a loopback Dear Diane server")


def _assert_empty_workspace(workspace: Path) -> None:
    if workspace.exists() and not workspace.is_dir():
        raise ValueError("flagship workspace must be a directory")
    if workspace.is_dir() and any(workspace.iterdir()):
        raise ValueError(
            "flagship workspace must be empty so unrelated files are not sent "
            "into the live provider context"
        )


def _default_evidence_dir(case: FlagshipAcceptanceCase) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("output/super-dan-flagship") / case.case_id / stamp


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--server-url", default="http://127.0.0.1:8000")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approve-provider-disclosure", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=1_800)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)

    case = get_flagship_case(args.case_id)
    _assert_loopback_server(args.server_url)
    plan = {
        "case_id": case.case_id,
        "workspace": str(args.workspace.resolve()),
        "server_url": args.server_url,
        "prompt": case.prompt,
        "steering_trigger": case.steering_trigger,
        "steering_message": case.steering_message,
        "required_files": list(case.required_files),
        "required_test_ids": list(case.required_test_ids),
        "rubric_template": {
            "reviewer": "independent reviewer identifier",
            "evidence_refs": [
                f"{case.case_id}-desktop.png",
                f"{case.case_id}-phone.png",
            ],
            "scores": {dimension: None for dimension in case.rubric_dimensions},
        },
        "provider_disclosure_acknowledgement": DISCLOSURE_ACK,
        "mode": "execute" if args.execute else "dry-run",
    }
    if not args.execute:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    if not args.approve_provider_disclosure:
        parser.error(
            "--execute requires --approve-provider-disclosure after the user has "
            f"explicitly stated: {DISCLOSURE_ACK}"
        )
    _assert_empty_workspace(args.workspace)
    args.workspace.mkdir(parents=True, exist_ok=True)
    evidence_dir = args.evidence_dir or _default_evidence_dir(case)
    status, event_log = run_live_case(
        case,
        args.workspace,
        server_url=args.server_url,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
        evidence_dir=evidence_dir,
    )
    print(
        json.dumps(
            {
                **plan,
                "event_log": str(event_log.resolve()),
                "status": "completed" if status == 0 else "failed",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
