"""
Review Loop Workflow
====================
An iterative workflow where a writer drafts content and a reviewer
provides feedback until the content is approved.

Flow:
1. Writer drafts initial content based on requirements
2. Reviewer evaluates content and provides feedback
3. Decision node checks if content meets approval criteria
4. If approved → finalize and output
5. If not approved → loop back to writer with feedback
6. Repeat until approved or max iterations reached
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
import json


class ReviewDecision(Enum):
    """Possible outcomes from the review process."""
    APPROVED = "approved"
    NEEDS_REVISION = "needs_revision"
    REJECTED = "rejected"


@dataclass
class ContentDraft:
    """Represents a content draft with metadata."""
    content: str
    version: int = 1
    author: str = "writer"
    created_at: Optional[str] = None
    
    def __post_init__(self):
        if self.created_at is None:
            from datetime import datetime
            self.created_at = datetime.now().isoformat()


@dataclass
class ReviewFeedback:
    """Feedback from the reviewer."""
    decision: ReviewDecision
    comments: str
    suggestions: List[str] = field(default_factory=list)
    score: Optional[float] = None  # 0-100 quality score
    reviewed_at: Optional[str] = None
    
    def __post_init__(self):
        if self.reviewed_at is None:
            from datetime import datetime
            self.reviewed_at = datetime.now().isoformat()


@dataclass
class ReviewLoopState:
    """Maintains state across iterations of the review loop."""
    requirements: str
    current_draft: Optional[ContentDraft] = None
    feedback_history: List[ReviewFeedback] = field(default_factory=list)
    iteration_count: int = 0
    max_iterations: int = 5
    is_complete: bool = False
    final_output: Optional[str] = None


class WriterNode:
    """
    Writer Node: Generates or revises content based on requirements and feedback.
    
    Input: requirements, previous_feedback (optional)
    Output: ContentDraft
    """
    
    def __init__(self, model_config: Optional[Dict] = None):
        self.model_config = model_config or {}
    
    def run(self, state: ReviewLoopState) -> ContentDraft:
        """Generate content based on requirements and any previous feedback."""
        
        if state.iteration_count == 0:
            # Initial draft
            print(f"[Writer] Creating initial draft for: {state.requirements[:50]}...")
            content = self._generate_initial_content(state.requirements)
        else:
            # Revision based on feedback
            last_feedback = state.feedback_history[-1]
            print(f"[Writer] Revising draft (iteration {state.iteration_count + 1})")
            print(f"[Writer] Addressing feedback: {last_feedback.comments[:50]}...")
            content = self._revise_content(
                state.current_draft.content if state.current_draft else "",
                last_feedback
            )
        
        draft = ContentDraft(
            content=content,
            version=state.iteration_count + 1
        )
        return draft
    
    def _generate_initial_content(self, requirements: str) -> str:
        """Generate initial content based on requirements."""
        # In a real implementation, this would call an LLM
        return f"""
# Draft Content

## Based on Requirements:
{requirements}

## Content:
This is the initial draft addressing the specified requirements.
- Point 1: Initial thought on the topic
- Point 2: Supporting evidence
- Point 3: Conclusion

[Draft v1 - Awaiting review]
"""
    
    def _revise_content(self, current_content: str, feedback: ReviewFeedback) -> str:
        """Revise content based on reviewer feedback."""
        # In a real implementation, this would call an LLM with the feedback
        suggestions_text = "\n".join([f"- {s}" for s in feedback.suggestions])
        return f"""
{current_content}

## Revision Notes (v{len(feedback.comments) + 2}):
Addressed the following feedback:
{feedback.comments}

Specific changes made:
{suggestions_text}

[Revised draft - Awaiting review]
"""


class ReviewerNode:
    """
    Reviewer Node: Evaluates content and provides feedback.
    
    Input: ContentDraft, requirements
    Output: ReviewFeedback
    """
    
    def __init__(self, approval_threshold: float = 80.0):
        self.approval_threshold = approval_threshold
    
    def run(self, draft: ContentDraft, requirements: str) -> ReviewFeedback:
        """Review the content and provide feedback."""
        print(f"[Reviewer] Reviewing draft v{draft.version}...")
        
        # In a real implementation, this would call an LLM for evaluation
        score = self._evaluate_content(draft.content, requirements)
        
        if score >= self.approval_threshold:
            decision = ReviewDecision.APPROVED
            comments = f"Content meets quality standards (Score: {score}/100). Approved for publication."
            suggestions = []
        else:
            decision = ReviewDecision.NEEDS_REVISION
            comments = f"Content needs improvement (Score: {score}/100). See suggestions."
            suggestions = [
                "Add more specific examples to support key points",
                "Improve clarity in the introduction section",
                "Strengthen the conclusion with actionable insights",
                "Consider adding visual elements or data references"
            ]
        
        feedback = ReviewFeedback(
            decision=decision,
            comments=comments,
            suggestions=suggestions,
            score=score
        )
        
        print(f"[Reviewer] Decision: {decision.value} (Score: {score}/100)")
        return feedback
    
    def _evaluate_content(self, content: str, requirements: str) -> float:
        """Evaluate content quality (mock implementation)."""
        # In a real implementation, this would use an LLM or rubric-based scoring
        import random
        # Simulate scoring that improves with iterations
        base_score = 60 + random.randint(0, 20)
        return min(95, base_score)


class DecisionNode:
    """
    Decision Node: Determines if the loop should continue or exit.
    
    Input: ReviewFeedback, iteration_count, max_iterations
    Output: Decision (continue loop or finalize)
    """
    
    def run(self, state: ReviewLoopState, feedback: ReviewFeedback) -> Dict[str, Any]:
        """Make decision based on review feedback and iteration limits."""
        print(f"[Decision] Evaluating iteration {state.iteration_count + 1}/{state.max_iterations}")
        
        if feedback.decision == ReviewDecision.APPROVED:
            print("[Decision] ✓ Content approved - finalizing output")
            return {
                "action": "finalize",
                "reason": "Content meets approval criteria"
            }
        
        if state.iteration_count >= state.max_iterations - 1:
            print("[Decision] ⚠ Max iterations reached - forcing finalization")
            return {
                "action": "finalize",
                "reason": "Maximum iterations reached"
            }
        
        print("[Decision] → Continuing to next revision cycle")
        return {
            "action": "revise",
            "reason": feedback.comments
        }


class ReviewLoopWorkflow:
    """
    Main workflow orchestrator for the review loop.
    """
    
    def __init__(
        self,
        max_iterations: int = 5,
        approval_threshold: float = 80.0
    ):
        self.writer = WriterNode()
        self.reviewer = ReviewerNode(approval_threshold)
        self.decision = DecisionNode()
        self.max_iterations = max_iterations
    
    def run(self, requirements: str) -> ReviewLoopState:
        """
        Execute the review loop workflow.
        
        Args:
            requirements: The content requirements/prompt
            
        Returns:
            Final state with approved content or exhausted iterations
        """
        print("\n" + "="*60)
        print("STARTING REVIEW LOOP WORKFLOW")
        print("="*60)
        
        # Initialize state
        state = ReviewLoopState(
            requirements=requirements,
            max_iterations=self.max_iterations
        )
        
        while not state.is_complete and state.iteration_count < state.max_iterations:
            print(f"\n--- Iteration {state.iteration_count + 1} ---")
            
            # Step 1: Writer creates/revises content
            draft = self.writer.run(state)
            state.current_draft = draft
            
            # Step 2: Reviewer evaluates content
            feedback = self.reviewer.run(draft, state.requirements)
            state.feedback_history.append(feedback)
            
            # Step 3: Decision node determines next action
            decision = self.decision.run(state, feedback)
            
            if decision["action"] == "finalize":
                state.is_complete = True
                state.final_output = draft.content
            else:
                state.iteration_count += 1
        
        # Finalize
        if not state.is_complete:
            state.final_output = state.current_draft.content if state.current_draft else None
        
        print("\n" + "="*60)
        print("WORKFLOW COMPLETE")
        print("="*60)
        print(f"Total iterations: {state.iteration_count + 1}")
        print(f"Final status: {'Approved' if state.feedback_history[-1].decision == ReviewDecision.APPROVED else 'Max iterations reached'}")
        
        return state
    
    def get_summary(self, state: ReviewLoopState) -> str:
        """Generate a summary of the review process."""
        summary = f"""
Review Loop Summary
===================
Requirements: {state.requirements[:50]}...
Iterations: {state.iteration_count + 1}/{state.max_iterations}
Final Status: {"✓ Approved" if state.is_complete and state.feedback_history[-1].decision == ReviewDecision.APPROVED else "⚠ Max iterations"}

Feedback History:
"""
        for i, fb in enumerate(state.feedback_history, 1):
            summary += f"\n  Iteration {i}: {fb.decision.value} (Score: {fb.score}/100)"
            summary += f"\n    Comments: {fb.comments[:80]}..."
        
        return summary


# Example usage and testing
if __name__ == "__main__":
    # Example 1: Simple review loop
    print("\nExample 1: Article Review Loop")
    workflow = ReviewLoopWorkflow(max_iterations=5, approval_threshold=75)
    
    requirements = """
    Write a blog post about supply chain resilience in the context of 
    the Panama Canal. The post should be informative, data-driven, and 
    suitable for an executive audience.
    """
    
    final_state = workflow.run(requirements)
    print(workflow.get_summary(final_state))
    
    # Example 2: Academic paper review simulation
    print("\n\nExample 2: Paper Review Loop")
    workflow2 = ReviewLoopWorkflow(max_iterations=3, approval_threshold=85)
    
    paper_requirements = """
    Draft a literature review section on equity research methodologies,
    focusing on quantitative approaches and their applications in 
    emerging markets.
    """
    
    final_state2 = workflow2.run(paper_requirements)
    print(workflow2.get_summary(final_state2))
