# 38-4: Domain Learning Fidelity

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** not-started
**Goal:** Expand domain learning to capture common real-world professional domains and preserve fidelity for abbreviations and original user labels.

## Context

The domain keyword seed map only covers six domains (`paper_rendering`, `equity_research`, `data_analysis`, `literature_review`, `code_generation`, `workflow_building`). Common domains like supply chain, inventory optimization, and operations management produce no preference at all. Common abbreviations like `ML` and `NLP` don't round-trip through the taxonomy with expected fidelity.

Relevant code:
- `src/dan/server/concierge/domain_learning.py`: shared seed keyword map
- `src/dan/engine/preference_extractor.py`: `_extract_domains()`
- `src/dan/engine/domain_taxonomy.py`: alias coverage
- `src/dan/engine/memory_adapters.py`: `ProfileAdapter` normalization

Failing tests:
- `tests/test_engine/test_consolidation.py::TestPreferenceExtractionWiring::test_try_extract_preferences_stores_domains`
- `tests/test_engine/test_memory_kernel.py::TestAdapterLayer::test_profile_adapter_imports_preferences`

## Tasks

- [ ] 1. Expand domain seed keywords
  - [ ] 1-1. Add seeds for common business/technical domains: supply chain, operations management, inventory optimization, marketing analytics, product management, financial modeling, healthcare informatics, etc.
  - [ ] 1-2. Add seeds for common research domains: machine learning, natural language processing, computer vision, reinforcement learning, causal inference, econometrics, etc.
  - [ ] 1-3. Review and expand the keyword match threshold — current matching may be too strict for multi-word domain phrases
- [ ] 2. Add abbreviation aliases to the taxonomy
  - [ ] 2-1. Add common abbreviations: `ML` → `machine_learning`, `NLP` → `natural_language_processing`, `CV` → `computer_vision`, `RL` → `reinforcement_learning`, `OR` → `operations_research`, `SCM` → `supply_chain_management`, `OM` → `operations_management`, etc.
  - [ ] 2-2. Make alias matching case-insensitive
- [ ] 3. Preserve original user labels
  - [ ] 3-1. When `ProfileAdapter` normalizes domains, store both the canonical ID and the original user-provided phrase
  - [ ] 3-2. Retrieval should return the canonical ID for matching but surface the original phrase in user-facing contexts
- [ ] 4. Fix failing tests
  - [ ] 4-1. Fix `test_try_extract_preferences_stores_domains` — "supply chain logistics" / "inventory optimization" should store a preference
  - [ ] 4-2. Fix `test_profile_adapter_imports_preferences` — `ML` / `NLP` should round-trip with expected fidelity
- [ ] 5. Add new tests
  - [ ] 5-1. Test that multi-word domain phrases are matched (e.g. "supply chain logistics" → `supply_chain` or similar)
  - [ ] 5-2. Test that abbreviations resolve correctly (e.g. `ML` → `machine_learning`)
  - [ ] 5-3. Test that original labels are preserved alongside canonical IDs

## Decisions

- (filled in during execution)

## Notes

- The module audit suggests surfacing inferred domains to the user for confirmation rather than silently storing them. That's a UX improvement beyond this plan's scope but worth noting for future work.
- Consider whether the taxonomy should be extensible at runtime (user adds their own domain) vs. curated at build time.
