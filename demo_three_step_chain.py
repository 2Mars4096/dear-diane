#!/usr/bin/env python3
"""
Demo script for 3-Step Chain Workflow
Usage: python demo_three_step_chain.py [topic]
"""

import sys
import os

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from three_step_chain_enhanced import ThreeStepChain

def main():
    print("="*70)
    print("  3-STEP CHAIN WORKFLOW DEMO")
    print("  Research → Analyze → Summarize")
    print("="*70)
    
    # Get topic from command line or prompt
    if len(sys.argv) > 1:
        topic = " ".join(sys.argv[1:])
    else:
        print("\nExample topics:")
        print("  1. Artificial Intelligence in Supply Chain")
        print("  2. Sustainable Finance Trends 2026")
        print("  3. Quantum Computing Applications")
        print("  4. Remote Work Productivity Research")
        print()
        topic = input("Enter research topic (or press Enter for demo): ").strip()
        if not topic:
            topic = "Impact of Artificial Intelligence on Modern Healthcare"
            print(f"\nUsing demo topic: {topic}")
    
    # Create and run workflow
    print("\n" + "="*70)
    print("  INITIALIZING WORKFLOW")
    print("="*70)
    
    workflow = ThreeStepChain(num_results=5, output_format="markdown")
    
    print("\n✓ Workflow initialized")
    print(f"✓ Topic set: {topic}")
    print(f"✓ Steps configured: Research → Analysis → Summary")
    
    # Execute workflow
    result = workflow.run(topic)
    
    # Display results
    print("\n" + "="*70)
    print("  FINAL OUTPUT")
    print("="*70)
    print(result.final_summary)
    
    # Save to file
    filename = f"output/research_summary_{topic.replace(' ', '_').lower()[:30]}_{os.getpid()}.md"
    os.makedirs("output", exist_ok=True)
    
    try:
        with open(filename, 'w') as f:
            f.write(result.final_summary)
        print(f"\n✓ Saved to: {filename}")
    except Exception as e:
        print(f"\n✗ Could not save file: {e}")
    
    # Display statistics
    print("\n" + "="*70)
    print("  WORKFLOW STATISTICS")
    print("="*70)
    print(f"Topic:              {result.topic}")
    print(f"Sources found:      {len(result.research_data)}")
    print(f"Themes identified:  {result.analysis_results.get('themes_count', 0)}")
    print(f"Data quality:       {result.analysis_results.get('data_quality', 'Unknown')}")
    print(f"Avg relevance:      {result.analysis_results.get('average_relevance', 0):.1%}")
    print(f"Confidence:         {result.analysis_results.get('confidence_score', 0):.1%}")
    print(f"Summary length:     {len(result.final_summary)} characters")
    
    if result.errors:
        print(f"\n⚠️  Errors: {len(result.errors)}")
        for error in result.errors:
            print(f"   - {error}")
    
    print("\n" + "="*70)
    print("  DEMO COMPLETE")
    print("="*70)

if __name__ == "__main__":
    main()
