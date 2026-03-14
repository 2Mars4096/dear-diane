import { spawn, type ChildProcess } from "node:child_process";
import { EventEmitter } from "node:events";

export interface LspMessage {
  jsonrpc: "2.0";
  id?: number;
  method?: string;
  params?: any;
  result?: any;
  error?: any;
}

const REQUEST_TIMEOUT_MS = 10_000;

export class LspClient extends EventEmitter {
  private process: ChildProcess | null = null;
  private buffer = "";
  private contentLength = -1;
  private nextId = 1;
  private pendingRequests = new Map<
    number,
    { resolve: (v: any) => void; reject: (e: any) => void; timer: ReturnType<typeof setTimeout> }
  >();
  private serverCapabilities: any = null;
  private disposed = false;

  constructor(
    private command: string,
    private args: string[],
    private rootUri: string,
    private languageId: string,
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
      this.rejectAllPending("Server exited");
      this.emit("exit", code);
    });

    const result = await this.request("initialize", {
      processId: process.pid,
      rootUri: this.rootUri,
      capabilities: {
        textDocument: {
          synchronization: {
            willSave: false,
            didSave: true,
            willSaveWaitUntil: false,
            dynamicRegistration: false,
          },
          completion: {
            completionItem: {
              snippetSupport: true,
              documentationFormat: ["markdown", "plaintext"],
              resolveSupport: { properties: ["documentation", "detail"] },
            },
            contextSupport: true,
          },
          hover: { contentFormat: ["markdown", "plaintext"] },
          signatureHelp: {
            signatureInformation: {
              documentationFormat: ["markdown", "plaintext"],
              parameterInformation: { labelOffsetSupport: true },
            },
          },
          definition: { linkSupport: true },
          references: {},
          documentSymbol: { hierarchicalDocumentSymbolSupport: true },
          codeAction: {
            codeActionLiteralSupport: {
              codeActionKind: {
                valueSet: [
                  "quickfix",
                  "refactor",
                  "refactor.extract",
                  "refactor.inline",
                  "refactor.rewrite",
                  "source",
                  "source.organizeImports",
                ],
              },
            },
          },
          formatting: {},
          rangeFormatting: {},
          rename: { prepareSupport: true },
          callHierarchy: { dynamicRegistration: false },
          publishDiagnostics: { relatedInformation: true, tagSupport: { valueSet: [1, 2] } },
        },
        workspace: {
          workspaceFolders: true,
          didChangeConfiguration: { dynamicRegistration: false },
        },
      },
      workspaceFolders: [{ uri: this.rootUri, name: "root" }],
    });

    this.serverCapabilities = result.capabilities;
    this.notify("initialized", {});
  }

  request(method: string, params: any): Promise<any> {
    return new Promise((resolve, reject) => {
      if (this.disposed || !this.process?.stdin?.writable) {
        reject(new Error("LSP client is not connected"));
        return;
      }
      const id = this.nextId++;
      const timer = setTimeout(() => {
        if (this.pendingRequests.has(id)) {
          this.pendingRequests.delete(id);
          reject(new Error(`LSP request ${method} (id=${id}) timed out after ${REQUEST_TIMEOUT_MS}ms`));
        }
      }, REQUEST_TIMEOUT_MS);
      this.pendingRequests.set(id, { resolve, reject, timer });
      this.send({ jsonrpc: "2.0", id, method, params });
    });
  }

  notify(method: string, params: any) {
    this.send({ jsonrpc: "2.0", method, params });
  }

  private send(msg: LspMessage) {
    if (!this.process?.stdin?.writable) return;
    const body = JSON.stringify(msg);
    const header = `Content-Length: ${Buffer.byteLength(body)}\r\n\r\n`;
    this.process.stdin.write(header + body);
  }

  private handleData(chunk: Buffer) {
    this.buffer += chunk.toString();
    while (true) {
      if (this.contentLength < 0) {
        const headerEnd = this.buffer.indexOf("\r\n\r\n");
        if (headerEnd < 0) break;
        const header = this.buffer.slice(0, headerEnd);
        const match = header.match(/Content-Length:\s*(\d+)/i);
        if (!match) {
          this.buffer = this.buffer.slice(headerEnd + 4);
          continue;
        }
        this.contentLength = parseInt(match[1], 10);
        this.buffer = this.buffer.slice(headerEnd + 4);
      }
      if (this.buffer.length < this.contentLength) break;
      const body = this.buffer.slice(0, this.contentLength);
      this.buffer = this.buffer.slice(this.contentLength);
      this.contentLength = -1;
      try {
        const msg: LspMessage = JSON.parse(body);
        this.handleMessage(msg);
      } catch {
        // malformed JSON — skip
      }
    }
  }

  private handleMessage(msg: LspMessage) {
    if (msg.id !== undefined && (msg.result !== undefined || msg.error !== undefined)) {
      const pending = this.pendingRequests.get(msg.id as number);
      if (pending) {
        clearTimeout(pending.timer);
        this.pendingRequests.delete(msg.id as number);
        if (msg.error) pending.reject(msg.error);
        else pending.resolve(msg.result);
      }
    } else if (msg.method) {
      // Server-initiated request (e.g. window/showMessage, client/registerCapability)
      if (msg.id !== undefined) {
        this.send({ jsonrpc: "2.0", id: msg.id, result: null } as LspMessage);
      }
      this.emit("notification", msg.method, msg.params);
    }
  }

  private rejectAllPending(reason: string) {
    for (const [id, pending] of this.pendingRequests) {
      clearTimeout(pending.timer);
      pending.reject(new Error(reason));
    }
    this.pendingRequests.clear();
  }

  // --- Document synchronization ---

  didOpen(uri: string, languageId: string, version: number, text: string) {
    this.notify("textDocument/didOpen", {
      textDocument: { uri, languageId, version, text },
    });
  }

  didChange(uri: string, version: number, changes: any[]) {
    this.notify("textDocument/didChange", {
      textDocument: { uri, version },
      contentChanges: changes,
    });
  }

  didSave(uri: string, text?: string) {
    this.notify("textDocument/didSave", {
      textDocument: { uri },
      ...(text !== undefined ? { text } : {}),
    });
  }

  didClose(uri: string) {
    this.notify("textDocument/didClose", { textDocument: { uri } });
  }

  // --- Language features ---

  async completion(uri: string, line: number, character: number, context?: any) {
    return this.request("textDocument/completion", {
      textDocument: { uri },
      position: { line, character },
      ...(context ? { context } : {}),
    });
  }

  async hover(uri: string, line: number, character: number) {
    return this.request("textDocument/hover", {
      textDocument: { uri },
      position: { line, character },
    });
  }

  async definition(uri: string, line: number, character: number) {
    return this.request("textDocument/definition", {
      textDocument: { uri },
      position: { line, character },
    });
  }

  async references(uri: string, line: number, character: number) {
    return this.request("textDocument/references", {
      textDocument: { uri },
      position: { line, character },
      context: { includeDeclaration: true },
    });
  }

  async documentSymbol(uri: string) {
    return this.request("textDocument/documentSymbol", {
      textDocument: { uri },
    });
  }

  async formatting(uri: string, tabSize: number, insertSpaces: boolean) {
    return this.request("textDocument/formatting", {
      textDocument: { uri },
      options: { tabSize, insertSpaces },
    });
  }

  async rangeFormatting(uri: string, range: any, tabSize: number, insertSpaces: boolean) {
    return this.request("textDocument/rangeFormatting", {
      textDocument: { uri },
      range,
      options: { tabSize, insertSpaces },
    });
  }

  async codeAction(uri: string, range: any, diagnostics: any[]) {
    return this.request("textDocument/codeAction", {
      textDocument: { uri },
      range,
      context: { diagnostics },
    });
  }

  async rename(uri: string, line: number, character: number, newName: string) {
    return this.request("textDocument/rename", {
      textDocument: { uri },
      position: { line, character },
      newName,
    });
  }

  async signatureHelp(uri: string, line: number, character: number) {
    return this.request("textDocument/signatureHelp", {
      textDocument: { uri },
      position: { line, character },
    });
  }

  async prepareCallHierarchy(uri: string, line: number, character: number) {
    return this.request("textDocument/prepareCallHierarchy", {
      textDocument: { uri },
      position: { line, character },
    });
  }

  async incomingCalls(item: any) {
    return this.request("callHierarchy/incomingCalls", { item });
  }

  async outgoingCalls(item: any) {
    return this.request("callHierarchy/outgoingCalls", { item });
  }

  async shutdown() {
    this.disposed = true;
    try {
      await this.request("shutdown", null);
      this.notify("exit", null);
    } catch {
      // Server may already be gone
    } finally {
      this.rejectAllPending("Client shut down");
      this.process?.kill();
      this.process = null;
    }
  }

  getCapabilities() {
    return this.serverCapabilities;
  }

  isRunning() {
    return this.process !== null && !this.disposed;
  }
}
