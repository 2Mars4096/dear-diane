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
});
