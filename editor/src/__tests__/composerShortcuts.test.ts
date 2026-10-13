import { expect, it } from "vitest";
import { composerEnterPlacement } from "../lib/composerShortcuts";
const enter = { key: "Enter", shiftKey: false, altKey: false, metaKey: false, ctrlKey: false };
it("steers with Enter and queues with Cmd/Ctrl+Enter only during a run", () => {
  expect(composerEnterPlacement(enter, true)).toBe("steer");
  for (const modifier of ["metaKey", "ctrlKey"]) {
    expect(composerEnterPlacement({ ...enter, [modifier]: true }, true)).toBe("queue");
    expect(composerEnterPlacement({ ...enter, [modifier]: true }, false)).toBe("steer");
  }
});
it("preserves newlines and IME confirmation", () => {
  for (const modifier of ["shiftKey", "altKey", "isComposing"]) {
    expect(composerEnterPlacement({ ...enter, [modifier]: true }, true)).toBeNull();
  }
  expect(composerEnterPlacement({ ...enter, keyCode: 229 }, true)).toBeNull();
});
