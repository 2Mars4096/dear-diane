import { describe, expect, it } from "vitest";
import { buildResearchModeChatContext } from "../researchModeChatContext";

describe("buildResearchModeChatContext", () => {
  it("includes the active paper, recent annotations, figures, live training, and excerpt", () => {
    const context = buildResearchModeChatContext({
      activePaperId: "paper-1",
      activeRailSection: "training",
      primaryTab: "furnace",
      documentContent: "A".repeat(2100),
      papers: [
        {
          id: "paper-1",
          title: "Attention Is All You Need",
          authors: ["A. Vaswani", "N. Shazeer"],
          year: 2017,
          filePath: "/papers/attention.pdf",
        },
      ],
      annotations: [
        {
          paperId: "paper-1",
          page: 3,
          text: "Scaled dot-product attention stabilizes training.",
        },
      ],
      pageSummaries: {
        "paper-1:2": {
          paperId: "paper-1",
          page: 2,
          figures: ["Transformer encoder stack"],
        },
      },
      trainingSessions: [
        {
          name: "Transformer distillation",
          status: "running",
          currentPhase: "synthesis",
        },
      ],
    });

    expect(context).toContain("[Research Context]");
    expect(context).toContain("Active paper: Attention Is All You Need");
    expect(context).toContain("Paper path: /papers/attention.pdf");
    expect(context).toContain("Authors: A. Vaswani, N. Shazeer");
    expect(context).toContain("[Recent annotations]");
    expect(context).toContain("Page 3: Scaled dot-product attention stabilizes training.");
    expect(context).toContain("[Figure / table mentions]");
    expect(context).toContain("Page 2: Transformer encoder stack");
    expect(context).toContain("[Live training]");
    expect(context).toContain("Transformer distillation: running (synthesis)");
    expect(context).toContain("Rail section: training");
    expect(context).toContain("Primary tab: furnace");
    expect(context).toContain("[Document excerpt]");
    expect(context).toContain("A".repeat(2000));
    expect(context).not.toContain("A".repeat(2001));
  });

  it("still returns stable mode context when no paper is active", () => {
    const context = buildResearchModeChatContext({
      activePaperId: null,
      activeRailSection: "library",
      primaryTab: "editor",
      documentContent: "",
      papers: [],
      annotations: [],
      pageSummaries: {},
      trainingSessions: [],
    });

    expect(context).toContain("Rail section: library");
    expect(context).toContain("Primary tab: editor");
    expect(context).not.toContain("[Recent annotations]");
    expect(context).not.toContain("[Live training]");
  });
});
