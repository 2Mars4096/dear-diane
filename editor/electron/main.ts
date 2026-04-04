import { app, BrowserWindow, ipcMain, dialog, Tray, Menu, nativeImage, shell } from "electron";
import path from "node:path";
import fs, { type FSWatcher } from "node:fs";
import { spawn, exec, type ChildProcess } from "node:child_process";
import { promisify } from "node:util";
const execAsync = promisify(exec);
import http from "node:http";
import https from "node:https";
import net from "node:net";
import os from "node:os";
import * as pty from "node-pty";
import { autoUpdater } from "electron-updater";
import { commandExists } from "./commandExists";
import { LspManager } from "./lspManager";
import { DebugManager } from "./debugManager";
import { ExtensionHost } from "./extensionHost";
import { runCommand, type CommandResult } from "./runCommand";
import { waitForBackendHealth } from "./backendHealth";
import { buildBackendLaunchEnv } from "./backendLaunch";

let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
const lspManager = new LspManager();
const debugManager = new DebugManager();
const extensionHost = new ExtensionHost();

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

function getDefaultTerminalShell(): string {
  if (process.platform === "win32") {
    return process.env.ComSpec || "powershell.exe";
  }
  const shellPath = (process.env.SHELL || "").trim();
  if (shellPath) return shellPath;
  return process.platform === "darwin" ? "/bin/zsh" : "/bin/bash";
}

function getDefaultTerminalArgs(shellPath: string): string[] {
  if (process.platform === "win32") return [];
  const shellName = shellPath.replace(/\\/g, "/").toLowerCase().split("/").pop() || "";
  return ["zsh", "bash", "sh"].includes(shellName) ? ["-l"] : [];
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
    const healthy = await waitForBackendHealth({
      port: BACKEND_PORT,
      totalTimeoutMs: 5000,
      probeIntervalMs: 250,
      requestTimeoutMs: 1000,
    });
    if (!healthy) {
      throw new Error(
        `Port ${BACKEND_PORT} is already in use, but no healthy DAN backend responded at /api/health.`,
      );
    }
    console.log(`Backend already running on port ${BACKEND_PORT}, reusing.`);
    backendOwnedByUs = false;
    backendReady = true;
    return;
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

ipcMain.handle("dialog:openFile", async (_event, options: { filters?: Array<{ name: string; extensions: string[] }>; multiple?: boolean }) => {
  if (!mainWindow) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: options.multiple ? ["openFile", "multiSelections"] : ["openFile"],
    filters: options.filters,
  });
  if (result.canceled) return null;
  return result.filePaths;
});

ipcMain.handle("dialog:openDirectory", async () => {
  if (!mainWindow) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openDirectory"],
  });
  if (result.canceled) return null;
  return result.filePaths[0];
});

ipcMain.handle("dialog:saveFile", async (_event, options: { defaultPath?: string; filters?: Array<{ name: string; extensions: string[] }> }) => {
  if (!mainWindow) return null;
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: options.defaultPath,
    filters: options.filters,
  });
  if (result.canceled) return null;
  return result.filePath;
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
  await fs.promises.writeFile(resolved, content, "utf-8");
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

ipcMain.handle("fs:readDir", async (_event, dirPath: string) => {
  const entries = await fs.promises.readdir(expandHome(dirPath), { withFileTypes: true });
  return entries.map((e) => ({ name: e.name, isDirectory: e.isDirectory() }));
});

ipcMain.handle("fs:stat", async (_event, filePath: string) => {
  const stat = await fs.promises.stat(expandHome(filePath));
  return { size: stat.size, mtime: stat.mtimeMs, isDirectory: stat.isDirectory() };
});

ipcMain.handle("fs:mkdir", async (_event, dirPath: string) => {
  const resolved = validateWritePath(dirPath);
  await fs.promises.mkdir(resolved, { recursive: true });
});

ipcMain.handle("fs:rename", async (_event, oldPath: string, newPath: string) => {
  const resolvedOld = validateWritePath(oldPath);
  const resolvedNew = validateWritePath(newPath);
  await fs.promises.rename(resolvedOld, resolvedNew);
});

ipcMain.handle("fs:delete", async (_event, filePath: string) => {
  const resolved = validateWritePath(filePath);
  const stat = await fs.promises.stat(resolved);
  if (stat.isDirectory()) {
    await fs.promises.rm(resolved, { recursive: true, force: true });
  } else {
    await fs.promises.unlink(resolved);
  }
});

ipcMain.handle("fs:exists", async (_event, filePath: string) => {
  try {
    await fs.promises.access(expandHome(filePath));
    return true;
  } catch {
    return false;
  }
});

ipcMain.handle("fs:readGitignore", async (_event, rootPath: string) => {
  const gitignorePath = path.join(expandHome(rootPath), ".gitignore");
  try {
    return await fs.promises.readFile(gitignorePath, "utf-8");
  } catch {
    return null;
  }
});

ipcMain.handle("shell:openPath", async (_event, filePath: string) => {
  const error = await shell.openPath(filePath);
  return error === "";
});

// --- IPC Handlers: file watching ---

ipcMain.handle("watch:start", async (_event, filePath: string) => {
  if (fileWatchers.has(filePath)) return;
  try {
    const watcher = fs.watch(filePath, (eventType) => {
      if (eventType === "change") {
        mainWindow?.webContents.send("watch:changed", filePath);
      }
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

// --- IPC Handlers: terminal (node-pty) ---

const fileWatchers = new Map<string, FSWatcher>();

const terminals = new Map<string, pty.IPty>();
let terminalCounter = 0;

ipcMain.handle("terminal:create", async (_event, options: { cwd?: string; shell?: string; args?: string[]; env?: Record<string, string> }) => {
  const id = `term-${++terminalCounter}`;
  const shellPath = options.shell || getDefaultTerminalShell();
  const shellArgs = options.args ?? getDefaultTerminalArgs(shellPath);
  const cwd = options.cwd || os.homedir() || "/";
  const env = { ...process.env, ...options.env, TERM: "xterm-256color", COLORTERM: "truecolor" };

  try {
    const term = pty.spawn(shellPath, shellArgs, {
      name: "xterm-256color",
      cols: 80,
      rows: 24,
      cwd,
      env: env as Record<string, string>,
    });

    terminals.set(id, term);

    term.onData((data: string) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("terminal:data", id, data);
      }
    });

    term.onExit(({ exitCode }) => {
      terminals.delete(id);
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("terminal:exit", id, exitCode);
      }
    });

    return id;
  } catch (err: any) {
    console.error("Failed to create PTY:", err.message);
    return null;
  }
});

ipcMain.handle("terminal:write", async (_event, id: string, data: string) => {
  const term = terminals.get(id);
  if (term) term.write(data);
});

ipcMain.handle("terminal:resize", async (_event, id: string, cols: number, rows: number) => {
  const term = terminals.get(id);
  if (term) {
    try { term.resize(cols, rows); } catch {}
  }
});

ipcMain.handle("terminal:kill", async (_event, id: string) => {
  const term = terminals.get(id);
  if (term) {
    try { term.kill(); } catch {}
    terminals.delete(id);
  }
});

// --- IPC Handlers: search (ripgrep) ---

ipcMain.handle("search:ripgrep", async (_event, opts: { query: string; cwd: string; glob?: string; caseSensitive?: boolean; maxResults?: number }) => {
  const args = ["--json", "--max-count", String(opts.maxResults ?? 200)];
  if (!opts.caseSensitive) args.push("-i");
  if (opts.glob) args.push("--glob", opts.glob);
  args.push("--", opts.query, opts.cwd);

  return new Promise<string>((resolve) => {
    const proc = spawn("rg", args, { cwd: opts.cwd, stdio: ["ignore", "pipe", "pipe"] });
    let output = "";
    proc.stdout?.on("data", (d: Buffer) => { output += d.toString(); });
    proc.stderr?.on("data", (d: Buffer) => { output += d.toString(); });
    const timer = setTimeout(() => { proc.kill(); resolve(output); }, 10000);
    proc.on("close", () => { clearTimeout(timer); resolve(output); });
  });
});

ipcMain.handle("search:replaceInFile", async (_event, filePath: string, replacements: Array<{ lineNumber: number; matchStart: number; matchEnd: number; replacement: string }>) => {
  try {
    const resolved = validateWritePath(filePath);
    let content = await fs.promises.readFile(resolved, "utf-8");
    const lines = content.split("\n");

    const sorted = [...replacements].sort((a, b) =>
      b.lineNumber !== a.lineNumber ? b.lineNumber - a.lineNumber : b.matchStart - a.matchStart
    );

    for (const r of sorted) {
      const lineIdx = r.lineNumber - 1;
      if (lineIdx >= 0 && lineIdx < lines.length) {
        const line = lines[lineIdx];
        lines[lineIdx] = line.slice(0, r.matchStart) + r.replacement + line.slice(r.matchEnd);
      }
    }

    content = lines.join("\n");
    await fs.promises.writeFile(resolved, content, "utf-8");
    return { success: true };
  } catch (err) {
    return { success: false, error: String(err) };
  }
});

// --- IPC Handlers: git ---

function runGit(args: string[], cwd: string): Promise<CommandResult> {
  return runCommand("git", args, { cwd, timeoutMs: 15000 });
}

ipcMain.handle("git:status", async (_event, cwd: string) => runGit(["status", "--porcelain=v1", "-uall"], cwd));
ipcMain.handle("git:diff", async (_event, cwd: string, filePath?: string) => {
  const args = ["diff"];
  if (filePath) args.push("--", filePath);
  return runGit(args, cwd);
});
ipcMain.handle("git:diffStaged", async (_event, cwd: string, filePath?: string) => {
  const args = ["diff", "--cached"];
  if (filePath) args.push("--", filePath);
  return runGit(args, cwd);
});
ipcMain.handle("git:log", async (_event, cwd: string, maxCount?: number) =>
  runGit(["log", `--max-count=${maxCount ?? 50}`, "--pretty=format:%H|%an|%ae|%at|%s"], cwd));
ipcMain.handle("git:stage", async (_event, cwd: string, filePath: string) => runGit(["add", "--", filePath], cwd));
ipcMain.handle("git:unstage", async (_event, cwd: string, filePath: string) => runGit(["reset", "HEAD", "--", filePath], cwd));
ipcMain.handle("git:commit", async (_event, cwd: string, message: string) => runGit(["commit", "-m", message], cwd));
ipcMain.handle("git:branch", async (_event, cwd: string) => runGit(["branch", "--show-current"], cwd));
ipcMain.handle("git:fileShow", async (_event, cwd: string, ref: string, filePath: string) =>
  runGit(["show", `${ref}:${filePath}`], cwd));

ipcMain.handle("git:push", async (_event, cwd: string, remote?: string, branch?: string) => {
  const args = ["push"];
  if (remote) args.push(remote);
  if (branch) args.push(branch);
  return runGit(args, cwd);
});

ipcMain.handle("git:pull", async (_event, cwd: string, remote?: string, branch?: string) => {
  const args = ["pull"];
  if (remote) args.push(remote);
  if (branch) args.push(branch);
  return runGit(args, cwd);
});

ipcMain.handle("git:branchList", async (_event, cwd: string) =>
  runGit(["branch", "-a", "--format=%(refname:short)|%(HEAD)|%(upstream:short)"], cwd));

ipcMain.handle("git:checkout", async (_event, cwd: string, branchName: string) =>
  runGit(["checkout", branchName], cwd));

ipcMain.handle("git:createBranch", async (_event, cwd: string, branchName: string) =>
  runGit(["checkout", "-b", branchName], cwd));

ipcMain.handle("git:remoteInfo", async (_event, cwd: string) =>
  runGit(["remote", "-v"], cwd));

ipcMain.handle("git:aheadBehind", async (_event, cwd: string) =>
  runGit(["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], cwd));

ipcMain.handle("git:blame", async (_event, cwd: string, filePath: string) =>
  runGit(["blame", "--porcelain", filePath], cwd));

ipcMain.handle("git:stash", async (_event, cwd: string, message?: string) => {
  const args = ["stash", "push"];
  if (message) args.push("-m", message);
  return runGit(args, cwd);
});

ipcMain.handle("git:stashList", async (_event, cwd: string) =>
  runGit(["stash", "list", "--format=%gd|%s|%ai"], cwd));

ipcMain.handle("git:stashPop", async (_event, cwd: string, index: number) =>
  runGit(["stash", "pop", `stash@{${index}}`], cwd));

ipcMain.handle("git:stashApply", async (_event, cwd: string, index: number) =>
  runGit(["stash", "apply", `stash@{${index}}`], cwd));

ipcMain.handle("git:stashDrop", async (_event, cwd: string, index: number) =>
  runGit(["stash", "drop", `stash@{${index}}`], cwd));

ipcMain.handle("git:stashShow", async (_event, cwd: string, index: number) =>
  runGit(["stash", "show", "-p", `stash@{${index}}`], cwd));

ipcMain.handle("git:cherryPick", async (_event, cwd: string, hash: string) =>
  runGit(["cherry-pick", hash], cwd));

ipcMain.handle("git:logGraph", async (_event, cwd: string, maxCount?: number) =>
  runGit(["log", `--max-count=${maxCount ?? 80}`, "--format=%H|%h|%P|%s|%an|%ae|%ai|%D", "--all"], cwd));

// --- IPC Handlers: git rebase ---

ipcMain.handle("git:rebaseCommitList", async (_event, cwd: string, count: number) =>
  runGit(["log", "--oneline", `--format=%H|%h|%s|%an`, `--max-count=${count}`], cwd));

ipcMain.handle("git:rebaseStart", async (_event, cwd: string, entries: Array<{ hash: string; action: string; message: string }>) => {
  const tmpDir = os.tmpdir();
  const todoPath = path.join(tmpDir, `dan-rebase-todo-${Date.now()}`);
  const todoContent = entries.map((e) => `${e.action} ${e.hash} ${e.message}`).join("\n") + "\n";
  await fs.promises.writeFile(todoPath, todoContent, "utf-8");

  const baseHash = entries[0]?.hash;
  if (!baseHash) return { stdout: "", stderr: "No commits selected", code: 1 };

  const baseRes = await runGit(["rev-parse", `${baseHash}~1`], cwd);
  const base = baseRes.code === 0 ? baseRes.stdout.trim() : `${baseHash}~1`;

  const editorScript = path.join(tmpDir, `dan-rebase-editor-${Date.now()}.sh`);
  await fs.promises.writeFile(editorScript, `#!/bin/sh\ncp "${todoPath}" "$1"\n`, { mode: 0o755 });

  const result = await runCommand("git", ["rebase", "-i", base], {
    cwd,
    env: { ...process.env, GIT_SEQUENCE_EDITOR: editorScript },
    timeoutMs: 60000,
  });

  try { await fs.promises.unlink(todoPath); } catch {}
  try { await fs.promises.unlink(editorScript); } catch {}
  return result;
});

ipcMain.handle("git:rebaseAbort", async (_event, cwd: string) =>
  runGit(["rebase", "--abort"], cwd));

ipcMain.handle("git:rebaseContinue", async (_event, cwd: string) =>
  runGit(["rebase", "--continue"], cwd));

ipcMain.handle("git:rebaseStatus", async (_event, cwd: string) => {
  const rebaseMerge = path.join(cwd, ".git", "rebase-merge");
  const rebaseApply = path.join(cwd, ".git", "rebase-apply");
  let inProgress = false;
  try { await fs.promises.access(rebaseMerge); inProgress = true; } catch {}
  if (!inProgress) try { await fs.promises.access(rebaseApply); inProgress = true; } catch {}
  return { stdout: String(inProgress), stderr: "", code: 0 };
});

// --- IPC Handlers: git conflicts ---

ipcMain.handle("git:conflictFiles", async (_event, cwd: string) =>
  runGit(["diff", "--name-only", "--diff-filter=U"], cwd));

ipcMain.handle("git:conflictContent", async (_event, cwd: string, filePath: string) => {
  try {
    const fullPath = path.join(cwd, filePath);
    const content = await fs.promises.readFile(fullPath, "utf-8");
    return { stdout: content, stderr: "", code: 0 };
  } catch (err: any) {
    return { stdout: "", stderr: err.message, code: 1 };
  }
});

ipcMain.handle("git:showBase", async (_event, cwd: string, filePath: string) =>
  runGit(["show", `:1:${filePath}`], cwd));

ipcMain.handle("git:showOurs", async (_event, cwd: string, filePath: string) =>
  runGit(["show", `:2:${filePath}`], cwd));

ipcMain.handle("git:showTheirs", async (_event, cwd: string, filePath: string) =>
  runGit(["show", `:3:${filePath}`], cwd));

ipcMain.handle("git:markResolved", async (_event, cwd: string, filePath: string, content: string) => {
  try {
    const fullPath = path.join(cwd, filePath);
    await fs.promises.writeFile(fullPath, content, "utf-8");
    return runGit(["add", "--", filePath], cwd);
  } catch (err: any) {
    return { stdout: "", stderr: err.message, code: 1 };
  }
});

// --- IPC Handlers: debug attach ---

ipcMain.handle("debug:listProcesses", async () => {
  const processes: Array<{ pid: number; name: string; port: number | null; type: string }> = [];

  try {
    const { stdout: nodeOutput } = await execAsync("lsof -i :9229 -sTCP:LISTEN -P -n 2>/dev/null || true", { encoding: "utf-8", timeout: 5000 });
    for (const line of nodeOutput.split("\n").slice(1)) {
      const parts = line.split(/\s+/).filter(Boolean);
      if (parts.length >= 2) {
        const pid = parseInt(parts[1], 10);
        if (!isNaN(pid) && !processes.find((p) => p.pid === pid)) {
          processes.push({ pid, name: parts[0], port: 9229, type: "node" });
        }
      }
    }
  } catch {}

  try {
    const { stdout: pythonOutput } = await execAsync("ps -eo pid,command 2>/dev/null | grep -E 'python|debugpy' | grep -v grep || true", { encoding: "utf-8", timeout: 5000 });
    for (const line of pythonOutput.split("\n")) {
      const match = line.trim().match(/^(\d+)\s+(.+)$/);
      if (match) {
        const pid = parseInt(match[1], 10);
        if (!isNaN(pid) && !processes.find((p) => p.pid === pid)) {
          const portMatch = match[2].match(/(?:--listen|--port)\s+(\d+)/);
          processes.push({
            pid,
            name: match[2].slice(0, 80),
            port: portMatch ? parseInt(portMatch[1], 10) : null,
            type: "python",
          });
        }
      }
    }
  } catch {}

  return processes;
});

ipcMain.handle("debug:attach", async (_, config: any) => {
  try {
    await debugManager.startSession({ ...config, request: "attach" });
    return { success: true };
  } catch (err: any) {
    return { success: false, error: err.message };
  }
});

// --- IPC Handlers: LSP ---

ipcMain.handle("lsp:start", async (_, rootPath: string) => {
  await lspManager.detectAndStart(rootPath);
});

ipcMain.handle("lsp:didOpen", async (_, { filePath, languageId, version, text }: { filePath: string; languageId: string; version: number; text: string }) => {
  const client = lspManager.getClientForFile(filePath);
  if (client) client.didOpen(`file://${filePath}`, languageId, version, text);
});

ipcMain.handle("lsp:didChange", async (_, { filePath, version, changes }: { filePath: string; version: number; changes: any[] }) => {
  const client = lspManager.getClientForFile(filePath);
  if (client) client.didChange(`file://${filePath}`, version, changes);
});

ipcMain.handle("lsp:didSave", async (_, { filePath, text }: { filePath: string; text?: string }) => {
  const client = lspManager.getClientForFile(filePath);
  if (client) client.didSave(`file://${filePath}`, text);
});

ipcMain.handle("lsp:didClose", async (_, { filePath }: { filePath: string }) => {
  const client = lspManager.getClientForFile(filePath);
  if (client) client.didClose(`file://${filePath}`);
});

ipcMain.handle("lsp:completion", async (_, { filePath, line, character }: { filePath: string; line: number; character: number }) => {
  const client = lspManager.getClientForFile(filePath);
  if (!client) return null;
  try { return await client.completion(`file://${filePath}`, line, character); } catch { return null; }
});

ipcMain.handle("lsp:hover", async (_, { filePath, line, character }: { filePath: string; line: number; character: number }) => {
  const client = lspManager.getClientForFile(filePath);
  if (!client) return null;
  try { return await client.hover(`file://${filePath}`, line, character); } catch { return null; }
});

ipcMain.handle("lsp:definition", async (_, { filePath, line, character }: { filePath: string; line: number; character: number }) => {
  const client = lspManager.getClientForFile(filePath);
  if (!client) return null;
  try { return await client.definition(`file://${filePath}`, line, character); } catch { return null; }
});

ipcMain.handle("lsp:references", async (_, { filePath, line, character }: { filePath: string; line: number; character: number }) => {
  const client = lspManager.getClientForFile(filePath);
  if (!client) return null;
  try { return await client.references(`file://${filePath}`, line, character); } catch { return null; }
});

ipcMain.handle("lsp:documentSymbol", async (_, { filePath }: { filePath: string }) => {
  const client = lspManager.getClientForFile(filePath);
  if (!client) return null;
  try { return await client.documentSymbol(`file://${filePath}`); } catch { return null; }
});

ipcMain.handle("lsp:workspaceSymbol", async (_, { query }: { query: string }) => {
  const trimmed = query.trim();
  if (!trimmed) return [];
  const clients = lspManager.getAllClients();
  if (clients.length === 0) return [];

  const settled = await Promise.allSettled(
    clients.map((client) => client.workspaceSymbol(trimmed)),
  );
  const results: any[] = [];
  const seen = new Set<string>();

  for (const entry of settled) {
    if (entry.status !== "fulfilled" || !Array.isArray(entry.value)) continue;
    for (const symbol of entry.value) {
      const key = [
        symbol?.name ?? "",
        symbol?.kind ?? "",
        symbol?.containerName ?? "",
        symbol?.location?.uri ?? "",
        symbol?.location?.range?.start?.line ?? "",
        symbol?.location?.range?.start?.character ?? "",
      ].join("|");
      if (!seen.has(key)) {
        seen.add(key);
        results.push(symbol);
      }
    }
  }

  return results;
});

ipcMain.handle("lsp:formatting", async (_, { filePath, tabSize, insertSpaces }: { filePath: string; tabSize: number; insertSpaces: boolean }) => {
  const client = lspManager.getClientForFile(filePath);
  if (!client) return null;
  try { return await client.formatting(`file://${filePath}`, tabSize, insertSpaces); } catch { return null; }
});

ipcMain.handle("lsp:codeAction", async (_, { filePath, range, diagnostics }: { filePath: string; range: any; diagnostics: any[] }) => {
  const client = lspManager.getClientForFile(filePath);
  if (!client) return null;
  try { return await client.codeAction(`file://${filePath}`, range, diagnostics); } catch { return null; }
});

ipcMain.handle("lsp:rename", async (_, { filePath, line, character, newName }: { filePath: string; line: number; character: number; newName: string }) => {
  const client = lspManager.getClientForFile(filePath);
  if (!client) return null;
  try { return await client.rename(`file://${filePath}`, line, character, newName); } catch { return null; }
});

ipcMain.handle("lsp:signatureHelp", async (_, { filePath, line, character }: { filePath: string; line: number; character: number }) => {
  const client = lspManager.getClientForFile(filePath);
  if (!client) return null;
  try { return await client.signatureHelp(`file://${filePath}`, line, character); } catch { return null; }
});

ipcMain.handle("lsp:prepareCallHierarchy", async (_, { filePath, line, character }: { filePath: string; line: number; character: number }) => {
  const client = lspManager.getClientForFile(filePath);
  if (!client) return null;
  try { return await client.prepareCallHierarchy(`file://${filePath}`, line, character); } catch { return null; }
});

ipcMain.handle("lsp:incomingCalls", async (_, { item }: { item: any }) => {
  const uri: string = item?.uri ?? "";
  const filePath = uri.replace(/^file:\/\//, "");
  const client = lspManager.getClientForFile(filePath);
  if (!client) return null;
  try { return await client.incomingCalls(item); } catch { return null; }
});

ipcMain.handle("lsp:outgoingCalls", async (_, { item }: { item: any }) => {
  const uri: string = item?.uri ?? "";
  const filePath = uri.replace(/^file:\/\//, "");
  const client = lspManager.getClientForFile(filePath);
  if (!client) return null;
  try { return await client.outgoingCalls(item); } catch { return null; }
});

// --- IPC Handlers: shell command runner ---

ipcMain.handle("shell:run", async (_event, opts: { command: string; args: string[]; cwd: string }) => {
  return new Promise((resolve) => {
    const proc = spawn(opts.command, opts.args, {
      cwd: opts.cwd || process.env.HOME || "/",
      shell: true,
      env: { ...process.env, FORCE_COLOR: "0" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    proc.stdout?.on("data", (d: Buffer) => { stdout += d.toString(); });
    proc.stderr?.on("data", (d: Buffer) => { stderr += d.toString(); });
    const timer = setTimeout(() => { proc.kill(); resolve({ stdout, stderr, code: -1 }); }, 120_000);
    proc.on("close", (code) => {
      clearTimeout(timer);
      resolve({ stdout, stderr, code });
    });
    proc.on("error", (err) => {
      clearTimeout(timer);
      resolve({ stdout, stderr: stderr + "\n" + String(err), code: -1 });
    });
  });
});

// --- IPC Handlers: debugger (DAP) ---

ipcMain.handle("debug:start", async (_, config: any) => {
  try {
    await debugManager.startSession(config);
    return { success: true };
  } catch (err: any) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle("debug:stop", async () => {
  await debugManager.stopSession();
});

ipcMain.handle("debug:restart", async () => {
  try {
    await debugManager.restart();
    return { success: true };
  } catch (err: any) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle("debug:setBreakpoints", async (_, filePath: string, breakpoints: any[]) => {
  return debugManager.setBreakpoints(filePath, breakpoints);
});

ipcMain.handle("debug:continue", async (_, threadId: number) => {
  return debugManager.continue_(threadId);
});

ipcMain.handle("debug:next", async (_, threadId: number) => {
  return debugManager.next(threadId);
});

ipcMain.handle("debug:stepIn", async (_, threadId: number) => {
  return debugManager.stepIn(threadId);
});

ipcMain.handle("debug:stepOut", async (_, threadId: number) => {
  return debugManager.stepOut(threadId);
});

ipcMain.handle("debug:pause", async (_, threadId: number) => {
  return debugManager.pause(threadId);
});

ipcMain.handle("debug:threads", async () => {
  return debugManager.threads();
});

ipcMain.handle("debug:stackTrace", async (_, threadId: number) => {
  return debugManager.stackTrace(threadId);
});

ipcMain.handle("debug:scopes", async (_, frameId: number) => {
  return debugManager.scopes(frameId);
});

ipcMain.handle("debug:variables", async (_, variablesReference: number) => {
  return debugManager.variables(variablesReference);
});

ipcMain.handle("debug:evaluate", async (_, expression: string, frameId?: number) => {
  return debugManager.evaluate(expression, frameId);
});

// --- IPC Handlers: extension management ---

const EXTENSIONS_DIR = path.join(os.homedir(), ".dan", "extensions");
const MANIFEST_PATH = path.join(EXTENSIONS_DIR, "manifest.json");

async function ensureExtensionsDir() {
  await fs.promises.mkdir(EXTENSIONS_DIR, { recursive: true });
  try {
    await fs.promises.access(MANIFEST_PATH);
  } catch {
    await fs.promises.writeFile(MANIFEST_PATH, JSON.stringify({ installed: [] }, null, 2));
  }
}

async function readManifest(): Promise<{ installed: any[] }> {
  await ensureExtensionsDir();
  try {
    return JSON.parse(await fs.promises.readFile(MANIFEST_PATH, "utf-8"));
  } catch {
    return { installed: [] };
  }
}

async function writeManifest(manifest: { installed: any[] }) {
  await ensureExtensionsDir();
  await fs.promises.writeFile(MANIFEST_PATH, JSON.stringify(manifest, null, 2));
}

function downloadFile(url: string, dest: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest);
    const request = (reqUrl: string) => {
      https.get(reqUrl, (res) => {
        if (res.statusCode && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          request(res.headers.location);
          return;
        }
        if (res.statusCode && res.statusCode >= 400) {
          file.close();
          try { fs.unlinkSync(dest); } catch {}
          reject(new Error(`Download failed: HTTP ${res.statusCode}`));
          return;
        }
        res.pipe(file);
        file.on("finish", () => { file.close(() => resolve()); });
      }).on("error", (err) => {
        file.close();
        try { fs.unlinkSync(dest); } catch {}
        reject(err);
      });
    };
    request(url);
  });
}

ipcMain.handle("extension:install", async (_event, itemId: string, downloadUrl: string) => {
  await ensureExtensionsDir();
  const extDir = path.join(EXTENSIONS_DIR, itemId);
  const tmpVsix = path.join(EXTENSIONS_DIR, `${itemId}.vsix`);

  try {
    await downloadFile(downloadUrl, tmpVsix);
    await fs.promises.mkdir(extDir, { recursive: true });
    await execAsync(`unzip -o "${tmpVsix}" -d "${extDir}"`);
    try { await fs.promises.unlink(tmpVsix); } catch {}

    const manifest = await readManifest();
    const existing = manifest.installed.findIndex((e: any) => e.id === itemId);
    const entry = {
      id: itemId,
      extensionPath: extDir,
      installDate: Date.now(),
      enabled: true,
    };
    if (existing >= 0) {
      manifest.installed[existing] = { ...manifest.installed[existing], ...entry };
    } else {
      manifest.installed.push(entry);
    }
    await writeManifest(manifest);

    return { extensionPath: extDir };
  } catch (err) {
    try { await fs.promises.unlink(tmpVsix); } catch {}
    throw err;
  }
});

ipcMain.handle("extension:uninstall", async (_event, itemId: string) => {
  const extDir = path.join(EXTENSIONS_DIR, itemId);
  try { await fs.promises.rm(extDir, { recursive: true, force: true }); } catch {}
  const manifest = await readManifest();
  manifest.installed = manifest.installed.filter((e: any) => e.id !== itemId);
  await writeManifest(manifest);
});

ipcMain.handle("extension:readFile", async (_event, filePath: string) => {
  try {
    return await fs.promises.readFile(filePath, "utf-8");
  } catch {
    return null;
  }
});

ipcMain.handle("extension:listInstalled", async () => {
  const manifest = await readManifest();
  return manifest.installed;
});

ipcMain.handle("extension:getManifest", async (_event, itemId: string) => {
  const pkgPath = path.join(EXTENSIONS_DIR, itemId, "extension", "package.json");
  try {
    return JSON.parse(await fs.promises.readFile(pkgPath, "utf-8"));
  } catch {
    const altPath = path.join(EXTENSIONS_DIR, itemId, "package.json");
    try {
      return JSON.parse(await fs.promises.readFile(altPath, "utf-8"));
    } catch {
      return null;
    }
  }
});

ipcMain.handle("extension:importVsix", async (_event, filePath: string) => {
  await ensureExtensionsDir();
  const tmpDir = path.join(EXTENSIONS_DIR, "_tmp_import_" + Date.now());
  await fs.promises.mkdir(tmpDir, { recursive: true });

  try {
    await execAsync(`unzip -o "${filePath}" -d "${tmpDir}"`);

    let pkgJsonPath = path.join(tmpDir, "extension", "package.json");
    try {
      await fs.promises.access(pkgJsonPath);
    } catch {
      pkgJsonPath = path.join(tmpDir, "package.json");
      try {
        await fs.promises.access(pkgJsonPath);
      } catch {
        throw new Error("No package.json found in VSIX");
      }
    }

    const pkg = JSON.parse(await fs.promises.readFile(pkgJsonPath, "utf-8"));
    const publisher = pkg.publisher ?? "local";
    const name = pkg.name ?? "unknown";
    const itemId = `${publisher}.${name}`;
    const extDir = path.join(EXTENSIONS_DIR, itemId);

    try { await fs.promises.rm(extDir, { recursive: true, force: true }); } catch {}
    await fs.promises.rename(tmpDir, extDir);

    const manifest = await readManifest();
    const entry = {
      id: itemId,
      name: pkg.name ?? name,
      displayName: pkg.displayName ?? name,
      description: pkg.description ?? "",
      publisher,
      version: pkg.version ?? "0.0.0",
      extensionPath: extDir,
      installDate: Date.now(),
      enabled: true,
      categories: pkg.categories ?? [],
      tags: pkg.keywords ?? [],
      registry: "local",
    };
    const existing = manifest.installed.findIndex((e: any) => e.id === itemId);
    if (existing >= 0) {
      manifest.installed[existing] = entry;
    } else {
      manifest.installed.push(entry);
    }
    await writeManifest(manifest);

    return entry;
  } catch (err) {
    try { await fs.promises.rm(tmpDir, { recursive: true, force: true }); } catch {}
    throw err;
  }
});

// --- IPC Handlers: Extension Host ---

ipcMain.handle("extensionHost:start", async () => {
  const manifest = await readManifest();
  const enabled = manifest.installed.filter((e: any) => e.enabled);
  const paths: string[] = [];
  for (const e of enabled) {
    const extSubdir = path.join(e.extensionPath, "extension");
    let p = e.extensionPath;
    try { await fs.promises.access(path.join(extSubdir, "package.json")); p = extSubdir; } catch {}
    try { await fs.promises.access(path.join(p, "package.json")); paths.push(p); } catch {}
  }
  extensionHost.start(paths);
  return { status: "started", extensionCount: paths.length };
});

ipcMain.handle("extensionHost:stop", async () => {
  extensionHost.stop();
  return { status: "stopped" };
});

ipcMain.handle("extensionHost:status", async () => {
  return { running: extensionHost.running };
});

ipcMain.handle("extensionHost:executeCommand", async (_event, commandId: string, args?: any[]) => {
  if (!extensionHost.running) return { error: "Extension host not running" };
  try {
    const result = await extensionHost.request("executeCommand", { id: commandId, args: args ?? [] });
    return { result };
  } catch (err: any) {
    return { error: err.message };
  }
});

ipcMain.handle("extensionHost:getCommands", async () => {
  if (!extensionHost.running) return [];
  try {
    return await extensionHost.request("getCommands");
  } catch {
    return [];
  }
});

ipcMain.handle("extensionHost:getLanguageProviders", async () => {
  if (!extensionHost.running) return [];
  try {
    return await extensionHost.request("getLanguageProviders");
  } catch {
    return [];
  }
});

ipcMain.handle("extensionHost:invokeLanguageProvider", async (_event, payload: any) => {
  if (!extensionHost.running) return { error: "Extension host not running" };
  try {
    const result = await extensionHost.request("invokeLanguageProvider", payload);
    return { result };
  } catch (err: any) {
    return { error: err.message };
  }
});

extensionHost.on("extension:activated", (data) => {
  mainWindow?.webContents.send("extensionHost:event", { event: "activated", data });
});

extensionHost.on("extension:error", (data) => {
  mainWindow?.webContents.send("extensionHost:event", { event: "error", data });
});

extensionHost.on("window:showMessage", (data) => {
  mainWindow?.webContents.send("extensionHost:event", { event: "showMessage", data });
});

extensionHost.on("languages:registerProvider", (data) => {
  mainWindow?.webContents.send("extensionHost:event", { event: "languages:registerProvider", data });
});

extensionHost.on("languages:disposeProvider", (data) => {
  mainWindow?.webContents.send("extensionHost:event", { event: "languages:disposeProvider", data });
});

extensionHost.on("statusBar:show", (data) => {
  mainWindow?.webContents.send("extensionHost:event", { event: "statusBarShow", data });
});

extensionHost.on("output:append", (data) => {
  mainWindow?.webContents.send("extensionHost:event", { event: "outputAppend", data });
});

extensionHost.on("command:execute", (data) => {
  mainWindow?.webContents.send("extensionHost:event", { event: "commandExecute", data });
});

extensionHost.on("exit", (code) => {
  mainWindow?.webContents.send("extensionHost:event", { event: "exit", data: { code } });
});

extensionHost.on("error", (err) => {
  mainWindow?.webContents.send("extensionHost:event", { event: "hostError", data: { message: String(err) } });
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

// --- IPC Handlers: DAN Skills ---

const SKILLS_GLOBAL_DIR = path.join(os.homedir(), ".dan", "skills");
const SKILLS_MANIFEST = path.join(SKILLS_GLOBAL_DIR, "manifest.json");

async function ensureSkillsDir() {
  await fs.promises.mkdir(SKILLS_GLOBAL_DIR, { recursive: true });
  try {
    await fs.promises.access(SKILLS_MANIFEST);
  } catch {
    await fs.promises.writeFile(SKILLS_MANIFEST, JSON.stringify({ disabled: [] }, null, 2));
  }
}

async function readSkillsManifest(): Promise<{ disabled: string[] }> {
  await ensureSkillsDir();
  try {
    return JSON.parse(await fs.promises.readFile(SKILLS_MANIFEST, "utf-8"));
  } catch {
    return { disabled: [] };
  }
}

async function writeSkillsManifest(manifest: { disabled: string[] }) {
  await ensureSkillsDir();
  await fs.promises.writeFile(SKILLS_MANIFEST, JSON.stringify(manifest, null, 2));
}

function parseSkillMeta(content: string): { name?: string; description?: string; author?: string; version?: string; triggers?: string[] } {
  const meta: Record<string, any> = {};
  const fmMatch = content.match(/^---\s*\n([\s\S]*?)\n---/);
  if (fmMatch) {
    const fm = fmMatch[1];
    for (const key of ["name", "description", "author", "version"]) {
      const m = fm.match(new RegExp(`${key}:\\s*(.+)`));
      if (m) meta[key] = m[1].trim().replace(/^["']|["']$/g, "");
    }
    const triggerMatch = fm.match(/triggers?:\s*\[([^\]]*)\]/);
    if (triggerMatch) meta.triggers = triggerMatch[1].split(",").map((t: string) => t.trim().replace(/^["']|["']$/g, ""));
  }
  if (!meta.name) {
    const headingMatch = content.match(/^#\s+(.+)/m);
    if (headingMatch) meta.name = headingMatch[1].trim();
  }
  return meta;
}

async function scanSkillsDir(dir: string): Promise<Array<{ id: string; name: string; description: string; author?: string; version?: string; triggers?: string[]; path: string; modifiedAt: number }>> {
  const results: Array<any> = [];
  try { await fs.promises.access(dir); } catch { return results; }
  try {
    const entries = await fs.promises.readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory() || entry.name.startsWith(".")) continue;
      const skillPath = path.join(dir, entry.name, "SKILL.md");
      try { await fs.promises.access(skillPath); } catch { continue; }
      try {
        const content = await fs.promises.readFile(skillPath, "utf-8");
        const stat = await fs.promises.stat(skillPath);
        const meta = parseSkillMeta(content);
        results.push({
          id: entry.name,
          name: meta.name ?? entry.name,
          description: meta.description ?? "",
          author: meta.author,
          version: meta.version,
          triggers: meta.triggers,
          path: skillPath,
          modifiedAt: stat.mtimeMs,
        });
      } catch { /* skip unreadable */ }
    }
  } catch { /* dir not readable */ }
  return results;
}

ipcMain.handle("skills:scan", async () => {
  await ensureSkillsDir();
  const manifest = await readSkillsManifest();
  const globalSkills = await scanSkillsDir(SKILLS_GLOBAL_DIR);

  let workspaceSkills: typeof globalSkills = [];
  if (mainWindow) {
    const url = mainWindow.webContents.getURL();
    const match = url.match(/cwd=([^&]+)/);
    if (match) {
      const localDir = path.join(decodeURIComponent(match[1]), ".dan", "skills");
      workspaceSkills = (await scanSkillsDir(localDir)).map((s) => ({ ...s, id: `ws:${s.id}` }));
    }
  }

  const all = [...globalSkills, ...workspaceSkills];
  return all.map((s) => ({ ...s, enabled: !manifest.disabled.includes(s.id) }));
});

ipcMain.handle("skills:readSkill", async (_event, skillPath: string) => {
  try {
    return await fs.promises.readFile(skillPath, "utf-8");
  } catch {
    return null;
  }
});

ipcMain.handle("skills:importFromUrl", async (_event, url: string) => {
  await ensureSkillsDir();
  const urlObj = new URL(url);
  const segments = urlObj.pathname.split("/").filter(Boolean);
  const name = segments[segments.length - 1]?.replace(/\.md$/i, "") ?? `skill-${Date.now()}`;
  const skillDir = path.join(SKILLS_GLOBAL_DIR, name);
  await fs.promises.mkdir(skillDir, { recursive: true });
  const dest = path.join(skillDir, "SKILL.md");
  await downloadFile(url, dest);
  return { id: name, path: dest };
});

ipcMain.handle("skills:remove", async (_event, skillId: string) => {
  const skillDir = path.join(SKILLS_GLOBAL_DIR, skillId);
  try { await fs.promises.rm(skillDir, { recursive: true, force: true }); } catch {}
  const manifest = await readSkillsManifest();
  manifest.disabled = manifest.disabled.filter((d) => d !== skillId);
  await writeSkillsManifest(manifest);
});

ipcMain.handle("skills:toggleEnabled", async (_event, skillId: string) => {
  const manifest = await readSkillsManifest();
  const idx = manifest.disabled.indexOf(skillId);
  if (idx >= 0) {
    manifest.disabled.splice(idx, 1);
  } else {
    manifest.disabled.push(skillId);
  }
  await writeSkillsManifest(manifest);
  return !manifest.disabled.includes(skillId);
});

ipcMain.handle("skills:createTemplate", async (_event, name: string) => {
  await ensureSkillsDir();
  const skillDir = path.join(SKILLS_GLOBAL_DIR, name);
  await fs.promises.mkdir(skillDir, { recursive: true });
  const skillPath = path.join(skillDir, "SKILL.md");
  let skillExists = false;
  try { await fs.promises.access(skillPath); skillExists = true; } catch {}
  if (!skillExists) {
    const template = [
      "---",
      `name: "${name}"`,
      'description: "A new DAN skill"',
      'author: "local"',
      'version: "1.0.0"',
      'triggers: ["keyword1", "keyword2"]',
      "---",
      "",
      `# ${name}`,
      "",
      "## Description",
      "",
      "Describe what this skill does.",
      "",
      "## Instructions",
      "",
      "1. Step one",
      "2. Step two",
      "",
    ].join("\n");
    await fs.promises.writeFile(skillPath, template, "utf-8");
  }
  return { id: name, path: skillPath };
});

// --- IPC Handlers: MCP Servers ---

const MCP_CONFIG_DIR = path.join(os.homedir(), ".dan", "mcp");
const MCP_CONFIG_PATH = path.join(MCP_CONFIG_DIR, "config.json");

interface McpConfig {
  servers: Array<{
    id: string;
    name: string;
    command: string;
    args: string[];
    env?: Record<string, string>;
    enabled: boolean;
  }>;
}

async function ensureMcpDir() {
  await fs.promises.mkdir(MCP_CONFIG_DIR, { recursive: true });
  try {
    await fs.promises.access(MCP_CONFIG_PATH);
  } catch {
    await fs.promises.writeFile(MCP_CONFIG_PATH, JSON.stringify({ servers: [] }, null, 2));
  }
}

async function readMcpConfig(): Promise<McpConfig> {
  await ensureMcpDir();
  try {
    return JSON.parse(await fs.promises.readFile(MCP_CONFIG_PATH, "utf-8"));
  } catch {
    return { servers: [] };
  }
}

async function writeMcpConfig(config: McpConfig) {
  await ensureMcpDir();
  await fs.promises.writeFile(MCP_CONFIG_PATH, JSON.stringify(config, null, 2));
}

const mcpProcesses = new Map<string, ChildProcess>();

ipcMain.handle("mcp:listInstalled", async () => {
  const config = await readMcpConfig();
  return config.servers;
});

ipcMain.handle("mcp:install", async (_event, serverId: string, serverConfig: { command: string; args: string[]; env?: Record<string, string> }) => {
  const config = await readMcpConfig();
  const existing = config.servers.findIndex((s) => s.id === serverId);
  const entry = {
    id: serverId,
    name: serverId,
    command: serverConfig.command,
    args: serverConfig.args,
    env: serverConfig.env,
    enabled: true,
  };
  if (existing >= 0) {
    config.servers[existing] = entry;
  } else {
    config.servers.push(entry);
  }
  await writeMcpConfig(config);
  return entry;
});

ipcMain.handle("mcp:remove", async (_event, serverId: string) => {
  const proc = mcpProcesses.get(serverId);
  if (proc) {
    proc.kill();
    mcpProcesses.delete(serverId);
  }
  const config = await readMcpConfig();
  config.servers = config.servers.filter((s) => s.id !== serverId);
  await writeMcpConfig(config);
});

ipcMain.handle("mcp:getConfig", async (_event, serverId: string) => {
  const config = await readMcpConfig();
  return config.servers.find((s) => s.id === serverId) ?? null;
});

ipcMain.handle("mcp:start", async (_event, serverId: string) => {
  if (mcpProcesses.has(serverId)) return { status: "already-running" };
  const config = await readMcpConfig();
  const server = config.servers.find((s) => s.id === serverId);
  if (!server) return { status: "not-found" };

  try {
    const proc = spawn(server.command, server.args, {
      env: { ...process.env, ...server.env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    mcpProcesses.set(serverId, proc);
    proc.on("exit", () => { mcpProcesses.delete(serverId); });
    proc.on("error", () => { mcpProcesses.delete(serverId); });
    return { status: "started" };
  } catch (err: any) {
    return { status: "error", error: err.message };
  }
});

ipcMain.handle("mcp:stop", async (_event, serverId: string) => {
  const proc = mcpProcesses.get(serverId);
  if (proc) {
    proc.kill();
    mcpProcesses.delete(serverId);
    return { status: "stopped" };
  }
  return { status: "not-running" };
});

ipcMain.handle("mcp:status", async () => {
  const config = await readMcpConfig();
  return config.servers.map((s) => ({
    id: s.id,
    status: mcpProcesses.has(s.id) ? "connected" : "disconnected",
    enabled: s.enabled,
  }));
});

ipcMain.handle("mcp:toggleEnabled", async (_event, serverId: string) => {
  const config = await readMcpConfig();
  const server = config.servers.find((s) => s.id === serverId);
  if (!server) return false;
  server.enabled = !server.enabled;
  if (!server.enabled) {
    const proc = mcpProcesses.get(serverId);
    if (proc) {
      proc.kill();
      mcpProcesses.delete(serverId);
    }
  }
  await writeMcpConfig(config);
  return server.enabled;
});

// --- IPC Handlers: GitHub (gh CLI) ---

function runGh(args: string[], cwd: string): Promise<CommandResult> {
  return runCommand("gh", args, {
    cwd,
    env: { ...process.env, NO_COLOR: "1" },
    timeoutMs: 30000,
  });
}

ipcMain.handle("github:checkAvailable", async (_event, cwd: string) => {
  try {
    if (!commandExists("gh")) return false;
    const authRes = await runGh(["auth", "status"], cwd);
    return authRes.code === 0;
  } catch {
    return false;
  }
});

ipcMain.handle("github:listPRs", async (_event, cwd: string) =>
  runGh(["pr", "list", "--json", "number,title,state,author,createdAt,updatedAt,headRefName,baseRefName,isDraft,url,reviewDecision,additions,deletions", "--limit", "30"], cwd));

ipcMain.handle("github:getPR", async (_event, cwd: string, number: number) =>
  runGh(["pr", "view", String(number), "--json", "number,title,body,state,author,comments,reviews,files,additions,deletions,url,headRefName,baseRefName,mergeable,reviewDecision,labels"], cwd));

ipcMain.handle("github:createPR", async (_event, cwd: string, title: string, body: string, base: string, head: string) => {
  const args = ["pr", "create", "--title", title, "--body", body];
  if (base) args.push("--base", base);
  if (head) args.push("--head", head);
  return runGh(args, cwd);
});

ipcMain.handle("github:listIssues", async (_event, cwd: string) =>
  runGh(["issue", "list", "--json", "number,title,state,author,labels,createdAt,url", "--limit", "30"], cwd));

ipcMain.handle("github:prDiff", async (_event, cwd: string, number: number) =>
  runGh(["pr", "diff", String(number)], cwd));

ipcMain.handle("github:prReview", async (_event, cwd: string, number: number, action: string, body: string) => {
  const args = ["pr", "review", String(number), `--${action}`];
  if (body) args.push("--body", body);
  return runGh(args, cwd);
});

ipcMain.handle("github:prMerge", async (_event, cwd: string, number: number, method: string) =>
  runGh(["pr", "merge", String(number), `--${method}`, "--delete-branch"], cwd));

ipcMain.handle("github:prCheckout", async (_event, cwd: string, number: number) =>
  runGh(["pr", "checkout", String(number)], cwd));

// --- Auto-updater ---

function setupAutoUpdater() {
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;

  autoUpdater.on("update-available", (info) => {
    if (mainWindow) {
      mainWindow.webContents.send("updater:update-available", {
        version: info.version,
        releaseDate: info.releaseDate,
        releaseNotes: typeof info.releaseNotes === "string" ? info.releaseNotes : "",
      });
    }
  });

  autoUpdater.on("update-not-available", () => {
    if (mainWindow) {
      mainWindow.webContents.send("updater:up-to-date");
    }
  });

  autoUpdater.on("download-progress", (progress) => {
    if (mainWindow) {
      mainWindow.webContents.send("updater:download-progress", {
        percent: progress.percent,
        bytesPerSecond: progress.bytesPerSecond,
        total: progress.total,
        transferred: progress.transferred,
      });
    }
  });

  autoUpdater.on("update-downloaded", () => {
    if (mainWindow) {
      mainWindow.webContents.send("updater:update-downloaded");
    }
  });

  autoUpdater.on("error", (error) => {
    if (mainWindow) {
      mainWindow.webContents.send("updater:error", error.message);
    }
  });

  setInterval(() => {
    autoUpdater.checkForUpdates().catch(() => {});
  }, 4 * 60 * 60 * 1000);

  setTimeout(() => {
    autoUpdater.checkForUpdates().catch(() => {});
  }, 10000);
}

ipcMain.handle("updater:check", async () => {
  try {
    const result = await autoUpdater.checkForUpdates();
    return result?.updateInfo ?? null;
  } catch { return null; }
});

ipcMain.handle("updater:download", async () => {
  try {
    await autoUpdater.downloadUpdate();
    return true;
  } catch { return false; }
});

ipcMain.handle("updater:install", () => {
  autoUpdater.quitAndInstall(false, true);
});

// --- MCP auto-connect on startup ---

async function autoConnectMcpServers() {
  try {
    const config = await readMcpConfig();
    for (const server of config.servers) {
      if (server.enabled && server.command && !mcpProcesses.has(server.id)) {
        try {
          const proc = spawn(server.command, server.args ?? [], {
            env: { ...process.env, ...server.env },
            stdio: "pipe",
          });
          mcpProcesses.set(server.id, proc);

          proc.on("exit", () => {
            mcpProcesses.delete(server.id);
          });
          proc.on("error", () => {
            mcpProcesses.delete(server.id);
          });

          console.log(`MCP server ${server.id} auto-started (PID: ${proc.pid})`);
        } catch (err) {
          console.warn(`Failed to auto-start MCP server ${server.id}:`, err);
        }
      }
    }
  } catch {
    // No config file or empty — skip
  }
}

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
    lspManager.setMainWindow(mainWindow!);
    debugManager.setMainWindow(mainWindow!);
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

    lspManager.setMainWindow(mainWindow!);
    debugManager.setMainWindow(mainWindow!);

    autoConnectMcpServers();

    // Auto-updater disabled — no release server configured.
    // Enable when a GitHub/S3 publish target is set in package.json.
    // if (!isDev) { setupAutoUpdater(); }

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
        lspManager.setMainWindow(mainWindow!);
        debugManager.setMainWindow(mainWindow!);
      }
    });
  });
}

app.on("before-quit", () => {
  extensionHost.stop();
  lspManager.shutdownAll();
  debugManager.stopSession().catch(() => {});
  for (const [, watcher] of fileWatchers) watcher.close();
  fileWatchers.clear();
  for (const [, term] of terminals) {
    try { term.kill(); } catch {}
  }
  terminals.clear();
  for (const [, proc] of mcpProcesses) {
    proc.kill();
  }
  mcpProcesses.clear();

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
