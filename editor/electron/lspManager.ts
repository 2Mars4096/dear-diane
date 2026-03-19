/**
 * LSP Manager — manages language server lifecycle for the editor.
 *
 * HOW TO ADD A CUSTOM LANGUAGE SERVER:
 * 1. Add a ServerConfig entry to SERVER_CONFIGS with launch, languages, and fileExtensions.
 * 2. Add detection logic to detectAndStart():
 *    - Check for project marker files (e.g. Cargo.toml for Rust).
 *    - For system binaries (not npm-installed), use commandExists() and log an install hint if missing.
 * 3. For bundled Node-based servers, use a packageBin launch so the packaged app does not depend
 *    on ambient cwd or a globally installed npm/npx.
 */

import { LspClient, type LspClientOptions } from "./lspClient";
import { type BrowserWindow } from "electron";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { commandExists } from "./commandExists";

type LaunchSpec =
  | {
      type: "command";
      command: string;
      args: string[];
    }
  | {
      type: "packageBin";
      packageName: string;
      binName: string;
      args: string[];
    };

interface ServerConfig {
  launch: LaunchSpec;
  languages: string[];
  fileExtensions: string[];
}

function candidatePackageRoots(): string[] {
  const roots = new Set<string>();
  if (typeof __dirname === "string") {
    roots.add(path.resolve(__dirname, ".."));
  }
  roots.add(process.cwd());
  roots.add(path.resolve(process.cwd(), "editor"));
  return Array.from(roots);
}

function resolvePackageBinPath(packageName: string, binName: string): string {
  for (const appRoot of candidatePackageRoots()) {
    const packageJsonPath = path.join(appRoot, "node_modules", packageName, "package.json");
    if (!fs.existsSync(packageJsonPath)) {
      continue;
    }

    const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, "utf8")) as {
      bin?: string | Record<string, string>;
    };
    const binEntry =
      typeof packageJson.bin === "string" ? packageJson.bin : packageJson.bin?.[binName];

    if (!binEntry) {
      throw new Error(`Package ${packageName} does not expose bin ${binName}`);
    }

    return path.resolve(path.dirname(packageJsonPath), binEntry);
  }

  throw new Error(`Could not resolve ${packageName} from the app's bundled node_modules`);
}

function workspaceCwd(rootUri: string): string | undefined {
  try {
    return fileURLToPath(rootUri);
  } catch {
    return undefined;
  }
}

function resolveLaunch(
  launch: LaunchSpec,
  rootUri: string,
): { command: string; args: string[]; options?: LspClientOptions } {
  const cwd = workspaceCwd(rootUri);

  if (launch.type === "command") {
    return {
      command: launch.command,
      args: launch.args,
      options: cwd ? { cwd } : undefined,
    };
  }

  return {
    command: process.execPath,
    args: [resolvePackageBinPath(launch.packageName, launch.binName), ...launch.args],
    options: {
      ...(cwd ? { cwd } : {}),
      env: { ELECTRON_RUN_AS_NODE: "1" },
    },
  };
}

const SERVER_CONFIGS: Record<string, ServerConfig> = {
  typescript: {
    launch: {
      type: "packageBin",
      packageName: "typescript-language-server",
      binName: "typescript-language-server",
      args: ["--stdio"],
    },
    languages: ["typescript", "typescriptreact", "javascript", "javascriptreact"],
    fileExtensions: [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"],
  },
  python: {
    launch: {
      type: "packageBin",
      packageName: "pyright",
      binName: "pyright-langserver",
      args: ["--stdio"],
    },
    languages: ["python"],
    fileExtensions: [".py", ".pyi"],
  },
  json: {
    launch: {
      type: "packageBin",
      packageName: "vscode-langservers-extracted",
      binName: "vscode-json-language-server",
      args: ["--stdio"],
    },
    languages: ["json", "jsonc"],
    fileExtensions: [".json", ".jsonc"],
  },
  css: {
    launch: {
      type: "packageBin",
      packageName: "vscode-langservers-extracted",
      binName: "vscode-css-language-server",
      args: ["--stdio"],
    },
    languages: ["css", "scss", "less"],
    fileExtensions: [".css", ".scss", ".less"],
  },
  html: {
    launch: {
      type: "packageBin",
      packageName: "vscode-langservers-extracted",
      binName: "vscode-html-language-server",
      args: ["--stdio"],
    },
    languages: ["html"],
    fileExtensions: [".html", ".htm"],
  },
  go: {
    launch: {
      type: "command",
      command: "gopls",
      args: ["serve"],
    },
    languages: ["go"],
    fileExtensions: [".go"],
  },
  rust: {
    launch: {
      type: "command",
      command: "rust-analyzer",
      args: [],
    },
    languages: ["rust"],
    fileExtensions: [".rs"],
  },
  cpp: {
    launch: {
      type: "command",
      command: "clangd",
      args: ["--background-index"],
    },
    languages: ["c", "cpp"],
    fileExtensions: [".c", ".cpp", ".cc", ".h", ".hpp", ".hh"],
  },
};

export class LspManager {
  private clients = new Map<string, LspClient>();
  private starting = new Map<string, Promise<void>>();
  private rootUris = new Map<string, string>();
  private restartAttempts = new Map<string, number>();
  private restartTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private expectedStops = new Set<string>();
  private mainWindow: BrowserWindow | null = null;

  private readonly clientFactory: (
    command: string,
    args: string[],
    rootUri: string,
    serverId: string,
    options?: LspClientOptions,
  ) => LspClient;

  constructor(
    clientFactory?: (
      command: string,
      args: string[],
      rootUri: string,
      serverId: string,
      options?: LspClientOptions,
    ) => LspClient,
  ) {
    this.clientFactory = clientFactory ?? ((command, args, rootUri, serverId, options) =>
      new LspClient(command, args, rootUri, serverId, options));
  }

  setMainWindow(win: BrowserWindow) {
    this.mainWindow = win;
  }

  async detectAndStart(rootPath: string) {
    const rootUri = `file://${rootPath}`;

    const promises: Promise<void>[] = [];

    // TypeScript / JavaScript
    if (
      fs.existsSync(path.join(rootPath, "package.json")) ||
      fs.existsSync(path.join(rootPath, "tsconfig.json")) ||
      fs.existsSync(path.join(rootPath, "jsconfig.json"))
    ) {
      promises.push(this.startServer("typescript", rootUri));
    }

    // Python
    if (
      fs.existsSync(path.join(rootPath, "pyproject.toml")) ||
      fs.existsSync(path.join(rootPath, "requirements.txt")) ||
      fs.existsSync(path.join(rootPath, "setup.py")) ||
      fs.existsSync(path.join(rootPath, "setup.cfg"))
    ) {
      promises.push(this.startServer("python", rootUri));
    }

    // Go
    if (
      fs.existsSync(path.join(rootPath, "go.mod")) ||
      fs.existsSync(path.join(rootPath, "go.sum"))
    ) {
      if (commandExists("gopls")) {
        promises.push(this.startServer("go", rootUri));
      } else {
        console.log("Go project detected but gopls not found. Install: go install golang.org/x/tools/gopls@latest");
      }
    }

    // Rust
    if (fs.existsSync(path.join(rootPath, "Cargo.toml"))) {
      if (commandExists("rust-analyzer")) {
        promises.push(this.startServer("rust", rootUri));
      } else {
        console.log("Rust project detected but rust-analyzer not found. Install: rustup component add rust-analyzer");
      }
    }

    // C / C++
    const hasCMake = fs.existsSync(path.join(rootPath, "CMakeLists.txt"));
    const hasCompileCommands = fs.existsSync(path.join(rootPath, "compile_commands.json"));
    const hasMakefileWithSources =
      fs.existsSync(path.join(rootPath, "Makefile")) &&
      fs.readdirSync(rootPath).some((f) => /\.(c|cpp|cc)$/.test(f));
    if (hasCMake || hasCompileCommands || hasMakefileWithSources) {
      if (commandExists("clangd")) {
        promises.push(this.startServer("cpp", rootUri));
      } else {
        console.log("C/C++ project detected but clangd not found. Install: brew install llvm (macOS) or apt install clangd (Linux)");
      }
    }

    // Lightweight servers — always start
    promises.push(this.startServer("json", rootUri));
    promises.push(this.startServer("css", rootUri));
    promises.push(this.startServer("html", rootUri));

    await Promise.allSettled(promises);
  }

  private notifyLifecycle(serverId: string, params: Record<string, unknown>) {
    this.mainWindow?.webContents.send("lsp:notification", {
      serverId,
      method: "$/dan/serverStatus",
      params,
    });
  }

  private scheduleRestart(serverId: string, rootUri: string, code: number | null) {
    if (this.expectedStops.delete(serverId)) return;
    if (this.restartTimers.has(serverId)) return;

    const attempt = (this.restartAttempts.get(serverId) ?? 0) + 1;
    if (attempt > 3) {
      this.notifyLifecycle(serverId, {
        kind: "restart",
        level: "error",
        exhausted: true,
        exitCode: code,
        message: `${serverId} language server crashed too many times and will stay stopped until you reopen the workspace.`,
      });
      return;
    }

    this.restartAttempts.set(serverId, attempt);
    const retryInMs = 1000 * 2 ** (attempt - 1);
    this.notifyLifecycle(serverId, {
      kind: "restart",
      level: "warning",
      exhausted: false,
      exitCode: code,
      attempt,
      retryInMs,
      message: `${serverId} language server crashed and is restarting (${attempt}/3).`,
    });

    const timer = setTimeout(() => {
      this.restartTimers.delete(serverId);
      void this.startServer(serverId, rootUri, { resetRetries: false });
    }, retryInMs);
    this.restartTimers.set(serverId, timer);
  }

  async startServer(
    serverId: string,
    rootUri: string,
    options?: { resetRetries?: boolean },
  ) {
    if (this.clients.has(serverId)) return;
    if (this.starting.has(serverId)) {
      await this.starting.get(serverId);
      return;
    }

    const config = SERVER_CONFIGS[serverId];
    if (!config) return;
    this.rootUris.set(serverId, rootUri);
    if (options?.resetRetries !== false) {
      this.restartAttempts.delete(serverId);
    }

    const startPromise = (async () => {
      let resolvedLaunch: { command: string; args: string[]; options?: LspClientOptions };
      try {
        resolvedLaunch = resolveLaunch(config.launch, rootUri);
      } catch (err) {
        console.error(`Failed to resolve LSP server ${serverId}:`, err);
        return;
      }

      const client = this.clientFactory(
        resolvedLaunch.command,
        resolvedLaunch.args,
        rootUri,
        serverId,
        resolvedLaunch.options,
      );

      client.on("notification", (method: string, params: any) => {
        if (method === "textDocument/publishDiagnostics") {
          this.mainWindow?.webContents.send("lsp:diagnostics", params);
        }
        this.mainWindow?.webContents.send("lsp:notification", { serverId, method, params });
      });

      client.on("log", (msg: string) => {
        this.mainWindow?.webContents.send("lsp:log", { serverId, message: msg });
      });

      client.on("error", (err: Error) => {
        console.error(`LSP server ${serverId} error:`, err.message);
      });

      client.on("exit", (code: number | null) => {
        console.log(`LSP server ${serverId} exited with code ${code}`);
        this.clients.delete(serverId);
        this.scheduleRestart(serverId, this.rootUris.get(serverId) ?? rootUri, code);
      });

      try {
        await client.start();
        this.clients.set(serverId, client);
      } catch (err) {
        console.error(`Failed to start LSP server ${serverId}:`, err);
      }
    })();

    this.starting.set(serverId, startPromise);
    try {
      await startPromise;
    } finally {
      this.starting.delete(serverId);
    }
  }

  getClientForFile(filePath: string): LspClient | undefined {
    const ext = path.extname(filePath).toLowerCase();
    for (const [id, client] of this.clients) {
      const config = SERVER_CONFIGS[id];
      if (config?.fileExtensions.includes(ext) && client.isRunning()) return client;
    }
    return undefined;
  }

  getClientForLanguage(languageId: string): LspClient | undefined {
    for (const [id, client] of this.clients) {
      const config = SERVER_CONFIGS[id];
      if (config?.languages.includes(languageId) && client.isRunning()) return client;
    }
    return undefined;
  }

  getAllClients(): LspClient[] {
    return Array.from(this.clients.values()).filter((c) => c.isRunning());
  }

  async shutdownAll() {
    for (const timer of this.restartTimers.values()) {
      clearTimeout(timer);
    }
    this.restartTimers.clear();
    const shutdowns = Array.from(this.clients.entries()).map(async ([id, client]) => {
      try {
        this.expectedStops.add(id);
        await client.shutdown();
      } catch (err) {
        console.error(`Error shutting down LSP server ${id}:`, err);
      }
    });
    await Promise.allSettled(shutdowns);
    this.clients.clear();
    this.rootUris.clear();
    this.restartAttempts.clear();
    this.expectedStops.clear();
  }
}
