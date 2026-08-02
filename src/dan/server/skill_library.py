"""Built-in prompt guidance exposed to the Super DAN skill catalog."""

from __future__ import annotations

from typing import Any

__all__ = ["SKILL_LIBRARY"]


SKILL_LIBRARY: dict[str, dict[str, Any]] = {
    "management_science_writing": {
        "name": "Management Science Writing",
        "description": "INFORMS Management Science submission guidelines and academic writing conventions",
        "tags": ["writing", "review"],
        "inject_as": "system",
        "text": (
            "You are writing for Management Science (INFORMS). Follow these conventions:\n"
            "- Frame the contribution clearly: state the research question, the gap in the "
            "literature, and the precise claim in the introduction.\n"
            "- Position relative to at least 3-5 closely related papers; explain how this "
            "work differs.\n"
            "- For analytical models: state assumptions explicitly, prove main results, "
            "discuss boundary conditions.\n"
            "- For empirical work: describe identification strategy, address endogeneity, "
            "report robustness checks.\n"
            "- Results section: lead with the main finding, then supporting evidence, then "
            "sensitivity analysis.\n"
            "- Discussion: implications for theory AND practice; limitations; future "
            "research directions.\n"
            "- Use formal academic tone. Avoid first person where possible. "
            "Be precise and concise.\n"
            "- Target length: 30-45 pages double-spaced (approximately 8,000-12,000 words)."
        ),
    },
    "informs_latex_style": {
        "name": "INFORMS LaTeX Style",
        "description": "INFORMS LaTeX template conventions for manuscript formatting",
        "tags": ["latex"],
        "inject_as": "system",
        "text": (
            "Format the LaTeX manuscript using INFORMS conventions:\n"
            "- Use document class: \\documentclass[mnsc,nonblindrev]{informs3}\n"
            "- Required packages: natbib (plainnat style), amsmath, graphicx, booktabs.\n"
            "- Section ordering: Introduction, Literature Review, Model/Framework, "
            "Data & Methods (if empirical), Results, Discussion, Conclusion.\n"
            "- Figures: use \\begin{figure}[htbp] with descriptive captions below.\n"
            "- Tables: use booktabs (\\toprule, \\midrule, \\bottomrule) with captions above.\n"
            "- Citations: use \\citet{} for textual and \\citep{} for parenthetical.\n"
            "- Bibliography: \\bibliographystyle{plainnat}, \\bibliography{references}.\n"
            "- Abstract: max 150 words. Keywords: 3-5 terms.\n"
            "- Number all equations. Reference as Equation (1), not Eq. 1.\n"
            "- Appendix for proofs and supplementary material."
        ),
    },
    "skill_creation": {
        "name": "DAN Skill Creation",
        "description": "Default guidance for creating or adapting DAN-compatible SKILL.md skills",
        "tags": ["skill", "skills", "authoring", "capability"],
        "inject_as": "system",
        "text": (
            "Create or adapt DAN-compatible skills as concise SKILL.md folders.\n"
            "- Use YAML frontmatter with at least name and description; make the description "
            "clear about when the skill should trigger.\n"
            "- Keep SKILL.md focused on essential workflow guidance. Move large references, "
            "examples, templates, or assets into sibling references/, scripts/, or assets/ folders.\n"
            "- Prefer interoperable fields shared by Codex, Claude Code, Cursor, and DAN; "
            "DAN-specific fields such as tags or attach_to_* are optional extensions.\n"
            "- Treat imported external skills as source material first: preserve their intent, "
            "adapt only what is needed for DAN's brief-driven runtime, and record provenance.\n"
            "- Skills may guide behavior, standards, and review criteria, but they must not "
            "silently expand tool permissions, workspace access, or safety boundaries."
        ),
    },
}
