# Simple Research Chain

A 3-step workflow chain: **Research → Analyze → Summarize**

## Overview

This workflow automates the process of researching a topic, analyzing the findings, and producing a concise summary.

## Steps

### 1. Research (`research` node)
- **Type**: Tool Operator (web_search)
- **Function**: Searches the web for information on the given topic
- **Configuration**:
  - Returns top 5 results
  - Fetches full content from each result
- **Output**: Raw search results with content

### 2. Analyze (`analyze` node)
- **Type**: LLM Operator
- **Function**: Analyzes research findings to extract key insights
- **Analysis includes**:
  - Key facts and data points
  - Main themes and patterns
  - Credibility assessment
  - Gaps or contradictions
- **Output**: Structured bullet-point analysis

### 3. Summarize (`summarize` node)
- **Type**: LLM Operator  
- **Function**: Creates a final, readable summary
- **Output**: 2-3 paragraph summary in LaTeX-compatible format

## Input

```json
{
  "topic": "string - The research topic to investigate"
}
```

## Output

```json
{
  "summary": "Final concise summary",
  "analysis": "Structured analysis of findings",
  "research": "Raw web search results"
}
```

## Example Usage

```python
# Run the workflow
result = workflow.run({
    "topic": "Panama Canal drought impact on shipping 2024"
})

# Access outputs
print(result["summarize"])  # Final summary
print(result["analyze"])    # Analysis
print(result["research"])   # Raw research
```

## Flow Diagram

```
┌──────────┐      ┌──────────┐      ┌──────────┐
│ Research │ ───→ │ Analyze  │ ───→ │ Summarize│
│ (search) │      │  (LLM)   │      │  (LLM)   │
└──────────┘      └──────────┘      └──────────┘
```

## Customization

- Adjust `num_results` in the research node to get more/less search results
- Modify the `system_prompt` in analyze/summarize nodes to change the analysis style
- Change `temperature` to control creativity (lower = more factual)
