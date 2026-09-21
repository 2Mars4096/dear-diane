import { expect, it } from "vitest";
import { closeMainTab, type MainTab } from "../MainTabs";

const tabs: MainTab[] = [{ id: "chat", kind: "chat", label: "Chat" }, { id: "pdf:a", kind: "pdf", label: "a.pdf" }, { id: "pdf:b", kind: "pdf", label: "b.pdf" }];
it("closes tabs, activating a neighbour and keeping the last tab", () => {
  expect(closeMainTab(tabs, "pdf:a", "pdf:a")).toEqual({ tabs: [tabs[0], tabs[2]], active: "pdf:b" });
  expect(closeMainTab(tabs, "pdf:b", "pdf:b").active).toBe("pdf:a");
  expect(closeMainTab(tabs, "pdf:a", "chat")).toEqual({ tabs: [tabs[1], tabs[2]], active: "pdf:a" });   // the chat tab can be closed
  expect(closeMainTab([tabs[1]], "pdf:a", "pdf:a").tabs).toHaveLength(1);                               // never zero tabs
});
