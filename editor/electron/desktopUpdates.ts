import { findPreparedBuild, updateErrorMessage } from "./preparedBuild";
import { app, ipcMain, dialog, type BrowserWindow } from "electron";
import { autoUpdater } from "electron-updater";
import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { assertNoActiveWork, stageBundle } from "./localUpdate";

import { UPDATE_CHECK_INTERVAL, updateSchedule } from "./updateSchedule";
import { stagePublishedMacBundle } from "./releaseBundle";

export function registerDesktopUpdates(options: {
  window: () => BrowserWindow | null; graphs: () => string;
  backend: () => { owned: boolean; pid?: number };
}) {
  const updatesDir = path.join(app.getPath("userData"), "updates");
  let local: Awaited<ReturnType<typeof stageBundle>> | null = null;
  let busy = false;
  let background = false;
  let releaseStaging: Promise<void> | null = null;
  const safeError = (error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    return updateErrorMessage(message);
  };
  autoUpdater.logger = null;
  let state = { phase: "idle", currentVersion: app.getVersion(), version: "", source: "", localBuildAvailable: false, message: "", percent: 0, releaseConfigured: false, localSupported: process.platform === "darwin" && app.isPackaged };
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.on("error", error => { if (!background) state = { ...state, phase: "error", message: safeError(error) }; });
  autoUpdater.on("update-available", info => { state = { ...state, phase: "available", source: "release", version: info.version, message: "A desktop update is available." }; });
  autoUpdater.on("update-not-available", () => { state = { ...state, phase: "idle", message: "You have the latest published desktop version." }; });
  autoUpdater.on("download-progress", progress => { state = { ...state, phase: "downloading", percent: Math.round(progress.percent) }; });
  autoUpdater.on("update-downloaded", info => {
    const ready = () => { state = { ...state, phase: "ready", source: "release", version: info.version, message: "Update downloaded. Install when your work is finished." }; };
    if (process.platform !== "darwin") { ready(); return; }
    state = { ...state, phase: "staging", message: "Preparing the downloaded update…" };
    releaseStaging = stagePublishedMacBundle(info.downloadedFile, target(), updatesDir, info.version)
      .then(prepared => { local = prepared; ready(); })
      .catch(error => { state = { ...state, phase: "error", message: safeError(error) }; });
  });
  const configured = async () => {
    try { await fs.access(path.join(process.resourcesPath, "app-update.yml")); return app.isPackaged; } catch { return false; }
  };
  const checkPublished = async (quiet = false) => {
    if (!await configured()) {
      if (quiet) return;
      throw new Error("Published updates are not configured for this build.");
    }
    // Public releases never consult local accounts or inherited GitHub tokens.
    autoUpdater.setFeedURL({ provider: "github", owner: "2Mars4096", repo: "dear-diane", private: false });
    await autoUpdater.checkForUpdates();
  };
  const automaticCheck = updateSchedule(async () => {
    // Keep downloaded and available updates, and never compete with user actions.
    if (busy || ["available", "ready", "staging", "downloading", "installing"].includes(state.phase)) return;
    busy = true; background = true;
    const previous = { ...state };
    try { await checkPublished(true); }
    catch { state = previous; /* Quiet failures retry on the next scheduled check. */ }
    finally { busy = false; background = false; }
  });
  if (app.isPackaged) {
    let timer: ReturnType<typeof setInterval> | null = null;
    const startup = setTimeout(() => {
      void automaticCheck();
      timer = setInterval(() => void automaticCheck(), UPDATE_CHECK_INTERVAL);
      timer.unref();
    }, 10_000);
    startup.unref();
    const focus = () => { void automaticCheck(); };
    app.on("browser-window-focus", focus);
    app.once("before-quit", () => {
      clearTimeout(startup); if (timer) clearInterval(timer); app.removeListener("browser-window-focus", focus);
    });
  }
  const target = () => path.resolve(app.getPath("exe"), "../../..");
  let candidate: string | null = null;
  let lastDiscovery = 0;
  let discovery: Promise<void> | null = null;
  const discover = async (force = false) => {
    if (!state.localSupported || busy && !force) return;
    if (discovery) return discovery;
    if (!force && Date.now() - lastDiscovery < 15000) return;
    discovery = (async () => {
      let remembered: string | undefined;
      try { remembered = JSON.parse(await fs.readFile(path.join(updatesDir, "local-source.json"), "utf8")).path; } catch { /* none selected */ }
      candidate = await findPreparedBuild(path.join(__dirname, "local-build.json"), target(), remembered);
      state.localBuildAvailable = Boolean(candidate);
      lastDiscovery = Date.now();
    })().finally(() => { discovery = null; });
    return discovery;
  };
  const prepare = async (source: string) => {
    state = { ...state, phase: "staging", message: "Preparing the local update…" };
    const prepared = await stageBundle(source, target(), updatesDir);
    if (local) await fs.rm(local.staged, { recursive: true, force: true });
    local = prepared;
    await fs.writeFile(path.join(updatesDir, "local-source.json"), JSON.stringify({path:source}), {mode:0o600});
    state = { ...state, phase: "ready", source: "local", version: local.version, message: "Local update ready. Your chats, settings, and icon will be kept." };
  };
  const idle = async () => {
    if (!app.isPackaged) throw new Error("Install updates from the packaged Dear Diane app.");
    if (!options.backend().owned) throw new Error("Dear Diane is using an externally started backend. Stop it and reopen Dear Diane before updating.");
    await assertNoActiveWork(options.graphs());
  };
  ipcMain.handle("updates:status", async event => {
    if (event.sender !== options.window()?.webContents || event.senderFrame !== event.sender.mainFrame) throw new Error("Untrusted update request");
    state.releaseConfigured = await configured();
    await discover();
    if (state.phase === "idle" && !state.message) {
      try {
        const previous = JSON.parse(await fs.readFile(path.join(updatesDir, "last-install.json"), "utf8"));
        if (previous.state === "failed") state.message = `Previous installation failed: ${previous.error}`;
        else if (previous.state === "installed") state.message = "Local desktop update installed.";
      } catch { /* no previous installation */ }
    }
    return { ...state };
  });
  ipcMain.handle("updates:action", async (event, action: unknown) => {
    if (event.sender !== options.window()?.webContents || event.senderFrame !== event.sender.mainFrame) throw new Error("Untrusted update request");
    if (busy) throw new Error("An update operation is already in progress.");
    busy = true;
    try {
      if (action === "install-local") {
        await idle();
        await discover(true);
        if (!candidate) throw new Error("No new local build was found. Check for a published update or prepare a desktop build first.");
        await prepare(candidate);
        action = "install";
      }
      if (action === "choose") {
        if (!state.localSupported) throw new Error("Local app installation is available in the packaged Mac app.");
        const selected = await dialog.showOpenDialog(options.window()!, { title: "Choose the new Dear Diane.app build", properties: ["openFile"], filters: [{ name: "Dear Diane application", extensions: ["app"] }] });
        if (selected.canceled || !selected.filePaths[0]) return state;
        await prepare(selected.filePaths[0]);
      } else if (action === "check") {
        if (local || state.phase === "ready") throw new Error("An update is already prepared. Install it before checking again.");
        if (!await configured()) throw new Error("Published updates are not configured for this build. You can install a prepared local build below.");
        state = { ...state, phase: "checking", message: "Checking for updates…", source: "release" };
        await checkPublished();
      } else if (action === "download") {
        if (state.phase !== "available" || state.source !== "release") throw new Error("Check for an update first.");
        state = { ...state, phase: "downloading", percent: 0, message: "Downloading update…" };
        await autoUpdater.downloadUpdate();
        if (releaseStaging) await releaseStaging;
      } else if (action === "install") {
        if (state.phase !== "ready") throw new Error("Prepare or download an update first.");
        await idle();
        if (local) {
          const helper = path.join(updatesDir, "installer.cjs");
          const jobFile = path.join(updatesDir, "install-job.json");
          await fs.copyFile(path.join(__dirname, "updateInstaller.cjs"), helper);
          await fs.writeFile(jobFile, JSON.stringify({ ...local, appPid: process.pid, backendPid: options.backend().pid }), { mode: 0o600 });
          const log = await fs.open(path.join(updatesDir, "install.log"), "a", 0o600);
          try {
            const child = spawn(process.execPath, [helper, jobFile], { detached: true, env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" }, stdio: ["ignore", log.fd, log.fd] });
            await new Promise<void>((resolve, reject) => { child.once("spawn", resolve); child.once("error", reject); });
            child.unref();
          } finally { await log.close(); }
          state = { ...state, phase: "installing", message: "Dear Diane will close and reopen with the update." };
          setTimeout(() => app.quit(), 300);
        } else {
          state = { ...state, phase: "installing", message: "Dear Diane will restart to install the update." };
          setTimeout(() => autoUpdater.quitAndInstall(false, true), 300);
        }
      } else throw new Error("Unknown update action.");
      return { ...state };
    } catch (error) {
      state = { ...state, phase: local || state.phase === "ready" ? "ready" : "error", message: safeError(error) };
      return { ...state };
    } finally { busy = false; }
  });
}
