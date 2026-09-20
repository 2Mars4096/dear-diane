import fs from "node:fs/promises";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { randomUUID } from "node:crypto";
const exec = promisify(execFile);

export async function assertNoActiveWork(graphs: string): Promise<void> {
  for (const folder of ["chat_v2/runs", "chat_v2/tasks"]) {
    const directory = path.join(graphs, folder);
    let names: string[];
    try { names = await fs.readdir(directory); } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") continue;
      throw error;
    }
    for (const name of names.filter(name => name.endsWith(".json"))) {
      const record = JSON.parse(await fs.readFile(path.join(directory, name), "utf8"));
      if (["running", "queued", "pending"].includes(record.status)
          || record.queue_items?.some((item: { status: string }) => ["queued", "injected"].includes(item.status))) {
        throw new Error("Finish or stop active work and clear queued messages before installing.");
      }
    }
  }
}

export async function inspectBundle(bundle: string): Promise<{ version: string; path: string }> {
  const resolved = await fs.realpath(bundle);
  if (path.extname(resolved) !== ".app") throw new Error("Choose a DAN.app build.");
  const plist = path.join(resolved, "Contents/Info.plist");
  const read = async (key: string) => (await exec("/usr/libexec/PlistBuddy", ["-c", `Print :${key}`, plist])).stdout.trim();
  if (await read("CFBundleIdentifier") !== "com.dan.desktop") throw new Error("This build is not DAN.");
  const executable = await read("CFBundleExecutable");
  if (path.basename(executable) !== executable) throw new Error("Invalid application executable.");
  const architectures = (await exec("/usr/bin/lipo", ["-archs", path.join(resolved, "Contents/MacOS", executable)])).stdout.trim().split(/\s+/);
  if (!architectures.includes(process.arch === "arm64" ? "arm64" : "x86_64")) throw new Error("This build is for a different Mac architecture.");
  await exec("/usr/bin/codesign", ["--verify", "--deep", "--strict", resolved]);
  return { path: resolved, version: await read("CFBundleShortVersionString") };
}

export async function stageBundle(source: string, target: string, updatesDir: string) {
  const info = await inspectBundle(source);
  target = await fs.realpath(target);
  if (info.path === target || info.path.startsWith(target + path.sep)) throw new Error("Choose a separate new build, not the running app.");
  const parent = path.dirname(target);
  await fs.access(parent, fs.constants.W_OK);
  const id = randomUUID();
  const staged = path.join(parent, `.DAN-update-${id}.app`);
  const backup = path.join(parent, `.DAN-previous-${id}.app`);
  await fs.mkdir(updatesDir, { recursive: true, mode: 0o700 });
  try {
    await exec("/usr/bin/ditto", [info.path, staged]);
    // The operator's installed icon survives local development builds.
    const icon = async (bundle: string) => (await exec("/usr/libexec/PlistBuddy", ["-c", "Print :CFBundleIconFile", path.join(bundle, "Contents/Info.plist")])).stdout.trim();
    const [oldIcon, newIcon] = await Promise.all([icon(target), icon(staged)]);
    if ([oldIcon, newIcon].every(name => name && path.basename(name) === name)) {
      await fs.copyFile(path.join(target, "Contents/Resources", oldIcon), path.join(staged, "Contents/Resources", newIcon));
      await exec("/usr/bin/codesign", ["--force", "--deep", "--sign", "-", staged]);
    }
    await inspectBundle(staged);
    return { staged, target, backup, version: info.version, statusPath: path.join(updatesDir, "last-install.json") };
  } catch (error) { await fs.rm(staged, { recursive: true, force: true }); throw error; }
}
