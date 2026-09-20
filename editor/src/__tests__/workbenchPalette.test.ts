// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from "vitest";
import { applyAppearanceTheme, subscribeToSystemAppearance } from "../lib/appearanceTheme";
import { workbenchPalette } from "../lib/workbenchPalette";

afterEach(() => vi.unstubAllGlobals());

it("keeps the scheme when system appearance changes and does not follow Notes tone", () => {
  let dark = false;
  let changed = () => {};
  const remove = vi.fn();
  vi.stubGlobal("matchMedia", () => ({ get matches() { return dark; }, addEventListener: (_: string, listener: () => void) => { changed = listener; }, removeEventListener: remove }));
  const apply = () => applyAppearanceTheme("system", "night", "neutral");
  const unsubscribe = subscribeToSystemAppearance(apply);
  apply();
  expect(document.documentElement.style.getPropertyValue("--dan-wb-background")).toBe("#FAFAFA");
  dark = true;
  changed();
  expect(document.documentElement.style.getPropertyValue("--dan-wb-background")).toBe("#0D0D0D");
  expect(document.documentElement.dataset.workbenchPalette).toBe("neutral");
  unsubscribe();
  expect(remove).toHaveBeenCalled();
});

it("falls back to the warm scheme for unknown saved values", () => {
  expect(workbenchPalette("removed-palette").id).toBe("warm");
});
