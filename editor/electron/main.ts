import { app, BrowserWindow, ipcMain, dialog, Tray, Menu, nativeImage, shell } from "electron";
import path from "node:path";
import fs, { type FSWatcher } from "node:fs";
import { spawn, type ChildProcess } from "node:child_process";
import http from "node:http";
import https from "node:https";
import net from "node:net";
import os from "node:os";
import {
  waitForBackendHealth,
  waitForBackendHealthOrRelease,
} from "./backendHealth";
import { buildBackendLaunchEnv } from "./backendLaunch";

let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;

const isDev = !app.isPackaged;
const VITE_DEV_URL = "http://localhost:5173";
const BACKEND_PORT = 8000;
const PROD_SERVER_PORT = 45173;
const BACKEND_PROXY_TIMEOUT_MS = 10000;

// --- Production backend + proxy server ---

let backendProcess: ChildProcess | null = null;
let prodServer: http.Server | null = null;
let prodServerPort = 0;
let backendReady = false;

function getPersistentGraphsDir(): string {
  return path.join(app.getPath("userData"), "graphs");
}

function getPersistentChatAttachmentsDir(): string {
  return path.join(app.getPath("userData"), "chat-attachments");
}

function sanitizeAttachmentStem(name?: string): string {
  const base = (name || "attachment").replace(/\.[^.]+$/, "").trim();
  const sanitized = base
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return sanitized || "attachment";
}

function inferAttachmentExtension(name?: string, mimeType?: string | null): string {
  const ext = path.extname(name || "").toLowerCase();
  if (ext) return ext;
  switch ((mimeType || "").toLowerCase()) {
    case "image/png":
      return ".png";
    case "image/jpeg":
      return ".jpg";
    case "image/webp":
      return ".webp";
    case "image/gif":
      return ".gif";
    case "image/svg+xml":
      return ".svg";
    case "application/pdf":
      return ".pdf";
    case "text/plain":
      return ".txt";
    case "application/json":
      return ".json";
    default:
      return "";
  }
}

function parseDataUrl(dataUrl: string): { mimeType: string | null; buffer: Buffer } {
  const match = /^data:([^;,]+)?(?:;charset=[^;,]+)?(;base64)?,([\s\S]*)$/.exec(dataUrl);
  if (!match) {
    throw new Error("Invalid attachment data URL");
  }
  const mimeType = match[1] || null;
  const isBase64 = Boolean(match[2]);
  const payload = match[3] || "";
  const buffer = isBase64
    ? Buffer.from(payload, "base64")
    : Buffer.from(decodeURIComponent(payload), "utf-8");
  return { mimeType, buffer };
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();

function findPython(): string {
  const candidates = [
    process.env.DAN_PYTHON ?? "",
    path.join(os.homedir(), ".venv", "bin", "python"),
    "/opt/anaconda3/bin/python",
    "/usr/local/bin/python3",
    "/usr/bin/python3",
    "python3",
    "python",
  ].filter(Boolean);
  for (const p of candidates) {
    try {
      const resolved = p.startsWith("/") ? p : "";
      if (resolved && fs.existsSync(resolved)) return resolved;
    } catch { /* skip */ }
  }
  return candidates[candidates.length - 1];
}

function isPortInUse(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const sock = new net.Socket();
    sock.setTimeout(1500);
    sock.once("connect", () => { sock.destroy(); resolve(true); });
    sock.once("error", () => resolve(false));
    sock.once("timeout", () => { sock.destroy(); resolve(false); });
    sock.connect(port, "127.0.0.1");
  });
}

let backendOwnedByUs = false;

async function startBackend(): Promise<void> {
  const alreadyRunning = await isPortInUse(BACKEND_PORT);
  if (alreadyRunning) {
    const portResolution = await waitForBackendHealthOrRelease({
      port: BACKEND_PORT,
      totalTimeoutMs: 5000,
      probeIntervalMs: 250,
      requestTimeoutMs: 1000,
      portInUseFn: () => isPortInUse(BACKEND_PORT),
    });
    if (portResolution === "healthy") {
      console.log(`Backend already running on port ${BACKEND_PORT}, reusing.`);
      backendOwnedByUs = false;
      backendReady = true;
      return;
    }
    if (portResolution === "timeout") {
      throw new Error(
        `Port ${BACKEND_PORT} stayed in use, but no healthy DAN backend responded at /api/health.`,
      );
    }
    console.warn(
      `Port ${BACKEND_PORT} was briefly occupied by an unhealthy listener, then cleared. Starting a fresh DAN backend.`,
    );
  }

  const danServe = process.env.DAN_SERVE_CMD;
  const graphsDir = process.env.DAN_GRAPHS_DIR || getPersistentGraphsDir();
  const env = buildBackendLaunchEnv({ env: process.env, graphsDir });
  const backendGraphsDir = env.DAN_GRAPHS_DIR || graphsDir;
  const workspaceRoot = env.DAN_WORKSPACE_ROOT || path.dirname(backendGraphsDir);
  fs.mkdirSync(backendGraphsDir, { recursive: true });
  fs.mkdirSync(workspaceRoot, { recursive: true });
  const danServeParts = danServe?.split(/\s+/).filter(Boolean) ?? [];

  const proc = danServe
    ? spawn(danServeParts[0], [...danServeParts.slice(1), "--no-reload"], {
        cwd: workspaceRoot,
        env,
        stdio: ["ignore", "pipe", "pipe"],
      })
    : spawn(findPython(), ["-m", "dan.server", "--no-reload"], {
        cwd: workspaceRoot,
        env,
        stdio: ["ignore", "pipe", "pipe"],
      });

  backendProcess = proc;
  backendOwnedByUs = true;
  backendReady = false;

  console.log(`Using DAN_GRAPHS_DIR=${backendGraphsDir}`);
  console.log(`Using DAN_WORKSPACE_ROOT=${workspaceRoot}`);
  sendBackendStatus();

  proc.stdout?.on("data", (d: Buffer) => {
    const text = d.toString();
    console.log("[backend]", text.trimEnd());
    mainWindow?.webContents.send("backend:log", { text, stream: "stdout" });
  });

  proc.stderr?.on("data", (d: Buffer) => {
    const text = d.toString();
    console.error("[backend]", text.trimEnd());
    mainWindow?.webContents.send("backend:log", { text, stream: "stderr" });
  });

  const startupFailure = new Promise<Error>((resolve) => {
    proc.once("error", (err) => {
      console.error("Failed to start backend:", err.message);
      resolve(err);
    });
    proc.once("exit", (code) => {
      resolve(new Error(`Backend exited before becoming healthy (code ${code})`));
    });
  });

  const healthReady = waitForBackendHealth({
    port: BACKEND_PORT,
    totalTimeoutMs: 30000,
    probeIntervalMs: 250,
    requestTimeoutMs: 1000,
  }).then((healthy) =>
    healthy
      ? null
      : new Error(
          `Timed out waiting for DAN backend health on http://127.0.0.1:${BACKEND_PORT}/api/health.`,
        ),
  );

  const startupResult = await Promise.race([startupFailure, healthReady]);
  if (startupResult instanceof Error) {
    backendReady = false;
    if (backendProcess === proc) {
      backendProcess = null;
    }
    if (!proc.killed) {
      try { proc.kill(); } catch {}
    }
    sendBackendStatus();
    throw startupResult;
  }

  backendReady = true;
  sendBackendStatus();

  proc.on("error", (err) => {
    console.error("Backend runtime error:", err.message);
    backendReady = false;
    sendBackendStatus();
  });

  proc.on("exit", (code) => {
    console.log("Backend exited with code", code);
    if (backendProcess === proc) {
      backendProcess = null;
    }
    backendReady = false;
    sendBackendStatus();
  });
}

function getMimeType(ext: string): string {
  const mimes: Record<string, string> = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".map": "application/json",
  };
  return mimes[ext] || "application/octet-stream";
}

function startProductionServer(distDir: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const url = req.url ?? "/";

      if (url.startsWith("/api/")) {
        const proxyReq = http.request(
          { hostname: "127.0.0.1", port: BACKEND_PORT, path: url, method: req.method, headers: { ...req.headers, host: `127.0.0.1:${BACKEND_PORT}` } },
          (proxyRes) => {
            res.writeHead(proxyRes.statusCode ?? 502, proxyRes.headers);
            proxyRes.pipe(res);
          },
        );
        proxyReq.setTimeout(BACKEND_PROXY_TIMEOUT_MS, () => {
          proxyReq.destroy(new Error("Backend request timed out"));
        });
        req.on("aborted", () => {
          proxyReq.destroy();
        });
        proxyReq.on("error", (err) => {
          if (res.headersSent) {
            res.destroy();
            return;
          }
          const timedOut = err.message.includes("timed out");
          res.writeHead(timedOut ? 504 : 502);
          res.end(timedOut ? "Backend request timed out" : "Backend unavailable");
        });
        req.pipe(proxyReq);
        return;
      }

      let filePath = path.join(distDir, url === "/" ? "index.html" : url);
      if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
        filePath = path.join(distDir, "index.html");
      }
      try {
        const content = fs.readFileSync(filePath);
        const ext = path.extname(filePath);
        res.writeHead(200, { "Content-Type": getMimeType(ext), "Content-Length": content.length });
        res.end(content);
      } catch {
        res.writeHead(404);
        res.end("Not found");
      }
    });

    server.on("upgrade", (req, socket, head) => {
      const url = req.url ?? "";
      if (!url.startsWith("/api/")) {
        socket.destroy();
        return;
      }
      const proxyReq = http.request({
        hostname: "127.0.0.1",
        port: BACKEND_PORT,
        path: url,
        method: "GET",
        headers: { ...req.headers, host: `127.0.0.1:${BACKEND_PORT}` },
      });

      proxyReq.on("upgrade", (_proxyRes, proxySocket, proxyHead) => {
        socket.write(
          "HTTP/1.1 101 Switching Protocols\r\n" +
          "Upgrade: websocket\r\n" +
          "Connection: Upgrade\r\n" +
          Object.entries(_proxyRes.headers)
            .filter(([k]) => !["upgrade", "connection"].includes(k.toLowerCase()))
            .map(([k, v]) => `${k}: ${v}`)
            .join("\r\n") +
          "\r\n\r\n",
        );
        if (proxyHead.length) socket.write(proxyHead);
        proxySocket.pipe(socket as net.Socket);
        (socket as net.Socket).pipe(proxySocket);
      });

      proxyReq.on("error", () => socket.destroy());
      proxyReq.end();
    });

    server.listen(PROD_SERVER_PORT, "127.0.0.1", () => {
      const addr = server.address() as net.AddressInfo;
      prodServerPort = addr.port;
      prodServer = server;
      console.log(`Production server listening on http://127.0.0.1:${prodServerPort}`);
      resolve(prodServerPort);
    });

    server.on("error", reject);
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    title: "DAN",
    titleBarStyle: "hiddenInset",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      webviewTag: true,
    },
  });

  if (isDev) {
    mainWindow.loadURL(VITE_DEV_URL);
    mainWindow.webContents.openDevTools({ mode: "detach" });
  } else {
    mainWindow.loadURL(`http://127.0.0.1:${prodServerPort}`);
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function createTray() {
  const icon = nativeImage.createFromDataURL(
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABHNCSVQICAgIfAhkiAAAADlJREFUOI1jYBhsgJGBgYGBgYHhPwMDA8P/////MzAwMDAQC5gYSABDwwBGIgxgZGAgCwxtA0YBAACbvxAJkHDJOAAAAABJRU5ErkJggg=="
  );
  tray = new Tray(icon);
  const contextMenu = Menu.buildFromTemplate([
    { label: "Open DAN", click: () => mainWindow?.show() || createWindow() },
    { type: "separator" },
    { label: "Quit", click: () => app.quit() },
  ]);
  tray.setToolTip("DAN");
  tray.setContextMenu(contextMenu);
  tray.on("click", () => mainWindow?.show() || createWindow());
}

// --- IPC Handlers: file operations for the renderer ---

ipcMain.handle("dialog:openDirectory", async () => {
  if (!mainWindow) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openDirectory"],
  });
  if (result.canceled) return null;
  return result.filePaths[0];
});

function expandHome(p: string): string {
  if (p.startsWith("~")) return p.replace("~", os.homedir());
  return p;
}

function getWorkspaceRoot(): string {
  return process.env.DAN_WORKSPACE_ROOT || process.cwd();
}

function isStrictSandbox(): boolean {
  const val = (process.env.DAN_STRICT_SANDBOX || "").trim().toLowerCase();
  return val === "1" || val === "true";
}

function resolvePath(p: string): string {
  try { return fs.realpathSync(p); } catch { return path.resolve(p); }
}

function validateWritePath(filePath: string): string {
  const resolved = resolvePath(expandHome(filePath));
  if (!isStrictSandbox()) return resolved;
  const root = resolvePath(getWorkspaceRoot());
  const normalRoot = root.endsWith(path.sep) ? root : root + path.sep;
  if (resolved !== root && !resolved.startsWith(normalRoot)) {
    throw new Error(
      `Write path '${filePath}' resolves outside workspace root '${root}' and DAN_STRICT_SANDBOX=1 is enabled.`,
    );
  }
  return resolved;
}

async function writeFileAtomically(
  filePath: string,
  content: string | Buffer,
  encoding?: BufferEncoding,
) {
  const dir = path.dirname(filePath);
  const base = path.basename(filePath);
  const tempPath = path.join(
    dir,
    `.${base}.dan-tmp-${process.pid}-${Date.now()}-${Math.random()
      .toString(36)
      .slice(2, 8)}`,
  );

  if (typeof content === "string") {
    await fs.promises.writeFile(tempPath, content, encoding ?? "utf-8");
  } else {
    await fs.promises.writeFile(tempPath, content);
  }

  await fs.promises.rename(tempPath, filePath);
}

ipcMain.handle("fs:readFile", async (_event, filePath: string) => {
  try {
    return await fs.promises.readFile(expandHome(filePath), "utf-8");
  } catch (err: any) {
    if (err?.code === "ENOENT") return null;
    throw err;
  }
});

ipcMain.handle("fs:writeFile", async (_event, filePath: string, content: string) => {
  const resolved = validateWritePath(filePath);
  await writeFileAtomically(resolved, content, "utf-8");
});

ipcMain.handle(
  "fs:writeTempAttachment",
  async (
    _event,
    payload: { name?: string; mimeType?: string; dataUrl: string },
  ) => {
    const parsed = parseDataUrl(payload.dataUrl);
    const mimeType = payload.mimeType || parsed.mimeType;
    const ext = inferAttachmentExtension(payload.name, mimeType);
    const fileName = [
      Date.now(),
      Math.random().toString(36).slice(2, 8),
      sanitizeAttachmentStem(payload.name),
    ].join("-") + ext;
    const dir = getPersistentChatAttachmentsDir();
    await fs.promises.mkdir(dir, { recursive: true });
    const targetPath = path.join(dir, fileName);
    await fs.promises.writeFile(targetPath, parsed.buffer);
    return targetPath;
  },
);

ipcMain.handle("shell:openPath", async (_event, filePath: string) => {
  const error = await shell.openPath(filePath);
  return error === "";
});

ipcMain.handle("shell:openExternal", async (_event, url: string) => {
  try {
    await shell.openExternal(url);
    return true;
  } catch {
    return false;
  }
});

// --- IPC Handlers: file watching ---

const fileWatchers = new Map<string, FSWatcher>();

ipcMain.handle("watch:start", async (_event, filePath: string) => {
  if (fileWatchers.has(filePath)) return;
  try {
    const expandedPath = expandHome(filePath);
    const dirPath = path.dirname(expandedPath);
    const targetName = path.basename(expandedPath);
    const watcher = fs.watch(dirPath, (_eventType, changedName) => {
      const normalizedName = changedName == null ? null : String(changedName);
      if (normalizedName && normalizedName !== targetName) return;
      mainWindow?.webContents.send("watch:changed", filePath);
    });
    watcher.on("error", () => {
      fileWatchers.delete(filePath);
    });
    fileWatchers.set(filePath, watcher);
  } catch {
    // File may not exist or not be watchable
  }
});

ipcMain.handle("watch:stop", async (_event, filePath: string) => {
  const watcher = fileWatchers.get(filePath);
  if (watcher) {
    watcher.close();
    fileWatchers.delete(filePath);
  }
});

// --- IPC Handlers: Backend process management ---

function getBackendStatus(): string {
  if (backendReady) return "running";
  if (backendProcess) return "starting";
  return backendOwnedByUs ? "stopped" : "unknown";
}

function sendBackendStatus() {
  mainWindow?.webContents.send("backend:statusChange", {
    status: getBackendStatus(),
    ownedByUs: backendOwnedByUs,
  });
}

ipcMain.handle("backend:getStatus", async () => {
  return { status: getBackendStatus(), ownedByUs: backendOwnedByUs };
});

ipcMain.handle("backend:restart", async () => {
  if (backendProcess && backendOwnedByUs) {
    backendProcess.kill();
    backendProcess = null;
  }
  backendReady = false;
  try {
    await startBackend();
    sendBackendStatus();
    return { status: "running" };
  } catch (err: any) {
    sendBackendStatus();
    return { status: "error", error: err.message };
  }
});

ipcMain.handle("backend:stop", async () => {
  if (backendProcess && backendOwnedByUs) {
    backendProcess.kill();
    backendProcess = null;
  }
  backendReady = false;
  sendBackendStatus();
  return { status: "stopped" };
});

// --- App lifecycle ---

if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!app.isReady()) return;
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
      return;
    }
    createWindow();
  });

  app.whenReady().then(async () => {
    if (!isDev) {
      const distDir = path.join(__dirname, "../dist");
      console.log("Starting DAN backend server...");
      try {
        await startBackend();
        console.log("Backend server started.");
        await startProductionServer(distDir);
        console.log(`Production UI server started on http://127.0.0.1:${prodServerPort}`);
      } catch (err) {
        console.error("DAN local services failed to start:", err);
        dialog.showErrorBox(
          "DAN Startup Failed",
          `DAN could not start its local services.\n\n${String(err)}`,
        );
        app.quit();
        return;
      }
    }

    createWindow();
    createTray();

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
      }
    });
  });
}

app.on("before-quit", () => {
  for (const [, watcher] of fileWatchers) watcher.close();
  fileWatchers.clear();

  if (prodServer) {
    prodServer.close();
    prodServer = null;
  }
  if (backendProcess && backendOwnedByUs) {
    backendProcess.kill();
    backendProcess = null;
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
