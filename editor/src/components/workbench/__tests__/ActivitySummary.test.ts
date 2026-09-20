import { expect, it } from "vitest";
import { currentActivity } from "../ActivitySummary";
it("uses observed actions without leaking shell commands or inventing completion", () => {
  expect(currentActivity([], "Running /bin/zsh -lc pytest tests/")).toBe("Running checks");
  expect(currentActivity([], "Running /bin/zsh -lc rg TODO src/")).toBe("Checking files");
  expect(currentActivity([], "Running /bin/zsh -lc unknown-command")).toBe("Working on your request");
  expect(currentActivity([{type:"status_reported",event_type:"status_reported",summary:"Inspecting the settings"},{type:"model_text_delta",event_type:"model_text_delta",summary:"Answer text"}])).toBe("Checking files");
  expect(currentActivity([])).toBe("Working on your request");
});
