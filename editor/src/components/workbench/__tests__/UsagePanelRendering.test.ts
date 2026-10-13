// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { UsagePanel } from "../UsagePanel";
it("shows separate CLI/app quotas, unavailable logins, API spend and observation ages", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const now = Date.now() / 1000;
  const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({ fetched_at: now, accounts: [
    { backend: "codex", account: "personal", label: "Native CLI", plan: "pro", observed_at: now - 300, windows: [{ label: "week", used_percent: 4, resets_at: now + 86400 }] },
    { backend: "codex", account: "app", label: "app", plan: "prolite", windows: [{ label: "week", used_percent: 7, resets_at: now + 86400 }] },
    { backend: "claude", account: "default", label: "Default profile", windows: [], error: "Could not read Claude Code login" },
    { backend: "openrouter", account: "api", label: "Configured API key", windows: [], spend: { usage_daily: 1.25, usage_monthly: 2.5, limit_remaining: null } },
  ] }) }));
  vi.stubGlobal("fetch", fetcher);
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(UsagePanel)));
    await act(async () => { const details = host.querySelector("details")!; details.open = true; details.dispatchEvent(new Event("toggle")); });
    expect(fetcher).toHaveBeenCalledWith("/api/usage");
    expect(host.querySelectorAll("section")).toHaveLength(4);
    expect(host.textContent).toContain("Native CLI · pro");
    expect(host.textContent).toContain("app · prolite");
    expect(host.textContent).toContain("Checked 5m ago");
    expect(host.textContent).toContain("$1.25");
    expect(host.textContent).toContain("account balance is separate");
    expect(host.querySelectorAll('[role="meter"]')).toHaveLength(2);
  } finally { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); }
});
