// @vitest-environment happy-dom
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

let useSettingsStore: typeof import("../store/useSettingsStore").useSettingsStore;
let consoleWarnSpy: ReturnType<typeof vi.spyOn>;

beforeAll(async () => {
  vi.stubGlobal("localStorage", {
    clear: vi.fn(),
    getItem: vi.fn(() => null),
    key: vi.fn(),
    length: 0,
    removeItem: vi.fn(),
    setItem: vi.fn(),
  });
  ({ useSettingsStore } = await import("../store/useSettingsStore"));
});

describe("useSettingsStore", () => {
  beforeEach(() => {
    consoleWarnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  });

  afterEach(() => {
    useSettingsStore.getState().resetToDefaults();
    consoleWarnSpy.mockRestore();
  });

  it("defaults the workspace surface to Factory Worn while preserving theme switching", () => {
    expect(useSettingsStore.getState().workspaceSurfaceTheme).toBe("factory-worn");
    expect(useSettingsStore.getState().workspaceSurfaceTone).toBe("system");

    useSettingsStore.getState().updateSetting("workspaceSurfaceTheme", "original");
    useSettingsStore.getState().updateSetting("workspaceSurfaceTone", "day");

    expect(useSettingsStore.getState().workspaceSurfaceTheme).toBe("original");
    expect(useSettingsStore.getState().workspaceSurfaceTone).toBe("day");
  });

  it("saves the color scheme independently of light/dark mode", () => {
    useSettingsStore.getState().updateSetting("workbenchColorScheme", "midnight");
    useSettingsStore.getState().updateSetting("theme", "vs");
    const saved = JSON.parse(vi.mocked(localStorage.setItem).mock.calls.at(-1)![1]);
    expect(saved.state).toMatchObject({ workbenchColorScheme: "midnight", theme: "vs" });
    useSettingsStore.getState().updateSetting("theme", "vs-dark");
    expect(useSettingsStore.getState().workbenchColorScheme).toBe("midnight");
  });

  it("migrates existing settings without replacing the chosen appearance", async () => {
    vi.mocked(localStorage.getItem).mockReturnValueOnce(JSON.stringify({ version: 6, state: {
      theme: "vs", workspaceSurfaceTheme: "original", workspaceSurfaceTone: "night",
    } }));
    await useSettingsStore.persist.rehydrate();
    expect(useSettingsStore.getState()).toMatchObject({ theme: "vs", workbenchColorScheme: "warm", workspaceSurfaceTone: "night" });
  });
});
