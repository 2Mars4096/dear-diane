"""Executable acceptance contracts for Diane's two flagship MVP cases.

The contracts deliberately separate three questions:

1. Did Diane produce the required artifact shape?
2. Does the artifact work in a real browser at desktop and phone sizes?
3. Does an independent reviewer consider the visible result genuinely polished?

The first two questions are deterministic.  The third uses a small, explicit
rubric and screenshot evidence instead of treating a model's own completion
claim as proof.

Usage after a live Diane run::

    PYTHONPATH=src:. python -m tests.eval.super_dan_flagship_acceptance \
        --case-id flagship-premium-site \
        --workspace /path/to/generated/site \
        --browser \
        --rubric /path/to/reviewer-rubric.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class FlagshipAcceptanceCase:
    case_id: str
    title: str
    prompt: str
    steering_message: str
    steering_trigger: str
    required_files: tuple[str, ...]
    required_test_ids: tuple[str, ...]
    rubric_dimensions: tuple[str, ...]
    min_rubric_average: float = 4.0
    min_rubric_dimension: float = 3.0
    min_static_pass_rate: float = 1.0
    min_browser_pass_rate: float = 1.0
    target_wall_time_seconds: float = 1_500
    target_total_tokens: int = 180_000


@dataclass(frozen=True, slots=True)
class AcceptanceCheck:
    name: str
    passed: bool
    detail: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AcceptanceSection:
    name: str
    checks: tuple[AcceptanceCheck, ...]

    @property
    def pass_rate(self) -> float:
        if not self.checks:
            return 0.0
        return sum(1 for check in self.checks if check.passed) / len(self.checks)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)


@dataclass(frozen=True, slots=True)
class FlagshipAcceptanceReport:
    case_id: str
    static: AcceptanceSection
    browser: AcceptanceSection | None = None
    rubric: AcceptanceSection | None = None
    steering: AcceptanceSection | None = None
    capability: Mapping[str, Any] | None = None

    @property
    def functional_prototype_passed(self) -> bool:
        return (
            self.static.passed
            and self.browser is not None
            and self.browser.passed
            and self.steering is not None
            and self.steering.passed
            and self.capability is not None
            and bool(
                (
                    self.capability.get("score")
                    if isinstance(self.capability.get("score"), Mapping)
                    else {}
                ).get("passed")
            )
        )

    @property
    def passed(self) -> bool:
        """Strict showcase bar; prototype success remains visible separately."""

        return (
            self.functional_prototype_passed
            and self.rubric is not None
            and self.rubric.passed
        )


_PREMIUM_SITE_PROMPT = """
Build an original, production-quality single-page launch site for the fictional
spatial-audio product "Aether One" in this empty workspace. Aim for the
precision, restraint, typography, responsive composition, and motion polish of
the best global hardware launches—Apple-level craft, but do not copy Apple
language, layouts, assets, trademarks, or product imagery.

Use only local HTML, CSS, and JavaScript; do not depend on a framework, CDN, or
remote asset. Deliver index.html, styles.css, app.js, and README.md. The page
must have semantic navigation and main content, at least four materially
different sections, a clear product story, a primary CTA, keyboard-visible
focus, responsive layouts at 1440x900 and 390x844, and reduced-motion support.
Use original CSS illustration or local generated visuals rather than stock
placeholders, and include a local or data-URL favicon so the clean browser run
does not hide a missing-asset error.

Acceptance instrumentation is part of the product, not a substitute for it:
mark the CTA with data-testid="primary-cta" and include a live region with
data-testid="cta-state". Activating the CTA must visibly update that state.
Run fresh syntax/static checks, serve the site locally, inspect it in a real
browser at both viewports, interact with the CTA, fix console or overflow
defects, and record the validation evidence in README.md.
""".strip()

_PREMIUM_SITE_STEERING = """
Mid-course direction: preserve the information architecture and finished work,
but shift the visual language warmer and more editorial. Use quiet mineral
tones, tactile material detail, and restrained motion; avoid neon gradients,
glass-card grids, and generic AI landing-page motifs. Re-check the phone layout
and CTA after adapting.
""".strip()

_RTS_PROMPT = """
Build an original, polished browser real-time strategy skirmish called
"Ashfall Command" in this empty workspace. It should evoke the immediacy,
readability, resource pressure, base growth, unit production, and battlefield
feedback of a classic commercial RTS such as Red Alert 2, while copying none of
its names, factions, art, maps, audio, UI, or other protected expression.

Use only local HTML, CSS, and JavaScript; do not depend on a framework, CDN, or
remote asset. Deliver index.html, styles.css, game.js, and README.md. The game
must be meaningfully playable: start/restart, a running simulation, resources,
at least one power/economy structure, unit production, player units, an enemy
base, an attack command, win/lose state, mouse controls, keyboard shortcuts,
clear feedback, and a legible battlefield at 1440x900 and 390x844. Include a
local or data-URL favicon so the clean browser run has no missing-asset error.

Expose deterministic acceptance hooks without changing normal play:
window.__DAN_ACCEPTANCE__.getState() must return JSON-safe fields
ticks, resources, powerStructures, playerUnits, enemyHealth, status, and
initialEnemyHealth. Provide these data-testid values:
battlefield, start-game, build-power, train-unit, attack-command, restart-game,
game-status, resource-count, player-unit-count, and enemy-base-health.
Starting must advance ticks; building must spend resources and increase
powerStructures; training must spend resources and increase playerUnits;
attacking with a unit must reduce enemyHealth; restarting must restore the
initial skirmish state. Run fresh syntax/static checks, serve and play the game
in a real browser, exercise the full loop, fix console or overflow defects, and
record validation evidence in README.md.
""".strip()

_RTS_STEERING = """
Mid-course direction: keep the working simulation and existing file structure,
but prioritize tactile command feedback and tactical readability over adding
more systems. Make selection, production, attack, damage, and win/lose changes
unmistakable; improve the phone command deck; then replay the complete
start-build-train-attack-restart loop.
""".strip()


def flagship_cases() -> tuple[FlagshipAcceptanceCase, ...]:
    """Return the frozen flagship acceptance cases."""

    return (
        FlagshipAcceptanceCase(
            case_id="flagship-premium-site",
            title="Aether One premium product launch",
            prompt=_PREMIUM_SITE_PROMPT,
            steering_message=_PREMIUM_SITE_STEERING,
            steering_trigger=(
                "Inject after the first artifact_changed event and before the first "
                "successful browser validation."
            ),
            required_files=("index.html", "styles.css", "app.js", "README.md"),
            required_test_ids=("primary-cta", "cta-state"),
            rubric_dimensions=(
                "composition",
                "typography",
                "responsive_craft",
                "motion_and_interaction",
                "accessibility",
                "originality",
            ),
        ),
        FlagshipAcceptanceCase(
            case_id="flagship-browser-rts",
            title="Ashfall Command browser RTS",
            prompt=_RTS_PROMPT,
            steering_message=_RTS_STEERING,
            steering_trigger=(
                "Inject after the first artifact_changed event and before the first "
                "successful full gameplay-loop validation."
            ),
            required_files=("index.html", "styles.css", "game.js", "README.md"),
            required_test_ids=(
                "battlefield",
                "start-game",
                "build-power",
                "train-unit",
                "attack-command",
                "restart-game",
                "game-status",
                "resource-count",
                "player-unit-count",
                "enemy-base-health",
            ),
            rubric_dimensions=(
                "battlefield_readability",
                "command_feedback",
                "art_direction",
                "responsive_craft",
                "gameplay_coherence",
                "originality",
            ),
        ),
    )


def get_flagship_case(case_id: str) -> FlagshipAcceptanceCase:
    for case in flagship_cases():
        if case.case_id == case_id:
            return case
    known = ", ".join(case.case_id for case in flagship_cases())
    raise KeyError(f"Unknown flagship case {case_id!r}; expected one of: {known}")


def validate_static_artifact(
    case: FlagshipAcceptanceCase,
    workspace: Path,
) -> AcceptanceSection:
    """Validate a generated artifact without trusting its own test report."""

    root = workspace.resolve()
    checks: list[AcceptanceCheck] = []
    files: dict[str, str] = {}
    for relative in case.required_files:
        path = root / relative
        exists = path.is_file()
        text = path.read_text(encoding="utf-8", errors="replace") if exists else ""
        files[relative] = text
        checks.append(
            AcceptanceCheck(
                name=f"required-file:{relative}",
                passed=exists and bool(text.strip()),
                detail=(
                    "present and non-empty"
                    if exists and text.strip()
                    else "missing or empty"
                ),
                evidence={"path": str(path), "bytes": len(text.encode("utf-8"))},
            )
        )

    combined = "\n".join(files.values())
    html = files.get("index.html", "")
    css = files.get("styles.css", "")
    script_name = "game.js" if case.case_id == "flagship-browser-rts" else "app.js"
    script = files.get(script_name, "")

    checks.extend(
        [
            _contains_check(
                "responsive-viewport",
                html,
                r"<meta[^>]+name=[\"']viewport[\"']",
                "viewport meta is present",
            ),
            _contains_check(
                "semantic-main", html, r"<main(?:\s|>)", "semantic main exists"
            ),
            _contains_check(
                "responsive-breakpoint",
                css,
                r"@media\s*\(",
                "at least one responsive media query exists",
            ),
            _contains_check(
                "reduced-motion",
                css,
                r"prefers-reduced-motion",
                "reduced-motion override exists",
            ),
            _contains_check(
                "keyboard-focus",
                css,
                r":focus(?:-visible)?",
                "keyboard focus styling exists",
            ),
            _contains_check(
                "favicon",
                html,
                r"<link[^>]+rel=[\"'][^\"']*\bicon\b[^\"']*[\"']",
                "a local or data-URL favicon is declared",
            ),
            AcceptanceCheck(
                name="local-only-assets",
                passed=not _remote_runtime_asset_refs(html, css, script),
                detail="no remote runtime asset references",
                evidence={"remote_refs": _remote_runtime_asset_refs(html, css, script)},
            ),
        ]
    )
    for test_id in case.required_test_ids:
        checks.append(
            _contains_check(
                f"test-id:{test_id}",
                html,
                rf"data-testid=[\"']{re.escape(test_id)}[\"']",
                f"data-testid={test_id} exists",
            )
        )

    forbidden = (
        ("Apple", r"\bapple\b"),
        ("Red Alert", r"\bred\s+alert\b"),
        ("Command & Conquer", r"\bcommand\s*(?:&|and)\s*conquer\b"),
        ("Electronic Arts", r"\belectronic\s+arts\b"),
    )
    brand_scan_text = re.sub(r"-apple-system\b", "", combined, flags=re.I)
    for label, pattern in forbidden:
        checks.append(
            AcceptanceCheck(
                name=f"originality-no-output-reference:{label.lower().replace(' ', '-')}",
                passed=re.search(pattern, brand_scan_text, re.I) is None,
                detail=f"generated artifact does not mention {label}",
            )
        )

    if case.case_id == "flagship-premium-site":
        checks.extend(_premium_site_static_checks(html, css, script))
    else:
        checks.extend(_rts_static_checks(html, script))
    return AcceptanceSection(name="static", checks=tuple(checks))


def validate_rubric(
    case: FlagshipAcceptanceCase,
    payload: Mapping[str, Any],
    *,
    evidence_root: Path,
) -> AcceptanceSection:
    """Validate an independent 1–5 visual review with screenshot evidence."""

    scores = payload.get("scores") if isinstance(payload.get("scores"), Mapping) else {}
    evidence_refs = payload.get("evidence_refs")
    refs = (
        [str(item).strip() for item in evidence_refs if str(item).strip()]
        if isinstance(evidence_refs, Sequence)
        and not isinstance(evidence_refs, (str, bytes))
        else []
    )
    reviewer = str(payload.get("reviewer") or "").strip()
    evidence_files, invalid_evidence_refs = _rubric_evidence_files(
        refs,
        evidence_root=evidence_root,
    )
    checks: list[AcceptanceCheck] = [
        AcceptanceCheck(
            name="independent-reviewer",
            passed=bool(reviewer)
            and reviewer.lower() not in {"super dan", "self", "builder"},
            detail=f"reviewer={reviewer or 'missing'}",
        ),
        AcceptanceCheck(
            name="desktop-and-phone-evidence",
            passed=(
                any("desktop" in ref.lower() for ref in refs)
                and any(
                    marker in ref.lower()
                    for ref in refs
                    for marker in ("phone", "mobile")
                )
            ),
            detail="rubric cites desktop and phone/mobile screenshots",
            evidence={"evidence_refs": refs},
        ),
        AcceptanceCheck(
            name="screenshot-evidence-files",
            passed=bool(refs) and not invalid_evidence_refs,
            detail=(
                f"{len(evidence_files)} non-empty local screenshot(s)"
                if refs and not invalid_evidence_refs
                else "invalid or missing: " + ", ".join(invalid_evidence_refs)
            ),
            evidence={"resolved_evidence": evidence_files},
        ),
    ]
    parsed_scores: dict[str, float] = {}
    for dimension in case.rubric_dimensions:
        value = _safe_float(scores.get(dimension))
        parsed_scores[dimension] = value if value is not None else 0.0
        checks.append(
            AcceptanceCheck(
                name=f"rubric:{dimension}",
                passed=value is not None and case.min_rubric_dimension <= value <= 5.0,
                detail=(f"{value:.1f}/5" if value is not None else "missing score"),
            )
        )
    average = (
        sum(parsed_scores.values()) / len(case.rubric_dimensions)
        if case.rubric_dimensions
        else 0.0
    )
    checks.append(
        AcceptanceCheck(
            name="rubric-average",
            passed=average >= case.min_rubric_average,
            detail=f"{average:.2f}/5; required {case.min_rubric_average:.2f}",
            evidence={"scores": parsed_scores},
        )
    )
    return AcceptanceSection(name="rubric", checks=tuple(checks))


def _rubric_evidence_files(
    refs: Sequence[str],
    *,
    evidence_root: Path,
) -> tuple[list[str], list[str]]:
    root = evidence_root.resolve()
    resolved_files: list[str] = []
    invalid: list[str] = []
    supported_suffixes = {".jpeg", ".jpg", ".png", ".webp"}
    for ref in refs:
        candidate = Path(ref).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            invalid.append(ref)
            continue
        try:
            valid = (
                resolved.is_file()
                and resolved.stat().st_size > 0
                and resolved.suffix.lower() in supported_suffixes
                and _has_supported_image_signature(resolved)
            )
        except OSError:
            valid = False
        if not valid:
            invalid.append(ref)
            continue
        resolved_files.append(str(resolved))
    return resolved_files, invalid


def _has_supported_image_signature(path: Path) -> bool:
    with path.open("rb") as handle:
        header = handle.read(32)
    suffix = path.suffix.lower()
    if suffix == ".png":
        return (
            len(header) >= 24
            and header.startswith(b"\x89PNG\r\n\x1a\n")
            and header[12:16] == b"IHDR"
        )
    if suffix in {".jpeg", ".jpg"}:
        return header.startswith(b"\xff\xd8\xff")
    if suffix == ".webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    return False


def validate_steering_trace(
    case: FlagshipAcceptanceCase,
    event_log: Path,
) -> AcceptanceSection:
    """Prove that the frozen direction change landed during, not after, work."""

    rows: list[Mapping[str, Any]] = []
    with event_log.open(encoding="utf-8") as handle:
        for line in handle:
            payload = json.loads(line)
            if isinstance(payload, Mapping):
                rows.append(payload)

    mutation_index: int | None = None
    queued_index: int | None = None
    injected_index: int | None = None
    delivered_index: int | None = None
    terminal_index: int | None = None
    steering_queue_item_id = ""
    signature = (
        "warmer and more editorial"
        if case.case_id == "flagship-premium-site"
        else "tactile command feedback"
    )

    for index, row in enumerate(rows):
        event_type = str(row.get("type") or row.get("event") or "").strip()
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        source_event_type = str(row.get("source_event_type") or "")
        tool_id = str(row.get("tool_id") or "")
        if mutation_index is None and (
            event_type == "artifact_changed"
            or bool(row.get("artifact_refs"))
            or (
                event_type.endswith("tool.completed")
                and tool_id in {"file_write", "file_edit"}
            )
        ):
            mutation_index = index
        if event_type == "queue_item_added":
            text = str(payload.get("text") or "")
            if signature in text.lower():
                queued_index = index
                steering_queue_item_id = str(payload.get("queue_item_id") or "")
        if event_type == "queue_item_injected" and (
            not steering_queue_item_id
            or str(payload.get("queue_item_id") or "") == steering_queue_item_id
        ):
            injected_index = index
        if (
            event_type == "queue_item_completed"
            and (
                not steering_queue_item_id
                or str(payload.get("queue_item_id") or "") == steering_queue_item_id
            )
        ) or source_event_type == "toolloop.operator_messages.delivered":
            delivered_index = index
        if _is_terminal_run_event(row):
            terminal_index = index

    checks = (
        AcceptanceCheck(
            name="steering-text-queued",
            passed=queued_index is not None,
            detail=f"frozen steering signature={signature!r}",
        ),
        AcceptanceCheck(
            name="steering-after-first-artifact",
            passed=(
                mutation_index is not None
                and queued_index is not None
                and mutation_index < queued_index
            ),
            detail=f"artifact index={mutation_index}; queue index={queued_index}",
        ),
        AcceptanceCheck(
            name="steering-injected-at-checkpoint",
            passed=(
                queued_index is not None
                and injected_index is not None
                and queued_index <= injected_index
            ),
            detail=f"queue index={queued_index}; injection index={injected_index}",
        ),
        AcceptanceCheck(
            name="steering-acknowledged-after-model-delivery",
            passed=(
                injected_index is not None
                and delivered_index is not None
                and injected_index <= delivered_index
            ),
            detail=f"injection index={injected_index}; delivery index={delivered_index}",
        ),
        AcceptanceCheck(
            name="steering-before-terminal",
            passed=(
                injected_index is not None
                and terminal_index is not None
                and injected_index < terminal_index
            ),
            detail=f"injection index={injected_index}; terminal index={terminal_index}",
        ),
    )
    return AcceptanceSection(name="steering", checks=checks)


def score_capability_trace(
    case: FlagshipAcceptanceCase,
    event_log: Path,
) -> dict[str, Any]:
    """Score delivery, validation, time, and tokens from the durable Agent trace."""

    from tests.eval import super_dan_capability_benchmark as benchmark

    benchmark_case = next(
        item for item in benchmark._benchmark_cases() if item.case_id == case.case_id
    )
    observation = benchmark.load_event_log_observation(
        event_log,
        case_id=case.case_id,
    )
    score = benchmark.score_observed_run(benchmark_case, observation)
    return {
        "observation": asdict(observation),
        "score": asdict(score),
    }


async def validate_browser_artifact(
    case: FlagshipAcceptanceCase,
    workspace: Path,
    *,
    screenshot_dir: Path,
) -> AcceptanceSection:
    """Run black-box interaction checks in a real Chromium browser."""

    from playwright.async_api import async_playwright

    checks: list[AcceptanceCheck] = []
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []

    with _serve_directory(workspace.resolve()) as base_url:
        async with async_playwright() as playwright:
            launch_options: dict[str, Any] = {"headless": True}
            system_chrome = Path(
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            )
            if system_chrome.is_file():
                launch_options["executable_path"] = str(system_chrome)
            browser = await playwright.chromium.launch(**launch_options)
            try:
                for label, viewport in (
                    ("desktop", {"width": 1440, "height": 900}),
                    ("phone", {"width": 390, "height": 844}),
                ):
                    page = await browser.new_page(viewport=viewport)
                    page.on(
                        "console",
                        lambda message: (
                            console_errors.append(message.text)
                            if message.type == "error"
                            else None
                        ),
                    )
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    response = await page.goto(base_url, wait_until="networkidle")
                    checks.append(
                        AcceptanceCheck(
                            name=f"{label}:http-load",
                            passed=response is not None and response.ok,
                            detail=f"status={response.status if response else 'no-response'}",
                        )
                    )
                    metrics = await page.evaluate("""() => ({
                          innerWidth: window.innerWidth,
                          documentWidth: document.documentElement.scrollWidth,
                          bodyWidth: document.body.scrollWidth
                        })""")
                    checks.append(
                        AcceptanceCheck(
                            name=f"{label}:no-horizontal-overflow",
                            passed=(
                                metrics["documentWidth"] <= metrics["innerWidth"] + 1
                                and metrics["bodyWidth"] <= metrics["innerWidth"] + 1
                            ),
                            detail=(
                                f"viewport={metrics['innerWidth']}, "
                                f"document={metrics['documentWidth']}, body={metrics['bodyWidth']}"
                            ),
                            evidence=metrics,
                        )
                    )
                    checks.append(
                        AcceptanceCheck(
                            name=f"{label}:main-visible",
                            passed=await page.locator("main").is_visible(),
                            detail="semantic main is visible",
                        )
                    )
                    if case.case_id == "flagship-premium-site":
                        checks.extend(await _exercise_premium_site(page, label))
                    else:
                        checks.extend(await _exercise_rts(page, label))
                        checks.append(await _stage_rts_visual_evidence(page, label))
                    screenshot_path = screenshot_dir / f"{case.case_id}-{label}.png"
                    await page.screenshot(path=str(screenshot_path), full_page=True)
                    checks.append(
                        AcceptanceCheck(
                            name=f"{label}:screenshot",
                            passed=screenshot_path.is_file()
                            and screenshot_path.stat().st_size > 0,
                            detail=str(screenshot_path),
                        )
                    )
                    await page.close()
            finally:
                await browser.close()

    checks.extend(
        [
            AcceptanceCheck(
                name="console-errors",
                passed=not console_errors,
                detail=(
                    "no browser console errors"
                    if not console_errors
                    else "; ".join(console_errors[:5])
                ),
            ),
            AcceptanceCheck(
                name="page-errors",
                passed=not page_errors,
                detail=(
                    "no uncaught page errors"
                    if not page_errors
                    else "; ".join(page_errors[:5])
                ),
            ),
        ]
    )
    return AcceptanceSection(name="browser", checks=tuple(checks))


async def _exercise_premium_site(page: Any, label: str) -> list[AcceptanceCheck]:
    checks: list[AcceptanceCheck] = []
    checks.append(
        AcceptanceCheck(
            name=f"{label}:title",
            passed=bool((await page.title()).strip()),
            detail=f"title={await page.title()}",
        )
    )
    section_count = await page.locator("main section").count()
    checks.append(
        AcceptanceCheck(
            name=f"{label}:story-depth",
            passed=section_count >= 4,
            detail=f"main sections={section_count}; required >=4",
        )
    )
    painted_sections = 0
    section_details: list[dict[str, Any]] = []
    sections = page.locator("main section")
    for index in range(section_count):
        section = sections.nth(index)
        await section.scroll_into_view_if_needed()
        await page.wait_for_timeout(125)
        paint = await section.evaluate("""element => {
              const style = getComputedStyle(element);
              const rect = element.getBoundingClientRect();
              return {
                id: element.id || `section-${Array.from(
                  element.parentElement?.children || []
                ).indexOf(element) + 1}`,
                opacity: Number(style.opacity),
                visibility: style.visibility,
                display: style.display,
                width: rect.width,
                height: rect.height
              };
            }""")
        is_painted = (
            paint["opacity"] > 0.05
            and paint["visibility"] != "hidden"
            and paint["display"] != "none"
            and paint["width"] > 0
            and paint["height"] > 0
        )
        painted_sections += int(is_painted)
        section_details.append({**paint, "painted": is_painted})
    checks.append(
        AcceptanceCheck(
            name=f"{label}:all-story-sections-painted",
            passed=section_count >= 4 and painted_sections == section_count,
            detail=f"painted sections={painted_sections}/{section_count}",
            evidence={"sections": section_details},
        )
    )
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(100)
    cta_locator = page.get_by_test_id("primary-cta")
    cta_count = await cta_locator.count()
    checks.append(
        AcceptanceCheck(
            name=f"{label}:unique-primary-cta",
            passed=cta_count == 1,
            detail=f"primary CTA count={cta_count}; required 1",
        )
    )
    state_locator = page.get_by_test_id("cta-state")
    state_count = await state_locator.count()
    checks.append(
        AcceptanceCheck(
            name=f"{label}:unique-cta-state",
            passed=state_count == 1,
            detail=f"CTA state count={state_count}; required 1",
        )
    )
    if cta_count == 0 or state_count == 0:
        return checks
    cta = cta_locator.first
    state = state_locator.first
    checks.append(
        AcceptanceCheck(
            name=f"{label}:cta-visible",
            passed=await cta.is_visible(),
            detail="primary CTA is visible",
        )
    )
    before = (await state.inner_text()).strip()
    await cta.click()
    await page.wait_for_timeout(100)
    after = (await state.inner_text()).strip()
    checks.append(
        AcceptanceCheck(
            name=f"{label}:cta-action",
            passed=bool(after) and after != before,
            detail=f"state changed from {before!r} to {after!r}",
        )
    )
    return checks


def _remote_runtime_asset_refs(html: str, css: str, script: str) -> list[str]:
    """Return network-loaded references without flagging text inside data payloads."""

    refs: list[str] = []
    patterns = (
        (
            html,
            r"""(?:src|href|poster)\s*=\s*["'](https?://[^"']+)["']""",
        ),
        (
            css,
            r"""(?:url\(\s*|@import\s+)(?:["']\s*)?(https?://[^\s"')]+)""",
        ),
        (
            script,
            r"""(?:fetch|import)\s*\(\s*["'](https?://[^"']+)["']""",
        ),
    )
    for text, pattern in patterns:
        refs.extend(match.group(1) for match in re.finditer(pattern, text, re.I))
    return refs


def _is_terminal_run_event(row: Mapping[str, Any]) -> bool:
    """Ignore recoverable worker failures when locating run settlement."""

    event_type = str(row.get("type") or row.get("event") or "")
    if event_type not in {"completed", "failed", "blocked", "stopped"}:
        return False
    source_event_type = str(row.get("source_event_type") or "").strip()
    return not source_event_type or source_event_type in {
        "run.log.completed",
        "run.log.failed",
        "chat_v2.background_run.completed",
        "chat_v2.background_run.failed",
        "chat_v2.background_run.blocked",
        "chat_v2.background_run.stopped",
    }


async def _exercise_rts(page: Any, label: str) -> list[AcceptanceCheck]:
    checks: list[AcceptanceCheck] = []
    battlefield = page.get_by_test_id("battlefield")
    checks.append(
        AcceptanceCheck(
            name=f"{label}:battlefield-visible",
            passed=await battlefield.is_visible(),
            detail="battlefield is visible",
        )
    )
    hook_exists = await page.evaluate("""() => Boolean(
          window.__DAN_ACCEPTANCE__ &&
          typeof window.__DAN_ACCEPTANCE__.getState === "function"
        )""")
    checks.append(
        AcceptanceCheck(
            name=f"{label}:acceptance-hook",
            passed=hook_exists,
            detail="window.__DAN_ACCEPTANCE__.getState is callable",
        )
    )
    if not hook_exists:
        return checks

    await page.get_by_test_id("start-game").click()
    state_started = await _wait_for_state(
        page,
        "(state) => state.status === 'playing' && state.ticks > 0",
    )
    checks.append(
        AcceptanceCheck(
            name=f"{label}:simulation-start",
            passed=state_started is not None,
            detail=f"state={state_started}",
        )
    )
    if state_started is None:
        return checks

    before_build = await _game_state(page)
    await page.get_by_test_id("build-power").click()
    after_build = await _wait_for_state(
        page,
        "(state) => state.powerStructures > 0",
    )
    checks.append(
        AcceptanceCheck(
            name=f"{label}:build-power",
            passed=(
                after_build is not None
                and after_build["resources"] < before_build["resources"]
                and after_build["powerStructures"] > before_build["powerStructures"]
            ),
            detail=f"before={before_build}; after={after_build}",
        )
    )

    before_train = await _game_state(page)
    await page.get_by_test_id("train-unit").click()
    after_train = await _wait_for_state(
        page,
        f"(state) => state.playerUnits > {int(before_train['playerUnits'])}",
    )
    checks.append(
        AcceptanceCheck(
            name=f"{label}:train-unit",
            passed=(
                after_train is not None
                and after_train["resources"] < before_train["resources"]
                and after_train["playerUnits"] > before_train["playerUnits"]
            ),
            detail=f"before={before_train}; after={after_train}",
        )
    )

    before_attack = await _game_state(page)
    await page.get_by_test_id("attack-command").click()
    after_attack = await _wait_for_state(
        page,
        f"(state) => state.enemyHealth < {float(before_attack['enemyHealth'])}",
        timeout_ms=5_000,
    )
    checks.append(
        AcceptanceCheck(
            name=f"{label}:attack-damage",
            passed=(
                after_attack is not None
                and after_attack["enemyHealth"] < before_attack["enemyHealth"]
            ),
            detail=f"before={before_attack}; after={after_attack}",
        )
    )

    await page.get_by_test_id("restart-game").click()
    restarted = await _wait_for_state(
        page,
        """(state) =>
          state.enemyHealth === state.initialEnemyHealth &&
          state.playerUnits === 0 &&
          state.powerStructures === 0
        """,
    )
    checks.append(
        AcceptanceCheck(
            name=f"{label}:restart",
            passed=restarted is not None,
            detail=f"state={restarted}",
        )
    )
    return checks


async def _game_state(page: Any) -> dict[str, Any]:
    return await page.evaluate("() => window.__DAN_ACCEPTANCE__.getState()")


async def _stage_rts_visual_evidence(page: Any, label: str) -> AcceptanceCheck:
    """Leave screenshots in a representative live-battle state after restart passes."""

    await page.get_by_test_id("start-game").click()
    started = await _wait_for_state(
        page,
        "(state) => state.status === 'playing' && state.ticks > 0",
    )
    if started is None:
        return AcceptanceCheck(
            name=f"{label}:active-battle-evidence",
            passed=False,
            detail="could not restart the simulation for visual evidence",
        )
    await page.get_by_test_id("build-power").click()
    await page.get_by_test_id("train-unit").click()
    await page.get_by_test_id("attack-command").click()
    staged = await _wait_for_state(
        page,
        """(state) =>
          state.powerStructures > 0 &&
          state.playerUnits > 0 &&
          state.enemyHealth < state.initialEnemyHealth
        """,
    )
    await page.wait_for_timeout(150)
    return AcceptanceCheck(
        name=f"{label}:active-battle-evidence",
        passed=staged is not None,
        detail=f"state={staged}",
    )


async def _wait_for_state(
    page: Any,
    predicate: str,
    *,
    timeout_ms: int = 2_500,
) -> dict[str, Any] | None:
    try:
        await page.wait_for_function(
            f"""() => {{
              const state = window.__DAN_ACCEPTANCE__.getState();
              return ({predicate})(state);
            }}""",
            timeout=timeout_ms,
        )
    except Exception:
        return None
    return await _game_state(page)


def _premium_site_static_checks(
    html: str,
    css: str,
    script: str,
) -> list[AcceptanceCheck]:
    return [
        _contains_check("semantic-nav", html, r"<nav(?:\s|>)", "semantic nav exists"),
        _contains_check(
            "primary-heading", html, r"<h1(?:\s|>)", "one primary heading exists"
        ),
        AcceptanceCheck(
            name="story-depth",
            passed=len(re.findall(r"<section(?:\s|>)", html, re.I)) >= 4,
            detail="at least four main story sections exist",
        ),
        AcceptanceCheck(
            name="design-token-depth",
            passed=len(set(re.findall(r"--[a-zA-Z][\w-]*\s*:", css))) >= 6,
            detail="at least six CSS custom properties define a coherent system",
        ),
        _contains_check(
            "cta-interaction",
            script,
            r"addEventListener\s*\(",
            "JavaScript interaction is wired",
        ),
    ]


def _rts_static_checks(html: str, script: str) -> list[AcceptanceCheck]:
    return [
        AcceptanceCheck(
            name="battlefield-renderer",
            passed=bool(
                re.search(r"<canvas(?:\s|>)", html, re.I)
                or re.search(r"requestAnimationFrame\s*\(", script)
            ),
            detail="canvas or animation-frame battlefield renderer exists",
        ),
        _contains_check(
            "running-simulation",
            script,
            r"requestAnimationFrame\s*\(|setInterval\s*\(",
            "the game has a running simulation loop",
        ),
        _contains_check(
            "acceptance-state-hook",
            script,
            r"__DAN_ACCEPTANCE__",
            "deterministic acceptance state hook exists",
        ),
        _contains_check(
            "keyboard-controls",
            script,
            r"(?:keydown|keyup)",
            "keyboard command handling exists",
        ),
        _contains_check(
            "pointer-controls",
            script,
            r"(?:click|pointerdown|mousedown)",
            "pointer command handling exists",
        ),
    ]


def _contains_check(
    name: str,
    text: str,
    pattern: str,
    detail: str,
) -> AcceptanceCheck:
    return AcceptanceCheck(
        name=name,
        passed=re.search(pattern, text, re.I | re.S) is not None,
        detail=detail,
    )


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: Any) -> None:
        return


@contextmanager
def _serve_directory(directory: Path) -> Iterator[str]:
    handler = partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _section_payload(section: AcceptanceSection | None) -> dict[str, Any] | None:
    if section is None:
        return None
    return {
        "name": section.name,
        "passed": section.passed,
        "pass_rate": round(section.pass_rate, 4),
        "checks": [asdict(check) for check in section.checks],
    }


def report_payload(report: FlagshipAcceptanceReport) -> dict[str, Any]:
    return {
        "case_id": report.case_id,
        "functional_prototype_passed": report.functional_prototype_passed,
        "showcase_passed": report.passed,
        "passed": report.passed,
        "static": _section_payload(report.static),
        "browser": _section_payload(report.browser),
        "rubric": _section_payload(report.rubric),
        "steering": _section_payload(report.steering),
        "capability": (
            dict(report.capability) if report.capability is not None else None
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--rubric", type=Path)
    parser.add_argument("--event-log", type=Path)
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        default=Path("output/playwright/super-dan-flagship"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)

    case = get_flagship_case(args.case_id)
    static = validate_static_artifact(case, args.workspace)
    browser = (
        asyncio.run(
            validate_browser_artifact(
                case,
                args.workspace,
                screenshot_dir=args.screenshot_dir,
            )
        )
        if args.browser
        else None
    )
    rubric_payload = (
        json.loads(args.rubric.read_text(encoding="utf-8"))
        if args.rubric is not None
        else None
    )
    rubric = (
        validate_rubric(
            case,
            rubric_payload,
            evidence_root=args.rubric.parent,
        )
        if isinstance(rubric_payload, Mapping)
        else None
    )
    steering = (
        validate_steering_trace(case, args.event_log)
        if args.event_log is not None
        else None
    )
    capability = (
        score_capability_trace(case, args.event_log)
        if args.event_log is not None
        else None
    )
    report = FlagshipAcceptanceReport(
        case_id=case.case_id,
        static=static,
        browser=browser,
        rubric=rubric,
        steering=steering,
        capability=capability,
    )
    text = json.dumps(report_payload(report), indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
