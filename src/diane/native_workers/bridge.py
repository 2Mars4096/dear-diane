"""Run-scoped file RPC so CLI leads can use Diane's enabled team via shell tools.

The client is standalone stdlib Python: no installed Diane package is required in
an agent's shell. The server executes only the fixed worker actions.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time
from uuid import uuid4


async def serve(directory: Path, team):
    from diane.tools.native_worker import native_worker
    from .service import current_team
    token = current_team.set(team)
    try:
        while True:
            for request in directory.glob("*.request"):
                response = request.with_suffix(".response")
                try:
                    if request.stat().st_size > 1024 * 1024:
                        raise ValueError("Team request exceeds 1 MB")
                    payload = json.loads(request.read_text())
                    result = await native_worker(**payload)
                    # Profiles contain server-side continuation details, not tool output.
                    def public(value):
                        if isinstance(value, dict):
                            return {k: public(v) for k, v in value.items() if k not in {"profile", "source_files", "inherited_policy", "history"}}
                        if isinstance(value, list):
                            return [public(v) for v in value]
                        return value
                    output = {"ok": True, "result": public(result)}
                except Exception as exc:
                    output = {"ok": False, "error": str(exc)}
                temporary = response.with_suffix(".tmp")
                temporary.write_text(json.dumps(output))
                temporary.replace(response)
                request.unlink(missing_ok=True)
            await asyncio.sleep(.1)
    finally:
        current_team.reset(token)


def main():
    parser = argparse.ArgumentParser(description="Control the enabled team for this Dear Diane lead run")
    parser.add_argument("--queue", required=True)
    parser.add_argument("action", choices=["start", "status", "stop", "resume", "apply", "review"])
    parser.add_argument("--shared-workspace", action="store_true")
    parser.add_argument("--profile-id", default="")
    parser.add_argument("--backend", default="")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--worker-id", default="")
    parser.add_argument("--scope", default="")
    parser.add_argument("--acceptance", action="append", default=[], help="Repeat for each acceptance criterion")
    parser.add_argument("--attempt", type=int, default=-1)
    parser.add_argument("--decision", choices=["accepted", "revise", "blocked"], default="")
    parser.add_argument("--evidence", action="append", default=[], help="Repeat for each inspected check or artifact")
    args = parser.parse_args()
    directory = Path(args.queue)
    if not directory.is_dir():
        parser.error("This lead run has ended; its team bridge is closed")
    request = directory / f"{uuid4().hex}.request"
    temporary = request.with_suffix(".tmp")
    payload = {"action": args.action, "profile_id": args.profile_id, "backend": args.backend, "prompt": args.prompt, "worker_id": args.worker_id,
               "isolate": not args.shared_workspace, "attempt": args.attempt, "decision": args.decision, "evidence": args.evidence}
    if args.scope or args.acceptance:
        payload["contract"] = {"scope": args.scope, "acceptance": args.acceptance}
    temporary.write_text(json.dumps(payload))
    temporary.replace(request)
    response = request.with_suffix(".response")
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if response.exists():
            output = json.loads(response.read_text())
            response.unlink(missing_ok=True)
            print(json.dumps(output))
            return 0 if output.get("ok") else 1
        if not directory.exists():
            break
        time.sleep(.1)
    print(json.dumps({"ok": False, "error": "Team bridge closed or timed out; inspect status before retrying a start"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
