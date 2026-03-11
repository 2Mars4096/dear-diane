# 32-5: Domain Generation Profiles

**Parent:** [32-workflow-optimization](32-workflow-optimization.md)
**Status:** completed
**Goal:** Pre-configure generation settings per domain so generated workflows automatically use domain-appropriate tools, model tiers, structure patterns, and validation rules. Domain keys must match `detect_domain()` values from `_DOMAIN_KEYWORDS` in `concierge/domain_learning.py`.

## Problem

A user who asks "build a literature review workflow" and a user who asks "build an equity research workflow" get the same generic generation treatment. But these domains have very different needs:

- **Research** — web_search, pdf_read, RAG, parallel paper processing, citation tracking, review loops
- **Equity** — web_search (financials), python_eval (ratios/trends), charting tools, structured report format, data validation
- **Data pipeline** — file_read, csv_read, python_eval, file_write, error handling, retry-heavy config
- **Content** — review loops, human approval, email/notify, template-based formatting

The codegen prompt doesn't know which tools are relevant, which model tiers make sense, or what validation rules to apply for a given domain. The user must specify all of this.

## Design

### Domain profile model

```python
class DomainGenerationProfile(BaseModel):
    domain: str
    description: str
    preferred_tools: list[str]
    preferred_patterns: list[str]
    model_tier_hints: dict[str, str]   # node_role → tier
    default_profile: str               # "minimal" | "standard" | "robust" (from 32-3)
    validation_hints: list[str]        # domain-specific validation guidance
    prompt_hints: list[str]            # domain-specific codegen guidance
```

### Seed profiles (4)

1. **Literature Review** (`literature_review`) — parallel paper processing, RAG, web_search + pdf_read, review loops, citation validation
2. **Paper Rendering** (`paper_rendering`) — LaTeX generation, bibliography, figure placement, structured sections
3. **Equity Research** (`equity_research`) — web_search for financials, python_eval for ratios, structured report templates, data source validation
4. **Data Analysis** (`data_analysis`) — file_read, csv_read, python_eval, file_write, robust retry, error handling
5. **Code Generation** (`code_generation`) — iterative code writing, test-driven loops, tool_chain with file tools

### Integration flow

1. `detect_domain()` (31-21) identifies domain from the user's prompt
2. Profile loaded from `src/dan/data/generation_profiles/{domain}.json`
3. Profile injected into codegen prompt as advisory context
4. Intent compiler uses `preferred_patterns` to bias pattern selection (32-2)
5. Defaults enricher uses `default_profile` setting (32-3)
6. User overrides in `~/.dan/generation_profiles/` take precedence over seed profiles

## Tasks

- [x] 1. Domain profile model
  - [x] 1-1. `DomainGenerationProfile` Pydantic model in `generation_defaults.py` (extends 32-3).
  - [x] 1-2. Profile registry: `get_domain_profile(domain) → DomainGenerationProfile | None`. Loads from seed files, then checks user override directory.
  - [x] 1-3. Profile loading from `src/dan/data/generation_profiles/` (JSON, same pattern as domain templates).

- [x] 2. Seed profiles (5 — matching `_DOMAIN_KEYWORDS` keys)
  - [x] 2-1. `literature_review.json` — tools: `[web_search, pdf_read, web_fetch]`. Patterns: `[research_review, fan_out_fan_in, rag_qa]`. Tiers: `{planning: routine, research: standard, synthesis: premium}`. Profile: `robust`. Hints: citation completeness, source diversity.
  - [x] 2-2. `paper_rendering.json` — tools: `[file_read, file_write]`. Patterns: `[linear_chain, review_loop]`. Tiers: `{drafting: standard, formatting: routine, final: premium}`. Profile: `standard`. Hints: LaTeX validity, bibliography completeness.
  - [x] 2-3. `equity_research.json` — tools: `[web_search, python_eval, web_fetch]`. Patterns: `[web_briefing, code_analysis, research_review]`. Tiers: `{data_gathering: routine, analysis: standard, report: premium}`. Profile: `robust`. Hints: data source citation, numerical consistency.
  - [x] 2-4. `data_analysis.json` — tools: `[file_read, csv_read, python_eval, file_write]`. Patterns: `[data_pipeline, tool_chain, tool_augmented]`. Tiers: `{all: routine}`. Profile: `standard`. Hints: input file validation, output format checks.
  - [x] 2-5. `code_generation.json` — tools: `[file_read, file_write, python_eval]`. Patterns: `[tool_chain, iterative_improvement, linear_chain]`. Tiers: `{planning: routine, coding: standard, review: routine}`. Profile: `standard`. Hints: test coverage, error handling.

- [x] 3. Codegen prompt injection
  - [x] 3-1. When domain is detected, inject profile's `description`, `preferred_tools`, and `prompt_hints` into the codegen system prompt.
  - [x] 3-2. Tool hint format: "For this [domain] workflow, consider using: [tool list with one-line descriptions]."
  - [x] 3-3. Pattern hint: "Common patterns for [domain] workflows: [pattern list]."

- [x] 4. Integration wiring
  - [x] 4-1. Wire domain detection → profile lookup in planner's pre-generation phase.
  - [x] 4-2. Pass profile to intent compiler for `preferred_patterns` bias (32-2 task 3).
  - [x] 4-3. Pass profile's `default_profile` to defaults enricher (32-3 task 1-3).
  - [x] 4-4. User override path: check `~/.dan/generation_profiles/{domain}.json` before seed files.

- [x] 5. Tests
  - [x] 5-1. All 5 seed profiles load and validate.
  - [x] 5-2. Codegen prompt includes tool and pattern hints when domain detected.
  - [x] 5-3. "Build a literature review" → research profile applied → web_search + pdf_read tools suggested.
  - [x] 5-4. No-domain fallback: unknown domain → no profile → standard defaults.
  - [x] 5-5. User override: `~/.dan/generation_profiles/research.json` overrides seed.

- [x] 6. Documentation
  - [x] 6-1. Update `docs/llm-api-guide.md` with domain profiles section.
  - [x] 6-2. Changelog entry.

## Files

| File | Action |
|------|--------|
| `src/dan/meta/generation_defaults.py` | Modify — add `DomainGenerationProfile`, profile registry |
| `src/dan/data/generation_profiles/literature_review.json` | Create — seed profile |
| `src/dan/data/generation_profiles/paper_rendering.json` | Create — seed profile |
| `src/dan/data/generation_profiles/equity_research.json` | Create — seed profile |
| `src/dan/data/generation_profiles/data_analysis.json` | Create — seed profile |
| `src/dan/data/generation_profiles/code_generation.json` | Create — seed profile |
| `src/dan/meta/planner.py` | Modify — inject domain profile into `CodegenPromptBuilder` prompt |
| `src/dan/meta/planner.py` | Modify — domain detection → profile lookup |
| `tests/test_meta/test_domain_profiles.py` | Create — profile tests |

## Decisions

- (filled in during execution)

## Notes

- **Depends on 32-3** in addition to 32-2 — `DomainGenerationProfile` extends `generation_defaults.py` which 32-3 creates. The `DefaultProfile` enum and `GenerationDefaults` model from 32-3 must exist first.
- **`src/dan/data/generation_profiles/` does not exist yet** — it must be created along with an `__init__.py` following the same pattern as `src/dan/data/domain_templates/`.
- Domain profiles are seed data, not dynamically generated. Users can add custom profiles in `~/.dan/generation_profiles/` following the same JSON schema.
- Profiles don't override user intent. If the user asks for something outside the domain's preferences, honor the user's request.
- The profile's `prompt_hints` are advisory context, not hard constraints. The LLM can ignore them if the user's request calls for it.
- Domain detection confidence matters: only apply profile when detection confidence is above the threshold (from 31-21). Low-confidence detection → no profile → generic defaults.
- Profile JSON files follow the same versioning pattern as domain templates (31-21): seed files in the package, user overrides in `~/.dan/`.
- Existing `DomainTemplate` (31-21) handles post-task learning and validation. `DomainGenerationProfile` handles pre-generation configuration. They're complementary: templates learn from experience, profiles configure generation.
