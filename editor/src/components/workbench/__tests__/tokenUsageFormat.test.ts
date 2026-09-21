import { expect, it } from "vitest";
import { curvePoints, fillDays, folderName, formatTokens, percent, visibleSessions, type SessionRow } from "../tokenUsageFormat";

it("formats token counts and shares compactly", () => {
  expect([0, 950, 1500, 34_200, 2_533_365, 265_968_945, 3.2e9].map(formatTokens)).toEqual(["0", "950", "1.5k", "34k", "2.5M", "266M", "3.2B"]);
  expect([0, 0.004, 0.356, 1].map(percent)).toEqual(["0%", "<1%", "36%", "100%"]);
  expect(folderName("/Users/me/projects/panama-canal/")).toBe("panama-canal");
});

it("filters and sorts sessions", () => {
  const row = (id: string, backend: string, total: number, updated_at: number, dan = false) => ({ id, backend, total, updated_at, dan: dan ? { role: "lead", run_id: "r", worker_id: "w" } : null }) as SessionRow;
  const rows = [row("a", "claude", 10, 3), row("b", "codex", 90, 2, true), row("c", "claude", 50, 1)];
  expect(visibleSessions(rows, "all", "tokens").map((item) => item.id)).toEqual(["b", "c", "a"]);
  expect(visibleSessions(rows, "claude", "recent").map((item) => item.id)).toEqual(["a", "c"]);
  expect(visibleSessions(rows, "dan", "recent").map((item) => item.id)).toEqual(["b"]);
});

it("scales the context curve to its peak", () => {
  expect(curvePoints([50, 100], 200, 40)).toBe("0.0,20.0 200.0,0.0");
  expect(curvePoints([7], 200, 40)).toBe("0.0,0.0");
});

it("fills quiet days between active ones", () => {
  const day = (name: string, total: number) => ({ day: name, total, sessions: 1, claude: total, codex: 0 });
  expect(fillDays([day("2026-09-18", 5), day("2026-09-21", 9)]).map((item) => [item.day, item.total])).toEqual([["2026-09-18", 5], ["2026-09-19", 0], ["2026-09-20", 0], ["2026-09-21", 9]]);
  expect(fillDays([])).toEqual([]);
});
