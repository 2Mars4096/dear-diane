// @vitest-environment happy-dom
import { expect, it } from "vitest";
import { loadWorkerProfiles } from "../NativeWorkers";

it("migrates saved Claude aliases to full model IDs", () => {
  localStorage.setItem("dan.test.profiles", JSON.stringify({ claude: { enabled: true, model: "opus" }, codex: { model: "gpt-5.5" } }));
  const profiles = loadWorkerProfiles("dan.test.profiles");
  expect(profiles.claude.model).toBe("claude-opus-5-5");
  expect(profiles.codex.model).toBe("gpt-5.5");
});

it('restores multiple named configurations for the same runtime', () => {
  localStorage.setItem('dan.test.roster', JSON.stringify({
    builder: { backend: 'codex', enabled: true, name: 'Builder', model: 'gpt-6-astra' },
    reviewer: { backend: 'codex', enabled: true, name: 'Reviewer', model: 'gpt-6-sol' },
    invalid: { backend: 'unknown', enabled: true },
  }));
  const profiles = loadWorkerProfiles('dan.test.roster');
  expect(Object.keys(profiles)).toEqual(['builder', 'reviewer']);
  expect(profiles.builder.model).toBe('gpt-6-astra');
  expect(profiles.reviewer.model).toBe('gpt-6-sol');
});


it('includes visible roster workers even when the old availability checkbox was off', () => {
  localStorage.setItem('dan.nativeWorkerProfiles.v1', JSON.stringify({
    codex: { backend: 'codex', enabled: false, model: 'gpt-6-astra' },
    reviewer: { backend: 'claude', enabled: false, model: 'claude-opus-5-5' },
    claude: { enabled: false, model: '' },
  }));
  const profiles = loadWorkerProfiles();
  expect(profiles.codex.enabled).toBe(true);
  expect(profiles.reviewer.enabled).toBe(true);
  expect(profiles.claude.enabled).toBe(false);
});
