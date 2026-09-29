// @vitest-environment happy-dom
import { act, createElement, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { ModelFields } from "../ModelFields";
import { EMPTY_PROFILE, leadExecutionProfile, switchModelSource, withLeadSelection, withModel, type Runtime, type WorkerProfile } from "../modelSelection";
import { loadWorkerProfiles } from "../NativeWorkers";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });
const runtime: Runtime = { id: "codex", label: "Codex", available: true, accounts: [{ id: "default", label: "Default" }],
  models: ["gpt-6-astra", "small-model"], model_efforts: { "gpt-6-astra": ["medium", "high"], "small-model": ["low"] },
  efforts: ["low", "medium", "high"], fast: true, version: "test", setup: "",
  sources: [{ id: "native", label: "Native", supported: true }, { id: "openrouter", label: "OpenRouter", supported: true, configured: true,
    models: ["deepseek/deepseek-v4.1-flash"], model_labels: { "deepseek/deepseek-v4.1-flash": "DeepSeek V4.1 Flash" }, efforts: ["low", "medium", "high"] }] };

it("restores each source's model and reasoning and removes incompatible model options", () => {
  const native = { ...EMPTY_PROFILE, model: "gpt-6-astra", effort: "medium", fast: true };
  const router = switchModelSource(native, "openrouter");
  expect(router).toMatchObject({ model: "deepseek/deepseek-v4.1-flash", provider: "openrouter", fast: false });
  expect(switchModelSource({ ...router, effort: "high" }, "native")).toMatchObject(native);
  expect(withModel(runtime, native, "small-model").effort).toBe("");
  const claude = { ...runtime, id: "claude" };
  expect(withModel(claude, { ...router, effort: "high" }, router.model).effort).toBe("");
});

it("loads old gateway selections without changing native Claude aliases or exposing secrets", () => {
  localStorage.setItem("profiles", JSON.stringify({ claude: { model: "opus" }, dan: { model: "deepseek/deepseek-v4.1-flash", base_url: "https://openrouter.ai/api/v1" } }));
  const profiles = loadWorkerProfiles("profiles");
  expect(profiles.claude.model).toBe("claude-opus-5");
  expect(leadExecutionProfile("native", profiles)).toEqual(profiles.dan);
  expect(switchModelSource(profiles.dan, "native").selections?.openrouter?.model).toBe("deepseek/deepseek-v4.1-flash");
});

it("selects OpenRouter for Codex without changing the harness and restores native fast settings", async () => {
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  const saved = vi.fn();
  function Harness() {
    const [profile, setProfile] = useState<WorkerProfile>({ ...EMPTY_PROFILE, model: "gpt-6-astra", effort: "medium", fast: true });
    return createElement(ModelFields, { runtime, profile, onChange: next => { setProfile(next); saved(next); } });
  }
  function change(label: string, value: string) {
    const input = host.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!;
    act(() => { input.value = value; input.dispatchEvent(new Event("change", { bubbles: true })); });
  }
  try {
    await act(async () => root.render(createElement(Harness)));
    change("Model source", "openrouter");
    expect(host.textContent).toContain("DeepSeek V4.1 Flash");
    expect(host.textContent).not.toContain("Account");
    expect(host.querySelector<HTMLInputElement>('input[type="checkbox"]')?.disabled).toBe(true);
    change("Reasoning", "high");
    expect(saved.mock.lastCall![0]).toMatchObject({ provider: "openrouter", model: "deepseek/deepseek-v4.1-flash", effort: "high", fast: false });
    change("Model source", "native");
    expect(saved.mock.lastCall![0]).toMatchObject({ provider: "native", model: "gpt-6-astra", effort: "medium", fast: true });
    change("Model", "__custom__");
    expect(host.querySelector('input[aria-label="Model"]')).not.toBeNull();
  } finally { act(() => root.unmount()); host.remove(); }
});

it("keeps unsupported sources visible but disabled", () => {
  const host = document.createElement("div"); const root = createRoot(host);
  try {
    act(() => root.render(createElement(ModelFields, { runtime: { ...runtime, id: "cursor", sources: runtime.sources!.map(source => ({ ...source, supported: source.id === "native", reason: "Native only" })) }, profile: EMPTY_PROFILE, onChange: vi.fn() })));
    expect(host.querySelector<HTMLOptionElement>('option[value="openrouter"]')?.disabled).toBe(true);
    expect(host.textContent).toContain("Native only");
  } finally { act(() => root.unmount()); }
});

it("uses the same selected source for Diane/native lead payloads and recorded metadata", () => {
  const profile = { ...switchModelSource(EMPTY_PROFILE, "openrouter"), effort: "medium" };
  for (const agent of ["native", "codex", "claude"]) {
    const payload = withLeadSelection({ profile_policy: { backend: agent }, metadata: { selected_agent: agent } }, agent, { dan: profile, codex: profile, claude: profile });
    expect(payload.profile_policy).toMatchObject({ backend: agent, lead_profile: profile });
    expect(payload.metadata).toMatchObject({ selected_agent: agent, selected_model_provider: "openrouter", selected_model: profile.model, selected_reasoning_effort: "medium" });
  }
});
