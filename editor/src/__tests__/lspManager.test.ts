import { EventEmitter } from "node:events";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LspManager } from "../../electron/lspManager";

class FakeLspClient extends EventEmitter {
  private running = false;

  async start(): Promise<void> {
    this.running = true;
  }

  async shutdown(): Promise<void> {
    this.running = false;
    this.emit("exit", 0);
  }

  isRunning(): boolean {
    return this.running;
  }
}

describe("LspManager", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("restarts crashed servers with backoff and a warning notification", async () => {
    const created: FakeLspClient[] = [];
    const manager = new LspManager((_command, _args, _rootUri, _serverId) => {
      const client = new FakeLspClient();
      created.push(client);
      return client as any;
    });
    const send = vi.fn();
    manager.setMainWindow({ webContents: { send } } as any);

    await manager.startServer("typescript", "file:///workspace");
    expect(created).toHaveLength(1);

    created[0].emit("exit", 1);
    expect(send).toHaveBeenCalledWith(
      "lsp:notification",
      expect.objectContaining({
        serverId: "typescript",
        method: "$/dan/serverStatus",
        params: expect.objectContaining({
          kind: "restart",
          attempt: 1,
          retryInMs: 1000,
          exhausted: false,
        }),
      }),
    );

    await vi.advanceTimersByTimeAsync(1000);
    expect(created).toHaveLength(2);
  });

  it("stops restarting after three crashes", async () => {
    const created: FakeLspClient[] = [];
    const manager = new LspManager((_command, _args, _rootUri, _serverId) => {
      const client = new FakeLspClient();
      created.push(client);
      return client as any;
    });
    const send = vi.fn();
    manager.setMainWindow({ webContents: { send } } as any);

    await manager.startServer("typescript", "file:///workspace");
    created[0].emit("exit", 1);
    await vi.advanceTimersByTimeAsync(1000);
    created[1].emit("exit", 1);
    await vi.advanceTimersByTimeAsync(2000);
    created[2].emit("exit", 1);
    await vi.advanceTimersByTimeAsync(4000);

    expect(created).toHaveLength(4);

    created[3].emit("exit", 1);
    await vi.advanceTimersByTimeAsync(10000);

    expect(created).toHaveLength(4);
    expect(send).toHaveBeenLastCalledWith(
      "lsp:notification",
      expect.objectContaining({
        serverId: "typescript",
        params: expect.objectContaining({
          exhausted: true,
          kind: "restart",
        }),
      }),
    );
  });
});
