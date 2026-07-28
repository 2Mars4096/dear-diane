"""Deterministic handoff gates for Super DAN academic and market workflows.

These checks do not claim that a paper is publishable or a strategy is
profitable. They verify that the generated package is complete enough for a
qualified human to review without first reconstructing missing provenance,
evaluation assumptions, or decision points.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class HumanAssistCase:
    case_id: str
    title: str
    required_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AcceptanceCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class AcceptanceSection:
    name: str
    checks: tuple[AcceptanceCheck, ...]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    @property
    def pass_rate(self) -> float:
        if not self.checks:
            return 0.0
        return sum(check.passed for check in self.checks) / len(self.checks)


@dataclass(frozen=True, slots=True)
class HumanAssistAcceptanceReport:
    case_id: str
    artifact: AcceptanceSection
    domain: AcceptanceSection
    human_handoff: AcceptanceSection

    @property
    def machine_ready(self) -> bool:
        return self.artifact.passed and self.domain.passed

    @property
    def human_handoff_ready(self) -> bool:
        return self.human_handoff.passed

    @property
    def passed(self) -> bool:
        return self.machine_ready and self.human_handoff_ready


_CASES = (
    HumanAssistCase(
        case_id="long-academic-first-draft",
        title="Academic first-draft package",
        required_paths=(
            "manuscript.md",
            "evidence-ledger.json",
            "reproducibility.md",
            "human-review.md",
        ),
    ),
    HumanAssistCase(
        case_id="long-market-strategy-framework",
        title="Market strategy test framework",
        required_paths=(
            "strategy.md",
            "assumptions.json",
            "backtest.py",
            "tests",
            "human-review.md",
        ),
    ),
)

_ACADEMIC_HEADING_GROUPS = (
    ("abstract",),
    ("introduction",),
    ("literature review", "related literature", "background"),
    ("data", "materials"),
    ("methods", "methodology", "empirical strategy"),
    ("results", "findings"),
    ("discussion",),
    ("limitations", "threats to validity"),
    ("conclusion",),
    ("references", "bibliography"),
)
_ACADEMIC_REPRO_TERMS = (
    "data provenance",
    "environment",
    "execution",
    "random seed",
    "limitations",
)
_ACADEMIC_HUMAN_TERMS = (
    "claim",
    "citation",
    "method",
    "result",
    "limitation",
    "decision",
)
_MARKET_STRATEGY_TERMS = (
    "hypothesis",
    "universe",
    "signal",
    "entry",
    "exit",
    "point-in-time",
    "leakage",
    "walk-forward",
    "transaction cost",
    "slippage",
    "failure criteria",
    "paper",
)
_MARKET_HUMAN_TERMS = (
    "data",
    "leakage",
    "cost",
    "stability",
    "failure",
    "paper",
    "decision",
)
_STRATEGY_TEST_TIMEOUT_SECONDS = 60


def human_assist_cases() -> tuple[HumanAssistCase, ...]:
    return _CASES


def get_human_assist_case(case_id: str) -> HumanAssistCase:
    for case in _CASES:
        if case.case_id == case_id:
            return case
    known = ", ".join(case.case_id for case in _CASES)
    raise KeyError(f"Unknown human-assist case {case_id!r}; expected one of: {known}")


def validate_human_assist_workspace(
    case: HumanAssistCase,
    workspace: Path,
) -> HumanAssistAcceptanceReport:
    root = workspace.resolve()
    artifact = _artifact_section(case, root)
    if case.case_id == "long-academic-first-draft":
        domain = _academic_domain_section(root)
        handoff = _human_handoff_section(
            root / "human-review.md",
            required_terms=_ACADEMIC_HUMAN_TERMS,
        )
    elif case.case_id == "long-market-strategy-framework":
        domain = _market_domain_section(root)
        handoff = _human_handoff_section(
            root / "human-review.md",
            required_terms=_MARKET_HUMAN_TERMS,
        )
    else:  # pragma: no cover - case lookup prevents this path
        raise KeyError(case.case_id)
    return HumanAssistAcceptanceReport(
        case_id=case.case_id,
        artifact=artifact,
        domain=domain,
        human_handoff=handoff,
    )


def _artifact_section(
    case: HumanAssistCase,
    root: Path,
) -> AcceptanceSection:
    checks: list[AcceptanceCheck] = []
    for relative in case.required_paths:
        path = root / relative
        exists = path.is_dir() if relative == "tests" else path.is_file()
        nonempty = exists and (
            any(path.iterdir()) if path.is_dir() else path.stat().st_size > 0
        )
        checks.append(
            AcceptanceCheck(
                name=f"required:{relative}",
                passed=bool(nonempty),
                detail="present and non-empty" if nonempty else "missing or empty",
            )
        )
    return AcceptanceSection(name="artifact", checks=tuple(checks))


def _academic_domain_section(root: Path) -> AcceptanceSection:
    manuscript = _read_text(root / "manuscript.md")
    headings = _markdown_headings(manuscript)
    checks: list[AcceptanceCheck] = []
    for alternatives in _ACADEMIC_HEADING_GROUPS:
        matched = next(
            (
                heading
                for heading in headings
                if any(term in heading for term in alternatives)
            ),
            "",
        )
        checks.append(
            AcceptanceCheck(
                name=f"manuscript-section:{alternatives[0]}",
                passed=bool(matched),
                detail=matched or "missing",
            )
        )

    ledger, ledger_error = _read_json(root / "evidence-ledger.json")
    claims = ledger.get("claims") if isinstance(ledger, Mapping) else ledger
    normalized_claims = (
        [item for item in claims if isinstance(item, Mapping)]
        if isinstance(claims, Sequence) and not isinstance(claims, (str, bytes))
        else []
    )
    checks.append(
        AcceptanceCheck(
            name="evidence-ledger:parse",
            passed=ledger_error == "" and bool(normalized_claims),
            detail=ledger_error or f"{len(normalized_claims)} claim rows",
        )
    )
    claim_errors: list[str] = []
    claim_ids: list[str] = []
    supported_source_refs: list[str] = []
    for index, claim in enumerate(normalized_claims):
        claim_id = str(claim.get("claim_id") or claim.get("id") or "").strip()
        claim_text = str(claim.get("claim") or claim.get("text") or "").strip()
        status = str(claim.get("status") or "").strip().lower()
        refs = claim.get("source_refs")
        source_refs = (
            [str(ref).strip() for ref in refs if str(ref).strip()]
            if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes))
            else []
        )
        if not claim_id or not claim_text:
            claim_errors.append(f"row {index + 1} lacks claim_id/text")
        if status not in {"supported", "unresolved", "needs_review"}:
            claim_errors.append(f"row {index + 1} has invalid status")
        if status == "supported" and not source_refs:
            claim_errors.append(f"row {index + 1} is supported without source_refs")
        if status == "supported":
            supported_source_refs.extend(source_refs)
        if claim_id:
            claim_ids.append(claim_id)
    if len(set(claim_ids)) != len(claim_ids):
        claim_errors.append("duplicate claim ids")
    checks.append(
        AcceptanceCheck(
            name="evidence-ledger:claim-contract",
            passed=bool(normalized_claims) and not claim_errors,
            detail="valid" if not claim_errors else "; ".join(claim_errors[:5]),
        )
    )
    missing_claim_links = [
        claim_id
        for claim_id in claim_ids
        if not re.search(rf"\b{re.escape(claim_id)}\b", manuscript, re.IGNORECASE)
    ]
    checks.append(
        AcceptanceCheck(
            name="evidence-ledger:manuscript-links",
            passed=bool(claim_ids) and not missing_claim_links,
            detail=(
                "every ledger claim is linked from the manuscript"
                if claim_ids and not missing_claim_links
                else "unlinked claim ids: " + ", ".join(missing_claim_links)
            ),
        )
    )
    known_source_ids = _ledger_source_ids(ledger)
    unresolved_source_refs = sorted(
        {
            ref
            for ref in supported_source_refs
            if not _is_internal_result_ref(ref)
            and ref.lower() not in known_source_ids
            and ref.lower() not in manuscript.lower()
        }
    )
    checks.append(
        AcceptanceCheck(
            name="evidence-ledger:source-resolution",
            passed=not unresolved_source_refs,
            detail=(
                "every supported source ref resolves to the ledger or manuscript"
                if not unresolved_source_refs
                else "unresolved source refs: " + ", ".join(unresolved_source_refs)
            ),
        )
    )

    reproducibility = _read_text(root / "reproducibility.md").lower()
    missing_repro = [
        term for term in _ACADEMIC_REPRO_TERMS if term not in reproducibility
    ]
    checks.append(
        AcceptanceCheck(
            name="reproducibility-contract",
            passed=not missing_repro,
            detail=(
                "all required reproducibility topics present"
                if not missing_repro
                else "missing: " + ", ".join(missing_repro)
            ),
        )
    )
    placeholder_citations = re.findall(
        r"\b(?:doi:\s*10\.0000|citation needed|author,\s*year)\b",
        manuscript,
        flags=re.IGNORECASE,
    )
    checks.append(
        AcceptanceCheck(
            name="no-disguised-placeholder-citations",
            passed=not placeholder_citations,
            detail=(
                "none"
                if not placeholder_citations
                else ", ".join(sorted(set(placeholder_citations)))
            ),
        )
    )
    return AcceptanceSection(name="academic-domain", checks=tuple(checks))


def _market_domain_section(root: Path) -> AcceptanceSection:
    strategy = _read_text(root / "strategy.md").lower()
    checks: list[AcceptanceCheck] = []
    missing_strategy = [term for term in _MARKET_STRATEGY_TERMS if term not in strategy]
    checks.append(
        AcceptanceCheck(
            name="strategy-contract",
            passed=not missing_strategy,
            detail=(
                "all required strategy topics present"
                if not missing_strategy
                else "missing: " + ", ".join(missing_strategy)
            ),
        )
    )
    prohibited_claims = [
        term
        for term in ("guaranteed profit", "risk-free", "cannot lose")
        if term in strategy
    ]
    checks.append(
        AcceptanceCheck(
            name="no-unsupported-profit-guarantee",
            passed=not prohibited_claims,
            detail="none" if not prohibited_claims else ", ".join(prohibited_claims),
        )
    )

    assumptions, assumptions_error = _read_json(root / "assumptions.json")
    required_keys = {
        "data_as_of",
        "point_in_time",
        "transaction_cost_bps",
        "slippage_bps",
        "walk_forward",
        "leakage_controls",
        "paper_only",
    }
    missing_keys = (
        sorted(required_keys - set(assumptions))
        if isinstance(assumptions, Mapping)
        else sorted(required_keys)
    )
    checks.append(
        AcceptanceCheck(
            name="assumptions-schema",
            passed=not assumptions_error and not missing_keys,
            detail=assumptions_error
            or (
                "complete"
                if not missing_keys
                else "missing: " + ", ".join(missing_keys)
            ),
        )
    )
    assumptions_valid = isinstance(assumptions, Mapping) and (
        _valid_data_as_of(assumptions.get("data_as_of"))
        and assumptions.get("point_in_time") is True
        and assumptions.get("paper_only") is True
        and _nonnegative_number(assumptions.get("transaction_cost_bps"))
        and _nonnegative_number(assumptions.get("slippage_bps"))
        and isinstance(assumptions.get("walk_forward"), Mapping)
        and bool(assumptions.get("walk_forward"))
        and isinstance(assumptions.get("leakage_controls"), list)
        and bool(assumptions.get("leakage_controls"))
    )
    checks.append(
        AcceptanceCheck(
            name="assumptions-safety-values",
            passed=assumptions_valid,
            detail=(
                "point-in-time, paper-only, cost, split, and leakage values valid"
                if assumptions_valid
                else "invalid data-as-of/point-in-time/paper-only/cost/split/leakage values"
            ),
        )
    )

    backtest_text = _read_text(root / "backtest.py")
    function_names: set[str] = set()
    syntax_error = ""
    try:
        tree = ast.parse(backtest_text)
        function_names = {
            node.name.lower()
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
    except SyntaxError as exc:
        syntax_error = f"{exc.msg} at line {exc.lineno}"
    required_function_groups = (
        ("signal",),
        ("walk_forward", "walkforward", "backtest"),
        ("cost", "fee", "slippage"),
        ("evaluate", "metrics", "score"),
    )
    missing_groups = [
        alternatives[0]
        for alternatives in required_function_groups
        if not any(
            any(term in function_name for term in alternatives)
            for function_name in function_names
        )
    ]
    checks.append(
        AcceptanceCheck(
            name="backtest-code-contract",
            passed=not syntax_error and not missing_groups,
            detail=syntax_error
            or (
                "required functions present"
                if not missing_groups
                else "missing function groups: " + ", ".join(missing_groups)
            ),
        )
    )

    test_files = (
        sorted((root / "tests").glob("test_*.py")) if (root / "tests").is_dir() else []
    )
    test_contract_errors = _strategy_test_contract_errors(test_files)
    checks.append(
        AcceptanceCheck(
            name="strategy-test-contract",
            passed=bool(test_files) and not test_contract_errors,
            detail=(
                f"{len(test_files)} substantive test file(s)"
                if test_files and not test_contract_errors
                else "; ".join(test_contract_errors) or "no test files"
            ),
        )
    )
    if test_contract_errors:
        tests_passed = False
        test_detail = "not run because the strategy test contract failed"
    else:
        tests_passed, test_detail = _run_strategy_tests(root, test_files)
    checks.append(
        AcceptanceCheck(
            name="strategy-regression-tests",
            passed=tests_passed,
            detail=test_detail,
        )
    )
    return AcceptanceSection(name="market-domain", checks=tuple(checks))


def _ledger_source_ids(ledger: Any) -> set[str]:
    if not isinstance(ledger, Mapping):
        return set()
    raw_sources = ledger.get("sources")
    if isinstance(raw_sources, Mapping):
        return {str(key).strip().lower() for key in raw_sources if str(key).strip()}
    if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, (str, bytes)):
        return set()
    source_ids: set[str] = set()
    for source in raw_sources:
        if not isinstance(source, Mapping):
            continue
        source_id = str(source.get("source_id") or source.get("id") or "").strip()
        if source_id:
            source_ids.add(source_id.lower())
    return source_ids


def _is_internal_result_ref(ref: str) -> bool:
    normalized = str(ref or "").strip().lower()
    return bool(
        re.match(
            r"^(?:appendix|fig(?:ure)?|model|result|table)[-_:\s]?\w+",
            normalized,
        )
    )


def _strategy_test_contract_errors(test_files: Sequence[Path]) -> list[str]:
    errors: list[str] = []
    topic_functions: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {
        "leakage": [],
        "cost": [],
        "walk": [],
    }
    imports_backtest = False
    for path in test_files:
        text = _read_text(path)
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            errors.append(f"{path.name}: {exc.msg} at line {exc.lineno}")
            continue
        imports_backtest = imports_backtest or any(
            (
                isinstance(node, ast.Import)
                and any(alias.name == "backtest" for alias in node.names)
            )
            or (isinstance(node, ast.ImportFrom) and node.module == "backtest")
            for node in ast.walk(tree)
        )
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            lowered = node.name.lower()
            for topic in topic_functions:
                if topic in lowered:
                    topic_functions[topic].append(node)

    if not imports_backtest:
        errors.append("tests do not import the backtest implementation")
    for topic, functions in topic_functions.items():
        if not functions:
            errors.append(f"missing {topic} test")
            continue
        if not any(_test_function_is_substantive(function) for function in functions):
            errors.append(f"{topic} test has no substantive call/assertion")
    return errors


def _test_function_is_substantive(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    has_call = any(isinstance(node, ast.Call) for node in ast.walk(function))
    has_substantive_assert = any(
        isinstance(node, ast.Assert)
        and not (isinstance(node.test, ast.Constant) and node.test.value is True)
        for node in ast.walk(function)
    )
    return has_call and has_substantive_assert


def _run_strategy_tests(
    root: Path,
    test_files: Sequence[Path],
) -> tuple[bool, str]:
    if not test_files:
        return False, "no test files"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        *(str(path.relative_to(root)) for path in test_files),
    ]
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=_STRATEGY_TEST_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"pytest timed out after {_STRATEGY_TEST_TIMEOUT_SECONDS}s"
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    detail = output[-2_000:] or f"pytest exit code {completed.returncode}"
    return completed.returncode == 0, detail


def _human_handoff_section(
    path: Path,
    *,
    required_terms: Sequence[str],
) -> AcceptanceSection:
    text = _read_text(path).lower()
    unchecked = re.findall(r"(?m)^\s*[-*]\s+\[\s\]\s+\S", text)
    checks = [
        AcceptanceCheck(
            name="human-checkpoints",
            passed=len(unchecked) >= 6,
            detail=f"{len(unchecked)} unchecked decision/review items; required >= 6",
        )
    ]
    for term in required_terms:
        checks.append(
            AcceptanceCheck(
                name=f"human-review:{term}",
                passed=term in text,
                detail="present" if term in text else "missing",
            )
        )
    return AcceptanceSection(name="human-handoff", checks=tuple(checks))


def _markdown_headings(text: str) -> list[str]:
    return [
        " ".join(match.group(1).lower().split())
        for match in re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", text)
    ]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_json(path: Path) -> tuple[Any, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _nonnegative_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    )


def _valid_data_as_of(value: Any) -> bool:
    try:
        parsed = date.fromisoformat(str(value or "").strip())
    except ValueError:
        return False
    return parsed <= date.today()


def _section_payload(section: AcceptanceSection) -> dict[str, Any]:
    return {
        "name": section.name,
        "passed": section.passed,
        "pass_rate": round(section.pass_rate, 4),
        "checks": [asdict(check) for check in section.checks],
    }


def report_payload(report: HumanAssistAcceptanceReport) -> dict[str, Any]:
    return {
        "case_id": report.case_id,
        "machine_ready": report.machine_ready,
        "human_handoff_ready": report.human_handoff_ready,
        "passed": report.passed,
        "claim_boundary": (
            "This gate proves review readiness, not publication quality or "
            "future investment performance."
        ),
        "artifact": _section_payload(report.artifact),
        "domain": _section_payload(report.domain),
        "human_handoff": _section_payload(report.human_handoff),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    case = get_human_assist_case(args.case_id)
    report = validate_human_assist_workspace(case, args.workspace)
    text = json.dumps(report_payload(report), indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
