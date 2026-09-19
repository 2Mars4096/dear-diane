import { describe, expect, it } from "vitest";
import { heldDirectionSlot, reconcileSessionSlots, type NavigationSession } from "../navigation";

describe("directional chords", () => {
  it.each([
    [["ArrowUp", "ArrowRight"], 1], [["ArrowDown", "ArrowRight"], 3],
    [["ArrowDown", "ArrowLeft"], 5], [["ArrowUp", "ArrowLeft"], 7],
    [["w", "d"], 1], [["s", "d"], 3], [["s", "a"], 5], [["w", "a"], 7],
    [["W", "D"], 1], [["w", "ArrowUp", "d"], 1],
    [["w"], 0], [["d"], 2], [["s"], 4], [["a"], 6],
    [["w", "s"], null], [[], null],
  ] as [string[], number | null][]) ("maps held %j to slot %s", (keys, slot) => {
    expect(heldDirectionSlot(keys)).toBe(slot);
  });
});

const sessions: NavigationSession[] = Array.from({ length: 9 }, (_, index) => ({
  id: `session-${index}`, title: `Session ${index}`, createdAt: `2026-09-${String(index + 1).padStart(2, "0")}T00:00:00Z`,
}));
describe("session wheel muscle memory", () => {
  it("places initial sessions clockwise by creation and leaves empty compass slots", () => {
    expect(reconcileSessionSlots([], sessions.slice(0, 3))).toEqual(["session-0", "session-1", "session-2", null, null, null, null, null]);
  });
  it("does not move sessions when input order or titles change", () => {
    const before = reconcileSessionSlots([], sessions.slice(0, 8));
    expect(reconcileSessionSlots(before, [...sessions.slice(0, 8)].reverse().map((item) => ({ ...item, title: "Edited" })))).toEqual(before);
  });
  it("replaces the oldest session in place when a ninth session arrives", () => {
    const before = reconcileSessionSlots([], sessions.slice(0, 8));
    expect(reconcileSessionSlots(before, sessions)).toEqual(["session-8", ...before.slice(1)]);
  });
  it("preserves surviving positions when archived sessions disappear", () => {
    const before = reconcileSessionSlots([], sessions.slice(0, 4));
    expect(reconcileSessionSlots(before, [sessions[0], sessions[2], sessions[3]])).toEqual(["session-0", null, "session-2", "session-3", null, null, null, null]);
  });
  it("isolates a project's eligible sessions and repairs stale or duplicate saved IDs", () => {
    expect(reconcileSessionSlots(["foreign", "session-2", "session-2"], sessions.slice(0, 3))).toEqual(["session-0", "session-2", "session-1", null, null, null, null, null]);
  });
  it("compares creation instants across timezone offsets", () => {
    const items = [...sessions.slice(0, 7), { id: "earlier", title: "Earlier", createdAt: "2026-09-10T01:00:00+08:00" }, { id: "later", title: "Later", createdAt: "2026-09-09T23:00:00Z" }];
    const result = reconcileSessionSlots([], items);
    expect(result.at(-1)).toBe("later");
    expect(result).not.toContain("session-0");
  });
});
