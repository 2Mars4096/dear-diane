import { findPreparedBuild, updateErrorMessage } from "./preparedBuild";
import { app, ipcMain, dialog, type BrowserWindow } from "electron";
import { autoUpdater } from "electron-updater";
import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { assertNoActiveWork, stageBundle } from "./localUpdate";
import { GitHubAuth, githubRelease } from "./githubAuth";

export function registerDesktopUpdates(options: {
  window: () => BrowserWindow | null; graphs: () => string;
  backend: () => { owned: boolean; pid?: number };
}) {
  const updatesDir = path.join(app.getPath("userData"), "updates");
  const github = new GitHubAuth();
  void github.refresh();
  app.once("before-quit", () => github.cancel());
  let local: Awaited<ReturnType<typeof stageBundle>> | null = null;
  let busy = false;
  let updateToken = "";
  const safeError = (error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    return updateErrorMessage(updateToken ? message.split(updateToken).join("[redacted]") : message);
  };
  autoUpdater.logger = null;
  let state = { phase: "idle", currentVersion: app.getVersion(), version: "", source: "", localBuildAvailable: false, message: "", percent: 0, releaseConfigured: false, localSupported: process.platform === "darwin" && app.isPackaged };
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.on("error", error => { state = { ...state, phase: "error", message: safeError(error) }; });
  autoUpdater.on("update-available", info => { state = { ...state, phase: "available", source: "release", version: info.version, message: "A desktop update is available." }; });
  autoUpdater.on("update-not-available", () => { state = { ...state, phase: "idle", message: "You have the latest published desktop version." }; });
  autoUpdater.on("download-progress", progress => { state = { ...state, phase: "downloading", percent: Math.round(progress.percent) }; });
  autoUpdater.on("update-downloaded", info => { state = { ...state, phase: "ready", source: "release", version: info.version, message: "Update downloaded. Install when your work is finished." }; });
  const configured = async () => {
    try { await fs.access(path.join(process.resourcesPath, "app-update.yml")); return app.isPackaged; } catch { return false; }
  };
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
    return { ...state, github: github.state };
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
      if (action === "github-sign-in") {
        github.start();
      } else if (action === "github-cancel") {
        github.cancel();
      } else if (action === "github-status") {
        if (github.state.status !== "signing_in") await github.refresh();
      } else if (action === "choose") {
        if (!state.localSupported) throw new Error("Local app installation is available in the packaged Mac app.");
        const selected = await dialog.showOpenDialog(options.window()!, { title: "Choose the new Dear Diane.app build", properties: ["openFile"], filters: [{ name: "Dear Diane application", extensions: ["app"] }] });
        if (selected.canceled || !selected.filePaths[0]) return state;
        await prepare(selected.filePaths[0]);
      } else if (action === "check") {
        if (local || state.phase === "ready") throw new Error("An update is already prepared. Install it before checking again.");
        await discover(true);
        if (candidate) { await prepare(candidate); return { ...state, github: github.state }; }
        if (!await configured()) throw new Error("Published updates are not configured for this build. You can install a prepared local build below.");
        state = { ...state, phase: "checking", message: "Checking for updates…", source: "release" };
        updateToken = await github.token();
        // An explicit in-memory token selects PrivateGitHubProvider; do not let
        // a different inherited GH_TOKEN override the browser-authorized account.
        autoUpdater.setFeedURL({ ...githubRelease, token: updateToken });
        await autoUpdater.checkForUpdates();
      } else if (action === "download") {
        if (state.phase !== "available" || state.source !== "release") throw new Error("Check for an update first.");
        state = { ...state, phase: "downloading", percent: 0, message: "Downloading update…" };
        await autoUpdater.downloadUpdate();
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
      return { ...state, github: github.state };
    } catch (error) {
      state = { ...state, phase: local || state.phase === "ready" ? "ready" : "error", message: safeError(error) };
      return { ...state, github: github.state };
    } finally { busy = false; }
  });
}
