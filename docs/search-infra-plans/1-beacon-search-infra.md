# 1: Beacon Search Infra

**Status:** in-progress
**Goal:** Build a separately named first-party search stack that can reduce reliance on third-party search APIs over time while preserving DAN's current `web_search` and `web_fetch` tools throughout rollout.

## Problem

- DAN already owns parts of grounding, dedup, and reranking, but discovery still depends heavily on external provider APIs.
- Search policy is split across the provider/tool layer and the capability layer, which makes replacement harder than necessary.
- Search state is mostly ephemeral, so repeated DAN usage does not accumulate into a durable corpus or retrieval advantage.

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [1-1](1-1-beacon-compatibility-and-broker-boundary.md) | Compatibility & Broker Boundary | Freeze the current caller-visible web-tool membrane, introduce an internal broker lifecycle, and keep the legacy path callable during rollout | P1 | not-started |
| [1-2](1-2-beacon-corpus-and-indexing.md) | Corpus & Indexing | Persist fetched documents/chunks, build first-party local retrieval indices, and reuse existing grounded fetch work instead of discarding it | P1 | not-started |
| [1-3](1-3-beacon-connectors-and-ingest.md) | Connectors & Ingest | Keep provider APIs as bounded discovery adapters while adding first-party vertical connectors and refresh jobs for high-value sources | P2 | not-started |
| [1-4](1-4-beacon-ranking-grounding-and-citations.md) | Ranking, Grounding & Citations | Replace heuristic-only ranking with hybrid broker-level ranking, improve passage extraction, and tighten citation/evidence contracts | P1 | not-started |
| [1-5](1-5-beacon-service-evals-and-cutover.md) | Service, Evals & Cutover | Add eval gates, package Beacon as its own subsystem, and define when a separate repo/service is actually warranted | P2 | not-started |

## Dependencies / Sequencing

Phase opening:
```
1-1 (Compatibility & Broker Boundary)   ← freeze the membrane before deeper infra work
1-2 (Corpus & Indexing)                 ← establish first-party storage and retrieval primitives
```

Middle wave:
```
1-3 (Connectors & Ingest)               ← provider adapters + high-value first-party source ingest
1-4 (Ranking, Grounding & Citations)    ← use the corpus and connectors to improve actual answer quality
```

Cutover wave:
```
1-5 (Service, Evals & Cutover)          ← keep legacy access, evaluate Beacon, then decide extraction/cutover
```

## Success Criteria

- Existing `web_search` and `web_fetch` callers continue to work during Beacon incubation.
- Repeated or adjacent DAN queries can be answered from Beacon's own corpus often enough to reduce external search calls materially.
- Tavily, Serper, Brave, and DDG become pluggable discovery backends rather than DAN's search identity.
- Citation, grounding, and freshness quality stay at least at current DAN levels while external-call dependence drops.
- Beacon can later move into its own repo/service without forcing DAN callers to change tool IDs or payload shapes.

## Decisions

- The separate search-infrastructure project name is **Beacon Search**.
- Beacon starts as a separate planning and module boundary inside this repo, not as a day-one separate repo.
- DAN's current `web_search` and `web_fetch` surfaces remain available throughout rollout; Beacon enters behind them as an internal broker.
- Provider APIs remain valid fallback and cold-start discovery inputs until Beacon proves it can replace enough of that dependence safely.

## Notes

- Beacon should focus on high-value repeated source families first: official docs, GitHub docs/releases, arXiv/Semantic Scholar, and authoritative company/regulatory pages.
- A broad general-web crawler is explicitly not the day-one target. First win narrow, high-authority retrieval slices that map well to DAN's real coding and research use cases.
- The decision to split Beacon into a separate repo/service should wait until there is at least one additional consumer or a clear deployment/ownership reason beyond DAN itself.
