# Bench 3: AppWorld

**Parent:** [benchmark-plan](benchmark-plan.md)
**Status:** not-started
**Goal:** Demonstrate DAN handles complex multi-API control flow (branching, looping, error handling) that monolithic agents struggle with, using AppWorld's 750 tasks across 9 simulated apps.

## Why This Benchmark

AppWorld is the ACL 2024 Best Resource Paper. It provides 457 APIs across 9 simulated applications (email, calendar, file storage, notes, music, shopping, banking, social media, weather). Tasks require complex control flow — branching on conditions, looping over collections, error handling — not just sequential API calls. GPT-4o solves 49% normal / 30% challenge. This directly exercises DAN's GateNode, ForEach, typed edges, and repair loop.

- **Paper:** [AppWorld: A Controllable World of Apps and People for Benchmarking Interactive Coding Agents](https://arxiv.org/abs/2407.18901)
- **GitHub:** https://github.com/StonyBrookNLP/appworld
- **Website:** https://appworld.dev/
- **Size:** 750 tasks (normal + challenge difficulty)
- **Evaluation:** State-based + execution-based unit tests (allows multiple valid solutions, detects side effects)
- **Key finding:** AppWorld recently added MCP server support — DAN's `mcp_bridge` could connect directly

## What DAN's Architecture Should Prove

| DAN Feature | AppWorld Signal |
|-------------|----------------|
| GateNode (if/else, while) | Correct branching/looping on API results |
| ForEach | Iterate over collections (emails, files, orders) |
| Typed edges (data/control) | Clean separation of API payloads vs. control flow |
| Context projection | Bounded context even when iterating over large result sets |
| MCP bridge | Direct connection to AppWorld's MCP server |
| Repair loop | Recovery from API errors (invalid params, auth failures) |
| Model heterogeneity | Cheap model for simple API calls, expensive for reasoning steps |

## Adapter Design

### Integration Strategy

Two approaches, benchmarked against each other:

**Strategy A: MCP bridge (preferred)**
```
AppWorld task description
        ↓
MetaController.plan()  →  workflow graph
        ↓
Engine.run(graph)      →  nodes call AppWorld APIs via MCP bridge
        ↓
AppWorld state-based evaluator
```

**Strategy B: Tool wrapper**
```
AppWorld task description
        ↓
MetaController.plan()  →  workflow graph
        ↓
Engine.run(graph)      →  nodes call wrapped AppWorld Python APIs
        ↓
AppWorld state-based evaluator
```

MCP bridge is preferred because it tests a realistic integration path (consuming external APIs). Tool wrapper is the fallback if MCP setup is too complex.

### Task Parser

```
AppWorld task JSON
  → extract task description (NL string)
  → extract initial app state (user accounts, data)
  → extract available APIs for this task
  → identify control flow requirements (branch/loop/error handling)
  → format as MetaController input
```

### Tool Mapping

AppWorld's 457 APIs are grouped by app. Map the full API surface to DAN tools:

| AppWorld App | API Count | DAN Integration |
|-------------|-----------|-----------------|
| Email | ~50 | MCP tool group or wrapped functions |
| Calendar | ~40 | MCP tool group or wrapped functions |
| File Storage | ~30 | MCP tool group or wrapped functions |
| Notes | ~25 | MCP tool group or wrapped functions |
| Shopping | ~80 | MCP tool group or wrapped functions |
| Banking | ~60 | MCP tool group or wrapped functions |
| Social Media | ~70 | MCP tool group or wrapped functions |
| Music | ~50 | MCP tool group or wrapped functions |
| Weather | ~20 | MCP tool group or wrapped functions |

### Evaluation

AppWorld uses state-based evaluation — check the app state after execution against expected state. This allows multiple valid solution paths.

```
Initial state → DAN executes workflow → Final state
                                              ↓
                                      State-based unit tests
                                              ↓
                                      Pass / Fail + side-effect check
```

## Tasks

- [ ] 1. **Environment setup**
  - [ ] 1-1. Install AppWorld (`pip install appworld && appworld install`)
  - [ ] 1-2. Verify simulated environment runs correctly
  - [ ] 1-3. Explore MCP server support — test connectivity with `mcp_bridge`
  - [ ] 1-4. If MCP works: configure DAN's MCP bridge for AppWorld
  - [ ] 1-5. If MCP fails: build Python tool wrappers for top-used APIs
- [ ] 2. **Task adapter**
  - [ ] 2-1. Parse AppWorld task format → DAN MetaController input
  - [ ] 2-2. Map available APIs per task to DAN tool registry
  - [ ] 2-3. Handle initial state setup (user accounts, pre-populated data)
- [ ] 3. **Control flow mapping**
  - [ ] 3-1. Categorize tasks by control flow type (sequential, branching, looping, error handling)
  - [ ] 3-2. Verify MetaController generates appropriate control flow nodes (GateNode, ForEach)
  - [ ] 3-3. Test repair loop on API error scenarios
- [ ] 4. **Evaluation integration**
  - [ ] 4-1. Wire DAN execution results into AppWorld's state-based evaluator
  - [ ] 4-2. Handle side-effect detection (did DAN modify state it shouldn't have?)
  - [ ] 4-3. Aggregate pass/fail by task difficulty and control flow type
- [ ] 5. **Benchmark runs**
  - [ ] 5-1. Run DAN on normal difficulty tasks
  - [ ] 5-2. Run DAN on challenge difficulty tasks
  - [ ] 5-3. Run monolithic agent baseline (same LLM, AppWorld tools, single conversation)
- [ ] 6. **Control flow analysis**
  - [ ] 6-1. Success rate by control flow type (seq / branch / loop / error)
  - [ ] 6-2. Token usage comparison (DAN vs. monolithic) per control flow type
  - [ ] 6-3. Identify tasks where GateNode/ForEach provide measurable advantage
  - [ ] 6-4. Error recovery analysis: how often does DAN's repair loop save a failing task?
- [ ] 7. **Ablation** (challenge difficulty subset)
  - [ ] 7-1. Disable GateNode (force sequential) — measure branch/loop task degradation
  - [ ] 7-2. Disable ForEach (force serial iteration) — measure throughput impact
  - [ ] 7-3. Disable repair loop — measure recovery rate drop
  - [ ] 7-4. Disable model heterogeneity — measure cost increase
- [ ] 8. **Feed results into bench-5 analysis framework**

## Expected Results

| Difficulty | Monolithic (predicted) | DAN (predicted) | Gap Driver |
|-----------|----------------------|----------------|------------|
| Normal | ~45% | ~60% | Control flow + context management |
| Challenge | ~25% | ~45% | Repair loop + model heterogeneity |

Largest gap expected on tasks requiring:
- Looping over collections (ForEach advantage)
- Conditional branching on API results (GateNode advantage)
- Multi-app coordination (context projection advantage)

## Estimated Effort

- Environment setup + MCP integration: 3 days
- Task adapter + tool mapping: 2 days
- Evaluation integration: 1 day
- Benchmark runs + baselines: 2 days (API costs ~$100-200)
- Analysis + ablation: 2 days
- **Total: ~10 days, ~$100-200 API costs**

## Decisions

- (to be filled during execution)

## Notes

- AppWorld's MCP support is recent — may need debugging/workarounds
- 457 APIs is a large surface — start with a subset of tasks using fewer APIs, then expand
- State-based evaluation is more forgiving than exact-match — multiple valid paths count as success
- AppWorld provides a Docker container for isolated execution — consider using for reproducibility
- Challenge tasks often require error handling that monolithic agents miss entirely
