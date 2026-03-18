"""
3-Step Chain Workflow: Research → Analyze → Summarize
======================================================

This workflow performs a 3-step chain:
1. RESEARCH: Gathers comprehensive information about a topic
2. ANALYZE: Critically analyzes the research findings
3. SUMMARIZE: Creates a concise summary with key takeaways

Usage:
    python research_analyze_summarize_workflow.py "Your topic here"
"""

import asyncio
import sys
from typing import Dict, Any

# Workflow Definition
WORKFLOW_DEF = {
    "name": "research_analyze_summarize",
    "description": "A 3-step chain: research a topic, analyze findings, summarize results",
    "version": "1.0.0",
    
    "nodes": [
        {
            "id": "research",
            "type": "llm_operator",
            "config": {
                "model": "kimi-k2.5",
                "system_prompt": """You are a research assistant. Your task is to gather comprehensive information about the given topic. 
Search for facts, data, recent developments, and key concepts. Be thorough and objective.""",
                "prompt_template": """Research the following topic thoroughly and provide detailed findings:

Topic: {{input.topic}}

Provide a comprehensive research report including:
1. Key facts and definitions
2. Recent developments or news
3. Important data points
4. Different perspectives on the topic

Be thorough and cite specific information where possible."""
            },
            "outputs": ["research_findings"]
        },
        {
            "id": "analyze",
            "type": "llm_operator",
            "config": {
                "model": "kimi-k2.5",
                "system_prompt": """You are an analytical expert. Your task is to analyze research findings critically, 
identify patterns, assess credibility, and draw meaningful insights.""",
                "prompt_template": """Analyze the following research findings:

{{research.research_findings}}

Provide an analysis that includes:
1. Key patterns and trends
2. Strengths and limitations of the information
3. Connections between different findings
4. Critical insights and implications

Be objective and highlight any gaps or areas needing further investigation."""
            },
            "inputs": ["research.research_findings"],
            "outputs": ["analysis"]
        },
        {
            "id": "summarize",
            "type": "llm_operator",
            "config": {
                "model": "kimi-k2.5",
                "system_prompt": """You are a summarization expert. Create clear, concise summaries that capture 
the essential points from complex information.""",
                "prompt_template": """Based on the research and analysis below, create a concise summary:

RESEARCH FINDINGS:
{{research.research_findings}}

ANALYSIS:
{{analyze.analysis}}

Provide a summary that includes:
1. Main takeaways (2-3 key points)
2. Conclusion or recommendation
3. Keep it concise and actionable (150-250 words)"""
            },
            "inputs": ["research.research_findings", "analyze.analysis"],
            "outputs": ["summary"]
        }
    ],
    
    "edges": [
        {"from": "research", "to": "analyze"},
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
            "research_findings": {
                "type": "string",
                "description": "Detailed research findings"
            },
            "analysis": {
                "type": "string",
                "description": "Critical analysis of findings"
            },
            "summary": {
                "type": "string",
                "description": "Concise summary with key takeaways"
            }
        }
    }
}


def visualize_workflow():
    """Display the workflow structure"""
    print("""
╔══════════════════════════════════════════════════════════════════╗
║           3-STEP CHAIN: RESEARCH → ANALYZE → SUMMARIZE          ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║   ┌─────────────┐      ┌─────────────┐      ┌─────────────┐     ║
║   │  RESEARCH   │─────▶│   ANALYZE   │─────▶│  SUMMARIZE  │     ║
║   │             │      │             │      │             │     ║
║   │ • Gather    │      │ • Patterns  │      │ • Key       │     ║
║   │   facts     │      │ • Trends    │      │   takeaways │     ║
║   │ • Recent    │      │ • Critique  │      │ • Concise   │     ║
║   │   news      │      │ • Insights  │      │   summary   │     ║
║   │ • Data      │      │             │      │             │     ║
║   └─────────────┘      └─────────────┘      └─────────────┘     ║
║          │                    │                    │             ║
║          └────────────────────┴────────────────────┘             ║
║                         OUTPUTS                                  ║
║              (research_findings, analysis, summary)              ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
    """)


def run_workflow_demo(topic: str):
    """Demonstrate the workflow with a sample topic"""
    print(f"\n{'='*60}")
    print(f"RUNNING 3-STEP CHAIN WORKFLOW")
    print(f"{'='*60}")
    print(f"\n📋 Input Topic: {topic}")
    print(f"\n🔍 STEP 1: RESEARCH")
    print(f"   → Gathering comprehensive information...")
    print(f"   → Output: research_findings (detailed report)")
    
    print(f"\n📊 STEP 2: ANALYZE")
    print(f"   → Processing research findings...")
    print(f"   → Identifying patterns and insights...")
    print(f"   → Output: analysis (critical assessment)")
    
    print(f"\n📝 STEP 3: SUMMARIZE")
    print(f"   → Synthesizing research + analysis...")
    print(f"   → Creating concise summary...")
    print(f"   → Output: summary (key takeaways)")
    
    print(f"\n{'='*60}")
    print(f"WORKFLOW COMPLETE")
    print(f"{'='*60}")
    print(f"\nTo execute this workflow with DAN, use:")
    print(f"  start_run(workflow_id='research_analyze_summarize', inputs={{'topic': '{topic}'}})")


if __name__ == "__main__":
    visualize_workflow()
    
    topic = sys.argv[1] if len(sys.argv) > 1 else "artificial intelligence regulation"
    run_workflow_demo(topic)
