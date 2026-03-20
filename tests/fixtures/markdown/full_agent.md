---
type: llm
model: claude-sonnet-4-20250514
temperature: 0.3
max_tokens: 4000
output_schema:
  type: object
  properties:
    sections:
      type: array
      items:
        type: object
        properties:
          title:
            type: string
          content:
            type: string
retry_policy:
  max_retries: 3
  backoff: 2.0
  on_failure: skip
---

> Accepts: topic (string), context (string)
> Returns: outline (object)

## System

You are an expert outline planner for academic papers. Follow INFORMS journal style.

Create a detailed paper outline for the given topic, incorporating the provided context.

Structure the outline with clear sections: Introduction, Literature Review, Methodology, Results, Discussion, Conclusion.
