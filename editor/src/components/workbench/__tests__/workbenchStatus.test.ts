import { expect, it } from "vitest";
import { workbenchStatus } from "../WorkbenchConversation";

it("hides idle and raw event statuses but keeps meaningful ones", () => {
  expect(workbenchStatus("Ready")).toBe("");
  expect(workbenchStatus("model_text_delta")).toBe("");
  expect(workbenchStatus("failed")).toBe("Run failed");
  expect(workbenchStatus("Loading session")).toBe("Loading session");
});

it("keeps readable live actions and drops raw event names", async () => {
  const { liveActionTextForTest } = await import("../../workspace/ChunkWorkspaceApp");
  expect(liveActionTextForTest("codex: turn.started")).toBe("");
  expect(liveActionTextForTest("item.completed")).toBe("");
  expect(liveActionTextForTest("Reading app.py")).toBe("Reading app.py");
  expect(liveActionTextForTest("Searched the web.")).toBe("Searched the web");
});
