# Bench 6: Agent Memory

**Parent:** [benchmark-plan](benchmark-plan.md)
**Status:** planned
**Goal:** Publicly measure DAN's long-term and cross-session memory on reproducible benchmarks that can be rerun after memory changes, with at least one benchmark focused on true agentic memory rather than pure recall.

## Why This Benchmark

DAN already has substantial memory surface area:

- session / conversation memory
- project-scoped memory
- workflow experience memory and reuse
- error / principle memory
- domain and preference memory
- cross-session resume

Those capabilities should not be inferred from GAIA or custom demos alone. DAN needs a dedicated memory track that answers two different questions:

1. **Can DAN remember and update facts across long, multi-session interactions?**
2. **Can DAN use memory to improve later actions in multi-session agent tasks?**

The public memory track should therefore mix conversational-memory benchmarks with truly agentic, action-coupled memory benchmarks.

## Public Benchmark Stack

### A. LongMemEval — Primary reproducible memory score

- **Paper:** https://proceedings.iclr.cc/paper_files/paper/2025/hash/d813d324dbf0598bbdc9c8e79740ed01-Abstract-Conference.html
- **GitHub:** https://github.com/xiaowu0162/LongMemEval

Best first benchmark for DAN's assistant-style memory because it directly measures long-term interactive memory across five capabilities:

- information extraction
- multi-session reasoning
- knowledge updates
- temporal reasoning
- abstention

Why it fits:

- DAN already has chat and cross-session memory surfaces
- the benchmark is public and reproducible
- accuracy deltas from `memory=off` vs `memory=on` should be easy to interpret

### B. MemoryArena — Primary agentic memory benchmark

- **Project page:** https://memoryarena.github.io/
- **Paper:** https://arxiv.org/abs/2602.16313

Best fit for the specific claim "DAN has strong agent memory," because it tests memory-guided action across interdependent multi-session tasks rather than recall in isolation.

Why it fits:

- explicitly agentic
- multi-session
- memory acquisition and later action are coupled
- reveals whether DAN's stored memory actually changes downstream decisions

### C. LoCoMo — Lightweight diagnostic regression

- **GitHub:** https://github.com/snap-research/locomo
- **Paper:** https://aclanthology.org/2024.acl-long.747/

Useful as a cheaper, smaller conversational-memory regression suite. It should not be the flagship evidence for DAN, but it is a good quick diagnostic after memory changes because it is widely recognized and easy to rerun.

## Publication Channel

Unlike GAIA or AppWorld, this track is not leaderboard-native today. The refreshable public record should therefore be:

1. public benchmark source links
2. DAN commit SHA and model/memory config
3. raw result artifacts
4. summarized score table checked into the repo or published as a public report

## Current Readiness

What is already true:

- benchmark execution trust hardening from Phase 29 (`42-*`) is complete
- the benchmark smoke-path work from Phase 30 (`43-*`) is complete
- DAN already has substantial memory infrastructure, and the benchmark program already has a public-first direction

What still needs to be frozen before publication:

- the minimal `bench-5` reporting/profile slice, including memory trace fields
- contamination-safe benchmark namespaces and reset/snapshot behavior
- a clear distinction between the **headline public memory score** and the **diagnostic regression suites**

This track should therefore be staged as: **LongMemEval first public score**, **LoCoMo fast regression**, and **MemoryArena first agentic-memory proof**.

## What DAN's Architecture Should Prove

| DAN Feature | Memory Benchmark Signal |
|-------------|-------------------------|
| Conversation memory | Correct recall across sessions |
| Project-scoped memory | No leakage between unrelated tasks |
| Domain / preference memory | Correct user-specific carryover |
| Experience memory | Better performance on repeated or dependent subtasks |
| Time-aware memory updates | Correct handling of stale vs updated facts |
| Cross-session resume | Continued progress without restarting from zero |
| Memory retrieval policy | Better accuracy at lower token cost than dumping full history |

## Execution Order

1. **LongMemEval subset** — validate adapter, scoring, and fair `memory_on` vs `memory_off` controls
2. **LoCoMo quick regression** — cheap diagnostic for recall / temporal / multi-hop issues
3. **MemoryArena subset** — first true agentic memory proof
4. **LongMemEval full run** — stable headline score
5. **MemoryArena broader run** — stable agent-memory scorecard

## Adapter Design

### LongMemEval

```
Timestamped chat sessions
        ↓
DAN chat / memory ingestion path
        ↓
Final benchmark question
        ↓
DAN answer
        ↓
LongMemEval evaluator
```

Integration rules:

- feed benchmark sessions through the same memory-writing path DAN uses in production chat
- disable unrelated adaptive learning that would contaminate repeated runs
- answer only after all prior sessions are ingested

### LoCoMo

```
Long multi-session conversation
        ↓
DAN memory ingestion path
        ↓
QA / summary task
        ↓
LoCoMo metrics
```

Use this mainly as a fast regression and ablation benchmark, not as the primary public memory claim.

### MemoryArena

```
Session 1 task / action / feedback
        ↓
memory write
        ↓
Session 2 dependent task
        ↓
memory retrieval-guided action
        ↓
MemoryArena task evaluator
```

Integration rules:

- DAN must interact through the same concierge / solver / tool surfaces used in real operation
- memory writes must happen between sessions, not via hidden oracle injection
- evaluation should preserve the benchmark's multi-session dependency structure

## Baselines

Every memory benchmark should run at least these variants:

| Variant | Purpose |
|---------|---------|
| **DAN memory off** | Establish whether memory actually contributes |
| **DAN working-memory only** | Distinguish short-window help from persistent memory |
| **DAN full memory** | Main system result |
| **Monolithic full-history agent** | Fair external-style baseline when token limits allow |
| **Oracle / benchmark upper bound** | Optional benchmark-native ceiling where available |

The most important comparison is not only DAN vs an external agent. It is:

- DAN full memory vs DAN memory off
- DAN full memory vs monolithic full-history agent

That isolates whether DAN's memory architecture helps beyond just using a larger context window.

## Benchmark Integrity And Red Lines

- No manual oracle seeding. The only facts allowed in memory are benchmark-provided sessions or DAN's own benchmark-time actions.
- No cross-example contamination. Each benchmark example must use a fresh benchmark namespace unless the benchmark itself defines multi-session continuity inside that example.
- No hidden benchmark-specific summaries or sidecar stores outside DAN's normal memory-writing path.
- Log memory mode and namespace/snapshot identifiers for every run so `memory_off`, `working_only`, and `full_memory` comparisons are auditable.
- Keep unrelated adaptive learning features off unless a benchmark explicitly intends to measure them. This is a memory benchmark, not a general online-learning benchmark.

## Run Profiles

Use a small named profile set instead of ad-hoc memory runs:

| Profile | Purpose | Scope |
|---------|---------|-------|
| `longmemeval_smoke_10` | wiring smoke test | 10 examples across core LongMemEval capabilities |
| `longmemeval_public_v1` | headline public memory score | full LongMemEval run on frozen memory profile |
| `locomo_regression_v1` | cheap recurring regression | fixed LoCoMo subset for recall / temporal / update regressions |
| `memoryarena_family1_pilot` | first agentic-memory pilot | one public MemoryArena task family with `memory_off` vs `memory_on` |
| `memoryarena_public_v1` | first agentic-memory scorecard | broadened MemoryArena run once the pilot family is stable |

## Core Metrics

| Metric | What It Measures |
|--------|------------------|
| **Accuracy / benchmark score** | End-task correctness |
| **Update correctness** | Whether later facts replace stale facts correctly |
| **Temporal reasoning score** | Whether DAN remembers when something was true |
| **Abstention correctness** | Whether DAN declines when memory is insufficient |
| **Task success with memory** | Whether memory changes downstream action quality |
| **Token cost** | Memory efficiency vs full-history baselines |
| **Memory retrieval hit rate** | Whether relevant memories are surfaced when needed |
| **Leakage rate** | Whether unrelated prior sessions contaminate answers |

## Tasks

- [ ] 0. **Entry gate and contamination controls**
  - [ ] 0-1. Implement the minimal `bench-5` profile/artifact bundle slice required for public memory reporting
  - [ ] 0-2. Freeze a benchmark profile: model, tier map, learning mode, memory mode, telemetry flags, memory backend, benchmark namespace rules
  - [ ] 0-3. Ensure `memory_off`, `working_memory_only`, and `full_memory` can be selected cleanly without hidden carryover
  - [ ] 0-4. Add fresh user/project/session namespace generation or snapshot-reset behavior so repeated runs do not contaminate each other
- [ ] 1. **Memory trace instrumentation**
  - [ ] 1-1. Record memory namespace / snapshot identifiers in every benchmark result
  - [ ] 1-2. Record write count, retrieval count, and retrieval hit/miss summary
  - [ ] 1-3. Record whether a benchmark answer depended on retrieved memory vs immediate context only
- [ ] 2. **LongMemEval — first public score**
  - [ ] 2-1. Load the official dataset and map sessions into DAN chat turns
  - [ ] 2-2. Route ingestion through DAN's production memory-writing path only
  - [ ] 2-3. Build answer extraction and official scoring integration
  - [ ] 2-4. Run `longmemeval_smoke_10` with `memory_off` and `full_memory`
  - [ ] 2-5. Freeze the headline public memory profile and run `longmemeval_public_v1`
- [ ] 3. **LoCoMo — cheap regression**
  - [ ] 3-1. Load official conversations and QA / summarization tasks
  - [ ] 3-2. Define one fixed recurring regression subset
  - [ ] 3-3. Run `locomo_regression_v1` and document which failure classes it catches well vs poorly
- [ ] 4. **MemoryArena — first agentic-memory proof**
  - [ ] 4-1. Load one public task family first
  - [ ] 4-2. Preserve the benchmark's multi-session action loop without oracle memory injection
  - [ ] 4-3. Run `memoryarena_family1_pilot` with `memory_off`, `working_memory_only`, and `full_memory`
  - [ ] 4-4. Expand to `memoryarena_public_v1` only after the pilot family is stable and interpretable
- [ ] 5. **Analysis and publication**
  - [ ] 5-1. Compare `memory_off`, `working_memory_only`, and `full_memory`
  - [ ] 5-2. Compare DAN against a monolithic full-history agent baseline where the benchmark allows it
  - [ ] 5-3. Break down failures into retrieval miss, stale memory use, update-resolution failure, leakage, and reasoning failure
  - [ ] 5-4. Publish a public score log with commit SHA, date, memory config, benchmark profile, and artifact bundle for every refresh
- [ ] 6. **Refresh policy**
  - [ ] 6-1. Re-run LoCoMo on every meaningful memory change as a cheap regression
  - [ ] 6-2. Re-run LongMemEval after major memory / chat / prompt-assembly changes
  - [ ] 6-3. Re-run MemoryArena after major memory / planning / tool-use changes

## Success Criteria

The first memory-benchmark phase is successful if it produces all of the following:

- one clean LongMemEval public artifact bundle on a frozen memory profile
- one fixed LoCoMo regression slice that can be rerun cheaply after memory changes
- one MemoryArena pilot family with interpretable `memory_off` vs `working_memory_only` vs `full_memory` comparisons
- explicit contamination controls and auditable memory trace fields in the result bundle

If DAN performs well on LongMemEval but poorly on MemoryArena, that should be treated as a product signal: memory recall is not yet turning into better action.

## Estimated Effort

- contamination controls + memory trace fields: ~1-2 days
- LongMemEval adapter + first stable public run: ~2-3 days
- LoCoMo regression adapter: ~1 day
- MemoryArena first task family + pilot comparisons: ~2-3 days
- publication/reporting polish: ~1 day
- **Total first publishable cycle: ~7-10 days**

## Decisions

- (to be filled during execution)

## Notes

- This is a **public reproducible** track, not necessarily a leaderboard-native track. DAN should still publish refreshable, commit-linked results.
- LongMemEval is the cleanest first score because it is focused, public, and already widely used for memory systems.
- MemoryArena is the best fit for "agent memory" specifically because it couples memory with downstream action across sessions.
- LoCoMo is useful, but it is not sufficient as the only memory benchmark because strong LoCoMo results can still hide poor memory-guided action.
- The most important failure to avoid is hidden contamination across runs; a slightly lower but clean memory score is more valuable than a higher but ambiguous one.
