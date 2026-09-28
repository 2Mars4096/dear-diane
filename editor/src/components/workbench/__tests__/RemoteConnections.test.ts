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

function button(text: string) { return [...host.querySelectorAll<HTMLButtonElement>("button")].find(item => item.textContent === text)!; }
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
  expect(button("Check SSH")).toBeTruthy();
  expect(button("Set up phone access")).toBeTruthy();
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
  await act(async () => button("Edit").click());
  await fill("Display name", "Renamed Mini");
  await act(async () => host.querySelector("dialog form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(saved).toMatchObject({ ...existing, name: "Renamed Mini", relay_enabled: true, ssh_via_relay: true });
  expect(host.querySelector("dialog [role=alert]")?.textContent).toContain("Save failed");
  expect(host.querySelector("dialog")?.open).toBe(true);
  await act(async () => button("Cancel").click());
  expect(host.querySelector("dialog")).toBeNull();
});
