import { expect, it } from "vitest";
import { workbenchStatus } from "../WorkbenchConversation";

it("hides idle and raw event statuses but keeps meaningful ones", () => {
  expect(workbenchStatus("Ready")).toBe("");
  expect(workbenchStatus("model_text_delta")).toBe("");
  expect(workbenchStatus("failed")).toBe("Run failed");
  expect(workbenchStatus("Loading session")).toBe("Loading session");
});
