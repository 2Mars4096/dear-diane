// @vitest-environment happy-dom

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ResearchFurnaceSessionCard,
  ResearchFurnaceSessionFamilyGroup,
  type ResearchFurnaceSessionFamilyGroupData,
} from "../ResearchFurnaceSessionCards";
import type { TrainingSession } from "../../../store/useResearchStore";

function buildSession(
  overrides: Partial<TrainingSession> = {},
): TrainingSession {
  return {
    id: "session-1",
    sessionId: "backend-session-1",
    recipeId: "recipe-1",
    name: "Session One",
    topic: "Supply chains",
    status: "running",
    targetPapers: 20,
    processedPapers: 8,
    startedAt: Date.now() - 60_000,
    lastActivityAt: Date.now() - 30_000,
    currentPhase: "distilling",
    sourceCount: 20,
    statusMessage: "Current phase: distilling",
    tags: ["logistics"],
    ...overrides,
  };
}

async function render(node: React.ReactElement) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(node);
  });
  return { container, root };
}

describe("ResearchFurnaceSessionCards", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("renders reconnecting session state and training actions", async () => {
    const session = buildSession({
      status: "paused",
      statusMessage: "Paused. Reconnecting live session updates...",
    });
    const { container, root } = await render(
      React.createElement(ResearchFurnaceSessionCard, {
        session,
        onContinue: () => {},
        onVariant: () => {},
        knownTags: ["logistics", "resilience"],
        activeTagFilters: [],
        onToggleTagFilter: () => {},
      }),
    );

    expect(container.textContent).toContain("Paused");
    expect(container.textContent).toContain("Reconnecting");
    expect(container.textContent).toContain("Resume");
    expect(container.textContent).toContain("Cancel");
    expect(container.textContent).toContain("#logistics");

    await act(async () => {
      root.unmount();
    });
  });

  it("renders family summary badges and expanded child sessions", async () => {
    const family: ResearchFurnaceSessionFamilyGroupData = {
      key: "family-1",
      displayName: "Market Structure",
      topic: "Industrial organization",
      sessions: [buildSession(), buildSession({ id: "session-2", variantLabel: "v2" })],
      visibleSessions: [
        buildSession(),
        buildSession({ id: "session-2", variantLabel: "v2" }),
      ],
      totalCount: 2,
      variantCount: 1,
      latestAt: Date.now() - 30_000,
      totalCostUsd: 0.42,
    };

    const { container, root } = await render(
      React.createElement(ResearchFurnaceSessionFamilyGroup, {
        group: family,
        expanded: true,
        onToggle: () => {},
        onContinue: () => {},
        onVariant: () => {},
        knownTags: ["logistics", "resilience"],
        activeTagFilters: [],
        onToggleTagFilter: () => {},
      }),
    );

    expect(container.textContent).toContain("Market Structure");
    expect(container.textContent).toContain("2 sessions");
    expect(container.textContent).toContain("1 variant");
    expect(container.textContent).toContain("Variant");

    await act(async () => {
      root.unmount();
    });
  });
});
