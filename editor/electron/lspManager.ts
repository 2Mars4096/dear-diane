import { LspClient } from "./lspClient";
import { type BrowserWindow } from "electron";
import path from "node:path";
import fs from "node:fs";

interface ServerConfig {
  command: string;
  args: string[];
  languages: string[];
  fileExtensions: string[];
}

const SERVER_CONFIGS: Record<string, ServerConfig> = {
  typescript: {
    command: "npx",
    args: ["typescript-language-server", "--stdio"],
    languages: ["typescript", "typescriptreact", "javascript", "javascriptreact"],
    fileExtensions: [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"],
  },
  python: {
    command: "pyright-langserver",
    args: ["--stdio"],
    languages: ["python"],
    fileExtensions: [".py", ".pyi"],
  },
  json: {
    command: "npx",
    args: ["vscode-json-languageserver", "--stdio"],
    languages: ["json", "jsonc"],
    fileExtensions: [".json", ".jsonc"],
  },
  css: {
    command: "npx",
    args: ["vscode-css-languageserver-bin", "--stdio"],
    languages: ["css", "scss", "less"],
    fileExtensions: [".css", ".scss", ".less"],
  },
  html: {
    command: "npx",
    args: ["vscode-html-languageserver-bin", "--stdio"],
    languages: ["html"],
    fileExtensions: [".html", ".htm"],
  },
};

export class LspManager {
  private clients = new Map<string, LspClient>();
  private starting = new Map<string, Promise<void>>();
  private mainWindow: BrowserWindow | null = null;

  setMainWindow(win: BrowserWindow) {
    this.mainWindow = win;
  }

  async detectAndStart(rootPath: string) {
    const rootUri = `file://${rootPath}`;

    const promises: Promise<void>[] = [];

    if (
      fs.existsSync(path.join(rootPath, "package.json")) ||
      fs.existsSync(path.join(rootPath, "tsconfig.json")) ||
      fs.existsSync(path.join(rootPath, "jsconfig.json"))
    ) {
      promises.push(this.startServer("typescript", rootUri));
    }

    if (
      fs.existsSync(path.join(rootPath, "pyproject.toml")) ||
      fs.existsSync(path.join(rootPath, "requirements.txt")) ||
      fs.existsSync(path.join(rootPath, "setup.py")) ||
      fs.existsSync(path.join(rootPath, "setup.cfg"))
    ) {
      promises.push(this.startServer("python", rootUri));
    }

    promises.push(this.startServer("json", rootUri));

    await Promise.allSettled(promises);
  }

  async startServer(serverId: string, rootUri: string) {
    if (this.clients.has(serverId)) return;
    if (this.starting.has(serverId)) {
      await this.starting.get(serverId);
      return;
    }

    const config = SERVER_CONFIGS[serverId];
    if (!config) return;

    const startPromise = (async () => {
      const client = new LspClient(config.command, config.args, rootUri, serverId);

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

  async shutdownAll() {
    const shutdowns = Array.from(this.clients.entries()).map(async ([id, client]) => {
      try {
        await client.shutdown();
      } catch (err) {
        console.error(`Error shutting down LSP server ${id}:`, err);
      }
    });
    await Promise.allSettled(shutdowns);
    this.clients.clear();
  }
}
