# Bench 1: WorFBench

**Parent:** [benchmark-plan](benchmark-plan.md)
**Status:** not-started
**Goal:** Prove DAN's meta-orchestrator generates higher-quality workflow DAGs than GPT-4 and other baselines on the WorFBench benchmark (ICLR 2025).

## Why This Benchmark

WorFBench directly tests the ability to generate executable workflows as directed acyclic graphs from natural language task descriptions. This is the most direct evaluation of DAN's core value proposition: the meta-orchestrator produces structured workflow graphs, not just sequential tool calls.

- **Paper:** [WorFBench: Benchmarking Agentic Workflow Generation](https://arxiv.org/abs/2410.07869)
- **GitHub:** https://github.com/zjunlp/WorfBench
- **HuggingFace:** https://huggingface.co/collections/zjunlp/worfbench
- **Size:** ~18k training examples, 2,146 test examples, 723 held-out generalization tasks
- **4 categories:** open-grounded, problem-solving, embodied, function-call tasks
- **Evaluation:** WorFEval — subsequence matching (sequence planning) + subgraph matching (graph planning)
- **Key baseline:** GPT-4 shows ~15% gap between sequence and graph planning

## What DAN's Architecture Should Prove

| DAN Feature | WorFBench Signal |
|-------------|-----------------|
| Builder DSL codegen | Higher structural accuracy than free-form JSON/text generation |
| Intent compiler | Common patterns compile deterministically → zero error rate on template-covered tasks |
| Typed edges (data/control/context) | Richer edge semantics → better subgraph match scores |
| Composite sub-graphs | Hierarchical structure → better match on complex nested tasks |
| Repair loop | Bounded diagnosis fixes structural errors → higher pass rate on first attempt |

## Adapter Design

### Task Parser

```
WorFBench task JSON
  → extract task description (NL string)
  → extract available tools/APIs (function signatures)
  → format as MetaController input
```

Each WorFBench task includes a natural language description and a set of available API functions. Map these to DAN's tool registry format.

### Generation Path

```
Task description + tool signatures
        ↓
MetaController.plan()
        ↓
IntentCompiler or BuilderCodegen
        ↓
dan_graph_v1 JSON
        ↓
Convert to WorFBench DAG format
```

### Output Converter

Convert DAN's `dan_graph_v1` graph to WorFBench's expected DAG format:
- Map DAN node types to WorFBench action nodes
- Map DAN edge types to WorFBench dependency edges
- Preserve topological ordering for sequence evaluation
- Handle composite nodes by flattening sub-graphs

### Evaluation

Use WorFEval directly:
- **Subsequence match** — does the generated sequence of actions match the gold sequence?
- **Subgraph match** — does the generated DAG structurally match the gold DAG?

## Tasks

- [ ] 1. **Dataset setup**
  - [ ] 1-1. Clone WorFBench repo, install dependencies
  - [ ] 1-2. Download test set from HuggingFace (`zjunlp/WorFBench_test`)
  - [ ] 1-3. Verify dataset loads correctly, inspect 5-10 examples
  - [ ] 1-4. Categorize tasks by complexity (node count, branching, loops)
- [ ] 2. **Task adapter**
  - [ ] 2-1. Parse WorFBench task JSON → extract NL description + tool signatures
  - [ ] 2-2. Map WorFBench tool signatures to DAN `ToolRegistry` format
  - [ ] 2-3. Format input for `MetaController.plan()`
- [ ] 3. **Output converter**
  - [ ] 3-1. Convert `dan_graph_v1` nodes to WorFBench action format
  - [ ] 3-2. Convert DAN edges to WorFBench dependency format
  - [ ] 3-3. Flatten composite sub-graphs for evaluation
  - [ ] 3-4. Handle edge cases (no-op nodes, gate nodes, input nodes)
- [ ] 4. **Evaluation runner**
  - [ ] 4-1. Run WorFEval subsequence matching on generated vs. gold
  - [ ] 4-2. Run WorFEval subgraph matching on generated vs. gold
  - [ ] 4-3. Aggregate scores by task category and complexity
- [ ] 5. **Baseline comparison**
  - [ ] 5-1. Run GPT-4 baseline (direct prompt → DAG) using WorFBench's provided baseline scripts
  - [ ] 5-2. Run DAN meta-orchestrator on same task set
  - [ ] 5-3. Compare sequence match and graph match scores
- [ ] 6. **Ablation runs**
  - [ ] 6-1. DAN with intent compiler only (no codegen fallback)
  - [ ] 6-2. DAN with codegen only (no intent compiler)
  - [ ] 6-3. DAN with repair loop disabled
  - [ ] 6-4. DAN with self-knowledge RAG disabled
- [ ] 7. **Analysis**
  - [ ] 7-1. Score breakdown by task category (open/problem/embodied/function-call)
  - [ ] 7-2. Score vs. task complexity (node count) chart
  - [ ] 7-3. Error taxonomy: where does DAN fail vs. where does GPT-4 fail?
  - [ ] 7-4. Feed results into bench-5 analysis framework

## Expected Results

- **Sequence match:** DAN should match or exceed GPT-4 since the builder DSL produces valid sequences by construction
- **Graph match:** DAN should significantly outperform GPT-4 due to typed edges and structural constraints
- **Intent compiler covered tasks:** Near-100% structural accuracy on template-covered patterns
- **Generalization set (723 tasks):** The real differentiator — DAN's repair loop + self-knowledge RAG should recover from novel patterns

## Estimated Effort

- Dataset setup + adapter: 2 days
- Output converter + evaluation: 2 days
- Baseline runs + ablation: 1 day
- Analysis: 1 day
- **Total: ~6 days**

## Decisions

- (to be filled during execution)

## Notes

- WorFBench gold DAGs may use different granularity than DAN's node types — need a mapping layer
- Some WorFBench tasks use tools DAN doesn't have — filter or skip those, report coverage
- The 723 held-out generalization tasks are the most valuable for demonstrating DAN's advantage
