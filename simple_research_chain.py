"""
Simple 3-Step Research Chain Workflow
Steps: Research → Analyze → Summarize
"""

from dan.workflows import Workflow, Node, Edge
from dan.operators import LLMOperator, ToolOperator

# Define the workflow
workflow = Workflow(
    name="simple_research_chain",
    description="A 3-step chain: research a topic, analyze findings, write a summary"
)

# Step 1: Research Node - Uses web search to gather information
research_node = Node(
    id="research",
    operator=ToolOperator(
        tool="web_search",
        config={
            "query": "{{input.topic}}",  # Takes topic from input
            "num_results": 5,
            "fetch_content": True
        }
    ),
    description="Search the web for information on the given topic"
)

# Step 2: Analyze Node - Uses LLM to analyze research findings
analyze_node = Node(
    id="analyze",
    operator=LLMOperator(
        config={
            "model": "kimi-k2.5",
            "system_prompt": """You are an analytical research assistant. 
Analyze the provided research findings and extract:
1. Key facts and data points
2. Main themes and patterns
3. Credibility assessment of sources
4. Gaps or contradictions in the information

Output your analysis in structured bullet points.""",
            "prompt": """Research findings on topic '{{input.topic}}':

{{research.output}}

Provide a structured analysis of these findings.""",
            "temperature": 0.3
        }
    ),
    description="Analyze the research findings for key insights"
)

# Step 3: Summarize Node - Uses LLM to create final summary
summarize_node = Node(
    id="summarize",
    operator=LLMOperator(
        config={
            "model": "kimi-k2.5",
            "system_prompt": """You are a professional summarizer. 
Create a clear, concise summary of the analyzed research.
The summary should be accessible to a general audience while maintaining accuracy.""",
            "prompt": """Topic: {{input.topic}}

Analysis of findings:
{{analyze.output}}

Write a concise summary (2-3 paragraphs) that captures the essential information about this topic.
Format in LaTeX-compatible plain text.""",
            "temperature": 0.4
        }
    ),
    description="Create a final summary of the analyzed research"
)

# Define edges (connections between nodes)
edges = [
    Edge(from_node="research", to_node="analyze"),
    Edge(from_node="analyze", to_node="summarize")
]

# Add nodes and edges to workflow
workflow.add_nodes([research_node, analyze_node, summarize_node])
workflow.add_edges(edges)

# Define entry and exit points
workflow.set_entry(["research"])
workflow.set_exit(["summarize"])

# Input schema
workflow.input_schema = {
    "topic": "string - The research topic to investigate"
}

# Output schema
workflow.output_schema = {
    "summary": "string - Final summarized output from the summarize node",
    "analysis": "string - Structured analysis from the analyze node",
    "raw_research": "string - Original web search results from the research node"
}

if __name__ == "__main__":
    # Example usage
    result = workflow.run({"topic": "Panama Canal drought impact on shipping 2024"})
    print(result)
