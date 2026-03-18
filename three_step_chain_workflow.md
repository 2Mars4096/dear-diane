# 3-Step Chain Workflow

## Overview
A simple sequential workflow that: **Research → Analyze → Summarize**

## Workflow Structure

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Research  │────▶│   Analyze   │────▶│   Summarize │
│    Step     │     │    Step     │     │    Step     │
└─────────────┘     └─────────────┘     └─────────────┘
       │                   │                   │
       ▼                   ▼                   ▼
   raw_findings      analysis_results    final_summary
```

## Nodes

### Node 1: Research
- **Type**: Tool/Search Node
- **Function**: `web_search`
- **Input**: `topic` (string)
- **Output**: `research_data` (list of findings)
- **Description**: Gathers information from multiple sources on the given topic

### Node 2: Analysis  
- **Type**: Processing Node
- **Function**: Analyze research findings
- **Input**: `research_data` (from Node 1)
- **Output**: `analysis_results` (structured insights)
- **Description**: Extracts themes, calculates metrics, identifies key sources

### Node 3: Summary
- **Type**: Output Node
- **Function**: Generate final report
- **Input**: `analysis_results` (from Node 2)
- **Output**: `final_summary` (markdown document)
- **Description**: Produces formatted summary document

## Data Flow

| Step | Input | Output | Description |
|------|-------|--------|-------------|
| 1 | `topic` | `research_data[]` | Search and collect sources |
| 2 | `research_data[]` | `analysis{}` | Process and analyze findings |
| 3 | `analysis{}` | `summary.md` | Generate final report |

## Usage

### Python Implementation
```python
from three_step_chain import ThreeStepChain

# Create workflow
workflow = ThreeStepChain()

# Run with your topic
result = workflow.run("Your Research Topic")

# Access outputs
print(result.research_data)      # Raw findings
print(result.analysis_results)   # Structured analysis  
print(result.final_summary)      # Final markdown report
```

### As DAN Workflow Graph
To implement in DAN visual editor:

1. **Create Node 1**: Add a tool node → select `web_search`
   - Input: `topic` parameter
   - Output: save to `research_data`

2. **Create Node 2**: Add a processing node
   - Input: wire from Node 1's `research_data`
   - Function: analysis logic
   - Output: save to `analysis_results`

3. **Create Node 3**: Add an output node
   - Input: wire from Node 2's `analysis_results`
   - Function: markdown formatting
   - Output: `final_summary`

## Example Output

For topic: "Panama Canal Drought Impact on Global Shipping"

```markdown
# Research Summary: Panama Canal Drought Impact on Global Shipping

## Overview
This report summarizes findings from 3 sources on Panama Canal Drought 
Impact on Global Shipping. The research data quality is rated as **High** 
with an average source relevance of 0.88.

## Key Themes Identified
- **Market Trends**
- **Key Challenges**
- **Opportunities**
- **Future Outlook**

## Top Sources
1. Source A - Panama Canal Drought Impact on Global Shipping Overview
2. Source B - Panama Canal Drought Impact on Global Shipping Recent Developments

## Conclusion
Analysis of Panama Canal Drought Impact on Global Shipping reveals 4 major 
areas of focus. The data suggests this is a well-documented topic with 
reliable source material.
```

## Extensions

Possible additions to this workflow:
- **Validation Step**: Check source credibility
- **Comparison Step**: Compare with previous research
- **Export Step**: Save to PDF, email, or database
- **Feedback Loop**: Human review before final output
