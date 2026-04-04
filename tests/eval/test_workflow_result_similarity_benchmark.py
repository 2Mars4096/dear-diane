from __future__ import annotations

from dan.models.graph import Graph
from tests.eval import workflow_result_similarity_benchmark as benchmark


def test_similarity_fixture_catalog_has_ten_long_workflows() -> None:
    fixtures = benchmark._comparison_fixtures()

    assert len(fixtures) >= 10
    assert all(len(fixture.final_sections) >= 6 for fixture in fixtures)
    assert all("at least 5 meaningful" in fixture.generation_prompt() for fixture in fixtures)


def test_reference_workflows_validate() -> None:
    for fixture in benchmark._comparison_fixtures():
        graph = benchmark._build_reference_graph(fixture, model="")
        Graph.model_validate(graph.model_dump(mode="json"))
        assert len(graph.nodes) >= 3


def test_primary_output_prefers_text_and_result_ports() -> None:
    assert benchmark._extract_primary_output({"text": "hello"}) == "hello"
    assert benchmark._extract_primary_output({"result": {"summary": "ok"}}) == "ok"
    rendered = benchmark._extract_primary_output({"alpha": 1, "beta": 2})
    assert '"alpha": 1' in rendered


def test_deterministic_similarity_metrics_are_non_negative() -> None:
    coverage = benchmark._section_coverage("## Executive Summary\n## Risks", ("Executive Summary", "Risks"))
    overlap = benchmark._keyword_overlap(
        "gross margin pressure and contract expansion",
        "contract expansion with margin pressure",
    )

    assert coverage == 1.0
    assert 0.0 <= overlap <= 1.0


def test_env_assignment_parser_preserves_inline_json() -> None:
    tier_map = benchmark._parse_env_assignment(
        'DAN_TIER_MAP={"1": "kimi-k2.5", "2": "kimi-k2.5"}'
    )
    provider_map = benchmark._parse_env_assignment(
        'DAN_EMBEDDING_MODEL_PROVIDER_MAP={"sentence-transformers/all-MiniLM-L6-v2":"local"}'
    )

    assert tier_map == (
        "DAN_TIER_MAP",
        '{"1": "kimi-k2.5", "2": "kimi-k2.5"}',
    )
    assert provider_map == (
        "DAN_EMBEDDING_MODEL_PROVIDER_MAP",
        '{"sentence-transformers/all-MiniLM-L6-v2":"local"}',
    )


def test_empty_exception_strings_still_have_repr() -> None:
    exc = TimeoutError()

    rendered = str(exc) or repr(exc)

    assert rendered == "TimeoutError()"
