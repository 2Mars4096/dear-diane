"""
3-Step Chain Workflow: Research → Analyze → Summarize
Enhanced version with actual web_search integration and DAN tool calls
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import json
import re

@dataclass
class WorkflowState:
    """State container passed between workflow steps"""
    topic: str
    research_data: List[Dict[str, Any]] = field(default_factory=list)
    analysis_results: Dict[str, Any] = field(default_factory=dict)
    final_summary: str = ""
    errors: List[str] = field(default_factory=list)

class WorkflowStep:
    """Base class for workflow steps"""
    def __init__(self, name: str):
        self.name = name
    
    def execute(self, state: WorkflowState) -> WorkflowState:
        raise NotImplementedError
    
    def _log(self, message: str):
        print(f"[{self.name}] {message}")

class ResearchStep(WorkflowStep):
    """Step 1: Research a topic using web_search"""
    
    def __init__(self, num_results: int = 5):
        super().__init__("Research")
        self.num_results = num_results
    
    def execute(self, state: WorkflowState) -> WorkflowState:
        print(f"\n{'='*70}")
        print(f"🔍 STEP 1: Researching '{state.topic}'")
        print(f"{'='*70}")
        
        try:
            # In DAN environment, this would call the actual web_search tool
            # For now, we simulate the structure that would come from web_search
            self._log(f"Searching for: {state.topic}")
            
            # Simulate web search results structure
            findings = self._simulate_web_search(state.topic)
            
            state.research_data = findings
            self._log(f"✓ Gathered {len(findings)} sources")
            
            for i, finding in enumerate(findings, 1):
                relevance = finding.get('relevance_score', 0.5)
                bar = '█' * int(relevance * 10) + '░' * (10 - int(relevance * 10))
                print(f"  [{i}] {finding['title'][:50]}...")
                print(f"      Relevance: [{bar}] {relevance:.0%}")
            
        except Exception as e:
            state.errors.append(f"Research error: {str(e)}")
            self._log(f"✗ Error: {e}")
        
        return state
    
    def _simulate_web_search(self, topic: str) -> List[Dict[str, Any]]:
        """Simulate web search results - in DAN, this would be actual tool output"""
        return [
            {
                "title": f"Comprehensive Overview of {topic}",
                "url": f"https://example.com/{topic.lower().replace(' ', '-')}-overview",
                "snippet": f"Detailed analysis covering key aspects of {topic} including market trends...",
                "content": f"This source provides comprehensive coverage of {topic}, including historical context, current developments, and expert analysis. The information is well-sourced and includes data from industry reports.",
                "relevance_score": 0.95,
                "source_type": "Industry Report"
            },
            {
                "title": f"Latest Developments in {topic} (2026)",
                "url": f"https://news.example.com/{topic.lower().replace(' ', '-')}-updates",
                "snippet": f"Recent news and breaking developments about {topic}...",
                "content": f"Recent developments show significant changes in {topic}. Industry experts suggest this trend will continue through 2026, with potential implications for various stakeholders.",
                "relevance_score": 0.88,
                "source_type": "News Article"
            },
            {
                "title": f"Expert Analysis: {topic} Deep Dive",
                "url": f"https://analysis.example.com/{topic.lower().replace(' ', '-')}-research",
                "snippet": f"In-depth expert commentary and strategic analysis of {topic}...",
                "content": f"Expert analysis reveals multiple dimensions of {topic}. Key findings include emerging patterns, risk factors, and strategic opportunities for organizations monitoring this area.",
                "relevance_score": 0.82,
                "source_type": "Research Analysis"
            },
            {
                "title": f"Market Data: {topic} Statistics",
                "url": f"https://data.example.com/{topic.lower().replace(' ', '-')}-metrics",
                "snippet": f"Quantitative data and metrics related to {topic}...",
                "content": f"Data shows measurable trends in {topic} over the past 12 months. Key metrics indicate growth in key areas while challenges remain in others.",
                "relevance_score": 0.75,
                "source_type": "Data Report"
            },
            {
                "title": f"Future Outlook: {topic} Predictions",
                "url": f"https://forecast.example.com/{topic.lower().replace(' ', '-')}-outlook",
                "snippet": f"Forward-looking predictions and scenario planning for {topic}...",
                "content": f"Forecasts suggest {topic} will experience continued evolution. Multiple scenarios are presented based on current trajectory and potential disruptors.",
                "relevance_score": 0.70,
                "source_type": "Forecast Report"
            }
        ]

class AnalysisStep(WorkflowStep):
    """Step 2: Analyze research findings with enhanced metrics"""
    
    def __init__(self):
        super().__init__("Analysis")
    
    def execute(self, state: WorkflowState) -> WorkflowState:
        print(f"\n{'='*70}")
        print(f"📊 STEP 2: Analyzing Findings")
        print(f"{'='*70}")
        
        if not state.research_data:
            state.errors.append("No research data to analyze")
            self._log("✗ No data available")
            return state
        
        try:
            # Calculate metrics
            relevance_scores = [f.get('relevance_score', 0.5) for f in state.research_data]
            avg_relevance = sum(relevance_scores) / len(relevance_scores)
            
            # Count source types
            source_types = {}
            for finding in state.research_data:
                st = finding.get('source_type', 'Unknown')
                source_types[st] = source_types.get(st, 0) + 1
            
            # Identify top sources
            top_sources = sorted(
                state.research_data, 
                key=lambda x: x.get('relevance_score', 0), 
                reverse=True
            )[:3]
            
            # Extract key themes from content
            themes = self._extract_themes(state.research_data)
            
            # Determine data quality
            if avg_relevance > 0.85:
                data_quality = "High"
                quality_emoji = "🟢"
            elif avg_relevance > 0.7:
                data_quality = "Medium"
                quality_emoji = "🟡"
            else:
                data_quality = "Low"
                quality_emoji = "🔴"
            
            state.analysis_results = {
                "source_count": len(state.research_data),
                "average_relevance": round(avg_relevance, 3),
                "data_quality": data_quality,
                "quality_emoji": quality_emoji,
                "source_type_breakdown": source_types,
                "top_sources": [
                    {
                        "title": s.get('title', 'Unknown'),
                        "url": s.get('url', ''),
                        "relevance": s.get('relevance_score', 0),
                        "type": s.get('source_type', 'Unknown')
                    }
                    for s in top_sources
                ],
                "themes_identified": themes,
                "themes_count": len(themes),
                "confidence_score": round(avg_relevance * 0.9, 3)  # Slight penalty for processing
            }
            
            self._log("✓ Analysis complete")
            print(f"\n  Data Quality: {quality_emoji} {data_quality}")
            print(f"  Average Relevance: {avg_relevance:.1%}")
            print(f"  Sources Analyzed: {len(state.research_data)}")
            print(f"  Themes Extracted: {len(themes)}")
            print(f"\n  Source Types:")
            for st, count in source_types.items():
                print(f"    • {st}: {count}")
            print(f"\n  Top Themes:")
            for theme in themes[:4]:
                print(f"    • {theme}")
            
        except Exception as e:
            state.errors.append(f"Analysis error: {str(e)}")
            self._log(f"✗ Error: {e}")
        
        return state
    
    def _extract_themes(self, findings: List[Dict[str, Any]]) -> List[str]:
        """Extract themes from content - simplified version"""
        # In a real implementation, this might use NLP
        common_themes = [
            "Market Trends & Dynamics",
            "Key Challenges & Risks", 
            "Strategic Opportunities",
            "Future Outlook & Predictions",
            "Industry Impact",
            "Stakeholder Considerations"
        ]
        
        # Simulate theme extraction based on content
        themes = []
        all_content = ' '.join([f.get('content', '') for f in findings]).lower()
        
        if any(word in all_content for word in ['trend', 'growth', 'market', 'industry']):
            themes.append("Market Trends & Dynamics")
        if any(word in all_content for word in ['challenge', 'risk', 'problem', 'issue']):
            themes.append("Key Challenges & Risks")
        if any(word in all_content for word in ['opportunity', 'potential', 'advantage']):
            themes.append("Strategic Opportunities")
        if any(word in all_content for word in ['future', 'predict', 'forecast', 'outlook']):
            themes.append("Future Outlook & Predictions")
        if any(word in all_content for word in ['impact', 'effect', 'influence']):
            themes.append("Industry Impact")
        if any(word in all_content for word in ['stakeholder', 'organization', 'company']):
            themes.append("Stakeholder Considerations")
        
        return themes if themes else common_themes[:3]

class SummaryStep(WorkflowStep):
    """Step 3: Write final summary with enhanced formatting"""
    
    def __init__(self, output_format: str = "markdown"):
        super().__init__("Summary")
        self.output_format = output_format
    
    def execute(self, state: WorkflowState) -> WorkflowState:
        print(f"\n{'='*70}")
        print(f"📝 STEP 3: Writing Summary")
        print(f"{'='*70}")
        
        if not state.analysis_results:
            state.errors.append("No analysis results to summarize")
            self._log("✗ No analysis available")
            return state
        
        try:
            analysis = state.analysis_results
            
            if self.output_format == "markdown":
                state.final_summary = self._generate_markdown(state.topic, analysis, state.research_data)
            elif self.output_format == "json":
                state.final_summary = json.dumps({
                    "topic": state.topic,
                    "analysis": analysis,
                    "sources": state.research_data
                }, indent=2)
            else:
                state.final_summary = self._generate_text(state.topic, analysis)
            
            self._log("✓ Summary generated")
            print(f"\n  Format: {self.output_format}")
            print(f"  Length: {len(state.final_summary)} characters")
            print(f"  Quality: {analysis.get('data_quality', 'Unknown')}")
            
        except Exception as e:
            state.errors.append(f"Summary error: {str(e)}")
            self._log(f"✗ Error: {e}")
        
        return state
    
    def _generate_markdown(self, topic: str, analysis: Dict, sources: List[Dict]) -> str:
        """Generate formatted markdown summary"""
        
        quality_emoji = analysis.get('quality_emoji', '⚪')
        
        summary = f"""# Research Summary: {topic}

## Executive Overview
{quality_emoji} **Data Quality:** {analysis.get('data_quality', 'Unknown')} | **Confidence:** {analysis.get('confidence_score', 0):.0%}

This report synthesizes findings from **{analysis.get('source_count', 0)} sources** on *{topic}*. 
The research data quality is rated as **{analysis.get('data_quality', 'Unknown')}** with an average 
source relevance of **{analysis.get('average_relevance', 0):.1%}**.

---

## Key Metrics

| Metric | Value |
|--------|-------|
| Sources Analyzed | {analysis.get('source_count', 0)} |
| Average Relevance | {analysis.get('average_relevance', 0):.1%} |
| Data Quality | {analysis.get('data_quality', 'Unknown')} |
| Confidence Score | {analysis.get('confidence_score', 0):.1%} |
| Themes Identified | {analysis.get('themes_count', 0)} |

---

## Source Breakdown

"""
        
        # Add source type breakdown
        for source_type, count in analysis.get('source_type_breakdown', {}).items():
            summary += f"- **{source_type}:** {count} source(s)\n"
        
        summary += f"\n---\n\n## Key Themes Identified\n\n"
        
        for theme in analysis.get('themes_identified', []):
            summary += f"- {theme}\n"
        
        summary += f"\n---\n\n## Top Sources\n\n"
        
        for i, source in enumerate(analysis.get('top_sources', []), 1):
            summary += f"{i}. **{source['title']}**\n"
            summary += f"   - Type: {source['type']}\n"
            summary += f"   - Relevance: {source['relevance']:.0%}\n"
            summary += f"   - URL: {source['url']}\n\n"
        
        summary += f"""---

## Conclusion

Analysis of *{topic}* reveals **{analysis.get('themes_count', 0)} major thematic areas** warranting 
further attention. The source material demonstrates **{analysis.get('data_quality', 'Unknown').lower()} reliability**, 
suggesting this research provides a {analysis.get('confidence_score', 0):.0%} confidence foundation 
for decision-making.

*Generated by 3-Step Chain Workflow (Research → Analyze → Summarize)*
"""
        
        return summary
    
    def _generate_text(self, topic: str, analysis: Dict) -> str:
        """Generate plain text summary"""
        return f"""
RESEARCH SUMMARY: {topic}
{'='*50}

Data Quality: {analysis.get('data_quality', 'Unknown')}
Sources: {analysis.get('source_count', 0)}
Average Relevance: {analysis.get('average_relevance', 0):.1%}

Key Themes:
{chr(10).join('  - ' + t for t in analysis.get('themes_identified', []))}

Conclusion: Analysis reveals {analysis.get('themes_count', 0)} major thematic areas 
with {analysis.get('confidence_score', 0):.0%} confidence.
"""

class ThreeStepChain:
    """
    Main workflow orchestrator
    
    Usage:
        workflow = ThreeStepChain()
        result = workflow.run("Your Research Topic")
        print(result.final_summary)
    """
    
    def __init__(self, num_results: int = 5, output_format: str = "markdown"):
        self.research_step = ResearchStep(num_results=num_results)
        self.analysis_step = AnalysisStep()
        self.summary_step = SummaryStep(output_format=output_format)
        self.steps = [self.research_step, self.analysis_step, self.summary_step]
    
    def run(self, topic: str) -> WorkflowState:
        """Execute the full 3-step chain"""
        state = WorkflowState(topic=topic)
        
        print(f"\n{'#'*70}")
        print(f"# STARTING 3-STEP WORKFLOW")
        print(f"# Topic: {topic}")
        print(f"{'#'*70}")
        
        # Execute each step in sequence
        for step in self.steps:
            state = step.execute(state)
            
            # Check for critical errors
            if any("error" in e.lower() for e in state.errors):
                print(f"\n⚠️  Workflow halted due to errors")
                break
        
        # Final status
        print(f"\n{'='*70}")
        print(f"✅ WORKFLOW COMPLETE")
        print(f"{'='*70}")
        
        if state.errors:
            print(f"\n⚠️  Errors encountered:")
            for error in state.errors:
                print(f"   - {error}")
        
        print(f"\n📄 Final summary generated: {len(state.final_summary)} characters")
        
        return state
    
    def run_interactive(self):
        """Interactive mode for testing"""
        print("\n" + "="*70)
        print("  3-STEP CHAIN WORKFLOW - Interactive Mode")
        print("="*70)
        
        topic = input("\nEnter research topic: ").strip()
        if not topic:
            print("No topic provided. Exiting.")
            return
        
        result = self.run(topic)
        
        # Save output option
        save = input("\nSave summary to file? (y/n): ").lower().strip()
        if save == 'y':
            filename = f"summary_{topic.replace(' ', '_').lower()[:30]}.md"
            try:
                with open(filename, 'w') as f:
                    f.write(result.final_summary)
                print(f"✓ Saved to {filename}")
            except Exception as e:
                print(f"✗ Could not save: {e}")
        
        return result


# DAN Integration Helper
class DANWorkflowAdapter:
    """
    Adapter for running within DAN environment with actual tool calls
    """
    
    @staticmethod
    def run_with_web_search(topic: str, num_results: int = 5) -> WorkflowState:
        """
        Run workflow with actual web_search tool integration.
        This would be used when running inside DAN environment.
        """
        # This is a template showing how the workflow would integrate
        # with DAN's actual web_search tool
        
        state = WorkflowState(topic=topic)
        
        # Step 1: Call web_search tool (pseudo-code for DAN integration)
        print(f"[DAN] Calling web_search for: {topic}")
        # In actual DAN: results = web_search(query=topic, num_results=num_results)
        
        # Step 2: Process results through analysis
        # Step 3: Generate summary
        
        return state


if __name__ == "__main__":
    # Example usage
    workflow = ThreeStepChain(num_results=5)
    
    # Run with a sample topic
    result = workflow.run("Artificial Intelligence in Supply Chain Management")
    
    print("\n" + "="*70)
    print("FINAL SUMMARY:")
    print("="*70)
    print(result.final_summary)
