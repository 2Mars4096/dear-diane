# 2: Beacon Provider-Independent Discovery

**Status:** in-progress
**Goal:** Make Beacon answer a meaningful share of live search tasks from first-party discovery plus corpus refresh so Tavily, Serper, Brave, and DDG become cold-start fallback rather than the normal path.

## Tasks
- [x] 1. Audit and expose current external-discovery dependence
  - [x] 1-1. Record the exact discovery backend used on every live search response and summarize it in eval output
  - [x] 1-2. Build a frozen live-query pack across docs, GitHub, company IR, filings, research papers, and open-web current-fact lookups
  - [x] 1-3. Set target thresholds for corpus-only, connector-first, and provider-fallback rates before claiming provider independence
- [ ] 2. Add first-party vertical discovery before broad-web crawling
  - [x] 2-1. Route official docs and reference queries directly into sitemap/feed/static-doc discovery
  - [ ] 2-2. Add company IR, issuer news, and regulatory/filing source discovery for equity and current-fact work
  - [ ] 2-3. Add GitHub docs/releases and research-paper connectors for DAN's coding and research query mix
- [ ] 3. Build Beacon-owned refresh and expansion loops
  - [ ] 3-1. Revisit known good domains and documents on TTL/seed schedules without provider APIs
  - [ ] 3-2. Expand from stored seeds through same-site navigation, sitemap discovery, feeds, and linked canonical pages
  - [ ] 3-3. Persist source budgets, robots/rate-limit policy, and failure history so refresh is safe and repeatable
- [ ] 4. Narrow third-party providers to true fallback
  - [x] 4-1. Add broker policy that can disable provider discovery per query family or environment
  - [ ] 4-2. Prove Beacon-first quality on evals before reducing Tavily/Serper/Brave/DDG budgets
  - [ ] 4-3. Define the cutover for `beacon` default and a later `provider_off` mode

## Decisions
- Phase 1 Beacon work is complete as incubation, not as full provider independence.
- The next win is vertical-first discovery, not a day-one general web crawler.
- Provider APIs remain allowed as cold-start fallback until evals show Beacon-first coverage is good enough.

## Notes
- 2026-04-17 live validation:
  - a real `FLY` equity-research prompt executed through DAN's own `handle_web_search(...)` surface succeeded, but the response still reported `provider: tavily`
  - the broker metadata, official-page grounding, and Beacon corpus persistence all worked, so the remaining gap is discovery independence rather than fetch/ground/cite plumbing
- The first implementation slice should be telemetry plus a gap audit on real query families, because "provider independence" needs a measurable definition before more connector work lands.
- 2026-04-17 implementation update:
  - `handle_web_search(...)` now surfaces `discovery_classification` and `discovery_source_counts` consistently in `search_broker`, `beacon_corpus`, and `search_result_set`
  - `tests/eval/beacon_search_eval.py` now carries a frozen provider-independence query pack plus explicit cutover thresholds for corpus-only, connector-first, provider-fallback, and provider-only rates
  - Beacon mode now has an initial connector-first path in `src/dan/search/connector_discovery.py`: allowed domains and a small built-in docs/regulatory/research seed catalog can mine sitemap/feed/static-doc URLs before falling back to Tavily/Brave/Serper/DDG
  - a second slice now adds explicit query-family routing plus provider-fallback policy: `DAN_BEACON_PROVIDER_POLICY=fallback|connectors_only|off` and `DAN_BEACON_PROVIDER_DISABLED_FAMILIES=...` let Beacon suppress legacy discovery by family/environment, and known GitHub repo seeds now cover a narrow but real subset of coding queries without provider calls
  - Beacon can now also expand from domains it already knows: corpus hits contribute `seed_domains`, and connector discovery now mines same-site HTML navigation in addition to sitemaps/feeds/static seeds, so repeated company/doc queries can revisit a known site even without a fresh `allowed_domains` hint
  - remaining gap: company IR/news coverage and broader GitHub/paper discovery still need dedicated connectors beyond the initial allowed-domain and built-in seed path
