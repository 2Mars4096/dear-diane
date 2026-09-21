// @vitest-environment happy-dom
import { expect, it } from "vitest";
import { loadWorkerProfiles } from "../NativeWorkers";

it("migrates saved Claude aliases to full model IDs", () => {
  localStorage.setItem("dan.test.profiles", JSON.stringify({ claude: { enabled: true, model: "opus" }, codex: { model: "gpt-5.5" } }));
  const profiles = loadWorkerProfiles("dan.test.profiles");
  expect(profiles.claude.model).toBe("claude-opus-5");
  expect(profiles.codex.model).toBe("gpt-5.5");
});
