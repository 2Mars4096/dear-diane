from __future__ import annotations

import asyncio
import json

import pytest

from dan.cli import main as main_cli
from dan.cli import reader as reader_cli
from dan.tools import pdf_read as pdf_read_tool


def test_build_parser_defaults() -> None:
    parser = reader_cli.build_parser()
    args = parser.parse_args(["summarize", "paper.pdf"])

    assert args.action == "summarize"
    assert args.source == "paper.pdf"
    assert args.output_format == "md"
    assert args.pdf_mode == "text"
    assert args.vision_model == ""
    assert args.start_page is None
    assert args.end_page is None
    assert args.parallelism == reader_cli.DEFAULT_PARALLELISM
    assert args.page_window == reader_cli.DEFAULT_PAGE_WINDOW
    assert args.max_fragment_chars == reader_cli.DEFAULT_MAX_FRAGMENT_CHARS


def test_build_sections_from_lines_infers_introduction() -> None:
    pages = [
        "\n".join(
            [
                "THE JOURNAL OF TESTS",
                "Information Asymmetry, Mispricing, and Security Issuance",
                "JIYOON LEE",
                "ABSTRACT",
                "This paper studies a bounded extraction workflow.",
                "It keeps the structure simple and direct.",
                "A MANAGER TYPICALLY HAS SUPERIOR INFORMATION",
                "This is the introduction body and it continues here.",
            ]
        ),
        "\n".join(
            [
                "I. Baseline Strategy and Data",
                "This section introduces the baseline empirical design.",
                "A. Data and Variables",
                "This subsection defines the main variables.",
                "REFERENCES",
                "Author, Example. 2024. A paper.",
            ]
        ),
    ]

    document = reader_cli._build_sections_from_lines(
        "/tmp/paper.pdf",
        pages,
        {"subject": "The Journal of Tests 2024.1:1-10"},
        page_window=3,
    )

    headings = [section.heading for section in document.sections]

    assert document.title == "Information Asymmetry, Mispricing, and Security Issuance"
    assert document.authors == ["Jiyoon Lee"]
    assert document.year == "2024"
    assert "Abstract" in headings
    assert "Introduction" in headings
    assert "I. Baseline Strategy and Data" in headings
    assert "A. Data and Variables" in headings
    assert "References" in headings


def test_build_sections_preserves_parent_hierarchy_without_direct_body_text() -> None:
    pages = [
        "\n".join(
            [
                "PAPER TITLE",
                "AUTHOR NAME",
                "ABSTRACT",
                "This paper studies a flat reader pipeline.",
                "A MANAGER TYPICALLY KNOWS MORE THAN OUTSIDE INVESTORS",
                "The introduction body starts here.",
            ]
        ),
        "\n".join(
            [
                "I. Baseline Strategy and Data",
                "A. Data and Variables",
                "This subsection defines the main variables.",
                "B. Validation Tests",
                "This subsection validates the proxy.",
            ]
        ),
    ]

    document = reader_cli._build_sections_from_lines(
        "/tmp/paper.pdf",
        pages,
        {"subject": "The Journal of Tests 2024.1:1-10"},
        page_window=3,
    )

    by_heading = {section.heading: section for section in document.sections}
    parent = by_heading["I. Baseline Strategy and Data"]
    first_child = by_heading["A. Data and Variables"]

    assert parent.text == ""
    assert parent.child_headings == ["A. Data and Variables", "B. Validation Tests"]
    assert parent.page_start == 2
    assert parent.page_end == 2
    assert first_child.parent_heading == "I. Baseline Strategy and Data"
    assert first_child.section_path == ["I. Baseline Strategy and Data", "A. Data and Variables"]


def test_heading_detector_accepts_pdf_spacing_drift() -> None:
    assert reader_cli._detect_heading_marker("IV . Alternative Explanations") == (
        "IV. Alternative Explanations",
        1,
    )
    assert reader_cli._detect_heading_marker("2 . 1 Identification Strategy") == (
        "2.1 Identification Strategy",
        2,
    )


def test_heading_detector_prefers_letter_heading_for_nonstandard_single_char_roman() -> None:
    assert reader_cli._detect_heading_marker("C. Public Debt versus Private Debt") == (
        "C. Public Debt versus Private Debt",
        2,
    )


def test_build_sections_strips_pdf_boilerplate_and_repairs_hyphenation() -> None:
    pages = [
        "\n".join(
            [
                "I. Results",
                "This sec-",
                "tion studies mis-",
                "pricing under bounded review.",
                "Downloaded from https://example.com by Someone",
                "See the Terms and Conditions on Wiley Online Library for rules of use",
                "3406",
            ]
        )
    ]

    document = reader_cli._build_sections_from_lines(
        "/tmp/paper.pdf",
        pages,
        {},
        page_window=3,
    )

    assert len(document.sections) == 1
    assert document.sections[0].text == "This section studies mispricing under bounded review."


def test_render_summary_markdown_contains_page_ranges_and_points() -> None:
    document = reader_cli.ReaderDocument(
        source_path="/tmp/paper.pdf",
        source_kind="pdf",
        page_count=12,
        title="Paper Title",
        authors=["Author One"],
        year="2024",
        journal="Journal of Tests",
        doi="10.1234/example",
        abstract="This is the abstract.",
        sections=[
            reader_cli.ReaderSection(
                heading="Introduction",
                level=1,
                page_start=1,
                page_end=3,
                span_id="section-001",
                section_path=["Introduction"],
                summary="This section frames the problem.",
                key_points=["States the main question.", "Motivates the design."],
                quotes=["This section frames the problem."],
                claims=["The section introduces the main question."],
                confidence=0.75,
            )
        ],
    )
    task = reader_cli.ReaderTask(
        action="summarize",
        source_path="/tmp/paper.pdf",
        output_format="md",
        workspace_root="/tmp",
    )
    result = reader_cli.ReaderResult(
        action="summarize",
        output_format="md",
        task=task,
        document=document,
        rendered="",
        notes=["Used deterministic fallback."],
    )

    rendered = reader_cli._render_result(result)

    assert "# Paper Title" in rendered
    assert "## Section Summaries" in rendered
    assert "Introduction (pages 1-3)" in rendered
    assert "- States the main question." in rendered
    assert "Used deterministic fallback." in rendered


def test_render_summary_json_redacts_api_key_and_keeps_section_contract() -> None:
    document = reader_cli.ReaderDocument(
        source_path="/tmp/paper.pdf",
        source_kind="pdf",
        page_count=4,
        title="Paper Title",
        sections=[
            reader_cli.ReaderSection(
                heading="Introduction",
                level=1,
                page_start=1,
                page_end=2,
                span_id="section-001",
                section_path=["Introduction"],
                child_headings=["A. Setup"],
                summary="This section frames the problem.",
                key_points=["States the main question."],
                keywords=["mispricing"],
                quotes=["This section frames the problem."],
                claims=["The section introduces the main question."],
                confidence=0.75,
                needs_followup=False,
                needs_vision=False,
            )
        ],
    )
    task = reader_cli.ReaderTask(
        action="summarize",
        source_path="/tmp/paper.pdf",
        output_format="json",
        workspace_root="/tmp",
        model="test-model",
        api_key="sk-secret",
        base_url="https://example.invalid/v1",
        pdf_mode="hybrid",
        vision_model="vision-model",
        start_page=10,
        end_page=12,
    )
    result = reader_cli.ReaderResult(
        action="summarize",
        output_format="json",
        task=task,
        document=document,
        rendered="",
    )

    payload = json.loads(reader_cli._render_result(result))

    assert "api_key" not in payload["task"]
    assert payload["task"]["api_key_configured"] is True
    assert payload["task"]["pdf_mode"] == "hybrid"
    assert payload["task"]["vision_model"] == "vision-model"
    assert payload["task"]["start_page"] == 10
    assert payload["task"]["end_page"] == 12
    assert payload["document"]["sections"][0]["span_id"] == "section-001"
    assert payload["document"]["sections"][0]["quotes"] == ["This section frames the problem."]
    assert payload["document"]["sections"][0]["confidence"] == 0.75


def test_render_convert_latex_escapes_reserved_characters() -> None:
    document = reader_cli.ReaderDocument(
        source_path="/tmp/paper.pdf",
        source_kind="pdf",
        page_count=2,
        title="A&B_1",
        sections=[
            reader_cli.ReaderSection(
                heading="Results_1",
                level=1,
                page_start=1,
                page_end=2,
                text="Revenue grew by 10% & profit stayed above $5.",
            )
        ],
    )

    rendered = reader_cli._render_convert_latex(document)

    assert r"\title{A\&B\_1}" in rendered
    assert r"\section{Results\_1}" in rendered
    assert r"10\% \& profit stayed above \$5." in rendered


def test_main_exposes_read_alias() -> None:
    assert main_cli._SUBCOMMANDS["read"] == main_cli._SUBCOMMANDS["reader"]


def test_run_reader_task_uses_shared_pdf_read_pipeline_for_hybrid_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_read_pdf_file(*args, **kwargs):
        assert kwargs["mode"] == "hybrid"
        assert kwargs["vision_model"] == "vision-model"
        assert kwargs["start_page"] == 2
        assert kwargs["end_page"] == 4
        return {
            "metadata": {"title": "Vision Paper", "author": "AUTHOR NAME"},
            "pages": [
                {
                    "page_number": 1,
                    "text": "\n".join(
                        [
                            "ABSTRACT",
                            "This paper uses hybrid extraction.",
                            "A MANAGER TYPICALLY KNOWS MORE THAN OUTSIDE INVESTORS",
                            "The introduction body starts here.",
                        ]
                    ),
                    "source_mode": "vision",
                },
                {
                    "page_number": 2,
                    "text": "\n".join(
                        [
                            "I. Results",
                            "A. Figure Summary",
                            "This subsection was recovered through shared vision pages.",
                        ]
                    ),
                    "source_mode": "vision",
                },
            ],
            "warning": "Vision mode processed 2 of 2 requested pages.",
        }

    monkeypatch.setattr(pdf_read_tool, "read_pdf_file", fake_read_pdf_file)
    monkeypatch.setattr(reader_cli, "validate_path", lambda path: path)

    task = reader_cli.ReaderTask(
        action="extract",
        source_path="/tmp/paper.pdf",
        output_format="json",
        workspace_root="/tmp",
        pdf_mode="hybrid",
        vision_model="vision-model",
        start_page=2,
        end_page=4,
    )

    result = asyncio.run(reader_cli._run_reader_task(task))

    assert result.document.title == "Vision Paper"
    assert result.document.notes[0] == "PDF extraction mode: hybrid."
    assert "Vision mode processed 2 of 2 requested pages." in result.document.notes
    headings = [section.heading for section in result.document.sections]
    assert "Introduction" in headings
    assert "I. Results" in headings
    assert "A. Figure Summary" in headings
