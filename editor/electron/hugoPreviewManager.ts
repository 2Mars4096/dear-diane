import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import net from "node:net";
import http from "node:http";
import https from "node:https";
import { spawn, type ChildProcess } from "node:child_process";

const HUGO_CONFIG_FILES = [
  "hugo.toml",
  "config.toml",
  "config.yaml",
  "config.yml",
] as const;

const DEFAULT_PREVIEW_READY_TIMEOUT_MS = 60_000;
const MIN_PREVIEW_READY_TIMEOUT_MS = 5_000;
const PREVIEW_READY_POLL_MS = 300;
const PREVIEW_REQUEST_TIMEOUT_MS = 1_500;
const LOG_BUFFER_LIMIT = 12;
const MACOS_HUGO_PATHS = [
  "/opt/homebrew/bin/hugo",
  "/usr/local/bin/hugo",
  "/opt/local/bin/hugo",
  "/usr/bin/hugo",
] as const;

export type ContentPreviewLifecycle =
  | "unsupported"
  | "stopped"
  | "starting"
  | "running"
  | "error";

export interface ContentPreviewStatus {
  projectRoot: string;
  lifecycle: ContentPreviewLifecycle;
  hugoAvailable: boolean;
  hasHugoConfig: boolean;
  hasContentDir: boolean;
  ready: boolean;
  port: number | null;
  baseUrl: string | null;
  pid: number | null;
  lastError: string | null;
  recentLogs: string[];
  updatedAt: number;
}

interface PreviewSession {
  process: ChildProcess | null;
  status: ContentPreviewStatus;
  startPromise: Promise<ContentPreviewStatus> | null;
  stopRequested: boolean;
}

type ExecutableProbe = (targetPath: string) => boolean;

function isExecutable(targetPath: string) {
  try {
    fs.accessSync(targetPath, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function uniquePathEntries(entries: Array<string | null | undefined>) {
  const seen = new Set<string>();
  return entries.filter((entry): entry is string => {
    if (!entry || seen.has(entry)) return false;
    seen.add(entry);
    return true;
  });
}

function homeRelativeCandidates(env: NodeJS.ProcessEnv) {
  const home = env.HOME || os.homedir();
  if (!home) return [];
  return [
    path.join(home, ".asdf", "shims", "hugo"),
    path.join(home, ".local", "bin", "hugo"),
    path.join(home, "go", "bin", "hugo"),
  ];
}

export function resolveHugoCommand(
  options: {
    env?: NodeJS.ProcessEnv;
    platform?: NodeJS.Platform;
    isExecutable?: ExecutableProbe;
  } = {},
) {
  const env = options.env ?? process.env;
  const platform = options.platform ?? process.platform;
  const canRun = options.isExecutable ?? isExecutable;
  const configured = env.DAN_HUGO_BIN?.trim();

  if (configured && canRun(configured)) {
    return configured;
  }

  const binaryNames =
    platform === "win32" ? ["hugo.exe", "hugo.cmd", "hugo.bat", "hugo"] : ["hugo"];
  const pathCandidates = (env.PATH ?? "")
    .split(path.delimiter)
    .filter(Boolean)
    .flatMap((entry) => binaryNames.map((binary) => path.join(entry, binary)));
  const fallbackCandidates =
    platform === "darwin" ? [...MACOS_HUGO_PATHS, ...homeRelativeCandidates(env)] : [];

  for (const candidate of [...pathCandidates, ...fallbackCandidates]) {
    if (canRun(candidate)) {
      return candidate;
    }
  }

  return null;
}

export function buildHugoPreviewEnv(
  hugoCommand: string,
  env: NodeJS.ProcessEnv = process.env,
) {
  const hugoDir = path.isAbsolute(hugoCommand) ? path.dirname(hugoCommand) : null;
  return {
    ...env,
    HUGO_ENV: "development",
    PATH: uniquePathEntries([
      hugoDir,
      "/opt/homebrew/bin",
      "/usr/local/bin",
      "/opt/local/bin",
      "/usr/bin",
      "/bin",
      "/usr/sbin",
      "/sbin",
      ...(env.PATH ?? "").split(path.delimiter),
    ]).join(path.delimiter),
  };
}

function cloneStatus(status: ContentPreviewStatus): ContentPreviewStatus {
  return {
    ...status,
    recentLogs: [...status.recentLogs],
  };
}

function createBaseStatus(projectRoot: string): ContentPreviewStatus {
  return {
    projectRoot,
    lifecycle: "stopped",
    hugoAvailable: resolveHugoCommand() !== null,
    hasHugoConfig: false,
    hasContentDir: false,
    ready: false,
    port: null,
    baseUrl: null,
    pid: null,
    lastError: null,
    recentLogs: [],
    updatedAt: Date.now(),
  };
}

async function pathExists(targetPath: string) {
  try {
    await fs.promises.access(targetPath);
    return true;
  } catch {
    return false;
  }
}

async function inspectProjectRoot(
  projectRoot: string,
  previous?: ContentPreviewStatus,
): Promise<ContentPreviewStatus> {
  const hasContentDir = await pathExists(path.join(projectRoot, "content"));
  let hasHugoConfig = false;
  for (const name of HUGO_CONFIG_FILES) {
    if (await pathExists(path.join(projectRoot, name))) {
      hasHugoConfig = true;
      break;
    }
  }

  let lifecycle: ContentPreviewLifecycle = "stopped";
  let lastError: string | null = null;
  const hugoCommand = resolveHugoCommand();

  if (!hasContentDir) {
    lifecycle = "unsupported";
    lastError = "The selected project does not contain a content/ directory.";
  } else if (!hasHugoConfig) {
    lifecycle = "unsupported";
    lastError = "No Hugo config file was found at the project root.";
  } else if (!hugoCommand) {
    lifecycle = "error";
    lastError =
      "Hugo is not installed or not available in DAN's environment. Install Hugo or set DAN_HUGO_BIN to the full hugo binary path.";
  }

  return {
    ...(previous ?? createBaseStatus(projectRoot)),
    projectRoot,
    lifecycle,
    hugoAvailable: Boolean(hugoCommand),
    hasHugoConfig,
    hasContentDir,
    ready: false,
    pid: null,
    lastError,
    updatedAt: Date.now(),
  };
}

function wait(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function resolvePreviewReadyTimeoutMs(
  env: NodeJS.ProcessEnv = process.env,
) {
  const rawValue = Number(env.DAN_CONTENT_PREVIEW_READY_TIMEOUT_MS);
  if (!Number.isFinite(rawValue)) {
    return DEFAULT_PREVIEW_READY_TIMEOUT_MS;
  }
  return Math.max(MIN_PREVIEW_READY_TIMEOUT_MS, Math.floor(rawValue));
}

export async function isUrlReachable(baseUrl: string) {
  try {
    const url = new URL(baseUrl);
    const client = url.protocol === "https:" ? https : http;
    await new Promise<void>((resolve, reject) => {
      const request = client.request(
        url,
        { method: "HEAD" },
        (response) => {
          response.destroy();
          resolve();
        },
      );
      request.once("error", reject);
      request.setTimeout(PREVIEW_REQUEST_TIMEOUT_MS, () => {
        request.destroy(new Error("timeout"));
      });
      request.end();
    });
    return true;
  } catch {
    return false;
  }
}

async function waitForPreviewReady(
  baseUrl: string,
  shouldContinue: () => boolean,
  timeoutMs = resolvePreviewReadyTimeoutMs(),
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!shouldContinue()) return false;
    if (await isUrlReachable(baseUrl)) return true;
    await wait(PREVIEW_READY_POLL_MS);
  }
  return false;
}

async function findAvailablePort(preferredPort = 1313): Promise<number> {
  const tryListen = (port: number) =>
    new Promise<number>((resolve, reject) => {
      const server = net.createServer();
      server.unref();
      server.once("error", reject);
      server.listen(port, "127.0.0.1", () => {
        const address = server.address();
        const resolvedPort =
          typeof address === "object" && address ? address.port : port;
        server.close(() => resolve(resolvedPort));
      });
    });

  try {
    return await tryListen(preferredPort);
  } catch {
    return tryListen(0);
  }
}

export class HugoPreviewManager {
  private sessions = new Map<string, PreviewSession>();

  private getSession(projectRoot: string) {
    let session = this.sessions.get(projectRoot);
    if (!session) {
      session = {
        process: null,
        status: createBaseStatus(projectRoot),
        startPromise: null,
        stopRequested: false,
      };
      this.sessions.set(projectRoot, session);
    }
    return session;
  }

  private updateStatus(
    session: PreviewSession,
    next: Partial<ContentPreviewStatus>,
  ) {
    session.status = {
      ...session.status,
      ...next,
      updatedAt: Date.now(),
    };
    return cloneStatus(session.status);
  }

  private appendLog(session: PreviewSession, chunk: Buffer | string) {
    const lines = chunk
      .toString()
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    if (lines.length === 0) return;
    const recentLogs = [...session.status.recentLogs, ...lines].slice(
      -LOG_BUFFER_LIMIT,
    );
    const lastError =
      [...lines].reverse().find((line) => /error|failed/i.test(line)) ??
      session.status.lastError;
    this.updateStatus(session, { recentLogs, lastError });
  }

  async getStatus(projectRoot: string): Promise<ContentPreviewStatus> {
    const session = this.getSession(projectRoot);
    const inspected = await inspectProjectRoot(projectRoot, session.status);
    if (!session.process) {
      session.status = {
        ...session.status,
        ...inspected,
        port: session.status.port,
        baseUrl: session.status.baseUrl,
        recentLogs: session.status.recentLogs,
      };
      if (session.status.lifecycle === "running") {
        session.status.lifecycle = "stopped";
      }
      session.status.ready = false;
      session.status.pid = null;
      session.status.updatedAt = Date.now();
    } else {
      session.status = {
        ...session.status,
        hugoAvailable: inspected.hugoAvailable,
        hasHugoConfig: inspected.hasHugoConfig,
        hasContentDir: inspected.hasContentDir,
        updatedAt: Date.now(),
      };
    }
    return cloneStatus(session.status);
  }

  async start(projectRoot: string): Promise<ContentPreviewStatus> {
    const session = this.getSession(projectRoot);
    if (
      session.process &&
      (session.status.lifecycle === "running" ||
        session.status.lifecycle === "starting")
    ) {
      return cloneStatus(session.status);
    }
    if (session.startPromise) {
      return session.startPromise;
    }

    session.startPromise = this.startInternal(session, projectRoot).finally(() => {
      session.startPromise = null;
    });
    return session.startPromise;
  }

  private async startInternal(
    session: PreviewSession,
    projectRoot: string,
  ): Promise<ContentPreviewStatus> {
    const inspected = await inspectProjectRoot(projectRoot, session.status);
    session.status = {
      ...session.status,
      ...inspected,
      recentLogs: [],
    };

    if (!inspected.hasContentDir || !inspected.hasHugoConfig) {
      return cloneStatus(session.status);
    }
    if (!inspected.hugoAvailable) {
      return cloneStatus(session.status);
    }
    const hugoCommand = resolveHugoCommand();
    if (!hugoCommand) {
      return this.updateStatus(session, {
        lifecycle: "error",
        ready: false,
        pid: null,
        lastError:
          "Hugo is not installed or not available in DAN's environment. Install Hugo or set DAN_HUGO_BIN to the full hugo binary path.",
      });
    }

    const port = await findAvailablePort(session.status.port ?? 1313);
    const baseUrl = `http://127.0.0.1:${port}/`;
    session.stopRequested = false;
    this.updateStatus(session, {
      lifecycle: "starting",
      ready: false,
      port,
      baseUrl,
      pid: null,
      lastError: null,
      recentLogs: [],
    });

    let proc: ChildProcess;
    try {
      proc = spawn(
        hugoCommand,
        [
          "server",
          "--bind",
          "127.0.0.1",
          "--port",
          String(port),
          "--baseURL",
          baseUrl,
          "--buildDrafts",
          "--buildFuture",
        ],
        {
          cwd: projectRoot,
          env: buildHugoPreviewEnv(hugoCommand),
          stdio: ["ignore", "pipe", "pipe"],
        },
      );
    } catch (error) {
      return this.updateStatus(session, {
        lifecycle: "error",
        ready: false,
        pid: null,
        lastError:
          error instanceof Error
            ? error.message
            : "Failed to start the Hugo preview process.",
      });
    }

    session.process = proc;

    proc.stdout?.on("data", (chunk) => {
      this.appendLog(session, chunk);
    });
    proc.stderr?.on("data", (chunk) => {
      this.appendLog(session, chunk);
    });
    proc.on("error", (error) => {
      if (session.process !== proc) return;
      session.process = null;
      this.updateStatus(session, {
        lifecycle: "error",
        ready: false,
        pid: null,
        lastError:
          error instanceof Error
            ? error.message
            : "The Hugo preview process failed.",
      });
    });
    proc.on("close", (code) => {
      if (session.process === proc) {
        session.process = null;
      }
      if (session.stopRequested) {
        this.updateStatus(session, {
          lifecycle: "stopped",
          ready: false,
          pid: null,
        });
        return;
      }
      this.updateStatus(session, {
        lifecycle: code === 0 ? "stopped" : "error",
        ready: false,
        pid: null,
        lastError:
          session.status.lastError ??
          `Hugo preview exited ${code === 0 ? "cleanly" : `with code ${code}`}.`,
      });
    });

    const ready = await waitForPreviewReady(
      baseUrl,
      () => session.process === proc && !session.stopRequested,
    );
    if (ready && session.process === proc) {
      return this.updateStatus(session, {
        lifecycle: "running",
        ready: true,
        pid: proc.pid ?? null,
        lastError: null,
      });
    }

    if (session.process === proc) {
      session.stopRequested = true;
      try {
        proc.kill();
      } catch {
        // Ignore failed kill attempts here; close/error handlers will reconcile.
      }
      session.process = null;
    }
    return this.updateStatus(session, {
      lifecycle: "error",
      ready: false,
      pid: null,
      lastError:
        session.status.lastError ??
        "Timed out waiting for the Hugo preview server to become ready.",
    });
  }

  async stop(projectRoot: string): Promise<ContentPreviewStatus> {
    const session = this.sessions.get(projectRoot);
    if (!session) {
      return inspectProjectRoot(projectRoot);
    }
    if (session.process) {
      const proc = session.process;
      session.stopRequested = true;
      session.process = null;
      try {
        proc.kill();
      } catch {
        // Best effort.
      }
    }
    return this.updateStatus(session, {
      lifecycle: "stopped",
      ready: false,
      pid: null,
      lastError: null,
    });
  }

  async restart(projectRoot: string): Promise<ContentPreviewStatus> {
    await this.stop(projectRoot);
    return this.start(projectRoot);
  }

  stopAll() {
    for (const projectRoot of this.sessions.keys()) {
      void this.stop(projectRoot);
    }
  }
}
