import { describe, expect, it } from "vitest";
import {
  getResearchFurnaceSessionStatusUi,
  isResearchSessionReconnecting,
} from "../researchFurnaceSessionStatus";

describe("researchFurnaceSessionStatus", () => {
  it("treats running sessions as live by default", () => {
    const ui = getResearchFurnaceSessionStatusUi({
      status: "running",
      statusMessage: "Current phase: distilling",
    });

    expect(ui.label).toBe("Live");
    expect(ui.reconnecting).toBe(false);
    expect(ui.messageTone).toBe("live");
    expect(ui.pulse).toBe(true);
  });

  it("surfaces reconnecting paused sessions as reconnecting info state", () => {
    const session = {
      status: "paused" as const,
      statusMessage: "Paused. Reconnecting live session updates...",
    };

    expect(isResearchSessionReconnecting(session)).toBe(true);

    const ui = getResearchFurnaceSessionStatusUi(session);
    expect(ui.label).toBe("Paused");
    expect(ui.reconnecting).toBe(true);
    expect(ui.messageTone).toBe("info");
    expect(ui.pulse).toBe(false);
    expect(ui.pillClass).toContain("sky");
  });

  it("keeps failed sessions in a failure tone", () => {
    const ui = getResearchFurnaceSessionStatusUi({
      status: "failed",
      statusMessage: "Provider timeout while extracting references",
    });

    expect(ui.label).toBe("Failed");
    expect(ui.reconnecting).toBe(false);
    expect(ui.messageTone).toBe("danger");
    expect(ui.pillClass).toContain("rose");
  });
});
