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

it("keeps legacy relay and deployment controls out of the SSH section", async () => {
  const fetcher = vi.fn(async (_url: string) => new Response(JSON.stringify({ local: true, connections: [{ id: "mini", name: "Mini", ssh_alias: "mini", relay_ssh_alias: "ny", relay_enabled: true, ssh_via_relay: true, installed: true }] })));
  vi.stubGlobal("fetch", fetcher);
  await act(async () => root.render(createElement(RemoteConnections)));
  expect(button("Phone access for Mini")).toBeUndefined();
  await act(async () => button("Connection details for Mini").click());
  expect(button("Check SSH")).toBeTruthy();
  for (const label of ["Phone access", "Installation", "Jump host", "OpenRouter", "Show access key"]) expect(host.textContent).not.toContain(label);
  expect(fetcher.mock.calls.some(([url]) => /install|access-key/.test(String(url)))).toBe(false);
});

it("remote browsers identify the machine and cannot bootstrap other hosts", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ local: false, machine: "mini", connections: [] }))));
  await act(async () => root.render(createElement(RemoteConnections)));
  expect(host.textContent).toContain("mini");
  expect(host.textContent).toContain("Connected");
  expect(host.textContent).not.toContain("Add remote connection");
  expect(host.querySelector("form")?.action).toContain("/remote/logout");
});

function button(text: string) { return [...host.querySelectorAll<HTMLButtonElement>("button")].find(item => item.textContent === text || item.getAttribute("aria-label") === text)!; }
async function fill(label: string, value: string) {
  const input = [...host.querySelectorAll("label")].find(item => item.textContent?.startsWith(label))!.querySelector("input")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
it("adds a manual host with optional port/key, without VPN fields or an install", async () => {
  let rows: unknown[] = [];
  let saved: Record<string, unknown> = {};
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/ssh-hosts")) return new Response(JSON.stringify({ hosts: ["mini", "s600"] }));
    if (init?.method === "PUT") { saved = JSON.parse(init.body as string); rows = [saved]; return new Response(JSON.stringify(saved)); }
    return new Response(JSON.stringify({ local: true, connections: rows }));
  });
  vi.stubGlobal("fetch", fetcher);
  await act(async () => root.render(createElement(RemoteConnections)));
  expect(button("Add SSH connection").disabled).toBe(false);
  await act(async () => button("Add SSH connection").click());
  expect(host.querySelector("dialog")?.open).toBe(true);
  expect(host.querySelector('input[placeholder="10.77.77.3"]')).toBeNull();
  expect(host.querySelectorAll("datalist option")).toHaveLength(2);
  for (const label of ["Advanced options", "Remote workspace", "relay", "Python packages"]) expect(host.querySelector("dialog")?.textContent).not.toContain(label);
  await fill("Display name", "Research box");
  await fill("Hostname", "adam@lab.example");
  await fill("SSH port", "2222");
  await act(async () => host.querySelectorAll<HTMLInputElement>('input[type="radio"]')[1].click());
  await fill("Identity file path", "~/.ssh/lab key");
  await act(async () => host.querySelector("dialog form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(saved).toMatchObject({ id: "lab-example", name: "Research box", ssh_alias: "adam@lab.example", ssh_port: 2222, identity_file: "~/.ssh/lab key", relay_enabled: false, ssh_via_relay: false });
  expect(fetcher.mock.calls.some(([url]) => url.endsWith("/install") || url.endsWith("/inspect"))).toBe(false);
  expect(host.querySelector("dialog")).toBeNull();
  expect(button("Add SSH connection").disabled).toBe(false);
  expect(button("Check SSH")).toBeUndefined();
  await act(async () => button("Connection details for Research box").click());
  expect(button("Check SSH")).toBeTruthy();
  expect(button("Set up")).toBeUndefined();
});

it("preserves legacy relay routing when editing and keeps errors in the dialog", async () => {
  const existing = { id: "mini", name: "Mini", ssh_alias: "mini", address: "10.77.77.3", relay_address: "10.77.77.1", relay_ssh_alias: "ny", port: 18765, relay_port: 8765, workspace: "~/work", installed: true };
  let saved: Record<string, unknown> = {};
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/ssh-hosts")) return new Response(JSON.stringify({ hosts: [] }));
    if (init?.method === "PUT") { saved = JSON.parse(init.body as string); return new Response(JSON.stringify({ detail: "Save failed. Try again." }), { status: 409 }); }
    return new Response(JSON.stringify({ local: true, connections: [existing] }));
  }));
  await act(async () => root.render(createElement(RemoteConnections)));
  await act(async () => button("Connection details for Mini").click());
  await act(async () => button("Edit SSH connection").click());
  await fill("Display name", "Renamed Mini");
  await act(async () => host.querySelector("dialog form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(saved).toMatchObject({ ...existing, name: "Renamed Mini", relay_enabled: true, ssh_via_relay: true });
  expect(host.querySelector("dialog [role=alert]")?.textContent).toContain("Save failed");
  expect(host.querySelector("dialog")?.open).toBe(true);
  await act(async () => button("Cancel").click());
  expect(host.querySelector("dialog")).toBeNull();
});

it("keeps rows compact and only shows connected after a successful SSH check", async () => {
  const existing = { id: "mini", name: "Mini", ssh_alias: "mini", url: "http://10.77.77.1:8765", installed: true, relay_enabled: true };
  const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith("/inspect") ? { hostname: "adam-mini", tools: {} } : url.endsWith("/ssh-hosts") ? { hosts: [] } : { local: true, connections: [existing] })));
  vi.stubGlobal("fetch", fetcher);
  await act(async () => root.render(createElement(RemoteConnections)));
  expect(host.textContent).not.toContain(existing.url);
  expect(host.textContent).not.toContain("SSH connected");
  expect(host.textContent).not.toContain("OpenRouter");
  expect(button("Open projects on Mini").disabled).toBe(false);
  expect(button("Check SSH")).toBeUndefined();
  await act(async () => button("Connection details for Mini").click());
  expect(host.querySelector("dialog")?.open).toBe(true);
  expect(host.querySelector("dialog")?.textContent).not.toContain(existing.url);
  await act(async () => button("Check SSH").click());
  expect(host.querySelector(".wb-ssh-row")?.textContent).toContain("SSH connected");
  await act(async () => button("Close connection details").click());
  expect(host.querySelector("dialog")).toBeNull();
  expect(host.textContent).not.toContain(existing.url);
  expect(fetcher.mock.calls.some(([url]) => url.endsWith("/install"))).toBe(false);
});
