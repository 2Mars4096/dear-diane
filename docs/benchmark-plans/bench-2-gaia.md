# Bench 2: GAIA Level 3

**Parent:** [benchmark-plan](benchmark-plan.md)
**Status:** not-started
**Goal:** Demonstrate DAN succeeds on 6+ step real-world tasks where monolithic agents fail, using the standard GAIA benchmark with public leaderboard placement.

## Why This Benchmark

GAIA is the most cited general agent benchmark. It tests multi-step reasoning with real tool use on questions that require web browsing, file processing, and multi-hop inference. Humans score 92%, GPT-4 with plugins scored 15%. Level 3 tasks (6+ steps with tool chaining) are exactly where monolithic agents break down and DAN's graph architecture should shine.

- **Paper:** [GAIA: A Benchmark for General AI Assistants](https://arxiv.org/abs/2311.12983)
- **HuggingFace:** https://huggingface.co/datasets/gaia-benchmark/GAIA
- **Leaderboard:** https://huggingface.co/spaces/gaia-benchmark/leaderboard
- **Size:** 466 questions (165 Level 1, 86 Level 2, 215 Level 3)
- **Evaluation:** Exact match against ground-truth answers
- **Key baseline:** Top systems ~70% Level 1, ~55% Level 2, ~35% Level 3

## What DAN's Architecture Should Prove

| DAN Feature | GAIA Signal |
|-------------|------------|
| Meta-orchestrator decomposition | Correct multi-step plans for 6+ step questions |
| Context projection | Bounded token usage even on deep chains |
| Model heterogeneity | Cheap models for simple extraction, expensive for reasoning |
| Checkpoint recovery | Resume from mid-chain failure instead of restarting |
| Tool composition | web_search → pdf_read → python_eval chains |
| While-loop quality gate | Re-attempt extraction when first parse fails |

## Adapter Design

### Task Parser

```
GAIA question JSON
  → extract question text
  → extract attached files (PDF, spreadsheet, image)
  → identify required tool categories (web, file, code, reasoning)
  → format as concierge/MetaController input
```

GAIA questions come with optional file attachments. The adapter must stage files in DAN's artifact store before workflow execution.

### Generation Path

Two strategies, benchmarked against each other:

**Strategy A: Full meta-orchestrator**
```
Question + files
        ↓
MetaController.plan()  →  workflow graph
        ↓
Engine.run(graph)      →  tool execution
        ↓
Extract final answer from RunResult
```

**Strategy B: Solver-direct**
```
Question + files
        ↓
Concierge solver path  →  direct tool use
        ↓
Extract final answer from response
```

Compare both strategies to isolate when workflow decomposition adds value vs. when direct tool use suffices.

### Tool Mapping

| GAIA Capability | DAN Tool |
|----------------|----------|
| Web browsing | `web_search`, `web_fetch` |
| PDF reading | `pdf_read` |
| Spreadsheet processing | `csv_read`, `spreadsheet_read`, `python_eval` |
| Image understanding | `image_describe` |
| Code execution | `python_eval` |
| File I/O | `file_read`, `file_write` |
| Arithmetic/reasoning | LLM operator (direct) |

### Answer Extractor

GAIA expects short, exact-match answers. The extractor must:
- Parse the final node/response output
- Strip formatting, units, and explanation
- Normalize numbers, dates, names to GAIA expected format
- Handle multi-part answers (comma-separated)

## Tasks

- [ ] 1. **Dataset setup**
  - [ ] 1-1. Load GAIA dataset from HuggingFace
  - [ ] 1-2. Stage file attachments to local artifact store
  - [ ] 1-3. Categorize by level and required tool types
  - [ ] 1-4. Identify questions requiring tools DAN has vs. doesn't have
- [ ] 2. **Task adapter**
  - [ ] 2-1. Parse GAIA question format → DAN input
  - [ ] 2-2. File attachment staging pipeline
  - [ ] 2-3. Tool availability check per question (skip if missing required tool)
- [ ] 3. **Answer extractor**
  - [ ] 3-1. Build answer normalization pipeline (numbers, dates, names)
  - [ ] 3-2. Handle multi-part and free-form answers
  - [ ] 3-3. Validate against GAIA exact-match evaluator
- [ ] 4. **Strategy A: Meta-orchestrator path**
  - [ ] 4-1. Run MetaController on each question → generate workflow
  - [ ] 4-2. Execute generated workflows
  - [ ] 4-3. Collect answers and evaluate
- [ ] 5. **Strategy B: Solver-direct path**
  - [ ] 5-1. Run concierge solver on each question directly
  - [ ] 5-2. Collect answers and evaluate
- [ ] 6. **Baselines**
  - [ ] 6-1. Monolithic agent baseline (same LLM, same tools, single conversation)
  - [ ] 6-2. Record per-level accuracy for all three approaches
- [ ] 7. **Level-stratified analysis**
  - [ ] 7-1. Accuracy by level (Level 1 / 2 / 3)
  - [ ] 7-2. Token usage by level (DAN vs. monolithic)
  - [ ] 7-3. Cost by level
  - [ ] 7-4. Failure mode taxonomy per level
  - [ ] 7-5. Identify the complexity crossover point
- [ ] 8. **Ablation runs** (Level 3 only, to save cost)
  - [ ] 8-1. Context projection on/off
  - [ ] 8-2. Model heterogeneity on/off (all same model vs. tier policy)
  - [ ] 8-3. Checkpoint recovery test (inject failures)
- [ ] 9. **Learning curve** (Level 3 subset, 20 questions × 5 runs)
  - [ ] 9-1. Run same 20 Level 3 questions 5 times each
  - [ ] 9-2. Plot accuracy and cost per run
  - [ ] 9-3. Identify which learning features contributed (model tiering, prompt variants)
- [ ] 10. **Leaderboard submission**
  - [ ] 10-1. Run on official GAIA test split
  - [ ] 10-2. Format predictions for HuggingFace submission
  - [ ] 10-3. Submit to public leaderboard

## Expected Results

| Level | Monolithic (predicted) | DAN Meta-orchestrator (predicted) | DAN Solver-direct (predicted) |
|-------|----------------------|----------------------------------|------------------------------|
| 1 | ~70% | ~75% | ~73% |
| 2 | ~50% | ~60% | ~55% |
| 3 | ~30% | ~50% | ~35% |

The gap should widen with level because Level 3 tasks have more steps where context confusion, wrong model selection, and lack of recovery hurt monolithic agents.

Token usage on Level 3 should be 40-60% lower for DAN due to context projection.

## Estimated Effort

- Dataset setup + adapter: 2 days
- Answer extractor + normalization: 1 day
- Strategy A + B runs: 2 days (API costs ~$50-100)
- Baselines + ablation: 2 days (API costs ~$100-200)
- Learning curve runs: 1 day (API costs ~$50)
- Analysis + leaderboard submission: 1 day
- **Total: ~9 days, ~$200-350 API costs**

## Decisions

- (to be filled during execution)

## Notes

- GAIA test split answers are hidden — submit to leaderboard for official scores
- Validation split has visible answers — use for development and ablation
- Some GAIA questions require real-time web data that may have changed since dataset creation
- Image questions require `image_describe` tool — skip if not available, report coverage
- Leaderboard placement is the highest-visibility marketing deliverable from this benchmark suite
