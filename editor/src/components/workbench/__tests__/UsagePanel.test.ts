import { expect, it } from "vitest";
import { agoLabel, resetsIn, usageLevel } from "../UsagePanel";
it("phrases reset and freshness times", () => {
  const now = 1_000_000;
  expect(resetsIn(now + 1500, now)).toBe("resets in 25m");
  expect(resetsIn(now + 3 * 3600, now)).toBe("resets in 3h");
  expect(resetsIn(now + 6 * 86400, now)).toBe("resets in 6d");
  expect(resetsIn(null, now)).toBe("");
  expect(agoLabel(now - 12, now)).toBe("just now");
  expect(agoLabel(now - 300, now)).toBe("5m ago");
});

it("warns under 25% left and turns critical under 10%", () => {
  expect(usageLevel(74)).toBeUndefined();
  expect(usageLevel(75)).toBeUndefined();
  expect(usageLevel(76)).toBe("mid");
  expect(usageLevel(90)).toBe("mid");
  expect(usageLevel(91)).toBe("high");
});
