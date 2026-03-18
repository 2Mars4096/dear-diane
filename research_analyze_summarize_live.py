"""
═══════════════════════════════════════════════════════════════════════════════
3-STEP CHAIN WORKFLOW: RESEARCH → ANALYZE → SUMMARIZE (LIVE WEB VERSION)
═══════════════════════════════════════════════════════════════════════════════

This workflow performs research using LIVE web search with fetched content,
then analyzes and summarizes the grounded information.

WORKFLOW STRUCTURE:
───────────────────

┌────────────────┐     ┌────────────────┐     ┌────────────────┐
│   WEB_SEARCH   │────▶│    ANALYZE     │────▶│   SUMMARIZE    │
│   (tool_operator)    │  (llm_operator)│     │  (llm_operator)│
└────────────────┘     └────────────────┘     └────────────────┘
        │                       │                       │
        ▼                       ▼                       ▼
  search_results            analysis                 summary
  (fetched content)     (critical review)      (grounded summary)

STEP-BY-STEP FLOW:
──────────────────

STEP 1: WEB_SEARCH (tool_operator)
  • Uses web_search tool with fetch_content=true
  • Searches for: {{input.topic}}
  • Retrieves: 5 results with full page content
  • Output: search_results (grounded, live data)

STEP 2: ANALYZE (llm_operator)
  • Input: search_results from Step 1
  • Task: Critical analysis of web sources
  • Output: analysis with:
    - Key facts extracted from sources
    - Source credibility assessment
    - Patterns across sources
    - Citations to specific sources

STEP 3: SUMMARIZE (llm_operator)
  • Inputs: search_results + analysis
  • Task: Create concise grounded summary
  • Output: summary with:
    - Main takeaways (cited)
    - Context and background
    - Areas of uncertainty
    - Conclusion

INPUT:
──────
{
  "topic": "your research topic"
}

OUTPUTS:
────────
{
  "search_results": "Raw web search results with fetched content",
  "analysis": "Critical analysis of web sources",
  "summary": "Concise grounded summary with citations"
}

USAGE:
──────
start_run(
    workflow_id="research_analyze_summarize_live",
    inputs={"topic": "artificial intelligence regulation 2024"}
)

═══════════════════════════════════════════════════════════════════════════════
"""

WORKFLOW_DEF = {
    "name": "research_analyze_summarize_live",
    "version": "1.0.0",
    "description": "3-step chain with live web search: research, analyze, summarize",
    
    "nodes": [
        {
            "id": "web_search",
            "type": "tool_operator",
            "config": {
                "tool": "web_search",
                "params": {
                    "query": "{{input.topic}}",
                    "num_results": 5,
                    "fetch_content": True
                }
            },
            "outputs": ["search_results"]
        },
        {
            "id": "analyze",
            "type": "llm_operator", 
            "config": {
                "model": "kimi-k2.5",
                "system_prompt": """You are an analytical expert. Analyze the provided web search results critically. 
Identify key facts, patterns, assess source credibility, and draw meaningful insights. 
Focus only on information present in the provided sources. Cite sources using [1], [2] format.""",
                "prompt_template": """Analyze the following web search results about: {{input.topic}}

SEARCH RESULTS:
{{web_search.search_results}}

Provide an analysis that includes:
1. Key facts and data points extracted from the sources
2. Main themes and patterns across sources
3. Assessment of source credibility and potential biases
4. Any contradictions or gaps in the information
5. Critical insights based ONLY on the provided sources

Cite sources inline as [1], [2], etc."""
            },
            "inputs": ["input.topic", "web_search.search_results"],
            "outputs": ["analysis"]
        },
        {
            "id": "summarize",
            "type": "llm_operator",
            "config": {
                "model": "kimi-k2.5",
                "system_prompt": """You are a summarization expert. Create a clear, concise summary based on 
the analyzed web research. Ensure all claims are supported by the sources. Use citations [1], [2].""",
                "prompt_template": """Based on the web research and analysis below, create a concise summary about: {{input.topic}}

RESEARCH FINDINGS (from web sources):
{{web_search.search_results}}

ANALYSIS:
{{analyze.analysis}}

Provide a summary that includes:
1. Main takeaways (2-4 key points) - cite sources
2. Important context or background
3. Any uncertainties or areas needing more research
4. Conclusion (1-2 sentences)

Keep it concise (200-300 words) and ensure all claims are grounded in the provided sources.
Use [1], [2] format for citations."""
            },
            "inputs": ["input.topic", "web_search.search_results", "analyze.analysis"],
            "outputs": ["summary"]
        }
    ],
    
    "edges": [
        {"from": "web_search", "to": "analyze"},
        {"from": "analyze", "to": "summarize"}
    ],
    
    "input_schema": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "The topic to research"
            }
        },
        "required": ["topic"]
    },
    
    "output_schema": {
        "type": "object",
        "properties": {
            "search_results": {
                "type": "string",
                "description": "Raw web search results with fetched content"
            },
            "analysis": {
                "type": "string",
                "description": "Critical analysis of web sources"
            },
            "summary": {
                "type": "string",
                "description": "Concise grounded summary with citations"
            }
        }
    }
}


def print_workflow_info():
    print(__doc__)


if __name__ == "__main__":
    print_workflow_info()
