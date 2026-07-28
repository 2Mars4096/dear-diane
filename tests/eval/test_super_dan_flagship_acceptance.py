from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path

import pytest

from tests.eval import super_dan_flagship_acceptance as acceptance
from tests.eval import run_super_dan_flagship as live_runner

_MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAusB9Wl2V9sAAAAASUVORK5CYII="
)


def test_flagship_cases_freeze_original_artifact_and_mid_run_steering_contracts() -> (
    None
):
    cases = acceptance.flagship_cases()

    assert {case.case_id for case in cases} == {
        "flagship-premium-site",
        "flagship-browser-rts",
    }
    assert all(case.steering_message for case in cases)
    assert all(
        "after the first artifact_changed" in case.steering_trigger for case in cases
    )
    assert all("do not depend" in case.prompt.lower() for case in cases)
    assert all("original" in case.prompt.lower() for case in cases)
    assert all(case.min_static_pass_rate == 1.0 for case in cases)
    assert all(case.min_browser_pass_rate == 1.0 for case in cases)


def test_live_runner_payload_is_super_dan_workspace_scoped_and_browser_capable(
    tmp_path: Path,
) -> None:
    case = acceptance.get_flagship_case("flagship-browser-rts")

    payload = live_runner.build_admission_payload(
        case,
        tmp_path,
        run_nonce="fixed",
    )

    assert payload["turn"]["workspace_root"] == str(tmp_path.resolve())
    assert payload["turn"]["capabilities"] == ["workspace_write", "browser_control"]
    assert payload["execute"]["backend"] == "super_dan"
    assert payload["execute"]["surface_profile"] == "super_tui"
    assert payload["execute"]["tool_policy"]["capability_packs"] == ["browser_control"]
    assert payload["execute"]["mutation_policy"]["permission"] == "workspace_mutation"


def test_live_runner_waits_for_artifact_before_steering_and_never_steers_terminal() -> (
    None
):
    assert live_runner.should_inject_steering(
        [
            {"type": "accepted"},
            {
                "type": "artifact_changed",
                "artifact_refs": [{"path": "index.html"}],
            },
        ],
        already_injected=False,
    )
    assert not live_runner.should_inject_steering(
        [{"type": "artifact_changed", "artifact_refs": []}],
        already_injected=False,
    )
    assert not live_runner.should_inject_steering(
        [
            {"type": "accepted"},
            {"type": "completed"},
            {
                "type": "artifact_changed",
                "artifact_refs": [{"path": "index.html"}],
            },
        ],
        already_injected=False,
    )
    assert not live_runner.should_inject_steering(
        [
            {
                "type": "artifact_changed",
                "artifact_refs": [{"path": "index.html"}],
            },
            {"type": "completed"},
        ],
        already_injected=False,
    )
    assert not live_runner.should_inject_steering(
        [{"type": "artifact_changed", "artifact_refs": [{"path": "index.html"}]}],
        already_injected=True,
    )
    assert live_runner.should_inject_steering(
        [
            {
                "type": "failed",
                "source_event_type": "model.timeout",
            },
            {
                "type": "blocked",
                "source_event_type": "contract.validation.completed",
            },
            {
                "type": "artifact_changed",
                "artifact_refs": [{"path": "index.html"}],
            },
        ],
        already_injected=False,
    )


def test_live_runner_requires_an_empty_isolated_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "case"
    live_runner._assert_empty_workspace(workspace)
    workspace.mkdir()
    live_runner._assert_empty_workspace(workspace)
    (workspace / "unrelated.txt").write_text("private context", encoding="utf-8")

    with pytest.raises(ValueError, match="must be empty"):
        live_runner._assert_empty_workspace(workspace)


def test_live_runner_exports_a_trace_that_passes_the_steering_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    case = acceptance.get_flagship_case("flagship-premium-site")
    queue_id = "queue-live-steer"
    poll_count = 0
    command_payloads: list[dict[str, object]] = []

    def fake_request(
        _server_url: str,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        nonlocal poll_count
        if path == "/api/v2/agent-runs/admit":
            assert method == "POST"
            return {"admission": {"run_id": "run-flagship"}}
        if path.endswith("/commands"):
            assert method == "POST"
            assert payload is not None
            command_payloads.append(payload)
            return {"event": {"type": "queue_item_added"}}
        if path.endswith("/events"):
            poll_count += 1
            if poll_count == 1:
                return {"events": [{"type": "accepted"}]}
            if poll_count == 2:
                return {
                    "events": [
                        {"type": "accepted"},
                        {
                            "type": "artifact_changed",
                            "artifact_refs": [{"path": "index.html"}],
                        },
                    ]
                }
            return {
                "events": [
                    {"type": "accepted"},
                    {
                        "type": "artifact_changed",
                        "artifact_refs": [{"path": "index.html"}],
                    },
                    {
                        "type": "queue_item_added",
                        "payload": {
                            "queue_item_id": queue_id,
                            "text": case.steering_message,
                        },
                    },
                    {
                        "type": "queue_item_injected",
                        "payload": {
                            "queue_item_id": queue_id,
                            "checkpoint": "tool.completed:file_write",
                        },
                    },
                    {"type": "completed"},
                    {
                        "type": "queue_item_completed",
                        "payload": {"queue_item_id": queue_id},
                    },
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(live_runner, "_request_json", fake_request)
    status, event_log = live_runner.run_live_case(
        case,
        tmp_path / "workspace",
        server_url="http://127.0.0.1:8000",
        timeout_seconds=2,
        poll_seconds=0,
        evidence_dir=tmp_path / "evidence",
    )

    assert status == 0
    assert len(command_payloads) == 1
    assert command_payloads[0]["payload"]["text"] == case.steering_message
    assert acceptance.validate_steering_trace(case, event_log).passed is True


def test_premium_site_static_contract_accepts_instrumented_original_fixture(
    tmp_path: Path,
) -> None:
    _write_premium_site_fixture(tmp_path)
    case = acceptance.get_flagship_case("flagship-premium-site")

    section = acceptance.validate_static_artifact(case, tmp_path)

    assert section.passed is True
    assert section.pass_rate == 1.0


def test_rts_static_contract_accepts_instrumented_playable_fixture(
    tmp_path: Path,
) -> None:
    _write_rts_fixture(tmp_path)
    case = acceptance.get_flagship_case("flagship-browser-rts")

    section = acceptance.validate_static_artifact(case, tmp_path)

    assert section.passed is True
    assert section.pass_rate == 1.0


def test_static_contract_rejects_placeholders_remote_assets_and_copied_brand(
    tmp_path: Path,
) -> None:
    (tmp_path / "index.html").write_text(
        '<main><h1>Apple Red Alert clone</h1><script src="https://cdn.example/app.js"></script></main>',
        encoding="utf-8",
    )
    (tmp_path / "styles.css").write_text("body {}", encoding="utf-8")
    (tmp_path / "app.js").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("placeholder", encoding="utf-8")
    case = acceptance.get_flagship_case("flagship-premium-site")

    section = acceptance.validate_static_artifact(case, tmp_path)
    failed_names = {check.name for check in section.checks if not check.passed}

    assert section.passed is False
    assert "local-only-assets" in failed_names
    assert "originality-no-output-reference:apple" in failed_names
    assert "originality-no-output-reference:red-alert" in failed_names


def test_static_contract_allows_data_svg_namespace_and_system_font_token(
    tmp_path: Path,
) -> None:
    _write_premium_site_fixture(tmp_path)
    html_path = tmp_path / "index.html"
    html_path.write_text(
        html_path.read_text(encoding="utf-8").replace(
            'href="data:,"',
            "href=\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3C/svg%3E\"",
        ),
        encoding="utf-8",
    )
    css_path = tmp_path / "styles.css"
    css_path.write_text(
        css_path.read_text(encoding="utf-8")
        + "\nbody{font-family:-apple-system,BlinkMacSystemFont,sans-serif}\n",
        encoding="utf-8",
    )

    section = acceptance.validate_static_artifact(
        acceptance.get_flagship_case("flagship-premium-site"),
        tmp_path,
    )

    assert section.passed is True


def test_visual_rubric_requires_independent_reviewer_two_viewports_and_floor(
    tmp_path: Path,
) -> None:
    case = acceptance.get_flagship_case("flagship-premium-site")
    (tmp_path / "site-desktop.png").write_bytes(_MINIMAL_PNG)
    (tmp_path / "site-phone.png").write_bytes(_MINIMAL_PNG)
    strong = acceptance.validate_rubric(
        case,
        {
            "reviewer": "independent-review-pass-1",
            "evidence_refs": ["site-desktop.png", "site-phone.png"],
            "scores": {dimension: 4.2 for dimension in case.rubric_dimensions},
        },
        evidence_root=tmp_path,
    )
    weak = acceptance.validate_rubric(
        case,
        {
            "reviewer": "Super DAN",
            "evidence_refs": ["site-desktop.png"],
            "scores": {
                **{dimension: 4.2 for dimension in case.rubric_dimensions},
                "originality": 2.0,
            },
        },
        evidence_root=tmp_path,
    )
    (tmp_path / "fake-desktop.png").write_text("not an image", encoding="utf-8")
    (tmp_path / "fake-phone.png").write_text("not an image", encoding="utf-8")
    fabricated = acceptance.validate_rubric(
        case,
        {
            "reviewer": "independent-review-pass-2",
            "evidence_refs": ["fake-desktop.png", "fake-phone.png"],
            "scores": {dimension: 4.2 for dimension in case.rubric_dimensions},
        },
        evidence_root=tmp_path,
    )

    assert strong.passed is True
    assert weak.passed is False
    assert fabricated.passed is False
    assert any(
        check.name == "screenshot-evidence-files" and not check.passed
        for check in fabricated.checks
    )


def test_steering_trace_requires_post_artifact_checkpoint_delivery_before_completion(
    tmp_path: Path,
) -> None:
    case = acceptance.get_flagship_case("flagship-premium-site")
    queue_id = "queue-steer-1"
    rows = [
        {"type": "accepted"},
        {"type": "artifact_changed", "artifact_refs": [{"path": "index.html"}]},
        {
            "type": "queue_item_added",
            "payload": {
                "queue_item_id": queue_id,
                "text": case.steering_message,
            },
        },
        {
            "type": "queue_item_injected",
            "payload": {"queue_item_id": queue_id, "checkpoint": "model.round.2"},
        },
        {"type": "queue_item_completed", "payload": {"queue_item_id": queue_id}},
        {"type": "completed"},
    ]
    event_log = tmp_path / "events.jsonl"
    event_log.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    section = acceptance.validate_steering_trace(case, event_log)

    assert section.passed is True


def test_steering_trace_rejects_update_added_only_after_terminal(
    tmp_path: Path,
) -> None:
    case = acceptance.get_flagship_case("flagship-browser-rts")
    rows = [
        {"type": "artifact_changed", "artifact_refs": [{"path": "game.js"}]},
        {"type": "completed"},
        {
            "type": "queue_item_added",
            "payload": {"queue_item_id": "late", "text": case.steering_message},
        },
        {"type": "queue_item_injected", "payload": {"queue_item_id": "late"}},
        {"type": "queue_item_completed", "payload": {"queue_item_id": "late"}},
    ]
    event_log = tmp_path / "events.jsonl"
    event_log.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    section = acceptance.validate_steering_trace(case, event_log)

    assert section.passed is False
    assert any(
        check.name == "steering-before-terminal" and not check.passed
        for check in section.checks
    )


def test_full_report_requires_capability_score_in_addition_to_external_gates() -> None:
    passed_section = acceptance.AcceptanceSection(
        name="pass",
        checks=(
            acceptance.AcceptanceCheck(
                name="proof",
                passed=True,
                detail="verified",
            ),
        ),
    )
    passed = acceptance.FlagshipAcceptanceReport(
        case_id="flagship-premium-site",
        static=passed_section,
        browser=passed_section,
        rubric=passed_section,
        steering=passed_section,
        capability={"score": {"passed": True}},
    )
    missing_capability = acceptance.FlagshipAcceptanceReport(
        case_id="flagship-premium-site",
        static=passed_section,
        browser=passed_section,
        rubric=passed_section,
        steering=passed_section,
    )
    failed_capability = acceptance.FlagshipAcceptanceReport(
        case_id="flagship-premium-site",
        static=passed_section,
        browser=passed_section,
        rubric=passed_section,
        steering=passed_section,
        capability={"score": {"passed": False}},
    )

    assert passed.passed is True
    assert missing_capability.passed is False
    assert failed_capability.passed is False


def test_full_report_separates_functional_prototype_from_showcase_quality() -> None:
    passed_section = acceptance.AcceptanceSection(
        name="pass",
        checks=(
            acceptance.AcceptanceCheck(
                name="proof",
                passed=True,
                detail="verified",
            ),
        ),
    )
    failed_rubric = acceptance.AcceptanceSection(
        name="rubric",
        checks=(
            acceptance.AcceptanceCheck(
                name="visual-bar",
                passed=False,
                detail="below showcase threshold",
            ),
        ),
    )
    report = acceptance.FlagshipAcceptanceReport(
        case_id="flagship-browser-rts",
        static=passed_section,
        browser=passed_section,
        rubric=failed_rubric,
        steering=passed_section,
        capability={"score": {"passed": True}},
    )

    payload = acceptance.report_payload(report)

    assert report.functional_prototype_passed is True
    assert report.passed is False
    assert payload["functional_prototype_passed"] is True
    assert payload["showcase_passed"] is False


def test_flagship_capability_score_reads_the_exported_agent_trace(
    tmp_path: Path,
) -> None:
    case = acceptance.get_flagship_case("flagship-premium-site")
    rows = [
        {
            "type": "worker_started",
            "source_event_type": "run.log.started",
            "payload": {"timestamp": "2026-07-24T01:00:00+00:00"},
        },
        {
            "type": "artifact_changed",
            "source_event_type": "tool.completed",
            "artifact_refs": [
                {"path": "index.html"},
                {"path": "styles.css"},
                {"path": "app.js"},
                {"path": "README.md"},
            ],
            "payload": {
                "timestamp": "2026-07-24T01:00:05+00:00",
                "event": "tool.completed",
                "tool_id": "file_write",
                "result": {"path": "index.html"},
            },
        },
        {
            "type": "token_usage_recorded",
            "source_event_type": "model.responded",
            "token_usage_delta": {
                "prompt_tokens": 1_000,
                "completion_tokens": 200,
                "total_tokens": 1_200,
            },
            "payload": {"timestamp": "2026-07-24T01:00:08+00:00"},
        },
        {
            "type": "completed",
            "source_event_type": "run.log.completed",
            "summary": "Completed and validated.",
            "payload": {
                "timestamp": "2026-07-24T01:00:15+00:00",
                "validation_passed": True,
                "overall_score": 0.92,
            },
        },
    ]
    event_log = tmp_path / "agent-events.jsonl"
    event_log.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    result = acceptance.score_capability_trace(case, event_log)

    assert result["observation"]["wall_time_seconds"] == 15
    assert result["observation"]["token_usage"]["total_tokens"] == 1_200
    assert result["score"]["passed"] is True


@pytest.mark.skipif(
    os.environ.get("DAN_RUN_BROWSER_EVAL") != "1",
    reason="set DAN_RUN_BROWSER_EVAL=1 for the real Chromium acceptance smoke",
)
@pytest.mark.parametrize(
    ("case_id", "fixture_writer"),
    [
        ("flagship-premium-site", "_write_premium_site_fixture"),
        ("flagship-browser-rts", "_write_rts_fixture"),
    ],
)
def test_flagship_browser_contract_runs_in_real_chromium(
    tmp_path: Path,
    case_id: str,
    fixture_writer: str,
) -> None:
    globals()[fixture_writer](tmp_path)
    case = acceptance.get_flagship_case(case_id)

    section = asyncio.run(
        acceptance.validate_browser_artifact(
            case,
            tmp_path,
            screenshot_dir=tmp_path / "screenshots",
        )
    )

    assert section.passed is True, [
        (check.name, check.detail) for check in section.checks if not check.passed
    ]


@pytest.mark.skipif(
    os.environ.get("DAN_RUN_BROWSER_EVAL") != "1",
    reason="set DAN_RUN_BROWSER_EVAL=1 for the full flagship CLI gate",
)
def test_flagship_cli_combines_artifact_browser_rubric_steering_and_score(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "site"
    workspace.mkdir()
    _write_premium_site_fixture(workspace)
    case = acceptance.get_flagship_case("flagship-premium-site")
    rubric = tmp_path / "rubric.json"
    (tmp_path / "site-desktop.png").write_bytes(_MINIMAL_PNG)
    (tmp_path / "site-phone.png").write_bytes(_MINIMAL_PNG)
    rubric.write_text(
        json.dumps(
            {
                "reviewer": "independent-review-pass-1",
                "evidence_refs": ["site-desktop.png", "site-phone.png"],
                "scores": {dimension: 4.2 for dimension in case.rubric_dimensions},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    queue_id = "queue-cli-steer"
    event_log = tmp_path / "agent-events.jsonl"
    rows = [
        {
            "type": "worker_started",
            "source_event_type": "run.log.started",
            "payload": {"timestamp": "2026-07-24T01:00:00+00:00"},
        },
        {
            "type": "artifact_changed",
            "source_event_type": "tool.completed",
            "artifact_refs": [
                {"path": "index.html"},
                {"path": "styles.css"},
                {"path": "app.js"},
                {"path": "README.md"},
            ],
            "payload": {
                "timestamp": "2026-07-24T01:00:05+00:00",
                "event": "tool.completed",
                "tool_id": "file_write",
                "result": {"path": "index.html"},
            },
        },
        {
            "type": "queue_item_added",
            "payload": {
                "queue_item_id": queue_id,
                "text": case.steering_message,
            },
        },
        {
            "type": "queue_item_injected",
            "payload": {
                "queue_item_id": queue_id,
                "checkpoint": "tool.completed:file_write",
            },
        },
        {
            "type": "token_usage_recorded",
            "source_event_type": "model.responded",
            "token_usage_delta": {
                "prompt_tokens": 1_000,
                "completion_tokens": 200,
                "total_tokens": 1_200,
            },
            "payload": {"timestamp": "2026-07-24T01:00:08+00:00"},
        },
        {
            "type": "completed",
            "source_event_type": "run.log.completed",
            "summary": "Completed and validated.",
            "payload": {
                "timestamp": "2026-07-24T01:00:15+00:00",
                "validation_passed": True,
                "overall_score": 0.92,
            },
        },
        {
            "type": "queue_item_completed",
            "payload": {"queue_item_id": queue_id},
        },
    ]
    event_log.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "acceptance-report.json"

    exit_code = acceptance.main(
        [
            "--case-id",
            case.case_id,
            "--workspace",
            str(workspace),
            "--browser",
            "--rubric",
            str(rubric),
            "--event-log",
            str(event_log),
            "--screenshot-dir",
            str(tmp_path / "screenshots"),
            "--output",
            str(output),
        ]
    )
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert report["passed"] is True
    assert report["static"]["passed"] is True
    assert report["browser"]["passed"] is True
    assert report["rubric"]["passed"] is True
    assert report["steering"]["passed"] is True
    assert report["capability"]["score"]["passed"] is True


def _write_premium_site_fixture(root: Path) -> None:
    (root / "index.html").write_text(
        """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Aether One</title><link rel="icon" href="data:,"><link rel="stylesheet" href="styles.css"></head>
<body><nav><a href="#story">Aether</a></nav><main>
<section><h1>Hear space differently.</h1>
<button data-testid="primary-cta">Enter the field</button>
<p aria-live="polite" data-testid="cta-state">Ready</p></section>
<section id="story"><h2>Form</h2></section><section><h2>Field</h2></section>
<section><h2>Material</h2></section><section><h2>Listening</h2></section>
</main><script src="app.js"></script></body></html>""",
        encoding="utf-8",
    )
    (root / "styles.css").write_text(
        """:root{--ink:#211d18;--paper:#f2ece1;--clay:#9b7253;--line:#c9bca9;
--space:clamp(1rem,4vw,5rem);--radius:1.5rem;--serif:Georgia,serif}
*{box-sizing:border-box}body{margin:0;color:var(--ink);background:var(--paper)}
nav,section{padding:var(--space)}main{overflow:hidden}section{min-height:45vh}
button:focus-visible,a:focus-visible{outline:3px solid var(--clay)}
@media(max-width:600px){section{min-height:36vh}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
""",
        encoding="utf-8",
    )
    (root / "app.js").write_text(
        """const cta=document.querySelector('[data-testid="primary-cta"]');
const state=document.querySelector('[data-testid="cta-state"]');
cta.addEventListener('click',()=>{state.textContent='Aether field opened';});""",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# Aether One\n\nValidated locally.\n", encoding="utf-8"
    )


def _write_rts_fixture(root: Path) -> None:
    (root / "index.html").write_text(
        """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ashfall Command</title><link rel="icon" href="data:,"><link rel="stylesheet" href="styles.css"></head>
<body><main><h1>Ashfall Command</h1>
<canvas data-testid="battlefield" width="960" height="480"></canvas>
<div class="hud"><button data-testid="start-game">Start</button>
<button data-testid="build-power">Build power</button>
<button data-testid="train-unit">Train unit</button>
<button data-testid="attack-command">Attack</button>
<button data-testid="restart-game">Restart</button>
<output data-testid="game-status"></output><output data-testid="resource-count"></output>
<output data-testid="player-unit-count"></output><output data-testid="enemy-base-health"></output>
</div></main><script src="game.js"></script></body></html>""",
        encoding="utf-8",
    )
    (root / "styles.css").write_text(
        """:root{--ink:#f5e9d0;--ground:#1a211d;--ember:#e1693d;--steel:#78908b;
--line:#51635c;--space:clamp(.5rem,2vw,1.5rem);--radius:.6rem}
*{box-sizing:border-box}body{margin:0;background:var(--ground);color:var(--ink)}
main{width:min(100%,1100px);margin:auto;padding:var(--space)}
canvas{display:block;width:100%;height:auto;border:1px solid var(--line)}
.hud{display:flex;flex-wrap:wrap;gap:.5rem;padding-top:var(--space)}
button:focus-visible{outline:3px solid var(--ember)}
@media(max-width:600px){.hud button{flex:1 1 40%}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
""",
        encoding="utf-8",
    )
    (root / "game.js").write_text(
        """const initial=()=>({ticks:0,resources:1000,powerStructures:0,playerUnits:0,
enemyHealth:100,status:'ready',initialEnemyHealth:100});let s=initial(),raf=0;
const q=x=>document.querySelector(`[data-testid="${x}"]`);
function draw(){const c=q('battlefield'),x=c.getContext('2d');x.fillStyle='#1a211d';
x.fillRect(0,0,c.width,c.height);x.fillStyle='#e1693d';x.fillRect(760,180,s.enemyHealth*1.5,80);}
function sync(){q('game-status').textContent=s.status;q('resource-count').textContent=s.resources;
q('player-unit-count').textContent=s.playerUnits;q('enemy-base-health').textContent=s.enemyHealth;draw();}
function loop(){if(s.status==='playing')s.ticks++;sync();raf=requestAnimationFrame(loop);}
function start(){s.status='playing';if(!raf)loop()}function build(){if(s.resources>=100){s.resources-=100;s.powerStructures++;sync()}}
function train(){if(s.resources>=150){s.resources-=150;s.playerUnits++;sync()}}
function attack(){if(s.playerUnits>0){s.enemyHealth=Math.max(0,s.enemyHealth-25);if(!s.enemyHealth)s.status='won';sync()}}
function restart(){s=initial();sync()}q('start-game').addEventListener('click',start);
q('build-power').addEventListener('click',build);q('train-unit').addEventListener('click',train);
q('attack-command').addEventListener('click',attack);q('restart-game').addEventListener('click',restart);
document.addEventListener('keydown',e=>{if(e.key==='b')build();if(e.key==='u')train();if(e.key==='a')attack()});
q('battlefield').addEventListener('pointerdown',()=>{});window.__DAN_ACCEPTANCE__={getState:()=>({...s})};sync();""",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# Ashfall Command\n\nValidated locally.\n",
        encoding="utf-8",
    )
