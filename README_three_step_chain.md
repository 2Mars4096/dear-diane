# 3-Step Chain Workflow: Research → Analyze → Summarize

A production-ready sequential workflow for automated research and report generation.

## 📁 Files Created

| File | Description |
|------|-------------|
| `three_step_chain.py` | Original basic implementation |
| `three_step_chain_enhanced.py` | Enhanced version with full features |
| `three_step_chain_workflow.md` | Documentation and architecture |
| `graphs/three_step_chain.json` | DAN visual workflow (importable) |
| `demo_three_step_chain.py` | Runnable demo script |
| `README_three_step_chain.md` | This file |

## 🏗️ Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  1. Research│────▶│ 2. Analysis │────▶│ 3. Summary  │
│  web_search │     │  metrics()  │     │  markdown   │
└─────────────┘     └─────────────┘     └─────────────┘
      │                    │                   │
      ▼                    ▼                   ▼
  search_results      analysis_results    final_report
```

### Step 1: Research
- **Tool**: `web_search`
- **Input**: Topic string
- **Output**: Structured search results with relevance scores
- **Features**: Source type classification, relevance ranking

### Step 2: Analysis
- **Processing**: Theme extraction, metrics calculation
- **Metrics**: Average relevance, data quality rating, confidence score
- **Outputs**: Structured analysis with source breakdown

### Step 3: Summary
- **Format**: Markdown report
- **Sections**: Executive overview, key metrics, themes, top sources, conclusion
- **Export**: Also supports JSON and plain text formats

## 🚀 Quick Start

### Python Usage

```python
from three_step_chain_enhanced import ThreeStepChain

# Create workflow
workflow = ThreeStepChain(num_results=5, output_format="markdown")

# Run research
result = workflow.run("Artificial Intelligence in Healthcare")

# Access outputs
print(result.final_summary)           # Markdown report
print(result.analysis_results)        # Structured data
print(result.research_data)           # Raw findings
```

### DAN Visual Workflow

Import `graphs/three_step_chain.json` into the DAN editor:

1. Open DAN workflow editor
2. Import → Select `graphs/three_step_chain.json`
3. Configure input topic
4. Run workflow

### Command Line Demo

```bash
# Run with default topic
python demo_three_step_chain.py

# Run with custom topic
python demo_three_step_chain.py "Your Research Topic Here"
```

## 📊 Output Format

### Markdown Summary Example

```markdown
# Research Summary: Panama Canal Impact on Global Trade

## Executive Overview
🟢 **Data Quality:** High | **Confidence:** 81%

This report synthesizes findings from **5 sources** on *Panama Canal Impact on Global Trade*.
The research data quality is rated as **High** with an average source relevance of **90.0%**.

---

## Key Metrics

| Metric | Value |
|--------|-------|
| Sources Analyzed | 5 |
| Average Relevance | 90.0% |
| Data Quality | High |
| Confidence Score | 81.0% |
| Themes Identified | 4 |

---

## Key Themes Identified

- Market Trends & Dynamics
- Key Challenges & Risks
- Strategic Opportunities
- Industry Impact

---

## Top Sources

1. **Comprehensive Overview of Panama Canal...**
   - Type: Industry Report
   - Relevance: 95%
   - URL: https://example.com/...

---

## Conclusion

Analysis reveals 4 major thematic areas warranting further attention...
```

## ⚙️ Configuration Options

### ResearchStep
```python
ResearchStep(num_results=5)  # Number of sources to gather
```

### AnalysisStep
- Automatic theme extraction
- Source type classification
- Quality scoring (High/Medium/Low)

### SummaryStep
```python
SummaryStep(output_format="markdown")  # Options: markdown, json, text
```

## 🔧 Extending the Workflow

### Add Validation Step
```python
class ValidationStep(WorkflowStep):
    def execute(self, state: WorkflowState) -> WorkflowState:
        # Check source credibility
        # Filter low-quality sources
        return state
```

### Add Export Step
```python
class ExportStep(WorkflowStep):
    def execute(self, state: WorkflowState) -> WorkflowState:
        # Save to PDF
        # Email report
        # Upload to database
        return state
```

### Add Human Review
Insert a pause step for human approval before finalizing.

## 📈 Metrics & Quality Scoring

| Metric | Description | Range |
|--------|-------------|-------|
| Relevance Score | How well source matches topic | 0.0 - 1.0 |
| Data Quality | Overall source reliability | High/Medium/Low |
| Confidence Score | Final report confidence | 0.0 - 1.0 |
| Theme Count | Number of identified themes | Integer |

## 🎯 Use Cases

- **Market Research**: Quick industry overviews
- **Competitive Analysis**: Company and product research
- **Trend Monitoring**: Stay updated on emerging topics
- **Report Generation**: Automated briefings and summaries
- **Due Diligence**: Initial research on new areas

## 🔗 Integration Points

### With DAN Tools
- Replace `_simulate_web_search()` with actual `web_search()` calls
- Add `file_write` for automatic report saving
- Use `notify` for completion alerts

### With External APIs
- Connect to proprietary databases
- Integrate with CRM systems
- Feed into business intelligence tools

## 📝 License

Part of the DAN (Deep Agent Network) project.
