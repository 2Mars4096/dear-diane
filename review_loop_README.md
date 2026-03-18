# Review Loop Workflow

## Overview

This workflow implements an **iterative review process** where:
1. A **Writer** creates or revises content
2. A **Reviewer** evaluates quality and provides feedback
3. A **Decision** node determines if more revisions are needed
4. The loop continues until content is **approved** or **max iterations** reached

## Architecture

```
┌─────────────────┐
│  Input: Topic   │
└────────┬────────┘
         ▼
┌─────────────────┐
│ Initialize State│
└────────┬────────┘
         ▼
┌─────────────────┐     ┌─────────────────┐
│  Writer Node    │────▶│ Reviewer Node   │
│ (Draft/Revise)  │     │ (Evaluate)      │
└─────────────────┘     └────────┬────────┘
         ▲                       ▼
         │               ┌─────────────────┐
         │               │  Decision Node  │
         │               │ (Approve/Revise)│
         │               └────────┬────────┘
         │                        │
         │         ┌──────────────┴──────────────┐
         │         │                             │
         │    Revise │                        Finalize
         │         │                             │
         │         ▼                             ▼
         │  ┌─────────────────┐        ┌─────────────────┐
         └──│ Update State    │        │ Output Result   │
            │ (Store feedback)│        │ (Final content) │
            └─────────────────┘        └─────────────────┘
```

## Nodes

### 1. Writer Node
- **Purpose**: Generate initial content or revise based on feedback
- **Input**: Requirements, previous feedback (if any)
- **Output**: ContentDraft
- **Implementation**: LLM with creative writing prompt

### 2. Reviewer Node
- **Purpose**: Evaluate content quality against requirements
- **Input**: ContentDraft, original requirements
- **Output**: ReviewFeedback with:
  - Quality score (0-100)
  - Decision: APPROVED / NEEDS_REVISION
  - Detailed comments
  - Specific suggestions
- **Implementation**: LLM with critical evaluation prompt

### 3. Decision Node
- **Purpose**: Route workflow based on review outcome
- **Logic**:
  - If APPROVED → Finalize
  - If NEEDS_REVISION → Continue loop
  - If max iterations reached → Force finalize

### 4. State Management
- Tracks: iteration count, feedback history, current draft
- Prevents infinite loops via max_iterations
- Maintains audit trail of all revisions

## Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_iterations` | int | 5 | Maximum review cycles |
| `approval_threshold` | float | 80.0 | Score needed for auto-approval |

## Usage

### Python API

```python
from review_loop_workflow import ReviewLoopWorkflow

# Create workflow
workflow = ReviewLoopWorkflow(
    max_iterations=5,
    approval_threshold=80.0
)

# Run with requirements
requirements = """
Write a blog post about supply chain resilience 
in the context of the Panama Canal.
"""

final_state = workflow.run(requirements)

# Access results
print(final_state.final_output)
print(f"Iterations: {final_state.iteration_count}")
print(f"Approved: {final_state.is_complete}")
```

### As a Workflow Block

```json
{
  "workflow_id": "review_loop_workflow",
  "inputs": {
    "requirements": "Your content requirements here",
    "max_iterations": 5
  }
}
```

## Example Output

```
============================================================
STARTING REVIEW LOOP WORKFLOW
============================================================

--- Iteration 1 ---
[Writer] Creating initial draft for: Write a blog post about supply chain resilience in Panama Canal...
[Reviewer] Reviewing draft v1...
[Reviewer] Decision: needs_revision (Score: 67/100)
[Decision] → Continuing to next revision cycle

--- Iteration 2 ---
[Writer] Revising draft (iteration 2)
[Writer] Addressing feedback: Content needs improvement (Score: 67/100)...
[Reviewer] Reviewing draft v2...
[Reviewer] Decision: approved (Score: 85/100)
[Decision] ✓ Content approved - finalizing output

============================================================
WORKFLOW COMPLETE
============================================================
Total iterations: 2
Final status: Approved
```

## Use Cases

- **Content Creation**: Blog posts, articles, marketing copy
- **Academic Writing**: Paper drafts, literature reviews
- **Technical Documentation**: API docs, user guides
- **Business Communications**: Reports, proposals, executive summaries
- **Iterative Design**: Requirements documents, specifications

## Extensions

### Human-in-the-Loop
Replace the LLM reviewer with a human reviewer node:
```python
class HumanReviewerNode:
    def run(self, draft: ContentDraft) -> ReviewFeedback:
        # Send draft to human for review
        # Wait for human feedback
        return human_feedback
```

### Multiple Reviewers
Add parallel review by multiple reviewers:
```python
# Consensus-based approval
reviewers = [ReviewerNode(role="technical"), 
             ReviewerNode(role="editorial")]
feedbacks = [r.run(draft) for r in reviewers]
approved = all(f.decision == APPROVED for f in feedbacks)
```

### Adaptive Threshold
Adjust approval threshold based on content type:
```python
if content_type == "blog_post":
    threshold = 75
elif content_type == "academic_paper":
    threshold = 90
```

## Files

| File | Description |
|------|-------------|
| `review_loop_workflow.py` | Python implementation with all nodes |
| `review_loop_definition.json` | Workflow schema for workflow engine |
| `review_loop_README.md` | This documentation |

## Testing

Run the examples:
```bash
python review_loop_workflow.py
```
