import type { BrowserWindow } from "electron";
import { DapClient } from "./dapClient";

interface DebugAdapterConfig {
  command: string;
  args: string[];
}

function resolveJsDebugAdapter(): string {
  try {
    return require.resolve("@vscode/js-debug/src/dapDebugServer");
  } catch {
    return "node_modules/@vscode/js-debug/src/dapDebugServer";
  }
}

function getDebugAdapter(type: string): DebugAdapterConfig | undefined {
  const adapters: Record<string, () => DebugAdapterConfig> = {
    node: () => ({
      command: "node",
      args: ["--inspect-brk", resolveJsDebugAdapter()],
    }),
    python: () => ({
      command: "python3",
      args: ["-m", "debugpy.adapter"],
    }),
  };
  return adapters[type]?.();
}

export interface LaunchConfig {
  name: string;
  type: string;
  request: "launch" | "attach";
  program?: string;
  args?: string[];
  cwd?: string;
  env?: Record<string, string>;
  port?: number;
  host?: string;
  stopOnEntry?: boolean;
  [key: string]: any;
}

export class DebugManager {
  private client: DapClient | null = null;
  private mainWindow: BrowserWindow | null = null;
  private currentConfig: LaunchConfig | null = null;

  setMainWindow(win: BrowserWindow) {
    this.mainWindow = win;
  }

  private sendEvent(event: string, body: any) {
    this.mainWindow?.webContents.send("debug:event", { event, body });
  }

  async startSession(config: LaunchConfig): Promise<void> {
    if (this.client?.isRunning()) {
      await this.stopSession();
    }

    const adapter = getDebugAdapter(config.type);
    if (!adapter) {
      throw new Error(`Unknown debug adapter type: ${config.type}. Available: node, python`);
    }

    this.currentConfig = config;
    this.client = new DapClient(adapter.command, adapter.args);

    this.client.on("event", (event: string, body: any) => {
      this.sendEvent(event, body);
    });

    this.client.on("log", (message: string) => {
      this.sendEvent("output", {
        category: "console",
        output: message,
      });
    });

    this.client.on("error", (err: Error) => {
      this.sendEvent("output", {
        category: "stderr",
        output: `Debug adapter error: ${err.message}\n`,
      });
    });

    this.client.on("exit", (code: number | null) => {
      this.sendEvent("terminated", { exitCode: code });
      this.client = null;
      this.currentConfig = null;
    });

    try {
      await this.client.start();
    } catch (err: any) {
      this.client = null;
      throw new Error(`Failed to start debug adapter: ${err.message}`);
    }

    try {
      if (config.request === "launch") {
        await this.client.launch({
          program: config.program,
          args: config.args ?? [],
          cwd: config.cwd,
          env: config.env,
          stopOnEntry: config.stopOnEntry ?? false,
          console: "integratedTerminal",
          ...Object.fromEntries(
            Object.entries(config).filter(
              ([k]) => !["name", "type", "request"].includes(k),
            ),
          ),
        });
      } else {
        await this.client.attach({
          port: config.port ?? 9229,
          host: config.host ?? "localhost",
          ...Object.fromEntries(
            Object.entries(config).filter(
              ([k]) => !["name", "type", "request"].includes(k),
            ),
          ),
        });
      }

      await this.client.configurationDone();
    } catch (err: any) {
      await this.client?.disconnect().catch(() => {});
      this.client = null;
      throw new Error(`Failed to ${config.request} debug session: ${err.message}`);
    }
  }

  async stopSession(): Promise<void> {
    if (!this.client?.isRunning()) return;
    try {
      await this.client.disconnect();
    } catch {
      // Force cleanup
    }
    this.client = null;
    this.currentConfig = null;
  }

  async restart(): Promise<void> {
    if (!this.currentConfig) throw new Error("No active debug session to restart");
    const config = { ...this.currentConfig };
    await this.stopSession();
    await this.startSession(config);
  }

  async setBreakpoints(filePath: string, breakpoints: Array<{ line: number; condition?: string; logMessage?: string }>): Promise<any> {
    if (!this.client?.isRunning()) return { breakpoints: [] };
    return this.client.setBreakpoints({ path: filePath }, breakpoints);
  }

  async continue_(threadId: number): Promise<any> {
    if (!this.client?.isRunning()) return null;
    return this.client.continue_(threadId);
  }

  async next(threadId: number): Promise<any> {
    if (!this.client?.isRunning()) return null;
    return this.client.next(threadId);
  }

  async stepIn(threadId: number): Promise<any> {
    if (!this.client?.isRunning()) return null;
    return this.client.stepIn(threadId);
  }

  async stepOut(threadId: number): Promise<any> {
    if (!this.client?.isRunning()) return null;
    return this.client.stepOut(threadId);
  }

  async pause(threadId: number): Promise<any> {
    if (!this.client?.isRunning()) return null;
    return this.client.pause(threadId);
  }

  async threads(): Promise<any> {
    if (!this.client?.isRunning()) return { threads: [] };
    return this.client.threads();
  }

  async stackTrace(threadId: number): Promise<any> {
    if (!this.client?.isRunning()) return { stackFrames: [] };
    return this.client.stackTrace(threadId);
  }

  async scopes(frameId: number): Promise<any> {
    if (!this.client?.isRunning()) return { scopes: [] };
    return this.client.scopes(frameId);
  }

  async variables(variablesReference: number): Promise<any> {
    if (!this.client?.isRunning()) return { variables: [] };
    return this.client.variables(variablesReference);
  }

  async evaluate(expression: string, frameId?: number): Promise<any> {
    if (!this.client?.isRunning()) return null;
    return this.client.evaluate(expression, frameId);
  }

  isActive(): boolean {
    return this.client?.isRunning() ?? false;
  }
}
