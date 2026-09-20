import { expect, it, vi } from "vitest";
import { deviceCode, githubEnvironment } from "../../electron/githubAuth";
it("extracts only a device code, including ANSI-colored output", () => {
  expect(deviceCode("! First copy your one-time code: \u001b[1mABCD-1234\u001b[0m\nPress Enter")).toBe("ABCD-1234");
  expect(deviceCode("oauth_token: never-show-this")).toBe("");
});
it("uses the local login instead of inherited automation credentials", () => {
  vi.stubEnv("GH_TOKEN", "automation-secret"); vi.stubEnv("GITHUB_TOKEN", "other-secret");
  try {
    const env = githubEnvironment(); expect(env.GH_TOKEN).toBeUndefined(); expect(env.GITHUB_TOKEN).toBeUndefined();
    expect(process.env.GH_TOKEN).toBe("automation-secret");
  } finally { vi.unstubAllEnvs(); }
});
