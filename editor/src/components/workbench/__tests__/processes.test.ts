import { expect, it } from "vitest";
import { uptime } from "../ProcessesPanel";
it("formats process uptime", () => {
  expect(uptime(100, 130)).toBe("30s");
  expect(uptime(0, 600)).toBe("10m");
  expect(uptime(0, 3 * 3600 + 25 * 60)).toBe("3h 25m");
  expect(uptime(0, 2 * 86400 + 5)).toBe("2d");
});
