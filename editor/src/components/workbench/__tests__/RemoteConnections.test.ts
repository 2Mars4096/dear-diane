// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RemoteConnections } from "../RemoteConnections";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("keeps credential provisioning opt-in and starts an explicit install", async () => {
  const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith("/install")
    ? { id: "job", status: "complete", message: "Installed" }
    : { local: true, connections: [{ id: "mini", name: "Mini", ssh_alias: "mini", relay_ssh_alias: "ny", installed: false }] }), { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  await act(async () => root.render(createElement(RemoteConnections)));
  const checkbox = host.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
  expect(checkbox.checked).toBe(false);
  const install = [...host.querySelectorAll("button")].find(button => button.textContent === "Install DAN")!;
  await act(async () => install.click());
  expect(fetcher).toHaveBeenLastCalledWith("/api/remote/connections/mini/install", expect.objectContaining({ method: "POST", body: JSON.stringify({ share_openrouter: false }) }));
  expect(host.textContent).toContain("Installed");
});

it("remote browsers identify the machine and cannot bootstrap other hosts", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ local: false, machine: "mini", connections: [] }))));
  await act(async () => root.render(createElement(RemoteConnections)));
  expect(host.textContent).toContain("Connected to mini");
  expect(host.textContent).not.toContain("Add remote connection");
  expect(host.querySelector("form")?.action).toContain("/remote/logout");
});
