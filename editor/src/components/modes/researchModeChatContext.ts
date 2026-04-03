interface ResearchModeChatContextState {
  activePaperId: string | null;
  activeRailSection: "library" | "plan" | "training";
  primaryTab: "editor" | "reader" | "furnace";
  documentContent: string;
  papers: Array<{
    id: string;
    title: string;
    authors: string[];
    year: number;
    filePath?: string;
  }>;
  annotations: Array<{
    paperId: string;
    page: number;
    text: string;
  }>;
  pageSummaries: Record<
    string,
    {
      paperId: string;
      page: number;
      figures: string[];
    }
  >;
  trainingSessions: Array<{
    name: string;
    status: "idle" | "running" | "paused" | "completed" | "failed";
    currentPhase?: string;
  }>;
}

export function buildResearchModeChatContext(
  state: ResearchModeChatContextState,
) {
  const lines: string[] = ["[Research Context]"];

  if (state.activePaperId) {
    const paper = state.papers.find((candidate) => candidate.id === state.activePaperId);
    if (paper) {
      lines.push(`Active paper: ${paper.title}`);
      if (paper.filePath) lines.push(`Paper path: ${paper.filePath}`);
      if (paper.authors.length > 0) lines.push(`Authors: ${paper.authors.join(", ")}`);
      if (paper.year) lines.push(`Year: ${paper.year}`);
    }

    const annotations = state.annotations
      .filter((annotation) => annotation.paperId === state.activePaperId)
      .slice(-3);
    if (annotations.length > 0) {
      lines.push(
        "",
        "[Recent annotations]",
        ...annotations.map(
          (annotation) => `- Page ${annotation.page}: ${annotation.text.slice(0, 240)}`,
        ),
      );
    }

    const figureMentions = Object.values(state.pageSummaries)
      .filter((summary) => summary.paperId === state.activePaperId)
      .flatMap((summary) =>
        summary.figures.map((figure) => `- Page ${summary.page}: ${figure}`),
      )
      .slice(0, 5);
    if (figureMentions.length > 0) {
      lines.push("", "[Figure / table mentions]", ...figureMentions);
    }
  }

  const liveSessions = state.trainingSessions
    .filter((session) => session.status === "running" || session.status === "paused")
    .slice(0, 3);
  if (liveSessions.length > 0) {
    lines.push(
      "",
      "[Live training]",
      ...liveSessions.map((session) =>
        session.currentPhase
          ? `- ${session.name}: ${session.status} (${session.currentPhase})`
          : `- ${session.name}: ${session.status}`,
      ),
    );
  }

  lines.push(`Rail section: ${state.activeRailSection}`);
  lines.push(`Primary tab: ${state.primaryTab}`);

  if (state.documentContent) {
    lines.push("", "[Document excerpt]", state.documentContent.slice(0, 2000));
  }

  return lines.join("\n");
}
