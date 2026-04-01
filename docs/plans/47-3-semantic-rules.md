# 47-3: Semantic Rules

**Parent:** [47-agent-output-linter](47-agent-output-linter.md)
**Status:** not-started
**Goal:** Implement Tier 2 semantic lint rules — lightweight model-based checks that catch content-level issues without full LLM calls.

## Tasks

- [ ] 1. Define embedder interface in `src/dan/linter/rules/semantic.py`
  - [ ] 1-1. `Embedder` protocol: `__call__(text: str) → list[float]`
  - [ ] 1-2. Utility: `cosine_similarity(a: list[float], b: list[float]) → float`
  - [ ] 1-3. Utility: `text_from_data(data: Any) → str` — extract text representation from arbitrary data (dict → JSON, str → passthrough, list → joined)
- [ ] 2. Implement `TopicSimilarityRule`
  - [ ] 2-1. Embed the output text and compare against the `embedding_reference` string (the intent/topic description)
  - [ ] 2-2. If `embedding_reference` is not set, generate reference from `topic_keywords` joined as a sentence
  - [ ] 2-3. Pass if cosine similarity ≥ `min_topic_similarity` (default 0.7)
  - [ ] 2-4. Return confidence score alongside pass/fail for Tier 3 escalation decisions
  - [ ] 2-5. No auto-fix — topic drift requires re-generation, which is a Tier 3 / orchestrator concern
- [ ] 3. Implement `EntityPresenceRule`
  - [ ] 3-1. Check that all entities in `required_entities` appear in the output text (case-insensitive substring match)
  - [ ] 3-2. Configurable matching: `exact` (substring), `fuzzy` (Levenshtein distance ≤ threshold), `embedding` (entity embedding similarity)
  - [ ] 3-3. Default to `exact` matching for zero-dependency, zero-cost operation
  - [ ] 3-4. No auto-fix — missing entities require re-generation
- [ ] 4. Implement `KeywordPresenceRule`
  - [ ] 4-1. Check that at least N of M topic keywords appear in the output text
  - [ ] 4-2. Configurable `min_keyword_ratio` (default 0.5 — at least half the keywords must appear)
  - [ ] 4-3. Return the ratio as a confidence score for Tier 3 escalation
  - [ ] 4-4. No auto-fix
- [ ] 5. Implement `LanguageDetectionRule`
  - [ ] 5-1. Check that the output text is in the expected language
  - [ ] 5-2. Use a lightweight heuristic (character set analysis + common word presence) — no external dependency
  - [ ] 5-3. Configurable `expected_language` (default: inferred from config or "en")
  - [ ] 5-4. Severity: warning (language mismatch is often a sign of deeper problems but not always fatal)
- [ ] 6. Implement `ContradictionDetectionRule` (optional, deferred if complexity is too high)
  - [ ] 6-1. Check if the output contradicts the input on key facts (uses embedder to compare claims)
  - [ ] 6-2. Extract simple factual claims from input and output, embed each, check for opposing semantics
  - [ ] 6-3. This is the most complex Tier 2 rule — mark as stretch goal
- [ ] 7. Register all Tier 2 rules in `semantic.py` as `SEMANTIC_RULES` list
- [ ] 8. Confidence aggregation
  - [ ] 8-1. Each semantic rule returns a `confidence: float` in its `RuleResult`
  - [ ] 8-2. The engine aggregates confidence across all Tier 2 rules (min, mean, or weighted — configurable)
  - [ ] 8-3. If aggregated confidence < `tier3_threshold`, the engine triggers Tier 3
- [ ] 9. Tests
  - [ ] 9-1. `tests/test_linter/test_semantic.py` — each rule with mock embedder
  - [ ] 9-2. Topic similarity with a real lightweight embedder (sentence-transformers or similar)
  - [ ] 9-3. Entity presence: exact, fuzzy, and embedding modes
  - [ ] 9-4. Confidence aggregation and Tier 3 escalation trigger
  - [ ] 9-5. Performance: Tier 2 lint with mock embedder completes in < 10ms

## Rule Inventory

| Rule | Config Source | Severity | Requires Embedder | Confidence |
|------|-------------|----------|-------------------|------------|
| TopicSimilarityRule | `semantic.min_topic_similarity`, `semantic.embedding_reference` | error | yes | cosine sim |
| EntityPresenceRule | `semantic.required_entities` | error | optional (fuzzy/embed modes) | entity hit ratio |
| KeywordPresenceRule | `semantic.topic_keywords`, `semantic.min_keyword_ratio` | warning | no | keyword ratio |
| LanguageDetectionRule | `semantic.expected_language` | warning | no | detection confidence |
| ContradictionDetectionRule | (input data + output data) | error | yes | contradiction score |

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

- (to be filled during execution)

## Notes

- `KeywordPresenceRule` does not require an embedder — it is pure string matching. This makes it the cheapest semantic rule and a good default when no embedding model is available.
- The `text_from_data` utility is important: agent output is often a dict with multiple fields, not a single string. The utility flattens it into a text representation for embedding/matching. Field names are included in the text to provide structural context.
- `ContradictionDetectionRule` is marked as a stretch goal because reliable contradiction detection is an open research problem. The initial implementation should be conservative — flag obvious contradictions (numeric value reversals, negation patterns) rather than attempting deep semantic reasoning.
