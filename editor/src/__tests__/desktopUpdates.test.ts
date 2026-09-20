import { createRequire } from "node:module";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { assertNoActiveWork } from "../../electron/localUpdate";
const require = createRequire(import.meta.url);
const { install } = require("../../electron/updateInstaller.cjs");
const roots: string[] = [];
afterEach(async () => { for (const root of roots.splice(0)) await fs.rm(root, { recursive: true, force: true }); });
async function fixture() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "dan-update-")); roots.push(root);
  const target = path.join(root, "DAN's app.app"), staged = path.join(root, "new.app"), backup = path.join(root, "old.app");
  await fs.mkdir(target); await fs.mkdir(staged);
  await fs.writeFile(path.join(target, "version"), "old"); await fs.writeFile(path.join(staged, "version"), "new");
  await fs.writeFile(path.join(root, "chats"), "preserve");
  return { target, staged, backup, appPid: 123, backendPid: 456, version: "2", statusPath: path.join(root, "status.json") };
}
it("waits for both processes then installs and relaunches without touching user data", async () => {
  const job = await fixture(); let waiting = true;
  const pause = vi.fn(async () => { expect(await fs.readFile(path.join(job.target, "version"), "utf8")).toBe("old"); waiting = false; });
  const launch = vi.fn();
  await install(job, { alive: (pid: number) => pid === 456 && waiting, pause, launch, verify: vi.fn() });
  expect(pause).toHaveBeenCalledOnce(); expect(launch).toHaveBeenCalledWith(job.target);
  expect(await fs.readFile(path.join(job.target, "version"), "utf8")).toBe("new");
  expect(await fs.readFile(path.join(job.backup, "version"), "utf8")).toBe("old");
  expect(await fs.readFile(path.join(path.dirname(job.target), "chats"), "utf8")).toBe("preserve");
});
it("rolls back when launch fails", async () => {
  const job = await fixture(); const launch = vi.fn().mockRejectedValueOnce(new Error("Launch failed")).mockResolvedValueOnce(undefined);
  await expect(install(job, { alive: () => false, verify: vi.fn(), launch })).rejects.toThrow("Launch failed");
  expect(await fs.readFile(path.join(job.target, "version"), "utf8")).toBe("old");
  expect(JSON.parse(await fs.readFile(job.statusPath, "utf8")).state).toBe("failed");
});
it("never replaces a live app after the wait limit or a failed verification", async () => {
  const job = await fixture();
  await expect(install(job, { alive: () => true, pause: async () => {} })).rejects.toThrow("did not finish closing");
  await expect(install(job, { alive: () => false, verify: () => { throw Error("Bad signature"); } })).rejects.toThrow("Bad signature");
  expect(await fs.readFile(path.join(job.target, "version"), "utf8")).toBe("old");
});
it("blocks running and queued work, fails closed on corrupt state, allows idle records", async () => {
  const job = await fixture(); const root = path.dirname(job.target);
  const folder = path.join(root, "chat_v2/tasks"); await fs.mkdir(folder, { recursive: true });
  const record = path.join(folder, "task.json");
  for (const row of [{ status: "running" }, { status: "paused", queue_items: [{ status: "queued" }] }]) {
    await fs.writeFile(record, JSON.stringify(row)); await expect(assertNoActiveWork(root)).rejects.toThrow("Finish or stop");
  }
  await fs.writeFile(record, "invalid"); await expect(assertNoActiveWork(root)).rejects.toThrow();
  await fs.writeFile(record, JSON.stringify({ status: "completed" })); await expect(assertNoActiveWork(root)).resolves.toBeUndefined();
});
