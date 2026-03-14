import { dispatchEngineEvent } from "./researchEventRouter";

/**
 * Simulate a full research pipeline execution for demo / testing purposes.
 * Returns a cancel function that clears all pending timers.
 */
export function simulateResearchPipeline(): () => void {
  const timers: ReturnType<typeof setTimeout>[] = [];

  const stages = [
    { id: "search", delay: 1000 },
    { id: "read", delay: 2000 },
    { id: "analyze", delay: 1500 },
    { id: "outline", delay: 1000 },
    { id: "write", delay: 3000 },
    { id: "review", delay: 2000 },
    { id: "finalize", delay: 1000 },
  ];

  let cumDelay = 0;

  for (const stage of stages) {
    const startDelay = cumDelay;
    const endDelay = cumDelay + stage.delay;

    timers.push(
      setTimeout(() => {
        dispatchEngineEvent({
          type: "node_started",
          payload: { stageId: stage.id },
          timestamp: Date.now(),
        });
      }, startDelay),
    );

    timers.push(
      setTimeout(() => {
        dispatchEngineEvent({
          type: "node_completed",
          payload: { stageId: stage.id },
          timestamp: Date.now(),
        });
      }, endDelay),
    );

    cumDelay = endDelay + 200;
  }

  // Simulate a reference discovery during the search phase
  timers.push(
    setTimeout(() => {
      dispatchEngineEvent({
        type: "artifact_created",
        payload: {
          artifactType: "reference",
          title: "Supply Chain Resilience Under Disruption: A Review",
          authors: ["Zhang, L.", "Wang, K."],
          year: 2024,
          abstract:
            "This paper reviews supply chain resilience strategies under various disruption scenarios.",
          doi: "10.1234/example",
          tags: ["supply-chain", "resilience"],
        },
        timestamp: Date.now(),
      });
    }, 3000),
  );

  // Simulate a second reference
  timers.push(
    setTimeout(() => {
      dispatchEngineEvent({
        type: "artifact_created",
        payload: {
          artifactType: "reference",
          title: "Multi-Echelon Inventory Optimization with Demand Uncertainty",
          authors: ["Chen, X.", "Li, M.", "Park, S."],
          year: 2025,
          doi: "10.5678/inventory",
          tags: ["inventory", "optimization"],
        },
        timestamp: Date.now(),
      });
    }, 4500),
  );

  // Simulate a document section being generated during the write phase
  timers.push(
    setTimeout(() => {
      dispatchEngineEvent({
        type: "artifact_created",
        payload: {
          artifactType: "document_section",
          sectionTitle: "Literature Review",
          content:
            "This section reviews the existing literature on supply chain resilience.\n\n" +
            "Zhang and Wang (2024) provide a comprehensive overview of disruption scenarios...\n" +
            "Chen et al. (2025) extend the analysis to multi-echelon settings...",
        },
        timestamp: Date.now(),
      });
    }, 7000),
  );

  // Simulate a review comment during the review phase
  timers.push(
    setTimeout(() => {
      dispatchEngineEvent({
        type: "review_output",
        payload: {
          section: "Literature Review",
          severity: "suggestion",
          comment:
            "Consider adding a comparison table summarizing the key differences between disruption models.",
        },
        timestamp: Date.now(),
      });
    }, 10000),
  );

  // Simulate streaming chunks during the write phase
  const streamChunks = [
    "\n\n## Methodology\n\n",
    "We adopt a ",
    "mixed-methods approach ",
    "combining analytical modeling ",
    "with empirical validation.\n\n",
    "The primary dataset is sourced from...",
  ];
  let chunkDelay = 8000;
  for (const chunk of streamChunks) {
    timers.push(
      setTimeout(() => {
        dispatchEngineEvent({
          type: "llm_chunk",
          payload: { target: "writing_pane", chunk },
          timestamp: Date.now(),
        });
      }, chunkDelay),
    );
    chunkDelay += 300;
  }

  return () => {
    for (const t of timers) clearTimeout(t);
  };
}
