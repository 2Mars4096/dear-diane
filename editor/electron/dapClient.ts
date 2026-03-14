import { spawn, type ChildProcess } from "node:child_process";
import { EventEmitter } from "node:events";

export interface DapMessage {
  seq: number;
  type: "request" | "response" | "event";
  command?: string;
  event?: string;
  request_seq?: number;
  success?: boolean;
  message?: string;
  body?: any;
  arguments?: any;
}

const REQUEST_TIMEOUT_MS = 15_000;

export class DapClient extends EventEmitter {
  private process: ChildProcess | null = null;
  private buffer = Buffer.alloc(0);
  private contentLength = -1;
  private nextSeq = 1;
  private pendingRequests = new Map<
    number,
    { resolve: (v: any) => void; reject: (e: any) => void; timer: ReturnType<typeof setTimeout> }
  >();
  private disposed = false;
  private initialized = false;

  constructor(
    private command: string,
    private args: string[],
  ) {
    super();
  }

  async start(): Promise<void> {
    this.process = spawn(this.command, this.args, {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, NODE_NO_WARNINGS: "1" },
    });

    this.process.stdout!.on("data", (chunk: Buffer) => this.handleData(chunk));
    this.process.stderr!.on("data", (chunk: Buffer) => {
      this.emit("log", chunk.toString());
    });
    this.process.on("error", (err) => {
      this.emit("error", err);
    });
    this.process.on("exit", (code) => {
      this.rejectAllPending("Debug adapter exited");
      this.emit("exit", code);
    });

    const capabilities = await this.request("initialize", {
      clientID: "dan-editor",
      clientName: "DAN Editor",
      adapterID: "generic",
      pathFormat: "path",
      linesStartAt1: true,
      columnsStartAt1: true,
      supportsVariableType: true,
      supportsVariablePaging: false,
      supportsRunInTerminalRequest: false,
      locale: "en-us",
    });

    this.initialized = true;
    this.emit("capabilities", capabilities);
  }

  request(command: string, args?: any): Promise<any> {
    return new Promise((resolve, reject) => {
      if (this.disposed || !this.process?.stdin?.writable) {
        reject(new Error("DAP client is not connected"));
        return;
      }
      const seq = this.nextSeq++;
      const timer = setTimeout(() => {
        if (this.pendingRequests.has(seq)) {
          this.pendingRequests.delete(seq);
          reject(new Error(`DAP request ${command} (seq=${seq}) timed out after ${REQUEST_TIMEOUT_MS}ms`));
        }
      }, REQUEST_TIMEOUT_MS);
      this.pendingRequests.set(seq, { resolve, reject, timer });
      this.send({ seq, type: "request", command, arguments: args });
    });
  }

  private send(msg: DapMessage) {
    if (!this.process?.stdin?.writable) return;
    const body = JSON.stringify(msg);
    const header = `Content-Length: ${Buffer.byteLength(body)}\r\n\r\n`;
    this.process.stdin.write(header + body);
  }

  private handleData(chunk: Buffer) {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    while (true) {
      if (this.contentLength < 0) {
        const headerEnd = this.buffer.indexOf("\r\n\r\n");
        if (headerEnd < 0) break;
        const header = this.buffer.subarray(0, headerEnd).toString("utf-8");
        const match = header.match(/Content-Length:\s*(\d+)/i);
        if (!match) {
          this.buffer = this.buffer.subarray(headerEnd + 4);
          continue;
        }
        this.contentLength = parseInt(match[1], 10);
        this.buffer = this.buffer.subarray(headerEnd + 4);
      }
      if (this.buffer.length < this.contentLength) break;
      const body = this.buffer.subarray(0, this.contentLength).toString("utf-8");
      this.buffer = this.buffer.subarray(this.contentLength);
      this.contentLength = -1;
      try {
        const msg: DapMessage = JSON.parse(body);
        this.handleMessage(msg);
      } catch {
        // malformed JSON — skip
      }
    }
  }

  private handleMessage(msg: DapMessage) {
    if (msg.type === "response") {
      const pending = this.pendingRequests.get(msg.request_seq!);
      if (pending) {
        clearTimeout(pending.timer);
        this.pendingRequests.delete(msg.request_seq!);
        if (msg.success === false) {
          pending.reject(new Error(msg.message ?? "DAP request failed"));
        } else {
          pending.resolve(msg.body ?? {});
        }
      }
    } else if (msg.type === "event") {
      this.emit("event", msg.event, msg.body);
    }
  }

  private rejectAllPending(reason: string) {
    for (const [, pending] of this.pendingRequests) {
      clearTimeout(pending.timer);
      pending.reject(new Error(reason));
    }
    this.pendingRequests.clear();
  }

  async launch(config: any): Promise<any> {
    return this.request("launch", config);
  }

  async attach(config: any): Promise<any> {
    return this.request("attach", config);
  }

  async setBreakpoints(source: { path: string }, breakpoints: Array<{ line: number; condition?: string; logMessage?: string }>): Promise<any> {
    return this.request("setBreakpoints", { source, breakpoints });
  }

  async configurationDone(): Promise<any> {
    return this.request("configurationDone");
  }

  async continue_(threadId: number): Promise<any> {
    return this.request("continue", { threadId });
  }

  async next(threadId: number): Promise<any> {
    return this.request("next", { threadId });
  }

  async stepIn(threadId: number): Promise<any> {
    return this.request("stepIn", { threadId });
  }

  async stepOut(threadId: number): Promise<any> {
    return this.request("stepOut", { threadId });
  }

  async pause(threadId: number): Promise<any> {
    return this.request("pause", { threadId });
  }

  async threads(): Promise<any> {
    return this.request("threads");
  }

  async stackTrace(threadId: number, startFrame?: number, levels?: number): Promise<any> {
    return this.request("stackTrace", { threadId, startFrame: startFrame ?? 0, levels: levels ?? 50 });
  }

  async scopes(frameId: number): Promise<any> {
    return this.request("scopes", { frameId });
  }

  async variables(variablesReference: number): Promise<any> {
    return this.request("variables", { variablesReference });
  }

  async evaluate(expression: string, frameId?: number, context?: string): Promise<any> {
    return this.request("evaluate", { expression, frameId, context: context ?? "repl" });
  }

  async disconnect(restart?: boolean): Promise<void> {
    this.disposed = true;
    try {
      await this.request("disconnect", { restart: restart ?? false, terminateDebuggee: true });
    } catch {
      // Adapter may already be gone
    } finally {
      this.rejectAllPending("Client disconnected");
      this.process?.kill();
      this.process = null;
    }
  }

  isRunning() {
    return this.process !== null && !this.disposed;
  }

  isInitialized() {
    return this.initialized;
  }
}
