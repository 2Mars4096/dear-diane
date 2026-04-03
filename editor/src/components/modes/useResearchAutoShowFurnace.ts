import { useEffect } from "react";
import { useResearchStore } from "../../store/useResearchStore";

export function useResearchAutoShowFurnace() {
  const trainingSessions = useResearchStore((state) => state.trainingSessions);
  const pipeline = useResearchStore((state) => state.pipeline);
  const setActiveRailSection = useResearchStore(
    (state) => state.setActiveRailSection,
  );

  const hasActiveTraining = trainingSessions.some(
    (session) => session.status === "running" || session.status === "paused",
  );
  const hasActivePipeline = pipeline.some(
    (stage) => stage.status === "active" || stage.status === "completed",
  );

  useEffect(() => {
    if (hasActiveTraining) {
      setActiveRailSection("training");
    }
  }, [hasActiveTraining, setActiveRailSection]);

  return { pipelineActive: hasActivePipeline };
}
