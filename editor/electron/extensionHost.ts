import { fork, type ChildProcess } from "node:child_process";
import path from "node:path";
import { EventEmitter } from "node:events";

const REQUEST_TIMEOUT_MS = 30_000;

export class ExtensionHost extends EventEmitter {
  private process: ChildProcess | null = null;
  private extensionPaths: string[] = [];
  private pendingRequests = new Map<
    number,
    { resolve: Function; reject: Function; timer: ReturnType<typeof setTimeout> }
  >();
  private nextRequestId = 1;

  start(extensionPaths: string[]) {
    if (this.process) this.stop();
    this.extensionPaths = extensionPaths;

    this.process = fork(
      path.join(__dirname, "extensionHostWorker.js"),
      [],
      {
        stdio: ["pipe", "pipe", "pipe", "ipc"],
        env: {
          ...process.env,
          EXTENSION_PATHS: JSON.stringify(extensionPaths),
          NODE_OPTIONS: "--max-old-space-size=512",
        },
      },
    );

    this.process.on("message", (msg: any) => {
      if (msg.type === "response" && this.pendingRequests.has(msg.id)) {
        const pending = this.pendingRequests.get(msg.id)!;
        this.pendingRequests.delete(msg.id);
        clearTimeout(pending.timer);
        if (msg.error) pending.reject(new Error(msg.error));
        else pending.resolve(msg.result);
      } else if (msg.type === "event") {
        this.emit(msg.event, msg.data);
      }
    });

    this.process.on("exit", (code) => {
      for (const [, { reject, timer }] of this.pendingRequests) {
        clearTimeout(timer);
        reject(new Error(`Extension host exited with code ${code}`));
      }
      this.pendingRequests.clear();
      this.emit("exit", code);
      this.process = null;
    });

    this.process.on("error", (err) => {
      this.emit("error", err);
    });

    this.process.stderr?.on("data", (chunk: Buffer) => {
      this.emit("log", chunk.toString());
    });
  }

  async request(command: string, args?: any): Promise<any> {
    if (!this.process) throw new Error("Extension host not running");
    const id = this.nextRequestId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this.pendingRequests.has(id)) {
          this.pendingRequests.delete(id);
          reject(new Error(`Extension host request timeout: ${command}`));
        }
      }, REQUEST_TIMEOUT_MS);
      this.pendingRequests.set(id, { resolve, reject, timer });
      this.process!.send({ type: "request", id, command, args });
    });
  }

  stop() {
    if (this.process) {
      this.process.kill();
      this.process = null;
    }
    for (const [, { reject, timer }] of this.pendingRequests) {
      clearTimeout(timer);
      reject(new Error("Extension host stopped"));
    }
    this.pendingRequests.clear();
  }

  get running(): boolean {
    return this.process !== null;
  }
}
