"""
3-Step Chain: Research → Analyze → Summarize
==============================================
A simple sequential workflow for processing information through three stages.

Usage:
    from three_step_chain import ThreeStepChain
    
    chain = ThreeStepChain()
    result = chain.run(
        topic="Artificial Intelligence in Healthcare",
        research_depth="comprehensive",
        output_format="structured"
    )
    
    print(result['summary'])
"""

from typing import Dict, Any, Optional, Literal


class ThreeStepChain:
    """
    A 3-step chain workflow that performs:
    1. Research - gathers comprehensive information on a topic
    2. Analysis - critically analyzes the findings
    3. Summary - produces a concise, formatted summary
    """
    
    def __init__(self, llm_client=None):
        """
        Initialize the chain with an optional LLM client.
        
        Args:
            llm_client: Optional LLM client for making calls
        """
        self.llm_client = llm_client
        self.name = "three_step_chain"
        self.version = "1.0.0"
    
    def run(
        self,
        topic: str,
        research_depth: Literal["brief", "standard", "comprehensive"] = "standard",
        output_format: Literal["bullet", "paragraph", "structured"] = "structured"
    ) -> Dict[str, Any]:
        """
        Execute the 3-step chain.
        
        Args:
            topic: The topic to research and summarize
            research_depth: Depth of research (brief|standard|comprehensive)
            output_format: Format for final summary (bullet|paragraph|structured)
            
        Returns:
            Dictionary with keys: research_findings, analysis, summary
        """
        # Step 1: Research
        research_findings = self._research(topic, research_depth)
        
        # Step 2: Analyze
        analysis = self._analyze(topic, research_findings)
        
        # Step 3: Summarize
        summary = self._summarize(topic, research_findings, analysis, output_format)
        
        return {
            "research_findings": research_findings,
            "analysis": analysis,
            "summary": summary
        }
    
    def _research(self, topic: str, depth: str) -> str:
        """Step 1: Research the topic."""
        prompt = f"""Research the following topic comprehensively.

Topic: {topic}
Depth: {depth}

Provide detailed findings including:
- Key facts and data points
- Relevant context and background
- Multiple perspectives if applicable
- Sources or authoritative references where possible

Be thorough and objective."""
        
        return self._call_llm(prompt)
    
    def _analyze(self, topic: str, research_findings: str) -> str:
        """Step 2: Analyze the findings."""
        prompt = f"""Analyze the following research findings on: {topic}

Research Findings:
{research_findings}

Provide a critical analysis that includes:
- Key patterns and themes identified
- Strengths and limitations of the information
- Connections between different findings
- Areas of uncertainty or gaps in knowledge
- Strategic insights or implications

Be analytical and insightful."""
        
        return self._call_llm(prompt)
    
    def _summarize(
        self, 
        topic: str, 
        research_findings: str, 
        analysis: str, 
        format_type: str
    ) -> str:
        """Step 3: Create a concise summary."""
        format_guidelines = {
            "bullet": "Use bullet points for key takeaways",
            "paragraph": "Write 1-2 cohesive paragraphs",
            "structured": "Use headers and organized sections"
        }
        
        prompt = f"""Create a concise summary for: {topic}

Format: {format_type} ({format_guidelines.get(format_type, "structured")})

Research Findings:
{research_findings}

Analysis:
{analysis}

Create a clear, concise summary that:
- Captures the essence of the research
- Highlights the most important insights
- Is accessible to a general audience
- Follows the requested format"""
        
        return self._call_llm(prompt)
    
    def _call_llm(self, prompt: str) -> str:
        """Call the LLM with the given prompt."""
        if self.llm_client:
            return self.llm_client.complete(prompt)
        
        # Placeholder for when no client is provided
        return f"[LLM would process]: {prompt[:100]}..."


# Example usage
if __name__ == "__main__":
    chain = ThreeStepChain()
    
    # Example run
    result = chain.run(
        topic="The impact of remote work on urban economies",
        research_depth="standard",
        output_format="structured"
    )
    
    print("=" * 60)
    print("RESEARCH FINDINGS")
    print("=" * 60)
    print(result['research_findings'][:500] + "...")
    
    print("\n" + "=" * 60)
    print("ANALYSIS")
    print("=" * 60)
    print(result['analysis'][:500] + "...")
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(result['summary'])
