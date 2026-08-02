"""Intent compiler path evaluator for golden intent fixtures.

Runs each golden intent through:
  fixture → WorkflowIntent (mock extraction) → CoverageChecker → IntentCompiler
  → exec() → validate → topology + structure checks → round-trip

No LLM access required — the extraction step is mocked by converting fixtures
directly into ``WorkflowIntent`` objects.
"""

from __future__ import annotations

import time

from dan.meta.intent_compiler import CoverageChecker, IntentCompiler
from dan.meta.planner import validate_codegen_output

from tests.quality_suite.codegen_runner import (
    IntentResult,
    check_structure_constraints,
    check_topology,
    classify_mismatch_error_type,
    fixture_to_workflow_intent,
)
from tests.quality_suite.graph_equivalence import round_trip_check


def run_intent_evaluation(intents: list[dict]) -> list[IntentResult]:
    """Run each intent through the intent extraction → coverage → compilation path.

    For each intent:
    1. Parse into a ``WorkflowIntent`` (mock extraction — no LLM)
    2. Run ``CoverageChecker``
    3. If covered: compile via ``IntentCompiler``, validate, run topology +
       structure checks, then round-trip
    4. If not covered: record as fallback
    5. Record ``IntentResult`` with coverage info
    """
    compiler = IntentCompiler()
    coverage = CoverageChecker()
    results: list[IntentResult] = []

    for fixture in intents:
        file_name = fixture.get("_source_file", "unknown")
        family = fixture.get("family", "unknown")
        variant = fixture.get("variant", "unknown")

        t0 = time.perf_counter()

        try:
            # Step 1: mock extraction — fixture → WorkflowIntent
            intent = fixture_to_workflow_intent(fixture)

            # Step 2: coverage check
            cov_result = coverage.check(intent)

            if cov_result.recommendation != "compile":
                results.append(IntentResult(
                    intent_file=file_name,
                    family=family,
                    variant=variant,
                    path="intent",
                    passed=False,
                    error_type="coverage_fallback",
                    error_message=(
                        f"recommendation={cov_result.recommendation}, "
                        f"unsupported={cov_result.unsupported_stages}"
                    ),
                    latency_ms=(time.perf_counter() - t0) * 1000,
                    topology_check={
                        "coverage": cov_result.model_dump(mode="json"),
                    },
                ))
                continue

            # Step 3: compile
            code = compiler.compile(intent)

            # Step 4: exec to get Graph
            ns: dict = {}
            exec(code, ns)  # noqa: S102
            graph = ns.get("graph")
            if graph is None:
                raise ValueError(
                    "Compiled code did not produce a 'graph' variable"
                )

            # Step 5: validate
            graph_dict = graph.model_dump(mode="json")
            validation = validate_codegen_output(graph_dict)
            if not validation.success:
                error_msgs = "; ".join(
                    e.message for e in (validation.errors or [])[:3]
                )
                results.append(IntentResult(
                    intent_file=file_name,
                    family=family,
                    variant=variant,
                    path="intent",
                    passed=False,
                    error_type="validation",
                    error_message=error_msgs[:200],
                    latency_ms=(time.perf_counter() - t0) * 1000,
                    topology_check={
                        "coverage": cov_result.model_dump(mode="json"),
                    },
                ))
                continue

            # Step 6: topology + round-trip
            expected = fixture.get("expected", {})
            expected_topo = expected.get("topology", {})
            topo = check_topology(graph, expected_topo)
            constraints = check_structure_constraints(graph, expected)
            topology_check = {
                **topo,
                "topology_match": topo["match"],
                "structure_constraints": constraints,
                "coverage": cov_result.model_dump(mode="json"),
                "match": topo["match"] and constraints["match"],
            }
            rt = round_trip_check(code)

            passed = topology_check["match"] and rt.equivalent
            error_parts: list[str] = []
            if not topology_check["topology_match"]:
                error_parts.append(
                    f"topology mismatch: expected={topo['expected']}, "
                    f"actual={topo['actual']}"
                )
            if not constraints["match"]:
                error_parts.append(
                    "structure mismatch: "
                    f"min_nodes expected>={constraints['expected']['min_nodes']} "
                    f"actual={constraints['actual']['node_count']}; "
                    f"missing node types={constraints['missing_node_types']}"
                )
            if not rt.equivalent:
                error_parts.append(
                    f"round-trip mismatch: {rt.mismatches[:3]}"
                )
            mismatch_error_type = classify_mismatch_error_type(
                topology_match=topology_check["topology_match"],
                structure_match=constraints["match"],
                roundtrip_equivalent=rt.equivalent,
            )

            results.append(IntentResult(
                intent_file=file_name,
                family=family,
                variant=variant,
                path="intent",
                passed=passed,
                error_type=mismatch_error_type,
                error_message="; ".join(error_parts) if error_parts else None,
                latency_ms=(time.perf_counter() - t0) * 1000,
                topology_check=topology_check,
            ))

        except Exception as e:
            results.append(IntentResult(
                intent_file=file_name,
                family=family,
                variant=variant,
                path="intent",
                passed=False,
                error_type=type(e).__name__,
                error_message=str(e)[:200],
                latency_ms=(time.perf_counter() - t0) * 1000,
            ))

    return results
