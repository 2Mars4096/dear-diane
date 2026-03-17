# 31-30: Domain Taxonomy Unification

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Remove the stale private domain taxonomy from `PreferenceExtractor` so profile preference capture follows the same live domain map as concierge domain learning.

## Tasks
- [x] 1. Replace the extractor's hardcoded domain table with the shared `domains/keyword_maps` source
 - [x] 1-1. Add a reusable keyword-map resolver in `domain_learning.py`
 - [x] 1-2. Pass `BehaviorStore` into runtime and CLI preference extraction
- [x] 2. Keep implicit preference inference conservative enough to avoid broad false positives
 - [x] 2-1. Filter low-signal domain keywords during durable profile extraction
 - [x] 2-2. Preserve the path-heavy no-preference behavior from the earlier memory hardening pass
- [x] 3. Add regressions and project-tracking updates
 - [x] 3-1. Update preference extractor tests for shared domain IDs and injected maps
 - [x] 3-2. Record the fix in `todo.md`, `changelog.md`, `architecture.md`, and `bugs.md`
- [x] 4. Clean up legacy persisted domains and add safe fallback normalization
 - [x] 4-1. Canonicalize `UserProfile.common_domains` on load / merge / save
 - [x] 4-2. Normalize old project / BehaviorStore domain ids with alias + slug fallback
 - [x] 4-3. Keep user-facing prompt labels readable even when storage uses canonical ids

## Decisions
- `PreferenceExtractor` now shares domain IDs and keyword-map storage with concierge domain learning, but keeps its own low-signal keyword filter because implicit profile updates should be stricter than per-turn task routing.
- CLI chat instantiates a lightweight `BehaviorStore` and registers the domain keyword seed so local profile learning can also see persisted learned domains without depending on concierge startup.
- Unknown legacy domain strings are not dropped during cleanup; they degrade to a safe slug (for example `Machine Learning` → `machine_learning`) so preference history survives even when no first-class template exists yet.

## Notes
- Validation: `pytest -q tests/test_engine/test_domain_taxonomy.py tests/test_engine/test_user_profile.py tests/test_engine/test_preference_extractor.py tests/test_concierge/test_domain_learning.py tests/test_server/test_chat_manager.py`
