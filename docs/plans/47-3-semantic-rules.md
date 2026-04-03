# 47-3: Semantic Rules

**Parent:** [47-agent-output-linter](47-agent-output-linter.md)
**Status:** completed
**Goal:** Implement Tier 2 semantic lint rules — lightweight model-based checks that catch content-level issues without full LLM calls.

## Tasks

- [x] 1. Define embedder interface in `src/dan/linter/rules/semantic.py`
  - [x] 1-1. `Embedder` protocol: `__call__(text: str) → list[float]`
  - [x] 1-2. Utility: `cosine_similarity(a: list[float], b: list[float]) → float`
  - [x] 1-3. Utility: `text_from_data(data: Any) → str` — extract text representation from arbitrary data (dict → JSON, str → passthrough, list → joined)
- [x] 2. Implement `TopicSimilarityRule`
  - [x] 2-1. Embed the output text and compare against the `embedding_reference` string (the intent/topic description)
  - [x] 2-2. If `embedding_reference` is not set, generate reference from `topic_keywords` joined as a sentence
  - [x] 2-3. Pass if cosine similarity ≥ `min_topic_similarity` (default 0.7)
  - [x] 2-4. Return confidence score alongside pass/fail for Tier 3 escalation decisions
  - [x] 2-5. No auto-fix — topic drift requires re-generation, which is a Tier 3 / orchestrator concern
- [x] 3. Implement `EntityPresenceRule`
  - [x] 3-1. Check that all entities in `required_entities` appear in the output text (case-insensitive substring match)
  - [x] 3-2. Configurable matching: `exact` (substring), `fuzzy` (Levenshtein/SequenceMatcher threshold), `embedding` (entity embedding similarity against the flattened output text)
  - [x] 3-3. Default to `exact` matching for zero-dependency, zero-cost operation
  - [x] 3-4. No auto-fix — missing entities require re-generation
- [x] 4. Implement `KeywordPresenceRule`
  - [x] 4-1. Check that at least N of M topic keywords appear in the output text
  - [x] 4-2. Configurable `min_keyword_ratio` (default 0.5 — at least half the keywords must appear)
  - [x] 4-3. Return the ratio as a confidence score for Tier 3 escalation
  - [x] 4-4. No auto-fix
- [x] 5. Implement `LanguageDetectionRule`
  - [x] 5-1. Check that the output text is in the expected language
  - [x] 5-2. Use a lightweight heuristic (character set analysis + common word presence) — no external dependency
  - [x] 5-3. Configurable `expected_language` (default: inferred from config or "en")
  - [x] 5-4. Severity: warning (language mismatch is often a sign of deeper problems but not always fatal)
- [x] 6. Implement `ContradictionDetectionRule`
  - [x] 6-1. Check if the output contradicts the configured reference text on key facts (claim overlap + optional embedder pairing)
  - [x] 6-2. Extract simple factual claims from reference/output text and detect obvious numeric, negation, or polarity conflicts
  - [x] 6-3. Keep the initial rule conservative — flag only obvious contradictions and leave deeper semantic reasoning to Tier 3
- [x] 7. Register all Tier 2 rules in `semantic.py` as `SEMANTIC_RULES` list
- [x] 8. Confidence aggregation
  - [x] 8-1. Each semantic rule contributes an internal confidence score to the validator
  - [x] 8-2. The engine aggregates confidence across Tier 2 rules (`min` or `mean`, configurable)
  - [x] 8-3. If aggregated confidence < `tier3_threshold`, the engine triggers Tier 3
- [x] 9. Tests
  - [x] 9-1. `tests/test_linter/test_semantic.py` — each rule with mock embedder
  - [x] 9-2. Topic similarity with a real lightweight embedder (sentence-transformers or similar)
  - [x] 9-3. Entity presence: exact, fuzzy, and embedding modes
  - [x] 9-4. Confidence aggregation and Tier 3 escalation trigger
  - [x] 9-5. Performance: Tier 2 lint with mock embedder completes in < 10ms

## Rule Inventory

| Rule | Config Source | Severity | Requires Embedder | Confidence |
|------|-------------|----------|-------------------|------------|
| TopicSimilarityRule | `semantic.min_topic_similarity`, `semantic.embedding_reference` | error | yes | cosine sim |
| EntityPresenceRule | `semantic.required_entities`, `semantic.entity_match_mode` | error | optional (embedding mode) | entity hit ratio |
| KeywordPresenceRule | `semantic.topic_keywords`, `semantic.min_keyword_ratio` | warning | no | keyword ratio |
| LanguageDetectionRule | `semantic.expected_language` | warning | no | detection confidence |
| ContradictionDetectionRule | `semantic.contradiction_reference_text` + output data | error | optional | contradiction score |

## Embedder Injection

The linter does not ship an embedder. The caller provides one:

```python
# Example: using sentence-transformers
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("all-MiniLM-L6-v2")

def embedder(text: str) -> list[float]:
    return model.encode(text).tolist()

result = lint(data=output, config=config, embedder=embedder)
```

If no embedder is provided and semantic rules are configured, the engine skips Tier 2 with a warning diagnostic (not an error). Structural rules still run.

## Decisions

- The landed Tier 2 slice keeps the cheapest useful rule set explicit: keyword presence with configurable ratio thresholds, entity presence with exact/fuzzy/embedding match modes, lightweight language detection, conservative contradiction detection, and embedding-backed topic similarity with `topic_keywords` fallback reference text.
- `SEMANTIC_RULES` is now a formal registry even though execution still lives in one validator function. This mirrors the structural/intent cleanup and makes the shipped rule inventory explicit for tests and docs.

## Notes

- `KeywordPresenceRule` does not require an embedder — it is pure string matching. This makes it the cheapest semantic rule and a good default when no embedding model is available.
- When semantic/intent config is only auto-generated from descriptions/instructions and there is no structural contract to justify a blocking gate, that generated lint now defaults to warning severity. Explicit lint config can still promote semantic checks to `error`.
- `min_keyword_ratio` now controls how strict keyword coverage should be. Missing some keywords is acceptable when the configured ratio is satisfied; below that threshold the rule emits one diagnostic with matched/missing metadata instead of pretending every missing keyword is equally fatal.
- Tier 2 confidence is now aggregated across whichever semantic sub-rules are configured. `confidence_aggregation="min"` is the stricter default; `mean` allows one weaker semantic signal to be balanced by stronger others before Tier 3 escalation.
- `LanguageDetectionRule` stays warning-only even when the overall lint severity is `error`, because language drift is a useful semantic signal but not automatically a contract-breaking handoff by itself.
- The `text_from_data` utility is important: agent output is often a dict with multiple fields, not a single string. The utility flattens it into a text representation for embedding/matching. Field names are included in the text to provide structural context.
- `ContradictionDetectionRule` now takes `semantic.contradiction_reference_text` plus the candidate output, splits both into short claims, and only flags obvious contradictions when the claims are clearly comparable. The current rule is intentionally conservative: numeric mismatches, negation mismatches, and opposing polarity words on the same subject are in scope; deeper factual reasoning is still a Tier 3 concern.
- `tests/test_linter/test_semantic.py` now covers the explicit rule registry, the public Tier 2 utility helpers, language detection, a mock-embedder similarity failure, `topic_keywords` fallback for similarity reference text, configurable keyword ratios, fuzzy entity matching, contradiction detection, and confidence aggregation. `tests/test_linter/test_core.py` now also proves that aggregation can either trigger or skip Tier 3 depending on the configured strategy, and that language-mismatch warnings do not fail the handoff by themselves.
- The semantic suite now also includes a real sentence-transformers path when the local MiniLM snapshot is available. `tests/test_linter/test_semantic.py` loads the cached `all-MiniLM-L6-v2` snapshot in local-files-only mode, derives a threshold from observed positive/negative scores, and proves that the actual embedder cleanly separates an on-topic finance summary from an unrelated hiking checklist without depending on network access.
- The mock-embedder latency gate is now explicit too. `tests/eval/semantic_lint_benchmark.py` measures repeated Tier 2 semantic lint on a representative structured payload and currently passes the strict `< 10ms median` gate with a report at `tests/eval/results/20260403T065904Z_semantic_lint_benchmark.json`.
